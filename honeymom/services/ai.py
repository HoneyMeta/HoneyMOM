"""AI：问数助手（大模型 + 只读 SQL 工具）、自动日报、异常解释、知识库问答。

大模型配置：config/llm.local.json（不入库）或环境变量 MOM_LLM_BASE_URL / MOM_LLM_API_KEY / MOM_LLM_MODEL（旧名 MES_LLM_*），
OpenAI 兼容接口。代码里没有默认的接口地址和模型：地址、模型、密钥三项都配齐才算已配置，否则所有功能退回规则版，页面照常可用。
"""
from ..env import get as env_get
from ..i18n import _tr
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db
from ..auth import current_user
from ..db import clock
from ..schema import BY_NAME, ENTITIES, TABLES
from . import ai_demo

router = APIRouter()
CFG_PATH = os.path.join(db.ROOT, 'config', 'llm.local.json')
DEFAULT = {'base_url': '', 'model': '', 'api_key': ''}      # no built-in endpoint: everything comes from the install's config


def llm_cfg(raw=False):
    """大模型配置。AI 总开关关闭或演示回放时 api_key 为空（所有调用退回规则版，不花 token）；raw=True 取文件里的真实配置。"""
    cfg = dict(DEFAULT)
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, encoding='utf-8') as f:
                cfg.update({k: v for k, v in json.load(f).items() if v})
        except Exception:
            pass
    for k in ('base_url', 'api_key', 'model'):
        v = env_get('MOM_LLM_' + k.upper())
        if v:
            cfg[k] = v
    if not raw:
        from . import ai_switch
        if not ai_switch.enabled() or ai_switch.demo():
            cfg['api_key'] = ''
    return cfg


def llm_configured(cfg) -> bool:
    """Base URL, model and key all set (there are no code defaults for any of them)."""
    return all(str(cfg.get(k) or '').strip() for k in ('base_url', 'model', 'api_key'))


def llm_available():
    return llm_configured(llm_cfg())


# 5.0：大模型调用计数（技能的定时运行要证明"不调用大模型"；GET /api/skills/llm_calls 读）
LLM_CALLS = {'n': 0}


def with_lang(messages):
    """Every model call ends its system context with the UI-language instruction (honeymom/i18n.py)."""
    from .. import i18n
    return list(messages) + [{'role': 'system', 'content': i18n.llm_instruction()}]


def _post(messages, tools=None, temperature=0.2):
    LLM_CALLS['n'] += 1
    messages = with_lang(messages)
    cfg = llm_cfg()
    body = {'model': cfg['model'], 'messages': messages, 'temperature': temperature}
    if tools:
        body['tools'] = tools
    req = urllib.request.Request(cfg['base_url'].rstrip('/') + '/chat/completions', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + cfg['api_key']})
    for attempt in range(4):       # 网关偶尔断连（SSL EOF、Remote end closed）或限流/5xx：退避重试
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if attempt == 3:
                raise
        time.sleep(2 * (attempt + 1))


def llm_chat(messages, tools=None, temperature=0.2, json_mode=False):
    """一次大模型调用：返回 (message, 用掉的 token 数)。智能体和聊天助手共用。"""
    LLM_CALLS['n'] += 1
    messages = with_lang(messages)
    cfg = llm_cfg()
    body = {'model': cfg['model'], 'messages': messages, 'temperature': temperature}
    if tools:
        body['tools'] = tools
    if json_mode:
        body['response_format'] = {'type': 'json_object'}
    req = urllib.request.Request(cfg['base_url'].rstrip('/') + '/chat/completions', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + cfg['api_key']})
    with urllib.request.urlopen(req, timeout=120) as r:
        j = json.loads(r.read())
    return j['choices'][0]['message'], int((j.get('usage') or {}).get('total_tokens') or 0)


def chat_once(prompt, system=None):
    system = system or _tr('你是制造业 MES 系统里的助手，回答简洁、务实，使用中文。')
    r = _post([{'role': 'system', 'content': system}, {'role': 'user', 'content': prompt}])
    return r['choices'][0]['message']['content']


