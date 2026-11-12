<template>
  <div class="page daily">
    <div class="page-head no-print"><h2>{{ $t('common.productionDailyReport') }}</h2><span class="sub">{{ $t('daily.dataIsAggregatedAutomaticallyWith') }}</span><span class="grow" />
      <el-date-picker v-model="day" type="date" value-format="YYYY-MM-DD" :placeholder="$t('common.today')" style="width: 140px" :clearable="false" @change="load" />
      <el-select v-model="day" :placeholder="$t('daily.previous')" style="width: 120px" @change="load"><el-option v-for="d in days" :key="d.day" :label="d.day.slice(5) + (d.by === 'rule' ? '' : ' · AI')" :value="d.day" /></el-select>
      <el-button :loading="busy === 'rule'" data-test="daily-gen-rule" @click="gen(false)"><el-icon><Refresh /></el-icon>{{ $t('daily.regenerate') }}</el-button>
      <el-button v-if="aiOn('daily')" type="primary" :loading="busy === 'ai'" data-test="daily-gen-ai" @click="gen(true)"><el-icon><MagicStick /></el-icon>{{ $t('automations.aiGenerated') }}</el-button>
      <el-button :loading="busy === 'pdf'" data-test="daily-pdf" @click="pdf"><el-icon><Download /></el-icon>{{ $t('daily.exportPdf') }}</el-button>
      <el-popover placement="bottom-end" :width="280" trigger="click">
        <template #reference><el-button :type="sched.enabled ? 'success' : ''" plain data-test="daily-sched"><el-icon><AlarmClock /></el-icon>{{ sched.enabled ? $t('daily.dailyAt0', [sched.time]) : $t('common.scheduled') }}</el-button></template>
        <div class="sched">
          <div class="sr"><span>{{ $t('daily.generateAutomaticallyEveryDay') }}</span><el-switch v-model="sched.enabled" :disabled="!canSched" /></div>
          <div class="sr"><span>{{ $t('daily.timeDataClock') }}</span><el-time-select v-model="sched.time" start="00:00" step="00:10" end="23:50" size="small" style="width: 110px" :disabled="!canSched" /></div>
          <div class="sr"><span>{{ $t('daily.reportFor') }}</span><el-radio-group v-model="sched.target" size="small" :disabled="!canSched"><el-radio-button value="today">{{ $t('daily.sameDay') }}</el-radio-button><el-radio-button value="yesterday">{{ $t('daily.previousDay') }}</el-radio-button></el-radio-group></div>
          <p class="muted small">{{ aiOn('daily') ? $t('daily.aiDailyReportIsOn') : $t('daily.aiDailyReportIsOff') }}{{ canSched ? '' : $t('daily.onlyAdministratorsPlantManagersCan') }}</p>
          <el-button size="small" type="primary" :disabled="!canSched" @click="saveSched">{{ $t('common.save') }}</el-button>
        </div>
      </el-popover>
    </div>

    <HmSkeleton v-if="!r" variant="kpi" />
    <article v-else class="mag" data-test="daily-report">
      <header class="mast">
        <div class="brand"><span class="logo" aria-hidden="true" />{{ $t('common.honeymom') }} <i>·</i> {{ $t('common.productionDailyReport') }}</div>
        <div class="dt"><b class="hm-num">{{ r.day.slice(5).replace('-', ' / ') }}</b><span>{{ r.day.slice(0, 4) }} · {{ weekday }}</span></div>
        <div class="by"><span class="tag" :class="r.by" data-test="daily-by">{{ BY[r.by] || r.by }}</span>
          <span class="muted">{{ r.generated_at ? $t('daily.generatedAt') + r.generated_at.slice(5, 16) : $t('daily.livePreviewNotSaved') }}</span></div>
      </header>

      <section class="hero">
        <h1 v-ai="{ kind: 'text', label: r.headline, data: { [$t('daily.dailyReport')]: r.day } }">{{ r.headline }}</h1>
        <p class="lede">{{ r.summary }}</p>
        <p v-if="r.by === 'demo' && r.demo_stale" class="aihid" data-test="daily-demo-note">{{ $t('daily.demoModeThisAiInsight') }}</p>
        <p v-if="r.ai_hidden" class="aihid">{{ $t('daily.thisReportOriginallyHadAi') }}</p>
      </section>

      <section class="kpis">
        <HmKpi v-for="k in r.kpis" :key="k.key" :label="k.label" :value="k.value" :unit="k.format === 'pct' ? '' : k.unit" :format="k.format" :delta="k.delta" :delta-pct="k.deltaPct"
          :good-when="k.good" :delta-label="r.has_prev ? $t('daily.vsPreviousDay') : ''" :ai="{ data: { [$t('common.date')]: r.day, [$t('common.value')]: k.value, [$t('daily.previousDay')]: k.prev } }" compact />
      </section>

      <section class="row r1">
        <div class="pane wide">
          <div class="ph"><h3>{{ $t('daily.outputRhythm') }}</h3><span class="muted small">{{ $t('daily.partsPerHourRedBars') }}</span></div>
          <Chart :option="outOpt" :height="250" :ai="$t('daily.dailyReportHourlyOutput')" />
        </div>
        <div class="pane notes">
          <div class="nb ok"><h3><span class="ic">▲</span>{{ $t('daily.highlights') }}</h3>
            <ul><li v-for="(h, i) in r.highlights" :key="i">{{ h.text }}</li><li v-if="!r.highlights?.length" class="muted">—</li></ul></div>
          <div class="nb risk"><h3><span class="ic">!</span>{{ $t('daily.risks') }}</h3>
            <ul><li v-for="(h, i) in r.risks" :key="i" :class="h.severity">{{ h.text }}</li><li v-if="!r.risks?.length" class="muted">{{ $t('daily.noNotableRisks') }}</li></ul></div>
        </div>
      </section>

      <section class="row r2">
        <div class="pane"><div class="ph"><h3>{{ $t('daily.oeeByMachine') }}</h3><span class="muted small">{{ $t('daily.lowToHighDashedLine') }}</span></div>
          <Chart :option="oeeOpt" :height="Math.max(180, 26 * r.charts.oee.length + 30)" :ai="$t('daily.dailyReportMachineOee')" /></div>
        <div class="pane"><div class="ph"><h3>{{ $t('daily.defectPareto') }}</h3><span class="muted small">{{ $t('daily.countCumulativeShare') }}</span></div>
          <Chart v-if="r.charts.pareto.length" :option="parOpt" :height="230" :ai="$t('daily.dailyReportDefectPareto')" /><HmEmpty v-else compact icon="data" :title="$t('daily.noNonconformancesThatDay')" /></div>
        <div class="pane"><div class="ph"><h3>{{ $t('daily.topDowntimeIdle') }}</h3><span class="muted small">{{ $t('common.min2') }}</span></div>
          <Chart v-if="r.charts.downtime.length" :option="dtOpt" :height="Math.max(180, 26 * r.charts.downtime.length + 40)" :ai="$t('daily.dailyReportNonRunningTime')" /><HmEmpty v-else compact icon="data" :title="$t('daily.noDowntimeRecords')" /></div>
      </section>

      <section class="row r3">
        <div class="pane"><div class="ph"><h3>{{ $t('daily.energy') }}</h3><span class="muted small">{{ $t('daily.kwhHourMelted0Heats', [r.facts.heats, r.facts.melt_t]) }}</span></div>
          <Chart :option="enOpt" :height="200" :ai="$t('daily.dailyReportHourlyPower')" /></div>
        <div class="pane acts">
          <div class="ph"><h3>{{ $t('daily.recommendedActions') }}</h3><span v-if="r.by !== 'rule'" class="aitag">AI</span></div>
          <ol>
            <li v-for="(a, i) in r.actions" :key="i">
              <span>{{ a.text }}</span>
              <span class="aa no-print">
                <el-button v-if="a.link" link size="small" @click="router.push(a.link)">{{ $t('daily.view') }}</el-button>
                <el-button v-if="a.op && aiOn('ask') && !drafts[i]" size="small" plain type="primary" :loading="dbusy[i]" @click="draft(a, i)">{{ $t('common.draft2') }}</el-button>
              </span>
              <DraftCard v-if="drafts[i]" :draft="drafts[i]" class="dft" />
            </li>
            <li v-if="!r.actions?.length" class="muted">—</li>
          </ol>
        </div>
      </section>
      <footer class="foot">{{ $t('daily.dataFromHoneymomDataClock', [r.by === 'rule' ? $t('daily.highlightsRisksRecommendationsAreRul') : $t('daily.numbersInTheAiInsight'), r.facts.wo_open, r.facts.ncr_open]) }}</footer>
    </article>
  </div>
