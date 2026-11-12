<template>
  <div class="tw" data-test="onboard-twin">
    <HmToolbar class="bar" data-tour="twin-bar">
      <el-radio-group v-model="view" size="small" data-test="twin-view">
        <el-radio-button value="2d">{{ $t('twinTab.n2dPlan') }}</el-radio-button>
        <el-radio-button value="3d">3D</el-radio-button>
      </el-radio-group>
      <el-button v-perm="'factory.layout.edit'" size="small" :loading="busy === 'dxf'" data-test="dxf-upload" @click="uploadDxf">{{ $t('twinTab.importDxfFloorPlan') }}</el-button>
      <el-button v-perm="'factory.layout.edit'" size="small" :loading="busy === 'bind'" data-test="autobind" @click="autobind">{{ $t('twinTab.autoPlaceByBlockAttributes') }}</el-button>
      <el-button v-if="view === '2d'" v-perm="'factory.layout.edit'" size="small" :type="edit ? 'primary' : 'default'" data-test="twin-edit" @click="toggleEdit">{{ edit ? $t('twinTab.exitDragMode') : $t('twinTab.dragToPlace') }}</el-button>
      <el-button v-if="edit" v-perm="'factory.layout.edit'" size="small" type="primary" :disabled="!Object.keys(pending).length" data-test="twin-save" @click="savePos">{{ $t('twinTab.savePositions0', [Object.keys(pending).length]) }}</el-button>
      <el-switch v-if="view === '3d'" v-model="useModels" size="small" :active-text="$t('twinTab.useGlbFromPackage')" />
      <span class="grow" />
      <span class="small muted">{{ $t('twinTab.n01Machines', [lay?.layout ? $t('twinTab.floorPlan0', [lay.layout.name]) : $t('twinTab.noFloorPlanEquipmentOnly'), lay?.devices?.length || 0]) }}<template v-if="unplaced.length"> {{ $t('twinTab.unplaced0', [unplaced.length]) }}</template></span>
    </HmToolbar>
    <div v-if="bindMsg" class="bind small" data-test="bind-result">{{ bindMsg }}</div>
    <div class="body">
      <div class="canvas">
        <Twin2D v-if="view === '2d' && lay" ref="t2" :layout="lay" :live="live" :editable="edit" :selected="pickCode" @moved="(m) => (pending = m)" @pick="(d) => (pickCode = d.code)" />
        <Twin3D v-else-if="view === '3d' && lay" :key="'3d' + useModels + reloadKey" :layout="lay" :live="live" :use-models="useModels" @pick="(c) => (pickCode = c)" />
      </div>
      <div class="side">
        <HmCard v-if="edit" :title="$t('twinTab.unplacedEquipment')" data-test="unplaced">
          <p class="small muted">{{ $t('twinTab.dragOntoTheFloorPlan') }}</p>
          <div class="chips"><span v-for="d in unplaced" :key="d.code" class="chip" draggable="true" :data-code="d.code" @dragstart="(e) => e.dataTransfer?.setData('text/device', d.code)">{{ d.code }} {{ d.name }}</span></div>
          <div v-if="!unplaced.length" class="small muted">{{ $t('twinTab.allPlaced') }}</div>
        </HmCard>
        <HmCard v-if="picked" :title="`${picked.code} ${picked.name}`" data-test="twin-pick">
          <div class="kv"><span>{{ $t('common.status') }}</span><b>{{ liveOf(picked.code)?.status_text || $t('common.offline') }}</b></div>
          <div class="kv"><span>{{ $t('common.position') }}</span><span class="hm-num">{{ picked.x }}, {{ picked.y }} m · {{ picked.rot || 0 }}°</span></div>
          <div class="kv"><span>{{ $t('modelTab.size') }}</span><span class="hm-num">{{ picked.w }} × {{ picked.d }} m</span></div>
          <div class="kv"><span>{{ $t('twinTab.n3d') }}</span><span>{{ picked.model3d || $t('twinTab.generateFromTemplate') + picked.kind3d + $t('twinTab.k1113') }}</span></div>
          <div v-for="(v, k) in liveOf(picked.code)?.tags || {}" :key="k" class="kv"><span class="hm-mono small">{{ k }}</span><span class="hm-num">{{ v }}</span></div>
        </HmCard>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
import { ElMessage } from 'element-plus';
import { computed, defineAsyncComponent, onMounted, onUnmounted, ref } from 'vue';
import { get, post, put } from '../../api';
import Twin2D from '../factory/Twin2D.vue';
import { fileB64, pickFile } from './common';

