"""工厂模型与接入（M2）检查：MES 和虚拟工厂都在跑（同 e2e：MES_URL / VF_URL / VF_MQTT / VF_OPCUA / VF_MODBUS）。

A 老库迁移（4.7 线上库副本 → 工厂模型字段、采集点标已确认、模板、漂移检查）   B 设备模板套用          C 点表导入 + 校验错误（客户表头对列）
D OPC UA 浏览（虚拟工厂）                                                   E MQTT 发现（含自己发的主题）   F 逐点核对颜色（HTTP 推送：绿 / 橙 / 黄 / 红）
G Sparkplug B（测试发布器：NBIRTH / DBIRTH / DDATA 别名）                       H 中间库（临时 SQLite 当外部库，水位增量）
I 交付包导入（幂等、版本、比对）                                               J 采集工作线程：锁库期间接口照常响应
K 原始现场：AI 草稿（演示模式）+ 标准答案打分                                   L 读 PLC 程序（软 PLC / PLCopen XML / 博途符号表）
M 联调验收报告（Word / PDF）                                                   N 数据范围：驾驶舱 / 大屏 / 平面图 / OEE / 能耗
用法：python tests/onboard_check.py [--only A,C,…]；A 需要 LIVE_DB（线上库只读副本，缺省 F:\\vfdev\\copies\\m1_live_src\\mes.db），J 需要 MES_DATA（MES 数据目录，SQLite）或 MES_DB（PostgreSQL URL）。
"""
import base64
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MES = os.environ.get('MES_URL', 'http://127.0.0.1:8900').rstrip('/')
VFH = os.environ.get('VF_URL', 'http://127.0.0.1:8800').rstrip('/')
VF = VFH + '/api/p/foundry-housing'
MQTT = os.environ.get('VF_MQTT', 'mqtt://127.0.0.1:1883')
ONLY = set((sys.argv[sys.argv.index('--only') + 1].split(',')) if '--only' in sys.argv else [])
results = []
TOKEN = ''


def ok(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name, str(detail)[:400], flush=True)


def section(k, title):
    if ONLY and k not in ONLY:
        return False
    print(f'\n===== {k} {title}', flush=True)
    return True


def req(method, url, body=None, token=None, timeout=300, raw=False):
    h = {'Content-Type': 'application/json'}
    tk = TOKEN if token is None else token
    if tk:
        h['Authorization'] = 'Bearer ' + tk
    r = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as f:
            data = f.read()
            return data if raw else json.loads(data or b'null')
    except urllib.error.HTTPError as e:
        try:
            return {'_http': e.code, **json.loads(e.read() or b'{}')}
        except ValueError:
            return {'_http': e.code}


def api(m, p, b=None, **kw):
    return req(m, MES + p, b, **kw)


def wait(cond, sec=60, step=1.0):
    t0 = time.time()
    while time.time() - t0 < sec:
        try:
            v = cond()
            if v:
                return v
        except Exception:
            pass
        time.sleep(step)
    return None


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def xlsx(sheets: dict) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def check_rows(ds_id=None):
    return api('GET', '/api/onboard/check' + (f'?datasource={ds_id}' if ds_id else ''))


def mk_ds(code, protocol, endpoint='', prefix='', options=None, enabled=True):
    cur = api('GET', f'/api/e/datasource?f_code={code}')['items']
    body = {'code': code, 'name': code, 'protocol': protocol, 'endpoint': endpoint, 'prefix': prefix, 'enabled': enabled, 'options': options or {}}
    if cur:
        api('PUT', f"/api/e/datasource/{cur[0]['id']}", body)
        did = cur[0]['id']
    else:
        did = api('POST', '/api/e/datasource', body)['id']
    api('POST', f'/api/e/datasource/{did}/do/restart', {})
    return did


def import_points(rows, ds_ids, devices=None):
    """点表导入（预览 + 确认）：rows 是点表行（标准列）。"""
    from openpyxl.utils import get_column_letter  # noqa: F401
    head = ['点位编码', '设备编码', '含义', '单位', '数据类型', '读写', '用途', '协议', '地址', '缩放', '偏移', '采集周期(ms)', '报警下限', '报警上限', '正常范围', '备注']
    sheets = {'点表': [head] + rows}
    if devices:
        sheets = {'设备': [['设备编码', '设备名称', '设备类型', '型号', '厂家', '车间', '产线', '工位', 'X(m)', 'Y(m)', '宽(m)', '深(m)', '朝向(°)', '3D模型文件', '理想节拍(s)', '备注']]
                  + devices, **sheets}
    pv = api('POST', '/api/onboard/points/import', {'name': 't.xlsx', 'data': b64(xlsx(sheets)), 'datasources': ds_ids})
    if pv.get('_http'):
        return pv, None
    res = api('POST', f"/api/onboard/imports/{pv['id']}/confirm", {'datasources': ds_ids})
    return pv, res


def mqtt_pub(topic, payload, retain=False):
    import paho.mqtt.client as mqtt
    host, port = MQTT.replace('mqtt://', '').rstrip('/').split(':')
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'oc-{time.time_ns() % 10**9}')
    c.connect(host, int(port), 10)
    c.loop_start()
    c.publish(topic, payload, qos=0, retain=retain).wait_for_publish(5)
    time.sleep(0.2)
    c.loop_stop()
    c.disconnect()


TOKEN = api('POST', '/api/login', {'username': 'admin', 'password': 'honey123'}, token='')['token']
ok('登录', TOKEN)
SW0 = api('GET', '/api/ai/switches')

