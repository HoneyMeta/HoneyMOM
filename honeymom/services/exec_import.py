"""M5 的数据导入器（登记到 M3 的通用导入框架 honeymom/impl/importer.py，界面"实施 → 数据导入"）：
量检具、校准记录、备件、包装规则、标签模板（版面 JSON）、控制计划（表头 + 明细，一行一个特性）。没有导入框架时跳过。"""
from ..i18n import _tr
import sqlalchemy as sa

from .. import db
from ..schema import TABLES
from ..schema_exec import GAUGE_T, LABEL_USE, SPECIAL


def register_all():
    try:
        from ..impl import importer as imp
        from ..impl.importer import Col, Importer
    except ImportError:
        return False
    from .exec_outsource import _after_spare_txn
    from .iatf import _after_cal, _after_gauge

    def _hooked(entity, hook, keys):
        """缺省写法 + 业务钩子（量具算到期状态、校准记录回写量具、备件出入库过账）。"""
        def write(ctx, v, existing):
            row = {k: x for k, x in v.items() if x is not None}
            if existing:
                ch = {k: x for k, x in row.items() if existing.get(k) != x and k not in keys}
                if not ch:
                    return 'skip'
                db.update(ctx.conn, entity, existing['id'], ch)
                hook(ctx.conn, existing['id'], ctx.user)
                return 'update'
            rid = db.insert(ctx.conn, entity, row)
            hook(ctx.conn, rid, ctx.user)
            return 'create'
        return write
    imp.register(Importer(key='gauge', label=_tr('量检具'), entity='gauge', keys=('code',), group='质量', order=72, exclude=('status',),
                          write=_hooked('gauge', _after_gauge, ('code',)),
                          rules=[_tr('编码唯一'), _tr('上次校准 + 校准周期 → 下次到期（不填时自动算）'), _tr('导入后状态按到期日自动维护；过期的自动锁定')],
                          examples=[{'code': 'G-CAL-11', 'name': _tr('数显卡尺 0–150'), 'gtype': _tr('卡尺'), 'spec': '0–150 / 0.01', 'cal_interval_days': 365,
                                     'last_cal': '2026-03-01', 'location': _tr('清理线'), 'owner': _tr('孙敏')}]))
    imp.register(Importer(key='gauge_cal', label=_tr('量具校准记录'), entity='gauge_cal', keys=('gauge', 'cal_date'), group='质量', order=73, depends=('gauge',),
                          write=_hooked('gauge_cal', _after_cal, ('gauge', 'cal_date')),
                          rules=[_tr('量具填量具编号'), _tr('合格 → 量具"待放行"（在量检具页面电子签名放行）；不合格 → 锁定')],
                          examples=[{'gauge': 'G-CAL-11', 'cal_date': '2026-03-01', 'result': '合格', 'agency': _tr('外校'), 'provider': _tr('省计量院'), 'cert_no': 'JL2603011'}]))
    imp.register(Importer(key='spare_part', label=_tr('备件'), entity='spare_part', keys=('code',), group='设备', order=74, exclude=('qty',),
                          rules=[_tr('期初库存请在"备件出入库"导入（类型 入库）')],
                          examples=[{'code': 'SP-BRG-7014', 'name': _tr('主轴轴承 7014C P4'), 'unit': '个', 'min_qty': 2, 'max_qty': 4, 'price': 4200, 'supplier': _tr('某轴承公司')}]))
    imp.register(Importer(key='spare_txn', label=_tr('备件出入库（期初）'), entity='spare_txn', keys=('code',), group='设备', order=75, depends=('spare_part',),
                          exclude=('balance', 'code'), find=lambda ctx, v: None, write=_hooked('spare_txn', _after_spare_txn, ()),
                          examples=[{'spare': 'SP-BRG-7014', 'kind': '入库', 'qty': 2, 'remark': _tr('期初库存')}]))
    imp.register(Importer(key='pack_rule', label=_tr('包装规则'), entity='pack_rule', keys=('customer', 'material'), group='销售', order=76, depends=('material',),
                          examples=[{'customer': 'C001', 'material': 'SGH-450', 'customer_part_no': '7700-450-001', 'carton_qty': 20, 'pallet_cartons': 24}]))

    def _tpl_cols():
        return [Col('code', _tr('编码'), req=True), Col('name', _tr('名称'), req=True), Col('usage', _tr('用途'), 'enum', enum=LABEL_USE), Col('width_mm', _tr('宽 mm'), 'float', req=True),
                Col('height_mm', _tr('高 mm'), 'float', req=True), Col('customer', _tr('客户')), Col('layout', _tr('版面 JSON'), 'json', req=True, hint=_tr('{"elements": [...]}，见 docs/dev/exec.md')),
                Col('sample', _tr('样例数据 JSON'), 'json')]

    def _tpl_write(ctx, v, existing):
        lay = v.get('layout')
        if isinstance(lay, list):
            lay = {'elements': lay}
        row = {k: v.get(k) for k in ('code', 'name', 'usage', 'width_mm', 'height_mm', 'customer', 'sample') if v.get(k) is not None}
        row['layout'] = lay
        if existing:
            db.update(ctx.conn, 'label_template', existing['id'], row)
            return 'update'
        db.insert(ctx.conn, 'label_template', row)
        return 'create'

    def _tpl_check(ctx, v, row):
        from .. import labelrender
        lay = v.get('layout')
        els = lay.get('elements') if isinstance(lay, dict) else lay
        if not isinstance(els, list):
            return _tr('版面 JSON 要有 elements 列表')
        probs = labelrender.validate({'width_mm': v.get('width_mm') or 100, 'height_mm': v.get('height_mm') or 60, 'elements': els})
        for p in probs:
            row.warn(p)
    imp.register(Importer(key='label_template', label=_tr('标签模板'), entity='label_template', columns=_tpl_cols, keys=('code',), group='质量', order=77,
                          write=_tpl_write, check_row=_tpl_check, rules=[_tr('版面坐标单位 mm；元素类型 text / barcode / line / box / image')]))

    def _cp_cols():
        return [Col('plan_code', _tr('控制计划编号'), req=True, aliases=(_tr('编号'), _tr('控制计划'))), Col('title', _tr('控制计划名称')), Col('product', _tr('产品'), 'ref', ref='material'),
                Col('phase', _tr('阶段'), 'enum', enum={'proto': _tr('样件'), 'prelaunch': _tr('试生产'), 'production': _tr('量产')}), Col('rev', _tr('版本')),
                Col('seq', _tr('顺序'), 'int', req=True), Col('process', _tr('过程'), 'ref', ref='process'), Col('machine', _tr('机器/装置/工装')), Col('char_no', _tr('特性编号')),
                Col('product_char', _tr('产品特性')), Col('process_char', _tr('过程特性')), Col('char', _tr('特性代码')), Col('special', _tr('特殊特性'), 'enum', enum=SPECIAL),
                Col('spec', _tr('规范/公差')), Col('lo', _tr('下限'), 'float'), Col('hi', _tr('上限'), 'float'), Col('unit', _tr('单位')), Col('method', _tr('评价测量技术')),
                Col('gauge_type', _tr('量具类型'), 'enum', enum=GAUGE_T), Col('sample_size', _tr('样本容量')), Col('frequency', _tr('频率')), Col('control_method', _tr('控制方法')),
                Col('reaction', _tr('反应计划'), 'text')]

    def _cp_find(ctx, v):
        cp = db.get(ctx.conn, 'control_plan', code=v['plan_code'])
        if not cp:
            return None
        it = TABLES['cp_item']
        r = ctx.conn.execute(sa.select(it).where((it.c.plan == cp['id']) & (it.c.seq == v['seq']))).mappings().first()
        return dict(r) if r else None

    def _cp_write(ctx, v, existing):
        code = v['plan_code']
        cid = ctx.created.get(('control_plan', code)) or (db.get(ctx.conn, 'control_plan', code=code) or {}).get('id')
        if not cid:
            cid = db.insert(ctx.conn, 'control_plan', {'code': code, 'title': v.get('title') or code, 'product': v.get('product'), 'phase': v.get('phase') or 'production',
                                                        'rev': v.get('rev') or 'A', 'status': 'draft'})
            ctx.remember('control_plan', code, cid)
        item = {k: v.get(k) for k in ('seq', 'process', 'machine', 'char_no', 'product_char', 'process_char', 'char', 'special', 'spec', 'lo', 'hi', 'unit', 'method',
                                      'gauge_type', 'sample_size', 'frequency', 'control_method', 'reaction') if v.get(k) is not None}
        if existing:
            ch = {k: x for k, x in item.items() if existing.get(k) != x}
            if not ch:
                return 'skip'
            db.update(ctx.conn, 'cp_item', existing['id'], ch)
            return 'update'
        db.insert(ctx.conn, 'cp_item', {'plan': cid, **item})
        return 'create'
    imp.register(Importer(key='control_plan', label=_tr('控制计划（含特性）'), entity=None, columns=_cp_cols, keys=('plan_code', 'seq'), group='质量', order=78,
                          find=_cp_find, write=_cp_write, depends=('material', 'process'),
                          perms=('iatf.control_plan.create', 'iatf.control_plan.update', 'iatf.cp_item.create', 'iatf.cp_item.update'),
                          rules=[_tr('一行一个特性，表头列（编号、名称、产品、阶段、版本）第一行填就行'), _tr('导入后是草稿：在“PFMEA 与控制计划”里生成检验模板、纳入文件受控')],
                          count=lambda c: c.execute(sa.select(sa.func.count()).select_from(TABLES['cp_item'])).scalar(),
                          examples=[{'plan_code': 'CP-SGH450', 'title': _tr('SGH-450 量产控制计划'), 'product': 'SGH-450', 'phase': _tr('量产'), 'rev': 'A', 'seq': 10, 'process': 'P60',
                                     'product_char': _tr('缸孔直径 Φ40 H7'), 'char': 'BORE_DIA', 'special': 'CC', 'lo': 40.0, 'hi': 40.025, 'unit': 'mm', 'method': _tr('内径表'),
                                     'gauge_type': _tr('内径表'), 'sample_size': _tr('5 件'), 'frequency': _tr('每 2 小时'), 'reaction': _tr('停机隔离，100% 全检')}]))
    return True
