# 蜂元智能 MOM 设计系统（浅色 · 科技）

精确、现代的工业系统，不花哨。白和极浅灰的表面，半透明"玻璃"面板，钴蓝主色 + 青色数据色，蜂元琥珀色只做点缀；AI 相关的东西统一用"青 → 蓝 → 紫"细渐变标识。

文件：

| 文件 | 内容 |
|---|---|
| `tokens.css` | 全部设计令牌（CSS 变量 `--hm-*`），以及 Element Plus 主色/圆角覆盖、旧变量（`--card` `--line` …）映射 |
| `base.css` | 全局基线、通用类（`.hm-*`）、老页面类（`.page` `.card` `.kpi` `.tag-st` …）的新外观、表格、AI 选中环、动效 |
| `format.ts` | 数字格式化：`fmtNum` `fmtPct` `fmtUnit` `fmtCompact` `fmtDur` `fmtTime` `fmtDelta` `fmtBy`；编号 + 名称：`codeName` `bareName`（名称里已带编号时不重复，如 AGV / "AGV 车队"） |
| `motion.ts` | `useTickUp`（数字跳动）、`reducedMotion()` |
| `components/` | `HmPageHead` `HmKpi` `HmNumber` `HmStatus` `HmCard` `HmEmpty` `HmToolbar` `HmTag` `HmSkeleton` `HmDataTable` `HmAutoScroll`（全局注册，页面里直接用） |
| `chart.ts` | ECharts 主题 `hm`、`PALETTE`、语义色 `CH`、`targetLine`（见下文"图表"） |
| `index.ts` | `installDesign`：注册组件和 `v-ai` / `v-ai-rows` 指令 |
| `MIGRATION.md` | 老页面怎么换上新样式和 AI 选择 |

## 令牌

**颜色**（只用变量，不写死色值）

- 表面：`--hm-canvas`（主画布，配 `.hm-grid-bg` 细网格）、`--hm-surface`（卡片/表格）、`--hm-surface-2`（表头/工具条）、`--hm-surface-3`（悬停）；玻璃 `--hm-glass` / `--hm-glass-strong` + `--hm-glass-blur`（或直接用 `.hm-glass`）
- 线：`--hm-border`、`--hm-border-strong`、`--hm-hairline`
- 文字：`--hm-text`（主）、`--hm-text-2`（次）、`--hm-text-3`（说明、单位）
- 主色：`--hm-primary`（钴蓝，按钮/链接/选中）、`--hm-primary-soft`；数据色 `--hm-cyan`（线条、图形；青色上的文字或白字底色用 `--hm-cyan-ink`）；品牌 `--hm-amber`（#d9822b，头像、计数徽标、少量点缀）
- AI：`--hm-ai-grad`（渐变）、`--hm-ai-soft`、`--hm-ai-ring`（"已选给 AI"的环）
- 状态：运行 `--hm-run`、待机 `--hm-idle`、故障 `--hm-fault`、离线 `--hm-offline`、换型 `--hm-setup`，各有 `-soft`（底色）和 `-ink`（文字）
- 对比度：文字和背景至少 3:1（`tests/pages_ui.js` 会查）。白字放在色块上用 `-ink` 做底（`--hm-idle`、`--hm-cyan` 上的白字都不够）；`--hm-text-3` 只放在白底 / `--hm-surface` 上，彩色浅底（`-soft`、渐变）上的说明文字用 `--hm-text-2`。

**字体**：正文 `--hm-font`；数字 `--hm-font-num` + `font-variant-numeric: tabular-nums`（类 `.hm-num`）；代码/编号 `--hm-font-mono`（类 `.hm-mono`）。
字号 `--hm-fs-2xs`(11) `xs`(12) `sm`(13，正文) `md`(14，卡片标题) `lg`(16) `xl`(20，页面标题) `2xl`(26) `3xl`(34，KPI 大数)。

**间距** `--hm-sp-1..8`（4 的倍数）；**圆角** `--hm-r-xs/sm/md/lg/pill`（卡片 12，按钮/输入 6）；**层次** `--hm-e1`（卡片）`--hm-e2`（悬停）`--hm-e3`（弹出层、AI 面板浮层）；**焦点环** `--hm-focus`（全局 `:focus-visible` 已用）。

