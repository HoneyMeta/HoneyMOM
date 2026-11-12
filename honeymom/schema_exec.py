"""5.0 现场执行与质量合规（M5）的实体：条码标签、包装发运、委外、备件、文件受控与 ECN、IATF 16949（量检具、PFMEA / 控制计划、MSA、
客诉召回、电子签名）。schema.py 在建表之前调用 extend(ENTITIES)：把这里的实体加进去，并给已有实体补几个字段。

接口与说明见 docs/dev/exec.md；迁移 migrations/v0400_exec_quality.py。
"""
from .i18n import _tr
from .schema import B, CODE, D, DT, E, EN, I, J, N, NAME, R, S, T  # noqa: F401  （schema 正在初始化：这里只取它已定义的辅助函数）

# 模块（中文名 → 权限键）：perms.MODULE_KEYS 合并这张表
MODULES = {'标签打印': 'label', '包装发运': 'pack', '委外加工': 'outsource', '备件管理': 'spare', '工艺变更': 'ecn', '文件受控': 'doc',
           '量检具': 'gauge', 'IATF 质量': 'iatf', '客诉召回': 'complaint'}

# 默认角色在这些模块里的写权限（perms.DEFAULT_ROLES 合并；迁移 v0400 给已有库的内置角色补上）。所有人仍有 *.read。
ROLE_GRANTS = {
    'planner': ['outsource.*', 'pack.ship_load.*', 'label.print'],
    'leader': ['pack.*', 'label.print', 'label.reprint', 'label.print_job.*', 'spare.spare_txn.*'],
    'operator': ['pack.package.*', 'pack.package_item.*', 'pack.station', 'label.print'],
    'qc': ['gauge.*', 'iatf.*', 'complaint.*', 'label.print', 'label.reprint', 'label.print_job.*', 'doc.ctrl_doc_rev.review'],
    'maint': ['spare.*', 'gauge.gauge_cal.*'],
    'warehouse': ['pack.*', 'label.*', 'outsource.*', 'spare.*'],
    'engineer': ['ecn.*', 'doc.*', 'iatf.*', 'gauge.*', 'label.*', 'complaint.*'],
}

LABEL_USE = {'part': '零件（一件一码）', 'lot': '批次/炉次', 'carton': '箱标签', 'pallet': '托盘标签', 'customer': '客户标签', 'location': '库位',
             'gauge': '量具', 'tool': '工装/刀具', 'wo': '工单流转卡', 'other': '其他'}
GAUGE_T = {'caliper': '卡尺', 'micrometer': '千分尺', 'bore': '内径表', 'dial': '百分表', 'cmm': '三坐标', 'plug': '塞规', 'ring': '环规', 'thread': '螺纹规',
           'height': '高度尺', 'hardness': '硬度计', 'spectro': '光谱仪', 'thermo': '测温仪', 'fixture': '检具', 'block': '量块', 'other': '其他'}
SPECIAL = {'none': '一般', 'CC': '关键特性 CC', 'SC': '重要特性 SC'}
DOC_ST = {'draft': '草稿', 'review': '审核中', 'approved': '已批准', 'effective': '生效', 'obsolete': '作废'}
DOC_T = {'wi': '作业指导书', 'drawing': '图纸', 'control_plan': '控制计划', 'pfmea': 'PFMEA', 'nc': 'NC 程序', 'other': '其他'}
AP = {'H': '高', 'M': '中', 'L': '低'}

