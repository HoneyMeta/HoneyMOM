"""AI 动作草稿：AI 提议的写操作（建维修工单 / 不合格品 / 安灯、调整排程、调整参数…）先变成一张"草稿"，
显示改动前后对比（diff），由有权限的人 通过 / 改一下再通过 / 驳回。没有通过的草稿什么都不会执行。

存储：复用审批中心的 approval 表（kind='ai_draft'，run/agent 为空），所以审批中心里也能看到并处理；
每一步（起草、通过、驳回、修改后通过）都写 audit_log（kind = ai_draft_create / ai_draft_approve / ai_draft_reject）。

来源（source）：assistant（右侧 AI 面板里模型调用 propose_*）、context（右键菜单"派维修"等）、automation（一句话自动化触发）、watch（AI 值守发现）。

接口（AI 功能"问数助手"开启时可用）：
  GET  /api/ai/drafts?status=pending|done|all       草稿列表（自己能看到的）
  GET  /api/ai/drafts/{id}                          一张草稿（含审计记录）
  POST /api/ai/drafts {op, payload, summary?, source?}                       起草（服务端校验 + 生成 diff）
  POST /api/ai/drafts/suggest {op, context: {entity, id, label, data}}       按右键选中的对象让 AI 填好草稿
  POST /api/ai/drafts/{id}/decide {decision: approve|reject, payload?, reason?}   通过（可带修改后的内容）/ 驳回
审批中心 POST /api/approvals/{id}/decide 遇到 kind='ai_draft' 也转到这里。
"""
from ..i18n import _tr
import json
from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import auth, db
from ..auth import current_user
from ..db import clock
from ..schema import BY_NAME, ROLES, TABLES, approval_t, audit_t
from . import ai_demo, ops

router = APIRouter()
SOURCES = {'assistant': 'AI 助手', 'context': '右键 · AI', 'automation': '一句话自动化', 'watch': 'AI 值守', 'daily': 'AI 日报'}


# ---------------- 新操作：调整参数、调整排程 ----------------
PARAMS = {   # 实体 → 允许 AI 提议修改的字段（主数据参数；不直接改真实设备）
    'tool': {'life_limit': ('寿命', '件', int)},
    'equipment': {'ideal_cycle_s': ('理想节拍', 's', float)},
    'maint_plan': {'cycle_days': ('保养周期', '天', int)},
    'process': {'std_cycle_s': ('标准节拍', 's', float)},
    'material': {'safety_stock': ('安全库存', '', float)},
}


def _rec_label(r, entity):
    if not r:
        return ''
    code = r.get('code') or r.get('sn') or f"#{r['id']}"
    name = r.get('name') or r.get('title') or ''
    if entity == 'tool' and r.get('equipment'):
        return f"{code} {name}".strip()
    return f"{code} {name}".strip()


def v_param(c, p):
    ent = p.get('entity')
    if ent not in PARAMS:
        raise ops.OpError(_tr('只能调整：') + _tr('、').join(_tr('{label}的{join}', label=BY_NAME[e].label, join='/'.join(v[0] for v in PARAMS[e].values())) for e in PARAMS))
    field = p.get('field')
    if field not in PARAMS[ent]:
        raise ops.OpError(_tr('{label}只能调整：', label=BY_NAME[ent].label) + _tr('、').join(v[0] for v in PARAMS[ent].values()))
    t = TABLES[ent]
    rec = None
    key = p.get('record') or p.get('id') or p.get('code')
    if ent == 'tool' and p.get('equipment') and key:
        eq = ops._find(c, 'equipment', p['equipment'])
        if eq:
            rec = c.execute(sa.select(t).where((t.c.equipment == eq['id']) & (t.c.code == str(key)))).mappings().first()
            rec = dict(rec) if rec else None
    if not rec:
        rec = ops._find(c, ent, key)
    if not rec:
        raise ops.OpError(_tr('找不到{label}：{key}', label=BY_NAME[ent].label, key=key))
    lab, unit, typ = PARAMS[ent][field]
    try:
        val = typ(float(p.get('value')))
    except (TypeError, ValueError):
        raise ops.OpError(_tr('{lab}的新值不对', lab=lab))
    if val <= 0:
        raise ops.OpError(_tr('{lab}必须大于 0', lab=lab))
    old = rec.get(field)
    if old not in (None, 0) and (val > old * 3 or val < old / 3):
        raise ops.OpError(_tr('{lab}从 {old:g} 改到 {val:g} 变化太大（超过 3 倍），请人工确认后在台账里改', lab=lab, old=old, val=val))
    return {'entity': ent, 'record': rec['id'], 'record_label': _rec_label(rec, ent), 'field': field, 'value': val, 'reason': ops._txt(p.get('reason'), 300)}


