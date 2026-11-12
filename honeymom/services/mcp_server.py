"""MCP（Model Context Protocol）数据接口（5.0 M6）：让我们自己的 AI 和客户自己的 AI 工具（Claude Desktop、Cursor、Dify……）查 MES 和设备数据。

传输：
  - Streamable HTTP：POST /mcp（JSON-RPC 2.0，单条或数组；回复 application/json），GET /mcp 405（不开服务端推送流），DELETE /mcp 结束会话。
    鉴权 = M1 的 API 密钥（X-API-Key: hmk_… 或 Authorization: Bearer hmk_…；也接受登录令牌，给界面里的"试一下"用）。
  - stdio：python -m honeymom mcp [--key hmk_… | 环境变量 HONEYMOM_API_KEY]，每行一条 JSON-RPC（本机进程直接读库）。
协议版本：2025-11-25 / 2025-06-18 / 2025-03-26 / 2024-11-05（握手协商；不认识的版本回最新的）。server/discover（无状态新协议）回 -32601，SDK 会退回 initialize 握手。

能力：tools（services/ai_tools.py 的只读工具 + 每个技能一个 skill_<id> 工具；可选的 draft_action 只起草动作草稿，默认关闭）、
     resources（mes://factory-model 工厂模型、mes://data-catalog 数据目录（表 + 点位含义和单位）、mes://kpi-definitions 指标口径、mes://skills 技能清单；
     模板 mes://equipment/{code}/points）、prompts（几条示例提问）。
安全：每次调用按密钥的权限点和数据范围执行（和登录用户同一套 perms），记审计（audit_log：mcp_connect / mcp_call，来源"接口"），
     按密钥限流（系统参数 mcp_rate_per_min，默认 120 次/分钟，超了 HTTP 429）；需要 AI 功能"MCP 数据接口"开着、密钥有 mcp.connect。
     浏览器来的请求校验 Origin（防 DNS 重绑定）：只允许同源和系统参数 mcp_allowed_origins 里列出的。
接入说明：docs/MCP接入说明.md。
"""
from __future__ import annotations

from ..i18n import _tr
import base64
import hashlib
import hmac
import json
import logging
import secrets
import sys
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .. import audit, auth, context, db, perms

log = logging.getLogger('mes.mcp')
router = APIRouter()
PERM_CONNECT = perms.declare('mcp.connect', '通过 MCP 接口查询数据（外部 AI 工具）', '系统')
PERM_WRITE = perms.declare('mcp.write', 'MCP 写工具：起草动作草稿（审批后才执行）', '系统')

VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')
LATEST = VERSIONS[0]
SERVER_INFO = {'name': 'honeymom', 'title': '蜂元智能 MOM', 'version': '5.0.0'}
INSTRUCTIONS = ('蜂元智能 MOM 的只读数据接口（铸造 + 机加工厂）。建议的用法：先读资源 mes://factory-model 或调用 factory_model / search 找到产线、设备、单据，'
                '再用 query_*（工单、产量、质量、OEE、保养、能耗）、ts_*（设备时序：原始值、汇总、最新值、开关量事件）、cycle_analysis（动作级节拍：瓶颈工位 → 瓶颈动作 → 损失）、'
                'trace（一件一码追溯）、run_skill / skill_*（已保存的技能）。时间参数 period 可以是 today / yesterday / shift / last_shift / 8h / 3d，或 day=YYYY-MM-DD，或 start/end。'
                '回答里的数字以工具结果为准；你只能看到这个密钥的权限和数据范围内的数据；写操作只能起草（审批通过后才执行）。')
