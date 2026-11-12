"""设备：实时监控、OEE（可用率×性能率×质量率）与损失分析、点检保养、维修工单、报警确认、能耗。"""
from ..i18n import _tr
from collections import defaultdict
from datetime import date, datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..auth import current_user
from ..crud import action, attach_labels, out
from ..db import clock
from ..schema import TABLES

router = APIRouter()
COUNT_EVENTS = {'cnc': ('machined',), 'inspection': ('inspected',), 'deburr': ('deburred',), 'molding': ('poured',), 'shotblast': ('cleaned',),
                'cooling': ('shakeout',), 'receiving': ('received',), 'warehouse': ('stored',)}
KEY_TAGS = {'furnace': ['Bath/Temperature', 'Electrical/Power', 'Heat/Phase', 'Heat/Number', 'Bath/Weight'], 'cnc': ['Monitoring/Spindle/Load', 'Production/PartsProduced', 'Tools/ActiveTool', 'Quality/BoreDiameter'],
            'molding': ['Production/MoldCount', 'Pouring/LastTemp', 'Production/WaitingMolds'], 'sand': ['Sand/Moisture', 'Sand/Compactability'],
            'inspection': ['Result/Last', 'Result/OK', 'Result/NG'], 'deburr': ['Brush/Wear', 'Production/PartsDone'], 'shotblast': ['Turbine/Current', 'Batch/Count'],
            'treatment': ['Treatment/Count', 'Ladle/Temperature', 'Ladle/ResidualMg'], 'cooling': ['Line/Molds', 'Line/ShakeoutTemp'], 'receiving': ['Received/Count'], 'crew': ['Crew/OnDuty', 'Crew/Busy'], 'warehouse': ['Stock/Count', 'Stock/Utilization'], 'agv': []}


def state_durations(c, eid, s, e):
    t = TABLES['equip_state']
    now = clock.now()
    dur = defaultdict(float)
    for r in c.execute(sa.select(t).where((t.c.equipment == eid) & (t.c.start < e) & sa.or_(t.c.end.is_(None), t.c.end > s))).mappings():
        a, b = max(r['start'], s), min(r['end'] or now, e)
        if b > a:
            dur[r['state']] += (b - a).total_seconds()
    return dur


def counts(c, eq, s, e):
    """设备在区间内的产出：合格、不良（按零件事件统计）。"""
    evs = COUNT_EVENTS.get(eq['etype'])
    if not evs:                                   # 5.0 M2：没有零件事件的设备，产出用计数点（用途 count → equip_output 小时产量）
        from ..schema_factory import equip_output_t as ot
        q = c.execute(sa.select(sa.func.sum(ot.c.qty)).where((ot.c.equipment == eq['id']) & (ot.c.hour >= s.replace(minute=0, second=0, microsecond=0))
                                                           & (ot.c.hour < e))).scalar()
        return int(q or 0), 0
    pe = TABLES['part_event']
    code = eq['code']
    good = c.execute(sa.select(sa.func.count()).select_from(pe).where((pe.c.station == code) & pe.c.event.in_(evs) & (pe.c.t >= s) & (pe.c.t < e))).scalar()
    bad = c.execute(sa.select(sa.func.count()).select_from(pe).where((pe.c.station == code) & (pe.c.event == 'scrap') & (pe.c.t >= s) & (pe.c.t < e))).scalar()
    if eq['etype'] == 'inspection':
        ng = c.execute(sa.select(sa.func.count()).select_from(TABLES['insp_record']).where(
            (TABLES['insp_record'].c.station == code) & (TABLES['insp_record'].c.result == 'NG') & (TABLES['insp_record'].c.time >= s)
            & (TABLES['insp_record'].c.time < e) & TABLES['insp_record'].c.sn.isnot(None))).scalar()
        return good - ng, ng
    return good, bad


