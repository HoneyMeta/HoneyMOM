"""IATF 16949：量检具（校准、到期锁用、放行签名、检验必须用有效量具）、PFMEA（S/O/D、RPN、AIAG-VDA AP）→ 控制计划 → 检验模板联动、
特殊特性（CC/SC）、MSA（GR&R 均值极差 / 方差分析、偏倚、线性、计数型一致性；计算在 honeymom/msa.py）。

接口一览见 docs/dev/exec.md「IATF」。检验录入的量具校验 check_gauges() 由 quality.save_values 和 gates.fai_inspect 调用。
"""
from ..i18n import _tr
import base64
import csv
import io
import math
import os
from datetime import date, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from .. import db, perms
from ..auth import principal
from ..crud import ACTIONS, HOOKS, action, attach_labels, out
from ..db import clock
from ..schema import TABLES
from ..schema_exec import GAUGE_T, SPECIAL
from . import esign
from .gates import Gate

router = APIRouter()
GAUGE_OK = ('active', 'due')
STATUS_CN = {'active': '有效', 'due': '即将到期', 'overdue': '过期锁定', 'quarantine': '待放行', 'locked': '锁定', 'repair': '维修', 'scrapped': '报废'}


def today() -> date:
    return clock.now().date()


# ───────────────────────── 量检具 ─────────────────────────
def due_days(c) -> int:
    try:
        return int(db.setting(c, 'gauge_due_days', 30) or 30)
    except ValueError:
        return 30


def date_state(g, d0=None, warn=30):
    """按到期日算状态（只对 有效 / 即将到期 / 过期 这三种自动维护）。"""
    d0 = d0 or today()
    nd = g['next_due']
    if not nd or nd < d0:
        return 'overdue'
    return 'due' if nd <= d0 + timedelta(days=warn) else 'active'


def refresh_gauges(c, notify=True) -> dict:
    """到期提醒 + 过期锁定：有效 → 即将到期 → 过期锁定（自动）；过期锁定的重新校准并放行后才恢复。返回各状态变化数。"""
    t = TABLES['gauge']
    d0, warn = today(), due_days(c)
    changed = {'due': 0, 'overdue': 0, 'active': 0}
    for g in db.rows(c, 'gauge', t.c.status.in_(('active', 'due', 'overdue'))):
        st = date_state(g, d0, warn)
        if st == g['status']:
            continue
        vals = {'status': st, 'lock_reason': _tr('校准过期（到期 {next_due}），已自动锁定', next_due=g['next_due']) if st == 'overdue' else None}
        db.update(c, 'gauge', g['id'], vals, raw=True)
        changed[st] += 1
        if notify and st in ('due', 'overdue'):
            title = _tr('量具 {code} {name} ', code=g['code'], name=g['name']) + (_tr('校准已过期，已锁定') if st == 'overdue' else _tr('将于 {next_due} 到期，请安排校准', next_due=g['next_due']))
            db.insert(c, 'notice', {'title': title, 'content': _tr('保管人：{v}；地点：{v2}。过期的量具不能用于检验录入。', v=g['owner'] or '—', v2=g['location'] or '—'),
                                    'to_role': 'qc', 'channel': 'inbox', 'level': 'urgent' if st == 'overdue' else 'warn', 'link': f"/iatf/gauges?id={g['id']}",
                                    'source': '量检具', 'read': False, 'time': clock.now()}, raw=True)
    return changed


def gauge_valid(c, g, item=None) -> str | None:
    """量具能不能用于检验：能用返回 None，不能用返回原因（给人看的中文）。"""
    if not g:
        return _tr('量具不存在')
    st = g['status']
    if st in GAUGE_OK and g['next_due'] and g['next_due'] < today():
        st = 'overdue'
    if st == 'overdue':
        return _tr('量具 {code} {name} 校准已过期（到期 {next_due}），已锁定，不能用于检验；请换一把有效量具，或送检校准并放行后再用', code=g['code'], name=g['name'], next_due=g['next_due'])
    if st not in GAUGE_OK:
        why = g['lock_reason'] or STATUS_CN.get(st, st)
        return _tr('量具 {code} {name} 当前是“{get}”（{why}），不能用于检验', code=g['code'], name=g['name'], get=STATUS_CN.get(st, st), why=why)
    if item and item.get('gauge_type') and g['gtype'] != item['gauge_type']:
        return _tr('“{name}”要用{get}，量具 {code} 是{get2}', name=item['name'], get=GAUGE_T.get(item['gauge_type'], item['gauge_type']), code=g['code'], get2=GAUGE_T.get(g['gtype'], g['gtype']))
    return None


def check_gauges(c, values, rec=None):
    """检验录入前校验量具：录了量具就必须有效；检验项目指定了量具类型的必须选量具（系统参数 gauge_required：item 默认 / all / off）。
    不通过抛 409（X-Gate: gauge），整次录入不落库。返回 {项目 id: 量具 id}。"""
    mode = (db.setting(c, 'gauge_required', 'item') or 'item').strip()
    ids = [int(v['item']) for v in values or [] if v.get('item')]
    items = {i['id']: i for i in db.rows(c, 'insp_item', TABLES['insp_item'].c.id.in_(ids))} if ids else {}
    default_g = (rec or {}).get('gauge')
    used = {}
    for v in values or []:
        it = items.get(int(v['item'])) if v.get('item') else None
        gid = v.get('gauge') or default_g
        has_value = v.get('value') not in (None, '') or v.get('ok') is not None or v.get('text')
        if gid:
            g = db.get(c, 'gauge', int(gid))
            why = gauge_valid(c, g, it)
            if why:
                raise Gate('gauge', why, gauge=int(gid))
            used[it['id'] if it else 0] = int(gid)
        elif mode != 'off' and has_value and it and (it.get('gauge_type') or mode == 'all'):
            kind = GAUGE_T.get(it.get('gauge_type'), '量具')
            raise Gate('gauge', _tr('“{name}”要用{kind}测量：请选择一把有效（已校准）的{kind}再录入', name=it['name'], kind=kind), item=it['id'])
    return used


