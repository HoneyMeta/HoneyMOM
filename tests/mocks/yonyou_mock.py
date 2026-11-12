"""用友 U8 OpenAPI + YonSuite OpenAPI 模拟服务（测试用，一个应用里两套接口）：
U8（开放平台）：
  GET  /u8/system/token?from_account=&app_key=&app_secret=                      → {errcode:"0", token:{id, expiresIn}}
  GET  /u8/api/<inventory|customer|vendor|bom|saleorder|currentstock>/batch_get?…&token=&page_index=&rows_per_page=   → {errcode, page_count, <对象>:[…]}
  POST /u8/api/<productin|saleout|purchasein|otherin|otherout>/add?…&token=&biz_id=   {<对象>: {…}}   → {errcode:"0", id, tradeid}
YonSuite（YonBIP）：
  GET  /ys/iuap-api-auth/open-auth/selfAppAuth/getAccessToken?appKey=&timestamp=&signature=     签名 = Base64(HmacSHA256("appKey"+appKey+"timestamp"+timestamp, appSecret))
  POST /ys/iuap-api-gateway/yonbip/digitalModel/product/list?access_token=  {pageIndex, pageSize}  → {code:"200", data:{recordList, pageCount}}
  POST /ys/iuap-api-gateway/yonbip/scm/<storeprorecord|salesout|purinrecord|othinrecord|othoutrecord>/save?access_token=  {data:{…}}
测试辅助：GET /mock/state、POST /mock/reset、POST /mock/expire。单独运行：python tests/mocks/yonyou_mock.py --port 9102
"""
import base64
import hashlib
import hmac
import secrets
import time

from fastapi import Body, FastAPI, Request

U8 = {'from_account': 'MES', 'to_account': '001', 'app_key': 'u8-app-key', 'app_secret': 'u8-app-secret'}
YS = {'app_key': 'ys-app-key', 'app_secret': 'ys-app-secret'}


def initial():
    return {
        'inventory': [{'code': 'U8-FG-01', 'name': '转向器壳体（U8）', 'specs': 'QT450', 'main_measure_name': '件', 'bself': True},
                      {'code': 'U8-RM-01', 'name': '生铁（U8）', 'specs': 'Z14', 'main_measure_name': 't', 'bself': False}],
        'customer': [{'code': 'U8-C01', 'name': 'U8 测试客户', 'abbrname': 'U8客户', 'taxregcode': '91310000U80001'}],
        'vendor': [{'code': 'U8-V01', 'name': 'U8 测试供应商', 'abbrname': 'U8供应商'}],
        'bom': [{'parentcode': 'U8-FG-01', 'entry': [{'childcode': 'U8-RM-01', 'baseqtyn': 0.02, 'baseqtyd': 1}]}],
        'saleorder': [{'code': 'U8SO0001', 'date': '2026-11-02', 'custcode': 'U8-C01',
                       'entry': [{'inventorycode': 'U8-FG-01', 'quantity': 60, 'unitprice': 400, 'taxrate': 13, 'dpredate': '2026-11-22'}]}],
        'currentstock': [{'invcode': 'U8-FG-01', 'whcode': 'W-FG', 'iquantity': 5}],
        'product': [{'id': 7001, 'code': 'YS-FG-01', 'name': {'simplifiedName': '转向器壳体（YS）'}, 'modelDescription': 'QT450', 'unit_Name': '件', 'materialType': '2'},
                    {'id': 7002, 'code': 'YS-RM-01', 'name': '生铁（YS）', 'unit_Name': 't', 'materialType': '1'}],
        'merchant': [{'id': 8001, 'code': 'YS-C01', 'name': {'simplifiedName': 'YS 测试客户'}, 'shortname': 'YS客户'}],
        'ys_vendor': [{'id': 8501, 'code': 'YS-V01', 'name': 'YS 测试供应商'}],
        'voucherorder': [{'id': 9001, 'code': 'YSSO0001', 'vouchdate': '2026-11-03 00:00:00', 'agentId_code': 'YS-C01',
                          'orderDetails': [{'productCode': 'YS-FG-01', 'qty': 30, 'oriUnitPrice': 410, 'taxRate': 13, 'hopeReceiveDate': '2026-11-24'}]}],
        'ys_bom': [{'productCode': 'YS-FG-01', 'bomComponents': [{'productCode': 'YS-RM-01', 'numeratorQuantity': 0.02, 'denominatorQuantity': 1}]}],
        'ys_stock': [{'product_code': 'YS-FG-01', 'warehouse_code': 'W-FG', 'currentqty': 2}],
    }


S = {'data': initial(), 'tokens': {}, 'saved': [], 'calls': []}


