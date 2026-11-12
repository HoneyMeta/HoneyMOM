"""统一的只读数据工具层（5.0 M6）：MCP 服务（services/mcp_server.py）、右下角 AI 助手、智能体节点、技能（services/skills.py）共用一份实现。

每个工具 = 名称 + 中文标题 + 说明 + JSON Schema 参数 + 需要的权限点 + 实现。实现里一律按"当前是谁"（context.actor()：
请求的用户 / API 密钥 / 智能体发布人；None = 系统内部）检查权限和数据范围（perms.scope_cond / allowed_ids），所以 MCP 的受限密钥、
助手里的操作工、定时运行的技能看到的数据范围都一样。

结果统一是 dict：{summary（中文摘要）, rows?, count?, data?, blocks?（块协议，见 ai_blocks.py，技能的输出和 MCP 的结构化结果用）}。
工具执行出错抛 ToolError（参数不对 / 没有权限 / 找不到对象），调用方转成"工具结果里的错误"。

写操作：只有一个可选的 draft_action（只起草动作草稿，进审批中心，审批通过才执行），默认关闭（系统参数 mcp_write_tools=1 且有 mcp.write 才出现）。
"""
from __future__ import annotations

from ..i18n import _tr
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

import sqlalchemy as sa

from .. import context, db, perms
from ..db import clock
from ..schema import BY_NAME, ENTITIES, TABLES, ts_point_t


class ToolError(Exception):
    pass


@dataclass
class Tool:
    name: str
    title: str
    description: str
    props: dict
    required: list
    perm: str | tuple | None
    fn: Callable
    phrase_fn: Callable | None = None
    group: str = 'data'            # data | ts | cycle | skill | write
    write: bool = False
    extra: dict = field(default_factory=dict)

    def schema(self):
        return {'type': 'object', 'properties': self.props, **({'required': self.required} if self.required else {})}

    def phrase(self, args):
        try:
            return self.phrase_fn(args or {}) if self.phrase_fn else _tr('正在{title}…', title=self.title)
        except Exception:
            return _tr('正在{title}…', title=self.title)


TOOLS: dict[str, Tool] = {}


def tool(name, title, description, props=None, required=(), perm=None, phrase=None, group='data', write=False):
    def deco(fn):
        TOOLS[name] = Tool(name, title, description, props or {}, list(required), perm, fn, phrase, group, write)
        return fn
    return deco


# 右下角助手额外拿到的工具（mes_query / query_sql / day_kpi / kb_search 原来就有）
ASSISTANT_TOOLS = ['search', 'get_record', 'factory_model', 'list_points', 'ts_range', 'ts_rollup', 'ts_latest', 'ts_events', 'cycle_analysis', 'trace',
                   'query_maintenance', 'query_energy', 'run_skill']
# 给 MCP / 技能用、但助手已经有同名实现的
LEGACY = {'query_sql', 'kb_search', 'day_kpi', 'mes_query'}


# ───────────────────────── 公共 ─────────────────────────
def actor():
    return context.actor()


def need(perm):
    u = actor()
    if u is None or perm is None:
        return
    ps = perm if isinstance(perm, (tuple, list)) else (perm,)
    if not any(perms.can(u, p) for p in ps):
        raise ToolError(_tr('没有权限：') + _tr('、').join(perms.label_of(p) for p in ps))


def _s(v, n=200):
    return ('' if v is None else str(v)).strip()[:n]


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def jsonable(v):
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] if v.microsecond else v.strftime('%Y-%m-%d %H:%M:%S')
    return db.jsonable(v)


def shifts():
    """班次表 [(code, name, 开始 'HH:MM', 结束)]（只取排产启用的）。"""
    with db.engine.connect() as c:
        rows = db.rows(c, 'shift', order=TABLES['shift'].c.start)
    out = [(r['code'], r['name'], r['start'] or '08:00', r['end'] or '20:00') for r in rows if r['active'] is not False and r['start'] and r['end']]
    return out or [('A', _tr('白班'), '08:00', '20:00'), ('B', _tr('夜班'), '20:00', '08:00')]


def _hm(day, s):
    h, m = (int(x) for x in s.split(':')[:2])
    return datetime(day.year, day.month, day.day, h, m)


def shift_windows(now, days_back=2):
    """最近几天的班次时间段 [(开始, 结束, 班次名)]，按开始时间排序。"""
    out = []
    for d in range(days_back, -2, -1):
        day = (now - timedelta(days=d)).date()
        for code, name, a, b in shifts():
            s = _hm(day, a)
            e = _hm(day, b)
            if e <= s:
                e += timedelta(days=1)
            out.append((s, e, name))
    return sorted(out)


def period(args: dict, default_hours=8.0):
    """统一的时间段参数 → (开始, 结束, 中文说明)。按数据时钟算。
    period: today / yesterday / shift（当前班次）/ last_shift（刚结束的班次）/ week / 8h / 3d；或 day=YYYY-MM-DD；或 start/end；或 hours。"""
    now = clock.now()
    a = args or {}
    if a.get('start'):
        s = db.parse_dt(a['start']) or _ms(a['start'])
        e = (db.parse_dt(a['end']) or _ms(a['end'])) if a.get('end') else now
        if not s:
            raise ToolError(_tr('开始时间格式不对：{start}', start=a['start']))
        return s, e, f"{jsonable(s)[:16]} ~ {jsonable(e)[:16]}"
    p = _s(a.get('period') or a.get('day') or '', 40).lower()
    if p in ('today', '今天', '当天'):
        s, e = db.day_range(now.date())
        return s, min(e, now), _tr('今天')
    if p in ('yesterday', '昨天'):
        s, e = db.day_range((now - timedelta(days=1)).date())
        return s, e, _tr('昨天')
    if p in ('shift', 'current_shift', '本班', '当班'):
        for s, e, name in shift_windows(now):
            if s <= now < e:
                return s, now, _tr('本班（{name}）', name=name)
    if p in ('last_shift', 'prev_shift', '上一班', '刚结束的班次'):
        done = [w for w in shift_windows(now) if w[1] <= now]
        if done:
            s, e, name = done[-1]
            return s, e, f"{name} {s:%m-%d %H:%M}~{e:%H:%M}"
    if p in ('week', '7d', '本周', '近7天'):
        return now - timedelta(days=7), now, _tr('近 7 天')
    if p and p[-1:] in ('h', 'd') and p[:-1].replace('.', '', 1).isdigit():
        n = float(p[:-1])
        return now - (timedelta(hours=n) if p[-1] == 'h' else timedelta(days=n)), now, _tr('近 {n:g} {v}', n=n, v=_tr('小时') if p[-1] == 'h' else _tr('天'))
    if p and len(p) == 10 and p[4] == '-':
        s, e = db.day_range(p)
        return s, min(e, now) if s.date() == now.date() else e, p
    h = _num(a.get('hours'), default_hours) or default_hours
    return now - timedelta(hours=h), now, _tr('近 {h:g} 小时', h=h)


def _ms(v):
    from ..compat import to_dt
    return to_dt(v) if v else None


def eq_scope_ids():
    u = actor()
    return None if u is None else perms.allowed_ids(u, 'equipment')


