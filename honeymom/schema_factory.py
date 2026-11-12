"""5.0 工厂模型与接入（M2）：统一工厂模型 = 设备台账 + 采集点表 + 2D/3D 孪生 + AI 数据目录。

工厂 → 车间 → 产线 → 工位 → 设备 → 采集点。层级本身仍是 4.7 的实体（plant / workshop / line / workstation / equipment / tag_point / datasource），
这里给它们补字段（设备的布局与模板、采集点的含义 / 用途 / 地址 / 缩放 / 报警限 / 确认状态…），并新增：
  device_template   设备类型模板（点位集合、用途映射、默认 3D 模型）——M3 的行业包引用它
  factory_version   工厂模型版本（发布 = 整个模型的快照；版本间可比对）
  factory_layout    车间平面图（DXF 解析结果：背景图元、区域、设备块）
  onboard_seen      采集到但点表里没有的地址（待确认队列 / 发现结果 / OPC UA 浏览结果 / Sparkplug 出生证书）
  onboard_import    点表 / 交付包导入批次（导入 → 校验 → 预览 → 确认）
  onboard_report    联调验收报告
  equip_output      设备计数点折算的小时产量（用途 count → 产量）
schema.py 在建表之前调 extend(ENTITIES)，在定义完 metadata 之后调 define_tables(metadata)。迁移：migrations/v0100_factory_model.py。
接口说明：docs/dev/factory-model.md。
"""
from .i18n import _tr
import sqlalchemy as sa

from .schema import B, CODE, DT, E, EN, I, J, N, NAME, R, S, T  # noqa: F401  （schema 正在初始化：只取它已定义的辅助函数）

# 模块（中文名 → 权限键）：perms.MODULE_KEYS 合并
MODULES = {'工厂模型': 'factory'}
# 默认角色的写权限（perms.DEFAULT_ROLES 合并；迁移 v0100 给已有库的内置角色补上）
ROLE_GRANTS = {
    'engineer': ['factory.*', 'onboard.*', 'system.datasource.*', 'system.tag_point.*'],
    'maint': ['factory.*', 'onboard.*', 'system.datasource.*', 'system.tag_point.*'],
}

# 点位用途（和交付包 / 接入规范同一套）
ROLES = {'state': '运行状态', 'alarm': '报警', 'count': '计数', 'power': '功率', 'energy': '电能', 'load': '负载', 'temp': '温度', 'quality': '质量值',
         'action': '动作信号', 'step': '程序段号', 'param': '设定参数', 'other': '其他'}
POINT_STATUS = {'confirmed': '已确认', 'pending': '待确认', 'ignored': '已忽略'}
POINT_ORIGIN = {'package': '交付包', 'import': '点表导入', 'discovered': '自动发现', 'template': '设备模板', 'ai': 'AI 草稿', 'manual': '手工'}
PROTOCOLS = {'mqtt': 'MQTT (UNS)', 'opcua': 'OPC UA', 'modbus': 'Modbus TCP', 'sparkplug': 'MQTT Sparkplug B', 'http': 'HTTP 推送', 'sql': '中间库（SQL）'}
# 设备类型（= 设备模板编码）；内置模板见 factory/templates.py
EQUIP_TYPES = {'die_casting': '压铸机', 'injection': '注塑机', 'robot_cell': '机器人单元', 'conveyor': '输送线', 'compressor': '空压机',
               'buffer': '缓存 / 线边库', 'sink': '出口', 'cell': '控制单元', 'plc': 'PLC', 'gateway': '工业网关', 'switch': '交换机', 'mes': '信息系统'}

ENTITIES_FACTORY = [
    E('device_template', '设备类型模板', '工厂模型', [
        CODE('模板编码'), NAME('设备类型'), S('category', '大类', hint='如 机加、铸造、物流、公用'),
        S('aliases', '别名', hint='点表"设备类型"列的写法，逗号分隔，如 加工中心,CNC,立加'), S('process_type', '默认工序类型', list=False),
        S('station_group', '工位组', list=False, hint='同类设备共用的工位组（如 CNC），空 = 用设备编码'),
        S('model3d', '默认 3D 模型', hint='参数化模型类型（cnc / furnace / conveyor…）或 GLB 文件'), N('w', '默认宽 m', list=False), N('d', '默认深 m', list=False),
        N('h', '默认高 m', list=False), N('ideal_cycle_s', '理想节拍 s', list=False), J('points', '点位集合', hint='见 docs/dev/factory-model.md「模板格式」'),
        T('maint_items', '点检内容'), B('builtin', '内置', ro=True), I('version', '版本', default=1), T('remark', '说明')],
      actions={'apply': '批量套用'}, order='code', menu=False),
]


