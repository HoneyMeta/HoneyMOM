<template>
  <div class="page" data-test="ai-settings">
    <HmPageHead :title="$t('common.aiFeatures')" :sub="$t('aiSettings.allOffByDefaultFor')">
      <template v-if="isAdmin">
        <el-button :disabled="busy" @click="setAll(true)">{{ $t('aiSettings.turnAllOn') }}</el-button>
        <el-button :disabled="busy" @click="setAll(false)">{{ $t('aiSettings.turnAllOff') }}</el-button>
      </template>
    </HmPageHead>
    <el-alert v-if="!isAdmin" type="info" :closable="false" show-icon :title="$t('aiSettings.onlyAdministratorsCanChangeAi')" style="margin-bottom:14px" />

    <div v-if="s" class="top grid">
      <div class="master hm-card" :class="{ on: s.global }">
        <div class="mt">
          <span class="spk" aria-hidden="true" />
          <div class="mtx"><b>{{ $t('aiSettings.aiMasterSwitch') }}</b><small>{{ s.global ? $t('aiSettings.onTheFeaturesEnabledBelow') : $t('aiSettings.offNoAiFeaturesAre') }}</small></div>
          <el-switch v-model="s.global" size="large" :disabled="!isAdmin || busy" data-test="ai-global" @change="save({ global: s.global })" />
        </div>
        <div class="llm">
          <span>{{ $t('common.llm') }}</span><b :class="s.llm_configured ? 'ok' : 'warn'">{{ s.llm_configured ? $t('aiSettings.configured') : $t('aiSettings.notConfigured') }}</b>
          <span v-if="s.model" class="hm-mono">{{ s.model }}</span>
          <span class="muted">{{ $t('aiSettings.theLlmConnectionIsConfigured') }}</span>
        </div>
      </div>
      <div class="mode hm-card">
        <b>{{ $t('common.runMode') }}</b>
        <el-radio-group v-model="modeSel" :disabled="!isAdmin || busy || !s.global" data-test="ai-mode" @change="setMode">
          <el-radio-button value="live">{{ $t('aiSettings.liveLlm') }}</el-radio-button>
          <el-radio-button value="demo">{{ $t('watchFeed.demoMode') }}</el-radio-button>
          <el-radio-button value="record">{{ $t('aiSettings.recordDemo') }}</el-radio-button>
        </el-radio-group>
        <p class="muted small">{{ MODE_TIP[modeSel] }}</p>
        <p v-if="isAdmin" class="small rec">{{ $t('aiSettings.recorded') }} <b class="hm-num">{{ recCount }}</b> {{ $t('aiSettings.demoAnswers') }}<span v-if="recByFeat" class="muted">{{ $t('aiSettings.n0', [recByFeat]) }}</span></p>
      </div>
    </div>

    <div v-if="s" class="feats grid">
      <div v-for="f in s.labels" :key="f.key" class="feat hm-card" :class="{ on: s.features[f.key], eff: s.effective[f.key] }">
        <div class="fh">
          <b>{{ f.label }}</b>
          <HmTag :tone="s.effective[f.key] ? 'ok' : s.features[f.key] ? 'warn' : 'neutral'">{{ s.effective[f.key] ? $t('common.available') : s.features[f.key] ? $t('aiSettings.waitingForMasterSwitch') : $t('common.off') }}</HmTag>
          <span class="grow" />
          <el-switch v-model="s.features[f.key]" :disabled="!isAdmin || busy" :data-test="'ai-feat-' + f.key" @change="save({ features: { [f.key]: s.features[f.key] } })" />
        </div>
        <p class="fd">{{ f.desc }}</p>
      </div>
    </div>
    <HmSkeleton v-else variant="table" :rows="6" />
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, ref } from 'vue';
import { setAiSwitches } from '../ai';
import { call, get, session } from '../api';
import { refreshShell } from '../layout/shell';

