<template>
  <div class="page" data-test="impl-fields">
    <HmPageHead :title="$t('common.customFields')" :sub="$t('implFields.addFieldsToAnyEntity')">
      <el-select v-model="entity" filterable :placeholder="$t('common.selectEntity')" style="width:220px" data-test="cf-entity" @change="load">
        <el-option v-for="e in meta.entities" :key="e.name" :value="e.name" :label="$t('common.n01', [e.label, e.module_label || e.module])" />
      </el-select>
      <el-button type="primary" :disabled="!entity" data-test="cf-new" @click="edit()"><el-icon><Plus /></el-icon>{{ $t('implFields.newField') }}</el-button>
    </HmPageHead>
    <HmCard flush>
      <HmDataTable :columns="COLS" :rows="rows" row-key="id" :ai="false" :empty-text="entity ? $t('implFields.thisEntityHasNoCustom') : $t('implFields.selectAnEntityFirst')" data-test="cf-table">
        <template #cell-key="{ row }"><span class="hm-mono">{{ row.key }}</span></template>
        <template #cell-ftype="{ row }">{{ row.ftype_label }}<span v-if="row.ftype === 'enum'" class="muted small">{{ $t('common.n0', [opts(row)]) }}</span><span v-if="row.ftype === 'ref'" class="muted small"> → {{ entLabel(row.ref) }}</span></template>
        <template #cell-flags="{ row }"><HmTag v-if="row.required" tone="warn">{{ $t('common.required2') }}</HmTag> <HmTag v-if="row.in_list" tone="neutral">{{ $t('implFields.list') }}</HmTag> <HmTag v-if="row.filterable" tone="primary">{{ $t('implFields.filterableIndexed') }}</HmTag></template>
        <template #cell-active="{ row }"><HmTag :tone="row.active ? 'ok' : 'neutral'">{{ row.active ? $t('common.enable') : $t('common.disabled') }}</HmTag></template>
        <template #cell-op="{ row }"><el-button link size="small" type="primary" :data-test="'cf-edit-' + row.key" @click="edit(row)">{{ $t('common.edit') }}</el-button>
          <el-button link size="small" @click="toggle(row)">{{ row.active ? $t('common.disabled') : $t('common.enable') }}</el-button></template>
      </HmDataTable>
    </HmCard>
    <p class="small muted">{{ $t('implFields.storageOneExtJsonColumn') }}</p>

    <el-dialog v-model="dlg" :title="form.id ? $t('implFields.editField0', [form.label]) : $t('implFields.newField0', [entLabel(entity)])" width="min(560px, 96vw)" append-to-body data-test="cf-dialog">
      <el-form label-width="96px">
        <el-form-item :label="$t('common.name')" required><el-input v-model="form.label" :placeholder="$t('implFields.eGColor')" data-test="cf-label" /></el-form-item>
        <el-form-item :label="$t('implFields.fieldKey')" required><el-input v-model="form.key" :disabled="!!form.id" :placeholder="$t('implFields.eGColorXAdded')" data-test="cf-key"><template #prepend>x_</template></el-input></el-form-item>
        <el-form-item :label="$t('common.type')" required><el-select v-model="form.ftype" :disabled="!!form.id" style="width:100%" data-test="cf-type"><el-option v-for="(l, k) in meta.ftypes" :key="k" :value="k" :label="l" /></el-select></el-form-item>
        <el-form-item v-if="form.ftype === 'enum'" :label="$t('implFields.options')" required><el-input v-model="form.options" type="textarea" :rows="3" :placeholder="$t('implFields.onePerLineOrComma')" data-test="cf-options" /></el-form-item>
        <el-form-item v-if="form.ftype === 'ref'" :label="$t('implFields.reference')" required><el-select v-model="form.ref" filterable :disabled="!!form.id" style="width:100%" data-test="cf-ref">
          <el-option v-for="e in meta.entities" :key="e.name" :value="e.name" :label="e.label" /></el-select></el-form-item>
        <el-form-item :label="$t('implFields.options')"><el-checkbox v-model="form.required">{{ $t('common.required2') }}</el-checkbox><el-checkbox v-model="form.in_list">{{ $t('implFields.showInList') }}</el-checkbox>
          <el-checkbox v-model="form.filterable" :disabled="form.ftype === 'file'" data-test="cf-filterable">{{ $t('implFields.filterableIndexed2') }}</el-checkbox></el-form-item>
        <el-form-item :label="$t('implFields.order')"><el-input-number v-model="form.seq" :min="0" :step="10" /></el-form-item>
        <el-form-item :label="$t('common.description')"><el-input v-model="form.help" :placeholder="$t('implFields.formHint')" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="dlg = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :loading="busy" data-test="cf-save" @click="save">{{ $t('common.save') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { onMounted, reactive, ref } from 'vue';
import { useRoute } from 'vue-router';
import { del, get, loadSession, post, put } from '../api';

const route = useRoute();
const meta = ref<any>({ entities: [], ftypes: {} }), entity = ref(String(route.query.entity || '')), rows = ref<any[]>([]), dlg = ref(false), busy = ref(false);
const form = reactive<any>({});
const COLS = [{ key: 'label', label: $t('common.name'), width: 140 }, { key: 'key', label: $t('implFields.fieldKey'), width: 150 }, { key: 'ftype', label: $t('common.type') }, { key: 'flags', label: '', sortable: false },
  { key: 'seq', label: $t('implFields.order'), num: true, width: 60 }, { key: 'active', label: $t('common.status'), width: 70 }, { key: 'op', label: '', width: 110, sortable: false }];
const entLabel = (n: string) => meta.value.entities.find((e: any) => e.name === n)?.label || n;
const opts = (r: any) => (Array.isArray(r.options) ? r.options.map((x: any) => (typeof x === 'object' ? x.label : x)) : Object.values(r.options || {})).join($t('common.k42'));
async function load() { rows.value = entity.value ? await get('/api/impl/fields?entity=' + entity.value) : []; }
function edit(r?: any) {
  for (const k of Object.keys(form)) delete form[k];
  Object.assign(form, r ? { ...r, key: r.key.replace(/^x_/, ''), options: Array.isArray(r.options) ? r.options.map((x: any) => (typeof x === 'object' ? x.label : x)).join('\n') : r.options }
    : { label: '', key: '', ftype: 'text', options: '', ref: '', required: false, in_list: true, filterable: false, seq: (rows.value.length + 1) * 10, help: '' });
  dlg.value = true;
}
async function save() {
  busy.value = true;
  try {
    const body = { ...form, entity: entity.value };
    if (form.id) await put(`/api/impl/fields/${form.id}`, body); else await post('/api/impl/fields', body);
    ElMessage.success($t('common.saved')); dlg.value = false; await load(); await loadSession();
  } finally { busy.value = false; }
}
async function toggle(r: any) { if (r.active) await del(`/api/impl/fields/${r.id}`); else await put(`/api/impl/fields/${r.id}`, { active: true }); await load(); await loadSession(); }
onMounted(async () => { meta.value = await get('/api/impl/fields/meta'); if (!entity.value) entity.value = 'material'; await load(); });
</script>
