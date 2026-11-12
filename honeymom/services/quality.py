"""质量：设备自动检验入库、不合格品闭环、SPC、检验单判定、8D。"""
from ..i18n import _tr
import math
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..auth import current_user
from ..crud import HOOKS, action, view
from ..db import clock
from ..schema import TABLES

router = APIRouter()

# ---------------- 不合格品"待处理"的唯一口径 ----------------
# 待处置（open）和评审中/返修中（reviewing）都算"待处理"；只有关闭的不算。首页计数、提醒、日报、不合格品页"待处理"视图都用这里，
# 以前首页按"未关闭"计数、页面却按 status=open 过滤，返修中的单子（reviewing）只计数不显示，造成"首页 359 条、列表为空"。
NCR_PENDING_STATUS = ('open', 'reviewing')
NCR_PENDING_LINK = '/e/ncr?view=pending'


@view('ncr', 'pending', '待处理（待处置 + 评审/返修中）')
def ncr_pending(t=None):
    t = TABLES['ncr'] if t is None else t
    return sa.or_(t.c.status.in_(NCR_PENDING_STATUS), t.c.status.is_(None))


@view('ncr', 'closed', '已关闭')
def ncr_closed(t=None):
    t = TABLES['ncr'] if t is None else t
    return t.c.status == 'closed'


@view('ncr', 'rework', '返修中')
def ncr_in_rework(t=None):
    t = TABLES['ncr'] if t is None else t
    return (t.c.status == 'reviewing') & (t.c.disposition == 'rework')


def ncr_pending_count(c) -> int:
    nt = TABLES['ncr']
    return c.execute(sa.select(sa.func.count()).select_from(nt).where(ncr_pending(nt))).scalar() or 0


@router.get('/api/quality/ncr/counts')
def ncr_counts(user=Depends(current_user)):
    """不合格品页签计数：和首页用同一个口径。"""
    nt = TABLES['ncr']
    with db.engine.connect() as c:
        n = lambda cond: c.execute(sa.select(sa.func.count()).select_from(nt).where(cond)).scalar() or 0     # noqa: E731
        return {'pending': n(ncr_pending(nt)), 'open': n(nt.c.status == 'open'), 'rework': n(ncr_in_rework(nt)),
                'reviewing': n(nt.c.status == 'reviewing'), 'closed': n(ncr_closed(nt)), 'all': n(sa.true()), 'link': NCR_PENDING_LINK}


CAUSE_KEYS = [('毛刺返修', 'D09'), ('冷隔', 'D03'), ('球化', 'D04'), ('内腔', 'D05'), ('残余毛刺', 'D06'), ('缸孔', 'D07'), ('断刀', 'D08'), ('误报', 'D10'),
              ('气孔', 'D01'), ('缩松', 'D02')]


def defect_id(c, cause: str):
    for k, code in CAUSE_KEYS:
        if k in (cause or ''):
            r = db.get(c, 'defect_code', code=code)
            return r['id'] if r else None
    return None


def _template(c, char_or_code):
    return db.get(c, 'insp_template', code=char_or_code)


# ---------------- 设备自动检验 ----------------
def auto_measure(c, part, station, char, value, ts):
    """在线测量（如 CNC 测头测缸孔）：每台设备每小时一张检验单，逐件追加测量值。"""
    it = db.get(c, 'insp_item', char=char)
    lo, hi = (it['lo'], it['hi']) if it else (None, None)
    ok = (lo is None or value >= lo) and (hi is None or value <= hi)
    rt = TABLES['insp_record']
    hour = ts.replace(minute=0, second=0, microsecond=0)
    tpl = it['template'] if it else None
    rid = c.execute(sa.select(rt.c.id).where((rt.c.station == station) & (rt.c.source == 'auto') & (rt.c.time == hour) & (rt.c.itype == 'IPQC'))).scalar()
    if rid:
        c.execute(rt.update().where(rt.c.id == rid).values(qty_sample=rt.c.qty_sample + 1, qty_ng=rt.c.qty_ng + (0 if ok else 1),
                                                          result=sa.case((rt.c.qty_ng + (0 if ok else 1) > 0, 'NG'), else_='OK'), updated_at=ts))
    else:
        rid = db.insert(c, 'insp_record', {'template': tpl, 'itype': 'IPQC', 'work_order': part.get('work_order'), 'station': station, 'qty_sample': 1,
                                           'qty_ng': 0 if ok else 1, 'result': 'OK' if ok else 'NG', 'time': hour, 'source': 'auto',
                                           'remark': _tr('{station} 在线测量（每小时汇总）', station=station)}, raw=True)
    c.execute(TABLES['insp_value'].insert().values(record=rid, item=it['id'] if it else None, char=char, value=value, text=part['sn'], ok=ok,
                                                   created_at=ts, updated_at=ts))


