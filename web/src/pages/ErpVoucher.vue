<template>
  <div class="page erp-voucher">
    <HmPageHead :title="$t('common.voucherExport')" :sub="$t('erpVoucher.generatesAccountingVouchersFromBusin')">
      <el-button @click="router.push('/e/acct_map')">{{ $t('nav.accountMapping') }}</el-button>
    </HmPageHead>
    <HmToolbar data-tour="v-bar">
      <el-date-picker v-model="period" type="month" value-format="YYYY-MM" class="mp" @change="load" />
      <el-button type="primary" v-perm:disable="'finance.voucher.generate'" data-test="v-gen" :loading="busy" @click="gen">{{ $t('erpVoucher.generateVouchersForThisPeriod') }}</el-button>
      <template #right>
        <el-select v-model="fmt" class="fmt" data-test="v-fmt"><el-option v-for="f in formats" :key="f.key" :label="f.label" :value="f.key" /></el-select>
        <el-button v-perm:disable="'finance.voucher.export'" data-tour="v-export" data-test="v-export" :disabled="!list.length" @click="exportFile"><el-icon><Download /></el-icon>{{ $t('common.export') }}</el-button>
      </template>
    </HmToolbar>
    <div class="grid g4 mt" data-tour="kpis">
      <HmKpi :label="$t('erpArAp.vouchers')" :value="list.length" format="int" :unit="$t('common.k2835')" />
      <HmKpi :label="$t('erpVoucher.totalDebit')" :value="sum('debit')" format="compact" :unit="$t('common.cny')" />
      <HmKpi :label="$t('erpVoucher.totalCredit')" :value="sum('credit')" format="compact" :unit="$t('common.cny')" :status="Math.abs(sum('debit') - sum('credit')) < 0.01 ? 'ok' : 'bad'" />
      <HmKpi :label="$t('erpVoucher.notExported')" :value="list.filter((v) => v.status === 'draft').length" format="int" :unit="$t('common.k2835')" />
    </div>
    <div class="grid g2 mt">
      <HmCard :title="$t('erpArAp.vouchers')" flush data-tour="v-table">
        <el-table :data="list" size="small" max-height="520" highlight-current-row data-test="v-table" @row-click="open">
          <template #empty><HmEmpty compact icon="data" :title="$t('erpVoucher.noVouchersForThisPeriod')" :desc="$t('erpVoucher.clickGenerateVouchersForThis')" /></template>
          <el-table-column prop="code" :label="$t('erpVoucher.voucherNo')" min-width="130" />
          <el-table-column prop="vdate" :label="$t('common.date')" min-width="100" />
          <el-table-column :label="$t('erpVoucher.transaction')" min-width="90"><template #default="{ row }">{{ SRC[row.source_type] || row.source_type }}</template></el-table-column>
          <el-table-column prop="source_code" :label="$t('erpVoucher.sourceDocument')" min-width="140" />
          <el-table-column :label="$t('common.amount')" align="right" min-width="110"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.debit, 2) }}</span></template></el-table-column>
          <el-table-column :label="$t('common.status')" min-width="80"><template #default="{ row }"><HmTag :tone="row.status === 'exported' ? 'ok' : row.status === 'void' ? 'bad' : 'neutral'">{{ VST[row.status] }}</HmTag></template></el-table-column>
        </el-table>
      </HmCard>
      <HmCard :title="cur ? `${cur.item.code} ${cur.item.summary}` : $t('erpVoucher.entries')" flush data-tour="v-lines">
        <el-table :data="cur?.lines || []" size="small" max-height="520">
          <template #empty><HmEmpty compact plain :title="$t('erpVoucher.clickAVoucherOnThe')" /></template>
          <el-table-column prop="summary" :label="$t('common.summary')" min-width="160" />
          <el-table-column :label="$t('erpVoucher.glAccount')" min-width="170"><template #default="{ row }"><span class="hm-mono">{{ row.account_code }}</span> {{ row.account_name }}</template></el-table-column>
          <el-table-column :label="$t('erpVoucher.auxiliaryAccounting')" min-width="120"><template #default="{ row }">{{ row.aux_code ? $t('common.n012', [row.aux_type, row.aux_name || row.aux_code]) : '' }}</template></el-table-column>
          <el-table-column :label="$t('erpVoucher.debit')" align="right" min-width="100"><template #default="{ row }"><span class="hm-num">{{ row.debit ? fmtNum(row.debit, 2) : '' }}</span></template></el-table-column>
          <el-table-column :label="$t('erpVoucher.credit')" align="right" min-width="100"><template #default="{ row }"><span class="hm-num">{{ row.credit ? fmtNum(row.credit, 2) : '' }}</span></template></el-table-column>
        </el-table>
      </HmCard>
    </div>
    <HmCard :title="$t('erpVoucher.exportPreview0', [formats.find((f) => f.key === fmt)?.label || ''])" :sub="$t('erpVoucher.columnDefinitionsCanBeChanged')" flush class="mt" data-tour="v-preview">
      <div class="pv">
        <table class="hm-table">
          <thead><tr v-for="(h, i) in pv.head" :key="i"><th v-for="(c, j) in h" :key="j">{{ c }}</th></tr></thead>
          <tbody><tr v-for="(r, i) in pv.rows.slice(0, 30)" :key="i"><td v-for="(c, j) in r" :key="j" class="hm-num">{{ c }}</td></tr></tbody>
        </table>
      </div>
    </HmCard>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { get, post, tok } from '../api';
