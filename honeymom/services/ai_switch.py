"""AI 功能开关：一个总开关 + 每个智能化功能一个开关 + 演示模式（回放录制好的 AI 回答）。

默认全部关闭。开关存在 setting 表（key = ai_switches，JSON），管理员在"系统 → AI 功能"里改。
关闭的功能：接口统一返回 403 {"detail": "<功能>已关闭（系统设置 → AI 功能可开启）", "code": "ai_disabled", "feature": "<key>"}，
前端据 GET /api/ai/switches 隐藏或置灰入口。

用法（后端）：
    from .ai_switch import enabled, require
    if enabled('tool_wear'): ...          # 功能是否开启（总开关 && 功能开关）
    require('daily')                      # 没开启就抛 403（接口里用）
    live_llm()                            # 现在可以真的调用大模型吗（总开关开、没在演示回放、配置了令牌）

新功能：在 FEATURES 里加一行；接口前缀写进 ROUTES 由中间件统一拦截（或在接口里调用 require）。
"""
from ..i18n import _tr
import json
import re
import threading
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import db
from ..auth import current_user

router = APIRouter()

# key, 名称, 说明
FEATURES = [
    ('ask', '问数助手', '右侧 AI 助手对话、问数整页、页面上的"问 AI"、8D 的 AI 起草'),
    ('daily', 'AI 日报', '生成每日生产日报（含 AI 分析与建议）'),
    ('watch', 'AI 值守', '设备异常解释；后台观察质量 / 设备 / 排程 / 物料，主动发现苗头并起草处理动作'),
    ('automation', '自动化', '一句话自动化规则（历史回放、启用 / 暂停）；智能体按事件 / 定时 / Webhook 自动运行（还需开启"智能体编排"）'),
    ('tool_wear', '刀具寿命估计', '读主轴负载估计刀具磨损（关闭时只按寿命计数）'),
    ('charge_opt', '配料优化', '最低成本配料（线性规划）'),
    ('kb', '知识库问答', '按知识库 / SOP 回答问题'),
    ('agents', '智能体编排', '智能体画布、调试运行、审批中心'),
    ('backtrace', '缺陷回溯 AI 解读', '缺陷回溯页的 AI 根因解读（统计本身不需要 AI）'),
    ('coplan', '协同排产 AI 建议', '铸机协同排产页的 AI 排产建议（排产计算本身不需要 AI）'),
    ('onboard', '接入辅助', '接入中心：点表对列、AI 补全点位含义 / 单位 / 设备 / 用途、读 PLC 程序生成采集点草稿（关闭时只能人工核对）'),
    ('impl', '实施助手', '数据导入时 AI 建议客户表格的列对到哪个字段（导入、校验本身不需要 AI）'),
    ('mcp', 'MCP 数据接口', '让外部 AI 工具（Claude、Cursor、Dify…）用 API 密钥通过 MCP 只读查询 MES 和设备数据（/mcp）'),
    ('cycle', '节拍分析 AI', '动作级节拍分析的 AI 解释、动作模型建议、节拍漂移值守（节拍统计本身不需要 AI）'),
    ('skills', 'AI 技能', '把问答保存为技能：改参数、定时运行（复用保存的查询，只在写结论时调用大模型）'),
    ('quality_plus', 'AI 质量助手', '按历史不良起草 PFMEA 条目、解释客诉影响范围、建议控制计划反应计划（都先成草稿，人确认后才写入）'),
    ('erp', 'ERP 助手', '解释 MRP 为什么建议这个、找出有交期风险的订单、起草采购订单（草稿要人审批后才建单；MRP / 订单看板本身不需要 AI）'),
]
LABEL = {k: n for k, n, _ in FEATURES}

# 接口 → 功能（按顺序匹配，先写更具体的）。method 为 None 表示所有方法。
ROUTES = [
    (None, re.compile(r'^/api/agents/\d+/webhook$'), ('automation', 'agents')),
    (None, re.compile(r'^/api/agents(/|$)'), ('agents',)),          # 审批中心不整体拦：审批流程（M3）不依赖 AI；智能体 / AI 草稿的审批在 agent/api.py 里按开关过滤
    ('POST', re.compile(r'^/api/impl/import/jobs/\d+/ai_map$'), ('impl',)),
    (None, re.compile(r'^/api/assistant/'), ('ask',)),
    ('POST', re.compile(r'^/api/ai/ask$'), ('ask',)),
    ('POST', re.compile(r'^/api/ai/daily$'), ('daily',)),
    (None, re.compile(r'^/api/ai/(explain|watch)(/|$)'), ('watch',)),
    (None, re.compile(r'^/api/ai/automations(/|$)'), ('automation',)),
    (None, re.compile(r'^/api/ai/drafts(/|$)'), ('ask',)),
    (None, re.compile(r'^/api/ai/backtrace(/|$)'), ('backtrace',)),
    (None, re.compile(r'^/api/ai/coplan(/|$)'), ('coplan',)),
    ('POST', re.compile(r'^/api/onboard/ai/(draft|plc|columns)$'), ('onboard',)),
    (None, re.compile(r'^/api/skills(/|$)'), ('skills',)),
    ('POST', re.compile(r'^/api/cycle/explain$'), ('cycle',)),
    (None, re.compile(r'^/api/ai/kb_ask$'), ('kb',)),
    (None, re.compile(r'^/api/iatf/ai/'), ('quality_plus',)),
    (None, re.compile(r'^/api/erp/ai/'), ('erp',)),
    (None, re.compile(r'^/api/foundry/optimize$'), ('charge_opt',)),
    ('POST', re.compile(r'^/api/e/report_8d/\d+/do/ai_draft$'), ('ask',)),
]

