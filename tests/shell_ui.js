// Pinned to zh-CN (UI language + X-Lang): the assertions use the Chinese UI texts; tests/i18n_ui.js covers English.
// 界面外壳与 AI 选择框架走查（Playwright）：菜单与菜单搜索、Ctrl+K、AI 面板收起/展开（内容重排）、Ctrl+点击加上下文、右键菜单、
// 表格行浮动工具条、AI 关闭时的提示、设置页开关保存、控制台无报错；并按 1280×720 / 1680×1050 / 2560×1440 截图。
// 用法（B 机，MES 已启动且有数据，比如先跑过 tests/e2e.py）：node tests/shell_ui.js
//   环境变量 MES_URL（默认 http://127.0.0.1:8900）；截图在 data/ui_shots/shell/（SHOTS_DIR 可改）
// 测试开始时把 AI 全部关掉，后面打开为"演示模式"（不花 token），结束时恢复原来的开关。
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(process.env.APPDATA, 'npm', 'node_modules', 'playwright'));

const BASE = (process.env.MES_URL || 'http://127.0.0.1:8900').replace(/\/$/, '');
const OUT = process.env.SHOTS_DIR || path.join(__dirname, '..', 'data', 'ui_shots', 'shell');
fs.mkdirSync(OUT, { recursive: true });
const results = [];
const ok = (name, cond, detail = '') => { results.push([name, !!cond]); console.log((cond ? 'PASS ' : 'FAIL ') + name, detail); };

