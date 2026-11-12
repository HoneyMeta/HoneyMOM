<template>
  <div class="page">
    <HmPageHead :title="$t('charge.meltChargeOptimization')" :sub="$t('charge.linearProgrammingFindsTheLowest')" art="foundry">
      <el-button @click="router.push('/e/charge_material')"><el-icon><Coin /></el-icon>{{ $t('charge.chargeMaterialPricesChemistry') }}</el-button>
      <el-button @click="router.push('/e/charge_plan')"><el-icon><Tickets /></el-icon>{{ $t('charge.previousSolutions') }}</el-button>
    </HmPageHead>

    <div class="grid lay">
      <HmCard :title="$t('charge.targetChemistry')" :sub="$t('charge.baseIron')" data-tour="charge-target">
        <el-form label-width="64px" size="small" class="tf">
          <el-form-item :label="$t('charge.chargeWeight')"><el-input-number v-model="kg" :step="100" :min="100" controls-position="right" class="kg" /><span class="hm-unit">kg</span></el-form-item>
          <el-form-item v-for="e in ['C', 'Si', 'Mn', 'S', 'P']" :key="e" :label="e">
            <div class="rg"><el-input-number v-model="t[e + '_lo']" :controls="false" :placeholder="$t('charge.min')" /><span>~</span><el-input-number v-model="t[e + '_hi']" :controls="false" :placeholder="$t('charge.max')" /></div>
          </el-form-item>
        </el-form>
        <div class="fb">
          <el-button type="primary" :loading="busy" @click="run(false)"><el-icon><Cpu /></el-icon>{{ $t('charge.solve') }}</el-button>
          <el-button :disabled="!r?.ok" @click="run(true)">{{ $t('charge.saveAsCurrentMix') }}</el-button>
        </div>
        <p class="small muted">{{ $t('charge.onceSavedChargeMaterialStock') }}</p>
      </HmCard>

      <div class="res" data-tour="charge-result">
        <HmSkeleton v-if="!r" variant="table" :rows="5" />
        <template v-else-if="r.ok">
          <el-alert v-if="r.stock_note" type="warning" :closable="false" show-icon :title="r.stock_note" style="margin-bottom: var(--hm-gap)" data-test="charge-stock-note" />
          <div class="grid g3">
            <HmKpi :label="$t('charge.optimizedChargeCost')" :value="r.cost" format="int" :unit="$t('charge.cnyT')" status="ok" />
            <HmKpi :label="$t('charge.ruleOfThumbMixCost')" :value="r.base_cost" format="int" :unit="$t('charge.cnyT')" />
            <HmKpi :label="$t('charge.savingPerTonne')" :value="r.saving_per_t" format="int" :unit="$t('common.cny')" :status="r.saving_per_t > 0 ? 'ok' : ''" :hint="$t('charge.at0TMeltedPer', [dayT, Math.round(r.saving_per_t * dayT * 300 / 10000)])" />
          </div>
          <div class="grid g2 mt">
            <HmCard :title="$t('charge.mix')" sub="%"><Chart :option="pie" :height="260" :ai="$t('charge.optimizedMix')" /></HmCard>
            <HmCard :title="$t('common.details')" flush>
              <HmDataTable :columns="COLS" :rows="r.detail" :ai="(x: any) => ({ kind: 'row', label: `${x.name} ${x.pct}% · ${x.kg} kg`, data: x })" />
              <template #footer><span>{{ $t('charge.predictedChemistry') }}</span><span v-for="(v, k) in r.comp" :key="k" class="cp"><b>{{ k }}</b> <span class="hm-num">{{ v }}</span></span></template>
            </HmCard>
          </div>
        </template>
        <HmCard v-else><HmEmpty icon="search" :title="$t('charge.noFeasibleSolution')" :desc="r.msg" /></HmCard>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { post } from '../api';
import Chart from '../components/Chart.vue';

const router = useRouter();
const kg = ref(3000), busy = ref(false), r = ref<any>(null), dayT = 40;
const t = reactive<any>({ C_lo: 3.6, C_hi: 3.8, Si_lo: 1.4, Si_hi: 1.7, Mn_lo: null, Mn_hi: 0.4, S_lo: null, S_hi: 0.02, P_lo: null, P_hi: 0.05 });
const COLS = [{ key: 'name', label: $t('charge.chargeMaterial') }, { key: 'pct', label: $t('charge.ratio'), num: true }, { key: 'kg', label: $t('charge.kgPerHeat'), num: true }, { key: 'cost', label: $t('charge.costContributionCnyT'), num: true }];
async function run(save: boolean) {
  busy.value = true;
  try { r.value = await post('/api/foundry/optimize', { furnace_kg: kg.value, target: t, save }); if (save && r.value.ok) ElMessage.success($t('charge.savedAsTheCurrentCharge')); } finally { busy.value = false; }
}
const pie = computed(() => ({ series: [{ type: 'pie', radius: ['44%', '64%'], data: r.value.detail.map((x: any) => ({ name: x.name, value: x.pct })) }] }));
run(false);
</script>

<style scoped>
.lay { grid-template-columns: minmax(280px, 1fr) minmax(0, 2.2fr); align-items: start; }
@media (max-width: 1279px) { .lay { grid-template-columns: minmax(0, 1fr); } }
.res { min-width: 0; }
.tf :deep(.el-form-item) { margin-bottom: 10px; }
.kg { width: calc(130px * var(--hm-scale)); }
.rg { display: flex; align-items: center; gap: 6px; } .rg :deep(.el-input-number) { width: calc(84px * var(--hm-scale)); } .rg span { color: var(--hm-text-3); }
.fb { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px; }
.cp { margin-left: 12px; }
</style>
