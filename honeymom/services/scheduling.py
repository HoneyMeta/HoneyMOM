"""有限产能排产（带约束）与改排影响评估。

约束（主数据都在通用增删改查里维护）：
  班次 shift（active=排产启用）           → 设备只在启用班次的时间里干活
  人员技能 skill                          → 某工位组/工序只有在"有会干的人"的班次里才能开（一人可看多台，不按人数限产能）
  设备停机计划 downtime_plan + 未修好的维修单 → 这台设备在这段时间不可用
  物料齐套 wo_kitting                     → 缺料的工单从预计到料时间之后才能开工
  模具/夹具 tooling                       → 需要模具的工序要拿到一套可用的模具；同一套模具同一时间只能在一台设备上；保养中的到可用时间才能用
  换型时间 changeover                     → 设备上一单是别的产品时，先换型

compute() 只读、算出整个计划（每道工序的开始/结束、用哪几台设备、冲突原因）；apply() 写回 wo_op。
preview：用"假设"（某设备停机、某料晚到、某模具保养）算一遍，对比当前计划，列出受影响的工单和原因，不落库。
"""
from ..i18n import _tr
from collections import defaultdict
from datetime import datetime, time, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db
from ..auth import current_user, require_write
from ..crud import ACTIONS, attach_labels, out
from ..db import clock, parse_dt
from ..schema import TABLES

router = APIRouter()
HORIZON_D = 45
REASON_CN = {'shortage': '缺料', 'machine_down': '设备停机', 'no_skill': '无合格操作工', 'mould_busy': '模具被占用', 'mould_maint': '模具保养中',
             'mould_life': '模具寿命不足', 'no_mould': '没有可用模具', 'changeover': '换型', 'shift_off': '非工作班次', 'late': '预计逾期', 'outsource': '委外'}
SEV = {'shortage': 'error', 'machine_down': 'warn', 'no_skill': 'error', 'mould_busy': 'warn', 'mould_maint': 'warn', 'mould_life': 'warn', 'no_mould': 'error',
       'changeover': 'info', 'shift_off': 'info', 'late': 'error', 'outsource': 'info'}


# ---------------- 日历 ----------------
def _hm(s, default):
    try:
        h, m = str(s or default).split(':')[:2]
        return time(int(h) % 24, int(m))
    except ValueError:
        return default if isinstance(default, time) else time(0, 0)


def shift_windows(shifts, start: datetime, days=HORIZON_D):
    """班次 → [(开始, 结束, 班次编码)]，跨零点的班次算到第二天。"""
    out_ = []
    d0 = start.date() - timedelta(days=1)
    for i in range(days + 2):
        d = d0 + timedelta(days=i)
        for s in shifts:
            a, b = _hm(s['start'], '00:00'), _hm(s['end'], '00:00')
            ws = datetime.combine(d, a)
            we = datetime.combine(d, b)
            if we <= ws:
                we += timedelta(days=1)
            out_.append((ws, we, s['code']))
    out_.sort()
    return out_


def merge(iv):
    iv = sorted(iv)
    res = []
    for a, b in iv:
        if res and a <= res[-1][1]:
            res[-1] = (res[-1][0], max(res[-1][1], b))
        else:
            res.append((a, b))
    return res


def subtract(iv, blocks):
    """可用区间 iv 减去不可用区间 blocks。"""
    res = []
    for a, b in iv:
        cur = [(a, b)]
        for x, y in blocks:
            nxt = []
            for p, q in cur:
                if y <= p or x >= q:
                    nxt.append((p, q))
                else:
                    if p < x:
                        nxt.append((p, x))
                    if y < q:
                        nxt.append((y, q))
            cur = nxt
        res += cur
    return [r for r in res if r[1] > r[0]]


def place(avail, earliest: datetime, dur: timedelta):
    """在可用区间里从 earliest 起排 dur（可跨班次断开）。返回 (开始, 结束, 是否因非工作时间推迟)。"""
    if dur.total_seconds() <= 0:
        for a, b in avail:
            if b > earliest:
                s = max(a, earliest)
                return s, s, s > earliest
        return earliest, earliest, False
    left = dur
    start = None
    for a, b in avail:
        if b <= earliest:
            continue
        s = max(a, earliest)
        if start is None:
            start = s
        take = min(b - s, left)
        left -= take
        if left.total_seconds() <= 0:
            return start, s + take, start > earliest
    # 超出排产视野：按连续时间往后补
    if start is None:
        start = max(earliest, avail[-1][1] if avail else earliest)
    end_base = avail[-1][1] if avail and avail[-1][1] > start else start
    return start, end_base + left, start > earliest


