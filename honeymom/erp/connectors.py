"""金蝶云星空 / 用友 U8 OpenAPI / 用友 YonSuite OpenAPI 对接样例：从 ERP 拉物料、客户、供应商、BOM、销售订单（进 MES），
把完工入库、销售出库、采购入库、库存差异推回 ERP。

**只对着 tests/mocks/ 里的模拟服务验证过**（接口路径、认证、报文结构按公开文档整理，见 docs/dev/erp.md「对接器」）。
真实环境里表单标识 / 字段键 / 接口路径因版本和客户二开会不同：每个对接器的 ENDPOINTS、FORMS、FIELDS 都可以在对接配置（erp_connector.config）里覆盖，
不用改代码；上线前要在客户的测试账套上逐项核对（文档里列了清单）。"""
from ..i18n import _tr
import base64
import hashlib
import hmac
import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import audit, db
from ..perms import require
from ..schema import ERP_TABLES

router = APIRouter()
T = ERP_TABLES
KINDS = {'kingdee': '金蝶云星空 WebAPI', 'yonyou_u8': '用友 U8 OpenAPI', 'yonsuite': '用友 YonSuite OpenAPI'}
DEFAULT_PUSH = ['wo.completion', 'delivery.posted', 'receipt.posted', 'stock.changed']
DEFAULT_PULL = ['material', 'customer', 'supplier', 'bom', 'sales_order']


class ConnError(Exception):
    pass


class Base:
    kind = ''

    def __init__(self, row):
        self.row = row
        self.code = row['code']
        self.base = (row.get('base_url') or '').rstrip('/')
        self.cfg = row.get('config') or {}
        self.sec = row.get('secret') or {}
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.calls = []

    def ep(self, key):
        return (self.cfg.get('endpoints') or {}).get(key) or self.ENDPOINTS[key]

    def http(self, method, url, body=None, headers=None, timeout=30):
        data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={'Content-Type': 'application/json;charset=utf-8', **(headers or {})})
        t0 = time.time()
        try:
            with self.opener.open(req, timeout=timeout) as r:
                raw = r.read()
                code = r.status
        except urllib.error.HTTPError as ex:
            raw, code = ex.read(), ex.code
        except OSError as ex:
            raise ConnError(_tr('连不上 {v}：{ex}', v=url.split("?")[0], ex=ex))
        self.calls.append({'url': url.split('?')[0], 'method': method, 'status': code, 'ms': int((time.time() - t0) * 1000)})
        try:
            j = json.loads(raw or b'null')
        except ValueError:
            raise ConnError(_tr('{v} 返回的不是 JSON（HTTP {code}）：{v2!r}', v=url.split("?")[0], code=code, v2=raw[:200]))
        if code >= 400:
            raise ConnError(_tr('HTTP {code}：{v}', code=code, v=str(j)[:300]))
        return j

    def wh(self, mes_code):
        return (self.cfg.get('wh_map') or {}).get(mes_code, mes_code)

    def push(self, ev):
        fn = {'wo.completion': self.push_completion, 'delivery.posted': self.push_delivery, 'receipt.posted': self.push_receipt,
              'stock.changed': self.push_stock}.get(ev['event'])
        if not fn:
            return {'skipped': ev['event']}
        return fn(ev['payload'] or {}, ev)


