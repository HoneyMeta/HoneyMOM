"""质量与报工闸门：

1. 报工幂等：客户端每次"提交意图"生成一个 idem_key（双击、网络重试都带同一个），服务端 report.idem_key 唯一；重复的直接返回第一次的结果，不重复计数。
2. 超报：本工序累计合格不能超过计划数（setting report_over_pct 可放宽）。
3. 跳序：上道工序没报工不能报本道；本道累计（合格+不良）不能超过上道转来的合格数。
4. 首检：工艺步骤勾了"首检放行"的工序，没有已放行的首检单就不能量产报工。
5. 批次冻结：冻结中的批次/炉次，零件不能报工、不能出库（出入库单的出库/移库会被拦下）。
6. 返修复检：返修完成 → 复检（生成检验单），复检合格才能关 NCR（quality._ncr_close 检查）。
"""
from ..i18n import _tr
import threading
from datetime import timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db
from ..auth import current_user, require_write
from ..crud import ACTIONS, HOOKS, action, attach_labels, out
from ..db import clock
from ..schema import TABLES

router = APIRouter()
_lock = threading.Lock()          # 单进程：报工串行处理，检查和累加之间不会被并发请求插队
SOURCES = {'manual', 'mobile', 'scan', 'terminal'}
REPORT_GATES: list = []           # fn(c, 工单, 工序, 请求体)：不允许报工时抛 Gate


class Gate(HTTPException):
    """违反业务闸门：HTTP 409，detail 是给人看的中文说明，响应头 X-Gate 是闸门编码（fai / hold / sequence / over / status）。"""
    def __init__(self, code, msg, **extra):
        super().__init__(409, msg, headers={'X-Gate': code})
        self.gate = code
        self.extra = extra


# ---------------- 首检 ----------------
def route_step_of(c, op):
    wo = db.get(c, 'work_order', op['work_order'])
    if not wo or not wo['route']:
        return None
    return db.get(c, 'route_step', route=wo['route'], seq=op['seq'])


def fai_required(c, op) -> bool:
    st = route_step_of(c, op)
    return bool(st and st.get('fai'))


def fai_status(c, op):
    """该工序的首检情况：(是否放行, 最近一张首检单)。"""
    ft = TABLES['first_article']
    rows = db.rows(c, 'first_article', order=ft.c.id.desc(), limit=5, wo_op=op['id'])
    ok = next((r for r in rows if r['status'] == 'approved'), None)
    return bool(ok), (ok or (rows[0] if rows else None))


def check_fai(c, op):
    if not fai_required(c, op):
        return
    ok, last = fai_status(c, op)
    if ok:
        return
    p = db.get(c, 'process', op['process']) or {}
    wo = db.get(c, 'work_order', op['work_order'])
    if last and last['status'] == 'pending':
        raise Gate('fai', _tr('首检未放行：{code} {get} 的首检单 {code2} 还在待检，放行后才能量产报工', code=wo['code'], get=p.get('name', ''), code2=last['code']), fai=last['id'])
    if last and last['status'] == 'rejected':
        raise Gate('fai', _tr('首检不放行：{code} {get} 的首检单 {code2} 判定不合格，请调整后重新申请首检', code=wo['code'], get=p.get('name', ''), code2=last['code']), fai=last['id'])
    raise Gate('fai', _tr('首检未放行：{code} {get} 需要先做首检（首件检验合格、质检放行）才能量产报工', code=wo['code'], get=p.get('name', '')))


def request_fai(c, op, user, equipment=None, sn=None, trigger='start'):
    tpl = db.get(c, 'insp_template', itype='FAI', process=op['process']) or db.get(c, 'insp_template', itype='FAI')
    wo = db.get(c, 'work_order', op['work_order'])
    eq = db.get(c, 'equipment', int(equipment)) if equipment else None
    rid = db.insert(c, 'insp_record', {'template': tpl['id'] if tpl else None, 'itype': 'FAI', 'work_order': wo['id'], 'sn': sn or None,
                                       'station': eq['code'] if eq else op['station_group'], 'qty_sample': 1, 'qty_ng': 0, 'result': 'pending', 'time': clock.now(),
                                       'source': 'manual', 'remark': _tr('首检')}, raw=True)
    fid = db.insert(c, 'first_article', {'work_order': wo['id'], 'wo_op': op['id'], 'process': op['process'], 'equipment': eq['id'] if eq else None, 'sn': sn or None,
                                         'trigger': trigger, 'status': 'pending', 'record': rid, 'requested_by': user['name'] or user['username'],
                                         'requested_at': clock.now()}, raw=True)
    return fid


