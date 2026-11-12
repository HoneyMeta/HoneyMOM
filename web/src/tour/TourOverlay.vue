<template>
  <div v-if="tour.on && tour.def" class="vt-root" data-test="tour-overlay" :data-guide="tour.key" :data-step="tour.i" :data-step-id="cur?.id || 'end'" :data-found="rect ? 1 : 0" :data-busy="tour.busy ? 1 : 0">
    <!-- Spotlight: target lit, the rest slightly dimmed; the layer ignores the mouse so users can still click -->
    <div v-if="rect && showSpot" class="vt-spot" :class="{ ok: tour.ok }" :style="spotStyle" data-test="tour-spot" />
    <button v-if="tour.min" class="vt-pill" data-test="tour-pill" @click="tour.min = false">
      <span class="sp" aria-hidden="true" />{{ $t('tourOverlay.tour012', [Math.min(tour.i + 1, n), n, cur?.title || $t('common.done')]) }}
    </button>
    <div v-else ref="card" class="vt-card" :style="cardStyle" role="dialog" aria-live="polite" :aria-label="tour.def.title" data-test="tour-card" @keydown="onKey">
      <div class="vt-prog"><i :style="{ width: Math.round((Math.min(tour.i + 1, n) / n) * 100) + '%' }" /></div>
      <div class="vt-top" :title="$t('tourOverlay.holdHereToDragThe')" @pointerdown="startDrag">
        <span class="vt-k">{{ KIND[tour.def.kind] || $t('commandPalette.tour') }}</span>
        <span class="vt-t">{{ tour.def.title }}</span>
        <span class="vt-n hm-num">{{ tour.finished ? $t('common.done') : `${tour.i + 1} / ${n}` }}</span>
        <button class="vt-x" :title="$t('tourOverlay.collapseTheTourStaysExpand')" :aria-label="$t('tourOverlay.collapseTour')" data-test="tour-min" @click="tour.min = true">—</button>
      </div>
      <template v-if="tour.finished">
        <div class="vt-body">
          <h4>{{ $t('tourOverlay.tourComplete') }}</h4>
          <p class="vt-do">{{ tour.def.outro || $t('tourOverlay.toSeeItAgainAnytime') }}</p>
        </div>
        <div class="vt-btns"><span class="grow" /><button class="vt-b pri" data-test="tour-close" @click="stopTour()">{{ $t('common.close') }}</button></div>
      </template>
      <template v-else-if="cur">
        <div class="vt-body" data-test="tour-body">
          <h4 data-test="tour-title">{{ cur.title }}</h4>
          <div class="vt-do" v-html="rich(cur.do)" />
          <details v-if="cur.why" class="vt-why"><summary>{{ $t('tourOverlay.learnMore') }}</summary><div v-html="rich(cur.why)" /></details>
          <div v-if="tour.ok" class="vt-ok" data-test="tour-ok">{{ $t('tourOverlay.done') }}</div>
          <div v-if="tour.busy" class="vt-miss">{{ $t('tourOverlay.opening') }}</div>
          <div v-else-if="!rect && cur.target" class="vt-miss" data-test="tour-miss">{{ $t('tourOverlay.theSpotThisStepPoints') }}</div>
        </div>
        <div class="vt-btns">
          <button class="vt-b q" :title="$t('tourOverlay.exitTourReopenLaterVia')" data-test="tour-exit" @click="exit">{{ $t('screen.exit') }}</button>
          <span class="grow" />
          <button class="vt-b" :disabled="!tour.i" data-test="tour-prev" @click="prev()">{{ $t('common.back') }}</button>
          <button v-if="tour.i < n - 1" class="vt-b" :title="$t('tourOverlay.skipThisStep')" data-test="tour-skip" @click="next()">{{ $t('common.skip') }}</button>
          <button ref="nextBtn" class="vt-b pri" data-test="tour-next" @click="next()">{{ tour.i < n - 1 ? $t('common.next') : $t('common.done') }}</button>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// Tour UI: spotlighted target + step card (progress, title, description, "Learn more", Back / Skip / Next / Exit).
// Target position is re-found every frame; the card sits next to the target (right → left → below → above → corner) and can be dragged away; the layer is pointer-events: none, only the card is clickable.
// Keyboard: ← / → in the card for Back / Next, Esc to collapse.
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { exitTour, next, prev, resolveTarget, stopTour, tour } from './engine';

