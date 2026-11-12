"""审批流程：配置在"实施 → 审批流程"（权限 config.flow.manage），挂在 实体 + 动作 上；满足条件时先审批、批完再执行。说明见 docs/dev/config.md「审批流程」。

- 动作：create / update / delete / 实体动作名（work_order.release、ncr.scrap…）/ 服务登记的动作（register_action，如 M5 的 ECN 发布）。
- 条件（流程级：什么时候要审批；步骤级：这一步什么时候需要）：[{field, op, value}] 全部满足；op：= != > >= < <= in contains empty not_empty。
  字段取"改后的值"（新建 = 请求体；修改 = 原记录 + 改动；动作 = 原记录 + 动作参数），自定义字段 x_… 也能用。例：qty > 1000 要厂长审批。
- 步骤：[{name, type: role|user, approver, condition}]，按顺序一步一步批；任一步驳回 = 整单驳回；最后一步通过 = 以申请人的身份执行原来的操作
  （权限、数据范围、审计照常；执行失败记在流程单上并通知申请人）。
- 审批中心：每一步一张 approval（kind='flow'，assignee_role = 角色 或 'user:账号'），和智能体 / AI 草稿在同一个列表里处理；不依赖 AI 开关。
- 审计：submit / approve / reject / execute / cancel 各记一条（entity='approval_flow_inst'）。

服务里自己的动作（不走通用增删改查）怎么接：
    from honeymom.impl import flows
    flows.register_action('ecn', 'release', '发布 ECN', executor=lambda body, user, rid: _release(rid, body, user))
    res = flows.gate('ecn', 'release', record=row, body=body, user=user)      # None = 不需要审批，接着执行；dict = 已提交审批（直接返回给前端）
"""
from ..i18n import _tr
import contextvars
import json
from datetime import datetime

import sqlalchemy as sa
from fastapi import HTTPException

from .. import audit, auth, context, db, perms
from ..schema import BY_NAME, ROLES, TABLES, approval_t

OPS = {'=': '等于', '!=': '不等于', '>': '大于', '>=': '大于等于', '<': '小于', '<=': '小于等于', 'in': '属于', 'contains': '包含', 'empty': '为空', 'not_empty': '不为空'}
BASE_ACTIONS = {'create': '新建', 'update': '修改', 'delete': '删除'}
EXECUTORS: dict = {}          # (实体或键, 动作) -> (中文名, fn(body, user, record_id) -> dict)
_bypass: contextvars.ContextVar = contextvars.ContextVar('hm_flow_bypass', default=None)


def T(name):
    from ..schema_impl import T as _T
    return _T[name]


def register_action(entity: str, action: str, label: str, executor):
    """登记一个服务动作（不走 /api/e 的）：流程设计器里能选到；最后一步批准后调用 executor(body, user, record_id)。"""
    EXECUTORS[(entity, action)] = (label, executor)


def actions_of(entity: str) -> dict:
    """实体可挂流程的动作：增删改 + 实体动作 + 登记的服务动作。"""
    from ..crud import ACTIONS
    out = dict(BASE_ACTIONS)
    e = BY_NAME.get(entity)
    if e:
        out.update(e.actions)
        for (en, a) in ACTIONS:
            if en == entity and a not in out:
                out[a] = a
    for (en, a), (lab, _) in EXECUTORS.items():
        if en == entity:
            out[a] = lab
    return out


# ───────────────────────── 条件 ─────────────────────────
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cond_ok(conds, rec: dict) -> bool:
    for c in conds or []:
        f, op, val = c.get('field'), c.get('op') or '=', c.get('value')
        v = rec.get(f)
        if op == 'empty':
            if v not in (None, ''):
                return False
            continue
        if op == 'not_empty':
            if v in (None, ''):
                return False
            continue
        if op in ('>', '>=', '<', '<='):
            a, b = _num(v), _num(val)
            if a is None or b is None:
                return False
            if not {'>': a > b, '>=': a >= b, '<': a < b, '<=': a <= b}[op]:
                return False
            continue
        if op == 'in':
            opts = val if isinstance(val, list) else [x.strip() for x in str(val or '').replace('，', ',').split(',') if x.strip()]
            if str(v) not in [str(x) for x in opts]:
                return False
            continue
        if op == 'contains':
            if str(val or '') not in str(v or ''):
                return False
            continue
        a, b = _num(v), _num(val)
        eq = (a == b) if a is not None and b is not None else str(v if v is not None else '') == str(val if val is not None else '')
        if (op == '=' and not eq) or (op == '!=' and eq):
            return False
    return True


