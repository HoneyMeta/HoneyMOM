"""成本：存货移动平均计价（按出入库记录增量计价，写计价流水）、标准成本（BOM + 工艺路线逐层卷积）、工单实际成本
（料：领料按移动平均 / 标准；工：报工工时 × 人工费率；机：设备运行工时 × 机器费率；能：energy_hour 电量 × 电价；制造费用按分摊规则）、
差异分析（按工单 / 产品 / 期间）、月末结账（锁期间、冻结工单成本、存货快照）。

计价依据（stock_txn → 单价）：采购入库 = 收货行单价；销售退货入库 = 原发货成本；生产入库 / 毛坯转储 / 期初 = 标准成本（没有就取物料单价）；
出库、盘点调整 = 当前移动平均。生产入库按标准成本入账，实际与标准的差额在"工单成本"里体现（差异），凭证里结转。"""
from ..i18n import _tr
from .. import i18n as _i18n
import math
from collections import defaultdict
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import audit, compat, db, perms
from ..crud import action, attach_labels, out
from ..db import clock
from ..perms import require
from ..schema import TABLES
from . import core
from .core import as_date, money, today, who

router = APIRouter()
P_CALC = perms.declare('cost.calc', '计算成本（计价、工单成本、标准成本卷积）', '成本核算')


# ───────────────── BOM ─────────────────
def bom_map(c) -> dict:
    """产品 → [(子件, 每件用量（子件基本单位）)]。BOM 行写了单位且和子件基本单位不同时，按 M3 的单位换算折算。"""
    try:
        from ..impl import uom
    except Exception:          # noqa: BLE001
        uom = None
    bt = TABLES['bom']
    res = defaultdict(list)
    for b in db.rows(c, 'bom'):
        if b.get('status') in ('obsolete', 'inactive'):
            continue
        q = float(b['qty'] or 0)
        if uom and b.get('unit'):
            try:
                base = uom.base_unit(c, b['component'])
                if base and uom.norm(b['unit']) != uom.norm(base):
                    f = uom.factor(c, b['component'], b['unit'], base)
                    if f:
                        q *= f
            except Exception:          # noqa: BLE001
                pass
        if b.get('base_qty'):
            q /= float(b['base_qty']) or 1
        q *= 1 + float(b.get('scrap_pct') or 0) / 100
        res[b['product']].append((b['component'], q))
    _ = bt
    return dict(res)


# ───────────────── 存货计价（移动平均） ─────────────────
def _std(c, material) -> float | None:
    r = db.get(c, 'std_cost', material=int(material), current=True)
    return float(r['total']) if r and r.get('total') else None


def _ref_price(c, t) -> tuple[float | None, str]:
    reason = (t.get('reason') or '').replace('[已过账] ', '')
    ref = t.get('ref_doc') or ''
    if _i18n.matches(reason, '采购入库') and ref:
        rc = db.get(c, 'purchase_receipt', code=ref)
        if rc:
            rl = db.rows(c, 'receipt_line', TABLES['receipt_line'].c.purchase_receipt == rc['id'], TABLES['receipt_line'].c.material == t['material'], limit=1)
            if rl:
                return float(rl[0]['price'] or 0), _tr('采购 {ref}', ref=ref)
    if (_i18n.matches(reason, '退货入库') or _i18n.matches(reason, '销售退货入库')) and ref:
        r = db.get(c, 'sales_return', code=ref)
        if r:
            rl = db.rows(c, 'return_line', TABLES['return_line'].c.sales_return == r['id'], TABLES['return_line'].c.material == t['material'], limit=1)
            if rl and rl[0].get('unit_cost'):
                return float(rl[0]['unit_cost']), _tr('原发货成本 {ref}', ref=ref)
    return None, ''


