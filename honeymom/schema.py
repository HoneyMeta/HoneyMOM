"""实体定义：一份定义同时生成数据库表、通用 CRUD 接口和前端列表/表单。

F(字段名, 中文名, 类型, ...)：类型 str/text/int/float/bool/date/datetime/enum/ref/json
E(实体名, 中文名, 模块, 字段..., children=[(子实体, 外键字段)], actions={动作: 中文名}, internal=只读/隐藏)
"""
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa


@dataclass
class F:
    name: str
    label: str
    t: str = 'str'
    ref: str | None = None
    enum: dict | None = None
    req: bool = False
    list: bool = True            # 是否出现在列表
    default: Any = None
    ro: bool = False             # 表单里只读（系统维护）
    auto: str | None = None      # 自动编号格式，如 'WO{d}{n:03}'
    search: bool = False
    unique: bool = False
    hint: str = ''


@dataclass
class E:
    name: str
    label: str
    module: str
    fields: list
    children: list = field(default_factory=list)
    actions: dict = field(default_factory=dict)
    internal: bool = False       # 系统写入、界面只读
    order: str = '-id'
    menu: bool = True


def S(n, l, **k): return F(n, l, 'str', **k)
def T(n, l, **k): return F(n, l, 'text', list=False, **k)
def I(n, l, **k): return F(n, l, 'int', **k)
def N(n, l, **k): return F(n, l, 'float', **k)
def B(n, l, **k): return F(n, l, 'bool', **k)
def D(n, l, **k): return F(n, l, 'date', **k)
def DT(n, l, **k): return F(n, l, 'datetime', **k)
def EN(n, l, enum, **k): return F(n, l, 'enum', enum=enum, **k)
def R(n, l, ref, **k): return F(n, l, 'ref', ref=ref, **k)
def J(n, l, **k): return F(n, l, 'json', list=False, **k)
def CODE(l='编码', auto=None): return F('code', l, 'str', req=auto is None, unique=True, search=True, auto=auto)
def NAME(l='名称'): return F('name', l, 'str', req=True, search=True)


PROCESS_T = {'melting': '熔炼', 'treatment': '球化孕育', 'molding': '造型浇注', 'cooling': '冷却落砂', 'cleaning': '清理抛丸', 'receiving': '来料',
             'machining': '机加工', 'deburr': '去毛刺', 'inspection': '检测', 'storage': '入库', 'other': '其他'}
EQUIP_T = {'furnace': '中频炉', 'treatment': '球化站', 'sand': '砂处理', 'molding': '造型线', 'cooling': '冷却线', 'shotblast': '抛丸机', 'receiving': '收货台',
           'cnc': '加工中心', 'deburr': '去毛刺单元', 'inspection': '检测站', 'warehouse': '立体仓库', 'agv': 'AGV', 'crew': '班组', 'other': '其他'}
ROLES = {'admin': '系统管理员', 'manager': '厂长/经理', 'planner': '计划员', 'leader': '班组长', 'operator': '操作工', 'qc': '质检员', 'maint': '设备员',
         'warehouse': '仓管员', 'engineer': '工艺工程师'}

