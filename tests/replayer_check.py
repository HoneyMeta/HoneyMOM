"""Replayer check: the MES runs against the open-source plant data replayer (replayer/) instead of the simulator.

Start the replayer and the MES (demo mode) first, then:  python tests/replayer_check.py [seconds to watch, default 90]
Environment:
  MES_URL           MES base URL (default http://127.0.0.1:8900)
  MES_USER / MES_PASSWORD   login (default admin / honey123)
  REPLAYER_MQTT     e.g. mqtt://127.0.0.1:1883              -> datasource VF (mqtt) is re-pointed here
  REPLAYER_OPCUA    e.g. opc.tcp://127.0.0.1:4840/vfactory/ -> datasource VF-OPC (opcua)
  REPLAYER_MODBUS   e.g. 127.0.0.1:5020                     -> datasource VF-MB (modbus), optional
  REPLAYER_PACKAGE  optional path of a delivery package (python -m replayer package --out x.zip): imported through
                    /api/onboard/package first (the onboarding path a new user takes)
Checks only numbers and structure, never dataset texts, so it works for MOM_DEMO_LANG=en and zh.
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
MES = os.environ.get('MES_URL', 'http://127.0.0.1:8900').rstrip('/')
WATCH = int(sys.argv[1]) if len(sys.argv) > 1 else 90
TOKEN = ''
results = []


def req(method, path, body=None, timeout=120):
    r = urllib.request.Request(MES + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                               headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + TOKEN} if TOKEN else {})})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return json.loads(f.read())


def get(p):
    return req('GET', p)


def ok(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name, detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False, default=str)[:300])
    return bool(cond)


def wait(fn, seconds, step=3):
    end = time.time() + seconds
    while True:
        try:
            v = fn()
        except Exception:
            v = None
        if v or time.time() > end:
            return v
        time.sleep(step)


def dt(s):
    return datetime.fromisoformat(str(s).replace('T', ' ')[:19]) if s else None


def connected(status: str) -> bool:
    s = (status or '').lower()
    return ('已连接' in s or 'connected' in s) and 'fail' not in s and '失败' not in s


TOKEN = req('POST', '/api/login', {'username': os.environ.get('MES_USER', 'admin'), 'password': os.environ.get('MES_PASSWORD', 'honey123')})['token']
ok('login', TOKEN)

# ---- optional: onboard the delivery package that points at the replayer ----
pkg = os.environ.get('REPLAYER_PACKAGE')
if pkg:
    data = base64.b64encode(open(pkg, 'rb').read()).decode()
    r = req('POST', '/api/onboard/package', {'name': os.path.basename(pkg), 'data': data, 'use_package_endpoints': True, 'confirm': True,
                                             'note': 'replayer_check'}, timeout=300)
    s = r.get('summary') or {}
    ok('delivery package imported', not s.get('errors') and s.get('points'), {k: s.get(k) for k in ('devices', 'points', 'by_protocol', 'errors')})

# ---- point the VF datasources at the replayer ----
EPS = {'VF': os.environ.get('REPLAYER_MQTT'), 'VF-OPC': os.environ.get('REPLAYER_OPCUA'), 'VF-MB': os.environ.get('REPLAYER_MODBUS')}
dss = {}
for code, ep in EPS.items():
    items = get(f'/api/e/datasource?f_code={code}')['items']
    if not items:
        continue
    ds = items[0]
    dss[code] = ds
    if ep and ds['endpoint'] != ep:
        req('PUT', f"/api/e/datasource/{ds['id']}", {'endpoint': ep, 'enabled': True})
        req('POST', f"/api/e/datasource/{ds['id']}/do/restart", {})
ok('datasources VF / VF-OPC exist', 'VF' in dss and 'VF-OPC' in dss, {k: v['endpoint'] for k, v in dss.items()})


def collector_ok():
    st = get('/api/collector')['status']
    want = [str(dss[c]['id']) for c in ('VF', 'VF-OPC') if c in dss] + ([str(dss['VF-MB']['id'])] if EPS['VF-MB'] and 'VF-MB' in dss else [])
    return st if all(connected(st.get(i)) for i in want) else None


st = wait(collector_ok, 60)
ok('collector connected (MQTT + OPC UA' + (' + Modbus)' if EPS['VF-MB'] else ')'), st, st or get('/api/collector')['status'])

# ---- baseline, then watch ----
c0 = get('/api/collector')
parts0 = get('/api/e/part?size=1')['total']
pev0 = get('/api/e/part_event?size=1')['total']
lat0 = get('/api/ts/latest')['items']
clk0 = dt(get('/api/clock')['now'])
print(f'watching {WATCH} s ...')
time.sleep(WATCH)
c1 = get('/api/collector')
s0, s1 = c0['stats'], c1['stats']
ok('tag values ingested', s1.get('tag_values', 0) > s0.get('tag_values', 0), f"{s0.get('tag_values')} -> {s1.get('tag_values')}")
ok('MES gateway part events arriving', s1.get('part_events', 0) > s0.get('part_events', 0), f"{s0.get('part_events')} -> {s1.get('part_events')}")
parts1 = get('/api/e/part?size=1')['total']
pev1 = get('/api/e/part_event?size=1')['total']
ok('parts (one serial per casting) growing', parts1 > parts0 or pev1 > pev0, f'parts {parts0} -> {parts1}, part events {pev0} -> {pev1}')
lat1 = get('/api/ts/latest')['items']


def _ts(v):
    return dt(v.get('ts') if isinstance(v, dict) else None)


adv = [k for k, v in lat1.items() if _ts(v) and (k not in lat0 or (_ts(lat0[k]) and _ts(v) > _ts(lat0[k])))]
ok('latest point values advancing', len(adv) >= 20, f'{len(adv)} of {len(lat1)} points got newer values')
clk1 = dt(get('/api/clock')['now'])
ok('data clock moving and close to now', clk1 and clk0 and clk1 > clk0 and abs((clk1 - datetime.now()).total_seconds()) < 3 * 3600,
   f'{clk0} -> {clk1} (wall {datetime.now():%Y-%m-%d %H:%M:%S})')
tsst = c1.get('ts') or {}
ok('time series rows written', (tsst.get('rows_new') or 0) > ((c0.get('ts') or {}).get('rows_new') or 0), {'before': (c0.get('ts') or {}).get('rows_new'), 'after': tsst.get('rows_new')})

# ---- pages ----
live = get('/api/home/live')
online = [x for x in live['items'] if x['status'] != 'offline']
ok('map / live: equipment online', len(online) >= 10, f"{len(online)} of {len(live['items'])} online; states {sorted({x['status'] for x in online})}")
lay = get('/api/home/layout')
ok('map layout (from the delivery package)', lay.get('ok') and lay.get('stations'), f"{len(lay.get('stations') or [])} stations")
ck = get('/api/home/cockpit')
ok('cockpit responds with data', isinstance(ck, dict) and len(ck) > 0, sorted(ck)[:12])
mon = get('/api/equipment/monitor')
ok('equipment monitor', sum(1 for e in mon['items'] if e.get('online')) >= 10, f"{sum(1 for e in mon['items'] if e.get('online'))} online")
oee = get('/api/equipment/oee?hours=2')
rows = oee.get('rows') or []
ok('OEE non-empty', rows and any((x.get('OEE') or 0) > 0 for x in rows), ', '.join(f"{x['code']} {x['OEE']:.2f}" for x in rows[:8]))
rep = get('/api/reports/grouped?hours=24')
rep_list = get('/api/reports/list?hours=24&size=5')
n_rep = rep_list.get('total') if isinstance(rep_list, dict) else None
ok('production reports (auto reporting from part events)', (n_rep or 0) > 0 or (rep.get('groups') if isinstance(rep, dict) else rep),
   f'{n_rep} reports in 24 h')

# ---- cycle analysis (action-level signals) ----
lines = get('/api/cycle/lines')['lines']
cands = [e for ln in lines for e in ln['equipment'] if e.get('has_actions')]
ok('cycle analysis: stations with action signals', cands, [e['code'] for e in cands])
best = None
for e in cands[:3]:
    if not e.get('model'):
        try:
            req('POST', f"/api/cycle/models/{e['id']}/propose", {'hours': 1, 'save': True}, timeout=300)
            req('POST', f"/api/cycle/models/{e['id']}/confirm", {})
        except urllib.error.HTTPError as ex:
            print('  propose/confirm', e['code'], ex.code, ex.read()[:200])
    r = get(f"/api/cycle/station/{e['id']}?hours=2&cycles=5")
    if r.get('cycles_n'):
        best = (e['code'], r['cycles_n'], (r.get('cycle') or {}).get('median'))
        break
ok('cycle analysis has samples', best, f'{best[0]}: {best[1]} cycles, median {best[2]} s' if best else 'no cycles found')

bad = [n for n, g in results if not g]
print(f"\n{len(results) - len(bad)}/{len(results)} passed" + (('; failed: ' + ', '.join(bad)) if bad else ''))
sys.exit(1 if bad else 0)
