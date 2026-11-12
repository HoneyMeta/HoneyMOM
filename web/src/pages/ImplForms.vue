<template>
  <div class="page" data-test="impl-forms">
    <HmPageHead :title="$t('common.formConfiguration')" :sub="$t('implForms.createEditFormsOnGeneric')">
      <el-select v-model="entity" filterable :placeholder="$t('common.selectEntity')" style="width:220px" data-test="form-entity" @change="load">
        <el-option v-for="e in ents" :key="e.name" :value="e.name" :label="$t('common.n01', [e.label, e.module_label || e.module])" />
      </el-select>
      <el-button :disabled="!d" @click="reset">{{ $t('common.resetToDefault') }}</el-button>
      <el-button type="primary" :disabled="!d" :loading="busy" data-test="form-save" @click="save">{{ $t('common.save') }}</el-button>
    </HmPageHead>
    <div v-if="d" class="grid two">
      <HmCard :title="$t('implForms.groupsOrder')" :sub="$t('implForms.fieldsNotPlacedInA')">
        <div v-for="(g, gi) in cfg.groups" :key="gi" class="grp" :data-test="'form-group-' + gi">
          <div class="gh"><el-input v-model="g.title" size="small" :placeholder="$t('implForms.groupName')" style="width:180px" :data-test="'form-group-title-' + gi" /><span class="grow" />
            <el-button size="small" link :disabled="gi === 0" @click="mv(gi, -1)">{{ $t('implForms.moveUp') }}</el-button><el-button size="small" link :disabled="gi === cfg.groups.length - 1" @click="mv(gi, 1)">{{ $t('implForms.moveDown') }}</el-button>
            <el-button size="small" link type="danger" @click="cfg.groups.splice(gi, 1)">{{ $t('common.delete') }}</el-button></div>
          <el-select v-model="g.fields" multiple filterable size="small" style="width:100%" :placeholder="$t('implForms.selectFieldsOrderedBySelection')" :data-test="'form-group-fields-' + gi">
            <el-option v-for="f in d.fields" :key="f.name" :value="f.name" :label="f.label + (f.cf ? $t('implForms.custom') : '')" :disabled="inOther(f.name, gi)" />
          </el-select>
        </div>
        <el-button size="small" data-test="form-add-group" @click="cfg.groups.push({ title: '', fields: [] })">{{ $t('implForms.group') }}</el-button>
      </HmCard>
      <HmCard :title="$t('common.byRole')" :sub="$t('implForms.requiredHiddenReadOnlySelect')">
        <div class="rw"><table class="hm-table dense roles" data-test="form-roles">
          <thead><tr><th>{{ $t('common.field') }}</th><th>{{ $t('common.required2') }}</th><th>{{ $t('home.hide') }}</th><th>{{ $t('implForms.readOnly') }}</th></tr></thead>
          <tbody><tr v-for="f in d.fields.filter((x: any) => !x.ro)" :key="f.name">
            <td>{{ f.label }}<span v-if="f.req" class="muted small">{{ $t('implForms.systemRequired') }}</span></td>
            <td v-for="k in ['required', 'hidden', 'readonly']" :key="k"><el-select v-model="rules[k][f.name]" multiple collapse-tags size="small" style="width:150px" :data-test="`form-${k}-${f.name}`">
              <el-option value="*" :label="$t('common.everyone')" /><el-option v-for="(n, r) in d.roles" :key="r" :value="r" :label="n" /></el-select></td>
          </tr></tbody>
        </table></div>
      </HmCard>
      <HmCard :title="$t('common.preview')" :sub="$t('implForms.previewAsRole0', [d.roles[asRole] || asRole])" class="span2">
        <template #actions><el-select v-model="asRole" size="small" style="width:140px" data-test="form-preview-role"><el-option v-for="(n, r) in d.roles" :key="r" :value="r" :label="n" /></el-select></template>
        <div v-for="g in previewGroups" :key="g.title" class="pg"><div v-if="g.title" class="pt">{{ g.title }}</div>
          <div class="pf"><span v-for="f in g.fields" :key="f.name" class="pi" :class="{ req: f.req, ro: f.roRole }">{{ f.label }}{{ f.req ? ' *' : '' }}{{ f.roRole ? $t('implForms.readOnly2') : '' }}</span></div></div>
      </HmCard>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, reactive, ref } from 'vue';
