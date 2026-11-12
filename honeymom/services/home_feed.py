"""工作台"提醒与 AI 值守"信息流：GET /api/home/feed。

规则提醒（本文件）和 AI 值守发现（其他模块通过 provider 注入）合成一条按严重程度、时间排序的信息流，前端工作台右侧一栏显示。

────────────────────────────── 对外接口（provider） ──────────────────────────────

    from honeymom.services.home_feed import register_feed_provider

    def my_findings(user: dict, now: datetime) -> list[dict]:
        return [{
            'id': 'watch:123',                 # 必填，全局唯一（建议 '<来源>:<编号>'），前端用它去重 / 记住已读
            'kind': 'ai',                      # 'alert'（规则提醒）| 'ai'（AI 值守发现）；provider 不填时按注册时的 kind
            'severity': 'warning',             # 'critical' | 'warning' | 'info'（排序：critical > warning > info，同级按 ts 新的在前）
            'category': 'equipment',           # 可选：equipment | quality | production | material | maintenance | tool | andon | ai
            'title': 'CNC3 主轴负载 30 分钟内上升 18%',
            'body': '可能是 T05 刀具磨损，建议提前换刀。',      # 可选，一两句话
            'evidence': [                      # 可选：依据（键值），前端显示为小标签；link 可点
                {'label': '主轴负载', 'value': '62% → 73%'},
                {'label': '设备', 'value': 'CNC3', 'link': '/e/equipment?id=12'},
            ],
            'actions': [                       # 可选：按钮。link = 打开页面；ask = 交给右侧 AI（AI 关闭时前端置灰）；
                {'label': '查看设备', 'link': '/e/equipment?id=12'},          # api = POST 这个接口（body 可选），完成后刷新
                {'label': '让 AI 分析', 'ask': '分析 CNC3 主轴负载上升的原因', 'feature': 'watch'},
                {'label': '确认', 'api': '/api/ai/watch/123/ack', 'body': {}},
            ],
            'ts': '2026-11-04 10:12:00',       # 发现/发生时间（字符串或 datetime）
            'link': '/e/equipment?id=12',      # 点卡片打开的页面
            'entity': 'equipment', 'entity_id': 12,     # 可选：对应 MES 记录，前端"交给 AI"时带上
            'ask': '诊断 CNC3 主轴负载上升的原因',        # 可选：卡片上"问 AI"按钮用的提问（不填时用标题）
            'data': {...},                     # 可选：给 AI 的结构化数据（前端放进 v-ai 的 data）
        }]

    register_feed_provider(my_findings, name='ai_watch', kind='ai', feature='watch')

- feature：provider 需要的 AI 功能（ai_switch 的 key）。功能关闭时这个 provider 根本不会被调用，AI 关闭时信息流里只有规则提醒。
- provider 抛异常只记日志、跳过，不影响其他提醒；每次请求都会调用，请自己做缓存（建议几秒）、别在里面调大模型。
- 同名再注册会替换（热重载安全）。unregister_feed_provider(name) 注销。
- 字段缺省会被补齐（kind / severity='info' / evidence=[] / actions=[] / ts=now），未知 severity 按 info。

GET /api/home/feed?limit=40 返回：
    {'items': [...], 'counts': {'critical': n, 'warning': n, 'info': n, 'ai': n, 'alert': n},
     'watch': bool（AI 值守是否开启）, 'providers': ['rules', ...], 'now': '...'}
"""
from __future__ import annotations

from ..i18n import _tr
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Callable

import sqlalchemy as sa
from fastapi import APIRouter, Depends

from .. import db
from ..auth import current_user
from ..crud import attach_labels
from ..db import clock
from ..schema import TABLES

log = logging.getLogger('mes.home_feed')
router = APIRouter()

SEVERITY = ('critical', 'warning', 'info')
_RANK = {s: i for i, s in enumerate(SEVERITY)}

# name -> {'fn', 'kind', 'feature', 'order'}
_PROVIDERS: dict[str, dict] = {}