# ---------------- 只读查询工具 ----------------
# 5.0：按提问人的权限和数据范围执行（auth / perms），两种方言都能跑（compat：PostgreSQL 上有 SQLite 风格函数，SQLite 上有 PostgreSQL 风格函数）。
HIDDEN = {'sys_user', 'setting', 'ai_log', 'assistant_thread', 'assistant_message', 'audit_log', 'sys_role', 'api_key', 'backup_log', 'ingest_seen',
          'schema_migration', 'ts_dirty', 'agent_action_log'}
# 不是通用实体的表 → 需要的权限点
TABLE_PERMS = {'tag_latest': 'equipment.ts.read', 'tag_sample': 'equipment.ts.read', 'ts_point': 'equipment.ts.read', 'ts_raw': 'equipment.ts.read',
               'ts_segment': 'equipment.ts.read', 'ts_1m': 'equipment.ts.read', 'ts_1h': 'equipment.ts.read',
               'agent': 'page.agents', 'agent_version': 'page.agents', 'agent_run': 'page.agents', 'agent_run_step': 'page.agents', 'agent_event': 'page.agents',
               'approval': 'page.approvals', 'ai_finding': 'page.ai.watch', 'ai_automation': 'page.ai.automations', 'ai_automation_fire': 'page.ai.automations',
               'ai_pin': 'page.home'}


def schema_text():
    lines = []
    for e in ENTITIES:
        if e.name in HIDDEN:
            continue
        cols = ', '.join(f"{f.name}({f.label}{'→' + f.ref if f.ref else ''}{'=' + '/'.join(f'{k}:{v}' for k, v in list(f.enum.items())[:8]) if f.enum else ''})"
                         for f in e.fields)
        from ..impl.cf import schema_hint as _cf_hint          # M3：自定义字段（ext 列）
        lines.append(_tr('{name}【{label}】: id, {cols}, created_at{v}', name=e.name, label=e.label, cols=cols, v=_cf_hint(e.name)))
    lines.append(_tr('tag_latest【点位最新值】: key, equipment→equipment, name, value, num, ts'))
    lines.append(_tr('tag_sample【点位采样（每 10 秒，少量点位）】: equipment, name, ts, num'))
    lines.append(_tr('ts_point【时序采集点】: id, key(数据源:设备/点位), equipment→equipment, name(点位名), dtype(num/bool/str), unit, last_ts, last_value, last_text'))
    lines.append(_tr('ts_raw【时序原始值，全部点位，变化即存】: point→ts_point, ts, value(数值；开关量 1/0), text(字符串值), q(质量 0 好 1 不确定 2 坏)'))
    lines.append(_tr('ts_segment【开关量/状态的区间】: point→ts_point, start, end(空=当前), value, text, dur_s'))
    lines.append(_tr('ts_1m / ts_1h【时序 1 分钟 / 1 小时汇总】: bucket, point→ts_point, n, vmin, vmax, vavg, vfirst, vlast'))
    return '\n'.join(lines)


def dialect_hint():
    """给大模型的方言提示（问数助手、右侧 AI 面板、智能体共用）。"""
    from ..compat import PORTABLE_HINT
    if db.engine.dialect.name == 'postgresql':
        d = _tr('PostgreSQL 方言（时间列是 timestamp 类型；也支持 SQLite 风格的 strftime / julianday / ifnull / group_concat）')
    else:
        d = _tr('SQLite 方言（时间列按 "YYYY-MM-DD HH:MM:SS" 存储；也支持 date_trunc / string_agg / greatest / least）')
    return d + _tr('；关联字段存的是对方表的 id。\n') + PORTABLE_HINT


_FROM = re.compile(r'\b(?:from|join)\s+([a-z_][a-z0-9_]*)((?:\s*(?:as\s+)?[a-z_][a-z0-9_]*)?(?:\s*,\s*[a-z_][a-z0-9_]*(?:\s+(?:as\s+)?[a-z_][a-z0-9_]*)?)*)')
_BLOCK = re.compile(r'\b(main|temp|public|pg_catalog|information_schema|_timescaledb_\w+|timescaledb_\w+)\s*\.|\bsqlite_(master|schema|temp_master)\b|\bpg_(read|ls|stat_file|sleep)\w*\s*\(')
_KW = {'where', 'group', 'order', 'limit', 'on', 'left', 'right', 'inner', 'outer', 'join', 'cross', 'union', 'having', 'as', 'using', 'natural', 'full', 'offset'}


