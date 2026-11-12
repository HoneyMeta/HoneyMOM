<template>
  <div class="mob" data-test="mobile">
    <header class="hd">
      <img src="/brand/mark.svg" alt="" class="mk" /><b>{{ $t('common.honeymom') }}</b>
      <span v-if="!online" class="off" data-test="m-offline">{{ $t('common.offline') }}</span>
      <span class="grow" />
      <span class="me">{{ session.user?.name }}</span>
      <button class="lnk" @click="router.push('/')">{{ $t('mobile.desktop') }}</button>
    </header>

    <main class="body">
      <!-- Scan to report -->
      <section v-if="tab === 'scan'" data-test="m-scan">
        <ScanInput ref="scanRef" big @scan="onScan" />
        <div v-if="scan.err" class="card warn">{{ scan.err }}</div>
        <div v-if="scan.part" class="card part" v-ai="{ kind: 'record', entity: 'part', sn: scan.part.sn, label: $t('mobile.part0', [scan.part.sn]), data: scan.part }">
          <div class="row"><b class="hm-mono">{{ scan.part.sn }}</b><span class="grow" /><HmTag :tone="scan.part.status === 'hold' ? 'bad' : 'neutral'">{{ PST[scan.part.status] || scan.part.status }}</HmTag></div>
          <div class="muted">{{ $t('mobile.wo0Heat1Location', [scan.part.work_order__label || '—', scan.part.heat || '—', scan.part.station || '—']) }}</div>
        </div>
        <template v-if="scan.ops.length">
          <label class="lab">{{ $t('common.operation') }}</label>
          <div class="seg">
            <button v-for="o in scan.ops" :key="o.id" :class="{ on: scan.op?.id === o.id, done: o.status === 'done' }" :data-test="'m-op-' + o.seq" @click="pickOp(o)">{{ o.seq }} {{ o.pname }}</button>
          </div>
          <div v-if="scan.chk" class="card" :class="scan.chk.blockers.length ? 'warn' : 'info'" data-test="m-check">
            <template v-if="scan.chk.blockers.length">{{ $t('mobile.cannotReport0', [scan.chk.blockers.join($t('common.k764'))]) }}</template>
            <template v-else>{{ $t('mobile.maxReportable') }} <b class="hm-num">{{ scan.chk.max }}</b> {{ $t('common.pcs') }}<span v-if="scan.chk.fai_required"> {{ $t('mobile.firstArticleReleased') }}</span></template>
          </div>
          <div class="qty2">
            <div class="q"><label>{{ $t('common.good') }}</label><div class="st"><button @click="rp.good = Math.max(0, rp.good - 1)">−</button><input v-model.number="rp.good" type="number" data-test="m-good" /><button @click="rp.good++">{{ $t('mobile.k4008') }}</button></div></div>
            <div class="q"><label>{{ $t('common.defective') }}</label><div class="st"><button @click="rp.ng = Math.max(0, rp.ng - 1)">−</button><input v-model.number="rp.ng" type="number" /><button @click="rp.ng++">{{ $t('mobile.k4008') }}</button></div></div>
          </div>
          <el-select v-if="rp.ng" v-model="rp.defect" :placeholder="$t('common.defect')" size="large" class="full"><el-option v-for="x in defects" :key="x.id" :label="x.name" :value="x.id" /></el-select>
          <button class="btn primary" :disabled="rp.busy || !scan.op || rp.good + rp.ng <= 0" data-test="m-report" @click="report">{{ rp.busy ? $t('mobile.submitting') : $t('mobile.report') }}</button>
        </template>
        <p v-else-if="!scan.part" class="muted center">{{ $t('mobile.scanAPartCodeOr') }}<br />{{ $t('mobile.barcodeScannerCameraOrPhoto') }}</p>
      </section>

      <!-- My tasks -->
      <section v-else-if="tab === 'task'" data-test="m-task">
        <div v-for="o in tasks" :key="o.id" class="card" v-ai="{ kind: 'row', entity: 'wo_op', id: o.id, label: `${o.work_order__label} ${o.process_name} ${o.qty_good || 0}/${o.qty_plan}`, data: o }">
          <div class="row"><b class="hm-mono">{{ o.work_order__label }}</b><span>{{ o.process_name }}</span><span class="grow" /><HmTag :tone="o.status === 'running' ? 'primary' : 'neutral'">{{ OS[o.status] }}</HmTag></div>
          <div class="muted">{{ o.station_group }} · <span class="hm-num">{{ o.qty_good || 0 }}/{{ o.qty_plan }}</span> · {{ o.mode === 'auto' ? $t('mobile.machineAutoReporting') : $t('mobile.manualReporting') }}</div>
          <div class="pb"><b :style="{ width: Math.min(100, (o.qty_good || 0) / (o.qty_plan || 1) * 100) + '%' }" /></div>
          <div v-if="o.mode !== 'auto'" class="row mt-s">
            <el-input-number v-model="o._n" :min="1" size="large" /><span class="grow" />
            <button class="btn sm primary" :disabled="o._busy" @click="reportOp(o)">{{ $t('mobile.reportGood') }}</button>
          </div>
        </div>
        <HmEmpty v-if="!tasks.length" compact :title="$t('mobile.noPendingTasks')" />
      </section>

      <!-- Lookup -->
      <section v-else-if="tab === 'query'" data-test="m-query">
        <ScanInput big :placeholder="$t('mobile.woNoPartCodeEquipment')" :hint="$t('mobile.scanOrTypePressEnter')" @scan="search" />
        <div v-for="r in found" :key="r.kind + r.id" class="card click" :data-test="'m-found-' + r.kind" @click="openFound(r)">
          <div class="row"><HmTag :tone="r.kind === 'part' ? 'amber' : r.kind === 'work_order' ? 'primary' : 'neutral'">{{ KIND[r.kind] }}</HmTag><b class="hm-mono">{{ r.title }}</b></div>
          <div class="muted">{{ r.sub }}</div>
        </div>
        <div v-if="detail" class="card" data-test="m-detail">
          <template v-if="detail.kind === 'part'">
            <b>{{ $t('mobile.part0', [detail.part.sn]) }}</b><div class="muted">{{ $t('mobile.heat01', [detail.part.heat || '—', detail.part.work_order__label || '']) }}</div>
            <ol class="tl"><li v-for="e in detail.events" :key="e.id"><span class="hm-num">{{ (e.t || '').slice(5, 16) }}</span> {{ e.station }} {{ e.label }}</li></ol>
          </template>
          <template v-else-if="detail.kind === 'work_order'">
            <b>{{ $t('erpMrp.workOrders0', [detail.item.code]) }}</b><div class="muted">{{ $t('mobile.n012Due3', [detail.item.product__label, detail.item.qty_done || 0, detail.item.qty, (detail.item.due || '').slice(0, 16)]) }}</div>
            <div v-for="o in detail.children.wo_op || []" :key="o.id" class="op"><span>{{ o.seq }} {{ (o.process__label || '').split(' ').slice(1).join(' ') }}</span><span class="grow" /><span class="hm-num">{{ o.qty_good }}/{{ o.qty_plan }}</span></div>
          </template>
          <template v-else><b>{{ detail.title }}</b><div class="muted">{{ detail.sub }}</div></template>
        </div>
      </section>

      <!-- Notifications -->
      <section v-else-if="tab === 'notice'" data-test="m-notice">
        <div class="row"><b>{{ $t('common.notifications') }}</b><span class="grow" /><button class="lnk" @click="readAll">{{ $t('topBar.markAllRead') }}</button></div>
        <div v-for="n in notices" :key="n.id" class="card" :class="{ unread: !n.read, warnl: n.level !== 'info' }" @click="n.link && router.push(n.link)">
          <div class="row"><b>{{ n.title }}</b><span class="grow" /><small class="muted hm-num">{{ (n.time || '').slice(5, 16) }}</small></div>
          <div class="muted">{{ n.content }}</div>
        </div>
        <HmEmpty v-if="!notices.length" compact :title="$t('topBar.noNotifications')" />
      </section>

      <!-- Request repair -->
      <section v-else-if="tab === 'repair'" data-test="m-repair">
        <label class="lab">{{ $t('common.equipment') }}</label>
        <el-select v-model="rep.equipment" filterable size="large" class="full"><el-option v-for="e in eqs" :key="e.id" :label="`${e.code} ${e.name}`" :value="e.id" /></el-select>
        <div class="chips"><button v-for="t in FAULTS" :key="t" class="chip" @click="rep.fault = t">{{ t }}</button></div>
        <el-input v-model="rep.fault" type="textarea" :rows="3" :placeholder="$t('common.faultDescription')" />
        <el-radio-group v-model="rep.level" size="large" class="mt-s"><el-radio-button value="normal">{{ $t('common.marginal') }}</el-radio-button><el-radio-button value="urgent">{{ $t('common.urgent') }}</el-radio-button></el-radio-group>
        <button class="btn danger" data-test="m-repair-submit" @click="repair">{{ $t('mobile.submitRepairRequest') }}</button>
      </section>

      <!-- Inspection rounds -->
      <section v-else-if="tab === 'spot'" data-test="m-spot">
        <div v-for="m in due" :key="m.id" class="card"><b>{{ m.name }}</b><div class="muted">{{ m.items }}</div>
          <div class="row mt-s"><button class="btn sm ok" @click="spot(m, 'ok')">{{ $t('common.normal') }}</button><button class="btn sm danger" @click="spot(m, 'abnormal')">{{ $t('common.abnormal') }}</button></div></div>
        <HmEmpty v-if="!due.length" compact :title="$t('mobile.allOfTodaySChecks')" />
      </section>

      <!-- First article -->
      <section v-else-if="tab === 'fai'" data-test="m-fai">
        <div v-for="c in fai" :key="c.wo_op" class="card">
          <div class="row"><b class="hm-mono">{{ c.work_order }}</b><span>{{ c.seq }} {{ c.process }}</span><span class="grow" />
            <HmTag :tone="c.fai_ok ? 'ok' : c.fai?.status === 'pending' ? 'warn' : 'bad'">{{ c.fai_ok ? $t('mobile.released') : c.fai?.status === 'pending' ? $t('mobile.pendingJudgment') : $t('mobile.notReleased') }}</HmTag></div>
          <div class="row mt-s">
            <button v-if="!c.fai_ok && c.fai?.status !== 'pending'" class="btn sm primary" @click="applyFai(c)">{{ $t('common.requestFirstArticle') }}</button>
            <button v-if="c.fai?.status === 'pending'" class="btn sm primary" @click="openFai(c.fai.id)">{{ $t('mobile.enterJudge') }}</button>
          </div>
        </div>
        <HmEmpty v-if="!fai.length" compact :title="$t('mobile.noOperationsNeedFirstArticle')" />
      </section>

      <!-- Scan to pack (5.0 M5: auto-seal and label when a carton is full; scan carton IDs to palletize) -->
      <section v-else-if="tab === 'pack'" data-test="m-pack">
        <div class="card"><div class="row"><span class="muted">{{ $t('common.packingStation') }}</span><el-input v-model="pk.station" size="large" style="max-width: 160px" data-test="m-pack-station" @change="savePk" /></div>
          <ScanInput big :placeholder="$t('mobile.scanPartCodeCartonNo')" :global="tab === 'pack'" data-test="m-pack-scan" @scan="doPack" /></div>
        <div v-if="pk.msg" class="card" :class="pk.bad ? 'err' : 'okc'" data-test="m-pack-msg">{{ pk.msg }}</div>
        <div v-if="pk.state?.carton" class="card" data-test="m-pack-carton">
          <div class="row"><b class="hm-mono">{{ pk.state.carton.code }}</b><span class="grow" /><b class="hm-num">{{ pk.state.carton.qty }} / {{ pk.state.carton.capacity }}</b></div>
          <div class="muted small">{{ $t('mobile.lot0', [pk.state.carton.lots || '—']) }}</div>
          <div class="row mt-s"><button class="btn sm primary" data-test="m-pack-close" @click="closePack(pk.state.carton.code)">{{ $t('common.sealCarton') }}</button></div>
        </div>
        <div v-if="pk.state?.pallet" class="card"><div class="row"><b class="hm-mono">{{ $t('mobile.pallet0', [pk.state.pallet.code]) }}</b><span class="grow" /><span class="hm-num">{{ $t('mobile.n01Cartons', [pk.state.pallet.cartons?.length || 0, pk.state.pallet.capacity]) }}</span></div></div>
      </section>

      <!-- My daily report -->
      <section v-else-if="tab === 'daily'" data-test="m-daily">
        <DailyForm mobile />
      </section>
    </main>

    <nav class="tabbar" data-test="m-tabbar">
      <button v-for="t in MAIN" :key="t.k" :class="{ on: tab === t.k }" :data-test="'m-tab-' + t.k" @click="setTab(t.k)">
        <el-icon><component :is="t.i" /></el-icon><span>{{ t.l }}</span><i v-if="t.k === 'notice' && home.unread" class="dot">{{ home.unread }}</i>
      </button>
      <button :class="{ on: MORE.some((m) => m.k === tab) }" data-test="m-tab-more" @click="more = true"><el-icon><Grid /></el-icon><span>{{ $t('mobile.more') }}</span></button>
    </nav>
    <el-drawer v-model="more" direction="btt" size="auto" :with-header="false" append-to-body>
      <div class="grid4">
        <button v-for="m in MORE" :key="m.k" class="gi" :data-test="'m-more-' + m.k" @click="setTab(m.k); more = false">
          <el-icon><component :is="m.i" /></el-icon><span>{{ m.l }}</span><i v-if="m.k === 'spot' && home.maint_due" class="dot">{{ home.maint_due }}</i>
          <i v-if="m.k === 'fai' && home.fai_pending" class="dot">{{ home.fai_pending }}</i>
        </button>
      </div>
    </el-drawer>
    <el-dialog v-model="fi.open" :title="$t('mobile.firstArticleEntry')" width="96vw" append-to-body>
      <div v-for="i in fi.items" :key="i.id" class="fitem"><div><b>{{ i.name }}</b><div class="muted small">{{ i.std }}</div></div>
        <el-input-number v-if="i.lo != null || i.hi != null" v-model="i.value" :controls="false" :precision="4" size="large" />
        <el-switch v-else v-model="i.ok" :active-text="$t('common.good')" :inactive-text="$t('common.ng')" />
        <InspGaugeSelect v-if="i.gauge_type || i.lo != null || i.hi != null" v-model="i.gauge" :item="i" size="large" class="fgauge" /></div>
      <template #footer><button class="btn primary" :disabled="fi.busy" @click="judge">{{ $t('common.saveJudge') }}</button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// Mobile (PWA): scan to report, my tasks, lookup, notifications, request repair, inspection rounds, first article, my daily report. Light design; can be "Added to Home Screen"; the shell opens offline (APIs still need a network).