def cond_text(conds, entity=None) -> str:
    labs = {}
    if entity in BY_NAME:
        labs = {f.name: f.label for f in BY_NAME[entity].fields}
        from . import cf
        labs.update({d['key']: d['label'] for d in cf.fields(entity)})
    return _tr(' 且 ').join(f"{labs.get(c.get('field'), c.get('field'))} {OPS.get(c.get('op') or '=', c.get('op'))}"
                      + ('' if c.get('op') in ('empty', 'not_empty') else f" {c.get('value')}") for c in conds or []) or _tr('总是')


# ───────────────────────── 流程定义 ─────────────────────────
_cache = {'at': 0.0, 'rows': []}


def flows_for(entity, action) -> list[dict]:
    import time
    if time.time() - _cache['at'] > 3:
        try:
            with db.engine.connect() as c:
                _cache['rows'] = [dict(r) for r in c.execute(sa.select(T('approval_flow')).where(T('approval_flow').c.enabled.is_(True))
                                                             .order_by(T('approval_flow').c.id)).mappings()]
        except Exception:
            _cache['rows'] = []
        _cache['at'] = time.time()
    return [f for f in _cache['rows'] if f['entity'] == entity and f['action'] == action]


def invalidate():
    _cache['at'] = 0.0


def any_enabled() -> bool:
    flows_for('', '')
    return bool(_cache['rows'])


def validate_flow(body: dict) -> dict:
    ent, act = body.get('entity'), body.get('action')
    if ent not in BY_NAME and not any(k[0] == ent for k in EXECUTORS):
        raise ValueError(_tr('没有实体 {ent}', ent=ent))
    if act not in actions_of(ent):
        raise ValueError(_tr('{v}没有动作 {act}', v=BY_NAME[ent].label if ent in BY_NAME else ent, act=act))
    steps = body.get('steps') or []
    if not steps:
        raise ValueError(_tr('至少要有一个审批步骤'))
    clean = []
    for i, s in enumerate(steps, 1):
        typ = s.get('type') or 'role'
        who = str(s.get('approver') or '').strip()
        if typ not in ('role', 'user') or not who:
            raise ValueError(_tr('第 {i} 步要选审批角色或审批人', i=i))
        if typ == 'role' and who not in perms.roles():
            raise ValueError(_tr('第 {i} 步：没有角色 {who}', i=i, who=who))
        if typ == 'user':
            with db.engine.connect() as c:
                if not db.get(c, 'sys_user', username=who):
                    raise ValueError(_tr('第 {i} 步：没有账号 {who}', i=i, who=who))
        clean.append({'name': str(s.get('name') or _tr('第 {i} 步', i=i))[:60], 'type': typ, 'approver': who, 'condition': _conds(s.get('condition'))})
    name = str(body.get('name') or '').strip()
    if not name:
        raise ValueError(_tr('请填写流程名称'))
    return {'name': name[:200], 'entity': ent, 'action': act, 'enabled': bool(body.get('enabled', True)), 'condition': _conds(body.get('condition')),
            'steps': clean, 'remark': str(body.get('remark') or '')[:400]}


def _conds(cs):
    out = []
    for c in cs or []:
        if not c or not c.get('field'):
            continue
        op = c.get('op') or '='
        if op not in OPS:
            raise ValueError(_tr('不认识的条件 {op}', op=op))
        out.append({'field': str(c['field']), 'op': op, 'value': c.get('value')})
    return out


# ───────────────────────── 拦截（crud 守卫 / 服务调用） ─────────────────────────
def guard(entity, op, body, user, row):
    """crud.GUARDS：命中流程 → 建流程单、返回"已提交审批"；否则 None。提交前先做基本校验（必填、格式），免得批完才发现填错。"""
    if _bypass.get() is None and user is not None and flows_for(entity, op) and op in ('create', 'update'):
        e = BY_NAME[entity]
        if op == 'create':
            for f in e.fields:
                if f.req and (body or {}).get(f.name) in (None, '') and not f.auto:
                    raise HTTPException(400, _tr('请填写{label}', label=f.label))
        try:
            db.coerce(entity, {k: v for k, v in (body or {}).items() if not k.endswith('__label')})
        except ValueError as ex:
            raise HTTPException(400, str(ex))
    return gate(entity, op, record=row, body=body, user=user)