# ---------------- 读取约束 ----------------
def load_context(c, now, overrides=None):
    ov = overrides or {}
    ctx = {'now': now}
    shifts = [s for s in db.rows(c, 'shift') if s.get('active') is not False]
    ctx['shifts'] = shifts
    ctx['windows'] = shift_windows(shifts, now) if shifts else [(now - timedelta(days=1), now + timedelta(days=HORIZON_D), '*')]
    # 设备（按工位组）
    eqs = {e['id']: e for e in db.rows(c, 'equipment')}
    ctx['eqs'] = eqs
    groups = defaultdict(list)
    for w in db.rows(c, 'workstation'):
        g = w['group'] or w['code']
        groups[g].append(w['equipment'] or f"ws:{w['code']}")
    ctx['groups'] = groups
    # 停机：计划 + 未修好的维修单（没有停机计划的，按默认 2 小时估）+ 假设
    down = defaultdict(list)
    dt = TABLES['downtime_plan']
    for r in db.rows(c, 'downtime_plan', dt.c.end > now):
        down[r['equipment']].append((r['start'], r['end'], r['reason'] or _tr('计划停机'), r['kind']))
    repair_h = float(db.setting(c, 'repair_default_h', 2) or 2)
    ro = TABLES['repair_order']
    for r in db.rows(c, 'repair_order', ro.c.status.in_(('reported', 'assigned', 'repairing'))):
        if not down.get(r['equipment']):
            down[r['equipment']].append((now, now + timedelta(hours=repair_h), _tr('维修单 {code}：{fault}', code=r['code'], fault=r['fault']), 'repair'))
    for d in ov.get('downtime') or []:
        e = _eq_id(c, d.get('equipment'))
        if e:
            down[e].append((parse_dt(d.get('start')) or now, parse_dt(d.get('end')) or now + timedelta(hours=4), d.get('reason') or _tr('假设停机'), 'whatif'))
    ctx['down'] = down
    # 技能：(工序, 工位组) → 有合格人员的班次
    emps = {e['id']: e for e in db.rows(c, 'employee')}
    teams = {t['id']: t for t in db.rows(c, 'team')}
    shift_code = {s['id']: s['code'] for s in db.rows(c, 'shift')}
    skills = db.rows(c, 'skill')
    ctx['skills_on'] = bool(skills)
    sk = []
    today = now.date()
    for s in skills:
        if s['valid_until'] and s['valid_until'] < today:
            continue
        e = emps.get(s['employee'])
        if not e:
            continue
        t = teams.get(e['team']) or {}
        sk.append({'process': s['process'], 'group': s['station_group'], 'equipment': s['equipment'], 'employee': e['name'],
                   'shift': shift_code.get(t.get('shift'))})
    absent = set(ov.get('absent') or [])
    ctx['skills'] = [s for s in sk if s['employee'] not in absent]
    # 物料齐套
    kit = defaultdict(list)
    mats = {m['id']: m for m in db.rows(c, 'material')}
    for k in db.rows(c, 'wo_kitting'):
        if (k['qty_ready'] or 0) + 1e-9 < (k['qty_need'] or 0):
            kit[k['work_order']].append({'material': (mats.get(k['material']) or {}).get('name') or f"#{k['material']}", 'eta': k['eta'],
                                         'short': (k['qty_need'] or 0) - (k['qty_ready'] or 0), 'unit': (mats.get(k['material']) or {}).get('unit') or ''})
    for k in ov.get('kitting') or []:
        w = _wo_id(c, k.get('work_order'))
        if w:
            kit[w].append({'material': k.get('material') or _tr('物料'), 'eta': parse_dt(k.get('eta')), 'short': k.get('short') or 0, 'unit': '', 'whatif': True})
    ctx['kit'] = kit
    # 模具
    tools = [t for t in db.rows(c, 'tooling') if t['status'] != 'scrapped']
    tmaint = {}
    for t in ov.get('tooling') or []:
        tid = _tool_id(c, t.get('tooling'))
        if tid:
            tmaint[tid] = parse_dt(t.get('until')) or now + timedelta(hours=8)
    for t in tools:
        if t['id'] in tmaint:
            t['status'], t['available_from'] = 'maint', tmaint[t['id']]
    ctx['tools'] = tools
    # 换型
    ctx['changeover'] = db.rows(c, 'changeover')
    return ctx


