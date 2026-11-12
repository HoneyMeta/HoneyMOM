"""5.0 M6 AI 新功能检查：MCP 数据接口、动作级节拍分析、保存为技能。对着运行中的 MES（测试副本）跑，SQLite 和 PostgreSQL 都一样。

    python tests/ai50_check.py
环境变量：
  MES_URL        MES 地址（默认 http://127.0.0.1:8900）
  VF_URL         虚拟工厂地址（给了就跑"气缸漏气"端到端：导入交付包 → 自动识别动作 → 学基线 → 注入漏气 → 找到工位和动作、算损失）
  MCP_SDK_PATH   官方 MCP Python SDK（pip 包 mcp）装在的目录：给了就用 SDK 当客户端再核对一遍（HTTP + stdio）
  AI50_PY / AI50_LAUNCHER / AI50_CWD   stdio 测试用的 python、启动方式（如 run_m6_hm.py 或 "-m honeymom"）、工作目录；HM_DATA / MES_DB 跟服务端一致
  AI50_SKIP      逗号分隔跳过的节：mcp,scope,synth,skills,vf
AI 用演示模式（回放录制，不花 token）；结束时恢复原来的 AI 开关。
"""
import json
import math
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding='utf-8')
MES = os.environ.get('MES_URL', 'http://127.0.0.1:8900').rstrip('/')
VFH = (os.environ.get('VF_URL') or '').rstrip('/')
SKIP = set((os.environ.get('AI50_SKIP') or '').split(','))
results = []
TOKEN = ''


def ok(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name, (str(detail)[:600] if detail not in (None, '') else ''), flush=True)
    return bool(cond)