def find_equipment(c, key, required=True):
    if key in (None, ''):
        if required:
            raise ToolError(_tr('请给设备编号'))
        return None
    t = TABLES['equipment']
    k = str(key).strip()
    r = c.execute(sa.select(t).where(sa.or_(t.c.code == k, t.c.name == k, *( [t.c.id == int(k)] if k.isdigit() else []))).limit(1)).mappings().first()
    if not r:
        r = c.execute(sa.select(t).where(sa.func.lower(t.c.code) == k.lower()).limit(1)).mappings().first()
    if not r:
        raise ToolError(_tr('找不到设备：{k}', k=k))
    ids = eq_scope_ids()
    if ids is not None and r['id'] not in ids:
        raise ToolError(_tr('设备 {k} 不在你的数据范围内', k=k))
    return dict(r)


def find_line(c, key):
    if key in (None, ''):
        return None
    t = TABLES['line']
    k = str(key).strip()
    r = c.execute(sa.select(t).where(sa.or_(t.c.code == k, t.c.name == k, *([t.c.id == int(k)] if k.isdigit() else []))).limit(1)).mappings().first()
    if not r:
        r = c.execute(sa.select(t).where(t.c.name.like(f'%{k}%')).limit(1)).mappings().first()
    if not r:
        raise ToolError(_tr('找不到产线：{k}', k=k))
    u = actor()
    if u is not None and perms.user_scope(u) is not None and r['id'] not in perms.scope_sets(perms.user_scope(u))['line']:
        raise ToolError(_tr('产线 {k} 不在你的数据范围内', k=k))
    return dict(r)


def _table(title, cols, rows, n=15):
    return {'type': 'table', 'title': title, 'columns': [{'key': k, 'label': lab, **({'num': True} if num else {}), **({'format': f} if f else {})}
                                                         for k, lab, num, f in cols], 'rows': rows[:n]}


# ───────────────────────── 实体 ─────────────────────────
HIDDEN = {'sys_user', 'setting', 'ai_log', 'notice', 'daily_report', 'part_event', 'insp_value'}


def _searchable(e):
    cols = [f.name for f in e.fields if f.search or f.name in ('code', 'name', 'title', 'sn')]
    return [c for c in dict.fromkeys(cols) if c in TABLES[e.name].c]


@tool('search', '搜索记录', '按关键词搜索 MES 记录（设备、工单、物料、不合格品、维修单、零件码……），返回实体类型、id、编号和名称。'
      '只返回你有权限、在你数据范围内的记录。', {'q': {'type': 'string', 'description': '关键词，如 CNC1、WO2611、气孔'},
                                     'entity': {'type': 'string', 'description': '可选：只搜某类实体（如 equipment、work_order、ncr）'},
                                     'limit': {'type': 'integer', 'description': '最多返回几条，默认 20'}},
      ['q'], phrase=lambda a: _tr('正在搜索「{v}」…', v=_s(a.get('q'), 20)))
def t_search(a):
    q = _s(a.get('q'), 80)
    if not q:
        raise ToolError(_tr('请给关键词'))
    lim = max(1, min(50, int(_num(a.get('limit'), 20))))
    u = actor()
    out = []
    ents = [BY_NAME[a['entity']]] if a.get('entity') in BY_NAME else ENTITIES
    with db.engine.connect() as c:
        for e in ents:
            if e.name in HIDDEN or e.module not in perms.MODULE_KEYS:
                continue
            if u is not None and not perms.can(u, perms.entity_perm(e.name, 'read')):
                continue
            cols = _searchable(e)
            if not cols:
                continue
            t = TABLES[e.name]
            cond = sa.or_(*[t.c[x].ilike(f'%{q}%') for x in cols])
            sc = perms.scope_cond(u, e.name, t) if u is not None else None
            qq = sa.select(t).where(cond)
            if sc is not None:
                qq = qq.where(sc)
            for r in c.execute(qq.order_by(t.c.id.desc()).limit(lim)).mappings():
                code = r.get('code') or r.get('sn') or str(r['id'])
                out.append({'entity': e.name, 'entity_label': e.label, 'id': r['id'], 'code': code,
                            'label': _s(r.get('name') or r.get('title') or r.get('fault') or r.get('description') or '', 80)})
            if len(out) >= lim * 2:
                break
    out.sort(key=lambda x: (x['code'].lower() != q.lower(), not x['code'].lower().startswith(q.lower())))
    out = out[:lim]
    return {'rows': out, 'count': len(out), 'summary': _tr('「{q}」找到 {len} 条：', q=q, len=len(out)) + _tr('；').join(f"{x['entity_label']} {x['code']} {x['label']}".strip() for x in out[:10])
            if out else _tr('没有找到「{q}」', q=q)}


@tool('get_record', '读一条记录', '读一条 MES 记录的全部字段（引用字段带中文名）。entity 用 search 返回的实体类型；id 或 code（编号 / 零件码）给一个。',
      {'entity': {'type': 'string'}, 'id': {'type': 'integer'}, 'code': {'type': 'string'}}, ['entity'],
      phrase=lambda a: _tr('正在读取 {v} 的详情…', v=a.get('code') or a.get('id') or ''))
def t_get_record(a):
    from ..crud import attach_labels, out
    ent = _s(a.get('entity'), 60)
    if ent not in BY_NAME or ent in HIDDEN:
        raise ToolError(_tr('没有这种实体：{ent}', ent=ent))
    need(perms.entity_perm(ent, 'read'))
    t = TABLES[ent]
    with db.engine.connect() as c:
        if a.get('id') not in (None, ''):
            r = db.get(c, ent, int(_num(a['id'], 0)))
        else:
            key = _s(a.get('code'), 120)
            r = None
            for col in ('code', 'sn', 'name', 'title'):
                if col in t.c and key:
                    r = db.get(c, ent, **{col: key})
                    if r:
                        break
        if not r:
            raise ToolError(_tr('记录不存在'))
        u = actor()
        if u is not None and not perms.in_scope(u, ent, r):
            raise ToolError(_tr('记录不存在'))
        attach_labels(c, ent, [r])
    rec = out([r])[0]
    e = BY_NAME[ent]
    show = {f.label: (f.enum or {}).get(rec.get(f.name), rec.get(f.name + '__label', rec.get(f.name))) for f in e.fields
            if f.name not in ('password_hash',) and rec.get(f.name) not in (None, '')}
    return {'rows': [rec], 'count': 1, 'data': {'entity': ent, 'entity_label': e.label, 'fields': show},
            'summary': f"{e.label} " + _tr('，').join(_tr('{k}：{v}', k=k, v=v) for k, v in list(show.items())[:16])}


# ───────────────────────── 业务查询（包一层 agent/queries，保持同一口径） ─────────────────────────
def _q(kind, a, keys):
    from ..agent.queries import run_query
    try:
        return run_query(kind, {k: a.get(k) for k in keys if a.get(k) not in (None, '')})
    except ValueError as ex:
        raise ToolError(str(ex))


@tool('query_work_orders', '查工单', '工单与进度：status=open（在制，默认）/ draft / released / in_progress / completed / all；late_only=true 只看延误。',
      {'status': {'type': 'string'}, 'late_only': {'type': 'boolean'}, 'limit': {'type': 'integer'}}, perm='production.work_order.read',
      phrase=lambda a: _tr('正在读取工单进度…'))
