"""内置 ERP 公共部分：金额 / 税额、单据（表头 + 明细）的保存与合计、会计期间锁、ERP 模式与主数据归属、价格查找。

单据配置 DOCS：表头实体 → 明细实体、外键、日期字段、往来单位字段。通用接口 /api/erp/doc/<表头实体>（services 在 sales.py / purchase.py /
finance.py 里补各自的默认值和校验，见 KINDS）。"""
from ..i18n import _tr
import json
from datetime import date, datetime, timedelta

import sqlalchemy as sa
from fastapi import HTTPException

from .. import db
from ..db import clock
from ..schema import BY_NAME, TABLES

DOCS = {
    'quotation': dict(line='quotation_line', fk='quotation', date='quote_date', partner='customer', price=True),
    'sales_order': dict(line='so_line', fk='sales_order', date='order_date', partner='customer', price=True),
    'delivery_order': dict(line='delivery_line', fk='delivery_order', date='ship_date', partner='customer', price=True),
    'sales_return': dict(line='return_line', fk='sales_return', date='return_date', partner='customer', price=True),
    'purchase_order': dict(line='po_line', fk='purchase_order', date='order_date', partner='supplier', price=True),
    'purchase_receipt': dict(line='receipt_line', fk='purchase_receipt', date='receive_date', partner='supplier', price=True),
    'supplier_return': dict(line='supplier_return_line', fk='supplier_return', date='return_date', partner='supplier', price=True),
    'ar_invoice': dict(line='ar_invoice_line', fk='invoice', date='invoice_date', partner='customer', price=False),
    'ap_invoice': dict(line='ap_invoice_line', fk='invoice', date='invoice_date', partner='supplier', price=False),
}
# 单据类型 → 保存前 / 保存后的业务钩子（各模块登记）：fn(c, header, lines, user, is_new)
BEFORE_SAVE: dict = {}
AFTER_SAVE: dict = {}
EDITABLE = {'quotation': ('draft',), 'sales_order': ('draft', 'credit_hold'), 'delivery_order': ('draft',), 'sales_return': ('draft',),
            'purchase_order': ('draft',), 'purchase_receipt': ('draft',), 'supplier_return': ('draft',), 'ar_invoice': ('open',), 'ap_invoice': ('open',)}


def money(x) -> float:
    return round(float(x or 0) + 1e-9, 2)


def qty(x) -> float:
    return round(float(x or 0), 4)


def today() -> date:
    return clock.now().date()


def as_date(v) -> date | None:
    if v is None or v == '':
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    d = db.parse_dt(v)
    return d.date() if d else None


def period_of(d) -> str:
    d = as_date(d) or today()
    return f'{d.year:04d}-{d.month:02d}'


def period_bounds(code: str):
    y, m = int(code[:4]), int(code[5:7])
    s = date(y, m, 1)
    e = date(y + (m == 12), m % 12 + 1, 1)
    return s, e


def ensure_period(c, code: str) -> dict:
    p = db.get(c, 'fin_period', code=code)
    if p:
        return p
    s, e = period_bounds(code)
    rid = db.insert(c, 'fin_period', {'code': code, 'start': s, 'end': e - timedelta(days=1), 'status': 'open'}, raw=True)
    return db.get(c, 'fin_period', rid)


def period_closed(c, d) -> dict | None:
    p = db.get(c, 'fin_period', code=period_of(d))
    return p if p and p['status'] == 'closed' else None


def check_open(c, d, what='过账'):
    """业务日期所在的会计期间已结账：不能再过账（409）。反结账要成本核算的"反结账"权限。"""
    p = period_closed(c, d)
    if p:
        raise HTTPException(409, _tr('{code} 已月末结账，不能{what}（业务日期 {as_date}）；确需更改请先反结账', code=p['code'], what=what, as_date=as_date(d)), headers={'X-Gate': 'period_closed'})


def setting(c, key, default=None):
    return db.setting(c, key, default)


