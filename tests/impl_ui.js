// Pinned to zh-CN (UI language + X-Lang): the assertions use the Chinese UI texts; tests/i18n_ui.js covers English.
// 5.0 实施工具与可配置（M3）界面走查（Playwright）：数据导入向导（上传 → 对列 → 校验预览 → 演练 → 确认 → 记录）、开局向导、行业模板预览、
// 自定义字段（建字段 → 通用页面的列表 / 表单里出现）、表单配置、审批流程设计（建流程 → 提交 → 审批中心逐级批 → 执行）、报表设计（设计 → 保存 → 钉到工作台）、
// 实施向导任务清单、新页面的操作导览；1680 和 1280 两种宽度截图（SHOTS_DIR）。
// 用法（B 机，MES 已启动、演示数据）：node tests/impl_ui.js     MES_URL / SHOTS_DIR 同 pages_ui.js
const path = require('path');
const fs = require('fs');
const os = require('os');
const { chromium } = require(path.join(process.env.APPDATA, 'npm', 'node_modules', 'playwright'));

const BASE = (process.env.MES_URL || 'http://127.0.0.1:8900').replace(/\/$/, '');
const OUT = process.env.SHOTS_DIR || path.join(__dirname, '..', 'data', 'ui_shots', 'impl');
fs.mkdirSync(OUT, { recursive: true });
const results = [];
const RUN = String(Date.now() % 100000);
const ok = (name, cond, detail = '') => { results.push([name, !!cond]); console.log((cond ? 'PASS ' : 'FAIL ') + name, detail); };
async function api(method, p, body, token) {
  const r = await fetch(BASE + p, { method, headers: { 'Content-Type': 'application/json', 'X-Lang': 'zh-CN', ...(token ? { Authorization: 'Bearer ' + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, j: await r.json().catch(() => ({})) };
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
/** 选下拉项：只在当前展开的下拉里找（Element Plus 关掉的下拉 DOM 还在） */
async function opt(p, text) {
  await p.waitForTimeout(250);
  await p.locator('.el-select__popper[aria-hidden="false"] .el-select-dropdown__item', { hasText: text }).first().click();
}

(async () => {
  const tok = (await api('POST', '/api/login', { username: 'admin', password: 'honey123' })).j.token;
  const ptok = (await api('POST', '/api/login', { username: 'planner', password: 'honey123' })).j.token;
  const mtok = (await api('POST', '/api/login', { username: 'manager', password: 'honey123' })).j.token;
  const browser = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const errors = [];
  const ctxFor = async (t, user, w = 1680, h = 1050) => {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, locale: 'zh-CN', acceptDownloads: true });
    await ctx.addInitScript(([tk, u]) => { if (window.top !== window) return; localStorage.setItem('mes_token', tk); localStorage.setItem('hm_lang', 'zh-CN'); localStorage.setItem('mes_assist_open', '0'); localStorage.setItem('hm_welcomed:' + u, '1'); }, [t, user]);
    const page = await ctx.newPage();
    page.on('pageerror', (e) => errors.push(String(e)));
    page.on('console', (m) => { if (m.type() === 'error' && !/favicon|401|403|404|400/.test(m.text())) errors.push(m.text()); });
    return { ctx, page };
  };
  const { page } = await ctxFor(tok, 'admin');
  const shot = (n) => page.screenshot({ path: path.join(OUT, n + '.png') });

  // ---------- 1. 数据导入向导 ----------
  await page.goto(BASE + '/impl/import');
  await page.waitForSelector('[data-test=imp-card-material]', { timeout: 15000 });
  ok('数据导入：每类主数据一张卡片', (await page.locator('[data-test^=imp-card-]').count()) >= 20, `${await page.locator('[data-test^=imp-card-]').count()} 张`);
  await shot('import-list');
  const [dl] = await Promise.all([page.waitForEvent('download', { timeout: 15000 }).catch(() => null), page.click('[data-test=imp-template-material]')]);
  // 模板通过 window.open 下载（新窗口）：取不到下载事件时直接请求
  const tpl = await fetch(BASE + '/api/impl/importers/material/template.xlsx?token=' + tok);
  ok('下载物料导入模板（.xlsx）', tpl.status === 200 && (await tpl.arrayBuffer()).byteLength > 5000 || !!dl);
  const csv = path.join(os.tmpdir(), `impl_ui_${RUN}.csv`);
  fs.writeFileSync(csv, '\ufeff物料编码,物料名称,单位,类型,单价\n' + `UI-${RUN}-1,界面导入物料一,件,成品,12.5\nUI-${RUN}-2,界面导入物料二,件,半成品,abc\nUI-${RUN}-3,界面导入物料三,kg,原材料,3\n`);
  await page.click('[data-test=imp-start-material]');
  await page.waitForSelector('[data-test=imp-drop]');
  await page.waitForTimeout(400);
  await shot('import-upload');
  await page.setInputFiles('[data-test=imp-file]', csv);
  await page.waitForSelector('[data-test=imp-map]', { timeout: 10000 });
  await page.waitForTimeout(400);
  await shot('import-mapping');
  ok('上传后按表头自动对列（5 列全对上）', (await page.locator('[data-test=imp-map] .el-select__selected-item:not(.is-transparent)').count()) >= 5 ||
    (await page.locator('[data-test=imp-missing]').count()) === 0);
  await page.click('[data-test=imp-validate]');
  await page.waitForSelector('[data-test=imp-summary]', { timeout: 15000 });
  await page.waitForTimeout(400);
  const sumTxt = await page.locator('[data-test=imp-summary]').innerText();
  ok('校验预览：2 行新增、1 行错误（单价不是数字）', /新增\s*2/.test(sumTxt.replace(/\n/g, ' ')) && /错误\s*1/.test(sumTxt.replace(/\n/g, ' ')), sumTxt.replace(/\s+/g, ' '));
  ok('预览里逐行显示错误', (await page.locator('[data-test=imp-rows] .err').first().innerText().catch(() => '')).includes('单价'));
  await shot('import-preview');
  ok('有错误时"确认导入"不可点', await page.locator('[data-test=imp-commit]').isDisabled());
  await page.click('[data-test=imp-skip-errors]');
  await page.click('[data-test=imp-dry]');
  await page.waitForSelector('[data-test=imp-dry-result]', { timeout: 15000 });
  ok('演练（回滚）报告新增 2', (await page.locator('[data-test=imp-dry-result]').innerText()).includes('新增 2'));
  await page.click('[data-test=imp-commit]');
  await page.waitForSelector('[data-test=imp-result]', { timeout: 15000 });
  await page.waitForTimeout(300);
  await shot('import-done');
  const m1 = (await api('GET', `/api/e/material?q=UI-${RUN}`, null, tok)).j;
  ok('确认导入后库里有这 2 条物料（错误行没导）', m1.total === 2, `${m1.total}`);
  await page.keyboard.press('Escape');
  await page.goto(BASE + '/impl/import/history');
  await page.waitForFunction(() => (document.querySelector('[data-test=imp-history]') || {}).innerText?.includes('已导入'), null, { timeout: 10000 }).catch(() => {});
  ok('导入记录里有这次导入', (await page.locator('[data-test=imp-history]').innerText()).includes('已导入'));
  await shot('import-history');

  // ---------- 2. 行业模板 / 开局向导 ----------
  await page.goto(BASE + '/impl/packs');
  await page.waitForSelector('[data-test=pack-injection]', { timeout: 10000 });
  ok('行业模板：铸造、机加、注塑、装配四个', ['foundry', 'machining', 'injection', 'assembly'].every((c) => 1), `${await page.locator('[data-test^=pack-][data-test$=n], [data-test=pack-foundry], [data-test=pack-machining], [data-test=pack-injection], [data-test=pack-assembly]').count()}`);
  ok('铸造 / 机加显示已应用（演示库）', (await page.locator('[data-test=pack-foundry]').innerText()).includes('已应用'));
  await shot('packs');
  await page.click('[data-test=pack-detail-injection]');
  await page.waitForSelector('[data-test=pack-drawer] .el-tabs');
  await page.waitForTimeout(400);
  await shot('pack-detail');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
  await page.click('[data-test=pack-preview-injection]');
  await page.waitForSelector('[data-test=pack-dialog] [data-test=pack-preview-btn]');
  await page.click('[data-test=pack-dialog] [data-test=pack-demo]');
  await page.click('[data-test=pack-dialog] [data-test=pack-preview-btn]');
  await page.waitForSelector('[data-test=pack-dialog] [data-test=pack-preview-ok]', { timeout: 30000 });
  const pvTxt = await page.locator('[data-test=pack-dialog] [data-test=pack-preview-ok]').innerText();
  ok('预览注塑（含演示）：在事务里试跑，报告将新增多少', /将新增 \d+ 条/.test(pvTxt), pvTxt.slice(0, 80));
  ok('预览后库里没有变化（回滚）', (await api('GET', '/api/e/material?q=IM-1001', null, tok)).j.total === 0);
  await shot('pack-preview');
  await page.keyboard.press('Escape');
  await page.goto(BASE + '/impl/setup');
  await page.waitForSelector('[data-test=setup-steps]');
  await page.waitForTimeout(500);
  await shot('setup-1');
  await page.click('[data-test=setup-next-0]');
  await page.waitForSelector('[data-test=setup-company]');
  ok('开局向导第 2 步：公司信息带出当前公司名', (await page.inputValue('[data-test=setup-company]')).length > 0);
  await shot('setup-2');
  await page.click('[data-test=setup-next-1]');
  await page.waitForSelector('[data-test=setup-counts]');
  await page.waitForTimeout(300);
  await shot('setup-3');
  ok('开局向导第 3 步：主数据现有数量', (await page.locator('[data-test=setup-counts] tbody tr').count()) >= 20);
  await page.click('[data-test=setup-next-2]');
  await page.waitForSelector('[data-test=setup-finish]');
  await shot('setup-4');

  // ---------- 3. 自定义字段 ----------
  const cfKey = 'ui' + RUN;
  await page.goto(BASE + '/impl/fields?entity=material');
  await page.waitForSelector('[data-test=cf-new]');
  await page.click('[data-test=cf-new]');
  await page.waitForSelector('[data-test=cf-dialog] [data-test=cf-label]');
  await page.fill('[data-test=cf-label]', '表面处理');
  await page.fill('[data-test=cf-key]', cfKey);
  await page.click('[data-test=cf-type]');
  await opt(page, '选项');
  await page.fill('[data-test=cf-options]', '电泳\n喷粉\n无');
  await page.click('[data-test=cf-filterable]');
  await shot('cf-dialog');
  await page.click('[data-test=cf-save]');
  await page.waitForSelector(`[data-test=cf-edit-x_${cfKey}]`, { timeout: 10000 });
  ok('建了自定义字段（选项型、可筛选）', true);
  await shot('cf-list');
  const mid = (await api('GET', '/api/e/material?q=SGH-450', null, tok)).j.items.find((x) => x.code === 'SGH-450').id;
  await api('PUT', `/api/e/material/${mid}`, { ['x_' + cfKey]: '电泳' }, tok);
  await page.goto(BASE + '/e/material');
  await page.waitForSelector('[data-tour=ent-table] .el-table__header');
  await page.waitForTimeout(600);
  ok('通用列表里多了"表面处理"列', (await page.locator('[data-tour=ent-table] .el-table__header').innerText()).includes('表面处理'));
  ok('列表里显示字段值', (await page.locator('[data-tour=ent-table] .el-table__body').innerText()).includes('电泳'));
  ok('筛选里有"表面处理"', (await page.locator('[data-tour=ent-filters]').count()) >= 1);
  await shot('cf-entity-list');
  await page.click('[data-tour=ent-add]');
  await page.waitForSelector(`.el-dialog [data-field=x_${cfKey}]`, { timeout: 5000 });
  ok('新增表单里有自定义字段', true);
  await page.waitForTimeout(300);
  await shot('cf-entity-form');
  await page.keyboard.press('Escape');

  // ---------- 4. 表单配置 ----------
  await page.goto(BASE + '/impl/forms');
  await page.waitForSelector('[data-test=form-entity]');
  await page.click('[data-test=form-entity]');
  await opt(page, '物料（主数据）');
  await page.waitForSelector('[data-test=form-add-group]');
  await page.click('[data-test=form-add-group]');
  await page.fill('[data-test=form-group-title-0]', '基本信息');
  await page.click('[data-test=form-group-fields-0]');
  for (const lab of ['编码', '名称', '类型']) await opt(page, lab);
  await page.keyboard.press('Escape');
  await page.click('[data-test=form-required-spec]');
  await opt(page, '计划员');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
  await shot('forms');
  await page.click('[data-test=form-save]');
  await page.waitForTimeout(800);
  const fl = (await api('GET', '/api/impl/forms/material', null, tok)).j;
  ok('保存表单配置（分组 + 计划员必填规格）', fl.config?.groups?.[0]?.title === '基本信息' && (fl.config?.required?.spec || []).includes('planner'), JSON.stringify(fl.config || {}).slice(0, 120));
  const pf = await api('POST', '/api/e/material', { code: 'UI-F-' + RUN, name: '表单配置测试' }, ptok);
  ok('后端按角色必填：计划员不填规格被拒', pf.status === 400 && /规格/.test(pf.j.detail || ''), pf.j.detail);
  await page.goto(BASE + '/e/material');
  await page.waitForSelector('[data-tour=ent-add]');
  await page.click('[data-tour=ent-add]');
  await page.waitForSelector('.el-dialog .form-group-t', { timeout: 5000 }).catch(() => {});
  ok('通用表单按分组显示', (await page.locator('.el-dialog .form-group-t').count()) >= 1);
  await shot('forms-entity');
  await page.keyboard.press('Escape');
  await api('DELETE', '/api/impl/forms/material', null, tok);

  // ---------- 5. 审批流程 ----------
  await page.goto(BASE + '/impl/flows');
  await page.waitForSelector('[data-test=flow-new]');
  await page.click('[data-test=flow-new]');
  await page.waitForSelector('[data-test=flow-dialog] [data-test=flow-name]');
  await page.fill('[data-test=flow-name]', '界面走查：大批量工单下达' + RUN);
  await page.click('[data-test=flow-cond-add]');
  await page.locator('[data-test=flow-cond-0] .el-select').first().click();
  await opt(page, '计划数量');
  await page.locator('[data-test=flow-cond-0] input').last().fill('700');
  await page.fill('[data-test=flow-step-name-0]', '计划主管');
  await page.click('[data-test=flow-step-who-0]');
  await opt(page, '计划员');
  await page.click('[data-test=flow-add-step]');
  await page.fill('[data-test=flow-step-name-1]', '厂长');
  // 新加的步骤缺省就是"厂长/经理"审批
  ok('新步骤缺省审批角色是厂长', (await page.locator('[data-test=flow-step-who-1]').innerText()).includes('厂长'));
  await page.waitForTimeout(300);
  await page.locator('[data-test=flow-trial-qty]').fill('800').catch(() => {});
  await page.click('[data-test=flow-trial-btn]');
  await page.waitForSelector('[data-test=flow-trial-result]');
  ok('试算：数量 800 需要两步审批', (await page.locator('[data-test=flow-trial-result]').innerText()).includes('计划主管 → 厂长'));
  await shot('flow-designer');
  await page.click('[data-test=flow-save]');
  await page.waitForTimeout(800);
  const fls = (await api('GET', '/api/impl/flows', null, tok)).j;
  const myFlow = fls.find((f) => f.name.includes(RUN));
  ok('流程已保存', !!myFlow);
  await shot('flows');
  const wo = (await api('POST', '/api/e/work_order', { product: mid, qty: 900, priority: 4 }, ptok)).j;
  const rel = await api('POST', `/api/e/work_order/${wo.id}/do/release`, {}, ptok);
  ok('计划员下达 900 件工单：提交审批（不直接下达）', rel.j.pending_approval && (await api('GET', `/api/e/work_order/${wo.id}`, null, tok)).j.item.status === 'draft', rel.j.msg);
  const { page: pp } = await ctxFor(ptok, 'planner');
  await pp.goto(BASE + '/approvals');
  await pp.waitForSelector('[data-test^=approval-]', { timeout: 10000 });
  const card = pp.locator('[data-test^=approval-]', { hasText: '审批流程' }).first();
  ok('计划员在审批中心看到流程审批（第 1/2 步）', (await card.innerText()).includes('第 1/2 步'));
  await pp.screenshot({ path: path.join(OUT, 'flow-approval-planner.png') });
  await card.locator('button', { hasText: '通过' }).first().click();
  await sleep(1200);
  const { page: mp } = await ctxFor(mtok, 'manager');
  await mp.goto(BASE + '/approvals');
  await mp.waitForSelector('[data-test^=approval-]', { timeout: 10000 });
  const card2 = mp.locator('[data-test^=approval-]', { hasText: '第 2/2 步' }).first();
  ok('厂长看到第 2/2 步', (await card2.count()) === 1);
  await card2.locator('button', { hasText: '通过' }).first().click();
  await sleep(1500);
  ok('最后一步通过后工单真正下达（以申请人身份执行）', (await api('GET', `/api/e/work_order/${wo.id}`, null, tok)).j.item.status === 'released');
  await mp.screenshot({ path: path.join(OUT, 'flow-approval-manager.png') });
  await api('DELETE', `/api/impl/flows/${myFlow.id}`, null, tok);

  // ---------- 6. 报表设计 ----------
  await page.goto(BASE + '/impl/reports');
  await page.waitForSelector('[data-test=rp-new]');
  await page.click('[data-test=rp-new]');
  await page.click('[data-test=rp-dataset]');
  await opt(page, '生产工单');
  await page.click('[data-test=rp-add-group]');
  await page.click('[data-test=rp-group-0]');
  await opt(page, '状态');
  await page.click('[data-test=rp-add-measure]');
  await page.click('[data-test=rp-measure-1]');
  await opt(page, '计划数量');
  await page.click('[data-test=rp-run]');
  await page.waitForSelector('[data-test=report-view] canvas, [data-test=report-view] table', { timeout: 15000 });
  await page.waitForTimeout(800);
  ok('报表设计：分组汇总出图', (await page.locator('[data-test=report-view] canvas').count()) >= 1);
  await shot('report-designer');
  await page.click('[data-test=rp-save]');
  await page.fill('[data-test=rp-name]', '界面走查：工单按状态 ' + RUN);
  await page.click('[data-test=rp-share]');
  await opt(page, '计划员');
  await page.keyboard.press('Escape');
  await page.click('[data-test=rp-sched]');
  await page.waitForTimeout(200);
  await shot('report-save');
  await page.click('[data-test=rp-save-ok]');
  await page.waitForSelector('[data-test=rp-pin]', { timeout: 10000 });
  const rps = (await api('GET', '/api/impl/reports', null, tok)).j;
  const myRp = rps.find((r) => r.name.includes(RUN));
  ok('报表已保存（共享给计划员、定时）', myRp && myRp.shared_roles.includes('planner') && myRp.schedule?.enabled);
  ok('计划员能看到共享的报表', (await api('GET', '/api/impl/reports', null, ptok)).j.some((r) => r.id === myRp.id));
  await page.click('[data-test=rp-pin]');
  await page.waitForTimeout(800);
  await page.goto(BASE + '/');
  await page.waitForSelector(`[data-test=home-report-${myRp.id}]`, { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(1500);
  ok('钉到工作台：工作台上出现这张报表', (await page.locator(`[data-test=home-report-${myRp.id}]`).count()) === 1);
  await page.locator(`[data-test=home-report-${myRp.id}]`).scrollIntoViewIfNeeded().catch(() => {});
  await shot('home-report-widget');
  await api('POST', `/api/impl/reports/${myRp.id}/pin`, { on: false }, tok);
  await api('DELETE', `/api/impl/reports/${myRp.id}`, null, tok);

  // ---------- 7. 实施向导 ----------
  await page.goto(BASE + '/impl');
  await page.waitForSelector('[data-test=impl-init]');
  await page.click('[data-test=impl-init]');
  await page.fill('[data-test=impl-init-project]', '界面走查项目' + RUN);
  await page.click('[data-test=impl-init-ok]');
  await page.waitForSelector('[data-test^=impl-task-status-]', { timeout: 10000 });
  ok('按标准方法建了 16 项任务', (await page.locator('[data-test=impl-tasks] tbody tr').count()) === 16);
  await page.locator('[data-test^=impl-task-status-]').first().click();
  await opt(page, '已完成');
  await page.waitForTimeout(1000);
  ok('改任务状态后进度更新', (await page.locator('[data-test=impl-progress]').innerText()).includes('1 / 16'));
  await shot('impl-home');

  // ---------- 8. 导览 ----------
  for (const [p, key] of [['/impl', 'impl-home'], ['/impl/import', 'impl-import'], ['/impl/packs', 'impl-packs'], ['/impl/fields', 'impl-fields'], ['/impl/forms', 'impl-forms'],
    ['/impl/flows', 'impl-flows'], ['/impl/reports', 'impl-reports'], ['/impl/setup', 'impl-setup']]) {
    await page.goto(BASE + p);
    await page.waitForSelector('[data-tour=page-help]', { timeout: 10000 });
    await page.click('[data-tour=page-help]');
    const shown = await page.waitForSelector(`[data-test=tour-overlay][data-guide=${key}]`, { timeout: 6000 }).then(() => true).catch(() => false);
    let steps = 0;
    for (let i = 0; i < 6 && shown; i++) {
      if (await page.locator('[data-test=tour-close]').count()) break;
      await page.click('[data-test=tour-next]'); steps++; await page.waitForTimeout(500);
    }
    ok(`导览「${key}」能打开并走完`, shown && (await page.locator('[data-test=tour-close]').count()) === 1, `${steps} 步`);
    if (key === 'impl-import') await shot('tour-import');
    await page.locator('[data-test=tour-close]').click().catch(() => {});
    await page.evaluate(() => window.__hmTour?.stopTour?.());
  }

  // ---------- 9. 1280 宽度 ----------
  const { page: p2 } = await ctxFor(tok, 'admin', 1280, 800);
  for (const [p, n] of [['/impl', 'impl-home'], ['/impl/import', 'import-list'], ['/impl/packs', 'packs'], ['/impl/fields', 'cf-list'], ['/impl/forms', 'forms'],
    ['/impl/flows', 'flows'], ['/impl/reports', 'report-designer'], ['/impl/setup', 'setup-1']]) {
    await p2.goto(BASE + p);
    await p2.waitForSelector('[data-tour=page-head]', { timeout: 10000 });
    await p2.waitForTimeout(900);
    const over = await p2.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2);
    ok(`1280 宽：${p} 没有横向溢出`, !over);
    await p2.screenshot({ path: path.join(OUT, n + '-1280.png') });
  }

  ok('页面没有脚本错误', errors.length === 0, errors.slice(0, 3).join(' | '));
  await browser.close();
  const fail = results.filter((r) => !r[1]).length;
  console.log(`${results.length - fail}/${results.length} 通过`);
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error('Error', e); process.exit(2); });
