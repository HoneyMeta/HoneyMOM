"""业务写操作的统一入口：智能体的"动作"节点和聊天助手的写工具都走这里。

每个操作：校验参数（规范化）→ 预览文字 → 当前状态指纹（聊天助手的提交令牌要绑定它）→ 执行并返回服务端回执。
另外放安灯、物料申请、排程建议的实体动作，以及站内通知接口。
"""
from ..i18n import _tr
import hashlib
import json
import urllib.request
from datetime import timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends

from .. import db
from ..auth import current_user
from ..crud import action, attach_labels, out
from ..db import clock
from ..schema import TABLES

router = APIRouter()


class OpError(Exception):
    """参数不对或状态不允许：消息直接给用户/大模型看。"""


# ---------------- 编码 → 记录 ----------------
def _find(c, entity, key, fields=('code', 'name')):
    if key in (None, ''):
        return None
    if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
        r = db.get(c, entity, int(key))
        if r:
            return r
    t = TABLES[entity]
    s = str(key).strip()
    for f in fields:
        if f in t.c:
            r = c.execute(sa.select(t).where(t.c[f] == s).limit(1)).mappings().first()
            if r:
                return dict(r)
    # 允许"CNC3 3#加工中心"这种带名称的写法
    first = s.split()[0] if s.split() else s
    if first != s:
        return _find(c, entity, first, fields)
    return None


def _need(c, entity, key, label):
    r = _find(c, entity, key)
    if not r:
        raise OpError(_tr('找不到{label}：{key}', label=label, key=key))
    return r


def _txt(v, n=2000):
    if v is None:
        return ''
    if not isinstance(v, str):
        v = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
    return v.strip()[:n]


def _link(entity, rid):
    return f'/e/{entity}?id={rid}'


# ---------------- 各操作 ----------------
def v_repair(c, p):
    eq = _need(c, 'equipment', p.get('equipment'), _tr('设备'))
    fault = _txt(p.get('fault'), 190)
    if not fault:
        raise OpError(_tr('请填写故障描述'))
    level = p.get('level') if p.get('level') in ('urgent', 'normal') else ('urgent' if str(p.get('level') or '') in ('紧急', '高') else 'normal')
    return {'equipment': eq['code'], 'fault': fault, 'level': level, 'parts_used': _txt(p.get('parts_used') or p.get('parts'), 190),
            'cause': _txt(p.get('cause'), 2000), 'reported_by': _txt(p.get('reported_by'), 80)}


def x_repair(c, p, who, source):
    eq = _need(c, 'equipment', p['equipment'], _tr('设备'))
    rid = db.insert(c, 'repair_order', {'equipment': eq['id'], 'fault': p['fault'], 'level': p['level'], 'parts_used': p.get('parts_used') or None,
                                        'cause': p.get('cause') or None, 'reported_by': p.get('reported_by') or who, 'reported_at': clock.now(),
                                        'status': 'reported'}, raw=True)
    r = db.get(c, 'repair_order', rid)
    return {'entity': 'repair_order', 'id': rid, 'code': r['code'], 'text': _tr('已创建维修工单 {code}（{code2} {name}：{fault}）', code=r['code'], code2=eq['code'], name=eq['name'], fault=p['fault']),
            'link': _link('repair_order', rid)}


def s_repair(c, p):
    eq = _find(c, 'equipment', p.get('equipment'))
    if not eq:
        return ''
    t = TABLES['repair_order']
    n = c.execute(sa.select(sa.func.count()).select_from(t).where((t.c.equipment == eq['id']) & t.c.status.in_(('reported', 'assigned', 'repairing')))).scalar()
    return f"{eq['id']}:{eq['status']}:{n}"


def v_ncr(c, p):
    exist = _find(c, 'ncr', p.get('ncr')) if p.get('ncr') else None
    if p.get('ncr') and not exist:
        raise OpError(_tr('找不到不合格品单：{get}', get=p.get('ncr')))
    d = _find(c, 'defect_code', p.get('defect'))
    wo = _find(c, 'work_order', p.get('work_order'))
    desc = _txt(p.get('description') or p.get('d2'), 2000)
    if not (exist or d or desc or p.get('sn')):
        raise OpError(_tr('请至少给出缺陷类型、零件码或问题描述'))
    w8 = p.get('with_8d', True)
    out_ = {'ncr': exist['code'] if exist else '', 'sn': _txt(p.get('sn'), 80), 'defect': d['code'] if d else '', 'work_order': wo['code'] if wo else '',
            'qty': int(float(p.get('qty') or 1)), 'station': _txt(p.get('station'), 80), 'description': desc,
            'with_8d': w8 if isinstance(w8, bool) else str(w8).lower() not in ('0', 'false', 'no', '否')}
    for k in ('d1', 'd2', 'd3'):
        out_[k] = _txt(p.get(k), 3000)
    return out_