def tables_in(sql: str) -> set:
    """SQL 里 FROM / JOIN 后面（含逗号列表）的表名。"""
    out = set()
    for m in _FROM.finditer(sql.lower()):
        out.add(m.group(1))
        for part in (m.group(2) or '').split(','):
            w = part.strip().split()
            if w and w[0] not in _KW:
                out.add(w[0])
    return out


def sql_table_perm(name: str) -> str | None:
    if name in BY_NAME:
        from .. import perms
        return perms.entity_perm(name, 'read')
    return TABLE_PERMS.get(name)


def run_sql(sql: str, limit=200, user=None):
    """执行一条只读 SELECT。user 缺省 = 当前请求 / 智能体的执行人（context.actor()）；系统内部调用（没有人）不限权限。
    - 表级：用到的每张表都要有查看权限（实体 <模块>.<表>.read，其它表见 TABLE_PERMS），否则报"无权查询"。
    - 数据范围：用户限定了工厂 / 车间 / 产线时，在 SQL 前面加同名 CTE 把这些表换成"范围内的行"（不能用 schema 前缀绕过）。
    - 只读：SQLite query_only，PostgreSQL 只读事务 + 20 秒超时。"""
    from .. import context, perms
    user = user if user is not None else context.actor()
    s = sql.strip().rstrip(';').strip()
    low = s.lower()
    if not (low.startswith('select') or low.startswith('with')) or ';' in s:
        raise ValueError(_tr('只允许单条 SELECT 查询'))
    for h in HIDDEN:
        if re.search(rf'\b{h}\b', low):
            raise ValueError(_tr('不允许查询 {h}', h=h))
    if re.search(r'\b(insert|update|delete|drop|alter|create|attach|detach|pragma|replace|vacuum|copy|grant|revoke|truncate|call|do|set|reindex)\b', low):
        raise ValueError(_tr('只允许查询'))
    if _BLOCK.search(low):
        raise ValueError(_tr('查询里不能带库名 / 系统表 / 文件函数前缀'))
    known = set(db.metadata.tables)
    used = {t for t in tables_in(s) if t in known}
    if user is not None:
        for t in sorted(used):
            p = sql_table_perm(t)
            if p is None or not perms.can(user, p):
                raise ValueError(_tr('你没有查询"{v}"的权限', v=BY_NAME[t].label if t in BY_NAME else t))
    is_pg = db.engine.dialect.name == 'postgresql'
    with db.engine.connect() as c:
        if is_pg:
            c.execute(sa.text('SET TRANSACTION READ ONLY'))
            c.execute(sa.text("SET LOCAL statement_timeout = '20s'"))
        ctes = _scope_ctes(c, user, s, known) if user is not None else []
        if ctes:
            if low.startswith('with recursive'):
                s = 'WITH RECURSIVE ' + ', '.join(ctes) + ', ' + s[len('with recursive'):].lstrip()
            elif low.startswith('with'):
                s = 'WITH ' + ', '.join(ctes) + ', ' + s[4:].lstrip()
            else:
                s = 'WITH ' + ', '.join(ctes) + ' ' + s
        if not is_pg:
            c.exec_driver_sql('PRAGMA query_only = ON')
        try:
            r = c.execute(sa.text(_escape_colons(s)))
            cols = list(r.keys())
            rows = [[db.jsonable(v) for v in row] for row in r.fetchmany(limit)]
        finally:
            if not is_pg:
                c.exec_driver_sql('PRAGMA query_only = OFF')
            else:
                c.rollback()
    return {'columns': cols, 'rows': rows}


def _escape_colons(s: str) -> str:
    """sa.text 会把 :名字 当成参数；SQL 里的 '08:00' 这类时间字面量要转义。"""
    return re.sub(r'(?<![:\w]):(?=[A-Za-z_])', lambda m: '\\:', s)


def _scope_ctes(c, user, sql, known):
    """数据范围受限时：SQL 里出现的、有归属规则的表 → CTE（同名，指向带 schema 前缀的真表 + 范围条件）。"""
    from .. import perms
    if perms.user_scope(user) is None:
        return []
    low = sql.lower()
    schema = c.execute(sa.text('SELECT current_schema()')).scalar() if c.dialect.name == 'postgresql' else 'main'
    out = []
    for name in sorted(known):
        if name not in BY_NAME or not re.search(rf'\b{name}\b', low):
            continue
        base = TABLES[name].to_metadata(sa.MetaData(), schema=schema)
        cond = perms.scope_cond(user, name, base)
        if cond is None:
            continue
        q = sa.select(*base.c).where(cond)
        body = str(q.compile(dialect=c.dialect, compile_kwargs={'literal_binds': True}))
        out.append(f'"{name}" AS ({body})')
    return out


