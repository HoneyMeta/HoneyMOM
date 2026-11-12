"""MES 查询（只读）：智能体"MES 查询"节点和聊天助手的 mes_query 工具共用。

每种查询返回 {rows, count, summary, data?}；summary 是给大模型和人看的中文摘要。
"""
from ..i18n import _tr
from collections import defaultdict
from datetime import timedelta

import sqlalchemy as sa

from .. import db
from ..crud import attach_labels, out
from ..db import clock
from ..schema import TABLES


def _f(v, d=1):
    if v is None:
        return '—'
    return f'{v:.{d}f}' if isinstance(v, float) else str(v)


def _eq(c, code):
    if not code:
        return None
    from ..services.ops import _find
    return _find(c, 'equipment', code)


def q_work_orders(p):
    from ..services.production import board
    wot = TABLES['work_order']
    status = p.get('status') or 'open'
    with db.engine.connect() as c:
        if status == 'open':
            rows = board(user=None)
        else:
            rows = db.rows(c, 'work_order', *([wot.c.status == status] if status != 'all' else []), order=(wot.c.priority, wot.c.due), limit=int(p.get('limit') or 20))
            attach_labels(c, 'work_order', rows)
            rows = out(rows)
    now = clock.now()
    res = []
    for w in rows[: int(p.get('limit') or 20)]:
        late = bool(w.get('late')) or bool(w.get('due') and str(w['due']) < db.jsonable(now) and (w.get('qty_done') or 0) < (w.get('qty') or 0))
        res.append({'code': w['code'], 'product': w.get('product__label'), 'qty': w['qty'], 'qty_done': w.get('qty_done') or 0, 'qty_scrap': w.get('qty_scrap') or 0,
                    'due': w.get('due'), 'eta': w.get('eta'), 'status': w['status'], 'priority': w.get('priority'), 'customer': w.get('customer'), 'late': late})
    if p.get('late_only'):
        res = [r for r in res if r['late']]
    summary = _tr('；').join(_tr('{code}（{v}）完成 {qty_done}/{qty}，交期 {v2}，预计 {v3}，优先级 {priority}', code=r['code'], v=r['customer'] or '', qty_done=r['qty_done'], qty=r['qty'], v2=(r['due'] or '')[:16], v3=(r['eta'] or '—')[:16], priority=r['priority'])
                        + (_tr('，延误') if r['late'] else '') for r in res) or _tr('没有符合条件的工单')
    return {'rows': res, 'count': len(res), 'summary': summary}


def q_reports(p):
    hours = float(p.get('hours') or 8)
    rt = TABLES['report']
    with db.engine.connect() as c:
        rows = db.rows(c, 'report', rt.c.start >= clock.now() - timedelta(hours=hours), order=rt.c.id.desc(), limit=int(p.get('limit') or 50))
        attach_labels(c, 'report', rows)
    agg = defaultdict(lambda: [0, 0])
    for r in rows:
        k = r.get('process__label') or _tr('其他')
        agg[k][0] += r['qty_good'] or 0
        agg[k][1] += r['qty_ng'] or 0
    return {'rows': out(rows), 'count': len(rows), 'summary': _tr('近 {hours:g} 小时报工：', hours=hours) + (_tr('；').join(_tr('{k} 合格 {g} 不良 {b}', k=k, g=g, b=b) for k, (g, b) in agg.items()) or _tr('无'))}