def x_ncr(c, p, who, source):
    d = _find(c, 'defect_code', p.get('defect'))
    exist = _find(c, 'ncr', p.get('ncr')) if p.get('ncr') else None
    if exist:
        nid, n = exist['id'], exist
        upd = {'status': 'reviewing' if exist['status'] == 'open' else exist['status']}
        if p.get('description') and not exist['root_cause']:
            upd['root_cause'] = p['description']
        db.update(c, 'ncr', nid, upd, raw=True)
        d = d or (db.get(c, 'defect_code', exist['defect']) if exist['defect'] else None)
        rec = {'entity': 'ncr', 'id': nid, 'code': n['code'], 'text': _tr('不合格品单 {code} 转为评审中', code=n['code']), 'link': _link('ncr', nid)}
    else:
        wo = _find(c, 'work_order', p.get('work_order'))
        nid = db.insert(c, 'ncr', {'sn': p.get('sn') or None, 'work_order': wo['id'] if wo else None, 'qty': p.get('qty') or 1, 'defect': d['id'] if d else None,
                                   'station': p.get('station') or None, 'found_at': clock.now(), 'found_by': who, 'source': 'manual', 'disposition': 'pending',
                                   'status': 'open', 'root_cause': p.get('description') or None}, raw=True)
        n = db.get(c, 'ncr', nid)
        rec = {'entity': 'ncr', 'id': nid, 'code': n['code'], 'text': _tr('已创建不合格品单 {code}', code=n['code']) + (_tr('（{name}）', name=d['name']) if d else ''), 'link': _link('ncr', nid)}
    if p.get('with_8d'):
        rid = db.insert(c, 'report_8d', {'title': _tr('{v}（{code}）', v=d['name'] if d else _tr('质量异常'), code=n['code']), 'ncr': nid, 'defect': d['id'] if d else None,
                                         'd1': p.get('d1') or None, 'd2': p.get('d2') or p.get('description') or None, 'd3': p.get('d3') or None,
                                         'status': 'draft', 'ai_generated': source == 'agent'}, raw=True)
        r8 = db.get(c, 'report_8d', rid)
        rec['extra'] = [{'entity': 'report_8d', 'id': rid, 'code': r8['code'], 'text': _tr('已创建 8D 草稿 {code}', code=r8['code']), 'link': _link('report_8d', rid)}]
        rec['text'] += _tr('，并起草 8D {code}', code=r8['code'])
    return rec


def s_ncr(c, p):
    n = _find(c, 'ncr', p.get('ncr')) if p.get('ncr') else None
    return f"{n['id']}:{n['status']}" if n else ''


def v_sched(c, p):
    title = _txt(p.get('title'), 190)
    if not title:
        raise OpError(_tr('请填写建议标题'))
    wo = _find(c, 'work_order', p.get('work_order'))
    changes = p.get('changes') or []
    if isinstance(changes, str):
        try:
            changes = json.loads(changes)
        except ValueError:
            changes = []
    clean = []
    for ch in changes if isinstance(changes, list) else []:
        if not isinstance(ch, dict):
            continue
        w = _find(c, 'work_order', ch.get('work_order'))
        if not w:
            continue
        item = {'work_order': w['code']}
        if ch.get('priority') not in (None, ''):
            item['priority'] = max(1, min(9, int(float(ch['priority']))))
        if ch.get('note'):
            item['note'] = _txt(ch['note'], 200)
        clean.append(item)
    return {'title': title, 'work_order': wo['code'] if wo else '', 'proposal': _txt(p.get('proposal'), 4000), 'changes': clean}


def x_sched(c, p, who, source):
    wo = _find(c, 'work_order', p.get('work_order'))
    rid = db.insert(c, 'schedule_proposal', {'title': p['title'], 'work_order': wo['id'] if wo else None, 'proposal': p.get('proposal'), 'changes': p.get('changes') or [],
                                             'status': 'pending', 'created_by': who, 'source': 'agent' if source == 'agent' else 'manual'}, raw=True)
    r = db.get(c, 'schedule_proposal', rid)
    return {'entity': 'schedule_proposal', 'id': rid, 'code': r['code'], 'text': _tr('已提交排程调整建议 {code}：{title}', code=r['code'], title=p['title']), 'link': _link('schedule_proposal', rid)}


