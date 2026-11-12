"""文件受控与工艺变更（ECN）。

受控文件 ctrl_doc → 版本 ctrl_doc_rev（草稿 → 审核 → 批准（电子签名）→ 生效 → 作废）。版本的内容是一条业务记录（target_entity/target_id）：
作业指导书 / 图纸 = kb_doc，NC 程序 = nc_program，控制计划 = control_plan，PFMEA = pfmea。新版本生效时旧版本自动作废；
现场（工位终端、手机端、知识库）只显示生效版本：kb_doc.ctrl_status 为空（不受控）或 effective 的才出现（shopfloor.instruction_for、drawings.kb_list）。
控制计划生效时同步检验模板（iatf.sync_inspection），所以控制计划的修改经 ECN 生效后传到检验录入。

ECN：申请 → 影响分析（自动列出受影响的工艺路线、BOM、未完工单、在制品、库存、受控文件、检验模板、委外单、未发货包装）→ 提交批准 →
批准（电子签名；内置 ERP / 实施工具的审批流程上线后可接到那里）→ 实施任务 → 生效（关联的新版本文件一起生效，记录新旧版本）。
在制工单按 wo_policy：照旧生产 / 停工待处置（报工闸门 X-Gate: ecn，逐张"解除停工"）/ 做完再切换。
"""
from ..i18n import _tr
import os
import re
import shutil

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db, perms
from ..auth import principal
from ..crud import HOOKS, action, attach_labels, out
from ..db import clock
from ..schema import TABLES
from . import esign
from .gates import Gate

router = APIRouter()
PERM_ALL = perms.declare('doc.view_all', '查看受控文件的全部版本（草稿 / 作废）', '文件受控')
TARGET = {'wi': 'kb_doc', 'drawing': 'kb_doc', 'other': 'kb_doc', 'nc': 'nc_program', 'control_plan': 'control_plan', 'pfmea': 'pfmea'}


def next_rev(rev: str) -> str:
    """A → B，Z → AA，V3 → V4，1 → 2，1.2 → 1.3。"""
    rev = (rev or '').strip()
    m = re.match(r'^(.*?)(\d+)$', rev)
    if m:
        return f"{m.group(1)}{int(m.group(2)) + 1}"
    if rev and rev.isalpha() and rev.isupper():
        s = list(rev)
        i = len(s) - 1
        while i >= 0:
            if s[i] != 'Z':
                s[i] = chr(ord(s[i]) + 1)
                return ''.join(s)
            s[i] = 'A'
            i -= 1
        return 'A' + ''.join(s)
    return (rev + '.1') if rev else 'B'


def _set_target_state(c, ent, tid, state, rev=None):
    if not tid:
        return
    if ent == 'kb_doc':
        vals = {'ctrl_status': state}
        if rev:
            vals['version'] = rev
        db.update(c, 'kb_doc', tid, vals, raw=True)
    elif ent == 'nc_program':
        st = {'effective': 'released', 'obsolete': 'obsolete', 'approved': 'approved'}.get(state)
        if st:
            vals = {'status': st}
            if st == 'released':
                vals['released_at'] = clock.now()
            db.update(c, 'nc_program', tid, vals, raw=True)
    elif ent == 'control_plan':
        db.update(c, 'control_plan', tid, {'status': state}, raw=True)
        if state == 'effective':
            from .iatf import sync_inspection
            sync_inspection(c, db.get(c, 'control_plan', tid))
    elif ent == 'pfmea':
        st = {'effective': 'approved', 'obsolete': 'obsolete', 'review': 'review', 'draft': 'draft', 'approved': 'approved'}.get(state)
        if st:
            db.update(c, 'pfmea', tid, {'status': st}, raw=True)


def put_under_control(c, ent, tid, title, dtype, product, process, rev, user, ecn=None):
    """把一条已有记录纳入文件受控：建受控文件（已有就用原来的）+ 一个草稿版本。返回 {doc, doc_code, rev, rev_id}。"""
    rec = db.get(c, ent, tid)
    if not rec:
        raise HTTPException(404, _tr('记录不存在'))
    did = rec.get('ctrl_doc')
    if not did:
        did = db.insert(c, 'ctrl_doc', {'title': title, 'dtype': dtype, 'product': product, 'process': process, 'owner': user.get('name') or user['username'],
                                        'status': 'draft'}, raw=True)
        db.update(c, ent, tid, {'ctrl_doc': did}, raw=True)
    rid = db.insert(c, 'ctrl_doc_rev', {'doc': did, 'rev': rev, 'status': 'draft', 'target_entity': ent, 'target_id': tid, 'ecn': ecn,
                                        'author': user.get('name') or user['username'], 'change_note': _tr('纳入受控')}, raw=True)
    _set_target_state(c, ent, tid, 'draft')
    d = db.get(c, 'ctrl_doc', did)
    return {'doc': did, 'doc_code': d['code'], 'rev': rev, 'rev_id': rid}


