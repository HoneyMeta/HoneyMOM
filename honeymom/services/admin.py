"""数据底座的管理接口：角色与权限、用户数据范围、审计日志、API 密钥、备份恢复、数据库状态；设备时序与网关推送接口。
接口清单见 docs/dev/foundation.md。"""
from ..i18n import _tr
import csv
import io
import json
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

from .. import apikeys, audit, backup, context, db, perms, ts
from ..perms import require
from ..schema import TABLES, api_key_t, role_t

router = APIRouter()
CODE_OK = set('abcdefghijklmnopqrstuvwxyz0123456789_')


def _who(user):
    return user.get('username') or 'system'


# ───────────────────────── 角色与权限 ─────────────────────────
@router.get('/api/admin/perms/catalog')
def perm_catalog(user=Depends(require('system.role.manage'))):
    return perms.catalog()


def _role_out(r, counts):
    return {'code': r['code'], 'name': r['name'], 'perms': r['perms'] or [], 'scope': r['scope'], 'scope_label': perms.scope_label(r['scope']),
            'builtin': bool(r['builtin']), 'remark': r['remark'] or '', 'users': counts.get(r['code'], 0), 'updated_at': db.jsonable(r['updated_at'])}


def _role_counts(c):
    cnt = {}
    for (roles,) in c.execute(sa.select(TABLES['sys_user'].c.roles)).all():
        for r in (roles or '').split(','):
            if r.strip():
                cnt[r.strip()] = cnt.get(r.strip(), 0) + 1
    return cnt


@router.get('/api/admin/roles')
def roles_list(user=Depends(require('system.role.manage'))):
    with db.engine.connect() as c:
        cnt = _role_counts(c)
        rows = [dict(r) for r in c.execute(sa.select(role_t).order_by(role_t.c.builtin.desc(), role_t.c.id)).mappings()]
    return {'items': [_role_out(r, cnt) for r in rows], 'defaults': {k: v[1] for k, v in perms.DEFAULT_ROLES.items()}}


def _clean_perms(lst):
    out = []
    for p in lst or []:
        p = str(p).strip()
        if p and all(ch.isalnum() or ch in '._*!' for ch in p) and p not in out:
            out.append(p)
    return out


@router.post('/api/admin/roles')
def roles_create(body: dict = Body(...), user=Depends(require('system.role.manage'))):
    code = (body.get('code') or '').strip().lower()
    if not code or set(code) - CODE_OK or len(code) > 40:
        raise HTTPException(400, _tr('角色编码只能用小写字母、数字和下划线'))
    if not (body.get('name') or '').strip():
        raise HTTPException(400, _tr('请填写角色名称'))
    with db.engine.begin() as c:
        if c.execute(sa.select(role_t.c.id).where(role_t.c.code == code)).first():
            raise HTTPException(400, _tr('角色 {code} 已存在', code=code))
        now = db.clock_wall()
        vals = dict(code=code, name=body['name'].strip()[:80], perms=_clean_perms(body.get('perms')), scope=perms.normalize_scope(body.get('scope')),
                    builtin=False, remark=(body.get('remark') or '')[:200], created_at=now, updated_at=now)
        rid = c.execute(role_t.insert().values(**vals)).inserted_primary_key[0]
        audit.audit(c, 'create', 'sys_role', rid, diff=audit.diff_of(None, vals), target=f'role:{code}')
    perms.invalidate()
    return {'ok': True, 'code': code}


@router.put('/api/admin/roles/{code}')
def roles_update(code: str, body: dict = Body(...), user=Depends(require('system.role.manage'))):
    with db.engine.begin() as c:
        r = c.execute(sa.select(role_t).where(role_t.c.code == code)).mappings().first()
        if not r:
            raise HTTPException(404, _tr('没有这个角色'))
        vals = {}
        if 'name' in body:
            vals['name'] = (body['name'] or '').strip()[:80] or r['name']
        if 'perms' in body:
            vals['perms'] = _clean_perms(body['perms'])
            if code == 'admin' and '*' not in vals['perms']:
                raise HTTPException(400, _tr('系统管理员角色必须保留全部权限（*），否则可能没人能再管理权限'))
        if 'scope' in body:
            vals['scope'] = perms.normalize_scope(body['scope'])
        if 'remark' in body:
            vals['remark'] = (body['remark'] or '')[:200]
        vals['updated_at'] = db.clock_wall()
        c.execute(role_t.update().where(role_t.c.code == code).values(**vals))
        audit.audit(c, 'update', 'sys_role', r['id'], diff=audit.diff_of(dict(r), {**dict(r), **vals}, keys=set(vals)), target=f'role:{code}')
    perms.invalidate()
    return {'ok': True}


