"""生产：工单下达、有限产能排产、派工、手工/扫码报工、领退料、工单进度。"""
from ..i18n import _tr
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db
from ..auth import current_user, require_write
from ..crud import action, attach_labels, out
from ..db import clock
from ..schema import TABLES

router = APIRouter()


def group_capacity(c) -> dict:
    """工位组 → 可并行设备数（按工位表统计）。"""
    cap = {}
    for w in db.rows(c, 'workstation'):
        g = w['group'] or w['code']
        cap[g] = cap.get(g, 0) + 1
    return cap


@action('work_order', 'release')
def release(c, wo, body, user):
    if wo['status'] != 'draft':
        raise HTTPException(400, _tr('只有草稿状态的工单可以下达'))
    route = wo['route']
    if not route:
        r = db.get(c, 'route', product=wo['product'], status='active')
        route = r['id'] if r else None
    if not route:
        raise HTTPException(400, _tr('这个产品还没有生效的工艺路线'))
    steps = db.rows(c, 'route_step', order=TABLES['route_step'].c.seq, route=route)
    c.execute(TABLES['wo_op'].delete().where(TABLES['wo_op'].c.work_order == wo['id']))
    for i, s in enumerate(steps):
        db.insert(c, 'wo_op', {'work_order': wo['id'], 'seq': s['seq'], 'process': s['process'], 'station_group': s['station_group'],
                               'qty_plan': wo['qty'], 'qty_good': 0, 'qty_ng': 0, 'status': 'ready' if i == 0 else 'waiting'}, raw=True)
    db.update(c, 'work_order', wo['id'], {'route': route, 'status': 'released', 'released_at': clock.now(),
                                          'start_plan': wo['start_plan'] or clock.now()}, raw=True)
    from .. import seed_biz
    if wo['id'] in seed_biz.demo_orders(c):       # seed 的演示工单：CNC 首检直接放行（演示数据），新建的工单照常要首检
        seed_biz.approve_demo_fai(c, [wo['id']])
    schedule(c)
    return {'msg': _tr('已下达，生成 {len} 道工序并完成排产', len=len(steps))}


@action('work_order', 'close')
def close(c, wo, body, user):
    db.update(c, 'work_order', wo['id'], {'status': 'closed', 'completed_at': wo['completed_at'] or clock.now()}, raw=True)


@action('work_order', 'cancel')
def cancel(c, wo, body, user):
    if wo['status'] in ('in_progress', 'completed'):
        raise HTTPException(400, _tr('已开工的工单请用"关闭"'))
    db.update(c, 'work_order', wo['id'], {'status': 'cancelled'}, raw=True)


@action('wo_op', 'dispatch')
def dispatch(c, op, body, user):
    if not body.get('assignee'):
        raise HTTPException(400, _tr('请选择派工对象'))
    db.update(c, 'wo_op', op['id'], {'assignee': int(body['assignee']), 'status': 'ready' if op['status'] == 'waiting' else op['status']}, raw=True)


def schedule(c, overrides=None):
    """有限产能排产（带班次、停机、技能、模具、换型、齐套约束），实现见 services/scheduling.py。返回预计逾期的工单号。"""
    from .scheduling import schedule as _sched
    return _sched(c, overrides)


@router.get('/api/production/gantt')
def gantt(user=Depends(current_user)):
    wot, opt, pt = TABLES['work_order'], TABLES['wo_op'], TABLES['process']
    with db.engine.connect() as c:
        wos = db.rows(c, 'work_order', wot.c.status.in_(('released', 'in_progress', 'completed')), order=(wot.c.priority, wot.c.due, wot.c.id), limit=40)
        attach_labels(c, 'work_order', wos)
        ops = []
        for wo in wos:
            rows = db.rows(c, 'wo_op', order=opt.c.seq, work_order=wo['id'])
            attach_labels(c, 'wo_op', rows)
            ops += rows
    return {'orders': out(wos), 'ops': out(ops), 'now': db.jsonable(clock.now())}


