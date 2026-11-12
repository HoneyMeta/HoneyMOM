"""5.0 内置 ERP（M4）的实体：销售、采购、MRP、成本、应收应付、凭证，以及外部 ERP 集成用的表。

schema.py 在建表之前调用 extend(ENTITIES)（把这里的实体加进去、给生产工单补"销售订单"字段），建完 metadata 后调用 tables(metadata)
（集成用的普通表：Webhook、事件、投递、中间库、外部编码映射……这些表含密钥或是系统流水，不走通用增删改查）。
perms.py 调用 perms_extend()：模块权限键、默认角色补充（销售员 / 采购员 / 财务；计划员可写 MRP；仓管员可发货 / 收货；
非财务角色看不到财务和成本）。接口与说明见 docs/dev/erp.md；迁移 migrations/v0300_erp.py。
"""
from .i18n import _tr
import sqlalchemy as sa

from .schema import B, CODE, D, DT, E, EN, I, J, N, NAME, R, S, T  # noqa: F401  （schema 正在初始化：只取它已定义的辅助函数）

# 模块（中文名 → 权限键）
MODULES = {'销售管理': 'sales', '采购管理': 'purchase', '计划 MRP': 'mrp', '成本核算': 'cost', '财务': 'finance', 'ERP 集成': 'erp'}

PAY_T = {'net': '月结', 'cash': '现款', 'prepay': '预付', 'cod': '货到付款'}
MTYPE = {'raw': '原材料', 'semi': '半成品', 'fg': '成品', 'aux': '辅料', 'consumable': '易耗品'}
SRC = {'manual': '手工', 'quotation': '报价转单', 'req': '请购转单', 'mrp': 'MRP', 'ai': 'AI 起草', 'api': '接口', 'staging': '中间库',
       'kingdee': '金蝶', 'yonyou': '用友', 'outsource': '委外', 'spare': '备件', 'safety': '安全库存'}
# 凭证事件（科目映射表 acct_map.event）
VOUCHER_EVENTS = {
    'ar': '应收账款', 'revenue': '主营业务收入', 'tax_out': '销项税额', 'cogs': '主营业务成本', 'inventory': '存货（按物料类型）',
    'tax_in': '进项税额', 'ap': '应付账款', 'bank': '银行存款', 'bill_recv': '应收票据', 'wip_mat': '生产成本-直接材料', 'wip_lab': '生产成本-直接人工',
    'wip_ovh': '生产成本-制造费用', 'payroll': '应付职工薪酬', 'mfg_ovh': '制造费用（机器/能源/分摊）', 'inv_loss': '待处理财产损溢', 'sales_return': '销售退回（冲收入）',
}
VOUCHER_SRC = {'delivery': '销售发货', 'sales_return': '销售退货', 'ar_receipt': '收款', 'receipt': '采购入库', 'supplier_return': '退供应商',
               'ap_payment': '付款', 'issue': '生产领料', 'wo_cost': '生产成本结转', 'completion': '完工入库', 'adjust': '盘点调整'}

