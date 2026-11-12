import { $t, intlLocale, isZh } from '../i18n';
// Number formatting: unified digits, thousands separators, units, percent, durations. Don't hand-write toFixed in pages.
export const DASH = '—';
const isNil = (v: any) => v === null || v === undefined || v === '' || (typeof v === 'number' && !isFinite(v));

/** Remove a duplicated code when the name already contains it (e.g. code AGV, name "AGV Fleet"): bareName → "Fleet", codeName → "AGV Fleet" (not "AGV AGV Fleet") */
const dupCode = (code: any, name: any) => !!code && !!name && (String(name) === String(code) || String(name).startsWith(String(code) + ' '));
export function bareName(code: any, name: any): string { return dupCode(code, name) ? String(name).slice(String(code).length).trim() : String(name ?? ''); }
export function codeName(code: any, name: any): string { return dupCode(code, name) ? String(name) : [code, name].filter((x) => x !== null && x !== undefined && x !== '').join(' '); }

/** Number: integers without decimals by default, non-integers 1 decimal; thousands separators; digits = fixed decimals. */
export function fmtNum(v: any, digits?: number): string {
  if (isNil(v)) return DASH;
  const n = Number(v);
  if (isNaN(n)) return String(v);
  const d = digits ?? (Number.isInteger(n) ? 0 : Math.abs(n) < 10 ? 2 : 1);
  return n.toLocaleString(intlLocale, { minimumFractionDigits: d, maximumFractionDigits: d });
}

/** Ratio → percent: 0.873 → "87.3%". With ratio=false, v is already a percentage. */
export function fmtPct(v: any, digits = 1, ratio = true): string {
  if (isNil(v)) return DASH;
  return (Number(v) * (ratio ? 100 : 1)).toFixed(digits) + '%';
}

/** With unit: fmtUnit(1234.5, 'kWh') → "1,234.5 kWh". */
export function fmtUnit(v: any, unit = '', digits?: number): string {
  const s = fmtNum(v, digits);
  return s === DASH || !unit ? s : `${s} ${unit}`;
}

/** Large-number abbreviation. en: 12345 → "12.35K", 123456789 → "123.46M"; zh-CN uses the 10^4 / 10^8 units (format.k / format.b). */
export function fmtCompact(v: any, digits = 2): string {
  if (isNil(v)) return DASH;
  const n = Number(v), a = Math.abs(n);
  if (isZh) {
    if (a >= 1e8) return (n / 1e8).toFixed(digits) + $t('format.b');
    if (a >= 1e4) return (n / 1e4).toFixed(digits) + $t('format.k');
    return fmtNum(n);
  }
  if (a >= 1e9) return (n / 1e9).toFixed(digits) + 'B';
  if (a >= 1e6) return (n / 1e6).toFixed(digits) + 'M';
  if (a >= 1e4) return (n / 1e3).toFixed(digits) + 'K';
  return fmtNum(n);
}

/** Duration (seconds) → "2 h 05 min" / "12 min 30 s" / "45 s". */
export function fmtDur(sec: any): string {
  if (isNil(sec)) return DASH;
  const s = Math.round(Number(sec));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  if (h) return $t('format.n0H1M', [h, String(m).padStart(2, '0')]);
  if (m) return $t('format.n0M1S', [m, String(r).padStart(2, '0')]);
  return $t('format.n0S', [r]);
}

/** Time: "2026-11-04 08:12:33" → "11-04 08:12" (short) / "08:12:33" (time). */
export function fmtTime(v: any, mode: 'short' | 'time' | 'full' = 'short'): string {
  if (isNil(v)) return DASH;
  const s = String(v).replace('T', ' ');
  return mode === 'time' ? s.slice(11, 19) : mode === 'full' ? s.slice(0, 19) : s.slice(5, 16);
}

/** Delta: returns { text: "+3.2%", dir: 'up'|'down'|'flat' }. */
export function fmtDelta(v: any, digits = 1, asPct = false): { text: string; dir: 'up' | 'down' | 'flat' } {
  if (isNil(v)) return { text: DASH, dir: 'flat' };
  const n = Number(v);
  const dir = n > 0 ? 'up' : n < 0 ? 'down' : 'flat';
  const body = asPct ? (Math.abs(n) * 100).toFixed(digits) + '%' : fmtNum(Math.abs(n), digits);
  return { text: (n > 0 ? '+' : n < 0 ? '−' : '±') + body, dir };
}

/** Format a value by format name (for a component's format prop). */
export function fmtBy(v: any, format: 'int' | 'num' | 'pct' | 'compact' | 'dur' | string = 'num', digits?: number): string {
  if (format === 'int') return fmtNum(isNil(v) ? v : Math.round(Number(v)), 0);
  if (format === 'pct') return fmtPct(v, digits ?? 1);
  if (format === 'compact') return fmtCompact(v);
  if (format === 'dur') return fmtDur(v);
  return fmtNum(v, digits);
}
