"""OPC UA 浏览（接入中心）：列子节点和节点属性（DisplayName / Description / DataType / EngineeringUnits / 当前值 / 可写），每次请求连一次。"""
from __future__ import annotations

import asyncio

DT_SIMPLE = {'Boolean': 'bool', 'SByte': 'int', 'Byte': 'int', 'Int16': 'int', 'UInt16': 'int', 'Int32': 'int', 'UInt32': 'int', 'Int64': 'int', 'UInt64': 'int',
             'Float': 'float', 'Double': 'float', 'String': 'string', 'LocalizedText': 'string', 'DateTime': 'string', 'ByteString': 'string'}


async def _attrs(cli, node, with_children=False):
    from asyncua import ua
    out = {'nodeid': node.nodeid.to_string()}
    try:
        out['browse_name'] = (await node.read_browse_name()).Name
        out['display_name'] = (await node.read_display_name()).Text
        nc = await node.read_node_class()
        out['node_class'] = nc.name
    except Exception as ex:
        out['error'] = str(ex)[:120]
        return out
    try:
        d = await node.read_description()
        out['description'] = d.Text if d and d.Text else None
    except Exception:
        out['description'] = None
    if nc == ua.NodeClass.Variable:
        try:
            dtn = await cli.get_node(await node.read_data_type()).read_browse_name()
            out['datatype'] = dtn.Name
            out['dtype'] = DT_SIMPLE.get(dtn.Name, 'string')
        except Exception:
            pass
        try:
            v = await node.read_value()
            out['value'] = v if isinstance(v, (int, float, bool, str)) or v is None else str(v)[:120]
        except Exception:
            out['value'] = None
        try:
            al = await node.read_attribute(ua.AttributeIds.UserAccessLevel)
            out['writable'] = bool(al.Value.Value & 2)
        except Exception:
            out['writable'] = False
        try:
            for ch in await node.get_properties():
                bn = (await ch.read_browse_name()).Name
                if bn == 'EngineeringUnits':
                    eu = await ch.read_value()
                    out['unit'] = eu.DisplayName.Text if eu and eu.DisplayName else None
                elif bn == 'EURange':
                    r = await ch.read_value()
                    out['range'] = [r.Low, r.High] if r else None
        except Exception:
            pass
    if with_children:
        try:
            out['has_children'] = bool(await node.get_children(refs=33))
        except Exception:
            out['has_children'] = False
    return out


async def browse(ds, nodeid: str | None = None, limit: int = 500) -> dict:
    from asyncua import Client, ua
    async with Client(ds['endpoint'], timeout=10) as cli:
        node = cli.get_node(nodeid) if nodeid else cli.nodes.objects
        me = await _attrs(cli, node)
        kids = (await node.get_children(refs=33))[:limit]          # HierarchicalReferences
        sem = asyncio.Semaphore(16)

        async def one(n):
            async with sem:
                return await _attrs(cli, n, with_children=True)
        items = await asyncio.gather(*(one(n) for n in kids))
        items = [x for x in items if x.get('node_class') in ('Object', 'Variable', 'ObjectType', None) or 'error' in x]
        if nodeid is None:
            items = [x for x in items if x.get('browse_name') != 'Server'] or items
        nss = await cli.get_namespace_array()
    items.sort(key=lambda x: (x.get('node_class') != 'Object', x.get('display_name') or ''))
    return {'node': me, 'children': items, 'namespaces': nss, 'truncated': len(kids) >= limit}


async def attributes(ds, nodes: list) -> list[dict]:
    from asyncua import Client
    async with Client(ds['endpoint'], timeout=10) as cli:
        out = []
        for n in nodes:
            nid, path = (n.get('nodeid'), n.get('path')) if isinstance(n, dict) else (n, None)
            a = await _attrs(cli, cli.get_node(nid))
            a['path'] = path
            out.append(a)
        return out
