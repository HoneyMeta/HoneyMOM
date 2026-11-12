<template>
  <div class="term" data-test="station">
    <!-- Select workstation -->
    <div v-if="!wsCodes.length" class="setup">
      <header class="tb"><img src="/brand/mark.svg" alt="" class="mk" /><b>{{ $t('common.workstationTerminal') }}</b><span class="grow" /><button class="lnk" @click="router.push('/')">{{ $t('mobile.desktop') }}</button></header>
      <div class="setup-body">
        <h2>{{ $t('station.whichMachinesDoesThisTerminal') }}</h2><p class="muted">{{ $t('station.oneOperatorCanRunSeveral') }}</p>
        <div v-for="(list, line) in byLine" :key="line" class="ln">
          <div class="lnh">{{ line }}</div>
          <div class="pick">
            <button v-for="w in list" :key="w.code" class="pk" :class="{ on: pick.includes(w.code) }" :data-test="'pick-' + w.code" @click="toggle(w.code)">
              <b class="hm-mono">{{ (w.equipment__label || w.code).split(' ')[0] }}</b><span>{{ w.name }}</span>
            </button>
          </div>
        </div>
        <button class="big primary" :disabled="!pick.length" data-test="station-go" @click="go">{{ $t('station.enter0Machines', [pick.length]) }}</button>
      </div>
    </div>

    <template v-else>
      <header class="tb">
        <img src="/brand/mark.svg" alt="" class="mk" /><b>{{ $t('common.workstationTerminal') }}</b><span class="sep" />
        <span class="hm-mono">{{ d?.machines.map((m: any) => m.equipment?.code || m.workstation).join(' · ') }}</span>
        <span class="grow" />
        <span v-if="d?.shift" class="sh">{{ d.shift.name }} {{ d.shift.start }}–{{ d.shift.end }}</span>
        <span class="clk hm-num">{{ (d?.now || '').slice(11, 16) }}</span>
        <button class="who" :class="{ on: !!d?.attendance }" data-test="station-clock" @click="clockDlg = true">
          <el-icon><User /></el-icon><span>{{ emp ? emp.name : $t('station.notClockedIn') }}</span><small>{{ d?.attendance ? $t('station.onShiftClockOut') : $t('station.clockIn') }}</small>
        </button>
        <button class="lnk" :title="$t('station.changeStation')" @click="reset"><el-icon><Setting /></el-icon></button>
        <button class="lnk" @click="router.push('/')">{{ $t('mobile.desktop') }}</button>
      </header>

      <main class="main" v-if="d">
        <section class="machines" :style="{ '--n': Math.min(d.machines.length, 4) }">
          <article v-for="m in d.machines" :key="m.workstation" class="mc" :class="'s-' + (m.equipment?.status || 'offline')" :data-test="'mc-' + (m.equipment?.code || m.workstation)"
            v-ai="{ kind: 'device', entity: 'equipment', id: m.equipment?.id, label: `${m.equipment?.code} ${m.name} ${ST[m.equipment?.status] || ''}`, data: { [$t('common.status')]: ST[m.equipment?.status], [$t('station.cycleRatePcsH')]: m.rate_h, [$t('station.goodToday')]: m.good_today, [$t('station.defectiveToday')]: m.ng_today, [$t('common.alarm')]: m.alarm, [$t('common.workOrder')]: m.current?.work_order_code } }">
            <div class="band"><HmStatus :status="m.equipment?.status || 'offline'" /><b class="hm-mono">{{ m.equipment?.code || m.workstation }}</b><span>{{ m.name }}</span>
              <span class="grow" /><small v-if="m.since" class="hm-num">{{ $t('automations.from0', [(m.since || '').slice(11, 16)]) }}</small></div>
            <div v-if="m.alarm" class="alarm"><el-icon><WarningFilled /></el-icon>{{ m.alarm }}</div>
            <dl class="kv">
              <div><dt>{{ $t('station.cycleTime') }}</dt><dd class="hm-num">{{ m.rate_h }}<small>{{ $t('coplan.pcsH') }}</small></dd></div>
              <div><dt>{{ $t('station.goodToday') }}</dt><dd class="hm-num">{{ m.good_today }}</dd></div>
              <div><dt>{{ $t('station.defectiveToday') }}</dt><dd class="hm-num" :class="{ bad: m.ng_today || m.ncr_today }">{{ m.ng_today + m.ncr_today }}</dd></div>
            </dl>
            <div v-if="m.current" class="cur">
              <div class="cw"><b class="hm-mono">{{ m.current.work_order_code }}</b><span>{{ m.current.seq }} {{ m.current.process_name }}</span></div>
              <div class="pb"><b :style="{ width: Math.min(100, (m.current.qty_good / (m.current.qty_plan || 1)) * 100) + '%' }" /></div>
              <div class="muted hm-num">{{ m.current.qty_good }} / {{ m.current.qty_plan }} · {{ (m.current.product || '').split(' ')[0] }}</div>
              <div v-if="m.current.fai_required" class="fai" :class="m.current.fai_ok ? 'ok' : 'no'" data-test="mc-fai">
                <el-icon><component :is="m.current.fai_ok ? 'CircleCheckFilled' : 'WarningFilled'" /></el-icon>
                {{ m.current.fai_ok ? $t('reports.firstArticleReleased') : m.current.fai?.status === 'pending' ? $t('station.firstArticlePendingJudgment0', [m.current.fai.code]) : $t('station.firstArticleNotReleasedProduction') }}
                <button v-if="!m.current.fai_ok && m.current.fai?.status !== 'pending'" class="sm" data-test="mc-fai-apply" @click="applyFai(m)">{{ $t('common.requestFirstArticle') }}</button>
              </div>
            </div>
            <div v-else class="cur muted">{{ $t('station.noWorkOrderOperationIn') }}</div>
            <div v-for="t in m.tooling" :key="t.code" class="tool" :class="{ warn: t.maint_due || (t.remaining !== null && t.remaining < 1000) }"
              v-ai="{ kind: 'field', label: $t('station.mold0Shots1', [t.code, t.shots]), data: { [$t('station.mold')]: t.code, [$t('common.name')]: t.name, [$t('station.currentShots')]: t.shots, [$t('station.remainingShots')]: t.remaining, [$t('station.toPm')]: t.maint_in } }">
              <el-icon><Box /></el-icon><b class="hm-mono">{{ t.code }}</b>
              <span>{{ $t('station.shots') }} <b class="hm-num">{{ fmtNum(t.shots, 0) }}</b></span><span>{{ $t('station.remaining') }} <b class="hm-num">{{ t.remaining === null ? '—' : fmtNum(t.remaining, 0) }}</b></span>
              <span :class="{ due: t.maint_due }">{{ t.maint_in === null ? '' : t.maint_due ? $t('station.pmDue') : $t('station.n0ToPm', [fmtNum(t.maint_in, 0)]) }}</span>
            </div>
            <div v-if="m.maint_due.length" class="md"><el-icon><Bell /></el-icon>{{ $t('station.n0ChecksPmTasksDue', [m.maint_due.length]) }}</div>
          </article>
        </section>

        <section class="lower">
          <div class="pane ins">
            <div class="ph">{{ $t('common.workInstructions') }}</div>
            <InstructionPanel :ins="d.instruction" big inline />
          </div>
          <div class="pane tasks">
            <div class="ph">{{ $t('station.todaySProductionTasks') }} <small>{{ d.tasks.length }}</small></div>
            <div v-for="t in d.tasks" :key="t.id" class="task" :class="'t-' + t.status" v-ai="{ kind: 'row', entity: 'wo_op', id: t.id, label: `${t.work_order_code} ${t.process_name} ${t.qty_good}/${t.qty_plan}`, data: t }">
              <div><b class="hm-mono">{{ t.work_order_code }}</b> · {{ t.seq }} {{ t.process_name }}<span class="grow" /><HmTag :tone="t.status === 'running' ? 'primary' : 'neutral'">{{ OS[t.status] }}</HmTag></div>
              <div class="muted hm-num">{{ $t('station.n01Plan23', [t.qty_good || 0, t.qty_plan, (t.plan_start || '').slice(5, 16), (t.plan_end || '').slice(11, 16)]) }}</div>
              <div v-if="(t.conflicts || []).length" class="cfl">{{ [...new Set(t.conflicts.map((c: any) => c.label))].join($t('common.k42')) }}</div>
            </div>
            <HmEmpty v-if="!d.tasks.length" compact :title="$t('station.noTasksScheduledHereToday')" />
          </div>
        </section>
      </main>
      <HmSkeleton v-else variant="table" :rows="6" />

      <footer class="actions">
        <button class="big primary" data-test="act-report" @click="openReport()"><el-icon><Finished /></el-icon>{{ $t('mobile.report') }}</button>
        <button class="big" data-test="act-repair" @click="repDlg = true"><el-icon><SetUp /></el-icon>{{ $t('common.requestRepair') }}</button>
        <button class="big" data-test="act-spot" @click="spotDlg = true"><el-icon><List /></el-icon>{{ $t('mobile.checks') }}<i v-if="dueAll.length" class="cnt">{{ dueAll.length }}</i></button>
        <button class="big" data-test="act-fai" @click="router.push('/quality/fai')"><el-icon><Stamp /></el-icon>{{ $t('common.firstArticle') }}</button>
        <button v-if="can('pack.station')" class="big" data-test="act-pack" @click="openPack"><el-icon><Box /></el-icon>{{ $t('station.packing') }}</button>
        <button class="big" data-test="act-docs" @click="openDocs"><el-icon><Document /></el-icon>{{ $t('common.file') }}</button>
        <button class="big" @click="clockDlg = true"><el-icon><Timer /></el-icon>{{ d?.attendance ? $t('station.clockOut') : $t('station.clockIn') }}</button>
      </footer>
    </template>

    <!-- Clock in/out -->
    <el-dialog v-model="clockDlg" :title="d?.attendance ? $t('station.clockOut') : $t('station.clockIn')" width="min(560px, 96vw)" class="tdlg" append-to-body>
      <p class="muted">{{ $t('station.swipeYourBadgeScannerOr', [wsCodes.join($t('common.k42'))]) }}</p>
      <ScanInput big :placeholder="$t('station.swipeBadgeEnterEmployeeId')" :global="clockDlg" @scan="(c) => (empCode = c)" />
      <el-select v-model="empId" filterable :placeholder="$t('station.orSelectAnEmployee')" size="large" style="width:100%; margin-top: 10px" data-test="clock-emp">
        <el-option v-for="e in opts?.employees || []" :key="e.id" :label="`${e.code} ${e.name} · ${e.post || ''}`" :value="e.id" />
      </el-select>
      <template #footer>
        <button class="big" @click="clockDlg = false">{{ $t('common.cancel') }}</button>
        <button v-if="d?.attendance" class="big danger" data-test="clock-off" @click="clock('off')">{{ $t('station.clockOut') }}</button>
        <button v-else class="big primary" data-test="clock-on" @click="clock('on')">{{ $t('station.clockIn') }}</button>
      </template>
    </el-dialog>

    <!-- Report production -->
    <el-dialog v-model="rp.open" :title="$t('mobile.report')" width="min(640px, 96vw)" class="tdlg" append-to-body data-test="report-dlg">
      <div class="mpick">
        <button v-for="m in d?.machines.filter((x: any) => x.current) || []" :key="m.workstation" class="pk" :class="{ on: rp.m === m }" @click="selectM(m)">
          <b class="hm-mono">{{ m.equipment?.code }}</b><span>{{ m.current.work_order_code }} · {{ m.current.process_name }}</span>
        </button>
      </div>
      <div v-if="rp.chk" class="chk" :class="{ bad: rp.chk.blockers.length }" data-test="report-check">
        <template v-if="rp.chk.blockers.length">{{ $t('mobile.cannotReport0', [rp.chk.blockers.join($t('common.k764'))]) }}</template>
        <template v-else>{{ $t('station.maxReportableNow') }} <b class="hm-num">{{ rp.chk.max }}</b> {{ $t('station.pcsPlanShortBy0', [rp.chk.max_plan]) }}<template v-if="rp.chk.max_seq !== null">{{ $t('station.n0AvailableFromThePrevious', [rp.chk.max_seq]) }}</template>{{ $t('common.k386') }}</template>
      </div>
      <div class="qty">
        <div class="q"><label>{{ $t('common.good') }}</label><div class="st"><button @click="rp.good = Math.max(0, rp.good - 1)">−</button><input v-model.number="rp.good" type="number" min="0" data-test="report-good" /><button @click="rp.good++">{{ $t('mobile.k4008') }}</button></div></div>
        <div class="q"><label>{{ $t('common.defective') }}</label><div class="st"><button @click="rp.ng = Math.max(0, rp.ng - 1)">−</button><input v-model.number="rp.ng" type="number" min="0" data-test="report-ng" /><button @click="rp.ng++">{{ $t('mobile.k4008') }}</button></div></div>
      </div>
      <el-select v-if="rp.ng" v-model="rp.defect" :placeholder="$t('common.defect')" size="large" style="width:100%; margin-top: 10px"><el-option v-for="x in defects" :key="x.id" :label="`${x.code} ${x.name}`" :value="x.id" /></el-select>
      <template #footer>
        <button class="big" @click="rp.open = false">{{ $t('common.cancel') }}</button>
        <button class="big primary" :disabled="rp.busy || !rp.m || rp.good + rp.ng <= 0" data-test="report-submit" @click="doReport">{{ rp.busy ? $t('mobile.submitting') : $t('station.submitReport') }}</button>
      </template>
    </el-dialog>

    <!-- Request repair -->
    <el-dialog v-model="repDlg" :title="$t('common.requestRepair')" width="min(600px, 96vw)" class="tdlg" append-to-body>
      <div class="mpick"><button v-for="m in d?.machines || []" :key="m.workstation" class="pk" :class="{ on: rep.equipment === m.equipment?.id }" @click="rep.equipment = m.equipment?.id">
        <b class="hm-mono">{{ m.equipment?.code }}</b><span>{{ m.name }}</span></button></div>
      <div class="chips"><button v-for="t in FAULTS" :key="t" class="chip" @click="rep.fault = t">{{ t }}</button></div>
      <el-input v-model="rep.fault" type="textarea" :rows="3" :placeholder="$t('common.faultDescription')" data-test="repair-fault" />
      <el-radio-group v-model="rep.level" size="large" style="margin-top: 10px"><el-radio-button value="normal">{{ $t('common.marginal') }}</el-radio-button><el-radio-button value="urgent">{{ $t('station.urgentMachineDown') }}</el-radio-button></el-radio-group>
      <template #footer><button class="big" @click="repDlg = false">{{ $t('common.cancel') }}</button><button class="big danger" data-test="repair-submit" :disabled="!rep.equipment || !rep.fault" @click="doRepair">{{ $t('mobile.submitRepairRequest') }}</button></template>
    </el-dialog>

    <!-- Scan to pack (packing station; auto-seal and label when a carton is full) -->
    <el-dialog v-model="pk.open" :title="$t('mobile.scanToPack')" width="min(640px, 96vw)" class="tdlg" append-to-body data-test="pack-dlg">
      <p class="muted">{{ $t('station.scanPartCodesToPack') }}<b class="hm-mono">{{ pk.station }}</b></p>
      <ScanInput big :placeholder="$t('mobile.scanPartCodeCartonNo')" :global="pk.open" data-test="pack-scan-st" @scan="doPack" />
      <div v-if="pk.msg" class="chk" :class="{ bad: pk.bad }" data-test="pack-msg-st">{{ pk.msg }}</div>
      <div v-if="pk.state?.carton" class="pkc" data-test="pack-carton-st">
        <b class="hm-mono">{{ pk.state.carton.code }}</b><span class="hm-num">{{ pk.state.carton.qty }} / {{ pk.state.carton.capacity }}</span>
        <div class="pb"><b :style="{ width: Math.min(100, (pk.state.carton.qty / (pk.state.carton.capacity || 1)) * 100) + '%' }" /></div>
        <div class="muted small">{{ $t('mobile.lot0', [pk.state.carton.lots || '—']) }}</div>
      </div>
      <div v-if="pk.state?.pallet" class="pkc"><b class="hm-mono">{{ $t('mobile.pallet0', [pk.state.pallet.code]) }}</b><span class="hm-num">{{ $t('mobile.n01Cartons', [pk.state.pallet.cartons?.length || 0, pk.state.pallet.capacity]) }}</span></div>
      <template #footer>
        <button class="big" @click="pk.open = false">{{ $t('common.close') }}</button>
        <button v-if="pk.state?.carton" class="big primary" data-test="pack-close-st" @click="closePack(pk.state.carton.code)">{{ $t('common.sealCarton') }}</button>
      </template>
    </el-dialog>

    <!-- Controlled documents (current effective version only) -->
    <el-dialog v-model="dc.open" :title="$t('station.currentControlledDocuments')" width="min(760px, 96vw)" class="tdlg" append-to-body data-test="docs-dlg">
      <p class="muted">{{ $t('station.workInstructionsDrawingsNcPrograms') }}</p>
      <div v-for="x in dc.items" :key="x.doc" class="spot" data-test="doc-item">
        <div><b>{{ x.title }}</b> <HmTag tone="ok">{{ $t('station.current0', [x.rev]) }}</HmTag>
          <div class="muted">{{ $t('station.n01Approved2Effective', [x.code, DT[x.dtype] || x.dtype, x.approved_by || '—', (x.effective_at || '').slice(0, 10)]) }}</div>
          <div v-if="x.excerpt" class="exc">{{ x.excerpt }}</div></div>
        <div class="sb"><button v-if="x.file_type" class="big" @click="openFile(x)">{{ $t('commandPalette.open') }}</button></div>
      </div>
      <HmEmpty v-if="!dc.items.length" compact :title="$t('station.noControlledDocumentsForThe')" />
    </el-dialog>

    <!-- Inspection rounds -->
    <el-dialog v-model="spotDlg" :title="$t('common.checksPm')" width="min(640px, 96vw)" class="tdlg" append-to-body>
      <div v-for="p in dueAll" :key="p.id" class="spot">
        <div><b>{{ p.name }}</b><div class="muted">{{ p.items }}</div></div>
        <div class="sb"><button class="big ok" data-test="spot-ok" @click="spot(p, 'ok')">{{ $t('common.normal') }}</button><button class="big danger" @click="spot(p, 'abnormal')">{{ $t('common.abnormal') }}</button></div>
      </div>
      <HmEmpty v-if="!dueAll.length" compact :title="$t('mobile.allOfTodaySChecks')" />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { call, get, post, session, tok } from '../api';
