<template>
  <div class="page">
    <HmPageHead :title="$t('coplan.castingMachiningCoScheduling')" :sub="$t('coplan.theMachiningBottleneckSetsThe')" art="production">
      <el-button :loading="busy" @click="load"><el-icon><Refresh /></el-icon>{{ $t('coplan.recalculate') }}</el-button>
      <el-button @click="router.push('/production/gantt')"><el-icon><Histogram /></el-icon>{{ $t('coplan.schedulingProgress') }}</el-button>
    </HmPageHead>

    <HmSkeleton v-if="!d" variant="table" :rows="6" />
    <HmCard v-else-if="!d.ok"><HmEmpty :title="$t('coplan.canTCalculateRightNow')" :desc="d.msg" /></HmCard>
    <template v-else>
      <div class="grid g4" data-tour="cp-caps">
        <HmKpi v-for="(c, k) in d.caps" :key="k" :label="`${c.label}${k === d.bottleneck ? $t('coplan.bottleneck') : ''}`" :value="c.per_h" :digits="1" :unit="$t('coplan.pcsH')" :status="k === d.bottleneck ? 'bad' : ''"
          :hint="$t('coplan.n0MachinesOee1', [c.n, fmtPct(c.oee, 0)])" :badge="k === d.bottleneck ? $t('coplan.bottleneck2') : ''" />
      </div>
      <div class="grid g3 mt">
        <HmKpi :label="$t('coplan.currentRawCastings')" :value="d.blank" format="int" :unit="$t('common.pcs')" :hint="$t('coplan.n0PcsInRawCasting', [d.store])" :status="d.blank < d.target * 0.5 ? 'warn' : ''" />
        <HmKpi :label="$t('coplan.rawCastingStockTarget')" :value="d.target" format="int" :unit="$t('common.pcs')" :hint="$t('coplan.oneShiftOfMachiningDemand')" />
        <HmKpi :label="$t('coplan.foundryScrapRate')" :value="d.cast_scrap" format="pct" :digits="2" :hint="$t('coplan.last24H')" good-when="down" />
      </div>
      <div class="grid split mt">
        <HmCard :title="$t('coplan.planForTheNextThree')" flush data-tour="cp-plan">
          <HmDataTable :columns="COLS" :rows="d.shifts" row-key="shift" :ai="(r: any) => ({ kind: 'row', label: $t('coplan.n0Machining1PcsCasting', [r.shift, r.machining, r.casting]), data: r })" />
        </HmCard>
        <HmCard :title="$t('common.recommendation')" :sub="$t('coplan.rawCastingStockTrend')">
          <ul class="tips"><li v-for="t in d.tips" :key="t" v-ai="{ kind: 'text', label: t }">{{ t }}</li></ul>
          <Chart :option="opt" :height="180" :ai="$t('coplan.rawCastingStockTrend')" />
        </HmCard>
      </div>
      <AiExplain class="mt" feature="coplan" url="/api/ai/coplan/advise" :title="$t('coplan.aiSchedulingAdvice')" :button="$t('coplan.askAiForAdvice')"
        :hint="$t('coplan.basedOnTheBottleneckRaw')" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { get } from '../api';
import AiExplain from '../ai/AiExplain.vue';
import Chart from '../components/Chart.vue';
import { CH, targetLine } from '../design/chart';
import { fmtPct } from '../design/format';

const router = useRouter();
const d = ref<any>(null), busy = ref(false);
const COLS = [{ key: 'shift', label: $t('common.shift') }, { key: 'machining', label: $t('coplan.machiningPcs'), num: true }, { key: 'casting', label: $t('coplan.castingPcs'), num: true }, { key: 'molds', label: $t('coplan.molds'), num: true },
  { key: 'casting_hours', label: $t('coplan.moldingLineRunH'), num: true, digits: 1 }, { key: 'blank_end', label: $t('coplan.endOfShiftCastings'), num: true }];
async function load() { busy.value = true; try { d.value = await get('/api/cross/coplan'); } finally { busy.value = false; } }
onMounted(load);
const opt = computed(() => ({ tooltip: { trigger: 'axis' }, grid: { top: 12 }, xAxis: { type: 'category', data: [$t('common.current'), ...d.value.shifts.map((s: any) => s.shift + $t('coplan.end'))] },
  yAxis: { type: 'value' }, series: [{ type: 'line', name: $t('coplan.rawCastings'), showSymbol: true, data: [d.value.blank, ...d.value.shifts.map((s: any) => s.blank_end)], itemStyle: { color: CH.primary },
    areaStyle: { color: 'rgba(20,99,214,.08)' }, markLine: { data: [{ yAxis: d.value.target, ...targetLine($t('coplan.target')) }] } }] }));
</script>

<style scoped>
.split { grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr); align-items: start; }
@media (max-width: 1279px) { .split { grid-template-columns: minmax(0, 1fr); } }
.tips { margin: 0 0 8px; padding-left: 18px; font-size: var(--hm-fs-sm); line-height: 1.7; color: var(--hm-text-2); }
.tips li { margin-bottom: 4px; }
</style>
