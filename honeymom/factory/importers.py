"""把点表接到 M3 的通用导入框架（"实施 → 数据导入"，docs/dev/import.md）：导入器 tag_point「采集点（点表）」。

列和《接入规范》点表一致（另加一列"数据源"，空 = 按协议找第一个同协议的数据源）。校验用 pointtable.validate（和接入中心同一套规则），
写入用 model.point_row + upsert_point（时序键、模板补用途都一样）。整包导入（设备 + 平面图 + 三维）仍在接入中心。
"""
from __future__ import annotations

from ..i18n import _tr
from .. import db
from . import model, pointtable

LABELS = dict(zip(pointtable.PT_KEYS, pointtable.PT_COLS))
TYPES = {'scale': 'float', 'offset': 'float', 'period_ms': 'int', 'alarm_lo': 'float', 'alarm_hi': 'float'}


def register():
    try:
        from ..impl.importer import Col, Importer, register as reg
    except Exception:                     # 没有实施工具模块（M3）时不登记
        return

    def cols():
        out = [Col(k, LABELS[k], TYPES.get(k, 'str'), req=k in ('code', 'protocol'),
                   aliases=tuple(a for a in pointtable.SYN_PT.get(k, []) if a != LABELS[k])) for k in pointtable.PT_KEYS]
        out.append(Col('datasource', _tr('数据源'), 'str', aliases=(_tr('数据源编码'), 'datasource')))
        return out

    def _ds(ctx, v):
        cache = ctx.state.setdefault('ds', {})
        key = (v.get('datasource') or '', v.get('protocol') or '')
        if key not in cache:
            d = db.get(ctx.conn, 'datasource', code=key[0]) if key[0] else None
            if not d and key[1]:
                rows = db.rows(ctx.conn, 'datasource', protocol=key[1], limit=1)
                d = rows[0] if rows else None
            cache[key] = d
        return cache[key]

    def find(ctx, v):
        ds = _ds(ctx, v)
        if not ds or not v.get('code'):
            return None
        return db.get(ctx.conn, 'tag_point', code=str(v['code']), datasource=ds['id'])

    def check_all(ctx, rows):
        pts = []
        for r in rows:
            p = {k: r.values.get(k) for k in pointtable.PT_KEYS}
            if p.get('protocol'):
                p['protocol'] = pointtable.PROTO_ALIAS.get(str(p['protocol']).strip().lower(), str(p['protocol']).strip().lower())
                r.values['protocol'] = p['protocol']
            p['_row'] = r.n
            pts.append(p)
            if not _ds(ctx, r.values):
                r.err(_tr('没有{v}（先在“数据源”里建）', v=_tr('数据源 ') + r.values['datasource'] if r.values.get('datasource') else _tr('这个协议的数据源')), 'datasource')
        known = {e['code'] for e in db.rows(ctx.conn, 'equipment')}
        by_n = {r.n: r for r in rows}
        for i in pointtable.validate([], pts, known):
            r = by_n.get(i['row'])
            if r:
                (r.err if i['level'] == 'error' else r.warn)(i['msg'])

    def write(ctx, v, existing):
        ds = _ds(ctx, v)
        dev = db.get(ctx.conn, 'equipment', code=v['device']) if v.get('device') else None
        row = model.point_row({k: v.get(k) for k in pointtable.PT_KEYS}, dev, ds, 'import')
        _pid, st = model.upsert_point(ctx.conn, row)
        model.INDEX.dirty = True
        return {'new': 'create', 'updated': 'update'}.get(st, 'skip')

    reg(Importer(key='tag_point', label=_tr('采集点（点表）'), entity='tag_point', columns=cols, keys=('code',), group='设备', order=34,
                 depends=('equipment', 'datasource'), find=find, check_all=check_all, write=write,
                 perms=('system.tag_point.create', 'system.tag_point.update'),
                 doc=_tr('点表格式和《接入规范》一致（交付包里的 points.xlsx 的“点表”页可以直接导）。导入的点位是“待确认”，到“接入中心 → 逐点核对”确认。'),
                 rules=[_tr('点位编码唯一'), _tr('设备编码要在设备台账里'), _tr('地址按协议的格式（OPC UA NodeId / MQTT 主题 / Modbus 从站:表:地址 / Sparkplug 组/节点/设备:指标）'),
                        _tr('数值点要有单位'), _tr('整包（设备 + 平面图 + 三维）在“接入中心 → 导入交付包”')],
                 examples=[{'code': 'CNC1.Monitoring.Spindle.Load', 'device': 'CNC1', 'meaning': _tr('主轴负载'), 'unit': '%', 'dtype': 'float', 'rw': 'R',
                            'role': 'load', 'protocol': 'opcua', 'address': 'ns=2;s=Line1/CNC1/Monitoring/Spindle/Load', 'normal_range': '0~85'}]))
