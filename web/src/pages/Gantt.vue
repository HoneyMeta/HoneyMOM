<template>
  <div class="page gantt-page">
    <HmPageHead :title="$t('common.schedulingProgress')" :sub="$t('gantt.finiteCapacitySchedulingByPriority')">
      <el-button @click="router.push('/e/schedule_proposal')">{{ $t('gantt.whatIfChangeProposals') }}<el-badge v-if="d?.pending_proposals" :value="d.pending_proposals" class="bdg" /></el-button>
      <el-button @click="refreshKit" :loading="kitBusy">{{ $t('gantt.refreshKitting') }}</el-button>
      <el-button type="primary" :loading="busy" data-test="gantt-resched" @click="reschedule">{{ $t('gantt.reschedule') }}</el-button>
    </HmPageHead>

    <div class="cons" data-test="gantt-constraints">
      <button v-for="c in consItems" :key="c.k" class="cn" :class="c.tone" @click="router.push(c.to)" v-ai="{ kind: 'kpi', label: `${c.l} ${c.v}`, data: { [$t('gantt.constraints')]: c.l, [$t('common.quantity')]: c.v, [$t('common.description')]: c.s } }">
        <span class="l">{{ c.l }}</span><b class="hm-num">{{ c.v }}</b><small>{{ c.s }}</small>
      </button>
    </div>
    <el-alert v-if="d?.late.length" type="error" :closable="false" show-icon class="mt-s" :title="$t('gantt.expectedLate0', [d.late.join($t('common.k42'))])" />

    <HmSkeleton v-if="!d" variant="table" :rows="10" />
    <HmCard v-else flush class="mt-s gcard">
      <div class="legend">
        <span v-for="(v, k) in LEG" :key="k" class="lg"><i class="mk" :class="'sev-' + v[1]">{{ v[2] }}</i>{{ v[0] }}</span>
        <span class="lg"><i class="sw setup" />{{ $t('common.changeover') }}</span><span class="lg"><i class="sw down" />{{ $t('gantt.equipmentDowntime') }}</span>
      </div>
      <div class="gantt" ref="box">
        <div class="axis"><div class="lab">{{ $t('gantt.workOrderOperation') }}</div><div class="track">
          <span v-for="t in ticks" :key="t.x" class="tick" :style="{ left: t.x + '%' }">{{ t.label }}</span>
          <span class="now" :style="{ left: nowX + '%' }">{{ $t('common.current') }}</span></div></div>
        <template v-for="o in d.orders" :key="o.id">
          <div class="row wo" v-ai="{ kind: 'record', entity: 'work_order', id: o.id, label: $t('gantt.workOrder012', [o.code, o.qty_done || 0, o.qty]), data: { [$t('common.workOrder')]: o.code, [$t('common.customer')]: o.customer, [$t('common.plan')]: o.qty, [$t('common.complete')]: o.qty_done, [$t('common.dueDate')]: o.due, [$t('gantt.priority')]: o.priority, [$t('gantt.conflicts')]: woConf(o.id).map((x: any) => x.text) } }">
            <div class="lab"><b class="click hm-mono" @click="router.push('/e/work_order?id=' + o.id)">{{ o.code }}</b>
              <HmTag :tone="o.status === 'in_progress' ? 'primary' : 'neutral'">{{ ST[o.status] }}</HmTag>
              <span class="muted small"> {{ o.product__label?.split(' ')[0] }} · {{ o.qty_done || 0 }}/{{ o.qty }}</span>
              <i v-if="woConf(o.id).length" class="mk" :class="'sev-' + worst(woConf(o.id))" :title="woConf(o.id).map((x: any) => x.text).join('\n')">{{ woConf(o.id).length }}</i>
            </div>
            <div class="track"><span v-if="o.due" class="due" :style="{ left: x(o.due) + '%' }" :title="$t('gantt.due') + o.due">{{ $t('gantt.due2') }}</span></div>
          </div>
          <div v-for="op in ops(o.id)" :key="op.id" class="row" :data-test="'gantt-op-' + op.id">
            <div class="lab op">{{ op.seq }} {{ op.process__label?.split(' ').slice(1).join(' ') }} <span class="muted small">{{ op.machines || op.station_group }}</span></div>
            <div class="track">
              <span v-for="b in downFor(op)" :key="b.id" class="downb" :style="band(b)" :title="$t('gantt.n0Down123', [b.equipment__label, b.start, b.end, b.reason || ''])" />
              <div v-if="op.plan_start" class="bar" :class="['b-' + op.status, { conf: (op.conflicts || []).length }]" :style="bar(op)" @click="sel = op"
                v-ai="{ kind: 'row', entity: 'wo_op', id: op.id, label: `${o.code} ${op.seq} ${op.process__label} ${op.plan_start}–${op.plan_end}`, data: { [$t('gantt.plannedStart')]: op.plan_start, [$t('gantt.plannedEnd')]: op.plan_end, [$t('common.equipment')]: op.machines, [$t('common.done')]: `${op.qty_good}/${op.qty_plan}`, [$t('gantt.conflicts')]: (op.conflicts || []).map((x: any) => x.text) } }">
                <i v-if="op.setup_min" class="setup" :style="{ width: setupW(op) + '%' }" />
                <div class="fill" :style="{ width: Math.min(100, (op.qty_good || 0) / (op.qty_plan || 1) * 100) + '%' }" />
              </div>
              <span v-if="op.plan_start && (op.conflicts || []).length" class="marks" :style="{ left: x(op.plan_start) + '%' }" data-test="gantt-mark">
                <i v-for="t in types(op)" :key="t" class="mk" :class="'sev-' + (LEG[t]?.[1] || 'info')" :title="(op.conflicts || []).filter((c: any) => c.type === t).map((c: any) => c.text).join('\n')">{{ LEG[t]?.[2] || '!' }}</i>
              </span>
              <span v-if="op.plan_start" class="blab hm-num" :style="{ left: (x(op.plan_end) + 0.4) + '%' }">{{ op.qty_good || 0 }}/{{ op.qty_plan }}</span>
            </div>
          </div>
        </template>
        <HmEmpty v-if="!d.orders.length" :title="$t('gantt.noReleasedWorkOrders')" :desc="$t('gantt.createAndReleaseOneUnder')"><el-button type="primary" @click="router.push('/e/work_order')">{{ $t('common.workOrders') }}</el-button></HmEmpty>
      </div>
    </HmCard>

    <el-drawer :model-value="!!sel" @update:model-value="sel = null" :title="sel ? `${sel.work_order__label} · ${sel.seq} ${sel.process__label}` : ''" size="min(520px, 96vw)">
      <template v-if="sel">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item :label="$t('common.plan')">{{ sel.plan_start }} → {{ sel.plan_end }}</el-descriptions-item>
          <el-descriptions-item :label="$t('gantt.scheduledMachine')">{{ sel.machines || sel.station_group }}</el-descriptions-item>
          <el-descriptions-item :label="$t('common.changeover')">{{ sel.setup_min ? sel.setup_min + $t('common.min') : '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('common.done')">{{ $t('gantt.n01Defective2', [sel.qty_good, sel.qty_plan, sel.qty_ng]) }}</el-descriptions-item>
        </el-descriptions>
        <h4>{{ $t('gantt.conflictsConstraints') }}</h4>
        <div v-for="(c, i) in sel.conflicts || []" :key="i" class="cf" :class="'sev-' + c.severity"><b>{{ c.label }}</b>{{ c.text }}</div>
        <p v-if="!(sel.conflicts || []).length" class="muted">{{ $t('gantt.noConflicts') }}</p>
      </template>
    </el-drawer>

    <el-dialog v-model="imp.open" :title="$t('gantt.rescheduleAffectedWorkOrders')" width="min(980px, 96vw)" data-test="impact-dialog">
      <p class="muted small">{{ $t('gantt.comparesEstimatedCompletionBeforeAnd') }}</p>
      <ImpactTable :rows="imp.rows" />
      <template #footer><el-button type="primary" @click="imp.open = false">{{ $t('common.gotIt') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { get, post } from '../api';
import ImpactTable from '../components/biz/ImpactTable.vue';

const router = useRouter();
const d = ref<any>(null), busy = ref(false), kitBusy = ref(false), sel = ref<any>(null);
const imp = ref<any>({ open: false, rows: [] });
const ST: Record<string, string> = { released: $t('common.released'), in_progress: $t('common.inProduction'), completed: $t('gantt.completed') };
// Conflict types: [label, severity, marker]
const LEG: Record<string, [string, string, string]> = {
  shortage: [$t('gantt.materialShortage'), 'error', $t('gantt.m')], machine_down: [$t('gantt.equipmentDowntime'), 'warn', $t('gantt.d')], no_skill: [$t('gantt.noQualifiedOperator'), 'error', $t('gantt.p')], mould_busy: [$t('gantt.moldInUse'), 'warn', $t('gantt.t')],
  mould_maint: [$t('gantt.moldMaintenance'), 'warn', $t('gantt.pm')], mould_life: [$t('gantt.moldLife'), 'warn', $t('gantt.l')], no_mould: [$t('gantt.noMoldAvailable'), 'error', $t('common.none')], changeover: [$t('common.changeover'), 'info', $t('gantt.c')],
};
const T = (s: string) => new Date(s.replace(' ', 'T')).getTime();
const range = computed(() => {
  const ts: number[] = [T(d.value.now)];
  for (const o of d.value.ops) { if (o.plan_start) ts.push(T(o.plan_start)); if (o.plan_end) ts.push(T(o.plan_end)); }
  for (const o of d.value.orders) if (o.due) ts.push(T(o.due));
  const a = Math.min(...ts), b = Math.max(...ts);
  return [a - (b - a) * 0.02, b + (b - a) * 0.04 || a + 3600e3];
});
const x = (s: string) => ((T(s) - range.value[0]) / (range.value[1] - range.value[0])) * 100;
const nowX = computed(() => x(d.value.now));
const ticks = computed(() => {
  const [a, b] = range.value, out: any[] = [], span = b - a, step = span > 5 * 864e5 ? 864e5 : span > 2 * 864e5 ? 12 * 3600e3 : span > 864e5 ? 6 * 3600e3 : 3 * 3600e3;
  for (let t = Math.ceil(a / step) * step; t < b; t += step) { const dt = new Date(t); out.push({ x: ((t - a) / span) * 100, label: `${dt.getMonth() + 1}/${dt.getDate()} ${String(dt.getHours()).padStart(2, '0')}:00` }); }
  return out;
});
const ops = (id: number) => d.value.ops.filter((o: any) => o.work_order === id);
const bar = (op: any) => { const l = x(op.plan_start), r = x(op.plan_end); return { left: l + '%', width: Math.max(0.6, r - l) + '%' }; };
const setupW = (op: any) => { const span = T(op.plan_end) - T(op.plan_start); return span > 0 ? Math.min(100, (op.setup_min * 60e3) / span * 100) : 0; };
const types = (op: any) => [...new Set((op.conflicts || []).map((c: any) => c.type))] as string[];
const woConf = (id: number) => ops(id).flatMap((o: any) => o.conflicts || []).filter((c: any, i: number, a: any[]) => a.findIndex((y) => y.text === c.text) === i);
const worst = (cs: any[]) => (cs.some((c) => c.severity === 'error') ? 'error' : cs.some((c) => c.severity === 'warn') ? 'warn' : 'info');
const downFor = (op: any) => {
  const ms = new Set(String(op.machines || '').split(',').filter(Boolean));
  return (d.value.downtime || []).filter((b: any) => ms.has(String(b.equipment__label || '').split(' ')[0]) && T(b.end) > range.value[0] && T(b.start) < range.value[1]);
};
const band = (b: any) => { const l = Math.max(0, x(b.start)), r = Math.min(100, x(b.end)); return { left: l + '%', width: Math.max(0.3, r - l) + '%' }; };
const consItems = computed(() => {
  if (!d.value) return [];
  const sh = d.value.shifts || [], kit = d.value.kitting || [], tl = d.value.tooling || [], m = d.value.master || {};
  const short = kit.filter((k: any) => (k.qty_ready || 0) < (k.qty_need || 0));
  return [
    { k: 'shift', l: $t('common.shift'), v: `${sh.filter((s: any) => s.active !== false).length}/${sh.length}`, s: $t('gantt.activeAll'), to: '/e/shift', tone: '' },
    { k: 'down', l: $t('nav.plannedDowntime'), v: (d.value.downtime || []).length, s: $t('gantt.inProgressOrUpcoming'), to: '/e/downtime_plan', tone: (d.value.downtime || []).length ? 'warn' : '' },
    { k: 'kit', l: $t('nav.materialKitting'), v: short.length ? $t('gantt.short0', [short.length]) : $t('gantt.kitted'), s: $t('common.n0Rows', [kit.length]), to: '/e/wo_kitting', tone: short.length ? 'bad' : 'ok' },
    { k: 'skill', l: $t('nav.skillsMatrix'), v: m.skill || 0, s: $t('gantt.operatorOperation'), to: '/e/skill', tone: m.skill ? '' : 'warn' },
    { k: 'tool', l: $t('common.moldsFixtures'), v: tl.length, s: $t('gantt.inMaintenance0', [tl.filter((t: any) => t.status === 'maint').length]), to: '/e/tooling', tone: tl.some((t: any) => t.status === 'maint') ? 'warn' : '' },
    { k: 'co', l: $t('nav.changeoverTimes'), v: m.changeover || 0, s: $t('gantt.rules'), to: '/e/changeover', tone: '' },
  ];
});
async function load() { d.value = await get('/api/production/gantt2'); }
async function reschedule() {
  busy.value = true;
  try {
    const r = await post('/api/production/schedule', {});
    ElMessage.success($t('gantt.rescheduled0WorkOrdersAffected', [r.affected]));
    imp.value = { open: true, rows: r.impact };
    await load();
  } finally { busy.value = false; }
}
async function refreshKit() { kitBusy.value = true; try { const r = await post('/api/production/kitting/refresh', {}); ElMessage.success($t('gantt.refreshed0LinesFromBom', [r.lines])); await load(); } finally { kitBusy.value = false; } }
onMounted(load);
</script>

<style scoped>
.cons { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; }
@media (max-width: 1100px) { .cons { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.cn { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 8px 12px; border: 1px solid var(--hm-border); background: var(--hm-surface); border-radius: var(--hm-r-md);
  cursor: pointer; font: inherit; text-align: left; box-shadow: var(--hm-e1); }
.cn:hover { border-color: var(--hm-primary); } .cn .l { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); } .cn b { font-size: var(--hm-fs-lg); color: var(--hm-text); }
.cn small { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); }
.cn.bad { border-color: var(--hm-fault); background: var(--hm-fault-soft); } .cn.bad .l, .cn.bad small { color: var(--hm-text-2); }   /* text-3 on red background is below 3:1 */ .cn.warn { border-color: var(--hm-idle); } .cn.ok b { color: var(--hm-run-ink); }
.mt-s { margin-top: 10px; } .bdg { margin-left: 6px; }
.legend { display: flex; gap: 12px; flex-wrap: wrap; padding: 8px 12px; border-bottom: 1px solid var(--hm-border); font-size: var(--hm-fs-2xs); color: var(--hm-text-2); }
.lg { display: inline-flex; align-items: center; gap: 4px; }
.gantt { overflow-x: auto; padding: 0 12px 12px; }
.row, .axis { display: flex; align-items: center; min-width: 980px; } .row { height: 30px; border-bottom: 1px solid var(--hm-hairline); }
.row.wo { background: var(--hm-surface-2); height: 34px; }
.lab { width: 300px; flex: none; font-size: var(--hm-fs-xs); padding-right: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: flex; align-items: center; gap: 6px; }
.lab.op { padding-left: 18px; color: var(--hm-text-2); } .track { position: relative; flex: 1; height: 100%; }
.axis { height: 28px; border-bottom: 1px solid var(--hm-border); font-size: var(--hm-fs-2xs); color: var(--hm-text-3); } .tick { position: absolute; top: 7px; transform: translateX(-50%); white-space: nowrap; }
.now { position: absolute; top: 0; color: var(--hm-fault); font-weight: 600; transform: translateX(-50%); }
.bar { position: absolute; top: 6px; height: 18px; border-radius: 4px; background: var(--hm-primary-soft-2); overflow: hidden; cursor: pointer; }
.bar.conf { box-shadow: 0 0 0 1px var(--hm-idle); }
.fill { position: absolute; inset: 0 auto 0 0; background: var(--hm-primary); opacity: .55; }
.b-done .fill { background: var(--hm-run); } .b-running { box-shadow: 0 0 0 1px var(--hm-primary); }
.setup { position: absolute; inset: 0 auto 0 0; z-index: 1; background: repeating-linear-gradient(135deg, var(--hm-setup-soft) 0 4px, var(--hm-setup) 4px 6px); opacity: .8; }
.downb { position: absolute; top: 2px; bottom: 2px; background: repeating-linear-gradient(45deg, var(--hm-fault-soft) 0 5px, rgba(220, 43, 43, .35) 5px 7px); border-radius: 2px; }
.blab { position: absolute; top: 8px; font-size: var(--hm-fs-2xs); color: var(--hm-text-2); white-space: nowrap; }
.marks { position: absolute; top: -1px; transform: translateX(-100%); display: flex; gap: 2px; padding-right: 3px; }
.mk { display: inline-flex; align-items: center; justify-content: center; min-width: 16px; height: 16px; border-radius: 4px; font-style: normal; font-size: 10px; font-weight: 600; color: #fff; padding: 0 2px; }
.sev-error.mk { background: var(--hm-fault); } .sev-warn.mk { background: var(--hm-idle-ink); } .sev-info.mk { background: var(--hm-setup); }
.sw { display: inline-block; width: 18px; height: 10px; border-radius: 2px; } .sw.setup { background: repeating-linear-gradient(135deg, var(--hm-setup-soft) 0 4px, var(--hm-setup) 4px 6px); }
.sw.down { background: repeating-linear-gradient(45deg, var(--hm-fault-soft) 0 5px, rgba(220, 43, 43, .35) 5px 7px); }
.due { position: absolute; top: 9px; color: var(--hm-fault); font-size: var(--hm-fs-2xs); white-space: nowrap; transform: translateX(-4px); }
.click { cursor: pointer; color: var(--hm-primary); }
.cf { padding: 8px 10px; border-radius: var(--hm-r-sm); margin-bottom: 6px; font-size: var(--hm-fs-xs); border-left: 3px solid; background: var(--hm-surface-2); }
.cf b { margin-right: 8px; } .cf.sev-error { border-color: var(--hm-fault); } .cf.sev-warn { border-color: var(--hm-idle); } .cf.sev-info { border-color: var(--hm-setup); }
.muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-2xs); }
</style>
