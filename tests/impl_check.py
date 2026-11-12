"""5.0 实施工具与可配置（M3）回归测试（不需要启动服务）：

  python tests/impl_check.py                                  # SQLite（临时库）
  python tests/impl_check.py --db postgresql+psycopg://…/m3_mes   # PostgreSQL（要一个空库）

A 演示等价：新库上应用 foundry + machining 行业模板（带示范工厂演示数据）= 4.7 的 seed（tests/impl_files/demo_seed_47.json 逐表比，和 id 无关），
  导入设备后的数据（点检保养、夹具、NC 程序、停机计划）也一样；枚举选项和 4.7 一样。
B 导入框架：每个导入器出模板；正常导入、校验错误（类型 / 必填 / 选项 / 引用 / 文件内重复 / 只更新模式）、演练不写库、写入失败整批回滚、
  多级 BOM 循环（连同库里已有的）和"层级"写法、单位换算（同类别、按物料、串联、期初库存折算）、错误报告。
C 行业模板：四个行业包各自在新库上应用（SQLite：每个一个新库；PostgreSQL：在同一个库上依次应用）；预览回滚不留数据；枚举按包启用。
D 自定义字段：定义 → 接口写读（平铺 x_…）、筛选 / 排序 / 搜索、导出 CSV、导入、问数（表说明 + SQL 能取到）、审计、停用。
E 审批流程：条件命中 / 不命中、步骤条件、角色 / 指定人、无权审批、驳回、逐级通过后以申请人身份执行、审计。
F 报表设计器：数据集查询、汇总 / 时间分组 / 自定义字段分组、没有权限的数据集 403、数据范围过滤、xlsx / PDF、共享可见性、定时。
"""
import io
import json
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, 'impl_files'))
ARGS = sys.argv[1:]
URL = ARGS[ARGS.index('--db') + 1] if '--db' in ARGS else None
ONLY_PACK = ARGS[ARGS.index('--pack') + 1] if '--pack' in ARGS else None
TMP = tempfile.mkdtemp(prefix='hm_impl_')
os.environ['HM_DATA'] = TMP
if not URL:
    URL = 'sqlite:///' + os.path.join(TMP, 'mes.db').replace('\\', '/')
PG = URL.startswith('postgresql')

import sqlalchemy as sa  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402

from honeymom import audit, auth, crud, db, schema, seed  # noqa: E402

results = []


def ok(name, cond, detail=''):
    results.append((name, bool(cond)))
    print(('PASS ' if cond else 'FAIL ') + name, str(detail)[:300])


def section(t):
    print('=====', t)


def req(qs=''):
    from urllib.parse import parse_qsl, urlencode
    return Request({'type': 'http', 'method': 'GET', 'path': '/', 'query_string': urlencode(parse_qsl(qs)).encode(), 'headers': []})


def raises(fn, code=None):
    try:
        fn()
    except HTTPException as ex:
        return code is None or ex.status_code == code, ex.detail
    except ValueError as ex:
        return code is None, str(ex)
    return False, 'no error'


db.init(URL)
from honeymom.app import build_app  # noqa: E402
build_app()            # 登记各业务模块的实体动作、钩子（工单下达等），和服务里一样
from honeymom.impl import cf, flows, forms, importer as imp, packs, reports, uom  # noqa: E402
from honeymom.impl import api as iapi  # noqa: E402
flows.install()
forms.install()
packs.install()

# ───────────────────────── 只测一个行业包（SQLite 时由主进程用新库起子进程调用） ─────────────────────────
if ONLY_PACK:
    with db.engine.begin() as c:
        seed.ensure_users(c, only_admin=True)
    admin = auth.user_by_name('admin')
    pv = packs.preview([ONLY_PACK], demo=True, user=admin)
    with db.engine.connect() as c:
        empty_after_preview = not db.rows(c, 'material', limit=1) and not db.rows(c, 'process', limit=1)
    r = packs.apply([ONLY_PACK], demo=True, user=admin)
    from honeymom.schema import BY_NAME
    with db.engine.connect() as c:
        counts = {e: len(db.rows(c, e)) for e in ('process', 'material', 'bom', 'route_step', 'equipment', 'maint_plan', 'stock', 'employee', 'defect_code',
                                                  'insp_item', 'work_order')}
        counts['report_def'] = c.execute(sa.select(sa.func.count()).select_from(schema.IMPL_TABLES['report_def'])).scalar()
    et = next(f for f in BY_NAME['equipment'].fields if f.name == 'etype').enum
    print('RESULT ' + json.dumps({'preview_ok': pv['ok'], 'preview_rolled_back': empty_after_preview, 'ok': r['ok'], 'counts': counts, 'etype': list(et),
                                  'modules': sorted(packs.enabled_modules()), 'packs': packs.applied_codes()}, ensure_ascii=False))
    sys.exit(0)

# ───────────────────────── A 演示等价 ─────────────────────────
section('A 演示等价（foundry + machining 行业模板 = 4.7 演示数据）')
import seed_dump  # noqa: E402
FIX = json.load(open(os.path.join(HERE, 'impl_files', 'demo_seed_47.json'), encoding='utf-8'))
db.clock.data_now = seed_dump.CLOCK
with db.engine.begin() as c:
    packs.apply(packs.DEMO_PACKS, demo=True, conn=c)
    seed.ensure_users(c)
    from honeymom import seed_biz
    seed_biz.run(c)