def auto_inspection(c, part, station, result, cause, ts):
    tpl = db.get(c, 'insp_template', code='T-ENDO')
    rid = db.insert(c, 'insp_record', {'template': tpl['id'] if tpl else None, 'itype': 'IPQC', 'work_order': part.get('work_order'), 'sn': part['sn'],
                                       'station': station, 'qty_sample': 1, 'qty_ng': 1 if result == 'NG' else 0, 'result': result or 'pending',
                                       'time': ts, 'source': 'auto', 'remark': cause}, raw=True)
    c.execute(TABLES['insp_value'].insert().values(record=rid, char='ENDO', text=cause or 'OK', ok=result == 'OK', created_at=ts, updated_at=ts))
    nt = TABLES['ncr']
    rw = TABLES['rework']
    if result == 'NG':
        # 返修后复检仍不合格：返修单记上复检结论（完整记录），再按新的不合格登记
        r = c.execute(sa.select(rw.c.id).where((rw.c.sn == part['sn']) & (rw.c.result == 'doing')).order_by(rw.c.id.desc()).limit(1)).scalar()
        if r:
            c.execute(rw.update().where(rw.c.id == r).values(result='fail', recheck=rid, recheck_result='NG', recheck_by=station or _tr('检测站'), recheck_at=ts,
                                                           finished_at=sa.func.coalesce(rw.c.finished_at, ts), updated_at=ts))
        db.insert(c, 'ncr', {'sn': part['sn'], 'work_order': part.get('work_order'), 'material': part.get('product'), 'qty': 1,
                             'defect': defect_id(c, cause), 'station': station, 'found_at': ts, 'found_by': _tr('内窥检测站'), 'source': 'auto',
                             'disposition': 'pending', 'status': 'open', 'root_cause': cause}, raw=True)
    else:
        # 返修后复检合格：关闭返修和 NCR
        r = c.execute(sa.select(rw).where((rw.c.sn == part['sn']) & (rw.c.result == 'doing')).order_by(rw.c.id.desc()).limit(1)).mappings().first()
        if r:
            c.execute(rw.update().where(rw.c.id == r['id']).values(result='ok', updated_at=ts, remark=_tr('复检合格'), recheck=rid, recheck_result='OK',
                                                                  recheck_by=station or _tr('检测站'), recheck_at=ts, finished_at=r['finished_at'] or ts))
            if r['ncr']:
                c.execute(nt.update().where(nt.c.id == r['ncr']).values(status='closed', closed_at=ts, action=_tr('返修后复检合格')))


def on_rework(c, part, station, reason, ts):
    nt = TABLES['ncr']
    n = c.execute(sa.select(nt).where((nt.c.sn == part['sn']) & (nt.c.status != 'closed')).order_by(nt.c.id.desc()).limit(1)).mappings().first()
    if n:
        c.execute(nt.update().where(nt.c.id == n['id']).values(disposition='rework', status='reviewing', updated_at=ts))
    db.insert(c, 'rework', {'ncr': n['id'] if n else None, 'sn': part['sn'], 'station': _tr('去毛刺单元'), 'result': 'doing', 'time': ts,
                            'remark': _tr('自动返修：{reason}', reason=reason)}, raw=True)


def on_scrap(c, part, station, cause, ts):
    nt = TABLES['ncr']
    n = c.execute(sa.select(nt).where((nt.c.sn == part['sn']) & (nt.c.status != 'closed')).order_by(nt.c.id.desc()).limit(1)).mappings().first()
    if n:
        c.execute(nt.update().where(nt.c.id == n['id']).values(disposition='scrap', status='closed', closed_at=ts, action=_tr('判废：{cause}', cause=cause)))
        rw = TABLES['rework']
        c.execute(rw.update().where((rw.c.sn == part['sn']) & (rw.c.result == 'doing')).values(result='fail', updated_at=ts))
    else:
        db.insert(c, 'ncr', {'sn': part['sn'], 'work_order': part.get('work_order'), 'material': part.get('product'), 'qty': 1,
                             'defect': defect_id(c, cause), 'station': station, 'found_at': ts, 'found_by': station, 'source': 'auto',
                             'disposition': 'scrap', 'status': 'closed', 'root_cause': cause, 'closed_at': ts, 'action': _tr('系统判废')}, raw=True)