def _after_gauge(c, rid, user):
    g = db.get(c, 'gauge', rid)
    vals = {}
    if g['last_cal'] and not g['next_due']:
        vals['next_due'] = g['last_cal'] + timedelta(days=g['cal_interval_days'] or 365)
    nd = vals.get('next_due') or g['next_due']
    if g['status'] in GAUGE_OK + ('overdue',) and nd:
        st = date_state({**g, **vals}, today(), due_days(c))
        if st != g['status']:
            vals['status'] = st
            vals['lock_reason'] = _tr('校准过期（到期 {nd}），已自动锁定', nd=nd) if st == 'overdue' else None
    elif g['status'] == 'quarantine' and nd and nd < today():      # 登记时就已过期的：直接过期锁定（校准、放行后才能用）
        vals.update(status='overdue', lock_reason=_tr('校准过期（到期 {nd}），已锁定', nd=nd))
    elif g['status'] == 'quarantine' and not nd and not g['lock_reason']:
        vals['lock_reason'] = _tr('未校准：登记校准记录并放行后才能用')
    if vals:
        db.update(c, 'gauge', rid, vals, raw=True)


HOOKS[('gauge', 'after_save')] = _after_gauge


def _after_cal(c, rid, user):
    """校准记录 → 量具：最新一次校准决定上次校准、下次到期、证书号；合格 → 待放行（要电子签名放行），不合格 → 锁定。"""
    cal = db.get(c, 'gauge_cal', rid)
    g = db.get(c, 'gauge', cal['gauge'])
    ct = TABLES['gauge_cal']
    latest = c.execute(sa.select(ct.c.id).where(ct.c.gauge == g['id']).order_by(ct.c.cal_date.desc(), ct.c.id.desc()).limit(1)).scalar()
    if latest != rid:
        return
    nd = cal['next_due'] or (cal['cal_date'] + timedelta(days=g['cal_interval_days'] or 365))
    if not cal['next_due']:
        c.execute(ct.update().where(ct.c.id == rid).values(next_due=nd))
    vals = {'last_cal': cal['cal_date'], 'next_due': nd, 'cert_no': cal['cert_no'] or g['cert_no']}
    if cal['result'] == 'fail':
        vals.update(status='locked', lock_reason=_tr('校准不合格（{code}）', code=cal['code']))
    elif g['status'] != 'scrapped' and not (g['status'] in GAUGE_OK and g['last_cal'] == cal['cal_date']):
        vals.update(status='quarantine', lock_reason=_tr('已校准（{code}），待放行', code=cal['code']))
    db.update(c, 'gauge', g['id'], vals, raw=True)


HOOKS[('gauge_cal', 'after_save')] = _after_cal


@action('gauge', 'release')
def _gauge_release(c, g, body, user):
    if g['status'] == 'scrapped':
        raise HTTPException(400, _tr('报废的量具不能放行'))
    if not g['next_due'] or g['next_due'] < today():
        raise HTTPException(409, _tr('量具 {code} 没有有效的校准（到期 {v}），先登记校准记录再放行', code=g['code'], v=g['next_due'] or '—'))
    last = db.rows(c, 'gauge_cal', order=TABLES['gauge_cal'].c.cal_date.desc(), limit=1, gauge=g['id'])
    if last and last[0]['result'] == 'fail':
        raise HTTPException(409, _tr('最近一次校准 {code} 不合格，不能放行', code=last[0]['code']))
    sig = esign.require(c, user, body, 'gauge', g['id'], 'release', f"{g['code']} {g['name']}")
    st = date_state(g, today(), due_days(c))
    db.update(c, 'gauge', g['id'], {'status': st, 'lock_reason': None, 'released_by': (sig or {}).get('name') or user.get('name') or user['username'],
                                    'released_at': clock.now()}, raw=True)
    from .exec_label import fire
    fire(c, 'gauge_cal', 'gauge', g['id'], user)
    return {'msg': _tr('已放行，下次到期 {next_due}', next_due=g['next_due'])}


@action('gauge', 'lock')
def _gauge_lock(c, g, body, user):
    db.update(c, 'gauge', g['id'], {'status': 'locked', 'lock_reason': (body.get('reason') or _tr('人工锁定'))[:180]}, raw=True)
    return {'msg': _tr('已锁定，不能用于检验')}


@action('gauge', 'scrap')
def _gauge_scrap(c, g, body, user):
    db.update(c, 'gauge', g['id'], {'status': 'scrapped', 'lock_reason': (body.get('reason') or _tr('报废'))[:180]}, raw=True)
    return {'msg': _tr('已报废')}


PERM_GAUGE = 'gauge.gauge.read'


