"""铸造包：炉次记录、炉料倒冲、配料优化（线性规划）、炉次分析。"""
from ..i18n import _tr
import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db
from ..auth import current_user, require_write
from ..crud import attach_labels, out
from ..db import clock
from ..schema import TABLES

router = APIRouter()
ELEMS = ('C', 'Si', 'Mn', 'S', 'P')


def on_heat(c, eid, ev, ts):
    code = ev['heat']
    h = db.get(c, 'heat', code=code)
    if ev.get('phase') == 'start':
        if not h:
            db.insert(c, 'heat', {'code': code, 'furnace': eid, 'start': ts, 'status': 'melting', 'castings': 0, 'scrap': 0}, raw=True)
    elif ev.get('phase') == 'end':
        kg = ev.get('kg') or 0
        vals = {'end': ts, 'kwh': ev.get('kwh'), 'kg': kg, 'kwh_per_t': round(ev['kwh'] / (kg / 1000), 1) if kg and ev.get('kwh') else None,
                'lining': ev.get('lining'), 'status': 'done'}
        if h:
            db.update(c, 'heat', h['id'], vals, raw=True)
        else:
            db.insert(c, 'heat', {'code': code, 'furnace': eid, **vals}, raw=True)
        backflush(c, code, kg)


def on_chem(c, ev, ts):
    h = db.get(c, 'heat', code=ev['heat'])
    vals = {k: ev.get(k) for k in ELEMS} | {'CE': ev.get('CE'), 'tap_temp': ev.get('temp'), 'status': 'holding'}
    if h:
        db.update(c, 'heat', h['id'], vals, raw=True)


def backflush(c, heat_code, kg):
    """炉次结束按最近一次配料方案倒冲炉料消耗（没有方案用默认配比）。"""
    if not kg:
        return
    from .warehouse import move
    wh = db.get(c, 'warehouse', wtype='raw')
    if not wh:
        return
    plan = db.rows(c, 'charge_plan', order=TABLES['charge_plan'].c.id.desc(), limit=1)
    ratios = {d['code']: d['pct'] for d in (plan[0]['detail'] or [])} if plan else None
    for m in db.rows(c, 'charge_material'):
        pct = ratios.get(m['code']) if ratios else {'CM-PIG': 45, 'CM-STEEL': 25, 'CM-RET': 28, 'CM-CARB': 1, 'CM-FESI': 1}.get(m['code'], 0)
        if not pct:
            continue
        mat = db.get(c, 'material', code=m['code'])
        if mat:
            move(c, 'out', mat['id'], round(kg * pct / 100 / 1000, 3), wh['id'], ref=heat_code, who=_tr('系统倒冲'), reason=_tr('炉次炉料消耗'))
        t = TABLES['charge_material']
        c.execute(t.update().where(t.c.id == m['id']).values(stock_t=sa.func.coalesce(t.c.stock_t, 0) - kg * pct / 100 / 1000))


