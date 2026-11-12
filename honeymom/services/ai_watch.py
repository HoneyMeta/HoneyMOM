"""AI 值守：后台观察者（质量 / 设备 / 排程 / 物料）定期看数据，发现苗头就记一条"发现"（finding）：
标题、严重程度、证据（数据片段 + 可点的链接 + 小图）、建议动作（一张动作草稿，审批后才执行）。

- 去重：同一个 key（观察者 + 对象，如 equipment.alarm_repeat:CNC3）未关闭时只更新"最后发现时间 / 次数 / 证据"，不重复提醒；
  已确认（ack）的 12 小时内不再打开，之后情况还在才重新打开。
- 有大模型时，新发现由 AI 写一两句解读（演示模式回放录制，没有录制时用规则说明）。
- 发现进工作台信息流（home_feed provider "ai_watch"，需要 AI 功能"AI 值守"）。

接口（需要"AI 值守"）：
  GET  /api/ai/watch/findings?status=open|acked|all&domain=&limit=
  POST /api/ai/watch/scan                       立即检查一遍（返回新增 / 更新数）
  POST /api/ai/watch/findings/{id}/ack          确认（"知道了"）
  POST /api/ai/watch/findings/{id}/draft        按建议起草动作（返回动作草稿，审批后才执行）
后台每 60 秒检查一遍（AI 总开关和"AI 值守"开着时）。
"""
from ..i18n import _tr
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db
from ..auth import current_user
from ..db import clock
from ..schema import TABLES, metadata
from . import ai_demo, ops

router = APIRouter()
from ..schema import ai_finding_t as finding_t  # noqa: E402  (表定义在 schema.py)
DOMAINS = {'quality': '质量', 'equipment': '设备', 'schedule': '排程', 'material': '物料'}
SEV = {'critical': 0, 'warning': 1, 'info': 2}


def _ts(t):
    return t.strftime('%m-%d %H:%M') if t else ''


# ---------------- 观察者 ----------------
def w_spindle(c, now):
    """CNC 主轴负载：最近 2 小时 90 分位明显高于前 24 小时（或绝对值很高）。"""
    ts = metadata.tables['tag_sample']
    out = []
    for e in db.rows(c, 'equipment', TABLES['equipment'].c.etype == 'cnc'):
        rows = c.execute(sa.select(ts.c.ts, ts.c.num).where((ts.c.equipment == e['id']) & ts.c.name.like('Monitoring/Spindle/Load%')
                                                              & (ts.c.ts >= now - timedelta(hours=26))).order_by(ts.c.ts)).all()
        recent = [v for t, v in rows if t >= now - timedelta(hours=2) and v and v > 1]
        base = [v for t, v in rows if t < now - timedelta(hours=2) and v and v > 1]
        if len(recent) < 20:
            continue
        p90 = sorted(recent)[int(len(recent) * 0.9)]
        b90 = sorted(base)[int(len(base) * 0.9)] if len(base) >= 40 else None
        rise = (p90 / b90 - 1) if b90 else 0
        if not (p90 >= 85 or (b90 and rise >= 0.15 and p90 - b90 >= 4)):
            continue
        tools = db.rows(c, 'tool', equipment=e['id'])
        worn = sorted([t for t in tools if (t['wear_pct'] or 0) >= 60], key=lambda t: -(t['wear_pct'] or 0))
        hourly = defaultdict(list)
        for t, v in rows:
            if t >= now - timedelta(hours=12) and v and v > 1:
                hourly[t.strftime('%H:00')].append(v)
        x = sorted(hourly)
        draft = ({'op': 'param_adjust', 'payload': {'entity': 'tool', 'record': worn[0]['code'], 'equipment': e['code'], 'field': 'life_limit',
                                                     'value': max(1, int((worn[0]['life_limit'] or 100) * 0.85)), 'reason': _tr('主轴负载上升，{code} AI 估计磨损 {wear_pct:.0f}%', code=worn[0]['code'], wear_pct=worn[0]['wear_pct'])}}
                 if worn else {'op': 'repair_order', 'payload': {'equipment': e['code'], 'fault': _tr('主轴负载偏高（近 2 小时 90 分位 {p90:.0f}%）', p90=p90), 'level': 'normal',
                                                                 'cause': _tr('值守发现：主轴负载较前 24 小时明显上升，建议检查刀具、夹具和冷却')}})
        out.append({'key': f"equipment.spindle:{e['code']}", 'watcher': 'equipment.spindle', 'domain': 'equipment', 'subject': e['code'],
                    'severity': 'critical' if p90 >= 95 else 'warning',
                    'title': _tr('{code} 主轴负载上升', code=e['code']) + (f" {rise * 100:.0f}%" if b90 else _tr('（90 分位 {p90:.0f}%）', p90=p90)),
                    'summary': _tr('近 2 小时主轴负载 90 分位 {p90:.1f}%', p90=p90) + (_tr('，前 24 小时 {b90:.1f}%', b90=b90) if b90 else '') + _tr('。')
                               + (_tr('{code} 刀具 AI 估计磨损 {wear_pct:.0f}%，可能是刀具变钝。', code=worn[0]['code'], wear_pct=worn[0]['wear_pct']) if worn else _tr('常见原因：刀具磨损、余量变大、冷却不足。')),
                    'evidence': [{'label': _tr('近 2 小时 P90'), 'value': f'{p90:.1f}%'}, *([{'label': _tr('前 24 小时 P90'), 'value': f'{b90:.1f}%'}] if b90 else []),
                                 {'label': _tr('设备'), 'value': f"{e['code']} {e['name']}", 'link': f"/e/equipment?id={e['id']}"},
                                 *([{'label': _tr('刀具'), 'value': _tr('{code} 磨损 {wear_pct:.0f}%', code=worn[0]['code'], wear_pct=worn[0]['wear_pct']), 'link': f"/e/tool?id={worn[0]['id']}"}] if worn else [])],
                    'blocks': [{'type': 'chart', 'kind': 'line', 'title': _tr('{code} 主轴负载（每小时均值）', code=e['code']), 'x': x, 'unit': '%',
                                'series': [{'name': _tr('主轴负载'), 'data': [round(statistics.mean(hourly[h]), 1) for h in x]}]}],
                    'draft': draft, 'entity': 'equipment', 'entity_id': e['id'], 'data': {'设备': e['code'], '近2小时P90': round(p90, 1), '前24小时P90': round(b90, 1) if b90 else None}})
    return out


