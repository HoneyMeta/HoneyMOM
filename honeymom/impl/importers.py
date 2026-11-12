"""上线前要导的全部主数据（按开局导入顺序）：单位 → 组织（工厂 / 车间 / 产线）→ 工序 → 物料 → 单位换算 → BOM → 设备 → 工位 → 工艺路线 →
班次 / 班组 / 员工 / 技能 → 客户 / 供应商 → 仓库 / 库位 → 期初库存 → 缺陷代码 / 检验模板 → 模具夹具 → 点检保养计划 → 换型时间；
铸造模块：炉料；机加模块：刀具台账。说明见 docs/dev/import.md。"""
from ..i18n import _tr
from .. import i18n as _i18n
from datetime import timedelta

import sqlalchemy as sa

from .. import db
from ..schema import BY_NAME, TABLES
from . import importer as I
from . import uom
from .importer import Col, Importer, entity_columns, register


def ent(entity, order, keys=('code',), **kw):
    return register(Importer(key=kw.pop('key', entity), label=kw.pop('label', BY_NAME[entity].label), entity=entity, keys=keys, order=order, **kw))


def _null_find(entity, keys):
    """唯一键里允许有空值（空 = 匹配空）的查找。"""
    def find(ctx, v):
        t = TABLES[entity]
        q = sa.select(t)
        for k in keys:
            q = q.where(t.c[k].is_(None) if v.get(k) in (None, '') else t.c[k] == v[k])
        r = ctx.conn.execute(q.limit(1)).mappings().first()
        return dict(r) if r else None
    return find


def _positive(*cols):
    def chk(ctx, v, row):
        lab = {f.name: f.label for f in BY_NAME[ctx.imp.entity].fields}
        return [_tr('{get}要大于 0', get=lab.get(c, c)) for c in cols if v.get(c) is not None and v[c] <= 0]
    return chk


# ───────────────────────── 单位 ─────────────────────────
ent('uom', 10, group='基础', doc='计量单位：同一类别（重量、长度、体积…）里按"换算到基准"换算，基准单位填 1（kg=1、t=1000、g=0.001）。计数单位（件、箱）之间的换算在「单位换算」里按物料填。',
    rules=['编码就是单据上写的单位（如 kg、件、箱）'],
    examples=[{'code': '箱', 'name': '箱', 'category': '计数', 'ratio': 1, 'precision': 0}])


def _conv_check(ctx, v, row):
    errs = []
    if v.get('factor') is not None and v['factor'] <= 0:
        errs.append(_tr('换算系数要大于 0'))
    if v.get('from_unit') and v.get('to_unit') and uom.norm(v['from_unit']) == uom.norm(v['to_unit']):
        errs.append(_tr('从单位和到单位相同'))
    if v.get('material') and v.get('to_unit'):
        base = uom.base_unit(ctx.conn, v['material'])
        if base and uom.norm(base) != uom.norm(v['to_unit']) and uom.norm(base) != uom.norm(v['from_unit']):
            row.warn(_tr('物料的基本单位是 {base}，这条换算没有直接到基本单位（可以串起来用）', base=base))
    return errs


ent('uom_conv', 27, keys=('material', 'from_unit', 'to_unit'), group='物料', find=_null_find('uom_conv', ('material', 'from_unit', 'to_unit')),
    check_row=_conv_check, depends=('material',),
    doc='物料自己的单位换算：1 从单位 = 换算系数 × 到单位。到单位一般是物料的基本单位（物料主数据里的"单位"）。物料空着 = 所有物料通用。',
    rules=['同一物料的同一对单位只能有一条', '反方向不用再填（1 件 = 1/24 箱 自动算）'],
    examples=[{'material': 'FG-1001', 'from_unit': '箱', 'to_unit': '件', 'factor': 24, 'remark': '标准纸箱 24 件/箱'}])


# ───────────────────────── 组织 / 工序 / 物料 ─────────────────────────
ent('plant', 20, group='组织', examples=[{'code': 'P1', 'name': '一工厂', 'address': '郑州市经开区第八大街 18 号'}], exclude=('vf_source',))
ent('workshop', 21, group='组织', depends=('plant',), examples=[{'code': 'WS-M', 'name': '机加车间', 'plant': 'P1'}])
ent('line', 22, group='组织', depends=('workshop',), examples=[{'code': 'L1', 'name': '壳体加工线', 'workshop': 'WS-M'}])
ent('process', 24, group='工艺', doc='工序：工艺路线里每一步用哪道工序；标准节拍用于排产和产能。',
    examples=[{'code': 'OP10', 'name': '粗加工', 'std_cycle_s': 120, 'desc': '粗铣基准面、钻孔'}])


