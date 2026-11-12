<template>
  <div class="page" data-test="impl-import">
    <HmPageHead :title="$t('common.dataImport')" :sub="$t('implImport.masterAndOpeningDataBefore')">
      <el-button data-test="imp-workbook" @click="openDownload('/api/impl/workbook.xlsx')"><el-icon><Download /></el-icon>{{ $t('implImport.initializationWorkbook') }}</el-button>
      <label class="el-button el-button--primary" data-test="imp-workbook-up"><input type="file" accept=".xlsx" hidden @change="uploadWorkbook" /><el-icon><Upload /></el-icon>{{ $t('implImport.uploadWholeWorkbook') }}</label>
    </HmPageHead>

    <el-tabs v-model="tab" data-test="imp-tabs">
      <el-tab-pane :label="$t('implImport.importTemplates')" name="list">
        <div v-for="g in groups" :key="g.name" class="grp">
          <h4>{{ g.name }}</h4>
          <div class="cards">
            <div v-for="i in g.items" :key="i.key" class="ic" :data-test="'imp-card-' + i.key">
              <div class="ih"><b>{{ i.label }}</b><HmTag v-if="i.existing !== null && i.existing !== undefined" tone="neutral" class="hm-num">{{ $t('implImport.n0Existing', [i.existing]) }}</HmTag></div>
              <div class="small muted dsc">{{ i.doc || $t('implImport.existingMatchedBy0', [i.key_labels.join(' + ')]) }}</div>
              <div v-if="i.depends.length" class="small muted">{{ $t('implImport.importFirst0', [i.depends.map(labelOf).join($t('common.k42'))]) }}</div>
              <div class="ia">
                <el-button size="small" :data-test="'imp-template-' + i.key" @click="openDownload(`/api/impl/importers/${i.key}/template.xlsx`)">{{ $t('common.template') }}</el-button>
                <el-button size="small" type="primary" :disabled="!i.allowed" :data-test="'imp-start-' + i.key" @click="start(i)">{{ $t('common.import') }}</el-button>
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>
      <el-tab-pane :label="$t('common.importHistory')" name="history">
        <HmCard flush>
          <HmDataTable :columns="JCOLS" :rows="jobs" row-key="id" :ai="false" :empty-text="$t('implImport.noImportsYet')" data-test="imp-history">
            <template #cell-status="{ row }"><HmTag :tone="row.status === 'committed' ? 'ok' : row.status === 'failed' ? 'bad' : 'neutral'">{{ row.status_label }}</HmTag></template>
            <template #cell-summary="{ row }"><span class="small">{{ sumText(row.summary) }}</span></template>
            <template #cell-op="{ row }">
              <el-button v-if="row.status !== 'committed'" link size="small" type="primary" @click="resume(row)">{{ $t('overviewTab.continue') }}</el-button>
              <el-button link size="small" @click="openDownload(`/api/impl/import/jobs/${row.id}/errors.xlsx`)">{{ $t('implImport.report') }}</el-button>
            </template>
          </HmDataTable>
        </HmCard>
      </el-tab-pane>
    </el-tabs>

    <el-drawer v-model="wz" :size="drawerSize" :title="imp ? $t('implImport.import0', [imp.label]) : $t('common.import')" destroy-on-close data-test="imp-wizard">
      <el-steps :active="step" finish-status="success" simple class="steps">
        <el-step :title="$t('cfFile.upload')" /><el-step :title="$t('implImport.mapColumns')" /><el-step :title="$t('implImport.validatePreview')" /><el-step :title="$t('common.done')" />
      </el-steps>

      <!-- 0 Upload -->
      <div v-if="step === 0" class="pane">
        <el-alert v-if="imp?.depends?.length" type="info" :closable="false" show-icon :title="$t('implImport.pleaseImportFirst0References', [imp.depends.map(labelOf).join($t('common.k42'))])" />
        <label class="drop" data-test="imp-drop"><input type="file" accept=".xlsx,.xlsm,.csv,.txt" hidden data-test="imp-file" @change="upload" />
          <el-icon :size="32"><UploadFilled /></el-icon><div>{{ $t('implImport.clickHereToChooseA') }}</div>
          <div class="small muted">{{ $t('implImport.notSureHowToFill') }}<a class="lnk" @click.prevent="openDownload(`/api/impl/importers/${imp.key}/template.xlsx`)">{{ $t('implImport.downloadTheTemplate') }}</a>{{ $t('implImport.theFirstSheetHasInstructions') }}</div>
        </label>
        <div v-if="busy" class="muted">{{ $t('implImport.reading') }}</div>
      </div>

      <!-- 1 Map columns -->
      <div v-if="step === 1 && job" class="pane">
        <div class="bar">
          <span class="muted small">{{ $t('implImport.n012Rows', [job.filename, job.sheet, job.n_rows]) }}</span><span class="grow" />
          <el-radio-group v-model="mode" size="small" data-test="imp-mode"><el-radio-button v-for="(l, k) in MODES" :key="k" :value="k">{{ l }}</el-radio-button></el-radio-group>
          <el-button v-if="aiOn('impl')" size="small" :loading="aiBusy" data-test="imp-ai" @click="aiMap"><el-icon><MagicStick /></el-icon>{{ $t('implImport.aiSuggestMapping') }}</el-button>
        </div>
        <el-alert v-if="missing.length" type="warning" :closable="false" show-icon :title="$t('implImport.requiredColumnsNotYetMapped', [missing.join($t('common.k42'))])" data-test="imp-missing" />
        <table class="hm-table dense map" data-test="imp-map">
          <thead><tr><th>{{ $t('implImport.columnInFile') }}</th><th>{{ $t('implImport.sampleValue') }}</th><th style="width:240px">{{ $t('implImport.mapToField') }}</th><th /></tr></thead>
          <tbody>
            <tr v-for="(h, i) in job.headers" :key="i" :data-test="'imp-map-row-' + i">
              <td><b>{{ h || $t('implImport.emptyHeader') }}</b></td><td class="small muted">{{ samples(i) }}</td>
              <td><el-select v-model="mapping[String(i)]" clearable filterable size="small" :placeholder="$t('implImport.donTImportThisColumn')" style="width:100%" :data-test="'imp-map-' + i">
                <el-option v-for="c in job.columns" :key="c.key" :value="c.key" :label="c.label + (c.req ? ' *' : '')" :disabled="usedBy(c.key, i)" /></el-select></td>
              <td class="small"><HmTag v-if="aiSug[String(i)]" tone="ai">AI</HmTag><span v-if="aiWhy[h]" class="muted"> {{ aiWhy[h] }}</span></td>
            </tr>
          </tbody>
        </table>
        <div class="foot"><el-button @click="step = 0">{{ $t('common.back') }}</el-button><el-button type="primary" :loading="busy" data-test="imp-validate" @click="validate(1)">{{ $t('implImport.validatePreview') }}</el-button></div>
      </div>

      <!-- 2 Validate & preview -->
      <div v-if="step === 2 && pv" class="pane">
        <div class="kpis" data-test="imp-summary">
          <div v-for="k in SUM" :key="k.k" class="kp" :class="[k.k, { on: only === k.only }]" @click="filterBy(k.only)"><small>{{ k.l }}</small><b class="hm-num">{{ pv.summary[k.k] }}</b></div>
        </div>
        <el-alert v-for="(n, i) in pv.notes" :key="i" type="info" :closable="false" :title="n" />
        <el-alert v-if="dry" type="success" :closable="false" show-icon data-test="imp-dry-result"
          :title="$t('implImport.dryRunPassedRolledBack', [dry.done.create, dry.done.update, dry.done.skip])" />
        <table class="hm-table dense rows" data-test="imp-rows">
          <thead><tr><th style="width:56px">{{ $t('erpIntegration.rowNo') }}</th><th style="width:64px">{{ $t('common.result') }}</th><th v-for="c in shownCols" :key="c.key">{{ c.label }}</th><th>{{ $t('implImport.errorsNotes') }}</th></tr></thead>
          <tbody>
            <tr v-for="r in pv.rows" :key="r.n" :class="r.action" data-test="imp-row">
              <td class="hm-num">{{ r.n }}</td><td><HmTag :tone="TONE[r.action] || 'neutral'">{{ ACT[r.action] || r.action }}</HmTag></td>
              <td v-for="c in shownCols" :key="c.key" :class="{ bad: r.errors.some((e: any) => e.col === c.key) }">{{ show(r.raw[c.key]) }}</td>
              <td class="small"><div v-for="(e, i) in r.errors" :key="'e' + i" class="err">{{ e.msg }}</div><div v-for="(w, i) in r.warnings" :key="'w' + i" class="muted">{{ w.msg }}</div></td>
            </tr>
          </tbody>
        </table>
        <el-pagination v-if="pv.total > size" v-model:current-page="page" :page-size="size" :total="pv.total" layout="prev, pager, next, total" small @current-change="validate(page)" />
        <div class="foot">
          <el-button @click="step = 1">{{ $t('common.back') }}</el-button>
          <el-button v-if="pv.summary.error || pv.summary.warning" data-test="imp-errors" @click="openDownload(`/api/impl/import/jobs/${job.id}/errors.xlsx`)"><el-icon><Download /></el-icon>{{ $t('implImport.errorReport') }}</el-button>
          <el-checkbox v-if="pv.summary.error" v-model="skipErrors" data-test="imp-skip-errors">{{ $t('implImport.importOnlyRowsWithoutErrors') }}</el-checkbox>
          <span class="grow" />
          <el-button :loading="busy" :disabled="!!pv.summary.error && !skipErrors" data-test="imp-dry" @click="commit(true)">{{ $t('implImport.dryRun') }}</el-button>
          <el-button type="primary" :loading="busy" :disabled="!!pv.summary.error && !skipErrors" data-test="imp-commit" @click="commit(false)">{{ $t('overviewTab.confirmImport') }}</el-button>
        </div>
      </div>

      <!-- 3 Done -->
      <div v-if="step === 3 && result" class="pane" data-test="imp-result">
        <el-result :icon="result.ok ? 'success' : 'error'" :title="result.ok ? $t('implImport.importComplete') : $t('implImport.importFailedTheWholeBatch')"
          :sub-title="result.ok ? $t('implImport.added0Updated1Skipped', [result.done.create, result.done.update, result.done.skip]) : result.error">
          <template #extra>
            <el-button v-if="!result.ok" @click="step = 2">{{ $t('implImport.backToPreview') }}</el-button>
            <el-button v-if="nextJob" type="primary" data-test="imp-next-job" @click="resume(nextJob)">{{ $t('implImport.next0', [nextJob.importer_label]) }}</el-button>
            <el-button @click="wz = false; tab = 'history'">{{ $t('common.importHistory') }}</el-button>
          </template>
        </el-result>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, reactive, ref } from 'vue';
