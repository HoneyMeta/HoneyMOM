<template>
  <span v-if="variant === 'line'" class="sk line" :style="{ width: typeof width === 'number' ? width + 'px' : width, height: height + 'px' }" aria-hidden="true" />
  <div v-else class="sk-wrap" :class="variant" aria-busy="true" :aria-label="$t('hmSkeleton.loading')">
    <template v-if="variant === 'kpi'"><span class="sk" style="width:45%;height:11px" /><span class="sk" style="width:62%;height:28px;margin-top:8px" /><span class="sk" style="width:35%;height:10px;margin-top:8px" /></template>
    <template v-else-if="variant === 'table'"><span v-for="i in rows" :key="i" class="sk" :style="{ height: '14px', width: (i === 1 ? 100 : 88 + ((i * 37) % 12)) + '%', marginTop: i === 1 ? '0' : '10px' }" /></template>
    <template v-else-if="variant === 'block'"><span class="sk" :style="{ width: '100%', height: height + 'px' }" /></template>
    <template v-else><span v-for="i in rows" :key="i" class="sk" :style="{ height: '12px', width: (i === rows ? 60 : 92) + '%', marginTop: i === 1 ? '0' : '8px' }" /></template>
  </div>
</template>

<script setup lang="ts">
// Skeleton: line (one line) / text (a few lines) / kpi / table / block.
withDefaults(defineProps<{ variant?: 'line' | 'text' | 'kpi' | 'table' | 'block'; rows?: number; width?: number | string; height?: number }>(), { variant: 'text', rows: 3, width: '100%', height: 14 });
</script>

<style scoped>
.sk-wrap { display: flex; flex-direction: column; } .sk-wrap.kpi { padding: 12px 14px; border: 1px solid var(--hm-border); border-radius: var(--hm-r-lg); background: var(--hm-surface); }
.sk { display: block; border-radius: 4px; background: linear-gradient(90deg, #edf1f6 0, #f6f8fb 40%, #edf1f6 80%) 0 0 / 800px 100%; animation: hm-shimmer 1.4s linear infinite; }
.sk.line { display: inline-block; vertical-align: middle; }
@media (prefers-reduced-motion: reduce) { .sk { animation: none; } }
</style>
