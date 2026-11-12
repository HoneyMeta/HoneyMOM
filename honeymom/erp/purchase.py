"""采购：请购 → 采购订单（合格供方检查）→ 收货（开来料检验单，用现有检验模块）→ 按检验结果入库（合格入库、不合格转退供应商 + 不合格品单）
→ 供应商对账单；供应商绩效（准时到货率、来料 ppm）。"""
from ..i18n import _tr
from collections import defaultdict
from datetime import timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db, perms
from ..crud import action, attach_labels, out
from ..db import clock
from ..perms import require
from ..schema import TABLES
from . import core
from .core import as_date, check_open, money, today, who

router = APIRouter()
perms.bind_scope('purchase_order', 'plant', 'plant')
perms.bind_scope('purchase_receipt', 'plant', 'plant')
perms.bind_scope('po_line', 'purchase_order', 'purchase_order')


def asl_check(c, supplier, material):
    """合格供方：供应商没停用；ASL 严格模式（系统参数 erp_asl_strict，默认 1）下物料要在这个供应商的供货清单里且标了合格。"""
    s = db.get(c, 'supplier', supplier)
    if not s:
        raise HTTPException(400, _tr('供应商不存在'))
    if s.get('qual') == 'blocked' or s.get('active') is False:
        raise HTTPException(409, _tr('供应商 {code} {name} 已停用，不能下采购订单', code=s['code'], name=s['name']), headers={'X-Gate': 'asl'})
    if str(core.setting(c, 'erp_asl_strict', '1')) != '1':
        return None
    it = core.supplier_item(c, supplier, material)
    m = db.get(c, 'material', material) or {}
    if not it or not it.get('approved'):
        raise HTTPException(409, _tr('{get} {get2} 不在 {code} {name} 的合格供方清单（ASL）里；先在「供货清单」里登记并标合格', get=m.get('code', ''), get2=m.get('name', ''), code=s['code'], name=s['name']),
                            headers={'X-Gate': 'asl'})
    return it


def _before_po(c, h, lines, user, new):
    core.check_owned('purchase_order', user, c)
    if not h.get('plant'):
        p = db.rows(c, 'plant', limit=1)
        h['plant'] = p[0]['id'] if p else None
    for ln in lines:
        if not ln.get('material') or (ln.get('qty') or 0) <= 0:
            raise HTTPException(400, _tr('采购明细要有物料和大于 0 的数量'))
        it = asl_check(c, h['supplier'], ln['material'])
        if ln.get('price') is None:
            p, tr, _ = core.purchase_price(c, h['supplier'], ln['material'])
            ln['price'] = p
            if tr is not None and ln.get('tax_rate') is None:
                ln['tax_rate'] = tr
        if not ln.get('due'):
            lead = int((it or {}).get('lead_days') or 7)
            ln['due'] = h.get('due') or (as_date(h.get('order_date')) or today()) + timedelta(days=lead)
        if it and it.get('moq') and ln['qty'] < it['moq']:
            raise HTTPException(409, _tr('低于最小订量 {moq:g}', moq=it['moq']))
    if lines and not h.get('due'):
        h['due'] = max(as_date(ln['due']) for ln in lines)


def _before_receipt(c, h, lines, user, new):
    po = db.get(c, 'purchase_order', h['purchase_order']) if h.get('purchase_order') else None
    if po:
        if po['status'] not in ('approved', 'sent', 'partial'):
            raise HTTPException(409, _tr('采购订单 {code} 是“{e_status}”，不能收货', code=po['code'], e_status=core.e_status('purchase_order', po)))
        h['supplier'] = po['supplier']
        h['plant'] = h.get('plant') or po['plant']
    for ln in lines:
        pl = db.get(c, 'po_line', ln['po_line']) if ln.get('po_line') else None
        if pl:
            ln['material'] = ln.get('material') or pl['material']
            ln['price'] = pl['price'] if ln.get('price') is None else ln['price']
            ln['tax_rate'] = pl['tax_rate'] if ln.get('tax_rate') is None else ln['tax_rate']
        elif ln.get('price') is None and ln.get('material'):
            ln['price'] = core.purchase_price(c, h['supplier'], ln['material'])[0]
        if (ln.get('qty') or 0) <= 0:
            raise HTTPException(400, _tr('到货数量要大于 0'))
    if not h.get('warehouse') and lines:
        m = db.get(c, 'material', lines[0]['material']) or {}
        h['warehouse'] = core.wh_of_type(c, {'raw': 'raw', 'aux': 'aux', 'consumable': 'aux', 'semi': 'blank', 'fg': 'fg'}.get(m.get('mtype'), 'raw')) \
            or core.wh_of_type(c, 'raw')


