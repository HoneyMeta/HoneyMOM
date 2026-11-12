"""包装发运：包装工位扫码装箱（零件码 / 批次）→ 满箱自动封箱打箱标签 → 扫箱号组托 → 满托打托盘标签 + 客户标签 →
装车单扫托盘 / 箱 → 发运确认（零件置已发运、成品出库、发运记录、通知内置 ERP 的发货单）→ ASN（JSON / Excel / 平面文件 / EDIFACT DESADV 样例）。

追溯：发运（装车单 / ASN / 发货单）→ 托盘 → 箱 → 零件码，反过来零件 → 箱 → 托 → 装车单（views.trace_data 里的 packing）。
包装规则 pack_rule：客户 + 产品 → 每箱数量、每托箱数、是否允许混批、箱 / 托 / 客户标签模板、客户零件号。
发货单（M4 内置 ERP）：按发货单号关联（ship_load.delivery_code）；内置 ERP 在时 erp_link() 取发货单明细、发运后回写。
"""
from ..i18n import _tr
import io
import json

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import Response

from .. import db, perms
from ..crud import action, attach_labels, out
from ..db import clock
from ..schema import TABLES
from .gates import Gate, check_lot_out

router = APIRouter()
PERM_STATION = perms.declare('pack.station', '包装工位扫码装箱 / 组托', '包装发运')
PACKABLE = ('good', 'fg')


def rule_for(c, material, customer=None):
    rt = TABLES['pack_rule']
    if customer:
        r = db.rows(c, 'pack_rule', rt.c.customer == customer, material=material, limit=1)
        if r:
            return r[0]
    r = db.rows(c, 'pack_rule', sa.or_(rt.c.customer.is_(None), rt.c.customer == ''), material=material, limit=1)
    return r[0] if r else None


def _packed_in(c, sn):
    it, pk = TABLES['package_item'], TABLES['package']
    r = c.execute(sa.select(pk.c.id, pk.c.code, pk.c.status).select_from(it.join(pk, pk.c.id == it.c.package)).where(it.c.sn == sn).limit(1)).first()
    return r


def _open(c, kind, station, material, customer):
    pk = TABLES['package']
    q = [pk.c.kind == kind, pk.c.status == 'open', pk.c.station == station]
    if material:
        q.append(pk.c.material == material)
    if customer:
        q.append(pk.c.customer == customer)
    r = db.rows(c, 'package', *q, order=pk.c.id.desc(), limit=1)
    return r[0] if r else None


def _new_package(c, kind, station, material, customer, rule, who):
    code = db.next_code(c, 'package', ('CT' if kind == 'carton' else 'PL') + '{d}{n:04}')
    cap = (rule or {}).get('carton_qty' if kind == 'carton' else 'pallet_cartons') or (20 if kind == 'carton' else 24)
    pid = db.insert(c, 'package', {'code': code, 'kind': kind, 'material': material, 'customer': customer, 'customer_part_no': (rule or {}).get('customer_part_no'),
                                   'qty': 0, 'capacity': cap, 'status': 'open', 'rule': (rule or {}).get('id'), 'station': station, 'packed_by': who}, raw=True)
    return db.get(c, 'package', pid)


def _lots_add(lots, lot):
    xs = [x for x in (lots or '').split(',') if x]
    if lot and lot not in xs:
        xs.append(lot)
    return ','.join(xs)[:190]


def close_package(c, p, user, auto=False):
    """封箱 / 封托：打标签（自动打印规则 carton_close / pallet_close；托盘另打客户标签）。"""
    if p['status'] != 'open':
        return []
    if not p['qty']:
        raise HTTPException(400, _tr('{code} 是空的，不能封', code=p['code']))
    db.update(c, 'package', p['id'], {'status': 'closed', 'closed_at': clock.now()}, raw=True)
    from .exec_label import fire
    ev = 'carton_close' if p['kind'] == 'carton' else 'pallet_close'
    return fire(c, ev, p['kind'], p['code'], user, customer=p['customer'], material=p['material'])