def w_alarm_repeat(c, now):
    """同一台设备同一条报警 8 小时内反复出现（≥ 5 次，取最多的 4 条）。"""
    al = TABLES['alarm']
    rows = c.execute(sa.select(al.c.equipment, al.c.msg, sa.func.count(), sa.func.sum(al.c.dur_s), sa.func.max(al.c.start)).where(al.c.start >= now - timedelta(hours=8))
                     .group_by(al.c.equipment, al.c.msg).having(sa.func.count() >= 5).order_by(sa.func.count().desc()).limit(4)).all()
    out = []
    for eid, msg, n, dur, last in rows:
        e = db.get(c, 'equipment', eid)
        if not e:
            continue
        mins = round((dur or 0) / 60, 1)
        people = '操作工' in (msg or '') or '上料' in (msg or '')
        flow = any(w in (msg or '') for w in (_tr('堵料'), _tr('缓存满'), _tr('等料'), _tr('缺料'), _tr('断料')))
        hours = Counter(t.strftime('%H:00') for (t,) in c.execute(sa.select(al.c.start).where((al.c.equipment == eid) & (al.c.msg == msg) & (al.c.start >= now - timedelta(hours=8)))).all())
        x = sorted(hours)
        draft = ({'op': 'andon_raise', 'payload': {'atype': 'other', 'equipment': e['code'], 'description': _tr('{code} 反复「{msg}」：8 小时 {n} 次，请班长协调人员', code=e['code'], msg=msg, n=n)}} if people else
                 {'op': 'andon_raise', 'payload': {'atype': 'material', 'equipment': e['code'], 'description': _tr('{code} 反复「{msg}」：8 小时 {n} 次，请计划 / 物流平衡上下游节拍', code=e['code'], msg=msg, n=n)}} if flow else
                 {'op': 'repair_order', 'payload': {'equipment': e['code'], 'fault': _tr('反复报警：{msg}', msg=msg), 'level': 'urgent' if n >= 6 else 'normal',
                                                    'cause': _tr('值守发现：8 小时内出现 {n} 次，累计 {mins} 分钟', n=n, mins=mins)}})
        out.append({'key': f"equipment.alarm_repeat:{e['code']}:{msg}"[:190], 'watcher': 'equipment.alarm_repeat', 'domain': 'equipment', 'subject': e['code'],
                    'severity': 'critical' if n >= 8 or mins >= 60 else 'warning',
                    'title': _tr('{code} 8 小时内 {n} 次「{msg}」', code=e['code'], n=n, msg=msg),
                    'summary': _tr('累计 {mins} 分钟，最近一次 {v}。', mins=mins, v=_ts(last)) + (_tr('属于人员到位问题，影响开动率：建议调整上料岗位或排班。') if people else
                                                                       _tr('上下游节拍不平衡（堵料 / 等料），不是设备故障：建议平衡节拍或调整缓存。') if flow else _tr('反复出现，建议安排设备员排查根因。')),
                    'evidence': [{'label': _tr('次数'), 'value': _tr('{n} 次 / 8 小时', n=n)}, {'label': _tr('累计'), 'value': _tr('{mins} 分钟', mins=mins)},
                                 {'label': _tr('设备'), 'value': f"{e['code']} {e['name']}", 'link': f"/e/equipment?id={e['id']}"},
                                 {'label': _tr('报警记录'), 'value': _tr('查看'), 'link': f"/e/alarm?f_equipment={e['id']}"}],
                    'blocks': [{'type': 'chart', 'kind': 'bar', 'title': _tr('「{msg}」每小时次数', msg=msg), 'x': x, 'unit': '次', 'series': [{'name': _tr('次数'), 'data': [hours[h] for h in x]}]}],
                    'draft': draft, 'entity': 'equipment', 'entity_id': e['id'], 'data': {'设备': e['code'], '报警': msg, '次数': n, '累计分钟': mins}})
    return out