@router.post('/api/quality/fai')
def fai_create(body: dict = Body(...), user=Depends(current_user)):
    """申请首检：{wo_op | work_order+process, equipment?, sn?, trigger?}"""
    require_write(user, 'report', 'create')
    with db.engine.begin() as c:
        op = _resolve_op(c, body)
        _, last = fai_status(c, op)
        if last and last['status'] == 'pending':
            return {'ok': True, 'id': last['id'], 'code': last['code'], 'msg': _tr('已有待检的首检单 {code}', code=last['code'])}
        fid = request_fai(c, op, user, body.get('equipment'), body.get('sn'), body.get('trigger') or 'start')
        f = db.get(c, 'first_article', fid)
    return {'ok': True, 'id': fid, 'code': f['code'], 'msg': _tr('已申请首检 {code}，等质检判定放行', code=f['code'])}


@router.get('/api/quality/fai/candidates')
def fai_candidates(user=Depends(current_user)):
    """需要首检的在制工序（工艺步骤勾了首检放行）及其首检状态。"""
    wot, opt = TABLES['work_order'], TABLES['wo_op']
    res = []
    with db.engine.connect() as c:
        ops = [dict(r) for r in c.execute(sa.select(opt).join(wot, wot.c.id == opt.c.work_order).where(wot.c.status.in_(('released', 'in_progress')) & (opt.c.status != 'done'))
                                          .order_by(wot.c.priority, opt.c.seq)).mappings()]
        for op in ops:
            if not fai_required(c, op):
                continue
            ok, last = fai_status(c, op)
            wo = db.get(c, 'work_order', op['work_order'])
            p = db.get(c, 'process', op['process']) or {}
            res.append({'wo_op': op['id'], 'work_order': wo['code'], 'work_order_id': wo['id'], 'process': p.get('name'), 'process_id': op['process'], 'seq': op['seq'],
                        'station_group': op['station_group'], 'status': op['status'], 'fai_ok': ok, 'fai': out([last])[0] if last else None,
                        'qty_good': op['qty_good'], 'qty_plan': op['qty_plan']})
    return res


@router.get('/api/quality/fai/{fid}')
def fai_get(fid: int, user=Depends(current_user)):
    with db.engine.connect() as c:
        f = db.get(c, 'first_article', fid)
        if not f:
            raise HTTPException(404, _tr('首检单不存在'))
        attach_labels(c, 'first_article', [f])
        rec = db.get(c, 'insp_record', f['record']) if f['record'] else None
        items = db.rows(c, 'insp_item', order=TABLES['insp_item'].c.seq, template=rec['template']) if rec and rec['template'] else []
        vals = db.rows(c, 'insp_value', record=rec['id']) if rec else []
    return {'item': out([f])[0], 'record': out([rec])[0] if rec else None, 'items': out(items), 'values': out(vals)}


@router.post('/api/quality/fai/{fid}/inspect')
def fai_inspect(fid: int, body: dict = Body(...), user=Depends(current_user)):
    """首检录入：{values:[{item, value|ok}]} → 按上下限判定；合格即放行，不合格不放行（并登记 NCR）。"""
    require_write(user, 'first_article', 'create')
    from .quality import judge_record
    with db.engine.begin() as c:
        f = db.get(c, 'first_article', fid)
        if not f:
            raise HTTPException(404, _tr('首检单不存在'))
        if f['status'] != 'pending':
            raise HTTPException(400, _tr('这张首检单已经判定过了'))
        rid = f['record']
        from .iatf import check_gauges
        check_gauges(c, body.get('values') or [], db.get(c, 'insp_record', rid))     # 量具必须有效（过期锁定的拦下）
        iv = TABLES['insp_value']
        c.execute(iv.delete().where(iv.c.record == rid))
        now = clock.now()
        for v in body.get('values') or []:
            it = db.get(c, 'insp_item', int(v['item'])) if v.get('item') else None
            val = v.get('value')
            val = float(val) if val not in (None, '') else None
            c.execute(iv.insert().values(record=rid, item=it['id'] if it else None, char=it['char'] if it else None, value=val, text=v.get('text'),
                                         ok=v.get('ok', True) if val is None else None, gauge=int(v['gauge']) if v.get('gauge') else None,
                                         created_at=now, updated_at=now))
        if not body.get('values'):
            raise HTTPException(400, _tr('请录入首检项目的实测值'))
        judge_record(c, rid, user)
        rec = db.get(c, 'insp_record', rid)
        db.update(c, 'insp_record', rid, {'inspector': user.get('employee')}, raw=True)
        st = 'approved' if rec['result'] == 'OK' else 'rejected'
        if st == 'approved':           # 首件放行要电子签名（不合格不放行不用签）
            from . import esign
            esign.require(c, user, body, 'first_article', fid, 'approve', _tr('{code} 首件放行', code=f['code']))
        db.update(c, 'first_article', fid, {'status': st, 'decided_by': user['name'] or user['username'], 'decided_at': now}, raw=True)
    return {'ok': True, 'status': st, 'result': rec['result'], 'msg': _tr('首检合格，已放行，可以量产报工') if st == 'approved' else _tr('首检不合格，不放行')}


