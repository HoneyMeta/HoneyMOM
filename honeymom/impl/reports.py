"""报表设计器：选数据集 → 字段 / 筛选 / 分组 / 汇总 / 图表 → 保存、按角色共享、定时导出（xlsx / PDF 进站内通知）、钉到工作台。
说明见 docs/dev/config.md「报表设计器」。用户不写 SQL：数据集是策划好的安全视图（实体 + 关联的名称），查询用 SQLAlchemy 表达式拼，
权限 = 数据集里每个用到的实体都要有查看权限，数据范围按基础实体过滤（perms.scope_cond）。

登记数据集（别的模块）：
    from honeymom.impl import reports
    reports.dataset('sales_order', '销售订单', '销售', 'sales_order', time='order_date',
                    joins=[('customer', 'name', '客户名称'), ('customer', 'short_name', '客户简称')])
"""
from ..i18n import _tr
import asyncio
import io
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi import HTTPException

from .. import audit, db, perms
from ..schema import BY_NAME, TABLES

log = logging.getLogger('mes.impl.reports')
AGGS = {'count': '计数', 'sum': '合计', 'avg': '平均', 'min': '最小', 'max': '最大', 'count_distinct': '去重计数'}
BUCKETS = {'hour': '按小时', 'day': '按天', 'week': '按周', 'month': '按月'}
FOPS = {'=': '等于', '!=': '不等于', '>': '大于', '>=': '大于等于', '<': '小于', '<=': '小于等于', 'contains': '包含', 'in': '属于', 'empty': '为空',
        'not_empty': '不为空', 'today': '今天', 'last_days': '最近 N 天', 'this_month': '本月', 'between': '介于'}
CHARTS = {'table': '表格', 'bar': '柱状图', 'line': '折线图', 'pie': '饼图', 'kpi': '指标卡'}
MAX_ROWS = 5000


@dataclass
class Dataset:
    key: str
    label: str
    module: str
    entity: str
    time: str | None = None                 # 主要时间字段（缺省的时间筛选、按天分组）
    joins: list = field(default_factory=list)   # [(引用字段 或 '引用字段.下一级引用字段', 对方字段, 中文名)]
    exclude: tuple = ()
    desc: str = ''
    pack_module: str | None = None          # 只有启用了这个可选模块（行业模板）才显示

    def fields(self) -> list[dict]:
        e = BY_NAME[self.entity]
        out = []
        for f in e.fields:
            if f.t in ('json', 'text') or f.name in ('password_hash',) + tuple(self.exclude):
                continue
            t = 'num' if f.t in ('int', 'float') else f.t
            if f.t == 'ref':
                out.append({'key': f.name + '__label', 'label': f.label, 't': 'str', 'path': [f.name], 'col': _label_col(f.ref), 'ref': f.ref})
                continue
            out.append({'key': f.name, 'label': f.label, 't': t, 'path': [], 'col': f.name, 'enum': f.enum})
        for path, col, label in self.joins:
            ps = path.split('.')
            ent = self.entity
            for p in ps:
                fd = next(x for x in BY_NAME[ent].fields if x.name == p)
                ent = fd.ref
            cf_ = next((x for x in BY_NAME[ent].fields if x.name == col), None)
            t = 'num' if cf_ and cf_.t in ('int', 'float') else (cf_.t if cf_ else 'str')
            out.append({'key': '__'.join(ps) + '__' + col, 'label': label, 't': 'str' if t == 'ref' else t, 'path': ps, 'col': col, 'enum': cf_.enum if cf_ else None,
                        'ref': ent})
        from . import cf
        for d in cf.fields(self.entity):
            out.append({'key': d['key'], 'label': d['label'], 't': {'number': 'num', 'date': 'date'}.get(d['ftype'], 'str'), 'path': [], 'cf': d,
                        'enum': cf.enum_of(d) if d['ftype'] == 'enum' else None})
        return out

    def entities(self, used_paths) -> set:
        out = {self.entity}
        for ps in used_paths:
            ent = self.entity
            for p in ps:
                ent = next(x for x in BY_NAME[ent].fields if x.name == p).ref
                out.add(ent)
        return out

    def out(self):
        return {'key': self.key, 'label': self.label, 'module': self.module, 'entity': self.entity, 'time': self.time, 'desc': self.desc,
                'fields': [{k: v for k, v in f.items() if k in ('key', 'label', 't', 'enum')} for f in self.fields()]}


