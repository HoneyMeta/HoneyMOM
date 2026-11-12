"""销售：报价 → 销售订单（交货计划行）→ 变更 / 取消（有历史）→ 发货单（扣库存、记发运、按单生产连工单）→ 退货 → 对账单；
信用额度检查；订单状态看板（准时交付率、未交订单、有风险的订单）。"""
from ..i18n import _tr
from .. import i18n as _i18n
from collections import defaultdict
from datetime import timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import audit, db, perms
from ..crud import action, attach_labels, out
from ..db import clock
from ..perms import require
from ..schema import TABLES
from . import core
from .core import as_date, check_open, money, today, who

router = APIRouter()
perms.bind_scope('sales_order', 'plant', 'plant')
perms.bind_scope('delivery_order', 'plant', 'plant')
perms.bind_scope('sales_return', 'plant', 'plant')
perms.bind_scope('so_line', 'sales_order', 'sales_order')
perms.bind_scope('delivery_line', 'delivery_order', 'delivery_order')

OVERDUE, AT_RISK, WATCH = 'overdue', 'at_risk', 'watch'
RISK_CN = {OVERDUE: '已逾期', AT_RISK: '预计逾期', WATCH: '临近交期未备齐', 'ok': '正常'}


# ───────────────── 保存钩子 ─────────────────
def _price_lines(c, ent, h, lines):
    for ln in lines:
        if not ln.get('material'):
            raise HTTPException(400, _tr('明细行要选物料'))
        if (ln.get('qty') or 0) <= 0:
            raise HTTPException(400, _tr('数量要大于 0'))
        if ln.get('price') is None:
            p, tr, _ = core.sales_price(c, h['customer'], ln['material'], ln['qty'], h.get(core.DOCS[ent]['date']))
            ln['price'] = p
            if tr is not None and ln.get('tax_rate') is None:
                ln['tax_rate'] = tr
        if 'customer_part_no' in ln and not ln.get('customer_part_no'):
            cp = db.get(c, 'customer_part', customer=h['customer'], material=ln['material'])
            if cp:
                ln['customer_part_no'] = cp['customer_part_no']


def _before_quote(c, h, lines, user, new):
    _price_lines(c, 'quotation', h, lines)
    h.setdefault('valid_until', None)
    if not h.get('valid_until'):
        h['valid_until'] = (as_date(h.get('quote_date')) or today()) + timedelta(days=30)


def _before_so(c, h, lines, user, new):
    core.check_owned('sales_order', user, c)
    cust = db.get(c, 'customer', h['customer'])
    if cust and cust.get('active') is False:
        raise HTTPException(409, _tr('客户 {code} 已停用，不能下订单', code=cust['code']))
    _price_lines(c, 'sales_order', h, lines)
    for ln in lines:
        if not ln.get('due'):
            ln['due'] = h.get('due') or (as_date(h.get('order_date')) or today()) + timedelta(days=14)
    if lines and not h.get('due'):
        h['due'] = min(as_date(ln['due']) for ln in lines)
    if not h.get('plant'):
        p = db.rows(c, 'plant', limit=1)
        h['plant'] = p[0]['id'] if p else None


def _after_so(c, h, lines, user, new):
    """交货计划：明细给了 schedule=[{due, qty}] 就用它（合计要等于数量），否则一条 = 明细的交期和数量。"""
    for ln in lines:
        sched = (ln.get('_extra') or {}).get('schedule')
        set_schedule(c, h, db.get(c, 'so_line', ln['id']), sched)


def set_schedule(c, so, line, sched=None):
    st = TABLES['so_schedule']
    have = db.rows(c, 'so_schedule', st.c.so_line == line['id'], order=st.c.due)
    if sched is None:
        if len(have) == 1 and not have[0]['qty_shipped']:
            db.update(c, 'so_schedule', have[0]['id'], {'due': line['due'], 'qty': line['qty']}, raw=True)
            return
        if have and abs(sum(x['qty'] for x in have) - line['qty']) < 1e-9:
            return
        if len(have) > 1:                # 只改了数量：差额加到最后一条（减少时从后往前扣，不扣到已发以下）
            delta = float(line['qty']) - sum(x['qty'] for x in have)
            sched = [{'due': x['due'], 'qty': x['qty']} for x in have]
            if delta > 0:
                sched[-1]['qty'] += delta
            else:
                for x, h in zip(reversed(sched), reversed(have)):
                    take = min(-delta, x['qty'] - (h['qty_shipped'] or 0))
                    x['qty'] -= take
                    delta += take
                sched = [x for x in sched if x['qty'] > 1e-9] or [{'due': line['due'], 'qty': line['qty']}]
        else:
            sched = [{'due': line['due'], 'qty': line['qty']}]
    clean = []
    for s in sched:
        d, q = as_date(s.get('due')), float(s.get('qty') or 0)
        if not d or q <= 0:
            raise HTTPException(400, _tr('交货计划每行要有日期和大于 0 的数量'))
        clean.append((d, q))
    if abs(sum(q for _, q in clean) - float(line['qty'])) > 1e-6:
        raise HTTPException(400, _tr('第 {seq} 行交货计划合计 {sum:g} ≠ 订单数量 {qty:g}', seq=line['seq'], sum=sum(q for _, q in clean), qty=line['qty']))
    shipped = float(line.get('qty_shipped') or 0)
    for x in have:
        db.delete(c, 'so_schedule', x['id'])
    for d, q in sorted(clean):
        take = min(q, shipped)
        shipped -= take
        db.insert(c, 'so_schedule', {'so_line': line['id'], 'sales_order': so['id'], 'due': d, 'qty': q, 'qty_shipped': take}, raw=True)
    db.update(c, 'so_line', line['id'], {'due': sorted(clean)[0][0]}, raw=True)


def _before_delivery(c, h, lines, user, new):
    so = db.get(c, 'sales_order', h['sales_order']) if h.get('sales_order') else None
    if so:
        if so['status'] not in ('confirmed', 'partial'):
            raise HTTPException(409, _tr('销售订单 {code} 是“{e_status}”，不能发货', code=so['code'], e_status=core.e_status('sales_order', so)))
        h['customer'] = so['customer']
        h['plant'] = h.get('plant') or so['plant']
        h['ship_to'] = h.get('ship_to') or so.get('ship_to')
    if not h.get('warehouse'):
        h['warehouse'] = core.wh_of_type(c, 'fg')
    for ln in lines:
        sl = db.get(c, 'so_line', ln['so_line']) if ln.get('so_line') else None
        if sl:
            ln['material'] = ln.get('material') or sl['material']
            if ln.get('price') is None:
                ln['price'] = sl['price']
            if ln.get('tax_rate') is None:
                ln['tax_rate'] = sl['tax_rate']
            ln['work_order'] = ln.get('work_order') or sl.get('work_order')
        elif ln.get('price') is None and ln.get('material'):
            ln['price'] = core.sales_price(c, h['customer'], ln['material'], ln.get('qty'))[0]
        if (ln.get('qty') or 0) <= 0:
            raise HTTPException(400, _tr('发货数量要大于 0'))