# ───────────────── 金蝶云星空 ─────────────────
class Kingdee(Base):
    """金蝶云星空 WebAPI（K/3 Cloud）：
    登录   POST {base}/Kingdee.BOS.WebApi.ServicesStub.AuthService.ValidateUser.common.kdsvc   {acctID, username, password, lcid:2052} → LoginResultType=1，会话 Cookie kdservice-sessionid
           或 LoginByAppSecret（第三方系统登录授权：acctid, username, appid, appsecret, lcid）
    查询   POST …DynamicFormService.ExecuteBillQuery.common.kdsvc   {data: {FormId, FieldKeys, FilterString, StartRow, Limit}} → [[字段值…], …]
    保存   POST …DynamicFormService.Save.common.kdsvc   {formid, data: {Model, IsAutoSubmitAndAudit}} → {Result: {ResponseStatus: {IsSuccess, Errors, SuccessEntitys}}}"""
    kind = 'kingdee'
    ENDPOINTS = {'login': 'Kingdee.BOS.WebApi.ServicesStub.AuthService.ValidateUser.common.kdsvc',
                 'login_app': 'Kingdee.BOS.WebApi.ServicesStub.AuthService.LoginByAppSecret.common.kdsvc',
                 'query': 'Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.ExecuteBillQuery.common.kdsvc',
                 'save': 'Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.Save.common.kdsvc'}
    FORMS = {'material': 'BD_MATERIAL', 'customer': 'BD_Customer', 'supplier': 'BD_Supplier', 'bom': 'ENG_BOM', 'sales_order': 'SAL_SaleOrder',
             'completion': 'PRD_INSTOCK', 'delivery': 'SAL_OUTSTOCK', 'receipt': 'STK_InStock', 'stock': 'STK_Inventory', 'other_in': 'STK_MISCELLANEOUS',
             'other_out': 'STK_MisDelivery'}
    KEYS = {'material': 'FMATERIALID,FNumber,FName,FSpecification,FBaseUnitId.FNumber,FErpClsID',
            'customer': 'FCUSTID,FNumber,FName,FShortName,FTaxRegisterCode',
            'supplier': 'FSupplierId,FNumber,FName,FShortName',
            'bom': 'FID,FMATERIALID.FNumber,FMATERIALIDCHILD.FNumber,FNUMERATOR,FDENOMINATOR',
            'sales_order': 'FID,FBillNo,FDate,FCustId.FNumber,FSaleOrderEntry_FSeq,FMaterialId.FNumber,FQty,FPrice,FEntryTaxRate,FDeliveryDate',
            'stock': 'FMaterialId.FNumber,FStockId.FNumber,FBaseQty'}
    ERPCLS = {'1': 'raw', '2': 'fg', '3': 'semi', '5': 'semi', '6': 'consumable'}      # FErpClsID 物料属性：1 外购 2 自制 3 委外 5 虚拟 6 服务

    def url(self, key):
        return f"{self.base}/{self.ep(key)}"

    def login(self):
        if self.sec.get('app_secret'):
            j = self.http('POST', self.url('login_app'), {'acctid': self.cfg.get('acct_id'), 'username': self.cfg.get('username'), 'appid': self.cfg.get('app_id'),
                                                         'appsecret': self.sec['app_secret'], 'lcid': 2052})
        else:
            j = self.http('POST', self.url('login'), {'acctID': self.cfg.get('acct_id'), 'username': self.cfg.get('username'), 'password': self.sec.get('password'),
                                                     'lcid': 2052})
        if (j or {}).get('LoginResultType') != 1:
            raise ConnError(_tr('金蝶登录失败：{v}', v=(j or {}).get('Message') or j))
        return j

    def query(self, what, filt='', limit=2000):
        form = (self.cfg.get('forms') or {}).get(what) or self.FORMS[what]
        keys = (self.cfg.get('keys') or {}).get(what) or self.KEYS[what]
        rows, start = [], 0
        while True:
            body = {'FormId': form, 'FieldKeys': keys, 'FilterString': filt, 'OrderString': '', 'TopRowCount': 0, 'StartRow': start, 'Limit': limit}
            j = self.http('POST', self.url('query'), {'data': body} if self.cfg.get('envelope', 'data') == 'data' else {'parameters': [json.dumps(body)]})
            if isinstance(j, dict):              # 出错时返回 {Result: {ResponseStatus: …}}
                raise ConnError(_tr('金蝶查询 {form} 失败：{v}', form=form, v=_kd_err(j)))
            rows += j or []
            if not j or len(j) < limit:
                break
            start += limit
        names = [k.strip() for k in keys.split(',')]
        return [dict(zip(names, r)) for r in rows]

    def save(self, what, model):
        form = (self.cfg.get('forms') or {}).get(what) or self.FORMS[what]
        j = self.http('POST', self.url('save'), {'formid': form, 'data': {'Model': model, 'IsAutoSubmitAndAudit': self.cfg.get('auto_audit', True),
                                                                         'IsDeleteEntry': True}})
        st = ((j or {}).get('Result') or {}).get('ResponseStatus') or {}
        if not st.get('IsSuccess'):
            raise ConnError(_tr('金蝶保存 {form} 失败：{v}', form=form, v=_kd_err(j)))
        ok = (st.get('SuccessEntitys') or [{}])[0]
        return {'form': form, 'id': ok.get('Id'), 'number': ok.get('Number')}

    def test(self):
        j = self.login()
        return {'ok': True, 'user': ((j.get('Context') or {}).get('UserName')), 'acct': self.cfg.get('acct_id')}

    def pull(self, entity, since=None):
        self.login()
        filt = (self.cfg.get('filters') or {}).get(entity, '')
        if since and entity in ('material', 'customer', 'supplier', 'sales_order'):
            filt = (filt + ' and ' if filt else '') + f"FModifyDate>='{since:%Y-%m-%d %H:%M:%S}'"
        if entity == 'sales_order':
            filt = (filt + ' and ' if filt else '') + "FDocumentStatus='C'"
        rows = self.query(entity, filt)
        if entity == 'material':
            return [{'ext_id': str(r['FMATERIALID']), 'code': r['FNumber'], 'name': r['FName'], 'spec': r.get('FSpecification') or '',
                     'unit': (self.cfg.get('unit_map') or {}).get(r.get('FBaseUnitId.FNumber'), r.get('FBaseUnitId.FNumber')),
                     'mtype': self.ERPCLS.get(str(r.get('FErpClsID')), 'raw')} for r in rows]
        if entity == 'customer':
            return [{'ext_id': str(r['FCUSTID']), 'code': r['FNumber'], 'name': r['FName'], 'short_name': r.get('FShortName') or '', 'tax_no': r.get('FTaxRegisterCode') or ''}
                    for r in rows]
        if entity == 'supplier':
            return [{'ext_id': str(r['FSupplierId']), 'code': r['FNumber'], 'name': r['FName'], 'short_name': r.get('FShortName') or ''} for r in rows]
        if entity == 'bom':
            g = defaultdict(list)
            for r in rows:
                g[r['FMATERIALID.FNumber']].append({'component': r['FMATERIALIDCHILD.FNumber'], 'qty': float(r['FNUMERATOR'] or 0),
                                                     'base_qty': float(r.get('FDENOMINATOR') or 1)})
            return [{'code': p, 'product': p, 'items': items} for p, items in g.items()]
        if entity == 'sales_order':
            g = {}
            for r in rows:
                so = g.setdefault(r['FBillNo'], {'ext_id': str(r['FID']), 'code': r['FBillNo'], 'order_date': str(r['FDate'])[:10], 'customer': r['FCustId.FNumber'],
                                                 'lines': []})
                so['lines'].append({'seq': int(r['FSaleOrderEntry_FSeq']) * 10, 'material': r['FMaterialId.FNumber'], 'qty': float(r['FQty']), 'price': r.get('FPrice'),
                                    'tax_rate': r.get('FEntryTaxRate'), 'due': str(r.get('FDeliveryDate') or '')[:10] or None})
            return list(g.values())
        raise ConnError(_tr('金蝶对接器不支持拉取 {entity}', entity=entity))

    def _org(self):
        return {'FNumber': self.cfg.get('org') or '100'}

    def push_completion(self, p, ev):
        self.login()
        return self.save('completion', {'FDate': p.get('time', '')[:10] or str(datetime.now().date()), 'FStockOrgId': self._org(), 'FPrdOrgId': self._org(),
                                        'FDescription': _tr('MES 完工 {get}', get=p.get('work_order')),
                                        'FEntity': [{'FMaterialId': {'FNumber': p['product']}, 'FRealQty': p['qty'], 'FStockId': {'FNumber': self.wh(p.get('warehouse'))},
                                                     'FMoBillNo': (p.get('ext') or {}).get('code') or p.get('work_order'), 'FMemo': f"MES:{ev['id']}"}]})

    def push_delivery(self, p, ev):
        self.login()
        return self.save('delivery', {'FDate': p['date'], 'FSaleOrgId': self._org(), 'FStockOrgId': self._org(), 'FCustomerID': {'FNumber': p['customer']},
                                      'FNote': _tr('MES 发货 {code}', code=p['code']),
                                      'FEntity': [{'FMaterialID': {'FNumber': ln['material']}, 'FRealQty': ln['qty'], 'FStockID': {'FNumber': self.wh(ln.get('warehouse'))},
                                                   'FTaxPrice': round((ln.get('price') or 0) * (1 + (ln.get('tax_rate') or 0) / 100), 6), 'FEntryTaxRate': ln.get('tax_rate'),
                                                   'FSoorDerno': p.get('sales_order'), 'FLot': {'FNumber': ln.get('lot') or ''}} for ln in p['lines']]})

    def push_receipt(self, p, ev):
        self.login()
        return self.save('receipt', {'FDate': p['date'], 'FStockOrgId': self._org(), 'FPurchaseOrgId': self._org(), 'FSupplierId': {'FNumber': p['supplier']},
                                     'FInStockEntry': [{'FMaterialId': {'FNumber': ln['material']}, 'FRealQty': ln.get('accepted') or 0,
                                                        'FStockId': {'FNumber': self.wh(ln.get('warehouse'))}, 'FPOOrderNo': p.get('purchase_order'),
                                                        'FLot': {'FNumber': ln.get('lot') or ''}} for ln in p['lines'] if (ln.get('accepted') or 0) > 0]})

    def push_stock(self, p, ev):
        """库存：查 ERP 即时库存（STK_Inventory），MES 和 ERP 的差额生成其他入库 / 其他出库单（配置 stock_mode=none 时只比对不过账）。"""
        if self.cfg.get('stock_mode') == 'none':
            return {'skipped': 'stock_mode=none'}
        self.login()
        have = defaultdict(float)
        for r in self.query('stock'):
            have[(r['FMaterialId.FNumber'], r['FStockId.FNumber'])] += float(r.get('FBaseQty') or 0)
        ins, outs = [], []
        for it in p.get('items') or []:
            k = (it['material'], self.wh(it['warehouse']))
            diff = round(float(it['qty']) - have.get(k, 0.0), 6)
            if diff > 1e-6:
                ins.append({'FMATERIALID': {'FNumber': k[0]}, 'FQty': diff, 'FSTOCKID': {'FNumber': k[1]}})
            elif diff < -1e-6:
                outs.append({'FMaterialId': {'FNumber': k[0]}, 'FQty': -diff, 'FStockId': {'FNumber': k[1]}})
        res = {'in': None, 'out': None, 'diffs': len(ins) + len(outs)}
        if ins:
            res['in'] = self.save('other_in', {'FDate': str(datetime.now().date()), 'FStockOrgId': self._org(), 'FNote': _tr('MES 库存校准 {doc_code}', doc_code=ev['doc_code']), 'FEntity': ins})
        if outs:
            res['out'] = self.save('other_out', {'FDate': str(datetime.now().date()), 'FStockOrgId': self._org(), 'FNote': _tr('MES 库存校准 {doc_code}', doc_code=ev['doc_code']),
                                                 'FEntity': outs})
        return res


