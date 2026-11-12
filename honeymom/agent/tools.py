"""给大模型用的只读数据工具：智能体的"智能体"节点和右下角聊天助手共用同一套定义和"人话"进度描述。"""
from ..i18n import _tr
import json
import re

from ..services import ai
from .queries import QUERY_KINDS, run_query

DATA_TOOLS = {
    'query_sql': {'label': '只读 SQL 查询', 'schema': {
        'description': '在 MES 数据库上执行一条只读 SELECT 查询，返回最多 200 行。',
        'parameters': {'type': 'object', 'properties': {'sql': {'type': 'string'}}, 'required': ['sql']}}},
    'day_kpi': {'label': '某天汇总指标', 'schema': {
        'description': '取某天（YYYY-MM-DD，缺省为当天）的生产、质量、设备、能耗汇总指标。',
        'parameters': {'type': 'object', 'properties': {'day': {'type': 'string'}}}}},
    'kb_search': {'label': '知识库/SOP 检索', 'schema': {
        'description': '在工厂知识库（作业指导书、SOP、判定标准）里检索相关段落。',
        'parameters': {'type': 'object', 'properties': {'q': {'type': 'string', 'description': '检索的问题或关键词'}}, 'required': ['q']}}},
    'mes_query': {'label': 'MES 业务查询', 'schema': {
        'description': 'MES 常用业务查询，返回行和中文摘要。kind 可选：' + '；'.join(f'{k}={v[0]}' for k, v in QUERY_KINDS.items())
                       + '。常用参数：equipment（设备编号如 CNC3）、hours（最近几小时）、status、id、day、limit。',
        'parameters': {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': list(QUERY_KINDS)}, 'equipment': {'type': 'string'},
                                                         'hours': {'type': 'number'}, 'status': {'type': 'string'}, 'id': {'type': 'string'},
                                                         'day': {'type': 'string'}, 'limit': {'type': 'integer'}}, 'required': ['kind']}}},
}

TABLE_CN = None


def _tables_in(sql: str):
    global TABLE_CN
    if TABLE_CN is None:
        from ..schema import ENTITIES
        TABLE_CN = {e.name: e.label for e in ENTITIES}
        TABLE_CN.update({'tag_latest': _tr('实时点位'), 'tag_sample': _tr('点位趋势')})
    names = []
    for m in re.finditer(r'\b(?:from|join)\s+([a-z_0-9]+)', sql.lower()):
        lab = TABLE_CN.get(m.group(1))
        if lab and lab not in names:
            names.append(lab)
    return names


def phrase(name: str, args: dict) -> str:
    """工具调用 → 给用户看的进度描述（不出现工具名）。"""
    args = args or {}
    if name == 'query_sql':
        t = _tables_in(args.get('sql') or '')
        return _tr('正在查{join}数据…', join=_tr('、').join(t[:3])) if t else _tr('正在查数据库…')
    if name == 'day_kpi':
        return _tr('正在汇总{v}的生产、质量、设备和能耗指标…', v=args.get('day') or _tr('今天'))
    if name == 'kb_search':
        return _tr('正在查知识库：「{v}」…', v=(args.get('q') or '')[:30])
    if name == 'mes_query':
        lab = QUERY_KINDS.get(args.get('kind'), ('业务数据',))[0]
        who = args.get('equipment') or args.get('id') or ''
        hrs = _tr('最近 {hours:g} 小时', hours=args['hours']) if isinstance(args.get('hours'), (int, float)) else ''
        return _tr('正在读取{v}{hrs}的{lab}…', v=(who + ' ') if who else '', hrs=hrs, lab=lab)
    if name == 'go_to_page':
        return _tr('正在打开页面…')
    if name.startswith('run_agent_'):
        return _tr('正在运行智能体…')
    if name.startswith('propose_'):
        return _tr('正在检查这项修改能不能做…')
    if name in _ai_tools.TOOLS:
        return _ai_tools.phrase(name, args)
    return _tr('正在处理…')


def tool_schemas(names):
    return [{'type': 'function', 'function': {'name': n, **DATA_TOOLS[n]['schema']}} for n in names if n in DATA_TOOLS]


def call(name: str, args: dict):
    args = args or {}
    if name == 'query_sql':
        return ai.run_sql(args['sql'])
    if name == 'day_kpi':
        return ai.day_kpi(args.get('day') or None)
    if name == 'kb_search':
        return {'refs': ai.kb_search(args.get('q') or '')}
    if name == 'mes_query':
        kind = args.get('kind')
        return run_query(kind, {k: v for k, v in args.items() if k != 'kind'})
    if name in _ai_tools.TOOLS and not _ai_tools.TOOLS[name].write:       # 5.0 M6：统一工具层（出错作为结果返回给模型）
        try:
            return _ai_tools.call(name, args)
        except _ai_tools.ToolError as ex:
            return {'error': str(ex)}
    raise ValueError(_tr('没有这个工具：{name}', name=name))


def dumps(res, n=12000):
    return json.dumps(res, ensure_ascii=False, default=str)[:n]


# 5.0 M6：统一的只读工具层（services/ai_tools.py，MCP 也用同一份）登记进来：右下角助手、智能体节点都能选
from ..services import ai_tools as _ai_tools  # noqa: E402
_ai_tools.register_data_tools(DATA_TOOLS)