def x_param(c, p, who, source):
    rec = db.get(c, p['entity'], int(p['record']))
    db.update(c, p['entity'], rec['id'], {p['field']: p['value']}, raw=True)
    lab, unit, _ = PARAMS[p['entity']][p['field']]
    return {'entity': p['entity'], 'id': rec['id'], 'code': rec.get('code') or '', 'link': ops._link(p['entity'], rec['id']),
            'text': _tr('已把 {record_label} 的{lab}从 {v} 改为 {v2}{unit}', record_label=p['record_label'], lab=lab, v=_fmt(rec.get(p['field'])), v2=_fmt(p['value']), unit=unit)}


def s_param(c, p):
    rec = db.get(c, p.get('entity'), int(p.get('record') or 0)) if p.get('entity') in PARAMS and p.get('record') else None
    return f"{rec['id']}:{rec.get(p.get('field'))}" if rec else ''


def v_sched_change(c, p):
    changes = p.get('changes') or []
    if isinstance(changes, str):
        try:
            changes = json.loads(changes)
        except ValueError:
            changes = []
    if isinstance(changes, dict):
        changes = [changes]
    if not changes and p.get('work_order'):
        changes = [{'work_order': p.get('work_order'), 'priority': p.get('priority'), 'due': p.get('due')}]
    clean = []
    for ch in changes if isinstance(changes, list) else []:
        if not isinstance(ch, dict):
            continue
        w = ops._find(c, 'work_order', ch.get('work_order'))
        if not w:
            raise ops.OpError(_tr('找不到工单：{get}', get=ch.get('work_order')))
        if w['status'] not in ('released', 'in_progress', 'draft'):
            raise ops.OpError(_tr('工单 {code} 已{status}，不能再调整', code=w['code'], status=w['status']))
        item = {'work_order': w['code']}
        if ch.get('priority') not in (None, ''):
            item['priority'] = max(1, min(9, int(float(ch['priority']))))
        if ch.get('due'):
            try:
                item['due'] = datetime.fromisoformat(str(ch['due']).replace('T', ' ')[:19]).strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                raise ops.OpError(_tr('交期格式不对：{due}', due=ch['due']))
        if len(item) > 1:
            clean.append(item)
    if not clean:
        raise ops.OpError(_tr('请给出要调整的工单和新的优先级或交期'))
    return {'changes': clean, 'reason': ops._txt(p.get('reason'), 400)}


def x_sched_change(c, p, who, source):
    n = 0
    for ch in p['changes']:
        w = ops._find(c, 'work_order', ch['work_order'])
        upd = {}
        if 'priority' in ch:
            upd['priority'] = ch['priority']
        if 'due' in ch:
            upd['due'] = datetime.strptime(ch['due'], '%Y-%m-%d %H:%M:%S')
        db.update(c, 'work_order', w['id'], upd, raw=True)
        n += 1
    from .production import schedule
    schedule(c)
    codes = _tr('、').join(ch['work_order'] for ch in p['changes'])
    first = ops._find(c, 'work_order', p['changes'][0]['work_order'])
    return {'entity': 'work_order', 'id': first['id'], 'code': first['code'], 'link': '/production/gantt', 'text': _tr('已调整 {n} 个工单（{codes}）并重新排产', n=n, codes=codes)}


def s_sched_change(c, p):
    parts = []
    for ch in p.get('changes') or []:
        w = ops._find(c, 'work_order', ch.get('work_order'))
        if w:
            parts.append(f"{w['id']}:{w['priority']}:{db.jsonable(w['due'])}:{w['status']}")
    return '|'.join(parts)


ops.OPS.setdefault('param_adjust', dict(label='调整参数', entity='equipment', risk='high', v=v_param, x=x_param, s=s_param, fields=[
    ('entity', '对象类型 tool/equipment/maint_plan/process/material', 'str', True), ('record', '对象编号（刀位/设备编号/计划编码…）', 'str', True),
    ('equipment', '刀具所在机床（调刀具时填）', 'str', False), ('field', '字段 life_limit/ideal_cycle_s/cycle_days/std_cycle_s/safety_stock', 'str', True),
    ('value', '新值', 'float', True), ('reason', '原因', 'str', False)]))
