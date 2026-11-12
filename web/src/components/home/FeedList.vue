<template>
  <div class="feed" :class="{ big }" data-test="feed">
    <div class="tabs" role="tablist">
      <button v-for="t in tabs" :key="t.k" role="tab" :aria-selected="tab === t.k" :class="{ on: tab === t.k, ai: t.k === 'ai' }" :data-tab="t.k" @click="tab = t.k">
        {{ t.label }}<b class="hm-num">{{ t.n }}</b>
      </button>
      <span class="grow" />
      <span v-if="watch" class="watch on" data-test="watch-on" :title="$t('feedList.aiWatchIsOnAnomalies')"><i class="hm-pulse" />{{ $t('feedList.aiWatchActive') }}</span>
      <el-tooltip v-else :content="aiOffText('watch') || $t('feedList.aiWatchIsOffSystem')" placement="top">
        <span class="watch" data-test="watch-off"><i />{{ $t('feedList.ruleBasedWatch') }}</span>
      </el-tooltip>
    </div>
    <div class="list" :class="{ scroll: !big }">
      <TransitionGroup name="fd">
        <template v-for="it in shown" :key="it.id">
          <component :is="watchCard" v-if="it.kind === 'ai' && watchCard" :item="it" :finding="it" class="fd-ai" @changed="$emit('refresh')" />
          <article v-else class="fi" :class="['sv-' + it.severity, 'k-' + it.kind]" v-ai="aiOf(it)" :data-id="it.id" data-test="feed-item" @click="go(it, $event)">
            <div class="ic" :class="'c-' + it.category" aria-hidden="true"><el-icon><component :is="ICON[it.category] || 'Bell'" /></el-icon></div>
            <div class="bd">
              <div class="t"><span v-if="it.kind === 'ai'" class="aib">AI</span><span class="sv">{{ SV[it.severity] }}</span>{{ it.title }}</div>
              <div v-if="it.body" class="b">{{ it.body }}</div>
              <div v-if="it.evidence.length" class="ev">
                <span v-for="(e, i) in it.evidence.slice(0, 3)" :key="i" class="e" :class="{ lk: !!e.link }" @click.stop="e.link && router.push(e.link)"><i>{{ e.label }}</i>{{ e.value }}</span>
              </div>
              <div class="ft">
                <span class="tm hm-num" :title="it.ts">{{ ago(it.ts) }}</span>
                <span class="grow" />
                <button v-for="a in it.actions.slice(0, 2)" :key="a.label" class="act" :class="{ dis: a.ask && !aiOn(a.feature || 'ask') }" @click.stop="act(it, a)">{{ a.label }}</button>
                <button class="act ai" :class="{ dis: !aiOn('ask') }" :title="aiOn('ask') ? $t('feedList.askAiToAnalyzeThis') : aiOffText('ask')" data-test="feed-ask" @click.stop="ask(it)">
                  <span class="spk" />{{ $t('common.askAi') }}</button>
              </div>
            </div>
          </article>
        </template>
      </TransitionGroup>
      <div v-if="!shown.length && !loading" class="empty" data-test="feed-empty">
        <el-icon><CircleCheckFilled /></el-icon><b>{{ tab === 'all' ? $t('feedList.allClear') : $t('feedList.noAlertsInThisCategory') }}</b><span>{{ $t('feedList.noActiveAlarmsOverdueWork') }}</span>
      </div>
      <template v-if="loading && !items.length"><HmSkeleton v-for="i in 4" :key="i" variant="block" :height="74" /></template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// Alerts & AI Watch feed: rule alerts (alarms, Andon, overdue work orders, nonconformances, PM, cutting tools, shortages) + AI Watch findings (GET /api/home/feed; providers in honeymom/services/home_feed.py).
// Each card has v-ai (kind=alert): Ctrl+click / right-click hands it to AI; "Ask AI" asks with this card attached. Entries with kind='ai' render via ai/WatchFeed.vue (AI Watch component) if present.
import { computed, defineAsyncComponent, ref } from 'vue';
import { useRouter } from 'vue-router';
import { aiOffText, aiOn, askAi, toast } from '../../ai';
import { post } from '../../api';
import { since } from './usePoll';

const props = withDefaults(defineProps<{ items?: any[]; counts?: any; watch?: boolean; loading?: boolean; now?: string; big?: boolean; limit?: number }>(),
  { items: () => [], counts: () => ({}), watch: false, loading: false, now: '', big: false, limit: 50 });
const emit = defineEmits<{ refresh: [] }>();
const router = useRouter();
const SV: Record<string, string> = { critical: $t('common.urgent'), warning: $t('watchFeed.warning'), info: $t('common.hint') };
const ICON: Record<string, string> = { equipment: 'Cpu', quality: 'CircleCheck', production: 'Tickets', material: 'Box', maintenance: 'SetUp', tool: 'Tools', andon: 'BellFilled', ai: 'MagicStick', other: 'Bell' };
// AI Watch card (H2's ai/WatchFeed.vue): use it if present
const mods = import.meta.glob('../../ai/WatchFeed.vue');
const watchCard = Object.keys(mods).length ? defineAsyncComponent(mods[Object.keys(mods)[0]] as any) : null;

