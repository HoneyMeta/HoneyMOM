<template>
  <div class="page">
    <HmPageHead :title="$t('common.stock')" :sub="$t('stock.chargeMaterialsBackflushedPerHeat')" art="material">
      <el-button @click="router.push('/e/stock_txn')"><el-icon><Tickets /></el-icon>{{ $t('stock.inventoryTransactions') }}</el-button>
      <el-button @click="router.push('/e/material_request')"><el-icon><DocumentAdd /></el-icon>{{ $t('stock.materialRequests') }}</el-button>
      <el-button type="primary" data-tour="stock-count" @click="countOpen = true"><el-icon><Finished /></el-icon>{{ $t('stock.cycleCount') }}</el-button>
    </HmPageHead>

    <div class="grid g4" data-tour="kpis">
      <HmKpi :label="$t('stock.stockItems')" :value="rows.length" format="int" :unit="$t('common.k2360')" :loading="loading" :hint="$t('stock.n0Warehouses', [whs.length])" />
      <HmKpi :label="$t('stock.belowSafetyStock')" :value="low.length" format="int" :unit="$t('common.k2360')" :status="low.length ? 'bad' : 'ok'" :loading="loading" :hint="low.length ? low.slice(0, 2).map((r) => r.material__label).join($t('common.k42')) : $t('stock.allSufficient')" />
      <HmKpi :label="$t('erpCost.materialItems')" :value="new Set(rows.map((r) => r.material)).size" format="int" :unit="$t('common.k2857')" :loading="loading" />
      <HmKpi :label="$t('stock.zeroStock')" :value="rows.filter((r) => !r.qty).length" format="int" :unit="$t('common.k2360')" :status="rows.some((r) => !r.qty) ? 'warn' : ''" :loading="loading" />
    </div>

    <HmToolbar class="mt">
      <el-segmented v-model="wh" class="seg" :options="[{ label: $t('stock.allWarehouses'), value: '' }, ...whs.map((w) => ({ label: w, value: w }))]" />
      <el-checkbox v-model="onlyLow">{{ $t('stock.belowSafetyStockOnly') }}</el-checkbox>
      <el-input v-model="kw" :placeholder="$t('common.material')" clearable class="kw"><template #prefix><el-icon><Search /></el-icon></template></el-input>
    </HmToolbar>

    <div class="hm-card tbl mt" data-tour="stock-table">
      <el-table :data="shown" size="small" v-loading="loading" :row-class-name="({ row }: any) => (row.low ? 'low' : '')"
        v-ai-rows="{ entity: 'material', rows: () => shown, item: (r: any) => ({ kind: 'row', entity: 'material', id: r.material, label: $t('stock.n0Stock12', [r.material__label, fmtNum(r.qty, 2), r.unit || '']),
          data: { [$t('common.warehouse')]: r.warehouse__label, [$t('common.material')]: r.material__label, [$t('common.stock')]: r.qty, [$t('common.unit')]: r.unit, [$t('erpMrp.safetyStock')]: r.safety, [$t('stock.belowSafetyStock')]: !!r.low, id: r.material } }) }">
        <template #empty><HmEmpty compact :icon="kw || wh || onlyLow ? 'search' : 'data'" :title="kw || wh || onlyLow ? $t('stock.noMatchingStock') : $t('stock.noStockYet')" /></template>
        <el-table-column prop="warehouse__label" :label="$t('common.warehouse')" min-width="140" />
        <el-table-column prop="material__label" :label="$t('common.material')" min-width="200" />
        <el-table-column :label="$t('common.stock')" align="right" min-width="130"><template #default="{ row }"><b class="hm-num" :class="{ bad: row.low }">{{ fmtNum(row.qty, 2) }}</b><span class="hm-unit">{{ row.unit }}</span></template></el-table-column>
        <el-table-column :label="$t('erpMrp.safetyStock')" align="right" min-width="110"><template #default="{ row }"><span class="hm-num">{{ row.safety ?? '—' }}</span></template></el-table-column>
        <el-table-column :label="$t('stock.level')" min-width="160"><template #default="{ row }">
          <div v-if="row.safety" class="lvl"><i :style="{ width: Math.min(100, (row.qty || 0) / (row.safety * 2) * 100) + '%' }" :class="{ low: row.low }" /><em :style="{ left: '50%' }" /></div>
          <span v-else class="muted small">—</span></template></el-table-column>
        <el-table-column label="" width="120"><template #default="{ row }"><HmTag v-if="row.low" tone="bad">{{ $t('stock.belowSafetyStock') }}</HmTag></template></el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="countOpen" :title="$t('stock.cycleCount2')" width="min(580px, 96vw)">
      <el-form label-width="80px"><el-form-item :label="$t('common.warehouse')"><RefSelect v-model="cnt.warehouse" entity="warehouse" /></el-form-item></el-form>
      <el-table :data="cntRows" size="small" max-height="360">
        <template #empty><HmEmpty compact :title="$t('stock.selectAWarehouseFirst')" /></template>
        <el-table-column prop="material__label" :label="$t('common.material')" /><el-table-column :label="$t('stock.book')" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty, 2) }}</span></template></el-table-column>
        <el-table-column :label="$t('stock.counted')" width="140"><template #default="{ row }"><el-input-number v-model="row.actual" :controls="false" style="width:120px" /></template></el-table-column>
      </el-table>
      <template #footer><el-button @click="countOpen = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :disabled="!cntRows.length" @click="doCount">{{ $t('stock.postCountAdjustment') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { get, post } from '../api';
