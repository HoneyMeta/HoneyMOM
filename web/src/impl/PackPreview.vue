<template>
  <div class="pp" data-test="pack-preview">
    <div class="sel">
      <el-checkbox-group v-model="chosen" data-test="pack-choose">
        <el-checkbox v-for="p in packs" :key="p.code" :value="p.code" :data-test="'pack-check-' + p.code">{{ p.name }}<span v-if="p.applied" class="muted small">{{ $t('packPreview.applied') }}</span></el-checkbox>
      </el-checkbox-group>
      <el-switch v-model="demo" :active-text="$t('packPreview.includeDemoData')" data-test="pack-demo" />
      <el-button :loading="busy" :disabled="!chosen.length" data-test="pack-preview-btn" @click="preview">{{ $t('common.preview') }}</el-button>
      <el-button v-if="canApply" type="primary" :loading="applying" :disabled="!rep?.ok" data-test="pack-apply-btn" @click="apply">{{ $t('packPreview.apply') }}</el-button>
    </div>
    <template v-if="rep">
      <el-alert v-if="!rep.ok" type="error" :closable="false" show-icon :title="$t('packPreview.previewFailed') + rep.error" />
      <el-alert v-else type="success" :closable="false" show-icon data-test="pack-preview-ok"
        :title="$t('packPreview.willAdd0Skip1', [sum.create, sum.skip, sum.deferred ? $t('packPreview.n0DeferredUntilEquipmentIs', [sum.deferred]) : '', rep.reports, rep.packs.map(nameOf).join(' → ')])" />
      <div v-if="Object.keys(rep.new_enums || {}).length" class="enums small">
        <b>{{ $t('packPreview.newOptions') }}</b><span v-for="(opts, path) in rep.new_enums" :key="path">{{ $t('packPreview.n01', [ENUM_CN[path] || path, Object.values(opts).join($t('common.k42'))]) }}</span>
      </div>
      <table class="hm-table dense" data-test="pack-steps">
        <thead><tr><th>{{ $t('common.template') }}</th><th>{{ $t('common.content') }}</th><th class="num">{{ $t('common.row') }}</th><th class="num">{{ $t('common.add') }}</th><th class="num">{{ $t('common.skip') }}</th><th class="num">{{ $t('packPreview.deferred') }}</th></tr></thead>
        <tbody><tr v-for="(s, i) in rep.steps" :key="i"><td>{{ packLabel(s.pack) }}</td><td>{{ s.step }}</td><td class="num">{{ s.rows }}</td><td class="num">{{ s.create }}</td>
          <td class="num">{{ s.skip }}</td><td class="num">{{ s.deferred || '' }}</td></tr></tbody>
      </table>
    </template>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// Industry template preview + apply (shared by the Industry Templates page and Setup Wizard): preview = backend runs it for real in a transaction, then rolls back
import { ElMessage, ElMessageBox } from 'element-plus';
import { computed, ref, watch } from 'vue';
import { post } from '../api';

const props = defineProps<{ packs: any[]; initial?: string[]; canApply?: boolean; demoDefault?: boolean }>();
const emit = defineEmits(['applied']);
const chosen = ref<string[]>(props.initial || []), demo = ref(!!props.demoDefault), rep = ref<any>(null), busy = ref(false), applying = ref(false);
watch(() => props.initial, (v) => { if (v) chosen.value = [...v]; });
watch([chosen, demo], () => { rep.value = null; }, { deep: true });
const ENUM_CN: Record<string, string> = { 'process.ptype': $t('packPreview.operationTypes'), 'equipment.etype': $t('common.equipmentType'), 'workshop.wtype': $t('packPreview.workshopTypes'), 'defect_code.category': $t('packPreview.defectCategories') };
const nameOf = (c: string) => props.packs.find((p) => p.code === c)?.name || (c === 'common' ? $t('packPreview.general') : c);
const packLabel = (s: string) => /\u00b7(Demo|\u6f14\u793a)$/.test(s || '') ? nameOf(s.replace(/\u00b7(Demo|\u6f14\u793a)$/, '')) + $t('packPreview.demo2') : nameOf(s);
const sum = computed(() => (rep.value?.steps || []).reduce((a: any, s: any) => ({ create: a.create + (s.create || 0), skip: a.skip + (s.skip || 0), deferred: a.deferred + (s.deferred || 0) }),
  { create: 0, skip: 0, deferred: 0 }));
async function preview() { busy.value = true; try { rep.value = await post('/api/impl/packs/preview', { packs: chosen.value, demo: demo.value }); } finally { busy.value = false; } }
async function apply() {
  await ElMessageBox.confirm($t('packPreview.apply01ExistingData', [chosen.value.map(nameOf).join($t('common.k42')), demo.value ? $t('packPreview.withDemoData') : '']), $t('packPreview.applyIndustryTemplate'));
  applying.value = true;
  try { const r = await post('/api/impl/packs/apply', { packs: chosen.value, demo: demo.value }); ElMessage.success($t('packPreview.applied2')); emit('applied', r); rep.value = null; } finally { applying.value = false; }
}
defineExpose({ preview });
</script>

<style scoped>
.pp { display: flex; flex-direction: column; gap: 10px; }
.sel { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.enums { background: var(--hm-surface-2); border-radius: var(--hm-r-md); padding: 6px 10px; }
</style>