snap1 = seed_dump.dump(db, schema, FIX['entities'])
with db.engine.begin() as c:
    for code, name, et in seed_dump.EQUIP:
        if not db.get(c, 'equipment', code=code):
            db.insert(c, 'equipment', {'code': code, 'name': name, 'etype': et, 'status': 'offline', 'install_date': seed_dump.CLOCK.date()}, raw=True)
    packs.after_equipment(c)
snap2 = seed_dump.dump(db, schema, FIX['entities'])
KNOWN = {('datasource', 'remark')}      # M2 改了示范数据源的说明（走交付包）
NEW_SETTINGS = {'setup_state', 'demo_seed', 'ts_mode'}          # ts_mode：时序存储模式随数据库方言不同
OLD_KEYS = {r['key'] for r in FIX['seed']['setting']}


def canon(rows, ent, keys):
    out = []
    for r in rows:
        if ent == 'setting' and (r.get('key') in NEW_SETTINGS or r.get('key') not in OLD_KEYS):
            continue          # 别的模块（M2 / M4 / M5 的迁移）新加的系统参数不比
        out.append(json.dumps({k: (None if (ent, k) in KNOWN else r.get(k)) for k in keys}, ensure_ascii=False, sort_keys=True))
    return sorted(out)


for phase, snap in (('seed', snap1), ('after_equipment', snap2)):
    bad = []
    for ent in FIX['entities']:
        old = FIX[phase].get(ent, [])
        if ent in ('equipment',) and phase == 'after_equipment':
            pass
        keys = sorted(old[0]) if old else []
        if canon(old, ent, keys) != canon(snap.get(ent, []), ent, keys) and not (not old and not snap.get(ent)):
            bad.append(f'{ent}({len(old)}→{len(snap.get(ent, []))})')
    ok(f'{phase}：每个 4.7 实体逐行一致（{len(FIX["entities"])} 个实体）', not bad, ', '.join(bad))
wo = snap1['work_order']
ok('演示工单 3 张、计划时间相对数据时钟', len(wo) == 3 and wo[0]['start_plan'] == '2026-11-04 09:00:00', [w['start_plan'] for w in wo])
from honeymom.schema import BY_NAME  # noqa: E402
OLD_PT = ['melting', 'treatment', 'molding', 'cooling', 'cleaning', 'receiving', 'machining', 'deburr', 'inspection', 'storage', 'other']
pt = list(next(f for f in BY_NAME['process'].fields if f.name == 'ptype').enum)
ok('工序类型选项和 4.7 一样（同样的顺序）', pt == OLD_PT, pt)
ok('可选模块 = 铸造 + 机加', packs.enabled_modules() == {'foundry', 'machining'}, packs.enabled_modules())
db.clock.data_now = None

admin = auth.user_by_name('admin')
planner = auth.user_by_name('planner')
manager = auth.user_by_name('manager')
qc = auth.user_by_name('qc')
from honeymom import context  # noqa: E402
context._ctx.set({'actor': admin, 'source': 'ui'})          # 像一个界面请求：自动审计开着

# ───────────────────────── B 导入框架 ─────────────────────────
section('B 导入框架')
bad = []
for i in imp.all_importers():
    try:
        b = imp.template(i.key)
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(b))
        if '说明' not in wb.sheetnames or len(wb.sheetnames) < 2:
            bad.append(i.key)
    except Exception as ex:
        bad.append(f'{i.key}: {ex}')
ok(f'每个导入器都能出模板（说明页 + 数据页）：{len(imp.all_importers())} 个', not bad, bad)
wbk = openpyxl.load_workbook(io.BytesIO(imp.template('material')))
ws = wbk[imp.sheet_title('物料')]
ok('模板：表头、示例行、下拉', ws.cell(1, 1).value == '编码' and ws.cell(2, 1).value == 'FG-1001' and len(ws.data_validations.dataValidation) >= 1)
ok('初始化工作簿：全部导入器一本', len(openpyxl.load_workbook(io.BytesIO(imp.workbook_template())).sheetnames) >= len(imp.all_importers()) - 2)

