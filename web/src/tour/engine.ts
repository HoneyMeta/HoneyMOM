// Guided tour engine (slimmed-down port of the Virtual Factory 4.7 tour engine: VF web/src/panels/teach/tour/tour.ts).
// A tour = several steps; each step spotlights a target on the page (data-tour="name" or a CSS selector) with a step card beside it (Back / Next / Skip / Exit);
// a step can first navigate to another page (route) or click something open (click), so one tour can walk users through a task across pages.
// Refreshing resumes the current step (sessionStorage); exiting midway remembers the step and next time asks "Resume / Start over" (localStorage).
//
// Step: { id, title, do, why?, target?, route?, click?, done?, manual? = true }
//   target: { tour: 'name' } → [data-tour="name"] | { sel: 'CSS' } | array = first visible in order; omitted = card only (explanation step)
//   route: page opened before entering the step (path only; with ? compares the full URL)  click: element clicked on entering the step (CSS, e.g. first table row to open detail)
//   done: completion condition (when met the card shows "✓ Done"; steps with manual=false advance automatically once met):
//         { route: '/e/report' } on this page | { seen: target } target visible | { all: [] } { any: [] } { not: … }
import { createApp, reactive } from 'vue';
import type { Router } from 'vue-router';

export type GuideStep = { id: string; title: string; do: string; why?: string; target?: any; route?: string; click?: string; done?: any; manual?: boolean; delay?: number };
export type Guide = {
  key: string; title: string; desc: string; kind: 'welcome' | 'module' | 'task';
  /** Which pages this tour covers (the page "?" finds tours by path) */
  routes?: string[]; kw?: string; steps: GuideStep[]; outro?: string;
};

const KEY = 'hm_tour', AT = 'hm_guide_at';
export const tour = reactive<any>({
  on: false, def: null as Guide | null, key: '', i: 0, stepAt: 0, finished: false, min: false, ok: false, okAt: 0, busy: false,
  hold: false,   // For UI tests: don't auto-advance
  /** >0 when the page header (HmPageHead) has a "?", so the shell doesn't add another on the breadcrumb row */
  heads: 0,
});

let _router: Router | null = null;
let _defs: () => Guide[] = () => [];
export function setTourRouter(r: Router) { _router = r; }
export function setGuideSource(f: () => Guide[]) { _defs = f; }
export const guideDef = (key: string) => _defs().find((g) => g.key === key) || null;

function save() {
  try {
    if (!tour.on) { sessionStorage.removeItem(KEY); return; }
    sessionStorage.setItem(KEY, JSON.stringify({ key: tour.key, i: tour.i, min: tour.min }));
  } catch { /* incognito mode */ }
}
export function guideAt(key: string): number | null {
  try { const v = JSON.parse(localStorage.getItem(AT) || '{}')[key]; return typeof v === 'number' ? v : null; } catch { return null; }
}
export function setGuideAt(key: string, i: number | null) {
  try { const m = JSON.parse(localStorage.getItem(AT) || '{}'); if (i === null || i <= 0) delete m[key]; else m[key] = i; localStorage.setItem(AT, JSON.stringify(m)); } catch { /* */ }
}

/** Start a tour (at: starting step, 0-based). Returns false if the tour doesn't exist. */
export function startGuide(key: string, at = 0) {
  const def = guideDef(key);
  if (!def) return false;
  Object.assign(tour, { on: true, def, key, finished: false, min: false });
  ensureOverlay();
  go(at);
  loop();
  return true;
}
export function stopTour() {
  tour.on = false; tour.def = null; tour.finished = false; tour.busy = false;
  clearTimeout(timer); timer = null;
  save();
}
/** Exit (remembers the step so it can be resumed next time) */
export function exitTour() {
  if (tour.on && !tour.finished) setGuideAt(tour.key, tour.i);
  stopTour();
}
export const step = (): GuideStep | null => tour.def?.steps?.[tour.i] || null;
export const next = () => go(tour.i + 1);
export const prev = () => go(tour.i - 1);

