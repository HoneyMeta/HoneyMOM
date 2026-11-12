"""5.0 实施工具与可配置（M3）的表结构。schema.py 在两处调用这里（和 M5 的 schema_exec.py 同一个做法）：

- extend(ENTITIES)：新实体（计量单位、单位换算、客户、供应商、实施任务），走通用增删改查；
- define_tables(metadata)：不走通用增删改查的表（导入任务、自定义字段、表单配置、审批流程、报表、行业模板记录）。

另外每个实体表都有一列 ext（JSON）：自定义字段的值（schema.py 建表循环里加，见 docs/dev/config.md「自定义字段」）。
"""
from .i18n import _tr
import sqlalchemy as sa

MODULE = ('实施', 'impl')          # perms.MODULE_KEYS 里的模块：实施任务的权限点 impl.impl_task.*

UOM_CAT = {'count': '计数', 'mass': '重量', 'length': '长度', 'area': '面积', 'volume': '体积', 'time': '时间', 'other': '其他'}
PARTNER_T = {'raw': '原材料', 'aux': '辅料', 'outsource': '委外加工', 'equipment': '设备备件', 'service': '服务', 'other': '其他'}
TASK_PHASE = {'survey': '调研', 'blueprint': '业务蓝图', 'master': '主数据初始化', 'config': '系统配置', 'onboard': '设备接入', 'train': '培训',
              'trial': '试运行', 'golive': '上线', 'accept': '验收'}
TASK_STATUS = {'todo': '未开始', 'doing': '进行中', 'done': '已完成', 'blocked': '受阻', 'na': '不适用'}


def extend(ENTITIES):
    from .schema import CODE, NAME, B, D, E, EN, I, N, R, S, T
    ENTITIES += [
        # 计量单位：同一类别（重量、长度…）内按 ratio 换算到类别的基准单位（ratio=1 的那个）；不同类别之间按物料的单位换算（uom_conv）
        E('uom', _tr('计量单位'), _tr('主数据'), [CODE(), NAME(), EN('category', _tr('类别'), UOM_CAT, default='count'),
                                     N('ratio', _tr('换算到基准'), default=1, hint=_tr('同类别里基准单位为 1，如 kg=1、t=1000、g=0.001')),
                                     I('precision', _tr('小数位'), default=2), B('active', _tr('启用'), default=True)], order='code'),
        E('uom_conv', _tr('单位换算'), _tr('主数据'), [R('material', _tr('物料'), 'material', hint=_tr('空 = 所有物料通用（如 1 t = 1000 kg）')), S('from_unit', _tr('从单位'), req=True, hint=_tr('如 箱')),
                                          S('to_unit', _tr('到单位'), req=True, hint=_tr('一般是物料的基本单位，如 件')), N('factor', _tr('换算系数'), req=True, hint=_tr('1 从单位 = 系数 × 到单位')),
                                          S('remark', _tr('说明'), list=False)], order='material'),
        # 轻量往来单位（内置 ERP 的 M4 在此基础上加字段 / 业务）
        E('customer', _tr('客户'), _tr('主数据'), [CODE(_tr('客户编码')), NAME(_tr('客户名称')), S('short_name', _tr('简称')), S('contact', _tr('联系人')), S('phone', _tr('电话')),
                                      S('address', _tr('地址'), list=False), S('tax_no', _tr('税号'), list=False), S('remark', _tr('备注'), list=False), B('active', _tr('启用'), default=True)],
          order='code'),
        E('supplier', _tr('供应商'), _tr('主数据'), [CODE(_tr('供应商编码')), NAME(_tr('供应商名称')), S('short_name', _tr('简称')), EN('category', _tr('类别'), PARTNER_T, default='raw'),
                                        S('contact', _tr('联系人')), S('phone', _tr('电话')), S('address', _tr('地址'), list=False), S('tax_no', _tr('税号'), list=False),
                                        S('remark', _tr('备注'), list=False), B('active', _tr('启用'), default=True)], order='code'),
        # 实施向导：每一步（调研 → 蓝图 → 主数据 → 配置 → 接入 → 培训 → 试运行 → 上线 → 验收）的任务、负责人、到期日
        E('impl_task', _tr('实施任务'), _tr('实施'), [S('project', _tr('项目'), default=_tr('默认项目'), search=True), EN('phase', _tr('阶段'), TASK_PHASE, req=True), I('seq', _tr('顺序'), default=0),
                                        S('title', _tr('任务'), req=True, search=True), S('owner', _tr('负责人')), D('due', _tr('到期日')),
                                        EN('status', _tr('状态'), TASK_STATUS, default='todo'), S('deliverable', _tr('交付物'), hint=_tr('如 docs/实施模板/上线检查清单.xlsx')),
                                        S('link', _tr('系统入口'), list=False, hint=_tr('如 /impl/import')), D('done_on', _tr('完成日期'), list=False), T('note', _tr('说明'))],
          order='seq', menu=False),
    ]


T: dict[str, sa.Table] = {}


