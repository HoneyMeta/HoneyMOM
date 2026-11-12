# 蜂元智能 MOM（HoneyMOM）· MCP 接入说明

MCP（Model Context Protocol）让你们自己的 AI 工具（Claude Desktop、Cursor、Dify 等）直接查询 MES 和设备数据。这个接口默认只读，规则如下：

- 每个 API 密钥有自己的权限点和数据范围（工厂 / 车间 / 产线），和登录用户用同一套规则。
- 每次调用都记审计日志，按密钥限流。
- 写操作只能"起草"，草稿进审批中心，审批通过后才执行。

## 1. 开启

1. 「系统 › AI 功能」：打开 AI 总开关和「MCP 数据接口」。
2. 「系统 › MCP 接入」：新建密钥（明文只显示一次），可以选：
   - 权限预设：全部只读 / 设备与节拍 / 质量 / 只读 + 起草
   - 产线范围、到期时间、IP 白名单

   已有的密钥在「系统 › API 密钥」里管理、吊销。密钥要有 `mcp.connect` 权限。

## 2. 连接方式

| 方式 | 地址 / 命令 | 鉴权 |
|---|---|---|
| Streamable HTTP（远程） | `POST http://<服务器>:8900/mcp` | 请求头 `X-API-Key: hmk_…` 或 `Authorization: Bearer hmk_…` |
| stdio（服务器本机） | `python -m honeymom mcp --key hmk_…`（或环境变量 `HONEYMOM_API_KEY`，旧名 `HONEYMES_API_KEY` 仍可用） | 同一个密钥 |

协议版本支持 2025-11-25、2025-06-18、2025-03-26、2024-11-05（握手时协商）。服务器只返回 `application/json`，`GET /mcp` 返回 405（不开服务端推送流）。

### Claude Desktop（`claude_desktop_config.json`）

```json
{ "mcpServers": { "honeymom": {
  "command": "python", "args": ["-m", "honeymom", "mcp"], "cwd": "D:\\HoneyMOM",
  "env": { "HONEYMOM_API_KEY": "hmk_…", "MOM_DATA": "D:\\HoneyMOM\\data" } } } }
```

用 PostgreSQL 时在 `env` 里再加 `MOM_DB`（旧名 `MES_DB` 仍可用）。远程连接用 Claude 的"自定义连接器"，填上面的 HTTP 地址和请求头。

### Cursor（`.cursor/mcp.json`）

```json
{ "mcpServers": { "honeymom": { "url": "http://服务器:8900/mcp", "headers": { "X-API-Key": "hmk_…" } } } }
```

### Dify

「工具 › MCP › 添加 MCP 服务（HTTP）」：
- 地址：`http://服务器:8900/mcp`
- 请求头：`X-API-Key: hmk_…`

### 通用（curl）

```bash
curl -s http://服务器:8900/mcp -H "X-API-Key: hmk_…" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"cycle_analysis","arguments":{"period":"yesterday"}}}'
```

## 3. 工具（tools/list 只列出这个密钥有权限的）

| 工具 | 用途 |
|---|---|
| `search` / `get_record` | 按关键词找设备、工单、物料、不合格品……；读一条记录的全部字段 |
| `query_work_orders` / `query_production` / `query_quality` / `query_oee` / `query_equipment` / `query_maintenance` / `query_energy` | 工单进度、产量、缺陷帕累托、OEE 与损失、设备履历、逾期保养、能耗异常 |
| `factory_model` / `list_points` | 工厂 → 车间 → 产线 → 设备的层级；采集点（含义、单位、用途、最新值） |
| `ts_range` / `ts_rollup` / `ts_latest` / `ts_events` | 设备时序：原始值、1 分钟 / 1 小时汇总、最新值、开关量上升 / 下降沿和状态区间 |
| `cycle_analysis` | 动作级节拍：瓶颈工位 → 瓶颈动作，和标准时间、基线比，按天漂移，损失折算（秒 → 件 → 元） |
| `trace` | 追溯：零件码 → 全过程；批次 → 零件去向 |
| `run_skill`、`skill_<id>` | 运行已保存的技能（确定的查询计划，不调用大模型） |
| `query_sql` / `kb_search` / `day_kpi` / `mes_query` | 只读 SQL（按权限和数据范围）、知识库检索、当天指标、助手的业务查询 |
| `draft_action`（默认关闭） | 起草动作草稿（维修单、不合格品、安灯……），审批后才执行。「MCP 接入 › 设置」里打开，密钥还要有 `mcp.write` |

时间参数 `period`：`today` / `yesterday` / `shift` / `last_shift` / `8h` / `3d`；也可以用 `day=YYYY-MM-DD`，或者 `start` / `end`。

资源：

| 地址 | 内容 |
|---|---|
| `mes://factory-model` | 工厂模型 |
| `mes://data-catalog` | 数据目录：表、字段、点位含义和单位 |
| `mes://kpi-definitions` | 指标口径 |
| `mes://skills` | 技能清单 |
| `mes://equipment/{code}/points` | 资源模板：一台设备的采集点 |

提示词：`bottleneck`（找瓶颈动作）、`shift_review`（班次复盘）、`equipment_health`（设备体检）。

## 4. 安全

- 权限 / 数据范围：和登录用户同一套。范围外的设备、记录、时序、SQL 结果都看不到；没有权限的工具不出现在列表里。
- 审计：「系统 › 审计日志」里搜 `mcp:`，每条记录有调用者（`apikey:名称`）、工具、参数、耗时、成功没有。「MCP 接入」页面下方是最近的调用。
- 限流：每个密钥每分钟最多调用次数由系统参数 `mcp_rate_per_min` 设定（默认 120）。超出返回 HTTP 429。
- 浏览器发来的请求校验 `Origin`，防 DNS 重绑定。允许的来源在系统参数 `mcp_allowed_origins` 里配。

## 5. 示例提问（在你们的 AI 工具里）

- "机加区昨天的瓶颈工位和瓶颈动作是什么？一年损失多少钱？"
- "CNC1 的夹具气缸伸出时间最近 7 天有没有变慢？可能是什么原因？"
- "刚结束的班次产量、废品率和 OEE 最低的 3 台设备。"
- "零件 H2611050012 的全过程追溯，加工时机床有没有报警？"
- "运行技能「班末产量小结」，时间段 last_shift。"
