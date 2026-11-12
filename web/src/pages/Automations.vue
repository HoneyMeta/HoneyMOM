<template>
  <div class="page auto">
    <div class="page-head"><h2>{{ $t('nav.automations') }}</h2><span class="sub">{{ $t('automations.describeARuleInOne') }}</span><span class="grow" />
      <span class="muted small">{{ $t('automations.writeActionsAreOnlyDrafted') }}</span></div>

    <div class="cols">
      <div class="main">
        <!-- 1. One sentence -->
        <section class="composer hm-card" data-test="auto-composer">
          <div class="ch"><span class="spk" aria-hidden="true" /><b>{{ $t('automations.describeTheRule') }}</b><span class="grow" /><span v-if="parsedBy" class="by" :class="parsedBy">{{ BY[parsedBy] }}</span></div>
          <el-input v-model="text" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" resize="none" data-test="auto-text"
            :placeholder="$t('automations.eGIfSpindleLoad')" @keydown.ctrl.enter="parse" />
          <div class="ex">
            <button v-for="e in EXAMPLES" :key="e" class="exb" @click="text = e">{{ e }}</button>
          </div>
          <div class="ca"><el-button type="primary" :loading="parsing" :disabled="text.trim().length < 4" data-test="auto-parse" @click="parse"><el-icon><MagicStick /></el-icon>{{ $t('automations.generateFlow') }}</el-button>
            <span class="muted small">Ctrl+Enter</span></div>
        </section>

        <!-- 2. Flow -->
        <section v-if="flow" class="flow hm-card" data-test="auto-flow">
          <div class="ch"><b>{{ $t('common.flow') }}</b><el-input v-model="flow.name" size="small" class="nm" :placeholder="$t('nodeConfig.name')" /><span class="grow" /><span class="sumtxt">{{ summary }}</span></div>
          <div class="steps">
            <div class="step trig">
              <div class="sh"><span class="no">1</span>{{ $t('common.trigger') }}</div><div class="fl">
              <el-select v-model="flow.trigger.type" size="small" @change="dirty">
                <el-option v-for="(lab, k) in meta.triggers" :key="k" :label="lab" :value="k" /></el-select>
              <template v-if="flow.trigger.type === 'metric'">
                <el-select v-model="flow.trigger.metric" size="small" @change="dirty"><el-option v-for="m in meta.metrics" :key="m.key" :label="$t('common.n01', [m.label, m.unit])" :value="m.key" /></el-select>
                <div class="inl"><el-select v-model="flow.trigger.op" size="small" style="width: 70px" @change="dirty"><el-option v-for="o in ['>', '>=', '<', '<=']" :key="o" :label="o" :value="o" /></el-select>
                  <el-input-number v-model="flow.trigger.value" size="small" :controls="false" style="width: 90px" @change="dirty" /><span class="u">{{ unitOf(flow.trigger.metric) }}</span></div>
                <div class="inl"><span class="lb">{{ $t('automations.lasting') }}</span><el-input-number v-model="flow.trigger.for_min" size="small" :min="0" :controls="false" style="width: 70px" @change="dirty" /><span class="u">{{ $t('common.min2') }}</span></div>
              </template>
              <template v-else-if="flow.trigger.type === 'alarm'"><el-input v-model="flow.trigger.match" size="small" :placeholder="$t('automations.alarmTextContainsEmptyAny')" @change="dirty" /></template>
              <template v-else-if="flow.trigger.type === 'down'"><div class="inl"><span class="lb">{{ $t('automations.downForMoreThan') }}</span><el-input-number v-model="flow.trigger.for_min" size="small" :min="1" :controls="false" style="width: 70px" @change="dirty" /><span class="u">{{ $t('common.min2') }}</span></div></template>
              <template v-else-if="flow.trigger.type === 'ncr'">
                <el-input v-model="flow.trigger.match" size="small" :placeholder="$t('automations.defectNameContainsEG')" @change="dirty" />
                <div class="inl"><el-input-number v-model="flow.trigger.within_min" size="small" :min="10" :controls="false" style="width: 70px" @change="dirty" /><span class="u">{{ $t('automations.minCount') }}</span>
                  <el-input-number v-model="flow.trigger.count" size="small" :min="1" :controls="false" style="width: 56px" @change="dirty" /><span class="u">{{ $t('common.k2047') }}</span></div>
              </template>
              <el-select v-if="flow.trigger.type !== 'ncr'" v-model="eqSel" size="small" multiple collapse-tags collapse-tags-tooltip :placeholder="$t('automations.allEquipment')" @change="dirty">
                <el-option v-for="e in equipment" :key="e.code" :label="`${e.code} ${e.name}`" :value="e.code" /></el-select>
            </div></div>
            <div class="arrow" aria-hidden="true" />
            <div class="step cond">
              <div class="sh"><span class="no">2</span>{{ $t('automations.condition') }}</div><div class="fl">
              <label class="ck"><el-checkbox v-model="useTime" @change="dirty" />{{ $t('automations.onlyDuring') }}</label>
              <div v-if="useTime" class="inl"><el-time-select v-model="tFrom" size="small" start="00:00" step="00:30" end="23:30" style="width: 96px" @change="dirty" />
                <span class="u">{{ $t('automations.to') }}</span><el-time-select v-model="tTo" size="small" start="00:00" step="00:30" end="23:30" style="width: 96px" @change="dirty" /></div>
              <div class="inl"><span class="lb">{{ $t('automations.sameObject') }}</span><el-input-number v-model="flow.cooldown_min" size="small" :min="1" :controls="false" style="width: 60px" @change="dirty" /><span class="u">{{ $t('automations.minWithoutRepeating') }}</span></div>
            </div></div>
            <div class="arrow" aria-hidden="true" />
            <div class="step act">
              <div class="sh"><span class="no">3</span>{{ $t('common.action') }}</div><div class="fl">
              <div v-for="(a, i) in flow.actions" :key="i" class="actrow">
                <el-select v-model="a.type" size="small" style="width: 116px" @change="dirty"><el-option v-for="(lab, k) in meta.actions" :key="k" :label="lab" :value="k" /></el-select>
                <template v-if="a.type === 'notify'">
                  <el-select v-model="a.to_role" size="small" style="width: 96px" @change="dirty"><el-option v-for="(lab, k) in meta.roles" :key="k" :label="lab" :value="k" /></el-select>
                  <el-input v-model="a.title" size="small" :placeholder="$t('automations.notificationTitleCanUseEquipment')" @change="dirty" />
                </template>
                <el-input v-else-if="a.type === 'suggest'" v-model="a.text" size="small" :placeholder="$t('automations.adviceForTheShopFloor')" @change="dirty" />
                <template v-else-if="a.type === 'draft'">
                  <el-select v-model="a.op" size="small" style="width: 120px" @change="dirty"><el-option v-for="o in DRAFT_OPS" :key="o.k" :label="o.l" :value="o.k" /></el-select>
                  <span class="muted small">{{ $t('automations.draftWhenTriggeredWaitFor') }}</span>
                </template>
                <el-input-number v-else-if="a.type === 'agent'" v-model="a.agent_id" size="small" :controls="false" :placeholder="$t('automations.agent')" @change="dirty" />
                <button class="rm" :title="$t('automations.deleteThisAction')" @click="flow.actions.splice(i, 1); dirty()"><el-icon><Close /></el-icon></button>
              </div>
              <el-button link size="small" @click="flow.actions.push({ type: 'notify', to_role: 'leader', title: $t('automations.equipmentMetricAbnormal'), level: 'warn' }); dirty()"><el-icon><Plus /></el-icon>{{ $t('automations.addAnAction') }}</el-button>
            </div></div>
          </div>
        </section>

        <!-- 3. Replay -->
        <section v-if="flow" class="replay hm-card" data-test="auto-replay">
          <div class="ch"><b>{{ $t('automations.historicalReplay') }}</b><span class="muted small">{{ $t('automations.runThisRuleOverRecent') }}</span><span class="grow" />
            <el-radio-group v-model="days" size="small"><el-radio-button v-for="d in [1, 3, 7, 14]" :key="d" :value="d">{{ $t('common.n0Days', [d]) }}</el-radio-button></el-radio-group>
            <el-button size="small" type="primary" plain :loading="replaying" data-test="auto-replay-btn" @click="replay"><el-icon><VideoPlay /></el-icon>{{ $t('automations.replay') }}</el-button></div>
          <HmSkeleton v-if="replaying && !rep" variant="kpi" />
          <template v-if="rep">
            <div class="grid g4">
              <HmKpi :label="$t('automations.wouldFire')" :value="rep.count" format="int" :unit="$t('common.times')" :status="rep.per_day > 12 ? 'warn' : ''" :ai="false" data-test="auto-count" />
              <HmKpi :label="$t('automations.dailyAverage')" :value="rep.per_day" format="num" :unit="$t('common.times')" :ai="false" />
              <HmKpi :label="$t('automations.dataSpan')" :value="rep.days" format="num" :unit="$t('common.days2')" :hint="$t('automations.from0', [(rep.from || '').slice(5, 16)])" :ai="false" />
              <HmKpi :label="$t('automations.dataScanned')" :value="rep.scanned" format="int" :unit="$t('common.k2047')" :ai="false" />
            </div>
            <div class="note" :class="{ warn: rep.per_day > 12, none: !rep.count }">{{ rep.note }}</div>
            <div class="rg">
              <div class="tl"><div class="st">{{ $t('automations.triggerTimelinePer0', [rep.timeline.unit]) }}</div>
                <Chart :option="tlOpt" :height="170" :ai="$t('automations.automationReplayTriggerCount')" /></div>
              <div v-if="rep.by_equipment.length" class="be"><div class="st">{{ $t('automations.byObject') }}</div>
                <Chart :option="beOpt" :height="Math.min(170, 30 + rep.by_equipment.length * 22)" /></div>
            </div>
            <table v-if="rep.fires.length" class="fires">
              <thead><tr><th>{{ $t('automations.triggerTime') }}</th><th>{{ $t('common.object') }}</th><th class="n">{{ $t('common.value') }}</th><th class="n">{{ $t('automations.lasting') }}</th></tr></thead>
              <tbody><tr v-for="(f, i) in rep.fires.slice(-8).reverse()" :key="i"><td class="hm-num">{{ f.t.slice(5, 16) }}</td><td>{{ f.equipment }}</td>
                <td class="n">{{ f.peak ?? f.value }}</td><td class="n">{{ f.duration_min != null ? f.duration_min + $t('common.min') : f.msg || '—' }}</td></tr></tbody>
            </table>
          </template>
          <div class="save">
            <el-button type="primary" :disabled="!rep" data-test="auto-enable" @click="save('active')"><el-icon><CircleCheck /></el-icon>{{ $t('common.enable') }}</el-button>
            <el-button :disabled="!flow" @click="save('paused')">{{ $t('automations.saveFirstPaused') }}</el-button>
            <span v-if="!rep" class="muted small">{{ $t('automations.replayOnceBeforeEnabling') }}</span>
          </div>
        </section>
        <HmEmpty v-if="!flow" icon="data" :title="$t('automations.noFlowYet')" :desc="$t('automations.describeARuleInOne2')" />
      </div>

      <!-- Existing rules -->
      <aside class="rules hm-card" data-test="auto-rules">
        <div class="ch"><b>{{ $t('automations.myRules') }}</b><span class="muted small">{{ $t('common.n03', [rules.length]) }}</span></div>
        <HmEmpty v-if="!rules.length" compact icon="data" :title="$t('automations.noRulesYet')" />
        <div v-for="r in rules" :key="r.id" class="rule" :class="r.status" data-test="auto-rule">
          <div class="rh"><HmStatus :status="r.status === 'active' ? 'running' : 'idle'" variant="dot" /><b>{{ r.name }}</b><span class="grow" />
            <span class="rs">{{ r.status === 'active' ? $t('automations.active') : $t('automations.paused') }}</span></div>
          <div class="rsum">{{ r.summary }}</div>
          <div class="rm2"><span>{{ $t('automations.fired0Times', [r.fire_count || 0]) }}</span><span v-if="r.last_fire">{{ $t('automations.last0', [r.last_fire.slice(5, 16)]) }}</span>
            <span v-if="r.replay">{{ $t('automations.replay0Times1Days', [r.replay.count, r.replay.days]) }}</span></div>
          <div class="ra">
            <el-button v-if="r.status === 'active'" size="small" data-test="auto-pause" @click="setStatus(r, 'paused')">{{ $t('automations.pause') }}</el-button>
            <el-button v-else size="small" type="primary" plain @click="setStatus(r, 'active')">{{ $t('common.enable') }}</el-button>
            <el-button size="small" @click="edit(r)">{{ $t('automations.editReplay') }}</el-button>
            <el-button size="small" link @click="toggleFires(r)">{{ $t('automations.triggerLog') }}</el-button>
            <el-popconfirm :title="$t('automations.deleteThisRule')" @confirm="remove(r)"><template #reference><el-button size="small" link type="danger" data-test="auto-delete">{{ $t('common.delete') }}</el-button></template></el-popconfirm>
          </div>
          <div v-if="fires[r.id]" class="fls">
            <div v-if="!fires[r.id].length" class="muted small">{{ $t('automations.neverFired') }}</div>
            <div v-for="f in fires[r.id]" :key="f.id" class="fr"><span class="hm-num">{{ (f.t || '').slice(5, 16) }}</span> {{ f.equipment }} {{ f.value }}
              <span class="muted">→ {{ (f.detail?.actions || []).map((a: any) => a.text).join($t('common.k764')) }}</span></div>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// Automations (feature key: automation). Backend services/ai_automation.py.
