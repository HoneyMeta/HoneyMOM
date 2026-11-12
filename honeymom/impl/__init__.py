"""5.0 实施工具与可复制（M3）：通用导入框架、行业模板、自定义字段、表单配置、审批流程、报表设计器、实施向导。

模块：
  importer.py    通用导入框架（模板生成、解析、对列、校验、预览、确认、错误报告、导入记录）—— docs/dev/import.md
  importers.py   上线前要导的全部主数据的导入器
  uom.py         计量单位与换算
  cf.py          自定义字段（值存实体表的 ext 列）—— docs/dev/config.md
  forms.py       表单配置（顺序、分组、按角色必填 / 隐藏）
  flows.py       审批流程（挂在实体动作上，接审批中心）
  reports.py     报表设计器（数据集、查询、导出、定时、钉到工作台）
  packs.py       行业模板（铸造 / 机加 / 注塑 / 装配）与开局向导
  api.py         REST 接口
install(app) 在 app.build_app() 里调用一次。
"""


def install(app):
    from . import cf, flows, forms, packs
    cf.install()
    forms.install()
    flows.install()
    packs.install()
    from . import api
    app.include_router(api.router)
    api.on_startup(app)