def q_oee(p):
    from ..services.equipment import COUNT_EVENTS, oee_of
    hours = float(p.get('hours') or 24)
    e = clock.now()
    s = e - timedelta(hours=hours)
    with db.engine.connect() as c:
        eq = _eq(c, p.get('equipment'))
        eqs = [eq] if eq else db.rows(c, 'equipment', TABLES['equipment'].c.etype.in_(list(COUNT_EVENTS)), order=TABLES['equipment'].c.code)
        rows = []
        for x in eqs:
            o = oee_of(c, x, s, e)
            if o:
                rows.append({'code': x['code'], 'name': x['name'], 'OEE': o['OEE'], 'A': o['A'], 'P': o['P'], 'Q': o['Q'], 'good': o['good'], 'bad': o['bad'],
                             'loss_h': o['loss']})
    rows.sort(key=lambda r: r['OEE'])
    loss = defaultdict(float)
    for r in rows:
        for k, v in r['loss_h'].items():
            if k != '运行':
                loss[k] += v
    summary = _tr('近 {hours:g} 小时 OEE（从低到高）：', hours=hours) + _tr('；').join(
        _tr('{code} {v:.0f}%（可用 {v2:.0f}% 性能 {v3:.0f}% 质量 {v4:.0f}%，停机 {停机}h 换型 {换型换刀}h 待机 {v5}h）', code=r['code'], v=r['OEE'] * 100, v2=r['A'] * 100, v3=r['P'] * 100, v4=r['Q'] * 100, 停机=r['loss_h']['停机'], 换型换刀=r['loss_h']['换型换刀'], v5=r['loss_h']['待机/等待'])
        for r in rows[:8]) + (_tr('。损失合计：') + _tr('、').join(f'{k} {v:.1f}h' for k, v in sorted(loss.items(), key=lambda x: -x[1])) if loss else '')
    return {'rows': rows, 'count': len(rows), 'summary': summary if rows else _tr('这段时间没有 OEE 数据'), 'data': {'loss_tree': dict(loss)}}


def q_downtime(p):
    hours = float(p.get('hours') or 24)
    st, al = TABLES['equip_state'], TABLES['alarm']
    since = clock.now() - timedelta(hours=hours)
    with db.engine.connect() as c:
        eq = _eq(c, p.get('equipment'))
        q = sa.select(st).where((st.c.state == 'Stopped') & sa.or_(st.c.end.is_(None), st.c.end >= since))
        if eq:
            q = q.where(st.c.equipment == eq['id'])
        rows = [dict(r) for r in c.execute(q.order_by(st.c.start.desc()).limit(int(p.get('limit') or 30))).mappings()]
        attach_labels(c, 'equip_state', rows)
        aq = sa.select(al).where(al.c.start >= since)
        if eq:
            aq = aq.where(al.c.equipment == eq['id'])
        alarms = [dict(r) for r in c.execute(aq.order_by(al.c.start.desc()).limit(30)).mappings()]
        attach_labels(c, 'alarm', alarms)
    now = clock.now()
    for r in rows:      # 仿真重启时数据时间可能倒退：负时长按 0 计
        r['minutes'] = max(0.0, round(((r['end'] or max(now, r['start'])) - r['start']).total_seconds() / 60, 1))
    tot = defaultdict(float)
    for r in rows:
        tot[r.get('equipment__label') or '?'] += r['minutes']
    summary = (_tr('近 {hours:g} 小时停机 {len} 次：', hours=hours, len=len(rows)) + _tr('；').join(_tr('{k} 共 {v:.0f} 分钟', k=k, v=v) for k, v in sorted(tot.items(), key=lambda x: -x[1]))
               + (_tr('。报警：') + _tr('；').join(_tr('{get} {msg}（{v}）', get=a.get('equipment__label'), msg=a['msg'], v=db.jsonable(a['start'])[11:16]) for a in alarms[:8]) if alarms else '')) if rows or alarms else _tr('近 {hours:g} 小时没有停机', hours=hours)
    return {'rows': out(rows), 'count': len(rows), 'summary': summary, 'data': {'alarms': out(alarms)}}


