# HoneyMOM plant data replayer - SPDX-License-Identifier: AGPL-3.0-or-later
"""The three protocol endpoints of the replayer: an embedded MQTT broker (amqtt), an OPC UA server (asyncua) rebuilt from the
address-space metadata stored in the recording, and a Modbus TCP server (pymodbus) holding the recorded register image."""
import asyncio
import logging
from datetime import datetime, timezone

log = logging.getLogger('replayer')


# ───────────────────────── MQTT ─────────────────────────
class _ListSnapshotDict(dict):
    """amqtt walks the subscription table while awaiting; a client (un)subscribing meanwhile raises
    "dictionary changed size during iteration". Iterating over a copy avoids it."""

    def __iter__(self):
        return iter(list(dict.keys(self)))

    def keys(self):
        return list(dict.keys(self))

    def items(self):
        return list(dict.items(self))

    def values(self):
        return list(dict.values(self))


async def start_broker(host, port):
    from amqtt.broker import Broker
    cfg = {'listeners': {'default': {'type': 'tcp', 'bind': f'{host}:{port}'}},
           'plugins': {'amqtt.plugins.authentication.AnonymousAuthPlugin': {'allow_anonymous': True}}}
    b = Broker(cfg)
    if isinstance(getattr(b, '_subscriptions', None), dict):
        b._subscriptions = _ListSnapshotDict(b._subscriptions)
    await b.start()
    log.info('MQTT broker listening on %s:%s', host, port)
    return b


# ───────────────────────── OPC UA ─────────────────────────
class OpcUaServer:
    def __init__(self, meta: dict, order: list, endpoint: str):
        self.meta, self.order, self.endpoint = meta, order, endpoint
        self.server = None
        self.nids = []

    async def start(self):
        from asyncua import Server, ua
        s = self.server = Server()
        await s.init()
        s.set_endpoint(self.endpoint)
        s.set_server_name('HoneyMOM plant replayer')
        s.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        for uri in self.meta['namespaces'][2:]:
            idx = await s.register_namespace(uri)
            want = self.meta['namespaces'].index(uri)
            if idx != want:
                raise RuntimeError(f'OPC UA namespace {uri} got index {idx}, the recording needs {want}')
        await self._build(ua)
        await s.start()
        self.nids = [ua.NodeId.from_string(n) for n in self.order]
        log.info('OPC UA server listening on %s (%d variables)', self.endpoint, len(self.nids))

    async def _add(self, items, what):
        from asyncua import ua
        res = await self.server.iserver.isession.add_nodes(items)
        bad = [(it.RequestedNewNodeId.to_string(), r.StatusCode.name) for it, r in zip(items, res)
               if not r.StatusCode.is_good() and r.StatusCode.value != ua.StatusCodes.BadNodeIdExists]
        for b in bad[:10]:
            log.warning('OPC UA %s %s: %s', what, *b)

    async def _build(self, ua):
        m = self.meta
        N = ua.NodeId.from_string

        def qn(bn):
            return ua.QualifiedName(bn[1], bn[0])

        def lt(t):
            return ua.LocalizedText(t or '')

        # object types: supertypes first
        types = m.get('types') or {}
        done, items = set(), []

        def add_type(k):
            if k in done or k not in types:
                return
            t = types[k]
            if t.get('super') in types:
                add_type(t['super'])
            done.add(k)
            it = ua.AddNodesItem()
            it.RequestedNewNodeId = N(k)
            it.BrowseName = qn(t['bn'])
            it.NodeClass = ua.NodeClass.ObjectType
            it.ParentNodeId = N(t.get('super') or 'i=58')
            it.ReferenceTypeId = ua.NodeId(ua.ObjectIds.HasSubtype)
            at = ua.ObjectTypeAttributes()
            at.DisplayName, at.Description, at.IsAbstract = lt(t.get('dn')), lt(t.get('de')), False
            it.NodeAttributes = at
            items.append(it)
        for k in types:
            add_type(k)
        if items:
            await self._add(items, 'type')
        # folders / objects, parents before children
        par = m.get('parents') or {}

        def depth(k, n=0):
            p = par.get(k, {}).get('parent')
            return n if p not in par or n > 50 else depth(p, n + 1)
        items = []
        for k in sorted(par, key=depth):
            o = par[k]
            it = ua.AddNodesItem()
            it.RequestedNewNodeId = N(k)
            it.BrowseName = qn(o['bn'])
            it.NodeClass = ua.NodeClass.Object
            it.ParentNodeId = N(o.get('parent') or 'i=85')
            it.ReferenceTypeId = N(o.get('ref') or 'i=35')
            it.TypeDefinition = N(o.get('td') or 'i=61')
            at = ua.ObjectAttributes()
            at.DisplayName, at.Description, at.EventNotifier = lt(o.get('dn')), lt(o.get('de')), int(o.get('ev') or 0)
            it.NodeAttributes = at
            items.append(it)
        await self._add(items, 'object')
        # variables (+ EngineeringUnits / EURange properties)
        items = []
        for k in self.order:
            v = m['nodes'].get(k)
            if not v:
                continue
            nid = N(k)
            vt = ua.VariantType(v.get('vt') or 12)
            it = ua.AddNodesItem()
            it.RequestedNewNodeId = nid
            it.BrowseName = qn(v['bn'])
            it.NodeClass = ua.NodeClass.Variable
            it.ParentNodeId = N(v['parent'])
            it.ReferenceTypeId = ua.NodeId(ua.ObjectIds.HasComponent)
            it.TypeDefinition = N(v.get('td') or 'i=63')
            at = ua.VariableAttributes()
            at.DisplayName, at.Description = lt(v.get('dn')), lt(v.get('de'))
            at.DataType = N(v.get('dt') or 'i=12')
            at.Value = ua.Variant(_initial(v.get('v'), vt), vt)
            at.ValueRank = ua.ValueRank.Scalar
            at.ArrayDimensions = None
            al = int(v.get('al') or 1) & ~ua.AccessLevel.HistoryRead.mask       # no history in the replayer
            at.AccessLevel = at.UserAccessLevel = al
            at.Historizing = False
            at.WriteMask = at.UserWriteMask = 0
            it.NodeAttributes = at
            items.append(it)
            for prop, val, dt in (('EngineeringUnits', v.get('eu'), ua.ObjectIds.EUInformation), ('EURange', v.get('range'), ua.ObjectIds.Range)):
                if val is None:
                    continue
                if prop == 'EngineeringUnits':
                    x = ua.EUInformation(NamespaceUri=val.get('ns'), UnitId=int(val.get('id') if val.get('id') is not None else -1),
                                         DisplayName=lt(val.get('dn')), Description=lt(val.get('de')))
                else:
                    x = ua.Range(Low=float(val[0]), High=float(val[1]))
                p = ua.AddNodesItem()
                p.RequestedNewNodeId = ua.NodeId(f'{nid.Identifier}#{prop}', nid.NamespaceIndex)
                p.BrowseName = ua.QualifiedName(prop, 0)
                p.NodeClass = ua.NodeClass.Variable
                p.ParentNodeId = nid
                p.ReferenceTypeId = ua.NodeId(ua.ObjectIds.HasProperty)
                p.TypeDefinition = ua.NodeId(ua.ObjectIds.PropertyType)
                pa = ua.VariableAttributes()
                pa.DisplayName, pa.Description = lt(prop), lt(prop)
                pa.DataType = ua.NodeId(dt)
                pa.Value = ua.Variant(x, ua.VariantType.ExtensionObject)
                pa.ValueRank = ua.ValueRank.Scalar
                pa.AccessLevel = pa.UserAccessLevel = ua.AccessLevel.CurrentRead.mask
                p.NodeAttributes = pa
                items.append(p)
        for i in range(0, len(items), 1000):
            await self._add(items[i:i + 1000], 'variable')

    async def write(self, i, value, src_ts, status=0):
        from asyncua import ua
        k = self.order[i]
        v = self.meta['nodes'].get(k)
        if v is None:
            return
        vt = ua.VariantType(v.get('vt') or 12)
        try:
            var = ua.Variant(_coerce(value, vt), vt)
        except Exception:
            return
        kw = {'SourceTimestamp': src_ts, 'ServerTimestamp': datetime.now(timezone.utc)}
        if status:
            kw['StatusCode'] = ua.StatusCode(status)
        await self.server.write_attribute_value(self.nids[i], ua.DataValue(var, **kw))

    async def stop(self):
        if self.server:
            await self.server.stop()


