<template>
  <div class="page">
    <div class="page-head"><h2>{{ $t('common.traceability') }}</h2><span class="sub">{{ $t('trace.enterAPartCodeTo') }}</span></div>
    <div class="card" style="margin-bottom:14px;display:flex;gap:8px;flex-wrap:wrap">
      <el-input v-model="sn" :placeholder="$t('trace.partCodeEGSh2610300xxxx')" style="width:260px" clearable @keyup.enter="go" />
      <el-button type="primary" @click="go">{{ $t('trace.findPart') }}</el-button>
      <el-input v-model="heat" :placeholder="$t('trace.heatNoEGL261030')" style="width:220px" clearable @keyup.enter="goHeat" />
      <el-button @click="goHeat">{{ $t('trace.findHeat') }}</el-button>
      <span class="grow" style="flex:1" />
      <el-button v-if="d" @click="printIt"><el-icon><Printer /></el-icon>{{ $t('trace.printExportPdf') }}</el-button>
    </div>
    <template v-if="d">
      <div class="grid g3">
        <div class="card"><h3>{{ $t('common.part') }} <span class="tag-st" :class="'st-' + d.part.status">{{ PS[d.part.status] || d.part.status }}</span></h3>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item :label="$t('common.partCode')"><b class="mono">{{ d.part.sn }}</b></el-descriptions-item>
            <el-descriptions-item :label="$t('common.product')">{{ d.part.product__label }}</el-descriptions-item>
            <el-descriptions-item :label="$t('common.workOrder')">{{ d.part.work_order__label || '—' }}</el-descriptions-item>
            <el-descriptions-item :label="$t('layout.youAreHere')">{{ d.part.station }}</el-descriptions-item>
            <el-descriptions-item :label="$t('trace.machineTool')">{{ d.part.machine || '—' }}</el-descriptions-item>
            <el-descriptions-item :label="$t('trace.reworkCount')">{{ d.part.rework_count }}</el-descriptions-item>
            <el-descriptions-item v-if="d.part.scrap_cause" :label="$t('common.scrapReason')"><span class="bad">{{ d.part.scrap_cause }}</span></el-descriptions-item>
          </el-descriptions></div>
        <div class="card"><h3>{{ $t('trace.meltingPouring') }}</h3>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item :label="$t('common.heat')"><span class="click" style="color:#a8730f" @click="heat = d.part.heat; goHeat()">{{ d.part.heat || '—' }}</span>
              <span v-if="d.heat" class="muted small"> {{ d.heat.furnace__label }}</span></el-descriptions-item>
            <el-descriptions-item :label="$t('trace.baseIronChemistry')" v-if="d.heat">{{ $t('trace.c0Si1Ce', [d.heat.C, d.heat.Si, d.heat.CE]) }}</el-descriptions-item>
            <el-descriptions-item :label="$t('heats.tappingTemp')" v-if="d.heat">{{ d.heat.tap_temp }} °C</el-descriptions-item>
            <el-descriptions-item :label="$t('common.ladle')">{{ d.part.ladle || '—' }}<span v-if="d.treatment" class="muted small">&emsp;{{ $t('trace.n0CAfterNodularizationResidual', [d.treatment.temp, d.treatment.mg]) }}</span></el-descriptions-item>
            <el-descriptions-item :label="$t('common.pouringTemp')">{{ d.part.pour_temp ?? '—' }} °C</el-descriptions-item>
            <el-descriptions-item :label="$t('trace.residualMg')">{{ d.part.mg ?? '—' }} %</el-descriptions-item>
            <el-descriptions-item :label="$t('trace.sandMoldCavity')">{{ d.part.mold || '—' }} / {{ d.part.cavity ?? '—' }}</el-descriptions-item>
            <el-descriptions-item :label="$t('trace.sandMoistureCompactability')">{{ d.part.sand_moisture ?? '—' }} % / {{ d.part.compactability ?? '—' }} %</el-descriptions-item>
          </el-descriptions></div>
        <div class="card"><h3>{{ $t('trace.qualityRecords') }}</h3>
          <div v-for="r in d.inspections" :key="r.id" class="ln"><span class="tag-st" :class="'st-' + r.result">{{ r.result }}</span> {{ r.station }} <span class="muted small">{{ r.time.slice(5, 16) }}</span> {{ r.remark }}</div>
          <div v-for="m in d.measures" :key="m.created_at" class="ln">{{ $t('trace.bore') }} <b class="mono">{{ m.value?.toFixed(4) }}</b> mm <span :class="m.ok ? 'ok' : 'bad'">{{ m.ok ? $t('common.good') : $t('trace.outOfTolerance') }}</span></div>
          <div v-for="n in d.ncr" :key="n.id" class="ln"><span class="tag-st st-NG">NCR</span> <span class="click" @click="router.push('/e/ncr?id=' + n.id)">{{ n.code }}</span> {{ n.defect__label }} → {{ DISP[n.disposition] }}</div>
          <div v-for="r in d.rework" :key="r.id" class="ln"><span class="tag-st st-rework">{{ $t('common.rework') }}</span> {{ r.remark }} <span class="muted small">{{ r.result }}</span></div>
          <p v-if="!d.inspections.length && !d.ncr.length && !d.measures.length" class="muted">{{ $t('trace.noInspectionRecordsYet') }}</p>
          <template v-if="d.machining_ctx && d.machining_ctx.equipment">
            <h3 style="margin-top:12px">{{ $t('trace.atMachining0', [d.machining_ctx.equipment]) }}</h3>
            <div v-for="t in d.machining_ctx.tool_changes" :key="t.time" class="ln small">{{ $t('trace.toolChange012', [t.tool, t.tool_name, t.reason]) }}<span class="muted">{{ t.time.slice(5, 16) }}</span></div>
            <div v-for="a in d.machining_ctx.alarms" :key="a.start" class="ln small bad">{{ $t('trace.alarm0', [a.msg]) }}</div>
          </template>
        </div>
      </div>
      <div v-if="d.packing" class="card" style="margin-top:14px" data-test="trace-packing"><h3>{{ $t('trace.packingShipping') }}</h3>
        <el-descriptions :column="4" size="small" border>
          <el-descriptions-item :label="$t('common.cartonNo')"><b class="mono">{{ d.packing.carton }}</b></el-descriptions-item>
          <el-descriptions-item :label="$t('packStation.pallet')"><b class="mono">{{ d.packing.pallet || '—' }}</b></el-descriptions-item>
          <el-descriptions-item :label="$t('common.loadingLists')"><span class="mono">{{ d.packing.load || '—' }}</span></el-descriptions-item>
          <el-descriptions-item label="ASN"><span class="mono">{{ d.packing.asn_no || '—' }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('common.customer')">{{ d.packing.customer || '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('common.deliveryOrder')"><span class="mono">{{ d.packing.delivery_code || '—' }}</span></el-descriptions-item>
          <el-descriptions-item :label="$t('station.packing')">{{ (d.packing.packed_at || '').slice(5, 16) }} {{ d.packing.packed_by || '' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('common.shipped2')">{{ (d.packing.shipped_at || '').slice(5, 16) || $t('trace.notShipped') }}</el-descriptions-item>
        </el-descriptions>
        <el-button v-if="d.packing.load" link type="primary" style="margin-top:6px" @click="router.push('/exec/ship?load=' + d.packing.load_id)">{{ $t('trace.openLoadSheetAsn') }}</el-button>
      </div>
      <div class="card" style="margin-top:14px"><h3>{{ $t('trace.operationHistory') }}</h3>
        <el-timeline>
          <el-timeline-item v-for="e in d.events" :key="e.id" :timestamp="e.t" :type="e.event === 'scrap' ? 'danger' : e.event === 'rework' ? 'warning' : e.event === 'stored' || e.event === 'shipped' ? 'success' : 'primary'">
            <b>{{ e.label }}</b> <span class="muted">@ {{ e.station }}</span> <span class="small mono muted">{{ fmtData(e.data) }}</span>
          </el-timeline-item>
        </el-timeline>
      </div>
    </template>
    <div v-if="hd" class="card">
      <h3>{{ $t('trace.heat0ForwardTrace', [hd.heat?.code || heat]) }} <small>{{ $t('trace.n0Pcs', [hd.count]) }}</small></h3>
      <div style="margin-bottom:8px"><span v-for="(n, k) in hd.status" :key="k" class="tag-st" :class="'st-' + k" style="margin-right:6px">{{ PS[k] || k }} {{ n }}</span></div>
      <el-table :data="hd.parts" size="small" max-height="460" @row-click="(r: any) => { sn = r.sn; go(); }">
        <el-table-column prop="sn" :label="$t('common.partCode')" /><el-table-column :label="$t('common.status')"><template #default="{ row }"><span class="tag-st" :class="'st-' + row.status">{{ PS[row.status] || row.status }}</span></template></el-table-column>
        <el-table-column prop="station" :label="$t('common.position')" /><el-table-column prop="machine" :label="$t('common.machineTool')" /><el-table-column prop="scrap_cause" :label="$t('common.scrapReason')" />
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { get, tok } from '../api';

const route = useRoute(), router = useRouter();
const sn = ref((route.query.sn as string) || ''), heat = ref((route.query.heat as string) || ''), d = ref<any>(null), hd = ref<any>(null);
const PS: Record<string, string> = { wip: $t('common.wip'), good: $t('mobile.passedInspection'), fg: $t('mobile.fgStored'), shipped: $t('common.shipped'), scrap: $t('common.scrapped'), raw_store: $t('common.rawCastingStore'), rework: $t('common.inRework'), hold: $t('common.held') };
const DISP: Record<string, string> = { pending: $t('common.awaitingDisposition'), rework: $t('common.rework'), scrap: $t('common.scrapped'), concession: $t('common.useAsIs'), return: $t('common.return2') };
async function go() { if (!sn.value) return; hd.value = null; d.value = await get('/api/trace/part/' + encodeURIComponent(sn.value.trim())); }
async function goHeat() { if (!heat.value) return; hd.value = await get('/api/trace/heat/' + encodeURIComponent(heat.value.trim())); }
const fmtData = (o: any) => (o ? Object.entries(o).map(([k, v]) => `${k}=${v}`).join(' ') : '');
const printIt = () => window.open(`/api/trace/part/${encodeURIComponent(d.value.part.sn)}/print?token=${tok()}`);
onMounted(async () => {
  if (heat.value) { goHeat(); return; }
  if (sn.value) go();
  else { const r = await get('/api/e/part?f_status=fg&size=1'); if (r.items[0]) { sn.value = r.items[0].sn; } }
});
</script>

<style scoped>.ln { padding: 4px 0; font-size: 13px; border-bottom: 1px dashed #f0f0f0; }</style>
