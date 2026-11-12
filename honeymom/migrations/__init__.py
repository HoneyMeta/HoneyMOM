"""版本化迁移（5.0 起取代 4.7 的 db.migrate() 临时补列）。怎么写新迁移见 docs/dev/foundation.md「迁移」。

- 每个迁移是本目录下的 vNNNN_说明.py，模块里写 TITLE 和 up(op)。编号决定顺序；已发布的迁移不要再改，改结构就加新文件。
- 启动时 db.init() 调 upgrade()：按编号执行还没执行过的，每个迁移一个事务，执行完写一行 schema_migration。
- op 的每个动作都是幂等的（表/列/索引已存在就跳过），所以新库（v0001 按 4.7 定版建出全部基线表）和老库（线上 4.7 库，表都在）
  走同一套步骤，结果一样。识别老库不靠猜：v0001 对已存在的表只补缺的列。
- 表结构的"真相"仍是 schema.py（列定义、类型、索引都从那里取）；但 schema.py 里多出来、却没有任何迁移建出来的表/列/索引
  会被 check() 当成"漂移"报出来，启动直接失败——所以改 schema.py 一定要配一个迁移。
- 命令行：python -m honeymom db status | upgrade [--dry-run] | check
"""
import importlib
import logging
import pkgutil
import re
import time
from datetime import datetime

import sqlalchemy as sa

log = logging.getLogger('mes.migrate')
LOCK_KEY = 'honeymes.migrate'


def versions():
    """[(rev, 模块)]，按编号排序。"""
    out = []
    for m in pkgutil.iter_modules(__path__):
        mm = re.match(r'^v(\d{4})_\w+$', m.name)
        if mm:
            out.append((mm.group(1), importlib.import_module(f'{__name__}.{m.name}')))
    out.sort(key=lambda x: x[0])
    revs = [r for r, _ in out]
    if len(revs) != len(set(revs)):
        raise RuntimeError(f'迁移编号重复：{revs}')
    return out


