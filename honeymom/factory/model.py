"""统一工厂模型：工厂 → 车间 → 产线 → 工位 → 设备 → 采集点。

一份模型同时驱动：设备台账（equipment）、数据采集（datasource + tag_point，采集程序按地址映射）、2D / 3D 孪生（设备的 x/y/w/d/rot/model3d +
平面图）、AI 数据目录（catalog：每个点位的含义 / 单位 / 用途 / 时序键）。

这里是写模型的唯一入口（导入点表、交付包、模板套用、AI 草稿通过、拖拽落位都调这里），保证：
  - 层级（车间 / 产线 / 工位）按名称自动建，编码稳定；
  - 采集点的时序键 ts_key = <数据源编码>:<设备编码>/<点位路径>（没分配设备时设备段是 _）；改设备时时序点改名，历史不丢；
  - 用途 / 值映射 / 语义钩子缺省按设备模板补。
"""
from __future__ import annotations

from ..i18n import _tr
import json
import re
import time
from datetime import datetime

import sqlalchemy as sa

from .. import db
from ..db import clock
from ..schema import TABLES
from . import templates as tpl_mod

PT_FIELDS = ('code', 'equipment', 'datasource', 'name', 'address', 'dtype', 'unit', 'scale', 'offset', 'role', 'desc', 'rw', 'protocol', 'period_ms',
             'alarm_lo', 'alarm_hi', 'normal_range', 'encoding', 'value_map', 'sem', 'status', 'confidence', 'origin', 'remark', 'enabled')
DEV_FIELDS = ('code', 'name', 'etype', 'template', 'line', 'model', 'vendor', 'ideal_cycle_s', 'x', 'y', 'w', 'd', 'rot', 'model3d', 'location', 'remark')
DTYPES = {'bool': 'bool', 'boolean': 'bool', 'bit': 'bool', 'int': 'int', 'integer': 'int', 'int16': 'int', 'int32': 'int', 'uint16': 'int', 'word': 'int',
          'dint': 'int', 'float': 'float', 'real': 'float', 'double': 'float', 'float32': 'float', 'lreal': 'float', 'num': 'float',
          'string': 'string', 'str': 'string', 'text': 'string', 'char': 'string'}


# ───────────────────────── 模板 ─────────────────────────
_tpl_cache = (0.0, [])


def templates_all(conn=None, refresh=False) -> list[dict]:
    global _tpl_cache
    if not refresh and time.time() - _tpl_cache[0] < 10 and _tpl_cache[1]:
        return _tpl_cache[1]
    t = TABLES['device_template']

    def go(c):
        return [dict(r) for r in c.execute(sa.select(t).order_by(t.c.code)).mappings()]
    rows = go(conn) if conn is not None else _with_conn(go)
    if not rows:
        rows = [dict(x, builtin=True) for x in tpl_mod.BUILTIN]
    _tpl_cache = (time.time(), rows)
    return rows


def _with_conn(fn):
    with db.engine.connect() as c:
        return fn(c)


def template(code, conn=None) -> dict | None:
    for t in templates_all(conn):
        if t['code'] == code:
            return t
    return tpl_mod.BY_CODE.get(code)


def invalidate():
    global _tpl_cache
    _tpl_cache = (0.0, [])
    INDEX.dirty = True


# ───────────────────────── 规范化 ─────────────────────────
def norm_dtype(v) -> str | None:
    if v in (None, ''):
        return None
    return DTYPES.get(str(v).strip().lower(), None)


def parse_range(s):
    """'0~85' / '0-85' / '1420～1530' → (lo, hi)；只写一边也行（'~85'）。"""
    if s in (None, ''):
        return None, None
    m = re.match(r'^\s*(-?[\d.]+)?\s*[~～\-–—至]\s*(-?[\d.]+)?\s*$', str(s))
    if not m:
        return None, None
    lo, hi = m.group(1), m.group(2)
    try:
        return (float(lo) if lo not in (None, '', '.') else None), (float(hi) if hi not in (None, '', '.') else None)
    except ValueError:
        return None, None


ENC_RX = re.compile(r'\b(u?int16|u?int32|float32|float64|int64)\b\s*(ABCD|CDAB|BADC|DCBA)?', re.I)
MAP_RX = re.compile(r'(-?\d+)\s*[=＝:：]\s*([A-Za-z_一-鿿][\w一-鿿]*)')


def parse_remark(remark: str, role: str | None = None):
    """备注里的寄存器编码（float32 ABCD，2 个寄存器）和状态码对照（状态码：0=Idle、1=Executing…）。"""
    enc, vmap = None, None
    if remark:
        m = ENC_RX.search(remark)
        if m:
            enc = m.group(1).lower() + (' ' + m.group(2).upper() if m.group(2) else '')
        if '状态码' in remark or '=' in remark or '＝' in remark:
            pairs = MAP_RX.findall(remark.split('状态码', 1)[-1])
            if len(pairs) >= 2:
                vmap = {k: v for k, v in pairs}
    return enc, vmap