def _mat_check(ctx, v, row):
    if v.get('unit') and ctx.state.setdefault('has_uom', ctx.conn.execute(sa.select(TABLES['uom'].c.id).limit(1)).first() is not None) \
            and not uom.known(ctx.conn, v['unit']):
        row.warn(_tr('单位 {unit} 不在「计量单位」里（可以先导单位，换算才用得上）', unit=v["unit"]))
    errs = []
    for k in ('safety_stock', 'price', 'weight_kg'):
        if v.get(k) is not None and v[k] < 0:
            errs.append(_tr('{next}不能是负数', next=next(f.label for f in BY_NAME['material'].fields if f.name == k)))
    return errs


ent('material', 26, group='物料', check_row=_mat_check, depends=('uom',),
    doc='物料主数据：原材料、半成品、成品、辅料、易耗品。"单位"是基本单位，库存、BOM 用量都按它记。',
    rules=['编码唯一；已有的编码按"导入方式"更新', '单价、单重、安全库存不能是负数'],
    examples=[{'code': 'FG-1001', 'name': '转向器壳体 A 型', 'spec': 'QT450-10 精加工', 'unit': '件', 'mtype': '成品', 'safety_stock': 200, 'price': 380,
               'weight_kg': 15.2}])


# ───────────────────────── BOM（多级） ─────────────────────────
def _bom_prepare(ctx, recs):
    """多级 BOM 两种写法都认：① 每行写父件 + 子件；② 写"层级"（0 = 顶层产品，1 = 它的子件，2 = 子件的子件……），父件留空按上一层推。"""
    if not any(r.get('level') not in (None, '') for r in recs):
        return recs
    stack: dict = {}
    out = []
    for r in recs:
        lv = r.get('level')
        try:
            lv = int(float(lv)) if lv not in (None, '') else None
        except (TypeError, ValueError):
            out.append(r)
            continue
        if lv is None:
            out.append(r)
            continue
        if r.get('component') not in (None, ''):
            stack[lv] = r['component']
            for k in [k for k in stack if k > lv]:
                stack.pop(k)
        if lv == 0:
            if r.get('qty') in (None, ''):        # 顶层产品本身这一行：不是用量，跳过
                r = {k: (None if k not in ('__n',) else v) for k, v in r.items()}
            out.append(r)
            continue
        if r.get('product') in (None, '') and (lv - 1) in stack:
            r = {**r, 'product': stack[lv - 1]}
        out.append(r)
    return out


def _bom_check(ctx, v, row):
    errs = []
    if v.get('product') and v.get('product') == v.get('component'):
        errs.append(_tr('父件和子件是同一个物料'))
    if v.get('qty') is not None and v['qty'] <= 0:
        errs.append(_tr('用量要大于 0'))
    if v.get('unit') and v.get('component'):
        base = uom.base_unit(ctx.conn, v['component'])
        if base and uom.factor(ctx.conn, v['component'], v['unit'], base) is None:
            errs.append(_tr('子件基本单位是 {base}，没有 {unit} → {base} 的换算', base=base, unit=v["unit"]))
    return errs


def _bom_cycles(ctx, rows):
    """多级 BOM 不能有循环（A 用 B、B 又用 A，或更长的环）：库里已有的 BOM + 文件里的一起查。"""
    t = TABLES['bom']
    g: dict = {}
    for p, c in ctx.conn.execute(sa.select(t.c.product, t.c.component)):
        g.setdefault(p, set()).add(c)
    mine = {}
    for r in rows:
        if r.errors:
            continue
        p, c = r.values.get('product'), r.values.get('component')
        if p and c:
            g.setdefault(p, set()).add(c)
            mine.setdefault((p, c), []).append(r)
    color: dict = {}
    cycles = []

    def dfs(u, path):
        color[u] = 1
        path.append(u)
        for w in g.get(u, ()):
            if color.get(w) == 1:
                cycles.append(path[path.index(w):] + [w])
            elif not color.get(w):
                dfs(w, path)
        path.pop()
        color[u] = 2
    import sys
    sys.setrecursionlimit(max(5000, sys.getrecursionlimit()))
    for u in list(g):
        if not color.get(u):
            dfs(u, [])
    if not cycles:
        return
    mt = TABLES['material']
    ids = {x for cyc in cycles for x in cyc}
    code = dict(ctx.conn.execute(sa.select(mt.c.id, mt.c.code).where(mt.c.id.in_(ids))).all())
    for k, v in ctx.created.items():
        if k[0] == 'material':
            code[v] = k[1]
    for cyc in cycles:
        txt = ' → '.join(str(code.get(x, f'#{x}')) for x in cyc)
        hit = False
        for a, b in zip(cyc, cyc[1:]):
            for r in mine.get((a, b), []):
                if not any(_i18n.matches(e['msg'], 'BOM 循环：{txt}') for e in r.errors):
                    r.err(_tr('BOM 循环：{txt}', txt=txt))
                hit = True
        if not hit:
            ctx.notes.append(_tr('库里已有的 BOM 有循环：{txt}', txt=txt))


