"""金蝶云星空 WebAPI 模拟服务（测试用，只模拟 MES 对接要用到的几个接口）：
  POST /k3cloud/Kingdee.BOS.WebApi.ServicesStub.AuthService.ValidateUser.common.kdsvc        {acctID, username, password, lcid}
  POST /k3cloud/Kingdee.BOS.WebApi.ServicesStub.AuthService.LoginByAppSecret.common.kdsvc    {acctid, username, appid, appsecret, lcid}
  POST /k3cloud/Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.ExecuteBillQuery.common.kdsvc   {data: {FormId, FieldKeys, FilterString, StartRow, Limit}}
  POST /k3cloud/Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.Save.common.kdsvc        {formid, data: {Model, IsAutoSubmitAndAudit}}
会话：登录成功写 Cookie kdservice-sessionid；没有会话返回 {"Result": {"ResponseStatus": {"IsSuccess": false, "MsgCode": 1, "Errors": [{"Message": "会话信息已丢失"}]}}}。
测试辅助：GET /mock/state、POST /mock/reset、POST /mock/expire（让会话失效）、POST /mock/fail_next {n}（接下来 n 次保存失败）。
单独运行：python tests/mocks/kingdee_mock.py --port 9101
"""
import secrets

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse

ACCT, USER, PW, APPID, APPSECRET = 'MES_TEST', 'administrator', 'kd@123', 'APP-001', 'kd-app-secret'
EP = '/k3cloud/Kingdee.BOS.WebApi.ServicesStub.'


def initial():
    return {
        'BD_MATERIAL': [{'FMATERIALID': 100001, 'FNumber': 'KD-FG-001', 'FName': '转向器壳体（金蝶）', 'FSpecification': 'QT450', 'FBaseUnitId.FNumber': 'Pcs',
                         'FErpClsID': '2'},
                        {'FMATERIALID': 100002, 'FNumber': 'KD-SF-001', 'FName': '壳体毛坯（金蝶）', 'FSpecification': '铸件', 'FBaseUnitId.FNumber': 'Pcs', 'FErpClsID': '3'},
                        {'FMATERIALID': 100003, 'FNumber': 'KD-RM-001', 'FName': '生铁（金蝶）', 'FSpecification': 'Z14', 'FBaseUnitId.FNumber': 'kg', 'FErpClsID': '1'}],
        'BD_Customer': [{'FCUSTID': 200001, 'FNumber': 'KD-C001', 'FName': '金蝶测试客户一', 'FShortName': '客户一', 'FTaxRegisterCode': '91110000KD0001'},
                        {'FCUSTID': 200002, 'FNumber': 'KD-C002', 'FName': '金蝶测试客户二', 'FShortName': '客户二', 'FTaxRegisterCode': ''}],
        'BD_Supplier': [{'FSupplierId': 300001, 'FNumber': 'KD-S001', 'FName': '金蝶测试供应商', 'FShortName': '供应商一'}],
        'ENG_BOM': [{'FID': 400001, 'FMATERIALID.FNumber': 'KD-FG-001', 'FMATERIALIDCHILD.FNumber': 'KD-SF-001', 'FNUMERATOR': 1, 'FDENOMINATOR': 1},
                    {'FID': 400001, 'FMATERIALID.FNumber': 'KD-SF-001', 'FMATERIALIDCHILD.FNumber': 'KD-RM-001', 'FNUMERATOR': 19, 'FDENOMINATOR': 1}],
        'SAL_SaleOrder': [{'FID': 500001, 'FBillNo': 'XSDD000101', 'FDate': '2026-11-03T00:00:00', 'FCustId.FNumber': 'KD-C001', 'FSaleOrderEntry_FSeq': 1,
                           'FMaterialId.FNumber': 'KD-FG-001', 'FQty': 120, 'FPrice': 380, 'FEntryTaxRate': 13, 'FDeliveryDate': '2026-11-20T00:00:00',
                           'FDocumentStatus': 'C'},
                          {'FID': 500001, 'FBillNo': 'XSDD000101', 'FDate': '2026-11-03T00:00:00', 'FCustId.FNumber': 'KD-C001', 'FSaleOrderEntry_FSeq': 2,
                           'FMaterialId.FNumber': 'KD-FG-001', 'FQty': 80, 'FPrice': 380, 'FEntryTaxRate': 13, 'FDeliveryDate': '2026-11-27T00:00:00',
                           'FDocumentStatus': 'C'},
                          {'FID': 500002, 'FBillNo': 'XSDD000102', 'FDate': '2026-11-03T00:00:00', 'FCustId.FNumber': 'KD-C002', 'FSaleOrderEntry_FSeq': 1,
                           'FMaterialId.FNumber': 'KD-FG-001', 'FQty': 50, 'FPrice': 390, 'FEntryTaxRate': 13, 'FDeliveryDate': '2026-11-25T00:00:00',
                           'FDocumentStatus': 'A'}],               # A = 创建（未审核）：MES 只拉已审核的
        'STK_Inventory': [{'FMaterialId.FNumber': 'KD-FG-001', 'FStockId.FNumber': 'CK01', 'FBaseQty': 10}],
    }


S = {'data': initial(), 'sessions': set(), 'saved': [], 'fail_next': 0, 'logins': 0, 'calls': []}


def _err(msg, code=0):
    return {'Result': {'ResponseStatus': {'IsSuccess': False, 'ErrorCode': 500, 'MsgCode': code, 'Errors': [{'FieldName': '', 'Message': msg, 'DIndex': 0}]}}}