const KIND: Record<string, string> = { welcome: $t('common.welcome'), module: $t('common.guidedTour'), task: $t('tourOverlay.taskWalkthroughs') };
const card = ref<HTMLElement | null>(null), nextBtn = ref<HTMLButtonElement | null>(null);
const rect = ref<DOMRect | null>(null);
const n = computed(() => tour.def?.steps?.length || 1);
const cur = computed<any>(() => tour.def?.steps?.[tour.i] || null);
const showSpot = computed(() => !tour.min && !tour.finished && !tour.busy);
const scale = () => { try { return parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--hm-scale')) || 1; } catch { return 1; } };
let raf = 0;
const ch = ref(220), vw = ref(innerWidth), vh = ref(innerHeight), k = ref(scale());
function frame() {
  const t = cur.value && !tour.finished ? cur.value.target : null;
  const hit = t ? resolveTarget(t) : null;
  const r = hit ? hit.rect : null, o = rect.value;
  if (!r) { if (o) rect.value = null; }
  else if (!o || Math.abs(o.x - r.x) > 0.5 || Math.abs(o.y - r.y) > 0.5 || Math.abs(o.width - r.width) > 0.5 || Math.abs(o.height - r.height) > 0.5) rect.value = r;
  const h = card.value?.offsetHeight || 0;
  if (h && Math.abs(h - ch.value) > 1) ch.value = h;
  if (vw.value !== innerWidth || vh.value !== innerHeight) { vw.value = innerWidth; vh.value = innerHeight; k.value = scale(); }
  raf = requestAnimationFrame(frame);
}
raf = requestAnimationFrame(frame);
onBeforeUnmount(() => cancelAnimationFrame(raf));
watch(() => [tour.i, tour.on, tour.min], () => nextTick(() => nextBtn.value?.focus({ preventScroll: true })));

const PAD = 6, M = 12;
const CW = computed(() => Math.round(360 * k.value));
const spotStyle = computed(() => {
  const r = rect.value!;
  return { left: r.x - PAD + 'px', top: r.y - PAD + 'px', width: r.width + PAD * 2 + 'px', height: r.height + PAD * 2 + 'px' };
});
/** Place the card next to the target (right → left → below → above); if none fit, pick a corner that doesn't cover the target */
const place = computed(() => {
  const t = rect.value, W = vw.value, H = vh.value, hh = Math.min(ch.value, H - 2 * M), cw = CW.value;
  if (!t) return { x: W - cw - 24, y: H - hh - 48 };
  const cy = Math.min(Math.max(70, t.y + t.height / 2 - hh / 2), H - hh - M);
  if (t.right + 20 + cw < W - M) return { x: t.right + 20, y: cy };
  if (t.left - 20 - cw > M) return { x: t.left - 20 - cw, y: cy };
  const cx = Math.min(Math.max(M, t.x + t.width / 2 - cw / 2), W - cw - M);
  if (t.bottom + 20 + hh < H - M) return { x: cx, y: t.bottom + 20 };
  if (t.top - 20 - hh > M) return { x: cx, y: t.top - 20 - hh };
  const hit = (x: number, y: number) => Math.max(0, Math.min(x + cw, t.right + 8) - Math.max(x, t.left - 8)) * Math.max(0, Math.min(y + hh, t.bottom + 8) - Math.max(y, t.top - 8));
  const corners = [[W - cw - 24, H - hh - 48], [24, H - hh - 48], [W - cw - 24, 70], [24, 70]];
  return corners.map(([x, y]) => ({ x, y, c: hit(x, y) })).sort((a, b) => a.c - b.c)[0];
});
const moved = ref<{ x: number; y: number } | null>(null);
watch(() => tour.i, () => { moved.value = null; });
function startDrag(e: PointerEvent) {
  if ((e.target as HTMLElement).closest('button')) return;
  const sx = e.clientX, sy = e.clientY, ox = moved.value?.x ?? place.value.x, oy = moved.value?.y ?? place.value.y;
  const mv = (ev: PointerEvent) => { moved.value = { x: Math.max(0, Math.min(innerWidth - 80, ox + ev.clientX - sx)), y: Math.max(0, Math.min(innerHeight - 40, oy + ev.clientY - sy)) }; };
  const upf = () => { removeEventListener('pointermove', mv); removeEventListener('pointerup', upf); };
  addEventListener('pointermove', mv); addEventListener('pointerup', upf);
}
const cardStyle = computed(() => {
  const H = vh.value, W = vw.value, hh = Math.min(ch.value, H - 2 * M), cw = CW.value;
  const x = Math.max(M, Math.min(W - cw - M, moved.value?.x ?? place.value.x));
  const y = Math.max(M, Math.min(H - hh - M, moved.value?.y ?? place.value.y));
  return { left: x + 'px', top: y + 'px', width: cw + 'px', maxHeight: H - 2 * M + 'px' };
});
function rich(t: string) {
  const esc = String(t || '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));
  return esc.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`(.+?)`/g, '<kbd>$1</kbd>').replace(/\n/g, '<br>');
}
function onKey(e: KeyboardEvent) {
  if ((e.target as HTMLElement).closest('details')) return;
  if (e.key === 'ArrowRight') { e.preventDefault(); next(); }
  else if (e.key === 'ArrowLeft' && tour.i > 0) { e.preventDefault(); prev(); }
  else if (e.key === 'Escape') { e.preventDefault(); tour.min = true; }
}
function exit() {
  exitTour();
  import('../ai').then((m) => m.toast($t('tourOverlay.tourExitedClickTheNext'), 'info', 2600)).catch(() => {});
}
</script>

<style scoped>
.vt-root { position: fixed; inset: 0; z-index: 3150; pointer-events: none; font-family: var(--hm-font); font-size: var(--hm-fs-sm); color: var(--hm-text); }
.vt-spot { position: fixed; border-radius: 10px; border: 2px solid var(--hm-primary); box-shadow: 0 0 0 9999px rgba(15, 27, 45, .22), 0 0 0 5px rgba(20, 99, 214, .18);
  transition: left .18s var(--hm-ease), top .18s var(--hm-ease), width .18s var(--hm-ease), height .18s var(--hm-ease); }
.vt-spot.ok { border-color: var(--hm-run); box-shadow: 0 0 0 9999px rgba(15, 27, 45, .16), 0 0 0 5px rgba(21, 163, 74, .18); }
.vt-card { position: fixed; pointer-events: auto; background: var(--hm-glass-strong); backdrop-filter: var(--hm-glass-blur); -webkit-backdrop-filter: var(--hm-glass-blur);
  border: 1px solid var(--hm-hairline); border-radius: var(--hm-r-lg); box-shadow: var(--hm-e3); display: flex; flex-direction: column; overflow: hidden;
  padding: 0 var(--hm-sp-4) var(--hm-sp-3); transition: left .2s var(--hm-ease), top .2s var(--hm-ease); }
.vt-prog { height: 3px; margin: 0 calc(-1 * var(--hm-sp-4)) var(--hm-sp-2); background: var(--hm-surface-3); flex: none; }
.vt-prog i { display: block; height: 100%; background: var(--hm-ai-grad); transition: width .3s var(--hm-ease); }
.vt-top { display: flex; align-items: center; gap: 8px; font-size: var(--hm-fs-xs); color: var(--hm-text-3); cursor: move; user-select: none; flex: none; }
.vt-k { font-weight: 700; letter-spacing: .06em; color: var(--hm-primary); }
.vt-t { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.vt-n { color: var(--hm-text-2); }
.vt-x { border: 0; background: none; color: var(--hm-text-3); cursor: pointer; font-size: 14px; padding: 0 4px; border-radius: 4px; }
.vt-x:hover { color: var(--hm-text); background: var(--hm-surface-3); }
.vt-body { flex: 1 1 auto; min-height: 0; overflow-y: auto; overscroll-behavior: contain; }
h4 { margin: var(--hm-sp-2) 0 6px; font-size: var(--hm-fs-lg); font-weight: 600; color: var(--hm-text); }
.vt-do { line-height: 1.75; color: var(--hm-text-2); margin: 0; }
.vt-do :deep(b) { color: var(--hm-text); }
.vt-do :deep(kbd), .vt-why :deep(kbd) { font-family: var(--hm-font-mono); font-size: .86em; border: 1px solid var(--hm-border-strong); border-bottom-width: 2px; border-radius: 4px; padding: 0 4px; background: #fff; color: var(--hm-text-2); }
.vt-why { margin-top: 8px; background: var(--hm-surface-2); border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); padding: 5px 9px; line-height: 1.7; color: var(--hm-text-2); font-size: var(--hm-fs-xs); }
.vt-why summary { cursor: pointer; color: var(--hm-primary); }
.vt-ok { margin-top: 8px; color: var(--hm-run-ink); font-weight: 600; }
.vt-miss { margin-top: 8px; font-size: var(--hm-fs-xs); color: var(--hm-idle-ink); background: var(--hm-idle-soft); border-radius: var(--hm-r-sm); padding: 4px 8px; }
.vt-btns { display: flex; align-items: center; gap: 6px; margin-top: var(--hm-sp-3); flex: none; }
.vt-b { border: 1px solid var(--hm-border-strong); background: var(--hm-surface); color: var(--hm-text); border-radius: var(--hm-r-sm); height: calc(28px * var(--hm-scale)); padding: 0 12px;
  cursor: pointer; font: inherit; font-size: var(--hm-fs-xs); font-weight: 500; }
.vt-b:hover:not(:disabled) { border-color: var(--hm-primary); color: var(--hm-primary); background: var(--hm-primary-soft); }
.vt-b:disabled { opacity: .45; cursor: default; }
.vt-b.pri, .vt-b.pri:hover:not(:disabled) { background: var(--hm-primary); border-color: var(--hm-primary); color: #fff; }
.vt-b.pri:hover:not(:disabled) { background: var(--hm-primary-hover); }
.vt-b.q { border-color: transparent; color: var(--hm-text-3); padding: 0 6px; }
.vt-pill { position: fixed; left: 16px; bottom: calc(var(--hm-status-h) + 12px); pointer-events: auto; display: inline-flex; align-items: center; gap: 8px; border: 1px solid transparent;
  background: linear-gradient(#fff, #fff) padding-box, var(--hm-ai-grad) border-box; color: var(--hm-primary-press); border-radius: var(--hm-r-pill); padding: 6px 14px; cursor: pointer;
  box-shadow: var(--hm-e2); font: inherit; font-size: var(--hm-fs-xs); max-width: 60vw; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.vt-pill .sp { width: 8px; height: 8px; border-radius: 2px; transform: rotate(45deg); background: var(--hm-ai-grad); flex: none; }
.grow { flex: 1; }
@media (prefers-reduced-motion: reduce) { .vt-spot, .vt-card { transition: none; } }
</style>
