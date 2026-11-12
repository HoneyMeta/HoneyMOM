# HoneyMOM plant data replayer - SPDX-License-Identifier: AGPL-3.0-or-later
"""Replay loop: reads the recording, moves every timestamp to "now", publishes MQTT, updates OPC UA and Modbus."""
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from .fmt import TOKEN, Recording, render, shift_date6

log = logging.getLogger('replayer')
PLANT_CLOCK = ('SimTime', 'SimTimeText', 'Speed', 'Paused', 'TimeMode')


class Timeline:
    """Maps recording time (ms since t0) of the current loop to output timestamps."""

    def __init__(self, mode, speed, tz):
        self.mode, self.speed, self.tz = mode, speed, tz
        self.anchor = None          # sim mode: output time of recording ms 0 (aware datetime)
        self.wall0 = 0.0            # epoch seconds when recording ms r0 was due
        self.r0 = 0
        self.days = None            # whole-day shift of dated identifiers in this loop

    def start_loop(self, r0, first, duration_ms, rec_t0):
        now = time.time()
        if self.mode == 'sim':
            if first or self.anchor is None:
                self.anchor = datetime.fromtimestamp(now, self.tz) - timedelta(milliseconds=r0)
            else:
                self.anchor = self.anchor + timedelta(milliseconds=duration_ms)
        self.wall0, self.r0 = now, r0
        d = (self.at(0).date() - rec_t0.date()).days
        self.days = d if self.days is None or d > self.days else self.days + 1

    def due(self, r):
        return self.wall0 + (r - self.r0) / 1000.0 / self.speed

    def at(self, r) -> datetime:
        if self.mode == 'sim':
            return self.anchor + timedelta(milliseconds=r)
        return datetime.fromtimestamp(self.due(r), self.tz)

    def now_rel(self):
        return self.r0 + (time.time() - self.wall0) * 1000.0 * self.speed


