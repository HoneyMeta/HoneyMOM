"""现场执行与质量合规的演示数据（可重复执行，已存在的跳过）：打印机 / 打码机 / 自动打印规则、包装规则、量检具与校准、
PFMEA → 控制计划（受控、生效）→ 检验模板、受控作业指导书、MSA 研究、委外单、ECN 申请；备件在导入设备后（after_import）。"""
from datetime import timedelta

from . import db
from .db import clock
from .seed import ensure

AIAG_GRR = {   # AIAG MSA 第 4 版 GR&R 示例数据（3 人 × 3 次 × 10 件），演示里换算到缸孔尺寸：40.0125 + 值 × 0.004 mm
    'A': [[0.29, -0.56, 1.34, 0.47, -0.80, 0.02, 0.59, -0.31, 2.26, -1.36], [0.41, -0.68, 1.17, 0.50, -0.92, -0.11, 0.75, -0.20, 1.99, -1.25],
          [0.64, -0.58, 1.27, 0.64, -0.84, -0.21, 0.66, -0.17, 2.01, -1.31]],
    'B': [[0.08, -0.47, 1.19, 0.01, -0.56, -0.20, 0.47, -0.63, 1.80, -1.68], [0.25, -1.22, 0.94, 1.03, -1.20, 0.22, 0.55, 0.08, 2.12, -1.62],
          [0.07, -0.68, 1.34, 0.20, -1.28, 0.06, 0.83, -0.34, 2.19, -1.50]],
    'C': [[0.04, -1.38, 0.88, 0.14, -1.46, -0.29, 0.02, -0.46, 1.77, -1.49], [-0.11, -1.13, 1.09, 0.20, -1.07, -0.67, 0.01, -0.56, 1.45, -1.77],
          [-0.15, -0.96, 0.67, 0.11, -1.45, -0.49, 0.21, -0.49, 1.87, -2.16]]}

FMEA = [  # 工序, 功能, 产品特性, 特性代码, 特殊, 下限, 上限, 单位, 失效模式, 影响, S, 原因, 预防, O, 探测, D
    ('P10', '熔炼铁水成分合格', '原铁水 C / Si', 'C', 'SC', 3.60, 3.80, '%', '化学成分超差', '球化不良、力学性能不足', 8, '炉料配比错误 / 增碳剂加入量偏差',
     '配料优化 + 炉料台账', 3, '炉前光谱每炉', 3),
    ('P20', '球化处理充分', '残余镁', 'MG', 'CC', 0.030, 0.055, '%', '残镁不足', '球化率低，转向器壳体强度不足（安全件）', 9, '球化剂加入量不足 / 浇包等待超时',
     '球化剂称量 + 12 分钟浇完计时', 4, '球化试块光谱每包', 4),
    ('P30', '浇注充型完整', '浇注温度', 'POUR_T', 'SC', 1380, 1420, '°C', '浇注温度偏低', '冷隔、开裂', 8, '铁水等待时间长 / 浇包未烘烤', '浇注温度采集报警', 4, '测温每包', 5),
    ('P30', '型砂透气', '型砂水分', 'SAND_H2O', 'none', 3.3, 3.5, '%', '型砂水分偏高', '气孔', 7, '回砂温度高、加水量失控', '水分在线监测', 5, '巡检 + 内窥检测', 6),
    ('P60', '缸孔尺寸', '缸孔直径 Φ40 H7', 'BORE_DIA', 'CC', 40.000, 40.025, 'mm', '缸孔直径超差', '转向器装配泄漏 / 卡滞（安全件）', 9, '精镗刀磨损、热变形',
     '刀具寿命计数 + 换刀首检', 3, '测头 100% + 三坐标首件', 2),
    ('P60', '端面平面度', '端面平面度', 'FLAT', 'SC', None, 0.03, 'mm', '平面度超差', '密封不良', 7, '夹紧变形', '夹具点检', 3, '三坐标首件 / 巡检', 4),
    ('P60', '油道孔位置', '油道孔位置度', 'POS_OIL', 'none', None, 0.1, 'mm', '位置度超差', '油道不通 / 装配干涉', 7, '夹具定位销磨损', '夹具点检', 2, '三坐标首件', 4),
    ('P60', '螺纹 M10', '螺纹 M10-6H', 'THREAD', 'none', None, None, '', '螺纹不合格', '装配拧紧失效', 6, '丝锥磨损', '丝锥寿命计数', 2, '螺纹通止规', 5),
    ('P70', '交叉孔去毛刺', '交叉孔残余毛刺', 'ENDO_BURR', 'SC', None, 0.1, 'mm', '残余毛刺超标', '毛刺脱落堵塞油路', 8, '毛刷磨损 / 钻头磨损', '毛刷更换周期', 5, '自动内窥 100%', 2),
    ('P80', '内腔质量', '内腔气孔 / 缩松', 'ENDO_PORE', 'CC', None, 0.8, 'mm', '内腔缺陷漏检', '高压下渗漏（安全件）', 9, '光源亮度不足 / 判定阈值漂移', '标准件比对每班', 3, '自动内窥 100%', 3),
]