async function api(method, p, body, token) {
  const r = await fetch(BASE + p, { method, headers: { 'Content-Type': 'application/json', 'X-Lang': 'zh-CN', ...(token ? { Authorization: 'Bearer ' + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return r.json();
}

let PAGE = null;
(async () => {
  const tok = (await api('POST', '/api/login', { username: 'admin', password: 'honey123' })).token;
  const SW0 = await api('GET', '/api/ai/switches', null, tok);
  const allOff = { global: false, demo: false, record: false, features: Object.fromEntries(Object.keys(SW0.features).map((k) => [k, false])) };
  await api('PUT', '/api/ai/switches', allOff, tok);

  const browser = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
  await ctx.addInitScript((t) => {
    if (!sessionStorage.getItem('init')) {
      sessionStorage.setItem('init', '1'); localStorage.setItem('mes_token', t); localStorage.setItem('hm_lang', 'zh-CN'); localStorage.setItem('mes_assist_open', '0'); localStorage.removeItem('mes_assist_thread');
      localStorage.removeItem('hm_nav_collapsed'); localStorage.removeItem('hm_dock_w'); sessionStorage.removeItem('hm_ai_ctx');
    }
  }, tok);
  const page = await ctx.newPage();
  PAGE = page;
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  const shot = async (name, full = false) => { await page.waitForTimeout(500); await page.screenshot({ path: path.join(OUT, name + '.png'), fullPage: full }); };
  const toast = async () => (await page.locator('.el-message').allInnerTexts().catch(() => [])).join(' | ');
  const mainWidth = () => page.evaluate(() => document.querySelector('.hm-main').getBoundingClientRect().width);

  // ---------- 1. 菜单 + 菜单搜索 ----------
  await page.goto(BASE + '/');
  await page.waitForSelector('[data-test=side-nav]');
  await page.waitForSelector('.hm-kpi');
  await page.waitForTimeout(800);
  const groups = await page.locator('.hm-nav .grp-sec').count();
  ok('左侧菜单分组齐全', groups >= 9, `${groups} 组`);   // MOM: 工作台 + 生产/质量/维护/库存运营 + 计划与业务 + 工厂模型与接入 + 智能 + 系统
  await page.fill('[data-test=nav-search]', 'OEE');
  await page.waitForSelector('[data-test=nav-results] .item');
  ok('菜单搜索出结果', (await page.locator('[data-test=nav-results]').innerText()).includes('OEE 分析'));
  await page.press('[data-test=nav-search]', 'Enter');
  await page.waitForURL(/\/equipment\/oee/);
  ok('菜单搜索回车打开页面', page.url().includes('/equipment/oee'));
  ok('面包屑', (await page.locator('[data-test=breadcrumbs]').innerText()).includes('维护运营'));
  const w0 = await page.evaluate(() => document.querySelector('.hm-nav').getBoundingClientRect().width);
  await page.click('[data-test=nav-fold]');
  // 收起有 320ms 的宽度过渡，慢机器上 450ms 时可能还在半路：等宽度稳定下来再量
  await page.waitForFunction(() => document.querySelector('.hm-nav').getBoundingClientRect().width < 80, null, { timeout: 3000 }).catch(() => {});
  await page.waitForTimeout(100);
  const w1 = await page.evaluate(() => document.querySelector('.hm-nav').getBoundingClientRect().width);
  ok('菜单可收成图标栏', w1 < 80 && w0 > 200, `${Math.round(w0)} → ${Math.round(w1)}`);
  await page.click('[data-test=nav-fold]');
  await page.waitForTimeout(400);

  // ---------- 2. Ctrl+K ----------
  await page.keyboard.press('Control+k');
  await page.waitForSelector('[data-test=palette]');
  await page.fill('[data-test=palette-input]', '监控');
  await page.waitForTimeout(400);
  ok('Ctrl+K 搜到页面', (await page.locator('[data-test=palette]').innerText()).includes('实时监控'));
  await page.keyboard.press('Enter');
  await page.waitForURL(/\/equipment\/monitor/);
  ok('Ctrl+K 回车打开页面', page.url().includes('/equipment/monitor'));
  const wo = (await api('GET', '/api/e/work_order?size=1', null, tok)).items[0];
  await page.keyboard.press('Control+k');
  await page.fill('[data-test=palette-input]', wo.code.slice(0, 8));
  await page.waitForSelector('[data-test=palette] .po[data-kind=record]', { timeout: 8000 }).catch(() => {});
  ok('Ctrl+K 按单号搜到记录', await page.locator('[data-test=palette] .po[data-kind=record]').count() >= 1, wo.code);
  await shot('palette-1440');
  await page.keyboard.press('Escape');

  // ---------- 3. AI 关闭时 ----------
  await page.goto(BASE + '/');
  await page.waitForSelector('.hm-kpi');
  await page.waitForTimeout(600);
  ok('底部状态条：AI 未开启', (await page.locator('[data-test=ai-status]').innerText()).includes('AI 未开启'));
  const kpi0 = page.locator('.hm-kpi').nth(0);
  await kpi0.click({ button: 'right' });
  await page.waitForSelector('[data-test=ai-menu]');
  const menuTxt = await page.locator('[data-test=ai-menu]').innerText();
  ok('AI 关闭：右键菜单提示"AI 已关闭（系统设置可开启）"', menuTxt.includes('AI 已关闭（系统设置可开启）'));
  ok('AI 关闭：非 AI 动作（追溯/复制）仍在', menuTxt.includes('复制') && menuTxt.includes('追溯'));
  await shot('ai-off-menu-1440');
  await page.click('[data-test=ai-menu] [data-action=explain]', { force: true });     // aria-disabled，但点了要给出原因
  await page.waitForTimeout(300);
  ok('AI 关闭：点 AI 动作给出提示', (await toast()).includes('AI 已关闭'), await toast());
  await kpi0.click({ button: 'right' });
  await page.click('[data-test=ai-menu] [data-action=copy]');
  await page.waitForTimeout(300);
  ok('AI 关闭：复制照常可用', (await toast()).includes('已复制'));
  await kpi0.click({ modifiers: ['Control'] });
  await page.waitForTimeout(300);
  ok('AI 关闭：Ctrl+点击提示且不加上下文', (await toast()).includes('AI 已关闭') && (await page.locator('[data-ai-selected]').count()) === 0);
  await page.click('[data-test=assistant-pill]');
  await page.waitForSelector('[data-test=dock-off]');
  ok('AI 关闭：AI 面板说明已关闭', (await page.locator('[data-test=dock-off]').innerText()).includes('AI 未开启'));
  await shot('ai-off-dock-1440');
  await page.click('[data-test=dock-collapse]');
  await page.goto(BASE + '/ai/chat');
  await page.waitForSelector('[data-test=feature-off]');
  ok('AI 关闭：问数整页显示"已关闭"', (await page.locator('[data-test=feature-off]').innerText()).includes('已关闭'));
  ok('AI 关闭：菜单项置灰', await page.locator('.hm-nav .item.off').count() >= 3);

  // ---------- 4. 设置页：开关保存 ----------
  await page.goto(BASE + '/system/ai');
  await page.waitForSelector('[data-test=ai-global]');
  await page.click('[data-test=ai-global]');
  await page.waitForSelector('[data-test=ai-global].is-checked');
  for (const k of ['ask', 'agents', 'watch']) { await page.click(`[data-test=ai-feat-${k}]`); await page.waitForSelector(`[data-test=ai-feat-${k}].is-checked`); }
  await page.click('[data-test=ai-mode] label:has-text("演示模式")');
  await page.waitForTimeout(600);
  await page.reload();
  await page.waitForSelector('[data-test=ai-global]');
  await page.waitForTimeout(500);
  const persisted = (await page.locator('[data-test=ai-global].is-checked').count()) === 1 && (await page.locator('[data-test=ai-feat-ask].is-checked').count()) === 1
    && (await page.locator('[data-test=ai-feat-daily].is-checked').count()) === 0;
  const sw = await api('GET', '/api/ai/switches', null, tok);
  ok('设置页：开关刷新后仍在（服务端已保存）', persisted && sw.global && sw.features.ask && sw.demo, `mode=${sw.mode}`);
  await shot('settings-1440');

  // ---------- 5. AI 面板收起/展开，内容重排 ----------
  await page.goto(BASE + '/');
  await page.waitForSelector('.hm-kpi');
  await page.waitForTimeout(600);
  const mw0 = await mainWidth();
  const edge = await page.locator('[data-test=assistant-pill]').boundingBox();
  const mainBox = await page.evaluate(() => { const r = document.querySelector('.hm-main').getBoundingClientRect(); return { right: r.right }; });
  ok('收起时 AI 只占一条细边、不盖内容', edge && edge.width <= 32 && edge.x >= mainBox.right - 1, `edge ${edge && Math.round(edge.width)}px`);
  await page.click('[data-test=assistant-pill]');
  await page.waitForSelector('[data-test=assistant-panel]');
  await page.waitForTimeout(500);
  const mw1 = await mainWidth();
  ok('展开 AI 面板：主区变窄（重排，不遮挡）', mw0 - mw1 > 250, `${Math.round(mw0)} → ${Math.round(mw1)}`);
  await page.click('[data-test=dock-collapse]');
  // 主区宽度有 320ms 的过渡，数据多的库上主线程忙时会更久：等宽度回到原值（最多 3 秒）再判断
  await page.waitForFunction((w) => Math.abs(document.querySelector('.hm-main').getBoundingClientRect().width - w) < 2, mw0, { timeout: 3000 }).catch(() => {});
  ok('收起 AI 面板：主区恢复', Math.abs((await mainWidth()) - mw0) < 2, `${Math.round(mw0)} → ${Math.round(await mainWidth())}`);

  // ---------- 6. Ctrl+点击加上下文标签 ----------
  await page.locator('.hm-kpi').nth(0).click({ modifiers: ['Control'] });
  await page.locator('.hm-kpi').nth(3).click({ modifiers: ['Control'] });
  await page.waitForTimeout(300);
  ok('Ctrl+点击：元素出现"已选给 AI"的环', (await page.locator('[data-ai-selected].hm-kpi').count()) === 2);
  await page.click('[data-test=assistant-pill]');
  await page.waitForSelector('[data-test=ai-chips]');
  ok('Ctrl+点击：AI 面板出现 2 个上下文标签', (await page.locator('[data-test=ai-chip]').count()) === 2, await page.locator('[data-test=ai-chips]').innerText());
  await page.locator('[data-test=ai-chip] button').first().click();
  ok('上下文标签可移除', (await page.locator('[data-test=ai-chip]').count()) === 1 && (await page.locator('[data-ai-selected].hm-kpi').count()) === 1);
  await page.locator('.hm-kpi').nth(4).click({ modifiers: ['Control'] });
  await shot('ai-chips-1440');

  // ---------- 7. 右键菜单动作 ----------
  await page.locator('.hm-kpi').nth(1).click({ button: 'right' });
  await page.waitForSelector('[data-test=ai-menu]');
  const acts = await page.locator('[data-test=ai-menu] .mi').evaluateAll((els) => els.map((e) => e.getAttribute('data-action')));
  ok('右键菜单：问 AI / 解释 / 为什么变化 / 追溯 / 生成报告 / 加入对比 / 复制', ['ask', 'explain', 'why', 'trace', 'report', 'compare', 'copy'].every((a) => acts.includes(a)), acts.join(','));
  await shot('ai-menu-1440');
  await page.click('[data-test=ai-menu] [data-action=explain]');
  await page.waitForSelector('.dock .m.user');
  ok('右键"解释这个数"：发给 AI（带上下文）', (await page.locator('.dock .m.user').last().innerText()).includes('解释这个数'));
  await page.waitForSelector('.dock .m.assistant .mdx', { timeout: 60000 });
  await page.waitForFunction(() => !document.querySelector('.dock .prog'), null, { timeout: 60000 });
  ok('AI 回答出现（演示模式）', (await page.locator('.dock .m.assistant .mdx').last().innerText()).length > 10);
  await shot('ai-answer-1440');
  // 键盘：聚焦 KPI，Shift+F10 打开菜单，Esc 关闭
  await page.locator('.hm-kpi').nth(2).focus();
  await page.keyboard.press('Shift+F10');
  await page.waitForSelector('[data-test=ai-menu]');
  ok('键盘 Shift+F10 打开右键菜单', await page.locator('[data-test=ai-menu]').isVisible());
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Escape');
  // 菜单有 200ms 的淡出过渡，等它真正从 DOM 里移走再判断（原来固定等 250ms，慢机器上会偶发失败）
  await page.waitForSelector('[data-test=ai-menu]', { state: 'detached', timeout: 3000 }).catch(() => {});
  ok('Esc 关闭菜单', (await page.locator('[data-test=ai-menu]').count()) === 0);

  // ---------- 8. 表格行浮动工具条 ----------
  await page.goto(BASE + '/e/work_order');
  await page.waitForSelector('[data-ai-rows] .el-table__row');
  await page.waitForTimeout(500);
  await page.locator('[data-ai-rows] .el-table__row').first().hover();
  await page.waitForSelector('[data-test=ai-toolbar]', { timeout: 4000 }).catch(() => {});
  ok('表格行：悬停出现浮动工具条', await page.locator('[data-test=ai-toolbar]').isVisible().catch(() => false));
  await shot('row-toolbar-1440');
  if (await page.locator('[data-test=ai-toolbar]').isVisible().catch(() => false)) {
    await page.click('[data-test=ai-toolbar] [data-action=ask]');
    await page.waitForTimeout(400);
    ok('工具条"问 AI"把这一行加进上下文', (await page.locator('[data-test=ai-chips]').innerText()).includes(wo.code.slice(0, 4)) || (await page.locator('[data-test=ai-chip]').count()) >= 3);
  }
  await page.locator('[data-ai-rows] .el-table__row').nth(1).click({ button: 'right' });
  await page.waitForSelector('[data-test=ai-menu]');
  ok('表格行：右键菜单有"打开记录"', (await page.locator('[data-test=ai-menu] [data-action=open]').count()) === 1);
  await page.keyboard.press('Escape');

  // ---------- 9. 截图：三种分辨率 ----------
  await api('POST', '/api/assistant/threads', {}, tok).catch(() => {});
  for (const [w, h] of [[1280, 720], [1680, 1050], [2560, 1440]]) {
    await page.setViewportSize({ width: w, height: h });
    await page.goto(BASE + '/');
    await page.waitForSelector('.hm-kpi');
    await page.waitForTimeout(1200);
    await shot(`home-dock-${w}x${h}`);
    await page.click('[data-test=dock-collapse]').catch(() => {});
    await page.waitForTimeout(500);
    await shot(`home-${w}x${h}`);
    await page.goto(BASE + '/equipment/monitor');
    await page.waitForSelector('.eq');
    await page.waitForTimeout(800);
    await shot(`monitor-${w}x${h}`);
    await page.goto(BASE + '/e/work_order');
    await page.waitForSelector('.el-table__row');
    await page.waitForTimeout(600);
    await shot(`workorder-${w}x${h}`);
    await page.click('[data-test=assistant-pill]').catch(() => {});
    await page.waitForTimeout(300);
  }
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto(BASE + '/');
  await page.waitForSelector('.hm-kpi');
  await page.waitForTimeout(800);
  await shot('home-tablet-1024x768');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(BASE + '/');
  await page.waitForTimeout(1200);
  await shot('home-mobile-390x844');

  // 关掉 AI 再看一次首页（默认状态）
  await api('PUT', '/api/ai/switches', allOff, tok);
  await page.setViewportSize({ width: 1680, height: 1050 });
  await page.goto(BASE + '/');
  await page.waitForSelector('.hm-kpi');
  await page.waitForTimeout(1200);
  await shot('home-ai-off-1680x1050');

  const real = errors.filter((e) => !/ResizeObserver|favicon/.test(e));
  ok('浏览器控制台没有报错', real.length === 0, real.slice(0, 5).join(' | '));
  await browser.close();
  await api('PUT', '/api/ai/switches', { global: SW0.global, demo: SW0.demo, record: SW0.record, features: SW0.features }, tok);
  const bad = results.filter((r) => !r[1]);
  console.log(`\n${results.length - bad.length}/${results.length} 通过` + (bad.length ? '；失败：' + bad.map((r) => r[0]).join('、') : '') + `\n截图：${OUT}`);
  process.exit(bad.length ? 1 : 0);
})().catch(async (e) => {
  console.error(e);
  try { await PAGE.screenshot({ path: path.join(OUT, 'zz-failure.png') }); } catch { /* */ }
  process.exit(2);
});
