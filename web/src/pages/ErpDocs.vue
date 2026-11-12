<template>
  <div class="page erp-docs" :data-kind="kind">
    <HmPageHead :title="cfg.title" :sub="cfg.sub">
      <el-button v-if="kind === 'sales_order'" @click="router.push('/sales/board')"><el-icon><DataLine /></el-icon>{{ $t('erpDocs.orderBoard') }}</el-button>
      <el-button v-if="kind === 'purchase_order'" @click="router.push('/e/purchase_req')"><el-icon><Tickets /></el-icon>{{ $t('erpDocs.purchaseRequisitions') }}</el-button>
      <el-button v-if="canCreate" type="primary" data-tour="erp-new" data-test="erp-new" @click="openNew"><el-icon><Plus /></el-icon>&nbsp;{{ cfg.newLabel }}</el-button>
      <template #meta><HmTag v-if="owned" tone="amber" data-test="erp-owned">{{ $t('erpDocs.externalErpIsTheMaster') }}</HmTag></template>
    </HmPageHead>

    <HmToolbar data-tour="erp-filter">
      <el-input v-model="q" :placeholder="$t('erpDocs.docNoBusinessPartner')" clearable class="kw" @change="load(1)"><template #prefix><el-icon><Search /></el-icon></template></el-input>
      <el-select v-model="status" :placeholder="$t('erpDocs.allStatuses')" clearable class="st" @change="load(1)">
        <el-option v-for="(lab, k) in statuses" :key="k" :label="lab" :value="k" />
      </el-select>
      <div class="partner"><RefSelect v-model="partner" :entity="cfg.partner" @update:model-value="load(1)" /></div>
      <template #right><span class="muted small">{{ $t('erpDocs.n0Documents', [total]) }}</span></template>
    </HmToolbar>

    <div class="hm-card tbl mt" data-tour="erp-table">
      <el-table :data="items" size="small" v-loading="loading" data-test="erp-table" @row-click="(r: any) => openDoc(r.id)"
        v-ai-rows="{ entity: kind, rows: () => items, label: (r: any) => `${cfg.title} ${r.code}`, data: (r: any) => rowData(r) }">
        <template #empty><HmEmpty compact :icon="q || status || partner ? 'search' : 'data'" :title="q || status || partner ? $t('erpDocs.noMatchingDocuments') : $t('erpDocs.noneYet') + cfg.title" /></template>
        <el-table-column :label="$t('common.docNo')" min-width="150"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
        <el-table-column :label="cfg.partner === 'customer' ? $t('common.customer') : $t('common.suppliers')" min-width="180"><template #default="{ row }">{{ row[cfg.partner + '__label'] }}</template></el-table-column>
        <el-table-column :label="$t('common.date')" min-width="110"><template #default="{ row }"><span class="hm-num">{{ row[cfg.date] || '—' }}</span></template></el-table-column>
        <el-table-column :label="$t('common.status')" min-width="110"><template #default="{ row }"><HmTag :tone="statusTone(row.status)">{{ statuses[row.status] || row.status }}</HmTag></template></el-table-column>
        <el-table-column v-for="k in cfg.list" :key="k" :label="lab(kind, k)" :align="isNum(kind, k) ? 'right' : 'left'" min-width="120">
          <template #default="{ row }"><span :class="{ 'hm-num': isNum(kind, k) }">{{ show(kind, k, row) }}</span></template>
        </el-table-column>
        <el-table-column :label="$t('erpDocs.totalInclTax')" align="right" min-width="130"><template #default="{ row }"><b class="hm-num">{{ fmtNum(row.total, 2) }}</b></template></el-table-column>
      </el-table>
      <div class="pager"><el-pagination v-model:current-page="page" :page-size="30" layout="prev, pager, next" :total="total" hide-on-single-page @current-change="load" /></div>
    </div>

    <el-drawer v-model="drawer" :size="drawerSize" :title="doc ? `${cfg.title} ${doc.code || $t('overviewTab.new')}` : cfg.title" destroy-on-close data-test="erp-drawer">
      <template v-if="doc">
        <div class="bar" data-tour="erp-actions">
          <HmTag v-if="doc.id" :tone="statusTone(doc.status)">{{ statuses[doc.status] || doc.status }}</HmTag>
          <span v-if="doc.version > 1" class="muted small">{{ $t('common.version0', [doc.version]) }}</span>
          <span v-if="doc.credit_note" class="muted small credit">{{ doc.credit_note }}</span>
          <span class="grow" />
          <template v-if="doc.id">
            <el-button v-for="a in actionsFor(doc)" :key="a.act" size="small" :type="(a.type as any) || 'default'" :data-test="'act-' + a.act"
              v-perm:disable="a.perm || entPerm(kind, a.act)" @click="runAction(a)">{{ a.label }}</el-button>
          </template>
          <el-button v-if="editable" size="small" type="primary" data-test="erp-save" @click="save">{{ $t('common.save') }}</el-button>
        </div>
        <el-form :key="'h' + (doc.id || 'new')" label-position="top" class="head" :disabled="!editable">
          <el-form-item v-for="k in cfg.head" :key="k" :label="lab(kind, k)" :class="{ wide: ['remark', 'ship_to', 'reason'].includes(k) }">
            <ErpField v-model="doc[k]" :entity="kind" :name="k" :label="doc[k + '__label']" />
          </el-form-item>
        </el-form>

        <div class="lines-head">
          <b>{{ lab(lineEnt, '') || $t('common.details') }}</b>
          <span class="grow" />
          <el-button v-if="editable && cfg.addLines" size="small" data-test="erp-add-line" @click="addLine"><el-icon><Plus /></el-icon>{{ $t('erpDocs.addLine') }}</el-button>
        </div>
        <el-table :key="'l' + (doc.id || 'new')" :data="lines" size="small" class="lines" data-test="erp-lines">
          <el-table-column label="#" width="48"><template #default="{ $index }"><span class="hm-num muted">{{ $index + 1 }}</span></template></el-table-column>
          <el-table-column v-for="c in cfg.lines" :key="c.key" :label="c.label || lab(lineEnt, c.key)" :min-width="c.w || 110" :align="c.num ? 'right' : 'left'">
            <template #default="{ row }">
              <ErpField v-if="editable && c.edit" v-model="row[c.key]" :entity="lineEnt" :name="c.key" :label="row[c.key + '__label']" />
              <span v-else-if="c.key === 'insp_record' && row.insp_record" class="link" @click.stop="router.push('/e/insp_record?id=' + row.insp_record)">{{ row.insp_record__label }}</span>
              <HmTag v-else-if="c.key === 'status'" :tone="statusTone(row.status)">{{ enumLabel(lineEnt, 'status', row.status) }}</HmTag>
              <span v-else :class="{ 'hm-num': c.num }">{{ c.num ? fmtNum(row[c.key], c.digits) : show(lineEnt, c.key, row) }}</span>
            </template>
          </el-table-column>
          <el-table-column v-if="kind === 'sales_order'" :label="$t('erpDocs.deliverySchedule')" min-width="150">
            <template #default="{ row }">
              <span class="small">{{ (scheduleOf(row) || []).map((s: any) => `${s.due.slice(5)} × ${fmtNum(s.qty)}`).join($t('common.k362')) || '—' }}</span>
              <el-button v-if="editable" link size="small" type="primary" @click="editSchedule(row)">{{ $t('erpDocs.split') }}</el-button>
            </template>
          </el-table-column>
          <el-table-column v-if="editable" label="" width="56"><template #default="{ $index }"><el-button link type="danger" size="small" @click="lines.splice($index, 1)">{{ $t('nodeConfig.del') }}</el-button></template></el-table-column>
        </el-table>
        <div class="totals"><span>{{ $t('common.amount') }} <b class="hm-num">{{ fmtNum(doc.amount, 2) }}</b></span><span>{{ $t('erpDocs.tax') }} <b class="hm-num">{{ fmtNum(doc.tax, 2) }}</b></span>
          <span>{{ $t('erpDocs.totalInclTax') }} <b class="hm-num big">{{ fmtNum(doc.total, 2) }}</b></span></div>

        <el-tabs v-if="relTabs.length" v-model="relTab" class="rel">
          <el-tab-pane v-for="t in relTabs" :key="t.key" :label="$t('common.n01', [t.label, t.rows.length])" :name="t.key">
            <el-table :data="t.rows" size="small" max-height="260">
              <el-table-column v-for="c in t.cols" :key="c[0]" :label="c[1]" :min-width="c[2] || 110">
                <template #default="{ row }"><span>{{ typeof row[c[0]] === 'object' && row[c[0]] !== null ? JSON.stringify(row[c[0]]) : (row[c[0]] ?? '—') }}</span></template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>

    <el-dialog v-model="chg.open" :title="$t('erpDocs.changeOrderTrackedVersion1')" width="min(760px, 96vw)" data-test="so-change">
      <el-table :data="chg.lines" size="small">
        <el-table-column :label="$t('common.row')" width="60"><template #default="{ row }">{{ row.seq || $t('erpDocs.new') }}</template></el-table-column>
        <el-table-column :label="$t('common.material')" min-width="180"><template #default="{ row }"><RefSelect v-if="!row.id" v-model="row.material" entity="material" /><span v-else>{{ row.material__label }}</span></template></el-table-column>
        <el-table-column :label="$t('common.quantity')" width="120"><template #default="{ row }"><el-input-number v-model="row.qty" :controls="false" style="width: 100px" /></template></el-table-column>
        <el-table-column :label="$t('common.unitPrice')" width="120"><template #default="{ row }"><el-input-number v-model="row.price" :controls="false" style="width: 100px" /></template></el-table-column>
        <el-table-column :label="$t('common.dueDate')" width="160"><template #default="{ row }"><el-date-picker v-model="row.due" type="date" value-format="YYYY-MM-DD" style="width: 140px" /></template></el-table-column>
        <el-table-column :label="$t('erpDocs.shipped')" width="70" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty_shipped) }}</span></template></el-table-column>
      </el-table>
      <el-button size="small" class="mt" @click="chg.lines.push({ qty: 1, material: null })"><el-icon><Plus /></el-icon>{{ $t('erpDocs.addLine') }}</el-button>
      <el-input v-model="chg.reason" :placeholder="$t('erpDocs.changeReasonRequiredRecordedIn')" class="mt" data-test="so-change-reason" />
      <template #footer><el-button @click="chg.open = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :disabled="!chg.reason" data-test="so-change-ok" @click="doChange">{{ $t('erpDocs.submitChange') }}</el-button></template>
    </el-dialog>

    <el-dialog v-model="sch.open" :title="$t('erpDocs.deliveryScheduleSplitDeliveries')" width="min(520px, 96vw)">
      <div v-for="(s, i) in sch.rows" :key="i" class="sch-row">
        <el-date-picker v-model="s.due" type="date" value-format="YYYY-MM-DD" style="width: 160px" />
        <el-input-number v-model="s.qty" :controls="false" style="width: 120px" />
        <el-button link type="danger" @click="sch.rows.splice(i, 1)">{{ $t('nodeConfig.del') }}</el-button>
      </div>
      <el-button size="small" @click="sch.rows.push({ due: '', qty: 0 })">{{ $t('erpDocs.addDelivery') }}</el-button>
      <div class="muted small mt">{{ $t('erpDocs.total0MustEqualOrder', [fmtNum(sch.rows.reduce((a: number, s: any) => a + (+s.qty || 0), 0)), fmtNum(sch.line?.qty)]) }}</div>
      <template #footer><el-button @click="sch.open = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" @click="okSchedule">{{ $t('common.ok') }}</el-button></template>
    </el-dialog>

    <el-dialog v-model="st.open" :title="$t('erpDocs.putAwayByIncomingInspection')" width="min(640px, 96vw)" data-test="settle-dialog">
      <p class="muted small">{{ $t('erpDocs.defaultsToTheInspectionVerdict') }}</p>
      <el-table :data="st.rows" size="small">
        <el-table-column prop="material__label" :label="$t('common.material')" min-width="160" />
        <el-table-column :label="$t('erpDocs.arrived')" width="80" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty) }}</span></template></el-table-column>
        <el-table-column :label="$t('erpDocs.inspection')" width="130"><template #default="{ row }">{{ row.insp_record__label || $t('erpDocs.exempt') }}</template></el-table-column>
        <el-table-column :label="$t('erpDocs.acceptedQty')" width="120"><template #default="{ row }"><el-input-number v-model="row.acc" :controls="false" style="width: 100px" /></template></el-table-column>
        <el-table-column :label="$t('common.ng')" width="110"><template #default="{ row }"><span class="hm-num">{{ fmtNum((row.qty || 0) - (row.acc || 0)) }}</span></template></el-table-column>
      </el-table>
      <el-checkbox v-model="st.override" class="mt">{{ $t('erpDocs.putAwayTheQuantitiesAbove') }}</el-checkbox>
      <template #footer><el-button @click="st.open = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" data-test="settle-ok" @click="doSettle">{{ $t('common.goodsReceipt') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// Built-in ERP document page (quotation, sales order, delivery, return, purchase order, receipt, return to supplier, invoice): list + drawer with header / lines / status actions / linked documents.
