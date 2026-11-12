<template>
  <div class="page erp-pboard">
    <HmPageHead :title="$t('common.purchasingBoard')" :sub="$t('erpPurchaseBoard.openOrderLinesOverdueIn')">
      <el-button v-perm="'purchase.purchase_req.read'" @click="router.push('/e/purchase_req')"><el-icon><Tickets /></el-icon>{{ $t('erpDocs.purchaseRequisitions') }}</el-button>
      <el-button v-perm="'purchase.purchase_order.read'" type="primary" @click="router.push('/purchase/orders')"><el-icon><DocumentAdd /></el-icon>{{ $t('erpPurchaseBoard.purchaseOrders') }}</el-button>
    </HmPageHead>

    <div class="grid g4" data-tour="kpis">
      <HmKpi :label="$t('erpPurchaseBoard.openOrderLines')" :value="b?.open_lines?.length" format="int" :unit="$t('common.row')" :loading="!b" :hint="b ? $t('erpPurchaseBoard.amount0', [fmtCompact(b.open_value)]) : ''" />
      <HmKpi :label="$t('erpPurchaseBoard.overdue')" :value="b?.late" format="int" :unit="$t('common.row')" :loading="!b" :status="b?.late ? 'bad' : 'ok'" />
      <HmKpi :label="$t('erpPurchaseBoard.inIncomingInspection')" :value="b?.inspecting?.length" format="int" :unit="$t('common.receipts')" :loading="!b" :status="b?.inspecting?.length ? 'warn' : ''" to="/purchase/receipts" />
      <HmKpi :label="$t('erpPurchaseBoard.pendingRequisitions')" :value="b?.reqs?.length" format="int" :unit="$t('common.k2835')" :loading="!b" to="/e/purchase_req" />
    </div>

    <HmCard :title="$t('erpPurchaseBoard.openPurchaseOrderLines')" flush class="mt" data-tour="pb-open">
      <el-table :data="b?.open_lines || []" size="small" max-height="380" :row-class-name="({ row }: any) => (row.late_days > 0 ? 'late' : '')"
        @row-click="(r: any) => router.push('/purchase/orders?id=' + r.purchase_order)"
        v-ai-rows="{ entity: 'material', rows: () => b?.open_lines || [], item: (r: any) => ({ kind: 'row', entity: 'material', id: r.material, label: `${r.po_code} ${r.material__label}`,
          data: { [$t('common.purchaseOrder')]: r.po_code, [$t('common.suppliers')]: r.supplier_label, [$t('common.material')]: r.material__label, [$t('erpPurchaseBoard.outstanding')]: r.left, [$t('erpPurchaseBoard.arrivalDate')]: r.due, [$t('erpPurchaseBoard.daysOverdue')]: r.late_days, id: r.material } }) }">
        <template #empty><HmEmpty compact icon="data" :title="$t('erpPurchaseBoard.noOpenOrders')" /></template>
        <el-table-column :label="$t('common.purchaseOrder')" min-width="150"><template #default="{ row }"><span class="hm-mono">{{ row.po_code }}</span></template></el-table-column>
        <el-table-column prop="supplier_label" :label="$t('common.suppliers')" min-width="160" />
        <el-table-column prop="material__label" :label="$t('common.material')" min-width="180" />
        <el-table-column :label="$t('erpPurchaseBoard.ordered')" align="right" min-width="80"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty) }}</span></template></el-table-column>
        <el-table-column :label="$t('erpPurchaseBoard.outstanding')" align="right" min-width="80"><template #default="{ row }"><b class="hm-num">{{ fmtNum(row.left) }}</b></template></el-table-column>
        <el-table-column :label="$t('erpPurchaseBoard.arrivalDate')" min-width="110"><template #default="{ row }"><span class="hm-num">{{ row.due }}</span></template></el-table-column>
        <el-table-column label="" min-width="100"><template #default="{ row }"><HmTag v-if="row.late_days > 0" tone="bad">{{ $t('common.n0DaysOverdue', [row.late_days]) }}</HmTag></template></el-table-column>
      </el-table>
    </HmCard>

    <div class="grid g2 mt">
      <HmCard :title="$t('erpPurchaseBoard.inIncomingInspection')" :sub="$t('erpPurchaseBoard.afterQcDecidesClickPut')" flush data-tour="pb-iqc">
        <el-table :data="b?.inspecting || []" size="small" max-height="300" @row-click="(r: any) => router.push('/purchase/receipts?id=' + r.id)">
          <template #empty><HmEmpty compact plain :title="$t('erpPurchaseBoard.noReceiptsAwaitingInspection')" /></template>
          <el-table-column :label="$t('erpPurchaseBoard.goodsReceipt')" min-width="150"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
          <el-table-column prop="supplier__label" :label="$t('common.suppliers')" min-width="150" />
          <el-table-column :label="$t('erpPurchaseBoard.receiptDate')" min-width="110"><template #default="{ row }"><span class="hm-num">{{ row.receive_date }}</span></template></el-table-column>
        </el-table>
      </HmCard>
      <HmCard :title="$t('erpPurchaseBoard.pendingRequisitions')" flush data-tour="pb-req">
        <el-table :data="b?.reqs || []" size="small" max-height="300" @selection-change="(s: any[]) => (sel = s)">
          <template #empty><HmEmpty compact plain :title="$t('erpPurchaseBoard.noPendingRequisitions')" /></template>
          <el-table-column type="selection" width="40" :selectable="(r: any) => r.status === 'approved'" />
          <el-table-column :label="$t('nav.purchaseRequisitions')" min-width="140"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
          <el-table-column prop="material__label" :label="$t('common.material')" min-width="160" />
          <el-table-column :label="$t('common.quantity')" align="right" min-width="80"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty) }}</span></template></el-table-column>
          <el-table-column :label="$t('common.requiredDate')" min-width="110"><template #default="{ row }"><span class="hm-num">{{ row.need_date || '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('common.status')" min-width="90"><template #default="{ row }"><HmTag :tone="row.status === 'approved' ? 'ok' : 'warn'">{{ row.status === 'approved' ? $t('common.approved2') : $t('common.pendingApproval') }}</HmTag></template></el-table-column>
        </el-table>
        <div class="foot"><el-button size="small" type="primary" :disabled="!sel.length" v-perm:disable="'purchase.purchase_order.create'" data-test="req-to-po" @click="toPo">
          {{ $t('erpPurchaseBoard.convert0SelectedRequisitionsTo', [sel.length]) }}</el-button></div>
      </HmCard>
    </div>

    <HmCard :title="$t('erpPurchaseBoard.supplierPerformanceLast180Days')" :sub="$t('erpPurchaseBoard.score60OnTimeDelivery')" flush class="mt" data-tour="pb-perf">
      <template #actions><el-button size="small" v-perm:disable="'master.supplier.update'" @click="savePerf">{{ $t('erpPurchaseBoard.writeBackSupplierScores') }}</el-button></template>
      <el-table :data="b?.performance || []" size="small">
        <template #empty><HmEmpty compact plain :title="$t('erpPurchaseBoard.noReceiptsInTheLast')" /></template>
        <el-table-column :label="$t('common.suppliers')" min-width="200"><template #default="{ row }">{{ row.code }} {{ row.name }}</template></el-table-column>
        <el-table-column :label="$t('erpPurchaseBoard.receiptLines')" align="right" min-width="80"><template #default="{ row }"><span class="hm-num">{{ row.lines }}</span></template></el-table-column>
        <el-table-column :label="$t('erpPurchaseBoard.onTimeDelivery')" align="right" min-width="110"><template #default="{ row }"><span class="hm-num">{{ fmtPct(row.otd) }}</span></template></el-table-column>
        <el-table-column :label="$t('erpPurchaseBoard.incomingPpm')" align="right" min-width="100"><template #default="{ row }"><span class="hm-num" :class="{ bad: row.ppm > 3000 }">{{ fmtNum(row.ppm) }}</span></template></el-table-column>
        <el-table-column :label="$t('erpPurchaseBoard.avgDelayDays')" align="right" min-width="110"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.avg_delay, 1) }}</span></template></el-table-column>
        <el-table-column :label="$t('erpPurchaseBoard.score')" align="right" min-width="90"><template #default="{ row }"><b class="hm-num">{{ fmtNum(row.score, 1) }}</b></template></el-table-column>
      </el-table>
    </HmCard>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { get, post } from '../api';
import { fmtCompact, fmtNum, fmtPct } from '../design/format';

const router = useRouter(), b = ref<any>(null), sel = ref<any[]>([]);
async function load() { b.value = await get('/api/purchase/board'); }
async function toPo() {
  const r = await post('/api/purchase/req_to_po', { ids: sel.value.map((x) => x.id) });
  ElMessage.success($t('erpPurchaseBoard.purchaseOrderCreated') + r.orders.map((o: any) => o.code).join($t('common.k42')));
  load();
}
async function savePerf() { await get('/api/purchase/performance?save=1'); ElMessage.success($t('erpPurchaseBoard.supplierPerformanceScoresWrittenBack')); }
onMounted(load);
</script>

<style scoped>
.mt { margin-top: var(--hm-gap); }
.foot { padding: var(--hm-sp-2) var(--hm-sp-3); display: flex; justify-content: flex-end; }
.erp-pboard :deep(tr.late > td:first-child) { box-shadow: inset 3px 0 0 var(--hm-fault); }
.erp-pboard :deep(.el-table__row) { cursor: pointer; }
.bad { color: var(--hm-fault-ink); }
</style>