def _before_return(c, h, lines, user, new):
    d = db.get(c, 'delivery_order', h['delivery_order']) if h.get('delivery_order') else None
    if d:
        h['customer'], h['sales_order'] = d['customer'], h.get('sales_order') or d['sales_order']
        h['plant'] = h.get('plant') or d.get('plant')
        h['warehouse'] = h.get('warehouse') or d['warehouse']
    for ln in lines:
        dl = db.get(c, 'delivery_line', ln['delivery_line']) if ln.get('delivery_line') else None
        if dl:
            ln['material'] = ln.get('material') or dl['material']
            ln['price'] = dl['price'] if ln.get('price') is None else ln['price']
            ln['tax_rate'] = dl['tax_rate'] if ln.get('tax_rate') is None else ln['tax_rate']
            ln['lot'] = ln.get('lot') or dl['lot']
            ln['unit_cost'] = dl['unit_cost']
        if (ln.get('qty') or 0) <= 0:
            raise HTTPException(400, _tr('退货数量要大于 0'))


core.BEFORE_SAVE.update({'quotation': _before_quote, 'sales_order': _before_so, 'delivery_order': _before_delivery, 'sales_return': _before_return})
core.AFTER_SAVE['sales_order'] = _after_so


def _rel_so(c, h):
    t = TABLES
    res = {'schedules': out(db.rows(c, 'so_schedule', t['so_schedule'].c.sales_order == h['id'], order=t['so_schedule'].c.due)),
           'changes': out(db.rows(c, 'so_change', t['so_change'].c.sales_order == h['id'], order=t['so_change'].c.id.desc())),
           'deliveries': out(attach_labels(c, 'delivery_order', db.rows(c, 'delivery_order', t['delivery_order'].c.sales_order == h['id']))),
           'returns': out(db.rows(c, 'sales_return', t['sales_return'].c.sales_order == h['id'])),
           'work_orders': out(db.rows(c, 'work_order', t['work_order'].c.sales_order == h['id'])),
           'credit': credit(c, h['customer'])}
    return res


core.RELATED['sales_order'] = _rel_so
core.RELATED['delivery_order'] = lambda c, h: {'returns': out(db.rows(c, 'sales_return', TABLES['sales_return'].c.delivery_order == h['id']))}


# ───────────────── 信用额度 ─────────────────
def receivable(c, customer, before=None) -> float:
    """应收余额（发货口径）= 已过账发货（价税合计）− 已收货的退货 − 收款。before：只算这天之前的。"""
    d, r, rc = TABLES['delivery_order'], TABLES['sales_return'], TABLES['ar_receipt']
    q1 = sa.select(sa.func.coalesce(sa.func.sum(d.c.total), 0)).where((d.c.customer == customer) & (d.c.status == 'posted'))
    q2 = sa.select(sa.func.coalesce(sa.func.sum(r.c.total), 0)).where((r.c.customer == customer) & (r.c.status == 'received'))
    q3 = sa.select(sa.func.coalesce(sa.func.sum(rc.c.amount), 0)).where(rc.c.customer == customer)
    if before:
        q1, q2, q3 = q1.where(d.c.ship_date < before), q2.where(r.c.return_date < before), q3.where(rc.c.receipt_date < before)
    return money(c.execute(q1).scalar()) - money(c.execute(q2).scalar()) - money(c.execute(q3).scalar())


def open_orders_value(c, customer, exclude=None) -> float:
    so, sl = TABLES['sales_order'], TABLES['so_line']
    q = sa.select(sl.c.qty, sl.c.qty_shipped, sl.c.price, sl.c.tax_rate).join(so, so.c.id == sl.c.sales_order).where(
        (so.c.customer == customer) & so.c.status.in_(('confirmed', 'partial')) & (sl.c.status == 'open'))
    if exclude:
        q = q.where(so.c.id != exclude)
    tot = 0.0
    for r in c.execute(q):
        left = max(0.0, (r.qty or 0) - (r.qty_shipped or 0))
        tot += left * (r.price or 0) * (1 + (r.tax_rate or 0) / 100)
    return money(tot)


def credit(c, customer, extra=0.0, exclude=None) -> dict:
    cu = db.get(c, 'customer', customer) or {}
    lim = float(cu.get('credit_limit') or 0)
    ar, oo = receivable(c, customer), open_orders_value(c, customer, exclude)
    exp = money(ar + oo + extra)
    return {'limit': lim, 'receivable': ar, 'open_orders': oo, 'this_order': money(extra), 'exposure': exp,
            'available': money(lim - exp) if lim else None, 'over': bool(lim and exp > lim + 0.005)}


# ───────────────── 订单动作 ─────────────────
def _log(c, so, kind, diff=None, reason='', user=None, version=None):
    db.insert(c, 'so_change', {'sales_order': so['id'], 'version': version or so.get('version') or 1, 'kind': kind, 'diff': diff or {}, 'reason': reason,
                               'changed_by': who(user), 'time': clock.now()}, raw=True)


@action('sales_order', 'confirm')
def so_confirm(c, so, body, user):
    core.check_owned('sales_order', user, c)
    if so['status'] not in ('draft',):
        raise HTTPException(409, _tr('订单是“{e_status}”，不能确认', e_status=core.e_status('sales_order', so)))
    lines = core.lines_of(c, 'sales_order', so['id'])
    if not lines:
        raise HTTPException(400, _tr('订单还没有明细'))
    cr = credit(c, so['customer'], so['total'] or 0, exclude=so['id'])
    if cr['over']:
        note = _tr('超信用额度：额度 {limit:,.2f}，应收 {receivable:,.2f} + 未交订单 {open_orders:,.2f} + 本单 {this_order:,.2f} = {exposure:,.2f}', limit=cr['limit'], receivable=cr['receivable'], open_orders=cr['open_orders'], this_order=cr['this_order'], exposure=cr['exposure'])
        db.update(c, 'sales_order', so['id'], {'status': 'credit_hold', 'credit_note': note}, raw=True)
        _log(c, so, 'confirm', {'status': ['draft', 'credit_hold']}, note, user)
        return {'msg': note + _tr('；已转"信用待批"，需要有"信用特批"权限的人批准'), 'credit': cr, 'status': 'credit_hold'}
    db.update(c, 'sales_order', so['id'], {'status': 'confirmed', 'credit_note': _tr('信用检查通过') if cr['limit'] else _tr('未设信用额度')}, raw=True)
    _log(c, so, 'confirm', {'status': [so['status'], 'confirmed']}, '', user)
    from . import integration
    integration.emit(c, 'so.confirmed', 'sales_order', so['code'], {'code': so['code'], 'customer': core.code_of(c, 'customer', so['customer'])})
    return {'msg': _tr('订单 {code} 已确认', code=so['code']), 'credit': cr, 'status': 'confirmed'}


