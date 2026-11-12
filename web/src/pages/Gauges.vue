<template>
  <div class="page gg">
    <HmPageHead :title="$t('common.gauges')" :sub="$t('gauges.gaugesLockAutomaticallyWhenCalibrati')">
      <el-button v-perm:disable="'gauge.gauge.create'" type="primary" data-test="g-new" @click="openNew"><el-icon><Plus /></el-icon>{{ $t('gauges.newGauge') }}</el-button>
    </HmPageHead>

    <div class="grid g6" data-test="g-kpis">
      <HmKpi :label="$t('gauges.totalGauges')" :value="board.total" format="int" :unit="$t('gauges.k3208')" />
      <HmKpi :label="$t('adminApiKeys.active')" :value="cnt('active')" format="int" :unit="$t('gauges.k3208')" status="ok" />
      <HmKpi :label="$t('gauges.dueSoon')" :value="cnt('due')" format="int" :unit="$t('gauges.k3208')" :status="cnt('due') ? 'warn' : ''" :hint="$t('gauges.dueWithin0Days', [board.due_days || 30])" />
      <HmKpi :label="$t('gauges.expiredLocked')" :value="cnt('overdue')" format="int" :unit="$t('gauges.k3208')" :status="cnt('overdue') ? 'bad' : ''" />
      <HmKpi :label="$t('gauges.pendingRelease')" :value="cnt('quarantine')" format="int" :unit="$t('gauges.k3208')" :status="cnt('quarantine') ? 'warn' : ''" :hint="$t('gauges.calibratedAwaitingESignatureRelease')" />
      <HmKpi :label="$t('gauges.lockedInRepair')" :value="cnt('locked') + cnt('repair')" format="int" :unit="$t('gauges.k3208')" :status="cnt('locked') + cnt('repair') ? 'bad' : ''" />
    </div>

    <HmToolbar class="mt">
      <el-radio-group v-model="fs" size="small" data-test="g-status">
        <el-radio-button value="">{{ $t('common.all0', [board.total]) }}</el-radio-button>
        <el-radio-button v-for="(t, k) in ST" v-show="k === 'active' || cnt(k)" :key="k" :value="k">{{ t }} {{ cnt(k) }}</el-radio-button>
      </el-radio-group>
      <el-select v-model="ft" clearable :placeholder="$t('common.type')" size="small" style="width: 120px">
        <el-option v-for="(t, k) in GT" :key="k" :value="k" :label="t" />
      </el-select>
      <el-input v-model="q" clearable size="small" :placeholder="$t('gauges.codeNameLocationCustodian')" style="width: 220px" />
      <template #right><span class="muted small">{{ $t('gauges.sortByNextDue') }}</span></template>
    </HmToolbar>

    <HmCard flush class="mt">
      <el-table :data="rows" size="small" data-test="g-table" class="click" @row-click="(r: any) => openGauge(r.id)"
        v-ai-rows="{ entity: 'gauge', rows: () => rows, label: (r: any) => $t('gauges.gauge012', [r.code, r.name, ST[r.status] || r.status]), data: (r: any) => ({ [$t('common.no2')]: r.code, [$t('common.name')]: r.name, [$t('common.type')]: GT[r.gtype], [$t('gauges.nextDue')]: r.next_due, [$t('gauges.daysLeft')]: r.days_left, [$t('common.status')]: ST[r.status], [$t('common.reason')]: r.lock_reason, GRR: r.grr_pct }) }">
        <el-table-column :label="$t('common.no2')" width="120"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
        <el-table-column prop="name" :label="$t('common.name')" min-width="150" />
        <el-table-column :label="$t('common.type')" width="80"><template #default="{ row }">{{ GT[row.gtype] || row.gtype }}</template></el-table-column>
        <el-table-column prop="spec" :label="$t('gauges.range')" min-width="110" />
        <el-table-column prop="location" :label="$t('gauges.location')" width="96" />
        <el-table-column prop="owner" :label="$t('gauges.custodian')" width="76" />
        <el-table-column :label="$t('gauges.lastCalibrated')" width="100"><template #default="{ row }"><span class="hm-num">{{ row.last_cal || '—' }}</span></template></el-table-column>
        <el-table-column :label="$t('gauges.nextDue')" width="150"><template #default="{ row }">
          <span class="hm-num">{{ row.next_due || '—' }}</span>
          <span v-if="row.days_left != null" class="hm-num dl" :class="dueCls(row)">{{ row.days_left < 0 ? $t('gauges.n0DaysOverdue', [-row.days_left]) : $t('common.n0Days', [row.days_left]) }}</span>
        </template></el-table-column>
        <el-table-column :label="$t('common.status')" min-width="170"><template #default="{ row }">
          <HmTag :tone="TONE[row.status] || 'neutral'">{{ ST[row.status] || row.status }}</HmTag>
          <div v-if="row.lock_reason" class="muted small why">{{ row.lock_reason }}</div>
        </template></el-table-column>
        <el-table-column label="%GRR / ndc" width="104" align="right"><template #default="{ row }">
          <span v-if="row.grr_pct != null" class="hm-num" :class="grrCls(row.grr_pct)">{{ fmtNum(row.grr_pct, 1) }}%</span><span v-else class="muted">—</span>
          <span v-if="row.ndc != null" class="hm-num muted"> / {{ row.ndc }}</span>
        </template></el-table-column>
      </el-table>
      <HmEmpty v-if="!rows.length && loaded" plain :title="board.total ? $t('gauges.noMatchingGauges') : $t('gauges.noGaugesYet')" />
    </HmCard>

    <!-- Gauge detail -->
    <el-drawer v-model="dr.open" size="min(760px, 100%)" :title="dr.g ? `${dr.g.code} ${dr.g.name}` : ''" append-to-body destroy-on-close>
      <div v-if="dr.g" data-test="g-drawer" class="drw">
        <div class="st-line">
          <HmTag :tone="TONE[dr.g.status] || 'neutral'">{{ ST[dr.g.status] || dr.g.status }}</HmTag>
          <span v-if="dr.g.lock_reason" class="small t2">{{ dr.g.lock_reason }}</span>
        </div>
        <el-descriptions :column="3" size="small" border class="mt-s">
          <el-descriptions-item :label="$t('common.type')">{{ GT[dr.g.gtype] || dr.g.gtype }}</el-descriptions-item>
          <el-descriptions-item :label="$t('gauges.rangeSpec')">{{ dr.g.spec || '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('gauges.resolution')"><span class="hm-num">{{ dr.g.resolution ?? '—' }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('gauges.location')">{{ dr.g.location || '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('gauges.custodian')">{{ dr.g.owner || '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('gauges.calibrationInterval')"><span class="hm-num">{{ dr.g.cal_interval_days ?? '—' }}</span> {{ $t('common.days2') }}</el-descriptions-item>
          <el-descriptions-item :label="$t('gauges.lastCalibrated')"><span class="hm-num">{{ dr.g.last_cal || '—' }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('gauges.nextDue')"><span class="hm-num" :class="dueCls(dr.g)">{{ dr.g.next_due || '—' }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('gauges.certificateNo')"><span class="hm-mono">{{ dr.g.cert_no || '—' }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('gauges.serialNo')"><span class="hm-mono">{{ dr.g.serial || '—' }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('gauges.manufacturer')">{{ dr.g.maker || '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('gauges.release')">{{ dr.g.released_by || '—' }} <span class="hm-num t3">{{ fmtTime(dr.g.released_at) }}</span></el-descriptions-item>
          <el-descriptions-item label="%GRR"><span class="hm-num" :class="grrCls(dr.g.grr_pct)">{{ dr.g.grr_pct != null ? fmtNum(dr.g.grr_pct, 1) + '%' : '—' }}</span></el-descriptions-item>
          <el-descriptions-item label="ndc"><span class="hm-num">{{ dr.g.ndc ?? '—' }}</span></el-descriptions-item>
        </el-descriptions>

        <div class="acts">
          <el-button v-perm:disable="'gauge.gauge_cal.create'" type="primary" data-test="g-cal-new" @click="openCal"><el-icon><EditPen /></el-icon>{{ $t('gauges.logCalibration') }}</el-button>
          <el-button v-perm:disable="'gauge.gauge.release'" type="success" :disabled="!canRelease" :title="canRelease ? '' : $t('gauges.onlyCalibratedPendingReleaseGauges')" :loading="busy === 'release'" data-test="g-release" @click="act('release')">{{ $t('gauges.releaseESignature') }}</el-button>
          <el-button v-perm:disable="'gauge.gauge.lock'" :disabled="dr.g.status === 'scrapped' || dr.g.status === 'locked'" @click="lock">{{ $t('gauges.lock') }}</el-button>
          <el-button v-perm:disable="'gauge.gauge.scrap'" type="danger" plain :disabled="dr.g.status === 'scrapped'" @click="scrap">{{ $t('common.scrapped') }}</el-button>
          <el-button :loading="busy === 'label'" data-test="g-label" @click="printLabel"><el-icon><Printer /></el-icon>{{ $t('gauges.printGaugeLabel') }}</el-button>
        </div>

        <h4 class="sec">{{ $t('gauges.calibrationRecords') }}</h4>
        <el-table :data="dr.cals" size="small" data-test="g-cals">
          <el-table-column :label="$t('gauges.calibrationNo')" width="128"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
          <el-table-column :label="$t('common.date')" width="96"><template #default="{ row }"><span class="hm-num">{{ row.cal_date }}</span></template></el-table-column>
          <el-table-column :label="$t('common.conclusion')" width="104"><template #default="{ row }"><HmTag :tone="row.result === 'fail' ? 'bad' : row.result === 'adjust' ? 'warn' : 'ok'">{{ RES[row.result] || row.result }}</HmTag></template></el-table-column>
          <el-table-column :label="$t('gauges.methodProvider')" min-width="120"><template #default="{ row }">{{ AG[row.agency] || row.agency || '' }} {{ row.provider || '' }}</template></el-table-column>
          <el-table-column :label="$t('gauges.certificate')" min-width="120"><template #default="{ row }">
            <span class="hm-mono">{{ row.cert_no || '—' }}</span>
            <a v-if="row.cert_file" class="lnk" :href="`/api/gauge/cal/${row.id}/cert?token=${tok()}`" target="_blank" rel="noopener">{{ $t('common.view') }}</a>
          </template></el-table-column>
          <el-table-column :label="$t('gauges.nextDue')" width="96"><template #default="{ row }"><span class="hm-num">{{ row.next_due || '—' }}</span></template></el-table-column>
          <el-table-column prop="by" :label="$t('gauges.calibratedBy')" width="72" />
        </el-table>
        <HmEmpty v-if="!dr.cals.length" plain :title="$t('gauges.noCalibrationRecordsYet')" />

        <h4 class="sec">{{ $t('gauges.msaStudies') }}</h4>
        <el-table v-if="dr.msas.length" :data="dr.msas" size="small" class="click" @row-click="(r: any) => router.push('/iatf/msa?id=' + r.id)">
          <el-table-column :label="$t('common.no2')" width="130"><template #default="{ row }"><span class="hm-mono lnk">{{ row.code }}</span></template></el-table-column>
          <el-table-column prop="title" :label="$t('common.name')" min-width="160" />
          <el-table-column label="%GRR" width="80" align="right"><template #default="{ row }"><span class="hm-num">{{ row.grr_pct != null ? fmtNum(row.grr_pct, 1) + '%' : '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('common.conclusion')" width="110"><template #default="{ row }"><HmTag v-if="row.verdict" :tone="VT[row.verdict]?.[1] || 'neutral'">{{ VT[row.verdict]?.[0] || row.verdict }}</HmTag></template></el-table-column>
        </el-table>
        <HmEmpty v-else plain :title="$t('gauges.noMsaStudiesYet')">
          <el-button link type="primary" @click="router.push('/iatf/msa')">{{ $t('gauges.createInMsa') }}</el-button>
        </HmEmpty>
      </div>
    </el-drawer>

    <!-- Log calibration -->
    <el-dialog v-model="cf.open" :title="dr.g ? $t('gauges.logCalibration0', [dr.g.code]) : $t('gauges.logCalibration2')" width="min(600px, 96vw)" append-to-body destroy-on-close>
      <el-form label-width="96px" size="default" data-test="g-cal-form">
        <el-form-item :label="$t('gauges.calibrationDate')" required><el-date-picker v-model="cf.cal_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
        <el-form-item :label="$t('common.conclusion')" required>
          <el-radio-group v-model="cf.result"><el-radio-button value="pass">{{ $t('common.good') }}</el-radio-button><el-radio-button value="adjust">{{ $t('gauges.passAfterAdjustment') }}</el-radio-button><el-radio-button value="fail">{{ $t('common.ng') }}</el-radio-button></el-radio-group>
        </el-form-item>
        <el-form-item :label="$t('common.method')"><el-radio-group v-model="cf.agency"><el-radio value="internal">{{ $t('gauges.internal') }}</el-radio><el-radio value="external">{{ $t('gauges.external') }}</el-radio></el-radio-group></el-form-item>
        <el-form-item :label="$t('gauges.calibrationProvider')"><el-input v-model="cf.provider" :placeholder="$t('gauges.eGProvincialMetrologyInstitute')" /></el-form-item>
        <el-form-item :label="$t('gauges.certificateNo')"><el-input v-model="cf.cert_no" /></el-form-item>
        <el-form-item :label="$t('gauges.nextDue')"><el-date-picker v-model="cf.next_due" type="date" value-format="YYYY-MM-DD" :placeholder="$t('gauges.leaveEmptyToDeriveFrom')" style="width: 100%" /></el-form-item>
        <el-form-item :label="$t('gauges.calibrationData')"><el-input v-model="cf.readings" type="textarea" :rows="3" :placeholder="$t('gauges.indicationErrorReadingsAtEach')" /></el-form-item>
        <el-form-item :label="$t('gauges.certificateFile')">
          <input ref="fileEl" type="file" accept=".pdf,.png,.jpg,.jpeg" class="file" @change="pickFile" />
          <span v-if="cf.file" class="small t2">{{ cf.file.name }}</span>
        </el-form-item>
      </el-form>
      <p class="small t2">{{ $t('gauges.passPassAfterAdjustmentStatus') }}</p>
      <template #footer>
        <el-button @click="cf.open = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="busy === 'cal'" :disabled="!cf.cal_date" data-test="g-cal-save" @click="saveCal">{{ $t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- New gauge -->
    <el-dialog v-model="nf.open" :title="$t('gauges.newGauge2')" width="min(600px, 96vw)" append-to-body destroy-on-close>
      <el-form label-width="96px" data-test="g-new-form">
        <el-form-item :label="$t('common.no2')" required><el-input v-model="nf.code" :placeholder="$t('gauges.eGGCal05')" /></el-form-item>
        <el-form-item :label="$t('common.name')" required><el-input v-model="nf.name" /></el-form-item>
        <el-form-item :label="$t('common.type')"><el-select v-model="nf.gtype" style="width: 100%"><el-option v-for="(t, k) in GT" :key="k" :value="k" :label="t" /></el-select></el-form-item>
        <el-form-item :label="$t('gauges.rangeSpec')"><el-input v-model="nf.spec" placeholder="0–150 / 0.01" /></el-form-item>
        <el-form-item :label="$t('gauges.location')"><el-input v-model="nf.location" /></el-form-item>
        <el-form-item :label="$t('gauges.custodian')"><el-input v-model="nf.owner" /></el-form-item>
        <el-form-item :label="$t('gauges.calibrationInterval')"><el-input-number v-model="nf.cal_interval_days" :min="1" :max="3650" :controls="false" style="width: 120px" />{{ $t('common.days') }}</el-form-item>
        <el-form-item :label="$t('gauges.serialNo')"><el-input v-model="nf.serial" /></el-form-item>
      </el-form>
      <p class="small t2">{{ $t('gauges.newGaugesStartAsPending') }}</p>
      <template #footer>
        <el-button @click="nf.open = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="busy === 'new'" :disabled="!nf.code || !nf.name" data-test="g-new-save" @click="saveNew">{{ $t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t, lang } from '../i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ent, get, post, session, tok } from '../api';
import { fmtNum, fmtTime } from '../design';

const router = useRouter(), route = useRoute();
const ST: Record<string, string> = { active: $t('adminApiKeys.active'), due: $t('gauges.dueSoon'), overdue: $t('gauges.expiredLocked'), quarantine: $t('gauges.pendingRelease'), locked: $t('gauges.lock'), repair: $t('gauges.repair'), scrapped: $t('common.scrapped') };
const TONE: Record<string, any> = { active: 'ok', due: 'warn', overdue: 'bad', quarantine: 'primary', locked: 'bad', repair: 'warn', scrapped: 'neutral' };
const RES: Record<string, string> = { pass: $t('common.good'), adjust: $t('gauges.passAfterAdjustment'), fail: $t('common.ng') };
const AG: Record<string, string> = { internal: $t('gauges.internal'), external: $t('gauges.external') };
const VT: Record<string, [string, any]> = { acceptable: [$t('common.acceptable'), 'ok'], conditional: [$t('common.conditionallyAcceptable'), 'warn'], unacceptable: [$t('common.unacceptable'), 'bad'] };
const GT = computed<Record<string, string>>(() => ent('gauge')?.fields?.find((f: any) => f.name === 'gtype')?.enum
  || { caliper: $t('gauges.caliper'), micrometer: $t('gauges.micrometer'), bore: $t('gauges.boreGauge'), dial: $t('gauges.dialIndicator'), cmm: $t('gauges.cmm'), plug: $t('gauges.plugGauge'), ring: $t('gauges.ringGauge'), thread: $t('gauges.threadGauge'), other: $t('common.other') });

const board = ref<any>({ counts: {}, total: 0, gauges: [], due_days: 30 });
const loaded = ref(false);
const fs = ref(''), ft = ref(''), q = ref('');
const busy = ref('');
const cnt = (k: string) => board.value.counts?.[k] || 0;
const rows = computed(() => {
  const s = q.value.trim().toLowerCase();
  return (board.value.gauges || []).filter((g: any) => (!fs.value || g.status === fs.value) && (!ft.value || g.gtype === ft.value)
    && (!s || [g.code, g.name, g.location, g.owner, g.spec].some((x) => String(x || '').toLowerCase().includes(s))));
});
const dueDays = () => board.value.due_days || 30;
function daysLeft(g: any) {
  if (g.days_left != null) return g.days_left;
  if (!g.next_due) return null;
  return Math.round((new Date(g.next_due).getTime() - new Date((session.now || new Date().toISOString()).slice(0, 10)).getTime()) / 864e5);
}
function dueCls(g: any) { const d = daysLeft(g); return d == null ? '' : d < 0 ? 'bad' : d <= dueDays() ? 'warn' : ''; }
const grrCls = (v: any) => (v == null ? '' : v > 30 ? 'bad' : v > 10 ? 'warn' : 'good');

async function load() {
  board.value = await get('/api/gauge/board');
  loaded.value = true;
}

// ---------- Detail ----------
const dr = reactive<any>({ open: false, g: null, cals: [], msas: [] });
const canRelease = computed(() => !!dr.g && ['quarantine', 'locked', 'repair'].includes(dr.g.status));
async function openGauge(id: number) {
  const d = await get(`/api/e/gauge/${id}`);
  dr.g = d.item; dr.cals = d.children?.gauge_cal || []; dr.msas = d.children?.msa_study || []; dr.open = true;
  if (String(route.query.id || '') !== String(id)) router.replace({ query: { ...route.query, id } });
}
async function refreshAll() {
  await load();
  if (dr.g) { const d = await get(`/api/e/gauge/${dr.g.id}`); dr.g = d.item; dr.cals = d.children?.gauge_cal || []; dr.msas = d.children?.msa_study || []; }
}
async function act(a: string, body: any = {}) {
  busy.value = a;
  try { const r = await post(`/api/e/gauge/${dr.g.id}/do/${a}`, body); ElMessage.success(r.msg || $t('common.done')); await refreshAll(); } catch { /* already notified */ } finally { busy.value = ''; }
}
async function lock() {
  const { value } = await ElMessageBox.prompt($t('gauges.lockReasonEGDropped'), $t('gauges.lock0', [dr.g.code]), { inputValidator: (v: string) => !!v?.trim() || $t('common.pleaseEnterAReason') }).catch(() => ({ value: null } as any));
  if (value == null) return;
  await act('lock', { reason: value.trim() });
}
async function scrap() {
  const { value } = await ElMessageBox.prompt($t('common.scrapReason'), $t('gauges.scrap0Irreversible', [dr.g.code]), { inputValue: $t('gauges.damagedBeyondRepair'), confirmButtonText: $t('common.scrapped'), type: 'warning' }).catch(() => ({ value: null } as any));
  if (value == null) return;
  await act('scrap', { reason: value });
}
async function printLabel() {
  busy.value = 'label';
  const w = window.open('', '_blank');
  try {
    const tp = (await get('/api/e/label_template?f_code=LBL-GAUGE')).items?.[0];
    if (!tp) { w?.close(); ElMessage.warning($t('gauges.noGaugeLabelTemplateCode')); return; }
    const r = await post('/api/label/print', { template: tp.id, ref: dr.g.id });
    if (!r.pdf) { w?.close(); ElMessage.success(r.msg || $t('gauges.sentToPrinter')); return; }
    const res = await fetch(r.pdf, { headers: { 'X-Lang': lang, Authorization: 'Bearer ' + session.token } });
    if (!res.ok) throw new Error($t('gauges.failedToGenerateLabelPdf'));
    const url = URL.createObjectURL(await res.blob());
    if (w) w.location.href = url; else window.open(url, '_blank');
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (e: any) { w?.close(); if (e?.message) ElMessage.error(e.message); } finally { busy.value = ''; }
}

// ---------- Log calibration ----------
const today = () => (session.now || new Date().toISOString()).slice(0, 10);
const cf = reactive<any>({ open: false, cal_date: '', result: 'pass', agency: 'external', provider: '', cert_no: '', next_due: '', readings: '', file: null });
const fileEl = ref<HTMLInputElement>();
function openCal() { Object.assign(cf, { open: true, cal_date: today(), result: 'pass', agency: 'external', provider: '', cert_no: '', next_due: '', readings: '', file: null }); }
function pickFile(ev: Event) { cf.file = (ev.target as HTMLInputElement).files?.[0] || null; }
const b64 = (f: File) => new Promise<string>((ok, no) => { const r = new FileReader(); r.onload = () => ok(String(r.result)); r.onerror = no; r.readAsDataURL(f); });
async function saveCal() {
  busy.value = 'cal';
  try {
    const body: any = { gauge: dr.g.id, cal_date: cf.cal_date, result: cf.result, agency: cf.agency, provider: cf.provider || null, cert_no: cf.cert_no || null, readings: cf.readings || null };
    if (cf.next_due) body.next_due = cf.next_due;
    const r = await post('/api/e/gauge_cal', body);
    if (cf.file && r.id) await post(`/api/gauge/cal/${r.id}/cert`, { name: cf.file.name, data: await b64(cf.file) });
    ElMessage.success(cf.result === 'fail' ? $t('gauges.loggedCalibrationFailedGaugeLocked') : $t('gauges.loggedGaugePendingReleaseUsable'));
    cf.open = false; await refreshAll();
  } catch { /* already notified */ } finally { busy.value = ''; }
}

// ---------- New ----------
const nf = reactive<any>({ open: false });
function openNew() { Object.assign(nf, { open: true, code: '', name: '', gtype: 'caliper', spec: '', location: '', owner: '', cal_interval_days: 365, serial: '' }); }
async function saveNew() {
  busy.value = 'new';
  try {
    const { open, ...body } = nf; void open;
    const r = await post('/api/e/gauge', body);
    ElMessage.success($t('gauges.added0', [nf.code])); nf.open = false; await load(); if (r.id) openGauge(r.id);
  } catch { /* already notified */ } finally { busy.value = ''; }
}

watch(() => dr.open, (o) => { if (!o && route.query.id) { const { id, ...rest } = route.query; void id; router.replace({ query: rest }); } });
onMounted(async () => {
  await load();
  if (route.query.id) openGauge(Number(route.query.id));
  if (route.query.status) fs.value = String(route.query.status);
});
</script>

<style scoped>
.dl { margin-left: 6px; font-size: var(--hm-fs-2xs); color: var(--hm-text-3); }
.bad { color: var(--hm-fault-ink); font-weight: 600; } .warn { color: var(--hm-idle-ink); font-weight: 600; } .good { color: var(--hm-run-ink); }
.dl.bad, .dl.warn { font-weight: 600; }
.why { margin-top: 2px; line-height: 1.35; white-space: normal; }
.muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-2xs); }
.t2 { color: var(--hm-text-2); } .t3 { color: var(--hm-text-3); }
.click :deep(.el-table__row) { cursor: pointer; }
.drw { display: flex; flex-direction: column; gap: var(--hm-sp-2); }
.st-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.mt-s { margin-top: var(--hm-sp-1); }
.acts { display: flex; flex-wrap: wrap; gap: 8px; margin: var(--hm-sp-2) 0; }
.acts .el-button + .el-button { margin-left: 0; }
.sec { margin: var(--hm-sp-3) 0 var(--hm-sp-1); font-size: var(--hm-fs-md); font-weight: 600; color: var(--hm-text); }
.lnk { color: var(--hm-primary); margin-left: 6px; text-decoration: none; cursor: pointer; }
.file { font-size: var(--hm-fs-xs); color: var(--hm-text-2); max-width: 100%; }
</style>