RES = {
    'mes://factory-model': ('factory-model', '工厂模型', '工厂 → 车间 → 产线 → 工位 / 设备 的层级，设备类型、理想节拍、有没有动作信号 / 节拍模型（JSON）', 'application/json'),
    'mes://data-catalog': ('data-catalog', '数据目录', '能查的数据表和字段（中文名、关联）+ 设备采集点的含义、单位、用途（Markdown）', 'text/markdown'),
    'mes://kpi-definitions': ('kpi-definitions', '指标口径', 'OEE、可用率、性能率、质量率、废品率、节拍、单件节拍、损失折算等指标的定义和算法（Markdown）', 'text/markdown'),
    'mes://skills': ('skills', '技能清单', '你能运行的已保存技能：名称、说明、参数（JSON）', 'application/json'),
}
PROMPTS = {
    'bottleneck': ('找瓶颈动作', '找出某条产线的瓶颈工位和瓶颈动作，并折算损失', [{'name': 'line', 'description': '产线名称或编号', 'required': False},
                                                                  {'name': 'period', 'description': '时间段，如 yesterday、8h', 'required': False}],
                   '用 cycle_analysis 分析{line}{period}的动作节拍：瓶颈工位是哪台、瓶颈动作是哪个、比标准慢多少秒、每天少做多少件、一年损失多少钱；再对瓶颈动作看最近 7 天有没有漂移，给出现场检查建议。'),
    'shift_review': ('班次复盘', '刚结束的班次：产量、质量、设备 OEE，找出最大的问题', [],
                     '用 query_production、query_quality、query_oee（period=last_shift）复盘刚结束的班次，列出产量、废品率、OEE 最低的 3 台设备和最大的损失，给出下个班要先处理的 3 件事。'),
    'equipment_health': ('设备体检', '一台设备的状态、报警、保养、动作节拍', [{'name': 'equipment', 'description': '设备编号，如 CNC1', 'required': True}],
                         '给 {equipment} 做体检：query_equipment 看维修 / 报警 / 保养，cycle_analysis 看各动作用时和漂移，ts_latest 看实时值，最后用三句话总结风险。'),
}

# ───────────────────────── 会话 / 限流 ─────────────────────────
_rate: dict = defaultdict(deque)
_rate_lock = threading.Lock()
_cfg_cache = {'t': 0.0, 'v': None}


def cfg():
    if time.time() - _cfg_cache['t'] > 10 or _cfg_cache['v'] is None:
        with db.engine.connect() as c:
            v = {'rate_per_min': int(float(db.setting(c, 'mcp_rate_per_min') or 120)), 'write_tools': (db.setting(c, 'mcp_write_tools') or '0') == '1',
                 'allowed_origins': [x.strip() for x in (db.setting(c, 'mcp_allowed_origins') or '').replace(',', ' ').split() if x.strip()]}
        _cfg_cache.update(t=time.time(), v=v)
    return _cfg_cache['v']


def _who(user):
    return user.get('username') if user else 'system'


def rate_ok(user) -> bool:
    k = (user or {}).get('api_key_id') or _who(user)
    lim = max(1, cfg()['rate_per_min'])
    now = time.time()
    with _rate_lock:
        q = _rate[k]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= lim:
            return False
        q.append(now)
        return True


def _sign(s):
    return hmac.new(auth.secret(), ('mcp|' + s).encode(), hashlib.sha256).hexdigest()[:24]


def new_session(user, proto):
    body = base64.urlsafe_b64encode(json.dumps({'u': _who(user), 'v': proto, 'r': secrets.token_hex(6)}).encode()).decode().rstrip('=')
    return body + '.' + _sign(body)


def read_session(sid):
    try:
        body, sig = sid.split('.')
        if not hmac.compare_digest(_sign(body), sig):
            return None
        return json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
    except Exception:
        return None


# ───────────────────────── 结果 / 错误 ─────────────────────────
class RpcError(Exception):
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def _ok(mid, result):
    return {'jsonrpc': '2.0', 'id': mid, 'result': result}


def _err(mid, code, message, data=None):
    e = {'code': code, 'message': message}
    if data is not None:
        e['data'] = data
    return {'jsonrpc': '2.0', 'id': mid, 'error': e}