def _copy_target(c, ent, tid, rev, did):
    """升版：复制内容记录（知识库连附件、控制计划连明细、PFMEA 连条目）。"""
    src = db.get(c, ent, tid)
    data = {k: v for k, v in src.items() if k not in ('id', 'created_at', 'updated_at')}
    if ent == 'kb_doc':
        data.update(version=rev, ctrl_status='draft', ctrl_doc=did, updated=clock.now())
        nid = db.insert(c, 'kb_doc', data, raw=True)
        if src['file_name']:
            from .drawings import file_path
            p = file_path(src)
            if os.path.isfile(p):
                shutil.copyfile(p, file_path({**src, 'id': nid}))
        return nid
    if ent == 'nc_program':
        base = re.sub(r'-[^-]+$', '', src['code']) if '-' in (src['code'] or '') else src['code']
        code, i = f'{base}-{rev}', 1
        while db.get(c, 'nc_program', code=code):
            i += 1
            code = f'{base}-{rev}.{i}'
        data.update(code=code, version=rev, status='draft', approved_by=None, released_at=None, ctrl_doc=did)
        return db.insert(c, 'nc_program', data, raw=True)
    if ent == 'control_plan':
        data.update(code=None, rev=rev, status='draft', ctrl_doc=did)
        nid = db.insert(c, 'control_plan', data, raw=True)
        for it in db.rows(c, 'cp_item', order=TABLES['cp_item'].c.seq, plan=tid):
            db.insert(c, 'cp_item', {**{k: v for k, v in it.items() if k not in ('id', 'created_at', 'updated_at')}, 'plan': nid, 'insp_item': None}, raw=True)
        return nid
    if ent == 'pfmea':
        data.update(code=None, rev=rev, status='draft')
        nid = db.insert(c, 'pfmea', data, raw=True)
        for it in db.rows(c, 'pfmea_row', order=TABLES['pfmea_row'].c.seq, pfmea=tid):
            db.insert(c, 'pfmea_row', {**{k: v for k, v in it.items() if k not in ('id', 'created_at', 'updated_at')}, 'pfmea': nid}, raw=True)
        return nid
    raise HTTPException(400, _tr('不支持升版的内容类型 {ent}', ent=ent))


def revise(c, doc, user, ecn=None, note=''):
    rt = TABLES['ctrl_doc_rev']
    revs = db.rows(c, 'ctrl_doc_rev', order=rt.c.id.desc(), doc=doc['id'])
    if not revs:
        raise HTTPException(400, _tr('这份文件还没有任何版本：先从知识库 / NC 程序 / 控制计划"纳入文件受控"'))
    open_ = [r for r in revs if r['status'] in ('draft', 'review', 'approved')]
    if open_:
        r = open_[0]
        if ecn and not r['ecn']:
            db.update(c, 'ctrl_doc_rev', r['id'], {'ecn': ecn}, raw=True)
        return r, False
    base = next((r for r in revs if r['status'] == 'effective'), revs[0])
    new = next_rev(base['rev'])
    tid = _copy_target(c, base['target_entity'], base['target_id'], new, doc['id'])
    rid = db.insert(c, 'ctrl_doc_rev', {'doc': doc['id'], 'rev': new, 'status': 'draft', 'target_entity': base['target_entity'], 'target_id': tid, 'ecn': ecn,
                                        'author': user.get('name') or user['username'], 'change_note': note or _tr('由 {rev} 升版', rev=base['rev'])}, raw=True)
    return db.get(c, 'ctrl_doc_rev', rid), True


@action('ctrl_doc', 'revise')
def _doc_revise(c, doc, body, user):
    r, new = revise(c, doc, user, ecn=int(body['ecn']) if body.get('ecn') else None, note=body.get('note') or '')
    return {'id': r['id'], 'msg': _tr('已建新版本 {rev}（草稿）', rev=r['rev']) if new else _tr('已有未生效的版本 {rev}（{status}），在它上面改', rev=r['rev'], status=r['status'])}


