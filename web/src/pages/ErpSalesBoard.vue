<template>
  <div class="page erp-board">
    <HmPageHead :title="$t('common.orderBoard')" :sub="$t('erpSalesBoard.onTimeDeliveryOpenOrders', [days])">
      <el-button v-perm="'sales.sales_order.read'" @click="router.push('/sales/orders')"><el-icon><Tickets /></el-icon>{{ $t('erpSalesBoard.salesOrders') }}</el-button>
      <el-button v-if="aiOn('erp')" data-test="board-ai" @click="atRisk"><el-icon><MagicStick /></el-icon>{{ $t('erpSalesBoard.aiRiskCheck') }}</el-button>
    </HmPageHead>

    <div class="grid g4" data-tour="kpis">
      <HmKpi :label="$t('erpSalesBoard.onTimeDelivery')" :value="b?.otd" format="pct" :loading="!b" :status="b?.otd == null ? '' : b.otd >= 0.95 ? 'ok' : b.otd >= 0.85 ? 'warn' : 'bad'"
        :hint="b ? $t('erpSalesBoard.n1DueInLast0', [b.otd_window, fmtNum(b.due_qty), fmtNum(b.late_qty)]) : ''" />
      <HmKpi :label="$t('erpSalesBoard.openOrders')" :value="b?.backlog_value" format="compact" :unit="$t('common.cny')" :loading="!b" :hint="b ? $t('erpSalesBoard.n0Orders1ScheduleLines', [b.backlog_orders, b.backlog_lines]) : ''" />
      <HmKpi :label="$t('common.overdue')" :value="b?.overdue" format="int" :unit="$t('common.k2047')" :loading="!b" :status="b?.overdue ? 'bad' : 'ok'" to="/sales/orders" />
      <HmKpi :label="$t('erpSalesBoard.atRiskInclDueSoon')" :value="b?.risk_count" format="int" :unit="$t('common.k2047')" :loading="!b" :status="b?.risk_count ? 'warn' : 'ok'" />
    </div>

    <div class="grid g2 mt">
      <HmCard :title="$t('erpSalesBoard.openAmountByDueWeek')" data-tour="board-weeks"><Chart :option="weekOpt" :height="230" :ai="$t('erpSalesBoard.openAmountByWeek')" /></HmCard>
      <HmCard :title="$t('erpSalesBoard.onTimeDeliveryByMonth')" data-tour="board-otd"><Chart :option="otdOpt" :height="230" :ai="$t('erpSalesBoard.onTimeDeliveryByMonth2')" /></HmCard>
    </div>

    <HmCard :title="$t('erpSalesBoard.atRiskDeliverySchedules')" :sub="$t('erpSalesBoard.overdueExpectedLateDueSoon')" flush class="mt" data-tour="board-risk">
      <el-table :data="b?.at_risk || []" size="small" max-height="440" data-test="board-risk" @row-click="(r: any) => router.push('/sales/orders?id=' + r.sales_order)"
        v-ai-rows="{ entity: 'sales_order', rows: () => b?.at_risk || [], item: (r: any) => ({ kind: 'row', entity: 'sales_order', id: r.sales_order,
          label: $t('erpSalesBoard.n0Line12', [r.so_code, r.seq, r.risk_label]), data: { [$t('erpMrp.order')]: r.so_code, [$t('common.customer')]: r.customer, [$t('common.material')]: r.material_label, [$t('common.dueDate')]: r.due, [$t('erpSalesBoard.short')]: r.left, [$t('erpSalesBoard.expected')]: r.eta, [$t('common.reason')]: r.reason, id: r.sales_order } }) }">
        <template #empty><HmEmpty compact icon="data" :title="$t('erpSalesBoard.noAtRiskOrders')" /></template>
        <el-table-column :label="$t('daily.risks')" min-width="110"><template #default="{ row }"><HmTag :tone="row.risk === 'overdue' ? 'bad' : row.risk === 'at_risk' ? 'warn' : 'neutral'">{{ row.risk_label }}</HmTag></template></el-table-column>
        <el-table-column :label="$t('erpMrp.order')" min-width="150"><template #default="{ row }"><span class="hm-mono">{{ row.so_code }}</span> <span class="muted small">{{ $t('hmDataTable.row0', [row.seq]) }}</span></template></el-table-column>
        <el-table-column prop="customer" :label="$t('common.customer')" min-width="110" />
        <el-table-column prop="material_label" :label="$t('common.material')" min-width="180" />
        <el-table-column :label="$t('common.dueDate')" min-width="110"><template #default="{ row }"><span class="hm-num">{{ row.due }}</span></template></el-table-column>
        <el-table-column :label="$t('erpSalesBoard.short')" align="right" min-width="80"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.left) }}</span></template></el-table-column>
        <el-table-column :label="$t('erpSalesBoard.expectedReady')" min-width="110"><template #default="{ row }"><span class="hm-num">{{ row.eta || '—' }}</span></template></el-table-column>
        <el-table-column prop="reason" :label="$t('common.reason')" min-width="300" />
      </el-table>
    </HmCard>

    <HmCard :title="$t('erpSalesBoard.openOrdersByCustomer')" flush class="mt" data-tour="board-cust">
      <el-table :data="b?.by_customer || []" size="small">
        <template #empty><HmEmpty compact plain :title="$t('erpSalesBoard.noOpenOrders')" /></template>
        <el-table-column prop="customer" :label="$t('common.customer')" min-width="160" />
        <el-table-column :label="$t('erpDocs.deliverySchedule')" align="right" min-width="90"><template #default="{ row }"><span class="hm-num">{{ row.lines }}</span></template></el-table-column>
        <el-table-column :label="$t('erpSalesBoard.openQty')" align="right" min-width="110"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty) }}</span></template></el-table-column>
        <el-table-column :label="$t('erpSalesBoard.openAmountInclTax')" align="right" min-width="150"><template #default="{ row }"><b class="hm-num">{{ fmtNum(row.value, 2) }}</b></template></el-table-column>
      </el-table>
    </HmCard>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { aiOn } from '../ai';