import { computed, onMounted, reactive, ref } from 'vue';
import { useRoute } from 'vue-router';
import { toast } from '../ai';
import { del, get, post, put } from '../api';
import Chart from '../components/Chart.vue';
import { CH } from '../design/chart';

const route = useRoute();
const BY: Record<string, string> = { ai: $t('automations.aiGenerated'), demo: $t('automations.aiGeneratedDemoMode'), rule: $t('automations.ruleParsing') };
const EXAMPLES = [$t('automations.ifSpindleLoadStays90'), $t('automations.ifCncSpindleLoadStays'), $t('automations.ifMoreThan3Residual'),
  $t('automations.draftARepairOrderWhen')];
const DRAFT_OPS = [{ k: 'repair_order', l: $t('common.repairOrders') }, { k: 'andon_raise', l: $t('common.andon') }, { k: 'param_adjust', l: $t('automations.adjustParameters') }, { k: 'schedule_change', l: $t('automations.adjustSchedule') }, { k: 'ncr', l: $t('automations.ncr8d') }];
const text = ref(String(route.query.text || '')), parsing = ref(false), parsedBy = ref(''), flow = ref<any>(null), summary = ref('');
const meta = reactive<any>({ metrics: [], triggers: {}, actions: {}, roles: {} });
const equipment = ref<any[]>([]), rules = ref<any[]>([]), fires = reactive<Record<number, any[] | null>>({});
const days = ref(7), replaying = ref(false), rep = ref<any>(null), editingId = ref<number | null>(null);
const useTime = ref(false), tFrom = ref('08:00'), tTo = ref('20:00');
const eqSel = ref<string[]>([]);

