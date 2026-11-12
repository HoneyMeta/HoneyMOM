// Chart theme (ECharts): all page charts share one set of colors, axes, tooltip, legend and donut labels; components/Chart.vue applies it automatically.
// Pages specify only data and chart type, never hard-coded colors; for semantic colors (good/warning/bad, target line) use the exported CH.
//   import { CH, PALETTE, seq } from '../design/chart';
//   series: [{ type: 'bar', data, itemStyle: { color: CH.bad } }]
//   markLine: { data: [{ yAxis: 0.85, ...targetLine('Target 85%') }] }
// Donut (pie + radius array): default label "name + percent" placed outside, aligned in a column on each side, not truncated (outer radius capped at 60% to leave room); more than 6 slices switches to a right legend.
import { $t } from '../i18n';
import * as echarts from 'echarts';

/** Categorical colors: cobalt, cyan, amber, purple, turquoise, rose, slate, sky blue. The first three are most used, highly distinct, and separable for color-weak users (different lightness). */
export const PALETTE = ['#1463d6', '#0ba5c4', '#d9822b', '#6d5ae6', '#12a37f', '#d9466f', '#64748b', '#5b9bf0'];

/** Semantic colors (match status colors in tokens.css) */
export const CH = {
  primary: '#1463d6', cyan: '#0ba5c4', amber: '#d9822b', violet: '#6d5ae6',
  ok: '#15a34a', warn: '#d99a16', bad: '#dc2b2b', idle: '#d99a16', setup: '#1463d6', offline: '#94a3b8',
  /** Reference lines (target, mean, control limits): mid-grey dashed; use bad when out of limits */
  ref: '#7b8aa0', grid: '#edf1f6', axis: '#cbd5e1', text: '#0f1b2d', text2: '#44536a', text3: '#7b8aa0',
  /** Light bar background (baseline, previous period) */
  soft: 'rgba(20, 99, 214, .16)',
};
/** Equipment status → color (running/idle/changeover/down/offline) */
export const STATE_COLOR: Record<string, string> = { running: CH.ok, Executing: CH.ok, idle: CH.idle, Idle: CH.idle, setup: CH.setup, Setup: CH.setup, down: CH.bad, Stopped: CH.bad, offline: CH.offline };

