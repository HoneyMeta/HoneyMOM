"""智能体节点：类型定义（界面用的参数表、输出、分支出口）和执行函数。

执行函数签名：fn(ctx, node, params) -> Result；params 已经把 {节点@输出} 等变量替换好。
"""
from ..i18n import _tr
from .. import i18n as _i18n
import json
import re
import time
import urllib.request
from dataclasses import dataclass, field

from ..schema import ROLES
from ..services import ai, ops
from . import tools as T
from .queries import QUERY_KINDS, run_query


@dataclass
class Result:
    output: dict = field(default_factory=dict)
    handles: list | None = None          # 走哪些出口；None = 默认出口
    messages: list | None = None
    tool_calls: list | None = None
    tokens: int = 0
    wait: dict | None = None             # 需要人工处理：{kind, title, summary, payload, fields, role, timeout_min, on_timeout}


class NodeError(Exception):
    pass


class Rejected(Exception):
    """审批被驳回且没有"驳回"出口：整次运行结束为"已驳回"。"""


# ---------------- 界面参数表 ----------------
def P(key, label, type='text', **kw):
    return {'key': key, 'label': label, 'type': type, **kw}


ROLE_OPTS = [{'value': k, 'label': v} for k, v in ROLES.items()]
EVENT_KINDS = {
    'andon': ('安灯发起', ['andon_id', 'code', 'atype', 'description', 'equipment_code', 'equipment_name', 'station', 'raised_by', 'time']),
    'equipment_state': ('设备状态变化', ['equipment_id', 'equipment_code', 'equipment_name', 'etype', 'from', 'to', 'state', 'alarm', 'time']),
    'oee_low': ('OEE 低于阈值', ['equipment_code', 'equipment_name', 'oee', 'A', 'P', 'Q', 'hours', 'loss', 'time']),
    'ncr': ('出现不合格品', ['ncr_id', 'code', 'sn', 'defect', 'defect_name', 'station', 'work_order', 'qty', 'time']),
    'material_low': ('物料低于安全库存', ['material_code', 'material_name', 'qty', 'safety_stock', 'unit', 'time']),
    'wo_late': ('工单延误', ['work_order_id', 'code', 'customer', 'due', 'eta', 'qty', 'qty_done', 'priority', 'time']),
    'alarm': ('设备报警', ['equipment_code', 'equipment_name', 'msg', 'time']),
}
STATE_OPTS = [{'value': k, 'label': v} for k, v in {'down': '停机', 'running': '运行', 'idle': '待机', 'setup': '换型', 'offline': '计划停/离线'}.items()]
ETYPE_OPTS = [{'value': k, 'label': v} for k, v in {'furnace': '中频炉', 'cnc': '加工中心', 'molding': '造型线', 'shotblast': '抛丸机', 'deburr': '去毛刺',
                                                     'inspection': '检测站', 'treatment': '球化站', 'sand': '砂处理', 'cooling': '冷却线', 'warehouse': '立体仓库'}.items()]
COMMON = [P('retry', '失败重试次数', 'number', default=0, min=0, max=5, group='adv'),
          P('retry_delay_s', '重试间隔（秒）', 'number', default=2, min=0, max=30, group='adv'),
          P('on_error', '失败后', 'select', default='stop', group='adv', options=[{'value': 'stop', 'label': '结束这次运行'}, {'value': 'default', 'label': '用默认输出继续'},
                                                                                {'value': 'goto', 'label': '跳到指定节点'}]),
          P('default_output', '默认输出（JSON）', 'json', default='{}', group='adv', show={'on_error': ['default']}),
          P('error_goto', '跳到节点', 'node', group='adv', show={'on_error': ['goto']})]

TYPES: dict[str, dict] = {}


def node_type(name, label, cat, icon, desc, params=(), outputs=(), handles=None, risk=None, op=None):
    def deco(fn):
        TYPES[name] = {'type': name, 'label': label, 'cat': cat, 'icon': icon, 'desc': desc, 'params': list(params), 'outputs': list(outputs),
                       'handles': handles, 'risk': risk, 'op': op, 'fn': fn}
        return fn
    return deco


