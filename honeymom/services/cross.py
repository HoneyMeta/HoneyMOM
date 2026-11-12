"""跨工段包（铸造 + 机加一体厂特有）：机加后才暴露的缺陷回溯到熔炼/浇注/型砂参数；两段协同排产；8D 起草。"""
from ..i18n import _tr
import math
from collections import defaultdict
from datetime import timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Depends

from .. import db
from ..auth import current_user
from ..db import clock
from ..schema import TABLES

router = APIRouter()
NUM_FACTORS = [('pour_temp', '浇注温度', '°C'), ('mg', '残余镁', '%'), ('sand_moisture', '型砂水分', '%'), ('compactability', '紧实率', '%'), ('ce', '碳当量', '')]
CAT_FACTORS = [('ladle', '浇包'), ('heat', '炉次'), ('machine', '加工机床'), ('cavity', '型腔')]
DEFECT_GROUPS = {
    'casting': ('铸造缺陷（气孔、缩松、冷隔、球化不良）', ('内腔', '冷隔', '球化', '气孔', '缩松')),
    'pore': ('内腔气孔/缩松', ('内腔', '气孔', '缩松')),
    'burr': ('毛刺', ('毛刺',)),
    'all': ('全部不良', ()),
}


def _defective(p, ncr_causes, keys):
    causes = [p['scrap_cause'] or ''] + ncr_causes.get(p['sn'], [])
    bad = p['status'] == 'scrap' or bool(ncr_causes.get(p['sn']))
    if not bad:
        return False
    return not keys or any(k in x for x in causes for k in keys)


def backtrace_data(c, days=7, group='casting'):
    pt, nt, dt = TABLES['part'], TABLES['ncr'], TABLES['defect_code']
    since = clock.now() - timedelta(days=days)
    parts = [dict(r) for r in c.execute(sa.select(pt).where((pt.c.born_at >= since) & pt.c.status.in_(('good', 'fg', 'shipped', 'scrap', 'rework')))).mappings()]
    ncr_causes = defaultdict(list)
    for sn, name, rc in c.execute(sa.select(nt.c.sn, dt.c.name, nt.c.root_cause).select_from(nt.outerjoin(dt, dt.c.id == nt.c.defect)).where(nt.c.found_at >= since)).all():
        ncr_causes[sn].append(f'{name or ""} {rc or ""}')
    keys = DEFECT_GROUPS.get(group, DEFECT_GROUPS['casting'])[1]
    for p in parts:
        p['_bad'] = _defective(p, ncr_causes, keys)
    return parts


def analyse(parts):
    n = len(parts)
    nb = sum(p['_bad'] for p in parts)
    base = nb / n if n else 0
    factors = []
    for key, name, unit in NUM_FACTORS:
        vals = sorted(p[key] for p in parts if p.get(key) is not None)
        if len(vals) < 40:
            continue
        qs = [vals[int(len(vals) * q)] for q in (0.2, 0.4, 0.6, 0.8)]
        edges = [vals[0]] + qs + [vals[-1]]
        bins = []
        for i in range(5):
            lo, hi = edges[i], edges[i + 1]
            grp = [p for p in parts if p.get(key) is not None and (lo <= p[key] < hi or (i == 4 and p[key] == hi))]
            if grp:
                b = sum(x['_bad'] for x in grp)
                fm = (lambda v: f'{v:.0f}') if abs(hi) >= 100 else (lambda v: f'{v:.3g}')
                bins.append({'label': f'{fm(lo)}–{fm(hi)}', 'n': len(grp), 'bad': b, 'rate': round(b / len(grp), 4)})
        score = _chi2(bins, base)
        worst = max(bins, key=lambda b: b['rate']) if bins else None
        factors.append({'key': key, 'name': name, 'unit': unit, 'type': 'num', 'bins': bins, 'score': round(score, 2), 'worst': worst,
                        'lift': round(worst['rate'] / base, 2) if worst and base else None})
    for key, name in CAT_FACTORS:
        groups = defaultdict(list)
        for p in parts:
            v = p.get(key)
            if v is None or v == '':
                continue
            if key == 'ladle':
                v = str(v).split('#')[0]
            groups[str(v)].append(p)
        if len(groups) < 2:
            continue
        bins = [{'label': k, 'n': len(g), 'bad': sum(x['_bad'] for x in g), 'rate': round(sum(x['_bad'] for x in g) / len(g), 4)}
                for k, g in sorted(groups.items()) if len(g) >= 5]
        if len(bins) < 2:
            continue
        if len(bins) > 12:   # 炉次很多：只列最差和最好的
            bins = sorted(bins, key=lambda b: -b['rate'])[:6] + sorted(bins, key=lambda b: b['rate'])[:3]
        score = _chi2(bins, base)
        worst = max(bins, key=lambda b: b['rate'])
        factors.append({'key': key, 'name': name, 'unit': '', 'type': 'cat', 'bins': bins, 'score': round(score, 2), 'worst': worst,
                        'lift': round(worst['rate'] / base, 2) if base else None})
    factors.sort(key=lambda f: -f['score'])
    return {'n': n, 'bad': nb, 'rate': round(base, 4), 'factors': factors}


