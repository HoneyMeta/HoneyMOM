"""智能体引擎测试（进程内，不需要启动服务）：复制一份现有数据库，用假的大模型跑。

    python tests/agents_check.py            # 用 data/mes.db 的副本
    python tests/agents_check.py --real-llm # 智能体节点用真实大模型（需要 config/llm.local.json）

覆盖：8 个模板都能跑通；试运行不写数据；审批挂起/继续；驳回原因回到 AI 节点并重做；事件防抖/冷却/去重；校验规则；只运行一个节点；循环；权限白名单；幂等。
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa  # noqa: E402

from honeymom import db  # noqa: E402

SRC = os.path.join(db.ROOT, 'data', 'mes.db')
TMP = os.path.join(tempfile.mkdtemp(prefix='mes_agent_'), 'mes.db')
if os.path.exists(SRC):
    s, d = sqlite3.connect(SRC), sqlite3.connect(TMP)
    s.backup(d)
    s.close()
    d.close()
db.init('sqlite:///' + TMP.replace('\\', '/'))
from honeymom import seed  # noqa: E402

seed.run()
from honeymom.agent import api, engine, runtime, templates  # noqa: E402
from honeymom.schema import TABLES, agent_action_t, agent_run_t, agent_step_t, approval_t  # noqa: E402

REAL = '--real-llm' in sys.argv
results = []
PROMPTS = []


def ok(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name, detail)


def fake_llm(msgs, tools, json_mode):
    text = '\n'.join(str(m.get('content') or '') for m in msgs)
    PROMPTS.append(text)
    sys_ = str(msgs[0].get('content') or '')
    if tools and not any(m.get('role') == 'tool' for m in msgs):
        return {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'c1', 'type': 'function', 'function': {'name': 'mes_query', 'arguments': json.dumps({'kind': 'oee', 'hours': 8})}}]}, 50
    if json_mode and '分类器' in sys_:
        u = str(msgs[-1]['content'])
        cat = 'equip' if any(k in u.split('内容：')[-1] for k in ('故障', '停机', '报警')) else 'other'
        return {'role': 'assistant', 'content': json.dumps({'category': cat, 'reason': '测试'})}, 20
    if json_mode:
        if '改成一般' in text:
            return {'role': 'assistant', 'content': json.dumps({'level': 'normal', 'fault': '按审批意见修改后的故障描述'})}, 30
        return {'role': 'assistant', 'content': '{}'}, 30
    return {'role': 'assistant', 'content': '【测试回答】根据数据，一切正常。'}, 40


if not REAL:
    engine.LLM_HOOK = fake_llm
ADMIN = {'username': 'admin', 'name': '系统管理员', 'role_list': ['admin'], 'roles': 'admin'}
MAINT = {'username': 'maint', 'name': '设备员周涛', 'role_list': ['maint'], 'roles': 'maint'}
OPER = {'username': 'operator', 'name': '操作工', 'role_list': ['operator'], 'roles': 'operator'}


def wait_run(rid, until=('succeeded', 'failed', 'rejected', 'waiting', 'cancelled'), t=240):
    t0 = time.time()
    while time.time() - t0 < t:
        with db.engine.connect() as c:
            st = c.execute(sa.select(agent_run_t.c.status).where(agent_run_t.c.id == rid)).scalar()
        if st in until:
            return st
        time.sleep(0.2)
    return st


def count(entity):
    with db.engine.connect() as c:
        return c.execute(sa.select(sa.func.count()).select_from(TABLES[entity])).scalar()


def pending_approval(rid):
    with db.engine.connect() as c:
        return c.execute(sa.select(approval_t).where((approval_t.c.run == rid) & (approval_t.c.status == 'pending'))).mappings().first()


def steps(rid):
    with db.engine.connect() as c:
        return [dict(r) for r in c.execute(sa.select(agent_step_t).where(agent_step_t.c.run == rid).order_by(agent_step_t.c.id)).mappings()]


def sample(kind):
    s = api.sample_events(kind, user=ADMIN)
    return s[0]['payload'] if s else None


# ---------------- 模板 ----------------
APPROVER = {'quality_ncr': {'username': 'qc', 'name': '质检员', 'role_list': ['qc']}, 'downtime_repair': MAINT,
            'material_shortage': {'username': 'wh', 'name': '仓管员', 'role_list': ['warehouse']}, 'wo_late': {'username': 'planner', 'name': '计划员', 'role_list': ['planner']}}
INPUTS = {'shift_report': lambda: {}, 'oee_analysis': lambda: {'query': '最近哪台设备损失最大'}, 'andon_triage': lambda: sample('andon'),
          'quality_ncr': lambda: sample('ncr'), 'downtime_repair': lambda: sample('equipment_state') or {'equipment_code': 'CNC3', 'equipment_name': '3#加工中心',
                                                                                                         'alarm': '设备故障', 'time': '2026-10-30 10:00:00'},
          'material_shortage': lambda: {}, 'wo_late': lambda: sample('wo_late'), 'sop_qa': lambda: {'query': '残镁偏低怎么办'}}
WRITES = {'downtime_repair': 'repair_order', 'quality_ncr': 'report_8d', 'material_shortage': 'material_request', 'wo_late': 'schedule_proposal', 'shift_report': 'ai_report',
          'oee_analysis': 'ai_report'}
ids = {}
for t in templates.templates():
    key = t['key']
    aid = api.create_agent({'template': key}, user=ADMIN)['id']
    ids[key] = aid
    a = api.get_agent(aid, user=ADMIN)
    errs = [p for p in a['problems'] if p['level'] == 'error']
    ok(f'模板「{t["name"]}」校验无错误', not errs, '；'.join(p['msg'] for p in errs))
    inputs = INPUTS[key]()
    if inputs is None:
        ok(f'模板「{t["name"]}」有可用的演示事件', False, '数据库里没有这类记录')
        continue
    w = WRITES.get(key)
    before = count(w) if w else 0
    rid = api.run(aid, {'inputs': inputs, 'mode': 'live'}, user=ADMIN)['run_id']
    st = wait_run(rid)
    if st == 'waiting':
        ap = pending_approval(rid)
        api.decide(ap['id'], {'decision': 'approve'}, user=APPROVER.get(key, ADMIN))
        st = wait_run(rid, until=('succeeded', 'failed', 'rejected', 'cancelled'))
    d = api.run_detail(rid, user=ADMIN)
    after = count(w) if w else 0
    ok(f'模板「{t["name"]}」跑通', st == 'succeeded' and (not w or after > before or key == 'material_shortage' and d['node_status'].get('ok') == 'done'),
       f"状态 {st}；{len(d['steps'])} 步；回执：{'；'.join(r['text'] for r in d['output'].get('receipts') or [])[:160]}；错误：{d['error'] or ''}")

# ---------------- 试运行不写数据 ----------------
aid = ids['downtime_repair']
before = (count('repair_order'), count('notice'))
inp = INPUTS['downtime_repair']()
rid = api.run(aid, {'inputs': inp, 'mode': 'dry'}, user=ADMIN)['run_id']
st = wait_run(rid)
ap = pending_approval(rid)
ok('试运行也会停在审批', st == 'waiting' and ap and ap['dry_run'], st)
api.decide(ap['id'], {'decision': 'approve'}, user=MAINT)
st = wait_run(rid, until=('succeeded', 'failed', 'rejected'))
after = (count('repair_order'), count('notice'))
with db.engine.connect() as c:
    dry = c.execute(sa.select(sa.func.count()).select_from(agent_action_t).where((agent_action_t.c.run == rid) & agent_action_t.c.dry_run.is_(True))).scalar()
ok('试运行不写维修工单', st == 'succeeded' and after[0] == before[0], f'维修工单 {before[0]}→{after[0]}')
ok('试运行的写操作只记录', dry >= 2, f'记录了 {dry} 条（建单 + 通知）')

# ---------------- 驳回 → 原因回到 AI → 重做 ----------------
PROMPTS.clear()
before = count('repair_order')
rid = api.run(aid, {'inputs': inp, 'mode': 'live'}, user=ADMIN)['run_id']
wait_run(rid)
ap = pending_approval(rid)
api.decide(ap['id'], {'decision': 'reject', 'reason': '不是紧急故障，改成一般'}, user=MAINT)
t0 = time.time()
ap2 = None
while time.time() - t0 < 240:        # 等它带着驳回原因重新起草、再次提交审批
    ap2 = pending_approval(rid)
    with db.engine.connect() as c:
        st = c.execute(sa.select(agent_run_t.c.status).where(agent_run_t.c.id == rid)).scalar()
    if (ap2 and ap2['id'] != ap['id']) or st in ('failed', 'succeeded', 'rejected'):
        break
    time.sleep(0.5)
draft_steps = [s for s in steps(rid) if s['node'] == 'draft']
fed = any('不是紧急故障，改成一般' in p for p in PROMPTS)
ok('驳回后回到起草节点重做', st == 'waiting' and ap2 and ap2['id'] != ap['id'] and len(draft_steps) == 2, f'起草 {len(draft_steps)} 次，状态 {st}')
ok('驳回原因传给了大模型', fed or REAL)
ok('重做后的内容按意见修改', ap2 and (ap2['payload'] or {}).get('level') == 'normal' or REAL, json.dumps(ap2['payload'] if ap2 else {}, ensure_ascii=False)[:200])
# 审批人改参数再批
edited = dict(ap2['payload'])
edited['fault'] = '审批人改过的故障描述'
api.decide(ap2['id'], {'decision': 'approve', 'payload': edited}, user=MAINT)
st = wait_run(rid, until=('succeeded', 'failed', 'rejected'))
with db.engine.connect() as c:
    last = c.execute(sa.select(TABLES['repair_order']).order_by(TABLES['repair_order'].c.id.desc()).limit(1)).mappings().first()
ok('改参数再批：按改后的内容建单', st == 'succeeded' and count('repair_order') == before + 1 and last['fault'] == '审批人改过的故障描述', f"{last['code']} {last['fault']}")
# 没有"驳回"出口时：整次运行结束为已驳回
aid4 = api.create_agent({'template': 'downtime_repair'}, user=ADMIN)['id']
dsl = api.get_agent(aid4, user=ADMIN)['draft']
dsl['components']['approve']['downstream'] = [d for d in dsl['components']['approve']['downstream'] if d['handle'] != 'rejected']
dsl['components']['draft']['upstream'] = [u for u in dsl['components']['draft']['upstream'] if u != 'approve']
api.save_agent(aid4, {'draft': dsl}, user=ADMIN)
rid = api.run(aid4, {'inputs': inp}, user=ADMIN)['run_id']
wait_run(rid)
api.decide(pending_approval(rid)['id'], {'decision': 'reject', 'reason': '误报'}, user=MAINT)
st = wait_run(rid, until=('succeeded', 'failed', 'rejected'))
d = api.run_detail(rid, user=ADMIN)
ok('没有驳回出口时运行结束为"已驳回"并记下原因', st == 'rejected' and '误报' in (d['error'] or ''), d['error'])
# 无权限的人不能审批
rid = api.run(aid, {'inputs': inp, 'mode': 'dry'}, user=ADMIN)['run_id']
wait_run(rid)
ap = pending_approval(rid)
try:
    api.decide(ap['id'], {'decision': 'approve'}, user=OPER)
    ok('操作工不能审批设备员的单子', False)
except Exception as ex:
    ok('操作工不能审批设备员的单子', '设备员' in str(getattr(ex, 'detail', ex)), getattr(ex, 'detail', ex))
api.cancel(rid, user=ADMIN)
ok('取消运行', wait_run(rid, until=('cancelled',), t=5) == 'cancelled')

# ---------------- 校验 ----------------
bad = json.loads(json.dumps(templates.template('downtime_repair')['dsl']))
c_ = bad['components']
c_['create']['params']['require_approval'] = False
c_['approve']['downstream'] = [d for d in c_['approve']['downstream'] if d['handle'] != 'approved']
c_['create']['upstream'] = ['draft']
c_['draft']['downstream'].append({'target': 'create', 'handle': None})
probs = engine.validate(bad, ['repair_order', 'notify'])
ok('写操作不经过审批会被拦住', any('写操作' in p['msg'] and p['level'] == 'error' for p in probs), '；'.join(p['msg'] for p in probs if p['level'] == 'error')[:150])
probs = engine.validate(templates.template('downtime_repair')['dsl'], ['notify'])
ok('不在权限白名单的动作会被拦住', any('白名单' in p['msg'] for p in probs))
try:
    api.publish(aid4, {}, user=ADMIN)
    ok('发布', True)
except Exception as ex:
    ok('发布', False, str(ex))
try:
    api.publish(aid4, {}, user=OPER)
    ok('操作工不能发布智能体', False)
except Exception as ex:
    ok('操作工不能发布智能体', '只有' in str(getattr(ex, 'detail', ex)))

# ---------------- 版本：发布、回滚 ----------------
aid = ids['sop_qa']
api.publish(aid, {'note': 'v1'}, user=ADMIN)
dsl = api.get_agent(aid, user=ADMIN)['draft']
dsl['components']['reply']['params']['content'] = 'V2：{answer@text}'
api.save_agent(aid, {'draft': dsl}, user=ADMIN)
api.publish(aid, {'note': 'v2'}, user=ADMIN)
a = api.get_agent(aid, user=ADMIN)
v1 = [v for v in a['versions'] if v['version'] == 1][0]
api.rollback(aid, {'version_id': v1['id']}, user=ADMIN)
a = api.get_agent(aid, user=ADMIN)
ok('版本：发布两次再回滚到 v1', a['published'] == 1 and 'V2' not in json.dumps(a['draft'], ensure_ascii=False), f"当前发布 v{a['published']}，共 {len(a['versions'])} 个版本")
rid = api.run(aid, {'inputs': {'query': '残镁偏低怎么办'}, 'use': 'published'}, user=OPER)['run_id']
ok('普通用户可以运行已发布版本', wait_run(rid) == 'succeeded')

# ---------------- 只运行一个节点 ----------------
aid = ids['downtime_repair']
dsl = api.get_agent(aid, user=ADMIN)['draft']
r = api.run_node(aid, {'node': 'create', 'inputs': {}, 'mocks': {'approve': {'payload': {'equipment': 'CNC1', 'fault': '模拟故障', 'level': 'normal'}}}}, user=ADMIN)
st = r['steps'][-1] if r['steps'] else {}
ok('只运行一个节点（用模拟的上游输出，按试运行）', st.get('status') == 'done' and (st.get('output') or {}).get('dry_run'), (st.get('output') or {}).get('text'))
r = api.run_node(aid, {'node': 'hist', 'inputs': {'equipment_code': 'CNC2'}, 'mocks': {'begin': {'equipment_code': 'CNC2'}}}, user=ADMIN)
ok('只运行查询节点', r['steps'] and r['steps'][-1]['status'] == 'done' and 'CNC2' in (r['steps'][-1]['output'] or {}).get('summary', ''))

# ---------------- 循环 + 条件 + 变量 ----------------
loop_dsl = templates.build([
    templates.begin(trigger='manual', inputs=[]),
    ('q', 'mes_query', '设备', {'entity': 'equipment'}),
    ('lp', 'loop', '逐台', {'items': '{q@rows}', 'max_items': 4}),
    ('sw', 'switch', '是否 CNC', {'cases': [{'id': 'cnc', 'name': 'CNC', 'logic': 'and', 'conditions': [{'left': '{lp@item.etype}', 'op': '==', 'right': 'cnc'}]}]}),
    ('n1', 'set_var', '记一下', {'assignments': [{'key': 'last', 'value': '{lp@item.code}'}]}),
    ('n2', 'act_notify', '通知', {'channel': 'inbox', 'title': '设备 {lp@item.code}', 'content': '第 {lp@index} 台'}),
    ('rep', 'reply', '输出', {'content': '共 {lp@count} 台，最后一台 {vars.last}'}),
], [('begin', 'q', None), ('q', 'lp', None), ('lp', 'sw', 'body'), ('sw', 'n1', 'cnc'), ('sw', 'n1', 'else'), ('n1', 'n2', None), ('lp', 'rep', 'done')])
aid5 = api.create_agent({'name': '循环测试'}, user=ADMIN)['id']
api.save_agent(aid5, {'draft': loop_dsl}, user=ADMIN)
before = count('notice')
rid = api.run(aid5, {'mode': 'dry'}, user=ADMIN)['run_id']
st = wait_run(rid)
d = api.run_detail(rid, user=ADMIN)
ok('循环 4 项、条件分支、变量、试运行通知不落库', st == 'succeeded' and '共 4 台' in d['output']['reply'] and count('notice') == before
   and len([s for s in d['steps'] if s['node'] == 'n2']) == 4, d['output']['reply'])

# ---------------- 权限白名单（运行时） ----------------
aid6 = api.create_agent({'template': 'downtime_repair'}, user=ADMIN)['id']
api.save_agent(aid6, {'permissions': ['notify']}, user=ADMIN)
try:
    api.run(aid6, {'inputs': inp}, user=ADMIN)
    ok('白名单外的动作不允许运行', False)
except Exception as ex:
    ok('白名单外的动作不允许运行', '白名单' in str(getattr(ex, 'detail', ex)))

# ---------------- 幂等 ----------------
rid = engine.create_run(ids['shift_report'], templates.template('shift_report')['dsl'], mode='live', user='admin', run_as='admin')
ex = engine.Executor(rid)
ex.init_state()
n0 = count('ai_report')
r1 = ex.do_action('save', 'report', {'title': '幂等测试', 'content': 'x'}, {}, None)
r2 = ex.do_action('save', 'report', {'title': '幂等测试', 'content': 'x'}, {}, None)
ok('同一运行同一节点只写一次（幂等）', count('ai_report') == n0 + 1 and r2.output.get('repeated'), f"{r1.output.get('text')}")

# ---------------- 事件触发：防抖 / 冷却 / 去重 ----------------
aid = ids['downtime_repair']
api.publish(aid, {}, user=ADMIN)
api.enable(aid, {'enabled': True}, user=ADMIN)
rt = runtime.RT
rt.refresh(force=True)
with db.engine.connect() as c:
    n0 = c.execute(sa.select(sa.func.count()).select_from(agent_run_t).where(agent_run_t.c.agent == aid)).scalar()
p = dict(inp, to='down', etype='cnc', equipment_code='CNC4', equipment_name='4#加工中心', equipment_id=None, alarm='设备故障')
for _ in range(20):
    rt.dispatch('equipment_state', p)
rt.dispatch('equipment_state', dict(p, to='running'))          # 不匹配（不是停机）
rt.dispatch('equipment_state', dict(p, etype='agv', equipment_code='AGV'))  # 不匹配（设备类型）
for k in rt.pending:
    rt.pending[k][0] = 0
rt.flush_pending()
time.sleep(0.5)
with db.engine.connect() as c:
    n1 = c.execute(sa.select(sa.func.count()).select_from(agent_run_t).where(agent_run_t.c.agent == aid)).scalar()
ok('一阵事件风暴（20 次）只起 1 个运行', n1 - n0 == 1, f'起了 {n1 - n0} 个')
rt.dispatch('equipment_state', p)
for k in rt.pending:
    rt.pending[k][0] = 0
rt.flush_pending()
with db.engine.connect() as c:
    n2 = c.execute(sa.select(sa.func.count()).select_from(agent_run_t).where(agent_run_t.c.agent == aid)).scalar()
ok('冷却期内同一设备不再触发', n2 == n1)
rt.dispatch('equipment_state', dict(p, equipment_code='CNC5', equipment_name='5#加工中心'))
for k in rt.pending:
    rt.pending[k][0] = 0
rt.flush_pending()
with db.engine.connect() as c:
    n3 = c.execute(sa.select(sa.func.count()).select_from(agent_run_t).where(agent_run_t.c.agent == aid)).scalar()
ok('别的设备照常触发', n3 == n2 + 1)
rt.dispatch('equipment_state', dict(p, equipment_code='CNC2', equipment_name='2#加工中心', alarm='等待上下料（操作工未到位）'))
for k in rt.pending:
    rt.pending[k][0] = 0
rt.flush_pending()
with db.engine.connect() as c:
    n4 = c.execute(sa.select(sa.func.count()).select_from(agent_run_t).where(agent_run_t.c.agent == aid)).scalar()
ok('人员原因的停机（等待上下料）不建维修单', n4 == n3)
ev = api.events(aid, user=ADMIN)
outc = {e['outcome'] for e in ev}
ok('触发记录（合并/冷却/启动/排除）', {'merged', 'cooldown', 'started', 'filtered'} <= outc, str(sorted(outc)))
# 通过采集入库的同一条路触发
from honeymom.ingest import ING  # noqa: E402
ING.reload()
with db.engine.connect() as c:
    eq = db.get(c, 'equipment', code='CNC6')
if eq:
    rt.last_fire.clear()
    ING.state[eq['id']] = ('Executing', db.clock.now(), 0)
    ING.on_tag(eq['id'], 'Common/Alarm', '主轴过载（测试）', db.clock.now(), 'T:CNC6/Common/Alarm')
    ING.on_tag(eq['id'], 'Common/State', 'Stopped', db.clock.now(), 'T:CNC6/Common/State')
    while rt.queue:
        k, pl = rt.queue.popleft()
        rt.dispatch(k, pl)
    for k in rt.pending:
        rt.pending[k][0] = 0
    rt.flush_pending()
    with db.engine.connect() as c:
        r = c.execute(sa.select(agent_run_t).where(agent_run_t.c.agent == aid).order_by(agent_run_t.c.id.desc()).limit(1)).mappings().first()
    ok('设备状态变成停机 → 采集入库 → 触发智能体（带上当前报警）', r and r['inputs'].get('equipment_code') == 'CNC6' and r['inputs'].get('alarm') == '主轴过载（测试）',
       r and f"{r['trigger_key']} {r['inputs'].get('alarm')}")
    st = wait_run(r['id'])
    ok('触发的运行停在设备员审批', st == 'waiting', st)

# ---------------- 右下角助手：工具按角色给、拒绝作为结果、数字核对、令牌 ----------------
from honeymom.services import assistant as AS  # noqa: E402

QC = {'username': 'qc', 'name': '质检员', 'role_list': ['qc']}


def names(u):
    return {t['function']['name'] for t in AS.tools_for(u)[0]}


ok('质检员没有建维修工单的写工具，有建不合格品的', 'propose_repair_order' not in names(QC) and 'propose_ncr' in names(QC))
ok('设备员有建维修工单的写工具', 'propose_repair_order' in names(MAINT))
res, ev = AS._call(OPER, 1, 'go_to_page', {'path': '/system'}, [], [])
ok('带你去看：操作工打不开系统页（拒绝作为结果返回）', 'refused' in res, res)
res, ev = AS._call(OPER, 1, 'go_to_page', {'path': '/equipment/oee?eid=3'}, [], [])
ok('带你去看：白名单里的页面', ev and ev[0][0] == 'navigate' and ev[0][1]['label'] == 'OEE 分析')
res, ev = AS._call(QC, 1, 'propose_repair_order', {'equipment': 'CNC1', 'fault': 'x'}, [], [])
ok('模型硬调没授权的写工具会被拒绝', 'refused' in res, res)
res, ev = AS._call(MAINT, 1, 'propose_repair_order', {'equipment': 'NOPE', 'fault': 'x'}, ['repair_order'], [])
ok('写工具先检查：设备不存在就不出确认卡', 'error' in res and not ev, res)
res, ev = AS._call(MAINT, 1, 'propose_repair_order', {'equipment': 'CNC1', 'fault': '主轴异响'}, ['repair_order'], [])
card = ev[0][1] if ev else {}
t = AS.read_token(card.get('token', ''))
ok('确认卡的令牌绑定内容和数据状态', t and t['fp'] == __import__('honeymom.services.ops', fromlist=['x']).fingerprint(card['payload']) and t['st'], t and t['st'])
ok('数字核对：查到的数（含百分比换算）不算编造', AS.ungrounded('CNC2 停机 18.7 分钟，OEE 87.3%', '{"minutes": 18.73, "OEE": 0.873}') == [])
ok('数字核对：没查过的数会被找出来', AS.ungrounded('今天产量 523 件', '{"n": 12}') == ['523'])

engine.POOL.shutdown(wait=False, cancel_futures=True)
bad = [n for n, g in results if not g]
print(f"\n{len(results) - len(bad)}/{len(results)} 通过", ('；失败：' + '、'.join(bad)) if bad else '')
sys.stdout.flush()
os._exit(1 if bad else 0)
