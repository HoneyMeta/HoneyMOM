"""数据库访问：SQLAlchemy Core。默认 SQLite（data/mes.db），生产换 PostgreSQL 只改 db_url。

所有写操作都在事件循环线程里同步执行（单进程），采集和接口不会并发写同一行。
"""
from .env import get as env_get
from .i18n import _tr
import json
import os
import re
import threading
from datetime import date, datetime, timedelta

import sqlalchemy as sa

from . import i18n as _i18n

from .schema import BY_NAME, TABLES, metadata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 可写数据目录：默认 <ROOT>/data；环境变量 MOM_DATA（旧名 HM_DATA）挪到别处（测试副本、多套部署）
DATA = os.path.abspath(env_get('MOM_DATA')) if env_get('MOM_DATA') else os.path.join(ROOT, 'data')
engine: sa.Engine = None  # type: ignore


def init(url: str | None = None, migrate_db: bool = True):
    """建引擎并把库升级到最新（migrations.upgrade），再做漂移检查。MOM_DB（旧名 MES_DB）选库：
    sqlite:///路径（默认 <数据目录>/mes.db）或 postgresql+psycopg://用户:密码@主机:端口/库。"""
    global engine
    from . import compat
    url = url or env_get('MOM_DB') or 'sqlite:///' + os.path.join(DATA, 'mes.db').replace('\\', '/')
    if url.startswith('sqlite:///'):
        os.makedirs(os.path.dirname(url[10:]) or '.', exist_ok=True)
    if url.startswith('sqlite'):
        engine = sa.create_engine(url, connect_args={'check_same_thread': False, 'timeout': float(env_get('MOM_SQLITE_TIMEOUT', 30))})

        @sa.event.listens_for(engine, 'connect')
        def _pragma(conn, _):
            c = conn.cursor()
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('PRAGMA synchronous=NORMAL')
            c.close()
            compat.sqlite_register(conn)
    else:
        engine = sa.create_engine(url, pool_size=int(env_get('MOM_DB_POOL', 10)), max_overflow=20, pool_pre_ping=True, pool_recycle=1800)
    if migrate_db:
        from . import migrations
        migrations.upgrade(engine)
        problems = migrations.check(engine)
        if problems:
            raise RuntimeError(_tr('数据库结构和 schema.py 不一致（改了 schema.py 却没写迁移？见 docs/dev/foundation.md「迁移」）：\n  ')
                               + '\n  '.join(problems))
    return engine


def is_pg(conn_or_engine=None) -> bool:
    return (conn_or_engine or engine).dialect.name == 'postgresql'


def migrate(eng):
    """4.7 的临时补列，5.0 起由版本化迁移（honeymom/migrations）取代。保留这个名字给老脚本调用：等同执行全部待执行迁移。"""
    from . import migrations
    return [a for r in migrations.upgrade(eng) for a in r.get('actions', [])]


class Clock:
    """数据时钟：以采集到的数据时间为准（虚拟工厂会加速运行）；没有数据时用系统时间。"""
    data_now: datetime | None = None
    _behind = 0          # 连续收到明显更早的时间的次数

    def now(self) -> datetime:
        return self.data_now or datetime.now().replace(microsecond=0)

    def feed(self, ts: datetime):
        if not ts:
            return
        if self.data_now is None or ts > self.data_now:
            self.data_now, self._behind = ts, 0
        elif (self.data_now - ts).total_seconds() > 2 * 3600:
            # 数据源重置（如虚拟工厂点了"重置"，仿真时间回到起点）：持续收到早 2 小时以上的时间，就跟着回退
            self._behind += 1
            if self._behind >= 30:
                self.data_now, self._behind = ts, 0
        else:
            self._behind = 0


clock = Clock()


def clock_wall() -> datetime:
    """墙上时间（审计、备份、API 密钥用；业务数据用 clock.now() 数据时钟）。"""
    return datetime.now().replace(microsecond=0)


def parse_dt(v):
    if v is None or v == '':
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = str(v).replace('T', ' ').replace('Z', '')[:19]
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


EXT_COERCE = None      # M3：fn(实体, 行) -> 自定义字段字典 | None（impl/cf.py 挂上）
EXT_WRITE = None       # M3：fn(conn, 实体, id, 自定义字段字典) -> 改前的值（写 ext 列；impl/cf.py 挂上）