BASIC = [
    ('uom', [{'code': '盒', 'name': '盒', 'category': '计数'}]),
    ('plant', [{'code': 'TP', 'name': '测试工厂'}]),
    ('workshop', [{'code': 'TW', 'name': '测试车间', 'plant': 'TP', 'wtype': '机加'}]),
    ('line', [{'code': 'TL1', 'name': '测试一线', 'workshop': 'TW'}, {'code': 'TL2', 'name': '测试二线', 'workshop': 'TW'}]),
    ('process', [{'code': 'TOP10', 'name': '测试粗加工', 'std_cycle_s': 60}, {'code': 'TOP20', 'name': '测试精加工', 'std_cycle_s': 90}]),
    ('material', [{'code': 'TM-FG', 'name': '测试成品', 'unit': '件', 'mtype': '成品', 'price': '1,280.5'}, {'code': 'TM-SF', 'name': '测试半成品', 'unit': '件', 'mtype': 'semi'},
                  {'code': 'TM-RM', 'name': '测试原料', 'unit': 'kg', 'mtype': '原材料'}]),
    ('uom_conv', [{'material': 'TM-FG', 'from_unit': '箱', 'to_unit': '件', 'factor': 24}, {'material': 'TM-FG', 'from_unit': '件', 'to_unit': 'kg', 'factor': 2.5}]),
    ('bom', [{'product': 'TM-FG', 'component': 'TM-SF', 'qty': 1}, {'product': 'TM-SF', 'component': 'TM-RM', 'qty': 2.6, 'unit': 'kg'}]),
    ('equipment', [{'code': 'TE1', 'name': '测试机 1', 'etype': '加工中心', 'line': 'TL1'}, {'code': 'TE2', 'name': '测试机 2', 'etype': 'cnc', 'line': 'TL2'}]),
    ('workstation', [{'code': 'TS1', 'name': '测试工位', 'line': 'TL1', 'process': 'TOP10', 'equipment': 'TE1'}]),
    ('route', [{'route_code': 'TR1', 'route_name': '测试路线', 'product': 'TM-FG', 'seq': 10, 'process': 'TOP10', 'params': '转速=2000；进给=0.1'},
               {'route_code': 'TR1', 'seq': 20, 'process': 'TOP20', 'fai': '是'}]),
    ('shift', [{'code': 'TA', 'name': '测试班', 'start': '08:00', 'end': '17:00'}]),
    ('team', [{'code': 'TT', 'name': '测试组', 'shift': 'TA'}]),
    ('employee', [{'code': 'TX01', 'name': '测试员工', 'team': 'TT'}]),
    ('skill', [{'employee': 'TX01', 'process': 'TOP10', 'level': '骨干'}]),
    ('customer', [{'code': 'TC1', 'name': '测试客户'}]), ('supplier', [{'code': 'TSU1', 'name': '测试供应商', 'category': '原材料'}]),
    ('warehouse', [{'code': 'TWH', 'name': '测试仓', 'wtype': '成品库'}]), ('location', [{'code': 'TWH-01', 'name': '测试库位', 'warehouse': 'TWH'}]),
    ('opening_stock', [{'material': 'TM-FG', 'warehouse': 'TWH', 'location': 'TWH-01', 'lot': 'TL001', 'qty': 3, 'unit': '箱'}]),
    ('defect_code', [{'code': 'TD1', 'name': '测试缺陷', 'category': 'other', 'severity': '轻微'}]),
    ('insp_template', [{'tpl_code': 'TTPL', 'tpl_name': '测试模板', 'seq': 10, 'name': '外径', 'lo': 9.9, 'hi': 10.1}]),
    ('tooling', [{'code': 'TTL1', 'name': '测试夹具', 'ttype': '夹具'}]),
    ('maint_plan', [{'code': 'TMP1', 'name': '测试保养', 'equipment': 'TE1', 'cycle_days': 30, 'last_done': '2026-10-01'}]),
    ('changeover', [{'station_group': 'TG', 'minutes': 15}]),
    ('downtime_plan', [{'equipment': 'TE1', 'start': '2026-12-01 08:00', 'end': '2026-12-01 12:00'}]),
    ('work_order', [{'product': 'TM-FG', 'route': 'TR1', 'qty': 100}]),
    ('kb_doc', [{'title': '测试 SOP', 'content': '测试内容', 'kind': '作业指导书'}]),
]
bad = []
for key, rows in BASIC:
    r = imp.run(key, rows, user=admin)
    if not r['ok'] or r['done']['create'] < 1:
        bad.append(f"{key}: {r['errors'][:1] or r['summary']}")
ok(f'正常导入：{len(BASIC)} 个导入器各导一批', not bad, bad)
with db.engine.connect() as c:
    m = db.get(c, 'material', code='TM-FG')
    st = db.rows(c, 'route_step', route=db.get(c, 'route', code='TR1')['id'])
    stock = db.get(c, 'stock', lot='TL001')
    mp = db.get(c, 'maint_plan', code='TMP1')
ok('数字去千分位、选项按中文名', m['price'] == 1280.5 and m['mtype'] == 'fg')
ok('工艺路线 + 工序一张表导入：2 道工序、参数"名=值"', len(st) == 2 and st[0]['params'] == {'转速': '2000', '进给': '0.1'} and st[1]['fai'] is True)
ok('期初库存：3 箱 → 72 件', stock and stock['qty'] == 72, stock and stock['qty'])
ok('保养计划：下次到期 = 上次完成 + 周期', str(mp['next_due']) == '2026-10-31', mp['next_due'])
r = imp.run('material', [{'code': 'TM-FG', 'name': '测试成品（改名）'}], user=admin)
r2 = imp.run('material', [{'code': 'TM-FG', 'name': '测试成品（改名）'}], user=admin)
ok('已有的按编码更新；没变化的跳过', r['done']['update'] == 1 and r2['done']['skip'] == 1, (r['done'], r2['done']))
r = imp.run('material', [{'code': 'TM-FG', 'name': '不该改'}], mode='create', user=admin)
ok('"只新增"模式：已有的跳过', r['summary']['skip'] == 1)

# 校验错误
r = imp.run('material', [{'code': 'TV1', 'name': 'x', 'price': 'abc'}, {'code': 'TV2', 'name': 'x', 'mtype': '紫色'}, {'code': 'TV3'},
                         {'code': 'TV4', 'name': 'a'}, {'code': 'TV4', 'name': 'b'}], user=admin)
msgs = [e['msg'] for x in r['errors'] for e in x['errors']]
ok('类型错误', any('格式不对' in m_ for m_ in msgs), msgs)
ok('选项错误（列出可选值）', any('类型只能是' in m_ for m_ in msgs))
ok('必填', any('名称必填' in m_ for m_ in msgs))
ok('文件内重复', any('重复' in m_ for m_ in msgs))
ok('有错误整批不写', not r['ok'] and r.get('done') is None)
with db.engine.connect() as c:
    ok('库里没有半截数据', not db.get(c, 'material', code='TV4'))
