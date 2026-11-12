"""动作级节拍分析（5.0 M6）：从设备的动作 / 步号信号自动切出每个循环、每个动作的用时，对比标准时间和基线，找瓶颈工位 → 瓶颈动作，
看按天漂移（"夹具气缸伸出 7 天内从 0.6 s 增至 1.4 s"），把慢下来的秒数折算成每班 / 每天 / 每年少做的件数和钱。

数据：M1 的时序库（ts_point / ts_segment / ts_raw）。点位 key = 数据源:设备/点位（如 VF:CNC1/Actions/ClampValve），
属于哪台设备看 ts_point.equipment，或 key 以设备的"数据来源"（equipment.source，如 VF:CNC1）开头。

工位动作模型（表 cycle_model，一台设备一行，工程师确认后生效；AI 可以从观察到的信号顺序提议，但必须有人确认）：
  cycle   = {"mode": "signal", "point": "<循环进行中点位>"}          上升沿 = 循环开始，下降沿 = 循环结束
          | {"mode": "step", "point": "<步号点位>", "first": 10}     步号回到第一步 / 从 0 跳到非 0 / 变小（回绕）= 新循环
  actions = [{"key": "clamp_extend", "name": "夹具气缸伸出（夹紧）", "start": {"point": "…/ClampValve", "value": 1},
              "end": {"point": "…/ClampExtended", "value": 1}, "std_s": 1.1, "part": "clamp"}]
            动作用时 = 起始信号变成 value → 结束信号变成 value（同一点位 1→0 就是"开着的时长"，如主轴运转、射砂阀）
  baseline = {"<动作>": {"median", "p90", "n"}, "_cycle": {…}}       参考窗口里学到的基线
算法（_extract）：一次读出这台设备所有相关点位在窗口内的跳变 → 按循环信号 / 步号切循环 → 每个循环里，每个动作取
  "起始跳变 ≥ 循环开始" 的第一个，再取它之后第一个结束跳变 → 用时。毫秒级时间戳（采集保留毫秒）。

接口：
  GET  /api/cycle/lines                              有动作信号 / 动作模型的产线和设备
  GET  /api/cycle/line?line=&period=|hours=          全线：各工位节拍 vs 节拍目标、瓶颈工位 → 瓶颈动作、损失排行
  GET  /api/cycle/station/{设备id}?period=&days=&cycles=     单台：每个动作的用时 / 标准 / 基线、最近循环甘特、分布、按天漂移、损失
  GET  /api/cycle/models   GET|PUT /api/cycle/models/{设备id}
  POST /api/cycle/models/{设备id}/propose {hours}    从最近的信号自动识别动作（草稿，需确认）
  POST /api/cycle/models/{设备id}/confirm           确认（cycle.model.confirm）
  POST /api/cycle/models/{设备id}/baseline {start,end | hours}   学习基线
  POST /api/cycle/explain {equipment, action}        AI 解释为什么慢（演示模式回放录制；需要"节拍分析 AI"）
  POST /api/cycle/draft {equipment, action}          起草维修工单（动作草稿，审批后执行）
  GET  /api/cycle/alerts                             当前节拍告警（慢动作 / 漂移）
  GET|PUT /api/cycle/settings                        单件价值、年工作天数、每天工作小时、各产线节拍目标
值守：services/ai_watch 的观察者 equipment.cycle_drift（AI 值守 + 节拍分析 AI 都开着时），发现进工作台信息流。
"""
from __future__ import annotations

from ..i18n import _tr
import bisect
import json
import math
import re
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import audit, compat, db, perms
from ..db import clock
from ..schema import EQUIP_T, TABLES, cycle_daily_t, cycle_model_t, ts_point_t, ts_raw_t, ts_segment_t

router = APIRouter()
PERM_VIEW = perms.declare('cycle.view', '查看节拍分析', '设备管理')
PERM_EDIT = perms.declare('cycle.model.edit', '编辑工位动作模型（动作清单、标准时间）、节拍参数', '设备管理')
PERM_CONFIRM = perms.declare('cycle.model.confirm', '确认工位动作模型、学习基线', '设备管理')

TOL = 0.05                    # 起始跳变允许早于循环开始的秒数
POST_S = 5.0                  # 结束跳变最多晚于循环结束的秒数
RECENT_N = 10                 # 值守看最近几个循环
DEADBAND_S, DEADBAND_PCT = 0.05, 0.05      # 比参考慢不到 max(0.05 s, 5%) 的算正常抖动（标准时间本身也是从几十个循环估出来的）
SETTINGS_DEFAULT = {'value_per_part': 80.0, 'work_days': 300, 'hours_per_day': None, 'takt': {}}

PART_CN = {'door': '防护门', 'clamp': '夹具气缸', 'squeeze': '压实气缸', 'push': '推型气缸', 'gripper': '夹爪', 'spindle': '主轴', 'fill': '射砂',
           'robot': '机器人', 'cyl': '气缸', 'pusher': '推杆', 'lift': '升降气缸', 'stop': '阻挡气缸'}
DIR_CN = {'extend': '伸出', 'retract': '缩回', 'close': '关闭', 'open': '打开', 'run': '运转', 'home': '回原点'}
NAME_FIX = {('door', 'close'): '防护门关门', ('door', 'open'): '防护门开门', ('clamp', 'extend'): '夹具气缸伸出（夹紧）', ('clamp', 'retract'): '夹具气缸缩回（松开）',
            ('gripper', 'close'): '夹爪闭合', ('gripper', 'open'): '夹爪张开', ('spindle', 'run'): '主轴运转（加工）', ('fill', 'run'): '射砂',
            ('squeeze', 'extend'): '压实气缸压下', ('squeeze', 'retract'): '压实气缸回程', ('push', 'extend'): '推型气缸推出', ('push', 'retract'): '推型气缸回程'}
SENSOR_RX = re.compile(r'(?P<part>[A-Za-z]+?)(?P<dir>Extended|Retracted|Closed|Opened|AtHome|Home|Fwd|Back|Front)$')
CMD_RX = re.compile(r'(?P<part>[A-Za-z]+?)(Valve|Cmd|Command|Sol|Out)$', re.I)
DIR_OF = {'Extended': 'extend', 'Retracted': 'retract', 'Closed': 'close', 'Opened': 'open', 'AtHome': 'home', 'Home': 'home', 'Front': 'extend', 'Back': 'retract',
          'Fwd': 'extend'}
CAUSES = {
    'extend': '气缸伸出变慢常见原因：气缸 / 管路漏气（密封件磨损）、气源压力偏低、节流阀被调小或堵塞、电磁阀换向慢、导杆缺润滑或别劲。',
    'retract': '气缸缩回变慢常见原因：夹具卡滞（铁屑、毛刺卡在定位销 / 导轨）、回程排气不畅（消声器堵塞）、弹簧疲劳、气压偏低。',
    'close': '门 / 夹爪关闭变慢常见原因：导轨脏污或缺润滑、气缸漏气、气压偏低、门锁 / 到位开关松动（到位信号晚到）。',
    'open': '门 / 夹爪打开变慢常见原因：导轨脏污或缺润滑、排气不畅、气压偏低、到位开关位置偏移。',
    'run': '运转时间变长常见原因：程序 / 进给倍率被改、刀具磨损导致降速、余量变大、空走刀增加。',
}


# ───────────────────────── 设置 ─────────────────────────
def settings() -> dict:
    with db.engine.connect() as c:
        raw = db.setting(c, 'cycle_settings')
    s = json.loads(json.dumps(SETTINGS_DEFAULT))
    if raw:
        try:
            s.update({k: v for k, v in json.loads(raw).items() if k in SETTINGS_DEFAULT})
        except ValueError:
            pass
    if not s.get('hours_per_day'):
        from .ai_tools import shifts
        tot = 0.0
        for _c, _n, a, b in shifts():
            ha, hb = (int(x.split(':')[0]) + int(x.split(':')[1]) / 60 for x in (a, b))
            tot += (hb - ha) % 24 or 24
        s['hours_per_day'] = min(24.0, tot) or 16.0
    return s


def shifts_per_day() -> int:
    from .ai_tools import shifts
    return max(1, len(shifts()))


# ───────────────────────── 点位 ─────────────────────────
def station_points(c, eq: dict) -> list[dict]:
    """设备的时序点位：ts_point.equipment = 设备，或 key 以设备的数据来源（如 VF:CNC1）开头。"""
    t = ts_point_t
    conds = [t.c.equipment == eq['id']]
    if eq.get('source'):
        conds.append(t.c.key.like(eq['source'].replace('%', '') + '/%'))
    return [dict(r) for r in c.execute(sa.select(t).where(sa.or_(*conds)).order_by(t.c.key)).mappings()]


def _pids(c, keys):
    t = ts_point_t
    rows = c.execute(sa.select(t.c.id, t.c.key, t.c.discrete, t.c.dtype).where(t.c.key.in_(list(keys)))).all() if keys else []
    return {r.key: {'id': r.id, 'discrete': bool(r.discrete), 'dtype': r.dtype} for r in rows}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dt(v):
    return v if isinstance(v, datetime) or v is None else compat.to_dt(v)