ENTITIES: list[E] = [
    # ---------------- 主数据 ----------------
    E('plant', '工厂', '主数据', [CODE(), NAME(), S('address', '地址'), S('vf_source', '虚拟工厂来源', list=False, hint='如 http://127.0.0.1:8800|foundry-housing')],
      children=[('workshop', 'plant')]),
    E('workshop', '车间', '主数据', [CODE(), NAME(), R('plant', '工厂', 'plant'),
                                   EN('wtype', '类型', {'foundry': '铸造', 'machining': '机加', 'warehouse': '仓储', 'other': '其他'})],
      children=[('line', 'workshop')]),
    E('line', '产线', '主数据', [CODE(), NAME(), R('workshop', '车间', 'workshop')], children=[('workstation', 'line')]),
    E('process', '工序', '主数据', [CODE(), NAME(), EN('ptype', '类型', PROCESS_T), N('std_cycle_s', '标准节拍 s'), T('desc', '说明')]),
    E('workstation', '工位', '主数据', [CODE(), NAME(), R('line', '产线', 'line'), R('process', '工序', 'process'), R('equipment', '设备', 'equipment'),
                                     S('group', '工位组', hint='同组工位可互相替代，如 CNC')]),
    E('material', '物料', '主数据', [CODE(), NAME(), S('spec', '规格'), S('unit', '单位', default='件'),
                                  EN('mtype', '类型', {'raw': '原材料', 'semi': '半成品', 'fg': '成品', 'aux': '辅料', 'consumable': '易耗品'}),
                                  N('safety_stock', '安全库存'), N('price', '单价 元'), N('weight_kg', '单重 kg')]),
    E('bom', 'BOM', '主数据', [R('product', '产品', 'material', req=True), R('component', '子件', 'material', req=True), N('qty', '用量', req=True),
                             S('unit', '单位'), S('remark', '备注')], order='product'),
    E('route', '工艺路线', '主数据', [CODE(), NAME(), R('product', '产品', 'material'), S('version', '版本', default='V1'),
                                   EN('status', '状态', {'draft': '草稿', 'active': '生效', 'obsolete': '作废'}, default='active')],
      children=[('route_step', 'route')]),
    E('route_step', '工艺步骤', '主数据', [R('route', '工艺路线', 'route', req=True), I('seq', '顺序', req=True), R('process', '工序', 'process', req=True),
                                       S('station_group', '工位组/工位', hint='如 CNC、MOLD1'), N('std_cycle_s', '标准节拍 s'),
                                       B('inspection', '检验点'), EN('report_mode', '报工方式', {'auto': '设备自动', 'manual': '人工', 'scan': '扫码'}, default='auto'),
                                       B('fai', '首检放行', hint='开工/换型后须首检放行才能量产报工'), T('instruction', '作业指示'), J('params', '工艺参数')],
      order='seq', menu=False),
    E('shift', '班次', '主数据', [CODE(), NAME(), S('start', '开始', hint='HH:MM'), S('end', '结束'), B('active', '排产启用', default=True),
                                I('break_min', '休息 min', default=0)]),
    # 排产约束主数据（services/scheduling.py 使用）
    E('skill', '人员技能', '主数据', [R('employee', '员工', 'employee', req=True), R('process', '工序', 'process'), S('station_group', '工位组', hint='如 CNC；空=该工序全部'),
                                   R('equipment', '设备', 'equipment', hint='空=工位组内全部设备'),
                                   EN('level', '等级', {'1': '学徒（需带教）', '2': '熟练', '3': '骨干', '4': '可带教'}, default='2'), D('valid_until', '有效期至')], order='employee'),
    E('changeover', '换型时间', '主数据', [S('station_group', '工位组', req=True), R('from_product', '换型前产品', 'material'), R('to_product', '换型后产品', 'material'),
                                       N('minutes', '换型 min', req=True), S('remark', '说明')], order='station_group'),
    E('team', '班组', '主数据', [CODE(), NAME(), R('shift', '班次', 'shift'), S('leader', '班组长')], children=[('employee', 'team')]),
    E('employee', '员工', '主数据', [CODE('工号'), NAME('姓名'), R('team', '班组', 'team'), S('post', '岗位'), S('phone', '电话'), S('skills', '技能')]),
    E('defect_code', '缺陷代码', '质量管理', [CODE(), NAME(), EN('category', '类别', {'casting': '铸造', 'machining': '机加', 'appearance': '外观', 'other': '其他'}),
                                         EN('severity', '严重度', {'critical': '致命', 'major': '严重', 'minor': '轻微'}), R('process', '责任工序', 'process')]),

    # ---------------- 生产 ----------------
    E('work_order', '生产工单', '生产管理', [
        CODE('工单号', auto='WO{d}{n:03}'), R('product', '产品', 'material', req=True), R('route', '工艺路线', 'route'), I('qty', '计划数量', req=True),
        I('qty_done', '完工', ro=True, default=0), I('qty_scrap', '报废', ro=True, default=0), DT('start_plan', '计划开始'), DT('due', '交期'),
        EN('status', '状态', {'draft': '草稿', 'released': '已下达', 'in_progress': '生产中', 'completed': '已完工', 'closed': '已关闭', 'cancelled': '已取消'},
           default='draft', ro=True),
        I('priority', '优先级', default=5, hint='1 最高'), S('customer', '客户'), S('remark', '备注', list=False),
        DT('released_at', '下达时间', ro=True, list=False), DT('completed_at', '完工时间', ro=True, list=False)],
      children=[('wo_op', 'work_order'), ('report', 'work_order'), ('material_issue', 'work_order')],
      actions={'release': '下达', 'close': '关闭', 'cancel': '取消'}),
    E('wo_op', '工单工序', '生产管理', [
        R('work_order', '工单', 'work_order', req=True), I('seq', '顺序'), R('process', '工序', 'process'), S('station_group', '工位组'),
        I('qty_plan', '计划'), I('qty_good', '合格', default=0), I('qty_ng', '不良', default=0), DT('plan_start', '计划开始'), DT('plan_end', '计划结束'),
        DT('act_start', '实际开始'), DT('act_end', '实际结束'),
        EN('status', '状态', {'waiting': '待开工', 'ready': '可开工', 'running': '进行中', 'done': '完成'}, default='waiting'),
        R('assignee', '派工给', 'employee'), S('machines', '排产设备', list=False), N('setup_min', '换型 min', list=False), J('conflicts', '排程冲突')],
      order='seq', menu=False, actions={'dispatch': '派工'}),
    E('report', '报工记录', '生产管理', [
        CODE('报工单号', auto='BG{d}{n:04}'), R('work_order', '工单', 'work_order'), R('wo_op', '工序', 'wo_op', list=False), R('process', '工序', 'process'),
        R('employee', '报工人', 'employee'), R('equipment', '设备', 'equipment'), I('qty_good', '合格'), I('qty_ng', '不良'), R('defect', '缺陷', 'defect_code'),
        DT('start', '开始'), DT('end', '结束'),
        EN('source', '来源', {'auto': '设备自动', 'manual': '人工', 'mobile': '手机扫码', 'scan': '扫码枪', 'terminal': '工位终端'}, default='manual'),
        S('remark', '备注', list=False), I('interval_min', '汇总间隔 min', list=False, hint='设备自动报工按此间隔汇总成一条'),
        R('shift', '班次', 'shift', list=False), S('idem_key', '幂等键', list=False, unique=True, ro=True)]),
    E('material_issue', '领料退料', '生产管理', [
        CODE('单号', auto='LL{d}{n:03}'), EN('kind', '类型', {'issue': '领料', 'return': '退料'}, default='issue'), R('work_order', '工单', 'work_order'),
        R('material', '物料', 'material', req=True), N('qty', '数量', req=True), R('warehouse', '仓库', 'warehouse'), S('lot', '批次'),
        R('employee', '经办人', 'employee'), DT('time', '时间')]),
    E('wo_kitting', '物料齐套', '生产管理', [
        R('work_order', '工单', 'work_order', req=True), R('material', '物料', 'material', req=True), N('qty_need', '需求'), N('qty_ready', '已备'),
        DT('eta', '预计到料', hint='缺料时填写；排产从到料时间之后开始'), EN('status', '齐套', {'ready': '齐套', 'partial': '部分', 'short': '缺料'}, ro=True),
        S('remark', '备注', list=False)], order='work_order'),
    E('attendance', '上下工记录', '生产管理', [
        R('employee', '员工', 'employee', req=True), S('workstations', '看管设备', hint='一人多机：逗号分隔'), R('shift', '班次', 'shift'),
        DT('clock_on', '上工'), DT('clock_off', '下工'), N('hours', '工时 h', ro=True), EN('source', '来源', {'terminal': '工位终端', 'mobile': '手机', 'manual': '补录'}, default='terminal')],
      order='-clock_on'),
    E('worker_daily', '工人日报', '生产管理', [
        CODE('日报号', auto='RB{d}{n:03}'), D('day', '日期', req=True), R('shift', '班次', 'shift'), R('employee', '员工', 'employee', req=True), R('team', '班组', 'team'),
        I('qty_good', '合格'), I('qty_ng', '不良'), N('hours', '工时 h'), N('downtime_min', '停机 min'), T('remark', '备注'), J('lines', '明细'),
        EN('status', '状态', {'draft': '草稿', 'submitted': '待审核', 'approved': '已审核', 'rejected': '已退回'}, default='draft', ro=True),
        DT('submitted_at', '提交时间', ro=True, list=False), S('reviewer', '审核人', ro=True), DT('reviewed_at', '审核时间', ro=True, list=False), S('review_note', '审核意见', list=False)],
      order='-day', actions={'approve': '审核通过', 'reject': '退回'}),
    E('part', '在制品/一件一码', '生产管理', [
        S('sn', '零件码', search=True, unique=True), R('product', '产品', 'material'), R('work_order', '工单', 'work_order'),
        EN('status', '状态', {'wip': '在制', 'good': '检验合格', 'fg': '成品入库', 'shipped': '已发运', 'scrap': '报废', 'raw_store': '毛坯库', 'rework': '返修中', 'hold': '冻结'}),
        S('station', '当前位置'), S('heat', '炉次/批次', search=True), S('ladle', '浇包', list=False), S('mold', '砂型', list=False), I('cavity', '型腔', list=False),
        N('pour_temp', '浇注温度'), N('mg', '残镁 %', list=False), N('sand_moisture', '型砂水分 %', list=False), N('compactability', '紧实率', list=False),
        N('ce', '碳当量', list=False), S('machine', '加工机床'), I('rework_count', '返修次数', default=0), S('scrap_cause', '报废原因'),
        DT('born_at', '投产时间'), DT('finished_at', '完成时间', list=False)], internal=True, order='-id'),
    E('part_event', '零件事件', '生产管理', [S('sn', '零件码', search=True), DT('t', '时间'), S('station', '工位'), S('event', '事件'), J('data', '数据')],
      internal=True, menu=False),
    E('shipment', '发运记录', '仓储物流', [CODE('发运单', auto='FY{d}{n:03}'), I('qty', '数量'), S('customer', '客户'), R('work_order', '工单', 'work_order'),
                                       DT('time', '时间')], internal=True),

    # ---------------- 质量 ----------------
    E('insp_template', '检验模板', '质量管理', [CODE(), NAME(), EN('itype', '检验类型', {'IQC': '来料检验', 'IPQC': '过程检验', 'FAI': '首件检验', 'OQC': '出货检验', 'patrol': '巡检'}),
                                           R('material', '物料', 'material'), R('process', '工序', 'process'), S('sample_rule', '抽样规则')],
      children=[('insp_item', 'template')]),
    E('insp_item', '检验项目', '质量管理', [R('template', '模板', 'insp_template', req=True), I('seq', '顺序'), NAME('项目'), S('char', '特性代码', hint='SPC 用，如 BORE_DIA'),
                                       S('method', '方法/量具'), S('std', '标准'), N('lo', '下限'), N('hi', '上限'), S('unit', '单位'), B('key', '关键特性')],
      order='seq', menu=False),
    E('insp_record', '检验记录', '质量管理', [
        CODE('检验单号', auto='JY{d}{n:04}'), R('template', '模板', 'insp_template'), EN('itype', '类型', {'IQC': '来料', 'IPQC': '过程', 'FAI': '首件', 'OQC': '出货', 'patrol': '巡检'}),
        R('work_order', '工单', 'work_order'), S('sn', '零件码', search=True), S('lot', '批次'), S('station', '工位'), I('qty_sample', '样本数', default=1),
        I('qty_ng', '不合格数', default=0), EN('result', '结论', {'OK': '合格', 'NG': '不合格', 'pending': '待判'}, default='pending'),
        R('inspector', '检验员', 'employee'), DT('time', '时间'), EN('source', '来源', {'auto': '设备自动', 'manual': '人工', 'import': '导入'}, default='manual'),
        S('remark', '备注', list=False)], children=[('insp_value', 'record')]),
    E('insp_value', '检验值', '质量管理', [R('record', '检验单', 'insp_record', req=True), R('item', '项目', 'insp_item'), S('char', '特性代码'),
                                       N('value', '测量值'), S('text', '文字结果'), B('ok', '合格')], internal=True, menu=False),
    E('ncr', '不合格品', '质量管理', [
        CODE('NCR 号', auto='NCR{d}{n:03}'), S('sn', '零件码', search=True), S('lot', '批次'), R('work_order', '工单', 'work_order'), R('material', '物料', 'material'),
        I('qty', '数量', default=1), R('defect', '缺陷', 'defect_code'), S('station', '发现工位'), DT('found_at', '发现时间'), S('found_by', '发现人'),
        EN('source', '来源', {'auto': '设备自动', 'manual': '人工'}, default='manual'),
        EN('disposition', '处置', {'pending': '待处置', 'rework': '返修', 'scrap': '报废', 'concession': '让步接收', 'return': '退供应商'}, default='pending'),
        EN('status', '状态', {'open': '待处理', 'reviewing': '评审中', 'closed': '已关闭'}, default='open'), T('root_cause', '原因分析'), T('action', '纠正措施'),
        DT('closed_at', '关闭时间', list=False)],
      children=[('rework', 'ncr')], actions={'rework': '返修', 'scrap': '报废', 'concession': '让步接收', 'close': '关闭', 'to8d': '发起 8D'}),
    E('rework', '返修记录', '质量管理', [CODE('单号', auto='FX{d}{n:03}'), R('ncr', 'NCR', 'ncr'), S('sn', '零件码', search=True), S('station', '返修工位'),
                                     R('employee', '返修人', 'employee'), EN('result', '结果', {'ok': '合格', 'fail': '仍不合格', 'doing': '进行中'}), DT('time', '时间'),
                                     S('remark', '备注'), S('method', '返修方法', list=False), DT('finished_at', '返修完成', list=False),
                                     R('recheck', '复检单', 'insp_record', ro=True), EN('recheck_result', '复检结论', {'OK': '合格', 'NG': '不合格'}, ro=True),
                                     S('recheck_by', '复检人', ro=True, list=False), DT('recheck_at', '复检时间', ro=True)],
      actions={'finish': '返修完成', 'recheck': '复检'}),
    # 首检放行：开工 / 换型 / 换刀后首件检验合格并放行，才能量产报工（services/gates.py）
    E('first_article', '首检', '质量管理', [
        CODE('首检单号', auto='SJ{d}{n:03}'), R('work_order', '工单', 'work_order', req=True), R('wo_op', '工序', 'wo_op', list=False), R('process', '工序', 'process'),
        R('equipment', '设备', 'equipment'), S('sn', '首件零件码'), EN('trigger', '触发', {'start': '开工', 'changeover': '换型', 'tool': '换刀', 'shift': '换班', 'repair': '维修后'}, default='start'),
        EN('status', '状态', {'pending': '待检', 'approved': '已放行', 'rejected': '不放行'}, default='pending', ro=True), R('record', '检验单', 'insp_record', ro=True),
        S('requested_by', '申请人'), DT('requested_at', '申请时间'), S('decided_by', '判定人', ro=True), DT('decided_at', '放行时间', ro=True), S('remark', '说明')],
      actions={'approve': '放行', 'reject': '不放行'}),
    E('lot_hold', '批次冻结', '质量管理', [
        CODE('冻结单号', auto='HD{d}{n:03}'), S('lot', '批次/炉次', req=True, search=True), R('material', '物料', 'material'), S('reason', '冻结原因', req=True),
        EN('status', '状态', {'hold': '冻结中', 'released': '已解冻'}, default='hold', ro=True), I('parts', '涉及零件', ro=True), S('held_by', '冻结人', ro=True),
        DT('held_at', '冻结时间', ro=True), S('released_by', '解冻人', ro=True, list=False), DT('released_at', '解冻时间', ro=True), S('release_note', '解冻说明', list=False)],
      actions={'release': '解冻'}),
    E('report_8d', '8D 报告', '质量管理', [
        CODE('编号', auto='8D{d}{n:02}'), S('title', '标题', req=True), R('ncr', 'NCR', 'ncr'), R('defect', '缺陷', 'defect_code'),
        T('d1', 'D1 团队'), T('d2', 'D2 问题描述'), T('d3', 'D3 临时措施'), T('d4', 'D4 根本原因'), T('d5', 'D5 永久措施'), T('d6', 'D6 验证'),
        T('d7', 'D7 预防再发'), T('d8', 'D8 总结'), EN('status', '状态', {'draft': '草稿', 'review': '评审', 'closed': '关闭'}, default='draft'),
        B('ai_generated', 'AI 起草', ro=True)], actions={'ai_draft': 'AI 起草'}),

    # ---------------- 设备 ----------------
    E('equipment', '设备台账', '设备管理', [
        CODE('设备编号'), NAME('设备名称'), EN('etype', '类型', EQUIP_T), R('line', '产线', 'line'), S('model', '型号'), S('vendor', '厂家'),
        D('install_date', '投用日期', list=False), N('rated_kw', '额定功率 kW', list=False), N('ideal_cycle_s', '理想节拍 s'),
        EN('status', '状态', {'running': '运行', 'idle': '待机', 'setup': '换型', 'down': '停机', 'offline': '离线', 'scrapped': '报废'}, ro=True),
        S('source', '数据来源', list=False, hint='数据源编码:设备号，如 VF:CNC1'), S('location', '位置', list=False)],
      children=[('tag_point', 'equipment'), ('maint_plan', 'equipment'), ('repair_order', 'equipment'), ('tool', 'equipment')]),
    E('datasource', '数据源', '系统', [CODE(), NAME(), EN('protocol', '协议', {'mqtt': 'MQTT (UNS)', 'opcua': 'OPC UA', 'modbus': 'Modbus TCP'}),
                                    S('endpoint', '地址', hint='mqtt://host:1883 / opc.tcp://host:4840/… / host:5020'), S('prefix', '主题/节点前缀'),
                                    B('enabled', '启用', default=True), S('status', '运行状态', ro=True), S('remark', '备注', list=False)],
      children=[('tag_point', 'datasource')], actions={'import_vf': '从虚拟工厂导入', 'restart': '重连'}),
    E('tag_point', '采集点', '系统', [R('equipment', '设备', 'equipment'), R('datasource', '数据源', 'datasource'), S('name', '点位', search=True),
                                   S('address', '地址', hint='MQTT 主题 / OPC UA NodeId / Modbus unit:table:addr'), S('dtype', '类型'), S('unit', '单位'),
                                   N('scale', '缩放', list=False),
                                   EN('role', '用途', {'state': '运行状态', 'alarm': '报警', 'power': '功率', 'energy': '电能', 'count': '产量', 'load': '主轴负载',
                                                     'temp': '温度', 'quality': '质量值', 'other': '其他'}),
                                   S('desc', '说明'), B('enabled', '启用', default=True)], menu=False),
    E('equip_state', '设备状态记录', '设备管理', [R('equipment', '设备', 'equipment'), S('state', '状态'), DT('start', '开始'), DT('end', '结束'), N('dur_s', '时长 s'),
                                             S('reason', '原因')], internal=True, menu=False),
    E('alarm', '报警记录', '设备管理', [R('equipment', '设备', 'equipment'), S('msg', '报警内容', search=True), DT('start', '开始'), DT('end', '结束'), N('dur_s', '时长 s'),
                                     S('ack_by', '确认人'), DT('ack_at', '确认时间', list=False), S('note', '处理说明')], actions={'ack': '确认'}),
    E('maint_plan', '点检保养计划', '设备管理', [CODE(), NAME(), R('equipment', '设备', 'equipment'), EN('mtype', '类型', {'spot': '日常点检', 'maint': '定期保养', 'lube': '润滑'}),
                                             I('cycle_days', '周期 天', default=1), T('items', '内容'), D('last_done', '上次完成'), D('next_due', '下次到期'),
                                             R('owner', '负责人', 'employee')], children=[('maint_record', 'plan')], actions={'execute': '执行'}),
    E('maint_record', '点检保养记录', '设备管理', [CODE('单号', auto='DJ{d}{n:03}'), R('plan', '计划', 'maint_plan'), R('equipment', '设备', 'equipment'),
                                               R('executor', '执行人', 'employee'), DT('time', '时间'), EN('result', '结果', {'ok': '正常', 'abnormal': '异常'}), S('notes', '说明')]),
    E('repair_order', '维修工单', '设备管理', [
        CODE('维修单号', auto='WX{d}{n:03}'), R('equipment', '设备', 'equipment', req=True), S('fault', '故障描述', req=True), S('reported_by', '报修人'),
        DT('reported_at', '报修时间'), EN('level', '紧急程度', {'urgent': '紧急', 'normal': '一般'}, default='normal'),
        EN('status', '状态', {'reported': '已报修', 'assigned': '已派工', 'repairing': '维修中', 'done': '已修复', 'verified': '已验收'}, default='reported', ro=True),
        R('assignee', '维修人', 'employee'), DT('start', '开始维修', list=False), DT('end', '修复时间', list=False), N('downtime_min', '停机 min'),
        T('cause', '故障原因'), T('solution', '处理方法'), S('parts_used', '更换备件', list=False)],
      actions={'assign': '派工', 'start': '开始维修', 'finish': '修复完成', 'verify': '验收'}),
    E('tooling', '模具/夹具', '设备管理', [
        CODE('模具编号'), NAME(), EN('ttype', '类型', {'pattern': '模板/模具', 'core_box': '芯盒', 'fixture': '夹具', 'gauge': '检具', 'other': '其他'}, default='pattern'),
        R('product', '适用产品', 'material'), R('process', '工序', 'process'), S('station_group', '适用工位组'), R('equipment', '当前所在设备', 'equipment'),
        I('shots', '当前模次', default=0), I('life_shots', '寿命模次'), I('maint_every', '保养周期 模次'), I('last_maint_shots', '上次保养模次', default=0, list=False),
        EN('status', '状态', {'available': '可用', 'in_use': '在用', 'maint': '保养/维修中', 'scrapped': '报废'}, default='available'),
        DT('available_from', '预计可用', hint='保养/维修中时填写'), S('location', '存放位置', list=False), S('remark', '备注', list=False)],
      actions={'maint_done': '保养完成'}),
    E('downtime_plan', '设备停机计划', '设备管理', [
        R('equipment', '设备', 'equipment', req=True), EN('kind', '类型', {'maint': '计划保养', 'repair': '维修', 'holiday': '放假/停产', 'trial': '试制/调试', 'other': '其他'}, default='maint'),
        DT('start', '开始', req=True), DT('end', '结束', req=True), S('reason', '说明'), S('created_by', '登记人', list=False)], order='-start'),

    # ---------------- 仓储 ----------------
    E('warehouse', '仓库', '仓储物流', [CODE(), NAME(), EN('wtype', '类型', {'raw': '原材料库', 'blank': '毛坯库', 'wip': '线边库', 'fg': '成品库', 'aux': '辅料库'})],
      children=[('location', 'warehouse'), ('stock', 'warehouse')]),
    E('location', '库位', '仓储物流', [CODE(), NAME(), R('warehouse', '仓库', 'warehouse'), I('capacity', '容量')], menu=False),
    E('stock', '库存', '仓储物流', [R('material', '物料', 'material'), R('warehouse', '仓库', 'warehouse'), R('location', '库位', 'location'), S('lot', '批次'),
                                  N('qty', '数量'), DT('updated_at', '更新时间')], internal=True),
    E('stock_txn', '出入库记录', '仓储物流', [
        CODE('单号', auto='CK{d}{n:04}'), EN('ttype', '类型', {'in': '入库', 'out': '出库', 'transfer': '移库', 'adjust': '盘点调整'}),
        R('material', '物料', 'material', req=True), N('qty', '数量', req=True), R('warehouse', '仓库', 'warehouse', req=True), R('location', '库位', 'location', list=False),
        R('to_warehouse', '调入仓库', 'warehouse', list=False), S('lot', '批次'), S('ref_doc', '关联单据'), S('employee', '经办'), DT('time', '时间'), S('reason', '原因')]),
    E('agv_task', 'AGV 任务', '仓储物流', [S('agv', 'AGV'), S('task', '任务'), S('src', '起点'), S('dst', '终点'), I('load', '件数'), DT('start', '开始'), DT('end', '结束'),
                                       S('state', '状态')], internal=True),

    # ---------------- 能源 ----------------
    E('energy_hour', '小时电耗', '能源管理', [R('equipment', '设备', 'equipment'), S('area', '区域'), DT('hour', '小时'), N('kwh', '电量 kWh')], internal=True, menu=False),

    # ---------------- 铸造 ----------------
    E('heat', '炉次记录', '铸造', [
        CODE('炉次号'), R('furnace', '电炉', 'equipment'), DT('start', '开始'), DT('end', '结束'), N('kg', '出铁量 kg'), N('kwh', '电耗 kWh'), N('kwh_per_t', '吨耗 kWh/t'),
        N('C', 'C %'), N('Si', 'Si %'), N('Mn', 'Mn %', list=False), N('S', 'S %', list=False), N('P', 'P %', list=False), N('CE', '碳当量'), N('tap_temp', '出铁温度'),
        N('lining', '炉衬余厚 mm', list=False), I('castings', '铸件数', ro=True, default=0), I('scrap', '报废数', ro=True, default=0),
        EN('status', '状态', {'melting': '熔炼中', 'holding': '保温出铁', 'done': '结束'})], children=[('treatment_rec', 'heat_code')], internal=True),
    E('treatment_rec', '球化记录', '铸造', [S('ladle', '浇包'), S('heat_code', '炉次'), N('temp', '温度 °C'), N('mg', '残镁 %'), N('kg', '铁水 kg'), DT('time', '时间')],
      internal=True),
    E('charge_material', '炉料', '铸造', [CODE(), NAME(), N('price', '价格 元/t', req=True), N('C', 'C %'), N('Si', 'Si %'), N('Mn', 'Mn %'), N('S', 'S %'), N('P', 'P %'),
                                       N('min_ratio', '最小配比 %'), N('max_ratio', '最大配比 %'), N('stock_t', '库存 t'), N('yield_pct', '收得率 %', default=95)]),
    E('charge_plan', '配料方案', '铸造', [CODE('方案号', auto='PL{d}{n:02}'), N('furnace_kg', '炉料重量 kg'), N('target_C', '目标 C'), N('target_Si', '目标 Si'),
                                       N('cost', '吨成本 元/t'), J('detail', '配比'), DT('time', '时间'), S('remark', '说明')], internal=True),

    # ---------------- 机加 ----------------
    E('nc_program', 'NC 程序', '机加', [CODE('程序号'), NAME(), R('product', '产品', 'material'), S('station_group', '适用机床/组'), S('version', '版本', default='V1'),
                                     EN('status', '状态', {'draft': '编制', 'approved': '已审核', 'released': '已下发', 'obsolete': '作废'}, default='draft', ro=True),
                                     T('content', '程序内容'), S('approved_by', '审核人', ro=True), DT('released_at', '下发时间', ro=True), S('remark', '变更说明')],
      actions={'approve': '审核通过', 'release': '下发到机床', 'obsolete': '作废'}),
    E('tool', '刀具台账', '机加', [S('code', '刀位', req=True, search=True), NAME('刀具'), R('equipment', '机床', 'equipment'), S('ttype', '类型'), I('life_limit', '寿命 件'), I('used', '已用 件', ro=True),
                                I('changes', '换刀次数', ro=True, default=0), N('wear_pct', 'AI 估计磨损 %', ro=True), DT('last_change', '上次换刀', ro=True),
                                EN('status', '状态', {'ok': '正常', 'warn': '预警', 'change': '待换刀'}, ro=True, default='ok')], actions={'change': '换刀'}),
    E('tool_change', '换刀记录', '机加', [R('equipment', '机床', 'equipment'), S('tool', '刀位'), S('tool_name', '刀具'), I('used', '使用件数'),
                                       EN('reason', '原因', {'life': '到寿命', 'broken': '断刀', 'manual': '人工', 'ai': 'AI 预警'}), DT('time', '时间')], internal=True),

    # ---------------- AI 与知识 ----------------
    E('kb_doc', '知识库', 'AI 助手', [S('title', '标题', req=True, search=True), S('tags', '标签'), T('content', '内容'), DT('updated', '更新时间', ro=True),
                                    EN('kind', '类型', {'sop': '作业指导书', 'drawing': '加工图纸', 'standard': '标准规范', 'other': '其他'}, default='sop'),
                                    R('product', '产品', 'material'), R('process', '工序', 'process'), S('version', '版本'),
                                    S('file_name', '文件', ro=True), S('file_type', '文件类型', ro=True, list=False), I('file_size', '大小', ro=True, list=False)]),
    E('daily_report', '日报', 'AI 助手', [D('day', '日期'), T('content', '内容'), J('kpi', '指标'), EN('by', '生成', {'ai': '大模型', 'rule': '规则'}), DT('generated_at', '生成时间')],
      internal=True, menu=False),
    E('ai_log', '问答记录', 'AI 助手', [S('user', '用户'), T('question', '问题'), T('answer', '回答'), T('sqls', '执行的查询'), DT('time', '时间')], internal=True, menu=False),

    # ---------------- 安灯 / 申请 / 建议 / 通知（智能体动作也写这些表） ----------------
    E('andon', '安灯', '生产管理', [
        CODE('安灯号', auto='AD{d}{n:03}'), EN('atype', '类型', {'equipment': '设备', 'quality': '质量', 'material': '物料', 'safety': '安全', 'other': '其他'}, default='equipment'),
        R('equipment', '设备', 'equipment'), S('station', '工位'), S('description', '问题描述', req=True), S('raised_by', '发起人'), DT('raised_at', '发起时间'),
        EN('status', '状态', {'open': '待响应', 'responding': '处理中', 'closed': '已关闭'}, default='open', ro=True), S('responder', '响应人'),
        DT('response_at', '响应时间', list=False), DT('closed_at', '关闭时间', list=False), S('close_note', '处理结果', list=False),
        EN('source', '来源', {'manual': '人工', 'auto': '设备自动', 'agent': '智能体'}, default='manual')],
      actions={'respond': '响应', 'close': '关闭'}),
    E('material_request', '物料申请', '生产管理', [
        CODE('申请单号', auto='WL{d}{n:03}'), R('material', '物料', 'material', req=True), N('qty', '数量', req=True), R('warehouse', '发料仓库', 'warehouse'),
        R('work_order', '工单', 'work_order'), S('reason', '原因'), S('requested_by', '申请人'), DT('requested_at', '申请时间'),
        EN('status', '状态', {'submitted': '已提交', 'issued': '已发料', 'rejected': '已退回'}, default='submitted', ro=True),
        EN('source', '来源', {'manual': '人工', 'agent': '智能体'}, default='manual')], actions={'issue': '发料', 'reject': '退回'}),
    E('schedule_proposal', '排程调整建议', '生产管理', [
        CODE('建议号', auto='PC{d}{n:03}'), S('title', '标题', req=True), R('work_order', '工单', 'work_order'), T('proposal', '建议内容'), J('changes', '调整项'),
        J('impact', '影响评估'),
        EN('status', '状态', {'pending': '待处理', 'accepted': '已采纳', 'rejected': '未采纳'}, default='pending', ro=True), S('created_by', '提出人'),
        EN('source', '来源', {'manual': '人工', 'agent': '智能体'}, default='manual')], actions={'accept': '采纳', 'reject': '不采纳'}),
    E('notice', '站内通知', '系统', [S('title', '标题', search=True), T('content', '内容'), S('to_role', '接收角色'), S('to_user', '接收人'),
                                  EN('channel', '渠道', {'inbox': '站内信', 'board': '看板'}, default='inbox'), EN('level', '级别', {'info': '一般', 'warn': '重要', 'urgent': '紧急'}, default='info'),
                                  S('link', '链接', list=False), S('source', '来源'), B('read', '已读', default=False), DT('time', '时间')], internal=True, menu=False),
    E('ai_report', 'AI 报告', 'AI 助手', [S('title', '标题', search=True), S('kind', '类型'), T('content', '内容'), S('agent', '智能体'), DT('time', '时间')], internal=True),

    # ---------------- 系统 ----------------
    E('sys_user', '用户', '系统', [S('username', '账号', req=True, unique=True, search=True), S('name', '姓名'), S('roles', '角色', hint='逗号分隔：' + ','.join(ROLES)),
                                 R('employee', '员工', 'employee'), B('active', '启用', default=True), S('password_hash', '密码', list=False, ro=True),
                                 J('scope', '数据范围', ro=True, hint='空 = 按角色；在"系统 → 角色与权限"里设置')],
      actions={'reset_pw': '重置密码'}),
    E('setting', '系统参数', '系统', [S('key', '参数', req=True, unique=True), S('value', '值'), S('remark', '说明')], menu=False),
]