def _chi2(bins, base):
    """卡方独立性检验（不良/良品 × 分组）：返回 -log10(p)，越大说明这个因素越能区分好坏件；>1.3 即 p<0.05。"""
    if base <= 0 or base >= 1 or len(bins) < 2:
        return 0.0
    stat = 0.0
    for b in bins:
        eb, eg = b['n'] * base, b['n'] * (1 - base)
        if eb > 0 and eg > 0:
            stat += (b['bad'] - eb) ** 2 / eb + ((b['n'] - b['bad']) - eg) ** 2 / eg
    try:
        from scipy.stats import chi2
        p = max(float(chi2.sf(stat, len(bins) - 1)), 1e-12)
    except ImportError:
        p = math.exp(-stat / 2)
    return -math.log10(p)


def narrative(res, group):
    if not res['n']:
        return _tr('统计区间内还没有完成的零件。')
    lines = [_tr('统计 {n} 件已出结果的零件，其中{v} {bad} 件，不良率 {v2:.2f}%。', n=res['n'], v=DEFECT_GROUPS[group][0], bad=res['bad'], v2=res['rate'] * 100)]
    top = [f for f in res['factors'] if f['score'] >= 1.3][:3]
    if res['bad'] < 10:
        lines.append(_tr('缺陷件只有 {bad} 件，样本太少，下面的差异很可能是偶然的，仅供参考；建议拉长统计区间。', bad=res['bad']))
    if not top:
        lines.append(_tr('各因素分组之间的差异都不显著，可能是随机波动，或者原因不在已采集的参数里（建议补充记录）。'))
    for f in top:
        w = f['worst']
        lines.append(_tr('· {name}：{label}{unit} 这一组不良率 {v:.1f}%，是平均的 {lift} 倍（显著性 p={v2:.2g}）。', name=f['name'], label=w['label'], unit=f['unit'], v=w['rate'] * 100, lift=f['lift'], v2=10 ** -f['score']))
    if top:
        k = top[0]['key']
        tip = {'sand_moisture': _tr('型砂水分偏高时气孔明显增多：检查砂处理加水控制和回砂温度。'), 'mg': _tr('残镁偏低与缩松、球化不良相关：检查球化剂加入量、浇包等待时间。'),
               'pour_temp': _tr('浇注温度偏离窗口：检查出铁温度和浇包运输时间。'), 'ce': _tr('碳当量偏离目标：检查配料和光谱补料。'),
               'compactability': _tr('紧实率异常：检查型砂水分和混砂时间。'), 'ladle': _tr('个别浇包问题突出：检查浇包烘烤、包衬和球化处理。'),
               'heat': _tr('问题集中在少数炉次：调出这些炉次的光谱和温度记录比对。'), 'machine': _tr('问题集中在某台机床：检查刀具、夹具和程序版本。'),
               'cavity': _tr('某个型腔不良高：检查模具该型腔的排气和浇道。')}.get(k)
        if tip:
            lines.append(_tr('建议：') + tip)
    return '\n'.join(lines)