def make_effective(c, rev, user):
    """版本生效：同一文件原来的生效版本作废；内容记录状态跟着变（知识库 / NC 程序 / 控制计划 → 同步检验模板 / PFMEA）。"""
    now = clock.now()
    rt = TABLES['ctrl_doc_rev']
    old = db.rows(c, 'ctrl_doc_rev', rt.c.id != rev['id'], rt.c.status == 'effective', doc=rev['doc'])
    for o in old:
        db.update(c, 'ctrl_doc_rev', o['id'], {'status': 'obsolete', 'obsolete_at': now}, raw=True)
        _set_target_state(c, o['target_entity'], o['target_id'], 'obsolete')
    db.update(c, 'ctrl_doc_rev', rev['id'], {'status': 'effective', 'effective_at': now}, raw=True)
    _set_target_state(c, rev['target_entity'], rev['target_id'], 'effective', rev['rev'])
    db.update(c, 'ctrl_doc', rev['doc'], {'cur_rev': rev['rev'], 'cur_rev_id': rev['id'], 'status': 'effective'}, raw=True)
    return [o['rev'] for o in old]


def _rev_label(c, r):
    d = db.get(c, 'ctrl_doc', r['doc'])
    return _tr('{code} {title} 版本 {rev}', code=d['code'], title=d['title'], rev=r['rev'])


@action('ctrl_doc_rev', 'submit')
def _rev_submit(c, r, body, user):
    if r['status'] != 'draft':
        raise HTTPException(400, _tr('只有草稿可以提交审核'))
    db.update(c, 'ctrl_doc_rev', r['id'], {'status': 'review'}, raw=True)
    _set_target_state(c, r['target_entity'], r['target_id'], 'review')
    return {'msg': _tr('已提交审核')}


@action('ctrl_doc_rev', 'review')
def _rev_review(c, r, body, user):
    if r['status'] != 'review':
        raise HTTPException(400, _tr('不在审核中'))
    db.update(c, 'ctrl_doc_rev', r['id'], {'reviewed_by': user.get('name') or user['username']}, raw=True)
    return {'msg': _tr('已审核，等待批准')}


@action('ctrl_doc_rev', 'approve')
def _rev_approve(c, r, body, user):
    if r['status'] != 'review':
        raise HTTPException(400, _tr('先提交审核，再批准'))
    sig = esign.require(c, user, body, 'ctrl_doc_rev', r['id'], 'approve', _rev_label(c, r))
    db.update(c, 'ctrl_doc_rev', r['id'], {'status': 'approved', 'approved_by': (sig or {}).get('name') or user.get('name') or user['username'],
                                           'approved_at': clock.now(), 'reviewed_by': r['reviewed_by'] or user.get('name') or user['username']}, raw=True)
    _set_target_state(c, r['target_entity'], r['target_id'], 'approved')
    ecn = db.get(c, 'ecn', r['ecn']) if r['ecn'] else None
    if ecn and ecn['status'] != 'effective':
        return {'msg': _tr('已批准；随 {code} 一起生效', code=ecn['code'])}
    if not r['effective_date'] or r['effective_date'] <= clock.now().date():
        old = make_effective(c, db.get(c, 'ctrl_doc_rev', r['id']), user)
        return {'msg': _tr('已批准并生效') + (_tr('（{join} 作废）', join=_tr('、').join(old)) if old else '')}
    return {'msg': _tr('已批准，{effective_date} 生效', effective_date=r['effective_date'])}


@action('ctrl_doc_rev', 'effective')
def _rev_effective(c, r, body, user):
    if r['status'] != 'approved':
        raise HTTPException(400, _tr('只有已批准的版本可以生效'))
    ecn = db.get(c, 'ecn', r['ecn']) if r['ecn'] else None
    if ecn and ecn['status'] != 'effective':
        raise HTTPException(409, _tr('这个版本随 {code} 生效：请在 ECN 上点“生效”', code=ecn['code']))
    old = make_effective(c, r, user)
    return {'msg': _tr('已生效') + (_tr('（{join} 作废）', join=_tr('、').join(old)) if old else '')}


@action('ctrl_doc_rev', 'reject')
def _rev_reject(c, r, body, user):
    if r['status'] not in ('review', 'approved'):
        raise HTTPException(400, _tr('只有审核中 / 已批准未生效的版本可以退回'))
    db.update(c, 'ctrl_doc_rev', r['id'], {'status': 'draft', 'change_note': ((r['change_note'] or '') + _tr('\n退回：{v}', v=body.get('reason') or '')).strip()}, raw=True)
    _set_target_state(c, r['target_entity'], r['target_id'], 'draft')
    return {'msg': _tr('已退回草稿')}