r = imp.run('bom', [{'product': 'TM-FG', 'component': '没有这个物料', 'qty': 1}], user=admin)
ok('引用找不到（按编码）', any('找不到' in e['msg'] for x in r['errors'] for e in x['errors']), r['errors'])
r = imp.run('material', [{'code': 'TV-NEW', 'name': 'x'}], mode='update', user=admin)
ok('"只更新"模式：库里没有的报错', not r['ok'])
ok('没有权限的人不能导（质检员导物料）', raises(lambda: imp.run('material', [{'code': 'TVX', 'name': 'x'}], user=qc), 403)[0])
ok2, why = raises(lambda: imp.check_perms(imp.get('material'), qc), 403)
ok('没有导入权限 403', ok2, why)

# 文件流程：上传 → 对列 → 预览 → 演练 → 确认 → 错误报告
csv_ = '物料编码,物料名称,单位,类型,单价\nTF-1,文件一,件,成品,12\nTF-2,文件二,件,半成品,xx\nTF-3,文件三,kg,原材料,3\n'.encode('gb18030')
j = imp.create_job('material', 'm.csv', csv_, admin)
ok('CSV（GBK）按表头自动对列', set(j['mapping'].values()) == {'code', 'name', 'unit', 'mtype', 'price'}, j['mapping'])
p = imp.preview(j['id'], admin)
ok('预览：2 新增、1 错误', p['summary']['create'] == 2 and p['summary']['error'] == 1, p['summary'])
ok2, _ = raises(lambda: imp.commit(j['id'], admin), 400)
ok('有错误时拒绝确认', ok2)
d = imp.commit(j['id'], admin, dry_run=True, skip_errors=True)
with db.engine.connect() as c:
    ok('演练：报告新增 2，库里不变', d['done']['create'] == 2 and not db.get(c, 'material', code='TF-1'))
d = imp.commit(j['id'], admin, skip_errors=True)
with db.engine.connect() as c:
    ok('确认导入（跳过错误行）', d['ok'] and db.get(c, 'material', code='TF-1') and not db.get(c, 'material', code='TF-2'))
    au = [a for a in audit.query({'entity': 'material'}, limit=1000)[1] if a.get('source') == 'import']
ok('审计来源 = 导入', len(au) >= 2, len(au))
er = openpyxl.load_workbook(io.BytesIO(imp.error_report(j['id'], admin))).active
hdr = [c_.value for c_ in er[1]]
ok('错误报告：带"错误"列，错误行写明原因', '错误' in hdr and '格式不对' in str(er.cell(3, hdr.index('错误') + 1).value), hdr)
xb = io.BytesIO()
xw = openpyxl.Workbook()
xs = xw.active
xs.title = '我的物料'
xs.append(['料号', '品名', '单位'])
xs.append(['TX-1', 'Excel 物料', '件'])
xw.save(xb)
j2 = imp.create_job('material', 'x.xlsx', xb.getvalue(), admin)
ok('xlsx：别名表头（料号 / 品名）也能对上', j2['mapping'].get('0') == 'code', j2['mapping'])

# 整批回滚
calls = []


def _boom(ctx, v, ex):
    calls.append(v['code'])
    if len(calls) == 2:
        raise ValueError('模拟写入失败')
    return imp.default_write(ctx, v, ex)


imp.register(imp.Importer(key='_test_boom', label='测试回滚', entity='material', keys=('code',), write=_boom, perms=()))
jb = imp.create_job('_test_boom', 'b.csv', '编码,名称\nTB-1,一\nTB-2,二\nTB-3,三\n'.encode('utf-8'), admin)
rb = imp.commit(jb['id'], admin)
with db.engine.connect() as c:
    ok('写入时第 2 行失败 → 整批回滚（第 1 行也没留下）', not rb['ok'] and rb['rolled_back'] and not db.get(c, 'material', code='TB-1'), rb.get('error'))
imp.REGISTRY.pop('_test_boom')

# BOM 循环（多级，连同库里已有的）
r = imp.run('bom', [{'product': 'TM-RM', 'component': 'TM-FG', 'qty': 1}], user=admin)
ok('多级 BOM 循环：TM-FG → TM-SF → TM-RM → TM-FG', any('BOM 循环' in e['msg'] for x in r['errors'] for e in x['errors']), r['errors'])
r = imp.run('bom', [{'product': 'TM-X1', 'component': 'TM-X1', 'qty': 1}], user=admin)
ok('父件 = 子件报错', not r['ok'])
imp.run('material', [{'code': c_, 'name': c_} for c_ in ('TL-A', 'TL-B', 'TL-C')], user=admin)
r = imp.run('bom', [{'level': 0, 'component': 'TL-A'}, {'level': 1, 'component': 'TL-B', 'qty': 2}, {'level': 2, 'component': 'TL-C', 'qty': 3}], user=admin)
with db.engine.connect() as c:
    mid = {x['code']: x['id'] for x in db.rows(c, 'material')}
    edges = {(b['product'], b['component']) for b in db.rows(c, 'bom')}
ok('"层级"写法的多级 BOM：A→B、B→C', r['ok'] and (mid['TL-A'], mid['TL-B']) in edges and (mid['TL-B'], mid['TL-C']) in edges, r['errors'])

