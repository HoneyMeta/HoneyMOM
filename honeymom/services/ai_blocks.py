"""AI 生成式回答的"块"协议（右侧 AI 面板、askAi、日报、值守发现共用）。

回答 = 一段 Markdown 文字 + 若干结构化块。块由大模型在回答里用 ```blocks 代码块给出（JSON 数组），
或由后端根据工具结果补上（查询用到的数据来源、提到的单据）。前端 web/src/ai/AiBlocks.vue 渲染。

块（都是 JSON 对象，type 必填；id 由后端补）：
  {"type": "text",    "md": "Markdown 文字"}
  {"type": "kpis",    "title"?: "…", "items": [{"label": "废品率", "value": 0.023, "format": "pct"|"int"|"num", "unit"?: "件",
                                                 "delta"?: -0.004, "deltaPct"?: true, "good"?: "up"|"down", "ref"?: {entity, id}}]}
  {"type": "chart",   "kind": "line"|"bar"|"hbar"|"pie", "title": "…", "x": ["08:00", …], "unit"?: "件",
                      "series": [{"name": "机加", "data": [12, 15, …]}], "query"?: {"sql": "SELECT …", "x": "列", "y": ["列", …]}}
  {"type": "table",   "title"?: "…", "columns": [{"key": "code", "label": "设备", "num"?: true, "format"?: "pct"}], "rows": [{…}]}
  {"type": "refs",    "title"?: "相关记录", "items": [{"entity": "work_order", "id": 12, "code": "WO20261104001", "label": "…"}]}
  {"type": "sources", "items": [{"kind": "sql"|"kpi"|"query"|"kb"|"record"|"context", "label": "查了 设备台账、报警记录（12 行）", "sql"?: "…", "rows"?: 12}]}
  {"type": "callout", "tone": "info"|"ok"|"warn"|"risk", "title"?: "…", "md": "…"}
  {"type": "draft",   "draft": {…ai_drafts.draft_out…}}         动作草稿卡（审批后才执行）
文字里引用单据：[[work_order:WO20261104001]] 或 [[equipment:CNC3|3# 加工中心]]，前端渲染成可点的记录标签。

数字核对：图表、表格、KPI 里的数字和回答文字一样，要能在工具结果里找到（assistant.ungrounded 的口径）；
找不到的记在块的 "unverified": ["12.3", …]，前端显示"未核实"。
"""
from ..i18n import _tr
from .. import i18n as _i18n
import json
import re

import sqlalchemy as sa

from .. import db
from ..schema import BY_NAME, ENTITIES, TABLES

TYPES = ('text', 'kpis', 'chart', 'table', 'refs', 'sources', 'callout', 'draft')
CHART_KINDS = ('line', 'bar', 'hbar', 'pie', 'area')
FENCE = re.compile(r'```[ \t]*(blocks|chart|block|json[ \t]+blocks)[ \t]*\n(.*?)```', re.S | re.I)
REF = re.compile(r'\[\[([a-z_]+):([^\]|\n]{1,80})(?:\|([^\]\n]{1,80}))?\]\]')

REMIND = '（回答格式提醒：数字只用查到的；提到单据 / 设备写 [[实体:编号]]；排名、对比、趋势、构成类问题在末尾附 ```blocks JSON 图表块。不要提这条提醒。）'
PROMPT = '''回答格式（界面会把下面的"块"渲染成图表、表格、指标卡和可点的单据标签）：
- 先用 1~3 句话给结论，再给依据。提到具体单据/设备时写成 [[实体:编号]]，例如 [[work_order:WO20261104001]]、[[equipment:CNC3]]、[[ncr:NCR20261104002]]、[[repair_order:WX20261104001]]，界面会变成可点开的标签。
- 有趋势、对比、排名、构成时，在回答末尾附一个 ```blocks 代码块，内容是 JSON 数组，可以放这些块（数字必须来自查询结果，不要估算）：
  {"type":"kpis","items":[{"label":"废品率","value":0.023,"format":"pct","delta":-0.004,"good":"down"},{"label":"机加","value":412,"unit":"件","format":"int"}]}
  {"type":"chart","kind":"line|bar|hbar|pie","title":"近 8 小时机加产量","x":["08:00","09:00"],"series":[{"name":"机加","data":[52,61]}],"unit":"件"}
  {"type":"table","title":"OEE 最低的设备","columns":[{"key":"code","label":"设备"},{"key":"oee","label":"OEE","num":true,"format":"pct"}],"rows":[{"code":"CNC3","oee":0.62}]}
  {"type":"callout","tone":"warn","title":"需要关注","md":"一两句提醒"}
  比率用 0~1 的小数并写 "format":"pct"；件数写 "format":"int"。一张图最多 6 个系列、40 个点；表格最多 15 行。
- 排名、对比、趋势、构成类的问题一定要附图表块（必要时再加一张表）；简单的问题（一个数、一句话能说清）不要硬加图表。'''


