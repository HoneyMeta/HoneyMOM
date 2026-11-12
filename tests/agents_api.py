"""智能体与 AI 助手的接口测试 + 端到端演示（要先启动 MES；虚拟工厂可选）。

    python tests/agents_api.py              # 虚拟工厂在线就在虚拟工厂里注入故障，否则自动改用"在 MES 里模拟停机"
    python tests/agents_api.py --simulate   # 强制在 MES 里模拟停机事件（不碰虚拟工厂）
    python tests/agents_api.py --no-llm-chat  # 跳过需要大模型的聊天助手用例

端到端：虚拟工厂 CNC 注入故障 → MES 采集到停机 → "停机 → 维修工单"智能体自动起草 → 设备员审批 → 维修工单生成。
"""
import json
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')
MES, VF = 'http://127.0.0.1:8900', 'http://127.0.0.1:8800'
SIM = '--simulate' in sys.argv
CHAT = '--no-llm-chat' not in sys.argv
results = []


def req(method, url, body=None, tok='', raw=False, timeout=180):
    r = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                               headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + tok} if tok else {})})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        data = f.read()
        return data.decode('utf-8') if raw else json.loads(data)


def call(method, path, body=None, tok=None):
    return req(method, MES + path, body, tok or T['admin'])


def err(method, path, body=None, tok=None):
    try:
        call(method, path, body, tok)
        return None, None
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}').get('detail')


def ok(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name, detail, flush=True)


def wait_run(rid, until=('succeeded', 'failed', 'rejected', 'waiting', 'cancelled'), t=180):
    t0 = time.time()
    d = None
    while time.time() - t0 < t:
        d = call('GET', f'/api/agents/runs/{rid}')
        if d['status'] in until:
            return d
        time.sleep(1)
    return d


def sse(text, tok, thread=None, page='/', record=None):
    raw = req('POST', MES + '/api/assistant/chat', {'text': text, 'thread_id': thread, 'page': page, 'page_title': '', 'record': record}, tok, raw=True, timeout=300)
    evs = []
    for chunk in raw.split('\n\n'):
        ev, data = 'message', ''
        for line in chunk.split('\n'):
            if line.startswith('event:'):
                ev = line[6:].strip()
            elif line.startswith('data:'):
                data += line[5:].strip()
        if data:
            evs.append((ev, json.loads(data)))
    return evs


T = {u: req('POST', MES + '/api/login', {'username': u, 'password': 'honey123'})['token'] for u in ('admin', 'maint', 'qc', 'operator', 'planner')}
ok('登录 5 个角色', len(T) == 5)
meta = call('GET', '/api/agents/meta')
ok('节点类型和 8 个模板', len(meta['templates']) == 8 and 'approval' in meta['types'] and 'act_repair_order' in meta['types'], f"{len(meta['types'])} 种节点")
code, msg = err('POST', '/api/agents', {'template': 'sop_qa'}, T['operator'])
ok('操作工不能新建智能体', code == 403, msg)

# ---------------- 端到端：停机 → 维修工单 ----------------
aid = call('POST', '/api/agents', {'template': 'downtime_repair', 'name': '停机 → 维修工单（接口测试）'})['id']
call('POST', f'/api/agents/{aid}/publish', {'note': '接口测试'})
call('POST', f'/api/agents/{aid}/enable', {'enabled': True})
ok('从模板新建、发布、启用', call('GET', f'/api/agents/{aid}')['enabled'])
vf_ok = False
if not SIM:
    try:
        vtok = req('POST', VF + '/api/login', {'username': 'admin', 'password': 'honey123'})['token']
        plants = req('GET', VF + '/api/plants', tok=vtok)['plants']
        g = next((p for p in plants if p['id'] == 'foundry-housing'), None)
        vf_ok = bool(g) and not g['paused']
        if g and g['paused']:
            print('虚拟工厂处于暂停状态（不去改它），改用 MES 内模拟停机')
    except Exception as ex:
        print('虚拟工厂连不上，改用 MES 内模拟停机：', ex)
mon = call('GET', '/api/equipment/monitor')['items']
cands = [e for e in mon if e['etype'] == 'cnc' and e['status'] in ('running', 'idle')] or [e for e in mon if e['etype'] == 'cnc']
eq = cands[0]['code'] if cands else 'CNC3'
before = call('GET', f'/api/e/repair_order?f_equipment={cands[0]["id"]}&size=1')['total'] if cands else 0
t0 = time.time()
if vf_ok:
    req('POST', VF + f'/api/p/foundry-housing/station/{eq}/fault', {'kind': 'failure'}, vtok)
    print(f'已在虚拟工厂给 {eq} 注入故障，等 MES 采集到停机…')
