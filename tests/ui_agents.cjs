// 界面走查（Playwright）：从模板建智能体 → 改节点 → 调试面板运行 → 审批 → 看过程；审批中心；右下角助手；安灯看板。
// 用法（B 机，MES 已启动）：node tests/ui_agents.cjs [--no-chat]
// 截图保存在 data/ui_shots/
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(process.env.APPDATA, 'npm', 'node_modules', 'playwright'));

const BASE = (process.env.MES_URL || 'http://127.0.0.1:8900').replace(/\/$/, '');
const OUT = path.join(__dirname, '..', 'data', 'ui_shots', 'agents');
const CHAT = !process.argv.includes('--no-chat');
fs.mkdirSync(OUT, { recursive: true });
const results = [];
const ok = (name, cond, detail = '') => { results.push([name, !!cond]); console.log((cond ? 'PASS ' : 'FAIL ') + name, detail); };

async function api(method, p, body, token) {
  const r = await fetch(BASE + p, { method, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return r.json();
}

let PAGE = null;
(async () => {
  const tok = (await api('POST', '/api/login', { username: 'admin', password: 'honey123' })).token;
  // AI 功能默认关闭：走查期间全部打开（配置了大模型用真的，否则演示模式），结束时恢复
  const SW0 = await api('GET', '/api/ai/switches', null, tok);
  await api('PUT', '/api/ai/switches', { global: true, demo: !SW0.llm_configured, record: false, features: Object.fromEntries(Object.keys(SW0.features).map((k) => [k, true])) }, tok);
  const browser = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
  await ctx.addInitScript((t) => { if (!sessionStorage.getItem('init')) { sessionStorage.setItem('init', '1'); localStorage.setItem('mes_token', t); localStorage.setItem('mes_assist_open', '0'); localStorage.removeItem('mes_assist_thread'); } }, tok);
  const page = await ctx.newPage();
  PAGE = page;
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  const shot = async (name) => { await page.waitForTimeout(400); await page.screenshot({ path: path.join(OUT, name + '.png') }); };

  // 1. 智能体列表 + 模板库
  await page.goto(BASE + '/agents');
  await page.waitForSelector('text=新建智能体');
  await shot('01-agents-list');
  await page.click('[data-test=new-agent]');
  await page.waitForSelector('[data-test=tpl-downtime_repair]');
  ok('模板库有 8 个模板', (await page.locator('.tpl:not(.blank)').count()) === 8);
  await shot('02-template-gallery');
  await page.click('[data-test=tpl-downtime_repair]');
  await page.waitForURL(/\/agents\/\d+/);
  await page.waitForSelector('.vue-flow__node');
  await page.waitForTimeout(800);
  const nNodes = await page.locator('.vue-flow__node').count();
  ok('画布：停机→维修工单 模板的节点都画出来了', nNodes === 9, `${nNodes} 个节点`);
  const nEdges = await page.locator('.vue-flow__edge').count();
  ok('画布：连线（含驳回回边）', nEdges === 11, `${nEdges} 条`);
  await shot('03-editor-canvas');
  const agentId = Number(page.url().match(/agents\/(\d+)/)[1]);

  // 2. 改节点：点"起草维修工单"，改名字，在要求里用 / 插入变量
  await page.locator('.vue-flow__node', { hasText: '起草维修工单' }).click();
  await page.waitForSelector('.nc .row2 input');
  await page.locator('.nc .row2 input').fill('起草维修工单（已改）');
  const ta = page.locator('.nc .f', { hasText: '要求' }).locator('textarea');
  await ta.click();
  await ta.press('Control+End');
  await ta.type(' 设备：');
  await ta.press('/');
  await page.waitForSelector('.pick .it');
  await shot('04-var-picker');
  await page.locator('.pick input').fill('equipment_code');
  await page.locator('.pick .it').first().click();
  const val = await ta.inputValue();
  ok('"/" 弹出变量选择并插入 {节点@输出}', /\{begin@equipment_code\}$/.test(val), val.slice(-40));
  await page.click('[data-test=save]');
  await page.waitForSelector('text=已保存');
  const saved = await api('GET', `/api/agents/${agentId}`, null, tok);
  ok('保存后节点改动进了草稿', saved.draft.components.draft.name === '起草维修工单（已改）' && saved.draft.components.draft.params.instructions.includes('{begin@equipment_code}'));
  await shot('05-node-config');

  // 3. 调试面板：模拟事件（真实停机记录）→ 试运行 → 停在审批 → 通过 → 看过程
  await page.click('.el-tabs__item:has-text("调试运行")');
  await page.waitForSelector('[data-test=sample-select]');
  await page.waitForTimeout(800);
  await page.click('[data-test=debug-run]');
  await page.waitForSelector('.apc', { timeout: 180000 });
  await page.waitForTimeout(600);
  const waitingNode = await page.locator('.vue-flow__node .fn.s-waiting').count();
  const doneNodes = await page.locator('.vue-flow__node .fn.s-done').count();
  ok('运行时节点变色（完成=绿，等待审批=橙）', waitingNode === 1 && doneNodes >= 4, `完成 ${doneNodes}，等待 ${waitingNode}`);
  await shot('06-debug-waiting-approval');
  await page.locator('.apc button:has-text("通过")').first().click();
  await page.waitForSelector('.runhead .el-tag:has-text("已完成")', { timeout: 120000 });
  await page.waitForTimeout(800);
  ok('审批通过后运行完成', await page.locator('.runhead .el-tag:has-text("已完成")').count() === 1);
  ok('试运行回执显示"未写入"', (await page.locator('.reply, .chips').allInnerTexts()).join(' ').includes('试运行'), (await page.locator('.reply').allInnerTexts()).join(' ').slice(0, 80));
  await page.locator('.trace .step .head:visible', { hasText: '起草维修工单' }).first().click();
  await page.waitForTimeout(400);
  await shot('07-debug-trace');
  // 点节点看它这次的结果
  await page.locator('.vue-flow__node', { hasText: '设备履历' }).click();
  await page.click('.el-tabs__item:has-text("节点")');
  await page.waitForSelector('text=这次运行里的结果');
  ok('点节点能看到这次运行的输入/输出', await page.locator('.sec .trace .step').count() >= 1);
  await page.click('[data-test=run-node]');
  await page.waitForSelector('.sec .trace >> nth=1', { timeout: 60000 }).catch(() => {});
  await page.waitForTimeout(800);
  await shot('08-node-result-single-run');

  // 4. 发布 + 启用
  await page.click('[data-test=publish]');
  await page.waitForSelector('[data-test=publish-ok]');
  await page.click('[data-test=publish-ok]');
  await page.waitForSelector('text=已发布 v1');
  ok('发布 v1 并启用', (await api('GET', `/api/agents/${agentId}`, null, tok)).enabled === true);
  await page.click('.el-tabs__item:has-text("发布与设置")');
  await shot('09-publish-settings');
  await api('POST', `/api/agents/${agentId}/enable`, { enabled: false }, tok);

  // 5. 审批中心：再起一个试运行，到审批中心驳回（写原因）
  const r2 = await api('POST', `/api/agents/${agentId}/run`, { mode: 'dry', inputs: (await api('GET', '/api/agents/sample_events/equipment_state', null, tok))[0].payload }, tok);
  for (let i = 0; i < 120; i++) { const d = await api('GET', `/api/agents/runs/${r2.run_id}`, null, tok); if (d.status === 'waiting') break; await new Promise((r) => setTimeout(r, 1000)); }
  await page.goto(BASE + '/approvals');
  await page.waitForSelector('.apc');
  const badge = await page.locator('[data-test=approval-badge] sup').innerText().catch(() => '');
  ok('顶栏审批红点', Number(badge) >= 1, badge);
  await shot('10-approval-center');
  const card = page.locator('.apc').first();
  await card.locator('button:has-text("改一下再批")').click();
  await shot('11-approval-edit');
  await card.locator('button:has-text("驳回")').click();
  await card.locator('textarea').last().fill('不是紧急故障，紧急程度改成一般');
  await card.locator('button:has-text("确认驳回")').click();
  await page.waitForTimeout(1500);
  for (let i = 0; i < 120; i++) { const d = await api('GET', `/api/agents/runs/${r2.run_id}`, null, tok); if (d.status === 'waiting' && d.approvals.length === 2) break; await new Promise((r) => setTimeout(r, 1000)); }
  const d2 = await api('GET', `/api/agents/runs/${r2.run_id}`, null, tok);
  ok('驳回原因回到 AI，重新起草后再次等待审批', d2.status === 'waiting' && d2.approvals.length === 2, JSON.stringify(d2.approvals[1]?.payload || {}).slice(0, 120));
  await page.reload();
  await page.waitForSelector('.apc');
  await page.locator('.apc button:has-text("查看 AI 的过程")').first().click();
  await page.waitForSelector('.el-drawer .trace');
  await page.waitForTimeout(500);
  await shot('12-approval-trace-drawer');
  await page.keyboard.press('Escape');
  await api('POST', `/api/agents/runs/${r2.run_id}/cancel`, {}, tok);

  // 6. 右下角 AI 助手
  await page.goto(BASE + '/equipment/monitor');
  await page.waitForSelector('[data-test=assistant-pill]');
  await shot('13-assistant-pill');
  await page.click('[data-test=assistant-pill]');
  await page.waitForSelector('[data-test=assistant-panel]');
  await shot('14-assistant-open');
  if (CHAT) {
    await page.locator('.panel .inp textarea').fill('最近 8 小时哪台加工中心停机时间最长？');
    await page.keyboard.press('Enter');
    await page.waitForSelector('.panel .prog .pl', { timeout: 60000 });
    await page.waitForTimeout(1500);
    await shot('15-assistant-progress');
    await page.waitForSelector('.panel .m.assistant .mdx', { timeout: 240000 });
    await page.waitForFunction(() => !document.querySelector('.panel .prog'), null, { timeout: 240000 });
    await page.waitForTimeout(800);
    ok('助手回答 + 过程可展开', await page.locator('.panel .proc button').count() >= 1);
    await page.locator('.panel .proc button').last().click();
    await shot('16-assistant-answer');
    // 写操作：确认卡
    await page.locator('.panel .inp textarea').fill('CNC2 导轨润滑报警，帮我建一张一般级别的维修工单');
    await page.keyboard.press('Enter');
    await page.waitForSelector('[data-test=confirm-commit]', { timeout: 240000 });
    await page.waitForFunction(() => !document.querySelector('.panel .prog'), null, { timeout: 240000 });
    await shot('17-assistant-confirm-card');
    await page.click('[data-test=confirm-commit]');
    await page.waitForSelector('.panel .chip.ok', { timeout: 30000 });
    ok('确认提交后出现服务端回执', (await page.locator('.panel .chip.ok').last().innerText()).includes('已创建维修工单'), await page.locator('.panel .chip.ok').last().innerText());
    await shot('18-assistant-receipt');
    // 页面按钮把任务交给助手
    await page.goto(BASE + '/e/repair_order');
    await page.waitForSelector('.el-table__row');
    await page.locator('.el-table__row').first().click();
    await page.waitForSelector('[data-test=ask-ai]');
    await page.click('[data-test=ask-ai]');
    await page.waitForSelector('.panel .m.user >> nth=-1');
    const ctxText = await page.locator('.panel .ctx').innerText();
    ok('"问 AI"把选中的记录带给助手', ctxText.includes('维修工单'), ctxText);
    await page.waitForFunction(() => !document.querySelector('.panel .prog'), null, { timeout: 240000 });
    await shot('19-assistant-ask-from-record');
    // 刷新后保持打开和当前会话
    await page.reload();
    await page.waitForSelector('[data-test=assistant-panel]');
    ok('刷新后助手仍打开、会话还在', await page.locator('.panel .m.user').count() >= 1);
    // 历史
    await page.locator('.panel .ph button').nth(1).click();
    await page.waitForSelector('.hist .hrow');
    const hb = await page.locator('.hist .hrow').first().boundingBox();
    const onTop = await page.evaluate(([x, y]) => !!document.elementFromPoint(x, y)?.closest('.hist'), [hb.x + hb.width / 2, hb.y + hb.height / 2]);
    ok('历史对话列表盖在面板上面（没被挡住）', onTop);
    await shot('20-assistant-history');
  }

  // 7. 安灯看板、智能体列表
  await page.goto(BASE + '/andon');
  await page.waitForSelector('text=未关闭的安灯');
  await shot('21-andon-board');
  await page.goto(BASE + '/agents');
  await page.waitForSelector('.ag');
  await shot('22-agents-list-after');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(BASE + '/approvals');
  await page.waitForTimeout(1200);
  await shot('23-mobile-approvals');

  await api('DELETE', `/api/agents/${agentId}`, null, tok);
  await api('PUT', '/api/ai/switches', { global: SW0.global, demo: SW0.demo, record: SW0.record, features: SW0.features }, tok);
  const real = errors.filter((e) => !/ResizeObserver|favicon/.test(e));
  ok('浏览器控制台没有报错', real.length === 0, real.slice(0, 5).join(' | '));
  await browser.close();
  const bad = results.filter((r) => !r[1]);
  console.log(`\n${results.length - bad.length}/${results.length} 通过` + (bad.length ? '；失败：' + bad.map((r) => r[0]).join('、') : ''));
  process.exit(bad.length ? 1 : 0);
})().catch(async (e) => {
  console.error(e);
  try { await PAGE.screenshot({ path: path.join(OUT, 'zz-failure.png') }); } catch { /* */ }
  process.exit(2);
});
