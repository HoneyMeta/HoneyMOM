"""委外加工（热处理、涂装、电镀…）与备件。

委外单：发出（库存从发出仓移到"委外仓 OS-<供应商>"，零件码记履历）→ 收回检验（合格回库、报废登记不合格品、丢失按允许损耗扣款）→
结算关闭（加工费 = 单价 × 合格数 − 超损耗扣款；转应付：内置 ERP 在时调 erp.post_ap，否则留在 /api/outsource/ap 待 ERP 取）。
工艺步骤勾"委外"（route_step.outsourced / supplier / os_lead_days）：工单下达时自动建委外单草稿；排产按供应商周期排这道工序（scheduling.compute）。

备件：备件台账（最低 / 最高库存）、出入库（领用到维修工单 / 保养记录，按设备记使用历史）、补货建议（低于最低库存 → 补到最高；
推给内置 ERP 的请购：erp.create_purchase_request，没有时发站内信给计划员）。
"""
from ..i18n import _tr
from datetime import timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db, perms
from ..crud import HOOKS, action, attach_labels, out
from ..db import clock
from ..schema import TABLES
from .gates import check_lot_out

router = APIRouter()


# ───────────────────────── 委外 ─────────────────────────
def os_warehouse(c, supplier):
    code = 'OS-' + ''.join(ch for ch in supplier if ch.isalnum())[:20].upper()
    w = db.get(c, 'warehouse', code=code)
    if w:
        return w['id']
    return db.insert(c, 'warehouse', {'code': code, 'name': _tr('委外仓（{supplier}）', supplier=supplier), 'wtype': 'outsource'}, raw=True)


def _src_wh(c, o):
    if o['warehouse']:
        return o['warehouse']
    m = db.get(c, 'material', o['material'])
    for wt in (('fg',) if m and m['mtype'] == 'fg' else ('blank', 'wip', 'raw')):
        w = db.get(c, 'warehouse', wtype=wt)
        if w:
            return w['id']
    w = db.rows(c, 'warehouse', TABLES['warehouse'].c.wtype != 'outsource', limit=1)
    return w[0]['id'] if w else None


@action('outsource_order', 'send')
def _os_send(c, o, body, user):
    """发出：{qty? | lines:[{sn | lot, qty}]}。"""
    from .warehouse import move
    if o['status'] not in ('draft', 'sent', 'partial'):
        raise HTTPException(400, _tr('这张委外单已经结束'))
    lines = body.get('lines') or [{'qty': body.get('qty') or (o['qty'] - (o['qty_sent'] or 0)), 'lot': body.get('lot')}]
    now = clock.now()
    who = user.get('name') or user['username']
    src, dst = _src_wh(c, o), os_warehouse(c, o['supplier'])
    n = 0
    pt, pe = TABLES['part'], TABLES['part_event']
    for ln in lines:
        sn = (ln.get('sn') or '').strip() or None
        q = 1 if sn else float(ln.get('qty') or 0)
        if q <= 0:
            continue
        if sn:
            check_lot_out(c, sn=sn, what=_tr('委外发出'))
            p = db.get(c, 'part', sn=sn)
            if not p:
                raise HTTPException(404, _tr('没有零件 {sn}', sn=sn))
            lot = p['heat']
            c.execute(pt.update().where(pt.c.id == p['id']).values(station=_tr('委外:{supplier}', supplier=o['supplier']), updated_at=now))
            c.execute(pe.insert().values(sn=sn, t=now, station=_tr('委外:{supplier}', supplier=o['supplier']), event='outsource_out', data={'order': o['code']}, created_at=now, updated_at=now))
        else:
            lot = ln.get('lot') or None
            if lot:
                check_lot_out(c, lot=lot, what=_tr('委外发出'))
        if src:
            move(c, 'transfer', o['material'], q, src, to_wh=dst, lot=lot, ref=o['code'], who=who, reason=_tr('委外发出 {supplier}', supplier=o['supplier']))
        db.insert(c, 'outsource_line', {'order': o['id'], 'direction': 'out', 'sn': sn, 'lot': lot, 'qty': q, 'by': who, 'time': now}, raw=True)
        n += q
    if not n:
        raise HTTPException(400, _tr('发出数量要大于 0'))
    sent = (o['qty_sent'] or 0) + n
    if sent > (o['qty'] or 0) * 1.0001:
        raise HTTPException(409, _tr('发出 {sent:g} 超过委外数量 {qty:g}', sent=sent, qty=o['qty']))
    vals = {'qty_sent': sent, 'status': 'sent' if o['status'] == 'draft' else o['status'], 'os_warehouse': dst, 'sent_at': o['sent_at'] or now}
    if not o['due']:
        vals['due'] = now + timedelta(days=o['lead_days'] or 3)
    db.update(c, 'outsource_order', o['id'], vals, raw=True)
    from .exec_label import fire
    fire(c, 'outsource_send', 'lot', (lines[0].get('lot') or o['code']), user, material=o['material'])
    return {'msg': _tr('已发出 {n:g}（累计 {sent:g}/{qty:g}），要求 {jsonable} 回厂', n=n, sent=sent, qty=o['qty'], jsonable=db.jsonable(vals.get('due') or o['due']))}