def pack_part(c, code, station, customer, user, qty=None):
    """扫一个码装箱：零件码（一件一码）或批次号（qty 件）。返回 {carton, closed, jobs, msg}。"""
    code = (code or '').strip()
    who = user.get('name') or user['username']
    part = db.get(c, 'part', sn=code)
    if part:
        check_lot_out(c, sn=code, what=_tr('装箱'))
        if part['status'] not in PACKABLE:
            raise Gate('pack', _tr('零件 {code} 当前是“{status}”，检验合格（或入成品库）的才能装箱', code=code, status=part['status']))
        ex = _packed_in(c, code)
        if ex:
            raise Gate('pack', _tr('零件 {code} 已经装在 {v} 里了，不能重复装箱', code=code, v=ex[1]))
        material, lot, n = part['product'], part['heat'], 1
    else:
        # 批次装箱（非一件一码的产品）：code = 批次号，qty 件
        if not qty:
            raise HTTPException(404, _tr('没有零件 {code}；按批次装箱请同时填数量', code=code))
        check_lot_out(c, lot=code, what=_tr('装箱'))
        st = TABLES['stock']
        s = db.rows(c, 'stock', st.c.lot == code, st.c.qty > 0, limit=1)
        if not s:
            raise HTTPException(404, _tr('批次 {code} 没有库存', code=code))
        material, lot, n = s[0]['material'], code, int(qty)
    rule = rule_for(c, material, customer)
    carton = _open(c, 'carton', station, material, customer) or _new_package(c, 'carton', station, material, customer, rule, who)
    if not (rule or {}).get('mixed_lot', False) and carton['lots'] and lot and lot not in carton['lots'].split(','):
        raise Gate('pack', _tr('{code} 里是批次 {lots}，这件是 {lot}：这个产品不允许混批，先封箱再装', code=carton['code'], lots=carton['lots'], lot=lot))
    if (carton['qty'] or 0) + n > (carton['capacity'] or 999999):
        raise Gate('pack', _tr('{code} 还能装 {v} 件，本次 {n} 件', code=carton['code'], v=(carton['capacity'] or 0) - (carton['qty'] or 0), n=n))
    db.insert(c, 'package_item', {'package': carton['id'], 'sn': part['sn'] if part else None, 'lot': lot, 'qty': n, 'material': material, 'by': who,
                                  'time': clock.now()}, raw=True)
    db.update(c, 'package', carton['id'], {'qty': (carton['qty'] or 0) + n, 'lots': _lots_add(carton['lots'], lot)}, raw=True)
    if part:
        c.execute(TABLES['part_event'].insert().values(sn=part['sn'], t=clock.now(), station=station or _tr('包装'), event='packed', data={'carton': carton['code']},
                                                       created_at=clock.now(), updated_at=clock.now()))
    carton = db.get(c, 'package', carton['id'])
    jobs, closed = [], False
    if carton['qty'] >= (carton['capacity'] or 0):
        jobs = close_package(c, carton, user, auto=True)
        closed = True
    return {'carton': carton['code'], 'carton_id': carton['id'], 'qty': carton['qty'], 'capacity': carton['capacity'], 'closed': closed, 'jobs': jobs,
            'msg': _tr('{code} → {code2}（{qty}/{capacity}）', code=code, code2=carton['code'], qty=carton['qty'], capacity=carton['capacity']) + (_tr('，满箱已封箱打标签') if closed else '')}


def palletize(c, carton_code, station, customer, user):
    pk = TABLES['package']
    ct = db.get(c, 'package', code=carton_code)
    if not ct or ct['kind'] != 'carton':
        raise HTTPException(404, _tr('没有箱 {carton_code}', carton_code=carton_code))
    if ct['status'] == 'open':
        raise Gate('pack', _tr('{carton_code} 还没封箱', carton_code=carton_code))
    if ct['parent']:
        p = db.get(c, 'package', ct['parent'])
        raise Gate('pack', _tr('{carton_code} 已经在托盘 {code} 上', carton_code=carton_code, code=p['code']))
    if ct['status'] != 'closed':
        raise Gate('pack', _tr('{carton_code} 已{get}', carton_code=carton_code, get={'loaded': _tr('装车'), 'shipped': _tr('发运')}.get(ct['status'], ct['status'])))
    who = user.get('name') or user['username']
    rule = db.get(c, 'pack_rule', ct['rule']) if ct['rule'] else rule_for(c, ct['material'], ct['customer'])
    pal = _open(c, 'pallet', station, ct['material'], ct['customer'] or customer) or _new_package(c, 'pallet', station, ct['material'], ct['customer'] or customer, rule, who)
    n = c.execute(sa.select(sa.func.count()).select_from(pk).where(pk.c.parent == pal['id'])).scalar()
    db.update(c, 'package', ct['id'], {'parent': pal['id']}, raw=True)
    db.update(c, 'package', pal['id'], {'qty': (pal['qty'] or 0) + (ct['qty'] or 0), 'lots': _lots_add(_lots_add(pal['lots'], ''), ct['lots'])}, raw=True)
    pal = db.get(c, 'package', pal['id'])
    closed, jobs = False, []
    if n + 1 >= (pal['capacity'] or 0):
        jobs = close_package(c, pal, user, auto=True)
        closed = True
    return {'pallet': pal['code'], 'pallet_id': pal['id'], 'cartons': n + 1, 'capacity': pal['capacity'], 'qty': pal['qty'], 'closed': closed, 'jobs': jobs,
            'msg': _tr('{carton_code} → 托盘 {code}（{v}/{capacity} 箱）', carton_code=carton_code, code=pal['code'], v=n + 1, capacity=pal['capacity']) + (_tr('，满托已封托打标签') if closed else '')}


