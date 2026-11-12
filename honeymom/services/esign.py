"""电子签名（IATF 16949 / 21 CFR Part 11 式）：关键放行要重新输入本人密码 + 选择签名含义（批准 / 复核 …），记入电子签名表和审计日志。

签名点（POINTS）：首件放行、批次解冻放行、ECN 批准、量具放行、8D 关闭、文件版本批准、客诉关闭。系统参数 esign_off（逗号分隔的"实体.动作"）可关掉个别点。

协议：业务接口里调 require(c, user, body, 实体, id, 动作, 对象名)。
  - 请求体没有 esign → HTTP 428，detail = {msg, esign: {entity, id, action, point, label, meanings}}，响应头 X-Esign: required。
    前端 api.ts 统一拦截：弹出签名框（密码 + 含义），把 {esign: {password, meaning}} 加进原请求体重发。
  - 密码不对 → 403（单独记一条 esign_fail 审计；在业务事务回滚之后写，见 install 里的异常处理）。
  - API 密钥不能签名。
"""
from ..i18n import _tr
import hashlib
import json

import sqlalchemy as sa
from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse

from .. import audit, auth, context, db, perms

MEANINGS = ['批准', '复核', '审核', '放行', '关闭']
POINTS = {
    'first_article.approve': ('首件检验放行', '放行'),
    'lot_hold.release': ('批次解冻放行', '放行'),
    'ecn.approve': ('工艺变更 ECN 批准', '批准'),
    'gauge.release': ('量检具放行', '放行'),
    'report_8d.close': ('8D 报告关闭', '批准'),
    'ctrl_doc_rev.approve': ('受控文件版本批准', '批准'),
    'complaint.close': ('客户投诉关闭', '批准'),
}
PERM_VIEW = perms.declare('iatf.esign.view', '查看电子签名记录', 'IATF 质量')


class EsignRequired(HTTPException):
    def __init__(self, entity, rid, action, label):
        point, meaning = POINTS.get(f'{entity}.{action}', (action, '批准'))
        super().__init__(428, {'msg': _tr('{point}需要电子签名：请输入你的登录密码并选择签名含义', point=point), 'esign': {
            'entity': entity, 'id': rid, 'action': action, 'point': point, 'label': label, 'meanings': MEANINGS, 'default': meaning}},
            headers={'X-Esign': 'required'})


class EsignFailed(Exception):
    def __init__(self, user, entity, rid, action, msg):
        super().__init__(msg)
        self.user, self.entity, self.rid, self.action, self.msg = user, entity, rid, action, msg


def required(c, entity, action) -> bool:
    off = {x.strip() for x in (db.setting(c, 'esign_off', '') or '').split(',') if x.strip()}
    return f'{entity}.{action}' in POINTS and f'{entity}.{action}' not in off


def require(c, user, body, entity, rid, action, label=''):
    """要求电子签名并记录。返回签名记录（不需要签名时返回 None）。"""
    if not required(c, entity, action):
        return None
    sig = (body or {}).get('esign') if isinstance(body, dict) else None
    if not sig or not isinstance(sig, dict) or not sig.get('password'):
        raise EsignRequired(entity, rid, action, label)
    if user is None or user.get('is_api_key'):
        raise HTTPException(403, _tr('电子签名只能由本人登录后签署（API 密钥不能签名）'))
    meaning = str(sig.get('meaning') or '').strip()[:20]
    if not meaning:
        raise HTTPException(400, _tr('请选择签名含义（批准 / 复核 …）'))
    u = db.get(c, 'sys_user', username=user['username'])
    if not u or not u['active'] or not auth.check_pw(str(sig['password']), u['password_hash']):
        raise EsignFailed(user['username'], entity, rid, action, _tr('密码不对，电子签名未通过'))
    now = db.clock_wall()
    payload = {'user': u['username'], 'entity': entity, 'id': rid, 'action': action, 'meaning': meaning, 'at': now.strftime('%Y-%m-%d %H:%M:%S'),
               'label': label}
    h = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    sid = db.insert(c, 'esign', {'entity': entity, 'entity_id': rid, 'action': action, 'meaning': meaning, 'user': u['username'], 'name': u['name'],
                                 'record_label': str(label)[:190], 'signed_at': now, 'ip': context.ip(), 'hash': h}, raw=True)
    audit.audit(c, 'esign', entity, rid, detail={'point': POINTS[f'{entity}.{action}'][0], 'meaning': meaning, 'label': label, 'esign': sid, 'hash': h}, user=u['username'])
    return {'id': sid, 'user': u['username'], 'name': u['name'], 'meaning': meaning, 'signed_at': db.jsonable(now), 'hash': h}


