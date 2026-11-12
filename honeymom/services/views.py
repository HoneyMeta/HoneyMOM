"""追溯与看板。"""
from ..i18n import _tr
from collections import defaultdict
from datetime import timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from .. import db
from ..auth import current_user
from ..crud import attach_labels, out
from ..db import clock
from ..schema import TABLES

router = APIRouter()
EVENT_CN = {'poured': '浇注', 'received': '来料收货', 'shakeout': '落砂', 'cleaned': '抛丸清理', 'to_raw_store': '转入毛坯库', 'delivered': 'AGV 送到线边',
            'machining_start': '开始机加', 'machined': '机加完成', 'deburred': '去毛刺', 'inspected': '内窥检测', 'rework': '返修', 'scrap': '报废',
            'stored': '成品入库', 'shipped': '发运', 'packed': '装箱', 'outsource_out': '委外发出', 'outsource_back': '委外收回', 'hold': '冻结', 'release': '解冻'}


def trace_data(c, sn):
    part = db.get(c, 'part', sn=sn)
    if not part:
        return None
    attach_labels(c, 'part', [part])
    pe = TABLES['part_event']
    events = [dict(r) for r in c.execute(sa.select(pe).where(pe.c.sn == sn).order_by(pe.c.t, pe.c.id)).mappings()]
    for e in events:
        e['label'] = EVENT_CN.get(e['event'], e['event'])
    heat = db.get(c, 'heat', code=part['heat']) if part['heat'] else None
    if heat:
        attach_labels(c, 'heat', [heat])
    treat = None
    if part['ladle']:
        lad = part['ladle'].split('#')[0]
        tr = TABLES['treatment_rec']
        born = part['born_at']
        treat = c.execute(sa.select(tr).where((tr.c.ladle == part['ladle']) | ((tr.c.ladle.like(lad + '%')) & (tr.c.time <= born)))
                          .order_by(tr.c.time.desc()).limit(1)).mappings().first()
        treat = dict(treat) if treat else None
    ir, iv = TABLES['insp_record'], TABLES['insp_value']
    insp = [dict(r) for r in c.execute(sa.select(ir).where(ir.c.sn == sn).order_by(ir.c.time)).mappings()]
    meas = [dict(r) for r in c.execute(sa.select(iv.c.char, iv.c.value, iv.c.ok, iv.c.created_at).where(iv.c.text == sn)).mappings()]
    for r in insp:
        r['values'] = out(db.rows(c, 'insp_value', record=r['id']))
    ncr = out(attach_labels(c, 'ncr', db.rows(c, 'ncr', sn=sn)))
    rework = out(db.rows(c, 'rework', sn=sn))
    wo = db.get(c, 'work_order', part['work_order']) if part['work_order'] else None
    # 加工时机床的状态和报警
    ctx = []
    if part['machine']:
        eq = db.get(c, 'equipment', code=part['machine'])
        me = next((e for e in events if e['event'] == 'machined'), None)
        if eq and me:
            al = TABLES['alarm']
            t0 = me['t'] - timedelta(minutes=30)
            ctx = [dict(r) for r in c.execute(sa.select(al.c.msg, al.c.start, al.c.end).where((al.c.equipment == eq['id']) & (al.c.start <= me['t']) &
                                                                                           sa.or_(al.c.end.is_(None), al.c.end >= t0))).mappings()]
            tc = TABLES['tool_change']
            last_changes = [dict(r) for r in c.execute(sa.select(tc.c.tool, tc.c.tool_name, tc.c.reason, tc.c.time).where((tc.c.equipment == eq['id']) & (tc.c.time <= me['t']))
                                                       .order_by(tc.c.time.desc()).limit(5)).mappings()]
            ctx = {'alarms': out(ctx), 'tool_changes': out(last_changes), 'equipment': eq['name']}
    res = {'part': out([part])[0], 'events': out(events), 'heat': out([heat])[0] if heat else None, 'treatment': out([treat])[0] if treat else None,
           'inspections': out(insp), 'measures': out(meas), 'ncr': ncr, 'rework': rework, 'work_order': out([wo])[0] if wo else None, 'machining_ctx': ctx}
    for key, fn in TRACE_EXT:           # 其它模块补充的追溯段（如包装发运：箱 → 托 → 装车单 / ASN，services/exec_pack.pack_trace）
        res[key] = fn(c, sn)
    return res


