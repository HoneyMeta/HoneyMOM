"""自带的 8 个智能体模板（都能在演示数据上直接跑）。"""
from ..i18n import _tr
import copy
import json

from .engine import Graph


def build(nodes, edges):
    comps = {}
    for nid, typ, name, params in nodes:
        comps[nid] = {'type': typ, 'name': name, 'params': params, 'upstream': [], 'downstream': []}
    gedges = []
    for s, t, h in edges:
        comps[s]['downstream'].append({'target': t, 'handle': h})
        comps[t]['upstream'].append(s)
        gedges.append({'id': f"e-{s}-{t}-{h or 'out'}", 'source': s, 'target': t, 'sourceHandle': h})
    dsl = {'components': comps, 'graph': {'nodes': {}, 'edges': gedges}}
    g = Graph(dsl)
    layer = {}
    for n in g.order:
        ups = [layer[u] for u in g.fwd_up[n] if u in layer]
        layer[n] = (max(ups) + 1) if ups else 0
    cols = {}
    for n in g.order:
        cols.setdefault(layer[n], []).append(n)
    for L, ns in cols.items():
        for i, n in enumerate(ns):
            dsl['graph']['nodes'][n] = {'x': 40 + L * 260, 'y': 60 + (i - (len(ns) - 1) / 2) * 140 + 200}
    return dsl


def begin(**p):
    return ('begin', 'begin', _tr('开始'), p)


RAW = []


def tpl(key, name, desc, icon, trigger_label, permissions, chat_callable=False):
    def deco(fn):
        RAW.append({'key': key, 'name': name, 'description': desc, 'icon': icon, 'trigger_label': trigger_label, 'permissions': permissions,
                    'chat_callable': chat_callable, 'fn': fn})
        return fn
    return deco


@tpl('shift_report', '交接班报告', '每班结束时汇总产量、质量、设备报警、在制工单和未关安灯，写成交接班报告并发给班组长。', 'Notebook', '定时 · 每班结束', ['report', 'notify'])
def _t1():
    return build([
        begin(trigger='schedule', schedule_mode='shift', shift_at='end', offset_min=-10, inputs=[]),
        ('kpi', 'mes_query', _tr('今日指标'), {'entity': 'kpi'}),
        ('orders', 'mes_query', _tr('在制工单'), {'entity': 'work_orders', 'status': 'open', 'limit': 10}),
        ('alarms', 'mes_query', _tr('本班报警'), {'entity': 'alarms', 'hours': 8, 'limit': 20}),
        ('andons', 'mes_query', _tr('未关安灯'), {'entity': 'andon', 'status': 'open'}),
        ('write', 'llm', _tr('写交接班报告'), {'prompt': _tr('请根据下面的数据写一份交接班报告（Markdown，分"生产""质量""设备""遗留问题与提醒"四段，每段 2~4 条，只用给出的数字）。\n班次：{sys.shift}，时间：{sys.now}\n指标：{kpi@summary}\n在制工单：{orders@summary}\n报警：{alarms@summary}\n安灯：{andons@summary}')}),
        ('save', 'act_report', _tr('保存报告'), {'title': _tr('交接班报告 {sys.date} {sys.shift}'), 'kind': '交接班', 'content': '{write@text}'}),
        ('notify', 'act_notify', _tr('通知班组长'), {'channel': 'inbox', 'to_role': 'leader', 'title': _tr('交接班报告已生成（{sys.date} {sys.shift}）'), 'content': '{write@text}',
                                              'link': '{save@link}'}),
        ('reply', 'reply', _tr('输出'), {'content': '{write@text}'}),
    ], [('begin', 'kpi', None), ('begin', 'orders', None), ('begin', 'alarms', None), ('begin', 'andons', None), ('kpi', 'write', None), ('orders', 'write', None),
        ('alarms', 'write', None), ('andons', 'write', None), ('write', 'save', None), ('save', 'notify', None), ('notify', 'reply', None)])


@tpl('oee_analysis', 'OEE 损失分析', '查最近 24 小时各设备 OEE、损失树和停机记录，由大模型（可继续自己查库）找出最大的损失和改进建议。可在聊天里调用。', 'DataAnalysis',
     '聊天 / 手动', ['report'], chat_callable=True)
