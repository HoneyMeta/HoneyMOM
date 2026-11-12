"""智能体执行器：按画布连线执行节点，每步落库；审批/填写节点把运行挂起为"等待中"，处理完再接着跑。

DSL（逻辑和布局分开，参照 RAGFlow）：
{
  "components": {节点ID: {"type", "name", "params": {...}, "upstream": [ID], "downstream": [{"target": ID, "handle": 出口名或 null}]}},
  "graph": {"nodes": {ID: {"x", "y"}}, "edges": [{"id", "source", "target", "sourceHandle"}]}
}
变量：{节点ID@输出路径}、{sys.query} {sys.now} {sys.feedback} …、{inputs.字段}、{vars.名字}。
"""
from ..i18n import _tr
import json
import logging
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import sqlalchemy as sa

from .. import auth, db
from ..db import clock
from ..schema import agent_action_t, agent_run_t, agent_step_t, agent_t, approval_t, audit_t
from ..services import ai, ops
from .nodes import TYPES, NodeError, Rejected, Result, handles_of

log = logging.getLogger('mes.agent')
RESOLVED = ('done', 'skipped', 'failed')
POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='agent')
LLM_HOOK = None          # 测试可替换：fn(messages, tools, json_mode) -> (message, tokens)
MAX_LOOPS = 5            # 驳回后回到前面节点重做的最多次数
_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()
_done_events: dict[int, threading.Event] = {}


def now():
    return datetime.now().replace(microsecond=0)


class LLMUnavailable(Exception):
    pass


# ---------------- 变量 ----------------
REF = re.compile(r'\{([A-Za-z_][\w]*)@([^{}"]*)\}|\{(sys|inputs|vars)\.([^{}"]+)\}')


def get_path(obj, path):
    if path in (None, ''):
        return obj
    cur = obj
    for part in re.split(r'\.|\[(\d+)\]', path):
        if part in (None, ''):
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, (list, tuple)) and re.fullmatch(r'-?\d+', part):
            i = int(part)
            cur = cur[i] if -len(cur) <= i < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


def _lookup(m, scope):
    if m.group(1):
        return get_path(scope['outputs'].get(m.group(1)), m.group(2).strip())
    ns, path = m.group(3), m.group(4).strip()
    return get_path(scope[ns], path)


def _to_text(v):
    if v is None:
        return ''
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def resolve(value, scope):
    """字符串里正好是一个引用 → 原值（列表、对象原样传）；否则替换成文字。对象和列表递归处理。"""
    if isinstance(value, str):
        m = REF.fullmatch(value.strip())
        if m:
            return _lookup(m, scope)
        return REF.sub(lambda mm: _to_text(_lookup(mm, scope)), value)
    if isinstance(value, list):
        return [resolve(v, scope) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, scope) for k, v in value.items()}
    return value


def resolve_params(comp, scope):
    meta = TYPES[comp['type']]
    raw = dict(comp.get('params') or {})
    out = {}
    for spec in meta['params'] + [{'key': 'default_output', 'type': 'json'}]:
        k = spec['key']
        if k not in raw:
            if 'default' in spec:
                raw[k] = spec['default']
            else:
                continue
        v = raw[k]
        if spec['type'] == 'json' and isinstance(v, str):
            s = v.strip()
            try:
                v = json.loads(s) if s else None
            except ValueError:
                v = resolve(s, scope)
                if isinstance(v, str):
                    try:
                        v = json.loads(v)
                    except ValueError:
                        pass
                out[k] = v
                continue
        out[k] = resolve(v, scope) if spec.get('vars') or spec['type'] in ('json', 'kv', 'cases', 'categories', 'fields', 'text', 'textarea') else v
    for k, v in raw.items():
        out.setdefault(k, v)
    return out


def refs_in(value):
    """找出参数里引用到的节点 ID。"""
    s = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return {m.group(1) for m in REF.finditer(s) if m.group(1)}