def _eq_id(c, v):
    if v in (None, ''):
        return None
    if isinstance(v, int) or str(v).isdigit():
        return int(v)
    r = db.get(c, 'equipment', code=str(v))
    return r['id'] if r else None


def _wo_id(c, v):
    if v in (None, ''):
        return None
    if isinstance(v, int) or str(v).isdigit():
        return int(v)
    r = db.get(c, 'work_order', code=str(v))
    return r['id'] if r else None


def _tool_id(c, v):
    if v in (None, ''):
        return None
    if isinstance(v, int) or str(v).isdigit():
        return int(v)
    r = db.get(c, 'tooling', code=str(v))
    return r['id'] if r else None


def changeover_min(ctx, group, frm, to):
    if not frm or frm == to:
        return 0
    best = None
    for r in ctx['changeover']:
        if r['station_group'] != group:
            continue
        score = (r['from_product'] == frm) * 2 + (r['to_product'] == to) * 2 - (r['from_product'] not in (None, frm)) * 9 - (r['to_product'] not in (None, to)) * 9
        if score >= 0 and (best is None or score > best[0]):
            best = (score, r['minutes'] or 0)
    return best[1] if best else 0


def skilled_shifts(ctx, process, group, machine):
    """有合格操作工的班次集合；None=不限制（没有维护技能表）。"""
    if not ctx['skills_on']:
        return None
    ok = set()
    names = set()
    for s in ctx['skills']:
        if s['process'] not in (None, process):
            continue
        if s['group'] not in (None, '', group):
            continue
        if s['equipment'] not in (None, machine):
            continue
        if s['shift']:
            ok.add(s['shift'])
            names.add(s['employee'])
    return ok


