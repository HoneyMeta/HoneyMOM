"""ERP 的 AI（功能开关 erp，演示模式回放录制 ai_demo）：
  POST /api/erp/ai/explain_mrp {suggestion}     "为什么 MRP 建议这个"：按需求来源（pegging）和净算过程讲清楚；有大模型时润色成一段话
  POST /api/erp/ai/at_risk {days?}              "哪些订单有风险"：订单看板的风险清单 → 按严重程度排好、给出处理建议
  POST /api/erp/ai/draft_po {material | suggestion, qty?, supplier?}   "起草采购订单"：生成一张动作草稿（审批中心 / 右侧 AI 面板），通过才建采购订单
规则版答案不依赖大模型（AI 总开关开了但没配大模型时也能用）。"""
from ..i18n import _tr
import json
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import auth, db
from ..perms import require
from ..services import ai_demo, ops
from ..services.ai_switch import require as ai_require
from . import core
from .core import as_date, today

router = APIRouter()


def _llm(prompt):
    from ..services.ai import chat_once, llm_available
    if not llm_available():
        return None
    try:
        return chat_once(prompt, _tr('你是制造业 MES / ERP 里的计划与采购助手。只根据给出的事实回答，中文、简洁、分点，不编造数字。'))
    except Exception:          # noqa: BLE001
        return None


def explain_rule(s, m, unit):
    lines = [_tr('**{get} {get2} {get3} × {qty:g}{unit}**，需求日期 {need_date}，建议 {release_date} 下达。', get={'make': _tr('计划生产'), 'buy': _tr('计划采购'), 'expedite': _tr('提前')}.get(s['kind'], s['kind']), get2=m.get('code', ''), get3=m.get('name', ''), qty=s['qty'], unit=unit, need_date=s['need_date'], release_date=s['release_date'])]
    pegs = [p for p in s.get('pegging') or [] if p.get('kind') != 'top']
    tops = [p for p in s.get('pegging') or [] if p.get('kind') == 'top']
    if pegs:
        lines.append(_tr('这批量是为了满足：'))
        lines += [_tr('- {label}：{qty:g}{unit}（{date}）', label=p['label'], qty=p['qty'], unit=unit, date=p['date']) for p in pegs[:8]]
    if tops and any(p.get('kind') == 'parent' for p in pegs):
        lines.append(_tr('往上追到最终需求：'))
        lines += [f"- {p['label']}" for p in tops[:6]]
    if s.get('reason'):
        lines.append(_tr('净算：') + s['reason'])
    over = [x for x in s.get('capacity') or [] if x.get('over')]
    if over:
        lines.append(_tr('⚠ 粗能力检查：') + _tr('、').join(_tr('{group}（{week} 那周）', group=x['group'], week=x['week']) for x in over) + _tr(' 超负荷，下达前和排产确认。'))
    return '\n'.join(lines)


@router.post('/api/erp/ai/explain_mrp')
def explain_mrp(body: dict = Body(...), user=Depends(require('mrp.mrp_suggestion.read'))):
    ai_require('erp')
    with db.engine.connect() as c:
        s = db.get(c, 'mrp_suggestion', int(body.get('suggestion') or 0))
        if not s:
            raise HTTPException(404, _tr('建议不存在'))
        m = db.get(c, 'material', s['material']) or {}
        s = {k: db.jsonable(v) for k, v in s.items()}
    rule = explain_rule(s, m, m.get('unit') or '')

    def live():
        txt = _llm(_tr('用 3~6 句话向计划员解释这条 MRP 建议为什么存在、为什么是这个数量和日期、下达前要注意什么。\n\n事实：\n') + rule)
        return {'text': txt or rule, 'by': 'llm' if txt else 'rule'}
    res = ai_demo.run('erp', 'explain_mrp', {'material': m.get('code'), 'kind': s['kind'], 'qty': s['qty']}, live=live,
                      fallback=lambda: {'text': rule, 'by': 'rule'}, recordable=lambda r: r.get('by') == 'llm')
    return {**res, 'facts': rule, 'suggestion': s['id']}


