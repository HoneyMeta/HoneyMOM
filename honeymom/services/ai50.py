"""5.0 M6 AI 新功能接入（app.py 里 ai50.install(app) 一行）：
  - services/ai_tools.py    统一的只读数据工具层（MCP、右下角助手、智能体、技能共用）
  - services/mcp_server.py  MCP 数据接口（/mcp Streamable HTTP + python -m honeymom mcp 的 stdio）
  - services/cycle.py       动作级节拍分析（切循环 / 动作、基线、漂移、瓶颈、损失折算）+ 值守观察者 equipment.cycle_drift
  - services/skills.py      保存为技能（查询计划 + 参数 + 定时运行），内置 6 个技能，进值守信息流 / 通知 / 日报
权限点：cycle.view / cycle.model.edit / cycle.model.confirm、skill.use / skill.edit / skill.manage、mcp.connect / mcp.write（默认角色的授予见迁移 v0500）。
AI 功能开关：mcp、cycle、skills（services/ai_switch.py）。
"""
import asyncio
import logging

from .. import perms
from . import ai_drafts, cycle, mcp_server, skills

log = logging.getLogger('mes.ai50')

# 默认角色新增的权限点（"恢复默认"时一致；和迁移 v0500 的 GRANTS 相同）
DEFAULT_GRANTS = {
    'planner': ['cycle.view', 'skill.use', 'skill.edit'],
    'leader': ['cycle.view', 'skill.use', 'skill.edit'],
    'operator': ['cycle.view', 'skill.use'],
    'qc': ['cycle.view', 'skill.use', 'skill.edit'],
    'maint': ['cycle.view', 'cycle.model.edit', 'skill.use', 'skill.edit'],
    'warehouse': ['cycle.view', 'skill.use'],
    'engineer': ['cycle.*', 'skill.use', 'skill.edit'],
}
for _code, _add in DEFAULT_GRANTS.items():
    if _code in perms.DEFAULT_ROLES:
        _name, _ps = perms.DEFAULT_ROLES[_code]
        perms.DEFAULT_ROLES[_code] = (_name, list(_ps) + [p for p in _add if p not in _ps])

ai_drafts.SOURCES.setdefault('cycle', '节拍分析')
ai_drafts.SOURCES.setdefault('mcp', 'MCP 外部 AI')
ai_drafts.SOURCES.setdefault('skill', 'AI 技能')


def install(app):
    for r in (cycle.router, skills.router, mcp_server.router):
        app.include_router(r)
    cycle.install_watcher()
    skills.install()

    @app.on_event('startup')
    async def _ai50_start():
        try:
            await asyncio.to_thread(skills.ensure_builtins)
        except Exception:
            log.exception('builtin skills')
        asyncio.create_task(skills.loop())