def register_feed_provider(fn: Callable[[dict, datetime], list], *, name: str, kind: str = 'alert', feature: str | None = None, order: int = 100):
    """注册一个信息流来源。fn(user, now) -> list[dict]（字段见文件头）。feature：需要开启的 AI 功能，关闭时不调用。"""
    _PROVIDERS[name] = {'fn': fn, 'kind': kind, 'feature': feature, 'order': order}
    return fn


def unregister_feed_provider(name: str):
    _PROVIDERS.pop(name, None)


def _ts(v):
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    return str(v or '')[:19]


def _norm(it: dict, kind: str, now: datetime) -> dict | None:
    if not isinstance(it, dict) or not it.get('title'):
        return None
    out = dict(it)
    out['id'] = str(out.get('id') or f"{kind}:{hash(out['title'])}")
    out['kind'] = out.get('kind') or kind
    out['severity'] = out.get('severity') if out.get('severity') in _RANK else 'info'
    out['evidence'] = [e for e in (out.get('evidence') or []) if isinstance(e, dict)]
    out['actions'] = [a for a in (out.get('actions') or []) if isinstance(a, dict) and a.get('label')]
    out['ts'] = _ts(out.get('ts') or now)
    out.setdefault('body', '')
    out.setdefault('link', '')
    out.setdefault('category', 'ai' if out['kind'] == 'ai' else 'other')
    return out


def collect(user: dict, limit: int = 40) -> dict:
    from . import ai_switch
    now = clock.now()
    items, used = [], []
    for name, p in sorted(_PROVIDERS.items(), key=lambda kv: kv[1]['order']):
        if p['feature'] and not ai_switch.enabled(p['feature']):
            continue
        try:
            got = p['fn'](user, now) or []
        except Exception:                      # 一个来源出错不影响其他
            log.exception('home feed provider %s failed', name)
            continue
        used.append(name)
        for it in got:
            n = _norm(it, p['kind'], now)
            if n:
                items.append(n)
    seen, uniq = set(), []
    for it in items:
        if it['id'] not in seen:
            seen.add(it['id'])
            uniq.append(it)
    # 严重程度优先，同级时间新的在前
    uniq.sort(key=lambda x: x['ts'], reverse=True)
    uniq.sort(key=lambda x: _RANK[x['severity']])
    counts = defaultdict(int)
    for it in uniq:
        counts[it['severity']] += 1
        counts[it['kind']] += 1
    return {'items': uniq[:limit], 'counts': {k: counts.get(k, 0) for k in (*SEVERITY, 'alert', 'ai')}, 'total': len(uniq),
            'watch': ai_switch.enabled('watch'), 'providers': used, 'now': _ts(now)}


@router.get('/api/home/feed')
def home_feed(limit: int = 40, user=Depends(current_user)):
    return collect(user, max(1, min(200, limit)))


