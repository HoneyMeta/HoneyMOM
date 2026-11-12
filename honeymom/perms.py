"""权限：角色 → 权限点（页面 / 按钮动作 / 接口），加数据范围（工厂 / 车间 / 产线）。说明和例子见 docs/dev/foundation.md「权限」。

权限点是点分字符串：
  实体（通用增删改查和实体动作自动检查）：<模块键>.<实体>.<read|create|update|delete|动作名>
      如 quality.ncr.read、quality.ncr.update、quality.ncr.close、production.work_order.release
  页面（菜单 / 路由；规则见 page_perm()）：page.<路径用点连接>   如 /quality/spc → page.quality.spc，/ → page.home
  功能（自己声明）：<模块键>.<功能>.<动作>                      如 ai.agent.edit、system.role.manage、equipment.ts.write
角色里存"模式"列表：* 通配任意字符（'*'、'quality.*'、'*.read'、'page.*'），! 开头表示该角色里排除（'!system.role.manage'）。
用户可以 = 任一角色允许。敏感的系统权限不用 .read 结尾（用 .view / .manage），免得被 '*.read' 带上。

数据范围：{'plants': [id], 'workshops': [id], 'lines': [id]}；None / {} = 全部。用户自己设了就用用户的，否则取各角色的并集（有一个角色是全部就是全部）。
实体怎么归属到产线：bind_scope(实体, 字段, 目标)；没声明的按引用字段自动推（产线 > 车间 > 工厂 > 设备 > 工位）。归属字段为空的记录对所有人可见。
"""
from .i18n import _tr
import fnmatch
import re
import threading
import time

import sqlalchemy as sa
from fastapi import HTTPException, Request

from . import db

MODULE_KEYS = {'主数据': 'master', '生产管理': 'production', '质量管理': 'quality', '设备管理': 'equipment', '仓储物流': 'warehouse', '能源管理': 'energy',
               '铸造': 'foundry', '机加': 'machining', 'AI 助手': 'ai', '系统': 'system'}
from .schema_exec import MODULES as _EXEC_MODULES  # noqa: E402  M5 现场执行与质量合规：label pack outsource spare ecn doc gauge iatf complaint
MODULE_KEYS.update(_EXEC_MODULES)
from .schema_factory import MODULES as _FACTORY_MODULES  # noqa: E402  M2 工厂模型与接入
MODULE_KEYS.update(_FACTORY_MODULES)
MODULE_KEYS['实施'] = 'impl'  # M3 实施工具：实施任务 impl.impl_task.*（其余功能权限点 impl.* / config.* 在 honeymom/impl/ 里 declare）
MODULE_LABEL = {v: k for k, v in MODULE_KEYS.items()}
OPS = {'read': '查看', 'create': '新增', 'update': '修改', 'delete': '删除'}

# 功能权限点：id -> (中文名, 分组)。新功能用 declare() 加（模块导入时执行）。
FEATURES: dict[str, tuple] = {}


def declare(pid: str, label: str, group: str = '其他'):
    """声明一个功能权限点（出现在"角色与权限"页面里）。返回 pid，方便写 require(declare(...))。"""
    FEATURES[pid] = (label, group)
    return pid


declare('system.role.manage', '角色与权限、用户数据范围', '系统')
declare('system.audit.view', '查看审计日志', '系统')
declare('system.audit.export', '导出审计日志', '系统')
declare('system.apikey.manage', 'API 密钥管理', '系统')
declare('system.backup.view', '查看备份', '系统')
declare('system.backup.manage', '备份、恢复演练、删除备份', '系统')
declare('system.db.view', '数据库与迁移状态', '系统')
declare('system.ai.manage', 'AI 功能开关、演示录音', '系统')
declare('system.daily.schedule', '设置生产日报定时', '系统')
declare('ai.agent.edit', '新建/编辑/发布智能体', 'AI 助手')
declare('equipment.ts.read', '读设备时序数据', '设备管理')
declare('equipment.ts.write', '写入设备时序数据（网关 HTTP 推送）', '设备管理')

