<template>
  <section class="hm-card" :class="{ glass, flush, hoverable }">
    <header v-if="title || $slots.title || $slots.actions" class="ch">
      <div class="tt"><slot name="title"><h3>{{ title }}</h3></slot><small v-if="sub" class="sub">{{ sub }}</small></div>
      <div class="ac"><slot name="actions" /></div>
    </header>
    <div class="cb"><slot /></div>
    <footer v-if="$slots.footer" class="cf"><slot name="footer" /></footer>
  </section>
</template>

<script setup lang="ts">
// Card: title + subtitle + top-right actions + content + footer. glass = translucent glass (overlay above the canvas), flush = no horizontal padding (tables).
withDefaults(defineProps<{ title?: string; sub?: string; glass?: boolean; flush?: boolean; hoverable?: boolean }>(), {});
</script>

<style scoped>
.hm-card { background: var(--hm-surface); border: 1px solid var(--hm-border); border-radius: var(--hm-r-lg); box-shadow: var(--hm-e1); display: flex; flex-direction: column; min-width: 0; padding: 0; }
.hm-card.glass { background: var(--hm-glass); backdrop-filter: var(--hm-glass-blur); -webkit-backdrop-filter: var(--hm-glass-blur); border-color: var(--hm-hairline); }
.hm-card.hoverable:hover { box-shadow: var(--hm-e2); border-color: var(--hm-border-strong); }
.ch { display: flex; align-items: center; gap: 10px; padding: 11px 14px 0; min-height: 38px; }
.tt { display: flex; align-items: baseline; gap: 8px; flex: 1; min-width: 0; }
.tt :deep(h3) { margin: 0; font-size: var(--hm-fs-md); font-weight: 600; color: var(--hm-text); white-space: nowrap; }
.sub { color: var(--hm-text-3); font-size: var(--hm-fs-xs); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ac { display: flex; align-items: center; gap: 6px; }
.cb { padding: 10px 14px 14px; flex: 1; min-height: 0; }
.flush .cb { padding: 8px 0 0; }
.cf { border-top: 1px solid var(--hm-border); padding: 8px 14px; font-size: var(--hm-fs-xs); color: var(--hm-text-3); }
</style>
