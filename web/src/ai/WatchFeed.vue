<template>
  <div class="wf" data-test="watch-feed">
    <div v-if="!aiOn('watch') && !one" class="off" data-test="watch-off"><el-icon><Lock /></el-icon>{{ aiOffText('watch') }}</div>
    <template v-else>
      <div v-if="!compact && !one" class="bar">
        <button v-for="(lab, k) in DOM" :key="k" class="seg" :class="{ on: domain === k }" @click="domain = k as string; load()">{{ lab }}<em v-if="cnt(k as string)">{{ cnt(k as string) }}</em></button>
        <span class="grow" />
        <label class="sw"><el-switch v-model="showAcked" size="small" @change="load" /> {{ $t('watchFeed.includeAcknowledged') }}</label>
        <el-button size="small" :loading="scanning" data-test="watch-scan" @click="scan"><el-icon><Refresh /></el-icon>{{ $t('watchFeed.checkNow') }}</el-button>
      </div>
      <HmSkeleton v-if="!one && loading && !items.length" variant="table" :rows="3" />
      <HmEmpty v-else-if="!one && !items.length" icon="data" compact :title="$t('watchFeed.nothingFoundSoFar')" :desc="lastScan ? $t('watchFeed.lastCheck0QualityEquipment', [lastScan.slice(5, 16)]) : $t('watchFeed.aiWatchChecksTheData')" />
      <div v-else class="list">
        <article v-for="f in shown" :key="f.id" class="fi" :class="[f.severity, f.status]" data-test="watch-finding"
          v-ai="{ kind: 'alert', entity: f.entity || undefined, id: f.entity_id || undefined, label: f.title, data: { ...(f.data || {}), [$t('common.summary')]: f.summary } }">
          <div class="h">
            <span class="sev" :class="f.severity">{{ SEV[f.severity] }}</span>
            <span class="dm">{{ f.domain_label }}</span>
            <b class="t">{{ f.title }}</b>
            <span class="grow" />
            <span class="ts" :title="$t('watchFeed.firstSeen01Times', [f.first_seen, f.count])">{{ (f.last_seen || '').slice(5, 16) }}<i v-if="f.count > 1"> ×{{ f.count }}</i></span>
          </div>
          <p v-if="f.narrative" class="nar" :title="$t('watchFeed.aiInterpretationWrittenWhenFirst', [f.first_seen])"><span class="spk" aria-hidden="true" />{{ f.narrative }}<em>{{ f.by === 'demo' ? $t('watchFeed.demoMode') : f.by === 'demo_old' ? $t('watchFeed.demoModeNumbersFromThe') : $t('watchFeed.whenFirstDetected') }}</em></p>
          <p class="sm">{{ f.summary }}</p>
          <div class="ev">
            <component :is="e.link ? 'a' : 'span'" v-for="(e, j) in f.evidence" :key="j" class="e" :class="{ lk: !!e.link }" @click="e.link && router.push(e.link)">
              <span class="el">{{ e.label }}</span><span class="evv">{{ e.value }}</span></component>
          </div>
          <AiBlocks v-if="f.blocks?.length && open[f.id]" :blocks="f.blocks" compact :pinnable="false" />
          <DraftCard v-if="drafts[f.id]" :draft="drafts[f.id]" />
          <div class="ac">
            <el-button v-if="f.blocks?.length" link size="small" @click="open[f.id] = !open[f.id]">{{ open[f.id] ? $t('watchFeed.hideData') : $t('watchFeed.viewData') }}</el-button>
            <el-button v-if="f.draft && !drafts[f.id]" size="small" type="primary" plain :loading="busy[f.id]" data-test="watch-draft" @click="draft(f)">
              <el-icon><EditPen /></el-icon>{{ $t('watchFeed.draft0', [f.draft_label]) }}</el-button>
            <el-button size="small" @click="ask(f)"><el-icon><ChatLineRound /></el-icon>{{ $t('common.askAi') }}</el-button>
            <el-button v-if="f.status === 'open'" size="small" text data-test="watch-ack" @click="ack(f)">{{ $t('common.gotIt') }}</el-button>
            <span v-else class="acked">{{ $t('watchFeed.acknowledgedBy0', [f.acked_by]) }}</span>
          </div>
        </article>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// AI Watch findings list (cards). Can be placed on Home, the AI Watch page, or the right panel:
