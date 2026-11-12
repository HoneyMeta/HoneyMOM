<template>
  <div class="mom" data-test="mom-domains">
    <section v-for="d in domains" :key="d.key" class="dom hm-card" :class="'d-' + d.key" :data-test="'mom-' + d.key">
      <header>
        <span class="ic"><el-icon><component :is="d.icon" /></el-icon></span>
        <b>{{ d.title }}</b>
        <span class="grow" />
        <router-link class="more" :to="d.to">{{ $t('mom.open') }} ›</router-link>
      </header>
      <div class="kp">
        <HmKpi v-for="k in d.kpis" :key="k.label" compact :tick="false" :label="k.label" :value="k.value" :format="k.format || 'int'" :unit="k.unit"
          :status="k.status || ''" :to="k.to" :loading="k.value === undefined" :ai="{ data: { [d.title]: k.label, value: k.value } }" />
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
// Home cockpit, one card per ISA-95 operations domain (production, quality, maintenance, inventory) with its key KPIs.
// Values come from /api/home/cockpit (passed in) plus a few existing list APIs (totals with f_<field> filters), refreshed every 30 s.
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { $t } from '../../i18n';
import { call } from '../../api';
import { shell } from '../../layout/shell';

const props = defineProps<{ ck: any }>();
const K = computed(() => props.ck?.kpi);
const n = ref<Record<string, number | null>>({});

const total = (path: string) => call('GET', path, undefined, true).then((r: any) => (typeof r?.total === 'number' ? r.total : null)).catch(() => null);
const sum = async (paths: string[]) => { const v = await Promise.all(paths.map(total)); return v.every((x) => x === null) ? null : v.reduce((a: number, x) => a + (x || 0), 0); };
async function load() {
  const [wip, andon, fai, hold, repair, kit, mreq] = await Promise.all([
    total('/api/e/work_order?size=1&f_status=in_progress'),
    sum(['/api/e/andon?size=1&f_status=open', '/api/e/andon?size=1&f_status=responding']),
    total('/api/e/first_article?size=1&f_status=pending'),
    total('/api/e/lot_hold?size=1&f_status=hold'),
    sum(['/api/e/repair_order?size=1&f_status=reported', '/api/e/repair_order?size=1&f_status=assigned', '/api/e/repair_order?size=1&f_status=repairing']),
    total('/api/e/wo_kitting?size=1&f_status=short'),
    total('/api/e/material_request?size=1&f_status=submitted'),
  ]);
  const pm = await call('GET', '/api/equipment/maint_due', undefined, true).then((r: any) => (Array.isArray(r) ? r.length : null)).catch(() => null);
  const low = await call('GET', '/api/warehouse/overview', undefined, true).then((r: any) => (Array.isArray(r) ? r.filter((x: any) => x.low).length : null)).catch(() => null);
  n.value = { wip, andon, fai, hold, repair, kit, mreq, pm, low };
}
let t: any;
onMounted(() => { load(); t = setInterval(load, 30000); });
onUnmounted(() => clearInterval(t));

const v = (x: any) => (x === undefined ? undefined : x);
const runRate = computed(() => {
  const s = props.ck?.status || {}; const tot = Object.values(s).reduce((a: number, b: any) => a + Number(b || 0), 0);
  return tot ? (s.running || 0) / tot : (props.ck ? null : undefined);
});
const domains = computed(() => [
  { key: 'prod', title: $t('nav.grpProduction'), icon: 'Operation', to: '/e/work_order', kpis: [
    { label: $t('mom.outputToday'), value: v(K.value?.stored?.value), unit: $t('mom.pcs'), to: '/e/report' },
    { label: $t('home.planAttainment'), value: v(K.value?.stored?.attain), format: 'pct', to: '/production/gantt',
      status: K.value?.stored?.attain == null ? '' : K.value.stored.attain >= 0.95 ? 'ok' : K.value.stored.attain >= 0.8 ? 'warn' : 'bad' },
    { label: $t('mom.wosInProduction'), value: n.value.wip, to: '/e/work_order' },
    { label: $t('mom.openAndon'), value: n.value.andon, to: '/andon', status: n.value.andon ? 'warn' : '' },
  ] },
  { key: 'quality', title: $t('nav.grpQuality'), icon: 'CircleCheck', to: '/quality', kpis: [
    { label: $t('common.scrapRate'), value: v(K.value?.scrap_rate?.value), format: 'pct', to: '/quality', status: K.value && K.value.scrap_rate?.value > 0.03 ? 'bad' : '' },
    { label: $t('mom.openNcrs'), value: v(props.ck?.ncr_open), to: '/e/ncr' },
    { label: $t('mom.faiPending'), value: n.value.fai, to: '/quality/fai' },
    { label: $t('mom.lotsOnHold'), value: n.value.hold, to: '/quality/hold', status: n.value.hold ? 'warn' : '' },
  ] },
  { key: 'maint', title: $t('nav.grpMaintenance'), icon: 'Monitor', to: '/equipment/monitor', kpis: [
    { label: $t('home.avgOee'), value: v(K.value?.oee?.value), format: 'pct', to: '/equipment/oee', status: K.value?.oee?.value != null && K.value.oee.value < 0.6 ? 'bad' : '' },
    { label: $t('home.runningRate'), value: runRate.value, format: 'pct', to: '/equipment/monitor' },
    { label: $t('mom.activeAlarms'), value: shell.alarms ?? null, to: '/e/alarm', status: shell.alarms ? 'warn' : '' },
    { label: $t('mom.openRepairOrders'), value: n.value.repair, to: '/e/repair_order' },
    { label: $t('mom.pmDue'), value: n.value.pm, to: '/equipment/maint' },
  ].slice(0, 4) },
  { key: 'inv', title: $t('nav.grpInventory'), icon: 'Box', to: '/warehouse', kpis: [
    { label: $t('stock.belowSafetyStock'), value: n.value.low, to: '/warehouse', status: n.value.low ? 'warn' : '' },
    { label: $t('mom.kittingShortages'), value: n.value.kit, to: '/e/wo_kitting', status: n.value.kit ? 'bad' : '' },
    { label: $t('mom.openMaterialRequests'), value: n.value.mreq, to: '/e/material_request' },
    { label: $t('mom.shippedToday'), value: v(K.value?.stored?.shipped), unit: $t('mom.pcs'), to: '/exec/ship' },
  ] },
]);
</script>

<style scoped>
.mom { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--hm-gap, 12px); }
.dom { padding: 10px 12px 12px; min-width: 0; border-top: 3px solid var(--c, var(--hm-primary)); }
.d-prod { --c: var(--hm-primary); } .d-quality { --c: var(--hm-run, #1f9d55); } .d-maint { --c: var(--hm-cyan, #19a7c4); } .d-inv { --c: var(--hm-amber, #d9822b); }
header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; min-width: 0; }
header b { font-size: var(--hm-fs-md, 14px); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ic { display: inline-flex; width: 24px; height: 24px; border-radius: 6px; align-items: center; justify-content: center; color: var(--c); background: color-mix(in srgb, var(--c) 12%, transparent); flex: none; }
.grow { flex: 1; }
.more { font-size: 12px; color: var(--hm-text-3); text-decoration: none; white-space: nowrap; } .more:hover { color: var(--hm-primary); }
.kp { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
@media (max-width: 1500px) { .mom { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 700px) { .mom { grid-template-columns: 1fr; } }
</style>