def t_wo(a):
    need('production.work_order.read')
    r = _q('work_orders', a, ['status', 'late_only', 'limit'])
    r['blocks'] = [_table(_tr('工单进度'), [('code', _tr('工单'), False, None), ('product', _tr('产品'), False, None), ('qty_done', _tr('完工'), True, 'int'), ('qty', _tr('计划'), True, 'int'),
                                    ('due', _tr('交期'), False, None), ('eta', _tr('预计完工'), False, None)], [{**x, 'due': (x.get('due') or '')[:16], 'eta': (x.get('eta') or '')[:16]} for x in r['rows']])] if r['rows'] else []
    return r


@tool('query_production', '查产量', '一段时间的产量：浇注 / 来料、机加、检测、入库、发运件数，按工序的报工合格 / 不良。'
      'period：today / yesterday / shift / last_shift / 8h / 3d，或 day=YYYY-MM-DD。',
      {'period': {'type': 'string'}, 'day': {'type': 'string'}, 'hours': {'type': 'number'}}, perm='production.report.read',
      phrase=lambda a: _tr('正在统计{v}的产量…', v=a.get('period') or a.get('day') or _tr('这段时间')))
def t_production(a):
    need('production.report.read')
    s, e, lab = period(a, 8)
    pe, rt = TABLES['part_event'], TABLES['report']
    with db.engine.connect() as c:
        ev = dict(c.execute(sa.select(pe.c.event, sa.func.count()).where((pe.c.t >= s) & (pe.c.t < e)).group_by(pe.c.event)).all())
        rows = c.execute(sa.select(TABLES['process'].c.name, sa.func.sum(rt.c.qty_good), sa.func.sum(rt.c.qty_ng)).select_from(
            rt.outerjoin(TABLES['process'], TABLES['process'].c.id == rt.c.process)).where((rt.c.start >= s) & (rt.c.start < e))
            .group_by(TABLES['process'].c.name)).all()
    made = ev.get('poured', 0) + ev.get('received', 0)
    k = {'castings': made, 'machined': ev.get('machined', 0), 'inspected': ev.get('inspected', 0), 'stored': ev.get('stored', 0), 'shipped': ev.get('shipped', 0),
         'scrap': ev.get('scrap', 0), 'scrap_rate': round(ev.get('scrap', 0) / made, 4) if made else 0}
    by = [{'process': n or _tr('其他'), 'good': int(g or 0), 'ng': int(b or 0)} for n, g, b in rows]
    blocks = [{'type': 'kpis', 'title': _tr('{lab}产量', lab=lab), 'items': [{'label': _tr('浇注 / 来料'), 'value': k['castings'], 'format': 'int', 'unit': '件'},
                                                             {'label': _tr('机加'), 'value': k['machined'], 'format': 'int', 'unit': '件'},
                                                             {'label': _tr('入库'), 'value': k['stored'], 'format': 'int', 'unit': '件'},
                                                             {'label': _tr('废品率'), 'value': k['scrap_rate'], 'format': 'pct', 'good': 'down'}]}]
    if by:
        blocks.append(_table(_tr('按工序报工'), [('process', _tr('工序'), False, None), ('good', _tr('合格'), True, 'int'), ('ng', _tr('不良'), True, 'int')], by))
    return {'rows': by, 'count': len(by), 'data': {**k, 'period': lab, 'start': jsonable(s), 'end': jsonable(e)}, 'blocks': blocks,
            'summary': _tr('{lab}：浇注/来料 {castings} 件，机加 {machined}，检测 {inspected}，入库 {stored}，发运 {shipped}；报废 {scrap} 件，废品率 {v:.2f}%。', lab=lab, castings=k['castings'], machined=k['machined'], inspected=k['inspected'], stored=k['stored'], shipped=k['shipped'], scrap=k['scrap'], v=k['scrap_rate'] * 100) + (_tr('报工：') + _tr('；').join(_tr('{process} 合格 {good} 不良 {ng}', process=x['process'], good=x['good'], ng=x['ng']) for x in by) if by else '')}


@tool('query_quality', '查质量', '不合格品与缺陷帕累托、SPC：period 或 hours（默认 24 小时）。', {'period': {'type': 'string'}, 'day': {'type': 'string'}, 'hours': {'type': 'number'}},
      perm='quality.ncr.read', phrase=lambda a: _tr('正在统计不合格品和缺陷…'))
def t_quality(a):
    need('quality.ncr.read')
    s, e, lab = period(a, 24)
    nt, dt = TABLES['ncr'], TABLES['defect_code']
    with db.engine.connect() as c:
        par = c.execute(sa.select(dt.c.name, sa.func.count(), sa.func.sum(nt.c.qty)).select_from(nt.outerjoin(dt, dt.c.id == nt.c.defect))
                        .where((nt.c.found_at >= s) & (nt.c.found_at < e)).group_by(dt.c.name).order_by(sa.func.count().desc())).all()
        opened = c.execute(sa.select(sa.func.count()).select_from(nt).where((nt.c.found_at >= s) & (nt.c.found_at < e) & (nt.c.status != 'closed'))).scalar() or 0
    tot = sum(n for _, n, _q in par) or 0
    acc, rows = 0, []
    for name, n, q in par:
        acc += n
        rows.append({'defect': name or _tr('未分类'), 'n': n, 'qty': int(q or n), 'cum': round(acc / tot, 4) if tot else 0})
    blocks = []
    if rows:
        blocks.append({'type': 'chart', 'kind': 'bar', 'title': _tr('{lab}缺陷帕累托', lab=lab), 'x': [r['defect'] for r in rows[:10]], 'unit': '条',
                       'series': [{'name': _tr('不合格品'), 'data': [r['n'] for r in rows[:10]]}]})
        blocks.append(_table(_tr('缺陷排行'), [('defect', _tr('缺陷'), False, None), ('n', _tr('条数'), True, 'int'), ('cum', _tr('累计占比'), True, 'pct')], rows))
    return {'rows': rows, 'count': len(rows), 'data': {'total': tot, 'open': opened, 'period': lab}, 'blocks': blocks,
            'summary': _tr('{lab}不合格品 {tot} 条（未关闭 {opened}）', lab=lab, tot=tot, opened=opened) + (_tr('，按缺陷：') + _tr('、').join(f"{r['defect']} {r['n']}" for r in rows[:8]) if rows else '') + _tr('。')}


@tool('query_equipment', '查设备', '设备清单（不给 equipment）或某台设备的状态、最近维修 / 报警 / 保养计划 / 刀具。', {'equipment': {'type': 'string', 'description': '设备编号，如 CNC1'}},
      perm='equipment.equipment.read', phrase=lambda a: _tr('正在读取{v}的履历…', v=a.get('equipment') or _tr('设备')))
