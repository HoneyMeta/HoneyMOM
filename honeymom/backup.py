"""备份与恢复。说明和恢复演练步骤见 docs/dev/foundation.md「备份与恢复」。

备份 = 一个 zip（默认放 <数据目录>/backups，环境变量 MOM_BACKUP_DIR（旧名 MES_BACKUP_DIR）可改）：
  manifest.json      版本、方言、迁移版本、各表行数、文件清单（SHA-256）、说明
  db/mes.db          SQLite：在线备份 API（不停机、一致快照）
  db/mes.pgdump      PostgreSQL：pg_dump -Fc（含 TimescaleDB 超表）
  files/…            上传的文件（知识库/图纸 kb_files）、secret.key、首页偏好、尚未补传的采集缓存（spool）
定时：系统参数 backup_time（默认 02:30）每天一次，backup_keep（默认 14）个定时备份；手动备份保留 backup_keep_manual_days（默认 90）天。
恢复：只恢复到"新的"数据目录（和新的 PostgreSQL 库），不覆盖正在用的数据；恢复后自动核对（完整性、行数、文件哈希、审计哈希链、迁移版本），
核对报告写在新目录的 restore_report.json。确认无误后把服务的 MOM_DATA / MOM_DB（旧名 HM_DATA / MES_DB）指过去再启动。
"""
from .env import get as env_get
from .i18n import _tr
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta
from urllib.parse import unquote, urlparse

import sqlalchemy as sa

from . import db

log = logging.getLogger('mes.backup')
EXCLUDE_DIRS = {'backups', 'ui_shots', '__pycache__'}
EXCLUDE_FILES = ('mes.db', 'mes.db-wal', 'mes.db-shm', 'mes.db-journal')
_lock = threading.Lock()


def backup_dir() -> str:
    d = env_get('MOM_BACKUP_DIR') or os.path.join(db.DATA, 'backups')
    os.makedirs(d, exist_ok=True)
    return d


def _sha(path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _data_files(data_dir: str) -> list[str]:
    out = []
    for root, dirs, files in os.walk(data_dir):
        rel = os.path.relpath(root, data_dir)
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith('drill-') and not (rel == '.' and d.startswith('restore'))]
        for f in files:
            if rel == '.' and (f in EXCLUDE_FILES or f.endswith('.log')):
                continue
            out.append(os.path.normpath(os.path.join(rel, f)) if rel != '.' else f)
    return sorted(out)


def _counts(engine_or_conn) -> dict:
    from . import schema
    out = {}

    def go(c):
        names = set(sa.inspect(c).get_table_names()) | set(sa.inspect(c).get_view_names())
        if c.dialect.name == 'postgresql':
            names |= set(c.execute(sa.text("SELECT matviewname FROM pg_matviews WHERE schemaname = current_schema()")).scalars())
        for t in schema.metadata.sorted_tables:
            if t.name in names:
                out[t.name] = c.execute(sa.select(sa.func.count()).select_from(t)).scalar()
    if isinstance(engine_or_conn, sa.engine.Connection):
        go(engine_or_conn)
    else:
        with engine_or_conn.connect() as c:
            go(c)
    return out


def _pg_args(url: str):
    u = urlparse(url.replace('postgresql+psycopg', 'postgresql'))
    env = dict(os.environ)
    if u.password:
        env['PGPASSWORD'] = unquote(u.password)
    args = ['-h', u.hostname or '127.0.0.1', '-p', str(u.port or 5432), '-U', unquote(u.username or 'postgres')]
    return args, (u.path or '/').lstrip('/'), env


def pg_bin(name: str) -> str:
    for d in filter(None, [env_get('MOM_PG_BIN'), r'C:\Program Files\PostgreSQL\17\bin', '/usr/bin', '/usr/lib/postgresql/17/bin']):
        p = os.path.join(d, name + ('.exe' if os.name == 'nt' else ''))
        if os.path.exists(p):
            return p
    return name