def _label_col(entity):
    names = [f.name for f in BY_NAME[entity].fields]
    for k in ('name', 'title', 'username', 'code', 'sn'):
        if k in names:
            return k
    return 'id'


DATASETS: dict[str, Dataset] = {}


def dataset(key, label, module, entity, **kw) -> Dataset:
    DATASETS[key] = Dataset(key, label, module, entity, **kw)
    return DATASETS[key]


# 策划好的数据集（各模块的常用视图）
dataset('work_order', '生产工单', '生产', 'work_order', time='start_plan', joins=[('product', 'code', '产品编码'), ('product', 'mtype', '产品类型')],
        desc='计划 / 完工 / 报废、状态、交期')
dataset('report', '报工记录', '生产', 'report', time='end', joins=[('work_order.product', 'name', '产品'), ('employee.team', 'name', '班组')],
        desc='合格 / 不良、按人员 / 设备 / 工序 / 班次')
dataset('worker_daily', '工人日报', '生产', 'worker_daily', time='day')
dataset('attendance', '上下工记录', '生产', 'attendance', time='clock_on')
dataset('andon', '安灯', '生产', 'andon', time='raised_at')
dataset('insp_record', '检验记录', '质量', 'insp_record', time='time')
dataset('ncr', '不合格品', '质量', 'ncr', time='found_at', joins=[('defect', 'category', '缺陷类别'), ('defect', 'severity', '严重度')])
dataset('first_article', '首检', '质量', 'first_article', time='requested_at')
dataset('equipment', '设备台账', '设备', 'equipment', joins=[('line.workshop', 'name', '车间')])
dataset('alarm', '报警记录', '设备', 'alarm', time='start', joins=[('equipment', 'etype', '设备类型'), ('equipment.line', 'name', '产线')])
dataset('repair_order', '维修工单', '设备', 'repair_order', time='reported_at', joins=[('equipment', 'etype', '设备类型')])
dataset('maint_record', '点检保养记录', '设备', 'maint_record', time='time')
dataset('equip_state', '设备状态记录', '设备', 'equip_state', time='start', joins=[('equipment', 'etype', '设备类型')])
dataset('energy_hour', '小时电耗', '能源', 'energy_hour', time='hour', joins=[('equipment', 'etype', '设备类型')])
dataset('stock', '库存', '仓储', 'stock', time='updated_at', joins=[('material', 'mtype', '物料类型'), ('material', 'unit', '单位')])
dataset('stock_txn', '出入库记录', '仓储', 'stock_txn', time='time', joins=[('material', 'mtype', '物料类型')])
dataset('material', '物料', '主数据', 'material')
dataset('tooling', '模具 / 夹具', '设备', 'tooling')
dataset('part', '在制品 / 一件一码', '追溯', 'part', time='born_at')
dataset('heat', '炉次记录', '铸造', 'heat', time='start', pack_module='foundry')
dataset('tool', '刀具台账', '机加', 'tool', pack_module='machining')
dataset('tool_change', '换刀记录', '机加', 'tool_change', time='time', pack_module='machining')


def get_ds(key) -> Dataset:
    if key not in DATASETS:
        raise HTTPException(404, _tr('没有数据集 {key}', key=key))
    return DATASETS[key]


def can_ds(user, ds: Dataset, paths=()) -> bool:
    return all(perms.can(user, perms.entity_perm(e, 'read')) for e in ds.entities(paths))


def list_datasets(user) -> list:
    from . import packs
    mods = packs.enabled_modules()
    return [d.out() for d in DATASETS.values() if (not d.pack_module or d.pack_module in mods) and can_ds(user, d)]


# ───────────────────────── 查询 ─────────────────────────
def _bucket(col, unit, dialect):
    pg = dialect == 'postgresql'
    fmt = {'hour': ('%Y-%m-%d %H:00', 'YYYY-MM-DD HH24:00'), 'day': ('%Y-%m-%d', 'YYYY-MM-DD'), 'week': ('%Y-W%W', 'IYYY-"W"IW'),
           'month': ('%Y-%m', 'YYYY-MM')}[unit]
    return sa.func.to_char(col, fmt[1], type_=sa.String) if pg else sa.func.strftime(fmt[0], col, type_=sa.String)


