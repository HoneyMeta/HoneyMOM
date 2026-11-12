"""智能体接口：列表/模板/编辑/发布/版本、运行与调试、运行记录、审批中心、Webhook、模拟事件。"""
from ..i18n import _tr
import copy
import hashlib
import hmac
import json
from datetime import timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException, Request

from .. import auth, db
from ..auth import current_user
from ..crud import attach_labels, out
from ..db import clock
from ..schema import TABLES, agent_event_t, agent_run_t, agent_step_t, agent_t, agent_version_t, approval_t
from ..services import ops
from . import engine, nodes, runtime, templates
from .queries import QUERY_KINDS
from .runtime import RT
from ..schema import ROLES

router = APIRouter()


def _need_editor(user):
    if not auth.can_edit_agents(user):
        raise HTTPException(403, _tr('只有管理员、厂长和工艺工程师可以编辑智能体'))


def _agent(c, aid):
    a = c.execute(sa.select(agent_t).where(agent_t.c.id == aid)).mappings().first()
    if not a:
        raise HTTPException(404, _tr('智能体不存在'))
    return dict(a)


def _version(c, vid):
    if not vid:
        return None
    v = c.execute(sa.select(agent_version_t).where(agent_version_t.c.id == vid)).mappings().first()
    return dict(v) if v else None


def _j(rows):
    return [{k: db.jsonable(v) for k, v in r.items()} for r in rows]


def trigger_text(dsl):
    bp = runtime._bp(dsl)
    tr = bp.get('trigger') or 'manual'
    if tr == 'event':
        lab = nodes.EVENT_KINDS.get(bp.get('event'), ('未选事件',))[0]
        if bp.get('event') == 'equipment_state' and bp.get('to_state'):
            lab += '→' + {o['value']: o['label'] for o in nodes.STATE_OPTS}.get(bp['to_state'], bp['to_state'])
        return _tr('事件 · ') + lab
    if tr == 'schedule':
        if (bp.get('schedule_mode') or 'cron') == 'shift':
            return _tr('定时 · 每班') + (_tr('结束') if (bp.get('shift_at') or 'end') == 'end' else _tr('开始'))
        return _tr('定时 · ') + (bp.get('cron') or '')
    return {'chat': _tr('聊天'), 'manual': _tr('手动 / 表单'), 'webhook': 'Webhook'}.get(tr, tr)


def hook_key(aid):
    return hmac.new(auth.secret(), f'agent-hook-{aid}'.encode(), hashlib.sha256).hexdigest()[:20]


def _canon(d):
    return json.dumps((d or {}).get('components') or {}, sort_keys=True, ensure_ascii=False)


def agent_card(c, a):
    v = _version(c, a['published_version'])
    dsl = v['dsl'] if v else a['draft']
    pend = c.execute(sa.select(sa.func.count()).select_from(approval_t).where((approval_t.c.agent == a['id']) & (approval_t.c.status == 'pending'))).scalar()
    runs = c.execute(sa.select(sa.func.count()).select_from(agent_run_t).where((agent_run_t.c.agent == a['id']) & (agent_run_t.c.trigger != 'node_test'))).scalar()
    return {'id': a['id'], 'name': a['name'], 'description': a['description'], 'icon': a['icon'] or 'MagicStick', 'template': a['template'], 'enabled': bool(a['enabled']),
            'chat_callable': bool(a['chat_callable']), 'trigger': trigger_text(dsl), 'trigger_kind': runtime._bp(dsl).get('trigger') or 'manual',
            'published': v['version'] if v else None, 'draft_changed': bool(v) and _canon(v['dsl']) != _canon(a['draft']),
            'last_run_at': db.jsonable(a['last_run_at']), 'last_status': a['last_status'], 'pending': pend, 'runs': runs, 'permissions': a['permissions'],
            'updated_at': db.jsonable(a['updated_at'])}


