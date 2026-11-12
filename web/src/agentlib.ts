import { $t } from './i18n';
// Shared logic for the agent canvas: DSL <-> canvas conversion, branch outputs, available variables.
export type Meta = { types: Record<string, any>; categories: { key: string; label: string }[]; events: Record<string, { label: string; fields: string[] }>;
  roles: Record<string, string>; ops: Record<string, any>; tools: Record<string, string>; templates: any[]; query_kinds: Record<string, string> };

export const CAT_COLOR: Record<string, string> = { trigger: '#d99a16', data: '#2f7ad8', ai: '#8a4fd1', logic: '#4b5563', human: '#d9771c', action: '#c83a3a' };
export const HANDLE_LABEL: Record<string, string> = { approved: $t('common.approve'), rejected: $t('common.reject'), body: $t('agentlib.loopBody'), done: $t('common.done'), else: $t('agentlib.else'), other: $t('common.other') };

export function handlesOf(type: string, params: any): { id: string; label: string }[] | null {
  params = params || {};
  if (type === 'switch') return [...(params.cases || []).map((c: any) => ({ id: c.id, label: c.name || c.id })), { id: 'else', label: $t('agentlib.else') }];
  if (type === 'categorize') return [...(params.categories || []).map((c: any) => ({ id: c.id, label: c.name || c.id })), { id: 'other', label: $t('common.other') }];
  if (type === 'approval') return [{ id: 'approved', label: $t('common.approve') }, { id: 'rejected', label: $t('common.reject') }];
  if (type === 'loop') return [{ id: 'body', label: $t('agentlib.loopBody') }, { id: 'done', label: $t('common.done') }];
  return null;
}

export function defaults(meta: Meta, type: string) {
  const t = meta.types[type];
  const p: Record<string, any> = {};
  for (const s of [...t.params, ...(t.common || [])]) if (s.default !== undefined) p[s.key] = JSON.parse(JSON.stringify(s.default));
  return p;
}

const PREFIX: Record<string, string> = { mes_query: 'query', kb_search: 'kb', llm: 'llm', agent: 'agent', categorize: 'cls', extract: 'ext', switch: 'if', loop: 'loop',
  set_var: 'var', wait: 'wait', reply: 'reply', approval: 'approve', ask_user: 'ask', http: 'http', sql: 'sql' };
export function newId(type: string, existing: string[]) {
  const pre = PREFIX[type] || (type.startsWith('act_') ? 'act' : type);
  let n = 1;
  while (existing.includes(pre + n)) n++;
  return pre + n;
}

export function fromDsl(dsl: any) {
  const comps = dsl?.components || {};
  const pos = dsl?.graph?.nodes || {};
  const ids = Object.keys(comps);
  const nodes = ids.map((id, i) => ({
    id, type: 'agent', position: pos[id] ? { x: pos[id].x, y: pos[id].y } : { x: 60 + (i % 4) * 280, y: 80 + Math.floor(i / 4) * 160 },
    deletable: id !== 'begin', data: { type: comps[id].type, name: comps[id].name || id, params: comps[id].params || {}, status: '' },
  }));
  let edges: any[] = [];
  if (dsl?.graph?.edges?.length) edges = dsl.graph.edges.filter((e: any) => comps[e.source] && comps[e.target]).map((e: any) => mkEdge(e.source, e.target, e.sourceHandle || null));
  else for (const id of ids) for (const d of comps[id].downstream || []) if (comps[d.target]) edges.push(mkEdge(id, d.target, d.handle || null));
  return { nodes, edges };
}

export function mkEdge(source: string, target: string, handle: string | null) {
  return { id: `e-${source}-${target}-${handle || 'out'}`, source, target, sourceHandle: handle || undefined, label: handle ? HANDLE_LABEL[handle] || '' : undefined,
    type: 'smoothstep', animated: false };
}

export function toDsl(nodes: any[], edges: any[]) {
  const components: Record<string, any> = {};
  const gnodes: Record<string, any> = {};
  for (const n of nodes) {
    components[n.id] = { type: n.data.type, name: n.data.name, params: n.data.params, upstream: [], downstream: [] };
    gnodes[n.id] = { x: Math.round(n.position.x), y: Math.round(n.position.y) };
  }
  const gedges: any[] = [];
  for (const e of edges) {
    if (!components[e.source] || !components[e.target]) continue;
    const h = e.sourceHandle || null;
    components[e.source].downstream.push({ target: e.target, handle: h });
    if (!components[e.target].upstream.includes(e.source)) components[e.target].upstream.push(e.source);
    gedges.push({ id: e.id, source: e.source, target: e.target, sourceHandle: h });
  }
  return { components, graph: { nodes: gnodes, edges: gedges } };
}

export function ancestors(id: string, edges: any[]) {
  const up: Record<string, string[]> = {};
  for (const e of edges) (up[e.target] ||= []).push(e.source);
  const seen = new Set<string>();
  const st = [...(up[id] || [])];
  while (st.length) {
    const x = st.pop()!;
    if (seen.has(x)) continue;
    seen.add(x);
    st.push(...(up[x] || []));
  }
  return seen;
}

const SYS = [['query', $t('agentlib.userQuestionInput')], ['now', $t('common.dataClock')], ['date', $t('common.date')], ['shift', $t('agentlib.currentShift')], ['user', $t('agentlib.runBy')], ['run_id', $t('agentlib.runId')], ['agent_name', $t('agentlib.agentName')],
  ['feedback', $t('agentlib.rejectionReason')], ['mode', $t('agentlib.runMode')], ['trigger', $t('agentlib.trigger')]];

function payloadKeys(params: any) {
  const p = params?.payload;
  try { const o = typeof p === 'string' ? JSON.parse(p) : p; return o && typeof o === 'object' ? Object.keys(o) : []; } catch { return []; }
}

