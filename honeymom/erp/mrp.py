"""MRP：销售订单交货计划 + 预测（被订单冲减）+ 安全库存 → 多层 BOM 展开（低层码）→ 对现有库存、在途采购、在制工单、已批请购净算，
按提前期倒推下达日 → 计划生产 / 计划采购建议 + 提前（在途来晚了）建议，每条建议带需求来源（pegging，一直追到最上层的订单）；
粗能力检查（计划生产的工时 + 排产已占的工时 对比 班次产能，按工位组按周）；计划员调整后下达成工单 / 请购 / 采购订单。"""
from ..i18n import _tr
import math
from collections import defaultdict
from datetime import datetime, time, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db, perms
from ..crud import action, attach_labels, out
from ..db import clock
from ..perms import require
from ..schema import TABLES
from . import core
from .core import as_date, today, who

router = APIRouter()
P_RUN = perms.declare('mrp.run', '运行 MRP', '计划 MRP')
P_RELEASE = perms.declare('mrp.release', '下达 MRP 建议（生成工单 / 请购 / 采购订单）', '计划 MRP')
DEF_LEAD = {'make': 2, 'buy': 7}


def make_or_buy(c, material) -> str:
    mp = db.get(c, 'mat_plan', material=int(material))
    if mp and mp.get('make_buy'):
        return mp['make_buy']
    return 'make' if db.get(c, 'bom', product=int(material)) else 'buy'


def plan_params(c, material, mb=None) -> dict:
    m = db.get(c, 'material', int(material)) or {}
    mp = db.get(c, 'mat_plan', material=int(material)) or {}
    mb = mb or make_or_buy(c, material)
    sup = mp.get('supplier')
    it = None
    if mb == 'buy':
        it = core.supplier_item(c, sup, material) if sup else None
        if not it:
            it = core.best_supplier(c, material)
            sup = sup or (it or {}).get('supplier')
    lead = mp.get('lead_days')
    if lead is None:
        lead = (it or {}).get('lead_days') if mb == 'buy' else None
    if lead is None:
        lead = DEF_LEAD[mb]
    ss = mp.get('safety_stock')
    if ss is None:
        ss = m.get('safety_stock') or 0
    return {'make_buy': mb, 'lead': int(lead), 'min_lot': float(mp.get('min_lot') or (it or {}).get('moq') or 0), 'multiple': float(mp.get('lot_multiple') or 0),
            'safety': float(ss or 0), 'scrap': float(mp.get('scrap_pct') or 0), 'supplier': sup, 'unit': m.get('unit') or '', 'code': m.get('code'),
            'name': m.get('name'), 'mtype': m.get('mtype')}


def lot_qty(need, p):
    q = max(need, p['min_lot'] or 0)
    if p['multiple']:
        q = math.ceil(q / p['multiple'] - 1e-9) * p['multiple']
    if (p['unit'] or '').lower() in ('件', '个', '套', '台', '箱', 'pcs', 'pc', 'piece', 'pieces', 'ea', 'set', 'sets', 'unit', 'units', 'box', 'boxes') or p['mtype'] in ('fg', 'semi'):
        q = math.ceil(q - 1e-9)
    return round(q, 4)


def low_level_codes(boms) -> dict:
    """低层码：物料在所有 BOM 里出现的最深层级（成品 0）。有循环时报错。"""
    parents = defaultdict(set)
    for p, kids in boms.items():
        for k, _ in kids:
            parents[k].add(p)
    memo, stack = {}, set()

    def lv(m):
        if m in memo:
            return memo[m]
        if m in stack:
            raise HTTPException(409, _tr('BOM 有循环引用，MRP 无法展开'))
        stack.add(m)
        v = max((lv(p) + 1 for p in parents.get(m, ())), default=0)
        stack.discard(m)
        memo[m] = v
        return v
    allm = set(boms) | set(parents)
    return {m: lv(m) for m in allm}


def _bom(c):
    from .cost import bom_map
    return bom_map(c)


