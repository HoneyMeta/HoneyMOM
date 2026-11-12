<template>
  <div class="page sp">
    <HmPageHead :title="$t('common.spareParts')" :sub="$t('spares.sparePartStockChangesOnly')">
      <el-button v-perm:disable="'spare.spare_part.create'" data-test="sp-new" @click="router.push('/e/spare_part?new=1')"><el-icon><Plus /></el-icon>{{ $t('spares.newSparePart') }}</el-button>
      <el-button v-perm:disable="'spare.spare_txn.create'" type="primary" data-test="sp-issue" @click="openTxn('issue')">{{ $t('spares.issue') }}</el-button>
      <el-button v-perm:disable="'spare.spare_txn.create'" data-test="sp-in" @click="openTxn('in')">{{ $t('common.goodsReceipt') }}</el-button>
    </HmPageHead>

    <div class="grid g4" data-test="sp-kpis">
      <HmKpi :label="$t('spares.sparePartTypes')" :value="spares.length" format="int" :unit="$t('common.k2857')" />
      <HmKpi :label="$t('spares.stockValue')" :value="stockValue" format="int" :unit="$t('common.cny')" />
      <HmKpi :label="$t('spares.belowMinimum')" :value="reorder.length" format="int" :unit="$t('common.k2857')" :status="reorder.length ? 'warn' : ''" />
      <HmKpi :label="$t('spares.criticalSparesOutOfStock')" :value="criticalShort" format="int" :unit="$t('common.k2857')" :status="criticalShort ? 'bad' : ''" :hint="$t('spares.criticalAndZeroStock')" />
    </div>

    <el-tabs v-model="tab" class="mt" data-test="sp-tabs">
      <el-tab-pane name="stock" :label="$t('common.stock')">
        <HmToolbar>
          <el-input v-model="q" clearable size="small" :placeholder="$t('spares.codeNameSpecApplicableEquipment')" style="width: 240px" />
          <el-checkbox v-model="onlyLow" size="small">{{ $t('spares.belowMinimumOnly') }}</el-checkbox>
        </HmToolbar>
        <HmCard flush class="mt-s">
          <el-table :data="stockRows" size="small" data-test="sp-table"
            v-ai-rows="{ entity: 'spare_part', rows: () => stockRows, label: (r: any) => $t('spares.sparePart01', [r.code, r.name]), data: (r: any) => ({ [$t('common.code')]: r.code, [$t('common.name')]: r.name, [$t('spares.spec')]: r.spec, [$t('common.stock')]: r.qty, [$t('spares.min')]: r.min_qty, [$t('spares.max')]: r.max_qty, [$t('common.unitPrice')]: r.price, [$t('spares.for')]: r.applies, [$t('spares.critical')]: r.critical }) }">
            <el-table-column :label="$t('common.code')" width="120"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
            <el-table-column prop="name" :label="$t('common.name')" min-width="140" />
            <el-table-column prop="spec" :label="$t('spares.spec')" min-width="110" />
            <el-table-column prop="applies" :label="$t('spares.applicableEquipmentType')" min-width="120" />
            <el-table-column :label="$t('common.stock')" width="110" align="right"><template #default="{ row }">
              <span class="hm-num" :class="low(row) ? (row.qty <= 0 ? 'bad' : 'warn') : ''">{{ fmtNum(row.qty, 0) }}</span> <span class="t3">{{ row.unit }}</span>
            </template></el-table-column>
            <el-table-column :label="$t('spares.minMax')" width="100" align="right"><template #default="{ row }"><span class="hm-num t2">{{ row.min_qty ?? '—' }} / {{ row.max_qty ?? '—' }}</span></template></el-table-column>
            <el-table-column :label="$t('common.unitPrice')" width="90" align="right"><template #default="{ row }"><span class="hm-num">{{ row.price ?? '—' }}</span></template></el-table-column>
            <el-table-column prop="location" :label="$t('labelDesigner.location')" width="90" />
            <el-table-column label="" width="70"><template #default="{ row }"><HmTag v-if="row.critical" tone="warn">{{ $t('spares.critical') }}</HmTag></template></el-table-column>
            <el-table-column label="" width="70"><template #default="{ row }"><el-button link type="primary" size="small" @click="openTxn('issue', row)">{{ $t('spares.issue') }}</el-button></template></el-table-column>
          </el-table>
          <HmEmpty v-if="!stockRows.length && loaded" plain :title="spares.length ? $t('spares.noMatchingSpareParts') : $t('spares.noSparePartsYet')" />
        </HmCard>
      </el-tab-pane>

      <el-tab-pane name="reorder" :label="$t('spares.replenishmentSuggestions0', [reorder.length])">
        <HmToolbar>
          <span class="t2 small">{{ $t('spares.suggestedQtyMaxStockCurrent') }}</span>
          <template #right>
            <el-button v-perm:disable="'spare.spare_part.update'" type="primary" size="small" :disabled="!reorder.length" :loading="busy === 'push'" data-test="sp-push" @click="push">{{ $t('spares.createRequisitionNotifyPlanner') }}</el-button>
          </template>
        </HmToolbar>
        <HmCard flush class="mt-s">
          <el-table :data="reorder" size="small" data-test="sp-reorder">
            <el-table-column :label="$t('common.code')" width="120"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
            <el-table-column prop="name" :label="$t('common.name')" min-width="140" />
            <el-table-column :label="$t('spares.stockMin')" width="110" align="right"><template #default="{ row }"><span class="hm-num" :class="row.qty <= 0 ? 'bad' : 'warn'">{{ fmtNum(row.qty, 0) }}</span><span class="hm-num t3"> / {{ row.min_qty }}</span></template></el-table-column>
            <el-table-column :label="$t('spares.suggestedPurchase')" width="100" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.suggest, 0) }} {{ row.unit || '' }}</span></template></el-table-column>
            <el-table-column :label="$t('common.amount')" width="100" align="right"><template #default="{ row }"><span class="hm-num">{{ $t('outsource.cny0', [fmtNum(row.amount, 0)]) }}</span></template></el-table-column>
            <el-table-column :label="$t('spares.usageLast90Days')" width="110" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.use_90d, 0) }}</span></template></el-table-column>
            <el-table-column prop="supplier" :label="$t('common.suppliers')" min-width="110" />
            <el-table-column :label="$t('spares.cycle')" width="70" align="right"><template #default="{ row }"><span class="hm-num">{{ row.lead_days ?? '—' }}</span> {{ $t('common.days2') }}</template></el-table-column>
            <el-table-column label="" width="70"><template #default="{ row }"><HmTag v-if="row.critical" tone="warn">{{ $t('spares.critical') }}</HmTag></template></el-table-column>
          </el-table>
          <HmEmpty v-if="!reorder.length && loaded" plain :title="$t('spares.allStockIsAboveMinimum')" />
        </HmCard>
      </el-tab-pane>

      <el-tab-pane name="history" :label="$t('spares.usageRecords')">
        <HmToolbar>
          <div class="rs"><RefSelect v-model="hEquip" entity="equipment" /></div>
          <span class="t2 small">{{ $t('spares.sparePartConsumptionByMachine') }}</span>
        </HmToolbar>
        <HmCard :title="$t('spares.summaryByMachineSparePart')" flush class="mt-s">
          <el-table :data="hist.summary" size="small" data-test="sp-hist-sum">
            <el-table-column prop="equipment__label" :label="$t('common.equipment')" min-width="120"><template #default="{ row }">{{ row.equipment__label || row.equipment || $t('spares.noEquipmentLinked') }}</template></el-table-column>
            <el-table-column :label="$t('common.spareParts')" min-width="140"><template #default="{ row }">{{ row.spare__label || row.spare }}</template></el-table-column>
            <el-table-column :label="$t('common.count')" width="70" align="right"><template #default="{ row }"><span class="hm-num">{{ row.times }}</span></template></el-table-column>
            <el-table-column :label="$t('common.quantity')" width="80" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty, 0) }}</span></template></el-table-column>
            <el-table-column :label="$t('common.amount')" width="100" align="right"><template #default="{ row }"><span class="hm-num">{{ $t('outsource.cny0', [fmtNum(row.amount, 0)]) }}</span></template></el-table-column>
            <el-table-column :label="$t('common.latest')" width="140"><template #default="{ row }"><span class="hm-num">{{ fmtTime(row.last) }}</span></template></el-table-column>
          </el-table>
          <HmEmpty v-if="!hist.summary.length" plain :title="$t('spares.noIssueRecords')" />
        </HmCard>
        <HmCard :title="$t('spares.transactionLog')" flush class="mt">
          <el-table :data="hist.items" size="small" data-test="sp-hist">
            <el-table-column :label="$t('common.docNo')" width="130"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
            <el-table-column :label="$t('common.type')" width="80"><template #default="{ row }"><HmTag :tone="row.kind === 'issue' ? 'primary' : row.kind === 'in' ? 'ok' : 'neutral'">{{ KIND[row.kind] || row.kind }}</HmTag></template></el-table-column>
            <el-table-column prop="spare__label" :label="$t('common.spareParts')" min-width="140" />
            <el-table-column :label="$t('common.quantity')" width="70" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty, 0) }}</span></template></el-table-column>
            <el-table-column :label="$t('spares.balance')" width="70" align="right"><template #default="{ row }"><span class="hm-num t2">{{ fmtNum(row.balance, 0) }}</span></template></el-table-column>
            <el-table-column prop="equipment__label" :label="$t('common.equipment')" width="110" />
            <el-table-column prop="repair_order__label" :label="$t('common.repairOrders')" width="130" />
            <el-table-column prop="by" :label="$t('outsource.handledBy')" width="80" />
            <el-table-column :label="$t('common.time')" width="140"><template #default="{ row }"><span class="hm-num">{{ fmtTime(row.time) }}</span></template></el-table-column>
          </el-table>
        </HmCard>
      </el-tab-pane>
    </el-tabs>

    <!-- Issue / receive -->
    <el-dialog v-model="tf.open" :title="tf.kind === 'issue' ? $t('spares.sparePartIssue') : $t('spares.sparePartReceipt')" width="min(560px, 96vw)" append-to-body destroy-on-close>
      <el-form label-width="96px" data-test="sp-txn-form">
        <el-form-item :label="$t('common.spareParts')" required><RefSelect v-model="tf.spare" entity="spare_part" /></el-form-item>
        <el-form-item :label="$t('common.quantity')" required><el-input-number v-model="tf.qty" :min="1" :controls="false" style="width: 140px" /></el-form-item>
        <template v-if="tf.kind === 'issue'">
          <el-form-item :label="$t('common.repairOrders')"><RefSelect v-model="tf.repair_order" entity="repair_order" /></el-form-item>
          <el-form-item :label="$t('common.equipment')"><RefSelect v-model="tf.equipment" entity="equipment" /></el-form-item>
        </template>
        <el-form-item :label="$t('common.notes')"><el-input v-model="tf.remark" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="tf.open = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="busy === 'txn'" :disabled="!tf.spare || !tf.qty" data-test="sp-txn-save" @click="saveTxn">{{ $t('common.confirm') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { get, post } from '../api';