def valuate(c, limit=50000) -> int:
    """把还没计价的出入库记录按 id 顺序计价（幂等：计价流水 txn 唯一）。返回处理条数。"""
    compat.advisory_lock(c, 'erp:valuate')
    lt, st, ic = TABLES['cost_ledger'], TABLES['stock_txn'], TABLES['inv_cost']
    last = c.execute(sa.select(sa.func.max(lt.c.txn))).scalar() or 0
    rows = [dict(r) for r in c.execute(sa.select(st).where(st.c.id > last).order_by(st.c.id).limit(limit)).mappings()]
    if not rows:
        return 0
    state = {r['material']: dict(r) for r in c.execute(sa.select(ic)).mappings()}
    mats = {m['id']: m for m in db.rows(c, 'material')}
    method = core.setting(c, 'erp_cost_method', 'moving_avg')
    ledger = []
    touched = set()
    for t in rows:
        m = t['material']
        s = state.setdefault(m, {'material': m, 'qty': 0.0, 'avg_cost': None, 'value': 0.0, 'id': None})
        q = float(t['qty'] or 0)
        tt = t['ttype']
        sign = {'in': 1, 'out': -1, 'adjust': 1, 'transfer': 0}.get(tt, 0)
        dq = q * sign
        avg = s['avg_cost'] if s['avg_cost'] is not None else (_std(c, m) or float(mats.get(m, {}).get('price') or 0))
        basis = _tr('移动平均')
        if method == 'standard':
            uc = _std(c, m) or avg
            basis = _tr('标准成本')
        elif dq > 0 and tt == 'in':
            uc, basis = _ref_price(c, t)
            if uc is None:
                std = _std(c, m)
                uc, basis = (std, _tr('标准成本')) if std else (float(mats.get(m, {}).get('price') or 0), _tr('物料单价'))
        else:
            uc = avg
        val = dq * uc
        if dq > 0:
            s['value'] = (s['value'] or 0) + val
            s['qty'] = (s['qty'] or 0) + dq
            s['avg_cost'] = s['value'] / s['qty'] if s['qty'] > 1e-9 else uc
        elif dq < 0:
            s['qty'] = (s['qty'] or 0) + dq
            s['value'] = (s['value'] or 0) + val
            s['avg_cost'] = avg
            if abs(s['qty']) < 1e-9:
                s['value'] = 0.0
        ledger.append({'txn': t['id'], 'material': m, 'ttype': tt, 'qty': dq, 'unit_cost': round(uc, 6), 'value': round(val, 4), 'qty_after': round(s['qty'], 6),
                       'avg_after': round(s['avg_cost'] or 0, 6), 'ref_doc': t.get('ref_doc'), 'period': core.period_of(t.get('time') or t.get('created_at')),
                       'time': t.get('time') or t.get('created_at'), 'basis': basis, 'created_at': clock.now(), 'updated_at': clock.now()})
        touched.add(m)
    compat.insert_ignore(c, lt, ledger, keys=['txn'])
    now = clock.now()
    for m in touched:
        s = state[m]
        vals = {'qty': round(s['qty'], 6), 'avg_cost': round(s['avg_cost'] or 0, 6), 'value': round(s['value'], 4), 'last_txn': rows[-1]['id'], 'updated': now,
                'updated_at': now}
        if s.get('id'):
            c.execute(ic.update().where(ic.c.id == s['id']).values(**vals))
        else:
            s['id'] = c.execute(ic.insert().values(material=m, created_at=now, **vals)).inserted_primary_key[0]
    return len(rows)


def avg_cost(c, material) -> float:
    r = db.get(c, 'inv_cost', material=int(material))
    if r and r.get('avg_cost'):
        return float(r['avg_cost'])
    return _std(c, material) or float((db.get(c, 'material', int(material)) or {}).get('price') or 0)