def _before_sret(c, h, lines, user, new):
    for ln in lines:
        rl = db.get(c, 'receipt_line', ln['receipt_line']) if ln.get('receipt_line') else None
        if rl:
            ln['material'] = ln.get('material') or rl['material']
            ln['price'] = rl['price'] if ln.get('price') is None else ln['price']
            ln['tax_rate'] = rl['tax_rate'] if ln.get('tax_rate') is None else ln['tax_rate']
            ln['lot'] = ln.get('lot') or rl['lot']


core.BEFORE_SAVE.update({'purchase_order': _before_po, 'purchase_receipt': _before_receipt, 'supplier_return': _before_sret})
core.RELATED['purchase_order'] = lambda c, h: {
    'receipts': out(db.rows(c, 'purchase_receipt', TABLES['purchase_receipt'].c.purchase_order == h['id'])),
    'returns': out(db.rows(c, 'supplier_return', TABLES['supplier_return'].c.purchase_order == h['id'])),
    'reqs': out(db.rows(c, 'purchase_req', TABLES['purchase_req'].c.purchase_order == h['id']))}
core.RELATED['purchase_receipt'] = lambda c, h: {
    'inspections': out(db.rows(c, 'insp_record', TABLES['insp_record'].c.id.in_([x['insp_record'] for x in core.lines_of(c, 'purchase_receipt', h['id'])
                                                                                    if x.get('insp_record')] or [0]))),
    'returns': out(db.rows(c, 'supplier_return', TABLES['supplier_return'].c.purchase_receipt == h['id']))}


# ───────────────── 请购 ─────────────────
@action('purchase_req', 'approve')
def req_approve(c, r, body, user):
    if r['status'] not in ('draft', 'submitted'):
        raise HTTPException(409, _tr('请购单已{e_status}', e_status=core.e_status('purchase_req', r)))
    db.update(c, 'purchase_req', r['id'], {'status': 'approved'}, raw=True)
    return {'msg': _tr('已批准')}


@action('purchase_req', 'reject')
def req_reject(c, r, body, user):
    db.update(c, 'purchase_req', r['id'], {'status': 'rejected', 'reason': ((r['reason'] or '') + _tr(' [驳回：{v}]', v=body.get('reason') or '')).strip()}, raw=True)
    return {'msg': _tr('已驳回')}


@action('purchase_req', 'cancel')
def req_cancel(c, r, body, user):
    if r['status'] == 'ordered':
        raise HTTPException(409, _tr('已下单的请购不能取消（取消采购订单）'))
    db.update(c, 'purchase_req', r['id'], {'status': 'cancelled'}, raw=True)
    return {'msg': _tr('已取消')}


def reqs_to_po(c, ids, user, source='req'):
    """把批准的请购单按供应商合并成采购订单（草稿）。供应商：请购单上的建议供应商 → 首选合格供方。"""
    groups = defaultdict(list)
    for rid in ids:
        r = db.get(c, 'purchase_req', int(rid))
        if not r:
            continue
        if r['status'] not in ('approved',):
            raise HTTPException(409, _tr('请购单 {code} 是“{e_status}”，批准后才能转采购订单', code=r['code'], e_status=core.e_status('purchase_req', r)))
        sup = r['supplier'] or (core.best_supplier(c, r['material']) or {}).get('supplier')
        if not sup:
            m = db.get(c, 'material', r['material'])
            raise HTTPException(409, _tr('{code} 没有合格供方，请先维护供货清单', code=m['code']))
        groups[sup].append(r)
    made = []
    for sup, rs in groups.items():
        lines = [{'material': r['material'], 'qty': r['qty'], 'due': r['need_date'], 'purchase_req': r['id']} for r in rs]
        pid = core.save_doc(c, 'purchase_order', {'supplier': sup, 'order_date': today(), 'buyer': who(user), 'lines': lines}, user)
        db.update(c, 'purchase_order', pid, {'source': source}, raw=True)
        for ln, r in zip(core.lines_of(c, 'purchase_order', pid), rs):         # 明细按顺序对应请购单（purchase_req 是只读字段，单独写）
            db.update(c, 'po_line', ln['id'], {'purchase_req': r['id']}, raw=True)
            db.update(c, 'purchase_req', r['id'], {'status': 'ordered', 'purchase_order': pid}, raw=True)
        made.append({'id': pid, 'code': core.code_of(c, 'purchase_order', pid)})
    return made