# ───────────────────────── A 老库迁移 ─────────────────────────
if section('A', '老库迁移（4.7 线上库副本）'):
    live = os.environ.get('LIVE_DB', '')
    if not os.path.exists(live):
        ok('线上库副本存在（跳过迁移检查）', True, live)
    else:
        d = tempfile.mkdtemp(prefix='oc_mig_')
        dst = os.path.join(d, 'mes.db')
        src = sqlite3.connect(f'file:{live}?mode=ro', uri=True)
        out = sqlite3.connect(dst)
        src.backup(out)
        src.close()
        n_tp = out.execute('select count(*) from tag_point').fetchone()[0]
        out.close()
        code = r'''
import json, sys, os
sys.path.insert(0, os.environ['HM_ROOT'])
from honeymom import db, migrations
import sqlalchemy as sa
from honeymom.migrations import v0100_factory_model as m
t0 = __import__('time').time()
db.init(migrate_db=False)
migrations.upgrade(db.engine, target='0199')          # 只到 M2 的迁移段（别的模块的迁移在它们自己的检查里）
mine = [f'equipment.{x}' for x in m.EQ_COLS] + [f'datasource.{x}' for x in m.DS_COLS] + [f'tag_point.{x}' for x in m.TP_COLS] + ['work_order.line'] +        ['device_template', 'factory_version', 'factory_layout', 'onboard_seen', 'onboard_import', 'onboard_report', 'equip_output']
with db.engine.connect() as c:
    r = {'ms': int((__import__('time').time() - t0) * 1000), 'drift': [p for p in migrations.check(db.engine) if any(p.endswith(' ' + x) or (' ' + x + '(') in p or p.endswith(x) for x in mine)],
         'rev': [x[0] for x in c.execute(sa.text("select rev from schema_migration order by rev"))],
         'tp': c.execute(sa.text("select count(*), sum(case when status='confirmed' then 1 else 0 end), sum(case when code is not null then 1 else 0 end), sum(case when ts_key is not null then 1 else 0 end) from tag_point")).first(),
         'tpl': c.execute(sa.text("select count(*) from device_template")).scalar(), 'eq_tpl': c.execute(sa.text("select count(*) from equipment where template is not null")).scalar(),
         'eq': c.execute(sa.text("select count(*) from equipment")).scalar()}
print(json.dumps({k: (list(v) if isinstance(v, tuple) or hasattr(v, '_fields') else v) for k, v in r.items()}, default=str))
'''
        env = {**os.environ, 'HM_DATA': d, 'HM_ROOT': ROOT, 'MES_DB': 'sqlite:///' + dst.replace('\\', '/'), 'PYTHONIOENCODING': 'utf-8'}
        p = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, encoding='utf-8', timeout=1800)
        try:
            r = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:
            r = {}
            print(p.stdout[-2000:], p.stderr[-3000:])
        ok('迁移到 v0100（工厂模型）', '0100' in (r.get('rev') or []), f"{r.get('rev')} 用时 {r.get('ms')} ms")
        ok('迁移后没有漂移', r.get('drift') == [], r.get('drift'))
        tp = r.get('tp') or [0, 0, 0, 0]
        ok('老库采集点全部保留并标为已确认、补了编码和时序键', tp[0] == n_tp and tp[1] == n_tp and tp[2] == n_tp and tp[3] == n_tp, f'{tp}（原 {n_tp}）')
        ok('内置设备模板 ≥ 11 个', (r.get('tpl') or 0) >= 11, r.get('tpl'))
        ok('设备都有模板', r.get('eq_tpl') == r.get('eq'), f"{r.get('eq_tpl')}/{r.get('eq')}")
        shutil.rmtree(d, ignore_errors=True)

# ───────────────────────── I 交付包导入 ─────────────────────────
if section('I', '交付包导入（虚拟工厂标准交付包）'):
    vt = req('POST', VFH + '/api/login', {'username': 'admin', 'password': 'honey123'}, token='')['token']
    VFTOK = req('POST', VFH + '/api/tokens', {'name': 'onboard_check'}, token=vt)['token']
    EPS = {k: v for k, v in (('mqtt', MQTT), ('opcua', os.environ.get('VF_OPCUA')), ('modbus', os.environ.get('VF_MODBUS'))) if v}
    v0 = (api('GET', '/api/factory/versions')['versions'] or [{'version': 0}])[0]['version']
    r = api('POST', '/api/onboard/package', {'url': VF, 'token': VFTOK, 'use_package_endpoints': True, 'endpoints': EPS})
    s = r.get('summary') or {}
    ok('交付包预览：设备 / 点位 / 协议 / 端点', s.get('devices', 0) >= 25 and s.get('points', 0) >= 500 and len(s.get('datasources') or {}) >= 2,
       f"{s.get('devices')} 台 {s.get('points')} 点 {s.get('by_protocol')} 错误 {s.get('errors')} 提醒 {s.get('warnings')}")
    res = api('POST', f"/api/onboard/imports/{r['id']}/confirm", {})['result']
    ok('确认导入：平面图按块属性落位、三维模型、刀具台账', res['layout'] and res['layout']['bind']['matched'] >= 25 and res['models'] >= 10,
       {k: res.get(k) for k in ('devices_new', 'devices_updated', 'points_new', 'points_updated', 'models', 'tools', 'version')})
    ok('重复导入是幂等的（没有新设备 / 新点位）', res['devices_new'] == 0 and res['points_new'] == 0, f"新设备 {res['devices_new']} 新点位 {res['points_new']}")
    vs = api('GET', '/api/factory/versions')
    ok('导入后自动发布模型版本', vs['versions'][0]['version'] > v0, f"V{v0} → V{vs['versions'][0]['version']}")
    lay = api('GET', '/api/factory/layout')
    ok('孪生布局：工厂坐标、区域、图元', lay.get('ok') and len(lay['devices']) >= 25 and len(lay['areas']) >= 3 and len(lay['prims']) >= 5,
       f"{len(lay.get('devices') or [])} 台，{len(lay.get('areas') or [])} 个区域，{len(lay.get('prims') or [])} 个图元")
    glb = api('GET', '/api/factory/files/models/cnc.glb', raw=True)
    ok('三维模型文件（GLB）', isinstance(glb, bytes) and glb[:4] == b'glTF', len(glb) if isinstance(glb, bytes) else glb)
    home = api('GET', '/api/home/layout')
    ok('首页平面图来自工厂模型', home.get('source') == 'model' and len(home.get('stations') or []) >= 20, f"{home.get('source')} {len(home.get('stations') or [])}")
    st = wait(lambda: (lambda x: x if sum(1 for s in x['sources'] if '已连接' in (s['status'] or '')) >= 3 else None)(api('GET', '/api/onboard/summary')), 60)
    ok('三种协议都连上（OPC UA / MQTT / Modbus）', st, [(s['code'], s['status']) for s in (st or api('GET', '/api/onboard/summary'))['sources']][:6])
    # 版本比对：挪一台设备 → 和上一版比
    d0 = api('GET', '/api/factory/devices?q=SHOT1')['items'][0]
    api('PUT', '/api/factory/layout/positions', {'items': {'SHOT1': {'x': d0['x'] + 3, 'y': d0['y']}}})
    df = api('GET', '/api/factory/versions/diff?b=current')
    ch = [c for c in df['diff']['devices']['changed'] if c['key'] == 'SHOT1']
    ok('版本比对能看到设备位置变化', ch and 'x' in ch[0]['fields'], ch[:1])
    api('PUT', '/api/factory/layout/positions', {'items': {'SHOT1': {'x': d0['x'], 'y': d0['y']}}})
    cat = api('GET', '/api/factory/catalog?device=CNC1')['devices']
    act = [p for p in cat[0]['points'] if p['role'] == 'action'] if cat else []
    ok('AI 数据目录：CNC1 的动作信号带时序键', len(act) >= 8 and all(p['ts_key'] for p in act), f'{len(act)} 个动作点，例 {act[0]["ts_key"] if act else None}')