# ---------------- 计算 ----------------
def compute(c, overrides=None):
    now = clock.now().replace(second=0, microsecond=0)
    ctx = load_context(c, now, overrides)
    wot, opt = TABLES['work_order'], TABLES['wo_op']
    procs = {p['id']: p for p in db.rows(c, 'process')}
    steps_ct = {(s['route'], s['seq']): s['std_cycle_s'] for s in db.rows(c, 'route_step')}
    from .exec_outsource import os_steps, plan_outsourced      # 委外工序：按供应商周期排（不占设备）
    oss = os_steps(c)
    wos = db.rows(c, 'work_order', wot.c.status.in_(('released', 'in_progress')), order=(wot.c.priority, wot.c.due, wot.c.id))
    mfree: dict = {}                 # 设备 → 空闲时间
    mlast: dict = {}                 # 设备 → 上一单产品
    tfree: dict = {}                 # 模具 → (空闲时间, 占用它的工单号)
    avail_cache: dict = {}
    # 正在干的单：设备上一单产品取正在生产的工单
    for wo in wos:
        if wo['status'] == 'in_progress':
            for op in db.rows(c, 'wo_op', opt.c.status == 'running', work_order=wo['id']):
                g = op['station_group'] or f"P{op['process']}"
                for m in ctx['groups'].get(g) or [g]:
                    mlast.setdefault(m, wo['product'])

    def avail_for(m, process, group):
        key = (m, process, group)
        if key in avail_cache:
            return avail_cache[key]
        sh = skilled_shifts(ctx, process, group, m if isinstance(m, int) else None)
        wins = [(a, b) for a, b, code in ctx['windows'] if sh is None or code in sh or code == '*']
        wins = merge(wins)
        blocks = [(a, b) for a, b, _, _ in ctx['down'].get(m, [])] if isinstance(m, int) else []
        res = subtract(wins, merge(blocks)) if blocks else wins
        avail_cache[key] = (res, sh)
        return avail_cache[key]

    plan_ops = {}
    plan_wo = {}
    for wo in wos:
        ops = db.rows(c, 'wo_op', order=opt.c.seq, work_order=wo['id'])
        wo_conf = []
        earliest0 = max(now, wo['start_plan'] or now)
        short = ctx['kit'].get(wo['id']) or []
        if short:
            etas = [s['eta'] for s in short if s['eta']]
            no_eta = [s for s in short if not s['eta']]
            eta = max(etas) if etas else None
            if no_eta:
                eta = max(eta or now, now + timedelta(hours=float(db.setting(c, 'shortage_default_h', 24) or 24)))
            if eta and eta > earliest0:
                earliest0 = eta
            for s in short:
                wo_conf.append({'type': 'shortage', 'text': _tr('缺 {material} {short:g}{unit}，', material=s['material'], short=s['short'], unit=s['unit']) + (_tr('预计 {eta:%m-%d %H:%M} 到料', eta=s['eta']) if s['eta'] else _tr('没有到料时间（按 24 小时后估）'))})
        prev_s = prev_e = earliest0
        first = True
        for op in ops:
            g = op['station_group'] or f"P{op['process']}"
            proc = procs.get(op['process']) or {}
            ct = steps_ct.get((wo['route'], op['seq'])) or proc.get('std_cycle_s') or 60
            remain = max(0, (op['qty_plan'] or 0) - (op['qty_good'] or 0))
            conf = [dict(x) for x in wo_conf] if first else []
            if op['status'] == 'done' or remain == 0:
                plan_ops[op['id']] = {'start': op['plan_start'], 'end': op['plan_end'] or op['act_end'], 'machines': op['machines'], 'conflicts': [], 'setup': 0,
                                      'wo': wo['id']}
                first = False
                continue
            if (wo['route'], op['seq']) in oss:
                s, e, note = plan_outsourced(oss[(wo['route'], op['seq'])], prev_s, prev_e, now, remain, ct)
                plan_ops[op['id']] = {'start': s, 'end': e, 'machines': _tr('委外'), 'conflicts': _dedup(conf + [note]), 'setup': 0, 'wo': wo['id']}
                prev_s, prev_e = e, e          # 批量委外：回厂后下道才能开工
                first = False
                continue
            machines = list(ctx['groups'].get(g) or [g])
            # 模具
            need_tools = [t for t in ctx['tools'] if t['process'] == op['process'] and (not t['station_group'] or t['station_group'] == g)]
            usable_tools = [t for t in need_tools if t['product'] in (None, wo['product'])]
            if need_tools and not usable_tools:
                conf.append({'type': 'no_mould', 'text': _tr('{get} 没有适用于这个产品的模具/夹具', get=proc.get('name', ''))})
            if usable_tools:
                machines = machines[:max(1, len(usable_tools))]
            n = max(1, len(machines))
            dur_each = timedelta(seconds=remain * ct / n)
            earliest = max(prev_s + timedelta(seconds=ct * 2), now) if not first else max(prev_s, now)
            starts, ends, used = [], [], []
            setup_max = 0
            tpool = sorted(usable_tools, key=lambda t: (tfree.get(t['id'], (now, ''))[0], t['code']))
            taken = set()
            for i, m in enumerate(machines):
                avail, sh = avail_for(m, op['process'], g)
                if sh is not None and not sh:
                    if not any(x['type'] == 'no_skill' for x in conf):
                        conf.append({'type': 'no_skill', 'text': _tr('{g} {get} 没有维护合格操作工（人员技能），按全天估', g=g, get=proc.get('name', ''))})
                    avail = merge([(a, b) for a, b, _ in ctx['windows']])
                e0 = max(earliest, mfree.get(m, now))
                # 停机原因
                for a, b, why, kind in ctx['down'].get(m, []) if isinstance(m, int) else []:
                    if a < e0 + dur_each and b > e0:
                        conf.append({'type': 'machine_down', 'text': _tr('{v} {a:%m-%d %H:%M}–{b:%H:%M} 停机：{why}', v=_eq_label(ctx, m), a=a, b=b, why=why), 'machine': _eq_label(ctx, m)})
                # 模具
                if tpool:
                    # 装在这台设备上的模具/夹具优先；否则拿最早空出来、这道工序还没用的那套
                    t = next((x for x in tpool if x['equipment'] == m and x['id'] not in taken), None) \
                        or next((x for x in tpool if x['id'] not in taken and not (x['equipment'] and x['equipment'] in machines and x['equipment'] != m)), None) \
                        or tpool[i % len(tpool)]
                    taken.add(t['id'])
                    tf, by = tfree.get(t['id'], (now, ''))
                    if by == wo['code']:
                        tf = now
                    if t['status'] == 'maint':
                        av = t['available_from'] or now + timedelta(hours=8)
                        if av > e0:
                            conf.append({'type': 'mould_maint', 'text': _tr('模具 {code} 保养中，预计 {av:%m-%d %H:%M} 可用', code=t['code'], av=av)})
                            e0 = av
                    if tf > e0 + timedelta(minutes=1):
                        conf.append({'type': 'mould_busy', 'text': _tr('模具 {code} 被 {by} 占用到 {tf:%m-%d %H:%M}', code=t['code'], by=by, tf=tf)})
                        e0 = tf
                    left = (t['life_shots'] or 0) - (t['shots'] or 0) if t['life_shots'] else None
                    if left is not None and left < remain / n:
                        conf.append({'type': 'mould_life', 'text': _tr('模具 {code} 剩余 {left} 模次，不够本单 {int} 件', code=t['code'], left=left, int=int(remain / n))})
                # 换型
                setup = changeover_min(ctx, g, mlast.get(m), wo['product'])
                if setup:
                    conf.append({'type': 'changeover', 'text': _tr('{v} 换型 {setup:g} 分钟', v=_eq_label(ctx, m), setup=setup), 'minutes': setup})
                s, e, shifted = place(avail, e0, dur_each + timedelta(minutes=setup))
                if shifted and sh:
                    pass
                starts.append(s)
                ends.append(e)
                used.append(_eq_label(ctx, m))
                mfree[m] = e
                mlast[m] = wo['product']
                setup_max = max(setup_max, setup)
                if tpool:
                    tfree[t['id']] = (e, wo['code'])
            start = min(starts)
            if op['act_start'] and op['status'] == 'running':
                start = max(now, start)
            end = max(max(ends), prev_e + timedelta(seconds=ct))
            plan_ops[op['id']] = {'start': start, 'end': end, 'machines': ','.join(used), 'conflicts': _dedup(conf), 'setup': setup_max, 'wo': wo['id']}
            prev_s, prev_e = start, end
            first = False
        late = bool(wo['due'] and prev_e > wo['due'])
        plan_wo[wo['id']] = {'code': wo['code'], 'end': prev_e, 'due': wo['due'], 'late': late, 'priority': wo['priority'], 'customer': wo['customer']}
    return {'now': now, 'ops': plan_ops, 'wos': plan_wo}


