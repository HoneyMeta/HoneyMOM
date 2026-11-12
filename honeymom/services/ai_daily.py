"""生产日报（杂志式）：头条摘要、KPI（带环比）、图表（产出趋势、各设备 OEE、缺陷帕累托、停机 Top、能耗）、
亮点与风险、建议动作；可导出 PDF；可定时自动生成。

- AI 开着（"AI 日报"）：亮点 / 风险 / 建议 / 头条由大模型根据数据写（数字核对过；演示模式回放录制）。
- AI 关着：同一份报告照常生成，亮点 / 风险 / 建议按规则写（不调用任何 AI 接口）。

接口：
  GET  /api/report/daily?day=YYYY-MM-DD        读报告（没生成过就现算一份规则版，不保存）
  GET  /api/report/daily/days                   已生成的日期列表
  POST /api/report/daily/generate {day}          生成并保存（规则版；AI 不参与）
  POST /api/ai/daily {day}                       生成并保存（含 AI 解读；需要"AI 日报"）— 见 services/ai.py
  GET  /api/report/daily/pdf?day=                导出 PDF（服务端用无头 Chrome / Edge 打印；没有浏览器时 501，前端改用浏览器打印）
  GET/PUT /api/report/daily/schedule {enabled, time: "08:00", target: "yesterday"|"today"}   定时自动生成（按数据时钟）
"""
from ..env import get as env_get
from ..i18n import _tr
import html
import json
import os
import shutil
import subprocess
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import Response

from .. import db
from ..auth import current_user
from ..db import clock
from ..schema import TABLES
from . import ai_demo

router = APIRouter()


# ---------------- 数据 ----------------
def _hourly(c, s, e):
    pe = TABLES['part_event']
    h = defaultdict(lambda: defaultdict(int))
    for t, ev in c.execute(sa.select(pe.c.t, pe.c.event).where((pe.c.t >= s) & (pe.c.t < e) & pe.c.event.in_(('poured', 'received', 'machined', 'stored', 'scrap')))).all():
        h[t.hour]['cast' if ev in ('poured', 'received') else ev] += 1
    hours = [hh for hh in range(24) if s + timedelta(hours=hh) <= min(e, clock.now())]
    return {'x': [f'{hh:02d}:00' for hh in hours],
            'series': [{'name': _tr('浇注/来料'), 'data': [h[hh]['cast'] for hh in hours]}, {'name': _tr('机加'), 'data': [h[hh]['machined'] for hh in hours]},
                       {'name': _tr('入库'), 'data': [h[hh]['stored'] for hh in hours]}, {'name': _tr('报废'), 'data': [h[hh]['scrap'] for hh in hours]}]}


def _downtime(c, s, e):
    from .equipment import state_durations
    eqt = TABLES['equipment']
    rows = []
    for eq in db.rows(c, 'equipment', eqt.c.etype.notin_(('crew', 'agv', 'other'))):     # other：缓冲区、网关、交换机、PLC、MES 等，不是生产设备
        d = state_durations(c, eq['id'], s, min(e, clock.now()))
        stop, setup, idle = d.get('Stopped', 0) / 60, d.get('Setup', 0) / 60, d.get('Idle', 0) / 60
        if stop + setup + idle > 0:
            rows.append({'code': eq['code'], 'name': eq['name'], 'stop': round(stop, 1), 'setup': round(setup, 1), 'idle': round(idle, 1), 'total': round(stop + setup + idle, 1)})
    rows.sort(key=lambda r: -r['total'])
    return rows[:8]


def _energy(c, s, e):
    et = TABLES['energy_hour']
    h = defaultdict(float)
    for t, k in c.execute(sa.select(et.c.hour, et.c.kwh).where((et.c.hour >= s) & (et.c.hour < e))).all():
        h[t.hour] += k or 0
    hours = sorted(h)
    return {'x': [f'{hh:02d}:00' for hh in hours], 'kwh': [round(h[hh], 1) for hh in hours]}


def _avg_oee(k):
    o = [x['OEE'] for x in k['equipment']['oee'] if x['OEE'] is not None]
    return round(sum(o) / len(o), 4) if o else None


