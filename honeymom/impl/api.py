"""实施工具与可配置的接口（/api/impl/…）。权限点：impl.*（导入、行业模板、开局向导）、config.*（字段、表单、流程、报表）。"""
from ..i18n import _tr
import base64
import logging
import urllib.parse

from fastapi import APIRouter, Body, Depends, HTTPException, Request
import sqlalchemy as sa
from fastapi.responses import Response

from .. import audit, db, perms
from ..auth import principal
from ..perms import declare, require
from . import importer as imp

log = logging.getLogger('mes.impl')
router = APIRouter()

P_IMPORT = declare('impl.import.run', '数据导入（导入模板、上传、确认导入）', '实施')
P_IMPORT_VIEW = declare('impl.import.view', '查看所有人的导入记录', '实施')
P_PACK_VIEW = declare('impl.pack.view', '查看行业模板', '实施')
P_PACK_APPLY = declare('impl.pack.apply', '应用行业模板', '实施')
P_SETUP = declare('impl.setup.run', '开局向导（选行业模板、公司信息）', '实施')
P_FIELD = declare('config.field.manage', '自定义字段', '配置')
P_FORM = declare('config.form.manage', '表单配置（顺序、分组、必填、隐藏）', '配置')
P_FLOW = declare('config.flow.manage', '审批流程设计', '配置')
P_REPORT = declare('config.report.design', '设计报表（自己的）', '配置')
P_REPORT_ALL = declare('config.report.manage', '管理所有报表（共享、定时、删除别人的）', '配置')

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _file(data: bytes, name: str, media=XLSX):
    q = urllib.parse.quote(name)
    return Response(data, media_type=media, headers={'Content-Disposition': f"attachment; filename*=UTF-8''{q}"})


async def _upload_body(request: Request):
    """上传：JSON {filename, data: base64} 或原始字节（?filename=）。"""
    ct = request.headers.get('content-type', '')
    raw = await request.body()
    if len(raw) > 60 * 1024 * 1024:
        raise HTTPException(413, _tr('文件太大（最多 40 MB）'))
    if ct.startswith('application/json'):
        import json
        try:
            j = json.loads(raw or b'{}')
            name = j.get('filename') or 'upload.xlsx'
            data = base64.b64decode((j.get('data') or '').split(',')[-1])
            return name, data, j
        except (ValueError, TypeError):
            raise HTTPException(400, _tr('上传内容格式不对'))
    return request.query_params.get('filename') or 'upload.xlsx', raw, {}


# ───────────────────────── 导入 ─────────────────────────
@router.get('/api/impl/importers')
def importers(user=Depends(require(P_IMPORT))):
    from . import packs
    mods = packs.enabled_modules()
    with db.engine.connect() as c:
        out = [x.out(c) for x in imp.all_importers() if not x.module or x.module in mods]
    for o in out:
        o['allowed'] = all(perms.can(user, p) for p in o['perms'])
    return out


@router.get('/api/impl/importers/{key}')
def importer_one(key: str, user=Depends(require(P_IMPORT))):
    with db.engine.connect() as c:
        return imp.get(key).out(c)


@router.get('/api/impl/importers/{key}/template.xlsx')
def template(key: str, user=Depends(require(P_IMPORT))):
    i = imp.get(key)
    return _file(imp.template(key), _tr('导入模板-{label}.xlsx', label=i.label))


@router.get('/api/impl/workbook.xlsx')
def workbook(user=Depends(require(P_IMPORT))):
    from . import packs
    return _file(imp.workbook_template(modules=packs.enabled_modules()), _tr('主数据初始化工作簿.xlsx'))


@router.post('/api/impl/import/{key}/upload')
async def upload(key: str, request: Request, user=Depends(require(P_IMPORT))):
    name, data, j = await _upload_body(request)
    return imp.create_job(key, name, data, user, sheet=j.get('sheet') or request.query_params.get('sheet'))


