<template>
  <div class="page">
    <HmPageHead :title="$t('common.machiningDefectStats')" :sub="$t('machDefects.scrapAndReworkByMachine')">
      <el-segmented v-model="days" :options="[{ label: $t('common.n1Day'), value: 1 }, { label: $t('common.n7Days'), value: 7 }, { label: $t('common.n30Days'), value: 30 }]" @change="load" />
      <el-button data-tour="md-import" @click="cmmOpen = true"><el-icon><Upload /></el-icon>{{ $t('machDefects.importCmmFirstArticleData') }}</el-button>
    </HmPageHead>

    <div class="grid g4" data-tour="kpis">
      <HmKpi :label="$t('machDefects.machinedParts')" :value="tot.total" format="int" :unit="$t('common.pcs')" :loading="!d" :hint="$t('machDefects.last0Days1Machine', [days, d?.machines.length || 0])" />
      <HmKpi :label="$t('common.scrapped')" :value="tot.scrap" format="int" :unit="$t('common.pcs')" :status="tot.scrap ? 'warn' : 'ok'" :loading="!d" />
      <HmKpi :label="$t('machDefects.overallScrapRate')" :value="tot.total ? tot.scrap / tot.total : null" format="pct" :digits="2" :loading="!d" good-when="down" />
      <HmKpi :label="$t('machDefects.mostScrap')" :value="worst?.machine || '—'" :tick="false" :loading="!d" :hint="worst ? $t('machDefects.scrapRate0', [fmtPct(worst.scrap_rate, 2)]) : ''" />
    </div>

    <div class="grid split mt" v-if="d">
      <HmCard :title="$t('machDefects.scrapAndReworkRateBy')" data-tour="md-chart">
        <HmEmpty v-if="!d.machines.length" compact :title="$t('machDefects.noMachiningDataInThis')" />
        <Chart v-else :option="opt" :height="300" :ai="$t('machDefects.scrapAndReworkRateBy')" />
      </HmCard>
      <HmCard :title="$t('common.details')" flush>
        <HmDataTable :columns="COLS" :rows="d.machines" row-key="machine" max-height="340" :row-label="(r: any) => $t('machDefects.n0ScrapRate1', [r.machine, fmtPct(r.scrap_rate, 2)])">
          <template #cell-causes="{ row }"><span class="small">{{ Object.entries(row.causes).map(([k, v]) => `${k}×${v}`).join($t('common.k42')) || '—' }}</span></template>
        </HmDataTable>
      </HmCard>
    </div>
    <HmSkeleton v-else variant="table" :rows="6" />

    <el-dialog v-model="cmmOpen" :title="$t('machDefects.importCmmFirstArticleInspection')" width="min(580px, 96vw)">
      <p class="small muted">{{ $t('machDefects.eachCsvRowPartCode') }}</p>
      <el-input v-model="csv" type="textarea" :rows="8" placeholder="SH261030000123,BORE_DIA,40.012,CMM-01" class="hm-mono" />
      <template #footer><el-button @click="cmmOpen = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :disabled="!csv.trim()" @click="importCmm">{{ $t('common.import') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, ref } from 'vue';
import { get, post } from '../api';
import Chart from '../components/Chart.vue';
import { CH } from '../design/chart';
import { fmtPct } from '../design/format';

const d = ref<any>(null), days = ref(7), cmmOpen = ref(false), csv = ref('');
const COLS = [{ key: 'machine', label: $t('common.machineTool') }, { key: 'total', label: $t('machDefects.machinedParts'), num: true }, { key: 'scrap', label: $t('common.scrapped'), num: true },
  { key: 'scrap_rate', label: $t('heats.scrapRate'), format: 'pct', digits: 2, num: true }, { key: 'rework_rate', label: $t('machDefects.reworkRate'), format: 'pct', num: true }, { key: 'causes', label: $t('common.scrapReason'), sortable: false }];
const tot = computed(() => ({ total: (d.value?.machines || []).reduce((a: number, m: any) => a + (m.total || 0), 0), scrap: (d.value?.machines || []).reduce((a: number, m: any) => a + (m.scrap || 0), 0) }));
const worst = computed(() => [...(d.value?.machines || [])].sort((a, b) => (b.scrap_rate || 0) - (a.scrap_rate || 0))[0]);
async function load() { d.value = await get('/api/machining/defects?days=' + days.value); }
async function importCmm() { const r = await post('/api/machining/cmm_import', { csv: csv.value }); ElMessage.success($t('machDefects.imported0Parts1Values', [r.records, r.values])); cmmOpen.value = false; load(); }
onMounted(load);
const opt = computed(() => ({ tooltip: { trigger: 'axis', valueFormatter: (v: number) => fmtPct(v, 2) }, legend: {},
  xAxis: { type: 'category', data: d.value.machines.map((m: any) => m.machine) },
  yAxis: { type: 'value', axisLabel: { formatter: (v: number) => (v * 100).toFixed(1) + '%' } },
  series: [{ name: $t('heats.scrapRate'), type: 'bar', data: d.value.machines.map((m: any) => m.scrap_rate), itemStyle: { color: CH.bad } },
    { name: $t('machDefects.reworkRate'), type: 'bar', data: d.value.machines.map((m: any) => m.rework_rate), itemStyle: { color: CH.amber } }] }));
</script>

<style scoped>
.split { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
@media (max-width: 1279px) { .split { grid-template-columns: minmax(0, 1fr); } }
</style>