ENTITIES_EXEC = [
    # ---------------- 条码标签 ----------------
    E('label_template', '标签模板', '标签打印', [
        CODE(), NAME(), EN('usage', '用途', LABEL_USE, default='part'), N('width_mm', '宽 mm', default=100), N('height_mm', '高 mm', default=60),
        S('customer', '客户', hint='客户标签填客户编码；空 = 通用'), R('material', '产品', 'material'), J('layout', '版面', hint='设计器维护：元素列表（mm）'),
        J('sample', '样例数据'), EN('status', '状态', {'draft': '草稿', 'active': '启用', 'obsolete': '停用'}, default='active'), S('remark', '备注', list=False)],
      actions={'copy': '复制'}),
    E('printer', '标签打印机', '标签打印', [
        CODE(), NAME(), EN('kind', '指令', {'zpl': '斑马 ZPL', 'tspl': 'TSC TSPL', 'browser': '浏览器打印（PDF）'}, default='zpl'),
        S('host', 'IP 地址'), I('port', '端口', default=9100), I('dpi', '分辨率 dpi', default=203), I('darkness', '浓度', list=False),
        S('cjk_font', '中文字体', list=False, hint='ZPL 默认 E:SIMSUN.TTF；TSPL 默认 TSS24.BF2'), S('location', '位置'), B('enabled', '启用', default=True),
        EN('status', '状态', {'ok': '正常', 'error': '故障', 'unknown': '未知'}, default='unknown', ro=True), DT('last_ok', '最近成功', ro=True, list=False),
        S('last_error', '最近错误', ro=True, list=False)],
      actions={'test': '测试打印'}),
    E('marker', '激光打码机', '标签打印', [
        CODE(), NAME(), EN('mode', '连接', {'tcp': 'TCP 文本协议', 'serial_tcp': '串口服务器（TCP 透传）', 'sim': '模拟打码机'}, default='tcp'),
        S('host', 'IP 地址'), I('port', '端口', default=9000), T('cmd_template', '命令模板', hint='占位符 {data} {sn} {job} {fields.xxx}；\\r\\n 换行'),
        S('ack', '成功应答（正则）', default='OK', list=False), S('err', '失败应答（正则）', default='ERR|NG', list=False), I('timeout_ms', '超时 ms', default=5000, list=False),
        EN('symbology', '码制', {'datamatrix': 'DataMatrix', 'qr': 'QR', 'code128': 'Code128', 'text': '文字'}, default='datamatrix'),
        S('location', '位置'), B('enabled', '启用', default=True), EN('status', '状态', {'ok': '正常', 'error': '故障', 'unknown': '未知'}, default='unknown', ro=True),
        DT('last_ok', '最近成功', ro=True, list=False), S('last_error', '最近错误', ro=True, list=False)],
      actions={'test': '测试打码'}),
    E('label_rule', '自动打印规则', '标签打印', [
        EN('event', '时机', {'wo_release': '工单下达', 'part_create': '零件投产（一件一码）', 'carton_close': '装箱完成', 'pallet_close': '组托完成',
                             'receive': '收货', 'gauge_cal': '量具校准放行', 'outsource_send': '委外发出'}, default='carton_close'),
        R('template', '标签模板', 'label_template', req=True), R('printer', '打印机', 'printer'), R('marker', '打码机', 'marker'), I('copies', '份数', default=1),
        S('customer', '客户', hint='空 = 全部'), R('material', '产品', 'material', hint='空 = 全部'), B('enabled', '启用', default=True), S('remark', '备注', list=False)],
      order='event'),
    E('print_job', '打印任务', '标签打印', [
        CODE('任务号', auto='PJ{d}{n:04}'), EN('kind', '类型', {'print': '标签打印', 'mark': '激光打码', 'browser': '浏览器打印'}, default='print'),
        R('template', '模板', 'label_template'), R('printer', '打印机', 'printer'), R('marker', '打码机', 'marker'), S('target', '对象', search=True),
        S('ref', '单号/零件码', search=True), J('data', '数据'), T('payload', '指令内容'), I('copies', '份数', default=1),
        EN('status', '状态', {'queued': '排队', 'sending': '发送中', 'done': '完成', 'failed': '失败', 'cancelled': '已取消'}, default='queued'),
        I('attempts', '尝试次数', default=0), S('last_error', '错误', list=False), B('reprint', '补打'), S('reason', '补打原因'), S('requested_by', '申请人'),
        S('event', '触发', list=False), DT('next_try', '下次重试', list=False), DT('sent_at', '完成时间'), J('log', '发送日志')],
      internal=True, actions={'retry': '重试', 'cancel': '取消'}),

    # ---------------- 包装发运 ----------------
    E('pack_rule', '包装规则', '包装发运', [
        S('customer', '客户', hint='客户编码；空 = 通用'), R('material', '产品', 'material', req=True), S('customer_part_no', '客户零件号'),
        I('carton_qty', '每箱数量', req=True, default=20), I('pallet_cartons', '每托箱数', default=24), B('mixed_lot', '允许混批'),
        R('carton_template', '箱标签模板', 'label_template'), R('pallet_template', '托盘标签模板', 'label_template'),
        R('customer_template', '客户标签模板', 'label_template'), S('remark', '备注', list=False)], order='customer'),
    E('package', '包装（箱/托）', '包装发运', [
        CODE('箱号/托号'), EN('kind', '类型', {'carton': '箱', 'pallet': '托盘'}, default='carton'), R('parent', '所在托盘', 'package'), R('material', '产品', 'material'),
        S('customer', '客户'), S('customer_part_no', '客户零件号', list=False), I('qty', '数量', default=0), I('capacity', '容量'), S('lots', '批次/炉次'),
        EN('status', '状态', {'open': '装箱中', 'closed': '已封箱', 'loaded': '已装车', 'shipped': '已发运'}, default='open'), R('load', '装车单', 'ship_load'),
        R('rule', '包装规则', 'pack_rule', list=False), S('station', '包装工位', list=False), S('packed_by', '包装人'), DT('closed_at', '封箱时间'),
        DT('shipped_at', '发运时间', list=False)], internal=True),
    E('package_item', '箱内明细', '包装发运', [R('package', '箱', 'package', req=True), S('sn', '零件码', search=True), S('lot', '批次/炉次'), I('qty', '数量', default=1),
                                           R('material', '产品', 'material'), S('by', '装箱人'), DT('time', '时间')], internal=True, menu=False),
    E('ship_load', '装车发运', '包装发运', [
        CODE('装车单号', auto='ZC{d}{n:03}'), S('delivery_code', '发货单号', search=True, hint='内置 ERP 的发货单号（外部 ERP 时手填）'), I('delivery_id', '发货单 ID', list=False),
        S('customer', '客户', req=True), S('ship_to', '收货地址', list=False), S('vehicle', '车牌'), S('driver', '司机', list=False), S('dock', '月台', list=False),
        DT('plan_time', '计划发车'), EN('status', '状态', {'planned': '计划', 'loading': '装车中', 'shipped': '已发运', 'cancelled': '已取消'}, default='planned', ro=True),
        I('pallets', '托数', ro=True, default=0), I('cartons', '箱数', ro=True, default=0), I('qty', '件数', ro=True, default=0),
        S('asn_no', 'ASN 号', ro=True), DT('asn_at', 'ASN 时间', ro=True, list=False), DT('shipped_at', '发运时间', ro=True), S('shipped_by', '发运人', ro=True, list=False),
        R('shipment', '发运记录', 'shipment', ro=True, list=False), T('remark', '备注')],
      actions={'ship': '发运确认', 'asn': '生成 ASN', 'cancel': '取消'}),

    # ---------------- 委外 ----------------
    E('outsource_order', '委外单', '委外加工', [
        CODE('委外单号', auto='WW{d}{n:03}'), S('supplier', '供应商', req=True, search=True, hint='供应商编码或名称（内置 ERP 的供应商主数据）'), I('supplier_id', '供应商 ID', list=False),
        R('process', '委外工序', 'process'), R('work_order', '工单', 'work_order'), R('material', '物料', 'material', req=True), N('qty', '委外数量', req=True),
        N('qty_sent', '已发出', ro=True, default=0), N('qty_back', '已收回', ro=True, default=0), N('qty_ok', '合格', ro=True, default=0),
        N('qty_scrap', '报废', ro=True, default=0), N('qty_lost', '丢失', ro=True, default=0), N('unit_price', '加工单价 元'), N('loss_pct', '允许损耗 %', default=0.5, list=False),
        N('cost', '结算金额 元', ro=True), N('lead_days', '加工周期 天', default=3), DT('due', '要求回厂'), DT('sent_at', '发出时间', ro=True, list=False),
        DT('received_at', '回厂时间', ro=True, list=False), R('warehouse', '发出仓库', 'warehouse', list=False), R('os_warehouse', '委外仓', 'warehouse', ro=True, list=False),
        EN('status', '状态', {'draft': '草稿', 'sent': '已发出', 'partial': '部分收回', 'received': '已收回', 'closed': '已结算', 'cancelled': '已取消'}, default='draft', ro=True),
        B('ap_posted', '已转应付', ro=True), S('ap_ref', '应付单号', ro=True, list=False), T('remark', '备注')],
      children=[('outsource_line', 'order')], actions={'send': '发出', 'receive': '收回检验', 'close': '结算关闭', 'cancel': '取消'}),
    E('outsource_line', '委外明细', '委外加工', [
        R('order', '委外单', 'outsource_order', req=True), EN('direction', '方向', {'out': '发出', 'back': '收回'}), S('sn', '零件码', search=True), S('lot', '批次'),
        N('qty', '数量'), N('qty_ok', '合格'), N('qty_scrap', '报废'), N('qty_lost', '丢失'), R('record', '检验单', 'insp_record'),
        EN('result', '检验', {'OK': '合格', 'NG': '不合格', 'partial': '部分合格'}), S('by', '经办'), DT('time', '时间'), S('remark', '说明', list=False)],
      internal=True, menu=False),

    # ---------------- 备件 ----------------
    E('spare_part', '备件', '备件管理', [
        CODE('备件编码'), NAME(), S('spec', '规格'), S('unit', '单位', default='个'), S('applies', '适用设备/类型'), R('equipment', '主要设备', 'equipment', list=False),
        N('qty', '库存', ro=True, default=0), N('min_qty', '最低库存'), N('max_qty', '最高库存'), N('price', '单价 元'), S('supplier', '供应商'),
        N('lead_days', '采购周期 天', default=7, list=False), S('location', '库位'), B('critical', '关键备件')]),
    E('spare_txn', '备件出入库', '备件管理', [
        CODE('单号', auto='BJ{d}{n:04}'), R('spare', '备件', 'spare_part', req=True),
        EN('kind', '类型', {'in': '入库', 'issue': '领用', 'return': '退回', 'adjust': '盘点调整'}, default='issue'), N('qty', '数量', req=True),
        R('repair_order', '维修工单', 'repair_order'), R('maint_record', '保养记录', 'maint_record', list=False), R('equipment', '设备', 'equipment'),
        S('by', '经办'), DT('time', '时间'), N('price', '单价', list=False), N('balance', '结存', ro=True), S('remark', '备注', list=False)]),

    # ---------------- 文件受控与 ECN ----------------
    E('ctrl_doc', '受控文件', '文件受控', [
        CODE('文件编号', auto='DOC{n:04}'), S('title', '文件名称', req=True, search=True), EN('dtype', '类型', DOC_T, default='wi'), R('product', '产品', 'material'),
        R('process', '工序', 'process'), S('owner', '责任人'), S('cur_rev', '现行版本', ro=True), I('cur_rev_id', '现行版本 ID', ro=True, list=False),
        EN('status', '状态', {'draft': '起草中', 'effective': '生效', 'obsolete': '作废'}, default='draft', ro=True)],
      children=[('ctrl_doc_rev', 'doc')], actions={'revise': '升版'}),
    E('ctrl_doc_rev', '文件版本', '文件受控', [
        R('doc', '文件', 'ctrl_doc', req=True), S('rev', '版本', req=True), EN('status', '状态', DOC_ST, default='draft', ro=True),
        S('target_entity', '内容记录类型', ro=True, list=False), I('target_id', '内容记录 ID', ro=True, list=False), D('effective_date', '生效日期'),
        T('change_note', '变更说明'), R('ecn', 'ECN', 'ecn'), S('author', '编制'), S('reviewed_by', '审核', ro=True), S('approved_by', '批准', ro=True),
        DT('approved_at', '批准时间', ro=True, list=False), DT('effective_at', '生效时间', ro=True), DT('obsolete_at', '作废时间', ro=True, list=False)],
      menu=False, actions={'submit': '提交审核', 'review': '审核通过', 'approve': '批准（电子签名）', 'effective': '立即生效', 'reject': '退回', 'obsolete': '作废'}),
    E('ecn', '工艺变更 ECN', '工艺变更', [
        CODE('ECN 号', auto='ECN{d}{n:02}'), S('title', '变更标题', req=True, search=True),
        EN('ctype', '变更类型', {'process': '工艺', 'design': '设计/图纸', 'material': '材料', 'supplier': '供应商', 'cp': '控制计划', 'other': '其他'}, default='process'),
        R('product', '产品', 'material'), T('reason', '变更原因'), T('description', '变更内容'),
        EN('status', '状态', {'request': '申请', 'analysis': '影响分析', 'approval': '待批准', 'implementing': '实施中', 'effective': '已生效',
                              'rejected': '已驳回', 'cancelled': '已取消'}, default='request', ro=True),
        EN('wo_policy', '在制工单', {'allow': '照旧生产', 'block': '停工待处置', 'finish': '做完再切换'}, default='finish'), D('effective_date', '计划生效'),
        J('impact', '影响分析', ro=True), J('snapshot', '新旧版本', ro=True), S('requested_by', '申请人', ro=True), S('approved_by', '批准人', ro=True),
        DT('approved_at', '批准时间', ro=True, list=False), DT('effective_at', '生效时间', ro=True), I('flow', '审批流程实例', ro=True, list=False)],
      children=[('ecn_task', 'ecn'), ('ctrl_doc_rev', 'ecn'), ('ecn_wo', 'ecn')],
      actions={'analyze': '影响分析', 'submit': '提交批准', 'approve': '批准（电子签名）', 'reject': '驳回', 'effective': '生效', 'cancel': '取消'}),
    E('ecn_task', 'ECN 实施任务', '工艺变更', [R('ecn', 'ECN', 'ecn', req=True), I('seq', '顺序'), S('title', '任务', req=True), S('owner', '负责人'), D('due', '完成期限'),
                                           EN('status', '状态', {'open': '待办', 'done': '已完成', 'skipped': '不适用'}, default='open'), S('done_by', '完成人', ro=True),
                                           DT('done_at', '完成时间', ro=True), S('note', '说明')], order='seq', menu=False, actions={'done': '完成'}),
    E('ecn_wo', 'ECN 在制工单', '工艺变更', [R('ecn', 'ECN', 'ecn', req=True), R('work_order', '工单', 'work_order', req=True),
                                          EN('decision', '处置', {'allow': '照旧生产', 'block': '停工'}, default='allow'), B('released', '已解除'),
                                          S('released_by', '解除人', ro=True), DT('released_at', '解除时间', ro=True), S('note', '说明')],
      menu=False, actions={'unblock': '解除停工'}),

    # ---------------- 量检具 ----------------
    E('gauge', '量检具', '量检具', [
        CODE('量具编号'), NAME(), EN('gtype', '类型', GAUGE_T, default='caliper'), S('spec', '量程/规格'), N('resolution', '分辨力', list=False), S('serial', '出厂编号', list=False),
        S('maker', '制造商', list=False), S('location', '使用地点'), S('owner', '保管人'), I('cal_interval_days', '校准周期 天', default=365), D('last_cal', '上次校准'),
        D('next_due', '下次到期'), EN('status', '状态', {'active': '有效', 'due': '即将到期', 'overdue': '过期锁定', 'quarantine': '待放行', 'locked': '锁定',
                                                         'repair': '维修', 'scrapped': '报废'}, default='quarantine', ro=True),
        S('lock_reason', '锁定原因', ro=True), S('cert_no', '证书编号', ro=True, list=False), N('grr_pct', '%GRR', ro=True), I('ndc', 'ndc', ro=True, list=False),
        I('msa', '最近 MSA', ro=True, list=False), S('released_by', '放行人', ro=True, list=False), DT('released_at', '放行时间', ro=True, list=False)],
      children=[('gauge_cal', 'gauge'), ('msa_study', 'gauge')], actions={'release': '放行（电子签名）', 'lock': '锁定', 'scrap': '报废'}),
    E('gauge_cal', '校准记录', '量检具', [
        CODE('校准单号', auto='JZ{d}{n:03}'), R('gauge', '量具', 'gauge', req=True), D('cal_date', '校准日期', req=True),
        EN('result', '结论', {'pass': '合格', 'adjust': '调整后合格', 'fail': '不合格'}, default='pass'), EN('agency', '方式', {'internal': '内校', 'external': '外校'}, default='external'),
        S('provider', '校准机构'), S('cert_no', '证书编号'), S('cert_file', '证书文件', ro=True, list=False), S('std_used', '使用标准器', list=False), T('readings', '校准数据'),
        D('next_due', '下次到期'), S('by', '校准人'), T('remark', '备注')]),

    # ---------------- PFMEA / 控制计划 / MSA ----------------
    E('pfmea', 'PFMEA', 'IATF 质量', [
        CODE('PFMEA 编号', auto='PF{d}{n:02}'), S('title', '名称', req=True, search=True), R('product', '产品', 'material'), S('rev', '版本', default='A'),
        EN('status', '状态', {'draft': '草稿', 'review': '评审', 'approved': '已批准', 'obsolete': '作废'}, default='draft'),
        EN('method', '评价方法', {'ap': 'AP（AIAG-VDA 2019）', 'rpn': 'RPN（第四版）'}, default='ap'), S('team', '小组'), D('fmea_date', '日期'), T('scope', '范围')],
      children=[('pfmea_row', 'pfmea')], actions={'derive_cp': '生成控制计划'}),
    E('pfmea_row', 'PFMEA 条目', 'IATF 质量', [
        R('pfmea', 'PFMEA', 'pfmea', req=True), I('seq', '顺序'), R('process', '过程步骤', 'process'), S('function', '过程功能/要求'),
        S('product_char', '产品特性'), S('process_char', '过程特性', list=False), S('char', '特性代码', hint='SPC / 检验项目用，如 BORE_DIA'),
        EN('special', '特殊特性', SPECIAL, default='none'), N('lo', '下限', list=False), N('hi', '上限', list=False), S('unit', '单位', list=False),
        S('failure_mode', '失效模式'), S('effect', '失效影响'), I('severity', 'S'), S('cause', '失效原因'), S('prevention', '预防控制', list=False), I('occurrence', 'O'),
        S('detection_ctrl', '探测控制', list=False), I('detection', 'D'), I('rpn', 'RPN', ro=True), EN('ap', 'AP', AP, ro=True), T('actions', '改进措施'),
        S('owner', '责任人', list=False), D('due', '完成日期', list=False), EN('action_status', '措施状态', {'open': '未完成', 'done': '已完成'}, list=False),
        I('s2', 'S 措施后', list=False), I('o2', 'O 措施后', list=False), I('d2', 'D 措施后', list=False), EN('ap2', 'AP 措施后', AP, ro=True, list=False),
        B('ai', 'AI 起草', ro=True, list=False)], order='seq', menu=False),
    E('control_plan', '控制计划', 'IATF 质量', [
        CODE('控制计划编号', auto='CP{d}{n:02}'), S('title', '名称', req=True, search=True), R('product', '产品', 'material'),
        EN('phase', '阶段', {'proto': '样件', 'prelaunch': '试生产', 'production': '量产'}, default='production'), S('rev', '版本', default='A'), R('pfmea', 'PFMEA', 'pfmea'),
        EN('status', '状态', DOC_ST, default='draft', ro=True), R('ctrl_doc', '受控文件', 'ctrl_doc', ro=True, list=False), S('team', '小组', list=False)],
      children=[('cp_item', 'plan')], actions={'gen_insp': '生成/同步检验模板', 'to_doc': '纳入文件受控'}),
    E('cp_item', '控制计划项', 'IATF 质量', [
        R('plan', '控制计划', 'control_plan', req=True), I('seq', '顺序'), R('process', '过程', 'process'), S('machine', '机器/装置/工装'), S('char_no', '特性编号'),
        S('product_char', '产品特性'), S('process_char', '过程特性', list=False), S('char', '特性代码'), EN('special', '特殊特性', SPECIAL, default='none'),
        S('spec', '规范/公差'), N('lo', '下限'), N('hi', '上限'), S('unit', '单位'), S('method', '评价测量技术'), EN('gauge_type', '量具类型', GAUGE_T),
        R('gauge', '指定量具', 'gauge', list=False), S('sample_size', '样本容量'), S('frequency', '频率'), S('control_method', '控制方法'), T('reaction', '反应计划'),
        R('pfmea_row', 'PFMEA 条目', 'pfmea_row', list=False), R('insp_item', '检验项目', 'insp_item', ro=True, list=False)], order='seq', menu=False),
    E('msa_study', 'MSA 研究', 'IATF 质量', [
        CODE('编号', auto='MSA{d}{n:02}'), S('title', '名称', req=True), EN('method', '方法', {'grr_xbar': 'GR&R 均值极差法', 'grr_anova': 'GR&R 方差分析法', 'bias': '偏倚',
                                                                                       'linearity': '线性', 'attribute': '计数型一致性'}, default='grr_anova'),
        R('gauge', '量具', 'gauge'), S('char', '特性'), N('tolerance', '公差'), N('reference', '基准值', list=False), I('parts', '零件数', default=10), I('appraisers', '评价人数', default=3),
        I('trials', '测量次数', default=3), J('data', '数据'), J('result', '结果', ro=True), N('grr_pct', '%GRR', ro=True), I('ndc', 'ndc', ro=True),
        EN('verdict', '结论', {'acceptable': '可接受', 'conditional': '有条件接受', 'unacceptable': '不可接受'}, ro=True), D('study_date', '日期'), S('by', '分析人'),
        T('conclusion', '说明')], actions={'calc': '计算'}),
    E('esign', '电子签名', 'IATF 质量', [
        S('entity', '对象类型'), I('entity_id', '对象 ID'), S('action', '操作'), S('meaning', '签名含义'), S('user', '账号', search=True), S('name', '姓名'),
        S('record_label', '对象', search=True), DT('signed_at', '签名时间'), S('ip', 'IP', list=False), S('hash', '签名摘要', list=False)], internal=True, order='-id'),

    # ---------------- 客诉与召回 ----------------
    E('complaint', '客户投诉', '客诉召回', [
        CODE('投诉编号', auto='KS{d}{n:03}'), S('customer', '客户', req=True, search=True), S('customer_ref', '客户编号', list=False), S('delivery_code', '发货单号'),
        R('load', '装车单', 'ship_load', list=False), R('material', '产品', 'material'), T('sns', '零件码', hint='逗号/换行分隔'), S('lots', '批次/炉次'), S('cartons', '箱号/托号', list=False),
        I('qty', '投诉数量'), R('defect', '缺陷', 'defect_code'), T('description', '问题描述'),
        EN('severity', '严重度', {'critical': '致命', 'major': '严重', 'minor': '轻微'}, default='major'), DT('received_at', '接到时间'),
        EN('status', '状态', {'open': '已登记', 'containment': '遏制中', 'analysis': '分析中', 'closed': '已关闭'}, default='open', ro=True),
        J('impact', '影响范围', ro=True), T('containment', '遏制措施'), R('report_8d', '8D 报告', 'report_8d', ro=True), S('rma_code', 'RMA 退货单'),
        I('holds', '冻结单数', ro=True, default=0), B('notified', '已通知', ro=True), S('closed_by', '关闭人', ro=True), DT('closed_at', '关闭时间', ro=True, list=False)],
      actions={'analyze': '影响分析', 'contain': '冻结在库（遏制）', 'notify': '通知相关人', 'to8d': '发起 8D', 'rma': '建退货单（RMA）', 'close': '关闭（电子签名）'}),
]