@action('ctrl_doc_rev', 'obsolete')
def _rev_obsolete(c, r, body, user):
    if r['status'] == 'obsolete':
        return {'msg': _tr('已经作废')}
    db.update(c, 'ctrl_doc_rev', r['id'], {'status': 'obsolete', 'obsolete_at': clock.now()}, raw=True)
    _set_target_state(c, r['target_entity'], r['target_id'], 'obsolete')
    d = db.get(c, 'ctrl_doc', r['doc'])
    if d['cur_rev_id'] == r['id']:
        db.update(c, 'ctrl_doc', d['id'], {'status': 'obsolete', 'cur_rev': None, 'cur_rev_id': None}, raw=True)
    return {'msg': _tr('已作废（现场不再显示）')}


def tick():
    """到了生效日期的已批准版本自动生效（随 ECN 的除外）。"""
    rt = TABLES['ctrl_doc_rev']
    with db.engine.begin() as c:
        for r in db.rows(c, 'ctrl_doc_rev', rt.c.status == 'approved', rt.c.effective_date <= clock.now().date()):
            ecn = db.get(c, 'ecn', r['ecn']) if r['ecn'] else None
            if ecn and ecn['status'] != 'effective':
                continue
            make_effective(c, r, None)


# ───────────────────────── 现场只看生效版本 ─────────────────────────
def kb_visible(t):
    """知识库查询条件：不受控的 + 受控且生效的。"""
    return sa.or_(t.c.ctrl_status.is_(None), t.c.ctrl_status == 'effective')


@router.post('/api/doc/control')
def control(body: dict = Body(...), user=Depends(perms.require('doc.ctrl_doc.create'))):
    """把知识库文档 / NC 程序 / 控制计划 / PFMEA 纳入文件受控：{entity, id, rev?}"""
    ent = body.get('entity')
    if ent not in ('kb_doc', 'nc_program', 'control_plan', 'pfmea'):
        raise HTTPException(400, _tr('只能把知识库文档、NC 程序、控制计划、PFMEA 纳入受控'))
    with db.engine.begin() as c:
        rec = db.get(c, ent, int(body['id']))
        if not rec:
            raise HTTPException(404, _tr('记录不存在'))
        if ent == 'kb_doc':
            dtype, title, rev = ('drawing' if rec['kind'] == 'drawing' else 'wi'), rec['title'], rec['version'] or 'A'
        elif ent == 'nc_program':
            dtype, title, rev = 'nc', f"{rec['name']}", rec['version'] or 'V1'
        elif ent == 'control_plan':
            dtype, title, rev = 'control_plan', rec['title'], rec['rev'] or 'A'
        else:
            dtype, title, rev = 'pfmea', rec['title'], rec['rev'] or 'A'
        if rec.get('ctrl_doc') and ent != 'pfmea':
            raise HTTPException(400, _tr('已经是受控文件'))
        res = put_under_control(c, ent, rec['id'], title, dtype, rec.get('product'), rec.get('process'), body.get('rev') or rev, user)
    return {'ok': True, **res}


@router.get('/api/doc/list')
def doc_list(q: str = '', dtype: str = '', status: str = '', user=Depends(perms.require('doc.ctrl_doc.read'))):
    t, rt = TABLES['ctrl_doc'], TABLES['ctrl_doc_rev']
    cond = []
    if q.strip():
        cond.append(sa.or_(t.c.code.ilike(f'%{q.strip()}%'), t.c.title.ilike(f'%{q.strip()}%')))
    if dtype:
        cond.append(t.c.dtype == dtype)
    if status:
        cond.append(t.c.status == status)
    with db.engine.connect() as c:
        docs = attach_labels(c, 'ctrl_doc', db.rows(c, 'ctrl_doc', *cond, order=t.c.code))
        revs = {}
        for r in db.rows(c, 'ctrl_doc_rev', order=rt.c.id):
            revs.setdefault(r['doc'], []).append(r)
        for d in docs:
            rs = revs.get(d['id'], [])
            d['revisions'] = out(attach_labels(c, 'ctrl_doc_rev', list(reversed(rs))))
            d['pending'] = next((r['rev'] + ' ' + r['status'] for r in reversed(rs) if r['status'] in ('draft', 'review', 'approved')), None)
    return out(docs)