def coerce(entity: str, row: dict) -> dict:
    """把前端传来的值按字段类型转换。"""
    e = BY_NAME[entity]
    out = {}
    for f in e.fields:
        if f.name not in row:
            continue
        v = row[f.name]
        if v == '' and f.t not in ('str', 'text'):
            v = None
        try:
            if v is None:
                pass
            elif f.t in ('int', 'ref'):
                v = int(float(v))
            elif f.t == 'float':
                v = float(v)
            elif f.t == 'bool':
                v = v if isinstance(v, bool) else str(v).lower() in ('1', 'true', 'yes', 'on', '是')
            elif f.t == 'datetime':
                v = parse_dt(v)
            elif f.t == 'date':
                d = parse_dt(v)
                v = d.date() if d else None
            elif f.t == 'json' and isinstance(v, str):
                v = json.loads(v) if v.strip() else None
        except (TypeError, ValueError):
            raise ValueError(_tr('{label} 格式不对：{v}', label=f.label, v=v))
        out[f.name] = v
    if EXT_COERCE is not None:          # M3 自定义字段：行里的 x_… → ext（impl/cf.py）
        x = EXT_COERCE(entity, row)
        if x is not None:
            out['ext'] = x
    return out


_code_lock = threading.Lock()
_code_issued: dict = {}          # (实体, 前缀) -> 本进程最近发出的流水号（还没提交的事务也算）


def next_code(conn, entity: str, pattern: str, when: datetime | None = None) -> str:
    """按格式生成单号：{d}=yymmdd，{n:03}=当天流水。

    流水号按"数值"取最大再加一：流水超过位数（NCR 一天上千条 → NCR2611041000）后按字符串排序会把 999 排在 1000 前面，
    以前因此一直生成同一个号、插入撞唯一约束丢数据。生成后再查一次库，已存在就往后顺延（数据时钟回退、手工录入的号都不会撞）。
    另外记住本进程发出去的最大流水号（加锁）：两个并发事务（接口线程 + 采集）都还没提交时也不会拿到同一个号。"""
    if conn.dialect.name == 'postgresql':
        # 多进程 / 多实例：同一实体同一前缀的取号在事务内串行（事务结束才释放，后来的事务等前一个提交后再取最大值）。
        # 必须在进程锁外面拿：否则"等咨询锁的线程"占着进程锁，持有咨询锁的事务再取号就死锁了。同一连接重复加锁可重入。
        when = when or clock.now()
        prefix = pattern.split('{n')[0].format(d=when.strftime('%y%m%d'))
        conn.execute(sa.text('SELECT pg_advisory_xact_lock(hashtext(:k))'), {'k': f'code:{entity}:{prefix}'})
    with _code_lock:
        code, key, n = _next_code(conn, entity, pattern, when)
        _code_issued[key] = n
        return code


def _next_code(conn, entity, pattern, when):
    when = when or clock.now()
    d = when.strftime('%y%m%d')
    prefix = pattern.split('{n')[0].format(d=d)
    tail = pattern[len(pattern.split('{n')[0]):]
    suffix = tail[tail.index('}') + 1:] if '}' in tail else ''          # 流水号后面的固定部分（目前的格式都没有）
    t = TABLES[entity]
    like = prefix.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    # 同前缀里最长的号流水最大：先按长度、再按字符串倒序取一批，挑出"前缀 + 纯数字"的
    cand = conn.execute(sa.select(t.c.code).where(t.c.code.like(like, escape='\\'))
                        .order_by(sa.func.length(t.c.code).desc(), t.c.code.desc()).limit(50)).scalars().all()
    num = re.compile(re.escape(prefix) + r'(\d+)' + re.escape(suffix) + '$')
    nums = [int(m.group(1)) for m in (num.match(x or '') for x in cand) if m]
    if not nums and len(cand) == 50:            # 前 50 个都是带别的尾巴的号：退回全量扫描
        nums = [int(m.group(1)) for m in (num.match(x or '') for x in conn.execute(sa.select(t.c.code).where(t.c.code.like(like, escape='\\'))).scalars()) if m]
    key = (entity, prefix + '\0' + suffix)
    n = max(max(nums) + 1 if nums else 1, _code_issued.get(key, 0) + 1)
    for _ in range(1000):
        code = pattern.format(d=d, n=n)
        if conn.execute(sa.select(t.c.id).where(t.c.code == code).limit(1)).first() is None:
            return code, key, n
        n += 1
    return code, key, n