@router.post('/api/impl/import/workbook')
async def upload_workbook(request: Request, user=Depends(require(P_IMPORT))):
    name, data, _ = await _upload_body(request)
    return {'jobs': imp.create_workbook_jobs(name, data, user)}


@router.get('/api/impl/import/jobs')
def jobs(importer: str = '', limit: int = 100, user=Depends(require(P_IMPORT))):
    return imp.jobs(user, importer, limit)


@router.get('/api/impl/import/jobs/{jid}')
def job(jid: int, user=Depends(require(P_IMPORT))):
    return imp.job_out(jid, user)


@router.put('/api/impl/import/jobs/{jid}')
def job_set(jid: int, body: dict = Body(...), user=Depends(require(P_IMPORT))):
    return imp.set_mapping(jid, body.get('mapping'), body.get('mode'), user)


@router.post('/api/impl/import/jobs/{jid}/validate')
def job_validate(jid: int, body: dict = Body(default={}), user=Depends(require(P_IMPORT))):
    return imp.preview(jid, user, int(body.get('page') or 1), int(body.get('size') or 50), body.get('only') or '')


@router.post('/api/impl/import/jobs/{jid}/ai_map')
def job_ai_map(jid: int, user=Depends(require(P_IMPORT))):
    return imp.ai_map(jid, user)


@router.post('/api/impl/import/jobs/{jid}/commit')
def job_commit(jid: int, body: dict = Body(default={}), user=Depends(require(P_IMPORT))):
    return imp.commit(jid, user, dry_run=bool(body.get('dry_run')), skip_errors=bool(body.get('skip_errors')))


@router.get('/api/impl/import/jobs/{jid}/errors.xlsx')
def job_errors(jid: int, user=Depends(require(P_IMPORT))):
    j = imp.load_job(jid, user)
    return _file(imp.error_report(jid, user), _tr('错误报告-{label}-{jid}.xlsx', label=imp.get(j["importer"]).label, jid=jid))


# ───────────────────────── 自定义字段 ─────────────────────────
def _cf_out(r):
    from ..schema import BY_NAME
    from . import cf
    d = {k: db.jsonable(v) for k, v in dict(r).items()}
    d['ftype_label'] = cf.FTYPES.get(d['ftype'], d['ftype'])
    d['entity_label'] = BY_NAME[d['entity']].label if d['entity'] in BY_NAME else d['entity']
    return d


@router.get('/api/impl/fields')
def cf_list(entity: str = '', user=Depends(require(P_FIELD))):
    from . import cf
    t = cf.table()
    q = sa.select(t).order_by(t.c.entity, t.c.seq, t.c.id)
    if entity:
        q = q.where(t.c.entity == entity)
    with db.engine.connect() as c:
        return [_cf_out(r) for r in c.execute(q).mappings()]


@router.get('/api/impl/fields/meta')
def cf_meta(user=Depends(require(P_FIELD))):
    from ..schema import ENTITIES
    from . import cf
    return {'ftypes': cf.FTYPES, 'entities': [{'name': e.name, 'label': e.label, 'module': e.module} for e in ENTITIES if not e.internal]}


@router.post('/api/impl/fields')
def cf_create(body: dict = Body(...), user=Depends(require(P_FIELD))):
    from . import cf
    try:
        d = cf.validate_def(body)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    t = cf.table()
    now = db.clock_wall()
    with db.engine.begin() as c:
        if c.execute(sa.select(t.c.id).where((t.c.entity == d['entity']) & (t.c.key == d['key']))).first():
            raise HTTPException(400, _tr('{key} 已经有了', key=d['key']))
        cf.ensure_column(c, d['entity'])
        fid = c.execute(t.insert().values(**d, created_by=user['username'], created_at=now, updated_at=now)).inserted_primary_key[0]
        cf.ensure_index(c, {**d, 'id': fid})
        audit.audit(c, 'custom_field_create', 'custom_field', fid, detail=d, target=f"cf:{d['entity']}.{d['key']}", user=user['username'])
    cf.invalidate()
    return {'ok': True, 'id': fid, 'key': d['key']}


