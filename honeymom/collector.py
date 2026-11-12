"""数据采集（5.0 M2：通用采集，不再有虚拟工厂专用规则）。一切按工厂模型：数据源 + 采集点表（地址 → 设备 / 点位路径 / 用途 / 缩放…），
用途缺省按设备类型模板（factory/model.py INDEX）。

接入方式（datasource.protocol）：
  opcua      OPC UA：按点表的 NodeId 订阅（options.mode = poll 时按采集周期轮询）；浏览见接入中心
  mqtt       MQTT：订阅 <前缀>#，主题 = 点表地址；载荷是我们的 JSON（{"value", "ts", "quality"}）或纯数值 / 文本；
             <前缀>_mes/part | _mes/event 是网关上送的零件事件和生产日志
  sparkplug  MQTT Sparkplug B：订阅 spBv1.0/<组>/#（前缀填组名，空 = 全部组），按出生证书（NBIRTH / DBIRTH）还原别名；地址 = 组/节点/设备:指标
  modbus     Modbus TCP：地址 从站:表:地址（hr / ir / coil / di），按寄存器编码（int16 / uint16 / int32 / float32 + 字序）解码，工程值 = 原始 × 缩放 + 偏移
  http       HTTP 推送：POST /api/onboard/push（API 密钥），按点位编码或地址
  sql        中间库：按配置的查询轮询外部库的表 / 视图，按水位列增量取（options：table / query / tag_col / value_col / ts_col / watermark / layout / interval_s）
点表里没有的地址进待确认队列（factory/live.py；接入中心逐点核对标黄），点表里有却一直没值的标红。
时间戳保留毫秒（动作级信号的用时只有零点几秒，M6 节拍分析要用）；带时区的换成本地时间。

线程：协议收发在事件循环里；入库（GATE.submit → 语义入库 + 时序 + 断网续传）全部在一个采集工作线程里按到达顺序执行，
数据库被锁住 / 慢的时候只是工作线程等，接口照常响应（4.7 时采集在事件循环里直接写库，SQLite 锁等待会卡住 HTTP）。
后台：每秒时序落库 + 补传，每 2 秒最新值 / 电耗 / 产量落库 + 审计封存，每 10 秒待确认队列落库，每分钟时序汇总 + 点位统计，每小时清理去重表，
每天保留策略，每 10 分钟记一条采集汇总审计。
"""
from .i18n import _tr
import asyncio
import json
import logging
import struct
import threading
import time
from collections import deque
from datetime import datetime, timezone

from fastapi import HTTPException

from . import db
from .crud import action
from .db import clock
from .ingest import GATE, ING
from .schema import TABLES

log = logging.getLogger('mes.collector')
MAX_QUEUE = 500000


def parse_ts(v):
    """时间戳 → 本地时间（不带时区，保留毫秒）：ISO 8601（带不带时区都行）、毫秒 / 秒 epoch。"""
    if v in (None, ''):
        return None
    if isinstance(v, datetime):
        return v.astimezone().replace(tzinfo=None) if v.tzinfo else v
    if isinstance(v, (int, float)):
        x = float(v)
        return datetime.fromtimestamp(x / 1000 if x > 1e11 else x)
    s = str(v).strip()
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        return d.astimezone().replace(tzinfo=None) if d.tzinfo else d
    except ValueError:
        return db.parse_dt(s)


_ts_ms = parse_ts          # M6 的名字（保留毫秒的时间解析）


def _utc_naive(d):
    """OPC UA 的 SourceTimestamp 是 UTC：转成本地时间。"""
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone().replace(tzinfo=None)


