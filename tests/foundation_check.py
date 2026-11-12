"""5.0 数据底座检查（自己在进程里起服务，不需要另外启动）：
  迁移（空库 / 线上库副本 / 漂移检查）、权限与数据范围、审计（自动记录 + 哈希链 + 只追加 + 篡改检测）、API 密钥、
  设备时序（写入 / 去重 / 查询 / 1 分钟与 1 小时汇总 / 开关量时长与跳变 / 迟到数据 / 保留）、断网续传（模拟数据库中断 + 重放去重）、
  备份 → 恢复 → 核对、并发取单号（PostgreSQL 多进程）、问数 SQL 两种方言、计划达成率、采集吞吐。

用法：
  python tests/foundation_check.py                         SQLite（临时目录）
  python tests/foundation_check.py --db pg                 PostgreSQL + TimescaleDB（环境变量 FC_PG=postgresql+psycopg://用户:密码@主机:端口，用户要能建库）
  python tests/foundation_check.py --db pgplain            PostgreSQL 不用 TimescaleDB（原生分区）
  python tests/foundation_check.py --live 线上库副本.db      另外检查：线上 4.7 库副本的升级（只读复制一份再升级，不动原文件）
"""
import argparse
import asyncio
import json
import multiprocessing
import os
import random
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R: list = []


def check(name, ok, detail=''):
    R.append(bool(ok))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  （{detail}）' if detail else ''), flush=True)
    return ok


def section(t):
    print(f'\n===== {t}', flush=True)


