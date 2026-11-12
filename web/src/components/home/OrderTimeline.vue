<template>
  <div class="otl" :class="{ big }" data-test="orders">
    <div v-if="!orders.length" class="none"><HmEmpty compact :title="$t('common.noWorkOrdersInProgress')" :desc="$t('orderTimeline.releaseAWorkOrderTo')" /></div>
    <template v-else>
      <div class="hd">
        <div class="lh">{{ $t('orderTimeline.workOrdersProgress') }}</div>
        <div class="axis">
          <span v-for="t in ticks" :key="t.p" class="tk" :style="{ left: t.p + '%' }"><i />{{ t.label }}</span>
          <span class="nowl" :style="{ left: nowP + '%' }">{{ $t('orderTimeline.now0', [(now || '').slice(11, 16)]) }}</span>
        </div>
      </div>
      <div class="rows">
        <div v-for="o in rows" :key="o.id" class="row" :class="{ late: o.late, risk: o.risk }" v-ai="o.ai" data-test="order-row" @click="go(o, $event)">
          <div class="lc">
            <div class="l1"><b class="hm-mono">{{ o.code }}</b><span class="pill" :class="o.pillCls">{{ o.pill }}</span></div>
            <div class="l2">{{ o.customer || o.product }}</div>
            <div class="l3"><div class="pb"><i :style="{ width: o.pct * 100 + '%' }" /></div><span class="hm-num">{{ o.qty_done }}/{{ o.qty }}</span><b class="hm-num">{{ fmtPct(o.pct, 0) }}</b></div>
          </div>
          <div class="track">
            <span v-for="t in ticks" :key="t.p" class="grid" :style="{ left: t.p + '%' }" />
            <div class="ops" :style="{ '--n': o.ops.length }">
              <span v-for="op in o.ops" :key="op.seq" class="op" :class="['s-' + op.status, { clipL: op.clipL, clipR: op.clipR }]" :style="{ left: op.l + '%', width: op.w + '%' }" :title="op.title">
                <i :style="{ width: op.done * 100 + '%' }" />
              </span>
            </div>
            <span v-if="o.dueP !== null" class="due" :class="{ bad: o.late || o.risk }" :style="{ left: o.dueP + '%' }" :title="$t('orderTimeline.due0', [o.due])"><em>{{ $t('common.dueDate') }}</em></span>
            <span class="nowv" :style="{ left: nowP + '%' }" />
          </div>
        </div>
      </div>
      <div class="lgd">
        <span><i class="s-done" />{{ $t('common.completed') }}</span><span><i class="s-running" />{{ $t('common.inProgress') }}</span><span><i class="s-waiting" />{{ $t('common.waitingToStart') }}</span><span><i class="dq" />{{ $t('orderTimeline.goodQtyProgress') }}</span>
        <span class="grow" /><span class="muted">{{ $t('orderTimeline.eachThinLineIsAn', [back, ahead]) }}</span>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// Today's work order mini-Gantt: one row per work order, progress on the left, one thin line per operation on the right (planned start/end, color = status, dark part = good qty progress), vertical lines = now / due date.
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import { fmtPct } from '../../design/format';
import { toDate } from './usePoll';

const props = withDefaults(defineProps<{ orders?: any[]; now?: string; back?: number; ahead?: number; big?: boolean; max?: number }>(),
  { orders: () => [], now: '', back: 8, ahead: 16, big: false, max: 8 });
