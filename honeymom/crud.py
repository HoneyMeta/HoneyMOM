"""通用增删改查：/api/e/<实体>，以及实体动作 /api/e/<实体>/<id>/do/<动作>。

5.0：每个接口自动检查权限点（<模块>.<实体>.read/create/update/delete/<动作>，见 perms.py）和数据范围（列表只出范围内的，
单条读写范围外的 404/403）；写入自动记审计（db.insert/update/delete 记改前改后，实体动作另记一条带参数和改动的 action）。
登录用户和 API 密钥都能调。"""
from .i18n import _tr
import csv
import io

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from . import audit, context, db, perms
from .auth import principal
from .schema import BY_NAME, TABLES

router = APIRouter()
ACTIONS: dict = {}          # (实体, 动作) -> fn(conn, row, body, user) -> dict|None
HOOKS: dict = {}            # (实体, 'after_save') -> fn(conn, id, user)
# M3 可配置（honeymom/impl，见 docs/dev/config.md）：自定义字段的筛选 / 排序 / 搜索 / 导出列 / 引用标签；写入前的守卫（必填、审批流程）
EXT_FILTER = None           # fn(实体, 表, 字段, 'eq'|'ge'|'lt', 值) -> 条件 | None
EXT_SORT = None             # fn(实体, 表, 字段) -> 排序表达式 | None
EXT_SEARCH = None           # fn(实体, 表, 关键词) -> [条件]
EXT_COLS = None             # fn(实体) -> [(键, 中文名, 取值函数(行))]
LABEL_HOOKS: list = []      # fn(conn, 实体, 行列表)：补 <字段>__label
GUARDS: list = []           # fn(实体, 操作, 请求体, 用户, 当前行|None) -> None 继续 | dict 直接作为响应返回（如"已提交审批"）；可抛 HTTPException
# 命名视图：(实体, 视图名) -> (中文名, fn(table) -> SQL 条件)。列表接口 ?view=<名> 使用；首页计数等也调同一个函数，口径一致
VIEWS: dict = {}


def view(entity, name, label):
    def deco(fn):
        VIEWS[(entity, name)] = (label, fn)
        return fn
    return deco


def action(entity, name):
    def deco(fn):
        ACTIONS[(entity, name)] = fn
        return fn
    return deco


def label_of(r: dict) -> str:
    for a, b in (('code', 'name'), ('sn', None), ('username', 'name'), ('title', None), ('name', None), ('code', None), ('ladle', None)):
        if r.get(a):
            if b and r.get(b) and str(r[b]).startswith(f'{r[a]} '):      # 名称里已带编号（AGV / "AGV 车队"）：不重复成 "AGV AGV 车队"
                return str(r[b])
            return f"{r[a]} {r[b]}" if b and r.get(b) and r.get(b) != r.get(a) else str(r[a])
    return f"#{r['id']}"


def attach_labels(conn, entity: str, items: list[dict]):
    e = BY_NAME[entity]
    for f in e.fields:
        if f.t != 'ref':
            continue
        ids = {r[f.name] for r in items if r.get(f.name)}
        if not ids:
            continue
        t = TABLES[f.ref]
        lab = {r['id']: label_of(dict(r)) for r in conn.execute(sa.select(t).where(t.c.id.in_(ids))).mappings()}
        for r in items:
            if r.get(f.name):
                r[f.name + '__label'] = lab.get(r[f.name], f'#{r[f.name]}')
    for h in LABEL_HOOKS:
        h(conn, entity, items)
    return items


def out(items):
    return [{k: db.jsonable(v) for k, v in r.items()} for r in items]


def _ent(entity):
    if entity not in BY_NAME:
        raise HTTPException(404, _tr('没有实体 {entity}', entity=entity))
    return BY_NAME[entity], TABLES[entity]


def guard(entity, op, body, user, row=None):
    """写入前的守卫（GUARDS）：返回 dict 时调用方直接把它作为响应（如"已提交审批"），不执行写入。别的服务代用户写实体时也可以调。"""
    for g in GUARDS:
        r = g(entity, op, body, user, row)
        if r is not None:
            return r
    return None


def _need(user, entity, op):
    perms.check(user, perms.entity_perm(entity, op))


def _scoped(user, entity, row, write=False):
    """单条记录的数据范围检查：读不到 → 404（不暴露存在），写不了 → 403。"""
    if not perms.in_scope(user, entity, row):
        raise HTTPException(403 if write else 404, _tr('不在你的数据范围内') if write else _tr('记录不存在'))