/** Single-hue sequential scale (heatmaps, grading): light → dark cobalt */
export function seq(n: number, from = '#d3e3fb', to = '#0c479f') {
  const a = hex(from), b = hex(to);
  return Array.from({ length: n }, (_, i) => { const t = n === 1 ? 1 : i / (n - 1); return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * t)).join(',')})`; });
}
function hex(h: string) { const s = h.replace('#', ''); return [0, 2, 4].map((i) => parseInt(s.slice(i, i + 2), 16)); }

/** Reference line style: spread into a markLine data item { yAxis: v, ...targetLine('Target') } */
export function targetLine(label: string, color = CH.ref) {
  return { name: label, lineStyle: { color, type: 'dashed', width: 1 }, label: { formatter: label, position: 'insideEndTop', color, fontSize: 11 } };
}

const FONT = '"HarmonyOS Sans SC", "PingFang SC", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", system-ui, sans-serif';
const NUMF = '"DIN Alternate", Bahnschrift, "Segoe UI", system-ui, sans-serif';
const axis = (vert = false, k = 1) => ({
  axisLine: { show: !vert, lineStyle: { color: CH.axis } },
  axisTick: { show: false },
  axisLabel: { color: CH.text3, fontSize: 11 * k, fontFamily: NUMF, hideOverlap: true },
  splitLine: { show: vert, lineStyle: { color: CH.grid, type: [4, 3] } },
  nameTextStyle: { color: CH.text3, fontSize: 11 * k },
});

/** Theme (k = density --hm-scale: 1 / 1.125 / 1.25) */
export const buildTheme = (k = 1) => ({
  color: PALETTE,
  backgroundColor: 'transparent',
  textStyle: { fontFamily: FONT, color: CH.text2, fontSize: 12 * k },
  title: { textStyle: { color: CH.text, fontSize: 13 * k, fontWeight: 600 }, subtextStyle: { color: CH.text3 } },
  legend: { textStyle: { color: CH.text2, fontSize: 12 * k }, icon: 'roundRect', itemWidth: 10 * k, itemHeight: 6 * k, itemGap: 14, pageIconColor: CH.primary, pageTextStyle: { color: CH.text3 } },
  tooltip: {
    backgroundColor: 'rgba(255,255,255,.96)', borderColor: '#e2e8f0', borderWidth: 1, padding: [8, 10],
    textStyle: { color: CH.text, fontSize: 12 * k, fontFamily: FONT },
    extraCssText: 'box-shadow: 0 2px 6px rgba(15,32,60,.08), 0 10px 28px rgba(15,32,60,.12); border-radius: 8px;',
    axisPointer: { lineStyle: { color: '#94a3b8', type: 'dashed' }, crossStyle: { color: '#94a3b8' }, shadowStyle: { color: 'rgba(20,99,214,.06)' } },
  },
  categoryAxis: axis(false, k),
  valueAxis: axis(true, k),
  timeAxis: axis(false, k),
  logAxis: axis(true, k),
  line: { symbol: 'circle', symbolSize: 5, showSymbol: false, smooth: false, lineStyle: { width: 2 }, emphasis: { focus: 'series' } },
  bar: { barMaxWidth: 28, itemStyle: { borderRadius: [3, 3, 0, 0] } },
  pie: { itemStyle: { borderColor: '#fff', borderWidth: 2 } },
  scatter: { symbolSize: 6 },
  markLine: { symbol: 'none' },
  dataZoom: { borderColor: '#e2e8f0', fillerColor: 'rgba(20,99,214,.08)', handleStyle: { color: '#fff', borderColor: '#94a3b8' }, textStyle: { color: CH.text3 } },
});
export const THEME = buildTheme(1);
echarts.registerTheme('hm', THEME);
/** Theme name for the current density (registered on first use) */
export function chartScale() {
  try { return parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--hm-scale')) || 1; } catch { return 1; }
}
const made = new Set(['hm']);
export function themeName(k = chartScale()) {
  const n = k === 1 ? 'hm' : `hm-${k}`;
  if (!made.has(n)) { echarts.registerTheme(n, buildTheme(k)); made.add(n); }
  return n;
}

/** Look only at the page's grid: enable containLabel if not given (so axis labels aren't clipped) */
function fixGrid(g: any) {
  if (!g) return { left: 8, right: 12, top: 30, bottom: 8, containLabel: true };
  const one = (x: any) => (x.containLabel === undefined ? { ...x, containLabel: true, left: shrink(x.left), right: shrink(x.right), bottom: shrink(x.bottom) } : x);
  return Array.isArray(g) ? g.map(one) : one(g);
}
// Old pages' grid.left reserved room for axis labels (40~120); with containLabel that's too much space
const shrink = (v: any) => (typeof v === 'number' && v > 16 ? Math.min(v, 16) : v);

/** Donut/pie labels: outside, aligned on both sides, name + percent on two lines, not truncated; too many slices switch to a legend. */
function fixPie(s: any, height: number, k = 1) {
  if (s.type !== 'pie') return s;
  const n = (s.data || []).length;
  const out: any = { avoidLabelOverlap: true, minShowLabelAngle: 4, ...s };
  if (!s.label || s.label.position === undefined || s.label.position === 'outside') {
    out.label = {
      show: n <= 6, position: 'outside', alignTo: 'labelLine', edgeDistance: 6, bleedMargin: 4, minMargin: 4, overflow: 'none', lineHeight: 15 * k,
      formatter: '{b|{b}}\n{d|{d}%}',
      rich: { b: { color: CH.text2, fontSize: 11.5 * k }, d: { color: CH.text, fontSize: 12 * k, fontWeight: 600, fontFamily: NUMF } },
      ...(s.label || {}),
    };
    if (s.label?.formatter && !s.label.rich) out.label.rich = undefined;
    out.labelLine = { length: 10, length2: 14, smooth: 0.2, lineStyle: { color: CH.axis }, ...(s.labelLine || {}) };
    out.labelLayout = { hideOverlap: true, ...(s.labelLayout || {}) };
    // Outside labels need room: outer radius no more than 60% (smaller on short charts)
    const r = Array.isArray(s.radius) ? s.radius : s.radius ? [0, s.radius] : ['44%', '62%'];
    const cap = height < 200 ? 54 : 60;
    const pct = (v: any) => (typeof v === 'string' && v.endsWith('%') ? parseFloat(v) : null);
    const o = pct(r[1]), i = pct(r[0]);
    if (n <= 6 && o !== null && o > cap) out.radius = [i !== null ? `${Math.round(i * cap / o)}%` : r[0], `${cap}%`];
  }
  return out;
}

/** Page option → themed option (default grid, tooltip, donut labels). */
export function themed(opt: any, height = 260, k = 1) {
  if (!opt) return opt;
  const o: any = { ...opt };
  const hasAxis = o.xAxis || o.yAxis;
  if (hasAxis) o.grid = fixGrid(o.grid);
  if (o.legend && !Array.isArray(o.legend) && o.legend.top === undefined && o.legend.bottom === undefined) o.legend = { ...o.legend, top: 0 };
  if (o.legend && !Array.isArray(o.legend) && o.legend.type === undefined) o.legend = { type: 'scroll', ...o.legend };
  if (o.series) {
    const list = (Array.isArray(o.series) ? o.series : [o.series]).map((s: any) => fixPie(s, height, k));
    const pies = list.filter((s: any) => s.type === 'pie');
    // Many slices: turn labels off, vertical legend on the right (with percentages)
    if (pies.length === 1 && (pies[0].data || []).length > 6 && !o.legend) {
      const tot = pies[0].data.reduce((a: number, x: any) => a + (Number(x.value) || 0), 0) || 1;
      o.legend = { type: 'scroll', orient: 'vertical', right: 0, top: 'middle', formatter: (nm: string) => { const x = pies[0].data.find((d: any) => d.name === nm); return `${nm}  ${((Number(x?.value) || 0) / tot * 100).toFixed(1)}%`; } };
      pies[0].center = pies[0].center || ['34%', '50%'];
    }
    o.series = list;
    if (pies.length && !o.tooltip) o.tooltip = { trigger: 'item', formatter: $t('chart.bCD') };
  }
  return o;
}