def valuation(c, as_of=None) -> list[dict]:
    """存货结存（到 as_of 那天为止，用计价流水算；不给 = 当前）。"""
    valuate(c)
    lt = TABLES['cost_ledger']
    q = sa.select(lt.c.material, sa.func.sum(lt.c.qty), sa.func.sum(lt.c.value)).group_by(lt.c.material)
    if as_of:
        q = q.where(lt.c.time < datetime.combine(as_of, datetime.min.time()))
    mats = {m['id']: m for m in db.rows(c, 'material')}
    res = []
    for m, qq, v in c.execute(q):
        mm = mats.get(m, {})
        qq, v = float(qq or 0), float(v or 0)
        if abs(qq) < 1e-9 and abs(v) < 0.005:
            continue
        res.append({'material': m, 'code': mm.get('code'), 'name': mm.get('name'), 'mtype': mm.get('mtype'), 'unit': mm.get('unit'), 'qty': round(qq, 4),
                    'value': money(v), 'avg_cost': round(v / qq, 4) if abs(qq) > 1e-9 else None})
    return sorted(res, key=lambda x: -x['value'])


# ───────────────── 费率 ─────────────────
def rates(c, on=None):
    on = as_date(on) or today()
    rs = [r for r in db.rows(c, 'cost_rate') if not r.get('valid_from') or as_date(r['valid_from']) <= on]
    return sorted(rs, key=lambda r: (as_date(r.get('valid_from')) or datetime.min.date()), reverse=True)


def rate_for(rs, kind, process=None, group=None, equipment=None):
    """最具体的费率：设备 > 工位组 > 工序 > 通用。"""
    best, score = None, -1
    for r in rs:
        if r['kind'] != kind:
            continue
        if r.get('equipment') and r['equipment'] != equipment:
            continue
        if r.get('station_group') and r['station_group'] != group:
            continue
        if r.get('process') and r['process'] != process:
            continue
        sc = (4 if r.get('equipment') else 0) + (2 if r.get('station_group') else 0) + (1 if r.get('process') else 0)
        if sc > score:
            best, score = r, sc
    return best


def overhead(rs, lab_h, mach_h, direct, qty):
    tot, parts = 0.0, []
    for r in rs:
        if r['kind'] != 'overhead':
            continue
        b, rate = r['basis'], float(r['rate'] or 0)
        v = lab_h * rate if b == 'labour_hour' else mach_h * rate if b == 'machine_hour' else direct * rate / 100 if b == 'direct_pct' else qty * rate if b == 'unit' else 0
        tot += v
        parts.append({'rule': r['code'], 'basis': b, 'rate': rate, 'value': money(v)})
    return tot, parts