def handles_of(ntype, params) -> list | None:
    """分支节点的出口名（界面画多个连接点、执行时决定走哪条）。"""
    params = params or {}
    if ntype == 'switch':
        return [c['id'] for c in params.get('cases') or []] + ['else']
    if ntype == 'categorize':
        return [c['id'] for c in params.get('categories') or []] + ['other']
    if ntype in ('approval',):
        return ['approved', 'rejected']
    if ntype == 'loop':
        return ['body', 'done']
    return None


# ---------------- 开始 / 触发 ----------------
@node_type('begin', '开始', 'trigger', 'VideoPlay', '每个智能体一个：决定怎么被触发，以及有哪些输入变量。', params=[
    P('trigger', '触发方式', 'select', default='manual', options=[{'value': 'chat', 'label': '聊天'}, {'value': 'manual', 'label': '手动 / 表单'},
                                                           {'value': 'schedule', 'label': '定时'}, {'value': 'event', 'label': '事件'}, {'value': 'webhook', 'label': 'Webhook'}]),
    P('inputs', '输入变量', 'fields', default=[], show={'trigger': ['manual', 'chat', 'webhook']}, help='手动运行时要填的表单；聊天触发时自动有 {sys.query}'),
    P('schedule_mode', '定时方式', 'select', default='cron', show={'trigger': ['schedule']}, options=[{'value': 'cron', 'label': 'Cron 表达式'}, {'value': 'shift', 'label': '按班次'}]),
    P('cron', 'Cron（分 时 日 月 周）', 'text', default='0 8 * * *', show={'trigger': ['schedule'], 'schedule_mode': ['cron']}, help='例：0 */2 * * * 每两小时整点'),
    P('shift_at', '班次时点', 'select', default='end', show={'trigger': ['schedule'], 'schedule_mode': ['shift']},
      options=[{'value': 'end', 'label': '每班结束'}, {'value': 'start', 'label': '每班开始'}]),
    P('offset_min', '提前(-)/延后(+) 分钟', 'number', default=0, show={'trigger': ['schedule'], 'schedule_mode': ['shift']}),
    P('event', '事件', 'select', options=[{'value': k, 'label': v[0]} for k, v in EVENT_KINDS.items()], show={'trigger': ['event']}),
    P('to_state', '变成什么状态', 'select', default='down', options=STATE_OPTS, show={'trigger': ['event'], 'event': ['equipment_state']}),
    P('etypes', '设备类型（不选=全部）', 'multiselect', default=[], options=ETYPE_OPTS, show={'trigger': ['event'], 'event': ['equipment_state', 'oee_low', 'alarm']}),
    P('equipment', '只看这些设备（编号，逗号分隔）', 'text', default='', show={'trigger': ['event'], 'event': ['equipment_state', 'oee_low', 'alarm', 'andon']}),
    P('threshold', 'OEE 阈值（%）', 'number', default=60, show={'trigger': ['event'], 'event': ['oee_low']}),
    P('hours', '统计最近几小时', 'number', default=2, show={'trigger': ['event'], 'event': ['oee_low']}),
    P('keyword', '包含关键字', 'text', default='', show={'trigger': ['event'], 'event': ['alarm', 'andon']}),
    P('exclude', '报警里有这些字就不触发（逗号分隔）', 'text', default='', show={'trigger': ['event'], 'event': ['equipment_state', 'alarm']},
      help='例如：等待上下料,计划停 —— 人员/计划原因的停机不建维修单'),
    P('debounce_s', '防抖（秒，同一对象合并）', 'number', default=3, show={'trigger': ['event']}),
    P('cooldown_s', '冷却（秒，同一对象不重复触发）', 'number', default=600, show={'trigger': ['event']}),
    P('dedup_key', '去重键', 'text', default='', show={'trigger': ['event']}, help='默认按设备/单号；可写如 {equipment_code}'),
    P('max_concurrent', '同时最多运行', 'number', default=3, show={'trigger': ['event', 'schedule', 'webhook']}),
    P('max_steps', '每次最多步数', 'number', default=60, group='adv'),
    P('max_tokens', '每次最多 token', 'number', default=60000, group='adv'),
], outputs=['query', '（输入变量 / 事件字段）'])
def n_begin(ctx, node, p):
    return Result(output=dict(ctx.inputs))