TOOLS = [
    {'type': 'function', 'function': {'name': 'query_sql', 'description': '在 MES 数据库上执行一条只读 SELECT 查询，返回最多 200 行。',
                                      'parameters': {'type': 'object', 'properties': {'sql': {'type': 'string'}}, 'required': ['sql']}}},
    {'type': 'function', 'function': {'name': 'day_kpi', 'description': '取某天（YYYY-MM-DD，缺省为当天）的生产、质量、设备、能耗汇总指标。',
                                      'parameters': {'type': 'object', 'properties': {'day': {'type': 'string'}}}}},
]


def system_prompt():
    return _tr('你是"蜂元智能 MOM"的问数助手，服务于一家铸造 + 机加工厂。用户用中文问生产、质量、设备、能耗、库存的问题。\n做法：先想清楚要查哪些表，用 query_sql 查（{dialect_hint}），\n必要时多查几次，然后用简洁中文回答，给出关键数字和原因判断，不要编造数据。\n如果适合画图，在回答末尾附一个代码块，语言标记写 chart，内容是 JSON：{{"type":"bar"|"line"|"pie","title":"...","x":[...],"series":[{{"name":"...","data":[...]}}]}}。\n当前数据时钟：{now:%Y-%m-%d %H:%M}（"今天""昨天"按这个算）。\n数据表：\n{schema_text}', dialect_hint=dialect_hint(), now=clock.now(), schema_text=schema_text())


@router.post('/api/ai/ask')
def ask(body: dict = Body(...), user=Depends(current_user)):
    q = (body.get('q') or '').strip()
    hist = body.get('history') or []
    if not q:
        raise HTTPException(400, _tr('请输入问题'))
    return ai_demo.run('ask', 'ask', {'q': q}, live=lambda: _ask(q, hist, user), recordable=lambda r: r.get('llm'))


def _ask(q, hist, user):
    sqls = []
    if not llm_available():
        ans = rule_answer(q)
        _log(user, q, ans, [])
        return {'answer': ans, 'sqls': [], 'llm': False}
    msgs = [{'role': 'system', 'content': system_prompt()}] + [m for m in hist[-6:] if m.get('role') in ('user', 'assistant')] + [{'role': 'user', 'content': q}]
    try:
        for _ in range(6):
            r = _post(msgs, TOOLS)
            m = r['choices'][0]['message']
            calls = m.get('tool_calls') or []
            if not calls:
                ans = m.get('content') or ''
                break
            msgs.append({'role': 'assistant', 'content': m.get('content') or '', 'tool_calls': calls})
            for tc in calls:
                fn = tc['function']['name']
                try:
                    args = json.loads(tc['function'].get('arguments') or '{}')
                    if fn == 'query_sql':
                        sqls.append(args['sql'])
                        res = run_sql(args['sql'])
                    else:
                        res = day_kpi(args.get('day'))
                    content = json.dumps(res, ensure_ascii=False, default=str)[:12000]
                except Exception as ex:
                    content = json.dumps({'error': str(ex)}, ensure_ascii=False)
                msgs.append({'role': 'tool', 'tool_call_id': tc['id'], 'content': content})
        else:
            ans = _tr('查询步骤太多，没能得出结论，请把问题问得具体一些。')
    except Exception as ex:
        ans = _tr('（大模型接口调用失败：{ex}，下面是规则版回答）\n\n', ex=ex) + rule_answer(q)
    _log(user, q, ans, sqls)
    return {'answer': ans, 'sqls': sqls, 'llm': True}


def _log(user, q, a, sqls):
    with db.engine.begin() as c:
        db.insert(c, 'ai_log', {'user': user['username'], 'question': q, 'answer': a, 'sqls': '\n'.join(sqls), 'time': clock.now()}, raw=True)