def transitions(c, keys, s, e) -> dict:
    """{key: [(时间, 值)]}：每个点位在 [s, e] 内的跳变（值变成什么），时间升序。离散量读状态区间，数值量（步号）读原始值去重。"""
    info = _pids(c, keys)
    out = {k: [] for k in keys}
    disc = {v['id']: k for k, v in info.items() if v['discrete']}
    num = {v['id']: k for k, v in info.items() if not v['discrete']}
    st = ts_segment_t
    if disc:
        for pid, start, val, txt in c.execute(sa.select(st.c.point, st.c.start, st.c.value, st.c.text).where(
                st.c.point.in_(list(disc)), st.c.start >= s, st.c.start <= e).order_by(st.c.start)).all():
            out[disc[pid]].append((_dt(start), val if txt is None else txt))
    r = ts_raw_t
    for pid, k in num.items():
        prev = c.execute(sa.select(r.c.value).where(r.c.point == pid, r.c.ts < s).order_by(r.c.ts.desc()).limit(1)).scalar()
        for t_, v in c.execute(sa.select(r.c.ts, r.c.value).where(r.c.point == pid, r.c.ts >= s, r.c.ts <= e).order_by(r.c.ts)).all():
            if v != prev:
                out[k].append((_dt(t_), v))
                prev = v
    return out


def _eqv(a, b):
    fa, fb = _f(a), _f(b)
    if fa is not None and fb is not None:
        return abs(fa - fb) < 1e-9
    return str(a) == str(b)


def _times(trs, value):
    return [t for t, v in trs if _eqv(v, value)]


# ───────────────────────── 切循环 / 动作 ─────────────────────────
def _cycles(model, tr):
    cy = model.get('cycle') or {}
    seq = tr.get(cy.get('point')) or []
    out = []
    if cy.get('mode', 'signal') == 'signal':
        st = None
        for t, v in seq:
            on = (_f(v) or 0) != 0
            if on:
                st = t
            elif st is not None:
                out.append([st, t])
                st = None
    else:
        first = cy.get('first')
        st, prev = None, None
        for t, v in seq:
            fv = _f(v)
            if fv is None:
                continue
            new = (first is not None and _eqv(fv, first)) or (first is None and ((prev in (None, 0) and fv != 0) or (prev not in (None, 0) and fv != 0 and fv < prev)))
            if fv == 0 and st is not None:
                out.append([st, t])
                st = None
            elif new:
                if st is not None:
                    out.append([st, t])
                st = t
            prev = fv
    for i, cyc in enumerate(out):
        cyc.append((out[i + 1][0] - cyc[0]).total_seconds() if i + 1 < len(out) else None)
    return out


def extract(model, tr):
    """模型 + 跳变 → 循环列表 [{start, end, dur, interval, acts: {key: (相对循环开始的秒, 用时秒)}}]。"""
    cycles = _cycles(model, tr)
    idx = {}
    for a in model.get('actions') or []:
        for side in ('start', 'end'):
            p = a[side]
            k = (p['point'], json.dumps(p['value']))
            if k not in idx:
                idx[k] = _times(tr.get(p['point']) or [], p['value'])
    out = []
    for i, (cs, ce, iv) in enumerate(cycles):
        nxt = cycles[i + 1][0] if i + 1 < len(cycles) else None
        hi = ce + timedelta(seconds=POST_S)
        if nxt is not None:
            hi = min(hi, nxt)
        acts = {}
        for a in model.get('actions') or []:
            ts1 = idx[(a['start']['point'], json.dumps(a['start']['value']))]
            ts2 = idx[(a['end']['point'], json.dumps(a['end']['value']))]
            j = bisect.bisect_left(ts1, cs - timedelta(seconds=TOL))
            if j >= len(ts1) or ts1[j] > ce + timedelta(seconds=TOL):
                continue
            t1 = ts1[j]
            k = bisect.bisect_left(ts2, t1)
            if k < len(ts2) and ts2[k] == t1 and a['start']['point'] == a['end']['point']:
                k += 1
            if k >= len(ts2) or ts2[k] > hi:
                continue
            acts[a['key']] = (round((t1 - cs).total_seconds(), 3), round((ts2[k] - t1).total_seconds(), 3))
        out.append({'start': cs, 'end': ce, 'dur': round((ce - cs).total_seconds(), 3), 'interval': round(iv, 3) if iv is not None else None, 'acts': acts})
    return out


def model_keys(model):
    ks = {(model.get('cycle') or {}).get('point')}
    for a in model.get('actions') or []:
        ks.add(a['start']['point'])
        ks.add(a['end']['point'])
    return {k for k in ks if k}


def load_cycles(c, model, s, e):
    return extract(model, transitions(c, model_keys(model), s - timedelta(seconds=POST_S), e + timedelta(seconds=POST_S)))


# ───────────────────────── 统计 ─────────────────────────
def _pct(vals, q):
    if not vals:
        return None
    v = sorted(vals)
    return v[min(len(v) - 1, max(0, math.ceil(q * len(v)) - 1))]


def stat(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {'n': 0, 'median': None, 'p90': None, 'mean': None, 'min': None, 'max': None, 'sd': None}
    return {'n': len(vals), 'median': round(statistics.median(vals), 3), 'p90': round(_pct(vals, 0.9), 3), 'mean': round(sum(vals) / len(vals), 3),
            'min': round(min(vals), 3), 'max': round(max(vals), 3), 'sd': round(statistics.pstdev(vals), 3) if len(vals) > 1 else 0.0}


def action_stats(model, cycles):
    out = {}
    for a in model.get('actions') or []:
        durs = [cy['acts'][a['key']][1] for cy in cycles if a['key'] in cy['acts']]
        offs = [cy['acts'][a['key']][0] for cy in cycles if a['key'] in cy['acts']]
        st = stat(durs)
        st['offset'] = round(statistics.median(offs), 3) if offs else None
        out[a['key']] = st
    out['_cycle'] = stat([cy['dur'] for cy in cycles])
    out['_interval'] = stat([cy['interval'] for cy in cycles if cy['interval'] is not None])
    return out


def compare(model, st):
    """每个动作：当前中位数 vs 标准时间 / 基线 → 多出来的秒数（只算变慢的）。"""
    base = model.get('baseline') or {}
    rows = []
    cyc = (st.get('_cycle') or {}).get('median') or 0
    for a in model.get('actions') or []:
        s = st.get(a['key']) or {}
        med = s.get('median')
        b = (base.get(a['key']) or {}).get('median')
        std = a.get('std_s')
        ref = std if std else b
        over = (med - ref) if med is not None and ref else 0.0
        if over < max(DEADBAND_S, DEADBAND_PCT * (ref or 0)):        # 抖动范围内（±几十毫秒）不算变慢，免得损失排行全是噪声
            over = 0.0
        rows.append({'key': a['key'], 'name': a['name'], 'part': a.get('part') or '', 'dir': a.get('dir') or '', 'n': s.get('n', 0), 'median': med, 'p90': s.get('p90'),
                     'sd': s.get('sd'), 'offset': s.get('offset'), 'std_s': std, 'baseline': b, 'baseline_p90': (base.get(a['key']) or {}).get('p90'),
                     'excess_std': round(med - std, 3) if med is not None and std else None,
                     'excess_base': round(med - b, 3) if med is not None and b else None,
                     'excess': round(over, 3),
                     'share': round(med / cyc, 4) if med and cyc else None})
    return rows


def _model_out(r):
    if not r:
        return None
    d = {k: (db.jsonable(v) if not isinstance(v, (dict, list)) else v) for k, v in dict(r).items()}
    d['actions'] = d.get('actions') or []
    return d


def get_model(c, eid):
    r = c.execute(sa.select(cycle_model_t).where(cycle_model_t.c.equipment == eid)).mappings().first()
    return _model_out(r)


def all_models(c):
    return [_model_out(r) for r in c.execute(sa.select(cycle_model_t)).mappings()]


# ───────────────────────── 损失 ─────────────────────────
def losses(model, rows, st, cycles, s, e, n_parallel=1, bottleneck=False):
    """多出来的秒数 → 每班 / 每天 / 每年少做的件数和钱。
    产出口径：这台设备每循环多花 x 秒，在它忙的时候就少做 x / 循环间隔 件；只有在瓶颈工位上才真正影响整线产出（affects_output）。"""
    S = settings()
    ppc = float(model.get('parts_per_cycle') or 1)
    vpp = float(model.get('value_per_part') or S['value_per_part'] or 0)
    hours = max(1e-6, (e - s).total_seconds() / 3600)
    n = len(cycles)
    if cycles:
        span = max(1e-6, (cycles[-1]['end'] - cycles[0]['start']).total_seconds() / 3600)
        hours = max(hours if n < 3 else span, 1e-6)
    cph = n / hours if n else 0
    hpd = float(S['hours_per_day'] or 16)
    per_day = cph * hpd
    interval = (st.get('_interval') or {}).get('median') or (st.get('_cycle') or {}).get('median') or 0
    out = []
    for r in rows:
        x = r['excess'] or 0
        if x <= 0 or not interval:
            continue
        lost_s_day = x * per_day
        parts_day = per_day * ppc * x / interval
        out.append({'key': r['key'], 'name': r['name'], 'excess_s': round(x, 3), 'cycles_per_day': round(per_day, 1),
                    'lost_s_shift': round(lost_s_day / shifts_per_day(), 1), 'lost_s_day': round(lost_s_day, 1), 'lost_s_year': round(lost_s_day * S['work_days']),
                    'parts_shift': round(parts_day / shifts_per_day(), 2), 'parts_day': round(parts_day, 2), 'parts_year': round(parts_day * S['work_days'], 1),
                    'money_day': round(parts_day * vpp, 1), 'money_year': round(parts_day * vpp * S['work_days']), 'affects_output': bool(bottleneck)})
    out.sort(key=lambda x: -x['money_day'])
    tot = {k: round(sum(x[k] for x in out), 2) for k in ('excess_s', 'lost_s_day', 'parts_day', 'parts_year', 'money_day', 'money_year')}
    return {'items': out, 'total': tot, 'value_per_part': vpp, 'work_days': S['work_days'], 'hours_per_day': hpd, 'cycles_per_hour': round(cph, 2),
            'interval_s': interval, 'parts_per_cycle': ppc}


# ───────────────────────── 漂移 ─────────────────────────
def daily(c, model, eid, end: datetime, days=7):
    """最近 days 天每天每个动作的中位数（已过去的天存 cycle_daily，当天现算）。返回 {动作: [(日期, 中位数, n)]}。"""
    today = end.date()
    cd = cycle_daily_t
    have = defaultdict(dict)
    for r in c.execute(sa.select(cd).where(cd.c.equipment == eid, cd.c.day >= today - timedelta(days=days - 1), cd.c.day <= today)).mappings():
        d = r['day'] if isinstance(r['day'], date) else compat.to_dt(r['day']).date()
        have[d][r['action']] = r
    out = defaultdict(list)
    todo = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        if d in have and d != today and have[d]:
            continue
        todo.append(d)
    fresh = {}
    for d in todo:
        s0 = datetime(d.year, d.month, d.day)
        e0 = min(s0 + timedelta(days=1), end)
        cyc = load_cycles(c, model, s0, e0)
        st = action_stats(model, cyc)
        fresh[d] = st
    if fresh:
        rows = []
        for d, st in fresh.items():
            for k, v in st.items():
                if v.get('n'):
                    rows.append({'equipment': eid, 'day': d, 'action': k[:60], 'n': v['n'], 'median': v['median'], 'p90': v['p90'], 'mean': v['mean'],
                                 'vmin': v['min'], 'vmax': v['max'], 'computed_at': datetime.now().replace(microsecond=0)})
        if rows:
            with db.engine.begin() as w:
                compat.upsert(w, cd, rows, keys=['equipment', 'day', 'action'])
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        src = fresh.get(d)
        for a in (model.get('actions') or []) + [{'key': '_cycle'}]:
            k = a['key']
            if src is not None:
                v = src.get(k) or {}
                if v.get('n'):
                    out[k].append((d.isoformat(), v['median'], v['n']))
            elif k in have.get(d, {}):
                r = have[d][k]
                out[k].append((d.isoformat(), r['median'], r['n']))
    return dict(out)


def trend(series):
    """[(日期, 中位数, n)] → 斜率（秒/天）、首末值、是否在变坏。"""
    pts = [(i, m) for i, (_d, m, n) in enumerate(series) if m is not None and n >= 3]
    if len(pts) < 3:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs) or 1
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    syy = sum((y - my) ** 2 for y in ys) or 1e-12
    r = slope * math.sqrt(sxx / syy)
    first, last = ys[0], ys[-1]
    days = series[pts[-1][0]][0], series[pts[0][0]][0]
    return {'slope': round(slope, 4), 'r': round(r, 3), 'first': first, 'last': last, 'from': days[1], 'to': days[0], 'days': pts[-1][0] - pts[0][0] + 1,
            'worse': last - first >= max(0.2, 0.15 * first) and slope > 0 and r >= 0.6}


