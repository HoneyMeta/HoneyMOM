<template>
  <div class="page erp-int">
    <HmPageHead :title="$t('common.erpIntegration')" :sub="$t('erpIntegration.builtInExternalErpMode')">
      <el-button @click="openDocs"><el-icon><Document /></el-icon>{{ $t('erpIntegration.apiDocsApiDocs') }}</el-button>
      <el-button v-perm="'system.apikey.manage'" @click="router.push('/system/apikeys')">{{ $t('common.apiKeys') }}</el-button>
      <el-button v-perm:disable="'erp.sync.run'" type="primary" data-test="int-dispatch" @click="dispatch"><el-icon><Promotion /></el-icon>{{ $t('erpIntegration.deliverNow') }}</el-button>
      <template #meta><HmTag :tone="mode.mode === 'external' ? 'amber' : 'ok'" data-test="int-mode">{{ mode.mode === 'external' ? $t('erpIntegration.externalErpMode') : $t('erpIntegration.builtInErpMode') }}</HmTag></template>
    </HmPageHead>
    <el-tabs v-model="tab" class="tabs" data-tour="int-tabs">
      <el-tab-pane :label="$t('erpIntegration.modeOwnership')" name="mode" /><el-tab-pane :label="$t('erpIntegration.queueFailures')" name="queue" /><el-tab-pane label="Webhook" name="hook" />
      <el-tab-pane :label="$t('onboardCommon.stagingDatabase')" name="stage" /><el-tab-pane :label="$t('erpIntegration.kingdeeYonyou')" name="conn" />
    </el-tabs>

    <template v-if="tab === 'mode'">
      <HmCard :title="$t('erpIntegration.erpMode')" :sub="$t('erpIntegration.inExternalErpModeErp')" data-tour="int-mode-card">
        <el-radio-group v-model="mf.mode" data-test="mode-radio">
          <el-radio-button value="builtin">{{ $t('erpIntegration.builtInErpMesManages') }}</el-radio-button>
          <el-radio-button value="external">{{ $t('erpIntegration.externalErpKingdeeYonyouOther') }}</el-radio-button>
        </el-radio-group>
        <div class="own mt">
          <div v-for="(lab, k) in mode.own_keys" :key="k" class="own-row">
            <span class="own-lab">{{ lab }}</span>
            <el-radio-group v-model="mf.owner[k]" size="small" :disabled="mf.mode !== 'external'">
              <el-radio-button value="mes">{{ $t('erpIntegration.mesMaster') }}</el-radio-button><el-radio-button value="erp">{{ $t('erpIntegration.erpMaster') }}</el-radio-button>
            </el-radio-group>
          </div>
        </div>
        <h4>{{ $t('erpIntegration.transactionsWrittenBackToErp') }}</h4>
        <el-checkbox v-for="(lab, k) in mode.postback_keys" :key="k" v-model="mf.postback[k]" :disabled="mf.mode !== 'external'">{{ lab }}</el-checkbox>
        <h4>{{ $t('onboardCommon.stagingDatabase') }}</h4>
        <div class="stg">
          <el-checkbox v-model="mf.erp_staging_in" :disabled="mf.mode !== 'external'">{{ $t('erpIntegration.readDataWrittenByErp') }}</el-checkbox>
          <el-checkbox v-model="mf.erp_staging_out" :disabled="mf.mode !== 'external'">{{ $t('erpIntegration.writeEventsToErp') }}</el-checkbox>
          <el-input v-model="mf.staging_url" :placeholder="$t('erpIntegration.stagingDbConnectionEmptyErp')" class="url" />
        </div>
        <div class="mt"><el-button type="primary" v-perm:disable="'erp.mode.manage'" data-test="mode-save" @click="saveMode">{{ $t('common.save') }}</el-button></div>
      </HmCard>
    </template>

    <template v-else-if="tab === 'queue'">
      <div class="grid g4">
        <HmKpi :label="$t('erpIntegration.eventsLast24H')" :value="mon?.events_24h" format="int" :unit="$t('common.k2047')" />
        <HmKpi :label="$t('erpIntegration.pending')" :value="count('pending') + count('retry')" format="int" :unit="$t('common.k2047')" :status="count('retry') ? 'warn' : ''" />
        <HmKpi :label="$t('erpIntegration.failedNoMoreRetries')" :value="count('dead')" format="int" :unit="$t('common.k2047')" :status="count('dead') ? 'bad' : 'ok'" />
        <HmKpi :label="$t('erpIntegration.delivered')" :value="count('sent')" format="int" :unit="$t('common.k2047')" />
      </div>
      <HmCard :title="$t('erpIntegration.channel')" flush class="mt" data-tour="int-channels">
        <el-table :data="mon?.channels || []" size="small">
          <template #empty><HmEmpty compact plain :title="$t('erpIntegration.noDeliveriesYet')" /></template>
          <el-table-column prop="name" :label="$t('erpIntegration.channel')" min-width="200" />
          <el-table-column v-for="s in ['pending', 'retry', 'sent', 'dead']" :key="s" :label="DST[s]" align="right" min-width="90">
            <template #default="{ row }"><span class="hm-num" :class="{ bad: s === 'dead' && row[s] }">{{ row[s] || 0 }}</span></template></el-table-column>
        </el-table>
      </HmCard>
      <HmCard :title="$t('erpIntegration.failedRetrying')" flush class="mt" data-tour="int-fail">
        <template #actions><el-button size="small" v-perm:disable="'erp.sync.run'" :disabled="!mon?.failures?.length" @click="retryAll">{{ $t('erpIntegration.retryAll') }}</el-button></template>
        <el-table :data="mon?.failures || []" size="small" max-height="360" data-test="int-failures">
          <template #empty><HmEmpty compact plain :title="$t('erpIntegration.noFailedDeliveries')" /></template>
          <el-table-column prop="event_name" :label="$t('common.event')" min-width="140" /><el-table-column prop="doc_code" :label="$t('erpArAp.document')" min-width="140" />
          <el-table-column prop="channel" :label="$t('erpIntegration.channel')" min-width="130" />
          <el-table-column :label="$t('common.status')" min-width="80"><template #default="{ row }"><HmTag :tone="statusTone(row.status)">{{ DST[row.status] }}</HmTag></template></el-table-column>
          <el-table-column :label="$t('common.count')" align="right" min-width="60"><template #default="{ row }"><span class="hm-num">{{ row.attempts }}</span></template></el-table-column>
          <el-table-column prop="last_error" :label="$t('common.reason')" min-width="260" />
          <el-table-column :label="$t('erpIntegration.next')" min-width="130"><template #default="{ row }"><span class="hm-num">{{ fmtTime(row.next_try) }}</span></template></el-table-column>
          <el-table-column label="" min-width="70"><template #default="{ row }"><el-button link size="small" type="primary" v-perm="'erp.sync.run'" @click="retry(row)">{{ $t('common.retry') }}</el-button></template></el-table-column>
        </el-table>
      </HmCard>
      <HmCard :title="$t('erpIntegration.syncLog')" flush class="mt">
        <el-table :data="mon?.logs || []" size="small" max-height="300">
          <template #empty><HmEmpty compact plain :title="$t('erpIntegration.noSyncsYet')" /></template>
          <el-table-column prop="channel" :label="$t('erpIntegration.channel')" min-width="140" /><el-table-column prop="entity" :label="$t('common.object')" min-width="100" />
          <el-table-column :label="$t('erpIntegration.totalSucceededSkippedFailed')" min-width="170"><template #default="{ row }"><span class="hm-num">{{ row.total }} / {{ row.ok }} / {{ row.skipped }} / {{ row.failed }}</span></template></el-table-column>
          <el-table-column :label="$t('common.time')" min-width="140"><template #default="{ row }"><span class="hm-num">{{ fmtTime(row.started_at) }}</span></template></el-table-column>
          <el-table-column prop="by" :label="$t('common.operator')" min-width="100" />
        </el-table>
      </HmCard>
    </template>

    <template v-else-if="tab === 'hook'">
      <HmCard :title="$t('erpIntegration.newWebhook')" :sub="$t('erpIntegration.signatureXHmSignatureSha256')" data-tour="int-hook-new">
        <div class="hookf">
          <el-input v-model="hf.name" :placeholder="$t('common.name')" class="hn" />
          <el-input v-model="hf.url" placeholder="https://erp.example.com/mes/hook" class="hu" data-test="hook-url" />
          <el-select v-model="hf.events" multiple collapse-tags :placeholder="$t('erpIntegration.subscribedEventsEmptyAll')" class="he"><el-option v-for="(lab, k) in mode.events" :key="k" :label="lab" :value="k" /></el-select>
          <el-button type="primary" :disabled="!hf.url" v-perm:disable="'erp.webhook.manage'" data-test="hook-create" @click="createHook">{{ $t('common.create') }}</el-button>
        </div>
        <el-alert v-if="newSecret" type="success" :closable="false" class="mt" :title="$t('erpIntegration.signingSecretShownOnlyThis', [newSecret])" />
      </HmCard>
      <HmCard :title="$t('erpIntegration.subscriptions')" flush class="mt" data-tour="int-hooks">
        <el-table :data="hooks" size="small">
          <template #empty><HmEmpty compact plain :title="$t('erpIntegration.noWebhooksYet')" /></template>
          <el-table-column prop="name" :label="$t('common.name')" min-width="130" /><el-table-column prop="url" label="URL" min-width="240" />
          <el-table-column :label="$t('common.event')" min-width="180"><template #default="{ row }">{{ (row.events || []).map((e: string) => mode.events?.[e] || e).join($t('common.k42')) }}</template></el-table-column>
          <el-table-column :label="$t('adminApiKeys.key')" min-width="90"><template #default="{ row }"><span class="hm-mono">{{ row.secret_hint }}</span></template></el-table-column>
          <el-table-column :label="$t('common.enable')" min-width="70"><template #default="{ row }"><el-switch :model-value="row.enabled" @change="(v: any) => updHook(row, { enabled: v })" /></template></el-table-column>
          <el-table-column :label="$t('common.latest')" min-width="200"><template #default="{ row }"><span class="small">{{ row.last_error || (row.last_ok ? $t('erpIntegration.succeeded') + fmtTime(row.last_ok) : '—') }}</span></template></el-table-column>
          <el-table-column label="" min-width="170"><template #default="{ row }">
            <el-button link size="small" type="primary" @click="testHook(row)">{{ $t('erpIntegration.test') }}</el-button>
            <el-button link size="small" @click="rotate(row)">{{ $t('erpIntegration.rotateSecret') }}</el-button>
            <el-button link size="small" type="danger" @click="delHook(row)">{{ $t('common.delete') }}</el-button>
          </template></el-table-column>
        </el-table>
      </HmCard>
    </template>

    <template v-else-if="tab === 'stage'">
      <div class="grid g2">
        <HmCard :title="$t('erpIntegration.erpMesErpStgIn')" :sub="mon?.staging_separate ? $t('erpIntegration.separateStagingDb') : $t('erpIntegration.tablesInTheMesDb')" data-tour="int-stage-in">
          <div class="cnts"><span v-for="(n, s) in mon?.staging_in || {}" :key="s"><HmTag :tone="statusTone(String(s))">{{ SST[s] || s }} {{ n }}</HmTag></span><span v-if="!Object.keys(mon?.staging_in || {}).length" class="muted">{{ $t('erpIntegration.noDataYet') }}</span></div>
          <el-button class="mt" type="primary" v-perm:disable="'erp.sync.run'" data-test="stage-sync" @click="syncStage">{{ $t('erpIntegration.syncNow') }}</el-button>
        </HmCard>
        <HmCard :title="$t('erpIntegration.mesErpErpStgOut')" :sub="$t('erpIntegration.erpReadsRowsWithStatus')">
          <div class="cnts"><span v-for="(n, s) in mon?.staging_out || {}" :key="s"><HmTag :tone="statusTone(String(s))">{{ SST[s] || s }} {{ n }}</HmTag></span><span v-if="!Object.keys(mon?.staging_out || {}).length" class="muted">{{ $t('erpIntegration.noDataYet') }}</span></div>
        </HmCard>
      </div>
      <HmCard :title="$t('erpIntegration.recordsWithSyncErrors')" flush class="mt">
        <el-table :data="mon?.staging_errors || []" size="small" max-height="360">
          <template #empty><HmEmpty compact plain :title="$t('erpIntegration.noErrorRecords')" /></template>
          <el-table-column prop="id" :label="$t('erpIntegration.rowNo')" min-width="70" /><el-table-column prop="entity" :label="$t('common.object')" min-width="110" /><el-table-column prop="ext_key" :label="$t('erpIntegration.externalCode')" min-width="140" />
          <el-table-column prop="error" :label="$t('common.reason')" min-width="280" /><el-table-column prop="attempts" :label="$t('common.count')" min-width="60" />
          <el-table-column label="" min-width="70"><template #default="{ row }"><el-button link size="small" type="primary" v-perm="'erp.sync.run'" @click="retryStage(row)">{{ $t('common.retry') }}</el-button></template></el-table-column>
        </el-table>
      </HmCard>
    </template>

    <template v-else>
      <div class="grid g3" data-tour="int-conns">
        <HmCard v-for="k in Object.keys(conn.kinds || {})" :key="k" :title="conn.kinds[k]" :sub="connOf(k)?.last_sync ? $t('erpIntegration.lastSync01', [fmtTime(connOf(k).last_sync), connOf(k).last_status]) : $t('erpIntegration.notConfigured')">
          <el-form label-position="top" class="cf">
            <el-form-item :label="$t('common.address')"><el-input v-model="cf[k].base_url" :placeholder="PH[k]" /></el-form-item>
            <el-form-item :label="$t('erpIntegration.configJson')"><el-input v-model="cf[k].config" type="textarea" :rows="5" class="mono" /></el-form-item>
            <el-form-item :label="k === 'kingdee' ? $t('erpIntegration.passwordAppSecretLeaveEmpty') : $t('erpIntegration.appSecretAppSecretLeave')"><el-input v-model="cf[k].secret" type="password" show-password /></el-form-item>
            <el-checkbox v-model="cf[k].enabled">{{ $t('erpIntegration.enabledPushEventsBackIn') }}</el-checkbox>
          </el-form>
          <div class="cbtn">
            <el-button size="small" type="primary" v-perm:disable="'erp.connector.manage'" @click="saveConn(k)">{{ $t('common.save') }}</el-button>
            <el-button size="small" :disabled="!connOf(k)" @click="testConn(k)">{{ $t('erpIntegration.testConnection') }}</el-button>
            <el-button size="small" :disabled="!connOf(k)" @click="pullConn(k)">{{ $t('erpIntegration.pullMasterDataOrders') }}</el-button>
          </div>
          <div v-if="cres[k]" class="small cres">{{ cres[k] }}</div>
        </HmCard>
      </div>
      <p class="muted small mt">{{ $t('erpIntegration.connectorsHaveOnlyBeenVerified') }}</p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { onMounted, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { del, get, post, put, tok } from '../api';
import { fmtTime } from '../design/format';
import { statusTone } from '../erp/docs';

const router = useRouter();
const DST: Record<string, string> = { pending: $t('erpIntegration.pending'), retry: $t('erpIntegration.retrying'), sent: $t('erpIntegration.delivered'), dead: $t('common.failed'), sending: $t('erpIntegration.delivering') };
const SST: Record<string, string> = { new: $t('erpDocs.new'), done: $t('common.resolved'), skipped: $t('erpIntegration.unchangedSkipped'), error: $t('common.error2'), acked: $t('erpIntegration.erpAcknowledged'), read: $t('erpIntegration.erpRead') };
const PH: Record<string, string> = { kingdee: $t('erpIntegration.httpKingdeeServerK3cloud'), yonyou_u8: 'https://api.yonyouup.com', yonsuite: 'https://api.diwork.com' };
const CFG0: Record<string, any> = { kingdee: { acct_id: '', username: '', org: '100', wh_map: {} }, yonyou_u8: { from_account: '', to_account: '', app_key: '', wh_map: {} },
  yonsuite: { app_key: '', org: '', wh_map: {} } };
const tab = ref('mode'), mode = ref<any>({ own_keys: {}, postback_keys: {}, events: {} }), mon = ref<any>(null), hooks = ref<any[]>([]), newSecret = ref('');
const mf = reactive<any>({ mode: 'builtin', owner: {}, postback: {}, erp_staging_in: false, erp_staging_out: false, staging_url: '' });
const hf = reactive<any>({ name: '', url: '', events: [] });
const conn = ref<any>({ items: [], kinds: {} }), cf = reactive<any>({}), cres = reactive<any>({});
const count = (s: string) => (mon.value?.channels || []).reduce((a: number, c: any) => a + (c[s] || 0), 0);
const connOf = (k: string) => conn.value.items.find((x: any) => x.kind === k);

async function loadMode() {
  mode.value = await get('/api/erp/mode');
  Object.assign(mf, { mode: mode.value.mode, owner: { ...mode.value.owner }, postback: { ...mode.value.postback } });
  if (mode.value.mode !== 'external') for (const k of Object.keys(mf.owner)) mf.owner[k] = ['work_order'].includes(k) ? 'mes' : 'erp';
  try { const s = await get('/api/erp/settings', true); Object.assign(mf, { erp_staging_in: s.erp_staging_in === '1', erp_staging_out: s.erp_staging_out === '1', staging_url: s.erp_staging_url }); } catch { /* no monitoring permission */ }
}
async function saveMode() { await put('/api/erp/mode', { ...mf }); ElMessage.success($t('common.saved')); loadMode(); }
async function loadMon() { try { mon.value = await get('/api/erp/monitor', true); } catch { mon.value = null; } }
async function dispatch() { const r = await post('/api/erp/dispatch', {}); ElMessage.success($t('erpIntegration.collected0Delivered1Failed', [r.collected, r.sent, r.failed + r.dead])); loadMon(); }
async function retry(row: any) { await post(`/api/erp/deliveries/${row.id}/retry`, {}); loadMon(); }
async function retryAll() { const r = await post('/api/erp/deliveries/retry_failed', {}); ElMessage.success($t('erpIntegration.reQueued0', [r.requeued])); loadMon(); }
async function loadHooks() { try { hooks.value = await get('/api/erp/webhooks', true); } catch { hooks.value = []; } }
async function createHook() { const r = await post('/api/erp/webhooks', { ...hf, events: hf.events.length ? hf.events : ['*'] }); newSecret.value = r.secret; hf.url = ''; hf.name = ''; loadHooks(); }
async function updHook(row: any, v: any) { await put(`/api/erp/webhooks/${row.id}`, v); loadHooks(); }
async function rotate(row: any) { const r = await put(`/api/erp/webhooks/${row.id}`, { rotate: true }); newSecret.value = r.secret; loadHooks(); }
async function delHook(row: any) { try { await ElMessageBox.confirm($t('erpIntegration.delete0', [row.name]), $t('erpIntegration.deleteWebhook'), { type: 'warning' }); } catch { return; } await del(`/api/erp/webhooks/${row.id}`); loadHooks(); }
async function testHook(row: any) { const r = await post(`/api/erp/webhooks/${row.id}/test`, {}); r.ok ? ElMessage.success($t('erpIntegration.testSucceeded')) : ElMessage.error($t('erpIntegration.testFailed') + (r.error || r.code)); loadHooks(); }
async function syncStage() { const r = await post('/api/erp/staging/sync', {}); ElMessage.success($t('erpIntegration.processed0Skipped1Errors', [r.done, r.skipped, r.error])); loadMon(); }
async function retryStage(row: any) { await post(`/api/erp/staging/${row.id}/retry`, {}); loadMon(); }
async function loadConn() {
  try { conn.value = await get('/api/erp/connectors', true); } catch { conn.value = { items: [], kinds: {} }; return; }
  for (const k of Object.keys(conn.value.kinds)) {
    const c = connOf(k);
    cf[k] = { base_url: c?.base_url || '', config: JSON.stringify(c?.config || CFG0[k], null, 1), secret: '', enabled: !!c?.enabled };
  }
}
async function saveConn(k: string) {
  let config: any;
  try { config = JSON.parse(cf[k].config || '{}'); } catch { ElMessage.error($t('erpIntegration.configIsNotValidJson')); return; }
  const body: any = { kind: k, name: conn.value.kinds[k], base_url: cf[k].base_url, config, enabled: cf[k].enabled };
  if (cf[k].secret) body.secret = k === 'kingdee' && !config.app_id ? { password: cf[k].secret } : { app_secret: cf[k].secret };
  await put(`/api/erp/connectors/${connOf(k)?.code || k}`, body); ElMessage.success($t('common.saved')); loadConn();
}
async function testConn(k: string) { const r = await post(`/api/erp/connectors/${connOf(k).code}/test`, {}); cres[k] = r.ok ? $t('erpIntegration.connectedSuccessfully') : $t('erpIntegration.failed') + r.error; }
async function pullConn(k: string) {
  const r = await post(`/api/erp/connectors/${connOf(k).code}/pull`, {});
  cres[k] = Object.entries(r.summary || {}).map(([e, v]: any) => $t('erpIntegration.n0Added1Updated2', [e, v.created, v.updated, v.unchanged, v.error])).join($t('common.k764')); loadConn();
}
function openDocs() { window.open('/api/docs?token=' + tok(), '_blank'); }
watch(tab, (t) => { if (t === 'queue' || t === 'stage') loadMon(); if (t === 'hook') loadHooks(); if (t === 'conn') loadConn(); });
onMounted(async () => { await loadMode(); loadMon(); });
</script>

<style scoped>
.mt { margin-top: var(--hm-gap); }
.bad { color: var(--hm-fault-ink); }
.own { display: grid; grid-template-columns: repeat(auto-fill, minmax(calc(300px * var(--hm-scale)), 1fr)); gap: var(--hm-sp-2) var(--hm-sp-5); }
.own-row { display: flex; align-items: center; gap: var(--hm-sp-3); }
.own-lab { width: calc(80px * var(--hm-scale)); color: var(--hm-text-2); }
h4 { margin: var(--hm-sp-4) 0 var(--hm-sp-2); font-size: var(--hm-fs-sm); }
.stg { display: flex; flex-wrap: wrap; gap: var(--hm-sp-3); align-items: center; }
.url { width: calc(420px * var(--hm-scale)); max-width: 100%; }
.hookf { display: flex; flex-wrap: wrap; gap: var(--hm-sp-2); }
.hn { width: calc(160px * var(--hm-scale)); } .hu { width: calc(340px * var(--hm-scale)); max-width: 100%; } .he { width: calc(280px * var(--hm-scale)); }
.cnts { display: flex; flex-wrap: wrap; gap: var(--hm-sp-2); }
.cf :deep(textarea) { font-family: var(--hm-font-mono); font-size: var(--hm-fs-xs); }
.cbtn { display: flex; flex-wrap: wrap; gap: var(--hm-sp-2); margin-top: var(--hm-sp-2); }
.cres { margin-top: var(--hm-sp-2); color: var(--hm-text-2); white-space: pre-wrap; }
</style>