ALL_READ = ['page.*', '*.read']
ADMIN_ONLY = ['system.role.manage', 'system.apikey.manage', 'system.backup.manage', 'system.ai.manage']
# 默认角色 = 4.7 的行为：登录就能看全部，写按模块（auth.WRITE 原表）；管理员全部；厂长全部但不含纯管理员功能
DEFAULT_ROLES = {
    'admin': ('系统管理员', ['*']),
    'manager': ('厂长/经理', ['*'] + ['!' + p for p in ADMIN_ONLY]),
    'planner': ('计划员', ALL_READ + ['production.*', 'master.*']),
    'leader': ('班组长', ALL_READ + ['production.*', 'equipment.*']),
    'operator': ('操作工', ALL_READ + ['production.*', 'equipment.*']),
    'qc': ('质检员', ALL_READ + ['quality.*']),
    'maint': ('设备员', ALL_READ + ['equipment.*']),
    'warehouse': ('仓管员', ALL_READ + ['warehouse.*']),
    'engineer': ('工艺工程师', ALL_READ + ['master.*', 'machining.*', 'foundry.*', 'quality.*', 'ai.*']),
}
from .schema_exec import ROLE_GRANTS as _EXEC_GRANTS  # noqa: E402  M5：新模块的默认写权限
for _r, _ps in _EXEC_GRANTS.items():
    DEFAULT_ROLES[_r] = (DEFAULT_ROLES[_r][0], DEFAULT_ROLES[_r][1] + [p for p in _ps if p not in DEFAULT_ROLES[_r][1]])
from .schema_factory import ROLE_GRANTS as _FACTORY_GRANTS  # noqa: E402  M2：实施工程师 / 设备员可做工厂建模与接入
for _r, _ps in _FACTORY_GRANTS.items():
    DEFAULT_ROLES[_r] = (DEFAULT_ROLES[_r][0], DEFAULT_ROLES[_r][1] + [p for p in _ps if p not in DEFAULT_ROLES[_r][1]])
from .schema_erp import perms_extend as _erp_perms  # noqa: E402  M4 内置 ERP：sales purchase mrp cost finance erp 模块键 + 销售员 / 采购员 / 财务角色
_erp_perms(MODULE_KEYS, MODULE_LABEL, DEFAULT_ROLES)


def mkey(entity: str) -> str:
    from .schema import BY_NAME
    mod = BY_NAME[entity].module
    if mod not in MODULE_KEYS:
        raise KeyError(_tr('模块"{mod}"没有权限键：在 perms.MODULE_KEYS 里加一行', mod=mod))
    return MODULE_KEYS[mod]


def entity_perm(entity: str, op: str) -> str:
    """实体权限点：entity_perm('ncr', 'update') → 'quality.ncr.update'；op 也可以是实体动作名（close / release …）。"""
    return f'{mkey(entity)}.{entity}.{op}'


def page_perm(path: str) -> str:
    """页面路径 → 权限点（前端 web/src/perm.ts 用同一规则）：/e/<实体> → 实体 read；/ → page.home；其它 /a/b → page.a.b。"""
    p = (path or '/').split('?')[0].strip('/')
    if p.startswith('e/'):
        return entity_perm(p[2:].split('/')[0], 'read')
    return 'page.' + (p.replace('/', '.') if p else 'home')


def catalog() -> list[dict]:
    """全部权限点（页面除外：页面由前端菜单生成），给"角色与权限"页面用。"""
    from .crud import ACTIONS
    from .schema import ENTITIES
    out = []
    for e in ENTITIES:
        mk = MODULE_KEYS.get(e.module)
        if not mk:
            continue
        ops = ['read'] if e.internal else ['read', 'create', 'update', 'delete']
        acts = dict(e.actions)
        for (en, a) in ACTIONS:
            if en == e.name and a not in acts:
                acts[a] = a
        out.append({'entity': e.name, 'label': e.label, 'module': mk, 'module_label': _tr(e.module), 'module_name': e.module, 'internal': e.internal,
                    'perms': [{'id': f'{mk}.{e.name}.{o}', 'label': OPS[o], 'kind': 'api'} for o in ops]
                             + [{'id': f'{mk}.{e.name}.{a}', 'label': lab, 'kind': 'action'} for a, lab in acts.items()]})
    feats = [{'id': k, 'label': v[0], 'group': v[1], 'kind': 'feature'} for k, v in sorted(FEATURES.items())]
    return {'entities': out, 'features': feats, 'modules': [{'key': v, 'label': _tr(k), 'module_label': _tr(k), 'module_name': k} for k, v in MODULE_KEYS.items()]}


# ───────────────────────── 匹配 ─────────────────────────
_re_cache: dict = {}