def run(c):
    from .services.exec_doc import make_effective, put_under_control
    from .services.iatf import _after_fmea_row, _derive_cp, sync_inspection
    now = clock.now()
    today = now.date()
    db.set_setting(c, 'supplier_no', db.setting(c, 'supplier_no', '100234') or '100234', '本厂在客户处的供应商号（客户标签 / ASN 用；按客户可设 supplier_no:<客户>）')
    tpl = {t['code']: t['id'] for t in db.rows(c, 'label_template')}
    # 打印机 / 打码机 / 规则
    pdf = ensure(c, 'printer', 'code', {'code': 'PR-PDF', 'name': '包装工位浏览器打印', 'kind': 'browser', 'location': '包装区', 'enabled': True, 'status': 'ok'})
    ensure(c, 'printer', 'code', {'code': 'PR-ZEBRA-01', 'name': '标签机 TL411（包装线）', 'kind': 'zpl', 'host': '192.168.10.51', 'port': 9100, 'dpi': 203,
                                  'location': '包装区', 'enabled': False, 'status': 'unknown'})
    ensure(c, 'printer', 'code', {'code': 'PR-TSC-01', 'name': 'TSC TTP-244（来料区）', 'kind': 'tspl', 'host': '192.168.10.52', 'port': 9100, 'dpi': 203,
                                  'location': '来料区', 'enabled': False, 'status': 'unknown'})
    mk = ensure(c, 'marker', 'code', {'code': 'MK-LASER-01', 'name': '激光打码机（模拟）', 'mode': 'sim', 'cmd_template': 'MARK {job} {data}\\r\\n', 'ack': 'OK',
                                      'err': 'ERR|NG', 'timeout_ms': 5000, 'symbology': 'datamatrix', 'location': '清理抛丸后', 'enabled': True})
    if not db.rows(c, 'label_rule', limit=1):
        for ev, t, pr, m, en, rem in (('carton_close', 'LBL-CARTON', pdf, None, True, '满箱 / 手工封箱打箱标签'),
                                      ('pallet_close', 'LBL-PALLET', pdf, None, True, '封托打托盘标签'),
                                      ('pallet_close', 'LBL-AUTO-GTL', pdf, None, True, '封托打客户运输标签'),
                                      ('part_create', 'LBL-PART-DM', None, mk, False, '一件一码激光打 DataMatrix（产线接好打码机后启用）'),
                                      ('wo_release', 'LBL-WO', pdf, None, False, '下达打工单流转卡'),
                                      ('gauge_cal', 'LBL-GAUGE', pdf, None, True, '量具放行打状态标签')):
            if t in tpl:
                db.insert(c, 'label_rule', {'event': ev, 'template': tpl[t], 'printer': pr, 'marker': m, 'copies': 1, 'enabled': en, 'remark': rem}, raw=True)
    mats = {m['code']: m['id'] for m in db.rows(c, 'material')}
    pid = {p['code']: p['id'] for p in db.rows(c, 'process')}
    if 'SGH-450' in mats and not db.rows(c, 'pack_rule', limit=1):
        db.insert(c, 'pack_rule', {'customer': '示例主机厂', 'material': mats['SGH-450'], 'customer_part_no': '7700-450-001', 'carton_qty': 20, 'pallet_cartons': 24,
                                   'mixed_lot': False, 'carton_template': tpl.get('LBL-CARTON'), 'pallet_template': tpl.get('LBL-PALLET'),
                                   'customer_template': tpl.get('LBL-AUTO-GTL'), 'remark': '每箱 20 件、每托 24 箱，不混批'}, raw=True)
        db.insert(c, 'pack_rule', {'material': mats['SGH-450'], 'carton_qty': 20, 'pallet_cartons': 24, 'mixed_lot': True, 'carton_template': tpl.get('LBL-CARTON'),
                                   'remark': '通用（未指定客户）'}, raw=True)
    # 量检具
    gauges = [('G-CMM-01', '三坐标测量机', 'cmm', '700×1000×600', 365, -120, '计量室'), ('G-BORE-01', '内径百分表 Φ35–50', 'bore', 'Φ35–50 / 0.001', 180, -60, 'CNC 线'),
              ('G-BORE-02', '内径百分表 Φ35–50', 'bore', 'Φ35–50 / 0.001', 180, -170, 'CNC 线'), ('G-CAL-01', '数显卡尺 0–150', 'caliper', '0–150 / 0.01', 365, -30, '清理线'),
              ('G-CAL-02', '数显卡尺 0–150', 'caliper', '0–150 / 0.01', 365, -380, '清理线'), ('G-THR-M10', 'M10-6H 螺纹塞规', 'thread', 'M10-6H 通止', 180, -40, 'CNC 线'),
              ('G-SPEC-01', '直读光谱仪', 'spectro', 'Fe 基', 365, -90, '炉前化验室'), ('G-TH-01', '浸入式测温仪', 'thermo', '800–1800°C', 180, -20, '浇注区'),
              ('G-ENDO-01', '内窥检测系统标准件', 'fixture', '缺陷标准件 0.1 / 0.8 mm', 90, -10, '检测站'), ('G-HARD-01', '布氏硬度计', 'hardness', 'HBW 10/3000', 365, -200, '理化室')]
    for code, name, gt, spec, iv, ago, loc in gauges:
        if db.get(c, 'gauge', code=code):
            continue
        last = today + timedelta(days=ago)
        nd = last + timedelta(days=iv)
        st = 'overdue' if nd < today else 'due' if nd <= today + timedelta(days=30) else 'active'
        gid = db.insert(c, 'gauge', {'code': code, 'name': name, 'gtype': gt, 'spec': spec, 'cal_interval_days': iv, 'last_cal': last, 'next_due': nd, 'status': st,
                                     'location': loc, 'owner': '孙敏', 'cert_no': f'JL{last:%y%m}{code[-2:]}',
                                     'lock_reason': f'校准过期（到期 {nd}），已自动锁定' if st == 'overdue' else None, 'released_by': '孙敏', 'released_at': now}, raw=True)
        db.insert(c, 'gauge_cal', {'gauge': gid, 'cal_date': last, 'result': 'pass', 'agency': 'external' if gt in ('cmm', 'spectro', 'hardness') else 'internal',
                                   'provider': '省计量院' if gt in ('cmm', 'spectro', 'hardness') else '计量室', 'cert_no': f'JL{last:%y%m}{code[-2:]}', 'next_due': nd,
                                   'by': '孙敏'}, raw=True)
    # PFMEA → 控制计划（受控、生效）→ 检验模板
    if 'SGH-450' in mats and not db.get(c, 'pfmea', title='SGH-450 转向器壳体 过程 FMEA'):
        fid = db.insert(c, 'pfmea', {'title': 'SGH-450 转向器壳体 过程 FMEA', 'product': mats['SGH-450'], 'rev': 'A', 'status': 'approved', 'method': 'ap',
                                     'team': '郑凯（工艺）、孙敏（质量）、周涛（设备）、张强（生产）', 'fmea_date': today - timedelta(days=60),
                                     'scope': '熔炼 → 球化 → 造型浇注 → 清理 → CNC 机加 → 去毛刺 → 内窥检测'}, raw=True)
        for i, (pc, fn, pch, ch, sp, lo, hi, u, fm, eff, s, cause, prev, o, det, d) in enumerate(FMEA):
            rid = db.insert(c, 'pfmea_row', {'pfmea': fid, 'seq': (i + 1) * 10, 'process': pid.get(pc), 'function': fn, 'product_char': pch, 'char': ch, 'special': sp,
                                             'lo': lo, 'hi': hi, 'unit': u, 'failure_mode': fm, 'effect': eff, 'severity': s, 'cause': cause, 'prevention': prev,
                                             'occurrence': o, 'detection_ctrl': det, 'detection': d, 'action_status': 'open' if s * o * d >= 100 else None,
                                             'actions': '缸孔精镗改为在线补偿 + 测头闭环' if ch == 'BORE_DIA' else None}, raw=True)
            _after_fmea_row(c, rid, None)
        f = db.get(c, 'pfmea', fid)
        res = _derive_cp(c, f, {}, {'name': '郑凯', 'username': 'engineer'})
        cp = db.get(c, 'control_plan', res['id'])
        db.update(c, 'control_plan', cp['id'], {'title': 'SGH-450 转向器壳体 量产控制计划', 'team': f['team']}, raw=True)
        for it in db.rows(c, 'cp_item', plan=cp['id']):          # 缸孔指定量具类型为内径表（检验录入必须选有效的内径表）
            if it['char'] == 'BORE_DIA':
                db.update(c, 'cp_item', it['id'], {'gauge_type': 'bore', 'method': '内径百分表 / 测头'}, raw=True)
        user = {'name': '郑凯', 'username': 'engineer'}
        rev = put_under_control(c, 'control_plan', cp['id'], 'SGH-450 转向器壳体 量产控制计划', 'control_plan', mats['SGH-450'], None, 'A', user)
        r = db.get(c, 'ctrl_doc_rev', rev['rev_id'])
        db.update(c, 'ctrl_doc_rev', r['id'], {'status': 'approved', 'reviewed_by': '孙敏', 'approved_by': '郑凯', 'approved_at': now, 'change_note': '首版'}, raw=True)
        make_effective(c, db.get(c, 'ctrl_doc_rev', r['id']), user)
        sync_inspection(c, db.get(c, 'control_plan', cp['id']))
        # 作业指导书纳入受控（生效 A 版）
        for title, proc in (('球化处理作业指导书', 'P20'), ('CNC 换刀作业指导', 'P60')):
            k = db.get(c, 'kb_doc', title=title)
            if k and not k['ctrl_doc']:
                db.update(c, 'kb_doc', k['id'], {'product': mats['SGH-450'], 'process': pid.get(proc), 'version': 'A'}, raw=True)
                rv = put_under_control(c, 'kb_doc', k['id'], title, 'wi', mats['SGH-450'], pid.get(proc), 'A', user)
                db.update(c, 'ctrl_doc_rev', rv['rev_id'], {'status': 'approved', 'approved_by': '郑凯', 'reviewed_by': '孙敏', 'approved_at': now}, raw=True)
                make_effective(c, db.get(c, 'ctrl_doc_rev', rv['rev_id']), user)
    # MSA（AIAG 示例数据换算到缸孔）
    g = db.get(c, 'gauge', code='G-BORE-01')
    if g and not db.rows(c, 'msa_study', limit=1):
        data = {'appraisers': ['孙敏', '赵磊', '钱明'], 'values': [[[round(40.0125 + v * 0.004, 5) for v in t] for t in AIAG_GRR[a]] for a in 'ABC']}
        sid = db.insert(c, 'msa_study', {'title': '缸孔内径表 GR&R（方差分析）', 'method': 'grr_anova', 'gauge': g['id'], 'char': 'BORE_DIA', 'tolerance': 0.025,
                                         'parts': 10, 'appraisers': 3, 'trials': 3, 'data': data, 'study_date': today - timedelta(days=20), 'by': '孙敏'}, raw=True)
        from .services.iatf import _msa_calc
        _msa_calc(c, db.get(c, 'msa_study', sid), {}, {'name': '孙敏', 'username': 'qc'})
    # 委外（热处理）与 ECN 申请
    hid = ensure(c, 'process', 'code', {'code': 'P55', 'name': '去应力退火（委外）', 'ptype': 'other', 'std_cycle_s': 0})
    if 'SGH-450-B' in mats and not db.rows(c, 'outsource_order', limit=1):
        db.insert(c, 'outsource_order', {'supplier': '某热处理厂', 'process': hid, 'material': mats['SGH-450-B'], 'qty': 200, 'unit_price': 18, 'loss_pct': 0.5,
                                         'lead_days': 3, 'status': 'draft', 'remark': '毛坯去应力退火（演示）'}, raw=True)
    if 'SGH-450' in mats and not db.rows(c, 'ecn', limit=1):
        db.insert(c, 'ecn', {'title': 'SGH-450 缸孔精镗余量 0.25→0.20、O1201 升 V4', 'ctype': 'process', 'product': mats['SGH-450'],
                             'reason': '缸孔尺寸 Cpk 1.12 偏低，精镗余量调小提高一致性', 'description': 'NC 程序 O1201 V3→V4；控制计划缸孔反应计划补充"测头闭环补偿失效时停机"',
                             'wo_policy': 'finish', 'status': 'request', 'requested_by': '郑凯', 'effective_date': today + timedelta(days=7)}, raw=True)


