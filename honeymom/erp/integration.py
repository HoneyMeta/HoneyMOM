"""外部 ERP 集成：模式开关（内置 / 外部 ERP，谁做主）、业务事件 → 投递（Webhook 签名 + 重试、中间库、对接器），中间库同步作业，
标准 REST 接口 /api/erp/v1（API 密钥），集成监控（队列、失败、重试、手动重新同步）。设计说明见 docs/dev/erp.md「外部 ERP 模式」。

事件（event）：wo.completion 完工 / material.issued 领料 / receipt.posted 采购入库 / delivery.posted 发货 / stock.changed 库存变化 /
return.received 退货 / supplier_return.shipped 退供 / so.confirmed|changed|cancelled 订单 / po.sent 采购订单 / ping 测试。
回传开关（postback）按业务分：completion issue receipt delivery stock return。"""
from ..i18n import _tr
from .. import i18n as _i18n
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import audit, compat, context, db, perms
from ..crud import attach_labels, out
from ..db import clock
from ..perms import require
from ..schema import BY_NAME, ERP_TABLES, TABLES
from . import core
from .core import as_date, money, today, who

router = APIRouter()
v1 = APIRouter(tags=['ERP 接口 v1'])
log = logging.getLogger('mes.erp')
P_MODE = perms.declare('erp.mode.manage', 'ERP 模式与主数据归属、回传设置', 'ERP 集成')
P_HOOK = perms.declare('erp.webhook.manage', 'Webhook 订阅管理', 'ERP 集成')
P_CONN = perms.declare('erp.connector.manage', '金蝶 / 用友对接配置与同步', 'ERP 集成')
P_MON = perms.declare('erp.monitor.view', '集成监控（队列、失败、日志）', 'ERP 集成')
P_SYNC = perms.declare('erp.sync.run', '重试投递、手动重新同步、运行中间库同步', 'ERP 集成')
P_API_R = perms.declare('erp.api.read', 'ERP 接口：读（主数据、订单、事件、库存）', 'ERP 集成')
P_API_W = perms.declare('erp.api.write', 'ERP 接口：写（下发主数据、订单、工单）', 'ERP 集成')
EVENTS = {'wo.completion': ('completion', '工单完工'), 'material.issued': ('issue', '生产领料'), 'receipt.posted': ('receipt', '采购入库'),
          'delivery.posted': ('delivery', '销售发货'), 'stock.changed': ('stock', '库存变化'), 'return.received': ('return', '销售退货'),
          'supplier_return.shipped': ('return', '退供应商'), 'so.confirmed': (None, '订单确认'), 'so.changed': (None, '订单变更'),
          'so.cancelled': (None, '订单取消'), 'po.sent': (None, '采购订单下发'), 'ping': (None, '测试')}
BACKOFF = [10, 30, 120, 600, 1800, 7200, 21600]
T = ERP_TABLES


def now():
    return db.clock_wall()


# ───────────────── 事件与投递 ─────────────────
def _channels(c, event) -> list[str]:
    cfg = core.mode_cfg(c)
    pb = EVENTS.get(event, (None, ''))[0]
    if cfg['mode'] == 'external' and pb and not cfg['postback'].get(pb, True):
        return []
    chans = []
    for h in c.execute(sa.select(T['erp_webhook']).where(T['erp_webhook'].c.enabled.is_(True))).mappings():
        evs = h['events'] or ['*']
        if '*' in evs or event in evs or any(e.endswith('.*') and event.startswith(e[:-1]) for e in evs):
            chans.append(f"webhook:{h['id']}")
    if event != 'ping' and cfg['mode'] == 'external' and str(db.setting(c, 'erp_staging_out', '0')) == '1':
        chans.append('staging')
    if event != 'ping' and cfg['mode'] == 'external':
        from .connectors import push_events
        for r in c.execute(sa.select(T['erp_connector']).where(T['erp_connector'].c.enabled.is_(True))).mappings():
            if event in push_events(r):
                chans.append(f"connector:{r['code']}")
    return chans


def emit(c, event, doc_type, doc_code, payload, key=None, channels=None, force=False):
    """记一条业务事件，并给每个通道建一条待投递。没有任何通道、也不是外部 ERP 模式时不记（内置模式下不白白攒数据）。
    key 相同的事件只记一次（幂等）。返回事件 id 或 None。"""
    chans = channels if channels is not None else _channels(c, event)
    cfg = core.mode_cfg(c)
    if not chans and cfg['mode'] != 'external' and not force:
        return None
    et = T['erp_event']
    key = key or f'{event}:{doc_code}:{secrets.token_hex(4)}'
    if c.execute(sa.select(et.c.id).where(et.c.key == key)).first():
        return None
    t = now()
    eid = c.execute(et.insert().values(event=event, doc_type=doc_type, doc_code=doc_code, payload=payload, key=key, created_at=t)).inserted_primary_key[0]
    for ch in chans:
        c.execute(T['erp_delivery'].insert().values(event=eid, channel=ch, status='pending', attempts=0, next_try=t, created_at=t))
    return eid


def event_out(e) -> dict:
    return {'id': e['id'], 'event': e['event'], 'doc_type': e['doc_type'], 'doc_code': e['doc_code'], 'created_at': db.jsonable(e['created_at']), 'data': e['payload']}


def sign(secret: str, ts: str, body: bytes) -> str:
    """签名：HMAC-SHA256(密钥, "时间戳." + 原始请求体) 的十六进制，放在 X-HM-Signature: sha256=<hex>。接收方用同样的算法核对，并拒绝 5 分钟以前的时间戳。"""
    return 'sha256=' + hmac.new(secret.encode(), ts.encode() + b'.' + body, hashlib.sha256).hexdigest()


def verify(secret: str, headers: dict, body: bytes, tolerance=300) -> bool:
    h = {k.lower(): v for k, v in headers.items()}
    ts, sig = h.get('x-hm-timestamp', ''), h.get('x-hm-signature', '')
    if not ts.isdigit() or abs(time.time() - int(ts)) > tolerance:
        return False
    return hmac.compare_digest(sign(secret, ts, body), sig)


def _send_webhook(c_ro, hook, ev, delivery_id):
    body = json.dumps({'id': ev['id'], 'delivery': delivery_id, **event_out(ev)}, ensure_ascii=False, default=str).encode('utf-8')
    ts = str(int(time.time()))
    req = urllib.request.Request(hook['url'], data=body, method='POST', headers={
        'Content-Type': 'application/json; charset=utf-8', 'User-Agent': 'HoneyMOM-Webhook/5.0', 'X-HM-Event': ev['event'], 'X-HM-Delivery': str(delivery_id),
        'X-HM-Timestamp': ts, 'X-HM-Signature': sign(hook['secret'] or '', ts, body)})
    try:
        with urllib.request.urlopen(req, timeout=float(os.environ.get('ERP_WEBHOOK_TIMEOUT', 10))) as r:
            return r.status, r.read(2000).decode('utf-8', 'replace')
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read(2000).decode('utf-8', 'replace')


_dispatch_lock = threading.Lock()


