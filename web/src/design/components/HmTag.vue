<template>
  <span class="hm-tag" :class="[tone, { closable, mono }]">
    <el-icon v-if="icon" class="i"><component :is="icon" /></el-icon>
    <span class="t"><slot /></span>
    <button v-if="closable" class="x" type="button" :aria-label="$t('hmTag.remove') + (closeLabel || '')" @click.stop="$emit('close')"><el-icon><Close /></el-icon></button>
  </span>
</template>

<script setup lang="ts">
// Tag / filter chip / AI context tag. tone: neutral primary amber ok warn bad ai.
withDefaults(defineProps<{ tone?: 'neutral' | 'primary' | 'amber' | 'ok' | 'warn' | 'bad' | 'ai'; icon?: string; closable?: boolean; closeLabel?: string; mono?: boolean }>(), { tone: 'neutral' });
defineEmits<{ close: [] }>();
</script>

<style scoped>
.hm-tag { display: inline-flex; align-items: center; gap: 4px; max-width: 100%; height: 22px; padding: 0 8px; border-radius: var(--hm-r-pill); font-size: var(--hm-fs-xs);
  border: 1px solid transparent; white-space: nowrap; vertical-align: middle; }
.t { overflow: hidden; text-overflow: ellipsis; } .mono .t { font-family: var(--hm-font-mono); }
.i { font-size: 12px; flex: none; }
.neutral { background: var(--hm-surface-3); color: var(--hm-text-2); }
.primary { background: var(--hm-primary-soft); color: var(--hm-primary); }
.amber { background: var(--hm-amber-soft); color: var(--hm-amber-ink); }
.ok { background: var(--hm-run-soft); color: var(--hm-run-ink); } .warn { background: var(--hm-idle-soft); color: var(--hm-idle-ink); } .bad { background: var(--hm-fault-soft); color: var(--hm-fault-ink); }
.ai { background: linear-gradient(#fff, #fff) padding-box, var(--hm-ai-grad) border-box; border: 1px solid transparent; color: var(--hm-primary-press); }
.closable { padding-right: 3px; }
.x { display: inline-grid; place-items: center; width: 16px; height: 16px; border: 0; border-radius: 50%; background: transparent; color: inherit; cursor: pointer; padding: 0; opacity: .7; flex: none; }
.x:hover { background: rgba(15, 32, 60, .08); opacity: 1; }
</style>