@router.get('/api/gauge/board')
def gauge_board(user=Depends(perms.require(PERM_GAUGE))):
    """量检具看板：各状态数、30 天内到期、已过期、最近校准。"""
    with db.engine.begin() as c:
        refresh_gauges(c)
    t = TABLES['gauge']
    with db.engine.connect() as c:
        rows = db.rows(c, 'gauge', order=(t.c.next_due.asc().nulls_last(), t.c.code))
        cnt = {}
        for g in rows:
            cnt[g['status']] = cnt.get(g['status'], 0) + 1
        d0 = today()
        for g in rows:
            g['days_left'] = (g['next_due'] - d0).days if g['next_due'] else None
            g['status_text'] = STATUS_CN.get(g['status'], g['status'])
        cals = db.rows(c, 'gauge_cal', order=TABLES['gauge_cal'].c.id.desc(), limit=20)
        attach_labels(c, 'gauge_cal', cals)
    return {'counts': cnt, 'total': len(rows), 'gauges': out(rows), 'recent_cals': out(cals), 'due_days': 30}


@router.get('/api/gauge/valid')
def gauge_valid_list(gtype: str = '', item: int = 0, user=Depends(principal)):
    """检验录入用：可选的量具（有效的在前；过期 / 锁定的也列出来但带不可用原因，界面置灰）。"""
    with db.engine.begin() as c:
        refresh_gauges(c, notify=False)
        it = db.get(c, 'insp_item', item) if item else None
        want = gtype or (it or {}).get('gauge_type') or ''
        t = TABLES['gauge']
        rows = db.rows(c, 'gauge', t.c.status != 'scrapped', *([t.c.gtype == want] if want else []), order=t.c.code)
        res = []
        for g in rows:
            why = gauge_valid(c, g, it)
            res.append({'id': g['id'], 'code': g['code'], 'name': g['name'], 'gtype': g['gtype'], 'status': g['status'], 'next_due': db.jsonable(g['next_due']),
                        'ok': why is None, 'why': why, 'label': f"{g['code']} {g['name']}"})
    res.sort(key=lambda x: (not x['ok'], x['code']))
    return {'gtype': want, 'gtype_label': GAUGE_T.get(want, ''), 'items': res}


@router.post('/api/gauge/check')
def gauge_check(body: dict = Body(...), user=Depends(principal)):
    """预检（不落库）：{values:[{item, gauge}]} → 能不能录入。"""
    with db.engine.connect() as c:
        try:
            check_gauges(c, body.get('values') or [], body.get('record'))
        except Gate as g:
            return {'ok': False, 'msg': g.detail, 'gate': g.gate}
    return {'ok': True}


@router.post('/api/gauge/cal/{cid}/cert')
def cal_cert_upload(cid: int, body: dict = Body(...), user=Depends(perms.require('gauge.gauge_cal.update'))):
    """上传校准证书（PDF / 图片，base64）。存 <数据目录>/kb_files/gauge_cert_<id>.<扩展名>（随备份）。"""
    from .drawings import files_dir
    name = body.get('name') or 'cert.pdf'
    ext = os.path.splitext(name)[1].lower()
    if ext not in ('.pdf', '.png', '.jpg', '.jpeg'):
        raise HTTPException(400, _tr('证书只支持 PDF / PNG / JPG'))
    try:
        data = base64.b64decode((body.get('data') or '').split(',')[-1])
    except ValueError:
        raise HTTPException(400, _tr('文件内容不是 base64'))
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, _tr('文件太大（最大 20 MB）'))
    with db.engine.begin() as c:
        cal = db.get(c, 'gauge_cal', cid)
        if not cal:
            raise HTTPException(404, _tr('校准记录不存在'))
        for f in os.listdir(files_dir()):
            if f.startswith(f'gauge_cert_{cid}.'):
                os.remove(os.path.join(files_dir(), f))
        with open(os.path.join(files_dir(), f'gauge_cert_{cid}{ext}'), 'wb') as f:
            f.write(data)
        db.update(c, 'gauge_cal', cid, {'cert_file': name[:190]}, raw=True)
    return {'ok': True, 'cert_file': name}


@router.get('/api/gauge/cal/{cid}/cert')
def cal_cert_get(cid: int, user=Depends(perms.require('gauge.gauge_cal.read'))):
    from .drawings import files_dir
    for f in os.listdir(files_dir()):
        if f.startswith(f'gauge_cert_{cid}.'):
            ext = os.path.splitext(f)[1].lower()
            return FileResponse(os.path.join(files_dir(), f), media_type='application/pdf' if ext == '.pdf' else 'image/' + ext[1:].replace('jpg', 'jpeg'),
                                headers={'Content-Disposition': f'inline; filename="cert-{cid}{ext}"'})
    raise HTTPException(404, _tr('没有上传证书'))