def dispatch(limit=200, channel=None) -> dict:
    """投递到期的待投递（每条在自己的短事务里认领 / 记结果；HTTP 调用不占数据库事务）。失败按 10s/30s/2m/10m/30m/2h/6h 退避重试，
    超过 Webhook 的最大次数（默认 6）记为 dead（监控页可以手动重试）。"""
    from . import connectors
    dt, et = T['erp_delivery'], T['erp_event']
    res = {'sent': 0, 'failed': 0, 'dead': 0}
    with _dispatch_lock:
        with db.engine.connect() as c:
            q = sa.select(dt.c.id).where(dt.c.status.in_(('pending', 'retry')) & (dt.c.next_try <= now()))
            if channel:
                q = q.where(dt.c.channel == channel)
            ids = list(c.execute(q.order_by(dt.c.id).limit(limit)).scalars())
        for did in ids:
            with db.engine.begin() as c:
                n = c.execute(dt.update().where((dt.c.id == did) & dt.c.status.in_(('pending', 'retry'))).values(status='sending')).rowcount
                if n != 1:
                    continue
                d = dict(c.execute(sa.select(dt).where(dt.c.id == did)).mappings().first())
                ev = dict(c.execute(sa.select(et).where(et.c.id == d['event'])).mappings().first())
                hook = None
                if d['channel'].startswith('webhook:'):
                    hook = c.execute(sa.select(T['erp_webhook']).where(T['erp_webhook'].c.id == int(d['channel'].split(':')[1]))).mappings().first()
                    hook = dict(hook) if hook else None
            code, resp, err = None, '', None
            try:
                if d['channel'].startswith('webhook:'):
                    if not hook:
                        raise RuntimeError(_tr('Webhook 已删除'))
                    code, resp = _send_webhook(None, hook, ev, did)
                    if not 200 <= code < 300:
                        err = f'HTTP {code}'
                elif d['channel'] == 'staging':
                    stage_out(ev)
                    code = 200
                elif d['channel'].startswith('connector:'):
                    resp = json.dumps(connectors.push(d['channel'].split(':', 1)[1], ev), ensure_ascii=False, default=str)[:2000]
                    code = 200
            except Exception as ex:          # noqa: BLE001  网络 / 对方系统的任何错误都记下来重试
                err = f'{type(ex).__name__}: {ex}'[:1000]
            with db.engine.begin() as c:
                att = (d['attempts'] or 0) + 1
                if err is None:
                    c.execute(dt.update().where(dt.c.id == did).values(status='sent', attempts=att, last_code=code, response=(resp or '')[:2000], last_error=None,
                                                                       sent_at=now()))
                    if hook:
                        c.execute(T['erp_webhook'].update().where(T['erp_webhook'].c.id == hook['id']).values(last_ok=now(), last_error=None))
                    res['sent'] += 1
                else:
                    mx = int((hook or {}).get('max_attempts') or 6)
                    dead = att >= mx
                    nxt = now() + timedelta(seconds=BACKOFF[min(att - 1, len(BACKOFF) - 1)])
                    c.execute(dt.update().where(dt.c.id == did).values(status='dead' if dead else 'retry', attempts=att, last_code=code, last_error=err,
                                                                       response=(resp or '')[:2000], next_try=None if dead else nxt))
                    if hook:
                        c.execute(T['erp_webhook'].update().where(T['erp_webhook'].c.id == hook['id']).values(last_error=f'{now():%m-%d %H:%M} {err}'[:500]))
                    res['dead' if dead else 'failed'] += 1
    return res


# ───────────────── 中间库 ─────────────────
_stg_engines: dict = {}


def staging_engine():
    """中间库：系统参数 erp_staging_url（或环境变量 ERP_STAGING_URL）指向单独的库（ERP 那边能连的）；没设 = MES 库里的 erp_stg_in / erp_stg_out 两张表。"""
    url = os.environ.get('ERP_STAGING_URL')
    if not url:
        with db.engine.connect() as c:
            url = db.setting(c, 'erp_staging_url') or ''
    if not url:
        return db.engine
    e = _stg_engines.get(url)
    if e is None:
        e = sa.create_engine(url, connect_args={'check_same_thread': False} if url.startswith('sqlite') else {}, pool_pre_ping=True)
        md = sa.MetaData()
        for n in ('erp_stg_in', 'erp_stg_out'):
            T[n].to_metadata(md)
        md.create_all(e)
        _stg_engines[url] = e
    return e


def stage_out(ev):
    se = staging_engine()
    so_ = T['erp_stg_out']
    with se.begin() as c:
        compat.insert_ignore(c, so_, [{'event_id': ev['id'], 'event': ev['event'], 'doc_type': ev['doc_type'], 'doc_code': ev['doc_code'], 'payload': ev['payload'],
                                       'status': 'new', 'created_at': now()}], keys=['event_id'])


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def sync_staging_in(limit=500, user=None) -> dict:
    """处理中间库里 ERP 写进来的记录（status = new，或 error 且重试少于 5 次），按 id 顺序；每条单独事务；同一外部键内容没变就跳过（幂等）。"""
    se = staging_engine()
    st = T['erp_stg_in']
    res = {'done': 0, 'error': 0, 'skipped': 0}
    started = now()
    with se.connect() as c:
        rows = [dict(r) for r in c.execute(sa.select(st).where((st.c.status == 'new') | ((st.c.status == 'error') & (sa.func.coalesce(st.c.attempts, 0) < 5)))
                                           .order_by(st.c.id).limit(limit)).mappings()]
    errs = []
    for r in rows:
        status, err, mid = 'done', None, None
        try:
            with db.engine.begin() as c, context.source('import'):
                act, mid = upsert(c, r['entity'], dict(r['payload'] or {}, **({'code': r['payload'].get('code') or r['ext_key']} if r.get('ext_key') else {})),
                                  'staging', r['ext_key'], op=r.get('op') or 'upsert')
                status = 'skipped' if act == 'unchanged' else 'done'
        except HTTPException as ex:
            status, err = 'error', str(ex.detail)
        except Exception as ex:          # noqa: BLE001
            status, err = 'error', f'{type(ex).__name__}: {ex}'
        res[status if status != 'skipped' else 'skipped'] += 1
        if err:
            errs.append({'id': r['id'], 'entity': r['entity'], 'key': r['ext_key'], 'error': err[:300]})
        with se.begin() as c:
            c.execute(st.update().where(st.c.id == r['id']).values(status=status, error=err, mes_id=mid, attempts=(r['attempts'] or 0) + 1, processed_at=now()))
    if rows:
        with db.engine.begin() as c:
            c.execute(T['erp_sync_log'].insert().values(channel='staging', direction='in', entity='*', total=len(rows), ok=res['done'], failed=res['error'],
                                                         skipped=res['skipped'], detail={'errors': errs[:50]}, started_at=started, ended_at=now(), by=who(user)))
    return res


# ───────────────── 回传采集：完工 / 领料 / 库存 ─────────────────
def _pb_get(c, key, default=0.0):
    r = c.execute(sa.select(T['erp_postback'].c.value).where(T['erp_postback'].c.key == key)).scalar()
    return default if r is None else float(r)


def _pb_set(c, key, value):
    compat.upsert(c, T['erp_postback'], [{'key': key, 'value': float(value), 'updated_at': now()}], keys=['key'])


