<template>
  <div class="page">
    <HmPageHead :title="$t('common.qualityOverview')" :sub="$t('quality.inspectionNonconformanceAndProcessCa', [days])" art="quality">
      <el-segmented v-model="days" :options="[{ label: $t('common.n1Day'), value: 1 }, { label: $t('common.n7Days'), value: 7 }, { label: $t('common.n30Days'), value: 30 }]" @change="load" />
      <el-button @click="router.push('/e/insp_record')"><el-icon><Tickets /></el-icon>{{ $t('quality.inspectionRecords') }}</el-button>
      <el-button @click="router.push('/e/report_8d')"><el-icon><Document /></el-icon>{{ $t('quality.n8dReports') }}</el-button>
    </HmPageHead>

    <div class="grid g4" data-tour="kpis">
      <HmKpi :label="$t('quality.endoscopeInspections')" :value="d?.endo.total" format="int" :unit="$t('common.pcs')" :loading="!d" :hint="$t('common.last0Days', [days])" />
      <HmKpi :label="$t('quality.endoscopeNgRate')" :value="d?.endo.rate" format="pct" :digits="2" :status="d ? (d.endo.rate > 0.1 ? 'bad' : d.endo.rate > 0.05 ? 'warn' : 'ok') : ''" :loading="!d"
        :hint="d ? $t('quality.ng0Pcs', [d.endo.ng]) : ''" good-when="down" />
      <HmKpi :label="$t('home.openNcrs')" :value="d ? (d.status.open || 0) + (d.status.reviewing || 0) : null" format="int" :unit="$t('common.receipts')" :status="d && (d.status.open || d.status.reviewing) ? 'bad' : 'ok'"
        :loading="!d" :hint="d ? $t('quality.closed0', [d.status.closed || 0]) : ''" to="/e/ncr?view=pending" :ai="{ entity: 'ncr' }" />
      <HmKpi :label="$t('quality.boreProcessCapabilityCpk')" :value="spc?.Cpk ?? null" :digits="2" :status="spc?.Cpk == null ? '' : spc.Cpk >= 1.33 ? 'ok' : spc.Cpk >= 1 ? 'warn' : 'bad'" :loading="!spc"
        :hint="$t('quality.n133Capable')" to="/quality/spc" />
    </div>

    <div class="grid g2 mt">
      <HmCard :title="$t('quality.defectPareto')" :sub="$t('quality.sortedByCountLineCumulative')" data-tour="q-pareto">
        <HmSkeleton v-if="!d" variant="block" :height="300" />
        <HmEmpty v-else-if="!d.pareto.length" compact :title="$t('quality.noDefectsRecordedInThis')" />
        <Chart v-else :option="pareto" :height="300" :ai="$t('quality.defectPareto')" />
      </HmCard>
      <HmCard :title="$t('quality.nonconformanceDisposition')" :sub="d ? $t('quality.n0Ncrs', [dispTotal]) : ''" data-tour="q-disp">
        <HmSkeleton v-if="!d" variant="block" :height="300" />
        <HmEmpty v-else-if="!dispTotal" compact :title="$t('quality.noNonconformancesInThisPeriod')" />
        <Chart v-else :option="disp" :height="300" :ai="$t('quality.nonconformanceDisposition')" />
      </HmCard>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { get } from '../api';
import Chart from '../components/Chart.vue';
import { CH } from '../design/chart';

const router = useRouter();
const d = ref<any>(null), spc = ref<any>(null), days = ref(7);
async function load() { d.value = await get('/api/quality/summary?days=' + days.value); }
onMounted(async () => { load(); spc.value = await get('/api/quality/spc?char=BORE_DIA', true).catch(() => ({ Cpk: null })); });
const pareto = computed(() => {
  const p = d.value.pareto, tot = p.reduce((a: number, x: any) => a + x.n, 0) || 1; let acc = 0;
  return { tooltip: { trigger: 'axis' }, legend: {},
    xAxis: { type: 'category', data: p.map((x: any) => x.name), axisLabel: { interval: 0, rotate: p.length > 6 ? 25 : 0 } },
    yAxis: [{ type: 'value', name: $t('common.pcs') }, { type: 'value', max: 100, axisLabel: { formatter: '{value}%' }, splitLine: { show: false } }],
    series: [{ name: $t('quality.defects'), type: 'bar', data: p.map((x: any) => x.n), itemStyle: { color: CH.primary } },
      { name: $t('quality.cumulativeShare'), type: 'line', yAxisIndex: 1, showSymbol: true, data: p.map((x: any) => +((acc += x.n) / tot * 100).toFixed(1)), itemStyle: { color: CH.amber }, lineStyle: { color: CH.amber } }] };
});
const DISP: Record<string, string> = { pending: $t('common.awaitingDisposition'), rework: $t('common.rework'), scrap: $t('common.scrapped'), concession: $t('common.useAsIs'), return: $t('common.return2') };
const DCOL: Record<string, string> = { pending: CH.offline, rework: CH.primary, scrap: CH.bad, concession: CH.amber, return: CH.violet };
const dispTotal = computed(() => Object.values(d.value?.disposition || {}).reduce((a: number, v: any) => a + (Number(v) || 0), 0));
const disp = computed(() => ({ series: [{ type: 'pie', radius: ['46%', '68%'],
  data: Object.entries(d.value.disposition).map(([k, v]) => ({ name: DISP[k] || k, value: v, itemStyle: { color: DCOL[k] } })) }] }));
</script>