@action('first_article', 'approve')
def _fai_approve(c, f, body, user):
    if f['status'] == 'approved':
        return {'msg': _tr('已经放行')}
    rec = db.get(c, 'insp_record', f['record']) if f['record'] else None
    if not rec or rec['result'] != 'OK':
        raise HTTPException(409, _tr('首检记录还没有判定合格（先录入首检值），不能放行'))
    db.update(c, 'first_article', f['id'], {'status': 'approved', 'decided_by': user['name'] or user['username'], 'decided_at': clock.now()}, raw=True)
    return {'msg': _tr('已放行')}


@action('first_article', 'reject')
def _fai_reject(c, f, body, user):
    db.update(c, 'first_article', f['id'], {'status': 'rejected', 'decided_by': user['name'] or user['username'], 'decided_at': clock.now(),
                                            'remark': body.get('remark') or f['remark']}, raw=True)
    return {'msg': _tr('已判定不放行')}


# ---------------- 批次冻结 ----------------
def lot_held(c, lot):
    if not lot:
        return None
    return db.get(c, 'lot_hold', lot=str(lot).strip(), status='hold')


def check_lot_out(c, lot=None, sn=None, what='出库'):
    if sn:
        p = db.get(c, 'part', sn=sn)
        if p and p['status'] == 'hold':
            h = lot_held(c, p['heat'])
            raise Gate('hold', _tr('零件 {sn} 所在批次 {v} 已冻结', sn=sn, v=p['heat'] or '') + (_tr('（{code}：{reason}）', code=h['code'], reason=h['reason']) if h else '') + _tr('，不能{what}', what=what), hold=h['id'] if h else None)
        lot = lot or (p['heat'] if p else None)
    h = lot_held(c, lot)
    if h:
        raise Gate('hold', _tr('批次 {lot} 已冻结（{code}：{reason}），不能{what}；质量解冻后再操作', lot=lot, code=h['code'], reason=h['reason'], what=what), hold=h['id'])


def _after_hold(c, rid, user):
    h = db.get(c, 'lot_hold', rid)
    if h['held_at']:
        return
    pt, pe = TABLES['part'], TABLES['part_event']
    now = clock.now()
    parts = db.rows(c, 'part', pt.c.status.notin_(('shipped', 'scrap', 'hold')), heat=h['lot'])
    for p in parts:
        c.execute(pe.insert().values(sn=p['sn'], t=now, station=_tr('质量'), event='hold', data={'prev': p['status'], 'hold': h['code']}, created_at=now, updated_at=now))
    c.execute(pt.update().where((pt.c.heat == h['lot']) & pt.c.status.notin_(('shipped', 'scrap', 'hold'))).values(status='hold', updated_at=now))
    db.update(c, 'lot_hold', rid, {'status': 'hold', 'parts': len(parts), 'held_by': user['name'] or user['username'], 'held_at': now}, raw=True)


HOOKS[('lot_hold', 'after_save')] = _after_hold


