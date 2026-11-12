<template>
  <div ref="wrap" class="lbl-wrap" @pointerdown.self="emit('select', -1)">
    <svg ref="svg" data-test="lbl-canvas" class="lbl-svg" :width="W * z + PADPX * 2" :height="H * z + PADPX * 2"
      :viewBox="`${-pad} ${-pad} ${W + pad * 2} ${H + pad * 2}`" @pointerdown="emit('select', -1)">
      <defs>
        <pattern :id="gid + 'm'" width="1" height="1" patternUnits="userSpaceOnUse"><path d="M 1 0 L 0 0 0 1" class="g1" fill="none" /></pattern>
        <pattern :id="gid + 'M'" width="5" height="5" patternUnits="userSpaceOnUse"><path d="M 5 0 L 0 0 0 5" class="g5" fill="none" /></pattern>
      </defs>
      <rect class="paper" x="0" y="0" :width="W" :height="H" />
      <rect v-if="z >= 4" x="0" y="0" :width="W" :height="H" :fill="`url(#${gid}m)`" pointer-events="none" />
      <rect x="0" y="0" :width="W" :height="H" :fill="`url(#${gid}M)`" pointer-events="none" />

      <g v-for="(el, i) in elements" :key="i" class="el" :class="{ on: i === selected }" @pointerdown.stop="down($event, i)">
        <!-- Text -->
        <template v-if="el.type === 'text'">
          <rect v-bind="boxAttrs(i)" class="hit" />
          <text :transform="textTf(el)" :x="textUx(el)" :y="num(el.h, 4) * 0.8" :font-size="num(el.h, 4)" :font-weight="el.bold ? 'bold' : 'normal'"
            :text-anchor="textAnchor(el)" :class="['ink', { ghost: !shownText(el) }]" :font-family="FONT">{{ shownText(el) || el.text || $t('common.text') }}</text>
        </template>
        <!-- Barcode -->
        <template v-else-if="el.type === 'barcode'">
          <rect v-bind="boxAttrs(i)" class="hit" />
          <template v-if="codeOk(el)">
            <image :href="codeUrl(el)" :x="num(el.x)" :y="num(el.y)" :width="is2d(el) ? side(el) : num(el.w, 40)" :height="is2d(el) ? side(el) : barsH(el)"
              preserveAspectRatio="none" :opacity="showVars ? 0.3 : 1" />
            <text v-if="!is2d(el) && el.hr" :x="num(el.x) + num(el.w, 40) / 2" :y="num(el.y) + barsH(el) + 0.4 + hrH(el) * 0.8" :font-size="hrH(el)"
              text-anchor="middle" class="ink" :font-family="FONT" :opacity="showVars ? 0.3 : 1">{{ codeData(el) }}</text>
          </template>
          <template v-else>
            <rect :x="num(el.x)" :y="num(el.y)" :width="num(el.w, 40)" :height="num(el.h, 12)" class="ph" />
            <text :x="num(el.x) + num(el.w, 40) / 2" :y="num(el.y) + num(el.h, 12) / 2 + 1" :font-size="Math.min(3, num(el.h, 12) / 3)" text-anchor="middle" class="warn">{{ codeProblem(el) }}</text>
          </template>
          <text v-if="showVars" :x="num(el.x) + num(el.w, 40) / 2" :y="num(el.y) + num(el.h, 12) / 2 + varFs(el) * 0.35" :font-size="varFs(el)" text-anchor="middle" class="var"
            :font-family="FONT">{{ el.data }}</text>
        </template>
        <!-- Line -->
        <template v-else-if="el.type === 'line'">
          <line :x1="num(el.x)" :y1="num(el.y)" :x2="num(el.x2, el.x)" :y2="num(el.y2, el.y)" class="hitline" :stroke-width="Math.max(num(el.t, 0.3), 8 / z)" />
          <line :x1="num(el.x)" :y1="num(el.y)" :x2="num(el.x2, el.x)" :y2="num(el.y2, el.y)" class="stroke" :stroke-width="num(el.t, 0.3)" />
        </template>
        <!-- Box -->
        <template v-else-if="el.type === 'box'">
          <rect v-if="el.fill" :x="num(el.x)" :y="num(el.y)" :width="num(el.w)" :height="num(el.h)" class="inkfill" />
          <template v-else>
            <rect v-bind="boxAttrs(i)" class="hit" />
            <rect :x="num(el.x) + num(el.t, 0.3) / 2" :y="num(el.y) + num(el.t, 0.3) / 2" :width="Math.max(0, num(el.w) - num(el.t, 0.3))"
              :height="Math.max(0, num(el.h) - num(el.t, 0.3))" class="stroke" fill="none" :stroke-width="num(el.t, 0.3)" />
          </template>
        </template>
        <!-- Image -->
        <template v-else-if="el.type === 'image'">
          <image v-if="String(el.src || '').startsWith('data:image/')" :href="el.src" :x="num(el.x)" :y="num(el.y)" :width="num(el.w, 20)" :height="num(el.h, 10)" preserveAspectRatio="none" />
          <template v-else>
            <rect :x="num(el.x)" :y="num(el.y)" :width="num(el.w, 20)" :height="num(el.h, 10)" class="ph" />
            <text :x="num(el.x) + num(el.w, 20) / 2" :y="num(el.y) + num(el.h, 10) / 2 + 1" :font-size="Math.min(3, num(el.h, 10) / 3)" text-anchor="middle" class="warn">{{ $t('common.image') }}</text>
          </template>
        </template>
      </g>

      <!-- Selection box and handles -->
      <g v-if="cur" class="selui">
        <template v-if="cur.type === 'line'">
          <rect v-for="hp in lineHandles" :key="hp.k" :x="hp.x - hs / 2" :y="hp.y - hs / 2" :width="hs" :height="hs" class="hd" :class="'c-move'"
            @pointerdown.stop="downHandle($event, hp.k)" />
        </template>
        <template v-else>
          <rect v-bind="selBox" class="outline" fill="none" />
          <rect v-for="hp in boxHandles" :key="hp.k" :x="hp.x - hs / 2" :y="hp.y - hs / 2" :width="hs" :height="hs" class="hd" :class="'c-' + hp.k"
            @pointerdown.stop="downHandle($event, hp.k)" />
        </template>
      </g>
    </svg>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// Label design canvas: SVG in millimetre coordinates (viewBox = mm, scaled by zoom px/mm), 1 / 5 mm grid, 0.5 mm snap.
