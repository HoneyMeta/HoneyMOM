<template>
  <DraftCard v-if="a.kind === 'ai_draft' && a.draft" :draft="a.draft" via="approvals" @done="$emit('done')" />
  <div v-else class="apc" :class="{ compact, done: a.status !== 'pending' }">
    <div class="top">
      <el-tag size="small" :type="a.kind === 'form' ? 'primary' : 'warning'" effect="plain">{{ a.kind === 'form' ? $t('common.required') : $t('common.pendingApproval') }}</el-tag>
      <el-tag v-if="a.dry_run" size="small" type="info">{{ $t('common.testRun') }}</el-tag>
      <b class="ttl">{{ a.title }}</b>
    </div>
    <div class="muted small"><template v-if="a.kind === 'flow'">{{ $t('approvalCard.approvalFlow0Step1', [a.agent_name, a.flow?.step, a.flow?.steps, a.flow?.requester]) }}</template><template v-else>{{ $t('approvalCard.fromAgent0', [a.agent_name]) }}</template> {{ $t('approvalCard.toBeHandledBy0', [a.role_label, (a.created_at || '').slice(5, 16)]) }}
      <template v-if="a.due_at"> {{ $t('approvalCard.n0Ago', [a.due_at.slice(5, 16)]) }}</template>
      <el-button v-if="!compact" link type="primary" size="small" @click="$emit('trace', a.run)">{{ $t('approvalCard.viewAiSteps') }}</el-button></div>
    <div v-if="a.summary" class="sum mdx" v-html="md(a.summary)" />
    <template v-if="a.status === 'pending'">
      <div v-if="editing || a.kind === 'form'" class="edit">
        <div v-for="k in keys" :key="k" class="row">
          <label>{{ label(k) }}</label>
          <el-switch v-if="typeof form[k] === 'boolean'" v-model="form[k]" />
          <el-input v-else-if="isComplex(k)" v-model="json[k]" type="textarea" :rows="3" class="mono" />
          <el-input v-else v-model="form[k]" :type="String(form[k] ?? '').length > 40 ? 'textarea' : 'text'" :autosize="{ minRows: 1, maxRows: 6 }" />
        </div>
      </div>
      <template v-else-if="keys.length">
        <el-button v-if="a.summary" link size="small" @click="showKv = !showKv">{{ showKv ? $t('approvalCard.hidePendingContent') : $t('approvalCard.viewRawPendingContent') }}</el-button>
        <div v-if="!a.summary || showKv" class="kv">
          <div v-for="k in keys" :key="k"><span class="muted">{{ $t('common.n02', [label(k)]) }}</span>{{ show(a.payload[k]) }}</div>
        </div>
      </template>
      <div v-if="rejecting" class="rej"><el-input v-model="reason" type="textarea" :rows="2" :placeholder="$t('approvalCard.rejectionReasonSentBackTo')" /></div>
      <div v-if="a.can_decide" class="btns">
        <template v-if="!rejecting">
          <el-button type="primary" size="small" :loading="busy" @click="approve">{{ a.kind === 'form' ? $t('approvalCard.submit') : editing ? $t('draftCard.approveWithEdits') : $t('common.approve') }}</el-button>
          <el-button v-if="a.kind !== 'form' && a.kind !== 'flow' && keys.length && !editing" size="small" @click="startEdit">{{ $t('approvalCard.editThenApprove') }}</el-button>
          <el-button size="small" type="danger" plain @click="rejecting = true">{{ a.kind === 'form' ? $t('approvalCard.leaveBlank') : $t('common.reject') }}</el-button>
        </template>
        <template v-else>
          <el-button size="small" type="danger" :loading="busy" @click="reject">{{ $t('draftCard.confirmRejection') }}</el-button>
          <el-button size="small" @click="rejecting = false">{{ $t('common.cancel') }}</el-button>
        </template>
      </div>
      <div v-else class="muted small">{{ $t('approvalCard.requires0', [a.role_label]) }}</div>
    </template>
    <div v-else class="res small">
      <el-tag size="small" :type="statusType(a.status)">{{ STATUS_CN[a.status] || a.status }}</el-tag>
      {{ a.decided_by || '' }} {{ (a.decided_at || '').slice(5, 16) }}<span v-if="a.reason">{{ $t('common.n0', [a.reason]) }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, reactive, ref } from 'vue';
import { post, STATUS_CN, statusType } from '../api';
import { md } from '../md';
import DraftCard from '../ai/DraftCard.vue';     // AI action draft (kind=ai_draft): before/after comparison

