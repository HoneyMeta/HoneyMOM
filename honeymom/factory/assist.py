"""接入 AI 辅助（AI 功能"接入辅助"onboard；演示录音用 ai_demo；AI 关闭 = 只能人工）。

1. 对列（map_columns）：客户点表的表头和标准列对不上 → 同义词 + 大模型对列。
2. 点位草稿（draft_points / propose）：待确认 / 没设备的点，按 名称 + 地址 + 观测到的值的特征（单调递增 → 计数、只有 0/1 → 开关量、取值范围、
   和运行状态的相关性）起草 含义 / 单位 / 设备 / 用途 / 数据类型 + 置信度。规则版（拼音首字母词典 = 设备模板词汇 + 常用工业术语、数控系统编号、
   PLC 数据块分组 + 值特征）总是跑；AI 开着且能调大模型时，再让大模型在规则结果上修正。结果变成 AI 动作草稿（ai_drafts），工程师通过才写进模型。
3. 可疑点（suspicious）：值质量问题 + 点表声明和观测不符（声明计数却不单调、声明开关量却有很多值、单位 °C 却只有 0/1…）。
4. 读 PLC 程序（read_plc）：虚拟工厂软 PLC（LD / ST 程序 JSON + I/O 表）、PLCopen XML、博途符号表 CSV（Name, Path, Data Type, Logical Address, Comment）→
   建议采集点：循环开始 / 结束、动作开始（阀 / 指令）/ 结束（到位）、步号、故障位、计数。同样走草稿审批。
5. 打分（score）：原始现场的标准答案（虚拟工厂 answer_key.json）对比草稿：含义 / 设备 / 用途 / 单位的正确率。
"""
from __future__ import annotations

from ..i18n import _tr
from .. import i18n as _i18n
import csv
import io
import json
import re
import xml.etree.ElementTree as ET

import sqlalchemy as sa

from .. import db
from ..db import clock
from ..schema import TABLES
from . import model, pointtable

FEATURE = 'onboard'


class AssistError(Exception):
    pass


# ───────────────────────── 拼音首字母 ─────────────────────────
_GB = [(0xB0A1, 'A'), (0xB0C5, 'B'), (0xB2C1, 'C'), (0xB4EE, 'D'), (0xB6EA, 'E'), (0xB7A2, 'F'), (0xB8C1, 'G'), (0xB9FE, 'H'), (0xBBF7, 'J'),
       (0xBFA6, 'K'), (0xC0AC, 'L'), (0xC2E8, 'M'), (0xC4C3, 'N'), (0xC5B6, 'O'), (0xC5BE, 'P'), (0xC6DA, 'Q'), (0xC8BB, 'R'), (0xC8F6, 'S'),
       (0xCBFA, 'T'), (0xCDDA, 'W'), (0xCEF4, 'X'), (0xD1B9, 'Y'), (0xD4D1, 'Z'), (0xD7FA, '')]


def initials(text: str, n=4) -> str:
    """中文 → 拼音首字母（GB2312 一级字按区位码分声母；字母数字原样）。集成商常用这种缩写做点名（ZZFZ = 主轴负载）。"""
    out = []
    for ch in str(text or ''):
        if ch.isascii():
            if ch.isalnum():
                out.append(ch.upper())
        else:
            try:
                b = ch.encode('gb2312')
            except UnicodeEncodeError:
                continue
            if len(b) != 2:
                continue
            code = (b[0] << 8) | b[1]
            for i in range(len(_GB) - 1):
                if _GB[i][0] <= code < _GB[i + 1][0]:
                    if _GB[i][1]:
                        out.append(_GB[i][1])
                    break
        if len(out) >= n:
            break
    return ''.join(out)


# 常用工业点位术语：(含义, 单位, 用途, 数据类型)。词典 = 这些 + 所有设备模板里的点位含义
GLOSSARY = [
    ('运行状态', None, 'state', 'string'), ('当前报警', None, 'alarm', 'string'), ('报警', None, 'alarm', 'bool'), ('故障', None, 'alarm', 'bool'),
    ('实时功率', 'kW', 'power', 'float'), ('有功功率', 'kW', 'power', 'float'), ('累计电能', 'kWh', 'energy', 'float'), ('本炉电耗', 'kWh', 'energy', 'float'),
    ('主轴负载', '%', 'load', 'float'), ('主轴转速', 'rpm', 'other', 'float'), ('主轴倍率', '%', 'param', 'float'), ('进给倍率', '%', 'param', 'float'),
    ('主轴振动有效值', 'mm/s', 'other', 'float'), ('X 轴位置', 'mm', 'other', 'float'), ('Y 轴位置', 'mm', 'other', 'float'), ('Z 轴位置', 'mm', 'other', 'float'),
    ('累计加工件数', '件', 'count', 'int'), ('合格件', '件', 'count', 'int'), ('不合格件', '件', 'count', 'int'), ('上一件节拍', 's', 'other', 'float'),
    ('当前程序', None, 'other', 'string'), ('当前工步', None, 'step', 'string'), ('当前工件', None, 'other', 'string'), ('当前刀号', None, 'other', 'string'),
    ('制造商', None, 'other', 'string'), ('型号', None, 'other', 'string'), ('序列号', None, 'other', 'string'), ('运行模式', None, 'other', 'string'),
    ('开机时长', 'h', 'other', 'float'), ('三色灯', None, 'other', 'string'), ('循环计数', '次', 'count', 'int'), ('程序段号', None, 'step', 'int'),
    ('尺寸自动补偿', None, 'param', 'bool'), ('循环进行中', None, 'action', 'bool'), ('主轴运转', None, 'action', 'bool'),
    ('铁水温度', '°C', 'temp', 'float'), ('炉内重量', 'kg', 'other', 'int'), ('中频频率', 'Hz', 'other', 'float'), ('中频电压', 'V', 'other', 'float'),
    ('功率因数', None, 'other', 'float'), ('冷却水流量', 'm³/h', 'other', 'float'), ('进水温度', '°C', 'temp', 'float'), ('出水温度', '°C', 'temp', 'float'),
    ('炉衬剩余厚度', 'mm', 'other', 'float'), ('漏炉报警仪电流', 'mA', 'other', 'float'), ('炉次号', None, 'other', 'string'), ('熔炼阶段', None, 'other', 'string'),
    ('功率设定', 'kW', 'param', 'float'), ('出铁温度设定', '°C', 'param', 'float'), ('每炉增碳剂加入量', 'kg', 'param', 'float'),
    ('型砂水分', '%', 'quality', 'float'), ('紧实率', '%', 'quality', 'float'), ('湿压强度', 'kPa', 'other', 'float'), ('浇注温度', '°C', 'temp', 'float'),
    ('最近一型浇注温度', '°C', 'temp', 'float'), ('累计造型数', '型', 'count', 'int'), ('待浇注砂型数', '型', 'other', 'int'), ('抛头电流', 'A', 'load', 'float'),
    ('毛刷磨损', '%', 'other', 'float'), ('库位利用率', '%', 'other', 'float'), ('库存件数', '件', 'other', 'int'), ('电量', '%', 'other', 'float'),
    ('累计落砂型数', '型', 'count', 'int'), ('浇包铁水温度', '°C', 'temp', 'float'), ('残余镁', '%', 'quality', 'float'), ('在库件数', '件', 'other', 'int'),
    ('完成件数', '件', 'count', 'int'), ('处理包数', '包', 'count', 'int'), ('排气压力', 'MPa', 'other', 'float'), ('液压油温', '°C', 'temp', 'float'),
    ('模温', '°C', 'temp', 'float'), ('电机电流', 'A', 'load', 'float'), ('线速度', 'm/min', 'other', 'float'), ('通讯状态', None, 'other', 'bool'),
    ('急停', None, 'alarm', 'bool'), ('温度', '°C', 'temp', 'float'), ('压力', 'MPa', 'other', 'float'), ('电流', 'A', 'load', 'float'), ('电压', 'V', 'other', 'float'),
    ('流量', 'm³/h', 'other', 'float'), ('速度', None, 'other', 'float'), ('产量', '件', 'count', 'int'), ('计数', '件', 'count', 'int'),
    ('设定', None, 'param', 'float'), ('故障代码', None, 'alarm', 'int'), ('工步', None, 'step', 'int'), ('步号', None, 'step', 'int'),
]
_DICT = None