@router.put('/api/impl/fields/{fid}')
def cf_update(fid: int, body: dict = Body(...), user=Depends(require(P_FIELD))):
    from . import cf
    t = cf.table()
    with db.engine.begin() as c:
        cur = c.execute(sa.select(t).where(t.c.id == fid)).mappings().first()
        if not cur:
            raise HTTPException(404, _tr('字段不存在'))
        try:
            d = cf.validate_def(body, dict(cur))
        except ValueError as ex:
            raise HTTPException(400, str(ex))
        for k in ('entity', 'key', 'ftype'):
            d.pop(k)
        c.execute(t.update().where(t.c.id == fid).values(**d, updated_at=db.clock_wall()))
        cf.ensure_index(c, {**dict(cur), **d})
        audit.audit(c, 'custom_field_update', 'custom_field', fid, diff=audit.diff_of(dict(cur), {**dict(cur), **d}),
                    target=f"cf:{cur['entity']}.{cur['key']}", user=user['username'])
    cf.invalidate()
    return {'ok': True}


@router.delete('/api/impl/fields/{fid}')
def cf_delete(fid: int, user=Depends(require(P_FIELD))):
    """停用（已有数据保留在 ext 里，重新启用就回来）。"""
    return cf_update(fid, {'active': False}, user)


# ───────────────────────── 表单配置 ─────────────────────────
@router.get('/api/impl/forms/{entity}')
def form_get(entity: str, user=Depends(require(P_FORM))):
    from ..perms import roles
    from ..schema import BY_NAME
    from . import cf, forms
    if entity not in BY_NAME:
        raise HTTPException(404, _tr('没有这个实体'))
    e = BY_NAME[entity]
    fields = [{'name': f.name, 'label': f.label, 't': f.t, 'req': f.req, 'ro': f.ro} for f in e.fields if f.name not in ('password_hash',)]
    fields += [{'name': d['key'], 'label': d['label'], 't': cf.SPEC_T.get(d['ftype']), 'req': bool(d.get('required')), 'cf': True} for d in cf.fields(entity)]
    return {'entity': entity, 'label': e.label, 'fields': fields, 'config': forms.get(entity), 'roles': {k: v['name'] for k, v in roles().items()}}


@router.put('/api/impl/forms/{entity}')
def form_put(entity: str, body: dict = Body(...), user=Depends(require(P_FORM))):
    from . import forms
    try:
        return {'ok': True, 'config': forms.save(entity, body.get('config') or body, user)}
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@router.delete('/api/impl/forms/{entity}')
def form_reset(entity: str, user=Depends(require(P_FORM))):
    from . import forms
    forms.reset(entity, user)
    return {'ok': True}


# ───────────────────────── 审批流程 ─────────────────────────
@router.get('/api/impl/flows')
def flow_list(user=Depends(require(P_FLOW))):
    from ..schema import BY_NAME
    from . import flows
    t = flows.T('approval_flow')
    it = flows.T('approval_flow_inst')
    with db.engine.connect() as c:
        rows = [dict(r) for r in c.execute(sa.select(t).order_by(t.c.id)).mappings()]
        cnt = dict(c.execute(sa.select(it.c.flow, sa.func.count()).group_by(it.c.flow)).all())
    for r in rows:
        r['entity_label'] = BY_NAME[r['entity']].label if r['entity'] in BY_NAME else r['entity']
        r['action_label'] = flows.actions_of(r['entity']).get(r['action'], r['action'])
        r['condition_text'] = flows.cond_text(r['condition'], r['entity'])
        r['instances'] = cnt.get(r['id'], 0)
    return [{k: db.jsonable(v) for k, v in r.items()} for r in rows]