const props = defineProps<{ a: any; compact?: boolean }>();
const emit = defineEmits(['done', 'trace']);
const showKv = ref(false), editing = ref(false), rejecting = ref(false), reason = ref(''), busy = ref(false);
const form = reactive<Record<string, any>>({}), json = reactive<Record<string, string>>({});
const LABELS: Record<string, string> = { equipment: $t('common.equipment'), fault: $t('common.faultDescription'), level: $t('approvalCard.urgency'), parts_used: $t('approvalCard.sparePartsList'), cause: $t('approvalCard.preliminaryCause'), ncr: $t('approvalCard.ncr'), d1: $t('approvalCard.d1Team'),
  d2: $t('approvalCard.d2ProblemDescription'), d3: $t('approvalCard.d3ContainmentActions'), material: $t('common.material'), qty: $t('common.quantity'), work_order: $t('common.workOrder'), reason: $t('common.reason'), title: $t('common.title'), proposal: $t('approvalCard.suggestedContent'), changes: $t('approvalCard.adjustments'),
  with_8d: $t('approvalCard.alsoDraftAn8d'), description: $t('common.problemDescription'), defect: $t('common.defect'), sn: $t('common.partCode'), station: $t('common.workstation'), close_note: $t('common.resolution'), andon: $t('common.andon'), warehouse: $t('common.warehouse'), atype: $t('common.type') };
const fieldLabels = computed(() => Object.fromEntries((props.a.fields && Array.isArray(props.a.fields) ? props.a.fields : []).map((f: any) => [f.key, f.label])));
const label = (k: string) => fieldLabels.value[k] || LABELS[k] || k;
const keys = computed(() => Object.keys(props.a.payload && typeof props.a.payload === 'object' ? props.a.payload : {}));
const isComplex = (k: string) => props.a.payload && typeof props.a.payload[k] === 'object' && props.a.payload[k] !== null;
const show = (v: any) => (v === true ? $t('common.yes') : v === false ? $t('common.no') : typeof v === 'object' && v !== null ? JSON.stringify(v) : v === '' || v == null ? '—' : String(v));
function init() {
  for (const k of keys.value) {
    if (isComplex(k)) json[k] = JSON.stringify(props.a.payload[k], null, 1);
    else form[k] = props.a.payload[k];
  }
}
init();
function startEdit() { init(); editing.value = true; }
function collect() {
  const out: Record<string, any> = {};
  for (const k of keys.value) {
    if (isComplex(k)) { try { out[k] = JSON.parse(json[k]); } catch { throw new Error($t('approvalCard.n0HasAnInvalidFormat', [label(k)])); } } else {
      const v = form[k];
      out[k] = typeof props.a.payload[k] === 'number' && v !== '' && !isNaN(Number(v)) ? Number(v) : v;
    }
  }
  return out;
}
async function approve() {
  busy.value = true;
  try {
    const payload = editing.value || props.a.kind === 'form' ? collect() : undefined;
    await post(`/api/approvals/${props.a.id}/decide`, { decision: 'approve', payload });
    ElMessage.success(props.a.kind === 'form' ? $t('common.submitted') : props.a.kind === 'flow' ? $t('common.approved') : $t('approvalCard.approvedTheAgentContinues')); emit('done');
  } catch (e: any) { if (e?.message?.includes($t('approvalCard.format'))) ElMessage.error(e.message); } finally { busy.value = false; }
}
async function reject() {
  busy.value = true;
  try { await post(`/api/approvals/${props.a.id}/decide`, { decision: 'reject', reason: reason.value }); ElMessage.success($t('common.rejected')); emit('done'); } finally { busy.value = false; }
}
</script>

<style scoped>
.apc { border: 1px solid #ecd9a8; background: #fffdf7; border-radius: 10px; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; }
.apc.done { border-color: #e6eaee; background: #fff; }
.top { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; } .ttl { font-size: 14px; }
.sum { background: #fff; border: 1px solid #f0e6cc; border-radius: 8px; padding: 6px 10px; font-size: 13px; max-height: 260px; overflow: auto; }
.kv { font-size: 13px; display: grid; gap: 2px; } .kv > div { word-break: break-all; }
.edit .row { display: grid; grid-template-columns: 84px 1fr; gap: 8px; align-items: start; margin-bottom: 6px; }
.edit label { font-size: 12.5px; color: #6b7680; padding-top: 5px; }
.btns { display: flex; gap: 6px; flex-wrap: wrap; } .rej { margin-top: 2px; }
.compact .sum { max-height: 140px; }
.mdx { line-height: 1.6; } .mdx :deep(p) { margin: 2px 0; } .mdx :deep(ul) { margin: 2px 0; padding-left: 18px; }
</style>