# ---------------- 列表 / 模板 / 元数据 ----------------
@router.get('/api/agents')
def list_agents(user=Depends(current_user)):
    with db.engine.connect() as c:
        rows = [dict(r) for r in c.execute(sa.select(agent_t).order_by(agent_t.c.id.desc())).mappings()]
        return {'items': [agent_card(c, a) for a in rows], 'can_edit': auth.can_edit_agents(user), 'runtime': RT.stats}


@router.get('/api/agents/meta')
def meta(user=Depends(current_user)):
    return {**nodes.registry_json(), 'templates': templates.templates(), 'query_kinds': {k: v[0] for k, v in QUERY_KINDS.items()}}


def _create(c, user, name, dsl, description='', icon='MagicStick', template=None, permissions=None, chat_callable=False):
    n = engine.now()
    extra = {}
    if db.engine.dialect.name == 'sqlite':
        # SQLite 会复用删掉的最大 ID：显式取一个没用过的，免得新智能体接上旧的运行记录
        used = [c.execute(sa.select(sa.func.max(col))).scalar() or 0 for col in (agent_t.c.id, agent_run_t.c.agent, agent_version_t.c.agent, approval_t.c.agent,
                                                                                 agent_event_t.c.agent)]
        extra['id'] = max(used) + 1
    return c.execute(agent_t.insert().values(**extra, name=name, description=description, icon=icon, template=template, enabled=False, chat_callable=chat_callable,
                                             draft=dsl, permissions=permissions if permissions is not None else ['notify', 'report'], created_by=user['username'],
                                             created_at=n, updated_at=n)).inserted_primary_key[0]


@router.post('/api/agents')
def create_agent(body: dict = Body(...), user=Depends(current_user)):
    _need_editor(user)
    with db.engine.begin() as c:
        if body.get('template'):
            t = templates.template(body['template'])
            if not t:
                raise HTTPException(404, _tr('没有这个模板'))
            aid = _create(c, user, body.get('name') or t['name'], t['dsl'], t['description'], t['icon'], t['key'], t['permissions'], t['chat_callable'])
        else:
            aid = _create(c, user, body.get('name') or _tr('新智能体'), templates.blank(), body.get('description') or '')
        ops.audit(c, user['username'], 'agent_create', f'agent {aid}', {'template': body.get('template')})
    return {'id': aid}


@router.post('/api/agents/import')
def import_agent(body: dict = Body(...), user=Depends(current_user)):
    _need_editor(user)
    dsl = body.get('dsl') or {}
    if not isinstance(dsl, dict) or 'components' not in dsl:
        raise HTTPException(400, _tr('文件格式不对：缺少 dsl.components'))
    for nid, comp in dsl['components'].items():
        if comp.get('type') not in nodes.TYPES:
            raise HTTPException(400, _tr('节点 {nid} 的类型 {get} 不认识', nid=nid, get=comp.get("type")))
    with db.engine.begin() as c:
        perms = body.get('permissions')
        aid = _create(c, user, (body.get('name') or _tr('导入的智能体')) + _tr('（导入）'), dsl, body.get('description') or '', body.get('icon') or 'MagicStick', body.get('template'),
                      perms if isinstance(perms, list) else None, bool(body.get('chat_callable')))
        ops.audit(c, user['username'], 'agent_import', f'agent {aid}', {})
    return {'id': aid}


@router.get('/api/agents/{aid}')
def get_agent(aid: int, user=Depends(current_user)):
    with db.engine.connect() as c:
        a = _agent(c, aid)
        vers = [dict(r) for r in c.execute(sa.select(agent_version_t.c.id, agent_version_t.c.version, agent_version_t.c.note, agent_version_t.c.published_by,
                                                     agent_version_t.c.published_at).where(agent_version_t.c.agent == aid).order_by(agent_version_t.c.version.desc())).mappings()]
        card = agent_card(c, a)
    return {**card, 'draft': a['draft'], 'versions': _j(vers), 'problems': engine.validate(a['draft'], a['permissions']), 'can_edit': auth.can_edit_agents(user),
            'webhook': f'/api/agents/{aid}/webhook?key={hook_key(aid)}' if card['trigger_kind'] == 'webhook' or runtime._bp(a['draft']).get('trigger') == 'webhook' else None}


