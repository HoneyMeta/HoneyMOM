"""触发器：事件（采集入库时直接推送 + 定期扫数据库）、定时（Cron / 按班次）、审批超时。

防风暴：同一智能体同一对象（去重键）的事件先防抖合并，触发后进入冷却期；同一智能体同时运行数有上限。
"""
from ..i18n import _tr
import asyncio
import logging
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta

import sqlalchemy as sa

from .. import db
from ..db import clock
from ..schema import TABLES, agent_event_t, agent_run_t, agent_t, agent_version_t, approval_t
from . import engine

log = logging.getLogger('mes.agent.rt')


# ---------------- Cron ----------------
def _field(spec, lo, hi):
    vals = set()
    for part in spec.split(','):
        step = 1
        if '/' in part:
            part, st = part.split('/', 1)
            step = int(st)
            if step <= 0:
                raise ValueError
        if part in ('*', ''):
            a, b = lo, hi
        elif '-' in part:
            a, b = (int(x) for x in part.split('-', 1))
        else:
            a = b = int(part)
        if a < lo or b > hi or a > b:
            raise ValueError
        vals.update(range(a, b + 1, step))
    return vals


def cron_parse(expr):
    parts = (expr or '').split()
    if len(parts) != 5:
        raise ValueError(_tr('需要 5 段'))
    mi, hr, dom, mon, dow = parts
    return (_field(mi, 0, 59), _field(hr, 0, 23), _field(dom, 1, 31), _field(mon, 1, 12), {d % 7 for d in _field(dow, 0, 7)}, dom != '*', dow != '*')


def cron_ok(expr):
    try:
        cron_parse(expr)
        return True
    except (ValueError, TypeError):
        return False


def cron_match(expr, t: datetime):
    mi, hr, dom, mon, dow, rdom, rdow = cron_parse(expr)
    if t.minute not in mi or t.hour not in hr or t.month not in mon:
        return False
    d_ok, w_ok = t.day in dom, (t.isoweekday() % 7) in dow
    if rdom and rdow:
        return d_ok or w_ok
    return d_ok and w_ok


