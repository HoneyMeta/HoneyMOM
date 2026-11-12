"""设备时序数据：原始值 (ts, point, value, quality)、离散量状态区间、1 分钟 / 1 小时汇总、保留策略、查询。说明见 docs/dev/foundation.md「时序」。

存储（迁移 v0004 决定，系统参数 ts_mode）：
  timescale  PostgreSQL + TimescaleDB：ts_raw 超表 + 压缩；ts_1m / ts_1h 连续聚合（实时）；保留策略交给 TimescaleDB。
  pgpart     PostgreSQL 原生分区（按月，写入时自动建分区）；ts_1m / ts_1h 普通表，rollup_job() 汇总。
  sqlite     无 rowid 表，主键 (point, ts)；汇总表 + rollup_job()。
三种模式对外的 Python 接口和 REST 接口完全一样。

采集点（point）：key 是"数据源:设备/点位"（如 VF:CNC1/Common/State），第一次写入自动登记；dtype = num / bool / str。
离散量（bool、str，以及手动标成 discrete 的整数如步号）另外维护状态区间 ts_segment：开/关时长、跳变次数、上升/下降沿都从这里算，
所以 1 秒内来回跳几次也不会丢，汇总表里也就不需要再存开关量的特殊统计。

写入：
  ts.write([(key, ts, value), …])          立即写（接口推送、测试）；返回新插入的行数（重复的 (point, ts) 跳过）
  ts.buffer(key, ts, value, …)             采集用：先进内存，ts.flush() 每秒批量写（数据库不可用时由 ingest.GATE 转去磁盘缓存）
默认"变化才存"：同一点位值没变就不存，但每 60 秒至少存一次（心跳）；系统参数 ts_store=all 改成全部存。
查询：raw / latest / rollup / segments / edges / stats，都接受点位 key 或 id。
"""
from .i18n import _tr
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

import sqlalchemy as sa

from . import compat, db
from .schema import ts_1h_t, ts_1m_t, ts_dirty_t, ts_point_t, ts_raw_t, ts_segment_t

log = logging.getLogger('mes.ts')
QUALITY = {0: '好', 1: '不确定', 2: '坏'}
HEARTBEAT_S = 60
INTERVALS = {'1m': timedelta(minutes=1), '1h': timedelta(hours=1)}
RETENTION_DEFAULT = {'raw': 30, '1m': 365, '1h': 1825, 'segment': 365}

_lock = threading.RLock()
_points: dict[str, dict] = {}          # key -> {id, dtype, discrete}
_by_id: dict[int, dict] = {}
_last: dict[int, tuple] = {}           # point -> (ts, value, text, q)  最近一次写入（变化判断、心跳）
_open_seg: dict[int, dict] = {}        # point -> {id, start, value, text}  当前状态区间
_buf: list = []                        # 待写：(point, ts, value, text, q)
_loaded = False
_mode = None
_parts: set = set()                    # pgpart：已确认存在的月分区
STATS = defaultdict(int)          # 写入计数（rows_in / rows_new / write_ms / skipped_same / late_discrete）


def _iso(v):
    """时间 → 字符串，保留毫秒（原始值是亚秒级的）。"""
    v = compat.to_dt(v) if v is not None and not isinstance(v, datetime) else v
    if v is None:
        return None
    return v.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] if v.microsecond else v.strftime('%Y-%m-%d %H:%M:%S')


def _dt(v):
    return v if isinstance(v, datetime) or v is None else compat.to_dt(v)


def mode() -> str:
    global _mode
    if _mode is None:
        with db.engine.connect() as c:
            _mode = db.setting(c, 'ts_mode') or ('sqlite' if c.dialect.name == 'sqlite' else 'pgpart')
    return _mode


def reset_cache():
    """换库（测试、恢复演练）后调用。"""
    global _loaded, _mode
    with _lock:
        _points.clear(), _by_id.clear(), _last.clear(), _open_seg.clear(), _buf.clear(), _parts.clear(), _touch_pending.clear()
        _loaded, _mode = False, None
        _wm[0], _wm[1] = None, 0.0


