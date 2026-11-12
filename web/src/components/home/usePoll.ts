// Polling for Home / displays: pauses when the page is hidden, refreshes immediately on return; keeps the last data on error.
import { $t } from '../../i18n';
import { onMounted, onUnmounted, ref, type Ref } from 'vue';
import { get } from '../../api';

export function usePoll<T = any>(path: string | (() => string), ms: number, opts: { immediate?: boolean } = {}) {
  const data = ref<T | null>(null) as Ref<T | null>;
  const error = ref(''), loading = ref(false), at = ref(0);
  let timer: any = null, busy = false;
  async function refresh() {
    if (busy) return;
    busy = true; loading.value = !data.value;
    try {
      data.value = await get(typeof path === 'function' ? path() : path, true);
      error.value = ''; at.value = Date.now();
    } catch (e: any) { error.value = String(e?.message || e); } finally { busy = false; loading.value = false; }
  }
  const vis = () => { if (!document.hidden) refresh(); };
  onMounted(() => {
    if (opts.immediate !== false) refresh();
    timer = setInterval(() => { if (!document.hidden) refresh(); }, ms);
    document.addEventListener('visibilitychange', vis);
  });
  onUnmounted(() => { clearInterval(timer); document.removeEventListener('visibilitychange', vis); });
  return { data, error, loading, at, refresh };
}

/** "2026-11-04 10:12:00" → Date (local time) */
export const toDate = (s: any) => (s ? new Date(String(s).replace(' ', 'T')) : null);
/** Minutes between two times → "12 min" / "1 h 05 min" */
export function since(from: any, now: any) {
  const a = toDate(from), b = toDate(now);
  if (!a || !b) return '';
  const m = Math.max(0, Math.round((b.getTime() - a.getTime()) / 60000));
  if (m < 1) return $t('feedList.justNow');
  if (m < 60) return $t('usePoll.n0Min', [m]);
  if (m < 1440) return $t('usePoll.n0H1Min', [Math.floor(m / 60), String(m % 60).padStart(2, '0')]);
  return $t('common.n0Days', [Math.floor(m / 1440)]);
}

/** Equipment status → tone (run/idle/fault/offline/setup in tokens) */
export const TONE: Record<string, string> = { running: 'run', idle: 'idle', setup: 'setup', down: 'fault', offline: 'offline' };
export const STATE_TEXT: Record<string, string> = { running: $t('common.running'), idle: $t('common.idle'), setup: $t('common.changeover'), down: $t('usePoll.breakdown'), offline: $t('common.offline') };