def q_quality(p):
    from ..services.quality import spc
    hours = float(p.get('hours') or 24)
    nt, dt = TABLES['ncr'], TABLES['defect_code']
    since = clock.now() - timedelta(hours=hours)
    with db.engine.connect() as c:
        ncrs = db.rows(c, 'ncr', nt.c.found_at >= since, order=nt.c.id.desc(), limit=int(p.get('limit') or 30))
        attach_labels(c, 'ncr', ncrs)
        pareto = c.execute(sa.select(dt.c.name, sa.func.count()).select_from(nt.outerjoin(dt, dt.c.id == nt.c.defect)).where(nt.c.found_at >= since)
                           .group_by(dt.c.name).order_by(sa.func.count().desc())).all()
    s = spc(char=p.get('char') or 'BORE_DIA', station='', n=125, sub=5, user=None)
    spc_txt = (_tr('SPC {get}：n={count}，Cpk={get2}，判异 {len} 处', get=s.get('name'), count=s['count'], get2=s.get('Cpk', '—'), len=len(s['rules']))
               + (_tr('（{rule}）', rule=s['rules'][-1]['rule']) if s['rules'] else '')) if s.get('enough') else _tr('SPC 数据不足')
    summary = _tr('近 {hours:g} 小时不合格品 {len} 条，', hours=hours, len=len(ncrs)) + (_tr('按缺陷：') + _tr('、').join(f'{n or _tr('未分类')} {k}' for n, k in pareto) if pareto else _tr('无')) + _tr('。{spc_txt}', spc_txt=spc_txt)
    return {'rows': out(ncrs), 'count': len(ncrs), 'summary': summary,
            'data': {'pareto': [{'name': n or _tr('未分类'), 'n': k} for n, k in pareto], 'spc': {k: s.get(k) for k in ('count', 'Cpk', 'Ppk', 'mean', 'lsl', 'usl', 'rules')}}}


def q_ncr_detail(p):
    with db.engine.connect() as c:
        from ..services.ops import _find
        n = _find(c, 'ncr', p.get('id') or p.get('code'))
        if not n:
            return {'rows': [], 'count': 0, 'summary': _tr('找不到不合格品 {v}', v=p.get('id') or p.get('code'))}
        attach_labels(c, 'ncr', [n])
        part = db.get(c, 'part', sn=n['sn']) if n['sn'] else None
        same = c.execute(sa.select(sa.func.count()).select_from(TABLES['ncr']).where(
            (TABLES['ncr'].c.defect == n['defect']) & (TABLES['ncr'].c.found_at >= clock.now() - timedelta(days=7)))).scalar() if n['defect'] else 0
    r = out([n])[0]
    summary = (_tr('不合格品 {code}：零件 {v}，缺陷 {v2}，发现于 {v3}（{get}），数量 {qty}，状态 {status}；近 7 天同类缺陷 {same} 起', code=n['code'], v=n['sn'] or '—', v2=r.get('defect__label') or _tr('未分类'), v3=n['station'] or '—', get=r.get('found_at'), qty=n['qty'], status=n['status'], same=same) + (_tr('；零件炉次 {heat}、浇包 {ladle}、浇注温度 {pour_temp}', heat=part['heat'], ladle=part['ladle'], pour_temp=part['pour_temp']) if part else ''))
    return {'rows': [r], 'count': 1, 'summary': summary, 'data': {'ncr': r, 'part': out([part])[0] if part else None, 'same_7d': same}}


