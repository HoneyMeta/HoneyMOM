<template>
  <div class="page" data-test="admin-roles">
    <HmPageHead :title="$t('nav.rolesPermissions')" :sub="$t('adminRoles.aRoleDeterminesWhichPages')">
      <el-button v-if="tab === 'roles'" type="primary" data-test="role-new" @click="newRole"><el-icon><Plus /></el-icon>{{ $t('adminRoles.newRole') }}</el-button>
    </HmPageHead>
    <el-tabs v-model="tab" class="tabs">
      <el-tab-pane :label="$t('common.roles')" name="roles" />
      <el-tab-pane :label="$t('adminRoles.userRolesDataScope')" name="users" />
    </el-tabs>

    <div v-if="tab === 'roles'" class="grid split">
      <HmCard :title="$t('common.roles')" flush>
        <HmDataTable :columns="RCOLS" :rows="roles" row-key="code" clickable :loading="!loaded" :ai="false" @row-click="pick">
          <template #cell-name="{ row }"><span :class="{ cur: cur && cur.code === row.code }">{{ row.name }}</span> <HmTag v-if="row.builtin" tone="neutral">{{ $t('common.builtIn') }}</HmTag></template>
          <template #cell-code="{ row }"><span class="hm-mono">{{ row.code }}</span></template>
          <template #cell-scope_label="{ row }"><span class="small">{{ row.scope_label }}</span></template>
        </HmDataTable>
      </HmCard>

      <HmCard v-if="cur" :title="cur.isNew ? $t('adminRoles.newRole2') : $t('adminRoles.edit0', [cur.name])" data-test="role-editor">
        <template #actions>
          <el-button v-if="!cur.isNew && cur.builtin" size="small" @click="resetRole">{{ $t('common.resetToDefault') }}</el-button>
          <el-popconfirm v-if="!cur.isNew && !cur.builtin" :title="$t('adminRoles.deleteThisRole')" @confirm="delRole"><template #reference><el-button size="small" type="danger" plain>{{ $t('common.delete') }}</el-button></template></el-popconfirm>
          <el-button size="small" type="primary" :loading="busy" data-test="role-save" @click="saveRole">{{ $t('common.save') }}</el-button>
        </template>
        <el-form label-width="76px" size="small" class="frm">
          <el-form-item :label="$t('common.code')"><el-input v-model="cur.code" :disabled="!cur.isNew" :placeholder="$t('adminRoles.lowercaseLettersDigitsAndUnderscores')" data-test="role-code" /></el-form-item>
          <el-form-item :label="$t('common.name')"><el-input v-model="cur.name" data-test="role-name" /></el-form-item>
          <el-form-item :label="$t('common.description')"><el-input v-model="cur.remark" /></el-form-item>
          <el-form-item :label="$t('common.dataScope')"><ScopePicker v-model="cur.scope" :all-label="$t('adminApiKeys.allData')" /></el-form-item>
        </el-form>

        <div class="perm-head">
          <b>{{ $t('common.permissions') }}</b><span class="muted small">{{ $t('adminRoles.n01PermissionsAllowed', [grantedCount, allPerms.length]) }}</span><span class="grow" />
          <el-input v-model="filter" size="small" clearable :placeholder="$t('adminRoles.filterEGNonconformanceNcr')" class="flt" />
        </div>
        <el-collapse v-model="open" class="mods">
          <el-collapse-item v-for="m in modules" :key="m.key" :name="m.key">
            <template #title>
              <span class="mt"><el-checkbox :model-value="modState(m) === 2" :indeterminate="modState(m) === 1" @click.stop @change="(v: any) => setModule(m, v)" />
                <b>{{ m.label }}</b><span class="muted small">{{ m.key }}.*</span></span>
            </template>
            <table class="pt">
              <thead><tr><th>{{ $t('common.object') }}</th><th v-for="o in OPS" :key="o.k">{{ o.l }}</th><th>{{ $t('adminRoles.buttonsActions') }}</th></tr></thead>
              <tbody>
                <tr v-for="e in m.entities" :key="e.entity">
                  <td>{{ e.label }} <span class="hm-mono muted small">{{ e.entity }}</span></td>
                  <td v-for="o in OPS" :key="o.k" class="c">
                    <el-checkbox v-if="hasOp(e, o.k)" :model-value="g(pid(e, o.k))" :data-test="'perm-' + pid(e, o.k)" @change="(v: any) => setPerm(pid(e, o.k), v)" />
                  </td>
                  <td><div class="acts"><el-checkbox v-for="a in e.actions" :key="a.id" :model-value="g(a.id)" size="small" @change="(v: any) => setPerm(a.id, v)">{{ a.label }}</el-checkbox></div></td>
                </tr>
              </tbody>
            </table>
          </el-collapse-item>
          <el-collapse-item name="_pages">
            <template #title><span class="mt"><b>{{ $t('adminRoles.pagesMenu') }}</b><span class="muted small">{{ $t('adminRoles.menuItemsWithoutPermissionAre') }}</span></span></template>
            <div class="pages">
              <div v-for="grp in pageGroups" :key="grp.label" class="pg"><h5>{{ grp.label }}</h5>
                <el-checkbox v-for="it in grp.items" :key="it.path" :model-value="g(it.perm)" size="small" @change="(v: any) => setPerm(it.perm, v)">{{ it.label }}</el-checkbox></div>
            </div>
          </el-collapse-item>
          <el-collapse-item name="_feat">
            <template #title><span class="mt"><b>{{ $t('adminRoles.featurePermissions') }}</b><span class="muted small">{{ $t('adminRoles.adminFunctionsAgentEditingTime') }}</span></span></template>
            <div class="feats"><el-checkbox v-for="f in features" :key="f.id" :model-value="g(f.id)" @change="(v: any) => setPerm(f.id, v)">{{ f.label }} <span class="hm-mono muted small">{{ f.id }}</span></el-checkbox></div>
          </el-collapse-item>
        </el-collapse>
        <details class="adv">
          <summary>{{ $t('adminRoles.advancedPermissionPatternsWildcardEx') }}</summary>
          <el-input v-model="patText" type="textarea" :rows="5" class="hm-mono" data-test="role-patterns" @change="fromText" />
          <p class="small muted">{{ $t('adminRoles.onePerLineExample') }}<span class="hm-mono">quality.*</span> {{ $t('adminRoles.allOfTheQualityModule') }}<span class="hm-mono">*.read</span> {{ $t('adminRoles.viewEverything') }}<span class="hm-mono">!quality.ncr.delete</span> {{ $t('adminRoles.butCannotDeleteNonconformances') }}</p>
        </details>
      </HmCard>
      <HmCard v-else><HmEmpty compact :title="$t('adminRoles.selectARoleToView')" :desc="$t('adminRoles.builtInRolesMatchThe')" /></HmCard>
    </div>

    <HmCard v-else :title="$t('common.users')" flush data-test="admin-users">
      <HmDataTable :columns="UCOLS" :rows="users" row-key="id" :ai="false" :loading="!uloaded">
        <template #cell-username="{ row }"><span class="hm-mono">{{ row.username }}</span><HmTag v-if="!row.active" tone="neutral">{{ $t('common.disabled') }}</HmTag></template>
        <template #cell-roles="{ row }"><HmTag v-for="r in row.role_list" :key="r" tone="primary">{{ roleName(r) }}</HmTag></template>
        <template #cell-scope_label="{ row }"><span class="small">{{ row.scope ? row.scope_label : $t('common.byRole') }}</span></template>
        <template #cell-op="{ row }"><el-button link type="primary" size="small" :data-test="'user-edit-' + row.username" @click="editUser(row)">{{ $t('common.edit2') }}</el-button></template>
      </HmDataTable>
    </HmCard>

    <el-dialog v-model="udlg" :title="$t('adminRoles.users0', [uform.name || uform.username])" width="560px" append-to-body>
      <el-form label-width="76px" size="small">
        <el-form-item :label="$t('common.roles')"><el-select v-model="uform.role_list" multiple filterable style="width:100%" data-test="user-roles">
          <el-option v-for="r in roles" :key="r.code" :value="r.code" :label="r.name" /></el-select></el-form-item>
        <el-form-item :label="$t('common.dataScope')"><ScopePicker v-model="uform.scope" :all-label="$t('common.byRole')" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="udlg = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :loading="busy" data-test="user-save" @click="saveUser">{{ $t('common.save') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, ref, watch } from 'vue';