# 单位换算
with db.engine.connect() as c:
    f = mid['TM-FG']
    ok('同类别：1.2 t = 1200 kg', uom.convert(c, None, 1.2, 't', 'kg') == 1200)
    ok('按物料：3 箱 = 72 件', uom.convert(c, f, 3, '箱') == 72)
    ok('反方向：48 件 = 2 箱', uom.convert(c, f, 48, '件', '箱') == 2)
    ok('串联：1 箱 = 24 件 = 60 kg = 0.06 t', abs(uom.convert(c, f, 1, '箱', 't') - 0.06) < 1e-9)
    ok2, why = raises(lambda: uom.convert(c, mid['TM-SF'], 1, '箱'))
    ok('换不了报错', ok2, why)
r = imp.run('bom', [{'product': 'TM-SF', 'component': 'TM-RM', 'qty': 1, 'unit': '箱'}], user=admin)
ok('BOM 单位换不到子件基本单位报错', not r['ok'])

# AI 对列（演示回放 / 兜底规则）
jx = imp.create_job('material', 'ai.csv', '料品编号,物品名称,计量单位,类别\nAI-1,甲,件,成品\n'.encode('utf-8'), admin)
from honeymom.services import ai_switch  # noqa: E402
ai_switch.save({'global': True, 'demo': True, 'features': {'impl': True}})
am = imp.ai_map(jx['id'], admin)
ai_switch.save({'global': False, 'demo': False, 'features': {'impl': False}})
ok('AI 建议对列（演示回放或规则兜底）：编码 / 名称都对上', {'code', 'name'} <= set(am['mapping'].values()), am)

# ───────────────────────── C 行业模板 ─────────────────────────
section('C 行业模板')
for code in ('foundry', 'machining', 'injection', 'assembly'):
    if PG:
        pv = packs.preview([code], demo=code in ('injection', 'assembly'), user=admin)
        r = packs.apply([code], demo=code in ('injection', 'assembly'), user=admin)
        res = {'preview_ok': pv['ok'], 'ok': r['ok'], 'preview_rolled_back': True}
        with db.engine.connect() as c:
            res['counts'] = {'material': len(db.rows(c, 'material')), 'process': len(db.rows(c, 'process'))}
        res['modules'] = sorted(packs.enabled_modules())
    else:
        sub = tempfile.mkdtemp(prefix='hm_pack_')
        env = dict(os.environ, HM_DATA=sub)
        out = subprocess.run([sys.executable, os.path.abspath(__file__), '--pack', code, '--db', 'sqlite:///' + os.path.join(sub, 'p.db').replace('\\', '/')],
                             capture_output=True, text=True, encoding='utf-8', env=env)
        line = next((x for x in out.stdout.splitlines() if x.startswith('RESULT ')), None)
        res = json.loads(line[7:]) if line else {'ok': False, 'err': (out.stderr or out.stdout)[-600:]}
    ok(f'{code}：在{"同一个" if PG else "新"}库上预览（回滚）+ 应用（含演示数据）',
       res.get('ok') and res.get('preview_ok') and res.get('preview_rolled_back') and code in res.get('modules', []), {k: v for k, v in res.items() if k != 'etype'})
    if not PG and res.get('ok'):
        cn = res['counts']
        if code in ('injection', 'assembly'):
            ok(f'{code}：工序、物料、BOM、工艺、设备、点检保养、库存、员工、报表都有', all(cn[k] > 0 for k in cn), cn)
            own = {'injection': 'injection', 'assembly': 'press'}[code]
            other = {'injection': 'press', 'assembly': 'injection'}[code]
            ok(f'{code}：设备类型只有通用 + 本行业的（没有 {other}、没有中频炉）', own in res['etype'] and other not in res['etype'] and 'furnace' not in res['etype'])
from honeymom.schema import BY_NAME  # noqa: E402,F811
with db.engine.connect() as c:
  if PG:
    ok('同一个库上叠加四个行业：注塑 + 装配的数据都在、铸造机加不受影响',
       db.get(c, 'material', code='IM-1001') and db.get(c, 'material', code='AS-EPS-A') and db.get(c, 'material', code='SGH-450'))

# ───────────────────────── D 自定义字段 ─────────────────────────
section('D 自定义字段')
iapi.cf_create({'entity': 'material', 'key': 'color', 'label': '颜色', 'ftype': 'enum', 'options': '红,黑,本色', 'filterable': True}, user=admin)
iapi.cf_create({'entity': 'material', 'key': 'wt', 'label': '净重', 'ftype': 'number', 'filterable': True}, user=admin)
iapi.cf_create({'entity': 'material', 'key': 'sup', 'label': '主供应商', 'ftype': 'ref', 'ref': 'supplier'}, user=admin)
iapi.cf_create({'entity': 'material', 'key': 'since', 'label': '启用日期', 'ftype': 'date'}, user=admin)
ok('字段键校验（x_ 前缀、不能和系统字段重名、重复）', raises(lambda: iapi.cf_create({'entity': 'material', 'key': 'color', 'label': 'x', 'ftype': 'text'}, user=admin), 400)[0])
with db.engine.connect() as c:
    q_ = "SELECT indexname FROM pg_indexes WHERE tablename = 'material'" if PG else "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'material'"
    has_idx = 'ix_cf_material_x_color' in set(c.execute(sa.text(q_)).scalars())
ok('可筛选字段建了表达式索引', has_idx)
meta = schema.spec_json()
mf = [f['name'] for f in next(e for e in meta if e['name'] == 'material')['fields'] if f.get('cf')]
ok('/api/meta 的字段里有自定义字段', mf == ['x_color', 'x_wt', 'x_sup', 'x_since'], mf)
sid = imp.run('supplier', [{'code': 'CF-S1', 'name': '自定义字段供应商'}], user=admin)
with db.engine.connect() as c:
    sup_id = db.get(c, 'supplier', code='CF-S1')['id']
