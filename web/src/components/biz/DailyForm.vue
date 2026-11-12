<template>
  <div class="df" :class="{ mobile }" data-test="daily-form">
    <div class="top">
      <el-date-picker v-model="day" type="date" value-format="YYYY-MM-DD" :size="mobile ? 'large' : 'default'" style="width: 150px" @change="touched = true; prefill()" />
      <el-select v-model="shift" :placeholder="$t('common.shift')" :size="mobile ? 'large' : 'default'" style="width: 120px" @change="touched = true; prefill()"><el-option v-for="s in shifts" :key="s.id" :label="`${s.name} ${s.start}–${s.end}`" :value="s.id" /></el-select>
      <slot />
      <el-button :size="mobile ? 'large' : 'default'" @click="prefill"><el-icon><Refresh /></el-icon>{{ $t('dailyForm.prefillAgain') }}</el-button>
    </div>
    <el-alert v-if="err" type="warning" :closable="false" :title="err" class="mt-s" />
    <template v-if="p">
      <div class="who">{{ p.employee.name }} · {{ p.window[0].slice(5, 16) }} – {{ p.window[1].slice(11, 16) }}
        <HmTag v-if="p.existing" :tone="p.existing.status === 'approved' ? 'ok' : p.existing.status === 'rejected' ? 'bad' : 'warn'">{{ DST[p.existing.status] }}</HmTag></div>
      <el-alert v-if="locked" type="success" :closable="false" :title="$t('dailyForm.approvedBy0CanNo', [p.existing.reviewer || $t('skillSaveDialog.teamLeader')])" class="mt-s" />
      <el-alert v-if="p.existing?.status === 'rejected'" type="error" :closable="false" :title="$t('dailyForm.returned0', [p.existing.review_note || ''])" class="mt-s" />
      <div class="nums">
        <label><span>{{ $t('common.good') }}</span><el-input-number v-model="f.qty_good" :disabled="locked" :min="0" :size="mobile ? 'large' : 'default'" data-test="daily-good" /></label>
        <label><span>{{ $t('common.defective') }}</span><el-input-number v-model="f.qty_ng" :disabled="locked" :min="0" :size="mobile ? 'large' : 'default'" /></label>
        <label><span>{{ $t('dailyForm.hoursH') }}</span><el-input-number v-model="f.hours" :disabled="locked" :min="0" :max="16" :step="0.5" :precision="2" :size="mobile ? 'large' : 'default'" data-test="daily-hours" /></label>
        <label><span>{{ $t('dailyForm.downtimeMin') }}</span><el-input-number v-model="f.downtime_min" :disabled="locked" :min="0" :size="mobile ? 'large' : 'default'" /></label>
      </div>
      <p class="muted small">{{ $t('dailyForm.prefilled0ProductionReports1', [p.lines.length, p.qty_good, p.qty_ng, p.hours, p.downtime_min]) }}</p>
      <el-table v-if="p.lines.length" :data="p.lines" size="small" max-height="240">
        <el-table-column prop="work_order" :label="$t('common.workOrder')" width="130" /><el-table-column prop="process" :label="$t('common.operation')" /><el-table-column prop="equipment" :label="$t('common.equipment')" width="80" />
        <el-table-column prop="good" :label="$t('common.good')" width="70" align="right" /><el-table-column prop="ng" :label="$t('common.defective')" width="60" align="right" /><el-table-column prop="source" :label="$t('common.source')" width="130" />
      </el-table>
      <el-input v-model="f.remark" :disabled="locked" type="textarea" :rows="3" :placeholder="$t('dailyForm.notesIssuesChangeoversWaitingFor')" class="mt-s" data-test="daily-remark" />
      <div class="btns">
        <el-button :size="mobile ? 'large' : 'default'" :disabled="locked" @click="submit(false)">{{ $t('dailyForm.saveDraft') }}</el-button>
        <el-button type="primary" :size="mobile ? 'large' : 'default'" :disabled="locked" :loading="busy" data-test="daily-submit" @click="submit(true)">{{ $t('dailyForm.submitToTeamLeader') }}</el-button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// Operator daily report form (shared by the desktop "Operator Daily Report" page and mobile "My Daily Report"): prefilled by date + shift from production reports, clock-in/out and equipment downtime; editable before submit.
import { ElMessage } from 'element-plus';
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { get, post } from '../../api';

const props = withDefaults(defineProps<{ employee?: number | null; mobile?: boolean }>(), { employee: null, mobile: false });
const emit = defineEmits<{ done: [] }>();
const DST: Record<string, string> = { draft: $t('common.draft'), submitted: $t('dailyForm.pendingReview'), approved: $t('dailyForm.approved'), rejected: $t('dailyForm.returned') };
const touched = ref(false), day = ref(''), shift = ref<number | null>(null), shifts = ref<any[]>([]), p = ref<any>(null), err = ref(''), busy = ref(false);
const f = reactive<any>({ qty_good: 0, qty_ng: 0, hours: 0, downtime_min: 0, remark: '' });
const locked = computed(() => p.value?.existing?.status === 'approved');

async function prefill() {
  err.value = '';
  try {
    const qs = new URLSearchParams();      // If not chosen manually, the server sets the date from the current shift (after midnight on night shift counts as the previous day)
    if (touched.value && day.value) qs.set('day', day.value);
    if (touched.value && shift.value) qs.set('shift', String(shift.value));
    if (props.employee) qs.set('employee', String(props.employee));
    p.value = await get('/api/labor/daily/prefill?' + qs, true);
    shift.value = p.value.shift; day.value = p.value.day;
    const ex = p.value.existing;
    Object.assign(f, ex && ex.status !== 'draft' ? { qty_good: ex.qty_good, qty_ng: ex.qty_ng, hours: ex.hours, downtime_min: ex.downtime_min, remark: ex.remark || '' }
      : { qty_good: p.value.qty_good, qty_ng: p.value.qty_ng, hours: p.value.hours, downtime_min: p.value.downtime_min, remark: ex?.remark || '' });
  } catch (e: any) { p.value = null; err.value = e.message; }
}
async function submit(sub: boolean) {
  busy.value = true;
  try {
    const r = await post('/api/labor/daily', { day: day.value, shift: shift.value, employee: p.value.employee.id, ...f, lines: p.value.lines, submit: sub });
    ElMessage.success(sub ? $t('dailyForm.submitted0WaitingForTeam', [r.code]) : $t('dailyForm.draftSaved')); await prefill(); if (sub) emit('done');
  } finally { busy.value = false; }
}
watch(() => props.employee, prefill);
onMounted(async () => {
  shifts.value = (await get('/api/e/shift?size=20&sort=code')).items;
  await prefill();
});
</script>

<style scoped>
.top { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.mobile .top > :deep(.el-date-editor), .mobile .top > :deep(.el-select) { flex: 1 1 140px; width: auto !important; }
.who { margin: 12px 0 6px; font-weight: 600; display: flex; gap: 8px; align-items: center; }
.nums { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 8px 0; }
.nums label { display: flex; flex-direction: column; gap: 4px; font-size: var(--hm-fs-xs); color: var(--hm-text-3); } .nums :deep(.el-input-number) { width: 100%; }
.mobile .nums { grid-template-columns: 1fr 1fr; }
.btns { display: flex; gap: 8px; justify-content: flex-end; margin-top: 10px; } .mobile .btns .el-button { flex: 1; }
.mt-s { margin-top: 8px; } .muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-2xs); }
</style>
