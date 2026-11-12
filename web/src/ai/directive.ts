// v-ai / v-ai-rows directives, plus finding "this element's AI context" from the DOM.
//
//   <div v-ai="{ kind: 'kpi', label: 'Avg OEE 72.4%', data: { value: 0.724, unit: '%', range: 'Today' } }">…</div>
//   <div data-ai-kind="device" data-ai-id="12" data-ai-label="CNC3 Machining Center" data-ai-entity="equipment">…</div>   (without the directive)
//   <div data-ai='{"kind":"alert","label":"CNC2 way lube alarm"}'>…</div>
//   <el-table v-ai-rows="{ entity: 'work_order', rows: () => rows, label: (r) => r.code }">…</el-table>
//
// v-ai-rows config: { kind?='row', entity?, rows: () => any[], label?: (row) => string, data?: (row) => any, item?: (row, index) => AiItem }
import type { Directive } from 'vue';
import { aiState, itemKey, normalizeItem, type AiItem } from './index';

const FOCUSABLE = /^(A|BUTTON|INPUT|SELECT|TEXTAREA)$/;

// "Selected for AI" is marked with the data-ai-selected attribute (Vue re-renders overwrite class but leave this attribute alone)
function setSel(el: HTMLElement, on: boolean) { if (on) el.setAttribute('data-ai-selected', ''); else el.removeAttribute('data-ai-selected'); }

function mark(el: HTMLElement, item: AiItem | null) {
  if (!item) {
    el.removeAttribute('data-ai-key'); el.removeAttribute('data-ai-selected');
    return;
  }
  el.setAttribute('data-ai-key', item.key!);
  el.setAttribute('data-ai-kind', String(item.kind));
  if (!el.hasAttribute('tabindex') && !FOCUSABLE.test(el.tagName)) el.tabIndex = 0;
  setSel(el, aiState.items.some((x) => x.key === item.key));
}

export const vAi: Directive<HTMLElement, any> = {
  mounted(el, b) { (el as any).__ai = normalizeItem(b.value); mark(el, (el as any).__ai); },
  updated(el, b) { (el as any).__ai = normalizeItem(b.value); mark(el, (el as any).__ai); },
  unmounted(el) { (el as any).__ai = null; },
};

export const vAiRows: Directive<HTMLElement, any> = {
  mounted(el, b) { (el as any).__aiRows = b.value; el.setAttribute('data-ai-rows', ''); },
  updated(el, b) { (el as any).__aiRows = b.value; queueMicrotask(() => refreshRowMarks(el)); },
  unmounted(el) { (el as any).__aiRows = null; },
};

function fromAttrs(el: HTMLElement): AiItem | null {
  const raw = el.getAttribute('data-ai');
  if (raw) { try { return normalizeItem(JSON.parse(raw)); } catch { return null; } }
  const kind = el.getAttribute('data-ai-kind');
  if (!kind) return null;
  return normalizeItem({ kind, id: el.getAttribute('data-ai-id') ?? undefined, label: el.getAttribute('data-ai-label') || el.textContent?.trim().slice(0, 40) || kind,
    entity: el.getAttribute('data-ai-entity') || undefined, sn: el.getAttribute('data-ai-sn') || undefined });
}

/** AI item for an el-table row (tr) */
export function rowItem(tr: HTMLElement): AiItem | null {
  const host = tr.closest('[data-ai-rows]') as any;
  const cfg = host?.__aiRows;
  if (!cfg) return null;
  const body = tr.parentElement;
  if (!body) return null;
  const idx = Array.from(body.children).filter((x) => x.classList.contains('el-table__row')).indexOf(tr);
  const rows = (typeof cfg.rows === 'function' ? cfg.rows() : cfg.rows) || [];
  const row = rows[idx];
  if (!row) return null;
  if (cfg.item) return normalizeItem(cfg.item(row, idx));
  const label = cfg.label ? cfg.label(row) : row.code || row.sn || row.name || row.title || `#${row.id ?? idx + 1}`;
  return normalizeItem({ kind: cfg.kind || 'row', entity: cfg.entity, id: row.id, label: (cfg.prefix ? cfg.prefix + ' ' : '') + label,
    data: cfg.data ? cfg.data(row) : row, sn: row.sn || undefined, heat: cfg.entity === 'heat' ? row.code : undefined });
}

/** Walk up from the event target to the first element with AI context */
export function resolveAiTarget(start: EventTarget | null): { el: HTMLElement; item: AiItem } | null {
  let el = start instanceof Element ? (start as HTMLElement) : (start as any)?.parentElement || null;
  while (el && el !== document.body) {
    if ((el as any).__ai) return { el, item: (el as any).__ai };
    if (el.hasAttribute?.('data-ai') || el.hasAttribute?.('data-ai-kind')) {
      const it = fromAttrs(el);
      if (it) return { el, item: it };
    }
    if (el.tagName === 'TR' && el.classList.contains('el-table__row') && el.closest('[data-ai-rows]')) {
      const it = rowItem(el);
      if (it) { el.setAttribute('data-ai-key', it.key!); return { el, item: it }; }
    }
    el = el.parentElement;
  }
  return null;
}

/** Refresh all "selected for AI" rings after the context changes */
export function refreshMarks(root: ParentNode = document) {
  const keys = new Set(aiState.items.map((x) => x.key));
  root.querySelectorAll<HTMLElement>('[data-ai-key]').forEach((el) => {
    if (el.tagName === 'TR') return;
    setSel(el, keys.has(el.getAttribute('data-ai-key')!));
  });
  root.querySelectorAll<HTMLElement>('[data-ai-rows]').forEach((t) => refreshRowMarks(t, keys));
}

export function refreshRowMarks(table: HTMLElement, keys = new Set(aiState.items.map((x) => x.key))) {
  table.querySelectorAll<HTMLElement>('tr.el-table__row').forEach((tr) => {
    const it = keys.size ? rowItem(tr) : null;
    const on = !!it && keys.has(it.key);
    if (it) tr.setAttribute('data-ai-key', it.key!);
    setSel(tr, on);
  });
}

export { itemKey };