# ---------------- 解析 ----------------
def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip().replace(',', '')
        pct = s.endswith('%')
        try:
            f = float(s.rstrip('%'))
        except ValueError:
            return None
        return f / 100 if pct else f
    return None


def _s(v, n=200):
    return ('' if v is None else str(v)).strip()[:n]


def normalize(b):
    """一个块 → 规范化后的块；不认识/不完整的返回 None。"""
    if not isinstance(b, dict):
        return None
    t = b.get('type')
    if t == 'chart' or (t is None and 'series' in b):
        kind = b.get('kind') or b.get('chart') or b.get('type_') or 'bar'
        if kind not in CHART_KINDS:
            kind = 'bar'
        x = [(_s(v, 40)) for v in (b.get('x') or b.get('labels') or [])][:200]
        series = []
        for s in (b.get('series') or [])[:8]:
            if not isinstance(s, dict):
                continue
            data = [_num(v) for v in (s.get('data') or [])][:200]
            series.append({'name': _s(s.get('name'), 40), 'data': data})
        if not series or (kind != 'pie' and not x):
            return None
        out = {'type': 'chart', 'kind': kind, 'title': _s(b.get('title'), 80), 'x': x, 'series': series, 'unit': _s(b.get('unit'), 12)}
        if b.get('format') in ('pct', 'int', 'num'):
            out['format'] = b['format']
        q = b.get('query')
        if isinstance(q, dict) and isinstance(q.get('sql'), str):
            out['query'] = {'sql': q['sql'][:2000], 'x': _s(q.get('x'), 60), 'y': [_s(y, 60) for y in (q.get('y') or [])][:6]}
        return out
    if t == 'kpis':
        items = []
        for it in (b.get('items') or [])[:8]:
            if not isinstance(it, dict) or not it.get('label'):
                continue
            v = _num(it.get('value'))
            o = {'label': _s(it.get('label'), 40), 'value': v if v is not None else _s(it.get('value'), 40)}
            for k in ('unit', 'hint'):
                if it.get(k):
                    o[k] = _s(it[k], 40)
            if it.get('format') in ('pct', 'int', 'num'):
                o['format'] = it['format']
            d = _num(it.get('delta'))
            if d is not None:
                o['delta'] = d
                o['deltaPct'] = bool(it.get('deltaPct'))
            if it.get('good') in ('up', 'down'):
                o['good'] = it['good']
            if isinstance(it.get('ref'), dict):
                o['ref'] = it['ref']
            items.append(o)
        return {'type': 'kpis', 'title': _s(b.get('title'), 80), 'items': items} if items else None
    if t == 'table':
        cols = []
        for cdef in (b.get('columns') or [])[:10]:
            if isinstance(cdef, str):
                cdef = {'key': cdef, 'label': cdef}
            if isinstance(cdef, dict) and cdef.get('key'):
                cols.append({'key': _s(cdef['key'], 60), 'label': _s(cdef.get('label') or cdef['key'], 40),
                             **({'num': True} if cdef.get('num') else {}), **({'format': cdef['format']} if cdef.get('format') in ('pct', 'int', 'num') else {})})
        rows = []
        for r in (b.get('rows') or [])[:50]:
            if isinstance(r, list) and cols:
                r = {c['key']: r[i] if i < len(r) else None for i, c in enumerate(cols)}
            if isinstance(r, dict):
                rows.append({k: (v if isinstance(v, (int, float)) or v is None else _s(v, 200)) for k, v in r.items()})
        if not cols and rows:
            cols = [{'key': k, 'label': k} for k in rows[0]]
        return {'type': 'table', 'title': _s(b.get('title'), 80), 'columns': cols, 'rows': rows} if cols and rows else None
    if t == 'callout':
        md = _s(b.get('md') or b.get('text'), 2000)
        return {'type': 'callout', 'tone': b.get('tone') if b.get('tone') in ('info', 'ok', 'warn', 'risk') else 'info', 'title': _s(b.get('title'), 80), 'md': md} if md else None
    if t == 'text':
        md = _s(b.get('md') or b.get('text'), 8000)
        return {'type': 'text', 'md': md} if md else None
    if t == 'refs':
        items = [it for it in (b.get('items') or []) if isinstance(it, dict) and it.get('entity') in BY_NAME][:20]
        return {'type': 'refs', 'title': _s(b.get('title'), 40), 'items': items} if items else None
    if t == 'sources':
        items = [it for it in (b.get('items') or []) if isinstance(it, dict) and it.get('label')][:20]
        return {'type': 'sources', 'items': items} if items else None
    if t == 'draft' and isinstance(b.get('draft'), dict):
        return {'type': 'draft', 'draft': b['draft']}
    return None


