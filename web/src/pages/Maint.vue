<template>
  <div class="page">
    <HmPageHead :title="$t('common.checksPm')" :sub="$t('maint.dueAndOverdueInspectionPm')" art="equipment">
      <span data-tour="maint-links" class="links">
        <el-button @click="router.push('/e/maint_plan')"><el-icon><Calendar /></el-icon>{{ $t('maint.pmPlans') }}</el-button>
        <el-button @click="router.push('/e/maint_record')"><el-icon><Tickets /></el-icon>{{ $t('maint.executionRecords') }}</el-button>
        <el-button @click="router.push('/e/repair_order')"><el-icon><SetUp /></el-icon>{{ $t('maint.repairOrders') }}</el-button>
      </span>
    </HmPageHead>

    <div class="grid g4" data-tour="kpis">
      <HmKpi :label="$t('maint.dueToday')" :value="rows.length" format="int" :unit="$t('common.k2360')" :loading="loading" :hint="$t('maint.includingOverdue')" />
      <HmKpi :label="$t('common.late')" :value="overdue.length" format="int" :unit="$t('common.k2360')" :status="overdue.length ? 'bad' : 'ok'" :loading="loading" :hint="overdue.length ? $t('maint.longest0Days', [maxOver]) : $t('maint.nothingOverdue')" />
      <HmKpi :label="$t('maint.machinesInvolved')" :value="eqCount" format="int" :unit="$t('overviewTab.machines')" :loading="loading" />
      <HmKpi :label="$t('implPacks.dailyInspectionRounds')" :value="rows.filter((r) => r.mtype === 'spot').length" format="int" :unit="$t('common.k2360')" :loading="loading" :hint="$t('maint.pm0Lubrication1', [rows.filter((r) => r.mtype === 'maint').length, rows.filter((r) => r.mtype === 'lube').length])" />
    </div>

    <HmToolbar class="mt">
      <el-segmented v-model="type" :options="TYPES" />
      <el-input v-model="kw" :placeholder="$t('maint.equipmentPlan')" clearable class="kw"><template #prefix><el-icon><Search /></el-icon></template></el-input>
    </HmToolbar>

    <div class="hm-card tbl mt" data-tour="maint-table">
      <el-table :data="shown" size="small" v-loading="loading" :row-class-name="({ row }: any) => (row.overdue_days > 0 ? 'od' : '')"
        v-ai-rows="{ entity: 'maint_plan', rows: () => shown, label: (r: any) => `${r.name} ${r.equipment__label || ''}${r.overdue_days > 0 ? $t('maint.overdue0Days', [r.overdue_days]) : ''}`,
          data: (r: any) => ({ [$t('common.plan')]: r.code, [$t('common.name')]: r.name, [$t('common.equipment')]: r.equipment__label, [$t('common.type')]: MT[r.mtype], [$t('common.content')]: r.items, [$t('common.expires')]: r.next_due, [$t('erpPurchaseBoard.daysOverdue')]: r.overdue_days, equipment_id: r.equipment }) }">
        <template #empty><HmEmpty compact :icon="kw || type ? 'search' : 'data'" :title="kw || type ? $t('maint.noMatchingTasks') : $t('maint.noChecksOrPmDue')" /></template>
        <el-table-column prop="code" :label="$t('common.plan')" width="150"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
        <el-table-column prop="name" :label="$t('common.name')" min-width="140" />
        <el-table-column prop="equipment__label" :label="$t('common.equipment')" min-width="140" />
        <el-table-column :label="$t('common.type')" width="96"><template #default="{ row }"><HmTag :tone="row.mtype === 'spot' ? 'primary' : row.mtype === 'lube' ? 'amber' : 'neutral'">{{ MT[row.mtype] || row.mtype }}</HmTag></template></el-table-column>
        <el-table-column prop="items" :label="$t('common.content')" min-width="220" show-overflow-tooltip />
        <el-table-column :label="$t('common.expires')" width="150"><template #default="{ row }">
          <span class="hm-num">{{ row.next_due || $t('maint.unscheduled') }}</span><HmTag v-if="row.overdue_days > 0" tone="bad" class="od-t">{{ $t('common.n0DaysOverdue', [row.overdue_days]) }}</HmTag></template></el-table-column>
        <el-table-column :label="$t('maint.execute')" width="156" fixed="right"><template #default="{ row }">
          <el-button size="small" type="primary" plain @click="exec(row, 'ok')">{{ $t('common.normal') }}</el-button><el-button size="small" type="danger" plain @click="exec(row, 'abnormal')">{{ $t('common.abnormal') }}</el-button></template></el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { get, post } from '../api';
import './aiActions';

const router = useRouter(), rows = ref<any[]>([]), loading = ref(true), type = ref(''), kw = ref('');
const MT: Record<string, string> = { spot: $t('implPacks.dailyInspectionRounds'), maint: $t('maint.periodicPm'), lube: $t('maint.lubrication') };
const TYPES = [{ label: $t('common.all'), value: '' }, ...Object.entries(MT).map(([value, label]) => ({ label, value }))];
const overdue = computed(() => rows.value.filter((r) => r.overdue_days > 0));
const maxOver = computed(() => Math.max(0, ...overdue.value.map((r) => r.overdue_days)));
const eqCount = computed(() => new Set(rows.value.map((r) => r.equipment)).size);
const shown = computed(() => { const k = kw.value.trim().toLowerCase(); return rows.value.filter((r) => (!type.value || r.mtype === type.value) && (!k || `${r.code} ${r.name} ${r.equipment__label}`.toLowerCase().includes(k))); });
async function load() { loading.value = true; try { rows.value = await get('/api/equipment/maint_due'); } finally { loading.value = false; } }
async function exec(r: any, result: string) {
  let notes = '';
  if (result === 'abnormal') notes = (await ElMessageBox.prompt($t('maint.describeTheAbnormalityARepair'), r.name)).value;
  const res = await post(`/api/e/maint_plan/${r.id}/do/execute`, { result, notes });
  ElMessage.success(res.msg || $t('common.recorded')); load();
}
onMounted(load);
</script>

<style scoped>
.links { display: inline-flex; gap: var(--hm-sp-2); flex-wrap: wrap; }
.kw { width: calc(200px * var(--hm-scale)); }
.tbl { padding: 0; overflow: hidden; }
.od-t { margin-left: 6px; }
.tbl :deep(tr.od > td:first-child) { box-shadow: inset 3px 0 0 var(--hm-fault); }
</style>