def rule_answer(q):
    k = day_kpi()
    p, qa, eq, en = k['production'], k['quality'], k['equipment'], k['energy']
    parts = []
    if any(w in q for w in (_tr('产量'), _tr('产出'), _tr('多少件'), _tr('完工'), _tr('生产'))):
        parts.append(_tr('今天（{day}）浇注 {castings} 件、机加 {machined} 件、入库 {stored} 件、发运 {shipped} 件。', day=k['day'], castings=p['castings'], machined=p['machined'], stored=p['stored'], shipped=p['shipped']))
    if any(w in q for w in (_tr('废品'), _tr('不良'), _tr('报废'), _tr('质量'), _tr('缺陷'))):
        top = _tr('、').join(f"{x['name']} {x['n']}" for x in qa['pareto'][:3]) or _tr('无')
        parts.append(_tr('今天报废 {scrap} 件，废品率 {v:.2f}%；主要缺陷：{top}。未关闭 NCR {ncr_open} 条。', scrap=qa['scrap'], v=qa['scrap_rate'] * 100, top=top, ncr_open=qa['ncr_open']))
    if any(w in q for w in ('OEE', 'oee', _tr('设备'), _tr('效率'), _tr('停机'), _tr('报警'))):
        low = sorted([x for x in eq['oee'] if x['OEE'] is not None], key=lambda x: x['OEE'])[:3]
        parts.append(_tr('OEE 最低的设备：') + _tr('、').join(f"{x['name']} {x['OEE'] * 100:.0f}%" for x in low) + _tr('。今天报警 {alarms} 次。', alarms=eq['alarms']))
    if any(w in q for w in (_tr('能耗'), _tr('电'), _tr('吨耗'))):
        parts.append(_tr('今天用电 {kwh:.0f} kWh，电炉吨耗 {v} kWh/t。', kwh=en['kwh'], v=en['kwh_per_t'] or '—'))
    if not parts:
        parts.append(_tr('今天浇注 {castings}、入库 {stored}、废品率 {v:.2f}%、用电 {kwh:.0f} kWh。', castings=p['castings'], stored=p['stored'], v=qa['scrap_rate'] * 100, kwh=en['kwh']))
    from . import ai_switch
    parts.append(_tr('\n（演示模式：这个问题没有录制的 AI 回答，以下是按规则统计的常见指标。）') if ai_switch.demo() else
                 _tr('\n（当前没有配置大模型，只能回答常见指标；配置后可以用自然语言任意查询。）'))
    return '\n'.join(parts)