def point_name(code: str, device: str | None, address: str | None = None) -> str:
    """点位路径：编码是 <设备编码>.<路径用 . 连> 时还原成 a/b/c（和 MQTT 主题 / OPC UA 浏览路径一致）；否则用编码本身。"""
    code = str(code or '').strip()
    if device and code.startswith(device + '.') and len(code) > len(device) + 1:
        return code[len(device) + 1:].replace('.', '/')
    return code


def ts_key(ds_code, dev_code, name) -> str:
    return f"{ds_code or '?'}:{dev_code or '_'}/{name}"


def norm_role(v) -> str | None:
    from ..schema_factory import ROLES
    if v in (None, ''):
        return None
    s = str(v).strip().lower()
    if s in ROLES:
        return s
    for k, lab in ROLES.items():
        if lab == str(v).strip():
            return k
    return 'other'


# ───────────────────────── 层级 ─────────────────────────
def _code_of(prefix, name, existing: set):
    base = re.sub(r'[^A-Za-z0-9]+', '', str(name or '').upper())[:12]
    if not base:
        base = f'{len(existing) + 1:02d}'
    code = f'{prefix}-{base}'
    i = 2
    while code in existing:
        code = f'{prefix}-{base}{i}'
        i += 1
    return code


class Hier:
    """按名称找 / 建 车间、产线、工位（一次导入里缓存）。"""

    def __init__(self, conn, plant_id):
        self.c, self.plant = conn, plant_id
        self.ws = {r['name']: r['id'] for r in db.rows(conn, 'workshop')}
        self.ws_codes = {r['code'] for r in db.rows(conn, 'workshop')}
        self.lines = {(r['workshop'], r['name']): r['id'] for r in db.rows(conn, 'line')}
        self.line_codes = {r['code'] for r in db.rows(conn, 'line')}
        self.created = {'workshop': 0, 'line': 0, 'workstation': 0}

    def workshop(self, name):
        if not name:
            return None
        if name not in self.ws:
            code = _code_of('WS', _pinyin_hint(name), self.ws_codes)
            self.ws_codes.add(code)
            nl = name.lower()
            wtype = 'foundry' if '铸' in name or 'cast' in nl or 'foundry' in nl else 'machining' if '机加' in name or '加工' in name or 'machin' in nl else 'warehouse' if '仓' in name or '物流' in name or 'wareh' in nl or 'logist' in nl or 'storage' in nl else 'other'
            self.ws[name] = db.insert(self.c, 'workshop', {'code': code, 'name': name, 'plant': self.plant, 'wtype': wtype}, raw=True)
            self.created['workshop'] += 1
        return self.ws[name]

    def line(self, ws_id, name):
        if not name:
            return None
        k = (ws_id, name)
        if k not in self.lines:
            # 同名产线（别的车间下）也算同一条：老库 / 4.7 导入的产线按名称复用
            same = next((v for (w, n), v in self.lines.items() if n == name), None)
            if same:
                self.lines[k] = same
                return same
            code = _code_of('L', _pinyin_hint(name), self.line_codes)
            self.line_codes.add(code)
            self.lines[k] = db.insert(self.c, 'line', {'code': code, 'name': name, 'workshop': ws_id}, raw=True)
            self.created['line'] += 1
        return self.lines[k]


AREA_HINT = {'熔炼': 'MELT', '造型': 'MOLD', '清理': 'CLEAN', '机加': 'MACH', '检测': 'INSP', '仓储': 'WH', '物流': 'LOG', '收货': 'RCV', '铸造': 'F', '机加车间': 'M',
             '仓储物流': 'W', '装配': 'ASSY', '压铸': 'DC', '注塑': 'IM', '公共': 'PUB', '涂装': 'PAINT', '焊接': 'WELD', '冲压': 'PRESS'}


def _pinyin_hint(name):
    for k in sorted(AREA_HINT, key=len, reverse=True):
        if k in str(name):
            return AREA_HINT[k]
    return re.sub(r'[^A-Za-z0-9]+', '', str(name)) or ''


