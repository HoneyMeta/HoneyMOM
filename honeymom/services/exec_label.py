"""条码标签：模板（设计器维护的版面，mm）→ 浏览器打印（PDF 精确尺寸）/ 斑马 ZPL / TSC TSPL（服务端生成，TCP 9100 直发网络打印机）/
激光打码机（通用 TCP 文本协议，命令模板可配；mode=sim 用内置模拟打码机）。

打印任务 print_job：排队 → 发送（后台线程，失败按 5 s / 20 s / 60 s 重试 3 次，每次尝试记日志）→ 完成 / 失败（可"重试"）。
补打要写原因（权限 label.reprint），记审计。自动打印规则 label_rule：工单下达、零件投产、装箱完成、组托完成、收货、量具放行、委外发出时
调 fire(c, 事件, 对象类型, 对象)。渲染在 honeymom/labelrender.py，条码编码在 honeymom/barcode.py（纯 Python）。

打码机协议（默认命令模板）：  MARK {job} {data}\\r\\n   → 应答一行，匹配 ack 正则（默认 OK）成功，匹配 err 正则（默认 ERR|NG）失败，超时失败。
"""
from ..i18n import _tr
import re
import socket
import threading
import time
from datetime import timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import Response

from .. import audit, db, perms
from ..auth import principal
from ..crud import action, attach_labels, out
from ..db import clock
from ..schema import TABLES

router = APIRouter()
PERM_PRINT = perms.declare('label.print', '打印标签 / 激光打码', '标签打印')
PERM_REPRINT = perms.declare('label.reprint', '补打标签（要写原因，记审计）', '标签打印')
RETRY_S = (5, 20, 60)
DEFAULT_MARK_CMD = 'MARK {job} {data}\\r\\n'


def tpl_of(row) -> dict:
    lay = row.get('layout') or {}
    return {'width_mm': row.get('width_mm') or 100, 'height_mm': row.get('height_mm') or 60, 'elements': lay.get('elements') if isinstance(lay, dict) else lay or []}


def _lr():
    from .. import labelrender
    return labelrender


# ───────────────────────── 标签数据（按用途） ─────────────────────────
def _company(c):
    return db.setting(c, 'company', '') or ''


def data_part(c, sn):
    p = db.get(c, 'part', sn=str(sn))
    if not p:
        raise HTTPException(404, _tr('没有零件 {sn}', sn=sn))
    m = db.get(c, 'material', p['product']) if p['product'] else None
    wo = db.get(c, 'work_order', p['work_order']) if p['work_order'] else None
    from .iatf import special_map
    sp = set(special_map(c, p['product']).values()) if p['product'] else set()
    return {'sn': p['sn'], 'product_code': (m or {}).get('code', ''), 'product_name': (m or {}).get('name', ''), 'heat': p['heat'] or '', 'wo': (wo or {}).get('code', ''),
            'date': (p['born_at'] or clock.now()).strftime('%Y-%m-%d'), 'special_mark': '◆ CC' if 'CC' in sp else '▲ SC' if 'SC' in sp else '', 'qr': p['sn']}


def data_package(c, code):
    from .exec_pack import package_data
    return package_data(c, code)


def data_gauge(c, ref):
    g = db.get(c, 'gauge', int(ref)) if str(ref).isdigit() else db.get(c, 'gauge', code=str(ref))
    if not g:
        raise HTTPException(404, _tr('没有量具 {ref}', ref=ref))
    from .iatf import STATUS_CN
    return {'code': g['code'], 'name': g['name'], 'last_cal': db.jsonable(g['last_cal']) or '—', 'next_due': db.jsonable(g['next_due']) or '—',
            'status_text': STATUS_CN.get(g['status'], g['status']), 'owner': g['owner'] or '', 'location': g['location'] or ''}


def data_location(c, ref):
    loc = db.get(c, 'location', code=str(ref)) or (db.get(c, 'location', int(ref)) if str(ref).isdigit() else None)
    if not loc:
        raise HTTPException(404, _tr('没有库位 {ref}', ref=ref))
    w = db.get(c, 'warehouse', loc['warehouse']) if loc['warehouse'] else None
    return {'code': loc['code'], 'name': loc['name'], 'warehouse': (w or {}).get('name', '')}


