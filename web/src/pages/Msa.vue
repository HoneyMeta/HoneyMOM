<template>
  <div class="page msa">
    <HmPageHead :title="$t('common.msaMeasurementSystemAnalysis')" :sub="$t('msa.grRAverageRangeAnova')">
      <el-button v-perm:disable="'iatf.msa_study.create'" type="primary" data-test="msa-new" @click="openNew"><el-icon><Plus /></el-icon>{{ $t('msa.newStudy') }}</el-button>
    </HmPageHead>

    <div class="lay">
      <HmCard flush class="side" data-test="msa-list">
        <button v-for="s in list" :key="s.id" type="button" class="it" :class="{ on: cur?.id === s.id }" :data-test="'msa-item-' + s.id" @click="pick(s.id)">
          <span class="r1"><span class="hm-mono c">{{ s.code }}</span><HmTag v-if="s.verdict" :tone="VT[s.verdict]?.[1] || 'neutral'">{{ VT[s.verdict]?.[0] || s.verdict }}</HmTag><HmTag v-else tone="neutral">{{ $t('msa.notCalculated') }}</HmTag></span>
          <span class="n">{{ s.title }}</span>
          <span class="r3 small">
            <span>{{ MT[s.method] || s.method }}</span>
            <span v-if="s.gauge__label">{{ s.gauge__label }}</span>
            <span v-if="s.grr_pct != null">%GRR <b class="hm-num">{{ fmtNum(s.grr_pct, 1) }}%</b></span>
            <span v-if="s.ndc != null">ndc <b class="hm-num">{{ s.ndc }}</b></span>
          </span>
        </button>
        <HmEmpty v-if="loaded && !list.length" plain :title="$t('gauges.noMsaStudiesYet')" />
      </HmCard>

      <div v-if="cur" class="main">
        <HmCard>
          <div class="hd">
            <div class="ttl"><span class="hm-mono">{{ cur.code }}</span> {{ cur.title }}</div>
            <HmTag tone="primary">{{ MT[cur.method] || cur.method }}</HmTag>
            <span class="grow" />
            <el-button v-perm:disable="'iatf.msa_study.update'" data-test="msa-import" @click="imp.open = true; imp.csv = ''"><el-icon><Upload /></el-icon>{{ $t('msa.importCsv') }}</el-button>
            <el-button v-perm:disable="'iatf.msa_study.calc'" type="primary" :loading="busy === 'calc'" data-test="msa-calc" @click="calc"><el-icon><DataAnalysis /></el-icon>{{ $t('msa.calculate') }}</el-button>
            <el-button :disabled="!cur.result" data-test="msa-report" @click="report"><el-icon><Printer /></el-icon>{{ $t('msa.report') }}</el-button>
          </div>
          <div class="meta">
            <span>{{ $t('common.gauge') }} <a v-if="cur.gauge" class="lnk" @click="router.push('/iatf/gauges?id=' + cur.gauge)">{{ cur.gauge__label || '#' + cur.gauge }}</a><b v-else>—</b></span>
            <span>{{ $t('msa.characteristic') }} <b class="hm-mono">{{ cur.char || '—' }}</b></span>
            <span class="fld">{{ $t('msaResult.tolerance3') }} <el-input-number v-model="cur.tolerance" size="small" :controls="false" :min="0" placeholder="—" style="width: 96px" @change="saveField('tolerance')" /></span>
            <span v-if="cur.method === 'bias'" class="fld">{{ $t('msaResult.referenceValue') }} <el-input-number v-model="cur.reference" size="small" :controls="false" :placeholder="$t('common.required2')" style="width: 96px" data-test="msa-ref" @change="saveField('reference')" /></span>
            <span>{{ $t('common.date') }} <b class="hm-num">{{ cur.study_date || '—' }}</b></span><span>{{ $t('msa.analyst') }} <b>{{ cur.by || '—' }}</b></span>
          </div>
        </HmCard>

        <!-- Data entry -->
        <HmCard :title="EDT[cur.method] || $t('layout.data')" :sub="editorSub" class="mt">
          <template #actions>
            <template v-if="isMatrix">
              <span class="dims small t2">{{ $t('msaResult.appraiser') }} <el-input-number v-model="dim.a" size="small" :min="1" :max="10" style="width: 84px" /> {{ $t('msa.trials') }} <el-input-number v-model="dim.t" size="small" :min="1" :max="10" style="width: 84px" /> {{ $t('msa.parts') }} <el-input-number v-model="dim.p" size="small" :min="2" :max="50" style="width: 84px" /></span>
              <el-button size="small" @click="resize">{{ $t('erpMrp.adjust') }}</el-button>
            </template>
            <el-button v-else-if="cur.method === 'linearity'" size="small" @click="ed.parts.push({ reference: '', readings: Array(ed.parts[0]?.readings.length || 12).fill('') })">{{ $t('msa.addReferencePart') }}</el-button>
            <el-button v-else-if="cur.method === 'bias'" size="small" @click="ed.readings.push('')">{{ $t('msa.addReading') }}</el-button>
          </template>

          <div class="gw" data-test="msa-grid">
            <!-- GR&R / attribute -->
            <table v-if="isMatrix" class="grid-t">
              <thead><tr><th class="l">{{ $t('msaResult.appraiser') }}</th><th class="l">{{ $t('common.times') }}</th><th v-for="p in nParts" :key="p">{{ p }}</th></tr></thead>
              <tbody>
                <template v-for="(a, ai) in ed.values" :key="'a' + ai">
                  <tr v-for="(t, ti) in a" :key="ai + '-' + ti" :class="{ first: ti === 0 }">
                    <td class="l"><input v-if="ti === 0" v-model="ed.appraisers[ai]" class="nm" :aria-label="$t('msa.appraiser') + (ai + 1)" /></td>
                    <td class="l hm-num t2">{{ ti + 1 }}</td>
                    <td v-for="(_, pi) in t" :key="pi"><input v-model="t[pi]" class="cell hm-num" :class="{ attr: cur.method === 'attribute' }" :aria-label="$t('msa.n0Trial1Part2', [ed.appraisers[ai], ti + 1, pi + 1])" @paste="(e: ClipboardEvent) => paste(e, ai, ti, pi)" /></td>
                  </tr>
                </template>
                <tr v-if="cur.method === 'attribute'" class="ref">
                  <td class="l">{{ $t('common.standard') }}</td><td class="l" />
                  <td v-for="(_, pi) in ed.reference" :key="pi"><input v-model="ed.reference[pi]" class="cell hm-num attr" :aria-label="$t('msa.standardPart') + (pi + 1)" /></td>
                </tr>
              </tbody>
            </table>

            <!-- Bias -->
            <div v-else-if="cur.method === 'bias'" class="rd">
              <label v-for="(_, i) in ed.readings" :key="i" class="rc"><span class="t2 small hm-num">{{ i + 1 }}</span><input v-model="ed.readings[i]" class="cell hm-num" :aria-label="$t('msa.reading') + (i + 1)" /></label>
            </div>

            <!-- Linearity -->
            <table v-else-if="cur.method === 'linearity'" class="grid-t">
              <thead><tr><th class="l">{{ $t('msaResult.referenceValue') }}</th><th v-for="j in (ed.parts[0]?.readings.length || 0)" :key="j">{{ j }}</th></tr></thead>
              <tbody>
                <tr v-for="(p, i) in ed.parts" :key="i">
                  <td class="l"><input v-model="p.reference" class="cell hm-num refc" :aria-label="$t('msa.reference') + (i + 1)" /></td>
                  <td v-for="(_, j) in p.readings" :key="j"><input v-model="p.readings[j]" class="cell hm-num" :aria-label="$t('msa.referencePart0Reading1', [i + 1, j + 1])" /></td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="small t2 hint">{{ $t('msa.n0YouCanCopyA', [HINT[cur.method]]) }}</p>
        </HmCard>

        <HmCard :title="$t('common.result')" class="mt">
          <template #actions><span v-if="cur.conclusion" class="small t2">{{ cur.conclusion }}</span></template>
          <MsaResult v-if="cur.result" :method="cur.method" :result="cur.result" :verdict="cur.verdict" :data="cur.data" />
          <HmEmpty v-else plain :title="$t('msa.notCalculatedYetEnterOr')" />
        </HmCard>
      </div>
      <HmEmpty v-else-if="loaded" class="main" :title="$t('msa.selectAnMsaStudy')" :desc="$t('msa.orCreateANewOne')" />
    </div>

    <!-- New -->
    <el-dialog v-model="nf.open" :title="$t('msa.newMsaStudy')" width="min(620px, 96vw)" append-to-body destroy-on-close>
      <el-form label-width="96px" data-test="msa-new-form">
        <el-form-item :label="$t('common.name')" required><el-input v-model="nf.title" :placeholder="$t('msa.eGCylinderBoreGauge')" /></el-form-item>
        <el-form-item :label="$t('common.method2')" required>
          <el-select v-model="nf.method" style="width: 100%" data-test="msa-new-method"><el-option v-for="(t, k) in MT" :key="k" :value="k" :label="t" /></el-select>
        </el-form-item>
        <el-form-item :label="$t('common.gauge')"><RefSelect v-model="nf.gauge" entity="gauge" /></el-form-item>
        <el-form-item :label="$t('msa.characteristic')"><el-input v-model="nf.char" :placeholder="$t('msa.eGBoreDia')" /></el-form-item>
        <el-form-item :label="$t('msaResult.tolerance3')"><el-input-number v-model="nf.tolerance" :controls="false" :min="0" :placeholder="$t('msa.uslLsl')" style="width: 160px" /></el-form-item>
        <el-form-item v-if="nf.method === 'bias'" :label="$t('msaResult.referenceValue')" required><el-input-number v-model="nf.reference" :controls="false" style="width: 160px" /></el-form-item>
        <el-form-item v-if="['grr_xbar', 'grr_anova', 'attribute'].includes(nf.method)" :label="$t('msa.size')">
          <div class="dims">{{ $t('common.part') }} <el-input-number v-model="nf.parts" :min="2" :max="50" style="width: 96px" /> {{ $t('msaResult.appraiser') }} <el-input-number v-model="nf.appraisers" :min="1" :max="10" style="width: 96px" /> {{ $t('common.count') }} <el-input-number v-model="nf.trials" :min="1" :max="10" style="width: 96px" /></div>
        </el-form-item>
        <el-form-item v-if="nf.method === 'bias'" :label="$t('msa.numberOfReadings')"><el-input-number v-model="nf.parts" :min="2" :max="100" style="width: 96px" />&nbsp;<span class="small t2">{{ $t('msa.n10Recommended') }}</span></el-form-item>
        <el-form-item v-if="nf.method === 'linearity'" :label="$t('msa.size')"><div class="dims">{{ $t('msa.referenceParts') }} <el-input-number v-model="nf.parts" :min="2" :max="20" style="width: 96px" /> {{ $t('msa.readingsPerPart') }} <el-input-number v-model="nf.trials" :min="1" :max="30" style="width: 96px" /></div></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="nf.open = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!nf.title || (nf.method === 'bias' && nf.reference == null)" :loading="busy === 'new'" data-test="msa-new-save" @click="saveNew">{{ $t('common.create') }}</el-button>
      </template>
    </el-dialog>

    <!-- Import -->
    <el-dialog v-model="imp.open" :title="$t('msa.importCsvLongFormat')" width="min(620px, 96vw)" append-to-body destroy-on-close>
      <p class="small t2">{{ $t('msa.n0AHeaderRowOn', [CSV_FMT[cur?.method || 'grr_anova']]) }}</p>
      <el-input v-model="imp.csv" type="textarea" :rows="10" class="hm-mono" :placeholder="CSV_EX[cur?.method || 'grr_anova']" data-test="msa-import-text" />
      <div class="fl"><input type="file" accept=".csv,.txt" class="file" @change="pickCsv" /></div>
      <template #footer>
        <el-button @click="imp.open = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!imp.csv.trim()" :loading="busy === 'imp'" data-test="msa-import-go" @click="doImport">{{ $t('common.import') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { get, post, put, tok } from '../api';
import RefSelect from '../components/RefSelect.vue';
import MsaResult from '../components/exec/MsaResult.vue';
import { fmtNum } from '../design';

const router = useRouter(), route = useRoute();
const MT: Record<string, string> = { grr_xbar: $t('msa.grRAverageRange'), grr_anova: $t('msa.grRAnova'), bias: $t('msaResult.bias'), linearity: $t('msa.linearity'), attribute: $t('msa.attributeAgreement') };
const VT: Record<string, [string, any]> = { acceptable: [$t('common.acceptable'), 'ok'], conditional: [$t('common.conditionallyAcceptable'), 'warn'], unacceptable: [$t('common.unacceptable'), 'bad'] };
const EDT: Record<string, string> = { grr_xbar: $t('msa.measurementDataAppraisersTrialsParts'), grr_anova: $t('msa.measurementDataAppraisersTrialsParts'), bias: $t('msa.referencePartReadings'), linearity: $t('msa.readingsPerReferencePart'), attribute: $t('msa.decisionsAppraisersTrialsParts') };
const HINT: Record<string, string> = {
  grr_xbar: $t('msa.eachAppraiserMeasuresEachPart'), grr_anova: $t('msa.eachAppraiserMeasuresEachPart'),
  bias: $t('msa.theSameReferencePartMeasured'), linearity: $t('msa.n5ReferencePartsCoveringThe'),
  attribute: $t('msa.enterDecisionsAs10'),
};
const CSV_FMT: Record<string, string> = {
  grr_xbar: $t('msa.eachRowAppraiserTrialPart'), grr_anova: $t('msa.eachRowAppraiserTrialPart'), attribute: $t('msa.eachRowAppraiserTrialPart2'),
  bias: $t('msa.oneReadingPerRowOr'), linearity: $t('msa.eachRowReferenceReading'),
};
const CSV_EX: Record<string, string> = {
  grr_xbar: 'appraiser,trial,part,value\nA,1,1,0.29\nA,1,2,-0.56', grr_anova: 'appraiser,trial,part,value\nA,1,1,0.29\nA,1,2,-0.56',
  attribute: $t('msa.appraiserTrialPartResultReference'), bias: 'value\n5.8\n5.7\n5.9', linearity: 'reference,value\n2,2.7\n2,2.5\n4,5.1',
};

const list = ref<any[]>([]), loaded = ref(false), cur = ref<any>(null), busy = ref('');
const isMatrix = computed(() => ['grr_xbar', 'grr_anova', 'attribute'].includes(cur.value?.method));

// ---------- Editor ----------
const ed = reactive<any>({ appraisers: [] as string[], values: [] as string[][][], reference: [] as string[], readings: [] as string[], parts: [] as any[] });
const dim = reactive({ a: 3, t: 3, p: 10 });
const nParts = computed(() => ed.values[0]?.[0]?.length || 0);
const s = (v: any) => (v === null || v === undefined ? '' : String(v));
const editorSub = computed(() => {
  if (!cur.value) return '';
  if (isMatrix.value) return $t('msa.n0Appraisers1Trials2', [ed.appraisers.length, ed.values[0]?.length || 0, nParts.value]);
  if (cur.value.method === 'bias') return $t('msa.n0Readings', [ed.readings.filter((x: string) => x.trim()).length]);
  return $t('msa.n0ReferenceParts', [ed.parts.length]);
});
function fillEditor(st: any) {
  const d = st.data || {};
  if (['grr_xbar', 'grr_anova', 'attribute'].includes(st.method)) {
    const A = d.values?.length || st.appraisers || 3, T = d.values?.[0]?.length || st.trials || 3, P = d.values?.[0]?.[0]?.length || st.parts || 10;
    ed.appraisers = Array.from({ length: A }, (_, i) => d.appraisers?.[i] ?? String.fromCharCode(65 + i));
    ed.values = Array.from({ length: A }, (_, a) => Array.from({ length: T }, (_, t) => Array.from({ length: P }, (_, p) => s(d.values?.[a]?.[t]?.[p]))));
    ed.reference = Array.from({ length: P }, (_, p) => s(d.reference?.[p]));
    Object.assign(dim, { a: A, t: T, p: P });
  } else if (st.method === 'bias') {
    const rd = d.readings || [];
    ed.readings = Array.from({ length: Math.max(rd.length, st.parts && st.parts > 2 ? st.parts : 15) }, (_, i) => s(rd[i]));
  } else if (st.method === 'linearity') {
    const ps = d.parts || [];
    const m = Math.max(...ps.map((p: any) => p.readings?.length || 0), st.trials && st.trials > 3 ? st.trials : 12);
    ed.parts = (ps.length ? ps : Array.from({ length: Math.max(st.parts || 5, 2) }, () => ({ reference: '', readings: [] })))
      .map((p: any) => ({ reference: s(p.reference), readings: Array.from({ length: m }, (_, j) => s(p.readings?.[j])) }));
  }
}
function resize() {
  const { a: A, t: T, p: P } = dim;
  const old = ed.values;
  ed.appraisers = Array.from({ length: A }, (_, i) => ed.appraisers[i] ?? String.fromCharCode(65 + i));
  ed.values = Array.from({ length: A }, (_, a) => Array.from({ length: T }, (_, t) => Array.from({ length: P }, (_, p) => old[a]?.[t]?.[p] ?? '')));
  ed.reference = Array.from({ length: P }, (_, p) => ed.reference[p] ?? '');
}
/** Paste a block from Excel (tab / newline separated): fill right and down from the current cell */
function paste(e: ClipboardEvent, ai: number, ti: number, pi: number) {
  const txt = e.clipboardData?.getData('text') || '';
  if (!/[\t\n]/.test(txt.trim())) return;
  e.preventDefault();
  const rows = txt.replace(/\r/g, '').split('\n').filter((x) => x.length).map((r) => r.split(/\t|,/));
  const flat: [number, number][] = [];
  ed.values.forEach((a: any, x: number) => a.forEach((_: any, y: number) => flat.push([x, y])));
  let k = flat.findIndex(([x, y]) => x === ai && y === ti);
  for (const r of rows) {
    if (k < 0 || k >= flat.length) break;
    const [x, y] = flat[k++];
    r.forEach((v, j) => { if (pi + j < nParts.value) ed.values[x][y][pi + j] = v.trim(); });
  }
}
const num = (v: string) => (v.trim() === '' ? null : Number(v));
const attrVal = (v: string) => { const t = v.trim(); return t === '' ? null : /^-?\d+(\.\d+)?$/.test(t) ? Number(t) : t; };
function collect(): any | string {
  const m = cur.value.method;
  if (isMatrix.value) {
    const conv = m === 'attribute' ? attrVal : num;
    const values = ed.values.map((a: string[][]) => a.map((t) => t.map(conv)));
    if (values.some((a: any[][]) => a.some((t) => t.some((x) => x === null || (typeof x === 'number' && isNaN(x)))))) return $t('msa.someCellsAreEmptyOr');
    const d: any = { appraisers: ed.appraisers.map((x: string, i: number) => x.trim() || String.fromCharCode(65 + i)), values };
    if (m === 'attribute') { const ref = ed.reference.map(attrVal); if (ref.some((x: any) => x !== null)) { if (ref.some((x: any) => x === null)) return $t('msa.theStandardRowMustBe'); d.reference = ref; } }
    return d;
  }
  if (m === 'bias') {
    const rd = ed.readings.map(num).filter((x: any) => x !== null);
    if (rd.some((x: number) => isNaN(x))) return $t('msa.someReadingsAreNotNumbers');
    return { readings: rd };
  }
  const parts = ed.parts.filter((p: any) => p.reference.trim() !== '').map((p: any) => ({ reference: Number(p.reference), readings: p.readings.map(num).filter((x: any) => x !== null) }));
  if (parts.some((p: any) => isNaN(p.reference) || p.readings.some((x: number) => isNaN(x)))) return $t('msa.someReferenceValuesOrReadings');
  return { parts };
}

// ---------- Load / actions ----------
async function loadList() { list.value = (await get('/api/e/msa_study?size=200&sort=-id')).items; loaded.value = true; }
async function loadCur(id: number) { cur.value = (await get(`/api/e/msa_study/${id}`)).item; fillEditor(cur.value); }
function pick(id: number) { router.replace({ query: { id } }); }
async function fromRoute() {
  const id = Number(route.query.id || 0);
  if (id) await loadCur(id).catch(() => { cur.value = null; });
  else if (list.value.length) pick(list.value[0].id);
}
async function saveField(k: 'tolerance' | 'reference') {
  try { await put(`/api/e/msa_study/${cur.value.id}`, { [k]: cur.value[k] ?? null }); } catch { /* already notified */ }
}
async function calc() {
  const d = collect();
  if (typeof d === 'string') { ElMessage.warning(d); return; }
  busy.value = 'calc';
  try {
    const r = await post(`/api/e/msa_study/${cur.value.id}/do/calc`, { data: d });
    ElMessage.success(r.msg || $t('msa.calculationComplete'));
    await Promise.all([loadCur(cur.value.id), loadList()]);
  } catch { /* already notified */ } finally { busy.value = ''; }
}
function report() { window.open(`/api/iatf/msa/${cur.value.id}/report?token=${tok()}`, '_blank'); }

const imp = reactive({ open: false, csv: '' });
async function pickCsv(e: Event) { const f = (e.target as HTMLInputElement).files?.[0]; if (f) imp.csv = await f.text(); }
async function doImport() {
  busy.value = 'imp';
  try {
    await post(`/api/iatf/msa/${cur.value.id}/import`, { csv: imp.csv });
    ElMessage.success($t('msa.importedClickCalculateToGet')); imp.open = false; await loadCur(cur.value.id);
  } catch { /* already notified */ } finally { busy.value = ''; }
}

const nf = reactive<any>({ open: false });
function openNew() { Object.assign(nf, { open: true, title: '', method: 'grr_anova', gauge: null, char: '', tolerance: null, reference: null, parts: 10, appraisers: 3, trials: 3 }); }
watch(() => nf.method, (m) => {
  if (m === 'bias') nf.parts = 15; else if (m === 'linearity') { nf.parts = 5; nf.trials = 12; } else { nf.parts = 10; nf.trials = m === 'attribute' ? 2 : 3; }
});
async function saveNew() {
  busy.value = 'new';
  try {
    const { open, ...body } = nf; void open;
    const r = await post('/api/e/msa_study', body);
    nf.open = false; await loadList(); pick(r.id);
  } catch { /* already notified */ } finally { busy.value = ''; }
}

watch(() => route.query.id, () => { if (loaded.value) fromRoute(); });
onMounted(async () => { await loadList(); await fromRoute(); });
</script>

<style scoped>
.lay { display: grid; grid-template-columns: calc(270px * var(--hm-scale)) minmax(0, 1fr); gap: var(--hm-gap); align-items: start; }
.side { padding: var(--hm-sp-1) 0; }
.it { display: flex; flex-direction: column; align-items: stretch; gap: 3px; width: 100%; padding: 9px var(--hm-sp-3); border: 0; border-left: 3px solid transparent; background: transparent;
  text-align: left; cursor: pointer; font: inherit; color: var(--hm-text); border-bottom: 1px solid var(--hm-hairline); }
.it:hover { background: var(--hm-surface-3); }
.it.on { background: var(--hm-primary-soft); border-left-color: var(--hm-primary); }
.r1 { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.c { font-size: var(--hm-fs-2xs); color: var(--hm-text-2); }
.n { font-size: var(--hm-fs-sm); line-height: 1.35; white-space: normal; word-break: break-all; }
.r3 { display: flex; flex-wrap: wrap; gap: 2px 10px; color: var(--hm-text-2); }
.r3 b { color: var(--hm-text); font-weight: 600; }
.main { min-width: 0; }
.hd { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.hd .el-button + .el-button { margin-left: 0; }
.ttl { font-size: var(--hm-fs-lg); font-weight: 600; }
.meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 20px; margin-top: var(--hm-sp-2); font-size: var(--hm-fs-xs); color: var(--hm-text-2); }
.meta b { font-weight: 500; color: var(--hm-text); }
.fld { display: inline-flex; align-items: center; gap: 6px; }
.lnk { color: var(--hm-primary); cursor: pointer; }
.dims { display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.gw { overflow-x: auto; max-width: 100%; padding-bottom: 4px; }
.grid-t { border-collapse: collapse; font-size: var(--hm-fs-xs); }
.grid-t th { font-weight: 600; color: var(--hm-text-2); background: var(--hm-surface-2); padding: 4px 4px; text-align: center; font-family: var(--hm-font-num); white-space: nowrap; }
.grid-t th.l, .grid-t td.l { text-align: left; padding: 2px 6px; white-space: nowrap; }
.grid-t td { padding: 2px; border-bottom: 1px solid var(--hm-hairline); }
.grid-t tr.first td { border-top: 1px solid var(--hm-border); }
.grid-t tr.ref td { background: var(--hm-amber-soft); }
.cell, .nm { height: calc(26px * var(--hm-scale)); border: 1px solid var(--hm-border); border-radius: var(--hm-r-xs); background: var(--hm-surface); color: var(--hm-text);
  padding: 0 4px; font-size: var(--hm-fs-xs); outline: none; box-sizing: border-box; }
.cell { width: calc(72px * var(--hm-scale)); text-align: right; }
.cell.attr { width: calc(44px * var(--hm-scale)); text-align: center; }
.cell.refc { width: calc(64px * var(--hm-scale)); }
.nm { width: calc(72px * var(--hm-scale)); font-family: var(--hm-font); }
.cell:focus, .nm:focus { border-color: var(--hm-primary); box-shadow: var(--hm-focus); }
.rd { display: grid; grid-template-columns: repeat(auto-fill, minmax(calc(104px * var(--hm-scale)), 1fr)); gap: 6px; }
.rc { display: flex; align-items: center; gap: 4px; }
.rc .cell { width: 100%; }
.hint { margin: var(--hm-sp-2) 0 0; }
.t2 { color: var(--hm-text-2); } .small { font-size: var(--hm-fs-2xs); }
.fl { margin-top: 8px; }
.file { font-size: var(--hm-fs-xs); color: var(--hm-text-2); }
@media (max-width: 1100px) { .lay { grid-template-columns: minmax(0, 1fr); } }
</style>
