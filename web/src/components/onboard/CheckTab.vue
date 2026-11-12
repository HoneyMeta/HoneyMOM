<template>
  <div class="ck" data-test="onboard-check">
    <HmToolbar class="bar" data-tour="check-filters">
      <el-select v-model="ds" size="small" clearable :placeholder="$t('aiTab.allDataSources')" class="sel" data-test="check-ds" @change="load">
        <el-option v-for="d in sources" :key="d.id" :label="$t('common.n01', [d.code, PROTO_CN[d.protocol] || d.protocol])" :value="d.id" />
      </el-select>
      <el-select v-model="dev" size="small" clearable filterable :placeholder="$t('checkTab.allEquipment')" class="sel" @change="load">
        <el-option v-for="d in data?.devices || []" :key="d.equipment || 0" :label="`${d.code || '—'} ${d.name}`" :value="d.equipment || 0" />
      </el-select>
      <span class="chips">
        <button v-for="c in COLORS" :key="c" class="chip" :class="[c, { on: colors.has(c) }]" :data-test="'chip-' + c" @click="toggle(c)">
          <i />{{ COLOR_CN[c] }} <b class="hm-num">{{ c === 'yellow' ? data?.counts.yellow ?? 0 : data?.counts[c] ?? 0 }}</b></button>
      </span>
      <el-input v-model="q" size="small" clearable :placeholder="$t('checkTab.searchCodeMeaningAddress')" class="q" />
      <span class="grow" />
      <span class="cov">{{ $t('common.coverage') }} <b class="hm-num" data-test="coverage">{{ data ? (data.coverage * 100).toFixed(1) + '%' : '—' }}</b>
        <span class="muted small">{{ $t('checkTab.confirmed01', [data?.confirmed ?? 0, data?.total ?? 0]) }}</span></span>
      <el-button v-perm="'onboard.point.confirm'" size="small" class="cg" :disabled="!greenPending" data-test="confirm-green" data-tour="check-confirm" @click="confirmGreen">
        {{ $t('checkTab.confirmAllGreen0', [greenPending]) }}</el-button>
      <el-button v-perm="'onboard.point.confirm'" size="small" :disabled="!sel.length" data-test="confirm-selected" @click="confirmIds(sel.map((r) => r.id))">{{ $t('checkTab.confirmSelected0', [sel.length]) }}</el-button>
    </HmToolbar>

    <div class="grid">
      <HmCard :title="$t('checkTab.byEquipment')" flush class="devs">
        <div class="dl" data-test="check-devices">
          <button v-for="d in data?.devices || []" :key="d.equipment || 0" class="dv" :class="{ on: dev === (d.equipment || 0) }" @click="dev = dev === (d.equipment || 0) ? null : (d.equipment || 0); load()">
            <span class="dc">{{ d.code || '—' }}</span><span class="dn">{{ d.name }}</span>
            <span class="bars"><i class="g" :style="{ width: pctw(d.green, d.total) }" /><i class="o" :style="{ width: pctw(d.orange, d.total) }" /><i class="r" :style="{ width: pctw(d.red, d.total) }" /></span>
            <span class="hm-num small">{{ d.confirmed }}/{{ d.total }}</span>
          </button>
        </div>
      </HmCard>

      <div class="main">
        <HmCard v-if="showYellow && (data?.yellow || []).length" :title="$t('checkTab.addressesNotInTagList', [data.yellow.length])" flush data-tour="check-yellow">
          <template #actions><el-button v-perm="'onboard.point.confirm'" link size="small" @click="clearYellow">{{ $t('checkTab.ignoreAll') }}</el-button></template>
          <HmDataTable :columns="Y_COLS" :rows="yellowRows" row-key="id" :ai="false" :max-height="260" data-test="yellow-table">
            <template #cell-address="{ row }"><span class="hm-mono small">{{ row.address }}</span></template>
            <template #cell-sample="{ row }"><span class="small">{{ String(row.sample ?? '').slice(0, 40) }}</span></template>
            <template #cell-hint="{ row }"><span class="small muted">{{ row.attrs?.unit || '' }} {{ row.attrs?.description || '' }}</span></template>
            <template #cell-op="{ row }">
              <el-button v-perm="'onboard.point.confirm'" link size="small" type="primary" :data-test="'yadd-' + row.id" @click="openAdd(row)">{{ $t('checkTab.addToTagList') }}</el-button>
              <el-button v-perm="'onboard.point.confirm'" link size="small" @click="ignoreSeen(row)">{{ $t('checkTab.ignore') }}</el-button>
            </template>
          </HmDataTable>
        </HmCard>

        <HmCard :title="$t('checkTab.tags0', [rows.length])" flush data-tour="check-table">
          <div class="tw">
            <el-table :data="rows" size="small" height="560" row-key="id" data-test="check-table" @selection-change="(v: any[]) => (sel = v)">
              <el-table-column type="selection" width="36" :selectable="(r: any) => r.status !== 'confirmed'" />
              <el-table-column label="" width="30"><template #default="{ row }"><span class="dot" :class="row.color" :title="COLOR_CN[row.color]" /></template></el-table-column>
              <el-table-column prop="code" :label="$t('common.tagCode')" min-width="190" show-overflow-tooltip />
              <el-table-column prop="device" :label="$t('common.equipment')" width="78" />
              <el-table-column prop="meaning" :label="$t('common.meaning')" min-width="130" show-overflow-tooltip />
              <el-table-column :label="$t('common.purpose')" width="76"><template #default="{ row }">{{ ROLE_CN[row.role] || row.role || '—' }}</template></el-table-column>
              <el-table-column :label="$t('aiTab.lastValue')" min-width="110" show-overflow-tooltip><template #default="{ row }"><span class="hm-num">{{ fmtV(row.value) }}</span> <span class="muted small">{{ row.unit || '' }}</span></template></el-table-column>
              <el-table-column :label="$t('common.count')" width="62" align="right"><template #default="{ row }"><span class="hm-num">{{ row.n }}</span></template></el-table-column>
              <el-table-column :label="$t('common.issue')" min-width="120" show-overflow-tooltip><template #default="{ row }"><span class="small" :class="{ warnc: row.flags.length }">{{ row.flags_cn.join($t('common.k42')) || (row.color === 'red' ? $t('checkTab.unreadable') : '') }}</span></template></el-table-column>
              <el-table-column :label="$t('common.confirm')" width="70"><template #default="{ row }"><HmTag :tone="row.status === 'confirmed' ? 'ok' : row.status === 'ignored' ? 'neutral' : 'warn'">{{ STATUS_CN[row.status] || $t('common.toConfirm') }}</HmTag></template></el-table-column>
              <el-table-column label="" width="96" fixed="right">
                <template #default="{ row }">
                  <el-button v-if="row.status !== 'confirmed'" v-perm="'onboard.point.confirm'" link size="small" type="primary" @click="confirmIds([row.id])">{{ $t('common.confirm') }}</el-button>
                  <el-button v-if="row.status !== 'ignored'" v-perm="'onboard.point.confirm'" link size="small" @click="act(row, 'ignore')">{{ $t('checkTab.ignore') }}</el-button>
                  <el-button v-else v-perm="'onboard.point.confirm'" link size="small" @click="act(row, 'pending')">{{ $t('checkTab.restore') }}</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </HmCard>
      </div>
    </div>

    <el-dialog v-model="adlg" :title="$t('checkTab.addToTagList')" width="520px" append-to-body data-test="yadd-dialog">
      <el-form v-if="addRow" label-width="80px" size="small">
        <el-form-item :label="$t('common.address')"><span class="hm-mono small">{{ addRow.address }}</span></el-form-item>
        <el-form-item :label="$t('checkTab.sampleValue')">{{ String(addRow.sample ?? '') }}</el-form-item>
        <el-form-item :label="$t('common.equipment')"><el-select v-model="addForm.device" filterable clearable style="width:100%"><el-option v-for="d in devices" :key="d.code" :label="`${d.code} ${d.name}`" :value="d.code" /></el-select></el-form-item>
        <el-form-item :label="$t('checkTab.tagPath')"><el-input v-model="addForm.name" :placeholder="$t('checkTab.eGMonitoringSpindleLoad')" /></el-form-item>
        <el-form-item :label="$t('common.meaning')"><el-input v-model="addForm.meaning" /></el-form-item>
        <el-form-item :label="$t('common.unit')"><el-input v-model="addForm.unit" style="width:120px" /></el-form-item>
        <el-form-item :label="$t('common.purpose')"><el-select v-model="addForm.role" clearable style="width:160px"><el-option v-for="r in ROLES" :key="r" :label="ROLE_CN[r]" :value="r" /></el-select></el-form-item>
        <el-form-item :label="$t('checkTab.confirmDirectly')"><el-switch v-model="addForm.confirm" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="adlg = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" data-test="yadd-ok" @click="addSeen">{{ $t('checkTab.addToTagList') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRoute } from 'vue-router';
import { get, post } from '../../api';
import { COLOR_CN, PROTO_CN, ROLE_CN, ROLES, STATUS_CN } from './common';

const route = useRoute();
const data = ref<any>(null), sources = ref<any[]>([]), devices = ref<any[]>([]);
const ds = ref<number | null>(route.query.ds ? Number(route.query.ds) : null), dev = ref<number | null>(null), q = ref(''), sel = ref<any[]>([]);
const COLORS = ['red', 'yellow', 'orange', 'green', 'grey'];
const colors = ref(new Set<string>(['red', 'yellow', 'orange', 'green']));
const showYellow = computed(() => colors.value.has('yellow'));
const Y_COLS = [{ key: 'datasource', label: $t('common.dataSource'), width: 70 }, { key: 'address', label: $t('common.address') }, { key: 'sample', label: $t('checkTab.sampleValue'), width: 160 }, { key: 'count', label: $t('common.count'), num: true, width: 60 },
  { key: 'hint', label: $t('checkTab.attribute'), width: 140, sortable: false }, { key: 'op', label: '', width: 120, sortable: false }];
const kw = (r: any, s: string) => !s || [r.code, r.meaning, r.address, r.device].some((x) => String(x || '').toLowerCase().includes(s.toLowerCase()));
const rows = computed(() => (data.value?.rows || []).filter((r: any) => colors.value.has(r.color) && kw(r, q.value)));
const yellowRows = computed(() => (data.value?.yellow || []).filter((r: any) => !q.value || String(r.address).toLowerCase().includes(q.value.toLowerCase())));
const greenPending = computed(() => (data.value?.rows || []).filter((r: any) => r.color === 'green' && r.status !== 'confirmed' && r.equipment).length);
const pctw = (n: number, t: number) => (t ? (n / t) * 100 : 0) + '%';
const fmtV = (v: any) => (v === null || v === undefined ? '—' : typeof v === 'number' ? (Number.isInteger(v) ? String(v) : v.toFixed(3).replace(/0+$/, '').replace(/\.$/, '')) : String(v).slice(0, 40));
function toggle(c: string) { const s = new Set(colors.value); s.has(c) ? s.delete(c) : s.add(c); colors.value = s; }
let timer: any = null;

async function load() {
  const qs = new URLSearchParams();
  if (ds.value) qs.set('datasource', String(ds.value));
  if (dev.value) qs.set('device', String(dev.value));
  data.value = await get('/api/onboard/check?' + qs.toString(), true).catch(() => data.value);
}
async function confirmIds(ids: number[]) { const r = await post('/api/onboard/confirm', { ids }); ElMessage.success($t('checkTab.n0TagsConfirmed', [r.n])); sel.value = []; load(); }
async function confirmGreen() { const r = await post('/api/onboard/confirm', { all_green: true, datasource: ds.value, device: dev.value }); ElMessage.success($t('checkTab.n0GreenTagsConfirmed', [r.n])); load(); }
async function act(row: any, a: string) { await post(`/api/onboard/points/${row.id}/${a}`, {}); load(); }
async function ignoreSeen(row: any) { await post(`/api/onboard/seen/${row.id}/ignore`, {}); load(); }
async function clearYellow() { const r = await post('/api/onboard/seen/clear/ignore', {}); ElMessage.success($t('checkTab.n0AddressesIgnored', [r.n])); load(); }
const adlg = ref(false), addRow = ref<any>(null), addForm = ref<any>({});
function openAdd(row: any) {
  addRow.value = row;
  const hint = row.attrs?.hint || '';
  const [d, ...rest] = hint.split('/');
  addForm.value = { device: devices.value.some((x) => x.code === d) ? d : '', name: rest.join('/') || String(row.address).split(/[/:]/).pop(), meaning: row.attrs?.description || '',
    unit: row.attrs?.unit || '', role: '', confirm: false };
  adlg.value = true;
}
async function addSeen() { await post(`/api/onboard/seen/${addRow.value.id}/add`, addForm.value); ElMessage.success($t('checkTab.addedToTagList')); adlg.value = false; load(); }
onMounted(async () => {
  const s = await get('/api/onboard/summary', true).catch(() => null);
  sources.value = s?.sources || [];
  devices.value = (await get('/api/factory/devices', true).catch(() => ({ items: [] }))).items;
  load();
  timer = setInterval(() => { if (!document.hidden) load(); }, 6000);
});
onUnmounted(() => clearInterval(timer));
</script>

<style scoped>
.ck { display: grid; gap: 12px; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.sel { width: 200px; } .q { width: 190px; } .grow { flex: 1; }
.chips { display: inline-flex; gap: 6px; flex-wrap: wrap; }
.chip { display: inline-flex; align-items: center; gap: 4px; height: 26px; padding: 0 9px; border-radius: 13px; border: 1px solid var(--hm-border); background: var(--hm-surface);
  font-size: var(--hm-fs-xs); color: var(--hm-text-2); cursor: pointer; opacity: .55; }
.chip.on { opacity: 1; border-color: var(--hm-border-strong, #b9c2ce); }
.chip i, .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; }
.red i, .dot.red { background: #d33; } .yellow i, .dot.yellow { background: #e0b100; } .orange i, .dot.orange { background: #e8711c; } .green i, .dot.green { background: #1f9d55; }
.grey i, .dot.grey { background: #9aa5b4; }
.cov { font-size: var(--hm-fs-sm); }
.cg { font-weight: 600; color: #145c33; border-color: #1f9d55; }
.grid { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 12px; align-items: start; }
@media (max-width: 1279px) { .grid { grid-template-columns: minmax(0, 1fr); } .devs { display: none; } }
.main { display: grid; gap: 12px; min-width: 0; }
.dl { max-height: 640px; overflow: auto; display: grid; }
.dv { display: grid; grid-template-columns: 62px minmax(0, 1fr) 54px; grid-template-rows: auto 4px; gap: 2px 6px; align-items: center; padding: 6px 10px; border: 0;
  border-bottom: 1px solid var(--hm-hairline); background: none; text-align: left; cursor: pointer; font-size: var(--hm-fs-xs); color: var(--hm-text); }
.dv.on { background: var(--hm-primary-soft); }
.dc { font-weight: 600; font-family: var(--hm-font-mono); } .dn { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--hm-text-2); }
.bars { grid-column: 1 / 4; display: flex; height: 4px; border-radius: 2px; overflow: hidden; background: var(--hm-surface-3); }
.bars .g { background: #1f9d55; } .bars .o { background: #e8711c; } .bars .r { background: #d33; }
.tw { padding: 0 4px; }
.small { font-size: var(--hm-fs-xs); } .muted { color: var(--hm-text-3); } .warnc { color: #b4530f; }
</style>
