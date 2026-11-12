"""跨工段的 AI 解读：缺陷回溯（功能 key: backtrace）、铸机协同排产（功能 key: coplan）。

统计和排产计算本身（/api/cross/backtrace、/api/cross/coplan）不是 AI，AI 关闭时照常可用；
这里是在它们之上的"AI 解读 / AI 建议"，各有独立的开关，演示模式回放录制（没有录制时给规则版说明）。

  POST /api/ai/backtrace/explain {days, group}    需要"缺陷回溯 AI 解读"
  POST /api/ai/coplan/advise {}                    需要"协同排产 AI 建议"
"""
from ..i18n import _tr
import json

from fastapi import APIRouter, Body, Depends

from .. import db
from ..auth import current_user
from . import ai_demo, cross

router = APIRouter()


def _llm(prompt, facts):
    from .ai import chat_once, llm_available
    if not llm_available():
        return None
    return chat_once(prompt + _tr('\n\n数据：\n') + json.dumps(facts, ensure_ascii=False, default=str),
                     system=_tr('你是铸造 + 机加一体厂的资深工艺 / 计划工程师。回答用简洁中文 Markdown，先结论后依据，只用给出的数字，不编造。'))


def _wrap(feature, op, key, facts, prompt, rule_text, blocks):
    from .ai_blocks import compose
    r = ai_demo.run(feature, op, key, live=lambda: (lambda t: {'text': t, 'by': 'ai'} if t else None)(_llm(prompt, facts)),
                    fallback=lambda: None, recordable=lambda r: bool(r), exact=True)
    if isinstance(r, dict) and r.get('text'):
        by = 'demo' if r.get('demo') else 'ai'
        res = compose(r['text'], [], json.dumps(facts, ensure_ascii=False, default=str))
        if by == 'demo':
            for b in res['blocks']:
                b.pop('unverified', None)
        return {'text': res['text'], 'blocks': blocks + [b for b in res['blocks'] if b['type'] != 'sources'], 'by': by}
    return {'text': rule_text, 'blocks': blocks, 'by': 'rule'}


@router.post('/api/ai/backtrace/explain')
def backtrace_explain(body: dict = Body(default={}), user=Depends(current_user)):
    days, group = int(body.get('days') or 7), body.get('group') or 'casting'
    if group not in cross.DEFECT_GROUPS:
        group = 'casting'
    with db.engine.connect() as c:
        res = cross.analyse(cross.backtrace_data(c, days, group))
    rule = cross.narrative(res, group)
    top = [f for f in res['factors'] if f['score'] >= 1.3][:4] or res['factors'][:3]
    facts = {'统计天数': days, '缺陷类别': cross.DEFECT_GROUPS[group][0], '零件数': res['n'], '不良数': res['bad'], '不良率': res['rate'],
             '因素': [{'因素': f['name'], '显著性 -log10p': f['score'], '最差分组': f['worst'], '是平均的倍数': f['lift'], '分组': f['bins']} for f in top]}
    blocks = []
    if top:
        f = top[0]
        blocks.append({'type': 'chart', 'kind': 'bar', 'title': _tr('{name}分组的不良率', name=f['name']), 'x': [b['label'] for b in f['bins']], 'format': 'pct',
                       'series': [{'name': _tr('不良率'), 'data': [b['rate'] for b in f['bins']]}]})
    out = _wrap('backtrace', 'explain', {'days': days, 'group': group}, facts,
                _tr('根据缺陷回溯的统计结果（{v}，近 {days} 天），说明：1) 最可能的根因（按证据强弱排序，说清楚显著性是否足够、样本是否太少）；2) 需要现场核实什么；3) 建议的纠正措施和要盯的参数窗口。不超过 200 字。', v=cross.DEFECT_GROUPS[group][0], days=days), rule, blocks)
    for i, b in enumerate(out['blocks'], 1):
        b['id'] = f'b{i}'
    return {**out, 'n': res['n'], 'bad': res['bad'], 'rate': res['rate']}


@router.post('/api/ai/coplan/advise')
def coplan_advise(body: dict = Body(default={}), user=Depends(current_user)):
    cp = cross.coplan(user)
    if not cp.get('ok'):
        return {'text': cp.get('msg') or _tr('没有数据'), 'blocks': [], 'by': 'rule'}
    facts = {k: cp[k] for k in ('caps', 'bottleneck', 'blank', 'store', 'target', 'cast_scrap', 'shifts', 'remain')}
    blocks = [{'type': 'chart', 'kind': 'bar', 'title': _tr('三个班的计划'), 'x': [s['shift'] for s in cp['shifts']],
               'series': [{'name': _tr('机加'), 'data': [s['machining'] for s in cp['shifts']]}, {'name': _tr('铸造'), 'data': [s['casting'] for s in cp['shifts']]},
                          {'name': _tr('班末毛坯'), 'data': [s['blank_end'] for s in cp['shifts']]}], 'unit': '件'}]
    out = _wrap('coplan', 'advise', {}, facts,
                _tr('根据两段协同排产的计算结果，给计划员 3~5 条具体建议：铸造各班开几小时造型线、熔炼放在什么时段、毛坯库存目标是否合理、机加瓶颈怎么缓解、有什么风险。不超过 220 字。'),
                '\n'.join('- ' + t for t in cp['tips']), blocks)
    for i, b in enumerate(out['blocks'], 1):
        b['id'] = f'b{i}'
    return out