import { can } from '../perm';
import { newKey, submitReport } from '../components/biz/bizkit';
import InstructionPanel from '../components/biz/InstructionPanel.vue';
import ScanInput from '../components/biz/ScanInput.vue';
import { fmtNum } from '../design';

const router = useRouter(), route = useRoute();
const ST: Record<string, string> = { running: $t('common.running'), idle: $t('common.idle'), setup: $t('common.changeover'), down: $t('common.down'), offline: $t('common.offline') };
const OS: Record<string, string> = { waiting: $t('common.waitingToStart'), ready: $t('common.readyToStart'), running: $t('common.inProgress'), done: $t('common.done') };
const FAULTS = [$t('mobile.spindleNoise'), $t('mobile.lowLubricationPressure'), $t('mobile.lowAirPressure'), $t('mobile.toolBreakage'), $t('mobile.looseFixture'), $t('station.coolantLeak'), $t('mobile.alarmStop')];
const K_WS = 'hm_station_ws', K_EMP = 'hm_station_emp';
const read = (k: string) => { try { return localStorage.getItem(k) || ''; } catch { return ''; } };
const write = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* */ } };
const wsCodes = ref<string[]>(String(route.query.ws || read(K_WS) || '').split(',').filter(Boolean));
const pick = ref<string[]>([]), opts = ref<any>(null), d = ref<any>(null), defects = ref<any[]>([]);
const empId = ref<number | null>(Number(read(K_EMP)) || session.user?.employee || null), empCode = ref('');
const emp = computed(() => d.value?.employee || null);
const clockDlg = ref(false), repDlg = ref(false), spotDlg = ref(false);
const rp = reactive<any>({ open: false, m: null, good: 1, ng: 0, defect: null, busy: false, key: '', chk: null });
const rep = reactive<any>({ equipment: null, fault: '', level: 'normal' });
let timer = 0;