@action('lot_hold', 'release')
def _hold_release(c, h, body, user):
    if h['status'] != 'hold':
        raise HTTPException(400, _tr('这个批次没有冻结'))
    pt, pe = TABLES['part'], TABLES['part_event']
    now = clock.now()
    n = 0
    after = {'inspected': None, 'stored': 'fg', 'shipped': 'shipped', 'rework': 'rework', 'to_raw_store': 'raw_store', 'delivered': 'wip', 'machined': 'wip'}
    for p in db.rows(c, 'part', status='hold', heat=h['lot']):
        hev = c.execute(sa.select(pe.c.id, pe.c.data).where((pe.c.sn == p['sn']) & (pe.c.event == 'hold')).order_by(pe.c.id.desc()).limit(1)).first()
        prev = ((hev[1] if hev else None) or {}).get('prev') or 'wip'
        # 冻结期间现场还在流转（采集照记）：按冻结之后最后一个事件恢复真实状态
        for ev, data in c.execute(sa.select(pe.c.event, pe.c.data).where((pe.c.sn == p['sn']) & (pe.c.id > (hev[0] if hev else 0))).order_by(pe.c.id)).all():
            if ev == 'inspected':
                prev = 'good' if (data or {}).get('result') == 'OK' else prev
            elif after.get(ev):
                prev = after[ev]
        c.execute(pt.update().where(pt.c.id == p['id']).values(status=prev, updated_at=now))
        c.execute(pe.insert().values(sn=p['sn'], t=now, station=_tr('质量'), event='release', data={'status': prev, 'hold': h['code']}, created_at=now, updated_at=now))
        n += 1
    db.update(c, 'lot_hold', h['id'], {'status': 'released', 'released_by': user['name'] or user['username'], 'released_at': now,
                                       'release_note': body.get('note') or h['release_note']}, raw=True)
    return {'msg': _tr('已解冻，恢复 {n} 个零件', n=n)}


# 出入库单：出库 / 移库遇到冻结批次直接拦下（整个保存回滚）
_prev_txn_hook = HOOKS.get(('stock_txn', 'after_save'))


def _txn_gate(c, rid, user):
    t = db.get(c, 'stock_txn', rid)
    if t['ttype'] in ('out', 'transfer') and t['lot']:
        check_lot_out(c, lot=t['lot'], what=_tr('出库') if t['ttype'] == 'out' else _tr('移库'))
    if t['ttype'] in ('out', 'transfer') and t['ref_doc']:
        p = db.get(c, 'part', sn=t['ref_doc'])
        if p:
            check_lot_out(c, sn=p['sn'])
    if _prev_txn_hook:
        _prev_txn_hook(c, rid, user)


HOOKS[('stock_txn', 'after_save')] = _txn_gate


@router.post('/api/warehouse/outbound')
def outbound(body: dict = Body(...), user=Depends(current_user)):
    """成品出库（按零件码）：{sns:[...], customer?}。冻结批次的零件整单拦下。"""
    require_write(user, 'stock_txn', 'create')
    from .warehouse import move
    sns = [s.strip() for s in body.get('sns') or [] if s and s.strip()]
    if not sns:
        raise HTTPException(400, _tr('请扫要出库的零件码'))
    with db.engine.begin() as c:
        parts = []
        for sn in sns:
            p = db.get(c, 'part', sn=sn)
            if not p:
                raise HTTPException(404, _tr('没有零件 {sn}', sn=sn))
            check_lot_out(c, sn=sn)
            if p['status'] not in ('fg', 'good'):
                raise Gate('status', _tr('零件 {sn} 当前状态不能出库（需检验合格或已入成品库）', sn=sn))
            parts.append(p)
        wh = db.get(c, 'warehouse', wtype='fg')
        pt = TABLES['part']
        now = clock.now()
        for p in parts:
            if wh and p['status'] == 'fg':
                move(c, 'out', p['product'], 1, wh['id'], ref=p['sn'], who=user['name'] or user['username'], reason=_tr('成品出库'), lot=p['heat'])
            c.execute(pt.update().where(pt.c.id == p['id']).values(status='shipped', finished_at=now, updated_at=now))
            c.execute(TABLES['part_event'].insert().values(sn=p['sn'], t=now, station=_tr('成品库'), event='shipped', data={'by': user['username']}, created_at=now, updated_at=now))
        db.insert(c, 'shipment', {'qty': len(parts), 'customer': body.get('customer') or _tr('客户'), 'work_order': parts[0]['work_order'], 'time': now}, raw=True)
    return {'ok': True, 'qty': len(parts)}