// Element rendering matches render_svg in honeymom/labelrender.py (text height = h mm, alignment box width w, after clockwise rotation the bounding box top-left stays at x,y; barcode images from /api/label/barcode.svg).
// Drag to move, drag handles to resize (lines: drag endpoints); changes write directly into the objects in elements, emitting begin / end at start / finish (parent records undo).
import { computed, ref } from 'vue';
import { tok } from '../../api';

const props = defineProps<{ width: number; height: number; elements: any[]; selected: number; zoom: number; data: Record<string, any>; showVars: boolean }>();
const emit = defineEmits<{ select: [i: number]; begin: []; end: [changed: boolean] }>();

const FONT = 'Microsoft YaHei, SimHei, sans-serif';
const PADPX = 14;
const gid = 'lg' + Math.random().toString(36).slice(2, 8);
const wrap = ref<HTMLElement>(), svg = ref<SVGSVGElement>();
const W = computed(() => Number(props.width) || 100), H = computed(() => Number(props.height) || 60);
const z = computed(() => props.zoom || 4);
const pad = computed(() => PADPX / z.value);
const hs = computed(() => 9 / z.value);

const num = (v: any, d = 0) => (v === null || v === undefined || v === '' || isNaN(Number(v)) ? d : Number(v));
const snap = (v: number) => Math.round(v * 2) / 2;

// ---------- Placeholders (same as labelrender.fill: {{key}} {{a.b}} {{key|default}}) ----------
const PH = /\{\{\s*([^{}|]*?)\s*(?:\|([^{}]*))?\}\}/g;
function look(data: any, key: string) {
  if (data && key in data) return data[key];
  let cur = data;
  for (const k of key.split('.')) { if (cur && typeof cur === 'object' && k in cur) cur = cur[k]; else return undefined; }
  return cur;
}
function fill(text: any, data: any) {
  return String(text ?? '').replace(PH, (_m, k, d) => { const v = look(data, k); return v === undefined || v === null || v === '' ? (d ?? '') : String(v); });
}

// ---------- Text ----------
const wide = (c: string) => /[\u1100-\u115f\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\ufe30-\ufe4f\uff00-\uff60\uffe0-\uffe6]/.test(c);
const textW = (s: string, h: number) => [...s].reduce((a, c) => a + (wide(c) ? 1 : 0.55), 0) * h;
const rotOf = (el: any) => { const r = ((Math.round(num(el.rotate)) % 360) + 360) % 360; return [0, 90, 180, 270].includes(r) ? r : 0; };
function shownText(el: any) { return props.showVars ? String(el.text ?? '') : fill(el.text, props.data); }
function textL(el: any) { const s = shownText(el) || String(el.text || $t('common.text')); return num(el.w) || textW(s, num(el.h, 4)); }
function textTf(el: any) {
  const rot = rotOf(el), L = textL(el), h = num(el.h, 4);
  const [dx, dy] = ({ 0: [0, 0], 90: [h, 0], 180: [L, h], 270: [0, L] } as Record<number, number[]>)[rot];
  return `translate(${num(el.x) + dx} ${num(el.y) + dy})` + (rot ? ` rotate(${rot})` : '');
}
const textAnchor = (el: any) => (num(el.w) ? ({ center: 'middle', right: 'end' } as any)[el.align] || 'start' : 'start');
const textUx = (el: any) => (num(el.w) ? ({ center: textL(el) / 2, right: textL(el) } as any)[el.align] || 0 : 0);