# ---------------- 日 KPI ----------------
def day_kpi(day=None):
    s, e = db.day_range(day)
    pe, pt, nt, dt, al = TABLES['part_event'], TABLES['part'], TABLES['ncr'], TABLES['defect_code'], TABLES['alarm']
    with db.engine.connect() as c:
        ev = dict(c.execute(sa.select(pe.c.event, sa.func.count()).where((pe.c.t >= s) & (pe.c.t < e)).group_by(pe.c.event)).all())
        castings = ev.get('poured', 0) + ev.get('received', 0)
        pareto = [{'name': r[0] or _tr('未分类'), 'n': r[1]} for r in c.execute(sa.select(dt.c.name, sa.func.count()).select_from(nt.outerjoin(dt, dt.c.id == nt.c.defect))
                                                                       .where((nt.c.found_at >= s) & (nt.c.found_at < e)).group_by(dt.c.name).order_by(sa.func.count().desc())).all()]
        from .quality import ncr_pending_count
        ncr_open = ncr_pending_count(c)      # 与不合格品页"待处理"同一口径
        alarms = c.execute(sa.select(sa.func.count()).select_from(al).where((al.c.start >= s) & (al.c.start < e))).scalar()
        top_al = [{'equipment': r[0], 'msg': r[1], 'n': r[2]} for r in c.execute(sa.select(TABLES['equipment'].c.name, al.c.msg, sa.func.count())
                                                                                  .join(TABLES['equipment'], TABLES['equipment'].c.id == al.c.equipment)
                                                                                  .where((al.c.start >= s) & (al.c.start < e)).group_by(TABLES['equipment'].c.name, al.c.msg)
                                                                                  .order_by(sa.func.count().desc()).limit(5)).all()]
        et = TABLES['energy_hour']
        kwh = c.execute(sa.select(sa.func.sum(et.c.kwh)).where((et.c.hour >= s) & (et.c.hour < e))).scalar() or 0
        ht = TABLES['heat']
        heats = c.execute(sa.select(sa.func.count(), sa.func.sum(ht.c.kg), sa.func.sum(ht.c.kwh), sa.func.avg(ht.c.CE)).where((ht.c.end >= s) & (ht.c.end < e))).first()
        from .equipment import oee_of
        oee = []
        for eq in db.rows(c, 'equipment', TABLES['equipment'].c.etype.in_(('cnc', 'molding', 'inspection', 'deburr', 'shotblast'))):
            o = oee_of(c, eq, s, e)
            if o:
                oee.append({'name': eq['name'], 'code': eq['code'], 'OEE': o['OEE'], 'A': o['A'], 'P': o['P'], 'Q': o['Q'], 'loss': o['loss']})
        wot = TABLES['work_order']
        wos = db.rows(c, 'work_order', wot.c.status.in_(('released', 'in_progress')))
        late = [w['code'] for w in wos if w['due'] and w['due'] < clock.now() and (w['qty_done'] or 0) < w['qty']]
    scrap = ev.get('scrap', 0)
    return {'day': s.date().isoformat(),
            'production': {'castings': castings, 'machined': ev.get('machined', 0), 'stored': ev.get('stored', 0), 'shipped': ev.get('shipped', 0),
                           'inspected': ev.get('inspected', 0), 'wo_open': len(wos), 'wo_late': late},
            'quality': {'scrap': scrap, 'scrap_rate': round(scrap / castings, 4) if castings else 0, 'rework': ev.get('rework', 0), 'pareto': pareto, 'ncr_open': ncr_open},
            'equipment': {'oee': oee, 'alarms': alarms, 'top_alarms': top_al},
            'energy': {'kwh': round(kwh, 1), 'heats': heats[0] or 0, 'melt_t': round((heats[1] or 0) / 1000, 2),
                       'kwh_per_t': round((heats[2] or 0) / ((heats[1] or 0) / 1000), 1) if heats[1] else None, 'ce_avg': round(heats[3], 3) if heats[3] else None}}


@router.get('/api/ai/kpi')
def kpi(day: str = '', user=Depends(current_user)):
    return day_kpi(day or None)


# ---------------- 日报 ----------------
def rule_report(k):
    p, q, e, en = k['production'], k['quality'], k['equipment'], k['energy']
    oee = [x for x in e['oee'] if x['OEE'] is not None]
    avg = sum(x['OEE'] for x in oee) / len(oee) if oee else None
    low = sorted(oee, key=lambda x: x['OEE'])[:3]
    lines = [_tr('## {day} 生产日报', day=k['day']), '', _tr('### 生产'),
             _tr('- 浇注/来料 {castings} 件，机加 {machined} 件，检测 {inspected} 件，入库 {stored} 件，发运 {shipped} 件', castings=p['castings'], machined=p['machined'], inspected=p['inspected'], stored=p['stored'], shipped=p['shipped']),
             _tr('- 在制工单 {wo_open} 个', wo_open=p['wo_open']) + (_tr('，已逾期：{join}', join=_tr('、').join(p['wo_late'])) if p['wo_late'] else _tr('，无逾期')), '', _tr('### 质量'),
             _tr('- 报废 {scrap} 件，废品率 {v:.2f}%，返修 {rework} 件，未关闭 NCR {ncr_open} 条', scrap=q['scrap'], v=q['scrap_rate'] * 100, rework=q['rework'], ncr_open=q['ncr_open']),
             _tr('- 主要缺陷：') + (_tr('、').join(f"{x['name']} {x['n']}" for x in q['pareto'][:4]) or _tr('无')), '', _tr('### 设备'),
             _tr('- 平均 OEE {v:.1f}%', v=avg * 100) if avg is not None else _tr('- 暂无 OEE 数据'),
             _tr('- OEE 最低：') + (_tr('、').join(_tr('{name} {v:.0f}%（可用 {v2:.0f}% / 性能 {v3:.0f}% / 质量 {v4:.0f}%）', name=x['name'], v=x['OEE'] * 100, v2=x['A'] * 100, v3=x['P'] * 100, v4=x['Q'] * 100) for x in low) or '—'),
             _tr('- 报警 {alarms} 次', alarms=e['alarms']) + (_tr('：') + _tr('；').join(f"{a['equipment']} {a['msg']} ×{a['n']}" for a in e['top_alarms'][:3]) if e['top_alarms'] else ''), '',
             _tr('### 能耗'), _tr('- 全厂用电 {kwh:.0f} kWh；熔炼 {heats} 炉 {melt_t} t，吨耗 {v} kWh/t，平均碳当量 {v2}', kwh=en['kwh'], heats=en['heats'], melt_t=en['melt_t'], v=en['kwh_per_t'] or '—', v2=en['ce_avg'] or '—')]
    return '\n'.join(lines)