def data_lot(c, ref):
    pt = TABLES['part']
    n = c.execute(sa.select(sa.func.count()).select_from(pt).where(pt.c.heat == str(ref))).scalar()
    p = db.get(c, 'part', heat=str(ref))
    m = db.get(c, 'material', p['product']) if p and p['product'] else None
    return {'lot': str(ref), 'material': (m or {}).get('name', ''), 'qty': n, 'date': clock.now().strftime('%Y-%m-%d')}


def data_wo(c, ref):
    w = db.get(c, 'work_order', code=str(ref)) or (db.get(c, 'work_order', int(ref)) if str(ref).isdigit() else None)
    if not w:
        raise HTTPException(404, _tr('没有工单 {ref}', ref=ref))
    m = db.get(c, 'material', w['product'])
    return {'code': w['code'], 'product': f"{m['code']} {m['name']}" if m else '', 'qty': w['qty'], 'due': (w['due'] or clock.now()).strftime('%Y-%m-%d'),
            'customer': w['customer'] or ''}


def data_tool(c, ref):
    t = db.get(c, 'tooling', code=str(ref)) or (db.get(c, 'tooling', int(ref)) if str(ref).isdigit() else None)
    if not t:
        raise HTTPException(404, _tr('没有工装 {ref}', ref=ref))
    return {'code': t['code'], 'name': t['name'], 'shots': t['shots'], 'life': t['life_shots'] or '', 'location': t['location'] or ''}


BUILDERS = {'part': data_part, 'carton': data_package, 'pallet': data_package, 'customer': data_package, 'gauge': data_gauge, 'location': data_location,
            'lot': data_lot, 'wo': data_wo, 'tool': data_tool}


def build_data(c, usage, ref, extra=None):
    fn = BUILDERS.get(usage)
    d = fn(c, ref) if fn and ref not in (None, '') else {}
    d.setdefault('company', _company(c))
    d.setdefault('date', clock.now().strftime('%Y-%m-%d'))
    d.update(extra or {})
    return d


# ───────────────────────── 渲染 ─────────────────────────
def render_payload(c, tpl_row, data, printer=None, copies=1) -> bytes:
    lr = _lr()
    tpl = tpl_of(tpl_row)
    kind = (printer or {}).get('kind') or 'zpl'
    dpi = (printer or {}).get('dpi') or 203
    if kind == 'tspl':
        return lr.render_tspl(tpl, data, dpi=dpi, copies=copies, **({'cjk_font': printer['cjk_font']} if (printer or {}).get('cjk_font') else {}))
    kw = {'cjk_font': printer['cjk_font']} if (printer or {}).get('cjk_font') else {}
    if (printer or {}).get('darkness') is not None:
        kw['darkness'] = printer['darkness']
    return lr.render_zpl(tpl, data, dpi=dpi, copies=copies, **kw)


def mark_text(tpl_row, data):
    """激光打码的内容：模板里第一个条码元素的数据（一件一码 = 零件码），没有条码取零件码。"""
    lr = _lr()
    for el in tpl_of(tpl_row)['elements']:
        if el.get('type') == 'barcode':
            return lr.fill(el.get('data') or '', data)
    return str(data.get('sn') or data.get('code') or '')


def mark_command(marker, job_code, text, data):
    tmpl = (marker.get('cmd_template') or DEFAULT_MARK_CMD).replace('\\r', '\r').replace('\\n', '\n')
    flat = {'job': job_code, 'data': text, 'sn': data.get('sn', text), 'symbology': marker.get('symbology') or 'datamatrix'}
    for k, v in (data or {}).items():
        flat[f'fields.{k}'] = v

    def rep(m):
        return str(flat.get(m.group(1), ''))
    return re.sub(r'\{([\w.]+)\}', rep, tmpl)


