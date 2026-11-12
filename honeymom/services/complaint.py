"""客户投诉与召回（IATF 16949 10.2.3 / 10.2.6）：登记（客户、发货单 / 装车单、零件码、批次、箱号）→ 影响分析（按追溯反查：
受影响的炉次 / 批次 → 全部零件 → 箱、托、装车单、客户；已发运 vs 在厂）→ 遏制（在厂的按批次冻结，沿用 lot_hold；装车未发的发运时会被拦下）
→ 通知 → 8D（report_8d，关闭要电子签名）/ RMA（内置 ERP 的退货单号）→ 关闭（电子签名）。

召回模拟：/api/complaint/recall_sim?code=炉次|零件码|箱号|托盘号|装车单号 —— 不冻结，只算范围和耗时（演练用，IATF 要求定期演练可追溯性）。
"""
from ..i18n import _tr
import re
import time

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from .. import db, perms
from ..crud import HOOKS, action, out
from ..db import clock
from ..schema import TABLES
from . import esign

router = APIRouter()
PERM_RECALL = perms.declare('complaint.recall', '召回模拟 / 按投诉冻结在库', '客诉召回')
IN_HOUSE = ('wip', 'good', 'fg', 'hold', 'rework', 'raw_store')


def _split(s):
    return [x for x in re.split(r'[\s,，;；]+', s or '') if x]


def seeds(c, cm) -> dict:
    """投诉里给的线索 → 零件码 / 炉次批次 / 箱托 / 装车单。"""
    sns, lots, pkgs, loads = set(_split(cm.get('sns'))), set(_split(cm.get('lots'))), set(_split(cm.get('cartons'))), set()
    lt = TABLES['ship_load']
    if cm.get('load'):
        loads.add(cm['load'])
    if cm.get('delivery_code'):
        loads |= {x['id'] for x in db.rows(c, 'ship_load', lt.c.delivery_code == cm['delivery_code'])}
    return {'sns': sns, 'lots': lots, 'packages': pkgs, 'loads': loads}