# ───────────────── 采购订单 ─────────────────
@action('purchase_order', 'approve')
def po_approve(c, po, body, user):
    core.check_owned('purchase_order', user, c)
    if po['status'] != 'draft':
        raise HTTPException(409, _tr('采购订单已{e_status}', e_status=core.e_status('purchase_order', po)))
    lines = core.lines_of(c, 'purchase_order', po['id'])
    if not lines:
        raise HTTPException(400, _tr('采购订单没有明细'))
    for ln in lines:
        asl_check(c, po['supplier'], ln['material'])
    db.update(c, 'purchase_order', po['id'], {'status': 'approved'}, raw=True)
    return {'msg': _tr('已审批')}


@action('purchase_order', 'send')
def po_send(c, po, body, user):
    from . import integration
    if po['status'] not in ('approved',):
        raise HTTPException(409, _tr('审批后才能下发供应商'))
    db.update(c, 'purchase_order', po['id'], {'status': 'sent'}, raw=True)
    integration.emit(c, 'po.sent', 'purchase_order', po['code'], {'code': po['code'], 'supplier': core.code_of(c, 'supplier', po['supplier']),
                                                                  'total': po['total']})
    return {'msg': _tr('已下发供应商')}


@action('purchase_order', 'close')
def po_close(c, po, body, user):
    for ln in core.lines_of(c, 'purchase_order', po['id']):
        if ln['status'] == 'open':
            db.update(c, 'po_line', ln['id'], {'status': 'closed'}, raw=True)
    db.update(c, 'purchase_order', po['id'], {'status': 'closed'}, raw=True)
    return {'msg': _tr('已关闭（未到的不再收）')}


@action('purchase_order', 'cancel')
def po_cancel(c, po, body, user):
    core.check_owned('purchase_order', user, c)
    if any((ln['qty_received'] or 0) > 0 for ln in core.lines_of(c, 'purchase_order', po['id'])):
        raise HTTPException(409, _tr('已经收过货的订单不能取消，请用"关闭"'))
    for ln in core.lines_of(c, 'purchase_order', po['id']):
        db.update(c, 'po_line', ln['id'], {'status': 'cancelled'}, raw=True)
        if ln.get('purchase_req'):
            db.update(c, 'purchase_req', ln['purchase_req'], {'status': 'approved', 'purchase_order': None}, raw=True)
    db.update(c, 'purchase_order', po['id'], {'status': 'cancelled'}, raw=True)
    return {'msg': _tr('已取消（关联的请购单退回"已批准"）')}


def po_refresh(c, po_id):
    po = db.get(c, 'purchase_order', po_id)
    if po['status'] not in ('approved', 'sent', 'partial', 'received'):
        return
    lines = [ln for ln in core.lines_of(c, 'purchase_order', po_id) if ln['status'] != 'cancelled']
    got = sum(min(ln['qty_received'] or 0, ln['qty']) for ln in lines)
    tot = sum(ln['qty'] for ln in lines)
    for ln in lines:
        want = 'closed' if (ln['qty_received'] or 0) >= ln['qty'] - 1e-9 else 'open'
        if ln['status'] != want:
            db.update(c, 'po_line', ln['id'], {'status': want}, raw=True)
    st = 'received' if tot and got >= tot - 1e-9 else 'partial' if got > 0 else po['status']
    if st != po['status']:
        db.update(c, 'purchase_order', po_id, {'status': st}, raw=True)


def receipt_from_po(c, po_id, user, wh=None, lot=None, qtys=None):
    po = db.get(c, 'purchase_order', po_id)
    lines = []
    for ln in core.lines_of(c, 'purchase_order', po_id):
        if ln['status'] != 'open':
            continue
        left = (ln['qty'] or 0) - (ln['qty_received'] or 0)
        if qtys is not None:
            if ln['id'] not in qtys:
                continue
            left = float(qtys[ln['id']])
        if left > 0:
            lines.append({'po_line': ln['id'], 'material': ln['material'], 'qty': left, 'lot': lot})
    if not lines:
        raise HTTPException(409, _tr('这张采购订单没有待收的数量'))
    return core.save_doc(c, 'purchase_receipt', {'purchase_order': po_id, 'supplier': po['supplier'], 'warehouse': wh, 'lines': lines}, user)


# ───────────────── 收货 / 来料检验 / 入库 ─────────────────
def iqc_template(c, material):
    t = TABLES['insp_template']
    r = c.execute(sa.select(t).where((t.c.itype == 'IQC') & (t.c.material == material)).limit(1)).mappings().first()
    if not r:
        r = c.execute(sa.select(t).where((t.c.itype == 'IQC') & t.c.material.is_(None)).limit(1)).mappings().first()
    return dict(r) if r else None


