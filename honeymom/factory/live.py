"""逐点核对与值质量（接入中心 → 逐点核对）。

采集程序每收到一个值就记一笔（内存，不碰数据库）：点表里的点 → PointStats；点表里没有的地址 → Unknown（待确认队列，每 10 秒落库到 onboard_seen）。
核对颜色（check）：
  red     点表里有、一直没收到值（或超过 max(10×采集周期, 10 分钟) 没再收到）
  yellow  收到了、但点表里没有（待确认队列）
  orange  收到了、但超出正常范围 / 报警限
  green   正常收到、在范围内
  grey    已忽略 / 停用
值质量（flags）：stuck 一直不变（数值点，设备在运行时也不变）、out_of_range 超范围、uncorrelated 和运行状态不相关（功率 / 负载 / 计数在停机和运行时没区别）、
  bad_quality 源端质量码是坏、type_mismatch 值的类型和点表的数据类型对不上。
覆盖率 = 已确认 / 总点数。
"""
from __future__ import annotations

from ..i18n import _tr
import threading
import time
from collections import defaultdict

import sqlalchemy as sa

from .. import db
from ..db import clock

RUN_STATES = ('Executing',)
STOP_STATES = ('Stopped', 'Idle', 'Planned', 'Setup', 'Starved', 'Blocked')


class PointStats:
    __slots__ = ('n', 'first', 'last', 'value', 'vmin', 'vmax', 'prev', 'ups', 'downs', 'distinct', 'changes', 'run_sum', 'run_n', 'stop_sum', 'stop_n',
                 'run_changes', 'stop_changes', 'bad', 'types', 'last_change')

    def __init__(self):
        self.n = 0
        self.first = self.last = self.last_change = None
        self.value = self.vmin = self.vmax = self.prev = None
        self.ups = self.downs = self.changes = self.bad = 0
        self.distinct = set()
        self.run_sum = self.stop_sum = 0.0
        self.run_n = self.stop_n = self.run_changes = self.stop_changes = 0
        self.types = set()

    def add(self, v, q, state):
        now = time.time()
        self.n += 1
        self.first = self.first or now
        self.last = now
        if q == 2:
            self.bad += 1
        self.types.add(type(v).__name__)
        num = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else (1.0 if v is True else 0.0 if v is False else None)
        changed = self.value is not None and v != self.value
        if changed:
            self.changes += 1
            self.last_change = now
            if state in RUN_STATES:
                self.run_changes += 1
            elif state in STOP_STATES:
                self.stop_changes += 1
        if num is not None:
            self.vmin = num if self.vmin is None else min(self.vmin, num)
            self.vmax = num if self.vmax is None else max(self.vmax, num)
            if isinstance(self.value, (int, float)) and not isinstance(self.value, bool):
                if num > self.value:
                    self.ups += 1
                elif num < self.value:
                    self.downs += 1
            if state in RUN_STATES:
                self.run_sum += num
                self.run_n += 1
            elif state in STOP_STATES:
                self.stop_sum += num
                self.stop_n += 1
        if len(self.distinct) < 12:
            self.distinct.add(v if isinstance(v, (int, float, bool, str)) else str(v))
        self.prev, self.value = self.value, v

    def out(self) -> dict:
        return {'n': self.n, 'first': self.first, 'last': self.last, 'value': _jsv(self.value), 'min': self.vmin, 'max': self.vmax, 'ups': self.ups,
                'downs': self.downs, 'changes': self.changes, 'distinct': len(self.distinct), 'values': sorted(map(str, self.distinct))[:12],
                'run_avg': self.run_sum / self.run_n if self.run_n else None, 'stop_avg': self.stop_sum / self.stop_n if self.stop_n else None,
                'run_n': self.run_n, 'stop_n': self.stop_n, 'run_changes': self.run_changes, 'stop_changes': self.stop_changes, 'bad': self.bad,
                'types': sorted(self.types), 'last_change': self.last_change}


def _jsv(v):
    if isinstance(v, (int, float, bool, str)) or v is None:
        return v
    return str(v)


