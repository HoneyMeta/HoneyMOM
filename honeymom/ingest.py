"""数据入库：把采集到的点位和网关事件变成 MES 业务记录。

点位（on_tag）：运行状态 → 设备状态记录与 OEE；累计电能 → 小时电耗；报警 → 报警记录；刀具计数、主轴负载 → 刀具管理和 AI 监测。
网关事件（on_part / on_event）：零件事件 → 一件一码、自动报工、检验、不合格品、返修、库存、AGV；
生产日志 → 炉次、光谱、球化、换刀、发运。
5.0：每个点位值同时进时序库（ts.buffer → ts_raw / 状态区间 / 汇总）；采集来的消息都经过 GATE：数据库不可用时按顺序写磁盘缓存（spool），
恢复后按原顺序补传；网关消息带去重（ingest_seen），重放不会重复入库。
"""
from .i18n import _tr
import hashlib
import json
import time as _time
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta

import sqlalchemy as sa

from . import compat, db, ts
from .db import clock, parse_dt
from .schema import TABLES, ingest_seen_t

log = logging.getLogger('mes.ingest')

STATE_MAP = {'Executing': 'running', 'Idle': 'idle', 'Setup': 'setup', 'Stopped': 'down', 'Planned': 'offline', 'Starved': 'idle', 'Blocked': 'idle'}
# 5.0（M2）：点位的语义（运行状态 / 报警 / 电能 / 产量 / 刀具 / 趋势采样）不再按虚拟工厂的点名判断，而是按工厂模型：
# 点表的"用途"+ 设备模板的点位模式（factory/model.py INDEX.role_of）。
# 零件事件 → 工序类型
EVENT_PROC = {'poured': 'molding', 'received': 'receiving', 'shakeout': 'cooling', 'cleaned': 'cleaning', 'machined': 'machining',
              'deburred': 'deburr', 'inspected': 'inspection', 'stored': 'storage'}
PART_CREATED: list = []          # fn(c, 零件)：新零件投产时调用（同一事务；如一件一码自动打码 services/exec_label.on_part_created）


