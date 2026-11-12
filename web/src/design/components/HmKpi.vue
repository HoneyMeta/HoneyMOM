<template>
  <div class="hm-kpi" :class="[status, { link: !!to, compact }]" v-ai="aiItem" :role="to ? 'link' : undefined" @click="go">
    <div class="kh">
      <span class="kl">{{ label }}</span>
      <span v-if="badge" class="kb">{{ badge }}</span>
      <slot name="icon" />
    </div>
    <div class="kv">
      <HmSkeleton v-if="loading" variant="line" width="60%" :height="26" />
      <template v-else><HmNumber class="v" :value="value" :format="format" :digits="digits" :tick="tick" /><span v-if="unit" class="hm-unit u">{{ unit }}</span></template>
    </div>
    <div class="kf">
      <span v-if="d" class="delta" :class="d.cls" :title="deltaLabel">{{ d.arrow }} {{ d.text }}</span>
      <span class="hint"><slot name="hint">{{ hint }}</slot></span>
      <svg v-if="spark && spark.length > 1" class="spark" viewBox="0 0 64 20" preserveAspectRatio="none" aria-hidden="true">
        <path :d="sparkPath" fill="none" stroke="currentColor" stroke-width="1.4" vector-effect="non-scaling-stroke" />
      </svg>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// KPI tile: label + big number (tabular digits, animated) + unit + delta + sparkline. Declares v-ai (kind=kpi) by default; Ctrl+click hands it to AI.
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import { fmtBy, fmtDelta } from '../format';
import HmNumber from './HmNumber.vue';
import HmSkeleton from './HmSkeleton.vue';

const props = withDefaults(defineProps<{
  label: string; value?: number | string | null; unit?: string; format?: string; digits?: number;
  status?: '' | 'ok' | 'warn' | 'bad'; delta?: number | null; deltaPct?: boolean; deltaLabel?: string; goodWhen?: 'up' | 'down';
  hint?: string; badge?: string; spark?: number[]; loading?: boolean; to?: string; tick?: boolean; compact?: boolean;
  /** false disables AI selection; object: merged into the AI context (e.g. { id, entity, data: { range: 'Today' } }) */
  ai?: boolean | Record<string, any>;
}>(), { format: 'num', status: '', goodWhen: 'up', tick: true, ai: true, deltaPct: false });

const router = useRouter();
const go = () => props.to && router.push(props.to);
const d = computed(() => {
  if (props.delta === null || props.delta === undefined) return null;
  const f = fmtDelta(props.delta, !props.deltaPct && (props.format === 'int' || Number.isInteger(Number(props.delta))) ? 0 : 1, props.deltaPct);     // Deltas of integer metrics like piece counts have no decimals (+354, not +354.0)
  const good = f.dir === 'flat' ? null : (f.dir === 'up') === (props.goodWhen === 'up');
  return { text: f.text, arrow: f.dir === 'up' ? '▲' : f.dir === 'down' ? '▼' : '■', cls: good === null ? 'flat' : good ? 'good' : 'bad' };
});
const sparkPath = computed(() => {
  const s = (props.spark || []).map(Number).filter((x) => !isNaN(x));
  if (s.length < 2) return '';
  const lo = Math.min(...s), hi = Math.max(...s), rg = hi - lo || 1;
  return s.map((v, i) => `${i ? 'L' : 'M'}${(i / (s.length - 1) * 64).toFixed(1)},${(18 - (v - lo) / rg * 16).toFixed(1)}`).join(' ');
});
const aiItem = computed(() => {
  if (props.ai === false) return null;
  const extra: Record<string, any> = typeof props.ai === 'object' ? props.ai : {};
  const shown = fmtBy(props.value, props.format, props.digits) + (props.unit ? ' ' + props.unit : '');
  return { kind: 'kpi', label: `${props.label} ${shown}`, ...extra,
    data: { [$t('common.kpi')]: props.label, [$t('common.value')]: props.value, [$t('chart.show')]: shown, [$t('common.unit')]: props.unit, [$t('impactTable.change')]: props.delta, [$t('common.description')]: props.hint, ...(extra.data || {}) } };
});
</script>

<style scoped>
.hm-kpi { position: relative; background: var(--hm-surface); border: 1px solid var(--hm-border); border-radius: var(--hm-r-lg); padding: 11px 14px 10px; box-shadow: var(--hm-e1);
  display: flex; flex-direction: column; gap: 2px; min-width: 0; overflow: hidden; transition: border-color var(--hm-dur-2) var(--hm-ease), box-shadow var(--hm-dur-2) var(--hm-ease), transform var(--hm-dur-2) var(--hm-ease); }
.hm-kpi::after { content: ''; position: absolute; inset: 0 0 auto 0; height: 2px; background: transparent; }
.hm-kpi.ok::after { background: var(--hm-run); } .hm-kpi.warn::after { background: var(--hm-idle); } .hm-kpi.bad::after { background: var(--hm-fault); }
.hm-kpi:hover { border-color: var(--hm-border-strong); box-shadow: var(--hm-e2); }
.hm-kpi.link { cursor: pointer; } .hm-kpi.link:hover { transform: translateY(-1px); }
.kh { display: flex; align-items: center; gap: 6px; min-height: 18px; }
.kl { font-size: var(--hm-fs-xs); color: var(--hm-text-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; letter-spacing: .02em; }
.kb { font-size: 10px; color: var(--hm-text-3); border: 1px solid var(--hm-border); border-radius: var(--hm-r-pill); padding: 0 6px; line-height: 16px; }
.kv { display: flex; align-items: baseline; gap: 2px; min-height: 36px; }
.v { font-size: var(--hm-fs-3xl); font-weight: 600; line-height: 1.1; color: var(--hm-text); letter-spacing: -.01em; }
.compact .v { font-size: var(--hm-fs-2xl); }
.ok .v { color: var(--hm-run-ink); } .warn .v { color: var(--hm-idle-ink); } .bad .v { color: var(--hm-fault-ink); }
.u { font-size: var(--hm-fs-sm); }
.kf { display: flex; align-items: center; gap: 8px; min-height: 18px; font-size: var(--hm-fs-xs); color: var(--hm-text-3); }
.hint { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.delta { font-family: var(--hm-font-num); font-variant-numeric: tabular-nums; font-weight: 600; font-size: 11.5px; padding: 0 5px; border-radius: var(--hm-r-xs); }
.delta.good { color: var(--hm-run-ink); background: var(--hm-run-soft); } .delta.bad { color: var(--hm-fault-ink); background: var(--hm-fault-soft); } .delta.flat { color: var(--hm-text-3); background: var(--hm-surface-3); }
.spark { width: 64px; height: 20px; color: var(--hm-cyan); flex: none; }
</style>
