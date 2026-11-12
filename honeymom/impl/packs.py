"""行业模板（行业包）：版本化的数据，不是代码。一个包 = honeymom/packs/<编码>/pack.json（+ 可选的演示数据 demo.json 和附件 files/）。
格式说明见 docs/dev/config.md「行业模板」。内置：common 通用（总是先应用）、foundry 铸造、machining 机加、injection 注塑、assembly 装配。

包里有：
  enums            枚举选项（工序类型、设备类型、车间类型、缺陷类别…）：只有启用了这个包，它的选项才出现（4.7 的铸造 / 机加选项归铸造 / 机加包）
  modules          可选模块（foundry 炉次 / 配料 / 球化，machining 刀具 / NC 程序…）：菜单、导入器、报表数据集按它显示
  device_templates 适用的设备类型模板（M2 的 device_template 编码）
  maint_templates  按设备类型的点检保养计划模板（导入设备后自动建）
  kpis / reports   默认 KPI、报表集（应用时建成共享报表）
  config           配置数据（工序、缺陷代码、检验模板、SOP…）：步骤列表，每步走通用导入框架
  demo             演示数据（demo.json：steps + after_equipment）
应用 = 一个事务：依赖的包（requires）先、配置数据、（可选）演示数据、报表、导入设备后的数据；已有的不覆盖（"只新增"），
预览 = 同样跑一遍再回滚，报告每一步新增 / 跳过多少。应用记录在 pack_applied。
"""
from ..env import get as env_get
from ..i18n import _tr
from .. import i18n as _i18n
import copy
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi import HTTPException

from .. import audit, context, db
from ..schema import BY_NAME, TABLES

PACK_DIR = env_get('MOM_PACKS') or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'packs')
DEMO_PACKS = ['foundry', 'machining']           # 演示（seed）用的行业模板：示范工厂 = 铸造 + 机加
_lock = threading.Lock()
_cache = {'at': 0.0, 'applied': None}


class PackError(Exception):
    pass


# ───────────────────────── 读包 ─────────────────────────
_packs: dict = {}


def load_all(refresh=False) -> dict:
    if _packs and not refresh:
        return _packs
    out = {}
    if os.path.isdir(PACK_DIR):
        for d in sorted(os.listdir(PACK_DIR)):
            p = os.path.join(PACK_DIR, d, 'pack.json')
            if os.path.isfile(p):
                with open(p, encoding='utf-8') as f:
                    pk = json.load(f)
                pk['_dir'] = os.path.join(PACK_DIR, d)
                out[pk['code']] = pk
    _packs.clear()
    _packs.update(out)
    return _packs


def get(code) -> dict:
    p = load_all().get(code)
    if not p:
        raise HTTPException(404, _tr('没有行业模板 {code}', code=code))
    return _localized(p)


def _localized(p):
    """Packs are authored in Chinese; in English (UI language or English seeding) their texts come from locale/data-en.json."""
    from .. import i18n
    if i18n.seeding_en() or i18n.lang() == 'en':
        return i18n.seed_value(p)
    return p


def demo_of(code) -> dict | None:
    pk = get(code)
    ref = pk.get('demo')
    if not ref:
        return None
    p = os.path.join(get(ref)['_dir'], 'demo.json')
    if not os.path.isfile(p):
        return None
    with open(p, encoding='utf-8') as f:
        d = json.load(f)
    d['_dir'] = get(ref)['_dir']
    d['_from'] = ref
    return _localized(d)


def closure(codes) -> list:
    """依赖展开 + 排序：common 最先，然后按 requires 拓扑序。"""
    order, seen = [], set()

    def visit(c, stack=()):
        if c in seen:
            return
        if c in stack:
            raise PackError(_tr('行业模板循环依赖：{join}', join=" → ".join(stack + (c,))))
        for r in get(c).get('requires') or []:
            visit(r, stack + (c,))
        seen.add(c)
        order.append(c)
    for c in ['common'] + list(codes):
        if c in load_all():
            visit(c)
    return order


