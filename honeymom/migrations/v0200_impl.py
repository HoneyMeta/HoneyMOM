"""5.0 实施工具与可配置（M3）：计量单位 / 单位换算 / 客户 / 供应商 / 实施任务；导入任务、自定义字段（各实体表 ext 列）、表单配置、
审批流程、报表设计器、行业模板记录（自定义字段的 ext 列按需补，见 impl/cf.py）。

老库（已经有工厂 / 物料数据的 4.7 库）：记为已应用"铸造""机加"两个行业模板（版本 legacy），枚举选项和菜单与 4.7 完全一致；
系统参数 setup_state = done（不弹开局向导）。"""
from datetime import datetime

import sqlalchemy as sa

TITLE = '实施工具：导入 / 自定义字段 / 表单 / 审批流程 / 报表 / 行业模板'

NEW_ENTITIES = ('uom', 'uom_conv', 'customer', 'supplier', 'impl_task')
NEW_TABLES = ('import_job', 'custom_field', 'form_layout', 'approval_flow', 'approval_flow_inst', 'report_def', 'report_export', 'pack_applied')


def up(op):
    for t in NEW_ENTITIES + NEW_TABLES:
        op.create_table(t)
    # 自定义字段的值存在实体表的 ext 列：不在这里加，某个实体第一次定义自定义字段时补（impl/cf.py ensure_column），迁移顺序无关
    op.run(_legacy, '老库：记为已应用铸造 + 机加行业模板（legacy），不弹开局向导')
    op.run(_units, '常用计量单位（表为空时）')


def _legacy(conn):
    from ..schema import IMPL_TABLES, TABLES
    has_data = conn.execute(sa.select(TABLES['material'].c.id).limit(1)).first() or conn.execute(sa.select(TABLES['plant'].c.id).limit(1)).first()
    if not has_data:
        return
    pa = IMPL_TABLES['pack_applied']
    now = datetime.now().replace(microsecond=0)
    have = set(conn.execute(sa.select(pa.c.pack)).scalars())
    for code in ('foundry', 'machining'):
        if code not in have:
            conn.execute(pa.insert().values(pack=code, version='legacy', demo=True, summary={'note': '4.7 库升级：沿用原有铸造 / 机加数据'},
                                            applied_by='migration', applied_at=now))
    st = TABLES['setting']
    if conn.execute(sa.select(st.c.id).where(st.c.key == 'setup_state')).first() is None:
        conn.execute(st.insert().values(key='setup_state', value='done', remark='开局向导（行业模板 → 公司信息 → 导入主数据 → 设备接入）', created_at=now, updated_at=now))
    if conn.execute(sa.select(st.c.id).where(st.c.key == 'demo_seed')).first() is None:
        conn.execute(st.insert().values(key='demo_seed', value='1', remark='演示库：启动时补演示数据（4.7 库都是演示 seed 建的）', created_at=now, updated_at=now))


UNITS = [('件', '件', 'count', 1), ('个', '个', 'count', 1), ('套', '套', 'count', 1), ('台', '台', 'count', 1), ('箱', '箱', 'count', 1), ('托', '托盘', 'count', 1),
         ('kg', '千克', 'mass', 1), ('g', '克', 'mass', 0.001), ('t', '吨', 'mass', 1000), ('m', '米', 'length', 1), ('mm', '毫米', 'length', 0.001),
         ('m2', '平方米', 'area', 1), ('L', '升', 'volume', 1), ('mL', '毫升', 'volume', 0.001), ('m3', '立方米', 'volume', 1000), ('h', '小时', 'time', 1),
         ('min', '分钟', 'time', 1 / 60)]


def _units(conn):
    from ..schema import TABLES
    t = TABLES['uom']
    if conn.execute(sa.select(t.c.id).limit(1)).first() is not None:
        return
    now = datetime.now().replace(microsecond=0)
    for code, name, cat, ratio in UNITS:
        conn.execute(t.insert().values(code=code, name=name, category=cat, ratio=ratio, precision=0 if cat == 'count' else 3, active=True,
                                       created_at=now, updated_at=now))
