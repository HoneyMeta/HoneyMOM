<template>
  <div class="page" data-test="impl-reports">
    <HmPageHead :title="$t('common.reportDesigner')" :sub="$t('implReports.pickADatasetFieldsFilters')">
      <el-button v-if="d.can_design" type="primary" data-test="rp-new" @click="newReport"><el-icon><Plus /></el-icon>{{ $t('implReports.newReport') }}</el-button>
    </HmPageHead>
    <div class="lay">
      <HmCard :title="$t('common.report')" flush class="list">
        <div v-for="r in reports" :key="r.id" class="ri" :class="{ on: cur?.id === r.id }" :data-test="'rp-item-' + r.id" @click="open(r)">
          <b>{{ r.name }}</b><div class="small muted">{{ r.dataset_label }} · {{ CHART[r.chart] || r.chart }}{{ r.owner !== me ? ' · ' + r.owner : '' }}{{ r.source ? $t('implReports.industryTemplate') : '' }}
            <HmTag v-if="r.pinned" tone="primary">{{ $t('common.home') }}</HmTag><HmTag v-if="r.schedule" tone="amber">{{ $t('common.scheduled') }}</HmTag></div>
        </div>
        <HmEmpty v-if="!reports.length" compact :title="$t('implReports.noReportsYet')" />
      </HmCard>

      <div class="main">
        <HmCard :title="cur?.id ? cur.name : $t('implReports.newReport2')" :sub="ds?.desc">
          <template #actions>
            <template v-if="cur?.id">
              <el-button size="small" data-test="rp-xlsx" @click="openDownload(`/api/impl/reports/${cur.id}/export.xlsx`)">{{ $t('implReports.exportToExcel') }}</el-button>
              <el-button size="small" data-test="rp-pdf" @click="openDownload(`/api/impl/reports/${cur.id}/export.pdf`)">{{ $t('daily.exportPdf') }}</el-button>
              <el-button size="small" :type="cur.pinned ? 'primary' : ''" data-test="rp-pin" @click="pin">{{ cur.pinned ? $t('aiBlocks.pinnedToHome') : $t('aiBlocks.pinToHome') }}</el-button>
            </template>
            <el-button v-if="d.can_design && (!cur?.id || cur.can_edit)" size="small" type="primary" data-test="rp-save" @click="openSave">{{ $t('common.save') }}</el-button>
          </template>
          <div v-if="d.can_design && (!cur?.id || cur.can_edit)" class="des" data-test="rp-designer">
            <div class="row"><label>{{ $t('implReports.dataset') }}</label><el-select v-model="cfg.dataset" filterable style="width:240px" data-test="rp-dataset" @change="resetCfg">
              <el-option-group v-for="g in dsGroups" :key="g.name" :label="g.name"><el-option v-for="x in g.items" :key="x.key" :value="x.key" :label="x.label" /></el-option-group></el-select>
              <el-radio-group v-model="mode" size="small" data-test="rp-mode"><el-radio-button value="agg">{{ $t('implReports.groupAggregate') }}</el-radio-button><el-radio-button value="list">{{ $t('common.details') }}</el-radio-button></el-radio-group>
              <el-select v-model="chart" size="small" style="width:110px" data-test="rp-chart"><el-option v-for="(l, k) in d.charts" :key="k" :value="k" :label="l" /></el-select></div>
            <div v-if="mode === 'list'" class="row"><label>{{ $t('common.field') }}</label><el-select v-model="cfg.fields" multiple filterable collapse-tags :max-collapse-tags="6" style="flex:1" data-test="rp-fields">
              <el-option v-for="f in ds?.fields || []" :key="f.key" :value="f.key" :label="f.label" /></el-select></div>
            <template v-else>
              <div class="row"><label>{{ $t('common.group') }}</label>
                <div class="items"><div v-for="(g, i) in cfg.group" :key="i" class="it">
                  <el-select v-model="g.field" filterable size="small" style="width:160px" :data-test="'rp-group-' + i"><el-option v-for="f in ds?.fields || []" :key="f.key" :value="f.key" :label="f.label" /></el-select>
                  <el-select v-if="isTime(g.field)" v-model="g.bucket" clearable size="small" :placeholder="$t('implReports.rawValues')" style="width:90px"><el-option v-for="(l, k) in d.buckets" :key="k" :value="k" :label="l" /></el-select>
                  <el-button size="small" link type="danger" @click="cfg.group.splice(i, 1)">×</el-button></div>
                  <el-button size="small" link type="primary" data-test="rp-add-group" @click="cfg.group.push({ field: '' })">{{ $t('implForms.group') }}</el-button></div></div>
              <div class="row"><label>{{ $t('implReports.aggregate') }}</label>
                <div class="items"><div v-for="(m, i) in cfg.measures" :key="i" class="it">
                  <el-select v-model="m.agg" size="small" style="width:96px" :data-test="'rp-agg-' + i"><el-option v-for="(l, k) in d.aggs" :key="k" :value="k" :label="l" /></el-select>
                  <el-select v-if="m.agg !== 'count'" v-model="m.field" filterable size="small" style="width:150px" :data-test="'rp-measure-' + i">
                    <el-option v-for="f in (ds?.fields || []).filter((x: any) => m.agg === 'count_distinct' || ['min', 'max'].includes(m.agg) || x.t === 'num')" :key="f.key" :value="f.key" :label="f.label" /></el-select>
                  <el-button size="small" link type="danger" @click="cfg.measures.splice(i, 1)">×</el-button></div>
                  <el-button size="small" link type="primary" data-test="rp-add-measure" @click="cfg.measures.push({ agg: 'sum', field: '' })">{{ $t('implReports.aggregate2') }}</el-button></div></div>
            </template>
            <div class="row"><label>{{ $t('implReports.filters') }}</label>
              <div class="items"><div v-for="(f, i) in cfg.filters" :key="i" class="it">
                <el-select v-model="f.field" filterable size="small" style="width:150px" :data-test="'rp-filter-' + i"><el-option v-for="x in ds?.fields || []" :key="x.key" :value="x.key" :label="x.label" /></el-select>
                <el-select v-model="f.op" size="small" style="width:110px" :data-test="'rp-filter-op-' + i"><el-option v-for="(l, k) in opsFor(f.field)" :key="k" :value="k" :label="l" /></el-select>
                <template v-if="!['empty', 'not_empty', 'today', 'this_month'].includes(f.op)">
                  <el-select v-if="enumOf(f.field) && ['=', '!='].includes(f.op)" v-model="f.value" size="small" style="width:130px"><el-option v-for="(l, k) in enumOf(f.field)" :key="k" :value="k" :label="l" /></el-select>
                  <el-input v-else v-model="f.value" size="small" style="width:130px" :placeholder="f.op === 'last_days' ? $t('implReports.days') : f.op === 'between' ? $t('implReports.startEnd') : $t('common.value')" :data-test="'rp-filter-val-' + i" /></template>
                <el-button size="small" link type="danger" @click="cfg.filters.splice(i, 1)">×</el-button></div>
                <el-button size="small" link type="primary" data-test="rp-add-filter" @click="cfg.filters.push({ field: ds?.time || '', op: ds?.time ? 'last_days' : '=', value: ds?.time ? 7 : '' })">{{ $t('implReports.filter') }}</el-button></div></div>
            <div class="row"><label /><el-button type="primary" plain size="small" :loading="busy" data-test="rp-run" @click="run">{{ $t('common.running') }}</el-button><span v-if="res" class="small muted">{{ $t('common.n0Rows', [res.rows.length]) }}</span></div>
          </div>
          <ReportView v-if="res" :res="res" :chart="chart" :height="300" show-table :title="cur?.name || ''" />
        </HmCard>
      </div>
    </div>

    <el-dialog v-model="sdlg" :title="$t('implReports.saveReport')" width="min(560px, 96vw)" append-to-body data-test="rp-save-dialog">
      <el-form label-width="96px">
        <el-form-item :label="$t('common.name')" required><el-input v-model="sv.name" data-test="rp-name" /></el-form-item>
        <el-form-item :label="$t('common.shareWith')"><el-select v-model="sv.shared_roles" multiple style="width:100%" :placeholder="$t('implReports.onlyMe')" data-test="rp-share">
          <el-option value="*" :label="$t('common.everyone')" /><el-option v-for="(n, r) in d.roles" :key="r" :value="r" :label="n" /></el-select></el-form-item>
        <el-form-item :label="$t('implReports.scheduledExport')"><el-switch v-model="sv.schedule.enabled" data-test="rp-sched" /></el-form-item>
        <template v-if="sv.schedule.enabled">
          <el-form-item :label="$t('implReports.frequency')"><el-select v-model="sv.schedule.freq" style="width:100px"><el-option value="daily" :label="$t('skillSaveDialog.daily')" /><el-option value="weekly" :label="$t('implReports.weekly')" /><el-option value="monthly" :label="$t('implReports.monthly')" /></el-select>
            <el-select v-if="sv.schedule.freq === 'weekly'" v-model="sv.schedule.weekday" style="width:90px;margin-left:6px"><el-option v-for="i in 7" :key="i" :value="i" :label="weekdayName(i)" /></el-select>
            <el-input-number v-if="sv.schedule.freq === 'monthly'" v-model="sv.schedule.day" :min="1" :max="28" style="width:100px;margin-left:6px" />
            <el-time-select v-model="sv.schedule.time" start="00:00" step="00:30" end="23:30" style="width:110px;margin-left:6px" /></el-form-item>
          <el-form-item :label="$t('approvalCard.format')"><el-radio-group v-model="sv.schedule.format"><el-radio-button value="xlsx">Excel</el-radio-button><el-radio-button value="pdf">PDF</el-radio-button></el-radio-group></el-form-item>
          <el-form-item :label="$t('implReports.sendTo')"><el-select v-model="sv.schedule.to_roles" multiple style="width:100%" :placeholder="$t('implReports.emptySendToMyself')"><el-option v-for="(n, r) in d.roles" :key="r" :value="r" :label="n" /></el-select></el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-popconfirm v-if="cur?.id" :title="$t('implReports.deleteThisReport')" @confirm="remove"><template #reference><el-button type="danger" plain>{{ $t('common.delete') }}</el-button></template></el-popconfirm>
        <el-button v-if="cur?.id && sv.schedule.enabled" data-test="rp-sched-now" @click="schedNow">{{ $t('implReports.runNow') }}</el-button>
        <el-button @click="sdlg = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :loading="busy" data-test="rp-save-ok" @click="save">{{ $t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t, intlLocale } from '../i18n';