def collect() -> int:
    """扫一遍：工单完工数增加 → wo.completion；新领料 → material.issued；新的出入库 → stock.changed（变化物料的最新结存）。
    游标存在 erp_postback；内置模式且没有订阅时只推进游标、不记事件。"""
    n = 0
    with db.engine.begin() as c:
        compat.advisory_lock(c, 'erp:collect')
        wt, mi, st = TABLES['work_order'], TABLES['material_issue'], TABLES['stock_txn']
        first = _pb_get(c, 'cursor:init', -1) < 0
        if first:                       # 第一次：从现在开始（不把历史全部回传）
            for w in db.rows(c, 'work_order', wt.c.qty_done > 0):
                _pb_set(c, f"wo:{w['id']}", w['qty_done'] or 0)
            _pb_set(c, 'cursor:issue', c.execute(sa.select(sa.func.max(mi.c.id))).scalar() or 0)
            _pb_set(c, 'cursor:stock', c.execute(sa.select(sa.func.max(st.c.id))).scalar() or 0)
            _pb_set(c, 'cursor:init', 1)
            return 0
        listening = core.mode_cfg(c)['mode'] == 'external' or c.execute(
            sa.select(T['erp_webhook'].c.id).where(T['erp_webhook'].c.enabled.is_(True)).limit(1)).first() is not None
        if not listening:               # 内置模式、没有订阅：只把游标推到最新（不记事件、不算库存），免得以后打开时回传一大堆旧数据
            for w in db.rows(c, 'work_order', wt.c.qty_done > 0, wt.c.status.in_(('released', 'in_progress', 'completed', 'closed'))):
                if (w['qty_done'] or 0) > _pb_get(c, f"wo:{w['id']}") + 1e-9:
                    _pb_set(c, f"wo:{w['id']}", w['qty_done'] or 0)
            for k, t in (('cursor:issue', mi), ('cursor:stock', st)):
                mx = c.execute(sa.select(sa.func.max(t.c.id))).scalar() or 0
                if mx > _pb_get(c, k):
                    _pb_set(c, k, mx)
            return 0
        for w in db.rows(c, 'work_order', wt.c.qty_done > 0, wt.c.status.in_(('released', 'in_progress', 'completed', 'closed'))):
            posted = _pb_get(c, f"wo:{w['id']}")
            if (w['qty_done'] or 0) > posted + 1e-9:
                delta = (w['qty_done'] or 0) - posted
                m = db.get(c, 'material', w['product']) or {}
                fg = core.wh_of_type(c, 'fg')
                n += bool(emit(c, 'wo.completion', 'work_order', w['code'], {
                    'work_order': w['code'], 'product': m.get('code'), 'product_name': m.get('name'), 'unit': m.get('unit'), 'qty': delta,
                    'qty_done': w['qty_done'], 'qty_plan': w['qty'], 'qty_scrap': w['qty_scrap'], 'status': w['status'],
                    'warehouse': core.code_of(c, 'warehouse', fg), 'sales_order': core.code_of(c, 'sales_order', w.get('sales_order')),
                    'ext': _ext(c, 'work_order', w['id']), 'time': db.jsonable(clock.now())}, key=f"wo.completion:{w['id']}:{w['qty_done']}"))
                _pb_set(c, f"wo:{w['id']}", w['qty_done'] or 0)
        cur = _pb_get(c, 'cursor:issue')
        for x in db.rows(c, 'material_issue', mi.c.id > cur, order=mi.c.id, limit=2000):
            m = db.get(c, 'material', x['material']) or {}
            n += bool(emit(c, 'material.issued', 'material_issue', x['code'], {
                'code': x['code'], 'kind': x['kind'], 'work_order': core.code_of(c, 'work_order', x['work_order']), 'material': m.get('code'),
                'qty': x['qty'], 'unit': m.get('unit'), 'warehouse': core.code_of(c, 'warehouse', x['warehouse']), 'lot': x['lot'],
                'time': db.jsonable(x['time'])}, key=f"material.issued:{x['id']}"))
            _pb_set(c, 'cursor:issue', x['id'])
        cur = _pb_get(c, 'cursor:stock')
        rows = db.rows(c, 'stock_txn', st.c.id > cur, order=st.c.id, limit=5000)
        if rows:
            changed = sorted({(r['material'], r['warehouse']) for r in rows} | {(r['material'], r['to_warehouse']) for r in rows if r.get('to_warehouse')})
            items = []
            for m_, wh in changed:
                m = db.get(c, 'material', m_) or {}
                items.append({'material': m.get('code'), 'warehouse': core.code_of(c, 'warehouse', wh), 'qty': round(core.stock_qty(c, m_, wh), 4), 'unit': m.get('unit')})
            n += bool(emit(c, 'stock.changed', 'stock', f"CK{rows[-1]['id']}", {'items': items, 'txn_from': rows[0]['id'], 'txn_to': rows[-1]['id'],
                                                                                 'time': db.jsonable(clock.now())}, key=f"stock.changed:{rows[-1]['id']}"))
            _pb_set(c, 'cursor:stock', rows[-1]['id'])
    return n


def _ext(c, entity, rid):
    r = c.execute(sa.select(T['erp_link']).where((T['erp_link'].c.entity == entity) & (T['erp_link'].c.local_id == rid))).mappings().first()
    return {'system': r['system'], 'id': r['ext_id'], 'code': r['ext_code']} if r else None


async def loop():
    """后台：每 3 秒 采集回传 + 投递；中间库同步按系统参数 erp_staging_in=1 每 30 秒一次；对接器按 erp_connector_pull_min 定时拉取。"""
    await asyncio.sleep(8)
    last_stage = last_pull = 0.0
    from ..ingest import GATE
    while True:
        try:
            if GATE.status().get('buffering'):         # 数据库不可用（采集在写本地缓存）：这一轮跳过
                await asyncio.sleep(3)
                continue
            await asyncio.to_thread(collect)
            await asyncio.to_thread(dispatch)
            if time.time() - last_stage > 30:
                last_stage = time.time()
                with db.engine.connect() as c:
                    on = str(db.setting(c, 'erp_staging_in', '0')) == '1'
                if on:
                    await asyncio.to_thread(sync_staging_in)
            if time.time() - last_pull > 60:
                last_pull = time.time()
                from . import connectors
                await asyncio.to_thread(connectors.scheduled_pull)
        except sa.exc.OperationalError as ex:            # 库忙 / 暂时连不上：下一轮再来
            log.warning(_tr('ERP 集成后台任务：数据库暂时不可用（%s）'), str(ex).splitlines()[0][:160])
        except Exception:          # noqa: BLE001
            log.exception(_tr('ERP 集成后台任务出错'))
        await asyncio.sleep(3)


# ───────────────── 主数据 / 订单 / 工单 写入（接口、中间库、对接器共用） ─────────────────
MTYPE_IN = {'成品': 'fg', '产成品': 'fg', '半成品': 'semi', '原材料': 'raw', '原料': 'raw', '辅料': 'aux', '易耗品': 'consumable', '外购': 'raw', '自制': 'semi'}


def _by_code(c, entity, code, label=None):
    if code in (None, ''):
        return None
    r = db.get(c, entity, code=str(code))
    if not r and entity in ('customer', 'supplier', 'material'):
        lk = c.execute(sa.select(T['erp_link'].c.local_id).where((T['erp_link'].c.entity == entity) & (T['erp_link'].c.ext_id == str(code)))).scalar()
        r = db.get(c, entity, lk) if lk else None
    if not r:
        raise HTTPException(400, _tr('找不到{v} {code}', v=label or BY_NAME[entity].label, code=code))
    return r


def _link(c, entity, local_id, system, ext_id, ext_code, h):
    lt = T['erp_link']
    ext_id = str(ext_id or ext_code)
    r = c.execute(sa.select(lt).where((lt.c.entity == entity) & (lt.c.system == system) & (lt.c.ext_id == ext_id))).mappings().first()
    if r:
        c.execute(lt.update().where(lt.c.id == r['id']).values(local_id=local_id, ext_code=ext_code, hash=h, synced_at=now()))
    else:
        c.execute(lt.insert().values(entity=entity, local_id=local_id, system=system, ext_id=ext_id, ext_code=ext_code, hash=h, synced_at=now()))


