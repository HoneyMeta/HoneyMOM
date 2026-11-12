"""5.0 内置 ERP（M4）：销售、采购、MRP、成本、应收应付、凭证导出，外部 ERP 集成（REST / Webhook / 中间库 / 金蝶 / 用友）。

模块：
  core.py         金额税额、单据保存（表头 + 明细）、会计期间锁、ERP 模式与主数据归属、价格
  sales.py        报价 / 销售订单（交货计划、变更历史、信用检查）/ 发货 / 退货 / 对账单 / 订单看板
  purchase.py     请购 / 采购订单（合格供方）/ 收货 → 来料检验 → 入库 / 退供 / 对账单 / 供应商绩效
  mrp.py          MRP（多层 BOM、净算、提前期、pegging、粗能力）、下达
  cost.py         移动平均计价、标准成本卷积、工单实际成本、差异、月末结账
  finance.py      发票登记、收付款核销、账龄
  voucher.py      凭证生成与导出（金蝶云星空 / 用友 U8 / YonSuite / Excel）
  integration.py  模式、事件与 Webhook、中间库、REST v1、监控、/api/docs
  connectors.py   金蝶云星空、用友 U8、YonSuite 对接样例
  ai.py           AI：解释 MRP 建议、订单风险、起草采购订单（动作草稿）
接口说明：docs/dev/erp.md。install(app) 在 app.build_app() 里调用一次。
"""
from ..i18n import _tr
import asyncio
import os

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException, Request

from .. import db, perms
from ..auth import principal
from ..crud import attach_labels, out
from ..schema import BY_NAME, TABLES
from . import ai, connectors, core, cost, finance, integration, mrp, purchase, sales, voucher

doc_router = APIRouter()


def _kind(kind):
    if kind not in core.DOCS:
        raise HTTPException(404, _tr('没有单据类型 {kind}', kind=kind))
    return kind


@doc_router.get('/api/erp/doc/{kind}')
def doc_list(kind: str, request: Request, page: int = 1, size: int = 30, user=Depends(principal)):
    """单据列表：?q=单号/往来单位 &status= &partner= &f_<字段>=…（数据范围按工厂）。"""
    _kind(kind)
    perms.check(user, perms.entity_perm(kind, 'read'))
    from ..crud import build_query
    params = dict(request.query_params)
    if params.get('partner'):
        params['f_' + core.DOCS[kind]['partner']] = params.pop('partner')
    if params.get('status'):
        params['f_status'] = params.pop('status')
    q, conds, t = build_query(kind, params, user)
    with db.engine.connect() as c:
        cnt = sa.select(sa.func.count()).select_from(t)
        for x in conds:
            cnt = cnt.where(x)
        total = c.execute(cnt).scalar()
        size = max(1, min(size, 500))
        items = attach_labels(c, kind, [dict(r) for r in c.execute(q.limit(size).offset((page - 1) * size)).mappings()])
    return {'total': total, 'items': out(items)}


@doc_router.get('/api/erp/doc/{kind}/{rid}')
def doc_get(kind: str, rid: int, user=Depends(principal)):
    _kind(kind)
    perms.check(user, perms.entity_perm(kind, 'read'))
    with db.engine.connect() as c:
        h = db.get(c, kind, rid)
        if not h or not perms.in_scope(user, kind, h):
            raise HTTPException(404, _tr('单据不存在'))
        return core.doc_out(c, kind, rid, user)


def flows():
    """M3 的审批流程（honeymom/impl/flows.py）；没合进来时返回 None。"""
    try:
        from ..impl import flows as f
        return f
    except Exception:          # noqa: BLE001
        return None


def flow_gate(entity, action, record=None, body=None, user=None):
    """挂了审批流程且条件满足 → 返回"已提交审批"的响应（调用方直接返回，不执行）；否则 None。"""
    f = flows()
    return f.gate(entity, action, record=record, body=body, user=user) if f else None


def _create_doc(kind, body, user):
    with db.engine.begin() as c:
        rid = core.save_doc(c, kind, body, user)
        return {'ok': True, 'id': rid, 'code': db.get(c, kind, rid).get('code')}