@router.post('/api/ai/daily')
def daily(body: dict = Body(default={}), user=Depends(current_user)):
    """生成日报（含 AI 解读，需要"AI 日报"）。报告结构和规则版见 services/ai_daily.py；AI 关闭时用 /api/report/daily/generate。"""
    from . import ai_daily
    return ai_daily.generate(body.get('day') or ai_daily.default_day(), use_ai=True)


@router.get('/api/ai/daily')
def daily_list(user=Depends(current_user)):
    with db.engine.connect() as c:
        rows = db.rows(c, 'daily_report', order=TABLES['daily_report'].c.day.desc(), limit=30)
    return [{'day': db.jsonable(r['day']), 'content': r['content'], 'by': r['by'], 'generated_at': db.jsonable(r['generated_at'])} for r in rows]


# ---------------- 异常解释 ----------------
@router.get('/api/ai/explain/{eid}')
def explain(eid: int, user=Depends(current_user)):
    from .equipment import oee_of
    now = clock.now()
    with db.engine.connect() as c:
        eq = db.get(c, 'equipment', eid)
        if not eq:
            raise HTTPException(404, _tr('设备不存在'))
        al = TABLES['alarm']
        alarms = [{'msg': r[0], 'start': db.jsonable(r[1]), 'dur_min': round((r[2] or 0) / 60, 1)} for r in
                  c.execute(sa.select(al.c.msg, al.c.start, al.c.dur_s).where((al.c.equipment == eid) & (al.c.start >= now - timedelta(hours=24))).order_by(al.c.start.desc()).limit(20)).all()]
        o24 = oee_of(c, eq, now - timedelta(hours=24), now)
        o4 = oee_of(c, eq, now - timedelta(hours=4), now)
        tl = db.metadata.tables['tag_latest']
        tags = {r[0]: r[1] for r in c.execute(sa.select(tl.c.name, tl.c.value).where(tl.c.equipment == eid)).all()}
        tools = db.rows(c, 'tool', equipment=eid)
    facts = {'设备': f"{eq['code']} {eq['name']}", '近24小时OEE': o24, '近4小时OEE': o4, '近24小时报警': alarms,
             '当前点位': {k: v for k, v in list(tags.items())[:40]}, '刀具': [{'刀位': t['code'], '已用': t['used'], '寿命': t['life_limit'], 'AI磨损%': t['wear_pct']} for t in tools]}
    rule = []
    if o4 and o24:
        if o4['A'] < o24['A'] - 0.1:
            rule.append(_tr('近 4 小时可用率 {v:.0f}% 明显低于 24 小时平均 {v2:.0f}%，主要是停机时间增加。', v=o4['A'] * 100, v2=o24['A'] * 100))
        if o4['P'] < o24['P'] - 0.1:
            rule.append(_tr('性能率下降（{v:.0f}% → {v2:.0f}%）：常见原因是等料、等人或节拍变慢。', v=o24['P'] * 100, v2=o4['P'] * 100))
        if o4['Q'] < o24['Q'] - 0.03:
            rule.append(_tr('质量率下降（{v:.0f}% → {v2:.0f}%）：检查刀具和尺寸补偿。', v=o24['Q'] * 100, v2=o4['Q'] * 100))
    cnt = Counter(a['msg'] for a in alarms)
    for msg, n in cnt.most_common(3):
        rule.append(_tr('报警「{msg}」出现 {n} 次。', msg=msg, n=n) + (_tr('属于人员到位问题，查看班组人数和岗位安排。') if '操作工' in msg else _tr('建议查看维修记录和点检情况。') if '故障' in msg else ''))
    worn = [t for t in tools if (t['wear_pct'] or 0) >= 80]
    for t in worn:
        rule.append(_tr('刀位 {code} AI 估计磨损 {wear_pct:.0f}%，建议提前换刀。', code=t['code'], wear_pct=t['wear_pct']))
    text = '\n'.join(rule) or _tr('近期没有发现明显异常。')
    by = 'rule'
    rec = ai_demo.lookup('watch', 'explain', {'equipment': eq['code']}) if ai_demo.ai_switch.demo() else None
    if rec:
        return {'text': rec, 'by': 'demo', 'facts': facts}
    if llm_available():
        try:
            text = chat_once(_tr('你是设备工程师。根据下面这台设备的数据，用 3~5 条中文要点说明当前是否异常、最可能的原因、建议怎么处理。只基于数据。\n\n')
                             + json.dumps(facts, ensure_ascii=False, default=str))
            by = 'ai'
            if ai_demo.ai_switch.recording():
                ai_demo.save('watch', 'explain', {'equipment': eq['code']}, text)
        except Exception as ex:
            text += _tr('\n（AI 解释失败：{ex}）', ex=ex)
    return {'text': text, 'by': by, 'facts': facts}