def w_oee_drop(c, now):
    from .equipment import oee_of
    eqt = TABLES['equipment']
    out = []
    for e in db.rows(c, 'equipment', eqt.c.etype.in_(('cnc', 'molding', 'inspection', 'deburr'))):
        o24, o4 = oee_of(c, e, now - timedelta(hours=24), now), oee_of(c, e, now - timedelta(hours=4), now)
        if not o24 or not o4 or o24['OEE'] is None or o4['OEE'] is None or o24['OEE'] < 0.3 or o4['span_h'] < 2:
            continue
        drop = o24['OEE'] - o4['OEE']
        if drop < 0.12:
            continue
        worst = min(('A', 'P', 'Q'), key=lambda k: o4[k] - o24[k])
        name = {'A': _tr('可用率'), 'P': _tr('性能率'), 'Q': _tr('质量率')}[worst]
        out.append({'key': f"equipment.oee_drop:{e['code']}", 'watcher': 'equipment.oee_drop', 'domain': 'equipment', 'subject': e['code'],
                    'severity': 'warning' if drop < 0.25 else 'critical',
                    'title': _tr('{code} OEE 下降 {v:.0f} 个点（{v2:.0f}% → {v3:.0f}%）', code=e['code'], v=drop * 100, v2=o24['OEE'] * 100, v3=o4['OEE'] * 100),
                    'summary': _tr('主要掉在{name}：{v:.0f}% → {v2:.0f}%。', name=name, v=o24[worst] * 100, v2=o4[worst] * 100),
                    'evidence': [{'label': _tr('24 小时 OEE'), 'value': f"{o24['OEE'] * 100:.1f}%"}, {'label': _tr('近 4 小时'), 'value': f"{o4['OEE'] * 100:.1f}%"},
                                 {'label': name, 'value': f"{o24[worst] * 100:.0f}% → {o4[worst] * 100:.0f}%"}, {'label': _tr('OEE 分析'), 'value': _tr('查看'), 'link': '/equipment/oee'}],
                    'blocks': [{'type': 'chart', 'kind': 'bar', 'title': _tr('{code} OEE 构成', code=e['code']), 'x': [_tr('可用率'), _tr('性能率'), _tr('质量率'), 'OEE'], 'format': 'pct',
                                'series': [{'name': _tr('24 小时'), 'data': [o24['A'], o24['P'], o24['Q'], o24['OEE']]}, {'name': _tr('近 4 小时'), 'data': [o4['A'], o4['P'], o4['Q'], o4['OEE']]}]}],
                    'draft': None, 'entity': 'equipment', 'entity_id': e['id'], 'data': {'设备': e['code'], 'OEE24h': o24['OEE'], 'OEE4h': o4['OEE']}})
    return out


