"""应收应付（不做总账）：发票登记（销售发票对发货单、采购发票对收货单）、收款 / 付款（核销到发票，没指定就按到期先后自动核销）、
账龄（未到期 / 1–30 / 31–60 / 61–90 / 90 天以上，按发票到期日；未开票的发货 / 入库按单据日期 + 账期）、余额。
口径：应收余额 = 已发货价税合计 − 退货 − 收款（和对账单一致）；其中"已开票未收" + "已发货未开票" − "未核销的收款" = 余额。"""
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
BUCKETS = [('current', '未到期', None, 0), ('d30', '1–30 天', 1, 30), ('d60', '31–60 天', 31, 60), ('d90', '61–90 天', 61, 90), ('d90p', '90 天以上', 91, None)]
SIDE = {
    'ar': dict(inv='ar_invoice', line='ar_invoice_line', partner='customer', doc='delivery_order', ret='sales_return', pay='ar_receipt', apply='ar_apply',
               pay_fk='receipt', pay_date='receipt_date', doc_date='ship_date', doc_ok=('posted',), ret_date='return_date', ret_ok=('received',)),
    'ap': dict(inv='ap_invoice', line='ap_invoice_line', partner='supplier', doc='purchase_receipt', ret='supplier_return', pay='ap_payment', apply='ap_apply',
               pay_fk='payment', pay_date='pay_date', doc_date='receive_date', doc_ok=('done',), ret_date='return_date', ret_ok=('shipped',)),
}


def _before_inv(side):
    cfg = SIDE[side]

    def fn(c, h, lines, user, new):
        d = as_date(h.get('invoice_date')) or today()
        check_open(c, d, _tr('登记发票'))
        p = core.partner(c, cfg['partner'], h[cfg['partner']]) or {}
        if not h.get('due_date'):
            h['due_date'] = d + timedelta(days=int(p.get('terms_days') or 30))
        it, rt = TABLES[cfg['line']], TABLES[cfg['inv']]
        for ln in lines:
            doc = db.get(c, cfg['doc'], ln[cfg['doc']]) if ln.get(cfg['doc']) else None
            ret = db.get(c, cfg['ret'], ln[cfg['ret']]) if ln.get(cfg['ret']) else None
            src = doc or ret
            if not src:
                raise HTTPException(400, _tr('发票明细要对应') + (_tr('发货单 / 退货单') if side == 'ar' else _tr('收货单 / 退供单')))
            if src[cfg['partner']] != h[cfg['partner']]:
                raise HTTPException(400, _tr('{code} 不是这个{v}的', code=src['code'], v=_tr('客户') if side == 'ar' else _tr('供应商')))
            if doc and doc['status'] not in cfg['doc_ok']:
                raise HTTPException(409, _tr('{code} 还没过账，不能开票', code=doc['code']))
            sign = -1 if ret else 1
            col = it.c[cfg['ret'] if ret else cfg['doc']]
            done = c.execute(sa.select(sa.func.coalesce(sa.func.sum(it.c.total), 0)).join(rt, rt.c.id == it.c.invoice).where(
                (col == src['id']) & (rt.c.status != 'void') & ((it.c.id != ln['_id']) if ln.get('_id') else sa.true()))).scalar() or 0
            left = money(sign * (src['total'] or 0) - float(done))
            if ln.get('total') is None and ln.get('amount') is None:
                ratio = left / (sign * src['total']) if src['total'] else 0
                ln['amount'] = money(sign * (src['amount'] or 0) * ratio)
                ln['tax'] = money(sign * (src['tax'] or 0) * ratio)
            ln['tax'] = money(ln.get('tax') or 0)
            ln['total'] = money((ln.get('amount') or 0) + ln['tax'])
            if abs(ln['total']) > abs(left) + 0.01:
                raise HTTPException(409, _tr('{code} 还能开票 {left:,.2f}，本次 {total:,.2f}', code=src['code'], left=left, total=ln['total']))
    return fn


def _after_inv(side):
    cfg = SIDE[side]

    def fn(c, h, lines, user, new):
        _sync_invoiced(c, side, [ln.get(cfg['doc']) for ln in lines if ln.get(cfg['doc'])])
        if h.get('opening') and not lines:
            db.update(c, cfg['inv'], h['id'], {'total': money((h.get('amount') or 0) + (h.get('tax') or 0))}, raw=True)
    return fn