def _parse_fence(kind, body):
    try:
        j = json.loads(body.strip())
    except ValueError:
        # 模型偶尔在 JSON 前后加说明：取第一个 [ 或 { 到最后一个 ] 或 }
        m = re.search(r'[\[{].*[\]}]', body, re.S)
        if not m:
            return []
        try:
            j = json.loads(m.group(0))
        except ValueError:
            return []
    if isinstance(j, dict) and isinstance(j.get('blocks'), list):
        j = j['blocks']
    if isinstance(j, dict):
        j = [j]
    if not isinstance(j, list):
        return []
    if kind.lower() == 'chart':      # 旧格式：{"type": "bar"|"line"|"pie", ...}
        j = [{**x, 'kind': x['type'], 'type': 'chart'} if isinstance(x, dict) and x.get('type') in CHART_KINDS else x for x in j]
    return [b for b in (normalize(x) for x in j) if b]


def split_answer(text):
    """回答文字 → (去掉代码块后的文字, 块列表)。"""
    blocks = []

    def take(m):
        blocks.extend(_parse_fence(m.group(1), m.group(2)))
        return ''
    clean = FENCE.sub(take, text or '')
    clean = re.sub(r'\n{3,}', '\n\n', clean).strip()
    return clean, blocks


# ---------------- 单据引用 ----------------
_CODE_RX = None


def _code_patterns():
    """自动编号的实体（WO… NCR… WX… AD…）：前缀 + 日期 + 序号。"""
    global _CODE_RX
    if _CODE_RX is None:
        pats = []
        for e in ENTITIES:
            for f in e.fields:
                auto = getattr(f, 'auto', None)
                if f.name == 'code' and isinstance(auto, str) and '{' in auto:
                    pre = auto.split('{')[0]
                    if pre and pre.isalpha():
                        pats.append((e.name, pre))
        pats.sort(key=lambda x: -len(x[1]))
        _CODE_RX = [(ent, re.compile(rf'(?<![A-Za-z0-9]){pre}\d{{8,12}}(?!\d)')) for ent, pre in pats]
    return _CODE_RX


def resolve_ref(c, entity, key):
    if entity not in TABLES:
        return None
    t = TABLES[entity]
    key = str(key).strip()
    row = None
    for col in ('code', 'sn', 'name', 'username'):
        if col in t.c:
            row = c.execute(sa.select(t).where(t.c[col] == key).limit(1)).mappings().first()
            if row:
                break
    if not row and key.isdigit():
        row = c.execute(sa.select(t).where(t.c.id == int(key))).mappings().first()
    if not row:
        return None
    r = dict(row)
    code = r.get('code') or r.get('sn') or str(r['id'])
    name = r.get('name') or r.get('title') or r.get('fault') or r.get('description') or ''
    return {'entity': entity, 'id': r['id'], 'code': code, 'label': f"{code} {name}".strip()[:60], 'kind': BY_NAME[entity].label if entity in BY_NAME else entity}


def find_refs(text, limit=12):
    """文字里的 [[实体:编号]] 和自动编号的单据号 → 记录引用列表（只保留能在库里找到的）。"""
    want = []
    for m in REF.finditer(text or ''):
        want.append((m.group(1), m.group(2).strip()))
    for ent, rx in _code_patterns():
        for m in rx.finditer(text or ''):
            want.append((ent, m.group(0)))
    seen, out = set(), []
    if not want:
        return out
    with db.engine.connect() as c:
        for ent, key in want:
            if (ent, key) in seen:
                continue
            seen.add((ent, key))
            r = resolve_ref(c, ent, key)
            if r and not any(x['entity'] == r['entity'] and x['id'] == r['id'] for x in out):
                out.append(r)
            if len(out) >= limit:
                break
    return out


