"""5.0 现场执行与质量合规（M5）：条码标签、包装发运、委外、备件、文件受控与 ECN、IATF 16949（量检具、PFMEA / 控制计划、MSA、客诉召回、电子签名）。

新表按 schema_exec.py 建；已有表补列（检验项目的量具类型 / 特殊特性、检验值的量具、工艺步骤的委外、知识库的受控状态、8D 关闭人……）；
内置角色补上新模块的写权限；写入内置标签模板（零件 DataMatrix、箱、托盘、通用汽车行业客户标签、量具、库位、批次）。
"""
from datetime import datetime

import sqlalchemy as sa

TITLE = '现场执行与质量合规：标签 / 包装发运 / 委外 / 备件 / 文件受控 ECN / 量检具 / PFMEA 控制计划 / MSA / 客诉 / 电子签名'

ADD_COLS = {
    'insp_item': ['gauge_type', 'special', 'cp_item'],
    'insp_record': ['gauge'],
    'insp_value': ['gauge'],
    'route_step': ['outsourced', 'supplier', 'os_lead_days'],
    'kb_doc': ['ctrl_status', 'ctrl_doc'],
    'nc_program': ['ctrl_doc'],
    'report_8d': ['closed_by', 'closed_at', 'complaint'],
}


def up(op):
    from ..schema_exec import ENTITIES_EXEC
    # 先建被引用的表（顺序无所谓：SQLite / PostgreSQL 这里都不建外键约束），按定义顺序
    for e in ENTITIES_EXEC:
        op.create_table(e.name)
    for t, cols in ADD_COLS.items():
        for col in cols:
            op.add_column(t, col)
    op.create_index('ix_package_item_package_sn', 'package_item', ['package', 'sn'])
    op.create_index('ix_esign_entity', 'esign', ['entity', 'entity_id'])
    op.create_index('ix_print_job_status', 'print_job', ['status'])
    op.run(_grant, '内置角色补上新模块的写权限')
    op.run(_templates, '内置标签模板')


def _grant(conn):
    from ..schema import role_t
    from ..schema_exec import ROLE_GRANTS
    now = datetime.now().replace(microsecond=0)
    for code, add in ROLE_GRANTS.items():
        r = conn.execute(sa.select(role_t.c.id, role_t.c.perms, role_t.c.builtin).where(role_t.c.code == code)).first()
        if r is None or not r.builtin:
            continue
        cur = list(r.perms or [])
        new = cur + [p for p in add if p not in cur]
        if new != cur:
            conn.execute(role_t.update().where(role_t.c.id == r.id).values(perms=new, updated_at=now))


def _templates(conn):
    from ..label_builtin import BUILTIN_TEMPLATES
    from ..schema import TABLES
    t = TABLES['label_template']
    now = datetime.now().replace(microsecond=0)
    have = set(conn.execute(sa.select(t.c.code)).scalars())
    for tpl in BUILTIN_TEMPLATES:
        if tpl['code'] in have:
            continue
        conn.execute(t.insert().values(**tpl, status='active', created_at=now, updated_at=now))