def run(c, user=None, horizon=90, forecast=True, safety=True):
    now = today()
    end = now + timedelta(days=horizon)
    boms = _bom(c)
    llc = low_level_codes(boms)
    mats = {m['id']: m for m in db.rows(c, 'material')}
    demands = defaultdict(list)          # 物料 → [{date, qty, kind, ref, label, peg}]
    supplies = defaultdict(list)         # 物料 → [{date, qty, kind, ref, label, doc}]
    so, sl, sch = TABLES['sales_order'], TABLES['so_line'], TABLES['so_schedule']
    custs = {x['id']: x for x in db.rows(c, 'customer')}
    so_week = defaultdict(float)
    q = sa.select(sch.c.id, sch.c.due, sch.c.qty, sch.c.qty_shipped, sl.c.material, sl.c.seq, so.c.code, so.c.customer, so.c.id.label('so_id')).join(
        sl, sl.c.id == sch.c.so_line).join(so, so.c.id == sl.c.sales_order).where(so.c.status.in_(('confirmed', 'partial', 'credit_hold')) & (sl.c.status == 'open'))
    for r in c.execute(q):
        left = (r.qty or 0) - (r.qty_shipped or 0)
        if left <= 1e-9:
            continue
        d = as_date(r.due) or now
        if d > end:
            continue
        cu = custs.get(r.customer, {})
        lab = _tr('销售订单 {code} 第 {seq} 行（{v}）', code=r.code, seq=r.seq, v=cu.get('short_name') or cu.get('name') or '')
        demands[r.material].append({'date': d, 'qty': left, 'kind': 'so', 'ref': f'{r.code}#{r.seq}', 'label': lab,
                                    'top': [{'kind': 'so', 'ref': r.code, 'so_id': r.so_id, 'label': lab, 'date': str(d), 'qty': left}]})
        so_week[(r.material, d - timedelta(days=d.weekday()))] += left
    if forecast:
        ft = TABLES['forecast']
        for f in db.rows(c, 'forecast', ft.c.due >= now - timedelta(days=7), ft.c.due <= end):
            d = as_date(f['due'])
            wk = d - timedelta(days=d.weekday())
            used = min(so_week[(f['material'], wk)], f['qty'])
            so_week[(f['material'], wk)] -= used
            left = f['qty'] - used
            if left > 1e-9:
                lab = _tr('需求预测 {d}', d=d) + (_tr('（订单已冲减 {used:g}）', used=used) if used else '')
                demands[f['material']].append({'date': max(d, now), 'qty': left, 'kind': 'forecast', 'ref': f"FC#{f['id']}", 'label': lab,
                                               'top': [{'kind': 'forecast', 'ref': f"FC#{f['id']}", 'label': lab, 'date': str(d), 'qty': left}]})
    # 在制工单：产出是供应，未领的子件是需求
    wot, mi = TABLES['work_order'], TABLES['material_issue']
    issued = defaultdict(float)
    for r in c.execute(sa.select(mi.c.work_order, mi.c.material, mi.c.kind, sa.func.sum(mi.c.qty)).where(mi.c.work_order.isnot(None)).group_by(
            mi.c.work_order, mi.c.material, mi.c.kind)):
        issued[(r[0], r[1])] += (r[3] or 0) * (-1 if r[2] == 'return' else 1)
    for w in db.rows(c, 'work_order', wot.c.status.in_(('draft', 'released', 'in_progress'))):
        left = max(0.0, (w['qty'] or 0) - (w['qty_done'] or 0) - (w['qty_scrap'] or 0))
        if left <= 0:
            continue
        due = as_date(w['due']) or now
        supplies[w['product']].append({'date': due, 'qty': left, 'kind': 'wo', 'ref': w['code'], 'label': _tr('工单 {code}（{v}）', code=w['code'], v=_tr('草稿') if w['status'] == 'draft' else _tr('在制')),
                                       'doc': ('work_order', w['id'])})
        start = as_date(w['start_plan']) or now
        for comp, bq in boms.get(w['product'], []):
            need = bq * (w['qty'] or 0) - issued[(w['id'], comp)]
            need = min(need, bq * left) if issued[(w['id'], comp)] == 0 else need
            if need > 1e-9:
                lab = _tr('工单 {code} 用料（未领）', code=w['code'])
                demands[comp].append({'date': max(start, now), 'qty': need, 'kind': 'wo', 'ref': w['code'], 'label': lab,
                                      'top': [{'kind': 'wo', 'ref': w['code'], 'label': lab, 'date': str(start), 'qty': need}]})
    po, pl = TABLES['purchase_order'], TABLES['po_line']
    for r in c.execute(sa.select(pl.c.id, pl.c.material, pl.c.qty, pl.c.qty_received, pl.c.due, pl.c.seq, po.c.code, po.c.id.label('po_id')).join(
            po, po.c.id == pl.c.purchase_order).where(po.c.status.in_(('draft', 'approved', 'sent', 'partial')) & (pl.c.status == 'open'))):
        left = (r.qty or 0) - (r.qty_received or 0)
        if left > 1e-9:
            supplies[r.material].append({'date': as_date(r.due) or now, 'qty': left, 'kind': 'po', 'ref': f'{r.code}#{r.seq}', 'label': _tr('采购订单 {code} 在途', code=r.code),
                                         'doc': ('po_line', r.id)})
    rq = TABLES['purchase_req']
    for r in db.rows(c, 'purchase_req', rq.c.status.in_(('submitted', 'approved'))):
        supplies[r['material']].append({'date': as_date(r['need_date']) or now, 'qty': r['qty'], 'kind': 'req', 'ref': r['code'], 'label': _tr('请购 {code}', code=r['code']),
                                        'doc': ('purchase_req', r['id'])})
    rl, rt = TABLES['receipt_line'], TABLES['purchase_receipt']
    for r in c.execute(sa.select(rl.c.material, rl.c.qty, rt.c.code).join(rt, rt.c.id == rl.c.purchase_receipt).where(rl.c.status == 'inspecting')):
        supplies[r.material].append({'date': now, 'qty': r.qty or 0, 'kind': 'iqc', 'ref': r.code, 'label': _tr('收货 {code} 来料检验中', code=r.code), 'doc': None})
    st = TABLES['stock']
    onhand = {r[0]: max(0.0, float(r[1] or 0)) for r in c.execute(sa.select(st.c.material, sa.func.sum(st.c.qty)).group_by(st.c.material))}
    # 逐层净算
    sugg = []
    params = {}
    involved = set(demands) | set(supplies) | {m for m, mm in mats.items() if safety and (mm.get('safety_stock') or 0) > 0}
    involved |= {r['material'] for r in db.rows(c, 'mat_plan') if safety and (r.get('safety_stock') or 0) > 0}
    max_lv = max(llc.values(), default=0) + 1
    for level in range(0, max_lv + 1):
        todo = sorted(m for m in (set(demands) | involved) if llc.get(m, 0) == level and m in mats)
        for m in todo:
            p = params.setdefault(m, plan_params(c, m))
            dem = sorted(demands.get(m, []), key=lambda x: (x['date'], {'so': 0, 'wo': 1, 'parent': 1, 'forecast': 2}.get(x['kind'], 3)))
            if safety and p['safety'] > 0:
                dem = [{'date': now, 'qty': p['safety'], 'kind': 'safety', 'ref': 'SS', 'label': _tr('安全库存 {safety:g}', safety=p['safety']),
                        'top': [{'kind': 'safety', 'ref': 'SS', 'label': _tr('安全库存 {safety:g}', safety=p['safety']), 'date': str(now), 'qty': p['safety']}]}] + dem
            if not dem:
                continue
            pool = [{'date': now, 'qty': onhand.get(m, 0.0), 'left': onhand.get(m, 0.0), 'kind': 'stock', 'ref': _tr('库存'), 'label': _tr('现有库存 {get:g}', get=onhand.get(m, 0.0))}]
            pool += [{**s, 'left': s['qty']} for s in sorted(supplies.get(m, []), key=lambda s: s['date'])]
            planned = []
            expedite = {}
            for d in dem:
                need = d['qty']
                for s in pool + planned:
                    if need <= 1e-9:
                        break
                    if s['left'] <= 1e-9:
                        continue
                    if s['kind'] == 'planned' and s['date'] > d['date']:
                        continue
                    take = min(s['left'], need)
                    s['left'] -= take
                    need -= take
                    if s['kind'] == 'planned':
                        s['peg'].append({**{k: d[k] for k in ('kind', 'ref', 'label', 'qty')}, 'date': str(d['date']), 'qty': take, 'top': d['top']})
                    elif s['kind'] in ('po', 'wo', 'req') and s['date'] > d['date']:
                        e = expedite.setdefault(s['ref'], {'s': s, 'to': d['date'], 'qty': 0.0, 'for': []})
                        e['to'] = min(e['to'], d['date'])
                        e['qty'] += take
                        e['for'].append(d['label'])
                if need > 1e-9:
                    q_ = lot_qty(need, p)
                    rel = d['date'] - timedelta(days=p['lead'])
                    po_ = {'kind': 'planned', 'date': d['date'], 'qty': q_, 'left': q_ - need, 'release': rel, 'need': need,
                           'peg': [{**{k: d[k] for k in ('kind', 'ref', 'label')}, 'date': str(d['date']), 'qty': need, 'top': d['top']}]}
                    planned.append(po_)
            for po_ in planned:
                idx = len(sugg)
                tops = []
                for pg in po_['peg']:
                    tops += pg.get('top') or []
                rel = po_['release']
                parts = [_tr('{label} 需要 {qty:g}{unit}（{date}）', label=x['label'], qty=x['qty'], unit=p['unit'], date=x['date']) for x in po_['peg'][:4]]
                avail = onhand.get(m, 0.0)
                why = _tr('；').join(parts) + (_tr(' 等 {len} 项', len=len(po_['peg'])) if len(po_['peg']) > 4 else '')
                why += _tr('。现有库存 {avail:g}，在途 {sum:g}，都已分配完；', avail=avail, sum=sum(s['qty'] for s in supplies.get(m, [])))
                why += _tr('净需求 {sum:g}', sum=sum(x['qty'] for x in po_['peg'])) + (_tr('，按最小批量 / 批量倍数取 {qty:g}', qty=po_['qty']) if po_['qty'] - sum(x['qty'] for x in po_['peg']) > 1e-6 else '')
                why += _tr('；提前期 {lead} 天，最晚 {rel} 下达', lead=p['lead'], rel=rel) + (_tr('（已经晚了，请加急）') if rel < now else '')
                sugg.append({'kind': p['make_buy'], 'material': m, 'level': llc.get(m, 0), 'qty': po_['qty'], 'need_date': po_['date'], 'release_date': rel,
                             'supplier': p['supplier'] if p['make_buy'] == 'buy' else None, 'pegging': po_['peg'], 'reason': why, '_tops': tops})
                if p['make_buy'] == 'make':
                    for comp, bq in boms.get(m, []):
                        cp = params.setdefault(comp, plan_params(c, comp))
                        dq = bq * po_['qty'] * (1 + cp['scrap'] / 100)
                        lab = _tr('计划生产 {code} × {qty:g}（建议 #{v}）', code=p['code'], qty=po_['qty'], v=idx + 1)
                        demands[comp].append({'date': max(rel, now), 'qty': dq, 'kind': 'parent', 'ref': f'#{idx + 1}', 'label': lab, 'parent': idx, 'top': tops})
            for ref, e in expedite.items():
                s = e['s']
                sugg.append({'kind': 'expedite', 'material': m, 'level': llc.get(m, 0), 'qty': e['qty'], 'need_date': e['to'], 'release_date': now,
                             'ref_doc': ref, 'pegging': [{'kind': 'supply', 'ref': ref, 'label': s['label'], 'date': str(s['date']), 'qty': e['qty']}],
                             'reason': _tr('{label} 预计 {date} 到，但 {v}{v2} 在 {to} 就要用 {qty:g}{unit}：建议提前到 {to}', label=s['label'], date=s['date'], v=e['for'][0], v2=_tr(' 等') if len(e['for']) > 1 else '', to=e['to'], qty=e['qty'], unit=p['unit']),
                             '_doc': s.get('doc')})
    cap = rough_cut(c, [s for s in sugg if s['kind'] == 'make'])
    return sugg, cap


