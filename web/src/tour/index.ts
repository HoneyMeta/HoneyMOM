// Guided tour entry points (usage in engine.ts / guides.ts):
//   installTour(router)       call once in main.ts: resume the last step after refresh, welcome tour on first login
//   openGuide(key)            open a tour; if previously exited midway, asks "Resume from step N / Start over"
//   openPageGuide(path?)      open the current page's tour (the "?" next to the page title)
//   GUIDES / guideForPath     tour list (used by "Tour: …" in the Ctrl+K command palette)
import { $t } from '../i18n';
import { ElMessageBox } from 'element-plus';
import type { Router } from 'vue-router';
import { session } from '../api';
import { guideAt, guideDef, resumeTour, setGuideSource, setTourRouter, startGuide, tour } from './engine';
import { GUIDES, guideForPath } from './guides';

export { GUIDES, guideForPath, TASK_GUIDES } from './guides';
export { stopTour, tour } from './engine';

setGuideSource(() => GUIDES);
let _router: Router | null = null;

export async function openGuide(key: string) {
  const def = guideDef(key);
  if (!def) return false;
  const at = guideAt(key);
  if (at && at > 0 && at < def.steps.length) {
    const r = await ElMessageBox.confirm($t('tourIndex.lastTimeYouWereAt', [at + 1, def.steps[at].title]), def.title, {
      confirmButtonText: $t('tourIndex.continueFromStep0', [at + 1]), cancelButtonText: $t('tourIndex.startOver'), distinguishCancelAndClose: true, type: 'info',
    }).then(() => 'go').catch((a) => a);
    if (r === 'close') return false;
    return startGuide(key, r === 'go' ? at : 0);
  }
  return startGuide(key, 0);
}
export function openPageGuide(path?: string) {
  const g = guideForPath(path || _router?.currentRoute.value.path || '/');
  return g ? openGuide(g.key) : openGuide('welcome');
}

const WELCOME = 'hm_welcomed:';
const SHELL_FREE = /^\/(login|m|screen)(\/|$)/;
/** On first login (first time for this account on this computer), show the welcome tour once when entering a shell page. Skipped for automated tests (navigator.webdriver) unless localStorage.hm_tour_force_welcome=1. */
function maybeWelcome(path: string) {
  if (SHELL_FREE.test(path) || !session.user || tour.on) return;
  let force = false;
  try {
    force = localStorage.getItem('hm_tour_force_welcome') === '1';
    if (localStorage.getItem(WELCOME + session.user.username)) return;
    if ((navigator as any).webdriver && !force) return;
    localStorage.setItem(WELCOME + session.user.username, new Date().toISOString());
  } catch { return; }
  setTimeout(() => { if (!tour.on) startGuide('welcome'); }, 1200);
}

export function installTour(router: Router) {
  _router = router;
  setTourRouter(router);
  router.isReady().then(() => resumeTour());
  router.afterEach((to) => maybeWelcome(to.path));
}
(window as any).__hmTour = Object.assign((window as any).__hmTour || {}, { openGuide, openPageGuide, GUIDES, guideForPath });