**动效**：`--hm-dur-1/2/3`（120/200/320ms）+ `--hm-ease`。数字跳动 `HmNumber`/`useTickUp`；状态脉冲 `.hm-pulse`（运行、故障的圆点）；面板滑入 `<Transition name="hm-slide-right">`、弹出 `hm-fade`；入场 `.hm-enter`。
系统设置"减少动效"时全部关闭（tokens.css 和 base.css 里的 `prefers-reduced-motion`）。

## 组件

```vue
<HmKpi label="平均 OEE" :value="0.724" format="pct" :delta="0.031" delta-pct hint="近 8 小时" :spark="[0.6,0.7,0.72]" to="/equipment/oee" />
<HmKpi label="今日用电" :value="12345" unit="kWh" format="int" status="warn" :ai="{ data: { 吨耗: 612 } }" />
<HmStatus status="running" />            <!-- 运行（绿点脉冲） --> <HmStatus status="down" variant="dot" />
<HmCard title="设备状态" sub="实时"> <template #actions><el-button link>全部 ›</el-button></template> … </HmCard>
<HmDataTable :columns="[{ key: 'code', label: '设备' }, { key: 'oee', label: 'OEE', num: true, format: 'pct' }]" :rows="rows" entity="equipment" max-height="420" />
<HmEmpty icon="search" title="没有找到" desc="换个条件试试"><el-button>清空筛选</el-button></HmEmpty>
<HmEmpty plain title="还没有消息" />   <!-- 只有一行字、不放插画 -->
<HmToolbar> <el-input … /> <template #right><el-button type="primary">新增</el-button></template> </HmToolbar>
<HmTag tone="ok">合格</HmTag> <HmTag tone="ai" closable @close="…">CNC3</HmTag>
<HmSkeleton variant="kpi" /> <HmSkeleton variant="table" :rows="6" />
<HmNumber :value="1234.5" unit="kg" />
```

- `HmKpi` 默认带 `v-ai`（kind=kpi），Ctrl+点击/右键就能交给 AI；`:ai="false"` 关掉，`:ai="{ id, entity, data }"` 补充上下文。
- `HmDataTable`：表头吸顶、点表头排序、数字列右对齐等宽；每行自动 `v-ai`（kind=row），`:ai="(row) => ({…})"` 自定义。大表格/分页仍用 `el-table` + `v-ai-rows`。
- 格式化一律用 `format.ts`，不要手写 `toFixed`。
- `HmEmpty`：一页里只给最主要的一个空区域放插画，其余空区域用 `plain`（或 `HmDataTable` 的 `empty-plain`），别让同一张插画在一页出现好几次。

- `HmAutoScroll`：内容超出高度时匀速向上滚动、首尾无缝衔接（车间大屏的长表格、滚动公告），鼠标悬停暂停；`:speed`（像素/秒，默认 24）、`height`、`:paused`；系统"减少动效"时退回普通滚动条。
  ```vue
  <div class="th">…表头…</div>
  <HmAutoScroll :speed="22" style="flex:1;min-height:0"> <div v-for="r in rows" class="tr">…</div> </HmAutoScroll>
  ```

工作台 / 车间大屏专用组件在 `web/src/components/home/`（不全局注册）：`FactoryMap`（虚拟工厂平面布局 + 实时状态，悬停卡片、点击打开设备、Ctrl+点击/右键走 AI，没有布局时退回状态网格）、
`FeedList`（提醒与 AI 值守信息流，接口与 provider 见 `honeymom/services/home_feed.py`）、`OrderTimeline`（工单迷你甘特）、`AndonStrip`（安灯条）、`usePoll`（轮询，页面隐藏时暂停）。都有 `big` 属性给大屏用。

