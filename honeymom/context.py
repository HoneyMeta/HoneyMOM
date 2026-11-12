"""请求上下文：谁（用户 / API 密钥 / 智能体）、从哪来（界面 / 接口 / AI / 智能体 / 导入 / 采集）、客户端 IP。

审计（audit.py）和 AI 工具（问数 SQL 的权限与数据范围）从这里取"当前是谁"，不用一层层传参数。
- HTTP 请求：app 的中间件（install）在请求开始时设好 ip 和 source（带 X-API-Key 的是 'api'，否则 'ui'）；
  current_user / principal 解析出用户后写入 actor。
- 后台代码：with acting(user, 'agent'): …   或   with source('import'): …
FastAPI 的同步接口在线程池里跑，contextvars 会复制到线程里，所以中间件设的值在接口函数里能读到。
"""
import contextvars
from contextlib import contextmanager

_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar('hm_ctx', default=None)

SOURCES = {'ui': '界面', 'api': '接口', 'ai': 'AI', 'agent': '智能体', 'import': '导入', 'collector': '采集', 'system': '系统', 'migration': '迁移'}


def get() -> dict:
    return _ctx.get() or {}


def actor() -> dict | None:
    return get().get('actor')


def current_source() -> str:
    return get().get('source') or 'system'


def ip() -> str | None:
    return get().get('ip')


def set_actor(user: dict | None):
    """记下当前请求的用户。原地改字典：依赖函数和接口函数在不同的线程池调用里跑，复制出的上下文引用的是同一个字典。"""
    d = _ctx.get()
    if d is None:
        _ctx.set({'actor': user})
    else:
        d['actor'] = user


@contextmanager
def acting(user: dict | None, src: str | None = None):
    d = dict(get())
    d['actor'] = user
    if src:
        d['source'] = src
    tok = _ctx.set(d)
    try:
        yield
    finally:
        _ctx.reset(tok)


@contextmanager
def source(src: str):
    d = dict(get())
    d['source'] = src
    tok = _ctx.set(d)
    try:
        yield
    finally:
        _ctx.reset(tok)


@contextmanager
def audit_skip(entity: str, rid):
    """这段代码里对 (entity, rid) 的 db.update 不单独审计（实体动作统一记一条 action，见 crud.run_action）。"""
    d = dict(get())
    d['audit_skip'] = (entity, rid)
    tok = _ctx.set(d)
    try:
        yield
    finally:
        _ctx.reset(tok)


def client_ip(scope) -> str | None:
    """X-Forwarded-For（经反向代理时）优先，其次直连地址。"""
    for k, v in scope.get('headers') or []:
        if k == b'x-forwarded-for':
            return v.decode('latin-1').split(',')[0].strip()[:60]
    c = scope.get('client')
    return c[0] if c else None


def install(app):
    """纯 ASGI 中间件：每个 HTTP 请求一个新的上下文。"""
    class _Mw:
        def __init__(self, app):
            self.inner = app

        async def __call__(self, scope, receive, send):
            if scope.get('type') != 'http':
                return await self.inner(scope, receive, send)
            hdrs = dict(scope.get('headers') or [])
            auth = hdrs.get(b'authorization', b'').decode('latin-1')
            is_key = b'x-api-key' in hdrs or auth.startswith('Bearer hmk_') or auth.startswith('ApiKey ')
            tok = _ctx.set({'ip': client_ip(scope), 'source': 'api' if is_key else 'ui', 'actor': None})
            try:
                return await self.inner(scope, receive, send)
            finally:
                _ctx.reset(tok)
    app.add_middleware(_Mw)