@router.get('/api/impl/flows/meta')
def flow_meta(user=Depends(require(P_FLOW))):
    from ..perms import roles
    from ..schema import ENTITIES
    from . import cf, flows
    ents = []
    for e in ENTITIES:
        if e.internal:
            continue
        fs = [{'name': f.name, 'label': f.label, 't': f.t, 'enum': f.enum} for f in e.fields if f.t not in ('json', 'text') and f.name != 'password_hash']
        fs += [{'name': d['key'], 'label': d['label'], 't': cf.SPEC_T.get(d['ftype']), 'enum': cf.enum_of(d) if d['ftype'] == 'enum' else None}
               for d in cf.fields(e.name)]
        ents.append({'name': e.name, 'label': e.label, 'module': e.module, 'actions': flows.actions_of(e.name), 'fields': fs})
    for (en, a), (lab, _) in flows.EXECUTORS.items():
        if not any(x['name'] == en for x in ents):
            ents.append({'name': en, 'label': en, 'module': '服务', 'actions': {a: lab}, 'fields': []})
    with db.engine.connect() as c:
        users = [{'username': u['username'], 'name': u['name']} for u in db.rows(c, 'sys_user') if u['active']]
    return {'entities': ents, 'ops': flows.OPS, 'roles': {k: v['name'] for k, v in roles().items()}, 'users': users}


@router.post('/api/impl/flows')
def flow_create(body: dict = Body(...), user=Depends(require(P_FLOW))):
    return _flow_save(None, body, user)


@router.put('/api/impl/flows/{fid}')
def flow_update(fid: int, body: dict = Body(...), user=Depends(require(P_FLOW))):
    return _flow_save(fid, body, user)


def _flow_save(fid, body, user):
    import secrets
    from . import flows
    try:
        d = flows.validate_flow(body)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    t = flows.T('approval_flow')
    now = db.clock_wall()
    with db.engine.begin() as c:
        if fid:
            cur = c.execute(sa.select(t).where(t.c.id == fid)).mappings().first()
            if not cur:
                raise HTTPException(404, _tr('流程不存在'))
            c.execute(t.update().where(t.c.id == fid).values(**d, updated_at=now))
            audit.audit(c, 'flow_update', 'approval_flow', fid, diff=audit.diff_of(dict(cur), {**dict(cur), **d}), user=user['username'])
        else:
            code = str(body.get('code') or '').strip() or f"FL-{d['entity']}-{d['action']}-{secrets.token_hex(2)}"
            fid = c.execute(t.insert().values(code=code[:60], **d, created_by=user['username'], created_at=now, updated_at=now)).inserted_primary_key[0]
            audit.audit(c, 'flow_create', 'approval_flow', fid, detail=d, user=user['username'])
    flows.invalidate()
    return {'ok': True, 'id': fid}


@router.delete('/api/impl/flows/{fid}')
def flow_delete(fid: int, user=Depends(require(P_FLOW))):
    from . import flows
    t = flows.T('approval_flow')
    with db.engine.begin() as c:
        cur = c.execute(sa.select(t).where(t.c.id == fid)).mappings().first()
        if not cur:
            raise HTTPException(404, _tr('流程不存在'))
        c.execute(t.delete().where(t.c.id == fid))
        audit.audit(c, 'flow_delete', 'approval_flow', fid, detail={k: db.jsonable(v) for k, v in dict(cur).items()}, user=user['username'])
    flows.invalidate()
    return {'ok': True}


@router.post('/api/impl/flows/test')
def flow_test(body: dict = Body(...), user=Depends(require(P_FLOW))):
    """试算：给一条记录（字段 → 值），看会不会触发、要走哪几步。"""
    from . import flows
    try:
        d = flows.validate_flow(body.get('flow') or {})
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    rec = body.get('record') or {}
    hit = flows.cond_ok(d['condition'], rec)
    steps = [s for s in d['steps'] if flows.cond_ok(s.get('condition'), rec)] if hit else []
    return {'triggered': hit and bool(steps), 'steps': steps, 'condition_text': flows.cond_text(d['condition'], d['entity'])}