ENTITIES_ERP = [
    # ───────────────── 销售 ─────────────────
    E('customer_contact', '客户联系人', '销售管理', [R('customer', '客户', 'customer', req=True), NAME('姓名'), S('title', '职务'), S('phone', '电话'),
                                              S('email', '邮箱'), B('primary', '主联系人')], menu=False, order='customer'),
    E('customer_part', '客户零件号', '销售管理', [R('customer', '客户', 'customer', req=True), R('material', '我方物料', 'material', req=True),
                                            S('customer_part_no', '客户零件号', req=True, search=True), S('customer_part_name', '客户零件名称'), S('drawing_rev', '图纸版本'),
                                            S('remark', '备注', list=False)], order='customer'),
    E('price_list', '价格表', '销售管理', [CODE(), NAME(), R('customer', '客户', 'customer', hint='空 = 通用价格表'), S('currency', '币别', default='CNY'),
                                        D('valid_from', '生效'), D('valid_to', '失效'), EN('status', '状态', {'active': '启用', 'inactive': '停用'}, default='active')],
      children=[('price_item', 'price_list')]),
    E('price_item', '价格明细', '销售管理', [R('price_list', '价格表', 'price_list', req=True), R('material', '物料', 'material', req=True),
                                          N('min_qty', '起订量', default=0), N('price', '单价（不含税）', req=True), N('tax_rate', '税率 %'), S('remark', '备注', list=False)],
      menu=False, order='material'),
    E('quotation', '报价单', '销售管理', [
        CODE('报价单号', auto='BJ{d}{n:03}'), R('customer', '客户', 'customer', req=True), D('quote_date', '报价日期'), D('valid_until', '有效期至'),
        EN('status', '状态', {'draft': '草稿', 'sent': '已报价', 'accepted': '已成交', 'lost': '未成交', 'expired': '已过期'}, default='draft', ro=True),
        N('amount', '金额', ro=True), N('tax', '税额', ro=True, list=False), N('total', '价税合计', ro=True), R('sales_order', '销售订单', 'sales_order', ro=True),
        S('salesman', '业务员'), T('remark', '备注')],
      children=[('quotation_line', 'quotation')], actions={'send': '发出报价', 'accept': '成交转订单', 'lose': '未成交'}),
    E('quotation_line', '报价明细', '销售管理', [R('quotation', '报价单', 'quotation', req=True), I('seq', '行号'), R('material', '物料', 'material', req=True),
                                              S('customer_part_no', '客户零件号'), N('qty', '数量', req=True), N('price', '单价'), N('tax_rate', '税率 %'),
                                              N('amount', '金额', ro=True), I('lead_days', '交期 天'), S('remark', '备注', list=False)], menu=False, order='seq'),
    E('sales_order', '销售订单', '销售管理', [
        CODE('订单号', auto='SO{d}{n:03}'), R('customer', '客户', 'customer', req=True), S('customer_po', '客户订单号', search=True), D('order_date', '下单日期'),
        D('due', '要求交期'), EN('status', '状态', {'draft': '草稿', 'credit_hold': '信用待批', 'confirmed': '已确认', 'partial': '部分发货', 'shipped': '已发完',
                                                  'closed': '已关闭', 'cancelled': '已取消'}, default='draft', ro=True),
        R('plant', '工厂', 'plant', list=False), S('currency', '币别', default='CNY', list=False), N('amount', '金额', ro=True), N('tax', '税额', ro=True, list=False),
        N('total', '价税合计', ro=True), I('version', '版本', default=1, ro=True), R('quotation', '报价单', 'quotation', ro=True, list=False),
        EN('source', '来源', SRC, default='manual', ro=True), B('mto', '按单生产', hint='确认后可一键生成工单，发货时只从这些工单的产出发'), S('salesman', '业务员'),
        S('ship_to', '收货地址', list=False), S('credit_note', '信用检查', ro=True, list=False), T('remark', '备注')],
      children=[('so_line', 'sales_order'), ('so_change', 'sales_order'), ('delivery_order', 'sales_order'), ('work_order', 'sales_order')],
      actions={'confirm': '确认', 'approve_credit': '信用特批', 'close': '关闭', 'cancel': '取消', 'make_wo': '生成工单'}),
    E('so_line', '订单明细', '销售管理', [
        R('sales_order', '销售订单', 'sales_order', req=True), I('seq', '行号'), R('material', '物料', 'material', req=True), S('customer_part_no', '客户零件号'),
        N('qty', '数量', req=True), N('price', '单价'), N('tax_rate', '税率 %'), N('amount', '金额', ro=True), D('due', '交期'),
        N('qty_shipped', '已发', ro=True, default=0), N('qty_returned', '已退', ro=True, default=0), R('work_order', '工单（按单生产）', 'work_order'),
        EN('status', '状态', {'open': '未完', 'closed': '已完', 'cancelled': '已取消'}, default='open', ro=True), S('remark', '备注', list=False)],
      children=[('so_schedule', 'so_line')], menu=False, order='seq'),
    E('so_schedule', '交货计划', '销售管理', [R('so_line', '订单行', 'so_line', req=True), R('sales_order', '销售订单', 'sales_order'), D('due', '交货日期', req=True),
                                           N('qty', '数量', req=True), N('qty_shipped', '已发', ro=True, default=0)], menu=False, order='due'),
    E('so_change', '订单变更记录', '销售管理', [R('sales_order', '销售订单', 'sales_order', req=True), I('version', '变更后版本'),
                                              EN('kind', '类型', {'change': '变更', 'cancel': '取消', 'close': '关闭', 'confirm': '确认', 'credit': '信用特批'}),
                                              J('diff', '改了什么'), S('reason', '原因'), S('changed_by', '变更人'), DT('time', '时间')],
      internal=True, menu=False, order='-id'),
    E('delivery_order', '发货单', '销售管理', [
        CODE('发货单号', auto='FH{d}{n:03}'), R('customer', '客户', 'customer', req=True), R('sales_order', '销售订单', 'sales_order'), D('ship_date', '发货日期'),
        R('warehouse', '发货仓库', 'warehouse'), R('plant', '工厂', 'plant', list=False),
        EN('status', '状态', {'draft': '草稿', 'posted': '已发货', 'cancelled': '已取消'}, default='draft', ro=True), S('ship_to', '收货地址', list=False),
        S('carrier', '承运商', list=False), S('tracking', '运单号', list=False), N('amount', '金额', ro=True), N('tax', '税额', ro=True, list=False),
        N('total', '价税合计', ro=True), N('cost', '发货成本', ro=True, list=False), N('invoiced', '已开票', ro=True, default=0),
        S('posted_by', '过账人', ro=True, list=False), DT('posted_at', '过账时间', ro=True, list=False), T('remark', '备注')],
      children=[('delivery_line', 'delivery_order')], actions={'post': '发货过账', 'cancel': '取消'}),
    E('delivery_line', '发货明细', '销售管理', [
        R('delivery_order', '发货单', 'delivery_order', req=True), R('so_line', '订单行', 'so_line'), R('material', '物料', 'material', req=True),
        N('qty', '数量', req=True), S('lot', '批次'), N('price', '单价'), N('tax_rate', '税率 %'), N('amount', '金额', ro=True), N('unit_cost', '单位成本', ro=True),
        R('work_order', '工单', 'work_order'), R('warehouse', '仓库', 'warehouse', list=False)], menu=False),
    E('sales_return', '销售退货', '销售管理', [
        CODE('退货单号', auto='TH{d}{n:03}'), R('customer', '客户', 'customer', req=True), R('sales_order', '销售订单', 'sales_order'),
        R('delivery_order', '原发货单', 'delivery_order'), D('return_date', '退货日期'), R('warehouse', '退回仓库', 'warehouse'), R('plant', '工厂', 'plant', list=False),
        EN('disposition', '处理', {'restock': '退回入库', 'rework': '返修', 'scrap': '报废'}, default='restock'),
        EN('status', '状态', {'draft': '草稿', 'received': '已收货', 'cancelled': '已取消'}, default='draft', ro=True), S('reason', '退货原因'),
        S('complaint_code', '客诉编号', hint='关联客诉召回'), N('amount', '金额', ro=True), N('tax', '税额', ro=True, list=False), N('total', '价税合计', ro=True),
        T('remark', '备注')],
      children=[('return_line', 'sales_return')], actions={'receive': '退货收货', 'cancel': '取消'}),
    E('return_line', '退货明细', '销售管理', [R('sales_return', '退货单', 'sales_return', req=True), R('delivery_line', '原发货行', 'delivery_line'),
                                            R('material', '物料', 'material', req=True), N('qty', '数量', req=True), S('lot', '批次'), N('price', '单价'),
                                            N('tax_rate', '税率 %'), N('amount', '金额', ro=True), N('unit_cost', '单位成本', ro=True)], menu=False),
    E('customer_statement', '客户对账单', '销售管理', [
        CODE('对账单号', auto='DZ{d}{n:03}'), R('customer', '客户', 'customer', req=True), S('period', '期间', search=True), D('start', '开始'), D('end', '结束'),
        N('opening', '期初'), N('shipped', '本期发货'), N('returned', '本期退货'), N('received', '本期收款'), N('closing', '期末'), N('invoiced', '本期开票', list=False),
        EN('status', '状态', {'draft': '待发出', 'sent': '已发客户', 'confirmed': '客户已确认', 'disputed': '有异议'}, default='draft', ro=True), J('lines', '明细'),
        S('confirmed_by', '确认人', ro=True, list=False), DT('confirmed_at', '确认时间', ro=True, list=False), S('note', '说明', list=False)],
      internal=True, actions={'send': '发给客户', 'confirm': '客户确认', 'dispute': '有异议'}),

    # ───────────────── 采购 ─────────────────
    E('supplier_material', '供货清单', '采购管理', [
        R('supplier', '供应商', 'supplier', req=True), R('material', '物料', 'material', req=True), S('supplier_part_no', '供应商料号'), N('price', '单价（不含税）'),
        N('tax_rate', '税率 %'), I('lead_days', '采购周期 天', default=7), N('moq', '最小订量', default=0),
        B('approved', '合格供方（ASL）', default=True, hint='只有合格供方能下采购订单'), B('preferred', '首选'), B('iqc_exempt', '免检', hint='收货直接入库，不开来料检验'),
        D('valid_to', '有效期至'), S('remark', '备注', list=False)], order='material'),
    E('purchase_req', '请购单', '采购管理', [
        CODE('请购单号', auto='QG{d}{n:03}'), R('material', '物料', 'material', req=True), N('qty', '数量', req=True), D('need_date', '需求日期'),
        R('supplier', '建议供应商', 'supplier'), S('requester', '申请人'), S('reason', '用途/原因'), EN('source', '来源', SRC, default='manual'),
        EN('status', '状态', {'draft': '草稿', 'submitted': '待审批', 'approved': '已批准', 'ordered': '已下单', 'rejected': '已驳回', 'cancelled': '已取消'},
           default='submitted', ro=True),
        R('purchase_order', '采购订单', 'purchase_order', ro=True), I('mrp_suggestion', 'MRP 建议', ro=True, list=False)],
      actions={'approve': '批准', 'reject': '驳回', 'cancel': '取消'}),
    E('purchase_order', '采购订单', '采购管理', [
        CODE('采购订单号', auto='PO{d}{n:03}'), R('supplier', '供应商', 'supplier', req=True), D('order_date', '下单日期'), D('due', '要求到货'),
        EN('status', '状态', {'draft': '草稿', 'approved': '已审批', 'sent': '已下发', 'partial': '部分到货', 'received': '已到齐', 'closed': '已关闭', 'cancelled': '已取消'},
           default='draft', ro=True),
        R('plant', '工厂', 'plant', list=False), S('currency', '币别', default='CNY', list=False), N('amount', '金额', ro=True), N('tax', '税额', ro=True, list=False),
        N('total', '价税合计', ro=True), S('buyer', '采购员'), EN('source', '来源', SRC, default='manual', ro=True), T('remark', '备注')],
      children=[('po_line', 'purchase_order'), ('purchase_receipt', 'purchase_order')],
      actions={'approve': '审批', 'send': '下发供应商', 'close': '关闭', 'cancel': '取消'}),
    E('po_line', '采购明细', '采购管理', [
        R('purchase_order', '采购订单', 'purchase_order', req=True), I('seq', '行号'), R('material', '物料', 'material', req=True), N('qty', '数量', req=True),
        N('price', '单价'), N('tax_rate', '税率 %'), N('amount', '金额', ro=True), D('due', '到货日期'), N('qty_received', '已收', ro=True, default=0),
        N('qty_accepted', '合格入库', ro=True, default=0), N('qty_rejected', '不合格', ro=True, default=0), R('purchase_req', '请购单', 'purchase_req', ro=True),
        EN('status', '状态', {'open': '未完', 'closed': '已完', 'cancelled': '已取消'}, default='open', ro=True), S('remark', '备注', list=False)],
      menu=False, order='seq'),
    E('purchase_receipt', '采购收货', '采购管理', [
        CODE('收货单号', auto='SH{d}{n:03}'), R('supplier', '供应商', 'supplier', req=True), R('purchase_order', '采购订单', 'purchase_order'),
        D('receive_date', '收货日期'), R('warehouse', '入库仓库', 'warehouse'), R('plant', '工厂', 'plant', list=False), S('delivery_note', '供应商送货单号'),
        EN('status', '状态', {'draft': '草稿', 'inspecting': '来料检验中', 'done': '已入库', 'cancelled': '已取消'}, default='draft', ro=True),
        N('amount', '入库金额', ro=True), N('tax', '税额', ro=True, list=False), N('total', '价税合计', ro=True), N('invoiced', '已收票', ro=True, default=0),
        S('posted_by', '收货人', ro=True, list=False), T('remark', '备注')],
      children=[('receipt_line', 'purchase_receipt')], actions={'post': '收货送检', 'settle': '按检验结果入库', 'cancel': '取消'}),
    E('receipt_line', '收货明细', '采购管理', [
        R('purchase_receipt', '收货单', 'purchase_receipt', req=True), R('po_line', '采购订单行', 'po_line'), R('material', '物料', 'material', req=True),
        N('qty', '到货数量', req=True), S('lot', '批次'), N('qty_accepted', '合格入库', ro=True), N('qty_rejected', '不合格', ro=True),
        R('insp_record', '来料检验单', 'insp_record', ro=True),
        EN('status', '状态', {'pending': '待收货', 'inspecting': '检验中', 'accepted': '合格', 'partial': '部分合格', 'rejected': '不合格'}, default='pending', ro=True),
        N('price', '单价'), N('tax_rate', '税率 %'), N('amount', '金额', ro=True), R('warehouse', '仓库', 'warehouse', list=False)], menu=False),
    E('supplier_return', '退供应商', '采购管理', [
        CODE('退供单号', auto='TG{d}{n:03}'), R('supplier', '供应商', 'supplier', req=True), R('purchase_receipt', '收货单', 'purchase_receipt'),
        R('purchase_order', '采购订单', 'purchase_order'), D('return_date', '退货日期'),
        EN('kind', '类型', {'iqc': '来料检验不合格', 'stock': '库存退货'}, default='iqc'),
        EN('status', '状态', {'draft': '待退', 'shipped': '已退出', 'cancelled': '已取消'}, default='draft', ro=True), S('reason', '原因'),
        R('warehouse', '出库仓库', 'warehouse', hint='库存退货才扣库存'), N('amount', '金额', ro=True), N('tax', '税额', ro=True, list=False), N('total', '价税合计', ro=True),
        T('remark', '备注')],
      children=[('supplier_return_line', 'supplier_return')], actions={'ship': '退货出库', 'cancel': '取消'}),
    E('supplier_return_line', '退供明细', '采购管理', [R('supplier_return', '退供单', 'supplier_return', req=True), R('receipt_line', '收货行', 'receipt_line'),
                                                     R('material', '物料', 'material', req=True), N('qty', '数量', req=True), S('lot', '批次'), N('price', '单价'),
                                                     N('tax_rate', '税率 %'), N('amount', '金额', ro=True)], menu=False),
    E('supplier_statement', '供应商对账单', '采购管理', [
        CODE('对账单号', auto='GD{d}{n:03}'), R('supplier', '供应商', 'supplier', req=True), S('period', '期间', search=True), D('start', '开始'), D('end', '结束'),
        N('opening', '期初'), N('received', '本期入库'), N('returned', '本期退货'), N('paid', '本期付款'), N('closing', '期末'), N('invoiced', '本期收票', list=False),
        EN('status', '状态', {'draft': '待发出', 'sent': '已发供应商', 'confirmed': '已确认', 'disputed': '有异议'}, default='draft', ro=True), J('lines', '明细'),
        S('confirmed_by', '确认人', ro=True, list=False), DT('confirmed_at', '确认时间', ro=True, list=False), S('note', '说明', list=False)],
      internal=True, actions={'send': '发给供应商', 'confirm': '已确认', 'dispute': '有异议'}),

    # ───────────────── 计划 MRP ─────────────────
    E('forecast', '需求预测', '计划 MRP', [R('material', '物料', 'material', req=True), D('due', '需求日期', req=True), N('qty', '数量', req=True),
                                        R('customer', '客户', 'customer'), S('remark', '备注')], order='due'),
    E('mat_plan', '物料计划参数', '计划 MRP', [
        R('material', '物料', 'material', req=True, unique=True), EN('make_buy', '来源', {'make': '自制', 'buy': '外购'}, hint='空 = 有 BOM 的自制，否则外购'),
        I('lead_days', '提前期 天'), N('min_lot', '最小批量'), N('lot_multiple', '批量倍数'), N('safety_stock', '安全库存', hint='空 = 用物料主数据的安全库存'),
        N('scrap_pct', '损耗 %'), R('supplier', '默认供应商', 'supplier'), R('warehouse', '默认仓库', 'warehouse'), S('remark', '备注', list=False)], order='material'),
    E('mrp_run', 'MRP 运算', '计划 MRP', [CODE('运算号', auto='MRP{d}{n:02}'), DT('run_at', '运算时间'), I('horizon_days', '计划期 天'), J('params', '参数'),
                                        J('stats', '结果'), S('created_by', '运算人'), EN('status', '状态', {'done': '已完成', 'superseded': '已被新运算取代'}, default='done')],
      children=[('mrp_suggestion', 'run')], internal=True),
    E('mrp_suggestion', 'MRP 建议', '计划 MRP', [
        R('run', '运算', 'mrp_run', req=True), EN('kind', '建议', {'make': '计划生产', 'buy': '计划采购', 'expedite': '提前', 'defer': '推迟'}),
        R('material', '物料', 'material'), I('level', '层级', default=0), N('qty', '建议数量'), N('qty_adj', '调整数量'), D('need_date', '需求日期'),
        D('release_date', '建议下达'), D('date_adj', '调整日期'), R('supplier', '供应商', 'supplier'), S('ref_doc', '关联单据'), J('pegging', '需求来源'),
        T('reason', '原因'), EN('status', '状态', {'new': '待处理', 'adjusted': '已调整', 'released': '已下达', 'ignored': '已忽略'}, default='new', ro=True),
        R('work_order', '生成的工单', 'work_order', ro=True), R('purchase_req', '生成的请购', 'purchase_req', ro=True),
        R('purchase_order', '生成的采购订单', 'purchase_order', ro=True), J('capacity', '产能检查'), S('planner', '处理人', ro=True, list=False)],
      internal=True, actions={'release': '下达', 'ignore': '忽略'}),

    # ───────────────── 成本 ─────────────────
    E('cost_rate', '费率', '成本核算', [
        CODE(), NAME(), EN('kind', '类型', {'labour': '人工', 'machine': '机器', 'energy': '能源', 'overhead': '制造费用分摊'}, default='labour'),
        EN('basis', '计算方式', {'hour': '元/小时', 'kwh': '元/kWh', 'labour_hour': '按人工工时 元/h', 'machine_hour': '按机器工时 元/h',
                               'direct_pct': '按直接成本 %', 'unit': '按件 元/件'}, default='hour'),
        N('rate', '费率', req=True), R('process', '工序', 'process', hint='空 = 全部工序'), S('station_group', '工位组'), R('equipment', '设备', 'equipment'),
        D('valid_from', '生效日期'), S('remark', '备注', list=False)], order='kind'),
    E('std_cost', '标准成本', '成本核算', [R('material', '物料', 'material', req=True), D('as_of', '计算日期'), N('mat', '材料'), N('lab', '人工'), N('mach', '机器'),
                                        N('energy', '能源'), N('ovh', '制造费用'), N('total', '单位标准成本'), I('level', '层级'), J('detail', '明细'),
                                        B('current', '当前', default=True), S('by', '来源')], internal=True, order='-id'),
    E('wo_cost', '工单成本', '成本核算', [
        R('work_order', '工单', 'work_order', req=True, unique=True), R('product', '产品', 'material'), S('period', '期间'), N('qty_done', '完工数'),
        N('mat', '材料'), N('lab', '人工'), N('mach', '机器'), N('energy', '能源'), N('ovh', '制造费用'), N('total', '实际总成本'), N('unit_cost', '实际单位成本'),
        N('std_unit', '标准单位成本'), N('std_total', '标准总成本'), N('variance', '差异（实际 − 标准）'), J('var_detail', '差异明细'), J('detail', '计算明细'),
        EN('status', '状态', {'open': '未结账', 'frozen': '已结账'}, default='open'), DT('calc_at', '计算时间')], internal=True),
    E('inv_cost', '存货成本', '成本核算', [R('material', '物料', 'material', req=True, unique=True), N('qty', '结存数量'), N('avg_cost', '移动平均单价'),
                                        N('value', '结存金额'), I('last_txn', '已计价到出入库记录'), DT('updated', '更新时间')], internal=True, order='material'),
    E('cost_ledger', '存货计价流水', '成本核算', [I('txn', '出入库记录 id', unique=True), R('material', '物料', 'material'), S('ttype', '类型'), N('qty', '数量（入 + / 出 −）'),
                                              N('unit_cost', '单价'), N('value', '金额'), N('qty_after', '结存数量'), N('avg_after', '结存单价'), S('ref_doc', '关联单据'),
                                              S('period', '期间'), DT('time', '时间'), S('basis', '计价依据')], internal=True, menu=False),
    E('fin_period', '会计期间', '成本核算', [
        S('code', '期间', req=True, unique=True, search=True, hint='YYYY-MM'), D('start', '开始'), D('end', '结束'),
        EN('status', '状态', {'open': '未结账', 'closed': '已结账'}, default='open', ro=True), S('closed_by', '结账人', ro=True), DT('closed_at', '结账时间', ro=True),
        J('snapshot', '结账快照', ro=True)], order='-code', actions={'close': '月末结账', 'reopen': '反结账'}),

    # ───────────────── 应收应付 ─────────────────
    E('ar_invoice', '销售发票', '财务', [
        CODE('登记号', auto='KP{d}{n:03}'), S('invoice_no', '发票号码', search=True), EN('itype', '票种', {'special': '增值税专用发票', 'normal': '普通发票', 'electronic': '数电发票'},
                                                                                  default='special'),
        R('customer', '客户', 'customer', req=True), D('invoice_date', '开票日期'), D('due_date', '到期日'), N('amount', '金额', ro=True), N('tax', '税额', ro=True),
        N('total', '价税合计', ro=True), N('paid', '已收', ro=True, default=0),
        EN('status', '状态', {'open': '未收', 'partial': '部分收款', 'paid': '已收清', 'void': '作废'}, default='open', ro=True), T('remark', '备注')],
      children=[('ar_invoice_line', 'invoice')], actions={'void': '作废'}),
    E('ar_invoice_line', '销售发票明细', '财务', [R('invoice', '发票', 'ar_invoice', req=True), R('delivery_order', '发货单', 'delivery_order'),
                                              R('sales_return', '退货单', 'sales_return'), N('amount', '金额'), N('tax', '税额'), N('total', '价税合计')], menu=False),
    E('ar_receipt', '收款单', '财务', [
        CODE('收款单号', auto='SK{d}{n:03}'), R('customer', '客户', 'customer', req=True), D('receipt_date', '收款日期'), N('amount', '金额', req=True),
        EN('method', '方式', {'bank': '银行转账', 'acceptance': '承兑汇票', 'cash': '现金', 'other': '其他'}, default='bank'), S('bank_ref', '银行流水号'),
        N('applied', '已核销', ro=True, default=0), EN('status', '状态', {'open': '未核销完', 'applied': '已核销'}, default='open', ro=True), T('remark', '备注')],
      children=[('ar_apply', 'receipt')]),
    E('ar_apply', '收款核销', '财务', [R('receipt', '收款单', 'ar_receipt', req=True), R('invoice', '发票', 'ar_invoice', req=True), N('amount', '核销金额'),
                                    DT('time', '时间')], internal=True, menu=False),
    E('ap_invoice', '采购发票', '财务', [
        CODE('登记号', auto='SP{d}{n:03}'), S('invoice_no', '发票号码', search=True), EN('itype', '票种', {'special': '增值税专用发票', 'normal': '普通发票', 'electronic': '数电发票'},
                                                                                  default='special'),
        R('supplier', '供应商', 'supplier', req=True), D('invoice_date', '开票日期'), D('due_date', '到期日'), N('amount', '金额', ro=True), N('tax', '税额', ro=True),
        N('total', '价税合计', ro=True), N('paid', '已付', ro=True, default=0),
        EN('status', '状态', {'open': '未付', 'partial': '部分付款', 'paid': '已付清', 'void': '作废'}, default='open', ro=True), T('remark', '备注')],
      children=[('ap_invoice_line', 'invoice')], actions={'void': '作废'}),
    E('ap_invoice_line', '采购发票明细', '财务', [R('invoice', '发票', 'ap_invoice', req=True), R('purchase_receipt', '收货单', 'purchase_receipt'),
                                              R('supplier_return', '退供单', 'supplier_return'), N('amount', '金额'), N('tax', '税额'), N('total', '价税合计')], menu=False),
    E('ap_payment', '付款单', '财务', [
        CODE('付款单号', auto='FK{d}{n:03}'), R('supplier', '供应商', 'supplier', req=True), D('pay_date', '付款日期'), N('amount', '金额', req=True),
        EN('method', '方式', {'bank': '银行转账', 'acceptance': '承兑汇票', 'cash': '现金', 'other': '其他'}, default='bank'), S('bank_ref', '银行流水号'),
        N('applied', '已核销', ro=True, default=0), EN('status', '状态', {'open': '未核销完', 'applied': '已核销'}, default='open', ro=True), T('remark', '备注')],
      children=[('ap_apply', 'payment')]),
    E('ap_apply', '付款核销', '财务', [R('payment', '付款单', 'ap_payment', req=True), R('invoice', '发票', 'ap_invoice', req=True), N('amount', '核销金额'),
                                    DT('time', '时间')], internal=True, menu=False),

    # ───────────────── 凭证 ─────────────────
    E('acct_map', '科目映射', '财务', [
        EN('event', '业务', VOUCHER_EVENTS, req=True), EN('mtype', '物料类型', MTYPE, hint='只对"存货"有用：空 = 全部类型'),
        S('account_code', '科目编码', req=True), S('account_name', '科目名称'), EN('aux', '辅助核算', {'none': '无', 'customer': '客户', 'supplier': '供应商',
                                                                                       'dept': '部门', 'material': '物料'}, default='none'),
        S('remark', '备注', list=False)], order='event'),
    E('voucher', '凭证', '财务', [
        CODE('凭证号', auto='PZ{d}{n:03}'), S('period', '期间', search=True), D('vdate', '日期'), S('word', '凭证字', default='记'),
        EN('source_type', '业务', VOUCHER_SRC), S('source_code', '来源单据', search=True), S('summary', '摘要'), N('debit', '借方合计'), N('credit', '贷方合计'),
        EN('status', '状态', {'draft': '未导出', 'exported': '已导出', 'void': '作废'}, default='draft'), S('export_batch', '导出批次', list=False),
        DT('exported_at', '导出时间', list=False), S('src_key', '来源键', unique=True, list=False)],
      children=[('voucher_line', 'voucher')], internal=True, actions={'void': '作废'}),
    E('voucher_line', '凭证分录', '财务', [
        R('voucher', '凭证', 'voucher', req=True), I('seq', '行号'), S('summary', '摘要'), S('account_code', '科目编码'), S('account_name', '科目名称'),
        N('debit', '借方'), N('credit', '贷方'), S('aux_type', '辅助核算类型'), S('aux_code', '辅助核算编码'), S('aux_name', '辅助核算名称'), N('qty', '数量'),
        S('material', '物料编码', list=False)], internal=True, menu=False, order='seq'),
    E('voucher_export', '凭证导出', '财务', [CODE('批次号', auto='DC{d}{n:03}'), S('fmt', '格式'), S('period', '期间'), I('count', '凭证数'), S('file_name', '文件'),
                                          S('created_by', '导出人'), DT('time', '时间')], internal=True, menu=False),
]