def station_state(c, station):
    pk = TABLES['package']
    cart = db.rows(c, 'package', pk.c.kind == 'carton', pk.c.status == 'open', station=station, order=pk.c.id.desc(), limit=1)
    pal = db.rows(c, 'package', pk.c.kind == 'pallet', pk.c.status == 'open', station=station, order=pk.c.id.desc(), limit=1)
    res = {'station': station, 'carton': None, 'pallet': None}
    if cart:
        cc = attach_labels(c, 'package', cart)[0]
        res['carton'] = {**out([cc])[0], 'items': out(db.rows(c, 'package_item', order=TABLES['package_item'].c.id.desc(), package=cc['id']))}
    if pal:
        pp = attach_labels(c, 'package', pal)[0]
        res['pallet'] = {**out([pp])[0], 'cartons': out(db.rows(c, 'package', order=pk.c.id, parent=pp['id']))}
    res['recent'] = out(attach_labels(c, 'package', db.rows(c, 'package', pk.c.status != 'open', station=station, order=pk.c.id.desc(), limit=12)))
    return res


@router.get('/api/pack/station')
def api_station(station: str = 'PACK1', user=Depends(perms.require('pack.package.read'))):
    with db.engine.connect() as c:
        return station_state(c, station)


@router.post('/api/pack/scan')
def api_scan(body: dict = Body(...), user=Depends(perms.require(PERM_STATION))):
    """包装工位扫码：零件码 → 装箱；箱号 → 组托；批次号 + qty → 按批次装箱。{code, station, customer?, qty?}"""
    code = (body.get('code') or '').strip()
    if not code:
        raise HTTPException(400, _tr('请扫码'))
    station = (body.get('station') or 'PACK1').strip()[:40]
    with db.engine.begin() as c:
        p = db.get(c, 'package', code=code)
        if p and p['kind'] == 'carton':
            res = palletize(c, code, station, body.get('customer'), user)
            res['action'] = 'pallet'
        elif p:
            raise Gate('pack', _tr('{code} 是托盘号：去装车单扫托盘', code=code))
        else:
            res = pack_part(c, code, station, body.get('customer'), user, body.get('qty'))
            res['action'] = 'pack'
        res['state'] = station_state(c, station)
    if res.get('jobs'):
        from .exec_label import _wake
        _wake.set()
    return {'ok': True, **res}


@router.post('/api/pack/close')
def api_close(body: dict = Body(...), user=Depends(perms.require(PERM_STATION))):
    """手工封箱 / 封托（不满也封）：{code}。"""
    with db.engine.begin() as c:
        p = db.get(c, 'package', code=(body.get('code') or '').strip())
        if not p:
            raise HTTPException(404, _tr('没有这个箱 / 托'))
        jobs = close_package(c, p, user)
        st = station_state(c, p['station']) if p['station'] else None
    from .exec_label import _wake
    _wake.set()
    return {'ok': True, 'jobs': jobs, 'msg': _tr('{code} 已封', code=p['code']) + (_tr('，标签已排队打印') if jobs else ''), 'state': st}


@router.post('/api/pack/unpack')
def api_unpack(body: dict = Body(...), user=Depends(perms.require(PERM_STATION))):
    """从未封的箱里拿出一件（扫错了）：{sn}。"""
    sn = (body.get('sn') or '').strip()
    with db.engine.begin() as c:
        it = db.get(c, 'package_item', sn=sn)
        if not it:
            raise HTTPException(404, _tr('{sn} 没有装箱', sn=sn))
        p = db.get(c, 'package', it['package'])
        if p['status'] != 'open':
            raise Gate('pack', _tr('{code} 已封箱，要拆箱请先找班组长', code=p['code']))
        db.delete(c, 'package_item', it['id'])
        db.update(c, 'package', p['id'], {'qty': max(0, (p['qty'] or 0) - (it['qty'] or 1))}, raw=True)
        st = station_state(c, p['station'])
    return {'ok': True, 'msg': _tr('{sn} 已从 {code} 取出', sn=sn, code=p['code']), 'state': st}


# ───────────────────────── 标签数据 ─────────────────────────
def package_data(c, code):
    p = db.get(c, 'package', code=str(code)) or (db.get(c, 'package', int(code)) if str(code).isdigit() else None)
    if not p:
        raise HTTPException(404, _tr('没有箱 / 托 {code}', code=code))
    m = db.get(c, 'material', p['material']) if p['material'] else {}
    m = m or {}
    pk = TABLES['package']
    cartons = c.execute(sa.select(sa.func.count()).select_from(pk).where(pk.c.parent == p['id'])).scalar() if p['kind'] == 'pallet' else 1
    load = db.get(c, 'ship_load', p['load']) if p['load'] else None
    net = round((m.get('weight_kg') or 0) * (p['qty'] or 0), 1)
    cust_no = p['customer_part_no'] or m.get('code', '')
    supplier_no = db.setting(c, f"supplier_no:{p['customer']}", None) or db.setting(c, 'supplier_no', '100234')
    digits = ''.join(ch for ch in p['code'] if ch.isdigit())
    lots = p['lots'] or ''
    return {'code': p['code'], 'kind': p['kind'], 'customer': p['customer'] or '', 'part_no': cust_no, 'part_name': m.get('name', ''), 'product_code': m.get('code', ''),
            'qty': p['qty'] or 0, 'unit': m.get('unit') or _tr('件'), 'lots': lots, 'cartons': cartons, 'date': (p['closed_at'] or clock.now()).strftime('%Y-%m-%d'),
            'packed_by': p['packed_by'] or '', 'delivery': (load or {}).get('delivery_code') or '', 'qr': f"{p['code']}|{cust_no}|{p['qty'] or 0}|{lots}",
            # 汽车行业运输标签（VDA 4902 版式参考）字段
            'receiver': p['customer'] or '', 'receiver_addr': (load or {}).get('ship_to') or '', 'dock': (load or {}).get('dock') or '',
            'delivery_no': ''.join(ch for ch in ((load or {}).get('delivery_code') or '') if ch.isalnum()) or digits, 'supplier_name': db.setting(c, 'company', ''),
            'net_kg': net, 'gross_kg': round(net * 1.07 + (8 if p['kind'] == 'carton' else 25), 1), 'packages': cartons, 'cust_part_no': ''.join(cust_no.split()),
            'description': m.get('name', ''), 'supplier_part_no': m.get('code', ''), 'ecl': _ecl(c, p['material']), 'supplier_no': supplier_no,
            'package_no': digits, 'batch_no': ''.join(ch for ch in (lots.split(',')[0] if lots else '') if ch.isalnum())}


