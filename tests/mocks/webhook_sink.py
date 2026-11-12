"""Webhook 接收端模拟（测试用）：核对蜂元智能 MOM 的签名（X-HM-Signature = "sha256=" + HMAC-SHA256(密钥, X-HM-Timestamp + "." + 请求体)），
可以设置"前 N 次返回 500"来测重试。
  POST /hook/<名字>          接收；签名不对返回 401
  POST /mock/config {name, secret, fail_first}     GET /mock/received?name=     POST /mock/reset
单独运行：python tests/mocks/webhook_sink.py --port 9103
"""
import hashlib
import hmac
import json
import time

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse

S = {'cfg': {}, 'got': [], 'counts': {}}


def verify(secret, ts, body, sig, tolerance=300):
    if not ts or not ts.isdigit() or abs(time.time() - int(ts)) > tolerance:
        return False
    want = 'sha256=' + hmac.new(secret.encode(), ts.encode() + b'.' + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, sig or '')


def build():
    app = FastAPI(title='Webhook 接收端模拟')

    @app.post('/hook/{name}')
    async def hook(name: str, req: Request):
        body = await req.body()
        cfg = S['cfg'].get(name) or {}
        S['counts'][name] = S['counts'].get(name, 0) + 1
        ok = verify(cfg.get('secret', ''), req.headers.get('x-hm-timestamp'), body, req.headers.get('x-hm-signature'))
        rec = {'name': name, 'n': S['counts'][name], 'event': req.headers.get('x-hm-event'), 'delivery': req.headers.get('x-hm-delivery'), 'sig_ok': ok,
               'body': json.loads(body or b'{}')}
        S['got'].append(rec)
        if not ok:
            return JSONResponse({'error': 'bad signature'}, status_code=401)
        if S['counts'][name] <= int(cfg.get('fail_first') or 0):
            return JSONResponse({'error': 'simulated failure'}, status_code=500)
        return {'ok': True}

    @app.post('/mock/config')
    def config(body: dict = Body(...)):
        S['cfg'][body['name']] = body
        S['counts'][body['name']] = 0
        return {'ok': True}

    @app.get('/mock/received')
    def received(name: str = ''):
        return [g for g in S['got'] if not name or g['name'] == name]

    @app.post('/mock/reset')
    def reset():
        S.update(cfg={}, got=[], counts={})
        return {'ok': True}
    return app


if __name__ == '__main__':
    import argparse

    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=9103)
    uvicorn.run(build(), host='127.0.0.1', port=ap.parse_args().port)