def analyze(c, sns=(), lots=(), packages=(), loads=(), material=None) -> dict:
    """反查影响范围。返回 {heats, parts_total, shipped, in_house, scrap, cartons, loads, customers, stock_lots, elapsed_ms}。"""
    t0 = time.time()
    pt, it, pk, lt = TABLES['part'], TABLES['package_item'], TABLES['package'], TABLES['ship_load']
    heats = set(lots)
    sns = set(sns)
    # 箱 / 托 / 装车单 → 零件码
    pkg_ids = set()
    for code in packages:
        p = db.get(c, 'package', code=code)
        if p:
            pkg_ids.add(p['id'])
            if p['kind'] == 'pallet':
                pkg_ids |= {x['id'] for x in db.rows(c, 'package', parent=p['id'])}
    for lid in loads:
        pkg_ids |= {x['id'] for x in db.rows(c, 'package', load=lid)}
    if pkg_ids:
        for r in db.rows(c, 'package_item', it.c.package.in_(pkg_ids)):
            if r['sn']:
                sns.add(r['sn'])
            elif r['lot']:
                heats.add(r['lot'])
    for p in db.rows(c, 'part', pt.c.sn.in_(sns)) if sns else []:
        if p['heat']:
            heats.add(p['heat'])
    heats.discard(None)
    parts = db.rows(c, 'part', pt.c.heat.in_(heats)) if heats else []
    parts += [p for p in (db.rows(c, 'part', pt.c.sn.in_(sns)) if sns else []) if not p['heat']]
    if material:
        parts = [p for p in parts if p['product'] in (None, material)]
    by_sn = {p['sn']: p for p in parts}
    # 包装 / 发运
    items = db.rows(c, 'package_item', it.c.sn.in_(list(by_sn))) if by_sn else []
    items += db.rows(c, 'package_item', it.c.lot.in_(heats), it.c.sn.is_(None)) if heats else []
    pmap = {p['id']: p for p in db.rows(c, 'package', pk.c.id.in_({x['package'] for x in items}))} if items else {}
    parents = {p['id']: p for p in db.rows(c, 'package', pk.c.id.in_({p['parent'] for p in pmap.values() if p['parent']}))} if pmap else {}
    lmap = {}
    lids = {(parents.get(p['parent']) or p)['load'] for p in pmap.values()} - {None}
    if lids:
        lmap = {x['id']: x for x in db.rows(c, 'ship_load', lt.c.id.in_(lids))}
    carton_of = {x['sn']: pmap.get(x['package']) for x in items if x['sn']}
    shipped, in_house, scrap = [], [], []
    by_status, customers, cartons = {}, {}, {}
    for p in parts:
        ct = carton_of.get(p['sn'])
        pal = parents.get(ct['parent']) if ct and ct['parent'] else None
        ld = lmap.get((pal or ct or {}).get('load')) if (pal or ct) else None
        row = {'sn': p['sn'], 'heat': p['heat'], 'status': p['status'], 'carton': ct['code'] if ct else None, 'pallet': pal['code'] if pal else None,
               'load': ld['code'] if ld else None, 'customer': (ld or {}).get('customer') or (ct or {}).get('customer')}
        by_status[p['status']] = by_status.get(p['status'], 0) + 1
        if p['status'] == 'shipped' or (ld and ld['status'] == 'shipped'):
            shipped.append(row)
            if row['customer']:
                customers[row['customer']] = customers.get(row['customer'], 0) + 1
        elif p['status'] == 'scrap':
            scrap.append(row)
        else:
            in_house.append(row)
        if ct:
            cartons.setdefault(ct['code'], {'code': ct['code'], 'pallet': row['pallet'], 'load': row['load'], 'status': ct['status'], 'n': 0})['n'] += 1
    st = TABLES['stock']
    stock = [{'material': s['material'], 'warehouse': s['warehouse'], 'lot': s['lot'], 'qty': s['qty']}
             for s in (db.rows(c, 'stock', st.c.lot.in_(heats), st.c.qty > 0) if heats else [])]
    loads_out = [{'code': x['code'], 'customer': x['customer'], 'status': x['status'], 'delivery_code': x['delivery_code'], 'asn_no': x['asn_no'],
                  'shipped_at': db.jsonable(x['shipped_at'])} for x in lmap.values()]
    held = {h['lot'] for h in db.rows(c, 'lot_hold', TABLES['lot_hold'].c.lot.in_(heats), status='hold')} if heats else set()
    return {'heats': sorted(heats), 'held_heats': sorted(held), 'parts_total': len(parts), 'by_status': by_status,
            'shipped': {'count': len(shipped), 'customers': customers, 'parts': shipped[:300]},
            'in_house': {'count': len(in_house), 'parts': in_house[:300], 'not_held': sum(1 for p in in_house if p['status'] != 'hold')},
            'scrap': len(scrap), 'cartons': list(cartons.values())[:300], 'loads': loads_out, 'stock_lots': stock, 'elapsed_ms': int((time.time() - t0) * 1000)}


def _after_complaint(c, rid, user):
    cm = db.get(c, 'complaint', rid)
    if not cm['received_at']:
        db.update(c, 'complaint', rid, {'received_at': clock.now()}, raw=True)


HOOKS[('complaint', 'after_save')] = _after_complaint


@action('complaint', 'analyze')
def _cm_analyze(c, cm, body, user):
    s = seeds(c, cm)
    if not any(s.values()):
        raise HTTPException(400, _tr('请先填零件码、批次/炉次、箱号或发货单号，才能反查影响范围'))
    imp = analyze(c, s['sns'], s['lots'], s['packages'], s['loads'], cm['material'])
    db.update(c, 'complaint', cm['id'], {'impact': imp, 'status': 'analysis' if cm['status'] == 'open' else cm['status']}, raw=True)
    return {'msg': _tr('受影响炉次/批次 {len} 个、零件 {parts_total} 件：已发运 {count}（{len2} 个客户）、在厂 {count2}（未冻结 {not_held}）；用时 {elapsed_ms} ms', len=len(imp['heats']), parts_total=imp['parts_total'], count=imp['shipped']['count'], len2=len(imp['shipped']['customers']), count2=imp['in_house']['count'], not_held=imp['in_house']['not_held'], elapsed_ms=imp['elapsed_ms']), 'impact': imp}


