"""应用入口：登录、元数据、各业务接口、前端静态文件、采集启动。"""
from .i18n import _tr
import logging
import os

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import audit, auth, context, crud, db, perms
from .auth import current_user
from .collector import COLLECTOR
from .db import clock
from .schema import ROLES, spec_json
from .agent import api as agent_api
from .services import ai, ai_demo, ai_switch, assistant, cross, equipment, foundry, machining, ops, production, quality, shell, views, warehouse
from .services import home, home_feed      # 工作台驾驶舱 / 车间大屏数据 + 提醒与 AI 值守信息流（provider 接口见 home_feed.py）

WEB = os.path.join(db.ROOT, 'web', 'dist')
log = logging.getLogger('mes')


async def _backup_loop():
    """定时备份（系统参数 backup_time / backup_enabled），每 30 秒看一次。"""
    import asyncio
    from . import backup
    await asyncio.sleep(30)
    while True:
        try:
            if await asyncio.to_thread(backup.due_now):
                await asyncio.to_thread(backup.create, 'scheduled', _tr('每日定时备份'), 'system:schedule')
        except Exception:
            log.exception(_tr('定时备份失败'))
        await asyncio.sleep(30)


def build_app():
    app = FastAPI(title=_tr('蜂元智能 MOM'), version='1.0.0')
    routers = [crud.router, production.router, quality.router, equipment.router, warehouse.router, foundry.router, machining.router, views.router, ops.router,
               cross.router, ai.router, ai_demo.router, agent_api.router, assistant.router, shell.router]
    for r in routers:
        app.include_router(r)
    from .services import admin
    app.include_router(admin.router)       # 5.0 数据底座：角色权限 / 审计 / API 密钥 / 备份 / 数据库状态 / 时序接口（services/admin.py）
    from . import i18n
    i18n.install(app)                      # request language: X-Lang / ?lang= / Accept-Language (honeymom/i18n.py)
    context.install(app)                   # 请求上下文：谁、来源、IP（审计和 AI 工具用）
    app.include_router(home.router)
    app.include_router(home_feed.router)
    # 4.7 业务深化：质量/报工闸门、带约束排产、报工记录与工位终端与工人日报、追溯对账、知识库图纸（gates 要在 warehouse 之后导入，它包住出入库钩子）
    from .services import drawings, gates, scheduling, shopfloor, trace_recon
    for r in (gates.router, scheduling.router, shopfloor.router, trace_recon.router, drawings.router):
        app.include_router(r)
    from .services import exec as exec_m5
    exec_m5.install(app)     # 5.0 现场执行与质量合规：标签 / 包装发运 / 委外 / 备件 / 文件受控 ECN / 量检具 / PFMEA 控制计划 / MSA / 客诉 / 电子签名
    ai_switch.install(app)   # AI 功能开关：/api/ai/switches + 关闭的功能统一返回"已关闭"（见 services/ai_switch.py）
    from .services import ai_plus
    ai_plus.install(app)
    from .factory import api as factory_api, assist as factory_assist
    app.include_router(factory_api.router)     # 5.0 M2 工厂模型与接入中心（/api/factory/…、/api/onboard/…，docs/dev/factory-model.md）
    factory_assist.register_ops()              # 接入 AI 草稿的两个操作：补全采集点、按 PLC 程序新建采集点     # 4.7 AI 体验：块协议/钉到工作台、动作草稿、一句话自动化、AI 值守、日报、跨工段 AI 解读（见 services/ai_plus.py）
    from .services import ai50
    ai50.install(app)        # 5.0 M6：MCP 数据接口（/mcp）、动作级节拍分析、保存为技能（见 services/ai50.py）
    from . import impl
    impl.install(app)        # 5.0 实施工具与可配置（M3）：导入、行业模板、自定义字段、表单、审批流程、报表设计器（honeymom/impl）
    from . import erp
    erp.install(app)         # 5.0 内置 ERP（M4）：销售 / 采购 / MRP / 成本 / 应收应付 / 凭证 / 外部 ERP 集成（honeymom/erp，docs/dev/erp.md）

    @app.on_event('startup')
    async def _start():
        import asyncio
        from .agent import engine as agent_engine
        from .agent.runtime import RT
        from .schema import agent_run_t
        with db.engine.begin() as c:     # 上次进程退出时还在跑的运行：标为中断（等待审批的保留，批完能接着跑）
            c.execute(agent_run_t.update().where(agent_run_t.c.status.in_(('queued', 'running'))).values(
                status='failed', error=_tr('服务重启，运行被中断'), ended_at=agent_engine.now()))
        await COLLECTOR.start()
        asyncio.create_task(RT.loop())
        asyncio.create_task(_backup_loop())
        from .services import demo_autopilot
        asyncio.create_task(demo_autopilot.loop())   # 只对演示库：按虚拟工厂产量自动下达工单

    @app.post('/api/login')
    def login(body: dict = Body(...)):
        name = (body.get('username') or '').strip()
        with db.engine.connect() as c:
            u = db.get(c, 'sys_user', username=name)
        if not u or not u['active'] or not auth.check_pw(body.get('password') or '', u['password_hash']):
            with db.engine.begin() as c:
                audit.audit(c, 'login_fail', 'sys_user', u['id'] if u else None, target=f'login:{name[:60]}', user=name[:80] or '?')
            raise HTTPException(401, _tr('账号或密码不对'))
        with db.engine.begin() as c:
            audit.audit(c, 'login', 'sys_user', u['id'], target=f'login:{name}', user=name)
        return {'token': auth.make_token(u), 'user': {'username': u['username'], 'name': u['name'], 'roles': u['roles']}}

    @app.get('/api/setup/admin')
    def setup_admin_state():
        """First start of a production install: is the administrator account still to be created? (no login needed)"""
        from . import i18n, seed
        with db.engine.connect() as c:
            return {'needed': seed.needs_admin(c), 'demo': i18n.demo_mode(), 'lang': i18n.lang()}

    @app.post('/api/setup/admin')
    def setup_admin(body: dict = Body(...)):
        """Create the first administrator (only while none exists), then log in. Afterwards the set-up wizard runs."""
        from . import seed
        try:
            with db.engine.begin() as c:
                seed.create_admin(c, body.get('username'), body.get('password'), body.get('name') or '')
                u = db.get(c, 'sys_user', username=(body.get('username') or '').strip())
                audit.audit(c, 'create_admin', 'sys_user', u['id'], target='setup:admin', user=u['username'])
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {'token': auth.make_token(u), 'user': {'username': u['username'], 'name': u['name'], 'roles': u['roles']}}

    @app.get('/api/me')
    def me(user=Depends(current_user)):
        with db.engine.connect() as c:
            company = db.setting(c, 'company', '')
        sc = perms.user_scope(user)
        return {**{k: db.jsonable(v) for k, v in user.items() if not k.startswith('_')}, 'company': company, 'now': db.jsonable(clock.now()),
                'writable': sorted({e['module'] for e in spec_json() if auth.can_write(user, e['name'])}),
                'perms': perms.effective_perms(user), 'scope': sc, 'scope_label': perms.scope_label(sc)}

    @app.post('/api/me/password')
    def change_pw(body: dict = Body(...), user=Depends(current_user)):
        with db.engine.begin() as c:
            u = db.get(c, 'sys_user', username=user['username'])
            if not auth.check_pw(body.get('old') or '', u['password_hash']):
                raise HTTPException(400, _tr('原密码不对'))
            if len(body.get('new') or '') < 6:
                raise HTTPException(400, _tr('新密码至少 6 位'))
            db.update(c, 'sys_user', u['id'], {'password_hash': auth.hash_pw(body['new'])}, raw=True)
        return {'ok': True}

    @crud.action('sys_user', 'reset_pw')
    def reset_pw(c, u, body, user):
        from . import i18n
        pw = body.get('password') or ('honey123' if i18n.demo_mode() else '')
        if len(pw) < 8 and not i18n.demo_mode():
            raise HTTPException(400, _tr('新密码至少 8 位'))
        db.update(c, 'sys_user', u['id'], {'password_hash': auth.hash_pw(pw)}, raw=True)
        return {'msg': _tr('密码已重置为 {pw}', pw=pw) if i18n.demo_mode() else _tr('密码已重置')}

    @app.get('/api/meta')
    def meta(user=Depends(current_user)):
        ents = spec_json()
        for e in ents:
            e['mkey'] = perms.MODULE_KEYS.get(e['module'])
        from . import i18n
        return {'entities': i18n.tr_meta(ents), 'roles': i18n.tr_obj({**ROLES, **{k: v['name'] for k, v in perms.roles().items()}}),
                'lang': i18n.lang(), 'langs': list(i18n.LANGS), 'demo': i18n.demo_mode()}

    @app.get('/api/clock')
    def clk():
        return {'now': db.jsonable(clock.now()), 'data': clock.data_now is not None}

    @app.get('/api/collector')
    def coll(user=Depends(current_user)):
        from . import ts
        from .ingest import GATE, ING
        return {'status': {str(k): v for k, v in COLLECTOR.status.items()}, 'codes': {str(k): v for k, v in COLLECTOR.status.codes().items()}, 'stats': dict(ING.stats), 'now': db.jsonable(clock.now()),
                'gate': GATE.status(), 'ts': {**dict(ts.STATS), 'pending': ts.pending(), 'mode': ts.mode()}}

    if os.path.isdir(WEB):
        app.mount('/assets', StaticFiles(directory=os.path.join(WEB, 'assets')), name='assets')

        @app.get('/{path:path}')
        def spa(path: str):
            if path.startswith('api/'):
                raise HTTPException(404, _tr('没有这个接口'))
            f = os.path.join(WEB, path)
            if path and os.path.isfile(f) and os.path.abspath(f).startswith(os.path.abspath(WEB)):
                return FileResponse(f)
            return FileResponse(os.path.join(WEB, 'index.html'))
    return app
