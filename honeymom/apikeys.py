"""对外 API 密钥：给 MCP 服务、ERP 对接、网关 HTTP 推送用。说明见 docs/dev/foundation.md「API 密钥」。

- 管理员在"系统 → API 密钥"新建：名称、权限（挂角色 和/或 权限点模式）、数据范围、到期时间、IP 白名单。
- 明文只在创建时返回一次（hmk_<前缀>_<密文>）；库里只存 SHA-256 和前缀。可吊销；记录最后使用时间和 IP。
- 调用：请求头 X-API-Key: hmk_…   或   Authorization: Bearer hmk_…
- 鉴权后得到一个"用户"字典（is_api_key=True），权限检查、数据范围、审计和登录用户完全一样走 perms / audit。
"""
from .i18n import _tr
import hashlib
import hmac
import ipaddress
import secrets
import threading
import time
from datetime import datetime

import sqlalchemy as sa
from fastapi import HTTPException

from . import db
from .schema import api_key_t

PREFIX = 'hmk_'
_cache: dict = {}            # 前缀 -> (行, 读入时间)
_cache_lock = threading.Lock()
_touch: dict = {}            # id -> 上次写 last_used 的时间
CACHE_S = 10                 # 吊销 / 修改在其它进程最多 10 秒后生效（本进程立即生效）


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create(conn, name: str, roles: str = '', perms: list | None = None, scope: dict | None = None, expires_at=None, ip_allow: str = '',
           remark: str = '', created_by: str = '') -> tuple[int, str]:
    """新建密钥，返回 (id, 明文)。明文只此一次。"""
    if not (name or '').strip():
        raise ValueError(_tr('请填写名称'))
    check_ip_list(ip_allow)
    pre = secrets.token_hex(4)
    while conn.execute(sa.select(api_key_t.c.id).where(api_key_t.c.prefix == pre)).first():
        pre = secrets.token_hex(4)
    key = f'{PREFIX}{pre}_{secrets.token_urlsafe(30)}'
    from .perms import normalize_scope
    rid = conn.execute(api_key_t.insert().values(
        name=name.strip()[:100], prefix=pre, key_hash=_hash(key), roles=','.join(r.strip() for r in (roles or '').split(',') if r.strip()),
        perms=[p.strip() for p in (perms or []) if p and p.strip()], scope=normalize_scope(scope), ip_allow=(ip_allow or '').strip(),
        expires_at=db.parse_dt(expires_at), remark=(remark or '')[:300], created_by=created_by, created_at=datetime.now().replace(microsecond=0),
        use_count=0)).inserted_primary_key[0]
    return rid, key


def revoke(conn, kid: int, by: str):
    conn.execute(api_key_t.update().where(api_key_t.c.id == kid).values(revoked_at=datetime.now().replace(microsecond=0), revoked_by=by))
    _drop(kid)


def update(conn, kid: int, vals: dict):
    from .perms import normalize_scope
    v = {}
    for k in ('name', 'roles', 'remark', 'ip_allow'):
        if k in vals:
            v[k] = (vals[k] or '').strip()
    if 'ip_allow' in v:
        check_ip_list(v['ip_allow'])
    if 'perms' in vals:
        v['perms'] = [p.strip() for p in (vals['perms'] or []) if p and p.strip()]
    if 'scope' in vals:
        v['scope'] = normalize_scope(vals['scope'])
    if 'expires_at' in vals:
        v['expires_at'] = db.parse_dt(vals['expires_at'])
    if v:
        conn.execute(api_key_t.update().where(api_key_t.c.id == kid).values(**v))
    _drop(kid)


def _drop(kid):
    with _cache_lock:
        for k in [k for k, (r, _) in _cache.items() if r and r['id'] == kid]:
            _cache.pop(k, None)


def check_ip_list(s: str):
    for item in _ip_items(s):
        try:
            ipaddress.ip_network(item, strict=False)
        except ValueError:
            raise ValueError(_tr('IP 白名单格式不对：{item}（写 IP 或网段，如 10.0.0.5、192.168.1.0/24）', item=item))