ops.OPS.setdefault('schedule_change', dict(label='调整排程', entity='work_order', risk='high', v=v_sched_change, x=x_sched_change, s=s_sched_change, fields=[
    ('changes', '调整项 [{work_order, priority(1 最高), due(YYYY-MM-DD HH:MM)}]', 'json', True), ('reason', '原因', 'str', False)]))


# ---------------- diff ----------------
def _fmt(v):
    if v is None or v == '':
        return '—'
    if isinstance(v, bool):
        return _tr('是') if v else _tr('否')
    if isinstance(v, float):
        return f'{v:g}'
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M')
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)[:300]
    return str(v)


ENUM_CN = {'level': {'urgent': '紧急', 'normal': '一般'}, 'atype': {'equipment': '设备', 'quality': '质量', 'material': '物料', 'safety': '安全', 'other': '其他'},
           'channel': {'inbox': '站内信', 'board': '看板', 'webhook': 'Webhook'}, 'to_role': ROLES}
TARGET = {'repair_order': 'repair_order', 'ncr': 'ncr', 'andon_raise': 'andon', 'andon_close': 'andon', 'material_request': 'material_request',
          'schedule_proposal': 'schedule_proposal', 'notify': 'notice', 'report': 'ai_report'}


def _ref_text(c, entity, key):
    r = ops._find(c, entity, key) if key else None
    return _rec_label(r, entity) if r else _fmt(key)


DIFFS: dict = {}       # 其它模块的操作自带 diff：op → fn(c, payload) -> (mode, target, changes)（如 services/exec_ai.py）


def diff(c, op, p):
    """草稿 → (mode, target, changes)。changes: [{key, label, before, after}]，before 为 None 表示新建。"""
    if ops.OPS[op].get('diff'):                       # 5.0 M2：操作自带 diff（onboard_points / onboard_plc）
        return ops.OPS[op]['diff'](c, p)
    if op in DIFFS:
        return DIFFS[op](c, p)
    names = {k: lab.split(' ')[0] for k, lab, *_ in ops.OPS[op]['fields']}
    if op == 'param_adjust':
        rec = db.get(c, p['entity'], int(p['record']))
        lab, unit, _ = PARAMS[p['entity']][p['field']]
        return 'update', {'entity': p['entity'], 'id': rec['id'], 'label': p['record_label'], 'kind': BY_NAME[p['entity']].label}, [
            {'key': p['field'], 'label': lab + (_tr('（{unit}）', unit=unit) if unit else ''), 'before': _fmt(rec.get(p['field'])), 'after': _fmt(p['value'])}]
    if op == 'schedule_change':
        ch = []
        for it in p['changes']:
            w = ops._find(c, 'work_order', it['work_order'])
            if 'priority' in it:
                ch.append({'key': f"{w['code']}.priority", 'label': _tr('{code} 优先级', code=w['code']), 'before': _fmt(w['priority']), 'after': _fmt(it['priority'])})
            if 'due' in it:
                ch.append({'key': f"{w['code']}.due", 'label': _tr('{code} 交期', code=w['code']), 'before': _fmt(w['due']), 'after': it['due'][:16]})
        first = ops._find(c, 'work_order', p['changes'][0]['work_order'])
        return 'update', {'entity': 'work_order', 'id': first['id'], 'label': _tr('、').join(x['work_order'] for x in p['changes']), 'kind': '生产工单'}, ch
    if op == 'andon_close':
        a = ops._find(c, 'andon', p['andon'])
        return 'update', {'entity': 'andon', 'id': a['id'], 'label': f"{a['code']} {a['description']}", 'kind': '安灯'}, [
            {'key': 'status', 'label': _tr('状态'), 'before': {'open': _tr('待响应'), 'responding': _tr('处理中')}.get(a['status'], a['status']), 'after': _tr('已关闭')},
            {'key': 'close_note', 'label': _tr('处理结果'), 'before': _fmt(a['close_note']), 'after': p['close_note']}]
    if op == 'ncr' and p.get('ncr'):
        n = ops._find(c, 'ncr', p['ncr'])
        ch = [{'key': 'status', 'label': _tr('状态'), 'before': {'open': _tr('待处理'), 'reviewing': _tr('评审中'), 'closed': _tr('已关闭')}.get(n['status'], n['status']),
               'after': _tr('评审中') if n['status'] == 'open' else {'reviewing': _tr('评审中'), 'closed': _tr('已关闭')}.get(n['status'], n['status'])}]
        if p.get('description') and not n['root_cause']:
            ch.append({'key': 'root_cause', 'label': _tr('原因分析'), 'before': '—', 'after': p['description']})
        if p.get('with_8d'):
            ch.append({'key': 'report_8d', 'label': _tr('8D 报告'), 'before': None, 'after': _tr('新建 8D 草稿')})
        return 'update', {'entity': 'ncr', 'id': n['id'], 'label': n['code'], 'kind': '不合格品'}, ch
    ent = TARGET.get(op, ops.OPS[op]['entity'])
    ch = []
    for k, v in p.items():
        if v in (None, '', [], False) or k in ('webhook_url', 'webhook_kind'):
            continue
        if k in ('equipment', 'work_order', 'material', 'warehouse', 'defect'):
            shown = _ref_text(c, {'defect': 'defect_code'}.get(k, k), v)
        elif k in ENUM_CN:
            shown = ENUM_CN[k].get(v, v)
        elif k == 'changes' and isinstance(v, list):
            for it in v:
                w = ops._find(c, 'work_order', it.get('work_order'))
                if w and it.get('priority'):
                    ch.append({'key': f"{w['code']}.priority", 'label': _tr('{code} 优先级（采纳后）', code=w['code']), 'before': _fmt(w['priority']), 'after': _fmt(it['priority'])})
            continue
        else:
            shown = _fmt(v)
        ch.append({'key': k, 'label': names.get(k, k), 'before': None, 'after': shown})
    if op == 'ncr' and p.get('with_8d'):
        ch.append({'key': 'report_8d', 'label': _tr('8D 报告'), 'before': None, 'after': _tr('同时新建 8D 草稿')})
    return 'create', {'entity': ent, 'id': None, 'label': _tr('新建') + (BY_NAME[ent].label if ent in BY_NAME else ''), 'kind': BY_NAME[ent].label if ent in BY_NAME else ent}, ch