# 5.0 现场执行与质量合规（M5）：实体在 schema_exec.py，这里并入（并给已有实体补字段），之后统一建表
from .schema_exec import extend as _exec_extend  # noqa: E402
_exec_extend(ENTITIES)
# 5.0 工厂模型与接入（M2）：设备模板实体 + 给设备 / 数据源 / 采集点 / 工单补字段（schema_factory.py）
from .schema_factory import extend as _factory_extend  # noqa: E402
_factory_extend(ENTITIES)
# 5.0 实施工具与可配置（M3）：计量单位、单位换算、客户、供应商、实施任务（schema_impl.py）
from .schema_impl import extend as _impl_extend  # noqa: E402
_impl_extend(ENTITIES)
# 5.0 内置 ERP（M4）：销售 / 采购 / MRP / 成本 / 应收应付 / 凭证（schema_erp.py，docs/dev/erp.md）；要在 M3 之后（扩展它的客户 / 供应商）
from .schema_erp import extend as _erp_extend  # noqa: E402
_erp_extend(ENTITIES)

BY_NAME = {e.name: e for e in ENTITIES}
COLTYPE = {'str': sa.String(200), 'text': sa.Text, 'int': sa.Integer, 'float': sa.Float, 'bool': sa.Boolean, 'date': sa.Date, 'datetime': sa.DateTime,
           'enum': sa.String(40), 'ref': sa.Integer, 'json': sa.JSON}