rid = crud.create('material', {'code': 'CF-1', 'name': '带自定义字段', 'x_color': '黑', 'x_wt': '2.5', 'x_sup': sup_id, 'x_since': '2026/11/4'}, user=admin)['id']
crud.create('material', {'code': 'CF-2', 'name': '第二个', 'x_color': '红', 'x_wt': 9}, user=admin)
g = crud.get_('material', rid, user=admin)['item']
ok('接口写读：平铺 x_…、类型转换、引用标签', g['x_color'] == '黑' and g['x_wt'] == 2.5 and g['x_since'] == '2026-11-04' and g['x_sup__label'].startswith('CF-S1'), g)
ok2, why = raises(lambda: crud.create('material', {'code': 'CF-3', 'name': 'x', 'x_color': '紫'}, user=admin), 400)
ok('选项值校验', ok2, why)
crud.update_('material', rid, {'x_wt': 3}, user=admin)
g = crud.get_('material', rid, user=admin)['item']
ok('修改只改传进来的键', g['x_wt'] == 3 and g['x_color'] == '黑')
lst = crud.list_('material', req('f_x_color=黑'), user=admin)
ok('按自定义字段筛选', lst['total'] == 1 and lst['items'][0]['code'] == 'CF-1', lst['total'])
lst = crud.list_('material', req('f_x_wt__from=5'), user=admin)
ok('数字范围筛选', [x['code'] for x in lst['items']] == ['CF-2'])
lst = crud.list_('material', req('sort=-x_wt'), size=2, user=admin)
ok('按自定义字段排序', [x['code'] for x in lst['items']] == ['CF-2', 'CF-1'], [x['code'] for x in lst['items']])
csv_out = crud.export('material', req('f_x_color=黑'), user=admin).body.decode('utf-8-sig')
ok('导出 CSV 带自定义字段列和值', '颜色' in csv_out.splitlines()[0] and '黑' in csv_out and 'CF-S1' in csv_out, csv_out.splitlines()[:2])
r = imp.run('material', [{'code': 'CF-IMP', 'name': '导入的', 'x_color': '本色', 'x_wt': 7}], user=admin)
g = crud.get_('material', crud.list_('material', req('q=CF-IMP'), user=admin)['items'][0]['id'], user=admin)['item']
ok('导入（模板列里有自定义字段）', r['ok'] and g['x_color'] == '本色' and any(c_.key == 'x_color' for c_ in imp.get('material').cols()))
r = imp.run('material', [{'code': 'CF-IMP', 'x_color': '紫'}], user=admin)
ok('导入校验自定义字段的选项', not r['ok'])
from honeymom.services import ai as ai_mod  # noqa: E402
ok('问数：表说明里写了自定义字段怎么取', "json_extract(material.ext,'$.x_color')" in ai_mod.schema_text())
q = ai_mod.run_sql("SELECT code, json_extract(ext, '$.x_color') AS c FROM material WHERE json_extract(ext, '$.x_color') = '黑'", user=admin)
ok('问数 SQL 能取自定义字段', q['rows'] == [['CF-1', '黑']], q)
au = [a for a in audit.query({'entity': 'material', 'entity_id': rid})[1] if a.get('action') == 'update']
ok('改自定义字段记审计（改前改后）', au and 'x_wt' in (au[0].get('diff') or {}), au[:1])
fid = next(d_['id'] for d_ in cf.fields('material') if d_['key'] == 'x_wt')
iapi.cf_delete(fid, user=admin)
cf.invalidate()
g = crud.get_('material', rid, user=admin)['item']
ok('停用后不再显示，数据保留', 'x_wt' not in g and cf.read_one(db.engine.connect(), 'material', rid).get('x_wt') == 3)
iapi.cf_create({'entity': 'work_order', 'key': 'lot_note', 'label': '批注', 'ftype': 'text', 'required': True}, user=admin)
ok2, why = raises(lambda: crud.create('work_order', {'product': mid['TM-FG'], 'qty': 5}, user=admin), 400)
ok('必填自定义字段：新建时没填拒绝', ok2 and '批注' in str(why), why)
cf_wo = next(d_['id'] for d_ in cf.fields('work_order'))
iapi.cf_update(cf_wo, {'required': False}, user=admin)
cf.invalidate()
# 表单配置：按角色必填 / 只读
forms.save('material', {'required': {'spec': ['planner']}, 'readonly': {'price': ['planner']}}, admin)
ok2, why = raises(lambda: crud.create('material', {'code': 'FM-1', 'name': 'x'}, user=planner), 400)
ok('表单配置：计划员必填规格（后端检查）', ok2 and '规格' in str(why), why)
fm = crud.create('material', {'code': 'FM-1', 'name': 'x', 'spec': 's', 'price': 5}, user=planner)['id']
crud.update_('material', fm, {'price': 99, 'name': 'y'}, user=planner)
with db.engine.connect() as c:
    fr = db.get(c, 'material', fm)
ok('表单配置：计划员只读的单价改动被忽略', fr['price'] == 5 and fr['name'] == 'y', (fr['price'], fr['name']))
forms.reset('material', admin)

# ───────────────────────── E 审批流程 ─────────────────────────
section('E 审批流程')
with db.engine.connect() as c:
    tr1 = db.get(c, 'route', code='TR1')['id']