def _target_entity(op, p):
    return p.get('entity') if op == 'param_adjust' else ops.OPS[op]['entity']


def _assignee(user, op, p):
    ent = _target_entity(op, p)
    for r in user.get('role_list') or []:
        if auth.can_write({**user, 'role_list': [r]}, ent):
            return r
    return 'admin'


# ---------------- 起草 / 读取 ----------------
def _title(op, p, target):
    lab = ops.OPS[op]['label']
    if op == 'repair_order':
        return _tr('{lab}：{get} {get2}', lab=lab, get=p.get('equipment'), get2=p.get('fault', ''))[:120]
    if op == 'andon_raise':
        return _tr('{lab}：{get}', lab=lab, get=p.get('description', ''))[:120]
    if op == 'param_adjust':
        return _tr('{lab}：{record_label} {v} → {v2}', lab=lab, record_label=p['record_label'], v=PARAMS[p['entity']][p['field']][0], v2=_fmt(p['value']))
    if op == 'schedule_change':
        return _tr('{lab}：{join}', lab=lab, join=_tr('、').join(x['work_order'] for x in p['changes']))
    if op == 'notify':
        return _tr('{lab}：{get}', lab=lab, get=p.get('title', ''))[:120]
    return _tr('{lab}：{get}', lab=lab, get=target.get('label', ''))[:120]


def create(user, op, payload, *, source='assistant', summary='', thread=None, context=None, conn=None):
    """起草一张动作草稿（不执行）。返回 draft_out。校验不通过抛 ops.OpError。"""
    if op not in ops.OPS:
        raise ops.OpError(_tr('没有这个操作：{op}', op=op))

    def _do(c):
        p = ops.validate(c, op, payload)
        if not auth.can_write(user, _target_entity(op, p)) and source in ('assistant', 'context'):
            raise ops.OpError(_tr('当前角色没有“{label}”的权限', label=ops.OPS[op]['label']))
        mode, target, changes = diff(c, op, p)
        st = ops.state_of(c, op, p)
        now = clock.now()
        fields = {'action': op, 'source': source, 'mode': mode, 'target': target, 'changes': changes, 'state': st, 'thread': thread,
                  'requested_by': user['username'], 'context': (context or [])[:6]}
        did = c.execute(approval_t.insert().values(run=None, agent=None, node=None, kind='ai_draft', title=_title(op, p, target), summary=summary or '',
                                                   payload=p, fields=fields, assignee_role=_assignee(user, op, p), status='pending', dry_run=False,
                                                   created_at=now)).inserted_primary_key[0]
        ops.audit(c, user['username'], 'ai_draft_create', f'ai_draft:{did}', {'op': op, 'payload': p, 'source': source, 'thread': thread})
        row = c.execute(sa.select(approval_t).where(approval_t.c.id == did)).mappings().first()
        return draft_out(c, dict(row), user)
    if conn is not None:
        return _do(conn)
    with db.engine.begin() as c:
        return _do(c)