def _load():
    global _loaded
    if _loaded:
        return
    with db.engine.connect() as c:
        for r in c.execute(sa.select(ts_point_t)).mappings():
            p = {'id': r['id'], 'key': r['key'], 'dtype': r['dtype'], 'discrete': bool(r['discrete']), 'equipment': r['equipment'], 'name': r['name']}
            _points[r['key']] = p
            _by_id[r['id']] = p
            if r['last_ts'] is not None:
                _last[r['id']] = (r['last_ts'], r['last_value'], r['last_text'], r['last_q'] or 0)
        for r in c.execute(sa.select(ts_segment_t).where(ts_segment_t.c.end.is_(None))).mappings():
            _open_seg[r['point']] = {'id': r['id'], 'start': r['start'], 'value': r['value'], 'text': r['text']}
    _loaded = True


def _dtype(v):
    if isinstance(v, bool):
        return 'bool'
    if isinstance(v, (int, float)):
        return 'num'
    return 'str'


def point(key: str, equipment: int | None = None, name: str | None = None, dtype: str | None = None, unit: str | None = None,
          discrete: bool | None = None, conn=None) -> int:
    """采集点 key → id（没有就登记）。"""
    with _lock:
        _load()
        p = _points.get(key)
        if p:
            return p['id']
        dt = dtype or 'num'
        disc = discrete if discrete is not None else dt in ('bool', 'str')
        vals = dict(key=key[:300], equipment=equipment, name=(name or key.split('/', 1)[-1])[:200], dtype=dt, unit=unit, discrete=disc,
                    created_at=db.clock_wall())

        def ins(c):
            ex = c.execute(sa.select(ts_point_t.c.id).where(ts_point_t.c.key == vals['key'])).scalar()
            return ex or c.execute(ts_point_t.insert().values(**vals)).inserted_primary_key[0]
        if conn is not None:
            pid = ins(conn)
        else:
            with db.engine.begin() as c:
                pid = ins(c)
        p = {'id': pid, 'key': key, 'dtype': dt, 'discrete': disc, 'equipment': equipment, 'name': vals['name']}
        _points[key], _by_id[pid] = p, p
        return pid


def set_point(key_or_id, **vals):
    """改采集点属性：unit / discrete / name / equipment。"""
    pid = _pid(key_or_id)
    with db.engine.begin() as c:
        c.execute(ts_point_t.update().where(ts_point_t.c.id == pid).values(**{k: v for k, v in vals.items() if k in ('unit', 'discrete', 'name', 'equipment')}))
    with _lock:
        if pid in _by_id:
            _by_id[pid].update({k: v for k, v in vals.items() if k in ('discrete', 'name', 'equipment')})


def _pid(key_or_id) -> int:
    if isinstance(key_or_id, int):
        return key_or_id
    if isinstance(key_or_id, str) and key_or_id.isdigit():
        return int(key_or_id)
    with _lock:
        _load()
        p = _points.get(key_or_id)
    if not p:
        raise KeyError(_tr('没有采集点 {key_or_id}', key_or_id=key_or_id))
    return p['id']


def _norm(value):
    """→ (value 浮点或 None, text 或 None, dtype)"""
    if value is None:
        return None, None, 'num'
    if isinstance(value, bool):
        return (1.0 if value else 0.0), None, 'bool'
    if isinstance(value, (int, float)):
        return float(value), None, 'num'
    s = str(value)
    return None, s[:200], 'str'


# ───────────────────────── 写入 ─────────────────────────
def buffer(key: str, ts: datetime, value, q: int = 0, equipment: int | None = None, name: str | None = None, store_all: bool | None = None):
    """采集用：进内存缓冲（变化才存 + 心跳），flush() 批量落库。"""
    v, t, dt = _norm(value)
    with _lock:
        pid = point(key, equipment=equipment, name=name, dtype=dt)
        last = _last.get(pid)
        if last is not None and not (store_all if store_all is not None else _store_all()):
            if last[1] == v and last[2] == t and last[3] == q and ts >= last[0] and (ts - last[0]).total_seconds() < HEARTBEAT_S:
                STATS['skipped_same'] += 1
                return
        if last is not None and ts == last[0] and (v != last[1] or t != last[2]):
            ts = ts + timedelta(microseconds=1)          # 同一时间戳来了不同的值（源只有秒级时间）：顺延 1 微秒，别被主键去重吞掉
        _buf.append((pid, ts, v, t, q))
        if last is None or ts >= last[0]:
            _last[pid] = (ts, v, t, q)


_store_all_cache = (0.0, False)