import RefSelect from '../components/RefSelect.vue';
import { fmtNum } from '../design/format';
import './aiActions';

const router = useRouter(), rows = ref<any[]>([]), loading = ref(true), countOpen = ref(false), cnt = reactive<any>({ warehouse: null });
const wh = ref(''), kw = ref(''), onlyLow = ref(false), cntRows = ref<any[]>([]);
const whs = computed(() => [...new Set(rows.value.map((r) => r.warehouse__label).filter(Boolean))]);
const low = computed(() => rows.value.filter((r) => r.low));
const shown = computed(() => { const k = kw.value.trim().toLowerCase(); return rows.value.filter((r) => (!wh.value || r.warehouse__label === wh.value) && (!onlyLow.value || r.low) && (!k || String(r.material__label).toLowerCase().includes(k))); });
watch(() => cnt.warehouse, (w) => { cntRows.value = rows.value.filter((r) => r.warehouse === w).map((r) => ({ ...r, actual: +(r.qty || 0).toFixed(3) })); });
async function load() { loading.value = true; try { rows.value = await get('/api/warehouse/overview'); } finally { loading.value = false; } }
async function doCount() {
  const r = await post('/api/warehouse/count', { warehouse: cnt.warehouse, lines: cntRows.value.map((x) => ({ material: x.material, actual: x.actual })) });
  ElMessage.success($t('stock.n0ItemsAdjusted', [r.adjusted])); countOpen.value = false; load();
}
onMounted(load);
</script>

<style scoped>
.kw { width: calc(180px * var(--hm-scale)); }
.seg { max-width: 100%; overflow-x: auto; }   /* long warehouse names (English) scroll instead of widening the page */
.tbl { padding: 0; overflow: hidden; }
.tbl :deep(tr.low > td:first-child) { box-shadow: inset 3px 0 0 var(--hm-fault); }
b.bad { color: var(--hm-fault-ink); }
.lvl { position: relative; height: 6px; border-radius: 3px; background: var(--hm-surface-3); overflow: visible; }
.lvl i { display: block; height: 100%; border-radius: 3px; background: var(--hm-cyan); } .lvl i.low { background: var(--hm-fault); }
.lvl em { position: absolute; top: -3px; bottom: -3px; width: 1px; background: var(--hm-text-3); }
</style>