# ───────────────────────── PFMEA ─────────────────────────
def action_priority(s, o, d) -> str | None:
    """AIAG-VDA FMEA 手册（2019）过程 FMEA 的措施优先级 AP 表：H 高 / M 中 / L 低。"""
    if not s or not o or not d:
        return None
    s, o, d = int(s), int(o), int(d)

    def band_d(dd):
        return 0 if dd >= 7 else 1 if dd >= 5 else 2 if dd >= 2 else 3
    db_ = band_d(d)
    if s >= 9:
        tbl = {'o8': 'HHHH', 'o6': 'HHHH', 'o4': 'HHHM', 'o2': 'HMLL', 'o1': 'LLLL'}
    elif s >= 7:
        tbl = {'o8': 'HHHH', 'o6': 'HHHM', 'o4': 'HMMM', 'o2': 'MMLL', 'o1': 'LLLL'}
    elif s >= 4:
        tbl = {'o8': 'HHMM', 'o6': 'MMML', 'o4': 'MLLL', 'o2': 'LLLL', 'o1': 'LLLL'}
    elif s >= 2:
        tbl = {'o8': 'MMLL', 'o6': 'LLLL', 'o4': 'LLLL', 'o2': 'LLLL', 'o1': 'LLLL'}
    else:
        return 'L'
    ob = 'o8' if o >= 8 else 'o6' if o >= 6 else 'o4' if o >= 4 else 'o2' if o >= 2 else 'o1'
    return tbl[ob][db_]


def _after_fmea_row(c, rid, user):
    r = db.get(c, 'pfmea_row', rid)
    s, o, d = r['severity'], r['occurrence'], r['detection']
    for k, v in (('severity', s), ('occurrence', o), ('detection', d)):
        if v is not None and not 1 <= v <= 10:
            raise HTTPException(400, _tr('{v} 要在 1–10 之间', v={"severity": "严重度 S", "occurrence": _tr('频度 O'), "detection": _tr('探测度 D')}[k]))
    vals = {'rpn': s * o * d if s and o and d else None, 'ap': action_priority(s, o, d),
            'ap2': action_priority(r['s2'] or s, r['o2'] or o, r['d2'] or d) if (r['s2'] or r['o2'] or r['d2']) else None}
    if any(r.get(k) != v for k, v in vals.items()):
        c.execute(TABLES['pfmea_row'].update().where(TABLES['pfmea_row'].c.id == rid).values(**vals))


HOOKS[('pfmea_row', 'after_save')] = _after_fmea_row

GAUGE_GUESS = [('三坐标', 'cmm'), ('CMM', 'cmm'), ('内径', 'bore'), ('测头', 'bore'), ('卡尺', 'caliper'), ('千分尺', 'micrometer'), ('光谱', 'spectro'),
               ('通止', 'thread'), ('螺纹', 'thread'), ('塞规', 'plug'), ('环规', 'ring'), ('测温', 'thermo'), ('温度', 'thermo'), ('硬度', 'hardness'),
               ('高度', 'height'), ('检具', 'fixture'), ('百分表', 'dial')]
REACTION = {'CC': '立即停机隔离本班产品，100% 全检；通知质量工程师和班组长；按 NCR 评审处置，查明原因并验证纠正措施后才能恢复生产',
            'SC': '停机调整，追溯上次合格以来的产品并加严检验（连续 5 件合格再恢复正常频次）；登记 NCR',
            'none': '调整后复检；不合格品隔离并登记 NCR'}
FREQ = {'CC': ('5 件', '首件 + 每 2 小时 + 换刀/换型后'), 'SC': ('3 件', '首件 + 每班 2 次'), 'none': ('1 件', '首件 / 末件')}


def guess_gauge(text):
    for k, v in GAUGE_GUESS:
        if k in (text or ''):
            return v
    return None


def _spec_text(lo, hi, unit):
    u = unit or ''
    if lo is not None and hi is not None:
        return f'{lo:g}–{hi:g} {u}'.strip()
    if hi is not None:
        return f'≤{hi:g} {u}'.strip()
    if lo is not None:
        return f'≥{lo:g} {u}'.strip()
    return ''


@action('pfmea', 'derive_cp')
def _derive_cp(c, f, body, user):
    """由 PFMEA 生成控制计划（草稿）：每个带特性的条目 → 一行控制计划（特性、规范、评价方法、量具类型、样本 / 频率、控制方法、反应计划）。
    已有这份 PFMEA 的草稿控制计划时追加缺的行，不重复。"""
    rows = db.rows(c, 'pfmea_row', order=TABLES['pfmea_row'].c.seq, pfmea=f['id'])
    rows = [r for r in rows if r['char'] or r['product_char'] or r['process_char']]
    if not rows:
        raise HTTPException(400, _tr('PFMEA 里还没有带产品/过程特性的条目'))
    cpt = TABLES['control_plan']
    cp = db.rows(c, 'control_plan', cpt.c.status == 'draft', pfmea=f['id'], order=cpt.c.id.desc(), limit=1)
    if cp:
        cid = cp[0]['id']
    else:
        cid = db.insert(c, 'control_plan', {'title': _tr('{title} 控制计划', title=f['title']), 'product': f['product'], 'phase': 'production', 'rev': f['rev'] or 'A',
                                            'pfmea': f['id'], 'status': 'draft', 'team': f['team']}, raw=True)
    have = {(x['process'], x['char'] or x['product_char']) for x in db.rows(c, 'cp_item', plan=cid)}
    seq = len(have) * 10
    n = 0
    for r in rows:
        key = (r['process'], r['char'] or r['product_char'])
        if key in have:
            continue
        have.add(key)
        seq += 10
        sp = r['special'] or 'none'
        size, freq = FREQ.get(sp, FREQ['none'])
        db.insert(c, 'cp_item', {'plan': cid, 'seq': seq, 'process': r['process'], 'char_no': f"{seq // 10:02d}", 'product_char': r['product_char'] or r['function'],
                                 'process_char': r['process_char'], 'char': r['char'], 'special': sp, 'spec': _spec_text(r['lo'], r['hi'], r['unit']),
                                 'lo': r['lo'], 'hi': r['hi'], 'unit': r['unit'], 'method': r['detection_ctrl'], 'gauge_type': guess_gauge(r['detection_ctrl']),
                                 'sample_size': size, 'frequency': freq, 'control_method': r['prevention'] or _tr('SPC / 检验记录'), 'reaction': REACTION.get(sp, REACTION['none']),
                                 'pfmea_row': r['id']}, raw=True)
        n += 1
    cpr = db.get(c, 'control_plan', cid)
    return {'id': cid, 'code': cpr['code'], 'msg': _tr('控制计划 {code}：新增 {n} 行（共 {len} 行）', code=cpr['code'], n=n, len=len(have))}


