"""会计凭证：按业务单据生成（借贷平衡），科目来自科目映射表（acct_map，可按物料类型分存货科目），导出给财务软件导入：
金蝶云星空（凭证引入模板）、用友 U8（总账凭证引入）、用友 YonSuite（凭证导入模板）、通用 Excel。

格式说明与假设见 docs/dev/erp.md「凭证导出」：列定义在 FORMATS 里，可以用系统参数 erp_voucher_formats（JSON）整体替换某个格式的列，
不改代码就能对上客户财务软件里实际的引入模板。凭证一张单据一张（来源键唯一，重复生成不会重复）；已结账期间的凭证照样能生成 / 导出（数据已冻结）。"""
from ..i18n import _tr
import csv
import io
import json
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from xml.sax.saxutils import escape

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import Response

from .. import db, perms
from ..crud import action, attach_labels, out
from ..db import clock
from ..perms import require
from ..schema import TABLES
from . import core
from .core import as_date, money, today, who

router = APIRouter()
P_GEN = perms.declare('finance.voucher.generate', '生成凭证', '财务')
P_EXPORT = perms.declare('finance.voucher.export', '导出凭证（金蝶 / 用友 / Excel）', '财务')


# ───────────────── 科目 ─────────────────
class Accounts:
    def __init__(self, c):
        self.m = defaultdict(dict)
        for r in db.rows(c, 'acct_map'):
            self.m[r['event']][r.get('mtype') or ''] = r

    def get(self, event, mtype=None):
        d = self.m.get(event) or {}
        r = d.get(mtype or '') or d.get('')
        if not r and d:
            r = next(iter(d.values()))
        if not r:
            from ..schema_erp import VOUCHER_EVENTS
            raise HTTPException(409, _tr('科目映射里没有「{get}」，请在 财务 › 凭证 › 科目映射 里补上', get=VOUCHER_EVENTS.get(event, event)))
        return r


def _line(acc, summary, debit=0.0, credit=0.0, aux=None, qty=None, material=None):
    a = {'account_code': acc['account_code'], 'account_name': acc.get('account_name'), 'summary': summary, 'debit': money(debit), 'credit': money(credit),
         'qty': qty, 'material': material}
    if aux and acc.get('aux') and acc['aux'] != 'none':
        a.update(aux_type={'customer': _tr('客户'), 'supplier': _tr('供应商'), 'material': _tr('物料'), 'dept': _tr('部门')}.get(acc['aux'], acc['aux']),
                 aux_code=aux.get(acc['aux'], {}).get('code'), aux_name=aux.get(acc['aux'], {}).get('name'))
    return a


def _save(c, key, vdate, stype, scode, summary, lines):
    vt = TABLES['voucher']
    if c.execute(sa.select(vt.c.id).where(vt.c.src_key == key)).first():
        return None
    lines = [x for x in lines if abs(x['debit']) > 0.0049 or abs(x['credit']) > 0.0049]
    if not lines:
        return None
    dr, cr = money(sum(x['debit'] for x in lines)), money(sum(x['credit'] for x in lines))
    if abs(dr - cr) > 0.01:                      # 四舍五入差：挂到最后一行贷方 / 借方
        diff = money(dr - cr)
        tgt = next((x for x in reversed(lines) if x['credit']), lines[-1])
        tgt['credit'] = money(tgt['credit'] + diff) if tgt['credit'] else tgt['credit']
        if not tgt['credit']:
            tgt['debit'] = money(tgt['debit'] - diff)
        dr, cr = money(sum(x['debit'] for x in lines)), money(sum(x['credit'] for x in lines))
    vid = db.insert(c, 'voucher', {'period': core.period_of(vdate), 'vdate': as_date(vdate), 'word': _tr('记'), 'source_type': stype, 'source_code': scode,
                                   'summary': summary[:190], 'debit': dr, 'credit': cr, 'status': 'draft', 'src_key': key}, raw=True)
    for i, x in enumerate(lines):
        db.insert(c, 'voucher_line', {**x, 'voucher': vid, 'seq': i + 1}, raw=True)
    return vid


def _mtype(c, mid, cache={}):
    if mid not in cache:
        cache[mid] = (db.get(c, 'material', mid) or {}).get('mtype') or 'raw'
    return cache[mid]