class Op:
    """迁移里能用的动作。全部幂等；dry_run 时只记录计划、不改库（但会模拟结果，后面的判断照常）。"""

    def __init__(self, conn, dry_run=False):
        self.conn = conn
        self.dialect = conn.dialect.name
        self.dry_run = dry_run
        self.actions: list[str] = []
        insp = sa.inspect(conn)
        self._tables = set(insp.get_table_names())
        self._views = set(insp.get_view_names())
        if self.dialect == 'postgresql':
            self._views |= set(conn.execute(sa.text("SELECT matviewname FROM pg_matviews WHERE schemaname = current_schema()")).scalars())
        self._cols: dict[str, set] = {}
        self._idx: set = set()
        for t in self._tables:
            self._idx |= {i['name'] for i in insp.get_indexes(t) if i.get('name')}

    # ---------- 查询 ----------
    @property
    def pg(self):
        return self.dialect == 'postgresql'

    def has_table(self, name):
        return name in self._tables or name in self._views

    def has_column(self, table, col):
        if table not in self._cols:
            self._cols[table] = {c['name'] for c in sa.inspect(self.conn).get_columns(table)} if table in self._tables else set()
        return col in self._cols[table]

    def has_index(self, name):
        return name in self._idx

    def scalar(self, sql, **params):
        return self.conn.execute(sa.text(sql), params).scalar()

    def _do(self, desc, fn):
        self.actions.append(desc)
        if not self.dry_run:
            fn()

    # ---------- 表 / 列 / 索引 ----------
    def _schema_table(self, name):
        from .. import schema
        t = schema.metadata.tables.get(name)
        if t is None:
            raise KeyError(f'schema.py 里没有表 {name}')
        return t

    def create_table(self, name, columns: list[str] | None = None):
        """按 schema.py 的定义建表（已存在跳过）。columns 只建其中这些列（基线用；之后的列由后续迁移 add_column 补）。"""
        if self.has_table(name):
            return False
        t = self._schema_table(name)
        md = sa.MetaData()
        cols = [c._copy() for c in t.columns if columns is None or c.name in columns]
        nt = sa.Table(name, md, *cols, **t.dialect_kwargs)
        self._do(f'建表 {name}（{len(cols)} 列）', lambda: nt.create(self.conn))
        self._tables.add(name)
        self._cols[name] = {c.name for c in cols}
        self._idx |= {i.name for i in nt.indexes}
        for idx in t.indexes:       # 复合索引等：列都在才建
            if all(c.name in self._cols[name] for c in idx.columns):
                self.create_index(idx.name, name, [c.name for c in idx.columns], unique=bool(idx.unique))
        return True

    def add_column(self, table, col: str | sa.Column, backfill=None):
        """加列（已存在跳过）。col 是列名时取 schema.py 里的定义；unique/index 的列同时建索引（ux_/ix_表_列）。
        backfill：给已有行填的值（UPDATE … WHERE 列 IS NULL）。"""
        c = self._schema_table(table).c[col] if isinstance(col, str) else col
        if self.has_column(table, c.name):
            return False
        ctype = c.type.compile(dialect=self.conn.dialect)
        self._do(f'加列 {table}.{c.name} {ctype}', lambda: self.conn.execute(sa.text(f'ALTER TABLE "{table}" ADD COLUMN "{c.name}" {ctype}')))
        self._cols.setdefault(table, set()).add(c.name)
        if c.unique:
            self.create_index(f'ux_{table}_{c.name}', table, [c.name], unique=True)
        elif c.index:
            self.create_index(f'ix_{table}_{c.name}', table, [c.name])
        if backfill is not None:
            self._do(f'回填 {table}.{c.name} = {backfill!r}',
                     lambda: self.conn.execute(sa.text(f'UPDATE "{table}" SET "{c.name}" = :v WHERE "{c.name}" IS NULL'), {'v': backfill}))
        return True

    def create_index(self, name, table, columns: list[str], unique=False, where: str | None = None):
        """建索引（同名已存在跳过）。where：部分索引条件（两个方言语法相同）。"""
        if self.has_index(name):
            return False
        cols = ', '.join(f'"{c}"' for c in columns)
        sql = f'CREATE {"UNIQUE " if unique else ""}INDEX IF NOT EXISTS "{name}" ON "{table}" ({cols})' + (f' WHERE {where}' if where else '')
        self._do(f'建{"唯一" if unique else ""}索引 {name} ON {table}({", ".join(columns)})', lambda: self.conn.execute(sa.text(sql)))
        self._idx.add(name)
        return True

    def drop_index(self, name):
        if not self.has_index(name):
            return False
        self._do(f'删索引 {name}', lambda: self.conn.execute(sa.text(f'DROP INDEX IF EXISTS "{name}"')))
        self._idx.discard(name)
        return True

    def rename_column(self, table, old, new):
        if not self.has_column(table, old) or self.has_column(table, new):
            return False
        self._do(f'改列名 {table}.{old} → {new}', lambda: self.conn.execute(sa.text(f'ALTER TABLE "{table}" RENAME COLUMN "{old}" TO "{new}"')))
        self._cols[table].discard(old)
        self._cols[table].add(new)
        return True

    def alter_type(self, table, col, type_: sa.types.TypeEngine, using: str | None = None):
        """改列类型：PostgreSQL 执行 ALTER COLUMN TYPE；SQLite 是动态类型，什么都不做。"""
        if not self.pg:
            return False
        t = type_.compile(dialect=self.conn.dialect)
        sql = f'ALTER TABLE "{table}" ALTER COLUMN "{col}" TYPE {t}' + (f' USING {using}' if using else '')
        self._do(f'改类型 {table}.{col} → {t}', lambda: self.conn.execute(sa.text(sql)))
        return True

    def execute(self, sql: str, dialect: str | None = None, desc: str | None = None, **params):
        """执行原生 SQL；dialect='postgresql'/'sqlite' 时只在该方言执行。尽量用上面的幂等动作，原生 SQL 自己保证幂等。"""
        if dialect and dialect != self.dialect:
            return False
        self._do(desc or ('SQL: ' + ' '.join(sql.split())[:120]), lambda: self.conn.execute(sa.text(sql), params) if params else _raw(self.conn, sql))
        return True

    def run(self, fn, desc: str):
        """数据迁移：fn(conn)。dry_run 时只记录。"""
        self._do('数据：' + desc, lambda: fn(self.conn))


def _raw(conn, sql):
    """不带参数的原生 SQL（可多条）：直接用 DBAPI 游标执行，免得 % 被当成占位符。和 conn 在同一个事务里。"""
    cur = conn.connection.cursor()
    try:
        cur.execute(sql)          # SQLite 一次只能一条（executescript 会先提交事务，不能用）；PostgreSQL 可以多条
    finally:
        cur.close()


# ───────────────────────── 执行 ─────────────────────────
def _ensure_rev_table(conn):
    from ..schema import schema_migration_t
    schema_migration_t.create(conn, checkfirst=True)


def applied(engine) -> dict:
    from ..schema import schema_migration_t as t
    with engine.connect() as c:
        if not sa.inspect(c).has_table('schema_migration'):
            return {}
        return {r['rev']: dict(r) for r in c.execute(sa.select(t)).mappings()}