@router.put('/api/agents/{aid}')
def save_agent(aid: int, body: dict = Body(...), user=Depends(current_user)):
    _need_editor(user)
    vals = {}
    for k in ('name', 'description', 'icon'):
        if k in body:
            vals[k] = (body[k] or '')[:300]
    if 'chat_callable' in body:
        vals['chat_callable'] = bool(body['chat_callable'])
    if 'permissions' in body:
        vals['permissions'] = [p for p in body['permissions'] or [] if p in ops.OPS]
    if 'draft' in body:
        d = body['draft']
        if not isinstance(d, dict) or 'components' not in d:
            raise HTTPException(400, _tr('画布数据不对'))
        vals['draft'] = d
    vals['updated_at'] = engine.now()
    with db.engine.begin() as c:
        a = _agent(c, aid)
        c.execute(agent_t.update().where(agent_t.c.id == aid).values(**vals))
        a.update(vals)
    RT.refresh(force=True)
    return {'ok': True, 'problems': engine.validate(a['draft'], a['permissions'])}


@router.delete('/api/agents/{aid}')
def delete_agent(aid: int, user=Depends(current_user)):
    _need_editor(user)
    with db.engine.begin() as c:
        _agent(c, aid)
        c.execute(approval_t.update().where((approval_t.c.agent == aid) & (approval_t.c.status == 'pending')).values(status='cancelled'))
        c.execute(agent_version_t.delete().where(agent_version_t.c.agent == aid))
        c.execute(agent_t.delete().where(agent_t.c.id == aid))
        ops.audit(c, user['username'], 'agent_delete', f'agent {aid}', {})
    RT.refresh(force=True)
    return {'ok': True}


@router.post('/api/agents/{aid}/publish')
def publish(aid: int, body: dict = Body(default={}), user=Depends(current_user)):
    _need_editor(user)
    with db.engine.begin() as c:
        a = _agent(c, aid)
        probs = [p for p in engine.validate(a['draft'], a['permissions']) if p['level'] == 'error']
        if probs:
            raise HTTPException(400, _tr('还有问题没解决：') + _tr('；').join(p['msg'] for p in probs[:5]))
        n = (c.execute(sa.select(sa.func.max(agent_version_t.c.version)).where(agent_version_t.c.agent == aid)).scalar() or 0) + 1
        vid = c.execute(agent_version_t.insert().values(agent=aid, version=n, dsl=copy.deepcopy(a['draft']), note=(body.get('note') or '')[:390],
                                                        published_by=user['username'], published_at=engine.now())).inserted_primary_key[0]
        c.execute(agent_t.update().where(agent_t.c.id == aid).values(published_version=vid, updated_at=engine.now()))
        ops.audit(c, user['username'], 'agent_publish', f'agent {aid}', {'version': n, 'note': body.get('note')})
    RT.refresh(force=True)
    return {'ok': True, 'version': n}


@router.post('/api/agents/{aid}/rollback')
def rollback(aid: int, body: dict = Body(...), user=Depends(current_user)):
    _need_editor(user)
    with db.engine.begin() as c:
        _agent(c, aid)
        v = _version(c, body.get('version_id'))
        if not v or v['agent'] != aid:
            raise HTTPException(404, _tr('版本不存在'))
        vals = {'published_version': v['id'], 'updated_at': engine.now()}
        if body.get('to_draft', True):
            vals['draft'] = copy.deepcopy(v['dsl'])
        c.execute(agent_t.update().where(agent_t.c.id == aid).values(**vals))
        ops.audit(c, user['username'], 'agent_rollback', f'agent {aid}', {'version': v['version']})
    RT.refresh(force=True)
    return {'ok': True, 'version': v['version']}