# ───────────────────────── B 模板套用 ─────────────────────────
if section('B', '设备模板批量套用'):
    tp = api('GET', '/api/factory/templates')['items']
    codes = {t['code'] for t in tp}
    need = {'cnc', 'molding', 'furnace', 'die_casting', 'injection', 'shotblast', 'robot_cell', 'conveyor', 'agv', 'compressor', 'inspection'}
    ok('内置模板覆盖要求的 11 类设备', need <= codes, sorted(need - codes))
    r = api('POST', '/api/factory/templates/cnc/apply', {'model': 'VMC-850'})
    ok('按型号批量套用（同型号 6 台加工中心）', r.get('devices') == 6, r)
    # 新设备 + 模板补标准点位：建一台压铸机，套用 die_casting 并按 MQTT 规范补点位
    pl = api('GET', '/api/e/line?size=1')['items'][0]
    ex = api('GET', '/api/e/equipment?f_code=DC-T1')['items']
    eid = ex[0]['id'] if ex else api('POST', '/api/e/equipment', {'code': 'DC-T1', 'name': '测试压铸机', 'etype': 'other', 'line': pl['id'], 'model': 'DCC-800'})['id']
    vf = api('GET', '/api/e/datasource?f_code=VF')['items'][0]
    r = api('POST', '/api/factory/templates/die_casting/apply', {'devices': ['DC-T1'], 'create_points': True, 'datasource': vf['id']})
    dev = api('GET', '/api/factory/device/DC-T1')
    ok('新设备套模板：类型、尺寸、标准点位（待确认）', dev['device']['template'] == 'die_casting' and dev['device']['w'] and len(dev['points']) >= 8
       and all(p['status'] == 'pending' for p in dev['points']), f"{dev['device']['template']} w={dev['device']['w']} 点位 {len(dev['points'])}")
    ok('模板点位带用途 / 单位 / 语义', any(p['role'] == 'count' and p['sem'] == 'output' for p in dev['points']) and any(p['unit'] == 'MPa' for p in dev['points']))

# ───────────────────────── C 点表导入 + 校验 ─────────────────────────
if section('C', '点表导入 + 校验（客户表头）'):
    hid = mk_ds('T-HTTP', 'http')
    head = ['Tag Name', 'Device', 'Description', 'Unit', 'Data Type', 'Access', '用途', 'Protocol', 'Address', 'Scale', 'Offset', 'Scan Rate', 'Low Limit', 'High Limit', 'Normal Range', 'Notes']
    rows = [['T1.Temp', 'CNC1', '测试温度', '°C', 'float', 'R', 'temp', 'http', 'T1.Temp', 1, 0, 1000, None, 90, '20~80', ''],
            ['T1.Temp', 'CNC1', '重复编码', '°C', 'float', 'R', 'temp', 'http', 'T1.Temp2', 1, 0, 1000, None, None, None, ''],
            ['T1.NoUnit', 'CNC1', '缺单位', None, 'float', 'R', 'other', 'http', 'T1.NoUnit', 1, 0, 1000, None, None, None, ''],
            ['T1.BadDev', 'NOPE9', '设备不存在', '%', 'float', 'R', 'load', 'http', 'T1.BadDev', 1, 0, 1000, None, None, None, ''],
            ['T1.BadOpc', 'CNC1', '地址错', '%', 'float', 'R', 'load', 'opcua', 'Line1/CNC1/x', 1, 0, 1000, None, None, None, ''],
            ['T1.BadMb', 'CNC1', '地址错', '%', 'float', 'R', 'load', 'modbus', '1:xx:5', 1, 0, 1000, None, None, None, ''],
            ['T1.BadType', 'CNC1', '类型错', '%', 'double-ish', 'R', 'load', 'http', 'T1.BadType', 1, 0, 1000, None, None, None, ''],
            ['T1.Count', 'CNC1', '测试计数', '件', 'int', 'R', 'count', 'http', 'T1.Count', 1, 0, 1000, None, None, None, ''],
            ['T1.Never', 'CNC1', '从来不推', '%', 'float', 'R', 'other', 'http', 'T1.Never', 1, 0, 1000, None, None, None, ''],
            ['T1.NoMeaning', 'CNC1', None, '%', 'float', 'R', 'other', 'http', 'T1.NoMeaning', 1, 0, 1000, None, None, None, '']]
    pv = api('POST', '/api/onboard/points/import', {'name': 'customer.xlsx', 'data': b64(xlsx({'Tags': [head] + rows})), 'datasources': {'http': hid}})
    iss = pv.get('issues') or []
    msgs = ' | '.join(i['msg'] for i in iss)
    ok('客户表头按同义词对上标准列', (pv.get('summary') or {}).get('columns', {}).get('point', {}).get('Tag Name') == 'code'
       and pv['summary']['columns']['point'].get('Address') == 'address', pv.get('summary', {}).get('columns'))
    ok('校验：重复编码', '编码重复' in msgs)
    ok('校验：缺单位 / 缺含义', '缺单位' in msgs and '缺含义' in msgs)
    ok('校验：设备不存在', '设备编码 NOPE9' in msgs)
    ok('校验：OPC UA / Modbus 地址格式', 'OPC UA 地址' in msgs and 'Modbus 地址' in msgs)
    ok('校验：数据类型', '数据类型 double-ish' in msgs)
    res = api('POST', f"/api/onboard/imports/{pv['id']}/confirm", {'datasources': {'http': hid}})['result']
    pts = api('GET', f'/api/factory/points?datasource={hid}')['items']
    codes = {p['code'] for p in pts}
    ok('确认导入：错误行跳过、其余进待确认', {'T1.Temp', 'T1.NoUnit', 'T1.Count', 'T1.Never'} <= codes and not {'T1.BadDev', 'T1.BadOpc', 'T1.BadMb', 'T1.BadType'} & codes,
       f"导入 {res['points_new']} 跳过 {res['skipped']}：{sorted(codes)}")
    tpl = api('GET', '/api/onboard/points/template.xlsx', raw=True)
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(tpl))
    ok('点表模板：设备 / 点表 / 说明 + 下拉', {'设备', '点表', '说明'} <= set(wb.sheetnames) and len(wb['点表'].data_validations.dataValidation) >= 4, wb.sheetnames)