register(Importer(key='bom', label='BOM（多级）', entity='bom', keys=('product', 'component'), order=30, group='物料',
                  columns=lambda: [Col('level', _tr('层级'), 'int', hint=_tr('可选：0 = 顶层产品，1 = 子件，2 = 子件的子件；填了层级父件可以不填'), aliases=(_tr('层次'), 'level', _tr('阶'))),
                                   *entity_columns('bom')],
                  prepare=_bom_prepare, check_row=_bom_check, check_all=_bom_cycles, depends=('material',),
                  write=lambda ctx, v, ex: I.default_write(ctx, {k: x for k, x in v.items() if k != 'level'}, ex),
                  doc='BOM：每行一个"父件 → 子件 用量"。多级 BOM 照样一行一对（半成品既是上一级的子件、又是下一级的父件），或者用"层级"列按缩进写。用量按子件的单位。',
                  rules=['父件 + 子件唯一', '用量 > 0', '不能有循环（A 用 B、B 又用 A）：连同库里已有的 BOM 一起检查', '单位和子件基本单位不同时要有单位换算'],
                  examples=[{'product': 'FG-1001', 'component': 'SF-1001', 'qty': 1, 'unit': '件', 'remark': '毛坯'}]))


# ───────────────────────── 设备 / 工位 ─────────────────────────
ent('equipment', 31, group='设备', depends=('line',), exclude=('source',),
    doc='设备台账（设备类型模板、采集点在「设备接入」里配）。导入设备后，行业模板里按设备类型的点检保养计划会自动建上。',
    examples=[{'code': 'CNC1', 'name': '1# 加工中心', 'etype': '加工中心', 'line': 'L1', 'model': 'VMC-850', 'vendor': '某机床厂', 'ideal_cycle_s': 420}])
ent('workstation', 32, group='组织', depends=('line', 'process', 'equipment'),
    examples=[{'code': 'ST-CNC1', 'name': 'CNC1 工位', 'line': 'L1', 'process': 'OP10', 'equipment': 'CNC1', 'group': 'CNC'}])


# ───────────────────────── 工艺路线（含步骤） ─────────────────────────
RS = BY_NAME['route_step']


def _route_cols():
    st = next(f for f in BY_NAME['route'].fields if f.name == 'status').enum
    rm = next(f for f in RS.fields if f.name == 'report_mode').enum
    return [Col('route_code', _tr('工艺路线编码'), 'str', req=True, aliases=(_tr('路线编码'), _tr('工艺路线'), _tr('路线'), _tr('工艺编码'))),
            Col('route_name', _tr('工艺路线名称'), 'str', aliases=(_tr('路线名称'), _tr('工艺名称')), hint=_tr('同一路线的第一行填就行')),
            Col('product', _tr('产品'), 'ref', ref='material', aliases=(_tr('产品编码'), _tr('物料编码'))), Col('version', _tr('版本'), hint=_tr('缺省 V1')),
            Col('status', _tr('状态'), 'enum', enum=st, hint=_tr('缺省 生效')),
            Col('seq', _tr('顺序'), 'int', req=True, aliases=(_tr('工序号'), _tr('工步'), _tr('序号'), _tr('步骤'))),
            Col('process', _tr('工序'), 'ref', ref='process', req=True, aliases=(_tr('工序编码'),)),
            Col('station_group', _tr('工位组/工位'), aliases=(_tr('工位组'), _tr('工位'), _tr('设备组'))), Col('std_cycle_s', _tr('标准节拍 s'), 'float', aliases=(_tr('节拍'), _tr('标准工时'), _tr('节拍s'))),
            Col('inspection', _tr('检验点'), 'bool'), Col('report_mode', _tr('报工方式'), 'enum', enum=rm), Col('fai', _tr('首检放行'), 'bool', aliases=(_tr('首检'),)),
            Col('instruction', _tr('作业指示'), 'text', aliases=(_tr('作业指导'), _tr('操作要点'))),
            Col('params', _tr('工艺参数'), 'json', hint=_tr('名=值；名=值，如 主轴转速 rpm=2800；进给 mm/r=0.15'), aliases=(_tr('参数'),))]