def _kd_err(j):
    st = ((j or {}).get('Result') or {}).get('ResponseStatus') or {}
    errs = st.get('Errors') or []
    return _tr('；').join(f"{e.get('FieldName') or ''} {e.get('Message')}".strip() for e in errs) or str(j)[:300]


# ───────────────── 用友 U8 OpenAPI ─────────────────
class YonyouU8(Base):
    """用友 U8 OpenAPI（U8+ 开放平台）：
    令牌   GET {base}/system/token?from_account=&app_key=&app_secret=   → {errcode:"0", token:{id, expiresIn}}
    查询   GET {base}/api/<对象>/batch_get?from_account=&to_account=&app_key=&token=&page_index=&rows_per_page=   → {errcode:"0", page_count, <对象>:[…]}
    新增   POST {base}/api/<对象>/add?…&biz_id=<唯一号>   {<对象>: {…}}   → {errcode:"0", id, tradeid}"""
    kind = 'yonyou_u8'
    ENDPOINTS = {'token': 'system/token', 'get': 'api/{obj}/batch_get', 'add': 'api/{obj}/add'}
    OBJ = {'material': 'inventory', 'customer': 'customer', 'supplier': 'vendor', 'bom': 'bom', 'sales_order': 'saleorder', 'completion': 'productin',
           'delivery': 'saleout', 'receipt': 'purchasein', 'stock': 'currentstock', 'other_in': 'otherin', 'other_out': 'otherout'}

    def _token(self):
        tok = getattr(self, '_tok', None)
        if tok and tok[1] > time.time() + 60:
            return tok[0]
        q = urllib.parse.urlencode({'from_account': self.cfg.get('from_account'), 'app_key': self.cfg.get('app_key'), 'app_secret': self.sec.get('app_secret')})
        j = self.http('GET', f"{self.base}/{self.ep('token')}?{q}")
        if str(j.get('errcode')) != '0':
            raise ConnError(_tr('用友 U8 取令牌失败：{v}', v=j.get('errmsg') or j))
        self._tok = (j['token']['id'], time.time() + int(j['token'].get('expiresIn') or 7200))
        return self._tok[0]

    def _q(self, **kw):
        return urllib.parse.urlencode({'from_account': self.cfg.get('from_account'), 'to_account': self.cfg.get('to_account'), 'app_key': self.cfg.get('app_key'),
                                       'token': self._token(), **({'ds_sequence': self.cfg['ds_sequence']} if self.cfg.get('ds_sequence') else {}), **kw})

    def obj(self, what):
        return (self.cfg.get('objects') or {}).get(what) or self.OBJ[what]

    def batch(self, what, **kw):
        o = self.obj(what)
        rows, page = [], 1
        while True:
            j = self.http('GET', f"{self.base}/{self.ep('get').format(obj=o)}?{self._q(page_index=page, rows_per_page=100, **kw)}")
            if str(j.get('errcode')) != '0':
                raise ConnError(_tr('用友 U8 查询 {o} 失败：{v}', o=o, v=j.get('errmsg') or j))
            rows += j.get(o) or []
            if page >= int(j.get('page_count') or 1):
                break
            page += 1
        return rows

    def add(self, what, payload):
        o = self.obj(what)
        j = self.http('POST', f"{self.base}/{self.ep('add').format(obj=o)}?{self._q(biz_id=uuid.uuid4().hex)}", {o: payload})
        if str(j.get('errcode')) != '0':
            raise ConnError(_tr('用友 U8 新增 {o} 失败：{v}', o=o, v=j.get('errmsg') or j))
        return {'object': o, 'id': j.get('id'), 'tradeid': j.get('tradeid')}

    def test(self):
        self._tok = None
        return {'ok': bool(self._token()), 'account': self.cfg.get('to_account')}

    def pull(self, entity, since=None):
        kw = {'ts_begin': f'{since:%Y-%m-%d %H:%M:%S}'} if since and entity != 'bom' else {}
        rows = self.batch(entity, **kw)
        if entity == 'material':
            return [{'code': r['code'], 'name': r['name'], 'spec': r.get('specs') or '', 'unit': r.get('main_measure_name') or r.get('main_measure') or _tr('件'),
                     'mtype': 'fg' if str(r.get('bself')).lower() in ('1', 'true') else 'raw'} for r in rows]
        if entity == 'customer':
            return [{'code': r['code'], 'name': r['name'], 'short_name': r.get('abbrname') or '', 'tax_no': r.get('taxregcode') or ''} for r in rows]
        if entity == 'supplier':
            return [{'code': r['code'], 'name': r['name'], 'short_name': r.get('abbrname') or ''} for r in rows]
        if entity == 'bom':
            return [{'code': r['parentcode'], 'product': r['parentcode'], 'items': [{'component': e['childcode'], 'qty': float(e.get('baseqtyn') or e.get('qty') or 0),
                                                                                    'base_qty': float(e.get('baseqtyd') or 1)} for e in r.get('entry') or []]} for r in rows]
        if entity == 'sales_order':
            return [{'code': r['code'], 'order_date': str(r.get('date'))[:10], 'customer': r['custcode'],
                     'lines': [{'seq': (i + 1) * 10, 'material': e['inventorycode'], 'qty': float(e['quantity']), 'price': e.get('unitprice'),
                                'tax_rate': e.get('taxrate'), 'due': str(e.get('dpredate') or '')[:10] or None} for i, e in enumerate(r.get('entry') or [])]}
                    for r in rows]
        raise ConnError(_tr('用友 U8 对接器不支持拉取 {entity}', entity=entity))

    def push_completion(self, p, ev):
        return self.add('completion', {'date': p.get('time', '')[:10], 'warehousecode': self.wh(p.get('warehouse')), 'memory': _tr('MES 完工 {get}', get=p.get('work_order')),
                                       'entry': [{'inventorycode': p['product'], 'quantity': p['qty'], 'mocode': (p.get('ext') or {}).get('code') or p.get('work_order')}]})

    def push_delivery(self, p, ev):
        return self.add('delivery', {'date': p['date'], 'custcode': p['customer'], 'warehousecode': self.wh(p['lines'][0].get('warehouse')) if p['lines'] else '',
                                     'memory': _tr('MES 发货 {code}', code=p['code']),
                                     'entry': [{'inventorycode': ln['material'], 'quantity': ln['qty'], 'socode': p.get('sales_order'), 'unitprice': ln.get('price'),
                                                'taxrate': ln.get('tax_rate'), 'batch': ln.get('lot') or ''} for ln in p['lines']]})

    def push_receipt(self, p, ev):
        return self.add('receipt', {'date': p['date'], 'vencode': p['supplier'], 'warehousecode': self.wh(p['lines'][0].get('warehouse')) if p['lines'] else '',
                                    'entry': [{'inventorycode': ln['material'], 'quantity': ln.get('accepted') or 0, 'pocode': p.get('purchase_order'), 'batch': ln.get('lot') or ''}
                                              for ln in p['lines'] if (ln.get('accepted') or 0) > 0]})

    def push_stock(self, p, ev):
        if self.cfg.get('stock_mode') == 'none':
            return {'skipped': 'stock_mode=none'}
        have = defaultdict(float)
        for r in self.batch('stock'):
            have[(r['invcode'], r['whcode'])] += float(r.get('iquantity') or 0)
        res = {'diffs': 0}
        by = defaultdict(list)
        for it in p.get('items') or []:
            k = (it['material'], self.wh(it['warehouse']))
            diff = round(float(it['qty']) - have.get(k, 0.0), 6)
            if abs(diff) > 1e-6:
                by[('other_in' if diff > 0 else 'other_out', k[1])].append({'inventorycode': k[0], 'quantity': abs(diff)})
        for (what, wh), entry in by.items():
            res[f'{what}:{wh}'] = self.add(what, {'date': str(datetime.now().date()), 'warehousecode': wh, 'memory': _tr('MES 库存校准 {doc_code}', doc_code=ev['doc_code']), 'entry': entry})
            res['diffs'] += len(entry)
        return res