def _t2():
    return build([
        begin(trigger='chat', inputs=[]),
        ('oee', 'mes_query', _tr('OEE 与损失树'), {'entity': 'oee', 'hours': 24}),
        ('down', 'mes_query', _tr('停机与报警'), {'entity': 'downtime', 'hours': 24}),
        ('analyst', 'agent', _tr('效率分析师'), {'prompt': _tr('用户的问题：{sys.query}\n\n各设备 OEE（从低到高）：{oee@summary}\n\n停机与报警：{down@summary}\n\n请找出损失最大的 3 项（设备 + 损失类型 + 小时数），判断原因，给出可执行的改进建议。需要更多数据可以调用工具查询。用 Markdown，300 字以内。'),
                                         'tools': ['query_sql', 'mes_query'], 'max_rounds': 3}),
        ('save', 'act_report', _tr('保存分析'), {'title': _tr('OEE 损失分析 {sys.date}'), 'kind': 'OEE', 'content': '{analyst@text}'}),
        ('reply', 'reply', _tr('输出'), {'content': '{analyst@text}'}),
    ], [('begin', 'oee', None), ('begin', 'down', None), ('oee', 'analyst', None), ('down', 'analyst', None), ('analyst', 'save', None), ('save', 'reply', None)])


@tpl('andon_triage', '安灯分诊', '安灯一亮：判断属于设备/质量/物料哪类，查 SOP 和设备履历，给出第一步处置和建议响应人，推到安灯看板并通知对应角色。', 'Bell', '事件 · 安灯',
     ['notify'])
def _t3():
    return build([
        begin(trigger='event', event='andon', debounce_s=2, cooldown_s=300),
        ('cls', 'categorize', _tr('判断类别'), {'input': _tr('安灯类型：{begin@atype}；描述：{begin@description}；设备：{begin@equipment_name}'),
                                       'categories': [{'id': 'equip', 'name': _tr('设备故障'), 'description': _tr('设备停机、报警、异响、漏油、电气问题'), 'keywords': _tr('故障,停机,报警,异响,漏,主轴,电,equipment')},
                                                      {'id': 'quality', 'name': _tr('质量问题'), 'description': _tr('尺寸超差、毛刺、气孔、不合格'), 'keywords': _tr('质量,尺寸,毛刺,气孔,不合格,缺陷,quality')},
                                                      {'id': 'material', 'name': _tr('缺料等料'), 'description': _tr('缺料、等料、物料不对'), 'keywords': _tr('缺料,等料,物料,没料,material')}]}),
        ('r_eq', 'set_var', _tr('找设备员'), {'assignments': [{'key': 'role', 'value': 'maint'}, {'key': 'who', 'value': _tr('设备员')}]}),
        ('r_q', 'set_var', _tr('找质检员'), {'assignments': [{'key': 'role', 'value': 'qc'}, {'key': 'who', 'value': _tr('质检员')}]}),
        ('r_m', 'set_var', _tr('找仓管员'), {'assignments': [{'key': 'role', 'value': 'warehouse'}, {'key': 'who', 'value': _tr('仓管员')}]}),
        ('r_o', 'set_var', _tr('找班组长'), {'assignments': [{'key': 'role', 'value': 'leader'}, {'key': 'who', 'value': _tr('班组长')}]}),
        ('sop', 'kb_search', _tr('查 SOP'), {'query': '{begin@description} {begin@equipment_name} {cls@category_name}', 'top_k': 2}),
        ('hist', 'mes_query', _tr('设备履历'), {'entity': 'equipment', 'equipment': '{begin@equipment_code}'}),
        ('advise', 'llm', _tr('处置建议'), {'prompt': _tr('安灯 {begin@code}：{begin@description}（设备 {begin@equipment_code} {begin@equipment_name}，工位 {begin@station}）。\n分类：{cls@category_name}。\n相关 SOP：{sop@text}\n设备履历：{hist@summary}\n\n请给出：1）最可能的原因；2）现场第一步怎么处置；3）建议响应人：{vars.who}。总共 120 字以内。')}),
        ('board', 'act_notify', _tr('推到安灯看板'), {'channel': 'board', 'title': _tr('安灯 {begin@code}：{begin@description}'), 'content': '{advise@text}', 'level': 'warn'}),
        ('notify', 'act_notify', _tr('通知响应人'), {'channel': 'inbox', 'to_role': '{vars.role}', 'title': _tr('请响应安灯 {begin@code}（{cls@category_name}）'), 'content': '{advise@text}',
                                             'level': 'urgent', 'link': '/e/andon?id={begin@andon_id}'}),
        ('reply', 'reply', _tr('输出'), {'content': _tr('分类：{cls@category_name}；响应人：{vars.who}\n\n{advise@text}')}),
    ], [('begin', 'cls', None), ('cls', 'r_eq', 'equip'), ('cls', 'r_q', 'quality'), ('cls', 'r_m', 'material'), ('cls', 'r_o', 'other'),
        ('r_eq', 'sop', None), ('r_q', 'sop', None), ('r_m', 'sop', None), ('r_o', 'sop', None), ('sop', 'hist', None), ('hist', 'advise', None),
        ('advise', 'board', None), ('board', 'notify', None), ('notify', 'reply', None)])