# ───────────────────────── 排队 ─────────────────────────
def enqueue(c, tpl_row, data, *, printer=None, marker=None, target='', ref='', copies=1, user=None, reprint=False, reason='', event=None, kind=None):
    copies = max(1, min(int(copies or 1), 500))
    now = clock.now()
    who = (user or {}).get('name') or (user or {}).get('username') or _tr('系统')
    if marker:
        txt = mark_text(tpl_row, data)
        rid = db.insert(c, 'print_job', {'kind': 'mark', 'template': tpl_row['id'] if tpl_row else None, 'marker': marker['id'], 'target': target, 'ref': ref or txt,
                                         'data': data, 'copies': 1, 'status': 'queued', 'attempts': 0, 'reprint': reprint, 'reason': reason or None,
                                         'requested_by': who, 'event': event, 'next_try': now, 'log': []}, raw=True)
        code = db.get(c, 'print_job', rid)['code']
        db.update(c, 'print_job', rid, {'payload': mark_command(marker, code, txt, data)}, raw=True)
        return rid
    if printer and printer['kind'] == 'browser' or kind == 'browser' or not printer:
        rid = db.insert(c, 'print_job', {'kind': 'browser', 'template': tpl_row['id'], 'printer': printer['id'] if printer else None, 'target': target, 'ref': ref,
                                         'data': data, 'copies': copies, 'status': 'done', 'attempts': 0, 'reprint': reprint, 'reason': reason or None,
                                         'requested_by': who, 'event': event, 'sent_at': now, 'log': [{'t': db.jsonable(now), 'ok': True, 'msg': _tr('浏览器打印（PDF）')}]}, raw=True)
        return rid
    payload = render_payload(c, tpl_row, data, printer, copies)
    return db.insert(c, 'print_job', {'kind': 'print', 'template': tpl_row['id'], 'printer': printer['id'], 'target': target, 'ref': ref, 'data': data,
                                      'payload': payload.decode('utf-8') if printer['kind'] != 'tspl' else payload.decode('gbk', errors='replace'),
                                      'copies': copies, 'status': 'queued', 'attempts': 0, 'reprint': reprint, 'reason': reason or None, 'requested_by': who,
                                      'event': event, 'next_try': now, 'log': []}, raw=True)


def fire(c, event, usage_or_kind, ref, user=None, *, customer=None, material=None, extra=None):
    """业务事件触发自动打印：按 label_rule（启用的、客户 / 产品匹配的）给每条规则排一个任务。返回任务 id 列表。"""
    rt = TABLES['label_rule']
    rules = db.rows(c, 'label_rule', rt.c.enabled.is_(True), event=event)
    ids = []
    for r in rules:
        if r['customer'] and customer and r['customer'] != customer:
            continue
        if r['material'] and material and r['material'] != material:
            continue
        tpl = db.get(c, 'label_template', r['template'])
        if not tpl or tpl['status'] == 'obsolete':
            continue
        data = build_data(c, tpl['usage'] if tpl['usage'] in BUILDERS else usage_or_kind, ref, extra)
        pr = db.get(c, 'printer', r['printer']) if r['printer'] else None
        mk = db.get(c, 'marker', r['marker']) if r['marker'] else None
        if pr and not pr['enabled'] or mk and not mk['enabled']:
            continue
        ids.append(enqueue(c, tpl, data, printer=pr, marker=mk, target=f'{usage_or_kind}:{ref}', ref=str(ref), copies=r['copies'] or 1, user=user, event=event))
    if ids:
        _wake.set()
    return ids


_rules_cache = {'t': 0.0, 'events': set()}


def has_rule(c, event) -> bool:
    """有没有启用的自动打印规则（5 秒缓存；采集高频调用）。"""
    if time.time() - _rules_cache['t'] > 5:
        rt = TABLES['label_rule']
        _rules_cache['events'] = set(c.execute(sa.select(rt.c.event).where(rt.c.enabled.is_(True))).scalars())
        _rules_cache['t'] = time.time()
    return event in _rules_cache['events']


def on_part_created(c, part):
    """一件一码：零件投产（采集到浇注 / 收货）→ 按规则打码或打零件标签。出错不影响采集（保存点回滚）。"""
    if not has_rule(c, 'part_create'):
        return
    try:
        with c.begin_nested():
            fire(c, 'part_create', 'part', part['sn'], material=part['product'])
    except Exception:      # noqa: BLE001
        import logging
        logging.getLogger('mes.label').exception(_tr('零件 %s 自动打码失败'), part.get('sn'))


def on_receive(c, material, lot, qty=None, ref='', user=None):
    """收货（内置 ERP 的采购收货 / 来料）后打批次标签：M4 收货过账时调用。"""
    m = db.get(c, 'material', material) if material else None
    return fire(c, 'receive', 'lot', lot, user, material=material, extra={'material': (m or {}).get('name', ''), 'qty': qty if qty is not None else '', 'ref': ref})