# ───────────────── 标准成本卷积 ─────────────────
def roll_up(c, user=None, materials=None):
    """标准成本 = 子件标准成本 × 用量 之和 + 工艺路线每步（节拍 × 人工 / 机器费率）+ 能耗（额定功率 × 0.6 × 机器工时 × 电价）+ 制造费用分摊。
    外购件 = 首选供方价 → 物料单价。逐层从底往上算，写 std_cost（旧的标为非当前）。"""
    from .mrp import low_level_codes
    boms = bom_map(c)
    llc = low_level_codes(boms)
    mats = {m['id']: m for m in db.rows(c, 'material')}
    rs = rates(c)
    procs = {p['id']: p for p in db.rows(c, 'process')}
    eqs = db.rows(c, 'equipment')
    kw_by_group = defaultdict(list)
    for ws in db.rows(c, 'workstation'):
        eq = next((e for e in eqs if e['id'] == ws.get('equipment')), None)
        if eq and eq.get('rated_kw'):
            kw_by_group[ws.get('group') or ws['code']].append(float(eq['rated_kw']))
    erate = rate_for(rs, 'energy')
    eprice = float(erate['rate']) if erate else 0.0
    res = {}
    order = sorted(mats, key=lambda m: -llc.get(m, 0))
    for m in order:
        mm = mats[m]
        kids = boms.get(m, [])
        detail = []
        mat = lab = mach = eng = 0.0
        lab_h = mach_h = 0.0
        if kids:
            for comp, q in kids:
                cc = res.get(comp, {}).get('total')
                if cc is None:
                    cc = _purchase_std(c, comp, mats)
                mat += cc * q
                detail.append({'kind': 'mat', 'component': mats.get(comp, {}).get('code'), 'qty': q, 'unit_cost': round(cc, 4), 'value': round(cc * q, 4)})
            route = db.get(c, 'route', product=m, status='active')
            steps = db.rows(c, 'route_step', TABLES['route_step'].c.route == route['id'], order=TABLES['route_step'].c.seq) if route else []
            for s in steps:
                h = float(s['std_cycle_s'] or (procs.get(s['process'], {}).get('std_cycle_s') or 0)) / 3600
                g = s.get('station_group')
                lr = rate_for(rs, 'labour', s['process'], g)
                mr = rate_for(rs, 'machine', s['process'], g)
                lv = h * float(lr['rate']) if lr else 0
                mv = h * float(mr['rate']) if mr else 0
                kw = (sum(kw_by_group.get(g, [])) / len(kw_by_group[g])) if kw_by_group.get(g) else 0
                ev = h * kw * 0.6 * eprice
                lab, mach, eng = lab + lv, mach + mv, eng + ev
                lab_h, mach_h = lab_h + h, mach_h + h
                detail.append({'kind': 'step', 'seq': s['seq'], 'process': procs.get(s['process'], {}).get('name'), 'group': g, 'hours': round(h, 5),
                               'lab': round(lv, 4), 'mach': round(mv, 4), 'energy': round(ev, 4)})
            ovh, parts = overhead(rs, lab_h, mach_h, mat + lab + mach, 1)
            detail += [{'kind': 'ovh', **p} for p in parts]
            by = 'roll'
        else:
            mat = _purchase_std(c, m, mats)
            ovh = 0.0
            by = 'purchase'
        tot = mat + lab + mach + eng + ovh
        res[m] = {'material': m, 'mat': round(mat, 4), 'lab': round(lab, 4), 'mach': round(mach, 4), 'energy': round(eng, 4), 'ovh': round(ovh, 4),
                  'total': round(tot, 4), 'level': llc.get(m, 0), 'detail': detail, 'by': by}
    keep = set(int(x) for x in materials) if materials else None
    sc = TABLES['std_cost']
    n = 0
    for m, r in res.items():
        if keep is not None and m not in keep:
            continue
        cur = db.get(c, 'std_cost', material=m, current=True)
        if cur and abs((cur['total'] or 0) - r['total']) < 1e-6:
            continue
        c.execute(sc.update().where((sc.c.material == m) & (sc.c.current.is_(True))).values(current=False))
        db.insert(c, 'std_cost', {**r, 'as_of': today(), 'current': True}, raw=True)
        n += 1
    return {'changed': n, 'items': len(res)}


def _purchase_std(c, m, mats):
    b = core.best_supplier(c, m)
    if b and b.get('price'):
        return float(b['price'])
    return float(mats.get(m, {}).get('price') or 0)


