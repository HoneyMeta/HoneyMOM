"""AI 技能（5.0 M6）：把一次好的问答"保存为技能"，以后手动 / 定时 / 自动化规则触发时重放同一份查询计划。

技能 = 问题 + 查询计划（回答时实际调用的只读工具和参数，services/ai_tools.py）+ 参数（AI 建议把哪些字面量变成参数，如"3号线"→ {line}、日期 → {period}）
     + 输出格式（用了哪些块：图表 / 表格 / 指标卡）+ 运行方式（手动 / 每小时 / 每天 / 班末 / cron）+ 结果去向（值守信息流 / 通知某些人 / 日报）。

运行（run）：按计划逐步调用工具（确定的，不调用大模型）→ 拼出块和摘要；只有打开"AI 结论"时才调用一次大模型写一段结论（演示模式回放录制）。
关掉"AI 结论"的技能运行时大模型调用次数为 0（ai.LLM_CALLS 计数，测试会核对）。查询出错（点位 / 表结构 / 设备改了）→ 这次运行记"失效"（broken），
技能标成失效并给负责人发站内信，定时运行暂停，直到负责人修改。

格式（ai_skill 表）：
  plan    = {"steps": [{"tool": "cycle_analysis", "args": {"line": "{line}", "period": "{period}"}}]}
  params  = [{"key": "line", "label": "产线", "type": "line|equipment|period|day|str|number", "default": "机加线", "options"?: […]}]
            时间段参数 P 还提供 {P_start} / {P_end}（YYYY-MM-DD HH:MM:SS），给 SQL 用。
  output  = {"blocks": ["chart", "table", "kpis", "callout"]}
  schedule= {"mode": "manual|hourly|daily|shift_end|cron", "time": "07:30", "cron": "0 8 * * 1-5", "enabled": true, "params": {…}}
  targets = {"feed": true, "notify_users": ["leader1"], "notify_roles": ["maint"], "daily": true}
  share_roles = ["*"] 所有人可见 / ["leader", "maint"] / [] 只有自己；按工厂（plant）分库。

接口（需要 AI 功能"AI 技能"）：
  GET  /api/skills?scope=mine|shared|all      技能库        GET /api/skills/meta     可用工具、运行方式、角色
  POST /api/skills/from_message {message_id}   从一条助手回答生成技能草稿（计划 + 建议的参数 + 输出格式）
  POST /api/skills  PUT /api/skills/{id}  DELETE /api/skills/{id}   GET /api/skills/{id}（含版本、最近运行）
  POST /api/skills/{id}/run {params, narrative?}   手动运行            GET /api/skills/{id}/runs   GET /api/skills/runs/{run_id}
  POST /api/skills/tick {force_ids?}            立即检查一遍定时（skill.manage；测试 / 运维用）     GET /api/skills/llm_calls
技能也作为 MCP 工具（skill_<id>）对外提供（mcp_expose）。
"""
from __future__ import annotations

from ..i18n import _tr
import json
import logging
import re
import time
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import audit, auth, context, db, perms
from ..db import clock
from ..schema import ROLES, TABLES, ai_skill_run_t, ai_skill_t, ai_skill_version_t, message_t

log = logging.getLogger('mes.skills')
router = APIRouter()
PERM_USE = perms.declare('skill.use', '使用技能（查看共享技能、手动运行）', 'AI 助手')
PERM_EDIT = perms.declare('skill.edit', '保存 / 编辑自己的技能、设定时', 'AI 助手')
PERM_MANAGE = perms.declare('skill.manage', '管理全部技能（含别人的）', 'AI 助手')

MODES = {'manual': '手动', 'hourly': '每小时', 'daily': '每天', 'shift_end': '每班结束', 'cron': 'cron 表达式'}
PTYPES = {'line': '产线', 'equipment': '设备', 'period': '时间段', 'day': '日期', 'str': '文字', 'number': '数字'}
PERIODS = {'today': '今天', 'yesterday': '昨天', 'shift': '本班', 'last_shift': '刚结束的班次', '8h': '近 8 小时', '24h': '近 24 小时', 'week': '近 7 天'}
BLOCK_TYPES = ('kpis', 'chart', 'table', 'callout')
BROKEN_HINTS = ('没有采集点', '找不到', '没有这个', 'no such column', 'no such table', 'does not exist', 'undefinedcolumn', 'undefinedtable', '不存在', '没有这种')


# ───────────────────────── 读写 ─────────────────────────
def _out(r, user=None):
    if not r:
        return None
    d = {k: (db.jsonable(v) if not isinstance(v, (dict, list)) else v) for k, v in dict(r).items()}
    d['plan'] = d.get('plan') or {'steps': []}
    d['params'] = d.get('params') or []
    d['schedule'] = d.get('schedule') or {'mode': 'manual'}
    d['targets'] = d.get('targets') or {}
    d['share_roles'] = d.get('share_roles') or []
    d['mode_label'] = MODES.get(d['schedule'].get('mode'), '手动')
    if user is not None:
        d['can_edit'] = can_edit(user, d)
    return d


def visible(user, sk) -> bool:
    if user is None or perms.can(user, PERM_MANAGE) or sk.get('owner') == user.get('username'):
        return True
    if not perms.can(user, PERM_USE):
        return False
    roles = set(sk.get('share_roles') or [])
    if '*' in roles or roles & set(user.get('role_list') or []):
        u_sc = perms.user_scope(user)
        return not sk.get('plant') or u_sc is None or sk['plant'] in perms.scope_sets(u_sc)['plant']
    return False


def can_edit(user, sk) -> bool:
    return user is None or perms.can(user, PERM_MANAGE) or (sk.get('owner') == user.get('username') and perms.can(user, PERM_EDIT))


def get(sid) -> dict | None:
    with db.engine.connect() as c:
        r = c.execute(sa.select(ai_skill_t).where(ai_skill_t.c.id == sid)).mappings().first()
    return _out(r)