def begin_outputs(params):
    params = params or {}
    tr = params.get('trigger')
    if tr == 'event' and params.get('event') in EVENT_KINDS:
        return EVENT_KINDS[params['event']][1]
    keys = [f['key'] for f in params.get('inputs') or [] if f.get('key')]
    return (['query'] if tr == 'chat' else []) + keys


# ---------------- 数据（只读） ----------------
QOPTS = [{'value': k, 'label': v[0]} for k, v in QUERY_KINDS.items()]


def _qshow(*keys):
    return {'entity': [k for k, v in QUERY_KINDS.items() if any(x in v[2] for x in keys)]}


@node_type('mes_query', 'MES 查询', 'data', 'Search', '按类别查 MES 业务数据，输出行和中文摘要。', params=[
    P('entity', '查什么', 'select', default='work_orders', options=QOPTS, required=True),
    P('equipment', '设备编号', 'text', vars=True, show=_qshow('equipment'), help='如 CNC3，可引用变量'),
    P('hours', '最近几小时', 'number', default=24, show=_qshow('hours')),
    P('status', '状态', 'text', default='', vars=True, show=_qshow('status'), help='工单：open/all/草稿等；安灯：open/all'),
    P('id', '单号或 ID', 'text', vars=True, show=_qshow('id')),
    P('day', '日期 YYYY-MM-DD（空=今天）', 'text', vars=True, show=_qshow('day')),
    P('late_only', '只看延误', 'bool', default=False, show=_qshow('late_only')),
    P('low_only', '只看低于安全库存', 'bool', default=False, show=_qshow('low_only')),
    P('limit', '最多几行', 'number', default=20, show=_qshow('limit')),
], outputs=['summary', 'count', 'rows', 'data'])
def n_mes_query(ctx, node, p):
    kind = p.get('entity')
    keys = QUERY_KINDS.get(kind, (None, None, []))[2]
    return Result(output=run_query(kind, {k: p.get(k) for k in keys}))


@node_type('kb_search', '知识库 / SOP 检索', 'data', 'Reading', '在知识库（作业指导书、SOP、判定标准）里找最相关的段落。', params=[
    P('query', '检索内容', 'textarea', vars=True, required=True), P('top_k', '返回几段', 'number', default=3)], outputs=['text', 'refs', 'count'])
def n_kb(ctx, node, p):
    refs = ai.kb_search(str(p.get('query') or ''), int(p.get('top_k') or 3))
    return Result(output={'refs': refs, 'count': len(refs), 'text': '\n\n'.join(_tr('【{title}】{snippet}', title=r['title'], snippet=r['snippet']) for r in refs) or _tr('（知识库里没有找到相关内容）')})


@node_type('sql', '只读 SQL', 'data', 'Coin', '执行一条 SELECT 查询（只读，最多 200 行）。', params=[P('sql', 'SQL', 'textarea', vars=True, required=True)],
           outputs=['rows', 'columns', 'count', 'text'])
def n_sql(ctx, node, p):
    r = ai.run_sql(str(p.get('sql') or ''))
    text = '\n'.join(' | '.join(str(v) for v in row) for row in r['rows'][:50])
    return Result(output={**r, 'count': len(r['rows']), 'text': ' | '.join(r['columns']) + '\n' + text})


@node_type('http', 'HTTP 请求', 'data', 'Link', '调用外部接口（10 秒超时）。', params=[
    P('method', '方法', 'select', default='GET', options=[{'value': 'GET', 'label': 'GET'}, {'value': 'POST', 'label': 'POST'}]),
    P('url', '地址', 'text', vars=True, required=True), P('headers', '请求头', 'kv', default=[]), P('body', '请求体（JSON）', 'json', vars=True, default='',
                                                                                                     show={'method': ['POST']})], outputs=['status', 'json', 'text'])
def n_http(ctx, node, p):
    url = str(p.get('url') or '')
    if not url.startswith(('http://', 'https://')):
        raise NodeError(_tr('地址要以 http:// 或 https:// 开头'))
    hdr = {h['key']: str(h.get('value') or '') for h in p.get('headers') or [] if h.get('key')}
    data = None
    if p.get('method') == 'POST':
        b = p.get('body')
        data = (b if isinstance(b, str) else json.dumps(b, ensure_ascii=False)).encode()
        hdr.setdefault('Content-Type', 'application/json')
    req = urllib.request.Request(url, data=data, method=p.get('method') or 'GET', headers=hdr)
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read(500000).decode('utf-8', 'replace')
        st = r.status
    try:
        js = json.loads(raw)
    except ValueError:
        js = None
    return Result(output={'status': st, 'json': js, 'text': raw[:20000]})


