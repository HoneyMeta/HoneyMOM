"""工厂模型与接入中心的接口（docs/dev/factory-model.md 有完整说明）。

工厂模型（/api/factory/…，查看权限 factory.model.read，所有登录用户和带 *.read 的 API 密钥都有）
  GET  tree | devices | device/{code} | points | catalog            层级树、设备（含布局）、单台设备（点位 + 最新值）、点位、AI 数据目录
  GET  versions   POST versions {note}（factory.model.publish）   GET versions/diff?a=&b=（b 缺省 = 当前）
  GET  templates  GET templates/{code}  POST templates/{code}/apply {devices: [编码…] | model: 型号, create_points, datasource}（factory.template.apply）
  GET  layout（2D / 3D 孪生：工厂坐标）  POST layout/dxf {name, data(base64)}  POST layout/autobind  PUT layout/positions {编码: {x, y, rot}}（factory.layout.edit）
  GET  files/{path}                    交付包里的三维模型等文件（models/xxx.glb）
  GET  live                            孪生实时状态（设备状态 + 关键点位）
接入中心（/api/onboard/…）
  GET  summary                         数据源状态、点位 / 确认 / 覆盖率、待确认数、导入记录
  POST package {name, data(base64)} | {url, token}   交付包预览（onboard.import.run）   POST imports/{id}/confirm {…选项}  POST imports/{id}/cancel
  POST points/import {name, data, mapping?}         点表预览；确认同上     GET points/template.xlsx  点表模板
  GET  check?datasource=&device=       逐点核对（红 / 黄 / 橙 / 绿）+ 值质量 + 覆盖率
  POST confirm {ids | all_green: true, datasource?, device?}（onboard.point.confirm）  POST points/{id}/ignore
  POST seen/{id}/add {device, meaning, unit, role, dtype, name?}  POST seen/{id}/ignore   POST seen/clear
  POST discover {datasource, minutes}（onboard.discover.run）
  GET  opcua/browse?datasource=&node=   OPC UA 浏览（子节点 + DisplayName / Description / DataType / EngineeringUnits / 当前值）
  POST opcua/add {datasource, device, nodes: [nodeid…]}
  POST push {source, values: [{code | address, value, ts?, q?}]}（API 密钥，onboard.push.write）   HTTP 推送
  GET  report.docx | report.pdf        联调验收报告（onboard.report.create）
  GET  collector                       采集工作线程 / 队列状态
  AI：POST ai/columns {headers, samples}  POST ai/draft {datasource?, ids?}  POST ai/plc {name, data, kind?}  GET ai/suspicious
      GET ai/drafts  POST ai/drafts/{id}/decide {decision, payload?, reason?}   POST ai/score {key}（原始现场答案打分，测试用）
"""
from __future__ import annotations

from ..i18n import _tr
import base64
import mimetypes
import os

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from .. import context, db, perms
from ..perms import declare, require
from ..schema import TABLES
from . import layout as layout_mod, live, model, package, pointtable

router = APIRouter()
P_READ = declare('factory.model.read', '查看工厂模型（层级、设备布局、点位目录、版本）', '工厂模型')
P_PUBLISH = declare('factory.model.publish', '发布工厂模型版本', '工厂模型')
P_LAYOUT = declare('factory.layout.edit', '导入平面图、设备落位', '工厂模型')
P_TPL = declare('factory.template.apply', '设备模板批量套用', '工厂模型')
P_CHECK = declare('onboard.check.read', '逐点核对、接入概况', '接入中心')
P_IMPORT = declare('onboard.import.run', '导入交付包 / 点表', '接入中心')
P_CONFIRM = declare('onboard.point.confirm', '确认 / 忽略采集点、处理待确认队列', '接入中心')
P_DISCOVER = declare('onboard.discover.run', '发现点位、OPC UA 浏览', '接入中心')
P_REPORT = declare('onboard.report.create', '生成联调验收报告', '接入中心')
P_AI = declare('onboard.ai.run', 'AI 补全点表、读 PLC 程序', '接入中心')
P_PUSH = declare('onboard.push.write', 'HTTP 推送采集值（网关 / 边缘程序）', '接入中心')
perms.bind_scope('work_order', 'line', 'line')         # 工单按产线归属（产线由工艺路线推出，model.wo_line）


