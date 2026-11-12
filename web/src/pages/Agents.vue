<template>
  <div class="page">
    <div class="page-head"><h2>{{ $t('common.agents') }}</h2><span class="sub">{{ $t('agents.chainReadDataDecideDraft') }}</span><span class="grow" />
      <el-button @click="importOpen = true" v-if="canEdit"><el-icon><Upload /></el-icon>{{ $t('agents.import') }}</el-button>
      <el-button type="primary" @click="galleryOpen = true" v-if="canEdit" data-test="new-agent"><el-icon><Plus /></el-icon>{{ $t('agents.newAgent') }}</el-button></div>
    <div v-if="loaded && !items.length" class="card empty">
      <h3>{{ $t('agents.noAgentsYet') }}</h3>
      <p class="muted">{{ $t('agents.startingFromATemplateIs') }}</p>
      <el-button type="primary" @click="galleryOpen = true" v-if="canEdit">{{ $t('agents.newFromTemplate') }}</el-button>
    </div>
    <div class="grid g3">
      <div v-for="a in items" :key="a.id" class="card ag click" @click="router.push('/agents/' + a.id)" :data-test="'agent-' + a.id">
        <div class="top">
          <span class="ic"><el-icon size="20"><component :is="a.icon || 'MagicStick'" /></el-icon></span>
          <div class="nm"><b>{{ a.name }}</b><div class="muted small">{{ a.trigger }}</div></div>
          <span class="grow" />
          <el-switch :model-value="a.enabled" @click.stop @change="(v: any) => toggle(a, v)" :disabled="!canEdit || !a.published" size="small"
            :title="a.published ? '' : $t('agents.publishBeforeEnabling')" />
        </div>
        <p class="desc">{{ a.description || $t('agents.noDescription') }}</p>
        <div class="tags">
          <el-tag v-if="a.published" size="small" type="success" effect="plain">{{ $t('agentEditor.publishedV0', [a.published]) }}</el-tag><el-tag v-else size="small" type="info" effect="plain">{{ $t('common.draft') }}</el-tag>
          <el-tag v-if="a.draft_changed" size="small" type="warning" effect="plain">{{ $t('agents.draftHasChanges') }}</el-tag>
          <el-tag v-if="a.chat_callable" size="small" effect="plain">{{ $t('agents.callableFromChat') }}</el-tag>
          <el-tag v-if="a.pending" size="small" type="warning">{{ $t('agents.n0PendingApproval', [a.pending]) }}</el-tag>
        </div>
        <div class="foot muted small">
          <span>{{ $t('agents.lastRun0', [a.last_run_at ? a.last_run_at.slice(5, 16) : '—']) }}</span>
          <el-tag v-if="a.last_status" size="small" :type="statusType(a.last_status)">{{ STATUS_CN[a.last_status] || a.last_status }}</el-tag>
          <span>{{ $t('agents.n0Runs', [a.runs]) }}</span><span class="grow" />
          <el-dropdown @command="(c: string) => onCmd(a, c)" @click.stop trigger="click">
            <el-button link @click.stop><el-icon><MoreFilled /></el-icon></el-button>
            <template #dropdown><el-dropdown-menu>
              <el-dropdown-item command="run" :disabled="!a.published">{{ $t('agents.runPublished') }}</el-dropdown-item>
              <el-dropdown-item command="export">{{ $t('agentEditor.exportJson') }}</el-dropdown-item>
              <el-dropdown-item command="dup" v-if="canEdit">{{ $t('agents.duplicate') }}</el-dropdown-item>
              <el-dropdown-item command="del" v-if="canEdit" divided>{{ $t('common.delete') }}</el-dropdown-item>
            </el-dropdown-menu></template>
          </el-dropdown>
        </div>
      </div>
    </div>

    <el-dialog v-model="galleryOpen" :title="$t('agents.newAgent2')" width="min(980px, 96vw)" top="6vh">
      <div class="gal">
        <div class="tpl blank" @click="create(null)"><el-icon size="26"><Plus /></el-icon><b>{{ $t('agents.blankCanvas') }}</b><span class="muted small">{{ $t('agents.buildItYourselfFromThe') }}</span></div>
        <div v-for="t in templates" :key="t.key" class="tpl" @click="create(t.key)" :data-test="'tpl-' + t.key">
          <div class="th"><el-icon size="20" color="#d99a16"><component :is="t.icon" /></el-icon><b>{{ t.name }}</b></div>
          <el-tag size="small" effect="plain">{{ t.trigger_label }}</el-tag>
          <p class="small">{{ t.description }}</p>
          <div class="muted small">{{ $t('agents.writeActions0', [(t.permissions || []).map((p: string) => ops[p]?.label || p).join($t('common.k42')) || $t('agents.noneReadOnly')]) }}</div>
        </div>
      </div>
    </el-dialog>
    <el-dialog v-model="importOpen" :title="$t('agents.importAgentJson')" width="min(560px, 96vw)">
      <input type="file" accept=".json,application/json" @change="onFile" />
      <el-input v-model="importText" type="textarea" :rows="10" :placeholder="$t('agents.orPasteExportedJsonHere')" style="margin-top:8px" class="mono" />
      <template #footer><el-button @click="importOpen = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" @click="doImport">{{ $t('common.import') }}</el-button></template>
    </el-dialog>
    <el-dialog v-model="runOpen" :title="$t('agents.run') + (runAgent?.name || '')" width="min(520px, 96vw)">
      <p class="small muted">{{ $t('agents.runThePublishedVersionOnce') }}</p>
      <el-input v-model="runQ" type="textarea" :rows="3" :placeholder="$t('agents.inputTheQuestionForChat')" />
      <template #footer><el-button @click="runOpen = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" @click="doRun">{{ $t('common.running') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { del, get, post, STATUS_CN, statusType } from '../api';

