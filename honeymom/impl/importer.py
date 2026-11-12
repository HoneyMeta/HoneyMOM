"""通用导入框架（Excel .xlsx / CSV）。别的模块怎么登记导入器见 docs/dev/import.md。

流程：模板（说明页 + 下拉 + 示例行）→ 上传 → 对列（按表头自动；AI 功能"实施助手"打开时 AI 建议）→ 校验（类型、必填、按编码找引用、
文件内重复、和库里已有的、业务规则）→ 预览（逐行错误）→ 演练（真写一遍再回滚：新增 / 更新 / 跳过各多少）→ 确认（一个事务，
任何一行失败整批回滚）→ 错误报告（.xlsx，带"错误"列）。导入写库的审计来源是"导入"（context.source('import')），每次导入另记一条汇总。

    from honeymom.impl import importer as imp
    imp.register(imp.entity_importer('customer', keys=('code',), group='业务伙伴', order=60))     # 按实体定义自动出列
    imp.register(imp.Importer(key='opening_ar', label='期初应收', columns=[imp.Col('customer', '客户', 'ref', ref='customer', req=True), …],
                              keys=('customer', 'doc_no'), write=my_write, check_row=my_check, perms=('sales.ar.create',)))
    imp.run('customer', [{'code': 'C001', 'name': '北辰重卡'}], user=user)          # 程序里直接导（行业模板、测试用）
"""
from __future__ import annotations

from ..i18n import _tr
import csv
import io
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

import sqlalchemy as sa
from fastapi import HTTPException

from .. import audit, context, db, perms
from ..schema import BY_NAME, TABLES

MAX_ROWS = 50000
MODES = {'upsert': '新增或更新', 'create': '只新增（已有的跳过）', 'update': '只更新（没有的报错）'}
ACTIONS_CN = {'create': '新增', 'update': '更新', 'skip': '跳过', 'error': '错误', 'empty': '空行'}
TYPES_CN = {'str': '文本', 'text': '长文本', 'int': '整数', 'float': '数字', 'bool': '是/否', 'date': '日期', 'datetime': '日期时间',
            'enum': '选项', 'ref': '编码', 'json': '参数', 'file': '文件'}


@dataclass
class Col:
    key: str
    label: str
    t: str = 'str'
    req: bool = False
    enum: dict | None = None
    ref: str | None = None              # 引用的实体：按 ref_key（缺省 编码 / 账号 / 名称）找 id
    ref_key: str | None = None
    aliases: tuple = ()                 # 别的表头叫法（自动对列用）
    hint: str = ''
    example: Any = None
    cf: bool = False                    # 自定义字段
    ftype: str | None = None            # 自定义字段的类型（cf.FTYPES）

    def out(self):
        d = {k: v for k, v in self.__dict__.items() if v not in (None, '', (), False)}
        d['aliases'] = list(self.aliases)
        return d


@dataclass
class Importer:
    key: str
    label: str
    entity: str | None = None                   # 目标实体（缺省写法：按 keys 找已有 → db.update / db.insert）
    columns: list | Callable | None = None      # [Col] 或 fn() -> [Col]；缺省按实体字段 + 自定义字段
    keys: tuple = ('code',)                     # 唯一键（"已有"的判断、文件内重复）
    group: str = '主数据'
    order: int = 100                            # 开局导入的顺序（小的先导：单位 → 物料 → BOM → 工艺）
    doc: str = ''                               # 说明页的一段话
    rules: list = field(default_factory=list)   # 说明页的规则（中文短句）
    examples: list = field(default_factory=list)  # 示例行 [{列键: 值}]，第一行进模板
    perms: tuple | None = None                  # 需要的权限点（缺省 = 实体的 create + update）
    prepare: Callable | None = None             # fn(ctx, records) -> records：校验前整理原始行（如多级 BOM 按"层级"推出父件）
    check_row: Callable | None = None           # fn(ctx, values, row) -> 错误文本 | [错误] | None
    check_all: Callable | None = None           # fn(ctx, rows) -> None（往 row.errors 里加跨行错误，如 BOM 循环）
    find: Callable | None = None                # fn(ctx, values) -> 已有行 | None
    write: Callable | None = None               # fn(ctx, values, existing) -> 'create' | 'update' | 'skip'
    count: Callable | None = None               # fn(conn) -> 已有多少条（界面显示）
    depends: tuple = ()                         # 一般先导哪些（界面提示）
    exclude: tuple = ()                         # 自动出列时不要的字段
    module: str | None = None                   # 可选模块（行业模板启用后才显示，如 foundry）

    def cols(self) -> list[Col]:
        cs = self.columns() if callable(self.columns) else list(self.columns or [])
        if not cs and self.entity:
            cs = entity_columns(self.entity, exclude=self.exclude)
        elif self.entity and not any(c.cf for c in cs):
            cs = cs + cf_columns(self.entity)
        return cs

    def need_perms(self):
        if self.perms is not None:
            return list(self.perms)
        return [perms.entity_perm(self.entity, 'create'), perms.entity_perm(self.entity, 'update')] if self.entity else []

    def out(self, conn=None):
        cs = self.cols()
        d = {'key': self.key, 'label': self.label, 'entity': self.entity, 'group': self.group, 'order': self.order, 'doc': self.doc,
             'rules': self.rules, 'keys': list(self.keys), 'depends': list(self.depends), 'module': self.module, 'perms': self.need_perms(),
             'columns': [c.out() for c in cs], 'key_labels': [next((c.label for c in cs if c.key == k), k) for k in self.keys]}
        if conn is not None:
            try:
                d['existing'] = self.count(conn) if self.count else (conn.execute(sa.select(sa.func.count()).select_from(TABLES[self.entity])).scalar()
                                                                     if self.entity else None)
            except Exception:
                d['existing'] = None
        return d


REGISTRY: dict[str, Importer] = {}


def register(imp: Importer) -> Importer:
    """登记一个导入器（模块导入时调用；同 key 再登记 = 替换，方便别的模块扩展列）。"""
    REGISTRY[imp.key] = imp
    return imp


def get(key: str) -> Importer:
    _load_builtin()
    if key not in REGISTRY:
        raise HTTPException(404, _tr('没有导入器 {key}', key=key))
    return REGISTRY[key]


def all_importers() -> list[Importer]:
    _load_builtin()
    return sorted(REGISTRY.values(), key=lambda x: (x.order, x.key))


_loaded = False


def _load_builtin():
    global _loaded
    if not _loaded:
        _loaded = True
        from . import importers  # noqa: F401  内置导入器


# ───────────────────────── 列 ─────────────────────────
def ref_key_of(entity: str) -> str:
    names = {f.name for f in BY_NAME[entity].fields}
    for k in ('code', 'username', 'sn', 'name', 'title'):
        if k in names:
            return k
    return 'id'