# ───────────────── 粗能力 ─────────────────
def _shift_hours(c) -> float:
    h = 0.0
    for s in db.rows(c, 'shift'):
        if s.get('active') is False:
            continue
        try:
            a = datetime.combine(today(), time(*map(int, str(s['start']).split(':')[:2])))
            b = datetime.combine(today(), time(*map(int, str(s['end']).split(':')[:2])))
        except (ValueError, TypeError):
            continue
        if b <= a:
            b += timedelta(days=1)
        h += (b - a).total_seconds() / 3600 - (s.get('break_min') or 0) / 60
    return h or 8.0


def rough_cut(c, makes):
    """按工位组 × 周：已排产工时（工单工序的排产开始~结束 × 设备台数）+ 计划生产工时（数量 × 标准节拍 ÷ 并行设备数）对比 产能（设备数 × 班次小时 × 7）。"""
    from ..services.production import group_capacity
    caps = group_capacity(c)
    day_h = _shift_hours(c)
    load = defaultdict(lambda: {'existing': 0.0, 'planned': 0.0})
    opt, wot = TABLES['wo_op'], TABLES['work_order']
    for o in c.execute(sa.select(opt.c.station_group, opt.c.plan_start, opt.c.plan_end, opt.c.machines, opt.c.status).join(wot, wot.c.id == opt.c.work_order).where(
            wot.c.status.in_(('released', 'in_progress')) & (opt.c.status != 'done'))):
        if not o.plan_start or not o.plan_end:
            continue
        g = o.station_group or '—'
        n = len([x for x in (o.machines or '').split(',') if x]) or 1
        h = (o.plan_end - o.plan_start).total_seconds() / 3600 * n
        d = o.plan_start.date()
        load[(g, d - timedelta(days=d.weekday()))]['existing'] += h
    routes = {}
    per_sugg = []
    for s in makes:
        rt = routes.get(s['material'])
        if rt is None:
            r = db.get(c, 'route', product=s['material'], status='active')
            rt = db.rows(c, 'route_step', TABLES['route_step'].c.route == r['id'], order=TABLES['route_step'].c.seq) if r else []
            routes[s['material']] = rt
        d = s['release_date'] if s['release_date'] >= today() else today()
        wk = d - timedelta(days=d.weekday())
        mine = []
        for st in rt:
            g = st['station_group'] or '—'
            par = max(1, caps.get(g, 1))
            h = (s['qty'] * (st['std_cycle_s'] or 0) / 3600) / par * par      # 设备工时（并行不省工时，省的是日历时间）
            load[(g, wk)]['planned'] += h
            mine.append((g, wk))
        per_sugg.append(mine)
    rows = []
    for (g, wk), v in sorted(load.items(), key=lambda kv: (str(kv[0][1]), kv[0][0])):
        capacity = max(1, caps.get(g, 1)) * day_h * 7
        tot = v['existing'] + v['planned']
        rows.append({'group': g, 'week': str(wk), 'existing_h': round(v['existing'], 1), 'planned_h': round(v['planned'], 1), 'capacity_h': round(capacity, 1),
                     'load_pct': round(tot / capacity, 3) if capacity else None, 'over': tot > capacity * 1.0 + 1e-6, 'machines': caps.get(g, 0)})
    over = {(r['group'], r['week']) for r in rows if r['over']}
    for s, mine in zip(makes, per_sugg):
        s['capacity'] = [{'group': g, 'week': str(wk), 'over': (g, str(wk)) in over} for g, wk in sorted(set(mine), key=lambda x: (str(x[1]), x[0]))]
    return rows


