<template>
  <div class="page">
    <HmPageHead :title="$t('common.energyAnalysis')" :sub="$t('energy.meterTotalsAreRolledUp')" art="energy">
      <el-segmented v-model="days" :options="[{ label: $t('common.n1Day'), value: 1 }, { label: $t('common.n7Days'), value: 7 }, { label: $t('common.n30Days'), value: 30 }]" @change="load" />
    </HmPageHead>

    <div class="grid g4" data-tour="kpis">
      <HmKpi :label="$t('energy.totalPower')" :value="d?.total_kwh" format="int" unit="kWh" :loading="!d" :hint="$t('common.last0Days', [days])" />
      <HmKpi :label="$t('energy.melted')" :value="d?.melt_t" :digits="1" unit="t" :loading="!d" />
      <HmKpi :label="$t('energy.furnaceKwhT')" :value="d?.kwh_per_t ?? null" format="int" unit="kWh/t" :status="d?.kwh_per_t == null ? '' : d.kwh_per_t > 650 ? 'warn' : 'ok'" :loading="!d" :hint="$t('energy.bestPractice600')" good-when="down" />
      <HmKpi :label="$t('energy.totalEnergyPerPart')" :value="d?.kwh_per_piece ?? null" :digits="2" :unit="$t('energy.kwhPc')" :loading="!d" :hint="d ? $t('energy.n0PcsStored', [fmtNum(d.good)]) : ''" good-when="down" />
    </div>

    <div class="grid split mt" v-if="d">
      <HmCard :title="$t('energy.hourlyPowerLast48H')" :sub="$t('energy.stackedByEquipmentType')" data-tour="energy-hourly">
        <HmEmpty v-if="!Object.keys(d.hourly || {}).length" compact :title="$t('energy.noMeterDataYet')" />
        <Chart v-else :option="hourly" :height="300" :ai="$t('energy.hourlyPower')" />
      </HmCard>
      <HmCard :title="$t('energy.powerByMachine')" :sub="$t('energy.top0', [Math.min(12, d.by_equipment.length)])" data-tour="energy-rank">
        <HmEmpty v-if="!d.by_equipment.length" compact :title="$t('energy.noMachinePowerDataYet')" />
        <Chart v-else :option="rank" :height="300" :ai="$t('energy.powerByMachine')" />
      </HmCard>
    </div>
    <HmSkeleton v-else variant="table" :rows="6" />

    <HmCard v-if="d" class="mt" :title="$t('energy.energySavingTips')">
      <div class="tip" v-ai="{ kind: 'text', label: $t('energy.energySavingTips'), data: { [$t('common.recommendation')]: d.peak_note, [$t('energy.peakOffPeakPeriods')]: tou } }">
        <el-icon class="ti"><Sunny /></el-icon>
        <div><p>{{ d.peak_note || $t('energy.noSuggestions') }}</p><p class="muted small">{{ $t('energy.peakOffPeakPeriods0', [tou || $t('energy.notSet')]) }}</p></div>
      </div>
    </HmCard>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { computed, onMounted, ref } from 'vue';
import { get } from '../api';
import Chart from '../components/Chart.vue';
import { CH } from '../design/chart';
import { fmtNum } from '../design/format';

const d = ref<any>(null), days = ref(7), tou = ref('');
const ET: Record<string, string> = { furnace: $t('energy.inductionFurnace'), treatment: $t('energy.nodularization'), sand: $t('energy.sandPreparation'), molding: $t('energy.molding'), cooling: $t('energy.cooling'), shotblast: $t('energy.shotBlasting'), cnc: $t('common.machining'), deburr: $t('energy.deburring'), inspection: $t('energy.testing'),
  warehouse: $t('energy.asRs'), agv: 'AGV', crew: $t('common.teams'), receiving: $t('common.receive') };
async function load() { d.value = await get('/api/energy/summary?days=' + days.value); }
onMounted(async () => { load(); const s = await get('/api/e/setting?f_key=tou', true).catch(() => ({ items: [] })); tou.value = s.items[0]?.value || ''; });
const hourly = computed(() => {
  const hs = Object.keys(d.value.hourly), types = [...new Set(hs.flatMap((h) => Object.keys(d.value.hourly[h])))];
  return { tooltip: { trigger: 'axis', valueFormatter: (v: number) => fmtNum(v, 1) + ' kWh' }, legend: {},
    xAxis: { type: 'category', data: hs.map((h) => h.slice(11, 16)) }, yAxis: { type: 'value', name: 'kWh' },
    series: types.map((t) => ({ name: ET[t] || t, type: 'bar', stack: 'a', barMaxWidth: 16, itemStyle: { borderRadius: 0 }, data: hs.map((h) => +(d.value.hourly[h][t] || 0).toFixed(1)) })) };
});
const rank = computed(() => { const r = d.value.by_equipment.slice(0, 12);
  return { tooltip: { trigger: 'axis', valueFormatter: (v: number) => fmtNum(v) + ' kWh' }, grid: { right: 56 }, xAxis: { type: 'value' }, yAxis: { type: 'category', inverse: true, data: r.map((x: any) => x.name) },
    series: [{ name: $t('daily.power'), type: 'bar', data: r.map((x: any) => Math.round(x.kwh)), itemStyle: { color: CH.primary, borderRadius: [0, 3, 3, 0] },
      label: { show: true, position: 'right', color: CH.text2, fontSize: 11, formatter: (p: any) => fmtNum(p.value) } }] }; });
</script>

<style scoped>
.split { grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr); }
@media (max-width: 1279px) { .split { grid-template-columns: minmax(0, 1fr); } }
.tip { display: flex; gap: 12px; align-items: flex-start; }
.tip p { margin: 0 0 4px; line-height: 1.7; }
.ti { font-size: 20px; color: var(--hm-amber); margin-top: 2px; flex: none; }
</style>