def draft_out(c, a, user=None):
    f = a.get('fields') or {}
    op = f.get('action')
    meta = ops.OPS.get(op, {})
    can = bool(user) and a['status'] == 'pending' and auth.has_role(user, a['assignee_role']) and auth.can_write(user, _target_entity(op, a.get('payload') or {}) or 'notice')
    return {'id': a['id'], 'op': op, 'label': meta.get('label', op), 'risk': meta.get('risk', 'high'), 'title': a['title'], 'summary': a['summary'] or '',
            'status': a['status'], 'source': f.get('source'), 'source_label': SOURCES.get(f.get('source'), f.get('source') or ''), 'mode': f.get('mode'),
            'target': f.get('target') or {}, 'changes': f.get('changes') or [], 'payload': a.get('payload') or {},
            'fields': [{'key': k, 'label': lab.split(' ')[0], 'type': t, 'required': r} for k, lab, t, r in meta.get('fields', [])],
            'requested_by': f.get('requested_by'), 'created_at': db.jsonable(a['created_at']), 'decided_by': a.get('decided_by'),
            'decided_at': db.jsonable(a.get('decided_at')), 'reason': a.get('reason') or '', 'receipt': (a.get('result') or {}).get('receipt'),
            'edited': bool((a.get('result') or {}).get('edited')), 'role_label': ROLES.get(a['assignee_role'], a['assignee_role']), 'can_decide': can, 'kind': 'ai_draft'}


def _get(c, did):
    a = c.execute(sa.select(approval_t).where((approval_t.c.id == did) & (approval_t.c.kind == 'ai_draft'))).mappings().first()
    if not a:
        raise HTTPException(404, _tr('草稿不存在'))
    return dict(a)


# ---------------- 审批 ----------------
def decide(did, decision, payload, reason, user):
    if decision not in ('approve', 'reject'):
        raise HTTPException(400, _tr('decision 只能是 approve 或 reject'))
    with db.engine.begin() as c:
        a = _get(c, did)
        if a['status'] != 'pending':
            raise HTTPException(400, _tr('这张草稿已经处理过了'))
        f = a['fields'] or {}
        op = f['action']
        if not auth.has_role(user, a['assignee_role']):
            raise HTTPException(403, _tr('这张草稿要由{get}处理', get=ROLES.get(a['assignee_role'], a['assignee_role'])))
        who = user.get('name') or user['username']
        now = clock.now()
        if decision == 'reject':
            c.execute(approval_t.update().where(approval_t.c.id == did).values(status='rejected', decided_by=who, decided_at=now, reason=reason or '',
                                                                               result={'receipt': None}))
            ops.audit(c, user['username'], 'ai_draft_reject', f'ai_draft:{did}', {'op': op, 'reason': reason})
            return draft_out(c, _get(c, did), user)
        edited = payload is not None and payload != a['payload']
        try:
            p = ops.validate(c, op, payload if payload is not None else a['payload'])
        except ops.OpError as ex:
            raise HTTPException(400, _tr('内容不对：{ex}', ex=ex))
        if not auth.can_write(user, _target_entity(op, p)):
            raise HTTPException(403, _tr('当前角色没有“{label}”的权限', label=ops.OPS[op]['label']))
        if not edited and f.get('state') and ops.state_of(c, op, p) != f['state']:
            raise HTTPException(409, _tr('相关数据在起草之后被改过了，请重新起草（或改一下内容再通过）'))
        before_mode, target, changes = diff(c, op, p)
        from .. import context
        with context.source('ai' if f.get('source') != 'automation' else 'agent'):
            rec = ops.execute(c, op, p, who, 'assistant' if f.get('source') != 'automation' else 'agent')
        res = {'receipt': rec, 'edited': edited, 'payload': p, 'changes': changes}
        c.execute(approval_t.update().where(approval_t.c.id == did).values(status='approved', decided_by=who, decided_at=now, reason=reason or '', result=res,
                                                                           payload=p, fields={**f, 'changes': changes, 'target': target}))
        ops.audit(c, user['username'], 'ai_draft_approve', f'ai_draft:{did}', {'op': op, 'payload': p, 'edited': edited, 'original': a['payload'] if edited else None,
                                                                               'receipt': rec})
        out_ = draft_out(c, _get(c, did), user)
    if f.get('thread'):
        try:
            from .assistant import _save_msg
            _save_msg(int(f['thread']), 'receipt', rec['text'], receipts=[rec] + (rec.get('extra') or []))
        except Exception:
            pass
    return out_


