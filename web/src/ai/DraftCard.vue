<template>
  <div class="dc" :class="[d.status, { busy }]" data-test="draft-card" :data-draft-id="d.id">
    <div class="hd">
      <span class="badge">{{ $t('draftCard.actionDraft') }}</span>
      <b class="lb">{{ d.label }}</b>
      <span v-if="d.risk === 'high'" class="risk" :title="$t('draftCard.writeActionRunsOnlyAfter')">{{ $t('draftCard.approvalRequired') }}</span>
      <span class="grow" />
      <span class="st" :class="d.status">{{ STATUS[d.status] || d.status }}</span>
    </div>
    <div class="meta">
      <span>{{ d.source_label || 'AI' }}</span><span v-if="d.requested_by">{{ $t('draftCard.requestedBy0', [d.requested_by]) }}</span><span>· {{ (d.created_at || '').slice(5, 16) }}</span>
      <template v-if="d.target?.label"> · <a v-if="d.target.id" class="tg" @click="go(`/e/${d.target.entity}?id=${d.target.id}`)">{{ d.target.kind }} {{ d.target.label }}</a><span v-else>{{ d.target.label }}</span></template>
    </div>
    <div v-if="d.summary" class="sum">{{ d.summary }}</div>

    <table v-if="!editing" class="diff" data-test="draft-diff">
      <thead><tr><th>{{ d.mode === 'update' ? $t('common.field') : $t('draftCard.newContent') }}</th><th v-if="d.mode === 'update'">{{ $t('common.current') }}</th><th>{{ d.mode === 'update' ? $t('draftCard.changeTo') : '' }}</th></tr></thead>
      <tbody>
        <tr v-for="c in d.changes" :key="c.key">
          <td class="k">{{ c.label }}</td>
          <td v-if="d.mode === 'update'" class="b">{{ c.before ?? '—' }}</td>
          <td class="a"><span v-if="c.before === null || c.before === undefined" class="plus">+</span>{{ c.after }}</td>
        </tr>
        <tr v-if="!d.changes?.length"><td colspan="3" class="muted">{{ $t('draftCard.noChanges') }}</td></tr>
      </tbody>
    </table>

    <div v-else class="edit" data-test="draft-edit">
      <div v-for="f in editable" :key="f.key" class="row">
        <label :for="'f' + d.id + f.key">{{ f.label }}<i v-if="f.required">*</i></label>
        <el-switch v-if="f.type === 'bool'" :id="'f' + d.id + f.key" v-model="form[f.key]" />
        <el-input v-else-if="f.type === 'json' || f.type === 'text'" :id="'f' + d.id + f.key" v-model="form[f.key]" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" :class="{ mono: f.type === 'json' }" />
        <el-input v-else :id="'f' + d.id + f.key" v-model="form[f.key]" size="small" />
      </div>
    </div>

    <template v-if="d.status === 'pending'">
      <div v-if="rejecting" class="rej"><el-input v-model="reason" type="textarea" :rows="2" :placeholder="$t('draftCard.rejectionReasonOptionalRecordedIn')" /></div>
      <div v-if="d.can_decide" class="btns">
        <template v-if="!rejecting">
          <el-button type="primary" size="small" :loading="busy" data-test="draft-approve" @click="approve">{{ editing ? $t('draftCard.approveWithEdits') : $t('draftCard.approveRun') }}</el-button>
          <el-button v-if="!editing" size="small" data-test="draft-edit-btn" @click="startEdit">{{ $t('draftCard.edit') }}</el-button>
          <el-button v-else size="small" @click="editing = false">{{ $t('draftCard.discardEdits') }}</el-button>
          <el-button size="small" type="danger" plain data-test="draft-reject" @click="rejecting = true">{{ $t('common.reject') }}</el-button>
        </template>
        <template v-else>
          <el-button size="small" type="danger" :loading="busy" data-test="draft-reject-confirm" @click="reject">{{ $t('draftCard.confirmRejection') }}</el-button>
          <el-button size="small" @click="rejecting = false">{{ $t('common.cancel') }}</el-button>
        </template>
        <span class="note">{{ $t('draftCard.nothingRunsUntilApproved') }}</span>
      </div>
      <div v-else class="note">{{ $t('draftCard.requiresApprovalBy0Can', [d.role_label]) }}</div>
    </template>
    <div v-else class="res">
      <span>{{ d.decided_by }} {{ (d.decided_at || '').slice(5, 16) }} {{ d.status === 'approved' ? (d.edited ? $t('draftCard.approveWithEdits2') : $t('common.approve')) : $t('common.reject') }}</span>
      <span v-if="d.reason">{{ $t('common.n0', [d.reason]) }}</span>
      <a v-if="d.receipt" class="rc" data-test="draft-receipt" @click="go(d.receipt.link)"><el-icon><CircleCheck /></el-icon>{{ d.receipt.text }}</a>
    </div>
    <div v-if="err" class="err">{{ err }}</div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// Action draft card: a write operation proposed by AI, showing before/after; authorized users Approve / Edit & Approve / Reject. Nothing runs until approved.
