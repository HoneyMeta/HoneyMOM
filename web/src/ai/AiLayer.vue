<template>
  <Teleport to="body">
    <!-- Context menu -->
    <Transition name="hm-fade">
      <div v-if="menu.open" ref="menuEl" class="ai-menu hm-glass" role="menu" :aria-label="$t('aiLayer.aiActions') + menu.item?.label" data-test="ai-menu"
        :style="{ left: menu.x + 'px', top: menu.y + 'px' }" @keydown="menuKey" @contextmenu.prevent>
        <div class="mh">
          <span class="kind">{{ KIND[menu.item?.kind || ''] || $t('aiLayer.element') }}</span>
          <span class="lbl" :title="menu.item?.label">{{ menu.multi > 1 ? $t('aiLayer.n0Selected', [menu.multi]) : menu.item?.label }}</span>
        </div>
        <div v-if="!aiOn()" class="off" data-test="ai-off-note"><el-icon><Lock /></el-icon>{{ AI_OFF }}</div>
        <template v-for="(a, i) in menu.actions" :key="a.id">
          <div v-if="i > 0 && group(a) !== group(menu.actions[i - 1])" class="sep" role="separator" />
          <button class="mi" role="menuitem" :class="{ ai: a.ai, dis: isOff(a) || !!dis(a) }" :data-action="a.id" :aria-disabled="isOff(a) || !!dis(a)"
            :title="isOff(a) ? offText(a) : dis(a) || ''" @click="pick(a)" @mouseenter="focusIdx(i)">
            <el-icon class="ic"><component :is="a.icon || 'Pointer'" /></el-icon>
            <span class="tx">{{ a.label }}</span>
            <span v-if="a.ai" class="badge" :class="{ offb: isOff(a) }">{{ isOff(a) ? $t('common.off') : 'AI' }}</span>
          </button>
        </template>
        <div class="foot"><kbd>Ctrl</kbd>{{ $t('aiLayer.clickToAddToAi') }} <kbd>Shift</kbd>+<kbd>F10</kbd> {{ $t('aiLayer.openThisMenu') }}</div>
      </div>
    </Transition>

    <!-- Floating toolbar: selected text / table rows -->
    <Transition name="hm-fade">
      <div v-if="bar.open" ref="barEl" class="ai-bar hm-glass" role="toolbar" :aria-label="$t('aiLayer.quickActions') + bar.item?.label" data-test="ai-toolbar"
        :style="{ left: bar.x + 'px', top: bar.y + 'px' }" @mouseenter="barHover(true)" @mouseleave="barHover(false)" @mousedown.prevent>
        <span class="spark" aria-hidden="true" />
        <button v-for="a in bar.actions" :key="a.id" class="bi" :class="{ dis: isOff(a) || !!dis(a, bar.item) }" :data-action="a.id"
          :title="isOff(a) ? offText(a) : (dis(a, bar.item) || a.label)" @click="pickBar(a)">
          <el-icon><component :is="a.icon || 'Pointer'" /></el-icon><span>{{ a.id === 'ask' ? $t('common.askAi') : a.id === 'explain' ? $t('aiLayer.explain') : a.label }}</span>
        </button>
        <button class="bi more" :title="$t('aiLayer.moreActionsRightClickMenu')" @click="moreFromBar"><el-icon><MoreFilled /></el-icon></button>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { nextTick, onMounted, onUnmounted, reactive, ref } from 'vue';
import { useRoute } from 'vue-router';
import { actionsFor, AI_OFF, aiBus, aiContext, aiOffText, aiOn, aiState, normalizeItem, runAction, toast, type AiAction, type AiItem } from './index';
import { refreshMarks, resolveAiTarget } from './directive';
import { installAiExtras } from './actions';