# ───────────────────────── 备份 ─────────────────────────
def create(kind='manual', note='', user='system') -> dict:
    """立即备份，返回 backup_log 记录。kind = manual / scheduled。"""
    from . import audit, migrations
    from .schema import backup_t
    with _lock:
        t0 = time.time()
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        name = f'mes-{stamp}-{kind}.zip'
        dst = os.path.join(backup_dir(), name)
        tmp = tempfile.mkdtemp(prefix='hm_bk_', dir=backup_dir())
        dialect = db.engine.dialect.name
        try:
            files = []
            if dialect == 'sqlite':
                dbf = os.path.join(tmp, 'mes.db')
                src = db.engine.raw_connection()
                try:
                    out = sqlite3.connect(dbf)
                    src.driver_connection.backup(out)            # 在线备份 API：一次拷完（WAL 下不挡写入）
                    out.close()
                finally:
                    src.close()
                eng2 = sa.create_engine('sqlite:///' + dbf.replace('\\', '/'))
                counts = _counts(eng2)
                eng2.dispose()
                db_entry = ('db/mes.db', dbf)
            else:
                args, dbname, env = _pg_args(str(db.engine.url.render_as_string(hide_password=False)))
                dbf = os.path.join(tmp, 'mes.pgdump')
                # 同一个快照：先在可重复读事务里导出快照、数行数，pg_dump 用这个快照导出 —— 行数和备份内容严格对应
                with db.engine.connect().execution_options(isolation_level='REPEATABLE READ') as sc:
                    with sc.begin():
                        snap = sc.execute(sa.text('SELECT pg_export_snapshot()')).scalar()
                        counts = _counts(sc)
                        r = subprocess.run([pg_bin('pg_dump'), *args, '-d', dbname, '-Fc', '--no-owner', f'--snapshot={snap}', '-f', dbf], env=env,
                                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=3600)
                if r.returncode != 0:
                    raise RuntimeError(_tr('pg_dump 失败：') + (r.stderr or '')[-800:])
                db_entry = ('db/mes.pgdump', dbf)
            for rel in _data_files(db.DATA):
                p = os.path.join(db.DATA, rel)
                files.append({'path': rel.replace('\\', '/'), 'size': os.path.getsize(p), 'sha256': _sha(p)})
            st = migrations.status(db.engine)
            manifest = {'app': 'honeymom', 'format': 1, 'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'kind': kind, 'note': note,
                        'dialect': dialect, 'ts_mode': _setting('ts_mode'), 'schema_head': st['head'], 'pending': st['pending'],
                        'db_file': db_entry[0], 'db_sha256': _sha(db_entry[1]), 'db_size': os.path.getsize(db_entry[1]),
                        'counts': counts, 'files': files, 'data_dir': db.DATA, 'by': user}
            with zipfile.ZipFile(dst + '.part', 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as z:
                z.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=1))
                z.write(db_entry[1], db_entry[0], compress_type=zipfile.ZIP_STORED if dialect != 'sqlite' else zipfile.ZIP_DEFLATED)
                for f in files:
                    z.write(os.path.join(db.DATA, f['path']), 'files/' + f['path'])
            os.replace(dst + '.part', dst)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        ms = int((time.time() - t0) * 1000)
        with db.engine.begin() as c:
            rid = c.execute(backup_t.insert().values(file=name, kind=kind, dialect=dialect, size=os.path.getsize(dst), status='ok',
                                                     manifest={k: v for k, v in manifest.items() if k not in ('files',)} | {'n_files': len(files)},
                                                     note=note[:300], created_by=user, created_at=db.clock_wall(), ms=ms)).inserted_primary_key[0]
            audit.audit(c, 'backup', None, None, detail={'file': name, 'kind': kind, 'size': os.path.getsize(dst), 'ms': ms}, target=f'backup:{rid}', user=user)
        log.info(_tr('备份完成 %s（%d MB，%d ms）'), name, os.path.getsize(dst) // (1 << 20), ms)
        prune()
        return get(rid)


def _setting(key, default=None):
    with db.engine.connect() as c:
        return db.setting(c, key, default)


def get(rid) -> dict | None:
    from .schema import backup_t
    with db.engine.connect() as c:
        r = c.execute(sa.select(backup_t).where(backup_t.c.id == rid)).mappings().first()
    return _pub(dict(r)) if r else None


def _pub(r):
    p = os.path.join(backup_dir(), r['file'] or '')
    return {**{k: db.jsonable(v) for k, v in r.items()}, 'exists': os.path.exists(p), 'path': p}


def listing(limit=100) -> list[dict]:
    from .schema import backup_t
    with db.engine.connect() as c:
        rows = [dict(r) for r in c.execute(sa.select(backup_t).order_by(backup_t.c.id.desc()).limit(limit)).mappings()]
    return [_pub(r) for r in rows]


def delete(rid, user='system'):
    from . import audit
    from .schema import backup_t
    r = get(rid)
    if not r:
        raise KeyError(_tr('没有这个备份'))
    if r['exists']:
        os.remove(r['path'])
    with db.engine.begin() as c:
        c.execute(backup_t.update().where(backup_t.c.id == rid).values(status='deleted'))
        audit.audit(c, 'backup_delete', None, None, detail={'file': r['file']}, target=f'backup:{rid}', user=user)


def prune() -> list[str]:
    """保留策略：定时备份留最近 backup_keep 个；手动备份留 backup_keep_manual_days 天。"""
    from .schema import backup_t
    keep = int(float(_setting('backup_keep', 14) or 14))
    days = int(float(_setting('backup_keep_manual_days', 90) or 90))
    removed = []
    with db.engine.begin() as c:
        rows = [dict(r) for r in c.execute(sa.select(backup_t).where(backup_t.c.status == 'ok').order_by(backup_t.c.id.desc())).mappings()]
        sched = [r for r in rows if r['kind'] == 'scheduled']
        old = sched[keep:] + [r for r in rows if r['kind'] != 'scheduled' and r['created_at'] and r['created_at'] < datetime.now() - timedelta(days=days)]
        for r in old:
            p = os.path.join(backup_dir(), r['file'])
            if os.path.exists(p):
                os.remove(p)
            c.execute(backup_t.update().where(backup_t.c.id == r['id']).values(status='pruned'))
            removed.append(r['file'])
    return removed


def schedule() -> dict:
    return {'enabled': (_setting('backup_enabled', '1') or '1') in ('1', 'true', 'True'), 'time': _setting('backup_time', '02:30') or '02:30',
            'keep': int(float(_setting('backup_keep', 14) or 14)), 'keep_manual_days': int(float(_setting('backup_keep_manual_days', 90) or 90)),
            'dir': backup_dir()}


def set_schedule(v: dict) -> dict:
    with db.engine.begin() as c:
        if 'enabled' in v:
            db.set_setting(c, 'backup_enabled', '1' if v['enabled'] else '0', _tr('每天定时备份'))
        if v.get('time'):
            hh, mm = str(v['time']).split(':')
            assert 0 <= int(hh) < 24 and 0 <= int(mm) < 60
            db.set_setting(c, 'backup_time', f'{int(hh):02d}:{int(mm):02d}', _tr('定时备份时间（墙上时间）'))
        if v.get('keep'):
            db.set_setting(c, 'backup_keep', max(1, int(v['keep'])), _tr('保留的定时备份个数'))
        if v.get('keep_manual_days'):
            db.set_setting(c, 'backup_keep_manual_days', max(1, int(v['keep_manual_days'])), _tr('手动备份保留天数'))
    return schedule()


def due_now(now: datetime | None = None) -> bool:
    """定时备份到点了且今天还没做。"""
    from .schema import backup_t
    s = schedule()
    if not s['enabled']:
        return False
    now = now or datetime.now()
    hh, mm = map(int, s['time'].split(':'))
    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now < at:
        return False
    with db.engine.connect() as c:
        done = c.execute(sa.select(backup_t.c.id).where((backup_t.c.kind == 'scheduled') & (backup_t.c.created_at >= at))).first()
    return done is None


# ───────────────────────── 恢复 ─────────────────────────
def restore(zip_path: str, to_dir: str, to_db: str | None = None, finalize: bool = True) -> dict:
    """把备份恢复到新的数据目录（PostgreSQL 备份还要给新库 URL；库不存在会尝试创建），然后核对。不碰当前在用的数据。"""
    t0 = time.time()
    rep = {'ok': False, 'backup': os.path.basename(zip_path), 'to': to_dir, 'checks': []}

    def chk(name, ok, detail=''):
        rep['checks'].append({'check': name, 'ok': bool(ok), 'detail': detail})
        return ok
    if os.path.exists(to_dir) and os.listdir(to_dir):
        raise ValueError(_tr('目标目录不是空的：{to_dir}（恢复只往新目录里放，不覆盖）', to_dir=to_dir))
    os.makedirs(to_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        man = json.loads(z.read('manifest.json'))
        rep['manifest'] = {k: man.get(k) for k in ('created_at', 'kind', 'note', 'dialect', 'schema_head', 'ts_mode', 'by')}
        bad = z.testzip()
        chk(_tr('压缩包完整'), bad is None, bad or '')
        for f in man['files']:
            target = os.path.join(to_dir, *f['path'].split('/'))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open('files/' + f['path']) as src, open(target, 'wb') as out:
                shutil.copyfileobj(src, out)
        bad_files = [f['path'] for f in man['files'] if _sha(os.path.join(to_dir, *f['path'].split('/'))) != f['sha256']]
        chk(_tr('文件 {len} 个，哈希一致', len=len(man["files"])), not bad_files, _tr('、').join(bad_files[:10]))
        if man['dialect'] == 'sqlite':
            dbf = os.path.join(to_dir, 'mes.db')
            with z.open(man['db_file']) as src, open(dbf, 'wb') as out:
                shutil.copyfileobj(src, out)
            chk(_tr('数据库文件哈希一致'), _sha(dbf) == man['db_sha256'])
            url = 'sqlite:///' + dbf.replace('\\', '/')
            con = sqlite3.connect(dbf)
            ic = con.execute('PRAGMA integrity_check').fetchone()[0]
            con.close()
            chk(_tr('SQLite 完整性检查'), ic == 'ok', ic)
        else:
            if not to_db:
                raise ValueError(_tr('PostgreSQL 备份需要指定新库：--to-db postgresql+psycopg://…/新库名'))
            dump = os.path.join(to_dir, 'mes.pgdump.tmp')
            with z.open(man['db_file']) as src, open(dump, 'wb') as out:
                shutil.copyfileobj(src, out)
            chk(_tr('数据库文件哈希一致'), _sha(dump) == man['db_sha256'])
            ok = _pg_restore(dump, to_db, man.get('ts_mode') == 'timescale', rep, finalize)
            os.remove(dump)
            url = to_db
            if ok is False:                       # 没恢复成（如缺管理员账号）：不再往下核对
                rep['ok'] = False
                rep['ms'] = int((time.time() - t0) * 1000)
                with open(os.path.join(to_dir, 'restore_report.json'), 'w', encoding='utf-8') as f:
                    json.dump(rep, f, ensure_ascii=False, indent=1)
                return rep
    eng = sa.create_engine(url)
    try:
        counts = _counts(eng)
        diff = {t: (n, counts.get(t)) for t, n in man['counts'].items() if counts.get(t) != n}
        chk(_tr('各表行数一致（{len} 张表，{sum} 行）', len=len(man["counts"]), sum=sum(man["counts"].values())), not diff,
            _tr('；').join(_tr('{t}: 备份 {a} / 恢复 {b}', t=t, a=a, b=b) for t, (a, b) in list(diff.items())[:10]))
        from . import audit, migrations
        st = migrations.status(eng)
        chk(_tr('迁移版本'), st['head'] == man['schema_head'] or bool(st['pending']),
            _tr('备份时 {schema_head}，待执行 {v}（启动时会自动升级）', schema_head=man['schema_head'], v=st['pending'] or _tr('无')))
        v = audit.verify(eng, seal_first=False)
        chk(_tr('审计哈希链（{checked} 条已封存，{unsealed} 条未封存）', checked=v['checked'], unsealed=v['unsealed']), v['ok'], (v.get('problem') or {}).get('reason', ''))
    finally:
        eng.dispose()
    rep['ok'] = all(x['ok'] for x in rep['checks'])
    rep['ms'] = int((time.time() - t0) * 1000)
    rep['db_url'] = url if man['dialect'] == 'sqlite' else _mask(url)
    with open(os.path.join(to_dir, 'restore_report.json'), 'w', encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    return rep


def _mask(url):
    u = urlparse(url)
    return url.replace(f':{u.password}@', ':***@') if u.password else url


def _pg_restore(dump, to_db, timescale, rep, finalize=True):
    """pg_restore 到新库（库不存在就建，属主 = 应用的库用户）。
    TimescaleDB 的备份必须用数据库管理员恢复（timescaledb_pre_restore / post_restore 和内部 schema 要超级用户）：
    环境变量 MOM_PG_ADMIN_URL（旧名 MES_PG_ADMIN_URL）=postgresql+psycopg://postgres:密码@主机:端口（同一台服务器的管理员账号）。恢复后对象属主仍是应用用户。
    普通 PostgreSQL（pgpart 模式）用应用自己的账号就能恢复（需要 CREATEDB 权限才能自动建库）。"""
    app_user = sa.engine.make_url(to_db).username
    dbname = sa.engine.make_url(to_db).database
    admin = (env_get('MOM_PG_ADMIN_URL') or '').strip()
    use_admin = bool(timescale and admin)
    if timescale and not admin:
        rep['checks'].append({'check': _tr('TimescaleDB 恢复需要管理员账号'), 'ok': False,
                              'detail': _tr('设置环境变量 MOM_PG_ADMIN_URL=postgresql+psycopg://postgres:密码@主机:端口 后重试')})
        return False
    srv = (admin if use_admin else to_db.rsplit('/', 1)[0]).rstrip('/')
    srv = srv.rsplit('/', 1)[0] if sa.engine.make_url(srv).database else srv
    run_url = f'{srv}/{dbname}'
    args, _, env = _pg_args(run_url)
    eng = sa.create_engine(srv + '/postgres', isolation_level='AUTOCOMMIT')
    try:
        with eng.connect() as c:
            if not c.execute(sa.text('SELECT 1 FROM pg_database WHERE datname = :d'), {'d': dbname}).first():
                c.execute(sa.text(f'CREATE DATABASE "{dbname}"' + (f' OWNER "{app_user}"' if use_admin else '')))
    finally:
        eng.dispose()
    eng = sa.create_engine(run_url, isolation_level='AUTOCOMMIT')
    try:
        with eng.connect() as c:
            if sa.inspect(c).get_table_names():
                raise ValueError(_tr('新库 {dbname} 不是空的，恢复只往空库里放', dbname=dbname))
            if timescale:
                c.execute(sa.text('CREATE EXTENSION IF NOT EXISTS timescaledb'))
                c.execute(sa.text('SELECT timescaledb_pre_restore()'))
        cmd = [pg_bin('pg_restore'), *args, '-d', dbname] + ([] if use_admin else ['--no-owner']) + [dump]
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=3600)
        errs = [x for x in (r.stderr or '').splitlines() if 'error' in x.lower() and 'already exists' not in x]
        rep['checks'].append({'check': 'pg_restore' + (_tr('（管理员账号）') if use_admin else ''), 'ok': r.returncode == 0 or not errs, 'detail': '\n'.join(errs[:5])})
        if timescale and finalize:          # 演练库不结束恢复模式：不启动 TimescaleDB 后台作业，演练完能直接删掉
            with eng.connect() as c:
                c.execute(sa.text('SELECT timescaledb_post_restore()'))
    finally:
        eng.dispose()


def drill(rid, user='system', keep=False) -> dict:
    """恢复演练：把某个备份恢复到临时目录（PostgreSQL：临时库 <库名>_drill_<时间>），核对，写回报告，默认删掉临时数据。"""
    from . import audit
    from .schema import backup_t
    r = get(rid)
    if not r or not r['exists']:
        raise KeyError(_tr('备份文件不存在'))
    stamp = datetime.now().strftime('%Y%m%d%H%M%S')
    to_dir = os.path.join(backup_dir(), f'drill-{stamp}')
    to_db = None
    if r['dialect'] != 'sqlite':
        cur = db.engine.url.render_as_string(hide_password=False)
        to_db = cur.rsplit('/', 1)[0] + '/' + (db.engine.url.database or 'mes') + f'_drill_{stamp}'
    try:
        rep = restore(r['path'], to_dir, to_db, finalize=False)
    except Exception as ex:
        rep = {'ok': False, 'error': str(ex), 'checks': []}
    finally:
        if not keep:
            shutil.rmtree(to_dir, ignore_errors=True)
            if to_db:
                _drop_db(to_db)
    rep['drill_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rep['kept'] = keep
    with db.engine.begin() as c:
        c.execute(backup_t.update().where(backup_t.c.id == rid).values(verify=json.loads(json.dumps(rep, default=str))))
        audit.audit(c, 'backup_drill', None, None, detail={'file': r['file'], 'ok': rep.get('ok')}, target=f'backup:{rid}', user=user)
    return rep


def _drop_db(url):
    args, dbname, env = _pg_args(url)
    eng = sa.create_engine(url.rsplit('/', 1)[0] + '/postgres', isolation_level='AUTOCOMMIT')
    try:
        with eng.connect() as c:
            c.execute(sa.text('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :d'), {'d': dbname})
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}"'))
    except Exception:
        log.exception(_tr('删除演练库失败'))
    finally:
        eng.dispose()
