<template>
  <div class="page mon">
    <HmPageHead :title="$t('common.liveMonitor')" :sub="$t('monitor.dataClock0RefreshesEvery', [(d?.now || '').slice(0, 19) || '—'])">
      <el-input v-model="kw" :placeholder="$t('monitor.equipmentCodeName')" clearable class="kw" data-tour="mon-search"><template #prefix><el-icon><Search /></el-icon></template></el-input>
      <el-select v-model="line" :placeholder="$t('common.allLines')" clearable class="ln"><el-option v-for="l in lines" :key="l" :label="l" :value="l" /></el-select>
    </HmPageHead>

    <!-- Status summary = filter -->
    <div class="sum" role="tablist" :aria-label="$t('monitor.filterByStatus')" data-tour="mon-filter">
      <button v-for="s in SUMS" :key="s.k" role="tab" class="sb" :class="[s.tone, { on: filter === s.k }]" :aria-selected="filter === s.k" :data-test="'mon-f-' + (s.k || 'all')" @click="filter = s.k"
        v-ai="s.k ? { kind: 'kpi', label: $t('monitor.n0Machines1', [s.label, count(s.k)]), data: { [$t('common.status')]: s.label, [$t('monitor.machines')]: count(s.k), [$t('monitor.total')]: all.length } } : null">
        <i v-if="s.tone" class="dot" /><span class="l">{{ s.label }}</span><b class="hm-num">{{ count(s.k) }}</b>
      </button>
    </div>

    <HmSkeleton v-if="!d" variant="table" :rows="6" />
    <HmEmpty v-else-if="!all.length" icon="offline" :title="$t('monitor.noEquipmentDataCollectedYet')" :desc="$t('monitor.checkWhetherTheDataSources')">
      <el-button type="primary" @click="router.push('/e/datasource')">{{ $t('common.dataSourcesTags') }}</el-button>
    </HmEmpty>
    <HmEmpty v-else-if="!shown.length" icon="search" compact :title="$t('monitor.noMatchingEquipment')"><el-button @click="filter = ''; kw = ''; line = ''">{{ $t('entityPage.clearFilters') }}</el-button></HmEmpty>

    <div v-else class="grid g-auto cards" style="--min: 248px">
      <article v-for="(e, i) in shown" :key="e.id" class="eq hm-card" :class="['s-' + st(e), { al: !!e.alarm }]" tabindex="0" :data-tour="i === 0 ? 'mon-card' : undefined"
        v-ai="devItem(e)" @click="open(e)" @keydown.enter="open(e)">
        <header class="eh">
          <span class="code hm-mono">{{ e.code }}</span><span class="nm">{{ bareName(e.code, e.name) }}</span>
          <HmStatus :status="st(e)" />
        </header>
        <div class="meta">{{ ET[e.etype] || e.etype }}<template v-if="lineName(e)"> · {{ lineName(e) }}</template></div>
        <dl class="vals">
          <div v-if="e.power != null"><dt>{{ $t('common.power') }}</dt><dd><HmNumber :value="e.power" :digits="1" /><small class="hm-unit">kW</small></dd></div>
          <div v-if="e.oee8h != null"><dt>OEE · 8h</dt><dd :class="oeeCls(e.oee8h)"><HmNumber :value="e.oee8h" format="pct" :digits="0" /></dd><i class="bar"><b :style="{ width: Math.min(100, e.oee8h * 100) + '%' }" :class="oeeCls(e.oee8h)" /></i></div>
          <div v-for="t in tagsOf(e)" :key="t.name" :title="t.name"><dt>{{ t.label }}</dt><dd><span class="hm-num">{{ t.text }}</span><small v-if="t.unit" class="hm-unit">{{ t.unit }}</small></dd></div>
        </dl>
        <footer v-if="e.alarm" class="alarm" v-ai="{ kind: 'alert', entity: 'equipment', id: e.id, label: $t('monitor.n0Alarm1', [e.code, e.alarm]), data: { [$t('common.equipment')]: e.code, [$t('common.alarm')]: e.alarm, equipment_id: e.id } }">
          <el-icon><WarningFilled /></el-icon><span>{{ e.alarm }}</span>
        </footer>
      </article>
    </div>

    <el-drawer v-model="dr" :title="cur ? codeName(cur.code, cur.name) : ''" size="min(720px, 100%)">
      <template v-if="cur">
        <div class="dh">
          <HmStatus :status="st(cur)" /><span class="muted small">{{ ET[cur.etype] || cur.etype }} · {{ cur.line__label || '—' }}</span>
        </div>
        <div class="dacts">
          <el-button @click="router.push('/equipment/oee?eid=' + cur.id)"><el-icon><DataLine /></el-icon>{{ $t('monitor.oeeDowntimeTimeline') }}</el-button>
          <el-button @click="router.push(newRec('repair_order', { equipment: cur.id, fault: cur.alarm || '' }))"><el-icon><SetUp /></el-icon>{{ $t('monitor.requestRepair') }}</el-button>
          <el-button @click="router.push('/e/equipment?id=' + cur.id)">{{ $t('common.equipmentRegister') }}</el-button>
          <span class="grow" />
          <el-button v-if="aiOn('watch')" type="primary" plain :loading="exb" @click="explain"><el-icon><MagicStick /></el-icon>{{ $t('monitor.aiExplainCurrentState') }}</el-button>
          <el-button v-if="aiOn('ask')" @click="dr = false; askAi($t('monitor.n0IsCurrently1Help', [codeName(cur.code, cur.name), ST[st(cur)] || '']), devItem(cur), { action: 'diagnose' })">{{ $t('monitor.askAiToInvestigate') }}</el-button>
        </div>
        <div v-if="ex" class="ex hm-card">
          <div class="md small">{{ ex.text }}</div>
          <div class="muted small" style="margin-top:6px">{{ ex.by === 'ai' ? $t('monitor.generatedByLlm') : ex.by === 'demo' ? $t('monitor.demoModeRecordedAiAnswer') : $t('monitor.ruleBasedAnalysisMoreSpecific') }}</div>
        </div>
        <HmDataTable :columns="tagCols" :rows="tagRows" max-height="calc(100vh - 260px)" :ai="(r: any) => ({ kind: 'field', entity: 'equipment', id: cur.id, label: `${cur.code} ${r.label} ${r.text}${r.unit ? ' ' + r.unit : ''}`, data: { [$t('common.equipment')]: cur.code, [$t('common.tag')]: r.label, [$t('monitor.rawName')]: r.name, [$t('common.value')]: r.value, [$t('common.unit')]: r.unit, [$t('common.time')]: r.ts } })"
          :empty-text="$t('monitor.noTagDataForThis')">
          <template #cell-label="{ row }"><span>{{ row.label }}</span><small class="raw hm-mono">{{ row.name }}</small></template>
          <template #cell-text="{ row }"><span class="hm-num">{{ row.text }}</span><small v-if="row.unit" class="hm-unit">{{ row.unit }}</small></template>
        </HmDataTable>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// Live Monitor: status summary (also a filter) + equipment cards (status, power, 8h OEE, key tags with display names and units, alarms) + detail drawer (all tags, request repair, OEE, AI troubleshooting).