def gate(entity, action, record=None, body=None, user=None, title=None):
    if _bypass.get() is not None or user is None:
        return None
    fl = flows_for(entity, action)
    if not fl:
        return None
    rec = {**(record or {}), **(body or {})}
    if record and record.get('id'):
        from . import cf
        if cf.fields(entity):
            with db.engine.connect() as c:
                rec = {**cf.read_one(c, entity, record['id']), **rec}
    for f in fl:
        if not cond_ok(f['condition'], rec):
            continue
        steps = [dict(s, status='waiting') for s in f['steps'] if cond_ok(s.get('condition'), rec)]
        if not steps:
            continue
        return submit(f, entity, action, record, body or {}, user, steps, title)
    return None


def _rec_label(entity, record):
    if not record or entity not in BY_NAME:
        return ''
    from ..crud import label_of
    return label_of(record)


def submit(f, entity, action, record, body, user, steps, title=None) -> dict:
    now = db.clock.now()
    alab = actions_of(entity).get(action, action)
    elab = BY_NAME[entity].label if entity in BY_NAME else entity
    title = title or f"{alab}{elab}{(' ' + _rec_label(entity, record)) if record else ''}"
    with db.engine.begin() as c:
        iid = c.execute(T('approval_flow_inst').insert().values(
            flow=f['id'], flow_name=f['name'], entity=entity, action=action, record_id=(record or {}).get('id'), title=title[:300],
            body=_jsonable(body), requester=user['username'], status='pending', steps=steps, cur_step=0, created_at=now)).inserted_primary_key[0]
        aid = _open_step(c, iid, 0, steps, f['name'], title, entity, body, record, user)
        audit.audit(c, 'flow_submit', 'approval_flow_inst', iid, detail={'flow': f['name'], 'entity': entity, 'action': action, 'record': (record or {}).get('id'),
                                                                         'steps': [s['name'] for s in steps]}, user=user['username'])
    return {'ok': True, 'pending_approval': {'inst': iid, 'approval': aid, 'flow': f['name'], 'step': steps[0]['name'], 'steps': len(steps)},
            'msg': _tr('已提交审批：{name}（{name2}，{v}）', name=f['name'], name2=steps[0]['name'], v=_who_cn(steps[0]))}


def _who_cn(s):
    return ROLES.get(s['approver'], (perms.roles().get(s['approver']) or {}).get('name', s['approver'])) if s['type'] == 'role' else s['approver']


def _jsonable(d):
    return json.loads(json.dumps(d or {}, ensure_ascii=False, default=db.jsonable))


def _summary(entity, action, body, record, steps, i):
    labs = {f.name: f for f in BY_NAME[entity].fields} if entity in BY_NAME else {}
    lines = [_tr('**第 {v} / {len} 步**：{name}', v=i + 1, len=len(steps), name=steps[i]['name'])]
    shown = 0
    for k, v in (body or {}).items():
        if k.endswith('__label') or k in ('id', 'created_at', 'updated_at') or v in (None, ''):
            continue
        f = labs.get(k)
        if f and f.t == 'ref':
            v = (body.get(k + '__label') or v)
        elif f and f.enum:
            v = f.enum.get(v, v)
        old = (record or {}).get(k) if record else None
        lab = f.label if f else k
        lines.append(_tr('- {lab}：{v}', lab=lab, v=v) + (_tr('（原 {old}）', old=old) if record and old not in (None, '') and str(old) != str(v) and action == 'update' else ''))
        shown += 1
        if shown >= 12:
            break
    return '\n'.join(lines)