# ---------------- 图 ----------------
class Graph:
    def __init__(self, dsl):
        self.comps = dsl.get('components') or {}
        self.out = {n: [(d['target'], d.get('handle') or None) for d in (c.get('downstream') or []) if d.get('target') in self.comps]
                    for n, c in self.comps.items()}
        self.inc = {n: [] for n in self.comps}
        for s, lst in self.out.items():
            for t, h in lst:
                self.inc[t].append((s, h))
        # 回边（驳回后回到前面节点）：从"开始"深搜，指向祖先的边
        self.back = set()
        color = {}

        def dfs(n):
            color[n] = 1
            for t, _ in self.out.get(n, []):
                if color.get(t) == 1:
                    self.back.add((n, t))
                elif t not in color:
                    dfs(t)
            color[n] = 2
        if 'begin' in self.comps:
            dfs('begin')
        for n in self.comps:
            if n not in color:
                dfs(n)
        self.fwd_out = {n: [(t, h) for t, h in lst if (n, t) not in self.back] for n, lst in self.out.items()}
        self.fwd_up = {n: [s for s, _ in lst if (s, n) not in self.back] for n, lst in self.inc.items()}
        # 拓扑序
        indeg = {n: len(set(self.fwd_up[n])) for n in self.comps}
        q = sorted([n for n, d in indeg.items() if d == 0], key=lambda n: (n != 'begin', n))
        self.order = []
        seen = set()
        while q:
            n = q.pop(0)
            if n in seen:
                continue
            seen.add(n)
            self.order.append(n)
            for t in sorted({t for t, _ in self.fwd_out[n]}):
                indeg[t] -= 1
                if indeg[t] <= 0:
                    q.append(t)
        self.order += [n for n in self.comps if n not in seen]
        # 循环体
        self.loop_region = {}
        for n, c in self.comps.items():
            if c.get('type') == 'loop':
                body = self.reach([t for t, h in self.fwd_out[n] if h == 'body'], stop={n})
                done = self.reach([t for t, h in self.fwd_out[n] if h == 'done'], stop={n})
                self.loop_region[n] = [x for x in self.order if x in body and x not in done]
        self.owned = {x: n for n, reg in self.loop_region.items() for x in reg}

    def reach(self, starts, stop=()):
        seen, st = set(), list(starts)
        while st:
            n = st.pop()
            if n in seen or n in stop:
                continue
            seen.add(n)
            st += [t for t, _ in self.fwd_out.get(n, [])]
        return seen

    def ancestors(self, n):
        seen, st = set(), list(self.fwd_up.get(n, []))
        while st:
            x = st.pop()
            if x in seen:
                continue
            seen.add(x)
            st += self.fwd_up.get(x, [])
        return seen

    def approved_upstream(self, n):
        """节点是否在某个审批节点"通过"出口的下游。"""
        for a, c in self.comps.items():
            if c.get('type') == 'approval':
                if n in self.reach([t for t, h in self.fwd_out[a] if h == 'approved']):
                    return True
        return False


# ---------------- 校验 ----------------
CRON_RE = re.compile(r'^\s*\S+\s+\S+\s+\S+\s+\S+\s+\S+\s*$')


def validate(dsl, permissions=None):
    probs = []

    def p(node, level, msg):
        probs.append({'node': node, 'level': level, 'msg': msg})
    comps = (dsl or {}).get('components') or {}
    if 'begin' not in comps or comps['begin'].get('type') != 'begin':
        p(None, 'error', _tr('缺少"开始"节点'))
        return probs
    if sum(1 for c in comps.values() if c.get('type') == 'begin') > 1:
        p(None, 'error', _tr('只能有一个"开始"节点'))
    g = Graph(dsl)
    reach = g.reach(['begin'])
    bp = comps['begin'].get('params') or {}
    tr = bp.get('trigger') or 'manual'
    if tr == 'event' and not bp.get('event'):
        p('begin', 'error', _tr('请选择触发事件'))
    if tr == 'schedule' and (bp.get('schedule_mode') or 'cron') == 'cron':
        from .runtime import cron_ok
        if not cron_ok(bp.get('cron') or ''):
            p('begin', 'error', _tr('Cron 表达式不对（5 段：分 时 日 月 周）'))
    for n, c in comps.items():
        t = c.get('type')
        name = c.get('name') or n
        if t not in TYPES:
            p(n, 'error', _tr('未知节点类型 {t}', t=t))
            continue
        meta = TYPES[t]
        if n not in reach:
            p(n, 'warn', _tr('"{name}" 没有连到流程里，不会执行', name=name))
        prm = c.get('params') or {}
        for spec in meta['params']:
            if spec.get('required') and _shown(spec, prm) and prm.get(spec['key']) in (None, '', [], {}):
                p(n, 'error', _tr('"{name}" 的"{label}"没填', name=name, label=spec["label"]))
        if handles_of(t, prm) and not g.out[n]:
            p(n, 'warn', _tr('"{name}" 的出口还没有连线', name=name))
        # 变量引用
        anc = g.ancestors(n) | {n}
        for r in refs_in(prm):
            if r not in comps:
                p(n, 'error', _tr('"{name}" 引用了不存在的节点 {r}', name=name, r=r))
            elif r not in anc and r not in g.loop_region.get(n, []) and g.owned.get(n) != r:
                p(n, 'warn', _tr('"{name}" 引用的 {v} 不在它的上游，运行时可能取不到值', name=name, v=comps[r].get("name") or r))
        if meta.get('op'):
            op = meta['op']
            if permissions is not None and op not in permissions:
                p(n, 'error', _tr('"{name}"：本智能体的权限白名单里没有"{label}"（在"设置"里勾选）', name=name, label=ops.OPS[op]["label"]))
            if meta.get('risk') == 'high' and not prm.get('require_approval', True) and not g.approved_upstream(n):
                p(n, 'error', _tr('"{name}" 是写操作：要么放在审批节点"通过"出口后面，要么打开"执行前需要审批"', name=name))
        if t in ('approval', 'ask_user') and n in g.owned:
            p(n, 'error', _tr('"{name}"：循环体里不能放审批/填写节点', name=name))
        if prm.get('on_error') == 'goto' and prm.get('error_goto') not in comps:
            p(n, 'error', _tr('"{name}"：失败后要跳转的节点不存在', name=name))
    return probs