def transform(m: dict, v):
    """原始值 → 工程值：值映射（状态码）、缩放 / 偏移、按数据类型规整。"""
    if v is None:
        return None
    vm = m.get('map')
    if vm:
        k = str(int(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v).is_integer() else str(v)
        if k in vm:
            return vm[k]
    dt = m.get('dtype')
    if isinstance(v, str) and dt in ('float', 'int'):
        try:
            v = float(v)
        except ValueError:
            return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        sc, off = m.get('scale'), m.get('offset')
        if (sc not in (None, 1, 1.0)) or off:
            v = v * (sc if sc not in (None, 0) else 1) + (off or 0)
        if dt == 'int' and float(v).is_integer():
            v = int(v)
        elif dt == 'bool':
            v = bool(v)
        elif dt == 'float':
            v = float(v)
    elif isinstance(v, bool) and dt in ('int', 'float') and not vm:
        v = int(v)
    return v


class StatusMap(dict):
    """Data-source status: stored as (message id, code, kwargs); reading renders the text in the current request language."""
    def put(self, k, msg, code='', **kw):
        dict.__setitem__(self, k, (msg, code, kw))

    def raw(self, k):
        return dict.get(self, k)

    def code(self, k):
        v = dict.get(self, k)
        return v[1] if v else ''

    def codes(self):
        return {k: v[1] for k, v in dict.items(self)}

    def get(self, k, default=None):
        v = dict.get(self, k)
        return _tr(v[0], **v[2]) if v else default

    def __getitem__(self, k):
        return self.get(k)

    def items(self):
        return [(k, self.get(k)) for k in dict.keys(self)]

    def values(self):
        return [self.get(k) for k in dict.keys(self)]


class Worker:
    """采集工作线程：按到达顺序执行入库（GATE.submit）和后台作业。"""

    def __init__(self):
        self.q = deque()
        self.cv = threading.Condition()
        self.stats = {'done': 0, 'dropped': 0, 'errors': 0, 'max_q': 0, 'busy_ms': 0}
        self.current = None
        self.started = None
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.run, name='mes-collector-worker', daemon=True)
        self.thread.start()

    def put(self, item):
        with self.cv:
            if len(self.q) >= MAX_QUEUE and item[0] == 'tag':
                self.stats['dropped'] += 1
                return
            self.q.append(item)
            if len(self.q) > self.stats['max_q']:
                self.stats['max_q'] = len(self.q)
            self.cv.notify()

    def run(self):
        while True:
            with self.cv:
                while not self.q:
                    self.cv.wait(1.0)
                item = self.q.popleft()
            t0 = time.time()
            self.current, self.started = item[0], t0
            try:
                if item[0] == 'call':
                    item[1](*item[2])
                else:
                    GATE.submit(item[0], item[1])
                self.stats['done'] += 1
            except Exception:
                self.stats['errors'] += 1
                log.exception(_tr('采集入库：%s'), item[0] if item[0] != 'call' else getattr(item[1], '__name__', 'call'))
            finally:
                self.current = None
                self.stats['busy_ms'] += int((time.time() - t0) * 1000)

    def status(self):
        return {**self.stats, 'queue': len(self.q), 'current': self.current,
                'current_s': round(time.time() - self.started, 1) if self.current and self.started else 0}


