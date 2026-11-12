"""车间现场：报工记录（分组视图、详情）、工位终端、上下工、工人日报与工时、手机端查询、模具模次。

报工记录说明：设备自动报工（来源 auto）是采集到的零件事件按固定间隔汇总的，一条 = 一个工单的一道工序在一台设备上 N 分钟内的产出，
所以同一工单同一工序会有很多条——这是按时段汇总，不是重复提交。界面默认按 工单 → 工序 → 设备 分组看合计。
"""
from ..i18n import _tr
from collections import defaultdict
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db
from ..auth import current_user, require_write
from ..crud import action, attach_labels, out
from ..db import clock, parse_dt
from ..schema import TABLES

router = APIRouter()
SRC_CN = {'auto': '设备自动', 'manual': '人工', 'mobile': '手机扫码', 'scan': '扫码枪', 'terminal': '工位终端'}
# 工序类型 → 零件事件（看这一段里经过了哪些零件）
PROC_EVENT = {'molding': 'poured', 'receiving': 'received', 'cooling': 'shakeout', 'cleaning': 'cleaned', 'machining': 'machined', 'deburr': 'deburred',
              'inspection': 'inspected', 'storage': 'stored', 'melting': 'poured', 'treatment': 'poured'}
CASTING_PROC = {'melting', 'treatment', 'molding', 'cooling', 'cleaning'}


def source_text(r):
    if r.get('source') == 'auto':
        return _tr('设备自动 · 每 {v} 分钟汇总', v=r.get('interval_min') or 60)
    return SRC_CN.get(r.get('source'), r.get('source') or _tr('人工'))


def _range(day=None, frm=None, to=None, hours=None):
    if frm or to:
        a = parse_dt(frm) or clock.now() - timedelta(days=1)
        b = parse_dt(to) or clock.now() + timedelta(minutes=1)
        if to and len(str(to)) <= 10:
            b += timedelta(days=1)
        return a, b
    if hours:
        return clock.now() - timedelta(hours=float(hours)), clock.now() + timedelta(minutes=1)
    return db.day_range(day)


# ---------------- 报工记录 ----------------
@router.get('/api/reports/grouped')
def reports_grouped(day: str = '', frm: str = '', to: str = '', hours: float = 0, work_order: int = 0, source: str = '', q: str = '',
                    user=Depends(current_user)):
    """按 工单 → 工序 → 设备 分组的报工合计。默认看数据时钟的"今天"。"""
    a, b = _range(day or None, frm, to, hours or None)
    rt = TABLES['report']
    with db.engine.connect() as c:
        cond = [rt.c.start >= a, rt.c.start < b]
        if work_order:
            cond.append(rt.c.work_order == work_order)
        if source:
            cond.append(rt.c.source.in_(source.split(',')))
        rows = db.rows(c, 'report', *cond, order=rt.c.start)
        attach_labels(c, 'report', rows)
        ops = {o['id']: o for o in db.rows(c, 'wo_op', TABLES['wo_op'].c.id.in_({r['wo_op'] for r in rows if r['wo_op']}))} if rows else {}
        wos = {w['id']: w for w in db.rows(c, 'work_order', TABLES['work_order'].c.id.in_({r['work_order'] for r in rows if r['work_order']}))} if rows else {}
        attach_labels(c, 'work_order', list(wos.values()))
        eqs = {e['id']: e for e in db.rows(c, 'equipment')}
    kw = q.strip().lower()
    tree = {}
    for r in rows:
        if kw and kw not in ' '.join(str(r.get(k) or '') for k in ('work_order__label', 'process__label', 'equipment__label', 'employee__label', 'code')).lower():
            continue
        w = wos.get(r['work_order']) or {}
        node = tree.setdefault(r['work_order'], {'work_order': r['work_order'], 'code': w.get('code') or _tr('（无工单）'), 'product': w.get('product__label'),
                                                 'qty': w.get('qty'), 'status': w.get('status'), 'good': 0, 'ng': 0, 'records': 0, 'ops': {}})
        op = ops.get(r['wo_op']) or {}
        on = node['ops'].setdefault(r['wo_op'] or f"p{r['process']}", {'wo_op': r['wo_op'], 'seq': op.get('seq'), 'process': (r.get('process__label') or '').split(' ', 1)[-1],
                                                                       'qty_plan': op.get('qty_plan'), 'op_good': op.get('qty_good'), 'op_status': op.get('status'),
                                                                       'good': 0, 'ng': 0, 'records': 0, 'devices': {}})
        e = eqs.get(r['equipment']) or {}
        dk = r['equipment'] or f"src:{r['source']}"
        dn = on['devices'].setdefault(dk, {'equipment': r['equipment'], 'code': e.get('code') or ('—' if r['source'] == 'auto' else _tr('人工')),
                                           'name': e.get('name') or (_tr('无单台设备（按浇注件计）') if r['source'] == 'auto' else SRC_CN.get(r['source'], '')),
                                           'good': 0, 'ng': 0, 'records': 0, 'first': r['start'], 'last': r['end'] or r['start'], 'sources': defaultdict(int),
                                           'interval_min': r.get('interval_min') or (60 if r['source'] == 'auto' else None)})
        for n_ in (node, on, dn):
            n_['good'] += r['qty_good'] or 0
            n_['ng'] += r['qty_ng'] or 0
            n_['records'] += 1
        dn['sources'][r['source']] += 1
        dn['last'] = max(dn['last'], r['end'] or r['start'])
    res = []
    for node in tree.values():
        ops_ = []
        for on in sorted(node['ops'].values(), key=lambda x: x['seq'] or 0):
            devs = []
            for d in on['devices'].values():
                d['sources'] = dict(d['sources'])
                d['source_text'] = _tr('、').join((_tr('设备自动 · 每 {v} 分钟汇总 {n} 条', v=d['interval_min'] or 60, n=n) if s == 'auto' else _tr('{get} {n} 条', get=SRC_CN.get(s, s), n=n)) for s, n in d['sources'].items())
                d['first'], d['last'] = db.jsonable(d['first']), db.jsonable(d['last'])
                devs.append(d)
            on['devices'] = sorted(devs, key=lambda d: d['code'])
            ops_.append(on)
        node['ops'] = ops_
        res.append(node)
    res.sort(key=lambda n: n['code'])
    return {'from': db.jsonable(a), 'to': db.jsonable(b), 'groups': res, 'total': {'good': sum(n['good'] for n in res), 'ng': sum(n['ng'] for n in res),
                                                                                  'records': sum(n['records'] for n in res)}}


