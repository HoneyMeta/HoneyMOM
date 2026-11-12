"""右下角 AI 助手（参照 CourseWright 的做法）：

- 会话按用户存库，可改名、删除；每条消息带当前页面和选中的记录。
- 过程用 SSE 推送：progress / tool / navigate / confirm / card / wrote / answer / error / done，界面只显示人话，不显示工具名。
- 只读工具（查库、指标、知识库、MES 查询）+ 带你去看（go_to_page，按角色白名单）+ 已发布且允许聊天调用的智能体（run_agent_<id>）。
- 写操作一律"先起草 → 给人看改动 → 审批后执行"：模型只能调用 propose_*，服务端校验后生成一张动作草稿（services/ai_drafts.py，
  显示改动前后对比，进审批中心），用户 通过 / 改一下再通过 / 驳回；通过时再校验一次、数据被改过就要求重新起草。没有权限的角色拿不到写工具。
  （旧的确认卡 + /api/assistant/commit 仍然保留，历史会话里的卡片可用。）
- 回答 = 文字 + 结构化块（图表、表格、指标卡、单据标签、数据来源），协议见 services/ai_blocks.py；SSE 多一个 blocks 事件和 draft 事件。
- 回答里出现的数字（含图表 / 表格里的）必须能在查询结果里找到，否则让模型重查或去掉；块里对不上的数字标"未核实"。
"""
from ..i18n import _tr
from .. import i18n as _i18n
import base64
import hashlib
import hmac
import json
import re
import time
from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse

from .. import auth, db
from ..auth import current_user
from ..crud import attach_labels, out
from ..db import clock
from ..schema import BY_NAME, ENTITIES, audit_t, message_t, thread_t
from . import ai, ai_demo, ops

router = APIRouter()

# ---------------- 页面白名单 ----------------
PAGES = {'/': '工作台', '/screen': '车间大屏', '/production/gantt': '排产与进度', '/trace': '质量追溯', '/quality': '质量概览', '/quality/spc': 'SPC 分析',
         '/equipment/monitor': '设备实时监控', '/equipment/oee': 'OEE 分析', '/equipment/maint': '点检保养', '/warehouse': '库存', '/energy': '能耗分析',
         '/foundry/heats': '炉次分析', '/foundry/charge': '配料优化', '/machining/tools': '刀具监测', '/machining/defects': '机加不良统计',
         '/cross/backtrace': '缺陷回溯', '/cross/coplan': '铸机协同排产', '/ai/daily': '日报', '/ai/chat': '问数助手（整页）', '/ai/watch': 'AI 值守', '/ai/automations': '一句话自动化', '/agents': '智能体',
         '/approvals': '审批中心', '/andon': '安灯看板', '/system': '系统状态'}
ADMIN_ONLY = {'/system', '/e/sys_user', '/e/datasource', '/e/setting'}


def page_allowed(user, path):
    base = path.split('?')[0].rstrip('/') or '/'
    if base in ADMIN_ONLY and 'admin' not in user['role_list']:
        return None, _tr('这个页面只有管理员能看')
    from .. import perms
    if not perms.can(user, perms.page_perm(base)):
        return None, _tr('你没有这个页面的权限')
    if base in PAGES:
        return PAGES[base], None
    m = re.fullmatch(r'/e/(\w+)', base)
    if m and m.group(1) in BY_NAME:
        return BY_NAME[m.group(1)].label, None
    m = re.fullmatch(r'/agents/(\d+)', base)
    if m:
        return _tr('智能体编辑'), None
    return None, _tr('没有这个页面')


# ---------------- 令牌 ----------------
def _sign(body: str) -> str:
    return hmac.new(auth.secret(), ('assist|' + body).encode(), hashlib.sha256).hexdigest()[:40]


def make_token(user, op, payload, state, thread):
    body = base64.urlsafe_b64encode(json.dumps({'u': user['username'], 'op': op, 'fp': ops.fingerprint(payload), 'st': state, 'exp': int(time.time()) + 1800,
                                                'th': thread}).encode()).decode()
    return body + '.' + _sign(body)


def read_token(tok):
    try:
        body, sig = tok.split('.')
        if not hmac.compare_digest(_sign(body), sig):
            return None
        return json.loads(base64.urlsafe_b64decode(body))
    except Exception:
        return None


