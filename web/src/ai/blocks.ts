// "Block" protocol for AI generative answers (backend honeymom/services/ai_blocks.py is authoritative). Rendered by AiBlocks.vue; types and helpers live here.
import { CH, PALETTE } from '../design/chart';
import { fmtBy } from '../design/format';
import { md } from '../md';

export interface KpiItem { label: string; value: number | string | null; unit?: string; format?: 'pct' | 'int' | 'num'; delta?: number; deltaPct?: boolean; good?: 'up' | 'down'; hint?: string; ref?: RefItem }
export interface RefItem { entity: string; id?: number | null; code?: string; label?: string; kind?: string }
export interface SourceItem { id?: string; kind: 'sql' | 'kpi' | 'query' | 'kb' | 'record' | 'context' | 'agent' | string; label: string; sql?: string; rows?: number | null; failed?: boolean }
export type Block =
  | { id?: string; type: 'text'; md: string }
  | { id?: string; type: 'kpis'; title?: string; items: KpiItem[]; unverified?: string[] }
  | { id?: string; type: 'chart'; kind: 'line' | 'bar' | 'hbar' | 'pie' | 'area'; title?: string; x: string[]; series: { name: string; data: (number | null)[] }[]; unit?: string; format?: string; unverified?: string[]; query?: any }
  | { id?: string; type: 'table'; title?: string; columns: { key: string; label: string; num?: boolean; format?: string }[]; rows: Record<string, any>[]; unverified?: string[]; auto?: boolean }
  | { id?: string; type: 'refs'; title?: string; items: RefItem[] }
  | { id?: string; type: 'sources'; items: SourceItem[] }
  | { id?: string; type: 'callout'; tone: 'info' | 'ok' | 'warn' | 'risk'; title?: string; md: string }
  | { id?: string; type: 'draft'; draft: any };

export const refLink = (r: RefItem) => (r.id ? `/e/${r.entity}?id=${r.id}` : r.code ? `/e/${r.entity}?f_code=${encodeURIComponent(r.code)}` : `/e/${r.entity}`);

/** [[entity:id|label]] in text → clickable record tag (HTML; clicks handled via event delegation: data-ref-link) */
export function mdWithRefs(text: string, refs: RefItem[] = []) {
  const toks: string[] = [];
  const src = (text || '').replace(/\[\[([a-z_]+):([^\]|\n]{1,80})(?:\|([^\]\n]{1,80}))?\]\]/g, (_m, ent, key, shown) => {
    const k = String(key).trim();
    const r = refs.find((x) => x.entity === ent && (x.code === k || String(x.id) === k));
    toks.push(r ? `<a class="ai-ref" data-ref-link="${refLink(r)}" title="${esc(r.label || k)}"><i></i>${esc(shown || r.code || k)}</a>` : `<b>${esc(shown || k)}</b>`);
    return `\u0000${toks.length - 1}\u0000`;
  });
  return md(src).replace(/\u0000(\d+)\u0000/g, (_m, i) => toks[Number(i)]);
}
const esc = (s: string) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!));

export const fmtCell = (v: any, f?: string) => (typeof v === 'number' ? fmtBy(v, f || (Number.isInteger(v) ? 'int' : 'num')) : v ?? '—');

/** chart block → ECharts option (theme applied by Chart.vue; only data and type here) */
export function chartOption(b: any) {
  const pct = b.format === 'pct';
  const vf = (v: any) => (typeof v !== 'number' ? '—' : pct ? fmtBy(v, 'pct') : fmtBy(v, Number.isInteger(v) ? 'int' : 'num') + (b.unit ? ' ' + b.unit : ''));
  if (b.kind === 'pie') {
    const s = b.series[0] || { data: [] };
    return { tooltip: { trigger: 'item', valueFormatter: vf }, legend: b.x.length > 6 ? { type: 'scroll', orient: 'vertical', right: 0, top: 'middle' } : { bottom: 0 },
      series: [{ type: 'pie', radius: ['48%', '72%'], center: b.x.length > 6 ? ['38%', '50%'] : ['50%', '46%'], data: b.x.map((n: string, i: number) => ({ name: n, value: s.data[i] })) }] };
  }
  const h = b.kind === 'hbar';
  const cat = { type: 'category', data: b.x, axisLabel: { interval: h ? 0 : 'auto' } };
  const val = { type: 'value', axisLabel: { formatter: (v: number) => (pct ? Math.round(v * 100) + '%' : fmtBy(v, 'compact')) } };
  return {
    tooltip: { trigger: 'axis', valueFormatter: vf },
    legend: b.series.length > 1 ? { top: 0, right: 0 } : undefined,
    grid: { left: h ? 8 : 6, right: 14, top: b.series.length > 1 ? 30 : 14, bottom: 4, containLabel: true },
    xAxis: h ? val : cat, yAxis: h ? { ...cat, inverse: true } : val,
    series: b.series.map((s: any, i: number) => ({
      name: s.name, type: b.kind === 'line' || b.kind === 'area' ? 'line' : 'bar', data: s.data, smooth: b.kind !== 'bar',
      showSymbol: b.x.length <= 16, barMaxWidth: 26, connectNulls: true,
      areaStyle: b.kind === 'area' || (b.kind === 'line' && b.series.length === 1) ? { opacity: 0.08 } : undefined,
      itemStyle: { color: PALETTE[i % PALETTE.length], borderRadius: b.kind === 'bar' ? [3, 3, 0, 0] : h ? [0, 3, 3, 0] : 0 },
      label: h && b.x.length <= 12 ? { show: true, position: 'right', color: CH.text2, fontSize: 11, formatter: (p: any) => vf(p.value) } : undefined,
    })),
  };
}

/** Block height (charts) */
export const chartHeight = (b: any) => (b.kind === 'hbar' ? Math.min(380, 40 + b.x.length * 26) : b.kind === 'pie' ? 230 : 210);

export const SOURCE_ICON: Record<string, string> = { sql: 'Coin', kpi: 'DataLine', query: 'Search', kb: 'Reading', record: 'Document', context: 'Aim', agent: 'Cpu' };