@router.get('/api/reports/list')
def reports_list(day: str = '', frm: str = '', to: str = '', hours: float = 0, work_order: int = 0, wo_op: int = 0, equipment: int = 0, source: str = '',
                 page: int = 1, size: int = 50, user=Depends(current_user)):
    a, b = _range(day or None, frm, to, hours or None)
    rt = TABLES['report']
    cond = [rt.c.start >= a, rt.c.start < b]
    for col, v in (('work_order', work_order), ('wo_op', wo_op), ('equipment', equipment)):
        if v:
            cond.append(rt.c[col] == v)
    if source:
        cond.append(rt.c.source.in_(source.split(',')))
    with db.engine.connect() as c:
        total = c.execute(sa.select(sa.func.count()).select_from(rt).where(*cond)).scalar()
        rows = db.rows(c, 'report', *cond, order=(rt.c.start.desc(), rt.c.id.desc()), limit=min(500, size))
        if page > 1:
            rows = [dict(r) for r in c.execute(sa.select(rt).where(*cond).order_by(rt.c.start.desc(), rt.c.id.desc()).limit(size).offset((page - 1) * size)).mappings()]
        attach_labels(c, 'report', rows)
    for r in rows:
        r['source_text'] = source_text(r)
    return {'total': total, 'items': out(rows)}


def instruction_for(c, product, process, station_group=None, route=None, seq=None):
    """作业指示：工艺步骤的作业要点和参数 + 作业指导书 + 加工图纸（按产品、工序关联）。"""
    step = None
    if route and seq is not None:
        step = db.get(c, 'route_step', route=route, seq=seq)
    if not step and product and process:
        r = db.get(c, 'route', product=product, status='active')
        step = db.get(c, 'route_step', route=r['id'], process=process) if r else None
    kt = TABLES['kb_doc']
    docs = db.rows(c, 'kb_doc', sa.or_(kt.c.process == process, kt.c.process.is_(None)) if process else sa.true(),
                   sa.or_(kt.c.product == product, kt.c.product.is_(None)) if product else sa.true(),
                   sa.or_(kt.c.process.isnot(None), kt.c.product.isnot(None)),
                   sa.or_(kt.c.ctrl_status.is_(None), kt.c.ctrl_status == 'effective'), order=kt.c.id)     # 受控文件只显示生效版本（exec_doc）
    attach_labels(c, 'kb_doc', docs)
    p = db.get(c, 'process', process) if process else None
    sops = [d for d in docs if (d['kind'] or 'sop') != 'drawing']
    drawings = [d for d in docs if d['kind'] == 'drawing']
    for d in sops:
        d['content'] = (d['content'] or '')[:1500]
    return {'process': p['name'] if p else None, 'process_code': p['code'] if p else None, 'std_cycle_s': (step or {}).get('std_cycle_s') or (p or {}).get('std_cycle_s'),
            'instruction': (step or {}).get('instruction'), 'params': (step or {}).get('params') or {}, 'fai': bool((step or {}).get('fai')),
            'report_mode': (step or {}).get('report_mode'), 'sops': out(sops), 'drawings': out(drawings)}


def operators_on(c, equipment, a, b):
    """这段时间在这台设备上上工的人（工位终端上工记录，一人可看多台）。"""
    eq = db.get(c, 'equipment', equipment) if equipment else None
    if not eq:
        return []
    at = TABLES['attendance']
    rows = db.rows(c, 'attendance', at.c.clock_on <= b, sa.or_(at.c.clock_off.is_(None), at.c.clock_off >= a))
    ws = db.get(c, 'workstation', equipment=equipment)
    keys = {eq['code'], ws['code'] if ws else None}
    hit = [r for r in rows if keys & {x.strip() for x in (r['workstations'] or '').split(',')}]
    attach_labels(c, 'attendance', hit)
    return [{'employee': r['employee'], 'name': r.get('employee__label'), 'clock_on': db.jsonable(r['clock_on']), 'clock_off': db.jsonable(r['clock_off'])} for r in hit]


