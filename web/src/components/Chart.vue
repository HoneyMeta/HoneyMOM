<template><div ref="el" class="hm-chart" :style="{ height: height + 'px', width: '100%' }" /></template>

<script setup lang="ts">
import { $t } from '../i18n';
// ECharts container: unified theme (design/chart.ts: palette, axes, tooltip, donut labels) + auto-resize + hand data points to AI.
//   <Chart :option="opt" :height="260" ai="OEE by equipment" />            Ctrl+click / right-click hands the hovered data point to AI (kind=chart-point)
//   <Chart :option="opt" :ai="(p) => ({ kind: 'chart-point', label: …, entity: 'equipment', id: … })" />   custom
// Without ai the chart doesn't take part in AI selection. dark=true is for shop-floor displays (dark theme).
import * as echarts from 'echarts';
import { onMounted, onUnmounted, ref, watch } from 'vue';
import { aiState, normalizeItem, type AiItem } from '../ai';
import { chartScale, themed, themeName } from '../design/chart';
import { fmtNum } from '../design/format';

const props = withDefaults(defineProps<{ option: any; height?: number; dark?: boolean; ai?: string | ((p: any) => AiItem | null) | null }>(), { height: 260, dark: false, ai: null });
const emit = defineEmits(['click']);
const el = ref<HTMLElement>();
let chart: echarts.ECharts | null = null, ro: ResizeObserver | null = null;

const val = (v: any) => (Array.isArray(v) ? v[v.length - 1] : v && typeof v === 'object' && 'value' in v ? v.value : v);
function pointItem(p: any): AiItem | null {
  if (!props.ai || p?.componentType !== 'series') return null;
  if (typeof props.ai === 'function') return props.ai(p);
  const v = val(p.value), shown = typeof v === 'number' ? (Math.abs(v) <= 1.5 && /rate|ratio|yield|OEE|%|\u7387|\u6bd4/i.test(props.ai + (p.seriesName || '')) ? (v * 100).toFixed(1) + '%' : fmtNum(v)) : String(v ?? '');
  return { kind: 'chart-point', label: `${props.ai} · ${p.seriesName && !/^series\d/.test(p.seriesName) ? p.seriesName + ' ' : ''}${p.name ?? ''} = ${shown}`,
    data: { [$t('common.chart')]: props.ai, [$t('chart.series')]: p.seriesName, [$t('chart.category')]: p.name, [$t('common.value')]: v, [$t('chart.show')]: shown, ...(p.percent !== undefined ? { [$t('chart.share')]: p.percent + '%' } : {}) } };
}
/** Hovering a data point: attach it to the container (AiLayer's Ctrl+click / right-click walks up the DOM for __ai) */
function hover(p: any) {
  const host = el.value as any; if (!host) return;
  const it = normalizeItem(pointItem(p));
  host.__ai = it; host.__aiPoint = !!it;
  if (it) { host.setAttribute('data-ai-key', it.key!); host.setAttribute('data-ai-kind', 'chart-point'); if (aiState.items.some((x) => x.key === it.key)) host.setAttribute('data-ai-selected', ''); else host.removeAttribute('data-ai-selected'); }
  else { host.removeAttribute('data-ai-key'); host.removeAttribute('data-ai-selected'); }
}

let k = 1;
function render(o: any, replace = false) {
  chart?.setOption(themed(o, props.height, k), replace);
}
onMounted(() => {
  k = chartScale();
  chart = echarts.init(el.value!, props.dark ? 'dark' : themeName(k));
  chart.on('click', (p) => emit('click', p));
  chart.on('mouseover', hover);
  chart.on('globalout', () => hover(null));
  chart.on('mouseout', () => { if (el.value) (el.value as any).__aiPoint = false; });
  // Line chart (no dots) hovering an axis slot: hand all series values at that slot to AI
  chart.on('updateAxisPointer', (e: any) => {
    const info = e.axesInfo?.[0];
    if (!info || !props.ai || typeof props.ai !== 'string' || (el.value as any)?.__aiPoint) return;
    const o = chart!.getOption() as any, ax = (o.xAxis?.[0]?.type === 'category' ? o.xAxis[0] : o.yAxis?.[0]) || {};
    const cat = ax.data?.[info.value];
    if (cat === undefined) return;
    const vals = (o.series || []).map((s: any) => [s.name || '', val(s.data?.[info.value])]).filter((x: any) => x[1] !== undefined && x[1] !== null);
    const it = normalizeItem({ kind: 'chart-point', label: $t('chart.n012', [props.ai, typeof cat === 'object' ? cat.value : cat, vals.map((x: any) => `${x[0]} ${typeof x[1] === 'number' ? fmtNum(x[1]) : x[1]}`).join($t('common.k362'))]),
      data: { [$t('common.chart')]: props.ai, [$t('chart.category')]: cat, ...Object.fromEntries(vals.map((x: any, i: number) => [x[0] || $t('chart.series0', [i + 1]), x[1]])) } });
    const host = el.value as any; host.__ai = it; host.setAttribute('data-ai-key', it!.key!); host.setAttribute('data-ai-kind', 'chart-point');
  });
  render(props.option);
  ro = new ResizeObserver(() => chart?.resize()); ro.observe(el.value!);
});
watch(() => props.option, (o) => render(o, true), { deep: true });
onUnmounted(() => { ro?.disconnect(); chart?.dispose(); });
</script>

<style scoped>
.hm-chart { position: relative; }
.hm-chart[data-ai-key]:not([data-ai-selected]) { box-shadow: none; }
</style>