@router.get('/api/impl/flows/instances')
def flow_instances(status: str = '', limit: int = 100, user=Depends(require(P_FLOW))):
    from . import flows
    it = flows.T('approval_flow_inst')
    q = sa.select(it).order_by(it.c.id.desc()).limit(min(500, limit))
    if status:
        q = q.where(it.c.status == status)
    with db.engine.connect() as c:
        return [{k: db.jsonable(v) for k, v in dict(r).items()} for r in c.execute(q).mappings()]


@router.get('/api/impl/flows/my')
def flow_my(user=Depends(principal)):
    """我提交的审批（任何登录用户）。"""
    from . import flows
    it = flows.T('approval_flow_inst')
    with db.engine.connect() as c:
        return [{k: db.jsonable(v) for k, v in dict(r).items()}
                for r in c.execute(sa.select(it).where(it.c.requester == user['username']).order_by(it.c.id.desc()).limit(100)).mappings()]


@router.post('/api/impl/flows/instances/{iid}/cancel')
def flow_cancel(iid: int, user=Depends(principal)):
    from . import flows
    return flows.cancel(iid, user)


# ───────────────────────── 报表设计器 ─────────────────────────
@router.get('/api/impl/reports/datasets')
def rp_datasets(user=Depends(principal)):
    from . import reports
    return {'datasets': reports.list_datasets(user), 'aggs': reports.AGGS, 'buckets': reports.BUCKETS, 'ops': reports.FOPS, 'charts': reports.CHARTS,
            'roles': {k: v['name'] for k, v in perms.roles().items()}, 'can_design': perms.can(user, P_REPORT), 'can_manage': perms.can(user, P_REPORT_ALL)}


@router.post('/api/impl/reports/run')
def rp_run(body: dict = Body(...), user=Depends(require(P_REPORT))):
    from . import reports
    return reports.run(body.get('dataset'), body.get('config') or {}, user, body.get('limit'))


@router.get('/api/impl/reports')
def rp_list(user=Depends(principal)):
    from . import reports
    return reports.list_reports(user)


@router.post('/api/impl/reports')
def rp_create(body: dict = Body(...), user=Depends(require(P_REPORT))):
    from . import reports
    return {'ok': True, 'id': reports.save(None, body, user)}


@router.get('/api/impl/reports/widgets')
def rp_widgets(user=Depends(principal)):
    from . import reports
    return reports.widgets(user)


@router.get('/api/impl/reports/exports/{eid}')
def rp_export_file(eid: int, user=Depends(principal)):
    from . import reports
    data, name, fmt = reports.export_file(eid, user)
    return _file(data, name, 'application/pdf' if fmt == 'pdf' else XLSX)


@router.get('/api/impl/reports/{rid}')
def rp_get(rid: int, user=Depends(principal)):
    from . import reports
    return reports.out(reports.load(rid, user), user)


@router.put('/api/impl/reports/{rid}')
def rp_update(rid: int, body: dict = Body(...), user=Depends(principal)):
    from . import reports
    return {'ok': True, 'id': reports.save(rid, body, user)}


@router.delete('/api/impl/reports/{rid}')
def rp_delete(rid: int, user=Depends(principal)):
    from . import reports
    reports.delete(rid, user)
    return {'ok': True}


@router.get('/api/impl/reports/{rid}/data')
def rp_data(rid: int, user=Depends(principal)):
    from . import reports
    return reports.run_saved(rid, user)


@router.get('/api/impl/reports/{rid}/export.{fmt}')
def rp_export(rid: int, fmt: str, user=Depends(principal)):
    from . import reports
    if fmt not in ('xlsx', 'pdf'):
        raise HTTPException(400, _tr('只能导出 xlsx / pdf'))
    e = reports.export(rid, fmt, user)
    return _file(e['data'], f"{e['name']}.{fmt}", 'application/pdf' if fmt == 'pdf' else XLSX)