@action('complaint', 'contain')
def _cm_contain(c, cm, body, user):
    """遏制：受影响的炉次 / 批次全部冻结（在厂零件 → 冻结；冻结的不能报工、出库、装箱、发运）。"""
    imp = cm['impact'] or _cm_analyze(c, cm, {}, user)['impact']
    hook = HOOKS.get(('lot_hold', 'after_save'))
    n = 0
    for lot in imp['heats']:
        if db.get(c, 'lot_hold', lot=lot, status='hold'):
            continue
        hid = db.insert(c, 'lot_hold', {'lot': lot, 'material': cm['material'], 'reason': _tr('客诉 {code} 遏制：{v}', code=cm['code'], v=(cm['description'] or '')[:60])}, raw=False)
        if hook:
            hook(c, hid, user)
        n += 1
    imp2 = analyze(c, *seeds(c, cm).values(), material=cm['material'])
    db.update(c, 'complaint', cm['id'], {'status': 'containment', 'holds': (cm['holds'] or 0) + n, 'impact': imp2,
                                         'containment': cm['containment'] or _tr('冻结在厂受影响批次 {n} 个；已发运 {count} 件通知客户隔离 / 挑选', n=n, count=imp2['shipped']['count'])}, raw=True)
    return {'msg': _tr('已冻结 {n} 个批次（在厂 {count} 件，未冻结 {not_held}）', n=n, count=imp2['in_house']['count'], not_held=imp2['in_house']['not_held']), 'holds': n}


@action('complaint', 'notify')
def _cm_notify(c, cm, body, user):
    imp = cm['impact'] or {}
    txt = (_tr('客户 {customer} 投诉 {code}：{v}\n影响炉次 {join}；已发运 {v2} 件，在厂 {v3} 件。', customer=cm['customer'], code=cm['code'], v=cm['description'] or '', join=', '.join(imp.get('heats') or []), v2=((imp.get('shipped') or {}).get('count')) or 0, v3=((imp.get('in_house') or {}).get('count')) or 0))
    for role in ('qc', 'manager', 'warehouse', 'planner'):
        db.insert(c, 'notice', {'title': _tr('客诉 {code}（{customer}）', code=cm['code'], customer=cm['customer']), 'content': txt, 'to_role': role, 'channel': 'inbox',
                                'level': 'urgent' if cm['severity'] == 'critical' else 'warn', 'link': f"/iatf/complaints?id={cm['id']}", 'source': '客诉',
                                'read': False, 'time': clock.now()}, raw=True)
    db.update(c, 'complaint', cm['id'], {'notified': True}, raw=True)
    return {'msg': _tr('已通知质量、厂长、仓库、计划')}


@action('complaint', 'to8d')
def _cm_8d(c, cm, body, user):
    if cm['report_8d']:
        return {'id': cm['report_8d'], 'msg': _tr('已经有 8D 了')}
    imp = cm['impact'] or {}
    rid = db.insert(c, 'report_8d', {'title': _tr('客诉 {code}：{v}', code=cm['code'], v=(cm['description'] or '')[:40]), 'defect': cm['defect'], 'complaint': cm['id'],
                                     'd2': _tr('{customer} 投诉：{v}（数量 {v2}）', customer=cm['customer'], v=cm['description'] or '', v2=cm['qty'] or '—'),
                                     'd3': cm['containment'] or (_tr('冻结在厂受影响批次 {join}', join=', '.join(imp.get('heats') or [])) if imp else ''), 'status': 'draft'}, raw=True)
    db.update(c, 'complaint', cm['id'], {'report_8d': rid}, raw=True)
    return {'id': rid, 'msg': _tr('已建 8D 报告')}