const s = ref<any>(null), busy = ref(false), modeSel = ref('live'), recs = ref<any[]>([]);
const isAdmin = computed(() => (session.user?.role_list || []).includes('admin'));
const MODE_TIP: Record<string, string> = {
  live: $t('aiSettings.callsTheRealLlmUses'),
  demo: $t('aiSettings.replaysRecordedAiAnswersWithout'),
  record: $t('aiSettings.callsTheRealLlmAnd'),
};
const recCount = computed(() => recs.value.length);
const recByFeat = computed(() => {
  const c: Record<string, number> = {};
  for (const r of recs.value) c[r.feature] = (c[r.feature] || 0) + 1;
  return Object.entries(c).map(([k, n]) => `${s.value?.labels.find((x: any) => x.key === k)?.label || k} ${n}`).join($t('common.k42'));
});

function apply(v: any) {
  s.value = JSON.parse(JSON.stringify(v));
  modeSel.value = v.demo ? 'demo' : v.record ? 'record' : 'live';
  setAiSwitches(v);
  refreshShell();
}
async function load() {
  apply(await get('/api/ai/switches'));
  if (isAdmin.value) recs.value = (await get('/api/ai/demo/recordings', true).catch(() => ({ items: [] }))).items;
}
// Toggling several switches in a row: requests are queued and sent in order, only the last response refreshes the UI (switches aren't disabled while saving so clicks aren't swallowed; stale responses don't override newer clicks)
let chain: Promise<any> = Promise.resolve(), seq = 0;
function save(patch: any) {
  const my = ++seq;
  chain = chain.then(async () => {
    try { const v = await call('PUT', '/api/ai/switches', patch); if (my === seq) apply(v); ElMessage({ type: 'success', message: $t('common.saved'), duration: 1000, grouping: true }); }
    catch { if (my === seq) await load(); }
  });
  return chain;
}
function setMode(m: any) { save({ demo: m === 'demo', record: m === 'record' }); }
async function setAll(on: boolean) { busy.value = true; try { await save({ global: on, features: Object.fromEntries(s.value.labels.map((f: any) => [f.key, on])) }); } finally { busy.value = false; } }
onMounted(load);
</script>

<style scoped>
.top { grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); margin-bottom: 14px; }
.master, .mode { padding: 16px 18px; }
.master { position: relative; overflow: hidden; }
.master.on { border-color: transparent; background: linear-gradient(#fff, #fff) padding-box, var(--hm-ai-grad) border-box; border: 1px solid transparent; }
.mt { display: flex; align-items: center; gap: 14px; }
.spk { width: 30px; height: 30px; background: var(--hm-text-3); clip-path: polygon(50% 0, 62% 38%, 100% 50%, 62% 62%, 50% 100%, 38% 62%, 0 50%, 38% 38%); flex: none; }
.on .spk { background: var(--hm-ai-grad); }
.mtx { flex: 1; display: flex; flex-direction: column; } .mtx b { font-size: 16px; } .mtx small { color: var(--hm-text-3); font-size: 12.5px; margin-top: 2px; }
.llm { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-top: 14px; padding-top: 12px; border-top: 1px dashed var(--hm-border); font-size: 12.5px; color: var(--hm-text-2); }
.mode > b { display: block; margin-bottom: 10px; } .mode p { margin: 10px 0 0; line-height: 1.7; } .rec b { color: var(--hm-text); }
.feats { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.feat { padding: 12px 14px; transition: border-color var(--hm-dur-2), box-shadow var(--hm-dur-2); }
.feat.eff { border-color: rgba(21, 163, 74, .35); }
.fh { display: flex; align-items: center; gap: 8px; } .fh b { font-size: 14px; } .grow { flex: 1; }
.fd { margin: 8px 0 0; font-size: 12px; color: var(--hm-text-3); line-height: 1.6; min-height: 38px; }
@media (max-width: 1279px) { .feats { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 900px) { .top, .feats { grid-template-columns: 1fr; } }
</style>
