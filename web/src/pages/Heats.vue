<template>
  <div class="page">
    <HmPageHead :title="$t('common.heatAnalysis')" :sub="$t('heats.chemistryPowerConsumptionAndTapping')" art="foundry">
      <el-button @click="router.push('/e/heat')"><el-icon><Tickets /></el-icon>{{ $t('heats.heatRecords') }}</el-button>
      <el-button type="primary" data-tour="heats-charge" @click="router.push('/foundry/charge')"><el-icon><MagicStick /></el-icon>{{ $t('heats.chargeOptimization') }}</el-button>
    </HmPageHead>

    <div class="grid g4" data-tour="kpis">
      <HmKpi :label="$t('heats.heats')" :value="d?.rows.length" format="int" :unit="$t('heats.heats2')" :loading="!d" :hint="$t('heats.last80Heats')" />
      <HmKpi :label="$t('heats.avgKwhT')" :value="d?.avg.kwh_per_t ?? null" format="int" unit="kWh/t" :loading="!d" good-when="down" />
      <HmKpi :label="$t('heats.avgCarbonEquivalentCe')" :value="d?.avg.CE ?? null" :digits="2" :loading="!d" :hint="$t('heats.target415445')" />
      <HmKpi :label="$t('heats.ceOutOfWindow')" :value="d ? ceOut : null" format="int" :unit="$t('heats.heats2')" :status="d ? (ceOut ? 'bad' : 'ok') : ''" :loading="!d" />
    </div>

    <HmCard class="mt" :title="$t('heats.chemistryEnergyTrend')" :sub="$t('heats.ceTargetWindow415')" data-tour="heats-trend">
      <HmSkeleton v-if="!d" variant="block" :height="300" />
      <HmEmpty v-else-if="!d.rows.length" compact :title="$t('heats.noHeatsYet')" />
      <Chart v-else :option="trend" :height="300" :ai="heatAi" />
    </HmCard>

    <HmCard class="mt" :title="$t('heats.heatDetails')" :sub="$t('heats.clickARowToTrace')" flush data-tour="heats-table">
      <HmDataTable :columns="COLS" :rows="rows" entity="heat" clickable max-height="440" :loading="!d"
        :ai="(r: any) => ({ kind: 'row', entity: 'heat', id: r.id, heat: r.code, label: $t('heats.heat0Ce1Kwh', [r.code, r.CE ?? '—', r.kwh_per_t ?? '—']), data: { [$t('common.heat')]: r.code, [$t('heats.furnace')]: r.furnace__label, C: r.C, Si: r.Si, CE: r.CE, [$t('heats.tappingTemp')]: r.tap_temp, [$t('heats.kwhT')]: r.kwh_per_t, [$t('heats.residualMg')]: r.mg_avg, [$t('heats.castings')]: r.castings, [$t('heats.scrapRate')]: r.scrap_rate } })"
        @row-click="(r: any) => router.push('/trace?heat=' + r.code)">
        <template #cell-code="{ row }"><span class="hm-mono">{{ row.code }}</span></template>
        <template #cell-CE="{ row }"><span class="hm-num" :class="{ bad: row.CE && (row.CE < 4.15 || row.CE > 4.45) }">{{ row.CE ?? '—' }}</span></template>
        <template #cell-status="{ row }"><HmTag :tone="row.status === 'done' ? 'neutral' : 'amber'">{{ ST[row.status] || row.status }}</HmTag></template>
      </HmDataTable>
    </HmCard>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { get } from '../api';
import Chart from '../components/Chart.vue';
import { CH } from '../design/chart';
import { fmtTime } from '../design/format';
import './aiActions';

const router = useRouter(), d = ref<any>(null);
const ST: Record<string, string> = { melting: $t('heats.melting'), holding: $t('heats.holdingTapping'), done: $t('common.end') };
const COLS = [{ key: 'code', label: $t('common.heat'), width: 130 }, { key: 'furnace__label', label: $t('heats.furnace') }, { key: 'start_s', label: $t('common.start'), width: 100 },
  { key: 'C', label: 'C %', num: true, digits: 2 }, { key: 'Si', label: 'Si %', num: true, digits: 2 }, { key: 'CE', label: 'CE', num: true },
  { key: 'tap_temp', label: $t('heats.tappingTempC'), num: true, digits: 0 }, { key: 'kwh_per_t', label: $t('heats.kwhT'), num: true, digits: 0 }, { key: 'mg_avg', label: $t('heats.avgResidualMg'), num: true, digits: 3 },
  { key: 'castings', label: $t('heats.castings'), num: true }, { key: 'scrap_rate', label: $t('heats.scrapRate'), num: true, format: 'pct' }, { key: 'status', label: $t('common.status'), width: 90 }];
onMounted(async () => { d.value = await get('/api/foundry/heats?n=80'); });
const rows = computed(() => [...(d.value?.rows || [])].reverse().map((r: any) => ({ ...r, start_s: fmtTime(r.start) })));
const ceOut = computed(() => d.value.rows.filter((r: any) => r.CE && (r.CE < 4.15 || r.CE > 4.45)).length);
const heatAi = (p: any) => { const r = d.value.rows[p.dataIndex]; return r ? { kind: 'chart-point', entity: 'heat', id: r.id, heat: r.code, label: $t('heats.heat012', [r.code, p.seriesName, p.value]), data: { [$t('common.heat')]: r.code, C: r.C, Si: r.Si, CE: r.CE, [$t('heats.kwhT')]: r.kwh_per_t, [$t('heats.scrapRate')]: r.scrap_rate } } : null; };
const trend = computed(() => { const r = d.value.rows;
  return { tooltip: { trigger: 'axis' }, legend: {}, grid: { right: 8 }, xAxis: { type: 'category', data: r.map((x: any) => x.code.slice(-6)) },
    yAxis: [{ type: 'value', name: '%', scale: true }, { type: 'value', name: 'kWh/t', scale: true, splitLine: { show: false } }],
    series: [{ name: 'C', type: 'line', data: r.map((x: any) => x.C) }, { name: 'Si', type: 'line', data: r.map((x: any) => x.Si) },
      { name: 'CE', type: 'line', data: r.map((x: any) => x.CE), lineStyle: { width: 2.5 }, markArea: { silent: true, itemStyle: { color: 'rgba(21,163,74,.07)' }, data: [[{ yAxis: 4.15 }, { yAxis: 4.45 }]] } },
      { name: $t('heats.kwhT'), type: 'bar', yAxisIndex: 1, data: r.map((x: any) => x.kwh_per_t), itemStyle: { color: CH.soft }, barMaxWidth: 10 }] }; });
</script>

<style scoped>
.bad { color: var(--hm-fault-ink); font-weight: 600; }
</style>