# ───────────────── 用友 YonSuite OpenAPI ─────────────────
class YonSuite(Base):
    """用友 YonSuite（YonBIP）开放平台：
    令牌   GET {base}/iuap-api-auth/open-auth/selfAppAuth/getAccessToken?appKey=&timestamp=&signature=
           signature = URLEncode(Base64(HmacSHA256("appKey" + appKey + "timestamp" + timestamp, appSecret)))   → {code:"00000", data:{access_token, expire}}
    调用   {base}/iuap-api-gateway<路径>?access_token=…   列表 POST {pageIndex, pageSize} → {code:"200", data:{recordList, pageCount}}；保存 POST {data:{…}} → {code:"200"}"""
    kind = 'yonsuite'
    ENDPOINTS = {'token': 'iuap-api-auth/open-auth/selfAppAuth/getAccessToken', 'gateway': 'iuap-api-gateway',
                 'material': '/yonbip/digitalModel/product/list', 'customer': '/yonbip/digitalModel/merchant/list', 'supplier': '/yonbip/digitalModel/vendor/list',
                 'sales_order': '/yonbip/sd/voucherorder/list', 'bom': '/yonbip/mfg/bom/list', 'completion': '/yonbip/scm/storeprorecord/save',
                 'delivery': '/yonbip/scm/salesout/save', 'receipt': '/yonbip/scm/purinrecord/save', 'stock': '/yonbip/scm/stock/QueryCurrentStocksByCondition',
                 'other_in': '/yonbip/scm/othinrecord/save', 'other_out': '/yonbip/scm/othoutrecord/save'}

    @staticmethod
    def signature(app_key, ts, secret):
        raw = hmac.new(secret.encode(), f'appKey{app_key}timestamp{ts}'.encode(), hashlib.sha256).digest()
        return base64.b64encode(raw).decode()

    def _token(self):
        tok = getattr(self, '_tok', None)
        if tok and tok[1] > time.time() + 60:
            return tok[0]
        ts = str(int(time.time() * 1000))
        sig = self.signature(self.cfg.get('app_key') or '', ts, self.sec.get('app_secret') or '')
        q = urllib.parse.urlencode({'appKey': self.cfg.get('app_key'), 'timestamp': ts, 'signature': sig})
        j = self.http('GET', f"{self.base}/{self.ep('token')}?{q}")
        if str(j.get('code')) != '00000':
            raise ConnError(_tr('YonSuite 取令牌失败：{v}', v=j.get('message') or j))
        self._tok = (j['data']['access_token'], time.time() + int(j['data'].get('expire') or 7200))
        return self._tok[0]

    def call(self, key, body):
        url = f"{self.base}/{self.ep('gateway')}{self.ep(key)}?access_token={urllib.parse.quote(self._token())}"
        j = self.http('POST', url, body)
        if str(j.get('code')) != '200':
            raise ConnError(_tr('YonSuite {ep} 失败：{v}', ep=self.ep(key), v=j.get('message') or j))
        return j.get('data')

    def lst(self, key, extra=None):
        rows, page = [], 1
        while True:
            d = self.call(key, {'pageIndex': page, 'pageSize': 100, **(extra or {})}) or {}
            rows += d.get('recordList') or []
            if page >= int(d.get('pageCount') or 1):
                break
            page += 1
        return rows

    def test(self):
        self._tok = None
        return {'ok': bool(self._token())}

    def pull(self, entity, since=None):
        extra = {'pubts': f'{since:%Y-%m-%d %H:%M:%S}'} if since else None
        rows = self.lst(entity, extra)
        if entity == 'material':
            return [{'ext_id': str(r.get('id')), 'code': r['code'], 'name': r['name'] if isinstance(r['name'], str) else (r['name'] or {}).get('simplifiedName'),
                     'spec': r.get('modelDescription') or '', 'unit': r.get('unit_Name') or r.get('unitName') or _tr('件'),
                     'mtype': {'1': 'raw', '2': 'fg', '3': 'semi'}.get(str(r.get('materialType') or ''), 'raw')} for r in rows]
        if entity == 'customer':
            return [{'ext_id': str(r.get('id')), 'code': r['code'], 'name': r['name'] if isinstance(r['name'], str) else (r['name'] or {}).get('simplifiedName'),
                     'short_name': r.get('shortname') or ''} for r in rows]
        if entity == 'supplier':
            return [{'ext_id': str(r.get('id')), 'code': r['code'], 'name': r['name'] if isinstance(r['name'], str) else (r['name'] or {}).get('simplifiedName')}
                    for r in rows]
        if entity == 'sales_order':
            return [{'ext_id': str(r.get('id')), 'code': r['code'], 'order_date': str(r.get('vouchdate'))[:10], 'customer': r.get('agentId_code') or r.get('agentCode'),
                     'lines': [{'seq': (i + 1) * 10, 'material': e['productCode'], 'qty': float(e['qty']), 'price': e.get('oriUnitPrice'), 'tax_rate': e.get('taxRate'),
                                'due': str(e.get('hopeReceiveDate') or '')[:10] or None} for i, e in enumerate(r.get('orderDetails') or [])]} for r in rows]
        if entity == 'bom':
            return [{'code': r['productCode'], 'product': r['productCode'], 'items': [{'component': e['productCode'], 'qty': float(e.get('numeratorQuantity') or 0),
                                                                                     'base_qty': float(e.get('denominatorQuantity') or 1)} for e in r.get('bomComponents') or []]}
                    for r in rows]
        raise ConnError(_tr('YonSuite 对接器不支持拉取 {entity}', entity=entity))

    def _org(self):
        return self.cfg.get('org') or 'ORG001'

    def push_completion(self, p, ev):
        d = self.call('completion', {'data': {'org': self._org(), 'vouchdate': p.get('time', '')[:10], 'warehouse_code': self.wh(p.get('warehouse')), '_status': 'Insert',
                                               'memo': _tr('MES 完工 {get}', get=p.get('work_order')),
                                               'storeProRecords': [{'product_code': p['product'], 'qty': p['qty'], 'srcBillNO': p.get('work_order'), '_status': 'Insert'}]}})
        return {'id': (d or {}).get('id'), 'code': (d or {}).get('code')}

    def push_delivery(self, p, ev):
        d = self.call('delivery', {'data': {'salesOrg': self._org(), 'vouchdate': p['date'], 'cust_code': p['customer'], '_status': 'Insert', 'memo': _tr('MES 发货 {code}', code=p['code']),
                                             'details': [{'product_code': ln['material'], 'qty': ln['qty'], 'warehouse_code': self.wh(ln.get('warehouse')),
                                                          'srcBillNO': p.get('sales_order'), 'oriTaxUnitPrice': round((ln.get('price') or 0) * (1 + (ln.get('tax_rate') or 0) / 100), 6),
                                                          'taxRate': ln.get('tax_rate'), 'batchno': ln.get('lot') or '', '_status': 'Insert'} for ln in p['lines']]}})
        return {'id': (d or {}).get('id'), 'code': (d or {}).get('code')}

    def push_receipt(self, p, ev):
        d = self.call('receipt', {'data': {'org': self._org(), 'vouchdate': p['date'], 'vendor_code': p['supplier'], '_status': 'Insert',
                                            'purInRecords': [{'product_code': ln['material'], 'qty': ln.get('accepted') or 0, 'warehouse_code': self.wh(ln.get('warehouse')),
                                                              'srcBillNO': p.get('purchase_order'), '_status': 'Insert'} for ln in p['lines'] if (ln.get('accepted') or 0) > 0]}})
        return {'id': (d or {}).get('id')}

    def push_stock(self, p, ev):
        if self.cfg.get('stock_mode') == 'none':
            return {'skipped': 'stock_mode=none'}
        have = defaultdict(float)
        for r in self.call('stock', {'org': self._org()}) or []:
            have[(r.get('product_code'), r.get('warehouse_code'))] += float(r.get('currentqty') or 0)
        ins, outs = [], []
        for it in p.get('items') or []:
            k = (it['material'], self.wh(it['warehouse']))
            diff = round(float(it['qty']) - have.get(k, 0.0), 6)
            if abs(diff) > 1e-6:
                (ins if diff > 0 else outs).append({'product_code': k[0], 'qty': abs(diff), 'warehouse_code': k[1], '_status': 'Insert'})
        res = {'diffs': len(ins) + len(outs)}
        if ins:
            res['in'] = self.call('other_in', {'data': {'org': self._org(), 'vouchdate': str(datetime.now().date()), '_status': 'Insert', 'othInRecords': ins}})
        if outs:
            res['out'] = self.call('other_out', {'data': {'org': self._org(), 'vouchdate': str(datetime.now().date()), '_status': 'Insert', 'othOutRecords': outs}})
        return res