def extend(entities: list):
    """把本模块的实体加进 ENTITIES，并给生产工单补"销售订单"（按单生产）字段。"""
    entities.extend(ENTITIES_ERP)
    by = {e.name: e for e in entities}

    def add(ent, *fields):
        have = {f.name for f in by[ent].fields}
        by[ent].fields.extend(f for f in fields if f.name not in have)
    add('work_order', R('sales_order', _tr('销售订单'), 'sales_order', list=False, hint=_tr('按单生产：这张工单是为哪个销售订单做的')),
        I('so_line', _tr('订单行'), list=False, ro=True))
    # 客户 / 供应商：M3（schema_impl.py）建了轻量的往来单位，这里补商务字段和下级
    add('customer', S('invoice_title', _tr('开票抬头'), list=False), S('bank', _tr('开户行'), list=False), S('bank_account', _tr('银行账号'), list=False),
        S('email', _tr('邮箱'), list=False), N('credit_limit', _tr('信用额度 元'), hint=_tr('0 或空 = 不控制')), EN('pay_type', _tr('结算方式'), PAY_T, default='net'),
        I('terms_days', _tr('账期 天'), default=30), N('tax_rate', _tr('税率 %'), default=13), S('currency', _tr('币别'), default='CNY', list=False), S('salesman', _tr('业务员')))
    by['customer'].children.extend(c for c in [('customer_contact', 'customer'), ('customer_part', 'customer'), ('sales_order', 'customer')]
                                   if c not in by['customer'].children)
    add('supplier', S('bank', _tr('开户行'), list=False), S('bank_account', _tr('银行账号'), list=False), S('email', _tr('邮箱'), list=False),
        EN('pay_type', _tr('结算方式'), PAY_T, default='net'), I('terms_days', _tr('账期 天'), default=30), N('tax_rate', _tr('税率 %'), default=13),
        EN('qual', _tr('资格'), {'approved': _tr('合格'), 'trial': _tr('试用'), 'blocked': _tr('停用')}, default='approved', hint=_tr('停用的供应商不能下采购订单')),
        N('rating', _tr('绩效得分'), ro=True))
    by['supplier'].children.extend(c for c in [('supplier_material', 'supplier'), ('purchase_order', 'supplier')] if c not in by['supplier'].children)
    # 期初应收 / 应付：发票上直接填金额（没有明细行）
    add('ar_invoice', B('opening', _tr('期初'), list=False))
    add('ap_invoice', B('opening', _tr('期初'), list=False))