def build_query(entity, params: dict, user=None):
    e, t = _ent(entity)
    q = sa.select(t)
    conds = []
    sc = perms.scope_cond(user, entity, t)
    if sc is not None:
        conds.append(sc)
    vname = (params.get('view') or '').strip()
    if vname:
        if (entity, vname) not in VIEWS:
            raise HTTPException(400, _tr('{label}没有视图 {vname}', label=e.label, vname=vname))
        conds.append(VIEWS[(entity, vname)][1](t))
    kw = (params.get('q') or '').strip()
    if kw:
        cols = [t.c[f.name] for f in e.fields if f.search] or [t.c[f.name] for f in e.fields if f.t == 'str'][:3]
        conds.append(sa.or_(*[c.ilike(f'%{kw}%') for c in cols], *(EXT_SEARCH(entity, t, kw) if EXT_SEARCH else [])))
    for k, v in params.items():
        if k.startswith('f_') and v not in ('', None):
            name = k[2:]
            op = 'eq'
            if name.endswith('__from'):
                name, op = name[:-6], 'ge'
            elif name.endswith('__to'):
                name, op = name[:-4], 'lt'
            if name.startswith('x_') and EXT_FILTER is not None:      # 自定义字段
                try:
                    xc = EXT_FILTER(entity, t, name, op, v)
                except (TypeError, ValueError):
                    raise HTTPException(400, _tr('筛选值不对：{v}', v=v))
                if xc is not None:
                    conds.append(xc)
                continue
            if name not in t.c:
                continue
            f = next((x for x in e.fields if x.name == name), None)
            val = db.coerce(entity, {name: v})[name] if f else v
            conds.append(t.c[name] == val if op == 'eq' else t.c[name] >= val if op == 'ge' else t.c[name] < val)
    for c in conds:
        q = q.where(c)
    sort = params.get('sort') or e.order
    desc = sort.startswith('-')
    col = t.c.get(sort.lstrip('-'), t.c.id)
    if sort.lstrip('-').startswith('x_') and EXT_SORT is not None:
        col = EXT_SORT(entity, t, sort.lstrip('-'))
        col = t.c.id if col is None else col
    if sort.lstrip('-').startswith('x_'):          # 自定义字段：空值排最后（PostgreSQL 降序默认空值在前）
        q = q.order_by(col.desc().nulls_last() if desc else col.asc().nulls_last(), t.c.id.desc() if desc else t.c.id.asc())
    else:
        q = q.order_by(col.desc() if desc else col.asc(), t.c.id.desc() if desc else t.c.id.asc())
    return q, conds, t


@router.get('/api/e/{entity}')
def list_(entity: str, request: Request, page: int = 1, size: int = 20, user=Depends(principal)):
    if entity.endswith('.csv') and entity[:-4] in BY_NAME:      # /api/e/<实体>.csv 会先匹配到这里（路由顺序）：转给导出
        return export(entity[:-4], request, user)
    _ent(entity)
    _need(user, entity, 'read')
    params = dict(request.query_params)
    q, conds, t = build_query(entity, params, user)
    with db.engine.connect() as c:
        cnt = sa.select(sa.func.count()).select_from(t)
        for x in conds:
            cnt = cnt.where(x)
        total = c.execute(cnt).scalar()
        size = max(1, min(size, 2000))
        items = [dict(r) for r in c.execute(q.limit(size).offset((page - 1) * size)).mappings()]
        attach_labels(c, entity, items)
    return {'total': total, 'items': out(items)}


@router.get('/api/e/{entity}.csv', response_class=PlainTextResponse)
def export(entity: str, request: Request, user=Depends(principal)):
    e, _ = _ent(entity)
    _need(user, entity, 'read')
    q, _, _ = build_query(entity, dict(request.query_params), user)
    with db.engine.connect() as c:
        items = attach_labels(c, entity, [dict(r) for r in c.execute(q.limit(50000)).mappings()])
    buf = io.StringIO()
    w = csv.writer(buf)
    xcols = EXT_COLS(entity) if EXT_COLS else []          # 自定义字段
    w.writerow([_tr(f.label) for f in e.fields] + [_tr(x[1]) for x in xcols])     # column headers in the UI language
    for r in items:
        w.writerow([r.get(f.name + '__label', db.jsonable(r.get(f.name))) if f.t == 'ref' else _tr((f.enum or {}).get(r.get(f.name), db.jsonable(r.get(f.name)))) if f.enum and r.get(f.name) in f.enum else db.jsonable(r.get(f.name))
                    for f in e.fields] + [db.jsonable(x[2](r)) for x in xcols])
    return PlainTextResponse('﻿' + buf.getvalue(), media_type='text/csv; charset=utf-8',
                             headers={'Content-Disposition': f'attachment; filename={entity}.csv'})


@router.get('/api/e/{entity}/{rid}')
def get_(entity: str, rid: int, user=Depends(principal)):
    _ent(entity)
    _need(user, entity, 'read')
    with db.engine.connect() as c:
        r = db.get(c, entity, rid)
        if not r:
            raise HTTPException(404, _tr('记录不存在'))
        _scoped(user, entity, r)
        attach_labels(c, entity, [r])
        kids = {}
        for ch in BY_NAME[entity].children:
            child, fk = ch[0], ch[1]
            val = r.get(ch[2]) if len(ch) > 2 else rid
            ct = TABLES[child]
            items = [dict(x) for x in c.execute(sa.select(ct).where(ct.c[fk] == val).order_by(*(
                [ct.c.seq] if 'seq' in ct.c else [ct.c.id.desc()])).limit(500)).mappings()]
            kids[child] = out(attach_labels(c, child, items))
    return {'item': out([r])[0], 'children': kids}


