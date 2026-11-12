<template>
  <div class="page" data-test="onboard">
    <HmPageHead :title="TITLE[tab] || $t('common.connectivity')" :sub="SUB[tab]">
      <el-button v-if="tab !== 'overview'" size="small" @click="router.push('/onboard')">{{ $t('onboard.connectivityOverview') }}</el-button>
    </HmPageHead>
    <nav class="tabs" data-tour="onboard-tabs" :aria-label="$t('common.connectivity')">
      <router-link v-for="t in TABS" :key="t.k" :to="t.to" class="tb" :class="{ on: tab === t.k }" :data-test="'onboard-tab-' + t.k">{{ t.label }}</router-link>
    </nav>
    <OverviewTab v-if="tab === 'overview'" />
    <CheckTab v-else-if="tab === 'check'" />
    <DiscoverTab v-else-if="tab === 'discover'" />
    <ModelTab v-else-if="tab === 'model'" />
    <TwinTab v-else-if="tab === 'twin'" />
    <AiTab v-else-if="tab === 'ai'" />
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// Connectivity Center (M2): delivery package / tag list import → point-by-point check → discover & browse → plant model → floor plan & digital twin → AI assist. See docs/dev/factory-model.md.
import { computed, defineAsyncComponent } from 'vue';
import { useRoute, useRouter } from 'vue-router';

const OverviewTab = defineAsyncComponent(() => import('../components/onboard/OverviewTab.vue'));
const CheckTab = defineAsyncComponent(() => import('../components/onboard/CheckTab.vue'));
const DiscoverTab = defineAsyncComponent(() => import('../components/onboard/DiscoverTab.vue'));
const ModelTab = defineAsyncComponent(() => import('../components/onboard/ModelTab.vue'));
const TwinTab = defineAsyncComponent(() => import('../components/onboard/TwinTab.vue'));
const AiTab = defineAsyncComponent(() => import('../components/onboard/AiTab.vue'));
const route = useRoute(), router = useRouter();
const tab = computed(() => String(route.meta.tab || 'overview'));
const TABS = [{ k: 'overview', label: $t('nav.overviewImport'), to: '/onboard' }, { k: 'check', label: $t('common.tagCheck'), to: '/onboard/check' }, { k: 'discover', label: $t('common.discoverBrowse'), to: '/onboard/discover' },
  { k: 'model', label: $t('common.plantModel'), to: '/onboard/model' }, { k: 'twin', label: $t('common.floorPlanDigitalTwin'), to: '/onboard/twin' }, { k: 'ai', label: $t('nav.aiAssist'), to: '/onboard/ai' }];
const TITLE: Record<string, string> = { overview: $t('common.connectivity'), check: $t('common.tagCheck'), discover: $t('common.discoverBrowse'), model: $t('common.plantModel'), twin: $t('common.floorPlanDigitalTwin'), ai: $t('onboard.connectivityAiAssist') };
const SUB: Record<string, string> = {
  overview: $t('onboard.integratorsDeliverPerTheConnectivity'),
  check: $t('onboard.tagsInTheListThat'),
  discover: $t('onboard.opcUaBrowseNodesAnd'),
  model: $t('onboard.plantWorkshopLineWorkstationEquipmen'),
  twin: $t('onboard.importTheShopCadDxf'),
  ai: $t('onboard.draftsMeaningUnitEquipmentPurpose'),
};
</script>

<style scoped>
.tabs { display: flex; gap: 4px; flex-wrap: wrap; margin: -4px 0 14px; border-bottom: 1px solid var(--hm-hairline); }
.tb { padding: 7px 14px; font-size: var(--hm-fs-sm); color: var(--hm-text-2); text-decoration: none; border-bottom: 2px solid transparent; margin-bottom: -1px; }
.tb:hover { color: var(--hm-primary); }
.tb.on { color: var(--hm-primary); border-bottom-color: var(--hm-primary); font-weight: 600; }
</style>