def oee_of(c, eq, s, e):
    now = clock.now()
    e2 = min(e, now)
    if e2 <= s:
        return None
    dur = state_durations(c, eq['id'], s, e2)
    span = sum(dur.values()) - dur.get('Planned', 0)        # 计划停机/非生产时间不计入
    if span <= 0:
        return None
    down, setup = dur.get('Stopped', 0), dur.get('Setup', 0)
    run = dur.get('Executing', 0)
    oper = max(1.0, span - down - setup)
    good, bad = counts(c, eq, s, e2)
    total = good + bad
    A = oper / span
    ideal = eq['ideal_cycle_s']
    P = min(1.0, ideal * total / oper) if ideal and total else (run / oper if oper else 0)
    Q = good / total if total else 1.0
    return {'A': round(A, 3), 'P': round(P, 3), 'Q': round(Q, 3), 'OEE': round(A * P * Q, 3), 'good': good, 'bad': bad, 'span_h': round(span / 3600, 2),
            'loss': {'停机': round(down / 3600, 2), '换型换刀': round(setup / 3600, 2), '待机/等待': round(dur.get('Idle', 0) / 3600, 2), '运行': round(run / 3600, 2),
                         'down': round(down / 3600, 2), 'setup': round(setup / 3600, 2), 'idle': round(dur.get('Idle', 0) / 3600, 2), 'run': round(run / 3600, 2)}}


@router.get('/api/equipment/oee')
def oee(day: str = '', hours: int = 24, user=Depends(current_user)):
    if day:
        s, e = db.day_range(day)
    else:
        e = clock.now()
        s = e - timedelta(hours=hours)
    with db.engine.connect() as c:
        from ..schema_factory import equip_output_t as ot
        with_out = {r[0] for r in c.execute(sa.select(ot.c.equipment).where(ot.c.hour >= s).distinct())}
        from .. import perms
        sc = perms.scope_cond(user, 'equipment')             # 5.0 M2：数据范围
        eqs = db.rows(c, 'equipment', sa.or_(TABLES['equipment'].c.etype.in_(list(COUNT_EVENTS)), TABLES['equipment'].c.id.in_(sorted(with_out) or [-1])),
                      *([sc] if sc is not None else []), order=TABLES['equipment'].c.code)
        res = []
        for eq in eqs:
            o = oee_of(c, eq, s, e)
            if o:
                res.append({'id': eq['id'], 'code': eq['code'], 'name': eq['name'], 'etype': eq['etype'], 'ideal_cycle_s': eq['ideal_cycle_s'], **o})
    return {'from': db.jsonable(s), 'to': db.jsonable(min(e, clock.now())), 'rows': res}


@router.get('/api/equipment/oee_trend/{eid}')
def oee_trend(eid: int, hours: int = 24, user=Depends(current_user)):
    end = clock.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    with db.engine.connect() as c:
        eq = db.get(c, 'equipment', eid)
        pts = []
        for h in range(hours, 0, -1):
            s = end - timedelta(hours=h)
            o = oee_of(c, eq, s, s + timedelta(hours=1))
            pts.append({'t': db.jsonable(s), **(o or {'OEE': None})})
        st = TABLES['equip_state']
        tl = [dict(r) for r in c.execute(sa.select(st).where((st.c.equipment == eid) & sa.or_(st.c.end.is_(None), st.c.end > end - timedelta(hours=hours)))
                                        .order_by(st.c.start)).mappings()]
    return {'points': pts, 'timeline': out(tl), 'now': db.jsonable(clock.now())}


