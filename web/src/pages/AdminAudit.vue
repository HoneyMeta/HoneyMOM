<template>
  <div class="page" data-test="admin-audit">
    <HmPageHead :title="$t('nav.auditTrail')" :sub="$t('adminAudit.whoDidWhatToWhich')">
      <el-button :loading="vbusy" data-test="audit-verify" @click="verify"><el-icon><CircleCheck /></el-icon>{{ $t('adminAudit.verifyHashChain') }}</el-button>
      <el-button v-perm="'system.audit.export'" data-test="audit-export" @click="exportCsv"><el-icon><Download /></el-icon>{{ $t('common.export') }}</el-button>
    </HmPageHead>

    <el-alert v-if="vres" :type="vres.ok ? 'success' : 'error'" :closable="true" show-icon class="mb" data-test="audit-verify-result"
      :title="vres.ok ? $t('adminAudit.hashChainIntact0Entries', [vres.checked]) : $t('adminAudit.problemFoundAtEntry0', [vres.problem.seq, vres.problem.reason])" @close="vres = null" />

    <HmToolbar class="mb">
      <el-date-picker v-model="range" type="datetimerange" size="small" :start-placeholder="$t('common.start')" :end-placeholder="$t('common.end')" value-format="YYYY-MM-DD HH:mm:ss" style="width: 340px" />
      <el-select v-model="f.user" size="small" clearable filterable :placeholder="$t('common.users')" style="width: 140px"><el-option v-for="u in facets.users" :key="u" :value="u" :label="u" /></el-select>
      <el-select v-model="f.entity" size="small" clearable filterable :placeholder="$t('common.object')" style="width: 150px"><el-option v-for="e in facets.entities" :key="e.name" :value="e.name" :label="e.label" /></el-select>
      <el-select v-model="f.action" size="small" clearable filterable :placeholder="$t('common.action')" style="width: 150px"><el-option v-for="a in facets.actions" :key="a" :value="a" :label="actLabel(a)" /></el-select>
      <el-select v-model="f.source" size="small" clearable :placeholder="$t('common.source')" style="width: 110px"><el-option v-for="(l, k) in facets.sources" :key="k" :value="k" :label="l" /></el-select>
      <el-input v-model="f.q" size="small" clearable :placeholder="$t('adminAudit.keywordTargetUser')" style="width: 180px" @keyup.enter="search" />
      <el-button size="small" type="primary" @click="search">{{ $t('common.search') }}</el-button>
    </HmToolbar>

    <HmCard flush>
      <HmDataTable :columns="COLS" :rows="items" row-key="id" clickable :ai="false" :loading="loading" :empty-text="$t('adminAudit.noMatchingRecords')" @row-click="openRow">
        <template #cell-time="{ row }"><span class="hm-mono small">{{ row.time }}</span></template>
        <template #cell-source="{ row }"><HmTag :tone="SRC_TONE[row.source] || 'neutral'">{{ facets.sources?.[row.source] || row.source || '—' }}</HmTag></template>
        <template #cell-action="{ row }">{{ actLabel(row.action) }}</template>
        <template #cell-target="{ row }"><span class="small">{{ targetText(row) }}</span></template>
        <template #cell-diff="{ row }"><span class="small muted ell">{{ diffSummary(row) }}</span></template>
      </HmDataTable>
      <div class="pager"><el-pagination v-model:current-page="page" :page-size="size" :total="total" layout="total, prev, pager, next" small @current-change="load" /></div>
    </HmCard>

    <el-drawer v-model="drw" :title="cur ? `#${cur.seq ?? $t('adminAudit.notSealed')} ${actLabel(cur.action)}` : ''" size="560px" append-to-body>
      <template v-if="cur">
        <dl class="kv">
          <dt>{{ $t('common.time') }}</dt><dd class="hm-mono">{{ cur.time }}</dd><dt>{{ $t('common.users') }}</dt><dd>{{ cur.user }}</dd>
          <dt>{{ $t('common.source') }}</dt><dd>{{ facets.sources?.[cur.source] || cur.source }}<span v-if="cur.ip" class="hm-mono muted"> · {{ cur.ip }}</span></dd>
          <dt>{{ $t('common.object') }}</dt><dd>{{ targetText(cur) }}</dd>
        </dl>
        <h4 v-if="cur.diff && Object.keys(cur.diff).length">{{ $t('adminAudit.changes') }}</h4>
        <table v-if="cur.diff && Object.keys(cur.diff).length" class="dt">
          <thead><tr><th>{{ $t('common.field') }}</th><th>{{ $t('adminAudit.before') }}</th><th>{{ $t('adminAudit.after') }}</th></tr></thead>
          <tbody><tr v-for="(v, k) in cur.diff" :key="k"><td>{{ fieldLabel(cur.entity, k as string) }}</td><td class="old">{{ show(v[0]) }}</td><td class="new">{{ show(v[1]) }}</td></tr></tbody>
        </table>
        <h4 v-if="cur.detail">{{ $t('common.details2') }}</h4>
        <pre v-if="cur.detail" class="js">{{ JSON.stringify(cur.detail, null, 2) }}</pre>
        <p class="small muted hm-mono">{{ $t('adminAudit.hash0', [cur.hash || $t('adminAudit.sealedWithinSeconds')]) }}</p>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { onMounted, reactive, ref } from 'vue';
import { ent, get, tok } from '../api';