# ---------------- 按右键对象起草 ----------------
def suggest_payload(c, op, ctx, hint=''):
    """右键"派维修""发起安灯""调整排程"…：从选中的对象和最近的数据填好草稿（规则），有大模型时让它润色文字。"""
    ent, rid = ctx.get('entity'), ctx.get('id')
    data = ctx.get('data') or {}
    rec = db.get(c, ent, int(rid)) if ent in TABLES and str(rid or '').isdigit() else None
    if op in ('repair_order', 'andon_raise'):
        eq = rec if ent == 'equipment' else (db.get(c, 'equipment', rec['equipment']) if rec and rec.get('equipment') else None)
        if not eq and data.get('设备'):
            eq = ops._find(c, 'equipment', str(data['设备']).split()[0])
        if not eq:
            raise ops.OpError(_tr('没有找到对应的设备'))
        al = TABLES['alarm']
        recent = [dict(r) for r in c.execute(sa.select(al.c.msg, al.c.start, al.c.end).where(al.c.equipment == eq['id']).order_by(al.c.start.desc()).limit(12)).mappings()]
        msg = (rec.get('msg') if ent == 'alarm' and rec else None) or (recent[0]['msg'] if recent else '') or hint
        down = eq['status'] == 'down' or any(r['end'] is None for r in recent)
        if op == 'repair_order':
            fault = msg or _tr('{name}运行异常', name=eq['name'])
            seen = {}
            for r in recent:
                seen.setdefault(r['msg'], []).append(db.jsonable(r['start'])[5:16])
            cause = (_tr('最近报警：') + _tr('；').join(_tr('{m}（{len} 次，最近 {v}）', m=m, len=len(ts), v=ts[0]) for m, ts in list(seen.items())[:3])) if recent else ''
            return {'equipment': eq['code'], 'fault': fault[:180], 'level': 'urgent' if down else 'normal', 'cause': cause}, {'subject': eq['code'], 'msg': msg}
        return {'atype': 'equipment', 'equipment': eq['code'], 'description': (f"{eq['code']} {msg}" if msg else _tr('{code} 需要支援', code=eq['code']))[:180]}, {'subject': eq['code'], 'msg': msg}
    if op == 'schedule_change':
        w = rec if ent == 'work_order' else None
        if not w:
            raise ops.OpError(_tr('请在工单上使用'))
        return {'changes': [{'work_order': w['code'], 'priority': max(1, (w['priority'] or 5) - 2)}],
                'reason': hint or (_tr('交期临近，提高优先级') if w['due'] else _tr('提高优先级'))}, {'subject': w['code']}
    if op == 'param_adjust':
        if ent == 'tool' and rec:
            wear = rec.get('wear_pct') or 0
            new = max(1, int((rec['life_limit'] or 100) * (0.85 if wear >= 80 else 0.9)))
            eq = db.get(c, 'equipment', rec['equipment']) if rec.get('equipment') else None
            return {'entity': 'tool', 'record': rec['code'], 'equipment': eq['code'] if eq else '', 'field': 'life_limit', 'value': new,
                    'reason': hint or (_tr('AI 估计磨损 {wear:.0f}%，寿命下调约 15%', wear=wear) if wear >= 80 else _tr('刀具寿命偏长，下调 10% 以免崩刃'))}, {'subject': rec['code']}
        if ent == 'equipment' and rec:
            return {'entity': 'equipment', 'record': rec['code'], 'field': 'ideal_cycle_s', 'value': round((rec['ideal_cycle_s'] or 60) * 1.05, 1),
                    'reason': hint or _tr('负载偏高，节拍放慢 5%')}, {'subject': rec['code']}
        raise ops.OpError(_tr('请在刀具或设备上使用'))
    if op == 'ncr':
        return {'sn': data.get('sn') or (rec or {}).get('sn') or '', 'description': hint or ctx.get('label') or '', 'with_8d': False}, {'subject': ctx.get('label')}
    raise ops.OpError(_tr('不支持从右键起草：{op}', op=op))


