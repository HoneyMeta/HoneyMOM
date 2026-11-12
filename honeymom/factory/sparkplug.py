"""Sparkplug B（Eclipse Tahu sparkplug_b.proto）载荷的最小编解码：不依赖 protobuf 库，只实现采集要用的字段。

主题：spBv1.0/<组>/<消息类型>/<边缘节点>[/<设备>]，消息类型 NBIRTH NDATA NDEATH DBIRTH DDATA DDEATH NCMD DCMD STATE。
载荷 Payload：timestamp(1, uint64 毫秒)、metrics(2, Metric)、seq(3)、uuid(4)、body(5)。
Metric：name(1)、alias(2)、timestamp(3)、datatype(4)、is_historical(5)、is_transient(6)、is_null(7)、metadata(8)、properties(9)、
        int_value(10)、long_value(11)、float_value(12)、double_value(13)、boolean_value(14)、string_value(15)、bytes_value(16)、dataset(17)、template(18)。
BIRTH 里带指标名 + 别名（alias）+ 类型；DATA 里常常只带别名 → 按出生证书还原指标名。属性集（properties）里的 engUnit / engLow / engHigh / description
（常见的 Ignition / Tahu 约定）在发现时显示出来。
采集点地址：<组>/<边缘节点>/<设备>:<指标名>（节点级指标没有设备段：<组>/<边缘节点>:<指标名>）。
"""
from __future__ import annotations

from ..i18n import _tr
import struct

DATATYPES = {1: 'Int8', 2: 'Int16', 3: 'Int32', 4: 'Int64', 5: 'UInt8', 6: 'UInt16', 7: 'UInt32', 8: 'UInt64', 9: 'Float', 10: 'Double', 11: 'Boolean',
             12: 'String', 13: 'DateTime', 14: 'Text', 15: 'UUID', 16: 'DataSet', 17: 'Bytes', 18: 'File', 19: 'Template', 20: 'PropertySet'}
DT_SIMPLE = {**{k: 'int' for k in range(1, 9)}, 9: 'float', 10: 'float', 11: 'bool', 12: 'string', 13: 'int', 14: 'string', 15: 'string'}
SIGNED = {1: 8, 2: 16, 3: 32, 4: 64}


# ───────────────────────── protobuf 线格式 ─────────────────────────
def _varint(b, i):
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        if x < 0x80:
            return r, i
        s += 7


def _fields(b):
    i, n = 0, len(b)
    while i < n:
        key, i = _varint(b, i)
        f, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 1:
            v, i = b[i:i + 8], i + 8
        elif wt == 2:
            ln, i = _varint(b, i)
            v, i = b[i:i + ln], i + ln
        elif wt == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError(_tr('不支持的 protobuf 线类型 {wt}', wt=wt))
        yield f, wt, v