@action('sales_order', 'approve_credit')
def so_approve_credit(c, so, body, user):
    if so['status'] != 'credit_hold':
        raise HTTPException(409, _tr('只有"信用待批"的订单需要特批'))
    reason = (body.get('reason') or '').strip()
    if not reason:
        raise HTTPException(400, _tr('请写特批理由'))
    db.update(c, 'sales_order', so['id'], {'status': 'confirmed', 'credit_note': _tr('信用特批（{who}）：{reason}', who=who(user), reason=reason)}, raw=True)
    _log(c, so, 'credit', {'status': ['credit_hold', 'confirmed']}, reason, user)
    return {'msg': _tr('已特批并确认')}


@action('sales_order', 'cancel')
def so_cancel(c, so, body, user):
    core.check_owned('sales_order', user, c)
    if so['status'] in ('shipped', 'closed', 'cancelled'):
        raise HTTPException(409, _tr('订单已{e_status}', e_status=core.e_status('sales_order', so)))
    lines = core.lines_of(c, 'sales_order', so['id'])
    if any((ln['qty_shipped'] or 0) > 0 for ln in lines):
        raise HTTPException(409, _tr('已经有发货的订单不能取消，请用"关闭"（未发的部分作废）'))
    reason = (body.get('reason') or '').strip() or _tr('客户取消')
    wt = TABLES['work_order']
    for w in db.rows(c, 'work_order', wt.c.sales_order == so['id'], wt.c.status == 'draft'):
        db.update(c, 'work_order', w['id'], {'status': 'cancelled'}, raw=True)
    for ln in lines:
        db.update(c, 'so_line', ln['id'], {'status': 'cancelled'}, raw=True)
    db.update(c, 'sales_order', so['id'], {'status': 'cancelled', 'version': (so['version'] or 1) + 1}, raw=True)
    _log(c, so, 'cancel', {'status': [so['status'], 'cancelled']}, reason, user, (so['version'] or 1) + 1)
    from . import integration
    integration.emit(c, 'so.cancelled', 'sales_order', so['code'], {'code': so['code'], 'reason': reason})
    return {'msg': _tr('订单已取消')}


@action('sales_order', 'close')
def so_close(c, so, body, user):
    if so['status'] in ('closed', 'cancelled'):
        raise HTTPException(409, _tr('订单已关闭'))
    for ln in core.lines_of(c, 'sales_order', so['id']):
        if ln['status'] == 'open':
            db.update(c, 'so_line', ln['id'], {'status': 'closed'}, raw=True)
    db.update(c, 'sales_order', so['id'], {'status': 'closed'}, raw=True)
    _log(c, so, 'close', {'status': [so['status'], 'closed']}, body.get('reason') or '', user)
    return {'msg': _tr('订单已关闭（未发的部分不再发）')}


@action('sales_order', 'make_wo')
def so_make_wo(c, so, body, user):
    """按单生产：每条还没有工单的明细（自制件）生成一张草稿工单，工单交期 = 明细交期前 1 天。"""
    if so['status'] not in ('confirmed', 'partial'):
        raise HTTPException(409, _tr('订单确认后才能生成工单'))
    from .mrp import make_or_buy
    made = []
    for ln in core.lines_of(c, 'sales_order', so['id']):
        if ln['status'] != 'open' or ln.get('work_order'):
            continue
        if make_or_buy(c, ln['material']) != 'make':
            continue
        left = (ln['qty'] or 0) - (ln['qty_shipped'] or 0)
        if left <= 0:
            continue
        due = as_date(ln['due']) or today()
        route = db.get(c, 'route', product=ln['material'], status='active')
        cust = db.get(c, 'customer', so['customer']) or {}
        wid = db.insert(c, 'work_order', {'product': ln['material'], 'route': route['id'] if route else None, 'qty': int(round(left)), 'qty_done': 0, 'qty_scrap': 0,
                                          'due': db.parse_dt(str(due - timedelta(days=1)) + ' 17:00'), 'status': 'draft', 'priority': 5,
                                          'customer': cust.get('short_name') or cust.get('name'), 'sales_order': so['id'], 'so_line': ln['id'],
                                          'remark': _tr('按单生产：{code} 第 {seq} 行', code=so['code'], seq=ln['seq'])}, raw=True)
        db.update(c, 'so_line', ln['id'], {'work_order': wid}, raw=True)
        made.append(core.code_of(c, 'work_order', wid))
    if not made:
        return {'msg': _tr('没有需要生成工单的明细（已有工单或是外购件）'), 'work_orders': []}
    return {'msg': _tr('已生成 {len} 张工单：{join}（草稿，下达后排产）', len=len(made), join=_tr('、').join(made)), 'work_orders': made}