//   <DraftCard :draft="d" />                       via="approvals" uses the Approval Center API (when AI Ask Data is off but the Approval Center is on)
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { get, post } from '../api';
import { toast } from './index';

const props = withDefaults(defineProps<{ draft: any; via?: 'drafts' | 'approvals' }>(), { via: 'drafts' });
const emit = defineEmits<{ done: [d: any] }>();
const router = useRouter();
const STATUS: Record<string, string> = { pending: $t('common.pendingApproval'), approved: $t('draftCard.executed'), rejected: $t('common.rejected') };
const d = ref<any>({ ...props.draft });
watch(() => props.draft, (v) => { d.value = { ...v }; });
const editing = ref(false), rejecting = ref(false), reason = ref(''), busy = ref(false), err = ref('');
const form = reactive<Record<string, any>>({});
const editable = computed(() => (d.value.fields || []).filter((f: any) => f.key in (d.value.payload || {}) || f.required));
const go = (p: string) => p && router.push(p);

onMounted(async () => {   // Draft in message history: fetch its latest status (may already have been handled in the Approval Center)
  if (props.via === 'drafts' && d.value.id && d.value.status === 'pending') {
    try { d.value = await get(`/api/ai/drafts/${d.value.id}`, true); } catch { /* no permission or deleted */ }
  }
});
function startEdit() {
  for (const f of editable.value) {
    const v = d.value.payload?.[f.key];
    form[f.key] = f.type === 'json' ? JSON.stringify(v ?? [], null, 1) : f.type === 'bool' ? !!v : v ?? '';
  }
  editing.value = true;
}
function collect() {
  const out: Record<string, any> = { ...d.value.payload };
  for (const f of editable.value) {
    const v = form[f.key];
    if (f.type === 'json') { try { out[f.key] = JSON.parse(v || '[]'); } catch { throw new Error($t('draftCard.n0IsNotValidJson', [f.label])); } }
    else if ((f.type === 'int' || f.type === 'float') && v !== '' && !isNaN(Number(v))) out[f.key] = Number(v);
    else out[f.key] = v;
  }
  return out;
}
async function decide(decision: 'approve' | 'reject', payload?: any) {
  busy.value = true; err.value = '';
  try {
    const url = props.via === 'approvals' ? `/api/approvals/${d.value.id}/decide` : `/api/ai/drafts/${d.value.id}/decide`;
    const r = await post(url, { decision, payload, reason: reason.value });
    d.value = r.draft || r;
    editing.value = false; rejecting.value = false;
    toast(decision === 'approve' ? (d.value.receipt?.text || $t('draftCard.approvedAndExecuted')) : $t('draftCard.rejectedNoActionWasTaken'), decision === 'approve' ? 'success' : 'info', 2600);
    emit('done', d.value);
    window.dispatchEvent(new Event('approvals-changed'));
  } catch (e: any) { err.value = e.message || String(e); } finally { busy.value = false; }
}
function approve() {
  try { decide('approve', editing.value ? collect() : undefined); } catch (e: any) { err.value = e.message; }
}
const reject = () => decide('reject');
</script>