import { useRoute } from 'vue-router';
import { aiOn } from '../ai';
import { call, get, post, put } from '../api';
import { openDownload, uploadJson } from '../impl/state';

const route = useRoute();
const tab = ref(route.meta.tab === 'history' ? 'history' : 'list');
const list = ref<any[]>([]), jobs = ref<any[]>([]);
const wz = ref(false), step = ref(0), imp = ref<any>(null), job = ref<any>(null), busy = ref(false), aiBusy = ref(false);
const mapping = reactive<Record<string, string>>({}), aiSug = ref<Record<string, string>>({}), aiWhy = ref<Record<string, string>>({});
const mode = ref('upsert'), pv = ref<any>(null), dry = ref<any>(null), result = ref<any>(null), skipErrors = ref(false), only = ref(''), page = ref(1), size = 50;
const batch = ref<any[]>([]);
const drawerSize = innerWidth < 900 ? '100%' : 'min(1180px, 92%)';
const MODES = { upsert: $t('implImport.addOrUpdate'), create: $t('implImport.addOnly'), update: $t('implImport.updateOnly') };
const ACT: Record<string, string> = { create: $t('common.add'), update: $t('implImport.update'), skip: $t('common.skip'), error: $t('common.error') };
const TONE: Record<string, any> = { create: 'ok', update: 'primary', skip: 'neutral', error: 'bad' };
const SUM = [{ k: 'total', l: $t('implImport.validRows'), only: '' }, { k: 'create', l: $t('common.add'), only: 'create' }, { k: 'update', l: $t('implImport.update'), only: 'update' }, { k: 'skip', l: $t('common.skip'), only: 'skip' },
  { k: 'error', l: $t('common.error'), only: 'error' }, { k: 'warning', l: $t('common.hint'), only: 'warning' }];
