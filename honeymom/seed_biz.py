"""演示工单的首检放行（4.7 首检闸门的演示约定）。

5.0（M3）起，原来写在这里的业务演示数据（人员技能、第二个产品 SGH-520、作业指示和工艺参数、换型时间、模具 / 夹具、知识库关联、加工图纸、
设备停机计划、报工参数）都搬进了行业模板（honeymom/packs/：common / foundry / machining 和示范工厂演示数据 foundry/demo.json），
由 seed.base() → impl.packs.apply() 写入；导入设备后的部分由 impl.packs.after_equipment() 写入。这里只剩演示工单的首检约定。"""
from . import db
from .db import clock


def run(c):
    _demo_fai(c)


DEMO_FAI_BY = '演示数据'


def demo_orders(c) -> set:
    """演示工单（seed 建的那几张）的 id：下达时自动带一张已放行的 CNC 首检，演示不会一上来就被首检闸门拦住。"""
    v = db.setting(c, 'demo_fai_orders')
    try:
        return {int(x) for x in str(v).split(',') if x.strip()} if v else set()
    except ValueError:
        return set()


def approve_demo_fai(c, wo_ids=None):
    """给工单里"工艺步骤要求首检"的工序补一张已放行的首检单（已经有首检单的工序不动）。wo_ids=None：所有在制（已下达/生产中）工单。
    只用于演示数据：seed 的演示工单、以及升级时已经在跑的工单；之后新建的工单照常要首检。闸门逻辑（services/gates.py）不变。"""
    from . import i18n
    with i18n.demo_data():
        return _approve_demo_fai(c, wo_ids)


def _approve_demo_fai(c, wo_ids=None):
    from .services import gates
    wt = db.TABLES['work_order']
    wos = db.rows(c, 'work_order', wt.c.status.in_(('released', 'in_progress'))) if wo_ids is None else [w for w in (db.get(c, 'work_order', i) for i in wo_ids) if w]
    n = 0
    for wo in wos:
        for op in db.rows(c, 'wo_op', work_order=wo['id']):
            if not gates.fai_required(c, op) or db.rows(c, 'first_article', limit=1, wo_op=op['id']):
                continue
            now = clock.now()
            db.insert(c, 'first_article', {'work_order': wo['id'], 'wo_op': op['id'], 'process': op['process'], 'trigger': 'start', 'status': 'approved',
                                           'requested_by': DEMO_FAI_BY, 'requested_at': now, 'decided_by': DEMO_FAI_BY, 'decided_at': now}, raw=True)
            n += 1
    return n


def _demo_fai(c):
    # 新库：seed 刚建了演示工单（草稿），记下它们，下达时自动放行首检（production.release 调 approve_demo_fai）
    if db.setting(c, 'demo_fai_orders') is None:
        ids = [w['id'] for w in db.rows(c, 'work_order') if w['status'] == 'draft' and not w['released_at']] if not db.rows(c, 'report', limit=1) else []
        db.set_setting(c, 'demo_fai_orders', ','.join(map(str, ids)), '演示工单：下达时自动带已放行的首检（4.7 首检闸门）')
    # 老库升级（只做一次）：升级那一刻已经在跑的工单补上已放行的首检，现场演示不被拦；之后的新工单照常首检
    if db.setting(c, 'demo_fai_v47') is None:
        n = approve_demo_fai(c)
        db.set_setting(c, 'demo_fai_v47', n, '4.7 升级时给在制工单补的已放行首检数（只做一次）')


def after_import(c):
    """兼容：导入设备后的演示数据已由 seed.after_import → impl.packs.after_equipment() 写入。"""
    return None
