<template>
  <div class="page rep">
    <HmPageHead :title="$t('common.productionReports')" :sub="$t('reports.machineAutoReportsAreAggregated', [interval])">
      <el-segmented v-model="mode" :options="[{ label: $t('reports.groupedTotals'), value: 'group' }, { label: $t('common.details'), value: 'list' }]" data-test="rep-mode" />
    </HmPageHead>
    <HmToolbar>
      <el-select v-model="range" style="width: 130px" data-test="rep-range"><el-option v-for="r in RANGES" :key="r.v" :label="r.l" :value="r.v" /></el-select>
      <el-select v-model="src" style="width: 170px" :placeholder="$t('reports.allSources')" clearable data-test="rep-src">
        <el-option :label="$t('reports.machineAutoAggregated')" value="auto" /><el-option :label="$t('reports.manualScanTerminal')" value="manual,mobile,scan,terminal" />
      </el-select>
      <el-input v-model="kw" :placeholder="$t('reports.workOrderOperationEquipmentReporter')" clearable style="width: 220px" @keyup.enter="load"><template #prefix><el-icon><Search /></el-icon></template></el-input>
      <template #right>
        <el-button @click="load"><el-icon><Refresh /></el-icon></el-button>
        <el-button @click="exportCsv"><el-icon><Download /></el-icon>{{ $t('common.export') }}</el-button>
      </template>
    </HmToolbar>

    <div class="grid g4 kpis">
      <HmKpi :label="$t('reports.totalGood')" :value="g?.total.good" format="int" :unit="$t('common.pcs')" :hint="rangeText" />
      <HmKpi :label="$t('reports.totalDefective')" :value="g?.total.ng" format="int" :unit="$t('common.pcs')" :status="(g?.total.ng || 0) > 0 ? 'warn' : ''" />
      <HmKpi :label="$t('common.productionReports')" :value="g?.total.records" format="int" :unit="$t('common.k2047')" :hint="$t('reports.n0OfThemAreMachine', [autoN])" />
      <HmKpi :label="$t('reports.manualScanTerminal')" :value="manualN" format="int" :unit="$t('common.k2047')" :hint="$t('reports.traceableToReporter')" />
    </div>

    <HmSkeleton v-if="loading && !g" variant="table" :rows="8" />
    <template v-else-if="mode === 'group'">
      <HmEmpty v-if="!g?.groups.length" icon="search" :title="$t('reports.noProductionReportsInThis')" :desc="$t('reports.tryAnotherTimeRangeOr')" />
      <HmCard v-for="w in g?.groups" :key="w.work_order" class="wo mt" flush data-test="rep-wo">
        <template #default>
          <header class="woh" v-ai="{ kind: 'record', entity: 'work_order', id: w.work_order, label: $t('reports.workOrder0ReportedGood', [w.code, w.good]), data: { [$t('common.workOrder')]: w.code, [$t('common.product')]: w.product, [$t('common.plan')]: w.qty, [$t('common.good')]: w.good, [$t('common.defective')]: w.ng, [$t('common.record')]: w.records } }">
            <b class="hm-mono code" @click="router.push('/e/work_order?id=' + w.work_order)">{{ w.code }}</b>
            <span class="muted">{{ w.product }}</span><HmTag v-if="w.status" :tone="w.status === 'in_progress' ? 'primary' : 'neutral'">{{ WST[w.status] || w.status }}</HmTag>
            <span class="grow" />
            <span class="tot">{{ $t('common.good') }} <b class="hm-num">{{ fmtNum(w.good, 0) }}</b> {{ $t('reports.defective') }} <b class="hm-num" :class="{ bad: w.ng }">{{ fmtNum(w.ng, 0) }}</b> {{ $t('reports.n0Records', [w.records]) }}</span>
          </header>
          <table class="gt">
            <thead><tr><th style="width: 30%">{{ $t('reports.operationEquipment') }}</th><th>{{ $t('common.source') }}</th><th class="n">{{ $t('common.good') }}</th><th class="n">{{ $t('common.defective') }}</th><th class="n">{{ $t('reports.records') }}</th><th>{{ $t('reports.timeSlot') }}</th><th class="n">{{ $t('reports.operationTotalPlan') }}</th></tr></thead>
            <tbody>
              <template v-for="o in w.ops" :key="o.wo_op">
                <tr class="op" @click="toggle(o)" :data-test="'rep-op-' + o.seq">
                  <td><el-icon class="car" :class="{ open: isOpen(o) }"><ArrowRight /></el-icon><b>{{ o.seq }} {{ o.process }}</b> <span class="muted small">{{ $t('reports.n0MachinesSources', [o.devices.length]) }}</span></td>
                  <td></td>
                  <td class="n hm-num">{{ fmtNum(o.good, 0) }}</td><td class="n hm-num" :class="{ bad: o.ng }">{{ fmtNum(o.ng, 0) }}</td><td class="n hm-num">{{ o.records }}</td><td></td>
                  <td class="n"><span class="hm-num">{{ fmtNum(o.op_good, 0) }}</span><span class="muted"> / {{ fmtNum(o.qty_plan, 0) }}</span>
                    <i class="pb"><b :style="{ width: Math.min(100, (o.op_good || 0) / (o.qty_plan || 1) * 100) + '%' }" /></i></td>
                </tr>
                <tr v-for="d in isOpen(o) ? o.devices : []" :key="d.code + o.wo_op" class="dev" @click="openRecords(w, o, d)" data-test="rep-dev"
                  v-ai="{ kind: 'row', entity: 'report', label: $t('reports.n012Good3', [w.code, o.process, d.code, d.good]), data: { [$t('common.workOrder')]: w.code, [$t('common.operation')]: o.process, [$t('common.equipment')]: `${d.code} ${d.name}`, [$t('common.good')]: d.good, [$t('common.defective')]: d.ng, [$t('reports.records')]: d.records, [$t('common.source')]: d.source_text, [$t('reports.timeSlot')]: `${d.first} – ${d.last}` } }">
                  <td class="dn"><span class="hm-mono">{{ d.code }}</span> {{ d.name }}</td>
                  <td><HmTag :tone="d.sources.auto ? 'neutral' : 'primary'">{{ d.source_text }}</HmTag></td>
                  <td class="n hm-num">{{ fmtNum(d.good, 0) }}</td><td class="n hm-num" :class="{ bad: d.ng }">{{ fmtNum(d.ng, 0) }}</td><td class="n hm-num">{{ d.records }}</td>
                  <td class="muted small hm-num">{{ fmtTime(d.first) }} – {{ fmtTime(d.last, 'time') }}</td><td class="n"><el-button link type="primary" size="small">{{ $t('reports.viewRecords') }}</el-button></td>
                </tr>
              </template>
            </tbody>
          </table>
        </template>
      </HmCard>
    </template>
    <HmCard v-else flush class="mt">
      <el-table :data="list" v-loading="loading" height="calc(100vh - 330px)" @row-click="openDetail" data-test="rep-list"
        v-ai-rows="{ entity: 'report', rows: () => list, label: (r: any) => $t('reports.report01Good2', [r.code, r.work_order__label || '', r.qty_good]), data: (r: any) => ({ [$t('common.workOrder')]: r.work_order__label, [$t('common.operation')]: r.process__label, [$t('common.equipment')]: r.equipment__label, [$t('common.good')]: r.qty_good, [$t('common.defective')]: r.qty_ng, [$t('common.source')]: r.source_text, [$t('reports.timeSlot')]: `${r.start} – ${r.end}` }) }">
        <el-table-column prop="code" :label="$t('common.docNo')" width="140"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
        <el-table-column prop="work_order__label" :label="$t('common.workOrder')" width="130" />
        <el-table-column prop="process__label" :label="$t('common.operation')" min-width="120" />
        <el-table-column prop="equipment__label" :label="$t('common.equipment')" min-width="140"><template #default="{ row }">{{ row.equipment__label || '—' }}</template></el-table-column>
        <el-table-column :label="$t('common.good')" width="80" align="right"><template #default="{ row }"><span class="hm-num">{{ row.qty_good }}</span></template></el-table-column>
        <el-table-column :label="$t('common.defective')" width="70" align="right"><template #default="{ row }"><span class="hm-num" :class="{ bad: row.qty_ng }">{{ row.qty_ng }}</span></template></el-table-column>
        <el-table-column :label="$t('reports.timeSlot')" width="170"><template #default="{ row }"><span class="hm-num small">{{ fmtTime(row.start) }} – {{ fmtTime(row.end, 'time') }}</span></template></el-table-column>
        <el-table-column :label="$t('common.source')" width="190"><template #default="{ row }"><HmTag :tone="row.source === 'auto' ? 'neutral' : 'primary'">{{ row.source_text }}</HmTag></template></el-table-column>
        <el-table-column prop="employee__label" :label="$t('reports.reportedBy')" width="110"><template #default="{ row }">{{ row.employee__label || (row.source === 'auto' ? $t('common.equipment') : '—') }}</template></el-table-column>
      </el-table>
      <div class="pg"><el-pagination v-model:current-page="page" :page-size="50" :total="total" layout="total, prev, pager, next" @current-change="loadList" /></div>
    </HmCard>

    <!-- Records of an operation on a machine -->
    <el-drawer v-model="recOpen" :title="recTitle" size="min(720px, 96vw)" destroy-on-close>
      <p class="muted small">{{ recNote }}</p>
      <el-table :data="recs" size="small" @row-click="openDetail" data-test="rep-recs">
        <el-table-column :label="$t('reports.timeSlot')" min-width="150"><template #default="{ row }"><span class="hm-num">{{ fmtTime(row.start) }} – {{ fmtTime(row.end, 'time') }}</span></template></el-table-column>
        <el-table-column :label="$t('common.good')" width="70" align="right"><template #default="{ row }"><span class="hm-num">{{ row.qty_good }}</span></template></el-table-column>
        <el-table-column :label="$t('common.defective')" width="70" align="right"><template #default="{ row }"><span class="hm-num" :class="{ bad: row.qty_ng }">{{ row.qty_ng }}</span></template></el-table-column>
        <el-table-column :label="$t('common.source')" width="180"><template #default="{ row }">{{ row.source_text }}</template></el-table-column>
        <el-table-column label="" width="70"><template #default><el-button link type="primary" size="small">{{ $t('common.details2') }}</el-button></template></el-table-column>
      </el-table>
    </el-drawer>

    <!-- Detail -->
    <el-drawer v-model="detOpen" :title="det ? $t('reports.productionReport0', [det.item.code]) : $t('reports.productionReportDetails')" size="min(860px, 96vw)" destroy-on-close data-test="rep-detail">
      <template v-if="det">
        <div class="dhead">
          <HmTag :tone="det.item.source === 'auto' ? 'neutral' : 'primary'" data-test="rep-source">{{ det.item.source_text }}</HmTag>
          <HmTag v-if="det.fai" :tone="det.fai.ok ? 'ok' : 'warn'">{{ det.fai.ok ? $t('reports.firstArticleReleased') : $t('bizkit.firstArticleNotReleased') }}</HmTag>
          <span class="grow" />
          <el-button size="small" v-if="det.work_order" @click="router.push('/e/work_order?id=' + det.work_order.id)">{{ $t('common.workOrder') }}</el-button>
          <el-button size="small" v-if="det.parts.length" @click="router.push('/trace?sn=' + det.parts[0].sn)">{{ $t('common.trace') }}</el-button>
        </div>
        <el-descriptions :column="2" border size="small" class="mt-s">
          <el-descriptions-item :label="$t('common.workOrder')">{{ det.work_order?.code || '—' }} <span class="muted">{{ det.work_order?.product__label }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('common.operation')">{{ det.op?.seq }} {{ det.instruction?.process || det.item.process__label }}</el-descriptions-item>
          <el-descriptions-item :label="$t('reports.equipmentCode')"><span class="hm-mono">{{ det.equipment?.code || '—' }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('reports.equipmentName')">{{ det.equipment?.name || (det.item.source === 'auto' ? $t('reports.noSingleMachineCountedBy') : '—') }}</el-descriptions-item>
          <el-descriptions-item :label="$t('common.quantity')"><b class="hm-num">{{ $t('reports.good0', [det.item.qty_good]) }}</b> · <span :class="{ bad: det.item.qty_ng }">{{ $t('reports.defective0', [det.item.qty_ng]) }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('reports.productionPeriod')"><span class="hm-num">{{ det.item.start }} – {{ fmtTime(det.item.end, 'time') }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('reports.enteredAt')"><span class="hm-num">{{ det.entered_at }}</span><span v-if="det.updated_at !== det.entered_at" class="muted small">{{ $t('reports.lastAdded0', [fmtTime(det.updated_at, 'time')]) }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('common.operator')" data-test="rep-operator">{{ det.operator.text }}</el-descriptions-item>
        </el-descriptions>

        <HmCard v-if="det.casting" :title="$t('reports.castingInfo')" :sub="$t('reports.n0PcsInThisSlot', [det.parts_total])" class="mt-s" data-test="rep-casting">
          <div class="cast">
            <div v-for="h in det.casting.heats" :key="h.code" class="ch" v-ai="{ kind: 'field', heat: h.code, label: $t('reports.heat01Pcs', [h.code, h.parts]), data: h }">
              <b class="hm-mono">{{ h.code }}</b><span>{{ $t('packStation.n0Pcs2', [h.parts]) }}</span>
              <small class="hm-num">{{ $t('reports.c0Si1Ce', [fmtNum(h.C, 2), fmtNum(h.Si, 2), fmtNum(h.CE, 2), fmtNum(h.tap_temp, 0)]) }}</small>
            </div>
            <div class="ch" v-if="det.casting.ladles.length"><b>{{ $t('common.ladle') }}</b><span>{{ det.casting.ladles.join($t('common.k42')) }}</span></div>
            <div class="ch" v-if="det.casting.pour_temp_avg"><b>{{ $t('reports.avgPouringTemp') }}</b><span class="hm-num">{{ det.casting.pour_temp_avg }} °C</span></div>
          </div>
        </HmCard>
        <HmCard v-if="det.parts.length" :title="$t('reports.partsPassed0', [det.parts_total])" flush class="mt-s">
          <el-table :data="det.parts" size="small" max-height="260">
            <el-table-column :label="$t('common.partCode')" width="150"><template #default="{ row }"><el-button link type="primary" @click="router.push('/trace?sn=' + row.sn)">{{ row.sn }}</el-button></template></el-table-column>
            <el-table-column :label="$t('common.time')" width="90"><template #default="{ row }"><span class="hm-num">{{ fmtTime(row.t, 'time') }}</span></template></el-table-column>
            <el-table-column prop="heat" :label="$t('common.heat')" width="120" /><el-table-column prop="ladle" :label="$t('common.ladle')" width="90" />
            <el-table-column :label="$t('common.pouringTemp')" width="90" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.pour_temp, 0) }}</span></template></el-table-column>
            <el-table-column prop="machine" :label="$t('common.machineTool')" width="80" />
            <el-table-column :label="$t('common.result')" width="70"><template #default="{ row }"><HmTag :tone="row.ok ? 'ok' : 'bad'">{{ row.ok ? $t('common.good') : $t('common.defective') }}</HmTag></template></el-table-column>
          </el-table>
        </HmCard>
        <HmCard :title="$t('common.workInstructions')" class="mt-s"><InstructionPanel :ins="det.instruction" /></HmCard>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { askAi, registerAiActions } from '../ai';
import { get, tok } from '../api';
import InstructionPanel from '../components/biz/InstructionPanel.vue';
import { fmtNum, fmtTime } from '../design';

const router = useRouter(), route = useRoute();
const RANGES = [{ v: 'today', l: $t('common.today') }, { v: '24', l: $t('coplan.last24H') }, { v: '72', l: $t('reports.last3Days') }, { v: '168', l: $t('reports.last7Days') }];
const WST: Record<string, string> = { released: $t('common.released'), in_progress: $t('common.inProduction'), completed: $t('gantt.completed'), closed: $t('common.off') };
const mode = ref<'group' | 'list'>((route.query.mode as any) || 'group'), range = ref('24'), src = ref(''), kw = ref('');
const g = ref<any>(null), loading = ref(false), list = ref<any[]>([]), total = ref(0), page = ref(1);
const opened = ref(new Set<string>()), interval = ref(60);
const recOpen = ref(false), recs = ref<any[]>([]), recTitle = ref(''), recNote = ref('');
const detOpen = ref(false), det = ref<any>(null);

const qs = () => {
  const p = new URLSearchParams();
  if (range.value === 'today') p.set('day', ''); else p.set('hours', range.value);
  if (src.value) p.set('source', src.value);
  return p;
};
const rangeText = computed(() => RANGES.find((r) => r.v === range.value)?.l);
const autoN = computed(() => (g.value?.groups || []).reduce((a: number, w: any) => a + w.ops.reduce((b: number, o: any) => b + o.devices.reduce((c: number, d: any) => c + (d.sources.auto || 0), 0), 0), 0));
const manualN = computed(() => (g.value?.total.records || 0) - autoN.value);
const key = (o: any) => String(o.wo_op);
const isOpen = (o: any) => opened.value.has(key(o));
function toggle(o: any) { const s = new Set(opened.value); s.has(key(o)) ? s.delete(key(o)) : s.add(key(o)); opened.value = s; }

async function load() {
  loading.value = true;
  try {
    const p = qs(); if (kw.value) p.set('q', kw.value);
    g.value = await get('/api/reports/grouped?' + p);
    const ivs = g.value.groups.flatMap((w: any) => w.ops.flatMap((o: any) => o.devices.map((d: any) => d.interval_min))).filter(Boolean);
    if (ivs.length) interval.value = ivs[0];
    if (!opened.value.size) opened.value = new Set(g.value.groups.slice(0, 1).flatMap((w: any) => w.ops.slice(-3).map(key)));
    if (mode.value === 'list') await loadList();
  } finally { loading.value = false; }
}
async function loadList() {
  const p = qs(); p.set('page', String(page.value)); p.set('size', '50');
  const r = await get('/api/reports/list?' + p);
  list.value = r.items; total.value = r.total;
}
async function openRecords(w: any, o: any, d: any) {
  const p = qs(); p.set('wo_op', String(o.wo_op)); if (d.equipment) p.set('equipment', String(d.equipment)); p.set('size', '200');
  const r = await get('/api/reports/list?' + p);
  recs.value = d.equipment ? r.items : r.items.filter((x: any) => !x.equipment && x.source === Object.keys(d.sources)[0]);
  recTitle.value = `${w.code} · ${o.seq} ${o.process} · ${d.code} ${d.name}`;
  recNote.value = d.sources.auto ? $t('reports.machineAutoReportingCollectedParts', [d.interval_min || 60]) : $t('reports.manualScanWorkstationTerminalReports');
  recOpen.value = true;
}
async function openDetail(row: any) { det.value = await get(`/api/reports/${row.id}/detail`); detOpen.value = true; }
function exportCsv() { window.open(`/api/e/report.csv?token=${tok()}`); }

const off = registerAiActions('row', [
  { id: 'rep-detail', label: $t('reports.viewReportDetails'), icon: 'Document', order: 105, when: (it) => it.entity === 'report' && !!it.id, run: (it) => openDetail({ id: it.id }) },
  { id: 'rep-explain', label: $t('reports.aiInterpretThisOutput'), icon: 'MagicStick', ai: true, order: 30, when: (it) => it.entity === 'report',
    run: (it, sel) => askAi($t('reports.interpretTheseProductionReportsIs', [it.label]), sel, { action: 'explain' }) },
]);
onUnmounted(off);
watch([range, src], load);
watch(mode, (m) => { if (m === 'list') loadList(); });
onMounted(async () => { await load(); if (route.query.id) openDetail({ id: route.query.id }); });
</script>

<style scoped>
.kpis { margin-top: var(--hm-gap); }
.woh { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-bottom: 1px solid var(--hm-border); background: var(--hm-surface-2); flex-wrap: wrap; }
.woh .code { color: var(--hm-primary); cursor: pointer; font-size: var(--hm-fs-md); }
.tot { font-size: var(--hm-fs-xs); color: var(--hm-text-2); }
.gt { width: 100%; border-collapse: collapse; font-size: var(--hm-fs-sm); }
.gt th { text-align: left; font-weight: var(--hm-fw-medium); color: var(--hm-text-3); font-size: var(--hm-fs-2xs); padding: 6px 12px; border-bottom: 1px solid var(--hm-border); }
.gt td { padding: 7px 12px; border-bottom: 1px solid var(--hm-hairline); }
.gt .n { text-align: right; }
.gt tr.op { cursor: pointer; } .gt tr.op:hover, .gt tr.dev:hover { background: var(--hm-surface-3); }
.gt tr.dev { cursor: pointer; background: #fbfcfe; } .gt td.dn { padding-left: 38px; }
.car { transition: transform var(--hm-dur-1); vertical-align: -2px; margin-right: 4px; color: var(--hm-text-3); } .car.open { transform: rotate(90deg); }
.pb { display: inline-block; width: 60px; height: 4px; background: var(--hm-surface-3); border-radius: 2px; margin-left: 8px; vertical-align: middle; overflow: hidden; }
.pb b { display: block; height: 100%; background: var(--hm-primary); }
.bad { color: var(--hm-fault-ink); }
.muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-2xs); }
.pg { display: flex; justify-content: flex-end; padding: 8px 12px; }
.dhead { display: flex; gap: 8px; align-items: center; }
.mt-s { margin-top: 12px; }
.cast { display: flex; flex-wrap: wrap; gap: 8px; }
.ch { display: flex; flex-direction: column; gap: 2px; padding: 8px 10px; border: 1px solid var(--hm-border); border-radius: var(--hm-r-sm); background: var(--hm-surface-2); min-width: 150px; }
.ch small { color: var(--hm-text-3); }
</style>