const unitOf = (k: string) => meta.metrics.find((m: any) => m.key === k)?.unit || '';
function dirty() { rep.value = null; }
function build() {
  const f = JSON.parse(JSON.stringify(flow.value));
  f.trigger.equipment = eqSel.value.length ? eqSel.value : '*';
  f.conditions = useTime.value ? [{ type: 'time', from: tFrom.value, to: tTo.value }] : [];
  return f;
}
function load(f: any) {
  flow.value = JSON.parse(JSON.stringify(f));
  eqSel.value = Array.isArray(f.trigger.equipment) ? f.trigger.equipment : [];
  const tc = (f.conditions || []).find((c: any) => c.type === 'time');
  useTime.value = !!tc; if (tc) { tFrom.value = tc.from; tTo.value = tc.to; }
}
async function parse() {
  parsing.value = true;
  try {
    const r = await post('/api/ai/automations/parse', { text: text.value });
    load(r.flow); summary.value = r.summary; parsedBy.value = r.by; rep.value = null; editingId.value = null;
    replay();
  } finally { parsing.value = false; }
}
async function replay() {
  replaying.value = true;
  try { rep.value = await post('/api/ai/automations/replay', { flow: build(), days: days.value }); summary.value = rep.value.summary; } finally { replaying.value = false; }
}
async function save(status: 'active' | 'paused') {
  const f = build();
  if (editingId.value) {
    await put(`/api/ai/automations/${editingId.value}`, { flow: f });
    await post(`/api/ai/automations/${editingId.value}/status`, { status });
  } else {
    await post('/api/ai/automations', { flow: f, text: text.value, status, replay: rep.value, by: parsedBy.value });
  }
  toast(status === 'active' ? $t('automations.enabledChecksEvery30S') : $t('automations.savedPaused'), 'success');
  flow.value = null; rep.value = null; text.value = ''; editingId.value = null;
  loadRules();
}
async function loadRules() {
  const r = await get('/api/ai/automations');
  rules.value = r.items; Object.assign(meta, { metrics: r.metrics, triggers: r.triggers, actions: r.actions, roles: r.roles });
}
async function setStatus(r: any, status: string) { await post(`/api/ai/automations/${r.id}/status`, { status }); loadRules(); }
async function remove(r: any) { await del(`/api/ai/automations/${r.id}`); loadRules(); }
function edit(r: any) { load(r.flow); text.value = r.text || ''; editingId.value = r.id; summary.value = r.summary; parsedBy.value = ''; replay(); window.scrollTo({ top: 0 }); }
async function toggleFires(r: any) { fires[r.id] = fires[r.id] ? null : await get(`/api/ai/automations/${r.id}/fires`); }