@action('purchase_receipt', 'post')
def receipt_post(c, r, body, user):
    """收货：订单行已收数量累加；免检的直接入库，其余每行开一张来料检验单（IQC，按物料的来料检验模板），状态"来料检验中"。"""
    if r['status'] != 'draft':
        raise HTTPException(409, _tr('收货单已{e_status}', e_status=core.e_status('purchase_receipt', r)))
    d = as_date(r['receive_date']) or today()
    check_open(c, d, _tr('收货'))
    lines = core.lines_of(c, 'purchase_receipt', r['id'])
    if not lines:
        raise HTTPException(400, _tr('收货单没有明细'))
    over = float(core.setting(c, 'erp_over_receive_pct', 5) or 0)
    for ln in lines:
        if ln.get('po_line'):
            pl = db.get(c, 'po_line', ln['po_line'])
            if (pl['qty_received'] or 0) + ln['qty'] > pl['qty'] * (1 + over / 100) + 1e-9:
                raise HTTPException(409, _tr('订单行 {seq} 订 {qty:g}，已收 {v:g}，本次 {qty2:g} 超收（允许 {over:g}%）', seq=pl['seq'], qty=pl['qty'], v=pl['qty_received'] or 0, qty2=ln['qty'], over=over),
                                    headers={'X-Gate': 'over_receive'})
    sup = db.get(c, 'supplier', r['supplier'])
    n_insp = 0
    for ln in lines:
        lot = ln.get('lot') or f"{r['code']}-{ln['id']}"
        if ln.get('po_line'):
            pl = db.get(c, 'po_line', ln['po_line'])
            db.update(c, 'po_line', pl['id'], {'qty_received': (pl['qty_received'] or 0) + ln['qty']}, raw=True)
        it = core.supplier_item(c, r['supplier'], ln['material']) or {}
        if it.get('iqc_exempt'):
            db.update(c, 'receipt_line', ln['id'], {'lot': lot, 'status': 'accepted'}, raw=True)
            continue
        tpl = iqc_template(c, ln['material'])
        m = db.get(c, 'material', ln['material'])
        iid = db.insert(c, 'insp_record', {'template': tpl['id'] if tpl else None, 'itype': 'IQC', 'lot': lot, 'qty_sample': int(min(ln['qty'], 5) or 1),
                                           'qty_ng': 0, 'result': 'pending', 'time': clock.now(), 'source': 'manual', 'station': _tr('来料检验'),
                                           'remark': _tr('来料 {code} · {code2} {name} · {code3} {name2} × {qty:g}', code=r['code'], code2=sup['code'], name=sup['name'], code3=m['code'], name2=m['name'], qty=ln['qty'])}, raw=True)
        db.update(c, 'receipt_line', ln['id'], {'lot': lot, 'insp_record': iid, 'status': 'inspecting'}, raw=True)
        n_insp += 1
    db.update(c, 'purchase_receipt', r['id'], {'status': 'inspecting' if n_insp else 'draft', 'posted_by': who(user), 'receive_date': d}, raw=True)
    if r.get('purchase_order'):
        po_refresh(c, r['purchase_order'])
    if not n_insp:                      # 全部免检：直接入库
        return {**receipt_settle(c, db.get(c, 'purchase_receipt', r['id']), {}, user), 'inspections': 0}
    return {'msg': _tr('已收货，开了 {n_insp} 张来料检验单；检验判定后点"按检验结果入库"', n_insp=n_insp), 'inspections': n_insp}


