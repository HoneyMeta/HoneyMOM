"""工作台驾驶舱与车间大屏的数据接口。

GET  /api/home/cockpit     KPI（今日值、昨日同时段、近 12 小时趋势、计划达成）、24 小时产出、工单与工序计划（迷你甘特）、缺陷、OEE、安灯
GET  /api/home/live        设备实时状态（地图 / 状态网格用，轻量，每 3 秒取）：状态、报警、功率、在制数、缓冲区库存、关键点位
GET  /api/home/layout      工厂平面布局：5.0（M2）起从工厂模型取（设备布局 + 平面图，factory/layout.py home_layout），按数据范围过滤；
                           模型里还没有布局时用 4.7 留下的缓存副本（home_layout_cache.json），都没有返回 {ok: false}，前端退回状态网格
GET  /api/home/prefs       当前用户的工作台布局（组件顺序、隐藏）   PUT 保存   DELETE 恢复默认
GET  /api/home/screen      车间大屏补充数据：点检保养到期、刀具寿命、设备状态表
信息流（提醒 + AI 值守）在 home_feed.py。
"""
from __future__ import annotations

from ..i18n import _tr
import json
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends

from .. import db
from ..auth import current_user
from ..crud import attach_labels, out
from ..db import clock
from ..schema import TABLES

