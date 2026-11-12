"""计量单位与换算。

- 物料的基本单位 = material.unit（库存、BOM 用量、报工都按它记）。
- 同类别单位（重量、长度、面积、体积、时间）按 uom.ratio 换算：kg=1、t=1000、g=0.001 → 2.5 t = 2500 kg。计数类（件、箱、托…）之间没有通用比例。
- 物料自己的换算（uom_conv）：1 箱 = 24 件、1 件 = 15.2 kg；物料为空的是通用换算。正反方向都能用，可以串起来（箱 → 件 → kg）。

    from honeymom.impl import uom
    uom.convert(conn, material_id, 3, '箱')               # → 72.0（换到基本单位 件）
    uom.convert(conn, material_id, 1.2, 't', 'kg')        # → 1200.0
    uom.factor(conn, material_id, '箱', '件')              # → 24.0；换不了返回 None
"""
from ..i18n import _tr
from collections import deque

import sqlalchemy as sa

from ..schema import TABLES


def _units(conn) -> dict:
    t = TABLES['uom']
    out = {}
    for r in conn.execute(sa.select(t.c.code, t.c.name, t.c.category, t.c.ratio)).mappings():
        for k in {r['code'], r['name']} - {None, ''}:
            out[str(k).strip().lower()] = dict(r)
    return out


def norm(u) -> str:
    return str(u or '').strip().lower()


def base_unit(conn, material_id) -> str | None:
    t = TABLES['material']
    return conn.execute(sa.select(t.c.unit).where(t.c.id == material_id)).scalar() if material_id else None


def _edges(conn, material_id):
    t = TABLES['uom_conv']
    q = sa.select(t.c.from_unit, t.c.to_unit, t.c.factor).where(sa.or_(t.c.material.is_(None), t.c.material == material_id) if material_id else t.c.material.is_(None))
    g: dict = {}
    for a, b, f in conn.execute(q):
        if not a or not b or not f:
            continue
        a, b = norm(a), norm(b)
        g.setdefault(a, []).append((b, float(f)))
        g.setdefault(b, []).append((a, 1.0 / float(f)))
    return g


def factor(conn, material_id, from_unit, to_unit) -> float | None:
    """1 from_unit = ? to_unit（这个物料）。找不到换算路径返回 None。"""
    a, b = norm(from_unit), norm(to_unit)
    if not a or not b or a == b:
        return 1.0
    units = _units(conn)
    g = _edges(conn, material_id)
    # 同类别（非计数）按 ratio 连边
    for u, d in units.items():
        if d['category'] in (None, 'count') or not d['ratio']:
            continue
        for v, e in units.items():
            if v != u and e['category'] == d['category'] and e['ratio']:
                g.setdefault(u, []).append((v, float(d['ratio']) / float(e['ratio'])))
    seen = {a: 1.0}
    q = deque([a])
    while q:
        u = q.popleft()
        if u == b:
            return seen[u]
        for v, f in g.get(u, []):
            if v not in seen:
                seen[v] = seen[u] * f
                q.append(v)
    return None


def convert(conn, material_id, qty, from_unit, to_unit=None) -> float:
    """数量换到 to_unit（缺省 = 物料基本单位）。换不了抛 ValueError。"""
    to = to_unit or base_unit(conn, material_id)
    if qty is None or not from_unit or not to:
        return qty
    f = factor(conn, material_id, from_unit, to)
    if f is None:
        raise ValueError(_tr('没有 {from_unit} → {to} 的换算（在「单位换算」里加一条，如 1 {from_unit} = ? {to}）', from_unit=from_unit, to=to))
    return round(float(qty) * f, 6)


def known(conn, unit) -> bool:
    return norm(unit) in _units(conn)