def generate(c, period, kinds=None, user=None):
    """生成一个期间的凭证（已经生成过的单据跳过）。返回 {业务: 张数}。"""
    from . import cost
    cost.valuate(c)
    s, e = core.period_bounds(period)
    A = Accounts(c)
    kinds = set(kinds or ['delivery', 'sales_return', 'ar_receipt', 'receipt', 'supplier_return', 'ap_payment', 'issue', 'wo_cost', 'adjust'])
    made = defaultdict(int)
    custs = {x['id']: x for x in db.rows(c, 'customer')}
    sups = {x['id']: x for x in db.rows(c, 'supplier')}
    mats = {x['id']: x for x in db.rows(c, 'material')}

    def aux_c(cid):
        cu = custs.get(cid, {})
        return {'customer': {'code': cu.get('code'), 'name': cu.get('name')}}

    def aux_s(sid):
        su = sups.get(sid, {})
        return {'supplier': {'code': su.get('code'), 'name': su.get('name')}}

    def aux_m(mid):
        m = mats.get(mid, {})
        return {'material': {'code': m.get('code'), 'name': m.get('name')}}
    if 'delivery' in kinds:
        t = TABLES['delivery_order']
        for d in db.rows(c, 'delivery_order', t.c.status == 'posted', t.c.ship_date >= s, t.c.ship_date < e, order=t.c.id):
            cu = custs.get(d['customer'], {})
            sm = _tr('销售发货 {code} {v}', code=d['code'], v=cu.get('short_name') or cu.get('name') or '')
            ls = [_line(A.get('ar'), sm, debit=d['total'], aux=aux_c(d['customer'])), _line(A.get('revenue'), sm, credit=d['amount']),
                  _line(A.get('tax_out'), sm, credit=d['tax'])]
            cogs = defaultdict(float)
            for ln in core.lines_of(c, 'delivery_order', d['id']):
                cogs[ln['material']] += (ln['unit_cost'] or 0) * ln['qty']
            if cogs:
                ls.append(_line(A.get('cogs'), _tr('结转成本 {code}', code=d['code']), debit=sum(cogs.values())))
                for mid, v in cogs.items():
                    ls.append(_line(A.get('inventory', _mtype(c, mid)), _tr('结转成本 {code}', code=d['code']), credit=v, aux=aux_m(mid), material=mats.get(mid, {}).get('code')))
            made['delivery'] += bool(_save(c, f"delivery:{d['id']}", d['ship_date'], 'delivery', d['code'], sm, ls))
    if 'sales_return' in kinds:
        t = TABLES['sales_return']
        for r in db.rows(c, 'sales_return', t.c.status == 'received', t.c.return_date >= s, t.c.return_date < e, order=t.c.id):
            sm = _tr('销售退货 {code}', code=r['code'])
            ls = [_line(A.get('ar'), sm, debit=-r['total'], aux=aux_c(r['customer'])), _line(A.get('sales_return'), sm, credit=-r['amount']),
                  _line(A.get('tax_out'), sm, credit=-r['tax'])]
            if r['disposition'] == 'restock':
                for ln in core.lines_of(c, 'sales_return', r['id']):
                    v = (ln['unit_cost'] or 0) * ln['qty']
                    ls.append(_line(A.get('inventory', _mtype(c, ln['material'])), sm, debit=v, aux=aux_m(ln['material'])))
                    ls.append(_line(A.get('cogs'), sm, credit=v))
            made['sales_return'] += bool(_save(c, f"sales_return:{r['id']}", r['return_date'], 'sales_return', r['code'], sm, ls))
    if 'ar_receipt' in kinds:
        t = TABLES['ar_receipt']
        for r in db.rows(c, 'ar_receipt', t.c.receipt_date >= s, t.c.receipt_date < e, order=t.c.id):
            sm = _tr('收款 {code} {get}', code=r['code'], get=custs.get(r['customer'], {}).get('name', ''))
            dr = A.get('bill_recv' if r['method'] == 'acceptance' else 'bank')
            ls = [_line(dr, sm, debit=r['amount'], aux=aux_c(r['customer'])), _line(A.get('ar'), sm, credit=r['amount'], aux=aux_c(r['customer']))]
            made['ar_receipt'] += bool(_save(c, f"ar_receipt:{r['id']}", r['receipt_date'], 'ar_receipt', r['code'], sm, ls))
    if 'receipt' in kinds:
        t = TABLES['purchase_receipt']
        for r in db.rows(c, 'purchase_receipt', t.c.status == 'done', t.c.receive_date >= s, t.c.receive_date < e, order=t.c.id):
            sm = _tr('采购入库 {code} {get}', code=r['code'], get=sups.get(r['supplier'], {}).get('name', ''))
            ls = []
            for ln in core.lines_of(c, 'purchase_receipt', r['id']):
                q = ln['qty_accepted'] if ln['qty_accepted'] is not None else ln['qty']
                ls.append(_line(A.get('inventory', _mtype(c, ln['material'])), sm, debit=q * (ln['price'] or 0), aux=aux_m(ln['material']), qty=q,
                                material=mats.get(ln['material'], {}).get('code')))
            ls += [_line(A.get('tax_in'), sm, debit=r['tax']), _line(A.get('ap'), sm, credit=r['total'], aux=aux_s(r['supplier']))]
            made['receipt'] += bool(_save(c, f"receipt:{r['id']}", r['receive_date'], 'receipt', r['code'], sm, ls))
    if 'supplier_return' in kinds:
        t = TABLES['supplier_return']
        for r in db.rows(c, 'supplier_return', t.c.status == 'shipped', t.c.kind == 'stock', t.c.return_date >= s, t.c.return_date < e, order=t.c.id):
            sm = _tr('退供应商 {code}', code=r['code'])
            ls = [_line(A.get('ap'), sm, debit=r['total'], aux=aux_s(r['supplier'])), _line(A.get('tax_in'), sm, credit=r['tax'])]
            for ln in core.lines_of(c, 'supplier_return', r['id']):
                ls.append(_line(A.get('inventory', _mtype(c, ln['material'])), sm, credit=ln['amount'], aux=aux_m(ln['material'])))
            made['supplier_return'] += bool(_save(c, f"supplier_return:{r['id']}", r['return_date'], 'supplier_return', r['code'], sm, ls))
    if 'ap_payment' in kinds:
        t = TABLES['ap_payment']
        for r in db.rows(c, 'ap_payment', t.c.pay_date >= s, t.c.pay_date < e, order=t.c.id):
            sm = _tr('付款 {code} {get}', code=r['code'], get=sups.get(r['supplier'], {}).get('name', ''))
            ls = [_line(A.get('ap'), sm, debit=r['amount'], aux=aux_s(r['supplier'])), _line(A.get('bank'), sm, credit=r['amount'])]
            made['ap_payment'] += bool(_save(c, f"ap_payment:{r['id']}", r['pay_date'], 'ap_payment', r['code'], sm, ls))
    if 'issue' in kinds:            # 生产领料：按工单汇总一张（计价流水里 ref_doc = 工单号 的出库）
        lt, st = TABLES['cost_ledger'], TABLES['stock_txn']
        wos = {w['code']: w for w in db.rows(c, 'work_order')}
        q = sa.select(lt.c.ref_doc, lt.c.material, sa.func.sum(lt.c.value), sa.func.sum(lt.c.qty)).join(st, st.c.id == lt.c.txn).where(
            (lt.c.period == period) & (lt.c.qty < 0) & lt.c.ref_doc.in_(list(wos) or ['-'])).group_by(lt.c.ref_doc, lt.c.material)
        per = defaultdict(list)
        for ref, mid, v, qq in c.execute(q):
            per[ref].append((mid, -float(v or 0), -float(qq or 0)))
        for ref, items in per.items():
            sm = _tr('生产领料 {ref}', ref=ref)
            ls = [_line(A.get('wip_mat'), sm, debit=sum(v for _, v, _ in items))]
            ls += [_line(A.get('inventory', _mtype(c, mid)), sm, credit=v, aux=aux_m(mid), qty=qq, material=mats.get(mid, {}).get('code')) for mid, v, qq in items]
            made['issue'] += bool(_save(c, f"issue:{period}:{ref}", e - timedelta(days=1), 'issue', ref, sm, ls))
    if 'wo_cost' in kinds:          # 生产成本：人工、制造费用（机器 + 能源 + 分摊）计入生产成本；完工入库按实际成本结转（与标准入库价的差额进成本差异：这里简化为直接结转实际）
        t = TABLES['wo_cost']
        for w in db.rows(c, 'wo_cost', t.c.period == period, order=t.c.id):
            code = core.code_of(c, 'work_order', w['work_order'])
            sm = _tr('生产成本 {code}', code=code)
            ovh = (w['mach'] or 0) + (w['energy'] or 0) + (w['ovh'] or 0)
            ls = [_line(A.get('wip_lab'), sm, debit=w['lab']), _line(A.get('payroll'), sm, credit=w['lab']),
                  _line(A.get('wip_ovh'), sm, debit=ovh), _line(A.get('mfg_ovh'), sm, credit=ovh)]
            if w['qty_done']:
                fin = _tr('完工入库 {code}', code=code)
                ls += [_line(A.get('inventory', _mtype(c, w['product'])), fin, debit=w['total'], aux=aux_m(w['product']), qty=w['qty_done'],
                             material=mats.get(w['product'], {}).get('code')),
                       _line(A.get('wip_mat'), fin, credit=w['mat']), _line(A.get('wip_lab'), fin, credit=w['lab']), _line(A.get('wip_ovh'), fin, credit=ovh)]
            made['wo_cost'] += bool(_save(c, f"wo_cost:{period}:{w['work_order']}", e - timedelta(days=1), 'wo_cost', code, sm, ls))
    if 'adjust' in kinds:
        lt = TABLES['cost_ledger']
        for r in db.rows(c, 'cost_ledger', lt.c.period == period, lt.c.ttype == 'adjust', order=lt.c.id):
            if abs(r['value'] or 0) < 0.005:
                continue
            sm = _tr('盘点调整 {get}', get=mats.get(r['material'], {}).get('code', ''))
            inv = A.get('inventory', _mtype(c, r['material']))
            loss = A.get('inv_loss')
            v = r['value']
            ls = [_line(inv, sm, debit=v if v > 0 else 0, credit=-v if v < 0 else 0, aux=aux_m(r['material'])),
                  _line(loss, sm, debit=-v if v < 0 else 0, credit=v if v > 0 else 0)]
            made['adjust'] += bool(_save(c, f"adjust:{r['txn']}", r['time'] or e, 'adjust', f"CK#{r['txn']}", sm, ls))
    return dict(made)


