import { $t, lang } from '../../i18n';
// Shared by the Connectivity Center: read files as base64, display names for colors / purposes / protocols
export const COLOR_CN: Record<string, string> = { red: $t('checkTab.unreadable'), yellow: $t('onboardCommon.notInTagList'), orange: $t('onboardCommon.outOfRangeAbnormal'), green: $t('common.normal'), grey: $t('onboardCommon.ignored') };
export const COLOR_TONE: Record<string, 'bad' | 'warn' | 'amber' | 'ok' | 'neutral'> = { red: 'bad', yellow: 'warn', orange: 'amber', green: 'ok', grey: 'neutral' };
export const ROLE_CN: Record<string, string> = { state: $t('onboardCommon.runState'), alarm: $t('common.alarm'), count: $t('onboardCommon.counter'), power: $t('common.power'), energy: $t('onboardCommon.energy'), load: $t('onboardCommon.load'), temp: $t('onboardCommon.temperature'), quality: $t('onboardCommon.qualityValue'),
  action: $t('onboardCommon.actionSignal'), step: $t('onboardCommon.programBlockNo'), param: $t('onboardCommon.setpoint'), other: $t('common.other') };
export const ROLES = Object.keys(ROLE_CN);
export const PROTO_CN: Record<string, string> = { mqtt: 'MQTT', opcua: 'OPC UA', modbus: 'Modbus TCP', sparkplug: 'Sparkplug B', http: $t('onboardCommon.httpPush'), sql: $t('onboardCommon.stagingDatabase') };
export const STATUS_CN: Record<string, string> = { confirmed: $t('common.confirmed'), pending: $t('common.toConfirm'), ignored: $t('onboardCommon.ignored') };

export function fileB64(f: File): Promise<string> {
  return new Promise((ok, bad) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(',', 2)[1] || '');
    r.onerror = () => bad(r.error);
    r.readAsDataURL(f);
  });
}

export function pickFile(accept: string): Promise<File | null> {
  return new Promise((ok) => {
    const i = document.createElement('input');
    i.type = 'file'; i.accept = accept;
    i.onchange = () => ok(i.files?.[0] || null);
    i.click();
  });
}

/** Download with the login token (API needs an Authorization header, so location.href won't do) */
export async function download(url: string, name: string, token: string) {
  const r = await fetch(url, { headers: { 'X-Lang': lang, Authorization: 'Bearer ' + token } });
  if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || r.statusText); }
  const b = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(b); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}
