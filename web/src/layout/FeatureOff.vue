<template>
  <div class="page fo" data-test="feature-off">
    <HmEmpty icon="ai" :title="$t('featureOff.n0IsOff', [label])" :desc="desc">
      <el-button v-if="isAdmin" type="primary" @click="router.push('/system/ai')">{{ $t('featureOff.enable') }}</el-button>
      <el-button @click="router.push('/')">{{ $t('featureOff.backToHome') }}</el-button>
    </HmEmpty>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// When an AI feature the page needs is off, the shell shows this page instead (the original page isn't loaded, so disabled APIs aren't called).
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import { aiFeatureLabel, aiOn } from '../ai';
import { session } from '../api';
import HmEmpty from '../design/components/HmEmpty.vue';

const props = defineProps<{ feature: string }>();
const router = useRouter();
const isAdmin = computed(() => (session.user?.role_list || []).includes('admin'));
const label = computed(() => aiFeatureLabel(props.feature));
const desc = computed(() => (aiOn() ? $t('featureOff.theAiFeature0Is', [label.value]) : $t('featureOff.aiFeaturesAreNotEnabled')) + (isAdmin.value ? $t('featureOff.youCanTurnThemOn') : $t('featureOff.askAnAdministratorToTurn')));
</script>

<style scoped>
.fo { display: grid; place-items: center; min-height: 60vh; }
</style>
