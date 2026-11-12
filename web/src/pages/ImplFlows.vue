<template>
  <div class="page" data-test="impl-flows">
    <HmPageHead :title="$t('common.approvalFlows')" :sub="$t('implFlows.attachToActionsOnAny')">
      <el-button type="primary" data-test="flow-new" @click="edit()"><el-icon><Plus /></el-icon>{{ $t('implFlows.newWorkflow') }}</el-button>
    </HmPageHead>
    <el-tabs v-model="tab">
      <el-tab-pane :label="$t('common.flow')" name="flows">
        <HmCard flush>
          <HmDataTable :columns="COLS" :rows="flows" row-key="id" :ai="false" :empty-text="$t('implFlows.noApprovalWorkflowsYet')" data-test="flow-table">
            <template #cell-what="{ row }">{{ row.entity_label }} · {{ row.action_label }}</template>
            <template #cell-steps="{ row }">{{ row.steps.map((s: any) => s.name).join(' → ') }}</template>
            <template #cell-enabled="{ row }"><HmTag :tone="row.enabled ? 'ok' : 'neutral'">{{ row.enabled ? $t('common.enable') : $t('common.disabled') }}</HmTag></template>
            <template #cell-op="{ row }"><el-button link size="small" type="primary" :data-test="'flow-edit-' + row.id" @click="edit(row)">{{ $t('common.edit') }}</el-button>
              <el-popconfirm :title="$t('implFlows.deleteThisWorkflowSubmittedApprovals')" @confirm="remove(row)"><template #reference><el-button link size="small" type="danger">{{ $t('common.delete') }}</el-button></template></el-popconfirm></template>
          </HmDataTable>
        </HmCard>
      </el-tab-pane>
      <el-tab-pane :label="$t('implFlows.requests')" name="inst">
        <HmCard flush>
          <HmDataTable :columns="ICOLS" :rows="insts" row-key="id" :ai="false" :empty-text="$t('implFlows.noApprovalsSubmittedYet')" data-test="flow-insts">
            <template #cell-status="{ row }"><HmTag :tone="ST[row.status]?.[1] || 'neutral'">{{ ST[row.status]?.[0] || row.status }}</HmTag></template>
            <template #cell-steps="{ row }"><span class="small">{{ (row.steps || []).map((s: any) => `${s.name}${s.status === 'approved' ? '✓' : s.status === 'rejected' ? '✗' : ''}`).join(' → ') }}</span></template>
            <template #cell-error="{ row }"><span class="small" :class="{ err: row.error }">{{ row.error || row.result?.msg || '' }}</span></template>
          </HmDataTable>
        </HmCard>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="dlg" :title="form.id ? $t('implFlows.editApprovalWorkflow') : $t('implFlows.newApprovalWorkflow')" width="min(900px, 96vw)" append-to-body data-test="flow-dialog">
      <el-form label-width="96px">
        <el-form-item :label="$t('common.name')" required><el-input v-model="form.name" :placeholder="$t('implFlows.eGLargeBatchWork')" data-test="flow-name" /></el-form-item>
        <el-form-item :label="$t('implFlows.attachedTo')"><el-select v-model="form.entity" filterable style="width:200px" data-test="flow-entity" @change="form.action = 'create'">
          <el-option v-for="e in meta.entities" :key="e.name" :value="e.name" :label="e.label" /></el-select>
          <el-select v-model="form.action" style="width:180px;margin-left:8px" data-test="flow-action"><el-option v-for="(l, k) in ent?.actions || {}" :key="k" :value="k" :label="l" /></el-select>
          <el-switch v-model="form.enabled" :active-text="$t('common.enable')" style="margin-left:12px" /></el-form-item>
        <el-form-item :label="$t('implFlows.when')"><CondEditor v-model="form.condition" :fields="ent?.fields || []" :ops="meta.ops" test="flow-cond" /><span class="small muted">{{ $t('implFlows.leaveAllEmptyApprovalEvery') }}</span></el-form-item>
        <el-form-item :label="$t('implFlows.approvalSteps')">
          <div class="steps">
            <div v-for="(s, i) in form.steps" :key="i" class="st" :data-test="'flow-step-' + i">
              <b>{{ i + 1 }}</b><el-input v-model="s.name" size="small" :placeholder="$t('implFlows.stepName')" style="width:140px" :data-test="'flow-step-name-' + i" />
              <el-radio-group v-model="s.type" size="small"><el-radio-button value="role">{{ $t('common.roles') }}</el-radio-button><el-radio-button value="user">{{ $t('implFlows.namedUsers') }}</el-radio-button></el-radio-group>
              <el-select v-model="s.approver" filterable size="small" style="width:150px" :data-test="'flow-step-who-' + i">
                <template v-if="s.type === 'role'"><el-option v-for="(n, r) in meta.roles" :key="r" :value="r" :label="n" /></template>
                <template v-else><el-option v-for="u in meta.users" :key="u.username" :value="u.username" :label="$t('common.n01', [u.name || u.username, u.username])" /></template></el-select>
              <el-button size="small" link @click="s.showCond = !s.showCond">{{ s.condition?.length ? $t('implFlows.condition0', [s.condition.length]) : $t('implFlows.addCondition') }}</el-button>
              <el-button size="small" link type="danger" :disabled="form.steps.length < 2" @click="form.steps.splice(i, 1)">{{ $t('common.delete') }}</el-button>
              <div v-if="s.showCond || s.condition?.length" class="sc"><span class="small muted">{{ $t('implFlows.thisStepIsRequiredOnly') }}</span><CondEditor v-model="s.condition" :fields="ent?.fields || []" :ops="meta.ops" :test="'flow-step-cond-' + i" /></div>
            </div>
            <el-button size="small" data-test="flow-add-step" @click="form.steps.push({ name: $t('implFlows.step0', [form.steps.length + 1]), type: 'role', approver: 'manager', condition: [] })">{{ $t('implFlows.step') }}</el-button>
          </div>
        </el-form-item>
        <el-form-item :label="$t('common.description')"><el-input v-model="form.remark" /></el-form-item>
        <el-form-item :label="$t('implFlows.testRun')">
          <div class="trial"><span class="small muted">{{ $t('implFlows.enterARecordSValues') }}</span>
            <span v-for="c in trialFields" :key="c"><el-input v-model="trial[c]" size="small" :placeholder="fieldLabel(c)" style="width:120px" :data-test="'flow-trial-' + c" /></span>
            <el-button size="small" data-test="flow-trial-btn" @click="test">{{ $t('implFlows.testRun') }}</el-button>
            <span v-if="trialRes" class="small" data-test="flow-trial-result">{{ trialRes.triggered ? $t('implFlows.approvalRequired0', [trialRes.steps.map((s: any) => s.name).join(' → ')]) : $t('implFlows.noApprovalRequired') }}</span></div>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="dlg = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :loading="busy" data-test="flow-save" @click="save">{{ $t('common.save') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, defineComponent, h, onMounted, reactive, ref, resolveComponent } from 'vue';