# ---------------- AI ----------------
def _fallback_note():
    return _tr('（未接入大模型或调用失败，以下按规则生成）')


@node_type('agent', '智能体（大模型 + 工具）', 'ai', 'MagicStick', '大模型可以多轮调用选定的查询工具，再给出结论。', params=[
    P('system', '角色设定', 'textarea', vars=True, default='你是蜂元智能 MOM 里的分析助手，回答简洁、务实，使用中文，只基于查到的数据，不编造数字。'),
    P('prompt', '任务', 'textarea', vars=True, required=True),
    P('tools', '可用工具', 'multiselect', default=['query_sql', 'mes_query'], options=[{'value': k, 'label': v['label']} for k, v in T.DATA_TOOLS.items()]),
    P('max_rounds', '最多调用几轮工具', 'number', default=4, min=1, max=10), P('temperature', '发散度', 'number', default=0.2, group='adv')],
    outputs=['text', 'tool_calls'])
def n_agent(ctx, node, p):
    msgs = [{'role': 'system', 'content': str(p.get('system') or '') + _tr('\n当前数据时钟：{now}。', now=ctx.sys['now'])}, {'role': 'user', 'content': str(p.get('prompt') or '')}]
    calls_log, tokens = [], 0
    tl = T.tool_schemas(p.get('tools') or [])
    try:
        for _ in range(int(p.get('max_rounds') or 4)):
            m, tk = ctx.llm(msgs, tools=tl or None, temperature=float(p.get('temperature') or 0.2))
            tokens += tk
            calls = m.get('tool_calls') or []
            if not calls:
                return Result(output={'text': m.get('content') or '', 'tool_calls': calls_log}, messages=msgs + [{'role': 'assistant', 'content': m.get('content')}],
                              tool_calls=calls_log, tokens=tokens)
            msgs.append({'role': 'assistant', 'content': m.get('content') or '', 'tool_calls': calls})
            for tc in calls:
                fn = tc['function']['name']
                t0 = time.time()
                args = {}
                try:
                    args = json.loads(tc['function'].get('arguments') or '{}')
                    if fn not in (p.get('tools') or []):
                        raise ValueError(_tr('这个工具没有授权给本节点'))
                    res = T.call(fn, args)
                    content = T.dumps(res)
                except Exception as ex:
                    content = json.dumps({'error': str(ex)}, ensure_ascii=False)
                calls_log.append({'tool': fn, 'phrase': T.phrase(fn, args), 'args': args, 'result': content[:3000], 'ms': int((time.time() - t0) * 1000)})
                msgs.append({'role': 'tool', 'tool_call_id': tc['id'], 'content': content})
        msgs.append({'role': 'user', 'content': _tr('请根据以上查询结果直接给出结论，不要再调用工具。')})
        m, tk = ctx.llm(msgs)
        return Result(output={'text': m.get('content') or '', 'tool_calls': calls_log}, messages=msgs, tool_calls=calls_log, tokens=tokens + tk)
    except ctx.LLMUnavailable as ex:
        return Result(output={'text': f'{_fallback_note()}\n{ex}\n\n' + str(p.get('prompt') or '')[:3000], 'tool_calls': calls_log, 'fallback': _tr('{ex}，输出的是任务原文', ex=ex)},
                      tool_calls=calls_log, tokens=tokens)


@node_type('llm', '大模型', 'ai', 'ChatLineRound', '单次调用大模型：写摘要、报告、建议。', params=[
    P('system', '角色设定', 'textarea', vars=True, default='你是制造业 MES 系统里的助手，回答简洁、务实，使用中文，只基于给出的数据。'),
    P('prompt', '提示词', 'textarea', vars=True, required=True), P('json_mode', '要求输出 JSON', 'bool', default=False),
    P('temperature', '发散度', 'number', default=0.3, group='adv')], outputs=['text', 'json'])