@action('outsource_order', 'receive')
def _os_receive(c, o, body, user):
    """收回检验：{qty_ok, qty_scrap?, qty_lost?, lines?:[{sn, result: OK|NG}], remark?}。合格回发出仓，报废登记不合格品。"""
    from .warehouse import move
    if o['status'] not in ('sent', 'partial'):
        raise HTTPException(400, _tr('还没发出，或者已经收完了'))
    now = clock.now()
    who = user.get('name') or user['username']
    lines = body.get('lines') or []
    pt, pe = TABLES['part'], TABLES['part_event']
    if lines:
        ok = sum(1 for x in lines if str(x.get('result') or 'OK').upper() == 'OK')
        scrap = len(lines) - ok
    else:
        ok, scrap = float(body.get('qty_ok') or 0), float(body.get('qty_scrap') or 0)
    lost = float(body.get('qty_lost') or 0)
    total = ok + scrap + lost
    if total <= 0:
        raise HTTPException(400, _tr('请填收回数量（合格 / 报废 / 丢失）'))
    out_left = (o['qty_sent'] or 0) - (o['qty_back'] or 0)
    if total > out_left + 1e-9:
        raise HTTPException(409, _tr('收回 {total:g} 超过在外数量 {out_left:g}', total=total, out_left=out_left))
    src, dst = o['os_warehouse'] or os_warehouse(c, o['supplier']), _src_wh(c, o)
    rec = db.insert(c, 'insp_record', {'itype': 'IQC', 'work_order': o['work_order'], 'lot': o['code'], 'station': _tr('委外收回 {supplier}', supplier=o['supplier']),
                                       'qty_sample': int(ok + scrap), 'qty_ng': int(scrap), 'result': 'NG' if scrap else 'OK', 'inspector': user.get('employee'),
                                       'time': now, 'source': 'manual', 'remark': _tr('委外收回检验 {code}', code=o['code']) + (_tr('：{remark}', remark=body['remark']) if body.get('remark') else '')}, raw=True)
    lot_of = {}
    olt = TABLES['outsource_line']
    for ln in db.rows(c, 'outsource_line', olt.c.order == o['id'], olt.c.direction == 'out'):     # 'order' 是字段名，不能用 db.rows 的关键字过滤
        if ln['sn']:
            lot_of[ln['sn']] = ln['lot']
    for x in lines:
        sn = x.get('sn')
        good = str(x.get('result') or 'OK').upper() == 'OK'
        p = db.get(c, 'part', sn=sn) if sn else None
        if p:
            c.execute(pt.update().where(pt.c.id == p['id']).values(station=None if good else p['station'], status=p['status'] if good else 'scrap',
                                                                   scrap_cause=None if good else _tr('委外{supplier}不合格', supplier=o['supplier']), updated_at=now))
            c.execute(pe.insert().values(sn=sn, t=now, station=_tr('委外:{supplier}', supplier=o['supplier']), event='outsource_back' if good else 'scrap',
                                         data={'order': o['code'], 'result': 'OK' if good else 'NG'}, created_at=now, updated_at=now))
        db.insert(c, 'outsource_line', {'order': o['id'], 'direction': 'back', 'sn': sn, 'lot': lot_of.get(sn), 'qty': 1, 'qty_ok': 1 if good else 0,
                                        'qty_scrap': 0 if good else 1, 'result': 'OK' if good else 'NG', 'record': rec, 'by': who, 'time': now}, raw=True)
    if not lines:
        db.insert(c, 'outsource_line', {'order': o['id'], 'direction': 'back', 'qty': total, 'qty_ok': ok, 'qty_scrap': scrap, 'qty_lost': lost,
                                        'result': 'OK' if not scrap and not lost else ('NG' if not ok else 'partial'), 'record': rec, 'by': who, 'time': now,
                                        'remark': body.get('remark')}, raw=True)
    if ok:
        move(c, 'transfer', o['material'], ok, src, to_wh=dst, ref=o['code'], who=who, reason=_tr('委外收回 {supplier}', supplier=o['supplier']))
    if scrap + lost:
        move(c, 'out', o['material'], scrap + lost, src, ref=o['code'], who=who, reason=_tr('委外报废 {scrap:g} / 丢失 {lost:g}', scrap=scrap, lost=lost))
    if scrap:
        db.insert(c, 'ncr', {'lot': o['code'], 'work_order': o['work_order'], 'material': o['material'], 'qty': int(scrap), 'station': _tr('委外 {supplier}', supplier=o['supplier']),
                             'found_at': now, 'found_by': who, 'source': 'manual', 'disposition': 'return', 'status': 'open',
                             'root_cause': _tr('委外加工（{supplier}）收回检验不合格', supplier=o['supplier'])}, raw=True)
    back = (o['qty_back'] or 0) + total
    vals = {'qty_back': back, 'qty_ok': (o['qty_ok'] or 0) + ok, 'qty_scrap': (o['qty_scrap'] or 0) + scrap, 'qty_lost': (o['qty_lost'] or 0) + lost,
            'status': 'received' if back >= (o['qty'] or 0) - 1e-9 else 'partial', 'received_at': now}
    vals['cost'] = cost_of({**o, **vals}, c)['amount']
    db.update(c, 'outsource_order', o['id'], vals, raw=True)
    return {'msg': _tr('收回 {total:g}：合格 {ok:g}、报废 {scrap:g}、丢失 {lost:g}；加工费 {cost:.2f} 元', total=total, ok=ok, scrap=scrap, lost=lost, cost=vals['cost']), 'record': rec}