# ───────────────────────── 控制计划 → 检验模板 ─────────────────────────
def sync_inspection(c, cp) -> dict:
    """控制计划 → 检验模板（每道过程一个 IPQC 模板，编码 CP-<产品>-<工序>，跨版本不变）：按控制计划项新建 / 更新检验项目
    （特性、规范、上下限、方法、量具类型、特殊特性），控制计划里删掉的项目：没有检验值的删掉，有历史的保留。返回 {templates, created, updated, removed}。"""
    items = db.rows(c, 'cp_item', order=TABLES['cp_item'].c.seq, plan=cp['id'])
    prod = db.get(c, 'material', cp['product']) if cp['product'] else None
    pcode = prod['code'] if prod else f"CP{cp['id']}"
    by_proc = {}
    for it in items:
        by_proc.setdefault(it['process'], []).append(it)
    stats = {'templates': [], 'created': 0, 'updated': 0, 'removed': 0}
    ivt = TABLES['insp_value']
    for proc_id, its in by_proc.items():
        proc = db.get(c, 'process', proc_id) if proc_id else None
        code = f"CP-{pcode}-{proc['code'] if proc else 'GEN'}"
        tpl = db.get(c, 'insp_template', code=code)
        rule = _tr('按控制计划 {code} 版本 {v}：', code=cp['code'], v=cp['rev'] or '') + _tr('；').join(sorted({f"{x['sample_size'] or ''}/{x['frequency'] or ''}" for x in its}))[:150]
        if tpl:
            db.update(c, 'insp_template', tpl['id'], {'sample_rule': rule, 'name': _tr('{get} 过程检验（控制计划）', get=(proc or {}).get('name', ''))}, raw=True)
            tid = tpl['id']
        else:
            tid = db.insert(c, 'insp_template', {'code': code, 'name': _tr('{get} 过程检验（控制计划）', get=(proc or {}).get('name', '')), 'itype': 'IPQC',
                                                 'material': cp['product'], 'process': proc_id, 'sample_rule': rule}, raw=True)
        stats['templates'].append(code)
        existing = {x['char'] or x['name']: x for x in db.rows(c, 'insp_item', template=tid)}
        keep = set()
        for it in its:
            key = it['char'] or it['product_char'] or it['process_char']
            vals = {'template': tid, 'seq': it['seq'], 'name': (it['product_char'] or it['process_char'] or key)[:190], 'char': it['char'],
                    'method': it['method'], 'std': it['spec'], 'lo': it['lo'], 'hi': it['hi'], 'unit': it['unit'], 'key': (it['special'] or 'none') != 'none',
                    'special': it['special'] or 'none', 'gauge_type': it['gauge_type'], 'cp_item': it['id']}
            ex = existing.get(key)
            if ex:
                if any(ex.get(k) != v for k, v in vals.items()):
                    db.update(c, 'insp_item', ex['id'], vals, raw=True)
                    stats['updated'] += 1
                iid = ex['id']
            else:
                iid = db.insert(c, 'insp_item', vals, raw=True)
                stats['created'] += 1
            keep.add(iid)
            c.execute(TABLES['cp_item'].update().where(TABLES['cp_item'].c.id == it['id']).values(insp_item=iid))
        for x in existing.values():
            if x['id'] in keep:
                continue
            used = c.execute(sa.select(sa.func.count()).select_from(ivt).where(ivt.c.item == x['id'])).scalar()
            if used:
                if not (x['name'] or '').startswith('[已停用]'):
                    db.update(c, 'insp_item', x['id'], {'name': (_tr('[已停用] ') + (x['name'] or ''))[:190], 'cp_item': None}, raw=True)
            else:
                db.delete(c, 'insp_item', x['id'])
            stats['removed'] += 1
    return stats


@action('control_plan', 'gen_insp')
def _cp_gen(c, cp, body, user):
    st = sync_inspection(c, cp)
    return {'msg': _tr('检验模板 {join}：新增 {created} 项、更新 {updated} 项、停用 {removed} 项', join=_tr('、').join(st['templates']), created=st['created'], updated=st['updated'], removed=st['removed']), **st}


@action('control_plan', 'to_doc')
def _cp_to_doc(c, cp, body, user):
    from .exec_doc import put_under_control
    rev = put_under_control(c, 'control_plan', cp['id'], cp['title'], 'control_plan', cp['product'], None, cp['rev'] or 'A', user)
    return {'msg': _tr('已纳入文件受控（{doc_code} 版本 {rev}），走审核 → 批准 → 生效', doc_code=rev['doc_code'], rev=rev['rev']), 'id': rev['doc']}