def _sync_invoiced(c, side, doc_ids):
    cfg = SIDE[side]
    it, rt = TABLES[cfg['line']], TABLES[cfg['inv']]
    for did in {int(x) for x in doc_ids if x}:
        v = c.execute(sa.select(sa.func.coalesce(sa.func.sum(it.c.total), 0)).join(rt, rt.c.id == it.c.invoice).where(
            (it.c[cfg['doc']] == did) & (rt.c.status != 'void'))).scalar() or 0
        db.update(c, cfg['doc'], did, {'invoiced': money(v)}, raw=True)


core.BEFORE_SAVE.update({'ar_invoice': _before_inv('ar'), 'ap_invoice': _before_inv('ap')})
core.AFTER_SAVE.update({'ar_invoice': _after_inv('ar'), 'ap_invoice': _after_inv('ap')})


def _void(side):
    cfg = SIDE[side]

    def fn(c, inv, body, user):
        if (inv['paid'] or 0) > 0.005:
            raise HTTPException(409, _tr('已经核销了 {paid:,.2f}，先撤销核销', paid=inv['paid']))
        check_open(c, inv['invoice_date'], _tr('作废发票'))
        db.update(c, cfg['inv'], inv['id'], {'status': 'void'}, raw=True)
        _sync_invoiced(c, side, [ln.get(cfg['doc']) for ln in core.lines_of(c, cfg['inv'], inv['id'])])
        return {'msg': _tr('已作废')}
    return fn


action('ar_invoice', 'void')(_void('ar'))
action('ap_invoice', 'void')(_void('ap'))


def pay(c, side, body, user):
    """登记收款 / 付款并核销：body = {customer|supplier, amount, date, method, bank_ref, applies: [{invoice, amount}]}；applies 不给 = 按到期日先后自动核销。"""
    cfg = SIDE[side]
    pid = int(body.get(cfg['partner']) or 0)
    if not pid or not core.partner(c, cfg['partner'], pid):
        raise HTTPException(400, _tr('请选择') + (_tr('客户') if side == 'ar' else _tr('供应商')))
    amt = money(body.get('amount'))
    if amt <= 0:
        raise HTTPException(400, _tr('金额要大于 0'))
    d = as_date(body.get('date')) or today()
    check_open(c, d, _tr('登记收款') if side == 'ar' else _tr('登记付款'))
    rid = db.insert(c, cfg['pay'], {cfg['partner']: pid, cfg['pay_date']: d, 'amount': amt, 'method': body.get('method') or 'bank',
                                    'bank_ref': body.get('bank_ref') or '', 'remark': body.get('remark') or '', 'applied': 0, 'status': 'open'}, raw=True)
    apply(c, side, rid, body.get('applies'), user)
    return rid


def apply(c, side, pay_id, applies=None, user=None):
    cfg = SIDE[side]
    p = db.get(c, cfg['pay'], pay_id)
    left = money(p['amount'] - (p['applied'] or 0))
    it = TABLES[cfg['inv']]
    if applies is None:
        opens = db.rows(c, cfg['inv'], it.c[cfg['partner']] == p[cfg['partner']], it.c.status.in_(('open', 'partial')), order=(it.c.due_date, it.c.id))
        applies = []
        for inv in opens:
            if left <= 0.005:
                break
            take = min(left, money(inv['total'] - (inv['paid'] or 0)))
            if take > 0:
                applies.append({'invoice': inv['id'], 'amount': take})
                left = money(left - take)
    total = 0.0
    for a in applies:
        inv = db.get(c, cfg['inv'], int(a['invoice']))
        if not inv or inv[cfg['partner']] != p[cfg['partner']]:
            raise HTTPException(400, _tr('核销的发票不对'))
        if inv['status'] == 'void':
            raise HTTPException(409, _tr('发票 {code} 已作废', code=inv['code']))
        v = money(a['amount'])
        due = money(inv['total'] - (inv['paid'] or 0))
        if v > due + 0.005:
            raise HTTPException(409, _tr('发票 {code} 只欠 {due:,.2f}，核销 {v:,.2f} 太多', code=inv['code'], due=due, v=v))
        total = money(total + v)
        db.insert(c, cfg['apply'], {cfg['pay_fk']: pay_id, 'invoice': inv['id'], 'amount': v, 'time': clock.now()}, raw=True)
        paid = money((inv['paid'] or 0) + v)
        db.update(c, cfg['inv'], inv['id'], {'paid': paid, 'status': 'paid' if paid >= inv['total'] - 0.005 else 'partial'}, raw=True)
    if total > money(p['amount'] - (p['applied'] or 0)) + 0.005:
        raise HTTPException(409, _tr('核销合计超过这笔款的金额'))
    applied = money((p['applied'] or 0) + total)
    db.update(c, cfg['pay'], pay_id, {'applied': applied, 'status': 'applied' if applied >= p['amount'] - 0.005 else 'open'}, raw=True)
    return total