ATYPE = {'设备': 'equipment', '质量': 'quality', '物料': 'material', '安全': 'safety', '其他': 'other'}


def v_andon(c, p):
    desc = _txt(p.get('description'), 190)
    if not desc:
        raise OpError(_tr('请填写问题描述'))
    eq = _find(c, 'equipment', p.get('equipment'))
    if p.get('equipment') and not eq:
        raise OpError(_tr('找不到设备：{get}', get=p.get('equipment')))
    at = p.get('atype') or 'equipment'
    at = ATYPE.get(at, at) if at not in ATYPE.values() else at
    if at not in ATYPE.values():
        at = 'other'
    return {'atype': at, 'equipment': eq['code'] if eq else '', 'station': _txt(p.get('station'), 80), 'description': desc}


def x_andon(c, p, who, source):
    eq = _find(c, 'equipment', p.get('equipment'))
    rid = db.insert(c, 'andon', {'atype': p['atype'], 'equipment': eq['id'] if eq else None, 'station': p.get('station') or (eq['code'] if eq else None),
                                 'description': p['description'], 'raised_by': who, 'raised_at': clock.now(), 'status': 'open',
                                 'source': 'agent' if source == 'agent' else 'manual'}, raw=True)
    r = db.get(c, 'andon', rid)
    return {'entity': 'andon', 'id': rid, 'code': r['code'], 'text': _tr('已发起安灯 {code}：{description}', code=r['code'], description=p['description']), 'link': _link('andon', rid)}


def v_andon_close(c, p):
    a = _need(c, 'andon', p.get('andon'), _tr('安灯'))
    if a['status'] == 'closed':
        raise OpError(_tr('安灯 {code} 已经关闭', code=a['code']))
    return {'andon': a['code'], 'close_note': _txt(p.get('close_note') or p.get('note'), 190) or _tr('已处理')}


def x_andon_close(c, p, who, source):
    a = _need(c, 'andon', p['andon'], _tr('安灯'))
    if a['status'] == 'closed':
        raise OpError(_tr('安灯 {code} 已经关闭', code=a['code']))
    db.update(c, 'andon', a['id'], {'status': 'closed', 'closed_at': clock.now(), 'close_note': p['close_note'], 'responder': a['responder'] or who}, raw=True)
    return {'entity': 'andon', 'id': a['id'], 'code': a['code'], 'text': _tr('已关闭安灯 {code}', code=a['code']), 'link': _link('andon', a['id'])}


def s_andon_close(c, p):
    a = _find(c, 'andon', p.get('andon'))
    return f"{a['id']}:{a['status']}:{db.jsonable(a['updated_at'])}" if a else ''


def v_matreq(c, p):
    m = _need(c, 'material', p.get('material'), _tr('物料'))
    try:
        qty = float(p.get('qty'))
    except (TypeError, ValueError):
        raise OpError(_tr('数量不对'))
    if qty <= 0:
        raise OpError(_tr('数量必须大于 0'))
    wo = _find(c, 'work_order', p.get('work_order'))
    wh = _find(c, 'warehouse', p.get('warehouse'))
    return {'material': m['code'], 'qty': round(qty, 4), 'work_order': wo['code'] if wo else '', 'warehouse': wh['code'] if wh else '', 'reason': _txt(p.get('reason'), 190)}


def x_matreq(c, p, who, source):
    m = _need(c, 'material', p['material'], _tr('物料'))
    wo = _find(c, 'work_order', p.get('work_order'))
    wh = _find(c, 'warehouse', p.get('warehouse'))
    rid = db.insert(c, 'material_request', {'material': m['id'], 'qty': p['qty'], 'work_order': wo['id'] if wo else None, 'warehouse': wh['id'] if wh else None,
                                            'reason': p.get('reason'), 'requested_by': who, 'requested_at': clock.now(), 'status': 'submitted',
                                            'source': 'agent' if source == 'agent' else 'manual'}, raw=True)
    r = db.get(c, 'material_request', rid)
    return {'entity': 'material_request', 'id': rid, 'code': r['code'], 'text': _tr('已提交物料申请 {code}：{name} {qty:g} {v}', code=r['code'], name=m['name'], qty=p['qty'], v=m['unit'] or ''),
            'link': _link('material_request', rid)}