def t_equipment(a):
    need('equipment.equipment.read')
    if a.get('equipment'):
        with db.engine.connect() as c:
            find_equipment(c, a['equipment'])
        return _q('equipment', a, ['equipment'])
    ids = eq_scope_ids()
    t = TABLES['equipment']
    with db.engine.connect() as c:
        rows = db.rows(c, 'equipment', *([t.c.id.in_(ids)] if ids is not None else []), order=t.c.code)
    from ..schema import EQUIP_T
    rr = [{'code': r['code'], 'name': r['name'], 'type': EQUIP_T.get(r['etype'], r['etype']), 'status': r['status'], 'ideal_cycle_s': r['ideal_cycle_s']} for r in rows]
    return {'rows': rr, 'count': len(rr), 'summary': _tr('设备：') + _tr('、').join(_tr('{code} {name}（{v}）', code=r['code'], name=r['name'], v=r['status'] or '—') for r in rr)}


@tool('query_oee', '查 OEE', 'OEE 与损失（可用率 / 性能率 / 质量率、停机 / 换型 / 待机小时），从低到高。equipment 可选；period 或 hours（默认 24）。',
      {'equipment': {'type': 'string'}, 'period': {'type': 'string'}, 'hours': {'type': 'number'}}, perm='equipment.equipment.read',
      phrase=lambda a: _tr('正在计算{v}的 OEE…', v=a.get('equipment') or _tr('各设备')))
def t_oee(a):
    need('equipment.equipment.read')
    from .equipment import COUNT_EVENTS, oee_of
    s, e, lab = period(a, 24)
    ids = eq_scope_ids()
    with db.engine.connect() as c:
        eqs = [find_equipment(c, a['equipment'])] if a.get('equipment') else db.rows(c, 'equipment', TABLES['equipment'].c.etype.in_(list(COUNT_EVENTS)),
                                                                                       order=TABLES['equipment'].c.code)
        rows = []
        for x in eqs:
            if ids is not None and x['id'] not in ids:
                continue
            o = oee_of(c, x, s, e)
            if o and o['OEE'] is not None:
                rows.append({'code': x['code'], 'name': x['name'], 'OEE': o['OEE'], 'A': o['A'], 'P': o['P'], 'Q': o['Q'], 'good': o['good'], 'bad': o['bad'],
                             **{f'loss_{k}': v for k, v in (o['loss'] or {}).items()}})
    rows.sort(key=lambda r: r['OEE'])
    loss = defaultdict(float)
    for r in rows:
        for k, v in r.items():
            if k.startswith('loss_') and k != 'loss_运行':
                loss[k[5:]] += v or 0
    top = sorted(loss.items(), key=lambda x: -x[1])
    blocks = []
    if rows:
        blocks.append({'type': 'chart', 'kind': 'hbar', 'title': _tr('{lab} 各设备 OEE', lab=lab), 'x': [r['code'] for r in rows[:12]], 'format': 'pct',
                       'series': [{'name': 'OEE', 'data': [r['OEE'] for r in rows[:12]]}]})
    if top:
        blocks.append(_table(_tr('OEE 损失 Top 5（小时）'), [('loss', _tr('损失'), False, None), ('hours', _tr('小时'), True, 'num')], [{'loss': k, 'hours': round(v, 2)} for k, v in top[:5]]))
    return {'rows': rows, 'count': len(rows), 'data': {'loss_hours': dict(loss), 'period': lab}, 'blocks': blocks,
            'summary': (_tr('{lab} OEE（从低到高）：', lab=lab) + _tr('；').join(_tr('{code} {v:.0f}%（可用 {v2:.0f}% 性能 {v3:.0f}% 质量 {v4:.0f}%）', code=r['code'], v=r['OEE'] * 100, v2=r['A'] * 100, v3=r['P'] * 100, v4=r['Q'] * 100) for r in rows[:8])
                        + (_tr('。损失合计：') + _tr('、').join(f'{k} {v:.1f}h' for k, v in top) if top else '')) if rows else _tr('{lab}没有 OEE 数据', lab=lab)}


@tool('query_maintenance', '查保养与维修', '到期 / 逾期的点检保养计划，和还没修好的维修工单。', {'equipment': {'type': 'string'}}, perm='equipment.maint_plan.read',
      phrase=lambda a: _tr('正在查到期的保养和未完成的维修…'))
def t_maint(a):
    need('equipment.maint_plan.read')
    from ..crud import attach_labels
    today = clock.now().date()
    ids = eq_scope_ids()
    mp, ro = TABLES['maint_plan'], TABLES['repair_order']
    with db.engine.connect() as c:
        eq = find_equipment(c, a['equipment']) if a.get('equipment') else None
        plans = db.rows(c, 'maint_plan', sa.or_(mp.c.next_due.is_(None), mp.c.next_due <= today), *([mp.c.equipment == eq['id']] if eq else []), order=mp.c.next_due)
        reps = db.rows(c, 'repair_order', ro.c.status.in_(('reported', 'assigned', 'repairing')), *([ro.c.equipment == eq['id']] if eq else []), order=ro.c.reported_at)
        attach_labels(c, 'maint_plan', plans)
        attach_labels(c, 'repair_order', reps)
    plans = [p for p in plans if ids is None or p['equipment'] in ids or p['equipment'] is None]
    reps = [r for r in reps if ids is None or r['equipment'] in ids]
    now = clock.now()
    pr = [{'code': p['code'], 'name': p['name'], 'equipment': p.get('equipment__label') or '', 'next_due': db.jsonable(p['next_due']) or '',
           'overdue_days': (today - p['next_due']).days if p['next_due'] else None} for p in plans]
    rr = [{'code': r['code'], 'equipment': r.get('equipment__label') or '', 'fault': r['fault'], 'status': r['status'],
           'open_h': round(((now - r['reported_at']).total_seconds() / 3600) if r['reported_at'] else 0, 1)} for r in reps]
    blocks = []
    if pr:
        blocks.append(_table(_tr('到期 / 逾期保养'), [('code', _tr('计划'), False, None), ('equipment', _tr('设备'), False, None), ('name', _tr('内容'), False, None), ('overdue_days', _tr('逾期天'), True, 'int')],
                             sorted(pr, key=lambda x: -(x['overdue_days'] or 0))))
    if rr:
        blocks.append(_table(_tr('未完成维修'), [('code', _tr('维修单'), False, None), ('equipment', _tr('设备'), False, None), ('fault', _tr('故障'), False, None), ('open_h', _tr('已开小时'), True, 'num')], rr))
    return {'rows': pr + rr, 'count': len(pr) + len(rr), 'data': {'plans': pr, 'repairs': rr}, 'blocks': blocks,
            'summary': _tr('到期 / 逾期保养 {len} 项', len=len(pr)) + (_tr('：') + _tr('、').join(_tr('{equipment} {name}（逾期 {overdue_days} 天）', equipment=x['equipment'], name=x['name'], overdue_days=x['overdue_days']) for x in pr[:6] if x['overdue_days'] is not None) if pr else '')
                       + _tr('；未完成维修 {len} 单', len=len(rr)) + (_tr('：') + _tr('、').join(f"{x['code']} {x['equipment']} {x['fault']}" for x in rr[:6]) if rr else '') + _tr('。')}


