<template>
  <div class="fm" :class="{ big, fill }" data-test="factory-map">
    <!-- With layout: floor plan -->
    <template v-if="hasLayout">
      <div class="bar">
        <div class="legend" :aria-label="$t('factoryMap.legend')">
          <span v-for="s in LEGEND" :key="s[0]" class="lg" :class="'t-' + s[0]"><i />{{ s[1] }}</span>
          <span class="lg buf"><i />{{ $t('factoryMap.buffer') }}</span>
          <span v-if="showFlow" class="lg flow"><i />{{ $t('factoryMap.logisticsRoute') }}</span>
        </div>
        <span class="grow" />
        <span v-if="layout?.source === 'cache'" class="warn" :title="layout?.warning">{{ $t('factoryMap.offlineLayout') }}</span>
        <div v-if="!big" class="zoom" role="group" :aria-label="$t('factoryMap.zoom')">
          <button :title="$t('common.zoomOut')" :aria-label="$t('common.zoomOut')" @click="zoomBy(1 / 1.4)"><el-icon><Minus /></el-icon></button>
          <span class="zl hm-num">{{ Math.round(zoom * 100) }}%</span>
          <button :title="$t('common.zoomIn')" :aria-label="$t('common.zoomIn')" @click="zoomBy(1.4)"><el-icon><Plus /></el-icon></button>
          <button :title="$t('drawingViewer.fitToWindow')" :aria-label="$t('drawingViewer.fitToWindow')" data-test="map-fit" @click="fit"><el-icon><FullScreen /></el-icon></button>
        </div>
      </div>
      <div ref="wrap" class="stage" :style="stageStyle" @wheel="onWheel" @pointerdown="onDown" @pointermove="onMove" @pointerup="onUp" @pointercancel="onUp"
        @dblclick="onDbl" @mouseleave="hover = null">
        <svg ref="svg" class="svg" :viewBox="`${view.x} ${view.y} ${view.w} ${view.h}`" :preserveAspectRatio="fill ? 'xMidYMin meet' : 'xMidYMid meet'" role="img"
          :aria-label="$t('factoryMap.n0FloorPlan1Machines', [layout.plant || $t('common.plant'), stations.length])">
          <defs>
            <pattern id="fm-grid" :width="5" :height="5" patternUnits="userSpaceOnUse"><path d="M5 0H0V5" fill="none" class="gl" /></pattern>
            <pattern id="fm-conv" :width="1.2" :height="1" patternUnits="userSpaceOnUse"><path d="M0.6 0V1" class="cv" /></pattern>
            <pattern id="fm-rack" :width="1.5" :height="1.5" patternUnits="userSpaceOnUse"><path d="M1.5 0H0V1.5" fill="none" class="cv" /></pattern>
            <pattern id="fm-hatch" :width="0.8" :height="0.8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><path d="M0 0V0.8" class="ht" /></pattern>
          </defs>
          <!-- Floor, zones -->
          <rect class="floor" :x="0" :y="0" :width="layout.floor[0]" :height="layout.floor[1]" />
          <rect class="floor-grid" :x="0" :y="0" :width="layout.floor[0]" :height="layout.floor[1]" fill="url(#fm-grid)" />
          <g v-for="(a, i) in layout.areas" :key="a.name" class="area" :class="{ alt: i % 2 }">
            <rect :x="a.x" :y="a.y" :width="a.w" :height="a.h" />
            <line v-if="i" :x1="a.x" :y1="a.y" :x2="a.x" :y2="a.y + a.h" class="sep" vector-effect="non-scaling-stroke" />
            <text :x="a.x + px(8)" :y="a.y + px(15)" :font-size="px(big ? 15 : 11)" class="an"><title>{{ a.name }}</title>{{ areaLabel(a) }}</text>
          </g>
          <rect class="wall" :x="0" :y="0" :width="layout.floor[0]" :height="layout.floor[1]" vector-effect="non-scaling-stroke" />
          <!-- Logistics routes -->
          <polyline v-if="showFlow" class="flow" :points="flowPts" vector-effect="non-scaling-stroke" />
          <!-- Equipment -->
          <g v-for="s in stations" :key="s.code" class="st" :class="['t-' + s.tone, 'k-' + s.kind, { chg: changed.has(s.code), hov: hover?.code === s.code }]"
            v-ai="s.ai" :data-code="s.code" :data-status="s.status" @click="open(s, $event)" @keydown.enter.prevent="open(s, $event)"
            @mouseenter="enter(s, $event)" @mousemove="moveCard($event)" @focus="focusCard(s, $event)" @blur="hover = null">
            <rect class="ripple" :x="s.x" :y="s.y" :width="s.w" :height="s.h" :rx="r(s)" />
            <rect class="body" :x="s.x" :y="s.y" :width="s.w" :height="s.h" :rx="r(s)" vector-effect="non-scaling-stroke" />
            <rect v-if="s.tone === 'offline'" class="hatch" :x="s.x" :y="s.y" :width="s.w" :height="s.h" :rx="r(s)" fill="url(#fm-hatch)" />
            <!-- Simple glyph per equipment type -->
            <rect v-if="s.kind === 'cooling' || s.kind === 'molding'" class="deco" :x="s.x + 0.3" :y="s.y + s.h * 0.3" :width="s.w - 0.6" :height="s.h * 0.4" fill="url(#fm-conv)" />
            <rect v-else-if="s.kind === 'warehouse'" class="deco" :x="s.x + 0.6" :y="s.y + 0.6" :width="s.w - 1.2" :height="s.h - 1.2" fill="url(#fm-rack)" />
            <circle v-else-if="s.kind === 'furnace'" class="deco-l" :cx="s.x + s.w / 2" :cy="s.y + s.h / 2" :r="Math.min(s.w, s.h) * 0.3" vector-effect="non-scaling-stroke" />
            <rect v-else-if="s.kind === 'cnc'" class="deco-l" :x="s.x + s.w * 0.22" :y="s.y + s.h * 0.3" :width="s.w * 0.56" :height="s.h * 0.4" :rx="0.2" vector-effect="non-scaling-stroke" />
            <!-- Buffers: stock level -->
            <template v-if="s.isBuf && s.util !== null">
              <rect class="lvl" :x="s.x" :y="s.y + s.h * (1 - s.util)" :width="s.w" :height="s.h * s.util" :rx="r(s) * 0.6" />
            </template>
            <!-- Status light -->
            <circle class="led" :cx="s.x + px(7)" :cy="s.y + px(7)" :r="px(big ? 4.5 : 3.2)" />
            <!-- Name -->
            <text v-if="s.lab" class="lb" :class="{ out: s.lab.out }" :x="s.lab.x" :y="s.lab.y" :font-size="s.lab.fs" text-anchor="middle">{{ s.lab.text }}</text>
            <text v-if="s.sub" class="lb2" :x="s.sub.x" :y="s.sub.y" :font-size="s.sub.fs" text-anchor="middle">{{ s.sub.text }}</text>
            <!-- WIP / stock count -->
            <g v-if="s.badge" class="wip">
              <rect :x="s.badge.x" :y="s.badge.y" :width="s.badge.w" :height="px(big ? 20 : 15)" :rx="px(big ? 10 : 7.5)" />
              <text :x="s.badge.x + s.badge.w / 2" :y="s.badge.y + px(big ? 14.5 : 11)" :font-size="px(big ? 13 : 10)" text-anchor="middle">{{ s.badge.text }}</text>
            </g>
            <!-- Alarm -->
            <g v-if="s.alarmN" class="alm">
              <circle :cx="s.x + s.w" :cy="s.y" :r="px(big ? 9 : 6.5)" />
              <text :x="s.x + s.w" :y="s.y + px(big ? 4.5 : 3.4)" :font-size="px(big ? 12 : 9)" text-anchor="middle">{{ s.alarmN > 1 ? s.alarmN : '!' }}</text>
            </g>
          </g>
        </svg>
        <!-- Continuous pulse animation lives in the HTML layer (compositor animation, so the whole SVG isn't repainted every frame) -->
        <div class="ov" aria-hidden="true">
          <template v-for="s in pulses" :key="s.code">
            <span v-if="s.tone === 'fault'" class="fglow" :style="{ left: sx(s.x) + 'px', top: sy(s.y) + 'px', width: s.w * ppu + 'px', height: s.h * ppu + 'px' }" />
            <span v-if="s.alarmN" class="aring" :style="{ left: sx(s.x + s.w) + 'px', top: sy(s.y) + 'px' }" />
          </template>
        </div>
        <div v-if="hint" class="hint">{{ $t('factoryMap.ctrlScrollToZoomDrag') }}</div>
        <!-- Hover card -->
        <!-- Card is mounted on body (fixed position): not clipped by the map frame, flips to the other side near screen edges -->
        <Teleport to="body"><div v-if="hover && !big" ref="cardEl" class="card hm-glass fm-card" :style="{ left: cardPos.x + 'px', top: cardPos.y + 'px' }" data-test="map-card" role="tooltip">
          <div class="ch"><b>{{ hover.code }}</b><span>{{ bareName(hover.code, hover.name) }}</span><span class="grow" /><HmStatus :status="hover.status" :label="hover.statusText" /></div>
          <div class="cg">
            <span>{{ $t('factoryMap.duration') }}</span><b class="hm-num">{{ hover.sinceText || '—' }}</b>
            <template v-if="hover.power !== null && hover.power !== undefined"><span>{{ $t('common.power') }}</span><b class="hm-num">{{ fmtNum(hover.power, 1) }} <i>kW</i></b></template>
            <template v-if="hover.isBuf"><span>{{ $t('common.stock') }}</span><b class="hm-num">{{ fmtNum(hover.queue) }} / {{ fmtNum(hover.capacity) }} <i>{{ $t('common.pcs') }}</i></b></template>
            <template v-else-if="hover.wip"><span>{{ $t('common.wip') }}</span><b class="hm-num">{{ hover.wip }} <i>{{ $t('common.pcs') }}</i></b></template>
            <template v-for="t in hover.tagList" :key="t[0]"><span>{{ t[0] }}</span><b class="hm-num">{{ t[1] }}</b></template>
          </div>
          <div v-for="a in hover.alarms" :key="a.msg + a.start" class="ca"><el-icon><WarningFilled /></el-icon>{{ a.msg }}<span class="grow" /><i class="hm-num">{{ (a.start || '').slice(11, 16) }}</i></div>
          <div class="cf">{{ $t('factoryMap.clickToOpenEquipment') }} <kbd>Ctrl</kbd>{{ $t('factoryMap.clickToSendToAi') }}</div>
        </div></Teleport>
      </div>
      <!-- Zone summary: aligned with zones on the floor plan -->
      <div v-if="areaStats.length && !noStrip" class="strip" data-test="map-areas">
        <div v-for="a in areaStats" :key="a.name" class="as" :style="{ flexGrow: a.w }" v-ai="a.ai">
          <div class="asn">{{ a.name }}</div>
          <div class="asb" :title="$t('factoryMap.running0Idle1Fault', [a.n.run, a.n.idle, a.n.fault, a.n.offline])">
            <i v-for="k in ['run', 'setup', 'idle', 'fault', 'offline']" :key="k" :class="'t-' + k" :style="{ flexGrow: a.n[k] || 0 }" />
          </div>
          <div class="asv hm-num"><b>{{ a.n.run }}</b>/{{ a.total }}<span v-if="a.wip" class="aw">{{ $t('factoryMap.buffered0', [a.wip]) }}</span><span v-if="a.n.fault" class="af">{{ $t('factoryMap.fault0', [a.n.fault]) }}</span></div>
        </div>
      </div>
    </template>

    <!-- No layout: status grid -->
    <div v-else class="grid-fb" data-test="map-fallback">
      <div class="fbh"><el-icon><Grid /></el-icon><span>{{ loading ? $t('factoryMap.loadingPlantLayout') : $t('factoryMap.noPlantFloorLayoutShowing') }}</span>
        <span v-if="reason && !loading" class="rs" :title="reason">{{ reason }}</span><span class="grow" />
        <el-button v-if="!loading" size="small" link type="primary" @click="$emit('retry')">{{ $t('common.retry') }}</el-button></div>
      <div v-for="g in groups" :key="g.name" class="fbg">
        <div class="fbgn">{{ g.name || $t('common.other') }}</div>
        <div class="fbt">
          <button v-for="s in g.items" :key="s.code" class="tile" :class="['t-' + s.tone, { chg: changed.has(s.code) }]" v-ai="s.ai" :data-code="s.code" @click="open(s, $event)">
            <i class="led" /><b>{{ s.code }}</b><span>{{ s.statusText }}</span>
            <em v-if="s.alarmN" class="ta">!</em>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// Live plant map: virtual factory floor layout (zones, real equipment positions and sizes) + live status coloring, buffer levels, WIP counts, alarm badges.
// Equipment flashes on status change; hover shows a card; click opens equipment; Ctrl+click / right-click goes through the AI selection framework (kind=device).
// Zoom: Ctrl+wheel / double-click / buttons; drag to pan; "Fit to window" resets. Without a layout, falls back to a status grid by zone.
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { bareName, codeName, fmtNum } from '../../design/format';
import { since, STATE_TEXT, TONE } from './usePoll';

const props = withDefaults(defineProps<{ layout?: any; live?: any[]; now?: string; big?: boolean; fill?: boolean; loading?: boolean; noStrip?: boolean; showFlow?: boolean }>(),
  { live: () => [], now: '', big: false, fill: false, loading: false, noStrip: false, showFlow: true });
defineEmits<{ retry: [] }>();
const router = useRouter();
const LEGEND: [string, string][] = [['run', $t('common.running')], ['idle', $t('common.idle')], ['setup', $t('common.changeover')], ['fault', $t('common.fault')], ['offline', $t('common.offline')]];

const hasLayout = computed(() => !!props.layout?.ok && (props.layout.stations || []).length > 0);
const reason = computed(() => props.layout && !props.layout.ok ? props.layout.reason || '' : '');
const liveBy = computed(() => new Map((props.live || []).map((x: any) => [x.code, x])));

// ---------- View (viewBox zoom/pan) ----------
const base = computed(() => {
  const fl = props.layout?.floor || [100, 50];
  const b0 = props.layout?.bounds || [0, 0, ...fl];
  // fill (display panel with fixed height): show the whole floor, top-aligned, no empty space in the panel
  const b = props.fill ? [Math.min(b0[0], 0), Math.min(b0[1], 0), Math.max(b0[2], fl[0]), Math.max(b0[3], fl[1])] : b0;
  const pad = 2.5;
  return { x: b[0] - pad, y: b[1] - pad, w: b[2] - b[0] + pad * 2, h: b[3] - b[1] + pad * 2 };
});
const view = reactive({ x: 0, y: 0, w: 100, h: 50 });
const zoom = computed(() => base.value.w / view.w);
function fit() { Object.assign(view, base.value); }
watch(base, fit, { immediate: true });
const wrap = ref<HTMLElement>(), svg = ref<SVGSVGElement>();
const box = reactive({ w: 800, h: 300 });
let ro: ResizeObserver | null = null;
onMounted(() => { ro = new ResizeObserver(() => { if (wrap.value) { box.w = wrap.value.clientWidth; box.h = wrap.value.clientHeight; } }); if (wrap.value) ro.observe(wrap.value); });
watch(wrap, (el) => { if (el && ro) { ro.observe(el); box.w = el.clientWidth; box.h = el.clientHeight; } });
onUnmounted(() => ro?.disconnect());
/** Pixels per user unit (metre) */
const ppu = computed(() => Math.max(0.5, Math.min(box.w / view.w, box.h / view.h)));
/** Screen pixels → user units (text and badges don't scale with zoom) */
const px = (n: number) => n / ppu.value;
/** Zone label that fits its zone: English names are longer than Chinese ones, so drop a trailing "Area"/"Zone" and then truncate with "…" (full name in the tooltip). */
function areaLabel(a: any): string {
  const name = String(a.name || '');
  const fs = px(props.big ? 15 : 11);
  const avail = a.w - px(14);
  // Rough text width in user units: CJK glyphs ~1.1em, capitals ~0.72em, others ~0.6em, plus letter-spacing .08em
  const est = (s: string) => [...s].reduce((w, c) => w + fs * ((c.charCodeAt(0) >= 0x2e80 ? 1.1 : /[A-Z]/.test(c) ? 0.72 : 0.6) + 0.08), 0);
  if (est(name) <= avail) return name;
  const short = name.replace(/\s+(area|zone)$/i, '');
  if (est(short) <= avail) return short;
  let s = short;
  while (s.length > 1 && est(s + '…') > avail) s = s.slice(0, -1);
  return s.length > 1 ? s.trimEnd() + '…' : '';
}
// User coords → stage pixels (preserveAspectRatio: centered horizontally; top-aligned when fill, otherwise centered vertically)
const sx = (ux: number) => (ux - view.x) * ppu.value + (box.w - view.w * ppu.value) / 2;
const sy = (uy: number) => (uy - view.y) * ppu.value + (props.fill ? 0 : (box.h - view.h * ppu.value) / 2);
const stageStyle = computed(() => (props.fill ? {} : { aspectRatio: `${base.value.w} / ${base.value.h}` }));

function toUser(cx: number, cy: number) {
  const s = svg.value; if (!s) return { x: 0, y: 0 };
  const p = s.createSVGPoint(); p.x = cx; p.y = cy;
  const m = s.getScreenCTM(); if (!m) return { x: 0, y: 0 };
  const q = p.matrixTransform(m.inverse());
  return { x: q.x, y: q.y };
}
function clampView() {
  const b = base.value;
  view.w = Math.min(b.w, Math.max(b.w / 8, view.w)); view.h = view.w * (b.h / b.w);
  view.x = Math.min(b.x + b.w - view.w, Math.max(b.x, view.x)); view.y = Math.min(b.y + b.h - view.h, Math.max(b.y, view.y));
}
function zoomAt(f: number, ux?: number, uy?: number) {
  const cx = ux ?? view.x + view.w / 2, cy = uy ?? view.y + view.h / 2;
  const nw = Math.min(base.value.w, Math.max(base.value.w / 8, view.w / f)), k = nw / view.w;
  view.x = cx - (cx - view.x) * k; view.y = cy - (cy - view.y) * k; view.w = nw; view.h = nw * (base.value.h / base.value.w);
  clampView();
}
const zoomBy = (f: number) => zoomAt(f);
const hint = ref(false);
let hintT: any;
function onWheel(e: WheelEvent) {
  if (props.big) return;
  if (!(e.ctrlKey || e.metaKey)) { hint.value = true; clearTimeout(hintT); hintT = setTimeout(() => (hint.value = false), 1200); return; }
  e.preventDefault();
  const p = toUser(e.clientX, e.clientY);
  zoomAt(Math.exp(-e.deltaY * 0.0022), p.x, p.y);
}
function onDbl(e: MouseEvent) { if (props.big || (e.target as Element).closest('.st')) return; const p = toUser(e.clientX, e.clientY); zoomAt(1.6, p.x, p.y); }
let drag: { x: number; y: number; vx: number; vy: number; id: number; moved: boolean } | null = null;
let suppressClick = false;
function onDown(e: PointerEvent) {
  if (props.big || e.button !== 0) return;
  drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, id: e.pointerId, moved: false };
}
function onMove(e: PointerEvent) {
  if (!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (!drag.moved && Math.hypot(dx, dy) < 5) return;
  if (!drag.moved) { drag.moved = true; wrap.value?.setPointerCapture?.(drag.id); hover.value = null; }   // Capture the pointer only on a real drag (otherwise the click target becomes the container)
  view.x = drag.vx - dx / ppu.value; view.y = drag.vy - dy / ppu.value; clampView();
}
function onUp() { if (drag?.moved) { suppressClick = true; setTimeout(() => (suppressClick = false), 0); } drag = null; }

// ---------- Equipment ----------
const nowRef = computed(() => props.now);
function aiItem(s: any, lv: any) {
  return { kind: 'device', entity: s.eq_id ? 'equipment' : undefined, id: s.eq_id ?? undefined, link: s.eq_id ? `/e/equipment?id=${s.eq_id}` : undefined,
    label: `${codeName(s.code, s.name)} ${lv ? (STATE_TEXT[lv.status] || lv.status) : $t('lblCanvas.noData')}` + (lv?.alarms?.length ? $t('factoryMap.alarms0', [lv.alarms[0].msg]) : ''),
    data: { [$t('common.equipment')]: codeName(s.code, s.name), [$t('factoryMap.area')]: s.area, [$t('common.status')]: lv ? STATE_TEXT[lv.status] || lv.status : $t('lblCanvas.noData'), [$t('factoryMap.duration')]: lv?.since ? since(lv.since, nowRef.value) : undefined,
      [$t('common.alarm')]: (lv?.alarms || []).map((a: any) => a.msg).join($t('common.k764')) || $t('common.none'), [$t('factoryMap.powerKw')]: lv?.power ?? undefined,
      [$t('factoryMap.wipCount')]: lv?.wip || undefined, [$t('factoryMap.bufferStock')]: lv?.queue ?? undefined, [$t('factoryMap.bufferCapacity')]: lv?.capacity ?? undefined, [$t('common.dataClock')]: nowRef.value } };
}
const TAGN: Record<string, string> = { 'Bath/Temperature': $t('factoryMap.ironTemperatureC'), 'Heat/Phase': $t('factoryMap.furnaceStatus'), 'Monitoring/Spindle/Load': $t('factoryMap.spindleLoad'), 'Tools/ActiveTool': $t('factoryMap.currentTool'),
  'Production/WaitingMolds': $t('factoryMap.moldsAwaitingPour'), 'Line/Molds': $t('factoryMap.moldsOnLine'), 'Production/CurrentPart': $t('factoryMap.currentPart'), 'Result/Last': $t('factoryMap.lastResult'), 'Stock/Utilization': $t('factoryMap.storageUsed') };
function decorate(s: any) {
  const lv = liveBy.value.get(s.code);
  const status = lv?.status || 'offline';
  const tone = TONE[status] || 'offline';
  const isBuf = s.kind === 'buffer';
  const queue = lv?.queue ?? null, capacity = lv?.capacity ?? s.capacity ?? null;
  const util = isBuf && queue !== null && capacity ? Math.max(0, Math.min(1, queue / capacity)) : null;
  return { ...s, live: lv, status, tone, statusText: STATE_TEXT[status] || status, isBuf, queue, capacity, util,
    alarms: lv?.alarms || [], alarmN: (lv?.alarms || []).length + (lv?.andon || 0), wip: lv?.wip || 0, power: lv?.power,
    sinceText: lv?.since ? since(lv.since, props.now) : '',
    tagList: Object.entries(lv?.tags || {}).filter(([k]) => TAGN[k]).slice(0, 3).map(([k, v]) => [TAGN[k], typeof v === 'number' ? fmtNum(v, 1) : String(v)]),
    ai: aiItem(s, lv) };
}
const r = (s: any) => Math.min(s.w, s.h) * 0.12;
const textW = (t: string, fs: number) => [...t].reduce((a, ch) => a + (ch.charCodeAt(0) > 255 ? 1 : 0.58), 0) * fs;
const stations = computed(() => (hasLayout.value ? props.layout.stations : []).map((s0: any) => {
  const s = decorate(s0);
  const fsPx = props.big ? 14 : 11, fs = px(fsPx);
  const text = s.kind === 'plc' ? '' : s.code;
  const wpx = s.w * ppu.value, hpx = s.h * ppu.value;
  if (text) {
    // Write inside the equipment if it fits (shrink font to 9px if needed), otherwise below
    const fit = Math.min(fsPx, (wpx - 8) / textW(text, 1));
    const inside = !s.isBuf && fit >= (props.big ? 13 : 9) && hpx >= fit + 8;
    const ifs = px(fit);
    s.lab = inside ? { text, x: s.x + s.w / 2, y: s.y + s.h / 2 + ifs * 0.36, fs: ifs, out: false } : { text, x: s.x + s.w / 2, y: s.y + s.h + fs * 1.15, fs, out: true };
    // Large equipment also gets the Chinese name
    const nm = s.name.replace(/^\d#/, '');
    if (inside && hpx >= fsPx * 3 + 8 && wpx >= textW(nm, fsPx - 1) + 14) {
      s.lab.y = s.y + s.h / 2 - fs * 0.15;
      s.sub = { text: nm, x: s.x + s.w / 2, y: s.y + s.h / 2 + fs * 1.05, fs: px(fsPx - 1.5) };
    }
  }
  const btext = s.isBuf && s.queue !== null ? fmtNum(s.queue) : '';            // WIP count shown only on buffers (equipment WIP is in the hover card)
  if (btext) {
    const bfs = props.big ? 13 : 10, w = px(textW(btext, bfs) + 10);
    s.badge = { text: btext, w, x: s.x + s.w / 2 - w / 2, y: s.y - px(props.big ? 24 : 18) };
  }
  return s;
}));

const pulses = computed(() => stations.value.filter((s: any) => s.tone === 'fault' || s.alarmN));

// Status change: flash once
const changed = ref(new Set<string>());
const prev = new Map<string, string>();
watch(() => props.live, (items) => {
  const ch: string[] = [];
  for (const it of items || []) {
    const p = prev.get(it.code);
    if (p !== undefined && p !== it.status) ch.push(it.code);
    prev.set(it.code, it.status);
  }
  if (ch.length) {
    changed.value = new Set([...changed.value, ...ch]);
    setTimeout(() => { const n = new Set(changed.value); ch.forEach((c) => n.delete(c)); changed.value = n; }, 1800);
  }
}, { immediate: true });

const flowPts = computed(() => (props.layout?.route || []).map((p: any) => `${p.x},${p.y}`).join(' '));

// ---------- Zone summary ----------
const areaStats = computed(() => {
  if (!hasLayout.value) return [];
  return (props.layout.areas || []).map((a: any) => {
    const inA = stations.value.filter((s: any) => { const cx = s.x + s.w / 2; return cx >= a.x && cx < a.x + a.w; });
    const dev = inA.filter((s: any) => !s.isBuf && s.kind !== 'plc' && s.kind !== 'sink');
    const n: Record<string, number> = { run: 0, idle: 0, setup: 0, fault: 0, offline: 0 };
    dev.forEach((s: any) => { n[s.tone] = (n[s.tone] || 0) + 1; });
    const wip = inA.reduce((t: number, s: any) => t + (s.isBuf ? s.queue || 0 : 0), 0);
    return { name: a.name, w: a.w, n, total: dev.length, wip,
      ai: { kind: 'kpi', label: $t('factoryMap.n0Running12', [a.name, n.run, dev.length]) + (n.fault ? $t('factoryMap.fault02', [n.fault]) : ''), data: { [$t('factoryMap.area')]: a.name, [$t('factoryMap.machines')]: dev.length, [$t('common.running')]: n.run, [$t('common.idle')]: n.idle, [$t('common.fault')]: n.fault, [$t('common.offline')]: n.offline, [$t('factoryMap.bufferStock')]: wip } } };
  });
});

// ---------- Fallback: status grid ----------
const groups = computed(() => {
  const m = new Map<string, any[]>();
  for (const it of props.live || []) {
    if (['crew', 'agv'].includes(it.etype)) continue;
    const s = decorate({ code: it.code, name: it.name, kind: it.etype, area: it.location || it.line, eq_id: it.id, x: 0, y: 0, w: 1, h: 1 });
    const k = it.location || it.line || $t('common.other');
    if (!m.has(k)) m.set(k, []);
    m.get(k)!.push(s);
  }
  return [...m.entries()].map(([name, items]) => ({ name, items }));
});

// ---------- Interaction ----------
function open(s: any, e: Event) {
  if (suppressClick || (e as MouseEvent).ctrlKey || (e as MouseEvent).metaKey) return;
  if (s.eq_id) router.push(`/e/equipment?id=${s.eq_id}`);
}
const hover = ref<any>(null);
const cardPos = reactive({ x: 0, y: 0 });
const cardEl = ref<HTMLElement>();
let lastPt = { x: 0, y: 0 };
// Viewport coords (card position: fixed): flip left if no room on the right, lift up if no room below; based on the card's actual size
function place(cx: number, cy: number) {
  lastPt = { x: cx, y: cy };
  const w = cardEl.value?.offsetWidth || 290, h = cardEl.value?.offsetHeight || 220;
  const vw = window.innerWidth, vh = window.innerHeight, m = 8;
  let x = cx + 16, y = cy + 14;
  if (x + w > vw - m) x = cx - w - 12;
  if (y + h > vh - m) y = vh - m - h;
  cardPos.x = Math.max(m, x); cardPos.y = Math.max(m, y);
}
watch(() => hover.value?.code, () => nextTick(() => { if (hover.value) place(lastPt.x, lastPt.y); }));   // Reposition by actual height once the card has rendered
function enter(s: any, e: MouseEvent) { if (drag?.moved) return; hover.value = s; place(e.clientX, e.clientY); }
function moveCard(e: MouseEvent) { if (hover.value && !drag?.moved) place(e.clientX, e.clientY); }
function focusCard(s: any, e: FocusEvent) { const b = (e.target as Element).getBoundingClientRect(); hover.value = s; place(b.right, b.top); }
// Hover card refreshes with live data
watch(stations, (list) => { if (hover.value) hover.value = list.find((x: any) => x.code === hover.value.code) || null; });
</script>

<style scoped>
.fm { display: flex; flex-direction: column; min-height: 0; position: relative; }
.fm.fill { height: 100%; }
.bar { display: flex; align-items: center; gap: 10px; min-height: 28px; flex-wrap: wrap; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: var(--hm-fs-2xs); color: var(--hm-text-2); }
.lg { display: inline-flex; align-items: center; gap: 5px; }
.lg i { width: 10px; height: 10px; border-radius: 3px; display: inline-block; border: 1.5px solid; }
.lg.t-run i { background: var(--hm-run-soft); border-color: var(--hm-run); } .lg.t-idle i { background: var(--hm-idle-soft); border-color: var(--hm-idle); }
.lg.t-setup i { background: var(--hm-setup-soft); border-color: var(--hm-setup); } .lg.t-fault i { background: var(--hm-fault-soft); border-color: var(--hm-fault); }
.lg.t-offline i { background: var(--hm-offline-soft); border-color: var(--hm-offline); } .lg.buf i { background: linear-gradient(transparent 50%, rgba(11, 165, 196, .35) 50%); border-color: var(--hm-cyan); }
.lg.flow i { width: 16px; height: 0; border: 0; border-top: 2px dashed var(--hm-cyan); border-radius: 0; }
.warn { font-size: var(--hm-fs-2xs); color: var(--hm-idle-ink); background: var(--hm-idle-soft); padding: 0 6px; border-radius: var(--hm-r-pill); }
.zoom { display: inline-flex; align-items: center; border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); background: var(--hm-surface); overflow: hidden; }
.zoom button { border: 0; background: transparent; width: 26px; height: 24px; display: grid; place-items: center; cursor: pointer; color: var(--hm-text-2); }
.zoom button:hover { background: var(--hm-surface-3); color: var(--hm-primary); }
.zl { font-size: 11px; color: var(--hm-text-3); min-width: 38px; text-align: center; }

.stage { position: relative; width: 100%; min-height: 160px; margin-top: 6px; border-radius: var(--hm-r-md); overflow: hidden; user-select: none; touch-action: none; cursor: grab;
  background: radial-gradient(ellipse at 50% 0%, rgba(11, 165, 196, .07), transparent 70%), var(--hm-surface-2); border: 1px solid var(--hm-hairline); }
.fill .stage { flex: 1; min-height: 0; }
.stage:active { cursor: grabbing; }
.svg { width: 100%; height: 100%; display: block; position: absolute; inset: 0; }
.floor { fill: #fff; }
.gl { stroke: rgba(30, 64, 120, .07); stroke-width: .06; }
.cv { stroke: rgba(15, 32, 60, .16); stroke-width: .07; fill: none; }
.ht { stroke: rgba(91, 107, 128, .28); stroke-width: .12; }
.area rect { fill: rgba(20, 99, 214, .018); } .area.alt rect { fill: rgba(11, 165, 196, .035); }
.area .sep { stroke: rgba(20, 99, 214, .22); stroke-dasharray: 4 4; stroke-width: 1; }
.an { fill: var(--hm-text-3); font-weight: 600; letter-spacing: .08em; }
.wall { fill: none; stroke: #9fb1c7; stroke-width: 1.5; }
.flow { fill: none; stroke: var(--hm-cyan); stroke-width: 1.6; stroke-dasharray: 6 5; opacity: .55; pointer-events: none; }

.st { cursor: pointer; outline: none; }
.st .body { stroke-width: 1.4; transition: fill var(--hm-dur-3) var(--hm-ease), stroke var(--hm-dur-3) var(--hm-ease); }
.t-run .body { fill: var(--hm-run-soft); stroke: var(--hm-run); }
.t-idle .body { fill: var(--hm-idle-soft); stroke: var(--hm-idle); }
.t-setup .body { fill: var(--hm-setup-soft); stroke: var(--hm-setup); }
.t-fault .body { fill: var(--hm-fault-soft); stroke: var(--hm-fault); stroke-width: 2; }
.t-offline .body { fill: var(--hm-offline-soft); stroke: var(--hm-offline); stroke-dasharray: 3 2; }
.k-buffer .body { fill: #fff; stroke: var(--hm-cyan); stroke-dasharray: 2 2; }
.lvl { fill: rgba(11, 165, 196, .28); pointer-events: none; transition: y var(--hm-dur-3) var(--hm-ease), height var(--hm-dur-3) var(--hm-ease); }
.deco, .deco-l { pointer-events: none; } .deco-l { fill: none; stroke: rgba(15, 32, 60, .22); stroke-width: 1; }
.hatch { pointer-events: none; }
.led { pointer-events: none; }
.t-run .led { fill: var(--hm-run); } .t-idle .led { fill: var(--hm-idle); } .t-setup .led { fill: var(--hm-setup); }
.t-fault .led { fill: var(--hm-fault); } .t-offline .led { fill: var(--hm-offline); }
.k-buffer .led { display: none; }
.lb { fill: var(--hm-text); font-weight: 700; font-family: var(--hm-font-num); pointer-events: none; letter-spacing: .02em; }
.lb.out { fill: var(--hm-text-2); font-weight: 600; paint-order: stroke; stroke: rgba(255, 255, 255, .9); stroke-width: 3px; stroke-linejoin: round; }
.lb2 { fill: var(--hm-text-2); pointer-events: none; }
.wip rect { fill: var(--hm-text); opacity: .82; } .wip text { fill: #fff; font-family: var(--hm-font-num); font-weight: 600; pointer-events: none; }
.k-buffer .wip rect { fill: var(--hm-cyan); opacity: 1; }
.alm circle { fill: var(--hm-fault); } .alm text { fill: #fff; font-weight: 800; pointer-events: none; }
.ov { position: absolute; inset: 0; pointer-events: none; overflow: hidden; }
.aring { position: absolute; width: 22px; height: 22px; margin: -11px 0 0 -11px; border-radius: 50%; border: 2px solid var(--hm-fault); will-change: transform, opacity; animation: fm-ring 1.4s ease-out infinite; }
.big .aring { width: 30px; height: 30px; margin: -15px 0 0 -15px; }
@keyframes fm-ring { from { transform: scale(.7); opacity: .9; } to { transform: scale(1.9); opacity: 0; } }
.fglow { position: absolute; border-radius: 3px; box-shadow: 0 0 0 3px rgba(220, 43, 43, .55), 0 0 14px rgba(220, 43, 43, .6); will-change: opacity; animation: fm-glow 1.2s ease-in-out infinite; }
@keyframes fm-glow { 50% { opacity: .15; } }
.ripple { fill: none; stroke: transparent; stroke-width: 2; vector-effect: non-scaling-stroke; pointer-events: none; transform-box: fill-box; transform-origin: center; }
.chg .ripple { animation: fm-ripple 1.6s var(--hm-ease) 1; }
.t-run.chg .ripple { stroke: var(--hm-run); } .t-idle.chg .ripple { stroke: var(--hm-idle); } .t-fault.chg .ripple { stroke: var(--hm-fault); } .t-setup.chg .ripple, .t-offline.chg .ripple { stroke: var(--hm-primary); }
@keyframes fm-ripple { 0% { transform: scale(1); opacity: 1; } 100% { transform: scale(1.9); opacity: 0; } }
.st:hover .body, .st.hov .body, .st:focus-visible .body { stroke: var(--hm-primary); stroke-width: 2.4; }
.st[data-ai-selected] .body { stroke: var(--hm-ai-2); stroke-width: 3; filter: drop-shadow(0 0 3px rgba(20, 99, 214, .55)); }
.hint { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); background: rgba(15, 27, 45, .72); color: #fff; font-size: 12px; padding: 6px 12px; border-radius: var(--hm-r-pill); pointer-events: none; }

.card { position: fixed; width: 290px; border-radius: var(--hm-r-md); box-shadow: var(--hm-e3); padding: 10px 12px; z-index: 3000; pointer-events: none; font-size: var(--hm-fs-xs); }
.ch { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; } .ch b { font-family: var(--hm-font-num); font-size: 14px; } .ch span { color: var(--hm-text-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cg { display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; } .cg span { color: var(--hm-text-3); } .cg b { font-weight: 600; text-align: right; } .cg i { font-style: normal; color: var(--hm-text-3); font-weight: 400; }
.ca { display: flex; align-items: center; gap: 5px; margin-top: 6px; color: var(--hm-fault-ink); background: var(--hm-fault-soft); border-radius: var(--hm-r-sm); padding: 3px 6px; }
.ca i { font-style: normal; color: var(--hm-text-3); }
.cf { margin-top: 8px; padding-top: 6px; border-top: 1px solid var(--hm-border); color: var(--hm-text-3); font-size: 11px; }
kbd { font-family: var(--hm-font-mono); font-size: 10px; border: 1px solid var(--hm-border-strong); border-radius: 3px; padding: 0 3px; background: #fff; }

.strip { display: flex; gap: 4px; margin-top: 6px; }
.as { flex-basis: 0; min-width: 0; border-radius: var(--hm-r-sm); padding: 4px 6px 5px; background: var(--hm-surface-2); border: 1px solid var(--hm-hairline); cursor: default; }
.asn { font-size: 11px; color: var(--hm-text-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: 600; }
.asb { display: flex; height: 4px; border-radius: 2px; overflow: hidden; background: var(--hm-surface-3); margin: 3px 0; gap: 1px; }
.asb i { flex-basis: 0; } .asb .t-run { background: var(--hm-run); } .asb .t-idle { background: var(--hm-idle); } .asb .t-setup { background: var(--hm-setup); } .asb .t-fault { background: var(--hm-fault); } .asb .t-offline { background: var(--hm-offline); }
.asv { font-size: 11px; color: var(--hm-text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; } .asv b { color: var(--hm-text); font-size: 13px; }
.aw { margin-left: 6px; } .af { margin-left: 6px; color: var(--hm-fault-ink); font-weight: 600; }

.big .lg { font-size: 15px; } .big .lg i { width: 14px; height: 14px; }
.big .as { padding: 6px 10px; } .big .asn { font-size: 15px; } .big .asv { font-size: 14px; } .big .asv b { font-size: 18px; } .big .asb { height: 6px; }
.big .stage { cursor: default; }

/* Fallback: status grid */
.grid-fb { display: flex; flex-direction: column; gap: 10px; }
.fbh { display: flex; align-items: center; gap: 6px; font-size: var(--hm-fs-xs); color: var(--hm-text-2); background: var(--hm-surface-2); border: 1px dashed var(--hm-border-strong); border-radius: var(--hm-r-md); padding: 6px 10px; }
.rs { color: var(--hm-text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 40%; }
.fbgn { font-size: 11px; font-weight: 600; color: var(--hm-text-3); letter-spacing: .06em; margin-bottom: 4px; }
.fbt { display: grid; grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); gap: 6px; }
.tile { position: relative; display: flex; align-items: center; gap: 6px; padding: 7px 9px; border-radius: var(--hm-r-sm); border: 1px solid; font: inherit; cursor: pointer; text-align: left; }
.tile b { font-family: var(--hm-font-num); font-size: 13px; } .tile span { font-size: 11px; margin-left: auto; }
.tile .led { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.tile.t-run { background: var(--hm-run-soft); border-color: rgba(21, 163, 74, .35); color: var(--hm-run-ink); } .tile.t-run .led { background: var(--hm-run); }
.tile.t-idle { background: var(--hm-idle-soft); border-color: rgba(217, 154, 22, .4); color: var(--hm-idle-ink); } .tile.t-idle .led { background: var(--hm-idle); }
.tile.t-setup { background: var(--hm-setup-soft); border-color: rgba(20, 99, 214, .35); color: var(--hm-setup-ink); } .tile.t-setup .led { background: var(--hm-setup); }
.tile.t-fault { background: var(--hm-fault-soft); border-color: var(--hm-fault); color: var(--hm-fault-ink); } .tile.t-fault .led { background: var(--hm-fault); }
.tile.t-offline { background: var(--hm-offline-soft); border-color: var(--hm-border-strong); color: var(--hm-offline-ink); } .tile.t-offline .led { background: var(--hm-offline); }
.tile.chg { animation: hm-ai-flash 900ms var(--hm-ease); }
.ta { position: absolute; top: -6px; right: -6px; width: 16px; height: 16px; border-radius: 50%; background: var(--hm-fault); color: #fff; font-style: normal; font-size: 11px; font-weight: 800; display: grid; place-items: center; }
@media (prefers-reduced-motion: reduce) { .aring, .fglow, .ripple { animation: none !important; } }
</style>
