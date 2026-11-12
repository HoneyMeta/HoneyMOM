"""自定义字段：给任何实体加字段，不改代码、不改表结构。说明见 docs/dev/config.md「自定义字段」。

存储：实体表上一列 ext（JSON），自定义字段的值以 {"x_颜色": …} 存在里面；字段定义在 custom_field 表。
  - ext 列不写进 schema.py 的表定义（老迁移里 SELECT * 时它还不存在），而是某个实体第一次定义自定义字段时补上（ALTER TABLE … ADD COLUMN ext，
    幂等）；读写都用显式的列表达式（ext_col），所以只有用到自定义字段的表才有这一列，迁移顺序无关，别的助手新建的实体也能直接加字段。
  - 为什么不用 EAV（一值一行）：列表、筛选、排序、导出、导入、问数都是"整行"读写，JSON 列一次读出、和原字段一起进同一个 UPDATE（同一事务、
    同一条审计），SQLite / PostgreSQL 都原生支持取值函数；EAV 每个字段一次 JOIN，筛选两个字段就要两次自连接，问数 SQL 也难写。
  - 要筛选 / 排序的字段（filterable）建表达式索引：SQLite json_extract(ext,'$.x_k')，PostgreSQL (ext->>'x_k')，数字字段再 CAST；
    查询用的表达式和建索引用的表达式是同一个函数编译出来的（expr()），保证命中索引。
  - 字段键固定以 x_ 开头（x_ + 小写字母数字下划线），不会和系统字段撞名；接口里和原字段平铺在一起（行里直接有 x_color）。
类型：text 文本 / number 数字 / date 日期 / enum 选项 / ref 引用（存对方 id，显示对方编码名称）/ file 文件（存 {name, token, size}）。
"""
from ..i18n import _tr
import json
import re
import threading
import time
from datetime import date, datetime

import sqlalchemy as sa

from .. import db

KEY_RE = re.compile(r'^x_[a-z][a-z0-9_]{0,38}$')
FTYPES = {'text': '文本', 'number': '数字', 'date': '日期', 'enum': '选项', 'ref': '引用', 'file': '文件'}
SPEC_T = {'text': 'str', 'number': 'float', 'date': 'date', 'enum': 'enum', 'ref': 'ref', 'file': 'file'}
_cache: dict = {'at': 0.0, 'by': {}}
_lock = threading.Lock()
TTL = 5.0                       # 多进程：别的进程改了字段定义，最多 5 秒后生效


def table():
    from ..schema_impl import T
    return T['custom_field']


def invalidate():
    _cache['at'] = 0.0


def _load():
    t = table()
    by: dict = {}
    try:
        with db.engine.connect() as c:
            for r in c.execute(sa.select(t).where(t.c.active.is_(True)).order_by(t.c.entity, t.c.seq, t.c.id)).mappings():
                by.setdefault(r['entity'], []).append(dict(r))
    except Exception:            # 迁移之前（老库还没有 custom_field 表）
        return {}
    return by


def all_defs() -> dict:
    with _lock:
        if time.time() - _cache['at'] > TTL:
            _cache['by'], _cache['at'] = _load(), time.time()
        return _cache['by']


def fields(entity: str) -> list[dict]:
    """实体当前启用的自定义字段定义（按顺序）。"""
    return all_defs().get(entity, [])


def field(entity: str, key: str) -> dict | None:
    return next((d for d in fields(entity) if d['key'] == key), None)


def spec_of(d: dict) -> dict:
    """自定义字段 → 和 schema.F 一样形状的字段说明（/api/meta、导入列、报表数据集都用它）。"""
    o = {'name': d['key'], 'label': d['label'], 't': SPEC_T.get(d['ftype'], 'str'), 'cf': True, 'list': bool(d.get('in_list')),
         'filter': bool(d.get('filterable')), 'ftype': d['ftype']}
    if d.get('required'):
        o['req'] = True
    if d['ftype'] == 'enum':
        o['enum'] = enum_of(d)
    if d['ftype'] == 'ref' and d.get('ref'):
        o['ref'] = d['ref']
    if d.get('help'):
        o['hint'] = d['help']
    return o


def enum_of(d) -> dict:
    """选项：存成 [{"key":"red","label":"红"}…] 或 {"red":"红"} 或 ["红","绿"]（键 = 名）都认。"""
    op = d.get('options') or {}
    if isinstance(op, dict):
        return {str(k): str(v) for k, v in op.items()}
    out = {}
    for x in op:
        if isinstance(x, dict):
            out[str(x.get('key') or x.get('label'))] = str(x.get('label') or x.get('key'))
        else:
            out[str(x)] = str(x)
    return out