# ───────────────────────── 设备 ─────────────────────────
def apply_device(c, d: dict, hier: Hier, origin='package') -> tuple[int, str]:
    """一行设备表（规范化后的 dict：code name type model vendor workshop line station x y w d rot model3d ideal_cycle_s remark）→ 设备 + 工位。
    返回 (设备 id, 'new' | 'updated' | 'same')。"""
    tpls = templates_all(c)
    tcode = d.get('template') or tpl_mod.match_type(tpls, d.get('type')) or 'other'
    tp = template(tcode, c) or {}
    from ..schema import BY_NAME
    etypes = next(f.enum for f in BY_NAME['equipment'].fields if f.name == 'etype')
    ws_id = hier.workshop(d.get('workshop'))
    line_id = hier.line(ws_id, d.get('line')) if d.get('line') else None
    row = {'name': d.get('name') or d['code'], 'etype': tcode if tcode in etypes else 'other', 'template': tcode, 'line': line_id,
           'model': d.get('model'), 'vendor': d.get('vendor'), 'ideal_cycle_s': d.get('ideal_cycle_s') or tp.get('ideal_cycle_s'),
           'x': d.get('x'), 'y': d.get('y'), 'w': d.get('w') or tp.get('w'), 'd': d.get('d') or tp.get('d'), 'rot': d.get('rot') or 0,
           'model3d': d.get('model3d'), 'location': d.get('line') or d.get('workshop'), 'remark': d.get('remark')}
    cur = db.get(c, 'equipment', code=d['code'])
    if cur:
        upd = {k: v for k, v in row.items() if v is not None and cur.get(k) != v}
        if upd:
            db.update(c, 'equipment', cur['id'], upd, raw=True)
        eid, st = cur['id'], ('updated' if upd else 'same')
    else:
        eid = db.insert(c, 'equipment', {'code': d['code'], 'status': 'offline', 'install_date': clock.now().date(), **row}, raw=True)
        st = 'new'
    if tcode not in ('crew', 'agv', 'plc', 'gateway', 'switch', 'mes') and line_id:
        wcode = d.get('station') or f"WS-{d['code']}"
        w = db.get(c, 'workstation', code=wcode)
        procs = db.rows(c, 'process', ptype=tp.get('process_type')) if tp.get('process_type') else []
        wrow = {'name': d.get('name') or d['code'], 'line': line_id, 'equipment': eid}
        if not w:
            db.insert(c, 'workstation', {'code': wcode, **wrow, 'process': procs[0]['id'] if procs else None,
                                         'group': tp.get('station_group') or d['code']}, raw=True)
            hier.created['workstation'] += 1
        elif w.get('line') != line_id or w.get('equipment') != eid:
            db.update(c, 'workstation', w['id'], wrow, raw=True)
    return eid, st


# ───────────────────────── 采集点 ─────────────────────────
def point_row(p: dict, dev: dict | None, ds: dict | None, origin: str) -> dict:
    """规范化的点表行（code device meaning unit dtype rw role protocol address scale offset period_ms alarm_lo alarm_hi normal_range remark）
    + 设备 + 数据源 → tag_point 的列。用途 / 值映射 / 语义钩子缺省按设备模板补。"""
    name = p.get('name') or point_name(p['code'], dev['code'] if dev else None, p.get('address'))
    tp = template(dev.get('template') or dev.get('etype'), None) if dev else None
    tpp, _caps = tpl_mod.match_point((tp or {}).get('points'), name) if tp else (None, {})
    role = norm_role(p.get('role')) or (tpp or {}).get('role')
    enc, vmap = parse_remark(p.get('remark') or '', role)
    if role == 'state' and not vmap and norm_dtype(p.get('dtype')) == 'int':
        vmap = dict(tpl_mod.STATE_CODES)          # 接入规范的标准状态码（0 待机 1 运行 2 缺料 3 堵料 4 停机 5 换型 6 计划停机）
    if tpp and tpp.get('map') and not vmap:
        vmap = tpp['map']
    row = {'code': p['code'], 'equipment': dev['id'] if dev else None, 'datasource': ds['id'] if ds else None, 'name': name,
           'address': p.get('address'), 'dtype': norm_dtype(p.get('dtype')) or (tpp or {}).get('dtype'), 'unit': p.get('unit') or (tpp or {}).get('unit'),
           'scale': _f(p.get('scale'), 1.0), 'offset': _f(p.get('offset'), 0.0), 'role': role, 'desc': p.get('meaning') or (tpp or {}).get('meaning'),
           'rw': 'RW' if str(p.get('rw') or (tpp or {}).get('rw') or 'R').upper() == 'RW' else 'R', 'protocol': p.get('protocol') or (ds or {}).get('protocol'),
           'period_ms': _i(p.get('period_ms')), 'alarm_lo': _f(p.get('alarm_lo'), (tpp or {}).get('lo')), 'alarm_hi': _f(p.get('alarm_hi'), (tpp or {}).get('hi')),
           'normal_range': p.get('normal_range') or (tpp or {}).get('normal'), 'encoding': p.get('encoding') or enc, 'value_map': vmap,
           'sem': p.get('sem') or (tpp or {}).get('sem') or ('sample' if (tpp or {}).get('sample') else None), 'remark': p.get('remark'),
           'origin': origin, 'enabled': True}
    row['ts_key'] = ts_key(ds['code'] if ds else None, dev['code'] if dev else None, name)
    return row


def _f(v, default=None):
    if v in (None, ''):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v):
    try:
        return int(float(v)) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


COMPARE = ('equipment', 'datasource', 'name', 'address', 'dtype', 'unit', 'scale', 'offset', 'role', 'desc', 'rw', 'protocol', 'period_ms', 'alarm_lo',
           'alarm_hi', 'normal_range', 'encoding', 'value_map', 'sem')


