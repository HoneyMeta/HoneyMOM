<template>
  <div class="mr" data-test="msa-result">
    <div class="vd">
      <HmTag :tone="VT[vkey]?.[1] || 'neutral'" data-test="msa-verdict">{{ VT[vkey]?.[0] || '—' }}</HmTag>
      <template v-if="isGrr">
        <span class="kv">%GRR <b class="hm-num" :class="cls(r.pct_grr)">{{ pct(r.pct_grr) }}</b></span>
        <span v-if="r.pct_tol_grr != null" class="kv">{{ $t('msaResult.tolerance') }} <b class="hm-num" :class="cls(r.pct_tol_grr)">{{ pct(r.pct_tol_grr) }}</b></span>
        <span class="kv">ndc <b class="hm-num" :class="r.ndc != null && r.ndc < 5 ? 'bad' : 'good'">{{ r.ndc ?? '—' }}</b></span>
        <span class="t2 small">{{ $t('msaResult.criteriaGrr10Acceptable10') }}</span>
      </template>
    </div>

    <!-- GR&R -->
    <template v-if="isGrr">
      <table class="tb">
        <thead><tr><th>{{ $t('common.source') }}</th><th class="n">{{ $t('msaResult.stdDev') }}</th><th class="n">{{ $t('msaResult.studyVar0', [k]) }}</th><th class="n">{{ $t('msaResult.studyVar') }}</th><th v-if="hasTol" class="n">{{ $t('msaResult.tolerance2') }}</th><th v-if="r.pct_contribution" class="n">{{ $t('msaResult.contribution') }}</th></tr></thead>
        <tbody>
          <tr v-for="c in comps" :key="c.k" :class="{ em: c.k === 'grr' }">
            <td>{{ c.l }}</td><td class="n hm-num">{{ sig(r[c.k]) }}</td><td class="n hm-num">{{ sig(sv(c.k)) }}</td>
            <td class="n hm-num">{{ c.k === 'tv' ? '100.0%' : pct(r['pct_' + c.k]) }}</td>
            <td v-if="hasTol" class="n hm-num">{{ pct(r['pct_tol_' + c.k]) }}</td>
            <td v-if="r.pct_contribution" class="n hm-num">{{ pct(r.pct_contribution[CONTRIB[c.k]]) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="hasTol" class="small t2">{{ $t('msaResult.tolerance3') }} <span class="hm-num">{{ sig(r.tolerance) }}</span>{{ $t('msaResult.tolerance0Tolerance', [k]) }}</p>

      <template v-if="r.table">
        <div class="sub-h">
          <b>{{ $t('msaResult.anovaTable') }}</b>
          <el-radio-group v-if="r.table_reduced" v-model="tview" size="small">
            <el-radio-button value="full">{{ $t('msaResult.fullModel') }}</el-radio-button><el-radio-button value="reduced">{{ $t('msaResult.interactionPooledIntoError') }}</el-radio-button>
          </el-radio-group>
        </div>
        <table class="tb" data-test="msa-anova">
          <thead><tr><th>{{ $t('common.source') }}</th><th class="n">{{ $t('msaResult.df') }}</th><th class="n">SS</th><th class="n">MS</th><th class="n">F</th><th class="n">p</th></tr></thead>
          <tbody>
            <tr v-for="(row, key) in anova" :key="key">
              <td>{{ SRC[key] || key }}</td><td class="n hm-num">{{ row.df }}</td><td class="n hm-num">{{ sig(row.ss) }}</td><td class="n hm-num">{{ sig(row.ms) }}</td>
              <td class="n hm-num">{{ row.f != null ? fmtNum(row.f, 3) : '' }}</td><td class="n hm-num">{{ pv(row.p) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="r.pooled" class="note">{{ $t('msaResult.interactionNotSignificantP') }} <span class="hm-num">{{ pv(r.interaction_p) }}</span> {{ $t('msaResult.n0PooledIntoRepeatabilityEquipment', [r.alpha_interaction]) }}</p>
      </template>
    </template>

    <!-- Bias -->
    <template v-else-if="method === 'bias'">
      <div class="kg">
        <div v-for="x in biasKv" :key="x[0]" class="kc"><span class="l">{{ x[0] }}</span><span class="hm-num v">{{ x[1] }}</span></div>
      </div>
      <p class="note">{{ $t('msaResult.n95ConfidenceInterval') }} <span class="hm-num">{{ sig(r.ci_low) }} … {{ sig(r.ci_high) }}</span>{{ $t('msaResult.n0', [r.acceptable ? $t('msaResult.includes0BiasIsNot') : $t('msaResult.excludes0BiasIsSignificant')]) }}</p>
    </template>

    <!-- Linearity -->
    <template v-else-if="method === 'linearity'">
      <div class="kg">
        <div v-for="x in linKv" :key="x[0]" class="kc"><span class="l">{{ x[0] }}</span><span class="hm-num v">{{ x[1] }}</span></div>
      </div>
      <p class="note">{{ $t('msaResult.slope0Intercept1T', [r.slope_significant ? $t('msaResult.significant') : $t('msaResult.notSignificant'), r.intercept_significant ? $t('msaResult.significant') : $t('msaResult.notSignificant')]) }} <span class="hm-num">{{ fmtNum(r.t_crit, 3) }}</span>{{ $t('msaResult.theBias0LineIs', [r.zero_line_within_bands ? $t('msaResult.within') : $t('msaResult.notEntirelyWithin'), r.acceptable ? $t('msaResult.linearityAcceptable') : $t('msaResult.linearityUnacceptable')]) }}</p>
      <Chart :option="linOpt" :height="300" :ai="$t('msaResult.linearityStudyBiasVsReference')" data-test="msa-lin-chart" />
      <table class="tb">
        <thead><tr><th class="n">{{ $t('msaResult.referenceValue') }}</th><th class="n">{{ $t('msaResult.reading') }}</th><th class="n">{{ $t('msaResult.mean') }}</th><th class="n">{{ $t('msaResult.meanBias') }}</th></tr></thead>
        <tbody><tr v-for="p in r.per_part || []" :key="p.reference"><td class="n hm-num">{{ sig(p.reference) }}</td><td class="n hm-num">{{ p.n }}</td><td class="n hm-num">{{ sig(p.mean) }}</td><td class="n hm-num">{{ sig(p.bias) }}</td></tr></tbody>
      </table>
    </template>

    <!-- Attribute -->
    <template v-else-if="method === 'attribute'">
      <div class="kg">
        <div class="kc"><span class="l">{{ $t('msaResult.betweenAppraisers') }}</span><span class="hm-num v">{{ pct(r.between_appraisers?.pct) }}</span></div>
        <div v-if="r.all_vs_standard" class="kc"><span class="l">{{ $t('msaResult.allAppraisersVsStandard') }}</span><span class="hm-num v">{{ pct(r.all_vs_standard.pct) }}</span></div>
        <div class="kc"><span class="l">Fleiss kappa</span><span class="hm-num v">{{ fmtNum(r.fleiss_kappa, 3) }}</span></div>
        <div class="kc"><span class="l">{{ $t('msaResult.appraisersTrialsParts') }}</span><span class="hm-num v">{{ r.n_appraisers }} × {{ r.n_trials }} × {{ r.n_parts }}</span></div>
      </div>
      <div class="sub-h"><b>{{ $t('msaResult.appraiser') }}</b></div>
      <table class="tb">
        <thead><tr><th>{{ $t('msaResult.appraiser') }}</th><th class="n">{{ $t('msaResult.withinAppraiser') }}</th><th class="n">{{ $t('msaResult.withinKappa') }}</th><th v-if="r.each_vs_standard" class="n">{{ $t('msaResult.vsStandard') }}</th><th v-if="r.kappa_vs_reference" class="n">{{ $t('msaResult.kappaVsStandard') }}</th>
          <th v-if="r.rates" class="n">{{ $t('msaResult.effectiveness') }}</th><th v-if="r.rates" class="n">{{ $t('msaResult.missRate') }}</th><th v-if="r.rates" class="n">{{ $t('msaResult.falseAlarmRate') }}</th><th v-if="r.rates">{{ $t('common.conclusion') }}</th></tr></thead>
        <tbody>
          <tr v-for="a in r.appraisers || []" :key="a">
            <td>{{ a }}</td>
            <td class="n hm-num">{{ pct(r.within_appraiser?.[a]?.pct) }}</td><td class="n hm-num">{{ fmtNum(r.within_kappa?.[a], 3) }}</td>
            <td v-if="r.each_vs_standard" class="n hm-num">{{ pct(r.each_vs_standard[a]?.pct) }}</td>
            <td v-if="r.kappa_vs_reference" class="n hm-num">{{ fmtNum(r.kappa_vs_reference[a]?.kappa, 3) }} <span class="t2">{{ KR[r.kappa_vs_reference[a]?.rating] || '' }}</span></td>
            <template v-if="r.rates">
              <td class="n hm-num">{{ pct(r.rates[a]?.effectiveness) }}</td>
              <td class="n hm-num">{{ pct(r.rates[a]?.miss_rate) }}</td><td class="n hm-num">{{ pct(r.rates[a]?.false_alarm_rate) }}</td>
              <td><HmTag :tone="VT[r.rates[a]?.verdict]?.[1] || 'neutral'">{{ VT[r.rates[a]?.verdict]?.[0] || '—' }}</HmTag></td>
            </template>
          </tr>
        </tbody>
      </table>
      <p v-if="r.rates" class="small t2">{{ $t('msaResult.effectiveness90MissRate2') }}</p>
      <template v-if="(r.kappa_pairs || []).length">
        <div class="sub-h"><b>{{ $t('msaResult.pairwiseAppraiserKappa') }}</b></div>
        <table class="tb">
          <thead><tr><th>{{ $t('msaResult.appraiser') }}</th><th class="n">kappa</th><th class="n">{{ $t('msaResult.agreement') }}</th><th>{{ $t('msaResult.rating') }}</th></tr></thead>
          <tbody><tr v-for="p in r.kappa_pairs" :key="p.a + p.b"><td>{{ p.a }} × {{ p.b }}</td><td class="n hm-num">{{ fmtNum(p.kappa, 3) }}</td><td class="n hm-num">{{ pct((p.po ?? 0) * 100) }}</td><td>{{ KR[p.rating] || '—' }}</td></tr></tbody>
        </table>
      </template>
    </template>

    <ul v-if="notes.length" class="notes">
      <li v-for="(n, i) in notes" :key="i">{{ n }}</li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// MSA result panel: GR&R (average & range / ANOVA), bias, linearity (with bias vs reference chart), attribute agreement. Data from msa_study.result.
import { computed, ref } from 'vue';
import Chart from '../Chart.vue';
import { CH, fmtNum } from '../../design';

const props = defineProps<{ method: string; result: any; verdict?: string | null; data?: any }>();
const r = computed(() => props.result || {});
const isGrr = computed(() => props.method === 'grr_xbar' || props.method === 'grr_anova');
const vkey = computed(() => ({ marginal: 'conditional' } as any)[props.verdict || r.value.verdict] || props.verdict || r.value.verdict);
const VT: Record<string, [string, any]> = { acceptable: [$t('common.acceptable'), 'ok'], conditional: [$t('common.conditionallyAcceptable'), 'warn'], marginal: [$t('common.conditionallyAcceptable'), 'warn'], unacceptable: [$t('common.unacceptable'), 'bad'] };
const KR: Record<string, string> = { good: $t('msaResult.good'), marginal: $t('common.marginal'), poor: $t('msaResult.poor') };
const SRC: Record<string, string> = { appraiser: $t('msaResult.appraiser'), part: $t('common.part'), interaction: $t('msaResult.appraiserPart'), repeatability: $t('msaResult.repeatabilityEquipment'), total: $t('msaResult.total') };
const CONTRIB: Record<string, string> = { ev: 'repeatability', av: 'reproducibility', int: 'interaction', grr: 'grr', pv: 'part', tv: 'total' };
const k = computed(() => r.value.k_sigma || 6);
const hasTol = computed(() => r.value.pct_tol_grr != null);
const tview = ref<'full' | 'reduced'>('full');
const anova = computed(() => (tview.value === 'reduced' && r.value.table_reduced) || r.value.table || {});
const comps = computed(() => [
  { k: 'ev', l: $t('msaResult.repeatabilityEvEquipment') }, { k: 'av', l: $t('msaResult.reproducibilityAvAppraiser') },
  ...(r.value.int ? [{ k: 'int', l: $t('msaResult.interactionInt') }] : []),
  { k: 'grr', l: 'GRR' }, { k: 'pv', l: $t('msaResult.partToPartPv') }, { k: 'tv', l: $t('msaResult.totalVariationTv') },
]);
const sv = (key: string) => r.value.study_var?.[key] ?? (r.value[key] != null ? r.value[key] * k.value : null);

/** Significant digits (MSA variance components are often ~1e-6, fixed decimals don't work) */
function sig(v: any, n = 4) {
  if (v === null || v === undefined || v === '' || !isFinite(Number(v))) return '—';
  const x = Number(v);
  if (x === 0) return '0';
  if (Math.abs(x) < 1e-3 || Math.abs(x) >= 1e6) return x.toExponential(n - 1);
  return String(Number(x.toPrecision(n)));
}
const pct = (v: any) => (v === null || v === undefined || !isFinite(Number(v)) ? '—' : fmtNum(Number(v), 1) + '%');
const pv = (p: any) => (p === null || p === undefined ? '' : Number(p) < 0.001 ? '< 0.001' : fmtNum(Number(p), 3));
const cls = (v: any) => (v == null ? '' : v > 30 ? 'bad' : v >= 10 ? 'warn' : 'good');

const biasKv = computed(() => [
  [$t('msaResult.referenceValue'), sig(r.value.reference)], [$t('msaResult.reading'), String(r.value.n ?? '—')], [$t('msaResult.mean2'), sig(r.value.mean)], [$t('msaResult.bias'), sig(r.value.bias)],
  [$t('msaResult.repeatabilityR'), sig(r.value.sr)], ['σb', sig(r.value.sb)], [$t('msaResult.tStatistic'), sig(r.value.t)], [$t('msaResult.df'), String(r.value.df ?? '—')],
  [$t('msaResult.pValue'), pv(r.value.p)], [$t('msaResult.tCritical'), sig(r.value.t_crit)], ...(r.value.pct_bias_tol != null ? [[$t('msaResult.biasTolerance'), pct(r.value.pct_bias_tol)]] : []),
]);
const linKv = computed(() => [
  [$t('msaResult.slopeA'), sig(r.value.slope)], [$t('msaResult.interceptB'), sig(r.value.intercept)], ['R²', sig(r.value.r2, 3)], ['s', sig(r.value.s)],
  [$t('msaResult.tSlope'), sig(r.value.t_slope)], [$t('msaResult.tIntercept'), sig(r.value.t_intercept)], [$t('msaResult.pSlope'), pv(r.value.p_slope)], [$t('msaResult.pIntercept'), pv(r.value.p_intercept)],
  [$t('msaResult.linearity'), pct(r.value.pct_linearity)],
]);
const linOpt = computed(() => {
  const parts = props.data?.parts || [];
  const pts = parts.flatMap((p: any) => (p.readings || []).map((x: any) => [Number(p.reference), Number(x) - Number(p.reference)]));
  const bands = r.value.bands || [];
  const series: any[] = [
    { name: $t('msaResult.individualBias'), type: 'scatter', data: pts, symbolSize: 6, itemStyle: { color: CH.cyan, opacity: 0.75 } },
    { name: $t('msaResult.meanBias'), type: 'scatter', data: (r.value.per_part || []).map((p: any) => [p.reference, p.bias]), symbol: 'diamond', symbolSize: 11, itemStyle: { color: CH.amber } },
    { name: $t('msaResult.regressionLine'), type: 'line', data: bands.map((b: any) => [b.x, b.fit]), showSymbol: false, lineStyle: { color: CH.primary, width: 2 },
      markLine: { silent: true, symbol: 'none', data: [{ yAxis: 0, ...{ name: $t('msaResult.bias0'), lineStyle: { color: CH.ref, type: 'dashed', width: 1 }, label: { formatter: $t('msaResult.bias0'), position: 'insideEndTop', color: CH.text2, fontSize: 11 } } }] } },
  ];
  if (bands.length) {
    series.push({ name: $t('msaResult.n95ConfidenceBand'), type: 'line', data: bands.map((b: any) => [b.x, b.upper]), showSymbol: false, lineStyle: { color: CH.violet, type: 'dashed', width: 1 } });
    series.push({ name: $t('msaResult.n95ConfidenceBand2'), type: 'line', data: bands.map((b: any) => [b.x, b.lower]), showSymbol: false, lineStyle: { color: CH.violet, type: 'dashed', width: 1 } });
  }
  return {
    tooltip: { trigger: 'item', valueFormatter: (v: any) => sig(v) },
    legend: { top: 0, data: [$t('msaResult.individualBias'), $t('msaResult.meanBias'), $t('msaResult.regressionLine'), $t('msaResult.n95ConfidenceBand')] },
    grid: { top: 36, left: 8, right: 24, bottom: 8 },
    xAxis: { type: 'value', name: $t('msaResult.referenceValue'), scale: true }, yAxis: { type: 'value', name: $t('msaResult.bias') },
    series,
  };
});

const NOTE_CN: [RegExp, (m: RegExpMatchArray) => string][] = [
  [/^ndc < 5/, () => $t('msaResult.ndc5TheMeasurementSystem')],
  [/^interaction p = ([\d.e-]+) > ([\d.]+)/, () => ''],
  [/^AV term under the root was negative/, () => $t('msaResult.negativeValueUnderTheAv')],
  [/^(\d+) range\(s\) outside the R-chart limits/, (m) => $t('msaResult.n0RangesExceedTheR', [m[1]])],
];
const notes = computed(() => (r.value.notes || []).map((n: string) => {
  for (const [re, f] of NOTE_CN) { const m = n.match(re); if (m) return f(m); }
  return n;
}).filter((x: string) => x));
</script>

<style scoped>
.mr { display: flex; flex-direction: column; gap: var(--hm-sp-2); }
.vd { display: flex; align-items: center; gap: 8px 18px; flex-wrap: wrap; }
.kv { font-size: var(--hm-fs-sm); color: var(--hm-text-2); }
.kv b { font-size: var(--hm-fs-xl); margin-left: 4px; color: var(--hm-text); }
.good { color: var(--hm-run-ink) !important; } .warn { color: var(--hm-idle-ink) !important; } .bad { color: var(--hm-fault-ink) !important; }
.tb { width: 100%; border-collapse: collapse; font-size: var(--hm-fs-xs); }
.tb th, .tb td { padding: 5px 8px; border-bottom: 1px solid var(--hm-hairline); text-align: left; white-space: nowrap; }
.tb th { background: var(--hm-surface-2); color: var(--hm-text-2); font-weight: 600; }
.tb .n { text-align: right; }
.tb tr.em td { font-weight: 600; background: var(--hm-primary-soft); color: var(--hm-text); }
.sub-h { display: flex; align-items: center; gap: 12px; margin-top: var(--hm-sp-2); font-size: var(--hm-fs-sm); }
.kg { display: grid; grid-template-columns: repeat(auto-fill, minmax(calc(120px * var(--hm-scale)), 1fr)); gap: 8px; }
.kc { display: flex; flex-direction: column; padding: 6px 10px; border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); background: var(--hm-surface); }
.kc .l { font-size: var(--hm-fs-2xs); color: var(--hm-text-2); }
.kc .v { font-size: var(--hm-fs-lg); font-weight: 600; color: var(--hm-text); }
.note { margin: 0; font-size: var(--hm-fs-xs); color: var(--hm-text-2); }
.notes { margin: 0; padding: 8px 12px 8px 26px; background: var(--hm-idle-soft); border-radius: var(--hm-r-md); font-size: var(--hm-fs-xs); color: var(--hm-text); }
.t2 { color: var(--hm-text-2); } .small { font-size: var(--hm-fs-2xs); }
</style>