@tool('query_energy', '查能耗', '各设备用电（kWh）和能耗异常：某小时用电明显高于过去 7 天同一小时的中位数。period / hours（默认 24）。',
      {'period': {'type': 'string'}, 'hours': {'type': 'number'}, 'ratio': {'type': 'number', 'description': '异常倍数，默认 1.3'}}, perm='page.energy',
      phrase=lambda a: _tr('正在统计用电和能耗异常…'))
def t_energy(a):
    need('page.energy')
    s, e, lab = period(a, 24)
    ratio = _num(a.get('ratio'), 1.3) or 1.3
    et, eqt = TABLES['energy_hour'], TABLES['equipment']
    ids = eq_scope_ids()
    with db.engine.connect() as c:
        rows = c.execute(sa.select(et.c.equipment, eqt.c.code, eqt.c.name, et.c.hour, et.c.kwh).join(eqt, eqt.c.id == et.c.equipment)
                         .where((et.c.hour >= s - timedelta(days=7)) & (et.c.hour < e))).all()
    base = defaultdict(list)
    cur = []
    tot = defaultdict(float)
    for eid, code, name, hour, kwh in rows:
        if ids is not None and eid not in ids:
            continue
        hour = _ms(hour) if not isinstance(hour, datetime) else hour
        if hour < s:
            base[(code, hour.hour)].append(kwh or 0)
        else:
            cur.append((code, name, hour, kwh or 0))
            tot[(code, name)] += kwh or 0
    anom = []
    for code, name, hour, kwh in cur:
        b = base.get((code, hour.hour)) or []
        if len(b) >= 3:
            med = statistics.median(b)
            if med > 0.5 and kwh > med * ratio and kwh - med >= 1:
                anom.append({'code': code, 'name': name, 'hour': hour.strftime('%m-%d %H:00'), 'kwh': round(kwh, 1), 'baseline': round(med, 1),
                             'excess_kwh': round(kwh - med, 1)})
    anom.sort(key=lambda x: -x['excess_kwh'])
    by = sorted([{'code': k[0], 'name': k[1], 'kwh': round(v, 1)} for k, v in tot.items()], key=lambda x: -x['kwh'])
    blocks = []
    if by:
        blocks.append({'type': 'chart', 'kind': 'hbar', 'title': _tr('{lab}各设备用电', lab=lab), 'x': [x['code'] for x in by[:10]], 'unit': 'kWh',
                       'series': [{'name': _tr('用电'), 'data': [x['kwh'] for x in by[:10]]}]})
    if anom:
        blocks.append(_table(_tr('能耗异常（高于同时段中位数）'), [('code', _tr('设备'), False, None), ('hour', _tr('小时'), False, None), ('kwh', 'kWh', True, 'num'),
                                                     ('baseline', _tr('平时'), True, 'num'), ('excess_kwh', _tr('多用'), True, 'num')], anom))
    return {'rows': anom, 'count': len(anom), 'data': {'by_equipment': by, 'total_kwh': round(sum(tot.values()), 1), 'period': lab}, 'blocks': blocks,
            'summary': _tr('{lab}用电 {sum:.0f} kWh；能耗异常 {len} 处', lab=lab, sum=sum(tot.values()), len=len(anom)) + (_tr('：') + _tr('；').join(_tr('{code} {hour} {kwh} kWh（平时 {baseline}）', code=x['code'], hour=x['hour'], kwh=x['kwh'], baseline=x['baseline']) for x in anom[:6]) if anom else '') + _tr('。')}


# ───────────────────────── 工厂模型、点位 ─────────────────────────
@tool('factory_model', '读工厂模型', '工厂 → 车间 → 产线 → 工位 / 设备 的层级（只含你数据范围内的），设备带类型、理想节拍、有没有动作信号 / 节拍模型。',
      {}, perm='master.plant.read', phrase=lambda a: _tr('正在读取工厂模型…'))
def t_factory(a):
    need('master.plant.read')
    from ..schema import EQUIP_T, cycle_model_t
    u = actor()
    sc = perms.user_scope(u) if u is not None else None
    vis = perms.scope_sets(sc) if sc else None
    ids = eq_scope_ids()
    with db.engine.connect() as c:
        plants, wss, lns = db.rows(c, 'plant'), db.rows(c, 'workshop'), db.rows(c, 'line')
        eqs = db.rows(c, 'equipment', order=TABLES['equipment'].c.code)
        wst = db.rows(c, 'workstation')
        models = {r.equipment: r.status for r in c.execute(sa.select(cycle_model_t.c.equipment, cycle_model_t.c.status))}
        act = {r[0] for r in c.execute(sa.select(ts_point_t.c.equipment).where(ts_point_t.c.name.like('Actions/%')).distinct())}

    def ok(kind, rid):
        return vis is None or rid in vis[kind]
    tree = []
    for p in plants:
        if not ok('plant', p['id']):
            continue
        pn = {'id': p['id'], 'code': p['code'], 'name': p['name'], 'workshops': []}
        for w in [w for w in wss if w['plant'] == p['id'] and ok('workshop', w['id'])]:
            wn = {'id': w['id'], 'code': w['code'], 'name': w['name'], 'lines': []}
            for ln in [x for x in lns if x['workshop'] == w['id'] and ok('line', x['id'])]:
                wn['lines'].append({'id': ln['id'], 'code': ln['code'], 'name': ln['name'],
                                    'workstations': [{'code': s['code'], 'name': s['name'], 'group': s['group'], 'equipment': s['equipment']} for s in wst if s['line'] == ln['id']],
                                    'equipment': [_eq_brief(x, EQUIP_T, models, act) for x in eqs if x['line'] == ln['id'] and (ids is None or x['id'] in ids)]})
            pn['workshops'].append(wn)
        tree.append(pn)
    loose = [_eq_brief(x, EQUIP_T, models, act) for x in eqs if not x['line'] and (ids is None or x['id'] in ids)]
    n_eq = sum(len(ln['equipment']) for p in tree for w in p['workshops'] for ln in w['lines']) + len(loose)
    lines_txt = _tr('；').join(_tr('{name}：', name=ln['name']) + _tr('、').join(x['code'] for x in ln['equipment']) for p in tree for w in p['workshops'] for ln in w['lines'])
    return {'data': {'plants': tree, 'unassigned_equipment': loose}, 'count': n_eq, 'summary': _tr('工厂 {len} 个、设备 {n_eq} 台。', len=len(tree), n_eq=n_eq) + lines_txt}


def _eq_brief(x, EQUIP_T, models, act):
    return {'id': x['id'], 'code': x['code'], 'name': x['name'], 'type': EQUIP_T.get(x['etype'], x['etype']), 'status': x['status'], 'ideal_cycle_s': x['ideal_cycle_s'],
            'action_signals': x['id'] in act, 'cycle_model': models.get(x['id'])}


ROLE_GUESS = (('Actions/ProgramStep', 'step'), ('Actions/', 'action'), ('Common/State', 'state'), ('Common/Alarm', 'alarm'), ('PowerKW', 'power'), ('EnergyKWh', 'energy'),
              ('Count', 'count'), ('Spindle/Load', 'load'), ('Temp', 'temp'), ('Quality/', 'quality'))