const router = useRouter();
const nowD = computed(() => toDate(props.now) || new Date());
const t0 = computed(() => nowD.value.getTime() - props.back * 3600e3);
const t1 = computed(() => nowD.value.getTime() + props.ahead * 3600e3);
const P = (t: any) => { const d = toDate(t); return d ? ((d.getTime() - t0.value) / (t1.value - t0.value)) * 100 : null; };
const nowP = computed(() => P(props.now) ?? 0);
const ticks = computed(() => {
  const out: { p: number; label: string }[] = [];
  const step = (props.back + props.ahead) > 20 ? 4 : 2;
  const d = new Date(t0.value); d.setMinutes(0, 0, 0); d.setHours(Math.ceil(d.getHours() / step) * step);
  for (; d.getTime() <= t1.value; d.setHours(d.getHours() + step)) {
    const p = ((d.getTime() - t0.value) / (t1.value - t0.value)) * 100;
    if (p < 2 || p > 98) continue;
    out.push({ p, label: d.getHours() === 0 ? `${d.getMonth() + 1}-${d.getDate()}` : `${String(d.getHours()).padStart(2, '0')}:00` });
  }
  return out;
});
const ST: Record<string, string> = { waiting: $t('common.waitingToStart'), ready: $t('common.readyToStart'), running: $t('common.inProgress'), done: $t('common.done') };
const rows = computed(() => props.orders.slice(0, props.max).map((o: any) => {
  const pct = o.qty ? Math.min(1, (o.qty_done || 0) / o.qty) : 0;
  const ops = (o.ops || []).map((op: any) => {
    const a = P(op.plan_start), b = P(op.plan_end);
    if (a === null || b === null) return null;
    const l = Math.min(99.4, Math.max(0, a)), r = Math.max(l + 0.6, Math.min(100, b));     // Operations outside the window show a short stub at the edge
    const st = op.status === 'ready' ? 'waiting' : op.status;
    return { seq: op.seq, l, w: Math.max(0.6, r - l), clipL: a < 0, clipR: b > 100, status: r <= 0 || l >= 100 ? st + ' off' : st,
      done: op.qty_plan ? Math.min(1, (op.qty_good || 0) / op.qty_plan) : 0,
      title: $t('orderTimeline.n01Planned23', [op.process || 'OP' + op.seq, ST[op.status] || op.status, String(op.plan_start).slice(5, 16), String(op.plan_end).slice(5, 16), op.qty_good || 0, op.qty_plan || '—']) + (op.qty_ng ? $t('orderTimeline.defective0', [op.qty_ng]) : '') };
  }).filter(Boolean);
  const dueP = P(o.due);
  const pill = o.late ? $t('common.overdue') : o.risk ? $t('orderTimeline.atRiskOfDelay') : o.status === 'in_progress' ? $t('common.inProduction') : $t('common.released');
  return { ...o, pct, ops, dueP: dueP !== null && dueP >= 0 && dueP <= 100 ? dueP : null, pill, pillCls: o.late ? 'bad' : o.risk ? 'warn' : o.status === 'in_progress' ? 'run' : '',
    ai: { kind: 'record', entity: 'work_order', id: o.id, label: $t('orderTimeline.workOrder012', [o.code, o.qty_done, o.qty, fmtPct(pct, 0), pill]),
      data: { [$t('common.workOrder')]: o.code, [$t('common.product')]: o.product, [$t('common.customer')]: o.customer, [$t('common.plan')]: o.qty, [$t('common.done')]: o.qty_done, [$t('common.scrapped')]: o.qty_scrap, [$t('orderTimeline.completion')]: fmtPct(pct, 1), [$t('common.dueDate')]: o.due, [$t('orderTimeline.estCompletion')]: o.eta, [$t('common.status')]: pill,
        [$t('common.operation')]: (o.ops || []).map((x: any) => `${x.process} ${ST[x.status] || x.status} ${x.qty_good || 0}/${x.qty_plan || ''}`).join($t('common.k764')) } } };
}));
function go(o: any, e: MouseEvent) { if (!e.ctrlKey && !e.metaKey) router.push('/e/work_order?id=' + o.id); }
</script>

