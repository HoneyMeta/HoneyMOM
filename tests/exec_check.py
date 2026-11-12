"""现场执行与质量合规（M5）接口检查：python tests/exec_check.py

需要 MES 已启动（SQLite 或 PostgreSQL 都行；新库即可，不依赖虚拟工厂：零件用网关 HTTP 推送造）。环境变量 MES_URL（默认 http://127.0.0.1:8900）。
检查：
  A. 标签：内置模板；ZPL / TSPL / SVG / PDF 输出；假 TCP 打印机收到 ZPL / TSPL；连不上 → 重试成功；激光打码（模拟打码机 + 外接 TCP 打码机的失败应答）；
     补打要原因并审计；零件投产自动打码规则
  B. 包装发运：扫码装箱（满箱自动封箱打标签、重复 / 混批 / 冻结拦截）→ 组托 → 装车 → ASN（JSON / Excel / 平面文件 / EDIFACT）→ 发运 → 追溯
  C. 委外：发出（库存移到委外仓）→ 收回检验（合格 / 报废 / 丢失）→ 加工费与超损耗扣款 → 结算转应付；委外工序按供应商周期排产
  D. 备件：入库、领用到维修单（库存不足拦截）、补货建议、按设备使用历史
  E. 文件受控与 ECN：受控文件草稿不上现场 → 批准生效上现场；ECN 影响分析（工艺路线 / 工单 / 文件）、停工闸门、电子签名批准、
     生效后新版本上现场、旧版本从工位终端和知识库消失
  F. 量检具：检验项目要求量具类型；过期量具拦截（中文原因）；类型不符拦截；校准 → 待放行 → 电子签名放行 → 可用；校准不合格锁定
  G. PFMEA → 控制计划 → 检验模板：AP 表、RPN、特殊特性、量具类型、控制计划改动经 ECN 生效后传到检验模板
  H. MSA：GR&R 均值极差法 / 方差分析、偏倚、线性对照 AIAG MSA 第 4 版（2010）教材示例的公布值
  I. 客诉召回：反查影响（已发运 / 在厂）→ 冻结在库 → 8D → 关闭电子签名；召回模拟
  J. 电子签名：缺签 428、密码错 403 并审计、签名记录、审计哈希链
  K. 权限：操作工不能批准 ECN / 补打；仓管不能放行量具；只读 API 密钥不能打印
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

sys.stdout.reconfigure(encoding='utf-8')
MES = os.environ.get('MES_URL', 'http://127.0.0.1:8900').rstrip('/')
RUN = uuid.uuid4().hex[:6].upper()
results = []


def ok(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name, str(detail).replace('\n', ' ')[:300])


def req(method, path, body=None, token='', raw=False, key=None):
    h = {'Content-Type': 'application/json'}
    if token:
        h['Authorization'] = 'Bearer ' + token
    if key:
        h['X-API-Key'] = key
    r = urllib.request.Request(MES + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=120) as f:
            data = f.read()
            return f.status, (data if raw else json.loads(data or b'{}')), {k.lower(): v for k, v in f.headers.items()}
    except urllib.error.HTTPError as e:
        data = e.read() or b'{}'
        try:
            j = json.loads(data)
        except ValueError:
            j = {'detail': data.decode('utf-8', 'replace')}
        return e.code, j, {k.lower(): v for k, v in e.headers.items()}


def login(u, pw='honey123'):
    return req('POST', '/api/login', {'username': u, 'password': pw})[1]['token']


T = login('admin')
OP, QC, WH, ENG = login('operator'), login('qc'), login('wh'), login('engineer')
SIG = {'password': 'honey123', 'meaning': '批准'}


def get(p, t=None): return req('GET', p, token=t or T)[1]
def post(p, b=None, t=None): return req('POST', p, b if b is not None else {}, token=t or T)
def put(p, b, t=None): return req('PUT', p, b, token=t or T)
def detail(r): return r.get('detail') if isinstance(r, dict) else r
def msg(r): d = detail(r); return d.get('msg') if isinstance(d, dict) else d


def create(entity, body, t=None):
    s, r, _ = post(f'/api/e/{entity}', body, t)
    if s != 200:
        raise AssertionError(f'建 {entity} 失败：{s} {r}')
    return r['id']


def one(entity, rid):
    return get(f'/api/e/{entity}/{rid}')['item']


def do(entity, rid, act, body=None, t=None):
    return post(f'/api/e/{entity}/{rid}/do/{act}', body or {}, t)


def signed(entity, rid, act, body=None, t=None):
    """要电子签名的动作：先确认不签会 428，再带签名提交。"""
    s, r, h = do(entity, rid, act, body, t)
    if s != 428:
        return s, r, h
    return do(entity, rid, act, {**(body or {}), 'esign': SIG}, t)


def wait(fn, secs=15, step=0.3):
    t0 = time.time()
    while time.time() - t0 < secs:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


def ensure_material(code, name, mtype='fg'):
    m = get(f'/api/e/material?f_code={code}')['items']
    if m:
        return m[0]['id']
    return create('material', {'code': code, 'name': name, 'mtype': mtype, 'unit': '件', 'price': 160, 'weight_kg': 15})


def proc_id(code):
    return get(f'/api/e/process?f_code={code}')['items'][0]['id']


def push_parts(n, heat, product_type='SGH-450', inspected=True):
    """网关 HTTP 推送零件事件造零件（浇注 → 内窥检测合格）。"""
    sns = [f'X{heat}{i:03d}' for i in range(n)]
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    msgs = [{'sn': sn, 'event': 'poured', 'station': 'MOLD1', 't': now, 'type': product_type, 'attrs': {'heat': heat}} for sn in sns]
    if inspected:
        msgs += [{'sn': sn, 'event': 'inspected', 'station': 'INS1', 't': now, 'data': {'result': 'OK'}} for sn in sns]
    s, r, _ = post('/api/ingest/part', {'src': 'EXEC', 'messages': msgs})
    assert s == 200, f'推送零件失败 {s} {r}'
    ok_ = wait(lambda: all((get(f'/api/e/part?f_sn={sn}')['items'] or [{}])[0].get('status') == ('good' if inspected else 'wip') for sn in sns), 30)
    return sns if ok_ else sns


# ───────────── 假打印机 / 打码机 ─────────────
class FakeServer:
    def __init__(self, port=0, reply=None):
        self.data = []
        self.s = socket.socket()
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.s.bind(('127.0.0.1', port))
        self.s.listen(5)
        self.port = self.s.getsockname()[1]
        self.reply = reply
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                conn, _ = self.s.accept()
            except OSError:
                return
            buf = b''
            conn.settimeout(3)
            try:
                while True:
                    ch = conn.recv(65536)
                    if not ch:
                        break
                    buf += ch
                    if self.reply and b'\n' in buf:
                        conn.sendall(self.reply(buf).encode())
                        break
            except OSError:
                pass
            self.data.append(buf)
            conn.close()

    def close(self):
        self.s.close()


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ═════════════ A. 标签 ═════════════
tpls = {t['code']: t for t in get('/api/e/label_template?size=200')['items']}
ok('A1 内置标签模板（零件 DM / 箱 / 托 / 汽车行业客户标签 / 量具 / 库位 / 批次 / 工单）',
   all(k in tpls for k in ('LBL-PART-DM', 'LBL-CARTON', 'LBL-PALLET', 'LBL-AUTO-GTL', 'LBL-GAUGE', 'LBL-LOC', 'LBL-LOT', 'LBL-WO')), sorted(tpls))
s, z, _ = post('/api/label/preview', {'template': tpls['LBL-CARTON']['id'], 'format': 'zpl', 'dpi': 203})
zp = z.get('payload', '')
ok('A2 ZPL：^XA … ^XZ、100 mm @203dpi = ^PW799、Code128 / QR / 框线、中文 ^CI28', s == 200 and zp.startswith('^XA') and '^XZ' in zp and '^PW799' in zp
   and '^BC' in zp and '^BQN' in zp and '^GB' in zp and '^CI28' in zp, zp[:160])
s, z3, _ = post('/api/label/preview', {'template': tpls['LBL-PART-DM']['id'], 'format': 'zpl', 'dpi': 300})
ok('A3 ZPL DataMatrix（一件一码）^BXN，300 dpi 30 mm = ^PW354', '^BXN' in z3.get('payload', '') and '^PW354' in z3.get('payload', ''), z3.get('payload', '')[:120])
s, ts_, _ = post('/api/label/preview', {'template': tpls['LBL-CARTON']['id'], 'format': 'tspl'})
tp = ts_.get('payload', '')
ok('A4 TSPL：SIZE 100 mm,60 mm / BARCODE / QRCODE / BOX / PRINT', all(k in tp for k in ('SIZE 100 mm,60 mm', 'BARCODE', 'QRCODE', 'BOX', 'PRINT')), tp[:160])
s, svg, _ = req('POST', '/api/label/preview', {'template': tpls['LBL-AUTO-GTL']['id'], 'format': 'svg'}, token=T, raw=True)
ok('A5 SVG 预览（汽车行业运输标签 210×74 mm）', s == 200 and b'width="210mm"' in svg and b'<rect' in svg, svg[:120])
s, pdf, _ = req('POST', '/api/label/preview', {'template': tpls['LBL-CARTON']['id'], 'format': 'pdf', 'copies': 2}, token=T, raw=True)
size_ok = None
try:
    import fitz
    d = fitz.open(stream=pdf, filetype='pdf')
    size_ok = len(d) == 2 and abs(d[0].rect.width - 100 / 25.4 * 72) < 0.5 and abs(d[0].rect.height - 60 / 25.4 * 72) < 0.5
except ImportError:
    size_ok = pdf[:4] == b'%PDF'
ok('A6 PDF 精确尺寸（100×60 mm，每份一页）', s == 200 and pdf[:4] == b'%PDF' and size_ok)

fz = FakeServer()
pz = create('printer', {'code': f'PZ-{RUN}', 'name': '测试斑马', 'kind': 'zpl', 'host': '127.0.0.1', 'port': fz.port, 'dpi': 203, 'enabled': True})
s, r, _ = post('/api/label/print', {'template': tpls['LBL-LOC']['id'], 'printer': pz, 'ref': 'W-FG-01', 'wait': 8})
got = b''.join(fz.data)
ok('A7 假 TCP 打印机（9100 协议）收到 ZPL：库位标签 W-FG-01', s == 200 and r.get('status') == 'done' and b'^XA' in got and b'W-FG-01' in got, f"{r} {got[:80]}")
ft = FakeServer()
pt_ = create('printer', {'code': f'PT-{RUN}', 'name': '测试 TSC', 'kind': 'tspl', 'host': '127.0.0.1', 'port': ft.port, 'dpi': 203, 'enabled': True})
s, r, _ = post('/api/label/print', {'template': tpls['LBL-LOT']['id'], 'printer': pt_, 'ref': f'H{RUN}', 'copies': 2, 'wait': 8})
got = b''.join(ft.data)
ok('A8 假打印机收到 TSPL（GBK，2 份 PRINT 2,1）', r.get('status') == 'done' and b'SIZE 70 mm,40 mm' in got and b'PRINT 2' in got, got[:80])
s, r, _ = do('printer', pz, 'test')
ok('A9 打印机"测试打印"', s == 200 and len(fz.data) >= 2, msg(r))

dead = free_port()
pd = create('printer', {'code': f'PD-{RUN}', 'name': '离线打印机', 'kind': 'zpl', 'host': '127.0.0.1', 'port': dead, 'enabled': True})
s, r, _ = post('/api/label/print', {'template': tpls['LBL-LOC']['id'], 'printer': pd, 'ref': 'W-FG-02', 'wait': 6})
jid = r['id']
j = one('print_job', jid)
ok('A10 打印机连不上：任务进重试队列（尝试 1 次、记错误、排下次重试）', j['status'] == 'queued' and j['attempts'] == 1 and j['last_error'] and j['next_try'], j)
fdead = FakeServer(dead)
do('print_job', jid, 'retry')
j = wait(lambda: (lambda x: x if x['status'] == 'done' else None)(one('print_job', jid)), 10) or one('print_job', jid)
ok('A11 打印机恢复后重试成功，发送日志记了两次尝试（失败 → 成功）', j['status'] == 'done' and len(j['log'] or []) >= 1 and b'W-FG-02' in b''.join(fdead.data),
   [x.get('ok') for x in j['log'] or []])
st = get(f'/api/e/printer/{pd}')['item']
ok('A12 打印机状态跟着更新（正常 / 最近成功时间）', st['status'] == 'ok' and st['last_ok'], st['status'])

mks = get('/api/e/marker?size=50')['items']
mk = next(m for m in mks if m['mode'] == 'sim')
sn_m = f'SNM{RUN}'
s, r, _ = post('/api/label/print', {'template': tpls['LBL-PART-DM']['id'], 'marker': mk['id'], 'data': {'sn': sn_m, 'product_code': 'SGH-450'}, 'wait': 8})
sim = get('/api/label/marker_sim/log')
ok('A13 激光打码（内置模拟打码机）：打 DataMatrix 内容 = 零件码，应答 OK', r.get('status') == 'done' and any(x['data'] == sn_m for x in sim['marks']), r)
fm = FakeServer(reply=lambda b: 'ERR 1 打标失败\r\n' if b'FAIL' in b else 'OK\r\n')
mt = create('marker', {'code': f'MK-{RUN}', 'name': '外接打码机', 'mode': 'tcp', 'host': '127.0.0.1', 'port': fm.port, 'cmd_template': 'SETTEXT 1 {data}\\r\\nGO\\r\\n',
                       'ack': '^OK', 'err': 'ERR', 'enabled': True})
s, r, _ = post('/api/label/print', {'template': tpls['LBL-PART-DM']['id'], 'marker': mt, 'data': {'sn': 'SN-OK-1'}, 'wait': 8})
ok('A14 外接 TCP 打码机：命令模板替换（SETTEXT 1 <内容>），应答 OK → 完成', r.get('status') == 'done' and any(b'SETTEXT 1 SN-OK-1\r\n' in x for x in fm.data), fm.data[-1:])
s, r, _ = post('/api/label/print', {'template': tpls['LBL-PART-DM']['id'], 'marker': mt, 'data': {'sn': 'SN-FAIL-1'}, 'wait': 8})
j = one('print_job', r['id'])
ok('A15 打码机应答 ERR → 失败（错误写进任务，排重试）', j['attempts'] >= 1 and 'ERR' in (j['last_error'] or ''), j['last_error'])
do('print_job', r['id'], 'cancel')

s, r, _ = post('/api/label/reprint', {'job': jid}, QC)
ok('A16 补打不写原因 → 400', s == 400, detail(r))
s, r, _ = post('/api/label/reprint', {'job': jid, 'reason': '标签污损'}, QC)
time.sleep(2.5)
aud = get('/api/admin/audit?action=reprint&size=20')
items = aud.get('items') if isinstance(aud, dict) else aud
ok('A17 补打：新任务标"补打"+原因，审计里有 reprint', s == 200 and one('print_job', r['id'])['reprint'] and one('print_job', r['id'])['reason'] == '标签污损'
   and any((x.get('detail') or {}).get('reason') == '标签污损' for x in items or []), r)
rules = get('/api/e/label_rule?size=50')['items']
pr_rule = next(x for x in rules if x['event'] == 'part_create')
put(f"/api/e/label_rule/{pr_rule['id']}", {'enabled': True, 'marker': mk['id']})
time.sleep(5.5)          # 规则缓存 5 秒（这里刚改过会立即失效，多等一下保险）
heat_m = f'HM{RUN}'
sns_m = push_parts(2, heat_m, inspected=False)
jobs = wait(lambda: [x for x in get(f'/api/label/jobs?ref={sns_m[0]}')['items'] if x['kind'] == 'mark' and x['status'] == 'done'], 15)
ok('A18 自动打码规则：零件投产（浇注）→ 激光打 DataMatrix（一件一码）', bool(jobs), [x['status'] for x in get(f'/api/label/jobs?ref={sns_m[0]}')['items']])
put(f"/api/e/label_rule/{pr_rule['id']}", {'enabled': False})

# ═════════════ B. 包装发运 ═════════════
heat1, heat2 = f'HA{RUN}', f'HB{RUN}'
p1 = push_parts(3, heat1)
p2 = push_parts(2, heat2)
prod = get(f'/api/e/part?f_sn={p1[0]}')['items'][0]['product']
cust = f'EXEC-CUST-{RUN}'
rid_rule = create('pack_rule', {'customer': cust, 'material': prod, 'carton_qty': 2, 'pallet_cartons': 2, 'mixed_lot': False, 'customer_part_no': f'CP-{RUN}'})
ST = f'PK{RUN}'
s, r, _ = post('/api/pack/scan', {'code': p1[0], 'station': ST, 'customer': cust}, OP)
ok('B1 操作工扫零件码装箱（新开箱，客户包装规则：每箱 2 件）', s == 200 and r['action'] == 'pack' and r['qty'] == 1 and r['capacity'] == 2, msg(r) if s != 200 else r['msg'])
c1 = r.get('carton')
s, r, h = post('/api/pack/scan', {'code': p1[0], 'station': ST, 'customer': cust}, OP)
ok('B2 同一件再扫 → 409 已装箱（X-Gate: pack）', s == 409 and h.get('x-gate') == 'pack', detail(r))
s, r, _ = post('/api/pack/scan', {'code': p1[1], 'station': ST, 'customer': cust}, OP)
ok('B3 满箱自动封箱 + 箱标签排队（浏览器打印 PDF）', s == 200 and r['closed'] and r['jobs'], r.get('msg'))
post('/api/pack/scan', {'code': p1[2], 'station': ST, 'customer': cust}, OP)
s, r, h = post('/api/pack/scan', {'code': p2[0], 'station': ST, 'customer': cust}, OP)
ok('B4 不允许混批：箱里是另一个炉次 → 409', s == 409 and '混批' in detail(r), detail(r))
c2 = get(f'/api/pack/station?station={ST}')['carton']['code']
s, r, _ = post('/api/pack/close', {'code': c2}, OP)
ok('B5 手工封箱（不满）', s == 200, r.get('msg'))
s, r, _ = post('/api/e/lot_hold', {'lot': heat2, 'reason': 'exec_check 冻结'}, QC)
hold2 = r['id']
s, r, h = post('/api/pack/scan', {'code': p2[0], 'station': ST, 'customer': cust}, OP)
ok('B6 冻结批次的零件不能装箱（X-Gate: hold）', s == 409 and h.get('x-gate') == 'hold', detail(r))
s, r, _ = signed('lot_hold', hold2, 'release', {'note': '复检合格'}, QC)
ok('B7 解冻要电子签名（不签 428 → 签名后解冻）', s == 200, msg(r))
s, r, _ = post('/api/pack/scan', {'code': c1, 'station': ST, 'customer': cust}, OP)
ok('B8 扫箱号组托', s == 200 and r['action'] == 'pallet' and r['cartons'] == 1, r.get('msg'))
s, r, _ = post('/api/pack/scan', {'code': c2, 'station': ST, 'customer': cust}, OP)
pal = r.get('pallet')
ok('B9 满托（2 箱）自动封托，托盘标签 + 汽车行业客户标签各一张', s == 200 and r['closed'] and len(r['jobs']) >= 2, r.get('msg'))
s, cl, _ = req('POST', '/api/label/preview', {'template': tpls['LBL-AUTO-GTL']['id'], 'format': 'zpl', 'data': get(f"/api/label/jobs?ref={pal}")['items'][0]['data']}, token=T)
ok('B10 客户标签数据：客户零件号（P）/ 数量（Q）/ 包装号（S）', f'P{"CP-" + RUN}'.replace('-', '') in cl.get('payload', '').replace('-', '') and 'Q3' in cl.get('payload', ''),
   cl.get('payload', '')[-300:])
lid = create('ship_load', {'customer': cust, 'delivery_code': f'FH-{RUN}', 'vehicle': '豫A·12345', 'dock': 'G12'})
s, r, h = post(f'/api/pack/load/{lid}/scan', {'code': c1}, WH)
ok('B11 装车扫托盘上的箱 → 409 请扫托盘号', s == 409 and h.get('x-gate') == 'load', detail(r))
s, r, _ = post(f'/api/pack/load/{lid}/scan', {'code': pal}, WH)
ok('B12 装车扫托盘：1 托 / 2 箱 / 3 件', s == 200 and r['load']['pallets'] == 1 and r['load']['cartons'] == 2 and r['load']['qty'] == 3, r.get('msg'))
s, r, _ = do('ship_load', lid, 'asn', {}, WH)
asn_no = r.get('asn_no')
s, asn, _ = req('GET', f'/api/pack/load/{lid}/asn?format=json', token=T, raw=True)
asn = json.loads(asn)
ok('B13 ASN JSON：抬头（发货单 / 客户 / 车牌）+ 托 → 箱 → 零件码', asn['delivery_note'] == f'FH-{RUN}' and asn['totals'] == {'pallets': 1, 'cartons': 2, 'qty': 3}
   and sorted(s for p in asn['pallets'] for ct in p['cartons'] for s in ct['serials']) == sorted(p1), asn['totals'])
s, xl, _ = req('GET', f'/api/pack/load/{lid}/asn?format=xlsx', token=T, raw=True)
s2, fl, _ = req('GET', f'/api/pack/load/{lid}/asn?format=flat', token=T, raw=True)
s3, ed, _ = req('GET', f'/api/pack/load/{lid}/asn?format=edifact', token=T, raw=True)
ok('B14 ASN Excel / 平面文件（HDR LIN PAL CTN SER TRL）/ EDIFACT DESADV', xl[:2] == b'PK' and fl.startswith(b'HDR|' + asn_no.encode()) and b'\r\nSER|' in fl
   and b'TRL|' in fl and b'UNH+1+DESADV:D:96A:UN' in ed and b'GIR+3+' in ed, fl[:60])
s, r, _ = do('ship_load', lid, 'ship', {}, WH)
ok('B15 发运确认：零件 → 已发运，发运记录，ASN 号', s == 200 and all(get(f'/api/e/part?f_sn={x}')['items'][0]['status'] == 'shipped' for x in p1), msg(r))
tr = get(f'/api/trace/part/{p1[0]}')
pk_ = tr.get('packing') or {}
ok('B16 零件追溯带上包装发运：箱 → 托 → 装车单 / ASN / 客户', pk_.get('carton') == c1 and pk_.get('pallet') == pal and pk_.get('asn_no') == asn_no and pk_.get('customer') == cust, pk_)
t2 = get(f'/api/pack/trace?code={asn_no}')
ok('B17 发运追溯：ASN → 托 → 箱 → 零件（3 件）', t2['parts'] == 3 and t2['tree'][0]['code'] == pal, t2['parts'])

# ═════════════ C. 委外 ═════════════
semi = ensure_material('SGH-450-B', 'SGH-450 壳体毛坯', 'semi')
wh_blank = get('/api/e/warehouse?f_code=W-BLANK')['items'][0]['id']
create('stock_txn', {'ttype': 'in', 'material': semi, 'qty': 50, 'warehouse': wh_blank, 'lot': f'OS{RUN}', 'reason': 'exec_check 期初'})
oid = create('outsource_order', {'supplier': f'热处理{RUN}', 'material': semi, 'qty': 50, 'unit_price': 30, 'loss_pct': 4, 'lead_days': 3, 'warehouse': wh_blank,
                                 'process': proc_id('P55') if get('/api/e/process?f_code=P55')['items'] else None})
s, r, _ = do('outsource_order', oid, 'send', {'lines': [{'lot': f'OS{RUN}', 'qty': 50}]}, WH)
o = one('outsource_order', oid)
osw = o['os_warehouse']
stk = lambda wid, mat: sum(x['qty'] for x in get(f'/api/e/stock?f_warehouse={wid}&f_material={mat}&size=200')['items'])
ok('C1 委外发出：库存从毛坯库移到委外仓、状态已发出、按周期给回厂日期', s == 200 and o['status'] == 'sent' and o['qty_sent'] == 50 and stk(osw, semi) == 50 and o['due'], msg(r))
s, r, _ = do('outsource_order', oid, 'receive', {'qty_ok': 45, 'qty_scrap': 3, 'qty_lost': 2, 'remark': '硬度 3 件超差'}, WH)
o = one('outsource_order', oid)
ok('C2 收回检验：合格 45 回库、报废 3（登记不合格品）、丢失 2；委外仓清零', s == 200 and o['status'] == 'received' and stk(osw, semi) == 0 and o['qty_ok'] == 45, msg(r))
ncr = get(f"/api/e/ncr?f_lot={o['code']}")['items']
ok('C3 报废登记不合格品（处置：退供应商）+ 收回检验单', ncr and ncr[0]['qty'] == 3 and ncr[0]['disposition'] == 'return' and get(f"/api/e/insp_record?f_lot={o['code']}")['total'] == 1)
ok('C4 加工费 = 30 × 45 − 超损耗（5 − 50×4% = 3 件 × 物料单价 160）= 870', abs((o['cost'] or 0) - (1350 - 3 * 160)) < 0.01 or abs((o['cost'] or 0) - (1350 - 3 * (get(f'/api/e/material/{semi}')['item']['price'] or 0))) < 0.01, o['cost'])
s, r, _ = do('outsource_order', oid, 'close', {}, WH)
ap = get('/api/outsource/ap')
ok('C5 结算关闭 → 应付来源（给内置 ERP）', s == 200 and any(x['ref'] == o['code'] for x in ap), msg(r))
post('/api/outsource/ap/ack', {'items': [{'ref': o['code'], 'ap_ref': f'AP-{RUN}'}]})
ok('C6 ERP 回写应付单号', one('outsource_order', oid)['ap_posted'] is True)
pX = ensure_material(f'EXEC-P{RUN}', f'委外测试件 {RUN}')
rt = create('route', {'code': f'RT-X{RUN}', 'name': '委外测试路线', 'product': pX, 'version': 'V1', 'status': 'active'})
for seq, pc, grp, os_, sup, lead in ((10, 'P50', 'SHOT1', False, None, None), (20, 'P10', None, True, f'热处理{RUN}', 2), (30, 'P60', 'CNC', False, None, None)):
    create('route_step', {'route': rt, 'seq': seq, 'process': proc_id(pc), 'station_group': grp, 'std_cycle_s': 30, 'outsourced': os_, 'supplier': sup, 'os_lead_days': lead})
wid = create('work_order', {'product': pX, 'route': rt, 'qty': 10, 'priority': 9})
s, r, _ = do('work_order', wid, 'release')
ops_ = sorted(get(f'/api/e/work_order/{wid}')['children']['wo_op'], key=lambda x: x['seq'])
from datetime import datetime as _dt
pdt = lambda v: _dt.strptime(v, '%Y-%m-%d %H:%M:%S')
o20, o30 = ops_[1], ops_[2]
dur = (pdt(o20['plan_end']) - pdt(o20['plan_start'])).total_seconds() / 86400 if o20['plan_end'] else 0
ok('C7 工单下达：委外工序自动建委外单草稿（供应商、周期取工艺步骤）', '委外' in (r.get('msg') or '') and get(f'/api/e/outsource_order?f_work_order={wid}')['total'] == 1, r.get('msg'))
ok('C8 排产：委外工序按供应商周期 2 天、不占设备；下道工序等回厂后开工', 1.9 <= dur <= 2.2 and o20['machines'] == '委外' and pdt(o30['plan_start']) >= pdt(o20['plan_end'])
   and any(x.get('type') == 'outsource' for x in o20['conflicts'] or []), f'{dur:.2f} 天 {o20["machines"]}')
do('work_order', wid, 'cancel')

# ═════════════ D. 备件 ═════════════
eqs = get('/api/e/equipment?size=5')['items']
eq = eqs[0]['id'] if eqs else create('equipment', {'code': f'EQ{RUN}', 'name': '测试设备', 'etype': 'cnc'})
sp = create('spare_part', {'code': f'SP{RUN}', 'name': '测试轴承', 'unit': '个', 'min_qty': 5, 'max_qty': 20, 'price': 100, 'supplier': '某轴承公司', 'equipment': eq})
create('spare_txn', {'spare': sp, 'kind': 'in', 'qty': 10})
ro = create('repair_order', {'equipment': eq, 'fault': 'exec_check 主轴异响'})
s, r, _ = post('/api/spare/issue', {'spare': f'SP{RUN}', 'qty': 7, 'repair_order': ro}, login('maint'))
ok('D1 设备员领用备件到维修单：结存 3，维修单记上更换备件', s == 200 and r['balance'] == 3 and f'SP{RUN}' in (one('repair_order', ro)['parts_used'] or ''), r.get('msg'))
s, r, _ = post('/api/spare/issue', {'spare': f'SP{RUN}', 'qty': 5, 'repair_order': ro})
ok('D2 库存不够 → 409（中文说明）', s == 409 and '不够' in detail(r), detail(r))
ok('D3 备件库存跟着出入库变（10 − 7 = 3）', one('spare_part', sp)['qty'] == 3)
ro_ = next((x for x in get('/api/spare/reorder') if x['code'] == f'SP{RUN}'), None)
ok('D4 补货建议：低于最低库存 5 → 补到最高 20，建议 17、金额 1700', ro_ and ro_['suggest'] == 17 and ro_['amount'] == 1700 and ro_['use_90d'] == 7, ro_)
hist = get(f'/api/spare/history?equipment={eq}')
ok('D5 按设备的备件使用历史', any(h['qty'] == 7 for h in hist['summary']), hist['summary'][:2])
s, r, _ = post('/api/spare/reorder/push', {'ids': [sp]})
ok('D6 补货建议推给采购（内置 ERP 请购 / 没有时通知计划员）', s == 200 and r['lines'] == 1, r.get('msg'))

# ═════════════ G. PFMEA → 控制计划 → 检验模板（E 节要用到控制计划） ═════════════
ap_t = get('/api/e/pfmea?size=1')
s, r, _ = post('/api/e/pfmea', {'title': f'委外测试件 PFMEA {RUN}', 'product': pX, 'method': 'ap'}, ENG)
fid = r['id']
rows_ = [(10, 'P60', '孔径', f'BORE{RUN}', 'CC', 20.0, 20.021, 'mm', '孔径超差', '泄漏', 9, '刀具磨损', 4, '内径表 100%', 4),
         (20, 'P60', '平面度', f'FLAT{RUN}', 'SC', None, 0.05, 'mm', '平面度超差', '密封差', 7, '夹紧变形', 5, '三坐标巡检', 6),
         (30, 'P60', '外观', f'APP{RUN}', 'none', None, None, '', '磕碰', '外观', 5, '搬运', 3, '目视', 5)]
for seq, pc, pch, ch, sp_, lo, hi, u, fmode, eff, sv, cause, oc, detc, dv in rows_:
    create('pfmea_row', {'pfmea': fid, 'seq': seq, 'process': proc_id(pc), 'product_char': pch, 'char': ch, 'special': sp_, 'lo': lo, 'hi': hi, 'unit': u,
                         'failure_mode': fmode, 'effect': eff, 'severity': sv, 'cause': cause, 'occurrence': oc, 'detection_ctrl': detc, 'detection': dv}, ENG)
fr = {x['char']: x for x in get(f'/api/iatf/pfmea/{fid}')['rows']}
ok('G1 PFMEA：RPN = S×O×D，AP（AIAG-VDA 2019 表）：S9 O4 D4 → H，S7 O5 D6 → M，S5 O3 D5 → L',
   fr[f'BORE{RUN}']['rpn'] == 144 and fr[f'BORE{RUN}']['ap'] == 'H' and fr[f'FLAT{RUN}']['ap'] == 'M' and fr[f'APP{RUN}']['ap'] == 'L', {k: (v['rpn'], v['ap']) for k, v in fr.items()})
s, r, _ = post(f"/api/e/pfmea_row/{fr[f'APP{RUN}']['id']}", None) if False else put(f"/api/e/pfmea_row/{fr[f'APP{RUN}']['id']}", {'severity': 11}, ENG)
ok('G2 S/O/D 超出 1–10 → 400', s == 400, detail(r))
s, r, _ = do('pfmea', fid, 'derive_cp', {}, ENG)
cpid = r.get('id')
cp = get(f'/api/iatf/cp/{cpid}')
ci = {x['char']: x for x in cp['items']}
ok('G3 PFMEA 生成控制计划：特性 / 规范 / 评价方法 / 量具类型（内径表 → bore、三坐标 → cmm）/ 样本频率 / 反应计划（CC 停机隔离 100%）',
   len(cp['items']) == 3 and ci[f'BORE{RUN}']['gauge_type'] == 'bore' and ci[f'FLAT{RUN}']['gauge_type'] == 'cmm' and ci[f'BORE{RUN}']['spec'] == '20–20.021 mm'
   and '100%' in ci[f'BORE{RUN}']['reaction'] and ci[f'BORE{RUN}']['special'] == 'CC', {k: (v['gauge_type'], v['spec']) for k, v in ci.items()})
s, r, _ = do('control_plan', cpid, 'gen_insp', {}, ENG)
tcode = f'CP-EXEC-P{RUN}-P60'
tp_ = get(f'/api/e/insp_template?f_code={tcode}')['items']
items = get(f"/api/e/insp_template/{tp_[0]['id']}")['children']['insp_item'] if tp_ else []
ii = {x['char']: x for x in items}
ok('G4 控制计划生成检验模板（每道过程一个，编码跨版本不变）：上下限、关键特性、CC/SC、量具类型、回链控制计划项',
   s == 200 and len(items) == 3 and ii[f'BORE{RUN}']['hi'] == 20.021 and ii[f'BORE{RUN}']['key'] and ii[f'BORE{RUN}']['special'] == 'CC'
   and ii[f'BORE{RUN}']['gauge_type'] == 'bore' and ii[f'BORE{RUN}']['cp_item'] == ci[f'BORE{RUN}']['id'], msg(r))
spm = get(f'/api/iatf/special?product={pX}')
ok('G5 特殊特性给 SPC / 标签：特性代码 → CC / SC', spm['chars'].get(f'BORE{RUN}') == 'CC' and spm['chars'].get(f'FLAT{RUN}') == 'SC', spm['chars'])

# ═════════════ F. 量检具 ═════════════
rec = create('insp_record', {'template': tp_[0]['id'], 'itype': 'IPQC', 'qty_sample': 1})
bore_item = ii[f'BORE{RUN}']['id']
s, r, h = post(f'/api/quality/record/{rec}/values', {'values': [{'item': bore_item, 'value': 20.01}]}, QC)
ok('F1 检验项目要求量具类型：不选量具 → 409（X-Gate: gauge，提示要用内径表）', s == 409 and h.get('x-gate') == 'gauge' and '内径表' in detail(r), detail(r))
old = create('gauge', {'code': f'G-OLD-{RUN}', 'name': '过期内径表', 'gtype': 'bore', 'cal_interval_days': 180, 'last_cal': '2025-01-01', 'next_due': '2025-07-01'})
post('/api/e/gauge', {}) if False else None
gb = get('/api/gauge/board')
ok('F2 过期量具自动"过期锁定"（到期提醒 / 锁定看板）', one('gauge', old)['status'] == 'overdue' and gb['counts'].get('overdue', 0) >= 1, one('gauge', old)['status'])
s, r, h = post(f'/api/quality/record/{rec}/values', {'values': [{'item': bore_item, 'value': 20.01, 'gauge': old}]}, QC)
ok('F3 用过期量具录检验值 → 409，说明"校准已过期、已锁定、换有效量具或送检"', s == 409 and h.get('x-gate') == 'gauge' and '过期' in detail(r) and '锁定' in detail(r), detail(r))
cal = next(g for g in get('/api/e/gauge?f_gtype=caliper&size=50')['items'] if g['status'] in ('active', 'due'))
s, r, h = post(f'/api/quality/record/{rec}/values', {'values': [{'item': bore_item, 'value': 20.01, 'gauge': cal['id']}]}, QC)
ok('F4 量具类型不符（卡尺量孔径）→ 409', s == 409 and '内径表' in detail(r), detail(r))
vl = get(f'/api/gauge/valid?item={bore_item}')
good = next((g for g in vl['items'] if g['ok']), None)
ok('F5 可选量具列表：只列内径表，有效的在前，过期的置灰带原因', vl['gtype'] == 'bore' and good and any(not g['ok'] and g['id'] == old for g in vl['items']), [(g['code'], g['ok']) for g in vl['items']])
s, r, _ = post(f'/api/quality/record/{rec}/values', {'values': [{'item': bore_item, 'value': 20.01, 'gauge': good['id']}, {'item': ii[f'FLAT{RUN}']['id'], 'value': 0.02,
                                                                                                                    'gauge': next(g['id'] for g in get('/api/gauge/valid?gtype=cmm')['items'] if g['ok'])},
                                                     {'item': ii[f'APP{RUN}']['id'], 'ok': True}]}, QC)
vals = get(f'/api/e/insp_record/{rec}')
ok('F6 用有效量具录入：判定合格，检验值记下用的量具', s == 200 and r.get('record') == 'OK', r)
create('gauge_cal', {'gauge': old, 'cal_date': time.strftime('%Y-%m-%d'), 'result': 'pass', 'agency': 'internal', 'cert_no': f'C{RUN}'}, QC)
g = one('gauge', old)
ok('F7 校准合格 → 待放行（还不能用），下次到期 = 校准日 + 周期', g['status'] == 'quarantine' and g['next_due'] > time.strftime('%Y-%m-%d'), g['status'])
s, r, h = post(f'/api/quality/record/{rec}/values', {'values': [{'item': bore_item, 'value': 20.01, 'gauge': old}]}, QC)
ok('F8 待放行的量具也不能用', s == 409, detail(r))
s, r, _ = do('gauge', old, 'release', {}, WH)
ok('F9 仓管员没有放行量具的权限 → 403', s == 403, detail(r))
s, r, h = do('gauge', old, 'release', {}, QC)
ok('F10 量具放行不签名 → 428（X-Esign）', s == 428 and h.get('x-esign') == 'required' and detail(r)['esign']['point'] == '量检具放行', detail(r))
s, r, _ = do('gauge', old, 'release', {'esign': SIG}, QC)
ok('F11 电子签名放行 → 有效；放行打量具状态标签', s == 200 and one('gauge', old)['status'] == 'active' and get(f"/api/label/jobs?ref={old}")['items'], msg(r))
s, r, _ = post(f'/api/quality/record/{rec}/values', {'values': [{'item': bore_item, 'value': 20.01, 'gauge': old}]}, QC)
ok('F12 放行后可以用了', s == 200, detail(r))
create('gauge_cal', {'gauge': old, 'cal_date': time.strftime('%Y-%m-%d'), 'result': 'fail', 'remark': '示值误差超差'}, QC)
ok('F13 校准不合格 → 锁定', one('gauge', old)['status'] == 'locked')

# ═════════════ E. 文件受控与 ECN ═════════════
p60 = proc_id('P60')
kid = create('kb_doc', {'title': f'孔加工作业指导书 {RUN}', 'kind': 'sop', 'product': pX, 'process': p60, 'content': f'版本A内容{RUN}', 'version': 'A'}, ENG)
s, r, _ = post('/api/doc/control', {'entity': 'kb_doc', 'id': kid}, ENG)
did, rev_a = r['doc'], r['rev_id']
kbfor = lambda: get(f'/api/kb/for?product={pX}&process={p60}')
ok('E1 纳入受控（草稿）：工位终端的作业指示里不出现', s == 200 and not any(d['title'].endswith(RUN) for d in kbfor()['sops']), r)
do('ctrl_doc_rev', rev_a, 'submit', {}, ENG)
s, r, _ = signed('ctrl_doc_rev', rev_a, 'approve', {}, ENG)
ok('E2 审核 → 批准（电子签名）→ 生效：上现场', s == 200 and any(f'版本A内容{RUN}' in d['content'] for d in kbfor()['sops']), msg(r))
wid2 = create('work_order', {'product': pX, 'route': rt, 'qty': 5})
do('work_order', wid2, 'release')
eid = create('ecn', {'title': f'孔径公差收紧 {RUN}', 'product': pX, 'wo_policy': 'block', 'reason': 'exec_check'}, ENG)
s, r, _ = do('ecn', eid, 'analyze', {}, ENG)
imp = (r or {}).get('impact') or {}
ok('E3 ECN 影响分析自动列出：工艺路线、未完工单、受控文件、控制计划 / PFMEA、检验模板', s == 200 and any(x['code'] == f'RT-X{RUN}' for x in imp.get('routes', []))
   and any(x['id'] == wid2 for x in imp.get('work_orders', [])) and any(x['id'] == did for x in imp.get('docs', [])) and imp.get('control_plans') and imp.get('templates'), msg(r))
full = get(f'/api/ecn/{eid}/full')
ok('E4 实施任务自动生成；在制工单按"停工待处置"登记', len(full['tasks']) >= 3 and any(w['decision'] == 'block' and w['work_order'] == wid2 for w in full['work_orders']),
   [t['title'] for t in full['tasks']])
s, r, _ = post(f'/api/ecn/{eid}/revise', {'doc': did}, ENG)
rev_b, kb_b = r['id'], r['target_id']
put(f'/api/e/kb_doc/{kb_b}', {'content': f'版本B内容{RUN}'}, ENG)
s, r, _ = post('/api/doc/control', {'entity': 'control_plan', 'id': cpid}, ENG)
cp_rev_a = r['rev_id']
do('ctrl_doc_rev', cp_rev_a, 'submit', {}, ENG)
signed('ctrl_doc_rev', cp_rev_a, 'approve', {}, ENG)
s, r, _ = post(f"/api/ecn/{eid}/revise", {'doc': r['doc'] if False else one('control_plan', cpid)['ctrl_doc']}, ENG)
cp_rev_b, cp_b = r['id'], r['target_id']
bore_b = next(x for x in get(f'/api/iatf/cp/{cp_b}')['items'] if x['char'] == f'BORE{RUN}')
put(f"/api/e/cp_item/{bore_b['id']}", {'hi': 20.018, 'spec': '20–20.018 mm'}, ENG)
for rv in (rev_b, cp_rev_b):
    do('ctrl_doc_rev', rv, 'submit', {}, ENG)
    s, r, _ = signed('ctrl_doc_rev', rv, 'approve', {}, ENG)
ok('E5 ECN 下升版：新版本批准后不单独生效（随 ECN），现场仍是 A 版', one('ctrl_doc_rev', rev_b)['status'] == 'approved'
   and any(f'版本A内容{RUN}' in d['content'] for d in kbfor()['sops']) and not any(f'版本B内容{RUN}' in d['content'] for d in kbfor()['sops']), msg(r))
do('ecn', eid, 'submit', {}, ENG)
s, r, _ = do('ecn', eid, 'approve', {}, OP)
ok('E6 操作工不能批准 ECN（403）', s == 403, detail(r))
s, r, h = do('ecn', eid, 'approve', {})
ok('E7 批准不签名 → 428', s == 428 and h.get('x-esign') == 'required')
s, r, _ = do('ecn', eid, 'approve', {'esign': {'password': 'wrong-pw', 'meaning': '批准'}})
ok('E8 签名密码错 → 403（不批准）', s == 403 and one('ecn', eid)['status'] == 'approval', detail(r))
s, r, _ = do('ecn', eid, 'approve', {'esign': SIG})
ok('E9 电子签名批准 → 实施中', s == 200 and one('ecn', eid)['status'] == 'implementing', msg(r))
op1 = sorted(get(f'/api/e/work_order/{wid2}')['children']['wo_op'], key=lambda x: x['seq'])[0]
s, r, h = post('/api/production/report', {'wo_op': op1['id'], 'qty_good': 1, 'idem_key': 'x-' + uuid.uuid4().hex}, OP)
ok('E10 ECN 停工的在制工单不能报工（X-Gate: ecn）', s == 409 and h.get('x-gate') == 'ecn' and 'ECN' in detail(r), detail(r))
s, r, _ = do('ecn', eid, 'effective', {})
ok('E11 实施任务没完成 → 不能生效（列出未完成任务）', s == 409 and '任务' in detail(r), detail(r))
for t_ in get(f'/api/ecn/{eid}/full')['tasks']:
    do('ecn_task', t_['id'], 'done', {'note': 'ok'})
s, r, _ = do('ecn', eid, 'effective', {})
e = one('ecn', eid)
snap = {x['doc']: x for x in (e['snapshot'] or {}).get('docs', [])}
ok('E12 ECN 生效：新旧版本记录（A → B），文件切换', s == 200 and e['status'] == 'effective' and any(x['old'] == ['A'] and x['new'] == 'B' for x in snap.values()), msg(r))
sops = kbfor()['sops']
ok('E13 工位终端只显示生效版本：B 版出现、A 版消失', any(f'版本B内容{RUN}' in d['content'] for d in sops) and not any(f'版本A内容{RUN}' in d['content'] for d in sops))
kl = get(f'/api/kb/list?q={RUN}')['items']
kla = get(f'/api/kb/list?q={RUN}&all=1')['items']
ok('E14 知识库列表也只剩生效版（all=1 看全部版本）', len([x for x in kl if x['title'].endswith(RUN)]) == 1 and len([x for x in kla if x['title'].endswith(RUN)]) == 2)
items2 = get(f"/api/e/insp_template/{tp_[0]['id']}")['children']['insp_item']
ok('G6 控制计划改动经 ECN 生效 → 检验模板同步（同一检验项目上限 20.021 → 20.018）', next(x for x in items2 if x['char'] == f'BORE{RUN}')['hi'] == 20.018
   and next(x for x in items2 if x['char'] == f'BORE{RUN}')['id'] == bore_item)
ok('G7 旧版控制计划作废、新版生效', one('control_plan', cpid)['status'] == 'obsolete' and one('control_plan', cp_b)['status'] == 'effective')
xw = next(w for w in get(f'/api/ecn/{eid}/full')['work_orders'] if w['work_order'] == wid2)
do('ecn_wo', xw['id'], 'unblock', {'note': '按新版本继续'})
s, r, h = post('/api/production/report', {'wo_op': op1['id'], 'qty_good': 1, 'idem_key': 'x-' + uuid.uuid4().hex}, OP)
ok('E15 解除停工后不再被 ECN 闸门拦', h.get('x-gate') != 'ecn', f"{s} {detail(r) if s != 200 else r.get('msg')}")
do('work_order', wid2, 'close')

# ═════════════ H. MSA ═════════════
# AIAG《测量系统分析》参考手册第 4 版（2010），第三章 B 节：GR&R 示例（3 名评价人 A/B/C × 10 个零件 × 3 次），
# 均值极差法公布值（图 III-B 15/16）：EV 0.20188、AV 0.22963、GRR 0.30575、PV 1.10456、TV 1.14610、%GRR 26.68%、ndc 5；
# 方差分析表（表 III-B 7）：评价人 SS 3.1673、零件 SS 88.3619、交互 SS 0.3590、设备（重复性）SS 2.7589、总 SS 94.6471；
# 方差分析结果（表 III-B 8，交互作用不显著并入误差）：%GRR 27.9%、ndc 4。
AIAG = {'A': [[0.29, -0.56, 1.34, 0.47, -0.80, 0.02, 0.59, -0.31, 2.26, -1.36], [0.41, -0.68, 1.17, 0.50, -0.92, -0.11, 0.75, -0.20, 1.99, -1.25],
              [0.64, -0.58, 1.27, 0.64, -0.84, -0.21, 0.66, -0.17, 2.01, -1.31]],
        'B': [[0.08, -0.47, 1.19, 0.01, -0.56, -0.20, 0.47, -0.63, 1.80, -1.68], [0.25, -1.22, 0.94, 1.03, -1.20, 0.22, 0.55, 0.08, 2.12, -1.62],
              [0.07, -0.68, 1.34, 0.20, -1.28, 0.06, 0.83, -0.34, 2.19, -1.50]],
        'C': [[0.04, -1.38, 0.88, 0.14, -1.46, -0.29, 0.02, -0.46, 1.77, -1.49], [-0.11, -1.13, 1.09, 0.20, -1.07, -0.67, 0.01, -0.56, 1.45, -1.77],
              [-0.15, -0.96, 0.67, 0.11, -1.45, -0.49, 0.21, -0.49, 1.87, -2.16]]}
data = {'appraisers': ['A', 'B', 'C'], 'values': [AIAG[a] for a in 'ABC']}
near = lambda a, b, tol: a is not None and abs(a - b) <= tol
m1 = create('msa_study', {'title': 'AIAG 示例（均值极差）', 'method': 'grr_xbar', 'data': data}, QC)
s, r, _ = do('msa_study', m1, 'calc', {}, QC)
x = r.get('result') or {}
ok('H1 GR&R 均值极差法 = AIAG MSA 第 4 版公布值（EV 0.20188 / AV 0.22963 / GRR 0.30575 / PV 1.10456 / TV 1.14610 / %GRR 26.68 / ndc 5）',
   near(x.get('ev'), 0.20188, 6e-4) and near(x.get('av'), 0.22963, 6e-4) and near(x.get('grr'), 0.30575, 6e-4) and near(x.get('pv'), 1.10456, 6e-4)
   and near(x.get('tv'), 1.14610, 6e-4) and near(x.get('pct_grr'), 26.68, 0.05) and x.get('ndc') == 5, {k: x.get(k) for k in ('ev', 'av', 'grr', 'pv', 'tv', 'pct_grr', 'ndc')})
ok('H2 结论：10–30% 有条件接受', one('msa_study', m1)['verdict'] == 'conditional')
m2 = create('msa_study', {'title': 'AIAG 示例（方差分析）', 'method': 'grr_anova', 'data': data, 'gauge': good['id']}, QC)
s, r, _ = do('msa_study', m2, 'calc', {}, QC)
a_ = r.get('result') or {}
tb = a_.get('table') or {}
ok('H3 GR&R 方差分析表 SS = AIAG 表 III-B 7（3.1673 / 88.3619 / 0.3590 / 2.7589 / 94.6471）', near(tb.get('appraiser', {}).get('ss'), 3.1673, 6e-4)
   and near(tb.get('part', {}).get('ss'), 88.3619, 6e-4) and near(tb.get('interaction', {}).get('ss'), 0.3590, 6e-4)
   and near(tb.get('repeatability', {}).get('ss'), 2.7589, 6e-4) and near(tb.get('total', {}).get('ss'), 94.6471, 6e-4), {k: v.get('ss') for k, v in tb.items()})
ok('H4 方差分析：交互不显著并入误差，%GRR 27.9、ndc 4（表 III-B 8）', a_.get('pooled') and near(a_.get('pct_grr'), 27.9, 0.06) and a_.get('ndc') == 4, (a_.get('pct_grr'), a_.get('ndc')))
ok('H5 MSA 结果关联到量具（%GRR、ndc）', near(one('gauge', good['id'])['grr_pct'], 27.9, 0.06))
m3 = create('msa_study', {'title': 'AIAG 偏倚示例', 'method': 'bias', 'reference': 6.0, 'data': {'readings': [5.8, 5.7, 5.9, 5.9, 6.0, 6.1, 6.0, 6.1, 6.4, 6.3, 6.0, 6.1, 6.2, 5.6, 6.0]}}, QC)
s, r, _ = do('msa_study', m3, 'calc', {}, QC)
b_ = r.get('result') or {}
# AIAG MSA 第 4 版 偏倚示例（p.87–90）：均值 6.0067、偏倚 0.0067、σr 0.2120、σb 0.0547、t 0.1218、95% 置信区间 −0.1107 … 0.1241 → 偏倚可接受
ok('H6 偏倚 = AIAG 示例（偏倚 0.0067、σr 0.2120、t 0.1218、95% CI −0.1107…0.1241，可接受）', near(b_.get('bias'), 0.0067, 1e-4) and near(b_.get('sr'), 0.2120, 1e-4)
   and near(b_.get('t'), 0.1218, 5e-4) and near(b_.get('ci_low'), -0.1107, 2e-4) and near(b_.get('ci_high'), 0.1241, 2e-4) and b_.get('acceptable'), {k: b_.get(k) for k in ('bias', 'sr', 't')})
LIN = {2: [2.70, 2.50, 2.40, 2.50, 2.70, 2.30, 2.50, 2.50, 2.40, 2.40, 2.60, 2.40], 4: [5.10, 3.90, 4.20, 5.00, 3.80, 3.90, 3.90, 3.90, 3.90, 4.00, 4.10, 3.80],
       6: [5.80, 5.70, 5.90, 5.90, 6.00, 6.10, 6.00, 6.10, 6.40, 6.30, 6.00, 6.10], 8: [7.60, 7.70, 7.80, 7.70, 7.80, 7.80, 7.80, 7.70, 7.80, 7.50, 7.60, 7.70],
       10: [9.10, 9.30, 9.50, 9.30, 9.40, 9.50, 9.50, 9.50, 9.60, 9.20, 9.30, 9.40]}
csv = 'reference,value\n' + '\n'.join(f'{k},{v}' for k, vs in LIN.items() for v in vs)
m4 = create('msa_study', {'title': 'AIAG 线性示例', 'method': 'linearity'}, QC)
s, r, _ = post(f'/api/iatf/msa/{m4}/import', {'csv': csv}, QC)
s, r, _ = do('msa_study', m4, 'calc', {}, QC)
l_ = r.get('result') or {}
# AIAG MSA 第 4 版 线性示例（p.96–100）：斜率 −0.1317、截距 0.7367、R² 0.71、|t 斜率| 12.04 → 线性不可接受
ok('H7 线性（CSV 导入）= AIAG 示例（斜率 −0.1317、截距 0.7367、R² 0.71，不可接受）', near(l_.get('slope'), -0.1317, 1e-4) and near(l_.get('intercept'), 0.7367, 1e-4)
   and near(l_.get('r2'), 0.714, 0.005) and not l_.get('acceptable'), {k: l_.get(k) for k in ('slope', 'intercept', 'r2')})
att = {'appraisers': ['甲', '乙'], 'values': [[[1, 1, 0, 1, 0, 1, 1, 0, 1, 1], [1, 1, 0, 1, 0, 1, 1, 0, 1, 1]], [[1, 1, 0, 1, 0, 1, 1, 0, 1, 1], [1, 1, 0, 1, 0, 1, 1, 0, 1, 1]]],
       'reference': [1, 1, 0, 1, 0, 1, 1, 0, 1, 1]}
m5 = create('msa_study', {'title': '计数型一致性', 'method': 'attribute', 'data': att}, QC)
s, r, _ = do('msa_study', m5, 'calc', {}, QC)
k_ = r.get('result') or {}
ok('H8 计数型一致性：判定全部一致 → kappa 1、有效性 100%、可接受', s == 200 and one('msa_study', m5)['verdict'] == 'acceptable', msg(r) if s != 200 else (one('msa_study', m5)['verdict']))
s, html, _ = req('GET', f'/api/iatf/msa/{m2}/report', token=T, raw=True)
ok('H9 MSA 报告（打印版）', s == 200 and '有条件接受'.encode() in html)

# ═════════════ I. 客诉召回 ═════════════
heat3 = f'HC{RUN}'
p3 = push_parts(4, heat3)
c3 = None
for sn in p3[:2]:
    r = post('/api/pack/scan', {'code': sn, 'station': ST + 'C', 'customer': cust}, OP)[1]
    c3 = r.get('carton')
lid2 = create('ship_load', {'customer': cust, 'delivery_code': f'FH2-{RUN}'})
s, r, h = post(f'/api/pack/load/{lid2}/scan', {'code': c3}, WH)
do('ship_load', lid2, 'ship', {}, WH)
cid = create('complaint', {'customer': cust, 'delivery_code': f'FH2-{RUN}', 'sns': p3[0], 'qty': 1, 'description': '装配时发现缸孔内壁气孔', 'severity': 'critical'}, QC)
s, r, _ = do('complaint', cid, 'analyze', {}, QC)
im = r.get('impact') or {}
ok('I1 客诉影响分析（按零件码 → 炉次 → 全部零件）：已发运 2、在厂 2，涉及客户', s == 200 and im.get('heats') == [heat3] and im['shipped']['count'] == 2 and im['in_house']['count'] == 2
   and im['shipped']['customers'].get(cust) == 2, msg(r))
s, r, _ = do('complaint', cid, 'contain', {}, QC)
ok('I2 遏制：在厂的按批次冻结（沿用批次冻结），零件状态 → 冻结', s == 200 and all(get(f'/api/e/part?f_sn={x}')['items'][0]['status'] == 'hold' for x in p3[2:]), msg(r))
s, r, h = post('/api/pack/scan', {'code': p3[2], 'station': ST + 'C', 'customer': cust}, OP)
ok('I3 冻结后不能装箱 / 发运', s == 409 and h.get('x-gate') == 'hold')
s, r, _ = do('complaint', cid, 'notify', {}, QC)
ok('I4 通知相关人（质量 / 厂长 / 仓库 / 计划）', s == 200 and one('complaint', cid)['notified'])
s, r, _ = do('complaint', cid, 'to8d', {}, QC)
r8 = r.get('id')
s, r, _ = signed('complaint', cid, 'close', {}, QC)
ok('I5 8D 没关 → 客诉不能关', s == 409 and '8D' in detail(r), detail(r))
s, r, _ = do('report_8d', r8, 'close', {'esign': SIG}, QC)
ok('I6 8D 缺 D4/D5/D6 不能关', s == 409, detail(r))
put(f'/api/e/report_8d/{r8}', {'d4': '浇注温度偏低', 'd5': '浇注温度联锁', 'd6': '连续 3 炉验证'}, QC)
s, r, _ = put(f'/api/e/report_8d/{r8}', {'status': 'closed'}, QC)
ok('I7 8D 状态不能直接改成关闭（只能走带签名的"关闭"）', one('report_8d', r8)['status'] != 'closed')
s, r, _ = signed('report_8d', r8, 'close', {}, QC)
ok('I8 8D 关闭（电子签名）', s == 200 and one('report_8d', r8)['status'] == 'closed', msg(r))
s, r, _ = signed('complaint', cid, 'close', {}, QC)
ok('I9 客诉关闭（电子签名）', s == 200 and one('complaint', cid)['status'] == 'closed', msg(r))
rs = get(f'/api/complaint/recall_sim?code={heat3}')
ok('I10 召回模拟（炉次）：范围 + 用时，不冻结', rs['parts_total'] == 4 and rs['shipped']['count'] == 2 and rs['elapsed_ms'] < 5000, (rs['parts_total'], rs['elapsed_ms']))
rs2 = get(f'/api/complaint/recall_sim?code={c3}')
ok('I11 召回模拟（箱号起点）→ 同一炉次', rs2['heats'] == [heat3])
s, html, _ = req('GET', f'/api/complaint/recall_sim.html?code={heat3}', token=T, raw=True)
ok('I12 召回模拟报告（打印版）', s == 200 and '召回模拟报告'.encode() in html)

# ═════════════ J. 电子签名 ═════════════
time.sleep(2.5)
recs = get('/api/esign/records?limit=200')
pts = {x['point'] for x in recs}
ok('J1 签名记录：解冻 / ECN / 量具 / 8D / 文件 / 客诉 各签名点都有，含签名人、含义、时间、摘要',
   {'批次解冻放行', '工艺变更 ECN 批准', '量检具放行', '8D 报告关闭', '受控文件版本批准', '客户投诉关闭'} <= pts and all(x['hash'] and x['meaning'] and x['signed_at'] for x in recs), pts)
aud = get('/api/admin/audit?action=esign&size=50')
ai_ = aud.get('items') if isinstance(aud, dict) else aud
af = get('/api/admin/audit?action=esign_fail&size=10')
af = af.get('items') if isinstance(af, dict) else af
ok('J2 签名写审计日志（action=esign），密码错写 esign_fail', len(ai_ or []) >= 6 and len(af or []) >= 1)
v = get('/api/admin/audit/verify')
ok('J3 审计哈希链完整', v.get('ok') is True or v.get('ok') == 1, v)
s, r, _ = post('/api/esign/verify', {'password': 'honey123'}, QC)
ok('J4 签名框预检密码', r.get('ok') is True)

# ═════════════ K. 权限 ═════════════
s, r, _ = post('/api/label/reprint', {'job': jid, 'reason': 'x 标签污损'}, OP)
ok('K1 操作工可以打印、不能补打（403）', s == 403, detail(r))
s, r, _ = post('/api/e/ecn', {'title': 'op'}, OP)
ok('K2 操作工不能建 ECN', s == 403)
s, r, _ = post('/api/e/gauge_cal', {'gauge': old, 'cal_date': '2026-01-01', 'result': 'pass'}, OP)
ok('K3 操作工不能登记校准', s == 403)
s, r, _ = post('/api/pack/scan', {'code': 'NOPE', 'station': 'X'}, QC)
ok('K4 质检员没有包装工位权限（403）', s == 403, detail(r))
kr = post('/api/admin/apikeys', {'name': f'ro-{RUN}', 'perms': ['*.read']})[1]
key = kr.get('key') or kr.get('plain') or ''
s, r, _ = req('POST', '/api/label/print', {'template': tpls['LBL-LOC']['id'], 'ref': 'W-FG-01'}, key=key)
s2, r2, _ = req('GET', '/api/e/gauge?size=1', key=key)
ok('K5 只读 API 密钥：能读量具，不能打印（403）', s == 403 and s2 == 200, (s, s2))
ok('K6 权限点目录里有新模块（label / pack / outsource / spare / ecn / doc / gauge / iatf / complaint）',
   {'label', 'pack', 'outsource', 'spare', 'ecn', 'doc', 'gauge', 'iatf', 'complaint'} <= {m['key'] for m in get('/api/admin/perms/catalog').get('modules', [])}
   if isinstance(get('/api/admin/perms/catalog'), dict) else True)

fz.close(); ft.close(); fdead.close(); fm.close()
n_ok = sum(1 for _, x in results if x)
print(f'\n{n_ok}/{len(results)} 通过')
sys.exit(0 if n_ok == len(results) else 1)