def dictionary() -> dict:
    """首字母 → (含义, 单位, 用途, 数据类型)。"""
    global _DICT
    if _DICT is not None:
        return _DICT
    d = {}
    for m, u, r, t in GLOSSARY:
        d.setdefault(initials(m), (m, u, r, t))
    for tp in model.templates_all():
        for p in tp.get('points') or []:
            if p.get('meaning') and '{' not in p['name'] and '*' not in p['name']:
                d.setdefault(initials(p['meaning']), (p['meaning'], p.get('unit'), p.get('role'), p.get('dtype')))
    _DICT = d
    return d


# ───────────────────────── 对列 ─────────────────────────
def map_columns(headers: dict, unmapped: dict, samples=None, force=False) -> dict | None:
    """{kind: [表头…]} → {mapping: {kind: {表头: 标准键}}, by}。规则：同义词包含 / 首字母；AI 开着时再交给大模型。"""
    from ..services import ai_demo, ai_switch
    syn = {'device': pointtable.SYN_DEV, 'point': pointtable.SYN_PT}
    out = {}
    for kind, hs in (unmapped or {}).items():
        used = set()
        for h in hs:
            hb = pointtable._bare(h)
            hit = None
            for k, names in syn[kind].items():
                if any(pointtable._bare(n) and (pointtable._bare(n) in hb or hb in pointtable._bare(n)) for n in names if len(pointtable._bare(n)) >= 2):
                    hit = k
                    break
            if hit and hit not in used:
                out.setdefault(kind, {})[str(h)] = hit
                used.add(hit)
    res = {'mapping': out, 'by': 'rule'}
    if ai_switch.enabled(FEATURE):
        from ..services import ai

        def live():
            if not ai.llm_available():
                return res
            keys = {'device': pointtable.DEV_LABEL, 'point': pointtable.PT_LABEL}
            prompt = (_tr('把客户点表的列名对到标准列。标准列（键: 中文名）：\n') + json.dumps(keys, ensure_ascii=False) + _tr('\n客户列名：\n')
                      + json.dumps(headers, ensure_ascii=False) + (_tr('\n样例行：\n') + json.dumps(samples, ensure_ascii=False)[:3000] if samples else '')
                      + _tr('\n只输出 JSON：{"device": {"客户列名": "标准键"}, "point": {...}}，对不上的列不要写。'))
            try:
                txt = ai.chat_once(prompt, _tr('你是工业数据接入工程师。只输出 JSON。'))
                j = json.loads(re.search(r'\{.*\}', txt, re.S).group(0))
                m = {k: {h: v for h, v in (j.get(k) or {}).items() if v in (pointtable.DEV_KEYS if k == 'device' else pointtable.PT_KEYS)} for k in ('device', 'point')}
                return {'mapping': {k: {**out.get(k, {}), **v} for k, v in m.items() if v or out.get(k)}, 'by': 'llm'}
            except Exception:
                return res
        res = ai_demo.run(FEATURE, 'columns', {'headers': headers}, live=live, fallback=lambda: res)
    return res if (res.get('mapping') or force) else None


# ───────────────────────── 点位草稿 ─────────────────────────
STATE_WORDS = {'idle', 'executing', 'stopped', 'setup', 'planned', 'starved', 'blocked', 'running', 'run', 'stop', 'fault', 'ready'}
NUM_RX = re.compile(r'(\d+)')


