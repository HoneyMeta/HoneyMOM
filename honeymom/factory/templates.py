"""设备类型模板：每类设备的标准点位集合、用途映射（点位 → 运行状态 / 报警 / 产量 / 电能…）、默认 3D 模型和尺寸。

模板格式（device_template.points 的每一项，docs/dev/factory-model.md「模板格式」）：
  name      点位路径模式：'Common/State'；{xxx} 匹配一段并取出（如 'Tools/{tool}/LifeCount'）；* 匹配一段；** 匹配剩下所有段
  meaning   含义   unit 单位   dtype bool/int/float/string   rw R/RW
  role      用途（state alarm count power energy load temp quality action step param other）
  sem       语义钩子：output（产量计数）、good / bad（合格 / 不合格计数）、cycle（循环计数 / 循环进行中）、tool_life / tool_limit / tool_active（刀具）、
            sample（进趋势采样表 tag_sample，AI 监测用）
  key       true = 设备监控 / 首页卡片上显示的关键点位
  normal    正常范围 'lo~hi'；lo / hi 报警下限 / 上限；map 值映射（状态码 → 状态）
模板按 aliases 匹配点表"设备类型"列；同一点位名在设备没有点表信息时，也用模板补用途（采集按模板映射）。
内置模板在启动 / 迁移时写入（ensure）；用户可以新建模板（builtin=False），M3 的行业包也往这张表里加。
"""
import re

import sqlalchemy as sa

VERSION = 3          # 内置模板内容改了就加一：已有库里 builtin=True 且版本更低的模板会被更新


def P(name, meaning, unit=None, dtype='float', role='other', **kw):
    d = {'name': name, 'meaning': meaning, 'unit': unit, 'dtype': dtype, 'role': role}
    d.update({k: v for k, v in kw.items() if v is not None})
    return d


STATE_CODES = {'0': 'Idle', '1': 'Executing', '2': 'Starved', '3': 'Blocked', '4': 'Stopped', '5': 'Setup', '6': 'Planned'}
COMMON = [P('Common/State', '运行状态', None, 'string', 'state', key=True, map=None),
          P('Common/Alarm', '当前报警', None, 'string', 'alarm'),
          P('Common/PowerKW', '实时功率', 'kW', 'float', 'power'),
          P('Common/EnergyKWh', '累计电能', 'kWh', 'float', 'energy')]
ACTIONS = [P('Actions/CycleOn', '循环进行中（上升沿 = 循环启动）', None, 'bool', 'action', sem='cycle'),
           P('Actions/CycleCount', '循环计数', '次', 'int', 'count', sem='cycle'),
           P('Actions/ProgramStep', '程序段号', None, 'int', 'step'),
           P('Actions/Last/*', '上次动作用时', 's', 'float', 'action'),
           P('Actions/*', '动作信号（阀 / 到位）', None, 'bool', 'action')]


def T(code, name, aliases, category, model3d, w, d, h, points, process_type=None, station_group=None, ideal=None, maint=''):
    return {'code': code, 'name': name, 'aliases': aliases, 'category': category, 'model3d': model3d, 'w': w, 'd': d, 'h': h,
            'points': points, 'process_type': process_type, 'station_group': station_group, 'ideal_cycle_s': ideal, 'maint_items': maint}


