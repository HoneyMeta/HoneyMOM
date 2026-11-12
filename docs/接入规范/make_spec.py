"""生成《蜂元智能 MES 数据接入规范》（Word）和点表模板（Excel）。

    python docs/接入规范/make_spec.py            → docs/接入规范/蜂元智能MES数据接入规范.docx、点表模板.xlsx
PDF 用 Word / WPS / LibreOffice 另存（B 机：F:\\vfdev\\scripts\\m2\\docx2pdf.ps1）。
版式：全黑字；正文宋体、标题黑体；编号用（1）（2），不用圆点；不用全角空格；不把图和段落捆在一起。
点表格式和虚拟工厂交付包（VF docs/interface/点表格式.md）一致。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from docx import Document  # noqa: E402
from docx.enum.table import WD_TABLE_ALIGNMENT  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.shared import Cm, Pt, RGBColor  # noqa: E402

VERSION = 'V1.0'
DATE = '2026 年 11 月'
BLACK = RGBColor(0, 0, 0)


def _font(run, name='宋体', size=10.5, bold=False):
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = BLACK
    rpr = run._element.get_or_add_rPr()
    rf = rpr.find(qn('w:rFonts'))
    if rf is None:
        rf = rpr.makeelement(qn('w:rFonts'), {})
        rpr.append(rf)
    for k in ('w:eastAsia', 'w:ascii', 'w:hAnsi', 'w:cs'):
        rf.set(qn(k), name)


class Doc:
    def __init__(self):
        self.d = Document()
        sec = self.d.sections[0]
        sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
        sec.left_margin = sec.right_margin = Cm(2.6)
        sec.top_margin, sec.bottom_margin = Cm(2.5), Cm(2.3)
        st = self.d.styles['Normal']
        st.font.name = '宋体'
        st.font.size = Pt(10.5)
        st.font.color.rgb = BLACK
        st.element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
        st.paragraph_format.line_spacing = 1.35
        st.paragraph_format.space_after = Pt(3)
        for i in range(1, 4):
            hs = self.d.styles[f'Heading {i}']
            hs.font.color.rgb = BLACK
            hs.font.name = '黑体'
            hs.element.rPr.rFonts.set(qn('w:eastAsia'), '黑体')
            hs.paragraph_format.keep_with_next = False

    def title(self, text, size=22):
        p = self.d.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _font(p.add_run(text), '黑体', size, True)
        return p

    def center(self, text, size=12, name='宋体'):
        p = self.d.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _font(p.add_run(text), name, size)

    def h(self, text, level=1):
        p = self.d.add_heading(level=level)
        p.paragraph_format.keep_with_next = False
        p.paragraph_format.space_before = Pt(12 if level == 1 else 8)
        _font(p.add_run(text), '黑体', {1: 15, 2: 12.5, 3: 11}[level], True)

    def p(self, text, indent=True):
        par = self.d.add_paragraph()
        if indent:
            par.paragraph_format.first_line_indent = Pt(21)
        _font(par.add_run(text))
        return par

    def items(self, lst):
        for i, t in enumerate(lst, 1):
            par = self.d.add_paragraph()
            par.paragraph_format.left_indent = Pt(21)
            par.paragraph_format.first_line_indent = Pt(-21)
            _font(par.add_run(f'（{i}）{t}'))

    def code(self, text):
        for ln in text.strip('\n').split('\n'):
            par = self.d.add_paragraph()
            par.paragraph_format.left_indent = Pt(21)
            par.paragraph_format.space_after = Pt(0)
            _font(par.add_run(ln), 'Consolas', 9)
            par.runs[0]._element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')

    def table(self, rows, widths=None, size=9):
        t = self.d.add_table(rows=len(rows), cols=len(rows[0]))
        t.style = 'Table Grid'
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, r in enumerate(rows):
            for j, x in enumerate(r):
                c = t.cell(i, j)
                c.text = ''
                _font(c.paragraphs[0].add_run('' if x is None else str(x)), '黑体' if i == 0 else '宋体', size, i == 0)
                if widths:
                    c.width = Cm(widths[j])
        self.d.add_paragraph()
        return t

    def save(self, path):
        self.d.save(path)


def build(path):
    from honeymom.factory import pointtable, templates
    d = Doc()
    for _ in range(5):
        d.d.add_paragraph()
    d.title('蜂元智能 MES')
    d.title('数据接入规范', 26)
    d.center('（系统集成商 / 设备厂家版）', 14)
    for _ in range(10):
        d.d.add_paragraph()
    d.center(f'版本：{VERSION}', 12)
    d.center(f'发布日期：{DATE}', 12)
    d.center('蜂元科技（郑州）有限责任公司', 12, '黑体')
    d.d.add_page_break()

    d.h('一、总则')
    d.h('1.1 目的', 2)
    d.p('本规范规定系统集成商、设备厂家向蜂元智能 MES 交付设备数据时的命名、接入方式、点表、平面图、三维模型和联调验收要求。'
        '交付物在实施阶段按本规范提交，经 MES 接入中心导入、逐点核对、确认后，同一份工厂模型同时用于设备台账、数据采集、二维 / 三维数字孪生和 AI 数据目录。')
    d.h('1.2 适用范围', 2)
    d.p('适用于蜂元智能 MES 5.0 及以后版本的新建项目、改造项目和设备增补。与本规范不一致的历史系统，由实施方和集成商在调研阶段书面确认差异和处理办法。')
    d.h('1.3 术语', 2)
    d.table([['术语', '说明'],
             ['交付包', '集成商一次提交的 zip 文件：点表（points.xlsx）、平面图（layout.dxf）、三维模型（models/*.glb）、说明（README.md）'],
             ['设备编码', '设备的唯一编码，贯穿点表、平面图块属性、三维模型节点名、MES 设备台账'],
             ['点位编码', '采集点的唯一编码，建议为“设备编码.点位路径”'],
             ['点位路径', '设备内的点位层级名，用“/”分隔，如 Monitoring/Spindle/Load'],
             ['用途', '点位在 MES 中的业务含义类别，如运行状态、报警、计数、电能（见第五章）'],
             ['工厂坐标', '原点在厂房左下角，X 向右，Y 向上，单位米；平面图、点表设备位置、三维模型统一使用'],
             ['边缘网关', '部署在车间、把 Modbus、S7、FOCAS 等现场协议转换为 OPC UA 或 MQTT 的设备或软件']], [3.2, 12.6])

    d.h('二、命名与编码规则')
    d.items(['设备编码只用大写英文字母、数字、“-”和“_”，长度不超过 20 个字符，全厂唯一，如 CNC1、DC-03、FUR_2。同类设备按编号顺序编码，编号和现场铭牌一致。',
             '设备编码一经交付不得修改。确需修改的，以设备增补 / 停用的方式处理，并在交付说明中写明对应关系。',
             '点位编码全厂唯一，建议写成“设备编码.点位路径（用 . 连接）”，如 CNC1.Monitoring.Spindle.Load；原始 PLC 符号名可写在备注里。',
             '点位路径优先采用设备类型模板中的标准名（附录 C），如运行状态 Common/State、当前报警 Common/Alarm、实时功率 Common/PowerKW、累计电能 Common/EnergyKWh。'
             '与模板一致的点位，MES 自动识别用途，不需要逐点配置。',
             '车间、产线、工位的名称和客户组织结构一致；工位编码缺省为“WS-设备编码”。',
             '同一个点位只交付一个主协议地址；同一设备的点位尽量走同一种接入方式。'])

    d.h('三、接入方式')
    d.p('优先级从高到低为：OPC UA、MQTT（本规范的主题与载荷）、MQTT Sparkplug B、HTTP 推送、中间库。Modbus、S7、FOCAS、MC 等现场协议原则上不直接接入 MES，'
        '须经边缘网关转换为 OPC UA 或 MQTT。边缘网关可以采用开源的 Neuron，也可以采用集成商自有网关，但输出必须符合本规范。少量调试点或仪表可以 Modbus TCP 直连，须在交付说明中列明。')
    d.h('3.1 OPC UA（推荐）', 2)
    d.items(['服务器端点形如 opc.tcp://主机:4840/路径；交付说明写明端点、安全策略和账号。正式环境安全策略推荐 Basic256Sha256（签名并加密），调试阶段可以 None。',
             'NodeId 用字符串型，写完整，如 ns=2;s=Line1/CNC1/Monitoring/Spindle/Load。命名空间按 URI 解析，交付说明写明 URI，MES 不依赖命名空间序号不变。',
             '数值点须带 EngineeringUnits（工程单位），有量程的带 EURange；变量的 Description 填中文含义。',
             'MES 默认以订阅方式采集，采样间隔取点表“采集周期”；服务器须支持至少 1000 个监视项和 500 ms 的采样间隔。',
             'SourceTimestamp 填数据产生时刻（设备侧时间），精确到毫秒；动作级信号（阀、到位、循环开始 / 结束）的时间戳必须是动作发生的时刻。'])
    d.h('3.2 MQTT（本规范的主题与载荷）', 2)
    d.p('Broker 采用 MQTT 3.1.1 或 5.0；正式环境启用 TLS 和用户名 / 密码（或客户端证书），MES 使用只读账号订阅。主题与载荷见第六章。')
    d.h('3.3 MQTT Sparkplug B', 2)
    d.items(['主题 spBv1.0/组/消息类型/边缘节点[/设备]，载荷为 Sparkplug B 的 protobuf 编码；须发送 NBIRTH / DBIRTH 出生证书，DATA 消息可以只带别名。',
             '出生证书的指标属性中填写 engUnit（工程单位）、engLow / engHigh（量程）和 description（中文含义），MES 发现点位时直接读取。',
             '点表“地址”写“组/边缘节点/设备:指标名”，如 G1/EDGE1/PRESS1:Hydraulic/Pressure；节点级指标省略设备段。'])
    d.h('3.4 HTTP 推送', 2)
    d.p('适用于边缘程序、上位机自带推送能力的场景。MES 管理员在“系统 › API 密钥”中为推送方创建密钥（权限 onboard.push.write），推送方按下面的格式调用，一次不超过 5 万个值：')
    d.code('''POST /api/onboard/push
X-API-Key: hmk_xxxxxxxx_xxxxxxxxxxxxxxxx
{"source": "数据源编码", "values": [{"code": "CNC1.Common.State", "value": "Executing", "ts": "2026-11-05T08:00:00.250+08:00", "q": 0}]}''')
    d.p('code 为点位编码（也可以用 address 填点表地址）；ts 为 ISO 8601 时间（带时区，到毫秒）；q 为质量码（0 好、1 不确定、2 坏）。点表中没有的编码进入 MES 的待确认队列。')
    d.h('3.5 中间库', 2)
    d.items(['适用于已有 SCADA / 数据库的场景。集成商提供一张表或视图，MES 以只读账号按水位列增量轮询（默认每 5 秒）。',
             '长表结构（推荐）：点位名、时间、数值三列，外加一列单调递增的水位列（自增 id 或写入时间），如 mes_points(id, tag, ts, value)。宽表（一行一个时刻、每列一个点位）也支持。',
             '支持 SQL Server、PostgreSQL、MySQL、Oracle、SQLite 等 SQLAlchemy 能连接的数据库；交付说明写明连接串（不含密码，密码另行移交）、表名、各列含义。'])
    d.h('3.6 Modbus 与其它现场协议', 2)
    d.items(['Modbus 地址写成“从站:表:地址”，表为 hr（保持寄存器）、ir（输入寄存器）、coil（线圈）、di（离散输入），地址从 0 起（PDU 地址），如 11:hr:1000。',
             '多寄存器数值在点表“备注”中写明编码和字序，如“float32 ABCD，2 个寄存器”；整数寄存器按“工程值 = 原始值 × 缩放 + 偏移”换算。',
             'S7、FOCAS、MC、EtherNet/IP 等协议经边缘网关转换后按 OPC UA 或 MQTT 交付；点表中的地址为转换后的地址，原 PLC 地址写在备注里。'])

    d.h('四、网络与安全要求')
    d.items(['设备网络（OT）与办公网络（IT）分区，MES 服务器或其采集节点通过防火墙访问 OT 区，只开放必要端口：OPC UA 4840、MQTT 1883 / 8883、Modbus 502、HTTPS 443。',
             'MES 对设备侧只读。需要下发参数的点位（读写 RW）在点表中单独标注，由客户书面确认后再开放写权限。',
             '所有接入账号（OPC UA 用户、MQTT 账号、数据库账号、API 密钥）专号专用，不使用默认密码；密码和密钥通过线下渠道移交，不写入交付包。',
             '正式环境的 OPC UA 使用签名加密的安全策略，MQTT 使用 TLS；证书有效期不少于 2 年，并在交付说明中写明到期日。',
             '设备侧、网关、MES 服务器统一 NTP 授时，时间偏差不超过 1 秒。',
             '边缘网关须具备断网缓存能力（不少于 24 小时），恢复后按时间顺序补传；MES 侧同样会缓存后补传。'])

    d.h('五、点表模板与字段规则')
    d.p('点表为 Excel 文件，包含“设备”“点表”“协议”“说明”四张工作表；表头和列顺序固定，与 MES 提供的《点表模板》一致（随本规范发布）。')
    d.h('5.1 设备表', 2)
    d.table([['列', '必填', '说明']] + [[c, '是' if c in ('设备编码', '设备名称', '设备类型', '车间', '产线', 'X(m)', 'Y(m)', '宽(m)', '深(m)', '朝向(°)') else '否', exp]
                                     for c, exp in DEV_EXPLAIN], [2.8, 1.2, 11.8])
    d.h('5.2 点表', 2)
    d.table([['列', '必填', '说明']] + [[c, req, exp] for c, req, exp in PT_EXPLAIN], [2.8, 1.2, 11.8])
    d.h('5.3 用途', 2)
    d.table([['用途', '含义', '示例']] + [list(r) for r in ROLE_ROWS], [2.4, 3.2, 10.2])
    d.h('5.4 数据类型与取值约定', 2)
    d.items(['数据类型按工程值填写：bool、int、float、string。',
             '运行状态（state）优先用文字：Executing 运行、Idle 待机、Setup 换型、Stopped 停机、Planned 计划停机、Starved 缺料、Blocked 堵料；用整数状态码时按附录 A，并在备注中写“状态码：0=Idle、1=Executing……”。',
             '报警（alarm）用文字表示当前报警内容（无报警为空）；用开关量或报警代码时，在备注中写明代码表。',
             '计数（count）为累计值，只增不减，复位时在备注中写明复位条件；产量计数和刀具寿命计数分开设点。',
             '正常范围写成“下限~上限”，如 0~85；逐点核对时超出正常范围的点标为橙色。'])
    d.h('5.5 校验规则', 2)
    d.p('MES 导入点表时自动校验，错误的行不导入，提醒的行导入后进入待确认：')
    d.table([['规则', '级别'], ['设备编码、点位编码重复', '错误'], ['点表中的设备编码在设备表和 MES 台账中都不存在', '错误'],
             ['地址格式不符合协议要求（OPC UA NodeId、Modbus 从站:表:地址、Sparkplug 组/节点/设备:指标）', '错误'], ['数据类型、读写、用途取值不在规定范围内', '错误'],
             ['同一协议下地址重复', '错误'], ['缺含义、数值点缺单位、没有填设备', '提醒'], ['正常范围写法不规范', '提醒']], [12.8, 3.0])

    d.h('六、MQTT 主题与载荷')
    d.h('6.1 主题', 2)
    d.code('<前缀>/<区域>/<设备编码>/<点位路径>\n例：hm/plant1/machining/CNC1/Monitoring/Spindle/Load')
    d.items(['前缀由实施方分配（如 hm/工厂代号/），MES 订阅“前缀#”。区域为英文小写的车间或产线代号。',
             '点位路径与点表一致，用“/”分隔；主题中不使用空格、中文和通配符字符（+ #），不以“/”开头。',
             '只在值变化时发布（report by exception），并设置 retain；周期性数值可以按采集周期发布。',
             '写入（仅限读写点）：发布到“<点位主题>/set”，载荷 {"value": 值}；网关回执发布到“<点位主题>/set/ack”。'])
    d.h('6.2 载荷', 2)
    d.code('{"value": 63.5, "unit": "%", "quality": "good", "ts": "2026-11-05T08:00:00.250+08:00"}')
    d.table([['字段', '必填', '说明'], ['value', '是', '工程值（数字、布尔、文字）'], ['ts', '是', '数据产生时刻，ISO 8601 带时区，到毫秒'],
             ['quality', '否', 'good（缺省）/ uncertain / bad'], ['unit', '否', '工程单位，与点表一致']], [2.4, 1.2, 12.2])
    d.p('个别网关只能发纯数值或纯文字载荷时也可以接收，但不带时间戳和质量，须在交付说明中写明。')
    d.h('6.3 生产事件（可选）', 2)
    d.p('有一件一码、扫码报工的产线，网关把零件事件发布到“<前缀>_mes/part”、生产日志发布到“<前缀>_mes/event”（也可以用 HTTP 接口 /api/ingest/part、/api/ingest/event）：')
    d.code('{"sn": "SGH450-2611050001", "event": "machined", "station": "CNC1", "t": "2026-11-05T08:00:01+08:00", "data": {"bore": 40.012}}')

    d.h('七、时间戳与质量')
    d.items(['所有时间戳为数据产生时刻，不是网关转发或 MES 接收时刻；带时区（+08:00）或使用 UTC（Z）。',
             '时间精度到毫秒；动作级信号（阀、到位、循环开始 / 结束、程序段号）必须用动作发生的时刻，MES 的节拍分析按它计算每个动作的用时。',
             '质量码：0 好（OPC UA Good）、1 不确定（Uncertain）、2 坏（Bad，通讯故障、传感器故障）。质量为坏的值 MES 只存不用。',
             '设备侧时钟与 NTP 偏差超过 1 秒时，网关须用自己的授时时间改写时间戳，并在交付说明中写明。'])

    d.h('八、平面图（DXF）要求')
    d.items(['格式：DXF（R12 或 2000 以上的 ASCII 格式），单位米（$INSUNITS = 6），坐标与工厂坐标一致：原点在厂房左下角，X 向右，Y 向上。',
             '图层：墙体、区域、通道、设备、输送线、AGV 路径、文字分层绘制，建议图层名 HM_WALL、HM_AREA、HM_AISLE、HM_DEVICE、HM_CONVEYOR、HM_AGV_PATH、HM_TEXT'
             '（虚拟工厂交付包使用 VF_ 前缀，同样识别）。区域用闭合多段线画在区域图层，区域名用文字写在区域内。',
             '设备：每台设备一个块参照，块名 DEV_设备编码；插入点 = 设备中心，插入角 = 设备朝向（逆时针为正）；块内画设备外形矩形和一根指向设备自身 +X 方向的短线。',
             '块属性：CODE（设备编码，必填，和点表一致）、NAME（设备名称）、TYPE（设备类型）。MES 按 CODE 自动把设备落到图上的位置，对不上的设备在接入中心拖拽落位。',
             '中文文字使用 UTF-8 或 \\U+XXXX 转义；图纸只保留与布局有关的内容，删除标注、图框和无关图层，文件一般不超过 20 MB。'])

    d.h('九、三维模型要求')
    d.items(['格式：glTF 2.0 二进制（.glb），单位米，Y 轴向上；模型原点在设备底面中心，设备自身 +X 方向与平面图块内的朝向短线一致。',
             '每类设备至少一个模型，文件放在交付包 models 目录，点表“3D模型文件”列写相对路径，如 models/cnc.glb。整厂场景 models/scene.glb 可选，节点名为设备编码。',
             '单个模型面数不超过 20 万个三角形、文件不超过 20 MB；材质用 PBR 基本色，不依赖外部贴图文件。',
             '没有提供模型的设备，MES 按设备类型模板从平面图自动生成简化三维模型。'])

    d.h('十、交付包')
    d.table([['文件', '内容', '必交'], ['points.xlsx', '设备表、点表、协议表、说明表', '是'], ['layout.dxf', '车间平面图，设备为带属性的块', '是'],
             ['models/<设备类型>.glb', '设备三维模型', '否'], ['models/scene.glb', '整厂三维场景，节点名 = 设备编码', '否'],
             ['README.md', '协议端点、命名空间、主题前缀、网关说明、特殊约定', '是']], [4.2, 9.4, 2.2])
    d.p('交付包按版本提交，文件名带日期，如 某厂-delivery-20261105.zip。设备或点位变更时提交完整的新版本交付包，MES 导入时自动比对新增、变化和取消的设备与点位，并生成新的工厂模型版本。')

    d.h('十一、联调验收')
    d.h('11.1 流程', 2)
    d.items(['集成商提交交付包，实施方在 MES“接入中心 › 概况与导入”上传，查看预览和校验结果；有错误的，集成商修改后重新提交。',
             '确认导入后，MES 按点表连接各数据源；在“逐点核对”中，读不到的点标红，点表外的地址标黄，超出正常范围的点标橙。',
             '红色的点由集成商检查地址、权限、网络；黄色的地址由双方确认是补进点表还是忽略；橙色的点核对量程和正常范围。',
             '值质量检查：数值长时间不变、和运行状态不相关、源端质量差的点逐一说明原因。',
             '全部点位确认（覆盖率 100%）后，生成《联调验收报告》，由实施方、集成商、客户三方签字。'])
    d.h('11.2 检查清单', 2)
    d.table([['序号', '检查项', '要求'], ['1', '交付包校验', '无错误；提醒项已说明'], ['2', '数据源连接', '全部数据源状态为已连接，连续 24 小时无断线'],
             ['3', '点位覆盖率', '已确认点位 / 全部点位 = 100%'], ['4', '运行状态', '每台生产设备的运行状态与现场一致（抽查运行、待机、停机三种状态）'],
             ['5', '计数与产量', '计数点与现场计数一致，抽查误差为 0'], ['6', '报警', '人工触发至少一个报警，MES 在 5 秒内记录'],
             ['7', '时间戳', '与 NTP 时间偏差不超过 1 秒；动作信号时间精度到毫秒'], ['8', '平面图与三维', '全部设备已落位，位置、朝向与现场一致'],
             ['9', '断网续传', '断开网络 10 分钟后恢复，数据补齐、无重复'], ['10', '安全', '账号专用、默认密码已修改、MES 只读访问']], [1.4, 3.6, 10.8])

    d.h('附录 A 标准状态码')
    d.table([['状态码', '状态', '中文'], ['0', 'Idle', '待机'], ['1', 'Executing', '运行'], ['2', 'Starved', '缺料'], ['3', 'Blocked', '堵料'],
             ['4', 'Stopped', '停机 / 故障'], ['5', 'Setup', '换型 / 调机'], ['6', 'Planned', '计划停机']], [2.4, 4.0, 9.4])
    d.h('附录 B 点表示例')
    d.table([['点位编码', '设备编码', '含义', '单位', '数据类型', '用途', '协议', '地址'],
             ['CNC1.Common.State', 'CNC1', '运行状态', '', 'string', 'state', 'opcua', 'ns=2;s=Line1/CNC1/Common/State'],
             ['CNC1.Monitoring.Spindle.Load', 'CNC1', '主轴负载', '%', 'float', 'load', 'opcua', 'ns=2;s=Line1/CNC1/Monitoring/Spindle/Load'],
             ['CNC1.Production.PartsProduced', 'CNC1', '累计加工件数', '件', 'int', 'count', 'mqtt', 'hm/plant1/machining/CNC1/Production/PartsProduced'],
             ['CNC1.Common.EnergyKWh', 'CNC1', '累计电能', 'kWh', 'float', 'energy', 'modbus', '11:hr:1000']], [3.6, 1.4, 2.0, 1.0, 1.4, 1.2, 1.2, 4.0], 8)
    d.h('附录 C 设备类型模板')
    d.p('点表“设备类型”列按下表填写（别名也可识别）。模板中的标准点位路径和用途见 MES“接入中心 › 工厂模型 › 设备类型模板”。')
    d.table([['编码', '设备类型', '别名', '标准点位数']] + [[t['code'], t['name'], t['aliases'].replace(',', '、'), str(len(t['points']))] for t in templates.BUILTIN],
            [2.6, 3.0, 8.6, 1.6], 8.5)
    d.save(path)
    print('saved', path)


DEV_EXPLAIN = [('设备编码', '设备唯一编码，见第二章'), ('设备名称', '台账名称，如 1#加工中心'), ('设备类型', '按附录 C 填写，决定标准点位、用途映射和默认三维模型'),
               ('型号', '设备型号；同型号设备可在 MES 中批量套用模板'), ('厂家', '制造厂家'), ('车间', '组织结构第一层'), ('产线', '组织结构第二层'),
               ('工位', '缺省 WS-设备编码'), ('X(m)', '设备中心 X 坐标（工厂坐标，米）'), ('Y(m)', '设备中心 Y 坐标'), ('宽(m)', '沿设备自身 X 方向的外形尺寸'),
               ('深(m)', '沿设备自身 Y 方向的外形尺寸'), ('朝向(°)', '绕竖直轴，逆时针为正，与 DXF 块插入角一致'), ('3D模型文件', '交付包内 GLB 的相对路径'),
               ('理想节拍(s)', '设计循环时间，用于计算性能开动率'), ('备注', '其它说明')]
PT_EXPLAIN = [('点位编码', '是', '全厂唯一，见第二章'), ('设备编码', '是', '设备表中的编码'), ('含义', '是', '中文含义'), ('单位', '数值点必填', '工程单位'),
              ('数据类型', '是', 'bool / int / float / string'), ('读写', '是', 'R 只读 / RW 可写'), ('用途', '是', '见 5.3'),
              ('协议', '是', 'opcua / mqtt / sparkplug / modbus / http / sql'), ('地址', '是', '各协议的完整地址（第三章）；HTTP 推送可空'),
              ('缩放', '否', '工程值 = 原始值 × 缩放 + 偏移，缺省 1'), ('偏移', '否', '缺省 0'), ('采集周期(ms)', '否', '建议采集周期；0 表示变化即推送'),
              ('报警下限', '否', '低于即报警'), ('报警上限', '否', '高于即报警'), ('正常范围', '否', '下限~上限，如 0~85'),
              ('备注', '否', 'Modbus 编码与字序、状态码对照、原 PLC 地址等')]
ROLE_ROWS = [('state', '运行状态', 'Common/State'), ('alarm', '报警', 'Common/Alarm'), ('count', '计数', '累计加工件数、合格数、循环计数、刀具已用件数'),
             ('power', '功率', '实时功率 kW'), ('energy', '电能', '累计电能 kWh'), ('load', '负载 / 电流', '主轴负载、电机电流'), ('temp', '温度', '铁水温度、模温'),
             ('quality', '质量值', '在线测量值、检测结论'), ('action', '动作信号', '电磁阀、到位传感器、循环进行中、上次动作用时'), ('step', '程序段号', 'NC 程序段、机器人程序步'),
             ('param', '设定参数', '倍率、节拍设定、刀具寿命设定'), ('other', '其它', '不属于以上类别')]


if __name__ == '__main__':
    out = os.path.join(HERE, '蜂元智能MES数据接入规范.docx')
    build(out)
    from honeymom.factory import pointtable, templates
    with open(os.path.join(HERE, '点表模板.xlsx'), 'wb') as f:
        f.write(pointtable.template_xlsx([t['name'] for t in templates.BUILTIN]))
    print('saved', os.path.join(HERE, '点表模板.xlsx'))