// Equipment card v-ai (kind=device): Ctrl+click hands it to AI; right-click offers OEE, Request Repair, Inspection Plan, AI Diagnosis (pages/aiActions.ts).
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { aiOn, askAi } from '../ai';
import { get } from '../api';
import { bareName, codeName, fmtNum, fmtTime } from '../design/format';
import { newRec } from './aiActions';
import { tagDef } from './tagLabels';

const router = useRouter();
const d = ref<any>(null), filter = ref(''), kw = ref(''), line = ref(''), dr = ref(false), cur = ref<any>(null), tags = ref<any[]>([]), ex = ref<any>(null), exb = ref(false);
const ST: Record<string, string> = { running: $t('common.running'), idle: $t('common.idle'), setup: $t('common.changeover'), down: $t('common.down'), offline: $t('common.offline') };
const ET: Record<string, string> = { furnace: $t('energy.inductionFurnace'), treatment: $t('monitor.nodularizationStation'), sand: $t('energy.sandPreparation'), molding: $t('monitor.moldingLine'), cooling: $t('monitor.coolingLine'), shotblast: $t('monitor.shotBlaster'), cnc: $t('monitor.machiningCenter'), deburr: $t('energy.deburring'),
  inspection: $t('monitor.testStation'), warehouse: $t('monitor.asRs'), agv: 'AGV', crew: $t('common.teams'), receiving: $t('monitor.receivingDock'), other: $t('common.other'), plc: 'PLC', gateway: $t('monitor.gateway') };