const weekdayName = (i: number) => new Date(2024, 0, i).toLocaleDateString(intlLocale, { weekday: 'long' });  // 2024-01-01 is a Monday
import { ElMessage } from 'element-plus';
import { computed, onMounted, reactive, ref } from 'vue';
import { useRoute } from 'vue-router';
import { del, get, post, put, session } from '../api';
import ReportView from '../impl/ReportView.vue';
import { openDownload } from '../impl/state';

const route = useRoute();
const d = ref<any>({ datasets: [], aggs: {}, buckets: {}, ops: {}, charts: {}, roles: {} }), reports = ref<any[]>([]), cur = ref<any>(null), res = ref<any>(null), busy = ref(false);
const cfg = reactive<any>({ dataset: 'work_order', fields: [], filters: [], group: [], measures: [] }), mode = ref('agg'), chart = ref('bar');
const sdlg = ref(false), sv = reactive<any>({ name: '', shared_roles: [], schedule: { enabled: false } });
const CHART: Record<string, string> = { table: $t('common.table'), bar: $t('implReports.barChart'), line: $t('implReports.lineChart'), pie: $t('implReports.pieChart'), kpi: $t('skillSaveDialog.kpiCard') };
const me = computed(() => session.user?.username);
const ds = computed(() => d.value.datasets.find((x: any) => x.key === cfg.dataset));
const dsGroups = computed(() => { const g: Record<string, any[]> = {}; for (const x of d.value.datasets) (g[x.module] ||= []).push(x); return Object.entries(g).map(([name, items]) => ({ name, items })); });
const fieldOf = (k: string) => ds.value?.fields.find((f: any) => f.key === k);
const isTime = (k: string) => ['date', 'datetime'].includes(fieldOf(k)?.t);
const enumOf = (k: string) => fieldOf(k)?.enum;
const opsFor = (k: string) => { const t = fieldOf(k)?.t, o = d.value.ops; const time = ['today', 'last_days', 'this_month', 'between'];
  return Object.fromEntries(Object.entries(o).filter(([op]) => (['date', 'datetime'].includes(t) ? true : !time.includes(op) || op === 'between'))); };
