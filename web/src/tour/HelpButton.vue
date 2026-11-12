<template>
  <el-dropdown trigger="hover" placement="bottom-start" :show-timeout="350" @command="onCmd">
    <button class="hm-help" type="button" :data-tour="tourId" data-test="page-help" :aria-label="$t('helpButton.guidedTour0', [title])" @click="openPageGuide()">
      <span aria-hidden="true">?</span><em v-if="label">{{ label }}</em>
    </button>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item command="page"><el-icon><Guide /></el-icon>{{ $t('helpButton.tourOfThisPage0', [title]) }}</el-dropdown-item>
        <el-dropdown-item v-for="g in TASK_GUIDES()" :key="g.key" :command="g.key" :divided="g.key === 'task-report'"><el-icon><Pointer /></el-icon>{{ g.title }}</el-dropdown-item>
        <el-dropdown-item command="welcome" divided><el-icon><Compass /></el-icon>{{ $t('helpButton.welcomeTourForNewUsers') }}</el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// "?" guided tour button: click opens this page's tour; hover expands a menu (three task walkthroughs, welcome tour).
// HmPageHead includes one; for pages without HmPageHead the shell places one on the breadcrumb row (tourId=shell-help).
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { guideForPath, openGuide, openPageGuide, TASK_GUIDES } from './index';

withDefaults(defineProps<{ tourId?: string; label?: string }>(), { tourId: 'page-help', label: '' });
const route = useRoute();
const title = computed(() => guideForPath(route.path)?.title || $t('common.welcome'));
function onCmd(c: string) { if (c === 'page') openPageGuide(); else openGuide(c); }
</script>

<style scoped>
.hm-help { display: inline-flex; align-items: center; gap: 5px; height: calc(22px * var(--hm-scale)); min-width: calc(22px * var(--hm-scale)); padding: 0 6px; border-radius: var(--hm-r-pill);
  border: 1px solid var(--hm-border-strong); background: var(--hm-surface); color: var(--hm-text-2); font: inherit; font-size: var(--hm-fs-xs); font-weight: 700; cursor: pointer; justify-content: center;
  transition: border-color var(--hm-dur-1), color var(--hm-dur-1), background var(--hm-dur-1); }
.hm-help:hover { border-color: var(--hm-primary); color: var(--hm-primary); background: var(--hm-primary-soft); }
.hm-help em { font-style: normal; font-weight: 500; }
</style>
