"""演示自动排产：只对演示库（系统参数 demo_seed=1）生效，客户库不动。

虚拟工厂一直在生产，演示库里的工单却是初始化时建的几张，计划量做完 / 交期过了以后，新零件找不到工单，
报工、成本、交付这些数据就停在过去。这里每隔一会儿（数据时钟）看一次：最近 24 小时在产的产品，
如果在制工单（已下达 / 生产中）剩余量不够，就按约 6 小时的产量新建一张工单并下达（优先级 8，排在手工建的工单后面）（工艺路线 → 工序 → 排产），
同时给要求首检的工序补一张已放行的演示首检（和 seed 的演示工单一样），现场不会被首检闸门拦住。
系统参数 demo_autopilot=0 可关闭。"""
from ..i18n import _tr
import asyncio
import logging
from datetime import timedelta

import sqlalchemy as sa

from .. import db
from ..db import TABLES, clock

log = logging.getLogger('mes.demo')
SYS = {'username': 'system:demo', 'name': '演示自动排产', 'roles': 'admin', 'role_list': ['admin']}
INTERVAL_S = 120


def enabled(c) -> bool:
    return db.setting(c, 'demo_seed') == '1' and str(db.setting(c, 'demo_autopilot') or '1') != '0'


def tick() -> list[str]:
    """检查一次；返回新建的工单号。"""
    from . import production
    from .. import seed_biz
    made = []
    with db.engine.begin() as c:
        if not enabled(c):
            return made
        now = clock.now()
        pt, wt = TABLES['part'], TABLES['work_order']
        produced = c.execute(sa.select(pt.c.product, sa.func.count()).where((pt.c.born_at >= now - timedelta(hours=24)) & pt.c.product.isnot(None))
                             .group_by(pt.c.product)).all()
        for prod, n24 in produced:
            remaining = 0
            for w in db.rows(c, 'work_order', wt.c.status.in_(('released', 'in_progress')), product=prod):
                born = c.execute(sa.select(sa.func.count()).select_from(pt).where(pt.c.work_order == w['id'])).scalar() or 0
                remaining += max(0, (w['qty'] or 0) - born)
            if remaining >= max(10, n24 * 0.1):
                continue
            if not db.get(c, 'route', product=prod, status='active'):
                continue
            # 约 6 小时的产量，限 100～1000：虚拟工厂常开加速，"24 小时"里出生的零件可能上万，一张单太大排程会排出去十几天
            qty = int(min(1000, max(100, round(n24 / 40) * 10)))
            code = db.next_code(c, 'work_order', 'WO{d}{n:03}')
            wid = db.insert(c, 'work_order', {'code': code, 'product': prod, 'qty': qty, 'qty_done': 0, 'qty_scrap': 0, 'status': 'draft', 'priority': 8,
                                              'start_plan': now, 'due': now + timedelta(days=2), 'remark': _tr('演示自动排产（演示库按虚拟工厂产量自动下达）')}, raw=True)
            production.release(c, db.get(c, 'work_order', wid), {}, SYS)
            seed_biz.approve_demo_fai(c, [wid])
            made.append(code)
    for code in made:
        log.info(_tr('演示自动排产：下达工单 %s'), code)
    return made


async def loop():
    await asyncio.sleep(30)
    while True:
        try:
            await asyncio.get_running_loop().run_in_executor(None, tick)
        except Exception:
            log.exception(_tr('演示自动排产'))
        await asyncio.sleep(INTERVAL_S)
