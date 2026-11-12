"""内置 ERP 演示数据（示范工厂：转向器壳体铸造 + 机加）：客户、供应商与供货清单、价格表、客户零件号、物料计划参数、
销售订单（三张演示工单按单生产 + 一张没工单的，给 MRP 用）、在途采购订单、需求预测、标准成本。可重复执行（已有的跳过）。"""
from datetime import timedelta

from . import db
from .db import clock


def _ensure(c, entity, key, row):
    r = db.get(c, entity, **{key: row[key]})
    if r:
        fill = {k: v for k, v in row.items() if r.get(k) in (None, '') and v not in (None, '')}
        if fill:
            db.update(c, entity, r['id'], fill, raw=True)
        return r['id']
    return db.insert(c, entity, row, raw=True)


def run(c):
    from .erp import core, cost, sales
    mats = {m['code']: m for m in db.rows(c, 'material')}
    if 'SGH-450' not in mats:
        return
    today = clock.now().date()
    custs = {}
    for code, name, short, limit, terms, sm in (('C001', '北辰重型汽车有限公司', '北辰重卡', 800000, 60, '王磊'), ('C002', '西岭重型汽车有限公司', '西岭重卡', 600000, 60, '王磊'),
                                                ('C003', '长河商用车有限公司', '长河商用车', 500000, 90, '赵敏')):
        custs[short] = _ensure(c, 'customer', 'code', {'code': code, 'name': name, 'short_name': short, 'credit_limit': limit, 'terms_days': terms, 'pay_type': 'net',
                                                       'tax_rate': 13, 'salesman': sm, 'contact': '采购部', 'active': True})
    sups = {}
    for code, name, short, cat, items in (('S001', '某生铁贸易有限公司', '生铁供应', 'raw', [('CM-PIG', 7, 10)]),
                                          ('S002', '某再生资源回收有限公司', '废钢供应', 'raw', [('CM-STEEL', 5, 10)]),
                                          ('S003', '某冶金材料有限公司', '冶金辅料', 'aux', [('AUX-NODU', 10, 1), ('AUX-INOC', 10, 1), ('CM-FESI', 10, 1), ('CM-CARB', 10, 1)]),
                                          ('S004', '某铸造材料有限公司', '铸材', 'aux', [('AUX-SAND', 3, 20), ('AUX-SHOT', 7, 1)]),
                                          ('S005', '某金属材料有限公司', '金属材料（试用）', 'raw', [('CM-PIG', 10, 20)])):
        sid = _ensure(c, 'supplier', 'code', {'code': code, 'name': name, 'short_name': short, 'category': cat, 'terms_days': 30, 'tax_rate': 13, 'pay_type': 'net',
                                              'qual': 'trial' if code == 'S005' else 'approved', 'active': True, 'contact': '销售部'})
        sups[code] = sid
        for mc, lead, moq in items:
            m = mats.get(mc)
            if m and not db.get(c, 'supplier_material', supplier=sid, material=m['id']):
                db.insert(c, 'supplier_material', {'supplier': sid, 'material': m['id'], 'price': round((m['price'] or 0) * (1.03 if code == 'S005' else 1), 2),
                                                   'tax_rate': 13, 'lead_days': lead, 'moq': moq, 'approved': code != 'S005', 'preferred': code != 'S005',
                                                   'iqc_exempt': mc in ('AUX-SAND',)}, raw=True)
    fg = mats['SGH-450']
    if not db.get(c, 'price_list', code='PL-STD'):
        pl = db.insert(c, 'price_list', {'code': 'PL-STD', 'name': '标准价格表', 'currency': 'CNY', 'status': 'active'}, raw=True)
        db.insert(c, 'price_item', {'price_list': pl, 'material': fg['id'], 'min_qty': 0, 'price': 380, 'tax_rate': 13}, raw=True)
        pl2 = db.insert(c, 'price_list', {'code': 'PL-C001', 'name': '北辰重卡 年度价格', 'customer': custs['北辰重卡'], 'currency': 'CNY', 'status': 'active'}, raw=True)
        db.insert(c, 'price_item', {'price_list': pl2, 'material': fg['id'], 'min_qty': 0, 'price': 375, 'tax_rate': 13}, raw=True)
        db.insert(c, 'price_item', {'price_list': pl2, 'material': fg['id'], 'min_qty': 1000, 'price': 368, 'tax_rate': 13}, raw=True)
    for short, no in (('北辰重卡', 'JF-3401010-A'), ('西岭重卡', 'SX-DZ95189-450'), ('长河商用车', 'DF-3401N-010')):
        if not db.get(c, 'customer_part', customer=custs[short], material=fg['id']):
            db.insert(c, 'customer_part', {'customer': custs[short], 'material': fg['id'], 'customer_part_no': no, 'customer_part_name': '转向器壳体'}, raw=True)
    for mc, mb, lead, lot, mult in (('SGH-450', 'make', 3, 0, 0), ('SGH-450-B', 'make', 2, 100, 50), ('CM-PIG', 'buy', 7, 10, 5), ('CM-STEEL', 'buy', 5, 10, 5),
                                    ('AUX-NODU', 'buy', 10, 1, 0.5), ('AUX-SAND', 'buy', 3, 20, 10), ('AUX-SHOT', 'buy', 7, 1, 0.5), ('CM-RET', 'make', 0, 0, 0)):
        m = mats.get(mc)
        if m and not db.get(c, 'mat_plan', material=m['id']):
            db.insert(c, 'mat_plan', {'material': m['id'], 'make_buy': mb, 'lead_days': lead, 'min_lot': lot, 'lot_multiple': mult}, raw=True)
    if not db.rows(c, 'sales_order', limit=1):
        wt = core.TABLES['work_order']
        for w in db.rows(c, 'work_order', wt.c.product == fg['id'], order=wt.c.id, limit=3):
            cid = custs.get(w['customer'])
            if not cid:
                continue
            due = (w['due'] or clock.now()).date() + timedelta(days=1)
            sid = core.save_doc(c, 'sales_order', {'customer': cid, 'order_date': today - timedelta(days=3), 'customer_po': f"PO-{w['customer'][:2]}-{w['id']:04d}",
                                                   'mto': True, 'salesman': '王磊', 'lines': [{'material': fg['id'], 'qty': w['qty'], 'due': due}]}, None)
            ln = core.lines_of(c, 'sales_order', sid)[0]
            db.update(c, 'sales_order', sid, {'status': 'confirmed', 'credit_note': '演示数据'}, raw=True)
            db.update(c, 'so_line', ln['id'], {'work_order': w['id']}, raw=True)
            db.update(c, 'work_order', w['id'], {'sales_order': sid, 'so_line': ln['id']}, raw=True)
        sid = core.save_doc(c, 'sales_order', {'customer': custs['北辰重卡'], 'order_date': today - timedelta(days=1), 'customer_po': 'PO-YQ-2611-07', 'salesman': '王磊',
                                               'lines': [{'material': fg['id'], 'qty': 400, 'due': today + timedelta(days=9)}]}, None)
        db.update(c, 'sales_order', sid, {'status': 'confirmed', 'credit_note': '演示数据'}, raw=True)
        ln = core.lines_of(c, 'sales_order', sid)[0]
        sales.set_schedule(c, db.get(c, 'sales_order', sid), ln, [{'due': today + timedelta(days=9), 'qty': 200}, {'due': today + timedelta(days=16), 'qty': 200}])
        qid = core.save_doc(c, 'quotation', {'customer': custs['长河商用车'], 'quote_date': today, 'salesman': '赵敏',
                                             'lines': [{'material': fg['id'], 'qty': 1200, 'lead_days': 21}]}, None)
        db.update(c, 'quotation', qid, {'status': 'sent'}, raw=True)
    if not db.rows(c, 'forecast', limit=1):
        for wk in (3, 4, 5):
            d = today + timedelta(days=7 * wk)
            db.insert(c, 'forecast', {'material': fg['id'], 'due': d - timedelta(days=d.weekday()), 'qty': 500, 'customer': custs['西岭重卡'], 'remark': '客户滚动预测'},
                      raw=True)
    if not db.rows(c, 'purchase_order', limit=1) and 'CM-PIG' in mats:
        pid = core.save_doc(c, 'purchase_order', {'supplier': sups['S001'], 'order_date': today - timedelta(days=2), 'buyer': '采购员',
                                                  'lines': [{'material': mats['CM-PIG']['id'], 'qty': 50, 'due': today + timedelta(days=5)}]}, None)
        db.update(c, 'purchase_order', pid, {'status': 'sent'}, raw=True)
    core.ensure_period(c, core.period_of(today))
    if not db.rows(c, 'std_cost', limit=1):
        cost.roll_up(c)