# ───────────────────────── 已应用 ─────────────────────────
def T():
    from ..schema_impl import T as _T
    return _T['pack_applied']


def applied(conn=None, refresh=False) -> list:
    """已应用的包（按应用顺序，去重）：[{pack, version, demo, applied_at…}]。"""
    if not refresh and conn is None and _cache['applied'] is not None and time.time() - _cache['at'] < 5:
        return _cache['applied']
    def q(c):
        rows = [dict(r) for r in c.execute(sa.select(T()).order_by(T().c.id)).mappings()]
        out = {}
        for r in rows:
            prev = out.get(r['pack'])
            out[r['pack']] = {**r, 'demo': bool(r['demo']) or bool(prev and prev['demo'])}
        return list(out.values())
    try:
        if conn is not None:
            return q(conn)
        with db.engine.connect() as c:
            res = q(c)
    except Exception:
        res = []
    _cache['applied'], _cache['at'] = res, time.time()
    return res


def applied_codes(conn=None) -> list:
    return [r['pack'] for r in applied(conn)]


def enabled_modules(conn=None) -> set:
    mods = set()
    for code in applied_codes(conn):
        if code in load_all():
            mods |= set(get(code).get('modules') or [])
    return mods


def kpis() -> list:
    out = []
    for code in applied_codes():
        if code in load_all():
            out += [{**k, 'pack': code} for k in get(code).get('kpis') or []]
    return out


# ───────────────────────── 枚举 ─────────────────────────
_orig: dict = {}            # 'entity.field' -> schema 里原来的选项（第一次刷新时记下）


def _field(path):
    ent, fn = path.split('.')
    e = BY_NAME.get(ent)
    return next((f for f in e.fields if f.name == fn), None) if e else None


def refresh_enums(conn=None):
    """选项 = schema 里的（去掉"属于某个包但这个包没启用"的）+ 已启用包的。原地改字段的 enum 字典，/api/meta、导入、导出都跟着变。"""
    allp = load_all()
    owned: dict = {}
    for pk in allp.values():
        for path, opts in (pk.get('enums') or {}).items():
            owned.setdefault(path, set()).update(opts)
    active: dict = {}
    for code in applied_codes(conn):
        for path, opts in (allp.get(code, {}).get('enums') or {}).items():
            active.setdefault(path, {}).update(opts)
    with _lock:
        for path in set(owned) | set(active):
            f = _field(path)
            if f is None or f.enum is None:
                continue
            if path not in _orig:
                _orig[path] = dict(f.enum)
            base = _orig[path]
            new = {k: v for k, v in base.items() if k not in owned.get(path, set()) or k in active.get(path, {})}
            for k, v in active.get(path, {}).items():
                new.setdefault(k, v)
            f.enum.clear()
            f.enum.update(new)


_enum_at = {'t': 0.0}


def _spec(out):
    """/api/meta 前刷新（多进程：别的进程应用了包，最多 5 秒后这里也变）。"""
    if time.time() - _enum_at['t'] > 5:
        _enum_at['t'] = time.time()
        try:
            refresh_enums()
        except Exception:
            pass
    mods = enabled_modules()
    for e in out:
        e['pack_modules'] = sorted(mods)
        break


# ───────────────────────── 值里的相对时间 ─────────────────────────
_REL = re.compile(r'^@(now|today|hour|day)(?:([+-]\d+(?:\.\d+)?)([hdm]?))?(?:\s+(\d{1,2}):(\d{2}))?$')


