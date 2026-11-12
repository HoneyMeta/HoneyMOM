<template>
  <div class="page">
    <HmPageHead :title="$t('backtrace.defectBacktraceToFoundry')" :sub="$t('backtrace.defectsFoundOnlyAfterMachining')">
      <span class="flt" data-tour="bt-filter">
        <el-select v-model="group" class="w1" @change="load"><el-option v-for="(v, k) in d?.groups || {}" :key="k" :label="v" :value="k" /></el-select>
        <el-segmented v-model="days" :options="[1, 3, 7, 30].map((x) => ({ label: $t('common.n0Days', [x]), value: x }))" @change="load" />
      </span>
      <el-button type="primary" data-tour="bt-8d" :disabled="!d" @click="to8d"><el-icon><DocumentAdd /></el-icon>{{ $t('backtrace.start8d') }}</el-button>
    </HmPageHead>

    <HmSkeleton v-if="!d" variant="table" :rows="6" />
    <template v-else>
      <div class="grid g4" data-tour="kpis">
        <HmKpi :label="$t('backtrace.tracePart')" :value="d.n ?? d.total ?? null" format="int" :unit="$t('common.pcs')" :hint="$t('common.last0Days', [days])" />
        <HmKpi :label="$t('backtrace.defectiveParts')" :value="d.bad ?? null" format="int" :unit="$t('common.pcs')" :status="d.bad ? 'warn' : ''" />
        <HmKpi :label="$t('backtrace.avgDefectRate')" :value="d.rate ?? null" format="pct" :digits="2" good-when="down" />
        <HmKpi :label="$t('backtrace.significantFactors')" :value="sig.length" format="int" :unit="$t('common.k550')" :status="sig.length ? 'bad' : 'ok'" :hint="sig.slice(0, 2).map((f: any) => f.name).join($t('common.k42')) || $t('backtrace.noSignificantDifference')" />
      </div>
      <HmCard class="mt summary" :title="$t('common.conclusion')">
        <template #actions><HmTag tone="primary">{{ $t('backtrace.statisticalTests') }}</HmTag></template>
        <div class="md" v-ai="{ kind: 'text', label: $t('backtrace.defectBacktraceConclusion'), data: { [$t('common.defect')]: d.groups?.[group], [$t('common.conclusion')]: d.text } }">{{ d.text }}</div>
      </HmCard>
      <AiExplain class="mt" feature="backtrace" url="/api/ai/backtrace/explain" :body="{ days, group }" :title="$t('backtrace.aiRootCauseInsight')" :button="$t('backtrace.askAiForRootCause')"
        :hint="$t('backtrace.aiExplainsTheMostLikely')" />
      <div class="grid g2 mt" data-tour="bt-factors">
        <HmCard v-for="f in d.factors" :key="f.key" :title="f.name" :sub="`p = ${Math.pow(10, -f.score).toPrecision(2)}${f.lift ? $t('backtrace.worstGroupIs0The', [f.lift]) : ''}`">
          <template #actions><HmTag v-if="f.score >= 1.3 && d.bad >= 10" tone="bad">{{ $t('msaResult.significant') }}</HmTag><HmTag v-else-if="f.score >= 1.3" tone="warn">{{ $t('backtrace.smallSample') }}</HmTag></template>
          <Chart :option="opt(f)" :height="200" :ai="(p: any) => ({ kind: 'chart-point', label: $t('backtrace.n012DefectRate', [f.name, p.name, f.unit || '', fmtPct(p.value, 2)]), data: { [$t('backtrace.factor')]: f.name, [$t('common.group')]: p.name, [$t('backtrace.defectRate')]: p.value, [$t('common.parts')]: f.bins[p.dataIndex]?.n } })" />
        </HmCard>
      </div>
      <HmEmpty v-if="!d.factors?.length" :title="$t('backtrace.noFactorsToAnalyze')" :desc="$t('backtrace.notEnoughDefectivePartsIn')" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { get, post } from '../api';
import AiExplain from '../ai/AiExplain.vue';
import Chart from '../components/Chart.vue';
import { CH, targetLine } from '../design/chart';
import { fmtPct } from '../design/format';

const router = useRouter();
const d = ref<any>(null), group = ref('casting'), days = ref(7);
const sig = computed(() => (d.value?.factors || []).filter((f: any) => f.score >= 1.3 && d.value.bad >= 10));
async function load() { d.value = await get(`/api/cross/backtrace?days=${days.value}&group=${group.value}`); }
onMounted(load);
const opt = (f: any) => ({ tooltip: { formatter: (p: any) => `${p.name}${f.unit || ''}<br>${$t('backtrace.defectRate')} ${fmtPct(p.value, 2)}<br>${$t('common.parts')} ${f.bins[p.dataIndex].n}` },
  grid: { top: 12 }, xAxis: { type: 'category', data: f.bins.map((b: any) => b.label), axisLabel: { rotate: f.type === 'cat' ? 30 : 0 } },
  yAxis: { type: 'value', axisLabel: { formatter: (v: number) => (v * 100).toFixed(0) + '%' } },
  series: [{ name: $t('backtrace.defectRate'), type: 'bar', data: f.bins.map((b: any) => ({ value: b.rate, itemStyle: { color: b === f.worst || b.label === f.worst?.label ? CH.bad : CH.primary } })),
    markLine: { data: [{ yAxis: d.value.rate, ...targetLine($t('msaResult.mean')) }] } }] });
async function to8d() {
  const title = (d.value?.groups?.[group.value] || $t('backtrace.qualityIssue')) + $t('backtrace.backtraceAnalysis');
  const r = await post('/api/e/report_8d', { title, d2: d.value?.text });
  ElMessage.success($t('backtrace.n8dReportCreated')); router.push('/e/report_8d?id=' + r.id);
}
</script>

<style scoped>
.flt { display: inline-flex; gap: var(--hm-sp-2); flex-wrap: wrap; align-items: center; }
.w1 { width: calc(200px * var(--hm-scale)); }
.summary { background: linear-gradient(90deg, var(--hm-primary-soft), var(--hm-surface) 55%); }
</style>
