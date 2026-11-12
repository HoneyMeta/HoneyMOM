<template>
  <div class="page erp-cost">
    <HmPageHead :title="$t('common.costing')" :sub="$t('erpCost.actualWorkOrderCostMaterial')">
      <el-button v-perm:disable="'cost.calc'" data-tour="cost-calc" data-test="cost-calc" :loading="busy" @click="calc"><el-icon><Refresh /></el-icon>{{ $t('erpCost.recalculateWoCosts') }}</el-button>
      <el-button v-perm:disable="'cost.calc'" @click="rollUp">{{ $t('erpCost.standardCostRollUp') }}</el-button>
      <el-button @click="router.push('/e/cost_rate')">{{ $t('erpCost.rates') }}</el-button>
    </HmPageHead>
    <el-tabs v-model="tab" class="tabs" data-tour="cost-tabs">
      <el-tab-pane :label="$t('erpCost.workOrderCosts')" name="wo" /><el-tab-pane :label="$t('erpCost.varianceAnalysis')" name="var" /><el-tab-pane :label="$t('erpCost.inventoryValuation')" name="inv" /><el-tab-pane :label="$t('erpCost.monthEndClose')" name="close" />
    </el-tabs>

    <template v-if="tab === 'wo'">
      <div class="grid g4" data-tour="kpis">
        <HmKpi :label="$t('erpCost.workOrders')" :value="wos.length" format="int" :unit="$t('common.k2835')" />
        <HmKpi :label="$t('erpCost.totalActualCost')" :value="sum(wos, 'total')" format="compact" :unit="$t('common.cny')" />
        <HmKpi :label="$t('erpCost.totalStandardCost')" :value="sum(wos, 'std_total')" format="compact" :unit="$t('common.cny')" />
        <HmKpi :label="$t('erpCost.varianceActualStandard')" :value="sum(wos, 'variance')" format="compact" :unit="$t('common.cny')" :status="sum(wos, 'variance') > 0 ? 'warn' : 'ok'" />
      </div>
      <HmCard flush class="mt" data-tour="cost-table">
        <el-table :data="wos" size="small" max-height="520" @row-click="openWo"
          v-ai-rows="{ entity: 'work_order', rows: () => wos, item: (r: any) => ({ kind: 'row', entity: 'work_order', id: r.work_order, label: $t('erpCost.workOrderCosts0', [r.work_order__label]),
            data: { [$t('common.workOrder')]: r.work_order__label, [$t('common.product')]: r.product__label, [$t('common.complete')]: r.qty_done, [$t('erpCost.actualUnitCost')]: r.unit_cost, [$t('erpCost.standardUnitCost')]: r.std_unit, [$t('erpCost.variance')]: r.variance, id: r.work_order } }) }">
          <template #empty><HmEmpty compact icon="data" :title="$t('erpCost.noWorkOrderCostsCalculated')" :desc="$t('erpCost.clickRecalculateWoCosts')" /></template>
          <el-table-column prop="work_order__label" :label="$t('common.workOrder')" min-width="140" />
          <el-table-column prop="product__label" :label="$t('common.product')" min-width="170" />
          <el-table-column prop="period" :label="$t('erpArAp.period')" min-width="90" />
          <el-table-column v-for="c in COLS" :key="c[0]" :label="c[1]" align="right" min-width="100"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row[c[0]], 2) }}</span></template></el-table-column>
          <el-table-column :label="$t('erpCost.unitCost')" align="right" min-width="100"><template #default="{ row }"><b class="hm-num">{{ fmtNum(row.unit_cost, 2) }}</b></template></el-table-column>
          <el-table-column :label="$t('common.standard')" align="right" min-width="90"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.std_unit, 2) }}</span></template></el-table-column>
          <el-table-column :label="$t('erpCost.variance')" align="right" min-width="110"><template #default="{ row }"><span class="hm-num" :class="{ bad: row.variance > 0 }">{{ fmtNum(row.variance, 2) }}</span></template></el-table-column>
          <el-table-column label="" min-width="80"><template #default="{ row }"><HmTag v-if="row.status === 'frozen'" tone="primary">{{ $t('erpCost.closed') }}</HmTag></template></el-table-column>
        </el-table>
      </HmCard>
    </template>

    <template v-else-if="tab === 'var'">
      <HmToolbar><el-segmented v-model="vg" :options="[{ label: $t('erpCost.byProduct'), value: 'product' }, { label: $t('erpCost.byPeriod'), value: 'period' }, { label: $t('erpCost.byWorkOrder'), value: 'work_order' }]" @change="loadVar" /></HmToolbar>
      <div class="grid g2 mt">
        <HmCard :title="$t('erpCost.varianceBreakdown')" data-tour="var-chart"><Chart :option="varOpt" :height="300" :ai="$t('erpCost.costVariance')" /></HmCard>
        <HmCard :title="$t('erpCost.varianceDetails')" flush>
          <el-table :data="vars" size="small" max-height="340">
            <template #empty><HmEmpty compact plain :title="$t('common.noData')" /></template>
            <el-table-column prop="label" :label="$t('common.object')" min-width="160" />
            <el-table-column :label="$t('erpCost.actual')" align="right" min-width="100"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.actual, 2) }}</span></template></el-table-column>
            <el-table-column :label="$t('common.standard')" align="right" min-width="100"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.std, 2) }}</span></template></el-table-column>
            <el-table-column :label="$t('erpCost.variance')" align="right" min-width="100"><template #default="{ row }"><b class="hm-num" :class="{ bad: row.variance > 0 }">{{ fmtNum(row.variance, 2) }}</b></template></el-table-column>
            <el-table-column :label="$t('erpCost.variance2')" align="right" min-width="80"><template #default="{ row }"><span class="hm-num">{{ fmtPct(row.pct) }}</span></template></el-table-column>
          </el-table>
        </HmCard>
      </div>
    </template>

    <template v-else-if="tab === 'inv'">
      <div class="grid g4">
        <HmKpi :label="$t('erpCost.inventoryValue')" :value="inv.total" format="compact" :unit="$t('common.cny')" />
        <HmKpi :label="$t('erpCost.materialItems')" :value="inv.items.length" format="int" :unit="$t('common.k2857')" />
        <HmKpi :label="$t('erpCost.rawMaterials')" :value="byType('raw')" format="compact" :unit="$t('common.cny')" />
        <HmKpi :label="$t('erpCost.finishedSemiFinished')" :value="byType('fg') + byType('semi')" format="compact" :unit="$t('common.cny')" />
      </div>
      <HmCard :title="$t('erpCost.inventoryBalanceMovingAverage')" flush class="mt" data-tour="inv-table">
        <el-table :data="inv.items" size="small" max-height="520">
          <template #empty><HmEmpty compact icon="data" :title="$t('erpCost.notValuedYet')" /></template>
          <el-table-column :label="$t('common.material')" min-width="200"><template #default="{ row }">{{ row.code }} {{ row.name }}</template></el-table-column>
          <el-table-column :label="$t('common.quantity')" align="right" min-width="100"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty, 2) }}</span><span class="hm-unit">{{ row.unit }}</span></template></el-table-column>
          <el-table-column :label="$t('common.unitPrice')" align="right" min-width="100"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.avg_cost, 2) }}</span></template></el-table-column>
          <el-table-column :label="$t('common.amount')" align="right" min-width="120"><template #default="{ row }"><b class="hm-num">{{ fmtNum(row.value, 2) }}</b></template></el-table-column>
        </el-table>
      </HmCard>
    </template>

    <template v-else>
      <HmCard :title="$t('erpCost.accountingPeriod')" :sub="$t('erpCost.closeCatchUpValuationCalculate')" flush data-tour="close-table">
        <el-table :data="periods" size="small">
          <el-table-column prop="code" :label="$t('erpArAp.period')" min-width="100" />
          <el-table-column :label="$t('common.status')" min-width="90"><template #default="{ row }"><HmTag :tone="row.status === 'closed' ? 'primary' : 'ok'">{{ row.status === 'closed' ? $t('erpCost.closed') : $t('erpCost.open') }}</HmTag></template></el-table-column>
          <el-table-column :label="$t('erpCost.inventoryValue')" align="right" min-width="120"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.snapshot?.inventory_value, 2) }}</span></template></el-table-column>
          <el-table-column :label="$t('erpCost.frozenWos')" align="right" min-width="90"><template #default="{ row }"><span class="hm-num">{{ row.snapshot?.wo_frozen ?? '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('erpCost.close')" min-width="170"><template #default="{ row }">{{ row.closed_by || '' }} <span class="hm-num muted">{{ fmtTime(row.closed_at) }}</span></template></el-table-column>
          <el-table-column label="" min-width="140"><template #default="{ row }">
            <el-button v-if="row.status === 'open'" size="small" type="primary" plain v-perm:disable="'cost.fin_period.close'" data-test="period-close" @click="close(row)">{{ $t('erpCost.monthEndClose') }}</el-button>
            <el-button v-else size="small" v-perm:disable="'cost.fin_period.reopen'" @click="reopen(row)">{{ $t('erpCost.reopen') }}</el-button>
          </template></el-table-column>
        </el-table>
      </HmCard>
    </template>

    <el-drawer v-model="wd.open" :title="$t('erpCost.workOrderCosts0', [wd.item?.work_order__label || ''])" size="min(640px, 96vw)">
      <template v-if="wd.item">
        <div class="grid g3">
          <HmKpi :label="$t('erpCost.actualUnitCost')" :value="wd.item.unit_cost" format="num" :digits="2" :unit="$t('common.cny')" :ai="false" />
          <HmKpi :label="$t('erpCost.standardUnitCost')" :value="wd.item.std_unit" format="num" :digits="2" :unit="$t('common.cny')" :ai="false" />
          <HmKpi :label="$t('erpCost.variance')" :value="wd.item.variance" format="num" :digits="2" :unit="$t('common.cny')" :ai="false" />
        </div>
        <h4>{{ $t('erpCost.varianceBreakdown') }}</h4>
        <el-table :data="Object.entries(wd.item.var_detail || {}).map(([k, v]: any) => ({ k, ...v }))" size="small">
          <el-table-column :label="$t('erpCost.costElement')" min-width="90"><template #default="{ row }">{{ EL[row.k] }}</template></el-table-column>
          <el-table-column :label="$t('common.standard')" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.std, 2) }}</span></template></el-table-column>
          <el-table-column :label="$t('erpCost.actual')" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.actual, 2) }}</span></template></el-table-column>
          <el-table-column :label="$t('erpCost.variance')" align="right"><template #default="{ row }"><b class="hm-num" :class="{ bad: row.diff > 0 }">{{ fmtNum(row.diff, 2) }}</b></template></el-table-column>
        </el-table>
        <h4>{{ $t('erpCost.calculationDetails') }}</h4>
        <div v-for="k in ['mat', 'lab', 'mach', 'energy', 'ovh']" :key="k" class="det">
          <b>{{ EL[k] }}</b>
          <div v-for="(d, i) in (wd.item.detail?.[k] || []).slice(0, 12)" :key="i" class="small muted">{{ detLine(k, d) }}</div>
        </div>
        <p class="small muted">{{ $t('erpCost.labor0HMachine1', [fmtNum(wd.item.detail?.lab_h, 2), fmtNum(wd.item.detail?.mach_h, 2), fmtNum(wd.item.detail?.kwh, 2)]) }}</p>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { get, post } from '../api';