<style scoped>
.otl { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.hd, .row { display: grid; grid-template-columns: 210px minmax(0, 1fr); gap: 12px; }
.lh { font-size: 11px; color: var(--hm-text-3); align-self: end; }
.axis { position: relative; height: 18px; border-bottom: 1px solid var(--hm-border); }
.tk { position: absolute; bottom: 2px; transform: translateX(-50%); font-size: 10.5px; color: var(--hm-text-3); font-family: var(--hm-font-num); white-space: nowrap; }
.nowl { position: absolute; bottom: 1px; transform: translateX(-50%); font-size: 10.5px; color: #fff; background: var(--hm-cyan-ink); padding: 0 6px; border-radius: var(--hm-r-pill); font-family: var(--hm-font-num); white-space: nowrap; z-index: 2; }
.rows { display: flex; flex-direction: column; }
.row { padding: 6px 0; border-bottom: 1px dashed var(--hm-border); cursor: pointer; border-radius: var(--hm-r-sm); }
.row:hover { background: var(--hm-surface-2); }
.lc { min-width: 0; padding-left: 2px; }
.l1 { display: flex; align-items: center; gap: 6px; } .l1 b { font-size: 12.5px; }
.pill { font-size: 10.5px; padding: 0 6px; border-radius: var(--hm-r-pill); background: var(--hm-surface-3); color: var(--hm-text-2); line-height: 17px; white-space: nowrap; }
.pill.run { background: var(--hm-run-soft); color: var(--hm-run-ink); } .pill.warn { background: var(--hm-idle-soft); color: var(--hm-idle-ink); } .pill.bad { background: var(--hm-fault-soft); color: var(--hm-fault-ink); font-weight: 700; }
.l2 { font-size: 11px; color: var(--hm-text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.l3 { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--hm-text-3); }
.l3 b { color: var(--hm-text); font-size: 12px; min-width: 30px; text-align: right; }
.pb { flex: 1; height: 5px; border-radius: 3px; background: var(--hm-surface-3); overflow: hidden; } .pb i { display: block; height: 100%; background: linear-gradient(90deg, var(--hm-primary), var(--hm-cyan)); border-radius: 3px; transition: width var(--hm-dur-3) var(--hm-ease); }
.late .pb i { background: var(--hm-fault); }
.track { position: relative; min-height: 40px; }
.grid { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--hm-border); opacity: .6; }
.ops { position: absolute; inset: 2px 0; display: flex; flex-direction: column; justify-content: center; gap: 1.5px; }
.op { position: relative; display: block; height: 3px; border-radius: 2px; background: #d7dee8; overflow: hidden; flex: none; }
.op i { display: block; height: 100%; background: rgba(15, 27, 45, .35); }
.op.s-running { background: rgba(11, 165, 196, .35); } .op.s-running i { background: var(--hm-cyan); }
.op.s-done { background: rgba(21, 163, 74, .45); } .op.s-done i { background: var(--hm-run); }
.op.s-waiting { background: #d7dee8; } .op.s-waiting i { background: #9fb0c4; }
.op.clipL { border-top-left-radius: 0; border-bottom-left-radius: 0; } .op.clipR { border-top-right-radius: 0; border-bottom-right-radius: 0; }
.op.off { opacity: .35; }
.due { position: absolute; top: -2px; bottom: -2px; width: 0; border-left: 1.5px dashed var(--hm-text-3); }
.due em { position: absolute; top: -1px; left: 3px; font-style: normal; font-size: 9.5px; color: var(--hm-text-3); background: var(--hm-surface); padding: 0 2px; line-height: 12px; }
.due.bad { border-color: var(--hm-fault); } .due.bad em { color: var(--hm-fault-ink); font-weight: 700; }
.nowv { position: absolute; top: -7px; bottom: -7px; width: 2px; margin-left: -1px; background: var(--hm-cyan); box-shadow: 0 0 6px rgba(11, 165, 196, .6); z-index: 1; }
.lgd { display: flex; align-items: center; gap: 12px; font-size: 11px; color: var(--hm-text-3); padding-top: 4px; flex-wrap: wrap; }
.lgd span { display: inline-flex; align-items: center; gap: 4px; }
.lgd i { width: 14px; height: 3px; border-radius: 2px; display: inline-block; } .lgd .s-done { background: var(--hm-run); } .lgd .s-running { background: var(--hm-cyan); } .lgd .s-waiting { background: #c3cfdc; }
.lgd .dq { background: linear-gradient(90deg, rgba(15, 27, 45, .45) 50%, #d7dee8 50%); }
.muted { color: var(--hm-text-3); }
.big .hd, .big .row { grid-template-columns: 300px minmax(0, 1fr); }
.big .l1 b { font-size: 18px; } .big .l2, .big .l3 { font-size: 14px; } .big .l3 b { font-size: 16px; } .big .pill { font-size: 13px; line-height: 22px; }
.big .track { min-height: 54px; } .big .op { height: 4px; } .big .tk, .big .nowl { font-size: 13px; } .big .axis { height: 22px; } .big .lgd { display: none; }
@media (max-width: 760px) { .hd, .row { grid-template-columns: 150px minmax(0, 1fr); } }
</style>