def _ip_items(s):
    return [x for x in (s or '').replace(',', ' ').replace(';', ' ').split() if x]


def ip_allowed(allow: str, ip: str | None) -> bool:
    items = _ip_items(allow)
    if not items:
        return True
    if not ip:
        return False
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in ipaddress.ip_network(x, strict=False) for x in items)


def extract(headers) -> str | None:
    """从请求头取密钥。"""
    k = headers.get('x-api-key')
    if k:
        return k.strip()
    auth = headers.get('authorization', '')
    for p in ('Bearer ', 'ApiKey '):
        if auth.startswith(p) and auth[len(p):].strip().startswith(PREFIX):
            return auth[len(p):].strip()
    return None


def _row(prefix):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(prefix)
        if hit and now - hit[1] < CACHE_S:
            return hit[0]
    with db.engine.connect() as c:
        r = c.execute(sa.select(api_key_t).where(api_key_t.c.prefix == prefix)).mappings().first()
    r = dict(r) if r else None
    with _cache_lock:
        _cache[prefix] = (r, now)
    return r


def verify(key: str, ip: str | None) -> dict:
    """校验密钥 → 主体字典；失败抛 401 / 403。"""
    try:
        _, pre, _ = key.split('_', 2)
    except ValueError:
        raise HTTPException(401, _tr('API 密钥格式不对'))
    r = _row(pre)
    if not r or not hmac.compare_digest(r['key_hash'] or '', _hash(key)):
        raise HTTPException(401, _tr('API 密钥无效'))
    if r['revoked_at']:
        raise HTTPException(401, _tr('API 密钥已吊销'))
    if r['expires_at'] and r['expires_at'] < datetime.now():
        raise HTTPException(401, _tr('API 密钥已过期'))
    if not ip_allowed(r['ip_allow'], ip):
        raise HTTPException(403, _tr('API 密钥不允许从 {ip} 调用（IP 白名单）', ip=ip))
    _mark_used(r['id'], ip)
    return {'id': None, 'username': f"apikey:{r['name']}", 'name': _tr('API 密钥 {name}', name=r['name']), 'roles': r['roles'] or '',
            'role_list': [x for x in (r['roles'] or '').split(',') if x], 'perms_extra': list(r['perms'] or []), 'scope': r['scope'],
            'is_api_key': True, 'api_key_id': r['id'], 'active': True, 'employee': None}


def _mark_used(kid, ip):
    now = time.time()
    if now - _touch.get(kid, 0) < 30:
        return
    _touch[kid] = now
    try:
        with db.engine.begin() as c:
            c.execute(api_key_t.update().where(api_key_t.c.id == kid).values(
                last_used_at=datetime.now().replace(microsecond=0), last_used_ip=ip, use_count=sa.func.coalesce(api_key_t.c.use_count, 0) + 1))
    except Exception:
        pass


def public(r: dict) -> dict:
    from .perms import scope_label
    now = datetime.now()
    st = 'revoked' if r['revoked_at'] else 'expired' if r['expires_at'] and r['expires_at'] < now else 'active'
    return {'id': r['id'], 'name': r['name'], 'key_hint': f"{PREFIX}{r['prefix']}_…", 'roles': r['roles'] or '', 'perms': r['perms'] or [],
            'scope': r['scope'], 'scope_label': scope_label(r['scope']), 'ip_allow': r['ip_allow'] or '', 'expires_at': db.jsonable(r['expires_at']),
            'revoked_at': db.jsonable(r['revoked_at']), 'revoked_by': r['revoked_by'], 'last_used_at': db.jsonable(r['last_used_at']),
            'last_used_ip': r['last_used_ip'], 'use_count': r['use_count'] or 0, 'remark': r['remark'] or '', 'created_by': r['created_by'],
            'created_at': db.jsonable(r['created_at']), 'status': st}
