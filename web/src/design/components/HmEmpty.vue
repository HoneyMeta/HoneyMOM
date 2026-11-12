<template>
  <div class="hm-empty" :class="{ compact, plain }" role="status" :data-empty="icon">
    <img v-if="!plain" class="art" :src="ART[icon] || ART.data" alt="" aria-hidden="true" draggable="false" />
    <div class="t">{{ title }}</div>
    <div v-if="desc || $slots.desc" class="d"><slot name="desc">{{ desc }}</slot></div>
    <div v-if="$slots.default" class="a"><slot /></div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// Empty state: isometric line illustration (public/art/empty-*.webp, see public/art/manifest.json) + title + description + action buttons (default slot).
// plain: a single line of text, no illustration (when a page has several empty areas, only the main one gets an illustration; others use plain)
// icon: data no data yet / search no filter results / lock no permission / ai AI feature off / offline can't reach the server or data source
withDefaults(defineProps<{ title?: string; desc?: string; icon?: 'data' | 'search' | 'lock' | 'ai' | 'offline'; compact?: boolean; plain?: boolean }>(), { title: $t('hmDataTable.noData'), icon: 'data' });
const ART: Record<string, string> = {
  data: '/art/empty-data.webp', search: '/art/empty-search.webp', lock: '/art/empty-permission.webp', ai: '/art/empty-ai-off.webp', offline: '/art/empty-offline.webp',
};
</script>

<style scoped>
.hm-empty { display: flex; flex-direction: column; align-items: center; text-align: center; padding: var(--hm-sp-8) var(--hm-sp-4); color: var(--hm-text-2); }
.hm-empty.compact { padding: var(--hm-sp-4) var(--hm-sp-3); }
.hm-empty.plain { flex-direction: row; flex-wrap: wrap; justify-content: center; align-items: baseline; gap: 2px 8px; padding: var(--hm-sp-3); }
.plain .t { font-weight: 500; font-size: var(--hm-fs-sm); color: var(--hm-text-2); } .plain .d { margin-top: 0; }
.art { height: calc(132px * var(--hm-scale)); max-width: 80%; object-fit: contain; margin-bottom: var(--hm-sp-3); user-select: none; }
.compact .art { height: calc(76px * var(--hm-scale)); margin-bottom: var(--hm-sp-2); }
.t { font-weight: 600; font-size: var(--hm-fs-md); color: var(--hm-text); }
.d { margin-top: 4px; font-size: var(--hm-fs-xs); color: var(--hm-text-3); max-width: 440px; line-height: 1.7; }
.a { margin-top: 14px; display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; }
</style>
