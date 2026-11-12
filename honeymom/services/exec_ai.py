"""AI 质量助手（功能开关 quality_plus；演示模式回放 fixtures/ai_demo/quality_plus.json）：

  POST /api/iatf/ai/pfmea_draft {pfmea}       按这个产品近 180 天的不合格品 / 缺陷代码 / 原因，起草 PFMEA 条目（S/O/D 按严重度、发生频次、检测方式估）
  POST /api/iatf/ai/complaint {complaint}     解释客诉影响范围（已发运 / 在厂 / 客户 / 下一步），可存为投诉的遏制说明
  POST /api/iatf/ai/reaction {cp_item}        建议控制计划行的反应计划

都只生成"动作草稿"（ai_drafts，审批中心可见），人确认（通过 / 改后通过）才写入。规则版给出可用的初稿；有大模型时润色文字（不改数字）。
"""
from ..i18n import _tr
import json
from datetime import timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db
from ..auth import principal
from ..db import clock
from ..schema import TABLES
from . import ai_demo, ai_drafts, ops

router = APIRouter()
SEV_S = {'critical': 9, 'major': 7, 'minor': 4}
ai_drafts.SOURCES.setdefault('quality_plus', 'AI 质量助手')


def _o_from_ppm(ppm):
    """频度 O：按 AIAG-VDA 过程 FMEA 的发生频次（每千件）粗略对应。"""
    for lim, o in ((0.001, 1), (0.01, 2), (0.1, 3), (0.5, 4), (2, 5), (10, 6), (20, 7), (50, 8), (100, 9)):
        if ppm / 1000 <= lim:
            return o
    return 10


def _polish(kind, payload, facts):
    from .ai import chat_once, llm_available

    def live():
        if not llm_available():
            return {}
        txt = chat_once(_tr('你是汽车行业（IATF 16949）质量工程师。根据事实把下面草稿里的文字字段改写得专业、具体（中文），不要改数字、不要编造事实。只输出 JSON 对象，结构和输入相同。\n\n草稿：') + json.dumps(payload, ensure_ascii=False) + _tr('\n事实：') + json.dumps(facts, ensure_ascii=False, default=str))
        try:
            return json.loads(txt[txt.find('{'):txt.rfind('}') + 1])
        except ValueError:
            return {}
    try:
        # 演示回放的键：PFMEA 按名称精确匹配；反应计划 / 客诉按文字相似匹配（换个零件 / 投诉也能回放）
        req = {'q': str(facts.get('subject') or '')}
        got = ai_demo.run('quality_plus', kind, req, live=live, fallback=lambda: {},
                          recordable=lambda r: bool(r), mark=lambda r: r, exact=kind == 'pfmea_draft')
    except Exception:      # noqa: BLE001
        got = {}
    return got if isinstance(got, dict) else {}


# ───────────────────────── PFMEA 草稿 ─────────────────────────
def pfmea_facts(c, f):
    nt, rt = TABLES['ncr'], TABLES['report']
    since = clock.now() - timedelta(days=180)
    cond = [nt.c.found_at >= since]
    if f['product']:
        cond.append(sa.or_(nt.c.material == f['product'], nt.c.material.is_(None)))
    rows = db.rows(c, 'ncr', *cond)
    made = c.execute(sa.select(sa.func.coalesce(sa.func.sum(rt.c.qty_good + rt.c.qty_ng), 0)).where(rt.c.start >= since)).scalar() or 0
    defects = {d['id']: d for d in db.rows(c, 'defect_code')}
    procs = {p['id']: p for p in db.rows(c, 'process')}
    agg = {}
    for n in rows:
        d = defects.get(n['defect'])
        if not d:
            continue
        a = agg.setdefault(d['id'], {'defect': d['code'], 'name': d['name'], 'severity': d['severity'], 'category': d['category'], 'process': d['process'],
                                     'process_name': (procs.get(d['process']) or {}).get('name'), 'qty': 0, 'causes': {}})
        a['qty'] += n['qty'] or 1
        if n['root_cause']:
            a['causes'][n['root_cause'][:40]] = a['causes'].get(n['root_cause'][:40], 0) + 1
    covered = {(r['process'], (r['failure_mode'] or '').strip()) for r in db.rows(c, 'pfmea_row', pfmea=f['id'])}
    return {'made': made, 'defects': sorted(agg.values(), key=lambda x: -x['qty']), 'covered': covered}


