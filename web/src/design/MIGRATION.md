# 页面迁移指南：换上新设计系统和 AI 选择

老页面放进新外壳后已经能用（`.page` `.page-head` `.card` `.kpi` `.grid g2/g3/g4/g6` `.tag-st st-*` `.muted` `.mono` 都已换成新外观）。按下面的顺序逐页改。

## 1. 页面骨架

```vue
<div class="page">
  <div class="page-head"><h2>设备实时监控</h2><span class="sub">数据时钟 {{ now }}</span><span class="grow" /> …操作按钮… </div>
  <HmToolbar>…筛选…<template #right>…</template></HmToolbar>
  <div class="grid g4"> <HmKpi … /> … </div>
  <HmCard title="…">…</HmCard>
</div>
```

- 页面头部用 `<HmPageHead title sub>`（自带「?」操作导览；概览页可加 `art`），在 `web/src/tour/guides.ts` 给页面写一个导览（`routes` 写页面路径）。
- 外壳已经有面包屑和 `Ctrl+K`，页面不要再自己做返回/搜索入口。
- 外壳给主区 `.hm-main`（有细网格背景、自己滚动）。页面不要设 `height: 100vh`；要占满高度用 `calc(100vh - 140px)` 一类（顶栏 52 + 面包屑 34 + 状态条 26 + 边距）。
- 颜色、字号、间距只用 `var(--hm-*)`（见 README）；删掉写死的 `#d99a16`、`#1e2329`、`Consolas` 等。
- KPI：`<div class="kpi">…</div>` 换成 `<HmKpi>`；数字格式用 `format.ts`。
- 状态：`<span class="tag-st st-running">` 可以继续用，新代码用 `<HmStatus :status="e.status" />`。
- 空列表：`<HmEmpty>`（一页只给最主要的空区域放插画，其余用 `plain`）；加载中：`<HmSkeleton>`（不要整页转圈）。
- 编号 + 名称拼在一起显示时用 `codeName(code, name)`（`format.ts`），名称里已经带编号的不会重复成"AGV AGV 车队"。

## 2. 让数据可以交给 AI（v-ai）

**任何**用户可能想问的东西都声明 AI 上下文：KPI、图表数据点、表格行、设备卡、单据、字段、报警/提醒。

```vue
<div class="card eq" v-ai="{ kind: 'device', entity: 'equipment', id: e.id, label: `${e.code} ${e.name}`, data: { 状态: '停机', 功率kW: 12.3 } }">…</div>
<span v-ai="{ kind: 'field', label: `出铁温度 ${heat.tap_temp} °C`, data: { 值: heat.tap_temp, 标准: '1480–1500' } }">{{ heat.tap_temp }}</span>
```

字段（`AiItem`）：

| 字段 | 说明 |
|---|---|
| `kind` | `kpi` `chart-point` `row` `device` `record` `field` `alert` `text`（选中的文字，框架自动生成）`page`，也可以自定义 |
| `label` | 显示在上下文标签上的一句话，**带上数值**，如 "废品率 2.31%" |
| `id` / `entity` | 对应 MES 实体时给上（`work_order` `equipment` `ncr` …）：右键有"打开记录"，助手会读整条记录 |
| `data` | 给 AI 的结构化数据（中文键名即可），AI 的回答以这里的数字为准 |
| `sn` / `heat` | 零件码 / 炉次号：右键"追溯"直接打开追溯页 |
| `link` | "打开记录"跳转的地址（默认 `/e/<entity>?id=<id>`） |

不用指令也行：`data-ai-kind="device" data-ai-id="12" data-ai-label="CNC3" data-ai-entity="equipment"`，或 `data-ai='{"kind":"alert","label":"…"}'`。

**el-table 的行**用 `v-ai-rows`（放在 `<el-table>` 上）：

```vue
<el-table :data="rows" v-ai-rows="{ entity: 'ncr', rows: () => rows, label: (r) => `不合格品 ${r.code}`, data: (r) => ({ 缺陷: r.defect__label, 数量: r.qty }) }">
<!-- 或完全自定义： v-ai-rows="{ rows: () => rows, item: (r, i) => ({ kind: 'row', … }) }" -->
```

`HmKpi` 和 `HmDataTable` 自带 v-ai。**图表数据点**：在 ECharts 的 `click`/`contextmenu` 事件里调 `aiContext.add({ kind: 'chart-point', label: `${系列} ${x} = ${y}`, data: {...} })` 或 `askAi(…)`。

用户能做的（框架统一处理，页面不用写）：