const JCOLS = [{ key: 'created_at', label: $t('common.time'), width: 150 }, { key: 'importer_label', label: $t('common.import'), width: 140 }, { key: 'filename', label: $t('common.file') },
  { key: 'status', label: $t('common.status'), width: 90 }, { key: 'summary', label: $t('common.result') }, { key: 'created_by', label: $t('common.operator'), width: 90 }, { key: 'op', label: '', width: 110, sortable: false }];

const groups = computed(() => {
  const g: Record<string, any[]> = {};
  for (const i of list.value) (g[i.group] ||= []).push(i);
  return Object.entries(g).map(([name, items]) => ({ name, items }));
});
const labelOf = (k: string) => list.value.find((x) => x.key === k)?.label || k;
const missing = computed(() => (job.value?.columns || []).filter((c: any) => c.req && !Object.values(mapping).includes(c.key)).map((c: any) => c.label));
const usedBy = (key: string, i: number) => Object.entries(mapping).some(([k, v]) => v === key && k !== String(i));
const samples = (i: number) => (job.value?.sample || []).map((r: any[]) => r[i]).filter((v: any) => v !== null && v !== '').slice(0, 3).join($t('common.k362'));
const shownCols = computed(() => (job.value?.columns || []).filter((c: any) => Object.values(mapping).includes(c.key)).slice(0, 8));
const show = (v: any) => (v === null || v === undefined || v === '' ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v));
const sumText = (s: any) => (s ? $t('implImport.add0Update1Skip', [s.done?.create ?? s.create, s.done?.update ?? s.update, s.done?.skip ?? s.skip, s.error ? $t('implImport.n0Errors', [s.error]) : '']) : '');
const nextJob = computed(() => batch.value.find((j) => j.id !== job.value?.id && !j._done));

