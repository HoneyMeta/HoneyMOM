// Pinned to zh-CN (UI language + X-Lang): the assertions use the Chinese UI texts; tests/i18n_ui.js covers English.
// 现场执行与质量合规（M5）页面走查（Playwright）：标签设计、包装工位、装车发运、ECN、量检具（电子签名放行）、PFMEA / 控制计划、MSA、客诉、
// 批次解冻的电子签名框（质量关卡页），工位终端和手机端（390×844）的扫码装箱 / 受控文件。截图 1680×1050（手机 / 终端 390×844）。
// 用法（B 机，MES 已启动、已跑过 tests/e2e.py）：node tests/exec_ui.js    MES_URL（默认 http://127.0.0.1:8900）；SHOTS_DIR（默认 data/ui_shots/exec）
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(process.env.APPDATA, 'npm', 'node_modules', 'playwright'));

const BASE = (process.env.MES_URL || 'http://127.0.0.1:8900').replace(/\/$/, '');
const OUT = process.env.SHOTS_DIR || path.join(__dirname, '..', 'data', 'ui_shots', 'exec');
fs.mkdirSync(OUT, { recursive: true });
const RUN = Math.random().toString(36).slice(2, 7).toUpperCase();
const results = [];
const ok = (name, cond, detail = '') => { results.push([name, !!cond]); console.log((cond ? 'PASS ' : 'FAIL ') + name, String(detail).slice(0, 200)); };
let TOK = '';
async function api(method, p, body, token = TOK) {
  const r = await fetch(BASE + p, { method, headers: { 'Content-Type': 'application/json', 'X-Lang': 'zh-CN', ...(token ? { Authorization: 'Bearer ' + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return r.json().catch(() => ({}));
}
const I = (t) => `input[data-test=${t}], [data-test=${t}] input`;     // el-input 把 data-test 放在原生 input 上
const SIG = { password: 'honey123', meaning: '放行' };
async function pushParts(n, heat) {
  const now = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 19).replace('T', ' ');
  const sns = Array.from({ length: n }, (_, i) => `U${heat}${String(i).padStart(3, '0')}`);
  const msgs = [...sns.map((sn) => ({ sn, event: 'poured', station: 'MOLD1', t: now, type: 'SGH-450', attrs: { heat } })),
    ...sns.map((sn) => ({ sn, event: 'inspected', station: 'INS1', t: now, data: { result: 'OK' } }))];
  await api('POST', '/api/ingest/part', { src: 'UI', messages: msgs });
  for (let i = 0; i < 60; i++) {
    const r = await api('GET', `/api/e/part?f_heat=${heat}&f_status=good&size=50`);
    if ((r.items || []).length >= n) break;
    await new Promise((res) => setTimeout(res, 500));
  }
  return sns;
}

let PAGE = null;
(async () => {
  TOK = (await api('POST', '/api/login', { username: 'admin', password: 'honey123' })).token;
  const QC = (await api('POST', '/api/login', { username: 'qc', password: 'honey123' })).token;
  const browser = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const mk = async (w, h, t = TOK) => {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, locale: 'zh-CN' });
    await ctx.addInitScript((tk) => { if (window.top !== window) return; localStorage.setItem('mes_token', tk); localStorage.setItem('hm_lang', 'zh-CN'); localStorage.setItem('mes_assist_open', '0'); localStorage.setItem('hm_welcomed:admin', '1'); localStorage.setItem('hm_welcomed:qc', '1'); }, t);
    const p = await ctx.newPage();
    p.errors = [];
    p.on('pageerror', (e) => p.errors.push(String(e)));
    p.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource|status of 4\d\d/.test(m.text())) p.errors.push(m.text()); });
    return p;
  };
  const go = async (p, url, sel) => {
    await p.goto(BASE + url, { waitUntil: 'domcontentloaded' });
    await p.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
    if (sel) await p.waitForSelector(sel, { timeout: 15000 });
    await p.waitForTimeout(500);
  };
  const shot = (p, name) => p.screenshot({ path: path.join(OUT, name + '.png'), fullPage: false });
  async function sign(p) {           // 电子签名框：输密码 → 签名并提交
    await p.waitForSelector('[data-test=esign-box]', { timeout: 10000 });
    await p.fill(I('esign-pw'), 'honey123');
    await p.click('.hm-esign-box .el-message-box__btns .el-button--primary');
    await p.waitForTimeout(1200);
  }

  try {
    const page = PAGE = await mk(1680, 1050);
    // ---------- 1. 标签设计 ----------
    await go(page, '/exec/labels', '[data-test=lbl-canvas]');
    await page.click('[data-test=lbl-tpl-list] >> text=箱标签').catch(() => {});
    await page.waitForTimeout(600);
    const n0 = await page.locator('[data-test=lbl-canvas] g.el').count().catch(() => 0);
    await page.click('[data-test=lbl-add-text]');
    await page.waitForTimeout(300);
    const n1 = await page.locator('[data-test=lbl-canvas] g.el').count().catch(() => 0);
    ok('标签设计：画布渲染模板元素，"文字"新增一个元素', n1 > 0 && n1 === n0 + 1, `${n0} → ${n1}`);
    await page.click('[data-test=lbl-zpl]');
    await page.waitForTimeout(1200);
    const zpl = await page.locator('[data-test=lbl-payload]').innerText().catch(() => '');
    ok('标签设计：ZPL 预览（服务端生成）', zpl.includes('^XA') && zpl.includes('^XZ'), zpl.slice(0, 40));
    await shot(page, 'labels-designer');
    await page.keyboard.press('Escape');
    await page.click('[data-test=lbl-tab-devices]').catch(() => {});
    await page.waitForTimeout(600);
    ok('标签：打印机与打码机列表', await page.locator('[data-test=lbl-printers]').isVisible().catch(() => false));
    await shot(page, 'labels-devices');

    // ---------- 2. 包装工位 / 装车 ----------
    const heat = 'UI' + RUN;
    const sns = await pushParts(3, heat);
    await go(page, '/exec/pack', '[data-test=pack-scan]');
    await page.fill(I('pack-station'), 'UIPK' + RUN).catch(() => {});
    for (const sn of sns.slice(0, 2)) { await page.fill(I('pack-scan'), sn); await page.press(I('pack-scan'), 'Enter'); await page.waitForTimeout(900); }
    const ct = await page.locator('[data-test=pack-carton]').innerText().catch(() => '');
    ok('包装工位：扫零件码装箱，当前箱显示 2 件', /2\s*\/\s*20/.test(ct), ct.replace(/\s+/g, ' ').slice(0, 80));
    await page.fill(I('pack-scan'), sns[0]); await page.press(I('pack-scan'), 'Enter'); await page.waitForTimeout(900);
    const msg = await page.locator('[data-test=pack-msg]').innerText().catch(() => '');
    ok('包装工位：重复扫同一件 → 红色提示"已经装在"', msg.includes('已经装在'), msg.slice(0, 80));
    await shot(page, 'pack-station');
    await page.click('[data-test=pack-close]').catch(() => {});
    await page.waitForTimeout(800);
    await go(page, '/exec/ship', '[data-test=ship-loads]');
    ok('装车发运页', await page.locator('[data-test=ship-new]').isVisible());
    await shot(page, 'ship');

    // ---------- 3. ECN ----------
    const mat = (await api('GET', '/api/e/material?f_code=SGH-450')).items[0];
    const e = await api('POST', '/api/e/ecn', { title: `界面测试 ECN ${RUN}`, product: mat.id, wo_policy: 'finish', reason: 'exec_ui' });
    await go(page, '/exec/ecn', '[data-test=ecn-table]');
    await page.click(`[data-test=ecn-table] >> text=界面测试 ECN ${RUN}`);
    await page.waitForSelector('[data-test=ecn-detail]');
    await page.click('[data-test=ecn-act-analyze]');
    await page.waitForTimeout(1500);
    ok('ECN：影响分析（工艺路线 / 工单 / 文件…）显示出来', await page.locator('[data-test=ecn-impact]').isVisible() && (await api('GET', `/api/e/ecn/${e.id}`)).item.status === 'analysis');
    await shot(page, 'ecn');

    // ---------- 4. 量检具：校准 → 待放行 → 电子签名放行 ----------
    const gid = (await api('POST', '/api/e/gauge', { code: `G-UI-${RUN}`, name: '界面测试卡尺', gtype: 'caliper', cal_interval_days: 365 })).id;
    await api('POST', '/api/e/gauge_cal', { gauge: gid, cal_date: new Date().toISOString().slice(0, 10), result: 'pass', agency: 'internal', cert_no: 'UI' + RUN });
    await go(page, '/iatf/gauges', '[data-test=g-table]');
    await shot(page, 'gauges');
    await page.click(`[data-test=g-table] >> text=G-UI-${RUN}`);
    await page.waitForSelector('[data-test=g-release]');
    await page.click('[data-test=g-release]');
    await sign(page);
    const g = (await api('GET', `/api/e/gauge/${gid}`)).item;
    ok('量检具：放行弹出电子签名框，签名后状态 → 有效', g.status === 'active', g.status);
    await shot(page, 'gauges-released');

    // ---------- 5. PFMEA / 控制计划 ----------
    const fm = (await api('GET', '/api/e/pfmea?size=5')).items.find((x) => x.title.includes('SGH-450'));
    await go(page, `/iatf/pfmea?id=${fm.id}`, '[data-test=fmea-rows]');
    ok('PFMEA：条目表（S/O/D、RPN、AP）', (await page.locator('[data-test=fmea-rows] .el-table__row').count()) >= 5);
    await shot(page, 'pfmea');
    const cp = (await api('GET', '/api/e/control_plan?size=5')).items.find((x) => x.status === 'effective');
    await go(page, `/iatf/pfmea?cp=${cp.id}`, '[data-test=cp-items]');
    ok('控制计划：特性行 + 检验模板', (await page.locator('[data-test=cp-items] .el-table__row').count()) >= 5);
    await shot(page, 'control-plan');

    // ---------- 6. MSA ----------
    const st = (await api('GET', '/api/e/msa_study?size=5')).items[0];
    await go(page, `/iatf/msa?id=${st.id}`, '[data-test=msa-result]');
    const mr = await page.locator('[data-test=msa-result]').innerText();
    ok('MSA：GR&R 结果（%GRR、ndc、结论）', mr.includes('ndc') && /有条件接受|可接受|不可接受/.test(mr), mr.replace(/\s+/g, ' ').slice(0, 80));
    await shot(page, 'msa');

    // ---------- 7. 客诉 ----------
    const cm = await api('POST', '/api/e/complaint', { customer: '示例主机厂', lots: heat, description: `界面测试 ${RUN} 缸孔内壁气孔`, severity: 'major' }, QC);
    await go(page, '/iatf/complaints', '[data-test=cmp-table]');
    await page.click(`[data-test=cmp-table] >> text=界面测试 ${RUN}`);
    await page.waitForSelector('[data-test=cmp-detail]');
    await page.click('[data-test=cmp-act-analyze]');
    await page.waitForTimeout(1500);
    ok('客诉：影响分析（零件总数 / 已发运 / 在厂）', await page.locator('[data-test=cmp-impact]').isVisible() && !!(await api('GET', `/api/e/complaint/${cm.id}`)).item.impact);
    await shot(page, 'complaint');

    // ---------- 8. 批次解冻（质量关卡页）→ 电子签名框 ----------
    const hold = await api('POST', '/api/e/lot_hold', { lot: 'UIH' + RUN, reason: 'exec_ui 冻结' }, QC);
    await go(page, '/quality/hold', '[data-test=hold-table]');
    await page.click(`[data-test=hold-table] tr:has-text("UIH${RUN}") [data-test=hold-release]`);
    await page.waitForSelector('.el-message-box');
    await page.click('.el-message-box__btns .el-button--primary');          // 解冻说明
    await sign(page);
    ok('质量关卡：解冻弹出电子签名框，签名后解冻', (await api('GET', `/api/e/lot_hold/${hold.id}`)).item.status === 'released');
    ok('桌面页面没有前端报错', !page.errors.length, page.errors.slice(0, 3).join(' | '));

    // ---------- 9. 工位终端 / 手机端 390×844 ----------
    const ws = (await api('GET', '/api/station/options')).workstations.find((w) => /CNC/.test(w.code)) || (await api('GET', '/api/station/options')).workstations[0];
    const term = await mk(390, 844);
    await go(term, `/station?ws=${encodeURIComponent(ws.code)}`, '[data-test=act-pack]');
    await term.click('[data-test=act-docs]');
    await term.waitForSelector('[data-test=docs-dlg]');
    await term.waitForTimeout(600);
    await shot(term, 'station-docs-390');
    await term.keyboard.press('Escape');
    await term.waitForTimeout(400);
    await term.click('[data-test=act-pack]');
    await term.waitForSelector('[data-test=pack-dlg]');
    await term.fill(I('pack-dlg'), sns[2]);
    await term.press(I('pack-dlg'), 'Enter');
    await term.waitForTimeout(1200);
    const tm = await term.locator('[data-test=pack-msg-st]').innerText().catch(() => '');
    ok('工位终端（390）：扫码装箱', tm.includes(sns[2]), tm.slice(0, 80));
    await shot(term, 'station-pack-390');
    const mob = await mk(390, 844);
    await go(mob, '/m?tab=pack', '[data-test=m-pack]');
    await mob.fill(I('m-pack-scan'), sns[2]);
    await mob.press(I('m-pack-scan'), 'Enter');
    await mob.waitForTimeout(1200);
    const mm = await mob.locator('[data-test=m-pack-msg]').innerText().catch(() => '');
    ok('手机端（390）：扫码装箱（重复件给出提示）', mm.includes('已经装在'), mm.slice(0, 80));
    await shot(mob, 'mobile-pack-390');
    ok('工位终端 / 手机端没有前端报错', !term.errors.length && !mob.errors.length, [...term.errors, ...mob.errors].slice(0, 3).join(' | '));
  } catch (e) {
    ok('走查没有中断', false, e.stack || e);
    try { await PAGE.screenshot({ path: path.join(OUT, 'zz-failure.png') }); } catch { /* */ }
  }
  await browser.close();
  const n = results.filter((r) => r[1]).length;
  console.log(`\n${n}/${results.length} 通过`);
  process.exit(n === results.length ? 0 : 1);
})();