def _same(c, entity, system, ext_id, h):
    lt = T['erp_link']
    r = c.execute(sa.select(lt.c.hash, lt.c.local_id).where((lt.c.entity == entity) & (lt.c.system == system) & (lt.c.ext_id == str(ext_id)))).first()
    return r.local_id if r and r.hash == h and r.local_id else None


def _fields(entity, data, skip=()):
    names = {f.name: f for f in BY_NAME[entity].fields}
    out_ = {}
    for k, v in data.items():
        if k in names and k not in skip and not names[k].ro and names[k].t != 'ref':
            out_[k] = v
    return db.coerce(entity, out_)


def upsert(c, entity, data, system='api', ext_key=None, op='upsert', user=None):
    """按编码新增或更新，返回 (动作, 本地 id)；动作：created / updated / unchanged / deleted。内容和上次同步一样 → unchanged（幂等）。"""
    if entity not in ('material', 'customer', 'supplier', 'bom', 'sales_order', 'purchase_order', 'work_order'):
        raise HTTPException(400, _tr('不支持同步 {entity}', entity=entity))
    code = str(data.get('code') or data.get('product') or ext_key or '').strip()
    if not code:
        raise HTTPException(400, _tr('缺少 code'))
    ext_id = str(data.get('ext_id') or ext_key or code)
    h = _hash({k: v for k, v in data.items() if k not in ('ext_id',)})
    same = _same(c, entity, system, ext_id, h)
    if same and op != 'delete':
        return 'unchanged', same
    fn = {'material': _up_master, 'customer': _up_master, 'supplier': _up_master, 'bom': _up_bom, 'sales_order': _up_so, 'purchase_order': _up_po,
          'work_order': _up_wo}[entity]
    # 同步写入：审计来源记"接口"（API 密钥）或"导入"（中间库 / 对接器），ERP 做主的数据也能写
    with context.source('api' if context.current_source() == 'api' else 'import'):
        act, rid = fn(c, entity, data, code, op, user)
    _link(c, entity if entity != 'bom' else 'bom', rid, system, ext_id, code, h)
    return act, rid


def _up_master(c, entity, data, code, op, user):
    cur = db.get(c, entity, code=code)
    if op == 'delete':
        if not cur:
            return 'unchanged', None
        if 'active' in TABLES[entity].c:
            db.update(c, entity, cur['id'], {'active': False}, raw=True)
        return 'deleted', cur['id']
    d = dict(data)
    if entity == 'material' and d.get('mtype') in MTYPE_IN:
        d['mtype'] = MTYPE_IN[d['mtype']]
    vals = _fields(entity, d, skip=('id',))
    vals['code'] = code
    if not vals.get('name') and not cur:
        raise HTTPException(400, _tr('{code} 缺少名称', code=code))
    if cur:
        ch = {k: v for k, v in vals.items() if cur.get(k) != v}
        if ch:
            db.update(c, entity, cur['id'], ch, raw=True)
        return ('updated' if ch else 'unchanged'), cur['id']
    return 'created', db.insert(c, entity, vals, raw=True)


def _up_bom(c, entity, data, code, op, user):
    prod = _by_code(c, 'material', data.get('product') or code, _tr('父件'))
    bt = TABLES['bom']
    items = data.get('items') or data.get('components') or []
    want = {}
    for it in items:
        comp = _by_code(c, 'material', it.get('component') or it.get('material'), _tr('子件'))
        want[comp['id']] = (float(it.get('qty') or 0) / float(it.get('base_qty') or 1), it.get('unit'))
    have = {r['component']: r for r in db.rows(c, 'bom', bt.c.product == prod['id'])}
    ch = 0
    for comp, (q, unit) in want.items():
        if comp in have:
            if abs((have[comp]['qty'] or 0) - q) > 1e-9:
                db.update(c, 'bom', have[comp]['id'], {'qty': q}, raw=True)
                ch += 1
        else:
            db.insert(c, 'bom', {'product': prod['id'], 'component': comp, 'qty': q, 'unit': unit}, raw=True)
            ch += 1
    for comp, r in have.items():
        if comp not in want:
            db.delete(c, 'bom', r['id'])
            ch += 1
    return ('updated' if ch else 'unchanged') if have else 'created', prod['id']


def _up_so(c, entity, data, code, op, user):
    from . import sales
    cust = _by_code(c, 'customer', data.get('customer'), _tr('客户'))
    lines = []
    for i, ln in enumerate(data.get('lines') or []):
        m = _by_code(c, 'material', ln.get('material'), _tr('物料'))
        lines.append({'seq': int(ln.get('seq') or (i + 1) * 10), 'material': m['id'], 'qty': float(ln['qty']), 'price': ln.get('price'),
                      'tax_rate': ln.get('tax_rate'), 'due': ln.get('due') or data.get('due'), 'customer_part_no': ln.get('customer_part_no'),
                      'schedule': ln.get('schedule')})
    cur = db.get(c, 'sales_order', code=code)
    if op == 'delete' or data.get('status') in ('cancelled', 'closed'):
        if cur and cur['status'] not in ('cancelled', 'closed'):
            from ..crud import run_action
            run_action(c, 'sales_order', cur, 'close' if any((x['qty_shipped'] or 0) > 0 for x in core.lines_of(c, 'sales_order', cur['id'])) else 'cancel',
                       {'reason': _tr('ERP 同步：订单已取消 / 关闭')}, user)
            return 'deleted', cur['id']
        return 'unchanged', cur['id'] if cur else None
    head = {'customer': cust['id'], 'order_date': data.get('order_date') or data.get('date'), 'due': data.get('due'), 'customer_po': data.get('customer_po'),
            'ship_to': data.get('ship_to'), 'remark': data.get('remark'), 'mto': bool(data.get('mto'))}
    if not cur:
        sid = core.save_doc(c, 'sales_order', {**{k: v for k, v in head.items() if v is not None}, 'code': code, 'lines': lines}, user)
        db.update(c, 'sales_order', sid, {'source': 'api' if data.get('_system') in (None, 'api') else data['_system'],
                                          'status': 'confirmed', 'credit_note': _tr('外部 ERP 下发（信用由 ERP 控制）')}, raw=True)
        sales._log(c, db.get(c, 'sales_order', sid), 'confirm', {'status': [None, 'confirmed']}, _tr('外部 ERP 下发'), user)
        return 'created', sid
    if cur['status'] == 'draft':
        core.save_doc(c, 'sales_order', {**head, 'lines': lines}, user, cur['id'])
        return 'updated', cur['id']
    old = {x['seq']: x for x in core.lines_of(c, 'sales_order', cur['id'])}
    body = {'reason': data.get('change_reason') or _tr('ERP 同步变更'), 'lines': [], 'remove': [], 'due': data.get('due')}
    for ln in lines:
        o = old.get(ln['seq'])
        body['lines'].append({**ln, 'id': o['id']} if o else ln)
    body['remove'] = [o['id'] for s_, o in old.items() if s_ not in {ln['seq'] for ln in lines} and o['status'] != 'cancelled']
    try:
        sales.change_order(c, cur, body, user)
    except HTTPException as ex:
        if ex.detail in _i18n.variants('没有改动'):
            return 'unchanged', cur['id']
        raise
    return 'updated', cur['id']


