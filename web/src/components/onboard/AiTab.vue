<template>
  <div class="ai" data-test="onboard-ai">
    <div v-if="!aiOn" class="off" data-test="ai-off">{{ $t('aiTab.theAiFeatureConnectivityAssistant') }}</div>
    <div class="grid">
      <HmCard :title="$t('aiTab.aiCompleteTags')" data-tour="ai-draft">
        <p class="note">{{ $t('aiTab.forTagsToConfirmTypical') }}</p>
        <div class="row">
          <el-select v-model="ds" size="small" clearable :placeholder="$t('aiTab.allDataSources')" class="sel"><el-option v-for="d in sources" :key="d.id" :label="d.code" :value="d.id" /></el-select>
          <el-button v-perm="'onboard.ai.run'" type="primary" size="small" :disabled="!aiOn" :loading="busy === 'draft'" data-test="ai-draft" @click="draft">{{ $t('common.draft2') }}</el-button>
        </div>
      </HmCard>
      <HmCard :title="$t('aiTab.readPlcProgram')" data-tour="ai-plc">
        <p class="note">{{ $t('aiTab.supportedVirtualFactorySoftPlc') }}</p>
        <div class="row">
          <el-select v-model="plcDev" size="small" filterable clearable :placeholder="$t('aiTab.equipment')" class="sel"><el-option v-for="d in devices" :key="d.code" :label="`${d.code} ${d.name}`" :value="d.code" /></el-select>
          <el-select v-model="plcDs" size="small" :placeholder="$t('aiTab.attachToDataSource')" class="sel2"><el-option v-for="d in sources" :key="d.id" :label="d.code" :value="d.id" /></el-select>
          <el-button v-perm="'onboard.ai.run'" size="small" :disabled="!aiOn" :loading="busy === 'plc'" data-test="ai-plc" @click="readPlc">{{ $t('aiTab.uploadProgramFile') }}</el-button>
        </div>
        <div v-if="plcRes" class="small res" data-test="plc-result">{{ $t('aiTab.n01Symbols2Tags', [plcRes.kind, plcRes.symbols, plcRes.proposals.length, plcRes.draft ? $t('aiTab.draft') + plcRes.draft + $t('aiTab.pendingApprovalBelow') : '']) }}</div>
      </HmCard>
    </div>

    <HmCard :title="$t('aiTab.connectivityDraftsPendingApproval0', [drafts.length])" data-tour="ai-drafts">
      <div v-if="!drafts.length" class="muted small">{{ $t('aiTab.noDraftsPendingApproval') }}</div>
      <div v-for="d in drafts" :key="d.id" class="dr" :data-draft="d.id" data-test="onboard-draft">
        <div class="dh"><HmTag tone="ai">{{ $t('aiTab.aiDraft') }}</HmTag><b>{{ d.label }}</b><span class="small muted">{{ d.summary }}</span><span class="grow" />
          <el-button v-perm="'onboard.point.confirm'" size="small" type="primary" :data-test="'draft-approve-' + d.id" @click="decide(d, 'approve')">{{ edited[d.id] ? $t('draftCard.approveWithEdits2') : $t('common.approve') }}</el-button>
          <el-button v-perm="'onboard.point.confirm'" size="small" @click="decide(d, 'reject')">{{ $t('common.reject') }}</el-button>
        </div>
        <el-table :data="d.payload.points" size="small" max-height="320">
          <el-table-column :label="d.op === 'onboard_plc' ? $t('aiTab.symbol') : $t('common.tag')" min-width="150" show-overflow-tooltip><template #default="{ row }"><span class="hm-mono">{{ row.code || row.symbol }}</span></template></el-table-column>
          <el-table-column v-if="d.op === 'onboard_points'" :label="$t('common.equipment')" width="120"><template #default="{ row }"><el-select v-model="row.device" size="small" filterable clearable @change="edited[d.id] = true"><el-option v-for="x in devices" :key="x.code" :label="x.code" :value="x.code" /></el-select></template></el-table-column>
          <el-table-column v-if="d.op === 'onboard_plc'" :label="$t('common.address')" width="130"><template #default="{ row }"><el-input v-model="row.address" size="small" @change="edited[d.id] = true" /></template></el-table-column>
          <el-table-column :label="$t('common.meaning')" min-width="160"><template #default="{ row }"><el-input v-model="row.meaning" size="small" @change="edited[d.id] = true" /></template></el-table-column>
          <el-table-column v-if="d.op === 'onboard_points'" :label="$t('common.unit')" width="80"><template #default="{ row }"><el-input v-model="row.unit" size="small" @change="edited[d.id] = true" /></template></el-table-column>
          <el-table-column :label="$t('common.purpose')" width="110"><template #default="{ row }"><el-select v-model="row.role" size="small" @change="edited[d.id] = true"><el-option v-for="r in ROLES" :key="r" :label="ROLE_CN[r]" :value="r" /></el-select></template></el-table-column>
          <el-table-column v-if="d.op === 'onboard_points'" :label="$t('aiTab.confidence')" width="70" align="right"><template #default="{ row }"><span class="hm-num" :class="{ low: (row.conf || 0) < 0.5 }">{{ ((row.conf || 0) * 100).toFixed(0) }}%</span></template></el-table-column>
          <el-table-column :label="$t('aiTab.evidence')" min-width="160" show-overflow-tooltip><template #default="{ row }"><span class="small muted">{{ row.why }}</span></template></el-table-column>
        </el-table>
      </div>
    </HmCard>

    <HmCard :title="$t('aiTab.suspiciousTags0', [sus.length])" flush data-tour="ai-suspicious">
      <HmDataTable :columns="S_COLS" :rows="sus" row-key="id" :ai="false" :max-height="360" :empty-text="$t('aiTab.noSuspiciousTagsFound')" data-test="suspicious-table">
        <template #cell-role="{ row }">{{ ROLE_CN[row.role] || row.role || '—' }}</template>
        <template #cell-why="{ row }"><span class="small warnc">{{ row.why.join($t('common.k764')) }}</span></template>
      </HmDataTable>
    </HmCard>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