@router.post('/api/admin/roles/{code}/reset')
def roles_reset(code: str, user=Depends(require('system.role.manage'))):
    """内置角色恢复默认权限。"""
    if code not in perms.DEFAULT_ROLES:
        raise HTTPException(400, _tr('只有内置角色能恢复默认'))
    return roles_update(code, {'perms': perms.DEFAULT_ROLES[code][1], 'scope': None}, user)


@router.delete('/api/admin/roles/{code}')
def roles_delete(code: str, user=Depends(require('system.role.manage'))):
    with db.engine.begin() as c:
        r = c.execute(sa.select(role_t).where(role_t.c.code == code)).mappings().first()
        if not r:
            raise HTTPException(404, _tr('没有这个角色'))
        if r['builtin']:
            raise HTTPException(400, _tr('内置角色不能删除（可以改权限）'))
        n = _role_counts(c).get(code, 0)
        if n:
            raise HTTPException(400, _tr('还有 {n} 个用户用这个角色，先把他们换成别的角色', n=n))
        c.execute(role_t.delete().where(role_t.c.code == code))
        audit.audit(c, 'delete', 'sys_role', r['id'], diff=audit.diff_of(dict(r), None), target=f'role:{code}')
    perms.invalidate()
    return {'ok': True}


@router.get('/api/admin/users')
def users_list(user=Depends(require('system.role.manage'))):
    ut = TABLES['sys_user']
    with db.engine.connect() as c:
        rows = [dict(r) for r in c.execute(sa.select(ut.c.id, ut.c.username, ut.c.name, ut.c.roles, ut.c.active, ut.c.scope).order_by(ut.c.id)).mappings()]
    for r in rows:
        r['scope_label'] = perms.scope_label(r['scope'])
        r['role_list'] = [x for x in (r['roles'] or '').split(',') if x]
    return {'items': rows}


@router.put('/api/admin/users/{uid}')
def users_update(uid: int, body: dict = Body(...), user=Depends(require('system.role.manage'))):
    """改用户的角色和数据范围（数据范围为空 = 按角色）。"""
    vals = {}
    rs = perms.roles(refresh=True)
    if 'roles' in body:
        lst = body['roles'] if isinstance(body['roles'], list) else str(body['roles'] or '').split(',')
        lst = [x.strip() for x in lst if x and x.strip()]
        bad = [x for x in lst if x not in rs]
        if bad:
            raise HTTPException(400, _tr('没有这些角色：{join}', join=_tr('、').join(bad)))
        vals['roles'] = ','.join(lst)
    if 'scope' in body:
        vals['scope'] = perms.normalize_scope(body['scope'])
    with db.engine.begin() as c:
        u = db.get(c, 'sys_user', uid)
        if not u:
            raise HTTPException(404, _tr('没有这个用户'))
        if u['username'] == user.get('username') and 'roles' in vals and 'admin' in (u['roles'] or '') and 'admin' not in vals['roles']:
            raise HTTPException(400, _tr('不能去掉自己的管理员角色'))
        db.update(c, 'sys_user', uid, vals, raw=True)
    perms.invalidate()
    return {'ok': True}