const KIND: Record<string, string> = { kpi: $t('common.kpi'), 'chart-point': $t('aiLayer.chartDataPoint'), row: $t('aiLayer.tableRow'), device: $t('common.equipment'), record: $t('common.record'), field: $t('common.field'), alert: $t('aiLayer.alarmAlert'), text: $t('aiLayer.selectedText'), page: $t('aiLayer.page') };
const route = useRoute();
const menuEl = ref<HTMLElement>(), barEl = ref<HTMLElement>();
const menu = reactive({ open: false, x: 0, y: 0, item: null as AiItem | null, actions: [] as AiAction[], multi: 1, idx: 0, returnFocus: null as HTMLElement | null });
const bar = reactive({ open: false, x: 0, y: 0, item: null as AiItem | null, actions: [] as AiAction[], anchor: null as HTMLElement | null, hover: false, kind: '' as 'row' | 'text' | '' });

const isOff = (a: AiAction) => !!a.ai && !aiOn(a.feature || 'ask');
const offText = (a: AiAction) => aiOffText(a.feature || 'ask') || AI_OFF;
const dis = (a: AiAction, it: AiItem | null = menu.item) => (it && a.disabled ? a.disabled(it) || '' : '');
const group = (a: AiAction) => (a.ai ? 'ai' : (a.order ?? 100) < 100 ? 'base' : 'own');
const inShellChrome = (t: EventTarget | null) => t instanceof Element && !!t.closest('.ai-menu, .ai-bar, .dock, .hm-top, .hm-nav, .hm-status, .palette, input, textarea, [contenteditable=true], .el-select-dropdown, .el-picker-panel');

// ---------------- Menu ----------------
function openMenu(item: AiItem, x: number, y: number, from?: HTMLElement | null) {
  hideBar();
  menu.item = item;
  menu.actions = actionsFor(item);
  menu.multi = aiState.items.some((i) => i.key === item.key) ? aiState.items.length : 1;
  menu.returnFocus = from || (document.activeElement as HTMLElement);
  menu.x = x; menu.y = y; menu.idx = 0; menu.open = true;
  nextTick(() => {
    const el = menuEl.value; if (!el) return;
    const r = el.getBoundingClientRect();
    menu.x = Math.max(6, Math.min(x, innerWidth - r.width - 6));
    menu.y = Math.max(6, y + r.height > innerHeight - 6 ? y - r.height : y);
    focusIdx(0);
  });
}
function closeMenu(restore = true) {
  if (!menu.open) return;
  menu.open = false;
  if (restore) menu.returnFocus?.focus?.({ preventScroll: true });
}
function buttons() { return Array.from(menuEl.value?.querySelectorAll<HTMLButtonElement>('.mi') || []); }
function focusIdx(i: number) { menu.idx = i; buttons()[i]?.focus({ preventScroll: true }); }
function menuKey(e: KeyboardEvent) {
  const n = buttons().length;
  if (e.key === 'ArrowDown') { e.preventDefault(); focusIdx((menu.idx + 1) % n); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); focusIdx((menu.idx - 1 + n) % n); }
  else if (e.key === 'Home') { e.preventDefault(); focusIdx(0); }
  else if (e.key === 'End') { e.preventDefault(); focusIdx(n - 1); }
  else if (e.key === 'Escape' || e.key === 'Tab') { e.preventDefault(); closeMenu(); }
}
function pick(a: AiAction) {
  const it = menu.item!;
  closeMenu(false);
  runAction(a, it);
}