def setting_json(c, key, default=None):
    raw = db.setting(c, key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


# ───────────────── ERP 模式与主数据归属 ─────────────────
OWN_KEYS = {'customer': '客户', 'supplier': '供应商', 'material': '物料', 'bom': 'BOM', 'sales_order': '销售订单', 'purchase_order': '采购订单',
            'work_order': '生产工单'}
POSTBACK_KEYS = {'completion': '完工入库', 'issue': '生产领料', 'receipt': '采购收货', 'delivery': '销售发货', 'stock': '库存', 'return': '退货'}
# 外部 ERP 模式的默认：主数据和订单由 ERP 做主，MES 管工单执行；回传完工 / 领料 / 收货 / 发货 / 库存
DEFAULT_EXTERNAL = {'owner': {'customer': 'erp', 'supplier': 'erp', 'material': 'erp', 'bom': 'erp', 'sales_order': 'erp', 'purchase_order': 'erp',
                              'work_order': 'mes'},
                    'postback': {k: True for k in POSTBACK_KEYS}}
_mode_cache = {'t': 0.0, 'v': None}
# 主数据归属影响的实体（实体 → 归属键）：外部 ERP 做主时，界面上不能新增 / 修改 / 删除，只能经接口 / 中间库 / 对接器同步
OWNED_ENTITIES = {'customer': 'customer', 'customer_contact': 'customer', 'supplier': 'supplier', 'material': 'material', 'bom': 'bom',
                  'sales_order': 'sales_order', 'so_line': 'sales_order', 'purchase_order': 'purchase_order', 'po_line': 'purchase_order',
                  'work_order': 'work_order'}


def mode_cfg(c=None) -> dict:
    """{'mode': 'builtin'|'external', 'owner': {键: 'mes'|'erp'}, 'postback': {键: bool}}（2 秒缓存）。"""
    import time
    if c is None and _mode_cache['v'] is not None and time.time() - _mode_cache['t'] < 2:
        return _mode_cache['v']

    def load(cc):
        m = db.setting(cc, 'erp_mode', 'builtin') or 'builtin'
        cfg = setting_json(cc, 'erp_ownership', None) or {}
        own = {**DEFAULT_EXTERNAL['owner'], **(cfg.get('owner') or {})}
        pb = {**DEFAULT_EXTERNAL['postback'], **(cfg.get('postback') or {})}
        if m != 'external':
            own = {k: 'mes' for k in own}
        return {'mode': m, 'owner': own, 'postback': pb}
    if c is not None:
        return load(c)
    with db.engine.connect() as cc:
        v = load(cc)
    _mode_cache.update(t=time.time(), v=v)
    return v


def invalidate_mode():
    _mode_cache['v'] = None


def erp_owns(entity: str, c=None) -> bool:
    k = OWNED_ENTITIES.get(entity, entity)
    cfg = mode_cfg(c)
    return cfg['mode'] == 'external' and cfg['owner'].get(k) == 'erp'


def owned_msg(entity: str) -> str:
    k = OWNED_ENTITIES.get(entity, entity)
    return _tr('外部 ERP 模式：{get}由 ERP 做主，请在 ERP 里维护（MES 通过接口 / 中间库 / 对接器同步）', get=OWN_KEYS.get(k, BY_NAME[entity].label if entity in BY_NAME else entity))


def check_owned(entity: str, user, c=None):
    """界面用户改 ERP 做主的数据 → 409。接口（API 密钥）、同步作业不受限。"""
    from .. import context
    if user is None or user.get('is_api_key') or context.current_source() in ('api', 'system', 'import'):
        return                      # 系统内部（同步作业 / 对接器）、API 密钥、导入：不拦
    if erp_owns(entity, c):
        raise HTTPException(409, owned_msg(entity), headers={'X-Gate': 'erp_owned'})


# ───────────────── 往来单位、价格 ─────────────────
def partner(c, kind, pid):
    return db.get(c, 'customer' if kind == 'customer' else 'supplier', int(pid)) if pid else None


def default_tax(c, ent, header) -> float:
    p = partner(c, DOCS[ent]['partner'], header.get(DOCS[ent]['partner']))
    tr = p.get('tax_rate') if p else None
    return float(tr if tr not in (None, '') else 13)


def sales_price(c, customer, material, q=0, on=None):
    """销售价：客户专用价格表 → 通用价格表（生效期内、起订量满足的最大档）→ 物料单价。返回 (价, 税率|None, 来源)。"""
    on = as_date(on) or today()
    pl, pi = TABLES['price_list'], TABLES['price_item']
    q_ = float(q or 0)
    for cust in ([int(customer)] if customer else []) + [None]:
        cond = (pl.c.status == 'active') & (pi.c.material == int(material)) & (sa.func.coalesce(pi.c.min_qty, 0) <= q_)
        cond = cond & (pl.c.customer == cust if cust else pl.c.customer.is_(None))
        cond = cond & sa.or_(pl.c.valid_from.is_(None), pl.c.valid_from <= on) & sa.or_(pl.c.valid_to.is_(None), pl.c.valid_to >= on)
        r = c.execute(sa.select(pi.c.price, pi.c.tax_rate, pl.c.code).join(pl, pl.c.id == pi.c.price_list).where(cond)
                      .order_by(sa.func.coalesce(pi.c.min_qty, 0).desc()).limit(1)).first()
        if r:
            return float(r.price), r.tax_rate, _tr('价格表 {code}', code=r.code)
    m = db.get(c, 'material', int(material))
    return float((m or {}).get('price') or 0), None, _tr('物料单价')


def supplier_item(c, supplier, material):
    sm = TABLES['supplier_material']
    r = c.execute(sa.select(sm).where((sm.c.supplier == int(supplier)) & (sm.c.material == int(material))).limit(1)).mappings().first()
    return dict(r) if r else None


def best_supplier(c, material):
    """首选合格供方 → 任一合格供方（价低优先）。"""
    sm, st = TABLES['supplier_material'], TABLES['supplier']
    r = c.execute(sa.select(sm).join(st, st.c.id == sm.c.supplier).where((sm.c.material == int(material)) & (sm.c.approved.is_(True))
                                                                         & (sa.func.coalesce(st.c.qual, 'approved') != 'blocked'))
                  .order_by(sm.c.preferred.desc().nulls_last(), sa.func.coalesce(sm.c.price, 0), sm.c.id).limit(1)).mappings().first()
    return dict(r) if r else None


def purchase_price(c, supplier, material):
    it = supplier_item(c, supplier, material) if supplier else None
    if it and it.get('price') is not None:
        return float(it['price']), it.get('tax_rate'), _tr('供货清单')
    m = db.get(c, 'material', int(material))
    return float((m or {}).get('price') or 0), None, _tr('物料单价')


# ───────────────── 单据保存 ─────────────────
def lines_of(c, ent, rid) -> list[dict]:
    cfg = DOCS[ent]
    lt = TABLES[cfg['line']]
    order = [lt.c.seq, lt.c.id] if 'seq' in lt.c else [lt.c.id]
    return db.rows(c, cfg['line'], lt.c[cfg['fk']] == rid, order=order)


def recalc(c, ent, rid):
    """明细金额 = 数量 × 单价（收货按合格数、发票直接取明细金额），表头 金额 / 税额 / 价税合计 = 明细合计。"""
    cfg = DOCS[ent]
    lt = TABLES[cfg['line']]
    amt = tax = tot = 0.0
    for ln in lines_of(c, ent, rid):
        if cfg['price']:
            q = ln['qty']
            if ent == 'purchase_receipt' and ln.get('qty_accepted') is not None:
                q = ln['qty_accepted']
            a = money((q or 0) * (ln.get('price') or 0))
            t = money(a * float(ln.get('tax_rate') or 0) / 100)
            if a != (ln.get('amount') or 0):
                c.execute(lt.update().where(lt.c.id == ln['id']).values(amount=a))
            amt, tax = amt + a, tax + t
        else:
            amt, tax, tot = amt + (ln.get('amount') or 0), tax + (ln.get('tax') or 0), tot + (ln.get('total') or 0)
    h = db.get(c, ent, rid)
    if not cfg['price'] and h.get('opening') and not lines_of(c, ent, rid):        # 期初发票：表头直接填
        return h
    vals = {'amount': money(amt), 'tax': money(tax), 'total': money(amt + tax if cfg['price'] else tot)}
    if any(money(h.get(k)) != v for k, v in vals.items()):
        db.update(c, ent, rid, vals, raw=True)
    return db.get(c, ent, rid)


def save_doc(c, ent, body: dict, user, rid: int | None = None, raw_status=False) -> int:
    """保存一张单据（表头 + 明细）。body = {表头字段…, lines: [{id?, 明细字段…}]}；明细里不在列表中的行删除。只有可编辑状态能改。"""
    if ent not in DOCS:
        raise HTTPException(404, _tr('没有单据类型 {ent}', ent=ent))
    cfg = DOCS[ent]
    e, le = BY_NAME[ent], BY_NAME[cfg['line']]
    ro = {f.name for f in e.fields if f.ro} - ({'status'} if raw_status else set())
    head = {k: v for k, v in body.items() if k in {f.name for f in e.fields} and k not in ro and k not in ('code',) or (k == 'code' and v and rid is None)}
    try:
        head = db.coerce(ent, head)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    lines_in = body.get('lines')
    cur = None
    if rid:
        cur = db.get(c, ent, rid)
        if not cur:
            raise HTTPException(404, _tr('单据不存在'))
        if cur.get('status') not in EDITABLE.get(ent, ('draft',)) and not raw_status:
            raise HTTPException(409, _tr('{label} {code} 已{e_status}，不能再改', label=e.label, code=cur['code'], e_status=e_status(ent, cur)))
    merged = {**(cur or {}), **head}
    if not merged.get(cfg['date']):
        head[cfg['date']] = today()
        merged[cfg['date']] = head[cfg['date']]
    lines = []
    if lines_in is not None:
        if not isinstance(lines_in, list):
            raise HTTPException(400, _tr('lines 要是列表'))
        for i, ln in enumerate(lines_in):
            keep = {k: v for k, v in ln.items() if k in {f.name for f in le.fields} and k != cfg['fk'] and not (
                k in {f.name for f in le.fields if f.ro} and not raw_status)}
            try:
                d = db.coerce(cfg['line'], keep)
            except ValueError as ex:
                raise HTTPException(400, _tr('第 {v} 行：{ex}', v=i + 1, ex=ex))
            d['_id'] = int(ln['id']) if ln.get('id') else None
            d['_extra'] = {k: v for k, v in ln.items() if k not in keep and k != 'id'}
            lines.append(d)
    derived = {'delivery_order': 'sales_order', 'sales_return': 'delivery_order', 'purchase_receipt': 'purchase_order', 'supplier_return': 'purchase_receipt'}
    if not merged.get(cfg['partner']) and not merged.get(derived.get(ent, '-')):
        raise HTTPException(400, _tr('请选择{v}', v=_tr('客户') if cfg['partner'] == 'customer' else _tr('供应商')))
    hook = BEFORE_SAVE.get(ent)
    if hook:
        hook(c, merged, lines, user, rid is None)
        for k in list(merged):
            if k in {f.name for f in e.fields} and (cur is None or merged[k] != cur.get(k)) and k not in ('id',):
                head[k] = merged[k]
    if not merged.get(cfg['partner']):              # 钩子可能从来源单据带出客户 / 供应商（发货单 ← 订单，收货 ← 采购订单）
        raise HTTPException(400, _tr('请选择{v}', v=_tr('客户') if cfg['partner'] == 'customer' else _tr('供应商')))
    if rid is None:
        rid = db.insert(c, ent, {k: v for k, v in head.items() if not k.startswith('_')}, raw=True)
    elif head:
        db.update(c, ent, rid, {k: v for k, v in head.items() if not k.startswith('_')}, raw=True)
    if lines_in is not None:
        lt = TABLES[cfg['line']]
        old = {r['id']: r for r in lines_of(c, ent, rid)}
        keep_ids = set()
        for i, ln in enumerate(lines):
            lid, extra = ln.pop('_id'), ln.pop('_extra')
            if 'seq' in lt.c and not ln.get('seq'):
                ln['seq'] = (i + 1) * 10
            if cfg['price'] and ln.get('tax_rate') is None:
                ln['tax_rate'] = default_tax(c, ent, merged)
            if lid and lid in old:
                db.update(c, cfg['line'], lid, ln, raw=True)
                keep_ids.add(lid)
            else:
                lid = db.insert(c, cfg['line'], {**ln, cfg['fk']: rid}, raw=True)
                keep_ids.add(lid)
            ln['id'] = lid
            ln['_extra'] = extra
        for oid in set(old) - keep_ids:
            db.delete(c, cfg['line'], oid)
    after = AFTER_SAVE.get(ent)
    if after:
        after(c, db.get(c, ent, rid), lines, user, cur is None)
    recalc(c, ent, rid)
    return rid


def e_status(ent, row):
    f = next((x for x in BY_NAME[ent].fields if x.name == 'status'), None)
    return (f.enum or {}).get(row.get('status'), row.get('status')) if f else ''


def doc_out(c, ent, rid, user=None) -> dict:
    """单据详情：表头 + 明细（带引用名称）+ 关联单据。"""
    from ..crud import attach_labels, out
    h = db.get(c, ent, rid)
    if not h:
        raise HTTPException(404, _tr('单据不存在'))
    attach_labels(c, ent, [h])
    lines = attach_labels(c, DOCS[ent]['line'], lines_of(c, ent, rid))
    res = {'item': out([h])[0], 'lines': out(lines)}
    rel = RELATED.get(ent)
    if rel:
        res['related'] = rel(c, h)
    return res


RELATED: dict = {}          # 单据类型 → fn(c, header) -> {名: [行]}


def who(user) -> str:
    return (user or {}).get('name') or (user or {}).get('username') or _tr('系统')


def code_of(c, entity, rid) -> str:
    r = db.get(c, entity, rid) if rid else None
    return (r or {}).get('code') or ''


def wh_of_type(c, wtype):
    r = db.get(c, 'warehouse', wtype=wtype)
    return r['id'] if r else None


def stock_qty(c, material, wh=None, lot=None) -> float:
    st = TABLES['stock']
    q = sa.select(sa.func.coalesce(sa.func.sum(st.c.qty), 0)).where(st.c.material == int(material))
    if wh:
        q = q.where(st.c.warehouse == int(wh))
    if lot:
        q = q.where(st.c.lot == lot)
    return float(c.execute(q).scalar() or 0)


def link(entity, rid):
    return f'/e/{entity}?id={rid}'
