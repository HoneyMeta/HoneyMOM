<template>
  <div class="hm-toolbar" :class="{ dense, sticky }" role="toolbar">
    <slot />
    <span class="grow" />
    <slot name="right" />
  </div>
</template>

<script setup lang="ts">
// Toolbar: filters/search on the left (default slot), actions on the right (#right slot). sticky: sticks to the top while scrolling.
withDefaults(defineProps<{ dense?: boolean; sticky?: boolean }>(), {});
</script>

<style scoped>
.hm-toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 10px; background: var(--hm-surface-2); border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); }
.hm-toolbar.dense { padding: 5px 8px; gap: 6px; }
.hm-toolbar.sticky { position: sticky; top: 0; z-index: 2; background: var(--hm-glass-strong); backdrop-filter: var(--hm-glass-blur); }
.grow { flex: 1; }
</style>
