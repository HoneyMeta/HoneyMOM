// Pinned to zh-CN (UI language + X-Lang): the assertions use the Chinese UI texts; tests/i18n_ui.js covers English.
// AI 体验（4.7）界面走查（Playwright）：演示模式下 AI 面板的结构化回答（图表 + 数据来源）、右键"解释这个数"、
// 动作草稿（右键派维修 → 通过 = 建单；驳回 = 什么都不做）、一句话自动化（解析 → 回放 → 启用）、AI 值守发现、日报（渲染 + 导出 PDF）、
// AI 全关时各入口干净地关闭；并截图。
// 用法（B 机，MES 已启动且有数据，比如先跑过 tests/e2e.py；演示录制在 honeymom/fixtures/ai_demo/）：node tests/ai_ui.js
//   环境变量 MES_URL（默认 http://127.0.0.1:8900）；截图在 data/ui_shots/ai/（SHOTS_DIR 可改）。结束时恢复原来的 AI 开关。
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(process.env.APPDATA, 'npm', 'node_modules', 'playwright'));

const BASE = (process.env.MES_URL || 'http://127.0.0.1:8900').replace(/\/$/, '');
const OUT = process.env.SHOTS_DIR || path.join(__dirname, '..', 'data', 'ui_shots', 'ai');
fs.mkdirSync(OUT, { recursive: true });
const results = [];
const ok = (name, cond, detail = '') => { results.push([name, !!cond]); console.log((cond ? 'PASS ' : 'FAIL ') + name, String(detail).slice(0, 200)); };
async function api(method, p, body, token) {
  const r = await fetch(BASE + p, { method, headers: { 'Content-Type': 'application/json', 'X-Lang': 'zh-CN', ...(token ? { Authorization: 'Bearer ' + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return r.json();
}

let PAGE = null, BROWSER = null;
(async () => {
  const tok = (await api('POST', '/api/login', { username: 'admin', password: 'honey123' })).token;
  const SW0 = await api('GET', '/api/ai/switches', null, tok);
  const feats = (on) => Object.fromEntries(Object.keys(SW0.features).map((k) => [k, on]));
  await api('PUT', '/api/ai/switches', { global: true, demo: true, record: false, features: feats(true) }, tok);
  await api('PUT', '/api/report/daily/schedule', { enabled: false }, tok);

  const browser = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 960 }, locale: 'zh-CN', acceptDownloads: true });
  await ctx.addInitScript((t) => {
    if (!sessionStorage.getItem('init')) {
      sessionStorage.setItem('init', '1'); localStorage.setItem('mes_token', t); localStorage.setItem('hm_lang', 'zh-CN'); localStorage.setItem('mes_assist_open', '0'); localStorage.removeItem('mes_assist_thread');
      localStorage.removeItem('hm_dock_w'); sessionStorage.removeItem('hm_ai_ctx');
    }
  }, tok);
  const page = await ctx.newPage();
  PAGE = page; BROWSER = browser;
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  const shot = async (name, full = false) => { await page.waitForTimeout(500); await page.screenshot({ path: path.join(OUT, name + '.png'), fullPage: full }); };
  const toast = async () => (await page.locator('.el-message').allInnerTexts().catch(() => [])).join(' | ');
  const nAns = () => page.locator('.dock .m.assistant .mdx').count();
  const waitAnswer = async (before = 0) => {
    await page.waitForFunction((n) => document.querySelectorAll('.dock .m.assistant .mdx').length > n, before, { timeout: 120000 });
    await page.waitForFunction(() => !document.querySelector('.dock .prog'), null, { timeout: 120000 });
    await page.waitForTimeout(400);
  };

  // ---------- 1. AI 面板：结构化回答 ----------
  await page.goto(BASE + '/');
  await page.waitForSelector('.hm-kpi');
  await page.click('[data-test=assistant-pill]');
  await page.waitForSelector('[data-test=assistant-input]');
  await page.fill('[data-test=assistant-input]', '最近 8 小时各设备 OEE 对比，哪台最低？');
  await page.keyboard.press('Enter');
  await waitAnswer();
  const nChart = await page.locator('.dock [data-test=ai-block-chart], .dock [data-test=ai-block-table], .dock [data-test=ai-block-kpis]').count();
  ok('AI 面板：回答里有图表 / 表格 / 指标块', nChart >= 1, `${nChart} 个`);
  ok('AI 面板：有图表（ECharts 画出来了）', await page.locator('.dock [data-test=ai-block-chart] canvas').count() >= 1);
  ok('AI 面板：数据来源标签', await page.locator('.dock [data-test=ai-source]').count() >= 1, (await page.locator('.dock [data-test=ai-block-sources]').first().innerText().catch(() => '')).replace(/\n/g, ' '));
  const refs = await page.locator('.dock .ai-ref').count();
  ok('AI 面板：单据 / 设备变成可点的标签', refs >= 1, `${refs} 个`);
  await shot('01-dock-blocks');
  if (await page.locator('.dock [data-test=ai-pin]').count()) {
    await page.locator('.dock [data-test=ai-pin]').first().click();
    await page.waitForTimeout(500);
    const pins = await api('GET', '/api/ai/pins', null, tok);
    ok('钉到工作台', pins.length >= 1, pins.map((p) => p.title).join('、'));
    for (const p of pins) await api('DELETE', `/api/ai/pins/${p.id}`, null, tok);
  }
  if (refs) {
    await page.locator('.dock .ai-ref').first().click();
    await page.waitForTimeout(800);
    ok('点单据标签打开记录', /\/e\/\w+/.test(page.url()), page.url().replace(BASE, ''));
  }

  // ---------- 2. 右键"解释这个数"（日报页 KPI） ----------
  await page.goto(BASE + '/ai/daily');
  await page.waitForSelector('[data-test=daily-report] .hm-kpi');
  const scrapKpi = page.locator('[data-test=daily-report] .hm-kpi', { hasText: '废品率' }).first();
  const a0 = await nAns();
  await scrapKpi.click({ button: 'right' });
  await page.waitForSelector('[data-test=ai-menu]');
  await page.click('[data-test=ai-menu] [data-action=explain]');
  await page.waitForSelector('.dock .m.user');
  ok('右键"解释这个数"：问题带上了指标', (await page.locator('.dock .m.user').last().innerText()).includes('废品率'));
  await waitAnswer(a0);
  const ans = await page.locator('.dock .m.assistant .mdx').last().innerText();
  ok('解释回答出现', ans.length > 20, ans.slice(0, 80).replace(/\n/g, ' '));
  await shot('02-explain');

  // ---------- 3. 动作草稿：右键派维修 → 驳回 / 通过 ----------
  const n0 = (await api('GET', '/api/e/repair_order?size=1', null, tok)).total;
  await page.goto(BASE + '/equipment/monitor');
  await page.waitForSelector('[data-ai-kind=device]');
  await page.waitForTimeout(600);
  const dev = page.locator('[data-ai-kind=device]', { hasText: 'CNC' }).first();
  await dev.click({ button: 'right' });
  await page.waitForSelector('[data-test=ai-menu] [data-action=ai_repair]');
  ok('设备右键菜单有"AI 派维修（起草）"', true);
  await page.click('[data-test=ai-menu] [data-action=ai_repair]');
  await page.waitForSelector('.dock [data-test=draft-card]', { timeout: 60000 });
  const card1 = page.locator('.dock [data-test=draft-card]').last();
  ok('草稿卡：显示改动（新建内容）', (await card1.locator('[data-test=draft-diff] tbody tr').count()) >= 2, (await card1.locator('[data-test=draft-diff]').innerText()).replace(/\s+/g, ' ').slice(0, 120));
  await shot('03-draft-card');
  await card1.locator('[data-test=draft-reject]').click();
  await card1.locator('[data-test=draft-reject-confirm]').click();
  await page.waitForTimeout(800);
  const n1 = (await api('GET', '/api/e/repair_order?size=1', null, tok)).total;
  ok('驳回草稿：没有建维修单', n1 === n0 && (await card1.innerText()).includes('已驳回'), `${n0} → ${n1}`);
  await dev.click({ button: 'right' });
  await page.click('[data-test=ai-menu] [data-action=ai_repair]');
  await page.waitForFunction(() => document.querySelectorAll('.dock [data-test=draft-card]').length >= 2, null, { timeout: 60000 });
  const card2 = page.locator('.dock [data-test=draft-card]').last();
  await card2.locator('[data-test=draft-approve]').click();
  await card2.locator('[data-test=draft-receipt]').waitFor({ timeout: 15000 });
  const n2 = (await api('GET', '/api/e/repair_order?size=1', null, tok)).total;
  ok('通过草稿：维修单已创建，卡上有回执', n2 === n0 + 1, (await card2.locator('[data-test=draft-receipt]').innerText()));
  await shot('04-draft-approved');
  await card2.locator('[data-test=draft-receipt]').click();
  await page.waitForTimeout(800);
  ok('点回执打开新建的维修单', page.url().includes('/e/repair_order'), page.url().replace(BASE, ''));
  const aps = await api('GET', '/api/approvals?status=done', null, tok);
  ok('审批中心里有这两张草稿的处理记录', aps.filter((a) => a.kind === 'ai_draft').length >= 2);

  // ---------- 4. 一句话自动化：解析 → 回放 → 启用 ----------
  await page.click('[data-test=dock-collapse]').catch(() => {});
  await page.goto(BASE + '/ai/automations');
  await page.waitForSelector('[data-test=auto-text]');
  await page.fill('[data-test=auto-text]', 'CNC 主轴负载连续 1 分钟超过 40% 就提醒设备员，建议检查刀具');
  await page.click('[data-test=auto-parse]');
  await page.waitForSelector('[data-test=auto-flow]', { timeout: 60000 });
  await page.waitForSelector('[data-test=auto-count]', { timeout: 60000 });
  const flowTxt = (await page.locator('[data-test=auto-flow]').innerText()).replace(/\s+/g, ' ');
  ok('一句话 → 流程（触发 → 条件 → 动作）', flowTxt.includes('触发') && flowTxt.includes('动作'), flowTxt.slice(0, 120));
  const cnt = await page.locator('[data-test=auto-count] .v').innerText().catch(() => '');
  ok('历史回放：显示会触发几次 + 时间轴', cnt !== '' && (await page.locator('[data-test=auto-replay] canvas').count()) >= 1, `会触发 ${cnt} 次`);
  await shot('05-automation-replay', true);
  await page.locator('[data-test=auto-replay]').scrollIntoViewIfNeeded();
  await shot('05b-automation-timeline');
  const before = (await api('GET', '/api/ai/automations', null, tok)).items.length;
  await page.click('[data-test=auto-enable]');
  await page.waitForTimeout(800);
  const rules = (await api('GET', '/api/ai/automations', null, tok)).items;
  ok('启用规则', rules.length === before + 1 && rules[0].status === 'active', rules[0]?.summary);
  await page.locator('[data-test=auto-rule]').first().locator('[data-test=auto-pause]').click();
  await page.waitForTimeout(600);
  ok('暂停规则', (await api('GET', '/api/ai/automations', null, tok)).items[0].status === 'paused');
  await shot('06-automation-rules');
  await api('DELETE', `/api/ai/automations/${rules[0].id}`, null, tok);

  // ---------- 5. AI 值守 ----------
  await page.goto(BASE + '/ai/watch');
  await page.waitForSelector('[data-test=watch-finding]', { timeout: 60000 });
  const nf = await page.locator('[data-test=watch-finding]').count();
  ok('AI 值守：发现卡片（带证据）', nf >= 1 && (await page.locator('[data-test=watch-finding] .ev .e').count()) >= 1, `${nf} 条：` + (await page.locator('[data-test=watch-finding] .t').first().innerText()));
  await shot('07-watch', true);
  const wd = page.locator('[data-test=watch-finding] [data-test=watch-draft]').first();
  if (await wd.count()) {
    await wd.click();
    await page.waitForSelector('[data-test=watch-finding] [data-test=draft-card]', { timeout: 30000 });
    ok('AI 值守：按建议起草动作（草稿卡）', true);
    await page.locator('[data-test=watch-finding] [data-test=draft-card] [data-test=draft-reject]').first().click();
    await page.locator('[data-test=watch-finding] [data-test=draft-card] [data-test=draft-reject-confirm]').first().click();
    await page.waitForTimeout(500);
  }

  // ---------- 6. 日报：渲染 + AI 生成 + 导出 PDF ----------
  await page.goto(BASE + '/ai/daily');
  await page.waitForSelector('[data-test=daily-report]');
  await page.click('[data-test=daily-gen-ai]');
  await page.waitForFunction(() => /AI 解读|规则生成/.test(document.querySelector('[data-test=daily-by]')?.textContent || ''), null, { timeout: 120000 });
  await page.waitForTimeout(1200);
  const by = await page.locator('[data-test=daily-by]').innerText();
  const charts = await page.locator('[data-test=daily-report] canvas').count();
  // 5 个图区（产出、OEE、帕累托、停机、能耗）：没数据的区显示空状态（比如数据时钟刚过零点、当天还没有不合格品 / 停机）
  const empties = await page.locator('[data-test=daily-report] .hm-empty').count();
  ok('日报：杂志式版面（头条 + 8 个指标 + 图表）', (await page.locator('[data-test=daily-report] h1').innerText()).length > 8 && (await page.locator('[data-test=daily-report] .hm-kpi').count()) === 8
    && charts >= 2 && charts + empties >= 5, `${by} · ${charts} 张图 + ${empties} 个空区`);
  await shot('08-daily', true);
  await page.locator('[data-test=daily-report] .r2').scrollIntoViewIfNeeded();
  await shot('08b-daily-charts');
  await page.locator('[data-test=daily-report] .r3').scrollIntoViewIfNeeded();
  await shot('08c-daily-actions');
  const [dl] = await Promise.all([page.waitForEvent('download', { timeout: 200000 }), page.click('[data-test=daily-pdf]')]);
  const pdfPath = path.join(OUT, 'daily.pdf');
  await dl.saveAs(pdfPath);
  const head = fs.readFileSync(pdfPath).subarray(0, 4).toString();
  ok('日报导出 PDF', head === '%PDF' && fs.statSync(pdfPath).size > 5000, `${fs.statSync(pdfPath).size} 字节`);

  // ---------- 7. 缺陷回溯 / 协同排产 的 AI 解读 ----------
  await page.goto(BASE + '/cross/backtrace');
  await page.waitForSelector('[data-test=ai-explain-backtrace]');
  await page.locator('[data-test=ai-explain-backtrace] button').first().click();
  await page.waitForSelector('[data-test=ai-explain-backtrace] .mdx', { timeout: 120000 });
  ok('缺陷回溯：AI 根因解读', (await page.locator('[data-test=ai-explain-backtrace] .mdx').innerText()).length > 20);
  await shot('09-backtrace-ai');

  // 工作台信息流里的 AI 值守卡片（工作台页面由 H1 负责，这里只看值守卡片的样子）
  await page.goto(BASE + '/');
  await page.waitForSelector('.hm-kpi');
  if (await page.locator('[data-test=dock-collapse]').count()) await page.click('[data-test=dock-collapse]');
  await page.waitForTimeout(1500);
  const wf = page.locator('[data-test=watch-finding]').first();
  if (await wf.count()) { await wf.scrollIntoViewIfNeeded(); await page.waitForTimeout(400); }
  ok('工作台信息流里显示 AI 值守卡片（单条模式，不重复整个列表）', (await page.locator('.bar .seg').count()) === 0 || (await page.locator('[data-test=watch-finding]').count()) >= 1,
    `${await page.locator('[data-test=watch-finding]').count()} 张`);
  await shot('12-home-feed');

  // ---------- 8. AI 全关：各入口干净地关闭 ----------
  await api('PUT', '/api/ai/switches', { global: false, demo: false, record: false, features: feats(false) }, tok);
  errors.length = 0;
  await page.goto(BASE + '/ai/automations');
  await page.waitForSelector('[data-test=feature-off]');
  ok('AI 关闭：一句话自动化页显示"已关闭"', true);
  await page.goto(BASE + '/ai/watch');
  await page.waitForSelector('[data-test=feature-off]');
  ok('AI 关闭：AI 值守页显示"已关闭"', true);
  await page.goto(BASE + '/ai/daily');
  await page.waitForSelector('[data-test=daily-report] .hm-kpi');
  ok('AI 关闭：日报照常显示（规则版），没有"AI 生成"按钮', (await page.locator('[data-test=daily-gen-ai]').count()) === 0 && (await page.locator('[data-test=daily-report] h1').innerText()).length > 8,
    await page.locator('[data-test=daily-by]').innerText());
  await shot('10-daily-ai-off');
  await page.goto(BASE + '/equipment/monitor');
  await page.waitForSelector('[data-ai-kind=device]');
  await page.locator('[data-ai-kind=device]').first().click({ button: 'right' });
  await page.waitForSelector('[data-test=ai-menu]');
  const off = await page.locator('[data-test=ai-menu] [data-action=ai_repair]').getAttribute('aria-disabled');
  ok('AI 关闭：右键"AI 派维修"置灰', off === 'true');
  await page.click('[data-test=ai-menu] [data-action=ai_repair]', { force: true });
  await page.waitForTimeout(300);
  ok('AI 关闭：点了只提示，不起草', (await toast()).includes('已关闭') && (await page.locator('.dock [data-test=draft-card]').count()) === 0, await toast());
  await shot('11-menu-ai-off');
  await page.keyboard.press('Escape');
  await page.goto(BASE + '/cross/backtrace');
  await page.waitForSelector('.hm-kpi');
  await page.waitForTimeout(600);
  ok('AI 关闭：缺陷回溯页没有 AI 解读卡，统计照常', (await page.locator('[data-test=ai-explain-backtrace]').count()) === 0);
  await page.goto(BASE + '/');
  await page.waitForSelector('.hm-kpi');
  if (await page.locator('[data-test=assistant-pill]').count()) await page.click('[data-test=assistant-pill]');
  await page.waitForSelector('[data-test=dock-off]');
  ok('AI 关闭：AI 面板说明已关闭', true);
  await page.waitForTimeout(800);
  const real = errors.filter((e) => !/ResizeObserver|favicon/.test(e));
  ok('AI 关闭：控制台没有报错（没有去调已关闭的 AI 接口）', real.length === 0, real.slice(0, 3).join(' | '));

  await browser.close();
  await api('PUT', '/api/ai/switches', { global: SW0.global, demo: SW0.demo, record: SW0.record, features: SW0.features }, tok);
  const bad = results.filter((r) => !r[1]);
  console.log(`\n${results.length - bad.length}/${results.length} 通过` + (bad.length ? '；失败：' + bad.map((r) => r[0]).join('、') : '') + `\n截图：${OUT}`);
  process.exit(bad.length ? 1 : 0);
})().catch(async (e) => {
  console.error(e);
  try { await PAGE.screenshot({ path: path.join(OUT, 'zz-failure.png') }); } catch { /* */ }
  try { await BROWSER.close(); } catch { /* */ }
  process.exit(2);
});