def _ecl(c, material):
    """变更等级：产品图纸的现行受控版本。"""
    if not material:
        return ''
    t = TABLES['ctrl_doc']
    d = db.rows(c, 'ctrl_doc', t.c.status == 'effective', t.c.dtype == 'drawing', product=material, limit=1)
    return d[0]['cur_rev'] if d else ''


# ───────────────────────── 装车发运 ─────────────────────────
def erp_delivery(c, code):
    """内置 ERP 的发货单（M4）：{id, code, status, customer, lines:[{material, qty}]}；没有 → None（services/exec_erp.py）。"""
    from .exec_erp import delivery
    return delivery(c, code)


@router.post('/api/pack/load/from_delivery')
def load_from_delivery(body: dict = Body(...), user=Depends(perms.require('pack.ship_load.create'))):
    """按内置 ERP 的发货单建装车单：{code}（同一发货单已有未发运的装车单就直接用）。"""
    lt = TABLES['ship_load']
    with db.engine.begin() as c:
        d = erp_delivery(c, body.get('code'))
        if not d:
            raise HTTPException(404, _tr('没有发货单 {get}', get=body.get('code')))
        ex = db.rows(c, 'ship_load', lt.c.status.in_(('planned', 'loading')), delivery_code=d['code'], limit=1)
        if ex:
            return {'ok': True, 'id': ex[0]['id'], 'code': ex[0]['code'], 'msg': _tr('已有装车单 {code}', code=ex[0]['code'])}
        lid = db.insert(c, 'ship_load', {'delivery_code': d['code'], 'delivery_id': d['id'], 'customer': d['customer'] or d['customer_code'], 'ship_to': d['ship_to'],
                                         'status': 'planned', 'remark': _tr('发货单 {code}：{qty:g} 件', code=d['code'], qty=d['qty'])}, raw=True)
        ld = db.get(c, 'ship_load', lid)
    return {'ok': True, 'id': lid, 'code': ld['code'], 'msg': _tr('已按发货单 {code} 建装车单 {code2}', code=d['code'], code2=ld['code'])}


def _children(c, p):
    pk = TABLES['package']
    return db.rows(c, 'package', order=pk.c.id, parent=p['id']) if p['kind'] == 'pallet' else [p]


def _parts_of(c, cartons):
    it = TABLES['package_item']
    ids = [x['id'] for x in cartons]
    return db.rows(c, 'package_item', it.c.package.in_(ids), order=it.c.id) if ids else []


def refresh_load(c, lid):
    pk = TABLES['package']
    pkgs = db.rows(c, 'package', load=lid)
    pal = [p for p in pkgs if p['kind'] == 'pallet']
    ctn = [p for p in pkgs if p['kind'] == 'carton']
    db.update(c, 'ship_load', lid, {'pallets': len(pal), 'cartons': len(ctn), 'qty': sum(p['qty'] or 0 for p in ctn)}, raw=True)
    return pkgs, pk