def find(key, user=None) -> dict | None:
    """id / key / 名称 → 技能（只找 user 看得到的）。"""
    k = str(key or '').strip()
    if not k:
        return None
    t = ai_skill_t
    with db.engine.connect() as c:
        rows = c.execute(sa.select(t).where(sa.or_(*([t.c.id == int(k)] if k.isdigit() else []), t.c.key == k, t.c.name == k))).mappings().all()
        if not rows:
            rows = c.execute(sa.select(t).where(t.c.name.like(f'%{k}%')).limit(5)).mappings().all()
    for r in rows:
        d = _out(r)
        if visible(user, d) and d['status'] != 'disabled':
            return d
    return None


def list_skills(user, scope='all', plant=None):
    with db.engine.connect() as c:
        rows = [_out(r, user) for r in c.execute(sa.select(ai_skill_t).order_by(ai_skill_t.c.id)).mappings()]
    out = []
    for d in rows:
        if not visible(user, d):
            continue
        if scope == 'mine' and d['owner'] != user['username']:
            continue
        if scope == 'shared' and d['owner'] == user['username']:
            continue
        if plant and d.get('plant') not in (None, plant):
            continue
        out.append(d)
    return out


def _snapshot(d):
    return {k: d.get(k) for k in ('name', 'description', 'question', 'plan', 'params', 'output', 'narrative', 'narrative_prompt', 'schedule', 'targets', 'share_roles',
                                  'mcp_expose', 'plant')}


def _clean(body, cur=None):
    v = {}
    if 'name' in body or cur is None:
        v['name'] = str(body.get('name') or '').strip()[:190]
        if not v['name']:
            raise HTTPException(400, _tr('请填写技能名称'))
    for k in ('description', 'question', 'narrative_prompt'):
        if k in body:
            v[k] = str(body.get(k) or '')[:4000]
    if 'plan' in body or cur is None:
        steps = []
        for st in ((body.get('plan') or {}).get('steps') or []):
            from . import ai_tools
            if not isinstance(st, dict) or st.get('tool') not in ai_tools.TOOLS:
                raise HTTPException(400, _tr('查询计划里有不认识的工具：{get}', get=(st or {}).get('tool')))
            if ai_tools.TOOLS[st['tool']].write:
                raise HTTPException(400, _tr('技能只能用只读工具'))
            steps.append({'tool': st['tool'], 'args': st.get('args') if isinstance(st.get('args'), dict) else {}, 'title': str(st.get('title') or '')[:80]})
        if not steps:
            raise HTTPException(400, _tr('查询计划是空的'))
        v['plan'] = {'steps': steps}
    if 'params' in body:
        ps, seen = [], set()
        for p in body.get('params') or []:
            k = re.sub(r'[^a-zA-Z0-9_]', '', str((p or {}).get('key') or ''))[:30]
            if not k or k in seen:
                continue
            seen.add(k)
            ps.append({'key': k, 'label': str(p.get('label') or k)[:40], 'type': p.get('type') if p.get('type') in PTYPES else 'str',
                       'default': p.get('default'), **({'options': p['options'][:50]} if isinstance(p.get('options'), list) else {})})
        v['params'] = ps
    if 'output' in body:
        o = body.get('output') or {}
        v['output'] = {'blocks': [b for b in (o.get('blocks') or list(BLOCK_TYPES)) if b in BLOCK_TYPES]}
    if 'schedule' in body:
        s = body.get('schedule') or {}
        mode = s.get('mode') if s.get('mode') in MODES else 'manual'
        sch = {'mode': mode, 'enabled': bool(s.get('enabled', mode != 'manual')), 'params': s.get('params') if isinstance(s.get('params'), dict) else {}}
        if mode == 'daily':
            sch['time'] = _hhmm(s.get('time') or '07:30')
        if mode == 'cron':
            sch['cron'] = str(s.get('cron') or '').strip()
            try:
                cron_next(sch['cron'], datetime(2026, 1, 1))
            except ValueError as ex:
                raise HTTPException(400, _tr('cron 表达式不对：{ex}', ex=ex))
        v['schedule'] = sch
    if 'targets' in body:
        t = body.get('targets') or {}
        v['targets'] = {'feed': bool(t.get('feed')), 'daily': bool(t.get('daily')), 'notify_users': [str(x)[:80] for x in (t.get('notify_users') or [])][:20],
                        'notify_roles': [r for r in (t.get('notify_roles') or []) if r in ROLES]}
    if 'share_roles' in body:
        v['share_roles'] = [r for r in (body.get('share_roles') or []) if r == '*' or r in ROLES]
    for k in ('narrative', 'mcp_expose'):
        if k in body:
            v[k] = bool(body[k])
    if 'plant' in body:
        v['plant'] = int(body['plant']) if str(body.get('plant') or '').isdigit() else None
    return v