CLASSES = {'kingdee': Kingdee, 'yonyou_u8': YonyouU8, 'yonsuite': YonSuite}


def _row(c, code):
    r = c.execute(sa.select(T['erp_connector']).where(T['erp_connector'].c.code == code)).mappings().first()
    if not r:
        raise HTTPException(404, _tr('没有对接器 {code}', code=code))
    return dict(r)


def instance(row):
    cls = CLASSES.get(row.get('kind'))
    if not cls:
        raise HTTPException(400, _tr('对接器类型 {get} 不支持', get=row.get('kind')))
    return cls(row)


def push_events(row) -> set:
    return set((row.get('config') or {}).get('push') or DEFAULT_PUSH)


def push(code, ev):
    with db.engine.connect() as c:
        row = _row(c, code)
    return instance(row).push(ev)


def pull(code, entities=None, force=False, user=None):
    """从 ERP 拉取（顺序：物料 → 客户 → 供应商 → BOM → 销售订单），逐条按编码写进 MES（幂等；force=True 不看内容摘要）。"""
    from .integration import upsert
    with db.engine.connect() as c:
        row = _row(c, code)
    conn = instance(row)
    order = ['material', 'customer', 'supplier', 'bom', 'sales_order', 'purchase_order', 'work_order']
    ents = [e for e in order if e in (entities or (row.get('config') or {}).get('pull') or DEFAULT_PULL)]
    since = None if force or not row.get('last_sync') or not (row.get('config') or {}).get('incremental') else row['last_sync'] - timedelta(minutes=5)
    summary = {}
    started = db.clock_wall()
    status, err = 'ok', None
    for ent in ents:
        res = {'total': 0, 'created': 0, 'updated': 0, 'unchanged': 0, 'error': 0, 'errors': []}
        try:
            items = conn.pull(ent, since)
        except (ConnError, KeyError, TypeError, ValueError) as ex:
            res['errors'].append(_tr('拉取失败：{ex}', ex=ex))
            res['error'] += 1
            status, err = 'error', str(ex)
            summary[ent] = res
            continue
        for it in items:
            res['total'] += 1
            key = str(it.get('code') or it.get('product'))
            try:
                with db.engine.begin() as c:
                    if force:
                        c.execute(T['erp_link'].update().where((T['erp_link'].c.entity == ent) & (T['erp_link'].c.system == conn.kind)
                                                               & (T['erp_link'].c.ext_id == str(it.get('ext_id') or key))).values(hash=None))
                    act, _ = upsert(c, ent, {**it, '_system': 'kingdee' if conn.kind == 'kingdee' else 'yonyou'}, conn.kind, it.get('ext_id'), user=user)
                res[act if act in res else 'updated'] += 1
            except HTTPException as ex:
                res['error'] += 1
                res['errors'].append(_tr('{key}：{detail}', key=key, detail=ex.detail))
        summary[ent] = res
        with db.engine.begin() as c:
            c.execute(T['erp_sync_log'].insert().values(channel=f'connector:{code}', direction='in', entity=ent, total=res['total'],
                                                         ok=res['created'] + res['updated'], failed=res['error'], skipped=res['unchanged'],
                                                         detail={'errors': res['errors'][:50], 'calls': conn.calls[-20:]}, started_at=started, ended_at=db.clock_wall(),
                                                         by=(user or {}).get('username') or 'system'))
    if status == 'ok' and any(v['error'] for v in summary.values()):
        status = 'partial'
    with db.engine.begin() as c:
        c.execute(T['erp_connector'].update().where(T['erp_connector'].c.code == code).values(last_sync=started, last_status=status, last_error=err))
    return {'summary': summary, 'status': status}