# ────────────────────────────── 规则提醒（不需要 AI） ──────────────────────────────
def _ago(now: datetime, t) -> str:
    if not t:
        return ''
    m = int((now - t).total_seconds() // 60)
    if m < 1:
        return _tr('刚刚')
    if m < 60:
        return _tr('{m} 分钟前', m=m)
    if m < 60 * 24:
        return _tr('{v} 小时 {v2} 分前', v=m // 60, v2=m % 60)
    return _tr('{v} 天前', v=m // 1440)


def _dur(now: datetime, t) -> str:
    if not t:
        return ''
    m = int((now - t).total_seconds() // 60)
    if m < 1:
        return _tr('不到 1 分钟')
    return _tr('{m} 分钟', m=m) if m < 60 else _tr('{v} 小时 {v2} 分', v=m // 60, v2=m % 60) if m < 1440 else _tr('{v} 天', v=m // 1440)


def rule_alerts(user: dict, now: datetime) -> list[dict]:
    out: list[dict] = []
    with db.engine.connect() as c:
        # 1) 未恢复的设备报警（每条一张卡）
        al = TABLES['alarm']
        rows_ = [dict(r) for r in c.execute(sa.select(al.c.id, al.c.equipment, al.c.msg, al.c.start, al.c.ack_by).where(al.c.end.is_(None))
                                            .order_by(al.c.start).limit(60)).mappings()]
        attach_labels(c, 'alarm', rows_)
        grouped: dict = {}                       # 同一台设备的同一条报警只出一张卡（取最早开始的那条，记次数）
        for a in rows_:
            g = grouped.setdefault((a['equipment'], a['msg']), {**a, 'n': 0})
            g['n'] += 1
        alarms = sorted(grouped.values(), key=lambda a: a['start'] or now, reverse=True)[:12]
        for a in alarms:
            long_ = a['start'] and (now - a['start']) > timedelta(minutes=10)
            out.append({'id': f"alarm:{a['id']}", 'category': 'equipment', 'severity': 'critical' if not a['ack_by'] else 'warning',
                        'title': _tr('{v} 报警：{msg}', v=a.get('equipment__label') or _tr('设备'), msg=a['msg']) + (_tr('（{n} 条）', n=a['n']) if a['n'] > 1 else ''),
                        'body': (_tr('已持续 ') + _dur(now, a['start'])) + (_tr('，尚未确认') if not a['ack_by'] else _tr('，{ack_by} 已确认', ack_by=a['ack_by'])),
                        'evidence': [{'label': _tr('设备'), 'value': a.get('equipment__label') or '', 'link': f"/e/equipment?id={a['equipment']}"},
                                     {'label': _tr('开始'), 'value': _ts(a['start'])[11:16]}, {'label': _tr('持续'), 'value': _dur(now, a['start'])}],
                        'actions': ([{'label': _tr('确认报警'), 'api': f"/api/e/alarm/{a['id']}/do/ack", 'body': {}}] if not a['ack_by'] else [])
                        + [{'label': _tr('报修'), 'link': f"/e/repair_order?equipment={a['equipment']}"}],
                        'ask': _tr('{get} 报警「{msg}」{v}，帮我排查原因并给出处置建议', get=a.get('equipment__label'), msg=a['msg'], v=_tr('已持续 ') + _dur(now, a['start']) if long_ else ''),
                        'ts': a['start'], 'link': f"/e/alarm?id={a['id']}", 'entity': 'alarm', 'entity_id': a['id'],
                        'data': {'设备': a.get('equipment__label'), '报警': a['msg'], '开始': _ts(a['start']), '确认人': a['ack_by']}})
        # 2) 安灯（待响应 / 处理中）
        an = TABLES['andon']
        andons = db.rows(c, 'andon', an.c.status != 'closed', order=an.c.id.desc(), limit=10)
        attach_labels(c, 'andon', andons)
        ATYPE = {'equipment': _tr('设备'), 'quality': _tr('质量'), 'material': _tr('物料'), 'safety': _tr('安全'), 'other': _tr('其他')}
        for a in andons:
            waiting = a['status'] == 'open'
            out.append({'id': f"andon:{a['id']}", 'category': 'andon', 'severity': 'critical' if waiting or a['atype'] == 'safety' else 'warning',
                        'title': _tr('{get}安灯 {code}：{description}', get=ATYPE.get(a['atype'], ''), code=a['code'], description=a['description']),
                        'body': (_tr('等待响应 {v}', v=_dur(now, a['raised_at'])) if waiting else _tr('{v} 处理中', v=a['responder'] or '')) + (f" · {a['station']}" if a['station'] else ''),
                        'evidence': [{'label': _tr('类型'), 'value': ATYPE.get(a['atype'], a['atype'])}, {'label': _tr('发起'), 'value': a['raised_by'] or ''},
                                     *([{'label': _tr('设备'), 'value': a.get('equipment__label'), 'link': f"/e/equipment?id={a['equipment']}"}] if a['equipment'] else [])],
                        'actions': [{'label': _tr('去处理'), 'link': '/andon'}], 'ts': a['raised_at'] or now, 'link': f"/e/andon?id={a['id']}",
                        'entity': 'andon', 'entity_id': a['id'], 'ask': _tr('安灯 {code}「{description}」怎么处理？', code=a['code'], description=a['description']),
                        'data': {'安灯': a['code'], '类型': ATYPE.get(a['atype']), '描述': a['description'], '状态': a['status'], '发起时间': _ts(a['raised_at'])}})
        # 3) 逾期 / 有逾期风险的工单
        wot, opt = TABLES['work_order'], TABLES['wo_op']
        wos = db.rows(c, 'work_order', wot.c.status.in_(('released', 'in_progress')), order=wot.c.due)
        ends = dict(c.execute(sa.select(opt.c.work_order, sa.func.max(opt.c.plan_end)).group_by(opt.c.work_order)).all())
        for w in wos:
            if not w['due'] or (w['qty_done'] or 0) >= w['qty']:
                continue
            eta = ends.get(w['id'])
            late = w['due'] < now
            risk = not late and eta and eta > w['due']
            if not (late or risk):
                continue
            left = w['qty'] - (w['qty_done'] or 0)
            out.append({'id': f"wo:{w['id']}", 'category': 'production', 'severity': 'critical' if late else 'warning',
                        'title': _tr('工单 {code} {v}', code=w['code'], v=_tr('已逾期') if late else _tr('有逾期风险')) + (_tr('（{customer}）', customer=w['customer']) if w['customer'] else ''),
                        'body': _tr('交期 {v}，还差 {left} 件', v=_ts(w['due'])[5:16], left=left) + (_tr('，预计完工 {v}', v=_ts(eta)[5:16]) if eta else ''),
                        'evidence': [{'label': _tr('完成'), 'value': f"{w['qty_done'] or 0}/{w['qty']}"}, {'label': _tr('交期'), 'value': _ts(w['due'])[5:16]},
                                     *([{'label': _tr('预计'), 'value': _ts(eta)[5:16]}] if eta else [])],
                        'actions': [{'label': _tr('看排产'), 'link': '/production/gantt'}], 'ts': w['due'] if late else now - timedelta(minutes=1),
                        'link': f"/e/work_order?id={w['id']}", 'entity': 'work_order', 'entity_id': w['id'],
                        'ask': _tr('工单 {code} {v}，还差 {left} 件，怎么挽回？', code=w['code'], v=_tr('已逾期') if late else _tr('可能逾期'), left=left),
                        'data': {'工单': w['code'], '客户': w['customer'], '计划': w['qty'], '完成': w['qty_done'], '交期': _ts(w['due']), '预计完工': _ts(eta)}})
        # 4) 未关闭的不合格品（汇总一张卡 + 超过 24 小时未处理的单列）
        from .quality import NCR_PENDING_LINK, ncr_pending, ncr_pending_count     # "待处理"唯一口径（和不合格品页同一个视图）
        nt = TABLES['ncr']
        ncrs = db.rows(c, 'ncr', ncr_pending(nt), order=nt.c.found_at.desc(), limit=200)
        n_pending = ncr_pending_count(c)
        if ncrs:
            attach_labels(c, 'ncr', ncrs[:20])
            by_def = defaultdict(int)
            for n in ncrs:
                by_def[n.get('defect__label') or _tr('未分类')] += 1
            old = [n for n in ncrs if n['found_at'] and now - n['found_at'] > timedelta(hours=24)]
            top = sorted(by_def.items(), key=lambda kv: -kv[1])[:3]
            out.append({'id': 'ncr:open', 'category': 'quality', 'severity': 'warning' if len(ncrs) >= 3 or old else 'info',
                        'title': _tr('待处理不合格品 {n_pending} 条', n_pending=n_pending) + (_tr('，{len} 条超过 24 小时', len=len(old)) if old else ''),
                        'body': _tr('主要缺陷：') + _tr('、').join(f'{k} {v}' for k, v in top),
                        'evidence': [{'label': n['code'], 'value': (n.get('defect__label') or '') + (f" · {n['station']}" if n['station'] else ''),
                                      'link': f"/e/ncr?id={n['id']}"} for n in ncrs[:3]],
                        'actions': [{'label': _tr('去处置'), 'link': NCR_PENDING_LINK}], 'ts': ncrs[0]['found_at'] or now, 'link': NCR_PENDING_LINK,
                        'ask': _tr('现在有 {n_pending} 条待处理的不合格品，主要是 {join}，帮我分析原因和处置优先级', n_pending=n_pending, join=_tr('、').join(k for k, _ in top)),
                        'data': {'待处理': n_pending, '超过24小时': len(old), '缺陷分布': dict(top)}})
        # 5) 到期 / 逾期点检保养
        mt = TABLES['maint_plan']
        due = db.rows(c, 'maint_plan', mt.c.next_due <= now.date(), order=mt.c.next_due, limit=50)
        if due:
            attach_labels(c, 'maint_plan', due)
            over = [m for m in due if m['next_due'] < now.date()]
            worst = max(((now.date() - m['next_due']).days for m in due), default=0)
            out.append({'id': 'maint:due', 'category': 'maintenance', 'severity': 'warning' if over else 'info',
                        'title': _tr('点检保养到期 {len} 项', len=len(due)) + (_tr('，逾期 {len} 项（最长 {worst} 天）', len=len(over), worst=worst) if over else ''),
                        'body': _tr('、').join(f"{m.get('equipment__label') or ''} {m['name']}" for m in due[:3]) + (_tr(' 等') if len(due) > 3 else ''),
                        'evidence': [{'label': m.get('equipment__label') or m['code'], 'value': m['name'] + (_tr(' · 逾期 {days} 天', days=(now.date() - m['next_due']).days) if m['next_due'] < now.date() else _tr(' · 今天'))}
                                     for m in due[:3]],
                        'actions': [{'label': _tr('去执行'), 'link': '/equipment/maint'}], 'ts': now - timedelta(minutes=2), 'link': '/equipment/maint',
                        'ask': _tr('有 {len} 项点检保养到期（逾期 {len2} 项），按风险排一下今天的执行顺序', len=len(due), len2=len(over)),
                        'data': {'到期': len(due), '逾期': len(over), '最长逾期天数': worst}})
        # 6) 刀具预警 / 待换刀
        tl = TABLES['tool']
        tools = db.rows(c, 'tool', tl.c.status.in_(('warn', 'change')), order=tl.c.status, limit=20)
        if tools:
            attach_labels(c, 'tool', tools)
            chg = [t for t in tools if t['status'] == 'change']
            out.append({'id': 'tool:warn', 'category': 'tool', 'severity': 'warning' if chg else 'info',
                        'title': _tr('刀具{v}{v2}{v3}', v=_tr('待换刀 ') + str(len(chg)) + _tr(' 把') if chg else '', v2=_tr('，') if chg and len(tools) > len(chg) else '', v3=_tr('预警 ') + str(len(tools) - len(chg)) + _tr(' 把') if len(tools) > len(chg) else ''),
                        'body': _tr('、').join(f"{t.get('equipment__label') or ''} {t['code']}" for t in tools[:3]),
                        'evidence': [{'label': f"{t.get('equipment__label') or ''} {t['code']}", 'value': _tr('{v}/{v2} 件', v=t['used'] or 0, v2=t['life_limit'] or '—') + (_tr(' · 磨损 {wear_pct:.0f}%', wear_pct=t['wear_pct']) if t['wear_pct'] else '')}
                                     for t in tools[:3]],
                        'actions': [{'label': _tr('刀具监测'), 'link': '/machining/tools'}], 'ts': now - timedelta(minutes=3), 'link': '/machining/tools',
                        'ask': _tr('哪些刀具需要马上换？按风险排序并说明依据'),
                        'data': {'待换刀': len(chg), '预警': len(tools) - len(chg), '刀具': [f"{t.get('equipment__label')} {t['code']}" for t in tools[:6]]}})
        # 7) 缺料：库存低于安全库存 / 物料申请超过 30 分钟未发
        mat, st = TABLES['material'], TABLES['stock']
        short = c.execute(sa.select(mat.c.id, mat.c.code, mat.c.name, mat.c.unit, mat.c.safety_stock, sa.func.coalesce(sa.func.sum(st.c.qty), 0))
                          .select_from(mat.outerjoin(st, st.c.material == mat.c.id)).where(mat.c.safety_stock > 0)
                          .group_by(mat.c.id, mat.c.code, mat.c.name, mat.c.unit, mat.c.safety_stock)).all()
        short = [r for r in short if (r[5] or 0) < (r[4] or 0)]
        if short:
            out.append({'id': 'material:short', 'category': 'material', 'severity': 'critical' if any((r[5] or 0) <= 0 for r in short) else 'warning',
                        'title': _tr('缺料预警 {len} 种物料低于安全库存', len=len(short)),
                        'body': _tr('、').join(r[2] for r in short[:3]) + (_tr(' 等') if len(short) > 3 else ''),
                        'evidence': [{'label': r[2], 'value': f"{r[5]:g}/{r[4]:g} {r[3] or ''}"} for r in short[:3]],
                        'actions': [{'label': _tr('看库存'), 'link': '/warehouse'}], 'ts': now - timedelta(minutes=4), 'link': '/warehouse',
                        'ask': _tr('哪些物料低于安全库存？会影响哪些工单，建议怎么补？'),
                        'data': {r[2]: {'库存': r[5], '安全库存': r[4]} for r in short[:8]}})
        mr = TABLES['material_request']
        reqs = db.rows(c, 'material_request', mr.c.status == 'submitted', order=mr.c.id, limit=20)
        stale = [r for r in reqs if r['requested_at'] and now - r['requested_at'] > timedelta(minutes=30)]
        if stale:
            attach_labels(c, 'material_request', stale)
            out.append({'id': 'material:req', 'category': 'material', 'severity': 'warning',
                        'title': _tr('物料申请 {len} 张超过 30 分钟未发料', len=len(stale)),
                        'body': _tr('、').join(f"{r['code']} {r.get('material__label') or ''}" for r in stale[:3]),
                        'evidence': [{'label': r['code'], 'value': f"{r.get('material__label') or ''} × {r['qty']:g} · {_ago(now, r['requested_at'])}", 'link': f"/e/material_request?id={r['id']}"}
                                     for r in stale[:3]],
                        'actions': [{'label': _tr('去发料'), 'link': '/e/material_request'}], 'ts': stale[0]['requested_at'], 'link': '/e/material_request'})
        # 8) 紧急维修未完成
        rp = TABLES['repair_order']
        reps = db.rows(c, 'repair_order', rp.c.status.in_(('reported', 'assigned', 'repairing')), order=rp.c.id.desc(), limit=10)
        if reps:
            attach_labels(c, 'repair_order', reps)
            urgent = [r for r in reps if r['level'] == 'urgent']
            out.append({'id': 'repair:open', 'category': 'maintenance', 'severity': 'critical' if urgent else 'info',
                        'title': _tr('未完成维修 {len} 单', len=len(reps)) + (_tr('，紧急 {len} 单', len=len(urgent)) if urgent else ''),
                        'body': _tr('、').join(f"{r.get('equipment__label') or ''} {r['fault']}" for r in reps[:2]),
                        'evidence': [{'label': r['code'], 'value': f"{r.get('equipment__label') or ''} · {r['fault']}", 'link': f"/e/repair_order?id={r['id']}"} for r in reps[:3]],
                        'actions': [{'label': _tr('维修工单'), 'link': '/e/repair_order'}], 'ts': reps[0]['reported_at'] or now, 'link': '/e/repair_order'})
    return out


register_feed_provider(rule_alerts, name='rules', kind='alert', order=0)
