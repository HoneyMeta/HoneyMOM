<template>
  <div class="page mcp">
    <HmPageHead :title="$t('common.mcpAccess')" :sub="$t('mcpSetup.letYourOwnAiTools')">
      <HmTag :tone="info?.enabled ? 'ok' : 'warn'" data-test="mcp-status">{{ info?.enabled ? $t('mcpSetup.mcpEnabled') : $t('mcpSetup.mcpDisabled') }}</HmTag>
      <el-button v-if="!info?.enabled" size="small" @click="router.push('/system/ai')">{{ $t('mcpSetup.enableUnderAiFeatures') }}</el-button>
    </HmPageHead>

    <div class="grid g2">
      <HmCard :title="$t('mcpSetup.n1CreateAKey')" :sub="$t('mcpSetup.thePlainTextKeyIs')" data-tour="mcp-key">
        <el-form label-width="96px" size="small">
          <el-form-item :label="$t('common.name')"><el-input v-model="kf.name" :placeholder="$t('mcpSetup.eGQualityTeamClaude')" data-test="mcp-key-name" /></el-form-item>
          <el-form-item :label="$t('common.permissions')"><el-radio-group v-model="kf.preset" data-test="mcp-preset">
            <el-radio-button v-for="(p, k) in info?.presets || {}" :key="k" :value="k">{{ p.label }}</el-radio-button></el-radio-group>
            <div class="muted small">{{ (info?.presets?.[kf.preset]?.perms || []).join('  ') }}</div></el-form-item>
          <el-form-item :label="$t('common.dataScope')"><el-select v-model="kf.lines" multiple clearable :placeholder="$t('common.allLines')" style="width: 100%"><el-option v-for="l in lines" :key="l.id" :value="l.id" :label="l.name" /></el-select></el-form-item>
          <el-form-item :label="$t('common.expires')"><el-date-picker v-model="kf.expires_at" type="date" value-format="YYYY-MM-DD" :placeholder="$t('adminApiKeys.neverExpires')" /></el-form-item>
          <el-form-item :label="$t('adminApiKeys.ipAllowlist')"><el-input v-model="kf.ip_allow" :placeholder="$t('mcpSetup.optionalEG192168')" /></el-form-item>
          <el-form-item><el-button v-perm:disable="'system.apikey.manage'" type="primary" :loading="busy" data-test="mcp-key-create" @click="createKey">{{ $t('mcpSetup.generateKey') }}</el-button></el-form-item>
        </el-form>
        <div v-if="newKey" class="newkey" data-test="mcp-newkey"><b>{{ $t('mcpSetup.newKeyShownOnlyThis') }}</b><code>{{ newKey }}</code><el-button size="small" link type="primary" @click="copy(newKey)">{{ $t('common.copy') }}</el-button></div>
      </HmCard>

      <HmCard :title="$t('mcpSetup.n2ConnectYourAiTool')" :sub="info ? $t('mcpSetup.url0StreamableHttpProtocol', [info.endpoint, info.versions.join(' / ')]) : ''" data-tour="mcp-clients">
        <el-tabs v-model="tab" data-test="mcp-tabs">
          <el-tab-pane label="Claude Desktop" name="claude"><pre class="cfg">{{ snip.claude }}</pre><p class="muted small">{{ $t('mcpSetup.claudeDesktopUsesStdioRun') }} <code>python -m honeymom mcp</code>{{ $t('mcpSetup.forRemoteConnectionsUseThe') }}</p></el-tab-pane>
          <el-tab-pane label="Cursor" name="cursor"><pre class="cfg">{{ snip.cursor }}</pre><p class="muted small">{{ $t('mcpSetup.putItInTheProject') }}</p></el-tab-pane>
          <el-tab-pane label="Dify" name="dify"><pre class="cfg">{{ snip.dify }}</pre><p class="muted small">{{ $t('mcpSetup.difyToolsMcpAddMcp') }}</p></el-tab-pane>
          <el-tab-pane :label="$t('mcpSetup.genericCurl')" name="curl"><pre class="cfg">{{ snip.curl }}</pre></el-tab-pane>
        </el-tabs>
        <el-button size="small" link type="primary" @click="copy(snip[tab as keyof typeof snip])">{{ $t('mcpSetup.copyConfig') }}</el-button>
        <span class="muted small">{{ $t('mcpSetup.fullInstructionsInDocsMcp') }}</span>
      </HmCard>
    </div>

    <div class="grid split mt">
      <HmCard :title="$t('mcpSetup.n3AvailableTools0', [info?.tools?.length || 0])" :sub="$t('mcpSetup.visibleToTheCurrentAccount')" flush data-tour="mcp-tools">
        <div class="tools" data-test="mcp-tools">
          <div v-for="t in info?.tools || []" :key="t.name" class="tl" :class="{ sel: tryName === t.name }" @click="tryName = t.name">
            <b>{{ t.title }}</b><code>{{ t.name }}</code><HmTag v-if="!t.annotations?.readOnlyHint" tone="amber">{{ $t('common.draft2') }}</HmTag><div class="muted small">{{ t.description }}</div></div>
        </div>
      </HmCard>
      <div>
        <HmCard :title="$t('mcpSetup.tryIt')" :sub="$t('mcpSetup.calledAsTheSignedIn')" data-tour="mcp-try">
          <div class="tryh"><el-select v-model="tryName" size="small" filterable style="width: 220px"><el-option v-for="t in info?.tools || []" :key="t.name" :value="t.name" :label="t.title" /></el-select>
            <el-input v-model="tryArgs" size="small" :placeholder="$t('mcpSetup.argumentsJsonEGEquipment')" data-test="mcp-try-args" />
            <el-button size="small" type="primary" :disabled="!info?.enabled" :loading="trying" data-test="mcp-try" @click="tryIt">{{ $t('mcpSetup.call') }}</el-button></div>
          <pre v-if="tryRes" class="out" data-test="mcp-try-out">{{ tryRes }}</pre>
        </HmCard>
        <HmCard class="mt" :title="$t('overviewTab.settings')">
          <div class="prm"><span>{{ $t('mcpSetup.perKeyPerMinute') }}</span><el-input-number v-model="setv.rate_per_min" size="small" :min="1" :max="10000" /><span>{{ $t('common.times') }}</span></div>
          <div class="prm"><el-switch v-model="setv.write_tools" size="small" /><span>{{ $t('mcpSetup.exposeTheWriteToolDraft') }}</span></div>
          <el-button v-perm:disable="'system.apikey.manage'" size="small" @click="saveSet">{{ $t('common.save') }}</el-button>
        </HmCard>
      </div>
    </div>

    <HmCard class="mt" :title="$t('mcpSetup.recentMcpCalls')" :sub="$t('mcpSetup.mcpConnectMcpCallIn')" flush data-tour="mcp-calls">
      <HmDataTable :columns="CCOLS" :rows="calls" max-height="300" :ai="false" :empty-text="$t('mcpSetup.noCallsYet')" />
    </HmCard>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { get, post, put } from '../api';

