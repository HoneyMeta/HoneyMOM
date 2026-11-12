"""5.0 数据底座：角色权限、用户数据范围、审计日志扩展（+ 只追加触发器）、API 密钥、备份记录、采集去重。"""
from datetime import datetime

import sqlalchemy as sa

TITLE = '数据底座：角色权限 / 审计哈希链 / API 密钥 / 备份 / 采集去重'

AUDIT_COLS = ('entity', 'entity_id', 'action', 'diff', 'source', 'ip', 'seq', 'prev_hash', 'hash')

PG_AUDIT_GUARD = """
CREATE OR REPLACE FUNCTION hm_audit_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'audit_log 只能追加，不能删除'; END IF;
  IF OLD.hash IS NOT NULL THEN RAISE EXCEPTION 'audit_log 已封存的记录不能修改'; END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS audit_log_guard ON audit_log;
CREATE TRIGGER audit_log_guard BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION hm_audit_guard();
"""
SQLITE_AUDIT_GUARD = [
    """CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log WHEN OLD.hash IS NOT NULL
       BEGIN SELECT RAISE(ABORT, 'audit_log 已封存的记录不能修改'); END""",
    """CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
       BEGIN SELECT RAISE(ABORT, 'audit_log 只能追加，不能删除'); END""",
]


def up(op):
    for t in ('sys_role', 'api_key', 'backup_log', 'ingest_seen'):
        op.create_table(t)
    for c in AUDIT_COLS:
        op.add_column('audit_log', c)
    op.add_column('sys_user', 'scope')
    # 审计只追加
    if op.pg:
        op.execute(PG_AUDIT_GUARD, desc='审计表只追加触发器（PostgreSQL）')
    else:
        for sql in SQLITE_AUDIT_GUARD:
            op.execute(sql, desc='审计表只追加触发器（SQLite）')
    op.run(_seed_roles, '写入默认角色（已存在的不动）')


def _seed_roles(conn):
    from ..perms import DEFAULT_ROLES
    from ..schema import role_t
    have = set(conn.execute(sa.select(role_t.c.code)).scalars())
    now = datetime.now().replace(microsecond=0)
    for code, (name, perms) in DEFAULT_ROLES.items():
        if code not in have:
            conn.execute(role_t.insert().values(code=code, name=name, perms=perms, scope=None, builtin=True, remark='系统内置（与 4.7 行为一致）',
                                                created_at=now, updated_at=now))