# ---------------- 数据来源 ----------------
def sources_from_steps(steps):
    from ..agent import tools as T
    items = []
    for i, s in enumerate(steps or []):
        tool, args = s.get('tool'), s.get('args') or {}
        rows = None
        try:
            res = json.loads(s.get('result') or 'null')
            if isinstance(res, dict) and isinstance(res.get('rows'), list):
                rows = len(res['rows'])
        except (ValueError, TypeError):
            res = None
        failed = s.get('status') == 'failed'
        if tool == 'query_sql':
            t = T._tables_in(args.get('sql') or '')
            items.append({'id': f'q{i + 1}', 'kind': 'sql', 'label': _tr('查询 {v}', v=_tr('、').join(t[:4]) or _tr('数据库')) + (_tr('（{rows} 行）', rows=rows) if rows is not None else ''),
                          'sql': (args.get('sql') or '')[:2000], 'rows': rows, 'failed': failed})
        elif tool == 'day_kpi':
            items.append({'id': f'q{i + 1}', 'kind': 'kpi', 'label': _tr('{v} 汇总指标（生产 / 质量 / 设备 / 能耗）', v=args.get('day') or _tr('今天')), 'failed': failed})
        elif tool == 'mes_query':
            items.append({'id': f'q{i + 1}', 'kind': 'query', 'label': T.phrase(tool, args).replace('正在读取', '').rstrip('…') + (_tr('（{rows} 行）', rows=rows) if rows is not None else ''),
                          'args': args, 'failed': failed})
        elif tool == 'kb_search':
            titles = [r.get('title') for r in ((res or {}).get('refs') or []) if isinstance(r, dict)]
            items.append({'id': f'q{i + 1}', 'kind': 'kb', 'label': _tr('知识库：{v}', v=_tr('、').join(titles[:3]) or args.get('q', '')), 'failed': failed})
        elif tool and tool.startswith('run_agent_'):
            items.append({'id': f'q{i + 1}', 'kind': 'agent', 'label': s.get('phrase') or _tr('运行智能体'), 'failed': failed})
        elif tool and tool in _ai_tool_names():          # 5.0 M6 统一工具层：记下工具和参数（"保存为技能"从这里还原查询计划，演示回放也能还原）
            items.append({'id': f'q{i + 1}', 'kind': 'tool', 'tool': tool, 'args': args, 'failed': failed,
                          'label': (s.get('phrase') or T.phrase(tool, args)).replace('正在', '').rstrip('…') + (_tr('（{rows} 行）', rows=rows) if rows is not None else '')})
    return items


def _ai_tool_names():
    from .ai_tools import TOOLS
    return TOOLS


def auto_table(steps):
    """模型没给块时：最后一次成功的 SQL 查询如果是一张小表（2~8 列、1~15 行），直接给一张表格块。"""
    for s in reversed(steps or []):
        if s.get('tool') != 'query_sql' or s.get('status') == 'failed':
            continue
        try:
            res = json.loads(s.get('result') or 'null')
        except ValueError:
            return None
        if not isinstance(res, dict):
            return None
        cols, rows = res.get('columns') or [], res.get('rows') or []
        if 2 <= len(cols) <= 8 and 1 <= len(rows) <= 15:
            numcols = {c for i, c in enumerate(cols) if all(isinstance(r[i], (int, float)) or r[i] is None for r in rows)}
            return {'type': 'table', 'title': _tr('查询结果'), 'columns': [{'key': c, 'label': c, **({'num': True} if c in numcols else {})} for c in cols],
                    'rows': [dict(zip(cols, r)) for r in rows], 'auto': True}
        return None
    return None