# 非 int 外键：按字段名特判
STR_REF = set()

metadata = sa.MetaData()
TABLES: dict[str, sa.Table] = {}
for e in ENTITIES:
    cols = [sa.Column('id', sa.Integer, primary_key=True, autoincrement=True)]
    for f in e.fields:
        cols.append(sa.Column(f.name, COLTYPE[f.t], unique=f.unique, index=f.unique or f.t == 'ref' or f.search or f.name in ('sn', 't', 'time', 'hour', 'start')))
    names = {f.name for f in e.fields}
    cols += [sa.Column(n, sa.DateTime) for n in ('created_at', 'updated_at') if n not in names]
    TABLES[e.name] = sa.Table(e.name, metadata, *cols)

# 设备点位最新值（采集写，界面读）
tag_latest = sa.Table('tag_latest', metadata, sa.Column('key', sa.String(300), primary_key=True), sa.Column('equipment', sa.Integer, index=True),
                      sa.Column('name', sa.String(200)), sa.Column('value', sa.String(200)), sa.Column('num', sa.Float), sa.Column('ts', sa.DateTime))
# 少量时序（主轴负载、温度等）用于趋势和 AI
tag_sample = sa.Table('tag_sample', metadata, sa.Column('id', sa.Integer, primary_key=True), sa.Column('equipment', sa.Integer, index=True),
                      sa.Column('name', sa.String(200), index=True), sa.Column('ts', sa.DateTime, index=True), sa.Column('num', sa.Float))