# ───────────────── 保存 / 下达 ─────────────────
def save_run(c, sugg, cap, user, horizon, opts):
    rt = TABLES['mrp_run']
    for old in db.rows(c, 'mrp_run', rt.c.status == 'done'):
        db.update(c, 'mrp_run', old['id'], {'status': 'superseded'}, raw=True)
        st = TABLES['mrp_suggestion']
        c.execute(st.update().where((st.c.run == old['id']) & st.c.status.in_(('new', 'adjusted'))).values(status='ignored', planner=_tr('新运算取代')))
    stats = {'make': sum(1 for s in sugg if s['kind'] == 'make'), 'buy': sum(1 for s in sugg if s['kind'] == 'buy'),
             'expedite': sum(1 for s in sugg if s['kind'] == 'expedite'), 'late_release': sum(1 for s in sugg if s['kind'] in ('make', 'buy') and s['release_date'] < today()),
             'cap_over': [r for r in cap if r['over']][:20], 'capacity': cap[:200]}
    rid = db.insert(c, 'mrp_run', {'run_at': clock.now(), 'horizon_days': horizon, 'params': opts, 'stats': stats, 'created_by': who(user), 'status': 'done'}, raw=True)
    ids = []
    for s in sugg:
        row = {k: v for k, v in s.items() if not k.startswith('_')}
        for pg in row.get('pegging') or []:
            pg.pop('top', None)
        if s.get('_tops'):
            row['pegging'] = row['pegging'] + [{'kind': 'top', 'label': t['label'], 'ref': t['ref'], 'date': t['date'], 'qty': t['qty']} for t in _uniq(s['_tops'])]
        doc = s.get('_doc')
        if doc:
            row['ref_doc'] = f'{doc[0]}:{doc[1]}'
        ids.append(db.insert(c, 'mrp_suggestion', {**row, 'run': rid, 'status': 'new'}, raw=True))
    return rid, ids