def _eq_label(ctx, m):
    e = ctx['eqs'].get(m) if isinstance(m, int) else None
    return e['code'] if e else str(m).replace('ws:', '')


def _dedup(conf):
    seen, res = set(), []
    for x in conf:
        k = (x['type'], x['text'])
        if k not in seen:
            seen.add(k)
            res.append({**x, 'label': REASON_CN.get(x['type'], x['type']), 'severity': SEV.get(x['type'], 'info')})
    return res


def apply(c, plan):
    opt = TABLES['wo_op']
    for oid, p in plan['ops'].items():
        c.execute(opt.update().where(opt.c.id == oid).values(plan_start=p['start'], plan_end=p['end'], machines=p['machines'], conflicts=p['conflicts'],
                                                             setup_min=p['setup']))
    return [w['code'] for w in plan['wos'].values() if w['late']]


def schedule(c, overrides=None):
    """兼容旧接口：排产并写回，返回预计逾期的工单号。"""
    return apply(c, compute(c, overrides))


def current_plan(c):
    """当前已落库的计划（用于对比）。"""
    wot, opt = TABLES['work_order'], TABLES['wo_op']
    wos = db.rows(c, 'work_order', wot.c.status.in_(('released', 'in_progress')))
    res = {}
    for w in wos:
        ops = db.rows(c, 'wo_op', order=opt.c.seq, work_order=w['id'])
        ends = [o['plan_end'] for o in ops if o['plan_end']]
        res[w['id']] = {'end': max(ends) if ends else None, 'conflicts': [x for o in ops for x in (o['conflicts'] or [])]}
    return res


def impact(c, before, after):
    """对比两版计划：受影响的工单（完工时间变化、出现新冲突）和原因。"""
    rows = []
    for wid, a in after['wos'].items():
        b = before.get(wid) or {}
        old_end, new_end = b.get('end'), a['end']
        delta = round((new_end - old_end).total_seconds() / 60) if old_end and new_end else None
        reasons = []
        for oid, p in after['ops'].items():
            if p['wo'] != wid:
                continue
            for x in p['conflicts']:
                if x not in reasons:
                    reasons.append(x)
        old_types = {(x.get('type'), x.get('text')) for x in b.get('conflicts') or []}
        new_reasons = [x for x in reasons if (x['type'], x['text']) not in old_types]
        if a['late']:
            reasons.append({'type': 'late', 'label': REASON_CN['late'], 'severity': 'error',
                            'text': _tr('预计 {new_end:%m-%d %H:%M} 完工，晚于交期 {due:%m-%d %H:%M}', new_end=new_end, due=a['due'])})
        changed = (delta is not None and abs(delta) >= 1) or bool(new_reasons) or old_end is None
        rows.append({'work_order': wid, 'code': a['code'], 'customer': a['customer'], 'priority': a['priority'], 'old_end': db.jsonable(old_end),
                     'new_end': db.jsonable(new_end), 'delta_min': delta, 'due': db.jsonable(a['due']), 'late': a['late'], 'affected': changed,
                     'reasons': reasons, 'new_reasons': new_reasons})
    rows.sort(key=lambda r: (not r['affected'], -(r['delta_min'] or 0)))
    return rows