// Document type comes from route meta.kind; config in web/src/erp/docs.ts.
import { ElMessage, ElMessageBox } from 'element-plus';
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { get, post, put } from '../api';
import RefSelect from '../components/RefSelect.vue';
import { fmtNum } from '../design/format';
import ErpField from '../erp/ErpField.vue';
import { DOCS, enumLabel, field, lineEntity, spec, statusTone } from '../erp/docs';
import { can, entPerm } from '../perm';
import '../erp/aiActions';

const route = useRoute(), router = useRouter();
const kind = computed(() => (route.meta.kind as string) || 'sales_order');
const cfg = computed(() => DOCS[kind.value]);
const lineEnt = computed(() => lineEntity(kind.value));
const items = ref<any[]>([]), total = ref(0), page = ref(1), loading = ref(false);
const q = ref(''), status = ref(''), partner = ref<any>(null), mode = ref<any>(null);
const drawer = ref(false), doc = ref<any>(null), lines = ref<any[]>([]), related = ref<any>({}), relTab = ref('');
const drawerSize = 'min(1180px, 96vw)';
const statuses = computed(() => field(kind.value, 'status')?.enum || {});
const owned = computed(() => mode.value?.mode === 'external' && mode.value?.owner?.[kind.value] === 'erp');
const canCreate = computed(() => can(entPerm(kind.value, 'create')) && !owned.value && kind.value !== 'purchase_receipt');
const editable = computed(() => !!doc.value && (!doc.value.id || cfg.value.editable.includes(doc.value.status)) && can(entPerm(kind.value, doc.value.id ? 'update' : 'create')) && !owned.value);

