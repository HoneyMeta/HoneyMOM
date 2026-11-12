"""机加包：NC 程序审批下发、刀具寿命与换刀、不加传感器的刀具磨损监测（主轴负载）、机加不良统计、三坐标导入。"""
from ..i18n import _tr
import csv
import io
import json
import statistics
import urllib.request
from collections import defaultdict
from datetime import timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db
from ..auth import current_user, require_write
from ..crud import action, attach_labels, out
from ..db import clock
from ..schema import TABLES

router = APIRouter()
VB_LIMIT = 0.30          # 后刀面磨损判废标准 mm（ISO 3685 常用值）
K_FORCE = 1.6            # 切削力随磨损增大系数（主切削力 ≈ F0·(1+k·VB)）


@action('nc_program', 'approve')
def nc_approve(c, r, body, user):
    if r['status'] != 'draft':
        raise HTTPException(400, _tr('只有编制中的程序可以审核'))
    db.update(c, 'nc_program', r['id'], {'status': 'approved', 'approved_by': user['name'] or user['username']}, raw=True)


@action('nc_program', 'release')
def nc_release(c, r, body, user):
    if r['status'] != 'approved':
        raise HTTPException(400, _tr('审核通过后才能下发'))
    t = TABLES['nc_program']
    # 同一程序号旧版本作废
    c.execute(t.update().where((t.c.name == r['name']) & (t.c.id != r['id']) & (t.c.status == 'released')).values(status='obsolete'))
    db.update(c, 'nc_program', r['id'], {'status': 'released', 'released_at': clock.now()}, raw=True)
    return {'msg': _tr('已下发到 {v}（演示环境只记录，不实际传输到 CNC）', v=r['station_group'] or _tr('机床'))}


@action('nc_program', 'obsolete')
def nc_obsolete(c, r, body, user):
    db.update(c, 'nc_program', r['id'], {'status': 'obsolete'}, raw=True)


@action('tool', 'change')
def tool_change(c, r, body, user):
    db.insert(c, 'tool_change', {'equipment': r['equipment'], 'tool': r['code'], 'tool_name': r['name'], 'used': r['used'], 'reason': body.get('reason') or 'manual',
                                 'time': clock.now()}, raw=True)
    db.update(c, 'tool', r['id'], {'used': 0, 'changes': (r['changes'] or 0) + 1, 'last_change': clock.now(), 'status': 'ok', 'wear_pct': 0}, raw=True)
    return {'msg': _tr('已记录换刀（真实机床上要同时在 CNC 上把刀具寿命计数清零）')}


def estimate_wear(c, tool):
    """用主轴负载估计后刀面磨损：同一把刀换刀后最初的切削负载作基准，负载升高比例反推 VB。"""
    ts_ = db.metadata.tables['tag_sample']
    since = tool['last_change'] or (clock.now() - timedelta(hours=12))
    pts = c.execute(sa.select(ts_.c.ts, ts_.c.num).where((ts_.c.equipment == tool['equipment']) & (ts_.c.name == f"Monitoring/Spindle/Load@{tool['code']}")
                                                     & (ts_.c.ts >= since) & (ts_.c.num > 5)).order_by(ts_.c.ts)).all()
    if len(pts) < 12:
        return None
    base = statistics.median([p[1] for p in pts[:8]])
    ref = 'own'
    if not tool['changes']:
        # 这把刀在 MES 里还没换过，起点是半旧刀：借用其他机床同刀位换刀后的新刀负载作基准
        tt = TABLES['tool']
        peers = c.execute(sa.select(tt).where((tt.c.code == tool['code']) & (tt.c.changes > 0) & (tt.c.id != tool['id']))).mappings().all()
        bases = []
        for pr in peers:
            pp = c.execute(sa.select(ts_.c.num).where((ts_.c.equipment == pr['equipment']) & (ts_.c.name == f"Monitoring/Spindle/Load@{pr['code']}")
                                                    & (ts_.c.ts >= pr['last_change']) & (ts_.c.num > 5)).order_by(ts_.c.ts).limit(8)).all()
            if len(pp) >= 6:
                bases.append(statistics.median([x[0] for x in pp]))
        if bases:
            base, ref = statistics.median(bases), 'peer'
        else:
            ref = 'midlife'
    recent = statistics.median([p[1] for p in pts[-8:]])
    vb0 = 0.05                                     # 新刀磨合后约 0.05 mm
    vb = max(0.0, (recent / base * (1 + K_FORCE * vb0) - 1) / K_FORCE)
    return {'vb_est': round(vb, 3), 'wear_pct': round(min(150.0, vb / VB_LIMIT * 100), 1), 'base': round(base, 1), 'recent': round(recent, 1), 'n': len(pts), 'ref': ref,
            'series': [{'t': db.jsonable(p[0]), 'v': p[1]} for p in pts[-200:]]}


def refresh_tools():
    """采集循环定期调用：更新每把刀的 AI 磨损估计和状态。"刀具寿命估计"关闭时只按寿命计数（到 90% 预警、到数该换刀）。"""
    from .ai_switch import enabled
    ai_on = enabled('tool_wear')
    with db.engine.begin() as c:
        for t in db.rows(c, 'tool'):
            if not ai_on:
                life, used = t['life_limit'] or 0, t['used'] or 0
                status = 'change' if life and used >= life else 'warn' if life and used >= life * 0.9 else 'ok'
                if status != t['status']:
                    db.update(c, 'tool', t['id'], {'status': status}, raw=True)
                continue
            est = estimate_wear(c, t)
            if not est:
                continue
            life = t['life_limit'] or 0
            status = 'change' if est['wear_pct'] >= 100 else 'warn' if est['wear_pct'] >= 80 or (life and (t['used'] or 0) >= life * 0.9) else 'ok'
            db.update(c, 'tool', t['id'], {'wear_pct': est['wear_pct'], 'status': status}, raw=True)


