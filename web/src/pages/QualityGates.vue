<template>
  <div class="page qg">
    <HmPageHead :title="TITLE[tab]" :sub="SUB[tab]" />
    <el-tabs v-model="tab" @tab-change="go" class="tabs" data-test="qg-tabs">
      <el-tab-pane :label="$t('common.firstArticleRelease')" name="fai" /><el-tab-pane :label="$t('common.lotOnHold')" name="hold" /><el-tab-pane :label="$t('common.reworkReInspection')" name="rework" />
    </el-tabs>

    <!-- First article -->
    <template v-if="tab === 'fai'">
      <div class="grid g4">
        <HmKpi :label="$t('qualityGates.wipOperationsNeedingFirstArticle')" :value="cands.length" format="int" :unit="$t('qualityGates.k4512')" />
        <HmKpi :label="$t('qualityGates.notReleasedNoProductionReporting')" :value="cands.filter((x) => !x.fai_ok).length" format="int" :unit="$t('qualityGates.k4512')" :status="cands.some((x) => !x.fai_ok) ? 'warn' : 'ok'" />
        <HmKpi :label="$t('qualityGates.firstArticleInspectionsPending')" :value="fais.filter((x) => x.status === 'pending').length" format="int" :unit="$t('common.k2835')" />
        <HmKpi :label="$t('qualityGates.notReleased')" :value="fais.filter((x) => x.status === 'rejected').length" format="int" :unit="$t('common.k2835')" :status="fais.some((x) => x.status === 'rejected') ? 'bad' : ''" />
      </div>
      <HmCard :title="$t('qualityGates.operationsRequiringFirstArticle')" :sub="$t('qualityGates.routingStepsWithFirstArticle')" flush class="mt">
        <el-table :data="cands" size="small" data-test="fai-cands" v-ai-rows="{ rows: () => cands, item: (r: any) => ({ kind: 'row', entity: 'wo_op', id: r.wo_op, label: $t('qualityGates.n01FirstArticle2', [r.work_order, r.process, r.fai_ok ? $t('mobile.released') : $t('mobile.notReleased')]), data: r }) }">
          <el-table-column prop="work_order" :label="$t('common.workOrder')" width="140" /><el-table-column :label="$t('common.operation')" min-width="120"><template #default="{ row }">{{ row.seq }} {{ row.process }}</template></el-table-column>
          <el-table-column prop="station_group" :label="$t('erpMrp.workstationGroup')" width="90" />
          <el-table-column :label="$t('qualityGates.progress')" width="110"><template #default="{ row }"><span class="hm-num">{{ row.qty_good }}/{{ row.qty_plan }}</span></template></el-table-column>
          <el-table-column :label="$t('common.firstArticle')" width="160"><template #default="{ row }">
            <HmTag :tone="row.fai_ok ? 'ok' : row.fai?.status === 'pending' ? 'warn' : 'bad'">{{ row.fai_ok ? $t('mobile.released') : row.fai?.status === 'pending' ? $t('qualityGates.pending0', [row.fai.code]) : row.fai?.status === 'rejected' ? $t('qualityGates.notReleased') : $t('qualityGates.notRequested') }}</HmTag>
          </template></el-table-column>
          <el-table-column label="" width="190"><template #default="{ row }">
            <el-button v-if="!row.fai_ok && row.fai?.status !== 'pending'" size="small" type="primary" plain data-test="fai-apply" @click="apply(row)">{{ $t('common.requestFirstArticle') }}</el-button>
            <el-button v-if="row.fai?.status === 'pending'" size="small" type="primary" data-test="fai-inspect" @click="inspect(row.fai.id)">{{ $t('mobile.enterJudge') }}</el-button>
          </template></el-table-column>
        </el-table>
      </HmCard>
      <HmCard :title="$t('qualityGates.firstArticleInspection')" flush class="mt">
        <el-table :data="fais" size="small" max-height="380" @row-click="(r: any) => inspect(r.id)"
          v-ai-rows="{ entity: 'first_article', rows: () => fais, label: (r: any) => $t('qualityGates.firstArticle01', [r.code, FST[r.status]]), data: (r: any) => ({ [$t('common.workOrder')]: r.work_order__label, [$t('common.operation')]: r.process__label, [$t('common.equipment')]: r.equipment__label, [$t('common.status')]: FST[r.status], [$t('qualityGates.judgedBy')]: r.decided_by }) }">
          <el-table-column prop="code" :label="$t('qualityGates.faiNo')" width="140" /><el-table-column prop="work_order__label" :label="$t('common.workOrder')" width="130" />
          <el-table-column prop="process__label" :label="$t('common.operation')" min-width="110" /><el-table-column prop="equipment__label" :label="$t('common.equipment')" min-width="120" />
          <el-table-column :label="$t('common.trigger')" width="80"><template #default="{ row }">{{ TRG[row.trigger] || row.trigger }}</template></el-table-column>
          <el-table-column :label="$t('common.status')" width="90"><template #default="{ row }"><HmTag :tone="row.status === 'approved' ? 'ok' : row.status === 'rejected' ? 'bad' : 'warn'">{{ FST[row.status] }}</HmTag></template></el-table-column>
          <el-table-column prop="requested_by" :label="$t('common.requester')" width="100" /><el-table-column prop="decided_by" :label="$t('qualityGates.judgedBy')" width="100" />
          <el-table-column :label="$t('qualityGates.releasedAt')" width="130"><template #default="{ row }"><span class="hm-num">{{ fmtTime(row.decided_at) }}</span></template></el-table-column>
        </el-table>
      </HmCard>
    </template>

    <!-- Lot hold -->
    <template v-else-if="tab === 'hold'">
      <HmToolbar>
        <el-input v-model="hf.lot" :placeholder="$t('qualityGates.lotHeatNo')" style="width: 180px" data-test="hold-lot" />
        <el-input v-model="hf.reason" :placeholder="$t('qualityGates.holdReasonRequired')" style="width: 280px" data-test="hold-reason" />
        <el-button type="danger" :disabled="!hf.lot || !hf.reason" data-test="hold-create" @click="hold">{{ $t('qualityGates.holdLot') }}</el-button>
        <template #right><span class="muted small">{{ $t('qualityGates.onceHeldTheLotS') }}</span></template>
      </HmToolbar>
      <HmCard flush class="mt">
        <el-table :data="holds" size="small" data-test="hold-table"
          v-ai-rows="{ entity: 'lot_hold', rows: () => holds, label: (r: any) => $t('qualityGates.lotHold01', [r.lot, r.status === 'hold' ? $t('complaints.onHold') : $t('complaints.released')]), data: (r: any) => ({ [$t('common.lot')]: r.lot, [$t('common.reason')]: r.reason, [$t('common.part')]: r.parts, [$t('common.status')]: r.status }) }">
          <el-table-column prop="code" :label="$t('qualityGates.holdNo')" width="140" /><el-table-column prop="lot" :label="$t('common.lotHeat')" width="140"><template #default="{ row }"><span class="hm-mono">{{ row.lot }}</span></template></el-table-column>
          <el-table-column prop="reason" :label="$t('common.reason')" min-width="200" />
          <el-table-column :label="$t('complaints.partsInvolved')" width="90" align="right"><template #default="{ row }"><span class="hm-num">{{ row.parts ?? '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('common.status')" width="90"><template #default="{ row }"><HmTag :tone="row.status === 'hold' ? 'bad' : 'ok'">{{ row.status === 'hold' ? $t('complaints.onHold') : $t('complaints.released') }}</HmTag></template></el-table-column>
          <el-table-column :label="$t('common.held')" width="170"><template #default="{ row }">{{ row.held_by }} <span class="hm-num muted">{{ fmtTime(row.held_at) }}</span></template></el-table-column>
          <el-table-column label="" width="160"><template #default="{ row }">
            <el-button size="small" link type="primary" @click="router.push('/trace/recon?heat=' + encodeURIComponent(row.lot))">{{ $t('qualityGates.reconcile') }}</el-button>
            <el-button v-if="row.status === 'hold'" size="small" type="primary" plain data-test="hold-release" @click="release(row)">{{ $t('qualityGates.releaseHold') }}</el-button>
          </template></el-table-column>
        </el-table>
      </HmCard>
    </template>

    <!-- Rework re-inspection -->
    <template v-else>
      <HmToolbar>
        <el-radio-group v-model="rwf" @change="loadRework"><el-radio-button value="doing">{{ $t('common.inRework') }}</el-radio-button><el-radio-button value="">{{ $t('common.all') }}</el-radio-button></el-radio-group>
        <template #right><span class="muted small">{{ $t('qualityGates.reworkDoneRecordTheMethod') }}</span></template>
      </HmToolbar>
      <HmCard flush class="mt">
        <el-table :data="reworks" size="small" data-test="rw-table"
          v-ai-rows="{ entity: 'rework', rows: () => reworks, label: (r: any) => $t('qualityGates.rework01', [r.code, r.sn || '']), data: (r: any) => ({ [$t('common.part')]: r.sn, NCR: r.ncr__label, [$t('common.method2')]: r.method, [$t('common.result')]: r.result, [$t('qualityGates.reInspection')]: r.recheck_result }) }">
          <el-table-column prop="code" :label="$t('qualityGates.reworkOrder')" width="130" /><el-table-column prop="sn" :label="$t('common.partCode')" width="140"><template #default="{ row }"><span class="hm-mono">{{ row.sn }}</span></template></el-table-column>
          <el-table-column prop="ncr__label" label="NCR" width="140" /><el-table-column prop="station" :label="$t('qualityGates.reworkStation')" width="100" />
          <el-table-column prop="method" :label="$t('qualityGates.reworkMethod')" min-width="140" />
          <el-table-column :label="$t('ncr.reworkDone')" width="120"><template #default="{ row }"><span class="hm-num">{{ fmtTime(row.finished_at) }}</span></template></el-table-column>
          <el-table-column :label="$t('qualityGates.reInspection')" width="150"><template #default="{ row }">
            <HmTag v-if="row.recheck_result" :tone="row.recheck_result === 'OK' ? 'ok' : 'bad'">{{ row.recheck_result === 'OK' ? $t('common.good') : $t('common.ng') }}</HmTag>
            <span v-if="row.recheck__label" class="muted small"> {{ row.recheck__label }}</span><span v-if="!row.recheck_result" class="muted">{{ $t('ncr.awaitingReInspection') }}</span>
          </template></el-table-column>
          <el-table-column label="" width="190"><template #default="{ row }">
            <template v-if="row.result === 'doing'">
              <el-button v-if="!row.finished_at" size="small" type="primary" plain data-test="rw-finish" @click="finish(row)">{{ $t('ncr.reworkDone') }}</el-button>
              <template v-else>
                <el-button size="small" type="success" plain data-test="rw-ok" @click="recheck(row, 'OK')">{{ $t('qualityGates.reInspectionPassed') }}</el-button>
                <el-button size="small" type="danger" plain @click="recheck(row, 'NG')">{{ $t('common.ng') }}</el-button>
              </template>
            </template>
          </template></el-table-column>
        </el-table>
      </HmCard>
    </template>

    <!-- First article entry -->
    <el-dialog v-model="fi.open" :title="fi.d ? $t('qualityGates.firstArticle012', [fi.d.item.code, fi.d.item.work_order__label, fi.d.item.process__label]) : ''" width="min(860px, 96vw)" destroy-on-close>
      <template v-if="fi.d">
        <p class="muted small">{{ $t('qualityGates.enterMeasuredValuesItemBy') }}</p>
        <el-table :data="fi.items" size="small" data-test="fai-items">
          <el-table-column prop="name" :label="$t('common.item')" min-width="120" /><el-table-column prop="std" :label="$t('common.standard')" min-width="120" />
          <el-table-column :label="$t('entityPage.measured')" width="170"><template #default="{ row }">
            <el-input-number v-if="row.lo != null || row.hi != null" v-model="row.value" :controls="false" :precision="4" style="width: 140px" />
            <el-select v-else v-model="row.ok" style="width: 140px"><el-option :value="true" :label="$t('common.good')" /><el-option :value="false" :label="$t('common.ng')" /></el-select>
          </template></el-table-column>
          <el-table-column :label="$t('common.gauge')" min-width="170"><template #default="{ row }">
            <InspGaugeSelect v-if="row.gauge_type || row.lo != null || row.hi != null" v-model="row.gauge" :item="row" size="small" />
          </template></el-table-column>
        </el-table>
      </template>
      <template #footer>
        <el-button @click="fi.open = false">{{ $t('common.cancel') }}</el-button>
        <el-button v-if="fi.d?.item.status === 'pending'" type="primary" :loading="fi.busy" data-test="fai-judge" @click="judge">{{ $t('common.saveJudge') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { registerAiActions } from '../ai';
import { get, post } from '../api';
import { fmtTime } from '../design';
import InspGaugeSelect from '../components/exec/InspGaugeSelect.vue';

const router = useRouter(), route = useRoute();
const TITLE: Record<string, string> = { fai: $t('common.firstArticleRelease'), hold: $t('common.lotOnHold'), rework: $t('common.reworkReInspection') };
const SUB: Record<string, string> = { fai: $t('qualityGates.operationsWhoseFirstArticleIsn'), hold: $t('qualityGates.heldLotsCanTBe'), rework: $t('qualityGates.reworkNeedsACompleteRecord') };
const FST: Record<string, string> = { pending: $t('qualityGates.pending'), approved: $t('mobile.released'), rejected: $t('qualityGates.notReleased') };
const TRG: Record<string, string> = { start: $t('qualityGates.startUp'), changeover: $t('common.changeover'), tool: $t('qualityGates.toolChange'), shift: $t('qualityGates.shiftChange'), repair: $t('qualityGates.afterRepair') };
const tab = ref<string>((route.meta.tab as string) || 'fai');
const cands = ref<any[]>([]), fais = ref<any[]>([]), holds = ref<any[]>([]), reworks = ref<any[]>([]), rwf = ref('doing');
const hf = reactive({ lot: (route.query.lot as string) || '', reason: '' });
const fi = reactive<any>({ open: false, d: null, items: [], busy: false });

function go(t: any) { router.replace('/quality/' + (t === 'rework' ? 'rework' : t)); }
async function loadFai() { [cands.value, fais.value] = await Promise.all([get('/api/quality/fai/candidates'), get('/api/e/first_article?size=100&sort=-id').then((r) => r.items)]); }
async function loadHold() { holds.value = (await get('/api/e/lot_hold?size=100&sort=-id')).items; }
async function loadRework() { reworks.value = (await get('/api/e/rework?size=200&sort=-id' + (rwf.value ? '&f_result=' + rwf.value : ''))).items; }
const loaders: Record<string, () => Promise<any>> = { fai: loadFai, hold: loadHold, rework: loadRework };

async function apply(row: any) {
  const r = await post('/api/quality/fai', { wo_op: row.wo_op, trigger: row.status === 'running' ? 'shift' : 'start' });
  ElMessage.success(r.msg); await loadFai();
}
async function inspect(id: number) {
  const d = await get('/api/quality/fai/' + id);
  const old = Object.fromEntries((d.values || []).map((v: any) => [v.item, v]));
  fi.items = d.items.map((i: any) => ({ ...i, value: old[i.id]?.value ?? (i.lo != null && i.hi != null ? +((i.lo + i.hi) / 2).toFixed(4) : null), ok: old[i.id]?.ok ?? true,
    gauge: old[i.id]?.gauge ?? null }));
  fi.d = d; fi.open = true;
}
async function judge() {
  fi.busy = true;
  try {
    const r = await post(`/api/quality/fai/${fi.d.item.id}/inspect`, { values: fi.items.map((i: any) => ({ item: i.id, value: i.lo != null || i.hi != null ? i.value : null, ok: i.ok, gauge: i.gauge || null })) });
    ElMessage({ type: r.status === 'approved' ? 'success' : 'warning', message: r.msg }); fi.open = false; await loadFai();
  } finally { fi.busy = false; }
}
async function hold() {
  await post('/api/e/lot_hold', { lot: hf.lot.trim(), reason: hf.reason.trim() });
  ElMessage.success($t('qualityGates.lot0PutOnHold', [hf.lot])); hf.lot = ''; hf.reason = ''; await loadHold();
}
async function release(row: any) {
  const { value } = await ElMessageBox.prompt($t('qualityGates.releaseNoteEGPassed'), $t('qualityGates.releaseHold0', [row.lot]), { inputValue: $t('qualityGates.releasedAfterReview') });
  const r = await post(`/api/e/lot_hold/${row.id}/do/release`, { note: value }); ElMessage.success(r.msg); await loadHold();
}
async function finish(row: any) {
  const { value } = await ElMessageBox.prompt($t('qualityGates.reworkMethodRequiredEG'), $t('qualityGates.reworkDone0', [row.code]), { inputValidator: (v: string) => !!v?.trim() || $t('qualityGates.pleaseEnterTheReworkMethod') });
  const r = await post(`/api/e/rework/${row.id}/do/finish`, { method: value }); ElMessage.success(r.msg); await loadRework();
}
async function recheck(row: any, result: string) {
  const r = await post(`/api/e/rework/${row.id}/do/recheck`, { result }); ElMessage({ type: result === 'OK' ? 'success' : 'warning', message: r.msg }); await loadRework();
}
const off = registerAiActions('row', [
  { id: 'fai-do', label: $t('qualityGates.firstArticleEntryJudgment'), icon: 'Finished', order: 105, when: (it) => it.entity === 'first_article' && !!it.id, run: (it) => inspect(Number(it.id)) },
  { id: 'hold-recon', label: $t('qualityGates.reconcileThisLot'), icon: 'Connection', order: 106, when: (it) => it.entity === 'lot_hold', run: (it) => router.push('/trace/recon?heat=' + encodeURIComponent(it.data?.[$t('common.lot')] || '')) },
]);
onUnmounted(off);
watch(() => route.meta.tab, (t) => { if (t) { tab.value = t as string; loaders[tab.value](); } });
onMounted(() => loaders[tab.value]());
</script>

<style scoped>
.tabs { margin-bottom: 4px; }
.muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-2xs); }
</style>