# ───────────────────────── 多进程取单号（PostgreSQL） ─────────────────────────
def _code_worker(args):
    url, data, n, threads = args
    os.environ['HM_DATA'] = data
    sys.path.insert(0, ROOT)
    from honeymom import db
    db.init(url, migrate_db=False)
    out, errs = [], []

    def run():
        for _ in range(n):
            try:
                with db.engine.begin() as c:
                    code = db.next_code(c, 'ncr', 'NCR{d}{n:03}')
                    time.sleep(random.random() * 0.004)            # 拿到号以后、提交以前停一下，放大并发窗口
                    db.insert(c, 'ncr', {'code': code, 'qty': 1, 'source': 'manual', 'status': 'open', 'disposition': 'pending'}, raw=True)
                out.append(code)
            except Exception as ex:
                errs.append(str(ex)[:200])
    ts_ = [threading.Thread(target=run) for _ in range(threads)]
    [t.start() for t in ts_]
    [t.join() for t in ts_]
    return out, errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='sqlite', choices=['sqlite', 'pg', 'pgplain'])
    ap.add_argument('--live', default=None, help='线上 SQLite 库的副本（会再复制一份来升级）')
    ap.add_argument('--dir', default=None)
    ap.add_argument('--keep', action='store_true')
    a = ap.parse_args()
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding='utf-8')
    work = a.dir or tempfile.mkdtemp(prefix='hm_fc_')
    os.makedirs(work, exist_ok=True)
    data = os.path.join(work, 'data')
    shutil.rmtree(data, ignore_errors=True)
    os.makedirs(data)
    os.environ['HM_DATA'] = data
    os.environ['MES_SQLITE_TIMEOUT'] = '2'
    os.environ['MES_BACKUP_DIR'] = os.path.join(work, 'backups')
    if a.db == 'pgplain':
        os.environ['MES_TS_TIMESCALE'] = '0'
    sys.path.insert(0, ROOT)
    import sqlalchemy as sa
    pg_base = (os.environ.get('FC_PG') or '').rstrip('/')
    dbname = None
    adm = None
    if a.db == 'sqlite':
        url = 'sqlite:///' + os.path.join(data, 'mes.db').replace('\\', '/')
    else:
        if not pg_base:
            print('需要环境变量 FC_PG=postgresql+psycopg://用户:密码@主机:端口')
            sys.exit(2)
        dbname = f'fc_{a.db}_{os.getpid()}'
        adm = sa.create_engine((os.environ.get('FC_PG_ADMIN') or pg_base).rstrip('/') + '/postgres', isolation_level='AUTOCOMMIT')
        with adm.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}"'))
            c.execute(sa.text(f'CREATE DATABASE "{dbname}" OWNER "{sa.engine.make_url(pg_base + "/x").username}"'))
        url = f'{pg_base}/{dbname}'
    print(f'数据库：{a.db}  目录：{work}', flush=True)
    t_all = time.time()

    from honeymom import db, migrations, schema
    # ───────── A 迁移 ─────────
    section('A 迁移')
    db.init(url, migrate_db=False)
    dry = migrations.upgrade(db.engine, dry_run=True)
    check('空库演练：只列计划不执行', all(r['status'] == 'planned' for r in dry) and sum(len(r['actions']) for r in dry) >= 90 and not migrations.applied(db.engine),
          f"{sum(len(r['actions']) for r in dry)} 个动作")
    t0 = time.time()
    rep = migrations.upgrade(db.engine)
    check('空库迁移全部执行', [r['status'] for r in rep] == ['done'] * len(rep), f"{len(rep)} 个迁移，{time.time() - t0:.1f} 秒")
    check('迁移后结构与 schema.py 一致', migrations.check(db.engine) == [])
    check('再执行一次没有动作（幂等）', all(r['status'] == 'applied' for r in migrations.upgrade(db.engine)))
    probe = sa.Table('fc_probe', schema.metadata, sa.Column('id', sa.Integer, primary_key=True))
    p = migrations.check(db.engine)
    schema.metadata.remove(probe)
    check('漂移检查能发现 schema.py 里多出来、没写迁移的表', any('fc_probe' in x for x in p), '; '.join(p))
    with db.engine.connect() as c:
        ts_mode = db.setting(c, 'ts_mode')
    check('时序存储模式', ts_mode == {'sqlite': 'sqlite', 'pg': 'timescale', 'pgplain': 'pgpart'}[a.db], ts_mode)

    if a.live and a.db == 'sqlite':
        live = os.path.join(work, 'live', 'mes.db')
        os.makedirs(os.path.dirname(live), exist_ok=True)
        src = sqlite3.connect(f'file:{a.live}?mode=ro', uri=True)
        dst = sqlite3.connect(live)
        src.backup(dst)
        src.close(), dst.close()
        leng = sa.create_engine('sqlite:///' + live.replace('\\', '/'))
        keyt = ('work_order', 'part', 'part_event', 'ncr', 'stock_txn', 'report', 'audit_log', 'equipment', 'sys_user')
        with leng.connect() as c:
            before = {t: c.execute(sa.text(f'SELECT count(*) FROM "{t}"')).scalar() for t in keyt}
        d = migrations.upgrade(leng, dry_run=True)
        check('线上库副本：演练识别为 4.7 基线（v0001 无动作）', d[0]['status'] == 'planned' and not d[0]['actions'], f"v0002 {len(d[1]['actions'])} 个动作")
        t0 = time.time()
        migrations.upgrade(leng)
        check('线上库副本：升级完成且结构一致', migrations.check(leng) == [], f'{time.time() - t0:.1f} 秒')
        with leng.connect() as c:
            after = {t: c.execute(sa.text(f'SELECT count(*) FROM "{t}"')).scalar() for t in keyt}
            nroles = c.execute(sa.text('SELECT count(*) FROM sys_role')).scalar()
        check('线上库副本：业务数据行数不变', before == after, json.dumps({k: v for k, v in after.items()}, ensure_ascii=False))
        check('线上库副本：写入了默认角色', nroles == 9)
        from honeymom import audit
        n = audit.seal(leng)
        v = audit.verify(leng)
        check('线上库副本：旧审计记录接上哈希链并核对通过', v['ok'] and v['checked'] >= before['audit_log'], f'封存 {n} 条')
        leng.dispose()

    # 基础数据 + 测试用组织结构
    from honeymom import seed
    seed.run()
    with db.engine.begin() as c:
        c.execute(schema.TABLES['datasource'].update().values(enabled=False))      # 测试不连真实数据源
        plant = db.get(c, 'plant', code='GR')['id']
        w1 = db.insert(c, 'workshop', {'code': 'FC-W1', 'name': '一车间', 'plant': plant}, raw=True)
        w2 = db.insert(c, 'workshop', {'code': 'FC-W2', 'name': '二车间', 'plant': plant}, raw=True)
        l1 = db.insert(c, 'line', {'code': 'FC-L1', 'name': '一线', 'workshop': w1}, raw=True)
        l2 = db.insert(c, 'line', {'code': 'FC-L2', 'name': '二线', 'workshop': w2}, raw=True)
        e1 = db.insert(c, 'equipment', {'code': 'FC-E1', 'name': '一线设备', 'line': l1, 'etype': 'cnc', 'status': 'idle'}, raw=True)
        e2 = db.insert(c, 'equipment', {'code': 'FC-E2', 'name': '二线设备', 'line': l2, 'etype': 'cnc', 'status': 'idle'}, raw=True)
        e3 = db.insert(c, 'equipment', {'code': 'FC-E3', 'name': '未分配设备', 'etype': 'other', 'status': 'idle'}, raw=True)
        for e in (e1, e2, e3):
            db.insert(c, 'alarm', {'equipment': e, 'msg': f'测试报警 {e}', 'start': datetime.now()}, raw=True)
        ncr1 = db.insert(c, 'ncr', {'qty': 1, 'status': 'open', 'disposition': 'pending', 'source': 'manual'}, raw=True)

    # ───────── 起服务 ─────────
    import uvicorn
    from honeymom import apikeys, audit, auth, backup, context, perms, ts
    from honeymom.app import build_app
    from honeymom.collector import COLLECTOR
    from honeymom.ingest import GATE, ING
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    srv = uvicorn.Server(uvicorn.Config(build_app(), host='127.0.0.1', port=port, log_level='warning'))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        time.sleep(0.2)
        if srv.started and COLLECTOR.loop:
            break
    base = f'http://127.0.0.1:{port}'

    def http(method, path, body=None, token=None, key=None):
        h = {'Content-Type': 'application/json'}
        if token:
            h['Authorization'] = 'Bearer ' + token
        if key:
            h['X-API-Key'] = key
        req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw[:1] in (b'{', b'[') else raw)
        except urllib.error.HTTPError as ex:
            raw = ex.read()
            try:
                return ex.code, json.loads(raw)
            except ValueError:
                return ex.code, raw

    def login(u, pw='honey123'):
        return http('POST', '/api/login', {'username': u, 'password': pw})[1]['token']

    def in_loop(fn, *args):
        async def go():
            return fn(*args)
        return asyncio.run_coroutine_threadsafe(go(), COLLECTOR.loop).result(120)

    def wait(cond, sec=60):
        t = time.time()
        while time.time() - t < sec:
            if cond():
                return True
            time.sleep(0.3)
        return False
    admin = login('admin')

    # ───────── B 权限与数据范围 ─────────
    section('B 权限与数据范围')
    WRITE47 = {'admin': '*', 'manager': '*', 'planner': {'生产管理', '主数据'}, 'engineer': {'主数据', '机加', '铸造', '质量管理', 'AI 助手'},
               'leader': {'生产管理', '设备管理'}, 'operator': {'生产管理', '设备管理'}, 'qc': {'质量管理'}, 'maint': {'设备管理'}, 'warehouse': {'仓储物流'}}
    bad = []
    # 5.0 新模块（ERP / 现场执行 / 工厂模型…）给默认角色加的写权限不算和 4.7 不一致：取 perms.DEFAULT_ROLES 里 4.7 之外多出来的模式
    BASE47 = {'admin': ['*'], 'manager': ['*'], 'planner': ['page.*', '*.read', 'production.*', 'master.*'], 'leader': ['page.*', '*.read', 'production.*', 'equipment.*'],
              'operator': ['page.*', '*.read', 'production.*', 'equipment.*'], 'qc': ['page.*', '*.read', 'quality.*'], 'maint': ['page.*', '*.read', 'equipment.*'],
              'warehouse': ['page.*', '*.read', 'warehouse.*'], 'engineer': ['page.*', '*.read', 'master.*', 'machining.*', 'foundry.*', 'quality.*', 'ai.*']}
    EXTRA = {r: [p for p in perms.DEFAULT_ROLES[r][1] if p not in BASE47.get(r, []) and not p.startswith('!')] for r in WRITE47 if r in perms.DEFAULT_ROLES}
    from honeymom.migrations.baseline_47 import BASELINE_47      # 只核对 4.7 已有的实体（5.0 新实体的权限由各模块自己的检查管）
    for role, mods in WRITE47.items():
        u = {'role_list': [role]}
        for e in schema.ENTITIES:
            if e.name not in BASELINE_47:
                continue
            exp = mods == '*' or e.module in mods
            ex = exp or any(perms.grants(EXTRA.get(role, []), perms.entity_perm(e.name, o)) for o in ('create', 'update', 'delete'))
            if auth.can_write(u, e.name) not in ({exp, ex}) or not perms.can(u, perms.entity_perm(e.name, 'read')):
                bad.append(f'{role}/{e.name}')
            for act in e.actions:
                if perms.can(u, perms.entity_perm(e.name, act)) not in ({exp, exp or perms.grants(EXTRA.get(role, []), perms.entity_perm(e.name, act))}):
                    bad.append(f'{role}/{e.name}.{act}')
    check('默认角色与 4.7 的读写权限完全一致（9 个角色 × 全部实体 × 增删改 / 动作）', not bad, '、'.join(bad[:8]))
    check('系统管理功能只有管理员（厂长没有）', perms.can({'role_list': ['admin']}, 'system.role.manage') and not perms.can({'role_list': ['manager']}, 'system.role.manage')
          and perms.can({'role_list': ['manager']}, 'system.audit.view') and not perms.can({'role_list': ['qc']}, 'system.audit.view'))
    check('智能体编辑 = 管理员 / 厂长 / 工艺工程师', [r for r in WRITE47 if auth.can_edit_agents({'role_list': [r]})] == ['admin', 'manager', 'engineer'])
    code, r = http('POST', '/api/admin/roles', {'code': 'fc_line_qc', 'name': '一线质检（测试）', 'scope': {'lines': [l1]},
                                                 'perms': ['page.home', 'quality.ncr.read', 'quality.ncr.update', '!quality.ncr.delete', 'equipment.equipment.read',
                                                           'equipment.equipment.update', 'equipment.alarm.read', 'equipment.ts.read']}, admin)
    check('管理员新建角色（带数据范围）', code == 200, str(r))
    code, _ = http('POST', '/api/admin/roles', {'code': 'fc_x', 'name': 'x'}, login('qc'))
    check('非管理员不能管角色', code == 403)
    with db.engine.begin() as c:
        db.insert(c, 'sys_user', {'username': 'fc_l1', 'name': '测试一线质检', 'roles': 'fc_line_qc', 'active': True, 'password_hash': auth.hash_pw('fc123456')}, raw=True)
    tk = login('fc_l1', 'fc123456')
    me = http('GET', '/api/me', token=tk)[1]
    check('/api/me 返回权限模式和数据范围', me.get('scope') == {'plants': [], 'workshops': [], 'lines': [l1]} and me['perms'], me.get('scope_label'))
    code, r = http('GET', '/api/e/equipment?size=500', token=tk)
    ids = {x['id'] for x in r.get('items', [])} if code == 200 else set()
    check('列表按数据范围过滤（本线 + 未分配的可见，二线不可见）', e1 in ids and e3 in ids and e2 not in ids and all(x['line'] in (None, l1) for x in r['items']), f'{len(ids)} 台')
    check('范围外的单条读 404', http('GET', f'/api/e/equipment/{e2}', token=tk)[0] == 404)
    check('范围内可改', http('PUT', f'/api/e/equipment/{e1}', {'model': 'FC-M1'}, tk)[0] == 200)
    check('范围外不能改', http('PUT', f'/api/e/equipment/{e2}', {'model': 'x'}, tk)[0] == 403)
    check('不能把记录改到范围外', http('PUT', f'/api/e/equipment/{e1}', {'line': l2}, tk)[0] == 403)
    code, r = http('GET', '/api/e/alarm?size=100', token=tk)
    check('关联表（报警 → 设备 → 产线）也按范围过滤', code == 200 and {x['equipment'] for x in r['items']} <= {e1, e3} and r['items'])
    check('没有的实体读权限 → 403', http('GET', '/api/e/work_order', token=tk)[0] == 403)
    check('有读没新增 → 新增 403', http('POST', '/api/e/ncr', {'qty': 1}, tk)[0] == 403)
    check('排除的权限点（!quality.ncr.delete）→ 删除 403', http('DELETE', f'/api/e/ncr/{ncr1}', token=tk)[0] == 403)
    check('没有动作权限 → 实体动作 403', http('POST', f'/api/e/ncr/{ncr1}/do/close', {}, tk)[0] == 403)
    check('有修改权限 → 改不合格品 200', http('PUT', f'/api/e/ncr/{ncr1}', {'root_cause': '测试原因'}, tk)[0] == 200)
    check('页面接口按页面权限：工作台可看', http('GET', '/api/home/cockpit', token=tk)[0] == 200)
    check('页面接口按页面权限：没有 SPC 页面 → 403', http('GET', '/api/quality/spc', token=tk)[0] == 403)
    from honeymom.services import ai as ai_svc
    u = auth.user_by_name('fc_l1')
    res = ai_svc.run_sql('SELECT id, line FROM equipment', user=u)
    check('问数 SQL 按提问人的数据范围（同名 CTE 替换）', {row[0] for row in res['rows']} == ids, f"{len(res['rows'])} 行")
    res = ai_svc.run_sql('SELECT a.id FROM alarm a JOIN equipment e ON e.id = a.equipment', user=u)
    n_al = http('GET', '/api/e/alarm?size=1000', token=tk)[1]['total']
    check('问数 SQL：关联查询也在范围内', len(res['rows']) == n_al and n_al >= 2, f'{n_al} 条')
    for sql, why in (('SELECT * FROM work_order', '没有表权限'), ('SELECT * FROM ' + ('main' if a.db == 'sqlite' else 'public') + '.equipment', '带库名绕过'),
                     ('SELECT * FROM sys_user', '隐藏表'), ('DELETE FROM ncr', '写语句')):
        try:
            ai_svc.run_sql(sql, user=u)
            check(f'问数 SQL 拒绝：{why}', False)
        except ValueError as ex:
            check(f'问数 SQL 拒绝：{why}', True, str(ex))
    with context.acting(u, 'ai'):
        res = ai_svc.run_sql('SELECT count(*) FROM equipment')
    check('问数 SQL 不传 user 时取当前执行人（智能体 / 右侧 AI 面板）', res['rows'][0][0] == len(ids))

    # ───────── C 审计 ─────────
    section('C 审计')
    code, r = http('POST', '/api/e/ncr', {'qty': 3, 'sn': 'FC-SN-1', 'source': 'manual'}, admin)
    nid = r.get('id')
    http('PUT', f'/api/e/ncr/{nid}', {'qty': 5, 'root_cause': '浇注温度低'}, admin)
    http('POST', f'/api/e/ncr/{nid}/do/close', {}, admin)
    with db.engine.begin() as c:
        mid = db.insert(c, 'material', {'code': 'FC-DEL', 'name': '待删物料', 'mtype': 'aux'}, raw=True)
    http('DELETE', f'/api/e/material/{mid}', token=admin)
    audit.seal()
    tot, rows = audit.query({'entity': 'ncr', 'entity_id': nid}, limit=50)
    acts = {x['action']: x for x in rows}
    check('新增自动审计（字段改后值）', 'create' in acts and acts['create']['diff'].get('qty') == [None, 3], json.dumps(acts.get('create', {}).get('diff'), ensure_ascii=False)[:120])
    check('修改自动审计（改前 → 改后）', 'update' in acts and acts['update']['diff'].get('qty') == [3, 5] and 'root_cause' in acts['update']['diff'])
    check('实体动作审计（动作名 + 状态变化）', 'close' in acts and acts['close']['diff'].get('status') == ['open', 'closed'])
    check('审计记录用户 / 来源 / IP', acts['update']['user'] == 'admin' and acts['update']['source'] == 'ui' and acts['update']['ip'] == '127.0.0.1')
    tot, rows = audit.query({'entity': 'material', 'entity_id': mid, 'action': 'delete'})
    check('删除自动审计（改前内容）', tot == 1 and rows[0]['diff'].get('code') == ['FC-DEL', None])
    tot, rows = audit.query({'entity': 'equipment', 'entity_id': e1, 'action': 'update'})
    check('范围用户的修改也有审计', tot >= 1 and rows[0]['user'] == 'fc_l1' and rows[0]['diff'].get('model') == [None, 'FC-M1'])
    check('登录记审计', audit.query({'action': 'login', 'user': 'fc_l1'})[0] >= 1 and audit.query({'action': 'login_fail'})[0] >= 0)
    fcu = auth.user_by_name('fc_l1')['id']
    http('POST', f'/api/e/sys_user/{fcu}/do/reset_pw', {'password': 'fc123456'}, admin)
    audit.seal()
    t_, rows = audit.query({'entity': 'sys_user', 'entity_id': fcu, 'action': 'reset_pw'})
    check('密码字段打码', t_ == 1 and rows[0]['diff'].get('password_hash') == ['***', '***'], json.dumps(rows[0]['diff'] if rows else None))
    v = audit.verify()
    check('哈希链核对通过', v['ok'] and v['checked'] > 5 and v['unsealed'] == 0, f"{v['checked']} 条")
    for sql, what in (("UPDATE audit_log SET \"user\" = 'x' WHERE seq = 1", '改'), ('DELETE FROM audit_log WHERE seq = 1', '删')):
        try:
            with db.engine.begin() as c:
                c.execute(sa.text(sql))
            check(f'审计只追加：不能{what}已封存的记录', False)
        except Exception as ex:
            check(f'审计只追加：不能{what}已封存的记录', 'audit_log' in str(ex), str(ex).splitlines()[0][:80])
    code, r = http('GET', '/api/admin/audit?entity=ncr&size=5', token=admin)
    check('审计查看接口（筛选）', code == 200 and r['total'] >= 3)
    code, r = http('GET', '/api/admin/audit.csv?entity=ncr&token=' + admin)
    check('审计导出 CSV', code == 200 and '改动' in (r.decode('utf-8-sig') if isinstance(r, bytes) else str(r)))
    check('非管理员看不了审计', http('GET', '/api/admin/audit', token=login('qc'))[0] == 403)

    # ───────── D API 密钥 ─────────
    section('D API 密钥')
    code, r = http('POST', '/api/admin/apikeys', {'name': 'fc-gw', 'perms': ['equipment.ts.read', 'equipment.ts.write'], 'remark': '测试网关'}, admin)
    key = r.get('key', '')
    check('新建密钥，明文只返回一次', code == 200 and key.startswith('hmk_'))
    with db.engine.connect() as c:
        row = dict(c.execute(sa.select(schema.api_key_t).where(schema.api_key_t.c.id == r['id'])).mappings().first())
    check('库里只存哈希', key not in json.dumps(row, default=str) and len(row['key_hash']) == 64)
    code, w = http('GET', '/api/whoami', key=key)
    check('用密钥调用（X-API-Key）', code == 200 and w['is_api_key'] and w['username'] == 'apikey:fc-gw')
    req = urllib.request.Request(base + '/api/whoami', headers={'Authorization': 'Bearer ' + key})
    with urllib.request.urlopen(req) as resp:
        check('也接受 Authorization: Bearer hmk_…', resp.status == 200)
    check('密钥权限之外的实体 → 403', http('GET', '/api/e/work_order', key=key)[0] == 403)
    check('密钥不能调老的页面接口（没登记页面权限）', http('GET', '/api/home/cockpit', key=key)[0] == 403)
    code, r2 = http('POST', '/api/admin/apikeys', {'name': 'fc-scope', 'perms': ['*.read'], 'scope': {'lines': [l2]}}, admin)
    k2 = r2['key']
    code, r = http('GET', '/api/e/equipment?size=500', key=k2)
    check('密钥的数据范围', code == 200 and e2 in {x['id'] for x in r['items']} and e1 not in {x['id'] for x in r['items']})
    code, r3 = http('POST', '/api/admin/apikeys', {'name': 'fc-crud', 'perms': ['quality.ncr.create', 'quality.ncr.read']}, admin)
    code, r = http('POST', '/api/e/ncr', {'qty': 2, 'source': 'manual'}, key=r3['key'])
    audit.seal()
    t, rows = audit.query({'entity': 'ncr', 'entity_id': r.get('id'), 'action': 'create'})
    check('密钥写入的审计：用户 apikey:名称、来源"接口"', code == 200 and t == 1 and rows[0]['user'] == 'apikey:fc-crud' and rows[0]['source'] == 'api')
    code, r4 = http('POST', '/api/admin/apikeys', {'name': 'fc-exp', 'perms': ['*.read'], 'expires_at': (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')}, admin)
    check('过期的密钥 → 401', http('GET', '/api/whoami', key=r4['key'])[0] == 401)
    code, r5 = http('POST', '/api/admin/apikeys', {'name': 'fc-ip', 'perms': ['*.read'], 'ip_allow': '10.9.9.9'}, admin)
    check('IP 白名单之外 → 403', http('GET', '/api/whoami', key=r5['key'])[0] == 403)
    http('PUT', f"/api/admin/apikeys/{r5['id']}", {'ip_allow': '127.0.0.0/8, 10.0.0.0/8'}, admin)
    check('IP 白名单（网段）之内 → 200', http('GET', '/api/whoami', key=r5['key'])[0] == 200)
    check('IP 白名单格式校验', http('PUT', f"/api/admin/apikeys/{r5['id']}", {'ip_allow': 'abc'}, admin)[0] == 400)
    http('POST', f"/api/admin/apikeys/{r5['id']}/revoke", {}, admin)
    check('吊销后立即 401', http('GET', '/api/whoami', key=r5['key'])[0] == 401)
    check('错的密钥 → 401', http('GET', '/api/whoami', key=key[:-3] + 'xyz')[0] == 401)
    time.sleep(0.5)
    keys = http('GET', '/api/admin/apikeys', token=admin)[1]['items']
    gw = next(x for x in keys if x['name'] == 'fc-gw')
    check('记录最后使用时间 / IP / 次数', gw['last_used_at'] and gw['last_used_ip'] == '127.0.0.1' and gw['use_count'] >= 1)

    # ───────── E 设备时序 ─────────
    section('E 设备时序')
    now = datetime.now().replace(second=0, microsecond=0)
    t0 = now.replace(minute=0) - timedelta(hours=2)
    num, bol, st = 'FC:M1/Spindle/Load', 'FC:M1/Cyl/Extended', 'FC:M1/Common/State'
    rows = [(num, t0 + timedelta(milliseconds=250 * i), float(i % 100)) for i in range(720)]           # 3 分钟，每 250 ms
    brows, state, t = [], False, t0
    while t < t0 + timedelta(minutes=3):                                                               # 开 2.0 s / 关 3.0 s 循环
        state = not state
        brows.append((bol, t, state))
        t += timedelta(seconds=2.0 if state else 3.0)
    srows = [(st, t0, 'Idle'), (st, t0 + timedelta(seconds=30), 'Executing'), (st, t0 + timedelta(seconds=150), 'Stopped')]
    t1 = time.time()
    n = ts.write(rows + brows + srows)
    check('写入原始值（亚秒级、数值 / 开关 / 字符串）', n == len(rows) + len(brows) + len(srows), f'{n} 行 {time.time() - t1:.2f} 秒')
    check('重复写入被去重（补传重放）', ts.write(rows[:100] + brows[:5]) == 0)
    raw = ts.raw(num, t0, t0 + timedelta(minutes=3))
    check('原始值查询（按时间升序、毫秒精度）', len(raw) == 720 and abs((datetime.fromisoformat(raw[1]['ts']) - datetime.fromisoformat(raw[0]['ts'])).total_seconds() - 0.25) < 1e-6,
          f"{raw[0]['ts']} / {raw[1]['ts']}")
    lt = ts.latest([num, st])
    check('最新值', lt[num]['value'] == 19.0 and lt[st]['text'] == 'Stopped', json.dumps(lt, ensure_ascii=False)[:120])
    if ts.mode() == 'timescale':
        ts.refresh(t0 - timedelta(hours=1), t0 + timedelta(hours=2))
    else:
        ts.rollup_job(until=t0 + timedelta(minutes=3))
    m = ts.rollup(num, t0, t0 + timedelta(minutes=3), '1m')
    exp = [rows[i * 240:(i + 1) * 240] for i in range(3)]
    ok = len(m) == 3 and all(m[i]['n'] == 240 and m[i]['min'] == min(x[2] for x in exp[i]) and m[i]['max'] == max(x[2] for x in exp[i])
                             and abs(m[i]['avg'] - sum(x[2] for x in exp[i]) / 240) < 1e-9 and m[i]['first'] == exp[i][0][2] and m[i]['last'] == exp[i][-1][2] for i in range(3))
    check('1 分钟汇总：次数 / 最小 / 最大 / 平均 / 首 / 末', ok, json.dumps(m[:1], ensure_ascii=False))
    h = ts.rollup(num, t0, t0 + timedelta(hours=1), '1h')
    check('1 小时汇总', len(h) == 1 and h[0]['n'] == 720 and h[0]['max'] == 99.0 and abs(h[0]['avg'] - sum(x[2] for x in rows) / 720) < 1e-9 and h[0]['last'] == 19.0)
    # 开关量：期望值
    def exp_on(a_, b_):
        on = 0.0
        for i, (_, ts_, v) in enumerate(brows):
            en = brows[i + 1][1] if i + 1 < len(brows) else b_
            lo, hi = max(ts_, a_), min(en, b_)
            if v and hi > lo:
                on += (hi - lo).total_seconds()
        return on
    bm = ts.rollup(bol, t0, t0 + timedelta(minutes=3), '1m')
    exp_tr = [sum(1 for i, (_, ts_, v) in enumerate(brows) if i > 0 and t0 + timedelta(minutes=k) <= ts_ < t0 + timedelta(minutes=k + 1)) for k in range(3)]
    check('开关量每分钟的接通时长 / 跳变次数', [round(x['on_s'], 3) for x in bm] == [round(exp_on(t0 + timedelta(minutes=k), t0 + timedelta(minutes=k + 1)), 3) for k in range(3)]
          and [x['transitions'] for x in bm] == exp_tr, json.dumps([(x['on_s'], x['transitions']) for x in bm]))
    stt = ts.stats(bol, t0, t0 + timedelta(minutes=3))
    check('开关量统计（总接通时长 / 跳变）', abs(stt['on_s'] - exp_on(t0, t0 + timedelta(minutes=3))) < 1e-6 and stt['transitions'] == len(brows) - 1, f"on {stt['on_s']} s")
    ed = ts.edges(bol, t0, t0 + timedelta(minutes=3), 'rising')
    check('上升沿检测', len(ed) == sum(1 for i, x in enumerate(brows) if i > 0 and x[2]) and all(x['edge'] == 'rising' for x in ed), f'{len(ed)} 个')
    sst = ts.stats(st, t0, t0 + timedelta(minutes=3))
    check('状态量各值累计时长', sst['by_value'] == {'Idle': 30.0, 'Executing': 120.0, 'Stopped': 30.0}, json.dumps(sst['by_value'], ensure_ascii=False))
    ts.write([(num, t0 + timedelta(seconds=30, milliseconds=1), 1000.0)])                               # 迟到的数据
    if ts.mode() == 'timescale':
        ts.refresh(t0 - timedelta(hours=1), t0 + timedelta(hours=2))
    else:
        ts.rollup_job()
    m2 = ts.rollup(num, t0, t0 + timedelta(minutes=1), '1m')
    h2 = ts.rollup(num, t0, t0 + timedelta(hours=1), '1h')
    check('迟到 / 补传的数据会重算已汇总的分钟和小时', m2[0]['n'] == 241 and m2[0]['max'] == 1000.0 and h2[0]['n'] == 721, f"n={m2[0]['n']} max={m2[0]['max']}")
    # 65 days back: PostgreSQL drops whole monthly partitions, so the old value's month must end before the 30-day cut on any calendar day
    old = now - timedelta(days=65)
    ts.write([(num, old, 1.0)])
    if ts.mode() == 'timescale':
        ts.apply_retention()
        with db.engine.connect() as c:
            jobs = c.execute(sa.text("SELECT hypertable_name FROM timescaledb_information.jobs WHERE proc_name = 'policy_retention'")).scalars().all()
        check('保留策略（TimescaleDB 后台作业）', 'ts_raw' in jobs and 'ts_1m' in jobs, ', '.join(jobs))
    else:
        r = ts.retention_job(now)
        check('保留策略：过期原始值被删除', not ts.raw(num, old - timedelta(minutes=1), old + timedelta(minutes=1)), json.dumps(r, ensure_ascii=False))
    if ts.mode() == 'pgpart':
        with db.engine.connect() as c:
            parts = c.execute(sa.text("SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'ts_raw'")).scalars().all()
        check('原生分区：按月自动建分区', f'ts_raw_p{t0:%Y%m}' in parts, ', '.join(parts))
    q = f'point={urllib.request.quote(num)}&start={urllib.request.quote(str(t0))}&end={urllib.request.quote(str(t0 + timedelta(minutes=3)))}'
    code, r = http('GET', '/api/ts/rollup?interval=1m&' + q, token=admin)
    check('REST：/api/ts/rollup', code == 200 and len(r['items']) == 3)
    code, r = http('GET', f'/api/ts/edges?kind=falling&point={urllib.request.quote(bol)}&start={urllib.request.quote(str(t0))}&end={urllib.request.quote(str(t0 + timedelta(minutes=3)))}', key=key)
    check('REST：/api/ts/edges（API 密钥）', code == 200 and r['items'] and all(x['edge'] == 'falling' for x in r['items']))
    code, r = http('POST', '/api/ts/write', {'rows': [['FC:GW/Temp', str(now - timedelta(seconds=5)), 21.5], ['FC:GW/Temp', str(now), 21.7, 0]]}, key=key)
    check('REST：网关推送 /api/ts/write', code == 200 and r['inserted'] == 2)
    code, r = http('POST', '/api/ts/write', {'rows': [['FC:GW/Temp', str(now), 21.7]]}, key=key)
    check('REST：重复推送去重', code == 200 and r['inserted'] == 0 and r['duplicates'] == 1)
    check('读权限的密钥不能推送', http('POST', '/api/ts/write', {'rows': [['x', str(now), 1]]}, key=k2)[0] == 403)

    # ───────── F 断网续传 ─────────
    section('F 断网续传（模拟数据库中断）')
    ptable = schema.TABLES['part_event']

    def n_pe(prefix):
        with db.engine.connect() as c:
            return c.execute(sa.select(sa.func.count()).select_from(ptable).where(ptable.c.sn.like(prefix + '%'))).scalar()
    tz = datetime.now().replace(microsecond=0) - timedelta(minutes=20)
    msgs = [{'sn': f'FCSF{i:05d}', 'event': 'poured', 't': (tz + timedelta(seconds=i)).strftime('%Y-%m-%d %H:%M:%S'), 'station': 'MOLD1', 'type': 'SGH-450', 'attrs': {}}
            for i in range(200)]
    tags = [[None, 'Sim/Value', float(i), tz + timedelta(seconds=i, milliseconds=500), 'FCSF:SIM/Sim/Value'] for i in range(300)]
    in_loop(lambda: [GATE.submit('part', ['FCSF', m]) for m in msgs[:20]])
    check('在线时直接入库', n_pe('FCSF') == 20)
    lock = None
    if a.db == 'sqlite':
        lock = sqlite3.connect(os.path.join(data, 'mes.db'), timeout=0)
        lock.execute('BEGIN EXCLUSIVE')
    else:
        with sa.create_engine(pg_base + '/postgres', isolation_level='AUTOCOMMIT').connect() as c:      # 用应用自己的账号模拟：不准连 + 断开现有连接
            c.execute(sa.text(f'ALTER DATABASE "{dbname}" ALLOW_CONNECTIONS false'))
            c.execute(sa.text('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :d AND pid <> pg_backend_pid() AND usename = current_user'),
                      {'d': dbname})
    t_out = time.time()
    in_loop(lambda: [GATE.submit('part', ['FCSF', m]) for m in msgs[20:]])
    in_loop(lambda: [GATE.submit('tag', x) for x in tags])
    ok = wait(lambda: GATE.buffering and GATE.spool().pending() >= 180, 60)
    in_loop(GATE.flush_ts)
    st_ = GATE.status()
    check('数据库中断时转写本地缓存（按到达顺序）', ok and st_['pending'] >= 181, f"缓存 {st_['pending']} 条，{st_['last_error'][:60]}")
    time.sleep(3)
    if lock:
        lock.rollback()
        lock.close()
    else:
        with sa.create_engine(pg_base + '/postgres', isolation_level='AUTOCOMMIT').connect() as c:
            c.execute(sa.text(f'ALTER DATABASE "{dbname}" ALLOW_CONNECTIONS true'))
    ok = wait(lambda: not GATE.buffering and GATE.spool().pending() == 0, 120)
    check('数据库恢复后自动补传完', ok, f'中断 {time.time() - t_out:.0f} 秒，补传 {GATE.stats["replayed"]} 条')
    check('补传后零件事件一条不少、不重复', n_pe('FCSF') == 200)
    with db.engine.connect() as c:
        order = [r[0] for r in c.execute(sa.select(ptable.c.sn).where(ptable.c.sn.like('FCSF%')).order_by(ptable.c.id)).all()]
    check('补传顺序 = 到达顺序', order == [m['sn'] for m in msgs])
    in_loop(lambda: None)
    ts_ok = wait(lambda: len(ts.raw('FCSF:SIM/Sim/Value', tz, tz + timedelta(minutes=10))) == 300, 20)
    check('中断期间的点位值补进时序库', ts_ok, f"{len(ts.raw('FCSF:SIM/Sim/Value', tz, tz + timedelta(minutes=10)))} 行")
    dup0 = ING.stats['duplicates']
    in_loop(lambda: [GATE.submit('part', ['FCSF', m]) for m in msgs])
    check('同样的消息再来一遍（重放 / MQTT 重发）被去重', n_pe('FCSF') == 200 and ING.stats['duplicates'] - dup0 == 200)
    from honeymom.spool import Spool
    sp = Spool(os.path.join(work, 'spool_unit'))
    for i in range(50):
        sp.append('x', [i])
    got = []
    sp.drain(lambda k, a_: got.append(a_[0]), limit=20)
    sp.close()
    sp2 = Spool(os.path.join(work, 'spool_unit'))                    # 模拟进程重启：从游标接着补
    sp2.drain(lambda k, a_: got.append(a_[0]), limit=1000)
    check('磁盘缓存：分批补传 + 重启后从游标续传，不丢不重', got == list(range(50)) and sp2.pending() == 0)

    # ───────── G 备份与恢复 ─────────
    section('G 备份 → 恢复 → 核对')
    os.makedirs(os.path.join(data, 'kb_files'), exist_ok=True)
    with open(os.path.join(data, 'kb_files', 'fc_drawing.txt'), 'w', encoding='utf-8') as f:
        f.write('测试图纸')
    audit.seal()
    t1 = time.time()
    b = backup.create('manual', 'foundation_check', 'fc')
    check('立即备份（数据库 + 上传文件）', b and b['exists'] and b['manifest']['n_files'] >= 2, f"{b['size'] // 1024} KB，{time.time() - t1:.1f} 秒")
    to = os.path.join(work, 'restored')
    to_db = None
    if a.db != 'sqlite':
        to_db = f'{pg_base}/{dbname}_rs'
        with adm.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}_rs"'))
    rep = backup.restore(b['path'], to, to_db)
    check('恢复到新目录并核对全部通过', rep['ok'], '；'.join(f"{x['check']}:{'√' if x['ok'] else '×'}" for x in rep['checks']))
    check('上传的文件恢复了', open(os.path.join(to, 'kb_files', 'fc_drawing.txt'), encoding='utf-8').read() == '测试图纸')
    try:
        backup.restore(b['path'], to, to_db)
        check('不往非空目录恢复', False)
    except ValueError:
        check('不往非空目录恢复', True)
    code, r = http('POST', f"/api/admin/backups/{b['id']}/drill", {}, admin)
    check('恢复演练（接口）', code == 200 and r.get('ok'), r.get('error', ''))
    with db.engine.begin() as c:
        db.set_setting(c, 'backup_keep', 1)
    b1 = backup.create('scheduled', '', 'fc')
    b2 = backup.create('scheduled', '', 'fc')
    lst = {x['id']: x for x in backup.listing()}
    check('保留策略：定时备份只留最近 N 个', lst[b1['id']]['status'] == 'pruned' and lst[b2['id']]['status'] == 'ok' and not os.path.exists(b1['path']))
    backup.set_schedule({'enabled': True, 'time': '00:00'})
    check('定时备份到点判断（今天已做过 → 不再做；明天到点 → 要做）', backup.due_now() is False and backup.due_now(datetime.now() + timedelta(days=1)) is True)
    if to_db:
        with adm.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}_rs" WITH (FORCE)'))

    # ───────── H 并发取单号 ─────────
    section('H 并发取单号')
    if a.db == 'sqlite':
        out, errs = [], []

        def worker():
            for _ in range(40):
                try:
                    with db.engine.begin() as c:
                        code_ = db.next_code(c, 'ncr', 'NCR{d}{n:03}')
                        time.sleep(random.random() * 0.003)
                        db.insert(c, 'ncr', {'code': code_, 'qty': 1, 'source': 'manual', 'status': 'open', 'disposition': 'pending'}, raw=True)
                    out.append(code_)
                except Exception as ex:
                    errs.append(str(ex)[:120])
        th = [threading.Thread(target=worker) for _ in range(8)]
        [x.start() for x in th]
        [x.join() for x in th]
        check('SQLite 单进程 8 线程 × 40 张不合格品：不撞号', len(out) == 320 and len(set(out)) == 320 and not errs, f"{len(set(out))} 个号，错误 {len(errs)}{(' ' + errs[0]) if errs else ''}")
    else:
        ctx = multiprocessing.get_context('spawn')
        t1 = time.time()
        with ctx.Pool(4) as pool:
            res = pool.map(_code_worker, [(url, data, 30, 4)] * 4)
        out = [x for o, _ in res for x in o]
        errs = [x for _, e in res for x in e]
        check('PostgreSQL 4 进程 × 4 线程 × 30 张不合格品：不撞号（咨询锁）', len(out) == 480 and len(set(out)) == 480 and not errs,
              f"{len(set(out))} 个号，{time.time() - t1:.1f} 秒，错误 {len(errs)}{(' ' + errs[0]) if errs else ''}")
    with db.engine.connect() as c:
        dup = c.execute(sa.text('SELECT code, count(*) FROM ncr GROUP BY code HAVING count(*) > 1')).all()
    check('不合格品单号全表唯一', not dup)

    # ───────── I 问数 SQL 两种方言 ─────────
    section('I 问数 SQL（SQLite 风格 / PostgreSQL 风格 / 可移植写法 都能跑）')
    corpus = [
        "SELECT strftime('%Y-%m-%d', start) AS d, count(*) FROM alarm GROUP BY d",
        "SELECT date(found_at) AS d, count(*) FROM ncr GROUP BY 1",
        "SELECT ifnull(sum(qty), 0) FROM ncr",
        "SELECT group_concat(code) FROM equipment",
        "SELECT group_concat(code, '|') FROM equipment",
        "SELECT count(*) FROM ncr WHERE found_at >= datetime('now', '-7 days') OR found_at IS NULL",
        "SELECT round(avg(qty), 2) FROM ncr",
        "SELECT round(avg(dur_s), 1) FROM alarm",
        "SELECT julianday(max(start)) - julianday(min(start)) FROM alarm",
        "SELECT substr(start, 1, 10) AS d, count(*) FROM alarm GROUP BY 1",
        "SELECT count(*) FROM alarm WHERE start LIKE '2026-%'",
        "SELECT count(*) FROM tag_point WHERE enabled = 1",
        "SELECT count(*) FROM tag_point WHERE enabled = true",
        "SELECT iif(qty > 1, 'many', 'one') AS k, count(*) FROM ncr GROUP BY 1",
        "SELECT instr(code, '-') FROM equipment",
        "SELECT date_trunc('hour', start) AS h, count(*) FROM alarm GROUP BY 1",
        "SELECT string_agg(code, ',') FROM equipment",
        "SELECT greatest(1, 2, 3), least(4, 5)",
        "SELECT to_char(start, 'YYYY-MM-DD') FROM alarm",
        "SELECT hm_day(start) AS d, hm_hour(start) AS h, count(*) FROM alarm GROUP BY 1, 2",
        "SELECT hm_minutes(min(start), max(start)), hm_epoch(max(start)) FROM alarm",
        "SELECT coalesce(e.name, '-') AS n, count(a.id) FROM equipment e LEFT JOIN alarm a ON a.equipment = e.id GROUP BY e.name ORDER BY 2 DESC",
        "SELECT CASE WHEN qty > 2 THEN 'big' ELSE 'small' END AS k, sum(qty) * 1.0 / count(*) FROM ncr GROUP BY 1",
        "WITH x AS (SELECT equipment, count(*) n FROM alarm GROUP BY equipment) SELECT max(n) FROM x",
        "SELECT p.key, r.ts, r.value FROM ts_raw r JOIN ts_point p ON p.id = r.point WHERE p.key = 'FC:M1/Spindle/Load' ORDER BY r.ts DESC LIMIT 3",
        "SELECT bucket, vavg FROM ts_1m WHERE point = (SELECT id FROM ts_point WHERE key = 'FC:M1/Spindle/Load') ORDER BY bucket",
        "SELECT json_extract(evidence, '$.kind') FROM ai_finding LIMIT 1",
        "SELECT count(*) FROM alarm WHERE start >= '2026-01-01 08:00:00' AND start < '2099-01-01'",
    ]
    fails = []
    for sql in corpus:
        try:
            ai_svc.run_sql(sql, user=auth.user_by_name('admin'))
        except Exception as ex:
            fails.append(f'{sql[:60]} → {str(ex).splitlines()[0][:100]}')
    check(f'问数 SQL 语料 {len(corpus)} 条全部能执行（{db.engine.dialect.name}）', not fails, ' | '.join(fails[:3]))
    from honeymom.services.ai import dialect_hint
    check('问数提示带方言说明和可移植写法', ('PostgreSQL' if a.db != 'sqlite' else 'SQLite') in dialect_hint() and 'hm_hour' in dialect_hint())

    # ───────── J 计划达成率 ─────────
    section('J 计划达成率')
    from honeymom.services.home import attainment
    x0, x1, x2, x3 = attainment(50, 0, 0), attainment(1200, 100, 200), attainment(120, 100, 200), attainment(180, 100, 200)
    check('没有计划：不给百分比，说明原因', x0['attain'] is None and x0['plan_state'] == 'none' and x0['plan_note'])
    check('计划远小于实际（1200%）：不给百分比，给计划 / 实际件数', x1['attain'] is None and x1['plan_state'] == 'tiny' and '100' in x1['plan_note'] and '1200' in x1['plan_note'])
    check('正常：120%', x2['attain'] == 1.2 and x2['plan_state'] == 'ok' and not x2['attain_capped'])
    check('超过 150% 封顶显示', x3['attain'] == 1.5 and x3['attain_capped'] and x3['attain_raw'] == 1.8)
    code, r = http('GET', '/api/home/cockpit', token=admin)
    st0 = r['kpi']['stored'] if code == 200 else {}
    check('工作台接口带计划状态', code == 200 and st0.get('plan_state') in ('ok', 'none', 'tiny') and 'actual' in st0, st0.get('plan_state'))

    # ───────── K 采集吞吐 ─────────
    section('K 采集吞吐')
    tb = datetime.now().replace(microsecond=0) - timedelta(minutes=5)
    N = 20000
    vals = [[None, f'Bench/P{i % 200:03d}', float(i), tb + timedelta(milliseconds=10 * i), f'FCB:B{i % 200:03d}/Bench/P{i % 200:03d}'] for i in range(N)]
    t1 = time.time()
    in_loop(lambda: [GATE.submit('tag', x) for x in vals])
    in_loop(GATE.flush_ts)
    dt = time.time() - t1
    tags_ps = N / dt
    pm = [{'sn': f'FCBP{i:05d}', 'event': 'poured', 't': (tb + timedelta(seconds=i)).strftime('%Y-%m-%d %H:%M:%S'), 'station': 'MOLD1', 'type': 'SGH-450', 'attrs': {}} for i in range(500)]
    t1 = time.time()
    in_loop(lambda: [GATE.submit('part', ['FCB', m]) for m in pm])
    parts_ps = 500 / (time.time() - t1)
    bulk = [('FCB:BULK/V', tb + timedelta(milliseconds=i), float(i)) for i in range(50000)]
    t1 = time.time()
    ts.write(bulk)
    bulk_ps = 50000 / (time.time() - t1)
    check('采集吞吐（点位值经闸门 + 时序落库）', tags_ps > 300, f'{tags_ps:,.0f} 值/秒（200 个点位 × 100 次变化）')
    check('采集吞吐（网关零件事件，含一件一码 / 报工 / 去重）', parts_ps > 20, f'{parts_ps:,.0f} 条/秒')
    check('时序批量写入', bulk_ps > 2000, f'{bulk_ps:,.0f} 行/秒')
    print(f'THROUGHPUT {a.db} tags={tags_ps:.0f}/s parts={parts_ps:.0f}/s ts_bulk={bulk_ps:.0f}/s', flush=True)

    # ───────── L 篡改检测（放最后：会绕过触发器改审计表） ─────────
    section('L 审计篡改检测')
    audit.seal()
    with db.engine.connect() as c:
        mid_seq = c.execute(sa.text('SELECT max(seq) / 2 FROM audit_log')).scalar()
        orig = c.execute(sa.text('SELECT "user" FROM audit_log WHERE seq = :s'), {'s': mid_seq}).scalar()
    with db.engine.begin() as c:
        if a.db == 'sqlite':
            c.execute(sa.text('DROP TRIGGER audit_log_no_update'))
            c.execute(sa.text('DROP TRIGGER audit_log_no_delete'))
        else:
            c.execute(sa.text('ALTER TABLE audit_log DISABLE TRIGGER audit_log_guard'))
        c.execute(sa.text('UPDATE audit_log SET "user" = :u WHERE seq = :s'), {'u': 'mallory', 's': mid_seq})
    v = audit.verify()
    check('改了一条已封存的记录 → 核对发现，定位到那一条', not v['ok'] and v['problem']['seq'] == mid_seq, (v['problem'] or {}).get('reason', ''))
    with db.engine.begin() as c:
        c.execute(sa.text('UPDATE audit_log SET "user" = :u WHERE seq = :s'), {'u': orig, 's': mid_seq})
    check('改回原值 → 核对通过', audit.verify()['ok'])
    with db.engine.begin() as c:
        c.execute(sa.text('DELETE FROM audit_log WHERE seq = :s'), {'s': mid_seq + 1})
    v = audit.verify()
    check('删了一条 → 核对发现缺号', not v['ok'] and v['problem']['seq'] == mid_seq + 1 and '删除' in v['problem']['reason'], v['problem']['reason'] if v['problem'] else '')
    code, r = http('GET', '/api/admin/audit/verify', token=admin)
    check('核对接口返回问题', code == 200 and not r['ok'])

    # ───────── 结束 ─────────
    srv.should_exit = True
    time.sleep(1)
    passed = sum(R)
    print(f'\n{passed}/{len(R)} 通过（{a.db}，{time.time() - t_all:.0f} 秒）', flush=True)
    db.engine.dispose()
    if a.db != 'sqlite' and not a.keep:
        try:
            with adm.connect() as c:
                c.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
        except Exception as ex:
            print('清理测试库失败：', ex)
    if not a.keep and not a.dir:
        shutil.rmtree(work, ignore_errors=True)
    os._exit(0 if passed == len(R) else 1)


if __name__ == '__main__':
    main()