import RefSelect from '../components/RefSelect.vue';
import { fmtNum, fmtTime } from '../design';

const router = useRouter(), route = useRoute();
const KIND: Record<string, string> = { in: $t('common.goodsReceipt'), issue: $t('spares.issue'), return: $t('common.return'), adjust: $t('erpVoucher.stocktakeAdjustment') };
const spares = ref<any[]>([]), reorder = ref<any[]>([]);
const hist = ref<any>({ items: [], summary: [] });
const loaded = ref(false), busy = ref('');
const tab = ref(String(route.query.tab || 'stock'));
const q = ref(''), onlyLow = ref(false), hEquip = ref<any>(null);

const low = (s: any) => s.min_qty != null && (s.qty || 0) < s.min_qty;
const stockValue = computed(() => spares.value.reduce((a, s) => a + (s.qty || 0) * (s.price || 0), 0));
const criticalShort = computed(() => spares.value.filter((s) => s.critical && (s.qty || 0) <= 0).length);
const stockRows = computed(() => {
  const s = q.value.trim().toLowerCase();
  return spares.value.filter((x) => (!onlyLow.value || low(x)) && (!s || [x.code, x.name, x.spec, x.applies].some((v) => String(v || '').toLowerCase().includes(s))));
});

async function load() {
  const [a, b] = await Promise.all([get('/api/e/spare_part?size=500'), get('/api/spare/reorder')]);
  spares.value = a.items || []; reorder.value = b.items || b || [];
  loaded.value = true;
}
async function loadHist() {
  hist.value = await get(`/api/spare/history${hEquip.value ? `?equipment=${hEquip.value}` : ''}`);
}
async function push() {
  busy.value = 'push';
  try { const r = await post('/api/spare/reorder/push', {}); ElMessage.success(r.msg || $t('common.submitted')); } catch { /* already notified */ } finally { busy.value = ''; }
}