# ───────────────── 集成用的普通表（不走通用增删改查：含密钥 / 系统流水） ─────────────────
T_ = {}


def tables(metadata):
    def t(name, *cols):
        tb = sa.Table(name, metadata, sa.Column('id', sa.Integer, primary_key=True, autoincrement=True), *cols)
        T_[name] = tb
        return tb
    # Webhook 订阅：事件 → URL，HMAC-SHA256 签名（secret 明文存库才能签名；界面只显示前 4 位）
    t('erp_webhook', sa.Column('name', sa.String(100)), sa.Column('url', sa.String(500)), sa.Column('events', sa.JSON), sa.Column('secret', sa.String(200)),
      sa.Column('enabled', sa.Boolean, default=True), sa.Column('max_attempts', sa.Integer, default=6), sa.Column('remark', sa.String(300)),
      sa.Column('last_ok', sa.DateTime), sa.Column('last_error', sa.String(500)), sa.Column('created_by', sa.String(80)), sa.Column('created_at', sa.DateTime))
    # 业务事件（MES 发生的、要告诉外部 ERP 的事）：完工、领料、收货、发货、库存……
    t('erp_event', sa.Column('event', sa.String(60), index=True), sa.Column('doc_type', sa.String(40)), sa.Column('doc_code', sa.String(80), index=True),
      sa.Column('payload', sa.JSON), sa.Column('key', sa.String(200), unique=True), sa.Column('created_at', sa.DateTime, index=True))
    # 每个事件到每个通道（webhook:<id> / staging / connector:<code>）的投递：重试、状态、最后响应
    t('erp_delivery', sa.Column('event', sa.Integer, index=True), sa.Column('channel', sa.String(60), index=True), sa.Column('status', sa.String(20), index=True),
      sa.Column('attempts', sa.Integer, default=0), sa.Column('next_try', sa.DateTime, index=True), sa.Column('last_code', sa.Integer),
      sa.Column('last_error', sa.Text), sa.Column('response', sa.Text), sa.Column('sent_at', sa.DateTime), sa.Column('created_at', sa.DateTime),
      sa.Index('ix_erp_delivery_ev_ch', 'event', 'channel', unique=True))
    # 外部编码映射：本地记录 ↔ 外部系统的编码 / 内码；hash = 最近一次同步内容的摘要（没变就跳过，同步幂等）
    t('erp_link', sa.Column('entity', sa.String(40)), sa.Column('local_id', sa.Integer, index=True), sa.Column('system', sa.String(20)),
      sa.Column('ext_id', sa.String(120)), sa.Column('ext_code', sa.String(120)), sa.Column('hash', sa.String(64)), sa.Column('synced_at', sa.DateTime),
      sa.Index('ux_erp_link_ext', 'entity', 'system', 'ext_id', unique=True))
    # 中间库：ERP → MES（ERP 写入，MES 同步作业读取并回写状态 / 错误）
    t('erp_stg_in', sa.Column('batch', sa.String(60), index=True), sa.Column('entity', sa.String(40)), sa.Column('ext_key', sa.String(120)),
      sa.Column('op', sa.String(10), default='upsert'), sa.Column('payload', sa.JSON), sa.Column('status', sa.String(20), index=True, default='new'),
      sa.Column('error', sa.Text), sa.Column('mes_id', sa.Integer), sa.Column('attempts', sa.Integer, default=0), sa.Column('hash', sa.String(64)),
      sa.Column('created_at', sa.DateTime), sa.Column('processed_at', sa.DateTime))
    # 中间库：MES → ERP（MES 写入，ERP 读取后回写 acked / error）
    t('erp_stg_out', sa.Column('event_id', sa.Integer, unique=True), sa.Column('event', sa.String(60), index=True), sa.Column('doc_type', sa.String(40)),
      sa.Column('doc_code', sa.String(80)), sa.Column('payload', sa.JSON), sa.Column('status', sa.String(20), index=True, default='new'),
      sa.Column('error', sa.Text), sa.Column('ack_note', sa.String(500)), sa.Column('created_at', sa.DateTime), sa.Column('read_at', sa.DateTime),
      sa.Column('acked_at', sa.DateTime))
    # 对接器（金蝶云星空 / 用友 U8 / YonSuite）配置：地址、账套、认证（密码 / 密钥只存库，界面打码）
    t('erp_connector', sa.Column('code', sa.String(40), unique=True, index=True), sa.Column('name', sa.String(100)), sa.Column('kind', sa.String(20)),
      sa.Column('enabled', sa.Boolean, default=False), sa.Column('base_url', sa.String(300)), sa.Column('config', sa.JSON), sa.Column('secret', sa.JSON),
      sa.Column('last_sync', sa.DateTime), sa.Column('last_status', sa.String(20)), sa.Column('last_error', sa.Text), sa.Column('updated_at', sa.DateTime))
    # 同步 / 对接运行日志
    t('erp_sync_log', sa.Column('channel', sa.String(60), index=True), sa.Column('direction', sa.String(10)), sa.Column('entity', sa.String(40)),
      sa.Column('total', sa.Integer), sa.Column('ok', sa.Integer), sa.Column('failed', sa.Integer), sa.Column('skipped', sa.Integer),
      sa.Column('detail', sa.JSON), sa.Column('started_at', sa.DateTime, index=True), sa.Column('ended_at', sa.DateTime), sa.Column('by', sa.String(80)))
    # 回传游标：哪些业务已经生成过事件（完工按工单累计数、领料 / 出入库按记录 id）
    t('erp_postback', sa.Column('key', sa.String(120), unique=True, index=True), sa.Column('value', sa.Float), sa.Column('updated_at', sa.DateTime))
    return T_