BUILTIN = [
    T('cnc', '加工中心', '加工中心,CNC,数控机床,立式加工中心,卧式加工中心,数控车床,车削中心', '机加', 'cnc', 4, 3.4, 2.6, COMMON + [
        P('Monitoring/Spindle/Load', '主轴负载', '%', role='load', key=True, sample=True, normal='0~85', hi=90),
        P('Monitoring/Vibration/RMS', '主轴振动有效值', 'mm/s', sample=True, normal='0.3~2.8', hi=4.5),
        P('Monitoring/Spindle/ActualSpeed', '主轴转速', 'rpm'),
        P('Monitoring/Spindle/Override', '主轴倍率', '%', role='param', rw='RW'),
        P('Monitoring/Feed/Override', '进给倍率', '%', role='param', rw='RW'),
        P('Production/PartsProduced', '累计加工件数', '件', 'int', 'count', sem='output', key=True),
        P('Production/PartsGood', '合格件', '件', 'int', 'count', sem='good'),
        P('Production/PartsBad', '不合格件', '件', 'int', 'count', sem='bad'),
        P('Production/LastCycleTime', '上一件节拍', 's'),
        P('Production/ActiveProgram/Step', '当前工步', None, 'string', 'step'),
        P('Tools/ActiveTool', '当前刀号', None, 'string', sem='tool_active', key=True),
        P('Tools/{tool}/LifeCount', '刀具已用件数', '件', 'int', 'count', sem='tool_life'),
        P('Tools/{tool}/LifeLimit', '刀具寿命设定', '件', 'int', 'param', sem='tool_limit', rw='RW'),
        P('Quality/*', '在线测量值', 'mm', role='quality', key=True, sample=True)] + ACTIONS,
      process_type='machining', station_group='CNC', ideal=None, maint='导轨润滑油位；切削液浓度；主轴异响；排屑器；气压 0.5 MPa'),
    T('molding', '造型线', '造型线,造型机,水平分型造型线,垂直分型造型线,静压造型线', '铸造', 'molding', 26, 4, 3.4, COMMON + [
        P('Production/MoldCount', '累计造型数', '型', 'int', 'count', sem='output', key=True),
        P('Pouring/LastTemp', '上一型浇注温度', '°C', role='temp', key=True, normal='1395~1450', lo=1380, hi=1470),
        P('Production/WaitingMolds', '待浇注砂型数', '型', 'int', key=True)] + ACTIONS,
      process_type='molding', maint='液压站油温油位；模板清洁；脱模剂'),
    T('furnace', '中频炉', '中频炉,熔炼炉,感应炉,电炉,熔化炉,保温炉', '铸造', 'furnace', 4, 4, 2.2, COMMON + [
        P('Bath/Temperature', '铁水温度', '°C', role='temp', key=True, sample=True, normal='1420~1530', lo=1380, hi=1560),
        P('Bath/Weight', '炉内重量', 'kg', 'int', key=True),
        P('Electrical/Power', '有功功率', 'kW', role='power', key=True),
        P('Heat/Phase', '熔炼阶段', None, 'string', key=True), P('Heat/Number', '当前炉次号', None, 'string', key=True),
        P('Cooling/OutletTemp', '冷却出水温度', '°C', role='temp', sample=True),
        P('Lining/EarthLeakage', '炉衬漏电流', 'mA', sample=True)],
      process_type='melting', maint='冷却水流量/温度；炉衬漏电流；感应圈有无渗水；液压系统油位'),
    T('treatment', '球化孕育站', '球化孕育站,球化站,喂丝站', '铸造', 'treatment', 4, 3, 2.4, COMMON + [
        P('Treatment/Count', '处理包数', '包', 'int', 'count', sem='output', key=True),
        P('Ladle/Temperature', '浇包温度', '°C', role='temp', key=True),
        P('Ladle/ResidualMg', '残余镁', '%', role='quality', key=True, sample=True, normal='0.035~0.050', lo=0.030)],
      process_type='treatment', maint='浇包包衬；吊具'),
    T('sand', '砂处理系统', '砂处理系统,砂处理,混砂机,砂处理线', '铸造', 'sand', 10, 6, 3.5, COMMON + [
        P('Sand/Moisture', '型砂水分', '%', role='quality', key=True, sample=True, normal='3.4~3.8', lo=3.2, hi=4.0),
        P('Sand/Compactability', '紧实率', '%', role='quality', key=True, sample=True, normal='36~44', lo=32, hi=48)],
      process_type='molding', maint='混砂机刮板磨损；水分仪校准；皮带跑偏'),
    T('cooling', '冷却线', '冷却线,冷却输送线,落砂线', '铸造', 'conveyor', 32, 3, 1, COMMON + [
        P('Line/Molds', '线上砂型数', '型', 'int', key=True), P('Line/ShakeoutTemp', '落砂温度', '°C', role='temp', key=True)],
      process_type='cooling'),
    T('shotblast', '抛丸机', '抛丸机,吊钩抛丸机,通过式抛丸机,清理机', '铸造', 'shotblast', 6, 6, 3.2, COMMON + [
        P('Turbine/Current', '抛头电流', 'A', role='load', key=True),
        P('Batch/Count', '抛丸批次', '批', 'int', 'count', sem='output', key=True)],
      process_type='cleaning', maint='抛头叶片磨损；钢丸量；除尘器压差'),
    T('die_casting', '压铸机', '压铸机,压铸岛,冷室压铸机,热室压铸机', '压铸', 'die_casting', 8, 3, 3, COMMON + [
        P('Production/ShotCount', '压射计数', '模', 'int', 'count', sem='output', key=True),
        P('Injection/Pressure', '增压压力', 'MPa', key=True, sample=True), P('Injection/Speed', '高速速度', 'm/s'),
        P('Die/Temperature', '模温', '°C', role='temp', key=True, sample=True, normal='180~260'),
        P('Hydraulic/OilTemp', '液压油温', '°C', role='temp', normal='30~55', hi=60), P('Production/CycleTime', '循环时间', 's')] + ACTIONS,
      process_type='molding', maint='液压油位油温；模具冷却水；料筒；安全门联锁'),
    T('injection', '注塑机', '注塑机,注塑,立式注塑机', '注塑', 'injection', 6, 2, 2.2, COMMON + [
        P('Production/ShotCount', '开模计数', '模', 'int', 'count', sem='output', key=True),
        P('Barrel/Temperature/{zone}', '料筒温度', '°C', role='temp', sample=True), P('Mold/Temperature', '模温', '°C', role='temp', key=True),
        P('Injection/Pressure', '注射压力', 'MPa', key=True, sample=True), P('Production/CycleTime', '循环时间', 's')] + ACTIONS,
      process_type='molding', maint='料筒加热圈；模温机；液压油；顶针润滑'),
    T('deburr', '机器人去毛刺', '机器人去毛刺,去毛刺单元,去毛刺机', '机加', 'robot', 5, 5, 2.2, COMMON + [
        P('Brush/Wear', '毛刷磨损', '%', key=True, sample=True, normal='0~80', hi=90),
        P('Production/PartsDone', '完成件数', '件', 'int', 'count', sem='output', key=True)] + ACTIONS,
      process_type='deburr', maint='毛刷磨损；机器人原点'),
    T('robot_cell', '机器人单元', '机器人单元,机器人工作站,焊接机器人,搬运机器人,上下料机器人', '自动化', 'robot', 4, 4, 2.2, COMMON + [
        P('Robot/Mode', '运行模式', None, 'string'), P('Robot/ProgramStep', '程序步', None, 'int', 'step'),
        P('Production/PartsDone', '完成件数', '件', 'int', 'count', sem='output', key=True)] + ACTIONS,
      maint='机器人原点；夹爪气压；安全光栅'),
    T('inspection', '检测站', '内窥检测,检测站,检测设备,在线检测,视觉检测,三坐标', '质检', 'inspection', 4, 4, 2.4, COMMON + [
        P('Result/Last', '最近一件结论', None, 'string', 'quality', key=True),
        P('Result/OK', '合格数', '件', 'int', 'count', sem='good', key=True), P('Result/NG', '不合格数', '件', 'int', 'count', sem='bad', key=True)],
      process_type='inspection', maint='镜头清洁；光源亮度；标准件比对'),
    T('conveyor', '输送线', '输送线,输送机,皮带线,辊道,链板线', '物流', 'conveyor', 12, 1.2, 1, COMMON + [
        P('Line/Speed', '线速度', 'm/min', key=True), P('Motor/Current', '电机电流', 'A', role='load'),
        P('Line/Count', '通过件数', '件', 'int', 'count', sem='output', key=True)]),
    T('agv', 'AGV 车队', 'AGV 车队,AGV,AMR,叉车 AGV', '物流', 'agv', 1.2, 0.8, 0.5, COMMON + [P('Fleet/Queue', '待执行任务', '个', 'int', key=True)]),
    T('warehouse', '立体仓库', '立体仓库,立库,AS/RS,自动化仓库', '物流', 'warehouse', 12, 18, 7, COMMON + [
        P('Stock/Count', '库存件数', '件', 'int', key=True), P('Stock/Utilization', '库位利用率', '%', key=True)],
      process_type='storage', maint='堆垛机限位；货叉；光电'),
    T('compressor', '空压机', '空压机,空气压缩机,螺杆空压机', '公用', 'compressor', 2, 1.2, 1.8, COMMON + [
        P('Air/Pressure', '排气压力', 'MPa', key=True, sample=True, normal='0.6~0.8', lo=0.5, hi=0.9), P('Air/Flow', '排气量', 'm³/min', key=True),
        P('Discharge/Temperature', '排气温度', '°C', role='temp', normal='70~95', hi=105), P('Load/Rate', '加载率', '%', role='load'),
        P('Run/Hours', '累计运行时间', 'h')], maint='油位；滤芯压差；冷却器；排污'),
    T('receiving', '收货台', '收货台,来料检验台', '物流', 'buffer', 3, 3, 1, COMMON + [P('Received/Count', '收货件数', '件', 'int', 'count', sem='output', key=True)],
      process_type='receiving'),
    T('buffer', '缓存 / 线边库', '缓存,线边库,暂存,缓冲区,料架', '物流', 'buffer', 3, 3, 1, [
        P('Queue/Count', '在库件数', '件', 'int', key=True), P('Queue/Capacity', '容量', '件', 'int', key=True), P('Queue/Utilization', '利用率', '%')]),
    T('sink', '出口', '出口,下线口', '物流', 'buffer', 3, 3, 1, [P('Sink/Count', '下线件数', '件', 'int', 'count', sem='output')]),
    T('cell', '控制单元', '控制单元,工作单元,工段', '自动化', 'cell', 8, 3.6, 1, COMMON + [P('Cell/WIP', '在制件数', '件', 'int', key=True)]),
    T('plc', 'PLC', 'PLC,控制器,可编程控制器', '控制', 'plc', 1.2, 0.6, 2, [P('PLC/State', 'PLC 状态', None, 'string')]),
    T('gateway', '工业网关', '工业网关,网关,边缘网关', '控制', 'plc', 0.8, 0.5, 0.3, []),
    T('switch', '交换机', '交换机,工业交换机', '控制', 'plc', 0.8, 0.3, 0.3, []),
    T('mes', '信息系统', 'MES 系统,MES,SCADA,信息系统', '控制', 'plc', 1, 0.8, 1, []),
    T('other', '其他设备', '其他', '其他', 'box', 3, 3, 1.5, COMMON),
]
BY_CODE = {t['code']: t for t in BUILTIN}