# ---------------- 人工检验单：按项目上下限自动判定 ----------------
def judge_record(c, rid, user=None):
    vals = db.rows(c, 'insp_value', record=rid)
    if not vals:
        return
    items = {i['id']: i for i in db.rows(c, 'insp_item', TABLES['insp_item'].c.id.in_([v['item'] for v in vals if v['item']]))} if vals else {}
    ng = 0
    for v in vals:
        it = items.get(v['item'])
        ok = v['ok']
        if it and v['value'] is not None:
            ok = (it['lo'] is None or v['value'] >= it['lo']) and (it['hi'] is None or v['value'] <= it['hi'])
            c.execute(TABLES['insp_value'].update().where(TABLES['insp_value'].c.id == v['id']).values(ok=ok))
        if ok is False:
            ng += 1
    rec = db.get(c, 'insp_record', rid)
    res = 'NG' if ng else 'OK'
    db.update(c, 'insp_record', rid, {'qty_ng': max(rec['qty_ng'] or 0, 1 if ng else 0), 'result': res}, raw=True)
    if ng and rec['sn'] and not db.get(c, 'ncr', sn=rec['sn'], status='open'):
        db.insert(c, 'ncr', {'sn': rec['sn'], 'lot': rec['lot'], 'work_order': rec['work_order'], 'qty': 1, 'station': rec['station'],
                             'found_at': rec['time'] or clock.now(), 'found_by': _tr('检验员'), 'source': 'manual', 'disposition': 'pending', 'status': 'open'}, raw=True)


@router.post('/api/quality/record/{rid}/values')
def save_values(rid: int, body: dict, user=Depends(current_user)):
    """检验录入：[{item, value|text}] → 自动判定。"""
    with db.engine.begin() as c:
        rec = db.get(c, 'insp_record', rid)
        if not rec:
            raise HTTPException(404, _tr('检验单不存在'))
        from .iatf import check_gauges
        check_gauges(c, body.get('values', []), rec)          # 量具必须有效（IATF：过期锁定的量具不能用于检验）
        c.execute(TABLES['insp_value'].delete().where(TABLES['insp_value'].c.record == rid))
        now = rec['time'] or clock.now()
        for v in body.get('values', []):
            it = db.get(c, 'insp_item', int(v['item'])) if v.get('item') else None
            val = v.get('value')
            val = float(val) if val not in (None, '') else None
            c.execute(TABLES['insp_value'].insert().values(record=rid, item=it['id'] if it else None, char=it['char'] if it else None, value=val,
                                                           text=v.get('text'), ok=v.get('ok', True) if val is None else None,
                                                           gauge=int(v['gauge']) if v.get('gauge') else rec.get('gauge'), created_at=now, updated_at=now))
        judge_record(c, rid, user)
        return {'ok': True, 'record': db.jsonable(db.get(c, 'insp_record', rid)['result'])}


# ---------------- NCR 处置 ----------------
@action('ncr', 'rework')
def _ncr_rework(c, r, body, user):
    db.update(c, 'ncr', r['id'], {'disposition': 'rework', 'status': 'reviewing'}, raw=True)
    db.insert(c, 'rework', {'ncr': r['id'], 'sn': r['sn'], 'station': body.get('station') or _tr('返修台'), 'result': 'doing', 'time': clock.now(),
                            'remark': body.get('remark') or _tr('{v} 安排返修', v=user['name'] or user['username'])}, raw=True)


@action('ncr', 'scrap')
def _ncr_scrap(c, r, body, user):
    db.update(c, 'ncr', r['id'], {'disposition': 'scrap', 'status': 'closed', 'closed_at': clock.now(), 'action': body.get('remark') or _tr('评审判废')}, raw=True)
    if r['sn']:
        pt = TABLES['part']
        c.execute(pt.update().where(pt.c.sn == r['sn']).values(status='scrap', scrap_cause=_tr('NCR 判废')))