</template>

<script setup lang="ts">
import { $t, lang, intlLocale } from '../i18n';
// Magazine-style production daily report. With AI off it can still be viewed and generated (rule-based); only the "AI Generate" button and AI insight need "AI Daily Report". Backend services/ai_daily.py.
import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { aiOn, toast } from '../ai';
import DraftCard from '../ai/DraftCard.vue';
import { get, post, put, session } from '../api';
import Chart from '../components/Chart.vue';
import { CH, PALETTE, targetLine } from '../design/chart';

const router = useRouter();
const BY: Record<string, string> = { ai: $t('aiExplain.aiInsight'), demo: $t('daily.aiInsightDemoMode'), rule: $t('daily.ruleBased') };
const day = ref(''), r = ref<any>(null), days = ref<any[]>([]), busy = ref(''), drafts = reactive<Record<number, any>>({}), dbusy = reactive<Record<number, boolean>>({});
const sched = reactive<any>({ enabled: false, time: '23:50', target: 'today' });
const canSched = computed(() => (session.user?.role_list || []).some((x: string) => x === 'admin' || x === 'manager'));
const weekday = computed(() => (r.value ? new Date(r.value.day + 'T00:00:00').toLocaleDateString(intlLocale, { weekday: 'long' }) : ''));

async function load() {
  r.value = null;
  Object.keys(drafts).forEach((k) => delete drafts[+k]);
  r.value = await get('/api/report/daily' + (day.value ? `?day=${day.value}` : ''));
  if (!day.value) day.value = r.value.day;
}
async function loadDays() { days.value = await get('/api/report/daily/days').catch(() => []); }
async function gen(ai: boolean) {
  busy.value = ai ? 'ai' : 'rule';
  try {
    const res = await post(ai ? '/api/ai/daily' : '/api/report/daily/generate', { day: day.value || undefined });
    r.value = res.report; await loadDays();
    toast(ai ? (res.by === 'rule' ? $t('daily.aiIsTemporarilyUnavailableGenerated') : $t('daily.generatedWithAiInsight')) : $t('daily.regeneratedByRules'), 'success');
  } finally { busy.value = ''; }
}
async function pdf() {
  busy.value = 'pdf';
  try {
    const res = await fetch(`/api/report/daily/pdf?day=${r.value?.day || ''}`, { headers: { 'X-Lang': lang, Authorization: 'Bearer ' + session.token } });
    if (res.status === 501) { toast($t('daily.noBrowserIsAvailableOn'), 'info', 3000); setTimeout(() => window.print(), 300); return; }
    if (!res.ok) throw new Error(res.statusText);
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement('a'); a.href = url; a.download = $t('daily.productionDailyReport0Pdf', [r.value.day]); document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    toast($t('daily.pdfExported'), 'success');
  } catch (e: any) { toast($t('daily.exportFailed') + (e.message || e), 'error'); } finally { busy.value = ''; }
}
async function draft(a: any, i: number) {
  dbusy[i] = true;
  try { drafts[i] = await post('/api/ai/drafts', { op: a.op, payload: a.payload, source: 'daily', summary: $t('daily.dailyReport0Recommendation1', [r.value.day, a.text]) }); } finally { dbusy[i] = false; }
}
async function saveSched() { Object.assign(sched, await put('/api/report/daily/schedule', sched)); toast($t('common.saved'), 'success'); }