def _uname(user):
    return user.get('name') or user.get('username') or '?'


def _b64(body):
    d = body.get('data')
    if not d:
        raise HTTPException(400, _tr('没有文件内容（data，base64）'))
    try:
        return package.b64(d)
    except Exception:
        raise HTTPException(400, _tr('文件内容不是 base64'))


# ───────────────────────── 工厂模型 ─────────────────────────
@router.get('/api/factory/tree')
def f_tree(user=Depends(require(P_READ))):
    with db.engine.connect() as c:
        return {'tree': model.tree(c, user)}


@router.get('/api/factory/devices')
def f_devices(line: int = 0, workshop: int = 0, q: str = '', user=Depends(require(P_READ))):
    with db.engine.connect() as c:
        allowed = perms.allowed_ids(user, 'equipment')
        lines = {l['id']: l for l in db.rows(c, 'line')}
        wss = {w['id']: w for w in db.rows(c, 'workshop')}
        tp = TABLES['tag_point']
        cnt = {r[0]: (r[1], r[2]) for r in c.execute(sa.select(tp.c.equipment, sa.func.count(), sa.func.sum(sa.case((tp.c.status == 'confirmed', 1), else_=0)))
                                                     .group_by(tp.c.equipment))}
        out = []
        for e in db.rows(c, 'equipment', order=TABLES['equipment'].c.code):
            if allowed is not None and e['id'] not in allowed:
                continue
            ln = lines.get(e['line']) or {}
            if line and e['line'] != line or workshop and ln.get('workshop') != workshop:
                continue
            if q and q.lower() not in (e['code'] + (e['name'] or '')).lower():
                continue
            n, ok = cnt.get(e['id'], (0, 0))
            out.append({**{k: db.jsonable(e.get(k)) for k in ('id', 'code', 'name', 'etype', 'template', 'model', 'vendor', 'status', 'x', 'y', 'w', 'd', 'rot',
                                                                'model3d', 'ideal_cycle_s')},
                        'line': ln.get('name'), 'workshop': (wss.get(ln.get('workshop')) or {}).get('name'), 'points': n, 'confirmed': int(ok or 0)})
    return {'items': out}


@router.get('/api/factory/device/{key}')
def f_device(key: str, user=Depends(require(P_READ))):
    from .. import ts
    with db.engine.connect() as c:
        e = db.get(c, 'equipment', code=key) or (db.get(c, 'equipment', int(key)) if key.isdigit() else None)
        if not e or not perms.in_scope(user, 'equipment', e):
            raise HTTPException(404, _tr('设备不存在'))
        pts = db.rows(c, 'tag_point', equipment=e['id'], order=TABLES['tag_point'].c.id)
        tpl = model.template(e.get('template') or e['etype'])
    lat = ts.latest([p['ts_key'] for p in pts if p.get('ts_key')]) if pts else {}
    return {'device': {k: db.jsonable(v) for k, v in e.items()}, 'template': {'code': tpl['code'], 'name': tpl['name']} if tpl else None,
            'key_points': model.key_points(e.get('template') or e['etype']),
            'points': [{**{k: db.jsonable(p.get(k)) for k in ('id', 'code', 'name', 'desc', 'unit', 'dtype', 'role', 'sem', 'rw', 'protocol', 'address', 'status',
                                                              'normal_range', 'alarm_lo', 'alarm_hi', 'ts_key', 'confidence', 'origin')},
                        'latest': lat.get(p.get('ts_key'))} for p in pts]}


@router.get('/api/factory/points')
def f_points(device: str = '', role: str = '', status: str = '', datasource: int = 0, q: str = '', limit: int = 2000, user=Depends(require(P_READ))):
    with db.engine.connect() as c:
        tp = TABLES['tag_point']
        qq = sa.select(tp)
        if device:
            e = db.get(c, 'equipment', code=device)
            qq = qq.where(tp.c.equipment == (e['id'] if e else -1))
        if role:
            qq = qq.where(tp.c.role == role)
        if status:
            qq = qq.where(tp.c.status == status)
        if datasource:
            qq = qq.where(tp.c.datasource == datasource)
        if q:
            qq = qq.where(sa.or_(tp.c.code.ilike(f'%{q}%'), tp.c.desc.ilike(f'%{q}%'), tp.c.address.ilike(f'%{q}%')))
        allowed = perms.allowed_ids(user, 'equipment')
        rows = [dict(r) for r in c.execute(qq.order_by(tp.c.id).limit(max(1, min(limit, 20000)))).mappings()]
    rows = [r for r in rows if allowed is None or r['equipment'] is None or r['equipment'] in allowed]
    return {'items': [{k: db.jsonable(v) for k, v in r.items()} for r in rows]}