# ───────────────────────── 发送（后台线程） ─────────────────────────
def send_raw(host, port, data: bytes, timeout=5.0):
    with socket.create_connection((host, int(port or 9100)), timeout=timeout) as s:
        s.sendall(data)
        try:
            s.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def send_mark(marker, cmd: str):
    """发给打码机并等应答。返回 (ok, 应答文字)。"""
    host, port = marker['host'], marker['port']
    if marker['mode'] == 'sim' or not host:
        from . import marker_sim
        host, port = '127.0.0.1', marker_sim.ensure()
    timeout = (marker['timeout_ms'] or 5000) / 1000
    with socket.create_connection((host, int(port or 9000)), timeout=timeout) as s:
        s.sendall(cmd.encode('utf-8'))
        buf = b''
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            if b'\n' in buf:
                break
    resp = buf.decode('utf-8', errors='replace').strip()
    if not resp:
        return False, _tr('打码机没有应答（超时）')
    if marker['err'] and re.search(marker['err'], resp):
        return False, resp
    if re.search(marker['ack'] or 'OK', resp):
        return True, resp
    return False, _tr('应答不认识：{resp}', resp=resp)


def process_job(jid) -> dict:
    """发送一个任务（同步）。"""
    with db.engine.begin() as c:
        j = db.get(c, 'print_job', jid)
        if not j or j['status'] not in ('queued', 'failed'):
            return j or {}
        db.update(c, 'print_job', jid, {'status': 'sending'}, raw=True)
        dev = db.get(c, 'marker', j['marker']) if j['kind'] == 'mark' else db.get(c, 'printer', j['printer'])
    now = clock.now()
    ok, msg = False, ''
    try:
        if not dev:
            raise RuntimeError(_tr('设备不存在'))
        if j['kind'] == 'mark':
            ok, msg = send_mark(dev, j['payload'] or '')
        else:
            data = (j['payload'] or '').encode('gbk', errors='replace') if dev['kind'] == 'tspl' else (j['payload'] or '').encode('utf-8')
            send_raw(dev['host'], dev['port'], data)
            ok, msg = True, _tr('已发送 {len} 字节到 {host}:{port}', len=len(data), host=dev['host'], port=dev['port'])
    except Exception as ex:      # noqa: BLE001  网络错误都记下来
        ok, msg = False, f'{type(ex).__name__}: {ex}'
    with db.engine.begin() as c:
        j = db.get(c, 'print_job', jid)
        att = (j['attempts'] or 0) + 1
        log = list(j['log'] or []) + [{'t': db.jsonable(now), 'ok': ok, 'msg': msg[:300]}]
        if ok:
            vals = {'status': 'done', 'attempts': att, 'sent_at': now, 'last_error': None, 'log': log, 'next_try': None}
        elif att <= len(RETRY_S):
            vals = {'status': 'queued', 'attempts': att, 'last_error': msg[:190], 'log': log, 'next_try': now + timedelta(seconds=RETRY_S[att - 1])}
        else:
            vals = {'status': 'failed', 'attempts': att, 'last_error': msg[:190], 'log': log, 'next_try': None}
        db.update(c, 'print_job', jid, vals, raw=True)
        if dev:
            ent = 'marker' if j['kind'] == 'mark' else 'printer'
            db.update(c, ent, dev['id'], {'status': 'ok' if ok else 'error', **({'last_ok': now} if ok else {'last_error': msg[:190]})}, raw=True)
        return db.get(c, 'print_job', jid)


_wake = threading.Event()
_worker = {'t': None, 'stop': False}


def _loop():
    jt = TABLES['print_job']
    while not _worker['stop']:
        try:
            with db.engine.connect() as c:
                ids = list(c.execute(sa.select(jt.c.id).where((jt.c.status == 'queued') & sa.or_(jt.c.next_try.is_(None), jt.c.next_try <= clock.now()))
                                     .order_by(jt.c.id).limit(20)).scalars())
            for jid in ids:
                process_job(jid)
        except Exception:      # noqa: BLE001
            import logging
            logging.getLogger('mes.label').exception(_tr('打印队列出错'))
        _wake.wait(1.0)
        _wake.clear()


def start_worker():
    if _worker['t'] and _worker['t'].is_alive():
        return
    with db.engine.begin() as c:        # 上次进程退出时卡在"发送中"的：放回队列
        c.execute(TABLES['print_job'].update().where(TABLES['print_job'].c.status == 'sending').values(status='queued', next_try=clock.now()))
    _worker['stop'] = False
    _worker['t'] = threading.Thread(target=_loop, name='label-queue', daemon=True)
    _worker['t'].start()


