"""数据库方言兼容层：同一份代码在 SQLite（演示/单机，默认）和 PostgreSQL（正式）上都能跑。

规则写在 docs/dev/foundation.md 的"数据库可移植"一节。这里放三类东西：
1. 类型编译：PostgreSQL 上 VARCHAR 不限长（SQLite 不检查长度，PostgreSQL 超长直接报错；统一成"不限长"）。
2. 可移植的 SQL 小工具：upsert / insert_ignore / hour_bucket / day_bucket / epoch / advisory_lock。
3. "问数"SQL 兼容函数：大模型和用户写的 SQL 常带另一种方言的函数。
   - PostgreSQL 上建 SQLite 风格函数：strftime、julianday、datetime、date(x, 修饰符)、ifnull、iif、instr、group_concat、
     json_extract、round(double, int)、substr(时间)、"时间 LIKE 文本"、"布尔 = 整数"（迁移 v0003 建）。
   - SQLite 上注册 PostgreSQL 风格函数：date_trunc、now、string_agg、greatest、least、to_char（连接时注册）。
   - 两边都有的 hm_ 前缀函数（推荐）：hm_epoch(ts) 秒数、hm_hour(ts) 'YYYY-MM-DD HH:00:00'、hm_day(ts) 'YYYY-MM-DD'、hm_minutes(a, b) 分钟差。
"""
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.compiler import compiles


# ───────────────────────── 1. 类型 ─────────────────────────
@compiles(sa.String, 'postgresql')
def _pg_varchar(type_, compiler, **kw):
    return 'VARCHAR'


@compiles(sa.VARCHAR, 'postgresql')
def _pg_varchar2(type_, compiler, **kw):
    return 'VARCHAR'


# ───────────────────────── 2. 可移植 SQL 工具 ─────────────────────────
def dialect(conn_or_engine) -> str:
    return conn_or_engine.dialect.name


def _insert(conn):
    if dialect(conn) == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


def upsert(conn, table: sa.Table, rows: list[dict] | dict, keys: list[str], update: list[str] | None = None):
    """INSERT … ON CONFLICT (keys) DO UPDATE SET update 列 = 新值。两个方言都支持（SQLite ≥ 3.24）。update=None 表示更新除键以外的全部列。"""
    rows = [rows] if isinstance(rows, dict) else rows
    if not rows:
        return
    ins = _insert(conn)(table)
    cols = update if update is not None else [k for k in rows[0] if k not in keys]
    stmt = ins.on_conflict_do_update(index_elements=keys, set_={c: ins.excluded[c] for c in cols}) if cols else ins.on_conflict_do_nothing(index_elements=keys)
    conn.execute(stmt, rows)


def insert_ignore(conn, table: sa.Table, rows: list[dict], keys: list[str] | None = None) -> int:
    """批量插入，主键/唯一键冲突的行跳过（补传重放去重）。返回实际插入的行数（PostgreSQL 精确；SQLite 用 changes() 累计）。"""
    if not rows:
        return 0
    ins = _insert(conn)(table)
    stmt = ins.on_conflict_do_nothing(index_elements=keys) if keys else ins.on_conflict_do_nothing()
    if dialect(conn) == 'sqlite':
        before = conn.exec_driver_sql('SELECT total_changes()').scalar()
        conn.execute(stmt, rows)
        return conn.exec_driver_sql('SELECT total_changes()').scalar() - before
    # PostgreSQL：executemany 的 rowcount 不可靠，用 RETURNING 数实际插入的行（冲突跳过的行不返回）
    col = list(table.primary_key.columns)[0] if table.primary_key.columns else list(table.c)[0]
    return len(conn.execute(stmt.returning(col), rows).all())


def hour_bucket(col, conn):
    """时间列截到整点（返回时间类型，可 group by / 比较）。"""
    if dialect(conn) == 'postgresql':
        return sa.func.date_trunc('hour', col)
    return sa.func.strftime('%Y-%m-%d %H:00:00', col)


def minute_bucket(col, conn):
    if dialect(conn) == 'postgresql':
        return sa.func.date_trunc('minute', col)
    return sa.func.strftime('%Y-%m-%d %H:%M:00', col)


def day_bucket(col, conn):
    if dialect(conn) == 'postgresql':
        return sa.func.date_trunc('day', col)
    return sa.func.strftime('%Y-%m-%d 00:00:00', col)


def epoch(col, conn):
    """时间 → 秒数（浮点）。"""
    if dialect(conn) == 'postgresql':
        return sa.extract('epoch', col)
    return (sa.func.julianday(col) - 2440587.5) * 86400.0