class Live:
    def __init__(self):
        self.pt: dict[int, PointStats] = defaultdict(PointStats)
        self.unknown: dict[tuple, dict] = {}
        self.state: dict[int, str] = {}                  # 设备当前运行状态（按用途 state 的点）
        self.ds_seen: dict[int, float] = {}
        self.ds_count: dict[int, int] = defaultdict(int)
        self.alternate: dict[int, int] = defaultdict(int)
        self.started = time.time()
        self.lock = threading.Lock()
        self._dirty_unknown: set = set()

    def hit(self, m: dict, v, q=0):
        """点表里的点收到一个值（m = model.INDEX 里的点位）。"""
        if m.get('role') == 'state' and m.get('eid'):
            from .model import canon_state
            self.state[m['eid']] = canon_state(v)
        self.pt[m['id']].add(v, q, self.state.get(m.get('eid')))
        self.ds_seen[m['ds']] = time.time()
        self.ds_count[m['ds']] += 1

    def miss(self, ds_id: int, address: str, v, attrs: dict | None = None, hint: str | None = None, kind='value', alternate=False):
        """收到点表里没有的地址。alternate=True：是点表里另一协议上已有的同一个点（比如 MQTT 全量转发了 OPC UA 设备的点），不进待确认。"""
        self.ds_seen[ds_id] = time.time()
        if alternate:
            self.alternate[ds_id] += 1
            return
        k = (ds_id, address)
        with self.lock:
            u = self.unknown.get(k)
            if u is None:
                if len(self.unknown) > 50000:
                    return
                u = self.unknown[k] = {'stats': PointStats(), 'attrs': attrs or {}, 'hint': hint, 'kind': kind, 'first': clock.now()}
            elif attrs:
                u['attrs'].update(attrs)
            u['stats'].add(v, 0, None)
            u['last'] = clock.now()
            self._dirty_unknown.add(k)

    def flush_seen(self):
        """待确认队列落库（采集工作线程每 10 秒调一次）。"""
        from ..schema_factory import onboard_seen_t as t
        from .. import compat
        with self.lock:
            keys, self._dirty_unknown = self._dirty_unknown, set()
            rows = []
            for k in keys:
                u = self.unknown.get(k)
                if not u:
                    continue
                st = u['stats'].out()
                rows.append({'datasource': k[0], 'address': k[1][:400], 'sample': str(st['value'])[:400] if st['value'] is not None else None,
                             'dtype': _guess_dtype(u['stats']), 'count': st['n'], 'first_seen': u['first'], 'last_seen': u.get('last') or u['first'],
                             'stats': {x: st[x] for x in ('min', 'max', 'ups', 'downs', 'changes', 'distinct', 'values')}, 'attrs': {**u['attrs'], 'hint': u.get('hint')},
                             'kind': u.get('kind')})
        if not rows:
            return 0
        with db.engine.begin() as c:
            have = {(r['datasource'], r['address']): r for r in c.execute(sa.select(t.c.id, t.c.datasource, t.c.address, t.c.status)).mappings()}
            for r in rows:
                h = have.get((r['datasource'], r['address']))
                if h:
                    c.execute(t.update().where(t.c.id == h['id']).values(sample=r['sample'], dtype=r['dtype'], count=r['count'], last_seen=r['last_seen'],
                                                                         stats=r['stats'], attrs=r['attrs']))
                else:
                    compat.insert_ignore(c, t, [{**r, 'status': 'new'}], keys=['datasource', 'address'])
        return len(rows)

    def persist_points(self, every_s=60):
        """点位的采集统计写回 tag_point.stats（重启后核对页还能看到最后一次的值和统计）。"""
        from ..schema import TABLES
        tp = TABLES['tag_point']
        items = [(pid, s.out()) for pid, s in list(self.pt.items()) if s.last and s.last > time.time() - every_s - 5]
        if not items:
            return 0
        with db.engine.begin() as c:
            for pid, st in items:
                c.execute(tp.update().where(tp.c.id == pid).values(stats={**st, 'at': db.jsonable(clock.now())}))
        return len(items)


LIVE = Live()


