"""设备时序存储（honeymom/ts.py）。三种模式，记在系统参数 ts_mode：

- timescale：PostgreSQL + TimescaleDB。ts_raw 是超表（按天分块、7 天后压缩），ts_1m / ts_1h 是连续聚合（实时聚合，
  刷新策略每分钟 / 每 10 分钟），保留策略由 ts.apply_retention() 按系统参数设置。
- pgpart：PostgreSQL 没装 TimescaleDB（或 MOM_TS_TIMESCALE=0，旧名 MES_TS_TIMESCALE）。ts_raw 用原生分区表（按月，写入时自动建分区），
  ts_1m / ts_1h 是普通表，由 ts.rollup_job() 增量汇总；保留 = 删整月分区 / 删旧行。
- sqlite：ts_raw 无 rowid 表，主键 (point, ts)；汇总表 + 汇总作业同 pgpart。
"""
from ..env import get as env_get
import os

TITLE = '设备时序：原始值 / 状态区间 / 1 分钟与 1 小时汇总'

PG_PART_RAW = """CREATE TABLE IF NOT EXISTS ts_raw (point integer NOT NULL, ts timestamp NOT NULL, value double precision, text varchar, q smallint,
  PRIMARY KEY (point, ts)) PARTITION BY RANGE (ts)"""

TS_HYPER = [
    "SELECT create_hypertable('ts_raw', by_range('ts', INTERVAL '1 day'), if_not_exists => TRUE)",
    "ALTER TABLE ts_raw SET (timescaledb.compress, timescaledb.compress_segmentby = 'point', timescaledb.compress_orderby = 'ts DESC')",
    "SELECT add_compression_policy('ts_raw', INTERVAL '7 days', if_not_exists => TRUE)",
]
TS_CAGG_1M = """CREATE MATERIALIZED VIEW IF NOT EXISTS ts_1m WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 minute', ts) AS bucket, point, count(value)::integer AS n, min(value) AS vmin, max(value) AS vmax, avg(value) AS vavg,
       first(value, ts) AS vfirst, last(value, ts) AS vlast
FROM ts_raw GROUP BY 1, 2 WITH NO DATA"""
TS_CAGG_1H = """CREATE MATERIALIZED VIEW IF NOT EXISTS ts_1h WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 hour', bucket) AS bucket, point, sum(n)::integer AS n, min(vmin) AS vmin, max(vmax) AS vmax,
       sum(vavg * n) / NULLIF(sum(n), 0) AS vavg, first(vfirst, bucket) AS vfirst, last(vlast, bucket) AS vlast
FROM ts_1m GROUP BY 1, 2 WITH NO DATA"""
TS_POLICIES = [
    "SELECT add_continuous_aggregate_policy('ts_1m', start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 minute', "
    "schedule_interval => INTERVAL '1 minute', if_not_exists => TRUE)",
    "SELECT add_continuous_aggregate_policy('ts_1h', start_offset => INTERVAL '30 days', end_offset => INTERVAL '1 hour', "
    "schedule_interval => INTERVAL '10 minutes', if_not_exists => TRUE)",
]


def detect_mode(op) -> str:
    if not op.pg:
        return 'sqlite'
    if env_get('MOM_TS_TIMESCALE', '1') == '0':
        return 'pgpart'
    if op.scalar("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'"):
        return 'timescale'
    if not op.scalar("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'") or op.dry_run:
        return 'pgpart'
    sp = op.conn.begin_nested()
    try:
        op.conn.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS timescaledb')
        sp.commit()
        op.actions.append('CREATE EXTENSION timescaledb')
        return 'timescale'
    except Exception:          # 没有权限 / 没有 shared_preload_libraries：退回原生分区
        sp.rollback()
        return 'pgpart'


def up(op):
    for t in ('ts_point', 'ts_segment', 'ts_dirty'):
        op.create_table(t)
    mode = detect_mode(op)
    if mode == 'sqlite':
        for t in ('ts_raw', 'ts_1m', 'ts_1h'):
            op.create_table(t)
    elif mode == 'pgpart':
        if not op.has_table('ts_raw'):
            op.execute(PG_PART_RAW, desc='建 ts_raw（原生分区表，按月）')
        for t in ('ts_1m', 'ts_1h'):
            op.create_table(t)
    else:
        if not op.has_table('ts_raw'):
            op.create_table('ts_raw')
            for sql in TS_HYPER:
                op.execute(sql)
        if not op.has_table('ts_1m'):
            op.execute(TS_CAGG_1M, desc='建连续聚合 ts_1m')
        if not op.has_table('ts_1h'):
            op.execute(TS_CAGG_1H, desc='建连续聚合 ts_1h（基于 ts_1m）')
        for sql in TS_POLICIES:
            op.execute(sql)
    op.run(lambda c: _set_mode(c, mode), f'系统参数 ts_mode = {mode}')


def _set_mode(conn, mode):
    from .. import db
    db.set_setting(conn, 'ts_mode', mode, '设备时序存储模式（迁移 v0004 决定：timescale / pgpart / sqlite）')