def build():
    app = FastAPI(title='用友 U8 / YonSuite OpenAPI 模拟')

    # ───────── U8 ─────────
    def u8_ok(req: Request):
        q = req.query_params
        return q.get('token') in S['tokens'] and S['tokens'][q.get('token')] > time.time() and q.get('to_account') == U8['to_account'] \
            and q.get('app_key') == U8['app_key']

    @app.get('/u8/system/token')
    def u8_token(from_account: str = '', app_key: str = '', app_secret: str = ''):
        S['calls'].append('u8:token')
        if from_account != U8['from_account'] or app_key != U8['app_key'] or app_secret != U8['app_secret']:
            return {'errcode': '40001', 'errmsg': 'app_key 或 app_secret 不正确'}
        tok = 'u8tok_' + secrets.token_hex(8)
        S['tokens'][tok] = time.time() + 7200
        return {'errcode': '0', 'errmsg': '', 'token': {'id': tok, 'expiresIn': 7200}}

    @app.get('/u8/api/{obj}/batch_get')
    def u8_get(obj: str, req: Request, page_index: int = 1, rows_per_page: int = 100):
        S['calls'].append(f'u8:get:{obj}')
        if not u8_ok(req):
            return {'errcode': '40014', 'errmsg': 'token 无效或已过期'}
        rows = S['data'].get(obj)
        if rows is None:
            return {'errcode': '50001', 'errmsg': f'对象 {obj} 不存在'}
        pc = max(1, -(-len(rows) // rows_per_page))
        return {'errcode': '0', 'errmsg': '', 'page_index': page_index, 'rows_per_page': rows_per_page, 'row_count': len(rows), 'page_count': pc,
                obj: rows[(page_index - 1) * rows_per_page: page_index * rows_per_page]}

    @app.post('/u8/api/{obj}/add')
    def u8_add(obj: str, req: Request, body: dict = Body(...)):
        S['calls'].append(f'u8:add:{obj}')
        if not u8_ok(req):
            return {'errcode': '40014', 'errmsg': 'token 无效或已过期'}
        if obj not in ('productin', 'saleout', 'purchasein', 'otherin', 'otherout'):
            return {'errcode': '50001', 'errmsg': f'不支持 {obj}'}
        if not req.query_params.get('biz_id'):
            return {'errcode': '40002', 'errmsg': '缺少 biz_id'}
        doc = body.get(obj) or {}
        if not doc.get('entry'):
            return {'errcode': '50002', 'errmsg': '表体不能为空'}
        for e in doc['entry']:
            if not e.get('inventorycode') or not e.get('quantity') or e['quantity'] <= 0:
                return {'errcode': '50003', 'errmsg': '存货编码 / 数量不正确'}
        n = len(S['saved']) + 1
        S['saved'].append({'sys': 'u8', 'object': obj, 'doc': doc, 'id': f'U8{n:05d}'})
        return {'errcode': '0', 'errmsg': '', 'id': f'U8{n:05d}', 'tradeid': secrets.token_hex(6)}

    # ───────── YonSuite ─────────
    @app.get('/ys/iuap-api-auth/open-auth/selfAppAuth/getAccessToken')
    def ys_token(appKey: str = '', timestamp: str = '', signature: str = ''):
        S['calls'].append('ys:token')
        want = base64.b64encode(hmac.new(YS['app_secret'].encode(), f'appKey{appKey}timestamp{timestamp}'.encode(), hashlib.sha256).digest()).decode()
        if appKey != YS['app_key'] or signature != want:
            return {'code': '310036', 'message': '签名验证失败'}
        if abs(time.time() * 1000 - int(timestamp or 0)) > 300000:
            return {'code': '310037', 'message': '时间戳过期'}
        tok = 'ystok_' + secrets.token_hex(8)
        S['tokens'][tok] = time.time() + 7200
        return {'code': '00000', 'message': '成功！', 'data': {'access_token': tok, 'expire': 7200}}

    LISTS = {'/yonbip/digitalModel/product/list': 'product', '/yonbip/digitalModel/merchant/list': 'merchant', '/yonbip/digitalModel/vendor/list': 'ys_vendor',
             '/yonbip/sd/voucherorder/list': 'voucherorder', '/yonbip/mfg/bom/list': 'ys_bom'}
    SAVES = {'/yonbip/scm/storeprorecord/save': 'storeProRecords', '/yonbip/scm/salesout/save': 'details', '/yonbip/scm/purinrecord/save': 'purInRecords',
             '/yonbip/scm/othinrecord/save': 'othInRecords', '/yonbip/scm/othoutrecord/save': 'othOutRecords'}

    @app.post('/ys/iuap-api-gateway/{path:path}')
    def ys_gateway(path: str, req: Request, body: dict = Body(default={})):
        p = '/' + path
        S['calls'].append(f'ys:{p}')
        tok = req.query_params.get('access_token')
        if tok not in S['tokens'] or S['tokens'][tok] < time.time():
            return {'code': '1090003500065', 'message': '非法token'}
        if p in LISTS:
            rows = S['data'][LISTS[p]]
            pi, ps = int(body.get('pageIndex') or 1), int(body.get('pageSize') or 10)
            return {'code': '200', 'message': '操作成功', 'data': {'pageIndex': pi, 'pageSize': ps, 'recordCount': len(rows), 'pageCount': max(1, -(-len(rows) // ps)),
                                                                'recordList': rows[(pi - 1) * ps: pi * ps]}}
        if p == '/yonbip/scm/stock/QueryCurrentStocksByCondition':
            return {'code': '200', 'message': '操作成功', 'data': S['data']['ys_stock']}
        if p in SAVES:
            d = body.get('data') or {}
            rows = d.get(SAVES[p]) or []
            if not rows:
                return {'code': '999', 'message': '表体不能为空'}
            if any(not r.get('product_code') or not r.get('qty') or r['qty'] <= 0 for r in rows):
                return {'code': '999', 'message': '物料编码 / 数量不正确'}
            n = len(S['saved']) + 1
            S['saved'].append({'sys': 'ys', 'path': p, 'doc': d, 'id': n})
            return {'code': '200', 'message': '操作成功', 'data': {'id': 100000 + n, 'code': f'YS{n:05d}'}}
        return {'code': '404', 'message': f'接口 {p} 不存在'}

    @app.get('/mock/state')
    def state():
        return {'saved': S['saved'], 'calls': S['calls'][-60:]}

    @app.post('/mock/reset')
    def reset():
        S.update(data=initial(), tokens={}, saved=[], calls=[])
        return {'ok': True}

    @app.post('/mock/expire')
    def expire():
        S['tokens'].clear()
        return {'ok': True}
    return app


if __name__ == '__main__':
    import argparse

    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=9102)
    uvicorn.run(build(), host='127.0.0.1', port=ap.parse_args().port)