import { ElMessage, ElMessageBox } from 'element-plus';
import { onMounted, onUnmounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { call, get, post, session } from '../api';
import { can } from '../perm';
import InspGaugeSelect from '../components/exec/InspGaugeSelect.vue';
import DailyForm from '../components/biz/DailyForm.vue';
import ScanInput from '../components/biz/ScanInput.vue';
import { newKey, submitReport } from '../components/biz/bizkit';

const router = useRouter(), route = useRoute();
const MAIN = [{ k: 'scan', l: $t('mobile.scanToReport'), i: 'Aim' }, { k: 'task', l: $t('mobile.myTasks'), i: 'List' }, { k: 'query', l: $t('common.search'), i: 'Search' }, { k: 'notice', l: $t('common.notifications'), i: 'Bell' }];
const MORE = [{ k: 'daily', l: $t('mobile.myDailyReport'), i: 'Notebook' }, { k: 'repair', l: $t('common.requestRepair'), i: 'SetUp' }, { k: 'spot', l: $t('mobile.checks'), i: 'Checked' }, { k: 'fai', l: $t('common.firstArticle'), i: 'Stamp' },
  ...(can('pack.station') ? [{ k: 'pack', l: $t('mobile.scanToPack'), i: 'Box' }] : [])];
const OS: Record<string, string> = { waiting: $t('common.waitingToStart'), ready: $t('common.readyToStart'), running: $t('common.inProgress'), done: $t('common.done') };
const PST: Record<string, string> = { wip: $t('common.wip'), good: $t('mobile.passedInspection'), fg: $t('mobile.fgStored'), shipped: $t('common.shipped'), scrap: $t('common.scrapped'), raw_store: $t('common.rawCastingStore'), rework: $t('common.inRework'), hold: $t('common.held') };
const KIND: Record<string, string> = { part: $t('common.part'), work_order: $t('common.workOrder'), equipment: $t('common.equipment') };
const FAULTS = [$t('mobile.spindleNoise'), $t('mobile.lowLubricationPressure'), $t('mobile.lowAirPressure'), $t('mobile.toolBreakage'), $t('mobile.looseFixture'), $t('mobile.alarmStop')];
const tab = ref<string>((route.query.tab as string) || 'scan'), more = ref(false), online = ref(navigator.onLine);
const scanRef = ref<any>();
const scan = reactive<any>({ part: null, ops: [], op: null, chk: null, err: '' });
const rp = reactive<any>({ good: 1, ng: 0, defect: null, busy: false, key: newKey('m') });
const tasks = ref<any[]>([]), found = ref<any[]>([]), detail = ref<any>(null), notices = ref<any[]>([]), eqs = ref<any[]>([]), due = ref<any[]>([]), fai = ref<any[]>([]);
const defects = ref<any[]>([]), home = ref<any>({});
const rep = reactive<any>({ equipment: null, fault: '', level: 'normal' });
const fi = reactive<any>({ open: false, id: 0, items: [], busy: false });

function setTab(k: string) { tab.value = k; router.replace({ query: { tab: k } }); loadTab(); }
async function loadTab() {
  if (tab.value === 'task') tasks.value = (await get('/api/production/my_tasks')).map((o: any) => ({ ...o, _n: 1 }));
  if (tab.value === 'notice') notices.value = (await get('/api/notices')).items;
  if (tab.value === 'spot') due.value = await get('/api/equipment/maint_due');
  if (tab.value === 'fai') fai.value = await get('/api/quality/fai/candidates');
  if (tab.value === 'repair' && !eqs.value.length) eqs.value = (await get('/api/e/equipment?size=200&sort=code')).items.filter((e: any) => !['crew', 'agv'].includes(e.etype));
}
// ---- Scan to report ----
async function onScan(code: string) {
  Object.assign(scan, { part: null, ops: [], op: null, chk: null, err: '' });
  rp.key = newKey('m'); rp.good = 1; rp.ng = 0;
  let woId: number | null = null;
  try {
    const t = await get('/api/trace/part/' + encodeURIComponent(code), true);
    scan.part = t.part; woId = t.part.work_order;
  } catch {
    const w = (await get('/api/e/work_order?f_code=' + encodeURIComponent(code), true).catch(() => ({ items: [] }))).items[0];
    if (!w) { scan.err = $t('mobile.partCodeOrWorkOrder', [code]); return; }
    woId = w.id;
  }
  if (!woId) { scan.err = $t('mobile.thisPartHasNoLinked'); return; }
  const wo = await get('/api/e/work_order/' + woId);
  scan.ops = (wo.children.wo_op || []).map((o: any) => ({ ...o, pname: (o.process__label || '').split(' ').slice(1).join(' ') }));
  // Default to the operation matching the part's current workstation, then in-progress, then incomplete ones
  // A part's station is the equipment code (CNC3, FUR1) while operations use the workstation group (CNC, FUR): code = group + digits counts as the same
  const st = String(scan.part?.station || '');
  const inGroup = (g: string) => !!g && (st === g || (st.startsWith(g) && /^\d+$/.test(st.slice(g.length))));
  const here = st ? scan.ops.find((o: any) => inGroup(o.station_group) && o.status !== 'done') : null;
  const nxt = here || scan.ops.find((o: any) => o.status === 'running' && o.qty_good < o.qty_plan) || scan.ops.find((o: any) => o.status !== 'done');
  if (nxt) pickOp(nxt);
}
async function pickOp(o: any) {
  scan.op = o; scan.chk = null;
  try { scan.chk = await post('/api/production/report/check', { wo_op: o.id, sn: scan.part?.sn }); } catch { /* */ }
}
async function report() {
  if (rp.busy) return;
  rp.busy = true;
  try {
    const r = await submitReport({ wo_op: scan.op.id, sn: scan.part?.sn, qty_good: rp.good, qty_ng: rp.ng, defect: rp.defect, source: 'mobile' }, rp.key);
    if (r) { rp.key = newKey('m'); await pickOp(scan.op); }
  } finally { rp.busy = false; }
}
async function reportOp(o: any) {
  o._busy = true;
  try { const r = await submitReport({ wo_op: o.id, qty_good: o._n || 1, source: 'mobile' }, o._key ||= newKey('m')); if (r) { o._key = ''; await loadTab(); } } finally { o._busy = false; }
}
// ---- Lookup ----
async function search(q: string) { detail.value = null; found.value = (await get('/api/m/search?q=' + encodeURIComponent(q))).items; if (found.value.length === 1) openFound(found.value[0]); }
async function openFound(r: any) {
  if (r.kind === 'part') { const t = await get('/api/trace/part/' + encodeURIComponent(r.title)); detail.value = { kind: 'part', ...t }; }
  else if (r.kind === 'work_order') detail.value = { kind: 'work_order', ...(await get('/api/e/work_order/' + r.id)) };
  else detail.value = { kind: r.kind, title: r.title, sub: r.sub };
}
async function readAll() { await post('/api/notices/read', {}); await loadTab(); home.value.unread = 0; }
// ---- Request repair / inspection rounds / first article ----
async function repair() {
  if (!rep.equipment || !rep.fault) { ElMessage.warning($t('mobile.selectAMachineAndDescribe')); return; }
  await post('/api/e/repair_order', { ...rep, reported_by: session.user?.name, reported_at: null }); ElMessage.success($t('mobile.repairRequestedTheMaintenanceTechnic')); rep.fault = '';
}
async function spot(m: any, result: string) {
  const notes = result === 'abnormal' ? (await ElMessageBox.prompt($t('mobile.abnormalityDescription'), m.name)).value : '';
  const r = await post(`/api/e/maint_plan/${m.id}/do/execute`, { result, notes }); ElMessage.success(r.msg || $t('common.recorded')); await loadTab();
}
async function applyFai(c: any) { const r = await post('/api/quality/fai', { wo_op: c.wo_op }); ElMessage.success(r.msg); await loadTab(); }
async function openFai(id: number) {
  const d = await get('/api/quality/fai/' + id);
  fi.items = d.items.map((i: any) => ({ ...i, value: i.lo != null && i.hi != null ? +((i.lo + i.hi) / 2).toFixed(4) : null, ok: true })); fi.id = id; fi.open = true;
}
async function judge() {
  fi.busy = true;
  try { const r = await post(`/api/quality/fai/${fi.id}/inspect`, { values: fi.items.map((i: any) => ({ item: i.id, value: i.lo != null || i.hi != null ? i.value : null, ok: i.ok, gauge: i.gauge || null })) });
    ElMessage({ type: r.status === 'approved' ? 'success' : 'warning', message: r.msg }); fi.open = false; await loadTab(); } finally { fi.busy = false; }
}
// ---- Scan to pack ----
const pk = reactive<any>({ station: (() => { try { return localStorage.getItem('hm_m_pack_station') || 'PACK1'; } catch { return 'PACK1'; } })(), state: null, msg: '', bad: false });
function savePk() { try { localStorage.setItem('hm_m_pack_station', pk.station); } catch { /* */ } }
async function doPack(code: string) {
  try { const r = await call('POST', '/api/pack/scan', { code, station: pk.station }, true); pk.msg = r.msg; pk.bad = false; pk.state = r.state; }
  catch (e: any) { pk.msg = e?.message || String(e); pk.bad = true; }
}
async function closePack(code: string) { const r = await post('/api/pack/close', { code }); pk.msg = r.msg; pk.bad = false; pk.state = r.state; }
// ---- PWA ----
function pwa() {
  if (!document.querySelector('link[rel=manifest]')) {
    const l = document.createElement('link'); l.rel = 'manifest'; l.href = '/manifest.webmanifest'; document.head.appendChild(l);
    const t = document.createElement('meta'); t.name = 'theme-color'; t.content = '#ffffff'; document.head.appendChild(t);
    const a = document.createElement('link'); a.rel = 'apple-touch-icon'; a.href = '/brand/apple-touch-icon.png'; document.head.appendChild(a);
  }
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).then(async () => {
      const reg = await navigator.serviceWorker.ready;
      const urls = performance.getEntriesByType('resource').map((e) => new URL(e.name).pathname).filter((p) => /^\/(assets|vendor|brand)\//.test(p));
      reg.active?.postMessage({ type: 'cache', urls: [...new Set(urls)] });
    }).catch(() => undefined);
  }
}
const onNet = () => { online.value = navigator.onLine; };
onMounted(async () => {
  pwa();
  window.addEventListener('online', onNet); window.addEventListener('offline', onNet);
  defects.value = (await get('/api/e/defect_code?size=50&sort=code')).items;
  home.value = await get('/api/m/home');
  await loadTab();
  if (route.query.sn) onScan(String(route.query.sn));
});
onUnmounted(() => { window.removeEventListener('online', onNet); window.removeEventListener('offline', onNet); });
</script>