const byLine = computed(() => {
  const g: Record<string, any[]> = {};
  for (const w of opts.value?.workstations || []) (g[w.line__label?.split(' ').slice(1).join(' ') || $t('common.other')] ||= []).push(w);
  return g;
});
const dueAll = computed(() => (d.value?.machines || []).flatMap((m: any) => m.maint_due));
function toggle(c: string) { pick.value = pick.value.includes(c) ? pick.value.filter((x) => x !== c) : [...pick.value, c]; }
function go() { wsCodes.value = [...pick.value]; write(K_WS, wsCodes.value.join(',')); router.replace({ query: { ws: wsCodes.value.join(',') } }); load(); }
function reset() { pick.value = [...wsCodes.value]; wsCodes.value = []; }

async function load() {
  if (!wsCodes.value.length) return;
  const qs = new URLSearchParams({ ws: wsCodes.value.join(',') });
  if (empId.value) qs.set('employee', String(empId.value));
  d.value = await get('/api/station/view?' + qs, true).catch(() => d.value);
}
async function clock(action: string) {
  const e = empCode.value || empId.value;
  if (!e) { ElMessage.warning($t('station.swipeYourBadgeOrSelect')); return; }
  const r = await post('/api/station/clock', { employee: e, action, workstations: wsCodes.value.join(',') });
  ElMessage.success(r.msg); clockDlg.value = false;
  if (empId.value) write(K_EMP, String(empId.value));
  if (empCode.value) { const m = opts.value?.employees.find((x: any) => x.code === empCode.value); if (m) { empId.value = m.id; write(K_EMP, String(m.id)); } }
  empCode.value = '';
  await load();
}
async function selectM(m: any) {
  rp.m = m; rp.chk = null;
  try { rp.chk = await post('/api/production/report/check', { wo_op: m.current.id }); } catch { /* */ }
}
function openReport(m?: any) {
  Object.assign(rp, { open: true, good: 1, ng: 0, defect: null, busy: false, key: newKey('term'), chk: null, m: null });
  const first = m || d.value?.machines.find((x: any) => x.current);
  if (first) selectM(first);
}
async function doReport() {
  if (rp.busy) return;               // Double click: the second is ignored; network retries reuse the same idempotency key
  rp.busy = true;
  try {
    const r = await submitReport({ wo_op: rp.m.current.id, qty_good: rp.good, qty_ng: rp.ng, defect: rp.defect, equipment: rp.m.equipment?.id, source: 'terminal',
      employee: empId.value || undefined, station: rp.m.equipment?.code }, rp.key);
    if (r) { rp.open = false; await load(); }
  } finally { rp.busy = false; }
}
async function applyFai(m: any) {
  const r = await post('/api/quality/fai', { wo_op: m.current.id, equipment: m.equipment?.id, trigger: 'start' }); ElMessage.success(r.msg); await load();
}
async function doRepair() {
  await post('/api/e/repair_order', { equipment: rep.equipment, fault: rep.fault, level: rep.level, reported_by: emp.value?.name || session.user?.name, reported_at: null });
  ElMessage.success($t('mobile.repairRequestedTheMaintenanceTechnic')); repDlg.value = false; rep.fault = '';
}
async function spot(p: any, result: string) {
  const notes = result === 'abnormal' ? (await ElMessageBox.prompt($t('mobile.abnormalityDescription'), p.name)).value : '';
  const r = await post(`/api/e/maint_plan/${p.id}/do/execute`, { result, notes }); ElMessage.success(r.msg || $t('common.recorded')); await load();
}
// ---- Scan to pack / controlled documents (5.0 M5) ----
const DT: Record<string, string> = { wi: $t('common.workInstruction'), drawing: $t('common.drawing'), control_plan: $t('common.controlPlan'), pfmea: 'PFMEA', nc: $t('common.ncPrograms'), other: $t('common.other') };
const pk = reactive<any>({ open: false, station: '', state: null, msg: '', bad: false });
const dc = reactive<any>({ open: false, items: [] });
async function openPack() {
  pk.station = ('ST-' + wsCodes.value.join('+')).slice(0, 40);
  pk.open = true; pk.msg = ''; pk.state = await get('/api/pack/station?station=' + encodeURIComponent(pk.station), true).catch(() => null);
}
async function doPack(code: string) {
  try {
    const r = await call('POST', '/api/pack/scan', { code, station: pk.station }, true);
    pk.msg = r.msg; pk.bad = false; pk.state = r.state;
  } catch (e: any) { pk.msg = e?.message || String(e); pk.bad = true; }
}
async function closePack(code: string) {
  const r = await post('/api/pack/close', { code }); pk.msg = r.msg; pk.bad = false; pk.state = r.state;
}
async function openDocs() {
  const cur = d.value?.machines.find((x: any) => x.current)?.current;
  const qs = new URLSearchParams();
  if (cur?.product_id) qs.set('product', String(cur.product_id));
  if (cur?.process) qs.set('process', String(cur.process));
  dc.items = await get('/api/doc/effective?' + qs, true).catch(() => []);
  dc.open = true;
}
function openFile(x: any) { if (x.target_entity === 'kb_doc') window.open(`/api/kb/${x.target_id}/file?token=${tok()}`, '_blank'); }
onMounted(async () => {
  document.documentElement.classList.add('hm-terminal');
  opts.value = await get('/api/station/options');
  if (!empId.value && opts.value.me) empId.value = opts.value.me;
  defects.value = (await get('/api/e/defect_code?size=50&sort=code')).items;
  if (wsCodes.value.length) { write(K_WS, wsCodes.value.join(',')); await load(); }
  timer = window.setInterval(load, 10000);
});
onUnmounted(() => { clearInterval(timer); document.documentElement.classList.remove('hm-terminal'); });
</script>

