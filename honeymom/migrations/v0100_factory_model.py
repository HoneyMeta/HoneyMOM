"""5.0 工厂模型与接入（M2）：统一工厂模型（设备布局 / 模板、采集点含义与确认状态、数据源参数）、设备模板、模型版本、平面图、
待确认队列、导入批次、验收报告、小时产量；工单加产线（数据范围）。老库的采集点全部标为"已确认"（4.7 已在用）。"""
import sqlalchemy as sa

TITLE = '工厂模型与接入：模型字段 / 设备模板 / 版本 / 平面图 / 待确认队列 / 验收报告'

EQ_COLS = ('template', 'x', 'y', 'w', 'd', 'rot', 'model3d', 'remark')
DS_COLS = ('options', 'last_seen', 'discover_until')
TP_COLS = ('code', 'rw', 'protocol', 'offset', 'period_ms', 'alarm_lo', 'alarm_hi', 'normal_range', 'encoding', 'value_map', 'sem', 'status',
           'confidence', 'confirmed_by', 'confirmed_at', 'origin', 'ts_key', 'remark', 'stats')


def up(op):
    for t in ('device_template', 'factory_version', 'factory_layout', 'onboard_seen', 'onboard_import', 'onboard_report', 'equip_output'):
        op.create_table(t)
    for c in EQ_COLS:
        op.add_column('equipment', c)
    for c in DS_COLS:
        op.add_column('datasource', c)
    for c in TP_COLS:
        op.add_column('tag_point', c)
    op.add_column('work_order', 'line')
    op.create_index('ix_tag_point_ts_key', 'tag_point', ['ts_key'])
    op.run(_points, '老库采集点：补点位编码 / 协议 / 时序键，标为已确认')
    op.run(_equipment, '设备模板 = 设备类型')
    op.run(_grant, '实施工程师 / 设备员：工厂建模与接入权限')
    op.run(_templates, '内置设备类型模板')
    op.run(_wo_line, '工单产线（由工艺路线推出）')


def _points(conn):
    from ..schema import TABLES
    tp, eq, ds = TABLES['tag_point'], TABLES['equipment'], TABLES['datasource']
    eqs = {r['id']: r for r in conn.execute(sa.select(eq.c.id, eq.c.code)).mappings()}
    dss = {r['id']: r for r in conn.execute(sa.select(ds.c.id, ds.c.code, ds.c.protocol)).mappings()}
    for r in conn.execute(sa.select(tp.c.id, tp.c.equipment, tp.c.datasource, tp.c.name, tp.c.code, tp.c.status)).mappings().all():
        if r['code'] and r['status']:
            continue
        e, d = eqs.get(r['equipment']), dss.get(r['datasource'])
        code = f"{e['code']}.{(r['name'] or '').replace('/', '.')}" if e else (r['name'] or f"P{r['id']}")
        key = f"{d['code']}:{e['code']}/{r['name']}" if d and e else None
        conn.execute(tp.update().where(tp.c.id == r['id']).values(code=code, protocol=d['protocol'] if d else None, ts_key=key, status='confirmed',
                                                                  origin='import', rw='R', confirmed_by='migration'))


def _equipment(conn):
    from ..schema import TABLES
    eq = TABLES['equipment']
    conn.execute(eq.update().where(eq.c.template.is_(None)).values(template=eq.c.etype))


def _grant(conn):
    from ..schema import role_t
    from ..schema_factory import ROLE_GRANTS
    for code, add in ROLE_GRANTS.items():
        r = conn.execute(sa.select(role_t.c.perms).where(role_t.c.code == code)).scalar()
        if r is not None:
            new = list(r) + [p for p in add if p not in r]
            if new != list(r):
                conn.execute(role_t.update().where(role_t.c.code == code).values(perms=new))


def _templates(conn):
    from ..factory import templates
    templates.ensure(conn)


def _wo_line(conn):
    from ..factory import model
    model.derive_wo_lines(conn)