def draft_rows(c, f):
    facts = pfmea_facts(c, f)
    made = max(facts['made'], 1)
    auto_insp = {it['char'] for it in db.rows(c, 'insp_item') if it['method'] and any(k in it['method'] for k in (_tr('自动'), _tr('测头'), '100%'))}
    rows = []
    for d in facts['defects'][:8]:
        if (d['process'], d['name']) in facts['covered']:
            continue
        s = SEV_S.get(d['severity'], 6)
        o = _o_from_ppm(d['qty'] / made * 1e6)
        det = 3 if d['category'] in ('machining',) or auto_insp else 5
        cause = _tr('；').join(k for k, _ in sorted(d['causes'].items(), key=lambda x: -x[1])[:2]) or {
            'casting': _tr('熔炼 / 浇注工艺参数波动（温度、残镁、型砂水分）'), 'machining': _tr('刀具磨损 / 切削参数'), 'appearance': _tr('搬运磕碰')}.get(d['category'], '待分析')
        rows.append({'process': d['process'], 'function': _tr('{v} 满足图纸 / 工艺要求', v=d['process_name'] or ''), 'failure_mode': d['name'],
                     'effect': {'critical': _tr('装配后转向失效风险 / 客户停线'), 'major': _tr('客户装配困难或功能降级、退货'), 'minor': _tr('外观 / 轻微返工')}.get(d['severity'], '客户抱怨'),
                     'severity': s, 'cause': cause, 'occurrence': o, 'prevention': _tr('首件检验、点检、工艺参数监控（MES 采集）'),
                     'detection_ctrl': _tr('在线自动检测 100%') if det <= 3 else _tr('巡检 / 终检抽检'), 'detection': det, 'special': 'CC' if s >= 9 else 'SC' if s >= 7 else 'none',
                     'evidence': _tr('近 180 天 {qty} 件（{defect}），约 {v:.0f} ppm', qty=d['qty'], defect=d['defect'], v=d['qty'] / made * 1e6)})
    return rows, facts


def v_pfmea_rows(c, p):
    f = ops._find(c, 'pfmea', p.get('pfmea'), fields=('code', 'title')) if not str(p.get('pfmea') or '').isdigit() else db.get(c, 'pfmea', int(p['pfmea']))
    if not f:
        raise ops.OpError(_tr('请指定 PFMEA'))
    rows = []
    for r in p.get('rows') or []:
        if not (r.get('failure_mode') or '').strip():
            continue
        clean = {k: r.get(k) for k in ('process', 'function', 'failure_mode', 'effect', 'severity', 'cause', 'occurrence', 'prevention', 'detection_ctrl', 'detection',
                                        'special', 'char', 'product_char', 'evidence')}
        for k in ('severity', 'occurrence', 'detection'):
            v = int(clean.get(k) or 0)
            if not 1 <= v <= 10:
                raise ops.OpError(_tr('{k} 要在 1–10 之间', k=k))
            clean[k] = v
        if clean.get('special') not in ('none', 'CC', 'SC'):
            clean['special'] = 'none'
        rows.append(clean)
    if not rows:
        raise ops.OpError(_tr('没有可写入的条目'))
    return {'pfmea': f['id'], 'pfmea_code': f['code'], 'rows': rows}


def x_pfmea_rows(c, p, who, source):
    from .iatf import _after_fmea_row
    t = TABLES['pfmea_row']
    seq = (c.execute(sa.select(sa.func.max(t.c.seq)).where(t.c.pfmea == p['pfmea'])).scalar() or 0)
    for r in p['rows']:
        seq += 10
        rid = db.insert(c, 'pfmea_row', {**{k: v for k, v in r.items() if k != 'evidence'}, 'pfmea': p['pfmea'], 'seq': seq, 'ai': True,
                                         'actions': _tr('（AI 起草，依据：{v}）', v=r.get('evidence') or '')}, raw=True)
        _after_fmea_row(c, rid, None)
    return {'entity': 'pfmea', 'id': p['pfmea'], 'code': p['pfmea_code'], 'text': _tr('已写入 PFMEA {pfmea_code}：{len} 条', pfmea_code=p['pfmea_code'], len=len(p['rows'])), 'link': f"/iatf/pfmea?id={p['pfmea']}"}


def d_pfmea_rows(c, p):
    from .iatf import action_priority
    ch = [{'key': f"row{i}", 'label': _tr('新条目 {v}', v=i + 1), 'before': None,
           'after': _tr('{failure_mode}｜影响：{effect}｜原因：{cause}｜S{severity} O{occurrence} D{detection} → AP {action_priority}', failure_mode=r['failure_mode'], effect=r['effect'], cause=r['cause'], severity=r['severity'], occurrence=r['occurrence'], detection=r['detection'], action_priority=action_priority(r['severity'], r['occurrence'], r['detection']))}
          for i, r in enumerate(p['rows'])]
    return 'create', {'entity': 'pfmea', 'id': p['pfmea'], 'label': p['pfmea_code'], 'kind': 'PFMEA'}, ch