def define_tables(metadata):
    def t(name, *cols):
        T[name] = sa.Table(name, metadata, sa.Column('id', sa.Integer, primary_key=True, autoincrement=True), *cols)
        return T[name]

    # 导入任务：上传 → 对列 → 校验预览 → 确认（一个事务）→ 错误报告；rows 是解析出的原始行（列名 → 文本）
    t('import_job', sa.Column('importer', sa.String(60), index=True), sa.Column('filename', sa.String(300)), sa.Column('sheet', sa.String(100)),
      sa.Column('status', sa.String(20), index=True), sa.Column('mode', sa.String(20)), sa.Column('headers', sa.JSON), sa.Column('mapping', sa.JSON),
      sa.Column('rows', sa.JSON), sa.Column('n_rows', sa.Integer), sa.Column('summary', sa.JSON), sa.Column('issues', sa.JSON), sa.Column('error', sa.Text),
      sa.Column('created_by', sa.String(80)), sa.Column('created_at', sa.DateTime, index=True), sa.Column('committed_at', sa.DateTime), sa.Column('ms', sa.Integer),
      sa.Column('batch', sa.String(40), index=True))
    # 自定义字段定义（值存在各实体表的 ext 列里）
    t('custom_field', sa.Column('entity', sa.String(60), index=True), sa.Column('key', sa.String(60)), sa.Column('label', sa.String(100)),
      sa.Column('ftype', sa.String(20)), sa.Column('options', sa.JSON), sa.Column('ref', sa.String(60)), sa.Column('required', sa.Boolean),
      sa.Column('in_list', sa.Boolean), sa.Column('filterable', sa.Boolean), sa.Column('seq', sa.Integer), sa.Column('help', sa.String(300)),
      sa.Column('active', sa.Boolean), sa.Column('created_by', sa.String(80)), sa.Column('created_at', sa.DateTime), sa.Column('updated_at', sa.DateTime),
      sa.Index('ux_custom_field_entity_key', 'entity', 'key', unique=True))
    # 表单配置：字段顺序、分组、按角色必填 / 隐藏
    t('form_layout', sa.Column('entity', sa.String(60), unique=True, index=True), sa.Column('config', sa.JSON), sa.Column('updated_by', sa.String(80)),
      sa.Column('updated_at', sa.DateTime))
    # 审批流程：挂在 实体 + 动作（create / update / delete / 实体动作 / 服务登记的动作）上，条件满足时先审批再执行
    t('approval_flow', sa.Column('code', sa.String(60), unique=True, index=True), sa.Column('name', sa.String(200)), sa.Column('entity', sa.String(60), index=True),
      sa.Column('action', sa.String(60)), sa.Column('enabled', sa.Boolean), sa.Column('condition', sa.JSON), sa.Column('steps', sa.JSON),
      sa.Column('remark', sa.String(400)), sa.Column('created_by', sa.String(80)), sa.Column('created_at', sa.DateTime), sa.Column('updated_at', sa.DateTime))
    t('approval_flow_inst', sa.Column('flow', sa.Integer, index=True), sa.Column('flow_name', sa.String(200)), sa.Column('entity', sa.String(60), index=True),
      sa.Column('action', sa.String(60)), sa.Column('record_id', sa.Integer), sa.Column('title', sa.String(300)), sa.Column('body', sa.JSON),
      sa.Column('requester', sa.String(80), index=True), sa.Column('status', sa.String(20), index=True), sa.Column('steps', sa.JSON), sa.Column('cur_step', sa.Integer),
      sa.Column('approval', sa.Integer), sa.Column('result', sa.JSON), sa.Column('error', sa.Text), sa.Column('created_at', sa.DateTime, index=True),
      sa.Column('finished_at', sa.DateTime))
    # 报表设计器：数据集 + 字段 / 筛选 / 分组 / 汇总 / 图表；按角色共享；定时导出到站内通知；钉到工作台
    t('report_def', sa.Column('name', sa.String(200)), sa.Column('dataset', sa.String(60), index=True), sa.Column('config', sa.JSON), sa.Column('chart', sa.String(20)),
      sa.Column('owner', sa.String(80), index=True), sa.Column('shared_roles', sa.JSON), sa.Column('schedule', sa.JSON), sa.Column('last_run_at', sa.DateTime),
      sa.Column('source', sa.String(60)), sa.Column('remark', sa.String(400)), sa.Column('created_at', sa.DateTime), sa.Column('updated_at', sa.DateTime))
    t('report_export', sa.Column('report', sa.Integer, index=True), sa.Column('file', sa.String(300)), sa.Column('fmt', sa.String(10)), sa.Column('rows', sa.Integer),
      sa.Column('kind', sa.String(20)), sa.Column('created_by', sa.String(80)), sa.Column('created_at', sa.DateTime, index=True))
    # 行业模板应用记录（启用了哪些模板 → 枚举选项、可选模块、KPI、报表集）
    t('pack_applied', sa.Column('pack', sa.String(60), index=True), sa.Column('version', sa.String(20)), sa.Column('demo', sa.Boolean),
      sa.Column('summary', sa.JSON), sa.Column('applied_by', sa.String(80)), sa.Column('applied_at', sa.DateTime))
    return T