# ---------------- 接口 ----------------
@router.post('/api/production/schedule')
def run_schedule(body: dict = Body(default={}), user=Depends(current_user)):
    """重新排产（落库），返回预计逾期和受影响工单。"""
    require_write(user, 'work_order', 'update')
    with db.engine.begin() as c:
        before = current_plan(c)
        plan = compute(c)
        late = apply(c, plan)
        imp = impact(c, before, plan)
    return {'ok': True, 'late': late, 'impact': imp, 'affected': sum(1 for r in imp if r['affected'])}


@router.post('/api/production/schedule/preview')
def preview(body: dict = Body(default={}), user=Depends(current_user)):
    """假设分析：{downtime:[{equipment,start,end,reason}], kitting:[{work_order,material,eta}], tooling:[{tooling,until}], absent:[员工名]}
    → 不落库，返回受影响工单、原因和新计划。"""
    with db.engine.connect() as c:       # 不提交：优先级等临时改动在连接关闭时回滚
        before = current_plan(c)
        base = compute(c) if body.get('vs') == 'fresh' else None
        if base:
            before = {k: {'end': v['end'], 'conflicts': [x for p in base['ops'].values() if p['wo'] == k for x in p['conflicts']]} for k, v in base['wos'].items()}
        for p in body.get('priority') or []:
            w = _wo_id(c, p.get('work_order'))
            if w and p.get('priority'):
                c.execute(TABLES['work_order'].update().where(TABLES['work_order'].c.id == w).values(priority=int(p['priority'])))
        plan = compute(c, body)
        imp = impact(c, before, plan)
        c.rollback()
    ops = {str(k): {'start': db.jsonable(v['start']), 'end': db.jsonable(v['end']), 'machines': v['machines'], 'conflicts': v['conflicts']} for k, v in plan['ops'].items()}
    return {'impact': imp, 'affected': sum(1 for r in imp if r['affected']), 'late': [w['code'] for w in plan['wos'].values() if w['late']], 'ops': ops}


@router.post('/api/production/schedule/propose')
def propose(body: dict = Body(...), user=Depends(current_user)):
    """把一个假设存成排程调整建议（带影响评估）。采纳时把停机/到料/模具保养写进主数据并重排。"""
    require_write(user, 'schedule_proposal', 'create')
    changes = []
    for d in body.get('downtime') or []:
        changes.append({'kind': 'downtime', **d})
    for k in body.get('kitting') or []:
        changes.append({'kind': 'kitting', **k})
    for t in body.get('tooling') or []:
        changes.append({'kind': 'tooling', **t})
    for p in body.get('priority') or []:
        changes.append({'kind': 'priority', **p})
    with db.engine.begin() as c:
        before = current_plan(c)
        ov = {k: body.get(k) for k in ('downtime', 'kitting', 'tooling', 'absent')}
        pr = body.get('priority') or []
        plan = compute(c, ov) if not pr else None
        if pr:      # 优先级调整：在事务里临时改，算完回滚到原值
            old = {}
            for p in pr:
                w = _wo_id(c, p.get('work_order'))
                if w:
                    old[w] = db.get(c, 'work_order', w)['priority']
                    c.execute(TABLES['work_order'].update().where(TABLES['work_order'].c.id == w).values(priority=int(p['priority'])))
            plan = compute(c, ov)
            for w, v in old.items():
                c.execute(TABLES['work_order'].update().where(TABLES['work_order'].c.id == w).values(priority=v))
        imp = impact(c, before, plan)
        aff = [r for r in imp if r['affected']]
        title = body.get('title') or (_tr('排程调整：') + _tr('；').join(_change_text(c, x) for x in changes)[:120] if changes else _tr('重新排产'))
        text = (body.get('proposal') or '') + ('\n' if body.get('proposal') else '') + '\n'.join(
            _tr('- {code}：完工 {v} → {new_end}（{v2}{v3} 分钟）', code=r['code'], v=r['old_end'] or '—', new_end=r['new_end'], v2='+' if (r['delta_min'] or 0) >= 0 else '', v3=r['delta_min'] or 0)
            + (_tr('，原因：{join}', join=_tr('、').join(x['label'] for x in r['reasons'][:4])) if r['reasons'] else '') for r in aff)
        rid = db.insert(c, 'schedule_proposal', {'title': title, 'work_order': aff[0]['work_order'] if aff else None, 'proposal': text, 'changes': changes,
                                                 'impact': imp, 'status': 'pending', 'created_by': user['name'] or user['username'], 'source': 'manual'}, raw=True)
    return {'ok': True, 'id': rid, 'affected': len(aff), 'impact': imp}


