// Pinned to zh-CN (UI language + X-Lang): the assertions use the Chinese UI texts; tests/i18n_ui.js covers English.
// 工作台驾驶舱 + 车间大屏走查（Playwright）：KPI 与实时刷新、工厂地图（状态颜色、悬停卡片、点击打开设备、Ctrl+点击/右键走 AI）、
// 没有布局时退回状态网格、提醒信息流、组件排序/隐藏并按用户保存、恢复默认、大屏轮播与自动滚动、高对比度、控制台无报错；
// 截图：工作台 1280×720 / 1680×1050 / 2560×1440，大屏 1920×1080（三页 + 高对比度）。
// 用法（B 机，MES 已启动且有数据，比如先跑过 tests/e2e.py；虚拟工厂在线时地图用真实布局）：node tests/home_ui.js
//   环境变量 MES_URL（默认 http://127.0.0.1:8900）；截图目录 SHOTS_DIR（默认 data/ui_shots/home）
// 测试会临时打开 AI 演示模式（不花 token），结束时恢复原来的开关；会建一条安灯并在结束时关闭。
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(process.env.APPDATA, 'npm', 'node_modules', 'playwright'));

const BASE = (process.env.MES_URL || 'http://127.0.0.1:8900').replace(/\/$/, '');
const OUT = process.env.SHOTS_DIR || path.join(__dirname, '..', 'data', 'ui_shots', 'home');
fs.mkdirSync(OUT, { recursive: true });
const results = [];
const ok = (name, cond, detail = '') => { results.push([name, !!cond]); console.log((cond ? 'PASS ' : 'FAIL ') + name, detail); };
async function api(method, p, body, token) {
  const r = await fetch(BASE + p, { method, headers: { 'Content-Type': 'application/json', 'X-Lang': 'zh-CN', ...(token ? { Authorization: 'Bearer ' + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return r.json().catch(() => ({}));
}

let PAGE = null;
(async () => {
  const tok = (await api('POST', '/api/login', { username: 'admin', password: 'honey123' })).token;
  const SW0 = await api('GET', '/api/ai/switches', null, tok);
  const setAi = (on) => api('PUT', '/api/ai/switches', { global: on, demo: on, record: false, features: Object.fromEntries(Object.keys(SW0.features).map((k) => [k, on && ['ask', 'watch'].includes(k)])) }, tok);
  await setAi(false);
  await api('DELETE', '/api/home/prefs', null, tok);
  // 一条待响应的安灯（信息流和安灯条要显示它）
  const eqs = (await api('GET', '/api/e/equipment?size=100', null, tok)).items || [];
  const cnc = eqs.find((e) => e.code === 'CNC3') || eqs[0];
  const clk = await api('GET', '/api/clock', null, tok);
  const andon = await api('POST', '/api/e/andon', { atype: 'equipment', equipment: cnc && cnc.id, station: cnc && cnc.code, description: '主轴异响，请设备员到场', status: 'open', source: 'manual', raised_at: clk.now }, tok);
  const andonId = andon.id || andon.item?.id;

  const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const ctx = await browser.newContext({ viewport: { width: 1680, height: 1050 }, locale: 'zh-CN' });
  await ctx.addInitScript((t) => {
    if (!sessionStorage.getItem('init')) {
      sessionStorage.setItem('init', '1'); localStorage.setItem('mes_token', t); localStorage.setItem('hm_lang', 'zh-CN'); localStorage.setItem('mes_assist_open', '0');
      Object.keys(localStorage).filter((k) => k.startsWith('hm_home_')).forEach((k) => localStorage.removeItem(k)); sessionStorage.removeItem('hm_ai_ctx');
    }
  }, tok);
  const page = await ctx.newPage();
  PAGE = page;
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  const liveHits = [];
  page.on('response', (r) => { if (r.url().includes('/api/home/live')) liveHits.push(Date.now()); });
  const shot = async (name, full = false) => { await page.waitForTimeout(400); await page.screenshot({ path: path.join(OUT, name + '.png'), fullPage: full }); };
  const toast = async () => (await page.locator('.el-message').allInnerTexts().catch(() => [])).join(' | ');
  const widgets = () => page.locator('.wg[data-widget]').evaluateAll((els) => els.map((e) => e.getAttribute('data-widget')));

  // ---------- 1. 驾驶舱渲染 + 实时刷新 ----------
  await page.goto(BASE + '/');
  await page.waitForSelector('[data-test=cockpit]');
  await page.waitForFunction(() => document.querySelectorAll('.hm-kpi .v').length >= 6 && !document.querySelector('.hm-kpi .sk'), null, { timeout: 15000 });
  ok('驾驶舱：6 个关键指标', (await page.locator('.w-kpi .hm-kpi').count()) >= 6);
  ok('驾驶舱：KPI 带趋势线', (await page.locator('.w-kpi .hm-kpi .spark').count()) >= 4);
  ok('驾驶舱：AI 态势条有摘要', (await page.locator('[data-test=ai-brief]').innerText()).includes('入库'), await page.locator('[data-test=ai-brief]').innerText());
  ok('页头不再重复数据时钟', !(await page.locator('.ck-head').innerText()).includes('数据时钟'));
  await page.waitForSelector('[data-test=factory-map] .st', { timeout: 15000 }).catch(() => {});
  const n0 = liveHits.length;
  await page.waitForTimeout(7000);
  ok('实时刷新：设备状态每 3 秒取一次', liveHits.length - n0 >= 2, `${liveHits.length - n0} 次 / 7 秒`);
  const live = await api('GET', '/api/home/live', null, tok);
  const domSt = await page.locator('[data-test=factory-map] .st').evaluateAll((els) => els.map((e) => [e.getAttribute('data-code'), e.getAttribute('data-status')]));
  const apiSt = Object.fromEntries(live.items.map((x) => [x.code, x.status]));
  const same = domSt.filter(([c, s]) => apiSt[c] === s).length;
  ok('实时刷新：地图状态和服务端一致', domSt.length && same >= domSt.length - 2, `${same}/${domSt.length}`);

  // ---------- 2. 工厂地图 ----------
  const nSt = domSt.length;
  ok('地图：按虚拟工厂布局画出设备', nSt >= 15, `${nSt} 台`);
  const tones = await page.locator('[data-test=factory-map] .st').evaluateAll((els) => [...new Set(els.map((e) => [...e.classList].find((c) => c.startsWith('t-'))))]);
  const fills = await page.locator('[data-test=factory-map] .st:not(.k-buffer) .body').evaluateAll((els) => [...new Set(els.map((e) => getComputedStyle(e).fill))]);
  ok('地图：设备按状态着色', tones.length >= 1 && fills.length >= Math.min(2, tones.length), `${tones.join(',')} / ${fills.length} 种填充色`);
  ok('地图：缓冲区显示在制数', (await page.locator('[data-test=factory-map] .k-buffer .wip').count()) >= 3);
  ok('地图：区域汇总条', (await page.locator('[data-test=map-areas] .as').count()) >= 4);
  const st0 = page.locator('[data-test=factory-map] .st[data-code=CNC3] .body').first();
  await st0.hover({ force: true });
  await page.waitForSelector('[data-test=map-card]', { timeout: 3000 }).catch(() => {});
  ok('地图：悬停出现设备卡片', (await page.locator('[data-test=map-card]').innerText().catch(() => '')).includes('CNC3'));
  await page.mouse.wheel(0, 200);
  await page.click('[data-test=map-fit]');
  // Ctrl+点击（AI 关闭）→ 提示
  await st0.click({ modifiers: ['Control'], force: true });
  await page.waitForTimeout(300);
  ok('地图：AI 关闭时 Ctrl+点击给出提示', (await toast()).includes('AI 已关闭'), await toast());
  await st0.click({ force: true });
  await page.waitForURL(/\/e\/equipment\?id=/, { timeout: 5000 }).catch(() => {});
  ok('地图：点击设备打开设备页', /\/e\/equipment\?id=\d+/.test(page.url()), page.url());

  // AI 打开（演示模式）：Ctrl+点击加入上下文，右键出设备菜单
  await setAi(true);
  await page.goto(BASE + '/');
  await page.waitForSelector('[data-test=factory-map] .st[data-code=CNC3]', { timeout: 15000 });
  await page.waitForTimeout(800);
  const st1 = page.locator('[data-test=factory-map] .st[data-code=CNC3] .body').first();
  await st1.click({ modifiers: ['Control'], force: true });
  await page.waitForTimeout(400);
  ok('地图：Ctrl+点击把设备加入 AI 上下文', (await page.locator('[data-test=factory-map] .st[data-code=CNC3][data-ai-selected]').count()) === 1 && page.url().endsWith('/'), await toast());
  await st1.click({ button: 'right', force: true });
  await page.waitForSelector('[data-test=ai-menu]');
  const menu = await page.locator('[data-test=ai-menu]').innerText();
  ok('地图：右键是设备的 AI 菜单（诊断 / 打开记录 / 报修）', menu.includes('设备') && menu.includes('AI 诊断') && menu.includes('打开记录') && menu.includes('报修'), menu.replace(/\s+/g, ' ').slice(0, 120));
  await page.keyboard.press('Escape');
  ok('AI 打开：信息流显示"AI 值守中"', (await page.locator('[data-test=watch-on]').count()) === 1);
  await shot('home-ai-on-1680x1050');

  // ---------- 3. 提醒信息流 ----------
  await page.waitForSelector('[data-test=feed-item]', { timeout: 10000 }).catch(() => {});
  const feedTxt = await page.locator('[data-test=feed]').innerText();
  ok('信息流：有提醒卡片', (await page.locator('[data-test=feed-item]').count()) >= 1, `${await page.locator('[data-test=feed-item]').count()} 张`);
  ok('信息流：安灯出现在提醒里', feedTxt.includes('主轴异响'));
  const feedApi = await api('GET', '/api/home/feed', null, tok);
  ok('信息流接口：按严重程度排序', feedApi.items.every((x, i, a) => i === 0 || ['critical', 'warning', 'info'].indexOf(a[i - 1].severity) <= ['critical', 'warning', 'info'].indexOf(x.severity)), JSON.stringify(feedApi.counts));
  ok('安灯条：显示待响应安灯', (await page.locator('[data-test=andon-card]').count()) >= 1);
  await page.locator('[data-test=feed-item]').first().click({ button: 'right' });
  await page.waitForSelector('[data-test=ai-menu]');
  ok('信息流：卡片右键是"报警 / 提醒"的 AI 菜单', (await page.locator('[data-test=ai-menu]').innerText()).includes('报警'));
  await page.keyboard.press('Escape');
  await page.click('[data-test=ai-strip] [data-q=today]');
  await page.waitForSelector('.dock .m.user', { timeout: 8000 }).catch(() => {});
  ok('AI 态势条：快速提问发给 AI 面板', (await page.locator('.dock .m.user').last().innerText().catch(() => '')).includes('今天'));
  await page.click('[data-test=dock-collapse]').catch(() => {});

  // ---------- 4. 没有布局时退回状态网格 ----------
  await page.route('**/api/home/layout*', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: false, reason: '测试：虚拟工厂不可达' }) }));
  await page.goto(BASE + '/');
  await page.waitForSelector('[data-test=map-fallback] .tile', { timeout: 10000 }).catch(() => {});
  ok('地图：没有布局时退回状态网格', (await page.locator('[data-test=map-fallback] .tile').count()) >= 10);
  await shot('home-fallback-1680x1050');
  await page.unroute('**/api/home/layout*');

  // ---------- 5. 组件排序 / 隐藏 / 按用户保存 / 恢复默认 ----------
  await page.goto(BASE + '/');
  await page.waitForSelector('.wg[data-widget=map]');
  const w0 = await widgets();
  await page.click('[data-test=layout-edit]');
  await page.waitForSelector('[data-test=layout-bar]');
  await page.click('[data-move-down=map]');
  await page.click('[data-hide=oee]');
  // 拖动：把"今日质量"拖到"安灯"前面（MOM 运营概览在最上面，先把目标滚进视口）
  await page.locator('.wg[data-widget=andon]').scrollIntoViewIfNeeded();
  await page.dragAndDrop('.wg[data-widget=quality]', '.wg[data-widget=andon]', { targetPosition: { x: 40, y: 5 } }).catch((e) => console.log('drag', e.message));
  await page.waitForTimeout(500);
  const w1 = await widgets();
  ok('布局：前移/后移改变顺序', w1.indexOf('map') > w1.indexOf('feed'), w1.join(','));
  ok('布局：拖动排序', w1.indexOf('quality') < w1.indexOf('andon'), w1.join(','));
  ok('布局：隐藏组件', !w1.includes('oee'));
  await shot('home-edit-1680x1050');
  await page.click('[data-test=layout-edit]');
  await page.waitForTimeout(600);
  await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('hm_home_layout')).forEach((k) => localStorage.removeItem(k)));   // 只靠服务端
  await page.reload();
  await page.waitForSelector('.wg[data-widget=map]');
  await page.waitForTimeout(800);
  const w2 = await widgets();
  ok('布局：刷新后仍在（服务端按用户保存）', w2.join() === w1.join(), w2.join(','));
  const prefs = await api('GET', '/api/home/prefs', null, tok);
  ok('布局接口：保存了顺序和隐藏', (prefs.hidden || []).includes('oee') && prefs.order.indexOf('map') > prefs.order.indexOf('feed'));
  const qc = (await api('POST', '/api/login', { username: 'qc', password: 'honey123' })).token;
  ok('布局：别的用户不受影响', !(await api('GET', '/api/home/prefs', null, qc)).order);
  await page.click('[data-test=layout-edit]');
  await page.click('[data-test=layout-reset]');
  await page.waitForTimeout(500);
  ok('布局：恢复默认', (await widgets()).join() === w0.join(), (await widgets()).join(','));
  await page.click('[data-test=layout-edit]');

  // ---------- 6. 截图：三种分辨率 ----------
  for (const [w, h] of [[1280, 720], [1680, 1050], [2560, 1440]]) {
    await page.setViewportSize({ width: w, height: h });
    await page.goto(BASE + '/');
    await page.waitForSelector('[data-test=factory-map] .st', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1500);
    const hs = await page.evaluate(() => document.querySelector('.hm-main').scrollWidth - document.querySelector('.hm-main').clientWidth);
    ok(`${w}×${h}：没有横向滚动`, hs <= 1, `${hs}px`);
    await shot(`home-${w}x${h}`);
    if (w === 1680) await shot(`home-${w}x${h}-full`, true);
  }
  await setAi(false);
  await page.setViewportSize({ width: 1680, height: 1050 });
  await page.goto(BASE + '/');
  await page.waitForSelector('[data-test=factory-map] .st', { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(1200);
  ok('AI 关闭：态势条变成规则摘要', (await page.locator('[data-test=ai-strip]').innerText()).includes('态势摘要'));
  await shot('home-ai-off-1680x1050');

  // ---------- 7. 车间大屏 ----------
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto(BASE + '/screen?interval=4&hc=0');
  await page.waitForSelector('[data-test=scene]');
  await page.waitForTimeout(1500);
  const s0 = await page.locator('[data-test=scene]').getAttribute('data-scene');
  await shot('screen-1-prod-1920x1080');
  await page.waitForTimeout(4500);
  const s1 = await page.locator('[data-test=scene]').getAttribute('data-scene');
  ok('大屏：自动轮播', s0 !== s1, `${s0} → ${s1}`);
  const clock0 = await page.locator('[data-test=screen-clock]').innerText();
  await page.goto(BASE + '/screen?scene=2&interval=60');
  await page.waitForSelector('[data-test=eq-scroll]');
  await page.waitForTimeout(2500);
  const tr0 = await page.evaluate(() => document.querySelector('[data-test=eq-scroll] .track').style.transform);
  await page.waitForTimeout(2000);
  const tr1 = await page.evaluate(() => document.querySelector('[data-test=eq-scroll] .track').style.transform);
  ok('大屏：长表格自动滚动', (await page.locator('[data-test=eq-scroll].running').count()) === 1 && tr0 !== tr1, `${tr0} → ${tr1}`);
  ok('大屏：时钟在走', /\d\d:\d\d:\d\d/.test(clock0) && (await page.locator('[data-test=screen-clock]').innerText()) !== clock0);
  ok('大屏：设备页有工厂地图', (await page.locator('[data-test=factory-map] .st').count()) >= 15);
  await shot('screen-2-equip-1920x1080');
  await page.goto(BASE + '/screen?scene=3&interval=60');
  await page.waitForSelector('[data-test=maint-scroll]');
  await page.waitForTimeout(2000);
  await shot('screen-3-quality-1920x1080');
  await page.mouse.move(900, 500);
  await page.click('[data-test=screen-pause]');
  await page.selectOption('[data-test=screen-interval]', '30');
  await page.click('[data-test=screen-hc]');
  await page.waitForTimeout(600);
  ok('大屏：高对比度', (await page.locator('[data-test=big-screen].hc').count()) === 1);
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('hm_screen') || '{}'));
  ok('大屏：轮播间隔 / 高对比度记在本机', saved.interval === 30 && saved.hc === true, JSON.stringify(saved));
  await page.mouse.move(10, 10);
  await page.waitForTimeout(4500);
  await shot('screen-3-quality-hc-1920x1080');
  await page.click('[data-test=screen-hc]').catch(() => {});
  await page.evaluate(() => localStorage.removeItem('hm_screen'));

  const real = errors.filter((e) => !/ResizeObserver|favicon/.test(e));
  ok('浏览器控制台没有报错', real.length === 0, real.slice(0, 5).join(' | '));
  await browser.close();
  if (andonId) await api('POST', `/api/e/andon/${andonId}/do/close`, { note: '测试结束关闭' }, tok);
  await api('DELETE', '/api/home/prefs', null, tok);
  await api('PUT', '/api/ai/switches', { global: SW0.global, demo: SW0.demo, record: SW0.record, features: SW0.features }, tok);
  const bad = results.filter((r) => !r[1]);
  console.log(`\n${results.length - bad.length}/${results.length} 通过` + (bad.length ? '；失败：' + bad.map((r) => r[0]).join('、') : '') + `\n截图：${OUT}`);
  process.exit(bad.length ? 1 : 0);
})().catch(async (e) => {
  console.error(e);
  try { await PAGE.screenshot({ path: path.join(OUT, 'zz-failure.png') }); } catch { /* */ }
  process.exit(2);
});