@router.get('/api/equipment/monitor')
def monitor(user=Depends(current_user)):
    tl = db.metadata.tables['tag_latest']
    with db.engine.connect() as c:
        from .. import perms
        sc = perms.scope_cond(user, 'equipment')             # 数据范围：只看自己工厂 / 车间 / 产线的设备
        eqs = db.rows(c, 'equipment', *([sc] if sc is not None else []), order=TABLES['equipment'].c.code)
        attach_labels(c, 'equipment', eqs)
        latest = defaultdict(dict)
        for r in c.execute(sa.select(tl.c.equipment, tl.c.name, tl.c.value, tl.c.num, tl.c.ts)).all():
            latest[r[0]][r[1]] = r[3] if r[3] is not None else r[2]
        al = TABLES['alarm']
        open_al = {r[0]: r[1] for r in c.execute(sa.select(al.c.equipment, al.c.msg).where(al.c.end.is_(None))).all()}
        s = clock.now() - timedelta(hours=8)
        for eq in eqs:
            lt = latest.get(eq['id'], {})
            eq['power'] = lt.get('Common/PowerKW')
            eq['alarm'] = open_al.get(eq['id'])
            from ..factory.model import key_points           # 5.0 M2：关键点位来自设备模板（模板没有时用老的对照表）
            eq['tags'] = {k: lt[k] for k in (key_points(eq.get('template') or eq['etype']) or KEY_TAGS.get(eq['etype'], [])) if k in lt}
            eq['online'] = bool(lt)
            o = oee_of(c, eq, s, clock.now()) if eq['etype'] in COUNT_EVENTS else None
            eq['oee8h'] = o['OEE'] if o else None
    return {'now': db.jsonable(clock.now()), 'items': out(eqs)}


@router.get('/api/equipment/tags/{eid}')
def tags(eid: int, user=Depends(current_user)):
    tl = db.metadata.tables['tag_latest']
    with db.engine.connect() as c:
        rows = [{'name': r[0], 'value': r[1], 'ts': db.jsonable(r[2])} for r in c.execute(sa.select(tl.c.name, tl.c.value, tl.c.ts).where(tl.c.equipment == eid).order_by(tl.c.name))]
    return rows


@router.get('/api/equipment/samples/{eid}')
def samples(eid: int, name: str, hours: float = 8, user=Depends(current_user)):
    ts_ = db.metadata.tables['tag_sample']
    since = clock.now() - timedelta(hours=hours)
    with db.engine.connect() as c:
        rows = c.execute(sa.select(ts_.c.ts, ts_.c.num, ts_.c.name).where((ts_.c.equipment == eid) & ts_.c.name.like(name + '%') & (ts_.c.ts >= since))
                         .order_by(ts_.c.ts)).all()
    return [{'t': db.jsonable(r[0]), 'v': r[1], 'name': r[2]} for r in rows]


# ---------------- 点检保养 ----------------
@action('maint_plan', 'execute')
def maint_execute(c, p, body, user):
    today = clock.now().date()
    db.insert(c, 'maint_record', {'plan': p['id'], 'equipment': p['equipment'], 'executor': user.get('employee'), 'time': clock.now(),
                                  'result': body.get('result') or 'ok', 'notes': body.get('notes') or ''}, raw=True)
    db.update(c, 'maint_plan', p['id'], {'last_done': today, 'next_due': today + timedelta(days=p['cycle_days'] or 1)}, raw=True)
    if body.get('result') == 'abnormal':
        db.insert(c, 'repair_order', {'equipment': p['equipment'], 'fault': _tr('点检异常：{v}', v=body.get('notes') or p['name']), 'reported_by': user['name'] or user['username'],
                                      'reported_at': clock.now(), 'level': 'normal', 'status': 'reported'}, raw=True)
        return {'msg': _tr('已记录，并自动生成维修工单')}


@router.get('/api/equipment/maint_due')
def maint_due(user=Depends(current_user)):
    t = TABLES['maint_plan']
    today = clock.now().date()
    with db.engine.connect() as c:
        rows = db.rows(c, 'maint_plan', sa.or_(t.c.next_due.is_(None), t.c.next_due <= today), order=t.c.next_due)
        attach_labels(c, 'maint_plan', rows)
    for r in rows:
        r['overdue_days'] = (today - r['next_due']).days if r['next_due'] else None
    return out(rows)


# ---------------- 维修 ----------------
@action('repair_order', 'assign')
def rep_assign(c, r, body, user):
    if not body.get('assignee'):
        raise HTTPException(400, _tr('请选择维修人'))
    db.update(c, 'repair_order', r['id'], {'assignee': int(body['assignee']), 'status': 'assigned'}, raw=True)


@action('repair_order', 'start')
def rep_start(c, r, body, user):
    db.update(c, 'repair_order', r['id'], {'status': 'repairing', 'start': clock.now()}, raw=True)


