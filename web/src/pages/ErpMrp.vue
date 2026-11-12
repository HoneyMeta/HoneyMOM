<template>
  <div class="page erp-mrp">
    <HmPageHead :title="$t('common.mrp')" :sub="$t('erpMrp.salesOrderDeliverySchedulesForecasts')">
      <el-select v-model="horizon" class="hz"><el-option v-for="d in [30, 60, 90, 180]" :key="d" :label="$t('erpMrp.horizon0Days', [d])" :value="d" /></el-select>
      <el-button type="primary" :loading="running" v-perm:disable="'mrp.run'" data-tour="mrp-run" data-test="mrp-run" @click="run"><el-icon><Refresh /></el-icon>{{ $t('erpMrp.runMrp') }}</el-button>
      <template #meta><HmTag v-if="runInfo">{{ runInfo.code }} · {{ fmtTime(runInfo.run_at) }} · {{ runInfo.created_by }}</HmTag></template>
    </HmPageHead>

    <div class="grid g5" data-tour="kpis">
      <HmKpi :label="$t('erpMrp.plannedProduction')" :value="cnt('make')" format="int" :unit="$t('common.k2047')" :loading="loading" />
      <HmKpi :label="$t('erpMrp.plannedPurchases')" :value="cnt('buy')" format="int" :unit="$t('common.k2047')" :loading="loading" />
      <HmKpi :label="$t('erpMrp.expediteOpenOrders')" :value="cnt('expedite')" format="int" :unit="$t('common.k2047')" :loading="loading" :status="cnt('expedite') ? 'warn' : ''" />
      <HmKpi :label="$t('erpMrp.pastSuggestedReleaseDate')" :value="rows.filter((r) => r.late).length" format="int" :unit="$t('common.k2047')" :loading="loading" :status="rows.some((r) => r.late) ? 'bad' : 'ok'" />
      <HmKpi :label="$t('erpMrp.roughCutCapacityOverload')" :value="over.length" format="int" :unit="$t('erpMrp.workstationGroupWeeks')" :loading="loading" :status="over.length ? 'bad' : 'ok'" />
    </div>

    <HmToolbar class="mt" data-tour="mrp-filter">
      <el-segmented v-model="kindF" :options="[{ label: $t('common.all'), value: '' }, { label: $t('erpMrp.plannedProduction'), value: 'make' }, { label: $t('erpMrp.plannedPurchases'), value: 'buy' }, { label: $t('erpMrp.expedite'), value: 'expedite' }]" />
      <el-checkbox v-model="pendingOnly">{{ $t('erpMrp.pendingOnly') }}</el-checkbox>
      <template #right>
        <el-select v-model="buyAs" class="ba"><el-option :label="$t('erpMrp.purchaseRequisition')" value="req" /><el-option :label="$t('erpMrp.purchasePurchaseOrderDirectly')" value="po" /></el-select>
        <el-checkbox v-model="releaseWo">{{ $t('erpMrp.releaseWorkOrdersDirectlyTo') }}</el-checkbox>
        <el-button type="primary" :disabled="!sel.length" v-perm:disable="'mrp.release'" data-tour="mrp-release" data-test="mrp-release" @click="release">{{ $t('erpMrp.releaseSelected0', [sel.length]) }}</el-button>
        <el-button :disabled="!sel.length" v-perm:disable="'mrp.mrp_suggestion.ignore'" @click="ignore">{{ $t('checkTab.ignore') }}</el-button>
      </template>
    </HmToolbar>

    <div class="hm-card tbl mt" data-tour="mrp-table">
      <el-table :data="shown" size="small" v-loading="loading" max-height="560" data-test="mrp-table" @selection-change="(s: any[]) => (sel = s)" row-key="id"
        v-ai-rows="{ entity: 'mrp_suggestion', rows: () => shown, item: (r: any) => ({ kind: 'row', entity: 'mrp_suggestion', id: r.id, label: `MRP ${KIND[r.kind]} ${r.material__label} × ${fmtNum(r.qty_adj || r.qty)}`,
          data: { kind: r.kind, [$t('common.recommendation')]: KIND[r.kind], [$t('common.material')]: r.material__label, [$t('common.quantity')]: r.qty_adj || r.qty, [$t('common.requiredDate')]: r.need_date, [$t('erpMrp.suggestedRelease')]: r.release_date, [$t('common.reason')]: r.reason, id: r.id } }) }">
        <template #empty><HmEmpty compact :icon="runInfo ? 'search' : 'data'" :title="runInfo ? $t('erpMrp.noMatchingSuggestions') : $t('erpMrp.mrpHasnTBeenRun')" :desc="runInfo ? '' : $t('erpMrp.clickRunMrpInThe')" /></template>
        <el-table-column type="selection" width="40" :selectable="(r: any) => ['new', 'adjusted'].includes(r.status)" />
        <el-table-column :label="$t('erpMrp.level')" width="56" align="right"><template #default="{ row }"><span class="hm-num">{{ row.level }}</span></template></el-table-column>
        <el-table-column :label="$t('common.recommendation')" min-width="100"><template #default="{ row }"><HmTag :tone="row.kind === 'make' ? 'primary' : row.kind === 'buy' ? 'amber' : 'warn'">{{ KIND[row.kind] }}</HmTag></template></el-table-column>
        <el-table-column prop="material__label" :label="$t('common.material')" min-width="190" />
        <el-table-column :label="$t('common.quantity')" align="right" min-width="120"><template #default="{ row }">
          <b class="hm-num">{{ fmtNum(row.qty_adj || row.qty, 2) }}</b><span class="hm-unit">{{ row.unit }}</span>
          <div v-if="row.qty_adj" class="muted small">{{ $t('erpMrp.was0', [fmtNum(row.qty, 2)]) }}</div></template></el-table-column>
        <el-table-column :label="$t('common.requiredDate')" min-width="110"><template #default="{ row }"><span class="hm-num">{{ row.date_adj || row.need_date }}</span></template></el-table-column>
        <el-table-column :label="$t('erpMrp.suggestedRelease')" min-width="110"><template #default="{ row }"><span class="hm-num" :class="{ bad: row.late }">{{ row.release_date }}</span></template></el-table-column>
        <el-table-column :label="$t('erpMrp.supplierDocument')" min-width="150"><template #default="{ row }">{{ row.supplier__label || row.ref_doc || '—' }}</template></el-table-column>
        <el-table-column :label="$t('erpMrp.demandSource')" min-width="220"><template #default="{ row }">
          <span class="tops">{{ tops(row) }}</span>
          <el-button link size="small" type="primary" data-test="mrp-peg" @click.stop="peg(row)">{{ $t('common.source') }}</el-button></template></el-table-column>
        <el-table-column :label="$t('erpMrp.capacity')" min-width="90"><template #default="{ row }"><HmTag v-if="(row.capacity || []).some((x: any) => x.over)" tone="bad">{{ $t('erpMrp.overloaded') }}</HmTag><span v-else class="muted">—</span></template></el-table-column>
        <el-table-column :label="$t('common.status')" min-width="90"><template #default="{ row }"><HmTag :tone="statusTone(row.status)">{{ ST[row.status] }}</HmTag></template></el-table-column>
        <el-table-column label="" min-width="150"><template #default="{ row }">
          <el-button v-if="['new', 'adjusted'].includes(row.status)" link size="small" type="primary" v-perm="'mrp.mrp_suggestion.update'" @click.stop="adjust(row)">{{ $t('erpMrp.adjust') }}</el-button>
          <el-button v-if="aiOn('erp')" link size="small" type="primary" @click.stop="explainMrp(row.id)">{{ $t('cycle.aiExplain') }}</el-button>
          <span v-if="row.work_order__label || row.purchase_req__label || row.purchase_order__label" class="small">{{ row.work_order__label || row.purchase_order__label || row.purchase_req__label }}</span>
        </template></el-table-column>
      </el-table>
    </div>

    <HmCard :title="$t('erpMrp.roughCutCapacityCheckWorkstation')" :sub="$t('erpMrp.scheduledHoursScheduleResultPlanned')" flush class="mt" data-tour="mrp-cap">
      <el-table :data="cap" size="small" max-height="320">
        <template #empty><HmEmpty compact plain :title="$t('erpMrp.noPlannedProductionOrScheduled')" /></template>
        <el-table-column prop="group" :label="$t('erpMrp.workstationGroup')" min-width="110" />
        <el-table-column :label="$t('erpMrp.week')" min-width="110"><template #default="{ row }"><span class="hm-num">{{ row.week }}</span></template></el-table-column>
        <el-table-column :label="$t('erpMrp.scheduledH')" align="right" min-width="100"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.existing_h, 1) }}</span></template></el-table-column>
        <el-table-column :label="$t('erpMrp.plannedH')" align="right" min-width="90"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.planned_h, 1) }}</span></template></el-table-column>
        <el-table-column :label="$t('erpMrp.capacityH')" align="right" min-width="90"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.capacity_h, 1) }}</span></template></el-table-column>
        <el-table-column :label="$t('erpMrp.load')" min-width="200"><template #default="{ row }">
          <div class="lvl"><i :style="{ width: Math.min(100, (row.load_pct || 0) * 100) + '%' }" :class="{ over: row.over }" /></div>
          <span class="hm-num small" :class="{ bad: row.over }">{{ fmtPct(row.load_pct) }}</span></template></el-table-column>
      </el-table>
    </HmCard>

    <el-drawer v-model="pg.open" :title="$t('erpMrp.demandSourcePegging')" size="min(560px, 96vw)">
      <template v-if="pg.item">
        <p><b>{{ KIND[pg.item.kind] }} {{ pg.item.material__label }} × {{ fmtNum(pg.item.qty_adj || pg.item.qty, 2) }}</b></p>
        <p class="muted small reason">{{ pg.item.reason }}</p>
        <h4>{{ $t('erpMrp.directDemand') }}</h4>
        <div v-for="(p, i) in pg.list.filter((x: any) => x.kind !== 'top')" :key="'d' + i" class="peg"><HmTag>{{ PK[p.kind] || p.kind }}</HmTag> {{ p.label }} · <span class="hm-num">{{ fmtNum(p.qty, 2) }}</span> · <span class="hm-num">{{ p.date }}</span></div>
        <h4 v-if="pg.list.some((x: any) => x.kind === 'top')">{{ $t('erpMrp.topLevelDemand') }}</h4>
        <div v-for="(p, i) in pg.list.filter((x: any) => x.kind === 'top')" :key="'t' + i" class="peg"><HmTag tone="primary">{{ $t('erpMrp.orderForecast') }}</HmTag> {{ p.label }} · <span class="hm-num">{{ p.date }}</span></div>
        <h4 v-if="pg.cap.length">{{ $t('erpMrp.capacity') }}</h4>
        <div v-for="(c, i) in pg.cap" :key="'c' + i" class="peg">{{ $t('erpMrp.n0WeekOf1', [c.group, c.week]) }} <HmTag :tone="c.over ? 'bad' : 'ok'">{{ c.over ? $t('erpMrp.overloaded') : $t('erpMrp.ok') }}</HmTag></div>
      </template>
    </el-drawer>

    <el-dialog v-model="adj.open" :title="$t('erpMrp.adjustmentSuggestions')" width="min(420px, 96vw)">
      <el-form label-width="80px">
        <el-form-item :label="$t('common.quantity')"><el-input-number v-model="adj.qty" :controls="false" style="width: 100%" /></el-form-item>
        <el-form-item :label="$t('common.requiredDate')"><el-date-picker v-model="adj.date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
        <el-form-item v-if="adj.item?.kind === 'buy'" :label="$t('common.suppliers')"><RefSelect v-model="adj.supplier" entity="supplier" :label="adj.item?.supplier__label" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="adj.open = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" @click="saveAdj">{{ $t('common.save') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, reactive, ref } from 'vue';
import { aiOn } from '../ai';
import { get, post, put } from '../api';
import RefSelect from '../components/RefSelect.vue';
import { fmtNum, fmtPct, fmtTime } from '../design/format';
import { explainMrp } from '../erp/aiActions';
import { statusTone } from '../erp/docs';

const KIND: Record<string, string> = { make: $t('erpMrp.plannedProduction'), buy: $t('erpMrp.plannedPurchases'), expedite: $t('erpMrp.expedite'), defer: $t('erpMrp.postpone') };
const ST: Record<string, string> = { new: $t('common.pending'), adjusted: $t('erpMrp.adjusted'), released: $t('common.released'), ignored: $t('onboardCommon.ignored') };
const PK: Record<string, string> = { so: $t('erpMrp.order'), forecast: $t('erpMrp.forecast'), safety: $t('erpMrp.safetyStock'), wo: $t('erpMrp.woMaterial'), parent: $t('erpMrp.parentPlan'), supply: $t('erpMrp.onOrder') };
const rows = ref<any[]>([]), runInfo = ref<any>(null), loading = ref(true), running = ref(false), sel = ref<any[]>([]);
const horizon = ref(90), kindF = ref(''), pendingOnly = ref(true), buyAs = ref('req'), releaseWo = ref(false);
const shown = computed(() => rows.value.filter((r) => (!kindF.value || r.kind === kindF.value) && (!pendingOnly.value || ['new', 'adjusted'].includes(r.status))));
const cnt = (k: string) => rows.value.filter((r) => r.kind === k && ['new', 'adjusted'].includes(r.status)).length;
const cap = computed(() => runInfo.value?.stats?.capacity || []);
const over = computed(() => cap.value.filter((x: any) => x.over));
const tops = (r: any) => [...new Set((r.pegging || []).filter((p: any) => p.kind === 'top').map((p: any) => p.ref))].join($t('common.k42')) || (r.pegging || []).map((p: any) => p.label).slice(0, 1).join('');

async function load() {
  loading.value = true;
  try { const r = await get('/api/mrp/latest'); rows.value = r.suggestions; runInfo.value = r.run; } finally { loading.value = false; }
}
async function run() {
  running.value = true;
  try { const r = await post('/api/mrp/run', { horizon: horizon.value }); ElMessage.success($t('erpMrp.mrpComplete0Suggestions', [r.count])); await load(); } finally { running.value = false; }
}
async function release() {
  const r = await post('/api/mrp/release', { ids: sel.value.map((x) => x.id), buy_as: buyAs.value, release_wo: releaseWo.value });
  if (r.pending_approval) { ElMessage.success(r.msg || $t('erpDocs.submittedForApproval')); return; }
  ElMessage.success([$t('common.released'), r.work_orders.length && $t('erpMrp.workOrders0', [r.work_orders.join($t('common.k42'))]), r.reqs.length && $t('erpMrp.requisitions0', [r.reqs.length]), r.orders.length && $t('erpMrp.purchaseOrders0', [r.orders.join($t('common.k42'))]),
    r.expedited.length && $t('erpMrp.expedited0', [r.expedited.length])].filter(Boolean).join($t('common.k764')));
  load();
}
async function ignore() { for (const s of sel.value) await post(`/api/e/mrp_suggestion/${s.id}/do/ignore`, {}); ElMessage.success($t('onboardCommon.ignored')); load(); }
const pg = reactive<any>({ open: false, item: null, list: [], cap: [] });
async function peg(r: any) { const x = await get(`/api/mrp/pegging/${r.id}`); pg.item = x.item; pg.list = x.pegging; pg.cap = x.capacity; pg.open = true; }
const adj = reactive<any>({ open: false, item: null, qty: 0, date: '', supplier: null });
function adjust(r: any) { Object.assign(adj, { open: true, item: r, qty: r.qty_adj || r.qty, date: r.date_adj || r.need_date, supplier: r.supplier }); }
async function saveAdj() { await put(`/api/mrp/suggestions/${adj.item.id}`, { qty: adj.qty, date: adj.date, supplier: adj.supplier }); adj.open = false; load(); }
onMounted(load);
</script>

<style scoped>
.hz { width: calc(140px * var(--hm-scale)); }
.ba { width: calc(190px * var(--hm-scale)); }
.g5 { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: var(--hm-gap); }
@media (max-width: 1279px) { .g5 { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.mt { margin-top: var(--hm-gap); }
.tbl { padding: 0; overflow: hidden; }
.bad { color: var(--hm-fault-ink); font-weight: 600; }
.tops { font-size: var(--hm-fs-xs); color: var(--hm-text-2); margin-right: 4px; }
.lvl { display: inline-block; width: 110px; height: 6px; border-radius: 3px; background: var(--hm-surface-3); vertical-align: middle; margin-right: 6px; }
.lvl i { display: block; height: 100%; border-radius: 3px; background: var(--hm-cyan); }
.lvl i.over { background: var(--hm-fault); }
.peg { padding: 4px 0; border-bottom: 1px solid var(--hm-hairline); }
.reason { white-space: pre-wrap; }
h4 { margin: var(--hm-sp-3) 0 var(--hm-sp-1); font-size: var(--hm-fs-sm); }
</style>