@router.get('/api/admin/org')
def org_tree(user=Depends(require('system.role.manage', 'system.apikey.manage', any_of=True))):
    """工厂 → 车间 → 产线（数据范围选择用）。"""
    with db.engine.connect() as c:
        pl = db.rows(c, 'plant', order=TABLES['plant'].c.id)
        ws = db.rows(c, 'workshop', order=TABLES['workshop'].c.id)
        ln = db.rows(c, 'line', order=TABLES['line'].c.id)
    return {'plants': [{'id': p['id'], 'code': p['code'], 'name': p['name'],
                        'workshops': [{'id': w['id'], 'code': w['code'], 'name': w['name'],
                                       'lines': [{'id': x['id'], 'code': x['code'], 'name': x['name']} for x in ln if x['workshop'] == w['id']]}
                                      for w in ws if w['plant'] == p['id']]} for p in pl],
            'orphans': {'workshops': [{'id': w['id'], 'name': w['name']} for w in ws if not w['plant']],
                        'lines': [{'id': x['id'], 'name': x['name']} for x in ln if not x['workshop']]}}


# ───────────────────────── 审计 ─────────────────────────
def _audit_out(r):
    return {'id': r['id'], 'seq': r['seq'], 'time': db.jsonable(r['time']), 'user': r['user'], 'kind': r['kind'], 'action': r['action'] or r['kind'],
            'entity': r['entity'], 'entity_id': r['entity_id'], 'target': r['target'], 'diff': r['diff'], 'detail': r['detail'],
            'source': r['source'], 'ip': r['ip'], 'hash': r['hash']}


@router.get('/api/admin/audit')
def audit_list(request: Request, page: int = 1, size: int = 50, user=Depends(require('system.audit.view'))):
    f = dict(request.query_params)
    size = max(1, min(size, 500))
    total, rows = audit.query(f, limit=size, offset=(page - 1) * size)
    return {'total': total, 'items': [_audit_out(r) for r in rows], 'sources': context.SOURCES}


@router.get('/api/admin/audit/verify')
def audit_verify(user=Depends(require('system.audit.view'))):
    return audit.verify()


@router.get('/api/admin/audit/facets')
def audit_facets(user=Depends(require('system.audit.view'))):
    from ..schema import audit_t
    with db.engine.connect() as c:
        users = [r[0] for r in c.execute(sa.select(audit_t.c.user).distinct().limit(200)).all() if r[0]]
        ents = [r[0] for r in c.execute(sa.select(audit_t.c.entity).distinct().limit(200)).all() if r[0]]
        acts = [r[0] for r in c.execute(sa.select(audit_t.c.kind).distinct().limit(200)).all() if r[0]]
    from ..schema import BY_NAME
    return {'users': sorted(users), 'entities': [{'name': e, 'label': BY_NAME[e].label if e in BY_NAME else e} for e in sorted(ents)],
            'actions': sorted(acts), 'sources': context.SOURCES}


