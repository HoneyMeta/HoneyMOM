<template>
  <div class="page">
    <HmPageHead :title="$t('common.spc')" :sub="$t('spc.xRControlChartsSubgroup')">
      <span class="flt" data-tour="spc-filter">
        <HmTag v-if="sp[char]" :tone="sp[char] === 'CC' ? 'bad' : 'warn'" data-test="spc-special">{{ sp[char] === 'CC' ? $t('pfmea.criticalCharacteristicCc') : $t('pfmea.significantCharacteristicSc') }}</HmTag>
        <el-select v-model="char" class="w1" @change="load"><el-option v-for="c in d?.chars || ['BORE_DIA']" :key="c" :label="sp[c] ? $t('common.n01', [c, sp[c]]) : c" :value="c" /></el-select>
        <el-select v-model="station" clearable :placeholder="$t('checkTab.allEquipment')" class="w2" @change="load"><el-option v-for="s in d?.stations || []" :key="s" :label="s" :value="s" /></el-select>
        <el-select v-model="n" class="w2" @change="load"><el-option v-for="v in [125, 250, 500]" :key="v" :label="$t('spc.last0Pcs', [v])" :value="v" /></el-select>
        <el-tooltip :content="$t('common.refresh')"><el-button :aria-label="$t('common.refresh')" @click="load"><el-icon><Refresh /></el-icon></el-button></el-tooltip>
      </span>
    </HmPageHead>

    <HmSkeleton v-if="!d" variant="table" :rows="6" />
    <HmCard v-else-if="!d.enough"><HmEmpty :title="$t('spc.notEnoughData')" :desc="$t('spc.only0MeasurementsSoFar', [d.count])" /></HmCard>
    <template v-else>
      <div class="grid g6" data-tour="kpis">
        <HmKpi :label="$t('spc.n0Mean', [d.name])" :value="d.mean" :digits="4" :unit="d.unit" compact />
        <HmKpi :label="$t('spares.spec')" :value="`${d.lsl ?? '—'} ~ ${d.usl ?? '—'}`" :tick="false" compact :ai="{ data: { [$t('charge.min')]: d.lsl, [$t('charge.max')]: d.usl, [$t('common.unit')]: d.unit } }" />
        <HmKpi label="Cp" :value="d.Cp" :digits="2" :status="cls(d.Cp)" compact />
        <HmKpi label="Cpk" :value="d.Cpk" :digits="2" :status="cls(d.Cpk)" compact :hint="d.Cpk >= 1.33 ? $t('spc.capable') : d.Cpk >= 1 ? $t('spc.marginallyCapable') : $t('spc.notCapable')" />
        <HmKpi label="Ppk" :value="d.Ppk" :digits="2" :status="cls(d.Ppk)" compact />
        <HmKpi :label="$t('spc.ruleViolations')" :value="d.rules.length" format="int" :unit="$t('spc.k4808')" :status="d.rules.length ? 'bad' : 'ok'" compact :hint="d.rules.length ? $t('spc.outOfControlPoints') : $t('spc.processInControl')" />
      </div>
      <HmCard class="mt" :title="$t('spc.xChart')" :sub="`UCL ${fmtNum(d.ucl_x, 4)} · CL ${fmtNum(d.xbb, 4)} · LCL ${fmtNum(d.lcl_x, 4)}`" data-tour="spc-x">
        <Chart :option="xopt" :height="280" :ai="xAi" />
      </HmCard>
      <div class="grid g2 mt">
        <HmCard :title="$t('spc.rChart')" :sub="`R̄ ${fmtNum(d.rbar, 4)} · UCL ${fmtNum(d.ucl_r, 4)}`"><Chart :option="ropt" :height="220" :ai="$t('spc.rChart')" /></HmCard>
        <HmCard :title="$t('spc.histogram')" :sub="$t('spc.n0Measurements', [d.count])"><Chart :option="hopt" :height="220" :ai="$t('spc.dimensionDistribution')" /></HmCard>
      </div>
      <HmCard v-if="d.rules.length" class="mt" :title="$t('spc.ruleViolations2')" :sub="$t('spc.n0', [d.rules.length])" flush>
        <HmDataTable :columns="RC" :rows="ruleRows" :ai="(r: any) => ({ kind: 'alert', label: $t('spc.n0Subgroup1Violation2', [d.name, r.no, r.rule]), data: { [$t('msa.characteristic')]: d.name, [$t('spc.subgroup')]: r.no, [$t('common.time')]: r.time, [$t('spc.mean')]: r.xbar, [$t('common.rules')]: r.rule } })" />
      </HmCard>
    </template>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { computed, onMounted, ref } from 'vue';