def upsert_point(c, row: dict, status='pending', confirmed_by=None) -> tuple[int, str]:
    """按 (点位编码, 数据源) 找已有采集点：有就更新变化的列（已确认的点内容变了 → 回到待确认），没有就新建。"""
    tp = TABLES['tag_point']
    cur = c.execute(sa.select(tp).where((tp.c.code == row['code']) & ((tp.c.datasource == row['datasource']) if row.get('datasource') else tp.c.datasource.is_(None)))
                    .limit(1)).mappings().first()
    if cur is None and row.get('address') and row.get('datasource'):
        cur = c.execute(sa.select(tp).where((tp.c.address == row['address']) & (tp.c.datasource == row['datasource'])).limit(1)).mappings().first()
    now = clock.now()
    if cur:
        cur = dict(cur)
        upd = {k: row[k] for k in COMPARE if k in row and row[k] is not None and _ne(cur.get(k), row[k])}
        if row.get('code') != cur.get('code'):
            upd['code'] = row['code']
        if upd:
            if 'ts_key' in row and row['ts_key'] != cur.get('ts_key'):
                upd['ts_key'] = row['ts_key']
                rekey_ts(c, cur.get('ts_key'), row['ts_key'], row.get('equipment'))
            material = set(upd) - {'desc', 'unit', 'remark', 'normal_range', 'alarm_lo', 'alarm_hi', 'period_ms', 'code'}
            if status == 'confirmed':
                upd.update(status='confirmed', confirmed_by=confirmed_by, confirmed_at=now)
            elif cur.get('status') == 'confirmed' and material:
                upd['status'] = 'pending'
            db.update(c, 'tag_point', cur['id'], upd, raw=True)
            return cur['id'], 'updated'
        return cur['id'], 'same'
    vals = {**row, 'status': status}
    if status == 'confirmed':
        vals.update(confirmed_by=confirmed_by, confirmed_at=now)
    return db.insert(c, 'tag_point', vals, raw=True), 'new'


def _ne(a, b):
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) > 1e-9
        except (TypeError, ValueError):
            return a != b
    return a != b


def rekey_ts(c, old, new, equipment=None):
    """时序点改名（设备分配 / 改数据源后）：ts_raw 按点位 id 关联，历史不丢。"""
    if not old or old == new:
        return
    from ..schema import ts_point_t as t
    if c.execute(sa.select(t.c.id).where(t.c.key == new)).first():
        return
    c.execute(t.update().where(t.c.key == old).values(key=new, equipment=equipment))
    try:
        from .. import ts
        ts.reset_cache()
    except Exception:
        pass


def set_point_device(c, pid: int, eid: int | None, name: str | None = None):
    """给点位分配设备（AI 草稿通过 / 手工）：同时改时序键。"""
    p = db.get(c, 'tag_point', pid)
    dev = db.get(c, 'equipment', eid) if eid else None
    ds = db.get(c, 'datasource', p['datasource']) if p['datasource'] else None
    nm = name or p['name']
    key = ts_key(ds['code'] if ds else None, dev['code'] if dev else None, nm)
    rekey_ts(c, p.get('ts_key'), key, eid)
    db.update(c, 'tag_point', pid, {'equipment': eid, 'name': nm, 'ts_key': key}, raw=True)


def confirm(c, ids, user_name, status='confirmed'):
    tp = TABLES['tag_point']
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    vals = {'status': status, 'confirmed_by': user_name if status == 'confirmed' else None, 'confirmed_at': clock.now() if status == 'confirmed' else None,
            'updated_at': clock.now()}
    n = 0
    for i in range(0, len(ids), 500):
        n += c.execute(tp.update().where(tp.c.id.in_(ids[i:i + 500])).values(**vals)).rowcount
    from .. import audit
    audit.audit(c, 'point_' + ('confirm' if status == 'confirmed' else status), 'tag_point', ids[0] if len(ids) == 1 else None,
                detail={'n': n, 'ids': ids[:200]}, target=_tr('采集点 {n} 个', n=n))
    return n