def _polish(op, payload, facts):
    """有大模型时润色描述文字（演示模式回放录制的润色结果）。"""
    from .ai import chat_once, llm_available

    def live():
        if not llm_available():
            return {}
        txt = chat_once(_tr('你是 MES 里的 AI 助手。根据下面的草稿和事实，只改写文字字段（fault/cause/description/reason，没有就不写），中文、简洁、专业，不要编造数字。输出 JSON 对象，只包含需要改的字段。\n\n草稿：') + json.dumps(payload, ensure_ascii=False)
                        + _tr('\n事实：') + json.dumps(facts, ensure_ascii=False, default=str))
        try:
            j = json.loads(txt[txt.find('{'):txt.rfind('}') + 1])
        except ValueError:
            return {}
        return {k: v for k, v in j.items() if k in ('fault', 'cause', 'description', 'reason') and isinstance(v, str) and v.strip()}
    try:
        got = ai_demo.run('ask', 'draft_suggest', {'op': op, 'subject': str(facts.get('subject') or '')}, live=live, fallback=lambda: {},
                          recordable=lambda r: bool(r), mark=lambda r: r, exact=True)
    except Exception:
        got = {}
    return {**payload, **{k: v for k, v in (got or {}).items() if k in payload or k in ('cause', 'reason')}}


# ---------------- 接口 ----------------
@router.get('/api/ai/drafts')
def list_drafts(status: str = 'pending', limit: int = 50, user=Depends(current_user)):
    with db.engine.connect() as c:
        q = sa.select(approval_t).where(approval_t.c.kind == 'ai_draft')
        if status == 'pending':
            q = q.where(approval_t.c.status == 'pending')
        elif status == 'done':
            q = q.where(approval_t.c.status != 'pending')
        rows = [dict(r) for r in c.execute(q.order_by(approval_t.c.id.desc()).limit(min(200, limit))).mappings()]
        roles = set(user['role_list'])
        vis = [r for r in rows if roles & {'admin', 'manager'} or r['assignee_role'] in roles or (r['fields'] or {}).get('requested_by') == user['username']]
        return [draft_out(c, r, user) for r in vis]


@router.get('/api/ai/drafts/{did}')
def get_draft(did: int, user=Depends(current_user)):
    with db.engine.connect() as c:
        d = draft_out(c, _get(c, did), user)
        d['audit'] = [{'time': db.jsonable(r['time']), 'user': r['user'], 'kind': r['kind'], 'detail': r['detail']}
                      for r in c.execute(sa.select(audit_t).where(audit_t.c.target == f'ai_draft:{did}').order_by(audit_t.c.id)).mappings()]
    return d


@router.post('/api/ai/drafts')
def new_draft(body: dict = Body(...), user=Depends(current_user)):
    try:
        return create(user, body.get('op'), body.get('payload') or {}, source=body.get('source') if body.get('source') in SOURCES else 'assistant',
                      summary=(body.get('summary') or '')[:2000], context=body.get('context'))
    except ops.OpError as ex:
        raise HTTPException(400, str(ex))


@router.post('/api/ai/drafts/suggest')
def suggest(body: dict = Body(...), user=Depends(current_user)):
    op, ctx = body.get('op'), body.get('context') or {}
    if op not in ops.OPS:
        raise HTTPException(400, _tr('没有这个操作'))
    try:
        with db.engine.connect() as c:
            payload, facts = suggest_payload(c, op, ctx, (body.get('hint') or '')[:300])
        payload = _polish(op, payload, facts)
        summary = _tr('根据你右键选中的「{v}」起草', v=ctx.get('label') or '') + (_tr('；依据：{msg}', msg=facts['msg']) if facts.get('msg') else '') + _tr('。通过之前不会执行。')
        return create(user, op, payload, source='context', summary=summary, context=[ctx])
    except ops.OpError as ex:
        raise HTTPException(400, str(ex))


@router.post('/api/ai/drafts/{did}/decide')
def decide_api(did: int, body: dict = Body(...), user=Depends(current_user)):
    return decide(did, body.get('decision'), body.get('payload'), (body.get('reason') or '')[:500], user)