def change_order(c, so, body, user):
    """已确认订单的变更：改数量 / 交期 / 单价、加行、删行（没发货的）。记一条变更记录（改了什么、原因），版本号 + 1；不能改到比已发数量少。"""
    core.check_owned('sales_order', user, c)
    if so['status'] not in ('confirmed', 'partial', 'credit_hold'):
        raise HTTPException(409, _tr('订单是“{e_status}”，不能变更', e_status=core.e_status('sales_order', so)))
    reason = (body.get('reason') or '').strip()
    if not reason:
        raise HTTPException(400, _tr('请写变更原因'))
    lines = {ln['id']: ln for ln in core.lines_of(c, 'sales_order', so['id'])}
    diff = {}
    new_in = body.get('lines') or []
    seen = set()
    mats = {m['id']: m['code'] for m in db.rows(c, 'material')}
    for i, ln in enumerate(new_in):
        lid = int(ln['id']) if ln.get('id') else None
        if lid and lid in lines:
            seen.add(lid)
            old = lines[lid]
            upd = {}
            for k in ('qty', 'price', 'due', 'tax_rate'):
                if k in ln and ln[k] not in (None, ''):
                    v = as_date(ln[k]) if k == 'due' else float(ln[k])
                    if (as_date(old[k]) if k == 'due' else old[k]) != v:
                        upd[k] = v
            if 'qty' in upd and upd['qty'] < (old['qty_shipped'] or 0):
                raise HTTPException(409, _tr('第 {seq} 行已发 {qty_shipped:g}，数量不能改成 {qty:g}', seq=old['seq'], qty_shipped=old['qty_shipped'], qty=upd['qty']))
            if upd:
                diff[f"第{old['seq']}行 {mats.get(old['material'], '')}"] = {k: [db.jsonable(old[k]), db.jsonable(v)] for k, v in upd.items()}
                db.update(c, 'so_line', lid, dict(upd), raw=True)
            sl = db.get(c, 'so_line', lid)
            if 'schedule' in ln or 'qty' in upd or 'due' in upd:
                set_schedule(c, so, sl, ln.get('schedule'))
            if sl['status'] == 'closed' and (sl['qty_shipped'] or 0) < sl['qty']:
                db.update(c, 'so_line', lid, {'status': 'open'}, raw=True)
        elif not lid:
            mid = int(ln['material'])
            q = float(ln.get('qty') or 0)
            if q <= 0:
                raise HTTPException(400, _tr('新加的行数量要大于 0'))
            p = float(ln['price']) if ln.get('price') not in (None, '') else core.sales_price(c, so['customer'], mid, q)[0]
            seq = (max([x['seq'] or 0 for x in lines.values()] or [0]) + 10) + i
            nid = db.insert(c, 'so_line', {'sales_order': so['id'], 'seq': seq, 'material': mid, 'qty': q, 'price': p,
                                           'tax_rate': float(ln['tax_rate']) if ln.get('tax_rate') not in (None, '') else core.default_tax(c, 'sales_order', so),
                                           'due': as_date(ln.get('due')) or as_date(so['due']), 'qty_shipped': 0, 'qty_returned': 0, 'status': 'open'}, raw=True)
            set_schedule(c, so, db.get(c, 'so_line', nid), ln.get('schedule'))
            diff[f"加第{seq}行 {mats.get(mid, '')}"] = {'qty': [None, q], 'price': [None, p]}
    for lid in body.get('remove') or []:
        old = lines.get(int(lid))
        if not old:
            continue
        if (old['qty_shipped'] or 0) > 0:
            raise HTTPException(409, _tr('第 {seq} 行已经发货，不能删除（可以把数量改成已发数量）', seq=old['seq']))
        db.update(c, 'so_line', old['id'], {'status': 'cancelled', 'qty': 0}, raw=True)
        diff[f"取消第{old['seq']}行 {mats.get(old['material'], '')}"] = {'qty': [old['qty'], 0]}
    for k in ('due', 'customer_po', 'ship_to', 'remark'):
        if k in body and body[k] not in (None, '') and str(db.jsonable(so.get(k))) != str(body[k]):
            diff[k] = [db.jsonable(so.get(k)), body[k]]
            db.update(c, 'sales_order', so['id'], {k: as_date(body[k]) if k == 'due' else body[k]}, raw=True)
    if not diff:
        raise HTTPException(400, _tr('没有改动'))
    ver = (so['version'] or 1) + 1
    db.update(c, 'sales_order', so['id'], {'version': ver}, raw=True)
    core.recalc(c, 'sales_order', so['id'])
    refresh_status(c, so['id'])
    _log(c, so, 'change', diff, reason, user, ver)
    audit.audit(c, 'change', 'sales_order', so['id'], detail={'version': ver, 'reason': reason, 'diff': diff})
    from . import integration
    integration.emit(c, 'so.changed', 'sales_order', so['code'], {'code': so['code'], 'version': ver, 'reason': reason, 'diff': diff})
    return {'ok': True, 'version': ver, 'diff': diff}


def refresh_status(c, so_id):
    so = db.get(c, 'sales_order', so_id)
    if so['status'] not in ('confirmed', 'partial', 'shipped'):
        return so['status']
    lines = [ln for ln in core.lines_of(c, 'sales_order', so_id) if ln['status'] != 'cancelled']
    shipped = sum(min(ln['qty_shipped'] or 0, ln['qty']) for ln in lines)
    total = sum(ln['qty'] for ln in lines)
    st = 'shipped' if total and shipped >= total - 1e-9 else 'partial' if shipped > 0 else 'confirmed'
    for ln in lines:
        want = 'closed' if (ln['qty_shipped'] or 0) >= ln['qty'] - 1e-9 else 'open'
        if ln['status'] != want:
            db.update(c, 'so_line', ln['id'], {'status': want}, raw=True)
    if st != so['status']:
        db.update(c, 'sales_order', so_id, {'status': st}, raw=True)
    return st


# ───────────────── 报价 ─────────────────
@action('quotation', 'send')
def q_send(c, q, body, user):
    if q['status'] != 'draft':
        raise HTTPException(409, _tr('只有草稿可以发出'))
    if not core.lines_of(c, 'quotation', q['id']):
        raise HTTPException(400, _tr('报价单还没有明细'))
    db.update(c, 'quotation', q['id'], {'status': 'sent'}, raw=True)
    return {'msg': _tr('已发出报价')}


@action('quotation', 'lose')
def q_lose(c, q, body, user):
    db.update(c, 'quotation', q['id'], {'status': 'lost', 'remark': ((q['remark'] or '') + _tr('\n未成交：{v}', v=body.get('reason') or '')).strip()}, raw=True)
    return {'msg': _tr('已标记未成交')}


@action('quotation', 'accept')
def q_accept(c, q, body, user):
    if q['status'] not in ('draft', 'sent'):
        raise HTTPException(409, _tr('报价单已{e_status}', e_status=core.e_status('quotation', q)))
    lines = core.lines_of(c, 'quotation', q['id'])
    if not lines:
        raise HTTPException(400, _tr('报价单还没有明细'))
    start = today()
    so_lines = [{'material': ln['material'], 'qty': ln['qty'], 'price': ln['price'], 'tax_rate': ln['tax_rate'], 'customer_part_no': ln['customer_part_no'],
                 'due': start + timedelta(days=int(ln['lead_days'] or 14))} for ln in lines]
    sid = core.save_doc(c, 'sales_order', {'customer': q['customer'], 'order_date': start, 'customer_po': body.get('customer_po') or '',
                                           'salesman': q['salesman'], 'lines': so_lines}, user)
    db.update(c, 'sales_order', sid, {'quotation': q['id'], 'source': 'quotation'}, raw=True)
    db.update(c, 'quotation', q['id'], {'status': 'accepted', 'sales_order': sid}, raw=True)
    return {'msg': _tr('已转销售订单 {code_of}', code_of=core.code_of(c, 'sales_order', sid)), 'id': sid, 'code': core.code_of(c, 'sales_order', sid)}


# ───────────────── 发货 ─────────────────
def delivery_from_order(c, so_id, user, wh=None, ship_date=None, only=None):
    """按订单剩余数量生成发货单草稿（交货计划到期的优先；only = {so_line id: 数量} 只发这些）。"""
    so = db.get(c, 'sales_order', so_id)
    if not so:
        raise HTTPException(404, _tr('订单不存在'))
    lines = []
    for ln in core.lines_of(c, 'sales_order', so_id):
        if ln['status'] != 'open':
            continue
        left = (ln['qty'] or 0) - (ln['qty_shipped'] or 0)
        if only is not None:
            if ln['id'] not in only:
                continue
            left = min(left, float(only[ln['id']]))
        if left > 0:
            lines.append({'so_line': ln['id'], 'material': ln['material'], 'qty': left})
    if not lines:
        raise HTTPException(409, _tr('这个订单没有待发的数量'))
    return core.save_doc(c, 'delivery_order', {'sales_order': so_id, 'customer': so['customer'], 'warehouse': wh, 'ship_date': ship_date, 'lines': lines}, user)