def _up_po(c, entity, data, code, op, user):
    sup = _by_code(c, 'supplier', data.get('supplier'), _tr('供应商'))
    lines = []
    for i, ln in enumerate(data.get('lines') or []):
        m = _by_code(c, 'material', ln.get('material'), _tr('物料'))
        lines.append({'seq': int(ln.get('seq') or (i + 1) * 10), 'material': m['id'], 'qty': float(ln['qty']), 'price': ln.get('price'), 'tax_rate': ln.get('tax_rate'),
                      'due': ln.get('due') or data.get('due')})
    cur = db.get(c, 'purchase_order', code=code)
    if op == 'delete' or data.get('status') in ('cancelled', 'closed'):
        if cur and cur['status'] not in ('cancelled', 'closed'):
            db.update(c, 'purchase_order', cur['id'], {'status': 'closed'}, raw=True)
            return 'deleted', cur['id']
        return 'unchanged', cur['id'] if cur else None
    if not cur:
        prev = core.setting(c, 'erp_asl_strict', '1')
        db.set_setting(c, 'erp_asl_strict', '0')         # ERP 下发的采购订单：合格供方由 ERP 管，这里不再拦
        try:
            pid = core.save_doc(c, 'purchase_order', {'code': code, 'supplier': sup['id'], 'order_date': data.get('order_date') or data.get('date'),
                                                      'due': data.get('due'), 'buyer': data.get('buyer'), 'remark': data.get('remark'), 'lines': lines}, user)
        finally:
            db.set_setting(c, 'erp_asl_strict', prev)
        db.update(c, 'purchase_order', pid, {'status': 'sent', 'source': 'api' if data.get('_system') in (None, 'api') else data['_system']}, raw=True)
        return 'created', pid
    if cur['status'] in ('received', 'closed', 'cancelled'):
        return 'unchanged', cur['id']
    old = {x['seq']: x for x in core.lines_of(c, 'purchase_order', cur['id'])}
    ch = 0
    for ln in lines:
        o = old.get(ln['seq'])
        if o:
            upd = {k: ln[k] for k in ('qty', 'price', 'due') if ln.get(k) is not None and (as_date(o[k]) if k == 'due' else o[k]) != (as_date(ln[k]) if k == 'due' else ln[k])}
            if upd.get('qty') is not None and upd['qty'] < (o['qty_received'] or 0):
                raise HTTPException(409, _tr('采购订单 {code} 第 {seq} 行已收 {qty_received:g}，不能改成 {qty:g}', code=code, seq=o['seq'], qty_received=o['qty_received'], qty=upd['qty']))
            if upd:
                db.update(c, 'po_line', o['id'], {k: (as_date(v) if k == 'due' else v) for k, v in upd.items()}, raw=True)
                ch += 1
        else:
            db.insert(c, 'po_line', {**{k: v for k, v in ln.items()}, 'purchase_order': cur['id'], 'tax_rate': ln.get('tax_rate') or core.default_tax(c, 'purchase_order', cur),
                                     'qty_received': 0, 'qty_accepted': 0, 'qty_rejected': 0, 'status': 'open', 'due': as_date(ln.get('due')) or as_date(cur['due'])},
                      raw=True)
            ch += 1
    core.recalc(c, 'purchase_order', cur['id'])
    return ('updated' if ch else 'unchanged'), cur['id']


def _up_wo(c, entity, data, code, op, user):
    prod = _by_code(c, 'material', data.get('product'), _tr('产品'))
    so = db.get(c, 'sales_order', code=str(data['sales_order'])) if data.get('sales_order') else None
    cur = db.get(c, 'work_order', code=code)
    vals = {'product': prod['id'], 'qty': int(round(float(data.get('qty') or 0))), 'due': db.parse_dt(data.get('due')), 'start_plan': db.parse_dt(data.get('start_plan')),
            'priority': int(data.get('priority') or 5), 'customer': data.get('customer'), 'sales_order': so['id'] if so else None, 'remark': data.get('remark')}
    vals = {k: v for k, v in vals.items() if v is not None}
    if op == 'delete' or data.get('status') == 'cancelled':
        if cur and cur['status'] in ('draft', 'released'):
            db.update(c, 'work_order', cur['id'], {'status': 'cancelled'}, raw=True)
            return 'deleted', cur['id']
        return 'unchanged', cur['id'] if cur else None
    if vals.get('qty', 1) <= 0:
        raise HTTPException(400, _tr('工单数量要大于 0'))
    if not cur:
        route = db.get(c, 'route', product=prod['id'], status='active')
        wid = db.insert(c, 'work_order', {**vals, 'code': code, 'route': route['id'] if route else None, 'status': 'draft', 'qty_done': 0, 'qty_scrap': 0}, raw=True)
        if data.get('release') or data.get('status') == 'released':
            from ..crud import run_action
            run_action(c, 'work_order', db.get(c, 'work_order', wid), 'release', {}, user)
        return 'created', wid
    if cur['status'] in ('completed', 'closed', 'cancelled'):
        return 'unchanged', cur['id']
    if cur['status'] != 'draft':
        vals.pop('product', None)
        if vals.get('qty') is not None and vals['qty'] < (cur['qty_done'] or 0):
            raise HTTPException(409, _tr('工单 {code} 已完工 {qty_done}，数量不能改成 {qty}', code=code, qty_done=cur['qty_done'], qty=vals['qty']))
    ch = {k: v for k, v in vals.items() if cur.get(k) != v}
    if ch:
        db.update(c, 'work_order', cur['id'], ch, raw=True)
    if data.get('release') and cur['status'] == 'draft':
        from ..crud import run_action
        run_action(c, 'work_order', db.get(c, 'work_order', cur['id']), 'release', {}, user)
    return ('updated' if ch else 'unchanged'), cur['id']


# ───────────────── 外部 ERP 模式：界面写入拦截 ─────────────────
_CRUD_RX = re.compile(r'^/api/e/([a-z_0-9]+)(?:/(\d+)(?:/do/([a-z_0-9]+))?)?$')
_DOC_RX = re.compile(r'^/api/erp/doc/([a-z_]+)')


def install_middleware(app):
    @app.middleware('http')
    async def erp_owned_mw(request: Request, call_next):
        if request.method in ('POST', 'PUT', 'DELETE') and request.url.path.startswith('/api/') and not (
                request.headers.get('x-api-key') or request.headers.get('authorization', '').startswith('Bearer hmk_')):
            m = _CRUD_RX.match(request.url.path) or _DOC_RX.match(request.url.path)
            if m and m.group(1) in core.OWNED_ENTITIES:
                ent = m.group(1)
                act = m.group(3) if m.re is _CRUD_RX else None
                # 外部 ERP 做主的单据，MES 里只允许"执行"类动作（发货 / 收货 / 工单下达报工这些仍由 MES 做）
                ok_actions = {'work_order': {'release', 'close'}, 'sales_order': {'make_wo', 'close'}, 'purchase_order': {'close'}}
                if not (act and act in ok_actions.get(ent, set())):
                    try:
                        owned = await asyncio.to_thread(core.erp_owns, ent)
                    except Exception:          # noqa: BLE001
                        owned = False
                    if owned:
                        return JSONResponse({'detail': core.owned_msg(ent), 'code': 'erp_owned', 'entity': ent}, status_code=409, headers={'X-Gate': 'erp_owned'})
        return await call_next(request)


# ───────────────── 接口：模式 ─────────────────
@router.get('/api/erp/mode')
def mode_get(user=Depends(perms.require('page.home'))):
    cfg = core.mode_cfg()
    return {**cfg, 'own_keys': core.OWN_KEYS, 'postback_keys': core.POSTBACK_KEYS, 'events': {k: v[1] for k, v in EVENTS.items()}}