def optimize(mats, target, furnace_kg):
    """最低成本配料：x_i 为各炉料比例（%），满足成分区间、配比上下限和库存。"""
    try:
        from scipy.optimize import linprog
    except ImportError:
        raise HTTPException(500, _tr('服务器缺少 scipy，无法求解'))
    n = len(mats)
    cost = [m['price'] or 0 for m in mats]
    A_ub, b_ub = [], []
    for el in ELEMS:
        lo, hi = target.get(el + '_lo'), target.get(el + '_hi')
        coef = [(m[el] or 0) * (m['yield_pct'] or 100) / 100 for m in mats]
        if hi is not None:
            A_ub.append(coef); b_ub.append(hi)
        if lo is not None:
            A_ub.append([-x for x in coef]); b_ub.append(-lo)
    bounds, unknown = [], []
    for m in mats:
        lo = (m['min_ratio'] or 0) / 100
        hi = (m['max_ratio'] if m['max_ratio'] is not None else 100) / 100
        if m['stock_t'] is not None and m['stock_t'] < 0:
            unknown.append(m['name'] or m['code'])      # 库存是负数 = 账不准（炉次倒冲扣了、入库没登记）：不按库存限制，结果里提示
        elif m['stock_t'] is not None and furnace_kg:
            hi = min(hi, m['stock_t'] * 1000 / furnace_kg)
        bounds.append((lo, max(lo, hi)))
    res = linprog(cost, A_ub=A_ub or None, b_ub=b_ub or None, A_eq=[[1] * n], b_eq=[1], bounds=bounds, method='highs')
    if not res.success:
        return None
    x = res.x
    comp = {el: round(sum(x[i] * (mats[i][el] or 0) * (mats[i]['yield_pct'] or 100) / 100 for i in range(n)), 3) for el in ELEMS}
    return {'cost': round(res.fun, 1), 'comp': comp,
            'stock_note': _tr('{join} 的库存是负数（账不准，可能有炉料入库没登记），这次没按库存限制，请核对库存', join=_tr('、').join(unknown)) if unknown else '',
            'detail': [{'code': mats[i]['code'], 'name': mats[i]['name'], 'pct': round(x[i] * 100, 2), 'kg': round(x[i] * furnace_kg, 1),
                        'price': mats[i]['price'], 'cost': round(x[i] * (mats[i]['price'] or 0), 1)} for i in range(n) if x[i] > 1e-5]}


@router.post('/api/foundry/optimize')
def run_optimize(body: dict = Body(...), user=Depends(current_user)):
    target = {k: (float(v) if v not in (None, '') else None) for k, v in (body.get('target') or {}).items()}
    kg = float(body.get('furnace_kg') or 3000)
    with db.engine.connect() as c:
        mats = db.rows(c, 'charge_material', order=TABLES['charge_material'].c.id)
    if not mats:
        raise HTTPException(400, _tr('请先维护炉料'))
    r = optimize(mats, target, kg)
    if not r:
        return {'ok': False, 'msg': _tr('在当前成分要求、配比限制和库存下无可行解，请放宽某一项')}
    # 对比：常规经验配比的成本
    base = {'CM-PIG': 45, 'CM-STEEL': 25, 'CM-RET': 28, 'CM-CARB': 1, 'CM-FESI': 1}
    base_cost = sum((m['price'] or 0) * base.get(m['code'], 0) / 100 for m in mats)
    r['base_cost'] = round(base_cost, 1)
    r['saving_per_t'] = round(base_cost - r['cost'], 1)
    if body.get('save'):
        require_write(user, 'charge_material')
        with db.engine.begin() as c:
            pid = db.insert(c, 'charge_plan', {'furnace_kg': kg, 'target_C': target.get('C_lo'), 'target_Si': target.get('Si_lo'), 'cost': r['cost'],
                                               'detail': r['detail'], 'time': clock.now(), 'remark': _tr('比经验配比每吨省 {saving_per_t} 元', saving_per_t=r['saving_per_t'])}, raw=True)
        r['plan_id'] = pid
    return {'ok': True, **r}


@router.get('/api/foundry/heats')
def heats(n: int = 60, user=Depends(current_user)):
    ht = TABLES['heat']
    with db.engine.connect() as c:
        rows = db.rows(c, 'heat', order=ht.c.id.desc(), limit=n)
        attach_labels(c, 'heat', rows)
        tr = TABLES['treatment_rec']
        mg = {r[0]: r[1] for r in c.execute(sa.select(tr.c.heat_code, sa.func.avg(tr.c.mg)).group_by(tr.c.heat_code)).all()}
    for r in rows:
        r['mg_avg'] = round(mg.get(r['code']) or 0, 4) or None
        r['scrap_rate'] = round((r['scrap'] or 0) / r['castings'], 4) if r['castings'] else None
    done = [r for r in rows if r['status'] == 'done' and r['kwh_per_t']]
    avg = {'kwh_per_t': round(sum(r['kwh_per_t'] for r in done) / len(done), 1) if done else None,
           'CE': round(sum(r['CE'] for r in rows if r['CE']) / max(1, sum(1 for r in rows if r['CE'])), 3)}
    return {'rows': out(list(reversed(rows))), 'avg': avg}