def _route_find(ctx, v):
    rt, st = TABLES['route'], TABLES['route_step']
    rid = ctx.conn.execute(sa.select(rt.c.id).where(rt.c.code == v['route_code'])).scalar() or ctx.created.get(('route', v['route_code']))
    if not rid:
        return None
    r = ctx.conn.execute(sa.select(st).where((st.c.route == rid) & (st.c.seq == v['seq']))).mappings().first()
    return dict(r) if r else None


def _route_all(ctx, rows):
    by: dict = {}
    for r in rows:
        if r.values.get('route_code'):
            by.setdefault(r.values['route_code'], []).append(r)
    rt = TABLES['route']
    for code, rs in by.items():
        head = next((r for r in rs if r.values.get('route_name')), None)
        exists = ctx.conn.execute(sa.select(rt.c.id).where(rt.c.code == code)).scalar()
        if not head and not exists:
            for r in rs:
                r.err(_tr('新的工艺路线 {code} 要填工艺路线名称（第一行填就行）', code=code))
        names = {r.values.get('route_name') for r in rs if r.values.get('route_name')}
        prods = {r.values.get('product') for r in rs if r.values.get('product')}
        if len(names) > 1 or len(prods) > 1:
            for r in rs[1:]:
                r.err(_tr('同一工艺路线 {code} 的名称 / 产品前后不一致', code=code))
        seqs = [r.values.get('seq') for r in rs if r.values.get('seq') is not None]
        if any(s is not None and s <= 0 for s in seqs):
            for r in rs:
                if r.values.get('seq') is not None and r.values['seq'] <= 0:
                    r.err(_tr('顺序要大于 0'))
        if seqs != sorted(seqs):
            rs[0].warn(_tr('工艺路线 {code} 的工序没有按顺序排（按"顺序"列的数字执行）', code=code))
        if exists:
            st = TABLES['route_step']
            have = set(ctx.conn.execute(sa.select(st.c.seq).where(st.c.route == exists)).scalars())
            extra = have - set(seqs)
            if extra:
                ctx.notes.append(_tr('工艺路线 {code} 在库里还有顺序 {sorted} 的工序不在文件里（保留不动）', code=code, sorted=sorted(extra)))


def _route_write(ctx, v, existing):
    rt = TABLES['route']
    code = v['route_code']
    head = {'code': code, 'name': v.get('route_name'), 'product': v.get('product'), 'version': v.get('version'), 'status': v.get('status')}
    rid = ctx.created.get(('route', code)) or ctx.conn.execute(sa.select(rt.c.id).where(rt.c.code == code)).scalar()
    seen = ctx.state.setdefault('route_head', set())
    if not rid:
        rid = db.insert(ctx.conn, 'route', {k: x for k, x in head.items() if x is not None})
        ctx.remember('route', code, rid)
        seen.add(code)
    elif code not in seen:
        seen.add(code)
        cur = db.get(ctx.conn, 'route', rid)
        ch = {k: x for k, x in head.items() if x is not None and k != 'code' and cur.get(k) != x}
        if ch:
            db.update(ctx.conn, 'route', rid, ch)
    step = {k: v.get(k) for k in ('seq', 'process', 'station_group', 'std_cycle_s', 'inspection', 'report_mode', 'fai', 'instruction', 'params')}
    if existing:
        ch = {k: x for k, x in step.items() if x is not None and I._differs(existing, k, x)}
        if not ch:
            return 'skip'
        db.update(ctx.conn, 'route_step', existing['id'], ch)
        return 'update'
    db.insert(ctx.conn, 'route_step', {'route': rid, **{k: x for k, x in step.items() if x is not None}})
    return 'create'


register(Importer(key='route', label='工艺路线（含工序）', entity=None, columns=_route_cols, keys=('route_code', 'seq'), order=35, group='工艺',
                  find=_route_find, write=_route_write, check_all=_route_all, depends=('material', 'process'),
                  perms=('master.route.create', 'master.route.update', 'master.route_step.create', 'master.route_step.update'),
                  count=lambda c: c.execute(sa.select(sa.func.count()).select_from(TABLES['route_step'])).scalar(),
                  doc='工艺路线和它的工序写在一张表里：一行一道工序，同一条路线的行"工艺路线编码"相同；路线名称、产品、版本在第一行填就行。'
                      '工艺参数写成 名=值；名=值，现场工位终端会显示。',
                  rules=['工艺路线编码 + 顺序唯一', '新路线要有名称', '同一路线的名称 / 产品要一致', '顺序 > 0（建议 10、20、30…）', '工序要先在「工序」里有'],
                  examples=[{'route_code': 'RT-1001', 'route_name': 'A 型壳体机加', 'product': 'FG-1001', 'version': 'V1', 'status': '生效', 'seq': 10,
                             'process': 'OP10', 'station_group': 'CNC', 'std_cycle_s': 120, 'inspection': '否', 'report_mode': '设备自动', 'fai': '是',
                             'instruction': '装夹前清理定位面；首件三坐标检验', 'params': '主轴转速 rpm=2800；进给 mm/r=0.15'}]))