# ---------------- 知识库问答 ----------------
def _grams(s):
    s = re.sub(r'\s+', '', s or '')
    return Counter(s[i:i + 2] for i in range(len(s) - 1))


def kb_search(q: str, k: int = 3):
    """知识库检索（二元组打分，按段落取最相关的一段）。智能体的"知识库/SOP 检索"节点和聊天助手共用。"""
    with db.engine.connect() as c:
        docs = db.rows(c, 'kb_doc')
    qg = _grams(q)
    scored = []
    tg_w = 0.3
    for d in docs:
        paras = [p.strip() for p in re.split(r'\n\s*\n', d['content'] or '') if p.strip()]
        tg = _grams(d['title'])
        tsc = sum(min(v, tg[k2]) for k2, v in qg.items()) / (1 + len(qg))
        ps = sorted(((sum(min(v, _grams(p)[k2]) for k2, v in qg.items()) / (1 + len(qg)), i, p) for i, p in enumerate(paras)), key=lambda x: -x[0])
        if not ps:
            continue
        best = ps[0][0] + tg_w * tsc
        # 取得分最高的两段（按原文顺序拼起来），给模型更完整的上下文
        top = sorted([x for x in ps[:2] if x[0] > 0] or ps[:1], key=lambda x: x[1])
        scored.append((best, d, '\n'.join(p for _, _, p in top)))
    scored.sort(key=lambda x: -x[0])
    return [{'id': d['id'], 'title': d['title'], 'snippet': para[:600], 'score': round(sc, 3)} for sc, d, para in scored[:k] if sc > 0]


def kb_answer(q: str):
    refs = kb_search(q)
    if not refs:
        return {'answer': _tr('知识库里没有找到相关内容。'), 'refs': []}
    if llm_available():
        ctx = '\n\n'.join(_tr('【{title}】\n{snippet}', title=r['title'], snippet=r['snippet']) for r in refs)
        try:
            ans = chat_once(_tr('根据以下资料回答问题，资料里没有的就说不知道，并注明出处标题。\n\n资料：\n{ctx}\n\n问题：{q}', ctx=ctx, q=q))
            return {'answer': ans, 'refs': refs, 'llm': True}
        except Exception:
            pass
    return {'answer': _tr('找到以下相关内容：\n\n') + '\n\n'.join(_tr('**{title}**：{snippet}', title=r['title'], snippet=r['snippet']) for r in refs), 'refs': refs, 'llm': False}


@router.post('/api/ai/kb_ask')
def kb_ask(body: dict = Body(...), user=Depends(current_user)):
    q = (body.get('q') or '').strip()
    return ai_demo.run('kb', 'kb_ask', {'q': q}, live=lambda: kb_answer(q), recordable=lambda r: r.get('llm'))


@router.get('/api/ai/status')
def status(user=Depends(current_user)):
    from . import ai_switch
    cfg = llm_cfg(raw=True)
    return {'available': llm_available(), 'configured': llm_configured(cfg), 'model': cfg['model'], 'base_url': cfg['base_url'],
            'config_file': 'config/llm.local.json', 'mode': ai_switch.status()['mode']}