@action('delivery_order', 'post')
def delivery_post(c, d, body, user):
    """发货过账：检查订单剩余、按单生产的工单完工数、库存、冻结批次 → 扣库存（出入库记录"销售发货"）、记发运记录、
    更新订单已发数量和交货计划、订单状态；生成"发货"事件（外部 ERP 回传 / Webhook）。"""
    from ..services.gates import check_lot_out
    from ..services.warehouse import move
    from . import cost, integration
    if d['status'] != 'draft':
        raise HTTPException(409, _tr('发货单已{e_status}', e_status=core.e_status('delivery_order', d)))
    ship = as_date(d['ship_date']) or today()
    check_open(c, ship, _tr('发货过账'))
    lines = core.lines_of(c, 'delivery_order', d['id'])
    if not lines:
        raise HTTPException(400, _tr('发货单没有明细'))
    over_pct = float(core.setting(c, 'erp_over_ship_pct', 0) or 0)
    allow_neg = str(core.setting(c, 'erp_allow_negative', '0')) == '1'
    by_line = defaultdict(float)
    for ln in lines:
        by_line[ln['so_line']] += ln['qty'] or 0
    for sid, q in by_line.items():
        if not sid:
            continue
        sl = db.get(c, 'so_line', sid)
        left = (sl['qty'] or 0) * (1 + over_pct / 100) - (sl['qty_shipped'] or 0)
        if q > left + 1e-9:
            raise HTTPException(409, _tr('订单第 {seq} 行最多还能发 {max:g}，本次 {q:g}', seq=sl['seq'], max=max(0, left), q=q), headers={'X-Gate': 'over_ship'})
        if sl.get('work_order'):
            wo = db.get(c, 'work_order', sl['work_order'])
            done = float(wo['qty_done'] or 0)
            shipped_wo = c.execute(sa.select(sa.func.coalesce(sa.func.sum(TABLES['delivery_line'].c.qty), 0)).select_from(TABLES['delivery_line']).join(
                TABLES['delivery_order'], TABLES['delivery_order'].c.id == TABLES['delivery_line'].c.delivery_order).where(
                (TABLES['delivery_line'].c.work_order == wo['id']) & (TABLES['delivery_order'].c.status == 'posted'))).scalar() or 0
            if q > done - float(shipped_wo) + 1e-9:
                raise HTTPException(409, _tr('按单生产：工单 {code} 完工 {done:g}，已发 {float:g}，本次 {q:g} 超过可发数量', code=wo['code'], done=done, float=float(shipped_wo), q=q),
                                    headers={'X-Gate': 'mto'})
    need = defaultdict(float)
    for ln in lines:
        need[(ln['material'], ln.get('warehouse') or d['warehouse'], ln.get('lot'))] += ln['qty']
    for (m, wh, lot), q in need.items():
        if not wh:
            raise HTTPException(400, _tr('请选发货仓库'))
        if lot:
            check_lot_out(c, lot=lot, what=_tr('发货'))
        have = core.stock_qty(c, m, wh, lot)
        if have < q - 1e-9 and not allow_neg:
            mc = db.get(c, 'material', m)
            raise HTTPException(409, _tr('库存不足：{code} {name} 在 {code_of}{v} 只有 {have:g}，要发 {q:g}', code=mc['code'], name=mc['name'], code_of=core.code_of(c, 'warehouse', wh), v=_tr(' 批次 ') + lot if lot else '', have=have, q=q),
                                headers={'X-Gate': 'stock'})
    cost.valuate(c)
    tot_cost = 0.0
    sch_t = TABLES['so_schedule']
    cust = db.get(c, 'customer', d['customer']) or {}
    for ln in lines:
        wh = ln.get('warehouse') or d['warehouse']
        uc = cost.avg_cost(c, ln['material'])
        tot_cost += uc * ln['qty']
        db.update(c, 'delivery_line', ln['id'], {'unit_cost': round(uc, 4), 'warehouse': wh}, raw=True)
        move(c, 'out', ln['material'], ln['qty'], wh, lot=ln.get('lot'), ref=d['code'], who=who(user), reason=_tr('销售发货'))
        db.insert(c, 'shipment', {'qty': int(round(ln['qty'])), 'customer': cust.get('short_name') or cust.get('name'), 'work_order': ln.get('work_order'),
                                  'time': clock.now()}, raw=True)
        if ln.get('so_line'):
            sl = db.get(c, 'so_line', ln['so_line'])
            db.update(c, 'so_line', sl['id'], {'qty_shipped': (sl['qty_shipped'] or 0) + ln['qty']}, raw=True)
            left = ln['qty']
            for s in db.rows(c, 'so_schedule', sch_t.c.so_line == sl['id'], order=sch_t.c.due):
                room = (s['qty'] or 0) - (s['qty_shipped'] or 0)
                if room <= 0 or left <= 0:
                    continue
                take = min(room, left)
                left -= take
                late = take if as_date(s['due']) and ship > as_date(s['due']) else 0
                db.update(c, 'so_schedule', s['id'], {'qty_shipped': (s['qty_shipped'] or 0) + take}, raw=True)
                if late:
                    _late_add(c, s['id'], late)
                _ship_on(c, s['id'], ship)
    db.update(c, 'delivery_order', d['id'], {'status': 'posted', 'posted_at': clock.now(), 'posted_by': who(user), 'cost': money(tot_cost), 'ship_date': ship}, raw=True)
    if d.get('sales_order'):
        refresh_status(c, d['sales_order'])
    dd = db.get(c, 'delivery_order', d['id'])
    integration.emit(c, 'delivery.posted', 'delivery_order', d['code'], delivery_payload(c, dd))
    return {'msg': _tr('已发货过账 {code}：{len} 行，价税合计 {total:,.2f}', code=d['code'], len=len(lines), total=dd['total'])}


# 交货计划的"实际发货日 / 逾期发货数"存在 so_schedule.ext（M3 的自定义字段 JSON 列）里会和自定义字段混在一起，这里单独记在 erp_postback
def _late_add(c, sid, q):
    from ..schema import ERP_TABLES
    t = ERP_TABLES['erp_postback']
    key = f'sched_late:{sid}'
    r = c.execute(sa.select(t).where(t.c.key == key)).mappings().first()
    if r:
        c.execute(t.update().where(t.c.id == r['id']).values(value=(r['value'] or 0) + q, updated_at=clock.now()))
    else:
        c.execute(t.insert().values(key=key, value=q, updated_at=clock.now()))


def _ship_on(c, sid, d):
    from ..schema import ERP_TABLES
    t = ERP_TABLES['erp_postback']
    key = f'sched_ship:{sid}'
    v = float(d.toordinal())
    r = c.execute(sa.select(t.c.id).where(t.c.key == key)).first()
    if r:
        c.execute(t.update().where(t.c.id == r.id).values(value=v, updated_at=clock.now()))
    else:
        c.execute(t.insert().values(key=key, value=v, updated_at=clock.now()))