@router.get('/api/factory/catalog')
def f_catalog(device: str = '', user=Depends(require(P_READ))):
    with db.engine.connect() as c:
        return {'devices': model.catalog(c, user, device or None)}


@router.get('/api/factory/versions')
def f_versions(user=Depends(require(P_READ))):
    from ..schema_factory import factory_version_t as vt
    with db.engine.connect() as c:
        rows = c.execute(sa.select(vt.c.version, vt.c.note, vt.c.counts, vt.c.published_by, vt.c.published_at).order_by(vt.c.version.desc())).mappings().all()
        cur = model.counts(model.snapshot(c))
    return {'versions': [{**dict(r), 'published_at': db.jsonable(r['published_at'])} for r in rows], 'current': cur}


@router.post('/api/factory/versions')
def f_publish(body: dict = Body(default={}), user=Depends(require(P_PUBLISH))):
    with db.engine.begin() as c:
        return model.publish(c, body.get('note') or '', _uname(user))


@router.get('/api/factory/versions/diff')
def f_diff(a: str = '', b: str = 'current', user=Depends(require(P_READ))):
    with db.engine.connect() as c:
        from ..schema_factory import factory_version_t as vt
        if not a:
            a = c.execute(sa.select(vt.c.version).order_by(vt.c.version.desc()).limit(1)).scalar() or 0
        sa_, sb = model.version_snap(c, a), model.version_snap(c, b)
    if sa_ is None or sb is None:
        raise HTTPException(404, _tr('版本不存在'))
    d = model.diff(sa_, sb)
    return {'a': a, 'b': b, 'diff': d, 'total': {k: len(v['added']) + len(v['removed']) + len(v['changed']) for k, v in d.items()}}


@router.get('/api/factory/templates')
def f_templates(user=Depends(require(P_READ))):
    with db.engine.connect() as c:
        tps = model.templates_all(c, refresh=True)
        cnt = {r[0]: r[1] for r in c.execute(sa.select(TABLES['equipment'].c.template, sa.func.count()).group_by(TABLES['equipment'].c.template))}
    return {'items': [{**{k: db.jsonable(v) for k, v in t.items() if k != 'points'}, 'n_points': len(t.get('points') or []), 'devices': cnt.get(t['code'], 0)}
                      for t in tps]}


@router.get('/api/factory/templates/{code}')
def f_template(code: str, user=Depends(require(P_READ))):
    t = model.template(code)
    if not t:
        raise HTTPException(404, _tr('模板不存在'))
    with db.engine.connect() as c:
        devs = db.rows(c, 'equipment', template=code)
    return {'template': {k: db.jsonable(v) for k, v in t.items()}, 'devices': [{'id': d['id'], 'code': d['code'], 'name': d['name'], 'model': d['model']} for d in devs]}


@router.post('/api/factory/templates/{code}/apply')
def f_apply(code: str, body: dict = Body(...), user=Depends(require(P_TPL))):
    with db.engine.begin() as c:
        ids = []
        for k in body.get('devices') or []:
            e = db.get(c, 'equipment', code=str(k)) or (db.get(c, 'equipment', int(k)) if str(k).isdigit() else None)
            if e:
                ids.append(e['id'])
        if body.get('model'):                                   # 同型号的设备一起套
            ids += [e['id'] for e in db.rows(c, 'equipment', model=body['model']) if e['id'] not in ids]
        if not ids:
            raise HTTPException(400, _tr('没有选设备'))
        try:
            res = model.apply_template(c, code, ids, bool(body.get('create_points')), body.get('datasource'))
        except ValueError as ex:
            raise HTTPException(400, str(ex))
        from .. import audit
        audit.audit(c, 'template_apply', 'device_template', None, detail={'template': code, 'devices': len(ids), **res}, target=_tr('模板 {code}', code=code))
    from ..collector import COLLECTOR
    COLLECTOR.reload_model_threadsafe()
    return {'ok': True, **res}


