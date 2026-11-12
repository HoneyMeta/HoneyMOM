"""点表 Excel（接入规范 / 交付包同一格式：docs/接入规范、VF docs/interface/点表格式.md）：读、对列、校验、导出模板。

读（read_xlsx）：找"设备"表和"点表"表（按表名，或按表头里有没有 设备编码 / 点位编码），表头按同义词对到标准列；
对不上的列交给 AI 对列（assist.map_columns；AI 关闭时只用同义词）。返回规范化的行（英文键）和对列结果。
校验（validate）：重复编码 / 重复地址、缺含义 / 单位、设备编码不存在、地址格式不对、取值不在范围里。每条问题 {sheet, row, col, level, msg}，
level = error（不能导入）/ warn（能导入，点位进待确认）。
"""
from __future__ import annotations

from ..i18n import _tr
import io
import re

DEV_COLS = ['设备编码', '设备名称', '设备类型', '型号', '厂家', '车间', '产线', '工位', 'X(m)', 'Y(m)', '宽(m)', '深(m)', '朝向(°)', '3D模型文件', '理想节拍(s)', '备注']
PT_COLS = ['点位编码', '设备编码', '含义', '单位', '数据类型', '读写', '用途', '协议', '地址', '缩放', '偏移', '采集周期(ms)', '报警下限', '报警上限', '正常范围', '备注']
DEV_KEYS = ['code', 'name', 'type', 'model', 'vendor', 'workshop', 'line', 'station', 'x', 'y', 'w', 'd', 'rot', 'model3d', 'ideal_cycle_s', 'remark']
PT_KEYS = ['code', 'device', 'meaning', 'unit', 'dtype', 'rw', 'role', 'protocol', 'address', 'scale', 'offset', 'period_ms', 'alarm_lo', 'alarm_hi',
           'normal_range', 'remark']
DEV_LABEL = dict(zip(DEV_KEYS, DEV_COLS))
PT_LABEL = dict(zip(PT_KEYS, PT_COLS))
SYN_DEV = {
    'code': ['设备编码', '设备编号', '设备代码', '资产编号', '设备号', 'equipment code', 'equipment', 'device code', 'device id', 'asset', 'code'],
    'name': ['设备名称', '名称', '设备名', 'device name', 'name'], 'type': ['设备类型', '类型', '类别', '设备类别', 'type', 'device type', 'category'],
    'model': ['型号', '规格型号', 'model'], 'vendor': ['厂家', '制造商', '供应商', '品牌', 'vendor', 'manufacturer', 'brand'],
    'workshop': ['车间', 'workshop'], 'line': ['产线', '生产线', '线体', '区域', 'line', 'area'], 'station': ['工位', 'station', 'workstation'],
    'x': ['x(m)', 'x', 'x坐标', '坐标x'], 'y': ['y(m)', 'y', 'y坐标', '坐标y'], 'w': ['宽(m)', '宽', '宽度', 'width'], 'd': ['深(m)', '深', '深度', '长度', 'depth'],
    'rot': ['朝向(°)', '朝向', '角度', '旋转', 'rotation', 'angle'], 'model3d': ['3d模型文件', '3d模型', '模型文件', 'model file', 'gltf', 'glb'],
    'ideal_cycle_s': ['理想节拍(s)', '理想节拍', '节拍', '标准节拍', 'ideal cycle', 'cycle time'], 'remark': ['备注', 'remark', 'note', 'notes']}
SYN_PT = {
    'code': ['点位编码', '点位', '点名', '位号', '变量名', '标签', '符号', '符号名', 'tag', 'tag name', 'tagname', 'point', 'name', 'symbol'],
    'device': ['设备编码', '设备', '所属设备', '设备编号', 'device', 'equipment'],
    'meaning': ['含义', '描述', '说明', '注释', '点位描述', '中文名', 'description', 'comment', 'desc'], 'unit': ['单位', '工程单位', 'unit', 'eu', 'units'],
    'dtype': ['数据类型', '类型', 'data type', 'datatype', 'type'], 'rw': ['读写', '读写属性', '访问', 'access', 'r/w', 'rw'], 'role': ['用途', 'role', 'purpose'],
    'protocol': ['协议', 'protocol'], 'address': ['地址', 'nodeid', 'node id', '主题', 'topic', '寄存器地址', '寄存器', 'address', 'logical address'],
    'scale': ['缩放', '系数', '倍率', 'scale', 'factor'], 'offset': ['偏移', 'offset'], 'period_ms': ['采集周期(ms)', '采集周期', '周期', 'period', 'scan rate', 'period(ms)'],
    'alarm_lo': ['报警下限', '下限', 'low limit', 'lo'], 'alarm_hi': ['报警上限', '上限', 'high limit', 'hi'], 'normal_range': ['正常范围', '范围', 'range', 'normal range'],
    'remark': ['备注', 'remark', 'note', 'notes']}