# ───────────────── 工单实际成本 ─────────────────
def wo_actual(c, wo, rs=None) -> dict:
    rs = rs if rs is not None else rates(c, wo.get('completed_at') or clock.now())
    mats = {m['id']: m for m in db.rows(c, 'material')}
    done = float(wo['qty_done'] or 0)
    detail = {'mat': [], 'lab': [], 'mach': [], 'energy': [], 'ovh': []}
    # 料：领料（− 退料）× 计价流水里那笔出库的单价（没有出库记录的按当时移动平均）；一笔领料都没有 → 按 BOM 倒冲（完工数 × 用量 × 移动平均）
    mi, lt, st = TABLES['material_issue'], TABLES['cost_ledger'], TABLES['stock_txn']
    issues = db.rows(c, 'material_issue', mi.c.work_order == wo['id'])
    mat = 0.0
    if issues:
        led = {r.material: r.uc for r in c.execute(sa.select(lt.c.material, (sa.func.sum(lt.c.value) / sa.func.nullif(sa.func.sum(lt.c.qty), 0)).label('uc')).join(
            st, st.c.id == lt.c.txn).where((st.c.ref_doc == wo['code']) & (lt.c.qty < 0)).group_by(lt.c.material))}
        for x in issues:
            uc = float(led.get(x['material']) or avg_cost(c, x['material']))
            q = float(x['qty'] or 0) * (-1 if x['kind'] == 'return' else 1)
            mat += q * uc
            detail['mat'].append({'material': mats.get(x['material'], {}).get('code'), 'qty': q, 'unit_cost': round(uc, 4), 'value': money(q * uc), 'doc': x['code']})
    else:
        for comp, q in bom_map(c).get(wo['product'], []):
            uc = avg_cost(c, comp)
            v = done * q * uc
            mat += v
            detail['mat'].append({'material': mats.get(comp, {}).get('code'), 'qty': round(done * q, 4), 'unit_cost': round(uc, 4), 'value': money(v), 'doc': _tr('BOM 倒冲')})
    # 工 / 机：报工记录（有开始结束的按实际时长，否则按 数量 × 标准节拍）
    rt, opt = TABLES['report'], TABLES['wo_op']
    ops = {o['id']: o for o in db.rows(c, 'wo_op', opt.c.work_order == wo['id'])}
    route_ct = {}
    if wo.get('route'):
        route_ct = {s['seq']: s['std_cycle_s'] for s in db.rows(c, 'route_step', TABLES['route_step'].c.route == wo['route'])}
    procs = {p['id']: p for p in db.rows(c, 'process')}
    lab = mach = lab_h = mach_h = 0.0
    reps = db.rows(c, 'report', rt.c.work_order == wo['id'])
    eq_used = defaultdict(list)
    for r in reps:
        op = ops.get(r['wo_op']) or {}
        ct = route_ct.get(op.get('seq')) or procs.get(r['process'], {}).get('std_cycle_s') or 0
        n = float((r['qty_good'] or 0) + (r['qty_ng'] or 0))
        if r.get('start') and r.get('end') and r['end'] > r['start']:
            h = (r['end'] - r['start']).total_seconds() / 3600
            src = _tr('实际')
        else:
            h = n * float(ct) / 3600
            src = _tr('标准节拍')
        g = op.get('station_group')
        lr = rate_for(rs, 'labour', r['process'], g, r.get('equipment'))
        lv = h * float(lr['rate']) if lr else 0
        lab, lab_h = lab + lv, lab_h + h
        detail['lab'].append({'report': r['code'], 'hours': round(h, 4), 'rate': lr and lr['rate'], 'value': money(lv), 'by': src})
        if r.get('equipment') or g:
            mr = rate_for(rs, 'machine', r['process'], g, r.get('equipment'))
            mv = h * float(mr['rate']) if mr else 0
            mach, mach_h = mach + mv, mach_h + h
            detail['mach'].append({'report': r['code'], 'equipment': r.get('equipment'), 'hours': round(h, 4), 'rate': mr and mr['rate'], 'value': money(mv)})
            if r.get('equipment') and r.get('start') and r.get('end'):
                eq_used[r['equipment']].append((r['start'], r['end']))
    if not reps and done:
        for o in ops.values():
            n = float((o['qty_good'] or 0) + (o['qty_ng'] or 0))
            h = n * float(route_ct.get(o['seq']) or procs.get(o['process'], {}).get('std_cycle_s') or 0) / 3600
            lr, mr = rate_for(rs, 'labour', o['process'], o['station_group']), rate_for(rs, 'machine', o['process'], o['station_group'])
            lab += h * float(lr['rate']) if lr else 0
            mach += h * float(mr['rate']) if mr else 0
            lab_h, mach_h = lab_h + h, mach_h + h
            detail['lab'].append({'op': o['seq'], 'hours': round(h, 4), 'value': money(h * float(lr['rate']) if lr else 0), 'by': _tr('工序完工数 × 标准节拍')})
            if o.get('act_start') and o.get('act_end') and o.get('machines'):
                for code in str(o['machines']).split(','):
                    e = db.get(c, 'equipment', code=code.strip().replace('ws:', ''))
                    if e:
                        eq_used[e['id']].append((o['act_start'], o['act_end']))
    # 能：设备在本工单报工时段内的小时电量，同一设备同一小时有几张工单在做就平分
    eng_kwh = 0.0
    eh = TABLES['energy_hour']
    for eid, spans in eq_used.items():
        for a, b in spans:
            h0 = a.replace(minute=0, second=0, microsecond=0)
            for x in c.execute(sa.select(eh.c.hour, eh.c.kwh).where((eh.c.equipment == eid) & (eh.c.hour >= h0) & (eh.c.hour < b))):
                share = _share(c, eid, x.hour)
                frac = _overlap(a, b, x.hour, x.hour + timedelta(hours=1))
                eng_kwh += float(x.kwh or 0) * frac / max(1, share)
    er = rate_for(rs, 'energy')
    eng = eng_kwh * (float(er['rate']) if er else 0)
    if eng_kwh:
        detail['energy'].append({'kwh': round(eng_kwh, 3), 'price': er and er['rate'], 'value': money(eng)})
    ovh, parts = overhead(rs, lab_h, mach_h, mat + lab + mach, done)
    detail['ovh'] = parts
    tot = mat + lab + mach + eng + ovh
    return {'qty_done': done, 'mat': money(mat), 'lab': money(lab), 'mach': money(mach), 'energy': money(eng), 'ovh': money(ovh), 'total': money(tot),
            'unit_cost': round(tot / done, 4) if done else None, 'lab_h': round(lab_h, 3), 'mach_h': round(mach_h, 3), 'kwh': round(eng_kwh, 3), 'detail': detail}