def resolve(v):
    """"@now" "@today+7" "@hour+8h" "@hour+2d" "@day+1 08:00" → 相对数据时钟的时间（演示数据的计划时间、到期日）。"""
    if isinstance(v, dict):
        return {k: resolve(x) for k, x in v.items()}
    if isinstance(v, list):
        return [resolve(x) for x in v]
    if not isinstance(v, str) or not v.startswith('@'):
        return v
    m = _REL.match(v)
    if not m:
        return v
    now = db.clock.now()
    base, n, unit, hh, mm = m.groups()
    t = {'now': now, 'today': now.replace(hour=0, minute=0, second=0, microsecond=0), 'hour': now.replace(minute=0, second=0, microsecond=0),
         'day': now.replace(hour=0, minute=0, second=0, microsecond=0)}[base]
    if n:
        k = float(n)
        unit = unit or ('d' if base in ('today', 'day') else 'h')
        t = t + (timedelta(hours=k) if unit == 'h' else timedelta(minutes=k) if unit == 'm' else timedelta(days=k))
    if hh is not None:
        t = t.replace(hour=int(hh), minute=int(mm))
    if base == 'today' and hh is None:
        return t.date().isoformat()
    return t.strftime('%Y-%m-%d %H:%M:%S')


# ───────────────────────── 执行步骤 ─────────────────────────
def _adhoc(entity, keys):
    """包里的"实体"步骤：按实体全部字段（含系统维护的字段）导入，用于没有登记导入器的实体（数据源、NC 程序…）。"""
    from .importer import Col, Importer
    e = BY_NAME[entity]
    cols = [Col(f.name, f.label, f.t, enum=f.enum, ref=f.ref) for f in e.fields if f.name not in ('password_hash',)]
    return Importer(key=f'_pack_{entity}', label=e.label, entity=entity, columns=cols, keys=tuple(keys or ()), perms=())


def _run_import(c, imp, rows, mode, skip_missing=False, label=''):
    from . import importer as I
    rows = [resolve(dict(r)) for r in rows]
    keys = imp.keys
    if mode == 'append':
        imp = copy.copy(imp)
        imp.keys, imp.find = (), (lambda ctx, v: None)
        mode = 'create'
    with context.source('import'):
        ctx = I.Ctx(c, imp, mode, None)
        res_rows = I.validate(ctx, [dict(r) for r in rows])
        dropped = 0
        if skip_missing:
            keep = []
            for r in res_rows:
                if r.errors and all(_i18n.matches(e['msg'], '{label}：找不到{label2}「{v}」') for e in r.errors):
                    dropped += 1
                    continue
                keep.append(r)
            res_rows = keep
        bad = [r for r in res_rows if r.errors]
        if bad:
            raise PackError(_tr('{v}：第 {n} 行 {join}', v=label or imp.label, n=bad[0].n, join=_tr('；').join(e['msg'] for e in bad[0].errors))
                            + (_tr('（共 {len} 行有错）', len=len(bad)) if len(bad) > 1 else ''))
        done = I.write_rows(ctx, res_rows)
    _ = keys
    return {'step': label or imp.label, 'rows': len(rows), **done, 'deferred': dropped, 'notes': ctx.notes}


def run_step(c, step, pdir) -> dict:
    from . import importer as I
    mode = step.get('mode') or 'create'
    if 'import' in step:
        imp = I.get(step['import'])
        return _run_import(c, imp, step.get('rows') or [], mode, step.get('skip_missing'), imp.label)
    if 'entity' in step:
        imp = _adhoc(step['entity'], step.get('keys') or ['code'])
        return _run_import(c, imp, step.get('rows') or [], mode, step.get('skip_missing'), BY_NAME[step['entity']].label)
    if 'settings' in step:
        n = 0
        for k, v in step['settings'].items():
            val, rem = (v if isinstance(v, list) else [v, ''])[:2]
            if db.setting(c, k) is None:
                db.set_setting(c, k, resolve(val), rem or '')
                n += 1
        return {'step': _tr('系统参数'), 'rows': len(step['settings']), 'create': n, 'update': 0, 'skip': len(step['settings']) - n}
    if 'files' in step:
        from ..services import drawings
        n = 0
        for f in step['files']:
            d = db.get(c, 'kb_doc', title=f['kb'])
            if not d or d.get('file_name'):
                continue
            with open(os.path.join(pdir, f['file']), 'rb') as fh:
                drawings.save_file(c, d['id'], os.path.basename(f['file']), fh.read())
            n += 1
        return {'step': _tr('图纸 / 附件'), 'rows': len(step['files']), 'create': n, 'update': 0, 'skip': len(step['files']) - n}
    raise PackError(_tr('不认识的步骤：{list}', list=list(step)))


