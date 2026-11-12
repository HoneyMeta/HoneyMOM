<template>
  <div class="page" data-test="impl-home">
    <HmPageHead :title="$t('common.implementationGuide')" :sub="$t('implHome.trackAProjectWithThe')">
      <el-select v-model="project" filterable allow-create default-first-option style="width:180px" data-test="impl-project" @change="load">
        <el-option v-for="p in sum.projects" :key="p" :value="p" :label="p" /></el-select>
      <el-button v-perm="'impl.impl_task.create'" type="primary" data-test="impl-init" @click="initDlg = true">{{ $t('implHome.createTasksFromStandardMethod') }}</el-button>
    </HmPageHead>

    <div class="top">
      <HmCard :title="$t('implHome.overallProgress')" :sub="$t('implHome.n01Items', [sum.done || 0, sum.total || 0])" class="prog" data-test="impl-progress">
        <el-progress type="dashboard" :percentage="Math.round((sum.progress || 0) * 100)" :width="120" />
        <div class="small" :class="{ bad: sum.overdue }">{{ $t('implHome.n0Overdue', [sum.overdue || 0]) }}</div>
      </HmCard>
      <HmCard :title="$t('implHome.phases')" class="ph">
        <div class="phases">
          <div v-for="p in sum.phases" :key="p.phase" class="pp" :class="{ done: p.done === p.total, late: p.overdue }" :data-test="'impl-phase-' + p.phase">
            <b>{{ p.label }}</b><el-progress :percentage="p.total ? Math.round(p.done / p.total * 100) : 0" :stroke-width="6" :show-text="false" />
            <small>{{ p.done }}/{{ p.total }}{{ p.overdue ? $t('implHome.n0Overdue2', [p.overdue]) : '' }}</small></div>
        </div>
      </HmCard>
      <HmCard :title="$t('implHome.masterDataInTheSystem')" class="md">
        <div class="mds"><span v-for="(k, i) in MD" :key="i" class="mdi"><small>{{ k[1] }}</small><b class="hm-num">{{ sum.master_counts?.[k[0]] ?? '—' }}</b></span></div>
        <template #footer><router-link to="/impl/import">{{ $t('implHome.dataImport') }}</router-link> · <router-link to="/impl/packs">{{ $t('implHome.industryTemplates') }}</router-link> · <router-link to="/impl/setup">{{ $t('implHome.setupWizard') }}</router-link></template>
      </HmCard>
    </div>

    <HmCard :title="$t('common.task')" flush>
      <template #actions><el-radio-group v-model="filter" size="small"><el-radio-button value="">{{ $t('common.all') }}</el-radio-button><el-radio-button value="open">{{ $t('implHome.open') }}</el-radio-button><el-radio-button value="late">{{ $t('common.late') }}</el-radio-button></el-radio-group></template>
      <table class="hm-table dense tasks" data-test="impl-tasks">
        <thead><tr><th style="width:90px">{{ $t('common.phase') }}</th><th>{{ $t('common.task') }}</th><th style="width:130px">{{ $t('implHome.owner') }}</th><th style="width:140px">{{ $t('erpArAp.dueDate') }}</th><th style="width:120px">{{ $t('common.status') }}</th><th>{{ $t('implHome.deliverableEntry') }}</th></tr></thead>
        <tbody>
          <tr v-for="t in shown" :key="t.id" :class="{ late: isLate(t) }" :data-test="'impl-task-' + t.id">
            <td><HmTag tone="neutral">{{ sum.phase_labels?.[t.phase] || t.phase }}</HmTag></td>
            <td>{{ t.title }}<div v-if="t.note" class="small muted">{{ t.note }}</div></td>
            <td><el-input v-model="t.owner" size="small" :placeholder="$t('implHome.owner')" :disabled="!canEdit" @change="upd(t, { owner: t.owner })" /></td>
            <td><el-date-picker v-model="t.due" type="date" value-format="YYYY-MM-DD" size="small" style="width:130px" :disabled="!canEdit" @change="upd(t, { due: t.due })" /></td>
            <td><el-select v-model="t.status" size="small" :disabled="!canEdit" :data-test="'impl-task-status-' + t.id" @change="upd(t, { status: t.status, done_on: t.status === 'done' ? today : null })">
              <el-option v-for="(l, k) in sum.status_labels" :key="k" :value="k" :label="l" /></el-select></td>
            <td class="small"><span v-if="t.deliverable" class="hm-mono">{{ t.deliverable.replace('docs/', '') }}</span>
              <router-link v-if="t.link" :to="t.link" class="lnk"> {{ $t('implHome.open2') }}</router-link></td>
          </tr>
        </tbody>
      </table>
      <HmEmpty v-if="!tasks.length" compact :title="$t('implHome.thisProjectHasNoTasks')" :desc="$t('implHome.clickCreateTasksFromStandard')" />
    </HmCard>
    <p class="small muted">{{ $t('implHome.deliverableTemplatesAreInThe') }}</p>

    <el-dialog v-model="initDlg" :title="$t('implHome.createTasksFromStandardImplementatio')" width="min(480px, 96vw)" append-to-body>
      <el-form label-width="96px">
        <el-form-item :label="$t('common.item')"><el-input v-model="init.project" data-test="impl-init-project" /></el-form-item>
        <el-form-item :label="$t('implHome.startDate')"><el-date-picker v-model="init.start" type="date" value-format="YYYY-MM-DD" /></el-form-item>
        <el-form-item :label="$t('implHome.implementationLead')"><el-input v-model="init.owner" /></el-form-item>
      </el-form>
      <p class="small muted">{{ $t('implHome.creates16TasksAbout10') }}</p>
      <template #footer><el-button @click="initDlg = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" data-test="impl-init-ok" @click="doInit">{{ $t('implHome.createTasks') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, reactive, ref } from 'vue';
