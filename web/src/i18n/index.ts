// UI language (vue-i18n). Defaults to the browser language (zh* -> zh-CN, otherwise English); switchable.
// Messages live in ../locales/{en,zh-CN}.json, namespaced per page/component ("home.planAttainment").
// Placeholders are positional: "{0} pcs" -> $t(key, [n]). Named ECharts templates such as "{b}: {c}" pass through untouched.
// Switching language stores the choice and reloads the page, so module-level labels (menus, enums) are rebuilt in the new language.
import { createI18n } from 'vue-i18n';
import en from '../locales/en.json';
import zhCN from '../locales/zh-CN.json';

export type Lang = 'en' | 'zh-CN';
export const LANG_KEY = 'hm_lang';
/** Language names are shown in their own language. */
export const LANGS: { code: Lang; label: string; short: string }[] = [
  { code: 'en', label: 'English', short: 'EN' },
  { code: 'zh-CN', label: '\u4e2d\u6587', short: '\u4e2d' },
];

function initial(): Lang {
  try {
    const q = new URLSearchParams(location.search).get('lang');
    if (q === 'en' || q === 'zh-CN') localStorage.setItem(LANG_KEY, q);
    const v = q || localStorage.getItem(LANG_KEY);
    if (v === 'en' || v === 'zh-CN') return v;
    if (v && v.toLowerCase().startsWith('zh')) return 'zh-CN';
  } catch { /* storage blocked */ }
  // No stored choice: follow the browser language (Chinese browsers get zh-CN), else English.
  try {
    const nav = (navigator.languages && navigator.languages[0]) || navigator.language || '';
    if (nav.toLowerCase().startsWith('zh')) return 'zh-CN';
  } catch { /* no navigator */ }
  return 'en';
}

export const lang: Lang = initial();
export const isZh = lang === 'zh-CN';
/** BCP-47 tag for Intl / toLocaleString. */
export const intlLocale = isZh ? 'zh-CN' : 'en-US';

// Minimal message format: {0} / {name} are replaced when a value is given, everything else is literal
// (so "@", "|", "{b}" and HTML need no escaping in the locale files).
function compile(msg: any) {
  const s = typeof msg === 'string' ? msg : String(msg ?? '');
  return (ctx: any) => s.replace(/\{(\w+)\}/g, (m: string, k: string) => {
    const v = /^\d+$/.test(k) ? ctx.list(Number(k)) : ctx.named(k);
    return v === undefined || v === null ? m : String(v);
  });
}

export const i18n = createI18n({
  legacy: false,
  globalInjection: true,
  locale: lang,
  fallbackLocale: 'en',
  messages: { en, 'zh-CN': zhCN } as any,
  messageCompiler: compile as any,
  missingWarn: false,
  fallbackWarn: false,
  warnHtmlMessage: false,
});

const gt = i18n.global.t as any;
/** Translate in scripts (templates use the global $t). */
export function $t(key: string, list?: unknown[]): string {
  return list ? gt(key, list) : gt(key);
}

export function setLang(l: Lang) {
  try { localStorage.setItem(LANG_KEY, l); } catch { /* ignore */ }
  const u = new URL(location.href);
  if (u.searchParams.has('lang')) { u.searchParams.delete('lang'); location.replace(u.toString()); } else location.reload();
}

try { document.documentElement.lang = lang; } catch { /* ssr / tests */ }