@router.post('/api/impl/reports/{rid}/pin')
def rp_pin(rid: int, body: dict = Body(default={}), user=Depends(principal)):
    from . import reports
    return reports.pin(rid, user, bool(body.get('on', True)))


@router.post('/api/impl/reports/{rid}/run_schedule')
def rp_run_schedule(rid: int, user=Depends(require(P_REPORT))):
    """立即按定时设置跑一次（生成文件 + 发通知），用来试定时。"""
    from . import reports
    r = reports.load(rid, user, edit=True)
    if not r.get('schedule'):
        raise HTTPException(400, _tr('这张报表没有设置定时'))
    e = reports.run_schedule(r)
    return {'ok': True, 'export': e['id'], 'rows': e['rows']}


# ───────────────────────── 行业模板 ─────────────────────────
@router.get('/api/impl/packs')
def pk_list(user=Depends(require(P_PACK_VIEW))):
    from . import packs
    return {'packs': packs.list_packs(), 'history': packs.history(), 'kpis': packs.kpis(), 'modules': sorted(packs.enabled_modules()),
            'can_apply': perms.can(user, P_PACK_APPLY)}


@router.get('/api/impl/packs/{code}')
def pk_one(code: str, user=Depends(require(P_PACK_VIEW))):
    from . import packs
    return packs.pack_out(code)


@router.post('/api/impl/packs/preview')
def pk_preview(body: dict = Body(...), user=Depends(require(P_PACK_VIEW))):
    from . import packs
    codes = body.get('packs') or []
    if not codes:
        raise HTTPException(400, _tr('请选择行业模板'))
    return packs.preview(codes, bool(body.get('demo')), user)


@router.post('/api/impl/packs/apply')
def pk_apply(body: dict = Body(...), user=Depends(require(P_PACK_APPLY))):
    from . import packs
    codes = body.get('packs') or []
    if not codes:
        raise HTTPException(400, _tr('请选择行业模板'))
    return packs.apply(codes, bool(body.get('demo')), user)


# ───────────────────────── 开局向导 / 实施状态 ─────────────────────────
@router.get('/api/impl/setup/state')
def setup_state(user=Depends(principal)):
    """任何登录用户：开局向导是否待完成、启用的可选模块（菜单用）。"""
    from . import packs
    with db.engine.connect() as c:
        st = db.setting(c, 'setup_state') or 'done'
        company = db.setting(c, 'company', '')
    return {'state': st, 'pending': st == 'pending', 'modules': sorted(packs.enabled_modules()), 'packs': packs.applied_codes(), 'company': company,
            'can_setup': perms.can(user, P_SETUP)}


@router.get('/api/impl/setup')
def setup_get(user=Depends(require(P_SETUP))):
    from . import packs
    with db.engine.connect() as c:
        plant = db.rows(c, 'plant', limit=1)
        st = db.setting(c, 'setup_state') or 'done'
        info = {'company': db.setting(c, 'company', ''), 'plant': {k: plant[0][k] for k in ('code', 'name', 'address')} if plant else None,
                'counts': {x.key: x.out(c).get('existing') for x in imp.all_importers()}}
    return {'state': st, 'packs': [p for p in packs.list_packs() if p['kind'] != 'base'], 'applied': packs.applied_codes(), **info,
            'onboard': _onboard_link()}


def _onboard_link():
    """设备接入（M2 的接入中心）：有就用，没有退回设备台账。"""
    try:
        from ..schema import BY_NAME
        if 'device_template' in BY_NAME:
            return '/factory/onboard'
    except Exception:
        pass
    return '/e/equipment'


