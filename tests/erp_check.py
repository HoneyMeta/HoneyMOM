"""5.0 内置 ERP 与外部 ERP 集成检查（自己在进程里起 MES 和模拟服务，不需要另外启动）：

  B 销售：价格表取价、订单 + 交货计划、确认 / 信用待批 / 特批、变更历史、报价转订单、发货（扣库存 / 发运 / 订单状态 / 超发 / 缺货 / 按单生产闸门）、退货、对账单、订单看板
  C 采购：合格供方、请购 → 采购订单、收货 → 来料检验（现有检验模块）→ 入库 / 退供 / 不合格品、免检、供应商绩效
  D MRP：多层 BOM 净算、批量、提前期、pegging 追到订单、提前建议、调整、下达成工单 / 采购订单、粗能力
  E 成本：移动平均、工单实际成本（料工机能费）、标准成本卷积、差异、月末结账锁、冻结
  F 应收应付：开票（剩余可开）、收付款自动核销、账龄 = 余额、信用占用
  G 凭证：生成借贷平衡、幂等、金蝶 / 用友 U8 / YonSuite / Excel / CSV 导出、格式可配置
  H 外部 ERP 模式：界面写入拦截、REST v1（API 密钥、幂等、变更历史、只读密钥 403）、事件拉取、Webhook 签名 + 重试 + dead、完工回传
  I 中间库：单独的库、进 / 出、错误与重试、幂等跳过
  J 对接器：金蝶云星空 / 用友 U8 / YonSuite 模拟服务——认证、拉主数据和订单、回推发货 / 完工 / 库存、失败重试
  K 权限与数据范围：各角色、按工厂的范围（界面接口 + API 密钥）
  L AI：开关、解释 MRP、订单风险、起草采购订单 → 审批 → 建单

用法：
  python tests/erp_check.py                        SQLite（临时目录）
  python tests/erp_check.py --db pg                PostgreSQL（环境变量 FC_PG=postgresql+psycopg://用户:密码@主机:端口，用户要能建库）
"""
import argparse
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R: list = []


def check(name, ok, detail=''):
    R.append(bool(ok))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  （{detail}）' if detail not in ('', None) else ''), flush=True)
    return ok