def _guess_dtype(s: PointStats) -> str:
    t = s.types
    if t <= {'bool'}:
        return 'bool'
    if t <= {'int', 'bool'} and s.distinct and all(str(x) in ('0', '1', 'True', 'False') for x in s.distinct):
        return 'bool'
    if t <= {'int'}:
        return 'int'
    if t <= {'int', 'float'}:
        return 'float'
    return 'string'


# ───────────────────────── 核对 ─────────────────────────
def flags_of(p: dict, st: dict | None, dev_state_known: bool) -> list[str]:
    from .model import parse_range
    out = []
    if not st or not st.get('n'):
        return out
    if st.get('bad'):
        out.append('bad_quality')
    num = st.get('min') is not None
    dt = p.get('dtype')
    if dt in ('float', 'int') and not num and st.get('types') and set(st['types']) <= {'str'}:
        out.append('type_mismatch')
    lo, hi = parse_range(p.get('normal_range'))
    alo, ahi = p.get('alarm_lo'), p.get('alarm_hi')
    v = st.get('value')
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if (lo is not None and v < lo) or (hi is not None and v > hi) or (alo is not None and v < alo) or (ahi is not None and v > ahi):
            out.append('out_of_range')
    observed = (st.get('last') or 0) - (st.get('first') or 0)
    if num and dt != 'bool' and p.get('role') not in ('param', 'state', 'alarm', 'step') and st.get('n', 0) >= 20 and observed > 300 \
            and st.get('changes', 0) == 0 and st.get('run_n', 0) >= 10:
        out.append('stuck')
    if dev_state_known and p.get('role') in ('power', 'load', 'count') and st.get('run_n', 0) >= 10 and st.get('stop_n', 0) >= 10:
        ra, sa_ = st.get('run_avg'), st.get('stop_avg')
        if p['role'] == 'count':
            if st.get('stop_changes', 0) > st.get('run_changes', 0) and st.get('run_changes', 0) == 0:
                out.append('uncorrelated')
        elif ra is not None and sa_ is not None and abs(ra - sa_) <= max(abs(ra), abs(sa_), 1e-6) * 0.05:
            out.append('uncorrelated')
    return out


FLAG_CN = {'stuck': '值一直不变', 'out_of_range': '超出正常范围', 'uncorrelated': '和运行状态不相关', 'bad_quality': '源端质量差', 'type_mismatch': '类型和点表不符'}
COLOR_CN = {'red': '读不到', 'yellow': '点表里没有', 'orange': '超范围 / 异常', 'green': '正常', 'grey': '已忽略'}


