<template>
  <div class="page ncr">
    <HmPageHead :title="$t('common.nonconformances')" :sub="$t('ncr.openAwaitingDispositionUnderReview')">
      <el-button @click="router.push('/quality/rework')">{{ $t('common.reworkReInspection') }}</el-button>
      <el-button @click="router.push('/quality/hold')">{{ $t('common.lotOnHold') }}</el-button>
    </HmPageHead>
    <div class="tabs" role="tablist" data-test="ncr-tabs">
      <button v-for="t in TABS" :key="t.k" role="tab" class="tb" :class="{ on: view === t.k }" :aria-selected="view === t.k" :data-test="'ncr-tab-' + t.k" @click="setView(t.k)"
        v-ai="{ kind: 'kpi', label: $t('ncr.nonconformances01', [t.l, counts[t.k] ?? '—']), data: { [$t('ncr.definition')]: t.l, [$t('common.quantity')]: counts[t.k] } }">
        <span>{{ t.l }}</span><b class="hm-num">{{ counts[t.k] ?? '—' }}</b>
      </button>
    </div>
    <HmToolbar>
      <el-input v-model="kw" :placeholder="$t('ncr.ncrNoPartCode')" clearable style="width: 220px" @change="load(1)"><template #prefix><el-icon><Search /></el-icon></template></el-input>
      <el-select v-model="disp" :placeholder="$t('ncr.allDispositions')" clearable style="width: 130px" @change="load(1)"><el-option v-for="(v, k) in DISP" :key="k" :label="v" :value="k" /></el-select>
      <template #right><el-button @click="load()"><el-icon><Refresh /></el-icon></el-button></template>
    </HmToolbar>
    <el-alert v-if="legacy" type="info" :closable="false" show-icon class="mt-s"
      :title="$t('ncr.openedFromAnOldLink')" />
    <HmCard flush class="mt-s">
      <el-table :data="rows" v-loading="loading" height="calc(100vh - 330px)" @row-click="open" data-test="ncr-table"
        v-ai-rows="{ entity: 'ncr', rows: () => rows, label: (r: any) => $t('ncr.nonconformance01', [r.code, r.defect__label || '']), data: (r: any) => ({ [$t('common.defect')]: r.defect__label, [$t('common.part')]: r.sn, [$t('common.workstation')]: r.station, [$t('common.quantity')]: r.qty, [$t('common.disposition')]: DISP[r.disposition], [$t('common.status')]: ST[r.status], [$t('common.discover')]: r.found_at }) }">
        <template #empty><HmEmpty :icon="kw || disp ? 'search' : 'data'" :title="view === 'pending' ? $t('ncr.noOpenNonconformances') : $t('ncr.noRecords')" compact /></template>
        <el-table-column prop="code" :label="$t('ncr.ncrNo')" width="140"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
        <el-table-column prop="sn" :label="$t('common.partCode')" width="140"><template #default="{ row }"><span class="hm-mono">{{ row.sn || '—' }}</span></template></el-table-column>
        <el-table-column prop="defect__label" :label="$t('common.defect')" min-width="130" />
        <el-table-column prop="station" :label="$t('ncr.foundAt')" width="90" />
        <el-table-column :label="$t('common.quantity')" width="60" align="right"><template #default="{ row }"><span class="hm-num">{{ row.qty }}</span></template></el-table-column>
        <el-table-column :label="$t('ncr.found')" width="130"><template #default="{ row }"><span class="hm-num">{{ fmtTime(row.found_at) }}</span></template></el-table-column>
        <el-table-column :label="$t('common.disposition')" width="90"><template #default="{ row }"><HmTag :tone="row.disposition === 'pending' ? 'warn' : 'neutral'">{{ DISP[row.disposition] || '—' }}</HmTag></template></el-table-column>
        <el-table-column :label="$t('common.status')" width="90"><template #default="{ row }"><HmTag :tone="row.status === 'closed' ? 'ok' : row.status === 'open' ? 'bad' : 'warn'">{{ ST[row.status] || '—' }}</HmTag></template></el-table-column>
        <el-table-column prop="source" :label="$t('common.source')" width="80"><template #default="{ row }">{{ row.source === 'auto' ? $t('common.machineAuto') : $t('common.manual2') }}</template></el-table-column>
      </el-table>
      <div class="pg"><el-pagination v-model:current-page="page" :page-size="50" :total="total" layout="total, prev, pager, next" @current-change="load()" /></div>
    </HmCard>

    <el-drawer v-model="drawer" :title="d ? $t('ncr.nonconformance0', [d.item.code]) : ''" size="min(760px, 96vw)" destroy-on-close>
      <template v-if="d">
        <div class="acts">
          <el-button v-if="d.item.status !== 'closed'" type="primary" plain size="small" @click="act('rework', $t('ncr.scheduleRework'), [{ k: 'remark', l: $t('entityPage.reworkInstructions') }])">{{ $t('common.rework') }}</el-button>
          <el-button v-if="d.item.status !== 'closed'" type="danger" plain size="small" @click="act('scrap', $t('common.scrapped'), [{ k: 'remark', l: $t('entityPage.scrapNote') }])">{{ $t('common.scrapped') }}</el-button>
          <el-button v-if="d.item.status !== 'closed'" plain size="small" @click="act('concession', $t('common.useAsIs'), [{ k: 'remark', l: $t('entityPage.concessionNote') }])">{{ $t('common.useAsIs') }}</el-button>
          <el-button v-if="d.item.status !== 'closed'" plain size="small" data-test="ncr-close" @click="act('close', $t('common.close'))">{{ $t('common.close') }}</el-button>
          <el-button plain size="small" @click="act('to8d', $t('common.start8d'))">{{ $t('common.start8d') }}</el-button>
          <span class="grow" />
          <el-button v-if="d.item.sn" size="small" @click="router.push('/trace?sn=' + d.item.sn)">{{ $t('common.trace') }}</el-button>
        </div>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item :label="$t('common.partCode')"><span class="hm-mono">{{ d.item.sn || '—' }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('common.workOrder')">{{ d.item.work_order__label || '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('common.defect')">{{ d.item.defect__label || $t('ncr.uncategorized') }}</el-descriptions-item>
          <el-descriptions-item :label="$t('common.discover')">{{ d.item.station }} · {{ d.item.found_by }} · <span class="hm-num">{{ d.item.found_at }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('common.disposition')">{{ DISP[d.item.disposition] }}</el-descriptions-item>
          <el-descriptions-item :label="$t('common.status')">{{ ST[d.item.status] }}</el-descriptions-item>
          <el-descriptions-item :label="$t('ncr.rootCause')" :span="2">{{ d.item.root_cause || '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('ncr.actions')" :span="2">{{ d.item.action || '—' }}</el-descriptions-item>
        </el-descriptions>
        <h4 class="sec">{{ $t('ncr.reworkReInspection') }}</h4>
        <el-steps v-for="r in d.children.rework || []" :key="r.id" :active="r.recheck_result ? 3 : r.finished_at ? 2 : 1" finish-status="success" simple class="mt-s">
          <el-step :title="$t('ncr.rework0', [r.code])" :description="r.time" />
          <el-step :title="r.finished_at ? $t('ncr.reworkDone') : $t('common.inRework')" />
          <el-step :title="r.recheck_result ? $t('ncr.reInspection0', [r.recheck_result === 'OK' ? $t('common.good') : $t('common.ng')]) : $t('ncr.awaitingReInspection')" :status="r.recheck_result === 'NG' ? 'error' : undefined" />
        </el-steps>
        <p v-if="!(d.children.rework || []).length" class="muted">{{ $t('ncr.noReworkRecords') }}</p>
      </template>
    </el-drawer>
    <el-dialog v-model="ad.open" :title="ad.title" width="min(440px, 96vw)">
      <el-form label-width="80px"><el-form-item v-for="f in ad.fields" :key="f.k" :label="f.l"><el-input v-model="ad.body[f.k]" /></el-form-item></el-form>
      <p v-if="!ad.fields.length" class="muted">{{ $t('entityPage.confirm0', [ad.title]) }}</p>
      <template #footer><el-button @click="ad.open = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :loading="ad.busy" @click="runAct">{{ $t('common.ok') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { onMounted, onUnmounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { registerAiActions } from '../ai';
import { get, post } from '../api';
import { fmtTime } from '../design';

const router = useRouter(), route = useRoute();
const TABS = [{ k: 'pending', l: $t('common.pending') }, { k: 'rework', l: $t('common.inRework') }, { k: 'closed', l: $t('common.off') }, { k: 'all', l: $t('common.all') }];
const DISP: Record<string, string> = { pending: $t('common.awaitingDisposition'), rework: $t('common.rework'), scrap: $t('common.scrapped'), concession: $t('common.useAsIs'), return: $t('common.returnToSupplier') };
const ST: Record<string, string> = { open: $t('common.awaitingDisposition'), reviewing: $t('ncr.underReviewInRework'), closed: $t('common.off') };
// Old link /e/ncr?f_status=open means "Pending": display using the unified definition
const legacy = route.query.f_status === 'open' && !route.query.view;
const view = ref<string>((route.query.view as string) || (legacy ? 'pending' : 'pending'));
const rows = ref<any[]>([]), total = ref(0), page = ref(1), loading = ref(false), kw = ref(''), disp = ref('');
const counts = ref<Record<string, number>>({});
const drawer = ref(false), d = ref<any>(null);
const ad = reactive<any>({ open: false, title: '', name: '', fields: [], body: {}, busy: false });

async function load(p?: number) {
  if (p) page.value = p;
  loading.value = true;
  try {
    const qs = new URLSearchParams({ page: String(page.value), size: '50', sort: '-id' });
    if (view.value !== 'all') qs.set('view', view.value);
    if (kw.value) qs.set('q', kw.value);
    if (disp.value) qs.set('f_disposition', disp.value);
    const [r, c] = await Promise.all([get('/api/e/ncr?' + qs), get('/api/quality/ncr/counts')]);
    rows.value = r.items; total.value = r.total; counts.value = c;
  } finally { loading.value = false; }
}
function setView(v: string) { view.value = v; router.replace({ query: { view: v } }); load(1); }
async function open(row: any) { d.value = await get(`/api/e/ncr/${row.id}`); drawer.value = true; }
function act(name: string, title: string, fields: any[] = []) { Object.assign(ad, { open: true, name, title, fields, body: {}, busy: false }); }
async function runAct() {
  ad.busy = true;
  try {
    const r = await post(`/api/e/ncr/${d.value.item.id}/do/${ad.name}`, ad.body);
    ElMessage.success(r.msg || $t('common.completed')); ad.open = false;
    d.value = await get(`/api/e/ncr/${d.value.item.id}`); load();
  } finally { ad.busy = false; }
}
const off = registerAiActions('row', [
  { id: 'ncr-open', label: $t('ncr.openDisposition'), icon: 'Edit', order: 105, when: (it) => it.entity === 'ncr' && !!it.id, run: (it) => open({ id: it.id }) },
]);
onUnmounted(off);
onMounted(async () => { await load(); if (route.query.id) open({ id: route.query.id }); });
</script>

<style scoped>
.tabs { display: flex; gap: 8px; margin-bottom: var(--hm-gap); flex-wrap: wrap; }
.tb { display: flex; align-items: baseline; gap: 10px; padding: 10px 16px; border: 1px solid var(--hm-border); background: var(--hm-surface); border-radius: var(--hm-r-md);
  cursor: pointer; font: inherit; color: var(--hm-text-2); min-width: 120px; box-shadow: var(--hm-e1); }
.tb b { font-size: var(--hm-fs-xl); color: var(--hm-text); font-family: var(--hm-font-num); }
.tb.on { border-color: var(--hm-primary); background: var(--hm-primary-soft); color: var(--hm-primary); }
.pg { display: flex; justify-content: flex-end; padding: 8px 12px; }
.acts { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
.sec { margin: 16px 0 6px; font-size: var(--hm-fs-md); }
.mt-s { margin-top: 10px; } .muted { color: var(--hm-text-3); }
</style>