// ---------------- Toolbar ----------------
let barTimer: any, hideTimer: any;
function showBar(item: AiItem, anchor: HTMLElement | null, rect: DOMRect, kind: 'row' | 'text') {
  bar.item = item; bar.anchor = anchor; bar.kind = kind;
  bar.actions = actionsFor(item).filter((a) => a.toolbar).slice(0, 4);
  bar.open = true;
  nextTick(() => {
    const w = barEl.value?.offsetWidth || 260, h = barEl.value?.offsetHeight || 32;
    if (kind === 'text') { bar.x = Math.max(6, Math.min(rect.left + rect.width / 2 - w / 2, innerWidth - w - 6)); bar.y = rect.top - h - 8 < 56 ? rect.bottom + 8 : rect.top - h - 8; }
    else {
      const box = (anchor?.closest('.el-table__body-wrapper, .el-scrollbar__wrap, .hm-dt') as HTMLElement | null)?.getBoundingClientRect() || rect;
      bar.x = Math.max(box.left + 6, Math.min(rect.right, box.right) - w - 10); bar.y = rect.top + (rect.height - h) / 2;
    }
  });
}
function hideBar() { clearTimeout(barTimer); bar.open = false; bar.anchor = null; }
function barHover(v: boolean) { bar.hover = v; if (!v) scheduleHide(); else clearTimeout(hideTimer); }
function scheduleHide() { clearTimeout(hideTimer); hideTimer = setTimeout(() => { if (!bar.hover && bar.kind === 'row') hideBar(); }, 280); }
function pickBar(a: AiAction) { const it = bar.item!; hideBar(); window.getSelection()?.removeAllRanges(); runAction(a, it); }
function moreFromBar() { const it = bar.item!, r = barEl.value!.getBoundingClientRect(); openMenu(it, r.right - 220, r.bottom + 4, bar.anchor); }

// ---------------- Global events ----------------
function flash(el: HTMLElement) { el.classList.remove('ai-flash'); void el.offsetWidth; el.classList.add('ai-flash'); setTimeout(() => el.classList.remove('ai-flash'), 600); }

function toggleCtx(el: HTMLElement, item: AiItem) {
  if (!aiOn('ask')) { toast(aiOffText('ask') || AI_OFF); return; }
  const on = aiContext.toggle(item);
  flash(el);
  toast(on ? $t('aiLayer.addedToAiContext0', [item.label]) : $t('aiLayer.removedFromAiContext0', [item.label]), on ? 'success' : 'info', 1300);
}

function onClickCapture(e: MouseEvent) {
  if (!(e.ctrlKey || e.metaKey) || e.button !== 0 || inShellChrome(e.target)) return;
  const hit = resolveAiTarget(e.target);
  if (!hit) return;
  e.preventDefault(); e.stopPropagation();
  toggleCtx(hit.el, hit.item);
}
function onContextMenu(e: MouseEvent) {
  if (e.shiftKey || inShellChrome(e.target)) return;          // Shift+right-click = native browser menu
  const sel = selectionItem();
  const hit = resolveAiTarget(e.target);
  if (!hit && !sel) return;
  e.preventDefault();
  openMenu(sel && (!hit || hit.el.contains(window.getSelection()?.anchorNode || null)) ? sel.item : hit!.item, e.clientX, e.clientY, hit?.el);
}
function onKeyDown(e: KeyboardEvent) {
  document.body.classList.toggle('ai-ctrl', e.ctrlKey || e.metaKey);
  if (menu.open) {
    // When focus is outside the menu (e.g. stolen by something on the page), the menu's own keydown never sees Esc: close it here as a fallback
    if (e.key === 'Escape' && !menuEl.value?.contains(document.activeElement)) { e.preventDefault(); closeMenu(); }
    return;
  }
  if (e.key === 'Escape') { hideBar(); return; }
  const kb = (e.key === 'F10' && e.shiftKey) || e.key === 'ContextMenu';
  const add = e.key === 'Enter' && (e.ctrlKey || e.metaKey);
  if (!kb && !add) return;
  const act = document.activeElement as HTMLElement | null;
  if (!act || inShellChrome(act)) return;
  const hit = resolveAiTarget(act);
  if (!hit) return;
  e.preventDefault();
  if (add) { toggleCtx(hit.el, hit.item); return; }
  const r = hit.el.getBoundingClientRect();
  openMenu(hit.item, r.left + Math.min(24, r.width / 2), r.top + Math.min(r.height, 40), hit.el);
}
function onKeyUp(e: KeyboardEvent) { if (!e.ctrlKey && !e.metaKey) document.body.classList.remove('ai-ctrl'); }
function onBlur() { document.body.classList.remove('ai-ctrl'); }