@router.post('/api/agents/{aid}/enable')
def enable(aid: int, body: dict = Body(...), user=Depends(current_user)):
    _need_editor(user)
    on = bool(body.get('enabled'))
    with db.engine.begin() as c:
        a = _agent(c, aid)
        if on and not a['published_version']:
            raise HTTPException(400, _tr('请先发布，再启用'))
        c.execute(agent_t.update().where(agent_t.c.id == aid).values(enabled=on))
        ops.audit(c, user['username'], 'agent_enable', f'agent {aid}', {'enabled': on})
    RT.refresh(force=True)
    return {'ok': True, 'enabled': on}


@router.get('/api/agents/{aid}/export')
def export(aid: int, user=Depends(current_user)):
    with db.engine.connect() as c:
        a = _agent(c, aid)
    return {'format': 'honeymom-agent', 'format_version': 1, 'name': a['name'], 'description': a['description'], 'icon': a['icon'], 'template': a['template'],
            'permissions': a['permissions'], 'chat_callable': a['chat_callable'], 'dsl': a['draft']}


@router.post('/api/agents/{aid}/validate')
def validate(aid: int, body: dict = Body(...), user=Depends(current_user)):
    with db.engine.connect() as c:
        a = _agent(c, aid)
    return {'problems': engine.validate(body.get('draft') or a['draft'], body.get('permissions', a['permissions']))}


# ---------------- 运行 ----------------
@router.post('/api/agents/{aid}/run')
def run(aid: int, body: dict = Body(default={}), user=Depends(current_user)):
    """调试面板/列表页"运行"：use=draft|published，mode=dry|live。"""
    with db.engine.connect() as c:
        a = _agent(c, aid)
        use = body.get('use') or 'draft'
        if use == 'published':
            v = _version(c, a['published_version'])
            if not v:
                raise HTTPException(400, _tr('还没有发布的版本'))
            dsl, vid, run_as = v['dsl'], v['id'], v['published_by']
        else:
            _need_editor(user)
            dsl, vid, run_as = a['draft'], None, user['username']
    errs = [p for p in engine.validate(dsl, a['permissions']) if p['level'] == 'error']
    if errs:
        raise HTTPException(400, _tr('画布还有问题：') + _tr('；').join(p['msg'] for p in errs[:5]))
    mode = 'dry' if body.get('mode') == 'dry' else 'live'
    if mode == 'live' and use != 'published' and not auth.can_edit_agents(user):
        raise HTTPException(403, _tr('没有权限'))
    rid = engine.start_run(aid, dsl, version=vid, mode=mode, trigger=body.get('trigger') or 'debug', inputs=body.get('inputs') or {}, user=user['username'],
                           run_as=run_as, wait=min(60.0, float(body.get('wait') or 0)))
    with db.engine.begin() as c:
        ops.audit(c, user['username'], 'agent_run', f'agent {aid}', {'run': rid, 'mode': mode, 'use': use})
    return {'run_id': rid}


@router.post('/api/agents/{aid}/run_node')
def run_node(aid: int, body: dict = Body(...), user=Depends(current_user)):
    _need_editor(user)
    with db.engine.connect() as c:
        a = _agent(c, aid)
    dsl = body.get('draft') or a['draft']
    nid = body.get('node')
    if nid not in (dsl.get('components') or {}):
        raise HTTPException(404, _tr('节点不存在'))
    rid = engine.run_single(aid, dsl, nid, body.get('inputs') or {}, body.get('mocks') or {}, user['username'])
    return run_detail(rid, user)


@router.get('/api/agents/{aid}/runs')
def runs(aid: int, limit: int = 30, user=Depends(current_user)):
    cols = [agent_run_t.c[k] for k in ('id', 'agent', 'version', 'mode', 'trigger', 'trigger_key', 'status', 'error', 'tokens', 'steps', 'created_by', 'created_at',
                                       'started_at', 'ended_at', 'output')]
    with db.engine.connect() as c:
        rows = [dict(r) for r in c.execute(sa.select(*cols).where((agent_run_t.c.agent == aid) & (agent_run_t.c.trigger != 'node_test'))
                                           .order_by(agent_run_t.c.id.desc()).limit(min(200, limit))).mappings()]
    return _j(rows)