const tlOpt = computed(() => ({
  tooltip: { trigger: 'axis' }, grid: { left: 4, right: 8, top: 10, bottom: 4, containLabel: true },
  xAxis: { type: 'category', data: rep.value.timeline.x }, yAxis: { type: 'value', minInterval: 1 },
  series: [{ type: 'bar', name: $t('common.trigger'), data: rep.value.timeline.n, barMaxWidth: 14, itemStyle: { color: CH.amber, borderRadius: [2, 2, 0, 0] } }],
}));
const beOpt = computed(() => ({
  tooltip: { trigger: 'axis' }, grid: { left: 4, right: 24, top: 4, bottom: 4, containLabel: true },
  xAxis: { type: 'value', minInterval: 1 }, yAxis: { type: 'category', inverse: true, data: rep.value.by_equipment.map((x: any) => x.equipment) },
  series: [{ type: 'bar', name: $t('common.count'), data: rep.value.by_equipment.map((x: any) => x.n), barMaxWidth: 12, label: { show: true, position: 'right', fontSize: 11 } }],
}));

onMounted(async () => {
  await loadRules();
  equipment.value = (await get('/api/e/equipment?size=200')).items.filter((e: any) => !['crew', 'agv'].includes(e.etype));
  if (text.value) parse();
});
</script>