@router.get('/api/doc/effective')
def effective_docs(product: int = 0, process: int = 0, user=Depends(principal)):
    """现场用：某产品某工序当前生效的受控文件（作业指导书、图纸、NC 程序、控制计划）。"""
    t = TABLES['ctrl_doc']
    cond = [t.c.status == 'effective']
    if product:
        cond.append(sa.or_(t.c.product == product, t.c.product.is_(None)))
    if process:
        cond.append(sa.or_(t.c.process == process, t.c.process.is_(None)))
    with db.engine.connect() as c:
        docs = attach_labels(c, 'ctrl_doc', db.rows(c, 'ctrl_doc', *cond, order=t.c.code))
        res = []
        for d in docs:
            r = db.get(c, 'ctrl_doc_rev', d['cur_rev_id']) if d['cur_rev_id'] else None
            if not r:
                continue
            tgt = db.get(c, r['target_entity'], r['target_id']) if r['target_id'] else None
            res.append({'doc': d['id'], 'code': d['code'], 'title': d['title'], 'dtype': d['dtype'], 'rev': r['rev'], 'effective_at': db.jsonable(r['effective_at']),
                        'approved_by': r['approved_by'], 'target_entity': r['target_entity'], 'target_id': r['target_id'],
                        'file_type': (tgt or {}).get('file_type'), 'file_name': (tgt or {}).get('file_name'),
                        'excerpt': ((tgt or {}).get('content') or '')[:600] if r['target_entity'] in ('kb_doc', 'nc_program') else ''})
    return res


@router.get('/api/doc/{did}/history')
def doc_history(did: int, user=Depends(perms.require('doc.ctrl_doc.read'))):
    rt = TABLES['ctrl_doc_rev']
    with db.engine.connect() as c:
        d = db.get(c, 'ctrl_doc', did)
        if not d:
            raise HTTPException(404, _tr('文件不存在'))
        attach_labels(c, 'ctrl_doc', [d])
        revs = attach_labels(c, 'ctrl_doc_rev', db.rows(c, 'ctrl_doc_rev', order=rt.c.id.desc(), doc=did))
        for r in revs:
            r['signatures'] = esign.signatures(c, 'ctrl_doc_rev', r['id'])
            tgt = db.get(c, r['target_entity'], r['target_id']) if r['target_id'] else None
            r['target'] = {'id': r['target_id'], 'entity': r['target_entity'], 'label': (tgt or {}).get('title') or (tgt or {}).get('code') or (tgt or {}).get('name'),
                           'file_type': (tgt or {}).get('file_type'), 'content': ((tgt or {}).get('content') or '')[:3000] if r['target_entity'] in ('kb_doc', 'nc_program') else None}
    return {'item': out([d])[0], 'revisions': out(revs)}