else:
    call('POST', '/api/agents/debug/emit', {'kind': 'equipment_state', 'equipment': eq, 'state': 'Stopped', 'alarm': '设备故障（模拟）'})
    print(f'已在 MES 里模拟 {eq} 停机')
run = None


def wait_event_run(limit):
    t1 = time.time()
    while time.time() - t1 < limit:
        rs = [r for r in call('GET', f'/api/agents/{aid}/runs') if r['trigger'] == 'event']
        if rs:
            return rs[0]
        time.sleep(1)
    return None


run = wait_event_run(90 if vf_ok else 60)
if not run and vf_ok:
    # 虚拟工厂是共用的，别人可能刚好把它暂停了：清掉注入的故障，改用 MES 内模拟
    paused = next((p['paused'] for p in req('GET', VF + '/api/plants', tok=vtok)['plants'] if p['id'] == 'foundry-housing'), None)
    print(f'虚拟工厂 90 秒内没有产生停机（暂停={paused}），清掉故障，改用 MES 内模拟停机')
    req('POST', VF + f'/api/p/foundry-housing/station/{eq}/fault', {'kind': 'clear'}, vtok)
    vf_ok = False
    t0 = time.time()
    call('POST', '/api/agents/debug/emit', {'kind': 'equipment_state', 'equipment': eq, 'state': 'Stopped', 'alarm': '设备故障（模拟）'})
    run = wait_event_run(60)
ok(f'{"虚拟工厂注入故障" if vf_ok else "模拟停机"} → 智能体自动触发', run, f"{eq}，用时 {time.time() - t0:.0f} 秒" + (f"，去重键 {run['trigger_key']}" if run else ''))
if run:
    d = wait_run(run['id'])
    ok('智能体起草后停在设备员审批', d['status'] == 'waiting', f"{d['status']} {d['error'] or ''}")
    aps = call('GET', '/api/approvals', tok=T['maint'])
    ap = next((a for a in aps if a['run'] == run['id']), None)
    ok('设备员在审批中心看到这张单子', ap and ap['can_decide'], ap and ap['title'])
    cnt = call('GET', '/api/approvals/count', tok=T['maint'])['pending']
    ok('顶栏待审批数', cnt >= 1, cnt)
    code, msg = err('POST', f"/api/approvals/{ap['id']}/decide", {'decision': 'approve'}, T['qc'])
    ok('质检员不能批设备员的单子', code == 403, msg)
    print('  草稿：', json.dumps(ap['payload'], ensure_ascii=False))
    call('POST', f"/api/approvals/{ap['id']}/decide", {'decision': 'approve'}, T['maint'])
    d = wait_run(run['id'], until=('succeeded', 'failed', 'rejected'))
    recs = d['output'].get('receipts') or []
    ok('审批通过 → 维修工单生成', d['status'] == 'succeeded' and any(r.get('entity') == 'repair_order' for r in recs), '；'.join(r['text'] for r in recs))
    after = call('GET', f'/api/e/repair_order?f_equipment={cands[0]["id"]}&size=1')['total'] if cands else 0
    ok('维修工单表里多了一张', after == before + 1, f'{before} → {after}')
    ok('每一步都有记录（输入/输出/耗时）', all(s['status'] in ('done', 'waiting') and s['duration_ms'] is not None for s in d['steps']), f"{len(d['steps'])} 步，{d['tokens']} token")
if vf_ok:
    req('POST', VF + f'/api/p/foundry-housing/station/{eq}/fault', {'kind': 'clear'}, vtok)
else:
    call('POST', '/api/agents/debug/emit', {'kind': 'equipment_state', 'equipment': eq, 'state': 'Executing', 'alarm': ''})
call('POST', f'/api/agents/{aid}/enable', {'enabled': False})
# 冷却：同一设备马上再停机不重复触发（用 raw 事件，不碰设备）
call('POST', f'/api/agents/{aid}/enable', {'enabled': True})
sample = call('GET', '/api/agents/sample_events/equipment_state')
p = dict(sample[0]['payload'] if sample else {}, equipment_code=eq, to='down', etype='cnc')
call('POST', '/api/agents/debug/emit', {'kind': 'raw', 'event': 'equipment_state', 'payload': p, 'repeat': 15})
time.sleep(6)
ev = call('GET', f'/api/agents/{aid}/events')
ok('冷却期内同一设备再停机 15 次：不再起新运行', sum(1 for e in ev if e['outcome'] == 'started') == (1 if run else 0) and any(e['outcome'] in ('cooldown', 'merged') for e in ev),
   '、'.join(sorted({e['outcome'] for e in ev})))