// ---------- Barcode ----------
const is2d = (el: any) => ['qr', 'datamatrix'].includes(String(el.symbology || 'code128').toLowerCase());
const side = (el: any) => Math.min(num(el.w, 20), num(el.h, 20));
const hrH = (el: any) => Math.max(2, Math.min(3.5, num(el.h, 12) * 0.22));
const barsH = (el: any) => (el.hr && !is2d(el) ? num(el.h, 12) - hrH(el) - 0.4 : num(el.h, 12));
const codeData = (el: any) => fill(el.data, props.data);
function codeProblem(el: any) {
  const d = codeData(el);
  if (!d) return $t('lblCanvas.noData');
  if (!is2d(el) && /[^\x00-\x7f]/.test(d)) return $t('lblCanvas.code128CannotEncodeChineseCharacters');
  return '';
}
const codeOk = (el: any) => !codeProblem(el);
// The barcode image depends only on symbology and data: 1D codes stretch to w × bar height, 2D codes sit in a min(w,h) square (same as server), so request a fixed size and let the browser cache it
function codeUrl(el: any) {
  const sym = String(el.symbology || 'code128').toLowerCase();
  const [w, h] = is2d(el) ? [20, 20] : [40, 10];
  return `/api/label/barcode.svg?sym=${encodeURIComponent(sym)}&data=${encodeURIComponent(codeData(el))}&w=${w}&h=${h}&token=${tok()}`;
}
const varFs = (el: any) => Math.max(1.6, Math.min(3, num(el.h, 12) / 4, (num(el.w, 40) / Math.max(4, String(el.data || '').length)) * 1.6));

// ---------- Bounding box ----------
function bbox(el: any) {
  if (!el) return { x: 0, y: 0, w: 0, h: 0 };
  if (el.type === 'line') {
    const x1 = num(el.x), y1 = num(el.y), x2 = num(el.x2, x1), y2 = num(el.y2, y1);
    return { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1) };
  }
  if (el.type === 'text') {
    const L = textL(el), h = num(el.h, 4), r = rotOf(el);
    return r === 90 || r === 270 ? { x: num(el.x), y: num(el.y), w: h, h: L } : { x: num(el.x), y: num(el.y), w: L, h };
  }
  return { x: num(el.x), y: num(el.y), w: num(el.w, el.type === 'image' ? 20 : 40), h: num(el.h, el.type === 'image' ? 10 : 12) };
}
function boxAttrs(i: number) { const b = bbox(props.elements[i]); return { x: b.x, y: b.y, width: Math.max(b.w, 0.5), height: Math.max(b.h, 0.5) }; }
const cur = computed(() => (props.selected >= 0 ? props.elements[props.selected] : null));
const selBox = computed(() => { const b = bbox(cur.value); const m = 2 / z.value; return { x: b.x - m, y: b.y - m, width: b.w + m * 2, height: b.h + m * 2 }; });
const boxHandles = computed(() => {
  const b = bbox(cur.value), x0 = b.x, y0 = b.y, x1 = b.x + b.w, y1 = b.y + b.h, xm = (x0 + x1) / 2, ym = (y0 + y1) / 2;
  return [{ k: 'nw', x: x0, y: y0 }, { k: 'n', x: xm, y: y0 }, { k: 'ne', x: x1, y: y0 }, { k: 'e', x: x1, y: ym }, { k: 'se', x: x1, y: y1 }, { k: 's', x: xm, y: y1 },
    { k: 'sw', x: x0, y: y1 }, { k: 'w', x: x0, y: ym }];
});
const lineHandles = computed(() => { const el = cur.value; return el ? [{ k: 'p1', x: num(el.x), y: num(el.y) }, { k: 'p2', x: num(el.x2, el.x), y: num(el.y2, el.y) }] : []; });

