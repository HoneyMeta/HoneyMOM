// UI language walk-through (Playwright): English is the default, Chinese is switchable (top bar / login page), the choice persists,
// API calls carry X-Lang, nav / home cockpit / several pages render in both languages, no Chinese is left in English mode,
// and longer English strings don't break the layout at 1280×720 and 1680×1050. Screenshots for both languages.
// Usage (machine B, MES running with data): node tests/i18n_ui.js
//   MES_URL (default http://127.0.0.1:8900); SHOTS_DIR for screenshots (default data/ui_shots/i18n)
//   I18N_DATA=en when the demo dataset was seeded in English (MOM_DEMO_LANG=en): then the whole visible page text must be free of Chinese;
//   otherwise (Chinese demo data) only UI chrome is scanned: menus, top bar, page heads, table headers, buttons, form labels, placeholders, tabs.
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(process.env.APPDATA, 'npm', 'node_modules', 'playwright'));

const BASE = (process.env.MES_URL || 'http://127.0.0.1:8900').replace(/\/$/, '');
const OUT = process.env.SHOTS_DIR || path.join(__dirname, '..', 'data', 'ui_shots', 'i18n');
const DATA_EN = (process.env.I18N_DATA || '') === 'en';
fs.mkdirSync(OUT, { recursive: true });
const results = [];
const ok = (name, cond, detail = '') => { results.push([name, !!cond]); console.log((cond ? 'PASS ' : 'FAIL ') + name, detail); };
const CJK = /[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]/;

