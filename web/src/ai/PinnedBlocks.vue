<template>
  <div class="pb" data-test="pinned-blocks">
    <HmEmpty v-if="loaded && !pins.length && !hideEmpty" icon="data" compact :title="$t('pinnedBlocks.noPinnedResultsYet')" :desc="$t('pinnedBlocks.clickPinToHomeOn')" />
    <div v-else class="grid" :style="{ '--cols': cols }">
      <section v-for="p in pins" :key="p.id" class="pin" data-test="pinned-block">
        <header><b :title="p.title">{{ p.title }}</b><span class="m">{{ p.live ? $t('pinnedBlocks.live') : (p.created_at || '').slice(5, 16) + $t('pinnedBlocks.pinned') }}</span><span class="grow" />
          <button class="x" :title="$t('pinnedBlocks.unpin')" :aria-label="$t('pinnedBlocks.unpin2') + p.title" @click="unpin(p)"><el-icon><Close /></el-icon></button></header>
        <AiBlocks :blocks="[{ ...p.block, title: p.block.type === 'kpis' ? '' : p.block.title && p.block.title !== p.title ? p.block.title : '' }]" :pinnable="false" compact />
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
// AI results pinned to Home (chart / table / KPI card). The Home page hosts <PinnedBlocks /> (cols = column count, default 2). Viewable even with AI off.
import { onMounted, onUnmounted, ref } from 'vue';
import { del, get } from '../api';
import AiBlocks from './AiBlocks.vue';

withDefaults(defineProps<{ cols?: number; hideEmpty?: boolean }>(), { cols: 2, hideEmpty: false });
const pins = ref<any[]>([]), loaded = ref(false);
async function load() { try { pins.value = await get('/api/ai/pins', true); } catch { pins.value = []; } loaded.value = true; }
async function unpin(p: any) { await del(`/api/ai/pins/${p.id}`); pins.value = pins.value.filter((x) => x.id !== p.id); window.dispatchEvent(new Event('ai-pins-changed')); }
onMounted(() => { load(); window.addEventListener('ai-pins-changed', load); });
onUnmounted(() => window.removeEventListener('ai-pins-changed', load));
defineExpose({ load, count: () => pins.value.length });
</script>

<style scoped>
.grid { display: grid; grid-template-columns: repeat(var(--cols), minmax(0, 1fr)); gap: 10px; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
.pin { background: var(--hm-surface); border: 1px solid var(--hm-border); border-radius: var(--hm-r-lg); padding: 8px 10px 10px; box-shadow: var(--hm-e1); min-width: 0; }
header { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
header b { font-size: var(--hm-fs-sm); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.m { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); flex: none; } .grow { flex: 1; }
.x { border: 0; background: transparent; color: var(--hm-text-3); cursor: pointer; padding: 2px; border-radius: var(--hm-r-xs); } .x:hover { color: var(--hm-fault-ink); background: var(--hm-fault-soft); }
.pin :deep(.blk) { border: 0; padding: 0; }
</style>
