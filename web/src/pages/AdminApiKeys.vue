<template>
  <div class="page" data-test="admin-apikeys">
    <HmPageHead :title="$t('common.apiKeys')" :sub="$t('adminApiKeys.usedForTheMcpData')">
      <el-button type="primary" data-test="key-new" @click="openNew"><el-icon><Plus /></el-icon>{{ $t('adminApiKeys.newKey') }}</el-button>
    </HmPageHead>
    <HmCard flush>
      <HmDataTable :columns="COLS" :rows="items" row-key="id" :ai="false" :loading="!loaded" :empty-text="$t('adminApiKeys.noApiKeysYet')">
        <template #cell-name="{ row }"><b>{{ row.name }}</b><div class="small muted">{{ row.remark }}</div></template>
        <template #cell-key_hint="{ row }"><span class="hm-mono small">{{ row.key_hint }}</span></template>
        <template #cell-perms="{ row }"><HmTag v-for="r in (row.roles ? row.roles.split(',') : [])" :key="r" tone="primary">{{ roleName(r) }}</HmTag>
          <span class="hm-mono small">{{ row.perms.join(' ') }}</span></template>
        <template #cell-scope_label="{ row }"><span class="small">{{ row.scope_label }}</span></template>
        <template #cell-ip_allow="{ row }"><span class="hm-mono small">{{ row.ip_allow || $t('adminApiKeys.unrestricted') }}</span></template>
        <template #cell-expires_at="{ row }"><span class="small">{{ row.expires_at ? row.expires_at.slice(0, 10) : $t('adminApiKeys.neverExpires') }}</span></template>
        <template #cell-last_used_at="{ row }"><span class="small">{{ row.last_used_at || $t('adminApiKeys.never') }}</span><div v-if="row.last_used_ip" class="small muted hm-mono">{{ $t('adminApiKeys.n01Calls', [row.last_used_ip, row.use_count]) }}</div></template>
        <template #cell-status="{ row }"><HmTag :tone="row.status === 'active' ? 'ok' : 'neutral'">{{ ({ active: $t('adminApiKeys.active'), revoked: $t('adminApiKeys.revoked'), expired: $t('adminApiKeys.expired') } as any)[row.status] }}</HmTag></template>
        <template #cell-op="{ row }">
          <el-button v-if="row.status === 'active'" link size="small" type="primary" @click="openEdit(row)">{{ $t('common.edit2') }}</el-button>
          <el-popconfirm v-if="row.status === 'active'" :title="$t('adminApiKeys.theKeyStopsWorkingImmediately')" @confirm="revoke(row)"><template #reference>
            <el-button link size="small" type="danger" :data-test="'key-revoke-' + row.id">{{ $t('adminApiKeys.revoke') }}</el-button></template></el-popconfirm>
        </template>
      </HmDataTable>
    </HmCard>

    <el-dialog v-model="dlg" :title="form.id ? $t('adminApiKeys.editKey') : $t('adminApiKeys.newApiKey')" width="600px" append-to-body>
      <el-form label-width="84px" size="small">
        <el-form-item :label="$t('common.name')"><el-input v-model="form.name" :placeholder="$t('adminApiKeys.eGErpIntegrationMcp')" data-test="key-name" /></el-form-item>
        <el-form-item :label="$t('common.roles')"><el-select v-model="form.roles" multiple style="width:100%" :placeholder="$t('adminApiKeys.grantPermissionsByRoleOptional')"><el-option v-for="r in roles" :key="r.code" :value="r.code" :label="r.name" /></el-select></el-form-item>
        <el-form-item :label="$t('adminApiKeys.permissions')">
          <el-select v-model="form.perms" multiple filterable allow-create default-first-option style="width:100%" :placeholder="$t('adminApiKeys.addMorePermissionsOrPatterns')" data-test="key-perms">
            <el-option v-for="p in PRESET" :key="p.v" :value="p.v" :label="$t('common.n01', [p.l, p.v])" /></el-select>
        </el-form-item>
        <el-form-item :label="$t('common.dataScope')"><ScopePicker v-model="form.scope" :all-label="$t('adminApiKeys.allData')" /></el-form-item>
        <el-form-item :label="$t('common.expires')"><el-date-picker v-model="form.expires_at" type="date" value-format="YYYY-MM-DD 23:59:59" :placeholder="$t('adminApiKeys.neverExpires')" /></el-form-item>
        <el-form-item :label="$t('adminApiKeys.ipAllowlist')"><el-input v-model="form.ip_allow" :placeholder="$t('adminApiKeys.emptyUnrestrictedSeparateWithCommas')" class="hm-mono" /></el-form-item>
        <el-form-item :label="$t('common.description')"><el-input v-model="form.remark" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="dlg = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :loading="busy" data-test="key-save" @click="save">{{ form.id ? $t('common.save') : $t('common.create') }}</el-button></template>
    </el-dialog>

    <el-dialog v-model="showKey" :title="$t('adminApiKeys.copyAndSaveTheKey')" width="600px" append-to-body :close-on-click-modal="false">
      <el-alert type="warning" :closable="false" show-icon :title="$t('adminApiKeys.thisKeyIsShownOnly')" />
      <div class="key"><span class="hm-mono" data-test="key-plain">{{ newKey }}</span><el-button size="small" @click="copy">{{ $t('common.copy') }}</el-button></div>
      <p class="small muted">{{ $t('adminApiKeys.example') }}<span class="hm-mono">curl -H "X-API-Key: {{ newKey.slice(0, 16) }}…" {{ origin }}/api/whoami</span></p>
      <template #footer><el-button type="primary" @click="showKey = false">{{ $t('adminApiKeys.iVeSavedIt') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { onMounted, ref } from 'vue';
