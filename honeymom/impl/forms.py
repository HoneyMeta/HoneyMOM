"""表单配置：每个实体一份（form_layout 表），"实施 → 表单配置"里改（权限 config.form.manage）。说明见 docs/dev/config.md「表单配置」。

    {"groups": [{"title": "基本信息", "fields": ["code", "name", "x_color"]}, {"title": "计划", "fields": ["qty", "due"]}],
     "required": {"customer": ["*"], "remark": ["planner"]},      # 字段 → 哪些角色必填（* = 所有人）
     "hidden":   {"price": ["operator", "qc"]},                   # 字段 → 哪些角色看不到（表单 / 详情里不显示）
     "readonly": {"priority": ["operator"]}}                      # 字段 → 哪些角色只读
- 前端：/api/meta 的实体说明里带 form（EntityPage 的新增 / 编辑表单按它分组、排序、必填、隐藏、只读）。
- 后端：新增 / 修改时检查"按角色必填"（和自定义字段的必填），只读字段对这些角色忽略改动；隐藏只影响界面（接口照常返回，权限请用角色权限点和数据范围）。
"""
from ..i18n import _tr
import threading
import time

import sqlalchemy as sa
from fastapi import HTTPException

from .. import audit, db
from ..schema import BY_NAME

_cache = {'at': 0.0, 'by': {}}
_lock = threading.Lock()


def T():
    from ..schema_impl import T as _T
    return _T['form_layout']


def invalidate():
    _cache['at'] = 0.0


def all_layouts() -> dict:
    with _lock:
        if time.time() - _cache['at'] > 5:
            try:
                with db.engine.connect() as c:
                    _cache['by'] = {r['entity']: r['config'] or {} for r in c.execute(sa.select(T())).mappings()}
            except Exception:
                _cache['by'] = {}
            _cache['at'] = time.time()
        return _cache['by']


def get(entity) -> dict | None:
    return all_layouts().get(entity)


def field_names(entity) -> dict:
    from . import cf
    out = {f.name: f.label for f in BY_NAME[entity].fields}
    out.update({d['key']: d['label'] for d in cf.fields(entity)})
    return out


def validate(entity, cfg: dict) -> dict:
    if entity not in BY_NAME:
        raise ValueError(_tr('没有实体 {entity}', entity=entity))
    names = field_names(entity)
    out = {'groups': [], 'required': {}, 'hidden': {}, 'readonly': {}}
    seen = set()
    for g in cfg.get('groups') or []:
        fs = [f for f in (g.get('fields') or []) if f in names and f not in seen]
        seen |= set(fs)
        if fs or g.get('title'):
            out['groups'].append({'title': str(g.get('title') or '')[:40], 'fields': fs})
    from ..perms import roles
    known = set(roles()) | {'*'}
    for k in ('required', 'hidden', 'readonly'):
        for f, rs in (cfg.get(k) or {}).items():
            if f not in names:
                raise ValueError(_tr('没有字段 {f}', f=f))
            rs = [r for r in (rs if isinstance(rs, list) else [rs]) if r in known]
            if rs:
                out[k][f] = rs
    for f, rs in out['hidden'].items():
        if f in out['required'] and set(rs) & set(out['required'][f]):
            raise ValueError(_tr('{v}：同一个角色不能既必填又隐藏', v=names[f]))
    return out


def save(entity, cfg, user):
    clean = validate(entity, cfg)
    t = T()
    with db.engine.begin() as c:
        cur = c.execute(sa.select(t).where(t.c.entity == entity)).mappings().first()
        now = db.clock_wall()
        if cur:
            c.execute(t.update().where(t.c.id == cur['id']).values(config=clean, updated_by=user['username'], updated_at=now))
        else:
            c.execute(t.insert().values(entity=entity, config=clean, updated_by=user['username'], updated_at=now))
        audit.audit(c, 'form_layout', 'form_layout', cur['id'] if cur else None, detail={'entity': entity, 'before': (cur or {}).get('config'), 'after': clean},
                    target=f'form:{entity}', user=user['username'])
    invalidate()
    return clean


def reset(entity, user):
    t = T()
    with db.engine.begin() as c:
        c.execute(t.delete().where(t.c.entity == entity))
        audit.audit(c, 'form_layout_reset', 'form_layout', None, target=f'form:{entity}', user=user['username'])
    invalidate()


def _hit(rs, user) -> bool:
    return '*' in rs or bool(set(rs) & set((user or {}).get('role_list') or []))


def guard(entity, op, body, user, row):
    """crud.GUARDS：按角色必填 + 自定义字段必填；按角色只读的字段忽略改动。"""
    if op not in ('create', 'update') or user is None:
        return None
    from . import cf
    lay = get(entity) or {}
    labels = None
    miss = []
    for f, rs in (lay.get('required') or {}).items():
        if not _hit(rs, user):
            continue
        if op == 'create' and (body or {}).get(f) in (None, ''):
            miss.append(f)
        elif op == 'update' and f in (body or {}) and body.get(f) in (None, ''):
            miss.append(f)
    if op == 'create':
        for d in cf.fields(entity):
            if d.get('required') and (body or {}).get(d['key']) in (None, '') and d['key'] not in miss:
                miss.append(d['key'])
    if miss:
        labels = field_names(entity)
        raise HTTPException(400, _tr('请填写') + _tr('、').join(labels.get(f, f) for f in miss))
    if op == 'update' and body:
        for f, rs in (lay.get('readonly') or {}).items():
            if _hit(rs, user) and f in body and row is not None and str(body.get(f) or '') != str(row.get(f) or ''):
                body.pop(f, None)          # 这个角色只读：忽略改动
    return None


def _spec(out):
    lays = all_layouts()
    for e in out:
        if e['name'] in lays:
            e['form'] = lays[e['name']]


def install():
    from .. import crud
    from .. import schema as sch
    if guard not in crud.GUARDS:
        crud.GUARDS.insert(0, guard)
    if _spec not in sch.SPEC_HOOKS:
        sch.SPEC_HOOKS.append(_spec)
