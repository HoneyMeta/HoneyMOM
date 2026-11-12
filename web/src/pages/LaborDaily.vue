<template>
  <div class="page ld">
    <HmPageHead :title="$t('laborDaily.operatorDailyReportsLaborHours')" :sub="$t('laborDaily.operatorsSubmitOneDailyReport')" />
    <el-tabs v-model="tab" @tab-change="(t: any) => router.replace('/labor/' + (t === 'hours' ? 'hours' : 'daily') + (t === 'fill' ? '?fill=1' : ''))" data-test="ld-tabs">
      <el-tab-pane :label="$t('laborDaily.reviewReports')" name="review" /><el-tab-pane :label="$t('laborDaily.fillInReport')" name="fill" /><el-tab-pane :label="$t('nav.laborHours')" name="hours" />
    </el-tabs>

    <template v-if="tab === 'review'">
      <HmToolbar>
        <el-radio-group v-model="st" @change="loadList" data-test="ld-status">
          <el-radio-button value="submitted">{{ $t('laborDaily.pending0', [counts.submitted || 0]) }}</el-radio-button><el-radio-button value="approved">{{ $t('laborDaily.approved0', [counts.approved || 0]) }}</el-radio-button>
          <el-radio-button value="rejected">{{ $t('laborDaily.returned0', [counts.rejected || 0]) }}</el-radio-button><el-radio-button value="">{{ $t('common.all') }}</el-radio-button>
        </el-radio-group>
        <template #right><span class="muted small">{{ $t('laborDaily.onlyTeamLeadersOrPlant') }}</span></template>
      </HmToolbar>
      <HmCard flush class="mt">
        <el-table :data="list" size="small" @row-click="(r: any) => (cur = r)" data-test="ld-table" highlight-current-row
          v-ai-rows="{ entity: 'worker_daily', rows: () => list, label: (r: any) => $t('laborDaily.n01DailyReportGood', [r.employee__label, r.day, r.qty_good, r.hours]), data: (r: any) => ({ [$t('common.employees')]: r.employee__label, [$t('common.date')]: r.day, [$t('common.shift')]: r.shift__label, [$t('common.good')]: r.qty_good, [$t('common.defective')]: r.qty_ng, [$t('laborDaily.laborHours')]: r.hours, [$t('laborDaily.downtimeMin')]: r.downtime_min, [$t('common.notes')]: r.remark, [$t('common.status')]: DST[r.status] }) }">
          <el-table-column prop="code" :label="$t('laborDaily.reportNo')" width="130" /><el-table-column prop="day" :label="$t('common.date')" width="100" />
          <el-table-column prop="shift__label" :label="$t('common.shift')" width="90" /><el-table-column prop="employee__label" :label="$t('common.employees')" width="120" /><el-table-column prop="team__label" :label="$t('common.teams')" width="90" />
          <el-table-column :label="$t('common.good')" width="70" align="right"><template #default="{ row }"><span class="hm-num">{{ row.qty_good }}</span></template></el-table-column>
          <el-table-column :label="$t('common.defective')" width="60" align="right"><template #default="{ row }"><span class="hm-num">{{ row.qty_ng }}</span></template></el-table-column>
          <el-table-column :label="$t('dailyForm.hoursH')" width="70" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.hours, 1) }}</span></template></el-table-column>
          <el-table-column :label="$t('dailyForm.downtimeMin')" width="80" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.downtime_min, 0) }}</span></template></el-table-column>
          <el-table-column prop="remark" :label="$t('common.notes')" min-width="140" show-overflow-tooltip />
          <el-table-column :label="$t('common.status')" width="80"><template #default="{ row }"><HmTag :tone="row.status === 'approved' ? 'ok' : row.status === 'rejected' ? 'bad' : 'warn'">{{ DST[row.status] }}</HmTag></template></el-table-column>
          <el-table-column label="" width="150"><template #default="{ row }">
            <template v-if="row.status === 'submitted'">
              <el-button size="small" type="primary" plain data-test="ld-approve" @click.stop="decide(row, 'approve')">{{ $t('common.approve') }}</el-button>
              <el-button size="small" plain data-test="ld-reject" @click.stop="decide(row, 'reject')">{{ $t('common.return') }}</el-button>
            </template><span v-else class="muted small">{{ row.reviewer }}</span>
          </template></el-table-column>
        </el-table>
      </HmCard>
      <HmCard v-if="cur" :title="$t('laborDaily.detailsFor01', [cur.employee__label, cur.day])" class="mt" flush>
        <el-table :data="cur.lines || []" size="small"><el-table-column prop="work_order" :label="$t('common.workOrder')" width="140" /><el-table-column prop="process" :label="$t('common.operation')" />
          <el-table-column prop="equipment" :label="$t('common.equipment')" width="100" /><el-table-column prop="good" :label="$t('common.good')" width="80" align="right" /><el-table-column prop="ng" :label="$t('common.defective')" width="70" align="right" />
          <el-table-column prop="source" :label="$t('common.source')" width="160" /></el-table>
        <p v-if="cur.review_note" class="note">{{ $t('laborDaily.reviewComment0', [cur.review_note]) }}</p>
      </HmCard>
    </template>

    <template v-else-if="tab === 'fill'">
      <DailyForm :employee="fillEmp" @done="tab = 'review'; loadList()">
        <el-select v-model="fillEmp" filterable :placeholder="$t('laborDaily.me')" clearable style="width: 220px"><el-option v-for="e in emps" :key="e.id" :label="`${e.code} ${e.name}`" :value="e.id" /></el-select>
      </DailyForm>
    </template>

    <template v-else>
      <HmToolbar>
        <el-radio-group v-model="by" @change="loadHours" data-test="ld-by"><el-radio-button value="worker">{{ $t('laborDaily.byPerson') }}</el-radio-button><el-radio-button value="team">{{ $t('laborDaily.byTeam') }}</el-radio-button><el-radio-button value="day">{{ $t('laborDaily.byDay') }}</el-radio-button></el-radio-group>
        <el-date-picker v-model="rng" type="daterange" value-format="YYYY-MM-DD" :range-separator="$t('laborDaily.to')" style="width: 260px" @change="loadHours" />
      </HmToolbar>
      <div class="grid g4 mt" v-if="hrs">
        <HmKpi :label="$t('laborDaily.approvedHours')" :value="hrs.total.approved_h" unit="h" :digits="1" />
        <HmKpi :label="$t('laborDaily.hoursPendingReview')" :value="hrs.total.pending_h" unit="h" :digits="1" :status="hrs.total.pending_h ? 'warn' : ''" />
        <HmKpi :label="$t('laborDaily.clockedHours')" :value="hrs.total.clock_h" unit="h" :digits="1" :hint="$t('laborDaily.clockInToClockOut')" />
        <HmKpi :label="$t('laborDaily.goodQtyInReports')" :value="hrs.total.good" format="int" :unit="$t('common.pcs')" />
      </div>
      <HmCard flush class="mt" v-if="hrs">
        <HmDataTable :columns="hcols" :rows="hrs.rows" max-height="calc(100vh - 380px)" :row-label="(r: any) => $t('laborDaily.n0LaborHours1H', [r.key, r.approved_h])" data-test="ld-hours" />
      </HmCard>
    </template>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { get, post } from '../api';
