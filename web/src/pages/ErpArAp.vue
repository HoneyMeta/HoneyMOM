<template>
  <div class="page erp-arap">
    <HmPageHead :title="$t('common.arAp')" :sub="$t('erpArAp.arShipmentsReturnsReceiptsMatches')">
      <el-button v-perm="'finance.ar_invoice.read'" @click="router.push('/finance/ar_invoices')">{{ $t('common.salesInvoice') }}</el-button>
      <el-button v-perm="'finance.ap_invoice.read'" @click="router.push('/finance/ap_invoices')">{{ $t('common.purchaseInvoice') }}</el-button>
      <el-button v-perm="'finance.voucher.read'" @click="router.push('/finance/vouchers')">{{ $t('erpArAp.vouchers') }}</el-button>
    </HmPageHead>
    <div class="grid g4" data-tour="kpis">
      <HmKpi :label="$t('erpArAp.arBalance')" :value="sm?.ar_total" format="compact" :unit="$t('common.cny')" :loading="!sm" />
      <HmKpi :label="$t('erpArAp.arOverdue')" :value="sm?.ar_overdue" format="compact" :unit="$t('common.cny')" :loading="!sm" :status="sm?.ar_overdue > 0 ? 'bad' : 'ok'" />
      <HmKpi :label="$t('erpArAp.apBalance')" :value="sm?.ap_total" format="compact" :unit="$t('common.cny')" :loading="!sm" />
      <HmKpi :label="$t('erpArAp.receiptsPaymentsThisMonth')" :value="sm?.received_month" format="compact" :unit="$t('common.cny')" :loading="!sm" :hint="sm ? $t('erpArAp.payments0', [fmtCompact(sm.paid_month)]) : ''" />
    </div>
    <el-tabs v-model="tab" class="tabs mt" data-tour="arap-tabs">
      <el-tab-pane :label="$t('erpArAp.arAging')" name="ar" /><el-tab-pane :label="$t('erpArAp.apAging')" name="ap" /><el-tab-pane :label="$t('erpArAp.receiptsPayments')" name="pay" /><el-tab-pane :label="$t('erpArAp.statements')" name="st" />
    </el-tabs>

    <template v-if="tab === 'ar' || tab === 'ap'">
      <HmCard flush data-tour="ageing">
        <el-table :data="ag?.rows || []" size="small" max-height="520" row-key="partner" data-test="ageing-table">
          <template #empty><HmEmpty compact icon="data" :title="tab === 'ar' ? $t('erpArAp.noReceivables') : $t('erpArAp.noPayables')" /></template>
          <el-table-column type="expand"><template #default="{ row }">
            <el-table :data="row.detail" size="small" class="inner">
              <el-table-column prop="kind" :label="$t('common.type')" min-width="100" /><el-table-column prop="doc" :label="$t('erpArAp.document')" min-width="140" />
              <el-table-column prop="invoice_no" :label="$t('erpArAp.invoiceNo')" min-width="120" /><el-table-column prop="due" :label="$t('erpArAp.dueDate')" min-width="110" />
              <el-table-column :label="$t('erpArAp.open')" align="right" min-width="110"><template #default="{ row: d }"><span class="hm-num">{{ fmtNum(d.left, 2) }}</span></template></el-table-column>
              <el-table-column :label="$t('erpArAp.age')" min-width="100"><template #default="{ row: d }">{{ bucketLabel(d.bucket) }}</template></el-table-column>
            </el-table></template></el-table-column>
          <el-table-column :label="tab === 'ar' ? $t('common.customer') : $t('common.suppliers')" min-width="190"><template #default="{ row }">{{ row.code }} {{ row.name }}</template></el-table-column>
          <el-table-column v-for="b in ag?.buckets || []" :key="b.key" :label="b.label" align="right" min-width="100">
            <template #default="{ row }"><span class="hm-num" :class="{ bad: b.key !== 'current' && row[b.key] > 0 }">{{ fmtNum(row[b.key], 2) }}</span></template></el-table-column>
          <el-table-column :label="tab === 'ar' ? $t('erpArAp.advanceReceiptsUnapplied') : $t('erpArAp.prepaymentsUnapplied')" align="right" min-width="120"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.unapplied, 2) }}</span></template></el-table-column>
          <el-table-column :label="$t('msaResult.total')" align="right" min-width="120"><template #default="{ row }"><b class="hm-num">{{ fmtNum(row.total, 2) }}</b></template></el-table-column>
          <el-table-column v-if="tab === 'ar'" :label="$t('erpArAp.creditLimit')" align="right" min-width="110"><template #default="{ row }"><span class="hm-num">{{ row.limit ? fmtNum(row.limit, 0) : '—' }}</span></template></el-table-column>
        </el-table>
      </HmCard>
    </template>

    <template v-else-if="tab === 'pay'">
      <div class="grid g2">
        <HmCard v-for="side in ['ar', 'ap']" :key="side" :title="side === 'ar' ? $t('erpArAp.recordReceipt') : $t('erpArAp.recordPayment')" :sub="$t('erpArAp.withoutASpecifiedInvoiceApplied')" :data-tour="'pay-' + side">
          <el-form label-width="84px" class="payf">
            <el-form-item :label="side === 'ar' ? $t('common.customer') : $t('common.suppliers')"><RefSelect v-model="pf[side].partner" :entity="side === 'ar' ? 'customer' : 'supplier'" /></el-form-item>
            <el-form-item :label="$t('common.amount')"><el-input-number v-model="pf[side].amount" :controls="false" :min="0" style="width: 100%" /></el-form-item>
            <el-form-item :label="$t('common.date')"><el-date-picker v-model="pf[side].date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
            <el-form-item :label="$t('common.method')"><el-select v-model="pf[side].method" style="width: 100%"><el-option v-for="(l, k) in METHOD" :key="k" :label="l" :value="k" /></el-select></el-form-item>
            <el-form-item :label="$t('erpArAp.transactionNo')"><el-input v-model="pf[side].bank_ref" /></el-form-item>
            <el-button type="primary" :disabled="!pf[side].partner || !pf[side].amount" v-perm:disable="side === 'ar' ? 'finance.ar_receipt.create' : 'finance.ap_payment.create'"
              :data-test="'pay-' + side" @click="pay(side)">{{ $t('erpArAp.recordApply') }}</el-button>
          </el-form>
        </HmCard>
      </div>
      <div class="grid g2 mt">
        <HmCard v-for="side in ['ar', 'ap']" :key="'l' + side" :title="side === 'ar' ? $t('erpArAp.receipts') : $t('erpArAp.payments')" flush>
          <el-table :data="pays[side]" size="small" max-height="320">
            <template #empty><HmEmpty compact plain :title="$t('erpArAp.noRecordsYet')" /></template>
            <el-table-column prop="code" :label="$t('common.docNo')" min-width="140" />
            <el-table-column :prop="side === 'ar' ? 'customer__label' : 'supplier__label'" :label="side === 'ar' ? $t('common.customer') : $t('common.suppliers')" min-width="150" />
            <el-table-column :prop="side === 'ar' ? 'receipt_date' : 'pay_date'" :label="$t('common.date')" min-width="100" />
            <el-table-column :label="$t('common.amount')" align="right" min-width="110"><template #default="{ row }"><b class="hm-num">{{ fmtNum(row.amount, 2) }}</b></template></el-table-column>
            <el-table-column :label="$t('erpArAp.applied')" align="right" min-width="110"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.applied, 2) }}</span></template></el-table-column>
          </el-table>
        </HmCard>
      </div>
    </template>

    <template v-else>
      <HmToolbar data-tour="st-bar">
        <el-segmented v-model="stSide" :options="[{ label: $t('erpArAp.customerStatements'), value: 'ar' }, { label: $t('erpArAp.supplierStatements'), value: 'ap' }]" @change="loadSt" />
        <el-date-picker v-model="stPeriod" type="month" value-format="YYYY-MM" class="mp" />
        <el-button type="primary" v-perm:disable="stSide === 'ar' ? 'sales.customer_statement.send' : 'purchase.supplier_statement.send'" data-test="st-gen" @click="genSt">{{ $t('erpArAp.generateStatementsForThisPeriod') }}</el-button>
      </HmToolbar>
      <HmCard flush class="mt">
        <el-table :data="sts" size="small" max-height="520" @row-click="(r: any) => (stDetail = r)">
          <template #empty><HmEmpty compact plain :title="$t('erpArAp.noStatementsForThisPeriod')" /></template>
          <el-table-column prop="code" :label="$t('erpArAp.statementNo')" min-width="140" />
          <el-table-column :label="stSide === 'ar' ? $t('common.customer') : $t('common.suppliers')" min-width="160"><template #default="{ row }">{{ row.customer__label || row.supplier__label }}</template></el-table-column>
          <el-table-column prop="period" :label="$t('erpArAp.period')" min-width="90" />
          <el-table-column v-for="c in stCols" :key="c[0]" :label="c[1]" align="right" min-width="110"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row[c[0]], 2) }}</span></template></el-table-column>
          <el-table-column :label="$t('common.status')" min-width="100"><template #default="{ row }"><HmTag :tone="statusTone(row.status)">{{ STS[row.status] }}</HmTag></template></el-table-column>
          <el-table-column label="" min-width="170"><template #default="{ row }">
            <el-button v-if="row.status === 'draft'" link size="small" type="primary" @click.stop="stAct(row, 'send')">{{ $t('erpArAp.send') }}</el-button>
            <el-button v-if="['draft', 'sent'].includes(row.status)" link size="small" type="primary" @click.stop="stAct(row, 'confirm')">{{ $t('common.confirmed') }}</el-button>
            <el-button v-if="['draft', 'sent'].includes(row.status)" link size="small" @click.stop="stAct(row, 'dispute')">{{ $t('erpArAp.disputed') }}</el-button>
          </template></el-table-column>
        </el-table>
      </HmCard>
      <HmCard v-if="stDetail" :title="$t('erpArAp.n0Details', [stDetail.code])" flush class="mt">
        <el-table :data="stDetail.lines || []" size="small">
          <el-table-column prop="date" :label="$t('common.date')" min-width="100" /><el-table-column prop="kind" :label="$t('common.type')" min-width="80" /><el-table-column prop="doc" :label="$t('erpArAp.document')" min-width="140" />
          <el-table-column :label="$t('erpArAp.debitIncrease')" align="right" min-width="110"><template #default="{ row }"><span class="hm-num">{{ fmtNum(stSide === 'ar' ? row.debit : row.credit, 2) }}</span></template></el-table-column>
          <el-table-column :label="$t('erpArAp.creditDecrease')" align="right" min-width="110"><template #default="{ row }"><span class="hm-num">{{ fmtNum(stSide === 'ar' ? row.credit : row.debit, 2) }}</span></template></el-table-column>
          <el-table-column :label="$t('erpArAp.balance')" align="right" min-width="110"><template #default="{ row }"><b class="hm-num">{{ fmtNum(row.balance, 2) }}</b></template></el-table-column>
        </el-table>
      </HmCard>
    </template>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { get, post } from '../api';