@router.get('/api/factory/layout')
def f_layout(user=Depends(require(P_READ))):
    with db.engine.connect() as c:
        return layout_mod.twin_layout(c, user)


@router.post('/api/factory/layout/dxf')
def f_layout_dxf(body: dict = Body(...), user=Depends(require(P_LAYOUT))):
    data = _b64(body)
    text = data.decode('utf-8', 'replace') if data[:3] != b'\xef\xbb\xbf' else data[3:].decode('utf-8', 'replace')
    if 'SECTION' not in text[:5000]:
        raise HTTPException(400, _tr('不是 DXF 文本文件（请另存为 DXF，ASCII 格式）'))
    with db.engine.begin() as c, context.source('import'):
        pl = db.rows(c, 'plant', limit=1)
        r = layout_mod.import_dxf(c, text, body.get('name') or 'layout.dxf', pl[0]['id'] if pl else None, user['username'])
        r['bind'] = layout_mod.autobind(c, only_unplaced=not body.get('rebind'))
    _invalidate_home()
    return {'ok': True, **r}


@router.post('/api/factory/layout/autobind')
def f_autobind(body: dict = Body(default={}), user=Depends(require(P_LAYOUT))):
    with db.engine.begin() as c:
        r = layout_mod.autobind(c, only_unplaced=bool(body.get('only_unplaced')))
    _invalidate_home()
    return {'ok': True, **r}


@router.put('/api/factory/layout/positions')
def f_positions(body: dict = Body(...), user=Depends(require(P_LAYOUT))):
    with db.engine.begin() as c:
        n = layout_mod.save_positions(c, body.get('items') or body)
    _invalidate_home()
    return {'ok': True, 'moved': n}


def _invalidate_home():
    try:
        from ..services import home
        with home._lock:
            home._cache.pop('layout', None)
            home._cache.pop('model_layout', None)
    except Exception:
        pass


@router.get('/api/factory/files/{path:path}')
def f_file(path: str, user=Depends(require(P_READ))):
    base = os.path.abspath(os.path.join(db.DATA, 'factory'))
    name = os.path.basename(path)
    f = os.path.abspath(os.path.join(base, 'models', name)) if path.startswith('models/') else None
    if not f or not f.startswith(base) or not os.path.isfile(f):
        raise HTTPException(404, _tr('文件不存在'))
    return FileResponse(f, media_type=mimetypes.guess_type(f)[0] or ('model/gltf-binary' if f.endswith('.glb') else 'application/octet-stream'))


@router.get('/api/factory/live')
def f_live(user=Depends(require(P_READ))):
    from ..services.home import live_data
    d = live_data()
    ids = perms.allowed_ids(user, 'equipment')
    return d if ids is None else {**d, 'items': [x for x in d['items'] if x['id'] in ids]}


# ───────────────────────── 接入中心 ─────────────────────────
@router.get('/api/onboard/summary')
def ob_summary(user=Depends(require(P_CHECK))):
    from ..schema_factory import onboard_import_t as it, onboard_seen_t as st, onboard_report_t as rt
    with db.engine.connect() as c:
        tp = TABLES['tag_point']
        n = c.execute(sa.select(sa.func.count()).select_from(tp).where(tp.c.enabled.is_(True))).scalar() or 0
        ok = c.execute(sa.select(sa.func.count()).select_from(tp).where(tp.c.enabled.is_(True) & (tp.c.status == 'confirmed'))).scalar() or 0
        unassigned = c.execute(sa.select(sa.func.count()).select_from(tp).where(tp.c.enabled.is_(True) & tp.c.equipment.is_(None))).scalar() or 0
        seen = c.execute(sa.select(sa.func.count()).select_from(st).where(st.c.status == 'new')).scalar() or 0
        imports = [{**dict(r), 'created_at': db.jsonable(r['created_at']), 'decided_at': db.jsonable(r['decided_at'])}
                   for r in c.execute(sa.select(it.c.id, it.c.kind, it.c.file_name, it.c.status, it.c.created_by, it.c.created_at, it.c.decided_at, it.c.summary)
                                      .order_by(it.c.id.desc()).limit(10)).mappings()]
        for r in imports:
            s = r.pop('summary') or {}
            r['devices'], r['points'], r['result'] = s.get('devices'), s.get('points'), s.get('result')
        reports = [{**dict(r), 'created_at': db.jsonable(r['created_at'])} for r in c.execute(sa.select(rt.c.id, rt.c.title, rt.c.coverage, rt.c.created_by, rt.c.created_at)
                                                                                                .order_by(rt.c.id.desc()).limit(10)).mappings()]
        n_dev = c.execute(sa.select(sa.func.count()).select_from(TABLES['equipment'])).scalar()
        placed = c.execute(sa.select(sa.func.count()).select_from(TABLES['equipment']).where(TABLES['equipment'].c.x.isnot(None))).scalar()
        lay = layout_mod.active(c)
        sources = live.collector_status(c)
    from ..services import ai_switch
    return {'points': n, 'confirmed': ok, 'coverage': round(ok / n, 4) if n else 0.0, 'unassigned': unassigned, 'pending_seen': seen, 'devices': n_dev,
            'placed': placed, 'layout': {'id': lay['id'], 'name': lay['name'], 'blocks': len(lay['blocks'] or [])} if lay else None, 'sources': sources,
            'imports': imports, 'reports': reports, 'ai': ai_switch.enabled('onboard'), 'ai_demo': ai_switch.demo()}