@router.put('/api/erp/mode')
def mode_put(body: dict = Body(...), user=Depends(require(P_MODE))):
    m = body.get('mode') or 'builtin'
    if m not in ('builtin', 'external'):
        raise HTTPException(400, _tr('mode 只能是 builtin / external'))
    with db.engine.begin() as c:
        before = core.mode_cfg(c)
        cfg = core.setting_json(c, 'erp_ownership', None) or {}
        if body.get('owner'):
            bad = {k: v for k, v in body['owner'].items() if k not in core.OWN_KEYS or v not in ('mes', 'erp')}
            if bad:
                raise HTTPException(400, _tr('归属设置不对：{bad}', bad=bad))
            cfg['owner'] = {**(cfg.get('owner') or {}), **body['owner']}
        if body.get('postback'):
            cfg['postback'] = {**(cfg.get('postback') or {}), **{k: bool(v) for k, v in body['postback'].items() if k in core.POSTBACK_KEYS}}
        db.set_setting(c, 'erp_mode', m, _tr('ERP 模式：builtin 内置 / external 外部 ERP'))
        db.set_setting(c, 'erp_ownership', json.dumps(cfg, ensure_ascii=False), _tr('外部 ERP 模式下的主数据归属与回传'))
        for k in ('erp_staging_in', 'erp_staging_out'):
            if k in body:
                db.set_setting(c, k, '1' if body[k] else '0', _tr('中间库：') + (_tr('读 ERP 写入的数据') if k.endswith('in') else _tr('把事件写给 ERP')))
        if 'staging_url' in body:
            db.set_setting(c, 'erp_staging_url', body['staging_url'] or '', _tr('中间库连接（空 = MES 库里的 erp_stg_in / erp_stg_out）'))
        core.invalidate_mode()
        after = core.mode_cfg(c)
        audit.audit(c, 'erp_mode', 'setting', None, diff={'mode': [before['mode'], after['mode']]}, detail={'owner': after['owner'], 'postback': after['postback']},
                    target='setting:erp_mode')
    core.invalidate_mode()
    return mode_get(user)


@router.get('/api/erp/settings')
def settings_get(user=Depends(require(P_MON))):
    with db.engine.connect() as c:
        return {k: db.setting(c, k, d) for k, d in (('erp_staging_in', '0'), ('erp_staging_out', '0'), ('erp_staging_url', ''), ('erp_asl_strict', '1'),
                                                     ('erp_cost_method', 'moving_avg'), ('erp_over_ship_pct', '0'), ('erp_over_receive_pct', '5'))}