@tpl('quality_ncr', '质量异常 → 8D 草稿', '出现不合格品时：查这张单子、同类缺陷和 SPC，起草 8D 前三步（团队、问题描述、临时措施），质检员审批后建 8D 草稿；驳回会带着原因重写。',
     'CircleClose', '事件 · 不合格品', ['ncr', 'notify'])
def _t4():
    return build([
        begin(trigger='event', event='ncr', debounce_s=3, cooldown_s=1800, dedup_key='{defect}'),
        ('detail', 'mes_query', _tr('不合格品详情'), {'entity': 'ncr_detail', 'id': '{begin@ncr_id}'}),
        ('spc', 'mes_query', _tr('质量与 SPC'), {'entity': 'quality', 'hours': 24}),
        ('sop', 'kb_search', _tr('判定标准'), {'query': _tr('{begin@defect_name} 判定 标准 处置'), 'top_k': 2}),
        ('draft', 'extract', _tr('起草 8D 前三步'), {
            'input': _tr('不合格品：{detail@summary}\n质量概况：{spc@summary}\n相关标准：{sop@text}\n审批人上次的意见（如有，必须据此修改）：{sys.feedback}'),
            'instructions': _tr('起草 8D 的 D1（团队）、D2（问题描述，5W2H，引用给出的数字）、D3（临时围堵措施，3 条以内）。'),
            'fields': [{'key': 'd1', 'label': _tr('D1 团队'), 'type': 'text', 'description': _tr('角色列表'), 'default': _tr('质量工程师（组长）、工艺工程师、班组长、设备员')},
                       {'key': 'd2', 'label': _tr('D2 问题描述'), 'type': 'text', 'description': '5W2H', 'default': '{detail@summary}'},
                       {'key': 'd3', 'label': _tr('D3 临时措施'), 'type': 'text', 'description': _tr('围堵措施'), 'default': _tr('同批次加严检验；已入库成品按批次复查；异常参数恢复到工艺窗口内。')}]}),
        ('approve', 'approval', _tr('质检员审批'), {'title': _tr('{begin@code}（{begin@defect_name}）：起草 8D'), 'summary': _tr('**D1 团队**：{draft@d1}\n\n**D2 问题描述**：{draft@d2}\n\n**D3 临时措施**：{draft@d3}'),
                                          'payload': json.dumps({'ncr': '{begin@code}', 'd1': '{draft@d1}', 'd2': '{draft@d2}', 'd3': '{draft@d3}', 'with_8d': True}, ensure_ascii=False),
                                          'assignee_role': 'qc', 'timeout_min': 0}),
        ('create', 'act_ncr', _tr('建 8D 草稿'), {'ncr': '{approve@payload.ncr}', 'd1': '{approve@payload.d1}', 'd2': '{approve@payload.d2}', 'd3': '{approve@payload.d3}',
                                         'with_8d': True, 'require_approval': False}),
        ('notify', 'act_notify', _tr('通知质检'), {'channel': 'inbox', 'to_role': 'qc', 'title': '{create@text}', 'content': _tr('审批人：{approve@by}'), 'link': '{create@link}'}),
        ('reply', 'reply', _tr('输出'), {'content': '{create@text}'}),
    ], [('begin', 'detail', None), ('begin', 'spc', None), ('begin', 'sop', None), ('detail', 'draft', None), ('spc', 'draft', None), ('sop', 'draft', None),
        ('draft', 'approve', None), ('approve', 'create', 'approved'), ('approve', 'draft', 'rejected'), ('create', 'notify', None), ('notify', 'reply', None)])