# ───────────────────────── 接口 ─────────────────────────
@router.get('/api/label/variables')
def variables(usage: str = 'part', user=Depends(principal)):
    from ..label_builtin import SAMPLES
    s = SAMPLES.get(usage, {})
    return {'usage': usage, 'vars': [{'key': k, 'sample': v} for k, v in s.items()] + [{'key': 'company', 'sample': _tr('公司名称')}, {'key': 'date', 'sample': '2026-11-04'}]}


def _tpl_from_body(c, body):
    if body.get('template'):
        t = db.get(c, 'label_template', int(body['template']))
        if not t:
            raise HTTPException(404, _tr('模板不存在'))
        if body.get('layout') is not None:         # 设计器里还没保存的版面
            t = {**t, 'layout': body['layout'], 'width_mm': body.get('width_mm') or t['width_mm'], 'height_mm': body.get('height_mm') or t['height_mm']}
        return t
    return {'id': None, 'usage': body.get('usage') or 'other', 'width_mm': body.get('width_mm') or 100, 'height_mm': body.get('height_mm') or 60,
            'layout': body.get('layout') or {'elements': []}, 'sample': body.get('data') or {}}


@router.post('/api/label/preview')
def preview(body: dict = Body(...), user=Depends(principal)):
    """预览 / 导出：{template | layout+width_mm+height_mm, data?（缺省用模板样例）, format: svg|zpl|tspl|pdf, dpi?}"""
    lr = _lr()
    with db.engine.connect() as c:
        t = _tpl_from_body(c, body)
        data = body.get('data') or t.get('sample') or {}
        tpl = tpl_of(t)
        fmt = body.get('format') or 'svg'
        if fmt == 'svg':
            return Response(lr.render_svg(tpl, data), media_type='image/svg+xml')
        if fmt == 'pdf':
            return Response(lr.render_pdf(tpl, [data] * max(1, min(int(body.get('copies') or 1), 200))), media_type='application/pdf',
                            headers={'Content-Disposition': 'inline; filename="label.pdf"'})
        pr = {'kind': fmt, 'dpi': int(body.get('dpi') or 203)}
        payload = render_payload(c, t, data, pr)
        problems = lr.validate(tpl)
    return {'format': fmt, 'payload': payload.decode('utf-8') if fmt == 'zpl' else payload.decode('gbk', errors='replace'), 'bytes': len(payload), 'problems': problems}


@router.get('/api/label/barcode.svg')
def barcode_svg(sym: str = 'code128', data: str = 'HM', w: float = 40, h: float = 12, user=Depends(principal)):
    lr = _lr()
    tpl = {'width_mm': w, 'height_mm': h, 'elements': [{'type': 'barcode', 'x': 0, 'y': 0, 'w': w, 'h': h, 'symbology': sym, 'data': data or ' ', 'hr': False}]}
    try:
        svg = lr.render_svg(tpl, {})
    except ValueError as ex:
        raise HTTPException(400, _tr('条码内容不能编码：{ex}', ex=ex))
    return Response(svg, media_type='image/svg+xml', headers={'Cache-Control': 'max-age=600'})


@router.post('/api/label/print')
def print_label(body: dict = Body(...), user=Depends(perms.require(PERM_PRINT))):
    """打印 / 打码：{template, printer? | marker?, ref?（零件码、箱号、量具…，按模板用途取数据）, data?（直接给数据）, copies?}。
    printer 为空或浏览器打印机 → 返回 pdf 地址（前端打开打印）。"""
    with db.engine.begin() as c:
        t = db.get(c, 'label_template', int(body['template'])) if body.get('template') else None
        if not t:
            raise HTTPException(400, _tr('请选择标签模板'))
        data = body.get('data') or build_data(c, t['usage'], body.get('ref'))
        pr = db.get(c, 'printer', int(body['printer'])) if body.get('printer') else None
        mk = db.get(c, 'marker', int(body['marker'])) if body.get('marker') else None
        if pr and not pr['enabled'] or mk and not mk['enabled']:
            raise HTTPException(409, _tr('设备已停用'))
        jid = enqueue(c, t, data, printer=pr, marker=mk, target=f"{t['usage']}:{body.get('ref') or ''}", ref=str(body.get('ref') or ''),
                      copies=body.get('copies') or 1, user=user)
        j = db.get(c, 'print_job', jid)
    _wake.set()
    res = {'ok': True, 'id': jid, 'code': j['code'], 'kind': j['kind'], 'status': j['status']}
    if j['kind'] == 'browser':
        res['pdf'] = f'/api/label/job/{jid}/pdf'
    if body.get('wait') and j['kind'] != 'browser':
        res.update(_wait(jid, float(body.get('wait'))))
    return res