def _open_step(c, iid, i, steps, fname, title, entity, body, record, user) -> int:
    s = steps[i]
    role = s['approver'] if s['type'] == 'role' else 'user:' + s['approver']
    aid = c.execute(approval_t.insert().values(run=None, agent=None, node=f'flow:{iid}', kind='flow', title=f'{title}'[:290],
                                               summary=_summary(entity, None, body, record, steps, i), payload=_jsonable({k: v for k, v in (body or {}).items() if not k.endswith('__label')}),
                                               fields=[{'key': 'flow', 'label': fname, 'inst': iid, 'step': i, 'steps': len(steps), 'requester': user['username']}],
                                               result=None, assignee_role=role, status='pending', dry_run=False, created_at=db.clock.now())).inserted_primary_key[0]
    c.execute(T('approval_flow_inst').update().where(T('approval_flow_inst').c.id == iid).values(approval=aid, cur_step=i))
    db.insert(c, 'notice', {'title': _tr('待审批：{title}', title=title)[:190], 'content': _tr('{fname} · {name}（申请人 {v}）', fname=fname, name=s["name"], v=user.get("name") or user["username"]),
                            'to_role': role if s['type'] == 'role' else None, 'to_user': s['approver'] if s['type'] == 'user' else None,
                            'channel': 'inbox', 'level': 'warn', 'link': '/approvals', 'source': '审批流程', 'read': False, 'time': db.clock.now()}, raw=True)
    return aid


def can_decide(user, ap) -> bool:
    if ap['status'] != 'pending':
        return False
    who = ap['assignee_role'] or ''
    if who.startswith('user:'):
        return user['username'] == who[5:] or auth.is_admin(user)
    return auth.has_role(user, who)


def flow_out(c, ap, user) -> dict:
    meta = (ap.get('fields') or [{}])[0] if isinstance(ap.get('fields'), list) else {}
    inst = c.execute(sa.select(T('approval_flow_inst')).where(T('approval_flow_inst').c.id == meta.get('inst'))).mappings().first()
    return {'inst': meta.get('inst'), 'flow': meta.get('label'), 'step': (meta.get('step') or 0) + 1, 'steps': meta.get('steps'), 'requester': meta.get('requester'),
            'status': inst['status'] if inst else None, 'history': [{k: s.get(k) for k in ('name', 'status', 'decided_by', 'decided_at', 'reason')}
                                                                    for s in ((inst or {}).get('steps') or [])],
            'entity': inst['entity'] if inst else None, 'record_id': inst['record_id'] if inst else None, 'can_decide': can_decide(user, ap)}


def role_label(ap) -> str:
    who = ap['assignee_role'] or ''
    return _tr('{v}（指定人）', v=who[5:]) if who.startswith('user:') else ROLES.get(who, (perms.roles().get(who) or {}).get('name', who))


# ───────────────────────── 审批 ─────────────────────────
def decide(aid: int, decision: str, reason: str, user) -> dict:
    if decision not in ('approve', 'reject'):
        raise HTTPException(400, _tr('decision 只能是 approve 或 reject'))
    it = T('approval_flow_inst')
    with db.engine.begin() as c:
        ap = c.execute(sa.select(approval_t).where(approval_t.c.id == aid)).mappings().first()
        if not ap or ap['kind'] != 'flow':
            raise HTTPException(404, _tr('审批单不存在'))
        if ap['status'] != 'pending':
            raise HTTPException(400, _tr('这一步已经处理过了'))
        if not can_decide(user, ap):
            raise HTTPException(403, _tr('这一步要由{role_label}审批', role_label=role_label(ap)))
        if decision == 'reject' and not (reason or '').strip():
            raise HTTPException(400, _tr('驳回要写原因'))
        meta = ap['fields'][0]
        inst = dict(c.execute(sa.select(it).where(it.c.id == meta['inst'])).mappings().first())
        steps = inst['steps']
        i = inst['cur_step'] or 0
        now = db.clock.now()
        steps[i] = {**steps[i], 'status': 'approved' if decision == 'approve' else 'rejected', 'decided_by': user['username'], 'decided_at': db.jsonable(now),
                    'reason': reason or ''}
        c.execute(approval_t.update().where(approval_t.c.id == aid).values(status='approved' if decision == 'approve' else 'rejected',
                                                                           decided_by=user['username'], decided_at=now, reason=reason or ''))
        audit.audit(c, 'flow_' + decision, 'approval_flow_inst', inst['id'], detail={'flow': inst['flow_name'], 'step': steps[i]['name'], 'reason': reason or None},
                    user=user['username'])
        requester = auth.user_by_name(inst['requester']) or {'username': inst['requester'], 'role_list': [], 'name': inst['requester']}
        if decision == 'reject':
            c.execute(it.update().where(it.c.id == inst['id']).values(steps=steps, status='rejected', finished_at=now))
            _notify(c, inst, _tr('被驳回：{title}', title=inst['title']), _tr('{name}（{v}）：{reason}', name=steps[i]['name'], v=user.get('name') or user['username'], reason=reason), 'warn')
            return {'status': 'rejected', 'inst': inst['id']}
        if i + 1 < len(steps):
            c.execute(it.update().where(it.c.id == inst['id']).values(steps=steps))
            record = None
            if inst['record_id'] and inst['entity'] in BY_NAME:
                record = db.get(c, inst['entity'], inst['record_id'])
            _open_step(c, inst['id'], i + 1, steps, inst['flow_name'], inst['title'], inst['entity'], inst['body'], record, requester)
            return {'status': 'next', 'inst': inst['id'], 'step': steps[i + 1]['name']}
        c.execute(it.update().where(it.c.id == inst['id']).values(steps=steps))
    # 最后一步通过：以申请人身份执行（另一个事务；失败记在流程单上）
    res, err = None, None
    try:
        res = execute(inst, requester)
        status = 'approved'
    except HTTPException as ex:
        status, err = 'failed', str(ex.detail)
    except Exception as ex:
        status, err = 'failed', f'{type(ex).__name__}: {ex}'
    with db.engine.begin() as c:
        c.execute(it.update().where(it.c.id == inst['id']).values(status=status, result=_jsonable(res) if res else None, error=err, finished_at=db.clock.now()))
        audit.audit(c, 'flow_execute', 'approval_flow_inst', inst['id'], detail={'flow': inst['flow_name'], 'ok': status == 'approved', 'error': err,
                                                                                 'result': {k: v for k, v in (res or {}).items() if k in ('id', 'msg')} or None},
                    user=inst['requester'], source=context.current_source())
        _notify(c, inst, (_tr('已批准并执行：') if status == 'approved' else _tr('已批准但执行失败：')) + inst['title'],
                (res or {}).get('msg') or err or _tr('审批完成'), 'info' if status == 'approved' else 'urgent')
    return {'status': status, 'inst': inst['id'], 'result': res, 'error': err}