def after_import(c):
    """导入设备后：备件（关联主要设备）。"""
    if db.rows(c, 'spare_part', limit=1):
        return
    eq = {e['etype']: e['id'] for e in db.rows(c, 'equipment')}
    spares = [('SP-BRG-7014', '主轴轴承 7014C P4', 'CNC 主轴', 'cnc', 2, 4, 2, 4200, '某轴承公司', 30, True),
              ('SP-PULL-BT40', 'BT40 刀柄拉钉', '加工中心', 'cnc', 10, 30, 6, 85, '某刀具公司', 7, False),
              ('SP-LUB-FLT', '润滑泵滤芯', '加工中心润滑', 'cnc', 4, 12, 9, 60, '某液压件公司', 5, False),
              ('SP-SOL-24V', '电磁阀 4V210 24V', '气动通用', None, 4, 10, 3, 120, '某气动公司', 5, False),
              ('SP-PUMP-SEAL', '冷却水泵机械密封', '中频炉冷却', 'furnace', 2, 4, 2, 680, '某泵业', 10, True),
              ('SP-LINING', '炉衬耐火材料（中性）', '中频炉炉衬', 'furnace', 3, 8, 5, 5200, '某耐材公司', 15, True),
              ('SP-BLADE', '抛头叶片', '抛丸机', 'shotblast', 8, 24, 10, 160, '某抛丸设备厂', 10, False),
              ('SP-BRUSH', '去毛刺毛刷 Φ8', '去毛刺单元', 'deburr', 20, 60, 14, 35, '某磨具公司', 7, False),
              ('SP-LENS', '内窥镜头保护片', '内窥检测站', 'inspection', 5, 15, 6, 240, '某视觉公司', 14, False)]
    for code, name, applies, et, mn, mx, q, price, sup, lead, crit in spares:
        sid = db.insert(c, 'spare_part', {'code': code, 'name': name, 'applies': applies, 'equipment': eq.get(et), 'min_qty': mn, 'max_qty': mx, 'qty': 0, 'price': price,
                                          'supplier': sup, 'lead_days': lead, 'critical': crit, 'unit': '个', 'location': '备件库 A 区'}, raw=True)
        tid = db.insert(c, 'spare_txn', {'spare': sid, 'kind': 'in', 'qty': q, 'by': '吴芳', 'time': clock.now() - timedelta(days=30), 'remark': '期初库存'}, raw=True)
        from .services.exec_outsource import _after_spare_txn
        _after_spare_txn(c, tid, None)