def _compile(patterns) -> tuple:
    key = tuple(patterns or ())
    r = _re_cache.get(key)
    if r is None:
        pos = [fnmatch.translate(p) for p in key if p and not p.startswith('!')]
        neg = [fnmatch.translate(p[1:]) for p in key if p and p.startswith('!')]
        r = (re.compile('|'.join(pos)) if pos else None, re.compile('|'.join(neg)) if neg else None)
        if len(_re_cache) > 2000:
            _re_cache.clear()
        _re_cache[key] = r
    return r


def grants(patterns, perm: str) -> bool:
    """一个角色（模式列表）是否允许 perm。"""
    pos, neg = _compile(patterns)
    return bool(pos and pos.match(perm)) and not (neg and neg.match(perm))


# ───────────────────────── 角色（库里的 sys_role，带缓存） ─────────────────────────
_roles: dict = {}
_roles_at = 0.0
_roles_lock = threading.Lock()
ROLE_TTL = 5.0          # 多进程部署时，别的进程改了角色，最多 5 秒后生效


def roles(refresh=False) -> dict:
    """{code: {code, name, perms, scope, builtin}}；库里没有（老库迁移前）时退回 DEFAULT_ROLES。"""
    global _roles, _roles_at
    if not refresh and _roles and time.time() - _roles_at < ROLE_TTL:
        return _roles
    with _roles_lock:
        from .schema import role_t
        try:
            with db.engine.connect() as c:
                rows = {r['code']: dict(r) for r in c.execute(sa.select(role_t)).mappings()}
        except Exception:
            rows = {}
        if not rows:
            rows = {k: {'code': k, 'name': v[0], 'perms': v[1], 'scope': None, 'builtin': True} for k, v in DEFAULT_ROLES.items()}
        _roles, _roles_at = rows, time.time()
        return rows


def invalidate():
    global _roles_at
    _roles_at = 0.0
    _scope_cache.clear()


def user_patterns(user: dict) -> list[list[str]]:
    """用户的各角色模式列表（API 密钥：自己的 perms 作为一个"角色"，再加它挂的角色）。"""
    if user.get('_patterns') is not None:
        return user['_patterns']
    rs = roles()
    out = [rs[r]['perms'] or [] for r in user.get('role_list') or [] if r in rs]
    if user.get('perms_extra'):
        out.append(user['perms_extra'])
    user['_patterns'] = out
    return out


def can(user: dict | None, perm: str) -> bool:
    """user 有没有权限点 perm。user=None 表示系统内部（采集、定时任务）→ 允许。"""
    if user is None:
        return True
    return any(grants(p, perm) for p in user_patterns(user))


def check(user, perm: str, msg: str | None = None):
    if not can(user, perm):
        raise HTTPException(403, msg or _tr('没有权限：{label_of}', label_of=label_of(perm)))


def label_of(perm: str) -> str:
    if perm in FEATURES:
        return FEATURES[perm][0]
    parts = perm.split('.')
    if parts[0] == 'page':
        return _tr('页面 /') + '/'.join(parts[1:])
    if len(parts) == 3:
        from .schema import BY_NAME
        e = BY_NAME.get(parts[1])
        if e:
            act = OPS.get(parts[2]) or e.actions.get(parts[2]) or parts[2]
            return f'{e.label}·{act}'
    return perm


def require(*perms: str, any_of=False):
    """FastAPI 依赖：user=Depends(require('quality.spc.view'))。登录用户或 API 密钥都可以；没有权限 403。"""
    def dep(request: Request):
        from .auth import principal
        user = principal(request)
        ok = any(can(user, p) for p in perms) if any_of else all(can(user, p) for p in perms)
        if not ok:
            miss = [p for p in perms if not can(user, p)]
            raise HTTPException(403, _tr('没有权限：') + _tr('、').join(label_of(p) for p in miss))
        return user
    return dep


def effective_perms(user: dict) -> list:
    """给前端：[[模式…], …]（每个角色一组）；前端 can() 用同样的规则匹配。"""
    return user_patterns(user)