function resetCfg() { Object.assign(cfg, { fields: [], filters: [], group: [], measures: [{ agg: 'count' }] }); res.value = null; }
function newReport() { cur.value = null; resetCfg(); cfg.dataset = d.value.datasets[0]?.key || 'work_order'; mode.value = 'agg'; chart.value = 'bar'; }
const clean = () => (mode.value === 'list' ? { fields: cfg.fields, filters: cfg.filters.filter((f: any) => f.field), limit: 1000 }
  : { group: cfg.group.filter((g: any) => g.field), measures: cfg.measures.filter((m: any) => m.agg && (m.agg === 'count' || m.field)), filters: cfg.filters.filter((f: any) => f.field) });
async function run() { busy.value = true; try { res.value = await post('/api/impl/reports/run', { dataset: cfg.dataset, config: clean() }); } finally { busy.value = false; } }
async function load() { reports.value = await get('/api/impl/reports'); }
async function open(r: any) {
  cur.value = r; const c = r.config || {};
  Object.assign(cfg, { dataset: r.dataset, fields: c.fields || [], filters: JSON.parse(JSON.stringify(c.filters || [])), group: JSON.parse(JSON.stringify(c.group || [])),
    measures: JSON.parse(JSON.stringify(c.measures || [])) });
  mode.value = c.fields?.length && !c.group?.length ? 'list' : 'agg'; chart.value = r.chart || 'table';
  res.value = await get(`/api/impl/reports/${r.id}/data`);
}
function openSave() {
  Object.assign(sv, { name: cur.value?.name || $t('implReports.n0Report', [ds.value?.label || '']), shared_roles: cur.value?.shared_roles || [],
    schedule: { enabled: false, freq: 'daily', time: '08:00', weekday: 1, day: 1, format: 'xlsx', to_roles: [], ...(cur.value?.schedule || {}) } });
  sdlg.value = true;
}
async function save() {
  busy.value = true;
  try {
    const body = { name: sv.name, dataset: cfg.dataset, config: clean(), chart: chart.value, shared_roles: sv.shared_roles, schedule: sv.schedule };
    const r = cur.value?.id ? await put(`/api/impl/reports/${cur.value.id}`, body) : await post('/api/impl/reports', body);
    ElMessage.success($t('common.saved')); sdlg.value = false; await load(); const n = reports.value.find((x) => x.id === r.id); if (n) await open(n);
  } finally { busy.value = false; }
}
async function remove() { await del(`/api/impl/reports/${cur.value.id}`); sdlg.value = false; cur.value = null; await load(); newReport(); }
async function pin() {
  const r = await post(`/api/impl/reports/${cur.value.id}/pin`, { on: !cur.value.pinned }); cur.value.pinned = r.pinned;
  ElMessage.success(r.pinned ? $t('implReports.pinnedToHomeYouCan') : $t('implReports.removedFromHome')); await load();
}
async function schedNow() { const r = await post(`/api/impl/reports/${cur.value.id}/run_schedule`, {}); ElMessage.success($t('implReports.generated0RowsNotificationSent', [r.rows])); }
onMounted(async () => {
  d.value = await get('/api/impl/reports/datasets'); await load();
  if (route.query.export) { openDownload(`/api/impl/reports/exports/${route.query.export}`); }
  const want = route.query.id ? reports.value.find((x) => String(x.id) === String(route.query.id)) : reports.value[0];
  if (want) await open(want); else newReport();
});
</script>

<style scoped>
.lay { display: grid; grid-template-columns: 280px minmax(0, 1fr); gap: var(--hm-gap); align-items: start; }
@media (max-width: 1000px) { .lay { grid-template-columns: 1fr; } }
.list { max-height: calc(100vh - 200px); overflow: auto; }
.ri { padding: 8px 14px; border-bottom: 1px solid var(--hm-hairline); cursor: pointer; } .ri:hover { background: var(--hm-surface-2); } .ri.on { background: var(--hm-primary-soft); }
.des { display: flex; flex-direction: column; gap: 8px; margin-bottom: 10px; padding-bottom: 10px; border-bottom: 1px solid var(--hm-hairline); }
.row { display: flex; align-items: flex-start; gap: 8px; flex-wrap: wrap; } .row > label { width: 40px; color: var(--hm-text-3); font-size: var(--hm-fs-xs); padding-top: 5px; }
.items { display: flex; flex-direction: column; gap: 4px; flex: 1; } .it { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
</style>
