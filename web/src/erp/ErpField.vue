<template>
  <RefSelect v-if="f?.t === 'ref'" :model-value="modelValue" :entity="f.ref" :label="label" @update:model-value="emit('update:modelValue', $event)" />
  <el-select v-else-if="f?.t === 'enum'" :model-value="modelValue" clearable style="width: 100%" @update:model-value="emit('update:modelValue', $event)">
    <el-option v-for="(lab, k) in f.enum" :key="k" :label="lab" :value="k" />
  </el-select>
  <el-date-picker v-else-if="f?.t === 'date'" :model-value="modelValue" type="date" value-format="YYYY-MM-DD" style="width: 100%" @update:model-value="emit('update:modelValue', $event)" />
  <el-date-picker v-else-if="f?.t === 'datetime'" :model-value="modelValue" type="datetime" value-format="YYYY-MM-DD HH:mm:ss" style="width: 100%"
    @update:model-value="emit('update:modelValue', $event)" />
  <el-input-number v-else-if="f?.t === 'int' || f?.t === 'float'" :model-value="modelValue ?? undefined" :controls="false" style="width: 100%"
    @update:model-value="emit('update:modelValue', $event)" />
  <el-switch v-else-if="f?.t === 'bool'" :model-value="!!modelValue" @update:model-value="emit('update:modelValue', $event)" />
  <el-input v-else :model-value="modelValue" :type="f?.t === 'text' ? 'textarea' : 'text'" :rows="2" @update:model-value="emit('update:modelValue', $event)" />
</template>

<script setup lang="ts">
// One field of an ERP document form: control chosen by the type in the entity schema (/api/meta) (reference → RefSelect, options → dropdown, date, number, switch, text).
import { computed } from 'vue';
import RefSelect from '../components/RefSelect.vue';
import { field } from './docs';

const props = defineProps<{ modelValue: any; entity: string; name: string; label?: string }>();
const emit = defineEmits(['update:modelValue']);
const f = computed(() => field(props.entity, props.name));
</script>