# ───────────────────────── ECN ─────────────────────────
def impact(c, ecn) -> dict:
    """影响分析：按 ECN 的产品自动列出受影响的数据。"""
    pid = ecn['product']
    res = {'product': None, 'routes': [], 'bom': [], 'work_orders': [], 'wip': {}, 'wip_total': 0, 'stock': [], 'stock_total': 0, 'docs': [],
           'templates': [], 'control_plans': [], 'pfmea': [], 'outsource': [], 'packages': 0, 'revisions': []}
    res['revisions'] = [{'id': r['id'], 'doc': r['doc'], 'rev': r['rev'], 'status': r['status']} for r in db.rows(c, 'ctrl_doc_rev', ecn=ecn['id'])]
    if not pid:
        return res
    p = db.get(c, 'material', pid)
    res['product'] = {'id': p['id'], 'code': p['code'], 'name': p['name']}
    rts = db.rows(c, 'route', TABLES['route'].c.status != 'obsolete', product=pid)
    res['routes'] = [{'id': r['id'], 'code': r['code'], 'name': r['name'], 'version': r['version'], 'steps': len(db.rows(c, 'route_step', route=r['id']))} for r in rts]
    bt = TABLES['bom']
    mats = {m['id']: m for m in db.rows(c, 'material')}
    res['bom'] = [{'id': b['id'], 'product': mats.get(b['product'], {}).get('code'), 'component': mats.get(b['component'], {}).get('code'), 'qty': b['qty']}
                  for b in db.rows(c, 'bom', sa.or_(bt.c.product == pid, bt.c.component == pid))]
    wot = TABLES['work_order']
    for w in db.rows(c, 'work_order', wot.c.status.in_(('draft', 'released', 'in_progress')), product=pid, order=wot.c.id):
        res['work_orders'].append({'id': w['id'], 'code': w['code'], 'status': w['status'], 'qty': w['qty'], 'qty_done': w['qty_done'], 'due': db.jsonable(w['due'])})
    pt = TABLES['part']
    st = sa.select(pt.c.status, sa.func.count()).where((pt.c.product == pid) & pt.c.status.in_(('wip', 'hold', 'rework', 'good', 'raw_store'))).group_by(pt.c.status)
    res['wip'] = {s: n for s, n in c.execute(st).all()}
    res['wip_total'] = sum(res['wip'].values())
    comps = {b['component'] for b in db.rows(c, 'bom', product=pid)}
    stt = TABLES['stock']
    whs = {w['id']: w for w in db.rows(c, 'warehouse')}
    for s in db.rows(c, 'stock', stt.c.material.in_({pid} | comps), stt.c.qty > 0):
        res['stock'].append({'material': mats.get(s['material'], {}).get('code'), 'warehouse': whs.get(s['warehouse'], {}).get('name'), 'lot': s['lot'], 'qty': s['qty']})
    res['stock_total'] = sum(s['qty'] or 0 for s in res['stock'] if s['material'] == p['code'])
    dt = TABLES['ctrl_doc']
    res['docs'] = [{'id': d['id'], 'code': d['code'], 'title': d['title'], 'dtype': d['dtype'], 'rev': d['cur_rev'], 'status': d['status']}
                   for d in db.rows(c, 'ctrl_doc', dt.c.status != 'obsolete', product=pid)]
    res['templates'] = [{'id': t['id'], 'code': t['code'], 'name': t['name']} for t in db.rows(c, 'insp_template', material=pid)]
    res['control_plans'] = [{'id': x['id'], 'code': x['code'], 'rev': x['rev'], 'status': x['status']} for x in db.rows(c, 'control_plan', product=pid)
                            if x['status'] != 'obsolete']
    res['pfmea'] = [{'id': x['id'], 'code': x['code'], 'rev': x['rev'], 'status': x['status']} for x in db.rows(c, 'pfmea', product=pid) if x['status'] != 'obsolete']
    ot = TABLES['outsource_order']
    res['outsource'] = [{'id': o['id'], 'code': o['code'], 'supplier': o['supplier'], 'status': o['status']}
                        for o in db.rows(c, 'outsource_order', ot.c.status.in_(('draft', 'sent', 'partial')), material=pid)]
    pk = TABLES['package']
    res['packages'] = c.execute(sa.select(sa.func.count()).select_from(pk).where((pk.c.material == pid) & pk.c.status.in_(('open', 'closed')) & (pk.c.kind == 'carton'))).scalar()
    return res


def _after_ecn(c, rid, user):
    e = db.get(c, 'ecn', rid)
    if not e['requested_by']:
        db.update(c, 'ecn', rid, {'requested_by': (user or {}).get('name') or (user or {}).get('username') or _tr('系统')}, raw=True)


HOOKS[('ecn', 'after_save')] = _after_ecn


@action('ecn', 'analyze')
def _ecn_analyze(c, e, body, user):
    if e['status'] not in ('request', 'analysis'):
        raise HTTPException(400, _tr('只有申请 / 影响分析阶段可以重新分析'))
    imp = impact(c, e)
    db.update(c, 'ecn', e['id'], {'impact': imp, 'status': 'analysis'}, raw=True)
    # 实施任务（第一次分析时生成；之后只补缺的）
    have = {t['title'] for t in db.rows(c, 'ecn_task', ecn=e['id'])}
    tasks = []
    for r in imp['routes']:
        tasks.append(_tr('核对 / 更新工艺路线 {code}（{version}）', code=r['code'], version=r['version']))
    for d in imp['docs']:
        tasks.append(_tr('升版受控文件 {code} {title}', code=d['code'], title=d['title']))
    if imp['control_plans']:
        tasks.append(_tr('更新控制计划并同步检验模板'))
    if imp['work_orders']:
        tasks.append(_tr('处置在制工单 {len} 张（{v}）', len=len(imp['work_orders']), v={'allow': _tr('照旧生产'), 'block': _tr('停工待处置'), 'finish': _tr('做完再切换')}[e['wo_policy'] or 'finish']))
    if imp['wip_total'] or imp['stock_total']:
        tasks.append(_tr('处置在制品 {wip_total} 件、库存 {stock_total:g} 件（返工 / 让步 / 继续使用）', wip_total=imp['wip_total'], stock_total=imp['stock_total']))
    tasks.append(_tr('培训操作工并确认现场文件已更新'))
    seq = len(have) * 10
    for t in tasks:
        if t not in have:
            seq += 10
            db.insert(c, 'ecn_task', {'ecn': e['id'], 'seq': seq, 'title': t[:190], 'status': 'open'}, raw=True)
    # 在制工单处置
    have_wo = {x['work_order'] for x in db.rows(c, 'ecn_wo', ecn=e['id'])}
    for w in imp['work_orders']:
        if w['id'] not in have_wo and w['status'] in ('released', 'in_progress'):
            db.insert(c, 'ecn_wo', {'ecn': e['id'], 'work_order': w['id'], 'decision': 'block' if e['wo_policy'] == 'block' else 'allow', 'released': False}, raw=True)
    return {'msg': _tr('影响：工艺路线 {len}、BOM {len2}、未完工单 {len3}、在制品 {wip_total}、库存 {stock_total:g}、受控文件 {len4}、检验模板 {len5}', len=len(imp['routes']), len2=len(imp['bom']), len3=len(imp['work_orders']), wip_total=imp['wip_total'], stock_total=imp['stock_total'], len4=len(imp['docs']), len5=len(imp['templates'])), 'impact': imp}