@action('voucher', 'void')
def v_void(c, v, body, user):
    db.update(c, 'voucher', v['id'], {'status': 'void', 'src_key': f"void:{v['id']}:{v['src_key']}"}, raw=True)
    return {'msg': _tr('已作废（可以重新生成）')}


# ───────────────── 导出格式 ─────────────────
# 每个格式：列 = [(表头, 取值键 或 fn(凭证, 分录, 序号))]；header_rows：表头行（金蝶模板是"字段键 + 中文名"两行）；
# group_first：只在每张凭证第一行填表头字段（金蝶的单据头）。取值键：voucher.* / line.* / const:文本 / setting:参数。
FORMATS = {
    'kingdee': {
        'label': '金蝶云星空（凭证引入 .xlsx）', 'ext': 'xlsx', 'sheet': '凭证#单据头(FBillHead)', 'group_first': True,
        'columns': [
            ('FBillHead(GL_VOUCHER)', '*单据头(序号)', 'voucher.seq'), ('FAccountBookID', '(单据头)账簿#编码', 'setting:erp_kd_book|001'),
            ('FDate', '(单据头)日期', 'voucher.vdate'), ('FVOUCHERGROUPID', '(单据头)凭证字#编码', 'setting:erp_kd_word|PRE001'),
            ('FVOUCHERGROUPNO', '(单据头)凭证号', 'voucher.no'), ('FATTACHMENTS', '(单据头)附件数', 'const:1'),
            ('FEntity', '*分录(序号)', 'line.seq'), ('FEXPLANATION', '(分录)摘要', 'line.summary'), ('FACCOUNTID', '(分录)科目编码#编码', 'line.account_code'),
            ('FACCOUNTID#Name', '(分录)科目编码#名称', 'line.account_name'), ('FDetailID#FFLEX6', '(分录)核算维度#客户', 'line.aux_customer'),
            ('FDetailID#FFLEX4', '(分录)核算维度#供应商', 'line.aux_supplier'), ('FDetailID#FFLEX8', '(分录)核算维度#物料', 'line.aux_material'),
            ('FCURRENCYID', '(分录)币别#编码', 'const:PRE001'), ('FEXCHANGERATETYPE', '(分录)汇率类型#编码', 'const:HLTX01_SYS'),
            ('FEXCHANGERATE', '(分录)汇率', 'const:1'), ('FAMOUNTFOR', '(分录)原币金额', 'line.amount'), ('FDEBIT', '(分录)借方金额', 'line.debit'),
            ('FCREDIT', '(分录)贷方金额', 'line.credit'), ('FQUANTITY', '(分录)数量', 'line.qty')],
        'header_rows': 2},
    'yonyou_u8': {
        'label': '用友 U8（总账 › 凭证引入 .xlsx）', 'ext': 'xlsx', 'sheet': '凭证', 'group_first': False,
        'columns': [('凭证类别', 'const:记'), ('凭证号', 'voucher.no'), ('制单日期', 'voucher.vdate'), ('附单据数', 'const:1'), ('摘要', 'line.summary'),
                    ('科目编码', 'line.account_code'), ('借方金额', 'line.debit'), ('贷方金额', 'line.credit'), ('数量', 'line.qty'), ('外币', 'const:'),
                    ('汇率', 'const:1'), ('部门编码', 'const:'), ('个人编码', 'const:'), ('客户编码', 'line.aux_customer'), ('供应商编码', 'line.aux_supplier'),
                    ('项目编码', 'const:'), ('业务员', 'const:'), ('制单人', 'setting:erp_u8_maker|MES'), ('票号', 'voucher.source_code')],
        'header_rows': 1},
    'yonsuite': {
        'label': '用友 YonSuite（凭证导入 .xlsx）', 'ext': 'xlsx', 'sheet': '凭证', 'group_first': False,
        'columns': [('账簿编码', 'setting:erp_ys_book|BOOK001'), ('凭证类型', 'const:记账凭证'), ('凭证号', 'voucher.no'), ('制单日期', 'voucher.vdate'),
                    ('附单据数', 'const:1'), ('分录号', 'line.seq'), ('摘要', 'line.summary'), ('科目编码', 'line.account_code'), ('币种', 'const:CNY'),
                    ('本币借方金额', 'line.debit'), ('本币贷方金额', 'line.credit'), ('原币借方金额', 'line.debit'), ('原币贷方金额', 'line.credit'),
                    ('辅助核算', 'line.aux_text'), ('数量', 'line.qty'), ('来源单据', 'voucher.source_code')],
        'header_rows': 1},
    'excel': {
        'label': '通用 Excel（.xlsx）', 'ext': 'xlsx', 'sheet': '凭证', 'group_first': False,
        'columns': [('期间', 'voucher.period'), ('凭证号', 'voucher.code'), ('日期', 'voucher.vdate'), ('凭证字', 'voucher.word'), ('业务', 'voucher.source_label'),
                    ('来源单据', 'voucher.source_code'), ('行号', 'line.seq'), ('摘要', 'line.summary'), ('科目编码', 'line.account_code'), ('科目名称', 'line.account_name'),
                    ('借方', 'line.debit'), ('贷方', 'line.credit'), ('辅助核算类型', 'line.aux_type'), ('辅助核算编码', 'line.aux_code'),
                    ('辅助核算名称', 'line.aux_name'), ('数量', 'line.qty'), ('物料', 'line.material')],
        'header_rows': 1},
    'csv': {'label': '通用 CSV（UTF-8 BOM）', 'ext': 'csv', 'same_as': 'excel', 'header_rows': 1},
}