# ---------------- 运行时 ----------------
class Runtime:
    def __init__(self):
        self.queue: deque = deque()
        self.pending: dict = {}           # (agent, key) -> [deadline, payload, merged]
        self.last_fire: dict = {}         # (agent, key) -> wall time
        self.agents: list = []            # [(agent row, version dsl)]
        self.agents_at = 0.0
        self.cursor: dict = {}
        self.sched_last: datetime | None = None
        self.tick_n = 0
        self.lock = threading.Lock()
        self.started = False
        self.stats = {'events': 0, 'started': 0, 'suppressed': 0}

    # ----- 发布的智能体缓存 -----
    def refresh(self, force=False):
        if not force and time.time() - self.agents_at < 10:
            return
        with db.engine.connect() as c:
            rows = [dict(r) for r in c.execute(sa.select(agent_t).where(agent_t.c.enabled.is_(True) & agent_t.c.published_version.isnot(None))).mappings()]
            out = []
            for a in rows:
                v = c.execute(sa.select(agent_version_t).where(agent_version_t.c.id == a['published_version'])).mappings().first()
                if v:
                    out.append((a, dict(v)))
        self.agents = out
        self.agents_at = time.time()

    def subscribed(self, kind):
        return [(a, v) for a, v in self.agents if _bp(v['dsl']).get('trigger') == 'event' and _bp(v['dsl']).get('event') == kind]

    # ----- 事件入口（可在任意线程调用） -----
    def emit(self, kind, payload):
        self.queue.append((kind, payload))

    async def loop(self):
        if self.started:
            return
        self.started = True
        self.init_cursors()
        while True:
            await asyncio.sleep(1)
            try:
                await asyncio.get_running_loop().run_in_executor(None, self.tick)
            except Exception:
                log.exception('agent runtime tick')

    def tick(self):
        from ..services.ai_switch import enabled
        self.tick_n += 1
        if not enabled('agents'):            # 智能体编排关闭：什么都不跑（事件丢弃）
            self.queue.clear()
            return
        auto = enabled('automation')         # 自动化关闭：不按事件/定时自动运行（手动调试、聊天调用照常；审批超时照常处理）
        self.refresh()
        while self.queue:
            kind, payload = self.queue.popleft()
            if auto:
                self.dispatch(kind, payload)
        if self.tick_n % 10 == 0:            # 轮询照常推进游标（关闭期间的事件下一轮丢弃，开启时不会一下子补触发一大批）
            self.poll_db()
        if auto and self.tick_n % 60 == 0:
            self.poll_slow()
        if self.tick_n % 5 == 0:
            if auto:
                self.schedules()
            else:                            # 关闭期间错过的定时不补跑
                self.cursor['sched'] = datetime.now().replace(second=0, microsecond=0)
            self.timeouts()
        if auto:
            self.flush_pending()

    # ----- 分发 -----
    def dispatch(self, kind, payload, only_agent=None):
        self.stats['events'] += 1
        for a, v in self.subscribed(kind):
            if only_agent and a['id'] != only_agent:
                continue
            bp = _bp(v['dsl'])
            if not match(bp, kind, payload):
                continue
            key = dedup_key(bp, kind, payload)
            k = (a['id'], key)
            with self.lock:
                if k in self.pending:
                    self.pending[k][1] = payload
                    self.pending[k][2] += 1
                    self.log_event(a['id'], kind, key, payload, 'merged')
                    continue
                cd = float(bp.get('cooldown_s') if bp.get('cooldown_s') not in (None, '') else 600)
                last = self.last_fire.get(k)
                if last and time.time() - last < cd:
                    self.stats['suppressed'] += 1
                    self.log_event(a['id'], kind, key, payload, 'cooldown')
                    continue
                self.pending[k] = [time.time() + float(bp.get('debounce_s') if bp.get('debounce_s') not in (None, '') else 3), payload, 0, kind]

    def flush_pending(self):
        now_ = time.time()
        due = []
        with self.lock:
            for k, (dl, payload, merged, kind) in list(self.pending.items()):
                if dl <= now_:
                    due.append((k, payload, merged, kind))
                    del self.pending[k]
        for (aid, key), payload, merged, kind in due:
            pair = next(((a, v) for a, v in self.agents if a['id'] == aid), None)
            if not pair:
                continue
            a, v = pair
            bp = _bp(v['dsl'])
            if self.running(aid) >= int(bp.get('max_concurrent') or 3):
                self.stats['suppressed'] += 1
                self.log_event(aid, kind, key, payload, 'busy')
                continue
            self.last_fire[(aid, key)] = time.time()
            if kind == 'equipment_state':
                payload = _fill_alarm(payload)
            if excluded(bp, payload):
                self.log_event(aid, kind, key, payload, 'filtered')
                self.last_fire.pop((aid, key), None)
                continue
            rid = engine.start_run(aid, v['dsl'], version=v['id'], mode='live', trigger='event', inputs=payload, user=_tr('系统'), run_as=v['published_by'] or 'admin',
                                   key=key)
            self.stats['started'] += 1
            self.log_event(aid, kind, key, {**payload, '_merged': merged}, 'started', rid)

    def running(self, aid):
        with db.engine.connect() as c:
            return c.execute(sa.select(sa.func.count()).select_from(agent_run_t).where((agent_run_t.c.agent == aid) & agent_run_t.c.status.in_(('queued', 'running')))).scalar()

    def log_event(self, aid, kind, key, payload, outcome, run=None):
        try:
            with db.engine.begin() as c:
                c.execute(agent_event_t.insert().values(agent=aid, kind=kind, key=str(key)[:190], payload=engine._safe(payload), outcome=outcome, run=run,
                                                        created_at=engine.now()))
        except Exception:
            log.exception('log_event')

    # ----- 扫数据库 -----
    def init_cursors(self):
        with db.engine.connect() as c:
            for ent in ('andon', 'ncr'):
                t = TABLES[ent]
                self.cursor[ent] = c.execute(sa.select(sa.func.max(t.c.id))).scalar() or 0
        self.cursor['sched'] = datetime.now().replace(second=0, microsecond=0)

    def poll_db(self):
        self.refresh()
        for ent, kind in (('andon', 'andon'), ('ncr', 'ncr')):
            t = TABLES[ent]
            with db.engine.connect() as c:
                rows = db.rows(c, ent, t.c.id > self.cursor.get(ent, 0), order=t.c.id, limit=200)
                for r in rows:
                    self.cursor[ent] = max(self.cursor.get(ent, 0), r['id'])
                if not self.subscribed(kind):
                    continue
                for r in rows:
                    self.emit(kind, andon_payload(c, r) if ent == 'andon' else ncr_payload(c, r))

    def poll_slow(self):
        if self.subscribed('material_low'):
            from .queries import q_stock
            for r in q_stock({'low_only': True})['rows']:
                self.emit('material_low', {'material_code': r['material_code'], 'material_name': r['material_name'], 'qty': r['qty'], 'safety_stock': r['safety_stock'],
                                           'unit': r['unit'], 'time': db.jsonable(clock.now())})
        if self.subscribed('wo_late'):
            for p in late_orders():
                self.emit('wo_late', p)
        subs = self.subscribed('oee_low')
        if subs:
            hours = max(float(_bp(v['dsl']).get('hours') or 2) for _, v in subs)
            from .queries import q_oee
            for r in q_oee({'hours': hours})['rows']:
                self.emit('oee_low', {'equipment_code': r['code'], 'equipment_name': r['name'], 'oee': round(r['OEE'] * 100, 1), 'A': r['A'], 'P': r['P'], 'Q': r['Q'],
                                      'hours': hours, 'loss': r['loss_h'], 'time': db.jsonable(clock.now())})

    # ----- 定时 -----
    def schedules(self):
        nowm = datetime.now().replace(second=0, microsecond=0)
        last = self.cursor.get('sched') or nowm
        if nowm <= last:
            return
        minutes = []
        t = last + timedelta(minutes=1)
        while t <= nowm and len(minutes) < 120:
            minutes.append(t)
            t += timedelta(minutes=1)
        self.cursor['sched'] = nowm
        shifts = None
        for a, v in self.agents:
            bp = _bp(v['dsl'])
            if bp.get('trigger') != 'schedule':
                continue
            for m in minutes:
                fire, key = False, ''
                if (bp.get('schedule_mode') or 'cron') == 'cron':
                    try:
                        fire = cron_match(bp.get('cron') or '', m)
                    except (ValueError, TypeError):
                        fire = False
                    key = 'cron:' + m.strftime('%Y%m%d%H%M')
                else:
                    if shifts is None:
                        with db.engine.connect() as c:
                            shifts = db.rows(c, 'shift')
                    for s in shifts:
                        hm = s['end'] if (bp.get('shift_at') or 'end') == 'end' else s['start']
                        if not hm or ':' not in hm:
                            continue
                        h, mi = (int(x) for x in hm.split(':')[:2])
                        at = m.replace(hour=h, minute=mi) + timedelta(minutes=float(bp.get('offset_min') or 0))
                        if at.hour == m.hour and at.minute == m.minute:
                            fire, key = True, f"shift:{s['code']}:{m:%Y%m%d}"
                            break
                if fire:
                    if self.running(a['id']) >= int(bp.get('max_concurrent') or 3):
                        self.log_event(a['id'], 'schedule', key, {}, 'busy')
                        continue
                    rid = engine.start_run(a['id'], v['dsl'], version=v['id'], mode='live', trigger='schedule', inputs={'scheduled_at': m.strftime('%Y-%m-%d %H:%M')},
                                           user=_tr('系统'), run_as=v['published_by'] or 'admin', key=key)
                    self.log_event(a['id'], 'schedule', key, {'at': m.strftime('%Y-%m-%d %H:%M')}, 'started', rid)

    # ----- 审批超时 -----
    def timeouts(self):
        with db.engine.connect() as c:
            rows = [dict(r) for r in c.execute(sa.select(approval_t).where((approval_t.c.status == 'pending') & approval_t.c.due_at.isnot(None)
                                                                         & (approval_t.c.due_at <= engine.now()))).mappings()]
        for ap in rows:
            how = (ap['fields'] or {}).get('on_timeout') if ap['kind'] == 'approval' else 'reject'
            decide(ap['id'], 'approve' if how == 'approve' else 'reject', None, _tr('超时未处理，') + (_tr('自动通过') if how == 'approve' else _tr('按驳回处理')), None, system=True)


