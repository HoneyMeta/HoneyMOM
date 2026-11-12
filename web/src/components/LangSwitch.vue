<template>
  <div class="lang-sw" role="group" :aria-label="$t('langSwitch.language')" data-test="lang-switch" :title="$t('langSwitch.language')">
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm6.9 6h-2.95a15.7 15.7 0 0 0-1.38-3.56A8.03 8.03 0 0 1 18.9 8ZM12 4.04c.83 1.2 1.48 2.53 1.91 3.96h-3.82A13.9 13.9 0 0 1 12 4.04ZM4.26 14a8.2 8.2 0 0 1 0-4h3.38a16.5 16.5 0 0 0 0 4H4.26Zm.82 2h2.95c.32 1.25.78 2.45 1.38 3.56A7.99 7.99 0 0 1 5.08 16ZM8.03 8H5.08a7.99 7.99 0 0 1 4.33-3.56A15.7 15.7 0 0 0 8.03 8ZM12 19.96A14.1 14.1 0 0 1 10.09 16h3.82A14.1 14.1 0 0 1 12 19.96ZM14.34 14H9.66a14.7 14.7 0 0 1 0-4h4.68a14.7 14.7 0 0 1 0 4Zm.25 5.56c.6-1.11 1.06-2.31 1.38-3.56h2.95a8.03 8.03 0 0 1-4.33 3.56ZM16.36 14a16.5 16.5 0 0 0 0-4h3.38a8.2 8.2 0 0 1 0 4h-3.38Z"/></svg>
    <button v-for="l in LANGS" :key="l.code" type="button" :class="{ on: l.code === lang }" :aria-pressed="l.code === lang" :lang="l.code"
      :data-test="'lang-' + l.code" :title="l.label" @click="pick(l.code)">{{ compact ? l.short : l.label }}</button>
  </div>
</template>

<script setup lang="ts">
import { LANGS, lang, setLang, type Lang } from '../i18n';
defineProps<{ compact?: boolean }>();
const pick = (l: Lang) => { if (l !== lang) setLang(l); };
</script>

<style scoped>
.lang-sw { display: inline-flex; align-items: center; gap: 2px; padding: 2px 4px 2px 8px; border: 1px solid var(--hm-border); border-radius: 999px; color: var(--hm-text-3); background: var(--hm-surface); flex: none; }
.lang-sw svg { margin-right: 2px; }
.lang-sw button { border: 0; background: transparent; color: var(--hm-text-2); font: inherit; font-size: 12px; line-height: 20px; padding: 0 8px; border-radius: 999px; cursor: pointer; white-space: nowrap; }
.lang-sw button:hover { color: var(--hm-text); background: var(--hm-surface-3); }
.lang-sw button.on { background: var(--hm-text); color: var(--hm-surface); font-weight: 600; cursor: default; }
.lang-sw button:focus-visible { outline: 2px solid var(--hm-cyan, #19a7c4); outline-offset: 1px; }
</style>
