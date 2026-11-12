"""仓储：出入库过账、库存、盘点、零件流转带来的库存变化、AGV 任务。"""
from ..i18n import _tr
import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db
from ..auth import current_user, require_write
from ..crud import HOOKS, attach_labels, out
from ..db import clock
from ..schema import TABLES


def _stock_add(c, material, wh, qty, lot=None, loc=None):
    st = TABLES['stock']
    cond = (st.c.material == material) & (st.c.warehouse == wh) & ((st.c.lot == lot) if lot else st.c.lot.is_(None))
    if loc:
        cond = cond & (st.c.location == loc)
    r = c.execute(sa.select(st).where(cond).limit(1)).mappings().first()
    now = clock.now()
    if r:
        c.execute(st.update().where(st.c.id == r['id']).values(qty=(r['qty'] or 0) + qty, updated_at=now))
    else:
        c.execute(st.insert().values(material=material, warehouse=wh, location=loc, lot=lot, qty=qty, created_at=now, updated_at=now))


def move(c, ttype, material, qty, wh, to_wh=None, lot=None, ref='', who='系统', reason='', loc=None, txn=True):
    """记一笔出入库并更新库存。"""
    if ttype == 'in':
        _stock_add(c, material, wh, qty, lot, loc)
    elif ttype == 'out':
        _stock_add(c, material, wh, -qty, lot, loc)
    elif ttype == 'transfer':
        _stock_add(c, material, wh, -qty, lot, loc)
        _stock_add(c, material, to_wh, qty, lot)
    elif ttype == 'adjust':
        _stock_add(c, material, wh, qty, lot, loc)
    if txn:
        db.insert(c, 'stock_txn', {'ttype': ttype, 'material': material, 'qty': qty, 'warehouse': wh, 'to_warehouse': to_wh, 'lot': lot, 'ref_doc': ref,
                                   'employee': who, 'time': clock.now(), 'reason': reason, 'location': loc}, raw=True)


# 同一个钩子位上可能已经挂了别的（services/gates.py 的冻结批次闸门）：串起来，不管哪个模块先导入都两个都执行
_prev_txn_hook = HOOKS.get(('stock_txn', 'after_save'))


def _after_txn(c, rid, user):
    """手工新增的出入库单过账（只在新建时过账一次）。"""
    if _prev_txn_hook:
        _prev_txn_hook(c, rid, user)
    t = db.get(c, 'stock_txn', rid)
    if t.get('reason') and t['reason'].startswith('[已过账]'):
        return
    move(c, t['ttype'], t['material'], t['qty'] or 0, t['warehouse'], t['to_warehouse'], t['lot'], txn=False, loc=t['location'])
    db.update(c, 'stock_txn', rid, {'reason': _tr('[已过账] ') + (t['reason'] or ''), 'employee': t['employee'] or user['name'] or user['username'],
                                    'time': t['time'] or clock.now()}, raw=True)


HOOKS[('stock_txn', 'after_save')] = _after_txn


def _wh(c, wtype):
    r = db.get(c, 'warehouse', wtype=wtype)
    return r['id'] if r else None


def _blank_material(c, product):
    """成品对应的毛坯物料：BOM 里类型为半成品的子件。"""
    bt, mt = TABLES['bom'], TABLES['material']
    r = c.execute(sa.select(bt.c.component).join(mt, mt.c.id == bt.c.component).where((bt.c.product == product) & (mt.c.mtype == 'semi'))).scalar()
    return r or product


def on_part_move(c, part, ev, ts):
    if not part.get('product'):
        return
    if ev == 'to_raw_store':
        wh = _wh(c, 'blank')
        if wh:
            move(c, 'in', _blank_material(c, part['product']), 1, wh, ref=part['sn'], who='AGV', reason=_tr('毛坯转储'))
    elif ev == 'stored':
        wh = _wh(c, 'fg')
        if wh:
            move(c, 'in', part['product'], 1, wh, ref=part['sn'], who=_tr('立库'), reason=_tr('成品入库'), lot=part.get('heat'))
    elif ev == 'shipped':
        wh = _wh(c, 'fg')
        if wh:
            move(c, 'out', part['product'], 1, wh, ref=part['sn'], who=_tr('立库'), reason=_tr('发运'), lot=part.get('heat'))


def on_agv_delivery(c, agv, dest, ts):
    """同一台 AGV 同一时刻送达的零件算一趟任务。"""
    t = TABLES['agv_task']
    r = c.execute(sa.select(t.c.id).where((t.c.agv == agv) & (t.c.end == ts))).scalar()
    if r:
        c.execute(t.update().where(t.c.id == r).values(load=t.c.load + 1))
    else:
        names = {'cnc_in': _tr('机加线边库'), 'raw': _tr('毛坯暂存'), 'fg': _tr('成品待入库'), 'asrs': _tr('立库入口'), 'store': _tr('毛坯库')}
        c.execute(t.insert().values(agv=agv, task=_tr('送料到{get}', get=names.get(dest, dest)), src=_tr('毛坯暂存'), dst=names.get(dest, dest), load=1, end=ts,
                                    state='完成', created_at=ts, updated_at=ts))


router = APIRouter()


@router.get('/api/warehouse/overview')
def overview(user=Depends(current_user)):
    st, mt = TABLES['stock'], TABLES['material']
    with db.engine.connect() as c:
        rows = [dict(r) for r in c.execute(sa.select(st.c.material, st.c.warehouse, sa.func.sum(st.c.qty).label('qty')).group_by(st.c.material, st.c.warehouse)).mappings()]
        attach_labels(c, 'stock', rows)
        mats = {m['id']: m for m in db.rows(c, 'material')}
        for r in rows:
            m = mats.get(r['material'], {})
            r['safety'] = m.get('safety_stock')
            r['unit'] = m.get('unit')
            r['low'] = bool(m.get('safety_stock') and (r['qty'] or 0) < m['safety_stock'])
    return out(rows)


@router.post('/api/warehouse/count')
def count(body: dict = Body(...), user=Depends(current_user)):
    """盘点：{warehouse, lines:[{material, actual}]} → 差异生成调整单。"""
    require_write(user, 'stock_txn', 'create')
    st = TABLES['stock']
    n = 0
    with db.engine.begin() as c:
        wh = int(body['warehouse'])
        for ln in body.get('lines', []):
            m = int(ln['material'])
            cur = c.execute(sa.select(sa.func.coalesce(sa.func.sum(st.c.qty), 0)).where((st.c.material == m) & (st.c.warehouse == wh))).scalar()
            diff = float(ln['actual']) - float(cur)
            if abs(diff) > 1e-9:
                move(c, 'adjust', m, diff, wh, who=user['name'] or user['username'], reason=_tr('盘点：账面 {cur:g}，实盘 {float:g}', cur=cur, float=float(ln["actual"])))
                n += 1
    return {'ok': True, 'adjusted': n}