def _shown(spec, prm):
    for k, vals in (spec.get('show') or {}).items():
        if prm.get(k, None) not in vals:
            return False
    return True


# ---------------- 运行 ----------------
class Ctx:
    LLMUnavailable = LLMUnavailable

    def __init__(self, ex, nid, resume):
        self.ex, self.nid, self.resume = ex, nid, resume
        st = ex.st
        self.inputs, self.sys, self.vars, self.replies = st['inputs'], st['sys'], st['vars'], st['replies']
        self.mode = ex.mode

    def llm(self, msgs, tools=None, json_mode=False, temperature=0.2):
        if LLM_HOOK:
            return LLM_HOOK(msgs, tools, json_mode)
        # 演示模式：回放录制的大模型回复（按智能体 + 节点 + 最后一条提问匹配）；录制模式：真实调用后录下来
        from ..services import ai_demo, ai_switch
        if ai_switch.demo() or ai_switch.recording():
            key = {'agent': str(self.ex.row.get('agent')), 'node': self.nid, 'tools': bool(tools),
                   'prompt': next((str(m.get('content') or '')[:400] for m in reversed(msgs) if m.get('role') in ('user', 'tool')), '')}
            if ai_switch.demo():
                rec = ai_demo.lookup('agents', 'llm', key)
                if rec:
                    return rec[0], 0
            else:
                res = self._llm_live(msgs, tools, json_mode, temperature)
                try:
                    ai_demo.save('agents', 'llm', key, [res[0], res[1]])
                except OSError:
                    pass
                return res
        return self._llm_live(msgs, tools, json_mode, temperature)

    def _llm_live(self, msgs, tools=None, json_mode=False, temperature=0.2):
        if not ai.llm_available():
            raise LLMUnavailable(_tr('没有配置大模型'))
        err = None
        for attempt in range(3):      # 网络抖动重试；JSON 模式不被支持时第二次起改用普通模式
            try:
                return ai.llm_chat(msgs, tools=tools, temperature=temperature, json_mode=json_mode and attempt == 0)
            except Exception as ex:
                err = ex
                if getattr(ex, 'code', None) in (401, 403):
                    break
                time.sleep(1.5 * (attempt + 1))
        raise LLMUnavailable(_tr('大模型调用失败：{err}', err=err))

    def do_action(self, node, op, payload, params):
        return self.ex.do_action(self.nid, op, payload, params, self.resume)


