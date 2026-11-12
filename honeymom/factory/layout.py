"""车间平面图（DXF）与 2D / 3D 孪生数据。

导入（import_dxf）：用 services/drawings.py 的 DXF 小解析器（H3）读图元；
  - 设备块：INSERT 后面跟着 ATTRIB，属性名是 CODE / 设备编码 / DEVICE / EQUIP / TAG 之一 → {code, name, type, x, y, rot, w, d}（尺寸取块图形外框）；
  - 区域：图层名含 AREA / ZONE / 区域 的闭合多段线（里面的文字是区域名）；
  - 其余图元（墙、通道、AGV 路径、文字…）作为背景，按图层存成简化图元。
  单位：$INSUNITS（4 毫米 / 5 厘米 / 6 米）换算成米。中文的 \\U+XXXX 转回汉字。
落位（autobind）：设备块的 CODE 和设备编码一致 → 设备的 x / y / 朝向 / 宽 / 深 取图上的；对不上的列出来，界面上拖拽落位（save_positions）。
孪生数据（twin_layout）：工厂坐标（米，Y 向上）的设备 + 背景 + 区域；home_layout() 换成首页平面图用的格式（Y 向下，和 4.7 /api/home/layout 一样）。
"""
from __future__ import annotations

from ..i18n import _tr
import math
import os
import re

import sqlalchemy as sa

from .. import db
from ..db import clock

CODE_TAGS = ('CODE', '设备编码', 'DEVICE', 'EQUIP', 'EQUIPMENT', 'TAG', 'DEVCODE', 'ASSET')
UNIT_M = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 14: 0.1}
MAX_PRIMS = 60000


def _u(s: str) -> str:
    return re.sub(r'\\U\+([0-9A-Fa-f]{4})', lambda m: chr(int(m.group(1), 16)), s or '')


def _insunits(text: str) -> int | None:
    m = re.search(r'\$INSUNITS\s*\r?\n\s*70\s*\r?\n\s*(\d+)', text)
    return int(m.group(1)) if m else None


