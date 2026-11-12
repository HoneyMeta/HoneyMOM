<template>
  <div class="ov" data-test="onboard-overview">
    <div class="kpis" data-tour="onboard-kpis">
      <HmKpi :label="$t('overviewTab.tags')" :value="s?.points" :unit="$t('common.k550')" :loading="!s" :ai="false" />
      <HmKpi :label="$t('common.confirmed')" :value="s?.confirmed" :unit="$t('common.k550')" :loading="!s" :ai="false" />
      <HmKpi :label="$t('common.coverage')" :value="s ? s.coverage : null" format="pct" :status="s && s.coverage >= 0.999 ? 'ok' : s && s.coverage > 0.6 ? 'warn' : 'bad'" :loading="!s" :ai="false" to="/onboard/check" />
      <HmKpi :label="$t('overviewTab.addressesToConfirmNotIn')" :value="s?.pending_seen" :unit="$t('common.k550')" :status="s?.pending_seen ? 'warn' : 'ok'" :loading="!s" :ai="false" to="/onboard/check" />
      <HmKpi :label="$t('overviewTab.equipmentPlaced')" :value="s ? `${s.devices} / ${s.placed}` : null" :loading="!s" :ai="false" to="/onboard/twin" />
    </div>

    <div class="grid2">
      <HmCard :title="$t('overviewTab.importDeliveryPackage')" data-tour="onboard-package">
        <p class="note">{{ $t('overviewTab.theIntegratorSDeliveryPackage') }}<b>points.xlsx</b>{{ $t('overviewTab.equipmentTagsProtocols') }}<b>layout.dxf</b>{{ $t('overviewTab.floorPlan') }}<b>models/*.glb</b>{{ $t('overviewTab.n3dSeeTheConnectivitySpecification') }}</p>
        <div class="row">
          <el-button v-perm="'onboard.import.run'" type="primary" :loading="busy === 'zip'" data-test="pkg-upload" @click="uploadZip">{{ $t('overviewTab.uploadPackageZip') }}</el-button>
          <span class="muted small">{{ $t('overviewTab.orFetchFromUrl') }}</span>
          <el-input v-model="url" size="small" :placeholder="$t('overviewTab.eGHttpVirtualFactory')" class="url" data-test="pkg-url" />
          <el-input v-model="token" size="small" :placeholder="$t('overviewTab.tokenOptional')" class="tok" />
          <el-button v-perm="'onboard.import.run'" :loading="busy === 'url'" :disabled="!url" data-test="pkg-fetch" @click="fetchUrl">{{ $t('overviewTab.fetchPackage') }}</el-button>
        </div>
      </HmCard>
      <HmCard :title="$t('overviewTab.importTagListExcel')">
        <p class="note">{{ $t('overviewTab.useThisToUpdateThe', [s?.ai ? $t('overviewTab.andAi') : '']) }}</p>
        <div class="row">
          <el-button v-perm="'onboard.import.run'" :loading="busy === 'xlsx'" data-test="pt-upload" @click="uploadXlsx">{{ $t('overviewTab.uploadTagListXlsx') }}</el-button>
          <el-button link type="primary" data-test="pt-template" @click="dl('/api/onboard/points/template.xlsx', $t('overviewTab.tagListTemplateXlsx'))">{{ $t('overviewTab.downloadTagListTemplate') }}</el-button>
        </div>
      </HmCard>
    </div>

    <HmCard :title="$t('common.dataSource')" flush data-tour="onboard-sources">
      <HmDataTable :columns="SRC_COLS" :rows="s?.sources || []" row-key="id" :ai="false" :loading="!s" :empty-text="$t('overviewTab.noDataSourcesYet')">
        <template #cell-protocol="{ row }">{{ PROTO_CN[row.protocol] || row.protocol }}</template>
        <template #cell-endpoint="{ row }"><span class="hm-mono small">{{ row.endpoint || '—' }}</span><span v-if="row.prefix" class="hm-mono small muted"> · {{ row.prefix }}</span></template>
        <template #cell-status="{ row }"><HmTag :tone="/connected|push|\u5df2\u8fde\u63a5|\u63a8\u9001/i.test(row.status || '') ? 'ok' : row.enabled ? 'bad' : 'neutral'">{{ row.enabled ? (row.status || $t('common.notConnected')) : $t('overviewTab.disabled') }}</HmTag></template>
        <template #cell-values="{ row }"><span class="hm-num">{{ row.values }}</span><span v-if="row.last_seen_s !== null" class="muted small"> {{ $t('overviewTab.n0SAgo', [row.last_seen_s]) }}</span></template>
        <template #cell-op="{ row }">
          <el-button v-if="['mqtt', 'sparkplug', 'sql', 'http'].includes(row.protocol)" v-perm="'onboard.discover.run'" link size="small" type="primary" @click="discover(row)">
            {{ row.discovering ? $t('overviewTab.discovering0S', [row.discovering]) : $t('common.discover') }}</el-button>
          <el-button v-if="row.protocol === 'opcua'" link size="small" type="primary" @click="router.push('/onboard/discover?ds=' + row.id)">{{ $t('overviewTab.browse') }}</el-button>
          <el-button link size="small" @click="router.push('/onboard/check?ds=' + row.id)">{{ $t('overviewTab.check') }}</el-button>
          <el-button link size="small" @click="router.push('/e/datasource?id=' + row.id)">{{ $t('overviewTab.settings') }}</el-button>
        </template>
      </HmDataTable>
    </HmCard>

    <div class="grid2">
      <HmCard :title="$t('common.importHistory')" flush>
        <HmDataTable :columns="IMP_COLS" :rows="s?.imports || []" row-key="id" :ai="false" :empty-text="$t('overviewTab.nothingImportedYet')">
          <template #cell-kind="{ row }">{{ row.kind === 'package' ? $t('overviewTab.deliveryPackage') : $t('overviewTab.tagList') }}</template>
          <template #cell-status="{ row }"><HmTag :tone="row.status === 'confirmed' ? 'ok' : row.status === 'preview' ? 'warn' : 'neutral'">{{ IMP_ST[row.status] || row.status }}</HmTag></template>
          <template #cell-res="{ row }"><span class="small">{{ row.result ? $t('overviewTab.newEquipment0NewTags', [row.result.devices_new, row.result.points_new, row.result.points_updated]) : $t('modelTab.n0Machines1Tags', [row.devices ?? 0, row.points ?? 0]) }}</span></template>
          <template #cell-op="{ row }"><el-button v-if="row.status === 'preview'" link size="small" type="primary" @click="openPreview(row.id)">{{ $t('overviewTab.continue') }}</el-button></template>
        </HmDataTable>
      </HmCard>
      <HmCard :title="$t('overviewTab.commissioningAcceptanceReport')">
        <p class="note">{{ $t('overviewTab.generatedFromTheCurrentTag') }}</p>
        <div class="row">
          <el-button v-perm="'onboard.report.create'" type="primary" :loading="busy === 'docx'" data-test="report-docx" @click="report('docx')">{{ $t('overviewTab.generateWord') }}</el-button>
          <el-button v-perm="'onboard.report.create'" :loading="busy === 'pdf'" data-test="report-pdf" @click="report('pdf')">{{ $t('overviewTab.generatePdf') }}</el-button>
        </div>
        <ul class="reps"><li v-for="r in s?.reports || []" :key="r.id"><span class="hm-mono small">{{ r.created_at }}</span> {{ $t('overviewTab.coverage01', [((r.coverage || 0) * 100).toFixed(1), r.created_by]) }}</li></ul>
      </HmCard>
    </div>

    <el-dialog v-model="pdlg" :title="pv?.kind === 'package' ? $t('overviewTab.deliveryPackagePreview') : $t('overviewTab.tagListPreview')" width="min(1080px, 94vw)" append-to-body data-test="import-preview">
      <template v-if="pv">
        <div class="pvsum">
          <span v-if="pv.summary.plant">{{ $t('overviewTab.plant') }}<b>{{ pv.summary.plant }}</b></span>
          <span>{{ $t('common.equipment') }} <b>{{ pv.summary.devices }}</b> {{ $t('overviewTab.machines') }}<template v-if="pv.summary.devices_new !== undefined">{{ $t('overviewTab.n0New', [pv.summary.devices_new]) }}</template></span>
          <span>{{ $t('common.tag') }} <b>{{ pv.summary.points }}</b> {{ $t('common.k550') }}<template v-if="pv.summary.points_new !== undefined">{{ $t('overviewTab.n0New', [pv.summary.points_new]) }}</template></span>
          <span v-for="(n, p) in pv.summary.by_protocol" :key="p">{{ PROTO_CN[p] || p }} {{ n }}</span>
          <HmTag :tone="pv.summary.errors ? 'bad' : 'ok'">{{ $t('overviewTab.errors0', [pv.summary.errors]) }}</HmTag><HmTag :tone="pv.summary.warnings ? 'warn' : 'ok'">{{ $t('overviewTab.warnings0', [pv.summary.warnings]) }}</HmTag>
          <HmTag v-if="pv.summary.raw_site" tone="ai">{{ $t('overviewTab.rawSiteMostTagsHave') }}</HmTag>
        </div>
        <div v-if="pv.summary.datasources" class="ds">
          <div v-for="(d, p) in pv.summary.datasources" :key="p" class="dsr">
            <b>{{ PROTO_CN[p] || p }}</b> {{ $t('overviewTab.dataSource') }} <span class="hm-mono">{{ d.code }}</span>{{ d.exists ? $t('overviewTab.existing') : $t('overviewTab.new') }}
            <el-input v-model="eps[p]" size="small" class="ep" :placeholder="d.endpoint_package || $t('overviewTab.endpoint')" />
            <span v-if="d.exists && d.endpoint_current" class="muted small">{{ $t('overviewTab.current0', [d.endpoint_current]) }}</span>
          </div>
        </div>
        <div v-if="pv.summary.ai_mapping?.mapping && Object.keys(pv.summary.ai_mapping.mapping).length" class="map small">
          {{ $t('overviewTab.columnMapping0', [pv.summary.ai_mapping.by === 'llm' ? 'AI' : $t('overviewTab.synonyms')]) }}<span v-for="(m, k) in pv.summary.ai_mapping.mapping" :key="k"><template v-for="(v, h) in m" :key="h">{{ $t('overviewTab.n01', [h, v]) }}</template></span>
        </div>
        <div v-if="pv.summary.unmapped?.point?.length" class="small warnc">{{ $t('overviewTab.unmatchedColumnsIgnored0', [pv.summary.unmapped.point.join($t('common.k42'))]) }}</div>
        <HmDataTable :columns="ISS_COLS" :rows="pv.issues" :ai="false" :max-height="300" :empty-text="$t('overviewTab.noIssues')">
          <template #cell-level="{ row }"><HmTag :tone="row.level === 'error' ? 'bad' : 'warn'">{{ row.level === 'error' ? $t('common.error') : $t('overviewTab.warning') }}</HmTag></template>
        </HmDataTable>
        <p class="small muted">{{ $t('overviewTab.rowsWithErrorsAreNot') }}</p>
      </template>
      <template #footer>
        <el-button @click="cancelPreview">{{ $t('overviewTab.discard') }}</el-button>
        <el-button v-perm="'onboard.import.run'" type="primary" :loading="busy === 'confirm'" data-test="import-confirm" @click="confirmPreview">{{ $t('overviewTab.confirmImport') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
import { ElMessage } from 'element-plus';
import { onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { get, post, session } from '../../api';
import { PROTO_CN, download, fileB64, pickFile } from './common';

const router = useRouter();
const s = ref<any>(null), busy = ref(''), url = ref(''), token = ref(''), pdlg = ref(false), pv = ref<any>(null), eps = ref<Record<string, string>>({});
const IMP_ST: Record<string, string> = { preview: $t('common.toConfirm'), confirmed: $t('overviewTab.imported'), cancelled: $t('overviewTab.discarded') };
const SRC_COLS = [{ key: 'code', label: $t('common.code'), width: 90 }, { key: 'protocol', label: $t('common.protocol'), width: 100 }, { key: 'endpoint', label: $t('common.address') }, { key: 'status', label: $t('common.status'), width: 190 },
  { key: 'values', label: $t('overviewTab.valuesReceived'), width: 140, sortable: false }, { key: 'op', label: '', width: 200, sortable: false }];
const IMP_COLS = [{ key: 'created_at', label: $t('common.time'), width: 140 }, { key: 'kind', label: $t('common.type'), width: 64 }, { key: 'file_name', label: $t('common.file') }, { key: 'status', label: $t('common.status'), width: 76 },
  { key: 'res', label: $t('common.result'), sortable: false }, { key: 'op', label: '', width: 56, sortable: false }];
const ISS_COLS = [{ key: 'level', label: $t('overviewTab.level'), width: 64 }, { key: 'sheet', label: $t('overviewTab.sheet'), width: 56 }, { key: 'row', label: $t('common.row'), num: true, width: 56 }, { key: 'col', label: $t('overviewTab.column'), width: 96 }, { key: 'msg', label: $t('common.issue') }];
let timer: any = null;

async function load() { s.value = await get('/api/onboard/summary', true).catch(() => s.value); }
async function showPreview(r: any, kind: string) {
  pv.value = { ...r, kind }; eps.value = {};
  for (const [p, d] of Object.entries<any>(r.summary.datasources || {})) if (d && !Array.isArray(d)) eps.value[p] = d.endpoint || d.endpoint_package || '';
  pdlg.value = true;
}
async function uploadZip() {
  const f = await pickFile('.zip'); if (!f) return;
  busy.value = 'zip';
  try { showPreview(await post('/api/onboard/package', { name: f.name, data: await fileB64(f) }), 'package'); } finally { busy.value = ''; }
}
async function fetchUrl() {
  busy.value = 'url';
  try { showPreview(await post('/api/onboard/package', { url: url.value.trim(), token: token.value.trim() }), 'package'); } finally { busy.value = ''; }
}
async function uploadXlsx() {
  const f = await pickFile('.xlsx'); if (!f) return;
  busy.value = 'xlsx';
  try { showPreview(await post('/api/onboard/points/import', { name: f.name, data: await fileB64(f) }), 'points'); } finally { busy.value = ''; }
}
async function openPreview(id: number) { const r = await get('/api/onboard/imports/' + id); showPreview(r, r.kind); }
async function confirmPreview() {
  busy.value = 'confirm';
  try {
    const endpoints: Record<string, string> = {};
    for (const [p, v] of Object.entries(eps.value)) if (v) endpoints[p] = v;
    const r = await post(`/api/onboard/imports/${pv.value.id}/confirm`, { endpoints, use_package_endpoints: true });
    const x = r.result;
    ElMessage.success($t('overviewTab.imported0NewMachines1', [x.devices_new, x.points_new, x.points_updated, x.version ? $t('overviewTab.modelVersionV0', [x.version]) : '']));
    pdlg.value = false; await load();
  } finally { busy.value = ''; }
}
async function cancelPreview() { if (pv.value) await post(`/api/onboard/imports/${pv.value.id}/cancel`, {}); pdlg.value = false; load(); }
async function discover(row: any) { await post('/api/onboard/discover', { datasource: row.id, minutes: 5 }); ElMessage.success($t('overviewTab.discoveryStartedTopicsReceivedIn')); load(); }
async function dl(u: string, name: string) { try { await download(u, name, session.token); } catch (e: any) { ElMessage.error(e.message); } }
async function report(fmt: string) { busy.value = fmt; try { await dl(`/api/onboard/report.${fmt}`, $t('overviewTab.commissioningAcceptanceReport0', [fmt])); await load(); } finally { busy.value = ''; } }
onMounted(() => { load(); timer = setInterval(() => { if (!document.hidden) load(); }, 5000); });
onUnmounted(() => clearInterval(timer));
</script>

<style scoped>
.ov { display: grid; gap: 16px; }
.kpis { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; }
.grid2 { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 1279px) { .kpis { grid-template-columns: repeat(3, minmax(0, 1fr)); } .grid2 { grid-template-columns: minmax(0, 1fr); } }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.url { width: 300px; } .tok { width: 130px; }
.note { margin: 0 0 10px; color: var(--hm-text-2); font-size: var(--hm-fs-sm); line-height: 1.6; }
.small { font-size: var(--hm-fs-xs); } .muted { color: var(--hm-text-3); } .warnc { color: var(--hm-idle-ink); margin: 6px 0; }
.reps { margin: 10px 0 0; padding-left: 18px; color: var(--hm-text-2); font-size: var(--hm-fs-sm); }
.pvsum { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; margin-bottom: 10px; font-size: var(--hm-fs-sm); }
.ds { display: grid; gap: 6px; margin-bottom: 10px; font-size: var(--hm-fs-sm); }
.dsr { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; } .ep { width: 300px; }
.map { margin-bottom: 6px; color: var(--hm-text-2); }
</style>