def check(c, datasource=None, device=None, user=None) -> dict:
    """逐点核对：每个采集点一行（颜色、最后值、统计、质量问题），加上待确认队列（黄）。"""
    from ..schema import TABLES
    from ..schema_factory import onboard_seen_t as st_t
    from .. import perms
    tp = TABLES['tag_point']
    allowed = perms.allowed_ids(user, 'equipment') if user else None
    q = sa.select(tp)
    if datasource:
        q = q.where(tp.c.datasource == int(datasource))
    if device:
        q = q.where(tp.c.equipment == int(device))
    eqs = {e['id']: e for e in db.rows(c, 'equipment')}
    dss = {d['id']: d for d in db.rows(c, 'datasource')}
    now = time.time()
    rows, cnt = [], defaultdict(int)
    by_dev = {}
    for p in c.execute(q.order_by(tp.c.datasource, tp.c.equipment, tp.c.id)).mappings():
        p = dict(p)
        if allowed is not None and p['equipment'] and p['equipment'] not in allowed:
            continue
        s = LIVE.pt.get(p['id'])
        st = s.out() if s else None
        if st is None and p.get('stats'):                    # 进程重启：用上次落库的统计（但不算"现在收到"）
            st = {**p['stats'], 'stale': True}
        period = max(float(p.get('period_ms') or 1000) / 1000, 1)
        fresh = s is not None and s.last and now - s.last < max(10 * period, 600)
        retained = s is not None and s.n > 0                 # MQTT 只在变化时发：收到过就算读得到
        flags = flags_of(p, st, bool(p['equipment'] and p['equipment'] in LIVE.state)) if s else []
        if not p['enabled'] or p.get('status') == 'ignored' or not (dss.get(p['datasource']) or {}).get('enabled', True):
            color = 'grey'
        elif not retained:
            color = 'red'
        elif 'out_of_range' in flags or 'bad_quality' in flags or 'type_mismatch' in flags:
            color = 'orange'
        else:
            color = 'green'
        cnt[color] += 1
        e = eqs.get(p['equipment'])
        d = by_dev.setdefault(p['equipment'], {'equipment': p['equipment'], 'code': e['code'] if e else None, 'name': e['name'] if e else _tr('（未分配设备）'),
                                               'total': 0, 'confirmed': 0, 'red': 0, 'orange': 0, 'green': 0, 'grey': 0, 'flags': 0})
        d['total'] += 1
        d[color] += 1
        d['confirmed'] += 1 if p.get('status') == 'confirmed' else 0
        d['flags'] += 1 if flags else 0
        rows.append({'id': p['id'], 'code': p['code'], 'name': p['name'], 'device': e['code'] if e else None, 'device_name': e['name'] if e else None,
                     'equipment': p['equipment'], 'datasource': (dss.get(p['datasource']) or {}).get('code'), 'protocol': p.get('protocol'),
                     'address': p['address'], 'meaning': p['desc'], 'unit': p['unit'], 'dtype': p['dtype'], 'role': p['role'], 'status': p.get('status'),
                     'confidence': p.get('confidence'), 'origin': p.get('origin'), 'normal_range': p.get('normal_range'), 'color': color, 'flags': flags,
                     'flags_cn': [FLAG_CN[f] for f in flags], 'value': (st or {}).get('value'), 'n': (st or {}).get('n', 0), 'min': (st or {}).get('min'),
                     'max': (st or {}).get('max'), 'fresh': bool(fresh), 'stale': bool((st or {}).get('stale')),
                     'age_s': round(now - s.last, 1) if s and s.last else None})
    sq = sa.select(st_t).where(st_t.c.status == 'new')
    if datasource:
        sq = sq.where(st_t.c.datasource == int(datasource))
    yellow = [{'id': r['id'], 'datasource': (dss.get(r['datasource']) or {}).get('code'), 'datasource_id': r['datasource'], 'address': r['address'],
               'sample': r['sample'], 'dtype': r['dtype'], 'count': r['count'], 'first_seen': db.jsonable(r['first_seen']), 'last_seen': db.jsonable(r['last_seen']),
               'attrs': r['attrs'], 'stats': r['stats'], 'kind': r['kind'], 'color': 'yellow'} for r in c.execute(sq.order_by(st_t.c.id).limit(3000)).mappings()]
    total = len(rows)
    confirmed = sum(1 for r in rows if r['status'] == 'confirmed')
    active = [r for r in rows if r['color'] != 'grey']
    return {'rows': rows, 'yellow': yellow, 'counts': {**{k: cnt.get(k, 0) for k in ('red', 'orange', 'green', 'grey')}, 'yellow': len(yellow)},
            'total': total, 'confirmed': confirmed, 'coverage': round(confirmed / len(active), 4) if active else 0.0,
            'devices': sorted(by_dev.values(), key=lambda d: (d['code'] is None, d['code'] or '')),
            'alternate': dict(LIVE.alternate), 'since': round(now - LIVE.started), 'colors': COLOR_CN, 'flags': FLAG_CN}


def collector_status(c) -> list[dict]:
    from ..collector import COLLECTOR
    out = []
    for d in db.rows(c, 'datasource', order=db.metadata.tables['datasource'].c.code):
        seen = LIVE.ds_seen.get(d['id'])
        out.append({'id': d['id'], 'code': d['code'], 'name': d['name'], 'protocol': d['protocol'], 'endpoint': d['endpoint'], 'prefix': d['prefix'],
                    'enabled': d['enabled'], 'status': COLLECTOR.status.get(d['id']) or d['status'], 'values': LIVE.ds_count.get(d['id'], 0),
                    'last_seen_s': round(time.time() - seen, 1) if seen else None, 'discovering': COLLECTOR.discovering(d['id']),
                    'options': {k: v for k, v in (d.get('options') or {}).items() if not k.startswith('_')}})
    return out