# ───────────────────────── 老接口的页面级检查（auth.current_user 里调用） ─────────────────────────
# GET 请求路径前缀 → 页面权限点（最长前缀优先）。新页面的接口请直接用 require()；这里只为 4.7 已有接口兜底，
# 默认角色都有 page.*，所以不改变现有行为；自定义角色去掉某页面后，对应接口也读不到。
API_PAGE_PERMS = {
    '/api/home/screen': 'page.screen', '/api/home/': 'page.home', '/api/dash/': 'page.home',
    '/api/m/': 'page.m', '/api/station/': 'page.station',
    '/api/production/gantt': 'page.production.gantt', '/api/production/constraints': 'page.production.gantt',
    '/api/reports/': 'production.report.read', '/api/labor/daily': 'page.labor.daily', '/api/labor/hours': 'page.labor.hours',
    '/api/quality/spc': 'page.quality.spc', '/api/quality/summary': 'page.quality', '/api/quality/fai': 'quality.first_article.read',
    '/api/quality/ncr': 'quality.ncr.read',
    '/api/equipment/monitor': 'page.equipment.monitor', '/api/equipment/tags': 'page.equipment.monitor', '/api/equipment/samples': 'page.equipment.monitor',
    '/api/equipment/oee': 'page.equipment.oee', '/api/equipment/maint_due': 'page.equipment.maint', '/api/equipment/alarm_stats': 'equipment.alarm.read',
    '/api/warehouse/overview': 'page.warehouse', '/api/energy/': 'page.energy',
    '/api/foundry/heats': 'page.foundry.heats', '/api/machining/tools': 'page.machining.tools', '/api/machining/defects': 'page.machining.defects',
    '/api/cross/backtrace': 'page.cross.backtrace', '/api/cross/coplan': 'page.cross.coplan',
    '/api/trace/recon': 'page.trace.recon', '/api/trace/': 'page.trace',
    '/api/report/daily': 'page.ai.daily', '/api/kb/': 'ai.kb_doc.read', '/api/andon/board': 'page.andon',
    '/api/agents': 'page.agents', '/api/approvals': 'page.approvals', '/api/ai/watch': 'page.ai.watch', '/api/ai/automations': 'page.ai.automations',
}
_api_prefixes = sorted(API_PAGE_PERMS, key=len, reverse=True)


def api_page_perm(path: str) -> str | None:
    for p in _api_prefixes:
        if path.startswith(p):
            return API_PAGE_PERMS[p]
    return None


# ───────────────────────── 数据范围 ─────────────────────────
SCOPE_BIND: dict[str, tuple] = {'plant': ('id', 'plant'), 'workshop': ('id', 'workshop'), 'line': ('id', 'line')}
_AUTO_ORDER = ('line', 'workshop', 'plant', 'equipment', 'workstation')


def bind_scope(entity: str, column: str, target: str):
    """声明实体按哪个字段归属：target 是 'plant' / 'workshop' / 'line'，或另一个已归属的实体名（如 'equipment'）。
    例：bind_scope('work_order', 'line', 'line')；bind_scope('alarm', 'equipment', 'equipment')。"""
    SCOPE_BIND[entity] = (column, target)


def scope_binding(entity: str):
    if entity in SCOPE_BIND:
        return SCOPE_BIND[entity]
    from .schema import BY_NAME
    e = BY_NAME.get(entity)
    if not e:
        return None
    refs = {f.ref: f.name for f in e.fields if f.t == 'ref'}
    for tgt in _AUTO_ORDER:
        if tgt in refs and tgt != entity:
            return (refs[tgt], tgt)
    return None


def normalize_scope(s) -> dict | None:
    if not s or not isinstance(s, dict):
        return None
    out = {k: sorted({int(x) for x in (s.get(k) or []) if str(x).strip().lstrip('-').isdigit()}) for k in ('plants', 'workshops', 'lines')}
    return out if any(out.values()) else None


def user_scope(user: dict | None) -> dict | None:
    """用户生效的数据范围（None = 全部）。"""
    if user is None:
        return None
    if '_scope' in user:
        return user['_scope']
    s = normalize_scope(user.get('scope'))
    if s is None and not user.get('is_api_key'):
        rs = roles()
        parts = [rs[r].get('scope') for r in user.get('role_list') or [] if r in rs]
        if parts and all(normalize_scope(p) for p in parts):
            s = {k: sorted({x for p in parts for x in normalize_scope(p)[k]}) for k in ('plants', 'workshops', 'lines')}
        elif not parts:
            s = None
    user['_scope'] = s
    return s


_scope_cache: dict = {}