class Collector:
    def __init__(self):
        self.tasks: dict[int, asyncio.Task] = {}
        self.status = StatusMap()          # id -> status text in the reader's language; .code(id) = connected|error|waiting|push|stopped
        self.loop: asyncio.AbstractEventLoop | None = None
        self.worker = Worker()
        self.discover_until: dict[int, float] = {}
        self.sql_engines = {}

    # ---------- 生命周期 ----------
    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.worker.start()
        from .factory.model import INDEX
        await asyncio.to_thread(ING.reload)
        await asyncio.to_thread(INDEX.build)
        with db.engine.connect() as c:
            dss = db.rows(c, 'datasource', enabled=True)
        for ds in dss:
            self.launch(ds)
        asyncio.create_task(self.housekeeping())

    def launch(self, ds, discover_only=False):
        self.stop(ds['id'])
        fn = {'mqtt': self.mqtt_loop, 'sparkplug': self.mqtt_loop, 'opcua': self.opcua_loop, 'modbus': self.modbus_loop, 'sql': self.sql_loop}.get(ds['protocol'])
        if ds['protocol'] == 'http':
            self.set_status(ds, '等待推送（POST /api/onboard/push）', 'waiting')
            return
        if fn:
            self.tasks[ds['id']] = asyncio.create_task(fn(ds, discover_only))

    def stop(self, dsid):
        t = self.tasks.pop(dsid, None)
        if t:
            t.cancel()

    def restart_threadsafe(self, dsid):
        """从接口线程里重连某个数据源（模型改了也调它：采集映射按新点表重建）。读库都放到线程里，事件循环不等数据库。"""
        async def go():
            from .factory.model import INDEX

            def load():
                INDEX.dirty = True
                ING.reload()
                INDEX.build()
                with db.engine.connect() as c:
                    return db.get(c, 'datasource', dsid)
            try:
                ds = await asyncio.to_thread(load)
            except Exception:
                log.exception(_tr('重连数据源 %s'), dsid)
                return
            if ds and ds['enabled']:
                self.launch(ds)
            else:
                self.stop(dsid)
        if self.loop:
            # 调用方通常还在事务里：等提交后再重连
            self.loop.call_soon_threadsafe(lambda: self.loop.call_later(1.5, lambda: asyncio.ensure_future(go())))

    def reload_model_threadsafe(self):
        from .factory.model import INDEX
        INDEX.dirty = True

    def discover(self, ds, minutes: float):
        """发现模式：N 分钟内记下所有收到的地址和样本值（点表里有的也记）。数据源没启用时临时连上只做发现、不入库。"""
        self.discover_until[ds['id']] = time.time() + minutes * 60
        if self.loop:
            def go():
                if ds['id'] not in self.tasks or self.tasks[ds['id']].done():
                    self.launch(ds, discover_only=not ds['enabled'])
            self.loop.call_soon_threadsafe(go)

    def discovering(self, dsid):
        return max(0, round(self.discover_until.get(dsid, 0) - time.time()))

    # ---------- 入库 ----------
    def submit(self, kind, args):
        self.worker.put((kind, args))

    def submit_call(self, fn, *args):
        self.worker.put(('call', fn, args))

    def set_status(self, ds, msg, code='', **kw):
        """msg = catalogue id (Chinese source), rendered in the reader's language; code = machine state for logic."""
        if self.status.raw(ds['id']) == (msg, code, kw):
            return
        self.status.put(ds['id'], msg, code, **kw)
        s = self.status.get(ds['id'])
        t = TABLES['datasource']

        def go():
            with db.engine.begin() as c:
                c.execute(t.update().where(t.c.id == ds['id']).values(status=s[:190]))
        self.submit_call(go)

    def emit(self, ds, m, raw, ts=None, q=0):
        """点表里的点收到一个原始值。"""
        from .factory.live import LIVE
        v = transform(m, raw)
        LIVE.hit(m, v, q)
        if ds.get('_discover_only'):
            return
        self.submit('tag', [m['eid'], m['name'], v, ts, m['key'], q])

    def unknown(self, ds, address, raw, attrs=None, hint=None, kind='value'):
        from .factory.live import LIVE
        from .factory.model import INDEX
        alt = False
        if hint and '/' in hint:
            dev, name = hint.split('/', 1)
            eid = INDEX.eq_by_code.get(dev)
            alt = bool(eid and (eid, name) in INDEX.names)
        LIVE.miss(ds['id'], address, raw, attrs, hint, kind, alternate=alt)

    def lookup(self, ds, address):
        from .factory.model import INDEX
        INDEX.ensure(block=False)
        return (INDEX.by_addr.get(ds['id']) or {}).get(address)

    def push(self, ds, values: list) -> dict:
        """HTTP 推送（接口线程调用）：[{code | address, value, ts?, q?}]。"""
        from .factory.model import INDEX
        if INDEX.dirty:                     # 推送是接口线程同步调用：点表刚改过就马上重建（不等 2 秒节流）
            INDEX.build()
        by_code = INDEX.by_code.get(ds['id']) or {}
        by_addr = INDEX.by_addr.get(ds['id']) or {}
        ok = unk = 0
        for it in values:
            k = it.get('code') or it.get('address') or it.get('key')
            m = by_code.get(k) or by_addr.get(k)
            q = int(it.get('q') or 0)
            if m:
                self.emit(ds, m, it.get('value'), parse_ts(it.get('ts')), q)
                ok += 1
            else:
                self.unknown(ds, str(k), it.get('value'))
                unk += 1
        self.status.put(ds['id'], '推送：最近一次 {ok} 个值', 'push', ok=ok)
        return {'accepted': ok, 'unknown': unk}

    # ---------- 后台 ----------
    async def housekeeping(self):
        n = 0
        loop = asyncio.get_running_loop()
        last = {'rollup': 0.0, 'seen': time.time(), 'retention': time.time() - 23 * 3600, 'batch': time.time()}
        batch0 = {'v': {}}
        from .factory.live import LIVE

        def tick_ts():
            GATE.flush_ts()
            GATE.drain()

        def tick_flush():
            try:
                if not GATE.buffering:
                    ING.flush()
            except ingest_mod.DB_DOWN_ERRORS as ex:
                if not ingest_mod.db_alive():
                    GATE._go_offline(ex)

        def tick_batch():
            batch0['v'] = _batch_audit(batch0['v'])
        while True:
            await asyncio.sleep(1)
            n += 1
            if len(self.worker.q) < 5000:
                self.submit_call(tick_ts)                   # 时序落库 / 补传在工作线程里（和实时消息不并发）
            if n % 2:
                continue
            if len(self.worker.q) < 5000:
                self.submit_call(tick_flush)
            if n % 10 == 0:
                self.submit_call(LIVE.flush_seen)
            if n % 60 == 0:
                self.submit_call(LIVE.persist_points)
                self.submit_call(_ds_last_seen)
            try:
                if GATE.buffering:
                    continue
                from . import audit
                await loop.run_in_executor(None, audit.seal)
                if n % 60 == 0:
                    from .services.machining import refresh_tools
                    await loop.run_in_executor(None, refresh_tools)
                now = time.time()
                if now - last['rollup'] > 60:
                    last['rollup'] = now
                    from . import ts
                    await loop.run_in_executor(None, ts.rollup_job)
                if now - last['seen'] > 3600:
                    last['seen'] = now
                    await loop.run_in_executor(None, _clean_seen)
                if now - last['retention'] > 24 * 3600:
                    last['retention'] = now
                    from . import ts
                    await loop.run_in_executor(None, ts.retention_job)
                if now - last['batch'] > 600:
                    last['batch'] = now
                    self.submit_call(tick_batch)
            except Exception:
                log.exception('housekeeping')

    # ---------- MQTT / Sparkplug B ----------
    async def mqtt_loop(self, ds, discover_only=False):
        from amqtt.client import MQTTClient
        ds = {**ds, '_discover_only': discover_only}
        spb = ds['protocol'] == 'sparkplug'
        prefix = ds['prefix'] or ''
        url = ds['endpoint'] or 'mqtt://127.0.0.1:1883'
        if not url.startswith('mqtt'):
            url = 'mqtt://' + url
        if spb:
            from .factory.sparkplug import Births
            births = Births()
            sub = f"spBv1.0/{prefix.strip('/')}/#" if prefix.strip('/') else 'spBv1.0/#'
        else:
            births = None
            sub = prefix + '#'
        while True:
            cli = MQTTClient(client_id=f"honeymom-{ds['code']}-{int(time.time() * 1000) % 1000000}")
            try:
                await cli.connect(url.rstrip('/') + '/')
                await cli.subscribe([(sub, 0)])
                self.set_status(ds, '已连接（只发现）', 'connected') if discover_only else self.set_status(ds, '已连接', 'connected')
                n = 0
                while True:
                    if discover_only and self.discovering(ds['id']) <= 0:
                        self.set_status(ds, '发现结束（数据源未启用）', 'stopped')
                        await cli.disconnect()
                        return
                    try:
                        msg = await asyncio.wait_for(cli.deliver_message(), timeout=5) if discover_only else await cli.deliver_message()
                    except asyncio.TimeoutError:
                        continue
                    try:
                        if spb:
                            self.on_sparkplug(ds, births, msg.topic, msg.data)
                        else:
                            self.on_mqtt(ds, prefix, msg.topic, msg.data)
                    except Exception:
                        log.exception('mqtt message %s', msg.topic)
                    n += 1
                    if n % 200 == 0:
                        await asyncio.sleep(0)
            except asyncio.CancelledError:
                try:
                    await cli.disconnect()
                except Exception:
                    pass
                raise
            except Exception as ex:
                self.set_status(ds, '连接失败：{ex}', 'error', ex=str(ex)[:140])
                log.warning('mqtt %s: %s', url, ex)
                try:
                    await cli.disconnect()
                except Exception:
                    pass
                await asyncio.sleep(5)

    def on_mqtt(self, ds, prefix, topic, data):
        rest = topic[len(prefix):] if topic.startswith(prefix) else topic
        if rest.endswith('/set') or rest.endswith('/set/ack') or rest.startswith('_status'):
            return
        text = data.decode('utf-8', 'replace') if data else ''
        if rest.startswith('_mes/'):
            if ds.get('_discover_only'):
                return
            payload = json.loads(text or 'null')
            if rest.startswith('_mes/part'):
                self.submit('part', [ds['code'], payload])
            elif rest.startswith('_mes/event'):
                self.submit('event', [ds['code'], payload])
            return
        try:
            payload = json.loads(text) if text else None
        except ValueError:
            payload = text                                   # 纯文本载荷（有的网关发 "12.5"）
        q = 0
        if isinstance(payload, dict) and 'value' in payload:
            val = payload.get('value')
            ts = parse_ts(payload.get('ts') or payload.get('timestamp'))
            ql = str(payload.get('quality') or payload.get('q') or 'good').lower()
            q = 0 if ql in ('good', '0', '192') else 2 if ql in ('bad', '2') else 1
        else:
            val, ts = payload, None
        m = self.lookup(ds, topic)
        if m:
            self.emit(ds, m, val, ts, q)
            if self.discovering(ds['id']):
                from .factory.live import LIVE
                LIVE.miss(ds['id'], topic, val, {'payload': text[:300], 'point': m['code']}, None, 'discover')
            return
        parts = rest.split('/')
        # 我们的主题规范：<前缀><区域>/<设备编码>/<点位路径>；设备和点位能对上的是另一协议上已有的点（不进待确认）
        hint = f"{parts[1]}/{'/'.join(parts[2:])}" if len(parts) >= 3 else None
        self.unknown(ds, topic, val, {'payload': text[:300]}, hint)

    def on_sparkplug(self, ds, births, topic, data):
        for addr, val, ts_ms, meta in births.on_message(topic, data):
            m = self.lookup(ds, addr)
            ts = parse_ts(ts_ms) if ts_ms else None
            if m:
                self.emit(ds, m, val, ts)
            else:
                self.unknown(ds, addr, val, {k: v for k, v in (meta or {}).items() if v is not None}, None, 'sparkplug')

    # ---------- OPC UA ----------
    async def opcua_loop(self, ds, discover_only=False):
        from asyncua import Client
        from .factory.model import INDEX
        mode = ((ds.get('options') or {}).get('mode') or 'subscribe')
        while True:
            try:
                INDEX.ensure(block=False)
                pts = list((INDEX.by_addr.get(ds['id']) or {}).values())
                async with Client(ds['endpoint'], timeout=10) as cli:
                    nodes = {}
                    for p in pts:
                        try:
                            nodes[cli.get_node(p['address'])] = p
                        except Exception:
                            self.unknown(ds, p['address'], None, {'error': _tr('NodeId 格式不对')})
                    coll = self
                    if mode == 'poll':
                        self.set_status(ds, '已连接，轮询 {len} 点', 'connected', len=len(nodes))
                        period = max(0.2, min(10.0, min([(p.get('period_ms') or 1000) / 1000 for p in pts] or [1.0])))
                        items = list(nodes.items())
                        while True:
                            for i in range(0, len(items), 200):
                                chunk = items[i:i + 200]
                                try:
                                    dvs = await cli.read_attributes([n for n, _ in chunk])
                                except Exception:
                                    dvs = [None] * len(chunk)
                                for (n, p), dv in zip(chunk, dvs):
                                    if dv is None or dv.Value is None:
                                        continue
                                    ok = dv.StatusCode is None or dv.StatusCode.is_good()
                                    self.emit(ds, p, dv.Value.Value, _utc_naive(dv.SourceTimestamp), 0 if ok else 2)
                            await asyncio.sleep(period)
                            if INDEX.dirty:
                                break

                    class H:
                        def datachange_notification(self, node, val, data):
                            p = nodes.get(node)
                            if not p:
                                return
                            ts, q = None, 0
                            try:
                                dv = data.monitored_item.Value
                                ts = _utc_naive(dv.SourceTimestamp)
                                q = 0 if dv.StatusCode is None or dv.StatusCode.is_good() else 2
                            except Exception:
                                pass
                            coll.emit(ds, p, val, ts, q)

                        def status_change_notification(self, status):
                            pass
                    if mode != 'poll':
                        sub = await cli.create_subscription(500, H())
                        lst = list(nodes)
                        for i in range(0, len(lst), 500):
                            res = await sub.subscribe_data_change(lst[i:i + 500])
                            for n, r in zip(lst[i:i + 500], res if isinstance(res, list) else [res]):
                                if not isinstance(r, int):
                                    self.unknown(ds, nodes[n]['address'], None, {'error': _tr('订阅失败：{r}', r=r)})
                        self.set_status(ds, '已连接，订阅 {len} 点', 'connected', len=len(nodes))
                        while True:
                            await asyncio.sleep(5)
                            await cli.check_connection()
                            if INDEX.dirty:
                                INDEX.ensure(block=False)
                                if set((INDEX.by_addr.get(ds['id']) or {})) != {p['address'] for p in pts}:
                                    break                    # 点表变了：重连按新点表订阅
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                self.set_status(ds, '连接失败：{ex}', 'error', ex=str(ex)[:140])
                await asyncio.sleep(5)

    # ---------- Modbus ----------
    async def modbus_loop(self, ds, discover_only=False):
        from pymodbus.client import AsyncModbusTcpClient
        from .factory.model import INDEX
        host, _, port = (ds['endpoint'] or '127.0.0.1:502').replace('modbus://', '').replace('tcp://', '').partition(':')
        while True:
            cli = AsyncModbusTcpClient(host, port=int(port or 502))
            try:
                await cli.connect()
                if not cli.connected:
                    raise ConnectionError(_tr('无法连接'))
                INDEX.ensure(block=False)
                pts = list((INDEX.by_addr.get(ds['id']) or {}).values())
                plan, bad = modbus_plan(pts)
                for p in bad:
                    self.unknown(ds, p['address'], None, {'error': _tr('地址格式不对（从站:表:地址）')})
                self.set_status(ds, '已连接，轮询 {v} 点', 'connected', v=len(pts) - len(bad))
                period = max(0.2, min(10.0, min([(p.get('period_ms') or 1000) / 1000 for p in pts] or [1.0])))
                while True:
                    for (unit, table, start, count), items in plan:
                        try:
                            if table in ('coil', 'co', 'di'):
                                fn = cli.read_coils if table != 'di' else cli.read_discrete_inputs
                                r = await fn(start, count=count, slave=unit)
                                if r.isError():
                                    continue
                                bits = r.bits
                                for p, off, _n in items:
                                    self.emit(ds, p, bool(bits[off]))
                            else:
                                fn = cli.read_input_registers if table == 'ir' else cli.read_holding_registers
                                r = await fn(start, count=count, slave=unit)
                                if r.isError():
                                    continue
                                regs = r.registers
                                for p, off, n in items:
                                    v = decode_regs(regs[off:off + n], p.get('enc'))
                                    if v is not None:
                                        self.emit(ds, p, v)
                        except (ValueError, AttributeError, IndexError, struct.error):
                            continue
                    await asyncio.sleep(period)
                    if INDEX.dirty:
                        INDEX.ensure(block=False)
                        if {p['address'] for p in (INDEX.by_addr.get(ds['id']) or {}).values()} != {p['address'] for p in pts}:
                            break
            except asyncio.CancelledError:
                cli.close()
                raise
            except Exception as ex:
                self.set_status(ds, '连接失败：{ex}', 'error', ex=str(ex)[:140])
                cli.close()
                await asyncio.sleep(5)

    # ---------- 中间库 ----------
    async def sql_loop(self, ds, discover_only=False):
        o = dict(ds.get('options') or {})
        ds = {**ds, 'options': o, '_discover_only': discover_only}
        iv = max(0.5, float(o.get('interval_s') or 5))
        while True:
            try:
                rows, wm, cols = await asyncio.to_thread(self._sql_poll, ds)
                for addr, val, ts in rows:
                    m = self.lookup(ds, addr)
                    if m:
                        self.emit(ds, m, val, ts)
                    else:
                        self.unknown(ds, addr, val, {'columns': cols}, None, 'sql')
                if wm is not None and wm != o.get('_wm'):
                    o['_wm'] = wm
                    self.submit_call(_save_wm, ds['id'], wm)
                self.set_status(ds, '已连接，水位 {get}', 'connected', get=o.get("_wm"))
                await asyncio.sleep(iv if len(rows) < int(o.get('batch') or 5000) else 0.2)
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                self.set_status(ds, '查询失败：{ex}', 'error', ex=str(ex)[:140])
                await asyncio.sleep(max(iv, 5))

    def _sql_poll(self, ds):
        import sqlalchemy as sa
        o = ds.get('options') or {}
        url = ds['endpoint']
        eng = self.sql_engines.get(ds['id'])
        if eng is None or self.sql_engines.get(('url', ds['id'])) != url:
            eng = self.sql_engines[ds['id']] = sa.create_engine(url, pool_pre_ping=True)
            self.sql_engines[('url', ds['id'])] = url
        layout = o.get('layout') or 'long'
        tag_c, val_c, ts_c = o.get('tag_col') or 'tag', o.get('value_col') or 'value', o.get('ts_col') or 'ts'
        wm_c = o.get('watermark') or ts_c
        limit = int(o.get('batch') or 5000)
        if o.get('query'):
            sql = o['query']
        else:
            table = o.get('table') or 'mes_points'
            sql = f'SELECT * FROM {table} WHERE {wm_c} > :wm ORDER BY {wm_c} LIMIT {limit}'
        wm = o.get('_wm')
        if wm is None:
            wm = o.get('start_wm', '1970-01-01 00:00:00' if wm_c == ts_c else 0)
        with eng.connect() as c:
            res = c.execute(sa.text(sql), {'wm': wm, 'limit': limit})
            cols = list(res.keys())
            data = [dict(r) for r in res.mappings()]
        out, last = [], None
        for r in data:
            ts = parse_ts(r.get(ts_c)) if r.get(ts_c) is not None else None
            if layout == 'wide':
                for k, v in r.items():
                    if k not in (ts_c, wm_c):
                        out.append((k, v, ts))
            else:
                out.append((str(r.get(tag_c)), r.get(val_c), ts))
            w = r.get(wm_c)
            last = db.jsonable(w) if w is not None else last
        return out, last, cols