@router.post('/api/pack/load/{lid}/scan')
def load_scan(lid: int, body: dict = Body(...), user=Depends(perms.require('pack.ship_load.update'))):
    """装车：扫托盘号（或单箱）。托盘连同上面的箱一起装上；检查已封、客户一致、没冻结、没装到别的车。"""
    code = (body.get('code') or '').strip()
    with db.engine.begin() as c:
        ld = db.get(c, 'ship_load', lid)
        if not ld or ld['status'] in ('shipped', 'cancelled'):
            raise HTTPException(400, _tr('装车单不存在或已发运'))
        p = db.get(c, 'package', code=code)
        if not p:
            raise HTTPException(404, _tr('没有托盘 / 箱 {code}', code=code))
        if p['kind'] == 'carton' and p['parent']:
            pal = db.get(c, 'package', p['parent'])
            raise Gate('load', _tr('{code} 在托盘 {code2} 上：请扫托盘号', code=code, code2=pal['code']))
        if p['status'] == 'open':
            raise Gate('load', _tr('{code} 还没封', code=code))
        if p['load'] and p['load'] != lid:
            other = db.get(c, 'ship_load', p['load'])
            raise Gate('load', _tr('{code} 已经装在 {code2} 上', code=code, code2=other['code']))
        if p['load'] == lid:
            return {'ok': True, 'msg': _tr('{code} 已经在这辆车上', code=code), 'load': out([db.get(c, 'ship_load', lid)])[0]}
        if ld['customer'] and p['customer'] and p['customer'] != ld['customer']:
            raise Gate('load', _tr('{code} 是给 {customer} 的，这辆车发 {customer2}', code=code, customer=p['customer'], customer2=ld['customer']))
        cartons = _children(c, p)
        for it in _parts_of(c, cartons):
            if it['sn']:
                check_lot_out(c, sn=it['sn'], what=_tr('发运'))
            elif it['lot']:
                check_lot_out(c, lot=it['lot'], what=_tr('发运'))
        dl = erp_delivery(c, ld['delivery_code']) if ld['delivery_code'] else None
        warn = None
        if dl:
            need = sum(x['qty'] for x in dl.get('lines') or [] if x.get('material') == p['material'])
            have = (ld['qty'] or 0) + (p['qty'] or 0)
            if need and have > need:
                warn = _tr('装车数量 {have} 超过发货单 {delivery_code} 的 {need}', have=have, delivery_code=ld['delivery_code'], need=need)
        for x in [p] + ([q for q in cartons if q['id'] != p['id']]):
            db.update(c, 'package', x['id'], {'load': lid, 'status': 'loaded'}, raw=True)
        refresh_load(c, lid)
        if ld['status'] == 'planned':
            db.update(c, 'ship_load', lid, {'status': 'loading'}, raw=True)
        ld = db.get(c, 'ship_load', lid)
    return {'ok': True, 'msg': _tr('{code} 已装车（{pallets} 托 / {cartons} 箱 / {qty} 件）', code=code, pallets=ld['pallets'], cartons=ld['cartons'], qty=ld['qty']), 'warn': warn, 'load': out([ld])[0]}


@router.post('/api/pack/load/{lid}/unload')
def load_unload(lid: int, body: dict = Body(...), user=Depends(perms.require('pack.ship_load.update'))):
    code = (body.get('code') or '').strip()
    with db.engine.begin() as c:
        ld = db.get(c, 'ship_load', lid)
        p = db.get(c, 'package', code=code)
        if not ld or not p or p['load'] != lid:
            raise HTTPException(404, _tr('{code} 不在这辆车上', code=code))
        if ld['status'] == 'shipped':
            raise HTTPException(400, _tr('已发运，不能卸'))
        for x in [p] + [q for q in _children(c, p) if q['id'] != p['id']]:
            db.update(c, 'package', x['id'], {'load': None, 'status': 'closed'}, raw=True)
        refresh_load(c, lid)
    return {'ok': True, 'msg': _tr('{code} 已卸下', code=code)}


def asn_doc(c, ld) -> dict:
    """ASN（发货通知）内容：抬头 + 托盘 → 箱 → 零件码。"""
    pk = TABLES['package']
    pkgs = db.rows(c, 'package', order=pk.c.id, load=ld['id'])
    mats = {m['id']: m for m in db.rows(c, 'material')}
    pallets = []
    loose = []
    for p in pkgs:
        if p['kind'] == 'pallet':
            pallets.append(p)
        elif not p['parent'] or p['parent'] not in {x['id'] for x in pkgs}:
            loose.append(p)

    def carton_out(ct):
        items = db.rows(c, 'package_item', order=TABLES['package_item'].c.id, package=ct['id'])
        m = mats.get(ct['material'], {})
        return {'carton': ct['code'], 'part_no': ct['customer_part_no'] or m.get('code'), 'supplier_part_no': m.get('code'), 'description': m.get('name'),
                'qty': ct['qty'], 'lots': [x for x in (ct['lots'] or '').split(',') if x], 'serials': [x['sn'] for x in items if x['sn']]}
    doc = {'asn_no': ld['asn_no'] or f"ASN{ld['code'][2:]}", 'load': ld['code'], 'delivery_note': ld['delivery_code'], 'customer': ld['customer'],
           'ship_to': ld['ship_to'], 'dock': ld['dock'], 'vehicle': ld['vehicle'], 'supplier': db.setting(c, 'company', ''), 'supplier_no': db.setting(c, f"supplier_no:{ld['customer']}", None) or db.setting(c, 'supplier_no', '100234'),
           'ship_date': db.jsonable(ld['shipped_at'] or clock.now()), 'pallets': [], 'totals': {}}
    lines = {}
    for p in pallets:
        cts = [carton_out(x) for x in db.rows(c, 'package', order=pk.c.id, parent=p['id'])]
        doc['pallets'].append({'pallet': p['code'], 'qty': p['qty'], 'cartons': cts})
        for ct in cts:
            lines[ct['part_no']] = lines.get(ct['part_no'], 0) + (ct['qty'] or 0)
    if loose:
        cts = [carton_out(x) for x in loose]
        doc['pallets'].append({'pallet': None, 'qty': sum(x['qty'] or 0 for x in cts), 'cartons': cts})
        for ct in cts:
            lines[ct['part_no']] = lines.get(ct['part_no'], 0) + (ct['qty'] or 0)
    doc['lines'] = [{'part_no': k, 'qty': v} for k, v in lines.items()]
    doc['totals'] = {'pallets': len(pallets), 'cartons': sum(len(x['cartons']) for x in doc['pallets']), 'qty': sum(lines.values())}
    return doc