import { call, get, post, put } from '../api';
import ScopePicker from '../components/admin/ScopePicker.vue';
import { NAV } from '../layout/nav';
import { grants, navPerm } from '../perm';

const tab = ref('roles'), roles = ref<any[]>([]), users = ref<any[]>([]), catalog = ref<any>(null), loaded = ref(false), uloaded = ref(false), busy = ref(false);
const cur = ref<any>(null), filter = ref(''), open = ref<string[]>([]), patText = ref('');
const udlg = ref(false), uform = ref<any>({});
const OPS = [{ k: 'read', l: $t('common.view') }, { k: 'create', l: $t('common.add') }, { k: 'update', l: $t('common.edit2') }, { k: 'delete', l: $t('common.delete') }];
const RCOLS = [{ key: 'name', label: $t('common.roles') }, { key: 'code', label: $t('common.code'), width: 96 }, { key: 'users', label: $t('common.users'), num: true, width: 56 }, { key: 'scope_label', label: $t('common.dataScope') }];
const UCOLS = [{ key: 'username', label: $t('adminRoles.username'), width: 120 }, { key: 'name', label: $t('adminRoles.name'), width: 140 }, { key: 'roles', label: $t('common.roles') }, { key: 'scope_label', label: $t('common.dataScope') },
  { key: 'op', label: '', width: 64, sortable: false }];