async function load() { list.value = await get('/api/impl/importers'); jobs.value = await get('/api/impl/import/jobs'); }
function reset() { job.value = null; pv.value = null; dry.value = null; result.value = null; skipErrors.value = false; only.value = ''; page.value = 1; aiSug.value = {}; aiWhy.value = {}; for (const k of Object.keys(mapping)) delete mapping[k]; }
function start(i: any) { reset(); imp.value = i; step.value = 0; wz.value = true; }
function setJob(j: any) { job.value = j; for (const k of Object.keys(mapping)) delete mapping[k]; Object.assign(mapping, j.mapping || {}); mode.value = j.mode || 'upsert'; }
async function upload(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]; if (!f) return;
  busy.value = true;
  try { setJob(await uploadJson(`/api/impl/import/${imp.value.key}/upload`, f)); step.value = 1; } finally { busy.value = false; (e.target as HTMLInputElement).value = ''; }
}
async function uploadWorkbook(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0]; if (!f) return;
  try {
    const r = await uploadJson('/api/impl/import/workbook', f);
    batch.value = r.jobs; ElMessage.success($t('implImport.theWorkbookWasSplitInto', [r.jobs.length]));
    await load(); if (r.jobs.length) resume(r.jobs[0]);
  } finally { (e.target as HTMLInputElement).value = ''; }
}
async function resume(j: any) {
  reset(); const full = await get(`/api/impl/import/jobs/${j.id}`); imp.value = list.value.find((x) => x.key === full.importer) || { key: full.importer, label: full.importer_label, depends: [] };
  setJob(full); step.value = 1; wz.value = true;
}
async function aiMap() {
  aiBusy.value = true;
  try {
    const r = await post(`/api/impl/import/jobs/${job.value.id}/ai_map`, {});
    Object.assign(mapping, r.mapping); aiSug.value = r.suggested || {}; aiWhy.value = r.reasons || {};
    ElMessage.success(Object.keys(r.suggested || {}).length ? $t('implImport.n0Suggested1ColumnsReview', [r.by === 'ai' ? 'AI' : $t('common.rules'), Object.keys(r.suggested).length]) : (r.msg || $t('implImport.noMoreColumnsCanBe')));
  } finally { aiBusy.value = false; }
}
async function validate(p = 1) {
  busy.value = true; page.value = p; dry.value = null;
  try {
    if (step.value === 1) await put(`/api/impl/import/jobs/${job.value.id}`, { mapping: { ...mapping }, mode: mode.value });
    pv.value = await post(`/api/impl/import/jobs/${job.value.id}/validate`, { page: p, size, only: only.value }); step.value = 2;
  } finally { busy.value = false; }
}
function filterBy(o: string) { only.value = only.value === o ? '' : o; validate(1); }
async function commit(dryRun: boolean) {
  busy.value = true;
  try {
    const r = await post(`/api/impl/import/jobs/${job.value.id}/commit`, { dry_run: dryRun, skip_errors: skipErrors.value });
    if (dryRun) { dry.value = r.ok ? r : null; if (!r.ok) { result.value = r; step.value = 3; } return; }
    result.value = r; step.value = 3;
    if (r.ok) { const b = batch.value.find((x) => x.id === job.value.id); if (b) b._done = true; ElMessage.success($t('implImport.importComplete')); }
    load();
  } finally { busy.value = false; }
}
onMounted(async () => {
  await load();
  const q = route.query;
  if (q.job) resume({ id: q.job });
  else if (q.importer) { const i = list.value.find((x) => x.key === q.importer); if (i) start(i); }
});
const _ = call;
</script>

