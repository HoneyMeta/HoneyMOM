"""追溯对账：沿一条真实的链 炉次 → 浇包 → 零件码 → 机加工 → 检验 → 出库，每一步核对数量，对不上的标出来。

每一步给出：进入、转出、报废、在制（还在这一步）、冻结，平衡式 进入 = 转出 + 报废 + 在制 + 冻结；
另外做交叉核对（炉次铸件计数 vs 零件码数、浇包球化记录、成品入库单 vs 入库事件、发运出库单 vs 发运事件、未检先入库等），不一致的列为差异。
"""
from ..i18n import _tr
from .. import i18n as _i18n
from collections import defaultdict

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..auth import current_user
from ..schema import TABLES

router = APIRouter()
CAST_OUT = {'shakeout', 'cleaned', 'to_raw_store', 'delivered', 'machining_start', 'machined'}
MACH_IN = {'delivered', 'machining_start', 'machined'}


@router.get('/api/trace/recon/heats')
def recon_heats(limit: int = 30, user=Depends(current_user)):
    """可对账的炉次：最近有零件的炉次，带零件数和完成度。"""
    pt = TABLES['part']
    with db.engine.connect() as c:
        rows = c.execute(sa.select(pt.c.heat, sa.func.count(), sa.func.min(pt.c.born_at), sa.func.sum(sa.case((pt.c.status.in_(('fg', 'shipped')), 1), else_=0)),
                                   sa.func.sum(sa.case((pt.c.status == 'scrap', 1), else_=0)))
                         .where(pt.c.heat.isnot(None) & (pt.c.heat != '')).group_by(pt.c.heat).order_by(sa.func.min(pt.c.born_at).desc()).limit(limit)).all()
        heats = {h['code']: h for h in db.rows(c, 'heat', TABLES['heat'].c.code.in_([r[0] for r in rows]))} if rows else {}
    return [{'heat': r[0], 'parts': r[1], 'born': db.jsonable(r[2]), 'done': int(r[3] or 0), 'scrap': int(r[4] or 0), 'is_heat': r[0] in heats,
             'kg': (heats.get(r[0]) or {}).get('kg')} for r in rows]


def _default_heat(c):
    pt = TABLES['part']
    r = c.execute(sa.select(pt.c.heat).where(pt.c.heat.isnot(None) & pt.c.status.in_(('fg', 'shipped'))).group_by(pt.c.heat)
                  .order_by(sa.func.count().desc()).limit(1)).scalar()
    return r or c.execute(sa.select(pt.c.heat).where(pt.c.heat.isnot(None)).order_by(pt.c.id.desc()).limit(1)).scalar()