# ───────────────────────── 反应计划 ─────────────────────────
def v_reaction(c, p):
    it = db.get(c, 'cp_item', int(p.get('cp_item') or 0))
    if not it:
        raise ops.OpError(_tr('控制计划行不存在'))
    txt = ops._txt(p.get('reaction'), 2000)
    if not txt:
        raise ops.OpError(_tr('请填写反应计划'))
    return {'cp_item': it['id'], 'reaction': txt}


def x_reaction(c, p, who, source):
    db.update(c, 'cp_item', p['cp_item'], {'reaction': p['reaction']}, raw=True)
    it = db.get(c, 'cp_item', p['cp_item'])
    return {'entity': 'control_plan', 'id': it['plan'], 'code': '', 'text': _tr('已更新反应计划'), 'link': f"/iatf/pfmea?cp={it['plan']}"}


def d_reaction(c, p):
    it = db.get(c, 'cp_item', p['cp_item'])
    return 'update', {'entity': 'cp_item', 'id': it['id'], 'label': f"{it['char_no'] or ''} {it['product_char'] or it['process_char'] or ''}", 'kind': '控制计划项'}, [
        {'key': 'reaction', 'label': _tr('反应计划'), 'before': it['reaction'] or '—', 'after': p['reaction']}]


def suggest_reaction(c, it):
    from .iatf import REACTION
    sp = it['special'] or 'none'
    base = REACTION.get(sp, REACTION['none'])
    tail = []
    if it['char']:
        tail.append(_tr('SPC 判异（{char}）时：暂停并通知工艺，追溯判异点以来的零件', char=it['char']))
    if it['gauge_type']:
        tail.append(_tr('怀疑量具时：换有效量具复测，必要时做 GR&R'))
    if sp == 'CC':
        tail.append(_tr('恢复生产前首件经质量工程师电子签名放行'))
    return base + (_tr('；') + _tr('；').join(tail) if tail else '')


# ───────────────────────── 客诉解释 ─────────────────────────
def v_note(c, p):
    cm = db.get(c, 'complaint', int(p.get('complaint') or 0))
    if not cm:
        raise ops.OpError(_tr('投诉不存在'))
    return {'complaint': cm['id'], 'code': cm['code'], 'containment': ops._txt(p.get('containment'), 4000)}


def x_note(c, p, who, source):
    db.update(c, 'complaint', p['complaint'], {'containment': p['containment']}, raw=True)
    return {'entity': 'complaint', 'id': p['complaint'], 'code': p['code'], 'text': _tr('已写入 {code} 的遏制措施', code=p['code']), 'link': f"/iatf/complaints?id={p['complaint']}"}


def d_note(c, p):
    cm = db.get(c, 'complaint', p['complaint'])
    return 'update', {'entity': 'complaint', 'id': cm['id'], 'label': cm['code'], 'kind': '客户投诉'}, [
        {'key': 'containment', 'label': _tr('遏制措施 / 影响说明'), 'before': cm['containment'] or '—', 'after': p['containment']}]


def explain_complaint(cm):
    imp = cm['impact'] or {}
    sh, ih = imp.get('shipped') or {}, imp.get('in_house') or {}
    cust = _tr('、').join(_tr('{k} {v} 件', k=k, v=v) for k, v in (sh.get('customers') or {}).items()) or _tr('无')
    lines = [_tr('影响范围：炉次/批次 {v}，共 {get} 件。', v=_tr('、').join(imp.get('heats') or []) or '—', get=imp.get('parts_total', 0)),
             _tr('已发运 {get} 件（客户：{cust}），在厂 {get2} 件（未冻结 {get3} 件），报废 {get4} 件。', get=sh.get('count', 0), cust=cust, get2=ih.get('count', 0), get3=ih.get('not_held', 0), get4=imp.get('scrap', 0))]
    if ih.get('not_held'):
        lines.append(_tr('建议：立即冻结在厂受影响批次（“冻结在库”），装车未发的托盘会在发运时被拦下。'))
    if sh.get('count'):
        lines.append(_tr('建议：通知客户隔离并挑选已发运的 {get} 件；按 ASN / 箱号给出清单，必要时安排驻厂挑选或 RMA 退货。', get=sh.get('count')))
    lines.append(_tr('同时发起 8D：D3 临时措施写本遏制；D4 根因从炉次参数、刀具与检测数据追溯。'))
    return '\n'.join(lines)