class Q:
    """一次报表查询：别名、连接、字段表达式。"""

    def __init__(self, ds: Dataset, conn):
        self.ds, self.conn = ds, conn
        self.base = TABLES[ds.entity].alias('b')
        self.alias = {(): self.base}
        self.joins = []
        self.fmap = {f['key']: f for f in ds.fields()}
        self.paths = set()

    def table_for(self, path):
        path = tuple(path)
        if path in self.alias:
            return self.alias[path]
        parent = self.table_for(path[:-1])
        ent = self.ds.entity
        for p in path[:-1]:
            ent = next(x for x in BY_NAME[ent].fields if x.name == p).ref
        ref = next(x for x in BY_NAME[ent].fields if x.name == path[-1]).ref
        a = TABLES[ref].alias('j' + str(len(self.alias)))
        self.alias[path] = a
        self.joins.append((a, parent.c[path[-1]] == a.c.id))
        return a

    def col(self, key):
        f = self.fmap.get(key)
        if not f:
            raise HTTPException(400, _tr('数据集「{label}」没有字段 {key}', label=self.ds.label, key=key))
        if f.get('cf'):
            from . import cf
            if not cf.has_ext(self.conn, self.ds.entity):
                return sa.null()
            return cf.expr(self.base, f['cf'], self.conn.dialect.name)
        if f['path']:
            self.paths.add(tuple(f['path']))
        return self.table_for(f['path']).c[f['col']]

    def from_(self):
        j = self.base
        for a, on in self.joins:
            j = j.outerjoin(a, on)
        return j


def _rel_range(op, value):
    now = db.clock.now()
    if op == 'today':
        s = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return s, s + timedelta(days=1)
    if op == 'last_days':
        return now - timedelta(days=float(value or 7)), None
    if op == 'this_month':
        s = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return s, None
    return None, None


def _cond(q: Q, flt):
    key, op, val = flt.get('field'), flt.get('op') or '=', flt.get('value')
    f = q.fmap.get(key)
    col = q.col(key)
    t = f['t'] if f else 'str'

    def cv(v):
        if v in (None, ''):
            return v
        if t == 'num':
            return float(v)
        if t in ('date', 'datetime'):
            d = db.parse_dt(v)
            if d is None:
                raise HTTPException(400, _tr('日期不对：{v}', v=v))
            return d.date() if t == 'date' else d
        if t == 'bool':
            return str(v).lower() in ('1', 'true', '是', 'yes')
        return str(v)
    if op == 'empty':
        return sa.or_(col.is_(None), col == '') if t == 'str' else col.is_(None)
    if op == 'not_empty':
        return sa.and_(col.isnot(None), col != '') if t == 'str' else col.isnot(None)
    if op in ('today', 'last_days', 'this_month'):
        s, e = _rel_range(op, val)
        if t == 'date':
            s, e = s.date() if s else None, e.date() if e else None
        c = col >= s
        return c & (col < e) if e else c
    if op == 'between':
        a, b = (val or [None, None]) if isinstance(val, list) else (str(val).split(',') + [None])[:2]
        conds = []
        if a not in (None, ''):
            conds.append(col >= cv(a))
        if b not in (None, ''):
            conds.append(col <= cv(b))
        return sa.and_(*conds) if conds else sa.true()
    if op == 'contains':
        return col.ilike(f'%{val}%')
    if op == 'in':
        vals = val if isinstance(val, list) else [x.strip() for x in str(val or '').replace('，', ',').split(',') if x.strip()]
        return col.in_([cv(v) for v in vals])
    v = cv(val)
    return {'=': col == v, '!=': col != v, '>': col > v, '>=': col >= v, '<': col < v, '<=': col <= v}[op]