const router = useRouter();
const info = ref<any>(null), lines = ref<any[]>([]), newKey = ref(''), busy = ref(false), tab = ref('claude'), calls = ref<any[]>([]);
const kf = reactive<any>({ name: '', preset: 'readonly', lines: [], expires_at: null, ip_allow: '' });
const setv = reactive<any>({ rate_per_min: 120, write_tools: false });
const tryName = ref('factory_model'), tryArgs = ref('{}'), tryRes = ref(''), trying = ref(false);
const CCOLS = [{ key: 'time', label: $t('common.time'), width: 150 }, { key: 'user', label: $t('mcpSetup.caller') }, { key: 'target', label: $t('mcpSetup.toolResource') }, { key: 'ok', label: $t('common.result') }, { key: 'ms', label: 'ms', num: true }, { key: 'ip', label: 'IP' }];
const K = computed(() => newKey.value || $t('mcpSetup.hmkYourKey'));
const snip = computed(() => {
  const url = info.value?.endpoint || $t('mcpSetup.httpServer8900Mcp');
  return {
    claude: JSON.stringify({ mcpServers: { honeymom: { command: 'python', args: ['-m', 'honeymom', 'mcp'], cwd: 'D:\\HoneyMOM', env: { HONEYMOM_API_KEY: K.value, MOM_DATA: 'D:\\HoneyMOM\\data' } } } }, null, 2),
    cursor: JSON.stringify({ mcpServers: { honeymom: { url, headers: { 'X-API-Key': K.value } } } }, null, 2),
    dify: $t('mcpSetup.serverUrl0TransportStreamable', [url, K.value]),
    curl: `curl -s ${url} -H "X-API-Key: ${K.value}" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \\\n  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"cycle_analysis","arguments":{"period":"yesterday"}}}'`,
  };
});
async function load() {
  info.value = await get('/api/mcp/info');
  Object.assign(setv, { rate_per_min: info.value.settings.rate_per_min, write_tools: info.value.settings.write_tools });
  calls.value = (await get('/api/mcp/calls?limit=50')).items.map((c: any) => ({ ...c, ok: c.detail?.ok === false ? $t('common.error2') : $t('common.succeeded'), ms: c.detail?.ms }));
}
async function createKey() {
  busy.value = true;
  try {
    const r = await post('/api/mcp/keys', { name: kf.name || 'MCP', preset: kf.preset, scope: kf.lines.length ? { lines: kf.lines } : null, expires_at: kf.expires_at, ip_allow: kf.ip_allow });
    newKey.value = r.key; ElMessage.success($t('mcpSetup.keyGeneratedCopyAndSave'));
  } finally { busy.value = false; }
}
async function tryIt() {
  let a = {};
  try { a = JSON.parse(tryArgs.value || '{}'); } catch { ElMessage.error($t('mcpSetup.argumentsAreNotValidJson')); return; }
  trying.value = true;
  try { const r = await post('/api/mcp/try', { name: tryName.value, arguments: a }); tryRes.value = r.error ? JSON.stringify(r.error, null, 2) : (r.result?.content?.[0]?.text || '').slice(0, 6000); await load(); }
  finally { trying.value = false; }
}
async function saveSet() { await put('/api/mcp/settings', setv); ElMessage.success($t('common.saved')); load(); }
function copy(t: string) { navigator.clipboard?.writeText(t).then(() => ElMessage.success($t('common.copied'))).catch(() => {}); }
onMounted(async () => { await load(); const r = await get('/api/cycle/lines', true).catch(() => null); if (r) lines.value = r.lines.filter((l: any) => l.id); });
</script>