# ───────────────────────── 值 ─────────────────────────
def coerce_value(d: dict, v):
    """按字段类型转换（导入、接口写入都走这里）。错了抛 ValueError。"""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    ft = d['ftype']
    try:
        if ft == 'number':
            return float(str(v).replace(',', '').strip()) if not isinstance(v, (int, float)) or isinstance(v, bool) else float(v)
        if ft == 'date':
            if isinstance(v, datetime):
                return v.date().isoformat()
            if isinstance(v, date):
                return v.isoformat()
            dt = db.parse_dt(str(v).replace('/', '-').replace('.', '-'))
            if not dt:
                raise ValueError
            return dt.date().isoformat()
        if ft == 'enum':
            em = enum_of(d)
            s = str(v).strip()
            if s in em:
                return s
            back = {lab: k for k, lab in em.items()}
            if s in back:
                return back[s]
            raise ValueError
        if ft == 'ref':
            return int(float(v))
        if ft == 'file':
            if isinstance(v, dict) and v.get('token'):
                return {'name': str(v.get('name') or '')[:200], 'token': str(v['token'])[:80], 'size': int(v.get('size') or 0)}
            if isinstance(v, str) and v.startswith('{'):
                return coerce_value(d, json.loads(v))
            raise ValueError
        return str(v).strip()[:2000]
    except (TypeError, ValueError):
        if ft == 'enum':
            raise ValueError(_tr('{label} 只能是：{join}（填的是 {v}）', label=d['label'], join=_tr('、').join(enum_of(d).values()), v=v))
        raise ValueError(_tr('{label} 格式不对：{v}', label=d['label'], v=v))


def pack(entity: str, row: dict) -> dict | None:
    """db.coerce 的钩子：行里的 x_… → ext 字典。只认启用的字段（停用的字段，编辑表单回传的旧值忽略，库里原值保留）。"""
    keys = [k for k in row if isinstance(k, str) and k.startswith('x_') and not k.endswith('__label')]
    ext = row.get('ext') if isinstance(row.get('ext'), dict) else None
    if not keys and ext is None:
        return None
    defs = {d['key']: d for d in fields(entity)}
    out = {}
    for k, v in list((ext or {}).items()) + [(k, row[k]) for k in keys]:
        d = defs.get(k)
        if d:
            out[k] = coerce_value(d, v)
    return out if out or ext is not None else None


def flat(r: dict) -> dict:
    """接口输出：ext 里的值平铺到行上（x_color: …），去掉 ext。"""
    x = r.get('ext')
    base = {k: v for k, v in r.items() if k != 'ext'}
    if isinstance(x, str):
        try:
            x = json.loads(x)
        except ValueError:
            x = None
    if isinstance(x, dict):
        base.update(x)
    return base


def get(row: dict, key: str):
    x = row.get('ext')
    if isinstance(x, dict) and key in x:
        return x.get(key)
    return row.get(key)


# ───────────────────────── 查询表达式 / 索引 ─────────────────────────
def ext_col(t, qualified=True):
    """ext 列（不在表定义里）：t 可以是表或别名。"""
    return sa.literal_column(f'"{t.name}".ext' if qualified else 'ext', type_=sa.JSON)


_has_ext: dict = {}          # (url, 实体) -> bool


def has_ext(conn, entity) -> bool:
    k = (str(conn.engine.url), entity)
    if k not in _has_ext:
        _has_ext[k] = any(c['name'] == 'ext' for c in sa.inspect(conn).get_columns(entity))
    return _has_ext[k]


def ensure_column(conn, entity):
    """实体表补 ext 列（已有跳过）。定义第一个自定义字段时调用。"""
    if has_ext(conn, entity):
        return False
    typ = 'json' if conn.dialect.name == 'postgresql' else 'JSON'
    conn.execute(sa.text(f'ALTER TABLE "{entity}" ADD COLUMN {"IF NOT EXISTS " if conn.dialect.name == "postgresql" else ""}ext {typ}'))
    _has_ext[(str(conn.engine.url), entity)] = True
    return True


def read_many(conn, entity, ids) -> dict:
    """{id: {x_…: 值}}（表没有 ext 列返回空）。"""
    from ..schema import TABLES
    ids = [int(i) for i in ids if i is not None]
    if not ids or not has_ext(conn, entity):
        return {}
    t = TABLES[entity]
    out = {}
    for rid, x in conn.execute(sa.select(t.c.id, ext_col(t)).select_from(t).where(t.c.id.in_(ids))).all():
        if isinstance(x, str):
            try:
                x = json.loads(x)
            except ValueError:
                x = None
        out[rid] = x if isinstance(x, dict) else {}
    return out