# ───────────────────────── 人员 ─────────────────────────
ent('shift', 40, group='人员', examples=[{'code': 'A', 'name': '早班', 'start': '08:00', 'end': '16:00', 'active': '是', 'break_min': 30}])
ent('team', 41, group='人员', depends=('shift',), examples=[{'code': 'T1', 'name': '机加甲班', 'shift': 'A', 'leader': '张强'}])
ent('employee', 42, group='人员', depends=('team',), doc='员工：工号唯一。账号在「系统 › 用户」里关联员工。',
    examples=[{'code': 'E001', 'name': '张强', 'team': 'T1', 'post': '班长', 'phone': '13800000000', 'skills': 'CNC、首检'}])


def _skill_check(ctx, v, row):
    if not v.get('process') and not v.get('station_group') and not v.get('equipment'):
        return _tr('工序、工位组、设备至少填一个')
    return None


ent('skill', 43, keys=('employee', 'process', 'station_group', 'equipment'), group='人员', depends=('employee', 'process'),
    find=_null_find('skill', ('employee', 'process', 'station_group', 'equipment')), check_row=_skill_check,
    doc='人员技能：谁会哪道工序 / 哪个工位组 / 哪台设备（排产按它找人）。等级：1 学徒、2 熟练、3 骨干、4 可带教。',
    examples=[{'employee': 'E001', 'process': 'OP10', 'station_group': 'CNC', 'level': '骨干', 'valid_until': '2027-12-31'}])

# ───────────────────────── 往来单位 ─────────────────────────
ent('customer', 50, group='往来单位', examples=[{'code': 'C001', 'name': '北辰重型汽车有限公司', 'short_name': '北辰重卡', 'contact': '王工', 'phone': '0431-00000000'}])
ent('supplier', 51, group='往来单位', examples=[{'code': 'S001', 'name': '某铸造材料有限公司', 'short_name': '铸材', 'category': '原材料', 'contact': '王经理'}])

# ───────────────────────── 仓储 ─────────────────────────
ent('warehouse', 55, group='仓储', examples=[{'code': 'W-RAW', 'name': '原材料库', 'wtype': '原材料库'}])


def _loc_check(ctx, v, row):
    if v.get('capacity') is not None and v['capacity'] < 0:
        return _tr('容量不能是负数')
    return None


ent('location', 56, group='仓储', depends=('warehouse',), check_row=_loc_check, examples=[{'code': 'W-RAW-01', 'name': '原材料库 1 区', 'warehouse': 'W-RAW', 'capacity': 200}])


def _stock_cols():
    return [Col('material', _tr('物料'), 'ref', ref='material', req=True, aliases=(_tr('物料编码'), _tr('料号'))),
            Col('warehouse', _tr('仓库'), 'ref', ref='warehouse', req=True, aliases=(_tr('仓库编码'),)),
            Col('location', _tr('库位'), 'ref', ref='location', aliases=(_tr('库位编码'), _tr('货位'))), Col('lot', _tr('批次'), aliases=(_tr('批号'), _tr('炉次'))),
            Col('qty', _tr('数量'), 'float', req=True, aliases=(_tr('库存'), _tr('期初数量'), _tr('结存'))), Col('unit', _tr('单位'), hint=_tr('空 = 物料基本单位；别的单位按单位换算折算')),
            Col('remark', _tr('备注'))]


def _stock_find(ctx, v):
    st = TABLES['stock']
    q = sa.select(st).where((st.c.material == v['material']) & (st.c.warehouse == v['warehouse']))
    q = q.where(st.c.location.is_(None) if not v.get('location') else st.c.location == v['location'])
    q = q.where(st.c.lot.is_(None) if not v.get('lot') else st.c.lot == v['lot'])
    r = ctx.conn.execute(q.limit(1)).mappings().first()
    return dict(r) if r else None