const lab = (ent: string, k: string) => (k ? field(ent, k)?.label || k : spec(ent)?.label);
const isNum = (ent: string, k: string) => ['int', 'float'].includes(field(ent, k)?.t);
function show(ent: string, k: string, r: any) {
  const f = field(ent, k);
  if (!f) return r[k] ?? '—';
  if (f.t === 'ref') return r[k + '__label'] || (r[k] ? `#${r[k]}` : '—');
  if (f.t === 'enum') return f.enum?.[r[k]] ?? r[k] ?? '—';
  if (f.t === 'float' || f.t === 'int') return fmtNum(r[k], f.t === 'float' ? 2 : 0);
  if (f.t === 'bool') return r[k] ? $t('common.yes') : $t('common.no');
  return r[k] ?? '—';
}
const rowData = (r: any) => ({ [$t('common.docNo')]: r.code, [$t('erpDocs.businessPartner')]: r[cfg.value.partner + '__label'], [$t('common.status')]: statuses.value[r.status], [$t('erpDocs.totalInclTax')]: r.total, id: r.id });

async function load(p = page.value) {
  page.value = typeof p === 'number' ? p : 1;
  loading.value = true;
  try {
    const qs = new URLSearchParams({ page: String(page.value), size: '30' });
    if (q.value) qs.set('q', q.value);
    if (status.value) qs.set('status', status.value);
    if (partner.value) qs.set('partner', String(partner.value));
    const r = await get(`/api/erp/doc/${kind.value}?${qs}`);
    items.value = r.items; total.value = r.total;
  } finally { loading.value = false; }
}