@action('purchase_receipt', 'settle')
def receipt_settle(c, r, body, user):
    """按来料检验结果入库：合格（或 100% 检验时去掉不合格数）入库；不合格转"退供应商"（来料检验不合格）并开不合格品单（处置 = 退供应商）。
    body.lines = {收货行 id: {accepted, rejected}} 可以覆盖（挑选、让步接收）。"""
    from ..services.warehouse import move
    from . import cost, integration
    if r['status'] not in ('inspecting', 'draft'):
        raise HTTPException(409, _tr('收货单已{e_status}', e_status=core.e_status('purchase_receipt', r)))
    d = as_date(r['receive_date']) or today()
    check_open(c, d, _tr('采购入库'))
    over = {int(k): v for k, v in (body.get('lines') or {}).items()}
    lines = core.lines_of(c, 'purchase_receipt', r['id'])
    rejected = []
    for ln in lines:
        if ln['status'] in ('accepted', 'partial', 'rejected') and ln.get('qty_accepted') is not None:
            continue
        acc, rej = ln['qty'], 0.0
        if ln['id'] in over:
            acc, rej = float(over[ln['id']].get('accepted') or 0), float(over[ln['id']].get('rejected') or 0)
            if abs(acc + rej - ln['qty']) > 1e-6:
                raise HTTPException(400, _tr('收货行 {id}：合格 {acc:g} + 不合格 {rej:g} ≠ 到货 {qty:g}', id=ln['id'], acc=acc, rej=rej, qty=ln['qty']))
        elif ln.get('insp_record'):
            ins = db.get(c, 'insp_record', ln['insp_record'])
            if ins['result'] == 'pending':
                raise HTTPException(409, _tr('来料检验单 {code} 还没判定（质检在「检验记录」里判合格 / 不合格）', code=ins['code']), headers={'X-Gate': 'iqc_pending'})
            if ins['result'] == 'NG':
                if (ins['qty_sample'] or 0) >= ln['qty']:          # 全检：去掉不合格的
                    rej = float(min(ln['qty'], ins['qty_ng'] or 0))
                    acc = ln['qty'] - rej
                else:                                               # 抽检不合格：整批拒收
                    acc, rej = 0.0, float(ln['qty'])
        st = 'accepted' if rej == 0 else 'rejected' if acc == 0 else 'partial'
        wh = ln.get('warehouse') or r['warehouse']
        db.update(c, 'receipt_line', ln['id'], {'qty_accepted': acc, 'qty_rejected': rej, 'status': st, 'warehouse': wh}, raw=True)
        if acc > 0:
            move(c, 'in', ln['material'], acc, wh, lot=ln.get('lot'), ref=r['code'], who=who(user), reason=_tr('采购入库'))
        if ln.get('po_line'):
            pl = db.get(c, 'po_line', ln['po_line'])
            db.update(c, 'po_line', pl['id'], {'qty_accepted': (pl['qty_accepted'] or 0) + acc, 'qty_rejected': (pl['qty_rejected'] or 0) + rej}, raw=True)
        if rej > 0:
            rejected.append((ln, rej))
    ret = None
    if rejected:
        ret = core.save_doc(c, 'supplier_return', {'supplier': r['supplier'], 'purchase_receipt': r['id'], 'purchase_order': r['purchase_order'], 'kind': 'iqc',
                                                   'return_date': d, 'reason': _tr('来料检验不合格'),
                                                   'lines': [{'receipt_line': ln['id'], 'material': ln['material'], 'qty': q, 'lot': ln.get('lot')} for ln, q in rejected]},
                            user)
        for ln, q in rejected:
            ins = db.get(c, 'insp_record', ln['insp_record']) if ln.get('insp_record') else None
            db.insert(c, 'ncr', {'lot': ln.get('lot'), 'material': ln['material'], 'qty': int(round(q)), 'found_at': clock.now(), 'found_by': who(user),
                                 'source': 'manual', 'disposition': 'return', 'status': 'open', 'station': _tr('来料检验'),
                                 'root_cause': _tr('来料 {code} 检验不合格', code=r['code']) + (_tr('（{code}）', code=ins['code']) if ins else '') + _tr('，退供应商 {code_of}', code_of=core.code_of(c, 'supplier_return', ret))},
                      raw=True)
    db.update(c, 'purchase_receipt', r['id'], {'status': 'done'}, raw=True)
    core.recalc(c, 'purchase_receipt', r['id'])
    if r.get('purchase_order'):
        po_refresh(c, r['purchase_order'])
    cost.valuate(c)
    rr = db.get(c, 'purchase_receipt', r['id'])
    integration.emit(c, 'receipt.posted', 'purchase_receipt', r['code'], receipt_payload(c, rr))
    acc_n = sum((x['qty_accepted'] or 0) for x in core.lines_of(c, 'purchase_receipt', r['id']))
    return {'msg': _tr('已入库 {acc_n:g}', acc_n=acc_n) + (_tr('；不合格 {sum:g} 转退供应商 {code_of}', sum=sum(q for _, q in rejected), code_of=core.code_of(c, 'supplier_return', ret)) if ret else ''),
            'supplier_return': ret}


def receipt_payload(c, r):
    sup = db.get(c, 'supplier', r['supplier']) or {}
    lines = []
    for ln in core.lines_of(c, 'purchase_receipt', r['id']):
        m = db.get(c, 'material', ln['material']) or {}
        pl = db.get(c, 'po_line', ln['po_line']) if ln.get('po_line') else None
        lines.append({'material': m.get('code'), 'qty': ln['qty'], 'accepted': ln.get('qty_accepted'), 'rejected': ln.get('qty_rejected'), 'lot': ln.get('lot'),
                      'price': ln['price'], 'tax_rate': ln['tax_rate'], 'warehouse': core.code_of(c, 'warehouse', ln.get('warehouse') or r['warehouse']),
                      'po_line_seq': pl['seq'] if pl else None, 'inspection': core.code_of(c, 'insp_record', ln.get('insp_record'))})
    return {'code': r['code'], 'date': db.jsonable(r['receive_date']), 'supplier': sup.get('code'), 'supplier_name': sup.get('name'),
            'purchase_order': core.code_of(c, 'purchase_order', r.get('purchase_order')), 'amount': r['amount'], 'tax': r['tax'], 'total': r['total'], 'lines': lines}