def q_equipment(p):
    with db.engine.connect() as c:
        eq = _eq(c, p.get('equipment'))
        if not eq:
            rows = db.rows(c, 'equipment', order=TABLES['equipment'].c.code)
            return {'rows': out(rows), 'count': len(rows), 'summary': _tr('设备：') + _tr('、').join(_tr('{code} {name}（{v}）', code=e['code'], name=e['name'], v=e['status'] or '—') for e in rows)}
        rt, al, mp = TABLES['repair_order'], TABLES['alarm'], TABLES['maint_plan']
        reps = db.rows(c, 'repair_order', rt.c.equipment == eq['id'], order=rt.c.id.desc(), limit=5)
        alarms = db.rows(c, 'alarm', al.c.equipment == eq['id'], order=al.c.id.desc(), limit=8)
        plans = db.rows(c, 'maint_plan', mp.c.equipment == eq['id'])
        tools = db.rows(c, 'tool', equipment=eq['id'])
    summary = (_tr('{code} {name}（{v}，当前 {v2}）。最近维修：', code=eq['code'], name=eq['name'], v=eq['model'] or '', v2=eq['status'] or '—') +
               (_tr('；').join(_tr('{code} {fault}（{status}，{v}）', code=r['code'], fault=r['fault'], status=r['status'], v=db.jsonable(r['reported_at']) or '') for r in reps) or _tr('无')) +
               _tr('。最近报警：') + (_tr('；').join(_tr('{msg}（{jsonable}）', msg=a['msg'], jsonable=db.jsonable(a['start'])) for a in alarms) or _tr('无')) +
               _tr('。点检保养：') + (_tr('；').join(_tr('{name} 下次 {jsonable}', name=m['name'], jsonable=db.jsonable(m['next_due'])) for m in plans) or _tr('无')) +
               (_tr('。刀具：') + _tr('、').join(f"{t['code']} {t['used'] or 0}/{t['life_limit'] or '—'}" for t in tools[:6]) if tools else ''))
    return {'rows': out([eq]), 'count': 1, 'summary': summary, 'data': {'repairs': out(reps), 'alarms': out(alarms), 'maint_plans': out(plans), 'tools': out(tools)}}


def _stock_by_material(c):
    st = TABLES['stock']
    return {r[0]: r[1] or 0 for r in c.execute(sa.select(st.c.material, sa.func.sum(st.c.qty)).group_by(st.c.material)).all()}


def q_stock(p):
    with db.engine.connect() as c:
        mats = db.rows(c, 'material')
        have = _stock_by_material(c)
    rows = [{'material_code': m['code'], 'material_name': m['name'], 'unit': m['unit'], 'qty': round(have.get(m['id'], 0), 3), 'safety_stock': m['safety_stock'] or 0,
             'low': have.get(m['id'], 0) < (m['safety_stock'] or 0)} for m in mats]
    if p.get('low_only'):
        rows = [r for r in rows if r['low']]
    low = [r for r in rows if r['low']]
    return {'rows': rows, 'count': len(rows), 'summary': _tr('物料 {len} 种，低于安全库存 {len2} 种', len=len(rows), len2=len(low)) + (_tr('：') + _tr('、').join(f"{r['material_name']} {r['qty']:g}/{r['safety_stock']:g}{r['unit'] or ''}" for r in low) if low else '')}


def q_shortage(p):
    """在制/已下达工单剩余数量 × 多级 BOM → 需求，对比库存（扣安全库存）。"""
    wot = TABLES['work_order']
    with db.engine.connect() as c:
        wos = db.rows(c, 'work_order', wot.c.status.in_(('released', 'in_progress', 'draft')), order=(wot.c.priority, wot.c.due))
        boms = defaultdict(list)
        for b in db.rows(c, 'bom'):
            boms[b['product']].append((b['component'], b['qty'] or 0))
        mats = {m['id']: m for m in db.rows(c, 'material')}
        have = _stock_by_material(c)
    need = defaultdict(float)
    first_wo = {}

    def explode(mid, qty, wo, depth=0):
        for comp, q in boms.get(mid, []):
            need[comp] += qty * q
            first_wo.setdefault(comp, wo)
            if depth < 4:
                explode(comp, qty * q, wo, depth + 1)
    for w in wos:
        remain = max(0, (w['qty'] or 0) - (w['qty_done'] or 0) - (w['qty_scrap'] or 0))
        if remain:
            explode(w['product'], remain, w['code'])
    rows = []
    for mid, q in need.items():
        m = mats.get(mid)
        if not m:
            continue
        avail = have.get(mid, 0) - (m['safety_stock'] or 0)
        if q > avail + 1e-9:
            rows.append({'material_code': m['code'], 'material_name': m['name'], 'unit': m['unit'], 'need': round(q, 3), 'stock': round(have.get(mid, 0), 3),
                         'safety_stock': m['safety_stock'] or 0, 'shortage': round(q - avail, 3), 'work_order': first_wo.get(mid)})
    rows.sort(key=lambda r: -r['shortage'])
    return {'rows': rows, 'count': len(rows), 'summary': (_tr('缺料 {} 种：').format(len(rows)) + _tr('；').join(
        _tr('{material_name}（{material_code}）需 {need:g}{v}，库存 {stock:g}，安全库存 {safety_stock:g}，缺口 {shortage:g}，影响工单 {work_order}', material_name=r['material_name'], material_code=r['material_code'], need=r['need'], v=r['unit'] or '', stock=r['stock'], safety_stock=r['safety_stock'], shortage=r['shortage'], work_order=r['work_order'])
        for r in rows[:8])) if rows else _tr('在制工单的物料需求都能满足（已扣除安全库存）')}