@action('complaint', 'rma')
def _cm_rma(c, cm, body, user):
    """建退货单（内置 ERP 的销售退货草稿，complaint_code 关联本投诉）。"""
    from .exec_erp import rma
    r = rma(c, cm, user)
    if r is None:
        raise HTTPException(400, _tr('没有内置 ERP（外部 ERP 模式）：在 ERP 里建退货单后，把退货单号填到“RMA 退货单”'))
    if r.get('error'):
        raise HTTPException(400, r['error'])
    db.update(c, 'complaint', cm['id'], {'rma_code': r['code']}, raw=True)
    return {'msg': _tr('已建退货单 {code}（草稿）', code=r['code']), 'code': r['code'], 'id': r['id']}


@action('complaint', 'close')
def _cm_close(c, cm, body, user):
    if cm['report_8d']:
        r = db.get(c, 'report_8d', cm['report_8d'])
        if r and r['status'] != 'closed':
            raise HTTPException(409, _tr('8D 报告 {code} 还没关闭，先完成 8D', code=r['code']))
    sig = esign.require(c, user, body, 'complaint', cm['id'], 'close', f"{cm['code']} {cm['customer']}")
    db.update(c, 'complaint', cm['id'], {'status': 'closed', 'closed_by': (sig or {}).get('name') or user.get('name') or user['username'], 'closed_at': clock.now()}, raw=True)
    return {'msg': _tr('已关闭')}


# 8D：提交评审 / 关闭（电子签名）
@action('report_8d', 'submit_review')
def _8d_review(c, r, body, user):
    if r['status'] != 'draft':
        raise HTTPException(400, _tr('只有草稿可以提交评审'))
    db.update(c, 'report_8d', r['id'], {'status': 'review'}, raw=True)
    return {'msg': _tr('已提交评审')}


@action('report_8d', 'close')
def _8d_close(c, r, body, user):
    if r['status'] == 'closed':
        return {'msg': _tr('已经关闭')}
    miss = [k.upper() for k in ('d4', 'd5', 'd6') if not (r.get(k) or '').strip()]
    if miss:
        raise HTTPException(409, _tr('{join} 还没写，不能关闭 8D', join=_tr('、').join(miss)))
    sig = esign.require(c, user, body, 'report_8d', r['id'], 'close', f"{r['code']} {r['title']}")
    db.update(c, 'report_8d', r['id'], {'status': 'closed', 'closed_by': (sig or {}).get('name') or user.get('name') or user['username'], 'closed_at': clock.now()}, raw=True)
    return {'msg': _tr('8D 已关闭')}


def _resolve(c, code):
    """召回模拟的入口码 → 线索。"""
    code = code.strip()
    if db.get(c, 'part', sn=code):
        return {'sns': [code]}
    if db.get(c, 'package', code=code):
        return {'packages': [code]}
    lt = TABLES['ship_load']
    ld = db.rows(c, 'ship_load', sa.or_(lt.c.code == code, lt.c.asn_no == code, lt.c.delivery_code == code))
    if ld:
        return {'loads': [x['id'] for x in ld]}
    if db.get(c, 'part', heat=code) or db.get(c, 'heat', code=code) or db.rows(c, 'stock', TABLES['stock'].c.lot == code, limit=1):
        return {'lots': [code]}
    raise HTTPException(404, _tr('没找到 {code}（零件码 / 炉次批次 / 箱号 / 托盘号 / 装车单 / ASN / 发货单）', code=code))


@router.get('/api/complaint/recall_sim')
def recall_sim(code: str, user=Depends(perms.require(PERM_RECALL))):
    with db.engine.connect() as c:
        s = _resolve(c, code)
        imp = analyze(c, s.get('sns', ()), s.get('lots', ()), s.get('packages', ()), s.get('loads', ()))
    return {'code': code, 'seed': s, **imp, 'at': db.jsonable(clock.now())}


