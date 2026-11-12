<template>
  <div class="page">
    <div class="page-head"><h2>{{ $t('common.aiWatch') }}</h2><span class="sub">{{ $t('watch.everyMinuteABackgroundJob') }}</span></div>
    <div class="cols">
      <WatchFeed />
      <aside class="side">
        <HmCard :title="$t('watch.whatItWatches')" :sub="$t('watch.watcher')">
          <ul class="ws">
            <li v-for="w in WATCHERS" :key="w.k"><span class="dot" :class="w.d" /><b>{{ w.t }}</b><span>{{ w.s }}</span></li>
          </ul>
        </HmCard>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// AI Watch page (feature key: watch): findings list in ai/WatchFeed.vue, backend services/ai_watch.py.
import WatchFeed from '../ai/WatchFeed.vue';

const WATCHERS = [
  { k: 'spindle', d: 'equipment', t: $t('common.spindleLoad'), s: $t('watch.cncLoadOverTheLast') },
  { k: 'alarm', d: 'equipment', t: $t('watch.repeatedAlarms'), s: $t('watch.sameAlarmOnTheSame') },
  { k: 'oee', d: 'equipment', t: $t('watch.oeeDecline'), s: $t('watch.last4HMoreThan') },
  { k: 'tool', d: 'equipment', t: $t('watch.toolWear'), s: $t('watch.aiWearEstimate80While') },
  { k: 'scrap', d: 'quality', t: $t('common.scrapRate'), s: $t('watch.last8HMoreThan') },
  { k: 'defect', d: 'quality', t: $t('watch.defectCluster'), s: $t('watch.n4NonconformancesWithTheSame') },
  { k: 'pace', d: 'schedule', t: $t('watch.deliveryRisk'), s: $t('watch.workOrdersThatCanT') },
  { k: 'mat', d: 'material', t: $t('watch.materialCoverage'), s: $t('watch.remainingBomDemandOfWip') },
];
</script>

<style scoped>
.cols { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 14px; align-items: start; }
@media (max-width: 1100px) { .cols { grid-template-columns: 1fr; } }
.side { position: sticky; top: 8px; }
.ws { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.ws li { display: grid; grid-template-columns: 10px 64px 1fr; gap: 6px; align-items: baseline; font-size: var(--hm-fs-xs); color: var(--hm-text-2); line-height: 1.5; }
.ws b { color: var(--hm-text); font-weight: var(--hm-fw-medium); }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--hm-primary); align-self: center; }
.dot.quality { background: var(--hm-fault); } .dot.schedule { background: var(--hm-amber); } .dot.material { background: var(--hm-cyan); }
</style>