def n_llm(ctx, node, p):
    msgs = [{'role': 'system', 'content': str(p.get('system') or '')}, {'role': 'user', 'content': str(p.get('prompt') or '')}]
    try:
        m, tk = ctx.llm(msgs, json_mode=bool(p.get('json_mode')), temperature=float(p.get('temperature') or 0.3))
    except ctx.LLMUnavailable as ex:
        return Result(output={'text': _tr('{v}（{ex}）\n\n', v=_fallback_note(), ex=ex) + str(p.get('prompt') or '')[:3000], 'json': None, 'fallback': _tr('{ex}，输出的是提示词原文', ex=ex)}, messages=msgs)
    text = m.get('content') or ''
    return Result(output={'text': text, 'json': _json_in(text)}, messages=msgs + [{'role': 'assistant', 'content': text}], tokens=tk)


def _json_in(text):
    if not text:
        return None
    s = text.strip()
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', s)
    if m:
        s = m.group(1)
    a, b = s.find('{'), s.rfind('}')
    if a < 0 or b < a:
        return None
    try:
        return json.loads(s[a:b + 1])
    except ValueError:
        return None


@node_type('categorize', '分类', 'ai', 'Share', '让大模型把输入分到某一类，每一类一个出口；没配大模型时按关键字分。', params=[
    P('input', '要分类的内容', 'textarea', vars=True, required=True),
    P('categories', '类别', 'categories', default=[{'id': 'c1', 'name': '类别一', 'description': '', 'keywords': ''}])],
    outputs=['category', 'category_name', 'reason'], handles='dynamic')
def n_categorize(ctx, node, p):
    cats = p.get('categories') or []
    text = str(p.get('input') or '')
    msgs = None
    tokens = 0
    chosen, reason = None, ''
    try:
        lst = '\n'.join(_tr('- {id}：{name}。{v}', id=c['id'], name=c['name'], v=c.get('description') or '') for c in cats)
        msgs = [{'role': 'system', 'content': _tr('你是分类器。只输出 JSON：{"category": "类别id 或 other", "reason": "一句话理由"}')},
                {'role': 'user', 'content': _tr('类别：\n{lst}\n- other：都不是\n\n内容：{text}', lst=lst, text=text)}]
        m, tokens = ctx.llm(msgs, json_mode=True, temperature=0)
        j = _json_in(m.get('content') or '') or {}
        chosen, reason = j.get('category'), j.get('reason') or ''
        msgs = msgs + [{'role': 'assistant', 'content': m.get('content')}]
    except ctx.LLMUnavailable as ex:
        reason = _tr('{ex}，按关键字分类', ex=ex)
        for c in cats:
            kws = [k for k in re.split(r'[,，、\s]+', c.get('keywords') or '') if k]
            if any(k in text for k in kws) or (c.get('name') and c['name'] in text):
                chosen, reason = c['id'], reason + _tr('（命中「{name}」）', name=c['name'])
                break
    ids = {c['id']: c for c in cats}
    if chosen not in ids:
        chosen = 'other'
    fb = {'fallback': reason} if _i18n.matches(reason, '{ex}，按关键字分类') else {}
    return Result(output={'category': chosen, 'category_name': ids[chosen]['name'] if chosen in ids else _tr('其他'), 'reason': reason, **fb}, handles=[chosen],
                  messages=msgs, tokens=tokens)


@node_type('extract', '结构化抽取', 'ai', 'Document', '从文字里抽取指定字段（JSON），没配大模型时用各字段的默认值。', params=[
    P('input', '原始内容', 'textarea', vars=True, required=True), P('instructions', '要求', 'textarea', vars=True, default=''),
    P('fields', '字段', 'fields', default=[{'key': 'summary', 'label': '摘要', 'type': 'text', 'description': '', 'default': ''}])],
    outputs=['data', '（各字段）'])