def point_meta(c):
    """tag_point（点表，M2 接入中心维护）里的含义 / 单位 / 用途：(设备 id, 点位名) → {desc, unit, role}。"""
    tp = TABLES['tag_point']
    out = {}
    for r in c.execute(sa.select(tp.c.equipment, tp.c.name, tp.c.desc, tp.c.unit, tp.c.role)).all():
        out[(r[0], r[1])] = {'desc': r[2] or '', 'unit': r[3] or '', 'role': r[4] or ''}
    return out


def guess_role(name):
    for pat, role in ROLE_GUESS:
        if pat in (name or ''):
            return role
    return 'other'


def _point_check(c, key):
    """点位 key / id → ts_point 行；检查设备的数据范围。"""
    t = ts_point_t
    k = str(key).strip()
    r = c.execute(sa.select(t).where(t.c.key == k)).mappings().first()
    if not r and k.isdigit():
        r = c.execute(sa.select(t).where(t.c.id == int(k))).mappings().first()
    if not r:
        cand = c.execute(sa.select(t).where(t.c.key.like(f'%{k}')).limit(2)).mappings().all()
        if len(cand) == 1:
            r = cand[0]
    if not r:
        raise ToolError(_tr('没有采集点 {k}（用 list_points 查点位 key）', k=k))
    ids = eq_scope_ids()
    if ids is not None and r['equipment'] is not None and r['equipment'] not in ids:
        raise ToolError(_tr('采集点 {k} 所属设备不在你的数据范围内', k=k))
    return dict(r)


@tool('list_points', '查采集点', '设备的采集点（时序点位）：key、含义、单位、用途（state / action / step / load …）、最新值。equipment 或 q（点位名关键词）至少给一个更好。',
      {'equipment': {'type': 'string'}, 'q': {'type': 'string'}, 'role': {'type': 'string', 'description': '只看某种用途，如 action、step'},
       'limit': {'type': 'integer'}}, perm='equipment.ts.read', group='ts', phrase=lambda a: _tr('正在读取{v}的采集点…', v=a.get('equipment') or ''))
def t_points(a):
    need('equipment.ts.read')
    t = ts_point_t
    lim = max(1, min(500, int(_num(a.get('limit'), 200))))
    ids = eq_scope_ids()
    with db.engine.connect() as c:
        q = sa.select(t)
        if a.get('equipment'):
            eq = find_equipment(c, a['equipment'])
            q = q.where(t.c.equipment == eq['id'])
        if a.get('q'):
            q = q.where(t.c.key.ilike(f"%{_s(a['q'], 60)}%"))
        if ids is not None:
            q = q.where(sa.or_(t.c.equipment.is_(None), t.c.equipment.in_(ids)))
        rows = [dict(r) for r in c.execute(q.order_by(t.c.key).limit(lim * 3)).mappings()]
        meta = point_meta(c)
        codes = {r.id: r.code for r in c.execute(sa.select(TABLES['equipment'].c.id, TABLES['equipment'].c.code))}
    out = []
    for r in rows:
        m = meta.get((r['equipment'], r['name'])) or {}
        role = m.get('role') or guess_role(r['name'])
        if a.get('role') and role != a['role']:
            continue
        out.append({'key': r['key'], 'equipment': codes.get(r['equipment']), 'name': r['name'], 'meaning': m.get('desc') or '', 'unit': r['unit'] or m.get('unit') or '',
                    'dtype': r['dtype'], 'role': role, 'last': r['last_text'] if r['last_text'] is not None else r['last_value'], 'last_ts': jsonable(r['last_ts'])})
        if len(out) >= lim:
            break
    return {'rows': out, 'count': len(out), 'summary': _tr('采集点 {len} 个：', len=len(out)) + _tr('；').join(_tr('{key}（{v}）={last}', key=x['key'], v=x['meaning'] or x['role'], last=x['last']) for x in out[:20])}


def _ts_args(a):
    from .. import ts
    with db.engine.connect() as c:
        p = _point_check(c, a.get('point') or '')
    s, e, lab = period(a, 1)
    return ts, p, s, e, lab


@tool('ts_range', '读时序原始值', '一个采集点一段时间的原始值（时间升序，最多 limit 条，默认 500）。时间：start/end（YYYY-MM-DD HH:MM:SS），或 period / hours（默认 1 小时）。',
      {'point': {'type': 'string', 'description': '点位 key，如 VF:CNC1/Monitoring/Spindle/Load'}, 'start': {'type': 'string'}, 'end': {'type': 'string'},
       'period': {'type': 'string'}, 'hours': {'type': 'number'}, 'limit': {'type': 'integer'}}, ['point'], perm='equipment.ts.read', group='ts',
      phrase=lambda a: _tr('正在读取 {v} 的时序数据…', v=_s(a.get('point'), 40)))
def t_ts_range(a):
    need('equipment.ts.read')
    ts, p, s, e, lab = _ts_args(a)
    rows = ts.raw(p['id'], s, e, limit=max(1, min(5000, int(_num(a.get('limit'), 500)))))
    vals = [r['value'] for r in rows if isinstance(r['value'], (int, float))]
    return {'rows': rows, 'count': len(rows), 'data': {'point': p['key'], 'unit': p['unit'], 'period': lab},
            'summary': _tr('{key} {lab}：{len} 个值', key=p['key'], lab=lab, len=len(rows)) + (_tr('，最小 {min:g}，最大 {max:g}，平均 {v:.3g}', min=min(vals), max=max(vals), v=sum(vals) / len(vals)) if vals else '')}


@tool('ts_rollup', '读时序汇总', '采集点的 1 分钟 / 1 小时汇总（n、最小、最大、平均、首末值；开关量带开启秒数和跳变次数）。',
      {'point': {'type': 'string'}, 'interval': {'type': 'string', 'enum': ['1m', '1h']}, 'start': {'type': 'string'}, 'end': {'type': 'string'},
       'period': {'type': 'string'}, 'hours': {'type': 'number'}}, ['point'], perm='equipment.ts.read', group='ts',
      phrase=lambda a: _tr('正在汇总 {v} 的趋势…', v=_s(a.get('point'), 40)))
def t_ts_rollup(a):
    need('equipment.ts.read')
    ts, p, s, e, lab = _ts_args(a)
    iv = a.get('interval') if a.get('interval') in ('1m', '1h') else ('1h' if (e - s).total_seconds() > 6 * 3600 else '1m')
    rows = ts.rollup(p['id'], s, e, iv)[:2000]
    blocks = [{'type': 'chart', 'kind': 'line', 'title': _tr('{name}（{iv} 平均）', name=p['name'], iv=iv), 'x': [r['bucket'][5:16] for r in rows[-60:]], 'unit': p['unit'] or '',
               'series': [{'name': _tr('平均'), 'data': [round(r['avg'], 3) if r['avg'] is not None else None for r in rows[-60:]]}]}] if rows else []
    return {'rows': rows, 'count': len(rows), 'data': {'point': p['key'], 'interval': iv, 'period': lab}, 'blocks': blocks,
            'summary': _tr('{key} {lab} 按 {iv} 汇总 {len} 段', key=p['key'], lab=lab, iv=iv, len=len(rows))}


