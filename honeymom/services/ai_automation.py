"""一句话自动化：用户用一句话描述规则（"主轴负载连续 10 分钟 > 90% 就提醒班长并建议降速"），
AI 把它变成可编辑的流程（触发 → 条件 → 动作），启用前先用最近 N 天的数据"历史回放"，看它会在什么时候触发、触发几次。

流程（flow）JSON：
  {"name": "主轴负载持续过高",
   "trigger": {"type": "metric", "metric": "spindle_load", "equipment": ["CNC3"] | "*", "op": ">", "value": 90, "for_min": 10}
            | {"type": "alarm", "match": "主轴", "equipment": "*"}                      报警（内容包含）
            | {"type": "down", "equipment": "*", "for_min": 15}                        停机超过 N 分钟
            | {"type": "ncr", "match": "气孔", "count": 3, "within_min": 120},          N 小时内同类不合格品达到 M 条
   "conditions": [{"type": "time", "from": "08:00", "to": "20:00"}],                    可选：只在某个时段
   "actions": [{"type": "notify", "to_role": "leader", "title": "…{equipment}…", "content": "…", "level": "warn"},
               {"type": "suggest", "text": "建议把进给倍率降到 90%"},                     建议（写进通知里）
               {"type": "draft", "op": "repair_order" | "andon_raise" | "param_adjust" | "schedule_change", "payload": {…}},   起草动作（需要人审批）
               {"type": "agent", "agent_id": 3}],                                      运行一个已发布的智能体（复用智能体编排）
   "cooldown_min": 30}
动作文字里可以用 {equipment} {value} {threshold} {duration} {time} {metric}。高风险动作一律起草成审批草稿，不会直接执行。

接口（需要 AI 功能"自动化"）：
  POST /api/ai/automations/parse {text}             一句话 → 流程（大模型；演示模式回放录制；都没有时用规则解析）
  POST /api/ai/automations/replay {flow, days}      历史回放：什么时候会触发、各设备次数、时间分布
  GET/POST /api/ai/automations                      列表 / 保存（status: active 启用 | paused 暂停）
  PUT /api/ai/automations/{id}  POST /api/ai/automations/{id}/status {status}  DELETE /api/ai/automations/{id}
  GET /api/ai/automations/{id}/fires                触发记录
后台每 30 秒按数据时钟检查启用的规则（AI 总开关和"自动化"都开着时）。
"""
from ..i18n import _tr
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import auth, db
from ..auth import current_user
from ..db import clock
from ..schema import ROLES, TABLES, metadata
from . import ai_demo, ops

router = APIRouter()

from ..schema import ai_automation_fire_t as fire_t, ai_automation_t as auto_t  # noqa: E402  (表定义在 schema.py)

METRICS = {   # key: (名称, 点位名, 单位, 设备类型提示, 同义词)
    'spindle_load': ('主轴负载', 'Monitoring/Spindle/Load', '%', 'cnc', ('主轴负载', '主轴载荷', '负载', 'spindle')),
    'vibration': ('振动', 'Monitoring/Vibration/RMS', 'mm/s', 'cnc', ('振动', 'vibration')),
    'bath_temp': ('铁水温度', 'Bath/Temperature', '°C', 'furnace', ('铁水温度', '炉温', '铁水', '熔炼温度')),
    'sand_moisture': ('型砂水分', 'Sand/Moisture', '%', None, ('型砂水分', '水分', '砂水分')),
    'compactability': ('紧实率', 'Sand/Compactability', '%', None, ('紧实率',)),
    'bore_dia': ('缸孔直径', 'Quality/BoreDiameter', 'mm', None, ('缸孔', '孔径', '直径')),
    'brush_wear': ('毛刷磨损', 'Brush/Wear', '%', 'deburr', ('毛刷', '刷子')),
    'cooling_temp': ('冷却出水温度', 'Cooling/OutletTemp', '°C', 'furnace', ('冷却水', '出水温度', '冷却')),
    'earth_leakage': ('炉衬漏电流', 'Lining/EarthLeakage', 'mA', 'furnace', ('漏电', '炉衬')),
    'residual_mg': ('残余镁', 'Ladle/ResidualMg', '%', None, ('残镁', '残余镁', '镁')),
}
TRIGGERS = {'metric': '指标超限', 'alarm': '设备报警', 'down': '设备停机', 'ncr': '不合格品聚集'}
ACTIONS = {'notify': '发通知', 'suggest': '给出建议', 'draft': '起草动作（需审批）', 'agent': '运行智能体', 'skill': '运行技能'}   # skill：5.0 M6（services/skills.py）
OPS_TXT = {'>': '>', '>=': '≥', '<': '<', '<=': '≤'}
ROLE_WORDS = {'班长': 'leader', '班组长': 'leader', '组长': 'leader', '设备员': 'maint', '维修': 'maint', '质检': 'qc', '质量': 'qc', '计划': 'planner',
              '厂长': 'manager', '经理': 'manager', '仓管': 'warehouse', '工艺': 'engineer', '操作工': 'operator', '管理员': 'admin'}


