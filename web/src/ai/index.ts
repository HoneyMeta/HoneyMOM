// AI selection framework: any element on a page can declare an "AI context"; users hand it to AI via Ctrl+click / right-click / floating toolbar.
// Usage: web/src/design/MIGRATION.md. Public API (other pages use only these):
//   v-ai="{ kind, id, label, data }"            element declares AI context (directive.ts)
//   v-ai-rows="{ kind, entity, rows, item }"     each el-table row declares AI context
//   registerAiActions(kind, actions)            add context-menu/toolbar actions for an element kind (returns an unregister fn)
//   useAiContext()                              read/update the current AI context (selected elements)
//   askAi(prompt, context?, opts?)              hand a task to the right AI panel (when AI is off, shows a hint and returns false)
//   aiOn(feature?) / aiFeatureLabel(feature)    whether an AI feature is on (master switch && feature switch)
//   aiBus.on/off(event, fn)                     events: context-change / ask / action / dock
//   4.7: AiBlocks (structured answer blocks), DraftCard (action drafts), WatchFeed (AI Watch), PinnedBlocks (pinned to Home), suggestDraft (actions.ts)
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, reactive } from 'vue';
import type { Router } from 'vue-router';
import { get, session } from '../api';

export type AiKind = 'kpi' | 'chart-point' | 'row' | 'device' | 'record' | 'field' | 'alert' | 'text' | 'page' | (string & {});

export interface AiItem {
  kind: AiKind;
  /** Human-readable name shown on the context tag, e.g. "Avg OEE 72.4%", "CNC3 Machining Center" */
  label: string;
  id?: string | number;
  /** Structured data for AI (value, unit, time range, baseline…); numbers here are authoritative */
  data?: any;
  /** Corresponding MES entity (work_order / equipment / ncr …): enables "Open record"; the assistant reads the full record */
  entity?: string;
  /** For traceability: part code / heat number */
  sn?: string;
  heat?: string;
  /** URL of the page that opens it */
  link?: string;
  /** Unique key (default kind:entity:id:label) */
  key?: string;
}

export interface AiAction {
  id: string;
  label: string;
  /** Element Plus icon name, e.g. 'MagicStick' */
  icon?: string;
  /** Requires AI: greyed out when AI is off; clicking shows "AI is off (enable it in System Settings)" */
  ai?: boolean;
  /** Which AI feature is required (default ask = Ask Data assistant) */
  feature?: string;
  /** Show only for elements that match the condition */
  when?: (item: AiItem) => boolean;
  /** Reason when currently unavailable (shown greyed out + tooltip) */
  disabled?: (item: AiItem) => string | false | null | undefined;
  run: (item: AiItem, selected: AiItem[]) => any;
  /** Sort order, lower first; built-in actions use 10~90 */
  order?: number;
  /** Show on the floating toolbar (by default only the built-in ones) */
  toolbar?: boolean;
}

export const AI_OFF = $t('aiIndex.aiIsTurnedOffEnable');
/** Hint (placed below the top bar so it doesn't cover the search box) */
export function toast(message: string, type: 'info' | 'success' | 'warning' | 'error' = 'info', duration = 1800) {
  ElMessage({ type, message, grouping: true, duration, offset: 64 });
}
const K_OPEN = 'mes_assist_open', K_W = 'hm_dock_w', K_CTX = 'hm_ai_ctx';

export const aiState = reactive({
  items: [] as AiItem[],
  switches: null as any,
  dockOpen: false,
  dockWidth: 400,
  /** Text prefilled in the panel input ("Ask AI…") */
  draft: '',
});
try {
  aiState.dockOpen = localStorage.getItem(K_OPEN) === '1';
  aiState.dockWidth = Math.min(720, Math.max(320, Number(localStorage.getItem(K_W)) || 400));
  aiState.items = JSON.parse(sessionStorage.getItem(K_CTX) || '[]');
} catch { /* incognito mode etc. */ }

let _router: Router | null = null;
export function setAiRouter(r: Router) { _router = r; }
export const aiRouter = () => _router;

// ---------------- Events ----------------
type Fn = (payload?: any) => void;
const handlers: Record<string, Set<Fn>> = {};
export const aiBus = {
  on(ev: 'context-change' | 'ask' | 'action' | 'dock' | 'focus-input' | string, fn: Fn) { (handlers[ev] ||= new Set()).add(fn); return () => handlers[ev].delete(fn); },
  off(ev: string, fn: Fn) { handlers[ev]?.delete(fn); },
  emit(ev: string, payload?: any) { handlers[ev]?.forEach((f) => { try { f(payload); } catch (e) { console.error(e); } }); },
};