def drift_text(name, t):
    return _tr('{name} {days} 天内从 {first:.2f}s 增至 {last:.2f}s（每天 +{slope:.2f}s）', name=name, days=t['days'], first=t['first'], last=t['last'], slope=t['slope'])


# ───────────────────────── 报告 ─────────────────────────
def _eq(c, eid):
    r = db.get(c, 'equipment', eid)
    if not r:
        raise HTTPException(404, _tr('设备不存在'))
    return r


def _parallel(c, eq, models_by_eq):
    """同产线、同组（模型 grp，缺省设备类型）的设备台数（并联工位）。"""
    g = (models_by_eq.get(eq['id']) or {}).get('grp') or eq['etype']
    t = TABLES['equipment']
    peers = db.rows(c, 'equipment', t.c.line == eq['line']) if eq['line'] else [eq]
    return max(1, sum(1 for p in peers if ((models_by_eq.get(p['id']) or {}).get('grp') or p['etype']) == g and p['id'] in models_by_eq)), g


def station_report(eid, s, e, days=7, cycles=30, with_losses=True, keep=False):
    with db.engine.connect() as c:
        eq = _eq(c, eid)
        model = get_model(c, eid)
        if not model:
            pts = station_points(c, eq)
            return {'equipment': _eq_out(eq), 'model': None, 'has_actions': any('Actions/' in p['key'] or p['dtype'] == 'bool' for p in pts),
                    'points': len(pts), 'window': {'start': db.jsonable(s), 'end': db.jsonable(e)}}
        cyc = load_cycles(c, model, s, e)
        st = action_stats(model, cyc)
        rows = compare(model, st)
        models_by_eq = {m['equipment']: m for m in all_models(c)}
        npar, grp = _parallel(c, eq, models_by_eq)
        dly = daily(c, model, eid, e, days) if days else {}
    names = {a['key']: a['name'] for a in model['actions']}
    trends = {}
    for k, ser in dly.items():
        t = trend(ser)
        if t:
            t['text'] = drift_text(names.get(k, _tr('循环') if k == '_cycle' else k), t)
            trends[k] = t
    S = settings()
    takt = model.get('takt_s') or (S['takt'] or {}).get(str(eq['line']))
    eff = ((st['_cycle'] or {}).get('median') or 0) / npar / float(model.get('parts_per_cycle') or 1)
    out = {'equipment': _eq_out(eq), 'model': model, 'window': {'start': db.jsonable(s), 'end': db.jsonable(e)}, 'stats': st, 'actions': rows,
           'cycles_n': len(cyc), 'cycle': st['_cycle'], 'interval': st['_interval'], 'group': grp, 'parallel': npar, 'eff_cycle_s': round(eff, 3) if eff else None,
           'takt_s': takt, 'daily': {k: [{'day': d, 'median': m, 'n': n} for d, m, n in v] for k, v in dly.items()}, 'trends': trends,
           'recent': [{'start': db.jsonable(cy['start'])[11:19] + (f".{cy['start'].microsecond // 1000:03d}"), 'ts': _iso(cy['start']), 'dur': cy['dur'], 'interval': cy['interval'],
                       'acts': [{'key': k, 'off': v[0], 'dur': v[1]} for k, v in cy['acts'].items()]} for cy in cyc[-cycles:]] if cycles else [],
           'dist': _dist(model, cyc)}
    if with_losses:
        out['loss'] = losses(model, rows, st, cyc, s, e, npar)
    out['alerts'] = alerts_of(out)
    if keep:
        out['_cyc'] = cyc
    return out


def _iso(t):
    return t.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]


def _eq_out(eq):
    return {'id': eq['id'], 'code': eq['code'], 'name': eq['name'], 'type': EQUIP_T.get(eq['etype'], eq['etype']), 'etype': eq['etype'], 'line': eq['line'],
            'ideal_cycle_s': eq['ideal_cycle_s']}


def _dist(model, cyc, bins=16):
    out = {}
    for a in model.get('actions') or []:
        v = [cy['acts'][a['key']][1] for cy in cyc if a['key'] in cy['acts']]
        if not v:
            continue
        lo, hi = min(v), max(v)
        if hi - lo < 1e-6:
            out[a['key']] = {'x': [f'{lo:.2f}'], 'n': [len(v)]}
            continue
        w = (hi - lo) / bins
        cnt = [0] * bins
        for x in v:
            cnt[min(bins - 1, int((x - lo) / w))] += 1
        out[a['key']] = {'x': [f'{lo + w * (i + 0.5):.2f}' for i in range(bins)], 'n': cnt}
    return out


def alerts_of(rep, min_n=5):
    """慢动作（当前窗口 vs 基线 / 标准）+ 按天漂移 → 告警列表（按严重程度）。"""
    out = []
    for r in rep.get('actions') or []:
        ref = r['baseline'] or r['std_s']
        if not r['median'] or not ref or r['n'] < min_n:
            continue
        d = r['median'] - ref
        if d >= max(0.3, 0.2 * ref):
            out.append({'kind': 'slow', 'action': r['key'], 'name': r['name'], 'part': r['part'], 'dir': r['dir'], 'median': r['median'], 'ref': ref,
                        'ref_kind': _tr('基线') if r['baseline'] else _tr('标准'), 'delta': round(d, 3), 'ratio': round(r['median'] / ref, 2),
                        'severity': 'critical' if d >= max(1.0, 0.5 * ref) else 'warning',
                        'text': _tr('{name}：最近中位数 {median:.2f}s，{v} {ref:.2f}s，每循环慢 {d:.2f}s', name=r['name'], median=r['median'], v=_tr('基线') if r['baseline'] else _tr('标准'), ref=ref, d=d)})
    for k, t in (rep.get('trends') or {}).items():
        if k.startswith('_') or not t.get('worse'):
            continue
        r = next((x for x in rep['actions'] if x['key'] == k), {})
        out.append({'kind': 'drift', 'action': k, 'name': r.get('name', k), 'part': r.get('part'), 'dir': r.get('dir'), 'median': t['last'], 'ref': t['first'],
                    'delta': round(t['last'] - t['first'], 3), 'slope': t['slope'], 'severity': 'warning', 'text': t['text']})
    out.sort(key=lambda x: (x['severity'] != 'critical', -x['delta']))
    return out