const samePath = (want: string) => {
  const cur = _router?.currentRoute.value;
  if (!cur) return true;
  return want.includes('?') ? cur.fullPath === want : cur.path === want.split('?')[0];
};
let goSeq = 0;
export async function go(i: number) {
  const n = tour.def?.steps?.length || 0;
  if (i >= n) { tour.finished = true; tour.i = n; setGuideAt(tour.key, null); save(); return; }
  const my = ++goSeq;
  tour.i = Math.max(0, i); tour.finished = false; tour.ok = false; tour.okAt = 0; tour.stepAt = Date.now();
  save();
  const s = step()!;
  // Which page this step is on: its own route if set, else the nearest previous step's route (jumping to step N, or the user wandering to another page, both return to that page first)
  let route = s.route;
  for (let k = tour.i; !route && k >= 0; k--) route = tour.def.steps[k].route;
  tour.busy = !!((route && !samePath(route)) || s.click);
  try {
    if (route && _router && !samePath(route)) { await _router.push(route); await sleep(250); }
    if (my !== goSeq) return;
    if (s.click) {
      await until(() => document.querySelector(s.click!), 5000);
      if (my !== goSeq) return;
      (document.querySelector(s.click) as HTMLElement | null)?.click();
      await sleep(400);
    }
    if (s.target) { await until(() => hasCandidate(s.target), 5000); if (my === goSeq) revealTarget(s.target); }
  } catch { /* navigation interrupted */ }
  if (my === goSeq) tour.busy = false;
}

// ---------------- Conditions ----------------
export function cond(c: any): boolean {
  if (!c) return false;
  if (c.all) return c.all.every(cond);
  if (c.any) return c.any.some(cond);
  if (c.not) return !cond(c.not);
  if (c.route) return samePath(c.route);
  if (c.seen) return !!resolveTarget(c.seen);
  return false;
}

