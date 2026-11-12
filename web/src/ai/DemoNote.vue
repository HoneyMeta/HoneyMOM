<template>
  <p v-if="text" class="demo-note" data-test="demo-note"><span aria-hidden="true">ⓘ</span> {{ text }}</p>
</template>

<script setup lang="ts">
// Subtle hint for AI demo playback, e.g. "no recording in this language, showing the rule-based answer"
// (backend: `demo_note` on JSON results, SSE event `note`).
defineProps<{ text?: string | null }>();
</script>

<style scoped>
.demo-note { margin: 4px 0; font-size: var(--hm-fs-xs, 11.5px); line-height: 1.5; color: var(--hm-text-3); }
.demo-note span { color: var(--hm-primary); margin-right: 2px; }
</style>