export function outputsOf(meta: Meta, n: any): { path: string; label: string }[] {
  const t = meta.types[n.data.type];
  const p = n.data.params || {};
  if (n.data.type === 'begin') {
    if (p.trigger === 'event' && meta.events[p.event]) return meta.events[p.event].fields.map((f) => ({ path: f, label: f }));
    return [...(p.trigger === 'chat' ? [{ path: 'query', label: $t('agentlib.chatInput') }] : []), ...(p.inputs || []).filter((f: any) => f.key).map((f: any) => ({ path: f.key, label: f.label || f.key }))];
  }
  if (n.data.type === 'extract') return [...(p.fields || []).filter((f: any) => f.key).map((f: any) => ({ path: f.key, label: f.label || f.key })), { path: 'data', label: $t('agentlib.allFields') }];
  if (n.data.type === 'ask_user') return [...(p.fields || []).filter((f: any) => f.key).map((f: any) => ({ path: f.key, label: f.label || f.key })), { path: 'values', label: $t('common.all') }];
  if (n.data.type === 'approval') return [{ path: 'decision', label: $t('common.result') }, { path: 'reason', label: $t('agentlib.rejectionReasonComments') }, { path: 'by', label: $t('agentlib.approver') }, { path: 'payload', label: $t('agentlib.approvedContent') },
    ...payloadKeys(p).map((k) => ({ path: 'payload.' + k, label: $t('agentlib.approved') + k }))];
  if (n.data.type === 'set_var') return (p.assignments || []).filter((a: any) => a.key).map((a: any) => ({ path: a.key, label: a.key }));
  return (t?.outputs || []).filter((o: string) => !/^\s*[(\uff08]/.test(o)).map((o: string) => ({ path: o, label: o }));
}

export function varsFor(meta: Meta, nodeId: string, nodes: any[], edges: any[]) {
  const anc = ancestors(nodeId, edges);
  const groups: { label: string; items: { ref: string; label: string }[] }[] = [];
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const order = nodes.filter((n) => anc.has(n.id) || (n.id === nodeId && n.data.type === 'loop' && false));
  for (const n of order) {
    const outs = outputsOf(meta, n);
    if (!outs.length) continue;
    groups.push({ label: $t('common.n01', [n.data.name, n.id]), items: outs.map((o) => ({ ref: `{${n.id}@${o.path}}`, label: o.label })) });
  }
  const begin = byId.begin;
  if (begin) {
    const ins = outputsOf(meta, begin);
    if (ins.length) groups.push({ label: $t('agentlib.inputs'), items: ins.map((o) => ({ ref: `{inputs.${o.path}}`, label: o.label })) });
  }
  const vars = nodes.filter((n) => n.data.type === 'set_var').flatMap((n) => (n.data.params.assignments || []).filter((a: any) => a.key).map((a: any) => a.key));
  if (vars.length) groups.push({ label: $t('agentlib.variables'), items: [...new Set(vars)].map((k) => ({ ref: `{vars.${k}}`, label: k })) });
  groups.push({ label: $t('agentlib.system'), items: SYS.map(([k, l]) => ({ ref: `{sys.${k}}`, label: l })) });
  return groups;
}

export function summaryOf(meta: Meta, data: any): string {
  const p = data.params || {};
  const t = data.type;
  if (t === 'begin') {
    const tr = { chat: $t('agentlib.chat'), manual: $t('agentlib.manualForm'), schedule: $t('common.scheduled'), event: $t('common.event'), webhook: 'Webhook' }[p.trigger as string] || $t('common.manual');
    if (p.trigger === 'event') return $t('common.n012', [tr, meta.events[p.event]?.label || $t('agentlib.notSelected')]);
    if (p.trigger === 'schedule') return p.schedule_mode === 'shift' ? $t('agentlib.n0EveryShift1', [tr, p.shift_at === 'start' ? $t('common.start') : $t('common.end')]) : $t('common.n012', [tr, p.cron || '']);
    return tr;
  }
  if (t === 'mes_query') return meta.query_kinds[p.entity] || '';
  if (t === 'kb_search' || t === 'llm' || t === 'agent') return String(p.query || p.prompt || '').replace(/\s+/g, ' ').slice(0, 40);
  if (t === 'approval' || t === 'ask_user') return $t('agentlib.handledBy0', [meta.roles[p.assignee_role] || p.assignee_role || '']);
  if (t === 'switch') return $t('agentlib.n0ConditionBranches', [(p.cases || []).length]);
  if (t === 'categorize') return (p.categories || []).map((c: any) => c.name).join(' / ');
  if (t === 'extract') return (p.fields || []).map((f: any) => f.label || f.key).join($t('common.k42'));
  if (t === 'reply') return String(p.content || '').slice(0, 40);
  if (t === 'loop') return String(p.items || '');
  if (t === 'wait') return $t('agentlib.n0S', [p.seconds || 0]);
  if (t === 'set_var') return (p.assignments || []).map((a: any) => a.key).join($t('common.k42'));
  if (t?.startsWith('act_')) return (meta.types[t].risk === 'high' ? (p.require_approval === false ? $t('agentlib.approvedUpstream') : $t('agentlib.approveBeforeRunning')) : $t('agentlib.lowRisk')) + (p.channel ? ' · ' + ({ inbox: $t('agentlib.inAppMessage'), board: $t('agentlib.board'), webhook: 'Webhook' } as any)[p.channel] : '');
  return '';
}

export function shown(spec: any, params: any) {
  for (const [k, vals] of Object.entries(spec.show || {})) if (!(vals as any[]).includes(params[k])) return false;
  return true;
}