@action('ecn', 'submit')
def _ecn_submit(c, e, body, user):
    if e['status'] != 'analysis':
        raise HTTPException(400, _tr('先做影响分析再提交批准'))
    db.update(c, 'ecn', e['id'], {'status': 'approval'}, raw=True)
    db.insert(c, 'notice', {'title': _tr('ECN {code} {title} 待批准', code=e['code'], title=e['title']), 'content': _tr('请在工艺变更里查看影响分析后批准（需要电子签名）。'), 'to_role': 'manager',
                            'channel': 'inbox', 'level': 'warn', 'link': f"/exec/ecn?id={e['id']}", 'source': '工艺变更', 'read': False, 'time': clock.now()}, raw=True)
    return {'msg': _tr('已提交批准')}


@action('ecn', 'approve')
def _ecn_approve(c, e, body, user):
    if e['status'] != 'approval':
        raise HTTPException(400, _tr('不在待批准状态'))
    sig = esign.require(c, user, body, 'ecn', e['id'], 'approve', f"{e['code']} {e['title']}")
    db.update(c, 'ecn', e['id'], {'status': 'implementing', 'approved_by': (sig or {}).get('name') or user.get('name') or user['username'],
                                  'approved_at': clock.now()}, raw=True)
    return {'msg': _tr('已批准，进入实施')}


@action('ecn', 'reject')
def _ecn_reject(c, e, body, user):
    if e['status'] not in ('analysis', 'approval'):
        raise HTTPException(400, _tr('这个状态不能驳回'))
    db.update(c, 'ecn', e['id'], {'status': 'rejected', 'description': ((e['description'] or '') + _tr('\n驳回：{v}', v=body.get('reason') or '')).strip()}, raw=True)
    return {'msg': _tr('已驳回')}


@action('ecn', 'cancel')
def _ecn_cancel(c, e, body, user):
    if e['status'] in ('effective',):
        raise HTTPException(400, _tr('已生效的 ECN 不能取消（再走一个变更）'))
    db.update(c, 'ecn', e['id'], {'status': 'cancelled'}, raw=True)
    return {'msg': _tr('已取消')}


@action('ecn', 'effective')
def _ecn_effective(c, e, body, user):
    if e['status'] != 'implementing':
        raise HTTPException(400, _tr('批准后（实施中）才能生效'))
    open_t = [t for t in db.rows(c, 'ecn_task', ecn=e['id']) if t['status'] == 'open']
    if open_t and not body.get('force'):
        raise HTTPException(409, _tr('还有实施任务没完成：') + _tr('；').join(t['title'] for t in open_t[:5]) + ('…' if len(open_t) > 5 else ''))
    revs = db.rows(c, 'ctrl_doc_rev', ecn=e['id'])
    bad = [r for r in revs if r['status'] not in ('approved', 'effective', 'obsolete')]
    if bad:
        raise HTTPException(409, _tr('关联的文件版本还没批准：') + _tr('、').join(_rev_label(c, r) for r in bad))
    snap = []
    # 先置为已生效（关联版本的"随 ECN 生效"检查要看到）
    db.update(c, 'ecn', e['id'], {'status': 'effective', 'effective_at': clock.now()}, raw=True)
    for r in revs:
        if r['status'] != 'approved':
            continue
        old = make_effective(c, r, user)
        d = db.get(c, 'ctrl_doc', r['doc'])
        snap.append({'doc': d['code'], 'title': d['title'], 'old': old, 'new': r['rev'], 'target_entity': r['target_entity'], 'target_id': r['target_id']})
    wos = []
    for x in db.rows(c, 'ecn_wo', ecn=e['id']):
        w = db.get(c, 'work_order', x['work_order'])
        wos.append({'code': w['code'], 'status': w['status'], 'decision': x['decision'], 'released': bool(x['released'])})
    db.update(c, 'ecn', e['id'], {'snapshot': {'docs': snap, 'work_orders': wos, 'policy': e['wo_policy'], 'at': db.jsonable(clock.now())}}, raw=True)
    return {'msg': _tr('已生效：{len} 份文件切换到新版本', len=len(snap)) + (_tr('，{sum} 张工单停工待处置', sum=sum(1 for w in wos if w['decision'] == 'block' and not w['released'])) if wos else '')}