def _stock_check(ctx, v, row):
    errs = []
    if v.get('qty') is not None and v['qty'] < 0:
        errs.append(_tr('期初数量不能是负数'))
    if v.get('location'):
        wh = ctx.conn.execute(sa.select(TABLES['location'].c.warehouse).where(TABLES['location'].c.id == v['location'])).scalar()
        if wh and wh != v.get('warehouse'):
            errs.append(_tr('库位不属于这个仓库'))
    if v.get('unit') and v.get('material') and v.get('qty') is not None:
        try:
            q = uom.convert(ctx.conn, v['material'], v['qty'], v['unit'])
            base = uom.base_unit(ctx.conn, v['material'])
            if uom.norm(v['unit']) != uom.norm(base):
                row.warn(f'{v["qty"]} {v["unit"]} = {q:g} {base}')
        except ValueError as ex:
            errs.append(str(ex))
    return errs


def _stock_all(ctx, rows):
    seen = {}
    for r in rows:
        k = tuple(r.values.get(x) or None for x in ('material', 'warehouse', 'location', 'lot'))
        if k in seen and not r.errors:
            r.err(_tr('和第 {v} 行重复（物料、仓库、库位、批次相同）', v=seen[k]))
        seen.setdefault(k, r.n)


def _stock_write(ctx, v, existing):
    from ..services.warehouse import move
    qty = uom.convert(ctx.conn, v['material'], v['qty'], v['unit']) if v.get('unit') else v['qty']
    cur = float((existing or {}).get('qty') or 0)
    delta = round(qty - cur, 6)
    if abs(delta) < 1e-9:
        return 'skip'
    move(ctx.conn, 'in' if not existing and delta > 0 else 'adjust', v['material'], delta, v['warehouse'], lot=v.get('lot') or None,
         loc=v.get('location') or None, who=(ctx.user or {}).get('name') or (ctx.user or {}).get('username') or _tr('系统'),
         reason=_tr('期初库存') + (_tr('（{remark}）', remark=v["remark"]) if v.get('remark') else ''))
    return 'update' if existing else 'create'


register(Importer(key='opening_stock', label='期初库存', entity=None, columns=_stock_cols, keys=('material', 'warehouse'), order=60, group='仓储',
                  find=_stock_find, check_row=_stock_check, check_all=_stock_all, write=_stock_write, depends=('material', 'warehouse', 'location'),
                  perms=('warehouse.stock_txn.create',), count=lambda c: c.execute(sa.select(sa.func.count()).select_from(TABLES['stock'])).scalar(),
                  doc='期初库存（上线那一刻的结存）：一行一个 物料 + 仓库 + 库位 + 批次。导入后库存就是表里的数（已有库存按差额记一笔"盘点调整"，'
                      '新库存记一笔"入库"，出入库记录里都能查到"期初库存"）。',
                  rules=['物料 + 仓库 + 库位 + 批次唯一', '数量 ≥ 0', '库位要属于这个仓库', '单位不是基本单位时按单位换算折算'],
                  examples=[{'material': 'FG-1001', 'warehouse': 'W-FG', 'location': 'W-FG-01', 'lot': 'L20261101', 'qty': 360, 'unit': '件', 'remark': '盘点 11-01'}]))

# ───────────────────────── 质量 ─────────────────────────
ent('defect_code', 65, group='质量', depends=('process',), examples=[{'code': 'D01', 'name': '气孔', 'category': '其他', 'severity': '严重', 'process': 'OP10'}])


def _tpl_cols():
    it = next(f for f in BY_NAME['insp_template'].fields if f.name == 'itype').enum
    return [Col('tpl_code', _tr('检验模板编码'), 'str', req=True, aliases=(_tr('模板编码'), _tr('检验模板'))), Col('tpl_name', _tr('检验模板名称'), aliases=(_tr('模板名称'),), hint=_tr('同一模板第一行填就行')),
            Col('itype', _tr('检验类型'), 'enum', enum=it), Col('material', _tr('物料'), 'ref', ref='material'), Col('process', _tr('工序'), 'ref', ref='process'),
            Col('sample_rule', _tr('抽样规则')), Col('seq', _tr('项目顺序'), 'int', req=True, aliases=(_tr('顺序'), _tr('序号'))), Col('name', _tr('检验项目'), 'str', req=True, aliases=(_tr('项目'), _tr('项目名称'))),
            Col('char', _tr('特性代码'), hint=_tr('SPC 用，如 BORE_DIA')), Col('method', _tr('方法/量具'), aliases=(_tr('方法'), _tr('量具'))), Col('std', _tr('标准')),
            Col('lo', _tr('下限'), 'float'), Col('hi', _tr('上限'), 'float'), Col('unit', _tr('单位')), Col('key', _tr('关键特性'), 'bool')]


def _tpl_find(ctx, v):
    tt, it = TABLES['insp_template'], TABLES['insp_item']
    tid = ctx.conn.execute(sa.select(tt.c.id).where(tt.c.code == v['tpl_code'])).scalar() or ctx.created.get(('insp_template', v['tpl_code']))
    if not tid:
        return None
    r = ctx.conn.execute(sa.select(it).where((it.c.template == tid) & (it.c.seq == v['seq']))).mappings().first()
    return dict(r) if r else None