def _notify(c, inst, title, content, level):
    db.insert(c, 'notice', {'title': title[:190], 'content': content, 'to_user': inst['requester'], 'channel': 'inbox', 'level': level,
                            'link': f"/e/{inst['entity']}" + (f"?id={inst['record_id']}" if inst['record_id'] else '') if inst['entity'] in BY_NAME else '/approvals',
                            'source': '审批流程', 'read': False, 'time': db.clock.now()}, raw=True)


def execute(inst, user) -> dict:
    """以申请人的身份执行原来的操作（跳过流程拦截；权限、数据范围、审计照常）。"""
    from .. import crud
    ent, act, rid, body = inst['entity'], inst['action'], inst['record_id'], dict(inst['body'] or {})
    tok = _bypass.set(inst['id'])
    try:
        with context.acting(user, 'ui'):
            if (ent, act) in EXECUTORS:
                return EXECUTORS[(ent, act)][1](body, user, rid) or {}
            if act == 'create':
                return crud.create(ent, body, user=user)
            if act == 'update':
                return crud.update_(ent, rid, body, user=user)
            if act == 'delete':
                return crud.delete_(ent, rid, user=user)
            return crud.do_action(ent, rid, act, body, user=user)
    finally:
        _bypass.reset(tok)


def cancel(iid, user):
    it = T('approval_flow_inst')
    with db.engine.begin() as c:
        inst = c.execute(sa.select(it).where(it.c.id == iid)).mappings().first()
        if not inst:
            raise HTTPException(404, _tr('流程单不存在'))
        if inst['requester'] != user['username'] and not perms.can(user, 'config.flow.manage'):
            raise HTTPException(403, _tr('只能撤回自己提交的'))
        if inst['status'] != 'pending':
            raise HTTPException(400, _tr('已经处理完了，不能撤回'))
        c.execute(it.update().where(it.c.id == iid).values(status='cancelled', finished_at=db.clock.now()))
        c.execute(approval_t.update().where((approval_t.c.id == inst['approval']) & (approval_t.c.status == 'pending')).values(status='cancelled'))
        audit.audit(c, 'flow_cancel', 'approval_flow_inst', iid, user=user['username'])
    return {'ok': True}


def install():
    from .. import crud
    if guard not in crud.GUARDS:
        crud.GUARDS.append(guard)


_ = (datetime, TABLES)