@tpl('downtime_repair', '停机 → 维修工单', '设备一停机：查设备履历、近 24 小时停机和 SOP，起草维修工单（故障描述、紧急程度、备件清单），设备员审批后建单并通知；驳回会带着原因重写。',
     'SetUp', '事件 · 设备停机', ['repair_order', 'notify'])
def _t5():
    return build([
        begin(trigger='event', event='equipment_state', to_state='down', etypes=['cnc', 'furnace', 'molding', 'shotblast', 'deburr', 'inspection'],
              exclude=_tr('等待上下料,操作工未到位,计划停'), debounce_s=3, cooldown_s=600),
        ('hist', 'mes_query', _tr('设备履历'), {'entity': 'equipment', 'equipment': '{begin@equipment_code}'}),
        ('down', 'mes_query', _tr('近 24 小时停机'), {'entity': 'downtime', 'equipment': '{begin@equipment_code}', 'hours': 24}),
        ('sop', 'kb_search', _tr('查 SOP'), {'query': _tr('{begin@equipment_name} {begin@alarm} 故障 停机 处理'), 'top_k': 2}),
        ('draft', 'extract', _tr('起草维修工单'), {
            'input': _tr('设备 {begin@equipment_code} {begin@equipment_name} 在 {begin@time} 停机，当前报警：{begin@alarm}。\n设备履历：{hist@summary}\n近 24 小时停机：{down@summary}\n相关 SOP：{sop@text}\n审批人上次的意见（如有，必须据此修改）：{sys.feedback}'),
            'instructions': _tr('起草一张维修工单。紧急程度只能是 urgent 或 normal（产线设备停机默认 urgent）。备件清单写可能要换的件，没有依据就写"待现场确认"。'),
            'fields': [{'key': 'fault', 'label': _tr('故障描述'), 'type': 'text', 'description': _tr('一句话'), 'default': _tr('{begin@equipment_name} 停机：{begin@alarm}')},
                       {'key': 'level', 'label': _tr('紧急程度'), 'type': 'text', 'description': _tr('urgent 或 normal'), 'default': 'urgent'},
                       {'key': 'parts', 'label': _tr('备件清单'), 'type': 'text', 'description': _tr('可能需要的备件'), 'default': _tr('待现场确认')},
                       {'key': 'cause', 'label': _tr('初步原因'), 'type': 'text', 'description': _tr('根据履历和 SOP 判断'), 'default': _tr('待现场排查')}]}),
        ('approve', 'approval', _tr('设备员审批'), {'title': _tr('{begin@equipment_code} 停机：建维修工单'), 'summary': _tr('**故障**：{draft@fault}\n\n**紧急程度**：{draft@level}\n\n**备件**：{draft@parts}\n\n**初步原因**：{draft@cause}'),
                                          'payload': json.dumps({'equipment': '{begin@equipment_code}', 'fault': '{draft@fault}', 'level': '{draft@level}',
                                                                 'parts_used': '{draft@parts}', 'cause': '{draft@cause}'}, ensure_ascii=False),
                                          'assignee_role': 'maint', 'timeout_min': 240, 'on_timeout': 'reject'}),
        ('create', 'act_repair_order', _tr('建维修工单'), {'equipment': '{approve@payload.equipment}', 'fault': '{approve@payload.fault}', 'level': '{approve@payload.level}',
                                                  'parts_used': '{approve@payload.parts_used}', 'cause': '{approve@payload.cause}', 'require_approval': False}),
        ('notify', 'act_notify', _tr('通知设备员'), {'channel': 'inbox', 'to_role': 'maint', 'title': '{create@text}', 'content': _tr('审批人：{approve@by}'), 'link': '{create@link}',
                                             'level': 'warn'}),
        ('reply', 'reply', _tr('输出'), {'content': '{create@text}'}),
    ], [('begin', 'hist', None), ('begin', 'down', None), ('begin', 'sop', None), ('hist', 'draft', None), ('down', 'draft', None), ('sop', 'draft', None),
        ('draft', 'approve', None), ('approve', 'create', 'approved'), ('approve', 'draft', 'rejected'), ('create', 'notify', None), ('notify', 'reply', None)])