// ---------------- Targets (as in VF: spotlight only when actually visible on screen) ----------------
export type Hit = { el: Element | null; rect: DOMRect };
const CLIPS = /(auto|scroll|hidden|clip)/;
/** The visible part of an element (minus parts clipped by parent overflow or outside the viewport); null if nothing is visible. */
export function shownRect(el: Element): DOMRect | null {
  const r = el.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) return null;
  let x0 = Math.max(0, r.left), y0 = Math.max(0, r.top), x1 = Math.min(innerWidth, r.right), y1 = Math.min(innerHeight, r.bottom);
  for (let p = el.parentElement; p && p !== document.body && p !== document.documentElement; p = p.parentElement) {
    const cs = getComputedStyle(p);
    if (cs.display === 'none' || cs.visibility === 'hidden') return null;
    if (CLIPS.test(cs.overflowX + ' ' + cs.overflowY)) {
      const q = p.getBoundingClientRect();
      x0 = Math.max(x0, q.left); y0 = Math.max(y0, q.top); x1 = Math.min(x1, q.right); y1 = Math.min(y1, q.bottom);
    }
    if (cs.position === 'fixed') break;
  }
  if (x1 - x0 < 2 || y1 - y0 < 2) return null;
  return new DOMRect(x0, y0, x1 - x0, y1 - y0);
}
function enough(full: DOMRect, s: DOMRect) {
  const fa = full.width * full.height, sa = s.width * s.height;
  return sa >= fa * 0.6 || (fa > 40000 && s.width >= 60 && s.height >= 30);
}
/** Whether it is the topmost element at its center (skipping the tour layer itself; covered by a dialog/drawer doesn't count) */
function onTop(el: Element, s: DOMRect) {
  const pts = [[s.x + s.width / 2, s.y + s.height / 2], [s.x + Math.min(8, s.width / 3), s.y + Math.min(8, s.height / 3)]];
  return pts.some(([x, y]) => {
    const top = document.elementsFromPoint(x, y).find((e) => !e.closest('#hm-tour-host'));
    return !!top && (el.contains(top) || top.contains(el));
  });
}
function vis(e: Element | null): Hit | null {
  if (!e || !e.getClientRects().length) return null;
  if (!(e instanceof SVGElement) && (e as HTMLElement).offsetParent === null && getComputedStyle(e).position !== 'fixed') return null;
  const s = shownRect(e);
  if (!s || !enough(e.getBoundingClientRect(), s) || !onTop(e, s)) return null;
  return { el: e, rect: s };
}
function candidates(t: any): Element[] {
  if (!t) return [];
  if (t.sel) { try { return Array.from(document.querySelectorAll(t.sel)); } catch { return []; } }
  if (t.tour) return Array.from(document.querySelectorAll(`[data-tour="${t.tour}"]`));
  return [];
}
export function resolveTarget(t: any): Hit | null {
  if (!t) return null;
  if (Array.isArray(t)) { for (const x of t) { const h = resolveTarget(x); if (h) return h; } return null; }
  for (const e of candidates(t)) { const h = vis(e); if (h) return h; }
  return null;
}
export const hasCandidate = (t: any) => (Array.isArray(t) ? t : [t]).some((x) => !!x && candidates(x).length > 0);
function scrollBox(el: Element): HTMLElement | null {
  for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
    const cs = getComputedStyle(p);
    if (/(auto|scroll)/.test(cs.overflowY) && p.scrollHeight > p.clientHeight + 1) return p;
  }
  return null;
}
function tall(el: Element) { const h = el.getBoundingClientRect().height, box = scrollBox(el); return h > (box ? box.clientHeight : innerHeight) * 0.6; }
/** Target is on the page but scrolled away: scroll it into view (don't scroll if part of a tall container is visible) */
export function revealTarget(t: any): boolean {
  if (!t) return false;
  for (const x of Array.isArray(t) ? t : [t]) {
    if (resolveTarget(x)) return true;
    for (const e of candidates(x)) {
      if (!e.getClientRects().length) continue;
      const big = tall(e);
      if (big && shownRect(e)) return true;
      try { e.scrollIntoView({ block: big ? 'start' : 'center', inline: 'nearest' }); } catch { /* */ }
      if (resolveTarget(x) || (big && shownRect(e))) return true;
    }
  }
  return false;
}
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
export async function until(f: () => any, ms: number) { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (f()) return true; await sleep(80); } return !!f(); }

// ---------------- Loop: completion conditions ----------------
let timer: any = null;
function loop() {
  clearTimeout(timer);
  const tick = () => {
    if (!tour.on) return;
    const s = step();
    if (s && !tour.finished && !tour.busy) {
      if (!tour.ok && s.done && cond(s.done)) { tour.ok = true; tour.okAt = Date.now(); }
      if (tour.ok && s.manual === false && !tour.hold && Date.now() - tour.okAt > (s.delay ?? 1200)) next();
    }
    timer = setTimeout(tick, 400);
  };
  tick();
}

// ---------------- Mount on body (standalone mini Vue app, independent of the page) ----------------
let mounted = false;
export function ensureOverlay() {
  if (mounted) return;
  mounted = true;
  import('./TourOverlay.vue').then((m) => {
    const host = document.createElement('div');
    host.id = 'hm-tour-host';
    document.body.appendChild(host);
    createApp(m.default).mount(host);
  });
}

/** Page refresh: resume the last step */
export function resumeTour() {
  let st: any = null;
  try { st = JSON.parse(sessionStorage.getItem(KEY) || 'null'); } catch { /* */ }
  if (!st?.key) return;
  const def = guideDef(st.key);
  if (!def) { try { sessionStorage.removeItem(KEY); } catch { /* */ } return; }
  Object.assign(tour, { on: true, def, key: st.key, min: !!st.min, finished: false });
  ensureOverlay();
  go(st.i || 0);
  loop();
}

(window as any).__hmTour = Object.assign((window as any).__hmTour || {}, { tour, go, next, prev, startGuide, stopTour, resolveTarget, hasCandidate, guideDef, cond });