def asn_flat(doc) -> str:
    """平面文件（管道分隔，一行一条：HDR 抬头 / LIN 零件号汇总 / PAL 托盘 / CTN 箱 / SER 零件码 / TRL 行数）。"""
    rows = [['HDR', doc['asn_no'], doc['ship_date'][:10].replace('-', ''), doc['supplier_no'], doc['customer'] or '', doc['delivery_note'] or '', doc['vehicle'] or '',
             doc['dock'] or '']]
    for ln in doc['lines']:
        rows.append(['LIN', ln['part_no'] or '', str(ln['qty'])])
    for p in doc['pallets']:
        rows.append(['PAL', p['pallet'] or 'LOOSE', str(p['qty'] or 0), str(len(p['cartons']))])
        for ct in p['cartons']:
            rows.append(['CTN', ct['carton'], p['pallet'] or '', ct['part_no'] or '', str(ct['qty'] or 0), ','.join(ct['lots'])])
            rows += [['SER', ct['carton'], s] for s in ct['serials']]
    rows.append(['TRL', str(len(rows) + 1)])
    return '\r\n'.join('|'.join(r) for r in rows) + '\r\n'


def asn_edifact(doc) -> str:
    """EDIFACT DESADV D.96A 风格样例（UNA/UNB 省略收发方 ID 映射，联调时按客户的实施指南补全）。"""
    seg = []
    d = doc['ship_date'][:10].replace('-', '')
    seg.append("UNH+1+DESADV:D:96A:UN'")
    seg.append(f"BGM+351+{doc['asn_no']}+9'")
    seg.append(f"DTM+137:{d}:102'")
    seg.append(f"DTM+11:{d}:102'")
    seg.append(f"RFF+AAU:{doc['delivery_note'] or ''}'")
    seg.append(f"NAD+SU+{doc['supplier_no']}::92'")
    seg.append(f"NAD+ST+{doc['customer'] or ''}::92'")
    seg.append(f"LOC+11+{doc['dock'] or ''}'")
    seg.append(f"TDT+12++30+31+:::{doc['vehicle'] or ''}'")
    n = 1
    for p in doc['pallets']:
        n += 1
        seg.append(f"CPS+{n}+1'")
        seg.append("PAC+1++PLT'")
        seg.append("PCI+17'")
        seg.append(f"GIN+ML+{p['pallet'] or 'LOOSE'}'")
        for ct in p['cartons']:
            n += 1
            seg.append(f"CPS+{n}+{n - 1}'")
            seg.append("PAC+1++BOX'")
            seg.append(f"GIN+ML+{ct['carton']}'")
            seg.append(f"LIN+++{ct['part_no']}:IN'")
            seg.append(f"PIA+1+{ct['supplier_part_no']}:SA'")
            seg.append(f"QTY+12:{ct['qty']}:PCE'")
            for lot in ct['lots']:
                seg.append(f"GIR+3+{lot}:BX'")
            for s in ct['serials'][:200]:
                seg.append(f"GIR+3+{s}:BN'")
    seg.append(f"CNT+2:{doc['totals']['cartons']}'")
    seg.append(f"UNT+{len(seg) + 1}+1'")
    return '\n'.join(seg) + '\n'


def asn_xlsx(doc) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'ASN'
    for k, v in ((_tr('ASN 号'), doc['asn_no']), (_tr('装车单'), doc['load']), (_tr('发货单'), doc['delivery_note']), (_tr('客户'), doc['customer']), (_tr('收货地址'), doc['ship_to']),
                 (_tr('卸货点'), doc['dock']), (_tr('车牌'), doc['vehicle']), (_tr('供应商'), doc['supplier']), (_tr('供应商号'), doc['supplier_no']), (_tr('发运日期'), doc['ship_date']),
                 (_tr('托数'), doc['totals']['pallets']), (_tr('箱数'), doc['totals']['cartons']), (_tr('件数'), doc['totals']['qty'])):
        ws.append([k, v])
    ws.append([])
    ws.append([_tr('托盘'), _tr('箱号'), _tr('客户零件号'), _tr('供应商零件号'), _tr('名称'), _tr('数量'), _tr('批次')])
    for p in doc['pallets']:
        for ct in p['cartons']:
            ws.append([p['pallet'] or _tr('散箱'), ct['carton'], ct['part_no'], ct['supplier_part_no'], ct['description'], ct['qty'], ','.join(ct['lots'])])
    ws2 = wb.create_sheet(_tr('零件码'))
    ws2.append([_tr('箱号'), _tr('零件码')])
    for p in doc['pallets']:
        for ct in p['cartons']:
            for s in ct['serials']:
                ws2.append([ct['carton'], s])
    for w, col in ((14, 'A'), (16, 'B'), (16, 'C'), (14, 'D'), (24, 'E')):
        ws.column_dimensions[col].width = w
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