def http(method, url, body=None, headers=None, timeout=300, raw=False):
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(url, method=method, data=data, headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as f:
            b = f.read()
            return f.status, dict((k.lower(), v) for k, v in f.headers.items()), (b if raw else (json.loads(b) if b else None))
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            j = json.loads(b) if b else None
        except ValueError:
            j = b.decode('utf-8', 'replace')
        return e.code, dict((k.lower(), v) for k, v in e.headers.items()), j


def api(method, path, body=None, tok=None):
    st, _h, j = http(method, MES + path, body, {'Authorization': 'Bearer ' + (tok or TOKEN)})
    if st >= 400:
        raise RuntimeError(f'{method} {path} → {st} {j}')
    return j


def mcp(key, method, params=None, mid=1, headers=None, notify=False):
    msg = {'jsonrpc': '2.0', 'method': method, **({'params': params} if params is not None else {}), **({} if notify else {'id': mid})}
    return http('POST', MES + '/mcp', msg, {'X-API-Key': key, 'Accept': 'application/json, text/event-stream', **(headers or {})})


def call(key, name, args=None):
    st, _h, j = mcp(key, 'tools/call', {'name': name, 'arguments': args or {}})
    return st, j


def res_of(j):
    return (j or {}).get('result') or {}


TOKEN = http('POST', MES + '/api/login', {'username': 'admin', 'password': 'honey123'})[2]['token']
ok('登录', TOKEN)
SW0 = api('GET', '/api/ai/switches')
for k in ('mcp', 'cycle', 'skills'):
    ok(f'AI 功能开关有 {k}', k in SW0['features'])
api('PUT', '/api/ai/switches', {'global': True, 'demo': True, 'record': False, 'features': {k: True for k in SW0['features']}})
LLM0 = api('GET', '/api/skills/llm_calls')['n']


def new_key(name, preset='readonly', perms=None, scope=None):
    return api('POST', '/api/mcp/keys', {'name': f'{name}-{int(time.time() * 1000) % 100000}', 'preset': preset, **({'perms': perms} if perms else {}), 'scope': scope})['key']


def lines():
    return api('GET', '/api/e/line?size=200')['items']


def equipment(code):
    it = api('GET', '/api/e/equipment?q=' + urllib.parse.quote(code) + '&size=50')['items']
    return next((x for x in it if x['code'] == code), None)


KEY = new_key('ai50')

# ═════════════════════════ A. MCP 协议 ═════════════════════════
if 'mcp' not in SKIP:
    print('— MCP 协议 —')
    st, h, j = http('POST', MES + '/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}})
    ok('MCP：没有密钥 → 401', st == 401 and (j or {}).get('error'), st)
    st, h, j = mcp('hmk_deadbeef_nope', 'initialize', {'protocolVersion': '2025-06-18'})
    ok('MCP：错误密钥 → 401', st == 401, st)

    nk = api('POST', '/api/admin/apikeys', {'name': f'nomcp{int(time.time()) % 10000}', 'perms': ['*.read']})
    nkey = nk.get('key') if isinstance(nk, dict) else None
    if nkey:
        st, h, j = mcp(nkey, 'initialize', {'protocolVersion': '2025-06-18'})
        ok('MCP：密钥没有 mcp.connect → 403', st == 403, st)
    st, h, j = mcp(KEY, 'initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'ai50', 'version': '1'}})
    r = res_of(j)
    sid = h.get('mcp-session-id')
    ok('MCP initialize：协商到客户端的版本', st == 200 and r.get('protocolVersion') == '2025-06-18', r.get('protocolVersion'))
    ok('MCP initialize：能力 tools / resources / prompts + serverInfo + 说明', all(k in r.get('capabilities', {}) for k in ('tools', 'resources', 'prompts'))
       and r.get('serverInfo', {}).get('name') == 'honeymom' and r.get('instructions'), r.get('capabilities'))
    ok('MCP initialize：返回 Mcp-Session-Id', bool(sid))
    st, h, j = mcp(KEY, 'initialize', {'protocolVersion': '1999-01-01'})
    ok('MCP initialize：不认识的版本 → 回服务端最新版本', res_of(j).get('protocolVersion') == '2025-11-25', res_of(j).get('protocolVersion'))
    st, h, j = mcp(KEY, 'notifications/initialized', notify=True, headers={'Mcp-Session-Id': sid})
    ok('MCP：通知 → 202 无内容', st == 202 and not j, st)
    st, h, j = mcp(KEY, 'tools/list', headers={'Mcp-Session-Id': sid, 'MCP-Protocol-Version': '2025-06-18'})
    tools = {t['name']: t for t in res_of(j).get('tools', [])}
    want = {'search', 'get_record', 'query_work_orders', 'query_production', 'query_quality', 'query_equipment', 'query_oee', 'factory_model', 'list_points',
            'ts_range', 'ts_rollup', 'ts_latest', 'ts_events', 'cycle_analysis', 'trace', 'run_skill'}
    ok('MCP tools/list：查询 / 时序 / 节拍 / 追溯 / 技能工具齐全', want <= set(tools), sorted(want - set(tools)))
    ok('MCP tools/list：每个工具有 inputSchema 和只读标注', all(t.get('inputSchema', {}).get('type') == 'object' and t.get('annotations', {}).get('readOnlyHint') for t in tools.values()))
    ok('MCP tools/list：写工具默认不出现', 'draft_action' not in tools)
    ok('MCP tools/list：技能作为工具（skill_<id>）', any(n.startswith('skill_') for n in tools), [n for n in tools if n.startswith('skill_')][:3])
    st, j = call(KEY, 'factory_model')
    r = res_of(j)
    ok('MCP tools/call factory_model：文本 + 结构化结果', st == 200 and not r.get('isError') and r.get('content', [{}])[0].get('type') == 'text' and r.get('structuredContent', {}).get('data'),
       (r.get('content') or [{}])[0].get('text', '')[:80])
    st, j = call(KEY, 'search', {'q': 'CNC'})
    ok('MCP tools/call search', not res_of(j).get('isError') and res_of(j).get('structuredContent', {}).get('count', 0) >= 1, res_of(j).get('content', [{}])[0].get('text', '')[:80])
    st, j = call(KEY, 'get_record', {'entity': 'equipment', 'code': 'CNC1'})
    ok('MCP tools/call get_record', not res_of(j).get('isError'), res_of(j).get('content', [{}])[0].get('text', '')[:80])
    for name, args in (('query_work_orders', {}), ('query_production', {'period': 'today'}), ('query_quality', {'period': '24h'}), ('query_oee', {'period': '24h'}),
                       ('query_equipment', {'equipment': 'CNC1'}), ('query_maintenance', {}), ('query_energy', {}), ('list_points', {'equipment': 'CNC1', 'limit': 5}),
                       ('cycle_analysis', {}), ('trace', {'lot': 'H-NONE'})):
        st, j = call(KEY, name, args)
        ok(f'MCP tools/call {name}', st == 200 and not res_of(j).get('isError'), res_of(j).get('content', [{}])[0].get('text', '')[:100])
    st, j = call(KEY, 'get_record', {'entity': 'equipment', 'code': 'NO-SUCH'})
    ok('MCP tools/call：工具出错 → isError（不是协议错误）', st == 200 and res_of(j).get('isError'), res_of(j).get('content'))
    st, j = call(KEY, 'no_such_tool')
    ok('MCP tools/call：不存在的工具 → -32602', (j or {}).get('error', {}).get('code') == -32602, j)
    st, h, j = mcp(KEY, 'no/such/method')
    ok('MCP：不存在的方法 → -32601', (j or {}).get('error', {}).get('code') == -32601, j)
    st, h, j = mcp(KEY, 'server/discover', {})
    ok('MCP：server/discover → -32601（客户端回退 initialize 握手）', (j or {}).get('error', {}).get('code') == -32601)
    st, h, j = http('POST', MES + '/mcp', b'{not json', {'X-API-Key': KEY})
    ok('MCP：解析错误 → -32700', (j or {}).get('error', {}).get('code') == -32700, j)
    st, h, j = mcp(KEY, 'ping')
    ok('MCP ping', res_of(j) == {}, j)
    st, h, j = mcp(KEY, 'resources/list')
    uris = [x['uri'] for x in res_of(j).get('resources', [])]
    ok('MCP resources/list：工厂模型 / 数据目录 / 指标口径', {'mes://factory-model', 'mes://data-catalog', 'mes://kpi-definitions'} <= set(uris), uris)
    for u in ('mes://factory-model', 'mes://data-catalog', 'mes://kpi-definitions', 'mes://skills', 'mes://equipment/CNC1/points'):
        st, h, j = mcp(KEY, 'resources/read', {'uri': u})
        c = (res_of(j).get('contents') or [{}])[0]
        ok(f'MCP resources/read {u}', c.get('uri') == u and len(c.get('text') or '') > 20, (c.get('text') or '')[:60])
    st, h, j = mcp(KEY, 'resources/read', {'uri': 'mes://nope'})
    ok('MCP resources/read：不存在 → -32002', (j or {}).get('error', {}).get('code') == -32002)
    st, h, j = mcp(KEY, 'resources/templates/list')
    ok('MCP resources/templates/list', res_of(j).get('resourceTemplates'))
    st, h, j = mcp(KEY, 'prompts/list')
    ok('MCP prompts/list', len(res_of(j).get('prompts', [])) >= 3)
    st, h, j = mcp(KEY, 'prompts/get', {'name': 'bottleneck', 'arguments': {'line': '机加区'}})
    ok('MCP prompts/get', '机加区' in json.dumps(res_of(j), ensure_ascii=False))
    st, h, j = mcp(KEY, 'tools/list', headers={'Mcp-Session-Id': 'bogus.session'})
    ok('MCP：不认识的会话 → 404', st == 404, st)
    st, h, j = mcp(KEY, 'tools/list', headers={'MCP-Protocol-Version': '1999-01-01'})
    ok('MCP：不支持的协议版本头 → 400', st == 400, st)
    st, h, j = http('GET', MES + '/mcp', headers={'X-API-Key': KEY})
    ok('MCP：GET → 405（不开推送流）', st == 405, st)
    st, h, j = http('POST', MES + '/mcp', [{'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}], {'X-API-Key': KEY})
    ok('MCP：批量请求', isinstance(j, list) and len(j) == 2, type(j))
    st, h, j = http('POST', MES + '/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, {'X-API-Key': KEY, 'Origin': 'http://evil.example.com'})
    ok('MCP：陌生 Origin → 403（防 DNS 重绑定）', st == 403, st)

    # 审计
    time.sleep(2.5)
    au = api('GET', '/api/admin/audit?q=mcp:&size=200')
    items = au.get('items') or au.get('rows') or []
    me = [x for x in items if str(x.get('user', '')).startswith('apikey:ai50')]
    ok('MCP：每次调用记审计（用户 = 密钥、来源 = 接口）', any(x.get('action') == 'mcp_call' for x in me) and any(x.get('action') == 'mcp_connect' for x in me)
       and all(x.get('source') == 'api' for x in me), f'{len(me)} 条')
    ok('MCP：审计里有工具名、耗时、结果', any((x.get('detail') or {}).get('tool') == 'factory_model' and 'ms' in (x.get('detail') or {}) for x in me))

    # 限流
    api('PUT', '/api/mcp/settings', {'rate_per_min': 3})
    time.sleep(11)          # 设置缓存 10 秒
    rk = new_key('rate')
    codes = [call(rk, 'factory_model')[0] for _ in range(5)]
    ok('MCP：按密钥限流（超了 429）', codes[:3] == [200, 200, 200] and 429 in codes[3:], codes)
    api('PUT', '/api/mcp/settings', {'rate_per_min': 600})

    # 写工具：只起草
    api('PUT', '/api/mcp/settings', {'write_tools': True})
    time.sleep(11)
    wk = new_key('drafts', 'drafts')
    st, h, j = mcp(wk, 'tools/list')
    wt = {t['name']: t for t in res_of(j).get('tools', [])}
    ok('MCP 写工具：打开后 draft_action 出现（非只读）', 'draft_action' in wt and not wt['draft_action']['annotations']['readOnlyHint'])
    st, h, j = mcp(KEY, 'tools/list')
    ok('MCP 写工具：没有 mcp.write 的密钥看不到', 'draft_action' not in {t['name'] for t in res_of(j).get('tools', [])})
    n_rep0 = api('GET', '/api/e/repair_order?size=1')['total']
    st, j = call(wk, 'draft_action', {'op': 'repair_order', 'payload': {'equipment': 'CNC1', 'fault': 'MCP 测试：夹具动作慢', 'level': 'normal'}, 'reason': 'ai50'})
    sc = res_of(j).get('structuredContent', {}).get('data', {})
    ok('MCP 写工具：只生成动作草稿（待审批），没有直接建单', not res_of(j).get('isError') and sc.get('status') == 'pending'
       and api('GET', '/api/e/repair_order?size=1')['total'] == n_rep0, res_of(j).get('content', [{}])[0].get('text', '')[:120])
    if sc.get('draft_id'):
        api('POST', f"/api/ai/drafts/{sc['draft_id']}/decide", {'decision': 'reject', 'reason': 'ai50 测试'})
    api('PUT', '/api/mcp/settings', {'write_tools': False})

    # AI 开关关掉 MCP
    sw = api('GET', '/api/ai/switches')
    api('PUT', '/api/ai/switches', {'features': {'mcp': False}})
    time.sleep(2.5)
    st, h, j = mcp(KEY, 'tools/list')
    ok('MCP：AI 功能"MCP 数据接口"关闭 → 403', st == 403, st)
    api('PUT', '/api/ai/switches', {'features': {'mcp': True}})
    time.sleep(2.5)

    # 官方 SDK 当客户端
    sdk = os.environ.get('MCP_SDK_PATH')
    here = os.path.dirname(os.path.abspath(__file__))
    if sdk:
        env = {**os.environ, 'MCP_SDK_PATH': sdk, 'PYTHONIOENCODING': 'utf-8'}
        p = subprocess.run([sys.executable, os.path.join(here, 'mcp_sdk_client.py'), 'http', MES + '/mcp', KEY], capture_output=True, text=True, encoding='utf-8', env=env, timeout=180)
        try:
            o = json.loads((p.stdout or '').strip().splitlines()[-1])
        except (ValueError, IndexError):
            o = {'ok': False, 'error': (p.stderr or p.stdout)[-400:]}
        ok('官方 MCP SDK（Streamable HTTP）：initialize / tools / resources / prompts / call / ping', o.get('ok') and 'cycle_analysis' in o.get('tools', []) and o.get('call', {}).get('isError') is False,
           {k: o.get(k) for k in ('protocol', 'server', 'error')})
        launcher = os.environ.get('AI50_LAUNCHER')
        if launcher:
            p = subprocess.run([sys.executable, os.path.join(here, 'mcp_sdk_client.py'), 'stdio', os.environ.get('AI50_PY') or sys.executable, launcher, KEY,
                                os.environ.get('AI50_CWD') or os.getcwd()], capture_output=True, text=True, encoding='utf-8', env=env, timeout=240)
            try:
                o = json.loads((p.stdout or '').strip().splitlines()[-1])
            except (ValueError, IndexError):
                o = {'ok': False, 'error': (p.stderr or p.stdout)[-400:]}
            ok('官方 MCP SDK（stdio：python -m honeymom mcp）', o.get('ok') and 'factory_model' in o.get('tools', []), {k: o.get(k) for k in ('protocol', 'error')})
    else:
        print('（没有 MCP_SDK_PATH：跳过官方 SDK 客户端核对）')

# ═════════════════════════ B. 权限和数据范围 ═════════════════════════
if 'scope' not in SKIP:
    print('— 受限密钥：权限点和数据范围 —')
    ls = lines()
    cnc1 = equipment('CNC1')
    other = next((l for l in ls if not cnc1 or l['id'] != cnc1['line']), None)
    if other and cnc1:
        sk = new_key('scoped', scope={'lines': [other['id']]})
        st, j = call(sk, 'search', {'q': 'CNC1'})
        rows = res_of(j).get('structuredContent', {}).get('rows', [])
        ok('范围：搜不到范围外产线的设备', not any(r['entity'] == 'equipment' and r['code'] == 'CNC1' for r in rows), [r['code'] for r in rows][:6])
        st, j = call(sk, 'get_record', {'entity': 'equipment', 'code': 'CNC1'})
        ok('范围：读不到范围外的记录', res_of(j).get('isError'))
        st, j = call(sk, 'query_equipment', {'equipment': 'CNC1'})
        ok('范围：查不到范围外设备的履历', res_of(j).get('isError'), res_of(j).get('content', [{}])[0].get('text', '')[:80])
        st, j = call(sk, 'ts_latest', {'equipment': 'CNC1'})
        ok('范围：读不到范围外设备的时序', res_of(j).get('isError'))
        st, j = call(sk, 'cycle_analysis', {'equipment': 'CNC1'})
        ok('范围：节拍分析也受范围限制', res_of(j).get('isError'))
        st, j = call(sk, 'factory_model')
        tree = json.dumps(res_of(j).get('structuredContent', {}), ensure_ascii=False)
        ok('范围：工厂模型只含范围内的产线', other['name'] in tree and '"code": "CNC1"' not in tree)
        st, j = call(sk, 'query_sql', {'sql': "SELECT code FROM equipment WHERE code = 'CNC1'"})
        ok('范围：只读 SQL 也按范围过滤', not res_of(j).get('isError') and res_of(j).get('structuredContent', {}).get('rows') == [], res_of(j).get('structuredContent', {}).get('rows'))
    pk = new_key('noperm', perms=['mcp.connect', 'quality.*.read'])
    st, h, j = mcp(pk, 'tools/list')
    names = {t['name'] for t in res_of(j).get('tools', [])}
    ok('权限：只给质量查看的密钥看不到时序 / 节拍 / 设备工具', 'ts_range' not in names and 'cycle_analysis' not in names and 'query_equipment' not in names and 'query_quality' in names, sorted(names)[:12])
    st, j = call(pk, 'ts_latest', {'equipment': 'CNC1'})
    ok('权限：调没有权限的工具 → 当作不存在', (j or {}).get('error', {}).get('code') == -32602)

# ═════════════════════════ C. 合成信号：切循环 / 动作的准确度、漂移 ═════════════════════════
SYN = {}
if 'synth' not in SKIP:
    print('— 合成信号（已知真值）—')
    ws = api('GET', '/api/e/workshop?size=5')['items'][0]
    tag = datetime.now().strftime('%H%M%S')
    lid = api('POST', '/api/e/line', {'code': f'SYN-L{tag}', 'name': f'合成测试线{tag}', 'workshop': ws['id']})['id']
    wkey = api('POST', '/api/admin/apikeys', {'name': f'gw{tag}', 'perms': ['equipment.ts.write', 'equipment.ts.read']})['key']
    rnd = random.Random(42)
    ACTS = [('door_close', 'DoorCloseCmd', 1, 'DoorClosed', 1, 3.2, ('DoorOpened', 0)), ('clamp_extend', 'ClampValve', 1, 'ClampExtended', 1, 1.1, ('ClampRetracted', 0)),
            ('spindle_run', 'SpindleRun', 1, 'SpindleRun', 0, 40.0, None), ('clamp_retract', 'ClampValve', 0, 'ClampRetracted', 1, 0.9, ('ClampExtended', 0)),
            ('door_open', 'DoorCloseCmd', 0, 'DoorOpened', 1, 3.0, ('DoorClosed', 0))]

    def ts_(t):
        return t.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

    def gen(src, start, n, period, step_mode=False, clamp_fn=None):
        rows, truth = [], []
        init = {'DoorCloseCmd': False, 'DoorClosed': False, 'DoorOpened': True, 'ClampValve': False, 'ClampExtended': False, 'ClampRetracted': True, 'SpindleRun': False}
        if not step_mode:
            init['CycleOn'] = False
        for k, v in init.items():
            rows.append([f'{src}/Actions/{k}', ts_(start - timedelta(seconds=5)), v])
        if step_mode:
            rows.append([f'{src}/Actions/ProgramStep', ts_(start - timedelta(seconds=5)), 0])
        t = start
        for i in range(n):
            cs = t
            tt = {}
            cur = cs
            if not step_mode:
                rows.append([f'{src}/Actions/CycleOn', ts_(cs), True])
            else:
                rows.append([f'{src}/Actions/ProgramStep', ts_(cs), 10])
            for j, (key, cp, cv, ep, ev, nom, leave) in enumerate(ACTS):
                d = nom * (1 + rnd.gauss(0, 0.03))
                if key == 'clamp_extend' and clamp_fn:
                    d = clamp_fn(i) * (1 + rnd.gauss(0, 0.02))
                d = round(d, 3)
                t1 = cur.replace(microsecond=(cur.microsecond // 1000) * 1000)
                rows.append([f'{src}/Actions/{cp}', ts_(t1), bool(cv)])
                if leave:
                    rows.append([f'{src}/Actions/{leave[0]}', ts_(t1 + timedelta(seconds=0.1)), bool(leave[1])])
                t2 = t1 + timedelta(seconds=d)
                rows.append([f'{src}/Actions/{ep}', ts_(t2), bool(ev)])
                tt[key] = (round((t1 - cs).total_seconds(), 3), d)
                if step_mode and key == 'spindle_run':
                    for k2, sn in enumerate((20, 30, 40, 50, 60)):
                        rows.append([f'{src}/Actions/ProgramStep', ts_(t1 + timedelta(seconds=d * (k2 + 1) / 6)), sn])
                cur = t2 + timedelta(seconds=0.15 + (0.001 * rnd.randint(0, 40)))
            ce = cur
            if not step_mode:
                rows.append([f'{src}/Actions/CycleOn', ts_(ce), False])
            else:
                rows.append([f'{src}/Actions/ProgramStep', ts_(ce), 0])
            truth.append({'start': ts_(cs), 'dur': round((ce - cs).total_seconds(), 3), 'acts': tt})
            t = cs + timedelta(seconds=period * (1 + rnd.uniform(-0.05, 0.05)))
        return rows, truth

    def push(rows):
        for i in range(0, len(rows), 20000):
            st, _h, j = http('POST', MES + '/api/ts/write', {'rows': rows[i:i + 20000]}, {'X-API-Key': wkey})
            if st >= 400:
                raise RuntimeError(f'ts/write {st} {j}')

    def mk_eq(code):
        return api('POST', '/api/e/equipment', {'code': code, 'name': f'合成 {code}', 'etype': 'cnc', 'line': lid, 'source': f'SYN:{code}'})['id']

    base = (datetime.now() - timedelta(days=9)).replace(hour=8, minute=0, second=0, microsecond=0)
    c1, c2 = f'SY1{tag}', f'SY2{tag}'
    e1, e2 = mk_eq(c1), mk_eq(c2)
    r1, truth1 = gen(f'SYN:{c1}', base, 60, 70)
    r2, truth2 = gen(f'SYN:{c2}', base, 40, 75, step_mode=True)
    push(r1)
    push(r2)
    end1 = datetime.strptime(truth1[-1]['start'], '%Y-%m-%d %H:%M:%S.%f') + timedelta(seconds=120)
    end2 = datetime.strptime(truth2[-1]['start'], '%Y-%m-%d %H:%M:%S.%f') + timedelta(seconds=120)
    for code, eid, end, truth, mode in ((c1, e1, end1, truth1, 'signal'), (c2, e2, end2, truth2, 'step')):
        p = api('POST', f'/api/cycle/models/{eid}/propose', {'hours': 2, 'end': end.strftime('%Y-%m-%d %H:%M:%S'), 'save': True})
        acts = {a['key']: a for a in p['model']['actions']}
        ok(f'自动识别动作（{mode} 模式）：循环信号和 5 个动作都认出来', p['model']['cycle']['mode'] == mode and set(acts) == {a[0] for a in ACTS}, sorted(acts))
        ok(f'自动识别动作（{mode}）：标准时间 ≈ 真值中位数（±0.06 s）', all(abs(acts[k]['std_s'] - nom) <= 0.06 * max(1, nom / 3) for k, _c, _cv, _e, _ev, nom, _l in ACTS if k in acts),
           {k: acts[k]['std_s'] for k in acts})
        ok(f'自动识别的模型是草稿，要确认', (p.get('saved') or {}).get('status') == 'draft')
        api('POST', f'/api/cycle/models/{eid}/confirm')
        s0 = (datetime.strptime(truth[0]['start'], '%Y-%m-%d %H:%M:%S.%f') - timedelta(seconds=30)).strftime('%Y-%m-%d %H:%M:%S')
        rep = api('GET', f"/api/cycle/station/{eid}?start={urllib.parse.quote(s0)}&end={urllib.parse.quote(end.strftime('%Y-%m-%d %H:%M:%S'))}&cycles=200&days=0")
        got = {c['ts']: c for c in rep['recent']}
        ok(f'切循环（{mode}）：循环数 = 真值', rep['cycles_n'] == len(truth), f"{rep['cycles_n']} / {len(truth)}")
        errs, errc, miss = [], [], 0
        for tr in truth:
            g = got.get(tr['start'])
            if not g:
                miss += 1
                continue
            errc.append(abs(g['dur'] - tr['dur']))
            ga = {a['key']: a for a in g['acts']}
            for k, (off, d) in tr['acts'].items():
                if k not in ga:
                    miss += 1
                    continue
                errs.append(abs(ga[k]['dur'] - d))
        mx = max(errs) if errs else 9
        SYN[mode] = {'cycles': rep['cycles_n'], 'truth': len(truth), 'actions': len(errs), 'max_err_ms': round(mx * 1000, 2), 'cycle_err_ms': round(max(errc or [9]) * 1000, 2), 'missing': miss}
        ok(f'切动作（{mode}）：每个动作用时误差 ≤ 2 ms、没有漏', mx <= 0.002 and miss == 0, SYN[mode])
        ok(f'切循环（{mode}）：循环用时误差 ≤ 2 ms', max(errc or [9]) <= 0.002)
    # 漂移：7 天，夹具伸出 0.6 → 1.4 s
    c3 = f'SY3{tag}'
    e3 = mk_eq(c3)
    allrows, n_day = [], 30
    d0 = (datetime.now() - timedelta(days=8)).replace(hour=6, minute=0, second=0, microsecond=0)
    for day in range(7):
        rows, _t = gen(f'SYN:{c3}', d0 + timedelta(days=day), n_day, 80, clamp_fn=lambda i, day=day: 0.6 + 0.8 * day / 6)
        allrows += rows if day == 0 else [r for r in rows if r[1] >= (d0 + timedelta(days=day)).strftime('%Y-%m-%d %H:%M:%S')]
    push(allrows)
    end3 = d0 + timedelta(days=6, hours=2)
    p = api('POST', f'/api/cycle/models/{e3}/propose', {'hours': 2, 'end': (d0 + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S'), 'save': True})
    api('POST', f'/api/cycle/models/{e3}/confirm')
    api('POST', f'/api/cycle/models/{e3}/baseline', {'start': d0.strftime('%Y-%m-%d %H:%M:%S'), 'end': (d0 + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')})
    rep = api('GET', f"/api/cycle/station/{e3}?start={urllib.parse.quote((end3 - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S'))}&end={urllib.parse.quote(end3.strftime('%Y-%m-%d %H:%M:%S'))}&days=7&cycles=0")
    tr = (rep.get('trends') or {}).get('clamp_extend') or {}
    ok('漂移：识别出 7 天变坏的趋势', tr.get('worse') and tr.get('days') == 7, tr)
    ok('漂移：说法"7 天内从 0.6s 增至 1.4s"', '7 天内从 0.6' in (tr.get('text') or '') and '增至 1.4' in (tr.get('text') or ''), tr.get('text'))
    ok('漂移：每天 +0.13 s 左右', abs((tr.get('slope') or 0) - 0.8 / 6) < 0.03, tr.get('slope'))
    ok('漂移：告警里有漂移和慢动作', any(a['kind'] == 'drift' and a['action'] == 'clamp_extend' for a in rep['alerts']) and any(a['kind'] == 'slow' for a in rep['alerts']),
       [a['text'] for a in rep['alerts']][:3])
    SYN['drift'] = tr.get('text')

# ═════════════════════════ D. 技能 ═════════════════════════
if 'skills' not in SKIP:
    print('— 保存为技能 —')
    sk = api('GET', '/api/skills')['items']
    keys = {s.get('key') for s in sk}
    ok('内置 6 个技能', {'shift_summary', 'bottleneck_action', 'oee_loss_top5', 'quality_pareto', 'overdue_maint', 'energy_anomaly'} <= keys, sorted(k for k in keys if k))
    for s in [x for x in sk if x.get('key')]:
        r = api('POST', f"/api/skills/{s['id']}/run", {})
        ok(f"内置技能运行：{s['name']}", r['status'] == 'ok' and r['llm_calls'] == 0, r.get('error') or f"{len(r.get('blocks') or [])} 块")
    # 从一条助手回答保存
    q = '机加区最近 8 小时的瓶颈工位和瓶颈动作是什么？损失多少？'
    st, _h, raw = http('POST', MES + '/api/assistant/chat', {'text': q, 'page': '/cycle'}, {'Authorization': 'Bearer ' + TOKEN}, raw=True)
    evs = [ln[6:] for ln in raw.decode('utf-8').split('\n') if ln.startswith('data: ')]
    done = [json.loads(x) for x in evs if '"message_id"' in x]
    mid = done[-1]['message_id'] if done else None
    ok('助手回答（演示模式）有消息 id', mid, raw[-300:])
    sug = api('POST', '/api/skills/from_message', {'message_id': mid})
    steps = sug['plan']['steps']
    ok('从回答还原查询计划（实际调用的工具）', steps and all(s['tool'] for s in steps), [s['tool'] for s in steps])
    pk = {p['key'] for p in sug['params']}
    ok('AI / 规则建议把字面量变成参数', pk, sorted(pk))
    ok('输出格式：记下用了哪些块', sug['output']['blocks'], sug['output'])
    sug['name'] = 'ai50 瓶颈技能'
    sug['schedule'] = {'mode': 'hourly', 'enabled': True}
    sug['targets'] = {'feed': True, 'daily': True}
    sug['share_roles'] = ['engineer']
    s = api('POST', '/api/skills', sug)
    ok('保存技能：版本 1、负责人、下次运行时间', s['version'] == 1 and s['owner'] == 'admin' and s['next_run_at'], {k: s.get(k) for k in ('version', 'owner', 'next_run_at')})
    params = {p['key']: (p['default'] if p['type'] != 'period' else 'today') for p in s['params']}
    r = api('POST', f"/api/skills/{s['id']}/run", {'params': params})
    ok('手动运行（改参数）：成功、有结果、不调用大模型', r['status'] == 'ok' and (r['blocks'] or r.get('summary')) and r['llm_calls'] == 0, r.get('error') or [b['type'] for b in r['blocks']])
    n0 = api('GET', '/api/skills/llm_calls')['n']
    tk = api('POST', '/api/skills/tick', {'force_ids': [s['id']]})['runs']
    run = next((x for x in tk if x.get('skill') == s['id']), {})
    ok('定时运行：重放保存的查询计划', run.get('status') == 'ok' and run.get('trigger') == 'schedule', run)
    ok('定时运行：大模型调用 0 次（AI 结论关着）', run.get('llm_calls') == 0 and api('GET', '/api/skills/llm_calls')['n'] == n0, f"before {n0} after {api('GET', '/api/skills/llm_calls')['n']}")
    feed = api('GET', '/api/home/feed?limit=60')
    ok('定时结果进值守信息流', any(str(x.get('id', '')).startswith('skill:') for x in feed['items']), [x['id'] for x in feed['items']][:8])
    rep = api('GET', '/api/report/daily?day=' + datetime.now().strftime('%Y-%m-%d'))
    ok('定时结果进生产日报（技能一节）', isinstance(rep.get('skills'), list), len(rep.get('skills') or []))
    # 版本
    s2 = api('PUT', f"/api/skills/{s['id']}", {'description': 'ai50 改了说明', 'schedule': {'mode': 'cron', 'cron': '*/30 * * * *'}})
    ok('修改技能：版本 +1、cron 下次运行', s2['version'] == 2 and s2['next_run_at'] and len(api('GET', f"/api/skills/{s['id']}")['versions']) >= 2, s2['next_run_at'])
    st, _h, j = http('PUT', MES + f"/api/skills/{s['id']}", {'schedule': {'mode': 'cron', 'cron': 'bad cron'}}, {'Authorization': 'Bearer ' + TOKEN})
    ok('cron 表达式不对 → 400', st == 400)
    # 共享：engineer 能看到、operator 看不到
    for u, pw in (('engineer', 'honey123'), ('operator', 'honey123')):
        st, _h, j = http('POST', MES + '/api/login', {'username': u, 'password': pw})
        if st == 200:
            t2 = j['token']
            mine = [x['id'] for x in api('GET', '/api/skills', tok=t2)['items']]
            ok(f'按角色共享：{u} {"能" if u == "engineer" else "不能"}看到', (s['id'] in mine) == (u == 'engineer'))
    # 失效检测
    bad = api('POST', '/api/skills', {'name': 'ai50 会失效的技能', 'plan': {'steps': [{'tool': 'ts_latest', 'args': {'points': ['NOPE:GONE/Actions/X']}}]}, 'params': []})
    r = api('POST', f"/api/skills/{bad['id']}/run", {})
    b2 = api('GET', f"/api/skills/{bad['id']}")
    ok('点位改了 → 运行标失效、技能标失效', r['status'] == 'broken' and b2['status'] == 'broken' and b2['broken_reason'], r.get('error'))
    notes = api('GET', '/api/e/notice?size=20')['items']
    ok('失效 → 给负责人发站内信', any('ai50 会失效的技能' in (n.get('title') or '') and n.get('to_user') == 'admin' for n in notes))
    tk = api('POST', '/api/skills/tick', {})['runs']
    ok('失效的技能不再定时运行', not any(x.get('skill') == bad['id'] for x in tk))
    b3 = api('PUT', f"/api/skills/{bad['id']}", {'plan': {'steps': [{'tool': 'query_maintenance', 'args': {}}]}})
    ok('修改后恢复可用', b3['status'] == 'active' and api('POST', f"/api/skills/{bad['id']}/run", {})['status'] == 'ok')
    bad2 = api('POST', '/api/skills', {'name': 'ai50 SQL 失效', 'plan': {'steps': [{'tool': 'query_sql', 'args': {'sql': 'SELECT no_such_column FROM equipment'}}]}})
    ok('表结构对不上（SQL 列不存在）→ 失效', api('POST', f"/api/skills/{bad2['id']}/run", {})['status'] == 'broken')
    # 演示回放：AI 结论
    bn = next(x for x in sk if x.get('key') == 'bottleneck_action')
    r = api('POST', f"/api/skills/{bn['id']}/run", {'narrative': True})
    ok('演示模式：AI 结论回放录制（不花 token）', r['status'] == 'ok' and r['by'] in ('demo', 'none') and r['llm_calls'] == 0 and r['cost'] == 0, f"by={r['by']} {r['narrative'][:60]}")
    # MCP 工具
    st, j = call(KEY, f"skill_{s['id']}", params)
    ok('技能作为 MCP 工具运行', not res_of(j).get('isError'), res_of(j).get('content', [{}])[0].get('text', '')[:80])
    st, j = call(KEY, 'run_skill', {'skill': 'overdue_maint'})
    ok('run_skill 工具按 key 运行内置技能', not res_of(j).get('isError'))
    # 自动化规则可以运行技能
    au = api('POST', '/api/ai/automations', {'flow': {'name': 'ai50 报警跑技能', 'trigger': {'type': 'alarm', 'match': '主轴', 'equipment': '*'},
                                                     'actions': [{'type': 'skill', 'skill_id': bn['id']}]}, 'status': 'paused'})
    flow = au.get('flow') or {}
    ok('自动化规则的动作可以是"运行技能"', any(a.get('type') == 'skill' and a.get('skill_id') == bn['id'] for a in flow.get('actions', [])), flow.get('actions'))

# ═════════════════════════ E. 虚拟工厂：气缸漏气端到端 ═════════════════════════
VFRES = {}
if VFH and 'vf' not in SKIP:
    print('— 虚拟工厂：注入"气缸漏气"→ 找到工位和动作、算损失 —')
    VF = VFH + '/api/p/foundry-housing'
    st, _h, j = http('POST', VFH + '/api/login', {'username': 'admin', 'password': 'honey123'})
    vt = j['token']
    vtk = http('POST', VFH + '/api/tokens', {'name': 'ai50'}, {'Authorization': 'Bearer ' + vt})[2]['token']
    VH = {'Authorization': 'Bearer ' + vtk}
    EPS = {k: v for k, v in (('mqtt', os.environ.get('VF_MQTT')), ('opcua', os.environ.get('VF_OPCUA')), ('modbus', os.environ.get('VF_MODBUS'))) if v}
    st, _h, j = http('POST', MES + '/api/onboard/package', {'url': VF + '/delivery.zip', 'token': vtk, 'confirm': True, 'use_package_endpoints': True, 'endpoints': EPS},
                     {'Authorization': 'Bearer ' + TOKEN}, timeout=600)
    if st >= 400:            # 老的接入方式（M2 之前）
        ds = api('GET', '/api/e/datasource?f_code=VF')['items'][0]
        st, _h, j = http('POST', MES + f"/api/e/datasource/{ds['id']}/do/import_vf", {'url': VF, 'token': vtk}, {'Authorization': 'Bearer ' + TOKEN})
    ok('导入虚拟工厂交付包（点表里动作信号是 action / step）', st < 400, (j or {}).get('summary', {}).get('points') if isinstance(j, dict) else j)
    for d in api('GET', '/api/e/datasource?size=50')['items']:
        if not d['enabled']:
            api('PUT', f"/api/e/datasource/{d['id']}", {'enabled': True})
    http('POST', VF + '/station/CNC1/fault', {'kind': 'clear'}, VH)
    http('POST', VF + '/sim', {'speed': 120, 'paused': False}, VH)

    def cycles_of(code):
        it = api('GET', '/api/ts/points?q=' + urllib.parse.quote(f'{code}/Actions/CycleCount'))['items']
        return max([p['last_value'] or 0 for p in it] or [0])

    c0 = cycles_of('CNC1')
    t0 = time.time()
    while cycles_of('CNC1') - c0 < 10 and time.time() - t0 < 600:
        time.sleep(5)
    ok('采集到 CNC1 的动作信号（10 个循环）', cycles_of('CNC1') - c0 >= 10, f'{cycles_of("CNC1") - c0} 个循环 / {time.time() - t0:.0f} 秒')
    line_id = None
    for code in ('CNC1', 'CNC2', 'CNC3', 'CNC4', 'CNC5', 'CNC6'):
        e = equipment(code)
        line_id = e['line']
        p = api('POST', f"/api/cycle/models/{e['id']}/propose", {'hours': 1.2, 'replace': True})
        if code == 'CNC1':
            acts = {a['key']: a for a in p['model']['actions']}
            ok('VF：自动识别 CNC 动作（门 / 夹紧 / 主轴 / 松开 / 开门）', {'door_close', 'clamp_extend', 'spindle_run', 'clamp_retract', 'door_open'} <= set(acts), sorted(acts))
            ok('VF：夹紧标准时间 ≈ 1.1 s（虚拟工厂的标准用时）', abs(acts['clamp_extend']['std_s'] - 1.1) <= 0.1, acts['clamp_extend']['std_s'])
        api('POST', f"/api/cycle/models/{e['id']}/confirm")
        api('POST', f"/api/cycle/models/{e['id']}/baseline", {'hours': 1.2})
    clock_inject = api('GET', '/api/clock')['now']
    EXTRA = 3.0
    st, _h, j = http('POST', VF + '/station/CNC1/fault', {'kind': 'cyl_leak', 'params': {'cyl': 'clamp', 'extra_s': EXTRA}}, VH)
    ok('VF：注入 CNC1 夹具气缸漏气（伸出慢 3 s）', st == 200, j)
    c1 = cycles_of('CNC1')
    t0 = time.time()
    while cycles_of('CNC1') - c1 < 12 and time.time() - t0 < 600:
        time.sleep(5)
    s_from = (datetime.strptime(clock_inject, '%Y-%m-%d %H:%M:%S') + timedelta(seconds=30)).strftime('%Y-%m-%d %H:%M:%S')
    rep = api('GET', f'/api/cycle/line?line={line_id}&start={urllib.parse.quote(s_from)}')
    bn = rep['bottleneck'] or {}
    ok('VF：瓶颈工位 = CNC1', (bn.get('equipment') or {}).get('code') == 'CNC1', (bn.get('equipment') or {}).get('code'))
    ok('VF：瓶颈动作 = 夹具气缸伸出', (bn.get('action') or {}).get('key') == 'clamp_extend', (bn.get('action') or {}).get('name'))
    top = (rep['losses'] or [{}])[0]
    ok('VF：损失排行第一 = CNC1 夹具伸出', top.get('equipment') == 'CNC1' and top.get('key') == 'clamp_extend', {k: top.get(k) for k in ('equipment', 'name', 'excess_s')})
    st1 = api('GET', f"/api/cycle/station/{equipment('CNC1')['id']}?start={urllib.parse.quote(s_from)}&days=0&cycles=0")
    ca = next(a for a in st1['actions'] if a['key'] == 'clamp_extend')
    ok('VF：每循环慢的秒数 ≈ 注入的 3 s（对基线，±0.25 s）', abs((ca['excess_base'] or 0) - EXTRA) <= 0.25, f"对基线 +{ca['excess_base']} s，对标准 +{ca['excess_std']} s")
    L = top
    per_day = L.get('cycles_per_day') or 0
    ok('VF：损失折算自洽（秒/天 = 每循环慢 × 每天循环数）', per_day > 0 and abs(L['lost_s_day'] - L['excess_s'] * per_day) <= 0.02 * L['lost_s_day'] + 1, L)
    interval = st1['interval']['median']
    exp_parts = per_day * L['excess_s'] / interval
    ok('VF：少做的件数 = 每天循环数 × 慢的秒数 ÷ 循环间隔（±2%）', abs(L['parts_day'] - exp_parts) <= 0.02 * exp_parts + 0.02, f"{L['parts_day']} 件/天（期望 {exp_parts:.2f}），{L['money_year']} 元/年")
    others = [x for x in rep['stations'] if x['equipment']['code'] != 'CNC1']
    ok('VF：其它 CNC 没有被误报', not any(x['top_action'] and (x['top_action'].get('excess') or 0) > 0.5 for x in others), [(x['equipment']['code'], x['top_action']) for x in others if x['top_action']])
    ok('VF：CNC1 有慢动作告警', any(a['action'] == 'clamp_extend' and a['kind'] == 'slow' for a in st1['alerts']), [a['text'] for a in st1['alerts']])
    api('POST', '/api/ai/watch/scan')
    fs = api('GET', '/api/ai/watch/findings?status=all&limit=100')['items']
    f = next((x for x in fs if x['key'] == 'equipment.cycle_drift:CNC1:clamp_extend'), None)
    ok('VF：节拍值守发现进工作台信息流', f and f['status'] in ('open', 'acked'), f and f['title'])
    feed = api('GET', '/api/home/feed?limit=80')
    ok('VF：信息流里有这条发现', any('CNC1' in (x.get('title') or '') and '夹具' in (x.get('title') or '') for x in feed['items']))
    ex = api('POST', '/api/cycle/explain', {'equipment': equipment('CNC1')['id'], 'action': 'clamp_extend', 'start': s_from})
    ok('VF：AI 解释为什么慢（演示回放或规则）', ex['text'] and ex['by'] in ('demo', 'rule', 'ai') and ('漏气' in ex['text'] or '气' in ex['text']), f"{ex['by']}: {ex['text'][:80]}")
    n_rep0 = api('GET', '/api/e/repair_order?size=1')['total']
    dr = api('POST', '/api/cycle/draft', {'equipment': equipment('CNC1')['id'], 'action': 'clamp_extend', 'start': s_from})
    ok('VF：起草维修工单（草稿，待审批，没有直接建单）', dr['status'] == 'pending' and dr['op'] == 'repair_order' and 'CNC1' in json.dumps(dr['payload'], ensure_ascii=False)
       and api('GET', '/api/e/repair_order?size=1')['total'] == n_rep0, dr['title'])
    st, j = call(KEY, 'cycle_analysis', {'line': str(line_id), 'start': s_from})
    txt = res_of(j).get('content', [{}])[0].get('text', '')
    ok('VF：外部 AI 通过 MCP 问到同一个结论', 'CNC1' in txt and '夹具气缸伸出' in txt, txt[:160])
    VFRES = {'excess_base': ca['excess_base'], 'excess_std': ca['excess_std'], 'median': ca['median'], 'baseline': ca['baseline'], 'parts_day': L['parts_day'],
             'money_year': L['money_year'], 'lost_s_day': L['lost_s_day'], 'cycles_per_day': per_day}
    api('POST', f"/api/ai/drafts/{dr['id']}/decide", {'decision': 'reject', 'reason': 'ai50 测试'})
    http('POST', VF + '/station/CNC1/fault', {'kind': 'clear'}, VH)
    http('POST', VF + '/sim', {'speed': 10}, VH)

# ═════════════════════════ 收尾 ═════════════════════════
LLM1 = api('GET', '/api/skills/llm_calls')['n']
ok('整个检查（演示模式）没有调用大模型', LLM1 == LLM0, f'{LLM0} → {LLM1}')
api('PUT', '/api/ai/switches', {k: SW0[k] for k in ('global', 'demo', 'record')} | {'features': SW0['features']})
print('合成信号：', json.dumps(SYN, ensure_ascii=False))
print('虚拟工厂：', json.dumps(VFRES, ensure_ascii=False))
n_ok = sum(1 for _, x in results if x)
print(f'SUMMARY {n_ok}/{len(results)}')
sys.exit(0 if n_ok == len(results) else 1)