import Chart from '../components/Chart.vue';
import { fmtNum, fmtPct, fmtTime } from '../design/format';

const router = useRouter();
const COLS = [['mat', $t('ecn.material')], ['lab', $t('common.manual2')], ['mach', $t('erpCost.machine')], ['energy', $t('erpCost.energy')], ['ovh', $t('erpCost.overhead')], ['total', $t('erpCost.actualTotal')]];
const EL: Record<string, string> = { mat: $t('ecn.material'), lab: $t('common.manual2'), mach: $t('erpCost.machine'), energy: $t('erpCost.energy'), ovh: $t('erpCost.overhead') };
const tab = ref('wo'), wos = ref<any[]>([]), vars = ref<any[]>([]), vg = ref('product'), inv = ref<any>({ items: [], total: 0 }), periods = ref<any[]>([]), busy = ref(false);
const sum = (rs: any[], k: string) => rs.reduce((a, r) => a + (r[k] || 0), 0);
const byType = (t: string) => inv.value.items.filter((x: any) => x.mtype === t).reduce((a: number, x: any) => a + x.value, 0);
const varOpt = computed(() => {
  const top = vars.value.slice(0, 10);
  return { tooltip: { trigger: 'axis' }, legend: { top: 0 }, grid: { left: 8, right: 12, top: 30, bottom: 8 },
    xAxis: { type: 'category', data: top.map((x) => String(x.label).slice(0, 12)) }, yAxis: { type: 'value', name: $t('common.cny') },
    series: ['mat', 'lab', 'mach', 'energy', 'ovh'].map((k) => ({ type: 'bar', stack: 'v', name: EL[k], data: top.map((x) => x[k]) })) };
});
const wd = reactive<any>({ open: false, item: null });
function openWo(r: any) { wd.item = r; wd.open = true; }
function detLine(k: string, d: any) {
  if (k === 'mat') return $t('erpCost.n01234', [d.material, fmtNum(d.qty, 3), fmtNum(d.unit_cost, 2), fmtNum(d.value, 2), d.doc]);
  if (k === 'lab' || k === 'mach') return `${d.report || $t('erpCost.operation') + (d.op ?? '')} ${fmtNum(d.hours, 2)} h × ${d.rate ?? '—'} = ${fmtNum(d.value, 2)}${d.by ? $t('common.k25') + d.by + $t('common.k386') : ''}`;
  if (k === 'energy') return `${fmtNum(d.kwh, 2)} kWh × ${d.price} = ${fmtNum(d.value, 2)}`;
  return `${d.rule} ${d.basis} ${d.rate} = ${fmtNum(d.value, 2)}`;
}
async function loadWo() { wos.value = await get('/api/cost/work_orders'); }
async function loadVar() { vars.value = await get(`/api/cost/variance?group=${vg.value}`); }
async function loadInv() { inv.value = await get('/api/cost/valuation'); }
async function loadPeriods() { periods.value = await get('/api/cost/periods'); }
async function calc() { busy.value = true; try { const r = await post('/api/cost/work_orders', {}); ElMessage.success($t('erpCost.calculated0WorkOrders', [r.count])); loadWo(); } finally { busy.value = false; } }
async function rollUp() { const r = await post('/api/cost/roll_up', {}); ElMessage.success($t('erpCost.standardCost0MaterialsChanged', [r.changed])); }
async function close(p: any) {
  try { await ElMessageBox.confirm($t('erpCost.close0DocumentsDatedIn', [p.code]), $t('erpCost.monthEndClose'), { type: 'warning' }); } catch { return; }
  const r = await post(`/api/e/fin_period/${p.id}/do/close`, {}); ElMessage.success(r.msg); loadPeriods();
}
async function reopen(p: any) {
  let reason = '';
  try { reason = (await ElMessageBox.prompt($t('erpCost.reopenReason'), $t('erpCost.reopen0', [p.code]), { inputValidator: (v) => !!v || $t('common.required') })).value; } catch { return; }
  const r = await post(`/api/e/fin_period/${p.id}/do/reopen`, { reason }); ElMessage.success(r.msg); loadPeriods();
}
watch(tab, (t) => (t === 'wo' ? loadWo() : t === 'var' ? loadVar() : t === 'inv' ? loadInv() : loadPeriods()));
onMounted(loadWo);
</script>

<style scoped>
.mt { margin-top: var(--hm-gap); }
.bad { color: var(--hm-fault-ink); }
.erp-cost :deep(.el-table__row) { cursor: pointer; }
h4 { margin: var(--hm-sp-4) 0 var(--hm-sp-2); font-size: var(--hm-fs-sm); }
.det { margin-bottom: var(--hm-sp-2); }
</style>
