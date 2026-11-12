<template>
  <div class="page cyc">
    <HmPageHead :title="$t('common.cycleTimeAnalysis')" :sub="$t('cycle.automaticallySplitsEachCycleAnd')">
      <el-select v-model="lineId" size="small" style="width: 150px" data-tour="cyc-line" @change="load">
        <el-option :value="0" :label="$t('common.allLines')" />
        <el-option v-for="l in lines" :key="l.id || 0" :value="l.id" :label="l.name" />
      </el-select>
      <el-segmented v-model="hours" :options="RANGES" data-tour="cyc-range" @change="reload" />
      <el-button size="small" data-test="cyc-settings" @click="openSettings"><el-icon><Setting /></el-icon>{{ $t('common.parameters') }}</el-button>
    </HmPageHead>

    <div class="grid g4" data-tour="cyc-kpis">
      <HmKpi :label="$t('cycle.bottleneckStation')" :value="bn ? bn.equipment.code : '—'" format="text" :hint="bn ? $t('cycle.n0Group1MachinesIn', [bn.group, bn.parallel, fmtNum(bn.eff_cycle_s, 1)]) : $t('cycle.noMotionModelYet')" :loading="!d" />
      <HmKpi :label="$t('cycle.bottleneckAction')" :value="bn?.action?.name || '—'" format="text" :status="(bn?.action?.excess || 0) > 0 ? 'bad' : ''"
        :hint="bn?.action ? $t('cycle.median0S', [fmtNum(bn.action.median, 2)]) + ((bn.action.excess || 0) > 0 ? $t('cycle.n0SSlowerPerCycle', [fmtNum(bn.action.excess, 2)]) : '') : ''" :loading="!d" />
      <HmKpi :label="$t('cycle.bottleneckLoss')" :value="bnLoss?.parts_day ?? null" :unit="$t('cycle.pcsDay')" :digits="1" :status="bnLoss ? 'bad' : ''"
        :hint="bnLoss ? $t('cycle.n0SDayCny1', [fmtNum(bnLoss.lost_s_day, 0), fmtNum(bnLoss.money_year, 0)]) : $t('cycle.noActionsHaveSlowedDown')" :loading="!d" />
      <HmKpi :label="$t('cycle.slowActionAlerts')" :value="alertN" :unit="$t('common.k2360')" :status="alertN ? 'warn' : 'ok'" :hint="$t('cycle.window0', [d?.label || ''])" :loading="!d" />
    </div>

    <div class="grid split mt">
      <HmCard :title="$t('cycle.lineCycleTime')" :sub="takt ? $t('cycle.taktTarget0SCycle', [takt]) : $t('cycle.cycleTimePerPartCycle')" data-tour="cyc-line-chart">
        <HmSkeleton v-if="!d" variant="block" :height="280" />
        <HmEmpty v-else-if="!st.length" :title="$t('cycle.noStationMotionModelsYet')" :desc="$t('cycle.machinesWithActionSignalsAre')" />
        <Chart v-else :option="lineChart" :height="280" :ai="$t('cycle.cycleTimePerPartBy')" @click="(p: any) => pick(st[p.dataIndex]?.equipment.id)" />
      </HmCard>
      <HmCard :title="$t('cycle.slowActionLossRanking')" :sub="$t('cycle.secondsAboveStandardPcsDay')" flush data-tour="cyc-losses">
        <HmDataTable :columns="LCOLS" :rows="d?.losses || []" clickable max-height="300" :empty-text="$t('cycle.noActionsSlowerThanStandard')" :loading="!d" @row-click="(r: any) => pick(r.equipment_id)"
          :ai="(r: any) => ({ kind: 'row', entity: 'equipment', id: r.equipment_id, label: $t('cycle.n012SSlower', [r.equipment, r.name, r.excess_s]), data: r })">
          <template #cell-affects_output="{ row }"><HmTag :tone="row.affects_output ? 'bad' : 'neutral'">{{ row.affects_output ? $t('cycle.affectsOutput') : $t('cycle.potential') }}</HmTag></template>
        </HmDataTable>
      </HmCard>
    </div>

    <HmCard class="mt" :title="$t('common.workstation')" :sub="$t('cycle.clickARowForAction')" flush data-tour="cyc-stations">
      <HmDataTable :columns="SCOLS" :rows="stRows" clickable max-height="360" :loading="!d" :empty-text="$t('cycle.noStationsWithAModel')" @row-click="(r: any) => pick(r.id)"
        :ai="(r: any) => ({ kind: 'row', entity: 'equipment', id: r.id, label: $t('cycle.n0Cycle1S', [r.code, r.cycle_s]), data: r })">
        <template #cell-code="{ row }"><b :class="{ bnc: row.bottleneck }">{{ row.code }}</b><HmTag v-if="row.bottleneck" tone="bad" style="margin-left:6px">{{ $t('coplan.bottleneck2') }}</HmTag></template>
        <template #cell-status="{ row }"><HmTag :tone="row.status === 'confirmed' ? 'ok' : 'amber'">{{ row.status === 'confirmed' ? $t('common.confirmed') : $t('common.draft') }}</HmTag></template>
      </HmDataTable>
      <div v-if="todo.length" class="todo" data-test="cyc-todo">
        <span class="muted">{{ $t('cycle.withActionSignalsNotYet') }}</span>
        <span v-for="e in todo" :key="e.id" class="tdi">{{ e.code }}
          <el-button link type="primary" size="small" :loading="busy === 'p' + e.id" :data-test="'cyc-propose-' + e.code" @click="propose(e.id)">{{ $t('cycle.autoDetectActions') }}</el-button></span>
      </div>
    </HmCard>

    <!-- Single machine: motion details -->
    <section v-if="cur" class="mt drill" data-test="cyc-drill" data-tour="cyc-drill">
      <HmCard :title="`${cur.equipment.code} ${cur.equipment.name}`" :sub="curSub">
        <template #actions>
          <HmTag v-if="cur.model" :tone="cur.model.status === 'confirmed' ? 'ok' : 'amber'">{{ cur.model.status === 'confirmed' ? $t('cycle.confirmedV0', [cur.model.version]) : $t('cycle.draftToConfirm') }}</HmTag>
          <el-button size="small" data-test="cyc-edit" @click="openEditor"><el-icon><EditPen /></el-icon>{{ $t('cycle.motionModel') }}</el-button>
          <el-button v-if="cur.model && cur.model.status !== 'confirmed'" v-perm:disable="'cycle.model.confirm'" size="small" type="primary" data-test="cyc-confirm" @click="confirmModel">{{ $t('cycle.confirmModel') }}</el-button>
          <el-button v-perm:disable="'cycle.model.confirm'" size="small" data-test="cyc-baseline" @click="learnBase">{{ $t('cycle.learnBaseline') }}</el-button>
          <el-button size="small" text @click="cur = null"><el-icon><Close /></el-icon></el-button>
        </template>
        <HmEmpty v-if="!cur.model" :title="$t('cycle.thisMachineHasNoMotion')" :desc="cur.has_actions ? $t('cycle.clickMotionModelAutoDetect') : $t('cycle.noActionSignalsFound')" />
        <template v-else>
          <div v-if="cur.alerts?.length" class="alerts">
            <div v-for="(a, i) in cur.alerts" :key="i" class="al" :class="a.severity" data-test="cyc-alert">
              <el-icon><WarningFilled /></el-icon><span class="at">{{ a.text }}</span><span class="grow" />
              <el-button size="small" link type="primary" :disabled="!aiOn('cycle')" :loading="busy === 'x' + a.action" data-test="cyc-explain" @click="explain(a.action)">{{ $t('cycle.aiExplain') }}</el-button>
              <el-button size="small" link type="primary" data-test="cyc-draft" :loading="busy === 'd' + a.action" @click="draft(a.action)">{{ $t('cycle.draftRepairOrder') }}</el-button>
            </div>
            <div v-if="expl" class="expl" data-test="cyc-expl"><b>{{ $t('common.n02', [expl.by === 'rule' ? $t('cycle.ruleBasedAnalysis') : expl.by === 'demo' ? $t('aiExplain.aiDemoMode') : 'AI']) }}</b>{{ expl.text }}</div>
            <DraftCard v-if="dr" :draft="dr" />
          </div>
          <HmDataTable :columns="ACOLS" :rows="cur.actions" :ai="(r: any) => ({ kind: 'row', entity: 'equipment', id: cur.equipment.id, label: `${cur.equipment.code} ${r.name} ${r.median}s`, data: r })">
            <template #cell-excess="{ row }"><b :class="{ bad: row.excess > 0 }">{{ row.excess > 0 ? '+' + fmtNum(row.excess, 2) : '—' }}</b></template>
          </HmDataTable>
          <div class="grid g2 mt">
            <div>
              <div class="ct"><b>{{ $t('cycle.last0CyclesGantt', [cur.recent.length]) }}</b><el-checkbox v-model="hideLong" size="small">{{ $t('cycle.compressLongActions') }}</el-checkbox></div>
              <div class="gantt" data-test="cyc-gantt" role="img" :aria-label="$t('cycle.n0ActionGanttOfRecent', [cur.equipment.code])">
                <div v-for="(cy, i) in gantt.rows" :key="i" class="gr"><span class="gl">{{ cy.label }}</span>
                  <div class="gb"><i v-for="s in cy.segs" :key="s.key" :style="{ left: s.l + '%', width: s.w + '%', background: s.c }" :title="`${s.name} ${s.dur}s`" :class="{ cut: s.cut }" /></div></div>
                <div class="glg"><span v-for="a in gantt.legend" :key="a.key"><i :style="{ background: a.c }" />{{ a.name }}</span></div>
              </div>
            </div>
            <div>
              <div class="ct"><b>{{ $t('cycle.recentCyclesOverlaidTimePer') }}</b></div>
              <Chart :option="overlay" :height="240" :ai="$t('cycle.n0TimePerActionIn', [cur.equipment.code])" />
            </div>
            <div>
              <div class="ct"><b>{{ $t('cycle.durationDistribution') }}</b><el-select v-model="distKey" size="small" style="width: 170px"><el-option v-for="a in shortActs" :key="a.key" :value="a.key" :label="a.name" /></el-select></div>
              <Chart :option="distChart" :height="220" :ai="$t('cycle.n01DurationDistribution', [cur.equipment.code, actName(distKey)])" />
            </div>
            <div>
              <div class="ct"><b>{{ $t('cycle.dailyDrift') }}</b><span v-if="trendText" class="muted small">{{ trendText }}</span></div>
              <Chart :option="driftChart" :height="220" :ai="$t('cycle.n0DailyMedianPerAction', [cur.equipment.code])" data-test="cyc-drift" />
            </div>
          </div>
          <div v-if="cur.loss?.items?.length" class="loss mt">
            <b>{{ $t('cycle.lossConversion') }}</b>{{ $t('cycle.n0CyclesPerDayPart', [fmtNum(cur.loss.cycles_per_day, 0), cur.loss.value_per_part, cur.loss.work_days]) }}
            <span v-for="x in cur.loss.items" :key="x.key" class="li">{{ $t('cycle.n01SCycle2', [x.name, fmtNum(x.excess_s, 2), fmtNum(x.lost_s_shift, 0), fmtNum(x.parts_day, 1), fmtNum(x.money_year, 0)]) }}</span>
          </div>
        </template>
      </HmCard>
    </section>

    <!-- Motion model editor -->
    <el-dialog v-model="edOpen" :title="$t('cycle.motionModel0', [cur?.equipment.code || ''])" width="min(1100px, 96vw)" data-test="cyc-editor">
      <div class="edh">
        <span>{{ $t('cycle.cycleSignal') }}</span>
        <el-select v-model="ed.cycle.mode" size="small" style="width: 130px"><el-option value="signal" :label="$t('cycle.cycleInProgress')" /><el-option value="step" :label="$t('cycle.programBlockNoWrapsAround')" /></el-select>
        <el-select v-model="ed.cycle.point" size="small" filterable style="width: 300px"><el-option v-for="p in pts" :key="p.key" :value="p.key" :label="p.name" /></el-select>
        <span>{{ $t('cycle.taktTargetS') }}</span><el-input-number v-model="ed.takt_s" size="small" :min="0" :controls="false" style="width: 90px" />
        <span>{{ $t('cycle.partValueCny') }}</span><el-input-number v-model="ed.value_per_part" size="small" :min="0" :controls="false" style="width: 90px" />
        <span class="grow" />
        <el-button size="small" :loading="busy === 'prop'" data-test="cyc-autoprop" @click="propose(cur.equipment.id, true)">{{ $t('cycle.autoDetectAiProposal') }}</el-button>
      </div>
      <el-table :data="ed.actions" size="small" max-height="420">
        <el-table-column :label="$t('common.action')" width="170"><template #default="{ row }"><el-input v-model="row.name" size="small" /></template></el-table-column>
        <el-table-column :label="$t('cycle.startSignal')" min-width="230"><template #default="{ row }"><div class="sig"><el-select v-model="row.start.point" size="small" filterable><el-option v-for="p in pts" :key="p.key" :value="p.key" :label="p.name" /></el-select>
          <el-select v-model="row.start.value" size="small" style="width: 70px"><el-option :value="1" label="→1" /><el-option :value="0" label="→0" /></el-select></div></template></el-table-column>
        <el-table-column :label="$t('cycle.endSignal')" min-width="230"><template #default="{ row }"><div class="sig"><el-select v-model="row.end.point" size="small" filterable><el-option v-for="p in pts" :key="p.key" :value="p.key" :label="p.name" /></el-select>
          <el-select v-model="row.end.value" size="small" style="width: 70px"><el-option :value="1" label="→1" /><el-option :value="0" label="→0" /></el-select></div></template></el-table-column>
        <el-table-column :label="$t('cycle.standardS')" width="100"><template #default="{ row }"><el-input-number v-model="row.std_s" size="small" :min="0" :step="0.05" :controls="false" style="width: 80px" /></template></el-table-column>
        <el-table-column :label="$t('cycle.baselineMedian')" width="96" align="right"><template #default="{ row }">{{ fmtNum(cur?.model?.baseline?.[row.key]?.median, 2) }}</template></el-table-column>
        <el-table-column width="50"><template #default="{ $index }"><el-button link size="small" @click="ed.actions.splice($index, 1)"><el-icon><Delete /></el-icon></el-button></template></el-table-column>
      </el-table>
      <div class="muted small mt">{{ $t('cycle.aiProposesActionsFromThe') }}<b>{{ $t('cycle.changesOnlyCountAfterConfirm') }}</b>{{ $t('cycle.changingTheActionListReturns') }}</div>
      <template #footer>
        <el-button @click="addAct">{{ $t('automations.addAnAction') }}</el-button>
        <el-button @click="edOpen = false">{{ $t('common.cancel') }}</el-button>
        <el-button v-perm:disable="'cycle.model.edit'" type="primary" data-test="cyc-save" @click="saveModel">{{ $t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="setOpen" :title="$t('cycle.cycleAnalysisParameters')" width="460px" data-test="cyc-settings-dlg">
      <el-form label-width="130px" size="small">
        <el-form-item :label="$t('cycle.partValueCny2')"><el-input-number v-model="sets.value_per_part" :min="0" /></el-form-item>
        <el-form-item :label="$t('cycle.workingDaysPerYear')"><el-input-number v-model="sets.work_days" :min="1" :max="366" /></el-form-item>
        <el-form-item :label="$t('cycle.workingHoursPerDay')"><el-input-number v-model="sets.hours_per_day" :min="1" :max="24" /></el-form-item>
        <el-form-item v-for="l in lines" :key="l.id || 0" :label="$t('cycle.n0TaktTargetS', [l.name])"><el-input-number v-model="sets.takt[String(l.id)]" :min="0" :controls="false" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="setOpen = false">{{ $t('common.cancel') }}</el-button><el-button v-perm:disable="'cycle.model.edit'" type="primary" @click="saveSettings">{{ $t('common.save') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { aiOn } from '../ai';
import DraftCard from '../ai/DraftCard.vue';
import { get, post, put } from '../api';
import Chart from '../components/Chart.vue';
import { CH, PALETTE, targetLine } from '../design/chart';
import { fmtNum } from '../design/format';
import './aiActions';

const route = useRoute(), router = useRouter();
const RANGES = [{ label: $t('cycle.n2H'), value: 2 }, { label: $t('cycle.n8H'), value: 8 }, { label: $t('cycle.n24H'), value: 24 }, { label: $t('common.n7Days'), value: 168 }];
const hours = ref(8), lineId = ref<number>(0), d = ref<any>(null), lines = ref<any[]>([]), cur = ref<any>(null), busy = ref(''), expl = ref<any>(null), dr = ref<any>(null);
const hideLong = ref(true), distKey = ref(''), edOpen = ref(false), setOpen = ref(false), pts = ref<any[]>([]);
const ed = reactive<any>({ cycle: { mode: 'signal', point: '' }, actions: [], takt_s: null, value_per_part: null });
const sets = reactive<any>({ value_per_part: 80, work_days: 300, hours_per_day: 16, takt: {} });

const st = computed<any[]>(() => d.value?.stations || []);
const bn = computed<any>(() => d.value?.bottleneck || null);
const bnLoss = computed<any>(() => (bn.value?.loss?.items || []).find((x: any) => x.key === bn.value?.action?.key) || null);
const alertN = computed(() => st.value.reduce((a, s) => a + (s.alerts?.length || 0), 0));
const takt = computed(() => d.value?.takt_s || null);
const todo = computed(() => lines.value.filter((l) => !lineId.value || l.id === lineId.value).flatMap((l) => l.equipment.filter((e: any) => e.has_actions && !e.model)));
const stRows = computed(() => st.value.map((s) => ({ id: s.equipment.id, code: s.equipment.code, name: s.equipment.name, status: s.status, cycles_n: s.cycles_n, cycle_s: s.cycle_s,
  eff_cycle_s: s.eff_cycle_s, parallel: s.parallel, peer: s.peer_median, top: s.top_action ? `${s.top_action.name} +${fmtNum(s.top_action.excess, 2)}s` : '—', money_day: s.money_day, bottleneck: s.bottleneck })));
const SCOLS = [{ key: 'code', label: $t('common.equipment'), width: 120 }, { key: 'name', label: $t('common.name') }, { key: 'status', label: $t('common.model'), width: 80 }, { key: 'cycles_n', label: $t('cycle.cycles'), num: true },
  { key: 'cycle_s', label: $t('cycle.cycleTimeS'), num: true, digits: 1 }, { key: 'parallel', label: $t('cycle.parallel'), num: true }, { key: 'eff_cycle_s', label: $t('cycle.cycleTimePerPartS'), num: true, digits: 1 },
  { key: 'peer', label: $t('cycle.groupMedianS'), num: true, digits: 1 }, { key: 'top', label: $t('cycle.slowestActionVsStandard') }, { key: 'money_day', label: $t('cycle.lossCnyDay'), num: true, digits: 0 }];
const LCOLS = [{ key: 'equipment', label: $t('common.equipment'), width: 70 }, { key: 'name', label: $t('common.action') }, { key: 'excess_s', label: $t('cycle.slowerSCycle'), num: true, digits: 2 },
  { key: 'parts_day', label: $t('cycle.pcsDay'), num: true, digits: 1 }, { key: 'money_year', label: $t('cycle.cnyYear'), num: true, digits: 0 }, { key: 'affects_output', label: '', width: 80 }];
const ACOLS = [{ key: 'name', label: $t('common.action') }, { key: 'n', label: $t('common.count'), num: true }, { key: 'median', label: $t('cycle.medianS'), num: true, digits: 3 }, { key: 'p90', label: 'P90 s', num: true, digits: 3 },
  { key: 'std_s', label: $t('cycle.standardS'), num: true, digits: 2 }, { key: 'baseline', label: $t('cycle.baselineS'), num: true, digits: 3 }, { key: 'excess', label: $t('cycle.aboveStandardS'), num: true },
  { key: 'share', label: $t('cycle.shareOfCycle'), num: true, format: 'pct' }];
const curSub = computed(() => cur.value?.model ? $t('cycle.n0CyclesMedianCycleTime', [cur.value.cycles_n, fmtNum(cur.value.cycle?.median, 1), fmtNum(cur.value.cycle?.p90, 1), cur.value.label || '']) : '');
const shortActs = computed(() => (cur.value?.actions || []).filter((a: any) => !(a.share > 0.5)));
const actName = (k: string) => cur.value?.actions?.find((a: any) => a.key === k)?.name || k;
const colorOf = (k: string) => PALETTE[(cur.value?.actions || []).findIndex((a: any) => a.key === k) % PALETTE.length];

async function loadLines() { const r = await get('/api/cycle/lines'); lines.value = r.lines; Object.assign(sets, { ...r.settings, takt: { ...(r.settings.takt || {}) } }); }
async function load() {
  d.value = null;
  d.value = await get(`/api/cycle/line?hours=${hours.value}` + (lineId.value ? `&line=${lineId.value}` : ''));
  if (!cur.value && route.query.eq) pick(Number(route.query.eq));
  else if (!cur.value && bn.value) pick(bn.value.equipment.id);
}
async function reload() { await load(); if (cur.value) pick(cur.value.equipment.id); }
async function pick(id: number) {
  if (!id) return;
  expl.value = null; dr.value = null;
  cur.value = await get(`/api/cycle/station/${id}?hours=${hours.value}&days=7&cycles=24`);
  if (!shortActs.value.find((a: any) => a.key === distKey.value)) distKey.value = (cur.value.alerts?.[0]?.action) || shortActs.value[0]?.key || '';
  if (route.query.eq !== String(id)) router.replace({ query: { ...route.query, eq: String(id) } });
}
async function propose(id: number, inEditor = false) {
  busy.value = inEditor ? 'prop' : 'p' + id;
  try {
    const r = await post(`/api/cycle/models/${id}/propose`, { hours: 4, save: !inEditor });
    if (inEditor) { Object.assign(ed, { cycle: { ...r.model.cycle }, actions: JSON.parse(JSON.stringify(r.model.actions)) }); ElMessage.success($t('cycle.detected0Actions1Cycles', [r.model.actions.length, r.evidence.cycles])); }
    else { ElMessage.success($t('cycle.detected0ActionsSavedAs', [r.model.actions.length])); await loadLines(); await load(); pick(id); }
  } finally { busy.value = ''; }
}
async function openEditor() {
  const r = await get(`/api/cycle/models/${cur.value.equipment.id}`);
  pts.value = r.points;
  const m = r.model || { cycle: { mode: 'signal', point: '' }, actions: [] };
  Object.assign(ed, { cycle: { ...m.cycle }, actions: JSON.parse(JSON.stringify(m.actions || [])), takt_s: m.takt_s ?? null, value_per_part: m.value_per_part ?? null });
  edOpen.value = true;
}
function addAct() { ed.actions.push({ key: 'act' + (ed.actions.length + 1), name: $t('cycle.newAction'), start: { point: '', value: 1 }, end: { point: '', value: 1 }, std_s: null }); }
async function saveModel() {
  await put(`/api/cycle/models/${cur.value.equipment.id}`, { cycle: ed.cycle, actions: ed.actions, takt_s: ed.takt_s || null, value_per_part: ed.value_per_part || null, name: cur.value.equipment.code });
  edOpen.value = false; ElMessage.success($t('cycle.savedDraftTakesEffectAfter')); await loadLines(); await reload();
}
async function confirmModel() { await post(`/api/cycle/models/${cur.value.equipment.id}/confirm`); ElMessage.success($t('cycle.motionModelConfirmed')); await reload(); }
async function learnBase() { const r = await post(`/api/cycle/models/${cur.value.equipment.id}/baseline`, { hours: Math.max(hours.value, 4) }); ElMessage.success($t('cycle.baselineLearnedFromTheLast', [Math.max(hours.value, 4), Object.keys(r.baseline || {}).length])); pick(cur.value.equipment.id); }
async function explain(k: string) { busy.value = 'x' + k; try { expl.value = await post('/api/cycle/explain', { equipment: cur.value.equipment.id, action: k, hours: hours.value }); } finally { busy.value = ''; } }
async function draft(k: string) { busy.value = 'd' + k; try { dr.value = await post('/api/cycle/draft', { equipment: cur.value.equipment.id, action: k, hours: hours.value }); ElMessage.success($t('cycle.repairOrderDraftedWaitingFor')); } finally { busy.value = ''; } }
function openSettings() { setOpen.value = true; }
async function saveSettings() { const r = await put('/api/cycle/settings', sets); Object.assign(sets, { ...r, takt: { ...(r.takt || {}) } }); setOpen.value = false; reload(); }

const lineChart = computed(() => ({
  tooltip: { trigger: 'axis', valueFormatter: (v: number) => fmtNum(v, 1) + ' s' }, legend: {}, grid: { top: 30 },
  xAxis: { type: 'category', data: st.value.map((s) => s.equipment.code) }, yAxis: { type: 'value', name: 's' },
  series: [
    { name: $t('cycle.cycleTimePerPart'), type: 'bar', data: st.value.map((s) => ({ value: s.eff_cycle_s, itemStyle: { color: s.bottleneck ? CH.bad : CH.primary } })),
      markLine: takt.value ? { symbol: 'none', data: [{ yAxis: takt.value, ...targetLine($t('cycle.taktTarget0S', [takt.value]), CH.bad) }] } : undefined },
    { name: $t('cycle.groupMedian'), type: 'line', data: st.value.map((s) => (s.peer_median && s.parallel ? s.peer_median / s.parallel : null)), color: CH.amber, showSymbol: true },
  ] }));
const gantt = computed(() => {
  const rows = (cur.value?.recent || []).slice().reverse();
  const long = new Set(hideLong.value ? (cur.value?.actions || []).filter((a: any) => a.share > 0.5).map((a: any) => a.key) : []);
  const disp = rows.map((cy: any) => {
    let shift = 0; const segs: any[] = [];
    for (const a of cy.acts.slice().sort((x: any, y: any) => x.off - y.off)) {
      const cut = long.has(a.key); const w = cut ? Math.min(a.dur, 2.5) : a.dur;
      segs.push({ key: a.key, name: actName(a.key), dur: a.dur, off: a.off - shift, w, c: colorOf(a.key), cut });
      if (cut) shift += a.dur - w;
    }
    return { label: cy.start.slice(0, 8), segs, span: Math.max(cy.dur - shift, ...segs.map((s) => s.off + s.w)) };
  });
  const span = Math.max(1, ...disp.map((x: any) => x.span));
  return { rows: disp.map((x: any) => ({ label: x.label, segs: x.segs.map((s: any) => ({ ...s, l: (s.off / span) * 100, w: Math.max(0.4, (s.w / span) * 100) })) })),
    legend: (cur.value?.actions || []).map((a: any) => ({ key: a.key, name: a.name + (long.has(a.key) ? $t('cycle.compressed') : ''), c: colorOf(a.key) })) };
});
const overlay = computed(() => {
  const rc = cur.value?.recent || [];
  return { tooltip: { trigger: 'axis', valueFormatter: (v: number) => fmtNum(v, 3) + ' s' }, legend: { type: 'scroll' }, grid: { top: 34 },
    xAxis: { type: 'category', data: rc.map((c: any) => c.start.slice(0, 5)) }, yAxis: { type: 'value', name: 's', scale: true },
    series: shortActs.value.map((a: any) => ({ name: a.name, type: 'line', showSymbol: true, color: colorOf(a.key),
      data: rc.map((c: any) => c.acts.find((x: any) => x.key === a.key)?.dur ?? null),
      markLine: a.baseline ? { symbol: 'none', silent: true, data: [{ yAxis: a.baseline, lineStyle: { color: colorOf(a.key), type: 'dotted' }, label: { show: false } }] } : undefined })) };
});
const distChart = computed(() => {
  const h = cur.value?.dist?.[distKey.value]; const a = cur.value?.actions?.find((x: any) => x.key === distKey.value);
  const ml: any[] = [];
  if (a?.std_s) ml.push({ xAxis: nearest(h?.x, a.std_s), ...targetLine($t('cycle.standard0S', [a.std_s])) });
  if (a?.baseline) ml.push({ xAxis: nearest(h?.x, a.baseline), ...targetLine($t('cycle.baseline0S', [fmtNum(a.baseline, 2)]), CH.amber) });
  return { tooltip: { trigger: 'axis' }, grid: { top: 24 }, xAxis: { type: 'category', data: h?.x || [], name: 's' }, yAxis: { type: 'value', name: $t('common.times') },
    series: [{ name: $t('common.count'), type: 'bar', data: h?.n || [], itemStyle: { color: colorOf(distKey.value) }, markLine: ml.length ? { symbol: 'none', data: ml } : undefined }] };
});
function nearest(xs: string[] | undefined, v: number) { if (!xs?.length) return undefined; let b = xs[0]; for (const x of xs) if (Math.abs(+x - v) < Math.abs(+b - v)) b = x; return b; }
const driftChart = computed(() => {
  const dl = cur.value?.daily || {}; const days = Array.from(new Set(Object.values(dl).flatMap((v: any) => v.map((x: any) => x.day)))).sort() as string[];
  return { tooltip: { trigger: 'axis', valueFormatter: (v: number) => fmtNum(v, 3) + ' s' }, legend: { type: 'scroll' }, grid: { top: 34 },
    xAxis: { type: 'category', data: days.map((x) => x.slice(5)) }, yAxis: { type: 'value', name: 's', scale: true },
    series: shortActs.value.map((a: any) => ({ name: a.name, type: 'line', showSymbol: true, color: colorOf(a.key), data: days.map((dd) => (dl[a.key] || []).find((x: any) => x.day === dd)?.median ?? null) })) };
});
const trendText = computed(() => Object.entries(cur.value?.trends || {}).filter(([k, v]: any) => !k.startsWith('_') && v.worse).map(([, v]: any) => v.text).join($t('common.k764')));
watch(() => route.query.eq, (v) => { if (v && Number(v) !== cur.value?.equipment?.id) pick(Number(v)); });
onMounted(async () => { if (route.query.line) lineId.value = Number(route.query.line); await loadLines(); if (!lineId.value) { const l = lines.value.find((x) => x.equipment.some((e: any) => e.model)); if (l?.id) lineId.value = l.id; } await load(); });
</script>

<style scoped>
.split { grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); }
@media (max-width: 1279px) { .split { grid-template-columns: minmax(0, 1fr); } }
.bnc { color: var(--hm-fault-ink); }
.todo { padding: 8px 14px 12px; font-size: var(--hm-fs-sm); display: flex; flex-wrap: wrap; gap: 4px 14px; align-items: center; }
.tdi { white-space: nowrap; }
.alerts { margin-bottom: 10px; display: flex; flex-direction: column; gap: 6px; }
.al { display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: var(--hm-r-sm); background: var(--hm-idle-bg, #fff7e6); font-size: var(--hm-fs-sm); }
.al.critical { background: var(--hm-fault-bg, #fdecec); }
.al .at { font-weight: 500; }
.expl { padding: 8px 10px; border-left: 3px solid var(--hm-ai, #6d5ae6); background: var(--hm-surface-2); font-size: var(--hm-fs-sm); line-height: 1.7; }
.ct { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; font-size: var(--hm-fs-sm); }
.gantt { font-size: var(--hm-fs-xs); }
.gr { display: flex; align-items: center; gap: 6px; height: 16px; margin-bottom: 3px; }
.gl { width: 58px; color: var(--hm-text-3); font-family: var(--hm-font-num, monospace); }
.gb { position: relative; flex: 1; height: 12px; background: var(--hm-surface-3); border-radius: 2px; }
.gb i { position: absolute; top: 0; bottom: 0; border-radius: 2px; }
.gb i.cut { background-image: repeating-linear-gradient(135deg, rgba(255,255,255,.55) 0 3px, transparent 3px 6px) !important; }
.glg { display: flex; flex-wrap: wrap; gap: 4px 12px; margin-top: 6px; color: var(--hm-text-3); }
.glg i { display: inline-block; width: 10px; height: 8px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }
.loss { font-size: var(--hm-fs-sm); line-height: 1.8; }
.loss .li { display: inline-block; margin-right: 14px; color: var(--hm-fault-ink); }
.edh { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; font-size: var(--hm-fs-sm); }
.sig { display: flex; gap: 4px; }
b.bad { color: var(--hm-fault-ink); }
.grow { flex: 1; }
.muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-xs); }
</style>