def modbus_plan(pts):
    """点位按 从站 + 表 分组，连续的地址合并成一次读（≤ 120 个寄存器 / 1000 个线圈）。返回 ([((从站, 表, 起始, 个数), [(点, 偏移, 寄存器数)])], 地址不对的点)。"""
    groups, bad = {}, []
    for p in pts:
        try:
            unit, table, addr = (p['address'] or '').split(':')
            unit, addr, table = int(unit), int(addr), table.lower()
        except (ValueError, AttributeError):
            bad.append(p)
            continue
        n = 1 if table in ('coil', 'co', 'di') else reg_count(p.get('enc'))
        groups.setdefault((unit, table), []).append((addr, n, p))
    plan = []
    for (unit, table), items in sorted(groups.items()):
        items.sort(key=lambda x: x[0])
        cap = 1000 if table in ('coil', 'co', 'di') else 120
        cur = None
        for addr, n, p in items:
            if cur and addr + n - cur[0] <= cap:
                cur[1] = max(cur[1], addr + n)
                cur[2].append((p, addr - cur[0], n))
            else:
                if cur:
                    plan.append(((unit, table, cur[0], cur[1] - cur[0]), cur[2]))
                cur = [addr, addr + n, [(p, 0, n)]]
        if cur:
            plan.append(((unit, table, cur[0], cur[1] - cur[0]), cur[2]))
    return plan, bad