// ---------------- Switches ----------------
export async function loadAiSwitches() {
  if (!session.token) return null;
  try { aiState.switches = await get('/api/ai/switches', true); } catch { /* not logged in */ }
  return aiState.switches;
}
export function setAiSwitches(s: any) { aiState.switches = s; }
/** Whether an AI feature is available (master switch && feature switch). Without feature, checks only the master switch. */
export function aiOn(feature?: string) {
  const s = aiState.switches;
  if (!s || !s.global) return false;
  return feature ? !!s.effective?.[feature] : true;
}
export function aiFeatureLabel(f?: string) { return aiState.switches?.labels?.find((x: any) => x.key === f)?.label || $t('common.aiFeatures'); }
/** Hint text when off */
export function aiOffText(feature?: string) {
  if (!aiOn()) return AI_OFF;
  return feature && !aiOn(feature) ? $t('aiIndex.n0IsTurnedOffEnable', [aiFeatureLabel(feature)]) : '';
}
export const aiMode = computed(() => aiState.switches?.mode || 'off');

// ---------------- Context ----------------
export function itemKey(it: AiItem) { return it.key || [it.kind, it.entity || '', it.id ?? '', it.id === undefined || it.id === null || it.id === '' ? it.label : ''].join(':'); }
export function normalizeItem(v: any): AiItem | null {
  if (!v || typeof v !== 'object') return null;
  const it: AiItem = { ...v, kind: v.kind || 'field', label: String(v.label ?? v.id ?? '') };
  it.key = itemKey(it);
  return it;
}
function persist() { try { sessionStorage.setItem(K_CTX, JSON.stringify(aiState.items.slice(0, 20))); } catch { /* */ } }
function changed() { persist(); aiBus.emit('context-change', aiState.items); }

export function useAiContext() {
  const has = (it: AiItem | string) => aiState.items.some((x) => x.key === (typeof it === 'string' ? it : itemKey(it)));
  function add(it: AiItem | AiItem[]) {
    for (const x of Array.isArray(it) ? it : [it]) {
      const n = normalizeItem(x);
      if (n && !aiState.items.some((y) => y.key === n.key)) aiState.items.push(n);
    }
    if (aiState.items.length > 20) aiState.items.splice(0, aiState.items.length - 20);
    changed();
  }
  function remove(it: AiItem | string) {
    const k = typeof it === 'string' ? it : itemKey(it);
    const i = aiState.items.findIndex((x) => x.key === k);
    if (i >= 0) { aiState.items.splice(i, 1); changed(); }
  }
  /** Remove if present, add if absent; returns whether it is in the context afterwards */
  function toggle(it: AiItem) { const n = normalizeItem(it)!; if (has(n)) remove(n); else add(n); return has(n); }
  function clear() { if (aiState.items.length) { aiState.items.splice(0); changed(); } }
  return { items: computed(() => aiState.items), has, add, remove, toggle, clear };
}
export const aiContext = useAiContext();

// ---------------- Panel ----------------
export function openAiDock(open = true) {
  aiState.dockOpen = open;
  try { localStorage.setItem(K_OPEN, open ? '1' : '0'); } catch { /* */ }
  aiBus.emit('dock', open);
}
export function setDockWidth(w: number) {
  aiState.dockWidth = Math.round(Math.min(720, Math.max(320, w)));
  try { localStorage.setItem(K_W, String(aiState.dockWidth)); } catch { /* */ }
}

/** Hand a task to AI: open the right panel, attach context, send. If AI (or the given feature) is off, show a hint and return false.
 *  context: elements to attach (added as context tags); omitted = attach the whole current context.
 *  opts.send=false only fills the input without sending; opts.action is the action name (explain/why/report/compare…, for the backend and demo recording). */
export function askAi(prompt: string, context?: AiItem | AiItem[] | null, opts: { action?: string; feature?: string; send?: boolean } = {}) {
  const f = opts.feature || 'ask';
  if (!aiOn(f)) { toast(aiOffText(f) || AI_OFF); return false; }
  if (context) aiContext.add(context);
  openAiDock(true);
  const payload = { prompt, action: opts.action || '', context: aiState.items.slice(), send: opts.send !== false };
  aiBus.emit('ask', payload);
  window.dispatchEvent(new CustomEvent('ask-assistant', { detail: payload }));
  return true;
}

// ---------------- Actions ----------------
const registry: Record<string, AiAction[]> = {};
/** Register context-menu / toolbar actions for an element kind ('*' = all). Returns an unregister fn (call it in the component's onUnmounted). */
export function registerAiActions(kind: AiKind | '*', actions: AiAction[]) {
  const list = (registry[kind] ||= []);
  for (const a of actions) { const i = list.findIndex((x) => x.id === a.id); if (i >= 0) list.splice(i, 1); list.push(a); }
  return () => { for (const a of actions) { const i = list.indexOf(a); if (i >= 0) list.splice(i, 1); } };
}
export function actionsFor(item: AiItem): AiAction[] {
  const all = [...BUILTIN, ...(registry['*'] || []), ...(registry[item.kind] || [])];
  const seen = new Map<string, AiAction>();
  for (const a of all) if (!a.when || a.when(item)) seen.set(a.id, a);       // Later-registered actions with the same name override built-in ones
  return [...seen.values()].sort((a, b) => (a.order ?? 100) - (b.order ?? 100));
}
export function runAction(a: AiAction, item: AiItem) {
  if (a.ai && !aiOn(a.feature || 'ask')) { toast(aiOffText(a.feature || 'ask') || AI_OFF); return; }
  const why = a.disabled?.(item);
  if (why) { toast(why); return; }
  const sel = aiState.items.some((x) => x.key === item.key) ? aiState.items.slice() : [item];
  aiBus.emit('action', { action: a.id, item, selected: sel });
  return a.run(item, sel);
}

