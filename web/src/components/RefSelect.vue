<template>
  <el-select :model-value="modelValue" filterable remote clearable :remote-method="search" :loading="loading" :placeholder="$t('refSelect.typeToSearch')" style="width:100%"
    @update:model-value="(v: any) => emit('update:modelValue', v)" @visible-change="(v: boolean) => v && !opts.length && search('')">
    <el-option v-for="o in opts" :key="o.id" :label="o.label" :value="o.id" />
  </el-select>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { get } from '../api';
import { codeName } from '../design/format';

const props = defineProps<{ modelValue: any; entity: string; label?: string }>();
const emit = defineEmits(['update:modelValue']);
const opts = ref<any[]>([]), loading = ref(false);
const lab = (r: any) => (r.code && r.name ? codeName(r.code, r.name) : r.sn || r.name || r.code || r.title || r.username || `#${r.id}`);
async function search(q: string) {
  loading.value = true;
  try { opts.value = (await get(`/api/e/${props.entity}?size=50&q=${encodeURIComponent(q)}`, true)).items.map((r: any) => ({ id: r.id, label: lab(r) })); } finally { loading.value = false; }
}
onMounted(() => { if (props.modelValue) opts.value = [{ id: props.modelValue, label: props.label || `#${props.modelValue}` }]; });
</script>