const items = ref<any[]>([]), total = ref(0), page = ref(1), size = 50, loading = ref(false);
const f = reactive<any>({ user: '', entity: '', action: '', source: '', q: '' }), range = ref<any>(null);
const facets = ref<any>({ users: [], entities: [], actions: [], sources: {} });
const drw = ref(false), cur = ref<any>(null), vres = ref<any>(null), vbusy = ref(false);
const COLS = [{ key: 'time', label: $t('common.time'), width: 150 }, { key: 'user', label: $t('common.users'), width: 110 }, { key: 'source', label: $t('common.source'), width: 76 },
  { key: 'action', label: $t('common.action'), width: 120 }, { key: 'target', label: $t('common.object') }, { key: 'diff', label: $t('adminAudit.changes') }];
const SRC_TONE: Record<string, any> = { ui: 'primary', api: 'amber', ai: 'ai', agent: 'ai', import: 'warn', collector: 'neutral', system: 'neutral' };
const ACT: Record<string, string> = { create: $t('common.add'), update: $t('common.edit2'), delete: $t('common.delete'), login: $t('adminAudit.signIn'), login_fail: $t('adminAudit.signInFailed'), backup: $t('adminAudit.backup'), backup_drill: $t('adminAudit.restoreDrill'),
  backup_delete: $t('adminAudit.deleteBackup'), backup_download: $t('adminAudit.downloadBackup'), apikey_create: $t('adminApiKeys.newApiKey'), apikey_update: $t('adminAudit.editApiKey'), apikey_revoke: $t('adminAudit.revokeApiKey'),
  collector_batch: $t('adminAudit.collectionRollup'), audit_export: $t('adminAudit.exportAudit'), ai_switches: $t('adminAudit.aiSwitches'), agent_action: $t('adminAudit.agentAction'), ts_retention: $t('adminAudit.timeSeriesRetentionPolicy'), backup_schedule: $t('adminAudit.backupSchedule') };
const actLabel = (a: string) => ACT[a] || a || '—';

function params() {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v) p.set(k, String(v));
  if (range.value) { p.set('start', range.value[0]); p.set('end', range.value[1]); }
  return p;
}
async function load() {
  loading.value = true;
  try { const p = params(); p.set('page', String(page.value)); p.set('size', String(size)); const r = await get('/api/admin/audit?' + p); items.value = r.items; total.value = r.total; }
  finally { loading.value = false; }
}
function search() { page.value = 1; load(); }
async function verify() { vbusy.value = true; try { vres.value = await get('/api/admin/audit/verify'); } finally { vbusy.value = false; } }
function exportCsv() { const p = params(); p.set('token', session.token); p.set('lang', lang); location.href = '/api/admin/audit.csv?' + p; }
function openRow(r: any) { cur.value = r; drw.value = true; }
function targetText(r: any) {
  if (r.entity) { const e = ent(r.entity); return `${e?.label || r.entity} #${r.entity_id ?? ''}`.trim(); }
  return r.target || '—';
}
function fieldLabel(entity: string, k: string) { return ent(entity)?.fields?.find((x: any) => x.name === k)?.label || k; }
const show = (v: any) => (v === null || v === undefined || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v));
function diffSummary(r: any) {
  if (r.diff && Object.keys(r.diff).length) {
    const ks = Object.keys(r.diff);
    if (r.action === 'create') return $t('adminAudit.created0Fields', [ks.length]);
    if (r.action === 'delete') return $t('adminAudit.deleted0FieldsBefore', [ks.length]);
    return ks.slice(0, 3).map((k) => $t('adminAudit.n012', [fieldLabel(r.entity, k), show(r.diff[k][0]), show(r.diff[k][1])])).join($t('common.k764')) + (ks.length > 3 ? $t('adminAudit.and0More', [ks.length]) : '');
  }
  if (r.detail) return Object.entries(r.detail).filter(([, v]) => v !== null && typeof v !== 'object').slice(0, 4).map(([k, v]) => `${k}=${v}`).join(' ');
  return '';
}
onMounted(async () => { facets.value = await get('/api/admin/audit/facets'); load(); });
</script>

<style scoped>
.mb { margin-bottom: 12px; }
.pager { display: flex; justify-content: flex-end; padding: 8px 12px; }
.small { font-size: var(--hm-fs-xs); }
.muted { color: var(--hm-text-3); }
.ell { display: inline-block; max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: bottom; }
.kv { display: grid; grid-template-columns: 56px minmax(0, 1fr); gap: 6px 12px; margin: 0 0 12px; }
.kv dt { color: var(--hm-text-3); font-size: var(--hm-fs-xs); } .kv dd { margin: 0; }
.dt { width: 100%; border-collapse: collapse; font-size: var(--hm-fs-sm); }
.dt th { text-align: left; color: var(--hm-text-3); font-weight: 500; font-size: var(--hm-fs-xs); border-bottom: 1px solid var(--hm-border); padding: 4px 6px; }
.dt td { padding: 4px 6px; border-bottom: 1px solid var(--hm-border); word-break: break-all; }
.dt .old { color: var(--hm-text-3); text-decoration: line-through; } .dt .new { color: var(--hm-text); }
.js { background: var(--hm-bg-2, #f5f7fa); padding: 8px; border-radius: var(--hm-r-sm); font-size: 12px; max-height: 300px; overflow: auto; white-space: pre-wrap; }
h4 { margin: 14px 0 6px; font-size: var(--hm-fs-sm); }
</style>