def w_tool_wear(c, now):
    """AI 磨损估计已经很高、但寿命计数还没到：计数换刀会来不及。"""
    out = []
    tl = TABLES['tool']
    for t in db.rows(c, 'tool', tl.c.wear_pct >= 80):
        life = t['life_limit'] or 0
        if life and (t['used'] or 0) >= life * 0.95:
            continue
        e = db.get(c, 'equipment', t['equipment']) if t['equipment'] else None
        code = e['code'] if e else ''
        out.append({'key': f"equipment.tool_wear:{code}:{t['code']}", 'watcher': 'equipment.tool_wear', 'domain': 'equipment', 'subject': f"{code} {t['code']}",
                    'severity': 'critical' if t['wear_pct'] >= 95 else 'warning',
                    'title': _tr('{code} {code2} AI 估计磨损 {wear_pct:.0f}%，寿命计数只到 {v:.0f}%', code=code, code2=t['code'], wear_pct=t['wear_pct'], v=(t['used'] or 0) / life * 100 if life else 0),
                    'summary': _tr('按计数换刀会晚，可能崩刃或尺寸超差。建议提前换刀，并把这把刀的寿命计数下调。'),
                    'evidence': [{'label': _tr('磨损'), 'value': f"{t['wear_pct']:.0f}%"}, {'label': _tr('已用 / 寿命'), 'value': _tr('{v}/{life} 件', v=t['used'] or 0, life=life)},
                                 {'label': _tr('刀具'), 'value': f"{code} {t['code']}", 'link': f"/e/tool?id={t['id']}"}],
                    'blocks': [], 'draft': {'op': 'param_adjust', 'payload': {'entity': 'tool', 'record': t['code'], 'equipment': code, 'field': 'life_limit',
                                                                               'value': max(1, int((t['used'] or life * 0.8) * 1.05)) if life else 100, 'reason': _tr('AI 估计磨损 {wear_pct:.0f}%', wear_pct=t['wear_pct'])}},
                    'entity': 'tool', 'entity_id': t['id'], 'data': {'刀具': f"{code} {t['code']}", '磨损%': t['wear_pct'], '已用': t['used'], '寿命': life}})
    return out


def w_scrap(c, now):
    """废品率：近 8 小时比之前 24 小时明显升高。"""
    pe = TABLES['part_event']

    def rate(s, e):
        ev = dict(c.execute(sa.select(pe.c.event, sa.func.count()).where((pe.c.t >= s) & (pe.c.t < e) & pe.c.event.in_(('poured', 'received', 'scrap'))).group_by(pe.c.event)).all())
        made = ev.get('poured', 0) + ev.get('received', 0)
        return (ev.get('scrap', 0) / made if made else None), ev.get('scrap', 0), made
    r8, s8, m8 = rate(now - timedelta(hours=8), now)
    r24, s24, m24 = rate(now - timedelta(hours=32), now - timedelta(hours=8))
    if r8 is None or s8 < 3 or m8 < 30:
        return []
    if r24 and r8 < r24 * 1.5:
        return []
    if not r24 and r8 < 0.03:
        return []
    hours = defaultdict(lambda: [0, 0])
    for t, ev in c.execute(sa.select(pe.c.t, pe.c.event).where((pe.c.t >= now - timedelta(hours=12)) & pe.c.event.in_(('poured', 'received', 'scrap')))).all():
        hours[t.strftime('%H:00')][1 if ev == 'scrap' else 0] += 1
    x = sorted(hours)
    nt, dt = TABLES['ncr'], TABLES['defect_code']
    top = c.execute(sa.select(dt.c.name, sa.func.count()).select_from(nt.outerjoin(dt, dt.c.id == nt.c.defect)).where(nt.c.found_at >= now - timedelta(hours=8))
                    .group_by(dt.c.name).order_by(sa.func.count().desc()).limit(3)).all()
    return [{'key': 'quality.scrap_spike', 'watcher': 'quality.scrap_spike', 'domain': 'quality', 'subject': _tr('全厂'),
             'severity': 'critical' if r8 >= 0.06 or (r24 and r8 >= r24 * 2.5) else 'warning',
             'title': _tr('废品率升高：近 8 小时 {v:.1f}%', v=r8 * 100) + (_tr('（之前 {v:.1f}%）', v=r24 * 100) if r24 else ''),
             'summary': _tr('近 8 小时报废 {s8} 件 / 投产 {m8} 件。主要缺陷：', s8=s8, m8=m8) + (_tr('、').join(f"{n or _tr('未分类')} {k}" for n, k in top) or '—') + _tr('。'),
             'evidence': [{'label': _tr('近 8 小时'), 'value': _tr('{v:.2f}%（{s8}/{m8}）', v=r8 * 100, s8=s8, m8=m8)}, *([{'label': _tr('之前 24 小时'), 'value': _tr('{v:.2f}%（{s24}/{m24}）', v=r24 * 100, s24=s24, m24=m24)}] if r24 else []),
                          {'label': _tr('缺陷回溯'), 'value': _tr('查看'), 'link': '/cross/backtrace'}, {'label': _tr('不合格品'), 'value': _tr('查看'), 'link': '/e/ncr'}],
             'blocks': [{'type': 'chart', 'kind': 'line', 'title': _tr('每小时废品率'), 'x': x, 'format': 'pct',
                         'series': [{'name': _tr('废品率'), 'data': [round(hours[h][1] / hours[h][0], 4) if hours[h][0] else None for h in x]}]}],
             'draft': {'op': 'ncr', 'payload': {'description': _tr('近 8 小时废品率 {v:.1f}%（{s8}/{m8}），主要缺陷：', v=r8 * 100, s8=s8, m8=m8) + _tr('、').join(n or _tr('未分类') for n, _ in top), 'with_8d': True,
                                                 'qty': s8}},
             'entity': None, 'entity_id': None, 'data': {'近8小时废品率': round(r8, 4), '之前24小时废品率': round(r24, 4) if r24 else None, '报废': s8, '投产': m8}}]


