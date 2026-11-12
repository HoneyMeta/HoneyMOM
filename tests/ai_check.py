"""AI 体验（4.7）后端检查：块协议、动作草稿与审批、一句话自动化（回放 / 启用）、AI 值守、日报（含 PDF）、跨工段 AI 解读、钉到工作台、AI 关闭时的表现。

先启动 MES（最好先跑过 tests/e2e.py 有数据），再运行：python tests/ai_check.py
环境变量 MES_URL（默认 http://127.0.0.1:8900）。测试用演示模式（不花 token）和全关两种开关，结束后恢复原来的开关。
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
MES = os.environ.get('MES_URL', 'http://127.0.0.1:8900').rstrip('/')
TOKEN = ''
results = []


def req(method, p, body=None, raw=False, timeout=240):
    r = urllib.request.Request(MES + p, method=method, data=json.dumps(body).encode() if body is not None else None,
                               headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + TOKEN} if TOKEN else {})})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as f:
            data = f.read()
            return (f.status, data) if raw else json.loads(data)
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            j = json.loads(data or b'{}')
        except ValueError:
            j = {'detail': data[:200].decode('utf-8', 'replace')}
        j['_status'] = e.code
        return (e.code, data) if raw else j


def get(p): return req('GET', p)
def post(p, b=None): return req('POST', p, b if b is not None else {})


def ok(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name, str(detail)[:220])


def sse(p, body):
    r = urllib.request.Request(MES + p, method='POST', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + TOKEN})
    evs = []
    with urllib.request.urlopen(r, timeout=400) as f:
        buf = f.read().decode('utf-8')
    for chunk in buf.split('\n\n'):
        ev, data = 'message', ''
        for line in chunk.split('\n'):
            if line.startswith('event:'):
                ev = line[6:].strip()
            elif line.startswith('data:'):
                data += line[5:].strip()
        if data:
            evs.append((ev, json.loads(data)))
    return evs


# ---------------- 0. 纯函数：块协议解析和数字核对 ----------------
try:
    from honeymom.services import ai_blocks
    text, blocks = ai_blocks.split_answer('结论：CNC3 最低。\n```blocks\n[{"type":"chart","kind":"bar","title":"OEE","x":["CNC1","CNC3"],"series":[{"name":"OEE","data":[0.82,0.61]}],"format":"pct"},'
                                          '{"type":"kpis","items":[{"label":"废品率","value":"2.3%","delta":-0.004,"good":"down"}]},{"type":"bogus"}]\n```\n')
    ok('块协议：解析 ```blocks``` 代码块', text == '结论：CNC3 最低。' and [b['type'] for b in blocks] == ['chart', 'kpis'] and blocks[1]['items'][0]['value'] == 0.023, blocks)
    _, legacy = ai_blocks.split_answer('看图\n```chart\n{"type":"line","title":"产量","x":["8","9"],"series":[{"name":"机加","data":[52,61]}]}\n```')
    ok('块协议：兼容旧的 ```chart``` 代码块', legacy and legacy[0]['kind'] == 'line', legacy)
    bad = ai_blocks.check_numbers([{'type': 'chart', 'series': [{'data': [0.82, 0.61, 377]}]}], '{"rows": [[0.82], [0.61]]}')
    ok('数字核对：查询结果里没有的数字标"未核实"', bad == ['377'], bad)
except Exception as ex:      # 不在仓库根目录运行时跳过
    ok('块协议纯函数（需要在仓库里运行）', False, ex)

TOKEN = req('POST', '/api/login', {'username': 'admin', 'password': 'honey123'})['token']
SW0 = get('/api/ai/switches')
FEATS = list(SW0['features'])
ok('新功能开关已登记（backtrace / coplan）', {'backtrace', 'coplan'} <= set(FEATS), FEATS)


def set_ai(mode):
    req('PUT', '/api/ai/switches', {'global': mode != 'off', 'features': {k: mode != 'off' for k in FEATS}, 'demo': mode == 'demo', 'record': False})


try:
    # ---------------- 1. AI 全关 ----------------
    set_ai('off')
    offs = {'草稿': get('/api/ai/drafts'), '自动化': get('/api/ai/automations'), '值守': get('/api/ai/watch/findings'),
            '回溯解读': post('/api/ai/backtrace/explain', {}), '排产建议': post('/api/ai/coplan/advise', {}), 'AI 日报': post('/api/ai/daily', {}),
            '自动化解析': post('/api/ai/automations/parse', {'text': '主轴负载连续 10 分钟大于 90% 提醒班长'})}
    ok('AI 关闭：AI 接口统一返回 403 ai_disabled', all(v.get('_status') == 403 and v.get('code') == 'ai_disabled' for v in offs.values()),
       {k: v.get('_status') for k, v in offs.items()})
    rep = get('/api/report/daily')
    ok('AI 关闭：日报照常（规则版亮点 / 风险）', rep.get('by') == 'rule' and len(rep.get('kpis') or []) == 8 and rep.get('headline') and rep.get('highlights'), rep.get('headline'))
    g = post('/api/report/daily/generate', {})
    ok('AI 关闭：生成并保存规则版日报', g.get('by') == 'rule' and g['report']['charts']['output']['x'], g.get('by'))
    feed = get('/api/home/feed')
    ok('AI 关闭：工作台信息流没有 AI 值守', 'ai_watch' not in (feed.get('providers') or []) and not (feed.get('counts') or {}).get('ai'), feed.get('providers'))
    ok('AI 关闭：钉住的块照常可读', isinstance(get('/api/ai/pins'), list))

    # ---------------- 2. 演示模式 ----------------
    set_ai('demo')
    # 2.1 右侧 AI 面板：结构化块
    evs = sse('/api/assistant/chat', {'text': '最近 8 小时各设备 OEE 对比，哪台最低？', 'page': '/', 'context': []})
    kinds = [e for e, _ in evs]
    blk = next((d for e, d in evs if e == 'blocks'), {}) or {}
    types = [b['type'] for b in blk.get('blocks') or []]
    ok('AI 面板：回答带结构化块（blocks 事件）', 'answer' in kinds and 'blocks' in kinds and 'done' in kinds, kinds)
    ok('AI 面板：块里有图表 / 指标 + 数据来源', ({'chart', 'kpis', 'table'} & set(types)) and 'sources' in types, types)
    # 2.2 动作草稿：驳回 → 什么都不做；通过 → 建单；改一下再通过；数据被改过 → 409
    eq = get('/api/e/equipment?size=100')['items']
    cnc = next(e for e in eq if e['code'].startswith('CNC'))
    n0 = get('/api/e/repair_order?size=1')['total']
    d1 = post('/api/ai/drafts', {'op': 'repair_order', 'payload': {'equipment': cnc['code'], 'fault': 'AI 检查：主轴异响', 'level': 'normal'}, 'source': 'assistant', 'summary': '测试'})
    ok('起草：维修工单草稿（待审批、带改动对比）', d1.get('status') == 'pending' and d1.get('mode') == 'create' and any(c['key'] == 'fault' for c in d1.get('changes') or []), d1.get('title'))
    r1 = post(f"/api/ai/drafts/{d1['id']}/decide", {'decision': 'reject', 'reason': '误报'})
    ok('驳回草稿：没有建单', r1.get('status') == 'rejected' and get('/api/e/repair_order?size=1')['total'] == n0, r1.get('status'))
    again = post(f"/api/ai/drafts/{d1['id']}/decide", {'decision': 'approve'})
    ok('已处理的草稿不能再通过', again.get('_status') == 400, again.get('detail'))
    d2 = post('/api/ai/drafts', {'op': 'repair_order', 'payload': {'equipment': cnc['code'], 'fault': 'AI 检查：主轴负载偏高', 'level': 'normal'}})
    r2 = post(f"/api/ai/drafts/{d2['id']}/decide", {'decision': 'approve'})
    ok('通过草稿：维修工单已创建（回执带链接）', r2.get('status') == 'approved' and r2.get('receipt', {}).get('entity') == 'repair_order'
       and get('/api/e/repair_order?size=1')['total'] == n0 + 1, (r2.get('receipt') or {}).get('text'))
    aud = get(f"/api/ai/drafts/{d2['id']}")
    ok('审计日志：起草 + 通过', [a['kind'] for a in aud.get('audit') or []] == ['ai_draft_create', 'ai_draft_approve'], [a['kind'] for a in aud.get('audit') or []])
    tools = [t for t in get('/api/e/tool?size=50')['items'] if t.get('life_limit')]
    if tools:
        t = tools[0]
        eqc = next((e['code'] for e in eq if e['id'] == t['equipment']), '')
        d3 = post('/api/ai/drafts', {'op': 'param_adjust', 'payload': {'entity': 'tool', 'record': t['code'], 'equipment': eqc, 'field': 'life_limit',
                                                                      'value': int(t['life_limit'] * 0.9), 'reason': '测试'}})
        ok('调整参数草稿：改动前后对比', d3.get('mode') == 'update' and d3['changes'][0]['before'] == str(t['life_limit']), d3.get('changes'))
        newv = int(t['life_limit'] * 0.8)
        r3 = post(f"/api/ai/drafts/{d3['id']}/decide", {'decision': 'approve', 'payload': {**d3['payload'], 'value': newv}})
        ok('改一下再通过：按修改后的值执行', r3.get('status') == 'approved' and r3.get('edited') and get(f"/api/e/tool/{t['id']}")['item'].get('life_limit') == newv, r3.get('receipt'))
        d4 = post('/api/ai/drafts', {'op': 'param_adjust', 'payload': {'entity': 'tool', 'record': t['code'], 'equipment': eqc, 'field': 'life_limit', 'value': int(newv * 0.9)}})
        req('PUT', f"/api/e/tool/{t['id']}", {'life_limit': newv + 5})
        r4 = post(f"/api/ai/drafts/{d4['id']}/decide", {'decision': 'approve'})
        ok('起草后数据被改过：拒绝执行（409）', r4.get('_status') == 409, r4.get('detail'))
        post(f"/api/ai/drafts/{d4['id']}/decide", {'decision': 'reject', 'reason': '数据已变'})
        req('PUT', f"/api/e/tool/{t['id']}", {'life_limit': t['life_limit']})
    sg = post('/api/ai/drafts/suggest', {'op': 'repair_order', 'context': {'kind': 'device', 'entity': 'equipment', 'id': cnc['id'], 'label': cnc['code']}})
    ok('右键"派维修"：按设备和最近报警起草', sg.get('status') == 'pending' and sg.get('source') == 'context' and sg['payload'].get('equipment') == cnc['code'], sg.get('title'))
    aps = get('/api/approvals?status=pending')
    mine = next((a for a in aps if a.get('id') == sg['id']), None)
    ok('审批中心里能看到 AI 草稿', mine and mine.get('kind') == 'ai_draft' and mine.get('draft', {}).get('changes'), (mine or {}).get('agent_name'))
    ra = post(f"/api/approvals/{sg['id']}/decide", {'decision': 'reject', 'reason': '审批中心驳回'})
    ok('审批中心处理 AI 草稿', ra.get('status') == 'rejected', ra)
    wos = get('/api/e/work_order?f_status=released')['items'] + get('/api/e/work_order?f_status=in_progress')['items']
    if wos:
        w = wos[0]
        d5 = post('/api/ai/drafts', {'op': 'schedule_change', 'payload': {'changes': [{'work_order': w['code'], 'priority': 1}], 'reason': '测试'}})
        r5 = post(f"/api/ai/drafts/{d5['id']}/decide", {'decision': 'approve'})
        ok('调整排程草稿：通过后工单优先级改了并重排', r5.get('status') == 'approved' and get(f"/api/e/work_order/{w['id']}")['item'].get('priority') == 1, (r5.get('receipt') or {}).get('text'))
        req('PUT', f"/api/e/work_order/{w['id']}", {'priority': w['priority']})
    bad = post('/api/ai/drafts', {'op': 'repair_order', 'payload': {'equipment': 'NOPE9', 'fault': 'x'}})
    ok('起草校验：找不到设备 → 400', bad.get('_status') == 400, bad.get('detail'))

    # 2.3 一句话自动化
    p = post('/api/ai/automations/parse', {'text': '主轴负载连续 10 分钟 > 90% 就提醒班长并建议降速'})
    tr = (p.get('flow') or {}).get('trigger') or {}
    acts = [a['type'] for a in (p.get('flow') or {}).get('actions') or []]
    ok('一句话 → 流程（触发 / 条件 / 动作）', tr.get('metric') == 'spindle_load' and tr.get('op') in ('>', '>=') and tr.get('value') == 90 and tr.get('for_min') == 10
       and 'notify' in acts, f"{p.get('by')} · {p.get('summary')}")
    rp = post('/api/ai/automations/replay', {'flow': p['flow'], 'days': 7})
    ok('历史回放：时间轴 + 次数', isinstance(rp.get('count'), int) and len(rp['timeline']['x']) == len(rp['timeline']['n']) and rp.get('note'), f"{rp.get('count')} 次 · {rp.get('note')}")
    # 虚拟工厂 4.7 按刀位分别上报主轴负载（Load@T01…），每把刀切削约 1 分钟、负载随刀具磨损变：阈值 / 持续时间逐级放低，直到看到触发
    # （检查的是"放低后能回放出触发"，不赌某一档阈值在这批数据里一定有）
    rp2, low = {}, None
    for v, m in ((20, 0.5), (12, 0.5), (8, 0.3), (5, 0.2), (2, 0.1)):
        low = {**p['flow'], 'trigger': {**p['flow']['trigger'], 'value': v, 'for_min': m}}
        rp2 = post('/api/ai/automations/replay', {'flow': low, 'days': 7})
        if rp2.get('count', 0) > 0:
            break
    ok('历史回放：阈值放低后能看到触发记录', rp2.get('count', 0) > 0 and rp2['fires'] and rp2['by_equipment'],
       f"> {low['trigger']['value']}% 持续 {low['trigger']['for_min']} 分钟：{rp2.get('count')} 次，{rp2.get('by_equipment')[:3]}")
    p2 = post('/api/ai/automations/parse', {'text': '8 小时内残余毛刺不合格品超过 3 条就提醒质检员并发起安灯'})
    ok('一句话 → 不合格品聚集规则（起草安灯）', (p2.get('flow') or {}).get('trigger', {}).get('type') == 'ncr' and any(a['type'] == 'draft' for a in p2['flow']['actions']), p2.get('summary'))
    cr = post('/api/ai/automations', {'flow': low, 'text': '测试规则', 'status': 'active', 'replay': rp2})
    lst = get('/api/ai/automations')
    ok('启用规则', cr.get('status') == 'active' and any(x['id'] == cr['id'] for x in lst['items']), cr.get('summary'))
    ps = post(f"/api/ai/automations/{cr['id']}/status", {'status': 'paused'})
    ok('暂停规则', ps.get('status') == 'paused')
    dl = req('DELETE', f"/api/ai/automations/{cr['id']}")
    ok('删除规则', dl.get('ok') and not any(x['id'] == cr['id'] for x in get('/api/ai/automations')['items']))

    # 2.4 AI 值守
    sc = post('/api/ai/watch/scan', {})
    fs = get('/api/ai/watch/findings?status=active')['items']
    ok('AI 值守：发现带证据和严重程度', fs and all(f.get('evidence') and f.get('severity') in ('critical', 'warning', 'info') for f in fs), f"{len(fs)} 条：" + '；'.join(f['title'] for f in fs[:3]))
    sc2 = post('/api/ai/watch/scan', {})
    ok('AI 值守：去重（再扫一遍没有新增同样的发现）', sc2.get('new') == 0 and len(get('/api/ai/watch/findings?status=active')['items']) == len(fs), sc2)
    withd = next((f for f in fs if f.get('draft')), None)
    if withd:
        fd = post(f"/api/ai/watch/findings/{withd['id']}/draft", {})
        ok('AI 值守：按建议起草动作（待审批）', fd.get('status') == 'pending' and fd.get('source') == 'watch', fd.get('title'))
        post(f"/api/ai/drafts/{fd['id']}/decide", {'decision': 'reject', 'reason': '测试'})
    ak = post(f"/api/ai/watch/findings/{fs[0]['id']}/ack", {}) if fs else {}
    ok('AI 值守：确认（知道了）', ak.get('status') == 'acked', ak.get('acked_by'))
    feed = get('/api/home/feed')
    ok('工作台信息流里有 AI 值守发现', 'ai_watch' in (feed.get('providers') or []) and any(i['kind'] == 'ai' for i in feed['items']), feed.get('counts'))

    # 2.5 日报 + PDF
    d = post('/api/ai/daily', {})
    r = d.get('report') or {}
    ok('AI 日报：杂志式结构（头条、8 个 KPI、5 张图、亮点 / 风险 / 建议）', r.get('headline') and len(r.get('kpis') or []) == 8
       and set(r.get('charts') or {}) >= {'output', 'oee', 'pareto', 'downtime', 'energy'} and r.get('risks') is not None and r.get('actions') is not None, f"{r.get('by')} · {r.get('headline')}")
    st, pdf = req('GET', '/api/report/daily/pdf', raw=True)
    ok('日报导出 PDF', st == 200 and pdf[:4] == b'%PDF' and len(pdf) > 5000, f'{st} {len(pdf)} 字节')
    sch = req('PUT', '/api/report/daily/schedule', {'enabled': True, 'time': '23:50', 'target': 'today'})
    ok('日报定时设置', sch.get('enabled') and sch.get('time') == '23:50', sch)
    req('PUT', '/api/report/daily/schedule', {'enabled': False})

    # 2.6 跨工段 AI 解读
    bt = post('/api/ai/backtrace/explain', {'days': 7, 'group': 'all'})
    ok('缺陷回溯 AI 解读', bt.get('text') and bt.get('blocks'), f"{bt.get('by')} · {bt.get('text', '')[:60]}")
    cp = post('/api/ai/coplan/advise', {})
    ok('协同排产 AI 建议', cp.get('text') and cp.get('blocks'), f"{cp.get('by')} · {cp.get('text', '')[:60]}")

    # 2.7 钉到工作台
    pn = post('/api/ai/pins', {'block': {'type': 'chart', 'kind': 'bar', 'title': '测试图', 'x': ['A', 'B'], 'series': [{'name': 'n', 'data': [1, 2]}]}})
    pins = get('/api/ai/pins')
    ok('钉到工作台', pn.get('id') and any(x['id'] == pn['id'] for x in pins))
    req('DELETE', f"/api/ai/pins/{pn['id']}")
    ok('取消钉住', not any(x['id'] == pn['id'] for x in get('/api/ai/pins')))
    bad = post('/api/ai/pins', {'block': {'type': 'sources', 'items': [{'label': 'x'}]}})
    ok('数据来源块不能钉', bad.get('_status') == 400)
finally:
    req('PUT', '/api/ai/switches', {k: SW0[k] for k in ('global', 'features', 'demo', 'record')})

bad = [n for n, g in results if not g]
print(f"\n{len(results) - len(bad)}/{len(results)} 通过", ('；失败：' + '、'.join(bad)) if bad else '')
sys.exit(1 if bad else 0)