WRITE_OPS = ['repair_order', 'ncr', 'andon_raise', 'andon_close', 'material_request', 'schedule_proposal', 'schedule_change', 'param_adjust']


def _prop_schema(op):
    props, req = {}, []
    for k, lab, t, r in ops.OPS[op]['fields']:
        js = {'str': 'string', 'text': 'string', 'int': 'integer', 'float': 'number', 'bool': 'boolean', 'json': 'array'}[t]
        props[k] = {'type': js, 'description': lab}
        if t == 'json':
            props[k]['items'] = {'type': 'object'}
        if r:
            req.append(k)
    return {'type': 'object', 'properties': props, 'required': req}


def tools_for(user):
    from ..agent import tools as T
    from . import ai_tools               # 5.0 M6：统一工具层（时序、节拍分析、追溯、技能……，MCP 用同一份）；没有对应查看权限的不给
    tl = T.tool_schemas(['query_sql', 'day_kpi', 'kb_search', 'mes_query'] + [t.name for t in ai_tools.available(user, False) if t.name in ai_tools.ASSISTANT_TOOLS])
    tl.append({'type': 'function', 'function': {'name': 'go_to_page', 'description': _tr('带用户去看某个页面（会在界面上打开它）。path 例：/equipment/oee、/e/repair_order?id=12、/e/ncr?f_status=open、/agents、/approvals、/andon。'),
                                                'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'reason': {'type': 'string'}}, 'required': ['path']}}})
    from .ai_switch import enabled
    if enabled('agents'):
        tl.append({'type': 'function', 'function': {'name': 'list_approvals', 'description': _tr('列出当前用户可以处理的待审批事项（智能体提交的）。'), 'parameters': {'type': 'object', 'properties': {}}}})
    allowed = []
    for op in WRITE_OPS:
        if auth.can_write(user, ops.OPS[op]['entity']):
            allowed.append(op)
            tl.append({'type': 'function', 'function': {'name': f'propose_{op}', 'description': _tr('提议{label}：服务端先检查，再生成一张动作草稿（显示改动前后对比），用户审批通过后才真正执行。调用后不要说"已完成"，要说"已起草，请审批"。', label=ops.OPS[op]['label']), 'parameters': _prop_schema(op)}})
    agents = chat_agents() if enabled('agents') else []
    for a in agents:
        tl.append({'type': 'function', 'function': {'name': f"run_agent_{a['id']}", 'description': _tr('运行智能体「{name}」：{v}', name=a['name'], v=a['description'] or '')[:900],
                                                    'parameters': {'type': 'object', 'properties': {'query': {'type': 'string', 'description': _tr('交给智能体的问题或任务')}}, 'required': ['query']}}})
    return tl, allowed, agents


def chat_agents():
    from ..schema import agent_t, agent_version_t
    with db.engine.connect() as c:
        rows = [dict(r) for r in c.execute(sa.select(agent_t).where(agent_t.c.chat_callable.is_(True) & agent_t.c.enabled.is_(True)
                                                                    & agent_t.c.published_version.isnot(None))).mappings()]
        out_ = []
        for a in rows:
            v = c.execute(sa.select(agent_version_t).where(agent_version_t.c.id == a['published_version'])).mappings().first()
            if v:
                out_.append({**a, 'version': dict(v)})
    return out_


# ---------------- 会话 ----------------
def _thread(c, tid, user):
    t = c.execute(sa.select(thread_t).where(thread_t.c.id == tid)).mappings().first()
    if not t or t['user'] != user['username']:
        raise HTTPException(404, _tr('会话不存在'))
    return dict(t)


@router.get('/api/assistant/threads')
def threads(user=Depends(current_user)):
    with db.engine.connect() as c:
        rows = c.execute(sa.select(thread_t).where(thread_t.c.user == user['username']).order_by(thread_t.c.updated_at.desc()).limit(100)).mappings().all()
    return [{k: db.jsonable(v) for k, v in dict(r).items()} for r in rows]


@router.post('/api/assistant/threads')
def new_thread(body: dict = Body(default={}), user=Depends(current_user)):
    n = datetime.now().replace(microsecond=0)
    with db.engine.begin() as c:
        tid = c.execute(thread_t.insert().values(user=user['username'], title=(body.get('title') or _tr('新对话'))[:190], created_at=n, updated_at=n)).inserted_primary_key[0]
    return {'id': tid, 'title': body.get('title') or _tr('新对话')}


@router.patch('/api/assistant/threads/{tid}')
def rename_thread(tid: int, body: dict = Body(...), user=Depends(current_user)):
    with db.engine.begin() as c:
        _thread(c, tid, user)
        c.execute(thread_t.update().where(thread_t.c.id == tid).values(title=(body.get('title') or _tr('未命名'))[:190]))
    return {'ok': True}


@router.delete('/api/assistant/threads/{tid}')
def delete_thread(tid: int, user=Depends(current_user)):
    with db.engine.begin() as c:
        _thread(c, tid, user)
        c.execute(message_t.delete().where(message_t.c.thread == tid))
        c.execute(thread_t.delete().where(thread_t.c.id == tid))
    return {'ok': True}


@router.get('/api/assistant/threads/{tid}')
def thread_messages(tid: int, user=Depends(current_user)):
    with db.engine.connect() as c:
        t = _thread(c, tid, user)
        msgs = c.execute(sa.select(message_t).where(message_t.c.thread == tid).order_by(message_t.c.id)).mappings().all()
    return {'thread': {k: db.jsonable(v) for k, v in t.items()}, 'messages': [{k: db.jsonable(v) for k, v in dict(m).items()} for m in msgs]}


def _save_msg(tid, role, content, page=None, record=None, steps=None, receipts=None, cards=None):
    n = datetime.now().replace(microsecond=0)
    with db.engine.begin() as c:
        mid = c.execute(message_t.insert().values(thread=tid, role=role, content=content, page=page, record=record, steps=steps, receipts=receipts, cards=cards,
                                                  created_at=n)).inserted_primary_key[0]
        c.execute(thread_t.update().where(thread_t.c.id == tid).values(updated_at=n))
    return mid


# ---------------- 页面上下文 ----------------
def record_context(rec):
    """选中的记录：取出主要字段给模型（也作为数字的依据）。"""
    if not rec or rec.get('entity') not in BY_NAME or not rec.get('id'):
        return None
    with db.engine.connect() as c:
        r = db.get(c, rec['entity'], int(rec['id']))
        if not r:
            return None
        attach_labels(c, rec['entity'], [r])
    e = BY_NAME[rec['entity']]
    d = {}
    for f in e.fields:
        if f.name in ('password_hash',):
            continue
        v = r.get(f.name + '__label', r.get(f.name))
        if v not in (None, ''):
            d[f.label] = (f.enum or {}).get(v, db.jsonable(v)) if f.t == 'enum' else db.jsonable(v)
    return {'entity': e.label, 'id': r['id'], 'data': d}


def context_text(items):
    """页面上 Ctrl+点击 / 右键选中交给 AI 的元素（v-ai）：[{kind, id, label, data}] → 给模型的一段说明。"""
    if not items:
        return ''
    KIND = {'kpi': _tr('指标'), 'chart-point': _tr('图表数据点'), 'row': _tr('表格行'), 'device': _tr('设备'), 'record': _tr('记录'), 'field': _tr('字段'), 'alert': _tr('报警/提醒'),
            'text': _tr('选中的文字'), 'page': _tr('页面')}
    lines = [_tr('用户在页面上选中了这些内容交给你（以此为准，数字可直接引用）：')]
    for i, it in enumerate(items[:12], 1):
        if not isinstance(it, dict):
            continue
        d = it.get('data')
        lines.append(f"{i}. [{KIND.get(it.get('kind'), it.get('kind') or _tr('元素'))}] {it.get('label') or ''}"
                     + (_tr('（id {id}）', id=it['id']) if it.get('id') not in (None, '') else '')
                     + ((_tr('：') + json.dumps(d, ensure_ascii=False, default=str)[:900]) if d not in (None, '', {}) else ''))
    return '\n'.join(lines)


def system_prompt(user, page, page_title, rec_ctx, allowed_writes, agents, ctx_items=None):
    roles = _tr('、').join(ai_role(r) for r in user['role_list'])
    lines = [_tr('你是"蜂元智能 MOM"右下角的 AI 助手，服务于一家铸造 + 机加工厂。当前用户：{v}（角色：{roles}）。', v=user["name"] or user["username"], roles=roles),
             _tr('当前数据时钟：{now:%Y-%m-%d %H:%M}（"今天""昨天"按这个算）。', now=clock.now()),
             _tr('用户当前所在页面：{v}（{v2}）。', v=page_title or "", v2=page or "/") + (_tr('页面上选中的记录：{entity} #{id}：{v}', entity=rec_ctx["entity"], id=rec_ctx["id"], v=json.dumps(rec_ctx["data"], ensure_ascii=False)[:2500]) if rec_ctx else ''),
             _tr('规则：'),
             _tr('1. 涉及数字（产量、OEE、件数、时长、金额等）必须先用工具查，回答里的数字只能来自工具结果或上面给出的记录；查不到就说查不到，不要估计。'),
             _tr('2. 回答用简洁中文，先结论后依据；适合时用 Markdown 列表或表格。'),
             _tr('3. 用户想看某个页面或你想让他看依据时，用 go_to_page 带他去。'),
             (_tr('4. 需要新建或修改数据时，只能调用 propose_* 工具提议，系统会给用户一张确认卡；用户确认前不要说"已完成"。当前用户的角色可以提议：')
              + _tr('、').join(ops.OPS[o]['label'] for o in allowed_writes) + _tr('。') if allowed_writes else _tr('4. 当前用户的角色没有写权限：只能查询和解释，不要承诺去修改数据。'))
             + (_tr('其它写操作（') + _tr('、').join(ops.OPS[o]['label'] for o in WRITE_OPS if o not in allowed_writes) + _tr('）当前角色没有权限，用户要求时直接说明"你的角色没有这个权限"，并建议找有权限的人（如设备员、质检员、计划员、仓管员、班组长）处理。')
                if len(allowed_writes) < len(WRITE_OPS) else ''),
             _tr('5. 不替人做签字类决定（审批、发布排程、改真实设备参数），只给建议。')]
    if ctx_items:
        lines.insert(3, context_text(ctx_items))
    if agents:
        lines.append(_tr('6. 可以调用这些已发布的智能体（run_agent_<id>）：') + _tr('；').join(f"{a['id']}={a['name']}" for a in agents) + _tr('。智能体的结果会以卡片显示。'))
    from .ai_blocks import PROMPT
    lines.append(PROMPT)
    lines.append(_tr('查数据库时：') + ai.dialect_hint() + _tr('\n只能查你（提问人）有权限的表和数据范围内的数据。数据表：\n') + ai.schema_text())
    return '\n'.join(lines)


def ai_role(r):
    from ..schema import ROLES
    return ROLES.get(r, r)


# ---------------- 数字核对 ----------------
NUM = re.compile(r'(?<![\w.])(\d+(?:\.\d+)?)')


def _nums(text):
    out_ = []
    for m in NUM.finditer(text or ''):
        try:
            out_.append(float(m.group(1)))
        except ValueError:
            pass
    return out_


def ungrounded(answer, corpus):
    have = _nums(corpus)
    hs = set(have)
    bad = []
    for m in NUM.finditer(re.sub(r'\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?|\d{1,2}:\d{2}', ' ', answer or '')):
        s = m.group(1)
        n = float(s)
        if n <= 10 and '.' not in s:
            continue
        if n in hs or s in corpus:
            continue
        if any(abs(c - n) <= max(0.011 * abs(n), 0.051) or abs(c * 100 - n) <= 0.6 or abs(c / 60 - n) <= 0.6 or abs(c / 3600 - n) <= 0.06 for c in have):
            continue
        bad.append(s)
    return bad[:6]


# ---------------- 对话 ----------------
def sse(kind, data):
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post('/api/assistant/chat')
def chat(body: dict = Body(...), user=Depends(current_user)):
    text = (body.get('text') or '').strip()
    if not text:
        raise HTTPException(400, _tr('请输入内容'))
    tid = body.get('thread_id')
    with db.engine.begin() as c:
        if tid:
            t = _thread(c, int(tid), user)
        else:
            n = datetime.now().replace(microsecond=0)
            tid = c.execute(thread_t.insert().values(user=user['username'], title=text[:30], created_at=n, updated_at=n)).inserted_primary_key[0]
            t = {'id': tid, 'title': text[:30]}
        if t.get('title') in (None, '', *_i18n.variants('新对话')):
            c.execute(thread_t.update().where(thread_t.c.id == tid).values(title=text[:30]))
            t['title'] = text[:30]
        hist = [dict(m) for m in c.execute(sa.select(message_t.c.role, message_t.c.content).where(message_t.c.thread == tid).order_by(message_t.c.id.desc()).limit(12)).mappings()]
    hist.reverse()
    page, page_title, rec = body.get('page') or '/', body.get('page_title') or '', body.get('record')
    ctx_items = [x for x in (body.get('context') or []) if isinstance(x, dict)][:12]
    if not rec:              # 选中的第一条记录（kind=record/row/device 且带 entity）也当作"选中的记录"
        rec = next(({'entity': x['entity'], 'id': x['id'], 'label': x.get('label')} for x in ctx_items if x.get('entity') and x.get('id')), None)
    _save_msg(tid, 'user', text, page=page, record={**(rec or {}), 'context': ctx_items} if ctx_items else rec)
    demo_key = {'text': text, 'context': [x.get('label') for x in ctx_items], 'action': body.get('action') or ''}
    # 演示回放的相似匹配：带了页面元素（解释 / 为什么变化 / 对比…）时，录制里必须是同样的指标 / 对象（去掉数字比较），否则回放别的指标的解释就错了
    want = sorted(_ctx_name(x) for x in demo_key['context'])
    accept = (lambda req: sorted(_ctx_name(x) for x in (req.get('context') or [])) == want) if want else None
    gen = ai_demo.run_stream('ask', 'chat', demo_key, lambda: _run(user, tid, t['title'], text, page, page_title, rec, hist, ctx_items),
                             delay=0.35, accept=accept)
    return StreamingResponse(_demo_wrap(gen, tid, t['title'], user, ctx_items), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


def _ctx_name(label):
    return re.sub(r'[\d.,，%％+\-−×/]+', '', str(label or '')).replace(' ', '')


def _demo_wrap(gen, tid, title, user=None, ctx_items=None):
    """(event, data) → SSE 文本；回放的录制没有 done 事件，这里补上（并把回答存进会话）。
    propose 事件（模型提议的写操作）在这里变成一张真正的动作草稿（draft 事件）——录制和回放都走这里，所以回放时也会生成草稿。
    回放时单据引用按编号重新在当前库里找（录制环境的 id 不一定对得上）。"""
    from . import ai_blocks, ai_drafts
    answer, done, drafts, blocks_ev = '', False, [], None
    for ev, data in gen:
        if ev == 'answer':
            answer = (data or {}).get('text') or ''
        if ev == 'done':
            done = True
            if drafts and (data or {}).get('message_id'):
                _add_cards(data['message_id'], [{'kind': 'draft', 'draft': d} for d in drafts])
        if ev == 'propose':
            try:
                d = ai_drafts.create(user, data['op'], data.get('payload') or {}, source='assistant', thread=tid, context=ctx_items,
                                     summary=(data.get('summary') or '')[:1000])
                drafts.append(d)
                yield sse('draft', d)
            except Exception as ex:
                yield sse('error', {'text': _tr('起草没成功：{ex}', ex=ex)})
            continue
        if ev == 'blocks' and not done and data is not None:
            blocks_ev = data
            if data.get('replay'):
                data = {**data, 'refs': ai_blocks.find_refs(answer)}
                for b in data.get('blocks') or []:
                    if b.get('type') == 'refs':
                        with db.engine.connect() as c:
                            b['items'] = [r for r in (ai_blocks.resolve_ref(c, x.get('entity'), x.get('code')) for x in b['items']) if r]
                data['blocks'] = ai_blocks.enrich([b for b in data.get('blocks') or [] if b.get('type') != 'refs' or b.get('items')])
                blocks_ev = data
        yield sse(ev, data)
    if not done:
        cards = ([{'kind': 'blocks', 'blocks': blocks_ev.get('blocks') or [], 'refs': blocks_ev.get('refs') or []}] if blocks_ev else []) + [{'kind': 'draft', 'draft': d} for d in drafts]
        mid = _save_msg(tid, 'assistant', answer + _tr('\n\n（演示模式：录制的回答）'), cards=cards or None)
        yield sse('done', {'message_id': mid, 'thread_id': tid, 'title': title, 'steps': [], 'demo': True})


def _add_cards(mid, extra):
    with db.engine.begin() as c:
        cur = c.execute(sa.select(message_t.c.cards).where(message_t.c.id == mid)).scalar() or []
        c.execute(message_t.update().where(message_t.c.id == mid).values(cards=list(cur) + extra))


def _run(user, tid, title, text, page, page_title, rec, hist, ctx_items=None):
    from ..agent import engine as E
    from ..agent import tools as T

    def sse(kind, data):                 # 这里产出 (事件, 数据)，由 chat() 里的 _demo_wrap 转成 SSE 文本（演示模式要录制/回放）
        return kind, data
    steps, receipts, cards = [], [], []
    answer = ''
    try:
        yield sse('progress', {'text': _tr('正在理解你的问题…')})
        rec_ctx = record_context(rec)
        tl, allowed, agents = tools_for(user)
        if not ai.llm_available():
            ans = ai.rule_answer(text)
            yield sse('answer', {'text': ans})
            k = ai.day_kpi()
            blk = [{'id': 'b1', 'type': 'kpis', 'items': [{'label': _tr('浇注 / 来料'), 'value': k['production']['castings'], 'format': 'int', 'unit': '件'},
                                                       {'label': _tr('机加'), 'value': k['production']['machined'], 'format': 'int', 'unit': '件'},
                                                       {'label': _tr('入库'), 'value': k['production']['stored'], 'format': 'int', 'unit': '件'},
                                                       {'label': _tr('废品率'), 'value': k['quality']['scrap_rate'], 'format': 'pct', 'good': 'down'}]},
                   {'id': 'b2', 'type': 'sources', 'items': [{'id': 'q1', 'kind': 'kpi', 'label': _tr('{day} 汇总指标（规则统计，未用大模型）', day=k['day'])}]}]
            yield sse('blocks', {'blocks': blk, 'refs': []})
            cards.append({'kind': 'blocks', 'blocks': blk, 'refs': []})
            mid = _save_msg(tid, 'assistant', ans, cards=cards)
            yield sse('done', {'message_id': mid, 'thread_id': tid, 'title': title})
            return
        msgs = [{'role': 'system', 'content': system_prompt(user, page, page_title, rec_ctx, allowed, agents, ctx_items)}]
        msgs += [{'role': m['role'], 'content': m['content'] or ''} for m in hist if m['role'] in ('user', 'assistant') and m['content']][-10:]
        from .ai_blocks import REMIND
        msgs.append({'role': 'system', 'content': REMIND})
        msgs.append({'role': 'user', 'content': text})
        corpus = [text, json.dumps(rec_ctx, ensure_ascii=False) if rec_ctx else '', context_text(ctx_items)] + [m['content'] or '' for m in hist]
        nudged = False
        for rnd in range(8):
            try:
                m, _tk = ai.llm_chat(msgs, tools=tl)
            except Exception:
                yield sse('progress', {'text': _tr('大模型响应慢，正在重试…')})
                time.sleep(2)
                m, _tk = ai.llm_chat(msgs, tools=tl)
            calls = m.get('tool_calls') or []
            if not calls:
                answer = m.get('content') or ''
                bad = ungrounded(answer, '\n'.join(corpus))
                if bad and not nudged and rnd < 7:
                    nudged = True
                    yield sse('progress', {'text': _tr('正在核对回答里的数字…')})
                    msgs.append({'role': 'assistant', 'content': answer})
                    msgs.append({'role': 'user', 'content': _tr('（系统检查）你的回答里这些数字在查询结果里找不到依据：{join}。请先调用工具查到再回答，或者去掉这些数字。不要提到这条检查。', join=_tr('、').join(bad))})
                    continue
                if bad:
                    answer += _tr('\n\n> 提示：回答里个别数字（') + _tr('、').join(bad) + _tr('）没有在查询结果里找到依据，请以页面数据为准。')
                break
            msgs.append({'role': 'assistant', 'content': m.get('content') or '', 'tool_calls': calls})
            for tc in calls:
                fn = tc['function']['name']
                try:
                    args = json.loads(tc['function'].get('arguments') or '{}')
                except ValueError:
                    args = {}
                ph = T.phrase(fn, args)
                if fn.startswith('run_agent_'):
                    a = next((x for x in agents if f"run_agent_{x['id']}" == fn), None)
                    ph = _tr('正在运行智能体「{name}」…', name=a['name']) if a else ph
                yield sse('tool', {'text': ph})
                t0 = time.time()
                res, events = _call(user, tid, fn, args, allowed, agents)
                for ev in events:
                    if ev[0] == 'card':
                        cards.append(ev[1])
                    if ev[0] == 'wrote':
                        receipts.append(ev[1])
                    yield sse(*ev)
                content = T.dumps(res)
                corpus.append(content)
                steps.append({'tool': fn, 'phrase': ph, 'args': args, 'result': content[:3000], 'ms': int((time.time() - t0) * 1000),
                              'status': 'failed' if isinstance(res, dict) and (res.get('error') or res.get('refused')) else 'done'})
                msgs.append({'role': 'tool', 'tool_call_id': tc['id'], 'content': content})
        else:
            answer = answer or _tr('查询步骤太多，没能得出结论，请把问题问得具体一些。')
        from . import ai_blocks
        comp = ai_blocks.compose(answer, steps, '\n'.join(corpus), ctx_items)
        answer = comp['text']
        yield sse('answer', {'text': answer})
        if comp['blocks'] or comp['refs']:
            yield sse('blocks', {'blocks': comp['blocks'], 'refs': comp['refs'], 'replay': True})
            cards.append({'kind': 'blocks', 'blocks': comp['blocks'], 'refs': comp['refs']})
    except Exception as ex:
        answer = answer or _tr('出错了：{ex}', ex=ex)
        yield sse('error', {'text': _tr('大模型或查询出错：{ex}', ex=ex)})
    mid = _save_msg(tid, 'assistant', answer, steps=steps or None, receipts=receipts or None, cards=cards or None)
    with db.engine.begin() as c:
        db.insert(c, 'ai_log', {'user': user['username'], 'question': text, 'answer': answer,
                                'sqls': '\n'.join(s['args'].get('sql', '') for s in steps if s['tool'] == 'query_sql'), 'time': clock.now()}, raw=True)
    yield sse('done', {'message_id': mid, 'thread_id': tid, 'title': title, 'steps': steps})


def _call(user, tid, fn, args, allowed, agents):
    """执行一个工具调用：返回 (给模型的结果, [界面事件])。拒绝和错误都作为结果返回给模型，不抛 500。"""
    from ..agent import tools as T
    try:
        if fn in T.DATA_TOOLS:
            return T.call(fn, args), []
        if fn == 'go_to_page':
            path = str(args.get('path') or '/')
            label, err = page_allowed(user, path)
            if err:
                return {'refused': err}, []
            return {'ok': True, 'opened': label}, [('navigate', {'path': path, 'label': label, 'reason': args.get('reason') or ''})]
        if fn == 'list_approvals':
            from ..agent.api import approvals
            rows = approvals(status='pending', limit=20, run=0, user=user)
            return {'count': len(rows), 'items': [{'id': r['id'], 'title': r['title'], 'agent': r['agent_name'], 'role': r['role_label'], 'can_decide': r['can_decide']}
                                                  for r in rows]}, ([('card', {'kind': 'approvals', 'count': len(rows)})] if rows else [])
        if fn.startswith('propose_'):
            op = fn[len('propose_'):]
            if op not in ops.OPS:
                return {'refused': _tr('没有这个操作')}, []
            if op not in allowed or not auth.can_write(user, ops.OPS[op]['entity']):
                return {'refused': _tr('当前角色没有“{label}”的权限。请告诉用户需要找有权限的人处理。', label=ops.OPS[op]['label'])}, []
            with db.engine.connect() as c:
                try:
                    p = ops.validate(c, op, args)
                except ops.OpError as ex:
                    return {'error': _tr('检查没通过：{ex}', ex=ex)}, []
                st = ops.state_of(c, op, p)
            # 生成动作草稿（在 _demo_wrap 里落库，回放录制时也能生成）：显示改动前后对比，审批通过后才执行
            return {'ok': True, 'status': '已生成动作草稿，等待用户审批（通过 / 修改后通过 / 驳回）；在用户通过之前不要说已经完成。', 'preview': ops.preview(op, p)}, \
                [('propose', {'op': op, 'payload': p, 'summary': str(args.get('reason') or args.get('cause') or args.get('description') or '')[:300]})]
        if fn.startswith('run_agent_'):
            a = next((x for x in agents if f"run_agent_{x['id']}" == fn), None)
            if not a:
                return {'refused': _tr('这个智能体没有发布或不允许在聊天里调用')}, []
            return _run_agent(user, a, args)
        return {'error': _tr('没有这个工具：{fn}', fn=fn)}, []
    except Exception as ex:
        return {'error': str(ex)}, []


def _run_agent(user, a, args):
    from ..agent import engine as E
    from ..agent.api import run_detail
    v = a['version']
    inputs = {'query': args.get('query') or ''}
    rid = E.start_run(a['id'], v['dsl'], version=v['id'], mode='live', trigger='chat', inputs=inputs, user=user['username'], run_as=v['published_by'] or 'admin',
                      wait=120)
    d = run_detail(rid, user)
    reply = (d['output'] or {}).get('reply') or ''
    recs = (d['output'] or {}).get('receipts') or []
    pend = [x for x in d['approvals'] if x['status'] == 'pending']
    card = {'kind': 'agent_run', 'run_id': rid, 'agent_id': a['id'], 'agent_name': a['name'], 'status': d['status'], 'reply': reply[:4000], 'receipts': recs,
            'approval': {'id': pend[0]['id'], 'title': pend[0]['title']} if pend else None, 'error': d['error']}
    evs = [('card', card)] + [('wrote', r) for r in recs]
    res = {'status': d['status'], 'reply': reply[:6000], 'receipts': [r.get('text') for r in recs], 'error': d['error'],
           'waiting_approval': pend[0]['title'] if pend else None}
    if d['status'] in ('queued', 'running'):
        res['note'] = _tr('智能体还在运行，结果会显示在卡片里')
    return res, evs


# ---------------- 提交 ----------------
@router.post('/api/assistant/commit')
def commit(body: dict = Body(...), user=Depends(current_user)):
    tok = body.get('token') or ''
    t = read_token(tok)
    if not t:
        raise HTTPException(400, _tr('确认令牌无效'))
    if t['u'] != user['username']:
        raise HTTPException(403, _tr('这张确认卡不是你的'))
    if t['exp'] < time.time():
        raise HTTPException(400, _tr('确认卡已过期，请让助手重新检查'))
    op = t['op']
    if not auth.can_write(user, ops.OPS[op]['entity']):
        raise HTTPException(403, _tr('当前角色没有“{label}”的权限', label=ops.OPS[op]['label']))
    sig = tok.split('.')[-1]
    with db.engine.begin() as c:
        if c.execute(sa.select(audit_t.c.id).where((audit_t.c.kind == 'assistant_commit') & (audit_t.c.target == sig))).first():
            raise HTTPException(400, _tr('这张确认卡已经提交过了'))
        try:
            p = ops.validate(c, op, body.get('payload') or {})
        except ops.OpError as ex:
            raise HTTPException(400, str(ex))
        if ops.fingerprint(p) != t['fp']:
            raise HTTPException(409, _tr('提交的内容和检查过的不一致，请让助手重新检查'))
        if ops.state_of(c, op, p) != t['st']:
            raise HTTPException(409, _tr('相关数据在检查之后被改过了，请让助手重新检查'))
        from .. import context
        with context.source('ai'):                # AI 起草、用户确认后提交：审计来源记"AI"
            rec = ops.execute(c, op, p, user['name'] or user['username'], 'assistant')
        rec['op'] = op
        ops.audit(c, user['username'], 'assistant_commit', sig, {'op': op, 'payload': p, 'receipt': rec, 'thread': t.get('th')})
    if t.get('th'):
        try:
            _save_msg(int(t['th']), 'receipt', rec['text'], receipts=[rec] + (rec.get('extra') or []))
        except Exception:
            pass
    return {'ok': True, 'receipt': rec}