def w_defect_cluster(c, now):
    """同一种缺陷 8 小时内 ≥ 4 条不合格品。"""
    nt, dt = TABLES['ncr'], TABLES['defect_code']
    rows = c.execute(sa.select(nt.c.defect, dt.c.name, sa.func.count(), sa.func.max(nt.c.found_at)).select_from(nt.join(dt, dt.c.id == nt.c.defect))
                     .where(nt.c.found_at >= now - timedelta(hours=8)).group_by(nt.c.defect, dt.c.name).having(sa.func.count() >= 4)).all()
    out = []
    for did, name, n, last in rows:
        ncrs = db.rows(c, 'ncr', nt.c.defect == did, nt.c.found_at >= now - timedelta(hours=8), order=nt.c.found_at.desc(), limit=20)
        stations = Counter(x['station'] or '—' for x in ncrs)
        opened = [x for x in ncrs if x['status'] == 'open']
        out.append({'key': f"quality.defect_cluster:{name}", 'watcher': 'quality.defect_cluster', 'domain': 'quality', 'subject': name,
                    'severity': 'critical' if n >= 10 else 'warning',
                    'title': _tr('「{name}」8 小时内 {n} 条不合格品', name=name, n=n),
                    'summary': _tr('主要在 {join}；{len} 条还没处理。建议转评审并起草 8D，找共同原因。', join=_tr('、').join(_tr('{k} {v} 条', k=k, v=v) for k, v in stations.most_common(2)), len=len(opened)),
                    'evidence': [{'label': x['code'], 'value': (x['station'] or '') + ' ' + _ts(x['found_at']), 'link': f"/e/ncr?id={x['id']}"} for x in ncrs[:3]]
                    + [{'label': _tr('缺陷回溯'), 'value': _tr('查看'), 'link': '/cross/backtrace'}],
                    'blocks': [{'type': 'table', 'title': _tr('最近的不合格品'), 'columns': [{'key': 'code', 'label': 'NCR'}, {'key': 'station', 'label': _tr('工位')}, {'key': 'time', 'label': _tr('时间')}],
                                'rows': [{'code': x['code'], 'station': x['station'] or '', 'time': _ts(x['found_at'])} for x in ncrs[:6]]}],
                    'draft': {'op': 'ncr', 'payload': {'ncr': (opened or ncrs)[0]['code'], 'description': _tr('「{name}」8 小时内 {n} 条，集中在 {v}', name=name, n=n, v=stations.most_common(1)[0][0]), 'with_8d': True}},
                    'entity': 'ncr', 'entity_id': (opened or ncrs)[0]['id'], 'data': {'缺陷': name, '条数': n, '工位': dict(stations)}})
    return out