def n_extract(ctx, node, p):
    fields = p.get('fields') or []
    data = {f['key']: f.get('default') for f in fields if f.get('key')}
    msgs, tokens, fb = None, 0, False
    try:
        spec = '\n'.join(_tr('- {key}（{v}，{v2}）：{v3}', key=f['key'], v=f.get('label') or '', v2=f.get('type') or 'text', v3=f.get('description') or '') for f in fields)
        msgs = [{'role': 'system', 'content': _tr('你从材料里抽取字段，只输出一个 JSON 对象，键名严格按要求，值用中文，不编造材料里没有的数字。')},
                {'role': 'user', 'content': _tr('字段：\n{spec}\n\n要求：{v}\n\n材料：\n{get}', spec=spec, v=p.get('instructions') or _tr('无'), get=p.get('input'))}]
        m, tokens = ctx.llm(msgs, json_mode=True, temperature=0.1)
        j = _json_in(m.get('content') or '') or {}
        for f in fields:
            k = f.get('key')
            if k and j.get(k) not in (None, ''):
                v = j[k]
                if f.get('type') == 'number':
                    try:
                        v = float(v)
                    except (TypeError, ValueError):
                        v = data.get(k)
                data[k] = v
        msgs = msgs + [{'role': 'assistant', 'content': m.get('content')}]
    except ctx.LLMUnavailable as ex:
        fb = _tr('{ex}，已用各字段的默认值', ex=ex)
    for f in fields:
        if f.get('type') == 'number' and isinstance(data.get(f['key']), str):
            try:
                data[f['key']] = float(data[f['key']])
            except ValueError:
                pass
    return Result(output={**data, 'data': data, **({'fallback': fb} if fb else {})}, messages=msgs, tokens=tokens)


# ---------------- 逻辑 ----------------
OPS_CMP = [{'value': v, 'label': l} for v, l in (('==', '等于'), ('!=', '不等于'), ('>', '大于'), ('>=', '大于等于'), ('<', '小于'), ('<=', '小于等于'),
                                                 ('contains', '包含'), ('not_contains', '不包含'), ('empty', '为空'), ('not_empty', '不为空'))]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compare(left, op, right):
    if op == 'empty':
        return left in (None, '', [], {}, 0)
    if op == 'not_empty':
        return left not in (None, '', [], {}, 0)
    if op in ('contains', 'not_contains'):
        hay = left if isinstance(left, (list, dict)) else str(left or '')
        r = (str(right) in hay) if not isinstance(hay, list) else (right in hay or str(right) in [str(x) for x in hay])
        return r if op == 'contains' else not r
    a, b = _num(left), _num(right)
    if a is not None and b is not None:
        return {'==': a == b, '!=': a != b, '>': a > b, '>=': a >= b, '<': a < b, '<=': a <= b}.get(op, False)
    a, b = str(left if left is not None else ''), str(right if right is not None else '')
    return {'==': a == b, '!=': a != b, '>': a > b, '>=': a >= b, '<': a < b, '<=': a <= b}.get(op, False)


@node_type('switch', '条件分支', 'logic', 'Switch', '按条件走不同的分支（从上到下第一个满足的；都不满足走"否则"）。', params=[
    P('cases', '分支', 'cases', default=[{'id': 'case1', 'name': '分支 1', 'logic': 'and', 'conditions': [{'left': '', 'op': '==', 'right': ''}]}])],
    outputs=['matched'], handles='dynamic')
def n_switch(ctx, node, p):
    for c in p.get('cases') or []:
        res = [compare(x.get('left'), x.get('op'), x.get('right')) for x in c.get('conditions') or []]
        ok = (all(res) if (c.get('logic') or 'and') == 'and' else any(res)) if res else False
        if ok:
            return Result(output={'matched': c['id'], 'matched_name': c.get('name')}, handles=[c['id']])
    return Result(output={'matched': 'else', 'matched_name': _tr('否则')}, handles=['else'])


@node_type('loop', '循环', 'logic', 'Refresh', '对列表里的每一项执行"循环体"出口后面的节点，结束后走"完成"出口。', params=[
    P('items', '列表', 'text', vars=True, required=True, help='引用一个列表，如 {query_1@rows}'), P('max_items', '最多几项', 'number', default=20)],
    outputs=['item', 'index', 'results', 'count'], handles='dynamic')
def n_loop(ctx, node, p):
    return Result(output={})          # 由执行器展开


@node_type('set_var', '变量赋值', 'logic', 'EditPen', '把值存到 {vars.名字}，后面的节点都能用。', params=[P('assignments', '赋值', 'kv', vars=True, default=[{'key': 'x', 'value': ''}])],
           outputs=['（各变量）'])
def n_setvar(ctx, node, p):
    out = {}
    for a in p.get('assignments') or []:
        if a.get('key'):
            ctx.vars[a['key']] = a.get('value')
            out[a['key']] = a.get('value')
    return Result(output=out)