@router.get('/api/agents/runs/{rid}')
def run_detail(rid: int, user=Depends(current_user)):
    with db.engine.connect() as c:
        r = c.execute(sa.select(agent_run_t).where(agent_run_t.c.id == rid)).mappings().first()
        if not r:
            raise HTTPException(404, _tr('运行记录不存在'))
        r = dict(r)
        steps = [dict(x) for x in c.execute(sa.select(agent_step_t).where(agent_step_t.c.run == rid).order_by(agent_step_t.c.id)).mappings()]
        aps = [dict(x) for x in c.execute(sa.select(approval_t).where(approval_t.c.run == rid).order_by(approval_t.c.id)).mappings()]
        name = c.execute(sa.select(agent_t.c.name).where(agent_t.c.id == r['agent'])).scalar()
    st = r['state'] or {}
    return {'id': rid, 'agent': r['agent'], 'agent_name': name, 'version': r['version'], 'mode': r['mode'], 'trigger': r['trigger'], 'status': r['status'],
            'error': r['error'], 'tokens': r['tokens'], 'steps_n': r['steps'], 'inputs': r['inputs'], 'output': r['output'], 'created_by': r['created_by'],
            'created_at': db.jsonable(r['created_at']), 'started_at': db.jsonable(r['started_at']), 'ended_at': db.jsonable(r['ended_at']),
            'node_status': st.get('status') or {}, 'outputs': st.get('outputs') or {}, 'waiting': st.get('waiting'), 'dsl': r['dsl'],
            'steps': _j(steps), 'approvals': _j(aps)}


@router.post('/api/agents/runs/{rid}/cancel')
def cancel(rid: int, user=Depends(current_user)):
    with db.engine.begin() as c:
        r = c.execute(sa.select(agent_run_t.c.status).where(agent_run_t.c.id == rid)).scalar()
        if r is None:
            raise HTTPException(404, _tr('运行记录不存在'))
        if r in ('succeeded', 'failed', 'rejected', 'cancelled'):
            return {'ok': True, 'status': r}
        c.execute(agent_run_t.update().where(agent_run_t.c.id == rid).values(status='cancelled', ended_at=engine.now(), error=_tr('被 {v} 取消', v=user['name'] or user['username'])))
        c.execute(approval_t.update().where((approval_t.c.run == rid) & (approval_t.c.status == 'pending')).values(status='cancelled'))
        ops.audit(c, user['username'], 'agent_cancel', f'run {rid}', {})
    return {'ok': True, 'status': 'cancelled'}


@router.get('/api/agents/{aid}/events')
def events(aid: int, limit: int = 50, user=Depends(current_user)):
    with db.engine.connect() as c:
        rows = [dict(r) for r in c.execute(sa.select(agent_event_t).where(agent_event_t.c.agent == aid).order_by(agent_event_t.c.id.desc()).limit(min(200, limit))).mappings()]
    return _j(rows)