_share_cache: dict = {}


def _share(c, eid, hour):
    k = (eid, hour)
    if k not in _share_cache:
        rt = TABLES['report']
        n = c.execute(sa.select(sa.func.count(sa.distinct(rt.c.work_order))).where((rt.c.equipment == eid) & (rt.c.start < hour + timedelta(hours=1))
                                                                                    & (rt.c.end > hour))).scalar()
        if len(_share_cache) > 20000:
            _share_cache.clear()
        _share_cache[k] = n or 1
    return _share_cache[k]


def _overlap(a, b, x, y):
    s, e = max(a, x), min(b, y)
    return max(0.0, (e - s).total_seconds() / 3600)


def calc_wo(c, wo, force=False):
    """算一张工单的实际成本、标准成本、差异，写 wo_cost。已结账（frozen）的不重算。"""
    cur = db.get(c, 'wo_cost', work_order=wo['id'])
    if cur and cur['status'] == 'frozen' and not force:
        return cur
    a = wo_actual(c, wo)
    std = db.get(c, 'std_cost', material=wo['product'], current=True) or {}
    done = a['qty_done']
    su = float(std.get('total') or 0)
    var = {}
    for k in ('mat', 'lab', 'mach', 'energy', 'ovh'):
        sv = float(std.get(k) or 0) * done
        var[k] = {'std': money(sv), 'actual': a[k], 'diff': money(a[k] - sv), 'pct': round((a[k] - sv) / sv, 4) if sv else None}
    period = core.period_of(wo.get('completed_at') or clock.now())
    row = {'work_order': wo['id'], 'product': wo['product'], 'period': period, 'qty_done': done, **{k: a[k] for k in ('mat', 'lab', 'mach', 'energy', 'ovh', 'total')},
           'unit_cost': a['unit_cost'], 'std_unit': round(su, 4), 'std_total': money(su * done), 'variance': money(a['total'] - su * done), 'var_detail': var,
           'detail': {**a['detail'], 'lab_h': a['lab_h'], 'mach_h': a['mach_h'], 'kwh': a['kwh']}, 'status': 'open', 'calc_at': clock.now()}
    if cur:
        db.update(c, 'wo_cost', cur['id'], row, raw=True)
        return db.get(c, 'wo_cost', cur['id'])
    rid = db.insert(c, 'wo_cost', row, raw=True)
    return db.get(c, 'wo_cost', rid)