call('POST', f'/api/agents/{aid}/enable', {'enabled': False})

# ---------------- 调试运行、驳回、试运行 ----------------
aid2 = call('POST', '/api/agents', {'template': 'wo_late'})['id']
s = call('GET', '/api/agents/sample_events/wo_late')
ok('模拟事件：取真实工单当输入', s, s[0]['label'] if s else '')
n0 = call('GET', '/api/e/schedule_proposal?size=1')['total']
rid = call('POST', f'/api/agents/{aid2}/run', {'inputs': s[0]['payload'], 'mode': 'dry'})['run_id']
d = wait_run(rid)
ap = next(a for a in d['approvals'] if a['status'] == 'pending')
call('POST', f"/api/approvals/{ap['id']}/decide", {'decision': 'reject', 'reason': '优先级只提到 2，别影响北辰的单子'}, T['planner'])
d = wait_run(rid, until=('waiting', 'failed', 'succeeded'), t=10)
time.sleep(1)
d = wait_run(rid)
plan_steps = [x for x in d['steps'] if x['node'] == 'plan']
ok('驳回 → 带着原因回到"起草调整建议"重做', d['status'] == 'waiting' and len(plan_steps) == 2, f"起草 {len(plan_steps)} 次")
ap2 = next((a for a in d['approvals'] if a['status'] == 'pending'), None)
ok('重做后的建议', ap2, json.dumps(ap2['payload'] if ap2 else {}, ensure_ascii=False)[:300])
if ap2:
    call('POST', f"/api/approvals/{ap2['id']}/decide", {'decision': 'approve'}, T['planner'])
    d = wait_run(rid, until=('succeeded', 'failed', 'rejected'))
ok('试运行：审批通过后不写排程建议', d['status'] == 'succeeded' and call('GET', '/api/e/schedule_proposal?size=1')['total'] == n0, (d['output'].get('reply') or '')[:120])
r = call('POST', f'/api/agents/{aid2}/run_node', {'node': 'board', 'mocks': {}})
ok('只运行一个节点', r['steps'] and r['steps'][-1]['status'] == 'done', (r['steps'][-1]['output'] or {}).get('summary', '')[:100] if r['steps'] else '')
exp = call('GET', f'/api/agents/{aid2}/export')
imp = call('POST', '/api/agents/import', exp)
ok('导出 / 导入 JSON', call('GET', f"/api/agents/{imp['id']}")['name'].endswith('（导入）'))
call('DELETE', f"/api/agents/{imp['id']}")

# ---------------- Webhook ----------------
aid3 = call('POST', '/api/agents', {'name': 'Webhook 测试'})['id']
dr = call('GET', f'/api/agents/{aid3}')['draft']
dr['components']['begin']['params'].update(trigger='webhook', inputs=[{'key': 'question', 'label': '问题', 'type': 'text'}])
call('PUT', f'/api/agents/{aid3}', {'draft': dr})
call('POST', f'/api/agents/{aid3}/publish', {})
call('POST', f'/api/agents/{aid3}/enable', {'enabled': True})
hook = call('GET', f'/api/agents/{aid3}')['webhook']
r = req('POST', MES + hook, {'question': '你好'})
d = wait_run(r['run_id'])
ok('Webhook 触发', d['status'] == 'succeeded' and '你好' in d['output']['reply'], d['output']['reply'])
try:
    req('POST', MES + hook.split('?')[0] + '?key=wrong', {})
    ok('Webhook 密钥不对会拒绝', False)
except urllib.error.HTTPError as e:
    ok('Webhook 密钥不对会拒绝', e.code == 403)
call('DELETE', f'/api/agents/{aid3}')