@action('ship_load', 'asn')
def _load_asn(c, ld, body, user):
    if not db.rows(c, 'package', load=ld['id'], limit=1):
        raise HTTPException(400, _tr('车上还没有托盘 / 箱'))
    asn_no = ld['asn_no'] or f"ASN{ld['code'][2:]}"
    db.update(c, 'ship_load', ld['id'], {'asn_no': asn_no, 'asn_at': clock.now()}, raw=True)
    return {'msg': _tr('ASN {asn_no} 已生成', asn_no=asn_no), 'asn_no': asn_no}


@action('ship_load', 'ship')
def _load_ship(c, ld, body, user):
    """发运确认：车上所有零件 → 已发运（成品库出库），包装 → 已发运，生成发运记录和 ASN，回写发货单。"""
    if ld['status'] == 'shipped':
        raise HTTPException(400, _tr('已经发运了'))
    pkgs = db.rows(c, 'package', load=ld['id'])
    if not pkgs:
        raise HTTPException(400, _tr('车上还没有托盘 / 箱'))
    cartons = [p for p in pkgs if p['kind'] == 'carton']
    items = _parts_of(c, cartons)
    for it in items:                       # 发车前再查一次冻结（装车后才冻结的也拦住）
        if it['sn']:
            check_lot_out(c, sn=it['sn'], what=_tr('发运'))
        elif it['lot']:
            check_lot_out(c, lot=it['lot'], what=_tr('发运'))
    from .exec_erp import post_delivery
    from .warehouse import move
    now = clock.now()
    who = user.get('name') or user['username']
    pt, pe = TABLES['part'], TABLES['part_event']
    erp_done = post_delivery(c, ld['delivery_code'], user) in ('posted', 'already')     # 内置 ERP 的发货过账已扣库存、记发运
    fg = None if erp_done else db.get(c, 'warehouse', wtype='fg')
    wo = None
    for it in items:
        if it['sn']:
            p = db.get(c, 'part', sn=it['sn'])
            if not p:
                continue
            wo = wo or p['work_order']
            if fg and p['status'] == 'fg':
                move(c, 'out', p['product'], 1, fg['id'], ref=p['sn'], who=who, reason=_tr('发运 {code}', code=ld['code']), lot=p['heat'])
            c.execute(pt.update().where(pt.c.id == p['id']).values(status='shipped', finished_at=now, updated_at=now))
            c.execute(pe.insert().values(sn=p['sn'], t=now, station=_tr('发运'), event='shipped', data={'load': ld['code'], 'carton': next((x['code'] for x in cartons if x['id'] == it['package']), None)},
                                         created_at=now, updated_at=now))
        elif it['lot'] and fg:
            move(c, 'out', it['material'], it['qty'] or 0, fg['id'], ref=ld['code'], who=who, reason=_tr('发运 {code}', code=ld['code']), lot=it['lot'])
    for p in pkgs:
        db.update(c, 'package', p['id'], {'status': 'shipped', 'shipped_at': now}, raw=True)
    qty = sum(p['qty'] or 0 for p in cartons)
    sid = None if erp_done else db.insert(c, 'shipment', {'qty': qty, 'customer': ld['customer'], 'work_order': wo, 'time': now}, raw=True)
    asn_no = ld['asn_no'] or f"ASN{ld['code'][2:]}"
    db.update(c, 'ship_load', ld['id'], {'status': 'shipped', 'shipped_at': now, 'shipped_by': who, 'shipment': sid, 'asn_no': asn_no,
                                         'asn_at': ld['asn_at'] or now}, raw=True)
    refresh_load(c, ld['id'])
    return {'msg': _tr('已发运 {qty} 件（{len} 托 / {len2} 箱），ASN {asn_no}', qty=qty, len=len([p for p in pkgs if p['kind'] == 'pallet']), len2=len(cartons), asn_no=asn_no)
                   + (_tr('；发货单 {delivery_code} 已过账', delivery_code=ld['delivery_code']) if erp_done else ''), 'asn_no': asn_no, 'shipment': sid}


@action('ship_load', 'cancel')
def _load_cancel(c, ld, body, user):
    if ld['status'] == 'shipped':
        raise HTTPException(400, _tr('已发运，不能取消'))
    for p in db.rows(c, 'package', load=ld['id']):
        db.update(c, 'package', p['id'], {'load': None, 'status': 'closed'}, raw=True)
    db.update(c, 'ship_load', ld['id'], {'status': 'cancelled'}, raw=True)
    refresh_load(c, ld['id'])
    return {'msg': _tr('已取消，托盘放回待发区')}