def scope_sets(scope: dict) -> dict:
    """数据范围 → 可见的 plant / workshop / line id 集合（向下包含，向上可见所属的车间和工厂）。"""
    key = repr(scope)
    hit = _scope_cache.get(key)
    if hit and time.time() - hit[0] < 10:
        return hit[1]
    from .schema import TABLES
    ws_t, ln_t = TABLES['workshop'], TABLES['line']
    with db.engine.connect() as c:
        ws_all = {r.id: r.plant for r in c.execute(sa.select(ws_t.c.id, ws_t.c.plant))}
        ln_all = {r.id: r.workshop for r in c.execute(sa.select(ln_t.c.id, ln_t.c.workshop))}
    plants = set(scope.get('plants') or [])
    wss = set(scope.get('workshops') or []) | {w for w, p in ws_all.items() if p in plants}
    lines = set(scope.get('lines') or []) | {ln for ln, w in ln_all.items() if w in wss}
    wss_vis = wss | {ln_all[ln] for ln in lines if ln_all.get(ln)}
    plants_vis = plants | {ws_all[w] for w in wss_vis if ws_all.get(w)}
    out = {'plant': plants_vis, 'workshop': wss_vis, 'line': lines}
    _scope_cache[key] = (time.time(), out)
    return out


def scope_cond(user: dict | None, entity: str, table: sa.Table | None = None, _depth=0):
    """数据范围过滤条件（SQLAlchemy 表达式）；None = 不过滤。列表 / 查询接口：q = q.where(cond)。"""
    s = user_scope(user)
    if s is None:
        return None
    b = scope_binding(entity)
    if not b or _depth > 4:
        return None
    from .schema import TABLES
    t = table if table is not None else TABLES[entity]
    col, target = b
    if col not in t.c:
        return None
    if target in ('plant', 'workshop', 'line'):
        ids = scope_sets(s)[target]
        if col == 'id':
            return t.c.id.in_(ids) if ids else sa.false()
        return sa.or_(t.c[col].is_(None), t.c[col].in_(ids)) if ids else t.c[col].is_(None)
    tt = TABLES[target]
    sub = scope_cond(user, target, tt, _depth + 1)
    if sub is None:
        return None
    return sa.or_(t.c[col].is_(None), t.c[col].in_(sa.select(tt.c.id).where(sub)))


def in_scope(user, entity: str, row: dict) -> bool:
    """一条记录（含新建前的值）是否在用户范围内。"""
    s = user_scope(user)
    if s is None:
        return True
    b = scope_binding(entity)
    if not b:
        return True
    col, target = b
    v = row.get(col)
    if col == 'id' and v is None:          # 新建工厂 / 车间 / 产线：看它挂在哪个上级下面
        parent = {'line': ('workshop', 'workshop'), 'workshop': ('plant', 'plant')}.get(entity)
        if not parent or row.get(parent[0]) in (None, ''):
            return False
        col, target, v = parent[0], parent[1], row.get(parent[0])
    if v is None or v == '':
        return True
    if target in ('plant', 'workshop', 'line'):
        return int(v) in scope_sets(s)[target]
    from .schema import TABLES
    tt = TABLES[target]
    cond = scope_cond(user, target, tt)
    if cond is None:
        return True
    with db.engine.connect() as c:
        return c.execute(sa.select(tt.c.id).where((tt.c.id == int(v)) & cond)).first() is not None


def allowed_ids(user, entity: str) -> set | None:
    """范围内的记录 id 集合（None = 不限）。给缓存了全量结果的接口做事后过滤：rows = [r for r in rows if ids is None or r['id'] in ids]。"""
    from .schema import TABLES
    t = TABLES[entity]
    cond = scope_cond(user, entity, t)
    if cond is None:
        return None
    with db.engine.connect() as c:
        return set(c.execute(sa.select(t.c.id).where(cond)).scalars())


def scope_label(scope) -> str:
    s = normalize_scope(scope)
    if not s:
        return _tr('全部')
    from .schema import TABLES
    names = []
    with db.engine.connect() as c:
        for k, ent in (('plants', 'plant'), ('workshops', 'workshop'), ('lines', 'line')):
            if s[k]:
                t = TABLES[ent]
                names += [r.name for r in c.execute(sa.select(t.c.name).where(t.c.id.in_(s[k])))]
    return _tr('、').join(names) or _tr('（空）')