def reg_count(enc):
    e = (enc or 'int16').lower()
    return 4 if '64' in e else 2 if '32' in e else 1


def decode_regs(regs, enc):
    """寄存器 → 数值。enc：int16（默认）/ uint16 / int32 / uint32 / float32 / float64 / int64 + 字序 ABCD（默认）/ CDAB / BADC / DCBA。"""
    e = (enc or 'int16').lower().split()
    typ = e[0]
    order = e[1].upper() if len(e) > 1 else 'ABCD'
    n = reg_count(typ)
    if len(regs) < n:
        return None
    b = b''.join(struct.pack('>H', r & 0xFFFF) for r in regs[:n])
    if n > 1 and order in ('CDAB', 'DCBA'):
        b = b''.join([b[i:i + 2] for i in range(0, len(b), 2)][::-1])
    if order in ('BADC', 'DCBA'):
        b = b''.join(b[i + 1:i + 2] + b[i:i + 1] for i in range(0, len(b), 2))
    fmt = {'int16': '>h', 'uint16': '>H', 'int32': '>i', 'uint32': '>I', 'float32': '>f', 'float64': '>d', 'int64': '>q'}.get(typ, '>h')
    v = struct.unpack(fmt, b)[0]
    if typ.startswith('float') and (v != v or abs(v) > 1e30):
        return None
    return v