def _maint_templates(codes) -> list:
    """合并各包的点检保养模板（同 key 合并：items_by_type 合并、types / skip_types 并集，其余后来的覆盖）。"""
    by: dict = {}
    order = []
    for code in codes:
        for t in _localized(load_all().get(code) or {}).get('maint_templates') or []:
            k = t['key']
            if k not in by:
                by[k] = {'items_by_type': {}, 'types': [], 'skip_types': []}
                order.append(k)
            cur = by[k]
            for kk, vv in t.items():
                if kk == 'items_by_type':
                    cur['items_by_type'].update(vv)
                elif kk in ('types', 'skip_types'):
                    cur[kk] = cur[kk] + [x for x in vv if x not in cur[kk]]
                else:
                    cur[kk] = vv
    return [by[k] for k in order]


def after_equipment(conn=None) -> dict:
    """导入设备之后（虚拟工厂导入、设备导入、接入中心）：按设备类型建点检保养计划；演示数据里依赖设备的记录（夹具、NC 程序、停机计划）。
    幂等：已有的跳过；设备还没有的记录留到下次。"""
    if conn is None:
        with db.engine.begin() as c:
            return after_equipment(c)
    c = conn
    codes = applied_codes(c)
    tpls = _maint_templates(codes)
    today = db.clock.now().date()
    n = 0
    if tpls:
        for e in db.rows(c, 'equipment', order=TABLES['equipment'].c.id):
            for t in tpls:
                if e['etype'] in (t.get('skip_types') or []):
                    continue
                # 有 default_items 的模板对所有设备类型（除 skip_types）；否则只对 types 里列出的类型
                if not t.get('default_items') and e['etype'] not in (t.get('types') or []):
                    continue
                # 只有覆盖项（没有 code）的模板：可能基础包没套用（旧库升级），用默认编码规则
                code = (t.get('code') or 'MP-{code}-' + str(t.get('key') or 'PM')).format(code=e['code'])
                if db.get(c, 'maint_plan', code=code):
                    continue
                items = (t.get('items_by_type') or {}).get(e['etype']) or t.get('items') or t.get('default_items') or _tr('外观、紧固、润滑')
                due = today + timedelta(days=int(t.get('next_due_days') or 0) + (e['id'] % int(t['stagger']) if t.get('stagger') else 0))
                db.insert(c, 'maint_plan', {'code': code, 'name': t['name'].format(name=e['name'], code=e['code']), 'equipment': e['id'], 'mtype': t.get('mtype') or 'spot',
                                            'cycle_days': t.get('cycle_days') or 1, 'items': items, 'next_due': due}, raw=True)
                n += 1
    steps = []
    done_demo = set()
    for r in applied(c):
        if not r['demo'] or r['pack'] not in load_all():
            continue
        d = demo_of(r['pack'])
        if not d or d['_from'] in done_demo:
            continue
        done_demo.add(d['_from'])
        if db.rows(c, 'equipment', limit=1):
            for st in d.get('after_equipment') or []:
                steps.append(run_step(c, st, d['_dir']))
    return {'maint_plans': n, 'steps': steps}