import { del, get, loadSession, put, session } from '../api';

const entity = ref('work_order'), d = ref<any>(null), busy = ref(false), asRole = ref('operator');
const cfg = reactive<any>({ groups: [] }), rules = reactive<any>({ required: {}, hidden: {}, readonly: {} });
const ents = computed(() => (session.meta?.entities || []).filter((e: any) => !e.internal));
const inOther = (n: string, gi: number) => cfg.groups.some((g: any, i: number) => i !== gi && g.fields.includes(n));
function mv(i: number, dlt: number) { const g = cfg.groups.splice(i, 1)[0]; cfg.groups.splice(i + dlt, 0, g); }
async function load() {
  d.value = await get('/api/impl/forms/' + entity.value);
  const c = d.value.config || {};
  cfg.groups = (c.groups || []).map((g: any) => ({ title: g.title, fields: [...g.fields] }));
  for (const k of ['required', 'hidden', 'readonly']) { rules[k] = {}; for (const f of d.value.fields) rules[k][f.name] = [...((c[k] || {})[f.name] || [])]; }
}
const clean = () => ({ groups: cfg.groups.filter((g: any) => g.fields.length || g.title), ...Object.fromEntries(['required', 'hidden', 'readonly'].map((k) => [k,
  Object.fromEntries(Object.entries(rules[k]).filter(([, v]: any) => v?.length))])) });
const previewGroups = computed(() => {
  if (!d.value) return [];
  const c = clean(), hit = (rs: string[]) => !!rs && (rs.includes('*') || rs.includes(asRole.value));
  const fs = d.value.fields.filter((f: any) => !f.ro && !hit((c.hidden as any)[f.name]));
  const mark = (f: any) => ({ ...f, req: f.req || hit((c.required as any)[f.name]), roRole: hit((c.readonly as any)[f.name]) });
  const used = new Set<string>();
  const gs = c.groups.map((g: any) => ({ title: g.title, fields: g.fields.map((n: string) => fs.find((f: any) => f.name === n)).filter((f: any) => f && !used.has(f.name) && used.add(f.name)).map(mark) }));
  const rest = fs.filter((f: any) => !used.has(f.name)).map(mark);
  return gs.length ? [...gs.filter((g: any) => g.fields.length), ...(rest.length ? [{ title: $t('common.other'), fields: rest }] : [])] : [{ title: '', fields: rest }];
});
async function save() {
  busy.value = true;
  try { await put('/api/impl/forms/' + entity.value, { config: clean() }); ElMessage.success($t('implForms.savedTakesEffectWhenThe')); await loadSession(); await load(); } finally { busy.value = false; }
}
async function reset() { await del('/api/impl/forms/' + entity.value); ElMessage.success($t('implForms.defaultsRestored')); await loadSession(); await load(); }
onMounted(load);
</script>

<style scoped>
.two { grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr); } .span2 { grid-column: 1 / -1; } @media (max-width: 1500px) { .two { grid-template-columns: minmax(0, 1fr); } }
.rw { overflow-x: auto; }
.grp { border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); padding: 8px; margin-bottom: 8px; } .gh { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.roles td { padding: 3px 6px; }
.pt { font-weight: 600; margin: 8px 0 4px; color: var(--hm-text-2); }
.pf { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 6px; }
.pi { border: 1px solid var(--hm-border); border-radius: var(--hm-r-sm); padding: 4px 8px; font-size: var(--hm-fs-xs); background: var(--hm-surface-2); }
.pi.req { border-color: var(--hm-fault); } .pi.ro { color: var(--hm-text-3); background: var(--hm-surface-3); }
</style>