const tf = reactive<any>({ open: false });
function openTxn(kind: string, row?: any) { Object.assign(tf, { open: true, kind, spare: row?.id || null, qty: 1, repair_order: null, equipment: row?.equipment || null, remark: '' }); }
async function saveTxn() {
  busy.value = 'txn';
  try {
    const r = tf.kind === 'issue'
      ? await post('/api/spare/issue', { spare: tf.spare, qty: tf.qty, repair_order: tf.repair_order, equipment: tf.equipment, remark: tf.remark || null })
      : await post('/api/e/spare_txn', { spare: tf.spare, kind: 'in', qty: tf.qty, remark: tf.remark || null });
    ElMessage.success(r.msg || $t('spares.posted')); tf.open = false;
    await load(); if (tab.value === 'history') await loadHist();
  } catch { /* already notified (insufficient stock 409) */ } finally { busy.value = ''; }
}

watch(tab, (t) => { router.replace({ query: { ...route.query, tab: t } }); if (t === 'history') loadHist(); });
watch(hEquip, loadHist);
onMounted(async () => { await load(); if (tab.value === 'history') loadHist(); });
</script>

<style scoped>
.bad { color: var(--hm-fault-ink); font-weight: 600; } .warn { color: var(--hm-idle-ink); font-weight: 600; }
.small { font-size: var(--hm-fs-2xs); } .t2 { color: var(--hm-text-2); } .t3 { color: var(--hm-text-3); }
.mt-s { margin-top: var(--hm-sp-1); }
.rs { width: 220px; }
</style>
