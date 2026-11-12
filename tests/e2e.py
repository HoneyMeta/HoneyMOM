"""端到端联调：虚拟工厂（:8800 + MQTT :1883）→ 蜂元智能 MOM（:8900）。

先启动两边服务，再运行：python tests/e2e.py [等待秒数]
另一套端口（测试副本）：环境变量 MES_URL（默认 http://127.0.0.1:8900）、VF_URL（默认 http://127.0.0.1:8800）、VF_MQTT（如 mqtt://127.0.0.1:1964，改数据源地址）。
步骤：登录 → 导入虚拟工厂的标准交付包（设备、点表、平面图、三维）→ 下达工单 → 加速仿真 → 检查各模块数据是否齐全、合理。

AI 功能默认全部关闭。E2E_AI 决定联调时怎么处理（结束后恢复原来的开关）：
  auto（默认）：服务端配置了大模型就用真的（live），否则用演示模式（demo，回放录制/规则版，不花 token）
  live / demo：强制；off：不打开，AI 检查改为核对"已关闭"的回应
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')
VFH = os.environ.get('VF_URL', 'http://127.0.0.1:8800').rstrip('/')
MES, VF = os.environ.get('MES_URL', 'http://127.0.0.1:8900').rstrip('/'), VFH + '/api/p/foundry-housing'
WAIT = int(sys.argv[1]) if len(sys.argv) > 1 else 120
TOKEN = ''
results = []


LANG = os.environ.get('E2E_LANG', 'zh-CN')      # UI language the checks run in (X-Lang); the text checks accept zh-CN and en


def req(method, url, body=None, timeout=120):
    r = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                               headers={'Content-Type': 'application/json', 'X-Lang': LANG, **({'Authorization': 'Bearer ' + TOKEN} if TOKEN else {})})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return json.loads(f.read())


def get(p): return req('GET', MES + p)
def post(p, b=None): return req('POST', MES + p, b or {})


def ok(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name, detail)


def ai_call(method, p, b=None):
    """AI 接口：功能关闭时服务端返回 403 {code: ai_disabled}，这里转成 {'_off': 提示}。"""
    try:
        return req(method, MES + p, b if method != 'GET' else None, timeout=360)     # 真实大模型多轮查询可能要 1~3 分钟
    except urllib.error.HTTPError as e:
        j = json.loads(e.read() or b'{}')
        if e.code == 403 and j.get('code') == 'ai_disabled':
            return {'_off': j.get('detail')}
        raise


TOKEN = req('POST', MES + '/api/login', {'username': 'admin', 'password': 'honey123'})['token']
ok('登录', TOKEN)
SW0 = get('/api/ai/switches')
AI_MODE = os.environ.get('E2E_AI', 'auto')
if AI_MODE == 'auto':
    AI_MODE = 'live' if SW0['llm_configured'] else 'demo'
req('PUT', MES + '/api/ai/switches', {'global': AI_MODE != 'off', 'features': {k: AI_MODE != 'off' for k in SW0['features']}, 'demo': AI_MODE == 'demo', 'record': False})
sw = get('/api/ai/switches')
ok('AI 功能开关', sw['mode'] == AI_MODE, f"模式 {sw['mode']}，大模型{'已' if sw['llm_configured'] else '未'}配置")
AI_ON = AI_MODE != 'off'
ds = get('/api/e/datasource?f_code=VF')['items'][0]
if os.environ.get('VF_MQTT') and ds['endpoint'] != os.environ['VF_MQTT']:
    req('PUT', f"{MES}/api/e/datasource/{ds['id']}", {'endpoint': os.environ['VF_MQTT']})
vt = req('POST', VFH + '/api/login', {'username': 'admin', 'password': 'honey123'})['token']
_t, TOKEN = TOKEN, vt
VFTOK = req('POST', VFH + '/api/tokens', {'name': 'MES 联调'})['token']
TOKEN = _t
# 5.0（M2）：按标准接入路径——取虚拟工厂的交付包（点表 + 平面图 + 三维），导入 → 校验 → 确认；端点用环境变量（测试副本）或交付包里写的
EPS = {k: v for k, v in (('mqtt', os.environ.get('VF_MQTT')), ('opcua', os.environ.get('VF_OPCUA')), ('modbus', os.environ.get('VF_MODBUS'))) if v}
r = req('POST', MES + '/api/onboard/package', {'url': VF, 'token': VFTOK, 'use_package_endpoints': True, 'endpoints': EPS, 'confirm': True,
                                               'note': '联调导入交付包'}, timeout=300)
res = r.get('result') or {}
ok('导入交付包（设备、点表、平面图、三维）', res and not r['summary']['errors'],
   f"设备 {r['summary']['devices']} 台、点位 {r['summary']['points']} 个（{r['summary']['by_protocol']}），新增设备 {res.get('devices_new')}、"
   f"新增点位 {res.get('points_new')}、平面图 {(res.get('layout') or {}).get('blocks')} 个设备块、三维模型 {res.get('models')} 个")
eqs = get('/api/e/equipment?size=100')
ok('设备台账', eqs['total'] >= 15, f"{eqs['total']} 台")
wos = get('/api/e/work_order?f_status=draft')['items']
for w in wos:
    post(f"/api/e/work_order/{w['id']}/do/release")
ok('工单下达 + 排产', len(get('/api/e/work_order?f_status=released')['items']) + len(get('/api/e/work_order?f_status=in_progress')['items']) >= len(wos), f'{len(wos)} 个')
TOKEN, _t = VFTOK, TOKEN
req('POST', VF + '/sim', {'speed': 60, 'paused': False})
TOKEN = _t
print(f'仿真 60× 运行 {WAIT} 秒（约 {WAIT * 60 / 3600:.1f} 小时生产）…')
time.sleep(WAIT)
TOKEN, _t = VFTOK, TOKEN
req('POST', VF + '/sim', {'speed': 10})
TOKEN = _t
st = get('/api/collector')
ok('采集在线', any(('已连接' in v or v.startswith('Connected')) for v in st['status'].values()), json.dumps(st, ensure_ascii=False))
parts = get('/api/e/part?size=1')
ok('一件一码', parts['total'] > 50, f"{parts['total']} 件")
ev = get('/api/e/part_event?size=1')
ok('零件事件', ev['total'] > 200, f"{ev['total']} 条")
mon = get('/api/equipment/monitor')
online = sum(1 for e in mon['items'] if e['online'])
ok('设备实时监控', online >= 15, f'{online} 台在线，数据时钟 {mon["now"]}')
oee = get('/api/equipment/oee?hours=2')
ok('OEE', len(oee['rows']) >= 8 and all(0 <= x['OEE'] <= 1 for x in oee['rows']), ', '.join(f"{x['code']} {x['OEE']:.2f}" for x in oee['rows'][:8]))
heats = get('/api/foundry/heats')
ok('炉次记录（成分、电耗）', any(h['C'] for h in heats['rows']), f"{len(heats['rows'])} 炉，平均吨耗 {heats['avg']['kwh_per_t']}")
rep = get('/api/e/report?size=5')
ok('自动报工', rep['total'] > 5, f"{rep['total']} 条")
board = get('/api/production/board')
ok('工单进度', any(o['ops'] and any(p['qty_good'] for p in o['ops']) for o in board), ' / '.join(f"{o['code']}:{o['status']}" for o in board))
spc = get('/api/quality/spc?char=BORE_DIA')
ok('SPC 缸孔', spc.get('enough'), f"n={spc.get('count')} Cpk={spc.get('Cpk')}")
ncr = get('/api/e/ncr?size=1')
ok('不合格品自动登记', ncr['total'] >= 1, f"{ncr['total']} 条")
energy = get('/api/energy/summary?days=1')
ok('能耗', energy['total_kwh'] > 0, f"{energy['total_kwh']} kWh，吨耗 {energy['kwh_per_t']}")
sn = [x for x in get('/api/e/part?f_status=fg&size=50')['items'] if '~' not in x['sn']][:1] or get('/api/e/part?size=1&sort=id')['items']   # ~ 是重复编号归档的旧件
if sn:
    t = get('/api/trace/part/' + sn[0]['sn'])
    ok('单件追溯', t['events'], f"{sn[0]['sn']}：{len(t['events'])} 个事件，炉次 {t['part']['heat']}")
bt = get('/api/cross/backtrace?days=7&group=all')
ok('缺陷回溯', bt['n'] > 0, bt['text'].split('\n')[0])
cp = get('/api/cross/coplan')
ok('协同排产', cp.get('ok'), (cp.get('tips') or [''])[0])
op = ai_call('POST', '/api/foundry/optimize', {'furnace_kg': 3000, 'target': {'C_lo': 3.6, 'C_hi': 3.8, 'Si_lo': 1.4, 'Si_hi': 1.7, 'Mn_hi': 0.4, 'S_hi': 0.02, 'P_hi': 0.05}})
ok('配料优化', op.get('ok') if AI_ON else op.get('_off'), op.get('_off') or f"成本 {op.get('cost')} 元/t，比经验配比省 {op.get('saving_per_t')}")
tools = get('/api/machining/tools')
est = [t for t in tools['tools'] if t.get('est')]
ok('刀具监测', tools['tools'] and (AI_ON or not est), f"{len(tools['tools'])} 把，有 AI 估计 {len(est)} 把" + (f"，例 {est[0]['equipment__label']} {est[0]['code']} 估 {est[0]['est']['vb_est']} / 真 {est[0]['vb_truth']}" if est else ''))
a = ai_call('POST', '/api/ai/ask', {'q': '今天产量和废品率怎么样'})
ok('问数助手', a.get('answer') if AI_ON else a.get('_off'), a.get('_off') or (a['answer'][:80].replace('\n', ' ') + (' [演示模式]' if a.get('demo') else '')))
d = ai_call('POST', '/api/ai/daily', {})
ok('日报', any(w in d.get('content', '') for w in ('生产日报', 'daily production report', 'Daily Production Report', 'Production Daily Report', 'Production daily report')) if AI_ON else d.get('_off'), d.get('_off') or d['by'])   # zh or en dataset
h = get('/api/dash/home')
ok('首页看板', h['kpi'], json.dumps(h['todo'], ensure_ascii=False))
req('PUT', MES + '/api/ai/switches', {k: SW0[k] for k in ('global', 'features', 'demo', 'record')})     # 恢复原来的 AI 开关
bad = [n for n, g in results if not g]
print(f"\n{len(results) - len(bad)}/{len(results)} 通过", ('；失败：' + '、'.join(bad)) if bad else '')
