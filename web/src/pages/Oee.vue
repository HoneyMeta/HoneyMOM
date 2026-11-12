<template>
  <div class="page">
    <HmPageHead :title="$t('common.oee')" :sub="$t('oee.oeeAvailabilityPerformanceQualityCal')">
      <el-segmented v-model="hours" :options="RANGES" data-tour="oee-range" @change="load" />
    </HmPageHead>

    <div class="grid g4" data-tour="kpis">
      <HmKpi :label="$t('common.avgOee')" :value="avg.OEE" format="pct" :status="lvl(avg.OEE)" :hint="$t('oee.n0CountingMachinesLast1', [rows.length, hours])" :loading="!d" />
      <HmKpi :label="$t('home.availability')" :value="avg.A" format="pct" :hint="$t('oee.down0HChangeover1', [fmtNum(loss.down, 1), fmtNum(loss.setup, 1)])" :loading="!d" />
      <HmKpi :label="$t('home.performance')" :value="avg.P" format="pct" :hint="$t('oee.actualCycleStandardCycle')" :loading="!d" />
      <HmKpi :label="$t('home.quality')" :value="avg.Q" format="pct" :hint="$t('oee.good0Defective1', [fmtNum(sum.good), fmtNum(sum.bad)])" :loading="!d" />
    </div>

    <HmCard class="mt" :title="$t('daily.oeeByMachine')" :sub="d ? $t('oee.n01ClickABar', [fmtTime(d.from), fmtTime(d.to)]) : ''" data-tour="oee-chart">
      <HmSkeleton v-if="!d" variant="block" :height="300" />
      <HmEmpty v-else-if="!rows.length" :title="$t('oee.noDataFromCountingMachines')" :desc="$t('oee.tryALongerTimeRange')" />
      <Chart v-else :option="bars" :height="300" :ai="$t('daily.oeeByMachine')" @click="(p: any) => pick(rows[p.dataIndex])" />
    </HmCard>

    <div class="grid split mt">
      <HmCard :title="$t('common.details')" :sub="$t('oee.clickARowForThe')" flush>
        <HmDataTable :columns="COLS" :rows="tableRows" entity="equipment" clickable max-height="420" :row-label="(r: any) => `${r.code} ${r.name} OEE ${fmtPct(r.OEE)}`" @row-click="pick">
          <template #cell-OEE="{ row }"><b class="hm-num" :class="lvl(row.OEE)">{{ fmtPct(row.OEE) }}</b></template>
        </HmDataTable>
      </HmCard>
      <HmCard :title="curName ? $t('oee.n0Last24H', [curName]) : $t('oee.machineTimeline')" :sub="$t('oee.hourlyOeeStatus')" data-tour="oee-trend">
        <HmEmpty v-if="!tr" compact :title="$t('oee.selectAMachine')" :desc="$t('oee.clickABarAboveOr')" />
        <template v-else>
          <Chart :option="trend" :height="200" :ai="$t('oee.n0HourlyOee', [curName])" />
          <div class="tl" role="img" :aria-label="$t('oee.n0StatusTimelineLast24', [curName])">
            <div v-for="s in tr.timeline" :key="s.id" :class="'s-' + s.state" :style="seg(s)" :title="`${SN[s.state] || s.state} ${s.start} ~ ${s.end || $t('oee.now')}`"
              v-ai="{ kind: 'field', entity: 'equipment', id: curId, label: `${curName} ${SN[s.state] || s.state} ${fmtTime(s.start, 'time')}~${s.end ? fmtTime(s.end, 'time') : $t('oee.now')}`, data: { [$t('common.status')]: SN[s.state] || s.state, [$t('common.start')]: s.start, [$t('common.end')]: s.end || $t('oee.now') } }" />
          </div>
          <div class="lg"><span v-for="(v, k) in SN" :key="k"><i :class="'s-' + k" />{{ v }}</span></div>
        </template>
      </HmCard>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { computed, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';
import { get } from '../api';
import Chart from '../components/Chart.vue';
import { CH, targetLine } from '../design/chart';
import { fmtNum, fmtPct, fmtTime } from '../design/format';
import './aiActions';

const route = useRoute();
const d = ref<any>(null), hours = ref(24), tr = ref<any>(null), curName = ref(''), curId = ref<number | null>(null);
const RANGES = [2, 8, 24, 72, 168].map((h) => ({ label: h < 72 ? $t('oee.n0H', [h]) : $t('common.n0Days', [h / 24]), value: h }));
const SN: Record<string, string> = { Executing: $t('common.running'), Idle: $t('common.idle'), Setup: $t('oee.changeoverToolChange'), Stopped: $t('common.down') };
const COLS = [{ key: 'code', label: $t('common.equipment'), width: 80 }, { key: 'name', label: $t('common.name') }, { key: 'OEE', label: 'OEE', num: true }, { key: 'A', label: $t('home.availability'), format: 'pct', num: true },
  { key: 'P', label: $t('home.performance'), format: 'pct', num: true }, { key: 'Q', label: $t('home.quality'), format: 'pct', num: true }, { key: 'good', label: $t('common.good'), num: true }, { key: 'bad', label: $t('common.defective'), num: true },
  { key: 'down', label: $t('oee.downH'), num: true, digits: 1 }, { key: 'setup', label: $t('oee.changeoverH'), num: true, digits: 1 }];
const rows = computed<any[]>(() => d.value?.rows || []);
// /api/oee loss buckets are keyed in Chinese by the backend (services/equipment.py); English aliases win when present
const LOSS_ZH: Record<string, string> = { down: '\u505c\u673a', setup: '\u6362\u578b\u6362\u5200', idle: '\u5f85\u673a/\u7b49\u5f85', run: '\u8fd0\u884c' };
const lossOf = (l: any, k: string) => l?.[k] ?? l?.[LOSS_ZH[k]];
const tableRows = computed(() => rows.value.map((r) => ({ ...r, down: lossOf(r.loss, 'down'), setup: lossOf(r.loss, 'setup'), idle: lossOf(r.loss, 'idle') })));
const mean = (k: string) => { const v = rows.value.map((r) => r[k]).filter((x) => typeof x === 'number'); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
const avg = computed(() => ({ OEE: mean('OEE'), A: mean('A'), P: mean('P'), Q: mean('Q') }));
const sum = computed(() => ({ good: rows.value.reduce((a, r) => a + (r.good || 0), 0), bad: rows.value.reduce((a, r) => a + (r.bad || 0), 0) }));
const loss = computed<any>(() => { const o: any = { down: 0, setup: 0 }; for (const r of rows.value) for (const k of Object.keys(o)) o[k] += lossOf(r.loss, k) || 0; return o; });
const lvl = (v: any) => (v === null || v === undefined ? '' : v < 0.6 ? 'bad' : v < 0.8 ? 'warn' : 'ok');

async function load() { d.value = await get('/api/equipment/oee?hours=' + hours.value); }
async function pick(r: any) { if (!r) return; curName.value = `${r.code} ${r.name}`; curId.value = r.id; tr.value = await get('/api/equipment/oee_trend/' + r.id); }
onMounted(async () => { await load(); const id = Number(route.query.eid); pick(rows.value.find((x) => x.id === id) || rows.value.find((x) => x.etype === 'cnc') || rows.value[0]); });

const pctAxis = { type: 'value', max: 1, axisLabel: { formatter: (v: number) => Math.round(v * 100) + '%' } };
const bars = computed(() => ({ tooltip: { trigger: 'axis', valueFormatter: (v: number) => fmtPct(v) }, legend: {},
  xAxis: { type: 'category', data: rows.value.map((r) => r.code) }, yAxis: pctAxis,
  series: [
    { name: 'OEE', type: 'bar', data: rows.value.map((r) => ({ value: r.OEE, itemStyle: { color: r.OEE < 0.6 ? CH.bad : CH.primary } })), markLine: { data: [{ yAxis: 0.85, ...targetLine($t('oee.worldClass85')) }] } },
    { name: $t('home.availability'), type: 'line', data: rows.value.map((r) => r.A), showSymbol: true },
    { name: $t('home.performance'), type: 'line', data: rows.value.map((r) => r.P), showSymbol: true },
    { name: $t('home.quality'), type: 'line', data: rows.value.map((r) => r.Q), showSymbol: true },
  ].map((s, i) => (i ? { ...s, color: [CH.cyan, CH.amber, CH.violet][i - 1] } : s)) }));
const trend = computed(() => ({ tooltip: { trigger: 'axis', valueFormatter: (v: number) => fmtPct(v) }, grid: { top: 12 },
  xAxis: { type: 'category', data: tr.value.points.map((p: any) => p.t.slice(11, 16)) }, yAxis: pctAxis,
  series: [{ name: 'OEE', type: 'bar', data: tr.value.points.map((p: any) => p.OEE), itemStyle: { color: CH.cyan } }] }));
const T = (s: string) => new Date(s.replace(' ', 'T')).getTime();
function seg(s: any) {
  const end = T(tr.value.now), start = end - 24 * 3600e3;
  const a = Math.max(start, T(s.start)), b = Math.min(end, s.end ? T(s.end) : end);
  return { left: ((a - start) / (end - start) * 100) + '%', width: Math.max(0.1, (b - a) / (end - start) * 100) + '%' };
}
</script>

<style scoped>
.split { grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); }
@media (max-width: 1279px) { .split { grid-template-columns: minmax(0, 1fr); } }
.tl { position: relative; height: calc(20px * var(--hm-scale)); background: var(--hm-surface-3); border-radius: var(--hm-r-xs); margin: 10px 0 6px; overflow: hidden; }
.tl div { position: absolute; top: 0; bottom: 0; }
.s-Executing { background: var(--hm-run); } .s-Idle { background: var(--hm-idle); } .s-Setup { background: var(--hm-setup); } .s-Stopped { background: var(--hm-fault); }
.lg { display: flex; gap: 14px; font-size: var(--hm-fs-xs); color: var(--hm-text-3); }
.lg i { display: inline-block; width: 10px; height: 6px; border-radius: 2px; margin-right: 5px; vertical-align: middle; }
b.ok { color: var(--hm-run-ink); } b.warn { color: var(--hm-idle-ink); } b.bad { color: var(--hm-fault-ink); }
</style>