# ---------------- 返修复检 ----------------
@action('rework', 'finish')
def _rw_finish(c, r, body, user):
    if r['result'] != 'doing':
        raise HTTPException(400, _tr('这张返修单不在返修中'))
    db.update(c, 'rework', r['id'], {'finished_at': clock.now(), 'method': body.get('method') or r['method'], 'employee': r['employee'] or user.get('employee'),
                                     'remark': body.get('remark') or r['remark']}, raw=True)
    return {'msg': _tr('返修完成，等待复检')}


@action('rework', 'recheck')
def _rw_recheck(c, r, body, user):
    if r['result'] != 'doing':
        raise HTTPException(400, _tr('这张返修单已经有结果了'))
    if not r['finished_at']:
        raise HTTPException(409, _tr('返修还没完成（先点"返修完成"并写返修方法），不能复检'))
    res = 'OK' if str(body.get('result') or '').upper() in ('OK', '合格', 'TRUE') else 'NG' if body.get('result') else None
    if not res:
        raise HTTPException(400, _tr('请选择复检结论'))
    ncr = db.get(c, 'ncr', r['ncr']) if r['ncr'] else None
    now = clock.now()
    rid = db.insert(c, 'insp_record', {'itype': 'IPQC', 'work_order': ncr['work_order'] if ncr else None, 'sn': r['sn'], 'station': body.get('station') or r['station'],
                                       'qty_sample': 1, 'qty_ng': 0 if res == 'OK' else 1, 'result': res, 'inspector': user.get('employee'), 'time': now,
                                       'source': 'manual', 'remark': _tr('返修复检（{code}）', code=r['code']) + (_tr('：{remark}', remark=body['remark']) if body.get('remark') else '')}, raw=True)
    db.update(c, 'rework', r['id'], {'result': 'ok' if res == 'OK' else 'fail', 'recheck': rid, 'recheck_result': res,
                                     'recheck_by': user['name'] or user['username'], 'recheck_at': now}, raw=True)
    if ncr:
        if res == 'OK':
            db.update(c, 'ncr', ncr['id'], {'status': 'closed', 'closed_at': now, 'action': (ncr['action'] or '') + _tr(' 返修后复检合格')}, raw=True)
        else:
            db.update(c, 'ncr', ncr['id'], {'status': 'open', 'disposition': 'pending', 'action': (ncr['action'] or '') + _tr(' 返修后复检仍不合格，重新处置')}, raw=True)
    if r['sn']:
        pt = TABLES['part']
        if res == 'OK':
            c.execute(pt.update().where((pt.c.sn == r['sn']) & (pt.c.status == 'rework')).values(status='good', updated_at=now))
    return {'msg': _tr('复检合格，NCR 已关闭') if res == 'OK' else _tr('复检不合格，NCR 退回待处置'), 'record': rid}


# ---------------- 报工 ----------------
def _resolve_op(c, body):
    op = db.get(c, 'wo_op', int(body['wo_op'])) if body.get('wo_op') else None
    if not op and body.get('sn'):
        part = db.get(c, 'part', sn=str(body['sn']).strip())
        if not part:
            raise HTTPException(404, _tr('没有零件码 {sn}', sn=body['sn']))
        if not part['work_order']:
            raise HTTPException(400, _tr('零件 {sn} 没有关联工单', sn=body['sn']))
        ops = db.rows(c, 'wo_op', order=TABLES['wo_op'].c.seq, work_order=part['work_order'])
        op = next((o for o in ops if str(o['process']) == str(body.get('process'))), None) if body.get('process') else None
        op = op or next((o for o in ops if o['status'] != 'done'), None)
    if not op and body.get('work_order') and body.get('process'):
        op = db.get(c, 'wo_op', work_order=int(body['work_order']), process=int(body['process']))
    if not op:
        raise HTTPException(400, _tr('没找到对应的工单工序'))
    return op


