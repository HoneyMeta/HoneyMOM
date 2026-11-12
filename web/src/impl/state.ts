// Frontend shared code for implementation tools (M3): Setup Wizard state, enabled optional modules (menu shows Foundry / Machining etc. based on it), file to base64, download with token.
import { reactive } from 'vue';
import { call, post, session, tok } from '../api';

export const implState = reactive<{ loaded: boolean; at: number; pending: boolean; can_setup: boolean; modules: string[] | null; packs: string[]; company: string }>({
  loaded: false, at: 0, pending: false, can_setup: false, modules: null, packs: [], company: '',
});

/** Fetch /api/impl/setup/state (once per session; force re-fetches). If unavailable the menu is unaffected (modules=null = show all). */
export async function loadImplState(force = false) {
  if (!session.user) return implState;
  if (implState.loaded && !force && Date.now() - implState.at < 60000) return implState;
  try {
    const s = await call('GET', '/api/impl/setup/state', undefined, true);
    Object.assign(implState, { loaded: true, at: Date.now(), pending: !!s.pending, can_setup: !!s.can_setup, modules: s.modules || [], packs: s.packs || [], company: s.company || '' });
  } catch { implState.loaded = true; implState.at = Date.now(); }
  return implState;
}

/** Optional module of a menu item (shown only after the industry template is enabled); shown if state wasn't fetched */
export const moduleOn = (m?: string) => !m || implState.modules === null || implState.modules.includes(m);

export function fileB64(f: File): Promise<string> {
  return new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(',').pop() || ''); r.onerror = () => bad(r.error); r.readAsDataURL(f); });
}
export async function uploadJson(url: string, f: File, extra: Record<string, any> = {}) {
  return post(url, { filename: f.name, data: await fileB64(f), ...extra });
}
export function openDownload(url: string) {
  window.open(url + (url.includes('?') ? '&' : '?') + 'token=' + tok(), '_blank');
}
export const roleList = () => (session.user?.role_list || []) as string[];