@router.post('/api/e/{entity}')
def create(entity: str, body: dict = Body(...), user=Depends(principal)):
    e, _ = _ent(entity)
    if e.internal:
        raise HTTPException(400, _tr('{label}由系统自动记录，不能手工新增', label=e.label))
    _need(user, entity, 'create')
    _scoped(user, entity, body, write=True)
    g = guard(entity, 'create', body, user)
    if g is not None:
        return g
    with db.engine.begin() as c:
        for f in e.fields:
            if f.req and body.get(f.name) in (None, '') and not f.auto:
                raise HTTPException(400, _tr('请填写{label}', label=f.label))
        try:
            rid = db.insert(c, entity, {k: v for k, v in body.items() if k not in ('id', 'created_at', 'updated_at')})
        except sa.exc.IntegrityError:
            raise HTTPException(400, _tr('编码重复'))
        except ValueError as ex:
            raise HTTPException(400, str(ex))
        h = HOOKS.get((entity, 'after_save'))
        if h:
            h(c, rid, user)
    return {'ok': True, 'id': rid}


@router.put('/api/e/{entity}/{rid}')
def update_(entity: str, rid: int, body: dict = Body(...), user=Depends(principal)):
    e, _ = _ent(entity)
    _need(user, entity, 'update')
    ro = {f.name for f in e.fields if f.ro}
    data = {k: v for k, v in body.items() if k not in ro and k not in ('id', 'created_at', 'updated_at') and not k.endswith('__label')}
    with db.engine.begin() as c:
        cur = db.get(c, entity, rid)
        if not cur:
            raise HTTPException(404, _tr('记录不存在'))
        _scoped(user, entity, cur, write=True)
        _scoped(user, entity, {**cur, **data}, write=True)
        g = guard(entity, 'update', data, user, cur)
        if g is not None:
            return g
        try:
            db.update(c, entity, rid, data)
        except sa.exc.IntegrityError:
            raise HTTPException(400, _tr('编码重复'))
        except ValueError as ex:
            raise HTTPException(400, str(ex))
        h = HOOKS.get((entity, 'after_save'))
        if h:
            h(c, rid, user)
    return {'ok': True}


@router.delete('/api/e/{entity}/{rid}')
def delete_(entity: str, rid: int, user=Depends(principal)):
    e, t = _ent(entity)
    _need(user, entity, 'delete')
    with db.engine.begin() as c:
        cur = db.get(c, entity, rid)
        if not cur:
            raise HTTPException(404, _tr('记录不存在'))
        _scoped(user, entity, cur, write=True)
        g = guard(entity, 'delete', {}, user, cur)
        if g is not None:
            return g
        # 被引用的不让删
        for other in BY_NAME.values():
            for f in other.fields:
                if f.t == 'ref' and f.ref == entity:
                    ot = TABLES[other.name]
                    n = c.execute(sa.select(sa.func.count()).select_from(ot).where(ot.c[f.name] == rid)).scalar()
                    if n:
                        raise HTTPException(400, _tr('还有 {n} 条{label}引用它，不能删除', n=n, label=other.label))
        db.delete(c, entity, rid)
    return {'ok': True}


@router.post('/api/e/{entity}/{rid}/do/{act}')
def do_action(entity: str, rid: int, act: str, body: dict = Body(default={}), user=Depends(principal)):
    fn = ACTIONS.get((entity, act))
    if not fn:
        raise HTTPException(404, _tr('没有这个操作'))
    _need(user, entity, act)
    with db.engine.begin() as c:
        r = db.get(c, entity, rid)
        if not r:
            raise HTTPException(404, _tr('记录不存在'))
        _scoped(user, entity, r, write=True)
        g = guard(entity, act, body or {}, user, r)
        if g is not None:
            return g
        res = run_action(c, entity, r, act, body or {}, user, fn)
    return {'ok': True, **(res or {})}


def run_action(c, entity, row, act, body, user, fn=None):
    """执行实体动作并审计（一条 action 记录：动作名、参数、这条记录的改动；动作里对同一条记录的 db.update 不再重复记）。
    别的服务要代用户执行实体动作时也调它。"""
    fn = fn or ACTIONS[(entity, act)]
    with context.audit_skip(entity, row['id']):
        res = fn(c, row, body, user)
    after = db.get(c, entity, row['id'])
    d = audit.diff_of(row, after) if after else {}
    if audit.auto_enabled() or user is not None:
        audit.audit(c, act, entity, row['id'], diff=d, detail={'action': BY_NAME[entity].actions.get(act, act), 'body': body or None,
                                                              'result': {k: v for k, v in (res or {}).items() if k in ('msg', 'id', 'code')} or None},
                    user=user)
    return res