@router.get('/api/reports/{rid}/detail')
def report_detail(rid: int, user=Depends(current_user)):
    with db.engine.connect() as c:
        r = db.get(c, 'report', rid)
        if not r:
            raise HTTPException(404, _tr('报工记录不存在'))
        attach_labels(c, 'report', [r])
        wo = db.get(c, 'work_order', r['work_order']) if r['work_order'] else None
        if wo:
            attach_labels(c, 'work_order', [wo])
        op = db.get(c, 'wo_op', r['wo_op']) if r['wo_op'] else None
        proc = db.get(c, 'process', r['process']) if r['process'] else None
        eq = db.get(c, 'equipment', r['equipment']) if r['equipment'] else None
        a, b = r['start'] or r['created_at'], (r['end'] or r['start'] or r['created_at']) + timedelta(seconds=1)
        # 这段时间经过的零件（铸件给出炉次、浇包、砂型、浇注温度）
        parts = []
        ev = PROC_EVENT.get((proc or {}).get('ptype'))
        if ev:
            pe, pt = TABLES['part_event'], TABLES['part']
            q = sa.select(pe.c.sn, pe.c.t, pe.c.station, pe.c.data).where((pe.c.event == ev) & (pe.c.t >= a) & (pe.c.t <= b))
            if eq:
                q = q.where(pe.c.station == eq['code'])
            evs = c.execute(q.order_by(pe.c.t).limit(200)).all()
            pmap = {p['sn']: p for p in db.rows(c, 'part', pt.c.sn.in_([e[0] for e in evs]))} if evs else {}
            for e in evs:
                p = pmap.get(e[0])
                if not p or (wo and p['work_order'] not in (None, wo['id'])):
                    continue
                parts.append({'sn': p['sn'], 't': db.jsonable(e[1]), 'station': e[2], 'heat': p['heat'], 'ladle': p['ladle'], 'mold': p['mold'], 'cavity': p['cavity'],
                              'pour_temp': p['pour_temp'], 'mg': p['mg'], 'status': p['status'], 'machine': p['machine'],
                              'ok': not (isinstance(e[3], dict) and e[3].get('result') == 'NG')})
        heats = defaultdict(int)
        for p in parts:
            if p['heat']:
                heats[p['heat']] += 1
        casting = None
        if (proc or {}).get('ptype') in CASTING_PROC or any(p['ladle'] for p in parts):
            hrows = db.rows(c, 'heat', TABLES['heat'].c.code.in_(list(heats))) if heats else []
            casting = {'heats': [{'code': h['code'], 'parts': heats[h['code']], 'C': h['C'], 'Si': h['Si'], 'CE': h['CE'], 'tap_temp': h['tap_temp'], 'kg': h['kg']} for h in hrows],
                       'ladles': sorted({p['ladle'] for p in parts if p['ladle']}), 'pour_temp_avg': round(sum(p['pour_temp'] for p in parts if p['pour_temp']) / max(1, sum(1 for p in parts if p['pour_temp'])), 1) if any(p['pour_temp'] for p in parts) else None}
        ops_on = operators_on(c, r['equipment'], a, b) if r['equipment'] else []
        ins = instruction_for(c, wo['product'] if wo else None, r['process'], op['station_group'] if op else None, wo['route'] if wo else None, op['seq'] if op else None)
        fai = None
        if op:
            from .gates import fai_required, fai_status
            if fai_required(c, op):
                ok, last = fai_status(c, op)
                fai = {'ok': ok, 'last': out([last])[0] if last else None}
    item = out([r])[0]
    item['source_text'] = source_text(r)
    return {'item': item, 'work_order': out([wo])[0] if wo else None, 'op': out([op])[0] if op else None,
            'equipment': {'id': eq['id'], 'code': eq['code'], 'name': eq['name'], 'etype': eq['etype'], 'status': eq['status']} if eq else None,
            'operator': {'reported_by': r.get('employee__label'), 'on_duty': ops_on,
                         'text': r.get('employee__label') or (_tr('、').join(o['name'] for o in ops_on) + _tr('（看机）') if ops_on else (_tr('设备自动采集，无人工录入') if r['source'] == 'auto' else '—'))},
            'entered_at': db.jsonable(r['created_at']), 'updated_at': db.jsonable(r['updated_at']), 'parts': parts[:100], 'parts_total': len(parts), 'casting': casting,
            'instruction': ins, 'fai': fai}


# ---------------- 模具模次（采集到产出时累加） ----------------
def on_cycle(c, eid, process, ts):
    t = TABLES['tooling']
    try:
        c.execute(t.update().where((t.c.equipment == eid) & (t.c.status == 'in_use')).values(shots=sa.func.coalesce(t.c.shots, 0) + 1, updated_at=ts))
    except Exception:       # noqa: BLE001  模具表还没迁移等：不影响采集
        pass


@action('tooling', 'maint_done')
def _tool_maint(c, t, body, user):
    db.update(c, 'tooling', t['id'], {'last_maint_shots': t['shots'] or 0, 'status': 'in_use' if t['equipment'] else 'available', 'available_from': None}, raw=True)
    return {'msg': _tr('已记录保养（模次 {v}）', v=t['shots'] or 0)}


def tooling_view(t):
    life = t['life_shots']
    rem = (life - (t['shots'] or 0)) if life else None
    nxt = ((t['last_maint_shots'] or 0) + t['maint_every'] - (t['shots'] or 0)) if t['maint_every'] else None
    return {**out([t])[0], 'remaining': rem, 'maint_in': nxt, 'maint_due': nxt is not None and nxt <= 0, 'life_pct': round((t['shots'] or 0) / life, 3) if life else None}