def entity_columns(entity: str, exclude=(), only=None) -> list[Col]:
    """按实体定义出列：跳过只读 / 自动编号 / JSON 字段（和 exclude），引用字段按编码填；后面接上自定义字段。"""
    e = BY_NAME[entity]
    out = []
    for f in e.fields:
        if (only and f.name not in only) or f.name in exclude or f.ro or f.t == 'json' or f.name in ('password_hash', 'ext'):
            continue
        if f.auto and not only:
            continue
        label = f.label
        hint = f.hint
        if f.t == 'ref':
            rk = ref_key_of(f.ref)
            hint = hint or _tr('填{label}的{v}', label=BY_NAME[f.ref].label, v=_key_cn(f.ref, rk))
        out.append(Col(f.name, label, f.t, req=f.req, enum=f.enum, ref=f.ref, hint=hint, example=None,
                       aliases=_aliases(entity, f)))
    return out + cf_columns(entity)


def _key_cn(entity, k):
    f = next((x for x in BY_NAME[entity].fields if x.name == k), None)
    return f.label if f else k


def _aliases(entity, f):
    al = [f.name]
    if f.name == 'code':
        al += [BY_NAME[entity].label + _tr('编码'), BY_NAME[entity].label + _tr('编号'), BY_NAME[entity].label + _tr('代码'), _tr('编码'), _tr('编号'), _tr('代码')]
        if entity == 'material':
            al += [_tr('料号'), _tr('品号'), _tr('物料号'), _tr('存货编码'), _tr('物料代码')]
    if f.name == 'name':
        al += [BY_NAME[entity].label + _tr('名称'), BY_NAME[entity].label + _tr('名'), _tr('名称')]
        if entity == 'material':
            al += [_tr('品名'), _tr('存货名称'), _tr('物品名称')]
    if f.t == 'ref':
        lab = BY_NAME[f.ref].label
        al += [f.label + _tr('编码'), f.label + _tr('编号'), f.label + _tr('代码'), lab, lab + _tr('编码')]
    return tuple(al)


def cf_columns(entity: str) -> list[Col]:
    from . import cf
    out = []
    for d in cf.fields(entity):
        s = cf.spec_of(d)
        out.append(Col(d['key'], d['label'], s['t'], req=bool(d.get('required')), enum=s.get('enum'), ref=s.get('ref'), hint=d.get('help') or '',
                       cf=True, ftype=d['ftype'], aliases=(d['key'],)))
    return out


# ───────────────────────── 解析文件 ─────────────────────────
def parse_file(name: str, data: bytes, sheet: str | None = None) -> dict:
    """→ {'sheets': [名…], 'sheet': 用的那页, 'headers': [...], 'rows': [[...]...]}（值是原样：文本 / 数字 / 日期）。"""
    low = (name or '').lower()
    if low.endswith('.csv') or low.endswith('.txt'):
        text = None
        for enc in ('utf-8-sig', 'gb18030'):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError(_tr('CSV 编码认不出来（请另存为 UTF-8）'))
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        grid = [r for r in csv.reader(io.StringIO(text), dialect)]
        return _grid_out(grid, ['CSV'], 'CSV')
    if not low.endswith(('.xlsx', '.xlsm')):
        raise ValueError(_tr('只支持 .xlsx 和 .csv（.xls 请先另存为 .xlsx）'))
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as ex:
        raise ValueError(_tr('打不开这个 Excel：{ex}', ex=ex))
    names = [ws.title for ws in wb.worksheets]
    pick = None
    if sheet and sheet in names:
        pick = wb[sheet]
    else:
        for ws in wb.worksheets:
            if ws.title in ('说明', '_lists', '填写说明') or ws.sheet_state != 'visible':
                continue
            pick = ws
            break
        pick = pick or wb.worksheets[0]
    grid = []
    for row in pick.iter_rows(values_only=True):
        grid.append(list(row))
        if len(grid) > MAX_ROWS + 20:
            break
    wb.close()
    return _grid_out(grid, names, pick.title)


def _grid_out(grid, names, sheet):
    grid = [[_clean(v) for v in r] for r in grid]
    # 表头行：前 10 行里非空单元格最多的一行（模板第一行就是表头；有的客户文件上面有标题行）
    best, bi = -1, 0
    for i, r in enumerate(grid[:10]):
        n = sum(1 for v in r if v not in (None, ''))
        if n > best:
            best, bi = n, i
    if best <= 0:
        return {'sheets': names, 'sheet': sheet, 'headers': [], 'rows': [], 'header_row': 1}
    headers = [str(v).strip() if v not in (None, '') else '' for v in grid[bi]]
    while headers and not headers[-1]:
        headers.pop()
    width = len(headers)
    rows = []
    for r in grid[bi + 1:]:
        r = (r + [None] * width)[:width]
        rows.append(r)
    while rows and all(v in (None, '') for v in rows[-1]):
        rows.pop()
    if len(rows) > MAX_ROWS:
        raise ValueError(_tr('一次最多导入 {MAX_ROWS} 行（这个文件 {len} 行），请分批', MAX_ROWS=MAX_ROWS, len=len(rows)))
    return {'sheets': names, 'sheet': sheet, 'headers': headers, 'rows': rows, 'header_row': bi + 1}


def _clean(v):
    if isinstance(v, str):
        v = v.strip()
        return v
    return v


def norm_header(h: str) -> str:
    s = unicodedata.normalize('NFKC', str(h or '')).strip().lower()
    s = re.sub(r'[\*＊]', '', s)
    s = re.sub(r'[\(（\[【].*?[\)）\]】]', '', s)
    return re.sub(r'[\s_\-:：/]', '', s)


def auto_map(imp: Importer, headers: list[str]) -> dict:
    """{表头序号(str): 列键}：按中文名 / 键名 / 别名精确匹配，再按相似度（≥0.75）匹配剩下的。"""
    cols = imp.cols()
    names = {}
    for c in cols:
        for n in (c.label, c.key) + tuple(c.aliases):
            names.setdefault(norm_header(n), c.key)
    for c in cols:              # "单价 元""标准节拍 s"：去掉单位的前半截也认
        if ' ' in c.label.strip():
            names.setdefault(norm_header(c.label.strip().rsplit(' ', 1)[0]), c.key)
    out, used = {}, set()
    for i, h in enumerate(headers):
        k = names.get(norm_header(h))
        if k and k not in used:
            out[str(i)] = k
            used.add(k)
    from ..services.ai_demo import similarity
    for i, h in enumerate(headers):
        if str(i) in out or not h:
            continue
        nh = norm_header(h)
        best, score = None, 0.0
        for n, k in names.items():
            if k in used:
                continue
            s = similarity(nh, n)
            if s > score:
                best, score = k, s
        if best and score >= 0.75:
            out[str(i)] = best
            used.add(best)
    return out