@tool('ts_latest', '读最新值', '采集点的最新值：points（key 列表）或 equipment（整台设备）。', {'points': {'type': 'array', 'items': {'type': 'string'}}, 'equipment': {'type': 'string'}},
      perm='equipment.ts.read', group='ts', phrase=lambda a: _tr('正在读取{v}的实时值…', v=a.get('equipment') or ''))
def t_ts_latest(a):
    need('equipment.ts.read')
    from .. import ts
    with db.engine.connect() as c:
        if a.get('equipment'):
            eq = find_equipment(c, a['equipment'])
            res = ts.latest(equipment=eq['id'])
        else:
            keys = [str(k) for k in (a.get('points') or [])][:200]
            if not keys:
                raise ToolError(_tr('请给 points 或 equipment'))
            for k in keys:
                _point_check(c, k)
            res = ts.latest(keys)
    rows = [{'key': k, **v} for k, v in sorted(res.items())]
    return {'rows': rows, 'count': len(rows), 'summary': _tr('；').join(_tr('{key}={v}（{ts}）', key=r['key'], v=r['text'] if r['text'] is not None else r['value'], ts=r['ts']) for r in rows[:30])}


@tool('ts_events', '读开关量事件', '开关量 / 状态量 / 步号的事件：kind=rising（上升沿）/ falling（下降沿）/ both / segments（状态区间，含每段时长 dur_s）。',
      {'point': {'type': 'string'}, 'kind': {'type': 'string', 'enum': ['rising', 'falling', 'both', 'segments']}, 'start': {'type': 'string'}, 'end': {'type': 'string'},
       'period': {'type': 'string'}, 'hours': {'type': 'number'}, 'limit': {'type': 'integer'}}, ['point'], perm='equipment.ts.read', group='ts',
      phrase=lambda a: _tr('正在读取 {v} 的动作事件…', v=_s(a.get('point'), 40)))
def t_ts_events(a):
    need('equipment.ts.read')
    ts, p, s, e, lab = _ts_args(a)
    lim = max(1, min(5000, int(_num(a.get('limit'), 500))))
    kind = a.get('kind') or 'both'
    if kind == 'segments':
        rows = ts.segments(p['id'], s, e)[:lim]
        durs = [r['dur_s'] for r in rows if r['dur_s'] is not None and (r['value'] or 0)]
        return {'rows': rows, 'count': len(rows), 'data': {'point': p['key'], 'period': lab},
                'summary': _tr('{key} {lab} 状态区间 {len} 段', key=p['key'], lab=lab, len=len(rows)) + (_tr('，开启段时长中位数 {median:.3f} s', median=statistics.median(durs)) if durs else '')}
    rows = ts.edges(p['id'], s, e, kind)[:lim]
    return {'rows': rows, 'count': len(rows), 'data': {'point': p['key'], 'period': lab}, 'summary': _tr('{key} {lab} {kind} 事件 {len} 个', key=p['key'], lab=lab, kind=kind, len=len(rows))}


# ───────────────────────── 节拍分析、追溯、技能 ─────────────────────────
@tool('cycle_analysis', '节拍分析', '动作级节拍分析：给 line（产线编号 / 名称）看全线各工位节拍对比节拍目标（takt）、瓶颈工位和瓶颈动作、损失折算（秒/循环 → 件/天 → 元/年）；'
      '给 equipment 看一台设备每个动作的用时、对比标准时间和基线、按天漂移。period / hours 默认最近 8 小时。',
      {'line': {'type': 'string'}, 'equipment': {'type': 'string'}, 'period': {'type': 'string'}, 'hours': {'type': 'number'}, 'days': {'type': 'integer', 'description': '漂移趋势看几天，默认 7'}},
      perm='cycle.view', group='cycle', phrase=lambda a: _tr('正在分析{v}的动作节拍…', v=a.get('equipment') or a.get('line') or _tr('各工位')))
def t_cycle(a):
    need('cycle.view')
    from . import cycle
    s, e, lab = period(a, 8)
    if a.get('equipment'):
        with db.engine.connect() as c:
            eq = find_equipment(c, a['equipment'])
        r = cycle.station_report(eq['id'], s, e, days=int(_num(a.get('days'), 7)), cycles=0)
        return cycle.tool_out_station(r, lab)
    line = None
    if a.get('line'):
        with db.engine.connect() as c:
            line = find_line(c, a['line'])
    r = cycle.line_report(line['id'] if line else None, s, e)
    return cycle.tool_out_line(r, lab)


@tool('trace', '追溯', '一件一码追溯：sn（零件码）→ 全部工序事件、炉次 / 浇包、检验、不合格品、加工时的机床状态；或 lot（炉次 / 批次号）→ 这批零件的去向。',
      {'sn': {'type': 'string'}, 'lot': {'type': 'string'}}, perm='production.part.read', phrase=lambda a: _tr('正在追溯 {v}…', v=a.get('sn') or a.get('lot') or ''))
def t_trace(a):
    need('production.part.read')
    from .views import trace_data
    if a.get('sn'):
        with db.engine.connect() as c:
            d = trace_data(c, _s(a['sn'], 80))
        if not d:
            raise ToolError(_tr('没有零件 {sn}', sn=a['sn']))
        ev = d['events']
        return {'rows': ev, 'count': len(ev), 'data': {k: d[k] for k in ('part', 'heat', 'treatment', 'ncr', 'work_order')},
                'summary': _tr('零件 {sn}：', sn=a['sn']) + ' → '.join(_tr('{label}（{v} {v2}）', label=x['label'], v=x.get('station') or '', v2=(x.get('t') or '')[5:16]) for x in ev[:14])
                           + (_tr('；炉次 {get}', get=d['part'].get('heat')) if d['part'].get('heat') else '') + (_tr('；不合格品 {len} 条', len=len(d['ncr'])) if d['ncr'] else '')}
    lot = _s(a.get('lot'), 80)
    if not lot:
        raise ToolError(_tr('请给 sn 或 lot'))
    pt = TABLES['part']
    with db.engine.connect() as c:
        parts = db.rows(c, 'part', order=pt.c.id, heat=lot)
    st = defaultdict(int)
    for p in parts:
        st[p['status']] += 1
    rows = [{'sn': p['sn'], 'status': p['status'], 'station': p['station'], 'machine': p['machine']} for p in parts[:500]]
    return {'rows': rows, 'count': len(parts), 'data': {'status': dict(st)}, 'summary': _tr('批次 {lot}：{len} 件，', lot=lot, len=len(parts)) + _tr('、').join(f'{k} {v}' for k, v in st.items())}


@tool('run_skill', '运行技能', '运行一个已保存的技能（确定的查询计划，不调用大模型）：skill 是技能 id 或名称，params 是参数（如 {"line": "机加线", "period": "yesterday"}）。',
      {'skill': {'type': 'string'}, 'params': {'type': 'object'}}, ['skill'], perm='skill.use', group='skill',
      phrase=lambda a: _tr('正在运行技能「{v}」…', v=_s(a.get('skill'), 30)))