def w_schedule(c, now):
    """排程：按最近 4 小时实际产出速度，预计赶不上交期的工单。"""
    wot, pe = TABLES['work_order'], TABLES['part_event']
    out = []
    for w in db.rows(c, 'work_order', wot.c.status.in_(('released', 'in_progress'))):
        if not w['due'] or w['due'] < now:
            continue
        left = w['qty'] - (w['qty_done'] or 0)
        if left <= 0:
            continue
        pt = TABLES['part']
        done4 = c.execute(sa.select(sa.func.count()).select_from(pe.join(pt, pt.c.sn == pe.c.sn)).where((pt.c.work_order == w['id']) & (pe.c.event == 'stored')
                                                                                                        & (pe.c.t >= now - timedelta(hours=4)))).scalar() or 0
        rate = done4 / 4
        hours_left = (w['due'] - now).total_seconds() / 3600
        if rate <= 0:
            if hours_left > 24 or w['status'] != 'in_progress':
                continue
            eta_h = None
        else:
            eta_h = left / rate
            if eta_h <= hours_left * 1.05:
                continue
        out.append({'key': f"schedule.pace:{w['code']}", 'watcher': 'schedule.pace', 'domain': 'schedule', 'subject': w['code'],
                    'severity': 'critical' if hours_left < 8 else 'warning',
                    'title': _tr('工单 {code} 按当前速度赶不上交期', code=w['code']),
                    'summary': _tr('还差 {left} 件，近 4 小时入库 {done4} 件（{rate:.1f} 件/小时），', left=left, done4=done4, rate=rate) + (_tr('预计还要 {eta_h:.1f} 小时，', eta_h=eta_h) if eta_h else _tr('目前没有产出，'))
                               + _tr('离交期 {hours_left:.1f} 小时。建议提高优先级或加班。', hours_left=hours_left),
                    'evidence': [{'label': _tr('剩余'), 'value': _tr('{left} 件', left=left)}, {'label': _tr('速度'), 'value': _tr('{rate:.1f} 件/小时', rate=rate)}, {'label': _tr('离交期'), 'value': _tr('{hours_left:.1f} 小时', hours_left=hours_left)},
                                 {'label': _tr('工单'), 'value': w['code'], 'link': f"/e/work_order?id={w['id']}"}],
                    'blocks': [], 'draft': {'op': 'schedule_change', 'payload': {'changes': [{'work_order': w['code'], 'priority': max(1, (w['priority'] or 5) - 2)}],
                                                                                 'reason': _tr('按当前速度预计晚于交期（还差 {left} 件）', left=left)}},
                    'entity': 'work_order', 'entity_id': w['id'], 'data': {'工单': w['code'], '剩余': left, '每小时': round(rate, 2), '离交期小时': round(hours_left, 1)}})
    return out[:5]


def w_material(c, now):
    """物料：在制工单按 BOM 还要用的量 > 现有库存。"""
    wot, st = TABLES['work_order'], TABLES['stock']
    need = defaultdict(float)
    users = defaultdict(list)
    for w in db.rows(c, 'work_order', wot.c.status.in_(('released', 'in_progress'))):
        left = max(0, w['qty'] - (w['qty_done'] or 0))
        for b in db.rows(c, 'bom', product=w['product']):
            need[b['component']] += left * (b['qty'] or 0)
            users[b['component']].append(w['code'])
    out = []
    for mid, q in need.items():
        if q <= 0:
            continue
        have = c.execute(sa.select(sa.func.coalesce(sa.func.sum(st.c.qty), 0)).where(st.c.material == mid)).scalar() or 0
        if have >= q:
            continue
        m = db.get(c, 'material', mid)
        short = q - have
        out.append({'key': f"material.coverage:{m['code']}", 'watcher': 'material.coverage', 'domain': 'material', 'subject': m['code'],
                    'severity': 'critical' if have <= 0 else 'warning',
                    'title': _tr('{name} 不够在制工单用：缺 {short:g} {v}', name=m['name'], short=short, v=m['unit'] or ''),
                    'summary': _tr('按 BOM，{join} 还要用 {q:g} {v}，库存 {have:g}。建议提交物料申请或调整投产顺序。', join=_tr('、').join(sorted(set(users[mid]))[:3]), q=q, v=m['unit'] or '', have=have),
                    'evidence': [{'label': _tr('需求'), 'value': f"{q:g} {m['unit'] or ''}"}, {'label': _tr('库存'), 'value': f"{have:g} {m['unit'] or ''}"},
                                 {'label': _tr('物料'), 'value': f"{m['code']} {m['name']}", 'link': f"/e/material?id={m['id']}"}, {'label': _tr('库存'), 'value': _tr('查看'), 'link': '/warehouse'}],
                    'blocks': [], 'draft': {'op': 'material_request', 'payload': {'material': m['code'], 'qty': round(short, 3), 'work_order': sorted(set(users[mid]))[0],
                                                                                   'reason': _tr('值守发现：在制工单需求超过库存')}},
                    'entity': 'material', 'entity_id': m['id'], 'data': {'物料': m['code'], '需求': q, '库存': have}})
    return out[:5]


WATCHERS = [('equipment.spindle', w_spindle), ('equipment.alarm_repeat', w_alarm_repeat), ('equipment.oee_drop', w_oee_drop), ('equipment.tool_wear', w_tool_wear),
            ('quality.scrap_spike', w_scrap), ('quality.defect_cluster', w_defect_cluster), ('schedule.pace', w_schedule), ('material.coverage', w_material)]