# ---------------- 工位终端 ----------------
def _ws_list(c, codes):
    res = []
    for code in [x.strip() for x in codes.split(',') if x.strip()]:
        w = db.get(c, 'workstation', code=code) or db.get(c, 'workstation', code='WS-' + code)
        if w:
            res.append(w)
    return res


@router.get('/api/station/options')
def station_options(user=Depends(current_user)):
    with db.engine.connect() as c:
        ws = db.rows(c, 'workstation', order=TABLES['workstation'].c.code)
        # 工位终端只管生产设备：网关、交换机、PLC、控制单元、MES 等（导入时 etype=other，又不在任何工艺步骤里）不列出来
        groups = {s['station_group'] for s in db.rows(c, 'route_step') if s['station_group']}
        et = {e['id']: e['etype'] for e in db.rows(c, 'equipment')}
        ws = [w for w in ws if not w['equipment'] or et.get(w['equipment']) not in ('other', 'crew', 'agv') or w['code'] in groups or w.get('process')]
        attach_labels(c, 'workstation', ws)
        emps = db.rows(c, 'employee', order=TABLES['employee'].c.code)
        attach_labels(c, 'employee', emps)
    return {'workstations': out(ws), 'employees': [{'id': e['id'], 'code': e['code'], 'name': e['name'], 'team': e.get('team__label'), 'post': e['post']} for e in emps],
            'me': user.get('employee')}


def _rate(c, station, now, minutes=60):
    pe = TABLES['part_event']
    return c.execute(sa.select(sa.func.count()).select_from(pe).where((pe.c.station == station) & (pe.c.t > now - timedelta(minutes=minutes)) & (pe.c.t <= now)
                                                                     & pe.c.event.in_(tuple(set(PROC_EVENT.values()) | {'scrap'})))).scalar() or 0


@router.get('/api/station/view')
def station_view(ws: str, employee: int = 0, user=Depends(current_user)):
    """工位终端数据：看管的设备（状态、节拍、不良）、模具、当前工序的作业指示、今天的生产任务、上工状态、到期点检。"""
    now = clock.now()
    d0, d1 = db.day_range()
    with db.engine.connect() as c:
        wss = _ws_list(c, ws)
        if not wss:
            raise HTTPException(404, _tr('没有工位 {ws}', ws=ws))
        emp = db.get(c, 'employee', employee or user.get('employee')) if (employee or user.get('employee')) else None
        at = TABLES['attendance']
        att = db.rows(c, 'attendance', at.c.clock_off.is_(None), order=at.c.id.desc(), limit=1, employee=emp['id']) if emp else []
        rt, nt, opt, wot = TABLES['report'], TABLES['ncr'], TABLES['wo_op'], TABLES['work_order']
        al = TABLES['alarm']
        groups = set()
        machines = []
        procs = {p['id']: p for p in db.rows(c, 'process')}
        for w in wss:
            eq = db.get(c, 'equipment', w['equipment']) if w['equipment'] else None
            g = w['group'] or w['code']
            groups.add(g)
            code = eq['code'] if eq else w['code']
            good = c.execute(sa.select(sa.func.coalesce(sa.func.sum(rt.c.qty_good), 0), sa.func.coalesce(sa.func.sum(rt.c.qty_ng), 0))
                             .where((rt.c.equipment == (eq['id'] if eq else -1)) & (rt.c.start >= d0) & (rt.c.start < d1))).first()
            ncr = c.execute(sa.select(sa.func.count()).select_from(nt).where((nt.c.station == code) & (nt.c.found_at >= d0) & (nt.c.found_at < d1))).scalar()
            alarm = c.execute(sa.select(al.c.msg, al.c.start).where((al.c.equipment == (eq['id'] if eq else -1)) & al.c.end.is_(None)).limit(1)).first()
            st = TABLES['equip_state']
            since = c.execute(sa.select(st.c.start).where((st.c.equipment == (eq['id'] if eq else -1)) & st.c.end.is_(None)).order_by(st.c.id.desc()).limit(1)).scalar()
            cur = c.execute(sa.select(opt).join(wot, wot.c.id == opt.c.work_order).where((opt.c.station_group == g) & opt.c.status.in_(('running', 'ready'))
                                                                                          & wot.c.status.in_(('released', 'in_progress')))
                            .order_by(sa.case((opt.c.status == 'running', 0), else_=1), wot.c.priority, opt.c.plan_start).limit(1)).mappings().first()
            cur_d = None
            if cur:
                cur = dict(cur)
                wo = db.get(c, 'work_order', cur['work_order'])
                attach_labels(c, 'work_order', [wo])
                from .gates import fai_required, fai_status
                need = fai_required(c, cur)
                ok, last = fai_status(c, cur) if need else (True, None)
                cur_d = {**out([cur])[0], 'work_order_code': wo['code'], 'product': wo.get('product__label'), 'product_id': wo['product'], 'route': wo['route'],
                         'process_name': (procs.get(cur['process']) or {}).get('name'), 'fai_required': need, 'fai_ok': ok, 'fai': out([last])[0] if last else None,
                         'due': db.jsonable(wo['due'])}
            tools = [tooling_view(t) for t in db.rows(c, 'tooling', equipment=eq['id'])] if eq else []
            mt = TABLES['maint_plan']
            due = db.rows(c, 'maint_plan', sa.or_(mt.c.next_due.is_(None), mt.c.next_due <= now.date()), equipment=eq['id']) if eq else []
            machines.append({'workstation': w['code'], 'group': g, 'name': w['name'], 'equipment': {'id': eq['id'], 'code': eq['code'], 'name': eq['name'], 'etype': eq['etype'],
                                                                                                    'status': eq['status'], 'ideal_cycle_s': eq['ideal_cycle_s']} if eq else None,
                             'since': db.jsonable(since), 'alarm': alarm[0] if alarm else None, 'rate_h': _rate(c, code, now), 'good_today': int(good[0] or 0),
                             'ng_today': int(good[1] or 0), 'ncr_today': ncr, 'current': cur_d, 'tooling': tools, 'maint_due': out(due)})
        # 今天的任务：这些工位组今天计划内或在做的工序
        tasks = [dict(r) for r in c.execute(sa.select(opt).join(wot, wot.c.id == opt.c.work_order).where(
            opt.c.station_group.in_(groups) & wot.c.status.in_(('released', 'in_progress')) & (opt.c.status != 'done')
            & sa.or_(opt.c.status == 'running', sa.and_(opt.c.plan_start < d1, sa.or_(opt.c.plan_end.is_(None), opt.c.plan_end >= d0)), opt.c.plan_start.is_(None)))
            .order_by(wot.c.priority, opt.c.plan_start).limit(30)).mappings()]
        attach_labels(c, 'wo_op', tasks)
        for t in tasks:
            t['process_name'] = (procs.get(t['process']) or {}).get('name')
            t['work_order_code'] = (t.get('work_order__label') or '').split(' ')[0]
        main = next((m['current'] for m in machines if m['current']), None)
        ins = instruction_for(c, main['product_id'], main['process'], main['station_group'], main['route'], main['seq']) if main else None
        shift = None
        from .gates import current_shift_id
        sid = current_shift_id(c, now)
        if sid:
            s = db.get(c, 'shift', sid)
            shift = {'id': s['id'], 'code': s['code'], 'name': s['name'], 'start': s['start'], 'end': s['end']}
    return {'now': db.jsonable(now), 'shift': shift, 'employee': {'id': emp['id'], 'code': emp['code'], 'name': emp['name']} if emp else None,
            'attendance': out(att)[0] if att else None, 'machines': machines, 'tasks': out(tasks), 'instruction': ins}