@router.post('/api/impl/setup/company')
def setup_company(body: dict = Body(...), user=Depends(require(P_SETUP))):
    name = str(body.get('company') or '').strip()
    if not name:
        raise HTTPException(400, _tr('请填写公司名称'))
    with db.engine.begin() as c:
        db.set_setting(c, 'company', name, _tr('公司名称'))
        pc, pn = str(body.get('plant_code') or '').strip(), str(body.get('plant_name') or '').strip()
        if pc and pn:
            cur = db.get(c, 'plant', code=pc)
            if cur:
                db.update(c, 'plant', cur['id'], {'name': pn, 'address': body.get('address') or cur.get('address')})
            else:
                db.insert(c, 'plant', {'code': pc, 'name': pn, 'address': body.get('address') or ''})
        audit.audit(c, 'setup_company', 'setting', None, detail={'company': name, 'plant': pc or None}, user=user['username'])
    return {'ok': True}


@router.post('/api/impl/setup/finish')
def setup_finish(user=Depends(require(P_SETUP))):
    with db.engine.begin() as c:
        db.set_setting(c, 'setup_state', 'done', _tr('开局向导（行业模板 → 公司信息 → 导入主数据 → 设备接入）'))
        audit.audit(c, 'setup_finish', 'setting', None, user=user['username'])
    return {'ok': True}


# ───────────────────────── 实施向导（任务清单） ─────────────────────────
TASK_TEMPLATE = [
    ('survey', 10, '各部门调研（生产 / 质量 / 设备 / 仓储 / 计划）', '实施模板/01_调研表.xlsx', '', 0, 7),
    ('survey', 20, '现场设备与网络普查（设备清单、控制器、点位）', '实施模板/01_调研表.xlsx', '', 0, 7),
    ('blueprint', 30, '编写并评审业务蓝图', '实施模板/02_业务蓝图.docx', '', 7, 14),
    ('blueprint', 40, '确定行业模板、可选模块、审批流程和报表清单', '实施模板/02_业务蓝图.docx', '/impl/packs', 7, 14),
    ('master', 50, '编制主数据清单与初始化计划', '实施模板/03_主数据清单与初始化计划.xlsx', '', 14, 18),
    ('master', 60, '收集并导入主数据（物料、BOM、工艺、人员、仓库……）', '实施模板/03_主数据清单与初始化计划.xlsx', '/impl/import', 14, 28),
    ('config', 70, '配置自定义字段与表单', '', '/impl/fields', 18, 24),
    ('config', 80, '配置审批流程', '', '/impl/flows', 18, 24),
    ('config', 90, '配置角色权限与数据范围、账号', '', '/system/roles', 18, 24),
    ('config', 100, '设计报表并设定定时导出', '', '/impl/reports', 20, 28),
    ('onboard', 110, '设备接入与点表对点、联调验收', '', '/e/equipment', 14, 35),
    ('train', 120, '分角色培训（操作工、班组长、质检、设备、计划、管理员）', '实施模板/06_培训计划.xlsx', '', 28, 35),
    ('master', 130, '导入期初库存与在制工单（切换前一天）', '实施模板/03_主数据清单与初始化计划.xlsx', '/impl/import', 34, 35),
    ('golive', 140, '上线检查（数据、权限、设备、备份、应急）', '实施模板/04_上线检查清单.xlsx', '', 35, 36),
    ('trial', 150, '试运行（双轨 2–4 周）与问题跟踪', '实施模板/05_试运行与验收单.docx', '', 36, 64),
    ('accept', 160, '验收与签字', '实施模板/05_试运行与验收单.docx', '', 64, 70),
]


@router.post('/api/impl/tasks/init')
def tasks_init(body: dict = Body(default={}), user=Depends(require(perms.entity_perm('impl_task', 'create')))):
    """按标准实施方法建任务清单（已有同名任务的项目跳过）：调研 → 蓝图 → 主数据 → 配置 → 接入 → 培训 → 上线 → 试运行 → 验收。"""
    from datetime import timedelta
    project = str(body.get('project') or _tr('默认项目')).strip()[:100]
    start = db.parse_dt(body.get('start')) or db.clock.now()
    n = 0
    with db.engine.begin() as c:
        have = {r['title'] for r in db.rows(c, 'impl_task', project=project)}
        for phase, seq, title, deliv, link, d0, d1 in TASK_TEMPLATE:
            if title in have:
                continue
            db.insert(c, 'impl_task', {'project': project, 'phase': phase, 'seq': seq, 'title': title, 'deliverable': ('docs/' + deliv) if deliv else '',
                                       'link': link, 'due': (start + timedelta(days=d1)).date(), 'status': 'todo', 'owner': body.get('owner') or ''})
            n += 1
    return {'ok': True, 'created': n, 'project': project}


