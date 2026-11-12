// First-run walk-through (Playwright) on a PRODUCTION install with no accounts (no MOM_DEMO): the login page shows the
// "Create administrator" form in English and Chinese, validates the password, creates the admin and lands in the set-up wizard.
// Usage (machine B, MES started without MOM_DEMO on an empty data dir): node tests/setup_ui.js
//   MES_URL (default http://127.0.0.1:8900); SHOTS_DIR for screenshots (default data/ui_shots/setup)
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(process.env.APPDATA, 'npm', 'node_modules', 'playwright'));

const BASE = (process.env.MES_URL || 'http://127.0.0.1:8900').replace(/\/$/, '');
const OUT = process.env.SHOTS_DIR || path.join(__dirname, '..', 'data', 'ui_shots', 'setup');
fs.mkdirSync(OUT, { recursive: true });
const results = [];
const ok = (name, cond, detail = '') => { results.push([name, !!cond]); console.log((cond ? 'PASS ' : 'FAIL ') + name, detail); };
const CJK = /[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]/;

(async () => {
  const st = await (await fetch(BASE + '/api/setup/admin')).json();
  ok('API: administrator still needed', st.needed === true && st.demo === false, JSON.stringify(st));
  const browser = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(BASE + '/login');
  await page.waitForSelector('[data-test=setup-admin]');
  const box = async () => page.locator('.box').innerText();
  const en = await box();
  ok('EN: create-administrator form', /Create Administrator/.test(en) && !/Sign in/.test(en) && !CJK.test(en.replace(/\u4e2d\u6587/g, '')), en.slice(0, 80).replace(/\n/g, ' '));
  ok('EN: no demo accounts hint in production', !(await page.locator('.demo').count()));
  await page.screenshot({ path: path.join(OUT, 'setup_en.png') });
  await page.click('[data-test="lang-zh-CN"]');
  await page.waitForFunction(() => /\u521b\u5efa\u7ba1\u7406\u5458/.test(document.querySelector('.box')?.innerText || ''));
  ok('ZH: form switches to Chinese', true);
  await page.screenshot({ path: path.join(OUT, 'setup_zh.png') });
  await page.click('[data-test="lang-en"]');
  await page.waitForSelector('[data-test=setup-admin]');
  const f = page.locator('[data-test=setup-admin] input');
  await f.nth(0).fill('Plant Admin'); await f.nth(1).fill('admin'); await f.nth(2).fill('short'); await f.nth(3).fill('short');
  await page.click('[data-test=setup-admin] .go');
  await page.waitForSelector('.el-message--error');
  ok('EN: short password rejected', /at least 8/.test(await page.locator('.el-message--error').last().innerText()));
  await f.nth(2).fill('honeymom-2026'); await f.nth(3).fill('honeymom-2026');
  await page.click('[data-test=setup-admin] .go');
  await page.waitForURL(/\/impl\/setup/, { timeout: 15000 }).catch(() => {});
  ok('admin created, set-up wizard opens', page.url().includes('/impl/setup'), page.url());
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(OUT, 'setup_wizard_en.png') });
  const st2 = await (await fetch(BASE + '/api/setup/admin')).json();
  ok('API: no longer needed', st2.needed === false);
  const login = await (await fetch(BASE + '/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'admin', password: 'honeymom-2026' }) })).json();
  ok('new admin can sign in', !!login.token);
  ok('no page errors', !errors.length, errors.slice(0, 3).join(' | '));
  await browser.close();
  const pass = results.filter((r) => r[1]).length;
  console.log(`${pass}/${results.length} passed`);
  process.exit(pass === results.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
