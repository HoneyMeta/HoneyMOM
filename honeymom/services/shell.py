"""界面外壳的状态：顶栏和底部状态条每几秒取一次（数据时钟、采集在线、设备在线、未恢复报警、未读通知、待审批、AI 状态）。"""
import sqlalchemy as sa
from fastapi import APIRouter, Depends

from .. import db
from ..auth import current_user
from ..db import clock
from ..schema import TABLES

router = APIRouter()
NOT_DEVICE = ('crew', 'agv')


@router.get('/api/shell/status')
def shell_status(user=Depends(current_user)):
    from ..collector import COLLECTOR
    from . import ai_switch
    from .ops import _mine
    eq, al, nt = TABLES['equipment'], TABLES['alarm'], TABLES['notice']
    with db.engine.connect() as c:
        sources = [{'id': d['id'], 'code': d['code'], 'name': d['name'], 'protocol': d['protocol'], 'enabled': bool(d['enabled']),
                    'status': COLLECTOR.status.get(d['id']) or d.get('status') or '', 'code': COLLECTOR.status.code(d['id'])} for d in db.rows(c, 'datasource')]
        by_status = {k or 'offline': n for k, n in c.execute(sa.select(eq.c.status, sa.func.count()).where(eq.c.etype.notin_(NOT_DEVICE)).group_by(eq.c.status)).all()}
        alarms = c.execute(sa.select(sa.func.count()).select_from(al).where(al.c.end.is_(None))).scalar() or 0
        unread = c.execute(sa.select(sa.func.count()).select_from(nt).where(_mine(user) & (nt.c.channel == 'inbox') & (nt.c.read.is_(False) | nt.c.read.is_(None)))).scalar() or 0
    approvals = None
    from ..impl import flows as _flows          # M3：审批流程不依赖 AI，有启用的流程就显示审批角标
    if ai_switch.enabled('agents') or _flows.any_enabled():
        from ..agent.api import approvals_count
        approvals = approvals_count(user=user)['pending']
    total = sum(by_status.values())
    on = [s for s in sources if s['enabled']]
    sw = ai_switch.status()
    return {'now': db.jsonable(clock.now()), 'data_clock': clock.data_now is not None,
            'sources': sources, 'collect': {'online': sum(1 for s in on if s.get('code') in ('connected', 'push')), 'total': len(on)},
            'equipment': {'total': total, 'online': total - by_status.get('offline', 0), 'by_status': by_status},
            'alarms_open': alarms, 'notices_unread': unread, 'approvals': approvals,
            'ai': {'mode': sw['mode'], 'model': sw['model'], 'llm_configured': sw['llm_configured'], 'effective': sw['effective'], 'global': sw['global']}}
