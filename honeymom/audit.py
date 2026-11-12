"""审计日志：谁、何时、对哪个对象做了什么、改前 → 改后、来源（界面/接口/AI/智能体/导入/采集）、客户端 IP。说明见 docs/dev/foundation.md「审计」。

- 自动：请求 / 智能体 / AI 动作里通过 db.insert / db.update / db.delete 的写入都会自动记一条（crud 通用接口也走这里）；
  采集写入不逐条记，每 10 分钟记一条批量汇总（collector_batch）。
- 手动：服务里直接 c.execute(t.update()…) 改了业务数据的，自己调 audit(conn, '动作', '实体', id, diff={字段: [旧, 新]}, detail={…})。
- 只追加：表上有触发器，已封存的行不能改、任何行不能删；每行封存时按顺序编号（seq）并算哈希链
  hash = sha256(prev_hash + 规范化内容)，verify() 从头核对，改一行、删一行、插一行都能发现。
- 封存（seal）在后台每 2 秒跑一次（多进程用数据库咨询锁），查询和核对前也会先封存。
"""
from .i18n import _tr
import hashlib
import json
import threading
from datetime import datetime

import sqlalchemy as sa

from . import context, db
from .schema import audit_t

SENSITIVE = {'password_hash', 'password', 'key_hash', 'token', 'api_key', 'secret'}
SKIP_ENTITIES = {'part_event', 'ai_log', 'tag_latest', 'tag_sample'}       # 高频的系统流水，不逐条审计
_seal_lock = threading.Lock()


def _safe(v):
    """转成可存 JSON 的值（datetime → 字符串），敏感字段打码。"""
    if v is None:
        return None
    return json.loads(json.dumps(v, ensure_ascii=False, default=lambda x: db.jsonable(x) if isinstance(x, datetime) or hasattr(x, 'isoformat') else str(x)))


def _who(user):
    if isinstance(user, str):
        return user
    u = user if user is not None else context.actor()
    return (u or {}).get('username') or 'system'


def audit(conn, action: str, entity: str | None = None, entity_id: int | None = None, diff: dict | None = None, detail=None,
          target: str | None = None, user=None, source: str | None = None, kind: str | None = None):
    """记一条审计。conn 用业务写入的同一个事务（回滚就一起没了）。user 缺省取当前请求的人；source 缺省取当前上下文。"""
    if diff:
        diff = {k: (['***', '***'] if k in SENSITIVE else v) for k, v in diff.items()}
    conn.execute(audit_t.insert().values(
        time=db.clock_wall(), user=_who(user)[:80], kind=(kind or action)[:40], action=(action or '')[:60], entity=entity, entity_id=entity_id,
        target=(target or (f'{entity}:{entity_id}' if entity else ''))[:190], diff=_safe(diff), detail=_safe(detail),
        source=(source or context.current_source())[:20], ip=context.ip()))


def diff_of(before: dict | None, after: dict | None, keys=None) -> dict:
    """{字段: [旧, 新]}，只含变化的字段；忽略 updated_at / created_at / *__label。"""
    before, after = before or {}, after or {}
    ks = keys if keys is not None else set(before) | set(after)
    out = {}
    for k in ks:
        if k in ('updated_at', 'created_at', 'id') or k.endswith('__label'):
            continue
        a, b = before.get(k), after.get(k)
        if db.jsonable(a) != db.jsonable(b) and not (a in (None, '') and b in (None, '')):
            out[k] = [db.jsonable(a), db.jsonable(b)]
    return out


def auto_enabled() -> bool:
    """自动审计只在"有人在操作"时开：HTTP 请求、智能体运行、AI 动作、导入。后台采集 / 定时任务不逐条记。"""
    ctx = context.get()
    return bool(ctx.get('actor')) and ctx.get('source') != 'collector'


def record(conn, op: str, entity: str, rid, before: dict | None, after: dict | None):
    """db.insert / update / delete 调用：自动审计。"""
    if entity in SKIP_ENTITIES or not auto_enabled():
        return
    if op == 'update' and context.get().get('audit_skip') == (entity, rid):
        return
    if op == 'update':
        d = diff_of(before, {**(before or {}), **(after or {})}, keys=set(after or {}))
        if not d:
            return
    else:
        d = diff_of(None, after) if op == 'create' else diff_of(before, None)
    audit(conn, op, entity, rid, diff=d)


# ───────────────────────── 哈希链 ─────────────────────────
def _canon(r: dict, seq: int) -> str:
    keys = ('time', 'user', 'kind', 'target', 'entity', 'entity_id', 'action', 'diff', 'detail', 'source', 'ip')
    body = {k: (db.jsonable(r.get(k)) if k == 'time' else r.get(k)) for k in keys}
    body['seq'] = seq
    return json.dumps(body, ensure_ascii=False, sort_keys=True, default=str, separators=(',', ':'))