def submit_report(c, body, user):
    """人工 / 扫码 / 工位终端报工。返回 {'id', 'duplicate', ...}；违反闸门抛 Gate(409)。调用方负责事务。"""
    key = (body.get('idem_key') or '').strip()[:120] or None
    rt = TABLES['report']
    if key:
        ex = db.get(c, 'report', idem_key=key)
        if ex:
            return {'id': ex['id'], 'code': ex['code'], 'duplicate': True, 'msg': _tr('这次报工已经提交过了（{code}），重复提交没有再计数', code=ex['code'])}
    # 冻结先查：冻结批次的零件不管有没有工单、工单什么状态，都应该明确说"冻结"（以前没关联工单的冻结件报的是"没有关联工单"）
    if body.get('sn'):
        check_lot_out(c, sn=str(body['sn']).strip(), what=_tr('报工'))
    op = _resolve_op(c, body)
    wo = db.get(c, 'work_order', op['work_order'])
    proc = db.get(c, 'process', op['process']) or {}
    pname = f"{op['seq']} {proc.get('name', '')}"
    if wo['status'] not in ('released', 'in_progress'):
        raise Gate('status', _tr('工单 {code} 当前是“{get}”，不能报工', code=wo['code'], get={'draft': _tr('草稿'), 'completed': _tr('已完工'), 'closed': _tr('已关闭'), 'cancelled': _tr('已取消')}.get(wo['status'], wo['status'])))
    try:
        g, n = int(body.get('qty_good') or 0), int(body.get('qty_ng') or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, _tr('数量要填整数'))
    if g < 0 or n < 0 or g + n <= 0:
        raise HTTPException(400, _tr('报工数量要大于 0'))
    # 跳序 / 超过上道转来的数量
    prev = db.rows(c, 'wo_op', TABLES['wo_op'].c.seq < op['seq'], order=TABLES['wo_op'].c.seq.desc(), limit=1, work_order=op['work_order'])
    done_here = (op['qty_good'] or 0) + (op['qty_ng'] or 0)
    if prev:
        p = prev[0]
        pp = db.get(c, 'process', p['process']) or {}
        pgood = p['qty_good'] or 0
        if pgood <= 0:
            raise Gate('sequence', _tr('上道工序 {seq} {get} 还没有合格品转过来，不能跳序报 {pname}', seq=p['seq'], get=pp.get('name', ''), pname=pname), prev_op=p['id'])
        if done_here + g + n > pgood:
            left = max(0, pgood - done_here)
            raise Gate('sequence', _tr('超过上道转来的数量：上道 {seq} {get} 合格 {pgood} 件，{pname} 已报 {done_here} 件，本次 {v} 件，最多还能报 {left} 件', seq=p['seq'], get=pp.get('name', ''), pgood=pgood, pname=pname, done_here=done_here, v=g + n, left=left), max=left)
    # 超计划
    over = float(db.setting(c, 'report_over_pct', 0) or 0)
    limit = int((op['qty_plan'] or 0) * (1 + over / 100))
    if (op['qty_good'] or 0) + g > limit:
        left = max(0, limit - (op['qty_good'] or 0))
        raise Gate('over', _tr('超出计划数量：{code} {pname} 计划 {qty_plan} 件，已报合格 {v} 件，本次 {g} 件，最多还能报 {left} 件', code=wo['code'], pname=pname, qty_plan=op['qty_plan'], v=op['qty_good'] or 0, g=g, left=left), max=left)
    # 首检
    check_fai(c, op)
    for g_ in REPORT_GATES:          # 其它模块的报工闸门（如 ECN 停工：services/exec_doc.report_gate）
        g_(c, wo, op, body)
    # 落库：先占住幂等键
    now = clock.now()
    src = body.get('source') if body.get('source') in SOURCES else ('mobile' if body.get('sn') or body.get('mobile') else 'manual')
    eq = int(body['equipment']) if body.get('equipment') else None
    rid = db.insert(c, 'report', {'work_order': op['work_order'], 'wo_op': op['id'], 'process': op['process'], 'employee': body.get('employee') or user.get('employee'),
                                  'equipment': eq, 'qty_good': g, 'qty_ng': n, 'defect': body.get('defect') or None, 'start': now, 'end': now, 'source': src,
                                  'remark': body.get('remark'), 'idem_key': key, 'shift': current_shift_id(c, now)}, raw=True)
    opt = TABLES['wo_op']
    c.execute(opt.update().where(opt.c.id == op['id']).values(qty_good=sa.func.coalesce(opt.c.qty_good, 0) + g, qty_ng=sa.func.coalesce(opt.c.qty_ng, 0) + n,
                                                              act_start=sa.func.coalesce(opt.c.act_start, now), status='running', updated_at=now))
    op2 = db.get(c, 'wo_op', op['id'])
    if (op2['qty_good'] or 0) >= (op2['qty_plan'] or 0):
        c.execute(opt.update().where(opt.c.id == op['id']).values(status='done', act_end=now))
    nxt = db.rows(c, 'wo_op', opt.c.seq > op['seq'], order=opt.c.seq, limit=1, work_order=op['work_order'])
    if nxt and nxt[0]['status'] == 'waiting':
        db.update(c, 'wo_op', nxt[0]['id'], {'status': 'ready'}, raw=True)
    if wo['status'] == 'released':
        db.update(c, 'work_order', wo['id'], {'status': 'in_progress'}, raw=True)
    if n:
        db.insert(c, 'ncr', {'sn': body.get('sn'), 'work_order': wo['id'], 'material': wo['product'], 'qty': n, 'defect': body.get('defect') or None,
                             'station': body.get('station'), 'found_at': now, 'found_by': user['name'] or user['username'], 'source': 'manual',
                             'disposition': 'pending', 'status': 'open'}, raw=True)
    r = db.get(c, 'report', rid)
    return {'id': rid, 'code': r['code'], 'duplicate': False, 'msg': _tr('已报工 {code}：{code2} {pname} 合格 {g}', code=r['code'], code2=wo['code'], pname=pname, g=g) + (_tr('，不良 {n}', n=n) if n else '')}


