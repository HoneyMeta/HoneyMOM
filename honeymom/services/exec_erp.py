"""M5 ↔ 内置 ERP（M4，docs/dev/erp.md）的衔接：按表是否存在决定（外部 ERP 模式或 ERP 表还没建时全部跳过，按单号关联）。

- 发货单 delivery_order / delivery_line：装车单按发货单号关联（ship_load.delivery_code / delivery_id），装车时校验数量；
  发运确认时发货单还是草稿 → 代为执行 M4 的"发货过账"（扣库存、记发运记录、订单已发数量都由 M4 做），M5 不再重复出库。
- 供应商 supplier（M3 主数据）：委外单 supplier 填编码或名称，自动对上 supplier_id。
- 请购 purchase_req（source=spare）：备件补货建议转请购；备件在物料主数据里没有时按备件编码建一条"易耗品"物料。
- 退货 sales_return（RMA）：客诉"建退货单"生成草稿，complaint_code 关联。
- 应付：委外结算金额经 /api/outsource/ap 给财务登记采购发票（M4 的 ap_invoice 按供应商发票登记）。
"""
from ..i18n import _tr
from datetime import timedelta

from .. import db
from ..crud import ACTIONS, run_action
from ..db import clock
from ..schema import TABLES


def has(*tables) -> bool:
    return all(t in TABLES for t in tables)


def _partner(c, entity, key):
    if not has(entity) or key in (None, ''):
        return None
    if isinstance(key, int) or str(key).isdigit():
        r = db.get(c, entity, int(key))
        if r:
            return r
    return db.get(c, entity, code=str(key)) or db.get(c, entity, name=str(key))


def supplier_id(c, supplier) -> int | None:
    r = _partner(c, 'supplier', supplier)
    return r['id'] if r else None


def delivery(c, code) -> dict | None:
    """发货单（M4）：{id, code, status, customer, customer_code, ship_to, lines: [{material, qty, lot}]}；没有 → None。"""
    if not has('delivery_order', 'delivery_line') or not code:
        return None
    d = db.get(c, 'delivery_order', code=str(code).strip())
    if not d:
        return None
    cu = db.get(c, 'customer', d['customer']) if d.get('customer') and has('customer') else None
    lines = [{'material': x['material'], 'qty': x['qty'] or 0, 'lot': x.get('lot')} for x in db.rows(c, 'delivery_line', delivery_order=d['id'])]
    return {'id': d['id'], 'code': d['code'], 'status': d['status'], 'customer': (cu or {}).get('name') or '', 'customer_code': (cu or {}).get('code') or '',
            'ship_to': d.get('ship_to') or (cu or {}).get('address') or '', 'lines': lines, 'qty': sum(x['qty'] for x in lines)}


def post_delivery(c, code, user) -> str:
    """发运确认时：发货单是草稿 → 执行 M4 的发货过账。返回 'posted'（M4 已扣库存、记发运）/ 'already'（之前已过账）/ 'none'（没有发货单，M5 自己出库）。"""
    if not has('delivery_order'):
        return 'none'
    d = db.get(c, 'delivery_order', code=str(code or '').strip()) if code else None
    if not d:
        return 'none'
    if d['status'] == 'posted':
        return 'already'
    if d['status'] != 'draft' or ('delivery_order', 'post') not in ACTIONS:
        return 'none'
    run_action(c, 'delivery_order', d, 'post', {}, user)
    return 'posted'


def spare_material(c, sp) -> int:
    m = db.get(c, 'material', code=sp['code'])
    if m:
        return m['id']
    return db.insert(c, 'material', {'code': sp['code'], 'name': sp['name'], 'spec': sp.get('spec'), 'unit': sp.get('unit') or _tr('个'), 'mtype': 'consumable',
                                     'price': sp.get('price')}, raw=True)


def purchase_requests(c, lines, user) -> list[str]:
    """备件补货 → 请购（M4 purchase_req，source=spare）。返回请购单号；没有请购表 → []。"""
    if not has('purchase_req'):
        return []
    codes = []
    for x in lines:
        sp = db.get(c, 'spare_part', x['id'])
        mid = spare_material(c, sp)
        rid = db.insert(c, 'purchase_req', {'material': mid, 'qty': x['suggest'], 'need_date': (clock.now() + timedelta(days=sp['lead_days'] or 7)).date(),
                                            'supplier': supplier_id(c, sp['supplier']), 'requester': (user or {}).get('name') or (user or {}).get('username'),
                                            'reason': _tr('备件补货：库存 {qty:g} 低于最低 {min_qty:g}', qty=x['qty'], min_qty=x['min_qty']), 'source': 'spare', 'status': 'submitted'}, raw=False)
        codes.append(db.get(c, 'purchase_req', rid)['code'])
    return codes


def rma(c, cm, user) -> dict | None:
    """客诉 → 退货单草稿（RMA）。"""
    if not has('sales_return', 'customer'):
        return None
    cu = _partner(c, 'customer', cm['customer'])
    if not cu:
        return {'error': _tr('客户主数据里没有“{customer}”，先建客户再建退货单', customer=cm['customer'])}
    d = db.get(c, 'delivery_order', code=cm['delivery_code']) if cm.get('delivery_code') and has('delivery_order') else None
    rid = db.insert(c, 'sales_return', {'customer': cu['id'], 'delivery_order': d['id'] if d else None, 'disposition': 'rework', 'status': 'draft',
                                        'reason': _tr('客诉 {code}：{v}', code=cm['code'], v=(cm['description'] or '')[:80]), 'complaint_code': cm['code']}, raw=False)
    return db.get(c, 'sales_return', rid)


def returns_of(c, code) -> list:
    if not has('sales_return'):
        return []
    return [{'id': r['id'], 'code': r['code'], 'status': r['status'], 'disposition': r['disposition'], 'total': r.get('total')}
            for r in db.rows(c, 'sales_return', complaint_code=code)]