class Player:
    def __init__(self, a):
        self.a = a
        self.rec = Recording(a.recording)
        h = self.rec.header
        self.h = h
        self.pid = h['plant']
        self.topics = h['topics']
        self.units = {int(k): v for k, v in (h.get('topic_units') or {}).items()}
        self.tindex = {t: i for i, t in enumerate(self.topics)}
        self.ids = [re.compile(p) for p in h.get('ids') or []]
        self.tz = datetime.now().astimezone().tzinfo
        self.tl = Timeline(a.timestamps, max(0.01, a.speed), self.tz)
        self.pub = None
        self.opc = None
        self.mb = None
        self.broker = None
        self.latest = {}          # topic index -> last 'P' value (for /set echo)
        self.stats = {'mqtt': 0, 'opcua': 0, 'modbus': 0, 'loops': 0, 'lag_max_s': 0.0}
        self.clock_nodes = {}
        self.stop = asyncio.Event()

    # ---------- value transforms ----------
    def untok(self, v):
        if isinstance(v, str):
            if v.startswith(TOKEN):
                if v.startswith(TOKEN + 'W'):
                    return render(datetime.now(self.tz), 'm')
                ms, _, f = v[3:].partition('|')
                return render(self.tl.at(int(ms)), f or 'i')
            if self.ids and v[:1] in ('S', 'L'):
                for rx in self.ids:
                    m = rx.match(v)
                    if m:
                        try:
                            return v[:m.start(1)] + shift_date6(m.group(1), self.tl.days) + v[m.end(1):]
                        except ValueError:
                            return v
            return v
        if isinstance(v, dict):
            if len(v) == 1 and '$t' in v:
                return self.tl.at(v['$t']).astimezone(timezone.utc)
            return {k: self.untok(x) for k, x in v.items()}
        if isinstance(v, list):
            return [self.untok(x) for x in v]
        return v

    # ---------- outputs ----------
    async def publish(self, topic, body, retain):
        if self.pub is None:
            return
        data = body if isinstance(body, bytes) else (body.encode() if isinstance(body, str) else
                                                      json.dumps(body, ensure_ascii=False, separators=(', ', ': ')).encode())
        await self.pub.publish(topic, data, qos=0, retain=retain)
        self.stats['mqtt'] += 1

    def point_payload(self, ti, value, ts):
        return {'value': value, 'unit': self.units.get(ti, ''), 'quality': 'good', 'ts': ts, 'sim_ts': ts,
                'wall_ts': render(datetime.now(self.tz), 'm')}

    async def handle(self, ev, quiet=False):
        kind = ev[0]
        r = ev[1]
        if kind == 'P':
            ti, v, off = ev[2], self.untok(ev[3]), ev[4]
            if off is None:
                ts = render(self.tl.at(r), 'i')
            elif isinstance(off, list):
                ts = render(self.tl.at(r + off[1]), 'i')
            else:
                ts = render(self.tl.at(r + off), 'm')
            self.latest[ti] = v
            if quiet:
                self.pending_retained[ti] = ('P', v, ts)
                return
            await self.publish(self.topics[ti], self.point_payload(ti, v, ts), True)
        elif kind == 'M':
            ti, body, retain = ev[2], ev[3], ev[4]
            if quiet:
                if retain:
                    self.pending_retained[ti] = ('M', body, None)
                return
            await self.publish(self.topics[ti], self.untok(body) if not isinstance(body, str) else self.untok(body), retain)
        elif kind == 'O':
            if self.opc is None:
                return
            i, v, off, st = ev[2], self.untok(ev[3]), ev[4], ev[5]
            src = self.tl.at(r + (off or 0)).astimezone(timezone.utc)
            await self.opc.write(i, v, src, st)
            self.stats['opcua'] += 1
        elif kind == 'B':
            if self.mb is not None:
                self.mb.set(ev[2], ev[3], ev[4], ev[5])
                self.stats['modbus'] += 1

    # ---------- background ----------
    async def heartbeat(self):
        """<plant>/_status (MQTT) every 10 s and the Plant clock nodes (OPC UA) every 0.5 s, like the source."""
        last_hb = 0.0
        from asyncua import ua
        while not self.stop.is_set():
            try:
                r = self.tl.now_rel()
                now_out = self.tl.at(int(r))
                if self.opc is not None and self.clock_nodes:
                    for name, val in (('SimTime', now_out.astimezone(timezone.utc)), ('SimTimeText', render(now_out, 's')),
                                      ('Speed', float(self.a.speed)), ('Paused', False), ('TimeMode', self.a.timestamps)):
                        i = self.clock_nodes.get(name)
                        if i is not None:
                            await self.opc.write(i, val, now_out.astimezone(timezone.utc))
                if self.pub is not None and time.time() - last_hb >= 10:
                    last_hb = time.time()
                    ts = render(now_out, 'i')
                    await self.publish(f'vfactory/{self.pid}/_status', {'online': True, 'ts': ts, 'sim_ts': ts, 'wall_ts': render(datetime.now(self.tz), 'm'),
                                                                        'speed': float(self.a.speed), 'paused': False, 'time_mode': self.a.timestamps,
                                                                        'source': 'replayer'}, True)
            except Exception:
                log.exception('heartbeat')
            await asyncio.sleep(0.5)

    async def listen_sets(self):
        """MQTT writes (<point topic>/set): accept, echo the value on the point topic, answer on /set/ack."""
        from amqtt.client import MQTTClient
        c = MQTTClient(client_id=f'replayer-set-{int(time.time()) % 100000}')
        await c.connect(self.mqtt_url)
        await c.subscribe([(f'vfactory/{self.pid}/+/+/' + '+/' * n + 'set', 0) for n in range(1, 9)])
        while not self.stop.is_set():
            try:
                msg = await c.deliver_message()
                topic = msg.topic
                base = topic[:-4]
                raw = (msg.data or b'').decode('utf-8', 'replace').strip()
                try:
                    v = json.loads(raw)
                except ValueError:
                    v = raw
                rid = None
                if isinstance(v, dict):
                    rid, v = v.get('id'), v.get('value')
                ts = render(self.tl.at(int(self.tl.now_rel())), 'i')
                ti = self.tindex.get(base)
                if getattr(msg, 'retain', False):
                    ack = {'ok': False, 'error': 'retained /set is not executed', 'code': 'retained', 'id': rid, 'ts': ts}
                elif ti is None:
                    ack = {'ok': False, 'error': f'unknown point {base}', 'code': 'notfound', 'id': rid, 'ts': ts}
                else:
                    self.latest[ti] = v
                    await self.publish(base, self.point_payload(ti, v, ts), True)
                    ack = {'ok': True, 'value': v, 'id': rid, 'ts': ts, 'sim_ts': ts}
                await self.publish(topic + '/ack', ack, False)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('mqtt /set')
                await asyncio.sleep(1)

    # ---------- main ----------
    async def start(self):
        a = self.a
        if not a.no_mqtt:
            if a.mqtt_broker:
                self.mqtt_url = a.mqtt_broker.rstrip('/') + '/'
            else:
                from .servers import start_broker
                self.broker = await start_broker(a.host, a.mqtt_port)
                self.mqtt_url = f'mqtt://127.0.0.1:{a.mqtt_port}/'
            from amqtt.client import MQTTClient
            self.pub = MQTTClient(client_id=f'replayer-pub-{int(time.time()) % 100000}', config={'auto_reconnect': True})
            await self.pub.connect(self.mqtt_url)
        if not a.no_opcua and self.h.get('opcua'):
            from .servers import OpcUaServer
            path = '/' + a.opcua_path.strip('/') + '/' if a.opcua_path.strip('/') else '/'
            self.opc = OpcUaServer(self.h['opcua'], self.h['nodes'], f'opc.tcp://{a.host}:{a.opcua_port}{path}')
            await self.opc.start()
            for i, n in enumerate(self.h['nodes']):
                if '/Plant/' in n and n.rsplit('/', 1)[-1] in PLANT_CLOCK:
                    self.clock_nodes[n.rsplit('/', 1)[-1]] = i
        if a.modbus_port and self.h.get('modbus', {}).get('blocks'):
            from .servers import ModbusServer
            self.mb = ModbusServer(self.h['modbus']['blocks'], a.host, a.modbus_port)
            await self.mb.start()

    async def play_loop(self):
        a = self.a
        D = self.rec.duration_ms
        r_start = int(max(0.0, a.start) * 3600 * 1000) % max(D, 1)
        first = True
        while not self.stop.is_set():
            self.pending_retained = {}
            r0 = r_start if first else 0
            self.tl.start_loop(r0, first, D, self.rec.t0)
            self.stats['loops'] += 1
            log.info('loop %d: recording %+.2f h .. %.2f h, timestamps from %s, speed %g, id dates %+d d', self.stats['loops'], r0 / 3600000, D / 3600000,
                     self.tl.at(r0).isoformat(timespec='seconds'), a.speed, self.tl.days)
            fast = r0 > 0
            last_log = time.time()
            n = 0
            for ev in self.rec.events():
                if self.stop.is_set():
                    return
                r = ev[1]
                if fast and r < r0:
                    # fast-forward: OPC UA / Modbus take the values silently, MQTT keeps the last retained value per topic
                    await self.handle(ev, quiet=True)
                    continue
                if fast:
                    fast = False
                    await self.flush_retained()
                wait = self.tl.due(r) - time.time()
                if wait > 0.002:
                    await asyncio.sleep(wait)
                elif wait < -1:
                    self.stats['lag_max_s'] = max(self.stats['lag_max_s'], round(-wait, 1))
                await self.handle(ev)
                n += 1
                if n % 500 == 0:
                    await asyncio.sleep(0)
                if time.time() - last_log > 60:
                    last_log = time.time()
                    log.info('at %.2f h of %.2f h: %s', r / 3600000, D / 3600000, self.stats)
            if fast:
                await self.flush_retained()
            # pause until the recorded end so the loop length stays exact
            wait = self.tl.due(D) - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
            first = False
            if not a.loop:
                log.info('end of recording (--once)')
                return

    async def flush_retained(self):
        for ti, (kind, v, ts) in self.pending_retained.items():
            if kind == 'P':
                await self.publish(self.topics[ti], self.point_payload(ti, v, ts), True)
            else:
                await self.publish(self.topics[ti], self.untok(v), True)
        self.pending_retained = {}

    async def close(self):
        self.stop.set()
        for x in (self.opc, self.mb):
            if x is not None:
                try:
                    await x.stop()
                except Exception:
                    pass
        if self.pub is not None:
            try:
                await self.pub.disconnect()
            except Exception:
                pass
        if self.broker is not None:
            try:
                await self.broker.shutdown()
            except Exception:
                pass


async def run(a):
    p = Player(a)
    s = p.rec.summary()
    log.info('recording %s: %s, %.2f h from %s (%s)', a.recording, s['plant'], s['duration_h'], s['t0'], s.get('source'))
    await p.start()
    bg = [asyncio.create_task(p.heartbeat())]
    if p.pub is not None:
        bg.append(asyncio.create_task(p.listen_sets()))
    try:
        await p.play_loop()
    finally:
        for t in bg:
            t.cancel()
        await p.close()
