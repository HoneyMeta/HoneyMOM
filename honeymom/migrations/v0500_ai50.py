"""5.0 M6 AI 新功能：动作级节拍分析（工位动作模型、每日动作统计）、技能库（技能 / 版本 / 运行记录）；默认角色加 cycle.* / skill.* 权限点。"""
TITLE = 'AI：节拍分析 / 技能库 / MCP 权限'

# 默认角色新增的权限点（与 services/ai50.py 的 DEFAULT_GRANTS 一致；admin / manager 是 * 已经有了）
GRANTS = {
    'planner': ['cycle.view', 'skill.use', 'skill.edit'],
    'leader': ['cycle.view', 'skill.use', 'skill.edit'],
    'operator': ['cycle.view', 'skill.use'],
    'qc': ['cycle.view', 'skill.use', 'skill.edit'],
    'maint': ['cycle.view', 'cycle.model.edit', 'skill.use', 'skill.edit'],
    'warehouse': ['cycle.view', 'skill.use'],
    'engineer': ['cycle.*', 'skill.use', 'skill.edit'],
}


def up(op):
    for t in ('cycle_model', 'cycle_daily', 'ai_skill', 'ai_skill_version', 'ai_skill_run'):
        op.create_table(t)
    op.run(_grant, '默认角色：节拍分析查看 / 技能使用')


def _grant(conn):
    import sqlalchemy as sa
    from ..schema import role_t
    for code, add in GRANTS.items():
        r = conn.execute(sa.select(role_t.c.perms, role_t.c.builtin).where(role_t.c.code == code)).first()
        if r is None or not r[1]:
            continue
        cur = list(r[0] or [])
        new = cur + [p for p in add if p not in cur]
        if new != cur:
            conn.execute(role_t.update().where(role_t.c.code == code).values(perms=new))
