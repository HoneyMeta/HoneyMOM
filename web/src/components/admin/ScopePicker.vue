<template>
  <div class="scope-pick">
    <el-radio-group v-model="mode" size="small" @change="emitNow">
      <el-radio-button value="all">{{ allLabel }}</el-radio-button>
      <el-radio-button value="some">{{ $t('scopePicker.selectPlantsWorkshopsLines') }}</el-radio-button>
    </el-radio-group>
    <div v-if="mode === 'some'" class="tree">
      <el-tree ref="tree" :data="nodes" node-key="key" show-checkbox check-strictly default-expand-all :props="{ label: 'label', children: 'children' }"
        :default-checked-keys="checked" @check="onCheck">
        <template #default="{ data }"><span class="nd"><HmTag :tone="data.tone">{{ data.kind }}</HmTag>{{ data.label }}</span></template>
      </el-tree>
      <p class="small muted">{{ $t('scopePicker.checkingAPlantTheWhole') }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// Data scope picker: value = null (all / by role) or { plants: [id], workshops: [id], lines: [id] }. Org tree comes from /api/admin/org.
import { computed, onMounted, ref, watch } from 'vue';
import { get } from '../../api';

const props = withDefaults(defineProps<{ modelValue: any; allLabel?: string }>(), { allLabel: $t('common.all') });
const emit = defineEmits<{ 'update:modelValue': [v: any] }>();
const org = ref<any>(null), tree = ref<any>(null);
const mode = ref<'all' | 'some'>('all');
const checked = ref<string[]>([]);

function fromValue(v: any) {
  const s = v || {};
  checked.value = [...(s.plants || []).map((x: number) => 'p' + x), ...(s.workshops || []).map((x: number) => 'w' + x), ...(s.lines || []).map((x: number) => 'l' + x)];
  mode.value = checked.value.length ? 'some' : 'all';
}
watch(() => props.modelValue, (v) => fromValue(v), { immediate: true });

const nodes = computed(() => {
  if (!org.value) return [];
  const ln = (x: any) => ({ key: 'l' + x.id, label: x.name, kind: $t('common.line'), tone: 'neutral' });
  const ws = (w: any) => ({ key: 'w' + w.id, label: w.name, kind: $t('common.workshop'), tone: 'primary', children: (w.lines || []).map(ln) });
  return [...org.value.plants.map((p: any) => ({ key: 'p' + p.id, label: p.name, kind: $t('common.plant'), tone: 'amber', children: p.workshops.map(ws) })),
    ...org.value.orphans.workshops.map((w: any) => ws({ ...w, lines: [] })), ...org.value.orphans.lines.map(ln)];
});

function value() {
  if (mode.value === 'all') return null;
  const keys: string[] = tree.value ? tree.value.getCheckedKeys() : checked.value;
  const pick = (c: string) => keys.filter((k) => k.startsWith(c)).map((k) => Number(k.slice(1)));
  const v = { plants: pick('p'), workshops: pick('w'), lines: pick('l') };
  return v.plants.length || v.workshops.length || v.lines.length ? v : null;
}
function onCheck() { checked.value = tree.value.getCheckedKeys(); emitNow(); }
function emitNow() { emit('update:modelValue', value()); }
onMounted(async () => { org.value = await get('/api/admin/org'); });
</script>

<style scoped>
.scope-pick { display: grid; gap: 8px; }
.tree { border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); padding: 6px 8px; max-height: 280px; overflow: auto; }
.nd { display: inline-flex; align-items: center; gap: 6px; font-size: var(--hm-fs-sm); }
.small { font-size: var(--hm-fs-xs); margin: 6px 0 0; }
.muted { color: var(--hm-text-3); }
</style>
