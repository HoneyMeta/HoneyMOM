<template>
  <div class="page sa">
    <HmPageHead :title="$t('common.scheduleChangeProposals')" :sub="$t('scheduleAdjust.assumeAScenarioFirstA')">
      <el-button @click="router.push('/production/gantt')">{{ $t('entityPage.schedulingGantt') }}</el-button>
    </HmPageHead>
    <div class="grid two">
      <HmCard :title="$t('scheduleAdjust.whatIfAnalysis')" :sub="$t('scheduleAdjust.changesNoDataOnlyCalculates')">
        <el-form label-position="top" class="wf" data-test="whatif-form">
          <el-form-item :label="$t('scheduleAdjust.adjustmentType')">
            <el-radio-group v-model="kind" data-test="whatif-kind">
              <el-radio-button value="downtime">{{ $t('gantt.equipmentDowntime') }}</el-radio-button><el-radio-button value="kitting">{{ $t('scheduleAdjust.materialDelayed') }}</el-radio-button>
              <el-radio-button value="tooling">{{ $t('gantt.moldMaintenance') }}</el-radio-button><el-radio-button value="absent">{{ $t('scheduleAdjust.absence') }}</el-radio-button><el-radio-button value="priority">{{ $t('scheduleAdjust.changePriority') }}</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <template v-if="kind === 'downtime'">
            <div class="r2">
              <el-form-item :label="$t('common.equipment')"><el-select v-model="f.equipment" filterable data-test="whatif-eq"><el-option v-for="e in eqs" :key="e.id" :label="`${e.code} ${e.name}`" :value="e.code" /></el-select></el-form-item>
              <el-form-item :label="$t('common.reason')"><el-input v-model="f.reason" :placeholder="$t('scheduleAdjust.eGSpindleNoiseDown')" /></el-form-item>
            </div>
            <div class="r2">
              <el-form-item :label="$t('common.start')"><el-date-picker v-model="f.start" type="datetime" value-format="YYYY-MM-DD HH:mm:ss" style="width:100%" /></el-form-item>
              <el-form-item :label="$t('common.end')"><el-date-picker v-model="f.end" type="datetime" value-format="YYYY-MM-DD HH:mm:ss" style="width:100%" /></el-form-item>
            </div>
          </template>
          <template v-else-if="kind === 'kitting'">
            <div class="r2">
              <el-form-item :label="$t('common.workOrder')"><el-select v-model="f.work_order" filterable><el-option v-for="w in wos" :key="w.id" :label="`${w.code} ${w.customer || ''}`" :value="w.code" /></el-select></el-form-item>
              <el-form-item :label="$t('common.material')"><el-select v-model="f.material" filterable><el-option v-for="m in mats" :key="m.id" :label="`${m.code} ${m.name}`" :value="m.code" /></el-select></el-form-item>
            </div>
            <div class="r2">
              <el-form-item :label="$t('scheduleAdjust.shortageQty')"><el-input-number v-model="f.short" :min="0" :controls="false" style="width:100%" /></el-form-item>
              <el-form-item :label="$t('scheduleAdjust.expectedArrival')"><el-date-picker v-model="f.eta" type="datetime" value-format="YYYY-MM-DD HH:mm:ss" style="width:100%" /></el-form-item>
            </div>
          </template>
          <template v-else-if="kind === 'tooling'">
            <div class="r2">
              <el-form-item :label="$t('common.moldsFixtures')"><el-select v-model="f.tooling" filterable><el-option v-for="t in tools" :key="t.id" :label="`${t.code} ${t.name}`" :value="t.code" /></el-select></el-form-item>
              <el-form-item :label="$t('scheduleAdjust.maintenanceUntil')"><el-date-picker v-model="f.until" type="datetime" value-format="YYYY-MM-DD HH:mm:ss" style="width:100%" /></el-form-item>
            </div>
          </template>
          <template v-else-if="kind === 'absent'">
            <el-form-item :label="$t('scheduleAdjust.absentPeopleTheirSkillsAre')"><el-select v-model="f.absent" multiple filterable style="width:100%"><el-option v-for="e in emps" :key="e.id" :label="`${e.name} · ${e.team || ''} · ${e.post || ''}`" :value="e.name" /></el-select></el-form-item>
          </template>
          <template v-else>
            <div class="r2">
              <el-form-item :label="$t('common.workOrder')"><el-select v-model="f.work_order" filterable><el-option v-for="w in wos" :key="w.id" :label="$t('scheduleAdjust.n0Currently1', [w.code, w.priority])" :value="w.code" /></el-select></el-form-item>
              <el-form-item :label="$t('scheduleAdjust.newPriority1Highest')"><el-input-number v-model="f.priority" :min="1" :max="9" style="width:100%" /></el-form-item>
            </div>
          </template>
          <div class="btns">
            <el-button type="primary" :loading="busy" data-test="whatif-preview" @click="preview">{{ $t('scheduleAdjust.previewImpact') }}</el-button>
            <el-button :disabled="!res" :loading="saving" data-test="whatif-save" @click="save">{{ $t('scheduleAdjust.saveAsProposal') }}</el-button>
          </div>
        </el-form>
        <template v-if="res">
          <div class="sum" data-test="whatif-summary"><b class="hm-num">{{ res.affected }}</b> {{ $t('scheduleAdjust.workOrdersAffected') }}<span v-if="res.late.length">{{ $t('scheduleAdjust.expectedLate') }} <b class="bad">{{ res.late.join($t('common.k42')) }}</b></span></div>
          <ImpactTable :rows="res.impact" />
        </template>
      </HmCard>

      <HmCard :title="$t('erpMrp.adjustmentSuggestions')" :sub="$t('scheduleAdjust.n0Pending', [items.filter((x) => x.status === 'pending').length])" flush>
        <el-table :data="items" size="small" height="calc(100vh - 260px)" @row-click="open" data-test="proposal-table"
          v-ai-rows="{ entity: 'schedule_proposal', rows: () => items, label: (r: any) => $t('scheduleAdjust.scheduleProposal01', [r.code, r.title]), data: (r: any) => ({ [$t('common.title')]: r.title, [$t('common.status')]: PST[r.status], [$t('common.source')]: r.source, [$t('scheduleAdjust.proposedBy')]: r.created_by }) }">
          <el-table-column prop="code" :label="$t('scheduleAdjust.proposalNo')" width="120" /><el-table-column prop="title" :label="$t('common.title')" min-width="200" show-overflow-tooltip />
          <el-table-column :label="$t('common.status')" width="80"><template #default="{ row }"><HmTag :tone="row.status === 'pending' ? 'warn' : row.status === 'accepted' ? 'ok' : 'neutral'">{{ PST[row.status] }}</HmTag></template></el-table-column>
          <el-table-column :label="$t('common.source')" width="70"><template #default="{ row }">{{ row.source === 'agent' ? $t('common.agents') : $t('common.manual2') }}</template></el-table-column>
        </el-table>
      </HmCard>
    </div>

    <el-drawer v-model="drawer" :title="cur ? `${cur.code} ${cur.title}` : ''" size="min(980px, 96vw)" destroy-on-close>
      <template v-if="cur">
        <div class="acts" v-if="cur.status === 'pending'">
          <el-button type="primary" data-test="proposal-accept" @click="decide('accept')">{{ $t('scheduleAdjust.acceptReschedule') }}</el-button><el-button @click="decide('reject')">{{ $t('scheduleAdjust.decline') }}</el-button>
        </div>
        <h4>{{ $t('approvalCard.adjustments') }}</h4>
        <ul class="chg"><li v-for="(c, i) in cur.changes || []" :key="i">{{ changeText(c) }}</li><li v-if="!(cur.changes || []).length" class="muted">—</li></ul>
        <h4>{{ $t('common.description') }}</h4><div class="md">{{ cur.proposal || '—' }}</div>
        <h4>{{ $t('scheduleAdjust.impactAssessment') }} <small class="muted">{{ cur.status === 'accepted' ? $t('scheduleAdjust.actualResultAfterAcceptance') : $t('scheduleAdjust.estimateAtProposalTime') }}</small></h4>
        <ImpactTable v-if="cur.impact?.length" :rows="cur.impact" /><p v-else class="muted">{{ $t('scheduleAdjust.noImpactAssessmentProposedBy') }}</p>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { onMounted, onUnmounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { askAi, registerAiActions } from '../ai';
import { get, post } from '../api';
import ImpactTable from '../components/biz/ImpactTable.vue';

const router = useRouter(), route = useRoute();
const PST: Record<string, string> = { pending: $t('common.pending'), accepted: $t('scheduleAdjust.accepted'), rejected: $t('scheduleAdjust.declined') };
const kind = ref('downtime'), busy = ref(false), saving = ref(false), res = ref<any>(null);
const f = reactive<any>({ equipment: '', reason: '', start: '', end: '', work_order: '', material: '', short: 1, eta: '', tooling: '', until: '', absent: [], priority: 1 });
const eqs = ref<any[]>([]), wos = ref<any[]>([]), mats = ref<any[]>([]), tools = ref<any[]>([]), emps = ref<any[]>([]), items = ref<any[]>([]);
const drawer = ref(false), cur = ref<any>(null);

const pad = (n: number) => String(n).padStart(2, '0');
const fmt = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:00`;
function body() {
  if (kind.value === 'downtime') return { downtime: [{ equipment: f.equipment, start: f.start, end: f.end, reason: f.reason || $t('scheduleAdjust.assumedDowntime') }] };
  if (kind.value === 'kitting') return { kitting: [{ work_order: f.work_order, material: f.material, eta: f.eta, short: f.short }] };
  if (kind.value === 'tooling') return { tooling: [{ tooling: f.tooling, until: f.until }] };
  if (kind.value === 'absent') return { absent: f.absent };
  return { priority: [{ work_order: f.work_order, priority: f.priority }] };
}
async function preview() {
  busy.value = true;
  try {
    res.value = await post('/api/production/schedule/preview', body());
  } finally { busy.value = false; }
}
async function save() {
  saving.value = true;
  try { const r = await post('/api/production/schedule/propose', body()); ElMessage.success($t('scheduleAdjust.savedAsProposal0Work', [r.affected])); await loadItems(); open({ id: r.id }); }
  finally { saving.value = false; }
}
async function loadItems() { items.value = (await get('/api/e/schedule_proposal?size=100&sort=-id')).items; }
async function open(row: any) { cur.value = (await get('/api/e/schedule_proposal/' + row.id)).item; drawer.value = true; }
async function decide(a: string) {
  const r = await post(`/api/e/schedule_proposal/${cur.value.id}/do/${a}`, {});
  ElMessage.success(r.msg || $t('common.resolved')); await loadItems(); await open(cur.value);
}
function changeText(c: any) {
  const k = c.kind || (c.priority ? 'priority' : '');
  return k === 'downtime' ? $t('scheduleAdjust.equipment0Down12', [c.equipment, c.start, c.end, c.reason || '']) : k === 'kitting' ? $t('scheduleAdjust.n1For0ExpectedTo', [c.work_order, c.material || $t('common.material'), c.eta])
    : k === 'tooling' ? $t('scheduleAdjust.mold0InMaintenanceUntil', [c.tooling, c.until]) : k === 'priority' ? $t('scheduleAdjust.n0PriorityChangedTo1', [c.work_order, c.priority]) + (c.note ? $t('aiSettings.n0', [c.note]) : '') : JSON.stringify(c);
}
const off = registerAiActions('row', [
  { id: 'sp-why', label: $t('scheduleAdjust.aiExplainThisProposal'), icon: 'MagicStick', ai: true, order: 30, when: (it) => it.entity === 'schedule_proposal',
    run: (it, sel) => askAi($t('scheduleAdjust.isThisScheduleProposalWorth', [it.label]), sel, { action: 'explain' }) },
]);
onUnmounted(off);
onMounted(async () => {
  const clk = await get('/api/clock');                       // Use the data clock (the virtual factory may run accelerated), not browser time
  const now = new Date(String(clk.now).replace(' ', 'T')); const s = new Date(now.getTime() + 2 * 3600e3); s.setMinutes(0);
  f.start = fmt(s); f.end = fmt(new Date(s.getTime() + 6 * 3600e3)); f.eta = fmt(new Date(s.getTime() + 24 * 3600e3)); f.until = fmt(new Date(s.getTime() + 8 * 3600e3));
  const [e, w, m, t, o] = await Promise.all([get('/api/e/equipment?size=200&sort=code'), get('/api/e/work_order?size=100&sort=-id'), get('/api/e/material?size=200&sort=code'),
    get('/api/e/tooling?size=200&sort=code'), get('/api/station/options')]);
  eqs.value = e.items.filter((x: any) => !['crew', 'agv'].includes(x.etype)); wos.value = w.items.filter((x: any) => ['released', 'in_progress'].includes(x.status));
  mats.value = m.items.filter((x: any) => x.mtype !== 'fg'); tools.value = t.items; emps.value = o.employees;
  f.equipment = eqs.value.find((x: any) => x.etype === 'cnc')?.code || eqs.value[0]?.code; f.work_order = wos.value[0]?.code; f.material = mats.value[0]?.code; f.tooling = tools.value[0]?.code;
  await loadItems();
  if (route.query.id) open({ id: route.query.id });
});
</script>

<style scoped>
.two { grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); align-items: start; }
@media (max-width: 1200px) { .two { grid-template-columns: 1fr; } }
.r2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.wf :deep(.el-select) { width: 100%; }
.btns { display: flex; gap: 8px; margin: 4px 0 12px; }
.sum { margin: 6px 0 8px; font-size: var(--hm-fs-sm); } .sum b { font-size: var(--hm-fs-lg); }
.acts { display: flex; gap: 8px; margin-bottom: 10px; }
.chg { margin: 4px 0 10px; padding-left: 18px; font-size: var(--hm-fs-sm); }
.md { white-space: pre-wrap; font-size: var(--hm-fs-sm); color: var(--hm-text-2); background: var(--hm-surface-2); padding: 8px 10px; border-radius: var(--hm-r-sm); }
.bad { color: var(--hm-fault-ink); } .muted { color: var(--hm-text-3); }
</style>