import RefSelect from '../components/RefSelect.vue';
import { fmtCompact, fmtNum } from '../design/format';
import { statusTone } from '../erp/docs';

const router = useRouter();
const METHOD: Record<string, string> = { bank: $t('erpArAp.bankTransfer'), acceptance: $t('erpArAp.bankAcceptanceBill'), cash: $t('erpArAp.cash'), other: $t('common.other') };
const STS: Record<string, string> = { draft: $t('erpArAp.toSend'), sent: $t('common.sentOut'), confirmed: $t('common.confirmed'), disputed: $t('erpArAp.disputed') };
const tab = ref('ar'), ag = ref<any>(null), sm = ref<any>(null), pays = reactive<any>({ ar: [], ap: [] });
const today = new Date().toISOString().slice(0, 10);
const pf = reactive<any>({ ar: { partner: null, amount: 0, date: today, method: 'bank', bank_ref: '' }, ap: { partner: null, amount: 0, date: today, method: 'bank', bank_ref: '' } });
const stSide = ref('ar'), stPeriod = ref(today.slice(0, 7)), sts = ref<any[]>([]), stDetail = ref<any>(null);
const stCols = computed(() => (stSide.value === 'ar' ? [['opening', $t('erpArAp.opening')], ['shipped', $t('erpArAp.shipment')], ['returned', $t('common.return2')], ['received', $t('erpArAp.receipt')], ['closing', $t('erpArAp.closing')]]
  : [['opening', $t('erpArAp.opening')], ['received', $t('common.goodsReceipt')], ['returned', $t('common.return2')], ['paid', $t('erpArAp.payment')], ['closing', $t('erpArAp.closing')]]));