def kpis_of(k, p):
    def item(key, label, v, pv, fmt, unit='', good='up'):
        d = None if v is None or pv is None else round(v - pv, 4)
        return {'key': key, 'label': label, 'value': v, 'prev': pv, 'delta': d, 'deltaPct': fmt == 'pct', 'format': fmt, 'unit': unit, 'good': good}
    P, Q, E, N = k['production'], k['quality'], k['equipment'], k['energy']
    pP, pQ, pE, pN = (p['production'], p['quality'], p['equipment'], p['energy']) if p else ({}, {}, {}, {})
    return [item('castings', _tr('浇注 / 来料'), P['castings'], pP.get('castings'), 'int', _tr('件')),
            item('machined', _tr('机加完成'), P['machined'], pP.get('machined'), 'int', _tr('件')),
            item('stored', _tr('成品入库'), P['stored'], pP.get('stored'), 'int', _tr('件')),
            item('scrap_rate', _tr('废品率'), Q['scrap_rate'], pQ.get('scrap_rate'), 'pct', '', 'down'),
            item('oee', _tr('平均 OEE'), _avg_oee(k), _avg_oee(p) if p else None, 'pct'),
            item('alarms', _tr('报警'), E['alarms'], pE.get('alarms'), 'int', _tr('次'), 'down'),
            item('kwh', _tr('全厂用电'), N['kwh'], pN.get('kwh'), 'int', 'kWh', 'down'),
            item('kwh_per_t', _tr('熔炼吨耗'), N['kwh_per_t'], pN.get('kwh_per_t'), 'num', 'kWh/t', 'down')]


def build(day=None):
    """规则版完整报告（不含 AI）。"""
    from .ai import day_kpi
    k = day_kpi(day)
    d0 = datetime.strptime(k['day'], '%Y-%m-%d')
    prev = None
    try:
        prev = day_kpi((d0 - timedelta(days=1)).strftime('%Y-%m-%d'))
        if not (prev['production']['castings'] or prev['production']['machined']):
            prev = None
    except Exception:
        prev = None
    s, e = d0, d0 + timedelta(days=1)
    with db.engine.connect() as c:
        hourly, downtime, energy = _hourly(c, s, e), _downtime(c, s, e), _energy(c, s, e)
    par = k['quality']['pareto'][:8]
    tot = sum(x['n'] for x in par) or 1
    acc, pareto = 0, []
    for x in par:
        acc += x['n']
        pareto.append({'name': x['name'], 'n': x['n'], 'cum': round(acc / tot, 4)})
    oee = sorted([x for x in k['equipment']['oee'] if x['OEE'] is not None], key=lambda x: x['OEE'])
    rep = {'day': k['day'], 'kpis': kpis_of(k, prev), 'has_prev': prev is not None,
           'charts': {'output': hourly, 'oee': [{'code': x['code'], 'name': x['name'], 'OEE': x['OEE'], 'A': x['A'], 'P': x['P'], 'Q': x['Q']} for x in oee],
                      'pareto': pareto, 'downtime': downtime, 'energy': energy},
           'facts': {'wo_open': k['production']['wo_open'], 'wo_late': k['production']['wo_late'], 'ncr_open': k['quality']['ncr_open'], 'rework': k['quality']['rework'],
                     'top_alarms': k['equipment']['top_alarms'][:5], 'heats': k['energy']['heats'], 'melt_t': k['energy']['melt_t'], 'ce_avg': k['energy']['ce_avg'],
                     'scrap': k['quality']['scrap']}}
    rep.update(rule_narrative(rep))
    rep['by'] = 'rule'
    try:                                   # 5.0 M6：这天定时运行、去向含"日报"的技能结果（services/skills.py）
        from .skills import daily_sections
        rep['skills'] = daily_sections(k['day'])
    except Exception:
        rep['skills'] = []
    return rep, k


def _pct(v):
    return '—' if v is None else f'{v * 100:.1f}%'