def cost_of(o, c=None) -> dict:
    """委外加工费：单价 × 合格数；超过允许损耗的报废 + 丢失按物料单价扣款。"""
    price = o['unit_price'] or 0
    ok, scrap, lost = o['qty_ok'] or 0, o['qty_scrap'] or 0, o['qty_lost'] or 0
    allowed = (o['qty_sent'] or 0) * (o['loss_pct'] or 0) / 100
    over = max(0.0, scrap + lost - allowed)
    mprice = 0
    if c is not None and o.get('material'):
        m = db.get(c, 'material', o['material'])
        mprice = (m or {}).get('price') or 0
    fee = price * ok
    deduct = over * mprice
    return {'fee': round(fee, 2), 'allowed_loss': round(allowed, 3), 'over_loss': round(over, 3), 'deduct': round(deduct, 2), 'amount': round(fee - deduct, 2)}


@action('outsource_order', 'close')
def _os_close(c, o, body, user):
    if o['status'] not in ('received', 'partial') and not body.get('force'):
        raise HTTPException(400, _tr('还没收回，不能结算'))
    co = cost_of(o, c)
    db.update(c, 'outsource_order', o['id'], {'status': 'closed', 'cost': co['amount'], 'ap_posted': False}, raw=True)
    db.insert(c, 'notice', {'title': _tr('委外结算 {code}（{supplier}）{amount:.2f} 元', code=o['code'], supplier=o['supplier'], amount=co['amount']), 'content': _tr('加工费 {fee:.2f} − 超损耗扣款 {deduct:.2f}；供应商开票后在财务"采购发票"登记，登记后回写应付单号（/api/outsource/ap/ack）。', fee=co['fee'], deduct=co['deduct']), 'to_role': 'manager', 'channel': 'inbox', 'level': 'info',
                            'link': '/exec/outsource', 'source': '委外', 'read': False, 'time': clock.now()}, raw=True)
    return {'msg': _tr('已结算：加工费 {fee:.2f} − 扣款 {deduct:.2f} = {amount:.2f} 元，进应付待登记', fee=co['fee'], deduct=co['deduct'], amount=co['amount']), **co}