def delivery_payload(c, d):
    cust = db.get(c, 'customer', d['customer']) or {}
    lines = []
    for ln in core.lines_of(c, 'delivery_order', d['id']):
        m = db.get(c, 'material', ln['material']) or {}
        sl = db.get(c, 'so_line', ln['so_line']) if ln.get('so_line') else None
        lines.append({'material': m.get('code'), 'qty': ln['qty'], 'unit': m.get('unit'), 'lot': ln.get('lot'), 'price': ln['price'], 'tax_rate': ln['tax_rate'],
                      'amount': ln['amount'], 'warehouse': core.code_of(c, 'warehouse', ln.get('warehouse') or d['warehouse']),
                      'so_line_seq': sl['seq'] if sl else None, 'work_order': core.code_of(c, 'work_order', ln.get('work_order'))})
    return {'code': d['code'], 'date': db.jsonable(d['ship_date']), 'customer': cust.get('code'), 'customer_name': cust.get('name'),
            'sales_order': core.code_of(c, 'sales_order', d.get('sales_order')), 'amount': d['amount'], 'tax': d['tax'], 'total': d['total'], 'lines': lines}


@action('delivery_order', 'cancel')
def delivery_cancel(c, d, body, user):
    if d['status'] != 'draft':
        raise HTTPException(409, _tr('已过账的发货单不能取消；客户退回请做"销售退货"'))
    db.update(c, 'delivery_order', d['id'], {'status': 'cancelled'}, raw=True)
    return {'msg': _tr('已取消')}


# ───────────────── 退货 ─────────────────
@action('sales_return', 'receive')
def return_receive(c, r, body, user):
    """退货收货：退回入库的进仓库（按原发货成本）；返修 / 报废的开不合格品单（质量处理）。订单"已退"数量累加；
    reship=true 时把已发数量减回去（订单要补发）。"""
    from ..services.warehouse import move
    from . import integration
    if r['status'] != 'draft':
        raise HTTPException(409, _tr('退货单已{e_status}', e_status=core.e_status('sales_return', r)))
    d = as_date(r['return_date']) or today()
    check_open(c, d, _tr('退货收货'))
    lines = core.lines_of(c, 'sales_return', r['id'])
    if not lines:
        raise HTTPException(400, _tr('退货单没有明细'))
    wh = r['warehouse'] or core.wh_of_type(c, 'fg')
    reship = bool(body.get('reship'))
    for ln in lines:
        dl = db.get(c, 'delivery_line', ln['delivery_line']) if ln.get('delivery_line') else None
        if dl:
            done = c.execute(sa.select(sa.func.coalesce(sa.func.sum(TABLES['return_line'].c.qty), 0)).select_from(TABLES['return_line']).join(
                TABLES['sales_return'], TABLES['sales_return'].c.id == TABLES['return_line'].c.sales_return).where(
                (TABLES['return_line'].c.delivery_line == dl['id']) & (TABLES['sales_return'].c.status == 'received'))).scalar() or 0
            if ln['qty'] > dl['qty'] - float(done) + 1e-9:
                raise HTTPException(409, _tr('退货数量 {qty:g} 超过原发货可退的 {v:g}', qty=ln['qty'], v=dl['qty'] - float(done)))
        if r['disposition'] == 'restock':
            move(c, 'in', ln['material'], ln['qty'], wh, lot=ln.get('lot'), ref=r['code'], who=who(user), reason=_tr('销售退货入库'))
        else:
            db.insert(c, 'ncr', {'lot': ln.get('lot'), 'material': ln['material'], 'qty': int(round(ln['qty'])), 'found_at': clock.now(), 'found_by': who(user),
                                 'source': 'manual', 'disposition': 'rework' if r['disposition'] == 'rework' else 'scrap', 'status': 'open',
                                 'station': _tr('客户退货'), 'root_cause': _tr('销售退货 {code}：{v}', code=r['code'], v=r['reason'] or '')}, raw=True)
        if dl and dl.get('so_line'):
            sl = db.get(c, 'so_line', dl['so_line'])
            upd = {'qty_returned': (sl['qty_returned'] or 0) + ln['qty']}
            if reship:
                upd['qty_shipped'] = max(0, (sl['qty_shipped'] or 0) - ln['qty'])
                if upd['qty_shipped'] < sl['qty']:
                    upd['status'] = 'open'
            db.update(c, 'so_line', sl['id'], upd, raw=True)
    db.update(c, 'sales_return', r['id'], {'status': 'received', 'return_date': d, 'warehouse': wh}, raw=True)
    if r.get('sales_order'):
        so = db.get(c, 'sales_order', r['sales_order'])
        if reship and so['status'] == 'shipped':
            db.update(c, 'sales_order', so['id'], {'status': 'partial'}, raw=True)
        refresh_status(c, so['id'])
    rr = db.get(c, 'sales_return', r['id'])
    integration.emit(c, 'return.received', 'sales_return', r['code'], {'code': r['code'], 'customer': core.code_of(c, 'customer', r['customer']),
                                                                       'disposition': r['disposition'], 'total': rr['total'],
                                                                       'delivery_order': core.code_of(c, 'delivery_order', r.get('delivery_order'))})
    return {'msg': _tr('退货已收货（{v}）', v={'restock': _tr('退回入库'), 'rework': _tr('返修'), 'scrap': _tr('报废')}[r['disposition']])}


@action('sales_return', 'cancel')
def return_cancel(c, r, body, user):
    if r['status'] != 'draft':
        raise HTTPException(409, _tr('已收货的退货单不能取消'))
    db.update(c, 'sales_return', r['id'], {'status': 'cancelled'}, raw=True)
    return {'msg': _tr('已取消')}


# ───────────────── 对账单 ─────────────────
def statement(c, customer, period, user=None, save=True):
    s, e = core.period_bounds(period)
    d, r, rc, inv = TABLES['delivery_order'], TABLES['sales_return'], TABLES['ar_receipt'], TABLES['ar_invoice']
    opening = receivable(c, customer, before=s)
    lines = []
    for x in db.rows(c, 'delivery_order', d.c.customer == customer, d.c.status == 'posted', d.c.ship_date >= s, d.c.ship_date < e, order=d.c.ship_date):
        lines.append({'date': db.jsonable(x['ship_date']), 'kind': '发货', 'doc': x['code'], 'debit': x['total'], 'credit': 0,
                      'so': core.code_of(c, 'sales_order', x['sales_order'])})
    for x in db.rows(c, 'sales_return', r.c.customer == customer, r.c.status == 'received', r.c.return_date >= s, r.c.return_date < e, order=r.c.return_date):
        lines.append({'date': db.jsonable(x['return_date']), 'kind': '退货', 'doc': x['code'], 'debit': -x['total'], 'credit': 0})
    for x in db.rows(c, 'ar_receipt', rc.c.customer == customer, rc.c.receipt_date >= s, rc.c.receipt_date < e, order=rc.c.receipt_date):
        lines.append({'date': db.jsonable(x['receipt_date']), 'kind': '收款', 'doc': x['code'], 'debit': 0, 'credit': x['amount']})
    lines.sort(key=lambda z: (z['date'] or '', z['doc']))
    bal = opening
    for z in lines:
        bal = money(bal + z['debit'] - z['credit'])
        z['balance'] = bal
    invoiced = money(c.execute(sa.select(sa.func.coalesce(sa.func.sum(inv.c.total), 0)).where(
        (inv.c.customer == customer) & (inv.c.status != 'void') & (inv.c.invoice_date >= s) & (inv.c.invoice_date < e))).scalar())
    res = {'customer': customer, 'period': period, 'start': s, 'end': e - timedelta(days=1), 'opening': money(opening),
           'shipped': money(sum(z['debit'] for z in lines if z['kind'] == '发货')), 'returned': money(-sum(z['debit'] for z in lines if z['kind'] == '退货')),
           'received': money(sum(z['credit'] for z in lines)), 'invoiced': invoiced, 'closing': money(bal), 'lines': lines}
    if not save:
        return res
    t = TABLES['customer_statement']
    old = db.rows(c, 'customer_statement', t.c.customer == customer, t.c.period == period)
    if old and old[0]['status'] == 'confirmed':
        raise HTTPException(409, _tr('{period} 的对账单 {code} 客户已确认，不能重新生成', period=period, code=old[0]['code']))
    if old:
        db.update(c, 'customer_statement', old[0]['id'], {**res, 'status': 'draft'}, raw=True)
        return old[0]['id']
    return db.insert(c, 'customer_statement', {**res, 'status': 'draft'}, raw=True)