@router.get('/api/impl/tasks/summary')
def tasks_summary(project: str = '', user=Depends(require(perms.entity_perm('impl_task', 'read')))):
    from ..schema_impl import TASK_PHASE, TASK_STATUS
    with db.engine.connect() as c:
        rows = db.rows(c, 'impl_task', **({'project': project} if project else {}))
        projects = sorted({r['project'] for r in db.rows(c, 'impl_task')})
        counts = {x.key: x.out(c).get('existing') for x in imp.all_importers()}
    today = db.clock.now().date()
    phases = []
    for k, lab in TASK_PHASE.items():
        rs = [r for r in rows if r['phase'] == k]
        if not rs:
            continue
        done = sum(1 for r in rs if r['status'] in ('done', 'na'))
        phases.append({'phase': k, 'label': lab, 'total': len(rs), 'done': done, 'overdue': sum(1 for r in rs if r['status'] not in ('done', 'na') and r['due'] and r['due'] < today)})
    tot = len(rows)
    done = sum(1 for r in rows if r['status'] in ('done', 'na'))
    return {'projects': projects, 'phases': phases, 'total': tot, 'done': done, 'progress': round(done / tot, 3) if tot else 0,
            'overdue': sum(p['overdue'] for p in phases), 'status_labels': TASK_STATUS, 'phase_labels': TASK_PHASE, 'master_counts': counts}


# ───────────────────────── 自定义字段的文件 ─────────────────────────
def _files_dir():
    import os
    d = os.path.join(db.DATA, 'cf_files')
    os.makedirs(d, exist_ok=True)
    return d


@router.post('/api/impl/files')
async def cf_file_upload(request: Request, user=Depends(principal)):
    """自定义字段"文件"类型的上传：返回 {name, token, size}，作为字段值保存。最多 20 MB。"""
    import json as _json
    import os
    import re
    import secrets
    name, data, _ = await _upload_body(request)
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, _tr('文件太大（最多 20 MB）'))
    tok_ = secrets.token_hex(12)
    safe = re.sub(r'[\\/:*?"<>|]', '_', os.path.basename(name or 'file'))[:120]
    with open(os.path.join(_files_dir(), tok_), 'wb') as f:
        f.write(data)
    with open(os.path.join(_files_dir(), tok_ + '.json'), 'w', encoding='utf-8') as f:
        _json.dump({'name': safe, 'size': len(data), 'by': user.get('username'), 'at': db.jsonable(db.clock_wall())}, f, ensure_ascii=False)
    return {'name': safe, 'token': tok_, 'size': len(data)}


@router.get('/api/impl/files/{token}')
def cf_file_get(token: str, user=Depends(principal)):
    import json as _json
    import mimetypes
    import os
    import re
    if not re.match(r'^[0-9a-f]{24}$', token):
        raise HTTPException(404, _tr('文件不存在'))
    p = os.path.join(_files_dir(), token)
    if not os.path.isfile(p):
        raise HTTPException(404, _tr('文件不存在'))
    try:
        meta = _json.load(open(p + '.json', encoding='utf-8'))
    except (OSError, ValueError):
        meta = {'name': token}
    with open(p, 'rb') as f:
        return _file(f.read(), meta['name'], mimetypes.guess_type(meta['name'])[0] or 'application/octet-stream')


def on_startup(app):
    """后台任务（报表定时导出）在 reports.py 里挂。"""
    try:
        from . import reports
        reports.install(app)
    except ImportError:
        pass