def formats(c=None):
    import copy
    f = copy.deepcopy(FORMATS)
    over = core.setting_json(c, 'erp_voucher_formats', None) if c is not None else None
    for k, v in (over or {}).items():
        if k in f and isinstance(v, dict):
            f[k].update({kk: (vv if kk != 'columns' else [tuple(x) for x in vv]) for kk, vv in v.items()})
    if f['csv'].get('same_as'):
        f['csv']['columns'] = f[f['csv']['same_as']]['columns']
    return f


def _cell(c, spec, v, ln, no):
    src = spec[-1]
    if src.startswith('const:'):
        return src[6:]
    if src.startswith('setting:'):
        k, _, dflt = src[8:].partition('|')
        return db.setting(c, k, dflt) or dflt
    obj, _, key = src.partition('.')
    if obj == 'voucher':
        if key == 'seq':
            return no
        if key == 'no':
            return no
        if key == 'source_label':
            from ..schema_erp import VOUCHER_SRC
            return VOUCHER_SRC.get(v['source_type'], v['source_type'])
        val = v.get(key)
    else:
        if key.startswith('aux_') and key not in ('aux_type', 'aux_code', 'aux_name'):
            want = {'aux_customer': _tr('客户'), 'aux_supplier': _tr('供应商'), 'aux_material': _tr('物料')}.get(key)
            if key == 'aux_text':
                return f"{ln.get('aux_type')}:{ln.get('aux_code')}" if ln.get('aux_code') else ''
            return ln.get('aux_code') if ln.get('aux_type') == want else ''
        if key == 'amount':
            return ln.get('debit') or ln.get('credit') or 0
        val = ln.get(key)
    if isinstance(val, datetime):
        return val.strftime('%Y-%m-%d')
    return db.jsonable(val) if val is not None else ''


