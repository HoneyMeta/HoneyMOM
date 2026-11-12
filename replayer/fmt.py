# HoneyMOM plant data replayer - SPDX-License-Identifier: AGPL-3.0-or-later
"""Recording format "honeymom-replay/1": gzip JSONL.

Line 1 is the header (JSON object): plant, t0 (recording start, ISO 8601 with offset), duration_ms, topics, topic_units,
nodes (OPC UA node ids), opcua (address-space metadata), modbus blocks, ids (identifier patterns whose embedded date
moves with the time shift) and counts.
Every further line is one event (JSON array), ordered by time; dt = milliseconds since the previous event:
  ["P", dt, topic, value, ts_off]          MQTT point in the standard payload {"value","unit","quality":"good","ts","sim_ts","wall_ts"}
                                           (unit from header.topic_units). ts_off null: ts = event time, whole seconds;
                                           int: ts = event time + ts_off ms, written with milliseconds (action signals);
                                           ["s", ms]: ts = event time + ms, whole seconds (retained values keep their old stamp)
  ["M", dt, topic, payload, retain]        any other MQTT message; payload is JSON (or a plain string) with time tokens
  ["O", dt, node, value, ts_off, status]   OPC UA value (SourceTimestamp = event time + ts_off ms; status = OPC UA status code)
  ["B", dt, unit, table, address, [v…]]    Modbus registers (table "hr") or coils ("coil") starting at address
Time tokens inside payloads / string values: "@@T<ms>|<f>" = recording time t0 + ms rendered as f (i: ISO seconds+offset,
m: ISO milliseconds+offset, s: "YYYY-MM-DD HH:MM:SS" local), "@@W|m" = the wall clock when published, {"$t": ms} = a DateTime.
"""
import gzip
import json
import re
from datetime import datetime, timedelta

FORMAT = 'honeymom-replay/1'
TOKEN = '@@'
RE_ISO = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?([+-]\d{2}:\d{2}|Z)$')
RE_LOCAL = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')
STD_KEYS = ('value', 'unit', 'quality', 'ts', 'sim_ts', 'wall_ts')


def render(d: datetime, f: str) -> str:
    """d is an aware datetime in the target time zone."""
    if f == 's':
        return d.strftime('%Y-%m-%d %H:%M:%S')
    return d.isoformat(timespec='milliseconds' if f == 'm' else 'seconds')


class Recording:
    def __init__(self, path):
        self.path = path
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            self.header = json.loads(f.readline())
        if self.header.get('format') != FORMAT:
            raise ValueError(f'{path}: not a {FORMAT} recording')
        self.t0 = datetime.fromisoformat(self.header['t0'])
        self.duration_ms = int(self.header['duration_ms'])

    def events(self):
        with gzip.open(self.path, 'rt', encoding='utf-8') as f:
            f.readline()
            t = 0
            for line in f:
                ev = json.loads(line)
                t += ev[1]
                ev[1] = t
                yield ev

    def summary(self):
        h = self.header
        return {'plant': h['plant'], 't0': h['t0'], 'duration_h': round(self.duration_ms / 3600000, 2), 'source': h.get('source'),
                'mqtt_topics': len(h['topics']), 'opcua_nodes': len(h['nodes']), 'modbus_blocks': len(h.get('modbus', {}).get('blocks', [])),
                'counts': h.get('counts')}


def shift_date6(s: str, days: int) -> str:
    """'261111' (yymmdd) + days."""
    d = datetime.strptime(s, '%y%m%d') + timedelta(days=days)
    return d.strftime('%y%m%d')