def run(dataset_key: str, cfg: dict, user, limit: int | None = None) -> dict:
    """执行报表查询（只读）。cfg：{fields, filters, group: [{field, bucket}], measures: [{field, agg}], sort: [{field, dir}], limit}。"""
    ds = get_ds(dataset_key)
    cfg = cfg or {}
    limit = min(int(limit or cfg.get('limit') or 1000), MAX_ROWS)
    with db.engine.connect() as c:
        q = Q(ds, c)
        conds = [_cond(q, f) for f in cfg.get('filters') or [] if f.get('field')]
        groups = [g for g in cfg.get('group') or [] if g.get('field')]
        measures = [m for m in cfg.get('measures') or [] if m.get('agg')]
        cols, sel, gb = [], [], []
        if groups or measures:
            for g in groups:
                ex = q.col(g['field'])
                f = q.fmap[g['field']]
                if g.get('bucket') and f['t'] in ('date', 'datetime'):
                    ex = _bucket(ex, g['bucket'], c.dialect.name)
                    lab = _tr('{label}（{get}）', label=f['label'], get=BUCKETS.get(g['bucket'], ''))
                    t = 'str'
                else:
                    lab, t = f['label'], f['t']
                key = g['field'] + (('@' + g['bucket']) if g.get('bucket') else '')
                sel.append(ex.label(_safe(key)))
                gb.append(ex)
                cols.append({'key': key, 'label': lab, 't': t, 'enum': f.get('enum') if not g.get('bucket') else None, 'dim': True})
            for m in measures:
                agg = m['agg']
                if agg not in AGGS:
                    raise HTTPException(400, _tr('不认识的汇总 {agg}', agg=agg))
                if agg == 'count' and not m.get('field'):
                    ex = sa.func.count()
                    lab = m.get('label') or _tr('条数')
                else:
                    base = q.col(m['field'])
                    f = q.fmap[m['field']]
                    if agg in ('sum', 'avg') and f['t'] != 'num':
                        raise HTTPException(400, _tr('{label}不是数字，不能{v}', label=f['label'], v=AGGS[agg]))
                    ex = sa.func.count(sa.distinct(base)) if agg == 'count_distinct' else getattr(sa.func, agg)(base)
                    lab = m.get('label') or f"{f['label']}{AGGS[agg]}"
                key = f"{m.get('field') or '_'}_{agg}"
                sel.append(ex.label(_safe(key)))
                cols.append({'key': key, 'label': lab, 't': 'num', 'measure': True})
        else:
            keys = cfg.get('fields') or [f['key'] for f in ds.fields()][:12]
            for k in keys:
                f = q.fmap.get(k)
                if not f:
                    raise HTTPException(400, _tr('数据集「{label}」没有字段 {k}', label=ds.label, k=k))
                sel.append(q.col(k).label(_safe(k)))
                cols.append({'key': k, 'label': f['label'], 't': f['t'], 'enum': f.get('enum')})
        if not can_ds(user, ds, q.paths):
            miss = [BY_NAME[e].label for e in ds.entities(q.paths) if not perms.can(user, perms.entity_perm(e, 'read'))]
            raise HTTPException(403, _tr('没有权限查看：') + _tr('、').join(miss))
        stmt = sa.select(*sel).select_from(q.from_())
        sc = perms.scope_cond(user, ds.entity, q.base)
        if sc is not None:
            conds.append(sc)
        for x in conds:
            stmt = stmt.where(x)
        if gb:
            stmt = stmt.group_by(*gb)
        order = []
        labels = {x['key']: _safe(x['key']) for x in cols}
        for s in cfg.get('sort') or []:
            k = s.get('field')
            if k in labels:
                ce = sa.literal_column(labels[k])
                order.append(ce.desc() if s.get('dir') == 'desc' else ce.asc())
        if not order and gb:
            order = [sa.literal_column(labels[cols[0]['key']]).asc()]
        if not order and not gb:
            order = [q.base.c.id.desc()]
        stmt = stmt.order_by(*order).limit(limit + 1)
        if c.dialect.name == 'postgresql':
            c.execute(sa.text("SET LOCAL statement_timeout = '20s'"))
        rows = [list(r) for r in c.execute(stmt).all()]
        c.rollback()
    trunc = len(rows) > limit
    rows = rows[:limit]
    for col_i, col in enumerate(cols):          # 选项显示中文
        if col.get('enum'):
            for r in rows:
                r[col_i] = col['enum'].get(r[col_i], r[col_i])
    return {'columns': [{k: v for k, v in x.items() if k != 'enum'} for x in cols], 'rows': [[_out(v) for v in r] for r in rows], 'truncated': trunc,
            'dataset': ds.key, 'dataset_label': ds.label}


def _safe(k):
    return 'c_' + re.sub(r'[^a-zA-Z0-9_]', '_', k)[:50]


