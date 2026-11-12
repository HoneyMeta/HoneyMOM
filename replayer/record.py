# HoneyMOM plant data replayer - SPDX-License-Identifier: AGPL-3.0-or-later
"""Capture live plant traffic (MQTT + OPC UA + Modbus TCP) into a raw capture file (gzip JSONL).

Works against any source that follows the delivery-package point table: subscribes to the MQTT topic prefix, subscribes
to every OPC UA node in the point table (plus <plant>/Plant/* clock nodes when present) and polls the Modbus blocks.
The raw capture is turned into a compact recording by replayer.compile.

Raw lines (one JSON object per line, "w" = wall-clock arrival in epoch seconds):
  {"k": "hdr", ...}                                   first line
  {"k": "meta", "opcua": {...}}                       OPC UA address-space metadata (types, units, ranges, folders)
  {"k": "m", "w", "t": topic, "p": payload text, "r": retain}
  {"k": "o", "w", "n": node index, "v": value, "vt": variant type, "st": source ts (epoch), "s": status code}
  {"k": "b", "w", "u": unit, "tb": "hr"|"coil", "a": start, "r": [values]}   Modbus block (only when it changed)
"""
import asyncio
import gzip
import json
import logging
import time
from datetime import datetime, timezone

from . import pointtable

log = logging.getLogger('replayer.record')


def _epoch(d):
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def _jsonable(v):
    if isinstance(v, datetime):
        return {'$dt': _epoch(v)}
    if isinstance(v, (bytes, bytearray)):
        return v.decode('utf-8', 'replace')
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return str(v)


class Writer:
    def __init__(self, path):
        self.f = gzip.open(path, 'wt', encoding='utf-8', compresslevel=6)
        self.n = {'m': 0, 'o': 0, 'b': 0}

    def put(self, obj):
        k = obj.get('k')
        if k in self.n:
            self.n[k] += 1
        self.f.write(json.dumps(obj, ensure_ascii=False, separators=(',', ':')) + '\n')

    def close(self):
        self.f.close()


async def _opcua_meta(cli, nids):
    """Address-space facts the replayer needs to rebuild the same nodes: per variable its type, unit, range, access,
    and the chain of parent folders / objects up to the Objects folder; object types with their supertypes."""
    from asyncua import ua
    A = ua.AttributeIds
    nodes, parents, types = {}, {}, {}
    ns = await cli.get_namespace_array()

    async def describe(n, cls_hint=None):
        res = await n.read_attributes([A.BrowseName, A.DisplayName, A.Description, A.NodeClass])
        bn, dn, de, nc = (r.Value.Value if r.Value is not None else None for r in res)
        return {'bn': [bn.NamespaceIndex, bn.Name] if bn else None, 'dn': dn.Text if dn else None, 'de': de.Text if de else None,
                'nc': int(nc) if nc is not None else None}

    async def add_type(tid):
        key = tid.to_string()
        if key in types or tid.NamespaceIndex == 0:
            return
        tn = cli.get_node(tid)
        d = await describe(tn)
        sup = None
        try:
            refs = await tn.get_references(refs=ua.ObjectIds.HasSubtype, direction=ua.BrowseDirection.Inverse)
            if refs:
                sup = refs[0].NodeId
        except Exception:
            pass
        d['super'] = sup.to_string() if sup is not None else None
        types[key] = d
        if sup is not None:
            await add_type(sup)

    async def add_parent(n):
        key = n.nodeid.to_string()
        if key in parents or n.nodeid.NamespaceIndex == 0:
            return
        d = await describe(n)
        try:
            td = await n.read_type_definition()
            d['td'] = td.to_string() if td is not None else None
            if td is not None:
                await add_type(td)
        except Exception:
            d['td'] = None
        try:
            en = await n.read_attribute(A.EventNotifier)
            d['ev'] = int(en.Value.Value or 0)
        except Exception:
            d['ev'] = 0
        par = await n.get_parent()
        d['parent'] = par.nodeid.to_string() if par is not None else None
        # reference type to the parent (Organizes / HasComponent)
        try:
            refs = await n.get_references(direction=ua.BrowseDirection.Inverse)
            hier = [r for r in refs if r.IsForward is False and par is not None and r.NodeId == par.nodeid]
            d['ref'] = hier[0].ReferenceTypeId.to_string() if hier else None
        except Exception:
            d['ref'] = None
        parents[key] = d
        if par is not None:
            await add_parent(par)

    for nid in nids:
        n = cli.get_node(nid)
        try:
            d = await describe(n)
            res = await n.read_attributes([A.DataType, A.AccessLevel, A.Value, A.Historizing])
            d['dt'] = res[0].Value.Value.to_string() if res[0].Value is not None else None
            d['al'] = int(res[1].Value.Value) if res[1].Value is not None else 1
            d['vt'] = res[2].Value.VariantType.value if res[2].Value is not None else None
            d['v'] = _jsonable(res[2].Value.Value) if res[2].Value is not None else None
            d['hist'] = bool(res[3].Value.Value) if res[3].Value is not None else False
            td = await n.read_type_definition()
            d['td'] = td.to_string() if td is not None else None
            for prop in ('EngineeringUnits', 'EURange'):
                try:
                    pv = await cli.get_node(ua.NodeId(f'{n.nodeid.Identifier}#{prop}', n.nodeid.NamespaceIndex)).read_value()
                except Exception:
                    child = None
                    try:
                        child = await n.get_child([f'0:{prop}'])
                        pv = await child.read_value()
                    except Exception:
                        pv = None
                if prop == 'EngineeringUnits' and pv is not None:
                    d['eu'] = {'ns': pv.NamespaceUri, 'id': pv.UnitId, 'dn': pv.DisplayName.Text, 'de': pv.Description.Text}
                elif prop == 'EURange' and pv is not None:
                    d['range'] = [pv.Low, pv.High]
            par = await n.get_parent()
            d['parent'] = par.nodeid.to_string() if par is not None else None
            nodes[nid] = d
            if par is not None:
                await add_parent(par)
        except Exception as ex:
            log.warning('OPC UA node %s: %s', nid, ex)
    return {'namespaces': ns, 'nodes': nodes, 'parents': parents, 'types': types}