def calc_all(c, period=None, ids=None):
    valuate(c)
    wt = TABLES['work_order']
    conds = [wt.c.status.in_(('in_progress', 'completed', 'closed'))]
    if ids:
        conds = [wt.c.id.in_([int(x) for x in ids])]
    n = 0
    for wo in db.rows(c, 'work_order', *conds):
        if period and core.period_of(wo.get('completed_at') or clock.now()) != period and wo['status'] != 'in_progress':
            continue
        calc_wo(c, wo)
        n += 1
    return n


def variance(c, group='product', period=None):
    """差异汇总：按产品 / 期间 / 工单。"""
    t = TABLES['wo_cost']
    rows = db.rows(c, 'wo_cost', *([t.c.period == period] if period else []))
    mats = {m['id']: m for m in db.rows(c, 'material')}
    agg = defaultdict(lambda: {'qty': 0.0, 'actual': 0.0, 'std': 0.0, 'mat': 0.0, 'lab': 0.0, 'mach': 0.0, 'energy': 0.0, 'ovh': 0.0, 'orders': 0})
    for r in rows:
        k = r['period'] if group == 'period' else r['work_order'] if group == 'work_order' else r['product']
        a = agg[k]
        a['qty'] += r['qty_done'] or 0
        a['actual'] += r['total'] or 0
        a['std'] += r['std_total'] or 0
        a['orders'] += 1
        for kk in ('mat', 'lab', 'mach', 'energy', 'ovh'):
            a[kk] += ((r['var_detail'] or {}).get(kk) or {}).get('diff') or 0
    res = []
    for k, a in agg.items():
        lab = (mats.get(k, {}).get('code', '') + ' ' + mats.get(k, {}).get('name', '')).strip() if group == 'product' else \
            core.code_of(c, 'work_order', k) if group == 'work_order' else k
        res.append({'key': k, 'label': lab, **{kk: (money(v) if isinstance(v, float) else v) for kk, v in a.items()},
                    'variance': money(a['actual'] - a['std']), 'pct': round((a['actual'] - a['std']) / a['std'], 4) if a['std'] else None})
    return sorted(res, key=lambda x: -abs(x['variance']))


# ───────────────── 月末结账 ─────────────────
@action('fin_period', 'close')
def period_close(c, p, body, user):
    """结账：先补计价、算本期完工工单成本 → 冻结；存货结存快照；期间标为已结账（之后本期日期的发货 / 收货 / 退货 / 发票 / 收付款都不能再过账）。
    要按顺序结：前面还有没结的期间时不让结。"""
    if p['status'] == 'closed':
        raise HTTPException(409, _tr('{code} 已经结账', code=p['code']))
    ft = TABLES['fin_period']
    prev = db.rows(c, 'fin_period', ft.c.code < p['code'], ft.c.status == 'open', order=ft.c.code, limit=1)
    if prev:
        raise HTTPException(409, _tr('请先结 {code}（期间要按顺序结账）', code=prev[0]['code']))
    s, e = core.period_bounds(p['code'])
    valuate(c)
    wt = TABLES['work_order']
    frozen = 0
    for wo in db.rows(c, 'work_order', wt.c.status.in_(('completed', 'closed'))):
        if core.period_of(wo.get('completed_at') or clock.now()) != p['code']:
            continue
        r = calc_wo(c, wo)
        db.update(c, 'wo_cost', r['id'], {'status': 'frozen'}, raw=True)
        frozen += 1
    val = valuation(c, e)
    snap = {'inventory_value': money(sum(x['value'] for x in val)), 'items': len(val), 'wo_frozen': frozen,
            'by_type': {k: money(sum(x['value'] for x in val if x['mtype'] == k)) for k in {x['mtype'] for x in val}}, 'closed_at': db.jsonable(clock.now())}
    db.update(c, 'fin_period', p['id'], {'status': 'closed', 'closed_by': who(user), 'closed_at': clock.now(), 'snapshot': snap}, raw=True)
    audit.audit(c, 'period_close', 'fin_period', p['id'], detail=snap)
    return {'msg': _tr('{code} 已结账：冻结 {frozen} 张工单成本，存货 {inventory_value:,.2f} 元', code=p['code'], frozen=frozen, inventory_value=snap['inventory_value']), 'snapshot': snap}


