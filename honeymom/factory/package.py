"""交付包导入：系统集成商按接入规范交付的 zip（points.xlsx + layout.dxf + models/*.glb + README.md）→ 工厂模型 + 平面图 + 3D + 采集点。

导入 → 校验 → 预览（preview：新增 / 变化的设备和点位、各协议端点、问题清单，存一个导入批次）→ 确认（confirm：写模型、建 / 更新数据源、
平面图落位、存三维模型、建刀具台账、点检计划，采集程序按新点表重连）→ 逐点核对（live.py）→ 确认点位。
虚拟工厂（演示）也走这条路：MES 从虚拟工厂取标准交付包（GET <虚拟工厂>/api/p/<工厂>/delivery.zip），和真实工厂收到集成商的 zip 一样处理。
"""
from __future__ import annotations

from ..i18n import _tr
import base64
import io
import json
import os
import re
import urllib.request
import zipfile

import sqlalchemy as sa

from .. import db
from ..db import clock
from . import layout as layout_mod, model, pointtable

DS_SUFFIX = {'mqtt': '', 'sparkplug': '-SPB', 'opcua': '-OPC', 'modbus': '-MB', 'http': '-HTTP', 'sql': '-SQL'}


def pkg_dir(*p):
    d = os.path.join(db.DATA, 'factory', *p)
    os.makedirs(d, exist_ok=True)
    return d