def special_map(c, product=None) -> dict:
    """特性代码 → CC / SC（来自生效的控制计划，没有生效的取最新草稿）。SPC、标签、检验录入用。"""
    cpt = TABLES['control_plan']
    q = [cpt.c.status.in_(('effective', 'approved', 'draft', 'review'))]
    if product:
        q.append(cpt.c.product == product)
    plans = db.rows(c, 'control_plan', *q, order=cpt.c.id)
    eff = [p for p in plans if p['status'] == 'effective']
    use = eff or plans
    res = {}
    for p in use:
        for it in db.rows(c, 'cp_item', plan=p['id']):
            if it['char'] and (it['special'] or 'none') != 'none':
                res[it['char']] = it['special']
    for it in db.rows(c, 'insp_item', TABLES['insp_item'].c.special.in_(('CC', 'SC'))):
        if it['char']:
            res.setdefault(it['char'], it['special'])
    return res


@router.get('/api/iatf/special')
def special(product: int = 0, user=Depends(principal)):
    with db.engine.connect() as c:
        m = special_map(c, product or None)
    return {'chars': m, 'labels': SPECIAL}


@router.get('/api/iatf/pfmea/{fid}')
def pfmea_full(fid: int, user=Depends(perms.require('iatf.pfmea.read'))):
    with db.engine.connect() as c:
        f = db.get(c, 'pfmea', fid)
        if not f:
            raise HTTPException(404, _tr('PFMEA 不存在'))
        attach_labels(c, 'pfmea', [f])
        rows = attach_labels(c, 'pfmea_row', db.rows(c, 'pfmea_row', order=TABLES['pfmea_row'].c.seq, pfmea=fid))
        cps = attach_labels(c, 'control_plan', db.rows(c, 'control_plan', pfmea=fid, order=TABLES['control_plan'].c.id.desc()))
    ap = {'H': 0, 'M': 0, 'L': 0}
    for r in rows:
        if r['ap'] in ap:
            ap[r['ap']] += 1
    return {'item': out([f])[0], 'rows': out(rows), 'plans': out(cps), 'ap_counts': ap}


@router.get('/api/iatf/cp/{cid}')
def cp_full(cid: int, user=Depends(perms.require('iatf.control_plan.read'))):
    with db.engine.connect() as c:
        cp = db.get(c, 'control_plan', cid)
        if not cp:
            raise HTTPException(404, _tr('控制计划不存在'))
        attach_labels(c, 'control_plan', [cp])
        items = attach_labels(c, 'cp_item', db.rows(c, 'cp_item', order=TABLES['cp_item'].c.seq, plan=cid))
        tpls = {}
        for it in items:
            if it['insp_item']:
                ii = db.get(c, 'insp_item', it['insp_item'])
                if ii:
                    t = db.get(c, 'insp_template', ii['template'])
                    it['insp_template'] = t['code'] if t else None
                    tpls[t['code']] = t['id']
        revs = []
        if cp['ctrl_doc']:
            revs = out(db.rows(c, 'ctrl_doc_rev', order=TABLES['ctrl_doc_rev'].c.id.desc(), doc=cp['ctrl_doc']))
    return {'item': out([cp])[0], 'items': out(items), 'templates': tpls, 'revisions': revs, 'gauge_types': GAUGE_T}


@router.get('/api/iatf/cp/{cid}/print', response_class=HTMLResponse)
def cp_print(cid: int, user=Depends(perms.require('iatf.control_plan.read'))):
    """控制计划打印版（AIAG 控制计划表格式，浏览器打印 / 另存 PDF）。"""
    d = cp_full(cid, user)
    cp = d['item']
    esc = lambda x: '' if x is None else str(x).replace('&', '&amp;').replace('<', '&lt;')
    rows = ''.join(f"<tr><td>{esc(i.get('process__label'))}</td><td>{esc(i['machine'])}</td><td>{esc(i['char_no'])}</td><td>{esc(i['product_char'])}</td>"
                   f"<td>{esc(i['process_char'])}</td><td class=sp>{'◆' if i['special'] == 'CC' else '▲' if i['special'] == 'SC' else ''}</td><td>{esc(i['spec'])}</td>"
                   f"<td>{esc(i['method'])}</td><td>{esc(i['sample_size'])}</td><td>{esc(i['frequency'])}</td><td>{esc(i['control_method'])}</td>"
                   f"<td>{esc(i['reaction'])}</td></tr>" for i in d['items'])
    return _tr('<!doctype html><html><head><meta charset="utf-8"><title>控制计划 {esc}</title><style>\n@page{{size:A4 landscape;margin:10mm}}body{{font-family:\'Microsoft YaHei\',sans-serif;font-size:11px;color:#111}}table{{border-collapse:collapse;width:100%}}\ntd,th{{border:1px solid #444;padding:3px 4px;vertical-align:top}}th{{background:#eee}}.sp{{text-align:center;font-weight:bold}}h1{{font-size:16px;margin:0 0 6px}}\n.k td{{border:none;padding:1px 8px 1px 0}}</style></head><body><h1>控制计划 Control Plan</h1>\n<table class=k><tr><td>编号 {esc}</td><td>版本 {esc2}</td><td>阶段 {esc3}</td>\n<td>产品 {esc4}</td><td>状态 {esc5}</td><td>小组 {esc6}</td></tr></table><br>\n<table><tr><th>过程</th><th>机器/装置</th><th>编号</th><th>产品特性</th><th>过程特性</th><th>特殊特性</th><th>规范/公差</th><th>评价测量技术</th><th>样本</th><th>频率</th>\n<th>控制方法</th><th>反应计划</th></tr>{rows}</table><p>◆ 关键特性 CC\u3000▲ 重要特性 SC</p><script>setTimeout(()=>window.print&&window.print(),400)</script></body></html>', esc=esc(cp['code']), esc2=esc(cp['rev']), esc3=esc({'proto': _tr('样件'), 'prelaunch': _tr('试生产'), 'production': _tr('量产')}.get(cp['phase'])), esc4=esc(cp.get('product__label')), esc5=esc(cp['status']), esc6=esc(cp['team']), rows=rows)