def signatures(c, entity, rid) -> list:
    from ..schema import TABLES
    t = TABLES['esign']
    return [{k: db.jsonable(v) for k, v in dict(r).items()} for r in c.execute(sa.select(t).where((t.c.entity == entity) & (t.c.entity_id == rid))
                                                                                 .order_by(t.c.id)).mappings()]


def install(app, router):
    @app.exception_handler(EsignFailed)
    async def _failed(request, exc: EsignFailed):
        # 业务事务已经回滚：单独记一条失败审计
        try:
            with db.engine.begin() as c:
                audit.audit(c, 'esign_fail', exc.entity, exc.rid, detail={'action': exc.action, 'msg': exc.msg}, user=exc.user)
        except Exception:      # noqa: BLE001
            pass
        return JSONResponse({'detail': exc.msg, 'code': 'esign_failed'}, status_code=403)

    @router.get('/api/esign/points')
    def points(user=Depends(auth.principal)):
        with db.engine.connect() as c:
            return [{'key': k, 'label': v[0], 'meaning': v[1], 'on': required(c, *k.split('.'))} for k, v in POINTS.items()]

    @router.put('/api/esign/points')
    def set_points(body: dict, user=Depends(perms.require('system.role.manage'))):
        off = [k for k, on in (body or {}).items() if k in POINTS and not on]
        with db.engine.begin() as c:
            db.set_setting(c, 'esign_off', ','.join(off), _tr('关闭电子签名的签名点（实体.动作）'))
            audit.audit(c, 'esign_points', detail={'off': off}, target='setting:esign_off')
        return {'ok': True, 'off': off}

    @router.get('/api/esign/records')
    def records(entity: str = '', entity_id: int = 0, q: str = '', limit: int = 200, user=Depends(perms.require(PERM_VIEW))):
        from ..schema import TABLES
        t = TABLES['esign']
        qq = sa.select(t)
        if entity:
            qq = qq.where(t.c.entity == entity)
        if entity_id:
            qq = qq.where(t.c.entity_id == entity_id)
        if q.strip():
            qq = qq.where(sa.or_(t.c.user.ilike(f'%{q.strip()}%'), t.c.record_label.ilike(f'%{q.strip()}%'), t.c.name.ilike(f'%{q.strip()}%')))
        with db.engine.connect() as c:
            rows = [dict(r) for r in c.execute(qq.order_by(t.c.id.desc()).limit(min(1000, limit))).mappings()]
        for r in rows:
            r['point'] = POINTS.get(f"{r['entity']}.{r['action']}", (r['action'],))[0]
        return [{k: db.jsonable(v) for k, v in r.items()} for r in rows]

    @router.post('/api/esign/verify')
    def verify(body: dict, user=Depends(auth.principal)):
        """只校验密码（前端签名框"确认"前预检，不落签名记录）。"""
        if user.get('is_api_key'):
            raise HTTPException(403, _tr('API 密钥不能签名'))
        with db.engine.connect() as c:
            u = db.get(c, 'sys_user', username=user['username'])
        return {'ok': bool(u and auth.check_pw(str(body.get('password') or ''), u['password_hash']))}

