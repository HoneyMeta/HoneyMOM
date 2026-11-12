import { $t, lang } from './i18n';
import { ElMessage } from 'element-plus';
import { reactive } from 'vue';

export const session = reactive<{ token: string; user: any; meta: any; now: string }>({
  token: localStorage.getItem('mes_token') || '', user: null, meta: null, now: '',
});

export async function call(method: string, path: string, body?: any, quiet = false) {
  const r = await fetch(path, {
    method, headers: { 'Content-Type': 'application/json', 'X-Lang': lang, ...(session.token ? { Authorization: 'Bearer ' + session.token } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const j = await r.json().catch(() => ({}));
  if (r.status === 401 && !path.endsWith('/login')) {
    session.token = ''; localStorage.removeItem('mes_token');
    if (!location.pathname.startsWith('/login')) location.href = '/login?next=' + encodeURIComponent(location.pathname + location.search);
    throw new Error($t('api.pleaseSignInFirst'));
  }
  if (r.status === 428 && j?.detail?.esign && body !== undefined) {
    // E-signature required (IATF critical release): show the signature dialog and resend the original request with the signature (esign.ts)
    const { esignPrompt } = await import('./esign');
    const sig = await esignPrompt(j.detail.esign);
    if (!sig) throw new Error($t('api.eSignatureCancelled'));
    return call(method, path, { ...(body || {}), esign: sig }, quiet);
  }
  if (!r.ok) {
    const msg = typeof j.detail === 'string' ? j.detail : (j.detail?.msg || r.statusText);
    if (!quiet) ElMessage.error(msg);
    throw new Error(msg);
  }
  return j;
}
export const get = (p: string, quiet = false) => call('GET', p, undefined, quiet);
export const post = (p: string, b: any = {}) => call('POST', p, b);
export const put = (p: string, b: any) => call('PUT', p, b);
export const del = (p: string) => call('DELETE', p);

export async function loadSession() {
  if (!session.token) return false;
  try {
    session.user = await get('/api/me', true);
    session.meta = await get('/api/meta', true);
    return true;
  } catch { return false; }
}

export function ent(name: string) { return session.meta?.entities.find((e: any) => e.name === name); }
export function canWrite(module: string) { return !!session.user?.writable?.includes(module); }
export const fmt = (v: any, d = 1) => (v === null || v === undefined || v === '' ? '—' : typeof v === 'number' ? (Number.isInteger(v) ? String(v) : v.toFixed(d)) : String(v));
export const pct = (v: any, d = 1) => (v === null || v === undefined ? '—' : (v * 100).toFixed(d) + '%');
/** Token for URLs the browser opens itself (downloads, print pages); also carries the UI language as ?lang= (docs/dev/i18n-notes.md). */
export const tok = () => encodeURIComponent(session.token) + '&lang=' + lang;

// ---------------- AI assistant (legacy API; new code uses askAi / v-ai from web/src/ai) ----------------
// Record selected on the current page (set when an entity detail, equipment drawer etc. opens); attached to every assistant message
export const assistantCtx = reactive<{ record: { entity: string; id: number; label: string } | null; open: boolean }>({ record: null, open: false });
export function setAssistantRecord(r: { entity: string; id: number; label: string } | null) { assistantCtx.record = r; }
/** Legacy API: hand a task to the right AI panel. Same as askAi(prompt, record); shows "AI is off" when AI is disabled. */
export function askAssistant(prompt: string, record?: { entity: string; id: number; label: string } | null) {
  if (record !== undefined) assistantCtx.record = record;
  window.dispatchEvent(new CustomEvent('hm-ask-legacy', { detail: { prompt, record: record || null } }));
}

export async function streamPost(path: string, body: any, onEvent: (ev: string, data: any) => void, signal?: AbortSignal) {
  const r = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Lang': lang, ...(session.token ? { Authorization: 'Bearer ' + session.token } : {}) },
    body: JSON.stringify(body), signal,
  });
  if (!r.ok || !r.body) {
    const j = await r.json().catch(() => ({}));
    throw new Error(typeof j.detail === 'string' ? j.detail : r.statusText);
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      let ev = 'message', data = '';
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) ev = line.slice(6).trim();
        else if (line.startsWith('data:')) data += line.slice(5).trim();
      }
      try { onEvent(ev, data ? JSON.parse(data) : null); } catch (e) { console.error(e); }
    }
  }
}

export const STATUS_CN: Record<string, string> = {
  queued: $t('api.queued'), running: $t('api.running'), waiting: $t('api.waiting'), succeeded: $t('common.completed'), failed: $t('common.failed'), rejected: $t('common.rejected'), cancelled: $t('common.cancelled'),
  pending: $t('common.pending'), approved: $t('common.approved'), done: $t('common.done'), skipped: $t('api.notReached'), tested: $t('api.tested'),
};
export const statusType = (s: string) => ({ succeeded: 'success', done: 'success', approved: 'success', failed: 'danger', rejected: 'danger', waiting: 'warning',
  pending: 'warning', running: 'primary', queued: 'info', cancelled: 'info', skipped: 'info' } as Record<string, any>)[s] || 'info';