router = APIRouter()
NOT_DEVICE = ('crew', 'agv')
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def _cached(key, ttl, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    v = fn()
    with _lock:
        _cache[key] = (time.time(), v)
    return v


def _hour_floor(t: datetime):
    return t.replace(minute=0, second=0, microsecond=0)


# ────────────────────────────── 驾驶舱 ──────────────────────────────
def _event_counts(c, s, e):
    pe = TABLES['part_event']
    return dict(c.execute(sa.select(pe.c.event, sa.func.count()).where((pe.c.t >= s) & (pe.c.t < e)).group_by(pe.c.event)).all())


def _kwh(c, s, e):
    et = TABLES['energy_hour']
    return c.execute(sa.select(sa.func.sum(et.c.kwh)).where((et.c.hour >= s) & (et.c.hour < e))).scalar() or 0


ATTAIN_CAP = 1.5          # 显示上限：超过 150% 显示"＞150%"
ATTAIN_VALID_MAX = 3.0    # 实际超过计划 3 倍：说明排产计划没覆盖今天在产的工单，达成率没有参考意义


def attainment(actual, due_now, due_day) -> dict:
    """计划达成率（诚实版）：计划为 0 / 计划远小于实际时不给百分比，而是说明原因；正常时最多显示到 150%。"""
    raw = round(actual / due_now, 3) if due_now else None
    if not due_now:
        state, note = 'none', _tr('今天到现在没有应完成的计划（工单没有排产计划，或计划时间不在今天）')
    elif raw > ATTAIN_VALID_MAX:
        state, note = 'tiny', _tr('计划量远小于实际：到现在应完成 {due_now} 件、实际入库 {actual} 件，达成率没有参考意义（检查排产计划是否覆盖今天在产的工单）', due_now=due_now, actual=actual)
    else:
        state, note = 'ok', ''
    return {'attain': min(raw, ATTAIN_CAP) if state == 'ok' else None, 'attain_raw': raw, 'attain_capped': bool(state == 'ok' and raw > ATTAIN_CAP),
            'plan_state': state, 'plan_note': note, 'plan_now': due_now, 'plan_day': due_day, 'actual': actual}


def _plan_due(c, s, now):
    """计划达成：每个在制工单最后一道工序按计划时间线性折算，到现在"应该完成"的件数（今日 / 全天）。"""
    opt, wot = TABLES['wo_op'], TABLES['work_order']
    last = sa.select(opt.c.work_order, sa.func.max(opt.c.seq).label('seq')).group_by(opt.c.work_order).subquery()
    rows = c.execute(sa.select(opt.c.plan_start, opt.c.plan_end, opt.c.qty_plan).join(last, (last.c.work_order == opt.c.work_order) & (last.c.seq == opt.c.seq))
                     .join(wot, wot.c.id == opt.c.work_order).where(wot.c.status.in_(('released', 'in_progress', 'completed')))).all()
    e = s + timedelta(days=1)
    now_due = day_due = 0.0
    for ps, pe_, q in rows:
        if not ps or not pe_ or not q or pe_ <= ps:
            continue
        span = (pe_ - ps).total_seconds()
        rate = q / span

        def part(a, b):
            a, b = max(a, ps), min(b, pe_)
            return max(0.0, (b - a).total_seconds()) * rate
        now_due += part(s, now)
        day_due += part(s, e)
    return round(now_due), round(day_due)


def _oee_hours(c, hours=8):
    """近 N 小时每小时的平均 OEE（缓存 2 分钟，算起来慢）。"""
    from .equipment import COUNT_EVENTS, oee_of
    end = _hour_floor(clock.now()) + timedelta(hours=1)
    eqs = db.rows(c, 'equipment', TABLES['equipment'].c.etype.in_(('cnc', 'molding', 'inspection', 'deburr', 'shotblast')))
    pts = []
    for h in range(hours, 0, -1):
        s = end - timedelta(hours=h)
        vals = [o['OEE'] for eq in eqs if eq['etype'] in COUNT_EVENTS for o in [oee_of(c, eq, s, s + timedelta(hours=1))] if o and o['OEE'] is not None]
        pts.append(round(sum(vals) / len(vals), 3) if vals else None)
    return pts


def cockpit_data():
    from .ai import day_kpi
    now = clock.now()
    k = day_kpi()
    s, _e = db.day_range(None)
    ys = s - timedelta(days=1)
    pe = TABLES['part_event']
    h0 = _hour_floor(now) - timedelta(hours=23)
    with db.engine.connect() as c:
        yday = _event_counts(c, ys, now - timedelta(days=1))            # 昨日同时段
        y_kwh = _kwh(c, ys, now - timedelta(days=1))
        hourly = defaultdict(lambda: defaultdict(int))
        for t, ev in c.execute(sa.select(pe.c.t, pe.c.event).where((pe.c.t >= h0) & pe.c.event.in_(('poured', 'received', 'machined', 'stored', 'scrap', 'shipped', 'inspected')))).all():
            hourly[t.strftime('%m-%d %H:00')][ev] += 1
        et = TABLES['energy_hour']
        kwh_h = defaultdict(float)
        for hr, kw in c.execute(sa.select(et.c.hour, et.c.kwh).where(et.c.hour >= h0)).all():
            kwh_h[hr.strftime('%m-%d %H:00')] += kw or 0
        due_now, due_day = _plan_due(c, s, now)
        oee_sp = _cached('oee_hours', 120, lambda: _oee_hours(c))
        y_oee = _cached('oee_yday:' + ys.date().isoformat(), 900, lambda: _avg_oee(day_kpi(ys.date().isoformat())))
        # 工单 + 工序计划（迷你甘特）
        wot, opt = TABLES['work_order'], TABLES['wo_op']
        wos = db.rows(c, 'work_order', wot.c.status.in_(('released', 'in_progress')), order=(wot.c.priority, wot.c.due), limit=10)
        attach_labels(c, 'work_order', wos)
        ops = defaultdict(list)
        if wos:
            o_rows = db.rows(c, 'wo_op', opt.c.work_order.in_([w['id'] for w in wos]), order=opt.c.seq)
            attach_labels(c, 'wo_op', o_rows)
            for o in o_rows:
                ops[o['work_order']].append({'seq': o['seq'], 'process': o.get('process__label') or '', 'status': o['status'], 'plan_start': db.jsonable(o['plan_start']),
                                             'plan_end': db.jsonable(o['plan_end']), 'act_start': db.jsonable(o['act_start']), 'act_end': db.jsonable(o['act_end']),
                                             'qty_plan': o['qty_plan'], 'qty_good': o['qty_good'], 'qty_ng': o['qty_ng']})
        orders = []
        for w in wos:
            ol = ops.get(w['id'], [])
            eta = max((o['plan_end'] for o in ol if o['plan_end']), default=None)
            orders.append({'id': w['id'], 'code': w['code'], 'product': w.get('product__label') or '', 'customer': w['customer'], 'qty': w['qty'], 'qty_done': w['qty_done'] or 0,
                           'qty_scrap': w['qty_scrap'] or 0, 'due': db.jsonable(w['due']), 'status': w['status'], 'priority': w['priority'], 'eta': eta,
                           'late': bool(w['due'] and w['due'] < now and (w['qty_done'] or 0) < w['qty']),
                           'risk': bool(eta and w['due'] and eta > db.jsonable(w['due'])), 'ops': ol})
        eq = TABLES['equipment']
        status = {k or 'offline': n for k, n in c.execute(sa.select(eq.c.status, sa.func.count()).where(eq.c.etype.notin_(NOT_DEVICE)).group_by(eq.c.status)).all()}
        an = TABLES['andon']
        andon_open = db.rows(c, 'andon', an.c.status != 'closed', order=an.c.id.desc(), limit=20)
        attach_labels(c, 'andon', andon_open)
        andon_closed = c.execute(sa.select(sa.func.count()).select_from(an).where((an.c.status == 'closed') & (an.c.closed_at >= s))).scalar() or 0
        company = db.setting(c, 'company', '')
    hours = [(h0 + timedelta(hours=i)).strftime('%m-%d %H:00') for i in range(24)]
    p, q, en = k['production'], k['quality'], k['energy']
    last12 = hours[-12:]
    cast_h = [hourly[h].get('poured', 0) + hourly[h].get('received', 0) for h in last12]
    scrap_h = [round(hourly[h].get('scrap', 0) / c_, 4) if c_ else 0 for h, c_ in zip(last12, cast_h)]
    y_cast = yday.get('poured', 0) + yday.get('received', 0)
    y_scrap_rate = round(yday.get('scrap', 0) / y_cast, 4) if y_cast else None
    kpi = {
        'castings': {'value': p['castings'], 'yday': y_cast, 'spark': cast_h},
        'machined': {'value': p['machined'], 'yday': yday.get('machined', 0), 'spark': [hourly[h].get('machined', 0) for h in last12]},
        'stored': {'value': p['stored'], 'yday': yday.get('stored', 0), 'spark': [hourly[h].get('stored', 0) for h in last12], 'shipped': p['shipped'],
                   **attainment(p['stored'], due_now, due_day)},
        'scrap_rate': {'value': q['scrap_rate'], 'yday': y_scrap_rate, 'spark': scrap_h, 'scrap': q['scrap'], 'rework': q['rework']},
        'oee': {'value': _avg_oee(k), 'yday': y_oee, 'spark': oee_sp, 'alarms': k['equipment']['alarms'],
                'worst': sorted([x for x in k['equipment']['oee'] if x['OEE'] is not None], key=lambda x: x['OEE'])[:1]},
        'kwh': {'value': en['kwh'], 'yday': round(y_kwh, 1), 'spark': [round(kwh_h.get(h, 0), 1) for h in last12], 'per_t': en['kwh_per_t'], 'heats': en['heats']},
        'wo': {'open': p['wo_open'], 'late': p['wo_late']},
        'inspected': p['inspected'],
    }
    return {'now': db.jsonable(now), 'day': k['day'], 'company': company, 'kpi': kpi, 'hours': hours,
            'hourly': {h: dict(hourly.get(h, {})) for h in hours}, 'status': status, 'orders': orders,
            'pareto': q['pareto'][:8], 'ncr_open': q['ncr_open'], 'oee': k['equipment']['oee'], 'top_alarms': k['equipment']['top_alarms'],
            'andon': {'open': out(andon_open), 'closed_today': andon_closed}}


def _avg_oee(k):
    o = [x['OEE'] for x in (k.get('equipment') or {}).get('oee', []) if x['OEE'] is not None]
    return round(sum(o) / len(o), 3) if o else None


@router.get('/api/home/cockpit')
def cockpit(user=Depends(current_user)):
    from ..factory import scope                             # 5.0 M2：数据范围（工厂 / 车间 / 产线）
    return scope.cockpit(user, _cached('cockpit', 4, cockpit_data))


# ────────────────────────────── 设备实时状态 ──────────────────────────────
LIVE_TAGS = ('Common/PowerKW', 'Queue/Count', 'Queue/Capacity', 'Queue/Utilization', 'Production/CurrentPart', 'Stock/Count', 'Stock/Utilization',
             'Bath/Temperature', 'Heat/Phase', 'Monitoring/Spindle/Load', 'Tools/ActiveTool', 'Production/WaitingMolds', 'Line/Molds', 'Cell/WIP',
             'Production/InProcess', 'Result/Last', 'Crew/OnDuty', 'Crew/Busy', 'Fleet/Queue')
STATE_CN = {'running': '运行', 'idle': '待机', 'setup': '换型', 'down': '停机', 'offline': '离线'}


def live_data():
    tl = db.metadata.tables['tag_latest']
    now = clock.now()
    with db.engine.connect() as c:
        eqs = db.rows(c, 'equipment', order=TABLES['equipment'].c.code)
        attach_labels(c, 'equipment', eqs)
        tags = defaultdict(dict)
        for eid, name, val, num, ts in c.execute(sa.select(tl.c.equipment, tl.c.name, tl.c.value, tl.c.num, tl.c.ts).where(tl.c.name.in_(LIVE_TAGS))).all():
            tags[eid][name] = num if num is not None else val
        seen = {r[0] for r in c.execute(sa.select(tl.c.equipment).distinct()).all()}
        al = TABLES['alarm']
        alarms = defaultdict(list)
        for eid, msg, st in c.execute(sa.select(al.c.equipment, al.c.msg, al.c.start).where(al.c.end.is_(None)).order_by(al.c.start)).all():
            alarms[eid].append({'msg': msg, 'start': db.jsonable(st)})
        es = TABLES['equip_state']
        since = {eid: st for eid, st in c.execute(sa.select(es.c.equipment, sa.func.max(es.c.start)).where(es.c.end.is_(None)).group_by(es.c.equipment)).all()}
        pt = TABLES['part']
        wip = dict(c.execute(sa.select(pt.c.station, sa.func.count()).where(pt.c.status.in_(('wip', 'rework', 'hold'))).group_by(pt.c.station)).all())
        an = TABLES['andon']
        andon = defaultdict(int)
        for eid, in c.execute(sa.select(an.c.equipment).where((an.c.status != 'closed') & an.c.equipment.isnot(None))).all():
            andon[eid] += 1
    items = []
    for e in eqs:
        t = tags.get(e['id'], {})
        st = e['status'] or 'offline'
        if e['id'] not in seen:
            st = 'offline'
        q = t.get('Queue/Count')
        items.append({'id': e['id'], 'code': e['code'], 'name': e['name'], 'etype': e['etype'], 'line': e.get('line__label') or '', 'location': e['location'] or '',
                      'status': st, 'status_text': STATE_CN.get(st, st), 'since': db.jsonable(since.get(e['id'])),
                      'alarms': alarms.get(e['id'], []), 'andon': andon.get(e['id'], 0),
                      'power': t.get('Common/PowerKW'), 'wip': wip.get(e['code'], 0),
                      'queue': q, 'capacity': t.get('Queue/Capacity'), 'util': t.get('Queue/Utilization') if q is not None else t.get('Stock/Utilization'),
                      'stock': t.get('Stock/Count'), 'tags': {k: v for k, v in t.items() if k not in ('Common/PowerKW',)}})
    return {'now': db.jsonable(now), 'items': items}


@router.get('/api/home/live')
def live(user=Depends(current_user)):
    from .. import perms
    d = _cached('live', 1.5, live_data)
    ids = perms.allowed_ids(user, 'equipment')               # 数据范围（缓存是全量的，按人过滤）
    return d if ids is None else {**d, 'items': [x for x in d['items'] if x['id'] in ids]}


# ────────────────────────────── 平面布局 ──────────────────────────────
SKIP_KINDS = ('agv', 'crew', 'switch', 'gateway', 'mes')
_LAYOUT_FILE = lambda: os.path.join(db.DATA, 'home_layout_cache.json')     # noqa: E731


def layout_data():
    try:                                                      # 5.0 M2：工厂模型（交付包 / DXF 平面图导入的设备布局），不再取虚拟工厂的私有接口
        from ..factory import layout as fl
        with db.engine.connect() as c:
            lay = fl.home_layout(c)
        if not (lay.get('ok') and lay.get('stations')):
            raise RuntimeError(lay.get('reason') or _tr('工厂模型里还没有设备布局：在"接入中心"导入交付包或 DXF 平面图'))
        try:
            with open(_LAYOUT_FILE(), 'w', encoding='utf-8') as f:
                json.dump(lay, f, ensure_ascii=False)
        except OSError:
            pass
        return lay
    except Exception as ex:                                   # 模型里还没有布局（老库升级、还没导入交付包）：用上次保存的布局
        try:
            with open(_LAYOUT_FILE(), encoding='utf-8') as f:
                lay = json.load(f)
            lay['source'] = 'cache'
            lay['warning'] = _tr('工厂模型里还没有设备布局，显示 {get} 保存的布局（在"接入中心"导入交付包或 DXF 平面图）', get=lay.get("fetched_at", ""))
            return lay
        except (OSError, ValueError):
            return {'ok': False, 'reason': str(ex)[:200]}


@router.get('/api/home/layout')
def layout(refresh: int = 0, user=Depends(current_user)):
    if refresh:
        with _lock:
            _cache.pop('layout', None)
    lay = _cached('layout', 60, layout_data)
    if not lay.get('ok'):
        with _lock:                                           # 失败只缓存 15 秒
            _cache['layout'] = (time.time() - 45, lay)
    from .. import perms
    ids = perms.allowed_ids(user, 'equipment')                # 数据范围：只画自己范围内的设备
    if ids is not None and lay.get('ok'):
        lay = {**lay, 'stations': [s for s in lay.get('stations') or [] if s.get('eq_id') in ids]}
    return lay


# ────────────────────────────── 用户布局偏好 ──────────────────────────────
_PREFS = lambda: os.path.join(db.DATA, 'home_prefs.json')     # noqa: E731


def _read_prefs():
    try:
        with open(_PREFS(), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_prefs(d):
    tmp = _PREFS() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, _PREFS())


@router.get('/api/home/prefs')
def prefs_get(user=Depends(current_user)):
    return _read_prefs().get(user['username']) or {}


@router.put('/api/home/prefs')
def prefs_put(body: dict = Body(...), user=Depends(current_user)):
    clean = {'order': [str(x)[:40] for x in (body.get('order') or [])][:40], 'hidden': [str(x)[:40] for x in (body.get('hidden') or [])][:40],
             'screen': body.get('screen') if isinstance(body.get('screen'), dict) else None}
    with _lock:
        d = _read_prefs()
        d[user['username']] = {k: v for k, v in clean.items() if v is not None}
        _write_prefs(d)
    return d[user['username']]


@router.delete('/api/home/prefs')
def prefs_reset(user=Depends(current_user)):
    with _lock:
        d = _read_prefs()
        d.pop(user['username'], None)
        _write_prefs(d)
    return {}


# ────────────────────────────── 车间大屏补充数据 ──────────────────────────────
def screen_data():
    from .equipment import oee_of
    now = clock.now()
    with db.engine.connect() as c:
        mt = TABLES['maint_plan']
        due = db.rows(c, 'maint_plan', mt.c.next_due <= now.date() + timedelta(days=3), order=mt.c.next_due, limit=40)
        attach_labels(c, 'maint_plan', due)
        for m in due:
            m['overdue_days'] = (now.date() - m['next_due']).days if m['next_due'] else None
        tl = TABLES['tool']
        tools = db.rows(c, 'tool', order=tl.c.equipment, limit=80)
        attach_labels(c, 'tool', tools)
        for t in tools:
            t['life_pct'] = round((t['used'] or 0) / t['life_limit'], 3) if t['life_limit'] else None
        tools.sort(key=lambda t: -(t['life_pct'] or 0))
        eqs = db.rows(c, 'equipment', TABLES['equipment'].c.etype.notin_(NOT_DEVICE + ('other',)), order=TABLES['equipment'].c.code)
        s = now - timedelta(hours=8)
        rows = []
        for e in eqs:
            o = oee_of(c, e, s, now)
            rows.append({'id': e['id'], 'code': e['code'], 'name': e['name'], 'etype': e['etype'], 'status': e['status'] or 'offline',
                         'oee8h': o['OEE'] if o else None, 'A': o['A'] if o else None, 'good': o['good'] if o else None})
        nt = TABLES['ncr']
        from .quality import ncr_pending      # "待处理"唯一口径
        ncr = db.rows(c, 'ncr', ncr_pending(nt), order=nt.c.found_at.desc(), limit=20)
        attach_labels(c, 'ncr', ncr)
    return {'now': db.jsonable(now), 'maint': out(due), 'tools': out(tools[:30]), 'equipment': rows, 'ncr': out(ncr)}


@router.get('/api/home/screen')
def screen(user=Depends(current_user)):
    from ..factory import scope
    return scope.screen(user, _cached('screen', 10, screen_data))