@tpl('material_shortage', '缺料预警', '每两小时用在制工单的 BOM 对库存（扣安全库存），有缺口就写预警、起草物料申请，仓管员审批后提交并通知。', 'Box', '定时 · 每 2 小时',
     ['material_request', 'notify'])
def _t6():
    return build([
        begin(trigger='schedule', schedule_mode='cron', cron='0 */2 * * *'),
        ('need', 'mes_query', _tr('工单缺料'), {'entity': 'shortage'}),
        ('check', 'switch', _tr('有没有缺口'), {'cases': [{'id': 'short', 'name': _tr('有缺口'), 'logic': 'and', 'conditions': [{'left': '{need@count}', 'op': '>', 'right': '0'}]}]}),
        ('warn', 'llm', _tr('写缺料预警'), {'prompt': _tr('根据缺料清单写一段缺料预警（100 字以内）：哪些物料缺多少、影响哪个工单、建议先申请哪一项。\n缺料清单：{need@summary}')}),
        ('pick', 'extract', _tr('起草物料申请'), {'input': '{need@summary}', 'instructions': _tr('挑缺口最大、最影响生产的一项起草物料申请。material 用物料编码。'),
                                         'fields': [{'key': 'material', 'label': _tr('物料编码'), 'type': 'text', 'description': _tr('如 SGH-450-B'), 'default': '{need@rows.0.material_code}'},
                                                    {'key': 'qty', 'label': _tr('申请数量'), 'type': 'number', 'description': _tr('等于缺口'), 'default': '{need@rows.0.shortage}'},
                                                    {'key': 'work_order', 'label': _tr('工单号'), 'type': 'text', 'description': '', 'default': '{need@rows.0.work_order}'},
                                                    {'key': 'reason', 'label': _tr('原因'), 'type': 'text', 'description': '',
                                                     'default': _tr('缺料预警：{need@rows.0.material_name} 缺口 {need@rows.0.shortage}')}]}),
        ('approve', 'approval', _tr('仓管员审批'), {'title': _tr('缺料预警：申请 {pick@material} × {pick@qty}'), 'summary': '{warn@text}',
                                          'payload': json.dumps({'material': '{pick@material}', 'qty': '{pick@qty}', 'work_order': '{pick@work_order}', 'reason': '{pick@reason}'},
                                                                ensure_ascii=False), 'assignee_role': 'warehouse'}),
        ('req', 'act_material_request', _tr('提交物料申请'), {'material': '{approve@payload.material}', 'qty': '{approve@payload.qty}', 'work_order': '{approve@payload.work_order}',
                                                   'reason': '{approve@payload.reason}', 'require_approval': False}),
        ('notify', 'act_notify', _tr('通知仓库'), {'channel': 'inbox', 'to_role': 'warehouse', 'title': '{req@text}', 'content': '{warn@text}', 'link': '{req@link}'}),
        ('ok', 'reply', _tr('无缺料'), {'content': _tr('库存能满足在制工单需求（已扣安全库存），没有缺料。')}),
        ('reply', 'reply', _tr('输出'), {'content': '{warn@text}\n\n{req@text}'}),
    ], [('begin', 'need', None), ('need', 'check', None), ('check', 'warn', 'short'), ('check', 'ok', 'else'), ('warn', 'pick', None), ('pick', 'approve', None),
        ('approve', 'req', 'approved'), ('req', 'notify', None), ('notify', 'reply', None)])


@tpl('wo_late', '工单延误 → 重排建议', '工单预计赶不上交期时：看所有在制工单，起草排程调整建议（优先级怎么调），计划员审批后提交建议；驳回会带着原因重写。', 'Calendar',
     '事件 · 工单延误', ['schedule_proposal', 'notify'])