def _tpl_check(ctx, v, row):
    if v.get('lo') is not None and v.get('hi') is not None and v['lo'] > v['hi']:
        return _tr('下限大于上限')
    return None


def _tpl_all(ctx, rows):
    tt = TABLES['insp_template']
    by: dict = {}
    for r in rows:
        if r.values.get('tpl_code'):
            by.setdefault(r.values['tpl_code'], []).append(r)
    for code, rs in by.items():
        if not any(r.values.get('tpl_name') for r in rs) and not ctx.conn.execute(sa.select(tt.c.id).where(tt.c.code == code)).scalar():
            for r in rs:
                r.err(_tr('新的检验模板 {code} 要填模板名称', code=code))


def _tpl_write(ctx, v, existing):
    tt = TABLES['insp_template']
    code = v['tpl_code']
    head = {'code': code, 'name': v.get('tpl_name'), 'itype': v.get('itype'), 'material': v.get('material'), 'process': v.get('process'), 'sample_rule': v.get('sample_rule')}
    tid = ctx.created.get(('insp_template', code)) or ctx.conn.execute(sa.select(tt.c.id).where(tt.c.code == code)).scalar()
    seen = ctx.state.setdefault('tpl_head', set())
    if not tid:
        tid = db.insert(ctx.conn, 'insp_template', {k: x for k, x in head.items() if x is not None})
        ctx.remember('insp_template', code, tid)
        seen.add(code)
    elif code not in seen:
        seen.add(code)
        cur = db.get(ctx.conn, 'insp_template', tid)
        ch = {k: x for k, x in head.items() if x is not None and k != 'code' and cur.get(k) != x}
        if ch:
            db.update(ctx.conn, 'insp_template', tid, ch)
    item = {k: v.get(k) for k in ('seq', 'name', 'char', 'method', 'std', 'lo', 'hi', 'unit', 'key')}
    if existing:
        ch = {k: x for k, x in item.items() if x is not None and I._differs(existing, k, x)}
        if not ch:
            return 'skip'
        db.update(ctx.conn, 'insp_item', existing['id'], ch)
        return 'update'
    db.insert(ctx.conn, 'insp_item', {'template': tid, **{k: x for k, x in item.items() if x is not None}})
    return 'create'


register(Importer(key='insp_template', label='检验模板（含项目）', entity=None, columns=_tpl_cols, keys=('tpl_code', 'seq'), order=66, group='质量',
                  find=_tpl_find, write=_tpl_write, check_row=_tpl_check, check_all=_tpl_all, depends=('material', 'process'),
                  perms=('quality.insp_template.create', 'quality.insp_template.update', 'quality.insp_item.create', 'quality.insp_item.update'),
                  count=lambda c: c.execute(sa.select(sa.func.count()).select_from(TABLES['insp_item'])).scalar(),
                  doc='检验模板和检验项目写在一张表里：一行一个项目，同一模板的行"检验模板编码"相同；有上下限的项目录入实测值自动判定。',
                  rules=['模板编码 + 项目顺序唯一', '下限 ≤ 上限', '新模板要有名称'],
                  examples=[{'tpl_code': 'T-FAI-1001', 'tpl_name': 'A 型壳体首件检验', 'itype': '首件检验', 'material': 'FG-1001', 'process': 'OP10',
                             'sample_rule': '每班首件 / 换刀后', 'seq': 10, 'name': '缸孔直径', 'char': 'BORE_DIA', 'method': '三坐标', 'std': 'Φ40 +0.025/0',
                             'lo': 40.0, 'hi': 40.025, 'unit': 'mm', 'key': '是'}]))

# ───────────────────────── 模具 / 保养 / 换型 ─────────────────────────


def _tooling_check(ctx, v, row):
    if v.get('life_shots') and v.get('shots') and v['shots'] > v['life_shots']:
        row.warn(_tr('当前模次已经超过寿命模次'))
    return [m for m in (_tr('当前模次不能是负数'),) if v.get('shots') is not None and v['shots'] < 0]


ent('tooling', 70, group='设备', depends=('material', 'process', 'equipment'), check_row=_tooling_check,
    doc='模具 / 夹具 / 检具：寿命模次和保养周期用于排产约束和到期提醒。',
    examples=[{'code': 'MJ-1001-01', 'name': 'A 型壳体模具 1#', 'ttype': '模板/模具', 'product': 'FG-1001', 'process': 'OP10', 'station_group': 'MOLD1',
               'shots': 12000, 'life_shots': 60000, 'maint_every': 5000, 'status': '可用', 'location': '模具库 A-01'}])