def build():
    app = FastAPI(title='金蝶云星空 WebAPI 模拟')

    def session_ok(req: Request):
        return req.cookies.get('kdservice-sessionid') in S['sessions']

    def login_ok(resp):
        sid = secrets.token_hex(12)
        S['sessions'].add(sid)
        S['logins'] += 1
        r = JSONResponse({'LoginResultType': 1, 'Message': None, 'Context': {'UserName': USER, 'DBid': ACCT, 'UserLocale': 'zh-CN'}, 'KDSVCSessionId': sid})
        r.set_cookie('kdservice-sessionid', sid)
        return r

    @app.post(EP + 'AuthService.ValidateUser.common.kdsvc')
    def validate(body: dict = Body(...)):
        S['calls'].append('login')
        if body.get('acctID') != ACCT or body.get('username') != USER or body.get('password') != PW:
            return {'LoginResultType': 0, 'Message': '用户名或密码错误'}
        return login_ok(None)

    @app.post(EP + 'AuthService.LoginByAppSecret.common.kdsvc')
    def by_app(body: dict = Body(...)):
        S['calls'].append('login_app')
        if body.get('acctid') != ACCT or body.get('appid') != APPID or body.get('appsecret') != APPSECRET:
            return {'LoginResultType': 0, 'Message': '应用 ID 或密钥错误'}
        return login_ok(None)

    @app.post(EP + 'DynamicFormService.ExecuteBillQuery.common.kdsvc')
    def query(req: Request, body: dict = Body(...)):
        if not session_ok(req):
            return _err('会话信息已丢失，请重新登录', 1)
        d = body.get('data')
        if d is None and body.get('parameters'):
            import json
            d = json.loads(body['parameters'][0])
        form = d.get('FormId')
        S['calls'].append(f'query:{form}')
        rows = S['data'].get(form)
        if rows is None:
            return _err(f'业务对象 {form} 不存在')
        keys = [k.strip() for k in d.get('FieldKeys', '').split(',')]
        flt = d.get('FilterString') or ''
        if "FDocumentStatus='C'" in flt:
            rows = [r for r in rows if r.get('FDocumentStatus') == 'C']
        bad = [k for k in keys if rows and k not in rows[0]]
        if bad:
            return _err(f'字段 {bad[0]} 不存在')
        start, limit = int(d.get('StartRow') or 0), int(d.get('Limit') or 2000)
        return [[r.get(k) for k in keys] for r in rows[start:start + limit]]

    @app.post(EP + 'DynamicFormService.Save.common.kdsvc')
    def save(req: Request, body: dict = Body(...)):
        if not session_ok(req):
            return _err('会话信息已丢失，请重新登录', 1)
        form = body.get('formid')
        S['calls'].append(f'save:{form}')
        if S['fail_next'] > 0:
            S['fail_next'] -= 1
            return _err('模拟：数据库繁忙，请稍后再试')
        model = ((body.get('data') or {}).get('Model')) or {}
        entry_key = {'PRD_INSTOCK': 'FEntity', 'SAL_OUTSTOCK': 'FEntity', 'STK_InStock': 'FInStockEntry', 'STK_MISCELLANEOUS': 'FEntity',
                     'STK_MisDelivery': 'FEntity'}.get(form)
        if not entry_key:
            return _err(f'模拟服务不支持保存 {form}')
        entries = model.get(entry_key) or []
        if not entries:
            return _err(f'{form} 明细不能为空')
        if not model.get('FDate'):
            return _err('日期 FDate 必填')
        nums = {r['FNumber'] for r in S['data']['BD_MATERIAL']}
        for i, e in enumerate(entries):
            mat = (e.get('FMaterialId') or e.get('FMaterialID') or e.get('FMATERIALID') or {}).get('FNumber')
            qty = e.get('FRealQty', e.get('FQty'))
            if mat not in nums and not str(mat).startswith(('SGH', 'CM-', 'AUX')):
                return _err(f'第 {i + 1} 行物料 {mat} 在金蝶里不存在')
            if not qty or qty <= 0:
                return _err(f'第 {i + 1} 行数量必须大于 0')
        n = len(S['saved']) + 1
        number = {'PRD_INSTOCK': 'SCRK', 'SAL_OUTSTOCK': 'XSCK', 'STK_InStock': 'CGRK', 'STK_MISCELLANEOUS': 'QTRK', 'STK_MisDelivery': 'QTCK'}[form] + f'{n:06d}'
        S['saved'].append({'form': form, 'number': number, 'model': model, 'audit': (body.get('data') or {}).get('IsAutoSubmitAndAudit')})
        return {'Result': {'ResponseStatus': {'IsSuccess': True, 'Errors': [], 'SuccessEntitys': [{'Id': 900000 + n, 'Number': number, 'DIndex': 0}]},
                           'Id': 900000 + n, 'Number': number, 'NeedReturnData': []}}

    @app.get('/mock/state')
    def state():
        return {'saved': S['saved'], 'logins': S['logins'], 'calls': S['calls'][-50:]}

    @app.post('/mock/reset')
    def reset():
        S.update(data=initial(), sessions=set(), saved=[], fail_next=0, logins=0, calls=[])
        return {'ok': True}

    @app.post('/mock/expire')
    def expire():
        S['sessions'].clear()
        return {'ok': True}

    @app.post('/mock/fail_next')
    def fail_next(body: dict = Body(...)):
        S['fail_next'] = int(body.get('n') or 1)
        return {'ok': True}

    @app.post('/mock/data/{form}')
    def add_data(form: str, body: dict = Body(...)):
        S['data'].setdefault(form, []).extend(body.get('rows') or [])
        return {'ok': True}
    return app


if __name__ == '__main__':
    import argparse

    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=9101)
    uvicorn.run(build(), host='127.0.0.1', port=ap.parse_args().port)