@action('repair_order', 'finish')
def rep_finish(c, r, body, user):
    end = clock.now()
    st = r['start'] or r['reported_at'] or end
    db.update(c, 'repair_order', r['id'], {'status': 'done', 'end': end, 'downtime_min': round((end - st).total_seconds() / 60, 1),
                                           'cause': body.get('cause') or r['cause'], 'solution': body.get('solution') or r['solution']}, raw=True)


@action('repair_order', 'verify')
def rep_verify(c, r, body, user):
    if r['status'] != 'done':
        raise HTTPException(400, _tr('修复完成后才能验收'))
    db.update(c, 'repair_order', r['id'], {'status': 'verified'}, raw=True)


@action('alarm', 'ack')
def alarm_ack(c, r, body, user):
    db.update(c, 'alarm', r['id'], {'ack_by': user['name'] or user['username'], 'ack_at': clock.now(), 'note': body.get('note') or r['note']}, raw=True)


@router.get('/api/equipment/alarm_stats')
def alarm_stats(days: int = 7, user=Depends(current_user)):
    al = TABLES['alarm']
    since = clock.now() - timedelta(days=days)
    with db.engine.connect() as c:
        rows = [dict(r) for r in c.execute(sa.select(al.c.equipment, al.c.msg, sa.func.count().label('n'), sa.func.sum(al.c.dur_s).label('dur'))
                                           .where(al.c.start >= since).group_by(al.c.equipment, al.c.msg).order_by(sa.func.count().desc()).limit(30)).mappings()]
        attach_labels(c, 'alarm', rows)
    return out(rows)


# ---------------- 能耗 ----------------
@router.get('/api/energy/summary')
def energy(days: int = 7, user=Depends(current_user)):
    et, eq = TABLES['energy_hour'], TABLES['equipment']
    end = clock.now()
    since = (end - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    with db.engine.connect() as c:
        by_eq = [dict(r) for r in c.execute(sa.select(eq.c.id, eq.c.code, eq.c.name, eq.c.etype, sa.func.sum(et.c.kwh).label('kwh'))
                                            .join(eq, eq.c.id == et.c.equipment).where(et.c.hour >= since).group_by(eq.c.id, eq.c.code, eq.c.name, eq.c.etype)
                                            .order_by(sa.func.sum(et.c.kwh).desc())).mappings()]
        hourly = defaultdict(lambda: defaultdict(float))
        for r in c.execute(sa.select(et.c.hour, eq.c.etype, et.c.kwh).join(eq, eq.c.id == et.c.equipment).where(et.c.hour >= end - timedelta(hours=48))).all():
            hourly[db.jsonable(r[0])][r[1]] += r[2] or 0
        daily = defaultdict(float)
        for r in c.execute(sa.select(et.c.hour, et.c.kwh).where(et.c.hour >= since)).all():
            daily[r[0].date().isoformat()] += r[1] or 0
        ht = TABLES['heat']
        melt = c.execute(sa.select(sa.func.sum(ht.c.kg), sa.func.sum(ht.c.kwh)).where((ht.c.end >= since) & (ht.c.status == 'done'))).first()
        pe = TABLES['part_event']
        good = c.execute(sa.select(sa.func.count()).select_from(pe).where((pe.c.event == 'stored') & (pe.c.t >= since))).scalar()
    total = sum(r['kwh'] or 0 for r in by_eq)
    from ..factory import scope                             # 5.0 M2：数据范围
    return scope.energy(user, {'by_equipment': out(by_eq), 'hourly': {k: dict(v) for k, v in sorted(hourly.items())}, 'daily': dict(sorted(daily.items())), 'total_kwh': round(total, 1),
            'melt_t': round((melt[0] or 0) / 1000, 2), 'kwh_per_t': round((melt[1] or 0) / ((melt[0] or 0) / 1000), 1) if melt[0] else None,
            'good': good, 'kwh_per_piece': round(total / good, 2) if good else None,
            'peak_note': _tr('电炉占比最高，适合把熔化安排在谷电时段（各地峰谷时段不同，请在系统参数里配置）')}, days)