def extend(entities: list):
    """把本模块的实体加进 ENTITIES，并给已有实体补字段 / 动作（只追加，不改原定义的含义）。"""
    by = {e.name: e for e in entities}
    entities.extend(ENTITIES_EXEC)

    def add(ent, *fields):
        have = {f.name for f in by[ent].fields}
        by[ent].fields.extend(f for f in fields if f.name not in have)
    # 检验：量具类型 / 特殊特性 / 来自控制计划；检验值记录用的量具
    add('insp_item', EN('gauge_type', _tr('量具类型'), GAUGE_T, list=False, hint=_tr('填了就必须选一把有效（已校准）的该类量具才能录入')),
        EN('special', _tr('特殊特性'), SPECIAL, default='none', list=False), R('cp_item', _tr('控制计划项'), 'cp_item', ro=True, list=False))
    add('insp_record', R('gauge', _tr('主量具'), 'gauge', list=False))
    add('insp_value', R('gauge', _tr('量具'), 'gauge'))
    # 工艺步骤：委外
    add('route_step', B('outsourced', _tr('委外'), hint=_tr('这道工序由供应商做（热处理、涂装…）；排产按供应商周期')), S('supplier', _tr('委外供应商')),
        N('os_lead_days', _tr('委外周期 天')))
    # 知识库 / NC 程序：受控状态（空 = 不受控，照旧显示；受控的只显示生效版本）
    add('kb_doc', EN('ctrl_status', _tr('受控状态'), DOC_ST, ro=True), R('ctrl_doc', _tr('受控文件'), 'ctrl_doc', ro=True, list=False))
    add('nc_program', R('ctrl_doc', _tr('受控文件'), 'ctrl_doc', ro=True, list=False))
    # 8D：关闭要电子签名（状态只能走动作改）
    add('report_8d', S('closed_by', _tr('关闭人'), ro=True), DT('closed_at', _tr('关闭时间'), ro=True, list=False), R('complaint', _tr('客户投诉'), 'complaint', ro=True, list=False))
    for f in by['report_8d'].fields:
        if f.name == 'status':
            f.ro = True
    by['report_8d'].actions.update({'submit_review': _tr('提交评审'), 'close': _tr('关闭（电子签名）')})
    # 仓库类型：委外仓（供应商处的库存）
    for f in by['warehouse'].fields:
        if f.name == 'wtype' and f.enum is not None:
            f.enum.setdefault('outsource', '委外仓')
