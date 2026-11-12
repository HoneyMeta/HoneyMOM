<template>
  <div class="aib" :class="{ compact }" @click="onClick">
    <template v-for="(b, i) in blocks" :key="b.id || i">
      <div v-if="b.type === 'text'" class="mdx" v-html="mdWithRefs(b.md, refs)" />

      <div v-else-if="b.type === 'kpis'" class="blk kp" data-test="ai-block-kpis">
        <div v-if="b.title || pinnable" class="bh"><span class="bt">{{ b.title }}</span><span class="grow" /><Unv :u="b.unverified" /><PinBtn v-if="pinnable" :b="b" /></div>
        <div class="kg" :style="{ '--n': Math.min(b.items.length, compact ? 2 : 4) }">
          <HmKpi v-for="(k, j) in b.items" :key="j" compact :label="k.label" :value="k.value" :unit="k.unit" :format="k.format || 'num'" :tick="false"
            :delta="k.delta ?? null" :delta-pct="k.deltaPct" :good-when="k.good || 'up'" :hint="k.hint" :ai="{ data: { [$t('common.value')]: k.value, [$t('common.source')]: $t('aiBlocks.aiAnswer') } }" />
        </div>
      </div>

      <div v-else-if="b.type === 'chart'" class="blk ch" data-test="ai-block-chart">
        <div class="bh"><el-icon class="bi"><TrendCharts /></el-icon><span class="bt">{{ b.title || $t('common.chart') }}</span><span v-if="b.unit" class="u">{{ b.unit }}</span>
          <span class="grow" /><Unv :u="b.unverified" /><PinBtn v-if="pinnable" :b="b" /></div>
        <Chart :option="chartOption(b)" :height="chartHeight(b) * (compact ? 0.92 : 1)" :ai="b.title || $t('common.chart')" />
      </div>

      <div v-else-if="b.type === 'table'" class="blk tb" data-test="ai-block-table">
        <div class="bh"><el-icon class="bi"><Grid /></el-icon><span class="bt">{{ b.title || $t('common.table') }}</span><span class="u">{{ $t('common.n0Rows', [b.rows.length]) }}</span>
          <span class="grow" /><Unv :u="b.unverified" /><PinBtn v-if="pinnable" :b="b" /></div>
        <div class="tw"><table>
          <thead><tr><th v-for="c in b.columns" :key="c.key" :class="{ n: c.num }">{{ c.label }}</th></tr></thead>
          <tbody><tr v-for="(r, j) in b.rows.slice(0, showAll[b.id || i] ? 200 : 15)" :key="j">
            <td v-for="c in b.columns" :key="c.key" :class="{ n: c.num || typeof r[c.key] === 'number' }">
              <a v-if="refOf(r[c.key])" class="ai-ref" :data-ref-link="refLink(refOf(r[c.key])!)"><i />{{ r[c.key] }}</a><template v-else>{{ fmtCell(r[c.key], c.format) }}</template>
            </td></tr></tbody>
        </table></div>
        <button v-if="b.rows.length > 15" class="more" @click.stop="showAll[b.id || i] = !showAll[b.id || i]">{{ showAll[b.id || i] ? $t('aiBlocks.collapse') : $t('aiBlocks.showAll0Rows', [b.rows.length]) }}</button>
      </div>

      <div v-else-if="b.type === 'refs'" class="blk rf" data-test="ai-block-refs">
        <span class="lb">{{ b.title || $t('aiBlocks.relatedRecords') }}</span>
        <a v-for="(r, j) in b.items" :key="j" class="ai-ref" :data-ref-link="refLink(r)" :title="r.label" data-test="ai-ref"><i />{{ r.code || r.label }}<em v-if="r.kind">{{ r.kind }}</em></a>
      </div>

      <div v-else-if="b.type === 'sources'" class="src" data-test="ai-block-sources">
        <button class="sh" :aria-expanded="!!openSrc[b.id || i]" @click.stop="openSrc[b.id || i] = !openSrc[b.id || i]">
          <el-icon><Coin /></el-icon>{{ $t('aiBlocks.dataSources0', [b.items.length]) }}<el-icon class="car"><ArrowDown v-if="!openSrc[b.id || i]" /><ArrowUp v-else /></el-icon></button>
        <div class="schips"><span v-for="(s, j) in b.items" :key="j" class="sc" :class="{ bad: s.failed }" data-test="ai-source" :title="s.sql || s.label">
          <el-icon><component :is="SOURCE_ICON[s.kind] || 'Coin'" /></el-icon>{{ s.label }}</span></div>
        <div v-if="openSrc[b.id || i]" class="sql">
          <div v-for="(s, j) in b.items.filter((x) => x.sql)" :key="j"><div class="sl">{{ s.label }}</div><pre>{{ s.sql }}</pre></div>
          <div v-if="!b.items.some((x) => x.sql)" class="muted small">{{ $t('aiBlocks.theseSourcesAreSystemSummary') }}</div>
        </div>
      </div>

      <div v-else-if="b.type === 'callout'" class="co" :class="b.tone">
        <b v-if="b.title">{{ b.title }}</b><div class="mdx" v-html="mdWithRefs(b.md, refs)" />
      </div>

      <DraftCard v-else-if="b.type === 'draft'" :draft="b.draft" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// Renders structured blocks in AI answers (chart / table / KPI card / record tag / data source / hint / action draft). Protocol: ./blocks.ts and backend services/ai_blocks.py.
