// Pinned to zh-CN (UI language + X-Lang): the assertions use the Chinese UI texts; tests/i18n_ui.js covers English.
// 业务页面走查（Playwright）：报工记录（分组 / 详情）、不合格品口径、排产甘特冲突标记 + 重排影响、排程调整假设分析、首检/冻结/返修、追溯对账、
// 知识库图纸预览（DXF / PDF / 图片）、工位终端（选设备 → 报工）、手机端 390×844（扫码：图片识别、模拟摄像头、扫码枪）、工人日报（提交 → 班组长审核）。
// 用法（B 机，MES 已启动且有数据：先跑过 tests/e2e.py 和 tests/biz_check.py）：node tests/biz_ui.js
//   环境变量 MES_URL（默认 http://127.0.0.1:8900）；截图在 data/ui_shots/biz/（SHOTS_DIR 可改）
const path = require('path');
const fs = require('fs');
const zlib = require('zlib');
const { chromium } = require(path.join(process.env.APPDATA, 'npm', 'node_modules', 'playwright'));
// 本地打包的 ZXing（web/ 下 package.json 是 "type": "module"，直接 require 拿不到 UMD 导出，这里按 CommonJS 包一层执行）
const ZX = (() => { const mod = { exports: {} }; new Function('exports', 'module', fs.readFileSync(path.join(__dirname, '..', 'web', 'public', 'vendor', 'zxing-library-0.21.3.min.js'), 'utf8'))(mod.exports, mod); return mod.exports; })();