def t_run_skill(a):
    need('skill.use')
    from . import skills
    sk = skills.find(a.get('skill'), actor())
    if not sk:
        raise ToolError(_tr('没有这个技能，或你没有权限：{get}', get=a.get('skill')))
    r = skills.run(sk['id'], a.get('params') or {}, trigger='tool', user=actor(), narrative=False)
    if r['status'] != 'ok':
        raise ToolError(_tr('技能运行失败（{status}）：{get}', status=r['status'], get=r.get('error')))
    return {'summary': r.get('summary') or '', 'blocks': r.get('blocks') or [], 'data': {'run_id': r['id'], 'skill': sk['name'], 'params': r.get('params')}}


@tool('query_sql', '只读 SQL', '在 MES 数据库上执行一条只读 SELECT（最多 200 行），只能查你有权限的表和数据范围内的行。表结构见资源 mes://data-catalog。',
      {'sql': {'type': 'string'}}, ['sql'], phrase=lambda a: _tr('正在查数据库…'))
def t_sql(a):
    from .ai import run_sql
    try:
        r = run_sql(a.get('sql') or '')
    except ValueError as ex:
        raise ToolError(str(ex))
    except Exception as ex:
        raise ToolError(_tr('SQL 执行出错：{v}', v=str(ex).splitlines()[0][:300]))
    return {**r, 'count': len(r['rows']), 'summary': _tr('{len} 行：{join}', len=len(r['rows']), join=', '.join(r['columns']))}


@tool('kb_search', '查知识库', '在工厂知识库（作业指导书、SOP、判定标准）里检索相关段落。', {'q': {'type': 'string'}}, ['q'], perm='ai.kb_doc.read',
      phrase=lambda a: _tr('正在查知识库：「{v}」…', v=_s(a.get('q'), 30)))
def t_kb(a):
    need('ai.kb_doc.read')
    from .ai import kb_search
    refs = kb_search(a.get('q') or '')
    return {'rows': refs, 'count': len(refs), 'summary': _tr('；').join(_s(r.get('title'), 40) for r in refs[:6]) or _tr('知识库里没有相关内容')}


@tool('day_kpi', '某天汇总指标', '某天（YYYY-MM-DD，缺省当天）的生产、质量、设备、能耗汇总指标。', {'day': {'type': 'string'}}, perm='page.home',
      phrase=lambda a: _tr('正在汇总{v}的指标…', v=a.get('day') or _tr('今天')))
def t_day_kpi(a):
    need('page.home')
    from .ai import day_kpi
    k = day_kpi(a.get('day') or None)
    from ..agent.queries import q_kpi
    return {'data': k, 'summary': q_kpi({'day': a.get('day')})['summary']}


@tool('mes_query', 'MES 业务查询', 'MES 常用业务查询（和右下角助手同一套）：kind = work_orders / reports / oee / downtime / quality / ncr_detail / equipment / stock / shortage / andon / alarms / kpi。',
      {'kind': {'type': 'string'}, 'equipment': {'type': 'string'}, 'hours': {'type': 'number'}, 'status': {'type': 'string'}, 'id': {'type': 'string'}, 'day': {'type': 'string'},
       'limit': {'type': 'integer'}}, ['kind'], phrase=lambda a: _tr('正在读取业务数据…'))
def t_mes_query(a):
    from ..agent.queries import run_query
    try:
        return run_query(a.get('kind'), {k: v for k, v in a.items() if k != 'kind'})
    except ValueError as ex:
        raise ToolError(str(ex))


# ───────────────────────── 写：只起草（默认关闭） ─────────────────────────
DRAFT_OPS = ('repair_order', 'ncr', 'andon_raise', 'material_request', 'schedule_proposal', 'notify')


@tool('draft_action', '起草动作', '起草一个写操作（建维修工单 / 不合格品 / 安灯 / 物料申请 / 排程建议 / 通知）。只生成一张动作草稿进审批中心，有权限的人审批通过后才执行；'
      '调用后不要说"已完成"，要说"已起草，等待审批"。op 取值：' + ' / '.join(DRAFT_OPS) + '；payload 是该操作的字段（如 repair_order：{equipment, fault, level, cause}）。',
      {'op': {'type': 'string', 'enum': list(DRAFT_OPS)}, 'payload': {'type': 'object'}, 'reason': {'type': 'string'}}, ['op', 'payload'], perm='mcp.write', group='write', write=True,
      phrase=lambda a: _tr('正在起草动作…'))
def t_draft(a):
    need('mcp.write')
    from . import ai_drafts, ops
    u = actor()
    if u is None:
        raise ToolError(_tr('需要调用者身份'))
    if a.get('op') not in DRAFT_OPS:
        raise ToolError(_tr('op 只能是：{join}', join=_tr('、').join(DRAFT_OPS)))
    try:
        d = ai_drafts.create(u, a['op'], a.get('payload') or {}, source='mcp', summary=_s(a.get('reason'), 500))
    except ops.OpError as ex:
        raise ToolError(_tr('检查没通过：{ex}', ex=ex))
    return {'data': {'draft_id': d['id'], 'title': d['title'], 'status': d['status'], 'changes': d['changes'], 'approver_role': d['role_label']},
            'summary': _tr('已起草「{title}」（草稿 #{id}），等待{role_label}在审批中心审批；审批通过前不会执行。', title=d['title'], id=d['id'], role_label=d['role_label'])}


def write_tools_enabled() -> bool:
    with db.engine.connect() as c:
        return (db.setting(c, 'mcp_write_tools') or '0') == '1'


# ───────────────────────── 调用 ─────────────────────────
def available(user, include_writes=None) -> list[Tool]:
    """这个人能用的工具（没有对应查看权限的不列出）。写工具只在打开 mcp_write_tools 且有 mcp.write 时出现。"""
    wr = write_tools_enabled() if include_writes is None else include_writes
    out = []
    for t in TOOLS.values():
        if t.write and not wr:
            continue
        if user is not None and t.perm:
            ps = t.perm if isinstance(t.perm, (tuple, list)) else (t.perm,)
            if not any(perms.can(user, p) for p in ps):
                continue
        out.append(t)
    return out


def call(name: str, args: dict, user=None):
    """执行工具（user 缺省 = 当前上下文的人）。返回结果 dict；参数 / 权限问题抛 ToolError。"""
    t = TOOLS.get(name)
    if not t:
        raise ToolError(_tr('没有这个工具：{name}', name=name))
    if t.write and not write_tools_enabled():
        raise ToolError(_tr('写操作工具没有开启（系统参数 mcp_write_tools）'))
    if user is not None and user is not actor():
        with context.acting(user, context.current_source() if context.current_source() != 'system' else 'ai'):
            return jsonable(t.fn(dict(args or {})))
    return jsonable(t.fn(dict(args or {})))


def phrase(name, args):
    t = TOOLS.get(name)
    return t.phrase(args) if t else _tr('正在处理…')


def register_data_tools(data_tools: dict):
    """把本层的工具登记进 agent/tools.DATA_TOOLS（右下角助手、智能体节点可选），已有同名的不覆盖。"""
    for n in ASSISTANT_TOOLS:
        t = TOOLS[n]
        data_tools.setdefault(n, {'label': t.title, 'schema': {'description': t.description, 'parameters': t.schema()}, 'ai_tools': True})


def dumps(res, n=12000):
    return json.dumps(res, ensure_ascii=False, default=str)[:n]