def chart_from_table(tb):
    """一张"标签 + 1~3 个数字列"的小表（排名 / 对比）→ 条形图块（数据就是表里的，不另外编）。不适合画图时返回 None。"""
    cols, rows = tb.get('columns') or [], tb.get('rows') or []
    if not (2 <= len(rows) <= 30) or len(cols) < 2:
        return None
    isnum = lambda k: all(isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool) for r in rows)
    labels = [c for c in cols if not isnum(c['key'])]
    nums = [c for c in cols if isnum(c['key']) and not re.search(r'(^|_)(id|code)$', c['key'], re.I)]
    if not labels or not nums:
        return None
    lab = labels[0]
    # 优先 OEE / 率 / 合计这类指标列；最多 3 个，量纲差太多的不放一起
    nums.sort(key=lambda c: 0 if re.search(r'oee|率|rate|总|合计|count|数|n$', c['key'] + c['label'], re.I) else 1)
    pick = [nums[0]]
    base = max(abs(r[nums[0]['key']]) for r in rows) or 1
    for c in nums[1:3]:
        m = max(abs(r[c['key']]) for r in rows) or 1
        if 0.2 <= m / base <= 5:
            pick.append(c)
    pct = all(0 <= r[c['key']] <= 1.0 for c in pick for r in rows) and any(re.search(r'oee|率|rate|pct|ratio', c['key'] + c['label'], re.I) for c in pick)
    x = [str(r.get(lab['key']) or '')[:24] for r in rows]
    kind = 'hbar' if len(rows) > 6 or max(len(v) for v in x) > 6 else 'bar'
    return {'type': 'chart', 'kind': kind, 'title': tb.get('title') if tb.get('title') not in ('', *_i18n.variants('查询结果')) else _tr('{label}（按{label2}）', label=pick[0]['label'], label2=lab['label']),
            'x': x, 'series': [{'name': c['label'], 'data': [r[c['key']] for r in rows]} for c in pick], 'unit': '', **({'format': 'pct'} if pct else {}), 'derived': 'table'}


def kpis_from_steps(steps):
    """模型查过当天汇总指标（day_kpi）却没给指标卡时：按查询结果补一组 KPI。"""
    for st in reversed(steps or []):
        if st.get('tool') != 'day_kpi' or st.get('status') == 'failed':
            continue
        try:
            k = json.loads(st.get('result') or 'null')
        except ValueError:
            return None
        if not isinstance(k, dict) or 'production' not in k:
            return None
        P, Q, N = k['production'], k['quality'], k.get('energy') or {}
        oee = [x['OEE'] for x in (k.get('equipment') or {}).get('oee') or [] if isinstance(x, dict) and isinstance(x.get('OEE'), (int, float))]
        items = [{'label': _tr('浇注 / 来料'), 'value': P.get('castings'), 'format': 'int', 'unit': '件'}, {'label': _tr('机加'), 'value': P.get('machined'), 'format': 'int', 'unit': '件'},
                 {'label': _tr('入库'), 'value': P.get('stored'), 'format': 'int', 'unit': '件'}, {'label': _tr('废品率'), 'value': Q.get('scrap_rate'), 'format': 'pct', 'good': 'down'}]
        if oee:
            items.append({'label': _tr('平均 OEE'), 'value': round(sum(oee) / len(oee), 4), 'format': 'pct'})
        if N.get('kwh'):
            items.append({'label': _tr('用电'), 'value': N['kwh'], 'format': 'int', 'unit': 'kWh', 'good': 'down'})
        return {'type': 'kpis', 'title': _tr('{get} 汇总', get=k.get('day', '')), 'items': [i for i in items if i['value'] is not None], 'derived': 'day_kpi'}
    return None


PCT_RX = re.compile(r'oee|率|rate|ratio|占比|可用|性能|质量', re.I)


def fix_formats(blocks):
    """模型常把 0~1 的比率写成 unit="%"：统一成 format=pct（显示 36.7% 而不是 0.37 %）。表格里比率列同理。"""
    for b in blocks:
        t = b.get('type')
        if t == 'chart' and not b.get('format'):
            vals = [v for s in b.get('series') or [] for v in s.get('data') or [] if isinstance(v, (int, float))]
            if vals and all(0 <= v <= 1.0 for v in vals) and (b.get('unit') in ('%', '％') or PCT_RX.search((b.get('title') or '') + ''.join(s.get('name') or '' for s in b['series']))):
                b['format'], b['unit'] = 'pct', ''
        elif t == 'kpis':
            for it in b.get('items') or []:
                v = it.get('value')
                if not it.get('format') and isinstance(v, (int, float)) and 0 <= v <= 1.0 and (it.get('unit') in ('%', '％') or PCT_RX.search(it.get('label') or '')):
                    it['format'] = 'pct'
                    it.pop('unit', None)
        elif t == 'table':
            for c in b.get('columns') or []:
                vals = [r.get(c['key']) for r in b.get('rows') or []]
                if not c.get('format') and vals and all(isinstance(v, (int, float)) and 0 <= v <= 1.0 for v in vals) and PCT_RX.search(c['key'] + c['label']):
                    c['format'], c['num'] = 'pct', True
    return blocks


def enrich(blocks):
    """没有图表、但有能画图的表格时，按表格数据补一张图（放在表格前面）。回放录制时也用。"""
    fix_formats(blocks)
    if any(b.get('type') == 'chart' for b in blocks):
        return blocks
    for i, b in enumerate(blocks):
        if b.get('type') == 'table':
            ch = chart_from_table(b)
            if ch:
                ch['id'] = (b.get('id') or 'b') + 'c'
                return blocks[:i] + [ch] + blocks[i:]
    return blocks