def _hhmm(s):
    m = re.match(r'^(\d{1,2}):(\d{2})$', str(s).strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise HTTPException(400, _tr('时间格式不对：{s}（HH:MM）', s=s))
    return f'{int(m.group(1)):02d}:{m.group(2)}'


def create(body, user, key=None):
    v = _clean(body)
    now = datetime.now().replace(microsecond=0)
    v.setdefault('params', [])
    v.setdefault('output', {'blocks': list(BLOCK_TYPES)})
    v.setdefault('schedule', {'mode': 'manual', 'enabled': False, 'params': {}})
    v.setdefault('targets', {'feed': False, 'daily': False, 'notify_users': [], 'notify_roles': []})
    v.setdefault('share_roles', [])
    v.setdefault('narrative', False)
    v.setdefault('mcp_expose', True)
    owner = (user or {}).get('username') or 'admin'
    with db.engine.begin() as c:
        sid = c.execute(ai_skill_t.insert().values(**v, key=key, owner=owner, status='active', version=1, run_count=0, tokens_total=0, cost_total=0.0,
                                                   source_message=body.get('source_message'), created_at=now, updated_at=now)).inserted_primary_key[0]
        c.execute(ai_skill_version_t.insert().values(skill=sid, version=1, snapshot=_snapshot(v), note=_tr('新建'), by=owner, created_at=now))
        audit.audit(c, 'create', 'ai_skill', sid, detail={'name': v['name'], 'steps': len(v['plan']['steps']), 'key': key}, target=f'ai_skill:{sid}', user=user)
    _reschedule(sid)
    return get(sid)


def update(sid, body, user):
    cur = get(sid)
    if not cur:
        raise HTTPException(404, _tr('技能不存在'))
    if not can_edit(user, cur):
        raise HTTPException(403, _tr('只有技能的负责人（或有"管理全部技能"权限的人）能修改'))
    v = _clean(body, cur)
    changed = {k: x for k, x in v.items() if cur.get(k) != x}
    now = datetime.now().replace(microsecond=0)
    if 'owner' in body and perms.can(user, PERM_MANAGE) and body['owner']:
        changed['owner'] = str(body['owner'])[:80]
    with db.engine.begin() as c:
        vals = {**changed, 'updated_at': now}
        if changed:
            vals['version'] = (cur['version'] or 1) + 1
            if cur['status'] == 'broken':
                vals['status'] = 'active'              # 修改后重新启用（失效的技能等负责人改）
                vals['broken_reason'] = None
            c.execute(ai_skill_version_t.insert().values(skill=sid, version=vals['version'], snapshot=_snapshot({**cur, **changed}),
                                                         note=str(body.get('note') or _tr('修改：') + _tr('、').join(changed))[:290], by=user['username'], created_at=now))
            audit.audit(c, 'update', 'ai_skill', sid, diff={k: [str(cur.get(k))[:200], str(x)[:200]] for k, x in changed.items()}, target=f'ai_skill:{sid}', user=user)
        if body.get('status') in ('active', 'disabled'):
            vals['status'] = body['status']
        c.execute(ai_skill_t.update().where(ai_skill_t.c.id == sid).values(**vals))
    _reschedule(sid)
    return get(sid)


def delete(sid, user):
    cur = get(sid)
    if not cur:
        raise HTTPException(404, _tr('技能不存在'))
    if not can_edit(user, cur):
        raise HTTPException(403, _tr('只有技能的负责人能删除'))
    with db.engine.begin() as c:
        c.execute(ai_skill_t.delete().where(ai_skill_t.c.id == sid))
        audit.audit(c, 'delete', 'ai_skill', sid, detail={'name': cur['name']}, target=f'ai_skill:{sid}', user=user)
    return {'ok': True}


# ───────────────────────── 参数代入 ─────────────────────────
PH = re.compile(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}')


def param_context(sk, given: dict):
    """技能参数 → 代入用的值（缺省值、定时参数、调用时给的，后者优先；时间段参数另给 _start / _end）。"""
    from .ai_tools import ToolError, period
    vals = {}
    for p in sk.get('params') or []:
        v = given.get(p['key'], (sk.get('schedule') or {}).get('params', {}).get(p['key'], p.get('default')))
        if p['type'] == 'number' and v not in (None, ''):
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(400, _tr('参数 {label} 要是数字', label=p['label']))
        vals[p['key']] = v
        if p['type'] in ('period', 'day') and v not in (None, ''):
            try:
                s, e, lab = period({'period': v} if p['type'] == 'period' else {'day': v})
            except ToolError as ex:
                raise HTTPException(400, _tr('参数 {label}：{ex}', label=p['label'], ex=ex))
            vals[p['key'] + '_start'] = s.strftime('%Y-%m-%d %H:%M:%S')
            vals[p['key'] + '_end'] = e.strftime('%Y-%m-%d %H:%M:%S')
            vals[p['key'] + '_label'] = lab
    for k, v in given.items():
        vals.setdefault(k, v)
    return vals


def subst(v, ctx):
    if isinstance(v, str):
        m = PH.fullmatch(v.strip())
        if m and m.group(1) in ctx:
            return ctx[m.group(1)]
        return PH.sub(lambda mm: str(ctx[mm.group(1)]) if mm.group(1) in ctx and ctx[mm.group(1)] is not None else mm.group(0), v)
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            y = subst(x, ctx)
            if y not in (None, '') or not isinstance(x, str) or not PH.search(x):
                out[k] = y
        return out
    if isinstance(v, list):
        return [subst(x, ctx) for x in v]
    return v


# ───────────────────────── 运行 ─────────────────────────
def _is_broken(msg: str) -> bool:
    m = (msg or '').lower()
    return any(h.lower() in m for h in BROKEN_HINTS)


def _price():
    with db.engine.connect() as c:
        try:
            return float(db.setting(c, 'llm_price_per_1k') or 0.02)
        except ValueError:
            return 0.02


def run(sid, params=None, trigger='manual', user=None, narrative=None):
    """运行技能。user = 谁触发（定时 / 自动化：负责人）。返回运行记录。"""
    from . import ai_tools
    from .ai import LLM_CALLS
    sk = get(sid)
    if not sk:
        raise HTTPException(404, _tr('技能不存在'))
    run_as = user if user is not None else (auth.user_by_name(sk['owner']) or auth.user_by_name('admin'))
    if user is not None and not visible(user, sk):
        raise HTTPException(403, _tr('你看不到这个技能'))
    ctx = param_context(sk, dict(params or {}))
    t0, n0 = time.time(), LLM_CALLS['n']
    started = datetime.now().replace(microsecond=0)
    steps, blocks, summaries, err, status = [], [], [], None, 'ok'
    want = set((sk.get('output') or {}).get('blocks') or BLOCK_TYPES)
    with context.acting(run_as, 'agent' if trigger in ('schedule', 'automation') else 'ai'):
        for i, st in enumerate(sk['plan']['steps']):
            args = subst(st.get('args') or {}, ctx)
            s0 = time.time()
            try:
                res = ai_tools.call(st['tool'], args)
                steps.append({'tool': st['tool'], 'args': args, 'status': 'done', 'ms': int((time.time() - s0) * 1000), 'rows': res.get('count'),
                              'summary': (res.get('summary') or '')[:600]})
                if res.get('summary'):
                    summaries.append(res['summary'])
                bl = [b for b in (res.get('blocks') or []) if b.get('type') in want]
                if not bl and 'table' in want and isinstance(res.get('rows'), list) and res['rows'] and isinstance(res['rows'][0], dict):
                    cols = list(res['rows'][0])[:8]
                    bl = [{'type': 'table', 'title': st.get('title') or ai_tools.TOOLS[st['tool']].title, 'columns': [{'key': k, 'label': k} for k in cols],
                           'rows': [{k: r.get(k) for k in cols} for r in res['rows'][:15]]}]
                blocks += bl
            except Exception as ex:
                msg = str(ex.detail if isinstance(ex, HTTPException) else ex).splitlines()[0][:400] if str(ex) else type(ex).__name__
                steps.append({'tool': st['tool'], 'args': args, 'status': 'failed', 'error': msg, 'ms': int((time.time() - s0) * 1000)})
                err = _tr('第 {v} 步（{v2}）：{msg}', v=i + 1, v2=ai_tools.TOOLS[st['tool']].title if st['tool'] in ai_tools.TOOLS else st['tool'], msg=msg)
                status = 'broken' if (isinstance(ex, ai_tools.ToolError) and _is_broken(msg)) or _is_broken(msg) or isinstance(ex, sa.exc.DBAPIError) else 'error'
                break
    summary = '\n'.join(summaries)
    nar, by, tokens = '', 'none', 0
    use_nar = sk.get('narrative') if narrative is None else narrative
    if status == 'ok' and use_nar:
        nar, by, tokens = _narrate(sk, ctx, summary)
    llm_calls = LLM_CALLS['n'] - n0
    cost = round(tokens / 1000 * _price(), 4)
    ended = datetime.now().replace(microsecond=0)
    blocks = _ids(blocks)
    who = (user or {}).get('username') or f"schedule:{sk['owner']}"
    with db.engine.begin() as c:
        rid = c.execute(ai_skill_run_t.insert().values(skill=sid, version=sk['version'], trigger=trigger, params={k: v for k, v in ctx.items() if not k.endswith(('_start', '_end', '_label'))},
                                                       status=status, blocks=blocks, narrative=nar, by=by, error=err, steps=steps, llm_calls=llm_calls, tokens=tokens,
                                                       cost=cost, ms=int((time.time() - t0) * 1000), run_by=who, targets=sk.get('targets') if trigger in ('schedule', 'automation') else {},
                                                       data_time=clock.now(), started_at=started, ended_at=ended)).inserted_primary_key[0]
        upd = {'last_run_at': ended, 'last_status': status, 'run_count': (sk['run_count'] or 0) + 1, 'tokens_total': (sk['tokens_total'] or 0) + tokens,
               'cost_total': round((sk['cost_total'] or 0) + cost, 4)}
        if status == 'broken':
            upd.update(status='broken', broken_reason=err)
        c.execute(ai_skill_t.update().where(ai_skill_t.c.id == sid).values(**upd))
        if status == 'broken' and sk['status'] != 'broken':
            db.insert(c, 'notice', {'title': _tr('技能「{name}」失效了，请修改', name=sk['name']), 'content': _tr('{err}\n\n可能是点位、设备或表结构改了。定时运行已暂停，修改技能后自动恢复。', err=err),
                                    'to_user': sk['owner'], 'channel': 'inbox', 'level': 'warn', 'link': f"/ai/skills?id={sid}", 'source': '技能', 'read': False,
                                    'time': clock.now()}, raw=True)
        if status == 'ok' and trigger in ('schedule', 'automation'):
            _deliver(c, sk, rid, summary, nar)
    out = run_get(rid)
    out['summary'] = summary
    return out


def _ids(blocks):
    for i, b in enumerate(blocks, 1):
        b['id'] = f's{i}'
    return blocks


def _deliver(c, sk, rid, summary, nar):
    t = sk.get('targets') or {}
    text = (nar + '\n\n' if nar else '') + summary
    for u in t.get('notify_users') or []:
        db.insert(c, 'notice', {'title': _tr('技能「{name}」', name=sk['name']), 'content': text[:3800], 'to_user': u, 'channel': 'inbox', 'level': 'info',
                                'link': f"/ai/skills?run={rid}", 'source': '技能', 'read': False, 'time': clock.now()}, raw=True)
    for r in t.get('notify_roles') or []:
        db.insert(c, 'notice', {'title': _tr('技能「{name}」', name=sk['name']), 'content': text[:3800], 'to_role': r, 'channel': 'inbox', 'level': 'info',
                                'link': f"/ai/skills?run={rid}", 'source': '技能', 'read': False, 'time': clock.now()}, raw=True)


def _narrate(sk, ctx, summary):
    """结论（可选）：只在这里调用大模型。演示模式回放录制（按技能 key / 名称精确匹配）。返回 (文字, by, tokens)。"""
    from . import ai_demo
    from .ai import llm_available, llm_chat
    tok = [0]

    def live():
        if not llm_available():
            return None
        m, tk = llm_chat([{'role': 'system', 'content': _tr('你是工厂的 AI 助手。根据下面这次定时查询的结果，用 2~4 句中文写结论：最重要的发现、和平时比是否异常、建议先做什么。只用给出的数字，不要编造，不要客套。') + ((_tr('\n额外要求：') + sk['narrative_prompt']) if sk.get('narrative_prompt') else '')},
                          {'role': 'user', 'content': _tr('技能：{name}\n参数：{dumps}\n结果：\n{v}', name=sk['name'], dumps=json.dumps({k: v for k, v in ctx.items() if not k.endswith(('_start', '_end'))}, ensure_ascii=False, default=str), v=summary[:6000])}])
        tok[0] = tk
        return m.get('content') or ''
    try:
        r = ai_demo.run('skills', 'narrate', {'skill': sk.get('key') or sk['name'], 'text': sk['name']}, live=live, fallback=lambda: None,
                        recordable=lambda r: bool(r), mark=lambda r: {'text': r, 'demo': True}, exact=True)
    except Exception as ex:
        log.warning('skill narrative failed: %s', ex)
        return '', 'none', 0
    if isinstance(r, dict) and r.get('demo'):
        return r['text'], 'demo', 0
    if isinstance(r, str) and r.strip():
        return r.strip(), 'ai', tok[0]
    return '', 'none', 0


def run_get(rid):
    with db.engine.connect() as c:
        r = c.execute(sa.select(ai_skill_run_t).where(ai_skill_run_t.c.id == rid)).mappings().first()
    if not r:
        raise HTTPException(404, _tr('运行记录不存在'))
    return {k: (db.jsonable(v) if not isinstance(v, (dict, list)) else v) for k, v in dict(r).items()}


# ───────────────────────── 定时 ─────────────────────────
def _field(spec, lo, hi):
    out = set()
    for part in spec.split(','):
        step = 1
        if '/' in part:
            part, st = part.split('/', 1)
            step = int(st)
        if part in ('*', ''):
            a, b = lo, hi
        elif '-' in part:
            a, b = (int(x) for x in part.split('-', 1))
        else:
            a = b = int(part)
        if a < lo or b > hi or a > b or step < 1:
            raise ValueError(_tr('{spec} 超出范围 {lo}-{hi}', spec=spec, lo=lo, hi=hi))
        out |= set(range(a, b + 1, step))
    return out


def cron_next(expr, after: datetime) -> datetime:
    """5 段 cron（分 时 日 月 周，周 0/7=周日），返回 after 之后的下一次。"""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(_tr('要 5 段：分 时 日 月 周'))
    mi, hr, dom, mon, dow = (_field(parts[0], 0, 59), _field(parts[1], 0, 23), _field(parts[2], 1, 31), _field(parts[3], 1, 12), _field(parts[4], 0, 7))
    dow = {d % 7 for d in dow}
    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(60 * 24 * 370):
        if t.month in mon and t.day in dom and ((t.weekday() + 1) % 7) in dow and t.hour in hr and t.minute in mi:
            return t
        t += timedelta(minutes=1)
    raise ValueError(_tr('一年内都不会触发'))


def next_run(sch, after: datetime):
    from .ai_tools import shift_windows
    mode = (sch or {}).get('mode') or 'manual'
    if mode == 'manual' or not (sch or {}).get('enabled', True):
        return None
    if mode == 'hourly':
        return after.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    if mode == 'daily':
        h, m = (int(x) for x in (sch.get('time') or '07:30').split(':'))
        t = after.replace(hour=h, minute=m, second=0, microsecond=0)
        return t if t > after else t + timedelta(days=1)
    if mode == 'shift_end':
        ends = sorted({e for _s, e, _n in shift_windows(after, 1) if e > after})
        return ends[0] if ends else after + timedelta(hours=8)
    if mode == 'cron':
        return cron_next(sch.get('cron') or '0 * * * *', after)
    return None


def _reschedule(sid, after=None):
    sk = get(sid)
    if not sk:
        return
    nxt = next_run(sk['schedule'], after or clock.now()) if sk['status'] == 'active' else None
    with db.engine.begin() as c:
        c.execute(ai_skill_t.update().where(ai_skill_t.c.id == sid).values(next_run_at=nxt))


def tick(force_ids=None):
    """到点的定时技能各跑一次（按数据时钟）。force_ids：不管时间立即跑这些（测试 / 运维）。返回 [运行记录]。"""
    now = clock.now()
    out = []
    with db.engine.connect() as c:
        rows = [_out(r) for r in c.execute(sa.select(ai_skill_t).where(ai_skill_t.c.status == 'active')).mappings()]
    for sk in rows:
        sch = sk['schedule']
        forced = force_ids is not None and sk['id'] in force_ids
        if not forced:
            if sch.get('mode', 'manual') == 'manual' or not sch.get('enabled', True):
                continue
            nra = db.parse_dt(sk.get('next_run_at'))
            if nra is None:
                _reschedule(sk['id'], now)
                continue
            if now < nra:
                continue
        try:
            r = run(sk['id'], sch.get('params') or {}, trigger='schedule', user=None)
            out.append(r)
        except Exception as ex:
            log.exception('scheduled skill %s', sk['id'])
            out.append({'skill': sk['id'], 'status': 'error', 'error': str(ex)[:300]})
        _reschedule(sk['id'], now)
    return out


async def loop():
    import asyncio
    from .ai_switch import enabled
    await asyncio.sleep(25)
    while True:
        try:
            if enabled('skills'):
                await asyncio.to_thread(tick)
        except Exception:
            log.exception('skills tick')
        await asyncio.sleep(20)


# ───────────────────────── 从一条回答生成技能 ─────────────────────────
def plan_from_message(mid, user):
    from . import ai_tools
    with db.engine.connect() as c:
        m = c.execute(sa.select(message_t).where(message_t.c.id == mid)).mappings().first()
        if not m or m['role'] != 'assistant':
            raise HTTPException(404, _tr('没有这条回答'))
        th = c.execute(sa.select(TABLES_THREAD.c.user).where(TABLES_THREAD.c.id == m['thread'])).scalar()
        if th != user['username'] and not perms.can(user, PERM_MANAGE):
            raise HTTPException(404, _tr('没有这条回答'))
        q = c.execute(sa.select(message_t.c.content).where((message_t.c.thread == m['thread']) & (message_t.c.role == 'user') & (message_t.c.id < mid))
                      .order_by(message_t.c.id.desc()).limit(1)).scalar() or ''
    steps = []
    for s in m['steps'] or []:
        if s.get('status') == 'failed' or s.get('tool') not in ai_tools.TOOLS or ai_tools.TOOLS[s['tool']].write:
            continue
        steps.append({'tool': s['tool'], 'args': s.get('args') or {}})
    cards = m['cards'] or []
    bl = next((x for x in cards if x.get('kind') == 'blocks'), {}) or {}
    blocks = bl.get('blocks') or []
    if not steps:                                  # 演示回放没有过程记录：从"数据来源"块还原查询
        src = next((b for b in blocks if b.get('type') == 'sources'), None)
        for it in (src or {}).get('items') or []:
            if it.get('failed'):
                continue
            if it.get('kind') == 'sql' and it.get('sql'):
                steps.append({'tool': 'query_sql', 'args': {'sql': it['sql']}})
            elif it.get('kind') == 'query' and isinstance(it.get('args'), dict):
                steps.append({'tool': 'mes_query', 'args': it['args']})
            elif it.get('kind') == 'kpi':
                steps.append({'tool': 'day_kpi', 'args': {}})
            elif it.get('kind') == 'tool' and it.get('tool') in ai_tools.TOOLS:
                steps.append({'tool': it['tool'], 'args': it.get('args') or {}})
    seen, uniq = set(), []
    for s in steps:
        k = json.dumps(s, sort_keys=True, ensure_ascii=False)
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    if not uniq:
        raise HTTPException(400, _tr('这条回答没有用到可以复用的查询（只有文字），不能保存为技能'))
    types = [t for t in BLOCK_TYPES if any(b.get('type') == t for b in blocks)] or ['table', 'chart']
    return q, uniq[:8], types


from ..schema import thread_t as TABLES_THREAD  # noqa: E402


def suggest_params(question, steps):
    """建议哪些字面量变成参数（规则：设备编号 / 产线 / 时间段 / 日期 / SQL 里的日期和设备编号），返回 (新计划, 参数列表)。"""
    from .ai_tools import _s
    with db.engine.connect() as c:
        lines = {r.code: r.name for r in c.execute(sa.select(TABLES['line'].c.code, TABLES['line'].c.name))}
        eqs = {r.code for r in c.execute(sa.select(TABLES['equipment'].c.code))}
    params, out = {}, []

    def add(key, label, typ, default):
        if key not in params:
            params[key] = {'key': key, 'label': label, 'type': typ, 'default': default}
        return '{' + key + '}'
    for st in steps:
        a = dict(st.get('args') or {})
        tool = st['tool']
        for k in list(a):
            v = a[k]
            if v in (None, ''):
                continue
            if k == 'line':
                a[k] = add('line', _tr('产线'), 'line', v)
            elif k == 'equipment':
                a[k] = add('equipment', _tr('设备'), 'equipment', v)
            elif k == 'period':
                a[k] = add('period', _tr('时间段'), 'period', v)
            elif k == 'day':
                a[k] = add('period', _tr('时间段'), 'period', v if v not in (_s(clock.now().date()),) else 'today')
                a['period'] = a.pop(k)
            elif k == 'hours' and tool != 'mes_query':
                a.pop(k)
                a['period'] = add('period', _tr('时间段'), 'period', f'{float(v):g}h')
            elif k == 'hours':
                a[k] = add('hours', _tr('最近几小时'), 'number', v)
            elif k in ('start', 'end') and tool != 'query_sql':
                a.pop(k)
                a['period'] = add('period', _tr('时间段'), 'period', 'today')
        if tool == 'query_sql' and isinstance(a.get('sql'), str):
            sql = a['sql']
            dates = re.findall(r"'(\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}(?::\d{2})?)?)'", sql)
            if dates:
                add('period', _tr('时间段'), 'period', 'today' if dates[0][:10] == clock.now().strftime('%Y-%m-%d') else dates[0][:10])
                for i, d in enumerate(dict.fromkeys(dates)):
                    sql = sql.replace(f"'{d}'", "'{period_" + ('start' if i == 0 else 'end') + "}'", 1 if i == 0 else -1)
            for code in sorted(eqs, key=len, reverse=True):
                if f"'{code}'" in sql:
                    sql = sql.replace(f"'{code}'", "'" + add('equipment', _tr('设备'), 'equipment', code) + "'")
                    break
            a['sql'] = sql
        out.append({**st, 'args': a})
    q = question or ''
    for code, name in lines.items():
        if (name and name in q) or (code and code in q):
            if 'line' not in params:
                params['line'] = {'key': 'line', 'label': _tr('产线'), 'type': 'line', 'default': name or code}
    return out, list(params.values())


def suggest(mid, user):
    """从一条回答生成技能草稿（不保存）。名称 / 说明 / 参数中文名可以让 AI 润色（演示模式回放录制；没有就用规则）。"""
    q, steps, types = plan_from_message(mid, user)
    plan, params = suggest_params(q, steps)
    name = re.sub(r'[？?。！!]+$', '', q.strip())[:30] or _tr('我的技能')
    draft = {'name': name, 'question': q, 'description': _tr('由问答"{v}"保存', v=q[:60]), 'plan': {'steps': plan}, 'params': params, 'output': {'blocks': types},
             'narrative': False, 'schedule': {'mode': 'manual', 'enabled': False, 'params': {}}, 'targets': {'feed': True, 'daily': False, 'notify_users': [], 'notify_roles': []},
             'share_roles': [], 'mcp_expose': True, 'source_message': mid, 'by': 'rule'}
    ai = _ai_polish(q, plan, params)
    if ai:
        draft['name'] = ai.get('name') or draft['name']
        draft['description'] = ai.get('description') or draft['description']
        labels = {p.get('key'): p.get('label') for p in ai.get('params') or [] if isinstance(p, dict)}
        for p in draft['params']:
            if labels.get(p['key']):
                p['label'] = str(labels[p['key']])[:40]
        draft['by'] = ai.get('_by', 'ai')
    return draft


def _ai_polish(q, plan, params):
    from . import ai_demo
    from .ai import llm_available, llm_chat
    from .ai_switch import enabled
    if not enabled('skills'):
        return None

    def live():
        if not llm_available():
            return None
        m, _tk = llm_chat([{'role': 'system', 'content': _tr('把一次问答保存成可重复运行的"技能"。给出 JSON：{"name": 12 字以内的技能名, "description": 一句话说明, "params": [{"key": 参数 key（保持原样）, "label": 参数中文名}]}。只输出 JSON。')},
                           {'role': 'user', 'content': json.dumps({'question': q, 'plan': plan, 'params': params}, ensure_ascii=False, default=str)[:6000]}], json_mode=True)
        try:
            return json.loads(m.get('content') or '{}')
        except ValueError:
            return None
    try:
        r = ai_demo.run('skills', 'polish', {'text': q}, live=live, fallback=lambda: None, recordable=lambda r: bool(r))
    except Exception:
        return None
    if isinstance(r, dict) and r.get('demo'):
        r = {**r, '_by': 'demo'}
    return r if isinstance(r, dict) else None


# ───────────────────────── 内置技能 ─────────────────────────
BUILTIN = [
    {'key': 'shift_summary', 'name': '班末产量小结', 'description': '刚结束的班次：产量、废品率、各设备 OEE，班末自动发到值守信息流。',
     'question': '刚结束的这个班产量、质量和设备情况怎么样？',
     'plan': {'steps': [{'tool': 'query_production', 'args': {'period': '{period}'}}, {'tool': 'query_quality', 'args': {'period': '{period}'}},
                        {'tool': 'query_oee', 'args': {'period': '{period}'}}]},
     'params': [{'key': 'period', 'label': '班次', 'type': 'period', 'default': 'last_shift'}], 'output': {'blocks': ['kpis', 'chart', 'table']},
     'schedule': {'mode': 'shift_end', 'enabled': False, 'params': {}}, 'targets': {'feed': True, 'daily': False, 'notify_users': [], 'notify_roles': ['leader']}},
    {'key': 'bottleneck_action', 'name': '瓶颈动作日报', 'description': '动作级节拍：瓶颈工位 → 瓶颈动作，慢了多少秒、折合多少件和钱，进生产日报。',
     'question': '机加线昨天的瓶颈工位和瓶颈动作是什么？损失多少？',
     'plan': {'steps': [{'tool': 'cycle_analysis', 'args': {'line': '{line}', 'period': '{period}'}}]},
     'params': [{'key': 'line', 'label': '产线', 'type': 'line', 'default': ''}, {'key': 'period', 'label': '时间段', 'type': 'period', 'default': 'yesterday'}],
     'output': {'blocks': ['chart', 'table', 'callout']}, 'schedule': {'mode': 'daily', 'time': '07:30', 'enabled': False, 'params': {}},
     'targets': {'feed': True, 'daily': True, 'notify_users': [], 'notify_roles': ['engineer', 'maint']}},
    {'key': 'oee_loss_top5', 'name': 'OEE 损失 Top 5', 'description': '近 24 小时 OEE 最低的设备和最大的 5 项损失（停机、换型、待机…）。',
     'question': '近 24 小时 OEE 损失最大的是哪几项？',
     'plan': {'steps': [{'tool': 'query_oee', 'args': {'period': '{period}'}}]}, 'params': [{'key': 'period', 'label': '时间段', 'type': 'period', 'default': '24h'}],
     'output': {'blocks': ['chart', 'table']}, 'schedule': {'mode': 'daily', 'time': '08:00', 'enabled': False, 'params': {}},
     'targets': {'feed': True, 'daily': True, 'notify_users': [], 'notify_roles': []}},
    {'key': 'quality_pareto', 'name': '当日质量帕累托', 'description': '当天不合格品按缺陷的帕累托（条数、累计占比）。',
     'question': '今天不合格品主要是哪些缺陷？',
     'plan': {'steps': [{'tool': 'query_quality', 'args': {'period': '{period}'}}]}, 'params': [{'key': 'period', 'label': '日期', 'type': 'period', 'default': 'today'}],
     'output': {'blocks': ['chart', 'table']}, 'schedule': {'mode': 'daily', 'time': '22:00', 'enabled': False, 'params': {}},
     'targets': {'feed': True, 'daily': True, 'notify_users': [], 'notify_roles': ['qc']}},
    {'key': 'overdue_maint', 'name': '逾期保养与未修设备', 'description': '到期 / 逾期的点检保养计划和还没修好的维修工单，每天早上发给设备员。',
     'question': '哪些设备保养逾期了，还有哪些维修单没修好？',
     'plan': {'steps': [{'tool': 'query_maintenance', 'args': {}}]}, 'params': [], 'output': {'blocks': ['table']},
     'schedule': {'mode': 'daily', 'time': '07:30', 'enabled': False, 'params': {}}, 'targets': {'feed': True, 'daily': False, 'notify_users': [], 'notify_roles': ['maint']}},
    {'key': 'energy_anomaly', 'name': '能耗异常', 'description': '近 24 小时哪台设备、哪个小时用电明显高于过去 7 天同一小时的平时水平。',
     'question': '近 24 小时有没有能耗异常？',
     'plan': {'steps': [{'tool': 'query_energy', 'args': {'period': '{period}', 'ratio': '{ratio}'}}]},
     'params': [{'key': 'period', 'label': '时间段', 'type': 'period', 'default': '24h'}, {'key': 'ratio', 'label': '异常倍数', 'type': 'number', 'default': 1.3}],
     'output': {'blocks': ['chart', 'table']}, 'schedule': {'mode': 'daily', 'time': '08:30', 'enabled': False, 'params': {}},
     'targets': {'feed': True, 'daily': True, 'notify_users': [], 'notify_roles': []}},
]


def ensure_builtins():
    with db.engine.connect() as c:
        have = set(c.execute(sa.select(ai_skill_t.c.key).where(ai_skill_t.c.key.is_not(None))).scalars())
    admin = auth.user_by_name('admin')
    for b in BUILTIN:
        if b['key'] in have:
            continue
        try:
            create({**b, 'share_roles': ['*'], 'narrative': False, 'mcp_expose': True}, admin or {'username': 'admin', 'role_list': ['admin']}, key=b['key'])
        except Exception:
            log.exception('builtin skill %s', b['key'])


# ───────────────────────── 信息流 / 日报 ─────────────────────────
def feed_provider(user, now):
    since = datetime.now() - timedelta(hours=24)
    with db.engine.connect() as c:
        rows = c.execute(sa.select(ai_skill_run_t, ai_skill_t.c.name, ai_skill_t.c.owner, ai_skill_t.c.share_roles, ai_skill_t.c.plant)
                         .join(ai_skill_t, ai_skill_t.c.id == ai_skill_run_t.c.skill)
                         .where((ai_skill_run_t.c.trigger.in_(('schedule', 'automation'))) & (ai_skill_run_t.c.status == 'ok') & (ai_skill_run_t.c.started_at >= since))
                         .order_by(ai_skill_run_t.c.id.desc()).limit(30)).mappings().all()
    out, seen = [], set()
    for r in rows:
        if r['skill'] in seen or not (r['targets'] or {}).get('feed'):
            continue
        if not visible(user, {'owner': r['owner'], 'share_roles': r['share_roles'], 'plant': r['plant']}):
            continue
        seen.add(r['skill'])
        first = next((s.get('summary') for s in r['steps'] or [] if s.get('summary')), '') or ''
        out.append({'id': f"skill:{r['id']}", 'kind': 'ai', 'severity': 'info', 'category': 'ai', 'title': _tr('技能「{name}」', name=r['name']),
                    'body': (r['narrative'] or first)[:300], 'ts': r['data_time'] or r['started_at'], 'link': f"/ai/skills?run={r['id']}",
                    'evidence': [{'label': _tr('运行'), 'value': db.jsonable(r['started_at'])[5:16]}],
                    'actions': [{'label': _tr('查看结果'), 'link': f"/ai/skills?run={r['id']}"}], 'ask': _tr('解读技能「{name}」最新的结果：{v}', name=r['name'], v=first[:300])})
    return out[:6]


def daily_sections(day: str):
    """日报里的"技能"一节：这天定时运行、去向含日报的技能结果（每个技能取最后一次）。"""
    d0 = datetime.strptime(day, '%Y-%m-%d')
    with db.engine.connect() as c:
        rows = c.execute(sa.select(ai_skill_run_t, ai_skill_t.c.name).join(ai_skill_t, ai_skill_t.c.id == ai_skill_run_t.c.skill)
                         .where((ai_skill_run_t.c.status == 'ok') & (ai_skill_run_t.c.data_time >= d0) & (ai_skill_run_t.c.data_time < d0 + timedelta(days=1)))
                         .order_by(ai_skill_run_t.c.id.desc())).mappings().all()
    out, seen = [], set()
    for r in rows:
        if r['skill'] in seen or not (r['targets'] or {}).get('daily'):
            continue
        seen.add(r['skill'])
        out.append({'skill': r['skill'], 'name': r['name'], 'run': r['id'], 'narrative': r['narrative'] or '', 'by': r['by'],
                    'summary': '\n'.join(s.get('summary') or '' for s in r['steps'] or [])[:1500], 'blocks': r['blocks'] or [], 'at': db.jsonable(r['started_at'])})
    return out


def install():
    try:
        from .home_feed import register_feed_provider
        register_feed_provider(feed_provider, name='skills', kind='ai', feature='skills', order=20)
    except ImportError:
        pass


# ───────────────────────── 接口 ─────────────────────────
@router.get('/api/skills')
def api_list(scope: str = 'all', plant: int | None = None, user=Depends(perms.require(PERM_USE))):
    return {'items': list_skills(user, scope, plant), 'can_edit': perms.can(user, PERM_EDIT), 'can_manage': perms.can(user, PERM_MANAGE)}


@router.get('/api/skills/meta')
def api_meta(user=Depends(perms.require(PERM_USE))):
    from . import ai_tools
    return {'tools': [{'name': t.name, 'title': t.title, 'description': t.description, 'schema': t.schema()} for t in ai_tools.available(user, False)],
            'modes': MODES, 'ptypes': PTYPES, 'periods': PERIODS, 'roles': ROLES, 'blocks': list(BLOCK_TYPES)}


@router.get('/api/skills/llm_calls')
def api_llm_calls(user=Depends(perms.require(PERM_USE))):
    from .ai import LLM_CALLS
    return {'n': LLM_CALLS['n']}


@router.post('/api/skills/from_message')
def api_from_message(body: dict = Body(...), user=Depends(perms.require(PERM_EDIT))):
    return suggest(int(body.get('message_id') or 0), user)


@router.post('/api/skills/tick')
def api_tick(body: dict = Body(default={}), user=Depends(perms.require(PERM_MANAGE))):
    ids = [int(x) for x in body.get('force_ids') or []] or None
    runs = tick(ids)
    return {'runs': [{k: r.get(k) for k in ('id', 'skill', 'status', 'llm_calls', 'error', 'trigger')} for r in runs]}


@router.post('/api/skills')
def api_create(body: dict = Body(...), user=Depends(perms.require(PERM_EDIT))):
    return create(body, user)


@router.get('/api/skills/runs/{rid}')
def api_run_get(rid: int, user=Depends(perms.require(PERM_USE))):
    r = run_get(rid)
    sk = get(r['skill'])
    if not sk or not visible(user, sk):
        raise HTTPException(404, _tr('运行记录不存在'))
    r['skill_name'] = sk['name']
    return r


@router.get('/api/skills/{sid}')
def api_get(sid: int, user=Depends(perms.require(PERM_USE))):
    sk = get(sid)
    if not sk or not visible(user, sk):
        raise HTTPException(404, _tr('技能不存在'))
    sk['can_edit'] = can_edit(user, sk)
    with db.engine.connect() as c:
        sk['versions'] = [{k: db.jsonable(v) for k, v in dict(r).items() if k != 'snapshot'} for r in c.execute(
            sa.select(ai_skill_version_t).where(ai_skill_version_t.c.skill == sid).order_by(ai_skill_version_t.c.version.desc()).limit(20)).mappings()]
        sk['runs'] = [{k: db.jsonable(v) for k, v in dict(r).items() if k not in ('blocks', 'steps')} for r in c.execute(
            sa.select(ai_skill_run_t).where(ai_skill_run_t.c.skill == sid).order_by(ai_skill_run_t.c.id.desc()).limit(30)).mappings()]
    return sk


@router.put('/api/skills/{sid}')
def api_update(sid: int, body: dict = Body(...), user=Depends(perms.require(PERM_EDIT))):
    return update(sid, body, user)


@router.delete('/api/skills/{sid}')
def api_delete(sid: int, user=Depends(perms.require(PERM_EDIT))):
    return delete(sid, user)


@router.post('/api/skills/{sid}/run')
def api_run(sid: int, body: dict = Body(default={}), user=Depends(perms.require(PERM_USE))):
    nar = body.get('narrative')
    return run(sid, body.get('params') or {}, trigger='manual', user=user, narrative=None if nar is None else bool(nar))


@router.get('/api/skills/{sid}/runs')
def api_runs(sid: int, limit: int = 30, user=Depends(perms.require(PERM_USE))):
    sk = get(sid)
    if not sk or not visible(user, sk):
        raise HTTPException(404, _tr('技能不存在'))
    with db.engine.connect() as c:
        return [{k: (db.jsonable(v) if not isinstance(v, (dict, list)) else v) for k, v in dict(r).items()} for r in c.execute(
            sa.select(ai_skill_run_t).where(ai_skill_run_t.c.skill == sid).order_by(ai_skill_run_t.c.id.desc()).limit(max(1, min(200, limit)))).mappings()]
