<template>
  <div class="rv" data-test="report-view">
    <HmEmpty v-if="!res || !res.rows?.length" compact :title="$t('common.noData')" :desc="$t('reportView.tryDifferentFilters')" />
    <template v-else>
      <div v-if="chart === 'kpi'" class="kpis">
        <div v-for="(c, i) in measureCols" :key="c.key" class="kp"><small>{{ c.label }}</small><b class="hm-num">{{ fmt(total(i)) }}</b></div>
      </div>
      <Chart v-else-if="chart !== 'table' && option" :option="option" :height="height" :ai="title || $t('common.report')" />
      <div v-if="chart === 'table' || showTable" class="tbl" :style="{ maxHeight: tableMax }">
        <table class="hm-table dense">
          <thead><tr><th v-for="c in res.columns" :key="c.key" :class="{ num: c.t === 'num' }">{{ c.label }}</th></tr></thead>
          <tbody><tr v-for="(r, i) in res.rows" :key="i"><td v-for="(v, j) in r" :key="j" :class="{ num: res.columns[j]?.t === 'num' }">{{ v === null || v === '' ? '—' : fmt(v) }}</td></tr></tbody>
        </table>
      </div>
      <div v-if="res.truncated" class="small muted">{{ $t('reportView.showingFirst0RowsOnly', [res.rows.length]) }}</div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// Report result: bar / line / pie / KPI card / table (shared by the Report Designer and the Home report widget). First dimension column = X axis, aggregate columns = series.
import { computed } from 'vue';
import Chart from '../components/Chart.vue';

const props = withDefaults(defineProps<{ res: any; chart?: string; height?: number; showTable?: boolean; title?: string; tableMax?: string }>(),
  { chart: 'table', height: 260, showTable: false, title: '', tableMax: '360px' });
const dimIdx = computed(() => (props.res?.columns || []).map((c: any, i: number) => (c.dim ? i : -1)).filter((i: number) => i >= 0));
const meaIdx = computed(() => (props.res?.columns || []).map((c: any, i: number) => (c.measure || (!c.dim && c.t === 'num') ? i : -1)).filter((i: number) => i >= 0));
const measureCols = computed(() => meaIdx.value.map((i: number) => props.res.columns[i]));
const total = (k: number) => (props.res?.rows || []).reduce((a: number, r: any[]) => a + (Number(r[meaIdx.value[k]]) || 0), 0);
const fmt = (v: any) => (typeof v === 'number' ? (Number.isInteger(v) ? v.toLocaleString() : (+v.toFixed(3)).toLocaleString()) : String(v));
const option = computed(() => {
  const r = props.res;
  if (!r?.rows?.length || !meaIdx.value.length) return null;
  const x = r.rows.map((row: any[]) => (dimIdx.value.length ? dimIdx.value.map((i: number) => row[i] ?? $t('reportView.empty')).join(' / ') : ''));
  if (props.chart === 'pie') {
    const m = meaIdx.value[0];
    return { tooltip: { trigger: 'item' }, series: [{ type: 'pie', radius: ['38%', '68%'], data: r.rows.map((row: any[], i: number) => ({ name: x[i], value: row[m] })) }] };
  }
  return { tooltip: { trigger: 'axis' }, legend: meaIdx.value.length > 1 ? { top: 0 } : undefined, grid: { left: 48, right: 12, top: meaIdx.value.length > 1 ? 30 : 12, bottom: 36 },
    xAxis: { type: 'category', data: x, axisLabel: { interval: 0, rotate: x.length > 8 ? 30 : 0 } }, yAxis: { type: 'value' },
    series: meaIdx.value.map((m: number) => ({ name: r.columns[m].label, type: props.chart === 'line' ? 'line' : 'bar', smooth: true, barMaxWidth: 28, data: r.rows.map((row: any[]) => row[m]) })) };
});
</script>

<style scoped>
.rv { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.kpis { display: flex; gap: 10px; flex-wrap: wrap; }
.kp { flex: 1 1 120px; background: var(--hm-surface-2); border: 1px solid var(--hm-hairline); border-radius: var(--hm-r-md); padding: 8px 12px; display: flex; flex-direction: column; }
.kp small { color: var(--hm-text-3); } .kp b { font-size: 22px; }
.tbl { overflow: auto; border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); }
</style>