@router.get('/api/cross/backtrace')
def backtrace(days: int = 7, group: str = 'casting', user=Depends(current_user)):
    with db.engine.connect() as c:
        parts = backtrace_data(c, days, group)
    res = analyse(parts)
    res['text'] = narrative(res, group)
    res['groups'] = {k: v[0] for k, v in DEFECT_GROUPS.items()}
    return res


def draft_8d(c, r):
    """8D 草稿：先用回溯结果填事实，再交给大模型润色（未配置大模型时直接用规则版）。"""
    d = db.get(c, 'defect_code', r['defect']) if r['defect'] else None
    ncr = db.get(c, 'ncr', r['ncr']) if r['ncr'] else None
    dn = (d['name'] or '').lower() if d else ''
    group = 'burr' if d and ('毛刺' in dn or 'burr' in dn) else 'pore' if d and ('内腔' in dn or '气孔' in dn or '缩松' in dn or 'poros' in dn or 'shrink' in dn or 'internal' in dn) else 'casting'
    res = analyse(backtrace_data(c, 7, group))
    facts = narrative(res, group)
    nt = TABLES['ncr']
    cnt = c.execute(sa.select(sa.func.count()).select_from(nt).where((nt.c.defect == r['defect']) & (nt.c.found_at >= clock.now() - timedelta(days=7)))).scalar() if r['defect'] else 0
    base = {
        'd1': _tr('质量工程师（组长）、熔炼班长、造型班长、机加工艺员、设备员'),
        'd2': _tr('近 7 天发现{v} {cnt} 起', v=d['name'] if d else _tr('不良'), cnt=cnt) + (_tr('，首次记录 {code}（零件 {sn}，{station}）', code=ncr['code'], sn=ncr['sn'], station=ncr['station']) if ncr else '') + _tr('。\n') + facts,
        'd3': _tr('1. 对同炉次/同浇包在制品加严检验（内窥全检）；\n2. 已入库成品按批次复查；\n3. 异常参数恢复到工艺窗口内并加密巡检。'),
        'd4': _tr('根据数据回溯，最可能的原因：\n') + '\n'.join(l for l in facts.split('\n') if l.startswith('·') or l.startswith('建议')),
        'd5': _tr('针对主要因素修订工艺卡上下限，并在 MES 里设置超限报警；必要时调整自动控制参数。'),
        'd6': _tr('措施实施后连续 3 天跟踪该缺陷不良率，与实施前对比（在 MES 缺陷回溯页面按天查看）。'),
        'd7': _tr('更新 PFMEA 和控制计划；相关参数纳入 SPC 监控。'),
        'd8': _tr('待验证有效后关闭。'),
    }
    from .ai import llm_available, chat_once
    from . import ai_demo

    def live():
        if not llm_available():
            return None
        import json
        prompt = (_tr('你是铸造和机加工厂的质量工程师。请根据下面的数据事实，起草一份 8D 报告的 D2、D4、D5 三部分，中文，简洁务实，不要编造数据。按 JSON 输出：{"d2": "...", "d4": "...", "d5": "..."}\n\n数据事实：\n') + base['d2'])
        txt = chat_once(prompt)
        j = json.loads(txt[txt.find('{'):txt.rfind('}') + 1])
        return {k: j[k] for k in ('d2', 'd4', 'd5') if isinstance(j.get(k), str) and j[k].strip()} or None
    try:       # 演示模式回放录制的 8D 草稿（按缺陷名）
        j = ai_demo.run('ask', 'draft_8d', {'defect': d['name'] if d else '', 'group': group}, live=live, fallback=lambda: None,
                        recordable=lambda r: bool(r), mark=lambda r: r, exact=True)
        for k in ('d2', 'd4', 'd5'):
            if isinstance(j, dict) and j.get(k):
                base[k] = j[k]
    except Exception:
        pass
    return base


