<template>
  <div class="page" data-test="forbidden">
    <HmEmpty icon="lock" :title="$t('forbidden.youDonTHavePermission')" :desc="$t('forbidden.requiredPermission0AskAn', [need])" />
    <div class="acts"><el-button type="primary" @click="router.push('/')">{{ $t('featureOff.backToHome') }}</el-button></div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';

const route = useRoute(), router = useRouter();
const need = computed(() => String(route.query.perm || '') || '—');
</script>

<style scoped>
.page { padding-top: 60px; }
.acts { display: flex; justify-content: center; margin-top: 16px; }
</style>