function selectionItem(): { item: AiItem; rect: DOMRect } | null {
  const s = window.getSelection();
  if (!s || s.isCollapsed || !s.rangeCount) return null;
  const text = s.toString().replace(/\s+/g, ' ').trim();
  if (text.length < 2 || text.length > 1200) return null;
  const node = s.anchorNode instanceof Element ? s.anchorNode : s.anchorNode?.parentElement;
  if (!node || !node.closest('.hm-main') || inShellChrome(node)) return null;
  const owner = resolveAiTarget(node);
  const item = normalizeItem({ kind: 'text', label: text.length > 36 ? text.slice(0, 36) + '…' : text,
    data: { text, page: route.fullPath, in: owner?.item.label }, entity: owner?.item.entity, id: owner?.item.entity ? owner?.item.id : undefined });
  return item ? { item, rect: s.getRangeAt(0).getBoundingClientRect() } : null;
}
function onMouseUp(e: MouseEvent) {
  if (e.button !== 0 || inShellChrome(e.target)) return;
  setTimeout(() => {
    const sel = selectionItem();
    if (sel) showBar(sel.item, null, sel.rect, 'text');
    else if (bar.kind === 'text') hideBar();
  }, 10);
}
function onMouseOver(e: MouseEvent) {
  const t = e.target as Element;
  if (!(t instanceof Element) || bar.kind === 'text' && bar.open) return;
  const tr = t.closest('tr') as HTMLElement | null;
  if (!tr || tr === bar.anchor) { if (!tr) scheduleHide(); else clearTimeout(hideTimer); return; }
  if (!(tr.closest('[data-ai-rows]') || tr.hasAttribute('data-ai-kind') || (tr as any).__ai)) { scheduleHide(); return; }
  clearTimeout(barTimer); clearTimeout(hideTimer);
  barTimer = setTimeout(() => {
    const hit = resolveAiTarget(tr);
    if (hit && tr.isConnected) showBar(hit.item, tr, tr.getBoundingClientRect(), 'row');
  }, 260);
}
function onFocusIn(e: FocusEvent) {
  const t = e.target as HTMLElement;
  if (!(t instanceof HTMLElement) || t.tagName !== 'TR' || inShellChrome(t)) return;
  const hit = resolveAiTarget(t);
  if (hit) showBar(hit.item, t, t.getBoundingClientRect(), 'row');
}
function onPointerDown(e: MouseEvent) {
  if (menu.open && !menuEl.value?.contains(e.target as Node)) closeMenu(false);
}
function onScroll() { if (menu.open) closeMenu(false); if (bar.open) hideBar(); }

let mo: MutationObserver | null = null, moPending = false;
onMounted(() => {
  installAiExtras();          // Global AI context actions: dispatch repair / raise Andon / adjust schedule / set as automation alert (actions.ts)
  document.addEventListener('click', onClickCapture, true);
  document.addEventListener('contextmenu', onContextMenu);
  document.addEventListener('keydown', onKeyDown, true);
  document.addEventListener('keyup', onKeyUp, true);
  document.addEventListener('mouseup', onMouseUp);
  document.addEventListener('mouseover', onMouseOver);
  document.addEventListener('focusin', onFocusIn);
  document.addEventListener('pointerdown', onPointerDown, true);
  document.addEventListener('scroll', onScroll, true);
  window.addEventListener('blur', onBlur);
  window.addEventListener('resize', onScroll);
  aiBus.on('context-change', () => refreshMarks());
  // Rows are rebuilt after table paging/refresh: re-mark the selected rows
  mo = new MutationObserver(() => { if (moPending || !aiState.items.length) return; moPending = true; requestAnimationFrame(() => { moPending = false; refreshMarks(); }); });
  const main = document.querySelector('.hm-main');
  if (main) mo.observe(main, { childList: true, subtree: true });
});
onUnmounted(() => {
  document.removeEventListener('click', onClickCapture, true);
  document.removeEventListener('contextmenu', onContextMenu);
  document.removeEventListener('keydown', onKeyDown, true);
  document.removeEventListener('keyup', onKeyUp, true);
  document.removeEventListener('mouseup', onMouseUp);
  document.removeEventListener('mouseover', onMouseOver);
  document.removeEventListener('focusin', onFocusIn);
  document.removeEventListener('pointerdown', onPointerDown, true);
  document.removeEventListener('scroll', onScroll, true);
  window.removeEventListener('blur', onBlur);
  window.removeEventListener('resize', onScroll);
  mo?.disconnect();
});
defineExpose({ openMenu, closeMenu });
</script>