import { get } from '../api';
import Chart from '../components/Chart.vue';
import { CH, targetLine } from '../design/chart';
import { fmtNum } from '../design/format';

const d = ref<any>(null), char = ref('BORE_DIA'), station = ref(''), n = ref(125);
const cls = (v: number) => (v == null ? '' : v >= 1.33 ? 'ok' : v >= 1 ? 'warn' : 'bad');
const RC = [{ key: 'no', label: $t('spc.subgroup'), num: true, width: 70 }, { key: 'time', label: $t('common.time'), width: 150 }, { key: 'xbar', label: 'X̄', num: true, digits: 4 }, { key: 'rule', label: $t('common.rules') }];
const ruleRows = computed(() => (d.value?.rules || []).map((r: any) => ({ no: r.i + 1, time: d.value.times[r.i], xbar: d.value.xbar[r.i], rule: r.rule })));
async function load() { d.value = await get(`/api/quality/spc?char=${char.value}&station=${station.value}&n=${n.value}`); }
// Special characteristics (from the effective control plan, M5): CC critical / SC significant
const sp = ref<Record<string, string>>({});
onMounted(async () => { sp.value = (await get('/api/iatf/special', true).catch(() => ({ chars: {} }))).chars || {}; });
onMounted(load);
const line = (v: number, name: string, color: string) => ({ yAxis: v, ...targetLine(`${name} ${fmtNum(v, 4)}`, color) });
const xAi = (p: any) => {
  const x = d.value, i = p.dataIndex; if (i === undefined) return null;
  const rule = x.rules.find((r: any) => r.i === i);
  return { kind: 'chart-point', label: $t('spc.n0Subgroup1X2', [x.name, i + 1, fmtNum(x.xbar[i], 4), rule ? $t('spc.violation') : '']),
    data: { [$t('msa.characteristic')]: x.name, [$t('spc.subgroup')]: i + 1, [$t('common.time')]: x.times[i], [$t('spc.mean')]: x.xbar[i], [$t('spc.range')]: x.r[i], UCL: x.ucl_x, LCL: x.lcl_x, [$t('spc.rules')]: rule?.rule || $t('common.none') } };
};
const xopt = computed(() => {
  const x = d.value; const bad = new Set(x.rules.map((r: any) => r.i));
  return { tooltip: { trigger: 'axis' }, grid: { right: 110 }, xAxis: { type: 'category', data: x.times.map((t: string) => t.slice(5, 16)) },
    yAxis: { type: 'value', scale: true }, series: [{ name: 'X̄', type: 'line', showSymbol: true, lineStyle: { color: CH.primary },
      data: x.xbar.map((v: number, i: number) => ({ value: +v.toFixed(5), itemStyle: { color: bad.has(i) ? CH.bad : CH.primary }, symbolSize: bad.has(i) ? 9 : 5 })),
      markLine: { data: [line(x.ucl_x, 'UCL', CH.bad), line(x.xbb, 'CL', CH.ok), line(x.lcl_x, 'LCL', CH.bad),
        ...(x.usl != null ? [line(x.usl, 'USL', CH.violet)] : []), ...(x.lsl != null ? [line(x.lsl, 'LSL', CH.violet)] : [])] } }] };
});
const ropt = computed(() => ({ tooltip: { trigger: 'axis' }, grid: { right: 100 }, xAxis: { type: 'category', data: d.value.times.map((t: string) => t.slice(11, 16)) },
  yAxis: { type: 'value' }, series: [{ name: 'R', type: 'line', showSymbol: true, data: d.value.r.map((v: number) => +v.toFixed(5)), itemStyle: { color: CH.cyan }, lineStyle: { color: CH.cyan },
    markLine: { data: [line(d.value.ucl_r, 'UCL', CH.bad), line(d.value.rbar, 'R̄', CH.ok)] } }] }));
const hopt = computed(() => { const h = d.value.hist; return { tooltip: {},
  xAxis: { type: 'category', data: h.counts.map((_: any, i: number) => ((h.edges[i] + h.edges[i + 1]) / 2).toFixed(4)) }, yAxis: { type: 'value' },
  series: [{ name: $t('common.parts'), type: 'bar', data: h.counts, itemStyle: { color: CH.primary, borderRadius: [2, 2, 0, 0] }, barCategoryGap: '8%' }] }; });
</script>

<style scoped>
.flt { display: inline-flex; gap: var(--hm-sp-2); flex-wrap: wrap; }
.w1 { width: calc(160px * var(--hm-scale)); } .w2 { width: calc(130px * var(--hm-scale)); }
</style>
