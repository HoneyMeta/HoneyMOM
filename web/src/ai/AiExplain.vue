<template>
  <section v-if="aiOn(feature)" class="aix" :data-test="'ai-explain-' + feature">
    <div class="h"><span class="spk" aria-hidden="true" /><b>{{ title }}</b><span v-if="res" class="by" :class="res.by">{{ BY[res.by] || res.by }}</span><span class="grow" />
      <el-button size="small" :type="res ? '' : 'primary'" :plain="!res" :loading="busy" @click="run"><el-icon><MagicStick /></el-icon>{{ res ? $t('aiExplain.reInterpret') : button }}</el-button></div>
    <p v-if="!res && !busy" class="hint">{{ hint }}</p>
    <HmSkeleton v-if="busy && !res" variant="line" />
    <template v-if="res">
      <DemoNote :text="res.demo_note" />
      <div class="mdx" v-html="mdWithRefs(res.text, [])" />
      <AiBlocks v-if="res.blocks?.length" :blocks="res.blocks" compact :source="title" />
    </template>
  </section>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// "AI Insight" card on a page: POST url (body) → { text, blocks, by }. When the feature is off the whole card is hidden (rest of the page unaffected).
//   <AiExplain feature="backtrace" url="/api/ai/backtrace/explain" :body="{ days, group }" title="AI Root Cause Insight" />
import { ref, watch } from 'vue';
import { post } from '../api';
import AiBlocks from './AiBlocks.vue';
import DemoNote from './DemoNote.vue';
import { mdWithRefs } from './blocks';
import { aiOn } from './index';

const props = withDefaults(defineProps<{ feature: string; url: string; body?: any; title?: string; button?: string; hint?: string }>(),
  { body: () => ({}), title: $t('aiExplain.aiInsight'), button: $t('aiExplain.aiInsight'), hint: $t('aiExplain.aiInterpretsTheStatisticsAbove') });
const BY: Record<string, string> = { ai: 'AI', demo: $t('aiExplain.aiDemoMode'), rule: $t('aiExplain.ruleBasedExplanationLlmUnavailable') };
const res = ref<any>(null), busy = ref(false);
async function run() { busy.value = true; try { res.value = await post(props.url, props.body); } finally { busy.value = false; } }
watch(() => JSON.stringify(props.body), () => { res.value = null; });
</script>

<style scoped>
.aix { position: relative; border: 1px solid var(--hm-primary-soft-2); border-radius: var(--hm-r-lg); padding: 12px 14px; background: linear-gradient(180deg, var(--hm-ai-soft), var(--hm-surface) 60%); }
.aix::before { content: ''; position: absolute; left: 0; top: 10px; bottom: 10px; width: 3px; border-radius: 0 3px 3px 0; background: var(--hm-ai-grad); }
.h { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; } .h b { font-size: var(--hm-fs-md); } .grow { flex: 1; }
.spk { width: 14px; height: 14px; background: var(--hm-ai-grad); clip-path: polygon(50% 0, 62% 38%, 100% 50%, 62% 62%, 50% 100%, 38% 62%, 0 50%, 38% 38%); }
.by { font-size: 10.5px; border-radius: var(--hm-r-pill); padding: 0 8px; line-height: 17px; background: var(--hm-surface-3); color: var(--hm-text-2); }
.by.ai, .by.demo { background: var(--hm-ai-grad); color: #fff; }
.hint { margin: 0; font-size: var(--hm-fs-xs); color: var(--hm-text-3); }
.mdx { line-height: 1.7; font-size: var(--hm-fs-sm); margin-bottom: 8px; } .mdx :deep(p) { margin: 3px 0; } .mdx :deep(ul), .mdx :deep(ol) { margin: 3px 0; padding-left: 18px; }
</style>
