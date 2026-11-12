// Pinned to zh-CN (UI language + X-Lang): the assertions use the Chinese UI texts; tests/i18n_ui.js covers English.
// 内置 ERP 页面走查（Playwright）：销售 / 采购 / 计划 / 财务 / ERP 集成的每个页面在 1680×1050 和 1280×720 下打开、没有报错、关键区域在、截图；
// 流程：界面上建销售订单 → 确认 → 生成发货单 → 发货过账；运行 MRP → 看需求来源；生成凭证 → 看分录 → 导出预览；成本重算；
// 切到外部 ERP 模式 → 订单页只读；操作工看不到财务菜单、进财务页 403；开 ERP 助手后"AI 解释"MRP 建议（规则版）。
// 用法（B 机，MES 已启动且有演示数据）：node tests/erp_ui.js    MES_URL（默认 http://127.0.0.1:8900）；截图在 SHOTS_DIR（默认 data/ui_shots/erp）
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(process.env.APPDATA, 'npm', 'node_modules', 'playwright'));

const BASE = (process.env.MES_URL || 'http://127.0.0.1:8900').replace(/\/$/, '');
const OUT = process.env.SHOTS_DIR || path.join(__dirname, '..', 'data', 'ui_shots', 'erp');
fs.mkdirSync(OUT, { recursive: true });
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const results = [];
const ok = (name, cond, detail = '') => { results.push([name, !!cond]); console.log((cond ? 'PASS ' : 'FAIL ') + name, detail); };
async function api(method, p, body, token) {
  const r = await fetch(BASE + p, { method, headers: { 'Content-Type': 'application/json', 'X-Lang': 'zh-CN', ...(token ? { Authorization: 'Bearer ' + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return r.json().catch(() => ({}));
}
const PAGES = [
  ['/sales/board', '订单看板', '[data-tour=board-risk]'], ['/sales/orders', '销售订单', '[data-test=erp-table]'], ['/sales/quotes', '报价单', '[data-test=erp-table]'],
  ['/sales/deliveries', '发货单', '[data-test=erp-table]'], ['/sales/returns', '销售退货', '[data-test=erp-table]'], ['/purchase/board', '采购看板', '[data-tour=pb-open]'],
  ['/purchase/orders', '采购订单', '[data-test=erp-table]'], ['/purchase/receipts', '采购收货', '[data-test=erp-table]'], ['/purchase/returns', '退供应商', '[data-test=erp-table]'],
  ['/mrp', 'MRP 计划', '[data-test=mrp-table]'], ['/finance/cost', '成本核算', '[data-tour=cost-table]'], ['/finance/arap', '应收应付', '[data-test=ageing-table]'],
  ['/finance/ar_invoices', '销售发票', '[data-test=erp-table]'], ['/finance/ap_invoices', '采购发票', '[data-test=erp-table]'], ['/finance/vouchers', '凭证导出', '[data-test=v-table]'],
  ['/system/erp', 'ERP 集成', '[data-test=mode-radio]'], ['/e/customer', '客户', '[data-tour=ent-table]'], ['/e/supplier_material', '供货清单', '[data-tour=ent-table]'],
  ['/e/mat_plan', '物料计划参数', '[data-tour=ent-table]'], ['/e/cost_rate', '费率', '[data-tour=ent-table]'],
];

(async () => {
  const tok = (await api('POST', '/api/login', { username: 'admin', password: 'honey123' })).token;
  const opTok = (await api('POST', '/api/login', { username: 'operator', password: 'honey123' })).token;
  const SW0 = await api('GET', '/api/ai/switches', null, tok);
  await api('PUT', '/api/erp/mode', { mode: 'builtin' }, tok);
  // 成品库存：保证界面上的发货能过账（e2e 跑过时立库里也有成品）
  const fg = (await api('GET', '/api/e/material?q=SGH-450&size=5', null, tok)).items.find((m) => m.code === 'SGH-450');
  const fgw = (await api('GET', '/api/e/warehouse?size=50', null, tok)).items.find((w) => w.wtype === 'fg');
  if (fg && fgw) await api('POST', '/api/e/stock_txn', { ttype: 'in', material: fg.id, qty: 200, warehouse: fgw.id, reason: 'erp_ui 测试库存' }, tok);
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const mkCtx = async (t, w = 1680, h = 1050) => {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, locale: 'zh-CN', acceptDownloads: true });
    await ctx.addInitScript((x) => { if (window.top !== window || !window.localStorage) return; localStorage.setItem('mes_token', x); localStorage.setItem('hm_lang', 'zh-CN'); localStorage.setItem('mes_assist_open', '0');
      localStorage.setItem('hm_welcomed:admin', '1'); localStorage.setItem('hm_welcomed:operator', '1'); sessionStorage.removeItem('hm_tour'); }, t);
    return ctx;
  };
  const ctx = await mkCtx(tok);
  const page = await ctx.newPage();
  let errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });
  const settle = async (ms = 700) => { await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {}); await page.waitForTimeout(ms); };
  const shot = (n) => page.screenshot({ path: path.join(OUT, n + '.png') });
  const okBox = async () => { const b = page.locator('.el-message-box__btns .el-button--primary'); await b.waitFor({ timeout: 5000 }); await b.click(); };
  async function pick(sel, text, match) {
    await page.locator(sel).first().click();
    if (text) await page.keyboard.type(text, { delay: 30 });
    await page.waitForTimeout(600);
    const opt = page.locator('.el-select-dropdown:visible .el-select-dropdown__item', match || text ? { hasText: match || text } : {}).first();
    await opt.waitFor({ timeout: 8000 });
    await opt.click();
    await page.waitForTimeout(200);
  }

  // ---------- 1. 每个页面 × 两种尺寸 ----------
  for (const [w, h] of [[1680, 1050], [1280, 720]]) {
    await page.setViewportSize({ width: w, height: h });
    for (const [p, title, sel] of PAGES) {
      errors = [];
      await page.goto(BASE + p, { waitUntil: 'domcontentloaded' });
      await settle(900);
      const has = await page.locator(sel).count();
      const head = await page.locator('.hm-main h2, .hm-main .page-head').first().innerText().catch(() => '');
      const hscroll = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2);
      ok(`${w}×${h} ${p} 打开：${title}`, has > 0 && head.includes(title.slice(0, 2)) && !errors.length && !hscroll, errors.slice(0, 2).join(' | ') || (hscroll ? '横向滚动' : ''));
      await shot(`${w}-${p.replace(/^\//, '').replace(/[/?=&]/g, '_')}`);
    }
  }
  await page.setViewportSize({ width: 1680, height: 1050 });

  // ---------- 2. 销售订单 → 确认 → 发货单 → 过账 ----------
  errors = [];
  await page.goto(BASE + '/sales/orders'); await settle();
  await page.click('[data-test=erp-new]'); await page.waitForSelector('[data-test=erp-drawer] .head');
  await pick('.el-drawer .head .el-form-item:has-text("客户") .el-select', '北辰');   // demo customer C001 (demo data renamed for open source)
  await pick('.el-drawer [data-test=erp-lines] .el-table__row >> nth=0 >> .el-select', 'SGH-450', /^\s*SGH-450 /);
  const qty = page.locator('.el-drawer [data-test=erp-lines] .el-table__row').first().locator('.el-input-number input').first();
  await qty.fill('12'); await qty.press('Tab');
  await shot('flow-so-new');
  await page.click('[data-test=erp-save]'); await settle();
  const code = (await page.locator('.el-drawer__title, .el-drawer header').first().innerText()).match(/SO\d+/)?.[0];
  ok('界面新建销售订单：选客户、选物料、自动取价、保存', !!code && !errors.length, code || errors.join(' | '));
  await page.click('[data-test=act-confirm]'); await settle();
  const st1 = await page.locator('.el-drawer .bar .hm-tag').first().innerText();
  ok('确认订单 → 已确认', st1.includes('已确认'), st1);
  await shot('flow-so-confirmed');
  await page.click('[data-test=act-delivery]'); await settle(1200);
  ok('生成发货单 → 跳到发货单并打开', page.url().includes('/sales/deliveries') && (await page.locator('[data-test=act-post]').count()) === 1, page.url());
  await page.click('[data-test=act-post]'); await okBox(); await settle();
  const st2 = await page.locator('.el-drawer .bar .hm-tag').first().innerText();
  ok('发货过账 → 已发货', st2.includes('已发货'), st2);
  await shot('flow-delivery-posted');

  // ---------- 3. MRP ----------
  errors = [];
  await page.goto(BASE + '/mrp'); await settle();
  await page.click('[data-test=mrp-run]'); await settle(1500);
  const nS = await page.locator('[data-test=mrp-table] .el-table__row').count();
  ok('运行 MRP → 有建议', nS > 0 && !errors.length, `${nS} 条`);
  if (nS) {
    await page.locator('[data-test=mrp-peg]').first().click(); await page.waitForTimeout(800);
    ok('需求来源（pegging）抽屉', (await page.locator('.el-drawer:visible .peg').count()) > 0);
    await shot('flow-mrp-pegging');
    await page.keyboard.press('Escape'); await page.waitForTimeout(400);
  }
  await shot('flow-mrp');

  // ---------- 4. 凭证 ----------
  errors = [];
  await page.goto(BASE + '/finance/vouchers'); await settle();
  await page.click('[data-test=v-gen]'); await settle(1500);
  const nV = await page.locator('[data-test=v-table] .el-table__row').count();
  ok('生成凭证 → 凭证列表', nV > 0 && !errors.length, `${nV} 张`);
  if (nV) { await page.locator('[data-test=v-table] .el-table__row').first().click(); await settle(); }
  ok('点凭证看分录、导出预览', (await page.locator('[data-tour=v-lines] .el-table__row').count()) > 0 && (await page.locator('.pv tbody tr').count()) > 0);
  await shot('flow-vouchers');
  const dl = page.waitForEvent('download', { timeout: 10000 }).catch(() => null);
  if (nV) await page.click('[data-test=v-export]');
  const d = nV ? await dl : null;
  ok('导出（金蝶云星空 .xlsx）下载', !!d && /\.xlsx$/.test(d.suggestedFilename()), d && d.suggestedFilename());

  // ---------- 5. 成本 ----------
  errors = [];
  await page.goto(BASE + '/finance/cost'); await settle();
  await page.click('[data-test=cost-calc]'); await settle(1500);
  ok('重算工单成本', !errors.length && (await page.locator('[data-tour=cost-table]').count()) === 1);
  await shot('flow-cost');
  for (const [t, slug] of [['差异分析', 'variance'], ['存货计价', 'inventory'], ['月末结账', 'close']]) {
    await page.locator('.el-tabs__item', { hasText: t }).click(); await settle(600);
    await shot('flow-cost-' + slug);
  }
  ok('成本页签：差异 / 存货 / 结账', !errors.length, errors.join(' | '));

  // ---------- 6. 外部 ERP 模式 ----------
  errors = [];
  await page.goto(BASE + '/system/erp'); await settle();
  await page.locator('[data-test=mode-radio] .el-radio-button', { hasText: '外部 ERP' }).click();
  await page.click('[data-test=mode-save]'); await settle();
  ok('界面切到外部 ERP 模式', (await page.locator('[data-test=int-mode]').innerText()).includes('外部'));
  await shot('flow-int-mode');
  for (const [t, slug] of [['队列与失败', 'queue'], ['Webhook', 'webhook'], ['中间库', 'staging'], ['金蝶 / 用友', 'connectors']]) {
    await page.locator('.el-tabs__item', { hasText: t }).click(); await settle(600);
    await shot('flow-int-' + slug);
  }
  ok('集成页签：队列 / Webhook / 中间库 / 对接器', !errors.length, errors.join(' | '));
  await page.goto(BASE + '/sales/orders'); await settle(1500);
  ok('外部模式：销售订单页标"外部 ERP 做主：只读"、没有新建按钮', (await page.locator('[data-test=erp-owned]').count()) === 1 && (await page.locator('[data-test=erp-new]').count()) === 0);
  await shot('flow-external-readonly');
  await api('PUT', '/api/erp/mode', { mode: 'builtin' }, tok);

  // ---------- 7. AI 解释 MRP（规则版） ----------
  await api('PUT', '/api/ai/switches', { global: true, demo: false, features: { erp: true } }, tok);
  await page.goto(BASE + '/mrp'); await settle(1200);
  const btn = page.locator('[data-test=mrp-table] .el-button', { hasText: 'AI 解释' }).first();
  if (await btn.count()) {
    await btn.click(); await page.waitForSelector('.erp-ai-box', { timeout: 8000 }).catch(() => {});
    const txt = await page.locator('.erp-ai-text').innerText().catch(() => '');
    ok('AI 解释 MRP 建议（讲需求来源和净算）', txt.includes('净算') || txt.includes('需求'), txt.slice(0, 60));
    await shot('flow-ai-explain');
    await okBox().catch(() => {});
  } else ok('AI 解释 MRP 建议', false, '没有待处理建议');
  await api('PUT', '/api/ai/switches', SW0, tok);

  // ---------- 8. 权限：操作工 ----------
  const ctx2 = await mkCtx(opTok);
  const p2 = await ctx2.newPage();
  await p2.goto(BASE + '/'); await p2.waitForTimeout(2500);
  const navTxt = await p2.locator('[data-test=side-nav]').innerText().catch(() => '');
  // MOM 菜单：销售 / 财务都在"计划与业务"组里，按菜单项判断
  ok('操作工：菜单里没有财务菜单（成本核算 / 应收应付），有"计划与业务"分组（销售只读）', !navTxt.includes('成本核算') && !navTxt.includes('应收应付') && navTxt.includes('计划与业务'), navTxt.replace(/\s+/g, ' ').slice(0, 80));
  await p2.goto(BASE + '/finance/arap'); await p2.waitForTimeout(2000);
  ok('操作工：进应收应付 → 没有权限页', p2.url().includes('/forbidden'), p2.url());
  await p2.screenshot({ path: path.join(OUT, 'perm-operator.png') });
  await ctx2.close();

  await browser.close();
  const n = results.length, pass = results.filter((r) => r[1]).length;
  console.log(`\n${pass}/${n} 通过`);
  process.exit(pass === n ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
