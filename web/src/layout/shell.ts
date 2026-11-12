// Shell state: fetches /api/shell/status every 3 s (data clock, collection, equipment online, alarms, notifications, approvals, AI status); shared by the top bar and bottom status bar.
import { reactive } from 'vue';
import { get } from '../api';
import { aiState, loadAiSwitches } from '../ai';

export const shell = reactive({
  ok: false, lastOk: 0, now: '', dataClock: false,
  sources: [] as any[], collect: { online: 0, total: 0 },
  equipment: { total: 0, online: 0, by_status: {} as Record<string, number> },
  alarms: 0, unread: 0, approvals: null as number | null, ai: null as any,
  navCollapsed: false, navDrawer: false, paletteOpen: false,
  /** Window width (for responsive layout): ≥1280 desktop; 1024–1279 tablet landscape (menu collapsed by default); <1100 AI panel becomes an overlay; ≤700 phone */
  vw: typeof innerWidth === 'number' ? innerWidth : 1440,
  navTabletOpen: false,
});
if (typeof window !== 'undefined') window.addEventListener('resize', () => { shell.vw = innerWidth; });
try { shell.navCollapsed = localStorage.getItem('hm_nav_collapsed') === '1'; } catch { /* */ }

let timer: any = null, n = 0;
async function tick() {
  try {
    const s = await get('/api/shell/status', true);
    Object.assign(shell, { ok: true, lastOk: Date.now(), now: s.now, dataClock: s.data_clock, sources: s.sources, collect: s.collect, equipment: s.equipment,
      alarms: s.alarms_open, unread: s.notices_unread, approvals: s.approvals, ai: s.ai });
    // AI switches changed elsewhere (another browser, an admin): re-fetch the full switch set
    if (aiState.switches && (aiState.switches.mode !== s.ai.mode || JSON.stringify(aiState.switches.effective) !== JSON.stringify(s.ai.effective))) loadAiSwitches();
  } catch { if (Date.now() - shell.lastOk > 10000) shell.ok = false; }
  if (n++ % 20 === 0 && !aiState.switches) loadAiSwitches();
}
export function startShellPolling() { if (timer) return; tick(); timer = setInterval(tick, 3000); window.addEventListener('approvals-changed', tick); }
export function stopShellPolling() { clearInterval(timer); timer = null; window.removeEventListener('approvals-changed', tick); }
export const refreshShell = tick;
export function setNavCollapsed(v: boolean) { shell.navCollapsed = v; try { localStorage.setItem('hm_nav_collapsed', v ? '1' : '0'); } catch { /* */ } }