def _uniq(tops):
    seen, res = set(), []
    for t in tops:
        k = (t.get('kind'), t.get('ref'))
        if k not in seen:
            seen.add(k)
            res.append(t)
    return res[:20]


def release(c, ids, user, buy_as='req', release_wo=False):
    """下达：计划生产 → 工单（草稿，release_wo=True 时直接下达并排产）；计划采购 → 请购（已批准）或直接采购订单（按供应商合并）；
    提前 → 改在途采购订单行 / 工单的交期。"""
    from .purchase import reqs_to_po
    st = TABLES['mrp_suggestion']
    made = {'work_orders': [], 'reqs': [], 'orders': [], 'expedited': []}
    reqs = []
    for sid in ids:
        s = db.get(c, 'mrp_suggestion', int(sid))
        if not s or s['status'] not in ('new', 'adjusted'):
            continue
        q = s['qty_adj'] if s.get('qty_adj') not in (None, 0) else s['qty']
        d = as_date(s.get('date_adj')) or as_date(s['need_date'])
        run_ = db.get(c, 'mrp_run', s['run'])
        upd = {'status': 'released', 'planner': who(user)}
        if s['kind'] == 'make':
            route = db.get(c, 'route', product=s['material'], status='active')
            tops = [p for p in (s['pegging'] or []) if p.get('kind') == 'top' and str(p.get('ref', '')).startswith('SO')]
            so_ = db.get(c, 'sales_order', code=tops[0]['ref']) if len(tops) == 1 else None
            cust = (db.get(c, 'customer', so_['customer']) or {}) if so_ else {}
            rel = as_date(s['release_date']) or today()
            wid = db.insert(c, 'work_order', {'product': s['material'], 'route': route['id'] if route else None, 'qty': int(math.ceil(q - 1e-9)), 'qty_done': 0,
                                              'qty_scrap': 0, 'start_plan': datetime.combine(max(rel, today()), time(8, 0)),
                                              'due': datetime.combine(d, time(17, 0)), 'status': 'draft', 'priority': 5,
                                              'customer': cust.get('short_name') or cust.get('name'), 'sales_order': so_['id'] if so_ else None,
                                              'remark': _tr('MRP {code} 建议 #{id}', code=run_['code'], id=s['id'])}, raw=True)
            upd['work_order'] = wid
            made['work_orders'].append(core.code_of(c, 'work_order', wid))
            if release_wo:
                from ..crud import run_action
                run_action(c, 'work_order', db.get(c, 'work_order', wid), 'release', {}, user)
        elif s['kind'] == 'buy':
            rid = db.insert(c, 'purchase_req', {'material': s['material'], 'qty': q, 'need_date': d, 'supplier': s['supplier'], 'requester': who(user),
                                                'reason': _tr('MRP {code}：{v}', code=run_['code'], v=(s['reason'] or '')[:150]), 'source': 'mrp', 'status': 'approved',
                                                'mrp_suggestion': s['id']}, raw=True)
            upd['purchase_req'] = rid
            reqs.append((s['id'], rid))
            made['reqs'].append(core.code_of(c, 'purchase_req', rid))
        elif s['kind'] == 'expedite' and s.get('ref_doc'):
            ent, did = s['ref_doc'].split(':')
            if ent == 'po_line':
                db.update(c, 'po_line', int(did), {'due': d}, raw=True)
            elif ent == 'work_order':
                db.update(c, 'work_order', int(did), {'due': datetime.combine(d, time(17, 0)), 'priority': 3}, raw=True)
            elif ent == 'purchase_req':
                db.update(c, 'purchase_req', int(did), {'need_date': d}, raw=True)
            made['expedited'].append(s['ref_doc'])
        c.execute(st.update().where(st.c.id == s['id']).values(**upd, updated_at=clock.now()))
    if buy_as == 'po' and reqs:
        orders = reqs_to_po(c, [r for _, r in reqs], user, source='mrp')
        made['orders'] = [o['code'] for o in orders]
        for sid, rid in reqs:
            r = db.get(c, 'purchase_req', rid)
            c.execute(st.update().where(st.c.id == sid).values(purchase_order=r['purchase_order']))
    return made