def scheduled_pull():
    with db.engine.connect() as c:
        rows = [dict(r) for r in c.execute(sa.select(T['erp_connector']).where(T['erp_connector'].c.enabled.is_(True))).mappings()]
    for r in rows:
        mins = float((r.get('config') or {}).get('pull_minutes') or 0)
        if mins > 0 and (not r.get('last_sync') or db.clock_wall() - r['last_sync'] > timedelta(minutes=mins)):
            try:
                pull(r['code'])
            except Exception:          # noqa: BLE001
                pass


def _public(r):
    d = {k: db.jsonable(v) for k, v in r.items() if k != 'secret'}
    d['secret_set'] = sorted((r.get('secret') or {}).keys())
    d['kind_label'] = KINDS.get(r.get('kind'))
    return d


# ───────────────── 接口 ─────────────────
@router.get('/api/erp/connectors')
def list_api(user=Depends(require('erp.connector.manage'))):
    with db.engine.connect() as c:
        rows = [dict(r) for r in c.execute(sa.select(T['erp_connector']).order_by(T['erp_connector'].c.id)).mappings()]
    return {'items': [_public(r) for r in rows], 'kinds': KINDS, 'push_events': DEFAULT_PUSH, 'pull_entities': DEFAULT_PULL}


@router.put('/api/erp/connectors/{code}')
def save_api(code: str, body: dict = Body(...), user=Depends(require('erp.connector.manage'))):
    """{kind, name, base_url, enabled, config: {acct_id, username, app_id, org, from_account, to_account, app_key, wh_map, push, pull, pull_minutes, endpoints…},
    secret: {password | app_secret}}；secret 不传 = 不改。"""
    if body.get('kind') and body['kind'] not in KINDS:
        raise HTTPException(400, _tr('kind 只能是 {join}', join='/'.join(KINDS)))
    t = T['erp_connector']
    with db.engine.begin() as c:
        cur = c.execute(sa.select(t).where(t.c.code == code)).mappings().first()
        vals = {k: body[k] for k in ('name', 'kind', 'base_url', 'enabled', 'config') if k in body}
        if body.get('secret'):
            vals['secret'] = {**((cur or {}).get('secret') or {}), **{k: v for k, v in body['secret'].items() if v}}
        vals['updated_at'] = db.clock_wall()
        if cur:
            c.execute(t.update().where(t.c.id == cur['id']).values(**vals))
        else:
            if not vals.get('kind'):
                raise HTTPException(400, _tr('请选对接类型'))
            c.execute(t.insert().values(code=code, **vals))
        audit.audit(c, 'connector_save', None, None, detail={'code': code, **{k: v for k, v in vals.items() if k not in ('secret', 'updated_at')},
                                                             'secret_changed': bool(body.get('secret'))}, target=f'connector:{code}')
        return _public(dict(c.execute(sa.select(t).where(t.c.code == code)).mappings().first()))


@router.post('/api/erp/connectors/{code}/test')
def test_api(code: str, user=Depends(require('erp.connector.manage'))):
    with db.engine.connect() as c:
        row = _row(c, code)
    conn = instance(row)
    try:
        return {**conn.test(), 'calls': conn.calls}
    except ConnError as ex:
        return {'ok': False, 'error': str(ex), 'calls': conn.calls}


@router.post('/api/erp/connectors/{code}/pull')
def pull_api(code: str, body: dict = Body(default={}), user=Depends(require('erp.connector.manage'))):
    return pull(code, body.get('entities'), bool(body.get('force')), user)


@router.post('/api/erp/connectors/{code}/push')
def push_api(code: str, user=Depends(require('erp.connector.manage'))):
    from .integration import collect, dispatch
    collect()
    return dispatch(channel=f'connector:{code}')