def balance(c, side, pid):
    if side == 'ar':
        from .sales import receivable
        return receivable(c, pid)
    from .purchase import payable
    return payable(c, pid)


def ageing(c, side, as_of=None, partner=None):
    """账龄：已开票未收 / 未付（按发票到期日）+ 已发货 / 入库未开票（按单据日期 + 账期）− 未核销的收 / 付款（单列"预收 / 预付"）。"""
    cfg = SIDE[side]
    as_of = as_date(as_of) or today()
    pt = TABLES[cfg['partner']]
    plist = db.rows(c, cfg['partner'], *([pt.c.id == int(partner)] if partner else []))
    it, dt, rt, pyt = TABLES[cfg['inv']], TABLES[cfg['doc']], TABLES[cfg['ret']], TABLES[cfg['pay']]
    res = []
    tot = defaultdict(float)
    for p in plist:
        b = {k: 0.0 for k, *_ in BUCKETS}
        detail = []
        for inv in db.rows(c, cfg['inv'], it.c[cfg['partner']] == p['id'], it.c.status.in_(('open', 'partial'))):
            left = money(inv['total'] - (inv['paid'] or 0))
            if abs(left) < 0.005:
                continue
            k = _bucket(as_of, as_date(inv['due_date']))
            b[k] += left
            detail.append({'doc': inv['code'], 'kind': '发票', 'invoice_no': inv['invoice_no'], 'due': db.jsonable(inv['due_date']), 'left': left, 'bucket': k})
        terms = int(p.get('terms_days') or 30)
        cond = dt.c[cfg['partner']] == p['id']
        for d in db.rows(c, cfg['doc'], cond, dt.c.status.in_(cfg['doc_ok'])):
            left = money((d['total'] or 0) - (d['invoiced'] or 0))
            if left <= 0.005:
                continue
            due = (as_date(d[cfg['doc_date']]) or as_of) + timedelta(days=terms)
            k = _bucket(as_of, due)
            b[k] += left
            detail.append({'doc': d['code'], 'kind': _tr('未开票') + (_tr('发货') if side == 'ar' else _tr('入库')), 'due': str(due), 'left': left, 'bucket': k})
        rcond = (rt.c[cfg['partner']] == p['id']) & rt.c.status.in_(cfg['ret_ok'])
        if side == 'ap':
            rcond = rcond & (rt.c.kind == 'stock')
        ret_tot = 0.0
        for r in db.rows(c, cfg['ret'], rcond):
            invd = c.execute(sa.select(sa.func.coalesce(sa.func.sum(TABLES[cfg['line']].c.total), 0)).where(TABLES[cfg['line']].c[cfg['ret']] == r['id'])).scalar() or 0
            left = money((r['total'] or 0) + float(invd))          # 红字发票已开的部分已经在发票里冲了
            if left > 0.005:
                ret_tot += left
                detail.append({'doc': r['code'], 'kind': '退货未开红票', 'due': db.jsonable(r[cfg['ret_date']]), 'left': -left, 'bucket': 'current'})
        b['current'] -= ret_tot
        unapplied = sum(money(x['amount'] - (x['applied'] or 0)) for x in db.rows(c, cfg['pay'], pyt.c[cfg['partner']] == p['id'], pyt.c.status == 'open'))
        total = money(sum(b.values()) - unapplied)
        if abs(total) < 0.005 and not any(abs(v) > 0.005 for v in b.values()):
            continue
        row = {'partner': p['id'], 'code': p['code'], 'name': p['name'], **{k: money(v) for k, v in b.items()}, 'unapplied': money(unapplied), 'total': total,
               'overdue': money(sum(v for k, v in b.items() if k != 'current')), 'balance': balance(c, side, p['id']), 'detail': detail,
               'limit': p.get('credit_limit') if side == 'ar' else None}
        res.append(row)
        for k in list(b) + ['unapplied', 'total', 'overdue']:
            tot[k] += row[k]
    return {'as_of': str(as_of), 'buckets': [{'key': k, 'label': lab} for k, lab, *_ in BUCKETS], 'rows': sorted(res, key=lambda r: -r['total']),
            'total': {k: money(v) for k, v in tot.items()}}