async function openDoc(id: number) {
  const r = await get(`/api/erp/doc/${kind.value}/${id}`);
  doc.value = r.item; lines.value = r.lines; related.value = r.related || {};
  relTab.value = relTabs.value[0]?.key || '';
  drawer.value = true;
}
function openNew() {
  doc.value = { status: '', amount: 0, tax: 0, total: 0 };
  lines.value = []; related.value = {};
  addLine();
  drawer.value = true;
}
function addLine() { lines.value.push(kind.value.endsWith('invoice') ? {} : { qty: 1 }); }

const scheduleOf = (row: any) => row.schedule || (related.value.schedules || []).filter((s: any) => s.so_line === row.id);
const sch = reactive<any>({ open: false, line: null, rows: [] });
function editSchedule(row: any) { sch.line = row; sch.rows = (scheduleOf(row) || []).map((s: any) => ({ due: s.due, qty: s.qty })); if (!sch.rows.length) sch.rows = [{ due: row.due || '', qty: row.qty }]; sch.open = true; }
function okSchedule() {
  const sum = sch.rows.reduce((a: number, s: any) => a + (+s.qty || 0), 0);
  if (Math.abs(sum - (+sch.line.qty || 0)) > 1e-6) { ElMessage.error($t('erpDocs.deliveryScheduleTotalMustEqual')); return; }
  sch.line.schedule = sch.rows.filter((s: any) => s.due && s.qty > 0); sch.open = false;
}