<style scoped>
.cols { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 14px; align-items: start; }
@media (max-width: 1180px) { .cols { grid-template-columns: 1fr; } }
.main { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.hm-card { padding: 12px 14px; }
.ch { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; } .ch b { font-size: var(--hm-fs-md); } .grow { flex: 1; }
.spk { width: 14px; height: 14px; background: var(--hm-ai-grad); clip-path: polygon(50% 0, 62% 38%, 100% 50%, 62% 62%, 50% 100%, 38% 62%, 0 50%, 38% 38%); }
.composer { border-color: var(--hm-primary-soft-2); background: linear-gradient(180deg, var(--hm-ai-soft), var(--hm-surface) 70%); }
.composer :deep(textarea) { font-size: var(--hm-fs-md); line-height: 1.6; }
.by { font-size: 11px; border-radius: var(--hm-r-pill); padding: 0 8px; line-height: 18px; background: var(--hm-surface-3); color: var(--hm-text-2); }
.by.ai, .by.demo { background: var(--hm-ai-grad); color: #fff; }
.ex { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.exb { border: 1px dashed var(--hm-border-strong); background: var(--hm-surface); color: var(--hm-text-2); border-radius: var(--hm-r-pill); padding: 2px 10px; font: inherit; font-size: var(--hm-fs-xs); cursor: pointer; }
.exb:hover { border-color: var(--hm-primary); color: var(--hm-primary); }
.ca { display: flex; align-items: center; gap: 8px; }
.nm { width: 220px; } .sumtxt { font-size: var(--hm-fs-xs); color: var(--hm-text-2); max-width: 100%; }
.steps { display: flex; flex-direction: column; }
.step { display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 8px; align-items: start; border: 1px solid var(--hm-border); border-left-width: 3px; border-radius: var(--hm-r-md);
  padding: 9px 12px; background: var(--hm-surface-2); }
.step.trig { border-left-color: var(--hm-amber); } .step.cond { border-left-color: var(--hm-cyan); } .step.act { border-left-color: var(--hm-primary); }
.fl { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; min-width: 0; }
.fl > .el-select { width: 170px; } .fl > .el-input { width: 260px; }
.sh { display: flex; align-items: center; gap: 6px; font-weight: var(--hm-fw-semibold); font-size: var(--hm-fs-sm); padding-top: 3px; }
.no { width: 18px; height: 18px; border-radius: 50%; background: var(--hm-text); color: #fff; font-size: 11px; display: inline-flex; align-items: center; justify-content: center; font-family: var(--hm-font-num); }
.arrow { width: 2px; height: 12px; margin-left: 46px; background: var(--hm-border-strong); }
.inl { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; } .u, .lb { font-size: var(--hm-fs-xs); color: var(--hm-text-3); }
.ck { display: flex; align-items: center; gap: 6px; font-size: var(--hm-fs-xs); color: var(--hm-text-2); }
.actrow { display: flex; align-items: center; gap: 6px; flex-basis: 100%; } .actrow > .el-input { flex: 1; min-width: 140px; }
.rm { border: 0; background: transparent; color: var(--hm-text-3); cursor: pointer; padding: 2px; } .rm:hover { color: var(--hm-fault-ink); }
.note { margin: 10px 0 6px; font-size: var(--hm-fs-xs); color: var(--hm-run-ink); background: var(--hm-run-soft); border-radius: var(--hm-r-sm); padding: 6px 10px; }
.note.warn { color: var(--hm-idle-ink); background: var(--hm-idle-soft); } .note.none { color: var(--hm-text-2); background: var(--hm-surface-3); }
.rg { display: grid; grid-template-columns: 1.6fr 1fr; gap: 12px; } @media (max-width: 1100px) { .rg { grid-template-columns: 1fr; } }
.st { font-size: var(--hm-fs-xs); color: var(--hm-text-3); margin-bottom: 2px; }
.fires { width: 100%; border-collapse: collapse; font-size: var(--hm-fs-xs); margin-top: 8px; }
.fires th { text-align: left; color: var(--hm-text-3); font-weight: var(--hm-fw-medium); padding: 4px 8px; border-bottom: 1px solid var(--hm-border); background: var(--hm-surface-2); }
.fires td { padding: 4px 8px; border-bottom: 1px solid var(--hm-hairline); } .fires .n { text-align: right; font-family: var(--hm-font-num); }
.save { display: flex; align-items: center; gap: 8px; margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--hm-hairline); }
.rules { position: sticky; top: 8px; max-height: calc(100vh - 150px); overflow: auto; }
.rule { border-top: 1px solid var(--hm-hairline); padding: 10px 0; } .rule:first-of-type { border-top: 0; }
.rh { display: flex; align-items: center; gap: 6px; } .rh b { font-size: var(--hm-fs-sm); }
.rs { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); } .rule.active .rs { color: var(--hm-run-ink); }
.rsum { font-size: var(--hm-fs-xs); color: var(--hm-text-2); margin: 4px 0; line-height: 1.55; }
.rm2 { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); display: flex; gap: 4px; flex-wrap: wrap; }
.ra { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; } .ra .el-button { margin: 0; }
.fls { margin-top: 6px; background: var(--hm-surface-2); border-radius: var(--hm-r-sm); padding: 6px 8px; font-size: var(--hm-fs-2xs); display: flex; flex-direction: column; gap: 3px; }
</style>