//   <AiBlocks :blocks="m.blocks" :refs="m.refs" compact />      pinnable=false hides "Pin to Home"
import { defineComponent, h, reactive } from 'vue';
import { useRouter } from 'vue-router';
import { post } from '../api';
import Chart from '../components/Chart.vue';
import { toast } from './index';
import { chartHeight, chartOption, fmtCell, mdWithRefs, refLink, SOURCE_ICON, type Block, type RefItem } from './blocks';
import DraftCard from './DraftCard.vue';

const props = withDefaults(defineProps<{ blocks: Block[]; refs?: RefItem[]; compact?: boolean; pinnable?: boolean; source?: string }>(), { refs: () => [], compact: false, pinnable: true, source: '' });
const router = useRouter();
const openSrc = reactive<Record<string, boolean>>({}), showAll = reactive<Record<string, boolean>>({});
const allRefs = () => [...props.refs, ...props.blocks.flatMap((b: any) => (b.type === 'refs' ? b.items : []))];
const refOf = (v: any): RefItem | null => (typeof v === 'string' && v.length >= 3 ? allRefs().find((r) => r.code === v) || null : null);

function onClick(e: MouseEvent) {
  const a = (e.target as HTMLElement).closest('[data-ref-link]') as HTMLElement | null;
  if (a) { e.preventDefault(); router.push(a.dataset.refLink!); }
}

const pinned = reactive<Record<string, boolean>>({});
async function pin(b: any) {
  try {
    await post('/api/ai/pins', { block: b, title: b.title || (b.type === 'kpis' ? $t('common.kpi') : $t('aiBlocks.aiResult')), source: props.source });
    pinned[b.id || JSON.stringify(b).slice(0, 60)] = true;
    toast($t('aiBlocks.pinnedToHome'), 'success');
    window.dispatchEvent(new Event('ai-pins-changed'));
  } catch { /* already notified */ }
}
const PinBtn = defineComponent({ props: { b: Object }, setup(p) {
  return () => {
    const key = (p.b as any).id || JSON.stringify(p.b).slice(0, 60);
    return h('button', { class: ['pin', { on: pinned[key] }], title: pinned[key] ? $t('aiBlocks.pinnedToHome') : $t('aiBlocks.pinToHome'), 'data-test': 'ai-pin', onClick: (e: Event) => { e.stopPropagation(); if (!pinned[key]) pin(p.b); } },
      [h('svg', { viewBox: '0 0 16 16', width: 13, height: 13, 'aria-hidden': 'true' }, [h('path', { d: 'M9.8 1.5 14.5 6.2l-2 .6-2.6 2.6.2 3.1-1 1-2.5-2.5L3 14.6l-.6-.6 3.6-3.6-2.5-2.5 1-1 3.1.2 2.6-2.6z', fill: 'currentColor' })]),
        h('span', pinned[key] ? $t('aiBlocks.pinned') : $t('aiBlocks.pinToHome'))]);
  };
} });
const Unv = defineComponent({ props: { u: Array }, setup(p) {
  return () => (p.u && p.u.length ? h('span', { class: 'unv', title: $t('aiBlocks.noEvidenceFoundInThe', [(p.u as string[]).join($t('common.k42'))]), 'data-test': 'ai-unverified' }, $t('aiBlocks.n0NumbersNotVerified', [p.u.length])) : null);
} });
</script>

