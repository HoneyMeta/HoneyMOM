// Form config (docs/dev/config.md "Form Config"): groups and orders fields by the entity's form (from /api/meta), marks required / hidden / read-only by the current user's role.
import { $t } from '../i18n';
import { roleList } from './state';

const hit = (rs: string[] | undefined) => !!rs && (rs.includes('*') || rs.some((r) => roleList().includes(r)));

/** fields: fields to show in the form (system read-only ones already removed). Returns [{ title, fields: [{…f, req, roRole}] }]; without config, one group with an empty title. */
export function layoutGroups(entity: any, fields: any[]) {
  const cfg = entity?.form;
  const mark = (f: any) => ({ ...f, req: !!f.req || hit(cfg?.required?.[f.name]), roRole: hit(cfg?.readonly?.[f.name]) });
  const shown = fields.filter((f) => !hit(cfg?.hidden?.[f.name]));
  if (!cfg || !cfg.groups?.length) return [{ title: '', fields: shown.map(mark) }];
  const by = Object.fromEntries(shown.map((f) => [f.name, f]));
  const used = new Set<string>();
  const groups = cfg.groups.map((g: any) => ({ title: g.title, fields: (g.fields || []).filter((n: string) => by[n] && !used.has(n) && used.add(n)).map((n: string) => mark(by[n])) }))
    .filter((g: any) => g.fields.length);
  const rest = shown.filter((f) => !used.has(f.name));
  if (rest.length) groups.push({ title: $t('common.other'), fields: rest.map(mark) });
  return groups;
}

/** Before create / save: role-required fields left empty → returns the list of missing field names */
export function missingRequired(groups: any[], form: Record<string, any>) {
  return groups.flatMap((g) => g.fields).filter((f: any) => f.req && !f.auto && (form[f.name] === undefined || form[f.name] === null || form[f.name] === '')).map((f: any) => f.label);
}