def _out(v):
    if isinstance(v, float):
        return round(v, 4)
    return db.jsonable(v)


# ───────────────────────── 保存的报表 ─────────────────────────
def T():
    from ..schema_impl import T as _T
    return _T['report_def']


def visible_cond(user):
    t = T()
    if perms.can(user, 'config.report.manage'):
        return sa.true()
    return sa.or_(t.c.owner == user['username'], t.c.shared_roles.isnot(None))


def can_see(user, r) -> bool:
    if perms.can(user, 'config.report.manage') or r['owner'] == user['username']:
        return True
    shared = r.get('shared_roles') or []
    return '*' in shared or bool(set(shared) & set(user.get('role_list') or []))


def can_edit(user, r) -> bool:
    return perms.can(user, 'config.report.manage') or (r['owner'] == user['username'] and perms.can(user, 'config.report.design'))


def load(rid, user, edit=False) -> dict:
    with db.engine.connect() as c:
        r = c.execute(sa.select(T()).where(T().c.id == rid)).mappings().first()
    if not r or not can_see(user, dict(r)):
        raise HTTPException(404, _tr('报表不存在'))
    r = dict(r)
    if edit and not can_edit(user, r):
        raise HTTPException(403, _tr('只能改自己的报表（或要"管理所有报表"权限）'))
    return r


def out(r, user=None) -> dict:
    d = {k: db.jsonable(v) for k, v in r.items()}
    ds = DATASETS.get(r['dataset'])
    d['dataset_label'] = ds.label if ds else r['dataset']
    if user is not None:
        d['can_edit'] = can_edit(user, r)
        d['pinned'] = f"report:{r['id']}" in _prefs_order(user)
    return d


def list_reports(user) -> list:
    t = T()
    with db.engine.connect() as c:
        rows = [dict(r) for r in c.execute(sa.select(t).where(visible_cond(user)).order_by(t.c.id)).mappings()]
    return [out(r, user) for r in rows if can_see(user, r)]


def save(rid, body, user) -> int:
    ds = get_ds(body.get('dataset') or (load(rid, user)['dataset'] if rid else None))
    name = str(body.get('name') or '').strip()
    if not name:
        raise HTTPException(400, _tr('请填写报表名称'))
    cfg = body.get('config') or {}
    run(ds.key, cfg, user, limit=1)                # 先试跑一行：字段 / 权限 / 筛选值都对
    chart = body.get('chart') or 'table'
    if chart not in CHARTS:
        raise HTTPException(400, _tr('图表类型只能是 ') + _tr('、').join(CHARTS.values()))
    shared = [r for r in (body.get('shared_roles') or []) if r == '*' or r in perms.roles()]
    if shared and not perms.can(user, 'config.report.manage') and not perms.can(user, 'config.report.design'):
        raise HTTPException(403, _tr('没有权限共享报表'))
    sched = _clean_schedule(body.get('schedule'))
    vals = {'name': name[:200], 'dataset': ds.key, 'config': cfg, 'chart': chart, 'shared_roles': shared or None, 'schedule': sched,
            'remark': str(body.get('remark') or '')[:400], 'updated_at': db.clock_wall()}
    t = T()
    with db.engine.begin() as c:
        if rid:
            cur = load(rid, user, edit=True)
            c.execute(t.update().where(t.c.id == rid).values(**vals))
            audit.audit(c, 'report_update', 'report_def', rid, diff=audit.diff_of(cur, {**cur, **vals}), user=user['username'])
        else:
            rid = c.execute(t.insert().values(**vals, owner=user['username'], created_at=db.clock_wall())).inserted_primary_key[0]
            audit.audit(c, 'report_create', 'report_def', rid, detail={'name': name, 'dataset': ds.key}, user=user['username'])
    return rid


def _clean_schedule(s):
    if not s or not s.get('enabled'):
        return None
    freq = s.get('freq') or 'daily'
    if freq not in ('daily', 'weekly', 'monthly'):
        raise HTTPException(400, _tr('定时只能 每天 / 每周 / 每月'))
    tm = str(s.get('time') or '08:00')
    if not re.match(r'^\d{1,2}:\d{2}$', tm):
        raise HTTPException(400, _tr('时间写成 08:00'))
    fmt = s.get('format') or 'xlsx'
    if fmt not in ('xlsx', 'pdf'):
        raise HTTPException(400, _tr('格式只能 xlsx / pdf'))
    return {'enabled': True, 'freq': freq, 'time': tm, 'weekday': int(s.get('weekday') or 1), 'day': max(1, min(28, int(s.get('day') or 1))), 'format': fmt,
            'to_roles': [r for r in s.get('to_roles') or [] if r in perms.roles()], 'to_users': [str(u) for u in s.get('to_users') or []][:20]}


