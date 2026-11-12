# HoneyMOM plant data replayer - SPDX-License-Identifier: AGPL-3.0-or-later
"""Reads a delivery package (zip with points.xlsx = sheets 设备 / 点表 / 协议 / 说明) and rewrites its endpoints."""
import io
import re
import zipfile

SHEET_DEVICES, SHEET_POINTS, SHEET_PROTOCOLS = '设备', '点表', '协议'


def _rows(ws):
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    if not rows:
        return []
    head = [str(h or '').strip() for h in rows[0]]
    return [dict(zip(head, r)) for r in rows[1:] if any(v not in (None, '') for v in r)]


def load(path_or_bytes) -> dict:
    """{'devices': [...], 'points': [...], 'protocols': [[proto, item, value, note]], 'plant': id}"""
    import openpyxl
    data = path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else open(path_or_bytes, 'rb').read()
    z = zipfile.ZipFile(io.BytesIO(data))
    wb = openpyxl.load_workbook(io.BytesIO(z.read('points.xlsx')), read_only=True, data_only=True)
    names = wb.sheetnames
    dev = _rows(wb[SHEET_DEVICES]) if SHEET_DEVICES in names else _rows(wb.worksheets[0])
    pts = _rows(wb[SHEET_POINTS]) if SHEET_POINTS in names else _rows(wb.worksheets[1])
    ws = wb[SHEET_PROTOCOLS] if SHEET_PROTOCOLS in names else wb.worksheets[2]
    protos = [list(r) for r in ws.iter_rows(values_only=True)][1:]
    out = {'devices': dev, 'points': [], 'protocols': protos, 'plant': None}
    for p in pts:
        proto = str(p.get('协议') or '').strip().lower()
        addr = str(p.get('地址') or '').strip()
        if not proto or not addr:
            continue
        out['points'].append({'code': p.get('点位编码'), 'device': p.get('设备编码'), 'name': p.get('含义'), 'unit': p.get('单位') or '',
                              'dtype': str(p.get('数据类型') or '').lower(), 'rw': str(p.get('读写') or 'R').upper(), 'use': p.get('用途'),
                              'proto': proto, 'address': addr, 'range': p.get('正常范围'), 'note': str(p.get('备注') or '')})
    for r in protos:
        if r and str(r[0] or '') == 'MQTT' and str(r[1] or '') == '主题前缀':
            m = re.match(r'vfactory/([^/]+)/', str(r[2] or ''))
            if m:
                out['plant'] = m.group(1)
    if not out['plant']:
        for p in out['points']:
            m = re.match(r'ns=\d+;s=([^/]+)/', p['address']) if p['proto'] == 'opcua' else None
            if m:
                out['plant'] = m.group(1)
                break
    return out


def modbus_regs(point) -> int:
    """Register count of a Modbus point: the 备注 column says e.g. "float32 ABCD，2 个寄存器"; otherwise 1."""
    m = re.search(r'(\d+)\s*个寄存器', point.get('note') or '')
    if m:
        return int(m.group(1))
    n = (point.get('note') or '').lower()
    return 4 if '64' in n else 2 if ('32' in n) else 1


def modbus_blocks(points, cap_regs=120, cap_bits=1000):
    """Modbus points → contiguous read blocks [(unit, table, start, count)] (table: hr | coil)."""
    groups = {}
    for p in points:
        if p['proto'] != 'modbus':
            continue
        try:
            unit, table, addr = p['address'].split(':')
            unit, addr, table = int(unit), int(addr), table.lower()
        except ValueError:
            continue
        table = {'co': 'coil', 'ir': 'hr', 'di': 'coil'}.get(table, table)
        n = 1 if table == 'coil' else modbus_regs(p)
        groups.setdefault((unit, table), []).append((addr, n))
    blocks = []
    for (unit, table), items in sorted(groups.items()):
        items.sort()
        cap = cap_bits if table == 'coil' else cap_regs
        cur = None
        for addr, n in items:
            if cur and addr + n - cur[0] <= cap and addr <= cur[1] + 8:
                cur[1] = max(cur[1], addr + n)
            else:
                if cur:
                    blocks.append((unit, table, cur[0], cur[1] - cur[0]))
                cur = [addr, addr + n]
        if cur:
            blocks.append((unit, table, cur[0], cur[1] - cur[0]))
    return blocks


def rewrite_endpoints(data: bytes, host: str, mqtt_port: int, opcua_port: int, modbus_port: int | None, opcua_path='/vfactory/') -> bytes:
    """Copy of the delivery package with the 协议 sheet (and README) pointing at the replayer. Everything else is unchanged."""
    import openpyxl
    zin = zipfile.ZipFile(io.BytesIO(data))
    wb = openpyxl.load_workbook(io.BytesIO(zin.read('points.xlsx')))
    ws = wb[SHEET_PROTOCOLS] if SHEET_PROTOCOLS in wb.sheetnames else wb.worksheets[2]
    old = {}
    opc = f'opc.tcp://{host}:{opcua_port}{opcua_path}'
    for row in ws.iter_rows(min_row=2):
        proto, item = str(row[0].value or ''), str(row[1].value or '')
        if proto.startswith('OPC') and item == '端点':
            old[str(row[2].value).split()[0]] = opc
            row[2].value = opc
        elif proto == 'MQTT' and item == 'Broker':
            old[str(row[2].value).split()[0]] = f'{host}:{mqtt_port}'
            row[2].value = f'{host}:{mqtt_port}'
        elif proto.startswith('Modbus') and item == '端点':
            v = f'{host}:{modbus_port}' if modbus_port else '（回放器未开 Modbus）'
            old[str(row[2].value).split()[0]] = v
            row[2].value = v
    buf = io.BytesIO()
    wb.save(buf)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zo:
        for info in zin.infolist():
            b = zin.read(info.filename)
            if info.filename == 'points.xlsx':
                b = buf.getvalue()
            elif info.filename.lower() == 'readme.md':
                t = b.decode('utf-8')
                for k, v in old.items():
                    t = t.replace(k, v)
                b = t.encode('utf-8')
            zo.writestr(info, b)
    return out.getvalue()