const bucketLabel = (k: string) => ag.value?.buckets?.find((b: any) => b.key === k)?.label || k;

async function loadAg() { ag.value = await get(`/api/finance/ageing?side=${tab.value}`); }
async function loadPays() {
  pays.ar = (await get('/api/e/ar_receipt?size=50')).items; pays.ap = (await get('/api/e/ap_payment?size=50')).items;
}
async function pay(side: string) {
  const f = pf[side];
  const body: any = { amount: f.amount, date: f.date, method: f.method, bank_ref: f.bank_ref, [side === 'ar' ? 'customer' : 'supplier']: f.partner };
  const r = await post(side === 'ar' ? '/api/finance/ar_receipts' : '/api/finance/ap_payments', body);
  ElMessage.success($t('erpArAp.recorded0Applied1', [r.code, fmtNum(r.item.applied, 2)])); f.amount = 0; loadPays(); sm.value = await get('/api/finance/summary');
}
async function loadSt() {
  stDetail.value = null;
  const ent = stSide.value === 'ar' ? 'customer_statement' : 'supplier_statement';
  sts.value = (await get(`/api/e/${ent}?size=200&f_period=${stPeriod.value}`)).items;
}
async function genSt() {
  const r = await post(stSide.value === 'ar' ? '/api/sales/statements' : '/api/purchase/statements', { period: stPeriod.value });
  ElMessage.success($t('erpArAp.generated0Statements', [r.count])); loadSt();
}
async function stAct(row: any, act: string) {
  const ent = stSide.value === 'ar' ? 'customer_statement' : 'supplier_statement';
  const r = await post(`/api/e/${ent}/${row.id}/do/${act}`, {}); ElMessage.success(r.msg || $t('erpArAp.updated')); loadSt();
}
watch(tab, (t) => (t === 'ar' || t === 'ap' ? loadAg() : t === 'pay' ? loadPays() : loadSt()));
watch(stPeriod, loadSt);
onMounted(async () => { loadAg(); sm.value = await get('/api/finance/summary'); });
</script>

<style scoped>
.mt { margin-top: var(--hm-gap); }
.bad { color: var(--hm-fault-ink); }
.mp { width: calc(150px * var(--hm-scale)); }
.payf { max-width: 520px; }
.inner { margin: 0 var(--hm-sp-4); width: auto; }
</style>