def _wait(jid, secs):
    t0 = time.time()
    while time.time() - t0 < min(secs, 30):
        with db.engine.connect() as c:
            j = db.get(c, 'print_job', jid)
        if j['status'] in ('done', 'failed') or (j['status'] == 'queued' and j['attempts']):
            return {'status': j['status'], 'last_error': j['last_error'], 'attempts': j['attempts']}
        time.sleep(0.2)
    return {'status': j['status']}


@router.post('/api/label/reprint')
def reprint(body: dict = Body(...), user=Depends(perms.require(PERM_REPRINT))):
    """补打：{job, reason, printer?}。原任务内容重新排队（标记补打），记审计（谁、为什么、补打了哪个标签）。"""
    reason = (body.get('reason') or '').strip()
    if len(reason) < 2:
        raise HTTPException(400, _tr('补打要写原因（如：标签污损、贴错、打印机卡纸）'))
    with db.engine.begin() as c:
        j = db.get(c, 'print_job', int(body['job'])) if body.get('job') else None
        if not j:
            raise HTTPException(404, _tr('原打印任务不存在'))
        t = db.get(c, 'label_template', j['template']) if j['template'] else None
        pr = db.get(c, 'printer', int(body['printer'])) if body.get('printer') else (db.get(c, 'printer', j['printer']) if j['printer'] else None)
        mk = db.get(c, 'marker', j['marker']) if j['marker'] else None
        if not t:
            raise HTTPException(400, _tr('原任务没有模板，不能补打'))
        nid = enqueue(c, t, j['data'] or {}, printer=pr if not mk else None, marker=mk, target=j['target'], ref=j['ref'], copies=body.get('copies') or j['copies'] or 1,
                      user=user, reprint=True, reason=reason)
        audit.audit(c, 'reprint', 'print_job', nid, detail={'original': j['code'], 'ref': j['ref'], 'reason': reason, 'template': t['code']})
        n = db.get(c, 'print_job', nid)
    _wake.set()
    return {'ok': True, 'id': nid, 'code': n['code'], 'kind': n['kind'], **({'pdf': f'/api/label/job/{nid}/pdf'} if n['kind'] == 'browser' else {})}


@router.get('/api/label/jobs')
def jobs(status: str = '', ref: str = '', limit: int = 100, user=Depends(perms.require('label.print_job.read'))):
    jt = TABLES['print_job']
    cond = []
    if status:
        cond.append(jt.c.status == status)
    if ref:
        cond.append(sa.or_(jt.c.ref.ilike(f'%{ref}%'), jt.c.target.ilike(f'%{ref}%'), jt.c.code.ilike(f'%{ref}%')))
    with db.engine.connect() as c:
        rows = attach_labels(c, 'print_job', db.rows(c, 'print_job', *cond, order=jt.c.id.desc(), limit=min(500, limit)))
        cnt = dict(c.execute(sa.select(jt.c.status, sa.func.count()).group_by(jt.c.status)).all())
    for r in rows:
        r['payload'] = (r['payload'] or '')[:4000]
    return {'items': out(rows), 'counts': cnt}


@router.get('/api/label/job/{jid}/pdf')
def job_pdf(jid: int, user=Depends(perms.require('label.print_job.read'))):
    lr = _lr()
    with db.engine.connect() as c:
        j = db.get(c, 'print_job', jid)
        t = db.get(c, 'label_template', j['template']) if j and j['template'] else None
        if not t:
            raise HTTPException(404, _tr('任务不存在或没有模板'))
    pdf = lr.render_pdf(tpl_of(t), [j['data'] or {}] * max(1, j['copies'] or 1))
    return Response(pdf, media_type='application/pdf', headers={'Content-Disposition': f'inline; filename="label-{jid}.pdf"'})