const Twin3D = defineAsyncComponent(() => import('../factory/Twin3D.vue'));
const view = ref<'2d' | '3d'>('2d'), edit = ref(false), useModels = ref(true), lay = ref<any>(null), live = ref<any[]>([]), busy = ref(''), bindMsg = ref('');
const pending = ref<Record<string, { x: number; y: number }>>({}), pickCode = ref(''), allDevs = ref<any[]>([]), reloadKey = ref(0), t2 = ref<any>(null);
const placed = computed(() => new Set((lay.value?.devices || []).map((d: any) => d.code)));
const unplaced = computed(() => allDevs.value.filter((d) => d.x === null && !placed.value.has(d.code) && !pending.value[d.code]));
const picked = computed(() => (lay.value?.devices || []).find((d: any) => d.code === pickCode.value) || null);
const liveOf = (code: string) => live.value.find((x) => x.code === code);
let timer: any = null;

async function load() {
  lay.value = await get('/api/factory/layout', true).catch(() => ({ ok: false }));
  if (lay.value && !lay.value.ok) lay.value = { ...lay.value, devices: [], prims: [], areas: [], floor: [100, 50] };
  allDevs.value = (await get('/api/factory/devices', true).catch(() => ({ items: [] }))).items;
}
async function poll() { live.value = (await get('/api/factory/live', true).catch(() => ({ items: live.value }))).items; }
async function uploadDxf() {
  const f = await pickFile('.dxf'); if (!f) return;
  busy.value = 'dxf';
  try {
    const r = await post('/api/factory/layout/dxf', { name: f.name, data: await fileB64(f) });
    bindMsg.value = $t('twinTab.floorPlan0Entities1', [r.prims, r.areas, r.blocks, r.bind.matched]) + (r.bind.unmatched_blocks.length ? $t('twinTab.onTheDrawingButNot', [r.bind.unmatched_blocks.join($t('common.k42'))]) : '')
      + (r.bind.unplaced_devices.length ? $t('twinTab.notYetPlaced0', [r.bind.unplaced_devices.join($t('common.k42'))]) : '');
    await load(); reloadKey.value++;
  } finally { busy.value = ''; }
}
async function autobind() {
  busy.value = 'bind';
  try {
    const r = await post('/api/factory/layout/autobind', {});
    bindMsg.value = r.reason || $t('twinTab.matched0MachinesByDxf', [r.matched, r.moved]) + (r.unmatched_blocks.length ? $t('twinTab.onTheDrawingButNot2', [r.unmatched_blocks.join($t('common.k42'))]) : '')
      + (r.unplaced_devices.length ? $t('twinTab.notYetPlaced02', [r.unplaced_devices.join($t('common.k42'))]) : '');
    await load(); reloadKey.value++;
  } finally { busy.value = ''; }
}
function toggleEdit() { edit.value = !edit.value; if (!edit.value) { pending.value = {}; t2.value?.reset(); } }
async function savePos() {
  const r = await put('/api/factory/layout/positions', { items: pending.value });
  ElMessage.success($t('twinTab.positionsSavedFor0Machines', [r.moved]));
  pending.value = {}; t2.value?.reset(); await load();
}
onMounted(() => { load(); poll(); timer = setInterval(() => { if (!document.hidden) poll(); }, 3000); });
onUnmounted(() => clearInterval(timer));
</script>

<style scoped>
.tw { display: grid; gap: 10px; }
.bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.grow { flex: 1; }
.bind { padding: 6px 10px; background: var(--hm-primary-soft); border-radius: 6px; color: var(--hm-text-2); }
.body { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 12px; align-items: start; }
@media (max-width: 1279px) { .body { grid-template-columns: minmax(0, 1fr); } }
.canvas { height: calc(100vh - 260px); min-height: 420px; border: 1px solid var(--hm-hairline); border-radius: var(--hm-r-md); overflow: hidden; }
.side { display: grid; gap: 12px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { padding: 3px 8px; border-radius: 12px; background: var(--hm-surface-3); font-size: var(--hm-fs-xs); cursor: grab; }
.kv { display: flex; justify-content: space-between; gap: 10px; font-size: var(--hm-fs-sm); padding: 3px 0; border-bottom: 1px dashed var(--hm-hairline); }
.small { font-size: var(--hm-fs-xs); } .muted { color: var(--hm-text-3); }
</style>