def rule_narrative(r):
    K = {x['key']: x for x in r['kpis']}
    F, C = r['facts'], r['charts']
    head = _tr('浇注 {value} 件、机加 {value2} 件、入库 {value3} 件；废品率 {v}，平均 OEE {v2}', value=K['castings']['value'], value2=K['machined']['value'], value3=K['stored']['value'], v=_pct(K['scrap_rate']['value']), v2=_pct(K['oee']['value']))
    hi, risks, acts = [], [], []
    if K['machined']['delta'] and K['machined']['delta'] > 0:
        hi.append({'text': _tr('机加完成比前一天多 {delta:.0f} 件。', delta=K['machined']['delta']), 'tone': 'ok'})
    if K['scrap_rate']['delta'] is not None and K['scrap_rate']['delta'] < 0:
        hi.append({'text': _tr('废品率比前一天下降 {v:.2f} 个点。', v=abs(K['scrap_rate']['delta']) * 100), 'tone': 'ok'})
    if C['oee']:
        best = C['oee'][-1]
        hi.append({'text': _tr('OEE 最高的是 {code} {name}：{v}。', code=best['code'], name=best['name'], v=_pct(best['OEE'])), 'tone': 'ok'})
    if K['kwh_per_t']['value'] and K['kwh_per_t']['delta'] is not None and K['kwh_per_t']['delta'] < 0:
        hi.append({'text': _tr('熔炼吨耗降到 {value:g} kWh/t。', value=K['kwh_per_t']['value']), 'tone': 'ok'})
    if not hi:
        hi.append({'text': _tr('全天入库 {value} 件，未关闭不合格品 {ncr_open} 条。', value=K['stored']['value'], ncr_open=F['ncr_open']), 'tone': 'info'})
    low = [x for x in C['oee'] if x['OEE'] < 0.6][:2]
    for x in low:
        loss = min(('A', 'P', 'Q'), key=lambda kk: x[kk])
        risks.append({'text': _tr('{code} OEE 只有 {v}，主要损失在{v2}（{v3}）。', code=x['code'], v=_pct(x['OEE']), v2={'A': _tr('可用率'), 'P': _tr('性能率'), 'Q': _tr('质量率')}[loss], v3=_pct(x[loss])), 'severity': 'warning'})
    if C['pareto']:
        top = C['pareto'][0]
        risks.append({'text': _tr('主要缺陷「{name}」{n} 件，占不良 {v}。', name=top['name'], n=top['n'], v=_pct(top['n'] / max(1, sum(p['n'] for p in C['pareto'])))), 'severity': 'warning'})
        acts.append({'text': _tr('针对「{name}」做缺陷回溯，必要时起草 8D。', name=top['name']), 'link': '/cross/backtrace'})
    if F['wo_late']:
        risks.append({'text': _tr('已逾期工单：{join}。', join=_tr('、').join(F['wo_late'][:4])), 'severity': 'critical'})
        acts.append({'text': _tr('提高 {v} 的优先级并重新排产。', v=F['wo_late'][0]), 'op': 'schedule_change',
                     'payload': {'changes': [{'work_order': F['wo_late'][0], 'priority': 1}], 'reason': _tr('日报：工单已逾期')}})
    if F['top_alarms']:
        a = F['top_alarms'][0]
        risks.append({'text': _tr('{equipment} 报警「{msg}」{n} 次。', equipment=a['equipment'], msg=a['msg'], n=a['n']), 'severity': 'warning'})
        if '操作工' in a['msg'] or '上料' in a['msg']:
            acts.append({'text': _tr('核对 {equipment} 的上料岗位和班组人数。', equipment=a['equipment'])})
        else:
            acts.append({'text': _tr('安排设备员排查 {equipment}「{msg}」。', equipment=a['equipment'], msg=a['msg'])})
    if C['downtime']:
        d = C['downtime'][0]
        acts.append({'text': _tr('{code} 非运行时间最长（{total:.0f} 分钟），复盘停机 / 待机原因。', code=d['code'], total=d['total']), 'link': '/equipment/oee'})
    return {'headline': head, 'summary': _tr('在制工单 {wo_open} 个，未关闭不合格品 {ncr_open} 条，报警 {value} 次。', wo_open=F['wo_open'], ncr_open=F['ncr_open'], value=K['alarms']['value']),
            'highlights': hi[:4], 'risks': risks[:4], 'actions': acts[:4]}