async function save() {
  const body: any = {};
  for (const k of cfg.value.head) body[k] = doc.value[k];
  body.lines = lines.value.map((l: any) => {
    const o: any = { id: l.id };
    for (const c of cfg.value.lines) if (c.edit) o[c.key] = l[c.key];
    for (const k of ['so_line', 'po_line', 'delivery_line', 'receipt_line']) if (l[k]) o[k] = l[k];
    if (l.schedule) o.schedule = l.schedule;
    return o;
  });
  const r = doc.value.id ? await put(`/api/erp/doc/${kind.value}/${doc.value.id}`, body).then(() => ({ id: doc.value.id })) : await post(`/api/erp/doc/${kind.value}`, body);
  if (r.pending_approval) { ElMessage.success(r.msg || $t('erpDocs.submittedForApproval')); drawer.value = false; return; }       // Approval workflow attached (M3): the document is created only after approval
  ElMessage.success($t('common.saved')); await openDoc(r.id); load();
}

const actionsFor = (d: any) => cfg.value.actions.filter((a) => a.when(d) && !(owned.value && !['make_wo', 'close', 'delivery', 'receipt', 'settle', 'post', 'return'].includes(a.act)));
async function runAction(a: any) {
  const d = doc.value;
  if (a.custom) return custom(a.act);
  let body: any = {};
  if (a.confirm) { try { await ElMessageBox.confirm(a.confirm, a.label, { type: 'warning' }); } catch { return; } }
  if (a.prompt) {
    try { const { value } = await ElMessageBox.prompt(a.prompt, a.label, { inputValidator: (v) => !!v || $t('common.required') }); body = { reason: value }; } catch { return; }
  }
  if (a.act === 'receive') {
    try { await ElMessageBox.confirm($t('erpDocs.shouldTheReturnedQuantityBe'), $t('erpDocs.receiveReturn'), { confirmButtonText: $t('erpDocs.reship'), cancelButtonText: $t('erpDocs.donTReship'), distinguishCancelAndClose: true }); body.reship = true; }
    catch (e: any) { if (e === 'close') return; }
  }
  const r = await post(`/api/e/${kind.value}/${d.id}/do/${a.act}`, body);
  ElMessage.success(r.msg || $t('common.completed'));
  if (a.act === 'accept' && r.id) { router.push('/sales/orders?id=' + r.id); return; }
  await openDoc(d.id); load();
}

