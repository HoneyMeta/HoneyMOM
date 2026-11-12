<template>
  <div class="page">
    <HmPageHead :title="$t('common.toolLifeMonitoring')" :sub="d?.ai ? $t('tools.readsCncSpindleLoadUses', [d.vb_limit]) : $t('tools.managedByToolLifeCount')">
      <template #meta><HmTag v-if="d?.ai" tone="ai" icon="MagicStick">{{ $t('tools.aiWearEstimate') }}</HmTag></template>
      <el-button @click="router.push('/e/tool')"><el-icon><Tickets /></el-icon>{{ $t('tools.toolRegister') }}</el-button>
    </HmPageHead>

    <div class="grid g4" data-tour="kpis">
      <HmKpi :label="$t('tools.tools')" :value="d?.tools.length" format="int" :unit="$t('gauges.k3208')" :loading="!d" />
      <HmKpi :label="$t('hmStatus.warning')" :value="d ? cnt('warn') : null" format="int" :unit="$t('gauges.k3208')" :status="d && cnt('warn') ? 'warn' : ''" :loading="!d" :hint="$t('tools.life90')" />
      <HmKpi :label="$t('tools.dueForChange')" :value="d ? cnt('change') : null" format="int" :unit="$t('gauges.k3208')" :status="d && cnt('change') ? 'bad' : 'ok'" :loading="!d" />
      <HmKpi :label="$t('tools.breakagesLast7Days')" :value="d ? broken : null" format="int" :unit="$t('common.times')" :status="broken ? 'bad' : ''" :loading="!d" />
    </div>

    <div class="grid split mt">
      <div class="hm-card tbl" data-tour="tools-table">
        <el-table :data="d?.tools || []" size="small" v-loading="!d" highlight-current-row max-height="520" @row-click="pick"
          v-ai-rows="{ entity: 'tool', rows: () => d?.tools || [], label: (r: any) => $t('tools.n012Life3', [r.equipment__label, r.code, r.name, r.used || 0, r.life_limit]),
            data: (r: any) => ({ [$t('common.machineTool')]: r.equipment__label, [$t('screen.toolPos')]: r.code, [$t('tools.tool')]: r.name, [$t('screen.used')]: r.used, [$t('common.life')]: r.life_limit, [$t('common.status')]: ST[r.status], [$t('tools.estimatedWearVb')]: r.est?.vb_est, [$t('tools.wear')]: r.est?.wear_pct, equipment_id: r.equipment }) }">
          <template #empty><HmEmpty compact :title="$t('tools.noToolsYet')" /></template>
          <el-table-column prop="equipment__label" :label="$t('common.machineTool')" min-width="120" />
          <el-table-column prop="code" :label="$t('screen.toolPos')" width="64"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
          <el-table-column prop="name" :label="$t('tools.tool')" min-width="120" />
          <el-table-column :label="$t('tools.lifeCount')" min-width="170"><template #default="{ row }">
            <div class="lf"><i :class="row.status" :style="{ width: Math.min(100, (row.used || 0) / (row.life_limit || 1) * 100) + '%' }" /></div>
            <span class="hm-num small">{{ fmtNum(row.used || 0) }} / {{ fmtNum(row.life_limit) }}</span></template></el-table-column>
          <el-table-column v-if="d?.ai" :label="$t('tools.aiWearEstimate')" min-width="170"><template #default="{ row }">
            <template v-if="row.est"><div class="lf"><i :class="row.est.wear_pct >= 100 ? 'change' : row.est.wear_pct >= 80 ? 'warn' : 'ok'" :style="{ width: Math.min(100, row.est.wear_pct) + '%' }" /></div>
              <span class="hm-num small">VB≈{{ row.est.vb_est }} mm</span><span class="muted small"> · {{ REF[row.est.ref] || '' }}</span></template>
            <span v-else class="muted small">{{ $t('tools.notEnoughSamples') }}</span></template></el-table-column>
          <el-table-column v-if="d?.ai" :label="$t('tools.simulatedTrueVb')" width="100" align="right"><template #default="{ row }"><span class="hm-num muted">{{ row.vb_truth ?? '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('common.status')" width="86"><template #default="{ row }"><HmTag :tone="row.status === 'change' ? 'bad' : row.status === 'warn' ? 'warn' : 'ok'">{{ ST[row.status] }}</HmTag></template></el-table-column>
          <el-table-column prop="changes" :label="$t('qualityGates.toolChange')" width="60" align="right" />
        </el-table>
        <p v-if="d?.ai" class="note small muted">{{ $t('tools.simulatedTrueValueExistsOnly') }}</p>
      </div>
      <HmCard :title="cur ? `${cur.equipment__label} ${cur.code} ${cur.name}` : $t('tools.cuttingLoad')" :sub="cur?.est ? $t('tools.baseline0Recent1', [cur.est.base, cur.est.recent]) : ''">
        <Chart v-if="cur?.est" :option="opt" :height="260" :ai="$t('tools.n01SpindleLoad', [cur.equipment__label, cur.code])" />
        <HmEmpty v-else compact :icon="d?.ai ? 'data' : 'ai'" :title="d?.ai ? $t('tools.selectAToolWithLoad') : $t('tools.aiWearEstimationIsOff')" :desc="d?.ai ? $t('tools.clickARowInThe') : $t('tools.withToolLifeEstimationOn')" />
      </HmCard>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { get } from '../api';
import Chart from '../components/Chart.vue';
import { CH, targetLine } from '../design/chart';
import { fmtNum } from '../design/format';
import './aiActions';

const router = useRouter();
const d = ref<any>(null), cur = ref<any>(null);
const ST: Record<string, string> = { ok: $t('common.normal'), warn: $t('hmStatus.warning'), change: $t('tools.dueForChange') };
const REF: Record<string, string> = { own: $t('tools.ownBaseline'), peer: $t('tools.borrowedBaselineSameToolPosition'), midlife: $t('tools.staleBaselineConservative') };
const cnt = (s: string) => d.value.tools.filter((t: any) => t.status === s).length;
const broken = computed(() => (d.value?.changes || []).filter((c: any) => c.reason === 'broken').reduce((a: number, c: any) => a + c.n, 0));
const pick = (r: any) => { cur.value = r; };
onMounted(async () => { d.value = await get('/api/machining/tools'); cur.value = d.value.tools.find((t: any) => t.est) || null; });
const opt = computed(() => ({ tooltip: { trigger: 'axis' }, xAxis: { type: 'category', data: cur.value.est.series.map((p: any) => p.t.slice(11, 16)) },
  yAxis: { type: 'value', scale: true, name: '%' }, series: [{ name: $t('common.spindleLoad'), type: 'line', data: cur.value.est.series.map((p: any) => p.v), itemStyle: { color: CH.primary },
    areaStyle: { color: 'rgba(20,99,214,.08)' }, markLine: { data: [{ yAxis: cur.value.est.base, ...targetLine($t('tools.baselineAfterToolChange')) }] } }] }));
</script>

<style scoped>
.split { grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr); align-items: start; }
@media (max-width: 1279px) { .split { grid-template-columns: minmax(0, 1fr); } }
.tbl { padding: 0; overflow: hidden; }
.note { margin: 0; padding: 8px 12px; border-top: 1px solid var(--hm-border); background: var(--hm-surface-2); }
.lf { height: 5px; border-radius: 3px; background: var(--hm-surface-3); overflow: hidden; margin: 2px 0; }
.lf i { display: block; height: 100%; border-radius: 3px; background: var(--hm-run); } .lf i.warn { background: var(--hm-idle); } .lf i.change { background: var(--hm-fault); }
</style>