def _after_os(c, rid, user):
    """委外单：供应商对上主数据（填编码或名称都行）。"""
    from .exec_erp import supplier_id
    o = db.get(c, 'outsource_order', rid)
    sid = supplier_id(c, o['supplier'])
    if sid and sid != o['supplier_id']:
        db.update(c, 'outsource_order', rid, {'supplier_id': sid}, raw=True)


HOOKS[('outsource_order', 'after_save')] = _after_os


@action('outsource_order', 'cancel')
def _os_cancel(c, o, body, user):
    if (o['qty_sent'] or 0) > (o['qty_back'] or 0):
        raise HTTPException(409, _tr('还有货在供应商处，先收回再取消'))
    db.update(c, 'outsource_order', o['id'], {'status': 'cancelled'}, raw=True)
    return {'msg': _tr('已取消')}


@router.get('/api/outsource/summary')
def os_summary(user=Depends(perms.require('outsource.outsource_order.read'))):
    t = TABLES['outsource_order']
    now = clock.now()
    with db.engine.connect() as c:
        rows = attach_labels(c, 'outsource_order', db.rows(c, 'outsource_order', t.c.status != 'cancelled', order=t.c.id.desc(), limit=300))
        for r in rows:
            r['out_qty'] = (r['qty_sent'] or 0) - (r['qty_back'] or 0)
            r['overdue'] = bool(r['due'] and r['due'] < now and r['status'] in ('sent', 'partial'))
            r['cost_detail'] = cost_of(r, c)
        sup = {}
        for r in rows:
            s = sup.setdefault(r['supplier'], {'supplier': r['supplier'], 'orders': 0, 'out_qty': 0, 'ok': 0, 'scrap': 0, 'lost': 0, 'amount': 0.0, 'overdue': 0})
            s['orders'] += 1
            s['out_qty'] += r['out_qty']
            s['ok'] += r['qty_ok'] or 0
            s['scrap'] += r['qty_scrap'] or 0
            s['lost'] += r['qty_lost'] or 0
            s['amount'] += r['cost'] or 0
            s['overdue'] += 1 if r['overdue'] else 0
    for s in sup.values():
        tot = s['ok'] + s['scrap'] + s['lost']
        s['yield'] = round(s['ok'] / tot, 4) if tot else None
    return {'orders': out(rows), 'suppliers': list(sup.values())}


