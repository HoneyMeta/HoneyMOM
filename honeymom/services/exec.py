"""5.0 现场执行与质量合规（M5）总入口：install(app) 挂接口、给已有动作加钩子（电子签名、自动打印、委外单、ECN 报工闸门）、
起后台（打印队列线程 + 每分钟：量具到期 / 锁定、受控文件到期生效）。说明见 docs/dev/exec.md。"""
from ..i18n import _tr
import asyncio
import logging

from fastapi import APIRouter

log = logging.getLogger('mes.exec')
router = APIRouter()


def _wrap_release():
    """工单下达后：委外工序建委外单草稿、按规则打工单流转卡。"""
    from ..crud import ACTIONS
    fn = ACTIONS.get(('work_order', 'release'))
    if not fn or getattr(fn, '_exec', False):
        return

    def wrapped(c, wo, body, user):
        res = fn(c, wo, body, user)
        from .exec_label import fire
        from .exec_outsource import on_wo_released
        ids = on_wo_released(c, wo, user)
        fire(c, 'wo_release', 'wo', wo['code'], user, material=wo['product'], customer=wo.get('customer'))
        if ids and isinstance(res, dict):
            res = {**res, 'msg': (res.get('msg') or '') + _tr('；委外工序已建委外单草稿 {len} 张', len=len(ids))}
        return res
    wrapped._exec = True
    ACTIONS[('work_order', 'release')] = wrapped


async def _loop():
    from . import exec_doc, iatf
    await asyncio.sleep(5)
    while True:
        for fn in (iatf.tick, exec_doc.tick):
            try:
                await asyncio.to_thread(fn)
            except Exception:      # noqa: BLE001
                log.exception(_tr('M5 后台任务出错'))
        await asyncio.sleep(60)


def install(app):
    from .. import ingest
    from ..crud import HOOKS
    from . import complaint, esign, exec_ai, exec_doc, exec_label, exec_outsource, exec_pack, gates, iatf
    esign.install(app, router)
    for r in (router, exec_label.router, exec_pack.router, exec_outsource.router, exec_doc.router, iatf.router, complaint.router, exec_ai.router):
        app.include_router(r)
    iatf.install_wrappers()          # 首件放行 / 批次解冻放行 → 电子签名
    _wrap_release()
    if exec_doc.report_gate not in gates.REPORT_GATES:
        gates.REPORT_GATES.append(exec_doc.report_gate)
    if exec_label.on_part_created not in ingest.PART_CREATED:
        ingest.PART_CREATED.append(exec_label.on_part_created)
    prev = HOOKS.get(('label_rule', 'after_save'))

    def _rules_changed(c, rid, user):
        exec_label._rules_cache['t'] = 0
        if prev:
            prev(c, rid, user)
    HOOKS[('label_rule', 'after_save')] = _rules_changed
    from . import exec_import
    exec_import.register_all()       # M3 导入框架：量检具 / 校准 / 备件 / 包装规则 / 标签模板 / 控制计划
    from . import views
    if exec_pack.pack_trace not in views.TRACE_EXT:
        views.TRACE_EXT.append(('packing', exec_pack.pack_trace))

    @app.on_event('startup')
    async def _start():
        try:
            exec_label.start_worker()
        except Exception:      # noqa: BLE001
            log.exception(_tr('打印队列启动失败'))
        asyncio.create_task(_loop())