def section(t):
    print(f'\n===== {t}', flush=True)


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def serve(app):
    import uvicorn
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        time.sleep(0.1)
        if srv.started:
            break
    return f'http://127.0.0.1:{port}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='sqlite', choices=['sqlite', 'pg'])
    ap.add_argument('--dir', default=None)
    a = ap.parse_args()
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding='utf-8')
    work = a.dir or tempfile.mkdtemp(prefix='hm_erp_')
    shutil.rmtree(work, ignore_errors=True)
    data = os.path.join(work, 'data')
    os.makedirs(data)
    os.environ['HM_DATA'] = data
    os.environ['ERP_LOOP'] = '0'                  # 后台投递关掉：测试里手动触发，结果可预期
    os.environ['MES_BACKUP_DIR'] = os.path.join(work, 'backups')
    os.environ['HM_AI_FIXTURES'] = os.path.join(work, 'ai_fixtures')
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, 'tests'))
    import sqlalchemy as sa
    if a.db == 'sqlite':
        url = 'sqlite:///' + os.path.join(data, 'mes.db').replace('\\', '/')
    else:
        base = (os.environ.get('FC_PG') or '').rstrip('/')
        if not base:
            print('需要环境变量 FC_PG=postgresql+psycopg://用户:密码@主机:端口')
            sys.exit(2)
        dbname = f'erp_check_{os.getpid()}'
        adm = sa.create_engine((os.environ.get('FC_PG_ADMIN') or base).rstrip('/') + '/postgres', isolation_level='AUTOCOMMIT')
        with adm.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}"'))
            c.execute(sa.text(f'CREATE DATABASE "{dbname}" OWNER "{sa.engine.make_url(base + "/x").username}"'))
        url = f'{base}/{dbname}'
    print(f'数据库：{a.db}  目录：{work}', flush=True)
    t_all = time.time()
    from honeymom import auth, db, migrations, seed
    db.init(url)
    seed.run()
    check('迁移后结构与 schema.py 一致', migrations.check(db.engine) == [])
    from honeymom.app import build_app
    from honeymom.schema import ERP_TABLES, TABLES
    from honeymom.services.warehouse import move
    from honeymom.erp import core, integration, voucher
    base_url = serve(build_app())
    from mocks import kingdee_mock, webhook_sink, yonyou_mock
    KD, YY, SINK = serve(kingdee_mock.build()), serve(yonyou_mock.build()), serve(webhook_sink.build())

    def http(method, path, body=None, token=None, key=None, raw=False, base=None):
        h = {'Content-Type': 'application/json'}
        if token:
            h['Authorization'] = 'Bearer ' + token
        if key:
            h['X-API-Key'] = key
        req = urllib.request.Request((base or base_url) + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                b = r.read()
                if raw:
                    return r.status, b, dict(r.headers)
                return r.status, (json.loads(b) if b[:1] in (b'{', b'[') else b)
        except urllib.error.HTTPError as ex:
            b = ex.read()
            try:
                j = json.loads(b)
            except ValueError:
                j = {'detail': b.decode('utf-8', 'replace')}
            if raw:
                return ex.code, j, dict(ex.headers)
            return ex.code, j

    def login(u):
        return http('POST', '/api/login', {'username': u, 'password': 'honey123'})[1]['token']

    def q1(sql, **kw):
        with db.engine.connect() as c:
            return c.execute(sa.text(sql), kw).first()

    def get(entity, **kw):
        with db.engine.connect() as c:
            return db.get(c, entity, **kw)

    def rows(entity, *conds, **kw):
        with db.engine.connect() as c:
            return db.rows(c, entity, *conds, **kw)

    def stock(code, wh=None):
        with db.engine.connect() as c:
            m = db.get(c, 'material', code=code)
            return core.stock_qty(c, m['id'], wh)

    def act(entity, rid, action, body=None, tok=None):
        return http('POST', f'/api/e/{entity}/{rid}/do/{action}', body or {}, tok or ADMIN)

    today = date.today()
    with db.engine.begin() as c:
        for u, roles in (('sales1', 'sales'), ('buyer1', 'buyer'), ('fin1', 'finance'), ('sales2', 'sales')):
            db.insert(c, 'sys_user', {'username': u, 'name': u, 'roles': roles, 'active': True, 'password_hash': auth.hash_pw('honey123')}, raw=True)
        mats = {m['code']: m for m in db.rows(c, 'material')}
        custs = {x['short_name']: x for x in db.rows(c, 'customer')}
        sups = {x['code']: x for x in db.rows(c, 'supplier')}
        whs = {w['wtype']: w['id'] for w in db.rows(c, 'warehouse')}
        move(c, 'in', mats['SGH-450']['id'], 500, whs['fg'], who='测试', reason='期初库存')
        plant1 = db.rows(c, 'plant')[0]['id']
    ADMIN, SALES, BUYER, FIN, PLAN, OPER, WH = (login(u) for u in ('admin', 'sales1', 'buyer1', 'fin1', 'planner', 'operator', 'wh'))
    FG = mats['SGH-450']['id']

    # ───────────────────────── B 销售 ─────────────────────────
    section('B 销售')
    p1 = http('GET', f"/api/sales/price?customer={custs['北辰重卡']['id']}&material={FG}&qty=100", token=SALES)[1]
    p2 = http('GET', f"/api/sales/price?customer={custs['北辰重卡']['id']}&material={FG}&qty=1200", token=SALES)[1]
    p3 = http('GET', f"/api/sales/price?customer={custs['西岭重卡']['id']}&material={FG}&qty=100", token=SALES)[1]
    check('价格：客户专用价格表 / 起订量阶梯 / 通用价格表', (p1['price'], p2['price'], p3['price']) == (375, 368, 380), f"{p1['price']} {p2['price']} {p3['price']}")
    st, so = http('POST', '/api/erp/doc/sales_order', {'customer': custs['西岭重卡']['id'], 'customer_po': 'SX-T1',
                                                       'lines': [{'material': FG, 'qty': 300, 'schedule': [{'due': str(today + timedelta(days=5)), 'qty': 150},
                                                                                                         {'due': str(today + timedelta(days=12)), 'qty': 150}]}]}, SALES)
    SO1 = so.get('id')
    so1 = get('sales_order', id=SO1) if SO1 else {}
    check('销售订单：建草稿、单价自动取价、价税合计', st == 200 and so1['amount'] == 114000 and so1['total'] == 128820 and so1['status'] == 'draft', f"{st} {so1.get('total')}")
    sch = rows('so_schedule', TABLES['so_schedule'].c.sales_order == SO1)
    check('交货计划：两行 150 + 150', sorted(x['qty'] for x in sch) == [150, 150])
    st, r = act('sales_order', SO1, 'confirm', tok=SALES)
    check('确认订单：信用检查通过', st == 200 and r.get('status') == 'confirmed', r.get('msg'))
    ln1 = rows('so_line', TABLES['so_line'].c.sales_order == SO1)[0]
    st, r = http('POST', f'/api/sales/orders/{SO1}/change', {'reason': '客户加量', 'lines': [{'id': ln1['id'], 'qty': 320}]}, SALES)
    sch = sorted(rows('so_schedule', TABLES['so_schedule'].c.sales_order == SO1), key=lambda x: x['due'])
    chg = rows('so_change', TABLES['so_change'].c.sales_order == SO1)
    check('订单变更：版本 + 1、记变更历史、交货计划差额加到最后一行', st == 200 and r['version'] == 2 and any(x['kind'] == 'change' and x['reason'] == '客户加量' for x in chg)
          and [x['qty'] for x in sch] == [150, 170], f"{st} {r} {[x['qty'] for x in sch]}")
    st, r = http('POST', f'/api/sales/orders/{SO1}/change', {'reason': '', 'lines': []}, SALES)
    check('变更必须写原因', st == 400)
    st, so3 = http('POST', '/api/erp/doc/sales_order', {'customer': custs['长河商用车']['id'], 'lines': [{'material': FG, 'qty': 2000}]}, SALES)
    SO3 = so3['id']
    st, r = act('sales_order', SO3, 'confirm', tok=SALES)
    check('超信用额度 → 信用待批（不报错，说明原因）', st == 200 and r.get('status') == 'credit_hold' and get('sales_order', id=SO3)['status'] == 'credit_hold', r.get('msg'))
    st, r = act('sales_order', SO3, 'approve_credit', {'reason': ''})
    check('信用特批要写理由', st == 400)
    st, r = act('sales_order', SO3, 'approve_credit', {'reason': '老客户，回款稳定'}, WH)
    check('仓管员不能信用特批（403）', st == 403)
    st, r = act('sales_order', SO3, 'approve_credit', {'reason': '老客户，回款稳定'})
    check('厂长 / 管理员特批 → 已确认', st == 200 and get('sales_order', id=SO3)['status'] == 'confirmed')
    q = rows('quotation')[0]
    st, r = act('quotation', q['id'], 'accept', {'customer_po': 'DF-Q1'}, SALES)
    qso = get('sales_order', id=r.get('id')) if r.get('id') else {}
    check('报价成交 → 销售订单（来源 = 报价，带报价单价）', st == 200 and qso.get('source') == 'quotation' and get('quotation', id=q['id'])['status'] == 'accepted', r.get('msg'))
    fg0 = stock('SGH-450', whs['fg'])
    st, r = http('POST', f'/api/sales/orders/{SO1}/delivery', {'lines': {str(ln1['id']): 100}}, SALES)
    D1 = r.get('id')
    st, r = act('delivery_order', D1, 'post', tok=WH)
    d1 = get('delivery_order', id=D1)
    ln1 = get('so_line', id=ln1['id'])
    sch = sorted(rows('so_schedule', TABLES['so_schedule'].c.sales_order == SO1), key=lambda x: x['due'])
    txn = rows('stock_txn', TABLES['stock_txn'].c.ref_doc == d1['code'])
    shp = rows('shipment')
    check('发货过账：扣库存、出入库记录"销售发货"、记发运', st == 200 and abs(stock('SGH-450', whs['fg']) - (fg0 - 100)) < 1e-6 and txn and txn[0]['ttype'] == 'out'
          and shp and shp[-1]['qty'] == 100, f"{st} {r.get('msg') or r.get('detail')}")
    check('发货过账：订单已发、交货计划按交期分配、订单"部分发货"、发货成本', ln1['qty_shipped'] == 100 and sch[0]['qty_shipped'] == 100 and sch[1]['qty_shipped'] == 0
          and get('sales_order', id=SO1)['status'] == 'partial' and d1['cost'] > 0, f"成本 {d1['cost']}")
    st, r = http('POST', '/api/erp/doc/delivery_order', {'sales_order': SO1, 'lines': [{'so_line': ln1['id'], 'qty': 500}]}, SALES)
    if st != 200:
        print('  建发货单失败：', st, r)
    st2, r2, h2 = http('POST', f"/api/e/delivery_order/{r['id']}/do/post", {}, ADMIN, raw=True)
    check('超发被拦（409 + X-Gate: over_ship）', st2 == 409 and {k.lower(): v for k, v in h2.items()}.get('x-gate') == 'over_ship', r2.get('detail'))
    act('delivery_order', r['id'], 'cancel')
    st, r = http('POST', f'/api/sales/orders/{SO3}/delivery', {}, SALES)
    st2, r2, h2 = http('POST', f"/api/e/delivery_order/{r['id']}/do/post", {}, ADMIN, raw=True)
    check('库存不足被拦（409 + X-Gate: stock）', st2 == 409 and {k.lower(): v for k, v in h2.items()}.get('x-gate') == 'stock', r2.get('detail'))
    act('delivery_order', r['id'], 'cancel')
    mto = rows('sales_order', TABLES['sales_order'].c.mto.is_(True))[0]
    st, r = http('POST', f"/api/sales/orders/{mto['id']}/delivery", {'lines': {str(rows('so_line', TABLES['so_line'].c.sales_order == mto['id'])[0]['id']): 10}}, SALES)
    st2, r2, h2 = http('POST', f"/api/e/delivery_order/{r['id']}/do/post", {}, ADMIN, raw=True)
    check('按单生产：工单没完工不能发（409 + X-Gate: mto）', st2 == 409 and {k.lower(): v for k, v in h2.items()}.get('x-gate') == 'mto', r2.get('detail'))
    act('delivery_order', r['id'], 'cancel')
    dl1 = rows('delivery_line', TABLES['delivery_line'].c.delivery_order == D1)[0]
    st, r = http('POST', '/api/erp/doc/sales_return', {'delivery_order': D1, 'disposition': 'restock', 'reason': '包装破损',
                                                       'lines': [{'delivery_line': dl1['id'], 'qty': 10}]}, SALES)
    RT1 = r.get('id')
    s0 = stock('SGH-450', whs['fg'])
    st, r = act('sales_return', RT1, 'receive', tok=WH)
    check('退货收货（退回入库）：库存 + 10、订单"已退"、按原发货成本', st == 200 and abs(stock('SGH-450', whs['fg']) - s0 - 10) < 1e-6
          and get('so_line', id=ln1['id'])['qty_returned'] == 10 and get('return_line', sales_return=RT1)['unit_cost'] == dl1['unit_cost'] if get('return_line', sales_return=RT1) else False,
          r.get('msg') or r.get('detail'))
    st, r = http('POST', '/api/erp/doc/sales_return', {'delivery_order': D1, 'disposition': 'scrap', 'lines': [{'delivery_line': dl1['id'], 'qty': 95}]}, SALES)
    st2, r2 = act('sales_return', r['id'], 'receive', tok=WH)
    check('退货超过可退数量被拦', st2 == 409, r2.get('detail'))
    period = core.period_of(today)
    st, r = http('POST', '/api/sales/statements', {'period': period, 'customer': custs['西岭重卡']['id']}, ADMIN)
    stt = get('customer_statement', id=r['ids'][0])
    rt1 = get('sales_return', id=RT1)
    check('客户对账单：期初 + 发货 − 退货 − 收款 = 期末', st == 200 and abs(stt['shipped'] - d1['total']) < 0.01 and abs(stt['returned'] - rt1['total']) < 0.01
          and abs(stt['closing'] - (stt['opening'] + stt['shipped'] - stt['returned'] - stt['received'])) < 0.01, f"{stt['shipped']} {stt['returned']} {stt['closing']}")
    st, r = act('customer_statement', stt['id'], 'confirm', {'by': '西岭 财务 刘'}, ADMIN)
    st2, r2 = http('POST', '/api/sales/statements', {'period': period, 'customer': custs['西岭重卡']['id']}, ADMIN)
    check('对账单客户确认后不能重新生成', st == 200 and st2 == 409)
    # 看板：一张过期的订单
    st, r = http('POST', '/api/erp/doc/sales_order', {'customer': custs['北辰重卡']['id'], 'order_date': str(today - timedelta(days=10)),
                                                      'lines': [{'material': FG, 'qty': 20, 'due': str(today - timedelta(days=2))}]}, SALES)
    LATE = r['id']
    act('sales_order', LATE, 'confirm', tok=SALES)
    st, b = http('GET', '/api/sales/board', token=SALES)
    late_row = [x for x in b.get('at_risk', []) if x['sales_order'] == LATE]
    check('订单看板：未交金额、过期订单标"已逾期"', st == 200 and b['backlog_value'] > 0 and late_row and late_row[0]['risk'] == 'overdue', f"风险 {b.get('risk_count')}")
    st, r = http('POST', f'/api/sales/orders/{LATE}/delivery', {}, SALES)
    act('delivery_order', r['id'], 'post', tok=WH)
    st, b = http('GET', '/api/sales/board', token=SALES)
    check('过期后发货 → 准时交付率 < 100%（逾期数量计入）', b['late_qty'] >= 20 and b['otd'] is not None and b['otd'] < 1, f"OTD {b['otd']} 逾期 {b['late_qty']}")

    # ───────────────────────── C 采购 ─────────────────────────
    section('C 采购')
    st, r = http('POST', '/api/erp/doc/purchase_order', {'supplier': sups['S005']['id'], 'lines': [{'material': mats['CM-PIG']['id'], 'qty': 20}]}, BUYER)
    check('不在合格供方清单（ASL）的物料不能下单（409）', st == 409, r.get('detail'))
    st, r = http('POST', '/api/e/purchase_req', {'material': mats['CM-STEEL']['id'], 'qty': 30, 'need_date': str(today + timedelta(days=7)), 'reason': '补库'}, PLAN)
    REQ = r.get('id')
    st, r = act('purchase_req', REQ, 'approve', tok=BUYER)
    st, r = http('POST', '/api/purchase/req_to_po', {'ids': [REQ]}, BUYER)
    PO1 = r['orders'][0]['id'] if st == 200 else None
    po1 = get('purchase_order', id=PO1) if PO1 else {}
    pl1 = rows('po_line', TABLES['po_line'].c.purchase_order == PO1)[0] if PO1 else {}
    check('请购批准 → 合并成采购订单（供应商 = 首选合格供方，单价 = 供货清单）', st == 200 and po1['supplier'] == sups['S002']['id'] and pl1['price'] == 2600
          and get('purchase_req', id=REQ)['status'] == 'ordered', f"{st} {r}")
    act('purchase_order', PO1, 'approve', tok=BUYER)
    st, r = act('purchase_order', PO1, 'send', tok=BUYER)
    check('采购订单审批、下发', st == 200 and get('purchase_order', id=PO1)['status'] == 'sent')
    st, r = http('POST', f'/api/purchase/orders/{PO1}/receipt', {'lot': 'L-STEEL-1'}, WH)
    RC1 = r.get('id')
    st, r = act('purchase_receipt', RC1, 'post', tok=WH)
    rl1 = rows('receipt_line', TABLES['receipt_line'].c.purchase_receipt == RC1)[0]
    ins = get('insp_record', id=rl1['insp_record']) if rl1.get('insp_record') else {}
    check('收货送检：开来料检验单（IQC，待判）、订单行已收 30、收货单"来料检验中"', st == 200 and ins.get('itype') == 'IQC' and ins.get('result') == 'pending'
          and get('po_line', id=pl1['id'])['qty_received'] == 30 and get('purchase_receipt', id=RC1)['status'] == 'inspecting', r.get('msg'))
    st, r, h = http('POST', f'/api/e/purchase_receipt/{RC1}/do/settle', {}, WH, raw=True)
    check('检验没判定不能入库（409 + X-Gate: iqc_pending）', st == 409 and {k.lower(): v for k, v in h.items()}.get('x-gate') == 'iqc_pending')
    st, _ = http('PUT', f"/api/e/insp_record/{ins['id']}", {'result': 'NG', 'qty_sample': 5, 'qty_ng': 1}, login('qc'))
    s0 = stock('CM-STEEL', whs['raw'])
    st, r = act('purchase_receipt', RC1, 'settle', {'lines': {str(rl1['id']): {'accepted': 25, 'rejected': 5}}}, WH)
    rl1 = get('receipt_line', id=rl1['id'])
    ret = rows('supplier_return', TABLES['supplier_return'].c.purchase_receipt == RC1)
    ncr = [x for x in rows('ncr') if x.get('disposition') == 'return']
    check('按检验结果入库（挑选：合格 25 / 不合格 5）：库存 + 25、退供单 5、不合格品单（退供应商）', st == 200 and abs(stock('CM-STEEL', whs['raw']) - s0 - 25) < 1e-6
          and rl1['qty_accepted'] == 25 and ret and ret[0]['kind'] == 'iqc' and ncr, r.get('msg') or r.get('detail'))
    rc1 = get('purchase_receipt', id=RC1)
    check('收货金额按合格数、订单行合格 / 不合格数', rc1['amount'] == 25 * 2600 and get('po_line', id=pl1['id'])['qty_rejected'] == 5 and rc1['status'] == 'done')
    st, r = act('supplier_return', ret[0]['id'], 'ship', tok=WH)
    check('来料不合格退货出库（不扣库存）', st == 200 and abs(stock('CM-STEEL', whs['raw']) - s0 - 25) < 1e-6)
    po_seed = rows('purchase_order', TABLES['purchase_order'].c.supplier == sups['S001']['id'])[0]
    st, r = http('POST', f"/api/purchase/orders/{po_seed['id']}/receipt", {}, WH)
    RC2 = r['id']
    act('purchase_receipt', RC2, 'post', tok=WH)
    i2 = rows('receipt_line', TABLES['receipt_line'].c.purchase_receipt == RC2)[0]['insp_record']
    http('PUT', f'/api/e/insp_record/{i2}', {'result': 'OK'}, login('qc'))
    st, r = act('purchase_receipt', RC2, 'settle', tok=WH)
    check('检验合格 → 全部入库、订单"已到齐"', st == 200 and get('purchase_order', id=po_seed['id'])['status'] == 'received', r.get('msg'))
    st, r = http('POST', '/api/erp/doc/purchase_order', {'supplier': sups['S004']['id'], 'lines': [{'material': mats['AUX-SAND']['id'], 'qty': 40}]}, BUYER)
    PO3 = r['id']
    act('purchase_order', PO3, 'approve', tok=BUYER)
    st, r = http('POST', f'/api/purchase/orders/{PO3}/receipt', {}, WH)
    s0 = stock('AUX-SAND')
    st, r = act('purchase_receipt', r['id'], 'post', tok=WH)
    check('免检物料：收货直接入库', st == 200 and abs(stock('AUX-SAND') - s0 - 40) < 1e-6, r.get('msg'))
    with db.engine.begin() as c:
        db.update(c, 'supplier', sups['S004']['id'], {'qual': 'blocked'}, raw=True)
    st, r = http('POST', '/api/erp/doc/purchase_order', {'supplier': sups['S004']['id'], 'lines': [{'material': mats['AUX-SAND']['id'], 'qty': 40}]}, BUYER)
    check('停用的供应商不能下单', st == 409, r.get('detail'))
    st, perf = http('GET', '/api/purchase/performance?save=1', token=ADMIN)
    s2 = next((x for x in perf if x['code'] == 'S002'), {})
    check('供应商绩效：来料 ppm = 不合格 / 到货 × 100 万、准时率、得分写回', s2.get('ppm') == round(5 / 30 * 1e6) and s2.get('otd') == 1.0
          and get('supplier', id=sups['S002']['id'])['rating'] == s2['score'], f"{s2}")

    # ───────────────────────── D MRP ─────────────────────────
    section('D MRP')
    with db.engine.begin() as c:
        ids = {}
        for code, name, mt, unit in (('T-FG', '测试成品', 'fg', '件'), ('T-SF', '测试半成品', 'semi', '件'), ('T-RM', '测试原料', 'raw', 'kg')):
            ids[code] = db.insert(c, 'material', {'code': code, 'name': name, 'mtype': mt, 'unit': unit, 'price': 10}, raw=True)
        db.insert(c, 'bom', {'product': ids['T-FG'], 'component': ids['T-SF'], 'qty': 2}, raw=True)
        db.insert(c, 'bom', {'product': ids['T-SF'], 'component': ids['T-RM'], 'qty': 1.5}, raw=True)
        db.insert(c, 'mat_plan', {'material': ids['T-FG'], 'make_buy': 'make', 'lead_days': 2}, raw=True)
        db.insert(c, 'mat_plan', {'material': ids['T-SF'], 'make_buy': 'make', 'lead_days': 3, 'min_lot': 100}, raw=True)
        db.insert(c, 'mat_plan', {'material': ids['T-RM'], 'make_buy': 'buy', 'lead_days': 5, 'lot_multiple': 50, 'supplier': sups['S001']['id']}, raw=True)
        db.insert(c, 'supplier_material', {'supplier': sups['S001']['id'], 'material': ids['T-RM'], 'price': 12, 'tax_rate': 13, 'lead_days': 5, 'approved': True,
                                           'preferred': True, 'iqc_exempt': True}, raw=True)
        move(c, 'in', ids['T-FG'], 20, whs['fg'], who='测试', reason='期初库存')
        move(c, 'in', ids['T-SF'], 30, whs['blank'], who='测试', reason='期初库存')
    st, r = http('POST', '/api/erp/doc/purchase_order', {'supplier': sups['S001']['id'], 'lines': [{'material': ids['T-RM'], 'qty': 40, 'due': str(today + timedelta(days=20))}]},
                 BUYER)
    PO_RM = r['id']
    act('purchase_order', PO_RM, 'approve', tok=BUYER)
    st, r = http('POST', '/api/erp/doc/sales_order', {'customer': custs['北辰重卡']['id'], 'lines': [{'material': ids['T-FG'], 'qty': 100, 'due': str(today + timedelta(days=15))}]},
                 SALES)
    SO_T = r['id']
    act('sales_order', SO_T, 'confirm', tok=SALES)
    so_t_code = get('sales_order', id=SO_T)['code']
    st, r = http('POST', '/api/mrp/run', {'horizon': 60}, OPER)
    check('操作工不能运行 MRP（403）', st == 403)
    st, r = http('POST', '/api/mrp/run', {'horizon': 60}, PLAN)
    check('计划员运行 MRP', st == 200 and r['count'] > 0, r.get('count'))
    st, lt = http('GET', '/api/mrp/latest', token=PLAN)
    S = {}
    for x in lt['suggestions']:
        if x['material'] in ids.values():
            S.setdefault((x['material'], x['kind']), x)
    fgm, sfm, rmb = S.get((ids['T-FG'], 'make')), S.get((ids['T-SF'], 'make')), S.get((ids['T-RM'], 'buy'))
    exp = S.get((ids['T-RM'], 'expedite'))
    check('成品：净需求 100 − 库存 20 = 80，需求日 +15，下达日 = 需求日 − 提前期 2', fgm and fgm['qty'] == 80 and fgm['need_date'] == str(today + timedelta(days=15))
          and fgm['release_date'] == str(today + timedelta(days=13)), fgm and (fgm['qty'], fgm['need_date'], fgm['release_date']))
    check('半成品（第 2 层）：80 × 2 − 30 = 130（≥ 最小批量 100），下达日再往前 3 天', sfm and sfm['qty'] == 130 and sfm['level'] == 1
          and sfm['release_date'] == str(today + timedelta(days=10)), sfm and (sfm['qty'], sfm['release_date']))
    check('原料（第 3 层）：130 × 1.5 − 在途 40 = 155 → 批量倍数 50 → 200，供应商 S001', rmb and rmb['qty'] == 200 and rmb['supplier'] == sups['S001']['id']
          and rmb['level'] == 2 and rmb['release_date'] == str(today + timedelta(days=5)), rmb and (rmb['qty'], rmb['supplier'], rmb['release_date']))
    tops = [p for p in (rmb or {}).get('pegging') or [] if p.get('kind') == 'top']
    check('pegging：原料建议一直追到销售订单', any(p['ref'] == so_t_code for p in tops), [p.get('ref') for p in tops])
    check('提前建议：在途采购订单 +20 天才到，需求在 +10 天', exp and exp['need_date'] == str(today + timedelta(days=10)), exp and exp['reason'])
    run = lt['run']
    caps = (run.get('stats') or {}).get('capacity') or []
    check('粗能力：有工位组 × 周的负荷表（已排产 + 计划 vs 产能）', len(caps) > 0 and all('capacity_h' in x for x in caps), len(caps))
    st, r = http('PUT', f"/api/mrp/suggestions/{sfm['id']}", {'qty': 150}, PLAN)
    st, r = http('POST', '/api/mrp/release', {'ids': [fgm['id'], sfm['id'], rmb['id'], exp['id']], 'buy_as': 'po'}, PLAN)
    sf2, rm2 = get('mrp_suggestion', id=sfm['id']), get('mrp_suggestion', id=rmb['id'])
    wo_sf = get('work_order', id=sf2['work_order']) if sf2.get('work_order') else {}
    check('下达：计划生产 → 草稿工单（用调整后的数量 150）', st == 200 and len(r['work_orders']) == 2 and wo_sf.get('qty') == 150 and wo_sf.get('status') == 'draft', r)
    po_new = get('purchase_order', id=rm2['purchase_order']) if rm2.get('purchase_order') else {}
    check('下达：计划采购 → 采购订单（按供应商合并）', po_new.get('supplier') == sups['S001']['id'] and po_new.get('source') == 'mrp', r.get('orders'))
    act('purchase_order', po_new['id'], 'approve', tok=BUYER)
    pl_rm = rows('po_line', TABLES['po_line'].c.purchase_order == PO_RM)[0]
    check('下达：提前建议 → 在途采购订单行的到货日期改成需求日', str(pl_rm['due']) == str(today + timedelta(days=10)), pl_rm['due'])
    wo_fg = get('work_order', id=get('mrp_suggestion', id=fgm['id'])['work_order'])
    check('计划生产工单关联到销售订单（单一订单需求时）', wo_fg.get('sales_order') == SO_T)

    # ───────────────────────── E 成本 ─────────────────────────
    section('E 成本')
    with db.engine.begin() as c:
        db.update(c, 'supplier_material', db.get(c, 'supplier_material', supplier=sups['S001']['id'], material=ids['T-RM'])['id'], {'price': 10}, raw=True)
    for price in (10, 13):
        st, r = http('POST', '/api/erp/doc/purchase_order', {'supplier': sups['S001']['id'], 'lines': [{'material': ids['T-RM'], 'qty': 100, 'price': price}]}, BUYER)
        act('purchase_order', r['id'], 'approve', tok=BUYER)
        st, r = http('POST', f"/api/purchase/orders/{r['id']}/receipt", {}, WH)
        act('purchase_receipt', r['id'], 'post', tok=WH)
    http('POST', '/api/cost/valuate', {}, FIN)
    ic = get('inv_cost', material=ids['T-RM'])
    check('移动平均：100 × 10 + 100 × 13 → 单价 11.5', ic and abs(ic['avg_cost'] - 11.5) < 1e-6 and abs(ic['qty'] - 200) < 1e-6, ic and ic['avg_cost'])
    prev_end = today.replace(day=1) - timedelta(days=1)
    day0 = datetime(prev_end.year, prev_end.month, min(prev_end.day, 20))
    with db.engine.begin() as c:
        eq = db.insert(c, 'equipment', {'code': 'T-EQ', 'name': '测试设备', 'etype': 'cnc', 'status': 'idle'}, raw=True)
        WO_C = db.insert(c, 'work_order', {'product': ids['T-SF'], 'qty': 100, 'qty_done': 0, 'qty_scrap': 0, 'status': 'released', 'due': day0}, raw=True)
    st, r = http('POST', '/api/production/issue_bom', {'work_order': WO_C, 'qty': 100, 'warehouse': whs['raw']}, PLAN)
    check('按 BOM 领料 150（出库记录关联工单）', st == 200, r)
    with db.engine.begin() as c:
        for h0, h1 in ((8, 10), (10, 13)):
            db.insert(c, 'report', {'work_order': WO_C, 'equipment': eq, 'qty_good': 50, 'qty_ng': 0, 'start': day0.replace(hour=h0), 'end': day0.replace(hour=h1),
                                    'source': 'manual'}, raw=True)
        for h in range(8, 13):
            db.insert(c, 'energy_hour', {'equipment': eq, 'hour': day0.replace(hour=h), 'kwh': 10}, raw=True)
        db.update(c, 'work_order', WO_C, {'qty_done': 100, 'status': 'completed', 'completed_at': day0.replace(hour=14)}, raw=True)
    st, r = http('POST', '/api/cost/roll_up', {}, FIN)
    std = get('std_cost', material=ids['T-SF'], current=True)
    check('标准成本卷积：半成品 = 1.5 × 原料标准价 10 = 15', std and abs(std['total'] - 15) < 1e-6, std and std['total'])
    st, r = http('POST', '/api/cost/work_orders', {'ids': [WO_C]}, FIN)
    wc = get('wo_cost', work_order=WO_C)
    exp_tot = 150 * 11.5 + 5 * 45 + 5 * 60 + 50 * 0.75 + 5 * 25
    check('工单实际：料 150×11.5 + 工 5h×45 + 机 5h×60 + 能 50kWh×0.75 + 费 5 机时×25', wc and abs(wc['mat'] - 1725) < 0.01 and abs(wc['lab'] - 225) < 0.01
          and abs(wc['mach'] - 300) < 0.01 and abs(wc['energy'] - 37.5) < 0.01 and abs(wc['ovh'] - 125) < 0.01 and abs(wc['total'] - exp_tot) < 0.01,
          wc and (wc['mat'], wc['lab'], wc['mach'], wc['energy'], wc['ovh'], wc['total']))
    check('差异：实际 − 标准（100 × 15），材料差异 = 1725 − 1500', wc and abs(wc['variance'] - (exp_tot - 1500)) < 0.01
          and abs(wc['var_detail']['mat']['diff'] - 225) < 0.01, wc and wc['variance'])
    st, v = http('GET', '/api/cost/variance?group=product', token=FIN)
    check('差异分析按产品汇总', st == 200 and any(x['key'] == ids['T-SF'] and abs(x['variance'] - (exp_tot - 1500)) < 0.01 for x in v))
    prev = core.period_of(prev_end)
    with db.engine.begin() as c:
        pid = core.ensure_period(c, prev)['id']
    st, r = act('fin_period', pid, 'close', tok=SALES)
    check('销售员不能结账（403）', st == 403)
    st, r = act('fin_period', pid, 'close', tok=FIN)
    check('月末结账：冻结本期完工工单成本', st == 200 and get('wo_cost', work_order=WO_C)['status'] == 'frozen', r.get('msg') or r.get('detail'))
    with db.engine.begin() as c:
        cr = db.get(c, 'cost_rate', code='LAB-STD')
        db.update(c, 'cost_rate', cr['id'], {'rate': 99}, raw=True)
    http('POST', '/api/cost/work_orders', {'ids': [WO_C]}, FIN)
    check('已结账的工单成本不再重算', abs(get('wo_cost', work_order=WO_C)['lab'] - 225) < 0.01)
    with db.engine.begin() as c:
        db.update(c, 'cost_rate', cr['id'], {'rate': 45}, raw=True)
    st, r = http('POST', f'/api/sales/orders/{SO1}/delivery', {'ship_date': str(prev_end), 'lines': {str(ln1['id']): 5}}, SALES)
    st2, r2, h2 = http('POST', f"/api/e/delivery_order/{r['id']}/do/post", {}, WH, raw=True)
    check('期间锁：已结账期间的日期不能发货过账（409 + X-Gate: period_closed）', st2 == 409 and {k.lower(): v for k, v in h2.items()}.get('x-gate') == 'period_closed',
          r2.get('detail'))
    act('delivery_order', r['id'], 'cancel')
    st, r = http('POST', '/api/finance/ar_receipts', {'customer': custs['西岭重卡']['id'], 'amount': 100, 'date': str(prev_end)}, FIN)
    check('期间锁：已结账期间不能登记收款', st == 409)
    st, r = act('fin_period', pid, 'reopen', {'reason': ''}, FIN)
    st2, r2 = act('fin_period', pid, 'reopen', {'reason': '补录一张发票'}, FIN)
    check('反结账要写原因；反结账后工单成本解冻', st == 400 and st2 == 200 and get('wo_cost', work_order=WO_C)['status'] == 'open')
    act('fin_period', pid, 'close', tok=FIN)

    # ───────────────────────── F 应收应付 ─────────────────────────
    section('F 应收应付')
    st, r = http('POST', '/api/erp/doc/ar_invoice', {'customer': custs['西岭重卡']['id'], 'invoice_no': '04400001', 'lines': [{'delivery_order': D1}]}, FIN)
    INV = r.get('id')
    inv = get('ar_invoice', id=INV) if INV else {}
    check('销售发票：金额不填 = 发货单剩余可开票；发货单"已开票"', st == 200 and abs(inv['total'] - d1['total']) < 0.01 and abs(get('delivery_order', id=D1)['invoiced'] - d1['total']) < 0.01,
          f"{st} {r}")
    st, r = http('POST', '/api/erp/doc/ar_invoice', {'customer': custs['西岭重卡']['id'], 'invoice_no': '04400002', 'lines': [{'delivery_order': D1, 'amount': 100, 'tax': 13}]}, FIN)
    check('同一张发货单不能超开发票（409）', st == 409, r.get('detail'))
    st, r = http('POST', '/api/finance/ar_receipts', {'customer': custs['西岭重卡']['id'], 'amount': 20000, 'method': 'bank', 'bank_ref': 'B001'}, FIN)
    inv = get('ar_invoice', id=INV)
    check('收款自动核销到发票（部分收款）', st == 200 and abs(inv['paid'] - 20000) < 0.01 and inv['status'] == 'partial')
    st, ag = http('GET', f"/api/finance/ageing?side=ar&partner={custs['西岭重卡']['id']}", token=FIN)
    row = (ag.get('rows') or [{}])[0]
    with db.engine.connect() as c:
        from honeymom.erp.sales import receivable
        bal = receivable(c, custs['西岭重卡']['id'])
    check('账龄合计 = 应收余额（发货 − 退货 − 收款）', st == 200 and abs(row.get('total', 0) - bal) < 0.01, f"{row.get('total')} vs {bal}")
    st, cr_ = http('GET', f"/api/sales/credit/{custs['西岭重卡']['id']}", token=SALES)
    check('信用占用 = 应收 + 未交订单', abs(cr_['exposure'] - (cr_['receivable'] + cr_['open_orders'])) < 0.01 and abs(cr_['receivable'] - bal) < 0.01)
    st, r = http('POST', '/api/erp/doc/ap_invoice', {'supplier': sups['S002']['id'], 'invoice_no': 'AP-001', 'lines': [{'purchase_receipt': RC1}]}, FIN)
    st2, r2 = http('POST', '/api/finance/ap_payments', {'supplier': sups['S002']['id'], 'amount': 10000}, FIN)
    api_ = get('ap_invoice', id=r.get('id')) if r.get('id') else {}
    check('采购发票（对收货单）、付款核销', st == 200 and st2 == 200 and abs(api_['total'] - rc1['total']) < 0.01 and abs(api_['paid'] - 10000) < 0.01, f"{st} {st2}")
    st, ag = http('GET', '/api/finance/ageing?side=ap', token=FIN)
    check('应付账龄', st == 200 and any(x['code'] == 'S002' for x in ag['rows']))

    # ───────────────────────── G 凭证 ─────────────────────────
    section('G 凭证')
    st, r = http('POST', '/api/finance/vouchers/generate', {'period': period}, FIN)
    made = r.get('made') or {}
    check('生成凭证：发货 / 退货 / 收款 / 采购入库 / 付款 / 领料', st == 200 and all(made.get(k, 0) >= 1 for k in ('delivery', 'sales_return', 'ar_receipt', 'receipt', 'ap_payment', 'issue')),
          made)
    vs = rows('voucher', TABLES['voucher'].c.period == period)
    unb = []
    for v in vs:
        ls = rows('voucher_line', TABLES['voucher_line'].c.voucher == v['id'])
        if abs(sum(x['debit'] or 0 for x in ls) - sum(x['credit'] or 0 for x in ls)) > 0.01:
            unb.append(v['code'])
    check('每张凭证借贷平衡', vs and not unb, f"{len(vs)} 张，不平：{unb[:3]}")
    dv = next((v for v in vs if v['source_type'] == 'delivery' and v['source_code'] == d1['code']), None)
    dls = rows('voucher_line', TABLES['voucher_line'].c.voucher == dv['id']) if dv else []
    check('发货凭证：借 1122 应收（客户辅助核算）/ 贷 6001 收入 + 销项税；结转成本', any(x['account_code'] == '1122' and x['aux_code'] == custs['西岭重卡']['code'] and abs(x['debit'] - d1['total']) < 0.01 for x in dls)
          and any(x['account_code'] == '6001' for x in dls) and any(x['account_code'] == '6401' for x in dls))
    st, r = http('POST', '/api/finance/vouchers/generate', {'period': period}, FIN)
    check('重复生成不会重复（幂等）', st == 200 and r['count'] == 0)
    st, r = http('POST', '/api/finance/vouchers/generate', {'period': prev}, FIN)
    check('已结账期间也能生成凭证（工单成本结转）', st == 200 and r['made'].get('wo_cost', 0) >= 1, r.get('made'))
    outs = {}
    for fmt in ('kingdee', 'yonyou_u8', 'yonsuite', 'excel', 'csv'):
        st, b, h = http('GET', f'/api/finance/vouchers/export?fmt={fmt}&period={period}&mark=0', token=FIN, raw=True)
        outs[fmt] = (st, b)
    kd = voucher.read_xlsx(outs['kingdee'][1])
    dcol, ccol = kd[0].index('FDEBIT'), kd[0].index('FCREDIT')
    check('金蝶云星空：两行表头（字段键 + 中文名）、单据头只在每张凭证第一行、借贷合计相等',
          kd[0][0] == 'FBillHead(GL_VOUCHER)' and kd[1][0] == '*单据头(序号)' and kd[3][kd[0].index('FDate')] in ('', None)
          and abs(sum(float(r[dcol] or 0) for r in kd[2:]) - sum(float(r[ccol] or 0) for r in kd[2:])) < 0.01, f"{len(kd) - 2} 行")
    u8 = voucher.read_xlsx(outs['yonyou_u8'][1])
    check('用友 U8：凭证类别 / 凭证号 / 制单日期 / 科目编码 / 借方 / 贷方', u8[0][:3] == ['凭证类别', '凭证号', '制单日期'] and u8[1][0] == '记' and '客户编码' in u8[0])
    ys = voucher.read_xlsx(outs['yonsuite'][1])
    check('用友 YonSuite：账簿编码 / 凭证类型 / 本币借贷', ys[0][0] == '账簿编码' and '本币借方金额' in ys[0] and len(ys) == len(u8))
    ex = voucher.read_xlsx(outs['excel'][1])
    check('通用 Excel 和 CSV（UTF-8 BOM）', ex[0][0] == '期间' and outs['csv'][1][:3] == '﻿'.encode('utf-8') and len(ex) == len(u8))
    http('PUT', '/api/erp/settings', {'erp_voucher_formats': {'yonyou_u8': {'columns': [['科目', 'line.account_code'], ['借', 'line.debit'], ['贷', 'line.credit']]}}}, ADMIN)
    st, b, h = http('GET', f'/api/finance/vouchers/export?fmt=yonyou_u8&period={period}&mark=1', token=FIN, raw=True)
    u8b = voucher.read_xlsx(b)
    check('导出格式可配置（系统参数 erp_voucher_formats 替换列）、导出后标"已导出"', u8b[0] == ['科目', '借', '贷']
          and all(v['status'] == 'exported' for v in rows('voucher', TABLES['voucher'].c.period == period)))
    http('PUT', '/api/erp/settings', {'erp_voucher_formats': {}}, ADMIN)
    st, b, h = http('GET', f'/api/finance/vouchers/export?fmt=kingdee&period={period}', token=SALES, raw=True)
    check('销售员不能导出凭证（403）', st == 403)

    # ───────────────────────── H 外部 ERP 模式 / REST / Webhook ─────────────────────────
    section('H 外部 ERP 模式：REST v1、Webhook')
    st, k = http('POST', '/api/admin/apikeys', {'name': 'ERP 对接', 'perms': ['erp.api.*'], 'roles': []}, ADMIN)
    KEY = k.get('key')
    st, k2 = http('POST', '/api/admin/apikeys', {'name': 'ERP 只读', 'perms': ['erp.api.read'], 'roles': []}, ADMIN)
    KEY_RO = k2.get('key')
    st, wh = http('POST', '/api/erp/webhooks', {'name': '测试 ERP', 'url': SINK + '/hook/a', 'events': ['delivery.posted', 'wo.completion', 'receipt.posted']}, ADMIN)
    HID, SECRET = wh.get('id'), wh.get('secret')
    http('POST', '/mock/config', {'name': 'a', 'secret': SECRET, 'fail_first': 2}, base=SINK)
    check('Webhook：创建时返回一次密钥，列表只显示前 4 位', st == 200 and SECRET.startswith('whsec_')
          and all('secret' not in x for x in http('GET', '/api/erp/webhooks', token=ADMIN)[1]))
    st, m = http('PUT', '/api/erp/mode', {'mode': 'external'}, ADMIN)
    check('切到外部 ERP 模式（默认：主数据和订单 ERP 做主，工单 MES 做主）', st == 200 and m['mode'] == 'external' and m['owner']['customer'] == 'erp' and m['owner']['work_order'] == 'mes')
    core.invalidate_mode()
    st, r = http('POST', '/api/e/customer', {'code': 'UI-C1', 'name': '界面新增'}, ADMIN)
    st2, r2 = http('PUT', f"/api/e/material/{FG}", {'name': '改名'}, ADMIN)
    st3, r3 = http('POST', '/api/erp/doc/sales_order', {'customer': custs['西岭重卡']['id'], 'lines': [{'material': FG, 'qty': 1}]}, ADMIN)
    check('ERP 做主的数据界面上不能新增 / 修改 / 下订单（409 erp_owned）', st == 409 and r.get('code') == 'erp_owned' and st2 == 409 and st3 == 409, r.get('detail'))
    st, r = http('POST', '/api/e/work_order', {'product': FG, 'qty': 5}, ADMIN)
    check('工单仍由 MES 做主：界面能建', st == 200)
    items = [{'code': 'EXT-FG-1', 'name': '外部成品', 'unit': '件', 'mtype': '成品', 'price': 500}, {'code': 'EXT-RM-1', 'name': '外部原料', 'unit': 'kg', 'mtype': '原材料'}]
    st, r = http('PUT', '/api/erp/v1/materials', {'items': items, 'system': 'generic'}, key=KEY)
    check('REST：下发物料（API 密钥）→ created', st == 200 and [x['action'] for x in r['results']] == ['created', 'created'] and get('material', code='EXT-FG-1')['mtype'] == 'fg',
          r)
    st, r = http('PUT', '/api/erp/v1/materials', {'items': items, 'system': 'generic'}, key=KEY)
    check('REST：同样内容再下发 → unchanged（幂等）', [x['action'] for x in r['results']] == ['unchanged', 'unchanged'])
    st, r = http('PUT', '/api/erp/v1/customers', {'items': [{'code': 'EXT-C1', 'name': '外部客户', 'credit_limit': 1}]}, key=KEY)
    st, r = http('PUT', '/api/erp/v1/boms', {'items': [{'product': 'EXT-FG-1', 'items': [{'component': 'EXT-RM-1', 'qty': 2.5}]}]}, key=KEY)
    check('REST：客户、BOM', st == 200 and r['results'][0]['action'] == 'created' and get('bom', product=get('material', code='EXT-FG-1')['id'])['qty'] == 2.5)
    so_item = {'code': 'ERP-SO-1', 'customer': 'EXT-C1', 'order_date': str(today), 'lines': [{'seq': 10, 'material': 'EXT-FG-1', 'qty': 40, 'price': 500,
                                                                                              'due': str(today + timedelta(days=9))}]}
    st, r = http('PUT', '/api/erp/v1/sales_orders', {'items': [so_item]}, key=KEY)
    eso = get('sales_order', code='ERP-SO-1')
    check('REST：下发销售订单 → 直接"已确认"（信用由 ERP 管），单号用 ERP 的', st == 200 and eso and eso['status'] == 'confirmed', r)
    so_item['lines'][0]['qty'] = 55
    so_item['change_reason'] = 'ERP 客户加单'
    st, r = http('PUT', '/api/erp/v1/sales_orders', {'items': [so_item]}, key=KEY)
    ch = rows('so_change', TABLES['so_change'].c.sales_order == eso['id'])
    check('REST：订单变更 → updated，记变更历史（原因 = ERP 给的）', r['results'][0]['action'] == 'updated' and any(x['reason'] == 'ERP 客户加单' for x in ch)
          and get('so_line', sales_order=eso['id'])['qty'] == 55)
    st, r = http('PUT', '/api/erp/v1/work_orders', {'items': [{'code': 'ERP-WO-1', 'product': 'EXT-FG-1', 'qty': 50, 'due': str(today + timedelta(days=8)),
                                                               'sales_order': 'ERP-SO-1'}]}, key=KEY)
    ewo = get('work_order', code='ERP-WO-1')
    check('REST：下发工单（关联销售订单）', st == 200 and ewo and ewo['sales_order'] == eso['id'])
    st, r = http('PUT', '/api/erp/v1/materials', {'items': items}, key=KEY_RO)
    check('只读密钥不能下发（403）', st == 403)
    st, r = http('PUT', '/api/erp/v1/sales_orders', {'items': [{'code': 'ERP-SO-X', 'customer': 'NOPE', 'lines': []}]}, key=KEY)
    check('REST：一条出错只报这一条（找不到客户）', st == 200 and r['results'][0]['action'] == 'error' and '找不到' in r['results'][0]['error'])
    st, r = http('GET', '/api/erp/v1/sales_orders?code=ERP-SO-1', key=KEY_RO)
    check('REST：读订单（带明细）', st == 200 and r['items'][0]['lines'][0]['qty'] == 55)
    st, r = http('GET', '/api/erp/v1/materials', token=None)
    check('REST：没有密钥 401', st == 401)
    st, oa = http('GET', '/api/openapi.json')
    st2, docs_html, _ = http('GET', '/api/docs', raw=True)
    check('OpenAPI：/api/openapi.json 含 ERP v1 接口和 API 密钥认证、/api/docs 页面', st == 200 and '/api/erp/v1/{kind}' in oa['paths']
          and 'ApiKey' in oa['components']['securitySchemes'] and st2 == 200 and b'swagger' in docs_html.lower())
    integration.collect()                      # 第一次：建游标
    with db.engine.begin() as c:
        move(c, 'in', get('material', code='EXT-FG-1')['id'], 100, whs['fg'], who='测试', reason='期初库存')
    st, r = http('POST', f"/api/sales/orders/{eso['id']}/delivery", {'lines': {str(get('so_line', sales_order=eso['id'])['id']): 30}}, WH)
    st, r = act('delivery_order', r['id'], 'post', tok=WH)
    d_ext = rows('delivery_order', TABLES['delivery_order'].c.sales_order == eso['id'])[0]
    with db.engine.connect() as c:
        evs = [dict(x) for x in c.execute(sa.select(ERP_TABLES['erp_event']).where(ERP_TABLES['erp_event'].c.doc_code == d_ext['code'])).mappings()]
    check('外部模式：发货过账（发货不归 ERP 管，界面能做）→ 事件 delivery.posted', st == 200 and evs and evs[0]['event'] == 'delivery.posted', r.get('detail'))
    dt = ERP_TABLES['erp_delivery']

    def dl(ev_id, ch):
        with db.engine.connect() as c:
            return dict(c.execute(sa.select(dt).where((dt.c.event == ev_id) & (dt.c.channel == ch))).mappings().first() or {})
    integration.dispatch()
    d_ = dl(evs[0]['id'], f'webhook:{HID}')
    check('Webhook 第 1 次失败（接收方 500）→ 重试、10 秒后再投', d_.get('status') == 'retry' and d_.get('attempts') == 1 and d_['next_try'] > integration.now(),
          d_.get('last_error'))

    def due_now():
        with db.engine.begin() as c:
            c.execute(dt.update().where(dt.c.status == 'retry').values(next_try=integration.now() - timedelta(seconds=1)))
    due_now()
    integration.dispatch()
    due_now()
    integration.dispatch()
    d_ = dl(evs[0]['id'], f'webhook:{HID}')
    got = http('GET', '/mock/received?name=a', base=SINK)[1]
    check('Webhook 退避重试后成功（第 3 次）', d_.get('status') == 'sent' and d_.get('attempts') == 3 and len(got) == 3, d_.get('status'))
    check('Webhook 签名：接收方每次都验签通过、同一个事件 id', all(g['sig_ok'] for g in got) and len({g['body']['id'] for g in got}) == 1
          and got[-1]['body']['data']['code'] == d_ext['code'] and got[-1]['event'] == 'delivery.posted')
    body = json.dumps({'a': 1}).encode()
    ts = str(int(time.time()))
    good = integration.sign('k1', ts, body)
    check('验签：密钥不对 / 内容被改 / 时间戳过期 都不通过', integration.verify('k1', {'X-HM-Timestamp': ts, 'X-HM-Signature': good}, body)
          and not integration.verify('k2', {'X-HM-Timestamp': ts, 'X-HM-Signature': good}, body)
          and not integration.verify('k1', {'X-HM-Timestamp': ts, 'X-HM-Signature': good}, body + b' ')
          and not integration.verify('k1', {'X-HM-Timestamp': str(int(time.time()) - 900), 'X-HM-Signature': integration.sign('k1', str(int(time.time()) - 900), body)}, body))
    st, wb = http('POST', '/api/erp/webhooks', {'name': '连不上', 'url': 'http://127.0.0.1:9/hook', 'events': ['delivery.posted'], 'max_attempts': 2}, ADMIN)
    st, r = http('POST', f"/api/erp/resync", {'doc_type': 'delivery_order', 'code': d_ext['code']}, ADMIN)
    due_now()
    integration.dispatch()
    d_b = dl(r['event'], f"webhook:{wb['id']}")
    check('手动重新同步：重新生成事件；连不上的 Webhook 超过最大次数 → dead', d_b.get('status') == 'dead' and d_b.get('attempts') == 2, d_b.get('last_error'))
    st, mon = http('GET', '/api/erp/monitor', token=ADMIN)
    check('监控：失败列表里有它、通道计数', st == 200 and any(x['id'] == d_b['id'] for x in mon['failures']) and any(x['channel'] == f'webhook:{HID}' for x in mon['channels']))
    st, r = http('POST', f"/api/erp/deliveries/{d_b['id']}/retry", {}, ADMIN)
    check('监控：手动重试（重新排队并立即投递）', st == 200 and dl(r and d_b['event'], f"webhook:{wb['id']}")['attempts'] >= 1)
    with db.engine.begin() as c:
        db.update(c, 'work_order', ewo['id'], {'qty_done': 20, 'status': 'in_progress'}, raw=True)
    n = integration.collect()
    with db.engine.connect() as c:
        wev = [dict(x) for x in c.execute(sa.select(ERP_TABLES['erp_event']).where(ERP_TABLES['erp_event'].c.event == 'wo.completion')).mappings()]
    check('完工回传：工单完工数增加 → wo.completion（增量 20）', n >= 1 and wev and wev[-1]['payload']['qty'] == 20 and wev[-1]['payload']['work_order'] == 'ERP-WO-1')
    with db.engine.begin() as c:
        db.update(c, 'work_order', ewo['id'], {'qty_done': 50}, raw=True)
    integration.collect()
    with db.engine.connect() as c:
        wev = [dict(x) for x in c.execute(sa.select(ERP_TABLES['erp_event']).where(ERP_TABLES['erp_event'].c.event == 'wo.completion')).mappings()]
    check('完工回传只报增量（第二次 30）', wev[-1]['payload']['qty'] == 30)
    st, r = http('GET', '/api/erp/v1/events?after=0&types=wo.completion,delivery.posted', key=KEY_RO)
    check('REST：按 id 增量拉事件', st == 200 and {'wo.completion', 'delivery.posted'} <= {e['event'] for e in r['events']} and r['next'] >= r['events'][-1]['id'])
    st, r = http('GET', '/api/erp/v1/stock?material=EXT-FG-1', key=KEY_RO)
    check('REST：库存结存', st == 200 and abs(sum(x['qty'] for x in r['items']) - 70) < 1e-6)
    st, m = http('PUT', '/api/erp/mode', {'mode': 'external', 'owner': {'customer': 'mes'}}, ADMIN)
    core.invalidate_mode()
    st, r = http('POST', '/api/e/customer', {'code': 'UI-C2', 'name': '界面新增 2'}, ADMIN)
    check('归属可以逐项改：客户改回 MES 做主后界面能建', st == 200)

    # ───────────────────────── I 中间库 ─────────────────────────
    section('I 中间库')
    stg_url = 'sqlite:///' + os.path.join(work, 'staging.db').replace('\\', '/')
    st, m = http('PUT', '/api/erp/mode', {'mode': 'external', 'staging_url': stg_url, 'erp_staging_in': True, 'erp_staging_out': True}, ADMIN)
    se = integration.staging_engine()
    check('中间库：单独的库（自动建表 erp_stg_in / erp_stg_out）', se is not db.engine and sa.inspect(se).has_table('erp_stg_in'))
    si, so_ = ERP_TABLES['erp_stg_in'], ERP_TABLES['erp_stg_out']
    now_ = datetime.now()
    with se.begin() as c:
        c.execute(si.insert(), [
            {'entity': 'material', 'ext_key': 'STG-RM-1', 'payload': {'code': 'STG-RM-1', 'name': '中间库原料', 'unit': 'kg', 'mtype': 'raw'}, 'status': 'new', 'created_at': now_},
            {'entity': 'customer', 'ext_key': 'STG-C1', 'payload': {'name': '中间库客户'}, 'status': 'new', 'created_at': now_},
            {'entity': 'sales_order', 'ext_key': 'STG-SO-1', 'payload': {'customer': 'STG-C1', 'lines': [{'material': 'STG-RM-1', 'qty': 5, 'price': 1}]},
             'status': 'new', 'created_at': now_},
            {'entity': 'sales_order', 'ext_key': 'STG-SO-2', 'payload': {'customer': 'STG-NOPE', 'lines': [{'material': 'STG-RM-1', 'qty': 5}]}, 'status': 'new',
             'created_at': now_}])
    st, r = http('POST', '/api/erp/staging/sync', {}, ADMIN)
    with se.connect() as c:
        stg = {x['ext_key']: dict(x) for x in c.execute(sa.select(si)).mappings()}
    check('中间库进：3 条成功、1 条错误（写回状态和原因）', r.get('done') == 3 and r.get('error') == 1 and stg['STG-SO-2']['status'] == 'error'
          and '找不到' in (stg['STG-SO-2']['error'] or '') and get('sales_order', code='STG-SO-1') is not None, r)
    with se.begin() as c:
        c.execute(si.insert().values(entity='material', ext_key='STG-RM-1', payload={'code': 'STG-RM-1', 'name': '中间库原料', 'unit': 'kg', 'mtype': 'raw'},
                                     status='new', created_at=now_))
        c.execute(si.insert().values(entity='customer', ext_key='STG-NOPE', payload={'name': '后补的客户'}, status='new', created_at=now_))
    st, r = http('POST', '/api/erp/staging/sync', {}, ADMIN)
    check('中间库进：内容没变 → skipped（幂等）', r.get('skipped') >= 1 and r.get('done') >= 1, r)
    st, r = http('POST', f"/api/erp/staging/{stg['STG-SO-2']['id']}/retry", {}, ADMIN)
    with se.connect() as c:
        s2 = dict(c.execute(sa.select(si).where(si.c.id == stg['STG-SO-2']['id'])).mappings().first())
    check('中间库进：补了客户后重试 → 成功', s2['status'] == 'done' and get('sales_order', code='STG-SO-2') is not None, s2.get('error'))
    st, r = http('POST', f"/api/purchase/orders/{po_new['id']}/receipt", {}, WH)
    st, r2 = act('purchase_receipt', r['id'], 'post', tok=WH)
    integration.dispatch()
    with se.connect() as c:
        outs_ = [dict(x) for x in c.execute(sa.select(so_)).mappings()]
    rc_code = get('purchase_receipt', id=r['id'])['code']
    hit = [x for x in outs_ if x['doc_code'] == rc_code]
    check('中间库出：采购入库事件写进 erp_stg_out（status=new）', hit and hit[0]['event'] == 'receipt.posted' and hit[0]['status'] == 'new', len(outs_))
    with se.begin() as c:
        c.execute(so_.update().where(so_.c.id == hit[0]['id']).values(status='acked', acked_at=datetime.now(), ack_note='ERP 已生成采购入库单'))
    integration.dispatch()
    with se.connect() as c:
        n_same = c.execute(sa.select(sa.func.count()).select_from(so_).where(so_.c.event_id == hit[0]['event_id'])).scalar()
    check('中间库出：同一事件只写一行（ERP 回写 acked 后不再重复写）', n_same == 1)
    st, mon = http('GET', '/api/erp/monitor', token=ADMIN)
    check('监控：中间库计数', mon['staging_separate'] and mon['staging_in'].get('done', 0) >= 4 and mon['staging_out'].get('acked') == 1)
    http('PUT', '/api/erp/mode', {'mode': 'external', 'staging_url': '', 'erp_staging_in': False, 'erp_staging_out': False}, ADMIN)

    # ───────────────────────── J 对接器 ─────────────────────────
    section('J 对接器：金蝶云星空 / 用友 U8 / YonSuite（模拟服务）')
    st, r = http('PUT', '/api/erp/connectors/kd', {'kind': 'kingdee', 'name': '金蝶云星空', 'base_url': KD + '/k3cloud', 'enabled': False,
                                                   'config': {'acct_id': 'MES_TEST', 'username': 'administrator', 'org': '100', 'wh_map': {'W-FG': 'CK01'}},
                                                   'secret': {'password': 'wrong'}}, ADMIN)
    st, r = http('POST', '/api/erp/connectors/kd/test', {}, ADMIN)
    check('金蝶：密码错 → 测试失败（说明原因）', r.get('ok') is False and '用户名或密码错误' in r.get('error', ''))
    http('PUT', '/api/erp/connectors/kd', {'secret': {'password': 'kd@123'}}, ADMIN)
    st, r = http('POST', '/api/erp/connectors/kd/test', {}, ADMIN)
    check('金蝶：登录成功', r.get('ok') is True, r)
    st, conns = http('GET', '/api/erp/connectors', token=ADMIN)
    check('对接配置接口不返回密码', all('secret' not in x and x['secret_set'] == ['password'] for x in conns['items'] if x['code'] == 'kd'))
    st, r = http('POST', '/api/erp/connectors/kd/pull', {}, ADMIN)
    sm = r.get('summary') or {}
    check('金蝶拉取：物料 3、客户 2、供应商 1、BOM 2、销售订单只拉已审核的 1 张', sm.get('material', {}).get('created') == 3 and sm.get('customer', {}).get('created') == 2
          and sm.get('supplier', {}).get('created') == 1 and sm.get('bom', {}).get('created') == 2 and sm.get('sales_order', {}).get('created') == 1, sm)
    kso = get('sales_order', code='XSDD000101')
    check('金蝶拉取：订单 2 行、来源 = 金蝶、物料属性 自制 → 成品；外部编码映射', kso and len(rows('so_line', TABLES['so_line'].c.sales_order == kso['id'])) == 2
          and kso['source'] == 'kingdee' and get('material', code='KD-FG-001')['mtype'] == 'fg'
          and q1("select count(*) from erp_link where system='kingdee'")[0] >= 9)
    st, r = http('POST', '/api/erp/connectors/kd/pull', {}, ADMIN)
    check('金蝶再拉一次：全部 unchanged', all(v['created'] == 0 and v['updated'] == 0 for v in r['summary'].values()), r['summary'])
    http('PUT', '/api/erp/connectors/kd', {'enabled': True}, ADMIN)
    http('PUT', '/api/erp/mode', {'mode': 'external', 'owner': {'customer': 'erp'}}, ADMIN)
    core.invalidate_mode()
    with db.engine.begin() as c:
        move(c, 'in', get('material', code='KD-FG-001')['id'], 200, whs['fg'], who='测试', reason='期初库存')
    integration.collect()
    integration.dispatch()
    http('POST', '/mock/reset', base=KD)
    http('POST', '/mock/expire', base=KD)
    kl = rows('so_line', TABLES['so_line'].c.sales_order == kso['id'])
    st, r = http('POST', f"/api/sales/orders/{kso['id']}/delivery", {'lines': {str(kl[0]['id']): 120}}, WH)
    st, r = act('delivery_order', r['id'], 'post', tok=WH)
    integration.collect()
    integration.dispatch()
    ks = http('GET', '/mock/state', base=KD)[1]
    out_ = [s for s in ks['saved'] if s['form'] == 'SAL_OUTSTOCK']
    check('金蝶回推发货：销售出库单（客户、仓库按映射 CK01、数量、关联订单号）', out_ and out_[0]['model']['FCustomerID']['FNumber'] == 'KD-C001'
          and out_[0]['model']['FEntity'][0]['FStockID']['FNumber'] == 'CK01' and out_[0]['model']['FEntity'][0]['FRealQty'] == 120
          and out_[0]['model']['FEntity'][0]['FSoorDerno'] == 'XSDD000101' and out_[0]['audit'] is True, ks['calls'][-5:])
    check('金蝶会话失效后自动重新登录', ks['logins'] >= 1)
    stk = [s for s in ks['saved'] if s['form'] in ('STK_MISCELLANEOUS', 'STK_MisDelivery')]
    check('金蝶回推库存：查即时库存，差额过其他出入库', stk and any(e['FMATERIALID']['FNumber'] == 'KD-FG-001' and abs(e['FQty'] - (80 - 10)) < 1e-6
                                                         for s in stk if s['form'] == 'STK_MISCELLANEOUS' for e in s['model']['FEntity']),
          [(s['form'], s['model']['FEntity'][:1]) for s in stk][:2])
    with db.engine.begin() as c:
        kwo = db.insert(c, 'work_order', {'code': 'WO-KD-1', 'product': get('material', code='KD-FG-001')['id'], 'qty': 30, 'qty_done': 0, 'qty_scrap': 0,
                                          'status': 'released'}, raw=True)
    integration.collect()
    with db.engine.begin() as c:            # 之前失败待重试的（物料在金蝶里没有的库存校准）往后推，免得它先用掉"下一次失败"
        c.execute(dt.update().where(dt.c.status == 'retry').values(next_try=integration.now() + timedelta(hours=1)))
    http('POST', '/mock/fail_next', {'n': 1}, base=KD)
    with db.engine.begin() as c:
        db.update(c, 'work_order', kwo, {'qty_done': 12, 'status': 'in_progress'}, raw=True)
    integration.collect()
    integration.dispatch()
    with db.engine.connect() as c:
        kev = c.execute(sa.select(ERP_TABLES['erp_event'].c.id).where((ERP_TABLES['erp_event'].c.event == 'wo.completion') & (ERP_TABLES['erp_event'].c.doc_code == 'WO-KD-1'))).scalar()
    d_k = dl(kev, 'connector:kd')
    check('金蝶保存失败（模拟数据库繁忙）→ 投递记录失败原因、待重试', d_k.get('status') == 'retry' and '繁忙' in (d_k.get('last_error') or ''), d_k.get('last_error'))
    due_now()
    integration.dispatch()
    ks = http('GET', '/mock/state', base=KD)[1]
    pi = [s for s in ks['saved'] if s['form'] == 'PRD_INSTOCK']
    check('金蝶回推完工：重试后生成生产入库单（数量 = 完工增量 12、关联工单号）', dl(kev, 'connector:kd').get('status') == 'sent' and pi
          and pi[-1]['model']['FEntity'][0]['FRealQty'] == 12 and pi[-1]['model']['FEntity'][0]['FMoBillNo'] == 'WO-KD-1')
    http('PUT', '/api/erp/connectors/kd', {'enabled': False}, ADMIN)
    # 用友 U8
    http('PUT', '/api/erp/connectors/u8', {'kind': 'yonyou_u8', 'name': '用友 U8', 'base_url': YY + '/u8', 'enabled': True,
                                           'config': {'from_account': 'MES', 'to_account': '001', 'app_key': 'u8-app-key', 'push': ['delivery.posted']},
                                           'secret': {'app_secret': 'u8-app-secret'}}, ADMIN)
    st, r = http('POST', '/api/erp/connectors/u8/test', {}, ADMIN)
    st2, r2 = http('POST', '/api/erp/connectors/u8/pull', {}, ADMIN)
    sm = r2.get('summary') or {}
    uso = get('sales_order', code='U8SO0001')
    check('用友 U8：取令牌、拉物料 / 客户 / 供应商 / BOM / 订单', r.get('ok') and sm.get('material', {}).get('created') == 2 and sm.get('bom', {}).get('created') == 1
          and uso and uso['source'] == 'yonyou' and get('material', code='U8-FG-01')['mtype'] == 'fg', sm)
    with db.engine.begin() as c:
        move(c, 'in', get('material', code='U8-FG-01')['id'], 100, whs['fg'], who='测试', reason='期初库存')
    http('POST', '/mock/expire', base=YY)
    ul = rows('so_line', TABLES['so_line'].c.sales_order == uso['id'])
    st, r = http('POST', f"/api/sales/orders/{uso['id']}/delivery", {}, WH)
    act('delivery_order', r['id'], 'post', tok=WH)
    integration.dispatch()
    ys_state = http('GET', '/mock/state', base=YY)[1]
    so_u8 = [s for s in ys_state['saved'] if s['sys'] == 'u8' and s['object'] == 'saleout']
    check('用友 U8 回推发货：销售出库单（令牌失效后重新取）', so_u8 and so_u8[0]['doc']['custcode'] == 'U8-C01' and so_u8[0]['doc']['entry'][0]['quantity'] == 60, ys_state['calls'][-4:])
    http('PUT', '/api/erp/connectors/u8', {'enabled': False}, ADMIN)
    # YonSuite
    http('PUT', '/api/erp/connectors/ys', {'kind': 'yonsuite', 'name': '用友 YonSuite', 'base_url': YY + '/ys', 'enabled': True,
                                           'config': {'app_key': 'ys-app-key', 'org': 'ORG001', 'push': ['wo.completion']}, 'secret': {'app_secret': 'bad'}}, ADMIN)
    st, r = http('POST', '/api/erp/connectors/ys/test', {}, ADMIN)
    check('YonSuite：签名不对 → 取令牌失败', r.get('ok') is False and '签名' in r.get('error', ''))
    http('PUT', '/api/erp/connectors/ys', {'secret': {'app_secret': 'ys-app-secret'}}, ADMIN)
    st, r = http('POST', '/api/erp/connectors/ys/pull', {'entities': ['material', 'customer', 'supplier', 'bom', 'sales_order']}, ADMIN)
    sm = r.get('summary') or {}
    check('YonSuite：HmacSHA256 签名取令牌、分页拉物料（名称多语言对象）/ 客户 / 订单', sm.get('material', {}).get('created') == 2
          and get('material', code='YS-FG-01')['name'] == '转向器壳体（YS）' and get('sales_order', code='YSSO0001') is not None, sm)
    with db.engine.begin() as c:
        ywo = db.insert(c, 'work_order', {'code': 'WO-YS-1', 'product': get('material', code='YS-FG-01')['id'], 'qty': 10, 'qty_done': 0, 'qty_scrap': 0, 'status': 'released'},
                        raw=True)
    integration.collect()
    with db.engine.begin() as c:
        db.update(c, 'work_order', ywo, {'qty_done': 10, 'status': 'completed'}, raw=True)
    integration.collect()
    integration.dispatch()
    ys_state = http('GET', '/mock/state', base=YY)[1]
    sp = [s for s in ys_state['saved'] if s['sys'] == 'ys' and s['path'].endswith('storeprorecord/save')]
    check('YonSuite 回推完工：产品入库单', sp and sp[-1]['doc']['storeProRecords'][0]['product_code'] == 'YS-FG-01' and sp[-1]['doc']['storeProRecords'][0]['qty'] == 10)
    http('PUT', '/api/erp/connectors/ys', {'enabled': False}, ADMIN)
    st, r = http('POST', '/api/erp/resync', {'connector': 'kd', 'entities': ['material']}, ADMIN)
    check('手动重新同步（对接器强制重拉，不看摘要）', st == 200 and r['summary']['material']['total'] == 3)
    http('PUT', '/api/erp/mode', {'mode': 'builtin'}, ADMIN)
    core.invalidate_mode()

    # ───────────────────────── K 权限与数据范围 ─────────────────────────
    section('K 权限与数据范围')
    st, r = http('POST', f'/api/sales/orders/{SO1}/delivery', {'lines': {str(ln1['id']): 5}}, SALES)
    DK = r['id']
    st1, _ = act('delivery_order', DK, 'post', tok=BUYER)
    st2, _ = act('purchase_order', PO3, 'close', tok=SALES)
    st3, _ = http('GET', '/api/erp/doc/ar_invoice', token=OPER)
    st4, _ = http('GET', '/api/finance/ageing?side=ar', token=OPER)
    st5, _ = http('GET', '/api/cost/work_orders', token=SALES)
    st6, _ = http('POST', '/api/finance/vouchers/generate', {'period': period}, BUYER)
    st7, _ = act('delivery_order', DK, 'post', tok=WH)
    check('角色：采购员不能发货、销售员不能关采购订单、操作工看不到发票 / 账龄、销售员看不到成本、采购员不能生成凭证；仓管员能发货',
          (st1, st2, st3, st4, st5, st6, st7) == (403, 403, 403, 403, 403, 403, 200), (st1, st2, st3, st4, st5, st6, st7))
    st, me = http('GET', '/api/me', token=OPER)
    flat = [p for g in me['perms'] for p in g]
    check('操作工的权限里排除了财务和成本页面', '!page.finance.*' in flat and '!finance.*' in flat)
    with db.engine.begin() as c:
        p2 = db.insert(c, 'plant', {'code': 'P2', 'name': '二厂'}, raw=True)
        db.update(c, 'sys_user', db.get(c, 'sys_user', username='sales2')['id'], {'scope': {'plants': [plant1]}}, raw=True)
    st, r = http('POST', '/api/erp/doc/sales_order', {'customer': custs['北辰重卡']['id'], 'plant': p2, 'lines': [{'material': FG, 'qty': 3}]}, ADMIN)
    SO_P2 = r['id']
    act('sales_order', SO_P2, 'confirm')
    from honeymom import perms as perms_m
    perms_m.invalidate()
    SALES2 = login('sales2')
    st, lst = http('GET', '/api/erp/doc/sales_order?size=500', token=SALES2)
    st2, one = http('GET', f'/api/erp/doc/sales_order/{SO_P2}', token=SALES2)
    st3, b2 = http('GET', '/api/sales/board', token=SALES2)
    st4, gen = http('GET', f'/api/e/sales_order?size=500', token=SALES2)
    check('数据范围（一厂）：单据列表、详情（404）、订单看板、通用列表都看不到二厂订单', st == 200 and all(x['id'] != SO_P2 for x in lst['items']) and st2 == 404
          and all(x['sales_order'] != SO_P2 for x in b2['open']) and all(x['id'] != SO_P2 for x in gen['items']) and lst['total'] > 0)
    st, r = http('POST', '/api/erp/doc/sales_order', {'customer': custs['北辰重卡']['id'], 'plant': p2, 'lines': [{'material': FG, 'qty': 3}]}, SALES2)
    check('数据范围：不能在范围外的工厂下订单（403）', st == 403)
    st, k3 = http('POST', '/api/admin/apikeys', {'name': '一厂 ERP', 'perms': ['erp.api.read'], 'scope': {'plants': [plant1]}}, ADMIN)
    st, r = http('GET', '/api/erp/v1/sales_orders?limit=5000', key=k3['key'])
    check('数据范围：API 密钥同样只读得到范围内的订单', st == 200 and all(x['id'] != SO_P2 for x in r['items']) and r['items'])

    # ───────────────────────── M 审批流程（M3）挂在 ERP 单据上 ─────────────────────────
    section('M 审批流程（M3 通用审批挂 ERP 单据）')
    st, f1 = http('POST', '/api/impl/flows', {'name': '大额采购订单审批', 'entity': 'purchase_order', 'action': 'approve',
                                             'condition': [{'field': 'total', 'op': '>', 'value': 100000}], 'steps': [{'name': '厂长', 'type': 'role', 'approver': 'manager'}]},
                  ADMIN)
    if st == 404:
        check('审批流程接口（M3）还没合进来：跳过', True)
    else:
        st, f2 = http('POST', '/api/impl/flows', {'name': '订单变更审批', 'entity': 'sales_order', 'action': 'change', 'steps': [{'name': '厂长', 'type': 'role', 'approver': 'manager'}]},
                      ADMIN)
        check('流程能挂 ERP 的实体动作和服务动作（采购订单审批、订单变更）', st == 200, f2)
        st, r = http('POST', '/api/erp/doc/purchase_order', {'supplier': sups['S001']['id'], 'lines': [{'material': mats['CM-PIG']['id'], 'qty': 40}]}, BUYER)
        BIG = r['id']
        st, r = act('purchase_order', BIG, 'approve', tok=BUYER)
        check('大额采购订单审批 → 先提交审批（不执行，订单还是草稿）', st == 200 and r.get('pending_approval') and get('purchase_order', id=BIG)['status'] == 'draft', r.get('msg'))
        MGR = login('manager')
        st, aps = http('GET', '/api/approvals?status=pending', token=MGR)
        items = aps if isinstance(aps, list) else (aps.get('items') or aps.get('approvals') or [])
        ap1 = next((x for x in items if x.get('kind') == 'flow' and '采购订单' in (x.get('title') or '')), None)
        st, r = http('POST', f"/api/approvals/{ap1['id']}/decide", {'decision': 'approve'}, MGR) if ap1 else (0, {})
        check('厂长审批通过 → 以申请人身份执行：采购订单已审批', st == 200 and get('purchase_order', id=BIG)['status'] == 'approved', r)
        st, r = http('POST', f'/api/sales/orders/{SO1}/change', {'reason': '客户再加 5 件', 'lines': [{'id': ln1['id'], 'qty': 325}]}, SALES)
        v0 = get('sales_order', id=SO1)['version']
        check('订单变更挂了审批 → 提交审批、版本没变', st == 200 and r.get('pending_approval') and get('so_line', id=ln1['id'])['qty'] == 320, r.get('msg'))
        st, aps = http('GET', '/api/approvals?status=pending', token=MGR)
        items = aps if isinstance(aps, list) else (aps.get('items') or aps.get('approvals') or [])
        ap2 = next((x for x in items if x.get('kind') == 'flow' and '订单变更' in (x.get('title') or '')), None)
        st, r = http('POST', f"/api/approvals/{ap2['id']}/decide", {'decision': 'approve'}, MGR) if ap2 else (0, {})
        check('审批通过 → 执行变更（数量 325、版本 + 1）', st == 200 and get('so_line', id=ln1['id'])['qty'] == 325 and get('sales_order', id=SO1)['version'] == v0 + 1, r)
        for f in (f1, f2):
            if f.get('id'):
                http('PUT', f"/api/impl/flows/{f['id']}", {**{k: v for k, v in f.items() if k in ('name', 'entity', 'action', 'steps', 'condition')}, 'enabled': False}, ADMIN)

    # ───────────────────────── L AI ─────────────────────────
    section('L AI（功能键 erp）')
    st, r = http('POST', '/api/erp/ai/at_risk', {}, ADMIN)
    check('AI 关闭时：ERP 助手接口返回"已关闭"（403 ai_disabled）', st == 403 and r.get('code') == 'ai_disabled')
    http('PUT', '/api/ai/switches', {'global': True, 'features': {'erp': True, 'ask': True}}, ADMIN)
    st, r = http('POST', '/api/erp/ai/explain_mrp', {'suggestion': rmb['id']}, PLAN)
    check('AI 解释 MRP 建议：讲清需求来源（追到订单号）和净算', st == 200 and so_t_code in r.get('text', '') and '净算' in r.get('facts', ''), (r.get('by'), r.get('text', '')[:80]))
    st, r = http('POST', '/api/erp/ai/at_risk', {}, SALES)
    check('AI 看订单风险：列出风险交货计划 + 建议', st == 200 and r.get('text') and isinstance(r.get('items'), list), r.get('text', '')[:60])
    st, d = http('POST', '/api/erp/ai/draft_po', {'material': mats['CM-PIG']['id'], 'qty': 30, 'reason': '补安全库存'}, BUYER)
    check('AI 起草采购订单 → 动作草稿（待审批，没建单）', st == 200 and d.get('status') == 'pending' and d.get('op') == 'purchase_order', d.get('detail') or d.get('title'))
    n_po = len(rows('purchase_order'))
    st, r = http('POST', f"/api/ai/drafts/{d['id']}/decide", {'decision': 'approve'}, ADMIN)
    new_po = rows('purchase_order', TABLES['purchase_order'].c.source == 'ai')
    check('审批通过 → 建采购订单（已审批，来源 AI）', st == 200 and len(rows('purchase_order')) == n_po + 1 and new_po and new_po[0]['status'] == 'approved',
          r.get('receipt') or r.get('detail'))
    st, r = http('POST', '/api/erp/ai/draft_po', {'material': mats['CM-PIG']['id'], 'supplier': 'S005', 'qty': 5}, BUYER)
    check('起草时也检查合格供方（试用供应商不在 ASL → 400）', st == 400, r.get('detail'))
    http('PUT', '/api/ai/switches', {'global': False}, ADMIN)

    # ───────────────────────── N 导入（M3 通用导入框架）─────────────────────────
    section('N 导入（M3 通用导入框架登记的 ERP 导入器）')
    try:
        from honeymom.impl import importer as imp
    except Exception:          # noqa: BLE001
        imp = None
    if imp is None:
        check('导入框架（M3）还没合进来：跳过', True)
    else:
        adm_u = auth.user_by_name('admin')
        keys = {i.key for i in imp.all_importers()}
        check('登记了 ERP 导入器（客户零件号 / 供货清单 / 价格表 / 价格明细 / 计划参数 / 预测 / 费率 / 期初应收 / 期初应付）',
              {'customer_part', 'supplier_material', 'price_list', 'price_item', 'mat_plan', 'forecast', 'cost_rate', 'opening_ar', 'opening_ap'} <= keys)
        r = imp.run('opening_ar', [{'customer': 'C001', 'invoice_no': 'OP-0001', 'invoice_date': str(today - timedelta(days=75)), 'amount': 10000, 'tax': 1300}],
                    user=adm_u)
        oi = get('ar_invoice', invoice_no='OP-0001')
        st, ag = http('GET', f"/api/finance/ageing?side=ar&partner={custs['北辰重卡']['id']}", token=FIN)
        row = (ag.get('rows') or [{}])[0]
        check('期初应收导入 → 发票（期初）→ 进账龄（按到期日分档）', r.get('ok') and oi and oi['opening'] and abs(oi['total'] - 11300) < 0.01
              and any(d['doc'] == oi['code'] and d['bucket'] != 'current' for d in row.get('detail', [])), r.get('summary') or r.get('errors'))
        r2 = imp.run('opening_ar', [{'customer': 'C001', 'invoice_no': 'OP-0001', 'invoice_date': str(today - timedelta(days=75)), 'amount': 10000, 'tax': 1300}],
                     user=adm_u)
        check('同一张期初发票再导一次：不重复', len(rows('ar_invoice', TABLES['ar_invoice'].c.invoice_no == 'OP-0001')) == 1, r2.get('summary'))
        r3 = imp.run('price_item', [{'price_list': 'PL-STD', 'material': 'SGH-450', 'min_qty': 500, 'price': 372, 'tax_rate': 13}], user=adm_u)
        p4 = http('GET', f"/api/sales/price?customer={custs['西岭重卡']['id']}&material={FG}&qty=600", token=SALES)[1]
        check('价格明细导入 → 销售取价按新的起订量阶梯', r3.get('ok') and p4['price'] == 372, (r3.get('summary'), r3.get('errors'), p4))

    print(f'\n共 {len(R)} 项，通过 {sum(R)} 项，失败 {len(R) - sum(R)} 项；用时 {time.time() - t_all:.0f} 秒', flush=True)
    if a.db == 'pg':
        db.engine.dispose()
        try:
            with adm.connect() as c:
                c.execute(sa.text(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{dbname}' AND pid <> pg_backend_pid()"))
                c.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}"'))
        except Exception:          # noqa: BLE001
            pass
    os._exit(0 if all(R) else 1)


if __name__ == '__main__':
    main()