def parse(text: str) -> dict:
    """DXF 文本 → {unit, scale, prims, blocks, areas, bounds, floor, layers}（坐标已换算成米）。"""
    from ..services.drawings import parse_dxf, to_prims
    ents, blocks = parse_dxf(text)
    units = _insunits(text)
    k = UNIT_M.get(units or 6, 1.0)
    if units is None:                                           # 没写单位：按尺寸猜（厂房几十到几百米；毫米图会到几万）
        xs = [e.get(10) for e in ents if isinstance(e.get(10), float)]
        if xs and max(abs(x) for x in xs) > 5000:
            k = 0.001
    devs, bg, i = [], [], 0
    while i < len(ents):
        e = ents[i]
        if e['type'] == 'INSERT':
            attrs = {}
            j = i + 1
            while j < len(ents) and ents[j]['type'] == 'ATTRIB':
                a = ents[j]
                attrs[str(a.get(2, '')).strip().upper()] = _u(str(a.get(1, '')).strip())
                j += 1
            code = next((attrs[t] for t in CODE_TAGS if attrs.get(t)), None)
            if code:
                b = blocks.get(e.get(2, ''))
                w = d = None
                if b:
                    prims = [p for p in to_prims(b['ents'], blocks) if p[0] == 'poly']
                    pts = [q for p in prims for q in p[1]]
                    if pts:
                        bx, by = b['base']
                        sx, sy = abs(e.get(41, 1) or 1), abs(e.get(42, 1) or 1)
                        w = round((max(p[0] for p in pts) - min(p[0] for p in pts)) * sx * k, 3)
                        d = round((max(p[1] for p in pts) - min(p[1] for p in pts)) * sy * k, 3)
                devs.append({'code': code, 'name': attrs.get('NAME') or attrs.get('名称'), 'type': attrs.get('TYPE') or attrs.get('类型'),
                             'x': round(e.get(10, 0) * k, 3), 'y': round(e.get(20, 0) * k, 3), 'rot': round(e.get(50, 0) % 360, 2), 'w': w, 'd': d,
                             'layer': e.get('layer'), 'block': e.get(2)})
                i = j
                continue
            bg.append(e)
            i = j
            continue
        if e['type'] not in ('ATTRIB', 'ATTDEF'):
            bg.append(e)
        i += 1
    out, areas = [], []
    layers = {}
    for p, e in _zip_layers(bg, blocks):
        lay = (e or {}).get('layer') or '0'
        layers[lay] = layers.get(lay, 0) + 1
        if p[0] == 'poly':
            pts = [[round(x * k, 3), round(y * k, 3)] for x, y in p[1]]
            out.append({'t': 'p', 'l': lay, 'pts': pts, 'z': 1 if p[2] else 0})
            if p[2] and re.search(r'AREA|ZONE|区域', lay, re.I) and len(pts) >= 3:
                areas.append({'pts': pts, 'name': '', 'layer': lay})
        elif p[0] == 'circle':
            out.append({'t': 'c', 'l': lay, 'x': round(p[1] * k, 3), 'y': round(p[2] * k, 3), 'r': round(p[3] * k, 3)})
        elif p[0] == 'text':
            s = _u(p[5])
            out.append({'t': 't', 'l': lay, 'x': round(p[1] * k, 3), 'y': round(p[2] * k, 3), 'h': round(p[3] * k, 3), 'r': p[4], 's': s})
        if len(out) >= MAX_PRIMS:
            break
    # 区域名：区域多段线里面的文字（同一图层优先）
    for a in areas:
        xs, ys = [q[0] for q in a['pts']], [q[1] for q in a['pts']]
        for t in out:
            if t['t'] == 't' and min(xs) <= t['x'] <= max(xs) and min(ys) <= t['y'] <= max(ys) and (t['l'] == a['layer'] or not a['name']):
                a['name'] = t['s']
                if t['l'] == a['layer']:
                    break
        a['bbox'] = [min(xs), min(ys), max(xs), max(ys)]
    allx = [q[0] for p in out if p['t'] == 'p' for q in p['pts']] + [d['x'] for d in devs]
    ally = [q[1] for p in out if p['t'] == 'p' for q in p['pts']] + [d['y'] for d in devs]
    bounds = [min(allx), min(ally), max(allx), max(ally)] if allx else [0, 0, 100, 50]
    walls = [q for p in out if p['t'] == 'p' and re.search(r'WALL|墙', p['l'], re.I) for q in p['pts']]
    floor = [max(q[0] for q in walls), max(q[1] for q in walls)] if walls else [bounds[2], bounds[3]]
    return {'unit': units, 'scale': k, 'prims': out, 'blocks': devs, 'areas': areas, 'bounds': bounds, 'floor': floor, 'layers': layers}


def _zip_layers(ents, blocks):
    """to_prims 不带图层：按实体顺序重新走一遍，给每个图元配上它来自的实体（拿图层）。"""
    from ..services.drawings import to_prims
    for e in ents:
        for p in to_prims([e], blocks):
            yield p, e


def import_dxf(c, text: str, name: str, plant_id=None, user='') -> dict:
    from ..schema_factory import factory_layout_t as lt
    lay = parse(text)
    c.execute(lt.update().values(active=False))
    rid = c.execute(lt.insert().values(plant=plant_id, name=name, file_name=name, unit=str(lay['unit'] or ''), bounds=lay['bounds'], floor=lay['floor'],
                                       prims=lay['prims'], areas=lay['areas'], blocks=lay['blocks'], active=True, created_by=user,
                                       created_at=clock.now())).inserted_primary_key[0]
    d = os.path.join(db.DATA, 'factory', 'layout')
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f'{rid}.dxf'), 'w', encoding='utf-8', newline='') as f:
        f.write(text)
    from .. import audit
    audit.audit(c, 'layout_import', 'factory_layout', rid, detail={'name': name, 'blocks': len(lay['blocks']), 'prims': len(lay['prims'])}, target=_tr('平面图 {name}', name=name))
    return {'id': rid, 'blocks': len(lay['blocks']), 'prims': len(lay['prims']), 'areas': len(lay['areas']), 'floor': lay['floor']}


def active(c) -> dict | None:
    from ..schema_factory import factory_layout_t as lt
    r = c.execute(sa.select(lt).where(lt.c.active.is_(True)).order_by(lt.c.id.desc()).limit(1)).mappings().first()
    return dict(r) if r else None