@router.put('/api/erp/settings')
def settings_put(body: dict = Body(...), user=Depends(require(P_MODE))):
    allowed = {'erp_asl_strict', 'erp_cost_method', 'erp_over_ship_pct', 'erp_over_receive_pct', 'erp_allow_negative', 'erp_voucher_formats', 'erp_kd_book',
               'erp_kd_word', 'erp_u8_maker', 'erp_ys_book', 'erp_connector_pull_min'}
    with db.engine.begin() as c:
        for k, v in body.items():
            if k in allowed:
                db.set_setting(c, k, v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
    return {'ok': True}


# ───────────────── 接口：Webhook ─────────────────
def _hook_out(h):
    d = {k: db.jsonable(v) for k, v in h.items() if k != 'secret'}
    d['secret_hint'] = (h.get('secret') or '')[:4] + '…' if h.get('secret') else ''
    return d


@router.get('/api/erp/webhooks')
def hooks_list(user=Depends(require(P_HOOK))):
    with db.engine.connect() as c:
        return [_hook_out(dict(r)) for r in c.execute(sa.select(T['erp_webhook']).order_by(T['erp_webhook'].c.id)).mappings()]


@router.post('/api/erp/webhooks')
def hooks_create(body: dict = Body(...), user=Depends(require(P_HOOK))):
    url = (body.get('url') or '').strip()
    if not re.match(r'^https?://', url):
        raise HTTPException(400, _tr('URL 要以 http:// 或 https:// 开头'))
    sec = body.get('secret') or 'whsec_' + secrets.token_urlsafe(24)
    with db.engine.begin() as c:
        hid = c.execute(T['erp_webhook'].insert().values(name=body.get('name') or url[:60], url=url, events=body.get('events') or ['*'], secret=sec, enabled=body.get('enabled', True),
                                                          max_attempts=int(body.get('max_attempts') or 6), remark=body.get('remark') or '', created_by=who(user),
                                                          created_at=now())).inserted_primary_key[0]
        audit.audit(c, 'webhook_create', None, None, detail={'id': hid, 'url': url, 'events': body.get('events')}, target=f'webhook:{hid}')
    return {'ok': True, 'id': hid, 'secret': sec}


@router.put('/api/erp/webhooks/{hid}')
def hooks_update(hid: int, body: dict = Body(...), user=Depends(require(P_HOOK))):
    vals = {k: body[k] for k in ('name', 'url', 'events', 'enabled', 'max_attempts', 'remark') if k in body}
    res = {'ok': True}
    if body.get('rotate'):
        vals['secret'] = res['secret'] = 'whsec_' + secrets.token_urlsafe(24)
    with db.engine.begin() as c:
        c.execute(T['erp_webhook'].update().where(T['erp_webhook'].c.id == hid).values(**vals))
        audit.audit(c, 'webhook_update', None, None, detail={'id': hid, **{k: v for k, v in vals.items() if k != 'secret'}, 'rotated': bool(body.get('rotate'))},
                    target=f'webhook:{hid}')
    return res


@router.delete('/api/erp/webhooks/{hid}')
def hooks_delete(hid: int, user=Depends(require(P_HOOK))):
    with db.engine.begin() as c:
        c.execute(T['erp_webhook'].update().where(T['erp_webhook'].c.id == hid).values(enabled=False, url='', remark=_tr('已删除')))
        c.execute(T['erp_delivery'].update().where((T['erp_delivery'].c.channel == f'webhook:{hid}') & T['erp_delivery'].c.status.in_(('pending', 'retry'))).values(
            status='dead', last_error=_tr('Webhook 已删除')))
        audit.audit(c, 'webhook_delete', None, None, detail={'id': hid}, target=f'webhook:{hid}')
    return {'ok': True}


@router.post('/api/erp/webhooks/{hid}/test')
def hooks_test(hid: int, user=Depends(require(P_HOOK))):
    with db.engine.begin() as c:
        eid = emit(c, 'ping', 'test', f'PING{int(time.time())}', {'msg': _tr('蜂元智能 MOM Webhook 测试'), 'by': who(user)}, channels=[f'webhook:{hid}'], force=True)
    r = dispatch(channel=f'webhook:{hid}')
    with db.engine.connect() as c:
        d = c.execute(sa.select(T['erp_delivery']).where(T['erp_delivery'].c.event == eid)).mappings().first()
    return {'ok': d['status'] == 'sent', 'status': d['status'], 'code': d['last_code'], 'error': d['last_error'], 'result': r}


# ───────────────── 接口：监控 ─────────────────
@router.get('/api/erp/monitor')
def monitor(user=Depends(require(P_MON))):
    dt, et, si, so_, sl = T['erp_delivery'], T['erp_event'], T['erp_stg_in'], T['erp_stg_out'], T['erp_sync_log']
    with db.engine.connect() as c:
        by = {}
        for ch, stt, n in c.execute(sa.select(dt.c.channel, dt.c.status, sa.func.count()).group_by(dt.c.channel, dt.c.status)):
            by.setdefault(ch, {})[stt] = n
        hooks = {f"webhook:{r['id']}": r['name'] for r in c.execute(sa.select(T['erp_webhook'])).mappings()}
        fails = [dict(r) for r in c.execute(sa.select(dt, et.c.event.label('event_name'), et.c.doc_code).join(et, et.c.id == dt.c.event).where(
            dt.c.status.in_(('retry', 'dead'))).order_by(dt.c.id.desc()).limit(100)).mappings()]
        recent = [dict(r) for r in c.execute(sa.select(dt, et.c.event.label('event_name'), et.c.doc_code).join(et, et.c.id == dt.c.event)
                                             .order_by(dt.c.id.desc()).limit(60)).mappings()]
        events_24h = c.execute(sa.select(sa.func.count()).select_from(et).where(et.c.created_at >= now() - timedelta(days=1))).scalar()
        logs = [dict(r) for r in c.execute(sa.select(sl).order_by(sl.c.id.desc()).limit(30)).mappings()]
        conns = [{k: db.jsonable(v) for k, v in dict(r).items() if k != 'secret'} for r in c.execute(sa.select(T['erp_connector'])).mappings()]
    se = staging_engine()
    with se.connect() as c:
        stg_in = dict(c.execute(sa.select(si.c.status, sa.func.count()).group_by(si.c.status)).all())
        stg_out = dict(c.execute(sa.select(so_.c.status, sa.func.count()).group_by(so_.c.status)).all())
        stg_err = [dict(r) for r in c.execute(sa.select(si).where(si.c.status == 'error').order_by(si.c.id.desc()).limit(50)).mappings()]
    def fx(rows):
        return [{k: db.jsonable(v) for k, v in r.items()} for r in rows]
    return {'mode': core.mode_cfg(), 'channels': [{'channel': k, 'name': hooks.get(k) or {'staging': _tr('中间库')}.get(k) or k.replace('connector:', '对接器 '), **v}
                                                  for k, v in sorted(by.items())],
            'failures': fx(fails), 'recent': fx(recent), 'events_24h': events_24h, 'staging_in': stg_in, 'staging_out': stg_out, 'staging_errors': fx(stg_err),
            'logs': fx(logs), 'connectors': conns, 'staging_separate': se is not db.engine}


@router.post('/api/erp/deliveries/{did}/retry')
def retry_one(did: int, user=Depends(require(P_SYNC))):
    with db.engine.begin() as c:
        n = c.execute(T['erp_delivery'].update().where((T['erp_delivery'].c.id == did) & T['erp_delivery'].c.status.in_(('retry', 'dead', 'sent'))).values(
            status='pending', next_try=now(), attempts=0)).rowcount
        audit.audit(c, 'erp_retry', None, None, detail={'delivery': did}, target=f'erp_delivery:{did}')
    return {'ok': bool(n), 'result': dispatch()}


@router.post('/api/erp/deliveries/retry_failed')
def retry_failed(body: dict = Body(default={}), user=Depends(require(P_SYNC))):
    dt = T['erp_delivery']
    with db.engine.begin() as c:
        q = dt.update().where(dt.c.status.in_(('retry', 'dead')))
        if body.get('channel'):
            q = q.where(dt.c.channel == body['channel'])
        n = c.execute(q.values(status='pending', next_try=now(), attempts=0)).rowcount
    return {'ok': True, 'requeued': n, 'result': dispatch()}


@router.post('/api/erp/dispatch')
def dispatch_api(user=Depends(require(P_SYNC))):
    c_ = collect()
    return {'ok': True, 'collected': c_, **dispatch()}


@router.post('/api/erp/staging/sync')
def staging_sync_api(user=Depends(require(P_SYNC))):
    return {'ok': True, **sync_staging_in(user=user)}


@router.post('/api/erp/staging/{sid}/retry')
def staging_retry(sid: int, user=Depends(require(P_SYNC))):
    with staging_engine().begin() as c:
        c.execute(T['erp_stg_in'].update().where(T['erp_stg_in'].c.id == sid).values(status='new', attempts=0, error=None))
    return {'ok': True, **sync_staging_in(user=user)}


@router.post('/api/erp/resync')
def resync(body: dict = Body(...), user=Depends(require(P_SYNC))):
    """手动重新同步：
    - {doc_type: delivery_order|purchase_receipt|work_order, code} 重新生成这张单据的回传事件（新事件，所有通道再投一次）；
    - {connector: 代码, entities: [...]} 让对接器重新拉取（不看内容摘要，全部按 ERP 覆盖）。"""
    from . import connectors, purchase, sales
    if body.get('connector'):
        return {'ok': True, **connectors.pull(body['connector'], body.get('entities'), force=True, user=user)}
    dtp, code = body.get('doc_type'), body.get('code')
    with db.engine.begin() as c:
        if dtp == 'delivery_order':
            d = db.get(c, 'delivery_order', code=code)
            if not d or d['status'] != 'posted':
                raise HTTPException(404, _tr('没有这张已过账的发货单'))
            eid = emit(c, 'delivery.posted', 'delivery_order', code, sales.delivery_payload(c, d), key=f'delivery.posted:{code}:resync:{int(time.time())}', force=True)
        elif dtp == 'purchase_receipt':
            r = db.get(c, 'purchase_receipt', code=code)
            if not r or r['status'] != 'done':
                raise HTTPException(404, _tr('没有这张已入库的收货单'))
            eid = emit(c, 'receipt.posted', 'purchase_receipt', code, purchase.receipt_payload(c, r), key=f'receipt.posted:{code}:resync:{int(time.time())}', force=True)
        elif dtp == 'work_order':
            w = db.get(c, 'work_order', code=code)
            if not w:
                raise HTTPException(404, _tr('没有这张工单'))
            m = db.get(c, 'material', w['product']) or {}
            eid = emit(c, 'wo.completion', 'work_order', code, {'work_order': code, 'product': m.get('code'), 'qty': w['qty_done'], 'qty_done': w['qty_done'],
                                                                'status': w['status'], 'resync': True}, key=f'wo.completion:{code}:resync:{int(time.time())}', force=True)
        else:
            raise HTTPException(400, _tr('doc_type 只能是 delivery_order / purchase_receipt / work_order，或者给 connector'))
        audit.audit(c, 'erp_resync', None, None, detail=body, target=f'{dtp}:{code}')
    return {'ok': True, 'event': eid, 'result': dispatch()}


# ───────────────── REST v1（外部 ERP 用 API 密钥调） ─────────────────
V1_ENT = {'materials': 'material', 'customers': 'customer', 'suppliers': 'supplier', 'boms': 'bom', 'sales_orders': 'sales_order',
          'purchase_orders': 'purchase_order', 'work_orders': 'work_order'}


@v1.put('/api/erp/v1/{kind}', summary='下发主数据 / 订单 / 工单（按编码新增或更新，幂等）')
def v1_put(kind: str, body: dict = Body(..., examples=[{'items': [{'code': 'M-1001', 'name': '壳体', 'unit': '件', 'mtype': 'fg'}]}]),
           user=Depends(require(P_API_W))):
    """body: {"items": [...], "system": "kingdee|yonyou|generic"}。每条单独提交（一条错不影响别的）。
    - materials: {code, name, spec, unit, mtype(fg/semi/raw/aux/consumable 或 成品/半成品/原材料…), price, safety_stock}
    - customers / suppliers: {code, name, short_name, tax_no, contact, phone, address, credit_limit, terms_days, tax_rate…}
    - boms: {product, items: [{component, qty, unit, base_qty}]}（整张替换）
    - sales_orders: {code, customer, order_date, due, customer_po, status, lines: [{seq, material, qty, price, tax_rate, due, schedule: [{due, qty}]}]}
    - purchase_orders: {code, supplier, order_date, due, status, lines: [{seq, material, qty, price, due}]}
    - work_orders: {code, product, qty, due, start_plan, sales_order, customer, priority, release: true}
    返回 {results: [{key, action: created|updated|unchanged|deleted|error, id, error}]}。"""
    ent = V1_ENT.get(kind)
    if not ent:
        raise HTTPException(404, _tr('没有 {kind}（可用：{join}）', kind=kind, join=_tr('、').join(V1_ENT)))
    items = body.get('items')
    if not isinstance(items, list):
        raise HTTPException(400, _tr('items 要是列表'))
    if len(items) > 2000:
        raise HTTPException(413, _tr('一次最多 2000 条'))
    system = str(body.get('system') or 'api')[:20]
    res = []
    for it in items:
        key = str(it.get('code') or it.get('product') or '')
        try:
            with db.engine.begin() as c:
                act, rid = upsert(c, ent, {**it, '_system': system if system in ('kingdee', 'yonyou', 'staging') else 'api'}, system, None,
                                  op='delete' if it.get('_delete') else 'upsert', user=user)
            res.append({'key': key, 'action': act, 'id': rid})
        except HTTPException as ex:
            res.append({'key': key, 'action': 'error', 'error': ex.detail})
    with db.engine.begin() as c:
        c.execute(T['erp_sync_log'].insert().values(channel=f'api:{system}', direction='in', entity=ent, total=len(res), ok=sum(r['action'] != 'error' for r in res),
                                                     failed=sum(r['action'] == 'error' for r in res), skipped=sum(r['action'] == 'unchanged' for r in res),
                                                     detail={'errors': [r for r in res if r['action'] == 'error'][:50]}, started_at=now(), ended_at=now(), by=who(user)))
    return {'results': res, 'ok': all(r['action'] != 'error' for r in res)}


@v1.get('/api/erp/v1/events', summary='拉取业务事件（完工 / 领料 / 收货 / 发货 / 库存…），按 id 增量')
def v1_events(after: int = 0, limit: int = 200, types: str = '', user=Depends(require(P_API_R))):
    et = T['erp_event']
    q = sa.select(et).where(et.c.id > after)
    if types:
        q = q.where(et.c.event.in_([x for x in types.split(',') if x]))
    with db.engine.connect() as c:
        rows = [event_out(dict(r)) for r in c.execute(q.order_by(et.c.id).limit(max(1, min(limit, 1000)))).mappings()]
    return {'events': rows, 'next': rows[-1]['id'] if rows else after}


@v1.get('/api/erp/v1/stock', summary='库存结存（按物料 × 仓库）')
def v1_stock(material: str = '', warehouse: str = '', user=Depends(require(P_API_R))):
    st, mt, wt = TABLES['stock'], TABLES['material'], TABLES['warehouse']
    q = sa.select(mt.c.code.label('material'), mt.c.name, mt.c.unit, wt.c.code.label('warehouse'), sa.func.sum(st.c.qty).label('qty')).select_from(st).join(
        mt, mt.c.id == st.c.material).join(wt, wt.c.id == st.c.warehouse).group_by(mt.c.code, mt.c.name, mt.c.unit, wt.c.code)
    if material:
        q = q.where(mt.c.code == material)
    if warehouse:
        q = q.where(wt.c.code == warehouse)
    with db.engine.connect() as c:
        return {'items': [{**dict(r), 'qty': round(float(r.qty or 0), 4)} for r in c.execute(q).mappings()], 'time': db.jsonable(clock.now())}


@v1.get('/api/erp/v1/work_orders/{code}', summary='工单进度')
def v1_wo(code: str, user=Depends(require(P_API_R))):
    with db.engine.connect() as c:
        w = db.get(c, 'work_order', code=code)
        if not w or not perms.in_scope(user, 'work_order', w):
            raise HTTPException(404, _tr('工单不存在'))
        ops = db.rows(c, 'wo_op', TABLES['wo_op'].c.work_order == w['id'], order=TABLES['wo_op'].c.seq)
        attach_labels(c, 'wo_op', ops)
        m = db.get(c, 'material', w['product']) or {}
        return {'code': w['code'], 'product': m.get('code'), 'qty': w['qty'], 'qty_done': w['qty_done'], 'qty_scrap': w['qty_scrap'], 'status': w['status'],
                'due': db.jsonable(w['due']), 'sales_order': core.code_of(c, 'sales_order', w.get('sales_order')),
                'ops': [{'seq': o['seq'], 'process': o.get('process__label'), 'good': o['qty_good'], 'ng': o['qty_ng'], 'status': o['status'],
                         'plan_end': db.jsonable(o['plan_end'])} for o in ops]}


@v1.get('/api/erp/v1/{kind}', summary='读主数据 / 订单 / 工单')
def v1_get(kind: str, code: str = '', updated_since: str = '', limit: int = 500, user=Depends(require(P_API_R))):
    ent = V1_ENT.get(kind)
    if not ent or ent == 'bom':
        raise HTTPException(404, _tr('没有 {kind}', kind=kind))
    t = TABLES[ent]
    q = sa.select(t)
    sc = perms.scope_cond(user, ent, t)
    if sc is not None:
        q = q.where(sc)
    if code:
        q = q.where(t.c.code == code)
    if updated_since:
        q = q.where(t.c.updated_at >= db.parse_dt(updated_since))
    with db.engine.connect() as c:
        rows = attach_labels(c, ent, [dict(r) for r in c.execute(q.order_by(t.c.id).limit(max(1, min(limit, 5000)))).mappings()])
        if ent in core.DOCS:
            for r in rows:
                r['lines'] = out(attach_labels(c, core.DOCS[ent]['line'], core.lines_of(c, ent, r['id'])))
    return {'items': out(rows)}


SWAGGER = """<!doctype html><html><head><meta charset="utf-8"><title>蜂元智能 MOM 接口</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css"></head>
<body><div id="ui"></div><script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
<script>window.ui = SwaggerUIBundle({url: '/api/openapi.json', dom_id: '#ui', persistAuthorization: true});</script>
<noscript>接口说明（OpenAPI JSON）：<a href="/api/openapi.json">/api/openapi.json</a></noscript></body></html>"""


def install_docs(app):
    """/api/docs（Swagger 界面）与 /api/openapi.json（OpenAPI 3 说明，给外部 ERP 开发对接用；API 密钥放 X-API-Key 头）。"""
    def spec():
        if not getattr(app, '_hm_openapi', None):
            s = app.openapi()
            s = json.loads(json.dumps(s))
            s.setdefault('components', {}).setdefault('securitySchemes', {})['ApiKey'] = {'type': 'apiKey', 'in': 'header', 'name': 'X-API-Key',
                                                                                          'description': _tr('系统 › API 密钥 里创建（hmk_…）；权限点 erp.api.read / erp.api.write')}
            s['security'] = [{'ApiKey': []}]
            s['info'] = {'title': _tr('蜂元智能 MOM 接口'), 'version': '5.0',
                         'description': _tr('外部 ERP 对接：先看「ERP 接口 v1」一组（/api/erp/v1/…）。通用增删改查 /api/e/<实体>、时序 /api/ts/… 也能用 API 密钥调。事件推送（Webhook）的签名：X-HM-Signature = "sha256=" + HMAC-SHA256(密钥, X-HM-Timestamp + "." + 请求体)。')}
            app._hm_openapi = s
        return app._hm_openapi

    @app.get('/api/openapi.json', include_in_schema=False)
    def openapi_json():
        return JSONResponse(spec())

    @app.get('/api/docs', include_in_schema=False)
    def docs_page():
        return HTMLResponse(SWAGGER)