# ---------------- AI 解读 ----------------
def ai_narrative(rep):
    """大模型写头条 / 亮点 / 风险 / 建议（JSON），数字核对：对不上的条目丢掉。返回 dict 或 None。"""
    from .ai import llm_available, llm_chat
    from .ai_blocks import grounded_strict
    from .assistant import _nums

    def live():
        if not llm_available():
            return None
        data = {k: rep[k] for k in ('day', 'kpis', 'facts')}
        data['charts'] = {'oee': rep['charts']['oee'], 'pareto': rep['charts']['pareto'], 'downtime': rep['charts']['downtime']}
        m, _ = llm_chat([{'role': 'system', 'content': _tr('你是铸造 + 机加工厂的生产经理助理，写当天生产日报的"解读"部分。只基于给出的数据，数字照抄不要估算。输出 JSON：{"headline":"一句话头条（20~40 字，最重要的结论）","summary":"两三句概述","highlights":[{"text":"亮点"}],"risks":[{"text":"风险","severity":"critical|warning"}],"actions":[{"text":"建议动作（具体到设备/工单/缺陷）"}]}，亮点 2~3 条、风险 2~4 条、建议 2~4 条。比率（scrap_rate、OEE 等 0~1 的小数）写成百分数，如 0.053 写 5.3%；件数照抄。')},
                         {'role': 'user', 'content': json.dumps(data, ensure_ascii=False, default=str)}], json_mode=True, temperature=0.3)
        txt = m.get('content') or ''
        try:
            return json.loads(txt[txt.find('{'):txt.rfind('}') + 1])
        except ValueError:
            return None
    r = ai_demo.run('daily', 'narrative', {}, live=live, fallback=lambda: None, recordable=lambda r: bool(r), mark=lambda r: {**r, '_demo': True})
    if not isinstance(r, dict) or not r.get('headline'):
        return None
    corpus = json.dumps({k: rep[k] for k in ('kpis', 'facts', 'charts')}, ensure_ascii=False, default=str)
    have = _nums(corpus)
    demo = bool(r.get('_demo'))

    def grounded(t):
        return all(grounded_strict(n, have) for n in _nums(t))

    def ok(t):   # 数字要和这一天的数据对得上；演示回放的录制来自录制那天：原样展示，界面注明"数字是录制时的"
        return demo or grounded(t)
    rule = rule_narrative(rep)
    out = {'headline': str(r.get('headline'))[:120] if ok(str(r.get('headline'))) else rule['headline'],
           'summary': str(r.get('summary') or '')[:400] if ok(str(r.get('summary') or '')) else rule['summary']}
    kept = 0
    for key in ('highlights', 'risks', 'actions'):
        items = []
        for it in r.get(key) or []:
            t = it.get('text') if isinstance(it, dict) else str(it)
            if t and ok(t):
                items.append({'text': t[:300], **({'severity': it.get('severity')} if isinstance(it, dict) and it.get('severity') in ('critical', 'warning') else {}),
                              **({'tone': 'ok'} if key == 'highlights' else {})})
        out[key] = items or rule[key]
        kept += len(items)
    if kept == 0 and out['headline'] == rule['headline']:      # AI 写的内容一条都对不上这一天的数据
        return None
    out['by'] = 'demo' if demo else 'ai'
    if demo:
        out['demo_stale'] = not all(grounded(x) for x in [out['headline'], out['summary']] + [it['text'] for k in ('highlights', 'risks', 'actions') for it in out[k]])
    return out


# ---------------- 生成 / 存取 ----------------
def to_markdown(rep):
    K = {x['key']: x for x in rep['kpis']}
    lines = [_tr('## {day} 生产日报', day=rep['day']), '', f"**{rep['headline']}**", '', rep.get('summary') or '', '', _tr('### 指标')]
    for x in rep['kpis']:
        v = x['value']
        lines.append(_tr('- {label}：{v}{v2}', label=x['label'], v=_pct(v) if x['format'] == 'pct' else ('—' if v is None else f'{v:g}'), v2='' if x['format'] == 'pct' else ' ' + x['unit']))
    for key, title in (('highlights', _tr('亮点')), ('risks', _tr('风险')), ('actions', _tr('建议动作'))):
        if rep.get(key):
            lines += ['', f'### {title}' + (_tr('（AI）') if rep['by'] in ('ai', 'demo') else '')] + [f"- {it['text']}" for it in rep[key]]
    for sk in rep.get('skills') or []:
        lines += ['', _tr('### 技能：{name}', name=sk['name']), sk.get('narrative') or '', sk.get('summary') or '']
    _ = K
    return '\n'.join(lines)