def read_one(conn, entity, rid) -> dict:
    return read_many(conn, entity, [rid]).get(rid, {})


def write(conn, entity, rid, xt: dict) -> dict:
    """db.insert / db.update 的钩子：把 xt 合并进这一行的 ext；返回改前的值。"""
    ensure_column(conn, entity)
    old = read_one(conn, entity, rid)
    new = {**old, **xt}
    conn.execute(sa.text(f'UPDATE "{entity}" SET ext = :x WHERE id = :id').bindparams(sa.bindparam('x', type_=sa.JSON)), {'x': new, 'id': rid})
    return old


def expr(t: sa.Table, d: dict, dialect: str | None = None, qualified=True):
    """自定义字段的取值表达式（筛选、排序、报表分组用；建索引也用它，保证一致）。"""
    k = d['key']
    assert KEY_RE.match(k)
    dialect = dialect or db.engine.dialect.name
    col = ext_col(t, qualified)
    if dialect == 'postgresql':
        base = col.op('->>', return_type=sa.String)(sa.literal_column(f"'{k}'"))
    else:
        base = sa.func.json_extract(col, sa.literal_column(f"'$.{k}'"), type_=sa.String)
    if d['ftype'] == 'number':
        return sa.cast(base, sa.Float)
    if d['ftype'] == 'ref':
        return sa.cast(base, sa.Integer)
    return base


def index_name(entity, key):
    return f'ix_cf_{entity}_{key}'[:60]


def ensure_index(conn, d: dict):
    """可筛选字段建表达式索引（已存在跳过）；不再可筛选时删掉。"""
    from ..schema import TABLES
    t = TABLES[d['entity']]
    name = index_name(d['entity'], d['key'])
    if not d.get('filterable') or not d.get('active', True):
        conn.execute(sa.text(f'DROP INDEX IF EXISTS "{name}"'))
        return
    ensure_column(conn, d['entity'])
    body = str(expr(t, d, conn.dialect.name, qualified=False).compile(dialect=conn.dialect, compile_kwargs={'literal_binds': True}))
    conn.execute(sa.text(f'CREATE INDEX IF NOT EXISTS "{name}" ON "{d["entity"]}" (({body}))'))


def filter_cond(entity, t, name, op, value):
    """crud.build_query 的钩子：f_x_color=red / f_x_qty__from=10。返回条件；不是自定义字段返回 None。"""
    d = field(entity, name)
    if not d:
        return None
    col = expr(t, d)
    if d['ftype'] == 'number':
        v = float(value)
    elif d['ftype'] == 'ref':
        v = int(float(value))
    elif d['ftype'] == 'enum':
        v = coerce_value(d, value)
    else:
        v = str(value)
    if op == 'ge':
        return col >= v
    if op == 'lt':
        return col < v
    if d['ftype'] == 'text':
        return col.ilike(f'%{v}%')
    return col == v


def sort_expr(entity, t, name):
    d = field(entity, name)
    return expr(t, d) if d else None


def search_conds(entity, t, kw):
    """关键词搜索也查可筛选的文本字段。"""
    return [expr(t, d).ilike(f'%{kw}%') for d in fields(entity) if d['ftype'] == 'text' and d.get('filterable')]


def attach_labels(conn, entity, items):
    """crud 的 LABEL_HOOKS：自定义字段的值平铺到行上（x_color: …），引用型补 x_…__label。"""
    from ..crud import label_of
    from ..schema import TABLES
    defs = fields(entity)
    if not defs or not items:
        return
    vals = read_many(conn, entity, [r.get('id') for r in items])
    for r in items:
        x = vals.get(r.get('id')) or {}
        for d in defs:
            r[d['key']] = x.get(d['key'])
    for d in defs:
        if d['ftype'] != 'ref' or not d.get('ref') or d['ref'] not in TABLES:
            continue
        ids = {get(r, d['key']) for r in items} - {None}
        ids = {int(x) for x in ids if str(x).lstrip('-').isdigit()}
        if not ids:
            continue
        rt = TABLES[d['ref']]
        lab = {r['id']: label_of(dict(r)) for r in conn.execute(sa.select(rt).where(rt.c.id.in_(ids))).mappings()}
        for r in items:
            v = get(r, d['key'])
            if v is not None:
                r[d['key'] + '__label'] = lab.get(int(v), f'#{v}')