# ───────────────────────── F 逐点核对颜色（HTTP 推送） ─────────────────────────
if section('F', '逐点核对颜色（HTTP 推送）'):
    hid = mk_ds('T-HTTP', 'http')
    UNK = f'T1.Unknown{int(time.time()) % 1000000}'
    api('POST', '/api/onboard/push', {'source': 'T-HTTP', 'values': [{'code': 'T1.Temp', 'value': 50}, {'code': 'T1.NoUnit', 'value': 5},
                                                                     {'code': 'T1.Count', 'value': 1}, {'code': 'T1.Count', 'value': 2}]})
    time.sleep(1)
    api('POST', '/api/onboard/push', {'source': 'T-HTTP', 'values': [{'code': 'T1.NoUnit', 'value': 6}, {'address': UNK, 'value': 1.5}]})
    r = wait(lambda: (lambda c: c if any(y['address'] == UNK for y in c['yellow']) else None)(check_rows(hid)), 30)
    by = {x['code']: x for x in (r or check_rows(hid))['rows']}
    ok('推送的点：绿色', by.get('T1.Temp', {}).get('color') == 'green', by.get('T1.Temp'))
    ok('从来没收到的点：红色', by.get('T1.Never', {}).get('color') == 'red')
    ok('点表外的地址：黄色（待确认队列）', r is not None)
    api('POST', '/api/onboard/push', {'source': 'T-HTTP', 'values': [{'code': 'T1.Temp', 'value': 95}]})
    r = wait(lambda: (lambda x: x if x.get('color') == 'orange' else None)(next((y for y in check_rows(hid)['rows'] if y['code'] == 'T1.Temp'), {})), 15)
    ok('超出正常范围：橙色', r, r and r.get('flags'))
    # 确认 → 覆盖率
    c0 = check_rows(hid)
    api('POST', '/api/onboard/confirm', {'all_green': True, 'datasource': hid})
    c1 = check_rows(hid)
    ok('批量确认绿色 → 绿色的点都已确认、覆盖率不降', c1['coverage'] >= c0['coverage'] and not any(x['color'] == 'green' and x['status'] != 'confirmed' and x['equipment'] for x in c1['rows'])
       and any(x['status'] == 'confirmed' for x in c1['rows']), f"{c0['coverage']} → {c1['coverage']}")
    # 待确认队列加进点表
    y = next(y for y in c1['yellow'] if y['address'] == UNK)
    api('POST', f"/api/onboard/seen/{y['id']}/add", {'device': 'CNC1', 'name': 'Test/Unknown', 'meaning': '新发现的点', 'unit': 'bar', 'role': 'other'})
    api('POST', '/api/onboard/push', {'source': 'T-HTTP', 'values': [{'address': UNK, 'value': 1.7}]})
    r = wait(lambda: next((x for x in check_rows(hid)['rows'] if x['address'] == UNK and x['n'] > 0), None), 15)
    ok('待确认地址加进点表后按点位采集', r, r and (r['code'], r['value']))
    ok('HTTP 推送要权限（没令牌 401）', req('POST', MES + '/api/onboard/push', {'source': 'T-HTTP', 'values': [{'code': 'T1.Temp', 'value': 1}]}, token='').get('_http') in (401, 403))

# ───────────────────────── D OPC UA 浏览 ─────────────────────────
if section('D', 'OPC UA 浏览'):
    opc = api('GET', '/api/e/datasource?f_protocol=opcua')['items']
    opc = [d for d in opc if d['enabled']]
    if not opc:
        ok('有 OPC UA 数据源', False)
    else:
        did = opc[0]['id']
        root = api('GET', f'/api/onboard/opcua/browse?datasource={did}')
        ok('浏览根节点（Objects）', root.get('children'), [c.get('display_name') for c in root.get('children') or []][:8])
        node, var = None, None
        q = list(root.get('children') or [])
        for _ in range(6):
            nxt = []
            for c in q[:8]:
                if c.get('node_class') == 'Variable' and c.get('datatype'):
                    var = c
                    break
                if c.get('node_class') == 'Object':
                    nxt += api('GET', f"/api/onboard/opcua/browse?datasource={did}&node={urllib.parse.quote(c['nodeid'])}").get('children') or []
            if var:
                break
            q = [x for x in nxt if x.get('node_class') == 'Variable'][:4] + [x for x in nxt if x.get('node_class') != 'Variable'][:6]
        ok('节点属性：DisplayName / DataType / 值', var and var.get('display_name') and var.get('datatype'), var)
        units = []
        cnc = api('GET', f"/api/onboard/opcua/browse?datasource={did}&node={urllib.parse.quote('ns=2;s=foundry-housing/CNC1/Monitoring/Spindle')}")
        units = [(c.get('display_name'), c.get('unit')) for c in cnc.get('children') or []]
        ok('EngineeringUnits（主轴负载 %）', any(u == '%' for _n, u in units), units)