class Ingest:
    def __init__(self):
        self.equip_by_src: dict[str, int] = {}        # 'VF:CNC1' -> equipment.id
        self.equip_code: dict[int, str] = {}
        self.state: dict[int, tuple] = {}             # equipment -> (state, since, row id)
        self.alarm: dict[int, tuple] = {}             # equipment -> (msg, row id)
        self.energy_last: dict[int, float] = {}
        self.energy_acc: dict[tuple, float] = defaultdict(float)
        self.latest: dict[str, tuple] = {}            # key -> (equipment, name, value, num, ts)
        self.dirty_latest: set = set()
        self.sample_last: dict[str, datetime] = {}
        self.active_tool: dict[int, str] = {}
        self.wo_born: dict[int, int] = {}
        self.cache_wo: dict[int, int] = {}            # product -> 当前投产工单
        self.stats = defaultdict(int)
        self.products: list[tuple] = []
        self.reloaded = 0.0
        self.missing: set = set()
        self.pending_state: dict[int, tuple] = {}
        self.count_last: dict[str, float] = {}         # 计数点（用途 count → 产量）上一次的值
        self.energy_key: dict[int, str] = {}            # 每台设备只用一个电能点 / 一个产量计数点（多个时优先标准名 / 语义 output）
        self.output_key: dict[int, str] = {}
        self.output_acc: dict[tuple, float] = defaultdict(float)

    # ---------- 基础映射 ----------
    def reload(self):
        import time
        self.reloaded = time.time()
        self.missing = set()
        with db.engine.connect() as c:
            eq = db.rows(c, 'equipment')
            self.equip_by_src = {e['source']: e['id'] for e in eq if e['source']}
            self.equip_code = {e['id']: e['code'] for e in eq}
            mats = db.rows(c, 'material', mtype='fg')
            self.products = sorted(((m['code'], m['id']) for m in mats), key=lambda x: -len(x[0]))
            for e in eq:
                last = c.execute(sa.select(TABLES['equip_state']).where(TABLES['equip_state'].c.equipment == e['id'])
                                 .order_by(TABLES['equip_state'].c.id.desc()).limit(1)).mappings().first()
                if last and last['end'] is None:
                    self.state[e['id']] = (last['state'], last['start'], last['id'])
        self.cache_wo.clear()
        self.wo_born.clear()

    def equip(self, src_key: str) -> int | None:
        eid = self.equip_by_src.get(src_key)
        if eid is None and src_key not in self.missing:
            # 设备是后来导入的：限频重新加载映射
            import time
            if time.time() - self.reloaded > 5:
                self.reload()
                eid = self.equip_by_src.get(src_key)
            if eid is None:
                self.missing.add(src_key)
        return eid

    def equip_code_of(self, code: str) -> int | None:
        """工位编码 = 设备编码（交付包 / 点表导入的设备没有 source 字段）。"""
        if not code:
            return None
        from .factory.model import INDEX
        INDEX.ensure()
        return INDEX.eq_by_code.get(code)

    def product_of(self, ptype: str | None) -> int | None:
        ptype = ptype or ''
        for code, mid in self.products:
            if ptype.startswith(code) or code in ptype:
                return mid
        return self.products[0][1] if self.products else None

    # ---------- 点位 ----------
    def on_tag(self, eid: int | None, name: str, value, ts: datetime | None, key: str, q: int = 0):
        if ts:
            clock.feed(ts)
        ts = ts or clock.now()
        from .factory.model import INDEX, canon_alarm, canon_state
        role, sem, caps = INDEX.role_of(key, eid, name)
        if role == 'state' and value is not None:
            value = canon_state(value)
        ts_mod.buffer(key, ts, value, q=q or 0, equipment=eid, name=name)
        self.stats['tag_values'] += 1
        num = None
        if isinstance(value, bool):
            num = 1.0 if value else 0.0
        elif isinstance(value, (int, float)):
            num = float(value)
        self.latest[key] = (eid, name, '' if value is None else str(value)[:200], num, ts)
        self.dirty_latest.add(key)
        if eid is None or role is None:
            return
        if role == 'state':
            self._state(eid, str(value), ts)
        elif role == 'alarm':
            self._alarm(eid, canon_alarm(value), ts)
        elif role == 'energy' and num is not None and self._main_key(self.energy_key, eid, key, name == 'Common/EnergyKWh'):
            last = self.energy_last.get(eid)
            self.energy_last[eid] = num
            if last is not None and 0 <= num - last < 10000:
                self.energy_acc[(eid, ts.replace(minute=0, second=0, microsecond=0))] += num - last
        elif sem == 'tool_active':
            self.active_tool[eid] = str(value or '')
        elif sem == 'tool_life' and num is not None:
            self._tool_count(eid, caps.get('tool') or (name.split('/')[1] if name.count('/') >= 2 else name), int(num))
        elif sem == 'tool_limit' and num is not None:
            self._tool_limit(eid, caps.get('tool') or (name.split('/')[1] if name.count('/') >= 2 else name), int(num))
        elif role == 'count' and sem in (None, '', 'output') and num is not None and self._main_key(self.output_key, eid, key, sem == 'output'):
            last = self.count_last.get(key)
            self.count_last[key] = num
            if last is not None and 0 < num - last < 100000:
                self.output_acc[(eid, ts.replace(minute=0, second=0, microsecond=0))] += num - last
        if num is not None and (sem == 'sample' or (sem is None and INDEX.sample(key, eid, name))):
            skey = f'{eid}|{name}'
            last = self.sample_last.get(skey)
            if last is None or (ts - last).total_seconds() >= 10 or ts < last:
                self.sample_last[skey] = ts
                sname = name
                if role == 'load' and self.active_tool.get(eid):
                    sname = f'{name}@{self.active_tool[eid]}'
                with db.engine.begin() as c:
                    c.execute(sa.insert(db.metadata.tables['tag_sample']).values(equipment=eid, name=sname, ts=ts, num=num))

    def _main_key(self, reg, eid, key, preferred):
        cur = reg.get(eid)
        if cur is None or (preferred and cur != key and not reg.get(('p', eid))):
            reg[eid] = key
            if preferred:
                reg[('p', eid)] = True
            if reg is self.energy_key:
                self.energy_last.pop(eid, None)
            return True
        return cur == key

    def _state(self, eid, st, ts):
        cur = self.state.get(eid)
        if cur is None:
            # 第一次看到这台设备：MQTT 保留消息的时间可能是很久以前，等时钟稳定后（flush 时）从"开始观测"算起
            self.pending_state[eid] = (st, ts)
            return
        if cur[0] == st:
            return
        es = TABLES['equip_state']
        with db.engine.begin() as c:
            if cur:
                dur = max(0.0, (ts - cur[1]).total_seconds())
                c.execute(es.update().where(es.c.id == cur[2]).values(end=ts, dur_s=dur))
            rid = c.execute(es.insert().values(equipment=eid, state=st, start=ts, created_at=ts, updated_at=ts)).inserted_primary_key[0]
            c.execute(TABLES['equipment'].update().where(TABLES['equipment'].c.id == eid).values(status=STATE_MAP.get(st, 'idle')))
        self.state[eid] = (st, ts, rid)
        from .agent.runtime import on_equipment_state
        on_equipment_state(eid, cur[0] if cur else None, st, ts)

    def _alarm(self, eid, msg, ts):
        cur = self.alarm.get(eid)
        if (cur[0] if cur else '') == msg:
            return
        t = TABLES['alarm']
        with db.engine.begin() as c:
            if cur:
                st = c.execute(sa.select(t.c.start).where(t.c.id == cur[1])).scalar()
                c.execute(t.update().where(t.c.id == cur[1]).values(end=ts, dur_s=(ts - st).total_seconds() if st else None))
            rid = c.execute(t.insert().values(equipment=eid, msg=msg, start=ts, created_at=ts, updated_at=ts)).inserted_primary_key[0] if msg else None
        self.alarm[eid] = (msg, rid) if msg else None
        if msg:
            from .agent.runtime import on_alarm
            on_alarm(eid, msg, ts)

    def _tool_count(self, eid, tool, n):
        t = TABLES['tool']
        with db.engine.begin() as c:
            c.execute(t.update().where((t.c.equipment == eid) & (t.c.code == tool)).values(used=n))

    def _tool_limit(self, eid, tool, n):
        t = TABLES['tool']
        with db.engine.begin() as c:
            c.execute(t.update().where((t.c.equipment == eid) & (t.c.code == tool) & ((t.c.life_limit != n) | t.c.life_limit.is_(None))).values(life_limit=n))

    def flush(self):
        """定期把最新值和电耗写库（失败时数据留在内存，下次再写）。"""
        if self.pending_state:
            pend, self.pending_state = self.pending_state, {}
            now = clock.now()
            es = TABLES['equip_state']
            with db.engine.begin() as c:
                for eid, (st, ts) in pend.items():
                    if eid in self.state:
                        continue
                    start = ts if (now - ts).total_seconds() < 120 else now
                    rid = c.execute(es.insert().values(equipment=eid, state=st, start=start, created_at=start, updated_at=start)).inserted_primary_key[0]
                    c.execute(TABLES['equipment'].update().where(TABLES['equipment'].c.id == eid).values(status=STATE_MAP.get(st, 'idle')))
                    self.state[eid] = (st, start, rid)
        if self.dirty_latest:
            tl = db.metadata.tables['tag_latest']
            keys, self.dirty_latest = self.dirty_latest, set()
            try:
                self._flush_latest(tl, keys)
            except Exception:
                self.dirty_latest |= keys
                raise
        if self.output_acc:
            acc, self.output_acc = self.output_acc, defaultdict(float)
            try:
                self._flush_output(acc)
            except Exception:
                for k, v in acc.items():
                    self.output_acc[k] += v
                raise
        if self.energy_acc:
            acc, self.energy_acc = self.energy_acc, defaultdict(float)
            try:
                self._flush_energy(acc)
            except Exception:
                for k, v in acc.items():
                    self.energy_acc[k] += v
                raise

    def _flush_latest(self, tl, keys):
        if True:
            with db.engine.begin() as c:
                for k in keys:
                    eid, name, val, num, ts = self.latest[k]
                    r = c.execute(tl.update().where(tl.c.key == k).values(value=val, num=num, ts=ts, equipment=eid, name=name))
                    if r.rowcount == 0:
                        c.execute(tl.insert().values(key=k, equipment=eid, name=name, value=val, num=num, ts=ts))

    def _flush_output(self, acc):
        from .schema_factory import equip_output_t as t
        with db.engine.begin() as c:
            for (eid, hour), qty in acc.items():
                r = c.execute(t.update().where((t.c.equipment == eid) & (t.c.hour == hour)).values(qty=t.c.qty + qty))
                if r.rowcount == 0:
                    c.execute(t.insert().values(equipment=eid, hour=hour, qty=qty))

    def _flush_energy(self, acc):
        if True:
            t = TABLES['energy_hour']
            with db.engine.begin() as c:
                for (eid, hour), kwh in acc.items():
                    r = c.execute(t.update().where((t.c.equipment == eid) & (t.c.hour == hour)).values(kwh=t.c.kwh + kwh))
                    if r.rowcount == 0:
                        c.execute(t.insert().values(equipment=eid, hour=hour, kwh=kwh, created_at=hour, updated_at=hour))

    # ---------- 网关：零件事件 ----------
    def active_wo(self, c, product: int | None) -> int | None:
        if product is None:
            return None
        wid = self.cache_wo.get(product)
        if wid:
            wo = db.get(c, 'work_order', wid)
            if wo and wo['status'] in ('released', 'in_progress') and self.wo_born.get(wid, 0) < wo['qty']:
                return wid
        t = TABLES['work_order']
        cands = db.rows(c, 'work_order', t.c.status.in_(('released', 'in_progress')), order=(t.c.priority, t.c.start_plan, t.c.id), product=product)
        for wo in cands:
            if wo['id'] not in self.wo_born:
                pt = TABLES['part']
                self.wo_born[wo['id']] = c.execute(sa.select(sa.func.count()).select_from(pt).where(pt.c.work_order == wo['id'])).scalar()
            if self.wo_born[wo['id']] < wo['qty']:
                self.cache_wo[product] = wo['id']
                return wo['id']
        return None

    def on_part(self, src: str, m: dict):
        ts = parse_dt(m.get('t')) or clock.now()
        clock.feed(ts)
        sn, ev, station, data = m['sn'], m['event'], m.get('station', ''), m.get('data') or {}
        pt = TABLES['part']
        with db.engine.begin() as c:
            if not seen_once(c, 'part', src, m):
                self.stats['duplicates'] += 1
                return
            self.stats['part_events'] += 1
            part = db.get(c, 'part', sn=sn)
            if ev in ('poured', 'received') and part and part['born_at'] and (ts - part['born_at']).total_seconds() > 60:
                # 编号重复（仿真重启或扫码错误）：旧记录改名归档
                c.execute(pt.update().where(pt.c.id == part['id']).values(sn=f"{sn}~{part['id']}"))
                pe = TABLES['part_event']      # 旧件的履历跟着一起改名，追溯不丢
                c.execute(pe.update().where(pe.c.sn == sn).values(sn=f"{sn}~{part['id']}"))
                part = None
            if part is None:
                prod = self.product_of(m.get('type'))
                wo = self.active_wo(c, prod)
                a = m.get('attrs') or {}
                row = {'sn': sn, 'product': prod, 'work_order': wo, 'status': 'wip', 'station': station, 'born_at': ts, 'rework_count': 0,
                       **{k: a.get(k) for k in ('heat', 'ladle', 'mold', 'cavity', 'pour_temp', 'mg', 'sand_moisture', 'compactability', 'ce')}}
                if a.get('supplier'):
                    row['heat'] = a['supplier']
                pid = db.insert(c, 'part', row, raw=True)
                part = db.get(c, 'part', pid)
                for h in PART_CREATED:
                    h(c, part)
                if wo:
                    self.wo_born[wo] = self.wo_born.get(wo, 0) + 1
                    wot = TABLES['work_order']
                    c.execute(wot.update().where((wot.c.id == wo) & (wot.c.status == 'released')).values(status='in_progress'))
                if row.get('heat') and ev == 'poured':
                    ht = TABLES['heat']
                    c.execute(ht.update().where(ht.c.code == row['heat']).values(castings=sa.func.coalesce(ht.c.castings, 0) + 1))
            c.execute(TABLES['part_event'].insert().values(sn=sn, t=ts, station=station, event=ev, data=data, created_at=ts, updated_at=ts))
            upd = {'station': station}
            eid = self.equip(f'{src}:{station}') or self.equip_code_of(station)
            if ev == 'machined':
                upd['machine'] = station
            elif ev == 'inspected':
                if data.get('result') == 'OK':
                    upd['status'] = 'good'
            elif ev == 'rework':
                upd['status'] = 'rework'
                upd['rework_count'] = (part['rework_count'] or 0) + 1
            elif ev == 'scrap':
                upd.update(status='scrap', scrap_cause=data.get('cause'), finished_at=ts)
            elif ev == 'stored':
                upd['status'] = 'fg'
            elif ev == 'shipped':
                upd.update(status='shipped', finished_at=ts)
            elif ev == 'to_raw_store':
                upd['status'] = 'raw_store'
            elif ev == 'delivered' and part['status'] == 'raw_store':
                upd['status'] = 'wip'
            if part.get('status') == 'hold' and upd.get('status') not in (None, 'scrap'):
                upd.pop('status')        # 冻结批次的零件：现场流转照记，状态保持"冻结"，等质量解冻
            c.execute(pt.update().where(pt.c.id == part['id']).values(updated_at=ts, **upd))
            part.update(upd)
            # 自动报工
            proc = EVENT_PROC.get(ev)
            if proc and part['work_order']:
                ok = not (ev == 'inspected' and data.get('result') == 'NG')
                self._auto_report(c, part['work_order'], proc, eid, ok, ts)
                if ev == 'poured':        # 熔炼、球化没有单件事件：按浇注出的铸件一并计入
                    for up in ('melting', 'treatment'):
                        self._auto_report(c, part['work_order'], up, None, True, ts)
            if ev == 'scrap' and part['work_order']:
                wot = TABLES['work_order']
                c.execute(wot.update().where(wot.c.id == part['work_order']).values(qty_scrap=sa.func.coalesce(wot.c.qty_scrap, 0) + 1))
            if ev == 'stored' and part['work_order']:
                self._wo_done(c, part['work_order'], ts)
            # 质量
            from .services import quality
            if ev == 'machined' and data.get('bore') is not None:
                quality.auto_measure(c, part, station, 'BORE_DIA', float(data['bore']), ts)
            elif ev == 'inspected':
                quality.auto_inspection(c, part, station, data.get('result'), data.get('cause') or '', ts)
            elif ev == 'rework':
                quality.on_rework(c, part, station, data.get('reason') or '', ts)
            elif ev == 'scrap':
                quality.on_scrap(c, part, station, data.get('cause') or '', ts)
                if part.get('heat'):
                    ht = TABLES['heat']
                    c.execute(ht.update().where(ht.c.code == part['heat']).values(scrap=sa.func.coalesce(ht.c.scrap, 0) + 1))
            # 库存 / 物流
            from .services import warehouse
            if ev in ('to_raw_store', 'stored', 'shipped') or (ev == 'delivered' and data.get('from_store')):
                warehouse.on_part_move(c, part, ev, ts)
            if ev == 'delivered':
                warehouse.on_agv_delivery(c, data.get('agv'), data.get('to'), ts)

    def _auto_report(self, c, wo_id, proc, eid, ok, ts):
        ops = TABLES['wo_op']
        prt = TABLES['process']
        op = c.execute(sa.select(ops).join(prt, prt.c.id == ops.c.process).where((ops.c.work_order == wo_id) & (prt.c.ptype == proc))
                       .order_by(ops.c.seq).limit(1)).mappings().first()
        if not op:
            return
        vals = {'qty_good': (op['qty_good'] or 0) + (1 if ok else 0), 'qty_ng': (op['qty_ng'] or 0) + (0 if ok else 1), 'updated_at': ts}
        if not op['act_start']:
            vals['act_start'] = ts
        if op['status'] in ('waiting', 'ready'):
            vals['status'] = 'running'
        if vals['qty_good'] >= (op['qty_plan'] or 0) > 0:
            vals.update(status='done', act_end=ts)
        c.execute(ops.update().where(ops.c.id == op['id']).values(**vals))
        # 设备自动报工按固定间隔汇总成一条（默认 60 分钟，系统参数 report_auto_interval_min），避免每件一条；
        # 同一工单同一工序同一台设备每个间隔一条，界面上标"设备自动 · 每 N 分钟汇总"
        rt = TABLES['report']
        iv = self.report_interval(c)
        slot = ts.replace(minute=(ts.minute // iv) * iv if iv < 60 else 0, second=0, microsecond=0)
        r = c.execute(sa.select(rt.c.id).where((rt.c.wo_op == op['id']) & (rt.c.source == 'auto') & (rt.c.start == slot)
                                              & ((rt.c.equipment == eid) if eid else rt.c.equipment.is_(None)))).scalar()
        if r:
            c.execute(rt.update().where(rt.c.id == r).values(qty_good=rt.c.qty_good + (1 if ok else 0), qty_ng=rt.c.qty_ng + (0 if ok else 1), end=ts,
                                                          updated_at=ts))
        else:
            db.insert(c, 'report', {'work_order': wo_id, 'wo_op': op['id'], 'process': op['process'], 'equipment': eid, 'qty_good': 1 if ok else 0,
                                    'qty_ng': 0 if ok else 1, 'start': slot, 'end': ts, 'source': 'auto', 'interval_min': iv}, raw=True)
        if eid:
            from .services import shopfloor
            shopfloor.on_cycle(c, eid, op['process'], ts)

    def report_interval(self, c):
        import time as _time
        if not hasattr(self, '_iv') or _time.time() - self._iv[1] > 30:
            try:
                v = int(float(db.setting(c, 'report_auto_interval_min', 60) or 60))
            except ValueError:
                v = 60
            v = v if v in (5, 10, 15, 20, 30, 60) else 60
            self._iv = (v, _time.time())
        return self._iv[0]

    def _wo_done(self, c, wo_id, ts):
        wot = TABLES['work_order']
        c.execute(wot.update().where(wot.c.id == wo_id).values(qty_done=sa.func.coalesce(wot.c.qty_done, 0) + 1, updated_at=ts))
        wo = db.get(c, 'work_order', wo_id)
        if wo['qty_done'] >= wo['qty'] and wo['status'] == 'in_progress':
            c.execute(wot.update().where(wot.c.id == wo_id).values(status='completed', completed_at=ts))

    # ---------- 网关：生产日志 ----------
    def on_event(self, src: str, ev: dict):
        ts = parse_dt(ev.get('t')) or clock.now()
        clock.feed(ts)
        kind, station = ev.get('kind'), ev.get('station', '')
        eid = self.equip(f'{src}:{station}') or self.equip_code_of(station)
        from .services import foundry
        with db.engine.begin() as c:
            if not seen_once(c, 'event', src, ev):
                self.stats['duplicates'] += 1
                return
            self.stats['events'] += 1
            if kind == 'heat' and ev.get('heat'):
                foundry.on_heat(c, eid, ev, ts)
            elif kind == 'chem' and ev.get('heat') and ev.get('C') is not None:
                foundry.on_chem(c, ev, ts)
            elif kind == 'treat' and ev.get('mg') is not None:
                db.insert(c, 'treatment_rec', {'ladle': ev.get('ladle'), 'heat_code': ev.get('heat'), 'temp': ev.get('temp'), 'mg': ev.get('mg'),
                                               'kg': ev.get('kg'), 'time': ts}, raw=True)
            elif kind == 'tool' and ev.get('tool'):
                self._tool_change(c, eid, ev['tool'], ev.get('tool_name'), ev.get('used'), 'life', ts)
            elif kind == 'alarm' and ev.get('broken') and ev.get('tool'):
                self._tool_change(c, eid, ev['tool'], None, ev.get('used'), 'broken', ts)
            elif kind == 'ship' and ev.get('qty'):
                db.insert(c, 'shipment', {'qty': ev['qty'], 'time': ts, 'customer': _tr('主机厂')}, raw=True)

    def _tool_change(self, c, eid, tool, name, used, reason, ts):
        db.insert(c, 'tool_change', {'equipment': eid, 'tool': tool, 'tool_name': name, 'used': used, 'reason': reason, 'time': ts}, raw=True)
        t = TABLES['tool']
        c.execute(t.update().where((t.c.equipment == eid) & (t.c.code == tool)).values(
            used=0, changes=sa.func.coalesce(t.c.changes, 0) + 1, last_change=ts, status='ok', wear_pct=0, updated_at=ts))


ING = Ingest()
ts_mod = ts


# ───────────────────────── 去重 ─────────────────────────
def msg_key(kind, src, payload) -> str:
    return hashlib.sha1(json.dumps([kind, src, payload], ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def seen_once(c, kind, src, payload) -> bool:
    """在业务写入的同一个事务里登记消息摘要：第一次见到返回 True；重复（重放、MQTT 重发）返回 False。"""
    return compat.insert_ignore(c, ingest_seen_t, [{'key': msg_key(kind, src, payload), 'at': db.clock_wall()}], keys=['key']) > 0


# ───────────────────────── 断网续传闸门 ─────────────────────────
DB_DOWN_ERRORS = (sa.exc.OperationalError, sa.exc.InterfaceError, sa.exc.DisconnectionError)


def db_alive() -> bool:
    try:
        with db.engine.connect() as c:
            c.execute(sa.text('SELECT 1'))
            if c.dialect.name == 'sqlite':          # SQLite 的"不可用"通常是被锁：试一下能不能拿写锁
                c.exec_driver_sql('BEGIN IMMEDIATE')
                c.exec_driver_sql('ROLLBACK')
        return True
    except Exception:
        return False


class Gate:
    """采集消息的入口：直接入库，或者（数据库不可用 / 磁盘缓存还没补完时）按顺序写磁盘缓存。"""

    def __init__(self):
        self.buffering = False
        self.since = None
        self.last_error = ''
        self.stats = defaultdict(int)
        self._last_probe = 0.0

    def spool(self):
        from . import spool
        return spool.get()

    def dispatch(self, kind, args):
        if kind == 'tag':
            eid, name, value, t, key = args[:5]               # 5.0 M2：第 6 项是质量码（0 好 / 1 不确定 / 2 坏），老缓存没有
            ING.on_tag(eid, name, value, compat.to_dt(t) if isinstance(t, str) else t, key, args[5] if len(args) > 5 else 0)
        elif kind == 'part':
            ING.on_part(args[0], args[1])
        elif kind == 'event':
            ING.on_event(args[0], args[1])
        elif kind == 'ts':                         # 时序缓冲里没写进去的行：[point, ts, value, text, q]
            ts.write_rows([(p, compat.to_dt(t), v, tx, q) for p, t, v, tx, q in args])

    def submit(self, kind: str, args: list):
        if self.buffering or self.spool().pending():
            self._to_spool(kind, args)
            return
        try:
            self.dispatch(kind, args)
        except DB_DOWN_ERRORS as ex:
            if db_alive():
                raise                              # 库是好的：是代码 / 数据问题，照常报错
            self._go_offline(ex)
            self._to_spool(kind, args)

    def _to_spool(self, kind, args):
        self.spool().append(kind, args)
        self.stats['spooled'] += 1

    def _go_offline(self, ex):
        if not self.buffering:
            log.warning(_tr('数据库不可用，采集数据先写本地缓存：%s'), str(ex).splitlines()[0][:200])
            self.since = clock.now()
            self.stats['outages'] += 1
        self.buffering = True
        self.last_error = str(ex).splitlines()[0][:300]

    def _spool_ts(self):
        rows = ts.take_buffer()
        if rows:
            self._to_spool('ts', [[p, t.strftime('%Y-%m-%d %H:%M:%S.%f'), v, tx, q] for p, t, v, tx, q in rows])

    def flush_ts(self):
        """时序缓冲落库；库不可用时把这批写进磁盘缓存（排在已缓存的消息后面）。"""
        if not ts.pending():
            return
        if self.buffering or self.spool().pending():
            self._spool_ts()
            return
        try:
            ts.flush()
        except DB_DOWN_ERRORS as ex:
            if db_alive():
                raise
            self._go_offline(ex)
            self._spool_ts()

    def drain(self, budget_s: float = 1.5) -> int:
        """补传：库能用了就按顺序把缓存交给 dispatch；全补完回到直接入库。在采集的事件循环里调用（和实时消息不并发）。"""
        sp = self.spool()
        if not sp.pending():
            if self.buffering and _time.time() - self._last_probe > 2:
                self._last_probe = _time.time()
                if db_alive():
                    self.buffering = False
                    log.info(_tr('数据库恢复'))
            return 0
        if self.stats['drain_fail'] and _time.time() - self._last_probe < 2:
            return 0
        self._last_probe = _time.time()
        if not db_alive():
            self.stats['drain_fail'] += 1
            return 0
        t0, n = _time.time(), 0
        try:
            while sp.pending() and _time.time() - t0 < budget_s:
                k = sp.drain(self._replay, limit=500)
                n += k
                if k == 0:
                    break
        except DB_DOWN_ERRORS as ex:
            self.stats['drain_fail'] += 1
            self.last_error = str(ex).splitlines()[0][:300]
            self.stats['replayed'] += n
            return n
        self.stats['replayed'] += n
        self.stats['drain_fail'] = 0
        if not sp.pending():
            if self.buffering:
                log.info(_tr('数据库恢复，本地缓存已全部补传'))
            self.buffering = False
        return n

    def _replay(self, kind, args):
        try:
            self.dispatch(kind, args)
        except DB_DOWN_ERRORS:
            raise
        except Exception:
            log.exception(_tr('补传一条消息失败，跳过：%s'), kind)

    def status(self) -> dict:
        return {'buffering': self.buffering, 'since': db.jsonable(self.since), 'pending': self.spool().pending(), 'last_error': self.last_error,
                **dict(self.stats), 'spool_dir': self.spool().dir}


# 5.0（M2）：入库由采集工作线程执行（collector.Worker）；接口线程 / 测试偶尔也直接调 GATE。加一把锁，保证缓存顺序和补传不并发。
import threading as _threading  # noqa: E402
Gate._lock = _threading.RLock()


def _gate_locked(fn):
    def w(self, *a, **k):
        with self._lock:
            return fn(self, *a, **k)
    w.__name__, w.__doc__ = fn.__name__, fn.__doc__
    return w


for _n in ('submit', 'flush_ts', 'drain'):
    setattr(Gate, _n, _gate_locked(getattr(Gate, _n)))
GATE = Gate()