def fetch(url: str, token: str = '') -> bytes:
    """从地址取交付包（虚拟工厂：<地址>/delivery.zip；也可以直接给 zip 的完整地址）。"""
    u = url.rstrip('/')
    if not u.endswith('.zip'):
        u += '/delivery.zip'
    req = urllib.request.Request(u, headers={'Authorization': 'Bearer ' + token} if token else {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def read_zip(data: bytes) -> dict:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError(_tr('不是 zip 文件'))
    names = z.namelist()
    xl = next((n for n in names if n.lower().endswith('.xlsx') and not n.startswith('__MACOSX')), None)
    if not xl:
        raise ValueError(_tr('交付包里没有点表（.xlsx）'))
    pt = pointtable.read_xlsx(z.read(xl))
    dxf = next((n for n in names if n.lower().endswith('.dxf')), None)
    models = [n for n in names if n.lower().endswith(('.glb', '.gltf'))]
    info = {}
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(z.read(xl)), read_only=True)
        if '说明' in wb.sheetnames:
            for r in wb['说明'].iter_rows(min_row=2, values_only=True):
                if r and r[0] == '交付包' and r[1]:
                    info[str(r[1])] = r[2]
    except Exception:
        pass
    return {'zip': z, 'names': names, 'xlsx': xl, 'points': pt, 'dxf': dxf, 'models': models, 'readme': 'README.md' in names, 'info': info}


def _default_prefix(c, endpoints, info):
    mp = endpoints.get('mqtt_prefix')
    if mp:
        for d in db.rows(c, 'datasource'):
            if d['prefix'] == mp and d['protocol'] == 'mqtt':
                return d['code']
    plant = str(info.get('工厂') or '')
    m = re.search(r'（([A-Za-z0-9_-]+)）', plant)
    base = re.sub(r'[^A-Za-z0-9]+', '', (m.group(1) if m else '') .upper())[:8]
    return base or 'SITE'


def plan_datasources(c, protos, endpoints, options, info) -> dict:
    prefix = (options.get('ds_prefix') or _default_prefix(c, endpoints, info)).upper()
    out = {}
    for proto in protos:
        code = (options.get('ds_codes') or {}).get(proto) or prefix + DS_SUFFIX.get(proto, '-' + proto.upper())
        cur = db.get(c, 'datasource', code=code)
        ep = (options.get('endpoints') or {}).get(proto) or endpoints.get(proto)
        out[proto] = {'code': code, 'exists': bool(cur), 'id': cur['id'] if cur else None, 'endpoint_package': endpoints.get(proto),
                      'endpoint_current': cur['endpoint'] if cur else None, 'endpoint': ep if (not cur or options.get('use_package_endpoints') or
                                                                                              (options.get('endpoints') or {}).get(proto)) else cur['endpoint'],
                      'prefix': endpoints.get('mqtt_prefix') if proto in ('mqtt',) else (cur['prefix'] if cur else None)}
    return out


def preview(c, data: bytes, file_name: str, options: dict, user: str) -> dict:
    from ..schema_factory import onboard_import_t as it
    pk = read_zip(data)
    pt = pk['points']
    devs, pts = pt['devices'], pt['points']
    known = {e['code'] for e in db.rows(c, 'equipment')}
    issues = pointtable.validate(devs, pts, known)
    endpoints = pointtable.endpoints_from(pt['protocols'])
    protos = sorted({p.get('protocol') for p in pts if p.get('protocol') in pointtable.PROTO_VALUES})
    dsplan = plan_datasources(c, protos, endpoints, options, pk['info'])
    eqs = {e['code']: e for e in db.rows(c, 'equipment')}
    d_new = [d['code'] for d in devs if d.get('code') and d['code'] not in eqs]
    d_upd = [d['code'] for d in devs if d.get('code') in eqs]
    tp = db.metadata.tables['tag_point']
    have = {}
    for r in c.execute(sa.select(tp.c.code, tp.c.datasource)).mappings():
        have.setdefault(r['datasource'], set()).add(r['code'])
    p_new = sum(1 for p in pts if p.get('code') and p['code'] not in have.get((dsplan.get(p.get('protocol')) or {}).get('id'), set()))
    raw = sum(1 for p in pts if not p.get('device') and not p.get('meaning'))
    by_proto = {}
    for p in pts:
        by_proto[p.get('protocol') or '?'] = by_proto.get(p.get('protocol') or '?', 0) + 1
    from . import templates as tpl
    tpls = model.templates_all(c)
    tmatch = {}
    for d in devs:
        tmatch.setdefault(d.get('type') or '', tpl.match_type(tpls, d.get('type')))
    summary = {'plant': pk['info'].get('工厂'), 'format': pk['info'].get('格式'), 'site_state': pk['info'].get('现场状态'), 'devices': len(devs),
               'devices_new': len(d_new), 'devices_updated': len(d_upd), 'points': len(pts), 'points_new': p_new, 'by_protocol': by_proto,
               'raw_points': raw, 'raw_site': raw > len(pts) * 0.5, 'files': pk['names'], 'models': pk['models'], 'dxf': pk['dxf'],
               'datasources': dsplan, 'endpoints': endpoints, 'templates': tmatch, 'columns': pt['columns'], 'unmapped': pt['unmapped'],
               'errors': sum(1 for i in issues if i['level'] == 'error'), 'warnings': sum(1 for i in issues if i['level'] == 'warn')}
    rid = c.execute(it.insert().values(kind='package', file_name=file_name, status='preview', mapping=pt['columns'], summary=summary, issues=issues[:3000],
                                       rows={'devices': devs, 'points': pts, 'protocols': pt['protocols']}, options=options, created_by=user,
                                       created_at=clock.now())).inserted_primary_key[0]
    with open(os.path.join(pkg_dir('packages'), f'{rid}.zip'), 'wb') as f:
        f.write(data)
    return {'id': rid, 'summary': summary, 'issues': issues[:300]}


def confirm(c, rid: int, options: dict, user: str) -> dict:
    """确认导入：写模型。错误行跳过（点表里的错误必须先改；设备 / 点位级别的错误行不导入，警告照常导入、点位进待确认）。"""
    from ..schema_factory import onboard_import_t as it
    r = c.execute(sa.select(it).where(it.c.id == rid)).mappings().first()
    if not r:
        raise ValueError(_tr('导入批次不存在'))
    if r['status'] != 'preview':
        raise ValueError(_tr('这个导入批次已经处理过了'))
    opts = {**(r['options'] or {}), **(options or {})}
    rows = r['rows'] or {}
    path = os.path.join(pkg_dir('packages'), f'{rid}.zip')
    data = open(path, 'rb').read() if os.path.exists(path) else None
    pk = read_zip(data) if data else None
    res = apply(c, rows.get('devices') or [], rows.get('points') or [], rows.get('protocols') or [], r['issues'] or [], opts, user, origin='package',
                pk=pk, info=(r['summary'] or {}).get('plant'))
    c.execute(it.update().where(it.c.id == rid).values(status='confirmed', decided_by=user, decided_at=clock.now(), summary={**(r['summary'] or {}), 'result': res}))
    return res


def apply(c, devs, pts, protocols, issues, opts, user, origin='package', pk=None, info=None) -> dict:
    from .. import audit, context
    bad_rows = {(i['sheet'], i['row']) for i in issues if i['level'] == 'error'}
    endpoints = pointtable.endpoints_from(protocols)
    protos = sorted({p.get('protocol') for p in pts if p.get('protocol') in pointtable.PROTO_VALUES})
    res = {'devices_new': 0, 'devices_updated': 0, 'points_new': 0, 'points_updated': 0, 'points_disabled': 0, 'skipped': 0, 'datasources': {},
           'layout': None, 'models': 0, 'tools': 0}
    with context.source('import'):
        plant = db.rows(c, 'plant', limit=1)
        if plant:
            plant_id = plant[0]['id']
        else:
            pname = str(info or _tr('工厂')).split('（')[0] or _tr('工厂')
            plant_id = db.insert(c, 'plant', {'code': 'P1', 'name': pname}, raw=True)
        # 数据源
        ds_ids = {}
        if origin == 'package' or opts.get('create_datasources'):
            for proto, d in plan_datasources(c, protos, endpoints, opts, {'工厂': info}).items():
                if d['id']:
                    upd = {}
                    if d['endpoint'] and d['endpoint'] != d['endpoint_current']:
                        upd['endpoint'] = d['endpoint']
                    if d.get('prefix') and proto == 'mqtt':
                        upd['prefix'] = d['prefix']
                    if upd:
                        db.update(c, 'datasource', d['id'], upd, raw=True)
                    ds_ids[proto] = d['id']
                else:
                    ds_ids[proto] = db.insert(c, 'datasource', {'code': d['code'], 'name': f"{info or _tr('现场')} {pointtable.PROTO_ALIAS.get(proto, proto).upper()}"[:60],
                                                                'protocol': proto, 'endpoint': d['endpoint'] or '', 'prefix': d.get('prefix') or '',
                                                                'enabled': True, 'remark': _tr('交付包导入')}, raw=True)
                res['datasources'][proto] = d['code']
        for proto, did in (opts.get('datasources') or {}).items():
            if did:
                ds_ids[proto] = int(did)
        if not ds_ids or any(p not in ds_ids for p in protos):
            for proto in protos:
                if proto not in ds_ids:
                    d = db.rows(c, 'datasource', protocol=proto, limit=1)
                    if d:
                        ds_ids[proto] = d[0]['id']
        dss = {k: db.get(c, 'datasource', v) for k, v in ds_ids.items()}
        # 设备
        hier = model.Hier(c, plant_id)
        for d in devs:
            if ('设备', d['_row']) in bad_rows or not d.get('code'):
                res['skipped'] += 1
                continue
            _eid, st = model.apply_device(c, d, hier, origin)
            if st == 'new':
                res['devices_new'] += 1
            elif st == 'updated':
                res['devices_updated'] += 1
        res['hierarchy'] = hier.created
        eqs = {e['code']: e for e in db.rows(c, 'equipment')}
        # 点位
        seen = {}
        for p in pts:
            if ('点表', p['_row']) in bad_rows or not p.get('code'):
                res['skipped'] += 1
                continue
            ds = dss.get(p.get('protocol'))
            if not ds:
                res['skipped'] += 1
                continue
            dev = eqs.get(p.get('device')) if p.get('device') else None
            row = model.point_row(p, dev, ds, origin)
            pid, st = model.upsert_point(c, row, status='confirmed' if opts.get('confirm_all') else 'pending', confirmed_by=user)
            seen.setdefault(ds['id'], set()).add(pid)
            if st == 'new':
                res['points_new'] += 1
            elif st == 'updated':
                res['points_updated'] += 1
        if opts.get('remove_missing', origin == 'package'):
            tp = db.metadata.tables['tag_point']
            for dsid, ids in seen.items():
                q = tp.update().where((tp.c.datasource == dsid) & tp.c.id.notin_(ids) & (tp.c.enabled.is_(True)) & tp.c.origin.in_(('package', 'import')))
                res['points_disabled'] += c.execute(q.values(enabled=False, updated_at=clock.now())).rowcount
        # 平面图 + 三维
        if pk:
            if pk['dxf']:
                text = pk['zip'].read(pk['dxf']).decode('utf-8', 'replace')
                res['layout'] = layout_mod.import_dxf(c, text, pk['dxf'], plant_id, user)
                res['layout']['bind'] = layout_mod.autobind(c, only_unplaced=True)
            mdir = pkg_dir('models')
            for n in pk['models']:
                rel = n if n.startswith('models/') else 'models/' + os.path.basename(n)
                safe = re.sub(r'[^A-Za-z0-9_.-]', '_', os.path.basename(rel))
                with open(os.path.join(mdir, safe), 'wb') as f:
                    f.write(pk['zip'].read(n))
                res['models'] += 1
        res['tools'] = ensure_tools(c)
        try:                                         # 点检保养计划等（失败不影响导入；PostgreSQL 要用保存点，否则整个事务作废）
            from ..seed import after_import
            with c.begin_nested():
                after_import(c)
        except Exception:
            import logging
            logging.getLogger('mes').exception(_tr('导入后补点检计划'))
        model.derive_wo_lines(c, only_missing=False)
        audit.audit(c, 'onboard_import', 'onboard_import', None, detail={k: v for k, v in res.items() if k != 'layout'}, target=_tr('导入{v}', v=_tr('交付包') if origin == "package" else _tr('点表')))
        if opts.get('publish', origin == 'package'):
            res['version'] = model.publish(c, opts.get('note') or (_tr('导入交付包') if origin == 'package' else _tr('导入点表')), user)['version']
    model.invalidate()
    from ..collector import COLLECTOR
    for did in set(ds_ids.values()):
        COLLECTOR.restart_threadsafe(did)
    return res


def ensure_tools(c) -> int:
    """刀具台账：模板里语义是 tool_limit / tool_life 的点位（Tools/<刀位>/LifeLimit…）→ 每台设备每个刀位一把刀（寿命等值来了再更新）。"""
    from . import templates as tpl
    from ..schema import TABLES
    tt = TABLES['tool']
    n = 0
    eqs = {e['id']: e for e in db.rows(c, 'equipment')}
    for p in db.rows(c, 'tag_point'):
        if p.get('sem') not in ('tool_limit', 'tool_life') or not p['equipment'] or not p['enabled']:
            continue
        e = eqs.get(p['equipment'])
        tp = model.template(e.get('template') or e['etype']) if e else None
        _tpp, caps = tpl.match_point((tp or {}).get('points'), p['name'])
        code = caps.get('tool') or (p['name'].split('/')[1] if p['name'].count('/') >= 2 else None)
        if not code:
            continue
        if c.execute(sa.select(tt.c.id).where((tt.c.equipment == p['equipment']) & (tt.c.code == code))).scalar():
            continue
        name = re.sub(r'\s*(寿命设定.*|已用件数.*|寿命.*)$', '', p['desc'] or '') or code
        db.insert(c, 'tool', {'code': code, 'name': name, 'equipment': p['equipment'], 'ttype': name.split()[-1] if ' ' in name else name,
                              'life_limit': None, 'used': 0, 'changes': 0, 'status': 'ok', 'last_change': clock.now()}, raw=True)
        n += 1
    return n


def import_points(c, data: bytes, file_name: str, options: dict, user: str) -> dict:
    """单独导入点表 Excel（不是整个交付包）：预览批次。确认走 confirm_points。"""
    from ..schema_factory import onboard_import_t as it
    pt = pointtable.read_xlsx(data, options.get('mapping'))
    mapping_ai = None
    if any(pt['unmapped'].values()) and not options.get('mapping'):
        from . import assist
        mapping_ai = assist.map_columns(pt['headers'], pt['unmapped'])
        if mapping_ai and mapping_ai.get('mapping'):
            pt = pointtable.read_xlsx(data, mapping_ai['mapping'])
    known = {e['code'] for e in db.rows(c, 'equipment')}
    issues = pointtable.validate(pt['devices'], pt['points'], known)
    by_proto = {}
    for p in pt['points']:
        by_proto[p.get('protocol') or '?'] = by_proto.get(p.get('protocol') or '?', 0) + 1
    summary = {'devices': len(pt['devices']), 'points': len(pt['points']), 'by_protocol': by_proto, 'columns': pt['columns'], 'unmapped': pt['unmapped'],
               'ai_mapping': mapping_ai, 'errors': sum(1 for i in issues if i['level'] == 'error'), 'warnings': sum(1 for i in issues if i['level'] == 'warn'),
               'datasources': {p: [{'id': d['id'], 'code': d['code']} for d in db.rows(c, 'datasource', protocol=p)] for p in by_proto}}
    rid = c.execute(it.insert().values(kind='points', file_name=file_name, status='preview', mapping=pt['columns'], summary=summary, issues=issues[:3000],
                                       rows={'devices': pt['devices'], 'points': pt['points'], 'protocols': pt['protocols']}, options=options,
                                       created_by=user, created_at=clock.now())).inserted_primary_key[0]
    return {'id': rid, 'summary': summary, 'issues': issues[:300]}


def confirm_points(c, rid: int, options: dict, user: str) -> dict:
    from ..schema_factory import onboard_import_t as it
    r = c.execute(sa.select(it).where(it.c.id == rid)).mappings().first()
    if not r or r['status'] != 'preview':
        raise ValueError(_tr('导入批次不存在或已处理'))
    opts = {**(r['options'] or {}), **(options or {})}
    rows = r['rows'] or {}
    res = apply(c, rows.get('devices') or [], rows.get('points') or [], rows.get('protocols') or [], r['issues'] or [], opts, user, origin='import')
    c.execute(it.update().where(it.c.id == rid).values(status='confirmed', decided_by=user, decided_at=clock.now(), summary={**(r['summary'] or {}), 'result': res}))
    return res


def b64(data: str) -> bytes:
    if ',' in data[:100] and data.startswith('data:'):
        data = data.split(',', 1)[1]
    return base64.b64decode(data)
