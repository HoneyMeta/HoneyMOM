<template>
  <div class="page kb">
    <HmPageHead :title="$t('common.knowledgeBase')" :sub="$t('knowledge.workInstructionsMachiningDrawingsOnl')">
      <el-button v-if="writable" type="primary" data-test="kb-new" @click="edit()"><el-icon><Plus /></el-icon>{{ $t('entityPage.new') }}</el-button>
    </HmPageHead>
    <HmToolbar>
      <el-radio-group v-model="kind" data-test="kb-kind" @change="load">
        <el-radio-button value="">{{ $t('common.all0', [total]) }}</el-radio-button>
        <el-radio-button v-for="(v, k) in KIND" :key="k" :value="k">{{ v }} {{ counts[k] || 0 }}</el-radio-button>
      </el-radio-group>
      <el-select v-model="proc" :placeholder="$t('knowledge.allOperations')" clearable style="width: 140px" @change="load"><el-option v-for="p in procs" :key="p.id" :label="`${p.code} ${p.name}`" :value="p.id" /></el-select>
      <el-input v-model="kw" :placeholder="$t('knowledge.titleTagContent')" clearable style="width: 200px" @change="load"><template #prefix><el-icon><Search /></el-icon></template></el-input>
    </HmToolbar>
    <div class="body mt">
      <div class="list" data-test="kb-list">
        <HmEmpty v-if="!items.length" icon="search" compact :title="$t('knowledge.nothingFound')" />
        <button v-for="d in items" :key="d.id" class="it" :class="{ on: cur?.id === d.id }" :data-test="'kb-item-' + d.id" @click="cur = d"
          v-ai="{ kind: 'record', entity: 'kb_doc', id: d.id, label: $t('common.n012', [KIND[d.kind] || $t('knowledge.knowledge'), d.title]), data: { [$t('common.title')]: d.title, [$t('common.type')]: KIND[d.kind], [$t('common.product')]: d.product__label, [$t('common.operation')]: d.process__label, [$t('common.versions')]: d.version, [$t('common.summary')]: d.excerpt } }">
          <el-icon class="ki" :class="d.kind"><component :is="d.kind === 'drawing' ? 'PictureFilled' : d.kind === 'standard' ? 'Stamp' : 'Document'" /></el-icon>
          <div class="tx"><b>{{ d.title }}</b>
            <small>{{ KIND[d.kind] }}<template v-if="d.process__label"> · {{ d.process__label }}</template><template v-if="d.version"> · {{ d.version }}</template>
              <template v-if="d.file_name"> · {{ (d.file_type || '').toUpperCase() }}</template></small></div>
        </button>
      </div>
      <div class="view">
        <HmEmpty v-if="!cur" :title="$t('knowledge.selectAnItemToView')" compact />
        <template v-else>
          <div class="vh">
            <h3>{{ cur.title }}</h3><HmTag>{{ KIND[cur.kind] }}</HmTag><HmTag v-if="cur.product__label" tone="primary">{{ cur.product__label.split(' ')[0] }}</HmTag>
            <HmTag v-if="cur.process__label" tone="amber">{{ cur.process__label }}</HmTag><span class="grow" />
            <el-button v-if="writable" size="small" @click="edit(cur)"><el-icon><Edit /></el-icon>{{ $t('entityPage.edit') }}</el-button>
            <el-popconfirm v-if="writable" :title="$t('entityPage.deleteThisRecord')" @confirm="remove(cur)"><template #reference><el-button size="small" type="danger" plain><el-icon><Delete /></el-icon></el-button></template></el-popconfirm>
          </div>
          <DrawingViewer v-if="cur.file_name" :doc="cur" tall />
          <div v-if="cur.content" class="md mt-s">{{ cur.content }}</div>
          <p class="muted small mt-s">{{ $t('knowledge.updated0', [cur.updated || cur.updated_at]) }}<template v-if="cur.tags"> {{ $t('knowledge.tags0', [cur.tags]) }}</template></p>
        </template>
      </div>
    </div>

    <el-dialog v-model="fo.open" :title="fo.id ? $t('common.edit') : $t('knowledge.newKnowledgeDrawing')" width="min(680px, 96vw)" destroy-on-close>
      <el-form label-width="90px" data-test="kb-form">
        <el-form-item :label="$t('common.title')" required><el-input v-model="fo.title" /></el-form-item>
        <el-form-item :label="$t('common.type')"><el-radio-group v-model="fo.kind"><el-radio-button v-for="(v, k) in KIND" :key="k" :value="k">{{ v }}</el-radio-button></el-radio-group></el-form-item>
        <div class="r2">
          <el-form-item :label="$t('common.product')"><el-select v-model="fo.product" clearable filterable><el-option v-for="m in prods" :key="m.id" :label="`${m.code} ${m.name}`" :value="m.id" /></el-select></el-form-item>
          <el-form-item :label="$t('common.operation')"><el-select v-model="fo.process" clearable filterable><el-option v-for="p in procs" :key="p.id" :label="`${p.code} ${p.name}`" :value="p.id" /></el-select></el-form-item>
        </div>
        <div class="r2">
          <el-form-item :label="$t('common.versions')"><el-input v-model="fo.version" placeholder="V1" /></el-form-item>
          <el-form-item :label="$t('knowledge.tags')"><el-input v-model="fo.tags" /></el-form-item>
        </div>
        <el-form-item :label="$t('knowledge.attachment')">
          <input ref="fileIn" type="file" accept=".pdf,.png,.jpg,.jpeg,.gif,.webp,.svg,.dxf" data-test="kb-file" @change="pick" />
          <div class="muted small">{{ $t('knowledge.pdfImageOrDxf20') }}<template v-if="fo.file_name">{{ $t('knowledge.current0', [fo.file_name]) }}</template></div>
        </el-form-item>
        <el-form-item :label="$t('common.content')"><el-input v-model="fo.content" type="textarea" :rows="5" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="fo.open = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :loading="fo.busy" data-test="kb-save" @click="save">{{ $t('common.save') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, reactive, ref } from 'vue';