def autobind(c, only_unplaced=False) -> dict:
    """按 DXF 块属性 CODE 自动落位。返回 {matched, moved, unmatched_blocks, unplaced_devices}。"""
    lay = active(c)
    if not lay:
        return {'matched': 0, 'moved': 0, 'unmatched_blocks': [], 'unplaced_devices': [], 'reason': _tr('还没有导入平面图')}
    eqs = {e['code']: e for e in db.rows(c, 'equipment')}
    eqs_ci = {k.upper(): v for k, v in eqs.items()}
    matched = moved = 0
    unmatched = []
    seen = set()
    for b in lay['blocks'] or []:
        e = eqs.get(b['code']) or eqs_ci.get(str(b['code']).upper())
        if not e:
            unmatched.append(b['code'])
            continue
        seen.add(e['id'])
        matched += 1
        if only_unplaced and e.get('x') is not None:
            continue
        upd = {k: b[k] for k in ('x', 'y', 'rot') if b.get(k) is not None}
        for k in ('w', 'd'):
            if b.get(k):
                upd[k] = b[k]
        if any(e.get(k) is None or abs(float(e.get(k) or 0) - float(v)) > 1e-3 for k, v in upd.items()):
            db.update(c, 'equipment', e['id'], upd, raw=True)
            moved += 1
    unplaced = [e['code'] for e in eqs.values() if e['id'] not in seen and e.get('x') is None]
    return {'matched': matched, 'moved': moved, 'unmatched_blocks': unmatched, 'unplaced_devices': unplaced}


def save_positions(c, items: dict) -> int:
    """拖拽落位：{设备编码: {x, y, rot?, w?, d?}}（工厂坐标，米）。"""
    n = 0
    for code, v in (items or {}).items():
        e = db.get(c, 'equipment', code=code)
        if not e:
            continue
        upd = {k: float(v[k]) for k in ('x', 'y', 'rot', 'w', 'd') if v.get(k) is not None}
        if upd:
            db.update(c, 'equipment', e['id'], upd, raw=True)
            n += 1
    return n


# ───────────────────────── 孪生数据 ─────────────────────────
HIDE_KINDS = ('crew',)


def twin_layout(c, user=None, with_prims=True) -> dict:
    """2D / 3D 孪生：{ok, floor, bounds, prims, areas, devices: [{code, name, etype, template, x, y, w, d, rot, model3d, line, eq_id}]}，工厂坐标（Y 向上）。"""
    from .. import perms
    from . import model
    allowed = perms.allowed_ids(user, 'equipment') if user else None
    lay = active(c)
    lines = {l['id']: l['name'] for l in db.rows(c, 'line')}
    devs = []
    for e in db.rows(c, 'equipment'):
        if e.get('x') is None or e['etype'] in HIDE_KINDS or (allowed is not None and e['id'] not in allowed):
            continue
        tp = model.template(e.get('template') or e['etype']) or {}
        devs.append({'code': e['code'], 'name': e['name'], 'etype': e['etype'], 'template': e.get('template'), 'x': e['x'], 'y': e['y'],
                     'w': e.get('w') or tp.get('w') or 2, 'd': e.get('d') or tp.get('d') or 2, 'h': tp.get('h') or 1.5, 'rot': e.get('rot') or 0,
                     'model3d': e.get('model3d'), 'kind3d': tp.get('model3d') or 'box', 'line': lines.get(e['line']), 'eq_id': e['id'], 'status': e['status']})
    if not devs and not lay:
        return {'ok': False, 'reason': _tr('工厂模型里还没有设备布局：在"接入中心 → 平面图与孪生"导入交付包或 DXF 平面图')}
    if lay:
        floor, bounds = lay['floor'] or [100, 50], lay['bounds'] or [0, 0, 100, 50]
    else:
        xs = [d['x'] for d in devs]
        ys = [d['y'] for d in devs]
        floor = [max(xs) + 10, max(ys) + 10]
        bounds = [0, 0, floor[0], floor[1]]
    return {'ok': True, 'source': 'model', 'floor': floor, 'bounds': bounds, 'prims': (lay['prims'] if lay and with_prims else []),
            'areas': [{'name': a.get('name'), 'pts': a['pts'], 'bbox': a.get('bbox')} for a in ((lay or {}).get('areas') or [])],
            'layout': {'id': lay['id'], 'name': lay['name']} if lay else None, 'devices': devs}


