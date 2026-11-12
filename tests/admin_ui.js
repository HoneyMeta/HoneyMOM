// Pinned to zh-CN (UI language + X-Lang): the assertions use the Chinese UI texts; tests/i18n_ui.js covers English.
// 5.0 管理页面走查（Playwright）：角色与权限、用户数据范围、审计日志、API 密钥、备份与恢复、系统状态里的数据库卡片；
// 受限角色：菜单按权限隐藏、直接打开没权限的地址显示"没有权限"、按钮按权限点隐藏。截图存 SHOTS_DIR。
// 用法（B 机，MES 已启动）：node tests/admin_ui.js     MES_URL / SHOTS_DIR 同 pages_ui.js
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.join(process.env.APPDATA, 'npm', 'node_modules', 'playwright'));

const BASE = (process.env.MES_URL || 'http://127.0.0.1:8900').replace(/\/$/, '');
const OUT = process.env.SHOTS_DIR || path.join(__dirname, '..', 'data', 'ui_shots', 'admin');
fs.mkdirSync(OUT, { recursive: true });
const results = [];
const RC = 'ui_line_' + (Date.now() % 100000);          // 每次一个新角色编码（可以在同一个库上反复跑）
const ok = (name, cond, detail = '') => { results.push([name, !!cond]); console.log((cond ? 'PASS ' : 'FAIL ') + name, detail); };
async function api(method, p, body, token) {
  const r = await fetch(BASE + p, { method, headers: { 'Content-Type': 'application/json', 'X-Lang': 'zh-CN', ...(token ? { Authorization: 'Bearer ' + token } : {}) }, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, j: await r.json().catch(() => ({})) };
}

(async () => {
  const tok = (await api('POST', '/api/login', { username: 'admin', password: 'honey123' })).j.token;
  const browser = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const errors = [];
  const ctxFor = async (t) => {
    const ctx = await browser.newContext({ viewport: { width: 1680, height: 1050 }, locale: 'zh-CN' });
    await ctx.addInitScript((tk) => { if (window.top !== window) return; localStorage.setItem('mes_token', tk); localStorage.setItem('hm_lang', 'zh-CN'); localStorage.setItem('mes_assist_open', '0'); localStorage.setItem('hm_welcomed:admin', '1'); localStorage.setItem('hm_welcomed:ui_l1', '1'); }, t);
    const page = await ctx.newPage();
    page.on('pageerror', (e) => errors.push(String(e)));
    page.on('console', (m) => { if (m.type() === 'error' && !/favicon|401|403|404/.test(m.text())) errors.push(m.text()); });
    return { ctx, page };
  };
  const { page } = await ctxFor(tok);
  global.PAGE = page;
  const shot = (n) => page.screenshot({ path: path.join(OUT, n + '.png') });

  // ---------- 角色与权限 ----------
  await page.goto(BASE + '/system/roles');
  await page.waitForSelector('[data-test=admin-roles] .hm-table tbody tr', { timeout: 10000 });
  const nRoles = await page.locator('[data-test=admin-roles] .hm-table tbody tr').count();
  ok('角色列表（内置 9 个角色）', nRoles >= 9, `${nRoles} 行`);
  await page.locator('[data-test=admin-roles] .hm-table tbody tr', { hasText: '质检员' }).first().click();
  await page.waitForSelector('[data-test=role-editor]');
  await page.locator('[data-test=role-editor] .el-collapse-item__header', { hasText: '质量管理' }).first().click();
  await page.waitForTimeout(500);
  const ncrUpd = page.locator('[data-test="perm-quality.ncr.update"] input');
  ok('质检员：不合格品·修改 已勾选（模块通配 quality.*）', await ncrUpd.isChecked());
  await shot('roles-qc');
  // 新建角色
  await page.click('[data-test=role-new]');
  await page.fill('[data-test=role-code]', RC);
  await page.fill('[data-test=role-name]', '一线班长（走查）');
  if (!(await page.locator('[data-test="perm-quality.ncr.read"]').isVisible())) {
    await page.locator('[data-test=role-editor] .el-collapse-item__header', { hasText: '质量管理' }).first().click();
    await page.waitForTimeout(400);
  }
  await page.locator('[data-test="perm-quality.ncr.read"]').click();
  await page.locator('[data-test="perm-quality.ncr.update"]').click();
  await page.locator('[data-test=role-editor] .el-radio-button', { hasText: '指定工厂' }).first().click();
  await page.waitForSelector('.scope-pick .el-tree-node');
  await page.locator('.scope-pick .el-tree-node__content').nth(2).locator('.el-checkbox').click();
  await page.waitForTimeout(200);
  await shot('roles-new');
  await page.click('[data-test=role-save]');
  let nr = null;
  for (let i = 0; i < 30 && !nr; i++) {
    await page.waitForTimeout(500);
    nr = ((await api('GET', '/api/admin/roles', null, tok)).j.items || []).find((r) => r.code === RC);
  }
  ok('界面新建角色（权限点 + 数据范围）保存成功', nr && nr.perms.includes('quality.ncr.update') && nr.scope, JSON.stringify(nr && { perms: nr.perms, scope: nr.scope }));
  // 用户
  await page.click('.el-tabs__item:has-text("用户的角色和数据范围")');
  await page.waitForSelector('[data-test=admin-users] .hm-table tbody tr');
  await shot('users');
  await page.click('[data-test=user-edit-leader]');
  await page.waitForSelector('[data-test=user-roles]');
  await page.waitForTimeout(400);
  await shot('user-edit');
  await page.keyboard.press('Escape');
  ok('用户列表 + 修改对话框', (await page.locator('[data-test=admin-users] .hm-table tbody tr').count()) >= 9);

  // ---------- 审计 ----------
  await page.goto(BASE + '/system/audit');
  await page.waitForSelector('[data-test=admin-audit] .hm-table tbody tr', { timeout: 10000 });
  ok('审计日志有记录', (await page.locator('[data-test=admin-audit] .hm-table tbody tr').count()) > 0);
  await page.click('[data-test=audit-verify]');
  await page.waitForSelector('[data-test=audit-verify-result]', { timeout: 20000 });
  ok('核对哈希链：完整', /哈希链完整/.test(await page.locator('[data-test=audit-verify-result]').innerText()));
  await page.locator('[data-test=admin-audit] .hm-table tbody tr', { hasText: '新增' }).first().click().catch(() => page.locator('[data-test=admin-audit] .hm-table tbody tr').first().click());
  await page.waitForSelector('.el-drawer .kv', { timeout: 5000 });
  await page.waitForTimeout(400);
  await shot('audit-detail');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
  await shot('audit');

  // ---------- API 密钥 ----------
  await page.goto(BASE + '/system/apikeys');
  await page.waitForSelector('[data-test=admin-apikeys]');
  await page.click('[data-test=key-new]');
  await page.fill('[data-test=key-name]', 'UI 走查网关');
  await page.waitForTimeout(300);
  await shot('apikey-new');
  await page.click('[data-test=key-save]');
  await page.waitForSelector('[data-test=key-plain]', { timeout: 8000 });
  const key = (await page.locator('[data-test=key-plain]').innerText()).trim();
  ok('新建密钥：只显示一次的明文', key.startsWith('hmk_'));
  await shot('apikey-created');
  const who = await fetch(BASE + '/api/whoami', { headers: { 'X-API-Key': key } }).then((r) => r.json());
  ok('界面建的密钥能调用接口', who.is_api_key === true, who.username);
  await page.keyboard.press('Escape');
  await page.locator('.el-dialog__footer button:has-text("我已保存")').click().catch(() => {});
  await page.waitForTimeout(600);
  await shot('apikeys');

  // ---------- 备份 ----------
  await page.goto(BASE + '/system/backups');
  await page.waitForSelector('[data-test=admin-backups]');
  await page.click('[data-test=backup-now]');
  await page.waitForFunction(() => document.querySelectorAll('[data-test=admin-backups] .hm-table tbody tr td').length > 1, null, { timeout: 180000 });
  await page.waitForTimeout(800);
  const drill = page.locator('[data-test^=backup-drill-]').first();
  await drill.click();
  await page.waitForSelector('[data-test=drill-report]', { timeout: 300000 });
  await page.waitForTimeout(500);
  ok('恢复演练报告：全部通过', /全部核对通过/.test(await page.locator('.el-dialog').last().innerText()));
  await shot('backup-drill');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(500);
  await shot('backups');

  // ---------- 系统状态 ----------
  await page.goto(BASE + '/system');
  await page.waitForSelector('[data-test=sys-db]', { timeout: 10000 });
  ok('系统状态：数据库 / 时序 / 断网缓存卡片', /结构版本/.test(await page.locator('[data-test=sys-db]').innerText()));
  await page.waitForTimeout(500);
  await shot('system-db');

  // ---------- 受限用户 ----------
  await api('POST', '/api/e/sys_user', { username: 'ui_l1', name: '走查受限用户', roles: RC, active: true }, tok);
  const u = ((await api('GET', '/api/e/sys_user?q=ui_l1', null, tok)).j.items || [])[0];
  await api('PUT', `/api/e/sys_user/${u.id}`, { roles: RC, active: true }, tok);
  await api('POST', `/api/e/sys_user/${u.id}/do/reset_pw`, { password: 'ui123456' }, tok);
  const t2 = (await api('POST', '/api/login', { username: 'ui_l1', password: 'ui123456' })).j.token;
  const { page: p2 } = await ctxFor(t2);
  await p2.goto(BASE + '/e/ncr');
  await p2.waitForTimeout(2500);
  const navText = await p2.locator('.hm-nav').innerText().catch(() => '');
  ok('受限用户：菜单只剩有权限的页面', navText.includes('不合格品') && !navText.includes('生产工单') && !navText.includes('角色与权限'));
  ok('受限用户：没有新增权限，不显示「新增」', (await p2.locator('[data-tour=ent-add]').count()) === 0);
  await p2.screenshot({ path: path.join(OUT, 'restricted-ncr.png') });
  await p2.goto(BASE + '/e/work_order');
  await p2.waitForSelector('[data-test=forbidden]', { timeout: 8000 }).catch(() => {});
  ok('受限用户：直接打开没权限的页面 → "没有权限"', (await p2.locator('[data-test=forbidden]').count()) === 1);
  await p2.screenshot({ path: path.join(OUT, 'forbidden.png') });
  await p2.goto(BASE + '/system/roles');
  await p2.waitForTimeout(1500);
  ok('受限用户：管理页面也拦住', (await p2.locator('[data-test=forbidden]').count()) === 1);

  ok('没有页面脚本错误', errors.length === 0, errors.slice(0, 3).join(' | '));
  // 清理走查数据（角色留着会让后面的测试看到多一个角色，不影响；用户停用）
  await api('PUT', `/api/e/sys_user/${u.id}`, { active: false }, tok);
  await browser.close();
  const pass = results.filter((r) => r[1]).length;
  console.log(`\n${pass}/${results.length} 通过  截图：${OUT}`);
  process.exit(pass === results.length ? 0 : 1);
})().catch(async (e) => {
  console.error(e);
  if (global.PAGE) await global.PAGE.screenshot({ path: path.join(OUT, 'error.png') }).catch(() => {});
  process.exit(2);
});