- **Ctrl+点击**（Mac ⌘+点击）：加入/移出 AI 上下文，元素出现蓝色"已选给 AI"环，右侧 AI 面板出现可删除的标签；键盘 `Ctrl+Enter`。
- **右键**：问 AI… / 解释这个数 / 为什么变化 / 追溯 / 生成报告 / 加入对比 / 打开记录 / 复制 + 这类元素自己的动作；键盘 `Shift+F10` 或菜单键；`Shift+右键` 仍是浏览器菜单。
- **浮动工具条**：选中一段文字、或鼠标停在 `v-ai-rows`/`HmDataTable` 的行上（键盘聚焦行也行）出现：问 AI / 解释 / 追溯 / 复制 / ⋯。
- AI 关闭时：追溯、复制、打开记录照常；AI 动作置灰，点了提示"AI 已关闭（系统设置可开启）"。

## 3. 页面专属动作

```ts
import { onUnmounted } from 'vue';
import { registerAiActions, askAi } from '../ai';

const off = registerAiActions('device', [
  { id: 'repair', label: '报修', icon: 'SetUp', order: 110, run: (it) => router.push(`/e/repair_order?equipment=${it.id}`) },
  { id: 'diagnose', label: 'AI 诊断停机', icon: 'MagicStick', ai: true, feature: 'watch', order: 25, toolbar: true,
    run: (it, sel) => askAi(`诊断 ${it.label} 最近的停机原因`, sel, { action: 'diagnose', feature: 'watch' }) },
]);
onUnmounted(off);
```

- `ai: true` 的动作在 AI 关闭时自动置灰并提示；`feature` 指定需要哪个 AI 功能（默认 `ask`）。
- `when(item)` 决定显不显示，`disabled(item)` 返回原因字符串表示不可用。
- `order`：内置 10–80（AI 动作 10–60，追溯 40、打开 70、复制 80）；页面动作用 ≥ 100 会排在后面一组。`toolbar: true` 会出现在浮动工具条（最多 4 个）。
- `kind` 写 `'*'` 对所有元素生效（少用）。

## 4. 把任务交给 AI

```ts
import { askAi, aiOn, useAiContext, aiBus } from '../ai';

askAi('这台设备最近 8 小时为什么停机这么多？', { kind: 'device', entity: 'equipment', id: 12, label: 'CNC3' }, { action: 'diagnose' });
// AI 关闭时返回 false 并提示；opts.send=false 只填进输入框；opts.feature 指定功能
if (aiOn('daily')) { …显示"生成日报"按钮… }       // 按钮：功能关闭时隐藏或置灰
const ctx = useAiContext();   // ctx.items / add / remove / toggle / clear / has
aiBus.on('context-change', (items) => …);   // 还有 ask / action / dock / focus-input
```

旧的 `askAssistant(prompt, record)`（api.ts）仍然可用，等同于 askAi，AI 关闭时会提示。

后端：`POST /api/assistant/chat` 的 body 多了 `context: [{ kind, id, label, data, entity, sn }]` 和 `action`；助手把上下文写进系统提示，并用来核对回答里的数字。

## 5. AI 功能开关

- 功能 key：`ask` 问数助手（含动作草稿）、`daily` AI 日报（只管 AI 解读，日报本身 AI 关闭也能看）、`watch` AI 值守、`automation` 一句话自动化 / 智能体自动运行、`tool_wear` 刀具寿命估计、`charge_opt` 配料优化、`kb` 知识库问答、`agents` 智能体编排、`backtrace` 缺陷回溯 AI 解读、`coplan` 协同排产 AI 建议。默认全部关闭。
- 路由需要某个功能：在 `router.ts` 里给路由加 `meta: { feature: 'daily' }`，关闭时外壳显示"已关闭"页（不加载页面，不会去调接口）。
- 菜单项需要某个功能：`layout/nav.ts` 里给项加 `feature`，关闭时置灰并带锁。
- 页面里的 AI 按钮：`v-if="aiOn('watch')"`（或 `:disabled="!aiOn('watch')"` + 提示 `aiOffText('watch')`）。不要在 AI 关闭时去调 AI 接口——它们会返回 403 `{"code": "ai_disabled"}`，浏览器控制台会有报错。
- 后端新接口：在 `honeymom/services/ai_switch.py` 的 `ROUTES` 里登记前缀（中间件统一返回"已关闭"），或在接口里调 `ai_switch.require('kb')`。功能是否开启用 `ai_switch.enabled('tool_wear')`。
- 演示模式：AI 接口用 `ai_demo.run(feature, op, payload, live=…, fallback=…)` 包一层（SSE 用 `ai_demo.run_stream`），就能录制/回放；手工加录制：`POST /api/ai/demo/recordings {feature, op, request, response}`。录制文件在 `honeymom/fixtures/ai_demo/<feature>.json`。