# ───────────────────────── MSA ─────────────────────────
VERDICT = {'acceptable': 'acceptable', 'conditional': 'conditional', 'marginal': 'conditional', 'unacceptable': 'unacceptable'}


def msa_compute(st) -> dict:
    from .. import msa
    d = st['data'] or {}
    m = st['method']
    tol = st['tolerance'] or None
    if m in ('grr_xbar', 'grr_anova'):
        vals = d.get('values')
        if not vals:
            raise HTTPException(400, _tr('请先录入或导入测量数据（评价人 × 次数 × 零件）'))
        names = d.get('appraisers') or [chr(65 + i) for i in range(len(vals))]
        data = dict(zip(names, vals))
        try:
            r = msa.grr_xbar_r(data, tolerance=tol) if m == 'grr_xbar' else msa.grr_anova(data, tolerance=tol)
        except ValueError as ex:
            raise HTTPException(400, _tr('数据不完整：{ex}', ex=ex))
        return {'result': r, 'grr_pct': r.get('pct_grr'), 'ndc': r.get('ndc'), 'verdict': VERDICT.get(r.get('verdict'))}
    if m == 'bias':
        rd = [float(x) for x in d.get('readings') or []]
        if len(rd) < 2 or st['reference'] is None:
            raise HTTPException(400, _tr('偏倚研究要基准值和至少 2 个读数（建议 ≥10）'))
        r = msa.bias(rd, float(st['reference']), tolerance=tol)
        return {'result': r, 'grr_pct': None, 'ndc': None, 'verdict': VERDICT.get(r.get('verdict'))}
    if m == 'linearity':
        parts = d.get('parts') or []
        if len(parts) < 2:
            raise HTTPException(400, _tr('线性研究要至少 2 个基准件（建议 5 个 × 12 次）'))
        r = msa.linearity([{'reference': float(p['reference']), 'readings': [float(x) for x in p['readings']]} for p in parts])
        return {'result': r, 'grr_pct': None, 'ndc': None, 'verdict': VERDICT.get(r.get('verdict'))}
    if m == 'attribute':
        vals = d.get('values')
        if not vals:
            raise HTTPException(400, _tr('请先录入判定结果（评价人 × 次数 × 零件）'))
        names = d.get('appraisers') or [chr(65 + i) for i in range(len(vals))]
        try:
            r = msa.attribute_agreement(dict(zip(names, vals)), reference=d.get('reference') or None)
        except ValueError as ex:
            raise HTTPException(400, _tr('数据不完整：{ex}', ex=ex))
        return {'result': r, 'grr_pct': None, 'ndc': None, 'verdict': VERDICT.get(r.get('verdict'))}
    raise HTTPException(400, _tr('不支持的方法 {m}', m=m))


def _clean(v):
    if isinstance(v, float):
        return None if not math.isfinite(v) else v
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if hasattr(v, 'item'):
        return _clean(v.item())
    return v


@action('msa_study', 'calc')
def _msa_calc(c, st, body, user):
    if body.get('data') is not None:
        db.update(c, 'msa_study', st['id'], {'data': body['data']}, raw=True)
        st = db.get(c, 'msa_study', st['id'])
    r = msa_compute(st)
    res = _clean(r['result'])
    vals = {'result': res, 'grr_pct': r['grr_pct'], 'ndc': r['ndc'], 'verdict': r['verdict'], 'study_date': st['study_date'] or today(),
            'by': st['by'] or user.get('name') or user['username']}
    db.update(c, 'msa_study', st['id'], vals, raw=True)
    if st['gauge'] and st['method'] in ('grr_xbar', 'grr_anova'):
        db.update(c, 'gauge', st['gauge'], {'grr_pct': r['grr_pct'], 'ndc': r['ndc'], 'msa': st['id']}, raw=True)
    vt = {'acceptable': _tr('可接受'), 'conditional': _tr('有条件接受'), 'unacceptable': _tr('不可接受')}.get(r['verdict'], '—')
    extra = _tr('，%GRR {grr_pct:.1f}%，ndc {ndc}', grr_pct=r['grr_pct'], ndc=r['ndc']) if r['grr_pct'] is not None else ''
    return {'msg': _tr('计算完成：{vt}{extra}', vt=vt, extra=extra), 'result': res}