# ───────────────────────── 值转换 ─────────────────────────
_TRUE = {'1', 'true', 'yes', 'y', 'on', '是', '√', '✓', '对', '有', '需要', '启用'}
_FALSE = {'0', 'false', 'no', 'n', 'off', '否', '×', '✗', '不', '无', '不需要', '停用'}


def _text(v) -> str:
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S') if (v.hour or v.minute or v.second) else v.strftime('%Y-%m-%d')
    if isinstance(v, date):
        return v.isoformat()
    return str(v).strip()


def _num(v):
    if isinstance(v, bool):
        raise ValueError
    if isinstance(v, (int, float)):
        return float(v)
    s = unicodedata.normalize('NFKC', str(v)).replace(',', '').replace(' ', '').rstrip('%')
    return float(s)


def _excel_date(v):
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, (int, float)) and 20000 < float(v) < 80000:          # Excel 序列号
        return datetime(1899, 12, 30) + timedelta(days=float(v))
    s = unicodedata.normalize('NFKC', str(v)).strip().replace('/', '-').replace('.', '-')
    s = re.sub(r'(\d{4})年(\d{1,2})月(\d{1,2})日?', r'\1-\2-\3', s)
    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})(.*)$', s)
    if m:
        s = f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}{m.group(4)}'
    d = db.parse_dt(s)
    if d is None:
        raise ValueError
    return d


def parse_params(v):
    """工艺参数等：JSON，或 "名=值；名=值"（分号 / 换行分隔）。"""
    if isinstance(v, dict):
        return v
    s = _text(v)
    if not s:
        return None
    if s.startswith('{'):
        return json.loads(s)
    out = {}
    for part in re.split(r'[;；\n]', s):
        if not part.strip():
            continue
        if '=' in part or '：' in part or ':' in part:
            k, val = re.split(r'[=：:]', part, maxsplit=1)
            out[k.strip()] = val.strip()
        else:
            raise ValueError
    return out


class Ctx:
    """一次导入的上下文：连接、用户、模式；引用查找带缓存（同一次导入里新建的也能被后面的行引用）。"""

    def __init__(self, conn, imp: Importer, mode='upsert', user=None):
        self.conn, self.imp, self.mode, self.user = conn, imp, mode, user
        self._ref: dict = {}
        self.created: dict = {}         # (实体, 编码) -> id：本次新建的
        self.notes: list = []
        self.state: dict = {}            # 导入器自用（如 BOM 已有的边）

    def ref(self, entity: str, value, key: str | None = None):
        """按编码找 id（找不到再按名称，名称唯一才算）；'#12' 直接是 id。找不到返回 None。"""
        s = _text(value)
        if not s:
            return None
        if s.startswith('#') and s[1:].isdigit():
            return int(s[1:])
        key = key or ref_key_of(entity)
        ck = (entity, key, s)
        if ck in self._ref:
            return self._ref[ck]
        if (entity, s) in self.created:
            return self.created[(entity, s)]
        t = TABLES[entity]
        rid = None
        if key in t.c:
            rid = self.conn.execute(sa.select(t.c.id).where(t.c[key] == s).limit(1)).scalar()
            if rid is None and key == 'code':
                rid = self.conn.execute(sa.select(t.c.id).where(sa.func.upper(t.c[key]) == s.upper()).limit(2)).scalars().all()
                rid = rid[0] if len(rid) == 1 else None
        if rid is None and 'name' in t.c and key != 'name':
            ids = self.conn.execute(sa.select(t.c.id).where(t.c.name == s).limit(2)).scalars().all()
            rid = ids[0] if len(ids) == 1 else None
        self._ref[ck] = rid
        return rid

    def remember(self, entity, code, rid):
        if code:
            self.created[(entity, str(code))] = rid
            self._ref = {k: v for k, v in self._ref.items() if not (k[0] == entity and k[2] == str(code))}