<style scoped>
.term { min-height: 100vh; background: var(--hm-canvas); display: flex; flex-direction: column; font-size: var(--hm-fs-md); color: var(--hm-text); }
.tb { display: flex; align-items: center; gap: 12px; height: 60px; padding: 0 16px; background: var(--hm-surface); border-bottom: 1px solid var(--hm-border); box-shadow: var(--hm-e1); }
.tb .mk { height: 28px; } .tb b { font-size: var(--hm-fs-lg); } .sep { width: 1px; height: 24px; background: var(--hm-border); }
.sh { color: var(--hm-text-2); } .clk { font-size: 26px; font-weight: 600; }
.who { display: flex; align-items: center; gap: 8px; height: 44px; padding: 0 14px; border-radius: var(--hm-r-pill); border: 1px solid var(--hm-border-strong); background: var(--hm-surface-2); font: inherit; cursor: pointer; }
.who.on { border-color: var(--hm-run); background: var(--hm-run-soft); color: var(--hm-run-ink); } .who small { color: var(--hm-text-3); }
.lnk { border: 0; background: none; font: inherit; color: var(--hm-primary); cursor: pointer; padding: 8px; font-size: var(--hm-fs-md); }
.main { flex: 1; padding: 14px 16px 96px; display: flex; flex-direction: column; gap: 14px; }
.machines { display: grid; grid-template-columns: repeat(var(--n), minmax(0, 1fr)); gap: 14px; }
@media (max-width: 900px) { .machines { grid-template-columns: 1fr; } }
@media (max-width: 600px) { .main { padding-bottom: 230px; } .actions .big { font-size: 16px; min-height: 52px; } }
.mc { background: var(--hm-surface); border: 1px solid var(--hm-border); border-radius: var(--hm-r-lg); box-shadow: var(--hm-e1); overflow: hidden; display: flex; flex-direction: column; }
.band { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-bottom: 1px solid var(--hm-border); background: var(--hm-surface-2); }
.band b { font-size: 22px; } .band small { color: var(--hm-text-3); }
.s-running .band { border-top: 4px solid var(--hm-run); } .s-down .band { border-top: 4px solid var(--hm-fault); background: var(--hm-fault-soft); }
.s-idle .band { border-top: 4px solid var(--hm-idle); } .s-setup .band { border-top: 4px solid var(--hm-setup); } .s-offline .band { border-top: 4px solid var(--hm-offline); }
.alarm { display: flex; gap: 6px; align-items: center; padding: 8px 14px; background: var(--hm-fault-soft); color: var(--hm-fault-ink); font-weight: 500; }
.kv { display: grid; grid-template-columns: repeat(3, 1fr); margin: 0; padding: 10px 14px; gap: 8px; }
.kv dt { font-size: var(--hm-fs-xs); color: var(--hm-text-3); } .kv dd { margin: 0; font-size: 30px; font-weight: 600; font-family: var(--hm-font-num); }
.kv dd small { font-size: var(--hm-fs-xs); color: var(--hm-text-3); margin-left: 3px; font-weight: 400; } .kv .bad { color: var(--hm-fault); }
.cur { padding: 8px 14px 10px; border-top: 1px solid var(--hm-hairline); }
.cw { display: flex; gap: 8px; align-items: baseline; } .cw b { font-size: var(--hm-fs-lg); color: var(--hm-primary); }
.pb { height: 8px; border-radius: 4px; background: var(--hm-surface-3); margin: 6px 0 4px; overflow: hidden; } .pb b { display: block; height: 100%; background: var(--hm-primary); }
.fai { margin-top: 8px; padding: 8px 10px; border-radius: var(--hm-r-sm); display: flex; align-items: center; gap: 6px; font-weight: 500; }
.fai.ok { background: var(--hm-run-soft); color: var(--hm-run-ink); } .fai.no { background: var(--hm-idle-soft); color: var(--hm-idle-ink); }
.sm { margin-left: auto; border: 1px solid currentColor; background: #fff; color: inherit; border-radius: var(--hm-r-sm); padding: 6px 12px; font: inherit; cursor: pointer; }
.tool { display: flex; gap: 10px; align-items: center; padding: 8px 14px; border-top: 1px solid var(--hm-hairline); font-size: var(--hm-fs-sm); flex-wrap: wrap; }
.tool.warn { background: var(--hm-idle-soft); } .tool .due { color: var(--hm-fault-ink); font-weight: 600; }
.md { padding: 8px 14px; color: var(--hm-idle-ink); background: var(--hm-idle-soft); display: flex; gap: 6px; align-items: center; }
.lower { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr); gap: 14px; }
@media (max-width: 1100px) { .lower { grid-template-columns: 1fr; } }
.pane { background: var(--hm-surface); border: 1px solid var(--hm-border); border-radius: var(--hm-r-lg); padding: 12px 14px; box-shadow: var(--hm-e1); }
.ph { font-weight: 600; font-size: var(--hm-fs-lg); margin-bottom: 8px; } .ph small { color: var(--hm-text-3); font-weight: 400; }
.task { padding: 10px 0; border-bottom: 1px solid var(--hm-hairline); } .task > div:first-child { display: flex; align-items: center; gap: 6px; }
.task.t-running { border-left: 3px solid var(--hm-primary); padding-left: 8px; } .cfl { color: var(--hm-idle-ink); font-size: var(--hm-fs-xs); }
.actions { position: fixed; left: 0; right: 0; bottom: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 10px; padding: 10px 16px; background: var(--hm-glass-strong);
  backdrop-filter: var(--hm-glass-blur); border-top: 1px solid var(--hm-border); }