def parse_csv(method, text):
    """导入：长表 CSV。GR&R / 计数型：评价人,次数,零件,值[,基准]；偏倚：值；线性：基准,值。"""
    rows = [r for r in csv.reader(io.StringIO(text.strip())) if r and any(x.strip() for x in r)]
    if rows and not any(ch.isdigit() for ch in ''.join(rows[0][-1:])):
        rows = rows[1:]          # 表头
    if method == 'bias':
        return {'readings': [float(r[-1]) for r in rows]}
    if method == 'linearity':
        parts = {}
        for r in rows:
            parts.setdefault(float(r[0]), []).append(float(r[1]))
        return {'parts': [{'reference': k, 'readings': v} for k, v in sorted(parts.items())]}
    apps, trials, parts, cells, ref = [], [], [], {}, {}
    for r in rows:
        a, t, p, v = r[0].strip(), r[1].strip(), r[2].strip(), r[3].strip()
        for lst, x in ((apps, a), (trials, t), (parts, p)):
            if x not in lst:
                lst.append(x)
        cells[(a, t, p)] = v if method == 'attribute' else float(v)
        if len(r) > 4 and r[4].strip():
            ref[p] = r[4].strip()
    vals = [[[cells.get((a, t, p)) for p in parts] for t in trials] for a in apps]
    if any(x is None for a in vals for t in a for x in t):
        raise HTTPException(400, _tr('数据不平衡：每个评价人、每次、每个零件都要有值'))
    out_ = {'appraisers': apps, 'values': vals, 'parts': parts}
    if method == 'attribute' and ref:
        out_['reference'] = [ref.get(p) for p in parts]
    return out_


@router.post('/api/iatf/msa/{sid}/import')
def msa_import(sid: int, body: dict = Body(...), user=Depends(perms.require('iatf.msa_study.update'))):
    with db.engine.begin() as c:
        st = db.get(c, 'msa_study', sid)
        if not st:
            raise HTTPException(404, _tr('MSA 研究不存在'))
        try:
            data = parse_csv(st['method'], body.get('csv') or '')
        except (ValueError, IndexError) as ex:
            raise HTTPException(400, _tr('CSV 格式不对：{ex}', ex=ex))
        upd = {'data': data}
        if data.get('appraisers'):
            upd.update(appraisers=len(data['appraisers']), trials=len(data['values'][0]), parts=len(data['values'][0][0]))
        db.update(c, 'msa_study', sid, upd, raw=True)
    return {'ok': True, 'data': data}


@router.get('/api/iatf/msa/{sid}/report', response_class=HTMLResponse)
def msa_report(sid: int, user=Depends(perms.require('iatf.msa_study.read'))):
    from .. import msa
    with db.engine.connect() as c:
        st = db.get(c, 'msa_study', sid)
        if not st:
            raise HTTPException(404, _tr('MSA 研究不存在'))
        attach_labels(c, 'msa_study', [st])
    esc = lambda x: '' if x is None else str(x).replace('&', '&amp;').replace('<', '&lt;')
    rows = ''.join(f'<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>' for k, v in msa.report_rows(st['result'] or {})[:120])
    vt = {'acceptable': _tr('可接受'), 'conditional': _tr('有条件接受'), 'unacceptable': _tr('不可接受')}.get(st['verdict'], '未计算')
    return _tr('<!doctype html><html><head><meta charset="utf-8"><title>MSA 报告 {esc}</title><style>body{{font-family:\'Microsoft YaHei\',sans-serif;margin:28px;font-size:12px}}\ntable{{border-collapse:collapse}}td{{border:1px solid #ccc;padding:3px 8px}}h1{{font-size:18px}}</style></head><body><h1>测量系统分析报告 {esc}</h1>\n<p>{esc2}\u3000方法：{esc3}\u3000量具：{esc4}\u3000特性：{esc5}\u3000公差：{esc6}\u3000日期：{esc7}\u3000分析人：{esc8}</p>\n<p><b>结论：{vt}</b>{v}</p>\n<p>依据 AIAG MSA 第四版（2010）：%GRR &lt;10% 可接受，10–30% 有条件接受，&gt;30% 不可接受；ndc ≥ 5。</p>\n<table>{rows}</table><p>{esc9}</p></body></html>', esc=esc(st['code']), esc2=esc(st['title']), esc3=esc(st['method']), esc4=esc(st.get('gauge__label')), esc5=esc(st['char']), esc6=esc(st['tolerance']), esc7=esc(st['study_date']), esc8=esc(st['by']), vt=vt, v=_tr('\u3000%GRR {grr_pct:.2f}%\u3000ndc {ndc}', grr_pct=st['grr_pct'], ndc=st['ndc']) if st['grr_pct'] is not None else '', rows=rows, esc9=esc(st['conclusion']))


# ───────────────────────── 后台：每分钟刷新量具状态 ─────────────────────────
def tick():
    with db.engine.begin() as c:
        refresh_gauges(c)


def install_wrappers():
    """已有动作上加电子签名（首件放行、批次解冻放行）。在 gates 导入之后调用。"""
    for ent, act in (('first_article', 'approve'), ('lot_hold', 'release')):
        fn = ACTIONS.get((ent, act))
        if not fn or getattr(fn, '_esign', False):
            continue

        def wrapped(c, row, body, user, _fn=fn, _ent=ent, _act=act):
            lab = row.get('code') or row.get('lot') or f"#{row['id']}"
            if _ent == 'lot_hold':
                lab = _tr('{code} 批次 {lot}', code=row['code'], lot=row['lot'])
            esign.require(c, user, body, _ent, row['id'], _act, lab)
            return _fn(c, row, body, user)
        wrapped._esign = True
        ACTIONS[(ent, act)] = wrapped

