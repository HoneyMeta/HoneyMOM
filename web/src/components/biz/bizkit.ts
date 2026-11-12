// Shared by business pages: production report submit (idempotent + retry), source text, gate error messages.
import { $t, lang } from '../../i18n';
import { ElMessage } from 'element-plus';
import { session } from '../../api';

export const SRC_CN: Record<string, string> = { auto: $t('common.machineAuto'), manual: $t('common.manual2'), mobile: $t('bizkit.mobileScan'), scan: $t('bizkit.barcodeScanner'), terminal: $t('common.workstationTerminal') };
export const sourceText = (r: any) => (r?.source === 'auto' ? $t('bizkit.machineAutoAggregatedEvery0', [r.interval_min || 60]) : SRC_CN[r?.source] || r?.source || $t('common.manual2'));
export const GATE_CN: Record<string, string> = { fai: $t('bizkit.firstArticleNotReleased'), hold: $t('common.lotOnHold'), sequence: $t('bizkit.operationSequence'), over: $t('bizkit.exceedsPlan'), status: $t('bizkit.workOrderStatus') };

export function newKey(prefix = 'rp') {
  const c: any = (globalThis as any).crypto;
  const id = c?.randomUUID ? c.randomUUID() : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  return `${prefix}-${id}`;
}

export class GateError extends Error { constructor(msg: string, public gate: string, public status: number) { super(msg); } }

/** POST with an idempotency key: on network failure retries automatically with the same key (server records each key once). A 409 gate throws GateError. */
export async function postIdem(path: string, body: any, key: string, tries = 3): Promise<any> {
  let last: any;
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(path, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Lang': lang, ...(session.token ? { 'X-Lang': lang, Authorization: 'Bearer ' + session.token } : {}) },
        body: JSON.stringify({ ...body, idem_key: key }),
      });
      const j = await r.json().catch(() => ({}));
      if (r.ok) return j;
      const msg = typeof j.detail === 'string' ? j.detail : r.statusText;
      if (r.status === 409) throw new GateError(msg, r.headers.get('X-Gate') || '', 409);
      throw new GateError(msg, '', r.status);
    } catch (e: any) {
      if (e instanceof GateError) throw e;
      last = e;                                   // Network down: wait a moment and retry with the same key
      await new Promise((ok) => setTimeout(ok, 400 * (i + 1)));
    }
  }
  throw last || new Error($t('bizkit.networkUnavailable'));
}

/** Report production: blocks repeat clicks while submitting; returns the result or null (error already shown). */
export async function submitReport(body: any, key: string) {
  try {
    const r = await postIdem('/api/production/report', body, key);
    if (r.duplicate) ElMessage.info(r.msg || $t('bizkit.thisReportWasAlreadySubmitted')); else ElMessage.success(r.msg || $t('bizkit.reported'));
    return r;
  } catch (e: any) {
    ElMessage({ type: e.status === 409 ? 'warning' : 'error', message: (e.gate ? $t('bizkit.n0', [GATE_CN[e.gate] || $t('bizkit.cannotReport')]) : '') + (e.message || $t('bizkit.reportingFailed')), duration: 5000, showClose: true });
    return null;
  }
}
