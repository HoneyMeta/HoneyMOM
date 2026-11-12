// Context-menu / toolbar actions per record type (open, trace, create linked document), keyed by entity. Import once to apply globally (re-registering the same name overrides).
// Non-AI actions (no ai flag) stay available when AI is off. AI actions set ai: true (greyed out with a hint when off).
// "Create linked document" goes to /e/<entity>?new=1&d_<field>=<value>: the data table page opens the create form with those fields prefilled.
import { $t } from '../i18n';
import { aiRouter, askAi, registerAiActions, type AiAction, type AiItem } from '../ai';

const go = (p: string) => aiRouter()?.push(p);
const isE = (...es: string[]) => (it: AiItem) => !!it.entity && es.includes(it.entity);
const idOf = (it: AiItem) => it.id ?? it.data?.id;
const newRec = (entity: string, d: Record<string, any>) => `/e/${entity}?new=1&` + Object.entries(d).filter(([, v]) => v !== undefined && v !== null && v !== '').map(([k, v]) => `d_${k}=${encodeURIComponent(String(v))}`).join('&');

const ACTIONS: AiAction[] = [
  // Equipment (Live Monitor cards, Equipment Register rows, equipment referenced in repair orders / alarms)
  { id: 'eq-oee', label: $t('home.oeeDowntimeTimeline'), icon: 'DataLine', order: 110, when: (it) => it.entity === 'equipment' && !!idOf(it), run: (it) => go(`/equipment/oee?eid=${idOf(it)}`) },
  { id: 'eq-repair', label: $t('pagesAiActions.requestRepairCreateRepairOrder'), icon: 'SetUp', order: 120, toolbar: false, when: (it) => it.entity === 'equipment' && !!idOf(it),
    run: (it) => go(newRec('repair_order', { equipment: idOf(it), fault: it.data?.[$t('common.alarm')] || it.data?.[$t('common.status')] === $t('common.down') ? `${it.label} ${it.data?.[$t('common.alarm')] || $t('common.down')}` : '' })) },
  { id: 'eq-maint', label: $t('pagesAiActions.checksPmPlan'), icon: 'Calendar', order: 130, when: (it) => it.entity === 'equipment' && !!idOf(it), run: (it) => go(`/e/maint_plan?f_equipment=${idOf(it)}`) },
  { id: 'eq-diagnose', label: $t('pagesAiActions.aiDiagnoseDowntime'), icon: 'MagicStick', ai: true, order: 25, when: (it) => it.entity === 'equipment',
    run: (it, sel) => askAi($t('pagesAiActions.whyWas0DownIn', [it.label]), sel, { action: 'diagnose' }) },
  // Work orders
  { id: 'wo-gantt', label: $t('pagesAiActions.viewInSchedulingGantt'), icon: 'Histogram', order: 110, when: isE('work_order'), run: () => go('/production/gantt') },
  { id: 'wo-parts', label: $t('pagesAiActions.partsOfThisWorkOrder'), icon: 'Postcard', order: 120, when: isE('work_order'), run: (it) => go(`/e/part?f_work_order=${idOf(it)}`) },
  { id: 'wo-reports', label: $t('common.productionReports'), icon: 'List', order: 125, when: isE('work_order'), run: (it) => go(`/e/report?f_work_order=${idOf(it)}`) },
  // Nonconformances / inspections
  { id: 'ncr-8d', label: $t('pagesAiActions.start8dReport'), icon: 'DocumentAdd', order: 120, when: isE('ncr'), run: (it) => go(newRec('report_8d', { title: $t('pagesAiActions.n0QualityIssue', [it.label]), d2: it.data?.[$t('common.defect')] || '' })) },
  { id: 'insp-ncr', label: $t('pagesAiActions.logNonconformance'), icon: 'CircleClose', order: 120, when: isE('insp_record'), run: (it) => go(newRec('ncr', { part: it.data?.part, remark: $t('pagesAiActions.n0FailedInspection', [it.label]) })) },
  // Repair orders, alarms, Andon: with equipment
  { id: 'rel-eq', label: $t('pagesAiActions.openEquipment'), icon: 'Monitor', order: 115, when: (it) => isE('repair_order', 'alarm', 'andon', 'maint_plan')(it) && !!it.data?.equipment_id,
    run: (it) => go(`/e/equipment?id=${it.data.equipment_id}`) },
  { id: 'alarm-repair', label: $t('pagesAiActions.requestRepairCreateRepairOrder'), icon: 'SetUp', order: 120, when: (it) => isE('alarm', 'andon')(it) && !!it.data?.equipment_id,
    run: (it) => go(newRec('repair_order', { equipment: it.data.equipment_id, fault: it.data?.[$t('common.content')] || it.label })) },
  // Heats, materials
  { id: 'heat-trace', label: $t('pagesAiActions.traceCastingsFromThisHeat'), icon: 'Connection', order: 110, when: (it) => it.entity === 'heat' || !!it.heat, run: (it) => go(`/trace?heat=${encodeURIComponent(it.heat || it.data?.[$t('common.heat')] || it.data?.code || '')}`) },
  { id: 'mat-txn', label: $t('nav.inventoryTransactions'), icon: 'Tickets', order: 110, when: (it) => it.entity === 'material' && !!idOf(it), run: (it) => go(`/e/stock_txn?f_material=${idOf(it)}`) },
  { id: 'mat-req', label: $t('nav.materialRequests'), icon: 'DocumentAdd', order: 120, when: (it) => it.entity === 'material' && !!idOf(it), run: (it) => go(newRec('material_request', { material: idOf(it) })) },
];
for (const k of ['row', 'device', 'record', 'kpi', 'chart-point', 'field', 'alert']) registerAiActions(k, ACTIONS);
export { newRec };