@router.get('/api/complaint/recall_sim.html', response_class=HTMLResponse)
def recall_sim_html(code: str, user=Depends(perms.require(PERM_RECALL))):
    d = recall_sim(code, user)
    esc = lambda x: '' if x is None else str(x).replace('&', '&amp;').replace('<', '&lt;')
    sh = ''.join(f"<tr><td>{esc(p['sn'])}</td><td>{esc(p['heat'])}</td><td>{esc(p['carton'])}</td><td>{esc(p['pallet'])}</td><td>{esc(p['load'])}</td><td>{esc(p['customer'])}</td></tr>"
                 for p in d['shipped']['parts'][:200])
    ih = ''.join(f"<tr><td>{esc(p['sn'])}</td><td>{esc(p['heat'])}</td><td>{esc(p['status'])}</td><td>{esc(p['carton'])}</td></tr>" for p in d['in_house']['parts'][:200])
    cu = _tr('、').join(_tr('{k} {v} 件', k=k, v=v) for k, v in d['shipped']['customers'].items()) or '—'
    return _tr('<!doctype html><html><head><meta charset="utf-8"><title>召回模拟 {esc}</title><style>body{{font-family:\'Microsoft YaHei\',sans-serif;margin:28px;font-size:12px}}\ntable{{border-collapse:collapse;width:100%;margin:6px 0 16px}}td,th{{border:1px solid #bbb;padding:3px 6px;text-align:left}}th{{background:#f1f1f1}}h1{{font-size:18px}}.k b{{font-size:15px}}</style></head>\n<body><h1>召回模拟报告（可追溯性演练）</h1><p>起点：{esc}\u3000时间：{esc2}\u3000用时：{elapsed_ms} ms</p>\n<p class=k>受影响炉次/批次：<b>{esc3}</b>\u3000零件 <b>{parts_total}</b> 件\u3000已发运 <b>{count}</b>\u3000在厂 <b>{count2}</b>\n（已冻结批次 {len} 个）\u3000报废 {scrap}</p><p>涉及客户：{esc4}\u3000装车单：{esc5}</p>\n<h3>已发运</h3><table><tr><th>零件码</th><th>炉次</th><th>箱</th><th>托</th><th>装车单</th><th>客户</th></tr>{sh}</table>\n<h3>在厂</h3><table><tr><th>零件码</th><th>炉次</th><th>状态</th><th>箱</th></tr>{ih}</table><script>setTimeout(()=>window.print&&window.print(),400)</script></body></html>', esc=esc(code), esc2=esc(d['at']), elapsed_ms=d['elapsed_ms'], esc3=esc(_tr('、').join(d['heats']) or '—'), parts_total=d['parts_total'], count=d['shipped']['count'], count2=d['in_house']['count'], len=len(d['held_heats']), scrap=d['scrap'], esc4=esc(cu), esc5=esc(_tr('、').join(x['code'] for x in d['loads']) or '—'), sh=sh, ih=ih)


@router.get('/api/complaint/{cid}/full')
def complaint_full(cid: int, user=Depends(perms.require('complaint.complaint.read'))):
    from ..crud import attach_labels
    with db.engine.connect() as c:
        cm = db.get(c, 'complaint', cid)
        if not cm:
            raise HTTPException(404, _tr('投诉不存在'))
        attach_labels(c, 'complaint', [cm])
        holds = out(db.rows(c, 'lot_hold', TABLES['lot_hold'].c.reason.like(_tr('客诉 {code}%', code=cm['code']))))
        r8 = db.get(c, 'report_8d', cm['report_8d']) if cm['report_8d'] else None
        sig = esign.signatures(c, 'complaint', cid)
        from .exec_erp import returns_of
        rmas = returns_of(c, cm['code'])
    return {'item': out([cm])[0], 'holds': holds, 'report_8d': out([r8])[0] if r8 else None, 'signatures': sig, 'returns': rmas}
