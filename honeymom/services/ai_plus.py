"""4.7 的 AI 体验：生成式回答（块协议 + 钉到工作台）、动作草稿与审批、一句话自动化、AI 值守、杂志式日报、跨工段 AI 解读。

app.py 里 ai_plus.install(app) 一行接入：注册各模块的接口、建表、把值守发现注册进工作台信息流、启动后台循环。
各模块说明见文件头：ai_blocks（块协议）、ai_drafts（动作草稿）、ai_automation（一句话自动化）、ai_watch（AI 值守）、
ai_daily（日报）、ai_cross（缺陷回溯 / 协同排产 AI 解读）。

钉到工作台：
  GET    /api/ai/pins            我钉住的块（带 query 的图表会按 SQL 重新取数）
  POST   /api/ai/pins {block, title?}
  DELETE /api/ai/pins/{id}
"""
from ..i18n import _tr
import asyncio
import logging
import time
from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db
from ..auth import current_user
from ..schema import metadata
from . import ai_automation, ai_blocks, ai_cross, ai_daily, ai_drafts, ai_watch

log = logging.getLogger('mes.ai_plus')
router = APIRouter()
from ..schema import ai_pin_t as pin_t  # noqa: E402  (表定义在 schema.py)


@router.get('/api/ai/pins')
def pins(user=Depends(current_user)):
    with db.engine.connect() as c:
        rows = [dict(r) for r in c.execute(sa.select(pin_t).where(pin_t.c.user == user['username']).order_by(pin_t.c.id.desc()).limit(24)).mappings()]
    out = []
    for r in rows:
        b = r['block'] or {}
        live = False
        if b.get('type') == 'chart' and isinstance(b.get('query'), dict):
            try:
                b = {**ai_blocks.chart_from_query(b['query'], b.get('kind') or 'bar', b.get('title') or '', b.get('unit') or ''), 'id': b.get('id')}
                live = True
            except Exception:
                pass
        out.append({'id': r['id'], 'title': r['title'], 'block': b, 'source': r['source'], 'created_at': db.jsonable(r['created_at']), 'live': live})
    return out


@router.post('/api/ai/pins')
def pin(body: dict = Body(...), user=Depends(current_user)):
    b = ai_blocks.normalize(body.get('block'))
    if not b or b['type'] in ('sources', 'draft'):
        raise HTTPException(400, _tr('这个块不能钉到工作台'))
    with db.engine.begin() as c:
        n = c.execute(sa.select(sa.func.count()).select_from(pin_t).where(pin_t.c.user == user['username'])).scalar()
        if n >= 24:
            raise HTTPException(400, _tr('最多钉 24 个，先取消一些'))
        pid = c.execute(pin_t.insert().values(user=user['username'], title=(body.get('title') or b.get('title') or _tr('AI 结果'))[:190], block=b,
                                              source=(body.get('source') or '')[:190], created_at=datetime.now())).inserted_primary_key[0]
    return {'id': pid, 'ok': True}


@router.delete('/api/ai/pins/{pid}')
def unpin(pid: int, user=Depends(current_user)):
    with db.engine.begin() as c:
        c.execute(pin_t.delete().where((pin_t.c.id == pid) & (pin_t.c.user == user['username'])))
    return {'ok': True}


# ---------------- 后台 ----------------
_state = {'auto': 0.0, 'watch': 0.0, 'daily': 0.0}


def _bg_once():
    from . import ai_switch
    now = time.time()
    if now - _state['auto'] >= 30:
        _state['auto'] = now
        try:
            ai_automation.tick()
        except Exception:
            log.exception('automation tick')
    if now - _state['watch'] >= 60 and ai_switch.enabled('watch'):
        _state['watch'] = now
        try:
            ai_watch.scan()
        except Exception:
            log.exception('watch scan')
    if now - _state['daily'] >= 30:
        _state['daily'] = now
        try:
            ai_daily.schedule_tick()
        except Exception:
            log.exception('daily schedule')


async def _loop():
    await asyncio.sleep(20)
    while True:
        try:
            await asyncio.to_thread(_bg_once)
        except Exception:
            log.exception('ai background')
        await asyncio.sleep(10)


def install(app):
    for r in (router, ai_drafts.router, ai_automation.router, ai_watch.router, ai_daily.router, ai_cross.router):
        app.include_router(r)
    ai_watch.register_feed()

    @app.on_event('startup')
    async def _ai_bg():
        asyncio.create_task(_loop())