def export_rows(c, fmt, vouchers):
    f = formats(c)[fmt]
    cols = f['columns']
    head = []
    if f.get('header_rows') == 2:
        head = [[x[0] for x in cols], [x[1] for x in cols]]
    else:
        head = [[x[0] for x in cols]]
    rows = []
    for no, v in enumerate(vouchers, 1):
        lines = db.rows(c, 'voucher_line', TABLES['voucher_line'].c.voucher == v['id'], order=TABLES['voucher_line'].c.seq)
        for i, ln in enumerate(lines):
            row = []
            for spec in cols:
                is_head = spec[-1].startswith('voucher.') or spec[0] in ('FAccountBookID', 'FDate', 'FVOUCHERGROUPID', 'FATTACHMENTS')
                if f.get('group_first') and i > 0 and is_head:
                    row.append('')
                else:
                    row.append(_cell(c, spec, v, ln, no))
            rows.append(row)
    return head, rows


def xlsx(sheet: str, rows: list[list]) -> bytes:
    """最小的 .xlsx 写出（不依赖 openpyxl）：一个工作表，字符串用内联字符串，数字原样。"""
    def col(i):
        s = ''
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s
    out_ = []
    for ri, row in enumerate(rows, 1):
        cells = []
        for ci, v in enumerate(row):
            ref = f'{col(ci)}{ri}'
            if isinstance(v, bool) or v is None or v == '':
                if v not in (None, ''):
                    cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{_tr('是') if v else _tr('否')}</t></is></c>')
                continue
            if isinstance(v, (int, float)):
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>')
        out_.append(f'<row r="{ri}">{"".join(cells)}</row>')
    sheet_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                 f'<sheetData>{"".join(out_)}</sheetData></worksheet>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   f'<sheet name="{escape(sheet[:31])}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml', sheet_xml)
    return buf.getvalue()