class Executor:
    def __init__(self, run_id, single=None):
        self.run_id = run_id
        with db.engine.connect() as c:
            self.row = dict(c.execute(sa.select(agent_run_t).where(agent_run_t.c.id == run_id)).mappings().first())
            self.agent = dict(c.execute(sa.select(agent_t).where(agent_t.c.id == self.row['agent'])).mappings().first() or {})
        self.dsl = self.row['dsl']
        self.g = Graph(self.dsl)
        self.mode = self.row['mode']
        self.st = self.row['state'] or {}
        self.single = single
        bp = (self.g.comps.get('begin') or {}).get('params') or {}
        self.max_steps = int(bp.get('max_steps') or 60)
        self.max_tokens = int(bp.get('max_tokens') or 60000)
        self.run_as = auth.user_by_name(self.row['run_as'] or 'admin') or {'username': self.row['run_as'], 'name': self.row['run_as'], 'role_list': []}
        self.permissions = self.agent.get('permissions')
        self.iteration = None

    # ----- 状态 -----
    def init_state(self):
        inputs = dict(self.row['inputs'] or {})
        sys_ = {'query': inputs.get('query') or '', 'user': self.row['created_by'] or '', 'now': db.jsonable(clock.now()), 'date': clock.now().strftime('%Y-%m-%d'),
                'shift': current_shift(), 'run_id': self.run_id, 'agent': self.row['agent'], 'agent_name': self.agent.get('name') or '', 'mode': self.mode,
                'trigger': self.row['trigger'], 'feedback': ''}
        self.st = {'status': {n: 'pending' for n in self.g.comps}, 'outputs': {}, 'active': [], 'forced': [], 'vars': {}, 'sys': sys_, 'inputs': inputs,
                   'replies': [], 'receipts': [], 'waiting': None, 'loops': 0, 'steps': 0, 'tokens': 0, 'gen': {}}

    def save(self, **extra):
        with db.engine.begin() as c:
            c.execute(agent_run_t.update().where(agent_run_t.c.id == self.run_id).values(state=self.st, steps=self.st['steps'], tokens=self.st['tokens'], **extra))

    def scope(self):
        return {'outputs': self.st['outputs'], 'sys': self.st['sys'], 'inputs': self.st['inputs'], 'vars': self.st['vars']}

    # ----- 入口 -----
    def start(self):
        self.init_state()
        self.save(status='running', started_at=now())
        begin = self.g.comps.get('begin')
        sid = self.step_begin('begin', begin, self.st['inputs'])
        self.st['outputs']['begin'] = dict(self.st['inputs'])
        self.st['status']['begin'] = 'done'
        self.step_end(sid, 'done', output=self.st['inputs'], t0=time.time())
        self.activate('begin', None, self.main_frame())
        self.advance()

    def resume_from(self, approval):
        w = self.st.get('waiting')
        if not w or w.get('approval') != approval['id']:
            return
        self.save(status='running')
        nid = w['node']
        self.st['waiting'] = None
        self.st['status'][nid] = 'pending'
        frame = self.main_frame()
        r = self.exec_node(nid, frame, resume=approval, resume_step=w.get('step'))
        if r == 'ok':
            self.advance()
        elif r == 'wait':
            self.suspend()

    def main_frame(self):
        return {'nodes': [n for n in self.g.order if n not in self.g.owned], 'status': self.st['status'], 'active': self.st['active'], 'forced': self.st['forced']}

    def cancelled(self):
        with db.engine.connect() as c:
            return c.execute(sa.select(agent_run_t.c.status).where(agent_run_t.c.id == self.run_id)).scalar() == 'cancelled'

    def advance(self, frame=None):
        top = frame is None
        frame = frame or self.main_frame()
        while True:
            if top and self.cancelled():
                return 'stop'
            nid = self.next_ready(frame)
            if nid is None:
                break
            if nid not in frame['forced'] and not self.is_active(nid, frame):
                frame['status'][nid] = 'skipped'
                continue
            r = self.exec_node(nid, frame)
            if r == 'wait':
                if not top:
                    raise NodeError(_tr('循环体里不能等待审批'))
                return self.suspend()
            if r == 'stop':
                return 'stop'
            if top:
                self.save()
        if top:
            self.finish('succeeded')
        return 'done'

    def next_ready(self, frame):
        st = frame['status']
        for n in frame['nodes']:
            if st.get(n) != 'pending':
                continue
            if n in frame['forced']:
                return n
            if all(st.get(u, 'skipped') in RESOLVED or (u not in frame['nodes'] and u not in self.g.owned) for u in self.g.fwd_up[n]):
                return n
        return None

    def is_active(self, nid, frame):
        return any(k.split('|')[1] == nid for k in frame['active'])

    def activate(self, nid, handles, frame):
        for t, h in self.g.out.get(nid, []):
            if not (h is None or (handles and h in handles)):
                continue
            if (nid, t) in self.g.back:
                self.loop_back(t, frame)
            else:
                k = f'{nid}|{t}|{h or ""}'
                if k not in frame['active']:
                    frame['active'].append(k)

    def loop_back(self, tgt, frame):
        self.st['loops'] += 1
        if self.st['loops'] > MAX_LOOPS:
            raise StopRun(_tr('已经驳回重做 {MAX_LOOPS} 次，自动结束', MAX_LOOPS=MAX_LOOPS))
        region = self.g.reach([tgt])
        for n in region:
            if n in frame['status']:
                frame['status'][n] = 'pending'
            self.st['gen'][n] = self.st['gen'].get(n, 0) + 1
        frame['active'][:] = [k for k in frame['active'] if k.split('|')[0] not in region]
        frame['forced'][:] = [f for f in frame['forced'] if f not in region] + [tgt]

    # ----- 单个节点 -----
    def exec_node(self, nid, frame, resume=None, resume_step=None):
        comp = self.g.comps[nid]
        t = comp['type']
        meta = TYPES[t]
        if self.st['steps'] >= self.max_steps:
            self.finish('failed', _tr('超过每次运行的步数上限（{max_steps}）', max_steps=self.max_steps))
            return 'stop'
        if self.st['tokens'] >= self.max_tokens:
            self.finish('failed', _tr('超过每次运行的 token 上限（{max_tokens}）', max_tokens=self.max_tokens))
            return 'stop'
        if nid in frame['forced']:
            frame['forced'].remove(nid)
        frame['status'][nid] = 'running'
        if t == 'loop':
            return self.exec_loop(nid, frame)
        raw = comp.get('params') or {}
        retry = max(0, min(5, int(raw.get('retry') or 0)))
        err = None
        for attempt in range(1, retry + 2):
            t0 = time.time()
            try:
                params = resolve_params(comp, self.scope())
            except Exception as ex:
                params = {}
                err = ex
            sid = resume_step if (resume is not None and resume_step) else self.step_begin(nid, comp, params, attempt)
            if err is not None:
                self.step_end(sid, 'failed', error=_tr('参数里的变量没法解析：{err}', err=err), t0=t0)
                break
            try:
                res: Result = meta['fn'](Ctx(self, nid, resume), comp, params)
            except Rejected as ex:
                self.step_end(sid, 'rejected', error=str(ex), t0=t0)
                frame['status'][nid] = 'failed'
                self.finish('rejected', str(ex))
                return 'stop'
            except StopRun:
                raise
            except Exception as ex:
                err = ex
                log.info(_tr('节点 %s 失败：%s'), nid, ex)
                self.step_end(sid, 'failed', error=f'{ex}', t0=t0, tb=traceback.format_exc(limit=3))
                if attempt <= retry:
                    time.sleep(max(0.0, min(30.0, float(raw.get('retry_delay_s') or 2))))
                    continue
                break
            if res.wait:
                if self.single:
                    self.step_end(sid, 'waiting', output={'会暂停等待': res.wait}, t0=t0)
                    frame['status'][nid] = 'done'
                    return 'ok'
                aid = self.create_approval(nid, comp, res.wait)
                self.step_end(sid, 'waiting', output={'等待': res.wait.get('title'), 'waiting': res.wait.get('title'), 'approval': aid}, t0=t0, keep_open=True)
                frame['status'][nid] = 'waiting'
                self.st['waiting'] = {'node': nid, 'approval': aid, 'step': sid}
                return 'wait'
            self.st['steps'] += 1
            self.st['tokens'] += res.tokens or 0
            self.st['outputs'][nid] = res.output
            frame['status'][nid] = 'done'
            self.step_end(sid, 'done', output=res.output, messages=res.messages, tool_calls=res.tool_calls, tokens=res.tokens, t0=t0,
                          since=(resume or {}).get('created_at') if resume is not None else None)
            self.activate(nid, res.handles, frame)
            return 'ok'
        # 失败处理
        self.st['steps'] += 1
        how = raw.get('on_error') or 'stop'
        if how == 'default':
            d = raw.get('default_output')
            try:
                d = json.loads(d) if isinstance(d, str) and d.strip() else (d or {})
            except ValueError:
                d = {'text': d}
            self.st['outputs'][nid] = {**(d if isinstance(d, dict) else {'value': d}), 'error': str(err)}
            frame['status'][nid] = 'done'
            self.activate(nid, None, frame)
            return 'ok'
        if how == 'goto' and raw.get('error_goto') in self.g.comps:
            self.st['outputs'][nid] = {'error': str(err)}
            frame['status'][nid] = 'failed'
            tgt = raw['error_goto']
            if frame['status'].get(tgt) in RESOLVED:
                self.loop_back(tgt, frame)
            elif tgt not in frame['forced']:
                frame['forced'].append(tgt)
            return 'ok'
        frame['status'][nid] = 'failed'
        self.finish('failed', _tr('{v}：{err}', v=comp.get('name') or nid, err=err))
        return 'stop'

    def exec_loop(self, nid, frame):
        comp = self.g.comps[nid]
        t0 = time.time()
        params = resolve_params(comp, self.scope())
        items = params.get('items')
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except ValueError:
                items = [x for x in re.split(r'[\n,，]+', items) if x.strip()]
        if isinstance(items, dict):
            items = list(items.values())
        items = list(items or [])[: max(1, int(params.get('max_items') or 20))]
        sid = self.step_begin(nid, comp, {'items': _tr('{len} 项', len=len(items))})
        region = self.g.loop_region.get(nid, [])
        sinks = [n for n in region if not any(t in region for t, _ in self.g.fwd_out[n])]
        results = []
        for i, item in enumerate(items):
            self.iteration = i
            self.st['outputs'][nid] = {'item': item, 'index': i, 'count': len(items), 'results': results}
            sub = {'nodes': region, 'status': {n: 'pending' for n in region}, 'active': [], 'forced': []}
            for t, h in self.g.fwd_out[nid]:
                if h == 'body' and t in region:
                    sub['active'].append(f'{nid}|{t}|body')
            if self.advance(sub) == 'stop':
                self.iteration = None
                return 'stop'
            outs = {s: self.st['outputs'].get(s) for s in sinks if sub['status'].get(s) == 'done'}
            results.append(next(iter(outs.values())) if len(outs) == 1 else outs)
            for k in sub['active']:
                if k.split('|')[1] not in region and k not in frame['active']:
                    frame['active'].append(k)
            for n in region:
                frame['status'][n] = sub['status'][n] if sub['status'][n] in RESOLVED else 'skipped'
        self.iteration = None
        for n in region:
            frame['status'].setdefault(n, 'skipped')
            if frame['status'][n] == 'pending':
                frame['status'][n] = 'skipped'
        out = {'item': items[-1] if items else None, 'index': len(items) - 1, 'count': len(items), 'results': results}
        self.st['outputs'][nid] = out
        self.st['steps'] += 1
        frame['status'][nid] = 'done'
        self.step_end(sid, 'done', output={'count': len(items), 'results': results}, t0=t0)
        self.activate(nid, ['done'], frame)
        return 'ok'

    # ----- 动作 -----
    def do_action(self, nid, op, payload, params, resume):
        meta = ops.OPS[op]
        if self.permissions is not None and op not in self.permissions:
            raise NodeError(_tr('本智能体没有被授权"{label}"（在智能体设置的权限白名单里勾选）', label=meta["label"]))
        if op not in ('notify', 'report') and not auth.can_write(self.run_as, meta['entity']):
            raise NodeError(_tr('以 {v} 的身份执行：没有"{label}"所需的修改权限', v=self.run_as.get("name") or self.run_as["username"], label=meta["label"]))
        with db.engine.begin() as c:
            p = ops.validate(c, op, payload)
        need = meta['risk'] == 'high' and params.get('require_approval', True) not in (False, 'false', 0) and not self.g.approved_upstream(nid)
        if need:
            if resume is None:
                return Result(wait={'kind': 'approval', 'title': _tr('智能体要{label}', label=meta['label']), 'summary': ops.preview(op, p), 'payload': p,
                                    'role': params.get('approval_role') or 'manager', 'timeout_min': 0, 'on_timeout': 'reject', 'action': op})
            if resume['status'] != 'approved':
                self.st['sys']['feedback'] = resume['reason'] or ''
                raise Rejected(_tr('审批未通过：{v}', v=resume['reason'] or _tr('未写原因')))
            with db.engine.begin() as c:
                p = ops.validate(c, op, (resume['result'] or {}).get('payload') or p)
        key = f"{self.run_id}:{nid}:{self.st['gen'].get(nid, 0)}:{self.iteration if self.iteration is not None else ''}"
        who = _tr('智能体「{v}」', v=self.agent.get('name') or self.row['agent'])
        if self.mode == 'dry':
            text = _tr('【试运行，未写入】将') + ops.preview(op, p).replace('\n', '；')
            with db.engine.begin() as c:
                if not c.execute(sa.select(agent_action_t.c.id).where(agent_action_t.c.idem_key == key)).first():
                    c.execute(agent_action_t.insert().values(idem_key=key, run=self.run_id, node=nid, action=op, payload=p, result={'text': text}, dry_run=True, created_at=now()))
            return Result(output={'dry_run': True, 'text': text, 'payload': p, 'code': '', 'id': None, 'link': ''})
        with db.engine.begin() as c:
            old = c.execute(sa.select(agent_action_t.c.result).where(agent_action_t.c.idem_key == key)).scalar()
            if old:
                return Result(output={**old, 'dry_run': False, 'repeated': True})
            try:
                rec = ops.execute(c, op, p, who, 'agent')
            except ops.OpError as ex:
                raise NodeError(str(ex))
            c.execute(agent_action_t.insert().values(idem_key=key, run=self.run_id, node=nid, action=op, payload=p, result=rec, dry_run=False, created_at=now()))
            from .. import audit as audit_mod
            audit_mod.audit(c, 'agent_action', None, None, target=f"run {self.run_id} / {nid}", user=self.run_as['username'], source='agent',
                            detail={'agent': self.row['agent'], 'op': op, 'payload': p, 'receipt': rec,
                                    'approved_by': resume['decided_by'] if resume is not None else None})
        self.st['receipts'].append(rec)
        for x in rec.get('extra') or []:
            self.st['receipts'].append(x)
        return Result(output={**rec, 'dry_run': False})

    # ----- 审批 -----
    def create_approval(self, nid, comp, w):
        tmo = float(w.get('timeout_min') or 0)
        with db.engine.begin() as c:
            aid = c.execute(approval_t.insert().values(
                run=self.run_id, agent=self.row['agent'], node=nid, kind=w.get('kind') or 'approval', title=str(w.get('title'))[:290],
                summary=w.get('summary') if isinstance(w.get('summary'), str) else json.dumps(w.get('summary'), ensure_ascii=False),
                payload=w.get('payload'), fields=w.get('fields') if w.get('kind') == 'form' else {'on_timeout': w.get('on_timeout'), 'action': w.get('action')},
                assignee_role=w.get('role'), status='pending', dry_run=self.mode == 'dry', created_at=now(),
                due_at=now() + timedelta(minutes=tmo) if tmo > 0 else None)).inserted_primary_key[0]
            db.insert(c, 'notice', {'title': _tr('待{v}：{get}', v=_tr('审批') if w.get('kind') != 'form' else _tr('填写'), get=w.get('title')), 'content': _tr('来自智能体「{get}」', get=self.agent.get('name'))
                                    + (_tr('（试运行）') if self.mode == 'dry' else ''), 'to_role': w.get('role'), 'channel': 'inbox', 'level': 'warn',
                                    'link': '/approvals', 'source': '智能体', 'read': False, 'time': clock.now()}, raw=True)
        return aid

    def suspend(self):
        self.save(status='waiting')
        self.touch_agent('waiting')
        _signal(self.run_id)
        return 'wait'

    # ----- 步骤记录 -----
    def step_begin(self, nid, comp, params, attempt=1):
        with db.engine.begin() as c:
            return c.execute(agent_step_t.insert().values(run=self.run_id, node=nid, ntype=comp.get('type'), name=comp.get('name') or nid, status='running',
                                                          input=_safe(params), attempt=attempt, iteration=self.iteration, started_at=now())).inserted_primary_key[0]

    def step_end(self, sid, status, output=None, messages=None, tool_calls=None, tokens=0, error=None, t0=None, tb=None, keep_open=False, since=None):
        end = now()
        dur = int((time.time() - t0) * 1000) if t0 else None
        vals = dict(status=status, output=_safe(output), messages=_safe(messages), tool_calls=_safe(tool_calls), tokens=tokens or 0,
                    error=(error + ('\n' + tb if tb and log.isEnabledFor(logging.DEBUG) else '')) if error else None)
        if not keep_open:
            vals.update(ended_at=end, duration_ms=dur)
        with db.engine.begin() as c:
            if since is not None:
                st = c.execute(sa.select(agent_step_t.c.started_at).where(agent_step_t.c.id == sid)).scalar()
                if st:
                    vals['duration_ms'] = int((end - st).total_seconds() * 1000)
            c.execute(agent_step_t.update().where(agent_step_t.c.id == sid).values(**vals))

    # ----- 结束 -----
    def finish(self, status, error=None):
        output = {'reply': '\n\n'.join(self.st.get('replies') or []), 'receipts': self.st.get('receipts') or []}
        self.save(status=status, ended_at=now(), output=output, error=error)
        with db.engine.begin() as c:
            if status in ('failed', 'cancelled', 'rejected'):
                c.execute(approval_t.update().where((approval_t.c.run == self.run_id) & (approval_t.c.status == 'pending')).values(status='cancelled'))
        self.touch_agent(status)
        _signal(self.run_id)
        return 'stop'

    def touch_agent(self, status):
        if self.row['trigger'] == 'node_test':
            return
        with db.engine.begin() as c:
            c.execute(agent_t.update().where(agent_t.c.id == self.row['agent']).values(last_run_at=self.row['created_at'] or now(), last_status=status))