const tab = ref<'all' | 'critical' | 'ai'>('all');
const tabs = computed(() => [
  { k: 'all' as const, label: $t('common.all'), n: props.items.length },
  { k: 'critical' as const, label: $t('common.urgent'), n: props.items.filter((x) => x.severity === 'critical').length },
  ...(props.watch || props.items.some((x) => x.kind === 'ai') ? [{ k: 'ai' as const, label: $t('feedList.aiFinding'), n: props.items.filter((x) => x.kind === 'ai').length }] : []),
]);
const shown = computed(() => props.items.filter((x) => tab.value === 'all' || (tab.value === 'ai' ? x.kind === 'ai' : x.severity === tab.value)).slice(0, props.limit));
const ago = (ts: string) => { const s = since(ts, props.now); return s === $t('feedList.justNow') ? $t('feedList.justNow') : s ? s + $t('feedList.ago') : ''; };

function aiOf(it: any) {
  return { kind: 'alert', key: 'feed:' + it.id, label: `${SV[it.severity] || ''} ${it.title}`, entity: it.entity, id: it.entity_id, link: it.link || undefined,
    data: { [$t('common.title')]: it.title, [$t('common.description')]: it.body, [$t('feedList.severity')]: SV[it.severity], [$t('common.source')]: it.kind === 'ai' ? $t('common.aiWatch') : $t('feedList.ruleAlert'), [$t('common.time')]: it.ts,
      ...Object.fromEntries((it.evidence || []).map((e: any) => [e.label, e.value])), ...(it.data || {}) } };
}
function go(it: any, e: MouseEvent) { if (e.ctrlKey || e.metaKey) return; if (it.link) router.push(it.link); }
function ask(it: any) { askAi(it.ask || $t('feedList.n01AnalyzeTheCause', [it.title, it.body || '']), aiOf(it), { action: 'feed' }); }
async function act(it: any, a: any) {
  if (a.link) return router.push(a.link);
  if (a.ask) return askAi(a.ask, aiOf(it), { feature: a.feature || 'ask', action: 'feed' });
  if (a.api) {
    try { await post(a.api, a.body || {}); toast($t('feedList.n0', [a.label]), 'success'); emit('refresh'); } catch { /* call() already notified */ }
  }
}
</script>