# ---------------- 模拟事件：拿真实记录当输入 ----------------
@router.get('/api/agents/sample_events/{kind}')
def sample_events(kind: str, user=Depends(current_user)):
    out_ = []
    with db.engine.connect() as c:
        if kind == 'equipment_state':
            st, al = TABLES['equip_state'], TABLES['alarm']
            for r in c.execute(sa.select(st).where(st.c.state == 'Stopped').order_by(st.c.id.desc()).limit(10)).mappings():
                eq = db.get(c, 'equipment', r['equipment'])
                if not eq:
                    continue
                msg = c.execute(sa.select(al.c.msg).where((al.c.equipment == eq['id']) & (al.c.start <= r['start'] + timedelta(seconds=30))
                                                          & sa.or_(al.c.end.is_(None), al.c.end >= r['start'])).order_by(al.c.id.desc()).limit(1)).scalar()
                out_.append({'label': _tr('{code} {name} 停机 {jsonable}', code=eq['code'], name=eq['name'], jsonable=db.jsonable(r['start'])) + (_tr('（{msg}）', msg=msg) if msg else ''),
                             'payload': runtime.state_payload(eq, 'running', 'down', 'Stopped', msg, r['start'])})
        elif kind == 'andon':
            t = TABLES['andon']
            for r in db.rows(c, 'andon', order=t.c.id.desc(), limit=10):
                out_.append({'label': f"{r['code']} {r['description']}", 'payload': runtime.andon_payload(c, r)})
            if not out_:
                al = TABLES['alarm']
                for r in db.rows(c, 'alarm', order=al.c.id.desc(), limit=8):
                    eq = db.get(c, 'equipment', r['equipment'])
                    out_.append({'label': _tr('（由报警生成）{v} {msg}', v=eq['code'] if eq else '', msg=r['msg']), 'payload': {
                        'andon_id': None, 'code': f"AD-模拟{r['id']}", 'atype': 'equipment', 'description': r['msg'], 'equipment_code': eq['code'] if eq else '',
                        'equipment_name': eq['name'] if eq else '', 'station': eq['code'] if eq else '', 'raised_by': _tr('模拟'), 'time': db.jsonable(r['start'])}})
        elif kind == 'ncr':
            t = TABLES['ncr']
            for r in db.rows(c, 'ncr', order=t.c.id.desc(), limit=10):
                p = runtime.ncr_payload(c, r)
                out_.append({'label': f"{r['code']} {p['defect_name'] or _tr('未分类')} {r['sn'] or ''}", 'payload': p})
        elif kind == 'alarm':
            al = TABLES['alarm']
            for r in db.rows(c, 'alarm', order=al.c.id.desc(), limit=10):
                eq = db.get(c, 'equipment', r['equipment'])
                if eq:
                    out_.append({'label': f"{eq['code']} {r['msg']} {db.jsonable(r['start'])}", 'payload': {'equipment_code': eq['code'], 'equipment_name': eq['name'],
                                                                                                           'etype': eq['etype'], 'msg': r['msg'], 'time': db.jsonable(r['start'])}})
    if kind == 'material_low':
        from .queries import q_shortage, q_stock
        for r in q_stock({'low_only': True})['rows'][:10]:
            out_.append({'label': _tr('{material_name} 库存 {qty:g} / 安全库存 {safety_stock:g}', material_name=r['material_name'], qty=r['qty'], safety_stock=r['safety_stock']), 'payload': {**r, 'time': db.jsonable(clock.now())}})
    elif kind == 'wo_late':
        late = runtime.late_orders()
        for p in late[:10]:
            out_.append({'label': _tr('{code} 交期 {v} 预计 {v2}', code=p['code'], v=(p['due'] or '')[:16], v2=(p['eta'] or '—')[:16]), 'payload': p})
        if not late:
            from ..services.production import board
            for w in board(user=None)[:5]:
                out_.append({'label': _tr('（当作延误）{code} 交期 {v}', code=w['code'], v=(w.get('due') or '')[:16]), 'payload': runtime.wo_payload(w)})
    elif kind == 'oee_low':
        from .queries import q_oee
        for r in q_oee({'hours': 8})['rows'][:10]:
            out_.append({'label': f"{r['code']} {r['name']} OEE {r['OEE'] * 100:.0f}%", 'payload': {
                'equipment_code': r['code'], 'equipment_name': r['name'], 'oee': round(r['OEE'] * 100, 1), 'A': r['A'], 'P': r['P'], 'Q': r['Q'], 'hours': 8,
                'loss': r['loss_h'], 'time': db.jsonable(clock.now())}})
    return out_