.big { min-height: 60px; border-radius: var(--hm-r-md); border: 1px solid var(--hm-border-strong); background: var(--hm-surface); font: inherit; font-size: 20px; font-weight: 600;
  cursor: pointer; display: inline-flex; align-items: center; justify-content: center; gap: 8px; padding: 0 22px; color: var(--hm-text); position: relative; }
.big:active { transform: translateY(1px); } .big:disabled { opacity: .5; cursor: not-allowed; }
.big.primary { background: var(--hm-primary); border-color: var(--hm-primary); color: #fff; } .big.danger { background: var(--hm-fault); border-color: var(--hm-fault); color: #fff; }
.big.ok { background: var(--hm-run); border-color: var(--hm-run); color: #fff; }
.cnt { position: absolute; top: 6px; right: 10px; background: var(--hm-amber); color: #fff; border-radius: 10px; font-size: 12px; padding: 1px 7px; font-style: normal; }
.setup-body { max-width: 1100px; margin: 0 auto; padding: 24px 16px 40px; } .setup-body h2 { margin: 0 0 4px; }
.ln { margin: 18px 0; } .lnh { font-weight: 600; color: var(--hm-text-2); margin-bottom: 8px; }
.pick, .mpick { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 10px; }
.pk { min-height: 64px; border: 2px solid var(--hm-border); background: var(--hm-surface); border-radius: var(--hm-r-md); font: inherit; cursor: pointer; display: flex; flex-direction: column;
  align-items: flex-start; justify-content: center; padding: 8px 12px; text-align: left; }
.pk b { font-size: var(--hm-fs-lg); } .pk span { font-size: var(--hm-fs-xs); color: var(--hm-text-3); } .pk.on { border-color: var(--hm-primary); background: var(--hm-primary-soft); }
.setup-body > .big { margin-top: 18px; width: 100%; }
.chk { margin: 12px 0; padding: 10px 12px; border-radius: var(--hm-r-sm); background: var(--hm-primary-soft); color: var(--hm-setup-ink); }
.chk.bad { background: var(--hm-idle-soft); color: var(--hm-idle-ink); font-weight: 500; }
.qty { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin-top: 10px; } .q label { display: block; color: var(--hm-text-3); margin-bottom: 6px; }
.st { display: flex; height: 64px; border: 1px solid var(--hm-border-strong); border-radius: var(--hm-r-md); overflow: hidden; }
.st button { width: 64px; border: 0; background: var(--hm-surface-2); font-size: 28px; cursor: pointer; }
.st input { flex: 1; min-width: 0; width: 100%; border: 0; text-align: center; font-size: 30px; font-family: var(--hm-font-num); font-weight: 600; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; } .chip { border: 1px solid var(--hm-border-strong); background: var(--hm-surface); border-radius: var(--hm-r-pill); padding: 10px 16px; font: inherit; cursor: pointer; }
.spot { display: flex; gap: 12px; align-items: center; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid var(--hm-hairline); } .sb { display: flex; gap: 8px; flex: none; }
.muted { color: var(--hm-text-3); } .grow { flex: 1; } .small { font-size: var(--hm-fs-xs); }
.pkc { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; padding: 10px 12px; margin-top: 10px; border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); background: var(--hm-surface-2); }
.pkc .pb { flex-basis: 100%; } .pkc b { font-size: var(--hm-fs-lg); } .exc { margin-top: 4px; color: var(--hm-text-2); white-space: pre-wrap; max-height: 120px; overflow: auto; }
</style>