RT = Runtime()


def _bp(dsl):
    return ((dsl or {}).get('components') or {}).get('begin', {}).get('params') or {}


def excluded(bp, p):
    words = [w.strip() for w in re.split(r'[,，、]+', bp.get('exclude') or '') if w.strip()]
    text = str(p.get('alarm') or '') + str(p.get('msg') or '')
    return bool(words) and any(w in text for w in words)


def match(bp, kind, p):
    if kind == 'alarm' and excluded(bp, p):      # 设备状态事件的报警要等防抖后再取，到触发前再判断
        return False
    eqs = [x.strip().upper() for x in re.split(r'[,，\s]+', bp.get('equipment') or '') if x.strip()]
    if eqs and str(p.get('equipment_code') or '').upper() not in eqs:
        return False
    if kind == 'equipment_state':
        if bp.get('to_state') and p.get('to') != bp['to_state']:
            return False
        if bp.get('etypes') and p.get('etype') not in bp['etypes']:
            return False
    if kind == 'alarm' and bp.get('etypes') and p.get('etype') not in bp['etypes']:
        return False
    if kind == 'oee_low':
        try:
            if float(p.get('oee')) >= float(bp.get('threshold') or 60):
                return False
        except (TypeError, ValueError):
            return False
    if bp.get('keyword') and kind in ('alarm', 'andon'):
        if bp['keyword'] not in (str(p.get('msg') or '') + str(p.get('description') or '')):
            return False
    return True