def advisory_lock(conn, key: str):
    """事务级互斥锁（PostgreSQL：pg_advisory_xact_lock，提交/回滚时释放；SQLite：写锁本来就是库级的，什么都不做）。
    多进程（多个服务实例 / 命令行）同时生成单号、写审计链时用。"""
    if dialect(conn) == 'postgresql':
        conn.execute(sa.text('SELECT pg_advisory_xact_lock(hashtext(:k))'), {'k': key})


def to_dt(v):
    """SQLite 上 func.strftime 之类返回字符串；统一转成 datetime。"""
    if v is None or isinstance(v, datetime):
        return v
    s = str(v).replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(s[:26], fmt)
        except ValueError:
            pass
    return None


# ───────────────────────── 3a. SQLite：注册 PostgreSQL 风格函数（连接时） ─────────────────────────
def _p(v):
    return to_dt(v) if not isinstance(v, datetime) else v


def _date_trunc(unit, v):
    d = _p(v)
    if d is None:
        return None
    u = (unit or '').lower()
    if u == 'year':
        d = d.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif u == 'quarter':
        d = d.replace(month=(d.month - 1) // 3 * 3 + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif u == 'month':
        d = d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif u == 'week':
        d = (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif u == 'day':
        d = d.replace(hour=0, minute=0, second=0, microsecond=0)
    elif u == 'hour':
        d = d.replace(minute=0, second=0, microsecond=0)
    elif u == 'minute':
        d = d.replace(second=0, microsecond=0)
    elif u == 'second':
        d = d.replace(microsecond=0)
    return d.strftime('%Y-%m-%d %H:%M:%S')


_PG_FMT = (('YYYY', '%Y'), ('YY', '%y'), ('MM', '%m'), ('DD', '%d'), ('HH24', '%H'), ('HH', '%I'), ('MI', '%M'), ('SS', '%S'))


def _to_char(v, fmt):
    d = _p(v)
    if d is None or fmt is None:
        return None
    out = str(fmt)
    for a, b in _PG_FMT:
        out = out.replace(a, b)
    return d.strftime(out)


class _StringAgg:
    def __init__(self):
        self.items, self.sep = [], ','

    def step(self, v, sep=','):
        if v is not None:
            self.items.append(str(v))
        self.sep = sep if sep is not None else ''

    def finalize(self):
        return self.sep.join(self.items) if self.items else None


def _greatest(*a):
    a = [x for x in a if x is not None]
    return max(a) if a else None


def _least(*a):
    a = [x for x in a if x is not None]
    return min(a) if a else None


def _hm_epoch(v):
    d = _p(v)
    return (d - datetime(1970, 1, 1)).total_seconds() if d else None


def _hm_minutes(a, b):
    x, y = _p(a), _p(b)
    return (y - x).total_seconds() / 60.0 if x and y else None


def sqlite_register(dbapi_conn):
    """在 SQLite 连接上注册兼容函数（db.init 的 connect 事件里调用）。"""
    f = dbapi_conn.create_function
    f('date_trunc', 2, _date_trunc, deterministic=True)
    f('to_char', 2, _to_char, deterministic=True)
    f('now', 0, lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    f('greatest', -1, _greatest, deterministic=True)
    f('least', -1, _least, deterministic=True)
    f('hm_epoch', 1, _hm_epoch, deterministic=True)
    f('hm_hour', 1, lambda v: _date_trunc('hour', v), deterministic=True)
    f('hm_day', 1, lambda v: (_p(v).strftime('%Y-%m-%d') if _p(v) else None), deterministic=True)
    f('hm_minutes', 2, _hm_minutes, deterministic=True)
    dbapi_conn.create_aggregate('string_agg', 2, _StringAgg)


# ───────────────────────── 3b. PostgreSQL：SQLite 风格函数（迁移 v0003 执行） ─────────────────────────
PG_COMPAT_SQL = r"""
CREATE OR REPLACE FUNCTION hm_fmt(fmt text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(replace(fmt,
    '%Y','YYYY'),'%m','MM'),'%d','DD'),'%H','HH24'),'%M','MI'),'%S','SS'),'%j','DDD'),'%w','D'),'%W','IW'),'%f','SS.MS'),'%s','"EPOCH"')
$$;
CREATE OR REPLACE FUNCTION hm_ts(v text) RETURNS timestamp LANGUAGE sql STABLE AS $$
  SELECT CASE WHEN lower(v) = 'now' THEN localtimestamp(0) ELSE v::timestamp END
$$;
CREATE OR REPLACE FUNCTION hm_mod(t timestamp, m text) RETURNS timestamp LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE s text := lower(trim(m));
BEGIN
  IF s IS NULL OR s = '' THEN RETURN t; END IF;
  IF s = 'start of day' THEN RETURN date_trunc('day', t); END IF;
  IF s = 'start of month' THEN RETURN date_trunc('month', t); END IF;
  IF s = 'start of year' THEN RETURN date_trunc('year', t); END IF;
  IF s = 'localtime' OR s = 'utc' THEN RETURN t; END IF;
  IF s ~ '^weekday [0-6]$' THEN RETURN t + (((substr(s, 9)::int - extract(dow from t)::int) + 7) % 7) * interval '1 day'; END IF;
  RETURN t + s::interval;
END $$;
CREATE OR REPLACE FUNCTION strftime(fmt text, t timestamp) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN fmt = '%s' THEN floor(extract(epoch from t))::bigint::text ELSE to_char(t, hm_fmt(fmt)) END
$$;
CREATE OR REPLACE FUNCTION strftime(fmt text, t text) RETURNS text LANGUAGE sql STABLE AS $$ SELECT strftime(fmt, hm_ts(t)) $$;
CREATE OR REPLACE FUNCTION strftime(fmt text, t timestamp, m1 text) RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT strftime(fmt, hm_mod(t, m1)) $$;
CREATE OR REPLACE FUNCTION strftime(fmt text, t text, m1 text) RETURNS text LANGUAGE sql STABLE AS $$ SELECT strftime(fmt, hm_mod(hm_ts(t), m1)) $$;
CREATE OR REPLACE FUNCTION strftime(fmt text, t text, m1 text, m2 text) RETURNS text LANGUAGE sql STABLE AS $$ SELECT strftime(fmt, hm_mod(hm_mod(hm_ts(t), m1), m2)) $$;
CREATE OR REPLACE FUNCTION julianday(t timestamp) RETURNS double precision LANGUAGE sql IMMUTABLE AS $$ SELECT extract(epoch from t)::double precision / 86400.0 + 2440587.5 $$;
CREATE OR REPLACE FUNCTION julianday(t text) RETURNS double precision LANGUAGE sql STABLE AS $$ SELECT julianday(hm_ts(t)) $$;
CREATE OR REPLACE FUNCTION julianday(t text, m1 text) RETURNS double precision LANGUAGE sql STABLE AS $$ SELECT julianday(hm_mod(hm_ts(t), m1)) $$;
CREATE OR REPLACE FUNCTION datetime(t timestamp) RETURNS timestamp LANGUAGE sql IMMUTABLE AS $$ SELECT date_trunc('second', t) $$;
CREATE OR REPLACE FUNCTION datetime(t text) RETURNS timestamp LANGUAGE sql STABLE AS $$ SELECT date_trunc('second', hm_ts(t)) $$;
CREATE OR REPLACE FUNCTION datetime(t timestamp, m1 text) RETURNS timestamp LANGUAGE sql IMMUTABLE AS $$ SELECT date_trunc('second', hm_mod(t, m1)) $$;
CREATE OR REPLACE FUNCTION datetime(t text, m1 text) RETURNS timestamp LANGUAGE sql STABLE AS $$ SELECT date_trunc('second', hm_mod(hm_ts(t), m1)) $$;
CREATE OR REPLACE FUNCTION datetime(t text, m1 text, m2 text) RETURNS timestamp LANGUAGE sql STABLE AS $$ SELECT date_trunc('second', hm_mod(hm_mod(hm_ts(t), m1), m2)) $$;
CREATE OR REPLACE FUNCTION date(t text, m1 text) RETURNS date LANGUAGE sql STABLE AS $$ SELECT hm_mod(hm_ts(t), m1)::date $$;
CREATE OR REPLACE FUNCTION date(t timestamp, m1 text) RETURNS date LANGUAGE sql IMMUTABLE AS $$ SELECT hm_mod(t, m1)::date $$;
CREATE OR REPLACE FUNCTION date(t text, m1 text, m2 text) RETURNS date LANGUAGE sql STABLE AS $$ SELECT hm_mod(hm_mod(hm_ts(t), m1), m2)::date $$;
CREATE OR REPLACE FUNCTION ifnull(a anycompatible, b anycompatible) RETURNS anycompatible LANGUAGE sql IMMUTABLE AS $$ SELECT coalesce(a, b) $$;
CREATE OR REPLACE FUNCTION iif(c boolean, a anycompatible, b anycompatible) RETURNS anycompatible LANGUAGE sql IMMUTABLE AS $$ SELECT CASE WHEN c THEN a ELSE b END $$;
CREATE OR REPLACE FUNCTION instr(a text, b text) RETURNS integer LANGUAGE sql IMMUTABLE AS $$ SELECT strpos(a, b) $$;
CREATE OR REPLACE FUNCTION round(v double precision, d integer) RETURNS double precision LANGUAGE sql IMMUTABLE AS $$ SELECT round(v::numeric, d)::double precision $$;
CREATE OR REPLACE FUNCTION substr(t timestamp, a integer) RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT substr(to_char(t, 'YYYY-MM-DD HH24:MI:SS'), a) $$;
CREATE OR REPLACE FUNCTION substr(t timestamp, a integer, b integer) RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT substr(to_char(t, 'YYYY-MM-DD HH24:MI:SS'), a, b) $$;
CREATE OR REPLACE FUNCTION json_extract(j json, p text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT j #>> string_to_array(regexp_replace(regexp_replace(p, '^\$\.?', ''), '\[(\d+)\]', '.\1', 'g'), '.')
$$;
CREATE OR REPLACE FUNCTION hm_gc_sf(acc text, v text, sep text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN v IS NULL THEN acc WHEN acc IS NULL THEN v ELSE acc || sep || v END
$$;
CREATE OR REPLACE FUNCTION hm_gc_sf1(acc text, v text) RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT hm_gc_sf(acc, v, ',') $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'group_concat' AND pronargs = 1) THEN
    CREATE AGGREGATE group_concat(text) (SFUNC = hm_gc_sf1, STYPE = text);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'group_concat' AND pronargs = 2) THEN
    CREATE AGGREGATE group_concat(text, text) (SFUNC = hm_gc_sf, STYPE = text);
  END IF;
END $$;
CREATE OR REPLACE FUNCTION hm_epoch(t timestamp) RETURNS double precision LANGUAGE sql IMMUTABLE AS $$ SELECT extract(epoch from t)::double precision $$;
CREATE OR REPLACE FUNCTION hm_hour(t timestamp) RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT to_char(date_trunc('hour', t), 'YYYY-MM-DD HH24:MI:SS') $$;
CREATE OR REPLACE FUNCTION hm_day(t timestamp) RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT to_char(t, 'YYYY-MM-DD') $$;
CREATE OR REPLACE FUNCTION hm_minutes(a timestamp, b timestamp) RETURNS double precision LANGUAGE sql IMMUTABLE AS $$ SELECT extract(epoch from (b - a))::double precision / 60.0 $$;
CREATE OR REPLACE FUNCTION hm_ts_like(t timestamp, p text) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$ SELECT to_char(t, 'YYYY-MM-DD HH24:MI:SS') LIKE p $$;
CREATE OR REPLACE FUNCTION hm_bool_eq_int(a boolean, b integer) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$ SELECT a = (b <> 0) $$;
CREATE OR REPLACE FUNCTION hm_int_eq_bool(a integer, b boolean) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$ SELECT (a <> 0) = b $$;
CREATE OR REPLACE FUNCTION hm_bool_ne_int(a boolean, b integer) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$ SELECT a <> (b <> 0) $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_operator WHERE oprname = '~~' AND oprleft = 'timestamp'::regtype AND oprright = 'text'::regtype) THEN
    CREATE OPERATOR ~~ (LEFTARG = timestamp, RIGHTARG = text, FUNCTION = hm_ts_like);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_operator WHERE oprname = '=' AND oprleft = 'boolean'::regtype AND oprright = 'integer'::regtype) THEN
    CREATE OPERATOR = (LEFTARG = boolean, RIGHTARG = integer, FUNCTION = hm_bool_eq_int);
    CREATE OPERATOR = (LEFTARG = integer, RIGHTARG = boolean, FUNCTION = hm_int_eq_bool);
    CREATE OPERATOR <> (LEFTARG = boolean, RIGHTARG = integer, FUNCTION = hm_bool_ne_int);
  END IF;
END $$;
"""

# 给大模型 / 文档用的"可移植写法"速查（两个方言都能跑）
PORTABLE_HINT = """可移植写法（SQLite 和 PostgreSQL 都能跑，优先用）：
- 时间比较：ts >= '2026-11-04 08:00:00'（字符串字面量和时间列直接比）；不要对时间列用 LIKE / substr。
- 按小时/天分组：hm_hour(ts)、hm_day(ts)；时间差（分钟）：hm_minutes(开始, 结束)；时间 → 秒：hm_epoch(ts)。
- 空值：coalesce(a, b)；条件：CASE WHEN … THEN … ELSE … END；拼接：a || b。
- 布尔列：= true / = false（不要写 = 1）。比例：sum(x) * 1.0 / count(*)；保留小数：round(x, 2)。
- 聚合时 SELECT 里的非聚合列都要写进 GROUP BY；字符串比较区分大小写，模糊查询用 lower(列) LIKE lower('%词%')。"""