# ---------------- 智能体（不走通用增删改查） ----------------
def _t(name, *cols):
    return sa.Table(name, metadata, sa.Column('id', sa.Integer, primary_key=True, autoincrement=True), *cols)


agent_t = _t('agent', sa.Column('name', sa.String(200)), sa.Column('description', sa.Text), sa.Column('icon', sa.String(40)),
             sa.Column('template', sa.String(60)), sa.Column('enabled', sa.Boolean, default=False), sa.Column('chat_callable', sa.Boolean, default=False),
             sa.Column('draft', sa.JSON), sa.Column('published_version', sa.Integer), sa.Column('permissions', sa.JSON),
             sa.Column('created_by', sa.String(80)), sa.Column('created_at', sa.DateTime), sa.Column('updated_at', sa.DateTime),
             sa.Column('last_run_at', sa.DateTime), sa.Column('last_status', sa.String(20)))
agent_version_t = _t('agent_version', sa.Column('agent', sa.Integer, index=True), sa.Column('version', sa.Integer), sa.Column('dsl', sa.JSON),
                     sa.Column('note', sa.String(400)), sa.Column('published_by', sa.String(80)), sa.Column('published_at', sa.DateTime))
agent_run_t = _t('agent_run', sa.Column('agent', sa.Integer, index=True), sa.Column('version', sa.Integer), sa.Column('mode', sa.String(10)),
                 sa.Column('trigger', sa.String(20)), sa.Column('trigger_key', sa.String(200), index=True), sa.Column('inputs', sa.JSON),
                 sa.Column('status', sa.String(20), index=True), sa.Column('dsl', sa.JSON), sa.Column('state', sa.JSON), sa.Column('output', sa.JSON),
                 sa.Column('error', sa.Text), sa.Column('tokens', sa.Integer, default=0), sa.Column('steps', sa.Integer, default=0),
                 sa.Column('run_as', sa.String(80)), sa.Column('created_by', sa.String(80)), sa.Column('created_at', sa.DateTime, index=True),
                 sa.Column('started_at', sa.DateTime), sa.Column('ended_at', sa.DateTime))