// ---------------- Built-in actions ----------------
const NUMERIC = ['kpi', 'chart-point', 'field', 'row', 'device', 'alert'];
const names = (sel: AiItem[]) => sel.map((x) => x.label).join($t('common.k42'));
function textOf(it: AiItem) {
  const d = it.data;
  if (d === undefined || d === null || d === '') return it.label;
  if (typeof d !== 'object') return $t('common.n012', [it.label, d]);
  return `${it.label}\n${Object.entries(d).filter(([, v]) => v !== null && v !== undefined && typeof v !== 'object').map(([k, v]) => `${k}: ${v}`).join('\n')}`;
}
async function copyText(s: string) {
  try { await navigator.clipboard.writeText(s); } catch {
    const t = document.createElement('textarea'); t.value = s; t.style.position = 'fixed'; t.style.opacity = '0'; document.body.appendChild(t); t.select();
    try { document.execCommand('copy'); } finally { t.remove(); }
  }
  toast($t('common.copied'), 'success', 1200);
}
const traceTarget = (it: AiItem) => it.sn ? `/trace?sn=${encodeURIComponent(it.sn)}` : it.heat ? `/trace?heat=${encodeURIComponent(it.heat)}` :
  it.entity === 'part' && it.data?.sn ? `/trace?sn=${encodeURIComponent(it.data.sn)}` : it.data?.sn ? `/trace?sn=${encodeURIComponent(it.data.sn)}` : '';

export const BUILTIN: AiAction[] = [
  { id: 'ask', label: $t('aiIndex.askAi'), icon: 'ChatLineRound', ai: true, order: 10, toolbar: true,
    run: (it, sel) => { aiContext.add(sel); aiState.draft = ''; openAiDock(true); setTimeout(() => aiBus.emit('focus-input'), 60); return true; } },
  { id: 'explain', label: $t('aiIndex.explainThisNumber'), icon: 'DataAnalysis', ai: true, order: 20, toolbar: true, when: (it) => NUMERIC.includes(it.kind) || it.kind === 'text',
    run: (it, sel) => askAi(sel.length > 1 ? $t('aiIndex.explainTheseItems0How', [names(sel)])
      : $t('aiIndex.explainThisNumber0How', [it.label]), sel, { action: 'explain' }) },
  { id: 'why', label: $t('aiIndex.whyDidItChange'), icon: 'TrendCharts', ai: true, order: 30, when: (it) => NUMERIC.includes(it.kind),
    run: (it, sel) => askAi($t('aiIndex.whyDid0ChangeCompare', [names(sel)]), sel, { action: 'why' }) },
  { id: 'trace', label: $t('common.trace'), icon: 'Connection', order: 40, toolbar: true,
    disabled: (it) => (traceTarget(it) || aiOn('ask') ? false : $t('aiIndex.thisItemHasNoTraceable')),
    run: (it, sel) => {
      const t = traceTarget(it);
      if (t) { _router?.push(t); return; }
      return askAi($t('aiIndex.trace0WhereDoesIts', [it.label]), sel, { action: 'trace' });
    } },
  { id: 'report', label: $t('aiIndex.generateReport'), icon: 'Document', ai: true, order: 50,
    run: (it, sel) => askAi($t('aiIndex.writeAShortReportOn', [names(sel)]), sel, { action: 'report' }) },
  { id: 'compare', label: $t('aiIndex.addToComparison'), icon: 'Switch', ai: true, order: 60,
    run: (it) => {
      aiContext.add(it);
      const n = aiState.items.length;
      toast(n >= 2 ? $t('aiIndex.addedToComparison0Items', [n]) : $t('aiIndex.addedToComparisonSelectOne'), 'success');
      openAiDock(true);
    } },
  { id: 'open', label: $t('aiIndex.openRecord'), icon: 'Link', order: 70, when: (it) => !!(it.link || (it.entity && it.id)),
    run: (it) => _router?.push(it.link || `/e/${it.entity}?id=${it.id}`) },
  { id: 'copy', label: $t('common.copy'), icon: 'CopyDocument', order: 80, toolbar: true, run: (it, sel) => copyText(sel.map(textOf).join('\n\n')) },
];