def q_andon(p):
    t = TABLES['andon']
    status = p.get('status') or 'open'
    with db.engine.connect() as c:
        rows = db.rows(c, 'andon', *([t.c.status != 'closed'] if status == 'open' else []), order=t.c.id.desc(), limit=int(p.get('limit') or 20))
        attach_labels(c, 'andon', rows)
    return {'rows': out(rows), 'count': len(rows), 'summary': (_tr('安灯 {len} 条：', len=len(rows)) + _tr('；').join(
        _tr('{code} {description}（{v}，{status}）', code=r['code'], description=r['description'], v=r.get('equipment__label') or r['station'] or '', status=r['status']) for r in rows[:10])) if rows else _tr('没有未关闭的安灯')}


def q_alarms(p):
    hours = float(p.get('hours') or 8)
    al = TABLES['alarm']
    with db.engine.connect() as c:
        eq = _eq(c, p.get('equipment'))
        rows = db.rows(c, 'alarm', al.c.start >= clock.now() - timedelta(hours=hours), *([al.c.equipment == eq['id']] if eq else []), order=al.c.id.desc(),
                       limit=int(p.get('limit') or 30))
        attach_labels(c, 'alarm', rows)
    return {'rows': out(rows), 'count': len(rows), 'summary': (_tr('近 {hours:g} 小时报警 {len} 条：', hours=hours, len=len(rows)) + _tr('；').join(
        _tr('{get} {msg}（{v}{v2}）', get=r.get('equipment__label'), msg=r['msg'], v=db.jsonable(r['start'])[11:16], v2=_tr('，未恢复') if not r['end'] else '') for r in rows[:10])) if rows else _tr('近 {hours:g} 小时没有报警', hours=hours)}


def q_vf_live(p):
    tl = db.metadata.tables['tag_latest']
    with db.engine.connect() as c:
        eq = _eq(c, p.get('equipment'))
        q = sa.select(tl.c.equipment, tl.c.name, tl.c.value, tl.c.ts)
        if eq:
            q = q.where(tl.c.equipment == eq['id'])
        rows = [{'equipment': r[0], 'name': r[1], 'value': r[2], 'ts': db.jsonable(r[3])} for r in c.execute(q.order_by(tl.c.name).limit(int(p.get('limit') or 80))).all()]
    return {'rows': rows, 'count': len(rows), 'summary': (_tr('{v} 实时点位：', v=eq['code'] if eq else _tr('全部设备')) + _tr('；').join(f"{r['name']}={r['value']}" for r in rows[:30])) if rows else _tr('没有实时点位（虚拟工厂未连接？）')}