def _maint_check(ctx, v, row):
    if v.get('cycle_days') is not None and v['cycle_days'] <= 0:
        return _tr('周期要大于 0 天')
    if not v.get('next_due') and v.get('last_done') and v.get('cycle_days'):
        v['next_due'] = v['last_done'] + timedelta(days=v['cycle_days'])
        row.warn(_tr('下次到期按上次完成 + 周期算：{next_due}', next_due=v["next_due"]))
    return None


ent('maint_plan', 72, group='设备', depends=('equipment', 'employee'), check_row=_maint_check,
    doc='点检保养计划：按设备、类型、周期；下次到期不填时按"上次完成 + 周期"算。',
    examples=[{'code': 'MP-CNC1-M', 'name': '1# 加工中心月度保养', 'equipment': 'CNC1', 'mtype': '定期保养', 'cycle_days': 30, 'items': '更换滤芯；检查润滑；精度复测',
               'last_done': '2026-10-20', 'owner': 'E001'}])
ent('changeover', 74, keys=('station_group', 'from_product', 'to_product'), group='工艺', find=_null_find('changeover', ('station_group', 'from_product', 'to_product')),
    check_row=_positive('minutes'), depends=('material',),
    doc='换型时间（排产约束）：某工位组从产品 A 换到产品 B 要多少分钟；产品空着 = 任意。',
    examples=[{'station_group': 'CNC', 'from_product': 'FG-1001', 'to_product': 'FG-1002', 'minutes': 45, 'remark': '换夹具 + 调程序 + 首件'}])

# ───────────────────────── 可选模块 ─────────────────────────
ent('charge_material', 80, group='铸造', module='foundry', doc='炉料（配料优化用）：价格、成分、配比上下限、库存。',
    examples=[{'code': 'CM-PIG', 'name': 'Z14 生铁', 'price': 3300, 'C': 4.2, 'Si': 1.4, 'Mn': 0.3, 'S': 0.03, 'P': 0.06, 'min_ratio': 20, 'max_ratio': 70,
               'stock_t': 180, 'yield_pct': 95}])
ent('tool', 82, keys=('equipment', 'code'), group='机加', module='machining', depends=('equipment',), find=_null_find('tool', ('equipment', 'code')),
    doc='刀具台账：机床 + 刀位唯一；寿命按件数。',
    examples=[{'code': 'T03', 'name': 'Φ8 钻头', 'equipment': 'CNC1', 'ttype': '钻头', 'life_limit': 100}])


# ───────────────────────── 上线时的在制 / 资料 ─────────────────────────
def _wo_cols():
    cols = entity_columns('work_order')
    return [Col('code', _tr('工单号'), hint=_tr('空 = 自动编号；填了按工单号更新'))] + cols


register(Importer(key='work_order', label='生产工单（期初在制）', entity='work_order', columns=_wo_cols, keys=('code',), order=90, group='生产',
                  depends=('material', 'route'), check_row=_positive('qty'),
                  doc='上线时还没做完的工单（或计划工单）：产品、工艺路线、计划数量、计划开始、交期。工单号空着自动编号；状态都是草稿，下达在系统里做。',
                  examples=[{'product': 'FG-1001', 'route': 'RT-1001', 'qty': 600, 'start_plan': '2026-11-05 08:00', 'due': '2026-11-08', 'priority': 3,
                             'customer': '北辰重卡'}]))
def _kb_write(ctx, v, existing):
    if not existing:
        v = {**v, 'updated': db.clock.now()}
    return I.default_write(ctx, v, existing)


ent('kb_doc', 92, keys=('title',), group='资料', label='知识库 / SOP', exclude=('file_name', 'file_type', 'file_size', 'updated'), write=_kb_write,
    doc='作业指导书、标准规范、图纸说明：标题唯一；关联工序 / 产品后，工位终端和报工详情里按工序自动带出。图纸文件在「知识库」页面上传。',
    examples=[{'title': '加工中心换刀作业指导', 'tags': '工艺,机加', 'content': '到寿命自动换刀；换刀后首件复测关键尺寸。', 'kind': '作业指导书', 'process': 'OP10'}])
ent('downtime_plan', 76, keys=('equipment', 'start'), group='设备', depends=('equipment',),
    doc='设备停机计划（排产避开）：设备、开始、结束、类型。',
    examples=[{'equipment': 'CNC1', 'kind': '计划保养', 'start': '2026-11-10 08:00', 'end': '2026-11-10 12:00', 'reason': '月度保养'}])