@router.get('/api/cross/coplan')
def coplan(user=Depends(current_user)):
    """两段协同排产：机加瓶颈决定节奏，铸造按毛坯库存缺口排产，避免毛坯积压或断料。"""
    from .equipment import oee_of
    now = clock.now()
    s = now - timedelta(hours=24)
    with db.engine.connect() as c:
        eqs = db.rows(c, 'equipment')
        caps = {}
        for kind, label in (('cnc', _tr('CNC 机加')), ('deburr', _tr('去毛刺')), ('inspection', _tr('内窥检测')), ('molding', _tr('造型浇注'))):
            sel = [e for e in eqs if e['etype'] == kind]
            if not sel:
                continue
            per_h = 0.0
            oees = []
            for e in sel:
                o = oee_of(c, e, s, now)
                oe = o['OEE'] if o and o['OEE'] else 0.8
                oees.append(oe)
                ct = e['ideal_cycle_s'] or 60
                per_h += 3600 / ct * oe
            caps[kind] = {'label': label, 'n': len(sel), 'per_h': round(per_h, 1), 'oee': round(sum(oees) / len(oees), 3)}
        pt = TABLES['part']
        blank = c.execute(sa.select(sa.func.count()).select_from(pt).where(pt.c.status.in_(('wip', 'raw_store')) & pt.c.machine.is_(None))).scalar()
        store = c.execute(sa.select(sa.func.count()).select_from(pt).where(pt.c.status == 'raw_store')).scalar()
        born = c.execute(sa.select(sa.func.count()).select_from(pt).where(pt.c.born_at >= s)).scalar()
        scrap_cast = c.execute(sa.select(sa.func.count()).select_from(pt).where((pt.c.born_at >= s) & (pt.c.status == 'scrap') & pt.c.machine.is_(None))).scalar()
        wot = TABLES['work_order']
        wos = db.rows(c, 'work_order', wot.c.status.in_(('released', 'in_progress')), order=wot.c.due)
    mach_keys = [k for k in ('cnc', 'deburr', 'inspection') if k in caps]
    if not mach_keys:
        return {'ok': False, 'msg': _tr('没有机加工段设备')}
    bott = min(mach_keys, key=lambda k: caps[k]['per_h'])
    mach_h = caps[bott]['per_h']
    cast_scrap = scrap_cast / born if born else 0.02
    cast_h = caps.get('molding', {}).get('per_h', 0) * 2      # 一型两件
    target = round(mach_h * 8)                                   # 毛坯库存目标：一个班的机加用量
    remain = sum(max(0, w['qty'] - (w['qty_done'] or 0)) for w in wos)
    shifts, stock = [], blank
    for i, name in enumerate((_tr('早班'), _tr('中班'), _tr('夜班'))):
        mach = round(mach_h * 8)
        need = max(0, target - (stock - mach))
        cast = min(round(cast_h * 8), round(need / (1 - cast_scrap))) if cast_h else 0
        stock = stock - mach + round(cast * (1 - cast_scrap))
        shifts.append({'shift': name, 'machining': mach, 'casting': cast, 'molds': math.ceil(cast / 2), 'blank_end': stock,
                       'casting_hours': round(cast / cast_h, 1) if cast_h else 0})
    tips = [_tr('机加段瓶颈是{label}，约 {mach_h:.0f} 件/小时（按近 24 小时 OEE {v:.0f}% 折算）。', label=caps[bott]['label'], mach_h=mach_h, v=caps[bott]['oee'] * 100)]
    if cast_h and cast_h > mach_h * 1.15:
        tips.append(_tr('造型能力约 {cast_h:.0f} 件/小时，比机加快 {v:.0%}：按毛坯缺口开造型线，不必满负荷浇注，多出的时间可安排设备保养；熔炼尽量放在谷电时段。', cast_h=cast_h, v=cast_h / mach_h - 1))
    if blank > target * 1.5:
        tips.append(_tr('当前毛坯 {blank} 件（其中毛坯库 {store} 件），超过目标 {target} 件较多，下一班铸造可减产。', blank=blank, store=store, target=target))
    elif blank < target * 0.5:
        tips.append(_tr('当前毛坯只有 {blank} 件，低于一个班用量，机加有断料风险，铸造应优先保产。', blank=blank))
    if remain:
        tips.append(_tr('在制工单剩余 {remain} 件，按机加节奏约需 {v:.1f} 小时。', remain=remain, v=remain / mach_h))
    return {'ok': True, 'caps': caps, 'bottleneck': bott, 'blank': blank, 'store': store, 'target': target, 'cast_scrap': round(cast_scrap, 4),
            'shifts': shifts, 'tips': tips, 'remain': remain}