NUM_DEV = ('x', 'y', 'w', 'd', 'rot', 'ideal_cycle_s')
NUM_PT = ('scale', 'offset', 'period_ms', 'alarm_lo', 'alarm_hi')
ROLE_VALUES = ('state', 'alarm', 'count', 'power', 'energy', 'load', 'temp', 'quality', 'action', 'step', 'param', 'other')
PROTO_VALUES = ('opcua', 'mqtt', 'modbus', 'sparkplug', 'http', 'sql')
PROTO_ALIAS = {'opc ua': 'opcua', 'opc-ua': 'opcua', 'opcua': 'opcua', 'ua': 'opcua', 'mqtt': 'mqtt', 'modbus': 'modbus', 'modbus tcp': 'modbus', 'modbus_tcp': 'modbus',
               'sparkplug': 'sparkplug', 'sparkplug b': 'sparkplug', 'spb': 'sparkplug', 'http': 'http', 'sql': 'sql', '中间库': 'sql'}
ADDR_RX = {'opcua': re.compile(r'^(ns=\d+;|nsu=[^;]+;)?(i=\d+|s=.+|g=[0-9a-fA-F-]{36}|b=.+)$'),
           'modbus': re.compile(r'^\d{1,3}:(hr|ir|coil|co|di):\d{1,5}$', re.I),
           'mqtt': re.compile(r'^[^#+\s][^#+]*$'),
           'sparkplug': re.compile(r'^[^/#+:\s]+/[^/#+:\s]+(/[^/#+:\s]+)?:.+$')}
ADDR_HINT = {'opcua': 'OPC UA 地址要写完整 NodeId，如 ns=2;s=Line1/CNC1/Spindle/Load', 'modbus': 'Modbus 地址格式 从站:表:地址，如 11:hr:1000（表 hr / ir / coil / di）',
             'mqtt': 'MQTT 地址是完整主题，不能有通配符 + #', 'sparkplug': 'Sparkplug 地址格式 组/边缘节点/设备:指标名，如 G1/EDGE1/PRESS1:Pressure'}


def _norm_h(s) -> str:
    s = str(s or '').strip().lower().replace('（', '(').replace('）', ')')
    return re.sub(r'\s+', ' ', s)


def _bare(s) -> str:
    return re.sub(r'\(.*?\)|\s', '', _norm_h(s))


def map_headers(headers: list, kind: str) -> tuple[dict, list]:
    """表头 → {列序号: 标准键}；对不上的列返回 [(序号, 表头)]。"""
    syn = SYN_DEV if kind == 'device' else SYN_PT
    out, used, left = {}, set(), []
    for i, h in enumerate(headers):
        if h in (None, ''):
            continue
        hn, hb = _norm_h(h), _bare(h)
        hit = None
        for k, names in syn.items():
            if k in used:
                continue
            if any(hn == _norm_h(n) for n in names) or any(hb == _bare(n) for n in names if _bare(n)):
                hit = k
                break
        if hit:
            out[i] = hit
            used.add(hit)
        else:
            left.append((i, str(h)))
    return out, left