<style scoped>
.feed { display: flex; flex-direction: column; min-height: 0; height: 100%; }
.tabs { display: flex; align-items: center; gap: 4px; padding-bottom: 8px; border-bottom: 1px solid var(--hm-border); flex: none; }
.tabs button { border: 0; background: transparent; font: inherit; font-size: var(--hm-fs-xs); color: var(--hm-text-2); padding: 3px 8px; border-radius: var(--hm-r-pill); cursor: pointer; display: inline-flex; gap: 5px; align-items: center; }
.tabs button b { font-weight: 600; color: var(--hm-text-3); }
.tabs button:hover { background: var(--hm-surface-3); }
.tabs button.on { background: var(--hm-primary-soft); color: var(--hm-primary-press); } .tabs button.on b { color: var(--hm-primary); }
.tabs button.ai.on { background: var(--hm-ai-soft); }
.watch { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: var(--hm-text-3); padding: 2px 8px; border-radius: var(--hm-r-pill); border: 1px solid var(--hm-border); white-space: nowrap; }
.watch i { width: 6px; height: 6px; border-radius: 50%; background: var(--hm-offline); }
.watch.on { color: var(--hm-primary-press); border: 1px solid transparent; background: linear-gradient(#fff, #fff) padding-box, var(--hm-ai-grad) border-box; font-weight: 600; }
.watch.on i { background: var(--hm-ai-2); color: rgba(20, 99, 214, .45); }
.list { flex: 1; min-height: 0; padding-top: 8px; display: flex; flex-direction: column; gap: 8px; }
.list.scroll { overflow: auto; margin-right: -8px; padding-right: 6px; }
/* AI Watch card (WatchFeed root): WatchFeed draws its own card style; here we only keep it from shrinking in the list; don't use .fi or borders/padding stack twice */
.fd-ai { flex: none; }
.fd-ai :deep(.fi) { box-shadow: none; }
.big .fd-ai :deep(.ac), .big .fd-ai :deep(.ev) { display: none; }
.fi { position: relative; display: flex; gap: 10px; padding: 9px 10px 8px 12px; border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); background: var(--hm-surface); cursor: pointer;
  transition: border-color var(--hm-dur-2), box-shadow var(--hm-dur-2), transform var(--hm-dur-2); flex: none; }
.fi::before { content: ''; position: absolute; left: -1px; top: 8px; bottom: 8px; width: 3px; border-radius: 0 3px 3px 0; background: var(--hm-offline); }
.fi.sv-critical::before { background: var(--hm-fault); } .fi.sv-warning::before { background: var(--hm-idle); } .fi.sv-info::before { background: var(--hm-primary); }
.fi.k-ai::before { background: var(--hm-ai-grad); width: 3px; }
.fi.k-ai { background: linear-gradient(180deg, var(--hm-ai-soft), #fff 60%); }
.fi.sv-critical { border-color: rgba(220, 43, 43, .28); }
.fi:hover { border-color: var(--hm-border-strong); box-shadow: var(--hm-e2); }
.ic { width: 28px; height: 28px; border-radius: var(--hm-r-md); display: grid; place-items: center; flex: none; background: var(--hm-surface-3); color: var(--hm-text-2); font-size: 15px; }
.sv-critical .ic { background: var(--hm-fault-soft); color: var(--hm-fault-ink); } .sv-warning .ic { background: var(--hm-idle-soft); color: var(--hm-idle-ink); }
.sv-info .ic { background: var(--hm-primary-soft); color: var(--hm-primary); } .k-ai .ic { background: var(--hm-ai-grad); color: #fff; }
.bd { min-width: 0; flex: 1; }
.t { font-size: var(--hm-fs-sm); font-weight: 600; color: var(--hm-text); line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.sv { font-size: 10.5px; font-weight: 700; margin-right: 6px; padding: 0 5px; border-radius: var(--hm-r-xs); vertical-align: 1px; }
.sv-critical .sv { color: var(--hm-fault-ink); background: var(--hm-fault-soft); } .sv-warning .sv { color: var(--hm-idle-ink); background: var(--hm-idle-soft); } .sv-info .sv { color: var(--hm-primary); background: var(--hm-primary-soft); }
.aib { font-size: 10px; font-weight: 800; color: #fff; background: var(--hm-ai-grad); border-radius: 3px; padding: 0 5px; margin-right: 6px; vertical-align: 1px; letter-spacing: .06em; }
.b { font-size: var(--hm-fs-xs); color: var(--hm-text-2); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ev { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
.e { font-size: 11px; background: var(--hm-surface-2); border: 1px solid var(--hm-hairline); border-radius: var(--hm-r-xs); padding: 0 6px; color: var(--hm-text); font-family: var(--hm-font-num); max-width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.e i { font-style: normal; color: var(--hm-text-3); margin-right: 4px; font-family: var(--hm-font); }
.e.lk { cursor: pointer; } .e.lk:hover { border-color: var(--hm-primary); color: var(--hm-primary); }
.ft { display: flex; align-items: center; gap: 4px; margin-top: 6px; }
.tm { font-size: 11px; color: var(--hm-text-3); }
.act { border: 1px solid var(--hm-border); background: #fff; color: var(--hm-text-2); font: inherit; font-size: 11.5px; height: 22px; padding: 0 8px; border-radius: var(--hm-r-sm); cursor: pointer; white-space: nowrap; }
.act:hover { border-color: var(--hm-primary); color: var(--hm-primary); }
.act.ai { display: inline-flex; align-items: center; gap: 4px; color: var(--hm-primary-press); border: 1px solid transparent; background: linear-gradient(#fff, #fff) padding-box, var(--hm-ai-grad) border-box; }
.act.dis { opacity: .5; } .act.ai.dis { background: #fff; border-color: var(--hm-border); color: var(--hm-text-3); }
.spk { width: 9px; height: 9px; background: var(--hm-ai-grad); clip-path: polygon(50% 0, 62% 38%, 100% 50%, 62% 62%, 50% 100%, 38% 62%, 0 50%, 38% 38%); }
.act.ai.dis .spk { background: var(--hm-text-3); }
.empty { display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 28px 10px; color: var(--hm-text-3); font-size: var(--hm-fs-xs); text-align: center; }
.empty .el-icon { font-size: 30px; color: var(--hm-run); } .empty b { color: var(--hm-text); font-size: var(--hm-fs-md); }
.fd-enter-active, .fd-leave-active { transition: all var(--hm-dur-3) var(--hm-ease); } .fd-enter-from { opacity: 0; transform: translateY(-6px); } .fd-leave-to { opacity: 0; }
.fd-move { transition: transform var(--hm-dur-3) var(--hm-ease); }
/* Display */
.big .t { font-size: 17px; -webkit-line-clamp: 1; } .big .b { font-size: 14px; } .big .fi { padding: 10px 14px; } .big .ic { width: 36px; height: 36px; font-size: 20px; }
.big .ft, .big .ev { display: none; } .big .tabs { display: none; }
</style>