// ---------------- Charts ----------------
const C = computed(() => r.value?.charts || {});
const outOpt = computed(() => {
  const o = C.value.output || { x: [], series: [] };
  const s = o.series || [];
  return {
    tooltip: { trigger: 'axis' }, legend: { top: 0, left: 0 }, grid: { left: 4, right: 8, top: 34, bottom: 4, containLabel: true },
    xAxis: { type: 'category', data: o.x, boundaryGap: false }, yAxis: [{ type: 'value', minInterval: 1 }, { type: 'value', splitLine: { show: false }, minInterval: 1 }],
    series: [
      ...s.slice(0, 3).map((x: any, i: number) => ({ name: x.name, type: 'line', smooth: true, showSymbol: false, data: x.data, lineStyle: { width: 2 }, itemStyle: { color: PALETTE[i] },
        areaStyle: i === 1 ? { opacity: 0.08 } : undefined })),
      ...(s[3] ? [{ name: s[3].name, type: 'bar', yAxisIndex: 1, data: s[3].data, barMaxWidth: 8, itemStyle: { color: CH.bad, opacity: 0.55, borderRadius: [2, 2, 0, 0] } }] : []),
    ],
  };
});
const oeeOpt = computed(() => {
  const d = C.value.oee || [];
  return {
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => (v * 100).toFixed(1) + '%' }, grid: { left: 4, right: 40, top: 6, bottom: 4, containLabel: true },
    xAxis: { type: 'value', max: 1, axisLabel: { formatter: (v: number) => v * 100 + '%' } }, yAxis: { type: 'category', data: d.map((x: any) => x.code), axisLabel: { interval: 0 } },
    series: [{ type: 'bar', name: 'OEE', barMaxWidth: 14, data: d.map((x: any) => ({ value: x.OEE, itemStyle: { color: x.OEE < 0.6 ? CH.bad : x.OEE < 0.75 ? CH.warn : CH.ok, borderRadius: [0, 3, 3, 0] } })),
      label: { show: true, position: 'right', fontSize: 11, formatter: (p: any) => (p.value * 100).toFixed(0) + '%' },
      markLine: { symbol: 'none', data: [{ xAxis: 0.85, ...targetLine('85%') }] } }],
  };
});
const parOpt = computed(() => {
  const d = C.value.pareto || [];
  return {
    tooltip: { trigger: 'axis' }, grid: { left: 4, right: 4, top: 24, bottom: 4, containLabel: true },
    xAxis: { type: 'category', data: d.map((x: any) => x.name), axisLabel: { interval: 0, width: 64, overflow: 'truncate' } },
    yAxis: [{ type: 'value', minInterval: 1 }, { type: 'value', max: 1, splitLine: { show: false }, axisLabel: { formatter: (v: number) => v * 100 + '%' } }],
    series: [{ name: $t('common.parts'), type: 'bar', data: d.map((x: any) => x.n), barMaxWidth: 26, itemStyle: { color: CH.amber, borderRadius: [3, 3, 0, 0] } },
      { name: $t('daily.cumulative'), type: 'line', yAxisIndex: 1, data: d.map((x: any) => x.cum), symbolSize: 5, itemStyle: { color: CH.text2 }, lineStyle: { width: 1.5 },
        tooltip: { valueFormatter: (v: number) => (v * 100).toFixed(0) + '%' } }],
  };
});
const dtOpt = computed(() => {
  const d = C.value.downtime || [];
  const mk = (k: string, name: string, color: string) => ({ name, type: 'bar', stack: 't', data: d.map((x: any) => x[k]), barMaxWidth: 14, itemStyle: { color } });
  return {
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => v.toFixed(0) + $t('common.min') }, legend: { top: 0, right: 0 }, grid: { left: 4, right: 10, top: 26, bottom: 4, containLabel: true },
    xAxis: { type: 'value' }, yAxis: { type: 'category', inverse: true, data: d.map((x: any) => x.code), axisLabel: { interval: 0 } },
    series: [mk('stop', $t('common.down'), CH.bad), mk('setup', $t('common.changeover'), CH.primary), mk('idle', $t('common.idle'), CH.warn)],
  };
});
const enOpt = computed(() => {
  const e = C.value.energy || { x: [], kwh: [] };
  return { tooltip: { trigger: 'axis', valueFormatter: (v: number) => v.toFixed(0) + ' kWh' }, grid: { left: 4, right: 8, top: 10, bottom: 4, containLabel: true },
    xAxis: { type: 'category', data: e.x }, yAxis: { type: 'value' },
    series: [{ type: 'bar', name: $t('daily.power'), data: e.kwh, barMaxWidth: 12, itemStyle: { color: CH.cyan, borderRadius: [2, 2, 0, 0] } }] };
});

