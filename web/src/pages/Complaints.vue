<template>
  <div class="page cmp">
    <HmPageHead :title="tab === 'recall' ? $t('complaints.recallSimulation') : $t('common.complaintsRecalls')" :sub="tab === 'recall' ? $t('complaints.lookUpTheAffectedScope') : $t('complaints.customerComplaintImpactAnalysisHeat')">
      <el-button v-if="tab === 'list'" v-perm:disable="'complaint.complaint.create'" type="primary" data-test="cmp-new" @click="openNew"><el-icon><Plus /></el-icon>{{ $t('complaints.logComplaint') }}</el-button>
    </HmPageHead>
    <el-tabs v-model="tab" class="tabs" data-test="cmp-tabs">
      <el-tab-pane :label="$t('complaints.customerComplaints')" name="list" /><el-tab-pane :label="$t('complaints.recallSimulation')" name="recall" />
    </el-tabs>

    <!-- Complaint list -->
    <template v-if="tab === 'list'">
      <div class="grid g4">
        <HmKpi :label="$t('complaints.openComplaints')" :value="items.filter((x) => x.status !== 'closed').length" format="int" :unit="$t('complaints.k2242')" :status="items.some((x) => x.status !== 'closed') ? 'warn' : 'ok'" />
        <HmKpi :label="$t('complaints.criticalMajorOpen')" :value="items.filter((x) => x.status !== 'closed' && x.severity !== 'minor').length" format="int" :unit="$t('complaints.k2242')" :status="items.some((x) => x.status !== 'closed' && x.severity === 'critical') ? 'bad' : ''" />
        <HmKpi :label="$t('complaints.containmentInProgress')" :value="items.filter((x) => x.status === 'containment').length" format="int" :unit="$t('complaints.k2242')" />
        <HmKpi :label="$t('common.off')" :value="items.filter((x) => x.status === 'closed').length" format="int" :unit="$t('complaints.k2242')" />
      </div>
      <HmToolbar class="mt">
        <el-radio-group v-model="fs" size="small">
          <el-radio-button value="">{{ $t('common.all') }}</el-radio-button>
          <el-radio-button v-for="(t, k) in ST" :key="k" :value="k">{{ t }}</el-radio-button>
        </el-radio-group>
        <el-input v-model="q" size="small" clearable :placeholder="$t('complaints.noCustomerDeliveryOrderDescription')" style="width: 240px" />
      </HmToolbar>
      <HmCard flush class="mt">
        <el-table :data="shown" size="small" class="click" data-test="cmp-table" @row-click="(r: any) => openDetail(r.id)"
          v-ai-rows="{ entity: 'complaint', rows: () => shown, label: (r: any) => $t('complaints.complaint012', [r.code, r.customer, ST[r.status] || '']), data: (r: any) => ({ [$t('common.customer')]: r.customer, [$t('common.deliveryOrder')]: r.delivery_code, [$t('common.product')]: r.material__label, [$t('common.quantity')]: r.qty, [$t('complaints.severity')]: SV[r.severity]?.[0], [$t('common.status')]: ST[r.status], [$t('andon.description')]: r.description }) }">
          <el-table-column :label="$t('common.no2')" width="128"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
          <el-table-column prop="customer" :label="$t('common.customer')" min-width="110" />
          <el-table-column :label="$t('common.deliveryOrder')" width="130"><template #default="{ row }"><span class="hm-mono">{{ row.delivery_code || '—' }}</span></template></el-table-column>
          <el-table-column prop="material__label" :label="$t('common.product')" min-width="130" />
          <el-table-column :label="$t('common.quantity')" width="64" align="right"><template #default="{ row }"><span class="hm-num">{{ row.qty ?? '—' }}</span></template></el-table-column>
          <el-table-column prop="description" :label="$t('common.issue')" min-width="160" />
          <el-table-column :label="$t('complaints.severity')" width="76"><template #default="{ row }"><HmTag :tone="SV[row.severity]?.[1] || 'neutral'">{{ SV[row.severity]?.[0] || row.severity }}</HmTag></template></el-table-column>
          <el-table-column :label="$t('common.status')" width="86"><template #default="{ row }"><HmTag :tone="STT[row.status] || 'neutral'">{{ ST[row.status] || row.status }}</HmTag></template></el-table-column>
          <el-table-column :label="$t('complaints.received')" width="110"><template #default="{ row }"><span class="hm-num">{{ fmtTime(row.received_at) }}</span></template></el-table-column>
        </el-table>
        <HmEmpty v-if="loaded && !shown.length" :title="items.length ? $t('complaints.noMatchingComplaints') : $t('complaints.noCustomerComplaintsYet')" :icon="items.length ? 'search' : 'data'" />
      </HmCard>
    </template>

    <!-- Recall simulation -->
    <template v-else>
      <HmToolbar>
        <el-input v-model="rc.code" :placeholder="$t('complaints.heatPartCodeCartonNo')" style="width: 360px" clearable data-test="recall-code" @keyup.enter="runRecall" />
        <el-button type="primary" :loading="rc.busy" :disabled="!rc.code.trim()" data-test="recall-run" @click="runRecall"><el-icon><Search /></el-icon>{{ $t('complaints.findScope') }}</el-button>
        <el-button :disabled="!rc.d" data-test="recall-print" @click="printRecall"><el-icon><Printer /></el-icon>{{ $t('complaints.printReport') }}</el-button>
        <template #right><span class="small t2">{{ $t('complaints.lookupOnlyNothingIsHeld') }}</span></template>
      </HmToolbar>
      <HmCard v-if="rc.d" class="mt" :title="$t('complaints.startPoint0', [rc.d.code])" :sub="$t('complaints.identifiedAs01', [seedText(rc.d.seed), fmtTime(rc.d.at, 'full')])" data-test="recall-result">
        <CmpImpact :impact="rc.d" />
      </HmCard>
      <HmEmpty v-else class="mt" icon="search" :title="$t('complaints.enterACodeToStart')" :desc="$t('complaints.startFromAnyLevelFind')" />
    </template>

    <!-- Complaint detail -->
    <el-drawer v-model="dt.open" size="min(1000px, 100%)" :title="dt.d ? $t('complaints.complaint01', [dt.d.item.code, dt.d.item.customer]) : ''" append-to-body destroy-on-close>
      <div v-if="dt.d" class="drw" data-test="cmp-detail">
        <el-steps :active="stepActive" finish-status="success" simple class="steps">
          <el-step v-for="s in STEPS" :key="s" :title="s" />
        </el-steps>

        <div class="acts">
          <el-button v-perm:disable="'complaint.complaint.analyze'" type="primary" plain :loading="busy === 'analyze'" :disabled="closed" data-test="cmp-act-analyze" @click="act('analyze')">{{ $t('common.impactAnalysis') }}</el-button>
          <el-button v-perm:disable="'complaint.complaint.contain'" type="danger" plain :loading="busy === 'contain'" :disabled="closed || !it.impact" data-test="cmp-act-contain" @click="act('contain')">{{ $t('complaints.holdStockContainment') }}</el-button>
          <el-button v-perm:disable="'complaint.complaint.notify'" :loading="busy === 'notify'" :disabled="closed || !it.impact" data-test="cmp-act-notify" @click="act('notify')">{{ it.notified ? $t('complaints.notifyAgain') : $t('complaints.notifyStakeholders') }}</el-button>
          <el-button v-perm:disable="'complaint.complaint.to8d'" :loading="busy === 'to8d'" :disabled="closed" data-test="cmp-act-to8d" @click="act('to8d')">{{ it.report_8d ? $t('complaints.open8d') : $t('common.start8d') }}</el-button>
          <el-button v-perm:disable="'complaint.complaint.rma'" :loading="busy === 'rma'" :disabled="closed || !!it.rma_code" data-test="cmp-act-rma" @click="act('rma')">{{ $t('complaints.createReturnRma') }}</el-button>
          <el-button v-perm:disable="'complaint.complaint.close'" type="success" :loading="busy === 'close'" :disabled="closed" data-test="cmp-act-close" @click="act('close')">{{ $t('complaints.closeESignature') }}</el-button>
          <el-button v-if="aiOn('quality_plus')" class="ai-btn" :loading="busy === 'ai'" :disabled="!it.impact" data-test="cmp-ai" @click="aiExplain"><el-icon><MagicStick /></el-icon>{{ $t('complaints.aiExplainImpact') }}</el-button>
        </div>

        <el-descriptions :column="3" size="small" border>
          <el-descriptions-item :label="$t('common.status')"><HmTag :tone="STT[it.status] || 'neutral'">{{ ST[it.status] || it.status }}</HmTag></el-descriptions-item>
          <el-descriptions-item :label="$t('complaints.severity')"><HmTag :tone="SV[it.severity]?.[1] || 'neutral'">{{ SV[it.severity]?.[0] || it.severity }}</HmTag></el-descriptions-item>
          <el-descriptions-item :label="$t('complaints.received')"><span class="hm-num">{{ fmtTime(it.received_at, 'full') }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('complaints.customerRefNo')"><span class="hm-mono">{{ it.customer_ref || '—' }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('common.deliveryOrder')"><span class="hm-mono">{{ it.delivery_code || '—' }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('common.product')">{{ it.material__label || '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('complaints.complaintQuantity')"><span class="hm-num">{{ it.qty ?? '—' }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('common.defect')">{{ it.defect__label || '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('common.lotHeat')"><span class="hm-mono">{{ it.lots || '—' }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('common.partCode')" :span="3"><span class="hm-mono brk">{{ it.sns || '—' }}</span></el-descriptions-item>
          <el-descriptions-item v-if="it.cartons" :label="$t('complaints.cartonPalletNo')" :span="3"><span class="hm-mono">{{ it.cartons }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('common.problemDescription')" :span="3">{{ it.description || '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('complaints.containmentActions')" :span="3"><span class="pre">{{ it.containment || '—' }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('common.n8dReports')"><a v-if="it.report_8d" class="lnk hm-mono" data-test="cmp-8d" @click="router.push('/e/report_8d?id=' + it.report_8d)">{{ it.report_8d__label || dt.d.report_8d?.code }}</a><span v-else>—</span>
            <HmTag v-if="dt.d.report_8d" class="ml" :tone="dt.d.report_8d.status === 'closed' ? 'ok' : 'warn'">{{ R8[dt.d.report_8d.status] || dt.d.report_8d.status }}</HmTag></el-descriptions-item>
          <el-descriptions-item :label="$t('complaints.notified')">{{ it.notified ? $t('common.yes') : $t('common.no') }}</el-descriptions-item>
          <el-descriptions-item :label="$t('common.close')">{{ it.closed_by || '—' }} <span class="hm-num t3">{{ fmtTime(it.closed_at) }}</span></el-descriptions-item>
        </el-descriptions>

        <div v-if="ai.text" class="ai-box" data-test="cmp-ai-text">
          <b>{{ $t('complaints.aiExplainImpact2') }}</b>
          <p class="pre">{{ ai.text }}</p>
          <span class="small t2">{{ $t('complaints.theDraftedContainmentStatementIs') }}</span>
        </div>

        <h4 class="sec">{{ $t('complaints.impactScope') }}</h4>
        <CmpImpact v-if="it.impact" :impact="it.impact" />
        <HmEmpty v-else plain :title="$t('complaints.noImpactAnalysisYetClick')" />

        <div class="two">
          <div>
            <h4 class="sec">{{ $t('common.lotOnHold') }} <a class="lnk small" @click="router.push('/quality/hold')">{{ $t('complaints.lotHold') }}</a></h4>
            <el-table v-if="dt.d.holds.length" :data="dt.d.holds" size="small" data-test="cmp-holds">
              <el-table-column :label="$t('complaints.holdOrder')" width="124"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
              <el-table-column :label="$t('common.lot')" width="110"><template #default="{ row }"><span class="hm-mono">{{ row.lot }}</span></template></el-table-column>
              <el-table-column :label="$t('common.status')" width="80"><template #default="{ row }"><HmTag :tone="row.status === 'hold' ? 'bad' : 'ok'">{{ row.status === 'hold' ? $t('complaints.onHold') : $t('complaints.released') }}</HmTag></template></el-table-column>
              <el-table-column :label="$t('complaints.partsInvolved')" min-width="70" align="right"><template #default="{ row }"><span class="hm-num">{{ row.parts ?? '—' }}</span></template></el-table-column>
            </el-table>
            <HmEmpty v-else plain :title="$t('complaints.noHoldOrders')" />
          </div>
          <div>
            <h4 class="sec">{{ $t('complaints.returnsRma') }}</h4>
            <el-table v-if="dt.d.returns.length" :data="dt.d.returns" size="small">
              <el-table-column :label="$t('complaints.returnOrder')" width="130"><template #default="{ row }"><a class="lnk hm-mono" @click="router.push('/e/sales_return?id=' + row.id)">{{ row.code }}</a></template></el-table-column>
              <el-table-column prop="status" :label="$t('common.status')" width="80" />
              <el-table-column prop="disposition" :label="$t('common.disposition')" min-width="80" />
              <el-table-column :label="$t('common.amount')" width="90" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.total) }}</span></template></el-table-column>
            </el-table>
            <p v-else class="small t2">{{ it.rma_code ? `RMA ${it.rma_code}` : $t('complaints.noReturnOrders') }}</p>
          </div>
        </div>

        <h4 class="sec">{{ $t('complaints.eSignature') }}</h4>
        <el-table v-if="dt.d.signatures.length" :data="dt.d.signatures" size="small" data-test="cmp-sigs">
          <el-table-column :label="$t('esign.signer')" width="110"><template #default="{ row }">{{ row.name || row.user }}</template></el-table-column>
          <el-table-column prop="meaning" :label="$t('common.meaning')" width="90" />
          <el-table-column prop="action" :label="$t('complaints.actions')" width="80" />
          <el-table-column :label="$t('common.time')" width="150"><template #default="{ row }"><span class="hm-num">{{ fmtTime(row.signed_at, 'full') }}</span></template></el-table-column>
          <el-table-column :label="$t('common.summary')" min-width="160"><template #default="{ row }"><span class="hm-mono t2">{{ (row.hash || '').slice(0, 16) }}</span></template></el-table-column>
        </el-table>
        <p v-else class="small t2">{{ $t('complaints.closingAComplaintRequiresAn') }}</p>
      </div>
    </el-drawer>

    <!-- Log complaint -->
    <el-dialog v-model="nf.open" :title="$t('complaints.logCustomerComplaint')" width="min(720px, 96vw)" append-to-body destroy-on-close>
      <el-form label-width="92px" class="fgrid" data-test="cmp-form">
        <el-form-item :label="$t('common.customer')" required><el-input v-model="nf.customer" data-test="cmp-f-customer" /></el-form-item>
        <el-form-item :label="$t('complaints.customerRefNo')"><el-input v-model="nf.customer_ref" :placeholder="$t('complaints.customerComplaintNo')" /></el-form-item>
        <el-form-item :label="$t('common.deliveryOrderNo')"><el-input v-model="nf.delivery_code" data-test="cmp-f-delivery" /></el-form-item>
        <el-form-item :label="$t('common.product')"><RefSelect v-model="nf.material" entity="material" /></el-form-item>
        <el-form-item :label="$t('common.partCode')" class="span2"><el-input v-model="nf.sns" type="textarea" :rows="2" :placeholder="$t('complaints.separateWithCommasOrNew')" data-test="cmp-f-sns" /></el-form-item>
        <el-form-item :label="$t('common.lotHeat')"><el-input v-model="nf.lots" /></el-form-item>
        <el-form-item :label="$t('complaints.cartonPalletNo')"><el-input v-model="nf.cartons" /></el-form-item>
        <el-form-item :label="$t('complaints.complaintQuantity')"><el-input-number v-model="nf.qty" :min="0" :controls="false" style="width: 100%" /></el-form-item>
        <el-form-item :label="$t('common.defect')">
          <el-select v-model="nf.defect" clearable filterable style="width: 100%"><el-option v-for="d in defects" :key="d.id" :value="d.id" :label="`${d.code} ${d.name}`" /></el-select>
        </el-form-item>
        <el-form-item :label="$t('complaints.severity')" class="span2">
          <el-radio-group v-model="nf.severity"><el-radio-button value="critical">{{ $t('complaints.critical') }}</el-radio-button><el-radio-button value="major">{{ $t('complaints.major') }}</el-radio-button><el-radio-button value="minor">{{ $t('complaints.minor') }}</el-radio-button></el-radio-group>
        </el-form-item>
        <el-form-item :label="$t('common.problemDescription')" class="span2"><el-input v-model="nf.description" type="textarea" :rows="3" data-test="cmp-f-desc" /></el-form-item>
      </el-form>
      <p class="small t2">{{ $t('complaints.fillInAtLeastOne') }}</p>
      <template #footer>
        <el-button @click="nf.open = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!nf.customer?.trim()" :loading="busy === 'new'" data-test="cmp-save" @click="saveNew">{{ $t('complaints.log') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { aiBus, aiOn, openAiDock } from '../ai';
import { get, post, tok } from '../api';
import RefSelect from '../components/RefSelect.vue';
import CmpImpact from '../components/exec/CmpImpact.vue';
import { fmtNum, fmtTime } from '../design';

const router = useRouter(), route = useRoute();
const ST: Record<string, string> = { open: $t('complaints.logged'), analysis: $t('complaints.analyzing'), containment: $t('complaints.containmentInProgress'), closed: $t('common.off') };
const STT: Record<string, any> = { open: 'primary', analysis: 'amber', containment: 'warn', closed: 'ok' };
const SV: Record<string, [string, any]> = { critical: [$t('complaints.critical'), 'bad'], major: [$t('complaints.major'), 'warn'], minor: [$t('complaints.minor'), 'neutral'] };
const R8: Record<string, string> = { draft: $t('common.draft'), review: $t('complaints.underReview'), closed: $t('common.off') };
const STEPS = [$t('complaints.log'), $t('common.impactAnalysis'), $t('complaints.containment'), $t('common.notifications'), '8D', $t('common.close')];

const tab = ref<string>(route.query.tab === 'recall' || route.query.code ? 'recall' : 'list');
const items = ref<any[]>([]), loaded = ref(false), defects = ref<any[]>([]);
const fs = ref(''), q = ref(''), busy = ref('');
const shown = computed(() => {
  const s = q.value.trim().toLowerCase();
  return items.value.filter((x) => (!fs.value || x.status === fs.value) && (!s || [x.code, x.customer, x.delivery_code, x.description, x.customer_ref].some((v) => String(v || '').toLowerCase().includes(s))));
});
async function load() { items.value = (await get('/api/e/complaint?size=300&sort=-id')).items; loaded.value = true; }

// ---------- Detail ----------
const dt = reactive<any>({ open: false, d: null });
const it = computed(() => dt.d?.item || {});
const closed = computed(() => it.value.status === 'closed');
const stepActive = computed(() => {
  const i = it.value;
  if (i.status === 'closed') return 6;
  const done = [true, !!i.impact, i.status === 'containment' || (i.holds || 0) > 0, !!i.notified, !!i.report_8d];
  let n = 0; while (n < done.length && done[n]) n++;
  return n;
});
const ai = reactive({ text: '' });
async function loadDetail(id: number) { dt.d = await get(`/api/complaint/${id}/full`); }
async function openDetail(id: number) {
  ai.text = '';
  await loadDetail(id); dt.open = true;
  if (String(route.query.id || '') !== String(id)) router.replace({ query: { id } });
}
async function act(a: string) {
  if (a === 'to8d' && it.value.report_8d) { router.push('/e/report_8d?id=' + it.value.report_8d); return; }
  busy.value = a;
  try {
    const r = await post(`/api/e/complaint/${it.value.id}/do/${a}`, {});
    ElMessage({ type: 'success', message: r.msg || $t('common.done'), duration: a === 'analyze' ? 5000 : 2500 });
    await Promise.all([loadDetail(it.value.id), load()]);
  } catch { /* api.ts already notified (incl. 409: 8D not closed) */ } finally { busy.value = ''; }
}
async function aiExplain() {
  busy.value = 'ai';
  const item = { kind: 'record', entity: 'complaint', id: it.value.id, label: $t('complaints.complaint0', [it.value.code]) };
  openAiDock(true);
  aiBus.emit('draft-pending', { label: item.label, op: 'complaint_note' });
  try {
    const d = await post('/api/iatf/ai/complaint', { complaint: it.value.id });
    ai.text = d.explanation || '';
    aiBus.emit('draft', { draft: d, item });
  } catch (e: any) { aiBus.emit('draft', { error: e?.message || String(e), item }); } finally { busy.value = ''; }
}
const onApproved = () => { if (dt.open && dt.d) loadDetail(it.value.id); load(); };

// ---------- Log ----------
const nf = reactive<any>({ open: false });
async function openNew() {
  Object.assign(nf, { open: true, customer: '', customer_ref: '', delivery_code: '', material: null, sns: '', lots: '', cartons: '', qty: null, defect: null, severity: 'major', description: '' });
  if (!defects.value.length) defects.value = (await get('/api/e/defect_code?size=200', true).catch(() => ({ items: [] }))).items;
}
async function saveNew() {
  busy.value = 'new';
  try {
    const { open, ...body } = nf; void open;
    for (const k of Object.keys(body)) if (body[k] === '') body[k] = null;
    const r = await post('/api/e/complaint', body);
    ElMessage.success($t('complaints.logged')); nf.open = false; await load(); if (r.id) openDetail(r.id);
  } catch { /* already notified */ } finally { busy.value = ''; }
}

// ---------- Recall simulation ----------
const rc = reactive<any>({ code: (route.query.code as string) || '', d: null, busy: false });
const SEED: Record<string, string> = { sns: $t('common.partCode'), packages: $t('complaints.cartonPallet'), loads: $t('complaints.loadSheetAsnDeliveryOrder'), lots: $t('complaints.heatLot') };
const seedText = (s: any) => Object.keys(s || {}).filter((k) => (s[k] || []).length).map((k) => SEED[k] || k).join($t('common.k42')) || '—';
async function runRecall() {
  const code = rc.code.trim(); if (!code) return;
  rc.busy = true;
  try { rc.d = await get('/api/complaint/recall_sim?code=' + encodeURIComponent(code)); router.replace({ query: { tab: 'recall', code } }); }
  catch { rc.d = null; } finally { rc.busy = false; }
}
function printRecall() { window.open(`/api/complaint/recall_sim.html?code=${encodeURIComponent(rc.d.code)}&token=${tok()}`, '_blank'); }

watch(tab, (t) => { router.replace({ query: t === 'recall' ? { tab: 'recall', ...(rc.code ? { code: rc.code } : {}) } : {} }); });
watch(() => dt.open, (o) => { if (!o && route.query.id) router.replace({ query: {} }); });
onMounted(async () => {
  window.addEventListener('approvals-changed', onApproved);
  await load();
  if (route.query.id) openDetail(Number(route.query.id));
  if (tab.value === 'recall' && rc.code) runRecall();
});
onUnmounted(() => window.removeEventListener('approvals-changed', onApproved));
</script>

<style scoped>
.tabs { margin-bottom: 4px; }
.click :deep(.el-table__row) { cursor: pointer; }
.t2 { color: var(--hm-text-2); } .t3 { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-2xs); }
.drw { display: flex; flex-direction: column; gap: var(--hm-sp-2); }
.steps { margin-bottom: var(--hm-sp-1); }
.acts { display: flex; flex-wrap: wrap; gap: 8px; }
.acts .el-button + .el-button { margin-left: 0; }
.sec { margin: var(--hm-sp-3) 0 var(--hm-sp-1); font-size: var(--hm-fs-md); font-weight: 600; display: flex; align-items: center; gap: 10px; }
.lnk { color: var(--hm-primary); cursor: pointer; font-weight: 400; }
.ml { margin-left: 6px; }
.pre { white-space: pre-wrap; margin: 0; }
.brk { word-break: break-all; }
.two { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--hm-gap); }
.ai-btn { background: linear-gradient(var(--hm-surface), var(--hm-surface)) padding-box, var(--hm-ai-grad) border-box; border: 1px solid transparent; color: var(--hm-primary-press); }
.ai-box { padding: 10px 12px; border-radius: var(--hm-r-md); background: linear-gradient(var(--hm-surface), var(--hm-surface)) padding-box, var(--hm-ai-grad) border-box; border: 1px solid transparent;
  font-size: var(--hm-fs-xs); color: var(--hm-text); display: flex; flex-direction: column; gap: 4px; }
.fgrid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 12px; }
.fgrid .span2 { grid-column: span 2; }
@media (max-width: 900px) { .two { grid-template-columns: minmax(0, 1fr); } }
</style>
