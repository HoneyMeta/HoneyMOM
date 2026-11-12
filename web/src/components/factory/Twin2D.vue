<template>
  <div class="t2" :class="{ edit: editable }" data-test="twin2d">
    <svg ref="svg" class="cv" :viewBox="vb" preserveAspectRatio="xMidYMid meet" @pointermove="onMove" @pointerup="onUp" @pointerleave="onUp" @dragover.prevent @drop="onDrop">
      <g class="bg">
        <template v-for="(p, i) in prims" :key="'p' + i">
          <polyline v-if="p.t === 'p'" :points="pts(p.pts)" :class="['l', layerCls(p.l), { closed: p.z }]" vector-effect="non-scaling-stroke" />
          <circle v-else-if="p.t === 'c'" :cx="p.x" :cy="Y(p.y)" :r="p.r" class="l c" vector-effect="non-scaling-stroke" />
          <text v-else-if="p.t === 't' && showText" :x="p.x" :y="Y(p.y)" :font-size="Math.max(p.h, 0.4)" class="bt">{{ p.s }}</text>
        </template>
        <g v-for="(a, i) in areas" :key="'a' + i" class="area">
          <polygon :points="pts(a.pts)" vector-effect="non-scaling-stroke" />
        </g>
      </g>
      <g v-for="d in devs" :key="d.code" class="dev" :class="['s-' + tone(d), { sel: selected === d.code, drag: dragging === d.code }]"
        :transform="`translate(${d.x} ${Y(d.y)}) rotate(${-(d.rot || 0)})`" :data-code="d.code" @pointerdown="onDown($event, d)" @click="$emit('pick', d)">
        <rect :x="-d.w / 2" :y="-d.d / 2" :width="d.w" :height="d.d" rx="0.15" vector-effect="non-scaling-stroke" />
        <line :x1="d.w / 2 - Math.min(d.w, d.d) * 0.25" y1="0" :x2="d.w / 2" y2="0" class="dir" vector-effect="non-scaling-stroke" />
        <text :font-size="lbl(d)" text-anchor="middle" dominant-baseline="central" :transform="`rotate(${d.rot || 0})`">{{ d.code }}</text>
      </g>
    </svg>
    <div v-if="!devs.length && !prims.length" class="empty">{{ emptyText }}</div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// 2D twin: draws the floor plan background (DXF entities, zones) in plant coordinates (metres, Y up) + equipment outlines (rotated by orientation), color = live status.
// editable: drag equipment to reposition (emit moved {code: {x, y}}); drop from outside (dataTransfer 'text/device' = code) at the drop point.
import { computed, ref } from 'vue';

const props = withDefaults(defineProps<{ layout: any; live?: any[]; editable?: boolean; selected?: string; showText?: boolean; emptyText?: string }>(),
  { live: () => [], editable: false, selected: '', showText: true, emptyText: $t('twin2D.noFloorPlanOrEquipment') });
const emit = defineEmits<{ pick: [d: any]; moved: [items: Record<string, { x: number; y: number }>] }>();
const svg = ref<SVGSVGElement | null>(null);
const moved = ref<Record<string, { x: number; y: number }>>({});
const dragging = ref(''), off = ref({ x: 0, y: 0 });