@action('mrp_suggestion', 'release')
def _act_release(c, s, body, user):
    perms.check(user, P_RELEASE)
    m = release(c, [s['id']], user, body.get('buy_as') or 'req', bool(body.get('release_wo')))
    return {'msg': _tr('已下达：') + _tr('、').join(m['work_orders'] + m['reqs'] + m['orders'] + m['expedited']), **m}


@action('mrp_suggestion', 'ignore')
def _act_ignore(c, s, body, user):
    db.update(c, 'mrp_suggestion', s['id'], {'status': 'ignored', 'planner': who(user)}, raw=True)
    return {'msg': _tr('已忽略')}


def latest_run(c):
    rt = TABLES['mrp_run']
    r = c.execute(sa.select(rt).order_by(rt.c.id.desc()).limit(1)).mappings().first()
    return dict(r) if r else None


# ───────────────── 接口 ─────────────────
@router.post('/api/mrp/run')
def run_api(body: dict = Body(default={}), user=Depends(require(P_RUN))):
    horizon = int(body.get('horizon') or 90)
    opts = {'forecast': body.get('forecast', True) is not False, 'safety': body.get('safety', True) is not False}
    with db.engine.begin() as c:
        sugg, cap = run(c, user, horizon, **opts)
        rid, ids = save_run(c, sugg, cap, user, horizon, opts)
        r = db.get(c, 'mrp_run', rid)
    return {'ok': True, 'run': out([r])[0], 'count': len(ids)}


