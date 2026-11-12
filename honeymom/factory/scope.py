"""数据范围（工厂 / 车间 / 产线，M1 perms.py）接到驾驶舱、车间大屏、OEE、能耗上。

这些接口的全量结果是缓存的（多人共用）；范围受限的用户在全量结果上按工厂模型重算 / 过滤：
设备 → 产线（equipment.line）；零件事件按工位编码 = 设备编码归属；工单按 work_order.line（由工艺路线推出）；安灯 / 报警按设备。
范围不限（None）原样返回。
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import timedelta

import sqlalchemy as sa

from .. import db, perms
from ..db import clock
from ..schema import TABLES

_cache: dict = {}


def _ids(user):
    return perms.allowed_ids(user, 'equipment') if user else None


def _eqs(c, ids):
    return [e for e in db.rows(c, 'equipment') if e['id'] in ids]


def cockpit(user, d: dict) -> dict:
    ids = _ids(user)
    if ids is None:
        return d
    key = ('cockpit', frozenset(ids), tuple(sorted(str(x) for x in (perms.user_scope(user) or {}).items())))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 4:
        return hit[1]
    out = _cockpit(user, d, ids)
    _cache[key] = (time.time(), out)
    return out


def _cockpit(user, d, ids):
    d = {**d, 'kpi': {k: dict(v) if isinstance(v, dict) else v for k, v in d['kpi'].items()}, 'scoped': True}
    now = clock.now()
    s, _e = db.day_range(None)
    ys = s - timedelta(days=1)
    pe, et, eq, an = TABLES['part_event'], TABLES['energy_hour'], TABLES['equipment'], TABLES['andon']
    with db.engine.connect() as c:
        eqs = _eqs(c, ids)
        codes = [e['code'] for e in eqs] or ['\x00']
        names = {e['name'] for e in eqs}

        def ev(a, b):
            return dict(c.execute(sa.select(pe.c.event, sa.func.count()).where((pe.c.t >= a) & (pe.c.t < b) & pe.c.station.in_(codes)).group_by(pe.c.event)).all())
        today, yday = ev(s, now + timedelta(seconds=1)), ev(ys, now - timedelta(days=1))
        h0 = d['hours'][0] if d.get('hours') else None
        hourly = defaultdict(lambda: defaultdict(int))
        start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
        for t, e_ in c.execute(sa.select(pe.c.t, pe.c.event).where((pe.c.t >= start) & pe.c.station.in_(codes))).all():
            hourly[t.strftime('%m-%d %H:00')][e_] += 1
        kwh = c.execute(sa.select(sa.func.sum(et.c.kwh)).where((et.c.hour >= s) & et.c.equipment.in_(list(ids) or [-1]))).scalar() or 0
        kwh_h = defaultdict(float)
        for hr, kw in c.execute(sa.select(et.c.hour, et.c.kwh).where((et.c.hour >= start) & et.c.equipment.in_(list(ids) or [-1]))).all():
            kwh_h[hr.strftime('%m-%d %H:00')] += kw or 0
        status = {k or 'offline': n for k, n in c.execute(sa.select(eq.c.status, sa.func.count()).where(eq.c.id.in_(list(ids) or [-1]) & eq.c.etype.notin_(('crew', 'agv')))
                                                          .group_by(eq.c.status)).all()}
        wo_ids = {o['id'] for o in d.get('orders') or []}
        wo_ok = set()
        if wo_ids:
            for w in db.rows(c, 'work_order', TABLES['work_order'].c.id.in_(wo_ids)):
                if perms.in_scope(user, 'work_order', w):
                    wo_ok.add(w['id'])
    hours = d.get('hours') or []
    last12 = hours[-12:]
    k = d['kpi']
    cast = today.get('poured', 0) + today.get('received', 0)
    ycast = yday.get('poured', 0) + yday.get('received', 0)
    cast_h = [hourly[h].get('poured', 0) + hourly[h].get('received', 0) for h in last12]
    k['castings'] = {**k['castings'], 'value': cast, 'yday': ycast, 'spark': cast_h}
    k['machined'] = {**k['machined'], 'value': today.get('machined', 0), 'yday': yday.get('machined', 0), 'spark': [hourly[h].get('machined', 0) for h in last12]}
    k['stored'] = {**k['stored'], 'value': today.get('stored', 0), 'yday': yday.get('stored', 0), 'spark': [hourly[h].get('stored', 0) for h in last12],
                   'shipped': today.get('shipped', 0)}
    scrap = today.get('scrap', 0)
    k['scrap_rate'] = {**k['scrap_rate'], 'value': round(scrap / cast, 4) if cast else 0, 'scrap': scrap, 'rework': today.get('rework', 0),
                       'spark': [round(hourly[h].get('scrap', 0) / x, 4) if x else 0 for h, x in zip(last12, cast_h)]}
    oee = [x for x in d.get('oee') or [] if x.get('code') in codes]
    vals = [x['OEE'] for x in oee if x.get('OEE') is not None]
    k['oee'] = {**k['oee'], 'value': round(sum(vals) / len(vals), 3) if vals else None, 'worst': sorted([x for x in oee if x['OEE'] is not None], key=lambda x: x['OEE'])[:1]}
    k['kwh'] = {**k['kwh'], 'value': round(kwh, 1), 'spark': [round(kwh_h.get(h, 0), 1) for h in last12]}
    k['inspected'] = today.get('inspected', 0)
    d['hourly'] = {h: dict(hourly.get(h, {})) for h in hours}
    d['status'] = status
    d['oee'] = oee
    d['top_alarms'] = [a for a in d.get('top_alarms') or [] if a.get('equipment') in names]
    d['orders'] = [o for o in d.get('orders') or [] if o['id'] in wo_ok]
    k['wo'] = {**(k.get('wo') or {}), 'open': len(d['orders'])}
    an_open = [a for a in (d.get('andon') or {}).get('open') or [] if not a.get('equipment') or a['equipment'] in ids]
    d['andon'] = {**(d.get('andon') or {}), 'open': an_open}
    _ = h0
    return d


def screen(user, d: dict) -> dict:
    ids = _ids(user)
    if ids is None:
        return d
    return {**d, 'equipment': [x for x in d.get('equipment') or [] if x['id'] in ids],
            'tools': [t for t in d.get('tools') or [] if not t.get('equipment') or t['equipment'] in ids],
            'maint': [m for m in d.get('maint') or [] if not m.get('equipment') or m['equipment'] in ids], 'scoped': True}


def rows_by_eq(user, rows: list, key='id') -> list:
    ids = _ids(user)
    return rows if ids is None else [r for r in rows if r.get(key) in ids]


def energy(user, d: dict, days: int) -> dict:
    ids = _ids(user)
    if ids is None:
        return d
    et, eq = TABLES['energy_hour'], TABLES['equipment']
    end = clock.now()
    since = (end - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    lst = list(ids) or [-1]
    with db.engine.connect() as c:
        hourly = defaultdict(lambda: defaultdict(float))
        for r in c.execute(sa.select(et.c.hour, eq.c.etype, et.c.kwh).join(eq, eq.c.id == et.c.equipment)
                           .where((et.c.hour >= end - timedelta(hours=48)) & et.c.equipment.in_(lst))).all():
            hourly[db.jsonable(r[0])][r[1]] += r[2] or 0
        daily = defaultdict(float)
        for r in c.execute(sa.select(et.c.hour, et.c.kwh).where((et.c.hour >= since) & et.c.equipment.in_(lst))).all():
            daily[r[0].date().isoformat()] += r[1] or 0
    by = [x for x in d.get('by_equipment') or [] if x.get('id') in ids]
    return {**d, 'by_equipment': by, 'hourly': {k: dict(v) for k, v in sorted(hourly.items())}, 'daily': dict(sorted(daily.items())),
            'total_kwh': round(sum(x.get('kwh') or 0 for x in by), 1), 'scoped': True}