import DailyForm from '../components/biz/DailyForm.vue';
import { fmtNum } from '../design';

const router = useRouter(), route = useRoute();
const DST: Record<string, string> = { draft: $t('common.draft'), submitted: $t('dailyForm.pendingReview'), approved: $t('dailyForm.approved'), rejected: $t('dailyForm.returned') };
const tab = ref(route.path.endsWith('/hours') ? 'hours' : route.query.fill ? 'fill' : 'review');
const st = ref('submitted'), list = ref<any[]>([]), counts = ref<any>({}), cur = ref<any>(null), emps = ref<any[]>([]), fillEmp = ref<number | null>(null);
const by = ref('worker'), rng = ref<string[] | null>(null), hrs = ref<any>(null);
const hcols = [{ key: 'key', label: $t('laborDaily.personTeamDay') }, { key: 'people', label: $t('laborDaily.headcount'), num: true, format: 'int' }, { key: 'approved_h', label: $t('laborDaily.approvedHours'), num: true, unit: 'h', digits: 1 },
  { key: 'pending_h', label: $t('laborDaily.hoursPendingReview'), num: true, unit: 'h', digits: 1 }, { key: 'clock_h', label: $t('laborDaily.clockedHours2'), num: true, unit: 'h', digits: 1 },
  { key: 'good', label: $t('common.good'), num: true, format: 'int' }, { key: 'ng', label: $t('common.defective'), num: true, format: 'int' }, { key: 'per_hour', label: $t('laborDaily.pcsLaborHour'), num: true, digits: 1 },
  { key: 'downtime_min', label: $t('dailyForm.downtimeMin'), num: true, digits: 0 }, { key: 'reports', label: $t('laborDaily.reports'), num: true, format: 'int' }];

async function loadList() {
  const r = await get('/api/labor/daily?' + new URLSearchParams(st.value ? { status: st.value } : {}));
  list.value = r.items; counts.value = r.counts;
  cur.value = list.value.find((x) => x.id === Number(route.query.id)) || null;
}
async function decide(row: any, a: string) {
  let note = '';
  if (a === 'reject') note = (await ElMessageBox.prompt($t('laborDaily.reasonForReturnRequired'), $t('laborDaily.return0SDailyReport', [row.employee__label]), { inputValidator: (v: string) => !!v?.trim() || $t('laborDaily.pleaseEnterAReasonFor') })).value;
  const r = await post(`/api/e/worker_daily/${row.id}/do/${a}`, { note }); ElMessage.success(r.msg); await loadList();
}
async function loadHours() {
  const qs = new URLSearchParams({ by: by.value });
  if (rng.value) { qs.set('frm', rng.value[0]); qs.set('to', rng.value[1]); }
  hrs.value = await get('/api/labor/hours?' + qs);
}
watch(tab, (t) => { if (t === 'hours') loadHours(); if (t === 'review') loadList(); });
onMounted(async () => {
  emps.value = (await get('/api/station/options')).employees;
  if (tab.value === 'hours') loadHours(); else loadList();
});
</script>

<style scoped>
.note { padding: 8px 14px; color: var(--hm-text-2); }
.muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-2xs); }
</style>