# ---------------- AI 解读 ----------------
def narrate(f):
    """新发现的一两句解读：有大模型时让它写；演示模式回放录制；都没有时用规则说明。返回 (文字, by)。"""
    from .ai import chat_once, llm_available

    def live():
        if not llm_available():
            return None
        return chat_once(_tr('你是工厂的 AI 值守员。下面是系统自动发现的一个苗头（JSON）。用 2~3 句中文说明：这意味着什么、最可能的原因、现在该做什么。只用给出的数字，不要编造，不要客套。\n\n') + json.dumps({k: f.get(k) for k in ('title', 'summary', 'evidence', 'data')}, ensure_ascii=False, default=str))
    try:
        r = ai_demo.run('watch', 'finding', {'watcher': f['watcher'], 'subject': f.get('subject') or '', 'text': f['watcher'] + ' ' + (f.get('subject') or '')},
                        live=live, fallback=lambda: None, recordable=lambda r: bool(r), mark=lambda r: {'text': r, 'demo': True}, exact=True)
    except Exception:
        r = None
    if isinstance(r, dict) and r.get('demo'):
        # 回放的解读来自录制那一刻：数字和现在这条发现对不上时标 demo_old（界面注明"录制时的数字"）
        from .ai_blocks import grounded_strict
        from .assistant import _nums
        facts = json.dumps({k: f.get(k) for k in ('title', 'summary', 'evidence', 'data')}, ensure_ascii=False, default=str)
        have = _nums(facts)
        return r['text'], ('demo' if all(grounded_strict(n, have) for n in _nums(r['text'])) else 'demo_old')
    if isinstance(r, str) and r.strip():
        return r.strip(), 'ai'
    return '', 'rule'


# ---------------- 扫描 / 存储 ----------------
_last_scan = {'t': None}


def scan(narrative=True):
    """跑一遍所有观察者，写入 / 更新发现。返回 {'new': n, 'updated': n, 'resolved': n}。"""
    now = clock.now()
    found = []
    with db.engine.connect() as c:
        for name, fn in WATCHERS:
            try:
                found += fn(c, now)
            except Exception as ex:          # 一个观察者出错不影响其他
                import logging
                logging.getLogger('mes.ai_watch').warning('watcher %s failed: %s', name, ex)
    new = upd = 0
    fresh = []
    keys = {f['key'] for f in found}
    with db.engine.begin() as c:
        for f in found:
            row = c.execute(sa.select(finding_t).where((finding_t.c.key == f['key']) & (finding_t.c.status != 'resolved')).order_by(finding_t.c.id.desc())).mappings().first()
            vals = {k: f.get(k) for k in ('watcher', 'domain', 'severity', 'title', 'summary', 'evidence', 'blocks', 'draft', 'entity', 'entity_id', 'data')}
            if row:
                reopen = row['status'] == 'acked' and row['acked_at'] and now - row['acked_at'] > timedelta(hours=12)
                c.execute(finding_t.update().where(finding_t.c.id == row['id']).values(**vals, last_seen=now, count=(row['count'] or 1) + 1,
                                                                                        **({'status': 'open'} if reopen else {})))
                upd += 1
            else:
                fid = c.execute(finding_t.insert().values(key=f['key'], **vals, status='open', count=1, first_seen=now, last_seen=now, narrative='', by='rule')).inserted_primary_key[0]
                fresh.append((fid, f))
                new += 1
        # 情况已经消失的（这一轮没再发现、且 6 小时没出现）：标记为已解除
        res = c.execute(finding_t.update().where((finding_t.c.status.in_(('open', 'acked'))) & (finding_t.c.last_seen < now - timedelta(hours=6))
                                                 & finding_t.c.key.notin_(keys or {''})).values(status='resolved'))
    if narrative:
        for fid, f in fresh[:6]:
            text, by = narrate(f)
            if text:
                with db.engine.begin() as c:
                    c.execute(finding_t.update().where(finding_t.c.id == fid).values(narrative=text, by=by))
    _last_scan['t'] = now
    return {'new': new, 'updated': upd, 'resolved': res.rowcount or 0, 'at': db.jsonable(now)}


def finding_out(r):
    d = {k: db.jsonable(v) for k, v in dict(r).items()}
    d['domain_label'] = DOMAINS.get(r['domain'], r['domain'])
    if d.get('draft') and d['draft'].get('op') in ops.OPS:
        d['draft_label'] = ops.OPS[d['draft']['op']]['label']
    return d


def list_findings(status='open', domain='', limit=50):
    with db.engine.connect() as c:
        q = sa.select(finding_t)
        if status == 'open':
            q = q.where(finding_t.c.status == 'open')
        elif status == 'acked':
            q = q.where(finding_t.c.status == 'acked')
        elif status == 'active':
            q = q.where(finding_t.c.status.in_(('open', 'acked')))
        if domain:
            q = q.where(finding_t.c.domain == domain)
        rows = c.execute(q.order_by(finding_t.c.last_seen.desc()).limit(min(200, limit * 2))).mappings().all()
    rows = sorted(rows, key=lambda r: (r['status'] != 'open', SEV.get(r['severity'], 3), -(r['last_seen'] or datetime.min).timestamp()))
    return [finding_out(r) for r in rows[:limit]]