//   <WatchFeed />                    full (filters, check now, view data)
//   <WatchFeed compact :limit="4" /> compact
//   <WatchFeed :finding="feedItem" />  single item (kind=ai entry in the Home feed: feedItem.finding is the raw finding); emits 'changed' after ack
// Backend: GET /api/ai/watch/findings, POST …/scan, …/{id}/ack, …/{id}/draft (services/ai_watch.py). When AI Watch is off, show a hint and skip API calls.
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { get, post } from '../api';
import { aiOffText, aiOn, askAi, toast } from './index';
import AiBlocks from './AiBlocks.vue';
import DraftCard from './DraftCard.vue';

const props = withDefaults(defineProps<{ compact?: boolean; limit?: number; finding?: any; item?: any }>(), { compact: false, limit: 50, finding: null, item: null });
const emit = defineEmits<{ changed: [] }>();
/** Single-item mode: entry passed in from the Home feed (the feed item's finding field, or the finding itself) */
const one = computed(() => { const x = props.finding || props.item; return x ? (x.finding || (x.watcher ? x : null)) : null; });
const router = useRouter();
const DOM: Record<string, string> = { '': $t('common.all'), quality: $t('common.quality'), equipment: $t('common.equipment'), schedule: $t('watchFeed.schedule'), material: $t('common.material') };
const SEV: Record<string, string> = { critical: $t('common.urgent'), warning: $t('watchFeed.warning'), info: $t('common.hint') };
const items = ref<any[]>([]), all = ref<any[]>([]), loading = ref(false), scanning = ref(false), lastScan = ref(''), domain = ref(''), showAcked = ref(false);
const open = reactive<Record<number, boolean>>({}), busy = reactive<Record<number, boolean>>({}), drafts = reactive<Record<number, any>>({});
const shown = computed(() => (one.value ? [one.value] : items.value.slice(0, props.limit)));
const cnt = (k: string) => (k ? all.value.filter((f) => f.domain === k).length : all.value.length);

async function load() {
  if (!aiOn('watch') || one.value) return;
  loading.value = true;
  try {
    const st = showAcked.value ? 'active' : 'open';
    const r = await get(`/api/ai/watch/findings?status=${st}&limit=${props.limit}`, true);
    all.value = r.items; lastScan.value = r.last_scan || '';
    items.value = domain.value ? r.items.filter((f: any) => f.domain === domain.value) : r.items;
  } catch { /* off or no permission */ } finally { loading.value = false; }
}
async function scan() {
  scanning.value = true;
  try { const r = await post('/api/ai/watch/scan', {}); toast($t('watchFeed.checkFinished0New1', [r.new, r.updated]), 'success'); await load(); } finally { scanning.value = false; }
}
async function ack(f: any) {
  await post(`/api/ai/watch/findings/${f.id}/ack`, {});
  f.status = 'acked';
  emit('changed');
  if (!showAcked.value) setTimeout(load, 300);
}
async function draft(f: any) {
  busy[f.id] = true;
  try { drafts[f.id] = await post(`/api/ai/watch/findings/${f.id}/draft`, {}); } finally { busy[f.id] = false; }
}
function ask(f: any) {
  askAi($t('watchFeed.aiWatchFound01', [f.title, f.summary]),
    { kind: 'alert', label: f.title, entity: f.entity || undefined, id: f.entity_id || undefined, data: { ...(f.data || {}), [$t('common.summary')]: f.summary } }, { action: 'watch', feature: 'watch' });
}
let t: any;
onMounted(() => { if (one.value) return; load(); t = setInterval(load, 60000); });
onUnmounted(() => clearInterval(t));
watch(() => aiOn('watch'), (v) => v && load());
defineExpose({ load });
</script>