def read_xlsx(data: bytes) -> list[list]:
    """读回上面写的 .xlsx（测试 / 预览用）。"""
    import re
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        x = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
    rows = []
    for rm in re.finditer(r'<row r="(\d+)">(.*?)</row>', x, re.S):
        cells = {}
        for cm in re.finditer(r'<c r="([A-Z]+)\d+"( t="inlineStr")?>(?:<is><t[^>]*>(.*?)</t></is>|<v>(.*?)</v>)</c>', rm.group(2), re.S):
            ci = 0
            for ch in cm.group(1):
                ci = ci * 26 + ord(ch) - 64
            v = cm.group(3) if cm.group(2) else cm.group(4)
            if not cm.group(2):
                v = float(v) if '.' in v or 'e' in v.lower() else int(v)
            else:
                v = v.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
            cells[ci - 1] = v
        rows.append([cells.get(i, '') for i in range(max(cells) + 1)] if cells else [])
    return rows


def export(c, fmt, period=None, ids=None, mark=True, user=None):
    fs = formats(c)
    if fmt not in fs:
        raise HTTPException(400, _tr('没有导出格式 {fmt}（可选：{join}）', fmt=fmt, join=_tr('、').join(fs)))
    vt = TABLES['voucher']
    conds = [vt.c.status != 'void']
    if ids:
        conds.append(vt.c.id.in_([int(x) for x in ids]))
    elif period:
        conds.append(vt.c.period == period)
    vs = db.rows(c, 'voucher', *conds, order=(vt.c.vdate, vt.c.id))
    head, rows = export_rows(c, fmt, vs)
    f = fs[fmt]
    if f['ext'] == 'csv':
        buf = io.StringIO()
        w = csv.writer(buf)
        for r in head + rows:
            w.writerow(r)
        data = ('﻿' + buf.getvalue()).encode('utf-8')
        mime = 'text/csv; charset=utf-8'
    else:
        data = xlsx(f.get('sheet') or _tr('凭证'), head + rows)
        mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    name = f"vouchers-{fmt}-{period or 'sel'}-{datetime.now():%Y%m%d%H%M%S}.{f['ext']}"
    if mark and vs:
        bid = db.insert(c, 'voucher_export', {'fmt': fmt, 'period': period or '', 'count': len(vs), 'file_name': name, 'created_by': who(user), 'time': clock.now()},
                        raw=True)
        bcode = core.code_of(c, 'voucher_export', bid)
        c.execute(vt.update().where(vt.c.id.in_([v['id'] for v in vs])).values(status='exported', export_batch=bcode, exported_at=clock.now()))
    return data, mime, name, len(vs)