def delete(rid, user):
    cur = load(rid, user, edit=True)
    with db.engine.begin() as c:
        c.execute(T().delete().where(T().c.id == rid))
        audit.audit(c, 'report_delete', 'report_def', rid, detail={'name': cur['name']}, user=user['username'])


def run_saved(rid, user) -> dict:
    r = load(rid, user)
    owner = _owner_user(r)
    res = run(r['dataset'], r['config'] or {}, user if user is not None else owner)
    return {**res, 'report': out(r, user), 'chart': r['chart']}


def _owner_user(r):
    from .. import auth
    return auth.user_by_name(r['owner']) or {'username': r['owner'], 'role_list': [], 'name': r['owner']}


# ───────────────────────── 导出 ─────────────────────────
def to_xlsx(title, res) -> bytes:
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = re.sub(r'[\[\]:*?/\\]', '-', title)[:31] or _tr('报表')
    ws.cell(1, 1, title).font = Font(bold=True, size=13)
    ws.cell(2, 1, _tr('数据集：{get}\u3000导出时间：{clock_wall:%Y-%m-%d %H:%M}', get=res.get("dataset_label", ""), clock_wall=db.clock_wall()).replace('　', '  '))
    for i, c in enumerate(res['columns'], 1):
        cell = ws.cell(4, i, c['label'])
        cell.font, cell.fill = Font(bold=True), PatternFill('solid', fgColor='EEF3FB')
        ws.column_dimensions[get_column_letter(i)].width = max(10, min(40, len(c['label']) * 2 + 4))
    for r_i, row in enumerate(res['rows'], 5):
        for i, v in enumerate(row, 1):
            ws.cell(r_i, i, v)
    ws.freeze_panes = 'A5'
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_pdf(title, res) -> bytes:
    """表格 PDF（A4 横向，宋体：PDF 标准中文字体 STSong-Light，不嵌入字体文件，阅读器自带）。"""
    W_, H_, M = 842, 595, 36
    cols = res['columns']
    rows = [[('' if v is None else str(v)) for v in r] for r in res['rows']]

    def tw(s, size):
        return sum(size * (0.5 if ord(ch) < 128 else 1.0) for ch in s)
    fs = 8.5
    widths = []
    for i, c in enumerate(cols):
        w = max([tw(c['label'], fs)] + [tw(r[i], fs) for r in rows[:200]]) + 8
        widths.append(min(w, 220))
    total = sum(widths) or 1
    if total > W_ - 2 * M:
        k = (W_ - 2 * M) / total
        widths = [w * k for w in widths]
    rh = 15
    per = int((H_ - 2 * M - 50) // rh) - 1
    pages = [rows[i:i + per] for i in range(0, max(1, len(rows)), per)] or [[]]

    def hexs(s):
        return '<' + s.encode('utf-16-be').hex().upper() + '>'

    def clip(s, w, size):
        out = ''
        for ch in s:
            if tw(out + ch, size) > w - 6:
                return out + '…'
            out += ch
        return out
    streams = []
    dsl = res.get('dataset_label', '')
    stamp = db.clock_wall().strftime('%Y-%m-%d %H:%M')
    for pi, chunk in enumerate(pages):
        sub = _tr('数据集：%s  导出：%s  第 %d/%d 页') % (dsl, stamp, pi + 1, len(pages))
        ops = ['BT', '/F1 13 Tf', f'{M} {H_ - M - 6} Td', f'{hexs(title)} Tj', 'ET',
               'BT', '/F1 8 Tf', f'{M} {H_ - M - 22} Td', f'{hexs(sub)} Tj', 'ET']
        y = H_ - M - 46
        ops += ['0.93 0.95 0.98 rg', f'{M} {y - 4} {sum(widths):.1f} {rh} re f', '0 0 0 rg']
        x = M
        for i, c in enumerate(cols):
            ops += ['BT', f'/F1 {fs} Tf', f'{x + 3:.1f} {y} Td', f'{hexs(clip(c["label"], widths[i], fs))} Tj', 'ET']
            x += widths[i]
        for r in chunk:
            y -= rh
            x = M
            for i, v in enumerate(r):
                ops += ['BT', f'/F1 {fs} Tf', f'{x + 3:.1f} {y} Td', f'{hexs(clip(v, widths[i], fs))} Tj', 'ET']
                x += widths[i]
            ops += ['0.85 0.87 0.9 RG', '0.4 w', f'{M} {y - 4} m {M + sum(widths):.1f} {y - 4} l S']
        streams.append('\n'.join(ops).encode('latin-1'))
    objs = []
    objs.append(b'<< /Type /Catalog /Pages 2 0 R >>')
    kids = ' '.join(f'{6 + 2 * i} 0 R' for i in range(len(streams)))
    objs.append(f'<< /Type /Pages /Kids [{kids}] /Count {len(streams)} >>'.encode())
    objs.append(b'<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H /DescendantFonts [4 0 R] >>')
    objs.append(b'<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light /CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 2 >> '
                b'/FontDescriptor 5 0 R /DW 1000 /W [1 [' + b' '.join([b'500'] * 95) + b']] >>')
    objs.append(b'<< /Type /FontDescriptor /FontName /STSong-Light /Flags 6 /FontBBox [-25 -254 1000 880] /ItalicAngle 0 /Ascent 880 /Descent -120 '
                b'/CapHeight 880 /StemV 93 >>')
    for i, st in enumerate(streams):
        objs.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W_} {H_}] /Resources << /Font << /F1 3 0 R >> >> /Contents {7 + 2 * i} 0 R >>'.encode())
        objs.append(f'<< /Length {len(st)} >>\nstream\n'.encode() + st + b'\nendstream')
    buf = io.BytesIO()
    buf.write(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(buf.tell())
        buf.write(f'{i} 0 obj\n'.encode() + o + b'\nendobj\n')
    x = buf.tell()
    buf.write(f'xref\n0 {len(objs) + 1}\n0000000000 65535 f \n'.encode())
    for o in offs:
        buf.write(f'{o:010d} 00000 n \n'.encode())
    buf.write(f'trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n'.encode())
    return buf.getvalue()


def export_dir():
    d = os.path.join(db.DATA, 'report_exports')
    os.makedirs(d, exist_ok=True)
    return d


def export(rid, fmt, user, kind='manual') -> dict:
    """导出并存档（report_export）；返回 {id, file, rows}。"""
    r = load(rid, user) if user is not None else _load_raw(rid)
    runner = user if user is not None else _owner_user(r)
    res = run(r['dataset'], r['config'] or {}, runner, limit=MAX_ROWS)
    data = to_pdf(r['name'], res) if fmt == 'pdf' else to_xlsx(r['name'], res)
    name = f"report{rid}-{db.clock_wall():%Y%m%d%H%M%S}.{fmt}"
    with open(os.path.join(export_dir(), name), 'wb') as f:
        f.write(data)
    from ..schema_impl import T as _T
    with db.engine.begin() as c:
        eid = c.execute(_T['report_export'].insert().values(report=rid, file=name, fmt=fmt, rows=len(res['rows']), kind=kind,
                                                            created_by=(user or {}).get('username') or 'system:schedule',
                                                            created_at=db.clock_wall())).inserted_primary_key[0]
    return {'id': eid, 'file': name, 'rows': len(res['rows']), 'data': data, 'name': r['name']}


def _load_raw(rid):
    with db.engine.connect() as c:
        r = c.execute(sa.select(T()).where(T().c.id == rid)).mappings().first()
    if not r:
        raise HTTPException(404, _tr('报表不存在'))
    return dict(r)


def export_file(eid, user) -> tuple:
    from ..schema_impl import T as _T
    with db.engine.connect() as c:
        e = c.execute(sa.select(_T['report_export']).where(_T['report_export'].c.id == eid)).mappings().first()
    if not e:
        raise HTTPException(404, _tr('导出文件不存在'))
    r = load(e['report'], user)
    p = os.path.join(export_dir(), e['file'])
    if not os.path.isfile(p):
        raise HTTPException(404, _tr('文件已清理'))
    with open(p, 'rb') as f:
        return f.read(), f"{r['name']}-{e['created_at']:%Y%m%d%H%M}.{e['fmt']}", e['fmt']


# ───────────────────────── 定时 ─────────────────────────
def last_due(s, now: datetime) -> datetime:
    """最近一次应运行的时间（≤ now）。"""
    hh, mm = (int(x) for x in s['time'].split(':'))
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if s['freq'] == 'daily':
        return t if t <= now else t - timedelta(days=1)
    if s['freq'] == 'weekly':
        wd = (s.get('weekday') or 1) - 1
        t = t - timedelta(days=(t.weekday() - wd) % 7)
        return t if t <= now else t - timedelta(days=7)
    d = s.get('day') or 1
    t = t.replace(day=d)
    if t > now:
        m = t.month - 1 or 12
        t = t.replace(year=t.year - (1 if m == 12 else 0), month=m)
    return t


def due_reports(now=None) -> list:
    now = now or db.clock_wall()
    with db.engine.connect() as c:
        rows = [dict(r) for r in c.execute(sa.select(T()).where(T().c.schedule.isnot(None))).mappings()]
    out = []
    for r in rows:
        s = r['schedule'] or {}
        if not s.get('enabled'):
            continue
        due = last_due(s, now)
        if (r['last_run_at'] or datetime.min) < due and (r['created_at'] or datetime.min) <= due:
            out.append(r)
    return out


def run_schedule(r) -> dict:
    s = r['schedule']
    e = export(r['id'], s.get('format') or 'xlsx', None, kind='scheduled')
    with db.engine.begin() as c:
        c.execute(T().update().where(T().c.id == r['id']).values(last_run_at=db.clock_wall()))
        targets = [('to_role', x) for x in s.get('to_roles') or []] + [('to_user', x) for x in s.get('to_users') or []] or [('to_user', r['owner'])]
        for k, v in targets:
            db.insert(c, 'notice', {'title': _tr('定时报表：{name}（{rows} 行）', name=r['name'], rows=e['rows'])[:190], 'content': _tr('{upper} 文件已生成，点开下载。', upper=s['format'].upper()),
                                    k: v, 'channel': 'inbox', 'level': 'info', 'link': f"/impl/reports?export={e['id']}", 'source': '报表设计器',
                                    'read': False, 'time': db.clock.now()}, raw=True)
        audit.audit(c, 'report_schedule', 'report_def', r['id'], detail={'export': e['id'], 'rows': e['rows'], 'format': s['format']}, user='system:schedule',
                    source='system')
    return e


async def _loop():
    await asyncio.sleep(20)
    while True:
        try:
            for r in await asyncio.to_thread(due_reports):
                await asyncio.to_thread(run_schedule, r)
        except Exception:
            log.exception(_tr('定时报表失败'))
        await asyncio.sleep(60)


def install(app):
    @app.on_event('startup')
    async def _start():
        asyncio.create_task(_loop())


# ───────────────────────── 钉到工作台（H1 的工作台布局偏好 /api/home/prefs） ─────────────────────────
def _prefs_order(user) -> list:
    from ..services import home
    return list((home._read_prefs().get(user['username']) or {}).get('order') or [])


def pin(rid, user, on=True):
    load(rid, user)
    from ..services import home
    wid = f'report:{rid}'
    with home._lock:
        d = home._read_prefs()
        p = d.get(user['username']) or {}
        order = [x for x in (p.get('order') or []) if x != wid]
        if on:
            order.append(wid)
        p['order'] = order
        p['hidden'] = [x for x in (p.get('hidden') or []) if x != wid]
        d[user['username']] = p
        home._write_prefs(d)
    return {'ok': True, 'pinned': on}


def widgets(user) -> list:
    ids = [int(x.split(':')[1]) for x in _prefs_order(user) if str(x).startswith('report:') and x.split(':')[1].isdigit()]
    if not ids:
        return []
    with db.engine.connect() as c:
        rows = {r['id']: dict(r) for r in c.execute(sa.select(T()).where(T().c.id.in_(ids))).mappings()}
    return [{'id': i, 'name': rows[i]['name'], 'chart': rows[i]['chart']} for i in ids if i in rows and can_see(user, rows[i])]


_ = json