# ───────────────────────── 模板套用 ─────────────────────────
def apply_template(c, code: str, eids: list[int], create_points=False, ds_id=None) -> dict:
    """给设备套用模板：设备类型 / 尺寸 / 3D 缺省 / 工序；已有点位按模板补用途 / 语义 / 单位 / 含义（不覆盖点表里已填的）；
    create_points=True 时，模板里有、设备上没有的标准点位按 MQTT 统一命名空间建成待确认的采集点（地址 = <前缀><设备>/<点位>）。"""
    tp = template(code, c)
    if not tp:
        raise ValueError(_tr('没有模板 {code}', code=code))
    from ..schema import BY_NAME
    etypes = next(f.enum for f in BY_NAME['equipment'].fields if f.name == 'etype')
    out = {'devices': 0, 'points_updated': 0, 'points_created': 0}
    ds = db.get(c, 'datasource', ds_id) if ds_id else None
    for eid in eids:
        e = db.get(c, 'equipment', eid)
        if not e:
            continue
        upd = {'template': code, 'etype': code if code in etypes else e['etype'] or 'other'}
        for k in ('w', 'd', 'ideal_cycle_s'):
            if not e.get(k) and tp.get(k):
                upd[k] = tp[k]
        db.update(c, 'equipment', eid, upd, raw=True)
        e = db.get(c, 'equipment', eid)
        out['devices'] += 1
        names = set()
        for p in db.rows(c, 'tag_point', equipment=eid):
            names.add(p['name'])
            tpp, _ = tpl_mod.match_point(tp.get('points'), p['name'])
            if not tpp:
                continue
            fix = {}
            for col, key in (('role', 'role'), ('sem', 'sem'), ('unit', 'unit'), ('desc', 'meaning'), ('normal_range', 'normal'), ('alarm_lo', 'lo'), ('alarm_hi', 'hi')):
                if p.get(col) in (None, '', 'other') and tpp.get(key) not in (None, ''):
                    fix[col] = tpp[key]
            if not p.get('sem') and tpp.get('sample'):
                fix['sem'] = 'sample'
            if fix:
                db.update(c, 'tag_point', p['id'], fix, raw=True)
                out['points_updated'] += 1
        if create_points and ds:
            for tpp in tp.get('points') or []:
                if any(ch in tpp['name'] for ch in '{*') or tpp['name'] in names:
                    continue
                pref = ds.get('prefix') or ''
                row = point_row({'code': f"{e['code']}.{tpp['name'].replace('/', '.')}", 'address': f"{pref}{e['code']}/{tpp['name']}",
                                 'protocol': ds['protocol']}, e, ds, 'template')
                upsert_point(c, row)
                out['points_created'] += 1
    INDEX.dirty = True
    return out


# ───────────────────────── 工单产线 ─────────────────────────
def wo_line(c, wo: dict) -> int | None:
    """工单的产线 = 工艺路线里瓶颈工序（标准节拍最长；一样长取靠后的）的工位组 / 工位所在产线（数据范围用）。"""
    if not wo.get('route'):
        return None
    rs = TABLES['route_step']                    # 只取要用的列（迁移时别的模块后加的列可能还没建）
    steps = c.execute(sa.select(rs.c.station_group, rs.c.std_cycle_s, rs.c.seq).where(rs.c.route == wo['route'])).mappings().all()
    steps = sorted(steps, key=lambda s: (-(s['std_cycle_s'] or 0), -(s['seq'] or 0)))
    ws = TABLES['workstation']
    for s in steps:
        g = s.get('station_group')
        if not g:
            continue
        r = c.execute(sa.select(ws.c.line).where(((ws.c.group == g) | (ws.c.code == g) | (ws.c.code == 'WS-' + g)) & ws.c.line.isnot(None)).limit(1)).scalar()
        if r:
            return r
    return None


def derive_wo_lines(c, only_missing=True) -> int:
    t = TABLES['work_order']
    q = sa.select(t.c.id, t.c.route, t.c.line)
    if only_missing:
        q = q.where(t.c.line.is_(None))
    n = 0
    cache = {}
    for w in c.execute(q).mappings().all():
        if w['route'] not in cache:
            cache[w['route']] = wo_line(c, dict(w))
        ln = cache[w['route']]
        if ln and ln != w['line']:
            c.execute(t.update().where(t.c.id == w['id']).values(line=ln))
            n += 1
    return n


# ───────────────────────── 模型快照 / 版本 ─────────────────────────
def snapshot(c) -> dict:
    """整个工厂模型（层级 + 设备 + 采集点 + 数据源 + 平面图摘要），版本快照和比对用。按编码索引（不依赖 id）。"""
    pl = {r['id']: r for r in db.rows(c, 'plant')}
    ws = {r['id']: r for r in db.rows(c, 'workshop')}
    ln = {r['id']: r for r in db.rows(c, 'line')}
    eq = db.rows(c, 'equipment')
    eqc = {e['id']: e['code'] for e in eq}
    dss = {r['id']: r for r in db.rows(c, 'datasource')}
    out = {'plants': {p['code']: {'name': p['name']} for p in pl.values()},
           'workshops': {w['code']: {'name': w['name'], 'plant': (pl.get(w['plant']) or {}).get('code')} for w in ws.values()},
           'lines': {l['code']: {'name': l['name'], 'workshop': (ws.get(l['workshop']) or {}).get('code')} for l in ln.values()},
           'stations': {s['code']: {'name': s['name'], 'line': (ln.get(s['line']) or {}).get('code'), 'device': eqc.get(s['equipment']), 'group': s['group']}
                        for s in db.rows(c, 'workstation')},
           'devices': {e['code']: {k: _js(e.get(k)) for k in ('name', 'etype', 'template', 'model', 'vendor', 'ideal_cycle_s', 'x', 'y', 'w', 'd', 'rot', 'model3d')}
                       | {'line': (ln.get(e['line']) or {}).get('code')} for e in eq},
           'datasources': {d['code']: {'protocol': d['protocol'], 'endpoint': d['endpoint'], 'prefix': d['prefix'], 'enabled': d['enabled']} for d in dss.values()},
           'points': {}}
    for p in db.rows(c, 'tag_point'):
        k = f"{(dss.get(p['datasource']) or {}).get('code', '?')}|{p['code'] or p['name']}"
        out['points'][k] = {'device': eqc.get(p['equipment']), 'name': p['name'], 'address': p['address'], 'meaning': p['desc'], 'unit': p['unit'],
                            'dtype': p['dtype'], 'role': p['role'], 'rw': p['rw'], 'scale': p['scale'], 'offset': p['offset'], 'status': p['status'],
                            'normal_range': p['normal_range'], 'alarm_lo': p['alarm_lo'], 'alarm_hi': p['alarm_hi']}
    from ..schema_factory import factory_layout_t as lt
    lay = c.execute(sa.select(lt.c.id, lt.c.name, lt.c.file_name).where(lt.c.active.is_(True)).order_by(lt.c.id.desc()).limit(1)).mappings().first()
    out['layout'] = dict(lay) if lay else None
    return out