import { get } from '../api';
import Chart from '../components/Chart.vue';
import { CH } from '../design/chart';
import { fmtNum } from '../design/format';
import { atRisk } from '../erp/aiActions';

const router = useRouter(), b = ref<any>(null), days = 90;
const weekOpt = computed(() => ({
  tooltip: { trigger: 'axis' }, grid: { left: 8, right: 12, top: 16, bottom: 8 },
  xAxis: { type: 'category', data: (b.value?.by_week || []).map((x: any) => x.week.slice(5)) },
  yAxis: { type: 'value', name: $t('common.cny') }, series: [{ type: 'bar', name: $t('erpSalesBoard.openAmount'), data: (b.value?.by_week || []).map((x: any) => x.value), barMaxWidth: 28 }],
}));
const otdOpt = computed(() => ({
  tooltip: { trigger: 'axis', valueFormatter: (v: any) => (v == null ? '—' : (v * 100).toFixed(1) + '%') }, grid: { left: 8, right: 12, top: 16, bottom: 8 },
  xAxis: { type: 'category', data: (b.value?.otd_trend || []).map((x: any) => x.month) },
  yAxis: { type: 'value', min: 0, max: 1, axisLabel: { formatter: (v: number) => Math.round(v * 100) + '%' } },
  series: [{ type: 'line', name: $t('erpSalesBoard.onTimeDelivery'), data: (b.value?.otd_trend || []).map((x: any) => x.otd), smooth: true, itemStyle: { color: CH.ok }, areaStyle: { opacity: 0.08 } }],
}));
onMounted(async () => { b.value = await get(`/api/sales/board?days=${days}`); });
</script>

<style scoped>
.mt { margin-top: var(--hm-gap); }
.erp-board :deep(.el-table__row) { cursor: pointer; }
</style>