@action('customer_statement', 'send')
def st_send(c, s, body, user):
    db.update(c, 'customer_statement', s['id'], {'status': 'sent'}, raw=True)
    return {'msg': _tr('已标记发给客户')}


@action('customer_statement', 'confirm')
def st_confirm(c, s, body, user):
    db.update(c, 'customer_statement', s['id'], {'status': 'confirmed', 'confirmed_by': body.get('by') or who(user), 'confirmed_at': clock.now()}, raw=True)
    return {'msg': _tr('客户已确认')}


@action('customer_statement', 'dispute')
def st_dispute(c, s, body, user):
    db.update(c, 'customer_statement', s['id'], {'status': 'disputed', 'note': body.get('note') or ''}, raw=True)
    return {'msg': _tr('已记录异议')}


# ───────────────── 订单状态看板 ─────────────────
def wo_eta(c, wo):
    opt = TABLES['wo_op']
    ends = [o['plan_end'] for o in db.rows(c, 'wo_op', opt.c.work_order == wo['id']) if o['plan_end']]
    return max(ends) if ends else None


def board(c, user=None, days=90):
    """准时交付率（近 days 天到期的交货计划）、未交订单、有风险的订单（按交货计划逐条算：库存 → 按单工单的排产完工时间 → 在制工单 → MRP 计划）。"""
    from ..schema import ERP_TABLES
    now = today()
    so, sl, sch = TABLES['sales_order'], TABLES['so_line'], TABLES['so_schedule']
    cond = so.c.status.in_(('confirmed', 'partial', 'shipped', 'closed', 'credit_hold'))
    sc = perms.scope_cond(user, 'sales_order', so)
    if sc is not None:
        cond = cond & sc
    q = sa.select(sch, sl.c.material, sl.c.price, sl.c.tax_rate, sl.c.work_order, sl.c.seq, sl.c.status.label('line_status'), so.c.code.label('so_code'),
                  so.c.customer, so.c.status.label('so_status'), so.c.id.label('so_id')).join(sl, sl.c.id == sch.c.so_line).join(so, so.c.id == sl.c.sales_order).where(
        cond & (sl.c.status != 'cancelled'))
    rows = [dict(r) for r in c.execute(q.order_by(sch.c.due, sch.c.id)).mappings()]
    pb = ERP_TABLES['erp_postback']
    lates = {k: v for k, v in c.execute(sa.select(pb.c.key, pb.c.value).where(pb.c.key.like('sched_late:%')))}
    ships = {k: v for k, v in c.execute(sa.select(pb.c.key, pb.c.value).where(pb.c.key.like('sched_ship:%')))}
    lo = now - timedelta(days=days)
    due_win = [r for r in rows if as_date(r['due']) and lo <= as_date(r['due']) <= now]
    shipped_q = sum(min(r['qty_shipped'] or 0, r['qty']) for r in due_win)
    late_q = sum(lates.get(f"sched_late:{r['id']}", 0) for r in due_win)
    due_q = sum(r['qty'] for r in due_win)
    ontime_q = shipped_q - late_q
    otd = (ontime_q / due_q) if due_q else None
    mats = {m['id']: m for m in db.rows(c, 'material')}
    custs = {x['id']: x for x in db.rows(c, 'customer')}
    open_ = [r for r in rows if r['so_status'] in ('confirmed', 'partial', 'credit_hold') and r['line_status'] == 'open' and (r['qty'] - (r['qty_shipped'] or 0)) > 1e-9]
    # 供应：成品库存按交期先后分配；然后是在制工单（按排产完工时间）
    fg = core.wh_of_type(c, 'fg')
    avail = defaultdict(float)
    for m in {r['material'] for r in open_}:
        avail[m] = core.stock_qty(c, m, fg) if fg else core.stock_qty(c, m)
    wot = TABLES['work_order']
    wo_sup = defaultdict(list)
    for w in db.rows(c, 'work_order', wot.c.status.in_(('draft', 'released', 'in_progress')), order=wot.c.due):
        eta = wo_eta(c, w) if w['status'] != 'draft' else None
        wo_sup[w['product']].append({'id': w['id'], 'code': w['code'], 'left': max(0.0, (w['qty'] or 0) - (w['qty_done'] or 0)), 'eta': eta,
                                     'status': w['status'], 'so_line': w.get('so_line')})
    risk_rows = []
    backlog_val = 0.0
    for r in open_:
        left = r['qty'] - (r['qty_shipped'] or 0)
        val = left * (r['price'] or 0) * (1 + (r['tax_rate'] or 0) / 100)
        backlog_val += val
        due = as_date(r['due'])
        take = min(avail[r['material']], left)
        avail[r['material']] -= take
        short = left - take
        eta, src, reason = None, _tr('库存'), ''
        if short > 1e-9:
            sups = sorted(wo_sup[r['material']], key=lambda w: (w['so_line'] != r['so_line'], w['eta'] or clock.now() + timedelta(days=999)))
            for w in sups:
                if short <= 1e-9:
                    break
                if w['left'] <= 0:
                    continue
                t = min(w['left'], short)
                w['left'] -= t
                short -= t
                src = _tr('工单 {code}', code=w['code'])
                if w['status'] == 'draft':
                    reason = _tr('工单 {code} 还没下达', code=w['code'])
                    eta = None
                    break
                eta = max(eta, w['eta']) if eta and w['eta'] else (w['eta'] or eta)
            if short > 1e-9:
                reason = reason or _tr('还差 {short:g} {v}没有库存也没有工单（跑一次 MRP）', short=short, v=mats.get(r['material'], {}).get('unit') or '')
                src = _tr('无')
        eta_d = as_date(eta) if eta else None
        if due and due < now:
            level = OVERDUE
            reason = reason or _tr('交期 {due} 已过，还差 {left:g}', due=due, left=left)
        elif reason:
            level = AT_RISK if not due or (due - now).days <= 14 else WATCH
        elif eta_d and due and eta_d > due:
            level = AT_RISK
            reason = _tr('{src} 预计 {eta_d} 完工，晚于交期 {due}（{days} 天）', src=src, eta_d=eta_d, due=due, days=(eta_d - due).days)
        elif due and (due - now).days <= 3 and src not in _i18n.variants('库存'):
            level = WATCH
            reason = _tr('{days} 天后到期，{src} 预计 {v} 完工', days=(due - now).days, src=src, v=eta_d or '—')
        else:
            level = 'ok'
        m = mats.get(r['material'], {})
        cu = custs.get(r['customer'], {})
        risk_rows.append({'schedule': r['id'], 'sales_order': r['so_id'], 'so_code': r['so_code'], 'seq': r['seq'], 'customer': cu.get('short_name') or cu.get('name'),
                          'customer_id': r['customer'], 'material': r['material'], 'material_label': f"{m.get('code', '')} {m.get('name', '')}".strip(),
                          'due': db.jsonable(due), 'left': left, 'value': money(val), 'eta': db.jsonable(eta_d), 'supply': src, 'risk': level,
                          'risk_label': RISK_CN[level], 'reason': reason})
    at_risk = [x for x in risk_rows if x['risk'] != 'ok']
    order = {OVERDUE: 0, AT_RISK: 1, WATCH: 2}
    at_risk.sort(key=lambda x: (order[x['risk']], x['due'] or ''))
    by_cust = defaultdict(lambda: {'qty': 0.0, 'value': 0.0, 'lines': 0})
    for x in risk_rows:
        k = x['customer'] or '—'
        by_cust[k]['qty'] += x['left']
        by_cust[k]['value'] += x['value']
        by_cust[k]['lines'] += 1
    weeks = defaultdict(float)
    for x in risk_rows:
        d0 = as_date(x['due']) or now
        wk = d0 - timedelta(days=d0.weekday())
        weeks[str(max(wk, now - timedelta(days=now.weekday() + 7)))] += x['value']
    per_month = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        d0 = as_date(r['due'])
        if d0 and d0 <= now and d0 >= now - timedelta(days=180):
            k = f'{d0.year}-{d0.month:02d}'
            per_month[k][0] += r['qty']
            per_month[k][1] += min(r['qty_shipped'] or 0, r['qty']) - lates.get(f"sched_late:{r['id']}", 0)
    return {'otd': otd, 'otd_window': days, 'due_qty': due_q, 'ontime_qty': ontime_q, 'late_qty': late_q,
            'backlog_lines': len(risk_rows), 'backlog_value': money(backlog_val), 'backlog_orders': len({x['sales_order'] for x in risk_rows}),
            'at_risk': at_risk, 'overdue': sum(1 for x in at_risk if x['risk'] == OVERDUE), 'risk_count': len(at_risk),
            'by_customer': sorted([{'customer': k, **{kk: (money(vv) if kk == 'value' else vv) for kk, vv in v.items()}} for k, v in by_cust.items()],
                                  key=lambda z: -z['value']),
            'by_week': [{'week': k, 'value': money(v)} for k, v in sorted(weeks.items())][:12],
            'otd_trend': [{'month': k, 'otd': (v[1] / v[0]) if v[0] else None} for k, v in sorted(per_month.items())],
            'open': risk_rows, 'last_ship': {k.split(':')[1]: v for k, v in ships.items()}}