const SUMS = [{ k: '', label: $t('common.all'), tone: '' }, { k: 'running', label: $t('common.running'), tone: 'run' }, { k: 'idle', label: $t('common.idle'), tone: 'idle' }, { k: 'setup', label: $t('common.changeover'), tone: 'setup' },
  { k: 'down', label: $t('common.down'), tone: 'fault' }, { k: 'offline', label: $t('common.offline'), tone: 'offline' }, { k: 'alarm', label: $t('monitor.alarmActive'), tone: 'fault' }];
const st = (e: any) => (e.status && ST[e.status] ? e.status : 'offline');
const all = computed<any[]>(() => d.value?.items || []);
const count = (k: string) => (k === '' ? all.value.length : k === 'alarm' ? all.value.filter((e) => e.alarm).length : all.value.filter((e) => st(e) === k).length);
const lines = computed(() => [...new Set(all.value.map((e) => e.line__label).filter(Boolean))].sort());
const shown = computed(() => {
  const k = kw.value.trim().toLowerCase();
  return all.value.filter((e) => (filter.value === 'alarm' ? !!e.alarm : !filter.value || st(e) === filter.value) && (!line.value || e.line__label === line.value)
    && (!k || `${e.code} ${e.name}`.toLowerCase().includes(k)));
});
const lineName = (e: any) => (e.line__label && !/^L-?$/.test(String(e.line__label).trim()) ? e.line__label : '');
const oeeCls = (v: number) => (v < 0.6 ? 'bad' : v < 0.8 ? 'warn' : 'ok');
function fmtVal(v: any, digits?: number) { return typeof v === 'number' ? fmtNum(v, digits ?? (Number.isInteger(v) ? 0 : 2)) : v === null || v === undefined || v === '' ? '—' : String(v); }
function tagsOf(e: any) {
  return Object.entries(e.tags || {}).map(([name, v]) => { const t = tagDef(name); return { name, label: t.label, unit: t.unit, text: fmtVal(v, t.digits) }; })
    .filter((t) => t.label !== $t('common.power')).slice(0, 4);
}
function devItem(e: any) {
  const data: Record<string, any> = { [$t('common.equipment')]: codeName(e.code, e.name), [$t('common.type')]: ET[e.etype] || e.etype, [$t('common.line')]: e.line__label, [$t('common.status')]: ST[st(e)], [$t('factoryMap.powerKw')]: e.power, [$t('monitor.oee8h')]: e.oee8h, [$t('common.alarm')]: e.alarm || $t('common.none'), equipment_id: e.id };
  for (const t of tagsOf(e)) data[t.label] = t.text + (t.unit ? ' ' + t.unit : '');
  return { kind: 'device', entity: 'equipment', id: e.id, label: `${codeName(e.code, e.name)} · ${ST[st(e)]}${e.alarm ? $t('monitor.alarm') : ''}`, data };
}
const tagCols = [{ key: 'label', label: $t('common.tag') }, { key: 'text', label: $t('common.value'), align: 'right' as const }, { key: 'time', label: $t('common.time'), width: 120 }];
const tagRows = computed(() => tags.value.map((t) => { const def = tagDef(t.name); return { ...t, label: def.label, unit: def.unit, text: fmtVal(t.value, def.digits), time: fmtTime(t.ts, 'time') }; }));

let t: any;
async function load() { d.value = await get('/api/equipment/monitor', true).catch(() => d.value || { items: [] }); if (cur.value) cur.value = all.value.find((e) => e.id === cur.value.id) || cur.value; }
async function open(e: any) { cur.value = e; ex.value = null; dr.value = true; tags.value = await get('/api/equipment/tags/' + e.id, true).catch(() => []); }
async function explain() { exb.value = true; try { ex.value = await get('/api/ai/explain/' + cur.value.id); } finally { exb.value = false; } }
onMounted(() => { load(); t = setInterval(load, 3000); });
onUnmounted(() => clearInterval(t));
</script>