TRACE_EXT: list = []          # [(键, fn(c, 零件码) -> dict | None)]


@router.get('/api/trace/part/{sn}')
def trace(sn: str, user=Depends(current_user)):
    with db.engine.connect() as c:
        d = trace_data(c, sn.strip())
    if not d:
        raise HTTPException(404, _tr('没有零件 {sn}', sn=sn))
    return d


@router.get('/api/trace/heat/{code}')
def trace_heat(code: str, user=Depends(current_user)):
    """正向追溯：一个炉次（或批次）的全部零件去向——出问题时圈定召回范围。"""
    pt = TABLES['part']
    with db.engine.connect() as c:
        parts = db.rows(c, 'part', order=pt.c.id, heat=code)
        heat = db.get(c, 'heat', code=code)
    st = defaultdict(int)
    for p in parts:
        st[p['status']] += 1
    return {'heat': out([heat])[0] if heat else None, 'count': len(parts), 'status': dict(st),
            'parts': [{'sn': p['sn'], 'status': p['status'], 'station': p['station'], 'machine': p['machine'], 'scrap_cause': p['scrap_cause']} for p in parts]}


@router.get('/api/trace/part/{sn}/print', response_class=HTMLResponse)
def trace_print(sn: str, user=Depends(current_user)):
    """可打印的追溯报告（浏览器"打印 → 另存为 PDF"）。"""
    with db.engine.connect() as c:
        d = trace_data(c, sn)
    if not d:
        raise HTTPException(404, _tr('没有这个零件'))
    p, h = d['part'], d['heat'] or {}
    esc = lambda x: '' if x is None else str(x).replace('&', '&amp;').replace('<', '&lt;')
    rows = ''.join(f"<tr><td>{esc(e['t'])}</td><td>{esc(e['station'])}</td><td>{esc(e['label'])}</td><td>{esc(e['data'])}</td></tr>" for e in d['events'])
    insp = ''.join(f"<tr><td>{esc(r['time'])}</td><td>{esc(r['station'])}</td><td>{esc(r['result'])}</td><td>{esc(r['remark'])}</td></tr>" for r in d['inspections'])
    return _tr('<!doctype html><html><head><meta charset="utf-8"><title>追溯报告 {esc}</title>\n<style>body{{font-family:\'Microsoft YaHei\',sans-serif;margin:32px;color:#222}}h1{{font-size:20px}}table{{border-collapse:collapse;width:100%;margin:8px 0 18px}}\ntd,th{{border:1px solid #ccc;padding:4px 6px;font-size:12px;text-align:left}}th{{background:#f3f3f3}}.k td:first-child{{width:140px;background:#fafafa}}\n.hd{{display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #e2b33c;padding-bottom:8px}}</style></head><body>\n<div class="hd"><h1>零件质量追溯报告</h1><div>蜂元智能 MOM · 打印时间 {now:%Y-%m-%d %H:%M}</div></div>\n<table class="k"><tr><td>零件码</td><td>{esc2}</td></tr><tr><td>产品</td><td>{esc3}</td></tr>\n<tr><td>工单</td><td>{esc4}</td></tr><tr><td>状态</td><td>{esc5} {esc6}</td></tr>\n<tr><td>炉次</td><td>{esc7}\u3000C {esc8}\u3000Si {esc9}\u3000CE {esc10}\u3000出铁温度 {esc11}</td></tr>\n<tr><td>浇注</td><td>浇包 {esc12}\u3000砂型 {esc13}\u3000浇注温度 {esc14} °C\u3000残镁 {esc15} %\u3000型砂水分 {esc16} %</td></tr>\n<tr><td>机加</td><td>{esc17}</td></tr></table>\n<h3>工序履历</h3><table><tr><th>时间</th><th>工位</th><th>事件</th><th>数据</th></tr>{rows}</table>\n<h3>检验记录</h3><table><tr><th>时间</th><th>工位</th><th>结论</th><th>说明</th></tr>{insp}</table>\n<script>window.print && setTimeout(()=>window.print(), 400)</script></body></html>', esc=esc(sn), now=clock.now(), esc2=esc(p['sn']), esc3=esc(p.get('product__label')), esc4=esc(p.get('work_order__label')), esc5=esc(p['status']), esc6=esc(p['scrap_cause']), esc7=esc(p['heat']), esc8=esc(h.get('C')), esc9=esc(h.get('Si')), esc10=esc(h.get('CE')), esc11=esc(h.get('tap_temp')), esc12=esc(p['ladle']), esc13=esc(p['mold']), esc14=esc(p['pour_temp']), esc15=esc(p['mg']), esc16=esc(p['sand_moisture']), esc17=esc(p['machine']), rows=rows, insp=insp)


