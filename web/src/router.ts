import { createRouter, createWebHistory } from 'vue-router';
import { loadSession, session } from './api';
import { can, pagePerm } from './perm';
import Layout from './layout/Layout.vue';
import { MENU } from './layout/nav';
import { loadImplState } from './impl/state';          // M3: Setup Wizard state, optional modules

const P = (n: string) => () => import(`./pages/${n}.vue`);

// Menus are defined in layout/nav.ts; MENU export kept here for legacy code
export { MENU };

// meta.feature: required AI feature; when off, the shell shows the "Disabled" page (layout/FeatureOff.vue) without loading the page
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: P('Login'), meta: { public: true } },
    { path: '/screen', component: P('Screen') },
    { path: '/m', component: P('Mobile') },
    { path: '/station', component: P('Station') },          // Workstation Terminal (full-screen shop-floor PC, large touch buttons)
    {
      path: '/', component: Layout, children: [
        { path: '', component: P('Home') },
        // These entities have dedicated pages (static paths take precedence over e/:entity)
        { path: 'e/report', component: P('Reports') },
        { path: 'e/ncr', component: P('Ncr') },
        { path: 'e/rework', redirect: '/quality/rework' },
        { path: 'e/schedule_proposal', component: P('ScheduleAdjust') },
        { path: 'e/kb_doc', component: P('Knowledge') },
        { path: 'quality/fai', component: P('QualityGates'), meta: { tab: 'fai' } },
        { path: 'quality/hold', component: P('QualityGates'), meta: { tab: 'hold' } },
        { path: 'quality/rework', component: P('QualityGates'), meta: { tab: 'rework' } },
        { path: 'trace/recon', component: P('TraceRecon') },
        { path: 'labor/daily', component: P('LaborDaily') },
        { path: 'labor/hours', component: P('LaborDaily') },
        { path: 'e/:entity', component: P('EntityPage'), props: true },
        { path: 'production/gantt', component: P('Gantt') },
        { path: 'trace', component: P('Trace') },
        { path: 'quality', component: P('Quality') },
        { path: 'quality/spc', component: P('Spc') },
        { path: 'equipment/monitor', component: P('Monitor') },
        { path: 'equipment/oee', component: P('Oee') },
        { path: 'equipment/maint', component: P('Maint') },
        { path: 'warehouse', component: P('Stock') },
        { path: 'energy', component: P('Energy') },
        { path: 'foundry/heats', component: P('Heats') },
        { path: 'foundry/charge', component: P('Charge'), meta: { feature: 'charge_opt' } },
        { path: 'machining/tools', component: P('Tools') },
        { path: 'machining/defects', component: P('MachDefects') },
        { path: 'cross/backtrace', component: P('Backtrace') },
        { path: 'cross/coplan', component: P('Coplan') },
        { path: 'ai/chat', component: P('Chat'), meta: { feature: 'ask' } },
        { path: 'ai/daily', component: P('Daily') },          // Viewable with AI off (rule-based); only AI insight needs "AI Daily Report"
        { path: 'ai/automations', component: P('Automations'), meta: { feature: 'automation' } },
        { path: 'ai/watch', component: P('Watch'), meta: { feature: 'watch' } },
        { path: 'cycle', component: P('Cycle'), meta: { perm: 'cycle.view' } },                       // 5.0 M6: motion-level cycle time analysis
        { path: 'ai/skills', component: P('Skills'), meta: { feature: 'skills', perm: 'skill.use' } },  // 5.0 M6: AI skill library
        { path: 'system/mcp', component: P('McpSetup'), meta: { perm: 'system.apikey.manage' } },     // 5.0 M6: MCP connectivity
        { path: 'system', component: P('System') },
        { path: 'system/roles', component: P('AdminRoles'), meta: { perm: 'system.role.manage' } },
        { path: 'system/audit', component: P('AdminAudit'), meta: { perm: 'system.audit.view' } },
        { path: 'system/apikeys', component: P('AdminApiKeys'), meta: { perm: 'system.apikey.manage' } },
        { path: 'system/backups', component: P('AdminBackups'), meta: { perm: 'system.backup.view' } },
        { path: 'forbidden', component: P('Forbidden'), meta: { public: true } },
        { path: 'system/ai', component: P('AiSettings') },
        { path: 'agents', component: P('Agents'), meta: { feature: 'agents' } },
        { path: 'agents/:id', component: P('AgentEditor'), props: true, meta: { feature: 'agents' } },
        { path: 'approvals', component: P('Approvals') },          // Approval workflows (M3) don't depend on AI: with AI off only workflow approvals are shown
        // 5.0 implementation tools & configurability (M3)
        { path: 'impl', component: P('ImplHome'), meta: { perm: 'impl.impl_task.read' } },
        { path: 'impl/setup', component: P('ImplSetup'), meta: { perm: 'impl.setup.run' } },
        { path: 'impl/import', component: P('ImplImport'), meta: { perm: 'impl.import.run' } },
        { path: 'impl/import/history', component: P('ImplImport'), meta: { perm: 'impl.import.run', tab: 'history' } },
        { path: 'impl/packs', component: P('ImplPacks'), meta: { perm: 'impl.pack.view' } },
        { path: 'impl/fields', component: P('ImplFields'), meta: { perm: 'config.field.manage' } },
        { path: 'impl/forms', component: P('ImplForms'), meta: { perm: 'config.form.manage' } },
        { path: 'impl/flows', component: P('ImplFlows'), meta: { perm: 'config.flow.manage' } },
        { path: 'impl/reports', component: P('ImplReports') },
        { path: 'andon', component: P('Andon') },
        // 5.0 M2 Connectivity Center (plant model & connectivity): one page, tabs by meta.tab
        { path: 'onboard', component: P('Onboard'), meta: { tab: 'overview' } },
        { path: 'onboard/check', component: P('Onboard'), meta: { tab: 'check' } },
        { path: 'onboard/discover', component: P('Onboard'), meta: { tab: 'discover' } },
        { path: 'onboard/model', component: P('Onboard'), meta: { tab: 'model' } },
        { path: 'onboard/twin', component: P('Onboard'), meta: { tab: 'twin' } },
        { path: 'onboard/ai', component: P('Onboard'), meta: { tab: 'ai' } },
        // 5.0 shop-floor execution & quality compliance (M5, docs/dev/exec.md)
        { path: 'exec/labels', component: P('LabelDesigner') },
        { path: 'exec/pack', component: P('PackStation'), meta: { tab: 'station' } },
        { path: 'exec/ship', component: P('PackStation'), meta: { tab: 'ship' } },
        { path: 'exec/outsource', component: P('Outsource') },
        { path: 'exec/spares', component: P('Spares') },
        { path: 'exec/docs', component: P('DocControl') },
        { path: 'exec/ecn', component: P('Ecn') },
        { path: 'iatf/gauges', component: P('Gauges') },
        { path: 'iatf/pfmea', component: P('Pfmea') },
        { path: 'iatf/msa', component: P('Msa') },
        { path: 'iatf/complaints', component: P('Complaints') },
        // 5.0 M4 built-in ERP (docs/dev/erp.md): document pages share ErpDocs (meta.kind = document type)
        { path: 'sales/board', component: P('ErpSalesBoard'), meta: { perm: 'sales.sales_order.read' } },
        { path: 'sales/orders', component: P('ErpDocs'), meta: { kind: 'sales_order', perm: 'sales.sales_order.read' } },
        { path: 'sales/quotes', component: P('ErpDocs'), meta: { kind: 'quotation', perm: 'sales.quotation.read' } },
        { path: 'sales/deliveries', component: P('ErpDocs'), meta: { kind: 'delivery_order', perm: 'sales.delivery_order.read' } },
        { path: 'sales/returns', component: P('ErpDocs'), meta: { kind: 'sales_return', perm: 'sales.sales_return.read' } },
        { path: 'purchase/board', component: P('ErpPurchaseBoard'), meta: { perm: 'purchase.purchase_order.read' } },
        { path: 'purchase/orders', component: P('ErpDocs'), meta: { kind: 'purchase_order', perm: 'purchase.purchase_order.read' } },
        { path: 'purchase/receipts', component: P('ErpDocs'), meta: { kind: 'purchase_receipt', perm: 'purchase.purchase_receipt.read' } },
        { path: 'purchase/returns', component: P('ErpDocs'), meta: { kind: 'supplier_return', perm: 'purchase.supplier_return.read' } },
        { path: 'mrp', component: P('ErpMrp'), meta: { perm: 'mrp.mrp_suggestion.read' } },
        { path: 'finance/cost', component: P('ErpCost'), meta: { perm: 'cost.wo_cost.read' } },
        { path: 'finance/arap', component: P('ErpArAp'), meta: { perm: 'finance.ar_invoice.read' } },
        { path: 'finance/ar_invoices', component: P('ErpDocs'), meta: { kind: 'ar_invoice', perm: 'finance.ar_invoice.read' } },
        { path: 'finance/ap_invoices', component: P('ErpDocs'), meta: { kind: 'ap_invoice', perm: 'finance.ap_invoice.read' } },
        { path: 'finance/vouchers', component: P('ErpVoucher'), meta: { perm: 'finance.voucher.read' } },
        { path: 'system/erp', component: P('ErpIntegration'), meta: { perm: 'erp.monitor.view' } },
      ],
    },
  ],
});

router.beforeEach(async (to) => {
  if (to.meta.public) return true;
  if (!session.user && !(await loadSession())) return '/login?next=' + encodeURIComponent(to.fullPath);
  // Route guard: meta.perm first, otherwise infer page permission from the path (/e/<entity> → entity read). No permission → 403 page (no redirect to login).
  const perm = (to.meta.perm as string) || pagePerm(to.path);
  if (!can(perm)) return { path: '/forbidden', query: { from: to.fullPath, perm } };
  // M3: on a blank install with the Setup Wizard unfinished, authorized users go to the wizard first (implementation pages remain accessible)
  const st = await loadImplState();
  if (st.pending && st.can_setup && !to.path.startsWith('/impl')) return '/impl/setup';
  return true;
});