import { del, get, post, put } from '../api';

const tab = ref('flows'), flows = ref<any[]>([]), insts = ref<any[]>([]), meta = ref<any>({ entities: [], ops: {}, roles: {}, users: [] }), dlg = ref(false), busy = ref(false);
const form = reactive<any>({}), trial = reactive<Record<string, any>>({}), trialRes = ref<any>(null);
const COLS = [{ key: 'name', label: $t('common.flow') }, { key: 'what', label: $t('implFlows.attachedTo'), width: 160 }, { key: 'condition_text', label: $t('automations.condition') }, { key: 'steps', label: $t('implFlows.steps') },
  { key: 'enabled', label: $t('common.status'), width: 70 }, { key: 'instances', label: $t('common.submitted'), num: true, width: 70 }, { key: 'op', label: '', width: 110, sortable: false }];
const ICOLS = [{ key: 'created_at', label: $t('common.time'), width: 150 }, { key: 'flow_name', label: $t('common.flow') }, { key: 'title', label: $t('common.content') }, { key: 'requester', label: $t('common.requester'), width: 90 },
  { key: 'steps', label: $t('implFlows.steps') }, { key: 'status', label: $t('common.status'), width: 90 }, { key: 'error', label: $t('common.result') }];
const ST: Record<string, [string, string]> = { pending: [$t('implFlows.inApproval'), 'warn'], approved: [$t('draftCard.executed'), 'ok'], rejected: [$t('common.rejected'), 'bad'], failed: [$t('implFlows.executionFailed'), 'bad'], cancelled: [$t('implFlows.withdrawn'), 'neutral'] };
const ent = computed(() => meta.value.entities.find((e: any) => e.name === form.entity));
const fieldLabel = (n: string) => ent.value?.fields.find((f: any) => f.name === n)?.label || n;
const trialFields = computed(() => [...new Set([...(form.condition || []), ...(form.steps || []).flatMap((s: any) => s.condition || [])].map((c: any) => c.field).filter(Boolean))] as string[]);

