// Global AI context actions (4.7): "Let AI draft an action" on equipment / alarm / work order rows / cutting tool rows; "Set as automation alert" on KPIs.
// A drafted action is a draft (DraftCard) shown in the right AI panel and runs only after approval. AiLayer calls installAiExtras() on mount.
import { $t } from '../i18n';
import { post } from '../api';
import { aiBus, aiRouter, aiState, openAiDock, registerAiActions, type AiItem } from './index';

/** Ask AI to draft an action for the selected object (POST /api/ai/drafts/suggest); the draft appears in the right panel. */
export async function suggestDraft(op: string, it: AiItem, hint = '') {
  openAiDock(true);
  aiBus.emit('draft-pending', { label: it.label, op });
  try {
    const d = await post('/api/ai/drafts/suggest', { op, hint, context: { kind: it.kind, entity: it.entity, id: it.id, label: it.label, data: it.data } });
    aiBus.emit('draft', { draft: d, item: it });
    return d;
  } catch (e: any) {
    aiBus.emit('draft', { error: e?.message || String(e), item: it });
    return null;
  }
}

const isEquip = (it: AiItem) => it.entity === 'equipment' || it.kind === 'device' || (it.entity === 'alarm' && !!it.id);
const automateText = (it: AiItem) => {
  const lab = it.label.replace(/\s+/g, ' ').slice(0, 40);
  return it.kind === 'device' ? $t('aiActions.alertTheMaintenanceTechnicianWhen', [lab]) : $t('aiActions.alertTheTeamLeaderWhen', [lab]);
};

let installed = false;
export function installAiExtras() {
  if (installed) return;
  installed = true;
  registerAiActions('*', [
    { id: 'ai_repair', label: $t('aiActions.aiDispatchRepairDraft'), icon: 'SetUp', ai: true, feature: 'ask', order: 52, when: isEquip,
      run: (it) => suggestDraft('repair_order', it) },
    { id: 'ai_andon', label: $t('aiActions.aiRaiseAndonDraft'), icon: 'Bell', ai: true, feature: 'ask', order: 53, when: (it) => isEquip(it) || it.kind === 'alert',
      disabled: (it) => (isEquip(it) || it.entity === 'equipment' ? false : $t('aiActions.thisItemHasNoMatching')), run: (it) => suggestDraft('andon_raise', it) },
    { id: 'ai_sched', label: $t('aiActions.aiAdjustScheduleDraft'), icon: 'Calendar', ai: true, feature: 'ask', order: 54, when: (it) => it.entity === 'work_order' && !!it.id,
      run: (it) => suggestDraft('schedule_change', it) },
    { id: 'ai_tool', label: $t('aiActions.aiAdjustToolLifeDraft'), icon: 'Tools', ai: true, feature: 'ask', order: 54, when: (it) => it.entity === 'tool' && !!it.id,
      run: (it) => suggestDraft('param_adjust', it) },
    { id: 'ai_automate', label: $t('aiActions.setUpAnAutomatedAlert'), icon: 'AlarmClock', ai: true, feature: 'automation', order: 58,
      when: (it) => ['kpi', 'field', 'chart-point', 'device', 'alert'].includes(it.kind),
      run: (it) => aiRouter()?.push({ path: '/ai/automations', query: { text: automateText(it) } }) },
  ]);
}

/** Currently selected context (for other modules) */
export const selectedItems = () => aiState.items.slice();
