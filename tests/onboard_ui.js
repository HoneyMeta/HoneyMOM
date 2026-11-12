// Pinned to zh-CN (UI language + X-Lang): the assertions use the Chinese UI texts; tests/i18n_ui.js covers English.
// 接入中心走查（Playwright）：概况与导入、逐点核对（颜色筛选、批量确认）、发现与浏览（OPC UA 树）、工厂模型（层级、模板、版本比对）、
// 平面图与孪生（2D 拖拽落位并保存、3D 场景）、AI 辅助（关闭时的提示、可疑点）、首页平面图来自工厂模型。1680×1050 和 1280×720 各截图。
// 用法（B 机，MES 已启动且接着虚拟工厂在跑）：node tests/onboard_ui.js     MES_URL（默认 http://127.0.0.1:8900）、SHOTS_DIR 截图目录
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(process.env.APPDATA, 'npm', 'node_modules', 'playwright'));

const BASE = (process.env.MES_URL || 'http://127.0.0.1:8900').replace(/\/$/, '');
const OUT = process.env.SHOTS_DIR || path.join(__dirname, '..', 'data', 'ui_shots', 'onboard');
fs.mkdirSync(OUT, { recursive: true });
const results = [];
const ok = (name, cond, detail = '') => { results.push([name, !!cond]); console.log((cond ? 'PASS ' : 'FAIL ') + name, detail); };
async function api(method, p, body, token) {
  const r = await fetch(BASE + p, { method, headers: { 'Content-Type': 'application/json', 'X-Lang': 'zh-CN', ...(token ? { Authorization: 'Bearer ' + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return r.json();
}

(async () => {
  const tok = (await api('POST', '/api/login', { username: 'admin', password: 'honey123' })).token;
  const browser = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const errors = [];
  for (const [W, H] of [[1680, 1050], [1280, 720]]) {
    const tag = `${W}`;
    const ctx = await browser.newContext({ viewport: { width: W, height: H }, locale: 'zh-CN' });
    await ctx.addInitScript((t) => { if (window.top !== window) return; localStorage.setItem('mes_token', t); localStorage.setItem('hm_lang', 'zh-CN'); localStorage.setItem('mes_assist_open', '0'); localStorage.setItem('hm_welcomed:admin', '1'); }, tok);
    const page = await ctx.newPage();
    page.on('pageerror', (e) => errors.push(`${W} ${String(e)}`));
    page.on('console', (m) => { if (m.type() === 'error' && !/favicon|ResizeObserver/.test(m.text())) errors.push(`${W} console: ${m.text().slice(0, 200)}`); });
    const shot = (n) => page.screenshot({ path: path.join(OUT, `${n}-${tag}.png`) });
    const hscroll = () => page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);

    // ---------- 概况 ----------
    await page.goto(BASE + '/onboard');
    await page.waitForSelector('[data-test=onboard-overview] .hm-table tbody tr td', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(800);
    const srcRows = await page.locator('[data-tour=onboard-sources] tbody tr').count();
    ok(`${W} 概况：数据源列表`, srcRows >= 3, `${srcRows} 行`);
    ok(`${W} 概况：页签 6 个`, (await page.locator('[data-tour=onboard-tabs] a').count()) === 6);
    ok(`${W} 概况：没有横向滚动`, !(await hscroll()));
    await shot('overview');
    if (W === 1680) {
      // 点表模板下载按钮可用、预览对话框（交付包从虚拟工厂取 —— 只在环境变量 VF_URL 有时做）
      ok('模板下载按钮', await page.locator('[data-test=pt-template]').isVisible());
      if (process.env.VF_URL) {
        const vt = await fetch(process.env.VF_URL.replace(/\/$/, '') + '/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Lang': 'zh-CN' },
          body: JSON.stringify({ username: 'admin', password: 'honey123' }) }).then((r) => r.json()).catch(() => ({}));
        await page.fill('[data-test=pkg-url]', process.env.VF_URL.replace(/\/$/, '') + '/api/p/foundry-housing');
        await page.locator('.tok input').fill(vt.token || '');
        await page.click('[data-test=pkg-fetch]');
        const dlg = await page.waitForSelector('[data-test=import-preview] .pvsum', { timeout: 60000 }).then(() => true).catch(() => false);
        ok('取交付包 → 预览对话框', dlg);
        await page.waitForTimeout(500);
        await page.screenshot({ path: path.join(OUT, `preview-${tag}.png`) });
        await page.click('[data-test=import-preview] .el-dialog__footer .el-button:not(.el-button--primary)');   // 放弃（不改端点）
        await page.waitForTimeout(500);
      }
    }

    // ---------- 逐点核对 ----------
    await page.goto(BASE + '/onboard/check');
    await page.waitForSelector('[data-test=check-table] .el-table__row', { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(600);
    const rows0 = await page.locator('[data-test=check-table] .el-table__body-wrapper .el-table__row').count();
    ok(`${W} 逐点核对：点位表`, rows0 > 20, `${rows0} 行（表格虚拟高度内）`);
    const greens = await page.locator('[data-test=chip-green] b').innerText();
    ok(`${W} 逐点核对：绿色计数`, Number(greens) > 100, greens);
    await page.click('[data-test=chip-green]');                     // 只看非绿
    await page.waitForTimeout(400);
    await shot('check');
    await page.click('[data-test=chip-green]');
    if (W === 1680) {
      const before = await page.locator('[data-test=coverage]').innerText();
      const btn = page.locator('[data-test=confirm-green]');
      const wasEnabled = await btn.isEnabled();
      if (wasEnabled) {
        await btn.click();
        await page.waitForFunction((b0) => parseFloat(document.querySelector('[data-test=coverage]')?.textContent || '0') > parseFloat(b0), before, { timeout: 20000 }).catch(() => {});
      }
      const after = await page.locator('[data-test=coverage]').innerText();
      ok('批量确认绿色 → 覆盖率上升（没有待确认的绿点时按钮不可用）', wasEnabled ? parseFloat(after) > parseFloat(before) : !(await btn.isEnabled()), `${before} → ${after}`);
      await page.waitForSelector('[data-test=yellow-table] tbody tr button', { timeout: 10000 }).catch(() => {});
      const yrows = await page.locator('[data-test=yellow-table] tbody tr').count();
      ok('点表外（黄）队列', yrows >= 1, `${yrows} 行`);
      if (yrows) {
        await page.locator('[data-test=yellow-table] [data-test^=yadd-]').first().click();
        const d = await page.waitForSelector('[data-test=yadd-dialog] .el-form', { timeout: 5000 }).then(() => true).catch(() => false);
        ok('点表外地址 → "加进点表"对话框', d);
        await page.screenshot({ path: path.join(OUT, `yadd-${tag}.png`) });
        await page.keyboard.press('Escape');
      }
    }
    ok(`${W} 逐点核对：没有横向滚动`, !(await hscroll()));

    // ---------- 发现与浏览 ----------
    await page.goto(BASE + '/onboard/discover');
    const tree = await page.waitForSelector('[data-test=opc-tree] .el-tree-node', { timeout: 20000 }).then(() => true).catch(() => false);
    ok(`${W} OPC UA 浏览：根节点`, tree);
    if (tree) {
      await page.locator('[data-test=opc-tree] .el-tree-node__content').first().click();
      await page.locator('[data-test=opc-tree] .el-tree-node__expand-icon').first().click();
      await page.waitForTimeout(2500);
      const n = await page.locator('[data-test=opc-tree] .el-tree-node').count();
      ok(`${W} OPC UA 浏览：展开子节点`, n > 1, `${n} 个节点`);
      ok(`${W} OPC UA 浏览：节点属性`, (await page.locator('[data-test=opc-attrs] .ar').count()) >= 5);
    }
    await shot('discover');

    // ---------- 工厂模型 ----------
    await page.goto(BASE + '/onboard/model');
    await page.waitForSelector('[data-test=model-tree] .el-tree-node', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(500);
    await page.locator('[data-test=model-tree] .el-tree-node__content', { hasText: 'CNC1' }).first().click();
    const devCard = await page.waitForSelector('[data-test=model-device] tbody tr', { timeout: 8000 }).then(() => true).catch(() => false);
    ok(`${W} 工厂模型：设备点位`, devCard);
    ok(`${W} 工厂模型：模板 ≥ 11 个`, (await page.locator('[data-test=templates-table] tbody tr').count()) >= 11);
    const dv = page.locator('[data-test^=diff-]').first();
    if (await dv.count()) {
      await dv.click();
      ok(`${W} 工厂模型：版本比对`, await page.waitForSelector('[data-test=version-diff]', { timeout: 5000 }).then(() => true).catch(() => false));
    }
    await shot('model');
    if (W === 1680) {
      await page.click('[data-test=tpl-apply-cnc]');
      ok('模板批量套用对话框（同类设备预选）', await page.waitForSelector('[data-test=apply-dialog] .el-select__selected-item, [data-test=apply-dialog] .el-tag', { timeout: 5000 }).then(() => true).catch(() => false));
      await page.screenshot({ path: path.join(OUT, `apply-${tag}.png`) });
      if (await page.locator('[data-test=apply-ok]').isEnabled()) { await page.click('[data-test=apply-ok]'); await page.waitForTimeout(800); }
      else await page.keyboard.press('Escape');
    }

    // ---------- 平面图与孪生 ----------
    await page.goto(BASE + '/onboard/twin');
    await page.waitForSelector('[data-test=twin2d] g.dev', { timeout: 15000 }).catch(() => {});
    const devs = await page.locator('[data-test=twin2d] g.dev').count();
    ok(`${W} 2D 孪生：设备`, devs >= 20, `${devs} 台`);
    await page.waitForTimeout(3500);
    const live = await page.locator('[data-test=twin2d] g.dev.s-run, [data-test=twin2d] g.dev.s-idle, [data-test=twin2d] g.dev.s-down').count();
    ok(`${W} 2D 孪生：实时状态上色`, live >= 5, `${live} 台有状态`);
    await shot('twin2d');
    if (W === 1680) {
      const b0 = (await api('GET', '/api/factory/devices?q=SHOT1', null, tok)).items[0];
      await page.click('[data-test=twin-edit]');
      const g = page.locator('[data-test=twin2d] g.dev[data-code=SHOT1] rect');
      const box = await g.boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width / 2 + 40, box.y + box.height / 2 + 25, { steps: 8 });
      await page.mouse.up();
      await page.waitForTimeout(300);
      ok('拖拽落位：保存按钮可用', await page.locator('[data-test=twin-save]').isEnabled());
      await page.screenshot({ path: path.join(OUT, `twin-drag-${tag}.png`) });
      await page.click('[data-test=twin-save]');
      await page.waitForTimeout(800);
      const b1 = (await api('GET', '/api/factory/devices?q=SHOT1', null, tok)).items[0];
      ok('拖拽落位：位置写进模型', Math.abs(b1.x - b0.x) > 0.5 || Math.abs(b1.y - b0.y) > 0.5, `(${b0.x}, ${b0.y}) → (${b1.x}, ${b1.y})`);
      await api('PUT', '/api/factory/layout/positions', { items: { SHOT1: { x: b0.x, y: b0.y } } }, tok);     // 放回原处
      await page.click('[data-test=twin-edit]');
    }
    await page.click('[data-test=twin-view] label:nth-child(2)');
    const canvas = await page.waitForSelector('[data-test=twin3d] canvas', { timeout: 15000 }).then(() => true).catch(() => false);
    ok(`${W} 3D 孪生：场景`, canvas);
    await page.waitForTimeout(4000);
    await shot('twin3d');

    // ---------- AI 辅助 ----------
    await page.goto(BASE + '/onboard/ai');
    await page.waitForSelector('[data-test=onboard-ai]', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(1500);
    const sw = await api('GET', '/api/ai/switches', null, tok);
    const aiOn = sw.global && sw.features && sw.features.onboard;
    ok(`${W} AI 辅助：开关状态显示正确`, aiOn ? (await page.locator('[data-test=ai-off]').count()) === 0 : (await page.locator('[data-test=ai-off]').count()) === 1);
    await shot('ai');

    // ---------- 首页平面图来自工厂模型 ----------
    await page.goto(BASE + '/');
    await page.waitForTimeout(3500);
    const st = await page.locator('g.st').count();
    ok(`${W} 首页平面图（工厂模型）`, st >= 15, `${st} 个设备`);
    await shot('home');
    await ctx.close();
  }
  await browser.close();
  ok('没有页面报错', errors.length === 0, errors.slice(0, 5).join(' | '));
  const bad = results.filter((r) => !r[1]).map((r) => r[0]);
  console.log(`\n${results.length - bad.length}/${results.length} 通过`, bad.length ? '；失败：' + bad.join('、') : '');
  process.exit(bad.length ? 1 : 0);
})();