<style scoped>
.split { grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); align-items: start; }
@media (max-width: 1279px) { .split { grid-template-columns: minmax(0, 1fr); } }
.cfg { background: var(--hm-surface-2); border: 1px solid var(--hm-border); border-radius: var(--hm-r-sm); padding: 10px; font-size: 12px; white-space: pre-wrap; word-break: break-all; margin: 0 0 6px; max-height: 220px; overflow: auto; }
.newkey { margin-top: 8px; padding: 8px 10px; background: var(--hm-idle-bg, #fff7e6); border-radius: var(--hm-r-sm); font-size: var(--hm-fs-sm); word-break: break-all; }
.tools { max-height: 460px; overflow: auto; }
.tl { padding: 8px 14px; border-bottom: 1px solid var(--hm-border); cursor: pointer; font-size: var(--hm-fs-sm); }
.tl:hover, .tl.sel { background: var(--hm-surface-2); }
.tl code { margin: 0 6px; font-size: 11px; color: var(--hm-text-3); }
.tryh { display: flex; gap: 6px; }
.out { background: var(--hm-surface-2); padding: 8px; font-size: 12px; max-height: 300px; overflow: auto; white-space: pre-wrap; word-break: break-all; margin-top: 8px; }
.prm { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; font-size: var(--hm-fs-sm); }
.muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-xs); }
</style>