const router = useRouter();
const items = ref<any[]>([]), canEdit = ref(false), loaded = ref(false), templates = ref<any[]>([]), ops = ref<any>({});
const galleryOpen = ref(false), importOpen = ref(false), importText = ref(''), runOpen = ref(false), runAgent = ref<any>(null), runQ = ref('');
async function load() {
  const r = await get('/api/agents');
  items.value = r.items; canEdit.value = r.can_edit; loaded.value = true;
}
onMounted(async () => {
  await load();
  const m = await get('/api/agents/meta');
  templates.value = m.templates; ops.value = m.ops;
});
async function create(tpl: string | null) {
  const r = await post('/api/agents', tpl ? { template: tpl } : { name: $t('agents.newAgent3') });
  galleryOpen.value = false;
  router.push('/agents/' + r.id);
}
async function toggle(a: any, v: any) {
  await post(`/api/agents/${a.id}/enable`, { enabled: v });
  a.enabled = v; ElMessage.success(v ? $t('agents.enabled0', [a.name]) : $t('agents.disabled0', [a.name]));
}
async function onCmd(a: any, c: string) {
  if (c === 'run') { runAgent.value = a; runQ.value = ''; runOpen.value = true; }
  if (c === 'export') {
    const j = await get(`/api/agents/${a.id}/export`);
    const el = document.createElement('a');
    el.href = URL.createObjectURL(new Blob([JSON.stringify(j, null, 2)], { type: 'application/json' }));
    el.download = `agent-${a.id}.json`; el.click();
  }
  if (c === 'dup') { const j = await get(`/api/agents/${a.id}/export`); const r = await post('/api/agents/import', j); ElMessage.success($t('common.copied')); router.push('/agents/' + r.id); }
  if (c === 'del') { await ElMessageBox.confirm($t('agents.delete0RunHistoryIs', [a.name]), $t('common.delete')); await del(`/api/agents/${a.id}`); load(); }
}
async function doRun() {
  const r = await post(`/api/agents/${runAgent.value.id}/run`, { use: 'published', mode: 'live', trigger: 'manual', inputs: runQ.value ? { query: runQ.value } : {} });
  runOpen.value = false;
  router.push(`/agents/${runAgent.value.id}?run=${r.run_id}`);
}
function onFile(e: any) {
  const f = e.target.files?.[0];
  if (!f) return;
  const rd = new FileReader();
  rd.onload = () => { importText.value = String(rd.result || ''); };
  rd.readAsText(f, 'utf-8');
}
async function doImport() {
  let j: any;
  try { j = JSON.parse(importText.value); } catch { ElMessage.error($t('agents.notValidJson')); return; }
  const r = await post('/api/agents/import', j);
  importOpen.value = false; ElMessage.success($t('agents.importedDraftNotEnabled'));
  router.push('/agents/' + r.id);
}
</script>

<style scoped>
.ag { display: flex; flex-direction: column; gap: 8px; transition: box-shadow .15s, border-color .15s; } .ag:hover { border-color: #e6b75a; box-shadow: 0 4px 14px rgba(0, 0, 0, .06); }
.top { display: flex; align-items: center; gap: 10px; } .grow { flex: 1; }
.ic { width: 36px; height: 36px; border-radius: 9px; background: #fbf4e6; color: #d99a16; display: inline-flex; align-items: center; justify-content: center; flex: none; }
.nm b { font-size: 15px; } .desc { margin: 0; font-size: 13px; color: #4b5560; line-height: 1.55; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; min-height: 40px; }
.tags { display: flex; gap: 5px; flex-wrap: wrap; } .foot { display: flex; align-items: center; gap: 8px; border-top: 1px solid #eef1f3; padding-top: 7px; }
.empty { margin-bottom: 14px; } .empty h3 { margin-top: 0; }
.gal { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 800px) { .gal { grid-template-columns: 1fr; } }
.tpl { border: 1px solid #e6eaee; border-radius: 10px; padding: 12px; cursor: pointer; display: flex; flex-direction: column; gap: 6px; align-items: flex-start; }
.tpl:hover { border-color: #e6b75a; background: #fffaf0; } .tpl p { margin: 0; line-height: 1.55; color: #3a434c; }
.th { display: flex; align-items: center; gap: 6px; } .th b { font-size: 14.5px; }
.tpl.blank { align-items: center; justify-content: center; color: #8a5a07; border-style: dashed; min-height: 150px; }
.mono :deep(textarea) { font-family: Consolas, monospace; font-size: 12px; }
</style>