const chg = reactive<any>({ open: false, lines: [], reason: '' });
const st = reactive<any>({ open: false, rows: [], override: false });
async function custom(act: string) {
  const d = doc.value;
  if (act === 'change') { chg.lines = lines.value.filter((l) => l.status !== 'cancelled').map((l) => ({ ...l })); chg.reason = ''; chg.open = true; return; }
  if (act === 'delivery') {
    const r = await post(`/api/sales/orders/${d.id}/delivery`, {});
    ElMessage.success($t('erpDocs.deliveryOrder0CreatedDraft', [r.code])); router.push('/sales/deliveries?id=' + r.id); return;
  }
  if (act === 'receipt') {
    const r = await post(`/api/purchase/orders/${d.id}/receipt`, {});
    ElMessage.success($t('erpDocs.goodsReceipt0CreatedDraft', [r.code])); router.push('/purchase/receipts?id=' + r.id); return;
  }
  if (act === 'return') {
    const r = await post('/api/erp/doc/sales_return', { delivery_order: d.id, disposition: 'restock', lines: lines.value.map((l) => ({ delivery_line: l.id, qty: l.qty })) });
    ElMessage.success($t('erpDocs.returnCreatedDraftAdjustQuantities')); router.push('/sales/returns?id=' + r.id); return;
  }
  if (act === 'settle') { st.rows = lines.value.map((l) => ({ ...l, acc: l.qty })); st.override = false; st.open = true; }
}
async function doChange() {
  const body = { reason: chg.reason, lines: chg.lines.map((l: any) => ({ id: l.id, material: l.material, qty: l.qty, price: l.price, due: l.due })) };
  const r = await post(`/api/sales/orders/${doc.value.id}/change`, body);
  ElMessage.success(r.pending_approval ? r.msg || $t('erpDocs.submittedForApproval') : $t('erpDocs.changedVersion0', [r.version])); chg.open = false; await openDoc(doc.value.id); load();
}
async function doSettle() {
  const body: any = {};
  if (st.override) body.lines = Object.fromEntries(st.rows.map((r: any) => [r.id, { accepted: r.acc, rejected: (r.qty || 0) - (r.acc || 0) }]));
  const r = await post(`/api/e/purchase_receipt/${doc.value.id}/do/settle`, body);
  ElMessage.success(r.msg || $t('erpDocs.putAway')); st.open = false; await openDoc(doc.value.id); load();
}

