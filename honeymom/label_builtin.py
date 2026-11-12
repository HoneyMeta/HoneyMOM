"""内置标签模板（迁移 v0400 写入；用户可以复制后修改）。坐标单位 mm，元素格式见 labelrender.py。

客户标签示例按汽车行业通用运输标签的版式（参考 VDA 4902：收货方 / 卸货点 / 发货单号 / 零件号 / 数量 / 供应商号 / 包装号 / 批次，
条码前缀用 ANSI MH10.8.2 数据标识符 N P Q V S H），不带任何主机厂品牌。
"""


def _t(x, y, h, text, **k):
    return {'type': 'text', 'x': x, 'y': y, 'h': h, 'text': text, **k}


def _bc(x, y, w, h, data, sym='code128', **k):
    return {'type': 'barcode', 'x': x, 'y': y, 'w': w, 'h': h, 'symbology': sym, 'data': data, **k}


def _ln(x, y, x2, y2, t=0.3):
    return {'type': 'line', 'x': x, 'y': y, 'x2': x2, 'y2': y2, 't': t}


def _box(x, y, w, h, t=0.4):
    return {'type': 'box', 'x': x, 'y': y, 'w': w, 'h': h, 't': t}


PART = [_bc(2, 3, 14, 14, '{{sn}}', 'datamatrix'), _t(18, 3, 2.6, '{{sn}}', bold=True), _t(18, 7.5, 2.2, '{{product_code}}'),
        _t(18, 11, 2.2, '炉 {{heat}}'), _t(18, 14.5, 2.2, '{{special_mark}}', bold=True)]
CARTON = [_box(1, 1, 98, 58), _t(4, 3, 4, '箱标签 CARTON', bold=True), _t(60, 3.5, 3, '{{customer}}', align='right', w=36),
          _ln(1, 9, 99, 9), _t(4, 11, 3.2, '零件号 {{part_no}}', bold=True), _t(4, 16, 3, '{{part_name}}'), _t(4, 21, 3, '数量 {{qty}} {{unit}}', bold=True),
          _t(4, 26, 2.8, '批次 {{lots}}'), _t(4, 30.5, 2.8, '日期 {{date}}   包装 {{packed_by}}'), _bc(74, 11, 22, 22, '{{qr}}', 'qr'),
          _ln(1, 36, 99, 36), _bc(6, 38, 88, 14, '{{code}}', 'code128', hr=True)]
PALLET = [_box(1, 1, 98, 148), _t(4, 4, 6, '托盘 PALLET', bold=True), _t(4, 13, 9, '{{code}}', bold=True), _ln(1, 26, 99, 26),
          _t(4, 29, 4, '客户 {{customer}}'), _t(4, 36, 4, '零件号 {{part_no}}', bold=True), _t(4, 43, 3.6, '{{part_name}}'),
          _t(4, 50, 4.5, '数量 {{qty}}  箱数 {{cartons}}', bold=True), _t(4, 58, 3.4, '批次 {{lots}}'), _t(4, 64, 3.4, '发货单 {{delivery}}   日期 {{date}}'),
          _ln(1, 71, 99, 71), _bc(24, 75, 52, 52, '{{qr}}', 'qr'), _bc(6, 130, 88, 15, '{{code}}', 'code128', hr=True)]