def line_stations(c, line_id=None):
    """产线上有动作模型的设备（line_id=None：全部；按当前用户的数据范围过滤）。"""
    from .ai_tools import eq_scope_ids
    ids = eq_scope_ids()
    models = {m['equipment']: m for m in all_models(c)}
    t = TABLES['equipment']
    conds = [t.c.id.in_(list(models) or [0])]
    if line_id:
        conds.append(t.c.line == line_id)
    eqs = db.rows(c, 'equipment', *conds, order=t.c.code)
    return [x for x in eqs if ids is None or x['id'] in ids], models


def line_report(line_id, s, e):
    """全线：各工位（有动作模型的设备）的节拍 vs 节拍目标；瓶颈工位（并联折算后单件节拍最长）→ 瓶颈动作；损失排行。"""
    with db.engine.connect() as c:
        eqs, models = line_stations(c, line_id)
        line = db.get(c, 'line', line_id) if line_id else None
    S = settings()
    stations = []
    for eq in eqs:
        rep = station_report(eq['id'], s, e, days=0, cycles=0, with_losses=False, keep=True)
        if not rep.get('model'):
            continue
        stations.append(rep)
    if not stations:
        return {'line': {'id': line['id'], 'name': line['name']} if line else None, 'stations': [], 'bottleneck': None, 'losses': [], 'takt_s': None,
                'window': {'start': db.jsonable(s), 'end': db.jsonable(e)}}
    for r in stations:
        r['peer_median'] = None
    by_grp = defaultdict(list)
    for r in stations:
        by_grp[(r['equipment']['line'], r['group'])].append(r)
    for grp, rs in by_grp.items():
        meds = [x['cycle']['median'] for x in rs if x['cycle']['median']]
        for x in rs:
            x['peer_median'] = round(statistics.median(meds), 3) if meds else None
    active = [r for r in stations if r['cycles_n']]           # 窗口里没有循环的（停机 / 没数据）不参与找瓶颈
    if not active:
        return {'line': {'id': line['id'], 'name': line['name'], 'code': line['code']} if line else None, 'window': {'start': db.jsonable(s), 'end': db.jsonable(e)},
                'takt_s': None, 'bottleneck': None, 'losses': [], 'total_money_day': 0, 'settings': {k: S[k] for k in ('value_per_part', 'work_days', 'hours_per_day')},
                'stations': [{'equipment': r['equipment'], 'status': r['model']['status'], 'group': r['group'], 'parallel': r['parallel'], 'cycles_n': 0, 'cycle_s': None,
                              'cycle_p90': None, 'interval_s': None, 'eff_cycle_s': None, 'peer_median': None, 'std_cycle_s': r['model'].get('std_cycle_s'), 'takt_s': None,
                              'load': None, 'excess_s': 0, 'top_action': None, 'alerts': [], 'money_day': 0, 'bottleneck': False} for r in stations]}
    for r in stations:
        if not r['cycles_n']:
            r.pop('_cyc', None)
    stations = active
    bn = max(stations, key=lambda r: ((r['eff_cycle_s'] or 0), (r['cycle']['median'] or 0)))
    # 瓶颈组里最慢的那台
    grp = [x for x in stations if x['group'] == bn['group'] and x['equipment']['line'] == bn['equipment']['line']]
    bn = max(grp, key=lambda r: r['cycle']['median'] or 0)
    bn_grp_key = (bn['equipment']['line'], bn['group'])
    loss_rows = []
    for r in stations:
        in_bn = (r['equipment']['line'], r['group']) == bn_grp_key
        L = _loss_from_report(r, s, e, in_bn)
        r['loss'] = L
        for it in L['items']:
            loss_rows.append({'equipment': r['equipment']['code'], 'equipment_id': r['equipment']['id'], **it})
    loss_rows.sort(key=lambda x: (-x['affects_output'], -x['money_day']))
    worst = sorted(bn['actions'], key=lambda a: -(a['excess'] or 0))
    act = worst[0] if worst and (worst[0]['excess'] or 0) >= 0.1 else max(bn['actions'], key=lambda a: a['median'] or 0, default=None)
    takt = (S['takt'] or {}).get(str(line_id)) if line_id else None
    takt = takt or next((x['model'].get('takt_s') for x in stations if x['model'].get('takt_s')), None)
    out_st = []
    for r in sorted(stations, key=lambda r: -(r['eff_cycle_s'] or 0)):
        top = sorted(r['actions'], key=lambda a: -(a['excess'] or 0))
        out_st.append({'equipment': r['equipment'], 'status': r['model']['status'], 'group': r['group'], 'parallel': r['parallel'], 'cycles_n': r['cycles_n'],
                       'cycle_s': r['cycle']['median'], 'cycle_p90': r['cycle']['p90'], 'interval_s': r['interval']['median'], 'eff_cycle_s': r['eff_cycle_s'],
                       'peer_median': r['peer_median'], 'std_cycle_s': r['model'].get('std_cycle_s'), 'takt_s': r['takt_s'] or takt,
                       'load': round(r['eff_cycle_s'] / takt, 3) if takt and r['eff_cycle_s'] else None, 'excess_s': round(sum(a['excess'] or 0 for a in r['actions']), 3),
                       'top_action': {'key': top[0]['key'], 'name': top[0]['name'], 'excess': top[0]['excess'], 'median': top[0]['median'], 'ref': top[0]['std_s'] or top[0]['baseline']}
                       if top and (top[0]['excess'] or 0) > 0 else None,
                       'alerts': r['alerts'], 'money_day': r['loss']['total']['money_day'], 'bottleneck': r is bn})
    return {'line': {'id': line['id'], 'name': line['name'], 'code': line['code']} if line else None, 'window': {'start': db.jsonable(s), 'end': db.jsonable(e)},
            'takt_s': takt, 'stations': out_st,
            'bottleneck': {'equipment': bn['equipment'], 'group': bn['group'], 'parallel': bn['parallel'], 'cycle_s': bn['cycle']['median'], 'eff_cycle_s': bn['eff_cycle_s'],
                           'action': act, 'loss': bn['loss']},
            'losses': loss_rows[:30], 'total_money_day': round(sum(x['money_day'] for x in loss_rows if x['affects_output']), 1),
            'settings': {k: S[k] for k in ('value_per_part', 'work_days', 'hours_per_day')}}


def _loss_from_report(r, s, e, bottleneck):
    return losses(r['model'], r['actions'], r['stats'], r.pop('_cyc', None) or [], s, e, r['parallel'], bottleneck)


# ───────────────────────── 工具输出（MCP / 助手 / 技能） ─────────────────────────
def tool_out_line(r, lab):
    st = r['stations']
    if not st:
        return {'summary': _tr('{lab}：还没有工位动作模型（在"节拍分析"里先对有动作信号的设备做"自动识别动作"并确认）。', lab=lab), 'data': r, 'blocks': []}
    bn = r['bottleneck']
    if not bn:
        return {'summary': _tr('{lab}：{len} 个工位有动作模型，但这段时间没有完整的循环（设备停着或没有数据）。', lab=lab, len=len(st)), 'data': r, 'blocks': [], 'rows': [], 'count': 0}
    act = bn.get('action') or {}
    L = bn.get('loss') or {}
    top = next((x for x in (L.get('items') or []) if x['key'] == act.get('key')), None)
    blocks = [{'type': 'chart', 'kind': 'bar', 'title': _tr('{lab} 各工位单件节拍（并联折算）', lab=lab), 'x': [x['equipment']['code'] for x in st], 'unit': 's',
               'series': [{'name': _tr('单件节拍'), 'data': [x['eff_cycle_s'] for x in st]}] + ([{'name': _tr('节拍目标'), 'data': [r['takt_s']] * len(st)}] if r.get('takt_s') else [])}]
    if r['losses']:
        blocks.append({'type': 'table', 'title': _tr('慢动作损失排行'), 'columns': [{'key': 'equipment', 'label': _tr('设备')}, {'key': 'name', 'label': _tr('动作')},
                                                                         {'key': 'excess_s', 'label': _tr('每循环慢 s'), 'num': True}, {'key': 'parts_day', 'label': _tr('少做 件/天'), 'num': True},
                                                                         {'key': 'money_year', 'label': _tr('元/年'), 'num': True, 'format': 'int'}],
                       'rows': [{k: x[k] for k in ('equipment', 'name', 'excess_s', 'parts_day', 'money_year')} for x in r['losses'][:10]]})
    blocks.append({'type': 'callout', 'tone': 'warn' if top else 'info', 'title': _tr('瓶颈：{code}', code=bn['equipment']['code']) + (f" · {act.get('name')}" if act else ''),
                   'md': (_tr('每循环比标准慢 **{excess_s:.2f} s**，约 {parts_day:.1f} 件/天、{money_year:,.0f} 元/年。', excess_s=top['excess_s'], parts_day=top['parts_day'], money_year=top['money_year']) if top else
                          _tr('单件节拍 {eff_cycle_s:.1f} s（{parallel} 台并联）。', eff_cycle_s=bn['eff_cycle_s'], parallel=bn['parallel']))})
    summ = (_tr('{lab}：{len} 个工位有动作模型。瓶颈工位 {code}（{group} 组 {parallel} 台并联，单台循环 {cycle_s:.1f}s，折算单件 {eff_cycle_s:.1f}s', lab=lab, len=len(st), code=bn['equipment']['code'], group=bn['group'], parallel=bn['parallel'], cycle_s=bn['cycle_s'], eff_cycle_s=bn['eff_cycle_s']) + (_tr('，节拍目标 {takt_s:g}s', takt_s=r['takt_s']) if r.get('takt_s') else '') + _tr('）。')
            + (_tr('瓶颈动作：{get}，中位数 {get2:.2f}s', get=act.get('name'), get2=act.get('median')) + (_tr('，比标准 / 基线慢 {get:.2f}s', get=act.get('excess')) if act.get('excess') else '') + _tr('。') if act else '')
            + (_tr('损失：约 {lost_s_day:.0f} 秒/天、{parts_day:.1f} 件/天、{money_day:,.0f} 元/天、{money_year:,.0f} 元/年。', lost_s_day=top['lost_s_day'], parts_day=top['parts_day'], money_day=top['money_day'], money_year=top['money_year']) if top else '')
            + (_tr(' 其它慢动作：') + _tr('；').join(f"{x['equipment']} {x['name']} +{x['excess_s']:.2f}s" for x in r['losses'][1:5]) if len(r['losses']) > 1 else ''))
    return {'summary': summ, 'data': {k: r[k] for k in ('line', 'window', 'takt_s', 'bottleneck', 'losses', 'total_money_day')} | {'stations': st}, 'blocks': blocks,
            'rows': [{'equipment': x['equipment']['code'], 'cycle_s': x['cycle_s'], 'eff_cycle_s': x['eff_cycle_s'], 'excess_s': x['excess_s'],
                      'top_action': (x['top_action'] or {}).get('name')} for x in st], 'count': len(st)}


