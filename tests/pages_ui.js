// Pinned to zh-CN (UI language + X-Lang): the assertions use the Chinese UI texts; tests/i18n_ui.js covers English.
// 全部页面走查（Playwright）：左侧菜单里的每个页面 × 1280×720 / 1680×1050 / 2560×1440，
// 检查：控制台没有报错、没有横向滚动、文字不互相压住、文字不被截断（没有省略号的截断）、文字对比度 ≥ 3、页面头部有「?」操作导览入口；每页截图。
// 扫描方法从虚拟工厂 tests/ui_scan.js 移植（tests/ui_scan.js）。
// 用法（B 机，MES 已启动且有数据，比如先跑过 tests/e2e.py）：node tests/pages_ui.js
//   MES_URL（默认 http://127.0.0.1:8900）；SHOTS_DIR 截图目录（默认 data/ui_shots/pages）；ONLY=/quality,/energy 只走这几个页面；SIZES=1680x1050 只走这个尺寸
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(process.env.APPDATA, 'npm', 'node_modules', 'playwright'));
const { SCAN } = require('./ui_scan');

const BASE = (process.env.MES_URL || 'http://127.0.0.1:8900').replace(/\/$/, '');
const OUT = process.env.SHOTS_DIR || path.join(__dirname, '..', 'data', 'ui_shots', 'pages');
fs.mkdirSync(OUT, { recursive: true });
const SIZES = (process.env.SIZES || '1280x720,1680x1050,2560x1440').split(',').map((s) => s.split('x').map(Number));
// 页面归属（谁负责改），只用于报告分组
const OWNER = (p) => (['/', '/screen'].includes(p) ? 'H1' : ['/m', '/station', '/e/report', '/production/gantt', '/e/schedule_proposal', '/e/rework', '/trace', '/trace/recon', '/e/kb_doc', '/e/ncr', '/e/part', '/labor/daily', '/labor/hours', '/quality/fai', '/quality/hold', '/quality/rework'].includes(p) ? 'H3'
  : ['/ai/daily', '/ai/chat', '/ai/watch', '/ai/automations', '/agents', '/approvals'].includes(p) ? 'H2' : 'H4');