# ───────────────── 接口 ─────────────────
@router.get('/api/sales/board')
def board_api(days: int = 90, user=Depends(require('sales.sales_order.read'))):
    with db.engine.connect() as c:
        b = board(c, user, days)
    b.pop('last_ship', None)
    return b


@router.get('/api/sales/price')
def price_api(customer: int, material: int, qty: float = 0, user=Depends(require('sales.sales_order.read'))):
    with db.engine.connect() as c:
        p, tr, src = core.sales_price(c, customer, material, qty)
        return {'price': p, 'tax_rate': tr if tr is not None else core.default_tax(c, 'sales_order', {'customer': customer}), 'source': src}


@router.get('/api/sales/credit/{customer}')
def credit_api(customer: int, user=Depends(require('sales.sales_order.read'))):
    with db.engine.connect() as c:
        return credit(c, customer)


@router.post('/api/sales/orders/{sid}/change')
def change_api(sid: int, body: dict = Body(...), user=Depends(require('sales.sales_order.update'))):
    with db.engine.begin() as c:
        so = db.get(c, 'sales_order', sid)
        if not so or not perms.in_scope(user, 'sales_order', so):
            raise HTTPException(404, _tr('订单不存在'))
    from . import flow_gate
    pending = flow_gate('sales_order', 'change', record=so, body=body, user=user)      # M3 审批流程（挂了才生效）
    if pending:
        return pending
    with db.engine.begin() as c:
        return change_order(c, db.get(c, 'sales_order', sid), body, user)


@router.post('/api/sales/orders/{sid}/delivery')
def make_delivery_api(sid: int, body: dict = Body(default={}), user=Depends(require('sales.delivery_order.create'))):
    with db.engine.begin() as c:
        so = db.get(c, 'sales_order', sid)
        if not so or not perms.in_scope(user, 'sales_order', so):
            raise HTTPException(404, _tr('订单不存在'))
        only = {int(k): v for k, v in (body.get('lines') or {}).items()} if body.get('lines') else None
        did = delivery_from_order(c, sid, user, body.get('warehouse'), body.get('ship_date'), only)
        return {'ok': True, 'id': did, 'code': core.code_of(c, 'delivery_order', did)}


@router.post('/api/sales/statements')
def statements_api(body: dict = Body(...), user=Depends(require('sales.customer_statement.read'))):
    """生成对账单：{period: 'YYYY-MM', customer?: id}（不填客户 = 本期有往来的全部客户）。"""
    period = body.get('period') or core.period_of(today())
    perms.check(user, 'sales.customer_statement.send')
    with db.engine.begin() as c:
        custs = [int(body['customer'])] if body.get('customer') else sorted(_active_customers(c, period))
        ids = [statement(c, cu, period, user) for cu in custs]
    return {'ok': True, 'ids': ids, 'count': len(ids)}


def _active_customers(c, period):
    s, e = core.period_bounds(period)
    d, rc = TABLES['delivery_order'], TABLES['ar_receipt']
    out_ = set(c.execute(sa.select(d.c.customer).where((d.c.status == 'posted') & (d.c.ship_date >= s) & (d.c.ship_date < e))).scalars())
    out_ |= set(c.execute(sa.select(rc.c.customer).where((rc.c.receipt_date >= s) & (rc.c.receipt_date < e))).scalars())
    for cu in db.rows(c, 'customer'):
        if abs(receivable(c, cu['id'], before=s)) > 0.005:
            out_.add(cu['id'])
    return out_
