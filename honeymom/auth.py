"""登录与权限：本地账号，PBKDF2 存密码，HMAC 签名令牌；API 密钥（apikeys.py）。权限点和数据范围在 perms.py。"""
from .i18n import _tr
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import HTTPException, Request

from . import context, db, perms
from .schema import BY_NAME

_secret = None


def secret():
    global _secret
    if _secret is None:
        p = os.path.join(db.DATA, 'secret.key')
        if not os.path.exists(p):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w') as f:
                f.write(secrets.token_hex(32))
        _secret = open(p).read().strip().encode()
    return _secret


def hash_pw(pw: str) -> str:
    salt = secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 120000).hex()
    return f'pbkdf2${salt}${h}'


def check_pw(pw: str, stored: str | None) -> bool:
    try:
        _, salt, h = (stored or '').split('$')
    except ValueError:
        return False
    return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 120000).hex(), h)


def make_token(user: dict, hours=24 * 7) -> str:
    body = base64.urlsafe_b64encode(json.dumps({'u': user['username'], 'exp': int(time.time() + hours * 3600)}).encode()).decode()
    sig = hmac.new(secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f'{body}.{sig}'


def read_token(tok: str) -> str | None:
    try:
        body, sig = tok.split('.')
        if not hmac.compare_digest(hmac.new(secret(), body.encode(), hashlib.sha256).hexdigest()[:32], sig):
            return None
        d = json.loads(base64.urlsafe_b64decode(body))
        return d['u'] if d['exp'] > time.time() else None
    except Exception:
        return None


def principal(request: Request) -> dict:
    """当前调用者：登录用户（令牌）或 API 密钥。新接口用它（或 perms.require(...)）；记入请求上下文供审计 / AI 工具使用。"""
    from . import apikeys
    key = apikeys.extract(request.headers)
    if key:
        u = apikeys.verify(key, context.ip() or (request.client.host if request.client else None))
        context.set_actor(u)
        return u
    tok = request.headers.get('authorization', '').removeprefix('Bearer ').strip() or request.query_params.get('token') or request.cookies.get('mes_token')
    name = read_token(tok) if tok else None
    if not name:
        raise HTTPException(401, _tr('请先登录'))
    u = user_by_name(name)
    if not u or not u['active']:
        raise HTTPException(401, _tr('账号已停用'))
    context.set_actor(u)
    return u


def current_user(request: Request) -> dict:
    """4.7 以来各业务接口用的依赖。比 principal 多一道页面级检查：GET 接口按 perms.API_PAGE_PERMS 的路径前缀要页面权限；
    API 密钥只能调已登记页面权限的接口（其余接口请改用 perms.require）。"""
    u = principal(request)
    path = request.url.path
    pp = perms.api_page_perm(path) if request.method == 'GET' else None
    if pp and not perms.can(u, pp):
        raise HTTPException(403, _tr('没有权限：{label_of}', label_of=perms.label_of(pp)))
    if u.get('is_api_key') and not pp and path not in ('/api/me', '/api/meta', '/api/clock'):
        raise HTTPException(403, _tr('API 密钥不能调用这个接口'))
    return u


def can_write(user: dict, entity: str, op: str | None = None) -> bool:
    """能否写某实体：op=create/update/delete/动作名；op=None 表示其中任一。"""
    if op:
        return perms.can(user, perms.entity_perm(entity, op))
    return any(perms.can(user, perms.entity_perm(entity, o)) for o in ('create', 'update', 'delete'))


def require_write(user, entity, op: str | None = None):
    if not can_write(user, entity, op):
        what = perms.label_of(perms.entity_perm(entity, op)) if op else _tr('{label}的修改', label=BY_NAME[entity].label)
        raise HTTPException(403, _tr('没有权限：{what}', what=what))


def can_edit_agents(user: dict) -> bool:
    """智能体：谁能新建/编辑/发布（权限点 ai.agent.edit；默认 管理员、厂长、工艺工程师）。"""
    return perms.can(user, 'ai.agent.edit')


def has_role(user: dict, role: str | None) -> bool:
    """审批人判断：角色匹配，或管理员/厂长（可代批）。"""
    roles = set(user.get('role_list') or [])
    return bool(roles & {'admin', 'manager'}) or (bool(role) and role in roles)


def is_admin(user: dict | None) -> bool:
    return bool(user) and 'admin' in (user.get('role_list') or [])


def user_by_name(username: str) -> dict | None:
    with db.engine.connect() as c:
        u = db.get(c, 'sys_user', username=username)
    if not u:
        return None
    u['role_list'] = [r.strip() for r in (u['roles'] or '').split(',') if r.strip()]
    u.pop('password_hash', None)
    return u