def _js(v):
    return db.jsonable(v)


def counts(snap: dict) -> dict:
    pts = snap['points'].values()
    return {'workshops': len(snap['workshops']), 'lines': len(snap['lines']), 'stations': len(snap['stations']), 'devices': len(snap['devices']),
            'datasources': len(snap['datasources']), 'points': len(snap['points']), 'confirmed': sum(1 for p in pts if p['status'] == 'confirmed')}


def diff(a: dict, b: dict) -> dict:
    """两个快照的差异：{分组: {added: [...], removed: [...], changed: [{key, fields: {字段: [旧, 新]}}]}}。"""
    out = {}
    for grp in ('workshops', 'lines', 'stations', 'devices', 'datasources', 'points'):
        A, B = (a or {}).get(grp) or {}, (b or {}).get(grp) or {}
        ch = []
        for k in sorted(set(A) & set(B)):
            f = {x: [A[k].get(x), B[k].get(x)] for x in set(A[k]) | set(B[k]) if _ne(A[k].get(x), B[k].get(x)) and x != 'status'}
            if f:
                ch.append({'key': k, 'fields': f})
        out[grp] = {'added': sorted(set(B) - set(A)), 'removed': sorted(set(A) - set(B)), 'changed': ch}
    return out


def publish(c, note: str, user: str) -> dict:
    from ..schema_factory import factory_version_t as vt
    snap = snapshot(c)
    last = c.execute(sa.select(vt.c.version).order_by(vt.c.version.desc()).limit(1)).scalar() or 0
    v = last + 1
    c.execute(vt.insert().values(version=v, note=(note or '')[:400], snapshot=snap, counts=counts(snap), published_by=user, published_at=clock.now()))
    from .. import audit
    audit.audit(c, 'factory_publish', 'factory_version', v, detail={'note': note, 'counts': counts(snap)}, target=_tr('工厂模型 V{v}', v=v))
    return {'version': v, 'counts': counts(snap)}


def version_snap(c, v) -> dict | None:
    from ..schema_factory import factory_version_t as vt
    if v in (None, '', 'current', 0, '0'):
        return snapshot(c)
    r = c.execute(sa.select(vt.c.snapshot).where(vt.c.version == int(v))).scalar()
    return r