<style scoped>
.dc { border: 1px solid var(--hm-border); border-left: 3px solid var(--hm-ai-2); border-radius: var(--hm-r-md); background: var(--hm-surface); padding: 9px 11px; display: flex; flex-direction: column; gap: 6px;
  box-shadow: var(--hm-e1); font-size: var(--hm-fs-sm); }
.dc.approved { border-left-color: var(--hm-run); } .dc.rejected { border-left-color: var(--hm-offline); opacity: .85; }
.hd { display: flex; align-items: center; gap: 6px; }
.badge { font-size: 10px; font-weight: 700; letter-spacing: .06em; color: #fff; background: var(--hm-ai-grad); border-radius: var(--hm-r-pill); padding: 0 7px; line-height: 17px; }
.lb { font-size: var(--hm-fs-md); } .grow { flex: 1; }
.risk { font-size: 10.5px; color: var(--hm-idle-ink); background: var(--hm-idle-soft); border-radius: var(--hm-r-pill); padding: 0 6px; line-height: 16px; }
.st { font-size: 11px; border-radius: var(--hm-r-pill); padding: 0 8px; line-height: 18px; background: var(--hm-idle-soft); color: var(--hm-idle-ink); }
.st.approved { background: var(--hm-run-soft); color: var(--hm-run-ink); } .st.rejected { background: var(--hm-offline-soft); color: var(--hm-offline-ink); }
.meta { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); display: flex; flex-wrap: wrap; gap: 3px; }
.tg { color: var(--hm-primary); cursor: pointer; }
.sum { font-size: var(--hm-fs-xs); color: var(--hm-text-2); background: var(--hm-ai-soft); border-radius: var(--hm-r-sm); padding: 5px 8px; line-height: 1.6; }
.diff { width: 100%; border-collapse: collapse; font-size: var(--hm-fs-xs); }
.diff th { text-align: left; font-weight: var(--hm-fw-medium); color: var(--hm-text-3); padding: 3px 6px; border-bottom: 1px solid var(--hm-border); background: var(--hm-surface-2); }
.diff td { padding: 4px 6px; border-bottom: 1px solid var(--hm-hairline); vertical-align: top; word-break: break-word; }
.diff .k { color: var(--hm-text-2); white-space: nowrap; width: 1%; }
.diff .b { color: var(--hm-fault-ink); text-decoration: line-through; text-decoration-color: rgba(161, 31, 31, .45); background: rgba(220, 43, 43, .05); }
.diff .a { color: var(--hm-run-ink); background: rgba(21, 163, 74, .06); font-weight: var(--hm-fw-medium); }
.plus { display: inline-block; width: 12px; color: var(--hm-run); font-weight: 700; }
.edit .row { display: grid; grid-template-columns: 92px 1fr; gap: 6px; align-items: start; margin-bottom: 5px; }
.edit label { font-size: var(--hm-fs-xs); color: var(--hm-text-2); padding-top: 4px; } .edit label i { color: var(--hm-fault); font-style: normal; }
.mono :deep(textarea) { font-family: var(--hm-font-mono); font-size: 11.5px; }
.btns { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; } .btns .el-button { margin: 0; }
.note { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); }
.res { font-size: var(--hm-fs-xs); color: var(--hm-text-2); display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.rc { display: inline-flex; align-items: center; gap: 4px; background: var(--hm-run-soft); color: var(--hm-run-ink); border-radius: var(--hm-r-pill); padding: 1px 9px; cursor: pointer; }
.err { color: var(--hm-fault-ink); background: var(--hm-fault-soft); border-radius: var(--hm-r-sm); padding: 4px 8px; font-size: var(--hm-fs-xs); }
</style>