def insert(conn, entity: str, row: dict, raw=False) -> int:
    e, t = BY_NAME[entity], TABLES[entity]
    row = _i18n.seed_row(row)
    data = row if raw else coerce(entity, row)
    for f in e.fields:
        if f.auto and not data.get(f.name):
            data[f.name] = next_code(conn, entity, f.auto)
        if f.name not in data and f.default is not None:
            data[f.name] = f.default
    now = clock.now()
    data.setdefault('created_at', now)
    data['updated_at'] = now
    xt = data.pop('ext', None)  # M3 insert：自定义字段不在表定义里，单独写 ext 列
    rid = conn.execute(t.insert().values(**data)).inserted_primary_key[0]
    if xt and EXT_WRITE is not None:
        EXT_WRITE(conn, entity, rid, xt)
        data = {**data, **xt}
    _audit(conn, 'create', entity, rid, None, data)
    return rid


def update(conn, entity: str, rid: int, row: dict, raw=False):
    t = TABLES[entity]
    row = _i18n.seed_row(row)
    data = row if raw else coerce(entity, row)
    before = get(conn, entity, rid) if _audit_on() else None
    data['updated_at'] = clock.now()
    xt = data.pop('ext', None)  # M3 update：自定义字段只改传进来的键
    conn.execute(t.update().where(t.c.id == rid).values(**data))
    if xt and EXT_WRITE is not None:
        old = EXT_WRITE(conn, entity, rid, xt)
        if before is not None:
            before, data = {**before, **{k: old.get(k) for k in xt}}, {**data, **xt}
    if before is not None:
        _audit(conn, 'update', entity, rid, before, data)


def delete(conn, entity: str, rid: int):
    """删除一条（自动审计改前内容）。"""
    t = TABLES[entity]
    before = get(conn, entity, rid) if _audit_on() else None
    conn.execute(t.delete().where(t.c.id == rid))
    if before is not None:
        _audit(conn, 'delete', entity, rid, before, None)


def _audit_on():
    from . import audit
    return audit.auto_enabled()


def _audit(conn, op, entity, rid, before, after):
    from . import audit
    audit.record(conn, op, entity, rid, before, after)


def get(conn, entity: str, rid=None, **where) -> dict | None:
    t = TABLES[entity]
    q = sa.select(t)
    if rid is not None:
        q = q.where(t.c.id == rid)
    for k, v in _i18n.seed_row(where).items():
        q = q.where(t.c[k] == v)
    r = conn.execute(q.limit(1)).mappings().first()
    return dict(r) if r else None


def rows(conn, entity: str, *conds, order=None, limit=None, **where) -> list[dict]:
    t = TABLES[entity]
    q = sa.select(t)
    for c in conds:
        q = q.where(c)
    for k, v in _i18n.seed_row(where).items():
        q = q.where(t.c[k] == v)
    if order is not None:
        q = q.order_by(*order) if isinstance(order, (tuple, list)) else q.order_by(order)
    if limit:
        q = q.limit(limit)
    return [dict(r) for r in conn.execute(q).mappings()]


def setting(conn, key, default=None):
    r = get(conn, 'setting', key=key)
    return r['value'] if r else default


def set_setting(conn, key, value, remark=''):
    r = get(conn, 'setting', key=key)
    if r:
        update(conn, 'setting', r['id'], {'value': str(value)}, raw=True)
    else:
        insert(conn, 'setting', {'key': key, 'value': str(value), 'remark': remark}, raw=True)


def jsonable(v):
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(v, date):
        return v.isoformat()
    return v


def day_range(d: date | str | None = None):
    if d is None:
        d = clock.now().date()
    if isinstance(d, str):
        d = parse_dt(d).date()
    s = datetime(d.year, d.month, d.day)
    return s, s + timedelta(days=1)