## 6. 检查清单（每页）

- [ ] 没有写死的颜色/字体；数字 `.hm-num`、单位 `.hm-unit`、格式化用 `format.ts`
- [ ] KPI → `HmKpi`；状态 → `HmStatus`；空/加载 → `HmEmpty`/`HmSkeleton`
- [ ] 每个指标、表格行、设备、单据、报警都有 `v-ai` / `v-ai-rows`，label 带数值
- [ ] AI 按钮按功能开关隐藏/置灰；AI 关闭时页面无报错
- [ ] 1280×720 / 1680×1050 / 2560×1440 不出横向滚动、文字不重叠不截断、文字对比度 ≥ 3、控制台无报错（`node tests/pages_ui.js`）；1024 宽（菜单收起、AI 面板浮层）可用
- [ ] 页面头部用 `HmPageHead`（有「?」导览入口），`guides.ts` 里有这一页的导览
- [ ] 图表用 `components/Chart.vue`（主题自动套用），不写死颜色；给 `ai="图表名"` 让数据点能交给 AI
- [ ] 键盘能走到主要操作（`Tab`、`Shift+F10`）

## 7. AI 体验（4.7）：结构化回答、动作草稿、自动化、值守、日报

后端总入口 `honeymom/services/ai_plus.py`（各模块文件头有完整说明）；前端组件都在 `web/src/ai/`。

- **结构化回答（块协议）**：AI 回答 = Markdown 文字 + 块（`chart` `table` `kpis` `refs` `sources` `callout` `draft`），协议见 `services/ai_blocks.py` / `ai/blocks.ts`。
  模型在回答末尾给 ```` ```blocks ```` JSON；后端补上数据来源（用过的查询）和单据引用（文字里的 `[[work_order:WO…]]` 和自动编号），核对块里的数字（对不上标"未核实"）。
  渲染：`<AiBlocks :blocks="…" :refs="…" />`；图表/表格/指标卡上有"钉到工作台"（`/api/ai/pins`；工作台的"钉住的 AI 结果"组件用 `<PinnedBlocks hide-empty />`，有钉住的块且 AI 打开时才出现，可排序 / 隐藏）。页面上的"AI 解读"卡片用 `<AiExplain feature="…" url="…" :body="…" />`。
- **动作草稿**：AI 提议的写操作（建维修单 / NCR / 安灯、调整排程、调整参数…）先变成草稿卡 `<DraftCard :draft="d" />`，显示改动前后对比；通过 / 改一下再通过 / 驳回，没通过不执行。
  存在审批中心同一张表（kind=`ai_draft`），审批中心也能处理；全程写 audit_log。后端 `services/ai_drafts.py`（`POST /api/ai/drafts`、`/suggest`、`/{id}/decide`）。
  页面里让 AI 按某个对象起草：`suggestDraft('repair_order', item)`（`ai/actions.ts`）；全局右键已有：AI 派维修、AI 发起安灯、AI 调整排程（工单行）、AI 调整刀具寿命（刀具行）、设为自动化提醒。
- **一句话自动化**（`automation`）：`/ai/automations` 页；一句话 → 流程（触发 → 条件 → 动作）→ 历史回放 → 启用 / 暂停 / 删除。写操作只会起草成草稿。`services/ai_automation.py`。
- **AI 值守**（`watch`）：后台观察者每分钟看质量 / 设备 / 排程 / 物料，发现带证据、严重程度、建议动作；去重、可确认。卡片组件 `<WatchFeed />`（`compact`、`limit`），页面 `/ai/watch`；
  已注册进工作台信息流（home_feed provider `ai_watch`）。`services/ai_watch.py`。
- **日报**：`/ai/daily` 杂志式版面（头条、8 个 KPI 带环比、产出 / OEE / 帕累托 / 停机 / 能耗图、亮点与风险、建议动作），导出 PDF（服务端无头 Chrome / Edge），定时生成。
  AI 关闭时同一份报告用规则写亮点和风险。`services/ai_daily.py`。
- **演示录制**：所有 AI 调用都走 `ai_demo`（右侧助手、解释 / 为什么、日报解读、自动化解析、值守解读、草稿润色、8D 起草、缺陷回溯 / 协同排产解读、智能体的大模型节点）。录制在 `honeymom/fixtures/ai_demo/`。