const modules = computed(() => {
  if (!catalog.value) return [];
  const k = filter.value.trim().toLowerCase();
  return catalog.value.modules.map((m: any) => ({
    ...m, entities: catalog.value.entities.filter((e: any) => e.module === m.key && (!k || (e.label + e.entity).toLowerCase().includes(k)))
      .map((e: any) => ({ ...e, ops: e.perms.filter((p: any) => p.kind === 'api').map((p: any) => p.id.split('.').pop()), actions: e.perms.filter((p: any) => p.kind === 'action') })),
  })).filter((m: any) => m.entities.length);
});
const features = computed(() => catalog.value?.features || []);
const pageGroups = computed(() => NAV.map((gp) => ({ label: gp.label, items: gp.items.filter((it) => !it.path.startsWith('/e/') && !it.perm).map((it) => ({ ...it, perm: navPerm(it) })) }))
  .filter((gp) => gp.items.length));
const allPerms = computed(() => [
  ...(catalog.value?.entities || []).flatMap((e: any) => e.perms.map((p: any) => p.id)), ...features.value.map((f: any) => f.id),
  ...pageGroups.value.flatMap((gp) => gp.items.map((it) => it.perm))]);
const grantedCount = computed(() => (cur.value ? allPerms.value.filter((p) => g(p)).length : 0));

const pid = (e: any, op: string) => `${e.module}.${e.entity}.${op}`;
const hasOp = (e: any, op: string) => e.ops.includes(op);
const g = (p: string) => !!cur.value && grants(cur.value.perms, p);
function setPerm(p: string, on: boolean) {
  const ps: string[] = cur.value.perms;
  if (on) {
    const i = ps.indexOf('!' + p); if (i >= 0) ps.splice(i, 1);
    if (!grants(ps, p)) { for (let j = ps.length - 1; j >= 0; j--) if (ps[j].startsWith('!') && grants([ps[j].slice(1)], p)) ps.splice(j, 1); }
    if (!grants(ps, p)) ps.push(p);
  } else {
    const i = ps.indexOf(p); if (i >= 0) ps.splice(i, 1);
    if (grants(ps, p)) ps.push('!' + p);
  }
  patText.value = ps.join('\n');
}
function modState(m: any) {
  const ids = m.entities.flatMap((e: any) => e.perms.map((p: any) => p.id));
  const n = ids.filter((p: string) => g(p)).length;
  return n === 0 ? 0 : n === ids.length ? 2 : 1;
}
function setModule(m: any, on: boolean) {
  const pre = m.key + '.';
  cur.value.perms = cur.value.perms.filter((p: string) => !(p.startsWith(pre) || p.startsWith('!' + pre)));
  if (on) cur.value.perms.push(m.key + '.*');
  else if (grants(cur.value.perms, m.key + '.x.read')) cur.value.perms.push('!' + m.key + '.*');
  patText.value = cur.value.perms.join('\n');
}
function fromText() { cur.value.perms = patText.value.split(/[\n,]/).map((x) => x.trim()).filter(Boolean); }
const roleName = (c: string) => roles.value.find((r) => r.code === c)?.name || c;