<style scoped>
.kw { width: calc(200px * var(--hm-scale)); } .ln { width: calc(140px * var(--hm-scale)); }
.sum { display: flex; flex-wrap: wrap; gap: var(--hm-sp-2); margin-bottom: var(--hm-gap); }
.sb { display: inline-flex; align-items: center; gap: 8px; height: calc(34px * var(--hm-scale)); padding: 0 14px 0 12px; border-radius: var(--hm-r-md); border: 1px solid var(--hm-border); background: var(--hm-surface);
  font: inherit; font-size: var(--hm-fs-sm); color: var(--hm-text-2); cursor: pointer; box-shadow: var(--hm-e1); transition: border-color var(--hm-dur-1), background var(--hm-dur-1); }
.sb:hover { border-color: var(--hm-border-strong); }
.sb.on { border-color: var(--hm-primary); background: var(--hm-primary-soft); color: var(--hm-primary-press); }
.sb b { font-size: var(--hm-fs-lg); font-weight: 600; color: var(--hm-text); }
.sb .dot { width: 8px; height: 8px; border-radius: 50%; }
.sb.run .dot { background: var(--hm-run); } .sb.idle .dot { background: var(--hm-idle); } .sb.setup .dot { background: var(--hm-setup); } .sb.fault .dot { background: var(--hm-fault); } .sb.offline .dot { background: var(--hm-offline); }
.sb.fault b { color: var(--hm-fault-ink); }

.eq { padding: var(--hm-sp-3) var(--hm-sp-3) var(--hm-sp-3) calc(var(--hm-sp-3) + 3px); cursor: pointer; display: flex; flex-direction: column; gap: 4px; position: relative; overflow: hidden;
  box-shadow: var(--hm-e1), inset 3px 0 0 var(--acc, var(--hm-offline)); transition: box-shadow var(--hm-dur-2) var(--hm-ease), border-color var(--hm-dur-2) var(--hm-ease); }
.eq:hover { border-color: var(--hm-border-strong); box-shadow: var(--hm-e2), inset 3px 0 0 var(--acc, var(--hm-offline)); }
.s-running { --acc: var(--hm-run); } .s-idle { --acc: var(--hm-idle); } .s-setup { --acc: var(--hm-setup); } .s-down { --acc: var(--hm-fault); } .s-offline { --acc: var(--hm-offline); }
.s-down, .eq.al { border-color: #f3caca; }
.s-offline .vals { opacity: .6; }
.eh { display: flex; align-items: center; gap: 8px; min-width: 0; }
.code { font-weight: 700; font-size: var(--hm-fs-md); color: var(--hm-text); letter-spacing: .01em; }
.nm { flex: 1; min-width: 0; font-size: var(--hm-fs-sm); color: var(--hm-text-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.meta { font-size: var(--hm-fs-xs); color: var(--hm-text-3); }
.vals { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 12px; margin: 6px 0 0; padding-top: 8px; border-top: 1px dashed var(--hm-border); }
.vals > div { min-width: 0; position: relative; }
.vals dt { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.vals dd { margin: 0; font-size: var(--hm-fs-lg); font-weight: 600; color: var(--hm-text); line-height: 1.25; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.vals dd.ok { color: var(--hm-run-ink); } .vals dd.warn { color: var(--hm-idle-ink); } .vals dd.bad { color: var(--hm-fault-ink); }
.vals dd .hm-unit { font-size: var(--hm-fs-2xs); }
.bar { display: block; height: 3px; border-radius: 2px; background: var(--hm-surface-3); margin-top: 3px; overflow: hidden; }
.bar b { display: block; height: 100%; } .bar b.ok { background: var(--hm-run); } .bar b.warn { background: var(--hm-idle); } .bar b.bad { background: var(--hm-fault); }
.alarm { display: flex; align-items: center; gap: 6px; margin: 6px calc(-1 * var(--hm-sp-3)) calc(-1 * var(--hm-sp-3)) calc(-1 * var(--hm-sp-3) - 3px); padding: 6px var(--hm-sp-3) 6px calc(var(--hm-sp-3) + 3px);
  background: var(--hm-fault-soft); color: var(--hm-fault-ink); font-size: var(--hm-fs-xs); }
.alarm span { min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dh { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.dacts { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.ex { padding: 10px 12px; margin-bottom: 12px; background: var(--hm-ai-soft); border-color: var(--hm-primary-soft-2); }
.raw { display: block; font-size: 10.5px; color: var(--hm-text-3); }
</style>