def _initial(v, vt):
    from asyncua import ua
    if isinstance(v, dict) and '$dt' in v:
        return datetime.fromtimestamp(v['$dt'], timezone.utc)
    if isinstance(v, dict) and '$t' in v:
        return datetime.now(timezone.utc)
    return _coerce(v, vt) if v is not None else {ua.VariantType.String: '', ua.VariantType.Boolean: False}.get(vt, 0)


def _coerce(v, vt):
    from asyncua import ua
    if vt == ua.VariantType.Double or vt == ua.VariantType.Float:
        return float(v or 0)
    if vt in (ua.VariantType.Int32, ua.VariantType.Int16, ua.VariantType.Int64, ua.VariantType.UInt32, ua.VariantType.UInt16):
        return int(v or 0)
    if vt == ua.VariantType.Boolean:
        return bool(v)
    if vt == ua.VariantType.String:
        return '' if v is None else str(v)
    return v


# ───────────────────────── Modbus TCP ─────────────────────────
class ModbusServer:
    """One unit (slave id) per device; holding registers mirrored to input registers, coils mirrored to discrete inputs."""

    def __init__(self, blocks, host, port):
        self.blocks, self.host, self.port = blocks, host, port
        self.ctx = None
        self.task = None

    async def start(self):
        from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext, ModbusSlaveContext
        from pymodbus.server import StartAsyncTcpServer
        size = {}
        for unit, table, start, count in self.blocks:
            s = size.setdefault(unit, {'hr': 0, 'coil': 0})
            s[table] = max(s[table], start + count)
        units = {}
        for unit, s in size.items():
            # pymodbus adds 1 to every request address (PDU address 0 = data block address 1): blocks start at 1
            hr = ModbusSequentialDataBlock(1, [0] * (s['hr'] + 16))
            co = ModbusSequentialDataBlock(1, [False] * (s['coil'] + 16))
            units[unit] = ModbusSlaveContext(di=co, co=co, hr=hr, ir=hr)
        self.ctx = ModbusServerContext(slaves=units, single=False)
        self.task = asyncio.create_task(StartAsyncTcpServer(context=self.ctx, address=(self.host, self.port)))
        await asyncio.sleep(0.3)
        if self.task.done() and self.task.exception():
            raise self.task.exception()
        log.info('Modbus TCP server listening on %s:%s (units %s)', self.host, self.port, sorted(units))

    def set(self, unit, table, addr, vals):
        try:
            if table == 'coil':
                self.ctx[unit].setValues(1, addr, [bool(x) for x in vals])
            else:
                self.ctx[unit].setValues(3, addr, [int(x) & 0xFFFF for x in vals])
        except Exception:
            log.debug('modbus set %s %s %s', unit, table, addr, exc_info=True)

    async def stop(self):
        try:
            from pymodbus.server import ServerAsyncStop
            await ServerAsyncStop()
        except Exception:
            pass
        if self.task:
            self.task.cancel()