@action('ncr', 'concession')
def _ncr_con(c, r, body, user):
    db.update(c, 'ncr', r['id'], {'disposition': 'concession', 'status': 'closed', 'closed_at': clock.now(),
                                  'action': body.get('remark') or _tr('让步接收（{v}）', v=user['name'] or user['username'])}, raw=True)


@action('ncr', 'close')
def _ncr_close(c, r, body, user):
    if r['disposition'] == 'rework':
        # 返修的单子：必须有返修记录、返修完成且复检合格，才能关闭（返修复检记录完整）
        rws = db.rows(c, 'rework', order=TABLES['rework'].c.id.desc(), ncr=r['id'])
        if not rws:
            raise HTTPException(409, _tr('这张单子处置为返修，但还没有返修记录，不能关闭'))
        last = rws[0]
        if last['recheck_result'] != 'OK':
            raise HTTPException(409, _tr('返修单 {code} 还没有复检合格（{v}），不能关闭；请先在返修记录里点"返修完成"再"复检"', code=last['code'], v=_tr('复检不合格') if last['recheck_result'] == 'NG' else _tr('待复检')))
    db.update(c, 'ncr', r['id'], {'status': 'closed', 'closed_at': clock.now()}, raw=True)


@action('ncr', 'to8d')
def _ncr_8d(c, r, body, user):
    d = db.get(c, 'defect_code', r['defect']) if r['defect'] else None
    rid = db.insert(c, 'report_8d', {'title': _tr('{v}（{code}）', v=d['name'] if d else _tr('质量问题'), code=r['code']), 'ncr': r['id'], 'defect': r['defect'], 'status': 'draft'}, raw=True)
    return {'id': rid, 'msg': _tr('已创建 8D 报告，可以点"AI 起草"')}


@action('report_8d', 'ai_draft')
def _8d_ai(c, r, body, user):
    from . import cross
    draft = cross.draft_8d(c, r)
    db.update(c, 'report_8d', r['id'], {**draft, 'ai_generated': True}, raw=True)
    return {'msg': _tr('已生成草稿，请评审修改')}


# ---------------- SPC ----------------
A2 = {2: 1.880, 3: 1.023, 4: 0.729, 5: 0.577, 6: 0.483}
D3 = {2: 0, 3: 0, 4: 0, 5: 0, 6: 0}
D4 = {2: 3.267, 3: 2.574, 4: 2.282, 5: 2.114, 6: 2.004}
d2 = {2: 1.128, 3: 1.693, 4: 2.059, 5: 2.326, 6: 2.534}


def western_electric(xs, cl, sigma):
    """判异规则（前四条）：返回 [(索引, 规则说明)]。"""
    out = []
    for i, x in enumerate(xs):
        if abs(x - cl) > 3 * sigma:
            out.append((i, _tr('规则1：超出控制限')))
        if i >= 8 and all((v - cl) > 0 for v in xs[i - 8:i + 1]) or i >= 8 and all((v - cl) < 0 for v in xs[i - 8:i + 1]):
            out.append((i, _tr('规则2：连续 9 点在中心线同侧')))
        if i >= 5:
            w = xs[i - 5:i + 1]
            if all(w[k] < w[k + 1] for k in range(5)) or all(w[k] > w[k + 1] for k in range(5)):
                out.append((i, _tr('规则3：连续 6 点递增或递减')))
        if i >= 2:
            w = xs[i - 2:i + 1]
            if sum(1 for v in w if v - cl > 2 * sigma) >= 2 or sum(1 for v in w if cl - v > 2 * sigma) >= 2:
                out.append((i, _tr('规则5：3 点中 2 点在 2σ 外')))
    return out