def _change_text(c, x):
    k = x.get('kind')
    if k == 'downtime':
        return _tr('{get} 停机 {get2}–{get3}', get=x.get('equipment'), get2=x.get('start', ''), get3=x.get('end', ''))
    if k == 'kitting':
        return _tr('{get} {v} 到料推迟到 {get2}', get=x.get('work_order'), v=x.get('material') or _tr('物料'), get2=x.get('eta'))
    if k == 'tooling':
        return _tr('模具 {get} 保养到 {get2}', get=x.get('tooling'), get2=x.get('until'))
    if k == 'priority':
        return _tr('{get} 优先级改为 {get2}', get=x.get('work_order'), get2=x.get('priority'))
    return str(x)


def _accept(c, r, body, user):
    """采纳排程调整建议：停机/到料/模具保养写进主数据，优先级生效，然后重排并记录实际影响。"""
    if r['status'] != 'pending':
        raise HTTPException(400, _tr('这条建议已经处理过了'))
    n = 0
    who = user['name'] or user['username']
    for ch in r['changes'] or []:
        k = ch.get('kind') or ('priority' if ch.get('priority') else None)
        if k == 'priority':
            w = _wo_id(c, ch.get('work_order'))
            if w and ch.get('priority'):
                db.update(c, 'work_order', w, {'priority': int(ch['priority'])}, raw=True)
                n += 1
        elif k == 'downtime':
            e = _eq_id(c, ch.get('equipment'))
            if e:
                db.insert(c, 'downtime_plan', {'equipment': e, 'kind': ch.get('dkind') or 'repair', 'start': parse_dt(ch.get('start')) or clock.now(),
                                               'end': parse_dt(ch.get('end')) or clock.now() + timedelta(hours=4), 'reason': ch.get('reason') or _tr('排程建议 {code}', code=r['code']),
                                               'created_by': who}, raw=True)
                n += 1
        elif k == 'kitting':
            w = _wo_id(c, ch.get('work_order'))
            if w:
                kt = TABLES['wo_kitting']
                m = db.get(c, 'material', code=ch.get('material')) if ch.get('material') else None
                rows = db.rows(c, 'wo_kitting', *( [kt.c.material == m['id']] if m else []), work_order=w)
                for row in rows:
                    db.update(c, 'wo_kitting', row['id'], {'eta': parse_dt(ch.get('eta'))}, raw=True)
                if not rows and m:
                    db.insert(c, 'wo_kitting', {'work_order': w, 'material': m['id'], 'qty_need': ch.get('short') or 1, 'qty_ready': 0, 'eta': parse_dt(ch.get('eta')),
                                                'status': 'short'}, raw=True)
                n += 1
        elif k == 'tooling':
            t = _tool_id(c, ch.get('tooling'))
            if t:
                db.update(c, 'tooling', t, {'status': 'maint', 'available_from': parse_dt(ch.get('until'))}, raw=True)
                n += 1
    before = current_plan(c)
    plan = compute(c)
    late = apply(c, plan)
    imp = impact(c, before, plan)
    db.update(c, 'schedule_proposal', r['id'], {'status': 'accepted', 'impact': imp}, raw=True)
    return {'msg': _tr('已采纳：写入 {n} 项调整并重新排产', n=n) + (_tr('，预计逾期 {join}', join=_tr('、').join(late)) if late else ''), 'late': late}


ACTIONS[('schedule_proposal', 'accept')] = _accept