@action('print_job', 'retry')
def _job_retry(c, j, body, user):
    if j['status'] not in ('failed', 'cancelled', 'queued'):
        raise HTTPException(400, _tr('只有失败 / 取消的任务可以重试'))
    db.update(c, 'print_job', j['id'], {'status': 'queued', 'attempts': 0, 'next_try': clock.now()}, raw=True)
    _wake.set()
    return {'msg': _tr('已重新排队')}


@action('print_job', 'cancel')
def _job_cancel(c, j, body, user):
    if j['status'] not in ('queued', 'failed'):
        raise HTTPException(400, _tr('这个任务已经发出去了'))
    db.update(c, 'print_job', j['id'], {'status': 'cancelled'}, raw=True)
    return {'msg': _tr('已取消')}


TEST_TPL = {'id': None, 'usage': 'other', 'width_mm': 60, 'height_mm': 40, 'layout': {'elements': [
    {'type': 'box', 'x': 1, 'y': 1, 'w': 58, 'h': 38, 't': 0.4}, {'type': 'text', 'x': 4, 'y': 3, 'h': 4, 'text': '蜂元智能 MOM 测试打印', 'bold': True},
    {'type': 'text', 'x': 4, 'y': 9, 'h': 2.8, 'text': '{{printer}}  {{time}}'}, {'type': 'barcode', 'x': 4, 'y': 15, 'w': 34, 'h': 10, 'symbology': 'code128', 'data': 'TEST-{{code}}'},
    {'type': 'barcode', 'x': 42, 'y': 14, 'w': 14, 'h': 14, 'symbology': 'datamatrix', 'data': 'TEST-{{code}}'},
    {'type': 'text', 'x': 4, 'y': 30, 'h': 2.5, 'text': 'ZPL/TSPL 直连 TCP 9100'}]}}


@action('printer', 'test')
def _printer_test(c, p, body, user):
    if p['kind'] == 'browser':
        return {'msg': _tr('浏览器打印机：在打印对话框里选实际打印机'), 'pdf': '/api/label/preview'}
    data = {'printer': p['code'], 'time': clock.now().strftime('%Y-%m-%d %H:%M'), 'code': p['code']}
    payload = render_payload(c, TEST_TPL, data, p)
    try:
        send_raw(p['host'], p['port'], payload, timeout=4)
    except OSError as ex:
        db.update(c, 'printer', p['id'], {'status': 'error', 'last_error': str(ex)[:190]}, raw=True)
        raise HTTPException(409, _tr('连不上打印机 {host}:{port}：{ex}', host=p['host'], port=p['port'], ex=ex))
    db.update(c, 'printer', p['id'], {'status': 'ok', 'last_ok': clock.now(), 'last_error': None}, raw=True)
    return {'msg': _tr('测试页已发送（{len} 字节）', len=len(payload))}


@action('marker', 'test')
def _marker_test(c, m, body, user):
    cmd = mark_command(m, 'TEST', body.get('data') or 'TEST-' + m['code'], {})
    try:
        ok, resp = send_mark(m, cmd)
    except OSError as ex:
        ok, resp = False, str(ex)
    db.update(c, 'marker', m['id'], {'status': 'ok' if ok else 'error', **({'last_ok': clock.now(), 'last_error': None} if ok else {'last_error': resp[:190]})}, raw=True)
    if not ok:
        raise HTTPException(409, _tr('打码机测试失败：{resp}', resp=resp))
    return {'msg': _tr('打码机应答：{resp}', resp=resp)}


@action('label_template', 'copy')
def _tpl_copy(c, t, body, user):
    data = {k: v for k, v in t.items() if k not in ('id', 'created_at', 'updated_at')}
    code = f"{t['code']}-COPY"
    i = 1
    while db.get(c, 'label_template', code=code):
        i += 1
        code = f"{t['code']}-COPY{i}"
    data.update(code=code, name=_tr('{name}（副本）', name=t['name']), status='draft')
    nid = db.insert(c, 'label_template', data, raw=True)
    return {'id': nid, 'msg': _tr('已复制为 {code}', code=code)}


@router.get('/api/label/marker_sim/log')
def sim_log(user=Depends(principal)):
    from . import marker_sim
    return {'port': marker_sim.PORT.get('port'), 'marks': list(marker_sim.LOG)[-100:]}