# ───────────────────────── E MQTT 发现 ─────────────────────────
if section('E', 'MQTT 发现'):
    vf = api('GET', '/api/e/datasource?f_code=VF')['items'][0]
    api('POST', '/api/onboard/discover', {'datasource': vf['id'], 'minutes': 1})
    topic = f"{vf['prefix']}test/OC1/Probe/Value"
    mqtt_pub(topic, json.dumps({'value': 42.5, 'ts': time.strftime('%Y-%m-%dT%H:%M:%S')}))
    r = wait(lambda: next((y for y in check_rows(vf['id'])['yellow'] if y['address'] == topic), None), 30)
    ok('发现：自己发的主题进待确认，带样本值', r and str(r['sample']).startswith('42.5'), r)
    ys = check_rows(vf['id'])['yellow']
    ok('发现：虚拟工厂里点表外的主题（人员定位等）', len(ys) >= 5, f'{len(ys)} 个')
    mqtt_pub(f"{vf['prefix']}plain/OC1/Text", '12.75')
    r = wait(lambda: next((y for y in check_rows(vf['id'])['yellow'] if y['address'].endswith('plain/OC1/Text')), None), 20)
    ok('纯文本载荷（不是 JSON）也能收', r and str(r['sample']).startswith('12.75'), r)
    summ = check_rows(vf['id'])
    ok('MQTT 全量转发的 OPC UA 设备点算"另一地址"、不进待确认', (summ.get('alternate') or {}).get(str(vf['id']), 0) > 50, summ.get('alternate'))

# ───────────────────────── G Sparkplug B ─────────────────────────
if section('G', 'Sparkplug B'):
    sys.path.insert(0, ROOT)
    from honeymom.factory import sparkplug as spb
    did = mk_ds('T-SPB', 'sparkplug', MQTT, 'OCG')
    time.sleep(2)
    wait(lambda: any(s['id'] == did and '已连接' in (s['status'] or '') for s in api('GET', '/api/onboard/summary')['sources']), 30)
    time.sleep(1)
    ts_ms = int(time.time() * 1000)
    mqtt_pub('spBv1.0/OCG/NBIRTH/EDGE1', spb.encode([{'name': 'Node/Uptime', 'alias': 1, 'value': 10, 'datatype': 3}], timestamp=ts_ms, seq=0))
    mqtt_pub('spBv1.0/OCG/DBIRTH/EDGE1/PRESS1', spb.encode([
        {'name': 'Hydraulic/Pressure', 'alias': 11, 'value': 18.5, 'datatype': 10, 'properties': {'engUnit': 'MPa', 'description': '液压压力'}},
        {'name': 'Production/Count', 'alias': 12, 'value': 100, 'datatype': 3},
        {'name': 'Common/State', 'alias': 13, 'value': 'Executing', 'datatype': 12}], timestamp=ts_ms, seq=1))
    addr = 'OCG/EDGE1/PRESS1:Hydraulic/Pressure'
    r = wait(lambda: next((y for y in check_rows(did)['yellow'] if y['address'] == addr), None), 30) or         wait(lambda: next((x for x in check_rows(did)['rows'] if x['address'] == addr), None), 5)        # 上一轮已经加进点表
    ok('出生证书 → 发现指标（含单位、说明）', r and ((r.get('attrs') or {}).get('unit') == 'MPa' or r.get('unit') == 'MPa'), r and (r.get('attrs') or r.get('unit')))
    rt = spb.decode(spb.encode([{'name': 'x', 'alias': 5, 'value': -3, 'datatype': 2}, {'alias': 6, 'value': 1.25, 'datatype': 9}], timestamp=1, seq=2))
    ok('Sparkplug 编解码（负数 Int16、Float、只有别名）', rt['metrics'][0]['value'] == -3 and abs(rt['metrics'][1]['value'] - 1.25) < 1e-6 and rt['metrics'][1].get('alias') == 6)
    if r and r.get('color') == 'yellow':
        api('POST', f"/api/onboard/seen/{r['id']}/add", {'device': 'CNC1', 'name': 'Spb/Pressure', 'meaning': '液压压力', 'unit': 'MPa', 'role': 'other'})
    time.sleep(2)
    mqtt_pub('spBv1.0/OCG/DDATA/EDGE1/PRESS1', spb.encode([{'alias': 11, 'value': 19.25, 'datatype': 10}], timestamp=ts_ms + 1000, seq=2))
    r = wait(lambda: next((x for x in check_rows(did)['rows'] if x['address'] == addr and x['n'] > 0 and abs((x['value'] or 0) - 19.25) < 1e-6), None), 20)
    ok('DDATA 只带别名 → 按出生证书还原指标名、入点位', r, r and r.get('value'))

# ───────────────────────── H 中间库 ─────────────────────────
if section('H', '中间库（外部 SQL 表，按水位增量）'):
    ext = os.path.join(tempfile.gettempdir(), f'oc_ext_{os.getpid()}.db')
    cx = sqlite3.connect(ext)
    cx.execute('create table if not exists mes_points(id integer primary key, tag text, ts text, value real)')
    cx.execute('delete from mes_points')
    cx.commit()
    did = mk_ds('T-SQL', 'sql', 'sqlite:///' + ext.replace('\\', '/'), '', {'table': 'mes_points', 'watermark': 'id', 'interval_s': 1, 'start_wm': 0})
    import_points([['SQL.A', 'CNC1', '外部库点 A', 'bar', 'float', 'R', 'other', 'sql', 'PT_A', 1, 0, 1000, None, None, None, ''],
                   ['SQL.B', 'CNC1', '外部库点 B', '℃', 'float', 'R', 'temp', 'sql', 'PT_B', 1, 0, 1000, None, None, None, '']], {'sql': did})
    api('POST', f'/api/e/datasource/{did}/do/restart', {})
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    cx.executemany('insert into mes_points(tag, ts, value) values (?, ?, ?)', [('PT_A', now, 1.0), ('PT_B', now, 20.0), ('PT_A', now, 1.5), ('PT_X', now, 9.0)])
    cx.commit()
    r = wait(lambda: (lambda rows: rows if rows.get('PT_A', {}).get('n', 0) >= 2 and rows.get('PT_B', {}).get('n', 0) >= 1 else None)(
        {x['address']: x for x in check_rows(did)['rows']}), 30)
    ok('外部表按点位名取值', r, r and {k: (v['n'], v['value']) for k, v in r.items()})
    ok('外部表里点表外的点名 → 待确认', wait(lambda: any(y['address'] == 'PT_X' for y in check_rows(did)['yellow']), 20))
    ds = wait(lambda: (lambda d: d if (d.get('options') or {}).get('_wm') == 4 else None)(api('GET', f'/api/e/datasource/{did}')['item']), 20)
    ok('水位列记下来（增量取，不重复）', ds, ds and ds.get('options'))
    cx.execute('insert into mes_points(tag, ts, value) values (?, ?, ?)', ('PT_A', now, 2.5))
    cx.commit()
    r = wait(lambda: next((x for x in check_rows(did)['rows'] if x['address'] == 'PT_A' and x['n'] >= 3 and x['value'] == 2.5), None), 20)
    ok('新行增量进来', r, r and (r['n'], r['value']))
    api('PUT', f'/api/e/datasource/{did}', {'enabled': False})
    api('POST', f'/api/e/datasource/{did}/do/restart', {})
    cx.close()

