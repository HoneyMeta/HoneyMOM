// ERP context / AI actions (feature key erp): MRP suggestions "AI Explain", "AI Draft Purchase Order" (action draft; the order is created only after approval), orders "AI Risk Check". Import once to apply globally.
import { $t } from '../i18n';
import { ElMessageBox } from 'element-plus';
import { h } from 'vue';
import { aiBus, aiRouter, openAiDock, registerAiActions, toast, type AiAction, type AiItem } from '../ai';
import { post } from '../api';

const idOf = (it: AiItem) => it.id ?? it.data?.id;

export function showText(title: string, text: string) {
  return ElMessageBox.alert(h('div', { class: 'erp-ai-text', style: 'white-space: pre-wrap; line-height: 1.7; max-height: 60vh; overflow: auto' }, text), title,
    { confirmButtonText: $t('common.gotIt'), customClass: 'erp-ai-box' }).catch(() => {});
}

export async function explainMrp(id: number) {
  try {
    const r = await post('/api/erp/ai/explain_mrp', { suggestion: id });
    showText($t('erpAiActions.whyThis0IsSuggested', [r.by === 'llm' ? $t('erpAiActions.ai') : $t('erpAiActions.rules')]), r.text);
    return r;
  } catch { return null; }
}

export async function draftPo(body: any, it?: AiItem) {
  openAiDock(true);
  aiBus.emit('draft-pending', { label: it?.label || $t('common.purchaseOrder'), op: 'purchase_order' });
  try {
    const d = await post('/api/erp/ai/draft_po', body);
    aiBus.emit('draft', { draft: d, item: it || { kind: 'row', label: d.title, key: 'po:' + d.id } });
    toast($t('erpAiActions.purchaseOrderDraftedItWill'), 'success', 2600);
    return d;
  } catch (e: any) {
    aiBus.emit('draft', { error: e?.message || String(e), item: it });
    return null;
  }
}

export async function atRisk() {
  try {
    const r = await post('/api/erp/ai/at_risk', {});
    showText($t('erpAiActions.orderRisk0', [r.by === 'llm' ? $t('erpAiActions.ai') : $t('erpAiActions.rules')]), r.text);
    return r;
  } catch { return null; }
}

const ACTIONS: AiAction[] = [
  { id: 'erp-mrp-explain', label: $t('erpAiActions.aiExplainThisSuggestion'), icon: 'MagicStick', ai: true, feature: 'erp', order: 24, when: (it) => it.entity === 'mrp_suggestion' && !!idOf(it),
    run: (it) => explainMrp(Number(idOf(it))) },
  { id: 'erp-mrp-po', label: $t('erpAiActions.aiDraftPurchaseOrder'), icon: 'DocumentAdd', ai: true, feature: 'erp', order: 26,
    when: (it) => (it.entity === 'mrp_suggestion' && it.data?.kind === 'buy') || (it.entity === 'material' && !!idOf(it)),
    run: (it) => draftPo(it.entity === 'mrp_suggestion' ? { suggestion: idOf(it) } : { material: idOf(it) }, it) },
  { id: 'erp-so-risk', label: $t('erpAiActions.aiFindAtRiskOrders'), icon: 'Warning', ai: true, feature: 'erp', order: 27, when: (it) => it.entity === 'sales_order', run: () => atRisk() },
  { id: 'erp-so-board', label: $t('common.orderBoard'), icon: 'DataLine', order: 112, when: (it) => it.entity === 'sales_order', run: () => aiRouter()?.push('/sales/board') },
  { id: 'erp-mat-mrp', label: $t('erpAiActions.mrpSuggestions'), icon: 'Histogram', order: 125, when: (it) => it.entity === 'material' && !!idOf(it), run: () => aiRouter()?.push('/mrp') },
];
for (const k of ['row', 'record', 'kpi']) registerAiActions(k as any, ACTIONS);