# ---------------- 流程规范化 ----------------
def _f(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def normalize_flow(f):
    """模型或界面给的流程 → 规范化（缺的补默认，错的抛 HTTPException 400）。"""
    if not isinstance(f, dict):
        raise HTTPException(400, _tr('流程格式不对'))
    tr = dict(f.get('trigger') or {})
    tt = tr.get('type') if tr.get('type') in TRIGGERS else 'metric'
    eq = tr.get('equipment') or '*'
    if isinstance(eq, str) and eq != '*':
        eq = [x for x in re.split(r'[,，、\s]+', eq) if x]
    trig = {'type': tt, 'equipment': eq}
    if tt == 'metric':
        m = tr.get('metric') if tr.get('metric') in METRICS else 'spindle_load'
        trig.update(metric=m, op=tr.get('op') if tr.get('op') in OPS_TXT else '>', value=_f(tr.get('value'), 90.0), for_min=max(0.0, _f(tr.get('for_min'), 0) or 0))
    elif tt == 'alarm':
        trig['match'] = str(tr.get('match') or '')[:60]
    elif tt == 'down':
        trig['for_min'] = max(1.0, _f(tr.get('for_min'), 15) or 15)
    elif tt == 'ncr':
        trig.update(match=str(tr.get('match') or '')[:60], count=max(1, int(_f(tr.get('count'), 3) or 3)), within_min=max(10, int(_f(tr.get('within_min'), 120) or 120)))
    conds = []
    for cd in f.get('conditions') or []:
        if isinstance(cd, dict) and cd.get('type') == 'time' and re.fullmatch(r'\d{1,2}:\d{2}', str(cd.get('from', ''))) and re.fullmatch(r'\d{1,2}:\d{2}', str(cd.get('to', ''))):
            conds.append({'type': 'time', 'from': cd['from'], 'to': cd['to']})
    acts = []
    for a in f.get('actions') or []:
        if not isinstance(a, dict) or a.get('type') not in ACTIONS:
            continue
        if a['type'] == 'notify':
            role = a.get('to_role') if a.get('to_role') in ROLES else ROLE_WORDS.get(str(a.get('to_role') or ''), 'leader')
            acts.append({'type': 'notify', 'to_role': role, 'title': str(a.get('title') or _tr('{equipment} {metric}异常'))[:120], 'content': str(a.get('content') or '')[:600],
                         'level': a.get('level') if a.get('level') in ('info', 'warn', 'urgent') else 'warn'})
        elif a['type'] == 'suggest':
            if a.get('text'):
                acts.append({'type': 'suggest', 'text': str(a['text'])[:300]})
        elif a['type'] == 'draft':
            if a.get('op') in ('repair_order', 'andon_raise', 'param_adjust', 'schedule_change', 'ncr', 'material_request'):
                acts.append({'type': 'draft', 'op': a['op'], 'payload': a.get('payload') if isinstance(a.get('payload'), dict) else {}})
        elif a['type'] == 'agent' and str(a.get('agent_id') or '').isdigit():
            acts.append({'type': 'agent', 'agent_id': int(a['agent_id'])})
        elif a['type'] == 'skill' and str(a.get('skill_id') or '').isdigit():
            acts.append({'type': 'skill', 'skill_id': int(a['skill_id']), 'params': a.get('params') if isinstance(a.get('params'), dict) else {}})
    if not acts:
        acts = [{'type': 'notify', 'to_role': 'leader', 'title': _tr('{equipment} {metric}异常'), 'content': '', 'level': 'warn'}]
    name = str(f.get('name') or '').strip()[:60] or describe_trigger(trig)
    return {'name': name, 'trigger': trig, 'conditions': conds, 'actions': acts, 'cooldown_min': max(1, int(_f(f.get('cooldown_min'), 30) or 30))}


def describe_trigger(t):
    eq = _tr('所有设备') if t.get('equipment') in ('*', None, []) else _tr('、').join(t['equipment'])
    if t['type'] == 'metric':
        m = METRICS[t['metric']]
        return f"{eq} {m[0]}{_tr(' 连续 %g 分钟') % t['for_min'] if t.get('for_min') else ''} {OPS_TXT[t['op']]} {t['value']:g}{m[2]}"
    if t['type'] == 'alarm':
        return _tr('{eq} 出现报警', eq=eq) + (_tr('「{match}」', match=t['match']) if t.get('match') else '')
    if t['type'] == 'down':
        return _tr('{eq} 停机超过 {for_min:g} 分钟', eq=eq, for_min=t['for_min'])
    return _tr('{v}{v2}内{v3}不合格品达到 {count} 条', v=t['within_min'] // 60 if t['within_min'] >= 60 else t['within_min'], v2=_tr('小时') if t['within_min'] >= 60 else _tr('分钟'), v3=_tr('「') + t['match'] + _tr('」') if t.get('match') else '', count=t['count'])


def describe(flow):
    acts = []
    for a in flow['actions']:
        if a['type'] == 'notify':
            acts.append(_tr('通知{get}', get=ROLES.get(a['to_role'], a['to_role'])))
        elif a['type'] == 'suggest':
            acts.append(_tr('建议：{text}', text=a['text']))
        elif a['type'] == 'draft':
            acts.append(_tr('起草「{get}」等人审批', get=ops.OPS.get(a['op'], {}).get('label', a['op'])))
        elif a['type'] == 'agent':
            acts.append(_tr('运行智能体 #{agent_id}', agent_id=a['agent_id']))
        elif a['type'] == 'skill':
            acts.append(_tr('运行技能 #{skill_id}', skill_id=a['skill_id']))
    cond = ''.join(_tr('（仅 {v}–{to}）', v=c['from'], to=c['to']) for c in flow['conditions'])
    return _tr('当 {describe_trigger}{cond} 时：{join}（同一设备 {cooldown_min} 分钟内不重复）', describe_trigger=describe_trigger(flow['trigger']), cond=cond, join=_tr('；').join(acts), cooldown_min=flow['cooldown_min'])


# ---------------- 一句话 → 流程 ----------------
def rule_parse(text):
    """不用大模型的解析：认识常见说法（指标 + 比较 + 持续时间 + 通知谁 + 建议 / 报修 / 安灯）。"""
    s = text or ''
    trig = {'type': 'metric', 'equipment': '*'}
    eqs = re.findall(r'\b([A-Z]{2,6}\d{1,2})\b', s.upper())
    if eqs:
        trig['equipment'] = sorted(set(eqs))
    metric = next((k for k, v in METRICS.items() if any(w in s for w in v[4])), None)
    if '报警' in s and not metric:
        m = re.search(r'报警[「“"]?([^」”"，,。]{1,12})', s)
        trig = {'type': 'alarm', 'equipment': trig['equipment'], 'match': (m.group(1).strip() if m and not re.match(r'^(就|时|了|，)', m.group(1)) else '')}
    elif '停机' in s and not metric:
        m = re.search(r'(\d+(?:\.\d+)?)\s*分钟', s)
        trig = {'type': 'down', 'equipment': trig['equipment'], 'for_min': float(m.group(1)) if m else 15}
    elif ('不合格' in s or 'NCR' in s.upper() or '废品' in s) and not metric:
        m = re.search(r'(\d+)\s*(?:条|个|件)', s)
        h = re.search(r'(\d+(?:\.\d+)?)\s*(小时|分钟)', s)
        mt = re.search(r'(气孔|缩松|冷隔|毛刷|毛刺|尺寸|球化|砂眼)', s)
        trig = {'type': 'ncr', 'match': mt.group(1) if mt else '', 'count': int(m.group(1)) if m else 3,
                'within_min': int(float(h.group(1)) * (60 if h.group(2) == '小时' else 1)) if h else 120}
    else:
        trig['metric'] = metric or 'spindle_load'
        m = re.search(r'(>=|<=|≥|≤|>|<|大于等于|小于等于|大于|小于|超过|高于|低于|不足|不到)\s*(\d+(?:\.\d+)?)', s)
        if m:
            w = m.group(1)
            trig['op'] = {'≥': '>=', '≤': '<=', '大于等于': '>=', '小于等于': '<=', '大于': '>', '超过': '>', '高于': '>', '小于': '<', '低于': '<', '不足': '<', '不到': '<'}.get(w, w)
            trig['value'] = float(m.group(2))
        else:
            n = re.search(r'(\d+(?:\.\d+)?)\s*%', s)
            trig['op'], trig['value'] = '>', float(n.group(1)) if n else 90.0
        d = re.search(r'(?:连续|持续|超过)?\s*(\d+(?:\.\d+)?)\s*分钟', s)
        trig['for_min'] = float(d.group(1)) if d and d.start() < (m.start() if m else len(s)) + 40 else 0
    acts = []
    roles = [r for w, r in ROLE_WORDS.items() if w in s and ('提醒' in s or '通知' in s or '告诉' in s)]
    for r in dict.fromkeys(roles):
        acts.append({'type': 'notify', 'to_role': r, 'title': '{equipment} ' + ('{metric} {value}' if trig['type'] == 'metric' else '{metric}'), 'level': 'warn',
                     'content': describe_trigger(normalize_flow({'trigger': trig, 'actions': [{'type': 'suggest', 'text': 'x'}]})['trigger'])})
    if not acts and ('提醒' in s or '通知' in s):
        acts.append({'type': 'notify', 'to_role': 'leader', 'title': _tr('{equipment} {metric}异常'), 'level': 'warn'})
    sm = re.search(r'建议([^，,。；;]{2,30})', s)
    if sm:
        acts.append({'type': 'suggest', 'text': sm.group(1).strip()})
    if '降速' in s and not sm:
        acts.append({'type': 'suggest', 'text': _tr('降低进给 / 主轴转速约 10%，并检查刀具磨损')})
    if '报修' in s or '派维修' in s or '维修单' in s:
        acts.append({'type': 'draft', 'op': 'repair_order', 'payload': {'equipment': '{equipment}', 'fault': _tr('{metric}异常：{value}（规则：{threshold}）'), 'level': 'normal'}})
    if '安灯' in s:
        acts.append({'type': 'draft', 'op': 'andon_raise', 'payload': {'atype': 'equipment', 'equipment': '{equipment}', 'description': _tr('{equipment} {metric}异常（自动化规则）')}})
    if '换刀' in s or '寿命' in s:
        acts.append({'type': 'suggest', 'text': _tr('检查当前刀具磨损，必要时提前换刀')})
    name = re.sub(r'[，,。].*$', '', s)[:24] or None
    return normalize_flow({'name': name, 'trigger': trig, 'actions': acts or [{'type': 'notify', 'to_role': 'leader'}]})


def llm_parse(text):
    from .ai import llm_available, llm_chat
    if not llm_available():
        return None
    cat = _tr('；').join(_tr('{k}={v}（{v2}）', k=k, v=v[0], v2=v[2]) for k, v in METRICS.items())
    with db.engine.connect() as c:
        eqs = ', '.join(f"{r[0]}({r[1]})" for r in c.execute(sa.select(TABLES['equipment'].c.code, TABLES['equipment'].c.name)).all()[:40])
    sysmsg = (_tr('你把工厂人员的一句话规则翻译成 MES 自动化流程 JSON。只输出 JSON 对象，字段：\nname（10 字以内的名字）, trigger, conditions, actions, cooldown_min。\ntrigger 四选一：{{"type":"metric","metric":<指标 key>,"equipment":"*"或["CNC3"],"op":">"|">="|"<"|"<=","value":数,"for_min":持续分钟}}；{{"type":"alarm","match":"报警内容关键字","equipment":"*"}}；{{"type":"down","equipment":"*","for_min":分钟}}；{{"type":"ncr","match":"缺陷关键字","count":条数,"within_min":分钟}}。\n指标 key：{cat}。设备：{eqs}。\nconditions 可选：[{{"type":"time","from":"08:00","to":"20:00"}}]。\nactions 数组，元素：{{"type":"notify","to_role":"leader|maint|qc|planner|manager|engineer|warehouse|operator","title":"…","content":"…","level":"info|warn|urgent"}}；{{"type":"suggest","text":"给现场的具体建议"}}；{{"type":"draft","op":"repair_order|andon_raise|param_adjust|schedule_change","payload":{{…}}}}（写操作只能起草，等人审批）。文字里可用占位符 {{equipment}} {{value}} {{threshold}} {{duration}} {{metric}}。班长=leader，设备员=maint，质检=qc。', cat=cat, eqs=eqs))
    m, _ = llm_chat([{'role': 'system', 'content': sysmsg}, {'role': 'user', 'content': text}], json_mode=True)
    txt = m.get('content') or ''
    try:
        j = json.loads(txt[txt.find('{'):txt.rfind('}') + 1])
    except ValueError:
        return None
    return normalize_flow(j)


def parse(text):
    def live():
        f = None
        try:
            f = llm_parse(text)
        except Exception:
            f = None
        return {'flow': f, 'by': 'ai'} if f else {'flow': rule_parse(text), 'by': 'rule'}
    r = ai_demo.run('automation', 'parse', {'text': text}, live=live, fallback=lambda: {'flow': rule_parse(text), 'by': 'rule'},
                    recordable=lambda r: r.get('by') == 'ai', exact=True)
    flow = normalize_flow(r['flow'])
    return {'flow': flow, 'by': 'demo' if r.get('demo') else r.get('by'), 'summary': describe(flow)}


# ---------------- 回放 / 求值 ----------------
def _eq_ids(c, equipment):
    et = TABLES['equipment']
    rows = c.execute(sa.select(et.c.id, et.c.code)).all()
    if equipment in ('*', None, []):
        return {r[0]: r[1] for r in rows}
    want = {str(x).upper() for x in equipment}
    return {r[0]: r[1] for r in rows if r[1].upper() in want}


def _in_time(t, conds):
    for cd in conds:
        if cd['type'] == 'time':
            hm = t.strftime('%H:%M')
            a, b = cd['from'].zfill(5), cd['to'].zfill(5)
            if not (a <= hm < b if a <= b else hm >= a or hm < b):
                return False
    return True


CMP = {'>': lambda a, b: a > b, '>=': lambda a, b: a >= b, '<': lambda a, b: a < b, '<=': lambda a, b: a <= b}


def detect(c, flow, start, end):
    """在 [start, end) 的数据上找出规则会触发的时刻（考虑持续时间和冷却）。返回 [{t, equipment, value, duration_min, detail}] 和扫描的数据量。"""
    tr, conds, cool = flow['trigger'], flow['conditions'], timedelta(minutes=flow['cooldown_min'])
    fires, scanned = [], 0
    ids = _eq_ids(c, tr.get('equipment'))
    if tr['type'] == 'metric':
        name = METRICS[tr['metric']][1]
        ts = TABLES.get('tag_sample') if 'tag_sample' in TABLES else metadata.tables['tag_sample']
        q = sa.select(ts.c.equipment, ts.c.ts, ts.c.num, ts.c.name).where((ts.c.ts >= start - timedelta(minutes=tr['for_min'] + 2)) & (ts.c.ts < end)
                                                                & ((ts.c.name == name) | ts.c.name.like(name + '@%')))
        if tr.get('equipment') not in ('*', None, []):
            q = q.where(ts.c.equipment.in_(list(ids)))
        # 按"设备 + 点位"分开看：一台机床有多把刀的点（Load@T01、Load@T02…）时，交错在一起会把连续超限打断
        by = defaultdict(list)
        for eid, t, v, nm in c.execute(q.order_by(ts.c.equipment, ts.c.name, ts.c.ts)).all():
            if v is not None:
                by[(eid, nm)].append((t, v))
        scanned = sum(len(v) for v in by.values())
        need, gap = timedelta(minutes=tr['for_min']), timedelta(minutes=max(3.0, tr['for_min'] / 3))
        op, thr = CMP[tr['op']], tr['value']
        lf: dict = {}                     # 冷却按设备算（同一台设备不同刀位不重复提醒）
        for (eid, _nm), rows in sorted(by.items(), key=lambda kv: (kv[0][0], kv[1][0][0])):
            run0, prev, peak, fired_at = None, None, None, None
            for t, v in rows:
                last_fire = lf.get(eid)
                hit = op(v, thr)
                if hit and prev is not None and run0 is not None and t - prev > gap:
                    run0, peak, fired_at = None, None, None
                if hit:
                    if run0 is None:
                        run0, peak = t, v
                    peak = max(peak, v) if tr['op'] in ('>', '>=') else min(peak, v)
                    if fired_at is None and t - run0 >= need and t >= start and _in_time(t, conds) and (last_fire is None or abs(t - last_fire) >= cool):
                        fired_at = lf[eid] = t
                        fires.append({'t': t, 'equipment': ids.get(eid) or str(eid), 'value': round(v, 2), 'since': run0})
                    if fired_at is not None and fires and fires[-1]['equipment'] == (ids.get(eid) or str(eid)):
                        fires[-1]['peak'] = round(peak, 2)
                        fires[-1]['duration_min'] = round((t - run0).total_seconds() / 60, 1)
                else:
                    run0, peak, fired_at = None, None, None
                prev = t
    elif tr['type'] == 'alarm':
        al = TABLES['alarm']
        q = sa.select(al.c.equipment, al.c.start, al.c.msg, al.c.dur_s).where((al.c.start >= start) & (al.c.start < end))
        if tr.get('match'):
            q = q.where(al.c.msg.like(f"%{tr['match']}%"))
        last = {}
        rows = c.execute(q.order_by(al.c.start)).all()
        scanned = len(rows)
        for eid, t, msg, dur in rows:
            if tr.get('equipment') not in ('*', None, []) and eid not in ids:
                continue
            if not _in_time(t, conds) or (eid in last and t - last[eid] < cool):
                continue
            last[eid] = t
            fires.append({'t': t, 'equipment': ids.get(eid) or str(eid), 'value': round((dur or 0) / 60, 1), 'msg': msg})
    elif tr['type'] == 'down':
        st = TABLES['equip_state']
        need = tr['for_min'] * 60
        rows = c.execute(sa.select(st.c.equipment, st.c.start, st.c.end, st.c.dur_s, st.c.state).where((st.c.start < end) & ((st.c.end.is_(None)) | (st.c.end >= start))
                                                                                                         & st.c.state.in_(('Stopped', 'down'))).order_by(st.c.start)).all()
        scanned = len(rows)
        last = {}
        for eid, s0, e0, dur, _s in rows:
            if tr.get('equipment') not in ('*', None, []) and eid not in ids:
                continue
            d = dur if dur is not None else ((e0 or end) - s0).total_seconds()
            t = s0 + timedelta(seconds=need)
            if d >= need and start <= t < end and _in_time(t, conds) and not (eid in last and t - last[eid] < cool):
                last[eid] = t
                fires.append({'t': t, 'equipment': ids.get(eid) or str(eid), 'value': round(d / 60, 1)})
    elif tr['type'] == 'ncr':
        nt, dt = TABLES['ncr'], TABLES['defect_code']
        rows = c.execute(sa.select(nt.c.found_at, dt.c.name, nt.c.code).select_from(nt.outerjoin(dt, dt.c.id == nt.c.defect))
                         .where((nt.c.found_at >= start - timedelta(minutes=tr['within_min'])) & (nt.c.found_at < end)).order_by(nt.c.found_at)).all()
        rows = [r for r in rows if r[0] and (not tr.get('match') or tr['match'] in (r[1] or ''))]
        scanned = len(rows)
        win, last = timedelta(minutes=tr['within_min']), None
        for i, (t, name, code) in enumerate(rows):
            n = sum(1 for r in rows[:i + 1] if t - r[0] <= win)
            if n >= tr['count'] and t >= start and _in_time(t, conds) and (last is None or t - last >= cool):
                last = t
                fires.append({'t': t, 'equipment': name or _tr('不合格品'), 'value': n, 'msg': code})
    fires.sort(key=lambda x: x['t'])
    return fires, scanned


def replay(flow, days=7):
    flow = normalize_flow(flow)
    end = clock.now()
    start = end - timedelta(days=max(0.04, min(30, days)))
    with db.engine.connect() as c:
        fires, scanned = detect(c, flow, start, end)
        if flow['trigger']['type'] == 'metric':
            ts = metadata.tables['tag_sample']
            name = METRICS[flow['trigger']['metric']][1]
            first = c.execute(sa.select(sa.func.min(ts.c.ts)).where((ts.c.name == name) | ts.c.name.like(name + '@%'))).scalar()
        else:
            first = None
    data_from = max(start, first) if first else start
    span_h = (end - data_from).total_seconds() / 3600
    # 时间分布：跨度 ≤ 2 天按小时，否则按天
    hourly = span_h <= 48
    fmt = '%m-%d %H:00' if hourly else '%m-%d'
    step = timedelta(hours=1) if hourly else timedelta(days=1)
    b0 = data_from.replace(minute=0, second=0, microsecond=0) if hourly else data_from.replace(hour=0, minute=0, second=0, microsecond=0)
    buckets = []
    t = b0
    while t <= end and len(buckets) < 800:
        buckets.append(t.strftime(fmt))
        t += step
    cnt = Counter(f['t'].strftime(fmt) for f in fires)
    by_eq = Counter(f['equipment'] for f in fires)
    out = [{**f, 't': db.jsonable(f['t']), 'since': db.jsonable(f.get('since'))} for f in fires]
    per_day = len(fires) / max(span_h / 24, 1 / 24)
    note = (_tr('这段时间里一次都不会触发：阈值可能太严，或者数据里没有这种情况。') if not fires else
            _tr('平均每天约 {per_day:.1f} 次', per_day=per_day) + (_tr('，偏多，可能会打扰现场：考虑提高阈值、加长持续时间或冷却时间。') if per_day > 12 else _tr('。')))
    return {'flow': flow, 'summary': describe(flow), 'from': db.jsonable(data_from), 'to': db.jsonable(end), 'days': round(span_h / 24, 2), 'scanned': scanned,
            'count': len(fires), 'per_day': round(per_day, 2), 'by_equipment': [{'equipment': k, 'n': v} for k, v in by_eq.most_common()],
            'timeline': {'x': buckets, 'n': [cnt.get(b, 0) for b in buckets], 'unit': _tr('小时') if hourly else _tr('天')}, 'fires': out[-200:], 'note': note}


# ---------------- 执行（后台） ----------------
def _fill(s, f, flow):
    tr = flow['trigger']
    m = METRICS.get(tr.get('metric') or '', ('', '', '', None, ()))
    vals = {'equipment': f.get('equipment', ''), 'value': f"{f.get('value', '')}{m[2] if tr['type'] == 'metric' else ''}",
            'threshold': f"{OPS_TXT.get(tr.get('op'), '')}{tr.get('value', '')}{m[2]}" if tr['type'] == 'metric' else describe_trigger(tr),
            'duration': f"{f.get('duration_min', tr.get('for_min', ''))}", 'time': db.jsonable(f['t'])[5:16] if f.get('t') else '',
            'metric': m[0] if tr['type'] == 'metric' else TRIGGERS[tr['type']]}
    if isinstance(s, str):
        return re.sub(r'\{(\w+)\}', lambda x: str(vals.get(x.group(1), x.group(0))), s)
    if isinstance(s, dict):
        return {k: _fill(v, f, flow) for k, v in s.items()}
    if isinstance(s, list):
        return [_fill(v, f, flow) for v in s]
    return s


def run_actions(rule, f):
    """一次触发：执行动作。通知直接发；写操作一律起草成草稿等人审批。返回动作结果列表。"""
    from . import ai_drafts
    flow = rule['flow']
    user = auth.user_by_name(rule['created_by']) or auth.user_by_name('admin')
    sugg = [a['text'] for a in flow['actions'] if a['type'] == 'suggest']
    res = []
    with db.engine.begin() as c:
        for a in flow['actions']:
            try:
                if a['type'] == 'notify':
                    content = _fill(a.get('content') or '', f, flow) + ''.join(_tr('\n建议：{v}', v=_fill(s, f, flow)) for s in sugg)
                    rec = ops.execute(c, 'notify', {'channel': 'inbox', 'title': _fill(a['title'], f, flow), 'content': content.strip(), 'to_role': a['to_role'],
                                                    'level': a.get('level') or 'warn', 'link': '/ai/automations'}, _tr('自动化「{name}」', name=rule['name']), 'automation')
                    res.append({'type': 'notify', 'ok': True, 'text': rec['text']})
                elif a['type'] == 'draft':
                    d = ai_drafts.create(user, a['op'], _fill(a.get('payload') or {}, f, flow), source='automation', conn=c,
                                         summary=_tr('自动化「{name}」在 {v} 触发：{describe_trigger}（实际 {get} {get2}）。', name=rule['name'], v=db.jsonable(f['t'])[5:16], describe_trigger=describe_trigger(flow['trigger']), get=f.get('equipment'), get2=f.get('value'))
                                                 + ''.join(_tr('建议：{s}', s=s) for s in sugg))
                    res.append({'type': 'draft', 'ok': True, 'draft_id': d['id'], 'text': d['title']})
                elif a['type'] == 'agent':
                    from ..agent import engine as E
                    from ..schema import agent_t, agent_version_t
                    ag = c.execute(sa.select(agent_t).where(agent_t.c.id == a['agent_id'])).mappings().first()
                    v = c.execute(sa.select(agent_version_t).where(agent_version_t.c.id == ag['published_version'])).mappings().first() if ag and ag['published_version'] else None
                    if not v:
                        res.append({'type': 'agent', 'ok': False, 'text': _tr('智能体没有发布')})
                        continue
                    rid = E.start_run(ag['id'], v['dsl'], version=v['id'], mode='live', trigger='automation', user=rule['created_by'], run_as=v['published_by'] or 'admin',
                                      inputs={'query': _tr('自动化「{name}」触发：{get} {get2}', name=rule['name'], get=f.get('equipment'), get2=f.get('value'))})
                    res.append({'type': 'agent', 'ok': True, 'run_id': rid, 'text': _tr('已启动智能体「{name}」', name=ag['name'])})
            except Exception as ex:
                res.append({'type': a['type'], 'ok': False, 'text': str(ex)[:200]})
    for a in flow['actions']:              # 5.0 M6：运行技能（自己开事务写运行记录，所以放在上面的事务提交之后）
        if a['type'] != 'skill':
            continue
        try:
            from . import skills
            r = skills.run(a['skill_id'], {**(a.get('params') or {}), 'equipment': a.get('params', {}).get('equipment') or f.get('equipment')}, trigger='automation',
                           user=None, narrative=None)
            res.append({'type': 'skill', 'ok': r['status'] == 'ok', 'run_id': r['id'], 'text': _tr('技能运行{v}', v=_tr('完成') if r['status'] == 'ok' else _tr('失败：') + (r.get('error') or ''))})
        except Exception as ex:
            res.append({'type': 'skill', 'ok': False, 'text': str(ex)[:200]})
    return res


def tick():
    """后台：检查启用的规则。数据时钟往前走了才看；第一次只记下时间，不补触发。"""
    from .ai_switch import enabled
    if not enabled('automation'):
        return 0
    now = clock.now()
    n = 0
    with db.engine.connect() as c:
        rules = [dict(r) for r in c.execute(sa.select(auto_t).where(auto_t.c.status == 'active')).mappings()]
    for r in rules:
        last = r['last_eval']
        if last is None or last > now:
            with db.engine.begin() as c:
                c.execute(auto_t.update().where(auto_t.c.id == r['id']).values(last_eval=now))
            continue
        if now - last < timedelta(seconds=20):
            continue
        try:
            flow = normalize_flow(r['flow'])
        except HTTPException:
            continue
        r['flow'] = flow
        with db.engine.connect() as c:
            fires, _ = detect(c, flow, last, now)
        st = r['state'] or {}
        lastfire = {k: datetime.fromisoformat(v) for k, v in (st.get('last_fire') or {}).items()}
        cool = timedelta(minutes=flow['cooldown_min'])
        for f in fires:
            if f['equipment'] in lastfire and f['t'] - lastfire[f['equipment']] < cool:
                continue
            lastfire[f['equipment']] = f['t']
            acts = run_actions(r, f)
            with db.engine.begin() as c:
                c.execute(fire_t.insert().values(rule=r['id'], t=f['t'], equipment=f['equipment'], value=f.get('value'), detail={'fire': {**f, 't': db.jsonable(f['t']),
                                                 'since': db.jsonable(f.get('since'))}, 'actions': acts}, created_at=datetime.now()))
                c.execute(auto_t.update().where(auto_t.c.id == r['id']).values(last_fire=f['t'], fire_count=auto_t.c.fire_count + 1))
            n += 1
        with db.engine.begin() as c:
            c.execute(auto_t.update().where(auto_t.c.id == r['id']).values(last_eval=now, state={**st, 'last_fire': {k: v.isoformat() for k, v in lastfire.items()}}))
    return n


# ---------------- 接口 ----------------
def _out(r):
    d = {k: db.jsonable(v) for k, v in r.items()}
    try:
        d['summary'] = describe(normalize_flow(r['flow']))
    except HTTPException:
        d['summary'] = ''
    return d


@router.post('/api/ai/automations/parse')
def api_parse(body: dict = Body(...), user=Depends(current_user)):
    text = (body.get('text') or '').strip()
    if len(text) < 4:
        raise HTTPException(400, _tr('请用一句话描述规则，例如：主轴负载连续 10 分钟 > 90% 就提醒班长并建议降速'))
    return parse(text)


@router.post('/api/ai/automations/replay')
def api_replay(body: dict = Body(...), user=Depends(current_user)):
    return replay(body.get('flow') or {}, float(body.get('days') or 7))


@router.get('/api/ai/automations')
def api_list(user=Depends(current_user)):
    with db.engine.connect() as c:
        rows = [dict(r) for r in c.execute(sa.select(auto_t).order_by(auto_t.c.id.desc())).mappings()]
    return {'items': [_out(r) for r in rows], 'metrics': [{'key': k, 'label': v[0], 'unit': v[2]} for k, v in METRICS.items()],
            'triggers': TRIGGERS, 'actions': ACTIONS, 'roles': ROLES}


@router.post('/api/ai/automations')
def api_create(body: dict = Body(...), user=Depends(current_user)):
    flow = normalize_flow(body.get('flow') or {})
    status = body.get('status') if body.get('status') in ('active', 'paused') else 'paused'
    rep = body.get('replay')
    now = datetime.now()
    with db.engine.begin() as c:
        rid = c.execute(auto_t.insert().values(name=flow['name'], text=(body.get('text') or '')[:1000], flow=flow, status=status, created_by=user['username'],
                                               created_at=now, updated_at=now, fire_count=0, by=body.get('by') or '',
                                               replay={k: rep.get(k) for k in ('count', 'per_day', 'days', 'from', 'to')} if isinstance(rep, dict) else None,
                                               last_eval=clock.now() if status == 'active' else None, state={})).inserted_primary_key[0]
        ops.audit(c, user['username'], 'ai_automation', f'ai_automation:{rid}', {'op': 'create', 'flow': flow, 'status': status})
        r = dict(c.execute(sa.select(auto_t).where(auto_t.c.id == rid)).mappings().first())
    return _out(r)


@router.put('/api/ai/automations/{rid}')
def api_update(rid: int, body: dict = Body(...), user=Depends(current_user)):
    flow = normalize_flow(body.get('flow') or {})
    with db.engine.begin() as c:
        if not c.execute(sa.select(auto_t.c.id).where(auto_t.c.id == rid)).first():
            raise HTTPException(404, _tr('规则不存在'))
        c.execute(auto_t.update().where(auto_t.c.id == rid).values(flow=flow, name=flow['name'], updated_at=datetime.now()))
        ops.audit(c, user['username'], 'ai_automation', f'ai_automation:{rid}', {'op': 'update', 'flow': flow})
        r = dict(c.execute(sa.select(auto_t).where(auto_t.c.id == rid)).mappings().first())
    return _out(r)


@router.post('/api/ai/automations/{rid}/status')
def api_status(rid: int, body: dict = Body(...), user=Depends(current_user)):
    st = body.get('status')
    if st not in ('active', 'paused'):
        raise HTTPException(400, _tr('status 只能是 active（启用）或 paused（暂停）'))
    with db.engine.begin() as c:
        if not c.execute(sa.select(auto_t.c.id).where(auto_t.c.id == rid)).first():
            raise HTTPException(404, _tr('规则不存在'))
        c.execute(auto_t.update().where(auto_t.c.id == rid).values(status=st, updated_at=datetime.now(), **({'last_eval': clock.now()} if st == 'active' else {})))
        ops.audit(c, user['username'], 'ai_automation', f'ai_automation:{rid}', {'op': st})
        r = dict(c.execute(sa.select(auto_t).where(auto_t.c.id == rid)).mappings().first())
    return _out(r)


@router.delete('/api/ai/automations/{rid}')
def api_delete(rid: int, user=Depends(current_user)):
    with db.engine.begin() as c:
        c.execute(fire_t.delete().where(fire_t.c.rule == rid))
        c.execute(auto_t.delete().where(auto_t.c.id == rid))
        ops.audit(c, user['username'], 'ai_automation', f'ai_automation:{rid}', {'op': 'delete'})
    return {'ok': True}


@router.get('/api/ai/automations/{rid}/fires')
def api_fires(rid: int, limit: int = 50, user=Depends(current_user)):
    with db.engine.connect() as c:
        rows = c.execute(sa.select(fire_t).where(fire_t.c.rule == rid).order_by(fire_t.c.id.desc()).limit(min(200, limit))).mappings().all()
    return [{k: db.jsonable(v) for k, v in dict(r).items()} for r in rows]