def generate(day=None, use_ai=False):
    rep, k = build(day)
    if use_ai:
        try:
            nar = ai_narrative(rep)
        except Exception as ex:
            nar = None
            rep['ai_error'] = str(ex)[:200]
        if nar:
            rep['rule'] = {kk: rep[kk] for kk in ('headline', 'summary', 'highlights', 'risks', 'actions')}
            rep.update({kk: nar[kk] for kk in ('headline', 'summary', 'highlights', 'risks', 'actions') if nar.get(kk) or kk in ('highlights', 'risks', 'actions')})
            rep['demo_stale'] = bool(nar.get('demo_stale'))
            rep['by'] = nar['by']
            if not rep['highlights']:
                rep['highlights'] = rep['rule']['highlights']
    rep['generated_at'] = db.jsonable(clock.now())
    content = to_markdown(rep)
    dd = datetime.strptime(rep['day'], '%Y-%m-%d').date()
    with db.engine.begin() as c:
        r = db.get(c, 'daily_report', day=dd)
        row = {'day': dd, 'content': content, 'kpi': {**k, 'report': rep}, 'by': 'ai' if rep['by'] in ('ai', 'demo') else 'rule', 'generated_at': clock.now()}
        if r:
            db.update(c, 'daily_report', r['id'], row, raw=True)
        else:
            db.insert(c, 'daily_report', row, raw=True)
    return {'content': content, 'kpi': k, 'by': rep['by'], 'report': rep}


def stored(day):
    dd = datetime.strptime(day, '%Y-%m-%d').date()
    with db.engine.connect() as c:
        r = db.get(c, 'daily_report', day=dd)
    if r and isinstance(r.get('kpi'), dict) and isinstance(r['kpi'].get('report'), dict):
        return r['kpi']['report']
    return None


def default_day():
    """没指定日期：凌晨 6 点前看前一天（当天还没多少数据）。"""
    now = clock.now()
    return (now - timedelta(days=1) if now.hour < 6 else now).strftime('%Y-%m-%d')


def get_report(day=None):
    from . import ai_switch
    day = day or default_day()
    rep = stored(day)
    if rep is None:
        rep, _ = build(day)
        rep['generated_at'] = None
        rep['live'] = True
    elif rep.get('by') in ('ai', 'demo') and not ai_switch.enabled('daily'):
        # AI 关闭：不展示 AI 写的解读，回到规则版
        rule = rep.get('rule') or rule_narrative(rep)
        rep = {**rep, **rule, 'by': 'rule', 'ai_hidden': True}
    return localize(rep)


# KPI labels/units by key: a stored report keeps the language it was generated in; reading relabels it in the reader's language
KPI_TEXT = {'castings': ('浇注 / 来料', '件'), 'machined': ('机加完成', '件'), 'stored': ('成品入库', '件'), 'scrap_rate': ('废品率', ''),
            'oee': ('平均 OEE', ''), 'alarms': ('报警', '次'), 'kwh': ('全厂用电', 'kWh'), 'kwh_per_t': ('熔炼吨耗', 'kWh/t')}


def localize(rep):
    for x in rep.get('kpis') or []:
        t = KPI_TEXT.get(x.get('key'))
        if t:
            x['label'] = _tr(t[0])
            if t[1]:
                x['unit'] = _tr(t[1])
    if rep.get('by') == 'rule':              # rule-based text is regenerated from the numbers in the reader's language
        try:
            rep = {**rep, **rule_narrative(rep)}
        except Exception:
            pass
    return rep


