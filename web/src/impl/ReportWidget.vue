<template>
  <div class="rw" :data-test="'report-widget-' + rid">
    <HmSkeleton v-if="loading && !res" variant="block" :height="200" />
    <template v-else-if="res">
      <ReportView :res="res" :chart="res.chart" :height="220" :title="res.report?.name" table-max="240px" />
      <div class="ft"><router-link :to="`/impl/reports?id=${rid}`" class="small">{{ $t('reportWidget.openInReportDesigner') }}</router-link></div>
    </template>
    <HmEmpty v-else compact :title="$t('reportWidget.reportCanTBeOpened')" :desc="err" />
  </div>
</template>

<script setup lang="ts">
// Report pinned to Home (Report Designer "Pin to Home" = add report:<id> to the layout in /api/home/prefs)
import { onMounted, onUnmounted, ref } from 'vue';
import { call } from '../api';
import ReportView from './ReportView.vue';

const props = defineProps<{ rid: number }>();
const res = ref<any>(null), loading = ref(true), err = ref('');
let t: any = null;
async function load() {
  try { res.value = await call('GET', `/api/impl/reports/${props.rid}/data`, undefined, true); err.value = ''; } catch (e: any) { err.value = e?.message || ''; } finally { loading.value = false; }
}
onMounted(() => { load(); t = setInterval(load, 120000); });
onUnmounted(() => clearInterval(t));
</script>

<style scoped>
.rw { min-height: 120px; } .ft { text-align: right; margin-top: 4px; }
</style>
