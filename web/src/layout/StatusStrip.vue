<template>
  <footer class="hm-status" data-test="status-strip" role="contentinfo">
    <span class="it" :title="shell.dataClock ? $t('statusStrip.dataClockBasedOnCollected') : $t('statusStrip.noDataCollectedYetShowing')">
      <i class="pd" :class="shell.ok ? (shell.dataClock ? 'g' : 'n') : 'r'" />{{ $t('common.dataClock') }} <b class="hm-num">{{ shell.now || '—' }}</b>
    </span>
    <span class="sep" />
    <span class="it" data-test="status-collect">{{ $t('statusStrip.collection') }} <b class="hm-num">{{ shell.collect.online }}/{{ shell.collect.total }}</b> {{ $t('common.online') }}</span>
    <span class="sep" />
    <span class="it">{{ $t('statusStrip.equipmentOnline') }} <b class="hm-num">{{ shell.equipment.online }}/{{ shell.equipment.total }}</b></span>
    <span class="it st"><i class="pd g" />{{ $t('common.running') }} <b class="hm-num">{{ bs.running || 0 }}</b></span>
    <span class="it st"><i class="pd y" />{{ $t('common.idle') }} <b class="hm-num">{{ bs.idle || 0 }}</b></span>
    <span class="it st"><i class="pd r" />{{ $t('common.down') }} <b class="hm-num">{{ bs.down || 0 }}</b></span>
    <span class="sep wide" />
    <span class="it wide" :class="{ hot: shell.alarms }">{{ $t('common.alarm') }} <b class="hm-num">{{ shell.alarms }}</b></span>
    <span class="grow" />
    <button class="it ai" :class="aiCls" data-test="ai-status" @click="router.push('/system/ai')" :title="$t('statusStrip.aiStatusClickForAi')">
      <span class="spk" aria-hidden="true" />{{ aiText }}
    </button>
    <span class="sep wide" />
    <span class="it ver wide">{{ $t('common.honeymom') }}</span>
  </footer>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import { aiState } from '../ai';
import { shell } from './shell';

const router = useRouter();
const bs = computed(() => shell.equipment.by_status || {});
const mode = computed(() => aiState.switches?.mode || shell.ai?.mode || 'off');
const aiText = computed(() => {
  const m = mode.value;
  if (m === 'off') return $t('aiDock.aiIsOff');
  const on = Object.values(aiState.switches?.effective || shell.ai?.effective || {}).filter(Boolean).length;
  const n = (aiState.switches?.labels || []).length || 8;
  if (m === 'demo') return $t('statusStrip.aiDemoMode01', [on, n]);
  if (m === 'record') return $t('statusStrip.aiOnlineRecordingDemo0', [on, n]);
  return (aiState.switches?.llm_configured ?? shell.ai?.llm_configured) ? $t('statusStrip.aiOnline01Features', [on, n]) : $t('statusStrip.aiOnNoLlmConfigured');
});
const aiCls = computed(() => ({ off: mode.value === 'off', demo: mode.value === 'demo' || mode.value === 'record' }));
</script>

<style scoped>
.hm-status { grid-area: status; display: flex; align-items: center; gap: 12px; height: var(--hm-status-h); padding: 0 12px; font-size: 11.5px; color: var(--hm-text-3);
  background: var(--hm-surface); border-top: 1px solid var(--hm-border); white-space: nowrap; overflow: hidden; position: relative; z-index: var(--hm-z-shell); }
.it { display: inline-flex; align-items: center; gap: 5px; } .it b { color: var(--hm-text-2); font-weight: 600; }
.it.hot, .it.hot b { color: var(--hm-fault-ink); }
.sep { width: 1px; height: 12px; background: var(--hm-border); }
.grow { flex: 1; }
.pd { width: 6px; height: 6px; border-radius: 50%; display: inline-block; background: var(--hm-offline); }
.pd.g { background: var(--hm-run); } .pd.y { background: var(--hm-idle); } .pd.r { background: var(--hm-fault); } .pd.n { background: var(--hm-offline); }
.ai { border: 0; background: transparent; font: inherit; color: var(--hm-primary-press); cursor: pointer; padding: 0 4px; border-radius: 4px; }
.ai:hover { background: var(--hm-surface-3); }
.spk { width: 10px; height: 10px; background: var(--hm-ai-grad); clip-path: polygon(50% 0, 62% 38%, 100% 50%, 62% 62%, 50% 100%, 38% 62%, 0 50%, 38% 38%); }
.ai.off { color: var(--hm-text-3); } .ai.off .spk { background: var(--hm-text-3); }
.ai.demo { color: var(--hm-amber-ink); }
.ver { color: var(--hm-text-3); letter-spacing: .04em; }
@media (max-width: 1023px) { .st, .wide { display: none; } }
@media (max-width: 700px) { .hm-status { display: none; } }
</style>