@router.get('/api/admin/audit.csv', response_class=PlainTextResponse)
def audit_export(request: Request, user=Depends(require('system.audit.export'))):
    total, rows = audit.query(dict(request.query_params), limit=100000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([_tr('序号'), _tr('时间'), _tr('用户'), _tr('动作'), _tr('对象'), _tr('对象ID'), _tr('目标'), _tr('来源'), 'IP', _tr('改动'), _tr('详情'), _tr('哈希')])
    for r in rows:
        w.writerow([r['seq'], db.jsonable(r['time']), r['user'], r['action'] or r['kind'], r['entity'] or '', r['entity_id'] or '', r['target'],
                    context.SOURCES.get(r['source'], r['source'] or ''), r['ip'] or '', json.dumps(r['diff'], ensure_ascii=False) if r['diff'] else '',
                    json.dumps(r['detail'], ensure_ascii=False) if r['detail'] else '', r['hash'] or ''])
    with db.engine.begin() as c:
        audit.audit(c, 'audit_export', None, None, detail={'rows': len(rows), 'filters': dict(request.query_params)}, target='audit_log')
    return PlainTextResponse('﻿' + buf.getvalue(), media_type='text/csv; charset=utf-8',
                             headers={'Content-Disposition': f'attachment; filename=audit-{datetime.now():%Y%m%d-%H%M}.csv'})


# ───────────────────────── API 密钥 ─────────────────────────
@router.get('/api/admin/apikeys')
def keys_list(user=Depends(require('system.apikey.manage'))):
    with db.engine.connect() as c:
        rows = [dict(r) for r in c.execute(sa.select(api_key_t).order_by(api_key_t.c.id.desc())).mappings()]
    return {'items': [apikeys.public(r) for r in rows], 'roles': [{'code': k, 'name': v['name']} for k, v in perms.roles().items()]}


@router.post('/api/admin/apikeys')
def keys_create(body: dict = Body(...), user=Depends(require('system.apikey.manage'))):
    try:
        with db.engine.begin() as c:
            kid, key = apikeys.create(c, body.get('name') or '', roles=','.join(body['roles']) if isinstance(body.get('roles'), list) else body.get('roles') or '',
                                      perms=_clean_perms(body.get('perms')), scope=body.get('scope'), expires_at=body.get('expires_at'),
                                      ip_allow=body.get('ip_allow') or '', remark=body.get('remark') or '', created_by=_who(user))
            audit.audit(c, 'apikey_create', 'api_key', kid, detail={'name': body.get('name'), 'roles': body.get('roles'), 'perms': body.get('perms'),
                                                                     'scope': body.get('scope'), 'expires_at': body.get('expires_at'), 'ip_allow': body.get('ip_allow')},
                        target=f'apikey:{kid}')
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {'ok': True, 'id': kid, 'key': key, 'msg': _tr('密钥只显示这一次，请立即复制保存')}


@router.put('/api/admin/apikeys/{kid}')
def keys_update(kid: int, body: dict = Body(...), user=Depends(require('system.apikey.manage'))):
    try:
        with db.engine.begin() as c:
            if not c.execute(sa.select(api_key_t.c.id).where(api_key_t.c.id == kid)).first():
                raise HTTPException(404, _tr('没有这个密钥'))
            if isinstance(body.get('roles'), list):
                body['roles'] = ','.join(body['roles'])
            apikeys.update(c, kid, body)
            audit.audit(c, 'apikey_update', 'api_key', kid, detail={k: body.get(k) for k in ('name', 'roles', 'perms', 'scope', 'expires_at', 'ip_allow') if k in body},
                        target=f'apikey:{kid}')
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {'ok': True}


@router.post('/api/admin/apikeys/{kid}/revoke')
def keys_revoke(kid: int, user=Depends(require('system.apikey.manage'))):
    with db.engine.begin() as c:
        apikeys.revoke(c, kid, _who(user))
        audit.audit(c, 'apikey_revoke', 'api_key', kid, target=f'apikey:{kid}')
    return {'ok': True}


@router.get('/api/whoami')
def whoami(request: Request):
    """API 密钥 / 令牌自检：返回调用者、权限模式和数据范围。"""
    from ..auth import principal
    u = principal(request)
    return {'username': u['username'], 'name': u.get('name'), 'is_api_key': bool(u.get('is_api_key')), 'roles': u.get('role_list'),
            'perms': perms.effective_perms(u), 'scope': perms.user_scope(u), 'scope_label': perms.scope_label(perms.user_scope(u))}


# ───────────────────────── 备份 ─────────────────────────
@router.get('/api/admin/backups')
def backups_list(user=Depends(require('system.backup.view'))):
    return {'items': backup.listing(), 'schedule': backup.schedule(), 'dialect': db.engine.dialect.name}


@router.post('/api/admin/backups')
def backups_create(body: dict = Body(default={}), user=Depends(require('system.backup.manage'))):
    try:
        return backup.create(kind='manual', note=(body or {}).get('note') or '', user=_who(user))
    except Exception as ex:
        raise HTTPException(500, _tr('备份失败：{ex}', ex=ex))


@router.post('/api/admin/backups/{rid}/drill')
def backups_drill(rid: int, user=Depends(require('system.backup.manage'))):
    try:
        return backup.drill(rid, user=_who(user))
    except KeyError as ex:
        raise HTTPException(404, str(ex))


@router.delete('/api/admin/backups/{rid}')
def backups_delete(rid: int, user=Depends(require('system.backup.manage'))):
    try:
        backup.delete(rid, user=_who(user))
    except KeyError as ex:
        raise HTTPException(404, str(ex))
    return {'ok': True}


@router.get('/api/admin/backups/{rid}/file')
def backups_download(rid: int, user=Depends(require('system.backup.manage'))):
    r = backup.get(rid)
    if not r or not r['exists']:
        raise HTTPException(404, _tr('备份文件不存在'))
    with db.engine.begin() as c:
        audit.audit(c, 'backup_download', None, None, detail={'file': r['file']}, target=f'backup:{rid}')
    return FileResponse(r['path'], filename=r['file'], media_type='application/zip')


@router.put('/api/admin/backups/schedule')
def backups_schedule(body: dict = Body(...), user=Depends(require('system.backup.manage'))):
    try:
        s = backup.set_schedule(body)
    except (ValueError, AssertionError):
        raise HTTPException(400, _tr('时间格式是 HH:MM'))
    with db.engine.begin() as c:
        audit.audit(c, 'backup_schedule', None, None, detail=body, target='backup:schedule')
    return s


# ───────────────────────── 数据库状态 ─────────────────────────
@router.get('/api/admin/db')
def db_status(user=Depends(require('system.db.view'))):
    from .. import migrations
    from ..ingest import GATE
    url = db.engine.url
    st = migrations.status(db.engine)
    with db.engine.connect() as c:
        size = None
        if c.dialect.name == 'postgresql':
            size = c.execute(sa.text('SELECT pg_database_size(current_database())')).scalar()
            ver = c.execute(sa.text('SHOW server_version')).scalar()
            tsv = c.execute(sa.text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")).scalar()
        else:
            import os
            p = url.database or ''
            size = os.path.getsize(p) if p and os.path.exists(p) else None
            ver = c.execute(sa.text('SELECT sqlite_version()')).scalar()
            tsv = None
        npts = c.execute(sa.select(sa.func.count()).select_from(db.metadata.tables['ts_point'])).scalar()
    return {'dialect': c.dialect.name, 'server_version': ver, 'timescaledb': tsv, 'url': url.render_as_string(hide_password=True), 'size': size,
            'migrations': st, 'drift': migrations.check(db.engine), 'ts_mode': ts.mode(), 'ts_points': npts, 'ts_stats': dict(ts.STATS),
            'ts_retention': ts.retention(), 'gate': GATE.status()}


# ───────────────────────── 设备时序（登录用户或 API 密钥） ─────────────────────────
def _eq_allowed(user, eid):
    """时序点挂在设备上：数据范围按设备。"""
    if eid is None or perms.user_scope(user) is None:
        return True
    with db.engine.connect() as c:
        e = db.get(c, 'equipment', eid)
    return perms.in_scope(user, 'equipment', e or {})


def _pt(user, point):
    try:
        info = ts.info(point)
    except KeyError as ex:
        raise HTTPException(404, str(ex))
    if not _eq_allowed(user, info.get('equipment')):
        raise HTTPException(404, _tr('没有这个采集点'))
    return info


def _call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except (KeyError, ValueError) as ex:
        raise HTTPException(400, str(ex))


@router.get('/api/ts/points')
def ts_points(equipment: int | None = None, q: str | None = None, user=Depends(require('equipment.ts.read'))):
    items = ts.points(equipment, q)
    if perms.user_scope(user) is not None:
        eqs = {e for e in {p['equipment'] for p in items} if _eq_allowed(user, e)}
        items = [p for p in items if p['equipment'] is None or p['equipment'] in eqs]
    return {'items': items, 'mode': ts.mode()}


@router.get('/api/ts/raw')
def ts_raw(point: str, start: str, end: str | None = None, limit: int = 10000, user=Depends(require('equipment.ts.read'))):
    _pt(user, point)
    return {'point': point, 'items': _call(ts.raw, point, start, end, min(max(limit, 1), 100000))}


@router.get('/api/ts/latest')
def ts_latest(points: str | None = None, equipment: int | None = None, user=Depends(require('equipment.ts.read'))):
    if equipment is not None and not _eq_allowed(user, equipment):
        raise HTTPException(404, _tr('没有这台设备'))
    keys = [int(x) if x.isdigit() else x for x in (points or '').split(',') if x] or None
    out = ts.latest(keys, equipment)
    if perms.user_scope(user) is not None and equipment is None:
        allowed = {p['key'] for p in ts_points(None, None, user)['items']}
        out = {k: v for k, v in out.items() if k in allowed}
    return {'items': out}


@router.get('/api/ts/rollup')
def ts_rollup(point: str, start: str, end: str | None = None, interval: str = '1m', user=Depends(require('equipment.ts.read'))):
    _pt(user, point)
    return {'point': point, 'interval': interval, 'items': _call(ts.rollup, point, start, end, interval)}


@router.get('/api/ts/segments')
def ts_segments(point: str, start: str, end: str | None = None, user=Depends(require('equipment.ts.read'))):
    _pt(user, point)
    return {'point': point, 'items': _call(ts.segments, point, start, end)}


@router.get('/api/ts/edges')
def ts_edges(point: str, start: str, end: str | None = None, kind: str = 'both', user=Depends(require('equipment.ts.read'))):
    _pt(user, point)
    return {'point': point, 'items': _call(ts.edges, point, start, end, kind)}


@router.get('/api/ts/stats')
def ts_stats(point: str, start: str, end: str | None = None, user=Depends(require('equipment.ts.read'))):
    _pt(user, point)
    return _call(ts.stats, point, start, end)


@router.post('/api/ts/write')
def ts_write(body: dict = Body(...), user=Depends(require('equipment.ts.write'))):
    """网关 HTTP 推送：{"rows": [["点位 key", "2026-11-04 08:00:00.250", 1, 0], …]} 或 {"points": [{"key","ts","value","q"}]}。重复的 (点位, 时间) 跳过。"""
    rows = body.get('rows') or [[p.get('key'), p.get('ts'), p.get('value'), p.get('q', 0)] for p in body.get('points') or []]
    if not rows or len(rows) > 50000:
        raise HTTPException(400, _tr('一次写 1 到 50000 行'))
    with context.source('api'):
        n = _call(ts.write, rows)
    return {'ok': True, 'rows': len(rows), 'inserted': n, 'duplicates': len(rows) - n}


@router.post('/api/ingest/{kind}')
def ingest_push(kind: str, body: dict = Body(...), user=Depends(require('equipment.ts.write'))):
    """网关 HTTP 推送网关消息：kind = part（零件事件）/ event（生产日志）。body = {"src": 数据源编码, "messages": [...]}。
    消息格式与 MQTT 的 <前缀>_mes/part、_mes/event 相同；交给采集的事件循环按顺序处理（与 MQTT 走同一个去重和断网续传）。"""
    if kind not in ('part', 'event'):
        raise HTTPException(400, _tr('kind 只能是 part 或 event'))
    from ..collector import COLLECTOR
    from ..ingest import GATE
    msgs = body.get('messages') or ([body['message']] if body.get('message') else [])
    src = body.get('src') or 'HTTP'
    if not COLLECTOR.loop:
        raise HTTPException(503, _tr('采集还没启动'))
    for m in msgs:
        COLLECTOR.submit(kind, [src, m])          # 5.0 M2：入库在采集工作线程里按顺序执行（不占事件循环）
    return {'ok': True, 'accepted': len(msgs)}


@router.get('/api/ts/retention')
def ts_retention_get(user=Depends(require('equipment.ts.read'))):
    return {'retention': ts.retention(), 'mode': ts.mode()}


@router.put('/api/ts/retention')
def ts_retention_put(body: dict = Body(...), user=Depends(require(perms.declare('system.ts.manage', '时序保留策略', '系统')))):
    try:
        r = ts.set_retention(body)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    with db.engine.begin() as c:
        audit.audit(c, 'ts_retention', None, None, detail=r, target='ts:retention')
    return r


@router.post('/api/ts/rollup/run')
def ts_rollup_run(user=Depends(require('system.ts.manage'))):
    return ts.rollup_job()