class StopRun(Exception):
    pass


def _safe(v):
    if v is None:
        return None
    try:
        s = json.dumps(v, ensure_ascii=False, default=str)
    except Exception:
        s = json.dumps(str(v), ensure_ascii=False)
    if len(s) > 60000:
        return {'_truncated': s[:60000]}
    return json.loads(s)


def _signal(run_id):
    e = _done_events.get(run_id)
    if e:
        e.set()


def current_shift():
    t = clock.now().strftime('%H:%M')
    try:
        with db.engine.connect() as c:
            for s in db.rows(c, 'shift'):
                a, b = s['start'] or '', s['end'] or ''
                if a and b and ((a <= t < b) if a < b else (t >= a or t < b)):
                    return s['name']
    except Exception:
        pass
    return ''


def _lock(run_id):
    with _locks_guard:
        return _locks.setdefault(run_id, threading.Lock())


def _run_job(run_id, approval=None):
    with _lock(run_id):
        try:
            ex = Executor(run_id)
            from .. import context
            with context.acting(ex.run_as, 'agent'):      # 智能体的写操作以发布人身份审计，来源"智能体"
                try:
                    if approval is None:
                        ex.start()
                    else:
                        ex.resume_from(approval)
                except StopRun as e:
                    ex.finish('failed', str(e))
        except Exception as e:
            log.exception(_tr('智能体运行 %s 出错'), run_id)
            with db.engine.begin() as c:
                c.execute(agent_run_t.update().where(agent_run_t.c.id == run_id).values(status='failed', error=_tr('执行器错误：{e}', e=e), ended_at=now()))
            _signal(run_id)