DEFAULT = {'global': False, 'features': {k: False for k, _, _ in FEATURES}, 'demo': False, 'record': False}
_lock = threading.Lock()
_cache = {'v': None, 't': 0.0}


def _load():
    with db.engine.connect() as c:
        raw = db.setting(c, 'ai_switches')
    v = json.loads(json.dumps(DEFAULT))
    if raw:
        try:
            d = json.loads(raw)
            v['global'] = bool(d.get('global'))
            v['demo'] = bool(d.get('demo'))
            v['record'] = bool(d.get('record'))
            for k in v['features']:
                v['features'][k] = bool((d.get('features') or {}).get(k))
        except ValueError:
            pass
    return v


def get():
    """当前开关（带 2 秒缓存；本进程写入时立即失效）。"""
    with _lock:
        if _cache['v'] is None or time.time() - _cache['t'] > 2:
            _cache['v'], _cache['t'] = _load(), time.time()
        return json.loads(json.dumps(_cache['v']))


def save(v: dict):
    cur = get()
    if 'global' in v:
        cur['global'] = bool(v['global'])
    for k in ('demo', 'record'):
        if k in v:
            cur[k] = bool(v[k])
    for k, val in (v.get('features') or {}).items():
        if k in cur['features']:
            cur['features'][k] = bool(val)
    with db.engine.begin() as c:
        db.set_setting(c, 'ai_switches', json.dumps(cur, ensure_ascii=False), _tr('AI 功能开关（系统 → AI 功能）'))
    with _lock:
        _cache['v'], _cache['t'] = cur, time.time()
    return cur


def enabled(feature: str | None = None) -> bool:
    v = get()
    if not v['global']:
        return False
    return True if feature is None else bool(v['features'].get(feature))


def demo() -> bool:
    """演示模式：AI 调用回放录制好的回答，不花 token。"""
    v = get()
    return v['global'] and v['demo']


def recording() -> bool:
    v = get()
    return v['global'] and v['record'] and not v['demo']


def off_message(feature: str | None) -> str:
    if feature is None or not get()['global']:
        name = LABEL.get(feature, 'AI 功能') if feature else _tr('AI 功能')
        return _tr('{name}已关闭：AI 总开关未开启（系统设置 → AI 功能可开启）', name=name)
    return _tr('{get}已关闭（系统设置 → AI 功能可开启）', get=LABEL.get(feature, feature))


class AiDisabled(HTTPException):
    def __init__(self, feature):
        super().__init__(403, off_message(feature))
        self.feature = feature


def require(*features):
    for f in features:
        if not enabled(f):
            raise AiDisabled(f)


def off_response(feature):
    return JSONResponse({'detail': off_message(feature), 'code': 'ai_disabled', 'feature': feature}, status_code=403)


def route_features(method: str, path: str):
    for m, rx, feats in ROUTES:
        if (m is None or m == method) and rx.match(path):
            return feats
    return ()


def status():
    from .ai import llm_cfg, llm_configured
    v = get()
    cfg = llm_cfg(raw=True)
    eff = {k: v['global'] and on for k, on in v['features'].items()}
    return {**v, 'effective': eff, 'labels': [{'key': k, 'label': n, 'desc': d} for k, n, d in FEATURES],
            'llm_configured': llm_configured(cfg), 'model': cfg.get('model'),
            'mode': 'off' if not v['global'] else 'demo' if v['demo'] else 'record' if v['record'] else 'live'}


# ---------------- 接口 ----------------
@router.get('/api/ai/switches')
def get_switches(user=Depends(current_user)):
    return status()


@router.put('/api/ai/switches')
def put_switches(body: dict = Body(...), user=Depends(current_user)):
    from .. import perms
    perms.check(user, 'system.ai.manage', _tr('只有管理员可以修改 AI 功能开关'))
    save(body)
    from .ops import audit
    with db.engine.begin() as c:
        audit(c, user['username'], 'ai_switches', 'setting:ai_switches', body)
    return status()


def install(app):
    app.include_router(router)

    @app.middleware('http')
    async def ai_switch_mw(request: Request, call_next):
        p = request.url.path
        if p.startswith('/api/'):
            for f in route_features(request.method, p):
                if not enabled(f):
                    return off_response(f)
        return await call_next(request)

    @app.exception_handler(AiDisabled)
    async def _off(request, exc: AiDisabled):
        return off_response(exc.feature)