@router.post('/api/erp/ai/at_risk')
def at_risk(body: dict = Body(default={}), user=Depends(require('sales.sales_order.read'))):
    ai_require('erp')
    from .sales import board
    with db.engine.connect() as c:
        b = board(c, user, int(body.get('days') or 90))
    risks = b['at_risk'][:20]
    if not risks:
        rule = _tr('现在没有逾期或预计逾期的订单。') + (_tr('近 {otd_window} 天准时交付率 {v:.1f}%。', otd_window=b['otd_window'], v=b['otd'] * 100) if b['otd'] is not None else '')
    else:
        rule = _tr('有 {len} 条交货计划需要关注（已逾期 {overdue} 条）：\n', len=len(b['at_risk']), overdue=b['overdue']) + '\n'.join(
            _tr('- [{risk_label}] {so_code} 第 {seq} 行 {v} {material_label}，还差 {left:g}，交期 {due}：{reason}', risk_label=x['risk_label'], so_code=x['so_code'], seq=x['seq'], v=x['customer'] or '', material_label=x['material_label'], left=x['left'], due=x['due'], reason=x['reason'])
            for x in risks)
        rule += _tr('\n建议：已逾期的先和客户确认新交期；预计逾期的看工单能否提前（排产与进度里调优先级），没工单的跑一次 MRP 下达。')

    def live():
        txt = _llm(_tr('根据下面的订单风险清单，按"先处理什么"排序，给计划员 3~5 条具体行动（提到订单号）。\n\n') + rule)
        return {'text': txt or rule, 'by': 'llm' if txt else 'rule'}
    res = ai_demo.run('erp', 'at_risk', {'n': len(risks), 'top': [x['so_code'] for x in risks[:5]]}, live=live, fallback=lambda: {'text': rule, 'by': 'rule'},
                      recordable=lambda r: r.get('by') == 'llm')
    return {**res, 'items': risks, 'otd': b['otd']}


# ───────────────── 动作草稿：起草采购订单 ─────────────────
def v_po(c, p):
    sup = ops._find(c, 'supplier', p.get('supplier'))
    if not sup:
        raise ops.OpError(_tr('找不到供应商：{get}', get=p.get('supplier')))
    lines = p.get('lines') or []
    if isinstance(lines, str):
        try:
            lines = json.loads(lines)
        except ValueError:
            lines = []
    clean = []
    from .purchase import asl_check
    for ln in lines if isinstance(lines, list) else []:
        m = ops._find(c, 'material', ln.get('material'))
        if not m:
            raise ops.OpError(_tr('找不到物料：{get}', get=ln.get('material')))
        try:
            q = float(ln.get('qty') or 0)
        except (TypeError, ValueError):
            q = 0
        if q <= 0:
            raise ops.OpError(_tr('{code} 的数量要大于 0', code=m['code']))
        try:
            asl_check(c, sup['id'], m['id'])
        except HTTPException as ex:
            raise ops.OpError(ex.detail)
        price = float(ln['price']) if ln.get('price') not in (None, '') else core.purchase_price(c, sup['id'], m['id'])[0]
        due = as_date(ln.get('due')) or today() + timedelta(days=int((core.supplier_item(c, sup['id'], m['id']) or {}).get('lead_days') or 7))
        clean.append({'material': m['code'], 'material_name': m['name'], 'qty': q, 'price': price, 'due': str(due)})
    if not clean:
        raise ops.OpError(_tr('采购订单至少要一行'))
    return {'supplier': sup['code'], 'supplier_name': sup['name'], 'lines': clean, 'reason': ops._txt(p.get('reason'), 400),
            'suggestion': p.get('suggestion')}