def upgrade(engine, dry_run=False, target: str | None = None) -> list[dict]:
    """执行待执行的迁移（到 target 为止）。返回报告：[{rev, title, status: applied|done|planned, actions, ms}]。"""
    from ..schema import schema_migration_t as t
    report = []
    lock = engine.connect()
    try:
        if engine.dialect.name == 'postgresql':
            lock.execute(sa.text('SELECT pg_advisory_lock(hashtext(:k))'), {'k': LOCK_KEY})
            lock.commit()
        if not dry_run:
            with engine.begin() as c:
                _ensure_rev_table(c)
        done = applied(engine)
        for rev, mod in versions():
            if target and rev > target:
                break
            title = getattr(mod, 'TITLE', mod.__name__)
            if rev in done:
                report.append({'rev': rev, 'title': title, 'status': 'applied', 'applied_at': done[rev]['applied_at'], 'actions': []})
                continue
            t0 = time.time()
            if dry_run:
                with engine.connect() as c:
                    op = Op(c, dry_run=True)
                    mod.up(op)
                    c.rollback()
                report.append({'rev': rev, 'title': title, 'status': 'planned', 'actions': op.actions})
                continue
            with engine.begin() as c:
                op = Op(c)
                mod.up(op)
                ms = int((time.time() - t0) * 1000)
                c.execute(t.insert().values(rev=rev, title=title[:200], applied_at=datetime.now().replace(microsecond=0), ms=ms,
                                            dialect=engine.dialect.name, ops=len(op.actions)))
            if getattr(mod, 'after_commit', None):      # 少数 DDL 不能在事务里跑（如 TimescaleDB 刷新连续聚合）
                mod.after_commit(engine)
            log.info('迁移 %s %s：%d 个动作，%d ms', rev, title, len(op.actions), ms)
            report.append({'rev': rev, 'title': title, 'status': 'done', 'actions': op.actions, 'ms': ms})
    finally:
        if engine.dialect.name == 'postgresql':
            try:
                lock.execute(sa.text('SELECT pg_advisory_unlock(hashtext(:k))'), {'k': LOCK_KEY})
                lock.commit()
            except Exception:
                pass
        lock.close()
    return report


def check(engine) -> list[str]:
    """漂移检查：schema.py 里有、库里没有的表 / 列 / 索引（按列组合比，不按名字）。返回问题列表，空 = 一致。"""
    from .. import schema
    problems = []
    with engine.connect() as c:
        insp = sa.inspect(c)
        tables = set(insp.get_table_names()) | set(insp.get_view_names())
        ts_views = set()
        if engine.dialect.name == 'postgresql':
            tables |= set(c.execute(sa.text("SELECT matviewname FROM pg_matviews WHERE schemaname = current_schema()")).scalars())
            if c.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")).first():
                ts_views = set(schema.TS_VIEWS_ON_TIMESCALE)
        for t in schema.metadata.sorted_tables:
            if t.name not in tables:
                problems.append(f'缺表 {t.name}')
                continue
            if t.name in ts_views:
                continue
            have = {x['name'] for x in insp.get_columns(t.name)}
            for col in t.columns:
                if col.name not in have:
                    problems.append(f'缺列 {t.name}.{col.name}')
            idx = {(tuple(i['column_names']), bool(i['unique'])) for i in insp.get_indexes(t.name)}
            uq = {tuple(u['column_names']) for u in insp.get_unique_constraints(t.name)}
            pk = tuple(insp.get_pk_constraint(t.name).get('constrained_columns') or ())
            for i in t.indexes:
                cols = tuple(x.name for x in i.columns)
                if not all(x in have for x in cols):
                    continue
                if (cols, bool(i.unique)) in idx or (i.unique and (cols in uq or cols == pk)) or (not i.unique and (cols, True) in idx):
                    continue
                problems.append(f'缺{"唯一" if i.unique else ""}索引 {t.name}({", ".join(cols)})')
    return problems


def status(engine) -> dict:
    done = applied(engine)
    vs = versions()
    return {'applied': [{'rev': r, 'title': getattr(m, 'TITLE', ''), 'applied_at': done[r]['applied_at'].strftime('%Y-%m-%d %H:%M:%S') if done.get(r) else None,
                         'ms': (done.get(r) or {}).get('ms')} for r, m in vs],
            'pending': [r for r, _ in vs if r not in done], 'head': vs[-1][0] if vs else None, 'dialect': engine.dialect.name}