# 汽车行业通用运输标签（VDA 4902 版式参考，210 × 74 mm）
VDA = [_box(0.5, 0.5, 209, 73, 0.4),
       _ln(0.5, 14, 209.5, 14), _ln(0.5, 29, 209.5, 29), _ln(0.5, 44, 209.5, 44), _ln(0.5, 59, 209.5, 59), _ln(105, 0.5, 105, 29), _ln(140, 29, 140, 59),
       _t(2, 1.5, 1.8, '(1) 收货方 Receiver'), _t(2, 5, 3, '{{receiver}}', bold=True), _t(2, 9.5, 2.4, '{{receiver_addr}}'),
       _t(107, 1.5, 1.8, '(2) 卸货点 Dock / Gate'), _t(107, 5.5, 4.5, '{{dock}}', bold=True),
       _t(2, 15.5, 1.8, '(3) 发货单号 Delivery note (N)'), _t(2, 18.5, 3, '{{delivery_no}}', bold=True), _bc(42, 15.5, 60, 12, 'N{{delivery_no}}'),
       _t(107, 15.5, 1.8, '(4) 供应商 Supplier'), _t(107, 19, 2.6, '{{supplier_name}}'), _t(107, 23, 2.2, '净重 {{net_kg}} kg  毛重 {{gross_kg}} kg  件数 {{packages}}'),
       _t(2, 30.5, 1.8, '(8) 客户零件号 Customer part no (P)'), _t(2, 34, 5, '{{cust_part_no}}', bold=True), _bc(62, 30.5, 76, 12, 'P{{cust_part_no}}'),
       _t(142, 30.5, 1.8, '(9) 数量 Quantity (Q)'), _t(142, 34, 5, '{{qty}}', bold=True), _bc(165, 30.5, 42, 12, 'Q{{qty}}'),
       _t(2, 45.5, 1.8, '(10) 名称 Description'), _t(2, 49, 3, '{{description}}'), _t(2, 53.5, 1.8, '(11) 供应商零件号 {{supplier_part_no}}   (14) 变更等级 {{ecl}}'),
       _t(142, 45.5, 1.8, '(12) 供应商号 Supplier no (V)'), _t(142, 48.5, 3, '{{supplier_no}}', bold=True), _bc(142, 52, 65, 6.5, 'V{{supplier_no}}'),
       _t(2, 60.5, 1.8, '(15) 包装号 Package no (S)'), _t(2, 63.5, 3, '{{package_no}}', bold=True), _bc(40, 60, 64, 12.5, 'S{{package_no}}'),
       _t(107, 60.5, 1.8, '(16) 批次 Batch (H)  (13) 日期 {{date}}'), _t(107, 63.5, 3, '{{batch_no}}', bold=True), _bc(145, 60, 62, 12.5, 'H{{batch_no}}')]
GAUGE = [_box(0.5, 0.5, 49, 29, 0.3), _t(2, 2, 3, '{{code}}', bold=True), _t(2, 6.5, 2.2, '{{name}}'), _t(2, 11, 2.2, '校准 {{last_cal}}'),
         _t(2, 15, 2.6, '到期 {{next_due}}', bold=True), _t(2, 20, 2.2, '状态 {{status_text}}'), _t(2, 24, 2, '{{owner}}'), _bc(32, 6, 16, 16, '{{code}}', 'qr')]
LOCATION = [_t(4, 3, 10, '{{code}}', bold=True), _t(4, 16, 3.5, '{{name}}'), _bc(4, 22, 72, 14, '{{code}}', 'code128')]
LOT = [_box(0.5, 0.5, 69, 39, 0.3), _t(3, 2.5, 3, '批次/炉次', bold=True), _t(3, 7, 6, '{{lot}}', bold=True), _t(3, 15, 2.8, '{{material}}'),
       _t(3, 19.5, 2.8, '数量 {{qty}}   日期 {{date}}'), _bc(3, 25, 64, 11, '{{lot}}', 'code128')]
WO = [_box(0.5, 0.5, 99, 69, 0.4), _t(3, 2.5, 4, '工单流转卡', bold=True), _t(3, 9, 6, '{{code}}', bold=True), _t(3, 18, 3.2, '{{product}}'),
      _t(3, 23.5, 3.2, '数量 {{qty}}   交期 {{due}}'), _t(3, 29, 3, '客户 {{customer}}'), _bc(72, 9, 24, 24, '{{code}}', 'qr'), _bc(5, 40, 90, 18, '{{code}}', 'code128', hr=True)]