# ───────────────────────── 应用 / 预览 ─────────────────────────
def _apply(c, codes, demo, user) -> dict:
    allp = load_all()
    for code in codes:
        if code not in allp:
            raise PackError(_tr('没有行业模板 {code}', code=code))
    order = closure(codes)
    have = {r['pack']: r for r in applied(c)}
    demos = []
    if demo:
        for code in order:
            d = demo_of(code)
            if d and d['_from'] not in [x['_from'] for x in demos]:
                for r in d.get('requires') or []:
                    if r not in order:
                        order += [x for x in closure([r]) if x not in order]
                demos.append(d)
    report = {'packs': order, 'steps': [], 'reports': 0, 'enums': {}, 'modules': []}
    now = db.clock_wall()
    for code in order:
        pk = allp[code]
        if code not in have:
            c.execute(T().insert().values(pack=code, version=pk.get('version'), demo=False, summary=None, applied_by=(user or {}).get('username') or 'system',
                                          applied_at=now))
        for path, opts in (pk.get('enums') or {}).items():
            report['enums'].setdefault(path, {}).update(opts)
        report['modules'] += [m for m in pk.get('modules') or [] if m not in report['modules']]
    _cache['at'] = 0.0
    refresh_enums(c)
    for code in order:
        pk = allp[code]
        for st in pk.get('config') or []:
            r = run_step(c, st, pk['_dir'])
            r['pack'] = code
            report['steps'].append(r)
    for d in demos:
        for st in d.get('steps') or []:
            r = run_step(c, st, d['_dir'])
            r['pack'] = d['_from'] + _tr('·演示')
            report['steps'].append(r)
        c.execute(T().insert().values(pack=d['_from'], version=allp[d['_from']].get('version'), demo=True, summary={'demo': d.get('name')},
                                      applied_by=(user or {}).get('username') or 'system', applied_at=now))
        for r in d.get('requires') or []:
            if not any(x['pack'] == r and x['demo'] for x in applied(c)):
                c.execute(T().insert().values(pack=r, version=allp[r].get('version'), demo=True, summary={'demo': d.get('name'), 'via': d['_from']},
                                              applied_by=(user or {}).get('username') or 'system', applied_at=now))
    report['reports'] = _install_reports(c, order, user)
    if db.rows(c, 'equipment', limit=1):
        report['after_equipment'] = after_equipment(c)
    _cache['at'] = 0.0
    return report


def _install_reports(c, codes, user) -> int:
    from ..schema_impl import T as _T
    rt = _T['report_def']
    n = 0
    for code in codes:
        for r in load_all()[code].get('reports') or []:
            if c.execute(sa.select(rt.c.id).where((rt.c.name == r['name']) & (rt.c.source == f'pack:{code}'))).first():
                continue
            c.execute(rt.insert().values(name=r['name'], dataset=r['dataset'], config=r.get('config') or {}, chart=r.get('chart') or 'table',
                                         owner=(user or {}).get('username') or 'admin', shared_roles=r.get('shared_roles') or ['*'], schedule=None,
                                         source=f'pack:{code}', remark=r.get('remark') or _tr('行业模板「{name}」自带', name=load_all()[code]["name"]),
                                         created_at=db.clock_wall(), updated_at=db.clock_wall()))
            n += 1
    return n


def apply(codes, demo=False, user=None, conn=None) -> dict:
    """应用行业模板（一个事务；conn 给了就用调用方的事务）。"""
    from .. import i18n
    if not i18n.seeding_en() and i18n.lang() == 'en':     # templates are written in the UI language (English data from locale/data-en.json)
        with i18n.seed_translation():
            return apply(codes, demo, user, conn)
    if conn is not None:
        return _apply(conn, list(codes), demo, user)
    with context.acting(user, 'import') if user is not None else context.source('import'):
        with db.engine.connect() as c:
            tx = c.begin()
            try:
                rep = _apply(c, list(codes), demo, user)
                audit.audit(c, 'pack_apply', 'pack_applied', None, detail={'packs': rep['packs'], 'demo': demo,
                                                                            'created': sum(s.get('create', 0) for s in rep['steps'])},
                            target='pack:' + ','.join(codes), user=(user or {}).get('username') or 'system', source='import')
                tx.commit()
            except PackError as ex:
                tx.rollback()
                raise HTTPException(400, str(ex))
            except Exception:
                tx.rollback()
                raise
    _cache['at'] = 0.0
    refresh_enums()
    return {'ok': True, **rep}