ops.OPS['pfmea_rows'] = dict(label='写入 PFMEA 条目', entity='pfmea_row', risk='high', v=v_pfmea_rows, x=x_pfmea_rows,
                             fields=[('pfmea', 'PFMEA 编号', 'str', True), ('rows', '条目 [{process, failure_mode, effect, severity, cause, occurrence, detection_ctrl, detection, special}]', 'json', True)])
ops.OPS['cp_reaction'] = dict(label='更新反应计划', entity='cp_item', risk='high', v=v_reaction, x=x_reaction,
                              fields=[('cp_item', '控制计划行 id', 'int', True), ('reaction', '反应计划', 'text', True)])
ops.OPS['complaint_note'] = dict(label='写入客诉遏制说明', entity='complaint', risk='high', v=v_note, x=x_note,
                                 fields=[('complaint', '投诉 id', 'int', True), ('containment', '遏制措施 / 影响说明', 'text', True)])
ai_drafts.DIFFS.update({'pfmea_rows': d_pfmea_rows, 'cp_reaction': d_reaction, 'complaint_note': d_note})


def _draft(user, op, payload, summary):
    try:
        return ai_drafts.create(user, op, payload, source='quality_plus', summary=summary)
    except ops.OpError as ex:
        raise HTTPException(400, str(ex))


@router.post('/api/iatf/ai/pfmea_draft')
def ai_pfmea(body: dict = Body(...), user=Depends(principal)):
    with db.engine.connect() as c:
        f = db.get(c, 'pfmea', int(body.get('pfmea') or 0))
        if not f:
            raise HTTPException(404, _tr('PFMEA 不存在'))
        rows, facts = draft_rows(c, f)
    if not rows:
        raise HTTPException(400, _tr('近 180 天没有新的不良可以起草（已有条目都覆盖了）'))
    got = _polish('pfmea_draft', {'rows': [{k: r[k] for k in ('failure_mode', 'effect', 'cause', 'prevention', 'detection_ctrl')} for r in rows]},
                  {'subject': f['title'], 'defects': facts['defects'][:8]})
    for r, g in zip(rows, (got.get('rows') or []) if isinstance(got.get('rows'), list) else []):
        if isinstance(g, dict):
            r.update({k: v for k, v in g.items() if k in ('effect', 'cause', 'prevention', 'detection_ctrl') and isinstance(v, str) and v.strip()})
    summ = _tr('按近 180 天 {sum} 件不良（{len} 类缺陷）起草 {len2} 条；S 按缺陷严重度，O 按发生频次，D 按检测方式。通过之前不会写入。', sum=sum(d['qty'] for d in facts['defects']), len=len(facts['defects']), len2=len(rows))
    return _draft(user, 'pfmea_rows', {'pfmea': f['id'], 'rows': rows}, summ)


@router.post('/api/iatf/ai/reaction')
def ai_reaction(body: dict = Body(...), user=Depends(principal)):
    with db.engine.connect() as c:
        it = db.get(c, 'cp_item', int(body.get('cp_item') or 0))
        if not it:
            raise HTTPException(404, _tr('控制计划行不存在'))
        txt = suggest_reaction(c, it)
    got = _polish('reaction', {'reaction': txt}, {'subject': f"{it['special'] or 'none'} {it['product_char'] or it['process_char'] or it['char'] or ''}", 'special': it['special'], 'char': it['char'], 'spec': it['spec']})
    if isinstance(got.get('reaction'), str) and got['reaction'].strip():
        txt = got['reaction'].strip()
    return _draft(user, 'cp_reaction', {'cp_item': it['id'], 'reaction': txt}, _tr('按特殊特性（{v}）和控制方法建议反应计划。', v=it['special'] or _tr('一般')))


@router.post('/api/iatf/ai/complaint')
def ai_complaint(body: dict = Body(...), user=Depends(principal)):
    with db.engine.connect() as c:
        cm = db.get(c, 'complaint', int(body.get('complaint') or 0))
        if not cm:
            raise HTTPException(404, _tr('投诉不存在'))
        if not cm['impact']:
            raise HTTPException(400, _tr('先做“影响分析”，AI 再解释'))
        txt = explain_complaint(cm)
    got = _polish('complaint', {'containment': txt}, {'subject': f"{cm['description'] or ''}", 'impact': {k: v for k, v in (cm['impact'] or {}).items() if k not in ('cartons',)}})
    if isinstance(got.get('containment'), str) and got['containment'].strip():
        txt = got['containment'].strip()
    d = _draft(user, 'complaint_note', {'complaint': cm['id'], 'containment': txt}, _tr('影响范围的解释和下一步建议；通过后写进投诉的遏制措施。'))
    return {**d, 'explanation': txt}