# ───────────────── 权限：模块键 + 默认角色补充（perms.py 调用） ─────────────────
NO_FIN = ['!finance.*', '!cost.*', '!page.finance.*']
ERP_ROLES = {
    'sales': ('销售员', ['page.*', '*.read', 'sales.*', 'master.customer.*', 'master.customer_part.*', '!cost.*', '!page.finance.cost', '!page.finance.vouchers']),
    'buyer': ('采购员', ['page.*', '*.read', 'purchase.*', 'master.supplier.*', 'mrp.mrp_suggestion.*', '!cost.*', '!page.finance.cost', '!page.finance.vouchers']),
    'finance': ('财务', ['page.*', '*.read', 'finance.*', 'cost.*', 'sales.customer_statement.*', 'purchase.supplier_statement.*', 'master.customer.update',
                       'master.supplier.update']),
}
# 已有角色追加（迁移 v0300 也按这张表给库里的内置角色补上）
ROLE_ADD = {
    'planner': ['mrp.*', 'sales.sales_order.make_wo', 'purchase.purchase_req.*'] + NO_FIN,
    'warehouse': ['sales.delivery_order.*', 'sales.sales_return.receive', 'purchase.purchase_receipt.*', 'purchase.receipt_line.*',
                  'purchase.supplier_return.*'] + NO_FIN,
    'leader': NO_FIN, 'operator': NO_FIN, 'qc': NO_FIN, 'maint': NO_FIN, 'engineer': NO_FIN,
}


def perms_extend(module_keys: dict, module_label: dict, default_roles: dict):
    module_keys.update(MODULES)
    module_label.update({v: k for k, v in MODULES.items()})
    for code, (name, pats) in ERP_ROLES.items():
        default_roles.setdefault(code, (name, pats))
    for code, add in ROLE_ADD.items():
        if code in default_roles:
            name, pats = default_roles[code]
            default_roles[code] = (name, list(pats) + [p for p in add if p not in pats])
    from .schema import ROLES
    for code, (name, _) in ERP_ROLES.items():
        ROLES.setdefault(code, name)