# ---------------- 工作台信息流 ----------------
def feed_provider(user, now):
    items = []
    for f in list_findings('open', limit=12):
        items.append({'id': f"watch:{f['id']}", 'kind': 'ai', 'severity': f['severity'], 'category': f['domain'], 'title': f['title'],
                      'body': f.get('narrative') or f['summary'], 'evidence': f.get('evidence') or [], 'ts': f['last_seen'] or now,
                      'link': next((e['link'] for e in f.get('evidence') or [] if e.get('link')), ''),
                      'entity': f.get('entity'), 'entity_id': f.get('entity_id'), 'data': f.get('data') or {}, 'finding': f,
                      'ask': _tr('AI 值守发现：{title}。{summary} 帮我分析原因并给出处理建议（查数据）。', title=f['title'], summary=f['summary']),
                      'actions': ([{'label': _tr('起草：{get}', get=f.get('draft_label')), 'api': f"/api/ai/watch/findings/{f['id']}/draft", 'body': {}, 'feature': 'watch'}] if f.get('draft') else [])
                      + [{'label': _tr('让 AI 分析'), 'ask': _tr('分析这个值守发现：{title}。{summary}', title=f['title'], summary=f['summary']), 'feature': 'watch'},
                         {'label': _tr('知道了'), 'api': f"/api/ai/watch/findings/{f['id']}/ack", 'body': {}}]})
    return items


def register_feed():
    try:
        from .home_feed import register_feed_provider
    except ImportError:
        return False
    register_feed_provider(feed_provider, name='ai_watch', kind='ai', feature='watch', order=10)
    return True


# ---------------- 接口 ----------------
@router.get('/api/ai/watch/findings')
def api_findings(status: str = 'open', domain: str = '', limit: int = 50, user=Depends(current_user)):
    if _last_scan['t'] is None:
        scan()
    return {'items': list_findings(status, domain, max(1, min(200, limit))), 'domains': DOMAINS, 'last_scan': db.jsonable(_last_scan['t'])}


@router.post('/api/ai/watch/scan')
def api_scan(user=Depends(current_user)):
    return scan()


@router.post('/api/ai/watch/findings/{fid}/ack')
def api_ack(fid: int, body: dict = Body(default={}), user=Depends(current_user)):
    with db.engine.begin() as c:
        r = c.execute(sa.select(finding_t).where(finding_t.c.id == fid)).mappings().first()
        if not r:
            raise HTTPException(404, _tr('发现不存在'))
        c.execute(finding_t.update().where(finding_t.c.id == fid).values(status='acked', acked_by=user.get('name') or user['username'], acked_at=clock.now()))
        ops.audit(c, user['username'], 'ai_watch_ack', f'ai_finding:{fid}', {'title': r['title'], 'note': body.get('note')})
        r = c.execute(sa.select(finding_t).where(finding_t.c.id == fid)).mappings().first()
    return finding_out(r)


@router.post('/api/ai/watch/findings/{fid}/draft')
def api_draft(fid: int, user=Depends(current_user)):
    from . import ai_drafts
    with db.engine.connect() as c:
        r = c.execute(sa.select(finding_t).where(finding_t.c.id == fid)).mappings().first()
    if not r:
        raise HTTPException(404, _tr('发现不存在'))
    if not r['draft']:
        raise HTTPException(400, _tr('这条发现没有建议的动作'))
    if r['draft_id']:
        with db.engine.connect() as c:
            from ..schema import approval_t
            a = c.execute(sa.select(approval_t).where(approval_t.c.id == r['draft_id'])).mappings().first()
            if a and a['status'] == 'pending':
                return ai_drafts.draft_out(c, dict(a), user)
    try:
        d = ai_drafts.create(user, r['draft']['op'], r['draft'].get('payload') or {}, source='watch',
                             summary=_tr('AI 值守发现：{title}。{v}', title=r['title'], v=r['narrative'] or r['summary']), context=[{'kind': 'alert', 'label': r['title'], 'data': r['data']}])
    except ops.OpError as ex:
        raise HTTPException(400, str(ex))
    with db.engine.begin() as c:
        c.execute(finding_t.update().where(finding_t.c.id == fid).values(draft_id=d['id']))
    return d