@router.post('/api/agents/debug/emit')
def debug_emit(body: dict = Body(...), user=Depends(current_user)):
    """在 MES 里模拟一次现场事件（管理员；虚拟工厂连不上时做端到端演示用）。
    kind=equipment_state：走采集入库的同一条路（写设备状态记录、改设备状态、触发智能体）；kind=raw：直接把事件交给触发器。"""
    if 'admin' not in user['role_list']:
        raise HTTPException(403, _tr('只有管理员可以模拟事件'))
    kind = body.get('kind')
    if kind == 'equipment_state':
        from ..ingest import ING
        with db.engine.connect() as c:
            eq = ops._find(c, 'equipment', body.get('equipment'))
        if not eq:
            raise HTTPException(404, _tr('设备不存在'))
        if ING.state.get(eq['id']) is None:
            ING.reload()
        src = eq['source'] or f"SIM:{eq['code']}"
        if body.get('alarm') is not None:
            ING.on_tag(eq['id'], 'Common/Alarm', body.get('alarm'), clock.now(), f'{src}/Common/Alarm')
        want = body.get('state') or 'Stopped'
        cur = ING.state.get(eq['id'])
        if cur is None:
            ING.state[eq['id']] = ('Executing', clock.now(), 0)
        elif cur[0] == want:
            ING.on_tag(eq['id'], 'Common/State', 'Executing', clock.now(), f'{src}/Common/State')
        ING.on_tag(eq['id'], 'Common/State', want, clock.now(), f'{src}/Common/State')
        return {'ok': True, 'equipment': eq['code']}
    if kind == 'raw':
        RT.refresh(force=True)
        for _ in range(int(body.get('repeat') or 1)):
            RT.dispatch(body.get('event'), body.get('payload') or {})
        return {'ok': True, 'pending': len(RT.pending)}
    raise HTTPException(400, _tr('kind 只能是 equipment_state 或 raw'))


# ---------------- Webhook ----------------
@router.post('/api/agents/{aid}/webhook')
async def webhook(aid: int, request: Request, key: str = ''):
    if not hmac.compare_digest(key, hook_key(aid)):
        raise HTTPException(403, _tr('密钥不对'))
    try:
        body = await request.json()
    except Exception:
        body = {}
    with db.engine.connect() as c:
        a = _agent(c, aid)
        v = _version(c, a['published_version'])
    if not v or not a['enabled']:
        raise HTTPException(400, _tr('智能体没有发布或没有启用'))
    if runtime._bp(v['dsl']).get('trigger') != 'webhook':
        raise HTTPException(400, _tr('这个智能体不是 Webhook 触发'))
    rid = engine.start_run(aid, v['dsl'], version=v['id'], mode='live', trigger='webhook', inputs=body if isinstance(body, dict) else {'body': body},
                           user='webhook', run_as=v['published_by'])
    return {'ok': True, 'run_id': rid}


# ---------------- 审批中心 ----------------
def _visible(user):
    roles = user['role_list']
    if set(roles) & {'admin', 'manager'}:
        return sa.true()
    mine = approval_t.c.assignee_role == 'user:' + user['username']          # M3 审批流程：指定人审批
    return sa.or_(approval_t.c.assignee_role.in_(roles), mine) if roles else mine


def approval_out(c, rows, user):
    names = {r[0]: r[1] for r in c.execute(sa.select(agent_t.c.id, agent_t.c.name)).all()}
    res = []
    for a in rows:
        d = {k: db.jsonable(v) for k, v in dict(a).items()}
        d['agent_name'] = names.get(a['agent'], '（已删除）')
        if a['kind'] == 'ai_draft':          # AI 动作草稿（services/ai_drafts.py）：同一张表，界面用草稿卡显示改动前后对比
            from ..services.ai_drafts import SOURCES, draft_out
            d['agent_name'] = SOURCES.get((a['fields'] or {}).get('source'), 'AI 助手')
            d['draft'] = draft_out(c, dict(a), user)
        d['can_decide'] = a['status'] == 'pending' and auth.has_role(user, a['assignee_role'])
        d['role_label'] = ROLES.get(a['assignee_role'], a['assignee_role'])
        if a['kind'] == 'flow':              # M3 审批流程（impl/flows.py）：按角色或指定人审批，最后一步通过后执行原操作
            from ..impl import flows
            d['flow'] = flows.flow_out(c, dict(a), user)
            d['agent_name'], d['can_decide'], d['role_label'] = d['flow']['flow'] or _tr('审批流程'), d['flow']['can_decide'], flows.role_label(a)
        res.append(d)
    return res


