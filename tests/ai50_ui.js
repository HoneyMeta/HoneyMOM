// Pinned to zh-CN (UI language + X-Lang): the assertions use the Chinese UI texts; tests/i18n_ui.js covers English.
// 5.0 M6 页面走查（Playwright）：节拍分析（全线 + 单台：甘特 / 叠加 / 分布 / 漂移 / 动作模型）、AI 技能库（运行、结果）、
// 右下角 AI 面板"保存为技能"、MCP 接入（新建密钥、配置片段、试一下）。AI 用演示模式，结束时恢复开关。截图（1680 宽）存 SHOTS_DIR。
// 用法（B 机，MES 已启动、最好先跑过 tests/ai50_check.py，库里有动作模型和技能）：node tests/ai50_ui.js
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(process.env.APPDATA, 'npm', 'node_modules', 'playwright'));

const BASE = (process.env.MES_URL || 'http://127.0.0.1:8900').replace(/\/$/, '');
const OUT = process.env.SHOTS_DIR || path.join(__dirname, '..', 'data', 'ui_shots', 'ai50');
fs.mkdirSync(OUT, { recursive: true });
const results = [];
const ok = (name, cond, detail = '') => { results.push([name, !!cond]); console.log((cond ? 'PASS ' : 'FAIL ') + name, detail); };
async function api(method, p, body, token) {
  const r = await fetch(BASE + p, { method, headers: { 'Content-Type': 'application/json', 'X-Lang': 'zh-CN', ...(token ? { Authorization: 'Bearer ' + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, j: await r.json().catch(() => ({})) };
}

(async () => {
  const tok = (await api('POST', '/api/login', { username: 'admin', password: 'honey123' })).j.token;
  const sw0 = (await api('GET', '/api/ai/switches', null, tok)).j;
  await api('PUT', '/api/ai/switches', { global: true, demo: true, record: false, features: Object.fromEntries(Object.keys(sw0.features).map((k) => [k, true])) }, tok);
  const browser = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const errors = [];
  const ctx = await browser.newContext({ viewport: { width: 1680, height: 1050 }, locale: 'zh-CN' });
  await ctx.addInitScript((tk) => { if (window.top !== window) return; localStorage.setItem('mes_token', tk); localStorage.setItem('hm_lang', 'zh-CN'); localStorage.setItem('mes_assist_open', '0'); localStorage.setItem('hm_welcomed:admin', '1'); }, tok);
  const page = await ctx.newPage();
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && !/favicon|401|403|404/.test(m.text())) errors.push(m.text()); });
  const shot = (n) => page.screenshot({ path: path.join(OUT, n + '.png') });

  try {
    // ---------- 节拍分析 ----------
    await page.goto(BASE + '/cycle');
    await page.waitForSelector('[data-tour=cyc-kpis] .hm-kpi', { timeout: 15000 });
    await page.waitForTimeout(2500);
    const nSt = await page.locator('[data-tour=cyc-stations] .hm-table tbody tr').count();
    ok('节拍分析：工位表', nSt >= 1, `${nSt} 行`);
    ok('节拍分析：全线节拍图', (await page.locator('[data-tour=cyc-line-chart] canvas').count()) >= 1);
    await shot('cycle-line');
    await page.waitForSelector('[data-test=cyc-drill]', { timeout: 15000 });
    await page.waitForTimeout(1500);
    ok('节拍分析：单台明细（动作表 + 甘特）', (await page.locator('[data-test=cyc-gantt] .gr').count()) >= 1 && (await page.locator('[data-test=cyc-drill] .hm-table tbody tr').count()) >= 1);
    ok('节拍分析：叠加 / 分布 / 漂移图', (await page.locator('[data-test=cyc-drill] canvas').count()) >= 3);
    await page.locator('[data-test=cyc-drill]').scrollIntoViewIfNeeded();
    await shot('cycle-station');
    const cnc1 = (await api('GET', '/api/e/equipment?q=CNC1&size=10', null, tok)).j.items?.find((x) => x.code === 'CNC1');
    if (cnc1) {
      await page.goto(BASE + '/cycle?eq=' + cnc1.id);
      await page.waitForSelector('[data-test=cyc-drill]', { timeout: 15000 });
      await page.waitForTimeout(1500);
      const nAl = await page.locator('[data-test=cyc-alert]').count();
      if (nAl) {
        await page.locator('[data-test=cyc-explain]').first().click();
        await page.waitForSelector('[data-test=cyc-expl]', { timeout: 15000 });
        ok('节拍分析：慢动作告警 + AI 解释', true, (await page.locator('[data-test=cyc-expl]').innerText()).slice(0, 60));
      } else ok('节拍分析：CNC1（没有告警，跳过 AI 解释）', true);
      await page.locator('[data-test=cyc-drill]').scrollIntoViewIfNeeded();
      await shot('cycle-cnc1');
      await page.click('[data-test=cyc-edit]');
      await page.waitForSelector('[data-test=cyc-editor] .el-table__row', { timeout: 10000 });
      ok('节拍分析：动作模型编辑（动作清单 / 起止信号 / 标准时间）', (await page.locator('[data-test=cyc-editor] .el-table__row').count()) >= 3);
      await shot('cycle-model');
      await page.keyboard.press('Escape');
    }

    // ---------- 技能库 ----------
    await page.goto(BASE + '/ai/skills');
    await page.waitForSelector('[data-test=skl-list] .it', { timeout: 15000 });
    ok('技能库：内置技能', (await page.locator('[data-test=skl-list] .it').count()) >= 6);
    await page.click('[data-test=skl-item-oee_loss_top5]');
    await page.waitForSelector('[data-test=skl-detail]');
    await page.click('[data-test=skl-run]');
    await page.waitForSelector('[data-test=skl-result]', { timeout: 30000 });
    await page.waitForTimeout(1200);
    ok('技能库：运行出结果（图表 / 表格）', (await page.locator('[data-test=skl-result] [data-test^=ai-block]').count()) >= 1);
    await shot('skills');

    // ---------- 保存为技能（AI 面板） ----------
    await page.goto(BASE + '/cycle');
    await page.waitForTimeout(800);
    await page.click('[data-test=assistant-pill]');
    await page.waitForSelector('[data-test=assistant-input]');
    await page.fill('[data-test=assistant-input]', '机加区最近 8 小时的瓶颈工位和瓶颈动作是什么？损失多少？');
    await page.keyboard.press('Enter');
    await page.waitForSelector('[data-test=save-skill]', { timeout: 60000 });
    ok('AI 面板：回答下面有"保存为技能"', true);
    await shot('dock-answer');
    await page.locator('[data-test=save-skill]').last().click();
    await page.waitForSelector('[data-test=skill-save-param]', { timeout: 15000 });
    ok('保存为技能：建议的参数', (await page.locator('[data-test=skill-save-param]').count()) >= 1);
    await page.fill('[data-test=skill-save-name]', 'UI 走查技能 ' + (Date.now() % 10000));
    await shot('skill-save');
    await page.click('[data-test=skill-save-ok]');
    await page.waitForURL(/\/ai\/skills\?id=/, { timeout: 15000 });
    await page.waitForSelector('[data-test=skl-plan] li');
    ok('保存为技能：存进技能库，带查询计划', (await page.locator('[data-test=skl-plan] li').count()) >= 1);
    await shot('skill-saved');

    // ---------- MCP 接入 ----------
    await page.goto(BASE + '/system/mcp');
    await page.waitForSelector('[data-test=mcp-tools] .tl', { timeout: 15000 });
    ok('MCP 接入：工具清单', (await page.locator('[data-test=mcp-tools] .tl').count()) >= 15);
    await page.fill('[data-test=mcp-key-name]', 'UI 走查');
    await page.click('[data-test=mcp-key-create]');
    await page.waitForSelector('[data-test=mcp-newkey]', { timeout: 10000 });
    ok('MCP 接入：新建密钥（只显示一次）', /hmk_/.test(await page.locator('[data-test=mcp-newkey]').innerText()));
    await page.fill('[data-test=mcp-try-args]', '{"equipment":"CNC1"}');
    await page.locator('[data-tour=mcp-tools] .tl', { hasText: '节拍分析' }).first().click();
    await page.click('[data-test=mcp-try]');
    await page.waitForSelector('[data-test=mcp-try-out]', { timeout: 30000 });
    ok('MCP 接入：试一下（cycle_analysis）', (await page.locator('[data-test=mcp-try-out]').innerText()).includes('CNC1'));
    await shot('mcp');
    await page.locator('[data-test=mcp-tabs] .el-tabs__item', { hasText: 'Cursor' }).click();
    await page.waitForTimeout(300);
    ok('MCP 接入：配置片段带新密钥', /hmk_/.test(await page.locator('[data-test=mcp-tabs] .el-tab-pane:visible pre').innerText()));
  } catch (e) {
    ok('走查没有中断', false, String(e).slice(0, 300));
    await shot('error');
  }
  ok('没有前端报错', !errors.length, errors.slice(0, 5).join(' | '));
  await api('PUT', '/api/ai/switches', { global: sw0.global, demo: sw0.demo, record: sw0.record, features: sw0.features }, tok);
  await browser.close();
  const n = results.filter((r) => r[1]).length;
  console.log(`SUMMARY ${n}/${results.length}`);
  process.exit(n === results.length ? 0 : 1);
})();