function pick(r: any) { cur.value = JSON.parse(JSON.stringify(r)); patText.value = cur.value.perms.join('\n'); }
function newRole() { cur.value = { isNew: true, code: '', name: '', remark: '', perms: ['page.home'], scope: null }; patText.value = 'page.home'; }
async function load() {
  const [r, c] = await Promise.all([get('/api/admin/roles'), catalog.value ? null : get('/api/admin/perms/catalog')]);
  roles.value = r.items; if (c) catalog.value = c; loaded.value = true;
  if (cur.value && !cur.value.isNew) { const x = roles.value.find((y) => y.code === cur.value.code); if (x) pick(x); }
}
async function loadUsers() { users.value = (await get('/api/admin/users')).items; uloaded.value = true; }
async function saveRole() {
  busy.value = true;
  try {
    const body = { code: cur.value.code, name: cur.value.name, remark: cur.value.remark, perms: cur.value.perms, scope: cur.value.scope };
    if (cur.value.isNew) { await post('/api/admin/roles', body); cur.value.isNew = false; } else await put('/api/admin/roles/' + cur.value.code, body);
    ElMessage.success($t('common.saved')); await load();
  } finally { busy.value = false; }
}
async function resetRole() { await post(`/api/admin/roles/${cur.value.code}/reset`); ElMessage.success($t('adminRoles.defaultPermissionsRestored')); await load(); }
async function delRole() { await call('DELETE', '/api/admin/roles/' + cur.value.code); cur.value = null; ElMessage.success($t('common.deleted')); await load(); }
function editUser(u: any) { uform.value = { ...u, role_list: [...u.role_list], scope: u.scope ? JSON.parse(JSON.stringify(u.scope)) : null }; udlg.value = true; }
async function saveUser() {
  busy.value = true;
  try { await put('/api/admin/users/' + uform.value.id, { roles: uform.value.role_list, scope: uform.value.scope }); udlg.value = false; ElMessage.success($t('common.saved')); await loadUsers(); await load(); }
  finally { busy.value = false; }
}
watch(tab, (t) => { if (t === 'users' && !uloaded.value) loadUsers(); });
onMounted(load);
</script>

<style scoped>
.split { grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.6fr); align-items: start; }
@media (max-width: 1279px) { .split { grid-template-columns: minmax(0, 1fr); } }
.tabs { margin-bottom: 4px; }
.cur { font-weight: 600; color: var(--hm-primary); }
.frm { max-width: 640px; }
.perm-head { display: flex; align-items: center; gap: 10px; margin: 8px 0; }
.grow { flex: 1; }
.flt { width: 220px; }
.mt { display: inline-flex; align-items: center; gap: 8px; }
.pt { width: 100%; border-collapse: collapse; font-size: var(--hm-fs-sm); }
.pt th { text-align: left; font-weight: 500; color: var(--hm-text-3); font-size: var(--hm-fs-xs); padding: 4px 6px; border-bottom: 1px solid var(--hm-border); }
.pt td { padding: 3px 6px; border-bottom: 1px solid var(--hm-border-soft, var(--hm-border)); vertical-align: middle; }
.pt td.c { width: 52px; }
.pt .acts { display: flex; flex-wrap: wrap; gap: 0 10px; }
.pages { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 6px 16px; }
.pg h5 { margin: 4px 0; font-size: var(--hm-fs-xs); color: var(--hm-text-3); font-weight: 500; }
.pg { display: flex; flex-direction: column; }
.feats { display: flex; flex-direction: column; gap: 2px; }
.adv { margin-top: 10px; }
.adv summary { cursor: pointer; color: var(--hm-text-2); font-size: var(--hm-fs-sm); }
.small { font-size: var(--hm-fs-xs); }
.muted { color: var(--hm-text-3); }
</style>
