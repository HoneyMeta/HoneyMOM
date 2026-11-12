"""工厂模型与接入（M2）。

model.py      工厂模型：层级、设备 / 采集点写入、模板套用、版本快照与比对、采集映射（点位 → 用途）
templates.py  设备类型模板（内置 + 用户 / 行业包）
pointtable.py 点表 Excel：读（含表头对列）、校验、模板导出
package.py    交付包（zip：点表 + DXF + 3D）导入
layout.py     DXF 平面图解析、设备按编码自动落位、2D / 3D 孪生数据
live.py       逐点核对（红 / 黄 / 橙）、值质量检查、覆盖率、待确认队列
sparkplug.py  Sparkplug B 载荷编解码
assist.py     AI 辅助：对列、点位含义 / 单位 / 设备 / 用途草稿、可疑点、读 PLC 程序
report.py     联调验收报告（Word / PDF）
api.py        接口（/api/factory/…、/api/onboard/…）
说明：docs/dev/factory-model.md
"""