DEFAULT_KEY = {'andon': '{code}', 'equipment_state': '{equipment_code}', 'oee_low': '{equipment_code}', 'ncr': '{code}', 'material_low': '{material_code}',
               'wo_late': '{code}', 'alarm': '{equipment_code}'}


def dedup_key(bp, kind, p):
    tpl = bp.get('dedup_key') or DEFAULT_KEY.get(kind, '{code}')
    return re.sub(r'\{(\w+)\}', lambda m: str(p.get(m.group(1), '')), tpl)[:190]


# ---------------- 事件载荷 ----------------
def andon_payload(c, r):
    eq = db.get(c, 'equipment', r['equipment']) if r['equipment'] else None
    return {'andon_id': r['id'], 'code': r['code'], 'atype': r['atype'], 'description': r['description'], 'equipment_code': eq['code'] if eq else '',
            'equipment_name': eq['name'] if eq else '', 'station': r['station'] or '', 'raised_by': r['raised_by'] or '', 'time': db.jsonable(r['raised_at'])}


def ncr_payload(c, r):
    d = db.get(c, 'defect_code', r['defect']) if r['defect'] else None
    wo = db.get(c, 'work_order', r['work_order']) if r['work_order'] else None
    return {'ncr_id': r['id'], 'code': r['code'], 'sn': r['sn'] or '', 'defect': d['code'] if d else '', 'defect_name': d['name'] if d else '',
            'station': r['station'] or '', 'work_order': wo['code'] if wo else '', 'qty': r['qty'], 'time': db.jsonable(r['found_at'])}


def state_payload(eq, frm, to, raw, alarm, ts):
    return {'equipment_id': eq['id'], 'equipment_code': eq['code'], 'equipment_name': eq['name'], 'etype': eq['etype'], 'from': frm, 'to': to, 'state': raw,
            'alarm': alarm or '', 'time': db.jsonable(ts)}