def x_po(c, p, who_, source):
    sup = ops._find(c, 'supplier', p['supplier'])
    lines = [{'material': ops._find(c, 'material', ln['material'])['id'], 'qty': ln['qty'], 'price': ln['price'], 'due': ln['due']} for ln in p['lines']]
    pid = core.save_doc(c, 'purchase_order', {'supplier': sup['id'], 'buyer': who_, 'remark': (_tr('AI 起草：') + (p.get('reason') or ''))[:300], 'lines': lines}, None)
    db.update(c, 'purchase_order', pid, {'source': 'ai', 'status': 'approved'}, raw=True)
    if p.get('suggestion'):
        s = db.get(c, 'mrp_suggestion', int(p['suggestion']))
        if s and s['status'] in ('new', 'adjusted'):
            db.update(c, 'mrp_suggestion', s['id'], {'status': 'released', 'purchase_order': pid, 'planner': who_}, raw=True)
    code = core.code_of(c, 'purchase_order', pid)
    return {'entity': 'purchase_order', 'id': pid, 'code': code, 'link': f'/purchase/orders?id={pid}',
            'text': _tr('已建采购订单 {code}（{supplier_name}，{len} 行，已审批，待下发供应商）', code=code, supplier_name=p['supplier_name'], len=len(lines))}


ops.OPS.setdefault('purchase_order', dict(label='起草采购订单', entity='purchase_order', risk='high', v=v_po, x=x_po, fields=[
    ('supplier', '供应商编码', 'str', True), ('lines', '明细 [{material 物料编码, qty, price?, due?}]', 'json', True), ('reason', '原因', 'str', False),
    ('suggestion', 'MRP 建议 id', 'int', False)]))


@router.post('/api/erp/ai/draft_po')
def draft_po(body: dict = Body(...), user=Depends(require('purchase.purchase_order.read'))):
    ai_require('erp')
    from ..services import ai_drafts
    with db.engine.connect() as c:
        s = db.get(c, 'mrp_suggestion', int(body['suggestion'])) if body.get('suggestion') else None
        mid = (s or {}).get('material') or (int(body['material']) if str(body.get('material') or '').isdigit() else None)
        m = db.get(c, 'material', mid) if mid else (ops._find(c, 'material', body.get('material')) if body.get('material') else None)
        if not m:
            raise HTTPException(400, _tr('请选物料或 MRP 建议'))
        sup = None
        if body.get('supplier'):
            sup = ops._find(c, 'supplier', body['supplier'])
        if not sup and s and s.get('supplier'):
            sup = db.get(c, 'supplier', s['supplier'])
        if not sup:
            b = core.best_supplier(c, m['id'])
            sup = db.get(c, 'supplier', b['supplier']) if b else None
        if not sup:
            raise HTTPException(409, _tr('{code} 没有合格供方，先维护供货清单', code=m['code']))
        qty = float(body.get('qty') or (s or {}).get('qty_adj') or (s or {}).get('qty') or 0)
        if qty <= 0:
            ss = float(m.get('safety_stock') or 0)
            qty = max(1.0, ss * 2 - core.stock_qty(c, m['id']))
        due = (s or {}).get('date_adj') or (s or {}).get('need_date')
        reason = body.get('reason') or ((s or {}).get('reason') or _tr('{code} 补货', code=m['code']))[:200]
    payload = {'supplier': sup['code'], 'lines': [{'material': m['code'], 'qty': qty, 'due': db.jsonable(due) if due else None}], 'reason': reason,
               'suggestion': s['id'] if s else None}
    try:
        d = ai_drafts.create(user, 'purchase_order', payload, source='assistant', summary=_tr('AI 起草：向 {name} 采购 {name2} × {qty:g}。通过之前不会建单。', name=sup['name'], name2=m['name'], qty=qty),
                             context=[{'kind': 'row', 'entity': 'mrp_suggestion' if s else 'material', 'id': (s or m)['id'], 'label': m['code']}])
    except ops.OpError as ex:
        raise HTTPException(400, str(ex))
    return d


def _can(user):
    return auth.can_write(user, 'purchase_order')