@router.get('/api/outsource/ap')
def os_ap(posted: int = 0, user=Depends(perms.require('outsource.outsource_order.read'))):
    """给内置 ERP 的应付来源：已结算的委外单（posted=0 只给还没转应付的）。"""
    t = TABLES['outsource_order']
    with db.engine.connect() as c:
        rows = db.rows(c, 'outsource_order', t.c.status == 'closed', *([] if posted else [t.c.ap_posted.isnot(True)]), order=t.c.id)
        res = [{'ref': r['code'], 'supplier': r['supplier'], 'supplier_id': r['supplier_id'], 'amount': r['cost'], 'qty_ok': r['qty_ok'], 'unit_price': r['unit_price'],
                **cost_of(r, c), 'closed_at': db.jsonable(r['updated_at']), 'ap_ref': r['ap_ref']} for r in rows]
    return res


@router.post('/api/outsource/ap/ack')
def os_ap_ack(body: dict = Body(...), user=Depends(perms.require('outsource.outsource_order.update'))):
    """ERP 取走应付后回写应付单号：{items:[{ref, ap_ref}]}。"""
    n = 0
    with db.engine.begin() as c:
        for it in body.get('items') or []:
            o = db.get(c, 'outsource_order', code=it.get('ref'))
            if o:
                db.update(c, 'outsource_order', o['id'], {'ap_posted': True, 'ap_ref': (it.get('ap_ref') or '')[:60]}, raw=True)
                n += 1
    return {'ok': True, 'updated': n}


def os_steps(c) -> dict:
    """{(工艺路线, 顺序): 步骤}：勾了委外的工艺步骤（排产 / 工单下达用）。"""
    st = TABLES['route_step']
    return {(s['route'], s['seq']): s for s in db.rows(c, 'route_step', st.c.outsourced.is_(True))}