def tool_out_station(r, lab):
    eq = r['equipment']
    if not r.get('model'):
        return {'summary': _tr('{code} 还没有动作模型', code=eq['code']) + (_tr('（有动作信号，可以在"节拍分析"里自动识别动作）') if r.get('has_actions') else _tr('（没有找到动作信号）')), 'data': r}
    acts = r['actions']
    blocks = [{'type': 'chart', 'kind': 'hbar', 'title': _tr('{code} 各动作用时（中位数 vs 标准）', code=eq['code']), 'x': [a['name'] for a in acts], 'unit': 's',
               'series': [{'name': _tr('当前'), 'data': [a['median'] for a in acts]}, {'name': _tr('标准'), 'data': [a['std_s'] for a in acts]}]}]
    tr = {k: v for k, v in (r.get('trends') or {}).items() if not k.startswith('_') and v.get('worse')}
    if r.get('daily'):
        k0 = next(iter(tr), None) or (max(acts, key=lambda a: a['excess'] or 0)['key'] if acts else None)
        if k0 and r['daily'].get(k0):
            ser = r['daily'][k0]
            blocks.append({'type': 'chart', 'kind': 'line', 'title': _tr('{next} 每天中位数', next=next(a['name'] for a in acts if a['key'] == k0)), 'x': [x['day'][5:] for x in ser], 'unit': 's',
                           'series': [{'name': _tr('中位数'), 'data': [x['median'] for x in ser]}]})
    L = r.get('loss') or {}
    summ = (_tr('{code} {lab}：{cycles_n} 个循环，循环用时中位数 {v:.1f}s。各动作（中位数 / 标准 / 基线）：', code=eq['code'], lab=lab, cycles_n=r['cycles_n'], v=(r['cycle'] or {}).get('median') or 0)
            + _tr('；').join(f"{a['name']} {a['median'] if a['median'] is not None else '—'}s / {a['std_s'] or '—'} / {a['baseline'] or '—'}" for a in acts) + _tr('。')
            + (_tr(' 告警：') + _tr('；').join(x['text'] for x in r['alerts'][:4]) + _tr('。') if r.get('alerts') else '')
            + (_tr(' 漂移：') + _tr('；').join(v['text'] for v in tr.values()) + _tr('。') if tr else '')
            + (_tr(' 损失合计约 {parts_day:.1f} 件/天、{money_year:,.0f} 元/年。', parts_day=L['total']['parts_day'], money_year=L['total']['money_year']) if L.get('items') else ''))
    return {'summary': summ, 'data': {k: r.get(k) for k in ('equipment', 'window', 'cycles_n', 'cycle', 'actions', 'trends', 'alerts', 'loss', 'daily')}, 'blocks': blocks,
            'rows': acts, 'count': len(acts)}


# ───────────────────────── 自动识别动作（提议，需要确认） ─────────────────────────
def _split_name(name):
    base = name.split('/')[-1]
    m = SENSOR_RX.match(base)
    if m:
        return 'sensor', _camel_key(m.group('part')), DIR_OF.get(m.group('dir'), m.group('dir').lower())
    m = CMD_RX.match(base)
    if m:
        p = re.sub(r'(Close|Open)$', '', m.group('part'))
        return 'cmd', _camel_key(p), None
    return None, _camel_key(base), None


def _camel_key(s):
    return re.sub(r'(?<!^)(?=[A-Z])', '_', s).lower()


def _act_name(part, d, meaning=''):
    if (part, d) in NAME_FIX:
        return NAME_FIX[(part, d)]
    return f"{PART_CN.get(part, meaning or part)}{DIR_CN.get(d, d or '')}"