def _sheet_kind(ws) -> str | None:
    t = ws.title.strip()
    if t in ('设备', '设备表', '设备清单', 'devices', 'Devices'):
        return 'device'
    if t in ('点表', '点位', '采集点', 'points', 'Points', '变量表', 'tags', 'Tags'):
        return 'point'
    try:
        head = [str(x or '') for x in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
    except StopIteration:
        head = []
    hs = {_bare(h) for h in head}
    if '点位编码' in hs or ('地址' in hs and ('含义' in hs or '数据类型' in hs)) or {'name', 'logicaladdress'} <= hs or 'tag' in hs:
        return 'point'
    if '设备编码' in hs and ('设备名称' in hs or '设备类型' in hs):
        return 'device'
    return None


def _val(v):
    if isinstance(v, str):
        v = v.strip()
        return v if v != '' else None
    return v


def read_rows(headers: list, data: list, kind: str, mapping: dict | None = None) -> tuple[list, dict, list]:
    """表头 + 数据行 → (规范化行, 对列 {序号: 键}, 没对上的表头)。mapping 可以覆盖（{表头: 键}，AI / 人工对列的结果）。"""
    cols, left = map_headers(headers, kind)
    if mapping:
        for i, h in enumerate(headers):
            k = mapping.get(str(h))
            if k:
                cols = {j: kk for j, kk in cols.items() if kk != k and j != i}
                cols[i] = k
        left = [(i, h) for i, h in left if i not in cols]
    rows = []
    for n, r in enumerate(data, start=2):
        if r is None or all(_val(x) is None for x in r):
            continue
        d = {'_row': n}
        for i, k in cols.items():
            d[k] = _val(r[i]) if i < len(r) else None
        rows.append(d)
    return rows, cols, left


def read_xlsx(data: bytes, mapping: dict | None = None) -> dict:
    """读点表 Excel：{devices, points, columns: {device: {...}, point: {...}}, unmapped: {device: [...], point: [...]}, sheets, protocols}。"""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out = {'devices': [], 'points': [], 'columns': {}, 'unmapped': {}, 'sheets': [], 'protocols': [], 'headers': {}}
    for ws in wb.worksheets:
        kind = _sheet_kind(ws)
        out['sheets'].append({'name': ws.title, 'kind': kind})
        if ws.title.strip() == '协议':
            out['protocols'] = [[_val(x) for x in r] for r in ws.iter_rows(min_row=2, values_only=True) if r and any(r)]
            continue
        if kind not in ('device', 'point'):
            continue
        it = ws.iter_rows(values_only=True)
        try:
            headers = list(next(it))
        except StopIteration:
            continue
        rows, cols, left = read_rows(headers, list(it), kind, (mapping or {}).get(kind))
        key = 'devices' if kind == 'device' else 'points'
        if out[key]:
            continue
        out[key] = rows
        out['columns'][kind] = {str(headers[i]): k for i, k in cols.items()}
        out['unmapped'][kind] = [h for _i, h in left]
        out['headers'][kind] = [str(h) if h is not None else '' for h in headers]
    for d in out['devices']:
        _numify(d, NUM_DEV, out, _tr('设备'))
    for p in out['points']:
        _numify(p, NUM_PT, out, _tr('点表'))
        if p.get('protocol'):
            p['protocol'] = PROTO_ALIAS.get(str(p['protocol']).strip().lower(), str(p['protocol']).strip().lower())
        for k in ('code', 'device', 'address'):
            if p.get(k) is not None and not isinstance(p[k], str):
                p[k] = str(p[k]).rstrip('0').rstrip('.') if isinstance(p[k], float) else str(p[k])
    for d in out['devices']:
        if d.get('code') is not None and not isinstance(d['code'], str):
            d['code'] = str(d['code'])
    return out


def _numify(d, keys, out, sheet):
    for k in keys:
        v = d.get(k)
        if v is None or isinstance(v, (int, float)):
            continue
        try:
            d[k] = float(str(v).replace(',', ''))
        except ValueError:
            d.setdefault('_bad', []).append(k)


def endpoints_from(protocols: list) -> dict:
    """交付包"协议"表 → {mqtt: 'mqtt://host:port', opcua: 'opc.tcp://…', modbus: 'host:port', mqtt_prefix: 'vfactory/xxx/'}。"""
    out = {}
    for r in protocols or []:
        if len(r) < 3:
            continue
        proto, item, val = str(r[0] or ''), str(r[1] or ''), str(r[2] or '')
        if '未开' in val:
            continue
        if proto.startswith('OPC') and item == '端点':
            out['opcua'] = val.split()[0]
        elif proto == 'MQTT' and item == 'Broker':
            v = val.split()[0]
            out['mqtt'] = v if v.startswith('mqtt') else 'mqtt://' + v
        elif proto == 'MQTT' and item == '主题前缀':
            out['mqtt_prefix'] = val.split()[0]
        elif proto.startswith('Modbus') and item == '端点':
            out['modbus'] = val.split()[0].replace('modbus://', '')
    return out


# ───────────────────────── 校验 ─────────────────────────
def validate(devices: list, points: list, known_devices: set | None = None, raw_ok=True) -> list[dict]:
    from .model import norm_dtype
    issues = []

    def add(sheet, row, col, level, msg):
        issues.append({'sheet': sheet, 'row': row, 'col': col, 'level': level, 'msg': msg})
    dev_codes = set()
    for d in devices:
        if not d.get('code'):
            add(_tr('设备'), d['_row'], _tr('设备编码'), 'error', _tr('设备编码不能为空'))
            continue
        if d['code'] in dev_codes:
            add(_tr('设备'), d['_row'], _tr('设备编码'), 'error', _tr('设备编码重复：{code}', code=d['code']))
        dev_codes.add(d['code'])
        if not d.get('name'):
            add(_tr('设备'), d['_row'], _tr('设备名称'), 'warn', _tr('缺设备名称'))
        if not d.get('type'):
            add(_tr('设备'), d['_row'], _tr('设备类型'), 'warn', _tr('缺设备类型（套不上设备模板）'))
        for k in d.get('_bad') or []:
            add(_tr('设备'), d['_row'], DEV_LABEL.get(k, k), 'error', _tr('{get} 不是数字', get=DEV_LABEL.get(k, k)))
    known = dev_codes | set(known_devices or ())
    seen_code, seen_addr = {}, {}
    for p in points:
        r = p['_row']
        code = p.get('code')
        if not code:
            add(_tr('点表'), r, _tr('点位编码'), 'error', _tr('点位编码不能为空'))
            continue
        if code in seen_code:
            add(_tr('点表'), r, _tr('点位编码'), 'error', _tr('点位编码重复：{code}（第 {v} 行）', code=code, v=seen_code[code]))
        seen_code.setdefault(code, r)
        proto = p.get('protocol')
        if not proto:
            add(_tr('点表'), r, _tr('协议'), 'error', _tr('缺协议（opcua / mqtt / modbus / sparkplug / http / sql）'))
        elif proto not in PROTO_VALUES:
            add(_tr('点表'), r, _tr('协议'), 'error', _tr('协议 {proto} 不认识（opcua / mqtt / modbus / sparkplug / http / sql）', proto=proto))
        addr = p.get('address')
        if not addr and proto not in ('http',):
            add(_tr('点表'), r, _tr('地址'), 'error', _tr('缺地址'))
        elif addr and proto in ADDR_RX and not ADDR_RX[proto].match(str(addr)):
            add(_tr('点表'), r, _tr('地址'), 'error', _tr('地址格式不对：{addr}。{v}', addr=addr, v=ADDR_HINT[proto]))
        if addr and proto:
            k = (proto, str(addr))
            if k in seen_addr:
                add(_tr('点表'), r, _tr('地址'), 'error', _tr('地址重复：{addr}（第 {v} 行）', addr=addr, v=seen_addr[k]))
            seen_addr.setdefault(k, r)
        dev = p.get('device')
        if dev and dev not in known:
            add(_tr('点表'), r, _tr('设备编码'), 'error', _tr('设备编码 {dev} 在设备表 / 设备台账里没有', dev=dev))
        elif not dev:
            add(_tr('点表'), r, _tr('设备编码'), 'warn' if raw_ok else 'error', _tr('没填设备（导入后进待确认，可用 AI 补全）'))
        dt = p.get('dtype')
        if dt and not norm_dtype(dt):
            add(_tr('点表'), r, _tr('数据类型'), 'error', _tr('数据类型 {dt} 不认识（bool / int / float / string）', dt=dt))
        if p.get('rw') and str(p['rw']).upper() not in ('R', 'RW', 'W'):
            add(_tr('点表'), r, _tr('读写'), 'error', _tr('读写只能是 R / RW：{rw}', rw=p['rw']))
        if p.get('role') and str(p['role']).lower() not in ROLE_VALUES:
            add(_tr('点表'), r, _tr('用途'), 'error', _tr('用途 {role} 不认识（{join}）', role=p['role'], join=' / '.join(ROLE_VALUES)))
        if not p.get('meaning'):
            add(_tr('点表'), r, _tr('含义'), 'warn', _tr('缺含义（导入后进待确认，可用 AI 补全）'))
        if (norm_dtype(dt) in ('float', 'int') or (not dt and proto)) and not p.get('unit') and p.get('role') not in ('state', 'alarm', 'step', 'action') \
                and norm_dtype(dt) != 'bool' and p.get('meaning'):
            add(_tr('点表'), r, _tr('单位'), 'warn', _tr('数值点缺单位'))
        for k in p.get('_bad') or []:
            add(_tr('点表'), r, PT_LABEL.get(k, k), 'error', _tr('{get} 不是数字', get=PT_LABEL.get(k, k)))
        if p.get('normal_range'):
            from .model import parse_range
            lo, hi = parse_range(p['normal_range'])
            if lo is None and hi is None:
                add(_tr('点表'), r, _tr('正常范围'), 'warn', _tr('正常范围写成 下限~上限：{normal_range}', normal_range=p['normal_range']))
        if p.get('alarm_lo') is not None and p.get('alarm_hi') is not None and isinstance(p['alarm_lo'], float) and isinstance(p['alarm_hi'], float) \
                and p['alarm_lo'] > p['alarm_hi']:
            add(_tr('点表'), r, _tr('报警下限'), 'error', _tr('报警下限大于上限'))
    return issues


# ───────────────────────── 模板 ─────────────────────────
def template_xlsx(type_names: list[str], example=True) -> bytes:
    """点表模板：设备 / 点表 / 说明 三张表，表头固定，用途 / 协议 / 数据类型 / 读写 / 设备类型 带下拉。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    head = PatternFill('solid', fgColor='D9D9D9')
    thin = Side(style='thin', color='999999')
    lists = wb.create_sheet(_tr('取值'))
    for i, (title, vals) in enumerate(((_tr('设备类型'), type_names), (_tr('数据类型'), ['bool', 'int', 'float', 'string']), (_tr('读写'), ['R', 'RW']),
                                        (_tr('用途'), list(ROLE_VALUES)), (_tr('协议'), list(PROTO_VALUES)))):
        col = chr(65 + i)
        lists[f'{col}1'] = title
        for j, v in enumerate(vals, start=2):
            lists[f'{col}{j}'] = v
    lists.sheet_state = 'hidden'

    def sheet(ws, cols, widths, rows):
        ws.append(cols)
        for c in ws[1]:
            c.font, c.fill, c.border = Font(bold=True), head, Border(top=thin, bottom=thin, left=thin, right=thin)
            c.alignment = Alignment(horizontal='center')
        for r in rows:
            ws.append(r)
        for i, w in enumerate(widths):
            ws.column_dimensions[chr(65 + i)].width = w
        ws.freeze_panes = 'A2'

    ws = wb.active
    ws.title = _tr('设备')
    sheet(ws, DEV_COLS, (12, 20, 14, 18, 16, 10, 10, 12, 8, 8, 8, 8, 9, 22, 11, 24),
          [['CNC1', _tr('1#加工中心'), _tr('加工中心'), 'VMC-850', _tr('某机床厂'), _tr('机加车间'), _tr('机加一线'), 'WS-CNC1', 90, 46, 4, 3.4, 0, 'models/cnc.glb', 445, '']] if example else [])
    n = 2000

    def dv(ws, col, list_col, count):
        d = DataValidation(type='list', formula1=_tr("='取值'!${list_col}$2:${list_col}${v}", list_col=list_col, v=count + 1), allow_blank=True)
        d.error, d.errorTitle = _tr('请从下拉列表里选'), _tr('取值不对')
        ws.add_data_validation(d)
        d.add(f'{col}2:{col}{n}')
    dv(ws, 'C', 'A', len(type_names))
    ws2 = wb.create_sheet(_tr('点表'))
    sheet(ws2, PT_COLS, (34, 12, 24, 8, 9, 6, 9, 9, 52, 7, 6, 11, 9, 9, 12, 30),
          [['CNC1.Common.State', 'CNC1', _tr('运行状态'), None, 'string', 'R', 'state', 'opcua', 'ns=2;s=Line1/CNC1/Common/State', 1, 0, 500, None, None, None,
            _tr('取值：Idle 待机、Executing 运行、Stopped 停机、Setup 换型')],
           ['CNC1.Monitoring.Spindle.Load', 'CNC1', _tr('主轴负载'), '%', 'float', 'R', 'load', 'opcua', 'ns=2;s=Line1/CNC1/Monitoring/Spindle/Load', 1, 0, 1000, None, 90,
            '0~85', ''],
           ['CNC1.Production.PartsProduced', 'CNC1', _tr('累计加工件数'), _tr('件'), 'int', 'R', 'count', 'mqtt', 'hm/plant1/machining/CNC1/Production/PartsProduced', 1, 0, 0,
            None, None, None, ''],
           ['CNC1.Common.EnergyKWh', 'CNC1', _tr('累计电能'), 'kWh', 'float', 'R', 'energy', 'modbus', '11:hr:1000', 1, 0, 5000, None, None, None, _tr('float32 ABCD，2 个寄存器')]]
          if example else [])
    dv(ws2, 'E', 'B', 4)
    dv(ws2, 'F', 'C', 2)
    dv(ws2, 'G', 'D', len(ROLE_VALUES))
    dv(ws2, 'H', 'E', len(PROTO_VALUES))
    ws3 = wb.create_sheet(_tr('说明'))
    sheet(ws3, [_tr('表'), _tr('列'), _tr('必填'), _tr('说明')], (8, 16, 6, 110), [list(r) for r in EXPLAIN])
    for row in ws3.iter_rows(min_row=2):
        row[3].alignment = Alignment(wrap_text=True, vertical='top')
    wb.move_sheet(_tr('取值'), offset=1)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


EXPLAIN = [
    ('设备', '设备编码', '是', '设备唯一编码。和点表的设备编码、平面图 DXF 块属性 CODE、三维模型节点名一致。只用字母、数字、- 和 _'),
    ('设备', '设备名称', '是', '台账名称，如 1#加工中心'),
    ('设备', '设备类型', '是', '从下拉里选（对应 MES 的设备类型模板，决定标准点位、用途映射和默认三维模型）；下拉里没有的写实际类型，实施时再建模板'),
    ('设备', '型号 / 厂家', '否', '台账信息'),
    ('设备', '车间 / 产线 / 工位', '是', '组织结构：车间 → 产线 → 工位。工位缺省为 WS-设备编码'),
    ('设备', 'X(m) / Y(m)', '是', '设备中心在工厂坐标系的位置：原点在厂房左下角，X 向右，Y 向上，单位米，和平面图 DXF 一致'),
    ('设备', '宽(m) / 深(m) / 朝向(°)', '是', '外形尺寸（宽沿设备自身 X、深沿设备自身 Y）；朝向逆时针为正，和 DXF 块插入角一致'),
    ('设备', '3D模型文件', '否', '交付包里的 glTF 二进制模型相对路径，如 models/cnc.glb；没有时 MES 按设备类型自动生成'),
    ('设备', '理想节拍(s)', '否', '设计节拍，算性能开动率用'),
    ('点表', '点位编码', '是', '点位唯一编码，建议 <设备编码>.<点位路径用 . 连接>，如 CNC1.Monitoring.Spindle.Load'),
    ('点表', '设备编码', '是', '属于哪台设备（设备表里的编码）'),
    ('点表', '含义 / 单位', '是', '中文含义和工程单位（%、°C、kW、kWh、s、件…）；开关量、状态、报警可不填单位'),
    ('点表', '数据类型', '是', 'bool / int / float / string（按工程值）'),
    ('点表', '读写', '是', 'R 只读 / RW 可写（设定值）'),
    ('点表', '用途', '是', 'state 运行状态、alarm 报警、count 计数、power 功率、energy 电能、load 负载、temp 温度、quality 质量、action 动作信号、'
                       'step 程序段号、param 设定参数、other 其它'),
    ('点表', '协议', '是', 'opcua（推荐）/ mqtt / sparkplug / modbus / http / sql。Modbus、S7 等现场协议建议经边缘网关转成 OPC UA 或 MQTT'),
    ('点表', '地址', '是', 'OPC UA：完整 NodeId（ns=2;s=…）；MQTT：完整主题；Sparkplug：组/边缘节点/设备:指标名；Modbus：从站:表:地址（hr / ir / coil / di，地址从 0 起）；'
                       'HTTP 推送：可空（按点位编码推）；中间库：表里的点位名'),
    ('点表', '缩放 / 偏移', '否', '工程值 = 原始值 × 缩放 + 偏移，默认 1 / 0'),
    ('点表', '采集周期(ms)', '否', '建议采集周期；0 = 变化即推送'),
    ('点表', '报警下限 / 报警上限 / 正常范围', '否', '超出报警限即报警；正常范围写成 下限~上限（如 0~85），逐点核对时超出标橙'),
    ('点表', '备注', '否', 'Modbus 多寄存器编码和字序（如 float32 ABCD，2 个寄存器）、状态码对照（如 状态码：0=Idle、1=Executing）等'),
]