@router.get('/api/dash/home')
def home(user=Depends(current_user)):
    from .ai import day_kpi, llm_available
    k = day_kpi()
    now = clock.now()
    pe = TABLES['part_event']
    start = (now - timedelta(hours=23)).replace(minute=0, second=0, microsecond=0)
    with db.engine.connect() as c:
        hourly = defaultdict(lambda: defaultdict(int))
        for t, ev in c.execute(sa.select(pe.c.t, pe.c.event).where((pe.c.t >= start) & pe.c.event.in_(('poured', 'received', 'machined', 'stored', 'scrap')))).all():
            hourly[t.strftime('%m-%d %H:00')][ev] += 1
        eq = TABLES['equipment']
        status = dict(c.execute(sa.select(eq.c.status, sa.func.count()).where(eq.c.etype.notin_(('crew', 'agv'))).group_by(eq.c.status)).all())
        al = TABLES['alarm']
        alarms = [dict(r) for r in c.execute(sa.select(al.c.id, al.c.equipment, al.c.msg, al.c.start).where(al.c.end.is_(None)).order_by(al.c.start.desc()).limit(10)).mappings()]
        attach_labels(c, 'alarm', alarms)
        rep = db.rows(c, 'daily_report', order=TABLES['daily_report'].c.day.desc(), limit=1)
        wot = TABLES['work_order']
        wos = db.rows(c, 'work_order', wot.c.status.in_(('released', 'in_progress')), order=(wot.c.priority, wot.c.due), limit=8)
        attach_labels(c, 'work_order', wos)
        from .quality import ncr_pending_count
        ncr_open = ncr_pending_count(c)          # 与不合格品页"待处理"视图同一口径（/e/ncr?view=pending）
        rt = TABLES['repair_order']
        rep_open = c.execute(sa.select(sa.func.count()).select_from(rt).where(rt.c.status.in_(('reported', 'assigned', 'repairing')))).scalar()
        mt = TABLES['maint_plan']
        due = c.execute(sa.select(sa.func.count()).select_from(mt).where(mt.c.next_due <= now.date())).scalar()
        tl = TABLES['tool']
        tool_warn = c.execute(sa.select(sa.func.count()).select_from(tl).where(tl.c.status.in_(('warn', 'change')))).scalar()
    hours = [(start + timedelta(hours=i)).strftime('%m-%d %H:00') for i in range(24)]
    return {'now': db.jsonable(now), 'kpi': k, 'hours': hours, 'hourly': {h: dict(hourly.get(h, {})) for h in hours}, 'status': status,
            'alarms': out(alarms), 'report': out(rep)[0] if rep else None, 'orders': out(wos),
            'todo': {'ncr': ncr_open, 'repair': rep_open, 'maint': due, 'tool': tool_warn}, 'llm': llm_available()}