def extend(entities):
    by = {e.name: e for e in entities}
    entities.extend(ENTITIES_FACTORY)

    def add(ent, *fields):
        have = {f.name for f in by[ent].fields}
        by[ent].fields.extend(f for f in fields if f.name not in have)

    # ---- 设备：布局（工厂坐标，米，Y 向上，原点厂房左下角）+ 模板 + 3D ----
    eq = by['equipment']
    for f in eq.fields:
        if f.name == 'etype':
            f.enum.update({k: v for k, v in EQUIP_TYPES.items() if k not in f.enum})
    add('equipment', S('template', _tr('设备模板'), list=False, hint=_tr('设备类型模板编码，空 = 按类型')),
        N('x', 'X m', list=False, hint=_tr('设备中心，工厂坐标')), N('y', 'Y m', list=False),
        N('w', _tr('宽 m'), list=False), N('d', _tr('深 m'), list=False), N('rot', _tr('朝向 °'), list=False, hint=_tr('逆时针为正')),
        S('model3d', _tr('3D 模型'), list=False, hint=_tr('交付包里的 GLB（models/xxx.glb）；空 = 按模板参数化生成')), S('remark', _tr('备注'), list=False))
    # ---- 数据源 ----
    ds = by['datasource']
    for f in ds.fields:
        if f.name == 'protocol':
            f.enum.update({k: v for k, v in PROTOCOLS.items() if k not in f.enum})
        if f.name == 'endpoint':
            f.hint = _tr('mqtt://host:1883 / opc.tcp://host:4840/… / host:502 / 中间库：SQLAlchemy URL；HTTP 推送不用填')
    ds.actions = {'restart': _tr('重连'), 'discover': _tr('发现点位')}
    add('datasource', J('options', _tr('参数'), hint=_tr('中间库：{query, watermark, layout, interval_s}；OPC UA：{mode: subscribe|poll}；MQTT：{payload: json|plain}')),
        DT('last_seen', _tr('最近收到'), ro=True), DT('discover_until', _tr('发现截止'), ro=True, list=False))
    # ---- 采集点 ----
    tp = by['tag_point']
    for f in tp.fields:
        if f.name == 'role':
            f.enum.update({k: v for k, v in ROLES.items() if k not in f.enum})
            f.enum['count'] = _tr('计数')
            f.enum['load'] = _tr('负载')
        if f.name == 'desc':
            f.label = _tr('含义')
        if f.name == 'name':
            f.label = _tr('点位路径')
            f.hint = _tr('设备下的点位路径，如 Monitoring/Spindle/Load')
        if f.name == 'dtype':
            f.hint = 'bool / int / float / string'
    tp.menu = True
    add('tag_point', S('code', _tr('点位编码'), search=True), EN('rw', _tr('读写'), {'R': _tr('只读'), 'RW': _tr('读写')}, default='R'), S('protocol', _tr('协议'), list=False),
        N('offset', _tr('偏移'), list=False), I('period_ms', _tr('采集周期 ms'), list=False), N('alarm_lo', _tr('报警下限'), list=False), N('alarm_hi', _tr('报警上限'), list=False),
        S('normal_range', _tr('正常范围'), list=False, hint=_tr('如 0~85')), S('encoding', _tr('寄存器编码'), list=False, hint=_tr('Modbus：int16 / uint16 / int32 / float32 + 字序 ABCD/CDAB…')),
        J('value_map', _tr('值映射'), hint=_tr('如状态码 {"0": "Idle", "1": "Executing"}')), S('sem', _tr('语义钩子'), list=False, hint='output / tool_life / tool_limit / tool_active / sample …'),
        EN('status', _tr('确认'), POINT_STATUS, default='pending'), N('confidence', _tr('置信度'), list=False), S('confirmed_by', _tr('确认人'), ro=True, list=False),
        DT('confirmed_at', _tr('确认时间'), ro=True, list=False), EN('origin', _tr('来源'), POINT_ORIGIN, default='manual', list=False),
        S('ts_key', _tr('时序键'), ro=True, list=False), S('remark', _tr('备注'), list=False), J('stats', _tr('采集统计'), ro=True))
    # ---- 工单：产线（由工艺路线 / 工序推出，数据范围用）----
    add('work_order', R('line', _tr('产线'), 'line', list=False, hint=_tr('由工艺路线推出（最后一道工序所在产线）；数据范围按它过滤')))