<style scoped>
.wf { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.off { display: flex; align-items: center; gap: 6px; font-size: var(--hm-fs-xs); color: var(--hm-text-3); background: var(--hm-surface-2); border-radius: var(--hm-r-md); padding: 10px 12px; }
.bar { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.seg { border: 1px solid var(--hm-border); background: var(--hm-surface); border-radius: var(--hm-r-pill); font: inherit; font-size: var(--hm-fs-xs); padding: 3px 11px; cursor: pointer; color: var(--hm-text-2); }
.seg.on { background: var(--hm-text); color: #fff; border-color: var(--hm-text); } .seg em { font-style: normal; margin-left: 4px; opacity: .7; font-family: var(--hm-font-num); }
.grow { flex: 1; } .sw { font-size: var(--hm-fs-xs); color: var(--hm-text-3); display: inline-flex; gap: 4px; align-items: center; }
.list { display: flex; flex-direction: column; gap: 8px; }
.fi { position: relative; background: var(--hm-surface); border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); padding: 10px 12px 8px 14px; box-shadow: var(--hm-e1); }
.fi::before { content: ''; position: absolute; left: 0; top: 8px; bottom: 8px; width: 3px; border-radius: 0 3px 3px 0; background: var(--hm-offline); }
.fi.critical::before { background: var(--hm-fault); } .fi.warning::before { background: var(--hm-idle); } .fi.info::before { background: var(--hm-primary); }
.fi.acked { opacity: .72; }
.h { display: flex; align-items: center; gap: 6px; min-width: 0; }
.sev { font-size: 10.5px; font-weight: 700; border-radius: var(--hm-r-xs); padding: 0 5px; line-height: 17px; flex: none; }
.sev.critical { background: var(--hm-fault-soft); color: var(--hm-fault-ink); } .sev.warning { background: var(--hm-idle-soft); color: var(--hm-idle-ink); } .sev.info { background: var(--hm-primary-soft); color: var(--hm-primary-press); }
.dm { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); flex: none; }
.t { font-size: var(--hm-fs-sm); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.ts { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); font-family: var(--hm-font-num); flex: none; } .ts i { font-style: normal; color: var(--hm-amber-ink); }
.nar { margin: 6px 0 2px; font-size: var(--hm-fs-xs); line-height: 1.6; color: var(--hm-text); background: var(--hm-ai-soft); border-radius: var(--hm-r-sm); padding: 5px 8px; display: flex; gap: 6px; }
.nar em { font-style: normal; font-size: 10px; color: var(--hm-amber-ink); flex: none; align-self: flex-start; }
.spk { width: 11px; height: 11px; margin-top: 3px; flex: none; background: var(--hm-ai-grad); clip-path: polygon(50% 0, 62% 38%, 100% 50%, 62% 62%, 50% 100%, 38% 62%, 0 50%, 38% 38%); }
.sm { margin: 4px 0; font-size: var(--hm-fs-xs); color: var(--hm-text-2); line-height: 1.55; }
.ev { display: flex; flex-wrap: wrap; gap: 4px; margin: 4px 0 6px; }
.e { display: inline-flex; font-size: 11px; border: 1px solid var(--hm-hairline); border-radius: var(--hm-r-sm); overflow: hidden; background: var(--hm-surface-2); }
.e .el { color: var(--hm-text-3); padding: 1px 6px; border-right: 1px solid var(--hm-hairline); } .e .evv { padding: 1px 6px; color: var(--hm-text); font-family: var(--hm-font-num); }
.e.lk { cursor: pointer; text-decoration: none; } .e.lk .evv { color: var(--hm-primary); } .e.lk:hover { border-color: var(--hm-primary); }
.ac { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; margin-top: 4px; } .ac .el-button { margin: 0; }
.acked { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); }
.fi :deep(.dc) { margin: 6px 0; }
</style>
