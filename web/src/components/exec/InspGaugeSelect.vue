<template>
  <el-select :model-value="modelValue" :placeholder="placeholder" filterable clearable :size="size" :class="['igs', { need: required && !modelValue }]"
    data-test="insp-gauge" @update:model-value="(v: any) => emit('update:modelValue', v ?? null)" @visible-change="(v: boolean) => v && load()">
    <el-option v-for="g in list" :key="g.id" :value="g.id" :label="g.label" :disabled="!g.ok">
      <span class="hm-mono">{{ g.code }}</span> <span>{{ g.name }}</span>
      <small :class="g.ok ? 'ok' : 'bad'">{{ g.ok ? $t('inspGaugeSelect.due0', [g.next_due || '—']) : $t('inspGaugeSelect.unavailable') }}</small>
    </el-option>
    <template #footer v-if="bad.length"><div class="why">{{ $t('inspGaugeSelect.n0UnavailableExpiredLockedPending', [bad.length, bad[0].why]) }}</div></template>
  </el-select>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// Gauge picker for inspection entry: only valid gauges (calibrated, released, not expired) are selectable; expired/locked ones are listed greyed out with the reason (IATF 16949 7.1.5).
// Required when the inspection item specifies a gauge type (insp_item.gauge_type); API GET /api/gauge/valid?item=
import { computed, onMounted, ref } from 'vue';
import { get } from '../../api';

const props = withDefaults(defineProps<{ modelValue?: number | null; item?: any; gtype?: string; size?: 'small' | 'default' | 'large' }>(), { modelValue: null, size: 'default' });
const emit = defineEmits<{ 'update:modelValue': [v: number | null] }>();
const list = ref<any[]>([]), kind = ref('');
const required = computed(() => !!(props.item?.gauge_type || props.gtype));
const bad = computed(() => list.value.filter((g) => !g.ok));
const placeholder = computed(() => (required.value ? $t('inspGaugeSelect.select0Required', [kind.value || $t('common.gauge')]) : $t('inspGaugeSelect.gaugeOptional')));
let loaded = false;
async function load() {
  if (loaded) return;
  loaded = true;
  const qs = props.item?.id ? `item=${props.item.id}` : `gtype=${props.gtype || ''}`;
  const r = await get('/api/gauge/valid?' + qs, true).catch(() => ({ items: [] }));
  list.value = r.items || []; kind.value = r.gtype_label || '';
}
onMounted(() => { if (required.value) load(); });
</script>

<style scoped>
.igs { width: 100%; }
.igs.need :deep(.el-select__wrapper) { box-shadow: 0 0 0 1px var(--hm-idle) inset; }
small { margin-left: 6px; } small.ok { color: var(--hm-text-2); } small.bad { color: var(--hm-fault-ink); }
.why { font-size: var(--hm-fs-2xs); color: var(--hm-text-2); padding: 2px 4px; max-width: 360px; white-space: normal; }
</style>