def current_shift_id(c, now):
    from datetime import time as _t
    for s in db.rows(c, 'shift'):
        try:
            a = _t(*map(int, (s['start'] or '0:0').split(':')[:2]))
            b = _t(*map(int, (s['end'] or '0:0').split(':')[:2]))
        except ValueError:
            continue
        t = now.time()
        if (a <= t < b) if a < b else (t >= a or t < b):
            return s['id']
    return None


def report_endpoint(body, user):
    require_write(user, 'report', 'create')
    with _lock:
        try:
            with db.engine.begin() as c:
                return {'ok': True, **submit_report(c, body, user)}
        except sa.exc.IntegrityError:
            # 并发重复提交（同一幂等键）：返回已经落库的那一条
            with db.engine.connect() as c:
                ex = db.get(c, 'report', idem_key=(body.get('idem_key') or '').strip()[:120])
            if ex:
                return {'ok': True, 'id': ex['id'], 'code': ex['code'], 'duplicate': True, 'msg': _tr('重复提交，已忽略')}
            raise


@router.post('/api/production/report/check')
def report_check(body: dict = Body(...), user=Depends(current_user)):
    """报工前预检（不落库）：返回能不能报、最多还能报几件、首检状态。"""
    with db.engine.connect() as c:
        op = _resolve_op(c, body)
        wo = db.get(c, 'work_order', op['work_order'])
        prev = db.rows(c, 'wo_op', TABLES['wo_op'].c.seq < op['seq'], order=TABLES['wo_op'].c.seq.desc(), limit=1, work_order=op['work_order'])
        done_here = (op['qty_good'] or 0) + (op['qty_ng'] or 0)
        max_seq = (max(0, (prev[0]['qty_good'] or 0) - done_here)) if prev else None
        max_plan = max(0, (op['qty_plan'] or 0) - (op['qty_good'] or 0))
        need = fai_required(c, op)
        ok, last = fai_status(c, op) if need else (True, None)
        p = db.get(c, 'part', sn=body['sn']) if body.get('sn') else None
        hold = lot_held(c, p['heat']) if p and p['status'] == 'hold' else None
    blockers = []
    if wo['status'] not in ('released', 'in_progress'):
        blockers.append(_tr('工单状态不能报工'))
    if max_seq == 0:
        blockers.append(_tr('上道工序没有可转入的数量'))
    if need and not ok:
        blockers.append(_tr('首检未放行'))
    if hold:
        blockers.append(_tr('批次 {lot} 已冻结', lot=hold['lot']))
    return {'wo_op': op['id'], 'work_order': wo['code'], 'max': min(x for x in (max_seq, max_plan) if x is not None), 'max_plan': max_plan, 'max_seq': max_seq,
            'fai_required': need, 'fai_ok': ok, 'fai': out([last])[0] if last else None, 'hold': out([hold])[0] if hold else None, 'blockers': blockers}