@router.post('/api/station/clock')
def station_clock(body: dict = Body(...), user=Depends(current_user)):
    """上工 / 下工：{employee(工号或 id), action: on|off, workstations: 'WS-CNC1,WS-CNC2'}。一人可同时看多台。"""
    require_write(user, 'attendance', 'create')
    now = clock.now()
    with db.engine.begin() as c:
        e = body.get('employee') or user.get('employee')
        emp = db.get(c, 'employee', int(e)) if str(e or '').isdigit() and int(e) < 100000 else db.get(c, 'employee', code=str(e or '').strip())
        if not emp:
            raise HTTPException(400, _tr('请先刷工牌或选择员工'))
        at = TABLES['attendance']
        open_ = db.rows(c, 'attendance', at.c.clock_off.is_(None), order=at.c.id.desc(), employee=emp['id'])
        from .gates import current_shift_id
        if body.get('action') == 'off':
            if not open_:
                raise HTTPException(400, _tr('{name} 还没有上工', name=emp['name']))
            for r in open_:
                hrs = round(max(0.0, (now - r['clock_on']).total_seconds()) / 3600, 2)
                db.update(c, 'attendance', r['id'], {'clock_off': now, 'hours': hrs}, raw=True)
            return {'ok': True, 'msg': _tr('{name} 已下工，本次 {hrs:g} 小时', name=emp['name'], hrs=hrs)}
        if open_:
            # 已在岗：更新看管的设备
            db.update(c, 'attendance', open_[0]['id'], {'workstations': body.get('workstations') or open_[0]['workstations']}, raw=True)
            return {'ok': True, 'msg': _tr('{name} 已在岗，看管设备已更新', name=emp['name']), 'id': open_[0]['id']}
        rid = db.insert(c, 'attendance', {'employee': emp['id'], 'workstations': body.get('workstations') or '', 'shift': current_shift_id(c, now), 'clock_on': now,
                                          'source': body.get('source') or 'terminal'}, raw=True)
    return {'ok': True, 'id': rid, 'msg': _tr('{name} 已上工', name=emp['name'])}


# ---------------- 工人日报 / 工时 ----------------
def _shift_window(c, day, shift_id):
    s = db.get(c, 'shift', shift_id) if shift_id else None
    d = parse_dt(day) if isinstance(day, str) else datetime(day.year, day.month, day.day)
    if not s:
        return d, d + timedelta(days=1)
    h1, m1 = map(int, (s['start'] or '00:00').split(':')[:2])
    h2, m2 = map(int, (s['end'] or '00:00').split(':')[:2])
    a = d.replace(hour=h1, minute=m1)
    b = d.replace(hour=h2, minute=m2)
    if b <= a:
        b += timedelta(days=1)
    return a, b


def _team_leader_ok(user):
    roles = set(user.get('role_list') or [])
    if not roles & {'leader', 'manager', 'admin'}:
        raise HTTPException(403, _tr('只有班组长（或厂长、管理员）能审核工人日报'))