async function api(method, p, body, token) {
  const r = await fetch(BASE + p, { method, headers: { 'Content-Type': 'application/json', 'X-Lang': 'zh-CN', ...(token ? { Authorization: 'Bearer ' + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return r.json();
}
/** 菜单里的页面（从 web/src/layout/nav.ts 读） */
function navRoutes() {
  const src = fs.readFileSync(path.join(__dirname, '..', 'web', 'src', 'layout', 'nav.ts'), 'utf8');
  const out = [];
  for (const m of src.matchAll(/\{ path: '([^']+)', label: '([^']+)'/g)) if (!out.some((x) => x.path === m[1])) out.push({ path: m[1], label: m[2] });
  return out;
}
const slug = (p) => (p === '/' ? 'home' : p.replace(/^\//, '').replace(/[/?=&]/g, '_'));

(async () => {
  const tok = (await api('POST', '/api/login', { username: 'admin', password: 'honey123' })).token;
  let routes = navRoutes();
  if (process.env.ONLY) routes = routes.filter((r) => process.env.ONLY.split(',').includes(r.path));
  const browser = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const ctx = await browser.newContext({ viewport: { width: 1680, height: 1050 }, locale: 'zh-CN' });
  await ctx.addInitScript((t) => {
    if (window.top !== window || !window.localStorage) return;      // 只在顶层页面（PDF 阅读器等内部 frame 里没有 storage）
    localStorage.setItem('mes_token', t); localStorage.setItem('hm_lang', 'zh-CN'); localStorage.setItem('mes_assist_open', '0'); localStorage.setItem('hm_welcomed:admin', '1'); localStorage.removeItem('hm_nav_collapsed');
    sessionStorage.removeItem('hm_tour');
  }, tok);
  await ctx.addInitScript({ content: SCAN });
  const page = await ctx.newPage();
  let errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });

  const report = [];
  let pass = 0, fail = 0;
  for (const [w, h] of SIZES) {
    await page.setViewportSize({ width: w, height: h });
    for (const r of routes) {
      errors = [];
      const t0 = Date.now();
      await page.goto(BASE + r.path, { waitUntil: 'domcontentloaded' });
      await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
      await page.waitForTimeout(900);
      const res = await page.evaluate(() => {
        const S = window.__hmScan, main = document.querySelector('.hm-main') || document.body;
        const hs = S.hscroll();
        return {
          hscroll: Math.max(hs.doc, hs.main),
          overlaps: S.overlaps(main).filter((o) => o.where !== 'nav').slice(0, 6),
          clipped: S.clipped(main).slice(0, 6),
          contrast: S.lowContrast(main, 3).slice(0, 6),
          help: !!document.querySelector('[data-tour=page-help], [data-tour=shell-help]'),
          head: !!document.querySelector('[data-tour=page-head]'),
          title: (document.querySelector('.hm-main h2') || {}).innerText || '',
          scale: getComputedStyle(document.documentElement).getPropertyValue('--hm-scale').trim(),
        };
      });
      const shellFree = /^\/(m|screen|station)$/.test(r.path);
      const errs = errors.filter((e) => !/ResizeObserver|favicon|net::ERR_ABORTED/.test(e));
      const probs = [];
      if (errs.length) probs.push('console: ' + errs.slice(0, 2).join(' | ').slice(0, 200));
      if (res.hscroll > 1) probs.push(`横向滚动 ${res.hscroll}px`);
      if (res.overlaps.length) probs.push('文字重叠: ' + res.overlaps.map((o) => `"${o.a}"×"${o.b}"`).join('; ').slice(0, 200));
      if (res.clipped.length) probs.push('文字截断: ' + res.clipped.map((c) => c.el).join('; ').slice(0, 200));
      if (res.contrast.length) probs.push('对比度<3: ' + res.contrast.map((c) => `"${c.text}" ${c.ratio}`).join('; ').slice(0, 200));
      if (!shellFree && !res.help) probs.push('没有「?」操作导览入口');
      const ok = !probs.length;
      ok ? pass++ : fail++;
      const shot = `${slug(r.path)}-${w}x${h}.png`;
      await page.screenshot({ path: path.join(OUT, shot) });
      report.push({ path: r.path, label: r.label, owner: OWNER(r.path), size: `${w}x${h}`, ok, probs, scale: res.scale, ms: Date.now() - t0, shot, headComponent: res.head });
      console.log(`${ok ? 'PASS' : 'FAIL'} [${OWNER(r.path)}] ${w}x${h} ${r.path} ${r.label}${probs.length ? ' — ' + probs.join(' / ') : ''}`);
    }
  }
  // 登录页（未登录）
  const anon = await browser.newContext({ viewport: { width: 1680, height: 1050 }, locale: 'zh-CN' });
  await anon.addInitScript({ content: SCAN });
  const lp = await anon.newPage();
  for (const [w, h] of SIZES) {
    await lp.setViewportSize({ width: w, height: h });
    await lp.goto(BASE + '/login'); await lp.waitForTimeout(900);
    const r = await lp.evaluate(() => ({ hs: window.__hmScan.hscroll().doc, ov: window.__hmScan.overlaps(document.body).length, hero: !!document.querySelector('.hero') && document.querySelector('.hero').naturalWidth > 0 }));
    const ok = r.hs <= 1 && !r.ov;
    ok ? pass++ : fail++;
    await lp.screenshot({ path: path.join(OUT, `login-${w}x${h}.png`) });
    report.push({ path: '/login', owner: 'H4', size: `${w}x${h}`, ok, probs: ok ? [] : [`hscroll ${r.hs} overlaps ${r.ov}`], hero: r.hero });
    console.log(`${ok ? 'PASS' : 'FAIL'} [H4] ${w}x${h} /login 登录${r.hero ? '（插画已加载）' : '（插画没加载）'}`);
  }
  await browser.close();
  fs.writeFileSync(path.join(OUT, 'pages_ui.json'), JSON.stringify(report, null, 1));
  const byOwner = {};
  for (const x of report) { const o = (byOwner[x.owner] ||= { pass: 0, fail: 0 }); x.ok ? o.pass++ : o.fail++; }
  console.log(`\n${pass}/${pass + fail} 通过（${routes.length} 个页面 × ${SIZES.length} 个尺寸 + 登录页）。按负责人：${Object.entries(byOwner).map(([k, v]) => `${k} ${v.pass}/${v.pass + v.fail}`).join('，')}\n截图：${OUT}`);
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