import { ElMessage } from 'element-plus';
import { onMounted, ref } from 'vue';
import { get, post } from '../../api';
import { ROLE_CN, ROLES, fileB64, pickFile } from './common';

const aiOn = ref(false), sources = ref<any[]>([]), devices = ref<any[]>([]), ds = ref<number | null>(null), busy = ref('');
const plcDev = ref(''), plcDs = ref<number | null>(null), plcRes = ref<any>(null), drafts = ref<any[]>([]), sus = ref<any[]>([]), edited = ref<Record<number, boolean>>({});
const S_COLS = [{ key: 'code', label: $t('common.tagCode') }, { key: 'device', label: $t('common.equipment'), width: 80 }, { key: 'meaning', label: $t('common.meaning') }, { key: 'role', label: $t('common.purpose'), width: 80 },
  { key: 'value', label: $t('aiTab.lastValue'), width: 110 }, { key: 'why', label: $t('aiTab.whySuspicious'), sortable: false }];

async function load() {
  const s = await get('/api/onboard/summary', true).catch(() => null);
  aiOn.value = !!s?.ai; sources.value = s?.sources || [];
  if (!plcDs.value) plcDs.value = sources.value.find((x) => x.protocol === 'opcua')?.id || sources.value[0]?.id || null;
  devices.value = (await get('/api/factory/devices', true).catch(() => ({ items: [] }))).items;
  drafts.value = (await get('/api/onboard/ai/drafts', true).catch(() => ({ items: [] }))).items;
  edited.value = {};
  sus.value = (await get('/api/onboard/ai/suspicious', true).catch(() => ({ items: [] }))).items;
}
async function draft() {
  busy.value = 'draft';
  try {
    const r = await post('/api/onboard/ai/draft', { datasource: ds.value });
    ElMessage.success(r.drafts.length ? $t('aiTab.created0DraftsCovering1', [r.drafts.length, r.points, r.by === 'llm' ? $t('aiTab.rulesLlm') : $t('common.rules')]) : r.msg || $t('aiTab.noTagsToComplete'));
    await load();
  } finally { busy.value = ''; }
}
async function readPlc() {
  const f = await pickFile('.json,.xml,.csv,.st,.txt'); if (!f) return;
  busy.value = 'plc';
  try { plcRes.value = await post('/api/onboard/ai/plc', { name: f.name, data: await fileB64(f), device: plcDev.value || null, datasource: plcDs.value }); await load(); } finally { busy.value = ''; }
}
async function decide(d: any, decision: string) {
  const r = await post(`/api/onboard/ai/drafts/${d.id}/decide`, { decision, payload: decision === 'approve' && edited.value[d.id] ? d.payload : undefined });
  ElMessage.success(decision === 'approve' ? (r.receipt?.text || $t('common.approved')) : $t('common.rejected'));
  await load();
}
onMounted(load);
</script>

<style scoped>
.ai { display: grid; gap: 16px; }
.off { padding: 10px 14px; border-radius: 8px; background: var(--hm-surface-3); color: var(--hm-text); font-size: var(--hm-fs-sm); border-left: 3px solid var(--hm-primary); }
.grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 1279px) { .grid { grid-template-columns: minmax(0, 1fr); } }
.note { margin: 0 0 10px; color: var(--hm-text-2); font-size: var(--hm-fs-sm); line-height: 1.6; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.sel { width: 220px; } .sel2 { width: 130px; }
.res { margin-top: 8px; color: var(--hm-text-2); }
.dr { border: 1px solid var(--hm-hairline); border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; }
.dh { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; } .grow { flex: 1; }
.low { color: #b4530f; } .warnc { color: #b4530f; }
.small { font-size: var(--hm-fs-xs); } .muted { color: var(--hm-text-3); }
</style>