@doc_router.post('/api/erp/doc/{kind}')
def doc_create(kind: str, body: dict = Body(...), user=Depends(principal)):
    _kind(kind)
    perms.check(user, perms.entity_perm(kind, 'create'))
    if not perms.in_scope(user, kind, body):
        raise HTTPException(403, _tr('不在你的数据范围内'))
    pending = flow_gate(kind, 'save_doc', body=body, user=user)
    if pending:
        return pending
    with db.engine.begin() as c:
        rid = core.save_doc(c, kind, body, user)
        h = db.get(c, kind, rid)
        if not perms.in_scope(user, kind, h):
            raise HTTPException(403, _tr('不在你的数据范围内'))
        return {'ok': True, 'id': rid, 'code': h.get('code')}


@doc_router.put('/api/erp/doc/{kind}/{rid}')
def doc_update(kind: str, rid: int, body: dict = Body(...), user=Depends(principal)):
    _kind(kind)
    perms.check(user, perms.entity_perm(kind, 'update'))
    with db.engine.begin() as c:
        h = db.get(c, kind, rid)
        if not h or not perms.in_scope(user, kind, h):
            raise HTTPException(404, _tr('单据不存在'))
        core.save_doc(c, kind, body, user, rid)
        return {'ok': True, 'id': rid}


@doc_router.get('/api/erp/meta')
def erp_meta(user=Depends(principal)):
    """前端单据页用：单据类型配置、状态名、ERP 模式。"""
    res = {}
    for k, v in core.DOCS.items():
        e = BY_NAME[k]
        st = next((f for f in e.fields if f.name == 'status'), None)
        res[k] = {**v, 'label': e.label, 'line_label': BY_NAME[v['line']].label, 'status': (st.enum if st else {}), 'actions': e.actions}
    return {'docs': res, 'mode': core.mode_cfg()}