# ───────────────────────── J 采集工作线程：锁库时接口照常 ─────────────────────────
if section('J', '采集工作线程：数据库被锁时接口照常响应'):
    lat, stop = [], threading.Event()

    def probe():
        while not stop.is_set():
            t0 = time.time()
            try:
                req('GET', MES + '/api/clock', timeout=30)
                lat.append(time.time() - t0)
            except Exception:
                lat.append(30)
            time.sleep(0.25)
    data_dir, mes_db = os.environ.get('MES_DATA'), os.environ.get('MES_DB')
    if not data_dir and not mes_db:
        ok('MES_DATA / MES_DB 没设（跳过锁库检查）', True)
    else:
        th = threading.Thread(target=probe, daemon=True)
        if mes_db and mes_db.startswith('postgresql'):
            import psycopg
            conn = psycopg.connect(mes_db.replace('postgresql+psycopg://', 'postgresql://'), autocommit=False)
            conn.execute('LOCK TABLE tag_latest, equip_state, ts_point IN ACCESS EXCLUSIVE MODE')
        else:
            conn = sqlite3.connect(os.path.join(data_dir, 'mes.db'), timeout=30)
            conn.isolation_level = None
            conn.execute('BEGIN EXCLUSIVE')
        q0 = api('GET', '/api/onboard/collector')['worker']
        th.start()
        time.sleep(15)
        q1 = api('GET', '/api/onboard/collector')['worker']
        conn.rollback() if hasattr(conn, 'rollback') and not isinstance(conn, sqlite3.Connection) else conn.execute('ROLLBACK')
        conn.close()
        stop.set()
        th.join(5)
        ok('锁库 15 秒期间接口最长响应 < 1.5 秒', lat and max(lat) < 1.5, f'{len(lat)} 次，最长 {max(lat or [0]):.2f}s，平均 {sum(lat) / max(1, len(lat)):.3f}s')
        ok('锁库期间采集消息在工作线程排队（不丢、不卡事件循环）', q1['queue'] > q0['queue'] or q1['current_s'] > 3 or q1['done'] > q0['done'], f'{q0} → {q1}')
        r = wait(lambda: (lambda w: w if w['queue'] < 500 else None)(api('GET', '/api/onboard/collector')['worker']), 90)
        ok('解锁后工作线程追上来', r, r)

# ───────────────────────── K 原始现场：AI 草稿 + 打分 ─────────────────────────
if section('K', '原始现场：AI 补全（演示模式）+ 标准答案打分'):
    vt = req('POST', VFH + '/api/login', {'username': 'admin', 'password': 'honey123'}, token='')['token']
    VFTOK = req('POST', VFH + '/api/tokens', {'name': 'onboard_check'}, token=vt)['token']
    req('PUT', VF + '/delivery/raw_site', {'on': True}, token=vt)
    time.sleep(20)
    EPS = {k: v for k, v in (('mqtt', MQTT), ('opcua', os.environ.get('VF_OPCUA')), ('modbus', os.environ.get('VF_MODBUS'))) if v}
    r = api('POST', '/api/onboard/package', {'url': VF, 'token': VFTOK, 'ds_prefix': 'RAW', 'use_package_endpoints': True, 'endpoints': EPS, 'publish': False})
    s = r.get('summary') or {}
    ok('原始现场交付包：识别为原始现场', s.get('raw_site'), f"原始点 {s.get('raw_points')} / {s.get('points')}")
    res = api('POST', f"/api/onboard/imports/{r['id']}/confirm", {'publish': False})['result']
    ok('原始现场点表导入到 RAW 数据源（不影响标准点表）', res['points_new'] + res['points_updated'] + len(api('GET', '/api/factory/points?datasource=' + str(api('GET', '/api/e/datasource?f_code=RAW-OPC')['items'][0]['id']))['items']) >= 300 and set(res['datasources'].values()) <= {'RAW', 'RAW-OPC', 'RAW-MB'}, res['datasources'])
    print('  等原始地址的值进来（看值特征）…', flush=True)
    time.sleep(int(os.environ.get('RAW_WAIT', '60')))
    key_path = os.environ.get('VF_ANSWER_KEY') or os.path.join(os.environ.get('VF_HOME', os.path.join(ROOT, '..', 'vf')), 'data', 'delivery', 'foundry-housing', 'answer_key.json')
    if os.path.exists(key_path):
        key = json.load(open(key_path, encoding='utf-8'))
        sc = api('POST', '/api/onboard/ai/score', {'key': key, 'datasources': ['RAW', 'RAW-OPC', 'RAW-MB']})
        print('  规则 + 值特征的补全正确率：', {k: sc.get(k) for k in ('points', 'device_acc', 'meaning_acc', 'role_acc', 'unit_acc')}, flush=True)
        for w in (sc.get('samples_wrong') or [])[:8]:
            print('   ', w, flush=True)
        ok('原始现场打分（答案对上点位）', 500 <= sc.get('points', 0) <= len(key['points']), f"设备 {sc.get('device_acc')} 含义 {sc.get('meaning_acc')} 用途 {sc.get('role_acc')} 单位 {sc.get('unit_acc')}")
        ok('补全正确率：用途 ≥ 50%、设备 ≥ 50%', sc.get('role_acc', 0) >= 0.5 and sc.get('device_acc', 0) >= 0.5)
    else:
        ok('标准答案文件（跳过打分）', True, key_path)
    # 草稿流程：AI 关闭 → 403；演示模式打开 → 起草 → 审批通过
    off = api('POST', '/api/onboard/ai/draft', {})
    if not (SW0['global'] and SW0['features'].get('onboard')):
        ok('AI 关闭时起草被拒（只能人工）', off.get('_http') == 403, off)
    api('PUT', '/api/ai/switches', {'global': True, 'features': {**SW0['features'], 'onboard': True}, 'demo': True, 'record': False})
    raw_ds = api('GET', '/api/e/datasource?f_code=RAW-OPC')['items'][0]
    dr = api('POST', '/api/onboard/ai/draft', {'datasource': raw_ds['id'], 'limit': 200})
    ok('演示模式下起草（每台设备一张草稿）', dr.get('drafts'), {k: dr.get(k) for k in ('points', 'by')} | {'drafts': len(dr.get('drafts') or [])})
    lst = api('GET', '/api/onboard/ai/drafts')['items']
    d = next((x for x in lst if x['payload'].get('device')), None)
    if d:
        pts = d['payload']['points']
        pts[0]['meaning'] = (pts[0].get('meaning') or '') + '（人工改）'
        r = api('POST', f"/api/onboard/ai/drafts/{d['id']}/decide", {'decision': 'approve', 'payload': d['payload']})
        p0 = next(p for p in api('GET', f"/api/factory/points?datasource={raw_ds['id']}&q={urllib.parse.quote(pts[0]['code'])}")['items'] if p['id'] == pts[0]['id'])
        ok('修改后通过：点位写进模型并确认（设备、时序键跟着改）', r.get('status') == 'approved' and p0['status'] == 'confirmed' and p0['desc'].endswith('（人工改）')
           and p0['ts_key'].split(':', 1)[1].startswith(d['payload']['device'] + '/'), (r.get('status'), p0['status'], p0['ts_key'], p0['origin']))
    sus = api('GET', '/api/onboard/ai/suspicious')['items']
    ok('可疑点检查（不需要 AI）', isinstance(sus, list), f'{len(sus)} 个')
    api('PUT', '/api/ai/switches', {k: SW0[k] for k in ('global', 'features', 'demo', 'record')})
    req('PUT', VF + '/delivery/raw_site', {'on': False}, token=vt)
    for code in ('RAW', 'RAW-OPC', 'RAW-MB'):
        x = api('GET', f'/api/e/datasource?f_code={code}')['items']
        if x:
            api('PUT', f"/api/e/datasource/{x[0]['id']}", {'enabled': False})
            api('POST', f"/api/e/datasource/{x[0]['id']}/do/restart", {})
    time.sleep(20)