def preview(codes, demo=False, user=None) -> dict:
    """预览：在事务里真跑一遍再回滚（新增 / 跳过 / 延后各多少都准）；再列出枚举、模块、KPI、报表、设备模板。"""
    with db.engine.connect() as c:
        tx = c.begin()
        try:
            rep = _apply(c, list(codes), demo, user)
            ok, err = True, None
        except PackError as ex:
            rep, ok, err = {'packs': closure(codes), 'steps': []}, False, str(ex)
        finally:
            tx.rollback()
    _cache['at'] = 0.0
    refresh_enums()
    allp = load_all()
    have = set(applied_codes())
    rep['ok'], rep['error'] = ok, err
    rep['detail'] = [pack_out(code, have) for code in rep['packs']]
    rep['new_enums'] = {}
    for code in rep['packs']:
        for path, opts in (allp[code].get('enums') or {}).items():
            f = _field(path)
            cur = set(f.enum or {}) if f else set()
            add = {k: v for k, v in opts.items() if k not in cur}
            if add:
                rep['new_enums'].setdefault(path, {}).update(add)
    return rep


def device_templates_status(codes) -> list:
    """包里引用的设备类型模板在 M2 的模板库（device_template）里有没有。"""
    out = []
    have = set()
    if 'device_template' in BY_NAME:
        try:
            with db.engine.connect() as c:
                have = {r[0] for r in c.execute(sa.select(TABLES['device_template'].c.code))}
        except Exception:
            have = set()
    for code in codes:
        for t in (load_all().get(code) or {}).get('device_templates') or []:
            out.append({**t, 'pack': code, 'installed': t['code'] in have, 'library': 'device_template' in BY_NAME})
    return out


def pack_out(code, have=None) -> dict:
    pk = get(code)
    have = set(applied_codes()) if have is None else have
    d = demo_of(code)
    step_info = []
    from . import importer as I
    for st in pk.get('config') or []:
        if 'import' in st:
            step_info.append({'what': I.get(st['import']).label, 'rows': len(st.get('rows') or [])})
        elif 'entity' in st:
            step_info.append({'what': BY_NAME[st['entity']].label, 'rows': len(st.get('rows') or [])})
        elif 'settings' in st:
            step_info.append({'what': _tr('系统参数'), 'rows': len(st['settings'])})
    demo_info = []
    if d:
        for st in (d.get('steps') or []) + (d.get('after_equipment') or []):
            if 'import' in st:
                demo_info.append({'what': I.get(st['import']).label, 'rows': len(st.get('rows') or [])})
            elif 'entity' in st:
                demo_info.append({'what': BY_NAME[st['entity']].label, 'rows': len(st.get('rows') or [])})
    rec = next((r for r in applied() if r['pack'] == code), None)
    return {'code': code, 'name': pk['name'], 'version': pk.get('version'), 'kind': pk.get('kind'), 'industry': pk.get('industry'),
            'description': pk.get('description'), 'requires': pk.get('requires') or [], 'modules': pk.get('modules') or [], 'enums': pk.get('enums') or {},
            'kpis': pk.get('kpis') or [], 'reports': [{'name': r['name'], 'dataset': r['dataset'], 'chart': r.get('chart')} for r in pk.get('reports') or []],
            'device_templates': device_templates_status([code]), 'maint_templates': pk.get('maint_templates') or [], 'config': step_info,
            'demo': {'name': d.get('name'), 'description': d.get('description'), 'requires': d.get('requires') or [], 'steps': demo_info} if d else None,
            'applied': code in have, 'applied_at': db.jsonable(rec['applied_at']) if rec else None, 'applied_version': rec['version'] if rec else None,
            'applied_demo': bool(rec and rec['demo'])}


def list_packs() -> list:
    have = set(applied_codes())
    return [pack_out(code, have) for code in load_all()]


def history() -> list:
    with db.engine.connect() as c:
        return [{k: db.jsonable(v) for k, v in dict(r).items()} for r in c.execute(sa.select(T()).order_by(T().c.id.desc())).mappings()]


def install():
    from .. import schema as sch
    if _spec not in sch.SPEC_HOOKS:
        sch.SPEC_HOOKS.append(_spec)
    try:
        refresh_enums()
    except Exception:
        pass


_ = datetime