def _audit(user, action, target, detail):
    try:
        with db.engine.begin() as c:
            audit.audit(c, action, None, None, detail=detail, target=target[:190], user=user, source='api')
    except Exception:
        log.exception('mcp audit')


# ───────────────────────── 工具清单 ─────────────────────────
def tool_list(user):
    from . import ai_tools, skills
    out = []
    wr = cfg()['write_tools']
    for t in ai_tools.available(user, wr):
        out.append({'name': t.name, 'title': t.title, 'description': t.description, 'inputSchema': t.schema(),
                    'annotations': {'title': t.title, 'readOnlyHint': not t.write, 'destructiveHint': False, 'idempotentHint': not t.write, 'openWorldHint': False}})
    if perms.can(user, skills.PERM_USE):
        for sk in skills.list_skills(user) if user else []:
            if sk['status'] != 'active' or not sk.get('mcp_expose', True):
                continue
            props = {p['key']: {'type': 'number' if p['type'] == 'number' else 'string', 'description': p['label'] + (_tr('（缺省 {default}）', default=p['default']) if p.get('default') not in (None, '') else '')
                                + (_tr('；取值 today / yesterday / shift / last_shift / 8h / 3d 或 YYYY-MM-DD') if p['type'] == 'period' else '')} for p in sk['params']}
            out.append({'name': f"skill_{sk['id']}", 'title': _tr('技能：{name}', name=sk['name']), 'description': _tr('运行已保存的技能「{name}」：{v}', name=sk['name'], v=sk.get('description') or sk.get('question') or '')[:900],
                        'inputSchema': {'type': 'object', 'properties': props},
                        'annotations': {'title': sk['name'], 'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}})
    return out


def call_tool(user, name, args):
    from . import ai_tools, skills
    t0 = time.time()
    if not isinstance(args, dict):
        raise RpcError(-32602, _tr('arguments 要是对象'))
    if name.startswith('skill_') and name[6:].isdigit():
        sk = skills.get(int(name[6:]))
        if not sk or not skills.visible(user, sk) or sk['status'] != 'active':
            raise RpcError(-32602, f'Unknown tool: {name}')
        try:
            r = skills.run(sk['id'], args, trigger='mcp', user=user, narrative=False)
            res = {'summary': r.get('summary') or '', 'blocks': r.get('blocks') or [], 'data': {'run_id': r['id'], 'status': r['status'], 'error': r.get('error')}}
            ok = r['status'] == 'ok'
            err = r.get('error')
        except HTTPException as ex:
            res, ok, err = None, False, str(ex.detail)
    else:
        t = ai_tools.TOOLS.get(name)
        if not t or t not in ai_tools.available(user, cfg()['write_tools']):
            raise RpcError(-32602, f'Unknown tool: {name}')
        try:
            res, ok, err = ai_tools.call(name, args), True, None
        except ai_tools.ToolError as ex:
            res, ok, err = None, False, str(ex)
        except HTTPException as ex:
            res, ok, err = None, False, str(ex.detail)
        except Exception as ex:
            log.exception('mcp tool %s', name)
            res, ok, err = None, False, f'{type(ex).__name__}: {str(ex)[:300]}'
    ms = int((time.time() - t0) * 1000)
    _audit(user, 'mcp_call', f'mcp:{name}', {'tool': name, 'args': {k: (str(v)[:300] if not isinstance(v, (int, float, bool)) else v) for k, v in args.items()},
                                              'ok': ok, 'error': err, 'ms': ms, 'rows': (res or {}).get('count')})
    if not ok:
        return {'content': [{'type': 'text', 'text': _tr('出错：{err}', err=err)}], 'isError': True}
    text = (res.get('summary') or '').strip()
    body = {k: v for k, v in res.items() if k != 'blocks'}
    payload = json.dumps(body, ensure_ascii=False, default=str)
    if len(payload) > 60000:
        body = {k: v for k, v in body.items() if k not in ('rows',)} | {'rows_truncated': True, 'rows': (res.get('rows') or [])[:100]}
        payload = json.dumps(body, ensure_ascii=False, default=str)[:60000]
    return {'content': [{'type': 'text', 'text': (text + '\n\n' if text else '') + payload}], 'structuredContent': json.loads(json.dumps(body, default=str)) if len(payload) < 60000 else {'summary': text},
            'isError': False}


# ───────────────────────── 资源 ─────────────────────────
KPI_MD = """# 蜂元智能 MOM 指标口径

| 指标 | 定义 |
|---|---|
| OEE | 可用率 × 性能率 × 质量率（按设备，时间段内） |
| 可用率 A | 运行时间 ÷ 计划时间（计划时间 = 时间段 − 计划停机） |
| 性能率 P | 理想节拍 × 产出件数 ÷ 运行时间 |
| 质量率 Q | 合格件 ÷ 产出件 |
| 废品率 | 报废件 ÷ 投产件（浇注 + 来料） |
| 循环用时（节拍分析） | 循环信号上升沿 → 下降沿（或步号回到第一步），每个循环一个值，取中位数 |
| 动作用时 | 起始信号变化（如电磁阀得电）→ 结束信号变化（如伸出到位），毫秒级 |
| 单件节拍 | 循环用时中位数 ÷ 并联台数 ÷ 每循环件数；最大的是瓶颈工位 |
| 节拍目标 takt | 产线设定（节拍分析 → 参数），工位单件节拍 ÷ takt = 负荷 |
| 慢动作 | 当前窗口中位数比基线（参考窗口学到的中位数）或标准时间多 ≥ max(0.3 s, 20%) |
| 漂移 | 每天中位数的线性趋势：末天 − 首天 ≥ max(0.2 s, 15%) 且斜率 > 0、相关系数 ≥ 0.6 |
| 损失折算 | 每循环多出的秒数 × 每天循环数 = 秒/天；× 每循环件数 ÷ 循环间隔 = 件/天；× 单件价值 = 元/天；× 年工作天数 = 元/年。只有瓶颈工位的损失才真正减少整线产出（affects_output） |
| 能耗异常 | 某设备某小时用电 > 过去 7 天同一小时中位数 × 倍数（默认 1.3）且多出 ≥ 1 kWh |
"""


def read_resource(user, uri):
    from . import ai_tools, skills
    with context.acting(user, 'api'):
        if uri == 'mes://factory-model':
            return 'application/json', json.dumps(ai_tools.call('factory_model', {})['data'], ensure_ascii=False, default=str)
        if uri == 'mes://kpi-definitions':
            return 'text/markdown', KPI_MD
        if uri == 'mes://skills':
            sk = skills.list_skills(user) if perms.can(user, skills.PERM_USE) else []
            return 'application/json', json.dumps([{'id': s['id'], 'tool': f"skill_{s['id']}", 'name': s['name'], 'description': s.get('description'), 'params': s['params'],
                                                    'status': s['status']} for s in sk], ensure_ascii=False)
        if uri == 'mes://data-catalog':
            return 'text/markdown', catalog_md(user)
        if uri.startswith('mes://equipment/') and uri.endswith('/points'):
            code = uri[len('mes://equipment/'):-len('/points')]
            return 'application/json', json.dumps(ai_tools.call('list_points', {'equipment': code, 'limit': 500}), ensure_ascii=False, default=str)
    raise RpcError(-32002, 'Resource not found', {'uri': uri})


def catalog_md(user):
    from . import ai_tools
    from .ai import TABLE_PERMS, schema_text, sql_table_perm
    lines = [_tr('# 蜂元智能 MOM 数据目录'), '', _tr('## 数据表（query_sql 可查；只列你有权限的）'), '']
    for ln in schema_text().split('\n'):
        name = ln.split('【')[0].split(':')[0].strip().split(' / ')[0]
        p = sql_table_perm(name) or TABLE_PERMS.get(name)
        if p is None or perms.can(user, p):
            lines.append('- ' + ln)
    lines += ['', _tr('## 设备采集点（key / 含义 / 单位 / 用途）'), '']
    try:
        pts = ai_tools.call('list_points', {'limit': 500})
        cur = None
        for r in sorted(pts['rows'], key=lambda x: (x['equipment'] or '', x['key'])):
            if r['equipment'] != cur:
                cur = r['equipment']
                lines += ['', f'### {cur or _tr('（未关联设备）')}', '', _tr('| key | 含义 | 单位 | 用途 |'), '|---|---|---|---|']
            lines.append(f"| {r['key']} | {r['meaning'] or ''} | {r['unit'] or ''} | {r['role']} |")
    except ai_tools.ToolError as ex:
        lines.append(_tr('（{ex}）', ex=ex))
    return '\n'.join(lines)


def resource_list(user):
    return [{'uri': u, 'name': n, 'title': t, 'description': d, 'mimeType': m} for u, (n, t, d, m) in RES.items()]


# ───────────────────────── 分发 ─────────────────────────
def handle(msg, user, session=None):
    """一条 JSON-RPC 消息 → 回复（通知返回 None）。"""
    if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or not isinstance(msg.get('method'), str):
        if isinstance(msg, dict) and ('result' in msg or 'error' in msg):
            return None                                  # 客户端发来的响应（我们不发请求），忽略
        return _err(msg.get('id') if isinstance(msg, dict) else None, -32600, 'Invalid Request')
    mid, method, params = msg.get('id'), msg['method'], msg.get('params') or {}
    is_note = 'id' not in msg
    try:
        if method.startswith('notifications/'):
            return None
        if method == 'initialize':
            pv = params.get('protocolVersion')
            proto = pv if pv in VERSIONS else LATEST
            _audit(user, 'mcp_connect', 'mcp:initialize', {'client': params.get('clientInfo'), 'protocol': proto, 'requested': pv})
            res = {'protocolVersion': proto, 'capabilities': {'tools': {'listChanged': False}, 'resources': {'listChanged': False, 'subscribe': False},
                                                              'prompts': {'listChanged': False}, 'logging': {}},
                   'serverInfo': SERVER_INFO, 'instructions': INSTRUCTIONS}
            return None if is_note else _ok(mid, res)
        if method == 'ping':
            r = {}
        elif method == 'tools/list':
            r = {'tools': tool_list(user)}
        elif method == 'tools/call':
            name = params.get('name')
            if not isinstance(name, str):
                raise RpcError(-32602, _tr('tools/call 需要 name'))
            if not rate_ok(user):
                raise RpcError(-32000, _tr('调用太频繁：每分钟最多 {rate_per_min} 次', rate_per_min=cfg()['rate_per_min']), {'retry_after': 60})
            with context.acting(user, 'api'):
                r = call_tool(user, name, params.get('arguments') or {})
        elif method == 'resources/list':
            r = {'resources': resource_list(user)}
        elif method == 'resources/templates/list':
            r = {'resourceTemplates': [{'uriTemplate': 'mes://equipment/{code}/points', 'name': 'equipment-points', 'title': _tr('设备采集点'),
                                        'description': _tr('一台设备的全部采集点（含义、单位、用途、最新值）'), 'mimeType': 'application/json'}]}
        elif method == 'resources/read':
            uri = params.get('uri') or ''
            if not rate_ok(user):
                raise RpcError(-32000, _tr('调用太频繁：每分钟最多 {rate_per_min} 次', rate_per_min=cfg()['rate_per_min']), {'retry_after': 60})
            try:
                mime, text = read_resource(user, uri)
                _audit(user, 'mcp_call', f'mcp:resource:{uri}', {'resource': uri, 'ok': True})
            except Exception as ex:
                if not isinstance(ex, RpcError):
                    _audit(user, 'mcp_call', f'mcp:resource:{uri}', {'resource': uri, 'ok': False, 'error': str(ex)[:300]})
                    raise RpcError(-32603, _tr('读资源出错：{v}', v=str(ex)[:300]))
                raise
            r = {'contents': [{'uri': uri, 'mimeType': mime, 'text': text}]}
        elif method == 'prompts/list':
            r = {'prompts': [{'name': k, 'title': t, 'description': d, 'arguments': a} for k, (t, d, a, _) in PROMPTS.items()]}
        elif method == 'prompts/get':
            p = PROMPTS.get(params.get('name'))
            if not p:
                raise RpcError(-32602, f"Unknown prompt: {params.get('name')}")
            a = params.get('arguments') or {}
            text = p[3].format(line=a.get('line') or '', period=(_tr('（') + a['period'] + _tr('）')) if a.get('period') else '', equipment=a.get('equipment') or _tr('某台设备'))
            r = {'description': p[1], 'messages': [{'role': 'user', 'content': {'type': 'text', 'text': text}}]}
        elif method == 'logging/setLevel':
            r = {}
        elif method == 'completion/complete':
            r = {'completion': {'values': [], 'hasMore': False}}
        else:
            raise RpcError(-32601, f'Method not found: {method}')
        return None if is_note else _ok(mid, r)
    except RpcError as ex:
        return None if is_note else _err(mid, ex.code, ex.message, ex.data)
    except Exception as ex:
        log.exception('mcp %s', method)
        return None if is_note else _err(mid, -32603, f'Internal error: {str(ex)[:300]}')


def check_user(user):
    from .ai_switch import enabled, off_message
    if not enabled('mcp'):
        raise HTTPException(403, off_message('mcp'))
    if not perms.can(user, PERM_CONNECT):
        raise HTTPException(403, _tr('这个密钥没有 MCP 权限（mcp.connect）'))


# ───────────────────────── HTTP ─────────────────────────
def _jerr(status, code, msg, headers=None, mid=None):
    return JSONResponse(_err(mid, code, msg), status_code=status, headers=headers or {})


@router.post('/mcp')
async def mcp_post(request: Request):
    import asyncio
    org = request.headers.get('origin')
    if org and not _origin_ok(org, request):
        return _jerr(403, -32000, _tr('不允许的 Origin：{org}', org=org))
    pv = request.headers.get('mcp-protocol-version')
    if pv and pv not in VERSIONS:
        return _jerr(400, -32000, _tr('不支持的协议版本：{pv}（支持 {join}）', pv=pv, join=", ".join(VERSIONS)))
    try:
        user = await asyncio.to_thread(auth.principal, request)
        await asyncio.to_thread(check_user, user)
    except HTTPException as ex:
        hd = {'WWW-Authenticate': 'Bearer realm="honeymom-mcp"'} if ex.status_code == 401 else {}
        return _jerr(ex.status_code, -32001 if ex.status_code == 401 else -32003, str(ex.detail), hd)
    try:
        body = json.loads((await request.body()) or b'null')
    except ValueError:
        return _jerr(400, -32700, 'Parse error')
    sid = request.headers.get('mcp-session-id')
    sess = None
    if sid:
        sess = read_session(sid)
        if not sess or sess.get('u') != _who(user):
            return _jerr(404, -32001, _tr('会话不存在或已过期，请重新 initialize'))
    msgs = body if isinstance(body, list) else [body]
    if not msgs:
        return _jerr(400, -32600, 'Invalid Request')

    def go():
        with context.acting(user, 'api'):
            return [handle(m, user, sess) for m in msgs]
    outs = await asyncio.to_thread(go)
    outs = [o for o in outs if o is not None]
    headers = {}
    init = next((m for m in msgs if isinstance(m, dict) and m.get('method') == 'initialize' and 'id' in m), None)
    if init:
        res = next((o for o in outs if o.get('id') == init.get('id') and 'result' in o), None)
        if res:
            headers['Mcp-Session-Id'] = new_session(user, res['result']['protocolVersion'])
    if not outs:
        return Response(status_code=202, headers=headers)
    if any(o.get('error', {}).get('code') == -32000 and 'retry_after' in (o['error'].get('data') or {}) for o in outs) and len(outs) == 1:
        return JSONResponse(outs[0], status_code=429, headers={**headers, 'Retry-After': '60'})
    return JSONResponse(outs if isinstance(body, list) else outs[0], headers=headers)


@router.get('/mcp')
def mcp_get():
    return Response(status_code=405, headers={'Allow': 'POST, DELETE'})


@router.delete('/mcp')
def mcp_delete(request: Request):
    return Response(status_code=204)


def _origin_ok(org, request):
    from urllib.parse import urlparse
    host = (request.headers.get('host') or '').lower()
    o = urlparse(org)
    if (o.netloc or '').lower() == host:
        return True
    if (o.hostname or '') in ('localhost', '127.0.0.1') and (request.client and request.client.host in ('127.0.0.1', '::1')):
        return True
    return org.rstrip('/') in [x.rstrip('/') for x in cfg()['allowed_origins']]


# ───────────────────────── 管理（"系统 → MCP 接入"页面） ─────────────────────────
PRESETS = {
    'readonly': ('全部只读', ['mcp.connect', '*.read', 'page.*', 'equipment.ts.read', 'cycle.view', 'skill.use']),
    'equipment': ('设备与节拍', ['mcp.connect', 'equipment.*.read', 'master.*.read', 'page.equipment.*', 'page.energy', 'equipment.ts.read', 'cycle.view', 'skill.use']),
    'quality': ('质量', ['mcp.connect', 'quality.*.read', 'production.*.read', 'master.*.read', 'page.quality.*']),
    'drafts': ('只读 + 起草动作', ['mcp.connect', 'mcp.write', '*.read', 'page.*', 'equipment.ts.read', 'cycle.view', 'skill.use']),
}


@router.get('/api/mcp/info')
def api_info(request: Request, user=Depends(perms.require('system.apikey.manage'))):
    from .ai_switch import enabled
    base = str(request.base_url).rstrip('/')
    return {'endpoint': base + '/mcp', 'enabled': enabled('mcp'), 'versions': list(VERSIONS), 'settings': cfg(), 'presets': {k: {'label': v[0], 'perms': v[1]} for k, v in PRESETS.items()},
            'tools': tool_list(user), 'resources': resource_list(user), 'prompts': [{'name': k, 'title': v[0], 'description': v[1]} for k, v in PROMPTS.items()],
            'stdio': _tr('python -m honeymom mcp --key <API 密钥>')}


@router.put('/api/mcp/settings')
def api_settings(body: dict = Body(...), user=Depends(perms.require('system.apikey.manage'))):
    with db.engine.begin() as c:
        if 'rate_per_min' in body:
            n = int(float(body['rate_per_min'] or 120))
            if not 1 <= n <= 10000:
                raise HTTPException(400, _tr('每分钟次数 1~10000'))
            db.set_setting(c, 'mcp_rate_per_min', n, _tr('MCP 每个密钥每分钟最多调用次数'))
        if 'write_tools' in body:
            db.set_setting(c, 'mcp_write_tools', '1' if body['write_tools'] else '0', _tr('MCP 写工具（只起草动作草稿）开关'))
        if 'allowed_origins' in body:
            db.set_setting(c, 'mcp_allowed_origins', ' '.join(str(x) for x in (body['allowed_origins'] or []))[:500], _tr('MCP 允许的浏览器 Origin'))
        audit.audit(c, 'mcp_settings', None, None, detail=body, target='setting:mcp', user=user)
    _cfg_cache['t'] = 0
    return cfg()


@router.post('/api/mcp/keys')
def api_new_key(body: dict = Body(...), user=Depends(perms.require('system.apikey.manage'))):
    from .. import apikeys
    pre = PRESETS.get(body.get('preset') or 'readonly')
    ps = body.get('perms') if isinstance(body.get('perms'), list) and body.get('perms') else (pre[1] if pre else PRESETS['readonly'][1])
    if 'mcp.connect' not in ps:
        ps = ['mcp.connect'] + ps
    try:
        with db.engine.begin() as c:
            kid, key = apikeys.create(c, body.get('name') or 'MCP', roles='', perms=ps, scope=body.get('scope'), expires_at=body.get('expires_at'),
                                      ip_allow=body.get('ip_allow') or '', remark=body.get('remark') or _tr('MCP 接入'), created_by=user['username'])
            audit.audit(c, 'create', 'api_key', kid, detail={'name': body.get('name'), 'perms': ps, 'scope': body.get('scope'), 'for': 'mcp'}, target=f'api_key:{kid}', user=user)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {'id': kid, 'key': key, 'perms': ps}


@router.get('/api/mcp/calls')
def api_calls(limit: int = 50, user=Depends(perms.require('system.apikey.manage'))):
    total, rows = audit.query({'q': 'mcp:'}, limit=max(1, min(500, limit)))
    return {'total': total, 'items': [{'time': db.jsonable(r['time']), 'user': r['user'], 'action': r['action'], 'target': r['target'], 'detail': r['detail'], 'ip': r['ip']}
                                      for r in rows if (r['target'] or '').startswith('mcp:')]}


@router.post('/api/mcp/try')
def api_try(body: dict = Body(...), user=Depends(perms.require('system.apikey.manage'))):
    """界面上的"试一下"：用当前登录的人调用一个工具（同一套 handle）。"""
    check_user(user)
    with context.acting(user, 'api'):
        return handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': body.get('name'), 'arguments': body.get('arguments') or {}}}, user)


# ───────────────────────── stdio ─────────────────────────
def serve_stdio(key=None, username=None):
    """python -m honeymom mcp：stdin 每行一条 JSON-RPC，stdout 每行一条回复（日志走 stderr）。"""
    from .. import apikeys
    out = sys.stdout
    if key:
        try:
            user = apikeys.verify(key, '127.0.0.1')
        except HTTPException as ex:
            sys.stderr.write(_tr('MCP：API 密钥不可用：{detail}\n', detail=ex.detail))
            sys.exit(2)
    elif username:
        user = auth.user_by_name(username)
        if not user:
            sys.stderr.write(_tr('MCP：没有用户 {username}\n', username=username))
            sys.exit(2)
    else:
        sys.stderr.write(_tr('MCP：请用 --key 或环境变量 HONEYMOM_API_KEY 给 API 密钥\n'))
        sys.exit(2)
    try:
        check_user(user)
    except HTTPException as ex:
        sys.stderr.write(_tr('MCP：{detail}\n', detail=ex.detail))
        sys.exit(3)
    sys.stderr.write(_tr('蜂元智能 MOM MCP（stdio）已就绪：{username}\n', username=user['username']))
    sys.stderr.flush()
    inp = sys.stdin.buffer if hasattr(sys.stdin, 'buffer') else sys.stdin
    for raw in inp:
        line = raw.decode('utf-8', 'replace').strip() if isinstance(raw, bytes) else raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            msg = None
            res = [_err(None, -32700, 'Parse error')]
        else:
            with context.acting(user, 'api'):
                res = [handle(m, user) for m in (msg if isinstance(msg, list) else [msg])]
        res = [r for r in res if r is not None]
        if res:
            out.write(json.dumps(res if isinstance(msg, list) else res[0], ensure_ascii=False, default=str) + '\n')
            out.flush()