# ---------------- 数字核对 ----------------
def _grounded(n, have, corpus):
    s = f'{n:g}'
    if abs(n) <= 10 and float(n).is_integer():
        return True
    if s in corpus:
        return True
    return any(abs(c - n) <= max(0.011 * abs(n), 0.051) or abs(c * 100 - n) <= 0.6 or abs(c - n * 100) <= 0.6 or abs(c / 60 - n) <= 0.6
               or abs(c / 3600 - n) <= 0.06 for c in have)


def grounded_strict(n, have):
    """严格口径（整段文字核对用，语料大时宽松口径容易"碰巧对上"）：数值差 ≤ 0.6%；或比率 ↔ 百分数（0.053 ↔ 5.3%）。"""
    if abs(n) <= 10 and float(n).is_integer():
        return True
    for c in have:
        if abs(c - n) <= max(0.006 * abs(n), 0.051):
            return True
        if n <= 100 and 0 <= c <= 1.5 and abs(c * 100 - n) <= 0.06:
            return True
        if 0 <= n <= 1.5 and 0 <= c <= 100 and abs(c / 100 - n) <= 0.0006:
            return True
    return False


def check_numbers(blocks, corpus: str):
    """块里的数字能不能在数据里找到。找不到的写进块的 unverified；返回全部找不到的数字。"""
    from .assistant import _nums
    have = _nums(corpus)
    bad_all = []
    for b in blocks:
        vals = []
        if b['type'] == 'chart':
            vals = [v for s in b['series'] for v in s['data'] if isinstance(v, (int, float))]
        elif b['type'] == 'kpis':
            vals = [it['value'] for it in b['items'] if isinstance(it.get('value'), (int, float))]
        elif b['type'] == 'table':
            vals = [v for r in b['rows'] for v in r.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        bad = sorted({f'{v:g}' for v in vals if not _grounded(float(v), have, corpus)})
        if bad:
            b['unverified'] = bad[:8]
            bad_all += bad
    return bad_all


def compose(answer, steps=None, corpus='', context_items=None):
    """回答 + 工具调用 → {'text', 'blocks'}：解析模型给的块，补上单据引用和数据来源，核对数字。"""
    text, blocks = split_answer(answer)
    if not blocks:
        t = auto_table(steps)
        if t and '|' not in text:
            blocks.append(t)
    if not any(b['type'] == 'kpis' for b in blocks):
        kb = kpis_from_steps(steps)
        if kb:
            blocks.insert(0, kb)
    blocks = enrich(blocks)
    check_numbers(blocks, corpus or '')
    refs = find_refs(text + '\n' + json.dumps([b for b in blocks if b['type'] == 'table'], ensure_ascii=False))
    inline = {(m.group(1), m.group(2).strip()) for m in REF.finditer(text)}
    extra = [r for r in refs if (r['entity'], r['code']) not in inline]
    if extra:
        blocks.append({'type': 'refs', 'title': _tr('相关记录'), 'items': extra})
    src = sources_from_steps(steps)
    if context_items:
        src = [{'id': 'ctx', 'kind': 'context', 'label': _tr('你选中的：') + _tr('、').join(_s(x.get('label'), 30) for x in context_items[:4] if isinstance(x, dict))}] + src
    if src:
        blocks.append({'type': 'sources', 'items': src})
    for i, b in enumerate(blocks, 1):
        b['id'] = f'b{i}'
    return {'text': text, 'blocks': blocks, 'refs': refs}


# ---------------- 从查询结果生成块（钉到工作台的刷新、日报） ----------------
def chart_from_query(q, kind='bar', title='', unit=''):
    from .ai import run_sql
    res = run_sql(q['sql'], limit=200)
    cols = res['columns']
    xi = cols.index(q['x']) if q.get('x') in cols else 0
    ys = [y for y in (q.get('y') or []) if y in cols] or [c for i, c in enumerate(cols) if i != xi][:4]
    x = [str(r[xi]) for r in res['rows']]
    series = [{'name': y, 'data': [r[cols.index(y)] if isinstance(r[cols.index(y)], (int, float)) else None for r in res['rows']]} for y in ys]
    return {'type': 'chart', 'kind': kind, 'title': title, 'x': x, 'series': series, 'unit': unit, 'query': q}
