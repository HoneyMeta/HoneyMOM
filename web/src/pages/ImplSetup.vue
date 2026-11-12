<template>
  <div class="page" data-test="impl-setup">
    <HmPageHead :title="$t('common.setupWizard')" :sub="$t('implSetup.aNewInstallationStartsHere')">
      <HmTag :tone="d.state === 'pending' ? 'warn' : 'ok'">{{ d.state === 'pending' ? $t('implSetup.toDo') : $t('common.completed') }}</HmTag>
    </HmPageHead>
    <el-steps :active="step" finish-status="success" align-center class="steps" data-test="setup-steps">
      <el-step :title="$t('common.industryTemplates')" /><el-step :title="$t('implSetup.companyInfo')" /><el-step :title="$t('implSetup.importMasterData')" /><el-step :title="$t('implSetup.equipmentConnectivity')" />
    </el-steps>

    <HmCard v-if="step === 0" :title="$t('implSetup.n1ChooseIndustryTemplate')" :sub="$t('implSetup.multipleSelectionAllowedEG')">
      <div class="packs">
        <div v-for="p in d.packs" :key="p.code" class="pk" :class="{ on: p.applied }"><b>{{ p.name }}</b><HmTag v-if="p.applied" tone="ok">{{ $t('packPreview.applied2') }}</HmTag>
          <div class="small muted">{{ p.industry }}</div></div>
      </div>
      <PackPreview :packs="d.packs" :initial="d.applied?.filter((x: string) => x !== 'common')" can-apply @applied="reload" />
      <div class="foot"><el-button type="primary" data-test="setup-next-0" @click="step = 1">{{ $t('common.next') }}</el-button></div>
    </HmCard>

    <HmCard v-if="step === 1" :title="$t('implSetup.n2CompanyInfo')">
      <el-form label-width="96px" style="max-width:560px">
        <el-form-item :label="$t('implSetup.companyName')" required><el-input v-model="co.company" data-test="setup-company" /></el-form-item>
        <el-form-item :label="$t('implSetup.plantCode')"><el-input v-model="co.plant_code" :placeholder="$t('implSetup.eGP1')" data-test="setup-plant-code" /></el-form-item>
        <el-form-item :label="$t('implSetup.plantName')"><el-input v-model="co.plant_name" :placeholder="$t('implSetup.eGPlant1')" data-test="setup-plant-name" /></el-form-item>
        <el-form-item :label="$t('common.address')"><el-input v-model="co.address" /></el-form-item>
      </el-form>
      <div class="foot"><el-button @click="step = 0">{{ $t('common.back') }}</el-button><el-button type="primary" :loading="busy" data-test="setup-next-1" @click="saveCompany">{{ $t('implSetup.saveNext') }}</el-button></div>
    </HmCard>

    <HmCard v-if="step === 2" :title="$t('implSetup.n3ImportMasterData')" :sub="$t('implSetup.importInOrderUnitsOrganization')">
      <div class="bar"><el-button @click="openDownload('/api/impl/workbook.xlsx')"><el-icon><Download /></el-icon>{{ $t('implImport.initializationWorkbook') }}</el-button>
        <el-button type="primary" plain @click="router.push('/impl/import')">{{ $t('implSetup.openDataImport') }}</el-button></div>
      <table class="hm-table dense" data-test="setup-counts"><thead><tr><th>{{ $t('implSetup.masterData') }}</th><th class="num">{{ $t('implPacks.exists') }}</th><th /></tr></thead>
        <tbody><tr v-for="(n, k) in d.counts" :key="k"><td>{{ LBL[k] || k }}</td><td class="num">{{ n ?? '—' }}</td>
          <td><el-button link size="small" type="primary" @click="router.push('/impl/import?importer=' + k)">{{ $t('common.import') }}</el-button></td></tr></tbody></table>
      <div class="foot"><el-button @click="step = 1">{{ $t('common.back') }}</el-button><el-button type="primary" data-test="setup-next-2" @click="step = 3">{{ $t('common.next') }}</el-button></div>
    </HmCard>

    <HmCard v-if="step === 3" :title="$t('implSetup.n4EquipmentConnectivity')" :sub="$t('implSetup.equipmentRegisterTagListsEquipment')">
      <div class="bar"><el-button type="primary" plain data-test="setup-onboard" @click="router.push(onboard)">{{ $t('implSetup.openEquipmentConnectivity') }}</el-button>
        <el-button @click="router.push('/impl')">{{ $t('implSetup.implementationWizardTaskList') }}</el-button></div>
      <div class="foot"><el-button @click="step = 2">{{ $t('common.back') }}</el-button><el-button type="primary" :loading="busy" data-test="setup-finish" @click="finish">{{ $t('implSetup.finishEnterSystem') }}</el-button></div>
    </HmCard>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { get, loadSession, post } from '../api';
import PackPreview from '../impl/PackPreview.vue';
import { loadImplState, openDownload } from '../impl/state';
import { ALL_NAV } from '../layout/nav';

const router = useRouter();
const d = ref<any>({ packs: [], counts: {} }), step = ref(0), busy = ref(false);
const co = reactive<any>({ company: '', plant_code: '', plant_name: '', address: '' });
const LBL: Record<string, string> = {};
const onboard = computed(() => ALL_NAV.find((x) => /^\/onboard/.test(x.path) && x.path !== '/e/datasource')?.path || d.value.onboard || '/e/equipment');
async function reload() {
  d.value = await get('/api/impl/setup');
  co.company = d.value.company || ''; if (d.value.plant) { co.plant_code = d.value.plant.code; co.plant_name = d.value.plant.name; co.address = d.value.plant.address || ''; }
  for (const i of await get('/api/impl/importers')) LBL[i.key] = i.label;
  await loadImplState(true); await loadSession();
}
async function saveCompany() {
  if (!co.company.trim()) { ElMessage.warning($t('implSetup.pleaseEnterTheCompanyName')); return; }
  busy.value = true; try { await post('/api/impl/setup/company', co); step.value = 2; await reload(); } finally { busy.value = false; }
}
async function finish() {
  busy.value = true;
  try { await post('/api/impl/setup/finish', {}); await loadImplState(true); ElMessage.success($t('implSetup.setupComplete')); router.push('/'); } finally { busy.value = false; }
}
onMounted(reload);
</script>

<style scoped>
.steps { margin: 4px 0 16px; }
.steps :deep(.el-step__head.is-wait) { color: var(--hm-text-2); border-color: var(--hm-text-2); }
.steps :deep(.el-step__title.is-wait) { color: var(--hm-text-2); }
.packs { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; margin-bottom: 12px; }
.pk { border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); padding: 8px 10px; display: flex; flex-direction: column; gap: 2px; } .pk.on { border-color: rgba(21, 163, 74, .4); }
.foot, .bar { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
</style>