@router.get('/api/quality/spc')
def spc(char: str = 'BORE_DIA', station: str = '', n: int = 125, sub: int = 5, user=Depends(current_user)):
    vt, rt = TABLES['insp_value'], TABLES['insp_record']
    with db.engine.connect() as c:
        q = sa.select(vt.c.value, vt.c.created_at, vt.c.text, rt.c.station).join(rt, rt.c.id == vt.c.record).where((vt.c.char == char) & vt.c.value.isnot(None))
        if station:
            q = q.where(rt.c.station == station)
        data = list(reversed(c.execute(q.order_by(vt.c.id.desc()).limit(n)).all()))
        it = db.get(c, 'insp_item', char=char)
        stations = [r[0] for r in c.execute(sa.select(rt.c.station).join(vt, vt.c.record == rt.c.id).where(vt.c.char == char).distinct()).all() if r[0]]
        chars = [r[0] for r in c.execute(sa.select(TABLES['insp_item'].c.char, TABLES['insp_item'].c.name).where(TABLES['insp_item'].c.char.isnot(None))).all()]
    xs = [float(d[0]) for d in data]
    if len(xs) < sub * 4:
        return {'enough': False, 'count': len(xs), 'stations': stations, 'chars': chars}
    groups = [xs[i:i + sub] for i in range(0, len(xs) - len(xs) % sub, sub)]
    xbar = [sum(g) / len(g) for g in groups]
    rng = [max(g) - min(g) for g in groups]
    xbb, rbar = sum(xbar) / len(xbar), sum(rng) / len(rng)
    sigma_w = rbar / d2[sub]
    mean = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (len(xs) - 1))
    lsl, usl = (it['lo'], it['hi']) if it else (None, None)
    cap = {}
    if lsl is not None and usl is not None:
        cap = {'Cp': (usl - lsl) / (6 * sigma_w), 'Cpk': min(usl - xbb, xbb - lsl) / (3 * sigma_w),
               'Pp': (usl - lsl) / (6 * sd), 'Ppk': min(usl - mean, mean - lsl) / (3 * sd)}
    rules = western_electric(xbar, xbb, sigma_w / math.sqrt(sub))
    hist = {}
    if xs:
        lo, hi = min(xs + ([lsl] if lsl is not None else [])), max(xs + ([usl] if usl is not None else []))
        bins = 20
        w = (hi - lo) / bins or 1
        cnt = [0] * bins
        for x in xs:
            cnt[min(bins - 1, int((x - lo) / w))] += 1
        hist = {'edges': [lo + i * w for i in range(bins + 1)], 'counts': cnt}
    return {'enough': True, 'count': len(xs), 'char': char, 'name': it['name'] if it else char, 'unit': it['unit'] if it else '', 'lsl': lsl, 'usl': usl,
            'xbar': xbar, 'r': rng, 'xbb': xbb, 'rbar': rbar, 'ucl_x': xbb + A2[sub] * rbar, 'lcl_x': xbb - A2[sub] * rbar,
            'ucl_r': D4[sub] * rbar, 'lcl_r': D3[sub] * rbar, 'mean': mean, 'sd': sd, 'sigma_w': sigma_w, **{k: round(v, 3) for k, v in cap.items()},
            'rules': [{'i': i, 'rule': r} for i, r in rules], 'hist': hist, 'stations': stations, 'chars': chars,
            'times': [db.jsonable(data[min(len(data) - 1, i * sub + sub - 1)][1]) for i in range(len(groups))], 'sub': sub}


@router.get('/api/quality/summary')
def summary(days: int = 7, user=Depends(current_user)):
    """质量概览：不良分布、各工位不良率、NCR 状态。"""
    nt, dt = TABLES['ncr'], TABLES['defect_code']
    since = clock.now() - timedelta(days=days)
    with db.engine.connect() as c:
        pareto = [{'name': r[0] or _tr('未分类'), 'n': r[1]} for r in c.execute(
            sa.select(dt.c.name, sa.func.count()).select_from(nt.outerjoin(dt, dt.c.id == nt.c.defect)).where(nt.c.found_at >= since)
            .group_by(dt.c.name).order_by(sa.func.count().desc())).all()]
        status = {r[0]: r[1] for r in c.execute(sa.select(nt.c.status, sa.func.count()).group_by(nt.c.status)).all()}
        disp = {r[0]: r[1] for r in c.execute(sa.select(nt.c.disposition, sa.func.count()).where(nt.c.found_at >= since).group_by(nt.c.disposition)).all()}
        rt = TABLES['insp_record']
        fpy = c.execute(sa.select(sa.func.sum(rt.c.qty_sample), sa.func.sum(rt.c.qty_ng)).where((rt.c.time >= since) & (rt.c.sn.isnot(None)))).first()
    return {'pareto': pareto, 'status': status, 'disposition': disp,
            'endo': {'total': fpy[0] or 0, 'ng': fpy[1] or 0, 'rate': round((fpy[1] or 0) / max(1, fpy[0] or 0), 4)}}


def _after_template_item(c, rid, user):
    pass


HOOKS[('insp_record', 'after_save')] = lambda c, rid, user: None