@action('ecn_task', 'done')
def _task_done(c, t, body, user):
    db.update(c, 'ecn_task', t['id'], {'status': 'skipped' if body.get('skip') else 'done', 'done_by': user.get('name') or user['username'], 'done_at': clock.now(),
                                       'note': body.get('note') or t['note']}, raw=True)
    return {'msg': _tr('已完成') if not body.get('skip') else _tr('已标为不适用')}


@action('ecn_wo', 'unblock')
def _wo_unblock(c, x, body, user):
    db.update(c, 'ecn_wo', x['id'], {'released': True, 'released_by': user.get('name') or user['username'], 'released_at': clock.now(),
                                     'note': body.get('note') or x['note']}, raw=True)
    return {'msg': _tr('已解除停工')}


def report_gate(c, wo, op, body):
    """报工闸门：ECN 要求停工的在制工单（批准后、未解除）不能报工。"""
    xt, et = TABLES['ecn_wo'], TABLES['ecn']
    r = c.execute(sa.select(et.c.code, et.c.title).select_from(xt.join(et, et.c.id == xt.c.ecn)).where(
        (xt.c.work_order == wo['id']) & (xt.c.decision == 'block') & (xt.c.released.isnot(True)) & et.c.status.in_(('implementing', 'effective')))).first()
    if r:
        raise Gate('ecn', _tr('工单 {code} 因工艺变更 {v}（{v2}）停工待处置，不能报工；工艺工程师在 ECN 里“解除停工”后再报', code=wo['code'], v=r[0], v2=r[1]), ecn=r[0])


@router.get('/api/ecn/{eid}/full')
def ecn_full(eid: int, user=Depends(perms.require('ecn.ecn.read'))):
    with db.engine.connect() as c:
        e = db.get(c, 'ecn', eid)
        if not e:
            raise HTTPException(404, _tr('ECN 不存在'))
        attach_labels(c, 'ecn', [e])
        tasks = attach_labels(c, 'ecn_task', db.rows(c, 'ecn_task', order=TABLES['ecn_task'].c.seq, ecn=eid))
        revs = attach_labels(c, 'ctrl_doc_rev', db.rows(c, 'ctrl_doc_rev', ecn=eid))
        for r in revs:
            d = db.get(c, 'ctrl_doc', r['doc'])
            r['doc_code'], r['doc_title'], r['doc_cur_rev'] = d['code'], d['title'], d['cur_rev']
        wos = attach_labels(c, 'ecn_wo', db.rows(c, 'ecn_wo', ecn=eid))
        sigs = esign.signatures(c, 'ecn', eid)
    return {'item': out([e])[0], 'tasks': out(tasks), 'revisions': out(revs), 'work_orders': out(wos), 'signatures': sigs}


@router.post('/api/ecn/{eid}/revise')
def ecn_revise(eid: int, body: dict = Body(...), user=Depends(perms.require('doc.ctrl_doc.revise'))):
    """在 ECN 下给受控文件升版：{doc}。"""
    with db.engine.begin() as c:
        e = db.get(c, 'ecn', eid)
        d = db.get(c, 'ctrl_doc', int(body['doc'])) if body.get('doc') else None
        if not e or not d:
            raise HTTPException(404, _tr('ECN 或文件不存在'))
        if e['status'] in ('effective', 'rejected', 'cancelled'):
            raise HTTPException(400, _tr('这个 ECN 已经结束'))
        r, new = revise(c, d, user, ecn=eid, note=_tr('{code}：{title}', code=e['code'], title=e['title']))
    return {'ok': True, 'id': r['id'], 'rev': r['rev'], 'new': new, 'target_entity': r['target_entity'], 'target_id': r['target_id']}