@router.get('/api/production/gantt2')
def gantt2(user=Depends(current_user)):
    """排产甘特（带冲突标记、设备、换型、停机段）。"""
    wot, opt = TABLES['work_order'], TABLES['wo_op']
    now = clock.now()
    with db.engine.connect() as c:
        wos = db.rows(c, 'work_order', wot.c.status.in_(('released', 'in_progress', 'completed')), order=(wot.c.priority, wot.c.due, wot.c.id), limit=40)
        attach_labels(c, 'work_order', wos)
        ops = []
        for wo in wos:
            rows = db.rows(c, 'wo_op', order=opt.c.seq, work_order=wo['id'])
            attach_labels(c, 'wo_op', rows)
            ops += rows
        dt = TABLES['downtime_plan']
        down = db.rows(c, 'downtime_plan', dt.c.end > now - timedelta(hours=12), order=dt.c.start)
        attach_labels(c, 'downtime_plan', down)
        kit = db.rows(c, 'wo_kitting')
        attach_labels(c, 'wo_kitting', kit)
        tools = db.rows(c, 'tooling')
        attach_labels(c, 'tooling', tools)
        shifts = db.rows(c, 'shift')
        pend = c.execute(sa.select(sa.func.count()).select_from(TABLES['schedule_proposal']).where(TABLES['schedule_proposal'].c.status == 'pending')).scalar()
        md = {k: c.execute(sa.select(sa.func.count()).select_from(TABLES[k])).scalar() for k in ('shift', 'downtime_plan', 'wo_kitting', 'skill', 'tooling', 'changeover')}
    late = [w['code'] for w in wos if w['status'] != 'completed' and w['due'] and any(o['plan_end'] and o['plan_end'] > w['due'] for o in ops if o['work_order'] == w['id'])]
    return {'orders': out(wos), 'ops': out(ops), 'now': db.jsonable(now), 'downtime': out(down), 'kitting': out(kit), 'tooling': out(tools), 'shifts': out(shifts),
            'late': late, 'pending_proposals': pend, 'master': md}


@router.post('/api/production/kitting/refresh')
def kitting_refresh(body: dict = Body(default={}), user=Depends(current_user)):
    """按 BOM 和库存刷新物料齐套（已下达/生产中的工单；手填的到料时间保留）。"""
    require_write(user, 'wo_kitting', 'update')
    wot, st = TABLES['work_order'], TABLES['stock']
    n = 0
    with db.engine.begin() as c:
        wos = db.rows(c, 'work_order', wot.c.status.in_(('released', 'in_progress', 'draft')), order=(wot.c.priority, wot.c.due))
        avail = {r[0]: float(r[1] or 0) for r in c.execute(sa.select(st.c.material, sa.func.sum(st.c.qty)).group_by(st.c.material)).all()}
        mats = {m['id']: m for m in db.rows(c, 'material')}
        kt = TABLES['wo_kitting']

        def explode(product, mult, depth=0):
            # 自制半成品（如铸造毛坯）由本工单的工艺路线自己做出来：展开到它的 BOM，只核对外购料
            res = {}
            for b in db.rows(c, 'bom', product=product):
                q = (b['qty'] or 0) * mult
                sub = db.rows(c, 'bom', limit=1, product=b['component'])
                if (mats.get(b['component']) or {}).get('mtype') == 'semi' and sub and depth < 4:
                    for k, v in explode(b['component'], q, depth + 1).items():
                        res[k] = res.get(k, 0) + v
                else:
                    res[b['component']] = res.get(b['component'], 0) + q
            return res
        for wo in wos:
            left = max(0, (wo['qty'] or 0) - (wo['qty_done'] or 0))
            need_map = explode(wo['product'], left)
            c.execute(kt.delete().where((kt.c.work_order == wo['id']) & kt.c.material.notin_(list(need_map) or [-1])))
            for comp, qn in need_map.items():
                b = {'component': comp}
                need = round(qn, 3)
                have = max(0.0, avail.get(b['component'], 0.0))
                ready = min(need, have)
                avail[b['component']] = have - ready
                status = 'ready' if ready + 1e-9 >= need else 'partial' if ready > 0 else 'short'
                ex = db.get(c, 'wo_kitting', work_order=wo['id'], material=b['component'])
                if ex:
                    db.update(c, 'wo_kitting', ex['id'], {'qty_need': need, 'qty_ready': ready, 'status': status}, raw=True)
                else:
                    db.insert(c, 'wo_kitting', {'work_order': wo['id'], 'material': b['component'], 'qty_need': need, 'qty_ready': ready, 'status': status}, raw=True)
                n += 1
    return {'ok': True, 'lines': n}


@router.get('/api/production/constraints')
def constraints(user=Depends(current_user)):
    """排产用到的主数据一览（页面"约束"面板）。"""
    with db.engine.connect() as c:
        res = {}
        for k in ('shift', 'downtime_plan', 'wo_kitting', 'skill', 'tooling', 'changeover'):
            rows = db.rows(c, k, limit=500)
            attach_labels(c, k, rows)
            res[k] = out(rows)
    return res