import { get, post, put } from '../api';
import { can } from '../perm';

const project = ref($t('implHome.defaultProject')), sum = ref<any>({}), tasks = ref<any[]>([]), filter = ref(''), initDlg = ref(false);
const today = new Date().toISOString().slice(0, 10);
const init = reactive<any>({ project: $t('implHome.defaultProject'), start: today, owner: '' });
const canEdit = computed(() => can('impl.impl_task.update'));
const MD = [['material', $t('common.material')], ['bom', 'BOM'], ['route', $t('implHome.operationsRoutings')], ['equipment', $t('common.equipment')], ['employee', $t('common.employees')], ['warehouse', $t('common.warehouse')], ['opening_stock', $t('common.stock')], ['insp_template', $t('implHome.inspectionItems')]];
const isLate = (t: any) => t.status !== 'done' && t.status !== 'na' && t.due && t.due < today;
const shown = computed(() => tasks.value.filter((t) => !filter.value || (filter.value === 'open' ? !['done', 'na'].includes(t.status) : isLate(t))));
async function load() {
  sum.value = await get('/api/impl/tasks/summary?project=' + encodeURIComponent(project.value));
  if (!sum.value.projects.includes(project.value) && sum.value.projects.length && !tasks.value.length) { project.value = sum.value.projects[0]; sum.value = await get('/api/impl/tasks/summary?project=' + encodeURIComponent(project.value)); }
  tasks.value = (await get(`/api/e/impl_task?size=500&sort=seq&f_project=${encodeURIComponent(project.value)}`)).items;
}
async function upd(t: any, v: any) { await put(`/api/e/impl_task/${t.id}`, v); await load(); }
async function doInit() { const r = await post('/api/impl/tasks/init', init); ElMessage.success($t('implHome.created0Tasks', [r.created])); project.value = r.project; initDlg.value = false; await load(); }
onMounted(load);
</script>

<style scoped>
.top { display: grid; grid-template-columns: 200px minmax(0, 1.6fr) minmax(0, 1fr); gap: var(--hm-gap); margin-bottom: var(--hm-gap); }
@media (max-width: 1200px) { .top { grid-template-columns: 1fr; } }
.prog :deep(.cb) { display: flex; flex-direction: column; align-items: center; gap: 4px; } .bad { color: var(--hm-fault-ink); }
.phases { display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); gap: 8px; }
.pp { border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); padding: 6px 8px; display: flex; flex-direction: column; gap: 4px; }
.pp.done { border-color: rgba(21, 163, 74, .45); } .pp.late { border-color: var(--hm-fault); } .pp small { color: var(--hm-text-3); }
.mds { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; } .mdi { display: flex; flex-direction: column; background: var(--hm-surface-2); border-radius: var(--hm-r-sm); padding: 4px 8px; }
.mdi small { color: var(--hm-text-3); } .mdi b { font-size: 18px; }
.tasks tr.late td:first-child { box-shadow: inset 3px 0 var(--hm-fault); } .lnk { color: var(--hm-primary); }
</style>