def propose(eid, hours=4.0, end=None, user=None):
    """从最近的信号推断动作模型（草稿）：
    1. 循环：有"循环进行中"（…CycleOn / 含"循环进行中"）就用它；否则有步号（…ProgramStep / 用途 step）就按步号回绕切。
    2. 动作：每个"指令"开关量（…Valve / …Cmd）的每次跳变，配上之后最先到位的"到位传感器"（…Extended / …Retracted / …Closed / …Opened）
       ——在 ≥ 80% 的循环里都是同一个传感器，就认定为一个动作；没有到位传感器的指令（射砂阀）和运转信号（…Run）按"开着的时长"算。
    3. 标准时间先取观察到的中位数（取 0.05 s 整），工程师确认 / 修改。
    返回 {model, evidence}。"""
    from .ai_tools import point_meta
    with db.engine.connect() as c:
        eq = _eq(c, eid)
        pts = station_points(c, eq)
        meta = point_meta(c)
        end = end or clock.now()
        last = max((p['last_ts'] for p in pts if p['last_ts']), default=None)
        if last and (end - last).total_seconds() > 600 and last < end:
            end = last + timedelta(seconds=1)
        s = end - timedelta(hours=hours)
        bools = [p for p in pts if p['dtype'] == 'bool' or (p['discrete'] and p['dtype'] != 'str')]
        mean = lambda p: (meta.get((p['equipment'], p['name'])) or {}).get('desc') or ''
        role = lambda p: (meta.get((p['equipment'], p['name'])) or {}).get('role') or ''
        cyc_pt = next((p for p in bools if p['name'].endswith('CycleOn') or '循环进行中' in mean(p) or 'cycle in progress' in mean(p).lower()), None)
        step_pt = next((p for p in pts if p['name'].endswith('ProgramStep') or role(p) == 'step' or '段号' in mean(p) or '程序步' in mean(p) or 'block no' in mean(p).lower() or 'program step' in mean(p).lower()), None)
        if not cyc_pt and not step_pt:
            raise HTTPException(400, _tr('{code} 没有找到循环信号（循环进行中 / 程序段号），不能自动识别动作', code=eq['code']))
        cycle = {'mode': 'signal', 'point': cyc_pt['key']} if cyc_pt else {'mode': 'step', 'point': step_pt['key']}
        keys = [p['key'] for p in bools] + ([step_pt['key']] if step_pt else [])
        tr = transitions(c, set(keys) | {cycle['point']}, s, end)
    cycles = _cycles({'cycle': cycle}, tr)
    if len(cycles) < 3:
        raise HTTPException(400, _tr('最近 {hours:g} 小时只看到 {len} 个完整循环，数据不够（至少 3 个）：让设备多跑一会儿再识别', hours=hours, len=len(cycles)))
    kinds = {p['key']: _split_name(p['name']) for p in bools}
    for p in bools:                      # 原始现场（点名看不懂）：按含义猜
        k = kinds[p['key']]
        if k[0] is None:
            m = mean(p)
            m = m + ' ' + m.lower()
            if '电磁阀' in m or '指令' in m or 'solenoid' in m or 'valve' in m or 'command' in m:
                kinds[p['key']] = ('cmd', k[1], None)
            elif '到位' in m or 'position' in m or 'extended' in m or 'retracted' in m:
                kinds[p['key']] = ('sensor', k[1], 'extend' if '伸出' in m or '夹紧' in m or 'extend' in m or 'clamped' in m else 'retract' if '缩回' in m or '松开' in m or 'retract' in m or 'unclamp' in m or 'released' in m else
                                   'close' if '关' in m or '闭合' in m or 'close' in m else 'open' if '开' in m or '张开' in m or 'open' in m else None)
    sensors = [k for k, v in kinds.items() if v[0] == 'sensor' and k != cycle['point']]
    cmds = [k for k, v in kinds.items() if v[0] == 'cmd']
    runs = [p['key'] for p in bools if p['name'].endswith('Run') or '运转' in mean(p) or 'running' in mean(p).lower()]
    acts, evidence = [], []
    nc = len(cycles)
    lo_hi = [(cs - timedelta(seconds=TOL), ce + timedelta(seconds=TOL)) for cs, ce, _ in cycles]

    def in_cycles(times):
        out = []
        for lo, hi in lo_hi:
            j = bisect.bisect_left(times, lo)
            out.append(times[j] if j < len(times) and times[j] <= hi else None)
        return out
    sens_rise = {k: _times(tr.get(k) or [], 1.0) for k in sensors}
    for ck in cmds:
        # 指令和到位传感器名字能对上部件（ClampValve ↔ ClampExtended / ClampRetracted）就只在同部件里找；对不上（原始现场）再按时间顺序在全部传感器里找
        same = [s for s in sensors if kinds[s][1] == kinds[ck][1] or kinds[s][1].startswith(kinds[ck][1]) or kinds[ck][1].startswith(kinds[s][1])]
        pool = {k: sens_rise[k] for k in (same or sensors)}
        if not same and kinds[ck][1] and _split_name(ck.split('/')[-1])[0] == 'cmd':
            pool = {}                                  # 名字认得出是"某部件的阀"，但没有这个部件的到位开关（射砂阀）：按开着的时长算
        all_cmd = sorted(t for t, _v in tr.get(ck) or [])
        for val in (1.0, 0.0):
            starts = in_cycles(_times(tr.get(ck) or [], val))
            if sum(1 for x in starts if x) < 0.8 * nc:
                continue
            votes = defaultdict(list)
            for (lo, hi), t1 in zip(lo_hi, starts):
                if not t1:
                    continue
                j0 = bisect.bisect_right(all_cmd, t1)
                nxt_cmd = all_cmd[j0] if j0 < len(all_cmd) else None          # 到位必须在这个指令下一次变化之前
                best = None
                for sk, times in pool.items():
                    j = bisect.bisect_left(times, t1)
                    if j < len(times) and times[j] <= hi + timedelta(seconds=POST_S) and (nxt_cmd is None or times[j] <= nxt_cmd) \
                            and (times[j] - t1).total_seconds() > 0.005 and (best is None or times[j] < best[1]):
                        best = (sk, times[j])
                if best:
                    votes[best[0]].append((best[1] - t1).total_seconds())
            if votes:
                sk, durs = max(votes.items(), key=lambda kv: len(kv[1]))
                if len(durs) >= 0.8 * nc:
                    _k, part, d = kinds[sk]
                    acts.append({'start': {'point': ck, 'value': val}, 'end': {'point': sk, 'value': 1.0}, 'part': part, 'dir': d, 'durs': durs})
                    continue
            # 没有到位传感器：指令开着的时长（射砂阀）
            if val == 1.0:
                offs = in_cycles(_times(tr.get(ck) or [], 0.0))
                durs = [(b - a).total_seconds() for a, b in zip(starts, offs) if a and b and b > a]
                if len(durs) >= 0.8 * nc:
                    acts.append({'start': {'point': ck, 'value': 1.0}, 'end': {'point': ck, 'value': 0.0}, 'part': kinds[ck][1], 'dir': 'run', 'durs': durs})
    for rk in runs:
        if rk in cmds:
            continue
        a_ = in_cycles(_times(tr.get(rk) or [], 1.0))
        times0 = _times(tr.get(rk) or [], 0.0)
        durs = []
        for t1 in a_:
            if not t1:
                continue
            j = bisect.bisect_right(times0, t1)
            if j < len(times0):
                durs.append((times0[j] - t1).total_seconds())
        if len(durs) >= 0.8 * nc:
            acts.append({'start': {'point': rk, 'value': 1.0}, 'end': {'point': rk, 'value': 0.0}, 'part': _split_name(rk.split('/')[-1])[1].replace('_run', ''), 'dir': 'run',
                         'durs': durs})
    # 去重：同一个 (part, dir) 留观察次数多的
    seen = {}
    for a in acts:
        k = (a['part'], a['dir'])
        if k not in seen or len(a['durs']) > len(seen[k]['durs']):
            seen[k] = a
    model_acts = []
    offs_by = {}
    tmp_model = {'cycle': cycle, 'actions': []}
    for (part, d), a in seen.items():
        key = f"{part}_{d}"
        med = statistics.median(a['durs'])
        tmp_model['actions'].append({'key': key, 'name': _act_name(part, d), 'start': a['start'], 'end': a['end'], 'std_s': round(round(med / 0.05) * 0.05, 2) or round(med, 3),
                                     'part': part, 'dir': d})
        evidence.append({'action': key, 'n': len(a['durs']), 'median': round(med, 3), 'p90': round(_pct(a['durs'], 0.9), 3), 'cycles': nc})
    ex = extract(tmp_model, tr)
    for a in tmp_model['actions']:
        offs = [cy['acts'][a['key']][0] for cy in ex if a['key'] in cy['acts']]
        offs_by[a['key']] = statistics.median(offs) if offs else 1e9
    model_acts = sorted(tmp_model['actions'], key=lambda a: offs_by[a['key']])
    st = action_stats({'cycle': cycle, 'actions': model_acts}, ex)
    model = {'name': f"{eq['code']} {eq['name']}", 'cycle': cycle, 'actions': model_acts, 'std_cycle_s': (st['_cycle'] or {}).get('median'),
             'grp': eq['etype'], 'source': 'ai'}
    return {'model': model, 'evidence': {'window': {'start': db.jsonable(s), 'end': db.jsonable(end)}, 'cycles': nc, 'actions': evidence,
                                         'points': len(pts), 'cycle_point': cycle['point']}}


# ───────────────────────── 保存 / 确认 / 基线 ─────────────────────────
def _who(user):
    return (user or {}).get('username') or 'system'


def save_model(eid, m: dict, user=None, status=None):
    now = datetime.now().replace(microsecond=0)
    vals = {k: m[k] for k in ('name', 'cycle', 'actions', 'std_cycle_s', 'takt_s', 'grp', 'value_per_part', 'note', 'source') if k in m}
    if 'actions' in vals:
        vals['actions'] = _clean_actions(vals['actions'])
    if 'cycle' in vals and (not isinstance(vals['cycle'], dict) or not vals['cycle'].get('point')):
        raise HTTPException(400, _tr('循环信号（cycle.point）必填'))
    with db.engine.begin() as c:
        cur = c.execute(sa.select(cycle_model_t).where(cycle_model_t.c.equipment == eid)).mappings().first()
        if cur:
            changed = any(k in vals and vals[k] != cur[k] for k in ('cycle', 'actions'))
            vals['version'] = (cur['version'] or 1) + (1 if changed else 0)
            if status:
                vals['status'] = status
            elif changed and cur['status'] == 'confirmed':
                vals['status'] = 'draft'                  # 改了动作清单要重新确认
            vals['updated_at'] = now
            c.execute(cycle_model_t.update().where(cycle_model_t.c.id == cur['id']).values(**vals))
            audit.audit(c, 'update', 'cycle_model', cur['id'], diff=audit.diff_of({k: cur[k] for k in vals}, vals), target=f'cycle_model:{eid}', user=user)
        else:
            vals.update(equipment=eid, status=status or 'draft', version=1, proposed_by=_who(user), created_at=now, updated_at=now)
            rid = c.execute(cycle_model_t.insert().values(**vals)).inserted_primary_key[0]
            audit.audit(c, 'create', 'cycle_model', rid, detail={'equipment': eid, 'actions': len(vals.get('actions') or []), 'source': vals.get('source')},
                        target=f'cycle_model:{eid}', user=user)
        c.execute(cycle_daily_t.delete().where(cycle_daily_t.c.equipment == eid))
        return get_model(c, eid)


def _clean_actions(acts):
    out, seen = [], set()
    for a in acts or []:
        if not isinstance(a, dict):
            continue
        key = re.sub(r'[^a-z0-9_]', '_', str(a.get('key') or '').lower())[:50]
        st, en = a.get('start') or {}, a.get('end') or {}
        if not key or key in seen or not st.get('point') or not en.get('point'):
            raise HTTPException(400, _tr('动作 {v}：key、起始信号、结束信号都要填，key 不能重复', v=a.get('name') or key or '?'))
        seen.add(key)
        std = _f(a.get('std_s'))
        out.append({'key': key, 'name': str(a.get('name') or key)[:60], 'start': {'point': str(st['point']), 'value': _f(st.get('value')) if _f(st.get('value')) is not None else st.get('value')},
                    'end': {'point': str(en['point']), 'value': _f(en.get('value')) if _f(en.get('value')) is not None else en.get('value')},
                    'std_s': round(std, 3) if std and std > 0 else None, 'part': str(a.get('part') or '')[:30], 'dir': str(a.get('dir') or '')[:20]})
    return out