@router.get('/api/mrp/latest')
def latest_api(user=Depends(require('mrp.mrp_suggestion.read'))):
    with db.engine.connect() as c:
        r = latest_run(c)
        if not r:
            return {'run': None, 'suggestions': []}
        st = TABLES['mrp_suggestion']
        rows = attach_labels(c, 'mrp_suggestion', db.rows(c, 'mrp_suggestion', st.c.run == r['id'], order=(st.c.level, st.c.need_date, st.c.id)))
        mats = {m['id']: m for m in db.rows(c, 'material')}
        for x in rows:
            m = mats.get(x['material']) or {}
            x['unit'] = m.get('unit')
            x['late'] = bool(x['release_date'] and as_date(x['release_date']) < today() and x['status'] in ('new', 'adjusted'))
        return {'run': out([r])[0], 'suggestions': out(rows)}


@router.put('/api/mrp/suggestions/{sid}')
def adjust_api(sid: int, body: dict = Body(...), user=Depends(require('mrp.mrp_suggestion.update'))):
    with db.engine.begin() as c:
        s = db.get(c, 'mrp_suggestion', sid)
        if not s:
            raise HTTPException(404, _tr('建议不存在'))
        if s['status'] not in ('new', 'adjusted'):
            raise HTTPException(409, _tr('已下达 / 忽略的建议不能再调'))
        upd = {'status': 'adjusted', 'planner': who(user)}
        if body.get('qty') not in (None, ''):
            if float(body['qty']) <= 0:
                raise HTTPException(400, _tr('数量要大于 0'))
            upd['qty_adj'] = float(body['qty'])
        if body.get('date'):
            upd['date_adj'] = as_date(body['date'])
        if body.get('supplier'):
            upd['supplier'] = int(body['supplier'])
        db.update(c, 'mrp_suggestion', sid, upd, raw=True)
    return {'ok': True}


@router.post('/api/mrp/release')
def release_api(body: dict = Body(...), user=Depends(require(P_RELEASE))):
    from . import flow_gate
    pending = flow_gate('mrp_suggestion', 'release_batch', body=body, user=user)       # M3 审批流程（挂了才生效）
    if pending:
        return pending
    with db.engine.begin() as c:
        m = release(c, body.get('ids') or [], user, body.get('buy_as') or 'req', bool(body.get('release_wo')))
    return {'ok': True, **m}


@router.get('/api/mrp/pegging/{sid}')
def pegging_api(sid: int, user=Depends(require('mrp.mrp_suggestion.read'))):
    """一条建议的需求来源树：本层需求 → 上层计划 → 最上层的销售订单 / 预测 / 安全库存。"""
    with db.engine.connect() as c:
        s = db.get(c, 'mrp_suggestion', sid)
        if not s:
            raise HTTPException(404, _tr('建议不存在'))
        attach_labels(c, 'mrp_suggestion', [s])
        return {'item': out([s])[0], 'pegging': s['pegging'] or [], 'capacity': s.get('capacity') or []}