@router.get('/api/labor/daily/prefill')
def daily_prefill(day: str = '', shift: int = 0, employee: int = 0, user=Depends(current_user)):
    """日报预填：本人这个班次的报工（人工/扫码/终端）+ 上工期间看管设备的自动报工 + 上下工工时 + 设备停机。"""
    with db.engine.connect() as c:
        emp_id = employee or user.get('employee')
        emp = db.get(c, 'employee', emp_id) if emp_id else None
        if not emp:
            raise HTTPException(400, _tr('当前账号没有关联员工'))
        now = clock.now()
        if not shift:
            from .gates import current_shift_id
            shift = current_shift_id(c, now) or 0
        day_ = day or now.date().isoformat()
        if not day and shift:
            # 跨零点的班（夜班 23:00–07:00）：零点以后还算前一天的班
            s = db.get(c, 'shift', shift)
            if s and (s['end'] or '') < (s['start'] or '') and now.strftime('%H:%M') < (s['end'] or '00:00'):
                day_ = (now.date() - timedelta(days=1)).isoformat()
        a, b = _shift_window(c, day_, shift)
        rt, at, st = TABLES['report'], TABLES['attendance'], TABLES['equip_state']
        mine = db.rows(c, 'report', (rt.c.employee == emp['id']) & (rt.c.start >= a) & (rt.c.start < b))
        att = db.rows(c, 'attendance', (at.c.employee == emp['id']) & (at.c.clock_on < b) & sa.or_(at.c.clock_off.is_(None), at.c.clock_off > a))
        hours = 0.0
        eq_ids = set()
        win = []
        for r in att:
            s0, e0 = max(a, r['clock_on']), min(b, r['clock_off'] or clock.now())
            if e0 > s0:
                hours += (e0 - s0).total_seconds() / 3600
                win.append((s0, e0))
            for code in (r['workstations'] or '').split(','):
                w = db.get(c, 'workstation', code=code.strip()) or db.get(c, 'workstation', code='WS-' + code.strip())
                if w and w['equipment']:
                    eq_ids.add(w['equipment'])
        auto = []
        for s0, e0 in win:
            if eq_ids:
                auto += db.rows(c, 'report', (rt.c.source == 'auto') & rt.c.equipment.in_(eq_ids) & (rt.c.start >= s0 - timedelta(minutes=59)) & (rt.c.start < e0))
        rows = mine + [r for r in auto if r['id'] not in {m['id'] for m in mine}]
        attach_labels(c, 'report', rows)
        down = 0.0
        for s0, e0 in win:
            if eq_ids:
                for r in db.rows(c, 'equip_state', st.c.equipment.in_(eq_ids) & (st.c.state.in_(('Stopped', 'down'))) & (st.c.start < e0)
                                 & sa.or_(st.c.end.is_(None), st.c.end > s0)):
                    down += (min(e0, r['end'] or e0) - max(s0, r['start'])).total_seconds() / 60
        ex = db.get(c, 'worker_daily', day=parse_dt(day_).date(), employee=emp['id'], shift=shift or None)
    lines = defaultdict(lambda: {'good': 0, 'ng': 0, 'records': 0, 'sources': set()})
    for r in rows:
        k = (r.get('work_order__label') or '—', r.get('process__label') or '—', r.get('equipment__label') or '')
        lines[k]['good'] += r['qty_good'] or 0
        lines[k]['ng'] += r['qty_ng'] or 0
        lines[k]['records'] += 1
        lines[k]['sources'].add(SRC_CN.get(r['source'], r['source']))
    ls = [{'work_order': k[0].split(' ')[0], 'process': k[1].split(' ', 1)[-1], 'equipment': k[2].split(' ')[0], 'good': v['good'], 'ng': v['ng'], 'records': v['records'],
           'source': _tr('、').join(sorted(v['sources']))} for k, v in lines.items()]
    return {'day': day_, 'shift': shift or None, 'employee': {'id': emp['id'], 'code': emp['code'], 'name': emp['name'], 'team': emp['team']},
            'window': [db.jsonable(a), db.jsonable(b)], 'qty_good': sum(x['good'] for x in ls), 'qty_ng': sum(x['ng'] for x in ls), 'hours': round(hours, 2),
            'downtime_min': round(down, 1), 'lines': ls, 'existing': out([ex])[0] if ex else None}