def confirm(eid, user):
    with db.engine.begin() as c:
        cur = c.execute(sa.select(cycle_model_t).where(cycle_model_t.c.equipment == eid)).mappings().first()
        if not cur:
            raise HTTPException(404, _tr('这台设备还没有动作模型'))
        if not cur['actions']:
            raise HTTPException(400, _tr('动作清单是空的'))
        now = datetime.now().replace(microsecond=0)
        c.execute(cycle_model_t.update().where(cycle_model_t.c.id == cur['id']).values(status='confirmed', confirmed_by=_who(user), confirmed_at=now, updated_at=now))
        audit.audit(c, 'confirm', 'cycle_model', cur['id'], diff={'status': [cur['status'], 'confirmed']}, target=f'cycle_model:{eid}', user=user)
        return get_model(c, eid)


def learn_baseline(eid, s, e, user=None):
    with db.engine.connect() as c:
        m = get_model(c, eid)
        if not m:
            raise HTTPException(404, _tr('这台设备还没有动作模型'))
        cyc = load_cycles(c, m, s, e)
    if len(cyc) < 3:
        raise HTTPException(400, _tr('参考窗口里只有 {len} 个循环，至少要 3 个', len=len(cyc)))
    st = action_stats(m, cyc)
    base = {k: {'median': v['median'], 'p90': v['p90'], 'n': v['n']} for k, v in st.items() if v.get('n')}
    with db.engine.begin() as c:
        c.execute(cycle_model_t.update().where(cycle_model_t.c.equipment == eid).values(baseline=base, baseline_from=s, baseline_to=e,
                                                                                         updated_at=datetime.now().replace(microsecond=0)))
        audit.audit(c, 'baseline', 'cycle_model', m['id'], detail={'from': db.jsonable(s), 'to': db.jsonable(e), 'cycles': len(cyc)}, target=f'cycle_model:{eid}', user=user)
        return get_model(c, eid)


# ───────────────────────── AI 解释 / 起草维修 ─────────────────────────
def explain(eid, key, s, e):
    """为什么这个动作慢：规则（原因清单 + 数据）+ 大模型（演示模式回放录制，exact：同一种动作的录制可以复用到别的设备）。"""
    from . import ai_demo
    rep = station_report(eid, s, e, days=7, cycles=0)
    a = next((x for x in rep.get('actions') or [] if x['key'] == key), None)
    if not a:
        raise HTTPException(404, _tr('没有这个动作'))
    al = [x for x in rep['alerts'] if x['action'] == key]
    tr = (rep.get('trends') or {}).get(key)
    facts = {'设备': f"{rep['equipment']['code']} {rep['equipment']['name']}", '动作': a['name'], '当前中位数s': a['median'], 'P90s': a['p90'], '标准s': a['std_s'],
             '基线s': a['baseline'], '波动sd': a['sd'], '漂移': tr['text'] if tr else None, '告警': [x['text'] for x in al]}
    rule = CAUSES.get(a['dir'] or '', '动作变慢常见原因：气源压力、执行机构磨损或卡滞、到位开关位置偏移、程序参数被修改。')
    spread = (a['p90'] or 0) - (a['median'] or 0)
    hint = (_tr('用时波动大（P90 比中位数多 %.2fs），更像卡滞 / 间歇性问题；') % spread) if spread > 0.3 * (a['median'] or 1) else _tr('用时整体变长、波动不大，更像持续性的漏气 / 压力不足 / 节流；')
    if tr and tr.get('worse'):
        hint += _tr('按天逐渐变长，符合密封件磨损、漏气加剧的特征；')
    rule_text = _tr('{name}变慢。{hint}{rule}建议：先查气源压力和该气缸的管路 / 接头有无漏气声，再查节流阀开度和导向部位润滑。', name=a['name'], hint=hint, rule=rule)

    def live():
        from .ai import chat_once, llm_available
        if not llm_available():
            return None
        return chat_once(_tr('你是工厂设备工程师。下面是节拍分析发现的一个变慢的动作（JSON）。用 3~4 句中文说明：最可能的原因（按可能性排序，如漏气、卡滞、气压低、节流阀、润滑），怎么现场快速判断（看什么、听什么、测什么），先做什么。不要重复给出的具体数字，不要客套。\n\n') + json.dumps(facts, ensure_ascii=False, default=str))
    try:
        r = ai_demo.run('cycle', 'explain', {'action': key, 'dir': a['dir'] or '', 'kind': 'drift' if tr and tr.get('worse') else 'slow'}, live=live, fallback=lambda: None,
                        recordable=lambda r: bool(r), mark=lambda r: {'text': r, 'demo': True}, exact=True)
    except Exception:
        r = None
    if isinstance(r, dict) and r.get('demo'):
        return {'text': r['text'], 'by': 'demo', 'rule': rule_text, 'facts': facts}
    if isinstance(r, str) and r.strip():
        return {'text': r.strip(), 'by': 'ai', 'rule': rule_text, 'facts': facts}
    return {'text': rule_text, 'by': 'rule', 'rule': rule_text, 'facts': facts}


def draft_payload(rep, key):
    a = next((x for x in rep.get('actions') or [] if x['key'] == key), None)
    if not a:
        raise HTTPException(404, _tr('没有这个动作'))
    ref = a['baseline'] or a['std_s']
    eq = rep['equipment']
    L = next((x for x in ((rep.get('loss') or {}).get('items') or []) if x['key'] == key), None)
    fault = _tr('{name}变慢：{ref:.2f}s → {median:.2f}s（每循环 +{v:.2f}s）', name=a['name'], ref=ref, median=a['median'], v=a['median'] - ref) if ref and a['median'] else _tr('{name}变慢', name=a['name'])
    cause = (_tr('节拍分析发现：') + (CAUSES.get(a['dir'] or '', '') or _tr('动作变慢')) + (_tr(' 估计损失约 {parts_day:.1f} 件/天、{money_year:,.0f} 元/年。', parts_day=L['parts_day'], money_year=L['money_year']) if L else '')
             + _tr(' 建议检查：气源压力、气缸密封与管路接头、节流阀、导向润滑。'))
    return {'equipment': eq['code'], 'fault': fault[:190], 'level': 'urgent' if ref and a['median'] and a['median'] - ref >= max(1.0, 0.5 * ref) else 'normal', 'cause': cause}


# ───────────────────────── 值守（ai_watch 的观察者） ─────────────────────────
def recent_report(eid, now, n=RECENT_N, hours=8):
    """值守 / 告警用：只看最近 n 个循环（固定时间窗会把故障前的正常循环掺进中位数，刚开始变慢时看不出来）。"""
    rep = station_report(eid, now - timedelta(hours=hours), now, days=7, cycles=0, keep=True)
    cyc = (rep.pop('_cyc', None) or [])[-n:]
    if rep.get('model') and len(cyc) >= 5:
        m = rep['model']
        st = action_stats(m, cyc)
        rep.update(stats=st, actions=compare(m, st), cycles_n=len(cyc), cycle=st['_cycle'], interval=st['_interval'])
        rep['loss'] = losses(m, rep['actions'], st, cyc, cyc[0]['start'], cyc[-1]['end'], rep['parallel'])
        rep['alerts'] = alerts_of(rep)
    return rep


def w_cycle(c, now):
    """节拍值守：有基线 / 标准的动作模型，近 4 小时某动作明显变慢，或按天漂移在变坏 → 发现（建议起草维修工单）。"""
    from .ai_switch import enabled
    if not enabled('cycle'):
        return []
    out = []
    for m in all_models(c):
        if m['status'] != 'confirmed':
            continue
        try:
            rep = recent_report(m['equipment'], now)
        except Exception:
            continue
        for al in (rep.get('alerts') or [])[:2]:
            eq = rep['equipment']
            L = next((x for x in ((rep.get('loss') or {}).get('items') or []) if x['key'] == al['action']), None)
            ser = (rep.get('daily') or {}).get(al['action']) or []
            out.append({'key': f"equipment.cycle_drift:{eq['code']}:{al['action']}", 'watcher': 'equipment.cycle_drift', 'domain': 'equipment', 'subject': eq['code'],
                        'severity': al['severity'],
                        'title': _tr('{code} {name}变慢', code=eq['code'], name=al['name']) + (_tr('：{ref:.2f}s → {median:.2f}s', ref=al['ref'], median=al['median']) if al['kind'] == 'slow' else _tr('（{text}）', text=al['text'])),
                        'summary': al['text'] + _tr('。') + (_tr('按当前节奏约少做 {parts_day:.1f} 件/天、{money_year:,.0f} 元/年。', parts_day=L['parts_day'], money_year=L['money_year']) if L else '')
                                   + (CAUSES.get(al.get('dir') or '', '')[:60]),
                        'evidence': [{'label': _tr('动作'), 'value': al['name']}, {'label': _tr('最近中位数'), 'value': f"{al['median']:.2f}s"},
                                     {'label': al.get('ref_kind', '起点'), 'value': f"{al['ref']:.2f}s"},
                                     *([{'label': _tr('损失'), 'value': _tr('{money_year:,.0f} 元/年', money_year=L['money_year'])}] if L else []),
                                     {'label': _tr('节拍分析'), 'value': _tr('查看'), 'link': f"/cycle?eq={eq['id']}"}],
                        'blocks': [{'type': 'chart', 'kind': 'line', 'title': _tr('{name} 每天中位数', name=al['name']), 'x': [x['day'][5:] for x in ser], 'unit': 's',
                                    'series': [{'name': _tr('中位数'), 'data': [x['median'] for x in ser]}]}] if len(ser) >= 2 else [],
                        'draft': {'op': 'repair_order', 'payload': draft_payload(rep, al['action'])}, 'entity': 'equipment', 'entity_id': eq['id'],
                        'data': {'设备': eq['code'], '动作': al['name'], '中位数': al['median'], '参考': al['ref'], '慢s': al['delta']}})
    return out