@action('purchase_receipt', 'cancel')
def receipt_cancel(c, r, body, user):
    if r['status'] != 'draft':
        raise HTTPException(409, _tr('已收货的单据不能取消'))
    db.update(c, 'purchase_receipt', r['id'], {'status': 'cancelled'}, raw=True)
    return {'msg': _tr('已取消')}


@action('supplier_return', 'ship')
def sret_ship(c, r, body, user):
    """退货出库：库存退货扣库存（来料检验不合格的本来就没入库，只记录退出）。"""
    from ..services.warehouse import move
    from . import integration
    if r['status'] != 'draft':
        raise HTTPException(409, _tr('退供单已{e_status}', e_status=core.e_status('supplier_return', r)))
    d = as_date(r['return_date']) or today()
    check_open(c, d, _tr('退货出库'))
    lines = core.lines_of(c, 'supplier_return', r['id'])
    if r['kind'] == 'stock':
        for ln in lines:
            wh = r['warehouse'] or core.wh_of_type(c, 'raw')
            if core.stock_qty(c, ln['material'], wh, ln.get('lot')) < ln['qty'] - 1e-9:
                raise HTTPException(409, _tr('{code_of} 库存不足，不能退', code_of=core.code_of(c, 'material', ln['material'])))
            move(c, 'out', ln['material'], ln['qty'], wh, lot=ln.get('lot'), ref=r['code'], who=who(user), reason=_tr('退供应商'))
    db.update(c, 'supplier_return', r['id'], {'status': 'shipped', 'return_date': d}, raw=True)
    integration.emit(c, 'supplier_return.shipped', 'supplier_return', r['code'], {'code': r['code'], 'supplier': core.code_of(c, 'supplier', r['supplier']),
                                                                                   'kind': r['kind'], 'total': r['total']})
    return {'msg': _tr('已退货出库')}


@action('supplier_return', 'cancel')
def sret_cancel(c, r, body, user):
    if r['status'] != 'draft':
        raise HTTPException(409, _tr('已退出的单据不能取消'))
    db.update(c, 'supplier_return', r['id'], {'status': 'cancelled'}, raw=True)
    return {'msg': _tr('已取消')}


# ───────────────── 应付余额、对账单、绩效 ─────────────────
def payable(c, supplier, before=None) -> float:
    """应付余额（入库口径）= 已入库收货（合格部分价税合计）− 库存退货 − 付款。"""
    r, sr, p = TABLES['purchase_receipt'], TABLES['supplier_return'], TABLES['ap_payment']
    q1 = sa.select(sa.func.coalesce(sa.func.sum(r.c.total), 0)).where((r.c.supplier == supplier) & (r.c.status == 'done'))
    q2 = sa.select(sa.func.coalesce(sa.func.sum(sr.c.total), 0)).where((sr.c.supplier == supplier) & (sr.c.status == 'shipped') & (sr.c.kind == 'stock'))
    q3 = sa.select(sa.func.coalesce(sa.func.sum(p.c.amount), 0)).where(p.c.supplier == supplier)
    if before:
        q1, q2, q3 = q1.where(r.c.receive_date < before), q2.where(sr.c.return_date < before), q3.where(p.c.pay_date < before)
    return money(c.execute(q1).scalar()) - money(c.execute(q2).scalar()) - money(c.execute(q3).scalar())