@action('fin_period', 'reopen')
def period_reopen(c, p, body, user):
    if p['status'] != 'closed':
        raise HTTPException(409, _tr('这个期间没有结账'))
    ft = TABLES['fin_period']
    later = db.rows(c, 'fin_period', ft.c.code > p['code'], ft.c.status == 'closed', limit=1)
    if later:
        raise HTTPException(409, _tr('后面的 {code} 已结账，要先反结它', code=later[0]['code']))
    reason = (body.get('reason') or '').strip()
    if not reason:
        raise HTTPException(400, _tr('请写反结账原因'))
    wc = TABLES['wo_cost']
    c.execute(wc.update().where((wc.c.period == p['code']) & (wc.c.status == 'frozen')).values(status='open'))
    db.update(c, 'fin_period', p['id'], {'status': 'open'}, raw=True)
    audit.audit(c, 'period_reopen', 'fin_period', p['id'], detail={'reason': reason})
    return {'msg': _tr('{code} 已反结账', code=p['code'])}


# ───────────────── 接口 ─────────────────
@router.post('/api/cost/valuate')
def valuate_api(user=Depends(require(P_CALC))):
    with db.engine.begin() as c:
        n = valuate(c)
    return {'ok': True, 'processed': n}


@router.get('/api/cost/valuation')
def valuation_api(as_of: str = '', user=Depends(require('cost.inv_cost.read'))):
    with db.engine.begin() as c:
        rows = valuation(c, as_date(as_of) if as_of else None)
    return {'items': rows, 'total': money(sum(x['value'] for x in rows))}


@router.post('/api/cost/roll_up')
def roll_up_api(body: dict = Body(default={}), user=Depends(require(P_CALC))):
    with db.engine.begin() as c:
        return roll_up(c, user, body.get('materials'))


@router.post('/api/cost/work_orders')
def calc_api(body: dict = Body(default={}), user=Depends(require(P_CALC))):
    with db.engine.begin() as c:
        n = calc_all(c, body.get('period'), body.get('ids'))
    return {'ok': True, 'count': n}


@router.get('/api/cost/work_orders')
def wo_costs(period: str = '', user=Depends(require('cost.wo_cost.read'))):
    t = TABLES['wo_cost']
    with db.engine.connect() as c:
        rows = attach_labels(c, 'wo_cost', db.rows(c, 'wo_cost', *([t.c.period == period] if period else []), order=t.c.id.desc(), limit=500))
    return out(rows)


@router.get('/api/cost/variance')
def variance_api(group: str = 'product', period: str = '', user=Depends(require('cost.wo_cost.read'))):
    with db.engine.connect() as c:
        return variance(c, group if group in ('product', 'period', 'work_order') else 'product', period or None)


@router.get('/api/cost/periods')
def periods_api(user=Depends(require('cost.fin_period.read'))):
    with db.engine.begin() as c:
        cur = core.period_of(today())
        core.ensure_period(c, cur)
        ft = TABLES['fin_period']
        return out(db.rows(c, 'fin_period', order=ft.c.code.desc(), limit=36))