const REL: Record<string, [string, string, string[][]]> = {
  schedules: ['schedules', $t('erpDocs.deliverySchedule'), [['due', $t('erpDocs.deliveryDate')], ['qty', $t('common.quantity')], ['qty_shipped', $t('erpDocs.shipped')]]],
  changes: ['changes', $t('erpDocs.changeHistory'), [['version', $t('common.versions'), '60'], ['kind', $t('common.type'), '70'], ['reason', $t('common.reason'), '160'], ['diff', $t('erpDocs.whatChanged'), '260'], ['changed_by', $t('erpDocs.changedBy')], ['time', $t('common.time'), '150']]],
  deliveries: ['deliveries', $t('common.deliveryOrder'), [['code', $t('common.deliveryOrderNo'), '150'], ['ship_date', $t('common.date')], ['status', $t('common.status')], ['total', $t('erpDocs.totalInclTax')]]],
  returns: ['returns', $t('common.return2'), [['code', $t('common.docNo'), '150'], ['return_date', $t('common.date')], ['status', $t('common.status')], ['total', $t('erpDocs.totalInclTax')]]],
  work_orders: ['work_orders', $t('common.workOrder'), [['code', $t('common.woNo'), '150'], ['qty', $t('common.plan')], ['qty_done', $t('common.complete')], ['status', $t('common.status')], ['due', $t('common.dueDate'), '150']]],
  receipts: ['receipts', $t('common.receive'), [['code', $t('erpDocs.receiptNo'), '150'], ['receive_date', $t('common.date')], ['status', $t('common.status')], ['total', $t('erpDocs.totalInclTax')]]],
  reqs: ['reqs', $t('erpDocs.requisition'), [['code', $t('erpDocs.requisitionNo'), '150'], ['qty', $t('common.quantity')], ['need_date', $t('common.requiredDate')], ['status', $t('common.status')]]],
  inspections: ['inspections', $t('erpDocs.incomingInspection'), [['code', $t('erpDocs.inspectionNo'), '150'], ['lot', $t('common.lot'), '140'], ['result', $t('common.conclusion')], ['qty_sample', $t('erpDocs.sample')], ['qty_ng', $t('common.ng')], ['remark', $t('common.description'), '240']]],
};
const relTabs = computed(() => Object.entries(related.value || {}).filter(([k, v]) => REL[k] && Array.isArray(v)).map(([k, v]) => ({ key: k, label: REL[k][1], cols: REL[k][2], rows: v as any[] })));

watch(kind, () => { q.value = ''; status.value = ''; partner.value = null; drawer.value = false; load(1); });
watch(() => route.query.id, (id) => { if (id) openDoc(Number(id)); });
onMounted(async () => {
  get('/api/erp/mode', true).then((m) => (mode.value = m)).catch(() => {});
  await load(1);
  if (route.query.id) openDoc(Number(route.query.id));
});
</script>

<style scoped>
.kw { width: calc(200px * var(--hm-scale)); }
.st { width: calc(140px * var(--hm-scale)); }
.partner { width: calc(220px * var(--hm-scale)); }
.tbl { padding: 0; overflow: hidden; }
.tbl :deep(.el-table__row) { cursor: pointer; }
.pager { display: flex; justify-content: flex-end; padding: var(--hm-sp-2) var(--hm-sp-3); }
.bar { display: flex; align-items: center; gap: var(--hm-sp-2); flex-wrap: wrap; margin-bottom: var(--hm-sp-3); }
.bar .grow, .lines-head .grow { flex: 1; }
.credit { max-width: 520px; }
.head { display: grid; grid-template-columns: repeat(auto-fill, minmax(calc(200px * var(--hm-scale)), 1fr)); gap: 0 var(--hm-sp-3); }
.head .wide { grid-column: 1 / -1; }
.lines-head { display: flex; align-items: center; margin: var(--hm-sp-2) 0; }
.totals { display: flex; justify-content: flex-end; gap: var(--hm-sp-5); padding: var(--hm-sp-3) var(--hm-sp-2); color: var(--hm-text-2); }
.totals b { color: var(--hm-text); }
.totals .big { font-size: var(--hm-fs-lg); }
.rel { margin-top: var(--hm-sp-3); }
.link { color: var(--hm-primary); cursor: pointer; }
.sch-row { display: flex; gap: var(--hm-sp-2); align-items: center; margin-bottom: var(--hm-sp-2); }
.mt { margin-top: var(--hm-sp-3); }
</style>
