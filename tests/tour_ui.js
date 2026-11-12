// Pinned to zh-CN (UI language + X-Lang): the assertions use the Chinese UI texts; tests/i18n_ui.js covers English.
// 操作导览走查（Playwright）：
//   1. 每个导览的每一步：直接跳到这一步（会先打开它的页面、点开详情），目标找得到、聚光框出现、步骤卡在屏幕里；
//   2. 三个任务带做（报工、质检、排产）：从第一步点「下一步」一路走到「完成」，跨页面跳转正确；
//   3. 首次登录自动放欢迎导览；「?」按钮打开本页导览；Ctrl+K 输入"导览"能找到并打开；
//   4. 刷新页面接着当前这一步；中途退出后再打开问"从第 N 步继续"。
// 用法（B 机，MES 已启动且有数据）：node tests/tour_ui.js    MES_URL / SHOTS_DIR 同 pages_ui.js
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(process.env.APPDATA, 'npm', 'node_modules', 'playwright'));

const BASE = (process.env.MES_URL || 'http://127.0.0.1:8900').replace(/\/$/, '');
const OUT = process.env.SHOTS_DIR || path.join(__dirname, '..', 'data', 'ui_shots', 'tour');
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
  const browser = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const errors = [];

  // ---------- 1. 欢迎导览（首次登录） ----------
  {
    const ctx = await browser.newContext({ viewport: { width: 1680, height: 1050 }, locale: 'zh-CN' });
    await ctx.addInitScript((t) => { if (!sessionStorage.getItem('init')) { sessionStorage.setItem('init', '1'); localStorage.clear(); localStorage.setItem('mes_token', t); localStorage.setItem('hm_lang', 'zh-CN'); localStorage.setItem('hm_tour_force_welcome', '1'); } }, tok);
    const page = await ctx.newPage(); PAGE = page;
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto(BASE + '/');
    const shown = await page.waitForSelector('[data-test=tour-overlay][data-guide=welcome]', { timeout: 8000 }).then(() => true).catch(() => false);
    ok('首次登录自动打开欢迎导览', shown);
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.join(OUT, 'welcome-1.png') });
    let steps = 0;
    for (let i = 0; i < 12; i++) {
      const done = await page.locator('[data-test=tour-close]').count();
      if (done) break;
      await page.click('[data-test=tour-next]'); steps++;
      await page.waitForTimeout(700);
      if (i === 1) await page.screenshot({ path: path.join(OUT, 'welcome-nav.png') });
      if (i === 4) await page.screenshot({ path: path.join(OUT, 'welcome-ai.png') });
    }
    ok('欢迎导览可以一路"下一步"走完', (await page.locator('[data-test=tour-close]').count()) === 1, `${steps} 步`);
    await page.click('[data-test=tour-close]');
    await page.reload(); await page.waitForTimeout(2500);
    ok('欢迎导览只自动放一次', (await page.locator('[data-test=tour-overlay]').count()) === 0);
    // 「?」按钮
    await page.goto(BASE + '/quality'); await page.waitForSelector('[data-tour=page-help]');
    await page.click('[data-tour=page-help]');
    await page.waitForSelector('[data-test=tour-overlay][data-guide=quality]', { timeout: 5000 }).catch(() => {});
    ok('页面标题旁的「?」打开本页导览', (await page.locator('[data-test=tour-overlay][data-guide=quality]').count()) === 1);
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(OUT, 'help-quality.png') });
    // 退出 → 再打开问"继续"
    await page.click('[data-test=tour-next]'); await page.waitForTimeout(500);
    await page.click('[data-test=tour-next]'); await page.waitForTimeout(500);
    await page.click('[data-test=tour-exit]'); await page.waitForTimeout(400);
    ok('退出导览', (await page.locator('[data-test=tour-overlay]').count()) === 0);
    await page.click('[data-tour=page-help]');
    await page.waitForSelector('.el-message-box', { timeout: 4000 }).catch(() => {});
    const mb = await page.locator('.el-message-box').innerText().catch(() => '');
    ok('再打开时问"从第 N 步继续"', mb.includes('从第 3 步继续'), mb.replace(/\s+/g, ' ').slice(0, 60));
    await page.click('.el-message-box .el-button--primary').catch(() => {});
    await page.waitForTimeout(800);
    ok('选"继续"后停在第 3 步', (await page.getAttribute('[data-test=tour-overlay]', 'data-step').catch(() => '')) === '2');
    // 刷新接着这一步
    await page.reload(); await page.waitForSelector('[data-test=tour-overlay]', { timeout: 6000 }).catch(() => {});
    ok('刷新页面后接着这一步', (await page.getAttribute('[data-test=tour-overlay]', 'data-step').catch(() => '')) === '2');
    // 收起 / 展开
    await page.click('[data-test=tour-min]'); await page.waitForTimeout(300);
    ok('收起成左下角的小条', (await page.locator('[data-test=tour-pill]').count()) === 1);
    await page.click('[data-test=tour-pill]');
    await page.evaluate(() => window.__hmTour.stopTour());
    // Ctrl+K
    await page.keyboard.press('Control+k');
    await page.waitForSelector('[data-test=palette]');
    await page.fill('[data-test=palette-input]', '导览');
    await page.waitForTimeout(400);
    const n = await page.locator('[data-test=palette] .po[data-kind=tour]').count();
    ok('Ctrl+K 输入"导览"列出操作导览', n >= 4, `${n} 项`);
    await page.fill('[data-test=palette-input]', '导览 质检');
    await page.waitForTimeout(300);
    await page.fill('[data-test=palette-input]', '质检');
    await page.waitForTimeout(300);
    const qc = page.locator('[data-test=palette] .po[data-kind=tour]', { hasText: '质检' }).first();
    const has = await qc.count();
    if (has) await qc.click();
    await page.waitForSelector('[data-test=tour-overlay][data-guide=task-quality]', { timeout: 6000 }).catch(() => {});
    ok('Ctrl+K 打开"任务带做：质检"', (await page.locator('[data-test=tour-overlay][data-guide=task-quality]').count()) === 1);
    await page.evaluate(() => window.__hmTour.stopTour());
    await ctx.close();
  }

  // ---------- 2. 每个导览的每一步：目标找得到 ----------
  const ctx = await browser.newContext({ viewport: { width: 1680, height: 1050 }, locale: 'zh-CN' });
  await ctx.addInitScript((t) => { localStorage.setItem('mes_token', t); localStorage.setItem('hm_lang', 'zh-CN'); localStorage.setItem('hm_welcomed:admin', '1'); localStorage.setItem('mes_assist_open', '0'); }, tok);
  const page = await ctx.newPage(); PAGE = page;
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(BASE + '/'); await page.waitForTimeout(1500);
  const guides = await page.evaluate(() => window.__hmTour.GUIDES.map((g) => ({ key: g.key, title: g.title, kind: g.kind, steps: g.steps.map((s) => ({ id: s.id, title: s.title, target: !!s.target })) })));
  const missing = [];
  let stepsChecked = 0;
  for (const g of guides) {
    if (g.key === 'entity') await page.goto(BASE + '/e/material');
    for (let i = 0; i < g.steps.length; i++) {
      await page.keyboard.press('Escape').catch(() => {});
      await page.evaluate(([k, i]) => { window.__hmTour.tour.hold = true; window.__hmTour.startGuide(k, i); }, [g.key, i]);
      await page.waitForFunction(() => document.querySelector('[data-test=tour-overlay]')?.getAttribute('data-busy') === '0', null, { timeout: 9000 }).catch(() => {});
      await page.waitForTimeout(g.steps[i].target ? 500 : 150);
      let found = true;
      if (g.steps[i].target) {
        found = await page.waitForFunction((k) => { const s = window.__hmTour.guideDef(k[0]).steps[k[1]]; return !!window.__hmTour.resolveTarget(s.target); }, [g.key, i], { timeout: 4000 }).then(() => true).catch(() => false);
      }
      const card = await page.evaluate(() => { const c = document.querySelector('[data-test=tour-card]'); if (!c) return null; const r = c.getBoundingClientRect(); return r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight; });
      stepsChecked++;
      if (!found || !card) missing.push(`${g.key}#${i + 1} ${g.steps[i].title}${!found ? '（目标找不到）' : ''}${!card ? '（卡片不在屏幕内）' : ''} @ ${new URL(page.url()).pathname}`);
      if (i === 1 && ['monitor', 'oee', 'task-report', 'task-quality', 'task-plan', 'spc', 'andon'].includes(g.key)) await page.screenshot({ path: path.join(OUT, `step-${g.key}-${i + 1}.png`) });
    }
    await page.evaluate(() => window.__hmTour.stopTour());
  }
  ok(`每个导览的每一步目标都找得到（${guides.length} 个导览，${stepsChecked} 步）`, !missing.length, missing.slice(0, 12).join('\n   '));

  // ---------- 3. 三个任务带做：一路走完 ----------
  for (const key of ['task-report', 'task-quality', 'task-plan']) {
    await page.goto(BASE + '/'); await page.waitForTimeout(800);
    await page.evaluate((k) => { window.__hmTour.tour.hold = false; window.__hmTour.startGuide(k, 0); }, key);
    const paths = [], found = [];
    for (let i = 0; i < 12; i++) {
      await page.waitForFunction(() => document.querySelector('[data-test=tour-overlay]')?.getAttribute('data-busy') === '0', null, { timeout: 9000 }).catch(() => {});
      await page.waitForTimeout(700);
      if (await page.locator('[data-test=tour-close]').count()) break;
      paths.push(new URL(page.url()).pathname);
      found.push(await page.getAttribute('[data-test=tour-overlay]', 'data-found'));
      await page.screenshot({ path: path.join(OUT, `${key}-${i + 1}.png`) });
      await page.click('[data-test=tour-next]');
    }
    const finished = (await page.locator('[data-test=tour-close]').count()) === 1;
    ok(`任务带做 ${key}：走完 ${paths.length} 步，每步都聚光到目标`, finished && found.every((f) => f === '1'), `${paths.join(' → ')} | found ${found.join('')}`);
    await page.click('[data-test=tour-close]').catch(() => {});
    await page.keyboard.press('Escape').catch(() => {});
  }
  const real = errors.filter((e) => !/ResizeObserver|favicon/.test(e));
  ok('浏览器没有脚本报错', !real.length, real.slice(0, 3).join(' | '));
  await browser.close();
  const bad = results.filter((r) => !r[1]);
  console.log(`\n${results.length - bad.length}/${results.length} 通过` + (bad.length ? '；失败：' + bad.map((r) => r[0]).join('、') : '') + `\n截图：${OUT}`);
  process.exit(bad.length ? 1 : 0);
})().catch(async (e) => {
  console.error(e);
  try { await PAGE.screenshot({ path: path.join(OUT, 'zz-failure.png') }); } catch { /* */ }
  process.exit(2);
});
