<template>
  <span class="hm-status" :class="[tone, variant]" :title="label || text" role="status">
    <i class="dot" :class="{ 'hm-pulse': pulseOn }" aria-hidden="true" />
    <span v-if="variant !== 'dot'" class="t"><slot>{{ text }}</slot></span>
  </span>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// Status: running (green) / idle (amber) / down (red) / offline (grey) / changeover (blue). variant=dot shows only the dot; text has no background.
import { computed } from 'vue';

const props = withDefaults(defineProps<{ status?: string | null; label?: string; variant?: 'badge' | 'dot' | 'text'; pulse?: boolean | null }>(), { variant: 'badge', pulse: null });
const MAP: Record<string, [string, string]> = {
  running: ['run', $t('common.running')], run: ['run', $t('common.running')], ok: ['run', $t('common.normal')], online: ['run', $t('common.online')], done: ['run', $t('common.done')],
  idle: ['idle', $t('common.idle')], waiting: ['idle', $t('common.waiting')], warn: ['idle', $t('hmStatus.warning')],
  down: ['fault', $t('common.down')], fault: ['fault', $t('common.fault')], alarm: ['fault', $t('common.alarm')], error: ['fault', $t('common.abnormal')],
  setup: ['setup', $t('common.changeover')], offline: ['offline', $t('common.offline')], unknown: ['offline', $t('hmStatus.unknown')],
};
const m = computed(() => MAP[String(props.status || 'offline')] || ['offline', String(props.status || $t('common.offline'))]);
const tone = computed(() => m.value[0]);
const text = computed(() => props.label || m.value[1]);
const pulseOn = computed(() => (props.pulse === null ? tone.value === 'run' || tone.value === 'fault' : props.pulse));
</script>

<style scoped>
.hm-status { display: inline-flex; align-items: center; gap: 6px; font-size: var(--hm-fs-xs); line-height: 20px; white-space: nowrap; vertical-align: middle; }
.hm-status.badge { padding: 0 9px 0 7px; border-radius: var(--hm-r-pill); }
.dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; flex: none; }
.run { color: var(--hm-run-ink); } .run .dot { background: var(--hm-run); color: rgba(21, 163, 74, .45); } .run.badge { background: var(--hm-run-soft); }
.idle { color: var(--hm-idle-ink); } .idle .dot { background: var(--hm-idle); color: rgba(217, 154, 22, .45); } .idle.badge { background: var(--hm-idle-soft); }
.fault { color: var(--hm-fault-ink); } .fault .dot { background: var(--hm-fault); color: rgba(220, 43, 43, .5); } .fault.badge { background: var(--hm-fault-soft); }
.setup { color: var(--hm-setup-ink); } .setup .dot { background: var(--hm-setup); color: rgba(20, 99, 214, .45); } .setup.badge { background: var(--hm-setup-soft); }
.offline { color: var(--hm-offline-ink); } .offline .dot { background: var(--hm-offline); } .offline.badge { background: var(--hm-offline-soft); }
</style>