COLLECTOR = Collector()
from . import ingest as ingest_mod  # noqa: E402


def _clean_seen():
    """去重表只留 3 天。"""
    from datetime import timedelta
    from .schema import ingest_seen_t
    with db.engine.begin() as c:
        c.execute(ingest_seen_t.delete().where(ingest_seen_t.c.at < db.clock_wall() - timedelta(days=3)))


def _ds_last_seen():
    from .factory.live import LIVE
    t = TABLES['datasource']
    with db.engine.begin() as c:
        for dsid, at in list(LIVE.ds_seen.items()):
            c.execute(t.update().where(t.c.id == dsid).values(last_seen=datetime.fromtimestamp(at).replace(microsecond=0)))


def _save_wm(dsid, wm):
    t = TABLES['datasource']
    with db.engine.begin() as c:
        o = c.execute(t.select().where(t.c.id == dsid)).mappings().first()
        if o:
            opts = dict(o['options'] or {})
            opts['_wm'] = wm
            c.execute(t.update().where(t.c.id == dsid).values(options=opts))


def _batch_audit(prev: dict) -> dict:
    """采集不逐条审计：每 10 分钟记一条汇总（这段时间入库了多少零件事件、生产日志、点位值、时序行，缓存 / 补传了多少）。"""
    from . import audit, ts
    cur = {'part_events': ING.stats['part_events'], 'events': ING.stats['events'], 'tag_values': ING.stats['tag_values'],
           'duplicates': ING.stats['duplicates'], 'ts_rows': ts.STATS['rows_new'], 'spooled': GATE.stats['spooled'], 'replayed': GATE.stats['replayed']}
    delta = {k: v - prev.get(k, 0) for k, v in cur.items()}
    if any(delta.values()):
        with db.engine.begin() as c:
            audit.audit(c, 'collector_batch', 'datasource', None, detail=delta, target=_tr('采集汇总（10 分钟）'), user='system:collector', source='collector')
    return cur


@action('datasource', 'restart')
def ds_restart(c, ds, body, user):
    COLLECTOR.restart_threadsafe(ds['id'])
    return {'msg': _tr('正在重连')}


@action('datasource', 'discover')
def ds_discover(c, ds, body, user):
    minutes = max(0.2, min(60.0, float((body or {}).get('minutes') or 5)))
    COLLECTOR.discover(ds, minutes)
    return {'msg': _tr('开始发现：{minutes:g} 分钟内收到的地址都会记下（接入中心 → 逐点核对 → 待确认）', minutes=minutes), 'seconds': int(minutes * 60)}


def need_loop():
    if not COLLECTOR.loop:
        raise HTTPException(503, _tr('采集还没启动'))