# ───────────────────────── 采集映射（采集程序 / 入库用） ─────────────────────────
class Index:
    """采集点 → (设备, 点位路径, 用途, 语义, 值映射, 缩放…) 的内存索引。采集程序按 数据源 + 地址 找点位；入库（ingest）按时序键找用途。
    模型改了（导入 / 确认 / 模板）标 dirty，下次用时重建（最多每 2 秒一次）。"""

    def __init__(self):
        self.dirty = True
        self.built = 0.0
        self.by_addr: dict[int, dict[str, dict]] = {}      # datasource id → {地址: 点位}
        self.by_code: dict[int, dict[str, dict]] = {}      # datasource id → {点位编码: 点位}
        self.by_key: dict[str, dict] = {}                   # 时序键 → 点位
        self.eq_code: dict[int, str] = {}
        self.eq_tpl: dict[int, str] = {}
        self.eq_by_code: dict[str, int] = {}
        self.names: dict[tuple, dict] = {}                  # (设备 id, 点位路径) → 点位（另一协议上的同一点）
        self.ds: dict[int, dict] = {}
        self._sample: dict[str, bool] = {}
        self._building = False

    def ensure(self, block=True):
        """block=False（事件循环里用）：在后台线程重建，这次先用旧的映射，不让事件循环等数据库。"""
        if self.dirty and time.time() - self.built > 2:
            if block:
                self.build()
            elif not self._building:
                self._building = True

                def go():
                    try:
                        self.build()
                    except Exception:
                        self.dirty = True
                    finally:
                        self._building = False
                import threading
                threading.Thread(target=go, name='factory-index', daemon=True).start()

    def build(self):
        self.dirty = False
        self.built = time.time()
        with db.engine.connect() as c:
            eq = db.rows(c, 'equipment')
            self.eq_code = {e['id']: e['code'] for e in eq}
            self.eq_tpl = {e['id']: e.get('template') or e.get('etype') for e in eq}
            self.eq_by_code = {e['code']: e['id'] for e in eq}
            self.ds = {d['id']: d for d in db.rows(c, 'datasource')}
            by_addr, by_code, by_key, names = {}, {}, {}, {}
            for p in db.rows(c, 'tag_point'):
                if not p['enabled'] or p.get('status') == 'ignored':
                    continue
                ds = self.ds.get(p['datasource'])
                key = p.get('ts_key') or ts_key(ds['code'] if ds else None, self.eq_code.get(p['equipment']), p['name'])
                m = {'id': p['id'], 'eid': p['equipment'], 'name': p['name'], 'key': key, 'role': p['role'], 'sem': p.get('sem'), 'dtype': p['dtype'],
                     'scale': p['scale'], 'offset': p.get('offset'), 'map': p.get('value_map'), 'enc': p.get('encoding'), 'address': p['address'],
                     'code': p.get('code'), 'ds': p['datasource'], 'period_ms': p.get('period_ms')}
                if p['address']:
                    by_addr.setdefault(p['datasource'], {})[p['address']] = m
                if p.get('code'):
                    by_code.setdefault(p['datasource'], {})[p['code']] = m
                by_key[key] = m
                if p['equipment']:
                    names[(p['equipment'], p['name'])] = m
            self.by_addr, self.by_code, self.by_key, self.names = by_addr, by_code, by_key, names
            self._sample = {}

    def role_of(self, key: str, eid: int | None, name: str):
        """(用途, 语义, 模板取出的变量)：先看点表，再按设备模板的点位模式，最后按接入规范的标准点名（Common/State 等）。"""
        self.ensure()
        m = self.by_key.get(key)
        if m and m['role']:
            caps = {}
            if m.get('sem') in ('tool_life', 'tool_limit') or m['role'] == 'count':
                caps = self._caps(eid, name)
            return m['role'], m.get('sem'), caps
        tcode = self.eq_tpl.get(eid) if eid else None
        tp = template(tcode) if tcode else None
        tpp, caps = tpl_mod.match_point((tp or {}).get('points'), name) if tp else (None, {})
        if not tpp:
            tpp, caps = tpl_mod.match_point(tpl_mod.COMMON, name)
        if tpp:
            return tpp.get('role'), tpp.get('sem') or ('sample' if tpp.get('sample') else None), caps
        return None, None, {}

    def _caps(self, eid, name):
        tcode = self.eq_tpl.get(eid) if eid else None
        tp = template(tcode) if tcode else None
        _p, caps = tpl_mod.match_point((tp or {}).get('points'), name) if tp else (None, {})
        return caps

    def sample(self, key: str, eid, name) -> bool:
        hit = self._sample.get(key)
        if hit is not None and not self.dirty:
            return hit
        v = self._sample[key] = self._sample_calc(key, eid, name)
        return v

    def _sample_calc(self, key, eid, name) -> bool:
        role, sem, _ = self.role_of(key, eid, name)
        if sem == 'sample':
            return True
        tcode = self.eq_tpl.get(eid) if eid else None
        tp = template(tcode) if tcode else None
        tpp, _ = tpl_mod.match_point((tp or {}).get('points'), name) if tp else (None, {})
        return bool(tpp and tpp.get('sample'))


INDEX = Index()


def key_points(tcode: str) -> list[str]:
    """模板里标了 key 的点位路径（设备监控 / 首页卡片显示）。"""
    tp = template(tcode) if tcode else None
    return [p['name'] for p in (tp or {}).get('points') or [] if p.get('key') and not any(ch in p['name'] for ch in '{*')]


# ───────────────────────── 树 / 目录 ─────────────────────────
def tree(c, user=None) -> list[dict]:
    from .. import perms
    allowed = perms.allowed_ids(user, 'equipment') if user else None
    tp = TABLES['tag_point']
    cnt = {r[0]: (r[1], r[2]) for r in c.execute(sa.select(tp.c.equipment, sa.func.count(), sa.func.sum(sa.case((tp.c.status == 'confirmed', 1), else_=0)))
                                                 .group_by(tp.c.equipment))}
    eqs = [e for e in db.rows(c, 'equipment', order=TABLES['equipment'].c.code) if allowed is None or e['id'] in allowed]
    lines = db.rows(c, 'line', order=TABLES['line'].c.code)
    wss = db.rows(c, 'workshop', order=TABLES['workshop'].c.code)
    stations = {s['equipment']: s for s in db.rows(c, 'workstation') if s['equipment']}

    def dev(e):
        n, ok = cnt.get(e['id'], (0, 0))
        s = stations.get(e['id'])
        return {'type': 'device', 'id': e['id'], 'code': e['code'], 'name': e['name'], 'etype': e['etype'], 'template': e.get('template'), 'status': e['status'],
                'points': n, 'confirmed': int(ok or 0), 'station': s['code'] if s else None, 'placed': e.get('x') is not None}
    by_line = {}
    for e in eqs:
        by_line.setdefault(e['line'], []).append(dev(e))
    out = []
    for p in db.rows(c, 'plant'):
        pn = {'type': 'plant', 'id': p['id'], 'code': p['code'], 'name': p['name'], 'children': []}
        for w in wss:
            if w['plant'] not in (p['id'], None):
                continue
            wn = {'type': 'workshop', 'id': w['id'], 'code': w['code'], 'name': w['name'], 'children': []}
            for l in lines:
                if l['workshop'] == w['id'] and by_line.get(l['id']):
                    wn['children'].append({'type': 'line', 'id': l['id'], 'code': l['code'], 'name': l['name'], 'children': by_line.get(l['id'], [])})
            if wn['children']:
                pn['children'].append(wn)
        out.append(pn)
    if by_line.get(None):
        out.append({'type': 'group', 'id': 0, 'code': '-', 'name': _tr('未分配产线'), 'children': by_line[None]})
    n_un = c.execute(sa.select(sa.func.count()).select_from(tp).where(tp.c.equipment.is_(None))).scalar()
    if n_un:
        out.append({'type': 'group', 'id': -1, 'code': '?', 'name': _tr('未分配设备的点位 {n_un} 个', n_un=n_un), 'children': []})
    return out