<style scoped>
.mob { max-width: 600px; margin: 0 auto; overflow-x: clip; min-height: 100vh; background: var(--hm-canvas); display: flex; flex-direction: column; color: var(--hm-text); font-size: 15px; }
.hd { position: sticky; top: 0; z-index: 5; display: flex; align-items: center; gap: 8px; height: 52px; padding: 0 14px; background: var(--hm-surface); border-bottom: 1px solid var(--hm-border); }
.hd .mk { height: 26px; } .hd b { font-size: 16px; } .me { color: var(--hm-text-2); font-size: 13px; }
.off { background: var(--hm-idle-soft); color: var(--hm-idle-ink); border-radius: 10px; padding: 1px 8px; font-size: 12px; }
.lnk { border: 0; background: none; color: var(--hm-primary); font: inherit; font-size: 13px; cursor: pointer; padding: 6px; }
.body { flex: 1; padding: 12px 14px 90px; display: flex; flex-direction: column; min-width: 0; }
section { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
section > * { min-width: 0; max-width: 100%; }
.card { background: var(--hm-surface); border: 1px solid var(--hm-border); border-radius: var(--hm-r-lg); padding: 12px; box-shadow: var(--hm-e1); }
.card.warn { background: var(--hm-idle-soft); border-color: var(--hm-idle); color: var(--hm-idle-ink); } .card.info { background: var(--hm-primary-soft); border-color: var(--hm-primary-soft-2); }
.card.unread { border-left: 3px solid var(--hm-primary); } .card.warnl { border-left-color: var(--hm-amber); } .card.click { cursor: pointer; }
.row { display: flex; align-items: center; gap: 8px; } .grow { flex: 1; }
.muted { color: var(--hm-text-3); font-size: 13px; } .small { font-size: 12px; } .center { text-align: center; margin-top: 40px; line-height: 1.8; }
.lab { font-size: 13px; color: var(--hm-text-3); }
.seg { display: flex; gap: 6px; overflow-x: auto; padding-bottom: 2px; }
.seg button { flex: none; border: 1px solid var(--hm-border-strong); background: var(--hm-surface); border-radius: var(--hm-r-pill); padding: 10px 14px; font: inherit; font-size: 14px; cursor: pointer; }
.seg button.on { background: var(--hm-primary); border-color: var(--hm-primary); color: #fff; } .seg button.done { color: var(--hm-text-3); }
.qty2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; } .q { min-width: 0; } .q label { font-size: 13px; color: var(--hm-text-3); }
.st { display: flex; height: 56px; border: 1px solid var(--hm-border-strong); border-radius: var(--hm-r-md); overflow: hidden; background: var(--hm-surface); margin-top: 4px; }
.st button { width: 48px; flex: none; border: 0; background: var(--hm-surface-2); font-size: 24px; } .st input { flex: 1; min-width: 0; width: 100%; border: 0; text-align: center; font-size: 24px; font-weight: 600; font-family: var(--hm-font-num); }
.btn { min-height: 52px; border-radius: var(--hm-r-md); border: 1px solid var(--hm-border-strong); background: var(--hm-surface); font: inherit; font-size: 17px; font-weight: 600; cursor: pointer; }
.btn.primary { background: var(--hm-primary); border-color: var(--hm-primary); color: #fff; } .btn.danger { background: var(--hm-fault); border-color: var(--hm-fault); color: #fff; }
.btn.ok { background: var(--hm-run); border-color: var(--hm-run); color: #fff; } .btn:disabled { opacity: .5; }
.btn.sm { min-height: 42px; font-size: 15px; padding: 0 18px; }
.full { width: 100%; } .mt-s { margin-top: 8px; }
.pb { height: 6px; background: var(--hm-surface-3); border-radius: 3px; margin-top: 8px; overflow: hidden; } .pb b { display: block; height: 100%; background: var(--hm-primary); }
.tl { padding-left: 18px; margin: 8px 0 0; font-size: 13px; line-height: 1.8; } .op { display: flex; padding: 6px 0; border-bottom: 1px solid var(--hm-hairline); font-size: 14px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; } .chip { border: 1px solid var(--hm-border-strong); background: var(--hm-surface); border-radius: var(--hm-r-pill); padding: 8px 12px; font: inherit; font-size: 14px; }
.tabbar { position: fixed; bottom: 0; left: 50%; transform: translateX(-50%); width: min(600px, 100%); display: grid; grid-template-columns: repeat(5, 1fr); background: var(--hm-glass-strong);
  backdrop-filter: var(--hm-glass-blur); border-top: 1px solid var(--hm-border); padding-bottom: env(safe-area-inset-bottom); z-index: 10; }
.tabbar button { position: relative; display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 8px 0 6px; border: 0; background: none; font: inherit; font-size: 11px; color: var(--hm-text-3); cursor: pointer; }
.tabbar button .el-icon { font-size: 22px; } .tabbar button.on { color: var(--hm-primary); }
.dot { position: absolute; top: 4px; right: 50%; transform: translateX(18px); background: var(--hm-fault); color: #fff; border-radius: 9px; font-size: 10px; padding: 0 5px; font-style: normal; line-height: 16px; }
.grid4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; padding: 16px; }
.gi { position: relative; display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 14px 0; border: 1px solid var(--hm-border); border-radius: var(--hm-r-lg); background: var(--hm-surface); font: inherit; font-size: 13px; }
.gi .el-icon { font-size: 26px; color: var(--hm-primary); }
.fitem { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--hm-hairline); }
.fitem .fgauge { flex-basis: 100%; }
.card.err { background: var(--hm-idle-soft); color: var(--hm-idle-ink); font-weight: 500; } .card.okc { background: var(--hm-run-soft); color: var(--hm-run-ink); }
</style>