async function api(method, p, body, token) {
  const r = await fetch(BASE + p, { method, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return r.json();
}

const PAGES = ['/', '/e/work_order', '/production/gantt', '/andon', '/quality', '/quality/fai', '/iatf/pfmea', '/equipment/monitor', '/equipment/oee',
  '/equipment/maint', '/warehouse', '/exec/pack', '/sales/board', '/mrp', '/onboard', '/approvals', '/system/roles', '/system'];
const GROUPS = {
  en: ['Home', 'Production Operations', 'Quality Operations', 'Maintenance Operations', 'Inventory Operations', 'Planning & Business', 'Factory Model & Connectivity', 'Intelligence', 'System'],
  'zh-CN': ['\u5de5\u4f5c\u53f0', '\u751f\u4ea7\u8fd0\u8425', '\u8d28\u91cf\u8fd0\u8425', '\u7ef4\u62a4\u8fd0\u8425', '\u5e93\u5b58\u8fd0\u8425', '\u8ba1\u5212\u4e0e\u4e1a\u52a1', '\u5de5\u5382\u6a21\u578b\u4e0e\u63a5\u5165', '\u667a\u80fd', '\u7cfb\u7edf'],
};

// Chinese left in UI chrome (or anywhere when the dataset is English); returns [{ sel, text }]
const scanCjk = (page, full) => page.evaluate(({ full, re }) => {
  const R = new RegExp(re);
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const out = [];
  const sels = full ? ['body'] : ['.hm-nav', '.hm-top', '[data-test=breadcrumbs]', '.hm-ph', 'th', '.el-table__header', '.el-button', '.el-form-item__label',
    '.el-tabs__item', '.el-radio-button', '.hm-kpi .lbl', '.hm-kpi .label', '.hm-card-h', 'h1', 'h2', 'h3', '.el-empty__description', '.hm-empty'];
  for (const s of sels) for (const el of document.querySelectorAll(s)) {
    if (!vis(el)) continue;
    // ignore data cells inside table bodies and record values; chrome only (unless full)
    // language names in the switcher are allowed
    const t = (el.innerText || '').split('\n').filter((x) => R.test(x) && !/^\s*(中|中文)\s*$/.test(x));
    for (const x of t) out.push({ sel: s, text: x.slice(0, 80) });
  }
  for (const el of document.querySelectorAll('input[placeholder], textarea[placeholder]')) if (vis(el) && R.test(el.placeholder)) out.push({ sel: 'placeholder', text: el.placeholder });
  const seen = new Set();
  return out.filter((x) => (seen.has(x.text) ? false : (seen.add(x.text), true))).slice(0, 12);
}, { full, re: CJK.source });

// layout breakage: horizontal page scroll, top bar / page head overflow, clipped buttons
const layoutIssues = (page) => page.evaluate(() => {
  const iss = [];
  const de = document.documentElement;
  if (de.scrollWidth > innerWidth + 2) iss.push('page scrolls horizontally ' + de.scrollWidth + '>' + innerWidth);
  const top = document.querySelector('.hm-top');
  if (top && top.scrollWidth > top.clientWidth + 2) iss.push('top bar overflows ' + top.scrollWidth + '>' + top.clientWidth);
  const main = document.querySelector('.hm-main');
  if (main && main.scrollWidth > main.clientWidth + 4) iss.push('main area overflows ' + main.scrollWidth + '>' + main.clientWidth);
  for (const ph of document.querySelectorAll('.hm-ph')) if (ph.scrollWidth > ph.clientWidth + 4) iss.push('page head overflows');
  const clipped = [];
  for (const b of document.querySelectorAll('.hm-main .el-button, .hm-top button')) {
    const r = b.getBoundingClientRect(); if (!r.width) continue;
    const span = b.querySelector('span') || b; if (span.scrollWidth > b.clientWidth + 6) clipped.push((b.innerText || b.getAttribute('aria-label') || '?').trim().slice(0, 30));
  }
  if (clipped.length) iss.push('clipped buttons: ' + clipped.join(', '));
  return iss;
});

(async () => {
  const tok = (await api('POST', '/api/login', { username: 'admin', password: 'honey123' })).token;
  const browser = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const errors = [];

  // ---------- 1. login page: English by default, switch to Chinese and back ----------
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });   // browser locale must NOT decide the UI language
    const page = await ctx.newPage();
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto(BASE + '/login');
    await page.waitForSelector('[data-test=lang-switch]');
    const t0 = await page.locator('.box').innerText();
    ok('login defaults to English', /Sign in/i.test(t0) && !CJK.test(t0.replace(/\u4e2d\u6587/g, '')), t0.slice(0, 60).replace(/\n/g, ' '));
    ok('login subtitle names MOM', (await page.locator('.brand').innerText()).includes('MOM'));
    await page.click('[data-test="lang-zh-CN"]');
    await page.waitForFunction(() => /\u767b\u5f55/.test(document.querySelector('.box')?.innerText || ''));
    ok('login switches to Chinese', true);
    ok('choice stored in localStorage', (await page.evaluate(() => localStorage.getItem('hm_lang'))) === 'zh-CN');
    await page.screenshot({ path: path.join(OUT, 'login_zh.png') });
    await page.click('[data-test="lang-en"]');
    await page.waitForFunction(() => /Sign in/i.test(document.querySelector('.box')?.innerText || ''));
    await page.screenshot({ path: path.join(OUT, 'login_en.png') });
    ok('login switches back to English', true);
    await ctx.close();
  }

  for (const lang of ['en', 'zh-CN']) {
    const ctx = await browser.newContext({ viewport: { width: 1680, height: 1050 }, locale: 'en-US' });
    await ctx.addInitScript(([t, l]) => {
      if (!sessionStorage.getItem('init')) {
        sessionStorage.setItem('init', '1'); localStorage.setItem('mes_token', t); localStorage.setItem('mes_assist_open', '0');
        localStorage.removeItem('hm_nav_collapsed'); localStorage.setItem('hm_lang', l);
      }
    }, [tok, lang]);
    const page = await ctx.newPage();
    page.on('pageerror', (e) => errors.push(lang + ' ' + String(e)));
    page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(lang + ' ' + m.text()); });
    const hdr = [];
    page.on('request', (r) => { if (r.url().includes('/api/') && r.resourceType() === 'fetch') hdr.push(r.headers()['x-lang'] || ''); });
    const L = lang === 'en' ? 'EN' : 'ZH';

    // ---------- 2. home: nav groups + MOM cockpit ----------
    await page.goto(BASE + '/');
    await page.waitForSelector('[data-test=side-nav]');
    await page.waitForSelector('[data-test=mom-domains] .hm-kpi');
    await page.waitForTimeout(1500);
    const nav = await page.locator('.hm-nav').innerText();
    const miss = GROUPS[lang].filter((g) => !nav.includes(g));
    ok(`${L} nav shows the MOM groups`, !miss.length, miss.join(', '));
    ok(`${L} html lang`, (await page.evaluate(() => document.documentElement.lang)) === lang);
    const doms = await page.locator('[data-test=mom-domains] section').count();
    ok(`${L} home cockpit has four operations domains`, doms === 4, String(doms));
    const momText = await page.locator('[data-test=mom-domains]').innerText();
    ok(`${L} cockpit domain titles`, GROUPS[lang].slice(1, 5).every((g) => momText.includes(g)));
    ok(`${L} cockpit KPIs filled`, (await page.locator('[data-test=mom-domains] .hm-kpi').count()) >= 16);
    ok(`${L} API calls send X-Lang`, hdr.length > 3 && hdr.every((h) => h === lang), `${hdr.length} calls, ${[...new Set(hdr)].join('/')}`);
    const meta = await page.evaluate(async () => (await (await fetch('/api/meta', { headers: { Authorization: 'Bearer ' + localStorage.getItem('mes_token'), 'X-Lang': document.documentElement.lang } })).json()));
    const wo = (meta.entities || []).find((e) => e.name === 'work_order');
    ok(`${L} /api/meta labels follow the language`, wo && (lang === 'en' ? !CJK.test(wo.label) : CJK.test(wo.label)), wo && wo.label);
    await page.screenshot({ path: path.join(OUT, `home_${lang}_1680.png`) });

    // ---------- 3. pages: no Chinese left in English mode, Chinese present in zh mode, layout at 1280 / 1680 ----------
    for (const vp of [[1680, 1050], [1280, 720]]) {
      await page.setViewportSize({ width: vp[0], height: vp[1] });
      for (const p of PAGES) {
        await page.goto(BASE + p);
        await page.waitForSelector('.hm-main', { timeout: 15000 }).catch(() => {});
        await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
        await page.waitForTimeout(600);
        if (lang === 'en') {
          const left = await scanCjk(page, DATA_EN);
          ok(`EN ${p} @${vp[0]} no Chinese in ${DATA_EN ? 'page' : 'UI chrome'}`, !left.length, left.map((x) => `${x.sel}: ${x.text}`).join(' | '));
        } else if (vp[0] === 1680) {
          const chrome = await page.locator('.hm-nav').innerText();
          ok(`ZH ${p} shows Chinese UI`, CJK.test(chrome));
        }
        const iss = await layoutIssues(page);
        ok(`${L} ${p} @${vp[0]} layout`, !iss.length, iss.join('; '));
        const name = (p === '/' ? 'home' : p.slice(1).replace(/\//g, '_'));
        if (vp[0] === 1280 || ['/', '/e/work_order', '/quality', '/equipment/monitor', '/warehouse', '/sales/board'].includes(p)) await page.screenshot({ path: path.join(OUT, `${name}_${lang}_${vp[0]}.png`) });
      }
    }

    // ---------- 4. top-bar switch: switch language in place ----------
    await page.setViewportSize({ width: 1680, height: 1050 });
    await page.goto(BASE + '/equipment/oee');
    await page.waitForSelector('[data-test=lang-switch]');
    const other = lang === 'en' ? 'zh-CN' : 'en';
    await Promise.all([page.waitForEvent('load'), page.click(`[data-test="lang-${other}"]`)]);
    await page.waitForSelector('[data-test=side-nav]');
    const nav2 = await page.locator('.hm-nav').innerText();
    ok(`${L} top-bar switch → ${other}`, GROUPS[other].slice(1).every((g) => nav2.includes(g)) && page.url().includes('/equipment/oee'));
    ok(`${L} switch persisted`, (await page.evaluate(() => localStorage.getItem('hm_lang'))) === other);
    await ctx.close();
  }

  ok('no page errors', !errors.length, errors.slice(0, 5).join(' | '));
  await browser.close();
  const pass = results.filter((r) => r[1]).length;
  console.log(`${pass}/${results.length} passed`);
  process.exit(pass === results.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