agent_step_t = _t('agent_run_step', sa.Column('run', sa.Integer, index=True), sa.Column('node', sa.String(60)), sa.Column('ntype', sa.String(40)),
                  sa.Column('name', sa.String(200)), sa.Column('status', sa.String(20)), sa.Column('input', sa.JSON), sa.Column('output', sa.JSON),
                  sa.Column('messages', sa.JSON), sa.Column('tool_calls', sa.JSON), sa.Column('tokens', sa.Integer), sa.Column('attempt', sa.Integer),
                  sa.Column('iteration', sa.Integer), sa.Column('error', sa.Text), sa.Column('started_at', sa.DateTime), sa.Column('ended_at', sa.DateTime),
                  sa.Column('duration_ms', sa.Integer))
approval_t = _t('approval', sa.Column('run', sa.Integer, index=True), sa.Column('agent', sa.Integer, index=True), sa.Column('node', sa.String(60)),
                sa.Column('kind', sa.String(20)), sa.Column('title', sa.String(300)), sa.Column('summary', sa.Text), sa.Column('payload', sa.JSON),
                sa.Column('fields', sa.JSON), sa.Column('result', sa.JSON), sa.Column('assignee_role', sa.String(40)), sa.Column('status', sa.String(20), index=True),
                sa.Column('dry_run', sa.Boolean), sa.Column('decided_by', sa.String(80)), sa.Column('decided_at', sa.DateTime), sa.Column('reason', sa.Text),
                sa.Column('due_at', sa.DateTime), sa.Column('created_at', sa.DateTime))
agent_action_t = _t('agent_action_log', sa.Column('idem_key', sa.String(120), unique=True), sa.Column('run', sa.Integer, index=True), sa.Column('node', sa.String(60)),
                    sa.Column('action', sa.String(40)), sa.Column('payload', sa.JSON), sa.Column('result', sa.JSON), sa.Column('dry_run', sa.Boolean),
                    sa.Column('created_at', sa.DateTime))
agent_event_t = _t('agent_event', sa.Column('agent', sa.Integer, index=True), sa.Column('kind', sa.String(40)), sa.Column('key', sa.String(200)),
                   sa.Column('payload', sa.JSON), sa.Column('outcome', sa.String(20)), sa.Column('run', sa.Integer), sa.Column('created_at', sa.DateTime, index=True))
audit_t = _t('audit_log', sa.Column('time', sa.DateTime, index=True), sa.Column('user', sa.String(80)), sa.Column('kind', sa.String(40)),
             sa.Column('target', sa.String(200)), sa.Column('detail', sa.JSON))
thread_t = _t('assistant_thread', sa.Column('user', sa.String(80), index=True), sa.Column('title', sa.String(200)), sa.Column('created_at', sa.DateTime),
              sa.Column('updated_at', sa.DateTime))
message_t = _t('assistant_message', sa.Column('thread', sa.Integer, index=True), sa.Column('role', sa.String(20)), sa.Column('content', sa.Text),
               sa.Column('page', sa.String(300)), sa.Column('record', sa.JSON), sa.Column('steps', sa.JSON), sa.Column('receipts', sa.JSON),
               sa.Column('cards', sa.JSON), sa.Column('created_at', sa.DateTime))