# ---------------- PDF ----------------
def _browser():
    cand = [env_get('MOM_PDF_BROWSER') or '']
    for n in ('chrome', 'google-chrome', 'chromium', 'chromium-browser', 'msedge', 'microsoft-edge'):
        cand.append(shutil.which(n) or '')
    cand += [r'C:\Program Files\Google\Chrome\Application\chrome.exe', r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
             r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', r'C:\Program Files\Microsoft\Edge\Application\msedge.exe']
    return next((p for p in cand if p and os.path.isfile(p)), None)


C_PAL = ['#1463d6', '#0ba5c4', '#d9822b', '#6d5ae6', '#12a37f', '#d9466f', '#64748b', '#5b9bf0']


def _esc(s):
    return html.escape(str(s if s is not None else ''))


def svg_lines(x, series, w=520, h=190, pct=False, bars=False):
    if not x:
        return _tr('<div class="none">无数据</div>')
    pad_l, pad_b, pad_t = 36, 22, 10
    vals = [v for s in series for v in s['data'] if isinstance(v, (int, float))]
    vmax = max(vals + [1e-9]) * 1.1
    iw, ih = w - pad_l - 8, h - pad_b - pad_t
    n = len(x)
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg" font-family="Microsoft YaHei, sans-serif">']
    for i in range(5):
        y = pad_t + ih * i / 4
        out.append(f'<line x1="{pad_l}" x2="{w - 8}" y1="{y:.1f}" y2="{y:.1f}" stroke="#edf1f6"/>')
        lab = vmax * (4 - i) / 4
        out.append(f'<text x="{pad_l - 4}" y="{y + 3:.1f}" font-size="9" fill="#7b8aa0" text-anchor="end">{lab * 100:.0f}%</text>' if pct else
                   f'<text x="{pad_l - 4}" y="{y + 3:.1f}" font-size="9" fill="#7b8aa0" text-anchor="end">{lab:.0f}</text>')
    step = max(1, n // 8)
    for i, lab in enumerate(x):
        if i % step == 0:
            out.append(f'<text x="{pad_l + iw * (i + .5) / n:.1f}" y="{h - 6}" font-size="9" fill="#7b8aa0" text-anchor="middle">{_esc(lab)}</text>')
    if bars:
        k = len(series)
        bw = iw / n * 0.7 / k
        for si, s in enumerate(series):
            for i, v in enumerate(s['data']):
                if isinstance(v, (int, float)):
                    bh = ih * v / vmax
                    out.append(f'<rect x="{pad_l + iw * i / n + iw / n * 0.15 + si * bw:.1f}" y="{pad_t + ih - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="2" fill="{C_PAL[si % 8]}"/>')
    else:
        for si, s in enumerate(series):
            pts = [f'{pad_l + iw * (i + .5) / n:.1f},{pad_t + ih - ih * v / vmax:.1f}' for i, v in enumerate(s['data']) if isinstance(v, (int, float))]
            if pts:
                out.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{C_PAL[si % 8]}" stroke-width="2"/>')
    lx = pad_l
    for si, s in enumerate(series):
        out.append(f'<rect x="{lx}" y="0" width="9" height="5" rx="1" fill="{C_PAL[si % 8]}"/><text x="{lx + 12}" y="6" font-size="9" fill="#44536a">{_esc(s["name"])}</text>')
        lx += 18 + 11 * len(s['name'])
    out.append('</svg>')
    return ''.join(out)


def svg_hbar(labels, vals, w=520, pct=False, color='#1463d6', unit=''):
    if not labels:
        return _tr('<div class="none">无数据</div>')
    rh, h = 20, 20 * len(labels) + 6
    vmax = max([v for v in vals if isinstance(v, (int, float))] + [1e-9])
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" xmlns="http://www.w3.org/2000/svg" font-family="Microsoft YaHei, sans-serif">']
    for i, (lab, v) in enumerate(zip(labels, vals)):
        y = 3 + i * rh
        bw = (w - 200) * (v or 0) / vmax
        out.append(f'<text x="118" y="{y + 13}" font-size="10" fill="#44536a" text-anchor="end">{_esc(lab)[:14]}</text>'
                   f'<rect x="124" y="{y + 3}" width="{bw:.1f}" height="13" rx="2" fill="{color}"/>'
                   f'<text x="{128 + bw:.1f}" y="{y + 13}" font-size="10" fill="#0f1b2d">{(v * 100):.1f}%' if pct else
                   f'<text x="118" y="{y + 13}" font-size="10" fill="#44536a" text-anchor="end">{_esc(lab)[:14]}</text>'
                   f'<rect x="124" y="{y + 3}" width="{bw:.1f}" height="13" rx="2" fill="{color}"/>'
                   f'<text x="{128 + bw:.1f}" y="{y + 13}" font-size="10" fill="#0f1b2d">{v:g}{unit}')
        out.append('</text>')
    out.append('</svg>')
    return ''.join(out)


def report_html(rep):
    def kv(x):
        v = x['value']
        s = '—' if v is None else (f'{v * 100:.1f}%' if x['format'] == 'pct' else f'{v:,.0f}' if x['format'] == 'int' else f'{v:g}')
        d = x.get('delta')
        ds = ''
        if d is not None:
            good = (d > 0) == (x.get('good', 'up') == 'up') if d else None
            dtxt = (_tr('{v:+.2f} 点', v=d * 100) if x['format'] == 'pct' else f'{d:+,.0f}' if x['format'] == 'int' else f'{d:+.1f}')
            ds = f'<span class="d {"g" if good else "b" if good is False else ""}">{dtxt}</span>'
        return f'<div class="k"><div class="kl">{_esc(x["label"])}</div><div class="kv">{s}<small>{_esc(x["unit"] if x["format"] != "pct" else "")}</small></div>{ds}</div>'
    C = rep['charts']
    by = {'ai': _tr('AI 解读'), 'demo': _tr('AI 解读（演示模式）')}.get(rep.get('by'), '规则生成')
    li = lambda items: ''.join(f'<li>{_esc(it["text"])}</li>' for it in items) or '<li>—</li>'
    return _tr('<!doctype html><html><head><meta charset="utf-8"><title>{v} 生产日报</title><style>\n@page {{ size: A4; margin: 14mm 12mm; }}\nbody {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; color: #0f1b2d; font-size: 11.5px; margin: 0; }}\n.mast {{ display: flex; align-items: baseline; gap: 10px; border-bottom: 2px solid #0f1b2d; padding-bottom: 6px; }}\n.mast b {{ font-size: 20px; letter-spacing: .08em; }} .mast span {{ color: #7b8aa0; }} .mast .r {{ margin-left: auto; }}\nh1 {{ font-size: 19px; line-height: 1.4; margin: 12px 0 4px; }} .sum {{ color: #44536a; margin: 0 0 10px; }}\n.ks {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin: 8px 0 12px; }}\n.k {{ border: 1px solid #e2e8f0; border-radius: 6px; padding: 6px 8px; }} .kl {{ color: #7b8aa0; font-size: 10px; }}\n.kv {{ font-size: 18px; font-weight: 700; font-family: Bahnschrift, "DIN Alternate", sans-serif; }} .kv small {{ font-size: 10px; color: #7b8aa0; margin-left: 2px; }}\n.d {{ font-size: 10px; color: #7b8aa0; }} .d.g {{ color: #11713a; }} .d.b {{ color: #a11f1f; }}\n.g2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }} .box {{ border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px; break-inside: avoid; margin-bottom: 10px; }}\n.box h3 {{ font-size: 12px; margin: 0 0 4px; }} ul {{ margin: 2px 0; padding-left: 16px; }} li {{ margin: 2px 0; line-height: 1.55; }}\n.ok h3 {{ color: #11713a; }} .rk h3 {{ color: #a11f1f; }} .ac h3 {{ color: #0c479f; }} .none {{ color: #7b8aa0; padding: 10px; }}\n.foot {{ color: #7b8aa0; font-size: 9.5px; border-top: 1px solid #e2e8f0; padding-top: 4px; margin-top: 6px; }}\n</style></head><body>\n<div class="mast"><b>蜂元智能 MOM · 生产日报</b><span>{v}</span><span class="r">{by}</span></div>\n<h1>{v2}</h1><p class="sum">{v3}</p>\n<div class="ks">{join}</div>\n<div class="g2"><div class="box ok"><h3>亮点</h3><ul>{li}</ul></div><div class="box rk"><h3>风险</h3><ul>{li2}</ul></div></div>\n<div class="box"><h3>产出趋势（每小时）</h3>{svg_lines}</div>\n<div class="g2"><div class="box"><h3>各设备 OEE</h3>{svg_hbar}</div>\n<div class="box"><h3>缺陷帕累托</h3>{svg_hbar2}</div></div>\n<div class="g2"><div class="box"><h3>停机 / 待机 Top（分钟）</h3>{svg_hbar3}</div>\n<div class="box"><h3>能耗（kWh / 小时）</h3>{svg_lines2}</div></div>\n<div class="box ac"><h3>建议动作</h3><ul>{li3}</ul></div>\n<div class="foot">生成时间 {v4} · 数据来自蜂元智能 MOM · {by}</div>\n</body></html>', v=_esc(rep['day']), by=by, v2=_esc(rep.get('headline')), v3=_esc(rep.get('summary')), join=''.join(kv(x) for x in rep['kpis']), li=li(rep.get('highlights') or []), li2=li(rep.get('risks') or []), svg_lines=svg_lines(C['output']['x'], C['output']['series'][:3]), svg_hbar=svg_hbar([f"{x['code']}" for x in C['oee'][:10]], [x['OEE'] for x in C['oee'][:10]], w=360, pct=True), svg_hbar2=svg_hbar([x['name'] for x in C['pareto'][:8]], [x['n'] for x in C['pareto'][:8]], w=360, color='#d9822b', unit=' 件'), svg_hbar3=svg_hbar([x['code'] for x in C['downtime'][:8]], [x['total'] for x in C['downtime'][:8]], w=360, color='#dc2b2b', unit=''), svg_lines2=svg_lines(C['energy']['x'], [{'name': _tr('用电'), 'data': C['energy']['kwh']}], w=360, h=170, bars=True), li3=li(rep.get('actions') or []), v4=_esc(rep.get('generated_at') or ''))


def to_pdf(rep):
    exe = _browser()
    if not exe:
        return None
    d = tempfile.mkdtemp(prefix='hm_pdf_')
    try:
        src, dst = os.path.join(d, 'r.html'), os.path.join(d, 'r.pdf')
        with open(src, 'w', encoding='utf-8') as f:
            f.write(report_html(rep))
        cmd = [exe, '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run', '--no-pdf-header-footer', f'--user-data-dir={os.path.join(d, "u")}',
               f'--print-to-pdf={dst}', 'file:///' + src.replace('\\', '/')]
        try:
            subprocess.run(cmd, timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (subprocess.TimeoutExpired, OSError):
            return None
        if os.path.isfile(dst) and os.path.getsize(dst) > 1000:
            with open(dst, 'rb') as f:
                return f.read()
        return None
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------- 定时 ----------------
SCHED_DEFAULT = {'enabled': False, 'time': '23:50', 'target': 'today'}


def schedule_get():
    with db.engine.connect() as c:
        raw = db.setting(c, 'daily_schedule')
    v = dict(SCHED_DEFAULT)
    try:
        v.update(json.loads(raw) if raw else {})
    except ValueError:
        pass
    return v


def schedule_tick():
    from . import ai_switch
    v = schedule_get()
    if not v.get('enabled'):
        return None
    now = clock.now()
    if now.strftime('%H:%M') < v.get('time', '23:50') or v.get('last_run') == now.strftime('%Y-%m-%d'):
        return None
    day = (now - timedelta(days=1)) if v.get('target') == 'yesterday' else now
    r = generate(day.strftime('%Y-%m-%d'), use_ai=ai_switch.enabled('daily'))
    v['last_run'] = now.strftime('%Y-%m-%d')
    with db.engine.begin() as c:
        db.set_setting(c, 'daily_schedule', json.dumps(v, ensure_ascii=False), _tr('日报定时生成（报表 → 生产日报）'))
    return r['by']


# ---------------- 接口 ----------------
@router.get('/api/report/daily')
def api_get(day: str = '', user=Depends(current_user)):
    return get_report(day or None)


@router.get('/api/report/daily/days')
def api_days(user=Depends(current_user)):
    with db.engine.connect() as c:
        rows = db.rows(c, 'daily_report', order=TABLES['daily_report'].c.day.desc(), limit=60)
    return [{'day': db.jsonable(r['day']), 'by': ((r.get('kpi') or {}).get('report') or {}).get('by') or r['by'], 'generated_at': db.jsonable(r['generated_at']),
             'headline': ((r.get('kpi') or {}).get('report') or {}).get('headline') or ''} for r in rows]


@router.post('/api/report/daily/generate')
def api_generate(body: dict = Body(default={}), user=Depends(current_user)):
    return generate(body.get('day') or default_day(), use_ai=False)


@router.get('/api/report/daily/pdf')
def api_pdf(day: str = '', user=Depends(current_user)):
    rep = get_report(day or None)
    if rep.get('generated_at') is None:
        rep['generated_at'] = db.jsonable(clock.now())
    pdf = to_pdf(rep)
    if not pdf:
        raise HTTPException(501, _tr('服务器上没有可用的浏览器（Chrome / Edge）来生成 PDF，请用浏览器的"打印 → 另存为 PDF"'))
    return Response(pdf, media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="daily-{rep["day"]}.pdf"'})


@router.get('/api/report/daily/html')
def api_html(day: str = '', user=Depends(current_user)):
    rep = get_report(day or None)
    return Response(report_html(rep), media_type='text/html; charset=utf-8')


@router.get('/api/report/daily/schedule')
def api_sched(user=Depends(current_user)):
    return schedule_get()


@router.put('/api/report/daily/schedule')
def api_sched_put(body: dict = Body(...), user=Depends(current_user)):
    from .. import perms
    perms.check(user, 'system.daily.schedule', _tr('只有管理员或厂长可以设置日报定时'))
    v = schedule_get()
    if 'enabled' in body:
        v['enabled'] = bool(body['enabled'])
    if isinstance(body.get('time'), str) and len(body['time']) == 5 and body['time'][2] == ':':
        v['time'] = body['time']
    if body.get('target') in ('today', 'yesterday'):
        v['target'] = body['target']
    with db.engine.begin() as c:
        db.set_setting(c, 'daily_schedule', json.dumps(v, ensure_ascii=False), _tr('日报定时生成（报表 → 生产日报）'))
    return v