<style scoped>
.grp h4 { margin: 14px 0 8px; font-size: var(--hm-fs-md); color: var(--hm-text-2); }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 10px; }
.ic { background: var(--hm-surface); border: 1px solid var(--hm-border); border-radius: var(--hm-r-lg); padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; }
.ih { display: flex; align-items: center; gap: 6px; justify-content: space-between; } .dsc { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; min-height: 34px; }
.ia { display: flex; gap: 6px; justify-content: flex-end; margin-top: 4px; }
.steps { margin-bottom: 12px; } .pane { display: flex; flex-direction: column; gap: 10px; }
.drop { border: 2px dashed var(--hm-border-strong); border-radius: var(--hm-r-lg); padding: 36px; display: flex; flex-direction: column; align-items: center; gap: 8px; cursor: pointer; color: var(--hm-text-2); }
.drop:hover { border-color: var(--hm-primary); background: var(--hm-primary-soft); }
.bar, .foot { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; } .foot { margin-top: 6px; }
.map td { vertical-align: middle; } .rows td.bad { background: var(--hm-fault-soft); } .rows tr.error td:first-child { box-shadow: inset 3px 0 var(--hm-fault); }
.err { color: var(--hm-fault-ink); }
.kpis { display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; }
.kp { border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); padding: 6px 10px; cursor: pointer; display: flex; flex-direction: column; background: var(--hm-surface); }
.kp small { color: var(--hm-text-3); } .kp b { font-size: 20px; } .kp.on { border-color: var(--hm-primary); box-shadow: 0 0 0 2px var(--hm-primary-soft); }
.kp.error b { color: var(--hm-fault-ink); } .kp.create b { color: var(--hm-run-ink); }
.lnk { color: var(--hm-primary); cursor: pointer; }
@media (max-width: 700px) { .kpis { grid-template-columns: repeat(3, 1fr); } }
</style>
