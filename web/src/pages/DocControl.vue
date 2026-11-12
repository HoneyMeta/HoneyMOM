<template>
  <div class="page dc">
    <HmPageHead :title="$t('common.controlledDocuments')" :sub="$t('docControl.workInstructionsDrawingsNcPrograms')">
      <el-button v-perm="'doc.ctrl_doc.create'" type="primary" data-test="doc-control" @click="openCtrl">{{ $t('docControl.bringUnderControl') }}</el-button>
    </HmPageHead>
    <el-tabs v-model="tab" class="tabs" data-test="doc-tabs">
      <el-tab-pane :label="$t('docControl.documentList')" name="list" /><el-tab-pane :label="$t('docControl.effectiveOnTheShopFloor')" name="eff" />
    </el-tabs>

    <template v-if="tab === 'list'">
      <div class="grid g4">
        <HmKpi :label="$t('common.controlledDocuments')" :value="docs.length" format="int" :unit="$t('docControl.k2519')" />
        <HmKpi :label="$t('common.effective')" :value="docs.filter((d) => d.status === 'effective').length" format="int" :unit="$t('docControl.k2519')" status="ok" />
        <HmKpi :label="$t('docControl.pendingVersion')" :value="docs.filter((d) => d.pending).length" format="int" :unit="$t('docControl.k2519')" :status="docs.some((d) => d.pending) ? 'warn' : ''" />
        <HmKpi :label="$t('common.void')" :value="docs.filter((d) => d.status === 'obsolete').length" format="int" :unit="$t('docControl.k2519')" />
      </div>
      <HmToolbar class="mt">
        <el-input v-model="f.q" :placeholder="$t('docControl.noName')" clearable style="width: 200px" data-test="doc-q" @keyup.enter="load" @clear="load" />
        <el-select v-model="f.dtype" :placeholder="$t('common.type')" clearable style="width: 140px" @change="load">
          <el-option v-for="(v, k) in DT" :key="k" :value="k" :label="v" />
        </el-select>
        <el-select v-model="f.status" :placeholder="$t('common.status')" clearable style="width: 120px" @change="load">
          <el-option v-for="(v, k) in DST" :key="k" :value="k" :label="v" />
        </el-select>
        <el-button @click="load">{{ $t('common.search') }}</el-button>
        <template #right><span class="muted small">{{ $t('docControl.clickARowForVersion') }}</span></template>
      </HmToolbar>
      <HmCard flush class="mt">
        <el-table :data="docs" size="small" data-test="doc-table" class="clickable" @row-click="(r: any) => openDoc(r.id)"
          v-ai-rows="{ entity: 'ctrl_doc', rows: () => docs, label: (r: any) => $t('docControl.controlledDocument012', [r.code, r.title, r.cur_rev || '']), data: (r: any) => ({ [$t('common.type')]: DT[r.dtype], [$t('docControl.currentVersion')]: r.cur_rev, [$t('docControl.pending')]: r.pending, [$t('common.status')]: DST[r.status] }) }">
          <el-table-column :label="$t('common.no2')" width="110"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
          <el-table-column prop="title" :label="$t('common.name')" min-width="200" />
          <el-table-column :label="$t('common.type')" width="104"><template #default="{ row }">{{ DT[row.dtype] || row.dtype }}</template></el-table-column>
          <el-table-column prop="product__label" :label="$t('common.product')" min-width="150" />
          <el-table-column prop="process__label" :label="$t('common.operation')" min-width="120" />
          <el-table-column :label="$t('docControl.currentVersion')" width="86"><template #default="{ row }"><span class="hm-mono">{{ row.cur_rev || '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('docControl.pendingVersion2')" width="120"><template #default="{ row }">
            <HmTag v-if="row.pending" :tone="RT[pendSt(row)]">{{ pendRev(row) }} {{ RST[pendSt(row)] }}</HmTag><span v-else class="muted">—</span>
          </template></el-table-column>
          <el-table-column :label="$t('common.status')" width="90"><template #default="{ row }"><HmTag :tone="row.status === 'effective' ? 'ok' : 'neutral'">{{ DST[row.status] }}</HmTag></template></el-table-column>
          <template #empty><HmEmpty icon="search" :title="$t('docControl.noControlledDocuments')" :desc="$t('docControl.bringUnderControlFromKnowledge')" /></template>
        </el-table>
      </HmCard>
    </template>

    <!-- Effective version on the shop floor -->
    <template v-else>
      <HmToolbar>
        <span class="lbl">{{ $t('common.product') }}</span><div class="rs"><RefSelect v-model="ef.product" entity="material" /></div>
        <span class="lbl">{{ $t('common.operation') }}</span><div class="rs"><RefSelect v-model="ef.process" entity="process" /></div>
        <el-button type="primary" data-test="doc-eff-query" @click="loadEff">{{ $t('common.view') }}</el-button>
        <template #right><span class="muted small">{{ $t('docControl.workstationTerminalsMobileAndThe') }}</span></template>
      </HmToolbar>
      <div class="mt" data-test="doc-effective">
        <HmEmpty v-if="!effs.length" plain :title="$t('docControl.noEffectiveControlledDocuments')" />
        <div v-else class="grid g-auto eff" style="--min: 340px">
          <HmCard v-for="d in effs" :key="d.doc">
            <template #title><div class="et"><span class="hm-mono">{{ d.code }}</span><h3>{{ d.title }}</h3></div></template>
            <template #actions><HmTag tone="ok">{{ $t('common.version0', [d.rev]) }}</HmTag></template>
            <div class="meta"><HmTag>{{ DT[d.dtype] || d.dtype }}</HmTag><span>{{ $t('docControl.approved0', [d.approved_by || '—']) }}</span><span class="hm-num">{{ $t('docControl.effective0', [fmtTime(d.effective_at)]) }}</span></div>
            <pre v-if="d.excerpt" class="pv sm">{{ d.excerpt }}</pre>
            <div class="links">
              <el-button v-if="d.file_type" size="small" link type="primary" @click="openFile(d.target_id)">{{ $t('docControl.openFile0', [d.file_name || '']) }}</el-button>
              <el-button size="small" link type="primary" @click="openDoc(d.doc)">{{ $t('docControl.versionHistory') }}</el-button>
            </div>
          </HmCard>
        </div>
      </div>
    </template>

    <!-- Version history -->
    <el-drawer v-model="dr.open" size="min(760px, 100%)" :title="dr.d ? `${dr.d.item.code} ${dr.d.item.title}` : ''" destroy-on-close>
      <div v-if="dr.d" data-test="doc-drawer">
        <div class="hd">
          <div class="kv"><span>{{ $t('common.type') }}</span><b>{{ DT[dr.d.item.dtype] }}</b></div>
          <div class="kv"><span>{{ $t('common.product') }}</span><b>{{ dr.d.item.product__label || '—' }}</b></div>
          <div class="kv"><span>{{ $t('common.operation') }}</span><b>{{ dr.d.item.process__label || '—' }}</b></div>
          <div class="kv"><span>{{ $t('docControl.currentVersion') }}</span><b class="hm-mono">{{ dr.d.item.cur_rev || '—' }}</b></div>
          <div class="kv"><span>{{ $t('docControl.owner') }}</span><b>{{ dr.d.item.owner || '—' }}</b></div>
          <div class="kv"><span>{{ $t('common.status') }}</span><HmTag :tone="dr.d.item.status === 'effective' ? 'ok' : 'neutral'">{{ DST[dr.d.item.status] }}</HmTag></div>
          <span class="grow" />
          <el-button v-perm="'doc.ctrl_doc.revise'" type="primary" plain data-test="doc-act-revise" @click="revise">{{ $t('docControl.revise') }}</el-button>
        </div>
        <el-timeline class="revs" data-test="doc-revs">
          <el-timeline-item v-for="r in dr.d.revisions" :key="r.id" :type="r.status === 'effective' ? 'success' : r.status === 'obsolete' ? 'info' : 'primary'" :hollow="r.status === 'obsolete'">
            <div class="rv" :data-rev="r.rev">
              <div class="rh">
                <b class="hm-mono rn">{{ r.rev }}</b><HmTag :tone="RT[r.status]">{{ RST[r.status] }}</HmTag>
                <el-button v-if="r.ecn" size="small" link type="primary" @click="toEcn(r.ecn)">{{ r.ecn__label || 'ECN' }}</el-button>
                <span class="grow" />
                <el-button v-for="a in ACTS[r.status] || []" :key="a" v-perm="'doc.ctrl_doc_rev.' + a" size="small" :type="a === 'approve' || a === 'effective' ? 'primary' : a === 'reject' || a === 'obsolete' ? 'danger' : 'primary'"
                  :plain="a !== 'approve' && a !== 'effective'" :data-test="'doc-act-' + a" @click="act(r, a)">{{ AL[a] }}</el-button>
              </div>
              <div class="pp">
                <span>{{ $t('docControl.preparedBy0', [r.author || '—']) }}</span><span>{{ $t('docControl.reviewedBy0', [r.reviewed_by || '—']) }}</span><span>{{ $t('docControl.approved0', [r.approved_by || '—']) }}</span>
                <span v-if="r.effective_date" class="hm-num">{{ $t('docControl.plannedEffective0', [r.effective_date]) }}</span>
                <span v-if="r.effective_at" class="hm-num">{{ $t('docControl.effective0', [fmtTime(r.effective_at)]) }}</span>
                <span v-if="r.obsolete_at" class="hm-num">{{ $t('docControl.obsoleted0', [fmtTime(r.obsolete_at)]) }}</span>
              </div>
              <div v-if="r.change_note" class="note">{{ r.change_note }}</div>
              <div v-if="r.signatures?.length" class="sigs">
                <span v-for="s in r.signatures" :key="s.id" class="sig"><el-icon><EditPen /></el-icon>{{ s.name || s.user }} · {{ s.meaning }} · <span class="hm-num">{{ fmtTime(s.signed_at) }}</span></span>
              </div>
              <div class="tg">
                <span class="muted small">{{ $t('docControl.content01', [ENT[r.target?.entity] || r.target?.entity, r.target?.label || '']) }}</span>
                <el-button size="small" link type="primary" @click="toggle(r.id)">{{ shown.has(r.id) ? $t('aiBlocks.collapse') : $t('common.preview') }}</el-button>
                <el-button v-if="r.target?.file_type" size="small" link type="primary" @click="openFile(r.target.id)">{{ $t('docControl.openFile') }}</el-button>
                <el-button v-if="r.target?.entity === 'control_plan'" size="small" link type="primary" @click="openCp(r.target.id)">{{ $t('docControl.printVersion') }}</el-button>
                <el-button v-if="r.status === 'draft'" size="small" link type="primary" @click="router.push(`/e/${r.target.entity}?id=${r.target.id}`)">{{ $t('docControl.editContent') }}</el-button>
              </div>
              <template v-if="shown.has(r.id)">
                <pre v-if="r.target?.content" class="pv">{{ r.target.content }}</pre>
                <div v-else class="muted small">{{ r.target?.file_type ? $t('docControl.attachedFileClickOpenFile') : $t('docControl.structuredContent') }}
                  <el-button v-if="!r.target?.file_type" size="small" link type="primary" @click="router.push(`/e/${r.target.entity}?id=${r.target.id}`)">{{ $t('aiIndex.openRecord') }}</el-button></div>
              </template>
            </div>
          </el-timeline-item>
        </el-timeline>
      </div>
    </el-drawer>

    <!-- Bring under control -->
    <el-dialog v-model="cd.open" :title="$t('docControl.bringDocumentUnderControl')" width="min(560px, 96vw)" destroy-on-close>
      <el-form label-width="84px" data-test="doc-control-form">
        <el-form-item :label="$t('docControl.contentType')">
          <el-radio-group v-model="cd.entity" @change="loadCands">
            <el-radio-button v-for="(v, k) in ENT" :key="k" :value="k">{{ v }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="$t('common.record')">
          <el-select v-model="cd.id" filterable :placeholder="$t('docControl.selectTheRecordToControl')" style="width: 100%" :loading="cd.loading" data-test="doc-control-pick">
            <el-option v-for="o in cd.opts" :key="o.id" :value="o.id" :label="o.label" />
          </el-select>
        </el-form-item>
        <p class="muted small">{{ $t('docControl.createsADraftVersionVersion') }}</p>
      </el-form>
      <template #footer>
        <el-button @click="cd.open = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!cd.id" :loading="cd.busy" data-test="doc-control-ok" @click="doControl">{{ $t('docControl.bringUnderControl') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { get, post, tok } from '../api';
import RefSelect from '../components/RefSelect.vue';
import { fmtTime } from '../design';

const router = useRouter(), route = useRoute();
const ask = <T,>(p: Promise<T>) => p.then((v) => v, () => null);
const DT: Record<string, string> = { wi: $t('common.workInstruction'), drawing: $t('common.drawing'), control_plan: $t('common.controlPlan'), pfmea: 'PFMEA', nc: $t('common.ncPrograms'), other: $t('common.other') };
const DST: Record<string, string> = { draft: $t('docControl.drafting'), effective: $t('common.effective'), obsolete: $t('common.void') };
const RST: Record<string, string> = { draft: $t('common.draft'), review: $t('common.inReview'), approved: $t('common.approved2'), effective: $t('common.effective'), obsolete: $t('common.void') };
const RT: Record<string, any> = { draft: 'neutral', review: 'warn', approved: 'primary', effective: 'ok', obsolete: 'neutral' };
const ENT: Record<string, string> = { kb_doc: $t('docControl.knowledgeBaseDocument'), nc_program: $t('common.ncPrograms'), control_plan: $t('common.controlPlan'), pfmea: 'PFMEA' };
const AL: Record<string, string> = { submit: $t('docControl.submitForReview'), review: $t('docControl.passReview'), approve: $t('docControl.approveESignature'), effective: $t('docControl.makeEffectiveNow'), reject: $t('common.return'), obsolete: $t('common.void') };
const ACTS: Record<string, string[]> = { draft: ['submit', 'obsolete'], review: ['review', 'approve', 'reject'], approved: ['effective', 'reject'], effective: ['obsolete'] };

const tab = ref('list');
const docs = ref<any[]>([]), effs = ref<any[]>([]);
const f = reactive({ q: '', dtype: '', status: '' });
const ef = reactive<any>({ product: null, process: null });
const dr = reactive<any>({ open: false, d: null });
const shown = ref(new Set<number>());
const cd = reactive<any>({ open: false, entity: 'kb_doc', id: null, opts: [], loading: false, busy: false });

const pendRev = (r: any) => String(r.pending).split(' ')[0];
const pendSt = (r: any) => String(r.pending).split(' ')[1];

async function load() {
  const qs = new URLSearchParams({ q: f.q || '', dtype: f.dtype || '', status: f.status || '' });
  docs.value = await get('/api/doc/list?' + qs);
}
async function loadEff() {
  effs.value = await get(`/api/doc/effective?product=${ef.product || 0}&process=${ef.process || 0}`);
}
async function openDoc(id: number) {
  const d = await get(`/api/doc/${id}/history`);
  const first = d.revisions.find((r: any) => r.status !== 'obsolete') || d.revisions[0];
  shown.value = new Set(first ? [first.id] : []);
  dr.d = d; dr.open = true;
}
function toggle(id: number) { const s = new Set(shown.value); s.has(id) ? s.delete(id) : s.add(id); shown.value = s; }
async function refresh() { if (dr.d) { const keep = shown.value; dr.d = await get(`/api/doc/${dr.d.item.id}/history`); shown.value = keep; } await load(); }

async function act(r: any, a: string) {
  let body: any = {};
  if (a === 'reject') {
    const p: any = await ask(ElMessageBox.prompt($t('docControl.reasonForReturn'), $t('docControl.returnVersion0', [r.rev]), { inputValidator: (v: string) => !!v?.trim() || $t('common.pleaseEnterAReason') }));
    if (!p) return;
    body = { reason: p.value };
  } else if (a === 'obsolete') {
    if (!(await ask(ElMessageBox.confirm($t('docControl.afterObsoletingVersion0It', [r.rev]), $t('docControl.obsoleteVersion'), { type: 'warning' })))) return;
  }
  const res = await post(`/api/e/ctrl_doc_rev/${r.id}/do/${a}`, body);
  ElMessage.success(res.msg || $t('common.completed')); await refresh();
}
async function revise() {
  const res = await post(`/api/e/ctrl_doc/${dr.d.item.id}/do/revise`, {});
  ElMessage.success(res.msg); await refresh();
}
const toEcn = (id: number) => { dr.open = false; router.push('/exec/ecn?id=' + id); };
const openFile = (id: number) => window.open(`/api/kb/${id}/file?token=${tok()}`, '_blank');
const openCp = (id: number) => window.open(`/api/iatf/cp/${id}/print?token=${tok()}`, '_blank');

function openCtrl() { cd.open = true; cd.id = null; loadCands(); }
async function loadCands() {
  cd.loading = true; cd.id = null;
  try {
    const items = (await get(`/api/e/${cd.entity}?size=200&sort=-id`)).items;
    cd.opts = items.filter((x: any) => cd.entity === 'pfmea' || !x.ctrl_doc).map((x: any) => ({
      id: x.id, label: cd.entity === 'kb_doc' ? `${x.title}${x.version ? ' · ' + x.version : ''}` : [x.code, x.title || x.name, x.rev || x.version].filter(Boolean).join(' · '),
    }));
  } finally { cd.loading = false; }
}
async function doControl() {
  cd.busy = true;
  try {
    const r = await post('/api/doc/control', { entity: cd.entity, id: cd.id });
    ElMessage.success($t('docControl.underControl0Version1', [r.doc_code, r.rev])); cd.open = false;
    await load(); openDoc(r.doc);
  } finally { cd.busy = false; }
}

watch(tab, (t) => { if (t === 'eff') loadEff(); else load(); });
onMounted(async () => { await load(); if (route.query.id) openDoc(Number(route.query.id)); });
</script>

<style scoped>
.tabs { margin-bottom: 4px; }
.muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-xs); }
.clickable :deep(tbody tr) { cursor: pointer; }
.lbl { color: var(--hm-text-2); font-size: var(--hm-fs-xs); }
.rs { width: calc(220px * var(--hm-scale)); }
.et { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; min-width: 0; }
.et h3 { margin: 0; font-size: var(--hm-fs-md); font-weight: 600; color: var(--hm-text); }
.meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; color: var(--hm-text-2); font-size: var(--hm-fs-xs); }
.links { margin-top: 6px; display: flex; gap: 8px; flex-wrap: wrap; }
.pv { margin: 6px 0 0; padding: 8px 10px; background: var(--hm-surface-2); border: 1px solid var(--hm-border); border-radius: var(--hm-r-sm); white-space: pre-wrap; word-break: break-word;
  font-family: var(--hm-font-mono); font-size: var(--hm-fs-xs); color: var(--hm-text); max-height: 280px; overflow: auto; line-height: 1.55; }
.pv.sm { max-height: 140px; }
.hd { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; padding: 8px 10px; background: var(--hm-surface-2); border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); margin-bottom: 14px; }
.kv { display: flex; flex-direction: column; gap: 2px; font-size: var(--hm-fs-xs); }
.kv span { color: var(--hm-text-2); } .kv b { font-weight: 600; color: var(--hm-text); font-size: var(--hm-fs-sm); }
.grow { flex: 1; }
.revs { padding-left: 2px; }
.rv { display: flex; flex-direction: column; gap: 4px; }
.rh { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.rn { font-size: var(--hm-fs-lg); }
.pp { display: flex; gap: 14px; flex-wrap: wrap; color: var(--hm-text-2); font-size: var(--hm-fs-xs); }
.note { white-space: pre-wrap; font-size: var(--hm-fs-xs); color: var(--hm-text); }
.sigs { display: flex; gap: 10px; flex-wrap: wrap; }
.sig { display: inline-flex; align-items: center; gap: 4px; font-size: var(--hm-fs-xs); color: var(--hm-primary); background: var(--hm-primary-soft); padding: 2px 8px; border-radius: var(--hm-r-pill); }
.tg { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
</style>
