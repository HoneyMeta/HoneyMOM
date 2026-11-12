<template>
  <div class="page os">
    <HmPageHead :title="$t('common.outsourcing')" :sub="$t('outsource.subcontractedOperationsSuchAsHeat')">
      <el-button v-perm:disable="'outsource.outsource_order.create'" type="primary" data-test="os-new" @click="openNew"><el-icon><Plus /></el-icon>{{ $t('outsource.newOutsourcingOrder') }}</el-button>
    </HmPageHead>

    <div class="grid g4" data-test="os-kpis">
      <HmKpi :label="$t('outsource.qtyOut')" :value="sum('out_qty')" format="int" :unit="$t('common.pcs')" :hint="$t('outsource.sentNotReturned')" />
      <HmKpi :label="$t('outsource.openOutsourcingOrders')" :value="open.length" format="int" :unit="$t('common.receipts')" />
      <HmKpi :label="$t('outsource.overdueReturns')" :value="overdue" format="int" :unit="$t('common.receipts')" :status="overdue ? 'bad' : ''" />
      <HmKpi :label="$t('outsource.outsourcingYield')" :value="yieldAll" format="pct" :status="yieldAll != null && yieldAll < 0.98 ? 'warn' : ''" :hint="$t('outsource.returnedGoodGoodScrapLost')" />
    </div>

    <HmCard :title="$t('common.suppliers')" class="mt" flush>
      <el-table :data="data.suppliers" size="small" data-test="os-suppliers"
        v-ai-rows="{ entity: 'supplier', rows: () => data.suppliers, label: (r: any) => $t('outsource.outsourcingSuppliers0', [r.supplier]), data: (r: any) => ({ [$t('common.suppliers')]: r.supplier, [$t('outsource.orders')]: r.orders, [$t('outsource.out')]: r.out_qty, [$t('common.good')]: r.ok, [$t('common.scrapped')]: r.scrap, [$t('outsource.lost')]: r.lost, [$t('outsource.yield')]: r.yield, [$t('common.amount')]: r.amount, [$t('common.late')]: r.overdue }) }">
        <el-table-column prop="supplier" :label="$t('common.suppliers')" min-width="140" />
        <el-table-column :label="$t('common.outsourcingOrders')" width="80" align="right"><template #default="{ row }"><span class="hm-num">{{ row.orders }}</span></template></el-table-column>
        <el-table-column :label="$t('outsource.out')" width="80" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.out_qty, 0) }}</span></template></el-table-column>
        <el-table-column :label="$t('outsource.goodScrapLost')" width="150" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.ok, 0) }} / {{ fmtNum(row.scrap, 0) }} / {{ fmtNum(row.lost, 0) }}</span></template></el-table-column>
        <el-table-column :label="$t('outsource.yield')" width="90" align="right"><template #default="{ row }"><span class="hm-num" :class="row.yield != null && row.yield < 0.98 ? 'warn' : ''">{{ row.yield != null ? fmtNum(row.yield * 100, 1) + '%' : '—' }}</span></template></el-table-column>
        <el-table-column :label="$t('outsource.settledAmount')" width="110" align="right"><template #default="{ row }"><span class="hm-num">{{ $t('outsource.cny0', [fmtNum(row.amount, 0)]) }}</span></template></el-table-column>
        <el-table-column :label="$t('common.late')" width="70" align="right"><template #default="{ row }"><span class="hm-num" :class="row.overdue ? 'bad' : ''">{{ row.overdue }}</span></template></el-table-column>
      </el-table>
      <HmEmpty v-if="!data.suppliers.length && loaded" plain :title="$t('outsource.noOutsourcingActivityYet')" />
    </HmCard>

    <HmToolbar class="mt">
      <el-radio-group v-model="fs" size="small" data-test="os-status">
        <el-radio-button value="">{{ $t('common.all0', [data.orders.length]) }}</el-radio-button>
        <el-radio-button v-for="(t, k) in ST" v-show="cnt(k)" :key="k" :value="k">{{ t }} {{ cnt(k) }}</el-radio-button>
      </el-radio-group>
      <el-input v-model="q" clearable size="small" :placeholder="$t('outsource.noSupplierMaterialWorkOrder')" style="width: 220px" />
    </HmToolbar>

    <HmCard flush class="mt">
      <el-table :data="rows" size="small" class="click" data-test="os-table" @row-click="(r: any) => openOrder(r.id)"
        v-ai-rows="{ entity: 'outsource_order', rows: () => rows, label: (r: any) => $t('outsource.outsourcingOrder012', [r.code, r.supplier, ST[r.status] || r.status]), data: (r: any) => ({ [$t('common.docNo')]: r.code, [$t('common.suppliers')]: r.supplier, [$t('common.material')]: r.material__label, [$t('common.operation')]: r.process__label, [$t('common.quantity')]: r.qty, [$t('common.sentOut')]: r.qty_sent, [$t('outsource.returned')]: r.qty_back, [$t('common.good')]: r.qty_ok, [$t('common.scrapped')]: r.qty_scrap, [$t('outsource.dueBack')]: r.due, [$t('common.late')]: r.overdue, [$t('common.status')]: ST[r.status] }) }">
        <el-table-column :label="$t('common.outsourcingOrders')" width="130"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
        <el-table-column prop="supplier" :label="$t('common.suppliers')" min-width="120" />
        <el-table-column prop="material__label" :label="$t('common.material')" min-width="130" />
        <el-table-column prop="process__label" :label="$t('common.operation')" width="100" />
        <el-table-column prop="work_order__label" :label="$t('common.workOrder')" width="120" />
        <el-table-column :label="$t('outsource.qtySentReturned')" width="130" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty, 0) }} · {{ fmtNum(row.qty_sent, 0) }}/{{ fmtNum(row.qty_back, 0) }}</span></template></el-table-column>
        <el-table-column :label="$t('outsource.dueBack')" width="150"><template #default="{ row }"><span class="hm-num" :class="row.overdue ? 'bad' : ''">{{ fmtTime(row.due) || '—' }}</span><span v-if="row.overdue" class="bad small">{{ $t('outsource.overdue') }}</span></template></el-table-column>
        <el-table-column :label="$t('common.status')" width="100"><template #default="{ row }"><HmTag :tone="TONE[row.status] || 'neutral'">{{ ST[row.status] || row.status }}</HmTag></template></el-table-column>
      </el-table>
      <HmEmpty v-if="!rows.length && loaded" plain :title="data.orders.length ? $t('outsource.noMatchingOutsourcingOrders') : $t('outsource.noOutsourcingOrdersYet')" />
    </HmCard>

    <!-- Outsourcing order detail -->
    <el-drawer v-model="dr.open" size="min(760px, 100%)" :title="dr.o ? `${dr.o.code} · ${dr.o.supplier}` : ''" append-to-body destroy-on-close>
      <div v-if="dr.o" class="drw" data-test="os-drawer">
        <div class="st-line"><HmTag :tone="TONE[dr.o.status] || 'neutral'">{{ ST[dr.o.status] || dr.o.status }}</HmTag>
          <span v-if="dr.o.ap_posted" class="small t2">{{ $t('outsource.postedToAp0', [dr.o.ap_ref || '']) }}</span></div>
        <el-descriptions :column="3" size="small" border>
          <el-descriptions-item :label="$t('common.material')">{{ dr.o.material__label || '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('common.operation')">{{ dr.o.process__label || '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('common.workOrder')">{{ dr.o.work_order__label || '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('outsource.outsourcedQty')"><span class="hm-num">{{ fmtNum(dr.o.qty, 0) }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('outsource.sentReturned')"><span class="hm-num">{{ fmtNum(dr.o.qty_sent, 0) }} / {{ fmtNum(dr.o.qty_back, 0) }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('outsource.goodScrapLost')"><span class="hm-num">{{ fmtNum(dr.o.qty_ok, 0) }} / {{ fmtNum(dr.o.qty_scrap, 0) }} / {{ fmtNum(dr.o.qty_lost, 0) }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('outsource.unitProcessingPrice')"><span class="hm-num">{{ dr.o.unit_price ?? '—' }}</span> {{ $t('common.cny') }}</el-descriptions-item>
          <el-descriptions-item :label="$t('outsource.allowedLoss')"><span class="hm-num">{{ dr.o.loss_pct ?? 0 }}</span>%</el-descriptions-item>
          <el-descriptions-item :label="$t('outsource.settledAmount')"><span class="hm-num">{{ dr.o.cost != null ? fmtNum(dr.o.cost, 2) + $t('outsource.cny') : '—' }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('outsource.processingTime')"><span class="hm-num">{{ dr.o.lead_days ?? '—' }}</span> {{ $t('common.days2') }}</el-descriptions-item>
          <el-descriptions-item :label="$t('outsource.dueBack')"><span class="hm-num">{{ fmtTime(dr.o.due) || '—' }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('outsource.sendReturn')"><span class="hm-num">{{ fmtTime(dr.o.sent_at) || '—' }} / {{ fmtTime(dr.o.received_at) || '—' }}</span></el-descriptions-item>
        </el-descriptions>

        <div class="acts">
          <el-button v-perm:disable="'outsource.outsource_order.send'" type="primary" :disabled="!canSend" data-test="os-send" @click="send">{{ $t('erpArAp.send') }}</el-button>
          <el-button v-perm:disable="'outsource.outsource_order.receive'" type="success" :disabled="!canRecv" data-test="os-recv" @click="openRecv">{{ $t('outsource.receiveInspect') }}</el-button>
          <el-button v-perm:disable="'outsource.outsource_order.close'" :disabled="dr.o.status !== 'received'" :loading="busy === 'close'" data-test="os-close" @click="act('close')">{{ $t('outsource.settleClose') }}</el-button>
          <el-button v-perm:disable="'outsource.outsource_order.cancel'" type="danger" plain :disabled="!['draft', 'sent'].includes(dr.o.status) || (dr.o.qty_back || 0) > 0" @click="cancel">{{ $t('common.cancel') }}</el-button>
        </div>

        <h4 class="sec">{{ $t('outsource.sendReturnRecords') }}</h4>
        <el-table :data="dr.lines" size="small" data-test="os-lines">
          <el-table-column :label="$t('outsource.direction')" width="70"><template #default="{ row }"><HmTag :tone="row.direction === 'out' ? 'primary' : 'ok'">{{ row.direction === 'out' ? $t('erpArAp.send') : $t('outsource.return') }}</HmTag></template></el-table-column>
          <el-table-column :label="$t('outsource.partCodeLot')" min-width="140"><template #default="{ row }"><span class="hm-mono">{{ row.sn || row.lot || '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('common.quantity')" width="70" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty, 0) }}</span></template></el-table-column>
          <el-table-column :label="$t('outsource.goodScrapLost2')" width="120" align="right"><template #default="{ row }"><span v-if="row.direction === 'back'" class="hm-num">{{ fmtNum(row.qty_ok, 0) }}/{{ fmtNum(row.qty_scrap, 0) }}/{{ fmtNum(row.qty_lost, 0) }}</span></template></el-table-column>
          <el-table-column :label="$t('outsource.inspection')" width="80"><template #default="{ row }"><HmTag v-if="row.result" :tone="row.result === 'OK' ? 'ok' : row.result === 'NG' ? 'bad' : 'warn'">{{ RES[row.result] || row.result }}</HmTag></template></el-table-column>
          <el-table-column prop="by" :label="$t('outsource.handledBy')" width="80" />
          <el-table-column :label="$t('common.time')" width="140"><template #default="{ row }"><span class="hm-num">{{ fmtTime(row.time) }}</span></template></el-table-column>
        </el-table>
        <HmEmpty v-if="!dr.lines.length" plain :title="$t('outsource.nothingSentYet')" />
      </div>
    </el-drawer>

    <!-- Return inspection -->
    <el-dialog v-model="rf.open" :title="dr.o ? $t('outsource.receiveInspect0', [dr.o.code]) : ''" width="min(520px, 96vw)" append-to-body destroy-on-close>
      <el-form label-width="96px" data-test="os-recv-form">
        <el-form-item :label="$t('common.good')"><el-input-number v-model="rf.qty_ok" :min="0" :controls="false" style="width: 140px" /></el-form-item>
        <el-form-item :label="$t('common.scrapped')"><el-input-number v-model="rf.qty_scrap" :min="0" :controls="false" style="width: 140px" /></el-form-item>
        <el-form-item :label="$t('outsource.lost')"><el-input-number v-model="rf.qty_lost" :min="0" :controls="false" style="width: 140px" /></el-form-item>
      </el-form>
      <p class="small t2">{{ $t('outsource.n0PcsOutReceivingCreates', [fmtNum(outQty, 0)]) }}</p>
      <template #footer>
        <el-button @click="rf.open = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="busy === 'receive'" :disabled="rf.qty_ok + rf.qty_scrap + rf.qty_lost <= 0" data-test="os-recv-save" @click="recv">{{ $t('outsource.confirmReceipt') }}</el-button>
      </template>
    </el-dialog>

    <!-- New -->
    <el-dialog v-model="nf.open" :title="$t('outsource.newOutsourcingOrder2')" width="min(600px, 96vw)" append-to-body destroy-on-close>
      <el-form label-width="96px" data-test="os-new-form">
        <el-form-item :label="$t('common.suppliers')" required><el-input v-model="nf.supplier" :placeholder="$t('outsource.supplierCodeOrName')" /></el-form-item>
        <el-form-item :label="$t('common.material')" required><RefSelect v-model="nf.material" entity="material" /></el-form-item>
        <el-form-item :label="$t('outsource.outsourcedOperation')"><RefSelect v-model="nf.process" entity="process" /></el-form-item>
        <el-form-item :label="$t('common.workOrder')"><RefSelect v-model="nf.work_order" entity="work_order" /></el-form-item>
        <el-form-item :label="$t('outsource.outsourcedQty')" required><el-input-number v-model="nf.qty" :min="1" :controls="false" style="width: 140px" /></el-form-item>
        <el-form-item :label="$t('outsource.unitProcessingPrice')"><el-input-number v-model="nf.unit_price" :min="0" :precision="2" :controls="false" style="width: 140px" />{{ $t('outsource.cny') }}</el-form-item>
        <el-form-item :label="$t('outsource.allowedLoss')"><el-input-number v-model="nf.loss_pct" :min="0" :max="100" :precision="1" :controls="false" style="width: 140px" />&nbsp;%</el-form-item>
        <el-form-item :label="$t('outsource.processingTime')"><el-input-number v-model="nf.lead_days" :min="0" :controls="false" style="width: 140px" />{{ $t('common.days') }}</el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="nf.open = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="busy === 'new'" :disabled="!nf.supplier || !nf.material || !nf.qty" data-test="os-new-save" @click="saveNew">{{ $t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { get, post } from '../api';
import RefSelect from '../components/RefSelect.vue';
import { fmtNum, fmtTime } from '../design';

const router = useRouter(), route = useRoute();
const ST: Record<string, string> = { draft: $t('common.draft'), sent: $t('common.sentOut'), partial: $t('ecn.partiallyReceived'), received: $t('outsource.returned'), closed: $t('outsource.settled') };
const TONE: Record<string, any> = { draft: 'neutral', sent: 'primary', partial: 'warn', received: 'ok', closed: 'neutral' };
const RES: Record<string, string> = { OK: $t('common.good'), NG: $t('common.ng'), partial: $t('outsource.partiallyGood') };

const data = ref<any>({ orders: [], suppliers: [] });
const loaded = ref(false);
const fs = ref(''), q = ref(''), busy = ref('');
const cnt = (k: string) => data.value.orders.filter((o: any) => o.status === k).length;
const open = computed(() => data.value.orders.filter((o: any) => ['draft', 'sent', 'partial'].includes(o.status)));
const overdue = computed(() => data.value.orders.filter((o: any) => o.overdue).length);
const sum = (k: string) => data.value.orders.reduce((a: number, o: any) => a + (Number(o[k]) || 0), 0);
const yieldAll = computed(() => {
  const ok = sum('qty_ok'), tot = ok + sum('qty_scrap') + sum('qty_lost');
  return tot ? ok / tot : null;
});
const rows = computed(() => {
  const s = q.value.trim().toLowerCase();
  return data.value.orders.filter((o: any) => (!fs.value || o.status === fs.value)
    && (!s || [o.code, o.supplier, o.material__label, o.work_order__label].some((x) => String(x || '').toLowerCase().includes(s))));
});

async function load() { data.value = await get('/api/outsource/summary'); loaded.value = true; }

// ---------- Detail ----------
const dr = reactive<any>({ open: false, o: null, lines: [] });
const outQty = computed(() => (dr.o?.qty_sent || 0) - (dr.o?.qty_back || 0));
const canSend = computed(() => !!dr.o && ['draft', 'sent', 'partial'].includes(dr.o.status) && (dr.o.qty_sent || 0) < dr.o.qty);
const canRecv = computed(() => !!dr.o && ['sent', 'partial'].includes(dr.o.status) && outQty.value > 0);
async function openOrder(id: number) {
  const d = await get(`/api/e/outsource_order/${id}`);
  dr.o = d.item; dr.lines = d.children?.outsource_line || []; dr.open = true;
  if (String(route.query.id || '') !== String(id)) router.replace({ query: { ...route.query, id } });
}
async function refreshAll() { await load(); if (dr.o) await openOrder(dr.o.id); }
async function act(a: string, body: any = {}) {
  busy.value = a;
  try { const r = await post(`/api/e/outsource_order/${dr.o.id}/do/${a}`, body); ElMessage.success(r.msg || $t('common.done')); await refreshAll(); return true; } catch { return false; } finally { busy.value = ''; }
}
async function send() {
  const left = dr.o.qty - (dr.o.qty_sent || 0);
  const { value } = await ElMessageBox.prompt($t('outsource.quantityToSendUpTo', [left]), $t('outsource.send0', [dr.o.code]), { inputValue: String(left), inputPattern: /^\d+(\.\d+)?$/, inputErrorMessage: $t('outsource.pleaseEnterAQuantity') }).catch(() => ({ value: null } as any));
  if (value == null) return;
  await act('send', { qty: Number(value) });
}
const rf = reactive<any>({ open: false, qty_ok: 0, qty_scrap: 0, qty_lost: 0 });
function openRecv() { Object.assign(rf, { open: true, qty_ok: outQty.value, qty_scrap: 0, qty_lost: 0 }); }
async function recv() { if (await act('receive', { qty_ok: rf.qty_ok, qty_scrap: rf.qty_scrap, qty_lost: rf.qty_lost })) rf.open = false; }
async function cancel() {
  const ok = await ElMessageBox.confirm($t('outsource.cancelOutsourcingOrder0Quantities', [dr.o.code]), $t('outsource.cancelOutsourcingOrder'), { type: 'warning' }).catch(() => false);
  if (ok) await act('cancel');
}

// ---------- New ----------
const nf = reactive<any>({ open: false });
function openNew() { Object.assign(nf, { open: true, supplier: '', material: null, process: null, work_order: null, qty: 100, unit_price: 0, loss_pct: 0.5, lead_days: 3 }); }
async function saveNew() {
  busy.value = 'new';
  try {
    const { open: _o, ...body } = nf; void _o;
    const r = await post('/api/e/outsource_order', body);
    ElMessage.success($t('outsource.outsourcingOrderCreated')); nf.open = false; await load(); if (r.id) openOrder(r.id);
  } catch { /* already notified */ } finally { busy.value = ''; }
}

watch(() => dr.open, (o) => { if (!o && route.query.id) { const { id, ...rest } = route.query; void id; router.replace({ query: rest }); } });
onMounted(async () => { await load(); if (route.query.id) openOrder(Number(route.query.id)); });
</script>

<style scoped>
.bad { color: var(--hm-fault-ink); font-weight: 600; } .warn { color: var(--hm-idle-ink); font-weight: 600; }
.small { font-size: var(--hm-fs-2xs); } .t2 { color: var(--hm-text-2); }
.click :deep(.el-table__row) { cursor: pointer; }
.drw { display: flex; flex-direction: column; gap: var(--hm-sp-2); }
.st-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.acts { display: flex; flex-wrap: wrap; gap: 8px; margin: var(--hm-sp-2) 0; }
.acts .el-button + .el-button { margin-left: 0; }
.sec { margin: var(--hm-sp-3) 0 var(--hm-sp-1); font-size: var(--hm-fs-md); font-weight: 600; color: var(--hm-text); }
</style>