def export_cols(entity):
    """导出 CSV 的额外列：[(键, 中文名, 取值函数)]。"""
    out = []
    for d in fields(entity):
        def val(r, d=d):
            v = r.get(d['key'])
            if v is None:
                return ''
            if d['ftype'] == 'ref':
                return r.get(d['key'] + '__label') or v
            if d['ftype'] == 'enum':
                return enum_of(d).get(v, v)
            if d['ftype'] == 'file':
                return v.get('name') if isinstance(v, dict) else v
            return v
        out.append((d['key'], d['label'], val))
    return out


def schema_hint(entity) -> str:
    """问数助手的表说明：ext 里的自定义字段怎么取。"""
    fs = fields(entity)
    if not fs:
        return ''
    parts = []
    for d in fs:
        how = f"json_extract({entity}.ext,'$.{d['key']}')"
        if d['ftype'] == 'number':
            how = f'CAST({how} AS REAL)'
        extra = ('=' + '/'.join(f'{k}:{v}' for k, v in list(enum_of(d).items())[:8])) if d['ftype'] == 'enum' else ('→' + d['ref'] if d['ftype'] == 'ref' and d.get('ref') else '')
        parts.append(_tr('{key}({label}{extra}，取值 {how})', key=d['key'], label=d['label'], extra=extra, how=how))
    return _tr(', 自定义字段 ') + ', '.join(parts)


# ───────────────────────── 定义的增删改 ─────────────────────────
def validate_def(body: dict, existing: dict | None = None) -> dict:
    from ..schema import BY_NAME, TABLES
    ent = (existing or body).get('entity')
    if ent not in BY_NAME:
        raise ValueError(_tr('没有实体 {ent}', ent=ent))
    key = (existing or {}).get('key') or str(body.get('key') or '').strip().lower()
    if not key.startswith('x_'):
        key = 'x_' + key
    if not KEY_RE.match(key):
        raise ValueError(_tr('字段键只能用小写字母、数字、下划线，以字母开头（系统自动加 x_ 前缀），最长 40'))
    if key in TABLES[ent].c:
        raise ValueError(_tr('{key} 和系统字段重名', key=key))
    ft = (existing or {}).get('ftype') or body.get('ftype')
    if ft not in FTYPES:
        raise ValueError(_tr('类型只能是：') + _tr('、').join(f'{k}({v})' for k, v in FTYPES.items()))
    label = str(body.get('label') if body.get('label') is not None else (existing or {}).get('label') or '').strip()
    if not label:
        raise ValueError(_tr('请填写字段名称'))
    out = {'entity': ent, 'key': key, 'ftype': ft, 'label': label[:100]}
    for k in ('required', 'in_list', 'filterable', 'active'):
        if k in body:
            out[k] = bool(body[k])
        elif existing is None:
            out[k] = k in ('in_list', 'active')
    if 'seq' in body or existing is None:
        out['seq'] = int(body.get('seq') or 0)
    if 'help' in body:
        out['help'] = str(body.get('help') or '')[:300]
    if ft == 'enum':
        opts = body.get('options', (existing or {}).get('options'))
        if isinstance(opts, str):
            opts = [x.strip() for x in re.split(r'[,，\n]', opts) if x.strip()]
        if not opts:
            raise ValueError(_tr('选项型字段要填选项'))
        out['options'] = opts
    if ft == 'ref':
        ref = body.get('ref', (existing or {}).get('ref'))
        if ref not in BY_NAME:
            raise ValueError(_tr('引用型字段要选引用的实体'))
        out['ref'] = ref
    return out


def install():
    """把钩子挂到 db / crud / schema（app 启动时调用一次）。"""
    from .. import crud
    from .. import schema as sch
    db.EXT_COERCE = pack
    db.EXT_WRITE = write
    crud.EXT_FILTER = filter_cond
    crud.EXT_SORT = sort_expr
    crud.EXT_SEARCH = search_conds
    crud.EXT_COLS = export_cols
    if attach_labels not in crud.LABEL_HOOKS:
        crud.LABEL_HOOKS.append(attach_labels)
    if _spec not in sch.SPEC_HOOKS:
        sch.SPEC_HOOKS.append(_spec)


def _spec(out):
    for e in out:
        for d in fields(e['name']):
            e['fields'].append(spec_of(d))


install()          # 导入本模块就挂上钩子（命令行 / 测试 / 行业模板里不经过 app 也生效）