def on_wo_released(c, wo, user):
    """工单下达：委外工序自动建委外单草稿（供应商、周期取工艺步骤）。"""
    w = db.get(c, 'work_order', wo['id'])
    if not w['route']:
        return []
    ids = []
    for (rid, seq), s in os_steps(c).items():
        if rid != w['route']:
            continue
        if db.get(c, 'outsource_order', work_order=w['id'], process=s['process']):
            continue
        prod = w['product']
        bt, mt = TABLES['bom'], TABLES['material']
        semi = c.execute(sa.select(bt.c.component).join(mt, mt.c.id == bt.c.component).where((bt.c.product == prod) & (mt.c.mtype == 'semi'))).scalar()
        proc_seqs = sorted(x['seq'] for x in db.rows(c, 'route_step', route=w['route']))
        mat = semi if semi and seq < proc_seqs[len(proc_seqs) // 2] else prod
        ids.append(db.insert(c, 'outsource_order', {'supplier': s['supplier'] or _tr('待定供应商'), 'process': s['process'], 'work_order': w['id'], 'material': mat,
                                                    'qty': w['qty'], 'lead_days': s['os_lead_days'] or 3, 'status': 'draft',
                                                    'remark': _tr('工单 {code} 下达自动生成（工艺步骤 {seq}）', code=w['code'], seq=seq)}, raw=True))
    return ids


def plan_outsourced(step, prev_s, prev_e, now, remain, ct):
    """排产：委外工序按供应商周期（日历天，不占设备、不看班次）——上道开始后批量送出，周期后回厂。返回 (start, end, 冲突说明)。"""
    lead = timedelta(days=float(step.get('os_lead_days') or 3))
    start = max(prev_s + timedelta(seconds=ct * 2), now)
    end = max(start + lead, prev_e + lead)
    return start, end, {'type': 'outsource', 'text': _tr('委外 {v}：周期 {v2:g} 天，预计 {end:%m-%d} 回厂', v=step.get('supplier') or _tr('供应商'), v2=lead.days or float(step.get('os_lead_days') or 3), end=end)}


# ───────────────────────── 备件 ─────────────────────────
def _after_spare_txn(c, rid, user):
    t = db.get(c, 'spare_txn', rid)
    if t['balance'] is not None:        # 已过账（修改单据不重复过账）
        return
    sp = db.get(c, 'spare_part', t['spare'])
    q = t['qty'] or 0
    delta = {'in': q, 'return': q, 'issue': -q, 'adjust': q}.get(t['kind'], 0)
    if t['kind'] in ('in', 'issue', 'return') and q <= 0:
        raise HTTPException(400, _tr('数量要大于 0'))
    bal = (sp['qty'] or 0) + delta
    if bal < -1e-9:
        raise HTTPException(409, _tr('备件 {code} {name} 库存只有 {v:g}{v2}，不够领用 {q:g}', code=sp['code'], name=sp['name'], v=sp['qty'] or 0, v2=sp['unit'] or '', q=q))
    vals = {'balance': bal, 'time': t['time'] or clock.now(), 'by': t['by'] or (user or {}).get('name') or (user or {}).get('username'), 'price': t['price'] or sp['price']}
    if t['repair_order'] and not t['equipment']:
        ro = db.get(c, 'repair_order', t['repair_order'])
        vals['equipment'] = ro['equipment'] if ro else None
    if t['maint_record'] and not t['equipment']:
        mr = db.get(c, 'maint_record', t['maint_record'])
        vals['equipment'] = mr['equipment'] if mr else None
    db.update(c, 'spare_txn', rid, vals, raw=True)
    db.update(c, 'spare_part', sp['id'], {'qty': bal}, raw=True)
    if t['kind'] == 'issue' and t['repair_order']:
        ro = db.get(c, 'repair_order', t['repair_order'])
        if ro:
            used = ((ro['parts_used'] or '') + _tr('；{code} {name}×{q:g}', code=sp['code'], name=sp['name'], q=q)).strip('；')[:190]
            db.update(c, 'repair_order', ro['id'], {'parts_used': used}, raw=True)


HOOKS[('spare_txn', 'after_save')] = _after_spare_txn


@router.post('/api/spare/issue')
def spare_issue(body: dict = Body(...), user=Depends(perms.require('spare.spare_txn.create'))):
    """领用：{spare(编码或 id), qty, repair_order?, maint_record?, equipment?, remark?}"""
    with db.engine.begin() as c:
        key = body.get('spare')
        sp = db.get(c, 'spare_part', int(key)) if str(key).isdigit() else db.get(c, 'spare_part', code=str(key))
        if not sp:
            raise HTTPException(404, _tr('没有备件 {key}', key=key))
        rid = db.insert(c, 'spare_txn', {'spare': sp['id'], 'kind': body.get('kind') or 'issue', 'qty': float(body.get('qty') or 1), 'repair_order': body.get('repair_order'),
                                         'maint_record': body.get('maint_record'), 'equipment': body.get('equipment'), 'remark': body.get('remark'),
                                         'by': user.get('name') or user['username'], 'time': clock.now()}, raw=False)
        _after_spare_txn(c, rid, user)
        t = db.get(c, 'spare_txn', rid)
    return {'ok': True, 'id': rid, 'code': t['code'], 'balance': t['balance'], 'msg': _tr('已{v} {name} ×{qty:g}，结存 {balance:g}', v={'issue': _tr('领用'), 'in': _tr('入库'), 'return': _tr('退回'), 'adjust': _tr('调整')}[t['kind']], name=sp['name'], qty=t['qty'], balance=t['balance'])}


def reorder_list(c):
    t, tt = TABLES['spare_part'], TABLES['spare_txn']
    since = clock.now() - timedelta(days=90)
    use = dict(c.execute(sa.select(tt.c.spare, sa.func.sum(tt.c.qty)).where((tt.c.kind == 'issue') & (tt.c.time >= since)).group_by(tt.c.spare)).all())
    res = []
    for s in db.rows(c, 'spare_part', order=t.c.code):
        mn, mx, q = s['min_qty'], s['max_qty'], s['qty'] or 0
        if mn is None or q > mn:
            continue
        target = mx if mx and mx > mn else mn * 2 or 1
        sug = max(0.0, target - q)
        if sug <= 0:
            continue
        res.append({'id': s['id'], 'code': s['code'], 'name': s['name'], 'spec': s['spec'], 'unit': s['unit'], 'qty': q, 'min_qty': mn, 'max_qty': mx,
                    'suggest': sug, 'price': s['price'], 'amount': round(sug * (s['price'] or 0), 2), 'supplier': s['supplier'], 'lead_days': s['lead_days'],
                    'use_90d': float(use.get(s['id']) or 0), 'critical': bool(s['critical'])})
    res.sort(key=lambda x: (not x['critical'], x['qty'] - (x['min_qty'] or 0)))
    return res


@router.get('/api/spare/reorder')
def spare_reorder(user=Depends(perms.require('spare.spare_part.read'))):
    with db.engine.connect() as c:
        return reorder_list(c)


@router.post('/api/spare/reorder/push')
def spare_reorder_push(body: dict = Body(default={}), user=Depends(perms.require('spare.spare_part.update'))):
    """补货建议 → 内置 ERP 请购（erp.create_purchase_request）；没有内置 ERP 时发站内信给计划员。{ids?: [备件 id]}（空 = 全部建议）"""
    with db.engine.begin() as c:
        sug = reorder_list(c)
        ids = set(body.get('ids') or [x['id'] for x in sug])
        lines = [x for x in sug if x['id'] in ids]
        if not lines:
            return {'ok': True, 'lines': 0, 'msg': _tr('没有需要补货的备件')}
        from .exec_erp import purchase_requests
        codes = purchase_requests(c, lines, user)          # 内置 ERP：请购单（来源 = 备件）
        ref = _tr('、').join(codes) if codes else None
        if not ref:
            db.insert(c, 'notice', {'title': _tr('备件补货建议 {len} 项（约 {sum:.0f} 元）', len=len(lines), sum=sum(x['amount'] for x in lines)),
                                    'content': '\n'.join(_tr('{code} {name}：库存 {qty:g}，最低 {min_qty:g}，建议采购 {suggest:g}{v}', code=x['code'], name=x['name'], qty=x['qty'], min_qty=x['min_qty'], suggest=x['suggest'], v=x['unit'] or '') for x in lines),
                                    'to_role': 'planner', 'channel': 'inbox', 'level': 'warn', 'link': '/exec/spares', 'source': '备件', 'read': False, 'time': clock.now()}, raw=True)
    return {'ok': True, 'lines': len(lines), 'ref': ref, 'msg': _tr('已生成请购单 {ref}', ref=ref) if ref else _tr('已通知计划员采购 {len} 项', len=len(lines))}


@router.get('/api/spare/history')
def spare_history(equipment: int = 0, spare: int = 0, days: int = 365, user=Depends(perms.require('spare.spare_txn.read'))):
    """使用历史：按设备 / 备件的领用记录和汇总。"""
    tt = TABLES['spare_txn']
    cond = [tt.c.kind == 'issue', tt.c.time >= clock.now() - timedelta(days=days)]
    if equipment:
        cond.append(tt.c.equipment == equipment)
    if spare:
        cond.append(tt.c.spare == spare)
    with db.engine.connect() as c:
        rows = attach_labels(c, 'spare_txn', db.rows(c, 'spare_txn', *cond, order=tt.c.time.desc(), limit=500))
        by = {}
        for r in rows:
            k = (r.get('equipment__label') or _tr('未指定设备'), r.get('spare__label'))
            b = by.setdefault(k, {'equipment': k[0], 'spare': k[1], 'times': 0, 'qty': 0.0, 'amount': 0.0, 'last': None})
            b['times'] += 1
            b['qty'] += r['qty'] or 0
            b['amount'] += (r['qty'] or 0) * (r['price'] or 0)
            b['last'] = b['last'] or db.jsonable(r['time'])
    return {'items': out(rows), 'summary': sorted(by.values(), key=lambda x: -x['amount'])}