def q_kpi(p):
    from ..services.ai import day_kpi
    k = day_kpi(p.get('day') or None)
    pr, qa, eq, en = k['production'], k['quality'], k['equipment'], k['energy']
    oee = [x for x in eq['oee'] if x['OEE'] is not None]
    avg = sum(x['OEE'] for x in oee) / len(oee) if oee else None
    summary = (_tr('{day}：浇注/来料 {castings} 件，机加 {machined}，检测 {inspected}，入库 {stored}，发运 {shipped}；在制工单 {wo_open} 个', day=k['day'], castings=pr['castings'], machined=pr['machined'], inspected=pr['inspected'], stored=pr['stored'], shipped=pr['shipped'], wo_open=pr['wo_open'])
               + (_tr('，逾期 {join}', join=_tr('、').join(pr['wo_late'])) if pr['wo_late'] else '') + _tr('。报废 {scrap} 件，废品率 {v:.2f}%，返修 {rework}，未关闭 NCR {ncr_open}。', scrap=qa['scrap'], v=qa['scrap_rate'] * 100, rework=qa['rework'], ncr_open=qa['ncr_open'])
               + (_tr('平均 OEE {v:.1f}%，', v=avg * 100) if avg is not None else '') + _tr('报警 {alarms} 次。用电 {kwh:.0f} kWh，熔炼 {heats} 炉。', alarms=eq['alarms'], kwh=en['kwh'], heats=en['heats']))
    return {'rows': [], 'count': 1, 'summary': summary, 'data': k}


QUERY_KINDS = {
    'work_orders': ('工单与进度', q_work_orders, ['status', 'late_only', 'limit']),
    'reports': ('报工记录', q_reports, ['hours', 'limit']),
    'oee': ('OEE 与损失树', q_oee, ['equipment', 'hours']),
    'downtime': ('停机与报警', q_downtime, ['equipment', 'hours', 'limit']),
    'quality': ('质量与 SPC', q_quality, ['hours', 'char', 'limit']),
    'ncr_detail': ('某张不合格品详情', q_ncr_detail, ['id']),
    'equipment': ('设备与维修履历', q_equipment, ['equipment']),
    'stock': ('物料库存', q_stock, ['low_only']),
    'shortage': ('工单缺料（BOM 对库存）', q_shortage, []),
    'andon': ('安灯', q_andon, ['status', 'limit']),
    'alarms': ('报警记录', q_alarms, ['equipment', 'hours', 'limit']),
    'vf_live': ('虚拟工厂实时点位', q_vf_live, ['equipment', 'limit']),
    'kpi': ('某天汇总指标', q_kpi, ['day']),
}


# 每种业务查询需要的查看权限（5.0：按提问人 / 智能体执行人的权限；context.actor() 为空 = 系统内部，不限）
QUERY_PERMS = {'work_orders': 'production.work_order.read', 'reports': 'production.report.read', 'oee': 'page.equipment.oee', 'downtime': 'equipment.alarm.read',
               'quality': 'page.quality.spc', 'ncr_detail': 'quality.ncr.read', 'equipment': 'equipment.equipment.read', 'stock': 'warehouse.stock.read',
               'shortage': 'production.wo_kitting.read', 'andon': 'production.andon.read', 'alarms': 'equipment.alarm.read', 'vf_live': 'equipment.ts.read',
               'kpi': 'page.home'}


def run_query(kind: str, params: dict) -> dict:
    if kind not in QUERY_KINDS:
        raise ValueError(_tr('没有这种查询：{kind}', kind=kind))
    from .. import context, perms
    who = context.actor()
    if who is not None and QUERY_PERMS.get(kind) and not perms.can(who, QUERY_PERMS[kind]):
        raise ValueError(_tr('你没有查询"{v}"的权限', v=QUERY_KINDS[kind][0]))
    p = {k: v for k, v in (params or {}).items() if v not in (None, '')}
    for k in ('late_only', 'low_only'):
        if k in p and isinstance(p[k], str):
            p[k] = p[k].lower() in ('1', 'true', 'yes', '是')
    return QUERY_KINDS[kind][1](p)