def _aabb(d):
    """设备外框（考虑朝向）：宽 / 深。"""
    a = math.radians(float(d.get('rot') or 0))
    w, h = float(d['w']), float(d['d'])
    return abs(w * math.cos(a)) + abs(h * math.sin(a)), abs(w * math.sin(a)) + abs(h * math.cos(a))


def home_layout(c, user=None) -> dict:
    """首页平面图 / 车间大屏（components/home/FactoryMap.vue）用的格式：Y 向下，设备外框左上角 + 宽高，区域是矩形。"""
    tw = twin_layout(c, user, with_prims=False)
    if not tw.get('ok'):
        return tw
    fw, fh = float(tw['floor'][0]), float(tw['floor'][1])
    stations = []
    for d in tw['devices']:
        if d['etype'] in ('agv', 'switch', 'gateway', 'mes'):
            continue
        w, h = _aabb(d)
        stations.append({'code': d['code'], 'name': d['name'], 'kind': d['etype'], 'area': d['line'] or '', 'group': None,
                         'x': round(d['x'] - w / 2, 3), 'y': round(fh - d['y'] - h / 2, 3), 'w': round(w, 3), 'h': round(h, 3),
                         'eq_id': d['eq_id'], 'etype': d['etype'], 'capacity': None})
    areas = []
    for a in tw['areas']:
        b = a.get('bbox') or [0, 0, 0, 0]
        areas.append({'name': a.get('name') or '', 'x': b[0], 'y': round(fh - b[3], 3), 'w': round(b[2] - b[0], 3), 'h': round(b[3] - b[1], 3), 'color': ''})
    if not areas:                                                   # 没有区域图层：按产线把设备框起来
        by = {}
        for s in stations:
            by.setdefault(s['area'], []).append(s)
        for name, ss in by.items():
            x0, y0 = min(s['x'] for s in ss) - 1, min(s['y'] for s in ss) - 1
            x1, y1 = max(s['x'] + s['w'] for s in ss) + 1, max(s['y'] + s['h'] for s in ss) + 1
            areas.append({'name': name, 'x': x0, 'y': y0, 'w': x1 - x0, 'h': y1 - y0, 'color': ''})
    xs = [0, fw] + [s['x'] for s in stations] + [s['x'] + s['w'] for s in stations]
    ys = [0] + [s['y'] for s in stations] + [s['y'] + s['h'] + 4 for s in stations]
    if ys and max(ys) > fh * 0.85:
        ys.append(fh)
    if stations:
        ys[0] = max(0, min(s['y'] for s in stations) - 4)
    with_groups = {w['equipment']: w['group'] for w in db.rows(c, 'workstation') if w['equipment']}
    for s in stations:
        s['group'] = with_groups.get(s['eq_id'])
    pl = db.rows(c, 'plant', limit=1)
    return {'ok': True, 'source': 'model', 'plant': pl[0]['name'] if pl else '', 'product': '', 'floor': [fw, fh], 'bounds': [min(xs), min(ys), max(xs), max(ys)],
            'areas': areas, 'stations': stations, 'route': flow_route(c, stations), 'fetched_at': db.jsonable(clock.now())}


def flow_route(c, stations) -> list:
    """物流路线：生效的工艺路线按工序顺序 → 每道工序的工位组 / 工位 → 设备中心（首页平面图上的流向线）。"""
    from ..schema import TABLES
    rt = TABLES['route']
    r = c.execute(sa.select(rt.c.id).where(rt.c.status == 'active').order_by(rt.c.id).limit(1)).scalar()
    if not r:
        return []
    by_code = {s['code']: s for s in stations}
    by_group = {}
    for s in stations:
        if s.get('group'):
            by_group.setdefault(s['group'], []).append(s)
    pts = []
    for st in db.rows(c, 'route_step', route=r, order=TABLES['route_step'].c.seq):
        g = st.get('station_group')
        ss = by_group.get(g) or ([by_code[g]] if g in by_code else [])
        if not ss:
            continue
        cx = sum(s['x'] + s['w'] / 2 for s in ss) / len(ss)
        cy = sum(s['y'] + s['h'] / 2 for s in ss) / len(ss)
        pts.append({'name': g, 'x': round(cx, 2), 'y': round(cy, 2)})
    return pts