# ───────────────────────── L 读 PLC 程序 ─────────────────────────
if section('L', '读 PLC 程序'):
    import yaml
    api('PUT', '/api/ai/switches', {'global': True, 'features': {**SW0['features'], 'onboard': True}, 'demo': True, 'record': False})
    cfg = None
    for cand in (os.environ.get('VF_PLANT_YAML') or '', os.path.join(os.environ.get('VF_HOME') or os.path.join(ROOT, '..', 'vf'), 'plants', 'foundry-housing.yaml'), os.path.join(ROOT, '..', 'vf', 'plants', 'foundry-housing.yaml')):
        if cand and os.path.exists(cand):
            cfg = yaml.safe_load(open(cand, encoding='utf-8'))
            break
    blocks = (cfg or {}).get('stations') or (cfg or {}).get('blocks') or []
    plc = next((b for b in blocks if isinstance(b, dict) and b.get('id') == 'PLC1'), None)
    cell = next((b for b in blocks if isinstance(b, dict) and b.get('id') == 'CELL1'), None)
    if plc:
        cp = (cell or {}).get('params') or {}
        prog = {'program': plc['params']['program'], 'io': (cp.get('cell') or {}).get('io') or cp.get('io') or (cell or {}).get('io') or []}
        r = api('POST', '/api/onboard/ai/plc', {'name': 'PLC1.json', 'data': b64(json.dumps(prog, ensure_ascii=False).encode()), 'device': 'CELL1'})
        kinds = {p['symbol']: p['kind'] for p in r.get('proposals') or []}
        ok('软 PLC（LD）：动作开始 / 结束、故障、计数', kinds.get('P1_EXTEND') == 'action_start' and kinds.get('P1_FRONT') == 'action_end'
           and kinds.get('ESTOP') == 'fault' and kinds.get('C_OK.CV') == 'count', kinds)
        ok('PLC 建议变成待审批草稿', r.get('draft'))
    else:
        ok('虚拟工厂工厂配置（跳过软 PLC）', True)
    xml = '''<?xml version="1.0" encoding="utf-8"?>
<project xmlns="http://www.plcopen.org/xml/tc6_0200"><types><pous><pou name="Main" pouType="program"><interface>
<localVars><variable name="Step"><type><INT/></type><documentation><xhtml xmlns="http://www.w3.org/1999/xhtml">工步号</xhtml></documentation></variable>
<variable name="CycleStart"><type><BOOL/></type><documentation><xhtml xmlns="http://www.w3.org/1999/xhtml">循环启动</xhtml></documentation></variable>
<variable name="ClampValve" address="%Q0.1"><type><BOOL/></type><documentation><xhtml xmlns="http://www.w3.org/1999/xhtml">夹紧电磁阀</xhtml></documentation></variable>
<variable name="ClampOk" address="%I0.4"><type><BOOL/></type><documentation><xhtml xmlns="http://www.w3.org/1999/xhtml">夹紧到位</xhtml></documentation></variable>
<variable name="FaultAir"><type><BOOL/></type><documentation><xhtml xmlns="http://www.w3.org/1999/xhtml">气压低故障</xhtml></documentation></variable>
</localVars></interface><body><ST><xhtml xmlns="http://www.w3.org/1999/xhtml">IF CycleStart THEN Step := 10; ClampValve := TRUE; END_IF;</xhtml></ST></body></pou></pous></types></project>'''
    r = api('POST', '/api/onboard/ai/plc', {'name': 'main.xml', 'data': b64(xml.encode()), 'device': 'CNC1'})
    kinds = {p['symbol']: p['kind'] for p in r.get('proposals') or []}
    ok('PLCopen XML：步号 / 循环启动 / 阀 / 到位 / 故障', kinds.get('Step') == 'step' and kinds.get('CycleStart') == 'cycle_start' and kinds.get('ClampValve') == 'action_start'
       and kinds.get('ClampOk') == 'action_end' and kinds.get('FaultAir') == 'fault', kinds)
    csv_ = ('Name;Path;Data Type;Logical Address;Comment\n"Cyl_Push_Valve";"Default tag table";Bool;%Q0.3;推料气缸电磁阀\n"Cyl_Push_Home";"Default tag table";Bool;%I1.2;推料气缸缩回到位\n'
            '"Seq_Step";"Default tag table";Int;%MW10;顺控步号\n"E_Stop";"Default tag table";Bool;%I0.0;急停\n"Part_Count";"Default tag table";DInt;%MD20;产量计数\n')
    r = api('POST', '/api/onboard/ai/plc', {'name': 'tags.csv', 'data': b64(csv_.encode('utf-8-sig')), 'device': 'CNC2'})
    kinds = {p['symbol']: p['kind'] for p in r.get('proposals') or []}
    ok('博途符号表 CSV', kinds.get('Cyl_Push_Valve') == 'action_start' and kinds.get('Cyl_Push_Home') == 'action_end' and kinds.get('Seq_Step') == 'step'
       and kinds.get('E_Stop') == 'fault' and kinds.get('Part_Count') == 'count', kinds)
    lst = [d for d in api('GET', '/api/onboard/ai/drafts')['items'] if d['op'] == 'onboard_plc']
    if lst:
        rr = api('POST', f"/api/onboard/ai/drafts/{lst[0]['id']}/decide", {'decision': 'approve'})
        codes = {p['code'] for p in api('GET', '/api/factory/points?device=CNC2')['items']}
        ok('PLC 草稿通过 → 采集点（待确认）建好', rr.get('status') == 'approved' and {'CNC2.PLC.Cyl_Push_Valve', 'CNC2.PLC.Seq_Step'} <= codes, rr.get('receipt'))
    api('PUT', '/api/ai/switches', {k: SW0[k] for k in ('global', 'features', 'demo', 'record')})