def reconcile(c, heat):
    pt, pe = TABLES['part'], TABLES['part_event']
    h = db.get(c, 'heat', code=heat)
    parts = db.rows(c, 'part', order=pt.c.id, heat=heat)
    if not parts and not h:
        return None
    sns = [p['sn'] for p in parts]
    evs = defaultdict(list)
    if sns:
        for sn, t, ev, data in c.execute(sa.select(pe.c.sn, pe.c.t, pe.c.event, pe.c.data).where(pe.c.sn.in_(sns)).order_by(pe.c.t, pe.c.id)).all():
            evs[sn].append((t, ev, data or {}))
    steps = []
    # 1. 炉次
    mats = db.get(c, 'material', code='SGH-450-B')
    unit_kg = (mats or {}).get('weight_kg') or 18.5
    poured = [p for p in parts if any(e[1] == 'poured' for e in evs[p['sn']])]
    s1 = {'key': 'heat', 'label': _tr('炉次'), 'ref': heat, 'in': len(parts), 'out': len(parts), 'scrap': 0, 'wip': 0, 'hold': 0, 'checks': []}
    if h:
        s1['facts'] = {'出铁量 kg': h['kg'], 'C %': h['C'], 'Si %': h['Si'], 'CE': h['CE'], '出铁温度': h['tap_temp'], '铸件计数': h['castings'], '报废计数': h['scrap']}
        s1['in'] = h['castings'] if h['castings'] is not None else len(parts)
        s1['checks'].append({'name': _tr('炉次铸件计数 = 该炉次零件码数'), 'a': h['castings'] or 0, 'b': len(poured) or len(parts), 'ok': (h['castings'] or 0) == (len(poured) or len(parts))})
        if h['kg']:
            cap = int(h['kg'] / unit_kg)
            s1['checks'].append({'name': _tr('出铁量最多可浇铸件数（{kg:g} kg ÷ 单重 {unit_kg:g} kg）≥ 零件数', kg=h["kg"], unit_kg=unit_kg), 'a': cap, 'b': len(parts), 'ok': cap >= len(parts)})
        scr = sum(1 for p in parts if p['status'] == 'scrap')
        s1['checks'].append({'name': _tr('炉次报废计数 = 该炉次报废零件数'), 'a': h['scrap'] or 0, 'b': scr, 'ok': (h['scrap'] or 0) == scr})
    else:
        s1['facts'] = {'说明': _tr('外购毛坯批次（没有炉次记录）')}
    steps.append(s1)
    # 2. 浇包
    tr = TABLES['treatment_rec']
    treats = db.rows(c, 'treatment_rec', order=tr.c.time, heat_code=heat)
    by_ladle = defaultdict(list)
    for p in parts:
        by_ladle[(p['ladle'] or '').split('#')[0] or _tr('（无浇包）')].append(p)
    tre_l = {(t['ladle'] or '').split('#')[0] for t in treats}
    ladles = []
    for lad, ps in sorted(by_ladle.items()):
        trs = [t for t in treats if (t['ladle'] or '').split('#')[0] == lad]
        kg = sum(t['kg'] or 0 for t in trs)
        ladles.append({'ladle': lad, 'parts': len(ps), 'treatments': len(trs), 'mg': trs[-1]['mg'] if trs else None, 'temp': trs[-1]['temp'] if trs else None, 'kg': kg or None,
                       'ok': bool(trs) or lad in _i18n.variants('（无浇包）') or not h})
    s2 = {'key': 'ladle', 'label': _tr('浇包'), 'ref': _tr('{len} 包', len=len([x for x in ladles if x["ladle"] not in _i18n.variants("（无浇包）")])), 'in': len(parts), 'out': sum(x['parts'] for x in ladles),
          'scrap': 0, 'wip': 0, 'hold': 0, 'ladles': ladles, 'checks': []}
    if h:
        miss = [x['ladle'] for x in ladles if not x['ok']]
        s2['checks'].append({'name': _tr('每个浇包都有球化记录'), 'a': len(ladles) - len(miss), 'b': len(ladles), 'ok': not miss, 'detail': _tr('缺：') + _tr('、').join(miss) if miss else ''})
        s2['checks'].append({'name': _tr('各浇包零件数合计 = 零件码数'), 'a': sum(x['parts'] for x in ladles), 'b': len(parts), 'ok': sum(x['parts'] for x in ladles) == len(parts)})
        no_ladle = len(by_ladle.get(_tr('（无浇包）'), []))
        if no_ladle:
            s2['checks'].append({'name': _tr('零件都记了浇包'), 'a': len(parts) - no_ladle, 'b': len(parts), 'ok': False, 'detail': _tr('{no_ladle} 件没有浇包号', no_ladle=no_ladle)})
    steps.append(s2)

    # 每个零件走到了哪一步
    def evset(p):
        return {e[1] for e in evs[p['sn']]}

    def first_t(p, names):
        return next((e[0] for e in evs[p['sn']] if e[1] in names), None)

    anomalies = defaultdict(list)
    cast_out, cast_scrap, cast_wip, cast_hold = [], [], [], []
    for p in parts:
        es = evset(p)
        st = 'scrap' if p['status'] == 'scrap' else 'hold' if p['status'] == 'hold' else None
        left = bool(es & CAST_OUT)
        scrap_t = first_t(p, {'scrap'})
        mach_t = first_t(p, MACH_IN)
        if st == 'scrap' and (not mach_t or (scrap_t and scrap_t < mach_t)):
            cast_scrap.append(p)
        elif left:
            cast_out.append(p)
        elif st == 'hold':
            cast_hold.append(p)
        else:
            cast_wip.append(p)
    s3 = {'key': 'part', 'label': _tr('零件码（铸造 → 毛坯）'), 'ref': _tr('{len} 件', len=len(parts)), 'in': len(parts), 'out': len(cast_out), 'scrap': len(cast_scrap), 'wip': len(cast_wip),
          'hold': len(cast_hold), 'checks': []}
    steps.append(s3)
    m_in = [p for p in cast_out if evset(p) & MACH_IN]
    m_out = [p for p in m_in if 'machined' in evset(p)]
    m_scrap = [p for p in m_in if 'machined' not in evset(p) and p['status'] == 'scrap']
    m_hold = [p for p in m_in if 'machined' not in evset(p) and p['status'] == 'hold']
    m_wip = [p for p in m_in if p not in m_out and p not in m_scrap and p not in m_hold]
    blank_store = [p for p in cast_out if p not in m_in]
    s4 = {'key': 'machining', 'label': _tr('机加工'), 'ref': _tr('、').join(sorted({p['machine'] for p in m_out if p['machine']})) or '—', 'in': len(cast_out), 'out': len(m_out),
          'scrap': len(m_scrap), 'wip': len(m_wip) + len(blank_store), 'hold': len(m_hold), 'checks': [],
          'facts': {'毛坯库/待上线': len(blank_store), '在机加工': len(m_wip)}}
    for p in m_out:
        if not p['machine']:
            anomalies['machining'].append({'sn': p['sn'], 'issue': _tr('有机加完成事件但没有记录机床')})
    steps.append(s4)
    i_ok, i_scrap, i_wip, i_hold, rework = [], [], [], [], 0
    for p in m_out:
        ins = [e for e in evs[p['sn']] if e[1] == 'inspected']
        rework += sum(1 for e in evs[p['sn']] if e[1] == 'rework')
        last = ins[-1][2].get('result') if ins else None
        if p['status'] == 'scrap':
            i_scrap.append(p)
        elif last == 'OK' or p['status'] in ('good', 'fg', 'shipped'):
            i_ok.append(p)
            if not ins:
                anomalies['inspection'].append({'sn': p['sn'], 'issue': _tr('状态是{status}，但没有检验记录（未检先放行）', status=p['status'])})
        elif p['status'] == 'hold':
            i_hold.append(p)
        else:
            i_wip.append(p)
    ir = TABLES['insp_record']
    recs = c.execute(sa.select(sa.func.count()).select_from(ir).where(ir.c.sn.in_(sns) & (ir.c.itype == 'IPQC'))).scalar() if sns else 0
    insp_ev = sum(1 for p in parts for e in evs[p['sn']] if e[1] == 'inspected')
    s5 = {'key': 'inspection', 'label': _tr('检验'), 'ref': _tr('返修 {rework} 次', rework=rework), 'in': len(m_out), 'out': len(i_ok), 'scrap': len(i_scrap), 'wip': len(i_wip), 'hold': len(i_hold),
          'checks': [{'name': _tr('检验事件数 = 检验单数（逐件）'), 'a': insp_ev, 'b': recs, 'ok': insp_ev == recs}]}
    steps.append(s5)
    stored = [p for p in i_ok if evset(p) & {'stored', 'shipped'} or p['status'] in ('fg', 'shipped')]
    shipped = [p for p in stored if p['status'] == 'shipped' or 'shipped' in evset(p)]
    wait = [p for p in i_ok if p not in stored]
    for p in stored:
        if p['status'] == 'hold' or (p['status'] == 'shipped' and db.get(c, 'lot_hold', lot=heat, status='hold')):
            anomalies['outbound'].append({'sn': p['sn'], 'issue': _tr('冻结批次的零件出库了')})
    st_, wt = TABLES['stock_txn'], TABLES['warehouse']
    fg_wh = [r[0] for r in c.execute(sa.select(wt.c.id).where(wt.c.wtype == 'fg')).all()]     # 只看成品库（毛坯转储也是"入库"，不算）
    cnt_txn = lambda tt: c.execute(sa.select(sa.func.count()).select_from(st_).where((st_.c.ttype == tt) & st_.c.ref_doc.in_(sns) & st_.c.warehouse.in_(fg_wh))).scalar() if sns else 0  # noqa: E731
    txn_in, txn_out = cnt_txn('in'), cnt_txn('out')
    s6 = {'key': 'outbound', 'label': _tr('入库 / 出库'), 'ref': _tr('入库 {len} · 发运 {len2}', len=len(stored), len2=len(shipped)), 'in': len(i_ok), 'out': len(stored), 'scrap': 0, 'wip': len(wait),
          'hold': 0, 'facts': {'成品库存（未发运）': len(stored) - len(shipped), '已发运': len(shipped), '检验合格待入库': len(wait)},
          'checks': [{'name': _tr('成品入库单数 = 入库零件数'), 'a': txn_in, 'b': len([p for p in stored if 'stored' in evset(p)]), 'ok': txn_in == len([p for p in stored if 'stored' in evset(p)])},
                     {'name': _tr('发运出库单数 = 发运零件数'), 'a': txn_out, 'b': len(shipped), 'ok': txn_out == len(shipped)}]}
    steps.append(s6)
    for s in steps:
        s['diff'] = s['in'] - s['out'] - s['scrap'] - s['wip'] - s['hold']
        s['anomalies'] = anomalies.get(s['key'], [])[:50]
        s['ok'] = s['diff'] == 0 and all(x['ok'] for x in s['checks']) and not s['anomalies']
    # 链上的样例零件（每一步各取一件真实的零件码，点开看单件追溯）
    sample = (shipped or stored or i_ok or m_out or parts)[:1]
    return {'heat': heat, 'is_heat': bool(h), 'parts': len(parts), 'steps': steps, 'ok': all(s['ok'] for s in steps),
            'mismatches': sum(0 if s['ok'] else 1 for s in steps), 'sample_sn': sample[0]['sn'] if sample else None,
            'work_orders': sorted({p['work_order'] for p in parts if p['work_order']}),
            'lists': {'scrap': [{'sn': p['sn'], 'cause': p['scrap_cause'], 'station': p['station']} for p in parts if p['status'] == 'scrap'][:100],
                      'wip': [{'sn': p['sn'], 'status': p['status'], 'station': p['station']} for p in cast_wip + m_wip + blank_store + i_wip + wait][:100]}}


@router.get('/api/trace/recon')
def recon(heat: str = '', user=Depends(current_user)):
    with db.engine.connect() as c:
        heat = heat.strip() or _default_heat(c)
        if not heat:
            raise HTTPException(404, _tr('还没有带炉次/批次的零件'))
        d = reconcile(c, heat)
    if not d:
        raise HTTPException(404, _tr('没有炉次/批次 {heat}', heat=heat))
    return d
