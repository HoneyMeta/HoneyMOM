// Frontend permissions: same rules as backend honeymom/perms.py (see docs/dev/foundation.md "Permissions").
//   can('quality.ncr.update')            whether the current user has this permission
//   entPerm('ncr', 'update')            entity permission (module key from /api/meta) → 'quality.ncr.update'
//   pagePerm('/quality/spc')            page permission: /e/<entity> → entity read; / → page.home; others → page.a.b
//   v-perm="'quality.ncr.close'"        hidden without permission; v-perm:disable="…" disables instead (greyed + tooltip); value may be an array (any one suffices)
// Backend /api/me returns perms = [[role 1 patterns…], [role 2 patterns…]]: * wildcard, leading ! excludes within that role.
import { $t } from './i18n';
import type { App, Directive } from 'vue';
import { session } from './api';

const reCache = new Map<string, RegExp>();
function toRe(p: string) {
  let r = reCache.get(p);
  if (!r) {
    r = new RegExp('^' + p.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*') + '$');
    reCache.set(p, r);
  }
  return r;
}

export function grants(patterns: string[], perm: string) {
  let pos = false;
  for (const p of patterns || []) {
    if (!p) continue;
    if (p.startsWith('!')) { if (toRe(p.slice(1)).test(perm)) return false; }
    else if (!pos && toRe(p).test(perm)) pos = true;
  }
  return pos;
}

/** Whether the current user has the permission (array = any). Not logged in = no. */
export function can(perm: string | string[] | undefined | null): boolean {
  if (!perm) return true;
  const roles: string[][] = session.user?.perms || [];
  const list = Array.isArray(perm) ? perm : [perm];
  return list.some((p) => roles.some((r) => grants(r, p)));
}

export function entPerm(entity: string, op: string) {
  const e = session.meta?.entities?.find((x: any) => x.name === entity);
  return `${e?.mkey || 'unknown'}.${entity}.${op}`;
}

export function pagePerm(path: string) {
  const p = (path || '/').split('?')[0].replace(/^\/+|\/+$/g, '');
  if (p.startsWith('e/')) return entPerm(p.slice(2).split('/')[0], 'read');
  return 'page.' + (p ? p.replace(/\//g, '.') : 'home');
}

/** Permission for a menu item / route: explicit perm first, otherwise inferred from the path. */
export function navPerm(it: { path: string; perm?: string }) { return it.perm || pagePerm(it.path); }

function apply(el: HTMLElement, value: any, disable: boolean) {
  const ok = can(value);
  if (disable) {
    el.toggleAttribute('disabled', !ok);
    el.classList.toggle('is-disabled', !ok);
    if (!ok) el.setAttribute('title', $t('perm.noPermission')); else if (el.getAttribute('title') === $t('perm.noPermission')) el.removeAttribute('title');
    (el as any).__permBlock = !ok;
  } else {
    if ((el as any).__permDisplay === undefined) (el as any).__permDisplay = el.style.display;
    el.style.display = ok ? (el as any).__permDisplay : 'none';
  }
}

export const vPerm: Directive = {
  mounted(el, b) {
    apply(el, b.value, b.arg === 'disable');
    if (b.arg === 'disable') el.addEventListener('click', (ev: Event) => { if ((el as any).__permBlock) { ev.stopImmediatePropagation(); ev.preventDefault(); } }, true);
  },
  updated(el, b) { apply(el, b.value, b.arg === 'disable'); },
};

export function installPerm(app: App) {
  app.directive('perm', vPerm);
  app.config.globalProperties.$can = can;
}