# ───────────────────────── M 联调验收报告 ─────────────────────────
if section('M', '联调验收报告'):
    d = api('GET', '/api/onboard/report.docx', raw=True)
    ok('Word 报告', isinstance(d, bytes) and d[:2] == b'PK', len(d) if isinstance(d, bytes) else d)
    if isinstance(d, bytes):
        from docx import Document
        doc = Document(io.BytesIO(d))
        text = '\n'.join(p.text for p in doc.paragraphs)
        ok('Word 报告内容：结论、覆盖率、签字栏', '联调验收报告' in text and '覆盖率' in text and len(doc.tables) >= 3, f'{len(doc.tables)} 张表')
    p = api('GET', '/api/onboard/report.pdf', raw=True)
    ok('PDF 报告', isinstance(p, bytes) and p[:4] == b'%PDF', len(p) if isinstance(p, bytes) else p)
    j = api('GET', '/api/onboard/report.json')
    ok('报告数据：每台设备覆盖率', len(j.get('devices') or []) >= 20, f"覆盖率 {j.get('coverage')}")

# ───────────────────────── N 数据范围 ─────────────────────────
if section('N', '数据范围：驾驶舱 / 大屏 / 平面图 / OEE / 能耗'):
    lines = api('GET', '/api/e/line?size=100')['items']
    mach = next((l for l in lines if '机加' in l['name']), None)
    u = api('GET', '/api/e/sys_user?f_username=oc_scope')['items']
    if not u:
        api('POST', '/api/e/sys_user', {'username': 'oc_scope', 'name': '机加线班长', 'roles': 'leader', 'active': True})
        u = api('GET', '/api/e/sys_user?f_username=oc_scope')['items']
    api('POST', f"/api/e/sys_user/{u[0]['id']}/do/reset_pw", {'password': 'honey123'})
    sc = api('PUT', f"/api/admin/users/{u[0]['id']}", {'scope': {'lines': [mach['id']]}})
    ok('给用户设数据范围（机加线）', not sc.get('_http'), sc)
    ut = api('POST', '/api/login', {'username': 'oc_scope', 'password': 'honey123'}, token='')['token']
    devs_in = {e['code'] for e in api('GET', '/api/e/equipment?size=200', token=ut)['items']}
    ok('设备台账按范围', devs_in and 'FUR1' not in devs_in and 'CNC1' in devs_in, sorted(devs_in)[:10])
    ck_all, ck = api('GET', '/api/home/cockpit'), api('GET', '/api/home/cockpit', token=ut)
    ok('驾驶舱：设备状态只算范围内的', sum(ck['status'].values()) < sum(ck_all['status'].values()) and sum(ck['status'].values()) > 0,
       f"{sum(ck['status'].values())} / {sum(ck_all['status'].values())}")
    ok('驾驶舱：OEE 只列范围内设备', all(x['code'] in devs_in for x in ck['oee']), [x['code'] for x in ck['oee']])
    ok('驾驶舱：产量按范围重算（机加 ≤ 全厂）', ck['kpi']['castings']['value'] <= ck_all['kpi']['castings']['value'] and ck.get('scoped'))
    lay = api('GET', '/api/home/layout', token=ut)
    ok('首页平面图只画范围内设备', lay.get('stations') and all(s['code'] in devs_in for s in lay['stations']), len(lay.get('stations') or []))
    scr = api('GET', '/api/home/screen', token=ut)
    ok('车间大屏设备表按范围', scr['equipment'] and all(x['code'] in devs_in for x in scr['equipment']), len(scr['equipment']))
    oee = api('GET', '/api/equipment/oee?hours=8', token=ut)
    ok('OEE 分析按范围', all(x['code'] in devs_in for x in oee['rows']), [x['code'] for x in oee['rows']])
    en = api('GET', '/api/energy/summary?days=1', token=ut)
    en_all = api('GET', '/api/energy/summary?days=1')
    ok('能耗按范围', all(x['code'] in devs_in for x in en['by_equipment']) and en['total_kwh'] <= en_all['total_kwh'], f"{en['total_kwh']} / {en_all['total_kwh']}")
    wo = api('GET', '/api/e/work_order?size=50')['items']
    ok('工单有产线（由工艺路线推出）', wo and all(w.get('line') for w in wo if w.get('route')), [w.get('line__label') for w in wo][:4])

bad = [n for n, g in results if not g]
print(f"\n{len(results) - len(bad)}/{len(results)} 通过", ('；失败：' + '、'.join(bad)) if bad else '')
sys.exit(1 if bad else 0)