@router.get('/api/pack/load/{lid}/asn')
def load_asn(lid: int, format: str = 'json', user=Depends(perms.require('pack.ship_load.read'))):
    with db.engine.connect() as c:
        ld = db.get(c, 'ship_load', lid)
        if not ld:
            raise HTTPException(404, _tr('装车单不存在'))
        doc = asn_doc(c, ld)
    name = doc['asn_no']
    if format == 'xlsx':
        return Response(asn_xlsx(doc), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': f'attachment; filename="{name}.xlsx"'})
    if format == 'flat':
        return Response(asn_flat(doc), media_type='text/plain; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="{name}.txt"'})
    if format == 'edifact':
        return Response(asn_edifact(doc), media_type='text/plain; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="{name}.edi"'})
    return Response(json.dumps(doc, ensure_ascii=False, indent=1), media_type='application/json')


@router.get('/api/pack/load/{lid}')
def load_full(lid: int, user=Depends(perms.require('pack.ship_load.read'))):
    pk = TABLES['package']
    with db.engine.connect() as c:
        ld = db.get(c, 'ship_load', lid)
        if not ld:
            raise HTTPException(404, _tr('装车单不存在'))
        pkgs = attach_labels(c, 'package', db.rows(c, 'package', order=pk.c.id, load=lid))
        dl = erp_delivery(c, ld['delivery_code']) if ld['delivery_code'] else None
        ready = attach_labels(c, 'package', db.rows(c, 'package', pk.c.status == 'closed', pk.c.kind == 'pallet', pk.c.load.is_(None),
                                                    *([pk.c.customer == ld['customer']] if ld['customer'] else []), order=pk.c.id, limit=50))
    return {'item': out([ld])[0], 'packages': out(pkgs), 'delivery': dl, 'ready': out(ready)}


# ───────────────────────── 追溯 ─────────────────────────
def pack_trace(c, sn) -> dict | None:
    """零件 → 箱 → 托 → 装车单 / ASN / 客户（views.trace_data 用）。"""
    it = db.get(c, 'package_item', sn=sn)
    if not it:
        return None
    ct = db.get(c, 'package', it['package'])
    pal = db.get(c, 'package', ct['parent']) if ct and ct['parent'] else None
    ld = db.get(c, 'ship_load', (pal or ct)['load']) if (pal or ct) and (pal or ct)['load'] else None
    return {'carton': ct['code'] if ct else None, 'carton_status': ct['status'] if ct else None, 'pallet': pal['code'] if pal else None,
            'load': ld['code'] if ld else None, 'load_id': ld['id'] if ld else None, 'asn_no': (ld or {}).get('asn_no'), 'delivery_code': (ld or {}).get('delivery_code'),
            'customer': (ld or {}).get('customer') or (ct or {}).get('customer'), 'shipped_at': db.jsonable((ld or {}).get('shipped_at')), 'packed_at': db.jsonable(it['time']),
            'packed_by': it['by']}


@router.get('/api/pack/trace')
def trace(code: str, user=Depends(perms.require('pack.package.read'))):
    """发运追溯：code 可以是装车单号、ASN 号、发货单号、托盘号、箱号、零件码。返回 发运 → 托 → 箱 → 零件 的树。"""
    code = code.strip()
    pk, lt = TABLES['package'], TABLES['ship_load']
    with db.engine.connect() as c:
        loads = db.rows(c, 'ship_load', sa.or_(lt.c.code == code, lt.c.asn_no == code, lt.c.delivery_code == code))
        roots = []
        if loads:
            for ld in loads:
                roots += [p for p in db.rows(c, 'package', order=pk.c.id, load=ld['id']) if p['kind'] == 'pallet' or not p['parent']]
        else:
            p = db.get(c, 'package', code=code)
            if not p:
                pt = pack_trace(c, code)
                if not pt:
                    raise HTTPException(404, _tr('没找到 {code}（装车单 / ASN / 发货单 / 托盘 / 箱 / 零件码）', code=code))
                p = db.get(c, 'package', code=pt['pallet'] or pt['carton'])
            if p['parent']:
                p = db.get(c, 'package', p['parent'])
            roots = [p]
            if p['load']:
                loads = [db.get(c, 'ship_load', p['load'])]

        def node(p):
            if p['kind'] == 'pallet':
                kids = [node(x) for x in db.rows(c, 'package', order=pk.c.id, parent=p['id'])]
                return {'code': p['code'], 'kind': 'pallet', 'qty': p['qty'], 'status': p['status'], 'lots': p['lots'], 'children': kids}
            items = db.rows(c, 'package_item', order=TABLES['package_item'].c.id, package=p['id'])
            return {'code': p['code'], 'kind': 'carton', 'qty': p['qty'], 'status': p['status'], 'lots': p['lots'],
                    'parts': [{'sn': i['sn'], 'lot': i['lot'], 'qty': i['qty']} for i in items]}
        tree = [node(p) for p in roots]
    return {'loads': out(loads), 'tree': tree, 'parts': sum(len(x.get('parts') or []) for p in tree for x in ([p] if p['kind'] == 'carton' else p['children']))}
