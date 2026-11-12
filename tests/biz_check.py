"""业务闸门与业务深化接口检查（API）：python tests/biz_check.py

需要 MES 已启动、有演示数据（先跑过 tests/e2e.py：已从虚拟工厂导入设备、下达工单、跑过一段仿真）。环境变量 MES_URL（默认 http://127.0.0.1:8900）。
检查：
  1. 不合格品"待处理"计数口径一致（首页 / 提醒 / 列表视图 / 页签计数），返修中的单子也在列表里（回归：首页 359 条、列表为空）
  2. 报工幂等（同一键重复提交、并发提交只计一次）
  3. 超报、跳序被拦下（409 + 中文说明 + X-Gate）
  4. 首检未放行不能量产报工；首检合格放行后可以；首检不合格不放行
  5. 冻结批次不能出库、不能报工；解冻后恢复
  6. 返修复检：没复检不能关 NCR；返修完成 → 复检合格 → NCR 关闭，有复检单
  7. 排产约束：缺料、设备停机、无合格操作工、模具保养、换型都会出现在冲突/影响里；调整建议采纳后写入停机计划
  8. 追溯对账：每一步数量平衡；造一个入库单差异能被标出来
  9. 知识库图纸：DXF → SVG、PDF、图片；上传新 DXF
 10. 报工记录分组/详情、工位终端、上下工、工人日报（提交 → 班组长审核）、工时统计、手机端查询
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

sys.stdout.reconfigure(encoding='utf-8')
MES = os.environ.get('MES_URL', 'http://127.0.0.1:8900').rstrip('/')
results = []


def ok(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name, detail)


def req(method, path, body=None, token='', raw=False):
    r = urllib.request.Request(MES + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                               headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + token} if token else {})})
    try:
        with urllib.request.urlopen(r, timeout=120) as f:
            data = f.read()
            return f.status, (data if raw else json.loads(data or b'{}')), dict(f.headers)
    except urllib.error.HTTPError as e:
        data = e.read() or b'{}'
        try:
            j = json.loads(data)
        except ValueError:
            j = {'detail': data.decode('utf-8', 'replace')}
        return e.code, j, dict(e.headers)


def login(u):
    return req('POST', '/api/login', {'username': u, 'password': 'honey123'})[1]['token']


T = login('admin')
OP, LEAD, QC = login('operator'), login('leader'), login('qc')


def gate(h):
    return {k.lower(): v for k, v in (h or {}).items()}.get('x-gate')


def get(p, t=None): return req('GET', p, token=t or T)[1]
def post(p, b=None, t=None): return req('POST', p, b or {}, token=t or T)
def put(p, b, t=None): return req('PUT', p, b, token=t or T)


# ---------------- 1. 不合格品口径 ----------------
for _ in range(5):
    home = get('/api/dash/home')
    cnt = get('/api/quality/ncr/counts')
    lst = get('/api/e/ncr?view=pending&size=1')
    if home['todo']['ncr'] == lst['total'] == cnt['pending']:
        break
allc = get('/api/e/ncr?size=1')['total']
closed = get('/api/e/ncr?view=closed&size=1')['total']
ok('NCR 首页计数 = 待处理列表条数', home['todo']['ncr'] == lst['total'] == cnt['pending'], f"首页 {home['todo']['ncr']} / 列表 {lst['total']} / 页签 {cnt['pending']}")
ok('NCR 待处理 + 已关闭 = 全部', cnt['pending'] + closed == allc, f"{cnt['pending']} + {closed} = {allc}")
s, fd, _ = req('GET', '/api/home/feed?limit=60', token=T)
card = next((x for x in (fd.get('items') or fd.get('cards') or []) if isinstance(x, dict) and x.get('id') == 'ncr:open'), None) if s == 200 and isinstance(fd, dict) else None
ok('NCR 提醒卡片计数和链接同口径', card is None and cnt['pending'] == 0 or (card and str(cnt['pending']) in card['title'] and card['link'] == '/e/ncr?view=pending'),
   card['title'] if card else '（无待处理，没有卡片）')
# 回归：返修中（reviewing）的单子必须出现在待处理列表里
s, r, _ = post('/api/e/ncr', {'qty': 1, 'station': 'TEST', 'found_by': 'biz_check', 'source': 'manual', 'disposition': 'pending', 'status': 'open'})
nid = r['id']
post(f'/api/e/ncr/{nid}/do/rework', {'remark': 'biz_check 返修'})
pend_ids = {x['id'] for x in get('/api/e/ncr?view=pending&size=2000')['items']}
ok('返修中（评审中）的 NCR 出现在待处理列表', nid in pend_ids)


def ncr_triplet():
    # 仿真在跑时随时会新增不合格品：三个口径连着取，对不上就重取（最多 5 次），排除取数间隙造成的假差异
    for _ in range(5):
        t3 = (get('/api/dash/home')['todo']['ncr'], get('/api/e/ncr?view=pending&size=1')['total'], get('/api/quality/ncr/counts')['pending'])
        if len(set(t3)) == 1:
            break
    return t3


t3 = ncr_triplet()
ok('首页计数随之变化且仍一致', len(set(t3)) == 1, str(t3))

# ---------------- 6. 返修复检 ----------------
s, r, _ = post(f'/api/e/ncr/{nid}/do/close')
ok('返修未复检不能关闭 NCR', s == 409 and '复检' in r['detail'], r.get('detail'))
rw = get(f'/api/e/ncr/{nid}')['children']['rework'][0]
s, r, _ = post(f"/api/e/rework/{rw['id']}/do/recheck", {'result': 'OK'}, QC)
ok('返修没完成不能复检', s == 409, r.get('detail'))
s, r, _ = post(f"/api/e/rework/{rw['id']}/do/finish", {'method': '去毛刺单元复刷交叉孔'}, QC)
ok('返修完成', s == 200, r.get('msg'))
s, r, _ = post(f"/api/e/rework/{rw['id']}/do/recheck", {'result': 'OK'}, QC)
rw2 = get(f"/api/e/rework/{rw['id']}")['item']
n2 = get(f'/api/e/ncr/{nid}')['item']
ok('复检合格 → NCR 关闭，复检记录完整', s == 200 and n2['status'] == 'closed' and rw2['recheck'] and rw2['recheck_result'] == 'OK' and rw2['method'] and rw2['finished_at'],
   f"复检单 #{rw2['recheck']} 复检人 {rw2['recheck_by']}")
ok('复检生成了检验单', get(f"/api/e/insp_record/{rw2['recheck']}")['item']['result'] == 'OK')

# ---------------- 2~4. 报工闸门（用一个新工单，数字可控） ----------------
for w in get('/api/e/work_order?f_customer=biz_check&size=50')['items']:      # 上一轮中途失败留下的测试工单先关掉
    if w['status'] in ('released', 'in_progress'):
        post(f"/api/e/work_order/{w['id']}/do/close")
m2 = get('/api/e/material?f_code=SGH-520')['items'][0]
s, r, _ = post('/api/e/work_order', {'product': m2['id'], 'qty': 10, 'priority': 9, 'customer': 'biz_check'})
wid = r['id']
s, r, _ = post(f'/api/e/work_order/{wid}/do/release')
ok('新工单下达（SGH-520，10 件）', s == 200, r.get('msg'))
ops = sorted(get(f'/api/e/work_order/{wid}')['children']['wo_op'], key=lambda o: o['seq'])
o1, o2, o3 = ops[0], ops[1], ops[2]
k = 'test-' + uuid.uuid4().hex
s1, r1, _ = post('/api/production/report', {'wo_op': o1['id'], 'qty_good': 2, 'idem_key': k}, OP)
s2, r2, _ = post('/api/production/report', {'wo_op': o1['id'], 'qty_good': 2, 'idem_key': k}, OP)
g1 = get(f"/api/e/wo_op/{o1['id']}")['item']['qty_good']
ok('同一幂等键重复提交只计一次', s1 == 200 and s2 == 200 and r2.get('duplicate') and r1['id'] == r2['id'] and g1 == 2, f"第二次：{r2.get('msg')}；工序合格 {g1}")
k2 = 'test-' + uuid.uuid4().hex
outs = []
ths = [threading.Thread(target=lambda: outs.append(post('/api/production/report', {'wo_op': o1['id'], 'qty_good': 1, 'idem_key': k2}, OP))) for _ in range(6)]
[t.start() for t in ths]
[t.join() for t in ths]
g2 = get(f"/api/e/wo_op/{o1['id']}")['item']['qty_good']
n_rep = len([x for x in get(f"/api/reports/list?hours=100000&wo_op={o1['id']}")['items'] if x.get('idem_key') == k2])
ok('并发 6 次同一键（双击 / 网络重试）只计一次', g2 == 3 and all(o[0] == 200 for o in outs) and n_rep == 1, f"工序合格 {g2}，报工记录 {n_rep} 条，重复 {sum(1 for o in outs if o[1].get('duplicate'))} 次")
s, r, h = post('/api/production/report', {'wo_op': o1['id'], 'qty_good': 8, 'idem_key': 'test-' + uuid.uuid4().hex}, OP)
ok('超计划报工被拦下', s == 409 and gate(h) == 'over' and '最多还能报 7' in r['detail'], r['detail'])
s, r, h = post('/api/production/report', {'wo_op': o3['id'], 'qty_good': 1, 'idem_key': 'test-' + uuid.uuid4().hex}, OP)
ok('跳序报工被拦下（上道没有合格品）', s == 409 and gate(h) == 'sequence', r['detail'])
post('/api/production/report', {'wo_op': o2['id'], 'qty_good': 2, 'idem_key': 'test-' + uuid.uuid4().hex}, OP)
s, r, h = post('/api/production/report', {'wo_op': o3['id'], 'qty_good': 3, 'idem_key': 'test-' + uuid.uuid4().hex}, OP)
ok('超过上道转来的数量被拦下', s == 409 and gate(h) == 'sequence' and '最多还能报 2' in r['detail'], r['detail'])
s, r, _ = post('/api/production/report', {'wo_op': o3['id'], 'qty_good': 2, 'idem_key': 'test-' + uuid.uuid4().hex}, OP)
ok('按顺序、数量内报工通过', s == 200, r.get('msg'))
chk = post('/api/production/report/check', {'wo_op': o3['id']}, OP)[1]
ok('报工预检给出最多可报数', chk['max'] == 0 and chk['max_plan'] == 8, json.dumps(chk, ensure_ascii=False)[:160])
# 演示数据：seed 的演示工单（以及升级时已经在跑的工单）CNC 首检已放行，演示不会一上来就被拦；新工单照常要首检（下面这张）
cands = get('/api/quality/fai/candidates')
demo = [x for x in cands if ((x.get('fai') or {}).get('decided_by') == '演示数据')]
ok('演示工单的 CNC 首检已放行（演示数据）', demo and all(x['fai_ok'] for x in demo), ', '.join(f"{x['work_order']} {x['process']}" for x in demo))
ok('新下达的工单不带演示放行', not any(x['work_order_id'] == wid and x['fai_ok'] for x in cands))
# 首检：一路报到 CNC（P60，工艺步骤勾了首检）
fai_op = next(o for o in ops if o['station_group'] == 'CNC')
for o in ops:
    if o['seq'] >= fai_op['seq']:
        break
    if o['seq'] > o3['seq']:
        post('/api/production/report', {'wo_op': o['id'], 'qty_good': 2, 'idem_key': 'test-' + uuid.uuid4().hex}, OP)
s, r, h = post('/api/production/report', {'wo_op': fai_op['id'], 'qty_good': 1, 'idem_key': 'test-' + uuid.uuid4().hex}, OP)
ok('首检未放行不能量产报工', s == 409 and gate(h) == 'fai' and '首检' in r['detail'], r['detail'])
s, r, _ = post('/api/quality/fai', {'wo_op': fai_op['id'], 'trigger': 'start'}, OP)
fid = r['id']
s, r, h = post('/api/production/report', {'wo_op': fai_op['id'], 'qty_good': 1, 'idem_key': 'test-' + uuid.uuid4().hex}, OP)
ok('首检待判定时仍不能报工', s == 409 and '待检' in r['detail'], r['detail'])
fd = get(f'/api/quality/fai/{fid}')
bad = [{'item': i['id'], 'value': (i['hi'] + 1) if i['hi'] is not None else None, 'ok': i['hi'] is not None} for i in fd['items']]
s, r, _ = post(f'/api/quality/fai/{fid}/inspect', {'values': bad}, QC)
ok('首检不合格 → 不放行', s == 200 and r['status'] == 'rejected', r.get('msg'))
s, r, h = post('/api/production/report', {'wo_op': fai_op['id'], 'qty_good': 1, 'idem_key': 'test-' + uuid.uuid4().hex}, OP)
ok('首检不放行仍不能报工', s == 409 and '不放行' in r['detail'], r['detail'])
fid = post('/api/quality/fai', {'wo_op': fai_op['id'], 'trigger': 'start'}, OP)[1]['id']
fd = get(f'/api/quality/fai/{fid}')
good = [{'item': i['id'], 'value': ((i['lo'] or 0) + i['hi']) / 2 if i['hi'] is not None else (i['lo'] if i['lo'] is not None else None), 'ok': True} for i in fd['items']]
s, r, h = post(f'/api/quality/fai/{fid}/inspect', {'values': good}, QC)
ok('首检合格放行要电子签名（不签 → 428，检验值不落库）', s == 428 and {k.lower(): v for k, v in h.items()}.get('x-esign') == 'required', r.get('detail'))
s, r, _ = post(f'/api/quality/fai/{fid}/inspect', {'values': good, 'esign': {'password': 'honey123', 'meaning': '放行'}}, QC)
ok('首检合格 → 电子签名放行', s == 200 and r['status'] == 'approved', r.get('msg'))
s, r, _ = post('/api/production/report', {'wo_op': fai_op['id'], 'qty_good': 1, 'idem_key': 'test-' + uuid.uuid4().hex}, OP)
ok('首检放行后可以量产报工', s == 200, r.get('msg'))

# ---------------- 5. 批次冻结 ----------------
lot = 'TEST-LOT-' + uuid.uuid4().hex[:6]
s, r, _ = post('/api/e/lot_hold', {'lot': lot, 'reason': 'biz_check：疑似球化不良'}, QC)
hid = r['id']
fg = get('/api/e/warehouse?f_wtype=fg')['items'][0]
mat = get('/api/e/material?f_code=SGH-450')['items'][0]
s, r, h = post('/api/e/stock_txn', {'ttype': 'out', 'material': mat['id'], 'qty': 1, 'warehouse': fg['id'], 'lot': lot})
ok('冻结批次不能出库（出入库单被拦下）', s == 409 and gate(h) == 'hold', r.get('detail', f'{s} {r}'))
s, r, h = post('/api/e/stock_txn', {'ttype': 'transfer', 'material': mat['id'], 'qty': 1, 'warehouse': fg['id'], 'to_warehouse': fg['id'], 'lot': lot})
ok('冻结批次不能移库', s == 409, r.get('detail', f'{s} {r}'))
nt = get(f'/api/e/stock_txn?f_lot={lot}')['total']
ok('被拦下的出库单没有落库', nt == 0)
heats = get('/api/trace/recon/heats')
real = next((x for x in heats if x['is_heat'] and x['done'] > 0), None) or next((x for x in heats if x['is_heat']), None)
if real:
    s, r, _ = post('/api/e/lot_hold', {'lot': real['heat'], 'reason': 'biz_check：炉次冻结'}, QC)
    h2 = r['id']
    held = get(f"/api/e/part?f_heat={real['heat']}&f_status=hold&size=1")['total']
    ok('冻结炉次后零件状态变为冻结', held > 0, f"{real['heat']} 冻结 {held} 件")
    hp = get(f"/api/e/part?f_heat={real['heat']}&f_status=hold&size=100")['items']
    sn = (next((p for p in hp if p.get('work_order')), None) or hp[0])['sn']      # 优先挑有工单的冻结件（老库里有的零件没关联工单）
    s, r, h = post('/api/warehouse/outbound', {'sns': [sn]})
    ok('冻结零件不能按零件码出库', s == 409 and gate(h) == 'hold', r.get('detail', f'{s} {r}'))
    s, r, h = post('/api/production/report', {'sn': sn, 'qty_good': 1, 'idem_key': 'test-' + uuid.uuid4().hex}, OP)
    ok('冻结零件不能扫码报工', s == 409 and gate(h) == 'hold', r.get('detail', f'{s} {r}'))
    s, r, _ = post(f'/api/e/lot_hold/{h2}/do/release', {'note': 'biz_check 解冻', 'esign': {'password': 'honey123', 'meaning': '放行'}}, QC)
    ok('解冻后零件恢复原状态', s == 200 and get(f"/api/e/part?f_heat={real['heat']}&f_status=hold&size=1")['total'] == 0, r.get('msg'))
s, r, _ = post(f'/api/e/lot_hold/{hid}/do/release', {'note': 'ok', 'esign': {'password': 'honey123', 'meaning': '放行'}}, QC)
ok('批次解冻', s == 200)

# ---------------- 7. 排产约束与影响 ----------------
s, r, _ = post('/api/production/schedule', {})
ok('重新排产（带约束）', s == 200 and 'impact' in r, f"受影响 {r.get('affected')}，逾期 {r.get('late')}")
g = get('/api/production/gantt2')
my_ops = [o for o in g['ops'] if o['work_order'] == wid]
conf_types = {c['type'] for o in g['ops'] for c in (o['conflicts'] or [])}
ok('换型时间生效（SGH-520 在 CNC 上换型）', any(c['type'] == 'changeover' for o in my_ops for c in (o['conflicts'] or [])) and any((o['setup_min'] or 0) > 0 for o in my_ops),
   ', '.join(sorted(conf_types)))
ok('主数据齐全：班次 / 停机 / 齐套 / 技能 / 模具 / 换型', all(g['master'][k] > 0 for k in ('shift', 'skill', 'tooling', 'changeover')) and 'wo_kitting' in g['master'],
   json.dumps(g['master']))
cnc = [e['code'] for e in get('/api/e/equipment?f_etype=cnc&size=20')['items']]
now = get('/api/clock')['now']
t0 = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.mktime(time.strptime(now, '%Y-%m-%d %H:%M:%S')) + 1800))
t1 = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.mktime(time.strptime(now, '%Y-%m-%d %H:%M:%S')) + 1800 + 10 * 3600))
s, pv, _ = post('/api/production/schedule/preview', {'downtime': [{'equipment': c, 'start': t0, 'end': t1, 'reason': '假设全线停电'} for c in cnc]})
aff = [x for x in pv['impact'] if x['affected']]
ok('假设 CNC 全部停机 10 小时 → 列出受影响工单和"设备停机"原因', aff and any(r_['type'] == 'machine_down' for x in aff for r_ in x['reasons']) and any((x['delta_min'] or 0) > 0 for x in aff),
   '；'.join(f"{x['code']} +{x['delta_min']}min" for x in aff[:4]))
wcode = get(f'/api/e/work_order/{wid}')['item']['code']
eta = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.mktime(time.strptime(now, '%Y-%m-%d %H:%M:%S')) + 3 * 86400))
s, pv, _ = post('/api/production/schedule/preview', {'kitting': [{'work_order': wcode, 'material': 'CM-PIG', 'eta': eta, 'short': 2}]})
x = next(x for x in pv['impact'] if x['code'] == wcode)
first = min((v for k, v in pv['ops'].items() if int(k) in {o['id'] for o in my_ops} and v['start'] and v['conflicts']), key=lambda v: v['start'], default=None)
ok('假设缺料（3 天后到料）→ 工单推迟并标"缺料"', any(r_['type'] == 'shortage' for r_ in x['reasons']) and (x['delta_min'] or 0) > 0, f"{wcode} +{x['delta_min']} min")
names = [e['name'] for e in get('/api/station/options')['employees']]
s, pv, _ = post('/api/production/schedule/preview', {'absent': names})
ok('假设全员缺勤 → "无合格操作工"', any(r_['type'] == 'no_skill' for x in pv['impact'] for r_ in x['reasons']))
s, pv, _ = post('/api/production/schedule/preview', {'tooling': [{'tooling': 'MB-520-01', 'until': eta}]})
x = next(x for x in pv['impact'] if x['code'] == wcode)
ok('假设模具保养 → "模具保养中"并推迟', any(r_['type'] == 'mould_maint' for r_ in x['reasons']) and (x['delta_min'] or 0) > 0, f"+{x['delta_min']} min")
sh = get('/api/e/shift?f_code=C')['items'][0]
put(f"/api/e/shift/{sh['id']}", {'active': False})
s, pv, _ = post('/api/production/schedule/preview', {})
put(f"/api/e/shift/{sh['id']}", {'active': True})
ok('停用夜班 → 工单完工推后（只在启用班次干活）', any((x['delta_min'] or 0) > 60 for x in pv['impact']), '；'.join(f"{x['code']} +{x['delta_min']}min" for x in pv['impact'][:3]))
s, pp, _ = post('/api/production/schedule/propose', {'downtime': [{'equipment': cnc[0], 'start': t0, 'end': t1, 'reason': 'biz_check 主轴检修'}], 'title': 'biz_check：CNC 停机'})
ok('存为排程调整建议（带影响评估）', s == 200 and get(f"/api/e/schedule_proposal/{pp['id']}")['item']['impact'], f"受影响 {pp['affected']}")
n0 = get('/api/e/downtime_plan?size=1')['total']
s, r, _ = post(f"/api/e/schedule_proposal/{pp['id']}/do/accept")
ok('采纳建议 → 写入设备停机计划并重排', s == 200 and get('/api/e/downtime_plan?size=1')['total'] == n0 + 1, r.get('msg'))
s, r, _ = post('/api/production/kitting/refresh', {})
ok('按 BOM 刷新物料齐套', s == 200 and r['lines'] > 0, f"{r.get('lines')} 行")

# ---------------- 8. 追溯对账 ----------------
rc = get('/api/trace/recon')
ok('追溯对账 6 步', [s_['key'] for s_ in rc['steps']] == ['heat', 'ladle', 'part', 'machining', 'inspection', 'outbound'], rc['heat'])
ok('每一步数量平衡（进入 = 转出 + 报废 + 在制 + 冻结）', all(s_['in'] == s_['out'] + s_['scrap'] + s_['wip'] + s_['hold'] + s_['diff'] for s_ in rc['steps']) and all(s_['diff'] == 0 for s_ in rc['steps'][2:]),
   ' → '.join(f"{s_['label']} {s_['in']}/{s_['out']}" for s_ in rc['steps']))
ok('链条衔接：上一步转出 = 下一步进入', all(rc['steps'][i]['out'] == rc['steps'][i + 1]['in'] for i in range(2, 5)))
stored = [p for p in get(f"/api/e/part?f_heat={rc['heat']}&size=500")['items'] if p['status'] in ('fg', 'shipped')]
if stored:
    post('/api/e/stock_txn', {'ttype': 'in', 'material': mat['id'], 'qty': 1, 'warehouse': fg['id'], 'ref_doc': stored[0]['sn'], 'reason': 'biz_check 重复入库'})
    rc2 = get('/api/trace/recon?heat=' + rc['heat'])
    ob = rc2['steps'][-1]
    ok('重复入库单被对账标为差异', not ob['ok'] and not rc2['ok'] and any(not c['ok'] for c in ob['checks']), next((f"{c['name']}：{c['a']} vs {c['b']}" for c in ob['checks'] if not c['ok']), ''))
else:
    ok('重复入库单被对账标为差异（跳过：该炉次还没有入库件）', True)

# ---------------- 9. 图纸 ----------------
kb = get('/api/kb/list?kind=drawing')
seeded = [d for d in kb['items'] if d['title'].startswith('SGH-450')]
types = {d['file_type']: d for d in seeded}
ok('知识库有 DXF / PDF / 图片三类图纸，关联产品和工序', {'dxf', 'pdf', 'image'} <= set(types) and all(d['product'] and d['process'] for d in seeded), f"{len(seeded)} 张")
s, svg, _ = req('GET', f"/api/kb/{types['dxf']['id']}/svg", token=T, raw=True)
ok('DXF 转 SVG', s == 200 and svg.startswith(b'<svg') and b'<polygon' in svg and b'<circle' in svg and b'<text' in svg, f"{len(svg)} 字节")
s, pdf, hd = req('GET', f"/api/kb/{types['pdf']['id']}/file?token={T}", raw=True)
ok('PDF 原文件预览', s == 200 and pdf.startswith(b'%PDF') and 'pdf' in hd.get('content-type', hd.get('Content-Type', '')))
s, png, _ = req('GET', f"/api/kb/{types['image']['id']}/file?token={T}", raw=True)
ok('图片原文件', s == 200 and png[:4] == b'\x89PNG')
dxf = '0\nSECTION\n2\nENTITIES\n0\nLINE\n8\n0\n10\n0\n20\n0\n11\n100\n21\n50\n0\nCIRCLE\n8\n0\n10\n50\n20\n25\n40\n10\n0\nARC\n8\n0\n10\n20\n20\n20\n40\n8\n50\n0\n51\n180\n0\nENDSEC\n0\nEOF\n'
s, r, _ = post('/api/e/kb_doc', {'title': 'biz_check 上传图纸', 'kind': 'drawing', 'product': mat['id']})
did = r['id']
import base64  # noqa: E402
s, r, _ = post(f'/api/kb/{did}/file', {'name': 'test.dxf', 'data': base64.b64encode(dxf.encode()).decode()})
s2, svg2, _ = req('GET', f'/api/kb/{did}/svg', token=T, raw=True)
ok('上传 DXF 并转 SVG', s == 200 and s2 == 200 and b'data-prims="3"' in svg2, r.get('file_type'))
s, r, _ = post(f'/api/kb/{did}/file', {'name': 'x.exe', 'data': 'AAAA'})
ok('不支持的附件类型被拒绝', s == 400)
req('DELETE', f'/api/e/kb_doc/{did}', token=T)
ins = get(f"/api/kb/for?product={mat['id']}&process={types['dxf']['process']}")
ok('按产品 + 工序取作业指示和图纸', ins['drawings'] and ins['instruction'] and ins['params'], f"{ins['process']}：{len(ins['drawings'])} 张图纸，{len(ins['sops'])} 份指导书")

# ---------------- 10. 报工记录 / 工位终端 / 日报 / 手机 ----------------
gr = get('/api/reports/grouped?hours=100000')
ok('报工记录按 工单 → 工序 → 设备 分组合计', gr['groups'] and all('ops' in w for w in gr['groups']) and gr['total']['records'] > 0, f"{len(gr['groups'])} 个工单，{gr['total']['records']} 条")
auto = next((x for x in get('/api/reports/list?hours=100000&source=auto&size=200')['items'] if x['equipment']), None)
if auto:
    dt = get(f"/api/reports/{auto['id']}/detail")
    ok('自动报工详情：来源说明、设备编号和名称、录入时间、作业指示', '分钟汇总' in dt['item']['source_text'] and dt['equipment']['code'] and dt['equipment']['name'] and dt['entered_at']
       and dt['instruction']['process'], f"{dt['item']['source_text']} · {dt['equipment']['code']} {dt['equipment']['name']} · 操作人 {dt['operator']['text']}")
cast = next((x for x in get('/api/reports/list?hours=100000&source=auto&size=500')['items'] if (x.get('process__label') or '').startswith('P30')), None)
if cast:
    dt = get(f"/api/reports/{cast['id']}/detail")
    ok('造型浇注报工详情带铸件信息（炉次 / 浇包 / 浇注温度）', dt['casting'] and dt['parts'] and dt['parts'][0]['heat'], json.dumps(dt['casting'], ensure_ascii=False)[:160])
ws = ','.join(w['code'] for w in get('/api/station/options')['workstations'] if w['group'] == 'CNC')
emp = get('/api/me', OP)['employee']
s, r, _ = post('/api/station/clock', {'employee': emp, 'action': 'on', 'workstations': ws}, OP)
ok('工位终端上工（一人多机）', s == 200, r.get('msg'))
sv = get(f'/api/station/view?ws={ws}&employee={emp}', OP)
m0 = sv['machines'][0]
ok('工位终端：设备状态 / 节拍 / 不良、模具、作业指示、今日任务、在岗', len(sv['machines']) >= 2 and 'rate_h' in m0 and m0['tooling'] and sv['instruction'] and sv['attendance'],
   f"{len(sv['machines'])} 台，任务 {len(sv['tasks'])}，模具 {m0['tooling'][0]['code']} 模次 {m0['tooling'][0]['shots']}")
s, r, _ = post('/api/production/report', {'wo_op': fai_op['id'], 'qty_good': 1, 'equipment': sv['machines'][0]['equipment']['id'], 'source': 'terminal',
                                           'idem_key': 'test-' + uuid.uuid4().hex}, OP)
ok('工位终端报工', s == 200, r.get('msg'))
pf = get('/api/labor/daily/prefill', OP)
ok('工人日报从报工和上工记录预填', pf['qty_good'] > 0 and pf['lines'], f"合格 {pf['qty_good']}，{len(pf['lines'])} 项，工时 {pf['hours']} h")
s, r, _ = post('/api/station/clock', {'employee': emp, 'action': 'off'}, OP)
ok('下工记工时', s == 200, r.get('msg'))
pf = get('/api/labor/daily/prefill', OP)
# 同一人同一天同一班次只有一份：上一轮已经审核过就换一个过去的日期提交（只测提交 → 审核流程）
import datetime as _dt  # noqa: E402
dday = pf['day'] if not (pf.get('existing') or {}).get('status') == 'approved' else (_dt.date.fromisoformat(pf['day']) - _dt.timedelta(days=1 + int(uuid.uuid4().int % 300))).isoformat()
s, r, _ = post('/api/labor/daily', {'day': dday, 'shift': pf['shift'], 'qty_good': pf['qty_good'], 'qty_ng': pf['qty_ng'], 'hours': max(pf['hours'], 7.5),
                                     'downtime_min': pf['downtime_min'], 'remark': 'biz_check', 'lines': pf['lines'], 'submit': True}, OP)
did = r['id']
ok('工人提交日报', s == 200 and r['status'] == 'submitted', r.get('code'))
s, r, _ = post(f'/api/e/worker_daily/{did}/do/approve', {}, OP)
ok('操作工不能审核日报', s == 403)
s, r, _ = post(f'/api/e/worker_daily/{did}/do/reject', {}, LEAD)
ok('退回必须写原因', s == 400)
s, r, _ = post(f'/api/e/worker_daily/{did}/do/approve', {'note': '数据核对无误'}, LEAD)
ok('班组长审核通过', s == 200 and get(f'/api/e/worker_daily/{did}')['item']['status'] == 'approved')
s, r, _ = post('/api/labor/daily', {'day': dday, 'shift': pf['shift'], 'hours': 3, 'submit': True}, OP)
ok('已审核的日报不能再改', s == 409)
hw = get('/api/labor/hours?by=worker')
ht = get('/api/labor/hours?by=team')
hd_ = get('/api/labor/hours?by=day')
ok('工时统计：按人 / 班组 / 天', hw['rows'] and ht['rows'] and hd_['rows'] and hw['total']['approved_h'] >= 7.5, f"已审核 {hw['total']['approved_h']} h，上下工 {hw['total']['clock_h']} h")
q = get(f"/api/m/search?q={get(f'/api/e/work_order/{wid}')['item']['code']}", OP)
ok('手机端查询（工单 / 零件 / 设备）', q['items'] and q['items'][0]['kind'] == 'work_order' and get('/api/m/search?q=CNC1', OP)['items'], f"{len(q['items'])} 条")
ok('手机端角标', 'unread' in get('/api/m/home', OP))
s, mf, hd = req('GET', '/manifest.webmanifest', raw=True)
s2, sw, hd2 = req('GET', '/sw.js', raw=True)
ok('PWA：manifest + service worker', s == 200 and json.loads(mf)['start_url'].startswith('/m') and s2 == 200 and 'javascript' in (hd2.get('content-type') or hd2.get('Content-Type') or ''),
   (hd2.get('content-type') or hd2.get('Content-Type') or ''))

post(f'/api/e/work_order/{wid}/do/close')
bad = [n for n, g in results if not g]
print(f"\n{len(results) - len(bad)}/{len(results)} 通过", ('；失败：' + '、'.join(bad)) if bad else '')
sys.exit(1 if bad else 0)