# ---------------- AI 体验（4.7；原来定义在 services/ai_plus|ai_automation|ai_watch，5.0 起所有表都在本文件，迁移才看得全） ----------------
ai_pin_t = _t('ai_pin', sa.Column('user', sa.String(80), index=True), sa.Column('title', sa.String(200)), sa.Column('block', sa.JSON),
              sa.Column('source', sa.String(200)), sa.Column('created_at', sa.DateTime))
ai_automation_t = _t('ai_automation', sa.Column('name', sa.String(200)),
                     sa.Column('text', sa.Text), sa.Column('flow', sa.JSON), sa.Column('status', sa.String(20), index=True), sa.Column('replay', sa.JSON),
                     sa.Column('state', sa.JSON), sa.Column('created_by', sa.String(80)), sa.Column('created_at', sa.DateTime), sa.Column('updated_at', sa.DateTime),
                     sa.Column('last_eval', sa.DateTime), sa.Column('last_fire', sa.DateTime), sa.Column('fire_count', sa.Integer, default=0), sa.Column('by', sa.String(10)))
ai_automation_fire_t = _t('ai_automation_fire', sa.Column('rule', sa.Integer, index=True),
                          sa.Column('t', sa.DateTime, index=True), sa.Column('equipment', sa.String(60)), sa.Column('value', sa.Float), sa.Column('detail', sa.JSON),
                          sa.Column('created_at', sa.DateTime))
ai_finding_t = _t('ai_finding', sa.Column('key', sa.String(200), index=True),
                  sa.Column('watcher', sa.String(60)), sa.Column('domain', sa.String(20), index=True), sa.Column('severity', sa.String(10)),
                  sa.Column('title', sa.String(300)), sa.Column('summary', sa.Text), sa.Column('narrative', sa.Text), sa.Column('by', sa.String(10)),
                  sa.Column('evidence', sa.JSON), sa.Column('blocks', sa.JSON), sa.Column('draft', sa.JSON), sa.Column('entity', sa.String(40)),
                  sa.Column('entity_id', sa.Integer), sa.Column('data', sa.JSON), sa.Column('status', sa.String(10), index=True), sa.Column('count', sa.Integer),
                  sa.Column('first_seen', sa.DateTime), sa.Column('last_seen', sa.DateTime), sa.Column('acked_by', sa.String(80)), sa.Column('acked_at', sa.DateTime),
                  sa.Column('draft_id', sa.Integer))

# ---------------- 5.0 数据底座（docs/dev/foundation.md）：迁移版本、角色权限、审计、API 密钥、备份、时序、采集去重 ----------------
# 迁移版本记录（honeymom/migrations 维护）
schema_migration_t = sa.Table('schema_migration', metadata, sa.Column('rev', sa.String(40), primary_key=True), sa.Column('title', sa.String(200)),
                              sa.Column('applied_at', sa.DateTime), sa.Column('ms', sa.Integer), sa.Column('dialect', sa.String(20)), sa.Column('ops', sa.Integer))
# 角色 → 权限点（通配符模式列表）+ 默认数据范围（honeymom/perms.py）
role_t = _t('sys_role', sa.Column('code', sa.String(40), unique=True, index=True), sa.Column('name', sa.String(80)), sa.Column('perms', sa.JSON),
            sa.Column('scope', sa.JSON), sa.Column('builtin', sa.Boolean, default=False), sa.Column('remark', sa.String(200)),
            sa.Column('created_at', sa.DateTime), sa.Column('updated_at', sa.DateTime))
# 审计日志 4.7 已有前 5 列；5.0 加实体/动作/改前改后/来源/IP + 哈希链（seq/prev_hash/hash 由 audit.seal() 按顺序补上，之后只读）
audit_t.append_column(sa.Column('entity', sa.String(60), index=True))
audit_t.append_column(sa.Column('entity_id', sa.Integer))
audit_t.append_column(sa.Column('action', sa.String(60)))
audit_t.append_column(sa.Column('diff', sa.JSON))
audit_t.append_column(sa.Column('source', sa.String(20)))
audit_t.append_column(sa.Column('ip', sa.String(60)))
audit_t.append_column(sa.Column('seq', sa.Integer, unique=True, index=True))
audit_t.append_column(sa.Column('prev_hash', sa.String(64)))
audit_t.append_column(sa.Column('hash', sa.String(64)))
# 对外 API 密钥（只存哈希；honeymom/apikeys.py）
api_key_t = _t('api_key', sa.Column('name', sa.String(100)), sa.Column('prefix', sa.String(20), unique=True, index=True), sa.Column('key_hash', sa.String(64)),
               sa.Column('roles', sa.String(200)), sa.Column('perms', sa.JSON), sa.Column('scope', sa.JSON), sa.Column('ip_allow', sa.Text),
               sa.Column('expires_at', sa.DateTime), sa.Column('revoked_at', sa.DateTime), sa.Column('revoked_by', sa.String(80)),
               sa.Column('last_used_at', sa.DateTime), sa.Column('last_used_ip', sa.String(60)), sa.Column('use_count', sa.Integer, default=0),
               sa.Column('remark', sa.String(300)), sa.Column('created_by', sa.String(80)), sa.Column('created_at', sa.DateTime))
# 备份记录（honeymom/backup.py）
backup_t = _t('backup_log', sa.Column('file', sa.String(300)), sa.Column('kind', sa.String(20)), sa.Column('dialect', sa.String(20)),
              sa.Column('size', sa.BigInteger), sa.Column('status', sa.String(20)), sa.Column('manifest', sa.JSON), sa.Column('verify', sa.JSON),
              sa.Column('note', sa.String(300)), sa.Column('created_by', sa.String(80)), sa.Column('created_at', sa.DateTime, index=True),
              sa.Column('ms', sa.Integer))
# 采集去重：网关事件 / 补传重放时，同一条消息只入库一次（键 = 消息摘要；保留 3 天）
ingest_seen_t = sa.Table('ingest_seen', metadata, sa.Column('key', sa.String(64), primary_key=True), sa.Column('at', sa.DateTime, index=True))
# ---- 时序（honeymom/ts.py）----
# 采集点（时序编号）：key = 数据源:设备/点位；最后值随写入批量刷新
ts_point_t = _t('ts_point', sa.Column('key', sa.String(300), unique=True, index=True), sa.Column('equipment', sa.Integer, index=True),
                sa.Column('name', sa.String(200)), sa.Column('dtype', sa.String(10)), sa.Column('unit', sa.String(20)), sa.Column('discrete', sa.Boolean),
                sa.Column('last_ts', sa.DateTime), sa.Column('last_value', sa.Float), sa.Column('last_text', sa.String(200)), sa.Column('last_q', sa.SmallInteger),
                sa.Column('created_at', sa.DateTime))
# 原始值：(point, ts) 唯一（补传重放自动去重）；q 质量 0=好 1=不确定 2=坏；字符串值放 text
ts_raw_t = sa.Table('ts_raw', metadata, sa.Column('point', sa.Integer, primary_key=True, autoincrement=False), sa.Column('ts', sa.DateTime, primary_key=True),
                    sa.Column('value', sa.Float), sa.Column('text', sa.String(200)), sa.Column('q', sa.SmallInteger), sqlite_with_rowid=False)