onMounted(async () => { await Promise.all([load(), loadDays()]); Object.assign(sched, await get('/api/report/daily/schedule').catch(() => ({}))); });
</script>

<style scoped>
.mag { background: var(--hm-surface); border: 1px solid var(--hm-border); border-radius: var(--hm-r-lg); box-shadow: var(--hm-e1); padding: 22px 26px 18px; max-width: 1480px; }
.mast { display: grid; grid-template-columns: 1fr auto 1fr; align-items: end; gap: 12px; padding-bottom: 10px; border-bottom: 2px solid var(--hm-text); }
.brand { font-size: var(--hm-fs-sm); letter-spacing: .14em; font-weight: var(--hm-fw-semibold); color: var(--hm-text-2); display: flex; align-items: center; gap: 8px; }
.brand i { font-style: normal; color: var(--hm-amber-ink); }
.logo { width: 14px; height: 14px; background: var(--hm-amber); clip-path: polygon(25% 5%, 75% 5%, 100% 50%, 75% 95%, 25% 95%, 0 50%); }
.dt { text-align: center; line-height: 1.1; } .dt b { display: block; font-size: 30px; letter-spacing: .02em; font-family: var(--hm-font-num); }
.dt span { font-size: var(--hm-fs-xs); color: var(--hm-text-3); letter-spacing: .1em; }
.by { justify-self: end; display: flex; flex-direction: column; align-items: flex-end; gap: 3px; font-size: var(--hm-fs-2xs); }
.tag { font-size: 11px; font-weight: var(--hm-fw-semibold); border-radius: var(--hm-r-pill); padding: 1px 10px; background: var(--hm-surface-3); color: var(--hm-text-2); }
.tag.ai, .tag.demo { background: var(--hm-ai-grad); color: #fff; }
.hero { padding: 18px 0 14px; border-bottom: 1px solid var(--hm-border); }
.hero h1 { margin: 0; font-size: 26px; line-height: 1.35; font-weight: 700; letter-spacing: .01em; color: var(--hm-text); max-width: 1100px; }
.lede { margin: 8px 0 0; font-size: var(--hm-fs-md); color: var(--hm-text-2); line-height: 1.7; max-width: 980px; }
.aihid { margin: 6px 0 0; font-size: var(--hm-fs-xs); color: var(--hm-idle-ink); }
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; padding: 14px 0; border-bottom: 1px solid var(--hm-border); }
@media (min-width: 1960px) { .kpis { grid-template-columns: repeat(8, minmax(0, 1fr)); } }
.row { display: grid; gap: 16px; padding: 16px 0; border-bottom: 1px solid var(--hm-border); }
.r1 { grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr); } .r2 { grid-template-columns: repeat(3, minmax(0, 1fr)); } .r3 { grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr); border-bottom: 0; }
@media (max-width: 1200px) { .r1, .r2, .r3 { grid-template-columns: 1fr; } }
.pane { min-width: 0; } .ph { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; }
.ph h3, .nb h3 { margin: 0; font-size: var(--hm-fs-md); font-weight: 700; letter-spacing: .02em; }
.notes { display: flex; flex-direction: column; gap: 12px; }
.nb { border-radius: var(--hm-r-md); padding: 10px 12px; }
.nb.ok { background: var(--hm-run-soft); } .nb.ok h3 { color: var(--hm-run-ink); } .nb.risk { background: var(--hm-fault-soft); } .nb.risk h3 { color: var(--hm-fault-ink); }
.ic { display: inline-flex; width: 16px; height: 16px; border-radius: 50%; align-items: center; justify-content: center; font-size: 9px; color: #fff; margin-right: 6px; vertical-align: 2px; }
.ok .ic { background: var(--hm-run); } .risk .ic { background: var(--hm-fault); }
.nb ul { margin: 6px 0 0; padding-left: 18px; } .nb li { font-size: var(--hm-fs-sm); line-height: 1.6; margin: 3px 0; color: var(--hm-text); }
.nb li.critical { font-weight: var(--hm-fw-semibold); }
.acts ol { margin: 0; padding-left: 20px; } .acts li { font-size: var(--hm-fs-sm); line-height: 1.6; margin: 6px 0; }
.acts li::marker { color: var(--hm-primary); font-weight: 700; font-family: var(--hm-font-num); }
.aa { margin-left: 6px; } .dft { margin-top: 6px; }
.aitag { font-size: 10px; font-weight: 700; color: #fff; background: var(--hm-ai-grad); border-radius: var(--hm-r-pill); padding: 0 6px; line-height: 16px; }
.foot { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); padding-top: 10px; border-top: 1px solid var(--hm-hairline); }
.sched { display: flex; flex-direction: column; gap: 10px; } .sr { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: var(--hm-fs-xs); }
@media print {
  .no-print { display: none !important; }
  .mag { border: 0; box-shadow: none; padding: 0; max-width: none; }
  .row { break-inside: avoid; }
}
</style>

<style>
/* Hide the shell (top bar, menu, AI panel, status bar) when printing the daily report */
@media print {
  .hm-top, .hm-nav, .dock, .hm-status, .hm-crumbs, [data-test=breadcrumbs] { display: none !important; }
  .hm-main { overflow: visible !important; background: #fff !important; }
}
</style>