@node_type('wait', '等待', 'logic', 'Timer', '等待一段时间再继续（最长 5 分钟）。', params=[P('seconds', '秒', 'number', default=5, min=0, max=300)], outputs=['waited'])
def n_wait(ctx, node, p):
    s = max(0.0, min(300.0, float(p.get('seconds') or 0)))
    if ctx.mode == 'dry':
        s = min(s, 1.0)
    time.sleep(s)
    return Result(output={'waited': s})


@node_type('reply', '输出回复', 'logic', 'Promotion', '把内容作为这次运行的结果（聊天里显示给用户）。', params=[P('content', '内容', 'textarea', vars=True, required=True)],
           outputs=['content'])
def n_reply(ctx, node, p):
    content = p.get('content')
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, default=str)
    ctx.replies.append(content)
    return Result(output={'content': content})


# ---------------- 人工 ----------------
@node_type('approval', '审批', 'human', 'Stamp', '暂停运行，等指定角色审批；审批人可以改参数再批，或驳回并写原因（原因会回给前面的 AI 节点）。', params=[
    P('title', '审批标题', 'text', vars=True, required=True), P('summary', '说明（给审批人看）', 'textarea', vars=True, default=''),
    P('payload', '待批内容（JSON，审批人可修改）', 'json', vars=True, default='{}'),
    P('assignee_role', '审批角色', 'select', default='manager', options=ROLE_OPTS), P('timeout_min', '超时（分钟，0=不超时）', 'number', default=0),
    P('on_timeout', '超时后', 'select', default='reject', options=[{'value': 'reject', 'label': '按驳回处理'}, {'value': 'approve', 'label': '自动通过'}])],
    outputs=['decision', 'payload', 'reason', 'by'], handles='dynamic')
def n_approval(ctx, node, p):
    ap = ctx.resume
    if ap is None:
        payload = p.get('payload')
        if isinstance(payload, str):
            try:
                payload = json.loads(payload) if payload.strip() else {}
            except ValueError:
                payload = {'内容': payload}
        return Result(wait={'kind': 'approval', 'title': p.get('title') or _tr('审批'), 'summary': p.get('summary') or '', 'payload': payload,
                            'role': p.get('assignee_role') or 'manager', 'timeout_min': p.get('timeout_min') or 0, 'on_timeout': p.get('on_timeout') or 'reject'})
    res = ap['result'] or {}
    if ap['status'] == 'approved':
        return Result(output={'decision': 'approved', 'payload': res.get('payload', ap['payload']), 'reason': ap['reason'] or '', 'by': ap['decided_by']},
                      handles=['approved'])
    ctx.sys['feedback'] = ap['reason'] or _tr('（驳回，未写原因）')
    if not any(h == 'rejected' for _, h in ctx.ex.g.out.get(ctx.nid, [])):
        raise Rejected(_tr('审批驳回：{v}', v=ap['reason'] or _tr('未写原因')))
    return Result(output={'decision': 'rejected', 'payload': ap['payload'], 'reason': ap['reason'] or '', 'by': ap['decided_by']}, handles=['rejected'])


@node_type('ask_user', '请用户填写', 'human', 'Edit', '暂停运行，等用户填一张表再继续。', params=[
    P('title', '标题', 'text', vars=True, required=True), P('summary', '说明', 'textarea', vars=True, default=''),
    P('fields', '要填的字段', 'fields', default=[{'key': 'answer', 'label': '回答', 'type': 'text', 'description': '', 'default': ''}]),
    P('assignee_role', '谁来填', 'select', default='manager', options=ROLE_OPTS), P('timeout_min', '超时（分钟，0=不超时）', 'number', default=0)],
    outputs=['values', '（各字段）'])
def n_ask(ctx, node, p):
    ap = ctx.resume
    if ap is None:
        return Result(wait={'kind': 'form', 'title': p.get('title') or _tr('请填写'), 'summary': p.get('summary') or '', 'fields': p.get('fields') or [],
                            'payload': {f['key']: f.get('default') for f in p.get('fields') or [] if f.get('key')}, 'role': p.get('assignee_role') or 'manager',
                            'timeout_min': p.get('timeout_min') or 0, 'on_timeout': 'reject'})
    if ap['status'] != 'approved':
        raise Rejected(ap['reason'] or _tr('用户没有填写'))
    vals = (ap['result'] or {}).get('payload') or {}
    return Result(output={**vals, 'values': vals})