@router.get('/api/approvals')
def approvals(status: str = 'pending', limit: int = 50, run: int = 0, user=Depends(current_user)):
    with db.engine.connect() as c:
        q = sa.select(approval_t).where(_visible(user))
        if run:      # 调试面板看某次运行的审批：能编辑智能体的人都能看到（能不能批仍按角色）
            q = sa.select(approval_t).where((approval_t.c.run == run) & (_visible(user) if not auth.can_edit_agents(user) else sa.true()))
        elif status == 'pending':
            q = q.where(approval_t.c.status == 'pending')
        else:
            q = q.where(approval_t.c.status != 'pending')
        from ..services import ai_switch as _sw
        if not _sw.enabled('agents'):          # M3：智能体关闭时只看审批流程（kind=flow）
            q = q.where(approval_t.c.kind == 'flow')
        rows = [dict(r) for r in c.execute(q.order_by(approval_t.c.id.desc()).limit(min(200, limit))).mappings()]
        return approval_out(c, rows, user)


@router.get('/api/approvals/count')
def approvals_count(user=Depends(current_user)):
    with db.engine.connect() as c:
        from ..services import ai_switch as _sw
        cond = _visible(user) & (approval_t.c.status == 'pending')
        if not _sw.enabled('agents'):          # M3：智能体关闭时只数审批流程
            cond = cond & (approval_t.c.kind == 'flow')
        n = c.execute(sa.select(sa.func.count()).select_from(approval_t).where(cond)).scalar()
    return {'pending': n}


@router.post('/api/approvals/{aid}/decide')
def decide(aid: int, body: dict = Body(...), user=Depends(current_user)):
    with db.engine.connect() as c:
        ap = c.execute(sa.select(approval_t).where(approval_t.c.id == aid)).mappings().first()
    if not ap:
        raise HTTPException(404, _tr('审批单不存在'))
    if ap['kind'] == 'flow':                 # M3 审批流程：不依赖 AI 开关
        from ..impl import flows
        fd = flows.decide(aid, body.get('decision'), body.get('reason') or '', user)
        return {'ok': True, 'status': fd['status'], 'run': None, 'flow': fd}
    from ..services import ai_switch as _sw
    if not _sw.enabled('agents'):
        raise _sw.AiDisabled('agents')
    if not auth.has_role(user, ap['assignee_role']):
        raise HTTPException(403, _tr('这张单子要由{get}处理', get=ROLES.get(ap['assignee_role'], ap['assignee_role'])))
    if ap['kind'] == 'ai_draft':             # AI 动作草稿：通过 = 执行（不属于任何智能体运行）
        from ..services import ai_drafts
        d = ai_drafts.decide(aid, body.get('decision'), body.get('payload'), body.get('reason') or '', user)
        return {'ok': True, 'status': d['status'], 'run': None, 'draft': d}
    d = body.get('decision')
    if d not in ('approve', 'reject'):
        raise HTTPException(400, _tr('decision 只能是 approve 或 reject'))
    payload = body.get('payload')
    if d == 'approve' and payload is not None and (ap['fields'] or {}).get('action'):
        with db.engine.connect() as c:
            try:
                ops.validate(c, ap['fields']['action'], payload)
            except ops.OpError as ex:
                raise HTTPException(400, _tr('修改后的内容不对：{ex}', ex=ex))
    try:
        r = runtime.decide(aid, d, payload, body.get('reason') or '', user)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {'ok': True, 'status': r['status'], 'run': r['run']}