# ---------------- 右下角助手 ----------------
th = call('POST', '/api/assistant/threads', {'title': '接口测试'})
call('PATCH', f"/api/assistant/threads/{th['id']}", {'title': '接口测试（改名）'})
ok('会话：新建、改名', any(t['title'] == '接口测试（改名）' for t in call('GET', '/api/assistant/threads')))
call('DELETE', f"/api/assistant/threads/{th['id']}")
ok('会话：删除', not any(t['id'] == th['id'] for t in call('GET', '/api/assistant/threads')))
code, _ = err('GET', f"/api/assistant/threads/{th['id']}")
ok('别人的/删掉的会话打不开', code == 404)
code, msg = err('POST', '/api/assistant/commit', {'token': 'x.y', 'payload': {}})
ok('伪造的确认令牌被拒绝', code == 400, msg)
if CHAT:
    evs = sse('最近 8 小时哪台设备停机时间最长？带我去 OEE 页面看看', T['admin'], page='/equipment/monitor')
    kinds = [e for e, _ in evs]
    tools = [d['text'] for e, d in evs if e == 'tool']
    nav = [d for e, d in evs if e == 'navigate']
    ans = next((d['text'] for e, d in evs if e == 'answer'), '')
    ok('助手：先查数据再回答（进度是人话）', tools and not any(x in ' '.join(tools) for x in ('query_sql', 'mes_query', 'day_kpi')), ' | '.join(tools[:4]))
    ok('助手：带你去看（navigate 事件）', nav and nav[0]['path'].startswith('/equipment/oee'), nav[0] if nav else kinds)
    ok('助手：给出回答并保存会话', ans and 'done' in kinds, ans[:120].replace('\n', ' '))
    done = next(d for e, d in evs if e == 'done')
    msgs = call('GET', f"/api/assistant/threads/{done['thread_id']}")['messages']
    ok('会话里存了带页面的消息和过程', msgs[0]['page'] == '/equipment/monitor' and msgs[-1]['steps'], f"{len(msgs)} 条")
    # 写操作：检查 → 确认卡 → 凭令牌提交
    evs = sse(f'{eq} 主轴有异响，帮我建一张一般级别的维修工单，故障描述写"主轴异响，待检查"', T['maint'], page='/e/repair_order')
    card = next((d for e, d in evs if e == 'confirm'), None)
    ok('助手：写操作先给确认卡（还没写入）', card and card['op'] == 'repair_order', card['preview'].replace('\n', ' ') if card else [(e, d.get('text', '')[:200]) for e, d in evs if e in ('error', 'answer')])
    if card:
        n0 = call('GET', '/api/e/repair_order?size=1')['total']
        bad = dict(card['payload'], fault='被篡改的内容')
        code, msg = err('POST', '/api/assistant/commit', {'token': card['token'], 'payload': bad}, T['maint'])
        ok('改过的内容不能拿原令牌提交', code == 409, msg)
        code, msg = err('POST', '/api/assistant/commit', {'token': card['token'], 'payload': card['payload']}, T['qc'])
        ok('别人不能用这张确认卡', code == 403, msg)
        r = call('POST', '/api/assistant/commit', {'token': card['token'], 'payload': card['payload']}, T['maint'])
        ok('确认提交 → 服务端回执', r['receipt']['text'].startswith('已创建维修工单') and call('GET', '/api/e/repair_order?size=1')['total'] == n0 + 1, r['receipt']['text'])
        code, msg = err('POST', '/api/assistant/commit', {'token': card['token'], 'payload': card['payload']}, T['maint'])
        ok('同一张确认卡不能提交两次', code == 400, msg)
    evs = sse('帮我给 CNC1 建一张维修工单，故障是导轨润滑报警', T['qc'], page='/quality')
    ok('助手：没有权限的角色拿不到写工具（不会出确认卡）', not any(e == 'confirm' for e, _ in evs),
       next((d['text'] for e, d in evs if e == 'answer'), '')[:100].replace('\n', ' '))
    # 聊天调用智能体
    aid4 = call('POST', '/api/agents', {'template': 'sop_qa'})['id']
    call('POST', f'/api/agents/{aid4}/publish', {})
    call('POST', f'/api/agents/{aid4}/enable', {'enabled': True})
    evs = sse('请用"SOP 问答"智能体查一下：残镁偏低怎么办？', T['operator'])
    card = next((d for e, d in evs if e == 'card' and d.get('kind') == 'agent_run'), None)
    ok('助手调用已发布的智能体，结果以卡片返回', card and card['status'] == 'succeeded', (card['reply'] if card else '')[:120].replace('\n', ' '))
    call('POST', f'/api/agents/{aid4}/enable', {'enabled': False})

# 清理本测试建的智能体（运行记录、审批和建出来的单据保留）
for x in [aid, aid2] + ([aid4] if CHAT else []):
    try:
        call('DELETE', f'/api/agents/{x}')
    except Exception:
        pass
bad = [n for n, g in results if not g]
print(f"\n{len(results) - len(bad)}/{len(results)} 通过", ('；失败：' + '、'.join(bad)) if bad else '')
sys.exit(1 if bad else 0)