def late_orders():
    from ..services.production import board
    now_ = db.jsonable(clock.now())
    out = []
    for w in board(user=None):
        late = w.get('late') or (w.get('due') and w['due'] < now_ and (w.get('qty_done') or 0) < (w.get('qty') or 0))
        if late:
            out.append(wo_payload(w))
    return out


def wo_payload(w):
    return {'work_order_id': w['id'], 'code': w['code'], 'customer': w.get('customer') or '', 'due': w.get('due'), 'eta': w.get('eta'), 'qty': w.get('qty'),
            'qty_done': w.get('qty_done') or 0, 'priority': w.get('priority'), 'time': db.jsonable(clock.now())}


# ---------------- 采集入库的钩子 ----------------
def on_equipment_state(eid, prev_raw, raw_state, ts):
    """ingest 在设备状态变化时调用（采集循环里，只做轻量查询然后入队）。"""
    from ..ingest import STATE_MAP
    to = STATE_MAP.get(raw_state, 'idle')
    frm = STATE_MAP.get(prev_raw, '') if prev_raw else ''
    if frm == to or not RT.subscribed('equipment_state'):
        return
    try:
        with db.engine.connect() as c:
            eq = db.get(c, 'equipment', eid)
            al = TABLES['alarm']
            msg = c.execute(sa.select(al.c.msg).where((al.c.equipment == eid) & al.c.end.is_(None)).order_by(al.c.id.desc()).limit(1)).scalar()
        if eq:
            RT.emit('equipment_state', state_payload(eq, frm, to, raw_state, msg, ts))
    except Exception:
        log.exception('on_equipment_state')


def on_alarm(eid, msg, ts):
    if not msg or not RT.subscribed('alarm'):
        return
    try:
        with db.engine.connect() as c:
            eq = db.get(c, 'equipment', eid)
        if eq:
            RT.emit('alarm', {'equipment_code': eq['code'], 'equipment_name': eq['name'], 'etype': eq['etype'], 'msg': msg, 'time': db.jsonable(ts)})
    except Exception:
        log.exception('on_alarm')


def _fill_alarm(payload):
    """状态和报警两条消息先后到达：防抖结束、触发前再取一次当前未恢复的报警。"""
    if not payload.get('equipment_id'):
        return payload
    al = TABLES['alarm']
    with db.engine.connect() as c:
        msg = c.execute(sa.select(al.c.msg).where((al.c.equipment == payload['equipment_id']) & al.c.end.is_(None)).order_by(al.c.id.desc()).limit(1)).scalar()
    return {**payload, 'alarm': msg if msg is not None else (payload.get('alarm') or '')}


# ---------------- 审批 ----------------
def decide(aid, decision, payload, reason, user, system=False):
    """审批/填写：通过（可带修改后的内容）或驳回（原因回给运行）。返回审批记录。"""
    from ..services.ops import audit
    with db.engine.begin() as c:
        ap = c.execute(sa.select(approval_t).where(approval_t.c.id == aid)).mappings().first()
        if not ap:
            raise ValueError(_tr('审批单不存在'))
        ap = dict(ap)
        if ap['status'] != 'pending':
            raise ValueError(_tr('这张审批单已经处理过了'))
        who = _tr('系统') if system else (user.get('name') or user['username'])
        res = {'payload': payload if payload is not None else ap['payload']}
        st = 'approved' if decision == 'approve' else 'rejected'
        r = c.execute(approval_t.update().where((approval_t.c.id == aid) & (approval_t.c.status == 'pending')).values(
            status=st, decided_by=who, decided_at=engine.now(), reason=reason or '', result=res))
        if r.rowcount == 0:
            raise ValueError(_tr('这张审批单已经处理过了'))
        audit(c, 'system' if system else user['username'], 'approval', f'approval {aid}',
              {'decision': st, 'reason': reason, 'edited': payload is not None and payload != ap['payload'], 'run': ap['run']})
        ap.update(status=st, decided_by=who, reason=reason or '', result=res)
    engine.resume(ap['run'], ap)
    return ap