def catalog(c, user=None, equipment=None) -> list[dict]:
    """AI 数据目录：设备 → 点位（含义 / 单位 / 用途 / 时序键 / 正常范围）。M6 的 MCP 读这个。"""
    from .. import perms
    allowed = perms.allowed_ids(user, 'equipment') if user else None
    eqs = {e['id']: e for e in db.rows(c, 'equipment') if allowed is None or e['id'] in allowed}
    lines = {l['id']: l for l in db.rows(c, 'line')}
    out = {}
    for p in db.rows(c, 'tag_point', order=TABLES['tag_point'].c.id):
        e = eqs.get(p['equipment'])
        if not e or (equipment and e['code'] != equipment and e['id'] != _i(equipment)) or p.get('status') == 'ignored':
            continue
        d = out.setdefault(e['id'], {'code': e['code'], 'name': e['name'], 'type': e['etype'], 'template': e.get('template'),
                                     'line': (lines.get(e['line']) or {}).get('name'), 'ideal_cycle_s': e['ideal_cycle_s'], 'points': []})
        d['points'].append({'code': p['code'], 'name': p['name'], 'meaning': p['desc'], 'unit': p['unit'], 'dtype': p['dtype'], 'role': p['role'],
                            'sem': p.get('sem'), 'ts_key': p.get('ts_key'), 'normal_range': p.get('normal_range'), 'alarm_lo': p.get('alarm_lo'),
                            'alarm_hi': p.get('alarm_hi'), 'status': p.get('status')})
    return list(out.values())


# ───────────────────────── 值规范化（入库前） ─────────────────────────
STATE_SYN = {'executing': 'Executing', 'running': 'Executing', 'run': 'Executing', 'auto': 'Executing', 'active': 'Executing', 'producing': 'Executing',
             'working': 'Executing', '运行': 'Executing', '生产': 'Executing', '加工中': 'Executing',
             'idle': 'Idle', 'ready': 'Idle', 'standby': 'Idle', 'waiting': 'Idle', '待机': 'Idle', '空闲': 'Idle',
             'stopped': 'Stopped', 'stop': 'Stopped', 'fault': 'Stopped', 'alarm': 'Stopped', 'error': 'Stopped', 'down': 'Stopped', 'emergency': 'Stopped',
             '停机': 'Stopped', '故障': 'Stopped', '报警': 'Stopped', 'setup': 'Setup', 'changeover': 'Setup', 'setting': 'Setup', '换型': 'Setup', '调机': 'Setup',
             'planned': 'Planned', 'off': 'Planned', 'offline': 'Planned', 'poweroff': 'Planned', '计划停机': 'Planned', '关机': 'Planned',
             'starved': 'Starved', '缺料': 'Starved', 'blocked': 'Blocked', '堵料': 'Blocked'}


def canon_state(v):
    """运行状态统一成 MES 的状态词（Executing / Idle / Setup / Stopped / Planned / Starved / Blocked）：开关量 1 = 运行；
    整数按接入规范的状态码（值映射在采集时已换过的不会再到这里成数字）；常见的英文 / 中文写法都认。认不出的原样保留。"""
    if isinstance(v, bool):
        return 'Executing' if v else 'Idle'
    if isinstance(v, (int, float)):
        return tpl_mod.STATE_CODES.get(str(int(v)), str(v))
    s = str(v).strip()
    return STATE_SYN.get(s.lower(), STATE_SYN.get(s, s))


def canon_alarm(v) -> str:
    """报警：文字原样；开关量 1 = "报警"；数字非 0 = "报警代码 N"；0 / 空 = 无报警。"""
    if v is None:
        return ''
    if isinstance(v, bool):
        return _tr('报警') if v else ''
    if isinstance(v, (int, float)):
        return '' if v == 0 else _tr('报警代码 {v}', v=int(v) if float(v).is_integer() else v)
    s = str(v).strip()
    return '' if s.lower() in ('', '0', 'false', 'none', 'ok', 'normal', '正常', '无') else s