@router.get('/api/production/board')
def board(user=Depends(current_user)):
    """工单进度看板。"""
    wot = TABLES['work_order']
    with db.engine.connect() as c:
        wos = db.rows(c, 'work_order', wot.c.status.in_(('released', 'in_progress')), order=(wot.c.priority, wot.c.due))
        attach_labels(c, 'work_order', wos)
        for wo in wos:
            ops = db.rows(c, 'wo_op', order=TABLES['wo_op'].c.seq, work_order=wo['id'])
            attach_labels(c, 'wo_op', ops)
            wo['ops'] = out(ops)
            last = ops[-1]['plan_end'] if ops else None
            wo['eta'] = db.jsonable(last)
            wo['late'] = bool(last and wo['due'] and last > wo['due'])
    return out(wos)


# ---------------- 手机扫码报工 ----------------
@router.get('/api/production/my_tasks')
def my_tasks(user=Depends(current_user)):
    opt, wot = TABLES['wo_op'], TABLES['work_order']
    with db.engine.connect() as c:
        q = sa.select(opt).join(wot, wot.c.id == opt.c.work_order).where(wot.c.status.in_(('released', 'in_progress')) & opt.c.status.in_(('ready', 'running', 'waiting')))
        if user.get('employee') and 'operator' in user['role_list']:
            q = q.where(sa.or_(opt.c.assignee == user['employee'], opt.c.assignee.is_(None)))
        ops = [dict(r) for r in c.execute(q.order_by(wot.c.priority, opt.c.seq).limit(60)).mappings()]
        attach_labels(c, 'wo_op', ops)
        procs = {p['id']: p for p in db.rows(c, 'process')}
        modes = {(s['route'], s['seq']): s['report_mode'] for s in db.rows(c, 'route_step')}
        wos = {w['id']: w for w in db.rows(c, 'work_order', wot.c.id.in_({o['work_order'] for o in ops}))} if ops else {}
        for o in ops:
            wo = wos.get(o['work_order'], {})
            o['mode'] = modes.get((wo.get('route'), o['seq']), 'auto')
            o['process_name'] = procs.get(o['process'], {}).get('name')
    return out(ops)


@router.post('/api/production/report')
def manual_report(body: dict = Body(...), user=Depends(current_user)):
    """人工 / 扫码 / 工位终端报工：{wo_op 或 sn+process, qty_good, qty_ng, defect, equipment, idem_key, source}。
    幂等（idem_key）、超报、跳序、首检、批次冻结等闸门见 services/gates.py；违反时返回 409 和中文说明。"""
    from .gates import report_endpoint
    return report_endpoint(body, user)


@router.post('/api/production/issue_bom')
def issue_bom(body: dict = Body(...), user=Depends(current_user)):
    """按 BOM 领料：{work_order, qty(套数), warehouse}"""
    require_write(user, 'material_issue', 'create')
    from .warehouse import move
    with db.engine.begin() as c:
        wo = db.get(c, 'work_order', int(body['work_order']))
        sets = float(body.get('qty') or wo['qty'])
        bom = db.rows(c, 'bom', product=wo['product'])
        if not bom:
            raise HTTPException(400, _tr('这个产品没有 BOM'))
        wh = int(body['warehouse']) if body.get('warehouse') else None
        for b in bom:
            q = round(b['qty'] * sets, 3)
            db.insert(c, 'material_issue', {'kind': 'issue', 'work_order': wo['id'], 'material': b['component'], 'qty': q, 'warehouse': wh,
                                            'employee': user.get('employee'), 'time': clock.now()}, raw=True)
            if wh:
                move(c, 'out', b['component'], q, wh, ref=wo['code'], who=user['name'] or user['username'], reason=_tr('按 BOM 领料'))
    return {'ok': True, 'lines': len(bom)}