def _store_all() -> bool:
    global _store_all_cache
    if time.time() - _store_all_cache[0] > 30:
        try:
            with db.engine.connect() as c:
                _store_all_cache = (time.time(), (db.setting(c, 'ts_store') or 'change') == 'all')
        except Exception:
            _store_all_cache = (time.time(), _store_all_cache[1])
    return _store_all_cache[1]


def pending() -> int:
    return len(_buf)


def take_buffer() -> list:
    """取走缓冲（flush 失败时由 ingest.GATE 写进磁盘缓存）。"""
    global _buf
    with _lock:
        b, _buf = _buf, []
    return b


def flush() -> int:
    """把缓冲写库。失败时把数据放回缓冲最前面并抛异常（调用方决定是否转磁盘）。"""
    rows = take_buffer()
    if not rows:
        return 0
    try:
        return _write_rows(rows)
    except Exception:
        with _lock:
            _buf[:0] = rows
        raise


def write(rows, conn=None, store_all=True) -> int:
    """立即写：rows = [(key 或 id, ts, value[, q])]。ts 可以是 datetime 或字符串。返回新插入的行数。"""
    out = []
    for r in rows:
        k, ts, value = r[0], _dt(r[1]), r[2]
        q = int(r[3]) if len(r) > 3 and r[3] is not None else 0
        if ts is None:
            raise ValueError(_tr('时间格式不对：{v}', v=r[1]))
        v, t, dt = _norm(value)
        pid = k if isinstance(k, int) else point(str(k), dtype=dt, conn=conn)
        out.append((pid, ts, v, t, q))
        with _lock:
            last = _last.get(pid)
            if last is None or ts >= last[0]:
                _last[pid] = (ts, v, t, q)
    return _write_rows(out, conn)


def _write_rows(rows, conn=None) -> int:
    rows.sort(key=lambda r: (r[1], r[0]))
    t0 = time.time()

    def go(c):
        if mode() == 'pgpart':
            _ensure_parts(c, rows)
        n = compat.insert_ignore(c, ts_raw_t, [{'point': p, 'ts': ts, 'value': v, 'text': t, 'q': q} for p, ts, v, t, q in rows], keys=['point', 'ts'])
        _segments(c, rows)
        _touch_points(c, rows)
        if mode() != 'timescale':
            _mark_dirty(c, rows)
        return n
    if conn is not None:
        n = go(conn)
    else:
        with db.engine.begin() as c:
            n = go(c)
    STATS['rows_in'] += len(rows)
    STATS['rows_new'] += n
    STATS['write_ms'] += int((time.time() - t0) * 1000)
    return n


_touch_pending: dict = {}
_touch_at = [0.0]
TOUCH_S = 10            # ts_point.last_* 最多每 10 秒落一次库（latest() 会合并内存里更新的值）


def _touch_points(c, rows, force=False):
    for p, ts, v, t, q in rows:
        cur = _touch_pending.get(p)
        if cur is None or ts >= cur[0]:
            _touch_pending[p] = (ts, v, t, q)
    if not _touch_pending or (not force and time.time() - _touch_at[0] < TOUCH_S):
        return
    pt = ts_point_t
    items = [{'pid': p, 'lts': ts, 'lv': v, 'lt': t, 'lq': q} for p, (ts, v, t, q) in _touch_pending.items()]
    c.execute(pt.update().where((pt.c.id == sa.bindparam('pid')) & sa.or_(pt.c.last_ts.is_(None), pt.c.last_ts <= sa.bindparam('lts')))
              .values(last_ts=sa.bindparam('lts'), last_value=sa.bindparam('lv'), last_text=sa.bindparam('lt'), last_q=sa.bindparam('lq')), items)
    _touch_pending.clear()
    _touch_at[0] = time.time()


def _segments(c, rows):
    """离散量：值变了就关掉上一个区间、开新区间。晚到（早于当前区间开始）的值不改区间。"""
    st = ts_segment_t
    for p, ts, v, t, q in rows:
        info = _by_id.get(p)
        if not info or not info.get('discrete'):
            continue
        cur = _open_seg.get(p)
        if cur and (cur['value'] == v and cur['text'] == t):
            continue
        if cur and ts < cur['start']:
            STATS['late_discrete'] += 1
            continue
        if cur:
            c.execute(st.update().where(st.c.id == cur['id']).values(end=ts, dur_s=(ts - cur['start']).total_seconds()))
        sid = c.execute(st.insert().values(point=p, start=ts, value=v, text=t)).inserted_primary_key[0]
        _open_seg[p] = {'id': sid, 'start': ts, 'value': v, 'text': t}