def v_notify(c, p):
    ch = p.get('channel') or 'inbox'
    if ch not in ('inbox', 'board', 'webhook'):
        raise OpError(_tr('通知渠道只能是 站内信 / 看板 / Webhook'))
    title = _txt(p.get('title'), 190)
    if not title:
        raise OpError(_tr('请填写通知标题'))
    if ch == 'webhook' and not str(p.get('webhook_url') or '').startswith(('http://', 'https://')):
        raise OpError(_tr('请填写 Webhook 地址（企业微信/钉钉机器人）'))
    return {'channel': ch, 'title': title, 'content': _txt(p.get('content'), 4000), 'to_role': _txt(p.get('to_role'), 40), 'to_user': _txt(p.get('to_user'), 80),
            'level': p.get('level') if p.get('level') in ('info', 'warn', 'urgent') else 'info', 'link': _txt(p.get('link'), 190),
            'webhook_url': _txt(p.get('webhook_url'), 500), 'webhook_kind': p.get('webhook_kind') or 'wecom'}


def x_notify(c, p, who, source):
    if p['channel'] == 'webhook':
        text = f"**{p['title']}**\n\n{p['content']}"
        body = ({'msgtype': 'markdown', 'markdown': {'title': p['title'], 'text': text}} if p['webhook_kind'] == 'dingtalk'
                else {'msgtype': 'markdown', 'markdown': {'content': text}} if p['webhook_kind'] == 'wecom' else {'title': p['title'], 'content': p['content']})
        req = urllib.request.Request(p['webhook_url'], data=json.dumps(body, ensure_ascii=False).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        return {'entity': None, 'id': None, 'code': '', 'text': _tr('已推送到 Webhook：{title}', title=p['title']), 'link': ''}
    rid = db.insert(c, 'notice', {'title': p['title'], 'content': p['content'], 'to_role': p['to_role'] or None, 'to_user': p['to_user'] or None,
                                  'channel': p['channel'], 'level': p['level'], 'link': p['link'] or None, 'source': source if source != 'agent' else _tr('智能体（{who}）', who=who),
                                  'read': False, 'time': clock.now()}, raw=True)
    from ..schema import ROLES
    whom = _tr('给{get}', get=ROLES.get(p['to_role'], p['to_role'])) if p['to_role'] else (_tr('给 {to_user}', to_user=p['to_user']) if p['to_user'] else _tr('给所有人'))
    return {'entity': 'notice', 'id': rid, 'code': '', 'text': _tr('已发{v}{whom}：{title}', v=_tr('看板通知') if p['channel'] == 'board' else _tr('站内信'), whom=whom, title=p['title']), 'link': '/andon' if p['channel'] == 'board' else ''}


def v_report(c, p):
    title = _txt(p.get('title'), 190)
    if not title:
        raise OpError(_tr('请填写报告标题'))
    return {'title': title, 'kind': _txt(p.get('kind'), 40) or _tr('报告'), 'content': _txt(p.get('content'), 20000)}


def x_report(c, p, who, source):
    rid = db.insert(c, 'ai_report', {'title': p['title'], 'kind': p['kind'], 'content': p['content'], 'agent': who, 'time': clock.now()}, raw=True)
    return {'entity': 'ai_report', 'id': rid, 'code': '', 'text': _tr('已生成报告：{title}', title=p['title']), 'link': _link('ai_report', rid)}


# 名称、写哪个实体（权限按实体模块判断）、风险（high 必须经过审批）、字段（给界面和大模型用）
OPS = {
    'repair_order': dict(label='建维修工单', entity='repair_order', risk='high', v=v_repair, x=x_repair, s=s_repair, fields=[
        ('equipment', '设备编号', 'str', True), ('fault', '故障描述', 'str', True), ('level', '紧急程度 urgent/normal', 'str', False),
        ('parts_used', '备件清单', 'str', False), ('cause', '初步原因', 'text', False)]),
    'ncr': dict(label='建不合格品单 / 8D 草稿', entity='ncr', risk='high', v=v_ncr, x=x_ncr, s=s_ncr, fields=[
        ('ncr', '已有不合格品单号（填了就只补 8D）', 'str', False), ('defect', '缺陷代码或名称', 'str', False), ('sn', '零件码', 'str', False), ('work_order', '工单号', 'str', False), ('qty', '数量', 'int', False),
        ('station', '发现工位', 'str', False), ('description', '问题描述', 'text', False), ('with_8d', '同时起草 8D', 'bool', False),
        ('d1', 'D1 团队', 'text', False), ('d2', 'D2 问题描述', 'text', False), ('d3', 'D3 临时措施', 'text', False)]),
    'schedule_proposal': dict(label='提交排程调整建议', entity='schedule_proposal', risk='high', v=v_sched, x=x_sched, fields=[
        ('title', '标题', 'str', True), ('work_order', '涉及工单号', 'str', False), ('proposal', '建议内容', 'text', False),
        ('changes', '调整项 [{work_order, priority, note}]', 'json', False)]),
    'andon_raise': dict(label='发起安灯', entity='andon', risk='high', v=v_andon, x=x_andon, fields=[
        ('atype', '类型 equipment/quality/material/safety/other', 'str', False), ('equipment', '设备编号', 'str', False), ('station', '工位', 'str', False),
        ('description', '问题描述', 'str', True)]),
    'andon_close': dict(label='关闭安灯', entity='andon', risk='high', v=v_andon_close, x=x_andon_close, s=s_andon_close, fields=[
        ('andon', '安灯号', 'str', True), ('close_note', '处理结果', 'str', False)]),
    'material_request': dict(label='提交物料申请', entity='material_request', risk='high', v=v_matreq, x=x_matreq, fields=[
        ('material', '物料编码', 'str', True), ('qty', '数量', 'float', True), ('work_order', '工单号', 'str', False), ('warehouse', '发料仓库编码', 'str', False),
        ('reason', '原因', 'str', False)]),
    'notify': dict(label='发通知', entity='notice', risk='low', v=v_notify, x=x_notify, fields=[
        ('channel', '渠道 inbox/board/webhook', 'str', False), ('title', '标题', 'str', True), ('content', '内容', 'text', False),
        ('to_role', '接收角色', 'str', False), ('to_user', '接收人账号', 'str', False), ('level', '级别 info/warn/urgent', 'str', False),
        ('webhook_url', 'Webhook 地址', 'str', False), ('webhook_kind', 'wecom/dingtalk/generic', 'str', False)]),
    'report': dict(label='生成报告', entity='ai_report', risk='low', v=v_report, x=x_report, fields=[
        ('title', '标题', 'str', True), ('kind', '类型', 'str', False), ('content', '内容（Markdown）', 'text', True)]),
}


def validate(c, op, payload) -> dict:
    if op not in OPS:
        raise OpError(_tr('没有这个操作：{op}', op=op))
    return OPS[op]['v'](c, payload or {})


def state_of(c, op, payload) -> str:
    fn = OPS[op].get('s')
    return fn(c, payload) if fn else ''


def execute(c, op, payload, who, source) -> dict:
    p = validate(c, op, payload)
    rec = OPS[op]['x'](c, p, who, source)
    rec['op'] = op
    return rec


def preview(op, p) -> str:
    """给人看的改动说明。"""
    names = {k: lab for k, lab, *_ in OPS[op]['fields']}
    lines = [OPS[op]['label']]
    for k, v in p.items():
        if v in (None, '', [], False) or k == 'webhook_url':
            continue
        lines.append(_tr('· {v}：{v2}', v=names.get(k, k).split(' ')[0], v2=_txt(v, 300)))
    return '\n'.join(lines)


def fingerprint(payload) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def audit(c, user, kind, target, detail):
    """4.7 的写法，5.0 起转给 honeymom.audit.audit（带来源 / IP / 哈希链）。"""
    from .. import audit as audit_mod
    audit_mod.audit(c, kind, None, None, detail=detail, target=str(target)[:190], user=user)


# ---------------- 实体动作 ----------------
@action('andon', 'respond')
def _andon_respond(c, r, body, user):
    if r['status'] == 'closed':
        return {'msg': _tr('已经关闭')}
    db.update(c, 'andon', r['id'], {'status': 'responding', 'responder': user['name'] or user['username'], 'response_at': clock.now()}, raw=True)
    return {'msg': _tr('已响应')}


@action('andon', 'close')
def _andon_close(c, r, body, user):
    db.update(c, 'andon', r['id'], {'status': 'closed', 'closed_at': clock.now(), 'close_note': body.get('note') or _tr('已处理'),
                                     'responder': r['responder'] or user['name'] or user['username']}, raw=True)
    return {'msg': _tr('已关闭')}


@action('material_request', 'issue')
def _mr_issue(c, r, body, user):
    from .warehouse import move
    if r['status'] != 'submitted':
        return {'msg': _tr('这张申请已处理')}
    wh = r['warehouse']
    if not wh:
        st = TABLES['stock']
        wh = c.execute(sa.select(st.c.warehouse).where((st.c.material == r['material']) & (st.c.qty > 0)).order_by(st.c.qty.desc()).limit(1)).scalar()
    if wh:
        move(c, 'out', r['material'], r['qty'], wh, ref=r['code'], who=user['name'] or user['username'], reason=_tr('物料申请发料'))
    db.update(c, 'material_request', r['id'], {'status': 'issued', 'warehouse': wh}, raw=True)
    return {'msg': _tr('已发料') if wh else _tr('已标记发料（没有找到有库存的仓库，未过账）')}


@action('material_request', 'reject')
def _mr_reject(c, r, body, user):
    db.update(c, 'material_request', r['id'], {'status': 'rejected', 'reason': (r['reason'] or '') + _tr(' | 退回：{v}', v=body.get('note') or '')}, raw=True)
    return {'msg': _tr('已退回')}


@action('schedule_proposal', 'accept')
def _sp_accept(c, r, body, user):
    n = 0
    for ch in r['changes'] or []:
        w = _find(c, 'work_order', ch.get('work_order'))
        if w and ch.get('priority'):
            db.update(c, 'work_order', w['id'], {'priority': int(ch['priority'])}, raw=True)
            n += 1
    db.update(c, 'schedule_proposal', r['id'], {'status': 'accepted'}, raw=True)
    if n:
        from .production import schedule
        schedule(c)
    return {'msg': _tr('已采纳，调整了 {n} 个工单的优先级并重新排产', n=n) if n else _tr('已采纳')}


@action('schedule_proposal', 'reject')
def _sp_reject(c, r, body, user):
    db.update(c, 'schedule_proposal', r['id'], {'status': 'rejected'}, raw=True)
    return {'msg': _tr('已标记为不采纳')}


# ---------------- 站内通知 / 安灯看板 ----------------
def _mine(user):
    t = TABLES['notice']
    roles = user.get('role_list') or []
    return sa.or_(t.c.to_user == user['username'], t.c.to_role.in_(roles) if roles else sa.false(),
                  sa.and_(t.c.to_user.is_(None), t.c.to_role.is_(None)), *(([sa.true()]) if 'admin' in roles else []))


@router.get('/api/notices')
def notices(user=Depends(current_user)):
    t = TABLES['notice']
    with db.engine.connect() as c:
        rows = db.rows(c, 'notice', _mine(user), t.c.channel == 'inbox', order=t.c.id.desc(), limit=30)
        unread = c.execute(sa.select(sa.func.count()).select_from(t).where(_mine(user) & (t.c.channel == 'inbox') & (t.c.read.is_(False) | t.c.read.is_(None)))).scalar()
    return {'items': out(rows), 'unread': unread}


@router.post('/api/notices/read')
def notices_read(body: dict = Body(default={}), user=Depends(current_user)):
    t = TABLES['notice']
    with db.engine.begin() as c:
        q = t.update().where(_mine(user))
        if body.get('ids'):
            q = q.where(t.c.id.in_([int(i) for i in body['ids']]))
        c.execute(q.values(read=True))
    return {'ok': True}


@router.get('/api/andon/board')
def andon_board(user=Depends(current_user)):
    t, nt = TABLES['andon'], TABLES['notice']
    with db.engine.connect() as c:
        open_ = db.rows(c, 'andon', t.c.status != 'closed', order=t.c.id.desc(), limit=50)
        attach_labels(c, 'andon', open_)
        board = db.rows(c, 'notice', nt.c.channel == 'board', nt.c.time >= clock.now() - timedelta(days=2), order=nt.c.id.desc(), limit=30)
        closed = db.rows(c, 'andon', t.c.status == 'closed', order=t.c.id.desc(), limit=10)
        attach_labels(c, 'andon', closed)
    return {'open': out(open_), 'board': out(board), 'closed': out(closed), 'now': db.jsonable(clock.now())}


def _andon_fill(c, rid, user):
    """手工发起的安灯：补上发起人和时间。"""
    a = db.get(c, 'andon', rid)
    vals = {}
    if not a['raised_by']:
        vals['raised_by'] = user.get('name') or user['username']
    if not a['raised_at']:
        vals['raised_at'] = clock.now()
    if not a['status']:
        vals['status'] = 'open'
    if vals:
        db.update(c, 'andon', rid, vals, raw=True)


from ..crud import HOOKS  # noqa: E402

HOOKS[('andon', 'after_save')] = _andon_fill