def statement(c, supplier, period, user=None, save=True):
    s, e = core.period_bounds(period)
    r, sr, p, inv = TABLES['purchase_receipt'], TABLES['supplier_return'], TABLES['ap_payment'], TABLES['ap_invoice']
    opening = payable(c, supplier, before=s)
    lines = []
    for x in db.rows(c, 'purchase_receipt', r.c.supplier == supplier, r.c.status == 'done', r.c.receive_date >= s, r.c.receive_date < e, order=r.c.receive_date):
        lines.append({'date': db.jsonable(x['receive_date']), 'kind': '入库', 'doc': x['code'], 'credit': x['total'], 'debit': 0,
                      'po': core.code_of(c, 'purchase_order', x['purchase_order'])})
    for x in db.rows(c, 'supplier_return', sr.c.supplier == supplier, sr.c.status == 'shipped', sr.c.kind == 'stock', sr.c.return_date >= s, sr.c.return_date < e):
        lines.append({'date': db.jsonable(x['return_date']), 'kind': '退货', 'doc': x['code'], 'credit': -x['total'], 'debit': 0})
    for x in db.rows(c, 'ap_payment', p.c.supplier == supplier, p.c.pay_date >= s, p.c.pay_date < e):
        lines.append({'date': db.jsonable(x['pay_date']), 'kind': '付款', 'doc': x['code'], 'credit': 0, 'debit': x['amount']})
    lines.sort(key=lambda z: (z['date'] or '', z['doc']))
    bal = opening
    for z in lines:
        bal = money(bal + z['credit'] - z['debit'])
        z['balance'] = bal
    invoiced = money(c.execute(sa.select(sa.func.coalesce(sa.func.sum(inv.c.total), 0)).where(
        (inv.c.supplier == supplier) & (inv.c.status != 'void') & (inv.c.invoice_date >= s) & (inv.c.invoice_date < e))).scalar())
    res = {'supplier': supplier, 'period': period, 'start': s, 'end': e - timedelta(days=1), 'opening': money(opening),
           'received': money(sum(z['credit'] for z in lines if z['kind'] == '入库')), 'returned': money(-sum(z['credit'] for z in lines if z['kind'] == '退货')),
           'paid': money(sum(z['debit'] for z in lines)), 'invoiced': invoiced, 'closing': money(bal), 'lines': lines}
    if not save:
        return res
    t = TABLES['supplier_statement']
    old = db.rows(c, 'supplier_statement', t.c.supplier == supplier, t.c.period == period)
    if old and old[0]['status'] == 'confirmed':
        raise HTTPException(409, _tr('{period} 的对账单 {code} 已确认，不能重新生成', period=period, code=old[0]['code']))
    if old:
        db.update(c, 'supplier_statement', old[0]['id'], {**res, 'status': 'draft'}, raw=True)
        return old[0]['id']
    return db.insert(c, 'supplier_statement', {**res, 'status': 'draft'}, raw=True)


for _a, _st, _lab in (('send', 'sent', '已发供应商'), ('confirm', 'confirmed', '已确认'), ('dispute', 'disputed', '已记录异议')):
    def _mk(st, lab):
        def fn(c, s, body, user):
            vals = {'status': st}
            if st == 'confirmed':
                vals.update(confirmed_by=body.get('by') or who(user), confirmed_at=clock.now())
            if st == 'disputed':
                vals['note'] = body.get('note') or ''
            db.update(c, 'supplier_statement', s['id'], vals, raw=True)
            return {'msg': lab}
        return fn
    action('supplier_statement', _a)(_mk(_st, _lab))


def performance(c, days=180, supplier=None):
    """供应商绩效：近 days 天收货的每个订单行——准时到货率（收货日 ≤ 订单行到货日期）、来料不合格 ppm、平均延误天数；
    得分 = 60 × 准时率 + 40 × 质量分（ppm 0 → 1，≥ 10000 → 0），写回供应商"绩效得分"。"""
    r, rl, pl = TABLES['purchase_receipt'], TABLES['receipt_line'], TABLES['po_line']
    since = today() - timedelta(days=days)
    q = sa.select(r.c.supplier, r.c.receive_date, rl.c.qty, rl.c.qty_accepted, rl.c.qty_rejected, pl.c.due).join(rl, rl.c.purchase_receipt == r.c.id).outerjoin(
        pl, pl.c.id == rl.c.po_line).where(r.c.status.in_(('done', 'inspecting')) & (r.c.receive_date >= since))
    if supplier:
        q = q.where(r.c.supplier == int(supplier))
    agg = defaultdict(lambda: {'lines': 0, 'ontime': 0, 'qty': 0.0, 'rejected': 0.0, 'delay': 0.0})
    for x in c.execute(q):
        a = agg[x.supplier]
        a['lines'] += 1
        rd, due = as_date(x.receive_date), as_date(x.due)
        if not due or rd <= due:
            a['ontime'] += 1
        else:
            a['delay'] += (rd - due).days
        a['qty'] += x.qty or 0
        a['rejected'] += x.qty_rejected or 0
    res = []
    for sid, a in agg.items():
        s = db.get(c, 'supplier', sid) or {}
        otd = a['ontime'] / a['lines'] if a['lines'] else None
        ppm = a['rejected'] / a['qty'] * 1e6 if a['qty'] else 0
        score = round(60 * (otd or 0) + 40 * max(0.0, 1 - ppm / 10000), 1)
        res.append({'supplier': sid, 'code': s.get('code'), 'name': s.get('name'), 'lines': a['lines'], 'otd': otd, 'ppm': round(ppm),
                    'avg_delay': round(a['delay'] / max(1, a['lines'] - a['ontime']), 1) if a['lines'] > a['ontime'] else 0, 'qty': a['qty'],
                    'rejected': a['rejected'], 'score': score})
    return sorted(res, key=lambda z: -z['score'])