SAMPLES = {
    'part': {'sn': 'SG2611040001', 'product_code': 'SGH-450', 'heat': 'H261104-03', 'special_mark': '◆ CC'},
    'carton': {'code': 'CT2611040001', 'customer': '示例主机厂', 'part_no': '7700-450-001', 'part_name': 'SGH-450 转向器壳体', 'qty': 20, 'unit': '件',
               'lots': 'H261104-03', 'date': '2026-11-04', 'packed_by': '吴芳', 'qr': 'CT2611040001|7700-450-001|20|H261104-03'},
    'pallet': {'code': 'PL2611040001', 'customer': '示例主机厂', 'part_no': '7700-450-001', 'part_name': 'SGH-450 转向器壳体', 'qty': 480, 'cartons': 24,
               'lots': 'H261104-03,H261104-04', 'delivery': 'FH2611040001', 'date': '2026-11-04', 'qr': 'PL2611040001|7700-450-001|480'},
    'customer': {'receiver': '示例主机厂 总装二厂', 'receiver_addr': '示例市 示例路 1 号', 'dock': 'G12', 'delivery_no': '2611040001', 'supplier_name': '某转向器壳体铸造厂',
                 'net_kg': 364.8, 'gross_kg': 392.0, 'packages': 1, 'cust_part_no': '7700450001', 'qty': 480, 'description': '转向器壳体 SGH-450',
                 'supplier_part_no': 'SGH-450', 'ecl': 'C', 'supplier_no': '100234', 'package_no': '2611040001', 'batch_no': 'H26110403', 'date': '2026-11-04'},
    'gauge': {'code': 'G-CMM-01', 'name': '三坐标测量机', 'last_cal': '2026-03-01', 'next_due': '2027-03-01', 'status_text': '有效', 'owner': '计量室'},
    'location': {'code': 'W-FG-01', 'name': '成品立体仓库 1 区'},
    'lot': {'lot': 'H261104-03', 'material': 'SGH-450 壳体毛坯', 'qty': 120, 'date': '2026-11-04'},
    'wo': {'code': 'WO261104001', 'product': 'SGH-450 转向器壳体', 'qty': 600, 'due': '2026-11-06', 'customer': '示例主机厂'},
}

BUILTIN_TEMPLATES = [
    {'code': 'LBL-PART-DM', 'name': '零件 DataMatrix 标签（一件一码）', 'usage': 'part', 'width_mm': 30, 'height_mm': 20, 'layout': {'elements': PART}, 'sample': SAMPLES['part']},
    {'code': 'LBL-CARTON', 'name': '箱标签', 'usage': 'carton', 'width_mm': 100, 'height_mm': 60, 'layout': {'elements': CARTON}, 'sample': SAMPLES['carton']},
    {'code': 'LBL-PALLET', 'name': '托盘标签', 'usage': 'pallet', 'width_mm': 100, 'height_mm': 150, 'layout': {'elements': PALLET}, 'sample': SAMPLES['pallet']},
    {'code': 'LBL-AUTO-GTL', 'name': '汽车行业运输标签（VDA 4902 版式参考）', 'usage': 'customer', 'width_mm': 210, 'height_mm': 74, 'layout': {'elements': VDA},
     'sample': SAMPLES['customer'], 'remark': '通用版式示例；按客户的标签规范复制后修改字段位置和数据标识符'},
    {'code': 'LBL-GAUGE', 'name': '量具状态标签', 'usage': 'gauge', 'width_mm': 50, 'height_mm': 30, 'layout': {'elements': GAUGE}, 'sample': SAMPLES['gauge']},
    {'code': 'LBL-LOC', 'name': '库位标签', 'usage': 'location', 'width_mm': 80, 'height_mm': 40, 'layout': {'elements': LOCATION}, 'sample': SAMPLES['location']},
    {'code': 'LBL-LOT', 'name': '批次/炉次标签', 'usage': 'lot', 'width_mm': 70, 'height_mm': 40, 'layout': {'elements': LOT}, 'sample': SAMPLES['lot']},
    {'code': 'LBL-WO', 'name': '工单流转卡', 'usage': 'wo', 'width_mm': 100, 'height_mm': 70, 'layout': {'elements': WO}, 'sample': SAMPLES['wo']},
]