# ---------------- 非实体表 ----------------
factory_version_t = factory_layout_t = onboard_seen_t = onboard_import_t = onboard_report_t = equip_output_t = None


def define_tables(metadata):
    global factory_version_t, factory_layout_t, onboard_seen_t, onboard_import_t, onboard_report_t, equip_output_t

    def _t(name, *cols):
        return sa.Table(name, metadata, sa.Column('id', sa.Integer, primary_key=True, autoincrement=True), *cols)

    factory_version_t = _t('factory_version', sa.Column('version', sa.Integer, unique=True, index=True), sa.Column('note', sa.String(400)),
                           sa.Column('snapshot', sa.JSON), sa.Column('counts', sa.JSON), sa.Column('published_by', sa.String(80)),
                           sa.Column('published_at', sa.DateTime))
    factory_layout_t = _t('factory_layout', sa.Column('plant', sa.Integer, index=True), sa.Column('name', sa.String(200)), sa.Column('file_name', sa.String(300)),
                          sa.Column('unit', sa.String(10)), sa.Column('bounds', sa.JSON), sa.Column('floor', sa.JSON), sa.Column('prims', sa.JSON),
                          sa.Column('areas', sa.JSON), sa.Column('blocks', sa.JSON), sa.Column('active', sa.Boolean), sa.Column('created_by', sa.String(80)),
                          sa.Column('created_at', sa.DateTime))
    onboard_seen_t = _t('onboard_seen', sa.Column('datasource', sa.Integer, index=True), sa.Column('address', sa.String(400)),
                        sa.Column('sample', sa.String(400)), sa.Column('dtype', sa.String(10)), sa.Column('count', sa.Integer),
                        sa.Column('first_seen', sa.DateTime), sa.Column('last_seen', sa.DateTime), sa.Column('stats', sa.JSON),
                        sa.Column('attrs', sa.JSON), sa.Column('status', sa.String(12), index=True), sa.Column('point', sa.Integer),
                        sa.Column('kind', sa.String(12)), sa.Index('ux_onboard_seen_ds_addr', 'datasource', 'address', unique=True))
    onboard_import_t = _t('onboard_import', sa.Column('kind', sa.String(20)), sa.Column('file_name', sa.String(300)), sa.Column('status', sa.String(20), index=True),
                          sa.Column('mapping', sa.JSON), sa.Column('summary', sa.JSON), sa.Column('issues', sa.JSON), sa.Column('rows', sa.JSON),
                          sa.Column('options', sa.JSON), sa.Column('created_by', sa.String(80)), sa.Column('created_at', sa.DateTime),
                          sa.Column('decided_by', sa.String(80)), sa.Column('decided_at', sa.DateTime))
    onboard_report_t = _t('onboard_report', sa.Column('title', sa.String(200)), sa.Column('coverage', sa.Float), sa.Column('summary', sa.JSON),
                          sa.Column('file', sa.String(300)), sa.Column('created_by', sa.String(80)), sa.Column('created_at', sa.DateTime, index=True))
    equip_output_t = sa.Table('equip_output', metadata, sa.Column('equipment', sa.Integer, primary_key=True, autoincrement=False),
                              sa.Column('hour', sa.DateTime, primary_key=True), sa.Column('qty', sa.Float))
    return [factory_version_t, factory_layout_t, onboard_seen_t, onboard_import_t, onboard_report_t, equip_output_t]