def create_run(agent_id, dsl, *, version=None, mode='live', trigger='manual', inputs=None, user='', run_as='', key=None):
    with db.engine.begin() as c:
        return c.execute(agent_run_t.insert().values(agent=agent_id, version=version, mode=mode, trigger=trigger, trigger_key=key, inputs=inputs or {},
                                                     status='queued', dsl=dsl, state={}, tokens=0, steps=0, run_as=run_as or user or 'admin', created_by=user,
                                                     created_at=now())).inserted_primary_key[0]


def start_run(agent_id, dsl, *, wait: float = 0, **kw) -> int:
    rid = create_run(agent_id, dsl, **kw)
    ev = threading.Event()
    _done_events[rid] = ev
    POOL.submit(_run_job, rid)
    if wait:
        ev.wait(wait)
    return rid


def resume(run_id, approval):
    POOL.submit(_run_job, run_id, approval)


def run_single(agent_id, dsl, nid, inputs, mocks, user):
    """只运行一个节点：上游输出用 mocks（通常是上次运行的结果，可在面板里改），写操作一律按试运行。"""
    rid = create_run(agent_id, dsl, mode='dry', trigger='node_test', inputs=inputs, user=user, run_as=user)
    ex = Executor(rid, single=True)
    ex.init_state()
    ex.st['outputs'].update(mocks or {})
    ex.st['outputs'].setdefault('begin', dict(inputs or {}))
    frame = {'nodes': [nid], 'status': {nid: 'pending'}, 'active': [], 'forced': [nid]}
    try:
        if ex.g.comps[nid]['type'] == 'loop':
            raise NodeError(_tr('循环节点请整体运行'))
        r = ex.exec_node(nid, frame)
        if r != 'stop':
            ex.save(status='succeeded', ended_at=now())
    except Exception as e:
        ex.save(status='failed', ended_at=now(), error=str(e))
    return rid