@router.post('/api/labor/daily')
def daily_submit(body: dict = Body(...), user=Depends(current_user)):
    """提交（或保存草稿）工人日报：同一人同一天同一班次只有一份，退回后可改了再交。"""
    require_write(user, 'worker_daily', 'create')
    with db.engine.begin() as c:
        emp = db.get(c, 'employee', int(body.get('employee') or user.get('employee') or 0))
        if not emp:
            raise HTTPException(400, _tr('当前账号没有关联员工'))
        day = parse_dt(body.get('day') or clock.now().date().isoformat()).date()
        shift = int(body['shift']) if body.get('shift') else None
        ex = db.get(c, 'worker_daily', day=day, employee=emp['id'], shift=shift)
        if ex and ex['status'] == 'approved':
            raise HTTPException(409, _tr('{day} 的日报已经审核通过，不能再改', day=day))
        vals = {'day': day, 'shift': shift, 'employee': emp['id'], 'team': emp['team'], 'qty_good': int(body.get('qty_good') or 0), 'qty_ng': int(body.get('qty_ng') or 0),
                'hours': float(body.get('hours') or 0), 'downtime_min': float(body.get('downtime_min') or 0), 'remark': body.get('remark') or '',
                'lines': body.get('lines') or [], 'status': 'submitted' if body.get('submit', True) else 'draft'}
        if vals['hours'] < 0 or vals['hours'] > 16:
            raise HTTPException(400, _tr('工时要在 0–16 小时之间'))
        if vals['status'] == 'submitted':
            vals['submitted_at'] = clock.now()
        if ex:
            db.update(c, 'worker_daily', ex['id'], vals, raw=True)
            rid = ex['id']
        else:
            rid = db.insert(c, 'worker_daily', vals, raw=True)
        r = db.get(c, 'worker_daily', rid)
        if vals['status'] == 'submitted':
            db.insert(c, 'notice', {'title': _tr('{name} 提交了 {day} 的日报', name=emp['name'], day=day), 'content': _tr('合格 {qty_good}，不良 {qty_ng}，工时 {hours:g} h', qty_good=vals['qty_good'], qty_ng=vals['qty_ng'], hours=vals['hours']),
                                    'to_role': 'leader', 'channel': 'inbox', 'level': 'info', 'link': f'/labor/daily?id={rid}', 'source': '工人日报', 'read': False,
                                    'time': clock.now()}, raw=True)
    return {'ok': True, 'id': rid, 'code': r['code'], 'status': r['status']}


@action('worker_daily', 'approve')
def _daily_approve(c, r, body, user):
    _team_leader_ok(user)
    if r['status'] != 'submitted':
        raise HTTPException(400, _tr('只有待审核的日报能审核'))
    db.update(c, 'worker_daily', r['id'], {'status': 'approved', 'reviewer': user['name'] or user['username'], 'reviewed_at': clock.now(),
                                           'review_note': body.get('note') or r['review_note']}, raw=True)
    return {'msg': _tr('已审核通过')}


@action('worker_daily', 'reject')
def _daily_reject(c, r, body, user):
    _team_leader_ok(user)
    if r['status'] != 'submitted':
        raise HTTPException(400, _tr('只有待审核的日报能退回'))
    if not body.get('note'):
        raise HTTPException(400, _tr('退回请写明原因'))
    db.update(c, 'worker_daily', r['id'], {'status': 'rejected', 'reviewer': user['name'] or user['username'], 'reviewed_at': clock.now(), 'review_note': body['note']},
              raw=True)
    emp = db.get(c, 'employee', r['employee'])
    u = db.get(c, 'sys_user', employee=r['employee'])
    db.insert(c, 'notice', {'title': _tr('{day} 的日报被退回', day=r['day']), 'content': body['note'], 'to_user': u['username'] if u else None, 'to_role': None if u else 'operator',
                            'channel': 'inbox', 'level': 'warn', 'link': f"/m?tab=daily", 'source': '工人日报', 'read': False, 'time': clock.now()}, raw=True)
    return {'msg': _tr('已退回给 {v}', v=emp['name'] if emp else '')}


@router.get('/api/labor/daily')
def daily_list(status: str = '', day: str = '', frm: str = '', to: str = '', team: int = 0, mine: int = 0, user=Depends(current_user)):
    t = TABLES['worker_daily']
    cond = []
    if status:
        cond.append(t.c.status.in_(status.split(',')))
    if day:
        cond.append(t.c.day == parse_dt(day).date())
    if frm:
        cond.append(t.c.day >= parse_dt(frm).date())
    if to:
        cond.append(t.c.day <= parse_dt(to).date())
    if team:
        cond.append(t.c.team == team)
    if mine:
        cond.append(t.c.employee == (user.get('employee') or -1))
    with db.engine.connect() as c:
        rows = db.rows(c, 'worker_daily', *cond, order=(t.c.day.desc(), t.c.id.desc()), limit=300)
        attach_labels(c, 'worker_daily', rows)
        counts = dict(c.execute(sa.select(t.c.status, sa.func.count()).group_by(t.c.status)).all())
    return {'items': out(rows), 'counts': counts}