const BASE = (process.env.MES_URL || 'http://127.0.0.1:8900').replace(/\/$/, '');
const OUT = process.env.SHOTS_DIR || path.join(__dirname, '..', 'data', 'ui_shots', 'biz');
fs.mkdirSync(OUT, { recursive: true });
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const results = [];
const ok = (name, cond, detail = '') => { results.push([name, !!cond]); console.log((cond ? 'PASS ' : 'FAIL ') + name, detail); };
async function api(method, p, body, token) {
  const r = await fetch(BASE + p, { method, headers: { 'Content-Type': 'application/json', 'X-Lang': 'zh-CN', ...(token ? { Authorization: 'Bearer ' + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return r.json();
}

// ---- 测试条码：ZXing 生成二维码 → PNG（图片识别）+ Y4M（Chrome 模拟摄像头） ----
function qrMatrix(text, size) {
  const hints = new Map(); hints.set(ZX.EncodeHintType.MARGIN, 2);
  return new ZX.QRCodeWriter().encode(text, ZX.BarcodeFormat.QR_CODE, size, size, hints);
}
function crc32(buf) { let c, crc = 0xffffffff; for (let n = 0; n < buf.length; n++) { c = (crc ^ buf[n]) & 0xff; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; crc = (crc >>> 8) ^ c; } return (crc ^ 0xffffffff) >>> 0; }
function png(gray, w, h) {
  const raw = Buffer.alloc((w + 1) * h);
  for (let y = 0; y < h; y++) { raw[y * (w + 1)] = 0; gray.copy(raw, y * (w + 1) + 1, y * w, y * w + w); }
  const chunk = (t, d) => { const len = Buffer.alloc(4); len.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(t), d]); const c = Buffer.alloc(4); c.writeUInt32BE(crc32(td)); return Buffer.concat([len, td, c]); };
  const ih = Buffer.alloc(13); ih.writeUInt32BE(w, 0); ih.writeUInt32BE(h, 4); ih[8] = 8; ih[9] = 0; ih[10] = 0; ih[11] = 0; ih[12] = 0;
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ih), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}
function grayOf(m, W, H) {
  const g = Buffer.alloc(W * H, 255), s = m.getWidth(), ox = Math.floor((W - s) / 2), oy = Math.floor((H - s) / 2);
  for (let y = 0; y < s; y++) for (let x = 0; x < s; x++) if (m.get(x, y)) g[(oy + y) * W + ox + x] = 0;
  return g;
}
function y4m(gray, W, H, frames = 30) {
  const head = Buffer.from(`YUV4MPEG2 W${W} H${H} F10:1 Ip A1:1 C420jpeg\n`);
  const uv = Buffer.alloc((W / 2) * (H / 2), 128);
  const parts = [head];
  for (let i = 0; i < frames; i++) parts.push(Buffer.from('FRAME\n'), gray, uv, uv);
  return Buffer.concat(parts);
}

(async () => {
  const tok = (await api('POST', '/api/login', { username: 'admin', password: 'honey123' })).token;
  const opTok = (await api('POST', '/api/login', { username: 'operator', password: 'honey123' })).token;
  const leadTok = (await api('POST', '/api/login', { username: 'leader', password: 'honey123' })).token;
  const SW0 = await api('GET', '/api/ai/switches', null, tok);
  await api('PUT', '/api/ai/switches', { global: false, demo: false, record: false, features: Object.fromEntries(Object.keys(SW0.features).map((k) => [k, false])) }, tok);
  // 一个能扫码报工的零件：在制、有工单、没冻结
  let parts = (await api('GET', '/api/e/part?size=1000&sort=-id', null, tok)).items.filter((p) => p.work_order && !p.sn.includes('~') && p.status !== 'hold');
  if (!parts.length) {
    // 老库：最近的零件可能都没关联工单（工单数量早已报满，之后的零件不再挂工单）——按在制工单去找它们的零件
    const wos = [...(await api('GET', '/api/e/work_order?f_status=in_progress&size=50', null, tok)).items, ...(await api('GET', '/api/e/work_order?f_status=released&size=50', null, tok)).items];
    for (const w of wos) {
      parts = (await api('GET', `/api/e/part?f_work_order=${w.id}&size=200&sort=-id`, null, tok)).items.filter((p) => !p.sn.includes('~') && p.status !== 'hold');
      if (parts.length) break;
    }
  }
  const SN = (parts.find((p) => p.status === 'wip') || parts[0] || {}).sn;
  if (!SN) { console.error('没有带工单的零件：先跑 tests/e2e.py'); process.exit(2); }
  const W = 640, H = 480;
  const qrImg = png(grayOf(qrMatrix(SN || 'NONE', 360), 480, 480), 480, 480);
  fs.writeFileSync(path.join(OUT, 'test-qr.png'), qrImg);
  const camFile = path.join(OUT, 'test-qr.y4m');
  fs.writeFileSync(camFile, y4m(grayOf(qrMatrix(SN || 'NONE', 300), W, H), W, H));

  const browser = await chromium.launch({ executablePath: CHROME, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const mkCtx = async (b, token, viewport, extra = {}) => {
    const ctx = await b.newContext({ viewport, locale: 'zh-CN', ...extra });
    // 初始化脚本会跑进每个 frame：Chrome 自带 PDF 阅读器的内部 frame 里 sessionStorage 是 null（图纸 PDF 预览），只在顶层页面里做
    await ctx.addInitScript((t) => { if (window.top !== window || !window.sessionStorage) return; if (!sessionStorage.getItem('init')) { sessionStorage.setItem('init', '1'); localStorage.setItem('mes_token', t); localStorage.setItem('hm_lang', 'zh-CN'); localStorage.setItem('mes_assist_open', '0'); localStorage.setItem('hm_tour_done', '1'); } }, token);
    return ctx;
  };
  const errors = [];
  const watch = (page, tag) => {
    page.on('pageerror', (e) => errors.push(`${tag} ${page.url().replace(BASE, '')}: ${e}`));
    page.on('console', (m) => { if (m.type() === 'error' && !/favicon|Failed to load resource.*(404|409)/.test(m.text())) errors.push(`${tag} ${page.url().replace(BASE, '')}: ${m.text()} @${(m.location() || {}).url || ''}`); });
  };
  const shot = async (page, name, full = false) => { await page.waitForTimeout(500); await page.screenshot({ path: path.join(OUT, name + '.png'), fullPage: full }); };
  const ctx = await mkCtx(browser, tok, { width: 1440, height: 900 });
  const page = await ctx.newPage(); watch(page, 'desk');

  // ---------- 报工记录 ----------
  await page.goto(BASE + '/e/report');
  let haveGroups = await page.waitForSelector('[data-test=rep-wo]', { timeout: 10000 }).then(() => true).catch(() => false);
  if (!haveGroups) {
    // 默认"近 24 小时"按数据时钟算：老库接上测试用的虚拟工厂副本时数据时钟可能比库里的报工还早，放宽到近 7 天再看
    await page.click('[data-test=rep-range]'); await page.click('.el-select-dropdown__item:has-text("近 7 天")').catch(() => {});
    haveGroups = await page.waitForSelector('[data-test=rep-wo]', { timeout: 10000 }).then(() => true).catch(() => false);
  }
  ok('报工记录默认是分组合计视图', haveGroups || (await page.locator('[data-test=rep-mode]').count()) > 0, haveGroups ? '' : '这个时间范围里没有报工（数据时钟早于库里的报工），只查页面结构');
  if (haveGroups) {
  const firstDev = page.locator('[data-test=rep-dev]').first();
  if (!(await firstDev.count())) await page.locator('tr.op').first().click();
  await page.waitForSelector('[data-test=rep-dev]');
  // 第一个分组不一定是设备自动的（比如 biz_check 刚人工报过的工单排在最前）：在展开的分组里找，找不到再展开前几个工单
  const autoTxt = async () => (await page.locator('[data-test=rep-dev]').allInnerTexts()).find((t) => /设备自动 · 每 \d+ 分钟汇总/.test(t));
  let devTxt = await autoTxt();
  for (let i = 0; !devTxt && i < Math.min(6, await page.locator('tr.op').count()); i++) {
    await page.locator('tr.op').nth(i).click().catch(() => {}); await page.waitForTimeout(400); devTxt = await autoTxt();
  }
  const hasAuto = (await api('GET', '/api/e/report?f_source=auto&size=1', null, tok)).total > 0;
  ok('分组里写明来源"设备自动 · 每 N 分钟汇总"', devTxt || !hasAuto, (devTxt || (hasAuto ? '没找到' : '没有设备自动报工，跳过')).replace(/\s+/g, ' ').slice(0, 80));
  await shot(page, 'reports-grouped');
  await page.locator('[data-test=rep-dev]').first().click();
  await page.waitForSelector('[data-test=rep-recs] .el-table__row');
  await page.locator('[data-test=rep-recs] .el-table__row').first().click();
  await page.waitForSelector('[data-test=rep-source]');
  const det = await page.locator('[data-test=rep-detail]').innerText();
  ok('报工详情：来源、设备编号/名称、录入时间、操作人、作业指示', /汇总|人工|扫码|终端/.test(det) && det.includes('设备编号') && det.includes('录入时间') && det.includes('操作人') && det.includes('作业指示'));
  await shot(page, 'reports-detail');
  await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
  await page.click('[data-test=rep-mode] >> text=明细');
  await page.waitForSelector('[data-test=rep-list] .el-table__row');
  ok('明细视图', (await page.locator('[data-test=rep-list] .el-table__row').count()) > 0);
  }

  // ---------- 不合格品 ----------
  const home = await api('GET', '/api/dash/home', null, tok);
  await page.goto(BASE + '/e/ncr?f_status=open');
  await page.waitForSelector('[data-test=ncr-tab-pending]');
  await page.waitForTimeout(600);
  const pendTxt = await page.locator('[data-test=ncr-tab-pending]').innerText();
  const rows = await page.locator('[data-test=ncr-table] .el-table__row').count();
  ok('不合格品"待处理"页签 = 工作台计数，旧链接也按同口径显示', pendTxt.includes(String(home.todo.ncr)) && (home.todo.ncr === 0 || rows > 0), `工作台 ${home.todo.ncr}，页签 ${pendTxt.replace(/\s+/g, ' ')}，列表 ${rows} 行`);
  await shot(page, 'ncr');

  // ---------- 排产 ----------
  await page.goto(BASE + '/production/gantt');
  await page.waitForSelector('[data-test=gantt-constraints] .cn');
  ok('排产页显示约束主数据', (await page.locator('[data-test=gantt-constraints] .cn').count()) === 6);
  await page.click('[data-test=gantt-resched]');
  await page.waitForSelector('[data-test=impact-table]');
  ok('重新排产后弹出受影响工单和原因', (await page.locator('[data-test=impact-table]').innerText()).includes('新预计完工'));
  await shot(page, 'gantt-impact');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(400);
  ok('甘特上有冲突标记', (await page.locator('[data-test=gantt-mark]').count()) > 0, `${await page.locator('[data-test=gantt-mark]').count()} 处`);
  await shot(page, 'gantt');
  await page.goto(BASE + '/e/schedule_proposal');
  await page.waitForSelector('[data-test=whatif-preview]');
  await page.waitForTimeout(800);
  await page.click('[data-test=whatif-preview]');
  await page.waitForSelector('[data-test=whatif-summary]');
  // 受影响 = 完工时间被推迟的工单；老库里在制工单早已逾期时可能一个都不变，这时摘要里要列出（已经）预计逾期的工单
  const wiSum = await page.locator('[data-test=whatif-summary]').innerText();
  ok('假设设备停机：列出受影响工单', (await page.locator('[data-test=impact-table] .el-table__row').count()) > 0 || /WO\d+/.test(wiSum), wiSum);
  await shot(page, 'schedule-whatif');

  // ---------- 质量闸门 ----------
  for (const [p, sel, name] of [['/quality/fai', '[data-test=fai-cands]', 'quality-fai'], ['/quality/hold', '[data-test=hold-table]', 'quality-hold'], ['/quality/rework', '[data-test=rw-table]', 'quality-rework']]) {
    await page.goto(BASE + p); await page.waitForSelector(sel); await page.waitForTimeout(500);
    ok(`页面 ${p}`, true); await shot(page, name);
  }

  // ---------- 追溯对账 ----------
  await page.goto(BASE + '/trace/recon');
  await page.waitForSelector('[data-test=recon-chain] .st');
  ok('追溯对账 6 步链条', (await page.locator('[data-test=recon-chain] .st').count()) === 6, (await page.locator('[data-test=recon-summary]').innerText()).replace(/\s+/g, ' '));
  await shot(page, 'trace-recon');

  // ---------- 知识库图纸 ----------
  await page.goto(BASE + '/e/kb_doc?kind=drawing');
  await page.waitForSelector('[data-test=kb-list] .it');
  const items = page.locator('[data-test=kb-list] .it');
  let sawDxf = false, sawPdf = false, sawImg = false;
  for (let i = 0; i < await items.count(); i++) {
    await items.nth(i).click(); await page.waitForTimeout(700);
    if (await page.locator('[data-test=dxf-svg] svg').count()) { sawDxf = sawDxf || (await page.locator('[data-test=dxf-svg] svg polygon, [data-test=dxf-svg] svg circle').count()) > 3; if (sawDxf) await shot(page, 'kb-dxf'); }
    if (await page.locator('[data-test=pdf-frame]').count()) { sawPdf = true; await shot(page, 'kb-pdf'); }
    if (await page.locator('[data-test=img-preview]').count()) { sawImg = sawImg || await page.locator('[data-test=img-preview]').evaluate((im) => im.complete && im.naturalWidth > 0); }
  }
  ok('DXF 在浏览器里渲染成 SVG', sawDxf); ok('PDF 预览', sawPdf); ok('图片预览', sawImg);

  // ---------- 工人日报审核页 ----------
  await page.goto(BASE + '/labor/hours');
  await page.waitForSelector('[data-test=ld-hours]');
  ok('工时统计页', true); await shot(page, 'labor-hours');

  // ---------- 工位终端 ----------
  const sctx = await mkCtx(browser, opTok, { width: 1600, height: 900 });
  const sp = await sctx.newPage(); watch(sp, 'station');
  await sp.goto(BASE + '/station');
  await sp.waitForSelector('[data-test=pick-WS-CNC1], [data-test^=pick-]');
  for (const c of ['WS-CNC1', 'WS-CNC2', 'WS-CNC3']) if (await sp.locator(`[data-test=pick-${c}]`).count()) await sp.click(`[data-test=pick-${c}]`);
  await shot(sp, 'station-setup');
  await sp.click('[data-test=station-go]');
  await sp.waitForSelector('[data-test=station] .mc');
  ok('工位终端：一人看多台设备', (await sp.locator('.mc').count()) >= 2);
  ok('工位终端：作业指示 + 图纸', (await sp.locator('[data-test=instruction]').innerText()).length > 20 && (await sp.locator('[data-test=ins-drawing]').count()) > 0);
  ok('工位终端：模具模次', (await sp.locator('.mc .tool').count()) > 0);
  await shot(sp, 'station');
  await sp.click('[data-test=act-report]');
  await sp.waitForSelector('[data-test=report-check]');
  const chk = await sp.locator('[data-test=report-check]').innerText();
  ok('报工前给出闸门预检（最多可报 / 首检 / 冻结）', /最多可报|不能报工/.test(chk), chk);
  await shot(sp, 'station-report');
  if (/最多可报/.test(chk) && !/最多可报 0/.test(chk)) {
    await sp.dblclick('[data-test=report-submit]');                   // 双击：只会提交一次
    await sp.waitForTimeout(1500);
    ok('工位终端报工（双击只提交一次）', !(await sp.locator('[data-test=report-dlg]').isVisible().catch(() => false)));
  } else {
    await sp.click('[data-test=report-submit]').catch(() => undefined);
    await sp.waitForTimeout(1200);
    ok('闸门拦下时给出中文提示', (await sp.locator('.el-message').allInnerTexts()).join(' ').length > 4);
  }
  await sp.keyboard.press('Escape');
  await sp.setViewportSize({ width: 1280, height: 800 }); await shot(sp, 'station-1280');

  // ---------- 手机端 390×844 ----------
  const mctx = await mkCtx(browser, opTok, { width: 390, height: 844 }, { isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  const mp = await mctx.newPage(); watch(mp, 'mobile');
  await mp.goto(BASE + '/m');
  await mp.waitForSelector('[data-test=m-tabbar]');
  await shot(mp, 'mobile-home');
  const swOk = await mp.evaluate(async () => { try { const r = await Promise.race([navigator.serviceWorker.ready, new Promise((ok) => setTimeout(() => ok(null), 25000))]); return !!r; } catch { return false; } });
  const man = await mp.evaluate(() => document.querySelector('link[rel=manifest]')?.getAttribute('href'));
  ok('PWA：manifest + service worker 已注册', swOk && man === '/manifest.webmanifest');
  // 图片识别
  await mp.click('[data-test=scan-camera]');
  await mp.waitForSelector('[data-test=scan-image-input]', { state: 'attached' });
  await mp.setInputFiles('[data-test=scan-image-input]', path.join(OUT, 'test-qr.png'));
  await mp.waitForFunction(() => !!(window).__lastScan, null, { timeout: 15000 }).catch(() => undefined);
  const s1 = await mp.evaluate(() => (window).__lastScan);
  ok('拍照 / 图片识别二维码', s1 && s1.code === SN, JSON.stringify(s1));
  await mp.waitForSelector('[data-test=m-check]', { timeout: 10000 }).catch(() => undefined);
  ok('扫码后带出零件、工单工序和报工预检', (await mp.locator('.card.part').innerText().catch(() => '')).includes(SN) && (await mp.locator('[data-test=m-check]').count()) > 0);
  await shot(mp, 'mobile-scan');
  // 扫码枪（键盘楔）：焦点不在输入框时快速输入 + 回车
  await mp.locator('[data-test=m-scan] .scan input').fill('');
  await mp.evaluate(() => { (document.activeElement)?.blur(); (window).__lastScan = null; });
  await mp.locator('.hd').click();
  // 扫码枪是 USB 键盘：一口气打完一串再回车。直接在页面里连发按键事件（不受测试机负载影响）
  await mp.evaluate((code) => { for (const k of [...code, 'Enter']) window.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true })); }, SN);
  await mp.waitForTimeout(1200);
  ok('扫码枪输入（键盘楔）', (await mp.locator('[data-test=m-scan] .scan input').inputValue()) === SN);
  // 扫一件正在 CNC 上的零件：默认选中零件当前所在的工序（零件 station 是设备编号 CNC3，工序是工位组 CNC）
  const atCnc = ((await api('GET', '/api/e/part?f_status=wip&size=500', null, opTok)).items || []).find((p) => /^CNC\d+$/.test(p.station || '') && p.work_order);
  if (atCnc) {
    await mp.locator('[data-test=m-scan] .scan input').fill(atCnc.sn);
    await mp.press('[data-test=m-scan] .scan input', 'Enter');
    await mp.waitForSelector('[data-test^=m-op-].on', { timeout: 10000 }).catch(() => undefined);
    const sel = await mp.locator('[data-test^=m-op-].on').getAttribute('data-test').catch(() => '');
    const cncSeq = ((await api('GET', `/api/e/work_order/${atCnc.work_order}`, null, opTok)).children?.wo_op || []).find((o) => o.station_group === 'CNC')?.seq;
    ok('扫码默认选零件当前所在工序（在 CNC 上 → CNC 工序）', sel === `m-op-${cncSeq}`, `${atCnc.sn} @${atCnc.station} → ${sel}`);
  } else ok('扫码默认选零件当前所在工序（没有正在 CNC 上的零件，跳过）', true);
  // 其他页签
  for (const t of ['task', 'query', 'notice']) {
    await mp.click(`[data-test=m-tab-${t}]`); await mp.waitForSelector(`[data-test=m-${t}]`);
    if (t === 'query') {
      await mp.fill('[data-test=m-query] .scan input', 'CNC1'); await mp.press('[data-test=m-query] .scan input', 'Enter');
      await mp.waitForSelector('[data-test^=m-found-]'); ok('手机端查询', true);
    }
    await shot(mp, 'mobile-' + t);
  }
  for (const t of ['repair', 'spot', 'fai', 'daily']) { await mp.click('[data-test=m-tab-more]'); await mp.click(`[data-test=m-more-${t}]`); await mp.waitForSelector(`[data-test=m-${t}]`); await mp.waitForTimeout(400); await shot(mp, 'mobile-' + t); }
  // 我的日报：提交
  await mp.waitForSelector('[data-test=daily-submit]', { timeout: 10000 });
  if (await mp.locator('[data-test=daily-submit]').isEnabled()) {
    await mp.fill('[data-test=m-daily] textarea', 'biz_ui：手机端提交');
    const hoursIn = mp.locator('[data-test=m-daily] .nums label').nth(2).locator('input');
    if (Number(await hoursIn.inputValue()) <= 0) { await hoursIn.fill('8'); await hoursIn.press('Tab'); }
    await mp.click('[data-test=daily-submit]'); await mp.waitForTimeout(1500);
  }
  else ok('本班日报已审核：表单锁定不能再改', (await mp.locator('[data-test=m-daily]').innerText()).includes('已审核'));
  const daily = (await api('GET', '/api/labor/daily?mine=1', null, opTok)).items[0];
  ok('手机端提交我的日报', daily && ['submitted', 'approved'].includes(daily.status), daily && `${daily.code} ${daily.status}`);
  const ovf = await mp.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  ok('手机端 390 宽没有横向滚动', ovf <= 1, `溢出 ${ovf}px`);

  // 班组长审核
  const lctx = await mkCtx(browser, leadTok, { width: 1440, height: 900 });
  const lp = await lctx.newPage(); watch(lp, 'leader');
  await lp.goto(BASE + '/labor/daily');
  await lp.waitForSelector('[data-test=ld-table]');
  await lp.waitForTimeout(600);
  await shot(lp, 'labor-review');
  if (daily && daily.status === 'submitted') {
    await lp.locator('[data-test=ld-approve]').first().click();
    await lp.waitForTimeout(1200);
    const d2 = (await api('GET', `/api/e/worker_daily/${daily.id}`, null, tok)).item;
    ok('班组长在页面上审核通过', d2.status === 'approved' || (await api('GET', '/api/labor/daily?status=submitted', null, tok)).items.length < 1);
  }

  // ---------- 模拟摄像头扫码（单独起一个带假摄像头的浏览器） ----------
  const cam = await chromium.launch({ executablePath: CHROME, args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-video-capture=${camFile}`] });
  const cctx = await mkCtx(cam, opTok, { width: 390, height: 844 }, { isMobile: true, hasTouch: true, permissions: ['camera'] });
  const cp = await cctx.newPage(); watch(cp, 'camera');
  await cp.goto(BASE + '/m');
  await cp.waitForSelector('[data-test=scan-camera]');
  await cp.click('[data-test=scan-camera]');
  await cp.waitForFunction(() => !!(window).__lastScan, null, { timeout: 25000 }).catch(() => undefined);
  const s2 = await cp.evaluate(() => (window).__lastScan);
  ok('摄像头扫码（模拟摄像头 + ZXing / BarcodeDetector）', s2 && s2.code === SN, JSON.stringify(s2));
  await shot(cp, 'mobile-camera-result');
  await cam.close();

  const realErr = errors.filter((e) => !/ResizeObserver|net::ERR_ABORTED/.test(e));
  ok('控制台无报错', !realErr.length, realErr.slice(0, 5).join(' | '));
  await browser.close();
  await api('PUT', '/api/ai/switches', { global: SW0.global, features: SW0.features, demo: SW0.demo, record: SW0.record }, tok);
  const bad = results.filter((r) => !r[1]).map((r) => r[0]);
  console.log(`\n${results.length - bad.length}/${results.length} 通过`, bad.length ? '；失败：' + bad.join('、') : '');
  process.exit(bad.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