def vf_truth(eq):
    """演示用：从虚拟工厂取刀具真实磨损，对照 AI 估计（真实工厂没有这一项）。"""
    try:
        with db.engine.connect() as c:
            src, tok = db.setting(c, 'vf_http'), db.setting(c, 'vf_token') or ''
        if not src or not eq.get('source'):
            return None
        sid = eq['source'].split(':', 1)[1]
        req = urllib.request.Request(f'{src}/station/{sid}', headers={'Authorization': 'Bearer ' + tok} if tok else {})
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read()).get('truth', {}).get('VB_mm')
    except Exception:
        return None


@router.get('/api/machining/tools')
def tools(user=Depends(current_user)):
    with db.engine.connect() as c:
        rows = db.rows(c, 'tool', order=(TABLES['tool'].c.equipment, TABLES['tool'].c.code))
        attach_labels(c, 'tool', rows)
        from .ai_switch import enabled
        ai_on = enabled('tool_wear')
        eqs = {e['id']: e for e in db.rows(c, 'equipment', etype='cnc')} if ai_on else {}
        truths = {eid: vf_truth(e) for eid, e in eqs.items()}
        for r in rows:
            if not ai_on:            # "刀具寿命估计"关闭：没有 AI 磨损估计，只看寿命计数
                r['est'], r['vb_truth'], r['life_pct'] = None, None, round((r['used'] or 0) / r['life_limit'] * 100, 1) if r['life_limit'] else None
                continue
            est = estimate_wear(c, r)
            r['est'] = est
            tr = truths.get(r['equipment']) or {}
            r['vb_truth'] = tr.get(r['code'])
        tc = TABLES['tool_change']
        since = clock.now() - timedelta(days=7)
        stats = [dict(x) for x in c.execute(sa.select(tc.c.equipment, tc.c.tool, tc.c.reason, sa.func.count().label('n'), sa.func.avg(tc.c.used).label('avg_used'))
                                            .where(tc.c.time >= since).group_by(tc.c.equipment, tc.c.tool, tc.c.reason)).mappings()]
        attach_labels(c, 'tool_change', stats)
    return {'tools': out(rows), 'changes': out(stats), 'vb_limit': VB_LIMIT, 'ai': ai_on}


@router.get('/api/machining/defects')
def defects(days: int = 7, user=Depends(current_user)):
    """机加不良统计：按机床 × 不良原因；返修率；缸孔尺寸能力。"""
    pt, nt, dt = TABLES['part'], TABLES['ncr'], TABLES['defect_code']
    since = clock.now() - timedelta(days=days)
    with db.engine.connect() as c:
        # 每台机床加工件数与其后的不良
        rows = c.execute(sa.select(pt.c.machine, pt.c.status, pt.c.scrap_cause, pt.c.rework_count).where((pt.c.machine.isnot(None)) & (pt.c.born_at >= since))).all()
        by = defaultdict(lambda: defaultdict(int))
        for m, st, cause, rw in rows:
            d = by[m]
            d['total'] += 1
            if st == 'scrap':
                d['scrap'] += 1
                d['c:' + (cause or _tr('其他'))] += 1
            if rw:
                d['rework'] += 1
        mach = []
        for m, d in sorted(by.items()):
            mach.append({'machine': m, 'total': d['total'], 'scrap': d['scrap'], 'rework': d['rework'], 'scrap_rate': round(d['scrap'] / max(1, d['total']), 4),
                         'rework_rate': round(d['rework'] / max(1, d['total']), 4), 'causes': {k[2:]: v for k, v in d.items() if k.startswith('c:')}})
        ncr = [{'defect': r[0] or _tr('未分类'), 'station': r[1], 'n': r[2]} for r in c.execute(
            sa.select(dt.c.name, nt.c.station, sa.func.count()).select_from(nt.outerjoin(dt, dt.c.id == nt.c.defect)).where(nt.c.found_at >= since)
            .group_by(dt.c.name, nt.c.station)).all()]
    return {'machines': mach, 'ncr': ncr}


@router.post('/api/machining/cmm_import')
def cmm_import(body: dict = Body(...), user=Depends(current_user)):
    """三坐标 / 首件检验结果导入：CSV 列 sn,特性代码,实测值[,设备]。"""
    require_write(user, 'insp_record', 'create')
    text = body.get('csv') or ''
    rd = csv.reader(io.StringIO(text.strip()))
    rows = [r for r in rd if r and not r[0].lower().startswith('sn')]
    if not rows:
        raise HTTPException(400, _tr('没有数据'))
    by_sn = defaultdict(list)
    for r in rows:
        by_sn[r[0].strip()].append(r)
    with db.engine.begin() as c:
        n = 0
        for sn, items in by_sn.items():
            rid = db.insert(c, 'insp_record', {'itype': 'FAI', 'sn': sn, 'station': items[0][3].strip() if len(items[0]) > 3 else _tr('三坐标'),
                                               'qty_sample': 1, 'time': clock.now(), 'source': 'import', 'result': 'pending', 'inspector': user.get('employee')}, raw=True)
            for r in items:
                it = db.get(c, 'insp_item', char=r[1].strip())
                c.execute(TABLES['insp_value'].insert().values(record=rid, item=it['id'] if it else None, char=r[1].strip(), value=float(r[2]),
                                                               created_at=clock.now(), updated_at=clock.now()))
                n += 1
            from .quality import judge_record
            judge_record(c, rid, user)
    return {'ok': True, 'records': len(by_sn), 'values': n}