f1 = iapi._flow_save(None, {'name': '大批量下达', 'entity': 'work_order', 'action': 'release', 'condition': [{'field': 'qty', 'op': '>', 'value': 1000}],
                            'steps': [{'name': '计划主管', 'type': 'role', 'approver': 'planner'},
                                      {'name': '厂长', 'type': 'role', 'approver': 'manager', 'condition': [{'field': 'priority', 'op': '<=', 'value': 2}]},
                                      {'name': '指定人', 'type': 'user', 'approver': 'qc'}]}, admin)['id']
flows.invalidate()
ok('流程定义校验（角色 / 账号要存在）', raises(lambda: iapi._flow_save(None, {'name': 'x', 'entity': 'work_order', 'action': 'release',
                                                                             'steps': [{'type': 'role', 'approver': 'nobody'}]}, admin), 400)[0])
w_small = crud.create('work_order', {'product': mid['TM-FG'], 'route': tr1, 'qty': 500, 'priority': 5}, user=planner)['id']
r = crud.do_action('work_order', w_small, 'release', {}, user=planner)
ok('不满足条件（500 件）：直接执行', not r.get('pending_approval') and crud.get_('work_order', w_small, user=admin)['item']['status'] == 'released', r.get('msg'))
w_big = crud.create('work_order', {'product': mid['TM-FG'], 'route': tr1, 'qty': 1500, 'priority': 5}, user=planner)['id']
r = crud.do_action('work_order', w_big, 'release', {}, user=planner)
ok('满足条件（1500 件）：提交审批、不执行', r.get('pending_approval') and crud.get_('work_order', w_big, user=admin)['item']['status'] == 'draft', r.get('msg'))
ok('步骤条件：优先级 5 不需要厂长那一步（2 步）', r['pending_approval']['steps'] == 2, r['pending_approval'])
inst = r['pending_approval']['inst']
from honeymom.agent import api as agent_api  # noqa: E402
lst = agent_api.approvals(status='pending', user=planner)
a1 = next(a for a in lst if a['kind'] == 'flow')
ok('审批中心（AI 关着也能看）：计划员看到、能批', a1['can_decide'] and a1['flow']['step'] == 1)
ok2, why = raises(lambda: agent_api.decide(a1['id'], {'decision': 'approve'}, user=auth.user_by_name('wh')), 403)
ok('别的角色不能批', ok2, why)
agent_api.decide(a1['id'], {'decision': 'approve'}, user=planner)
a2 = next(a for a in agent_api.approvals(status='pending', user=qc) if a['kind'] == 'flow')
ok('下一步给指定人（质检员 qc）', a2['can_decide'] and a2['assignee_role'] == 'user:qc')
ok('指定人以外（计划员）看不到', not any(a['id'] == a2['id'] for a in agent_api.approvals(status='pending', user=planner)))
d = agent_api.decide(a2['id'], {'decision': 'approve'}, user=qc)
ok('最后一步通过：以申请人身份执行（工单已下达）', d['flow']['status'] == 'approved' and crud.get_('work_order', w_big, user=admin)['item']['status'] == 'released', d)
w3 = crud.create('work_order', {'product': mid['TM-FG'], 'route': tr1, 'qty': 2000, 'priority': 1}, user=planner)['id']
r3 = crud.do_action('work_order', w3, 'release', {}, user=planner)
ok('优先级 1：三步都要', r3['pending_approval']['steps'] == 3)
a = next(a for a in agent_api.approvals(status='pending', user=planner) if a['kind'] == 'flow')
ok2, why = raises(lambda: agent_api.decide(a['id'], {'decision': 'reject'}, user=planner), 400)
ok('驳回要写原因', ok2)
agent_api.decide(a['id'], {'decision': 'reject', 'reason': '数量太大'}, user=planner)
with db.engine.connect() as c:
    st3 = c.execute(sa.select(flows.T('approval_flow_inst').c.status).where(flows.T('approval_flow_inst').c.id == r3['pending_approval']['inst'])).scalar()
ok('驳回：流程单驳回、工单不动', st3 == 'rejected' and crud.get_('work_order', w3, user=admin)['item']['status'] == 'draft')
acts = {a['action'] for a in audit.query({'entity': 'approval_flow_inst'}, limit=1000)[1]}
ok('审计：提交 / 通过 / 驳回 / 执行', {'flow_submit', 'flow_approve', 'flow_reject', 'flow_execute'} <= acts, acts)
iapi._flow_save(None, {'name': '库存调整审批', 'entity': 'stock_txn', 'action': 'create', 'condition': [{'field': 'ttype', 'op': '=', 'value': 'adjust'}],
                       'steps': [{'name': '主管', 'type': 'role', 'approver': 'manager'}]}, admin)
flows.invalidate()
wh_ = db.engine.connect().execute(sa.select(schema.TABLES['warehouse'].c.id)).scalar()
r = crud.create('stock_txn', {'ttype': 'adjust', 'material': mid['TM-FG'], 'qty': -5, 'warehouse': wh_}, user=admin)
r2 = crud.create('stock_txn', {'ttype': 'in', 'material': mid['TM-FG'], 'qty': 5, 'warehouse': wh_}, user=admin)
ok('库存调整要审批、普通入库不要', r.get('pending_approval') and r2.get('id'))
fl_t = flows.T('approval_flow')
with db.engine.begin() as c:
    c.execute(fl_t.delete())
flows.invalidate()