def _register_importers():
    """登记到 M3 的通用导入框架（docs/dev/import.md）：客户零件号、供货清单、价格表 / 明细、物料计划参数、预测、费率、期初应收 / 应付。"""
    try:
        from ..impl import importer as imp
    except Exception:          # noqa: BLE001  M3 还没合进来时跳过
        return
    I = imp.Importer
    reg = imp.register
    reg(I(key='customer_part', label=_tr('客户零件号'), entity='customer_part', keys=('customer', 'material'), order=52, group='往来单位', depends=('customer', 'material'),
          examples=[{'customer': 'C001', 'material': 'FG-1001', 'customer_part_no': 'JF-3401010', 'customer_part_name': _tr('转向器壳体')}]))
    reg(I(key='supplier_material', label=_tr('供货清单（合格供方 / 采购价）'), entity='supplier_material', keys=('supplier', 'material'), order=53, group='往来单位',
          depends=('supplier', 'material'), examples=[{'supplier': 'S001', 'material': 'RM-1001', 'price': 3300, 'lead_days': 7, 'approved': _tr('是')}]))
    reg(I(key='price_list', label=_tr('价格表'), entity='price_list', keys=('code',), order=54, group='往来单位', depends=('customer',),
          examples=[{'code': 'PL-C001', 'name': _tr('北辰重卡 2026 价格'), 'customer': 'C001'}]))
    reg(I(key='price_item', label=_tr('价格明细'), entity='price_item', keys=('price_list', 'material', 'min_qty'), order=55, group='往来单位',
          depends=('price_list', 'material'), examples=[{'price_list': 'PL-C001', 'material': 'FG-1001', 'min_qty': 0, 'price': 380, 'tax_rate': 13}]))
    reg(I(key='mat_plan', label=_tr('物料计划参数（MRP）'), entity='mat_plan', keys=('material',), order=56, group='计划', depends=('material',),
          examples=[{'material': 'RM-1001', 'make_buy': _tr('外购'), 'lead_days': 7, 'min_lot': 10, 'safety_stock': 30}]))
    reg(I(key='forecast', label=_tr('需求预测'), entity='forecast', keys=('material', 'due', 'customer'), order=57, group='计划', depends=('material',),
          examples=[{'material': 'FG-1001', 'due': '2026-12-01', 'qty': 500}]))
    reg(I(key='cost_rate', label=_tr('成本费率'), entity='cost_rate', keys=('code',), order=58, group='成本',
          examples=[{'code': 'LAB-CNC', 'name': _tr('CNC 人工'), 'kind': '人工', 'basis': _tr('元/小时'), 'rate': 48, 'station_group': 'CNC'}]))

    def opening(side):
        cfg = finance.SIDE[side]
        ent = cfg['inv']

        def write(ctx, v, ex):
            total = core.money((v.get('amount') or 0) + (v.get('tax') or 0))
            if ex:
                if ex.get('paid'):
                    return 'skip'
                db.update(ctx.conn, ent, ex['id'], {'amount': v.get('amount'), 'tax': v.get('tax') or 0, 'total': total, 'due_date': v.get('due_date')}, raw=True)
                return 'update'
            p = db.get(ctx.conn, cfg['partner'], v[cfg['partner']]) or {}
            d = v.get('invoice_date') or core.today()
            db.insert(ctx.conn, ent, {cfg['partner']: v[cfg['partner']], 'invoice_no': v.get('invoice_no') or '', 'invoice_date': d,
                                      'due_date': v.get('due_date') or (core.as_date(d) + __import__('datetime').timedelta(days=int(p.get('terms_days') or 30))),
                                      'amount': v.get('amount') or 0, 'tax': v.get('tax') or 0, 'total': total, 'paid': 0, 'status': 'open', 'opening': True,
                                      'remark': v.get('remark') or _tr('期初')}, raw=True)
            return 'create'

        def find(ctx, v):
            t = TABLES[ent]
            r = ctx.conn.execute(sa.select(t).where((t.c[cfg['partner']] == v.get(cfg['partner'])) & (t.c.invoice_no == (v.get('invoice_no') or ''))
                                                    & (t.c.opening.is_(True)))).mappings().first()
            return dict(r) if r else None
        C = imp.Col
        lab = _tr('客户') if side == 'ar' else _tr('供应商')
        reg(I(key=f'opening_{side}', label=_tr('期初应收') if side == 'ar' else _tr('期初应付'), entity=None, order=60 if side == 'ar' else 61, group='期初',
              keys=(cfg['partner'], 'invoice_no'), depends=(cfg['partner'],), perms=(f'finance.{ent}.create',), find=find, write=write,
              columns=[C(cfg['partner'], lab, 'ref', ref=cfg['partner'], req=True), C('invoice_no', _tr('发票号 / 单据号'), req=True), C('invoice_date', _tr('日期'), 'date'),
                       C('due_date', _tr('到期日'), 'date'), C('amount', _tr('金额（不含税）'), 'float', req=True), C('tax', _tr('税额'), 'float'), C('remark', _tr('备注'))],
              doc=_tr('上线前还没收 / 付清的款项：每张发票（或每笔挂账）一行。导入后在应收应付、账龄、对账单里作为期初。'),
              examples=[{cfg['partner']: 'C001' if side == 'ar' else 'S001', 'invoice_no': '04400000001', 'invoice_date': '2026-10-15', 'amount': 100000, 'tax': 13000}],
              count=lambda conn: conn.execute(sa.select(sa.func.count()).select_from(TABLES[ent]).where(TABLES[ent].c.opening.is_(True))).scalar()))
    opening('ar')
    opening('ap')


def _register_flows():
    """审批流程能挂的 ERP 服务动作（通用的增删改和实体动作 confirm / approve / post … M3 已经自动支持）。"""
    f = flows()
    if not f:
        return
    from ..schema import BY_NAME as _B
    for kind in core.DOCS:
        f.register_action(kind, 'save_doc', _tr('新建{label}（单据页，含明细）', label=_B[kind].label), executor=lambda body, user, rid, k=kind: _create_doc(k, body, user))

    def _chg(body, user, rid):
        with db.engine.begin() as c:
            return sales.change_order(c, db.get(c, 'sales_order', rid), body, user)
    f.register_action('sales_order', 'change', _tr('订单变更（已确认后改数量 / 交期 / 单价）'), executor=_chg)

    def _rel(body, user, rid):
        with db.engine.begin() as c:
            return mrp.release(c, body.get('ids') or [], user, body.get('buy_as') or 'req', bool(body.get('release_wo')))
    f.register_action('mrp_suggestion', 'release_batch', _tr('MRP 批量下达'), executor=_rel)


def install(app):
    _register_flows()
    for r in (doc_router, sales.router, purchase.router, mrp.router, cost.router, finance.router, voucher.router, integration.router, integration.v1,
              connectors.router, ai.router):
        app.include_router(r)
    integration.install_middleware(app)
    integration.install_docs(app)
    _register_importers()

    @app.on_event('startup')
    async def _erp_start():
        if os.environ.get('ERP_LOOP', '1') != '0':
            asyncio.create_task(integration.loop())