# ───────────────── 接口 ─────────────────
@router.post('/api/finance/vouchers/generate')
def gen_api(body: dict = Body(default={}), user=Depends(require(P_GEN))):
    period = body.get('period') or core.period_of(today())
    with db.engine.begin() as c:
        made = generate(c, period, body.get('kinds'), user)
    return {'ok': True, 'period': period, 'made': made, 'count': sum(made.values())}


@router.get('/api/finance/vouchers/formats')
def formats_api(user=Depends(require('finance.voucher.read'))):
    with db.engine.connect() as c:
        return [{'key': k, 'label': v['label'], 'ext': v['ext'], 'columns': [x[0] for x in v['columns']]} for k, v in formats(c).items()]


@router.get('/api/finance/vouchers/export')
def export_api(fmt: str = 'excel', period: str = '', ids: str = '', mark: bool = True, user=Depends(require(P_EXPORT))):
    with db.engine.begin() as c:
        data, mime, name, n = export(c, fmt, period or None, [x for x in ids.split(',') if x] or None, mark, user)
    return Response(data, media_type=mime, headers={'Content-Disposition': f'attachment; filename="{name}"', 'X-Count': str(n)})


@router.get('/api/finance/vouchers/preview')
def preview_api(fmt: str = 'excel', period: str = '', limit: int = 40, user=Depends(require('finance.voucher.read'))):
    with db.engine.connect() as c:
        vt = TABLES['voucher']
        vs = db.rows(c, 'voucher', vt.c.status != 'void', *([vt.c.period == period] if period else []), order=(vt.c.vdate, vt.c.id), limit=limit)
        head, rows = export_rows(c, fmt, vs)
    return {'head': head, 'rows': rows[:300]}


@router.get('/api/finance/vouchers/{vid}')
def voucher_api(vid: int, user=Depends(require('finance.voucher.read'))):
    with db.engine.connect() as c:
        v = db.get(c, 'voucher', vid)
        if not v:
            raise HTTPException(404, _tr('凭证不存在'))
        lines = db.rows(c, 'voucher_line', TABLES['voucher_line'].c.voucher == vid, order=TABLES['voucher_line'].c.seq)
    return {'item': out([v])[0], 'lines': out(lines)}