def _hash(prev: str, r: dict, seq: int) -> str:
    return hashlib.sha256(((prev or '') + _canon(r, seq)).encode('utf-8')).hexdigest()


def seal(engine=None, batch=5000) -> int:
    """给还没封存的行按 id 顺序编号并接上哈希链。返回封存的行数。"""
    eng = engine or db.engine
    n = 0
    with _seal_lock:
        while True:
            with eng.begin() as c:
                from .compat import advisory_lock
                advisory_lock(c, 'honeymes.audit.seal')
                last = c.execute(sa.select(audit_t.c.seq, audit_t.c.hash).where(audit_t.c.seq.is_not(None)).order_by(audit_t.c.seq.desc()).limit(1)).first()
                seq, prev = (last[0], last[1]) if last else (0, '')
                rows = [dict(r) for r in c.execute(sa.select(audit_t).where(audit_t.c.seq.is_(None)).order_by(audit_t.c.id).limit(batch)).mappings()]
                for r in rows:
                    seq += 1
                    h = _hash(prev, r, seq)
                    c.execute(audit_t.update().where(audit_t.c.id == r['id']).values(seq=seq, prev_hash=prev, hash=h))
                    prev = h
                n += len(rows)
            if len(rows) < batch:
                return n


def verify(engine=None, batch=5000, seal_first=True) -> dict:
    """从头核对哈希链。返回 {ok, checked, unsealed, problem: {seq, id, reason} | None}。"""
    eng = engine or db.engine
    if seal_first:
        seal(eng)
    checked, prev, expect = 0, '', 1
    with eng.connect() as c:
        unsealed = c.execute(sa.select(sa.func.count()).select_from(audit_t).where(audit_t.c.seq.is_(None))).scalar()
        while True:
            rows = [dict(r) for r in c.execute(sa.select(audit_t).where(audit_t.c.seq >= expect).order_by(audit_t.c.seq).limit(batch)).mappings()]
            for r in rows:
                if r['seq'] != expect:
                    return {'ok': False, 'checked': checked, 'unsealed': unsealed,
                            'problem': {'seq': expect, 'id': None, 'reason': _tr('缺第 {expect} 条（下一条是第 {seq} 条）：有记录被删除', expect=expect, seq=r["seq"])}}
                if (r['prev_hash'] or '') != prev:
                    return {'ok': False, 'checked': checked, 'unsealed': unsealed,
                            'problem': {'seq': r['seq'], 'id': r['id'], 'reason': _tr('前序哈希对不上：前面的记录被改过或删过')}}
                if _hash(prev, r, r['seq']) != r['hash']:
                    return {'ok': False, 'checked': checked, 'unsealed': unsealed,
                            'problem': {'seq': r['seq'], 'id': r['id'], 'reason': _tr('内容哈希不符：这条记录封存后被改过')}}
                prev, expect = r['hash'], expect + 1
                checked += 1
            if len(rows) < batch:
                break
    return {'ok': True, 'checked': checked, 'unsealed': unsealed, 'problem': None}


# ───────────────────────── 查询 ─────────────────────────
def query(f: dict, limit=200, offset=0):
    """筛选：user / entity / entity_id / action / source / q（目标或详情关键字）/ start / end。返回 (total, rows)。"""
    seal()
    conds = []
    t = audit_t
    if f.get('user'):
        conds.append(t.c.user == f['user'])
    if f.get('entity'):
        conds.append(t.c.entity == f['entity'])
    if f.get('entity_id'):
        conds.append(t.c.entity_id == int(f['entity_id']))
    if f.get('action'):
        conds.append(sa.or_(t.c.action == f['action'], t.c.kind == f['action']))
    if f.get('source'):
        conds.append(t.c.source == f['source'])
    if f.get('start'):
        conds.append(t.c.time >= db.parse_dt(f['start']))
    if f.get('end'):
        conds.append(t.c.time < db.parse_dt(f['end']))
    if f.get('q'):
        kw = f"%{str(f['q']).lower()}%"
        conds.append(sa.or_(sa.func.lower(t.c.target).like(kw), sa.func.lower(t.c.kind).like(kw), sa.func.lower(t.c.user).like(kw)))
    with db.engine.connect() as c:
        total = c.execute(sa.select(sa.func.count()).select_from(t).where(*conds)).scalar()
        rows = [dict(r) for r in c.execute(sa.select(t).where(*conds).order_by(t.c.id.desc()).limit(limit).offset(offset)).mappings()]
    return total, rows