def _mark_dirty(c, rows):
    """汇总作业的水位之前来的数据（迟到 / 补传）：记脏分钟，下次 rollup_job 重算。"""
    wm = _wm_cached(c)
    if wm is None:
        return
    dirty = {(p, ts.replace(second=0, microsecond=0)) for p, ts, v, t, q in rows if ts < wm}
    if dirty:
        compat.insert_ignore(c, ts_dirty_t, [{'point': p, 'bucket': b} for p, b in dirty], keys=['point', 'bucket'])


def _ensure_parts(c, rows):
    months = {(ts.year, ts.month) for _, ts, *_ in rows}
    for y, m in months:
        if (y, m) in _parts:
            continue
        start = datetime(y, m, 1)
        end = datetime(y + (m == 12), m % 12 + 1, 1)
        name = f'ts_raw_p{y:04d}{m:02d}'
        c.exec_driver_sql(f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF ts_raw FOR VALUES FROM ('{start:%Y-%m-%d}') TO ('{end:%Y-%m-%d}')")
        _parts.add((y, m))


# ───────────────────────── 汇总作业（sqlite / pgpart） ─────────────────────────
_wm = [None, 0.0]


def _wm_cached(c):
    if time.time() - _wm[1] > 30:
        _wm[0], _wm[1] = _watermark(c), time.time()
    return _wm[0]


def _watermark(c):
    v = db.setting(c, 'ts_rollup_wm')
    return db.parse_dt(v) if v else None


def rollup_job(max_minutes=360, until: datetime | None = None) -> dict:
    """增量汇总：水位（已汇总到的整分钟）之后的已结束分钟 + 脏分钟 → ts_1m；涉及的小时 → ts_1h。TimescaleDB 下不需要（连续聚合）。
    读和写分开：先只读算出一段（≤ 1 小时）的汇总，再用一个很短的写事务落库 —— SQLite 下不会长时间占着写锁、挡住采集。
    每次最多补 max_minutes 分钟（后台每分钟跑一次，积压会逐步追上）。"""
    if mode() == 'timescale':
        return {'mode': 'timescale', 'skipped': True}
    t0 = time.time()
    lock = db.engine.connect()
    try:
        if lock.dialect.name == 'postgresql':
            if not lock.execute(sa.text('SELECT pg_try_advisory_lock(hashtext(:k))'), {'k': 'honeymes.ts.rollup'}).scalar():
                return {'skipped': 'busy'}
        with db.engine.connect() as c:
            wm = _watermark(c)
            mx = compat.to_dt(c.execute(sa.select(sa.func.max(ts_point_t.c.last_ts))).scalar())     # 点位表很小，比扫原始值快
            pend = max((v[0] for v in _touch_pending.values()), default=None)
            if pend and (mx is None or pend > mx):
                mx = pend
            if mx is None:
                return {'minutes': 0}
            upto = mx.replace(second=0, microsecond=0)             # 最新那一分钟可能还没收完，不汇总（until 可以指定汇总到哪）
            if until is not None:
                upto = _dt(until).replace(second=0, microsecond=0)
            if wm is None:
                mn = compat.to_dt(c.execute(sa.select(sa.func.min(ts_raw_t.c.ts))).scalar())
                if mn is None:
                    return {'minutes': 0}
                wm = mn.replace(second=0, microsecond=0)
            dirty = [(r.point, compat.to_dt(r.bucket)) for r in c.execute(sa.select(ts_dirty_t).limit(5000)).all()]
        start, end = wm, min(upto, wm + timedelta(minutes=max_minutes))
        n1 = _roll_1m(start, end) if end > start else 0
        for p, b in dirty:
            n1 += _roll_1m(b, b + timedelta(minutes=1), point=p)
        if dirty:
            with db.engine.begin() as w:
                w.execute(ts_dirty_t.delete().where((ts_dirty_t.c.point == sa.bindparam('p')) & (ts_dirty_t.c.bucket == sa.bindparam('b'))),
                          [{'p': p, 'b': b} for p, b in dirty])
        hours = set()
        if end > start:
            h = start.replace(minute=0)
            while h < end:
                hours.add((None, h))
                h += timedelta(hours=1)
        for p, b in dirty:
            hours.add((p, b.replace(minute=0)))
        n2 = 0
        for p, h in sorted(hours, key=lambda x: (x[1], x[0] or 0)):
            n2 += _roll_1h(h, point=p)
        if end > start:
            with db.engine.begin() as w:
                db.set_setting(w, 'ts_rollup_wm', end.strftime('%Y-%m-%d %H:%M:%S'), _tr('时序汇总水位（ts.rollup_job 维护）'))
            _wm[0], _wm[1] = end, time.time()
    finally:
        if lock.dialect.name == 'postgresql':
            try:
                lock.execute(sa.text('SELECT pg_advisory_unlock(hashtext(:k))'), {'k': 'honeymes.ts.rollup'})
            except Exception:
                pass
        lock.close()
    return {'from': db.jsonable(start), 'to': db.jsonable(end), 'rows_1m': n1, 'rows_1h': n2, 'dirty': len(dirty), 'ms': int((time.time() - t0) * 1000)}


def _roll_1m(start, end, point=None) -> int:
    """[start, end) 内的原始值 → ts_1m（按小时分段：只读算好，再短事务写入；first/last 用窗口函数，两种方言都支持）。"""
    n = 0
    a = start
    while a < end:
        b_ = min(end, a + timedelta(hours=1))
        with db.engine.connect() as rc:
            out = _agg_1m(rc, a, b_, point)
        if out:
            with db.engine.begin() as w:
                compat.upsert(w, ts_1m_t, out, keys=['bucket', 'point'])
        n += len(out)
        a = b_
    return n


def _agg_1m(c, start, end, point=None) -> list:
    r = ts_raw_t
    bk = compat.minute_bucket(r.c.ts, c)
    conds = [r.c.ts >= start, r.c.ts < end, r.c.value.is_not(None)]
    if point is not None:
        conds.append(r.c.point == point)
    inner = sa.select(r.c.point, r.c.value, bk.label('b'),
                      sa.func.first_value(r.c.value).over(partition_by=[r.c.point, bk], order_by=r.c.ts).label('f'),
                      sa.func.first_value(r.c.value).over(partition_by=[r.c.point, bk], order_by=r.c.ts.desc()).label('l')).where(*conds).subquery()
    agg = c.execute(sa.select(inner.c.b, inner.c.point, sa.func.count(inner.c.value), sa.func.min(inner.c.value), sa.func.max(inner.c.value),
                              sa.func.avg(inner.c.value), sa.func.min(inner.c.f), sa.func.min(inner.c.l)).group_by(inner.c.b, inner.c.point)).all()
    return [{'bucket': compat.to_dt(bk_), 'point': p, 'n': n, 'vmin': mn, 'vmax': mx, 'vavg': float(av) if av is not None else None, 'vfirst': f, 'vlast': l}
            for bk_, p, n, mn, mx, av, f, l in agg]


def _roll_1h(hour, point=None) -> int:
    m = ts_1m_t
    conds = [m.c.bucket >= hour, m.c.bucket < hour + timedelta(hours=1)]
    if point is not None:
        conds.append(m.c.point == point)
    with db.engine.connect() as c:
        rows = c.execute(sa.select(m).where(*conds).order_by(m.c.point, m.c.bucket)).mappings().all()
    acc = {}
    for r in rows:
        a = acc.get(r['point'])
        if a is None:
            acc[r['point']] = a = {'bucket': hour, 'point': r['point'], 'n': 0, 'vmin': None, 'vmax': None, 's': 0.0, 'vfirst': r['vfirst'], 'vlast': None}
        a['n'] += r['n'] or 0
        a['vmin'] = r['vmin'] if a['vmin'] is None else min(a['vmin'], r['vmin'])
        a['vmax'] = r['vmax'] if a['vmax'] is None else max(a['vmax'], r['vmax'])
        a['s'] += (r['vavg'] or 0) * (r['n'] or 0)
        a['vlast'] = r['vlast']
    out = [{'bucket': a['bucket'], 'point': a['point'], 'n': a['n'], 'vmin': a['vmin'], 'vmax': a['vmax'], 'vavg': a['s'] / a['n'] if a['n'] else None,
            'vfirst': a['vfirst'], 'vlast': a['vlast']} for a in acc.values()]
    if out:
        with db.engine.begin() as w:
            compat.upsert(w, ts_1h_t, out, keys=['bucket', 'point'])
    return len(out)


def refresh(start, end):
    """TimescaleDB：手动刷新连续聚合（补传了很久以前的数据时）。需要自动提交连接。"""
    if mode() != 'timescale':
        return rollup_job()
    with db.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
        for v in ('ts_1m', 'ts_1h'):
            c.execute(sa.text(f"CALL refresh_continuous_aggregate('{v}', :a, :b)"), {'a': _dt(start), 'b': _dt(end)})
    return {'refreshed': True}


# ───────────────────────── 保留策略 ─────────────────────────
def retention() -> dict:
    with db.engine.connect() as c:
        return {k: int(float(db.setting(c, f'ts_keep_{k}_days') or v)) for k, v in RETENTION_DEFAULT.items()}


def set_retention(vals: dict):
    with db.engine.begin() as c:
        for k in RETENTION_DEFAULT:
            if k in vals and vals[k] is not None:
                d = int(vals[k])
                if d < 1:
                    raise ValueError(_tr('保留天数至少 1 天'))
                db.set_setting(c, f'ts_keep_{k}_days', d, _tr('时序保留天数：{k}', k=k))
    apply_retention()
    return retention()


def apply_retention():
    """TimescaleDB：按系统参数重设保留策略（后台作业执行）。其它模式由 retention_job() 删除。"""
    if mode() != 'timescale':
        return
    r = retention()
    with db.engine.begin() as c:
        for rel, k in (('ts_raw', 'raw'), ('ts_1m', '1m'), ('ts_1h', '1h')):
            c.execute(sa.text(f"SELECT remove_retention_policy('{rel}', if_exists => TRUE)"))
            c.execute(sa.text(f"SELECT add_retention_policy('{rel}', INTERVAL '{int(r[k])} days', if_not_exists => TRUE)"))


def _delete_batched(t, cond, keys, batch=20000) -> int:
    """分批删（每批一个短事务），SQLite 下不长时间占写锁。"""
    n = 0
    while True:
        sub = sa.select(*[t.c[k] for k in keys]).where(cond).limit(batch)
        with db.engine.begin() as c:
            if len(keys) == 1:
                k = c.execute(t.delete().where(t.c[keys[0]].in_(sub))).rowcount
            else:
                k = c.execute(t.delete().where(sa.tuple_(*[t.c[x] for x in keys]).in_(sub))).rowcount
        n += max(k or 0, 0)
        if not k or k < batch:
            return n


def retention_job(now: datetime | None = None) -> dict:
    """删过期数据（sqlite / pgpart；TimescaleDB 的保留策略自己跑，这里只删状态区间）。时间基准 = 数据时钟。分批删除。"""
    now = now or db.clock.now()
    r = retention()
    out = {}
    out['segment'] = _delete_batched(ts_segment_t, ts_segment_t.c.end.is_not(None) & (ts_segment_t.c.end < now - timedelta(days=r['segment'])), ['id'])
    if mode() == 'timescale':
        return out
    cut = now - timedelta(days=r['raw'])
    if mode() == 'pgpart':
        dropped = []
        with db.engine.begin() as c:
            for (name,) in c.execute(sa.text("SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                                             "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'ts_raw'")).all():
                y, m = int(name[-6:-2]), int(name[-2:])
                end = datetime(y + (m == 12), m % 12 + 1, 1)
                if end <= cut:
                    c.exec_driver_sql(f'DROP TABLE IF EXISTS {name}')
                    _parts.discard((y, m))
                    dropped.append(name)
        out['raw_partitions_dropped'] = dropped
    else:
        out['raw'] = _delete_batched(ts_raw_t, ts_raw_t.c.ts < cut, ['point', 'ts'])
    out['1m'] = _delete_batched(ts_1m_t, ts_1m_t.c.bucket < now - timedelta(days=r['1m']), ['bucket', 'point'])
    out['1h'] = _delete_batched(ts_1h_t, ts_1h_t.c.bucket < now - timedelta(days=r['1h']), ['bucket', 'point'])
    return out


# ───────────────────────── 查询 ─────────────────────────
def _rng(start, end):
    s = _dt(start)
    e = _dt(end) if end is not None else None
    if s is None:
        raise ValueError(_tr('请给开始时间'))
    return s, e or (db.clock.now() + timedelta(seconds=1))


def info(key_or_id) -> dict:
    pid = _pid(key_or_id)
    with db.engine.connect() as c:
        r = c.execute(sa.select(ts_point_t).where(ts_point_t.c.id == pid)).mappings().first()
    if not r:
        raise KeyError(_tr('没有采集点 {key_or_id}', key_or_id=key_or_id))
    return {k: db.jsonable(v) for k, v in dict(r).items()}


def points(equipment: int | None = None, q: str | None = None, limit=2000) -> list[dict]:
    t = ts_point_t
    conds = []
    if equipment is not None:
        conds.append(t.c.equipment == equipment)
    if q:
        conds.append(sa.func.lower(t.c.key).like(f'%{q.lower()}%'))
    with db.engine.connect() as c:
        return [{k: db.jsonable(v) for k, v in dict(r).items()} for r in c.execute(sa.select(t).where(*conds).order_by(t.c.key).limit(limit)).mappings()]


def raw(key_or_id, start, end=None, limit=10000) -> list[dict]:
    """原始值（时间升序）。"""
    pid = _pid(key_or_id)
    s, e = _rng(start, end)
    r = ts_raw_t
    with db.engine.connect() as c:
        rows = c.execute(sa.select(r.c.ts, r.c.value, r.c.text, r.c.q).where(r.c.point == pid, r.c.ts >= s, r.c.ts < e).order_by(r.c.ts).limit(limit)).all()
    return [{'ts': _iso(ts), 'value': v, 'text': t, 'q': q} for ts, v, t, q in rows]


def latest(keys=None, equipment: int | None = None) -> dict:
    """最新值：{key: {ts, value, text, q}}。"""
    t = ts_point_t
    conds = []
    if keys:
        ids = [k for k in keys if isinstance(k, int)]
        names = [k for k in keys if not isinstance(k, int)]
        conds.append(sa.or_(t.c.id.in_(ids), t.c.key.in_(names)))
    if equipment is not None:
        conds.append(t.c.equipment == equipment)
    with db.engine.connect() as c:
        rows = c.execute(sa.select(t.c.key, t.c.last_ts, t.c.last_value, t.c.last_text, t.c.last_q).where(*conds)).all()
    out = {k: {'ts': _iso(ts), 'value': v, 'text': tx, 'q': q} for k, ts, v, tx, q in rows}
    with _lock:                           # 还在缓冲里没落库的更新值
        for pid, (ts, v, tx, q) in _last.items():
            p = _by_id.get(pid)
            if p and p['key'] in out and (out[p['key']]['ts'] is None or ts > _dt(out[p['key']]['ts'])):
                out[p['key']] = {'ts': _iso(ts), 'value': v, 'text': tx, 'q': q}
    return out


def rollup(key_or_id, start, end=None, interval='1m') -> list[dict]:
    """汇总：[{bucket, n, min, max, avg, first, last}]；离散量另带 on_s（值为真/非零的秒数）、transitions（跳变次数）。"""
    if interval not in INTERVALS:
        raise ValueError(_tr('interval 只能是 1m 或 1h'))
    pid = _pid(key_or_id)
    s, e = _rng(start, end)
    t = ts_1m_t if interval == '1m' else ts_1h_t
    with db.engine.connect() as c:
        rows = c.execute(sa.select(t).where(t.c.point == pid, t.c.bucket >= s, t.c.bucket < e).order_by(t.c.bucket)).mappings().all()
    out = [{'bucket': db.jsonable(compat.to_dt(r['bucket'])), 'n': r['n'], 'min': r['vmin'], 'max': r['vmax'], 'avg': r['vavg'], 'first': r['vfirst'],
            'last': r['vlast']} for r in rows]
    p = _by_id.get(pid) or {}
    if p.get('discrete'):
        step = INTERVALS[interval]
        segs = segments(pid, s, e)
        by = {o['bucket']: o for o in out}
        b = s.replace(second=0, microsecond=0) if interval == '1m' else s.replace(minute=0, second=0, microsecond=0)
        while b < e:
            k = db.jsonable(b)
            on, tr = _bucket_stats(segs, b, b + step)
            if k not in by and (on or tr):
                by[k] = {'bucket': k, 'n': 0, 'min': None, 'max': None, 'avg': None, 'first': None, 'last': None}
            if k in by:
                by[k]['on_s'], by[k]['transitions'] = on, tr
            b += step
        out = sorted(by.values(), key=lambda x: x['bucket'])
    return out


def _bucket_stats(segs, a, b):
    on, tr = 0.0, 0
    for sg in segs:
        st, en = _dt(sg['start']), _dt(sg['end']) if sg['end'] else None
        en = en or b
        lo, hi = max(st, a), min(en, b)
        if hi > lo and (sg['value'] or 0) != 0:
            on += (hi - lo).total_seconds()
        if a <= st < b and sg.get('prev') is not None:
            tr += 1
    return round(on, 3), tr


def segments(key_or_id, start, end=None, limit=20000) -> list[dict]:
    """离散量的状态区间（与 [start, end) 有交叠的），时间升序：[{start, end, value, text, dur_s, prev}]。end 为空 = 当前还在这个状态。"""
    pid = _pid(key_or_id)
    s, e = _rng(start, end)
    st = ts_segment_t
    with db.engine.connect() as c:
        rows = c.execute(sa.select(st).where(st.c.point == pid, st.c.start < e, sa.or_(st.c.end.is_(None), st.c.end > s)).order_by(st.c.start).limit(limit)).mappings().all()
        prev = c.execute(sa.select(st.c.value, st.c.text).where(st.c.point == pid, st.c.start < (rows[0]['start'] if rows else s)).order_by(st.c.start.desc()).limit(1)).first()
    out = []
    pv = (prev[0], prev[1]) if prev else None
    for r in rows:
        out.append({'start': _iso(r['start']), 'end': _iso(r['end']), 'value': r['value'], 'text': r['text'],
                    'dur_s': r['dur_s'], 'prev': (pv[1] if pv[1] is not None else pv[0]) if pv else None})
        pv = (r['value'], r['text'])
    return out


def edges(key_or_id, start, end=None, kind='both') -> list[dict]:
    """开关量的上升沿 / 下降沿（kind = rising / falling / both）：[{ts, edge, from, to}]。字符串 / 多值离散量给 change。"""
    out = []
    for sg in segments(key_or_id, start, end):
        if sg['prev'] is None:
            continue
        if _dt(sg['start']) < _rng(start, end)[0]:
            continue
        new = sg['text'] if sg['text'] is not None else sg['value']
        if isinstance(new, (int, float)) and isinstance(sg['prev'], (int, float)) and {bool(new), bool(sg['prev'])} == {True, False}:
            e = 'rising' if new else 'falling'
        else:
            e = 'change'
        if kind == 'both' or kind == e or (kind == 'change' and e == 'change'):
            out.append({'ts': sg['start'], 'edge': e, 'from': sg['prev'], 'to': new})
    return out


def stats(key_or_id, start, end=None) -> dict:
    """一段时间的统计：数值量 n/min/max/avg；离散量 on_s / off_s / transitions / 各值累计秒数。"""
    pid = _pid(key_or_id)
    s, e = _rng(start, end)
    p = _by_id.get(pid) or {}
    r = ts_raw_t
    with db.engine.connect() as c:
        n, mn, mx, av = c.execute(sa.select(sa.func.count(r.c.value), sa.func.min(r.c.value), sa.func.max(r.c.value), sa.func.avg(r.c.value))
                                  .where(r.c.point == pid, r.c.ts >= s, r.c.ts < e)).first()
    out = {'point': pid, 'start': _iso(s), 'end': _iso(e), 'n': n, 'min': mn, 'max': mx, 'avg': float(av) if av is not None else None}
    if p.get('discrete'):
        segs = segments(pid, s, e)
        by_val = defaultdict(float)
        on = trans = 0
        for sg in segs:
            st, en = max(_dt(sg['start']), s), min(_dt(sg['end']) if sg['end'] else e, e)
            d = max(0.0, (en - st).total_seconds())
            key = sg['text'] if sg['text'] is not None else sg['value']
            by_val[str(key)] += d
            if (sg['value'] or 0) != 0 and sg['text'] is None:
                on += d
            if sg['prev'] is not None and _dt(sg['start']) >= s:
                trans += 1
        out.update(on_s=round(on, 3), off_s=round(max(0.0, (e - s).total_seconds() - on), 3), transitions=trans, by_value={k: round(v, 3) for k, v in by_val.items()})
    return out


def write_rows(rows, conn=None) -> int:
    """写已经规范好的行 [(point_id, ts, value, text, q)]（磁盘缓存补传用）。"""
    return _write_rows(list(rows), conn) if rows else 0