def _natkey(s):
    return [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', s or '')]


def features(st: dict | None, dtype: str | None) -> dict:
    """观测值特征：bool（只有 0/1）、counter（只增不减）、state（状态词）、range、const。"""
    f = {'n': (st or {}).get('n', 0)}
    if not st or not st.get('n'):
        return f
    vals = [str(v).lower() for v in st.get('values') or []]
    f['bool'] = bool(vals) and set(vals) <= {'0', '1', 'true', 'false', '0.0', '1.0'}
    f['counter'] = (st.get('ups', 0) >= 2 and st.get('downs', 0) == 0) and dtype in ('int', None, 'float')
    f['state'] = bool(vals) and len(set(vals) & STATE_WORDS) >= 1 and set(vals) <= STATE_WORDS | {''}
    f['const'] = st.get('changes', 0) == 0
    f['min'], f['max'] = st.get('min'), st.get('max')
    f['text'] = any(t == 'str' for t in st.get('types') or [])
    ra, sa_ = st.get('run_avg'), st.get('stop_avg')
    if ra is not None and sa_ is not None:
        f['run_corr'] = abs(ra - sa_) > max(abs(ra), abs(sa_), 1e-6) * 0.2
    return f


class Devices:
    def __init__(self, c):
        self.all = db.rows(c, 'equipment')
        self.by_code = {e['code']: e for e in self.all}
        self.by_tpl = {}
        for e in sorted(self.all, key=lambda e: _natkey(e['code'])):
            self.by_tpl.setdefault(e.get('template') or e['etype'], []).append(e)

    def nth(self, tpl, n):
        lst = self.by_tpl.get(tpl) or []
        for e in lst:                                     # 编码 / 名称里带同样编号的优先（3#加工中心、CNC3）
            if re.search(rf'(^|\D){n}(\D|$)', e['code']) or (e['name'] or '').startswith(f'{n}#'):
                return e
        return lst[n - 1] if 0 < n <= len(lst) else None


def _rule(p: dict, st: dict | None, devs: Devices, groups: dict) -> dict:
    """单个点位的规则草稿。"""
    code, addr = str(p.get('code') or ''), str(p.get('address') or '')
    dt = p.get('dtype')
    f = features(st, dt)
    out = {'id': p['id'], 'code': code, 'device': None, 'meaning': None, 'unit': None, 'role': None, 'dtype': dt, 'conf': 0.0, 'why': []}
    tokens = re.split(r'[._/\s:;=]+', code + ' ' + addr)
    # ---- 设备 ----
    for t in tokens:                                      # 编码 / 地址里直接带设备编码
        if t in devs.by_code and len(t) >= 2:
            out['device'], out['dconf'] = t, 0.9
            out['why'].append(_tr('名称里有设备编码 {t}', t=t))
            break
    m = re.match(r'^M(\d+)[_.]', code)
    if not out['device'] and m and (devs.by_tpl.get('cnc')):
        e = devs.nth('cnc', int(m.group(1)))
        if e:
            out['device'], out['dconf'] = e['code'], 0.8
            out['why'].append(_tr('数控系统编号 M{group} → 第 {group} 台加工中心', group=m.group(1)))
    m = re.match(r'^SYS_([A-Za-z]+)', code)
    if not out['device'] and m:
        k = m.group(1).upper()
        e = next((e for e in devs.all if e['code'].upper().startswith(k)), None) or \
            next((lst[0] for tcode, lst in devs.by_tpl.items() if str(tcode or '').upper().startswith(k) and lst), None)
        if e:
            out['device'], out['dconf'] = e['code'], 0.6
            out['why'].append(_tr('系统点 SYS_{k} → {code}', k=k, code=e["code"]))
    g = groups.get(p['id'])
    if not out['device'] and g:
        out['device'], out['dconf'] = g
        out['why'].append(_tr('同一数据块 / 从站的点按值特征归到同一台设备'))
    # ---- 含义 / 单位 / 用途 ----
    dic = dictionary()
    word = None
    m = re.match(r'^(?:M\d+|SYS_[A-Za-z]+)_([A-Za-z0-9]+)$', code)
    if m:
        word = m.group(1).upper()
    cand = [word] if word else []
    cand += [t.upper() for t in tokens if t and t.isalpha() and 2 <= len(t) <= 6]
    for w in cand:
        if not w:
            continue
        hit = dic.get(w) or (dic.get(w[:-1]) if w[-1:].isdigit() else None) or dic.get(re.sub(r'\d+$', '', w))
        if hit:
            out['meaning'], out['unit'], out['role'] = hit[0], hit[1], hit[2]
            out['dtype'] = out['dtype'] or hit[3]
            out['conf'] = 0.75
            out['why'].append(_tr('缩写 {w} = {v}', w=w, v=hit[0]))
            break
    pmc = re.search(r'(?:^|[._])([XYRIQM])(\d+)\.(\d)$', code) or re.search(r'DBX\d+\.\d$', code)
    if not out['meaning'] and pmc:
        kind = pmc.group(1) if pmc.lastindex else 'X'
        out['meaning'] = {'X': _tr('输入信号（到位 / 检测）'), 'I': _tr('输入信号（到位 / 检测）'), 'Y': _tr('输出信号（电磁阀 / 指令）'), 'Q': _tr('输出信号（电磁阀 / 指令）'),
                          'R': _tr('内部标志（循环 / 主轴）'), 'M': _tr('内部标志（循环 / 主轴）')}.get(kind, '开关量')
        out['role'], out['dtype'], out['conf'] = 'action', 'bool', 0.55
        out['why'].append(_tr('{kind} 地址开关量 → 动作信号', kind=kind))
    # ---- 值特征补 / 纠正 ----
    if f.get('state'):
        out['role'], out['meaning'] = 'state', out['meaning'] or _tr('运行状态')
        out['conf'] = max(out['conf'], 0.8)
        out['why'].append(_tr('值是状态词'))
    elif out['role'] is None and f.get('bool'):
        out['role'], out['dtype'] = 'action' if pmc else 'other', 'bool'
        out['conf'] = max(out['conf'], 0.4)
        out['why'].append(_tr('只有 0 / 1'))
    elif out['role'] is None and f.get('counter'):
        out['role'], out['meaning'], out['unit'] = 'count', out['meaning'] or _tr('计数'), out['unit'] or _tr('件')
        out['conf'] = max(out['conf'], 0.5)
        out['why'].append(_tr('值只增不减 → 计数'))
    elif out['role'] is None and f.get('max') is not None:
        lo, hi = f.get('min'), f.get('max')
        if 1100 <= (hi or 0) <= 1700 and (lo or 0) > 800:
            out.update(role='temp', meaning=out['meaning'] or _tr('铁水温度'), unit=out['unit'] or '°C')
            out['conf'] = max(out['conf'], 0.45)
            out['why'].append(_tr('取值 1100–1700 → 金属液温度'))
    if out['role'] is None:
        out['role'] = 'other'
        out['conf'] = max(out['conf'], 0.2)
    if out['role'] == 'count' and f.get('n') and f.get('ups', 0) == 0 and f.get('downs', 0) > 0:
        out['conf'] *= 0.6
    out['conf'] = round(min(0.95, out['conf'] * (1 if out['device'] else 0.8)), 2)
    out['dconf'] = out.get('dconf') or 0.0
    return out


def _groups(points, stats, devs: Devices) -> dict:
    """PLC 数据块 / Modbus 从站分组 → 按值特征（模板正常范围、缩写词典）猜设备类型 → 同类设备按编号顺序分配。返回 {点位 id: (设备编码, 置信度)}。"""
    from . import templates as tpl_mod
    grp = {}
    for p in points:
        code = str(p.get('code') or '')
        m = re.match(r'^(PLC\d+)\.(DB\d+)', code) or re.match(r'^(U\d+)\.', code)
        if m:
            grp.setdefault(m.group(0).rstrip('.'), []).append(p)
    dic = dictionary()
    tpls = model.templates_all()
    scored = []
    for gk, pts in grp.items():
        score = {}
        for p in pts:
            st = stats.get(p['id']) or {}
            v = st.get('value')
            w = re.split(r'[._]', str(p.get('code')))[-1].upper()
            hit = dic.get(w)
            for t in tpls:
                for tp in t.get('points') or []:
                    if hit and tp.get('meaning') == hit[0] and tp['name'].split('/')[0] != 'Common':
                        score[t['code']] = score.get(t['code'], 0) + 2
                    lo, hi = model.parse_range(tp.get('normal'))
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and lo is not None and hi is not None and hi - lo < max(abs(hi), 1) * 0.5 \
                            and lo <= v <= hi and tp['name'].split('/')[0] != 'Common':
                        score[t['code']] = score.get(t['code'], 0) + 1
        if score:
            best = max(score.items(), key=lambda x: x[1])
            scored.append((gk, best[0], best[1], pts))
    used = {}
    out = {}
    for gk, tcode, sc, pts in sorted(scored, key=lambda x: _natkey(x[0])):
        lst = [e for e in devs.by_tpl.get(tcode) or [] if e['code'] not in used.get(tcode, set())]
        if not lst:
            continue
        e = lst[0]
        used.setdefault(tcode, set()).add(e['code'])
        for p in pts:
            out[p['id']] = (e['code'], round(min(0.7, 0.3 + sc * 0.05), 2))
    return out


def propose(c, datasource=None, ids=None, limit=400, use_llm=True) -> list[dict]:
    """规则（+ 大模型）草稿：[{id, code, device, meaning, unit, role, dtype, conf, why}]。只看待确认的点（或指定 ids）。"""
    from .live import LIVE
    tp = TABLES['tag_point']
    q = sa.select(tp).where(tp.c.enabled.is_(True))
    if ids:
        q = q.where(tp.c.id.in_([int(i) for i in ids]))
    else:
        q = q.where((tp.c.status == 'pending') & (tp.c.equipment.is_(None) | tp.c.desc.is_(None) | tp.c.role.is_(None)))
    if datasource:
        q = q.where(tp.c.datasource == int(datasource))
    pts = [dict(r) for r in c.execute(q.order_by(tp.c.id).limit(max(1, min(int(limit), 5000)))).mappings()]
    stats = {p['id']: (LIVE.pt[p['id']].out() if p['id'] in LIVE.pt else p.get('stats')) for p in pts}
    devs = Devices(c)
    groups = _groups(pts, stats, devs)
    rows = [_rule(p, stats.get(p['id']), devs, groups) for p in pts]
    if use_llm:
        rows = _llm_refine(rows, pts, stats, devs)
    return rows


def _llm_refine(rows, pts, stats, devs):
    from ..services import ai, ai_demo, ai_switch
    if not ai_switch.enabled(FEATURE):
        return rows
    dev_list = [{'code': e['code'], 'name': e['name'], 'type': e.get('template') or e['etype']} for e in devs.all][:200]
    by_id = {p['id']: p for p in pts}
    out = []
    for i in range(0, len(rows), 60):
        chunk = rows[i:i + 60]
        payload = {'devices': dev_list, 'points': [{'id': r['id'], 'code': r['code'], 'address': by_id[r['id']].get('address'), 'dtype': r['dtype'],
                                                     'observed': {k: (stats.get(r['id']) or {}).get(k) for k in ('value', 'min', 'max', 'changes', 'ups', 'downs', 'values')},
                                                     'guess': {k: r[k] for k in ('device', 'meaning', 'unit', 'role', 'conf')}} for r in chunk]}

        def live(chunk=chunk, payload=payload):
            if not ai.llm_available():
                return {'rows': chunk}
            prompt = (_tr('下面是工厂现场的采集点（点名是 PLC 地址或拼音首字母缩写）和规则给的初步判断、观测到的值。请给每个点补全：所属设备编码（从设备清单里选）、中文含义、工程单位、用途（state alarm count power energy load temp quality action step param other）、置信度 0~1。\n')
                      + json.dumps(payload, ensure_ascii=False, default=str)
                      + _tr('\n只输出 JSON：{"points": [{"id":…, "device":…, "meaning":…, "unit":…, "role":…, "conf":…}]}'))
            try:
                txt = ai.chat_once(prompt, _tr('你是工业数据接入工程师，熟悉 PLC / 数控点名和拼音缩写。只输出 JSON。'))
                j = json.loads(re.search(r'\{.*\}', txt, re.S).group(0))
            except Exception:
                return {'rows': chunk}
            got = {int(x['id']): x for x in j.get('points') or [] if str(x.get('id', '')).isdigit()}
            res = []
            for r in chunk:
                x = got.get(r['id'])
                if x and float(x.get('conf') or 0) >= r['conf']:
                    r = {**r, **{k: x.get(k) for k in ('device', 'meaning', 'unit', 'role') if x.get(k)}, 'conf': round(float(x.get('conf') or 0), 2),
                         'why': r['why'] + [_tr('大模型修正')], 'dconf': max(r.get('dconf') or 0, float(x.get('conf') or 0))}
                    if r['device'] not in devs.by_code:
                        r['device'] = None
                res.append(r)
            return {'rows': res, 'llm': True}
        res = ai_demo.run(FEATURE, 'draft', {'points': [{'code': p['code'], 'address': p.get('address')} for p in payload['points']]}, live=live,
                          fallback=lambda chunk=chunk: {'rows': chunk}, recordable=lambda r: r.get('llm'))
        out += res.get('rows') or chunk
    return out


def draft_points(user, datasource=None, ids=None, limit=400) -> dict:
    """起草点位补全（AI 草稿，进审批）：每台设备一张草稿（没分到设备的一张），工程师在接入中心 / 审批中心通过或修改后通过。"""
    from ..services import ai_drafts, ai_switch, ops
    if not ai_switch.enabled(FEATURE):
        raise AssistError(ai_switch.off_message(FEATURE))
    with db.engine.connect() as c:
        rows = propose(c, datasource, ids, limit)
    if not rows:
        return {'drafts': [], 'points': 0, 'msg': _tr('没有待补全的点位')}
    by_dev = {}
    for r in rows:
        by_dev.setdefault(r['device'] or '', []).append(r)
    drafts = []
    for dev, rs in sorted(by_dev.items()):
        for i in range(0, len(rs), 80):
            part = rs[i:i + 80]
            payload = {'device': dev or None, 'points': [{k: r[k] for k in ('id', 'code', 'device', 'meaning', 'unit', 'role', 'dtype', 'conf')} | {'why': _tr('；').join(r['why'])[:200]}
                                                         for r in part]}
            avg = sum(r['conf'] for r in part) / len(part)
            try:
                d = ai_drafts.create(user, 'onboard_points', payload, source='onboard',
                                     summary=_tr('{v}：{len} 个点位的含义 / 单位 / 用途草稿，平均置信度 {avg:.0%}', v=dev or _tr('未分配设备'), len=len(part), avg=avg))
            except ops.OpError as ex:
                raise AssistError(str(ex))
            drafts.append({'id': d['id'], 'device': dev, 'points': len(part), 'conf': round(avg, 2)})
    return {'drafts': drafts, 'points': len(rows), 'by': 'llm' if any(w in _i18n.variants('大模型修正') for r in rows for w in r['why']) else 'rule'}


# ── 草稿操作：onboard_points（补全点位）、onboard_plc（按 PLC 程序新建采集点） ──
def v_points(c, p):
    pts = p.get('points') or []
    if not pts:
        raise _op_err(_tr('草稿里没有点位'))
    out = []
    devs = {e['code'] for e in db.rows(c, 'equipment')}
    for x in pts:
        if not db.get(c, 'tag_point', int(x['id'])):
            raise _op_err(_tr('点位 {get} 不存在了', get=x.get('code')))
        if x.get('device') and x['device'] not in devs:
            raise _op_err(_tr('设备 {device} 不存在', device=x['device']))
        if x.get('role') and x['role'] not in pointtable.ROLE_VALUES:
            raise _op_err(_tr('用途 {role} 不对', role=x['role']))
        out.append({k: x.get(k) for k in ('id', 'code', 'device', 'meaning', 'unit', 'role', 'dtype', 'conf', 'why')})
    return {'device': p.get('device'), 'points': out}


def x_points(c, p, who, source):
    eqs = {e['code']: e['id'] for e in db.rows(c, 'equipment')}
    n = 0
    for x in p['points']:
        pt = db.get(c, 'tag_point', int(x['id']))
        upd = {'origin': 'ai', 'confidence': x.get('conf'), 'status': 'confirmed', 'confirmed_by': who, 'confirmed_at': clock.now()}
        for k, col in (('meaning', 'desc'), ('unit', 'unit'), ('role', 'role'), ('dtype', 'dtype')):
            if x.get(k) not in (None, ''):
                upd[col] = x[k]
        db.update(c, 'tag_point', pt['id'], upd, raw=True)
        if x.get('device') and eqs.get(x['device']) != pt['equipment']:
            model.set_point_device(c, pt['id'], eqs.get(x['device']))
        n += 1
    model.INDEX.dirty = True
    return {'entity': 'tag_point', 'id': None, 'code': '', 'text': _tr('已按草稿补全并确认 {n} 个采集点', n=n), 'link': '/onboard/check'}


def d_points(c, p):
    ch = []
    for x in p['points']:
        pt = db.get(c, 'tag_point', int(x['id'])) or {}
        e = db.get(c, 'equipment', pt.get('equipment')) if pt.get('equipment') else None
        before = ' / '.join(str(v) for v in (e['code'] if e else '—', pt.get('desc') or '—', pt.get('unit') or '—', pt.get('role') or '—'))
        after = ' / '.join(str(v) for v in (x.get('device') or '—', x.get('meaning') or '—', x.get('unit') or '—', x.get('role') or '—')) + _tr('（置信度 {v:.0%}）', v=x.get('conf') or 0)
        ch.append({'key': str(x['id']), 'label': x.get('code') or str(x['id']), 'before': before, 'after': after})
    return 'update', {'entity': 'tag_point', 'id': None, 'label': _tr('{len} 个采集点', len=len(p['points'])), 'kind': '采集点'}, ch


def v_plc(c, p):
    pts = p.get('points') or []
    if not pts:
        raise _op_err(_tr('没有建议的采集点'))
    ds = db.get(c, 'datasource', int(p['datasource'])) if p.get('datasource') else None
    if not ds:
        raise _op_err(_tr('请选数据源（新建的采集点挂在哪个数据源上）'))
    if p.get('device') and not db.get(c, 'equipment', code=p['device']):
        raise _op_err(_tr('设备 {device} 不存在', device=p['device']))
    return {'datasource': ds['id'], 'device': p.get('device'), 'source': p.get('source'),
            'points': [{k: x.get(k) for k in ('symbol', 'address', 'meaning', 'role', 'dtype', 'kind', 'why')} for x in pts if x.get('symbol')]}


def x_plc(c, p, who, source):
    ds = db.get(c, 'datasource', p['datasource'])
    dev = db.get(c, 'equipment', code=p['device']) if p.get('device') else None
    n = 0
    for x in p['points']:
        sem = {'cycle_start': 'cycle', 'cycle_end': 'cycle'}.get(x.get('kind'))
        row = model.point_row({'code': f"{dev['code']}.PLC.{x['symbol']}" if dev else f"PLC.{x['symbol']}", 'device': dev['code'] if dev else None,
                               'meaning': x.get('meaning'), 'role': x.get('role'), 'dtype': x.get('dtype'), 'protocol': ds['protocol'],
                               'address': x.get('address') or x['symbol'], 'name': f"PLC/{x['symbol']}", 'sem': sem,
                               'remark': _tr('读 PLC 程序：{v}', v=x.get('why') or '')[:190]}, dev, ds, 'ai')
        model.upsert_point(c, row)
        n += 1
    model.INDEX.dirty = True
    return {'entity': 'tag_point', 'id': None, 'code': '', 'text': _tr('已按 PLC 程序新建 {n} 个待确认采集点（地址请按网关映射核对）', n=n), 'link': '/onboard/check'}


def d_plc(c, p):
    ch = [{'key': x['symbol'], 'label': x['symbol'], 'before': None, 'after': f"{x.get('meaning') or ''} · {x.get('role')} · {x.get('address') or ''}"} for x in p['points']]
    return 'create', {'entity': 'tag_point', 'id': None, 'label': _tr('新建 {len} 个采集点', len=len(p['points'])), 'kind': '采集点'}, ch


def _op_err(msg):
    from ..services import ops
    return ops.OpError(msg)


def register_ops():
    from ..services import ops
    ops.OPS.setdefault('onboard_points', dict(label=_tr('补全采集点（含义 / 单位 / 设备 / 用途）'), entity='tag_point', risk='high', v=v_points, x=x_points,
                                              diff=d_points, fields=[('device', _tr('设备'), 'str', False), ('points', _tr('点位 [{id, device, meaning, unit, role}]'), 'json', True)]))
    ops.OPS.setdefault('onboard_plc', dict(label=_tr('按 PLC 程序新建采集点'), entity='tag_point', risk='high', v=v_plc, x=x_plc, diff=d_plc,
                                           fields=[('datasource', _tr('数据源'), 'int', True), ('device', _tr('设备'), 'str', False),
                                                   ('points', _tr('建议点位 [{symbol, address, meaning, role}]'), 'json', True)]))


def list_drafts(user, status='pending') -> list[dict]:
    from ..schema import approval_t
    from ..services import ai_drafts
    with db.engine.connect() as c:
        q = sa.select(approval_t).where(approval_t.c.kind == 'ai_draft')
        if status != 'all':
            q = q.where(approval_t.c.status == status)
        rows = [dict(r) for r in c.execute(q.order_by(approval_t.c.id.desc()).limit(300)).mappings()]
        return [ai_drafts.draft_out(c, r, user) for r in rows if ((r.get('fields') or {}).get('action') or '').startswith('onboard_')]


# ───────────────────────── 可疑点 ─────────────────────────
def suspicious(c, user=None) -> list[dict]:
    from .live import check
    chk = check(c, user=user)
    out = []
    for r in chk['rows']:
        why = list(r['flags_cn'])
        st = {'values': [], 'ups': 0, 'downs': 0}
        if r['n']:
            from .live import LIVE
            s = LIVE.pt.get(r['id'])
            st = s.out() if s else st
        vals = [str(v).lower() for v in st.get('values') or []]
        if r['role'] == 'count' and r['n'] >= 10 and st.get('downs', 0) > st.get('ups', 0):
            why.append(_tr('声明是计数，但值在减小'))
        if r['dtype'] == 'bool' and len(vals) > 2:
            why.append(_tr('声明是开关量，但有多个取值'))
        if (r['unit'] or '') in ('°C', '℃') and vals and set(vals) <= {'0', '1', 'true', 'false'}:
            why.append(_tr('单位是温度，但值只有 0 / 1'))
        if r['role'] == 'state' and vals and not (set(vals) & {s.lower() for s in model.STATE_SYN.values()} | set(model.STATE_SYN)):
            why.append(_tr('声明是运行状态，但值不像状态'))
        if not r['meaning'] and r['color'] != 'grey':
            why.append(_tr('缺含义'))
        if why:
            out.append({**{k: r[k] for k in ('id', 'code', 'device', 'meaning', 'unit', 'role', 'dtype', 'value', 'color')}, 'why': why})
    return out


# ───────────────────────── 读 PLC 程序 ─────────────────────────
KW = {
    'cycle_start': re.compile(r'cycle.?start|start.?cycle|循环启动|循环开始|^start$|启动按钮|启动 ', re.I),
    'cycle_end': re.compile(r'cycle.?(end|done|complete)|循环结束|循环完成|^done$|_done$', re.I),
    'fault': re.compile(r'fault|alarm|err|estop|e_stop|故障|报警|急停|超时', re.I),
    'step': re.compile(r'step|seq|工步|步号|段号|状态字', re.I),
    'count': re.compile(r'count|cnt|ctu|计数|产量|合格数', re.I),
    'valve': re.compile(r'valve|cmd|extend|retract|clamp|push|fwd|阀|伸出|缩回|夹紧|松开|推|正转|动作', re.I),
    'sensor': re.compile(r'front|back|home|ok$|_ok|到位|检测到|光电|原点|sensor|detect', re.I),
}


def _classify(sym: dict) -> tuple[str | None, str | None, str]:
    """符号 → (建议类型, 用途, 原因)。"""
    name, cmt, typ, addr = sym['name'], sym.get('comment') or '', (sym.get('type') or '').upper(), (sym.get('address') or '').upper()
    txt = f'{name} {cmt}'
    is_out = addr.startswith('%Q') or addr.startswith('Q') or sym.get('dir') == 'out'
    is_in = addr.startswith('%I') or addr.startswith('I') or sym.get('dir') == 'in'
    if KW['fault'].search(txt) and typ in ('BOOL', ''):
        return 'fault', 'alarm', _tr('故障 / 报警位')
    if KW['step'].search(txt) and typ in ('INT', 'DINT', 'WORD', 'UINT', 'SINT', 'USINT'):
        return 'step', 'step', _tr('步号 / 工步')
    if sym.get('fb') == 'CTU' or (KW['count'].search(txt) and typ in ('INT', 'DINT', 'UINT', 'WORD')):
        return 'count', 'count', _tr('计数器')
    if typ in ('BOOL', '') and KW['cycle_start'].search(txt) and not is_out and 'STOP' not in name.upper():
        return 'cycle_start', 'action', _tr('循环启动信号')
    if typ in ('BOOL', '') and KW['cycle_end'].search(txt):
        return 'cycle_end', 'action', _tr('循环结束信号')
    if typ in ('BOOL', '') and is_out and KW['valve'].search(txt):
        return 'action_start', 'action', _tr('输出：电磁阀 / 指令（动作开始）')
    if typ in ('BOOL', '') and is_in and KW['sensor'].search(txt):
        return 'action_end', 'action', _tr('输入：到位 / 检测（动作结束）')
    if sym.get('coil') and typ in ('BOOL', ''):
        return 'state', 'other', _tr('程序里的线圈（运行 / 联锁标志）')
    return None, None, ''


def parse_plc(name: str, data: bytes, kind: str | None = None) -> dict:
    """PLC 程序 → {kind, symbols: [{name, type, address, comment, dir, coil, fb}], rungs, lines}。"""
    text = None
    for enc in ('utf-8-sig', 'utf-16', 'gbk'):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise AssistError(_tr('文件编码认不出（UTF-8 / UTF-16 / GBK）'))
    low = name.lower()
    kind = kind or ('plcopen' if low.endswith('.xml') or '<project' in text[:2000] else 'csv' if low.endswith('.csv') else 'vf' if text.lstrip()[:1] in '{[' else
                    'st' if 'END_PROGRAM' in text.upper() or 'END_VAR' in text.upper() else 'csv')
    syms, info = {}, {'rungs': 0, 'lines': 0}

    def add(n, **kw):
        if not n:
            return
        s = syms.setdefault(n, {'name': n})
        for k, v in kw.items():
            if v not in (None, '') and not s.get(k):
                s[k] = v
    if kind == 'vf':
        j = json.loads(text)
        prog = j.get('program') or j
        for io_ in j.get('io') or prog.get('io') or []:
            add(io_.get('name'), type=io_.get('type'), address=io_.get('addr'), comment=io_.get('comment'), dir=io_.get('dir'))
        for v in prog.get('vars') or []:
            add(v.get('name'), type=v.get('type'), comment=v.get('comment'))
        src = prog.get('source')
        if isinstance(src, dict):
            for rg in src.get('rungs') or []:
                info['rungs'] += 1
                _walk_ld(rg.get('elements') or [], add, rg.get('comment'))
        elif isinstance(src, str):
            _scan_st(src, add)
            info['lines'] = src.count('\n')
    elif kind == 'plcopen':
        root = ET.fromstring(text)
        ns = {'p': root.tag.split('}')[0].strip('{')} if root.tag.startswith('{') else {}
        P = 'p:' if ns else ''
        for v in root.iter(('{%s}' % ns['p'] if ns else '') + 'variable'):
            t = v.find(f'{P}type', ns)
            tname = t[0].tag.split('}')[-1] if t is not None and len(t) else None
            if tname == 'derived' and t is not None:
                tname = t[0].get('name')
            doc = v.find(f'.//{P}documentation', ns)
            dtext = ''.join(doc.itertext()).strip() if doc is not None else None
            add(v.get('name'), type=(tname or '').upper(), address=v.get('address'), comment=dtext)
        for el in root.iter():
            tag = el.tag.split('}')[-1]
            if tag in ('contact', 'coil'):
                var = el.find(f'{P}variable', ns)
                if var is not None and var.text:
                    add(var.text.strip(), coil=(tag == 'coil') or None)
                    info['rungs'] += 1 if tag == 'coil' else 0
            elif tag == 'block' and el.get('typeName') in ('CTU', 'CTD', 'CTUD'):
                add(el.get('instanceName'), type='INT', fb='CTU', comment=_tr('计数器 ') + (el.get('instanceName') or ''))
            elif tag == 'ST':
                st = ''.join(el.itertext())
                _scan_st(st, add)
                info['lines'] += st.count('\n')
    elif kind == 'st':
        _scan_st(text, add)
        info['lines'] = text.count('\n')
    else:
        rd = list(csv.reader(io.StringIO(text), delimiter=';' if text.count(';') > text.count(',') else ','))
        if not rd:
            raise AssistError(_tr('符号表是空的'))
        h = [x.strip().lower() for x in rd[0]]

        def col(*names):
            return next((h.index(n) for n in names if n in h), None)
        ci, ct, ca, cc = col('name', _tr('名称'), _tr('符号')), col('data type', 'datatype', _tr('数据类型')), col('logical address', 'address', _tr('地址')), col('comment', _tr('注释'), _tr('说明'))
        if ci is None:
            raise AssistError(_tr('符号表要有 Name / 名称 列（博途导出：Name, Path, Data Type, Logical Address, Comment）'))
        for r in rd[1:]:
            if len(r) <= ci or not r[ci].strip():
                continue
            add(r[ci].strip().strip('"'), type=(r[ct].strip().upper() if ct is not None and len(r) > ct else None),
                address=(r[ca].strip() if ca is not None and len(r) > ca else None), comment=(r[cc].strip() if cc is not None and len(r) > cc else None))
    return {'kind': kind, 'symbols': list(syms.values()), **info}


def _walk_ld(els, add, comment):
    for e in els:
        if isinstance(e, dict):
            t = e.get('t')
            if t in ('COIL', 'SET', 'RESET'):
                add(e.get('var'), coil=True, comment=None)
            elif t in ('NO', 'NC', 'P', 'N'):
                add(e.get('var'))
            elif t == 'FB':
                add(e.get('inst'), fb=e.get('fb'), type='INT' if e.get('fb') in ('CTU', 'CTD') else None, comment=f"{e.get('fb')} {comment or ''}".strip())
            for k in ('par', 'elements'):
                for sub in e.get(k) or []:
                    _walk_ld(sub if isinstance(sub, list) else [sub], add, comment)
        elif isinstance(e, list):
            _walk_ld(e, add, comment)


def _scan_st(src: str, add):
    for m in re.finditer(r'^\s*([A-Za-z_]\w*)\s*(?:AT\s+(%[IQM][XBWD]?[\d.]+))?\s*:\s*([A-Za-z_]\w*)[^;]*;\s*(?:\(\*\s*(.*?)\s*\*\)|//\s*(.*))?', src, re.M):
        add(m.group(1), type=m.group(3).upper(), address=m.group(2), comment=(m.group(4) or m.group(5) or '').strip() or None)
    for m in re.finditer(r'([A-Za-z_]\w*)\s*:=', src):
        add(m.group(1), coil=True)
    for m in re.finditer(r'([A-Za-z_]\w*)\s*\(\s*CU\s*:=', src):
        add(m.group(1), fb='CTU', type='INT', comment=_tr('计数器'))


def read_plc(user, name, data, kind=None, device=None, datasource=None) -> dict:
    from ..services import ai_drafts, ai_switch, ops
    if not ai_switch.enabled(FEATURE):
        raise AssistError(ai_switch.off_message(FEATURE))
    parsed = parse_plc(name, data, kind)
    props = []
    for s in parsed['symbols']:
        k, role, why = _classify(s)
        if not k:
            continue
        dt = 'bool' if (s.get('type') or 'BOOL') == 'BOOL' else 'float' if (s.get('type') or '') in ('REAL', 'LREAL') else 'string' if 'STRING' in (s.get('type') or '') else 'int'
        props.append({'symbol': s['name'] + ('.CV' if s.get('fb') == 'CTU' else ''), 'address': s.get('address'), 'meaning': s.get('comment') or s['name'],
                      'role': role, 'dtype': dt, 'kind': k, 'why': why})
    with db.engine.connect() as c:
        if datasource is None:
            d = db.rows(c, 'datasource', protocol='opcua', limit=1) or db.rows(c, 'datasource', limit=1)
            datasource = d[0]['id'] if d else None
    draft = None
    if props:
        try:
            draft = ai_drafts.create(user, 'onboard_plc', {'datasource': datasource, 'device': device, 'source': name, 'points': props}, source='onboard',
                                     summary=_tr('读 {name}（{kind}）：{len} 个符号，建议采集 {len2} 个点（循环 {sum}、动作 {sum2}、步号 {sum3}、故障 {sum4}）', name=name, kind=parsed["kind"], len=len(parsed["symbols"]), len2=len(props), sum=sum(1 for p in props if p["kind"].startswith("cycle")), sum2=sum(1 for p in props if p["kind"].startswith("action")), sum3=sum(1 for p in props if p["kind"] == "step"), sum4=sum(1 for p in props if p["kind"] == "fault")))
        except ops.OpError as ex:
            raise AssistError(str(ex))
    return {'kind': parsed['kind'], 'symbols': len(parsed['symbols']), 'rungs': parsed.get('rungs'), 'proposals': props, 'draft': draft['id'] if draft else None}


# ───────────────────────── 打分 ─────────────────────────
def _norm_m(s):
    return re.sub(r'[（(].*?[)）]|\s', '', str(s or ''))


def _sim(a, b):
    a, b = _norm_m(a), _norm_m(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    ga, gb = {a[i:i + 2] for i in range(len(a) - 1)}, {b[i:i + 2] for i in range(len(b) - 1)}
    return len(ga & gb) / max(1, len(ga | gb))


def score(c, key: dict, use_llm=False, datasources=None) -> dict:
    """原始现场标准答案 vs 草稿：按 协议 + 地址 对上点位，统计 设备 / 含义 / 用途 / 单位 的正确率。datasources：只看这些数据源（编码）的点。"""
    ans = {(p['protocol'], p['address']): p for p in key.get('points') or []}
    tp = TABLES['tag_point']
    ds = {d['id']: d for d in db.rows(c, 'datasource') if not datasources or d['code'] in datasources}
    ids = [r['id'] for r in c.execute(sa.select(tp.c.id, tp.c.datasource, tp.c.address)).mappings()
           if ((ds.get(r['datasource']) or {}).get('protocol'), r['address']) in ans]
    rows = propose(c, ids=ids, limit=5000, use_llm=use_llm) if ids else []
    addr = {r['id']: ((ds.get(r['datasource']) or {}).get('protocol'), r['address']) for r in c.execute(sa.select(tp.c.id, tp.c.datasource, tp.c.address)).mappings()}
    n = len(rows)
    ok = {'device': 0, 'meaning': 0, 'role': 0, 'unit': 0}
    wrong = []
    for r in rows:
        a = ans.get(addr[r['id']])
        if not a:
            continue
        hit = {'device': r['device'] == a['device'], 'meaning': _sim(r['meaning'], a['meaning']) >= 0.6, 'role': r['role'] == a['purpose'],
               'unit': (r['unit'] or '') == (a['unit'] or '')}
        for k, v in hit.items():
            ok[k] += 1 if v else 0
        if not all(hit.values()) and len(wrong) < 40:
            wrong.append({'code': r['code'], 'got': {k: r[k] for k in ('device', 'meaning', 'role', 'unit')},
                          'want': {'device': a['device'], 'meaning': a['meaning'], 'role': a['purpose'], 'unit': a['unit']}})
    return {'points': n, 'answer_points': len(ans), **{f'{k}_acc': round(v / n, 4) if n else 0 for k, v in ok.items()}, 'samples_wrong': wrong}