@router.get('/api/labor/hours')
def labor_hours(frm: str = '', to: str = '', by: str = 'worker', user=Depends(current_user)):
    """工时统计：按人 / 班组 / 天。工时取日报（已审核 + 待审核分开列），上工记录的工时作对照。"""
    d1 = parse_dt(to).date() if to else clock.now().date()
    d0 = parse_dt(frm).date() if frm else d1 - timedelta(days=6)
    t, at = TABLES['worker_daily'], TABLES['attendance']
    with db.engine.connect() as c:
        rows = db.rows(c, 'worker_daily', (t.c.day >= d0) & (t.c.day <= d1))
        attach_labels(c, 'worker_daily', rows)
        att = db.rows(c, 'attendance', (at.c.clock_on >= datetime(d0.year, d0.month, d0.day)) & (at.c.clock_on < datetime(d1.year, d1.month, d1.day) + timedelta(days=1)))
        attach_labels(c, 'attendance', att)
        emps = {e['id']: e for e in db.rows(c, 'employee')}
        teams = {x['id']: x['name'] for x in db.rows(c, 'team')}
    key = {'worker': lambda r: (r.get('employee__label') or '—'), 'team': lambda r: teams.get(r.get('team') or (emps.get(r['employee']) or {}).get('team'), '—'),
           'day': lambda r: db.jsonable(r['day'])}.get(by)
    if not key:
        raise HTTPException(400, _tr('by 只能是 worker / team / day'))
    agg = defaultdict(lambda: {'approved_h': 0.0, 'pending_h': 0.0, 'clock_h': 0.0, 'good': 0, 'ng': 0, 'downtime_min': 0.0, 'reports': 0, 'people': set()})
    for r in rows:
        a = agg[key(r)]
        if r['status'] in ('approved', 'submitted'):
            a['approved_h' if r['status'] == 'approved' else 'pending_h'] += r['hours'] or 0
            a['good'] += r['qty_good'] or 0
            a['ng'] += r['qty_ng'] or 0
            a['downtime_min'] += r['downtime_min'] or 0
            a['reports'] += 1
            a['people'].add(r['employee'])
    for r in att:
        k = key({**r, 'team': (emps.get(r['employee']) or {}).get('team'), 'day': r['clock_on'].date()}) if by != 'worker' else (r.get('employee__label') or '—')
        agg[k]['clock_h'] += r['hours'] or (max(0, (clock.now() - r['clock_on']).total_seconds()) / 3600 if not r['clock_off'] else 0)
        agg[k]['people'].add(r['employee'])
    res = [{'key': k, **{x: (round(v, 2) if isinstance(v, float) else v) for x, v in a.items() if x != 'people'}, 'people': len(a['people']),
            'per_hour': round(a['good'] / a['approved_h'], 1) if a['approved_h'] else None} for k, a in agg.items()]
    res.sort(key=lambda r: r['key'])
    return {'from': d0.isoformat(), 'to': d1.isoformat(), 'by': by, 'rows': res,
            'total': {'approved_h': round(sum(r['approved_h'] for r in res), 2), 'pending_h': round(sum(r['pending_h'] for r in res), 2),
                      'clock_h': round(sum(r['clock_h'] for r in res), 2), 'good': sum(r['good'] for r in res)}}


# ---------------- 手机端查询 ----------------
@router.get('/api/m/search')
def m_search(q: str, user=Depends(current_user)):
    """查询：工单号、零件码、设备编号/名称（扫码或手输）。"""
    q = q.strip()
    if not q:
        return {'items': []}
    items = []
    with db.engine.connect() as c:
        p = db.get(c, 'part', sn=q)
        if p:
            attach_labels(c, 'part', [p])
            items.append({'kind': 'part', 'id': p['id'], 'title': p['sn'], 'sub': f"{p.get('product__label') or ''} · {p['status']} · {p['station'] or ''}",
                          'link': f"/trace?sn={p['sn']}", 'data': out([p])[0]})
        wt, et, pt = TABLES['work_order'], TABLES['equipment'], TABLES['part']
        for w in db.rows(c, 'work_order', wt.c.code.ilike(f'%{q}%'), order=wt.c.id.desc(), limit=5):
            attach_labels(c, 'work_order', [w])
            items.append({'kind': 'work_order', 'id': w['id'], 'title': w['code'], 'sub': f"{w.get('product__label') or ''} · {w['qty_done'] or 0}/{w['qty']} · {w['status']}",
                          'link': f"/e/work_order?id={w['id']}", 'data': out([w])[0]})
        for e in db.rows(c, 'equipment', sa.or_(et.c.code.ilike(f'%{q}%'), et.c.name.ilike(f'%{q}%')), order=et.c.code, limit=5):
            items.append({'kind': 'equipment', 'id': e['id'], 'title': f"{e['code']} {e['name']}", 'sub': _tr('状态 {v}', v=e['status'] or '—'), 'link': f"/e/equipment?id={e['id']}",
                          'data': {'code': e['code'], 'name': e['name'], 'status': e['status']}})
        if not p and len(q) >= 4:
            for x in db.rows(c, 'part', pt.c.sn.ilike(f'%{q}%'), order=pt.c.id.desc(), limit=5):
                items.append({'kind': 'part', 'id': x['id'], 'title': x['sn'], 'sub': f"{x['status']} · {x['station'] or ''}", 'link': f"/trace?sn={x['sn']}", 'data': out([x])[0]})
    return {'items': items}


@router.get('/api/m/home')
def m_home(user=Depends(current_user)):
    """手机端首页角标：我的任务数、待点检、待首检、未读通知、今天的日报状态。"""
    with db.engine.connect() as c:
        from .ops import _mine
        nt = TABLES['notice']
        unread = c.execute(sa.select(sa.func.count()).select_from(nt).where(_mine(user) & (nt.c.channel == 'inbox') & (nt.c.read.is_(False) | nt.c.read.is_(None)))).scalar()
        fa = TABLES['first_article']
        fai = c.execute(sa.select(sa.func.count()).select_from(fa).where(fa.c.status == 'pending')).scalar()
        mt = TABLES['maint_plan']
        due = c.execute(sa.select(sa.func.count()).select_from(mt).where(sa.or_(mt.c.next_due.is_(None), mt.c.next_due <= clock.now().date()))).scalar()
        d = db.get(c, 'worker_daily', day=clock.now().date(), employee=user.get('employee') or -1)
    return {'unread': unread, 'fai_pending': fai, 'maint_due': due, 'daily': d['status'] if d else None, 'employee': user.get('employee'), 'now': db.jsonable(clock.now())}