AI 结果组件在 `web/src/ai/`：`AiBlocks`（AI 回答里的图表 / 表格 / 指标卡 / 来源块，可"钉到工作台"）、`PinnedBlocks`（钉住的块；工作台的"钉住的 AI 结果"组件，只在有钉住的块且 AI 打开时出现）、
`WatchFeed`（AI 值守发现卡片，工作台信息流里 kind=ai 的条目也用它渲染）、`DraftCard`（动作草稿：通过才执行）。

## 原则

1. 工业要严谨：数字等宽、右对齐、带单位；状态用固定的五种颜色；不要装饰性的渐变和插画抢数据的位置。
2. 找得到：每页顶上是面包屑；全局 `Ctrl+K`（页面、单号、命令、交给 AI）；左侧菜单 `/` 筛选。
3. AI 处处可用但不打扰：任何数据都能 Ctrl+点击/右键交给 AI，AI 面板随时收成一条细边，主区重排不被遮挡；AI 关闭时非 AI 功能照常。
4. 只有浅色主题。

## 密度（大屏）

`--hm-scale` 按视口宽度取 1 / 1.125 / 1.25（< 1800px / 1800–2299px / ≥ 2300px，见 `tokens.css` 末尾）。字号 `--hm-fs-*`、间距 `--hm-sp-*`、栅格间距 `--hm-gap`、外壳尺寸（顶栏、菜单宽、状态条）、Element Plus 控件高度和字号都乘这个系数，页面最大宽度也跟着放大。
页面只要用令牌就自动适配；写死的 px 不会变。需要按比例的尺寸写 `calc(200px * var(--hm-scale))`。设备卡、看板卡这类"越大屏越多列"的用 `.grid.g-auto`（`style="--min: 248px"` 改最小列宽）。

## 图表（`chart.ts`）

`components/Chart.vue` 自动套用主题 `hm`（随 `--hm-scale` 放大文字）：配色 `PALETTE`（钴蓝、青、琥珀、紫……），坐标轴、网格线、提示框、图例统一；有坐标轴的图自动 `containLabel`。
环形图默认标签"名称 + 百分比"在外侧对齐成两列，外半径限制在 60% 以内不截断；切片多于 6 个改成右侧图例。语义色用 `CH.ok / warn / bad / ref`，参考线用 `targetLine('目标 85%')`。
`<Chart :option ai="图表名" />`：鼠标停在哪个数据点，Ctrl+点击 / 右键就把那个点交给 AI（折线图停在哪一格就是那一格所有系列的值）；也可以传函数自定义 AiItem。

## 页面头部 `HmPageHead`

```vue
<HmPageHead title="质量概览" sub="近 7 天…" art="quality"> …操作按钮… <template #meta><HmTag>共 3 条</HmTag></template></HmPageHead>
```
标题旁自带「?」操作导览（本页导览、三个任务带做、欢迎导览）。`art` 是模块插图（equipment / quality / production / material / foundry / energy），只给概览类页面用；数据密集的页面不要加。

## 插图（`public/art/`）

清单和生成提示词在 `public/art/manifest.json`：登录页 `login-hero`、空状态 `empty-data / search / permission / ai-off / offline`（`HmEmpty icon="data|search|lock|ai|offline"`）、模块头图 `head-*`、大屏背景 `screen-bg.webp`（深色原图；浅色大屏里反相、只取下半部分、低透明度叠成纹理，高对比度模式不显示）。风格：等距线描、炭灰描边、白/冷灰填充、钴蓝 + 青色数据层、琥珀点缀、无文字。

## 操作导览（`web/src/tour/`）

`guides.ts` 写导览内容（模块导览按 `routes` 对应页面；`task-*` 是任务带做；`welcome` 首次登录自动放）。步骤目标用 `data-tour="名"`（找不到时按数组里后面的 CSS 选择器兜底）。
入口：页面标题旁的「?」（`HmPageHead` 自带；没用它的页面外壳在面包屑那一行放一个）、Ctrl+K 输入"导览"。引擎 `engine.ts` 从虚拟工厂的导览引擎移植：聚光、步骤卡（上一步 / 下一步 / 跳过 / 退出）、跨页面、刷新后接着这一步、退出后下次问"从第 N 步继续"。