@router.post('/api/onboard/package')
def ob_package(body: dict = Body(...), user=Depends(require(P_IMPORT))):
    if body.get('url'):
        try:
            data = package.fetch(body['url'], body.get('token') or '')
        except Exception as ex:
            raise HTTPException(400, _tr('取不到交付包 {url}：{ex}', url=body['url'], ex=ex))
        name = body['url'].rstrip('/').split('/')[-1] + ('' if body['url'].endswith('.zip') else '-delivery.zip')
    else:
        data, name = _b64(body), body.get('name') or 'delivery.zip'
    opts = {k: body[k] for k in ('ds_prefix', 'endpoints', 'use_package_endpoints', 'confirm_all', 'publish', 'note', 'remove_missing') if k in body}
    try:
        with db.engine.begin() as c:
            r = package.preview(c, data, name, opts, user['username'])
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    if body.get('confirm'):                                    # 一步到位（演示 / 脚本）：没有错误就直接确认
        if r['summary']['errors'] and not body.get('force'):
            raise HTTPException(400, _tr('交付包有 {errors} 个错误，先看预览', errors=r['summary']['errors']))
        return {**r, 'result': _confirm(r['id'], opts, user)}
    return r


@router.post('/api/onboard/points/import')
def ob_points_import(body: dict = Body(...), user=Depends(require(P_IMPORT))):
    data = _b64(body)
    try:
        with db.engine.begin() as c:
            return package.import_points(c, data, body.get('name') or 'points.xlsx', {k: body[k] for k in ('mapping', 'datasources') if k in body},
                                         user['username'])
    except Exception as ex:
        if isinstance(ex, HTTPException):
            raise
        raise HTTPException(400, _tr('读不了这个 Excel：{ex}', ex=ex))


@router.get('/api/onboard/imports/{rid}')
def ob_import_get(rid: int, user=Depends(require(P_CHECK))):
    from ..schema_factory import onboard_import_t as it
    with db.engine.connect() as c:
        r = c.execute(sa.select(it).where(it.c.id == rid)).mappings().first()
    if not r:
        raise HTTPException(404, _tr('导入批次不存在'))
    rows = r['rows'] or {}
    return {'id': r['id'], 'kind': r['kind'], 'file_name': r['file_name'], 'status': r['status'], 'summary': r['summary'], 'issues': (r['issues'] or [])[:1000],
            'devices': (rows.get('devices') or [])[:500], 'points': (rows.get('points') or [])[:3000]}


def _confirm(rid, opts, user):
    from ..schema_factory import onboard_import_t as it
    with db.engine.begin() as c:
        kind = c.execute(sa.select(it.c.kind).where(it.c.id == rid)).scalar()
        try:
            res = (package.confirm if kind == 'package' else package.confirm_points)(c, rid, opts, user['username'])
        except ValueError as ex:
            raise HTTPException(400, str(ex))
    _invalidate_home()
    return res


@router.post('/api/onboard/imports/{rid}/confirm')
def ob_import_confirm(rid: int, body: dict = Body(default={}), user=Depends(require(P_IMPORT))):
    return {'ok': True, 'result': _confirm(rid, body or {}, user)}