const B = computed(() => props.layout?.bounds || [0, 0, ...(props.layout?.floor || [100, 50])]);
const H = computed(() => B.value[3]);
const Y = (y: number) => H.value - y;
const vb = computed(() => { const [x0, y0, x1, y1] = B.value; const p = Math.max(x1 - x0, y1 - y0) * 0.02; return `${x0 - p} ${H.value - y1 - p} ${x1 - x0 + 2 * p} ${y1 - y0 + 2 * p}`; });
const pts = (a: number[][]) => (a || []).map((q) => `${q[0]},${Y(q[1])}`).join(' ');
const prims = computed(() => props.layout?.prims || []);
const areas = computed(() => props.layout?.areas || []);
const liveBy = computed(() => new Map((props.live || []).map((x: any) => [x.code, x])));
const devs = computed(() => (props.layout?.devices || []).map((d: any) => ({ ...d, ...(moved.value[d.code] || {}) })));
const layerCls = (l: string) => (/WALL|\u5899/i.test(l) ? 'wall' : /AISLE|\u901a\u9053/i.test(l) ? 'aisle' : /AGV|PATH/i.test(l) ? 'path' : /AREA|\u533a\u57df/i.test(l) ? 'areal' : 'other');
const lbl = (d: any) => Math.max(0.35, Math.min(1.1, Math.min(d.w, d.d) * 0.32));
function tone(d: any) {
  const s = liveBy.value.get(d.code)?.status || d.status || 'offline';
  return ({ running: 'run', idle: 'idle', setup: 'setup', down: 'down', offline: 'off' } as any)[s] || 'off';
}
function toUser(ev: PointerEvent | DragEvent) {
  const s = svg.value!; const pt = s.createSVGPoint(); pt.x = ev.clientX; pt.y = ev.clientY;
  const p = pt.matrixTransform(s.getScreenCTM()!.inverse());
  return { x: p.x, y: H.value - p.y };
}
function onDown(ev: PointerEvent, d: any) {
  if (!props.editable) return;
  ev.preventDefault();
  const p = toUser(ev);
  dragging.value = d.code; off.value = { x: p.x - d.x, y: p.y - d.y };
  (ev.target as Element).setPointerCapture?.(ev.pointerId);
}
function onMove(ev: PointerEvent) {
  if (!dragging.value) return;
  const p = toUser(ev);
  moved.value = { ...moved.value, [dragging.value]: { x: Math.round((p.x - off.value.x) * 10) / 10, y: Math.round((p.y - off.value.y) * 10) / 10 } };
}
function onUp() {
  if (!dragging.value) return;
  dragging.value = '';
  emit('moved', { ...moved.value });
}
function onDrop(ev: DragEvent) {
  if (!props.editable) return;
  const code = ev.dataTransfer?.getData('text/device');
  if (!code) return;
  const p = toUser(ev);
  moved.value = { ...moved.value, [code]: { x: Math.round(p.x * 10) / 10, y: Math.round(p.y * 10) / 10 } };
  emit('moved', { ...moved.value });
}
function reset() { moved.value = {}; }
defineExpose({ reset, toUser });
</script>

<style scoped>
.t2 { position: relative; width: 100%; height: 100%; min-height: 320px; background: var(--hm-surface-2); border-radius: var(--hm-r-md); overflow: hidden; }
.cv { width: 100%; height: 100%; display: block; }
.l { fill: none; stroke: #9aa5b4; stroke-width: 1; }
.l.wall { stroke: #4a5566; stroke-width: 2; } .l.aisle { stroke: #c4ccd6; stroke-dasharray: 4 3; } .l.path { stroke: #d0a43a; stroke-dasharray: 2 3; }
.l.areal { stroke: #b7c2d0; } .l.c { stroke: #d0a43a; }
.bt { fill: #5b6676; font-family: var(--hm-font-sans, sans-serif); }
.area polygon { fill: rgba(64, 120, 200, 0.05); stroke: none; }
.dev rect { fill: #e7edf4; stroke: #4a5566; stroke-width: 1.2; }
.dev .dir { stroke: #4a5566; stroke-width: 1.5; }
.dev text { fill: #1d2433; font-weight: 600; pointer-events: none; font-family: var(--hm-font-sans, sans-serif); }
.dev.s-run rect { fill: var(--hm-run-soft, #dff3e6); stroke: var(--hm-run, #1f9d55); }
.dev.s-idle rect { fill: var(--hm-idle-soft, #fff3d6); stroke: var(--hm-idle, #c98a00); }
.dev.s-setup rect { fill: #e5e9ff; stroke: #4f63d2; }
.dev.s-down rect { fill: var(--hm-fault-soft, #fde3e3); stroke: var(--hm-fault, #d33); }
.dev.s-off rect { fill: #eef0f3; stroke: #9aa5b4; }
.dev.sel rect { stroke-width: 3; stroke: var(--hm-primary, #2f6fde); }
.edit .dev { cursor: grab; } .edit .dev.drag { cursor: grabbing; opacity: .85; }
.empty { position: absolute; inset: 0; display: grid; place-items: center; color: var(--hm-text-3); font-size: var(--hm-fs-sm); }
</style>
