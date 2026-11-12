<template>
  <div class="page">
    <div class="page-head"><h2>{{ $t('common.approvalCenter') }}</h2><span class="sub">{{ $t('approvals.documentApprovalsConfiguredUnderImpl') }}</span>
      <span class="grow" /><el-radio-group v-model="status" @change="load"><el-radio-button value="pending">{{ $t('approvals.pending0', [pendingN ? `(${pendingN})` : '']) }}</el-radio-button><el-radio-button value="done">{{ $t('common.resolved') }}</el-radio-button></el-radio-group>
      <el-button @click="load"><el-icon><Refresh /></el-icon></el-button></div>
    <p v-if="loaded && !items.length" class="card muted">{{ status === 'pending' ? $t('approvals.noPendingApprovalsApprovalsSubmitted') : $t('approvals.noProcessedApprovalsYet') }}</p>
    <div class="list">
      <ApprovalCard v-for="a in items" :key="a.id + a.status" :a="a" @done="load" @trace="openTrace" :data-test="'approval-' + a.id" />
    </div>
    <el-drawer v-model="trOpen" :title="tr ? $t('approvals.run01', [tr.id, tr.agent_name]) : ''" size="min(560px, 100%)">
      <template v-if="tr">
        <div class="small muted" style="margin-bottom:8px">{{ tr.created_at }} · {{ tr.mode === 'dry' ? $t('common.testRun') : $t('agentEditor.liveRun') }} · {{ STATUS_CN[tr.status] || tr.status }}
          <el-button link type="primary" @click="router.push(`/agents/${tr.agent}?run=${tr.id}`)">{{ $t('approvals.viewOnCanvas') }}</el-button></div>
        <TraceViewer :steps="tr.steps" :types="types" />
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { get, STATUS_CN } from '../api';
import ApprovalCard from '../components/ApprovalCard.vue';
import TraceViewer from '../components/TraceViewer.vue';

const router = useRouter();
const status = ref('pending'), items = ref<any[]>([]), loaded = ref(false), pendingN = ref(0), trOpen = ref(false), tr = ref<any>(null), types = ref<any>({});
async function load() {
  items.value = await get('/api/approvals?status=' + status.value);
  pendingN.value = (await get('/api/approvals/count')).pending;
  loaded.value = true;
  window.dispatchEvent(new Event('approvals-changed'));
}
async function openTrace(rid: number) {
  tr.value = await get(`/api/agents/runs/${rid}`);
  if (!Object.keys(types.value).length) types.value = (await get('/api/agents/meta')).types;
  trOpen.value = true;
}
onMounted(load);
</script>

<style scoped>
.list { display: grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 12px; align-items: start; }
@media (max-width: 700px) { .list { grid-template-columns: 1fr; } }
</style>