def convert(ctx: Ctx, c: Col, raw):
    """单元格 → 值。返回 (值, 错误文本|None)。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, None
    try:
        if c.t in ('str', 'text'):
            return _text(raw)[:4000 if c.t == 'text' else 200], None
        if c.t == 'int':
            f = _num(raw)
            if not float(f).is_integer():
                return None, _tr('{label}要填整数：{v}', label=c.label, v=_text(raw))
            return int(f), None
        if c.t == 'float':
            return _num(raw), None
        if c.t == 'bool':
            s = _text(raw).lower()
            if s in _TRUE:
                return True, None
            if s in _FALSE:
                return False, None
            return None, _tr('{label}只能填 是 / 否：{v}', label=c.label, v=_text(raw))
        if c.t == 'date':
            return _excel_date(raw).date(), None
        if c.t == 'datetime':
            return _excel_date(raw), None
        if c.t == 'enum':
            s = _text(raw)
            em = c.enum or {}
            if s in em:
                return s, None
            back = {str(v): k for k, v in em.items()}
            if s in back:
                return back[s], None
            low = {str(k).lower(): k for k in em}
            if s.lower() in low:
                return low[s.lower()], None
            return None, _tr('{label}只能是：{join}（填的是 {s}）', label=c.label, join=_tr('、').join(str(v) for v in em.values()), s=s)
        if c.t == 'ref':
            rid = ctx.ref(c.ref, raw, c.ref_key)
            if rid is None:
                return None, _tr('{label}：找不到{label2}「{v}」', label=c.label, label2=BY_NAME[c.ref].label, v=_text(raw))
            return rid, None
        if c.t == 'json':
            return parse_params(raw), None
        if c.t == 'file':
            return None, _tr('{label}：文件字段不能从表格导入（请在界面上传）', label=c.label)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, _tr('{label}格式不对：{v}（要{get}）', label=c.label, v=_text(raw), get=TYPES_CN.get(c.t, c.t))
    return raw, None


# ───────────────────────── 校验 ─────────────────────────
class Row:
    __slots__ = ('n', 'raw', 'values', 'errors', 'warnings', 'action', 'existing', 'key')

    def __init__(self, n, raw):
        self.n, self.raw = n, raw
        self.values: dict = {}
        self.errors: list = []
        self.warnings: list = []
        self.action = None
        self.existing = None
        self.key = None

    def err(self, msg, col=None):
        self.errors.append({'col': col, 'msg': msg})

    def warn(self, msg, col=None):
        self.warnings.append({'col': col, 'msg': msg})

    def out(self, cols_by_key=None):
        return {'n': self.n, 'action': 'error' if self.errors else self.action, 'errors': self.errors, 'warnings': self.warnings,
                'values': {k: _show(v) for k, v in self.values.items()}, 'raw': {k: _show(v) for k, v in self.raw.items()}}


def _show(v):
    if isinstance(v, (datetime, date)):
        return db.jsonable(v)
    return v


def default_find(ctx: Ctx, values: dict):
    imp = ctx.imp
    if not imp.entity or any(values.get(k) in (None, '') for k in imp.keys):
        return None
    t = TABLES[imp.entity]
    q = sa.select(t)
    for k in imp.keys:
        q = q.where(t.c[k] == values[k])
    r = ctx.conn.execute(q.limit(1)).mappings().first()
    if not r:
        return None
    r = dict(r)
    from . import cf
    if cf.fields(imp.entity):
        r['ext'] = cf.read_one(ctx.conn, imp.entity, r['id'])
    return r


def default_write(ctx: Ctx, values: dict, existing):
    """缺省写法：已有 → 只改文件里填了的字段（空单元格不清空）；没有 → 新增。返回 create / update / skip。"""
    imp = ctx.imp
    if existing:
        ch = {k: v for k, v in values.items() if v is not None and k not in imp.keys and _differs(existing, k, v)}
        if not ch:
            return 'skip'
        db.update(ctx.conn, imp.entity, existing['id'], ch)
        return 'update'
    rid = db.insert(ctx.conn, imp.entity, {k: v for k, v in values.items() if v is not None})
    if 'code' in values:
        ctx.remember(imp.entity, values.get('code'), rid)
    return 'create'


def _differs(existing, k, v):
    if k.startswith('x_'):
        cur = (existing.get('ext') or {}).get(k) if isinstance(existing.get('ext'), dict) else None
    else:
        cur = existing.get(k)
    if isinstance(cur, datetime) and isinstance(v, date) and not isinstance(v, datetime):
        cur = cur.date()
    if isinstance(cur, (int, float)) and isinstance(v, (int, float)) and not isinstance(v, bool):
        return abs(float(cur) - float(v)) > 1e-12
    return cur != v


def validate(ctx: Ctx, records: list[dict], example: dict | None = None) -> list[Row]:
    """records：[{列键: 原始值}]（文件行按对列结果转好的）。返回每行的校验结果；不写库。"""
    imp = ctx.imp
    cols = imp.cols()
    out: list[Row] = []
    seen: dict = {}
    if imp.prepare:
        records = imp.prepare(ctx, records)
    for i, raw in enumerate(records):
        r = Row(raw.pop('__n', i + 1), raw)
        if all(v in (None, '') for v in raw.values()):
            r.action = 'empty'
            out.append(r)
            continue
        if example and all(_text(raw.get(k)) == _text(v) for k, v in example.items() if v not in (None, '')) and any(v not in (None, '') for v in example.values()):
            r.action = 'skip'
            r.warn(_tr('模板里的示例行，已跳过'))
            out.append(r)
            continue
        for c in cols:
            if c.key not in raw:
                continue
            v, e = convert(ctx, c, raw[c.key])
            if e:
                r.err(e, c.key)
            elif c.cf and v is not None:
                from . import cf
                try:
                    v = cf.coerce_value(cf.field(imp.entity, c.key) or {'ftype': c.ftype, 'label': c.label}, v)
                except ValueError as ex:
                    r.err(str(ex), c.key)
            r.values[c.key] = v
        for k in imp.keys:                    # 唯一键总是必填（"已有"靠它判断）——但键里允许空值的导入器（技能、换型）自己的 find 处理
            c = next((x for x in cols if x.key == k), None)
            if c and c.req and r.values.get(k) in (None, '') and not any(x['col'] == k for x in r.errors):
                r.err(_tr('{label}必填', label=c.label), k)
        if imp.keys and all(r.values.get(k) not in (None, '') for k in imp.keys):
            r.key = tuple(r.values.get(k) for k in imp.keys)
            if r.key in seen:
                r.err(_tr('和第 {v} 行重复（{join}相同）', v=seen[r.key], join=_tr('、').join(_klabel(cols, k) for k in imp.keys)))
            seen.setdefault(r.key, r.n)
        if imp.entity and ctx.user is not None and not r.errors:
            probe = {k: v for k, v in r.values.items() if v is not None}
            if not perms.in_scope(ctx.user, imp.entity, probe):
                r.err(_tr('不在你的数据范围内（工厂 / 车间 / 产线）'))
        if not any(e['col'] in imp.keys or e['col'] is None for e in r.errors):      # 键没问题就找已有的（别的列有错也找，提示才准）
            try:
                r.existing = (imp.find or default_find)(ctx, r.values)
            except Exception as ex:          # 导入器自己的查找出错：当成这一行的错误
                r.err(_tr('查找已有记录出错：{ex}', ex=ex))
        if r.existing is None or ctx.mode == 'update':      # 新建要填全必填项；更新时空单元格 = 不改
            for c in cols:
                if c.req and r.values.get(c.key) in (None, '') and not any(x['col'] == c.key for x in r.errors) \
                        and not (r.existing is not None and c.key not in imp.keys):
                    r.err(_tr('{label}必填', label=c.label), c.key)
        if not r.errors:
            if r.existing is not None and ctx.mode == 'create':
                r.action = 'skip'
                r.warn(_tr('已存在，按"只新增"跳过'))
            elif r.existing is None and ctx.mode == 'update':
                r.err(_tr('库里没有这条记录（"只更新"模式）'))
            else:
                r.action = 'update' if r.existing is not None else 'create'
        if imp.check_row and not r.errors and r.action != 'skip':
            res = imp.check_row(ctx, r.values, r)
            for m in ([res] if isinstance(res, str) else res or []):
                r.err(m)
        out.append(r)
    if imp.check_all:
        imp.check_all(ctx, [r for r in out if r.action not in ('empty',) and not (r.action == 'skip' and not r.errors)])
    return out


def _klabel(cols, k):
    return next((c.label for c in cols if c.key == k), k)


def summary(rows: list[Row]) -> dict:
    s = {'total': 0, 'create': 0, 'update': 0, 'skip': 0, 'error': 0, 'empty': 0, 'warning': 0}
    for r in rows:
        if r.action == 'empty':
            s['empty'] += 1
            continue
        s['total'] += 1
        if r.errors:
            s['error'] += 1
        else:
            s[r.action or 'skip'] += 1
        if r.warnings:
            s['warning'] += 1
    return s


def write_rows(ctx: Ctx, rows: list[Row]) -> dict:
    """按顺序写（调用方负责事务）。返回实际的 {create, update, skip}；写入时某行出错抛 RowError（整批回滚）。"""
    imp = ctx.imp
    w = imp.write or default_write
    done = {'create': 0, 'update': 0, 'skip': 0}
    for r in rows:
        if r.errors or r.action in ('empty', 'skip', None):
            if r.action == 'skip' and not r.errors:
                done['skip'] += 1
            continue
        try:
            a = w(ctx, r.values, r.existing) or r.action
        except HTTPException as ex:
            raise RowError(r.n, str(ex.detail))
        except (ValueError, sa.exc.IntegrityError) as ex:
            msg = _tr('编码重复（唯一字段已存在）') if isinstance(ex, sa.exc.IntegrityError) else str(ex)
            raise RowError(r.n, msg)
        done[a] = done.get(a, 0) + 1
    return done


class RowError(Exception):
    def __init__(self, n, msg):
        super().__init__(_tr('第 {n} 行：{msg}', n=n, msg=msg))
        self.n, self.msg = n, msg


def check_perms(imp: Importer, user):
    if user is None:
        return
    perms.check(user, 'impl.import.run')
    for p in imp.need_perms():
        perms.check(user, p)


def run(key: str, records: list[dict], mode='upsert', dry_run=False, user=None, conn=None, label_keys=False) -> dict:
    """程序里直接导入：records = [{列键（或 label_keys=True 时中文列名）: 值}]。conn 给了就在调用方的事务里写（不提交）；
    否则自己开事务。有错误的行不写、整批也不写（返回 ok=False 和错误）；dry_run 只校验 + 演练。"""
    imp = get(key)
    if user is not None:
        check_perms(imp, user)
    if label_keys:
        lab = {c.label: c.key for c in imp.cols()}
        records = [{lab.get(k, k): v for k, v in r.items()} for r in records]
    records = [dict(r) for r in records]

    def body(c):
        with context.acting(user if user is not None else context.actor(), 'import') if user is not None else context.source('import'):
            ctx = Ctx(c, imp, mode, user)
            rows = validate(ctx, records)
            s = summary(rows)
            res = {'ok': s['error'] == 0, 'summary': s, 'errors': [{'n': r.n, 'errors': r.errors} for r in rows if r.errors][:200],
                   'notes': ctx.notes}
            if s['error'] or dry_run:
                return res, False
            res['done'] = write_rows(ctx, rows)
            return res, True

    if conn is not None:
        res, _ = body(conn)
        return res
    with db.engine.connect() as c:
        tx = c.begin()
        try:
            res, keep = body(c)
            if keep:
                tx.commit()
            else:
                tx.rollback()
        except Exception:
            tx.rollback()
            raise
    return res


# ───────────────────────── 导入任务（界面流程） ─────────────────────────
def jt():
    from ..schema_impl import T
    return T['import_job']


def create_job(key, filename, data: bytes, user, sheet=None, batch=None) -> dict:
    imp = get(key)
    check_perms(imp, user)
    try:
        p = parse_file(filename, data, sheet)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    if not p['headers']:
        raise HTTPException(400, _tr('这个文件里没有数据（第一行应该是表头）'))
    mapping = auto_map(imp, p['headers'])
    rows = [[_jsonable_cell(v) for v in r] for r in p['rows']]
    now = db.clock_wall()
    with db.engine.begin() as c:
        jid = c.execute(jt().insert().values(importer=key, filename=(filename or '')[:300], sheet=p['sheet'], status='uploaded', mode='upsert',
                                             headers=p['headers'], mapping=mapping, rows=rows, n_rows=len(rows), created_by=_who(user),
                                             created_at=now, batch=batch)).inserted_primary_key[0]
    return job_out(jid, user, extra={'sheets': p['sheets'], 'header_row': p['header_row']})


def _jsonable_cell(v):
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S') if (v.hour or v.minute or v.second) else v.strftime('%Y-%m-%d')
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)


def _who(user):
    return (user or {}).get('username') or 'system'


def load_job(jid, user=None) -> dict:
    with db.engine.connect() as c:
        j = c.execute(sa.select(jt()).where(jt().c.id == jid)).mappings().first()
    if not j:
        raise HTTPException(404, _tr('导入任务不存在'))
    j = dict(j)
    if user is not None and j['created_by'] != _who(user) and not perms.can(user, 'impl.import.view'):
        raise HTTPException(403, _tr('只能看自己的导入任务'))
    return j


def job_out(jid, user=None, extra=None, rows=False) -> dict:
    j = load_job(jid, user)
    imp = get(j['importer'])
    d = {k: db.jsonable(v) for k, v in j.items() if k not in ('rows',)}
    d['importer_label'] = imp.label
    d['columns'] = [c.out() for c in imp.cols()]
    d['sample'] = (j['rows'] or [])[:5]
    d['status_label'] = STATUS_CN.get(j['status'], j['status'])
    if rows:
        d['rows'] = j['rows']
    return {**d, **(extra or {})}


STATUS_CN = {'uploaded': '已上传', 'validated': '已校验', 'committed': '已导入', 'failed': '导入失败', 'dry_run': '已演练'}


def _records(j, mapping):
    hdr = j['headers'] or []
    out = []
    for i, row in enumerate(j['rows'] or []):
        rec = {'__n': i + 1 + 1}          # Excel 行号（表头在第 1 行）
        for idx, key in mapping.items():
            try:
                ii = int(idx)
            except ValueError:
                continue
            if key and ii < len(hdr) and ii < len(row):
                rec[key] = row[ii]
        out.append(rec)
    return out


def _example(imp):
    if not imp.examples:
        return None
    ex = imp.examples[0]
    return {k: v for k, v in ex.items()}


def set_mapping(jid, mapping: dict | None, mode: str | None, user) -> dict:
    j = load_job(jid, user)
    imp = get(j['importer'])
    check_perms(imp, user)
    if j['status'] == 'committed':
        raise HTTPException(400, _tr('这批已经导入了'))
    upd = {}
    if mapping is not None:
        keys = {c.key for c in imp.cols()}
        clean = {str(k): v for k, v in mapping.items() if v in keys}
        dup = [v for v in clean.values() if list(clean.values()).count(v) > 1]
        if dup:
            raise HTTPException(400, _tr('两列对到了同一个字段：{v}', v=dup[0]))
        upd['mapping'] = clean
    if mode:
        if mode not in MODES:
            raise HTTPException(400, _tr('mode 只能是 ') + '/'.join(MODES))
        upd['mode'] = mode
    if upd:
        with db.engine.begin() as c:
            c.execute(jt().update().where(jt().c.id == jid).values(**upd))
    return job_out(jid, user)


def preview(jid, user, page=1, size=50, only='') -> dict:
    """校验（不写库）：逐行结果 + 汇总。"""
    j = load_job(jid, user)
    imp = get(j['importer'])
    check_perms(imp, user)
    missing = [c for c in imp.cols() if c.req and c.key not in (j['mapping'] or {}).values()]
    t0 = time.time()
    with db.engine.connect() as c:
        ctx = Ctx(c, imp, j['mode'] or 'upsert', user)
        rows = validate(ctx, _records(j, j['mapping'] or {}), _example(imp))
        c.rollback()
    s = summary(rows)
    issues = [{'n': r.n, 'errors': r.errors, 'warnings': r.warnings} for r in rows if r.errors or r.warnings]
    with db.engine.begin() as c:
        c.execute(jt().update().where(jt().c.id == jid).values(status='validated' if j['status'] in ('uploaded', 'validated', 'dry_run', 'failed') else j['status'],
                                                                 summary=s, issues=issues[:5000], ms=int((time.time() - t0) * 1000)))
    sel = [r for r in rows if r.action != 'empty']
    if only == 'error':
        sel = [r for r in sel if r.errors]
    elif only == 'warning':
        sel = [r for r in sel if r.warnings]
    elif only in ('create', 'update', 'skip'):
        sel = [r for r in sel if r.action == only and not r.errors]
    size = max(1, min(500, size))
    return {'summary': s, 'missing': [{'key': c.key, 'label': c.label} for c in missing], 'total': len(sel),
            'rows': [r.out() for r in sel[(page - 1) * size: page * size]], 'ms': int((time.time() - t0) * 1000), 'notes': ctx.notes}


def commit(jid, user, dry_run=False, skip_errors=False) -> dict:
    """确认导入（一个事务）。有错误行时默认拒绝；skip_errors=True 只导没有错误的行。dry_run：真写一遍再回滚，报告新增 / 更新 / 跳过。"""
    j = load_job(jid, user)
    imp = get(j['importer'])
    check_perms(imp, user)
    if j['status'] == 'committed':
        raise HTTPException(400, _tr('这批已经导入了'))
    missing = [c.label for c in imp.cols() if c.req and c.key not in (j['mapping'] or {}).values()]
    if missing:
        raise HTTPException(400, _tr('必填列没有对上：') + _tr('、').join(missing))
    t0 = time.time()
    res: dict = {}
    status, err = 'committed', None
    with context.acting(user, 'import'):
        with db.engine.connect() as c:
            tx = c.begin()
            try:
                ctx = Ctx(c, imp, j['mode'] or 'upsert', user)
                rows = validate(ctx, _records(j, j['mapping'] or {}), _example(imp))
                s = summary(rows)
                if s['error'] and not skip_errors:
                    tx.rollback()
                    raise HTTPException(400, _tr('有 {error} 行错误，先改好（或选"只导入没有错误的行"）再导入', error=s['error']))
                done = write_rows(ctx, rows)
                res = {'summary': s, 'done': done, 'notes': ctx.notes}
                if dry_run:
                    tx.rollback()
                    status = 'dry_run'
                else:
                    audit.audit(c, 'import', imp.entity, None, detail={'job': jid, 'importer': imp.key, 'file': j['filename'], 'mode': j['mode'],
                                                                      'done': done, 'errors_skipped': s['error'] if skip_errors else 0},
                                target=f'import:{jid}', user=_who(user), source='import')
                    tx.commit()
            except HTTPException:
                raise
            except RowError as ex:
                tx.rollback()
                status, err = 'failed', str(ex)
            except Exception as ex:                  # 数据库错误等：整批回滚
                tx.rollback()
                status, err = 'failed', f'{type(ex).__name__}: {ex}'[:1000]
    ms = int((time.time() - t0) * 1000)
    with db.engine.begin() as c:
        vals = {'status': status, 'error': err, 'ms': ms}
        if res.get('summary'):
            vals['summary'] = {**res['summary'], 'done': res.get('done')}
        if status == 'committed':
            vals['committed_at'] = db.clock_wall()
        c.execute(jt().update().where(jt().c.id == jid).values(**vals))
    if status == 'failed':
        return {'ok': False, 'error': err, 'rolled_back': True, 'ms': ms}
    if imp.entity in ('equipment',) and status == 'committed':
        _after_equipment()
    return {'ok': True, 'dry_run': dry_run, **res, 'ms': ms}


def _after_equipment():
    """导入了设备：行业模板里按设备类型的点检保养模板、演示数据里依赖设备的记录接着补上。"""
    try:
        from . import packs
        packs.after_equipment()
    except Exception:
        import logging
        logging.getLogger('mes.impl').exception(_tr('导入设备后补行业模板数据失败'))


def jobs(user, importer='', limit=100) -> list:
    t = jt()
    q = sa.select(*[c for c in t.c if c.name != 'rows']).order_by(t.c.id.desc()).limit(min(500, limit))
    if importer:
        q = q.where(t.c.importer == importer)
    if not perms.can(user, 'impl.import.view'):
        q = q.where(t.c.created_by == _who(user))
    with db.engine.connect() as c:
        rows = [dict(r) for r in c.execute(q).mappings()]
    names = {k: v.label for k, v in REGISTRY.items()}
    for r in rows:
        r['importer_label'] = names.get(r['importer'], r['importer'])
        r['status_label'] = STATUS_CN.get(r['status'], r['status'])
    return [{k: db.jsonable(v) for k, v in r.items() if k != 'issues'} for r in rows]


# ───────────────────────── 模板 / 错误报告（.xlsx） ─────────────────────────
def sheet_title(label: str) -> str:
    """Excel 页名：最多 31 个字，不能有 [ ] : * ? / 反斜杠。"""
    return re.sub(r'[\[\]:*?/\\]', '-', label)[:31]


def _wb_styles():
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    thin = Side(style='thin', color='C9CED6')
    return {'head': Font(bold=True, color='1F2A37'), 'req': Font(bold=True, color='C0392B'), 'fill': PatternFill('solid', fgColor='EEF3FB'),
            'ex': Font(italic=True, color='7B8794'), 'err': PatternFill('solid', fgColor='FDECEA'), 'errfont': Font(color='C0392B'),
            'border': Border(left=thin, right=thin, top=thin, bottom=thin), 'wrap': Alignment(wrap_text=True, vertical='top'),
            'title': Font(bold=True, size=14)}


def _doc_lines(imp, cols):
    lines = [imp.doc or _tr('把{label}填在「{sheet_title}」这一页，一行一条，第一行表头不要改。', label=imp.label, sheet_title=sheet_title(imp.label)),
             _tr('唯一键：{join}（已存在的按"导入方式"更新或跳过；空单元格不会清空已有值）。', join=_tr('、').join(_klabel(cols, k) for k in imp.keys)),
             _tr('红色表头是必填；带下拉的列请从下拉里选；引用别的数据（物料、工序、设备……）填对方的编码。'),
             _tr('第 2 行是示例，导入时会自动跳过（也可以删掉）。日期写 2026-11-04；是/否 写 是 或 否。'),
             _tr('导入步骤：实施 → 数据导入 → 选这个模板 → 上传 → 核对列 → 校验预览 → 演练 → 确认导入；有错误时下载错误报告改好再传。')]
    lines += [_tr('规则：{r}', r=r) for r in imp.rules]
    if imp.depends:
        lines.append(_tr('请先导入：') + _tr('、').join(get(d).label for d in imp.depends if d in REGISTRY))
    return lines


def _data_sheet(ws, imp, cols, lists, li, conn, st):
    """数据页：表头（必填红色、批注）、示例行、冻结首行、下拉（选项写清单；引用从 _lists 页取已有的编码，最多 3000 个，允许清单外的值）。"""
    from openpyxl.comments import Comment
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation
    ex = imp.examples[0] if imp.examples else {}
    for i, c in enumerate(cols, 1):
        cell = ws.cell(1, i, c.label)
        cell.font = st['req'] if c.req else st['head']
        cell.fill, cell.border = st['fill'], st['border']
        tip = (_tr('必填。') if c.req else '') + (c.hint or '') + (_tr(' 可选：{join}', join=_tr('、').join(str(v) for v in c.enum.values())) if c.enum else '')
        if tip.strip():
            cell.comment = Comment(tip.strip()[:500], _tr('蜂元智能 MOM'))
        ws.column_dimensions[get_column_letter(i)].width = max(10, min(36, len(c.label) * 2 + 4))
        v = ex.get(c.key)
        if v not in (None, ''):
            ec = ws.cell(2, i, v if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False))
            ec.font = st['ex']
    ws.freeze_panes = 'A2'
    for i, c in enumerate(cols, 1):
        colL = get_column_letter(i)
        vals = None
        if c.t == 'enum' and c.enum:
            vals = [str(v) for v in c.enum.values()]
        elif c.t == 'bool':
            vals = [_tr('是'), _tr('否')]
        elif c.t == 'ref' and c.ref:
            rk = c.ref_key or ref_key_of(c.ref)
            t = TABLES[c.ref]
            if rk in t.c:
                vals = [str(x) for x in conn.execute(sa.select(t.c[rk]).where(t.c[rk].isnot(None)).order_by(t.c[rk]).limit(3000)).scalars()]
        if not vals:
            continue
        li += 1
        L = get_column_letter(li)
        lists.cell(1, li, f'{imp.label}·{c.label}')
        for k, v in enumerate(vals, 2):
            lists.cell(k, li, v)
        dv = DataValidation(type='list', formula1=f"='_lists'!${L}$2:${L}${len(vals) + 1}", allow_blank=True,
                            showErrorMessage=c.t != 'ref', errorStyle='stop' if c.t != 'ref' else 'warning',
                            error=_tr('{label}请从下拉里选', label=c.label), errorTitle=_tr('选项'))
        dv.add(f'{colL}2:{colL}{MAX_ROWS + 1}')
        ws.add_data_validation(dv)
    return li


def _doc_sheet(ws0, imp, cols, st, r=1, title=True):
    from openpyxl.utils import get_column_letter
    if title:
        ws0.cell(r, 1, _tr('导入模板：{label}', label=imp.label)).font = st['title']
        r += 2
    for ln in _doc_lines(imp, cols):
        ws0.cell(r, 1, ln).alignment = st['wrap']
        ws0.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        ws0.row_dimensions[r].height = 30 if len(ln) > 60 else 18
        r += 1
    r += 1
    for i, h in enumerate((_tr('列名'), _tr('必填'), _tr('类型'), _tr('说明'), _tr('可选值 / 示例'), _tr('字段键')), 1):
        cell = ws0.cell(r, i, h)
        cell.font, cell.fill, cell.border = st['head'], st['fill'], st['border']
    ex = imp.examples[0] if imp.examples else {}
    for c in cols:
        r += 1
        vals = _tr('、').join(str(v) for v in (c.enum or {}).values()) if c.enum else (_tr('{label}编码', label=BY_NAME[c.ref].label) if c.ref else '')
        if ex.get(c.key) not in (None, ''):
            vals = (vals + _tr('；') if vals else '') + _tr('例：{v}', v=ex[c.key])
        row = (c.label, _tr('是') if c.req else '', TYPES_CN.get(c.t, c.t) + (_tr('（自定义）') if c.cf else ''), c.hint, vals, c.key)
        for i, v in enumerate(row, 1):
            cell = ws0.cell(r, i, v)
            cell.border, cell.alignment = st['border'], st['wrap']
            if i == 1 and c.req:
                cell.font = st['req']
    for i, w in enumerate((18, 6, 12, 40, 50, 16), 1):
        ws0.column_dimensions[get_column_letter(i)].width = w
    return r + 2


def template(key: str) -> bytes:
    """导入模板：说明页、数据页（表头、必填红色、批注、示例行、下拉）、_lists 隐藏页（引用的编码清单）。"""
    import openpyxl
    imp = get(key)
    cols = imp.cols()
    st = _wb_styles()
    wb = openpyxl.Workbook()
    ws0 = wb.active
    ws0.title = _tr('说明')
    ws = wb.create_sheet(sheet_title(imp.label))
    lists = wb.create_sheet('_lists')
    lists.sheet_state = 'hidden'
    _doc_sheet(ws0, imp, cols, st)
    with db.engine.connect() as conn:
        _data_sheet(ws, imp, cols, lists, 0, conn, st)
    wb.active = 1
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def workbook_template(keys=None, modules=None) -> bytes:
    """主数据初始化工作簿：一页说明（按导入顺序列出全部模板）+ 每个导入器一页。整本上传后按页名分成多个导入任务。"""
    import openpyxl
    st = _wb_styles()
    wb = openpyxl.Workbook()
    ws0 = wb.active
    ws0.title = _tr('说明')
    ws0.cell(1, 1, _tr('主数据初始化工作簿')).font = st['title']
    ws0.cell(2, 1, _tr('每一页是一类主数据，按页的顺序导入（先单位、组织、工序、物料，再 BOM、工艺、人员、仓库、期初库存……）。整本上传：实施 → 数据导入 → 上传初始化工作簿，系统按页名分成多个导入任务，逐个校验、确认。'))
    ws0.merge_cells('A2:F2')
    lists = wb.create_sheet('_lists')
    lists.sheet_state = 'hidden'
    li, r = 0, 4
    imps = [x for x in all_importers() if (not keys or x.key in keys) and (not x.module or modules is None or x.module in modules)]
    with db.engine.connect() as conn:
        for n, imp in enumerate(imps, 1):
            cols = imp.cols()
            ws0.cell(r, 1, f'{n}. {imp.label}').font = st['head']
            r = _doc_sheet(ws0, imp, cols, st, r + 1, title=False)
            ws = wb.create_sheet(sheet_title(imp.label))
            li = _data_sheet(ws, imp, cols, lists, li, conn, st)
    wb.move_sheet('_lists', offset=len(wb.sheetnames))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def create_workbook_jobs(filename, data: bytes, user) -> list:
    """整本初始化工作簿：每个有数据的页（页名 = 导入器名称或键）建一个导入任务，同一批次号；按导入顺序返回。"""
    import openpyxl
    import secrets
    by_label = {}
    for imp in all_importers():
        by_label.setdefault(sheet_title(imp.label), imp)
        by_label.setdefault(imp.key, imp)
        if imp.entity:
            by_label.setdefault(BY_NAME[imp.entity].label, imp)
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        names = [ws.title for ws in wb.worksheets if ws.sheet_state == 'visible']
        wb.close()
    except Exception as ex:
        raise HTTPException(400, _tr('打不开这个 Excel：{ex}', ex=ex))
    batch = 'B' + db.clock_wall().strftime('%y%m%d%H%M%S') + secrets.token_hex(2)
    out = []
    for name in names:
        imp = by_label.get(name)
        if not imp:
            continue
        check_perms(imp, user)
        p = parse_file(filename, data, name)
        real = [r for r in p['rows'] if any(v not in (None, '') for v in r)]
        ex = _example(imp)
        if ex and len(real) == 1:
            m = auto_map(imp, p['headers'])
            rec = {m[str(i)]: v for i, v in enumerate(real[0]) if str(i) in m}
            if all(_text(rec.get(k)) == _text(v) for k, v in ex.items() if v not in (None, '')):
                continue          # 只有示例行：这页没填
        if not real:
            continue
        out.append((imp, create_job(imp.key, filename, data, user, sheet=name, batch=batch)))
    if not out:
        raise HTTPException(400, _tr('工作簿里没有认得的页（页名要和模板一致，如「物料」「BOM（多级）」）或都没填数据'))
    out.sort(key=lambda x: x[0].order)
    return [j for _, j in out]


def error_report(jid, user) -> bytes:
    """错误报告：原样的表头和行 + "行号""结果""错误"三列；有错的行标红。改好直接再上传。"""
    import openpyxl
    from openpyxl.utils import get_column_letter
    j = load_job(jid, user)
    imp = get(j['importer'])
    with db.engine.connect() as c:
        ctx = Ctx(c, imp, j['mode'] or 'upsert', user)
        rows = validate(ctx, _records(j, j['mapping'] or {}), _example(imp))
        c.rollback()
    by_n = {r.n: r for r in rows}
    st = _wb_styles()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = (imp.label + _tr('-错误报告'))[:31]
    hdr = list(j['headers'] or []) + [_tr('行号'), _tr('结果'), _tr('错误')]
    for i, h in enumerate(hdr, 1):
        cell = ws.cell(1, i, h)
        cell.font, cell.fill = st['head'], st['fill']
    nh = len(j['headers'] or [])
    cols = {c.key: c for c in imp.cols()}
    inv = {v: int(k) for k, v in (j['mapping'] or {}).items() if str(k).isdigit()}
    for ri, raw in enumerate(j['rows'] or [], 2):
        r = by_n.get(ri)
        for ci, v in enumerate(raw, 1):
            ws.cell(ri, ci, v)
        ws.cell(ri, nh + 1, ri)
        if r is None:
            continue
        act = 'error' if r.errors else (r.action or '')
        ws.cell(ri, nh + 2, ACTIONS_CN.get(act, act))
        msgs = [e['msg'] for e in r.errors] + [(_tr('提示：') + w['msg']) for w in r.warnings]
        ec = ws.cell(ri, nh + 3, _tr('；').join(msgs))
        if r.errors:
            ec.font = st['errfont']
            for e in r.errors:
                if e.get('col') in inv:
                    ws.cell(ri, inv[e['col']] + 1).fill = st['err']
            ws.cell(ri, nh + 2).fill = st['err']
    for i in range(1, len(hdr) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 60 if i == len(hdr) else 14
    ws.freeze_panes = 'A2'
    _ = cols
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ───────────────────────── AI 对列建议（AI 功能"实施助手"） ─────────────────────────
def ai_map(jid, user) -> dict:
    """规则对不上的表头交给大模型（演示模式回放录制）。返回 {mapping, by: ai|rule, reasons}。"""
    from ..services import ai_demo
    j = load_job(jid, user)
    imp = get(j['importer'])
    cols = imp.cols()
    cur = dict(j['mapping'] or {})
    used = set(cur.values())
    hdr = j['headers'] or []
    rows = j['rows'] or []
    todo = [(i, h) for i, h in enumerate(hdr) if str(i) not in cur and h]
    if not todo:
        return {'mapping': cur, 'by': 'rule', 'suggested': {}, 'reasons': {}, 'msg': _tr('所有列都已经对上了')}
    samples = {h: [_text(r[i]) for r in rows[:5] if i < len(r) and r[i] not in (None, '')][:3] for i, h in todo}
    payload = {'importer': imp.key, 'headers': [h for _, h in todo], 'samples': samples,
               'columns': [{'key': c.key, 'label': c.label, 't': c.t} for c in cols if c.key not in used]}

    def live():
        from ..services import ai as ai_mod
        if not ai_mod.llm_available():
            return fallback()
        prompt = (_tr('把客户 Excel 的表头对到 MES 导入字段。只输出 JSON：{{"mapping": {{"表头": "字段键 或 null"}}, "reasons": {{"表头": "一句话理由"}}}}。\n导入：{label}\n可用字段：', label=imp.label) + json.dumps(payload['columns'], ensure_ascii=False) +
                  _tr('\n客户表头和样例值：') + json.dumps(samples, ensure_ascii=False))
        msg, _tok = ai_mod.llm_chat([{'role': 'system', 'content': _tr('你是 MES 实施顾问，熟悉制造业主数据。')}, {'role': 'user', 'content': prompt}], json_mode=True)
        content = msg.get('content') or '{}'
        d = json.loads(re.search(r'\{.*\}', content, re.S).group(0))
        return {'mapping': d.get('mapping') or {}, 'reasons': d.get('reasons') or {}, 'by': 'ai'}

    def fallback():
        return {'mapping': _guess(imp, todo, samples, used), 'reasons': {}, 'by': 'rule'}

    res = ai_demo.run('impl', 'map_columns', payload, live=live, fallback=fallback, recordable=lambda r: r.get('by') == 'ai')
    keys = {c.key for c in cols}
    sug = {}
    for i, h in todo:
        k = (res.get('mapping') or {}).get(h)
        if k in keys and k not in used and k not in sug.values():
            sug[str(i)] = k
    return {'mapping': {**cur, **sug}, 'suggested': sug, 'by': res.get('by', 'rule'), 'reasons': res.get('reasons') or {}, 'demo': bool(res.get('demo'))}


def _guess(imp, todo, samples, used):
    """没有大模型时的兜底：按样例值的形状猜（像编码的、像日期的、像数字的）+ 宽松相似度。"""
    from ..services.ai_demo import similarity
    out = {}
    cols = [c for c in imp.cols() if c.key not in used]
    for _, h in todo:
        nh = norm_header(h)
        best, score = None, 0.0
        for c in cols:
            if c.key in out.values():
                continue
            s = max(similarity(nh, norm_header(n)) for n in (c.label, c.key) + tuple(c.aliases))
            if s > score:
                best, score = c.key, s
        if best and score >= 0.5:
            out[h] = best
    return out
