<template>
  <div class="fn" :class="['s-' + (data.status || 'none'), { sel: selected, bad: data.problem }]" :style="{ '--c': color }">
    <Handle v-if="data.type !== 'begin'" type="target" :position="Position.Left" class="hin" />
    <div class="hd">
      <span class="ic"><el-icon size="14"><component :is="t?.icon || 'Box'" /></el-icon></span>
      <span class="nm" :title="data.name">{{ data.name }}</span>
      <span v-if="data.status" class="st">{{ ST[data.status] || data.status }}</span>
    </div>
    <div class="tp">{{ t?.label }} <span class="id">{{ id }}</span></div>
    <div v-if="sum" class="sm" :title="sum">{{ sum }}</div>
    <div v-if="handles" class="outs">
      <div v-for="h in handles" :key="h.id" class="out" :class="'h-' + h.id">
        <span>{{ h.label }}</span>
        <Handle type="source" :id="h.id" :position="Position.Right" class="hout" />
      </div>
    </div>
    <Handle v-else type="source" :position="Position.Right" class="hout single" />
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
import { Handle, Position } from '@vue-flow/core';
import { computed, inject } from 'vue';
import { CAT_COLOR, handlesOf, summaryOf } from '../../agentlib';

const props = defineProps<{ id: string; data: any; selected?: boolean }>();
const meta: any = inject('agentMeta');
const t = computed(() => meta.value?.types[props.data.type]);
const color = computed(() => CAT_COLOR[t.value?.cat] || '#6b7680');
const handles = computed(() => handlesOf(props.data.type, props.data.params));
const sum = computed(() => (meta.value ? summaryOf(meta.value, props.data) : ''));
const ST: Record<string, string> = { running: $t('api.running'), done: $t('common.done'), failed: $t('common.failed'), skipped: $t('api.notReached'), waiting: $t('common.waiting'), pending: '' };
</script>

<style scoped>
.fn { width: 220px; background: #fff; border: 1.5px solid #dfe4e8; border-left: 4px solid var(--c); border-radius: 10px; padding: 7px 10px 8px; font-size: 12px;
  box-shadow: 0 2px 6px rgba(20, 30, 40, .06); position: relative; }
.fn.sel { border-color: var(--c); box-shadow: 0 0 0 3px color-mix(in srgb, var(--c) 22%, transparent); }
.fn.bad { border-color: #e08a8a; }
.hd { display: flex; align-items: center; gap: 6px; }
.ic { width: 22px; height: 22px; border-radius: 6px; background: color-mix(in srgb, var(--c) 14%, #fff); color: var(--c); display: inline-flex; align-items: center; justify-content: center; flex: none; }
.nm { font-weight: 600; font-size: 13px; color: #1f2933; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.st { font-size: 11px; border-radius: 4px; padding: 0 5px; background: #eef1f3; color: #5b6670; flex: none; }
.tp { color: #6b7680; margin-top: 2px; } .id { color: #a0a8b0; font-family: Consolas, monospace; font-size: 11px; margin-left: 4px; }
.sm { color: #3f4a54; margin-top: 4px; background: #f6f8fa; border-radius: 5px; padding: 2px 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.outs { margin-top: 6px; display: flex; flex-direction: column; gap: 3px; align-items: flex-end; }
.out { position: relative; font-size: 11px; color: #5b6670; background: #f3f5f7; border-radius: 4px; padding: 1px 12px 1px 6px; }
.out.h-approved { background: #dff3e7; color: #1d6d3e; } .out.h-rejected { background: #fbe1e1; color: #a12a2a; }
.out .hout { right: -15px; }
.hout, .hin { width: 10px; height: 10px; background: #fff; border: 2px solid var(--c); }
.hout.single { top: 50%; }
.s-running { border-color: #2f7ad8; box-shadow: 0 0 0 3px rgba(47, 122, 216, .25); animation: pulse 1s infinite; }
.s-done { border-color: #2e9d5b; } .s-done .st { background: #dff3e7; color: #1d6d3e; }
.s-failed { border-color: #c83a3a; } .s-failed .st { background: #fbe1e1; color: #a12a2a; }
.s-waiting { border-color: #e0a020; box-shadow: 0 0 0 3px rgba(224, 160, 32, .25); } .s-waiting .st { background: #fbefd5; color: #8a5a07; }
.s-skipped { opacity: .55; }
@keyframes pulse { 50% { box-shadow: 0 0 0 6px rgba(47, 122, 216, .08); } }
</style>