@router.post('/api/onboard/imports/{rid}/cancel')
def ob_import_cancel(rid: int, user=Depends(require(P_IMPORT))):
    from ..schema_factory import onboard_import_t as it
    with db.engine.begin() as c:
        c.execute(it.update().where((it.c.id == rid) & (it.c.status == 'preview')).values(status='cancelled', decided_by=user['username'], decided_at=db.clock.now()))
    return {'ok': True}


@router.get('/api/onboard/points/template.xlsx')
def ob_template(user=Depends(require(P_CHECK))):
    with db.engine.connect() as c:
        names = [t['name'] for t in model.templates_all(c)]
    return Response(pointtable.template_xlsx(names), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': 'attachment; filename=point-table-template.xlsx'})


@router.get('/api/onboard/check')
def ob_check(datasource: int = 0, device: int = 0, user=Depends(require(P_CHECK))):
    with db.engine.connect() as c:
        return live.check(c, datasource or None, device or None, user)


@router.post('/api/onboard/confirm')
def ob_confirm(body: dict = Body(...), user=Depends(require(P_CONFIRM))):
    with db.engine.begin() as c:
        ids = [int(i) for i in body.get('ids') or []]
        if body.get('all_green') or body.get('colors'):
            chk = live.check(c, body.get('datasource'), body.get('device'), user)
            want = set(body.get('colors') or ['green'])
            ids += [r['id'] for r in chk['rows'] if r['color'] in want and r['status'] != 'confirmed' and r['equipment']]
        n = model.confirm(c, ids, _uname(user), 'ignored' if body.get('ignore') else 'confirmed')
    model.INDEX.dirty = True
    return {'ok': True, 'n': n}


@router.post('/api/onboard/points/{pid}/{act}')
def ob_point_act(pid: int, act: str, body: dict = Body(default={}), user=Depends(require(P_CONFIRM))):
    if act not in ('confirm', 'ignore', 'pending', 'update'):
        raise HTTPException(404, _tr('没有这个操作'))
    with db.engine.begin() as c:
        p = db.get(c, 'tag_point', pid)
        if not p:
            raise HTTPException(404, _tr('点位不存在'))
        if act == 'update':
            upd = {k: body[k] for k in ('desc', 'unit', 'role', 'dtype', 'normal_range', 'alarm_lo', 'alarm_hi', 'scale', 'offset', 'sem') if k in body}
            if upd:
                db.update(c, 'tag_point', pid, upd)
            if 'device' in body:
                e = db.get(c, 'equipment', code=body['device']) if body['device'] else None
                model.set_point_device(c, pid, e['id'] if e else None, body.get('name'))
        else:
            model.confirm(c, [pid], _uname(user), {'confirm': 'confirmed', 'ignore': 'ignored', 'pending': 'pending'}[act])
    model.INDEX.dirty = True
    return {'ok': True}


@router.post('/api/onboard/seen/{sid}/{act}')
def ob_seen(sid: str, act: str, body: dict = Body(default={}), user=Depends(require(P_CONFIRM))):
    from ..schema_factory import onboard_seen_t as st
    from ..collector import COLLECTOR
    with db.engine.begin() as c:
        if sid == 'clear':
            n = c.execute(st.update().where(st.c.status == 'new').values(status='ignored')).rowcount
            return {'ok': True, 'n': n}
        r = c.execute(sa.select(st).where(st.c.id == int(sid))).mappings().first()
        if not r:
            raise HTTPException(404, _tr('记录不存在'))
        if act == 'ignore':
            c.execute(st.update().where(st.c.id == r['id']).values(status='ignored'))
            return {'ok': True}
        if act != 'add':
            raise HTTPException(404, _tr('没有这个操作'))
        ds = db.get(c, 'datasource', r['datasource'])
        dev = db.get(c, 'equipment', code=body.get('device')) if body.get('device') else None
        code = body.get('code') or (f"{dev['code']}.{(body.get('name') or r['address'].split('/')[-1]).replace('/', '.')}" if dev else r['address'])
        prow = model.point_row({'code': code, 'device': dev['code'] if dev else None, 'meaning': body.get('meaning') or (r['attrs'] or {}).get('description'),
                                'unit': body.get('unit') or (r['attrs'] or {}).get('unit'), 'dtype': body.get('dtype') or r['dtype'], 'role': body.get('role'),
                                'protocol': ds['protocol'], 'address': r['address'], 'name': body.get('name')}, dev, ds, 'discovered')
        pid, _st = model.upsert_point(c, prow, status='confirmed' if body.get('confirm') else 'pending', confirmed_by=_uname(user))
        c.execute(st.update().where(st.c.id == r['id']).values(status='added', point=pid))
    from .live import LIVE
    LIVE.unknown.pop((r['datasource'], r['address']), None)
    model.INDEX.dirty = True
    COLLECTOR.restart_threadsafe(r['datasource']) if ds['protocol'] in ('opcua', 'modbus') else None
    return {'ok': True, 'point': pid}


@router.post('/api/onboard/discover')
def ob_discover(body: dict = Body(...), user=Depends(require(P_DISCOVER))):
    from ..collector import COLLECTOR
    with db.engine.connect() as c:
        ds = db.get(c, 'datasource', int(body['datasource']))
    if not ds:
        raise HTTPException(404, _tr('数据源不存在'))
    if ds['protocol'] not in ('mqtt', 'sparkplug', 'sql', 'http'):
        raise HTTPException(400, _tr('OPC UA 用"浏览"；Modbus 没有发现（按点表轮询）'))
    COLLECTOR.discover(ds, float(body.get('minutes') or 5))
    return {'ok': True, 'seconds': int(float(body.get('minutes') or 5) * 60)}


@router.get('/api/onboard/opcua/browse')
async def ob_browse(datasource: int, node: str = '', user=Depends(require(P_DISCOVER))):
    from . import opcua_browse
    with db.engine.connect() as c:
        ds = db.get(c, 'datasource', datasource)
    if not ds or ds['protocol'] != 'opcua':
        raise HTTPException(400, _tr('只有 OPC UA 数据源能浏览'))
    try:
        return await opcua_browse.browse(ds, node or None)
    except Exception as ex:
        raise HTTPException(400, _tr('浏览失败：{ex}', ex=ex))


@router.post('/api/onboard/opcua/add')
async def ob_browse_add(body: dict = Body(...), user=Depends(require(P_CONFIRM))):
    from . import opcua_browse
    with db.engine.connect() as c:
        ds = db.get(c, 'datasource', int(body['datasource']))
    if not ds:
        raise HTTPException(404, _tr('数据源不存在'))
    try:
        attrs = await opcua_browse.attributes(ds, body.get('nodes') or [])
    except Exception as ex:
        raise HTTPException(400, _tr('读节点属性失败：{ex}', ex=ex))
    with db.engine.begin() as c:
        dev = db.get(c, 'equipment', code=body['device']) if body.get('device') else None
        n = 0
        for a in attrs:
            name = a.get('path') or a['display_name']
            code = f"{dev['code']}.{name.replace('/', '.')}" if dev else a['nodeid']
            row = model.point_row({'code': code, 'device': dev['code'] if dev else None, 'meaning': a.get('description') or a.get('display_name'), 'unit': a.get('unit'),
                                   'dtype': a.get('dtype'), 'protocol': 'opcua', 'address': a['nodeid'], 'name': name if dev else None,
                                   'rw': 'RW' if a.get('writable') else 'R'}, dev, ds, 'discovered')
            model.upsert_point(c, row)
            n += 1
    from ..collector import COLLECTOR
    COLLECTOR.restart_threadsafe(ds['id'])
    return {'ok': True, 'n': n}


@router.post('/api/onboard/push')
def ob_push(body: dict = Body(...), user=Depends(require(P_PUSH))):
    from ..collector import COLLECTOR
    vals = body.get('values') or []
    if not vals or len(vals) > 50000:
        raise HTTPException(400, _tr('一次推 1 到 50000 个值'))
    with db.engine.connect() as c:
        ds = db.get(c, 'datasource', code=body.get('source') or '')
    if not ds or ds['protocol'] != 'http' or not ds['enabled']:
        raise HTTPException(400, _tr('没有启用的 HTTP 推送数据源 {get}（数据源协议选“HTTP 推送”）', get=body.get('source')))
    with context.source('collector'):
        r = COLLECTOR.push(ds, vals)
    return {'ok': True, **r}


@router.get('/api/onboard/collector')
def ob_collector(user=Depends(require(P_CHECK))):
    from ..collector import COLLECTOR
    from ..ingest import GATE
    return {'worker': COLLECTOR.worker.status(), 'gate': GATE.status(), 'status': {str(k): v for k, v in COLLECTOR.status.items()}}


@router.get('/api/onboard/report.{fmt}')
def ob_report(fmt: str, user=Depends(require(P_REPORT))):
    from . import report
    if fmt not in ('docx', 'pdf', 'json'):
        raise HTTPException(404, _tr('格式只有 docx / pdf / json'))
    with db.engine.begin() as c:
        data = report.collect(c, user)
        if fmt == 'json':
            return data
        blob = report.docx(data) if fmt == 'docx' else report.pdf(data)
        report.record(c, data, fmt, blob, user['username'])
    mt = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' if fmt == 'docx' else 'application/pdf'
    return Response(blob, media_type=mt, headers={'Content-Disposition': f'attachment; filename=onboard-acceptance.{fmt}'})


# ───────────────────────── AI 辅助 ─────────────────────────
@router.post('/api/onboard/ai/columns')
def ai_columns(body: dict = Body(...), user=Depends(require(P_AI))):
    from . import assist
    return assist.map_columns({'point': body.get('headers') or []}, {'point': body.get('headers') or []}, body.get('samples'), force=True) or {'mapping': {}}


@router.post('/api/onboard/ai/draft')
def ai_draft(body: dict = Body(default={}), user=Depends(require(P_AI))):
    from . import assist
    try:
        return assist.draft_points(user, body.get('datasource'), body.get('ids'), body.get('limit') or 400)
    except assist.AssistError as ex:
        raise HTTPException(400, str(ex))


@router.post('/api/onboard/ai/plc')
def ai_plc(body: dict = Body(...), user=Depends(require(P_AI))):
    from . import assist
    data = _b64(body)
    try:
        return assist.read_plc(user, body.get('name') or 'program', data, body.get('kind'), body.get('device'), body.get('datasource'))
    except assist.AssistError as ex:
        raise HTTPException(400, str(ex))


@router.get('/api/onboard/ai/suspicious')
def ai_suspicious(user=Depends(require(P_CHECK))):
    from . import assist
    with db.engine.connect() as c:
        return {'items': assist.suspicious(c, user)}


@router.get('/api/onboard/ai/drafts')
def ai_drafts_list(status: str = 'pending', user=Depends(require(P_CHECK))):
    from . import assist
    return {'items': assist.list_drafts(user, status)}


@router.post('/api/onboard/ai/drafts/{did}/decide')
def ai_drafts_decide(did: int, body: dict = Body(...), user=Depends(require(P_CONFIRM))):
    from ..services import ai_drafts
    r = ai_drafts.decide(did, body.get('decision'), body.get('payload'), body.get('reason') or '', user)
    model.INDEX.dirty = True
    return r


@router.post('/api/onboard/ai/score')
def ai_score(body: dict = Body(...), user=Depends(require(P_AI))):
    """原始现场的补全打分：body = 虚拟工厂的标准答案（answer_key.json 的内容），对比规则（+ llm=true 时大模型）草稿的 含义 / 设备 / 用途 / 单位。测试用。"""
    from . import assist
    with db.engine.connect() as c:
        return assist.score(c, body.get('key') or body, bool(body.get('llm')), body.get('datasources'))


# 新建 / 修改工单后补产线（数据范围用；由工艺路线推出）。接在已有的 after_save 钩子后面。
def _install_wo_hook():
    from .. import crud
    prev = crud.HOOKS.get(('work_order', 'after_save'))

    def after(c, rid, user):
        if prev:
            prev(c, rid, user)
        w = db.get(c, 'work_order', rid)
        if w and not w.get('line') and w.get('route'):
            ln = model.wo_line(c, w)
            if ln:
                c.execute(TABLES['work_order'].update().where(TABLES['work_order'].c.id == rid).values(line=ln))
    crud.HOOKS[('work_order', 'after_save')] = after


_install_wo_hook()

# 点表也登记到 M3 的通用导入框架（实施 → 数据导入）
from . import importers as _importers  # noqa: E402
_importers.register()