<style scoped>
.ai-menu { position: fixed; z-index: var(--hm-z-pop); min-width: 220px; max-width: 300px; padding: 6px; border-radius: var(--hm-r-lg); box-shadow: var(--hm-e3); background: var(--hm-glass-strong); }
.mh { display: flex; flex-direction: column; padding: 6px 8px 8px; border-bottom: 1px solid var(--hm-hairline); margin-bottom: 4px; }
.mh .kind { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); letter-spacing: .06em; }
.mh .lbl { font-weight: 600; font-size: var(--hm-fs-sm); color: var(--hm-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.off { display: flex; align-items: center; gap: 6px; font-size: var(--hm-fs-xs); color: var(--hm-idle-ink); background: var(--hm-idle-soft); border-radius: var(--hm-r-sm); padding: 5px 8px; margin: 2px 2px 4px; }
.mi { width: 100%; display: flex; align-items: center; gap: 9px; border: 0; background: transparent; padding: 6px 8px; border-radius: var(--hm-r-sm); font: inherit; font-size: var(--hm-fs-sm);
  color: var(--hm-text); cursor: pointer; text-align: left; }
.mi:hover, .mi:focus-visible { background: var(--hm-primary-soft); outline: none; box-shadow: none; }
.mi .ic { color: var(--hm-text-2); font-size: 15px; } .mi.ai .ic { color: var(--hm-primary); }
.mi .tx { flex: 1; }
.mi.dis { color: var(--hm-text-3); } .mi.dis .ic { color: var(--hm-text-3); }
.badge { font-size: 10px; font-weight: 700; letter-spacing: .06em; padding: 0 6px; line-height: 16px; border-radius: var(--hm-r-pill); color: #fff; background: var(--hm-ai-grad); }
.badge.offb { background: var(--hm-surface-3); color: var(--hm-text-3); font-weight: 500; letter-spacing: 0; }
.sep { height: 1px; background: var(--hm-hairline); margin: 4px 6px; }
.foot { font-size: 10.5px; color: var(--hm-text-3); padding: 6px 8px 2px; border-top: 1px solid var(--hm-hairline); margin-top: 4px; }
kbd { font-family: var(--hm-font-mono); font-size: 10px; border: 1px solid var(--hm-border-strong); border-bottom-width: 2px; border-radius: 3px; padding: 0 3px; background: #fff; color: var(--hm-text-2); }

.ai-bar { position: fixed; z-index: var(--hm-z-pop); display: flex; align-items: center; gap: 2px; padding: 3px; border-radius: var(--hm-r-pill); box-shadow: var(--hm-e3); background: var(--hm-glass-strong); }
.spark { width: 6px; height: 18px; border-radius: 3px; background: var(--hm-ai-grad); margin: 0 3px 0 4px; }
.bi { display: inline-flex; align-items: center; gap: 4px; border: 0; background: transparent; font: inherit; font-size: var(--hm-fs-xs); color: var(--hm-text); padding: 4px 9px; border-radius: var(--hm-r-pill); cursor: pointer; white-space: nowrap; }
.bi:hover { background: var(--hm-primary-soft); color: var(--hm-primary); }
.bi.dis { color: var(--hm-text-3); } .bi.more { padding: 4px 7px; }
</style>