# 离散量（开关量、状态字符串、步号）的状态区间：开/关时长、跳变次数、上升/下降沿都从这里算；end 为空 = 当前状态
ts_segment_t = _t('ts_segment', sa.Column('point', sa.Integer), sa.Column('start', sa.DateTime), sa.Column('end', sa.DateTime),
                  sa.Column('value', sa.Float), sa.Column('text', sa.String(200)), sa.Column('dur_s', sa.Float),
                  sa.Index('ix_ts_segment_point_start', 'point', 'start'))


def _rollup_t(name):
    # TimescaleDB 下同名的是连续聚合视图（列相同），这里的表定义只在 SQLite / 普通 PostgreSQL 下建（见 migrations/v0004_timeseries.py）
    return sa.Table(name, metadata, sa.Column('bucket', sa.DateTime, primary_key=True), sa.Column('point', sa.Integer, primary_key=True, autoincrement=False),
                    sa.Column('n', sa.Integer), sa.Column('vmin', sa.Float), sa.Column('vmax', sa.Float), sa.Column('vavg', sa.Float),
                    sa.Column('vfirst', sa.Float), sa.Column('vlast', sa.Float), sqlite_with_rowid=False)


ts_1m_t = _rollup_t('ts_1m')
ts_1h_t = _rollup_t('ts_1h')
# 汇总作业的脏区间（迟到/补传的数据落在已汇总的分钟里）
ts_dirty_t = sa.Table('ts_dirty', metadata, sa.Column('point', sa.Integer, primary_key=True, autoincrement=False),
                      sa.Column('bucket', sa.DateTime, primary_key=True))
# 这些表不进通用 CRUD；迁移和漂移检查按这里的定义（时序的汇总表在 TimescaleDB 下是视图，漂移检查跳过）
TS_VIEWS_ON_TIMESCALE = {'ts_1m', 'ts_1h'}

# ---- 5.0 M6 AI 新功能（迁移 v0500）：动作级节拍分析（services/cycle.py）、技能库（services/skills.py）；MCP 不建表（审计进 audit_log）----
# 工位动作模型：一台设备一行。cycle = 怎么切循环，actions = 动作清单（起止信号 + 标准用时），baseline = 学到的基线（中位数 / P90）
cycle_model_t = _t('cycle_model', sa.Column('equipment', sa.Integer, unique=True, index=True), sa.Column('name', sa.String(200)),
                   sa.Column('status', sa.String(20), index=True), sa.Column('source', sa.String(20)), sa.Column('cycle', sa.JSON), sa.Column('actions', sa.JSON),
                   sa.Column('std_cycle_s', sa.Float), sa.Column('takt_s', sa.Float), sa.Column('grp', sa.String(60)), sa.Column('value_per_part', sa.Float),
                   sa.Column('baseline', sa.JSON), sa.Column('baseline_from', sa.DateTime), sa.Column('baseline_to', sa.DateTime), sa.Column('version', sa.Integer),
                   sa.Column('proposed_by', sa.String(80)), sa.Column('confirmed_by', sa.String(80)), sa.Column('confirmed_at', sa.DateTime), sa.Column('note', sa.Text),
                   sa.Column('created_at', sa.DateTime), sa.Column('updated_at', sa.DateTime))
# 每天每个动作的统计（漂移趋势用；已过去的天算一次存下，当天每次重算）
cycle_daily_t = sa.Table('cycle_daily', metadata, sa.Column('equipment', sa.Integer, primary_key=True, autoincrement=False), sa.Column('day', sa.Date, primary_key=True),
                         sa.Column('action', sa.String(60), primary_key=True), sa.Column('n', sa.Integer), sa.Column('median', sa.Float), sa.Column('p90', sa.Float),
                         sa.Column('mean', sa.Float), sa.Column('vmin', sa.Float), sa.Column('vmax', sa.Float), sa.Column('computed_at', sa.DateTime))
# 技能：保存下来的问答（确定的查询计划 + 参数 + 输出块），可手动 / 定时 / 自动化规则运行
ai_skill_t = _t('ai_skill', sa.Column('key', sa.String(60), index=True), sa.Column('name', sa.String(200)), sa.Column('description', sa.Text),
                sa.Column('plant', sa.Integer, index=True), sa.Column('owner', sa.String(80), index=True), sa.Column('share_roles', sa.JSON),
                sa.Column('status', sa.String(20), index=True), sa.Column('version', sa.Integer), sa.Column('question', sa.Text), sa.Column('plan', sa.JSON),
                sa.Column('params', sa.JSON), sa.Column('output', sa.JSON), sa.Column('narrative', sa.Boolean), sa.Column('narrative_prompt', sa.Text),
                sa.Column('schedule', sa.JSON), sa.Column('targets', sa.JSON), sa.Column('mcp_expose', sa.Boolean), sa.Column('next_run_at', sa.DateTime),
                sa.Column('last_run_at', sa.DateTime), sa.Column('last_status', sa.String(20)), sa.Column('run_count', sa.Integer), sa.Column('tokens_total', sa.Integer),
                sa.Column('cost_total', sa.Float), sa.Column('broken_reason', sa.Text), sa.Column('source_message', sa.Integer),
                sa.Column('created_at', sa.DateTime), sa.Column('updated_at', sa.DateTime))
ai_skill_version_t = _t('ai_skill_version', sa.Column('skill', sa.Integer, index=True), sa.Column('version', sa.Integer), sa.Column('snapshot', sa.JSON),
                        sa.Column('note', sa.String(300)), sa.Column('by', sa.String(80)), sa.Column('created_at', sa.DateTime))
ai_skill_run_t = _t('ai_skill_run', sa.Column('skill', sa.Integer, index=True), sa.Column('version', sa.Integer), sa.Column('trigger', sa.String(20)),
                    sa.Column('params', sa.JSON), sa.Column('status', sa.String(20), index=True), sa.Column('blocks', sa.JSON), sa.Column('narrative', sa.Text),
                    sa.Column('by', sa.String(10)), sa.Column('error', sa.Text), sa.Column('steps', sa.JSON), sa.Column('llm_calls', sa.Integer),
                    sa.Column('tokens', sa.Integer), sa.Column('cost', sa.Float), sa.Column('ms', sa.Integer), sa.Column('run_by', sa.String(80)),
                    sa.Column('targets', sa.JSON), sa.Column('data_time', sa.DateTime), sa.Column('started_at', sa.DateTime, index=True),
                    sa.Column('ended_at', sa.DateTime))
# 工厂模型与接入（M2）的非实体表：模型版本、平面图、待确认队列、导入批次、验收报告、小时产量
from .schema_factory import define_tables as _factory_tables  # noqa: E402
_factory_tables(metadata)
# 5.0 实施工具与可配置（M3）：导入任务、自定义字段、表单配置、审批流程、报表、行业模板记录（schema_impl.py）
from .schema_impl import define_tables as _impl_tables  # noqa: E402
IMPL_TABLES = _impl_tables(metadata)
# 5.0 内置 ERP（M4）：Webhook、事件与投递、中间库、外部编码映射、对接器、同步日志（schema_erp.py）
from .schema_erp import tables as _erp_tables  # noqa: E402
ERP_TABLES = _erp_tables(metadata)
SPEC_HOOKS: list = []          # fn(spec_list)：给 /api/meta 的实体说明加东西（M3：自定义字段、表单配置、行业模板的枚举）


def spec_json():
    out = []
    for e in ENTITIES:
        out.append({'name': e.name, 'label': e.label, 'module': e.module, 'internal': e.internal, 'menu': e.menu, 'children': e.children,
                    'actions': e.actions,
                    'fields': [{k: v for k, v in f.__dict__.items() if v not in (None, '', False) or k in ('list',)} for f in e.fields]})
    for h in SPEC_HOOKS:
        h(out)
    return out