def install_watcher():
    from . import ai_watch
    if not any(n == 'equipment.cycle_drift' for n, _ in ai_watch.WATCHERS):
        ai_watch.WATCHERS.append(('equipment.cycle_drift', w_cycle))


# ───────────────────────── 接口 ─────────────────────────
def _win(period: str = '', hours: float | None = None, start: str = '', end: str = '', default=8.0):
    from .ai_tools import ToolError, period as P
    try:
        return P({'period': period, 'hours': hours, 'start': start, 'end': end}, default)
    except ToolError as ex:
        raise HTTPException(400, str(ex))


def _check_eq(user, eid):
    ids = perms.allowed_ids(user, 'equipment')
    if ids is not None and eid not in ids:
        raise HTTPException(404, _tr('设备不存在'))


@router.get('/api/cycle/lines')
def api_lines(user=Depends(perms.require(PERM_VIEW))):
    from .ai_tools import eq_scope_ids
    ids = eq_scope_ids()
    with db.engine.connect() as c:
        models = {m['equipment']: m for m in all_models(c)}
        act = {r[0] for r in c.execute(sa.select(ts_point_t.c.equipment).where(ts_point_t.c.name.like('Actions/%')).distinct()) if r[0]}
        srcs = [r[0].split('/')[0] for r in c.execute(sa.select(ts_point_t.c.key).where(ts_point_t.c.name.like('Actions/CycleOn'))).all()]
        eqs = db.rows(c, 'equipment', order=TABLES['equipment'].c.code)
        lines = {r['id']: r for r in db.rows(c, 'line')}
    out = defaultdict(list)
    for e in eqs:
        if ids is not None and e['id'] not in ids:
            continue
        has = e['id'] in act or (e['source'] and e['source'] in srcs)
        if not has and e['id'] not in models:
            continue
        m = models.get(e['id'])
        out[e['line']].append({'id': e['id'], 'code': e['code'], 'name': e['name'], 'type': EQUIP_T.get(e['etype'], e['etype']), 'has_actions': has,
                               'model': {'status': m['status'], 'actions': len(m['actions']), 'baseline': bool(m.get('baseline')), 'version': m['version']} if m else None})
    return {'lines': [{'id': lid, 'name': (lines.get(lid) or {}).get('name') or _tr('未分配产线'), 'code': (lines.get(lid) or {}).get('code') or '', 'equipment': v}
                      for lid, v in out.items()], 'settings': settings()}


@router.get('/api/cycle/line')
def api_line(line: int | None = None, period: str = '', hours: float | None = None, start: str = '', end: str = '', user=Depends(perms.require(PERM_VIEW))):
    s, e, lab = _win(period, hours, start, end)
    r = line_report(line, s, e)
    r['label'] = lab
    return r


@router.get('/api/cycle/station/{eid}')
def api_station(eid: int, period: str = '', hours: float | None = None, start: str = '', end: str = '', days: int = 7, cycles: int = 30,
                user=Depends(perms.require(PERM_VIEW))):
    _check_eq(user, eid)
    s, e, lab = _win(period, hours, start, end)
    r = station_report(eid, s, e, days=max(0, min(60, days)), cycles=max(0, min(200, cycles)))
    r['label'] = lab
    return r


@router.get('/api/cycle/models')
def api_models(user=Depends(perms.require(PERM_VIEW))):
    with db.engine.connect() as c:
        ms = all_models(c)
        codes = {r.id: (r.code, r.name) for r in c.execute(sa.select(TABLES['equipment'].c.id, TABLES['equipment'].c.code, TABLES['equipment'].c.name))}
    return [{**m, 'equipment_code': codes.get(m['equipment'], ('', ''))[0], 'equipment_name': codes.get(m['equipment'], ('', ''))[1]} for m in ms]


@router.get('/api/cycle/models/{eid}')
def api_model(eid: int, user=Depends(perms.require(PERM_VIEW))):
    _check_eq(user, eid)
    with db.engine.connect() as c:
        eq = _eq(c, eid)
        m = get_model(c, eid)
        pts = station_points(c, eq)
    from .ai_tools import guess_role
    return {'model': m, 'equipment': _eq_out(eq), 'points': [{'key': p['key'], 'name': p['name'], 'dtype': p['dtype'], 'role': guess_role(p['name'])} for p in pts]}


@router.put('/api/cycle/models/{eid}')
def api_model_put(eid: int, body: dict = Body(...), user=Depends(perms.require(PERM_EDIT))):
    _check_eq(user, eid)
    with db.engine.connect() as c:
        _eq(c, eid)
    return save_model(eid, body, user)


@router.post('/api/cycle/models/{eid}/propose')
def api_propose(eid: int, body: dict = Body(default={}), user=Depends(perms.require(PERM_EDIT))):
    _check_eq(user, eid)
    end = db.parse_dt(body.get('end')) or _dt(body.get('end')) if body.get('end') else None
    r = propose(eid, float(body.get('hours') or 4), end, user)
    if body.get('save', True):
        with db.engine.connect() as c:
            cur = get_model(c, eid)
        if not cur or cur['status'] != 'confirmed' or body.get('replace'):
            r['saved'] = save_model(eid, {**r['model'], 'note': _tr('自动识别：{cycles} 个循环', cycles=r['evidence']['cycles'])}, user, status='draft')
    return r


@router.post('/api/cycle/models/{eid}/confirm')
def api_confirm(eid: int, user=Depends(perms.require(PERM_CONFIRM))):
    _check_eq(user, eid)
    return confirm(eid, user)


@router.post('/api/cycle/models/{eid}/baseline')
def api_baseline(eid: int, body: dict = Body(default={}), user=Depends(perms.require(PERM_CONFIRM))):
    _check_eq(user, eid)
    s, e, _lab = _win(body.get('period') or '', body.get('hours'), body.get('start') or '', body.get('end') or '', 24)
    return learn_baseline(eid, s, e, user)


@router.post('/api/cycle/explain')
def api_explain(body: dict = Body(...), user=Depends(perms.require(PERM_VIEW))):
    eid = int(body.get('equipment') or 0)
    _check_eq(user, eid)
    s, e, _lab = _win(body.get('period') or '', body.get('hours'), body.get('start') or '', body.get('end') or '')
    return explain(eid, str(body.get('action') or ''), s, e)


@router.post('/api/cycle/draft')
def api_draft(body: dict = Body(...), user=Depends(perms.require(PERM_VIEW))):
    from . import ai_drafts, ops
    eid = int(body.get('equipment') or 0)
    _check_eq(user, eid)
    s, e, _lab = _win(body.get('period') or '', body.get('hours'), body.get('start') or '', body.get('end') or '')
    rep = station_report(eid, s, e, days=7, cycles=0)
    p = draft_payload(rep, str(body.get('action') or ''))
    try:
        return ai_drafts.create(user, 'repair_order', p, source='cycle', summary=_tr('节拍分析：{fault}', fault=p['fault']),
                                context=[{'kind': 'alert', 'label': p['fault'], 'data': {'设备': p['equipment']}}])
    except ops.OpError as ex:
        raise HTTPException(400, str(ex))


@router.get('/api/cycle/alerts')
def api_alerts(period: str = '', hours: float | None = 4, user=Depends(perms.require(PERM_VIEW))):
    s, e, lab = _win(period, hours, '', '', 4)
    from .ai_tools import eq_scope_ids
    ids = eq_scope_ids()
    out = []
    with db.engine.connect() as c:
        ms = all_models(c)
    for m in ms:
        if ids is not None and m['equipment'] not in ids:
            continue
        rep = station_report(m['equipment'], s, e, days=7, cycles=0)
        for a in rep.get('alerts') or []:
            out.append({**a, 'equipment': rep['equipment']})
    out.sort(key=lambda x: (x['severity'] != 'critical', -x['delta']))
    return {'items': out, 'label': lab}


@router.get('/api/cycle/settings')
def api_settings(user=Depends(perms.require(PERM_VIEW))):
    return settings()


@router.put('/api/cycle/settings')
def api_settings_put(body: dict = Body(...), user=Depends(perms.require(PERM_EDIT))):
    cur = settings()
    for k in ('value_per_part', 'work_days', 'hours_per_day'):
        if k in body and body[k] not in (None, ''):
            v = _f(body[k])
            if v is None or v < 0:
                raise HTTPException(400, _tr('{k} 要是非负数', k=k))
            cur[k] = v
    if isinstance(body.get('takt'), dict):
        cur['takt'] = {str(k): _f(v) for k, v in body['takt'].items() if _f(v)}
    with db.engine.begin() as c:
        db.set_setting(c, 'cycle_settings', json.dumps(cur, ensure_ascii=False), _tr('节拍分析参数（单件价值、年工作天数、每天工作小时、各产线节拍目标）'))
    return settings()