import { get, post, put } from '../api';
import ScopePicker from '../components/admin/ScopePicker.vue';

const items = ref<any[]>([]), roles = ref<any[]>([]), loaded = ref(false), dlg = ref(false), busy = ref(false), form = ref<any>({});
const showKey = ref(false), newKey = ref(''), origin = location.origin;
const COLS = [{ key: 'name', label: $t('common.name') }, { key: 'key_hint', label: $t('adminApiKeys.key'), width: 130 }, { key: 'perms', label: $t('common.permissions') }, { key: 'scope_label', label: $t('common.dataScope'), width: 120 },
  { key: 'ip_allow', label: 'IP', width: 120 }, { key: 'expires_at', label: $t('common.expires'), width: 90 }, { key: 'last_used_at', label: $t('adminApiKeys.lastUsed'), width: 150 },
  { key: 'status', label: $t('common.status'), width: 70 }, { key: 'op', label: '', width: 96, sortable: false }];
const PRESET = [{ v: '*.read', l: $t('adminApiKeys.readAllData') }, { v: 'equipment.ts.read', l: $t('adminApiKeys.readEquipmentTimeSeries') }, { v: 'equipment.ts.write', l: $t('adminApiKeys.gatewayPushOfTimeSeries') },
  { v: 'production.*', l: $t('adminApiKeys.allProductionModules') }, { v: 'warehouse.*', l: $t('adminApiKeys.allWarehouseModules') }, { v: 'master.*', l: $t('adminApiKeys.allMasterData') }];
const roleName = (c: string) => roles.value.find((r) => r.code === c)?.name || c;

async function load() { const r = await get('/api/admin/apikeys'); items.value = r.items; roles.value = r.roles; loaded.value = true; }
function openNew() { form.value = { name: '', roles: [], perms: ['*.read'], scope: null, expires_at: null, ip_allow: '', remark: '' }; dlg.value = true; }
function openEdit(r: any) { form.value = { ...r, roles: r.roles ? r.roles.split(',') : [], perms: [...r.perms], scope: r.scope ? JSON.parse(JSON.stringify(r.scope)) : null }; dlg.value = true; }
async function save() {
  busy.value = true;
  try {
    const body = { name: form.value.name, roles: form.value.roles, perms: form.value.perms, scope: form.value.scope, expires_at: form.value.expires_at, ip_allow: form.value.ip_allow, remark: form.value.remark };
    if (form.value.id) { await put('/api/admin/apikeys/' + form.value.id, body); ElMessage.success($t('common.saved')); }
    else { const r = await post('/api/admin/apikeys', body); newKey.value = r.key; showKey.value = true; }
    dlg.value = false; await load();
  } finally { busy.value = false; }
}
async function revoke(r: any) { await post(`/api/admin/apikeys/${r.id}/revoke`); ElMessage.success($t('adminApiKeys.revoked')); await load(); }
async function copy() { try { await navigator.clipboard.writeText(newKey.value); ElMessage.success($t('common.copied')); } catch { ElMessage.info($t('adminApiKeys.pleaseSelectAndCopyManually')); } }
onMounted(load);
</script>

<style scoped>
.small { font-size: var(--hm-fs-xs); }
.muted { color: var(--hm-text-3); }
.key { display: flex; align-items: center; gap: 10px; margin: 14px 0 8px; padding: 10px 12px; background: var(--hm-bg-2, #f5f7fa); border-radius: var(--hm-r-md); word-break: break-all; }
.key span { flex: 1; font-size: 13px; }
</style>