def ensure(conn):
    """内置模板写入库（缺的新建；内置且版本低的更新）。迁移和启动时调用，幂等。"""
    from ..schema import TABLES
    t = TABLES['device_template']
    have = {r['code']: r for r in conn.execute(sa.select(t.c.id, t.c.code, t.c.builtin, t.c.version)).mappings()}
    from ..db import clock
    now = clock.now()
    n = 0
    for tp in BUILTIN:
        row = {**tp, 'aliases': tp['aliases'], 'builtin': True, 'version': VERSION, 'updated_at': now}
        cur = have.get(tp['code'])
        if cur is None:
            conn.execute(t.insert().values(**row, created_at=now))
            n += 1
        elif cur['builtin'] and (cur['version'] or 0) < VERSION:
            conn.execute(t.update().where(t.c.id == cur['id']).values(**row))
            n += 1
    return n


# ───────────────────────── 匹配 ─────────────────────────
_rx_cache: dict = {}


def _rx(pattern: str):
    r = _rx_cache.get(pattern)
    if r is None:
        parts = []
        for seg in pattern.split('/'):
            if seg == '**':
                parts.append(r'(?:.+)')
            elif seg == '*':
                parts.append(r'[^/]+')
            else:
                parts.append(re.sub(r'\\\{(\w+)\\\}', lambda m: f'(?P<{m.group(1)}>[^/]+)', re.escape(seg)))
        r = _rx_cache[pattern] = re.compile('^' + '/'.join(parts) + '$')
    return r


def match_point(points: list, name: str):
    """点位路径 → (模板点位, 取出的变量)；按模板顺序，精确的写在前面。没有匹配返回 (None, {})。"""
    for p in points or []:
        m = _rx(p['name']).match(name or '')
        if m:
            return p, m.groupdict()
    return None, {}


def norm_label(s: str) -> str:
    return re.sub(r'[\s（）()#\d]+', '', str(s or '')).lower()


def match_type(templates: list, label: str) -> str | None:
    """点表"设备类型"列（如 加工中心 / 中频炉 / 压铸机）→ 模板编码。先精确匹配编码 / 名称 / 别名，再包含匹配。"""
    if not label:
        return None
    s = norm_label(label)
    for t in templates:
        names = [t['code'], t['name']] + [a for a in (t.get('aliases') or '').split(',') if a]
        if any(norm_label(a) == s for a in names):
            return t['code']
    best = None
    for t in templates:
        for a in [t['name']] + [a for a in (t.get('aliases') or '').split(',') if a]:
            a = norm_label(a)
            if len(a) >= 2 and (a in s or s in a) and (best is None or len(a) > best[1]):
                best = (t['code'], len(a))
    return best[0] if best else None