def _signed(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def _propset(b):
    keys, vals = [], []
    for f, wt, v in _fields(b):
        if f == 1:
            keys.append(v.decode('utf-8', 'replace'))
        elif f == 2:
            vals.append(_propval(v))
    return dict(zip(keys, vals))


def _propval(b):
    typ, out = None, None
    for f, wt, v in _fields(b):
        if f == 1:
            typ = v
        elif f == 3:
            out = v
        elif f == 4:
            out = v
        elif f == 5:
            out = struct.unpack('<f', v)[0]
        elif f == 6:
            out = struct.unpack('<d', v)[0]
        elif f == 7:
            out = bool(v)
        elif f == 8:
            out = v.decode('utf-8', 'replace')
    if typ in SIGNED and isinstance(out, int):
        out = _signed(out, SIGNED[typ])
    return out


def decode_metric(b) -> dict:
    m = {}
    for f, wt, v in _fields(b):
        if f == 1:
            m['name'] = v.decode('utf-8', 'replace')
        elif f == 2:
            m['alias'] = v
        elif f == 3:
            m['timestamp'] = v
        elif f == 4:
            m['datatype'] = v
        elif f == 5:
            m['historical'] = bool(v)
        elif f == 7:
            m['is_null'] = bool(v)
        elif f == 9:
            m['properties'] = _propset(v)
        elif f == 10:
            m['value'] = v
        elif f == 11:
            m['value'] = v
        elif f == 12:
            m['value'] = struct.unpack('<f', v)[0]
        elif f == 13:
            m['value'] = struct.unpack('<d', v)[0]
        elif f == 14:
            m['value'] = bool(v)
        elif f in (15,):
            m['value'] = v.decode('utf-8', 'replace')
        elif f == 16:
            m['value'] = v.hex()
    dt = m.get('datatype')
    if dt in SIGNED and isinstance(m.get('value'), int):
        m['value'] = _signed(m['value'], SIGNED[dt])
    if m.get('is_null'):
        m['value'] = None
    return m


def decode(b: bytes) -> dict:
    out = {'metrics': []}
    for f, wt, v in _fields(b):
        if f == 1:
            out['timestamp'] = v
        elif f == 2:
            out['metrics'].append(decode_metric(v))
        elif f == 3:
            out['seq'] = v
        elif f == 4:
            out['uuid'] = v.decode('utf-8', 'replace')
    return out


def parse_topic(topic: str):
    """spBv1.0/G/NDATA/E[/D] → (组, 类型, 边缘节点, 设备)；不是 Sparkplug 主题返回 None。"""
    p = topic.split('/')
    if len(p) < 4 or p[0] != 'spBv1.0':
        return None
    return p[1], p[2], p[3], (p[4] if len(p) > 4 else None)


def address(group, node, device, metric) -> str:
    return f"{group}/{node}{'/' + device if device else ''}:{metric}"


# ───────────────────────── 编码（测试发布器 / 网关演示用） ─────────────────────────
def _ev(n):
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n:
            out.append(x | 0x80)
        else:
            out.append(x)
            return bytes(out)


def _k(f, wt):
    return _ev((f << 3) | wt)


def _ld(f, b):
    return _k(f, 2) + _ev(len(b)) + b


def encode_metric(name=None, value=None, datatype=None, alias=None, timestamp=None, properties=None) -> bytes:
    b = b''
    if name is not None:
        b += _ld(1, name.encode())
    if alias is not None:
        b += _k(2, 0) + _ev(alias)
    if timestamp is not None:
        b += _k(3, 0) + _ev(int(timestamp))
    if datatype is None:
        datatype = 11 if isinstance(value, bool) else 3 if isinstance(value, int) else 10 if isinstance(value, float) else 12
    b += _k(4, 0) + _ev(datatype)
    if properties:
        ks = b''.join(_ld(1, k.encode()) for k in properties)
        vs = b''
        for v in properties.values():
            if isinstance(v, str):
                vs += _ld(2, _k(1, 0) + _ev(12) + _ld(8, v.encode()))
            elif isinstance(v, float):
                vs += _ld(2, _k(1, 0) + _ev(10) + _k(6, 1) + struct.pack('<d', v))
            else:
                vs += _ld(2, _k(1, 0) + _ev(3) + _k(3, 0) + _ev(int(v) & 0xFFFFFFFF))
        b += _ld(9, ks + vs)
    if value is None:
        b += _k(7, 0) + _ev(1)
    elif datatype == 11:
        b += _k(14, 0) + _ev(1 if value else 0)
    elif datatype in (1, 2, 3, 5, 6, 7):
        b += _k(10, 0) + _ev(int(value) & 0xFFFFFFFF)
    elif datatype in (4, 8, 13):
        b += _k(11, 0) + _ev(int(value) & 0xFFFFFFFFFFFFFFFF)
    elif datatype == 9:
        b += _k(12, 5) + struct.pack('<f', float(value))
    elif datatype == 10:
        b += _k(13, 1) + struct.pack('<d', float(value))
    else:
        b += _ld(15, str(value).encode())
    return b


def encode(metrics: list[dict], timestamp=None, seq=None) -> bytes:
    b = b''
    if timestamp is not None:
        b += _k(1, 0) + _ev(int(timestamp))
    for m in metrics:
        b += _ld(2, encode_metric(**m))
    if seq is not None:
        b += _k(3, 0) + _ev(seq)
    return b


class Births:
    """出生证书缓存：(组, 节点, 设备) → {别名: 指标名}，以及每个指标的类型 / 属性（发现用）。"""

    def __init__(self):
        self.alias: dict[tuple, dict] = {}
        self.meta: dict[str, dict] = {}

    def on_message(self, topic: str, payload: bytes):
        """返回 [(地址, 值, 时间毫秒, 指标信息)]；BIRTH 消息的指标同样作为值返回（出生证书带当前值）。"""
        t = parse_topic(topic)
        if not t:
            return []
        group, mtype, node, dev = t
        if mtype not in ('NBIRTH', 'DBIRTH', 'NDATA', 'DDATA'):
            return []
        p = decode(payload)
        key = (group, node, dev)
        out = []
        if mtype.endswith('BIRTH'):
            self.alias[key] = {}
        for m in p['metrics']:
            name = m.get('name')
            if name is None and 'alias' in m:
                name = self.alias.get(key, {}).get(m['alias'])
            if name is None:
                continue
            if mtype.endswith('BIRTH') and 'alias' in m:
                self.alias[key][m['alias']] = name
            addr = address(group, node, dev, name)
            if mtype.endswith('BIRTH'):
                pr = m.get('properties') or {}
                self.meta[addr] = {'datatype': DATATYPES.get(m.get('datatype'), m.get('datatype')), 'dtype': DT_SIMPLE.get(m.get('datatype')),
                                   'unit': pr.get('engUnit') or pr.get('EngUnit') or pr.get('unit'), 'description': pr.get('description') or pr.get('Documentation'),
                                   'low': pr.get('engLow'), 'high': pr.get('engHigh'), 'birth': mtype, 'alias': m.get('alias')}
            out.append((addr, m.get('value'), m.get('timestamp') or p.get('timestamp'), self.meta.get(addr)))
        return out