async def capture(args):
    pkg = pointtable.load(args.package)
    pid = args.plant or pkg['plant']
    pts = pkg['points']
    w = Writer(args.out)
    w.put({'k': 'hdr', 'plant': pid, 'started': datetime.now().astimezone().isoformat(timespec='seconds'), 'mqtt': args.mqtt, 'opcua': args.opcua,
           'modbus': args.modbus, 'duration_h': args.hours})
    stop = asyncio.Event()
    state = {'sim0': None, 'sim': None}
    tasks = []

    # ---- OPC UA ----
    if args.opcua:
        from asyncua import Client, ua
        nids = [p['address'] for p in pts if p['proto'] == 'opcua']
        ns_idx = nids[0].split(';')[0] if nids else 'ns=2'
        plant_nodes = [f'{ns_idx};s={pid}/Plant/{x}' for x in ('SimTime', 'SimTimeText', 'TimeMode', 'Speed', 'Paused', 'Castings', 'GoodParts',
                                                                 'ScrapParts', 'EnergyKWh')]
        cli = Client(args.opcua, timeout=30)
        await cli.connect()
        ok_plant = []
        for x in plant_nodes:
            try:
                await cli.get_node(x).read_value()
                ok_plant.append(x)
            except Exception:
                pass
        allnids = nids + ok_plant
        meta = await _opcua_meta(cli, allnids)
        w.put({'k': 'meta', 'opcua': meta, 'order': allnids})
        log.info('OPC UA meta: %d variables, %d parents, %d types', len(meta['nodes']), len(meta['parents']), len(meta['types']))
        if args.set_speed and ok_plant:
            sp = f'{ns_idx};s={pid}/Plant/Speed'
            await cli.get_node(sp).write_value(ua.DataValue(ua.Variant(float(args.set_speed), ua.VariantType.Double)))
            log.info('source speed set to %s', args.set_speed)
        index = {nid: i for i, nid in enumerate(allnids)}
        simtime = f'{ns_idx};s={pid}/Plant/SimTime'

        class H:
            def datachange_notification(self, node, val, data):
                nid = node.nodeid.to_string()
                i = index.get(nid)
                if i is None:
                    return
                dv = data.monitored_item.Value
                st = _epoch(dv.SourceTimestamp)
                w.put({'k': 'o', 'w': round(time.time(), 3), 'n': i, 'v': _jsonable(val), 'vt': dv.Value.VariantType.value if dv.Value else None,
                       'st': st, 's': dv.StatusCode.value if dv.StatusCode is not None else 0})
                if nid == simtime and isinstance(val, datetime):
                    s = _epoch(val)
                    state['sim'] = s
                    if state['sim0'] is None:
                        state['sim0'] = s
                    if args.hours and s - state['sim0'] >= args.hours * 3600:
                        stop.set()

            def status_change_notification(self, status):
                log.warning('OPC UA subscription status %s', status)

        sub = await cli.create_subscription(ua.CreateSubscriptionParameters(RequestedPublishingInterval=100, RequestedLifetimeCount=10000,
                                                                            RequestedMaxKeepAliveCount=100, MaxNotificationsPerPublish=0,
                                                                            PublishingEnabled=True, Priority=0), H())
        nodes = [cli.get_node(x) for x in allnids]
        for i in range(0, len(nodes), 200):
            await sub.subscribe_data_change(nodes[i:i + 200], sampling_interval=0, queuesize=100)
        log.info('OPC UA subscribed %d nodes', len(nodes))

    # ---- MQTT ----
    async def mqtt_task():
        from amqtt.client import MQTTClient
        c = MQTTClient(client_id=f'replayer-rec-{int(time.time()) % 100000}', config={'auto_reconnect': True})
        await c.connect(args.mqtt.rstrip('/') + '/')
        await c.subscribe([(f'vfactory/{pid}/#', 0), (f'vda5050/v2/{pid}/#', 0)])
        log.info('MQTT subscribed vfactory/%s/#', pid)
        while not stop.is_set():
            try:
                msg = await asyncio.wait_for(c.deliver_message(), timeout=2)
            except asyncio.TimeoutError:
                continue
            t = msg.topic
            if t.endswith('/set') or t.endswith('/set/ack'):
                continue
            w.put({'k': 'm', 'w': round(time.time(), 3), 't': t, 'p': (msg.data or b'').decode('utf-8', 'replace'), 'r': bool(getattr(msg, 'retain', False))})
        await c.disconnect()

    # ---- Modbus ----
    async def modbus_task():
        from pymodbus.client import AsyncModbusTcpClient
        host, _, port = args.modbus.replace('modbus://', '').partition(':')
        blocks = pointtable.modbus_blocks(pts)
        cli = AsyncModbusTcpClient(host, port=int(port or 502))
        await cli.connect()
        last = {}
        log.info('Modbus polling %d blocks', len(blocks))
        while not stop.is_set():
            for unit, table, start, count in blocks:
                try:
                    if table == 'coil':
                        r = await cli.read_coils(start, count=count, slave=unit)
                        vals = None if r.isError() else [int(b) for b in r.bits[:count]]
                    else:
                        r = await cli.read_holding_registers(start, count=count, slave=unit)
                        vals = None if r.isError() else list(r.registers)
                except Exception as ex:
                    log.debug('modbus %s', ex)
                    vals = None
                if vals is not None and last.get((unit, table, start)) != vals:
                    last[(unit, table, start)] = vals
                    w.put({'k': 'b', 'w': round(time.time(), 3), 'u': unit, 'tb': table, 'a': start, 'r': vals})
            await asyncio.sleep(args.modbus_period)
        cli.close()

    if args.mqtt:
        tasks.append(asyncio.create_task(mqtt_task()))
    if args.modbus:
        tasks.append(asyncio.create_task(modbus_task()))

    t0 = time.time()
    last_log = 0
    try:
        while not stop.is_set():
            await asyncio.sleep(1)
            if args.wall_limit and time.time() - t0 > args.wall_limit:
                break
            if time.time() - last_log > 60:
                last_log = time.time()
                sim_h = (state['sim'] - state['sim0']) / 3600 if state['sim0'] else 0
                log.info('captured %s, sim %.2f h', w.n, sim_h)
                w.f.flush()
    finally:
        stop.set()
        for t in tasks:
            try:
                await asyncio.wait_for(t, timeout=10)
            except Exception:
                t.cancel()
        if args.opcua:
            try:
                await cli.disconnect()
            except Exception:
                pass
        w.close()
    log.info('capture done: %s', w.n)