# ---------------- 动作（写） ----------------
def _act_params(op):
    out = []
    for k, lab, t, req in ops.OPS[op]['fields']:
        if k == 'channel':
            out.append(P(k, _tr('渠道'), 'select', default='inbox', options=[{'value': 'inbox', 'label': _tr('站内信')}, {'value': 'board', 'label': _tr('安灯看板')},
                                                                       {'value': 'webhook', 'label': _tr('Webhook（企业微信/钉钉）')}]))
        elif k == 'webhook_kind':
            out.append(P(k, _tr('Webhook 类型'), 'select', default='wecom', show={'channel': ['webhook']},
                         options=[{'value': 'wecom', 'label': _tr('企业微信')}, {'value': 'dingtalk', 'label': _tr('钉钉')}, {'value': 'generic', 'label': _tr('通用 JSON')}]))
        elif k == 'webhook_url':
            out.append(P(k, lab, 'text', vars=True, show={'channel': ['webhook']}))
        elif k == 'to_role':
            out.append(P(k, _tr('接收角色'), 'text', vars=True, help=_tr('如 maint、qc、leader；可引用变量')))
        elif k == 'level' and op == 'notify':
            out.append(P(k, _tr('级别'), 'select', default='info', options=[{'value': 'info', 'label': _tr('一般')}, {'value': 'warn', 'label': _tr('重要')}, {'value': 'urgent', 'label': _tr('紧急')}]))
        elif k == 'with_8d':
            out.append(P(k, lab, 'bool', default=True))
        else:
            out.append(P(k, lab.split(' ')[0], 'json' if t == 'json' else 'textarea' if t == 'text' else 'text', vars=True, required=req,
                         help=lab[len(lab.split(' ')[0]):].strip()))
    if ops.OPS[op]['risk'] == 'high':
        out.append(P('require_approval', _tr('执行前需要审批'), 'bool', default=True, help=_tr('上游已经有审批节点时可以关掉')))
        out.append(P('approval_role', _tr('审批角色'), 'select', default='manager', options=ROLE_OPTS, show={'require_approval': [True]}))
    return out


def _make_action(op, ntype, icon):
    meta = ops.OPS[op]

    @node_type(ntype, meta['label'], 'action', icon, (_tr('写操作') + (_tr('（高风险：默认需要审批）') if meta['risk'] == 'high' else _tr('（低风险）')))
               + _tr('。试运行时只记录，不真正写入。'), params=_act_params(op), outputs=['text', 'code', 'id', 'link', 'dry_run'], risk=meta['risk'], op=op)
    def fn(ctx, node, p):
        return ctx.do_action(node, op, {k: v for k, v in p.items() if k not in ('require_approval', 'approval_role')}, p)
    return fn


for _op, _nt, _ic in (('repair_order', 'act_repair_order', 'SetUp'), ('ncr', 'act_ncr', 'CircleClose'), ('schedule_proposal', 'act_schedule', 'Calendar'),
                      ('andon_raise', 'act_andon_raise', 'Bell'), ('andon_close', 'act_andon_close', 'CircleCheck'), ('material_request', 'act_material_request', 'Box'),
                      ('notify', 'act_notify', 'Message'), ('report', 'act_report', 'Notebook')):
    _make_action(_op, _nt, _ic)


CATS = [('trigger', '触发'), ('data', '数据（只读）'), ('ai', 'AI'), ('logic', '逻辑'), ('human', '人工'), ('action', '动作（写）')]


def registry_json():
    return {'categories': [{'key': k, 'label': l} for k, l in CATS],
            'types': {k: {kk: vv for kk, vv in v.items() if kk != 'fn'} | {'common': [] if k == 'begin' else COMMON} for k, v in TYPES.items()},
            'events': {k: {'label': v[0], 'fields': v[1]} for k, v in EVENT_KINDS.items()}, 'roles': ROLES,
            'ops': {k: {'label': v['label'], 'risk': v['risk'], 'entity': v['entity']} for k, v in ops.OPS.items()},
            'tools': {k: v['label'] for k, v in T.DATA_TOOLS.items()}}