# ───────────────── 接口 ─────────────────
@router.post('/api/purchase/req_to_po')
def req_to_po_api(body: dict = Body(...), user=Depends(require('purchase.purchase_order.create'))):
    with db.engine.begin() as c:
        made = reqs_to_po(c, body.get('ids') or [], user)
    return {'ok': True, 'orders': made}


@router.post('/api/purchase/orders/{pid}/receipt')
def make_receipt_api(pid: int, body: dict = Body(default={}), user=Depends(require('purchase.purchase_receipt.create'))):
    with db.engine.begin() as c:
        po = db.get(c, 'purchase_order', pid)
        if not po or not perms.in_scope(user, 'purchase_order', po):
            raise HTTPException(404, _tr('采购订单不存在'))
        q = {int(k): v for k, v in (body.get('lines') or {}).items()} if body.get('lines') else None
        rid = receipt_from_po(c, pid, user, body.get('warehouse'), body.get('lot'), q)
        return {'ok': True, 'id': rid, 'code': core.code_of(c, 'purchase_receipt', rid)}


@router.get('/api/purchase/performance')
def perf_api(days: int = 180, save: bool = False, user=Depends(require('purchase.supplier_material.read'))):
    with db.engine.begin() as c:
        res = performance(c, days)
        if save and perms.can(user, 'master.supplier.update'):
            for x in res:
                db.update(c, 'supplier', x['supplier'], {'rating': x['score']}, raw=True)
    return res


@router.get('/api/purchase/board')
def purchase_board(user=Depends(require('purchase.purchase_order.read'))):
    """采购看板：未到货订单行（逾期的标出）、待检验收货、待批请购、供应商绩效。"""
    po, pl = TABLES['purchase_order'], TABLES['po_line']
    now = today()
    with db.engine.connect() as c:
        cond = po.c.status.in_(('approved', 'sent', 'partial')) & (pl.c.status == 'open')
        sc = perms.scope_cond(user, 'purchase_order', po)
        if sc is not None:
            cond = cond & sc
        rows = [dict(r) for r in c.execute(sa.select(pl, po.c.code.label('po_code'), po.c.supplier).join(po, po.c.id == pl.c.purchase_order).where(cond)
                                           .order_by(pl.c.due)).mappings()]
        attach_labels(c, 'po_line', rows)
        sups = {s['id']: s for s in db.rows(c, 'supplier')}
        for x in rows:
            x['supplier_label'] = (sups.get(x['supplier']) or {}).get('name')
            x['left'] = (x['qty'] or 0) - (x['qty_received'] or 0)
            x['late_days'] = (now - as_date(x['due'])).days if as_date(x['due']) and as_date(x['due']) < now else 0
        rt = TABLES['purchase_receipt']
        insp = attach_labels(c, 'purchase_receipt', db.rows(c, 'purchase_receipt', rt.c.status == 'inspecting', order=rt.c.id.desc(), limit=50))
        rq = TABLES['purchase_req']
        reqs = attach_labels(c, 'purchase_req', db.rows(c, 'purchase_req', rq.c.status.in_(('submitted', 'approved')), order=rq.c.need_date, limit=100))
        perf = performance(c, 180)
    return {'open_lines': out(rows), 'late': sum(1 for x in rows if x['late_days'] > 0), 'open_value': money(sum(x['left'] * (x['price'] or 0) for x in rows)),
            'inspecting': out(insp), 'reqs': out(reqs), 'performance': perf}


@router.post('/api/purchase/statements')
def statements_api(body: dict = Body(...), user=Depends(require('purchase.supplier_statement.send'))):
    period = body.get('period') or core.period_of(today())
    with db.engine.begin() as c:
        if body.get('supplier'):
            sups = [int(body['supplier'])]
        else:
            s, e = core.period_bounds(period)
            r = TABLES['purchase_receipt']
            sups = sorted(set(c.execute(sa.select(r.c.supplier).where((r.c.status == 'done') & (r.c.receive_date >= s) & (r.c.receive_date < e))).scalars())
                          | {x['id'] for x in db.rows(c, 'supplier') if abs(payable(c, x['id'], before=s)) > 0.005})
        ids = [statement(c, s_, period, user) for s_ in sups]
    return {'ok': True, 'ids': ids, 'count': len(ids)}