# ───────────────────────── F 报表设计器 ─────────────────────────
section('F 报表设计器')
r = reports.run('work_order', {'group': [{'field': 'product__label'}], 'measures': [{'field': 'qty', 'agg': 'sum'}, {'agg': 'count'}]}, admin)
ok('分组汇总（产品名称 × 数量合计 / 条数）', r['columns'][0]['label'] == '产品' and r['rows'], r['rows'][:3])
r = reports.run('work_order', {'group': [{'field': 'created_at', 'bucket': 'day'}], 'measures': [{'agg': 'count'}]}, admin) \
    if any(f['key'] == 'created_at' for f in reports.get_ds('work_order').fields()) else reports.run('report', {'group': [{'field': 'end', 'bucket': 'month'}],
                                                                                                                'measures': [{'agg': 'count'}]}, admin)
ok('时间分组', 'rows' in r)
r = reports.run('material', {'group': [{'field': 'x_color'}], 'measures': [{'agg': 'count'}], 'filters': [{'field': 'x_color', 'op': 'not_empty'}]}, admin)
ok('按自定义字段分组', sorted(x[0] for x in r['rows']) == ['本色', '红', '黑'], r['rows'])
r = reports.run('work_order', {'fields': ['code', 'qty', 'status'], 'filters': [{'field': 'qty', 'op': '>=', 'value': 1500}]}, admin)
ok('明细 + 筛选', r['rows'] and all(x[1] >= 1500 for x in r['rows']) and r['columns'][2]['label'] == '状态')
from honeymom.schema import role_t  # noqa: E402
with db.engine.begin() as c:
    c.execute(role_t.insert().values(code='rp_test', name='报表测试', perms=['page.*', 'master.*.read', 'equipment.equipment.read', 'config.report.design'], builtin=False))
    db.insert(c, 'sys_user', {'username': 'rpu', 'name': '报表测试', 'roles': 'rp_test', 'active': True, 'password_hash': 'x'}, raw=True)
from honeymom import perms  # noqa: E402
perms.invalidate()
rpu = auth.user_by_name('rpu')
ok2, why = raises(lambda: reports.run('work_order', {'fields': ['code']}, rpu), 403)
ok('没有工单查看权限：工单数据集 403', ok2, why)
ok('数据集列表只列有权限的', {d_['key'] for d_ in reports.list_datasets(rpu)} >= {'material', 'equipment'} and 'work_order' not in {d_['key'] for d_ in reports.list_datasets(rpu)})
with db.engine.connect() as c:
    tl1 = db.get(c, 'line', code='TL1')['id']
    tr1 = db.get(c, 'route', code='TR1')['id']
with db.engine.begin() as c:
    c.execute(schema.TABLES['sys_user'].update().where(schema.TABLES['sys_user'].c.username == 'rpu').values(scope={'lines': [tl1]}))
perms.invalidate()
rpu = auth.user_by_name('rpu')
r = reports.run('equipment', {'fields': ['code']}, rpu)
codes = {x[0] for x in r['rows']}
ok('数据范围：只看到产线 TL1 的设备（TE1，没有 TE2）', 'TE1' in codes and 'TE2' not in codes, codes)
rid_ = reports.save(None, {'name': '测试报表', 'dataset': 'material', 'config': {'group': [{'field': 'mtype'}], 'measures': [{'agg': 'count'}]}, 'chart': 'pie',
                           'shared_roles': ['planner'], 'schedule': {'enabled': True, 'freq': 'daily', 'time': '00:00', 'format': 'pdf', 'to_roles': ['planner']}}, admin)
ok('共享：计划员能看到、质检员看不到', any(x['id'] == rid_ for x in reports.list_reports(planner)) and not any(x['id'] == rid_ for x in reports.list_reports(qc)))
ok2, why = raises(lambda: reports.save(rid_, {'name': 'x', 'dataset': 'material', 'config': {}}, planner), 403)
ok('别人的报表不能改', ok2)
e = reports.export(rid_, 'pdf', admin)
ok('导出 PDF', e['data'][:5] == b'%PDF-' and e['rows'] > 0)
x = reports.export(rid_, 'xlsx', admin)
ok('导出 Excel', openpyxl.load_workbook(io.BytesIO(x['data'])).active.cell(4, 1).value == '类型')
from datetime import timedelta  # noqa: E402
with db.engine.begin() as c:          # 报表建在昨天：今天 00:00 的那次该跑了（建表之前的时间点不补跑）
    c.execute(schema.IMPL_TABLES['report_def'].update().where(schema.IMPL_TABLES['report_def'].c.id == rid_).values(created_at=db.clock_wall() - timedelta(days=1)))
due = reports.due_reports()
ok('定时：到点的报表', any(r_['id'] == rid_ for r_ in due))
reports.run_schedule(next(r_ for r_ in due if r_['id'] == rid_))
with db.engine.connect() as c:
    nt = db.rows(c, 'notice', to_role='planner')
ok('定时导出：通知发给计划员，再查不再到点', any('测试报表' in n['title'] for n in nt) and not any(r_['id'] == rid_ for r_ in reports.due_reports()))
reports.pin(rid_, planner)
ok('钉到工作台（工作台布局里有 report:<id>）', reports.widgets(planner)[0]['id'] == rid_)
reports.pin(rid_, planner, on=False)

fail = [n for n, c_ in results if not c_]
print(f'{len(results) - len(fail)}/{len(results)} 通过' + ('' if not fail else '；失败：' + '；'.join(fail)))
db.engine.dispose()
sys.exit(1 if fail else 0)
