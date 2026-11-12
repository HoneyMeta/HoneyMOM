<template>
  <div class="cff">
    <a v-if="modelValue?.token" class="lnk" :href="href" target="_blank">{{ modelValue.name }}</a>
    <span v-else class="muted small">{{ $t('cfFile.notUploaded') }}</span>
    <label class="el-button el-button--small"><input type="file" hidden @change="pick" />{{ busy ? $t('cfFile.uploading') : modelValue?.token ? $t('cfFile.replace') : $t('cfFile.upload') }}</label>
    <el-button v-if="modelValue?.token" size="small" link @click="$emit('update:modelValue', null)">{{ $t('cfFile.clear') }}</el-button>
  </div>
</template>

<script setup lang="ts">
// "File" type custom field: uploads to /api/impl/files; value is {name, token, size}
import { computed, ref } from 'vue';
import { tok } from '../api';
import { uploadJson } from './state';

const props = defineProps<{ modelValue: any }>();
const emit = defineEmits(['update:modelValue']);
const busy = ref(false);
const href = computed(() => `/api/impl/files/${props.modelValue?.token}?token=${tok()}`);
async function pick(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0];
  if (!f) return;
  busy.value = true;
  try { emit('update:modelValue', await uploadJson('/api/impl/files', f)); } finally { busy.value = false; (e.target as HTMLInputElement).value = ''; }
}
</script>

<style scoped>
.cff { display: flex; align-items: center; gap: 8px; } .lnk { color: var(--hm-primary); }
</style>