def _t7():
    return build([
        begin(trigger='event', event='wo_late', debounce_s=3, cooldown_s=3600),
        ('board', 'mes_query', _tr('在制工单'), {'entity': 'work_orders', 'status': 'open', 'limit': 15}),
        ('plan', 'extract', _tr('起草调整建议'), {
            'input': _tr('延误工单：{begin@code}（客户 {begin@customer}），交期 {begin@due}，预计完工 {begin@eta}，已完成 {begin@qty_done}/{begin@qty}，当前优先级 {begin@priority}。\n在制工单：{board@summary}\n审批人上次的意见（如有，必须据此修改）：{sys.feedback}'),
            'instructions': _tr('给出排程调整建议：延误工单的新优先级（1 最高），说明对其他工单的影响。只用给出的数字。'),
            'fields': [{'key': 'title', 'label': _tr('标题'), 'type': 'text', 'description': '', 'default': _tr('工单 {begin@code} 预计延误：建议提高优先级')},
                       {'key': 'proposal', 'label': _tr('建议内容'), 'type': 'text', 'description': _tr('3 句话以内'),
                        'default': _tr('把 {begin@code} 优先级提到 1，其余工单顺延；必要时安排加班。')},
                       {'key': 'priority', 'label': _tr('新优先级'), 'type': 'number', 'description': _tr('1~9，1 最高'), 'default': 1}]}),
        ('approve', 'approval', _tr('计划员审批'), {'title': '{plan@title}', 'summary': _tr('{plan@proposal}\n\n新优先级：{plan@priority}'),
                                          'payload': json.dumps({'title': '{plan@title}', 'work_order': '{begin@code}', 'proposal': '{plan@proposal}',
                                                                 'changes': [{'work_order': '{begin@code}', 'priority': '{plan@priority}', 'note': _tr('智能体建议')}]}, ensure_ascii=False),
                                          'assignee_role': 'planner'}),
        ('create', 'act_schedule', _tr('提交排程建议'), {'title': '{approve@payload.title}', 'work_order': '{approve@payload.work_order}', 'proposal': '{approve@payload.proposal}',
                                                'changes': '{approve@payload.changes}', 'require_approval': False}),
        ('notify', 'act_notify', _tr('通知计划员'), {'channel': 'inbox', 'to_role': 'planner', 'title': '{create@text}', 'content': '{approve@payload.proposal}', 'link': '{create@link}'}),
        ('reply', 'reply', _tr('输出'), {'content': '{create@text}'}),
    ], [('begin', 'board', None), ('board', 'plan', None), ('plan', 'approve', None), ('approve', 'create', 'approved'), ('approve', 'plan', 'rejected'),
        ('create', 'notify', None), ('notify', 'reply', None)])


@tpl('sop_qa', 'SOP 问答', '在知识库（作业指导书、SOP、判定标准）里找依据回答现场问题，注明出处。可在聊天里调用。', 'Reading', '聊天', [], chat_callable=True)
def _t8():
    return build([
        begin(trigger='chat', inputs=[]),
        ('kb', 'kb_search', _tr('查知识库'), {'query': '{sys.query}', 'top_k': 3}),
        ('answer', 'llm', _tr('组织回答'), {'system': _tr('你是工厂的 SOP 助手，只根据给出的资料回答，资料里没有就说"知识库里没有"，回答末尾注明出处标题。'),
                                     'prompt': _tr('资料：\n{kb@text}\n\n问题：{sys.query}')}),
        ('reply', 'reply', _tr('输出'), {'content': '{answer@text}'}),
    ], [('begin', 'kb', None), ('kb', 'answer', None), ('answer', 'reply', None)])


def templates():
    return [{k: v for k, v in t.items() if k != 'fn'} for t in RAW]


def template(key):
    for t in RAW:
        if t['key'] == key:
            return {**{k: v for k, v in t.items() if k != 'fn'}, 'dsl': copy.deepcopy(t['fn']())}
    return None


def blank():
    return build([begin(trigger='manual', inputs=[{'key': 'question', 'label': _tr('问题'), 'type': 'text', 'default': ''}]),
                  ('reply', 'reply', _tr('输出'), {'content': _tr('收到：{inputs.question}')})], [('begin', 'reply', None)])