<style scoped>
.aib { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.mdx { line-height: 1.65; font-size: var(--hm-fs-sm); color: var(--hm-text); }
.mdx :deep(p) { margin: 3px 0; } .mdx :deep(ul), .mdx :deep(ol) { margin: 3px 0; padding-left: 18px; } .mdx :deep(li) { margin: 1px 0; }
.mdx :deep(h3), .mdx :deep(h4), .mdx :deep(h5) { margin: 6px 0 3px; font-size: var(--hm-fs-md); }
.mdx :deep(.mdt) { border-collapse: collapse; margin: 4px 0; font-size: var(--hm-fs-xs); } .mdx :deep(.mdt td), .mdx :deep(.mdt th) { border: 1px solid var(--hm-border); padding: 2px 6px; }
.mdx :deep(code) { background: var(--hm-surface-3); padding: 0 3px; border-radius: 3px; font-family: var(--hm-font-mono); font-size: .92em; }
.blk { border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); background: var(--hm-surface); padding: 8px 10px; min-width: 0; }
.bh { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; min-height: 20px; }
.bi { color: var(--hm-primary); } .bt { font-weight: var(--hm-fw-semibold); font-size: var(--hm-fs-sm); color: var(--hm-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.u { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); } .grow { flex: 1; }
.kp { padding: 8px; } .kg { display: grid; grid-template-columns: repeat(var(--n), minmax(0, 1fr)); gap: 6px; }
.kg :deep(.hm-kpi) { box-shadow: none; }
.tw { overflow: auto; max-height: 360px; border-radius: var(--hm-r-sm); }
table { width: 100%; border-collapse: collapse; font-size: var(--hm-fs-xs); }
th { position: sticky; top: 0; background: var(--hm-surface-2); color: var(--hm-text-2); font-weight: var(--hm-fw-medium); text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--hm-border); white-space: nowrap; }
td { padding: 4px 8px; border-bottom: 1px solid var(--hm-hairline); color: var(--hm-text); }
th.n, td.n { text-align: right; font-family: var(--hm-font-num); font-variant-numeric: tabular-nums; }
tbody tr:hover td { background: var(--hm-surface-3); }
.more { border: 0; background: transparent; color: var(--hm-primary); font: inherit; font-size: var(--hm-fs-xs); cursor: pointer; padding: 4px 0 0; }
.rf { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; background: var(--hm-surface-2); border-style: dashed; }
.rf .lb { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); margin-right: 2px; }
.ai-ref, :deep(.ai-ref) { display: inline-flex; align-items: center; gap: 4px; font-family: var(--hm-font-mono); font-size: 11.5px; line-height: 18px; padding: 0 7px 0 5px; border-radius: var(--hm-r-pill);
  background: var(--hm-primary-soft); color: var(--hm-primary-press); cursor: pointer; text-decoration: none; border: 1px solid transparent; white-space: nowrap; vertical-align: 1px; }
.ai-ref:hover, :deep(.ai-ref:hover) { border-color: var(--hm-primary); background: #fff; }
.ai-ref i, :deep(.ai-ref i) { width: 6px; height: 6px; border-radius: 50%; background: var(--hm-primary); flex: none; }
.ai-ref em { font-style: normal; font-family: var(--hm-font); font-size: 10.5px; color: var(--hm-text-3); }
.src { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); }
.sh { display: inline-flex; align-items: center; gap: 4px; border: 0; background: transparent; color: var(--hm-text-3); font: inherit; font-size: var(--hm-fs-2xs); cursor: pointer; padding: 0; letter-spacing: .02em; }
.sh:hover { color: var(--hm-primary); } .car { font-size: 10px; }
.schips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.sc { display: inline-flex; align-items: center; gap: 3px; background: var(--hm-surface-2); border: 1px solid var(--hm-hairline); border-radius: var(--hm-r-sm); padding: 1px 6px; color: var(--hm-text-2); max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sc.bad { color: var(--hm-fault-ink); background: var(--hm-fault-soft); }
.sql { margin-top: 6px; display: flex; flex-direction: column; gap: 6px; }
.sql .sl { color: var(--hm-text-2); margin-bottom: 2px; }
.sql pre { margin: 0; white-space: pre-wrap; word-break: break-all; font-family: var(--hm-font-mono); font-size: 11px; background: #0f1b2d; color: #d6e2f5; padding: 6px 8px; border-radius: var(--hm-r-sm); max-height: 160px; overflow: auto; }
.co { border-radius: var(--hm-r-md); padding: 7px 10px; border-left: 3px solid var(--hm-primary); background: var(--hm-primary-soft); font-size: var(--hm-fs-sm); }
.co.ok { border-color: var(--hm-run); background: var(--hm-run-soft); } .co.warn { border-color: var(--hm-idle); background: var(--hm-idle-soft); }
.co.risk { border-color: var(--hm-fault); background: var(--hm-fault-soft); } .co b { display: block; margin-bottom: 2px; }
:deep(.pin) { display: inline-flex; align-items: center; gap: 3px; border: 1px solid var(--hm-border); background: var(--hm-surface); color: var(--hm-text-3); border-radius: var(--hm-r-pill);
  font: inherit; font-size: 10.5px; padding: 1px 7px; cursor: pointer; opacity: .75; transition: opacity var(--hm-dur-1); }
.blk:hover :deep(.pin) { opacity: 1; } :deep(.pin:hover) { color: var(--hm-primary); border-color: var(--hm-primary); }
:deep(.pin.on) { color: var(--hm-amber-ink); border-color: var(--hm-amber); background: var(--hm-amber-soft); opacity: 1; cursor: default; }
:deep(.unv) { font-size: 10.5px; color: var(--hm-idle-ink); background: var(--hm-idle-soft); border-radius: var(--hm-r-pill); padding: 0 7px; line-height: 17px; cursor: help; }
.compact .blk { padding: 7px 8px; }
</style>