import { useRoute } from 'vue-router';
import { canWrite, del, get, post, put } from '../api';
import DrawingViewer from '../components/biz/DrawingViewer.vue';

const route = useRoute();
const KIND: Record<string, string> = { sop: $t('common.workInstruction'), drawing: $t('knowledge.machiningDrawing'), standard: $t('knowledge.standard'), other: $t('common.other') };
const kind = ref((route.query.kind as string) || ''), proc = ref<any>(null), kw = ref('');
const items = ref<any[]>([]), counts = ref<Record<string, number>>({}), cur = ref<any>(null), procs = ref<any[]>([]), prods = ref<any[]>([]);
const total = computed(() => Object.values(counts.value).reduce((a, b) => a + b, 0));
const writable = computed(() => canWrite($t('aiDock.aiAssistant')));
const fo = reactive<any>({ open: false, busy: false, file: null });
const fileIn = ref<HTMLInputElement>();

async function load() {
  const qs = new URLSearchParams();
  if (kind.value) qs.set('kind', kind.value); if (proc.value) qs.set('process', proc.value); if (kw.value) qs.set('q', kw.value);
  const r = await get('/api/kb/list?' + qs);
  items.value = r.items; counts.value = r.counts;
  const want = Number(route.query.id) || cur.value?.id;
  cur.value = items.value.find((x) => x.id === want) || items.value[0] || null;
}
function edit(d?: any) {
  Object.assign(fo, { open: true, busy: false, file: null, id: d?.id, title: d?.title || '', kind: d?.kind || 'drawing', product: d?.product || null, process: d?.process || null,
    version: d?.version || '', tags: d?.tags || '', content: d?.content || '', file_name: d?.file_name || '' });
}
function pick(e: Event) { fo.file = (e.target as HTMLInputElement).files?.[0] || null; }
const b64 = (f: File) => new Promise<string>((ok, bad) => { const r = new FileReader(); r.onload = () => ok(String(r.result)); r.onerror = bad; r.readAsDataURL(f); });
async function save() {
  if (!fo.title.trim()) { ElMessage.warning($t('knowledge.pleaseEnterATitle')); return; }
  fo.busy = true;
  try {
    const body = { title: fo.title, kind: fo.kind, product: fo.product, process: fo.process, version: fo.version, tags: fo.tags, content: fo.content };
    let id = fo.id;
    if (id) await put('/api/e/kb_doc/' + id, body); else id = (await post('/api/e/kb_doc', body)).id;
    if (fo.file) await post(`/api/kb/${id}/file`, { name: fo.file.name, data: await b64(fo.file) });
    ElMessage.success($t('common.saved')); fo.open = false;
    await load(); cur.value = items.value.find((x) => x.id === id) || cur.value;
  } finally { fo.busy = false; }
}
async function remove(d: any) { await del('/api/e/kb_doc/' + d.id); ElMessage.success($t('common.deleted')); cur.value = null; await load(); }
onMounted(async () => {
  [procs.value, prods.value] = await Promise.all([get('/api/e/process?size=100&sort=code').then((r) => r.items), get('/api/e/material?size=200&sort=code').then((r) => r.items.filter((m: any) => m.mtype === 'fg' || m.mtype === 'semi'))]);
  await load();
});
</script>

<style scoped>
.body { display: grid; grid-template-columns: 340px minmax(0, 1fr); gap: var(--hm-gap); align-items: start; }
@media (max-width: 1000px) { .body { grid-template-columns: 1fr; } }
.list { display: flex; flex-direction: column; gap: 6px; max-height: calc(100vh - 230px); overflow: auto; }
.it { display: flex; gap: 10px; align-items: flex-start; text-align: left; padding: 10px 12px; border: 1px solid var(--hm-border); background: var(--hm-surface); border-radius: var(--hm-r-md);
  cursor: pointer; font: inherit; color: var(--hm-text); }
.it:hover { border-color: var(--hm-primary-soft-2); } .it.on { border-color: var(--hm-primary); background: var(--hm-primary-soft); }
.it .tx { display: flex; flex-direction: column; gap: 2px; min-width: 0; } .it small { color: var(--hm-text-3); font-size: var(--hm-fs-2xs); }
.ki { font-size: 20px; color: var(--hm-text-3); margin-top: 2px; } .ki.drawing { color: var(--hm-primary); } .ki.standard { color: var(--hm-amber); }
.view { background: var(--hm-surface); border: 1px solid var(--hm-border); border-radius: var(--hm-r-lg); padding: 14px; min-height: 300px; }
.vh { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; } .vh h3 { margin: 0; font-size: var(--hm-fs-lg); }
.md { white-space: pre-wrap; line-height: 1.75; font-size: var(--hm-fs-sm); color: var(--hm-text-2); }
.r2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; } .r2 :deep(.el-select) { width: 100%; }
.mt-s { margin-top: 10px; } .muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-2xs); }
</style>