def _bucket(as_of, due):
    if not due or due >= as_of:
        return 'current'
    days = (as_of - due).days
    for k, _, lo, hi in BUCKETS[1:]:
        if days >= lo and (hi is None or days <= hi):
            return k
    return 'd90p'


# ───────────────── 接口 ─────────────────
@router.post('/api/finance/ar_receipts')
def ar_receipt_api(body: dict = Body(...), user=Depends(require('finance.ar_receipt.create'))):
    with db.engine.begin() as c:
        rid = pay(c, 'ar', body, user)
        return {'ok': True, 'id': rid, 'code': core.code_of(c, 'ar_receipt', rid), 'item': out([db.get(c, 'ar_receipt', rid)])[0]}


@router.post('/api/finance/ap_payments')
def ap_payment_api(body: dict = Body(...), user=Depends(require('finance.ap_payment.create'))):
    with db.engine.begin() as c:
        rid = pay(c, 'ap', body, user)
        return {'ok': True, 'id': rid, 'code': core.code_of(c, 'ap_payment', rid), 'item': out([db.get(c, 'ap_payment', rid)])[0]}


@router.post('/api/finance/{side}/apply/{pid}')
def apply_api(side: str, pid: int, body: dict = Body(default={}), user=Depends(require('finance.ar_receipt.update', 'finance.ap_payment.update', any_of=True))):
    if side not in SIDE:
        raise HTTPException(404, _tr('没有这个'))
    perms.check(user, f"finance.{SIDE[side]['pay']}.update")
    with db.engine.begin() as c:
        return {'ok': True, 'applied': apply(c, side, pid, body.get('applies'), user)}


@router.get('/api/finance/ageing')
def ageing_api(side: str = 'ar', as_of: str = '', partner: int | None = None, user=Depends(require('finance.ar_invoice.read', 'finance.ap_invoice.read', any_of=True))):
    if side not in SIDE:
        raise HTTPException(400, _tr('side 只能是 ar / ap'))
    perms.check(user, f"finance.{SIDE[side]['inv']}.read")
    with db.engine.connect() as c:
        return ageing(c, side, as_of or None, partner)


@router.get('/api/finance/uninvoiced')
def uninvoiced_api(side: str = 'ar', partner: int = 0, user=Depends(require('finance.ar_invoice.read', 'finance.ap_invoice.read', any_of=True))):
    """还能开票的发货单 / 收货单（开票时选）。"""
    cfg = SIDE.get(side) or SIDE['ar']
    dt = TABLES[cfg['doc']]
    with db.engine.connect() as c:
        rows = db.rows(c, cfg['doc'], dt.c[cfg['partner']] == partner, dt.c.status.in_(cfg['doc_ok']), order=dt.c.id.desc(), limit=300)
        res = [dict(r, left=money((r['total'] or 0) - (r['invoiced'] or 0))) for r in rows if (r['total'] or 0) - (r['invoiced'] or 0) > 0.005]
        return out(attach_labels(c, cfg['doc'], res))


@router.get('/api/finance/summary')
def summary_api(user=Depends(require('finance.ar_invoice.read'))):
    with db.engine.connect() as c:
        ar, ap = ageing(c, 'ar'), ageing(c, 'ap')
        s, e = core.period_bounds(core.period_of(today()))
        rc, py = TABLES['ar_receipt'], TABLES['ap_payment']
        got = money(c.execute(sa.select(sa.func.coalesce(sa.func.sum(rc.c.amount), 0)).where((rc.c.receipt_date >= s) & (rc.c.receipt_date < e))).scalar())
        paid = money(c.execute(sa.select(sa.func.coalesce(sa.func.sum(py.c.amount), 0)).where((py.c.pay_date >= s) & (py.c.pay_date < e))).scalar())
    return {'ar_total': ar['total'].get('total', 0), 'ar_overdue': ar['total'].get('overdue', 0), 'ap_total': ap['total'].get('total', 0),
            'ap_overdue': ap['total'].get('overdue', 0), 'received_month': got, 'paid_month': paid}