import { fmtNum } from '../design/format';

const router = useRouter();
const SRC: Record<string, string> = { delivery: $t('erpVoucher.salesShipment'), sales_return: $t('common.salesReturn'), ar_receipt: $t('erpArAp.receipt'), receipt: $t('erpVoucher.purchaseReceipt'), supplier_return: $t('common.returnToSupplier'), ap_payment: $t('erpArAp.payment'),
  issue: $t('erpVoucher.productionMaterialIssue'), wo_cost: $t('erpVoucher.productionCost'), completion: $t('erpVoucher.productionReceipt'), adjust: $t('erpVoucher.stocktakeAdjustment') };
const VST: Record<string, string> = { draft: $t('erpVoucher.notExported'), exported: $t('erpVoucher.exported'), void: $t('common.void') };
const period = ref(new Date().toISOString().slice(0, 7)), list = ref<any[]>([]), cur = ref<any>(null), busy = ref(false);
const formats = ref<any[]>([]), fmt = ref('kingdee'), pv = ref<any>({ head: [], rows: [] });
const sum = (k: string) => list.value.reduce((a, v) => a + (v.status !== 'void' ? v[k] || 0 : 0), 0);
async function load() {
  list.value = (await get(`/api/e/voucher?size=500&f_period=${period.value}&sort=vdate`)).items; cur.value = null;
  loadPv();
}
async function loadPv() { pv.value = await get(`/api/finance/vouchers/preview?fmt=${fmt.value}&period=${period.value}`); }
async function open(r: any) { cur.value = await get(`/api/finance/vouchers/${r.id}`); }
async function gen() {
  busy.value = true;
  try { const r = await post('/api/finance/vouchers/generate', { period: period.value }); ElMessage.success($t('erpVoucher.generated0Vouchers', [r.count])); load(); } finally { busy.value = false; }
}
function exportFile() {
  const a = document.createElement('a');
  a.href = `/api/finance/vouchers/export?fmt=${fmt.value}&period=${period.value}&token=${tok()}`;
  a.download = '';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(load, 1200);
}
watch(fmt, loadPv);
onMounted(async () => { formats.value = await get('/api/finance/vouchers/formats'); load(); });
</script>

<style scoped>
.mt { margin-top: var(--hm-gap); }
.mp { width: calc(150px * var(--hm-scale)); }
.fmt { width: calc(260px * var(--hm-scale)); }
.erp-voucher :deep(.el-table__row) { cursor: pointer; }
.pv { overflow: auto; max-height: 360px; }
.pv table { border-collapse: collapse; font-size: var(--hm-fs-xs); }
.pv th, .pv td { padding: 4px 8px; border-bottom: 1px solid var(--hm-hairline); white-space: nowrap; text-align: left; }
.pv th { background: var(--hm-surface-2); color: var(--hm-text-2); position: sticky; top: 0; }
</style>