// ---------- Dragging ----------
let drag: any = null;
function pt(ev: PointerEvent) {
  const s = svg.value!, p = s.createSVGPoint();
  p.x = ev.clientX; p.y = ev.clientY;
  const m = s.getScreenCTM();
  const q = m ? p.matrixTransform(m.inverse()) : p;
  return { x: q.x, y: q.y };
}
function start(ev: PointerEvent, mode: string, i: number) {
  if (ev.button !== 0) return;
  const el = props.elements[i];
  drag = { mode, i, p0: pt(ev), c0: { x: ev.clientX, y: ev.clientY }, o: JSON.parse(JSON.stringify(el)), b: bbox(el), moved: false };
  emit('begin');
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up, { once: true });
  ev.preventDefault();
}
function down(ev: PointerEvent, i: number) { emit('select', i); start(ev, 'move', i); }
function downHandle(ev: PointerEvent, k: string) { start(ev, k, props.selected); }
function move(ev: PointerEvent) {
  if (!drag) return;
  if (!drag.moved && Math.hypot(ev.clientX - drag.c0.x, ev.clientY - drag.c0.y) < 3) return;
  drag.moved = true;
  const p = pt(ev), el = props.elements[drag.i], o = drag.o;
  if (!el) return;
  if (drag.mode === 'move') {
    const dx = snap(num(o.x) + p.x - drag.p0.x) - num(o.x), dy = snap(num(o.y) + p.y - drag.p0.y) - num(o.y);
    el.x = +(num(o.x) + dx).toFixed(2); el.y = +(num(o.y) + dy).toFixed(2);
    if (el.type === 'line') { el.x2 = +(num(o.x2, o.x) + dx).toFixed(2); el.y2 = +(num(o.y2, o.y) + dy).toFixed(2); }
    return;
  }
  if (drag.mode === 'p1' || drag.mode === 'p2') {
    let x = snap(p.x), y = snap(p.y);
    const [ax, ay] = drag.mode === 'p1' ? [num(o.x2, o.x), num(o.y2, o.y)] : [num(o.x), num(o.y)];
    if (ev.shiftKey) { if (Math.abs(x - ax) > Math.abs(y - ay)) y = ay; else x = ax; }
    if (drag.mode === 'p1') { el.x = x; el.y = y; } else { el.x2 = x; el.y2 = y; }
    return;
  }
  const b = drag.b, k = drag.mode;
  let L = b.x, T = b.y, R = b.x + b.w, B = b.y + b.h;
  if (k.includes('w')) L = Math.min(snap(p.x), R - 0.5);
  if (k.includes('e')) R = Math.max(snap(p.x), L + 0.5);
  if (k.includes('n')) T = Math.min(snap(p.y), B - 0.5);
  if (k.includes('s')) B = Math.max(snap(p.y), T + 0.5);
  const w = +(R - L).toFixed(2), h = +(B - T).toFixed(2);
  el.x = +L.toFixed(2); el.y = +T.toFixed(2);
  if (el.type === 'text') {
    const r = rotOf(el);
    if (r === 90 || r === 270) { el.w = h; el.h = w; } else { el.w = w; el.h = h; }
  } else if (el.type === 'barcode' && is2d(el)) {
    const s = Math.max(w, h); el.w = s; el.h = s;
  } else { el.w = w; el.h = h; }
}
function up() {
  window.removeEventListener('pointermove', move);
  const moved = !!drag?.moved;
  drag = null;
  emit('end', moved);
}

defineExpose({ wrap, bbox });
</script>

<style scoped>
.lbl-wrap { overflow: auto; background: var(--hm-surface-2); border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); display: flex; min-height: 0; outline: none; }
.lbl-svg { margin: auto; display: block; flex: none; touch-action: none; user-select: none; }
.paper { fill: var(--hm-surface); stroke: var(--hm-border-strong); stroke-width: 1px; vector-effect: non-scaling-stroke; }
.g1 { stroke: var(--hm-hairline); stroke-width: 0.5px; vector-effect: non-scaling-stroke; }
.g5 { stroke: var(--hm-border); stroke-width: 0.75px; vector-effect: non-scaling-stroke; }
.ink { fill: var(--hm-text); } .inkfill { fill: var(--hm-text); cursor: move; }
.ghost { fill: var(--hm-text-3); font-style: italic; }
.stroke { stroke: var(--hm-text); }
.var { fill: var(--hm-primary); font-family: var(--hm-font-mono); }
.warn { fill: var(--hm-fault-ink); }
.ph { fill: var(--hm-surface-3); stroke: var(--hm-border-strong); stroke-dasharray: 3 2; stroke-width: 1px; vector-effect: non-scaling-stroke; }
.hit { fill: transparent; cursor: move; }
.hitline { stroke: transparent; cursor: move; }
.el { cursor: move; }
.el:hover .hit { fill: var(--hm-primary-soft); fill-opacity: .5; }
.outline { stroke: var(--hm-primary); stroke-width: 1.2px; stroke-dasharray: 4 3; vector-effect: non-scaling-stroke; pointer-events: none; }
.hd { fill: var(--hm-surface); stroke: var(--hm-primary); stroke-width: 1.5px; vector-effect: non-scaling-stroke; }
.c-nw, .c-se { cursor: nwse-resize; } .c-ne, .c-sw { cursor: nesw-resize; } .c-n, .c-s { cursor: ns-resize; } .c-e, .c-w { cursor: ew-resize; } .c-move { cursor: crosshair; }
</style>