const CondEditor = defineComponent({
  props: { modelValue: Array, fields: Array, ops: Object, test: String },
  emits: ['update:modelValue'],
  setup(p: any, { emit }) {
    const ElSelect: any = resolveComponent('ElSelect'), ElOption: any = resolveComponent('ElOption'), ElInput: any = resolveComponent('ElInput'), ElButton: any = resolveComponent('ElButton');
    const list = () => (p.modelValue || []) as any[];
    const set = (v: any[]) => emit('update:modelValue', v);
    return () => h('div', { class: 'conds' }, [
      ...list().map((c: any, i: number) => {
        const f = (p.fields || []).find((x: any) => x.name === c.field);
        return h('div', { class: 'cond', 'data-test': `${p.test}-${i}` }, [
          h(ElSelect, { modelValue: c.field, size: 'small', filterable: true, style: 'width:150px', placeholder: $t('common.field'), 'onUpdate:modelValue': (v: any) => { c.field = v; set([...list()]); } },
            () => (p.fields || []).map((x: any) => h(ElOption, { value: x.name, label: x.label }))),
          h(ElSelect, { modelValue: c.op, size: 'small', style: 'width:100px', 'onUpdate:modelValue': (v: any) => { c.op = v; set([...list()]); } },
            () => Object.entries(p.ops || {}).map(([k, l]) => h(ElOption, { value: k, label: l as string }))),
          ['empty', 'not_empty'].includes(c.op) ? null : f?.enum && c.op !== 'in'
            ? h(ElSelect, { modelValue: c.value, size: 'small', style: 'width:130px', 'onUpdate:modelValue': (v: any) => { c.value = v; set([...list()]); } },
              () => Object.entries(f.enum).map(([k, l]) => h(ElOption, { value: k, label: l as string })))
            : h(ElInput, { modelValue: c.value, size: 'small', style: 'width:130px', placeholder: $t('common.value'), 'onUpdate:modelValue': (v: any) => { c.value = v; set([...list()]); } }),
          h(ElButton, { size: 'small', link: true, type: 'danger', onClick: () => set(list().filter((_: any, j: number) => j !== i)) }, () => $t('common.delete')),
        ]);
      }),
      h(ElButton, { size: 'small', link: true, type: 'primary', 'data-test': `${p.test}-add`, onClick: () => set([...list(), { field: '', op: '>', value: '' }]) }, () => $t('nodeConfig.condition')),
    ]);
  },
});

async function load() { flows.value = await get('/api/impl/flows'); insts.value = await get('/api/impl/flows/instances'); }
function edit(r?: any) {
  for (const k of Object.keys(form)) delete form[k];
  Object.assign(form, r ? JSON.parse(JSON.stringify(r)) : { name: '', entity: 'work_order', action: 'release', enabled: true, condition: [], steps: [{ name: $t('implFlows.supervisorApproval'), type: 'role', approver: 'manager', condition: [] }], remark: '' });
  for (const k of Object.keys(trial)) delete trial[k];
  trialRes.value = null; dlg.value = true;
}
const body = () => ({ ...form, steps: form.steps.map((s: any) => ({ name: s.name, type: s.type, approver: s.approver, condition: (s.condition || []).filter((c: any) => c.field) })),
  condition: (form.condition || []).filter((c: any) => c.field) });
async function save() {
  busy.value = true;
  try { if (form.id) await put(`/api/impl/flows/${form.id}`, body()); else await post('/api/impl/flows', body()); ElMessage.success($t('common.saved')); dlg.value = false; await load(); } finally { busy.value = false; }
}
async function remove(r: any) { await del(`/api/impl/flows/${r.id}`); await load(); }
async function test() { trialRes.value = await post('/api/impl/flows/test', { flow: body(), record: { ...trial } }); }
onMounted(async () => { meta.value = await get('/api/impl/flows/meta'); await load(); });
</script>

<style scoped>
.steps { display: flex; flex-direction: column; gap: 6px; width: 100%; }
.st { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); padding: 6px 8px; }
.st > b { width: 20px; height: 20px; border-radius: 50%; background: var(--hm-primary-soft); color: var(--hm-primary); display: grid; place-items: center; font-size: 12px; }
.sc { flex-basis: 100%; padding-left: 26px; }
:deep(.conds) { display: flex; flex-direction: column; gap: 4px; } :deep(.cond) { display: flex; gap: 6px; align-items: center; }
.trial { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; } .err { color: var(--hm-fault-ink); }
</style>
