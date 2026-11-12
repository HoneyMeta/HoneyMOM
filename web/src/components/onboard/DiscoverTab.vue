<template>
  <div class="dc" data-test="onboard-discover">
    <div class="grid">
      <HmCard :title="$t('discoverTab.opcUaBrowse')" data-tour="discover-opcua">
        <div class="row">
          <el-select v-model="opc" size="small" :placeholder="$t('discoverTab.selectAnOpcUaData')" class="sel" data-test="opc-ds" @change="reloadTree">
            <el-option v-for="d in sources.filter((x) => x.protocol === 'opcua')" :key="d.id" :label="`${d.code} ${d.endpoint}`" :value="d.id" />
          </el-select>
          <span class="muted small">{{ $t('discoverTab.expandNodesToSeeChildren') }}</span>
        </div>
        <div class="split">
          <div class="tree">
            <el-tree v-if="opc && treeKey" :key="treeKey" lazy :load="loadNode" :props="{ label: 'label', isLeaf: 'leaf' }" node-key="nodeid" show-checkbox check-strictly
              highlight-current data-test="opc-tree" @node-click="(d: any) => (cur = d)" @check="onCheck">
              <template #default="{ data: d }"><span class="tn" :class="d.node_class"><span>{{ d.label }}</span><span v-if="d.node_class === 'Variable'" class="tv hm-num">{{ fmtV(d.value) }}{{ d.unit ? ' ' + d.unit : '' }}</span></span></template>
            </el-tree>
            <div v-else class="muted small pad">{{ $t('discoverTab.selectAnOpcUaData2') }}</div>
          </div>
          <div class="attrs" data-test="opc-attrs">
            <template v-if="cur">
              <div v-for="k in ATTRS" :key="k[0]" class="ar"><span class="ak">{{ k[1] }}</span><span class="av" :class="{ 'hm-mono': k[0] === 'nodeid' }">{{ fmtV(cur[k[0]]) }}</span></div>
            </template>
            <div v-else class="muted small">{{ $t('discoverTab.clickANodeToSee') }}</div>
          </div>
        </div>
        <div class="row">
          <span class="small">{{ $t('discoverTab.checked') }} <b>{{ checked.length }}</b> {{ $t('discoverTab.variables') }}</span>
          <el-select v-model="opcDev" size="small" filterable clearable :placeholder="$t('discoverTab.addToEquipment')" class="sel"><el-option v-for="d in devices" :key="d.code" :label="`${d.code} ${d.name}`" :value="d.code" /></el-select>
          <el-button v-perm="'onboard.point.confirm'" size="small" type="primary" :disabled="!checked.length" data-test="opc-add" @click="addNodes">{{ $t('checkTab.addToTagList') }}</el-button>
        </div>
      </HmCard>

      <HmCard :title="$t('discoverTab.mqttSparkplugBDiscovery')" data-tour="discover-mqtt">
        <div class="row">
          <el-select v-model="mq" size="small" :placeholder="$t('discoverTab.selectAnMqttDataSource')" class="sel" data-test="mqtt-ds" @change="loadSeen">
            <el-option v-for="d in sources.filter((x) => ['mqtt', 'sparkplug', 'sql', 'http'].includes(x.protocol))" :key="d.id" :label="$t('common.n01', [d.code, PROTO_CN[d.protocol]])" :value="d.id" />
          </el-select>
          <el-input-number v-model="minutes" size="small" :min="0.5" :max="60" :step="1" class="num" />
          <span class="small">{{ $t('common.min2') }}</span>
          <el-button v-perm="'onboard.discover.run'" size="small" type="primary" :disabled="!mq" data-test="mqtt-discover" @click="startDiscover">{{ $t('discoverTab.startDiscovery') }}</el-button>
          <span v-if="left > 0" class="small">{{ $t('discoverTab.remaining') }} <b class="hm-num">{{ left }}</b> {{ $t('discoverTab.s') }}</span>
        </div>
        <p class="muted small">{{ $t('discoverTab.everyTopicSparkplugMetricReceived') }}</p>
        <HmDataTable :columns="S_COLS" :rows="seen" row-key="id" :ai="false" :max-height="420" :empty-text="$t('discoverTab.noDiscoveryResultsYet')" data-test="seen-table">
          <template #cell-address="{ row }"><span class="hm-mono small">{{ row.address }}</span></template>
          <template #cell-sample="{ row }"><span class="small">{{ String(row.sample ?? '').slice(0, 36) }}</span></template>
          <template #cell-meta="{ row }"><span class="small muted">{{ [row.attrs?.datatype, row.attrs?.unit, row.attrs?.description].filter(Boolean).join(' · ') || (row.attrs?.hint ? $t('discoverTab.possibly') + row.attrs.hint : '') }}</span></template>
        </HmDataTable>
      </HmCard>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
import { ElMessage } from 'element-plus';
import { onMounted, onUnmounted, ref } from 'vue';
import { useRoute } from 'vue-router';
import { get, post } from '../../api';
import { PROTO_CN } from './common';

const route = useRoute();
const sources = ref<any[]>([]), devices = ref<any[]>([]);
const opc = ref<number | null>(route.query.ds ? Number(route.query.ds) : null), treeKey = ref(1), cur = ref<any>(null), checked = ref<any[]>([]), opcDev = ref('');
const mq = ref<number | null>(null), minutes = ref(2), left = ref(0), seen = ref<any[]>([]);
const ATTRS = [['display_name', 'DisplayName'], ['browse_name', 'BrowseName'], ['nodeid', 'NodeId'], ['node_class', $t('discoverTab.nodeClass')], ['description', 'Description'],
  ['datatype', 'DataType'], ['unit', 'EngineeringUnits'], ['range', 'EURange'], ['value', $t('discoverTab.currentValue')], ['writable', $t('discoverTab.writable')]];
const S_COLS = [{ key: 'address', label: $t('discoverTab.topicAddress') }, { key: 'sample', label: $t('checkTab.sampleValue'), width: 150 }, { key: 'count', label: $t('common.count'), num: true, width: 60 },
  { key: 'meta', label: $t('checkTab.attribute'), width: 180, sortable: false }];
const fmtV = (v: any) => (v === null || v === undefined || v === '' ? '—' : Array.isArray(v) ? v.join(' ~ ') : typeof v === 'number' && !Number.isInteger(v) ? v.toFixed(3) : String(v));
let timer: any = null;

function reloadTree() { treeKey.value++; cur.value = null; checked.value = []; }
async function loadNode(node: any, resolve: (d: any[]) => void) {
  if (!opc.value) return resolve([]);
  try {
    const r = await get(`/api/onboard/opcua/browse?datasource=${opc.value}` + (node.level > 0 ? '&node=' + encodeURIComponent(node.data.nodeid) : ''));
    const parent = node.level > 0 ? node.data.path || [] : [];
    resolve(r.children.map((c: any) => ({ ...c, label: c.display_name || c.browse_name || c.nodeid, leaf: c.node_class === 'Variable' && !c.has_children, path: [...parent, c.display_name] })));
  } catch { resolve([]); }
}
function onCheck(_d: any, st: any) { checked.value = st.checkedNodes.filter((n: any) => n.node_class === 'Variable'); }
async function addNodes() {
  const nodes = checked.value.map((n: any) => {
    const i = opcDev.value ? n.path.indexOf(opcDev.value) : -1;
    return { nodeid: n.nodeid, path: (i >= 0 ? n.path.slice(i + 1) : n.path.slice(-2)).join('/') };
  });
  const r = await post('/api/onboard/opcua/add', { datasource: opc.value, device: opcDev.value || null, nodes });
  ElMessage.success($t('discoverTab.added0TagsToThe', [r.n]));
}
async function loadSeen() {
  if (!mq.value) return;
  const r = await get(`/api/onboard/check?datasource=${mq.value}`, true).catch(() => null);
  seen.value = r?.yellow || [];
  const s = sources.value.find((x) => x.id === mq.value);
  const fresh = (await get('/api/onboard/summary', true).catch(() => null))?.sources || [];
  sources.value = fresh.length ? fresh : sources.value;
  left.value = (fresh.find((x: any) => x.id === mq.value) || s)?.discovering || 0;
}
async function startDiscover() { await post('/api/onboard/discover', { datasource: mq.value, minutes: minutes.value }); ElMessage.success($t('discoverTab.startDiscovery')); loadSeen(); }
onMounted(async () => {
  sources.value = (await get('/api/onboard/summary', true).catch(() => null))?.sources || [];
  devices.value = (await get('/api/factory/devices', true).catch(() => ({ items: [] }))).items;
  if (!opc.value) opc.value = sources.value.find((x) => x.protocol === 'opcua')?.id || null;
  mq.value = sources.value.find((x) => x.protocol === 'mqtt' || x.protocol === 'sparkplug')?.id || null;
  loadSeen();
  timer = setInterval(() => { if (!document.hidden && mq.value) loadSeen(); }, 5000);
});
onUnmounted(() => clearInterval(timer));
</script>

<style scoped>
.grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 1279px) { .grid { grid-template-columns: minmax(0, 1fr); } }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
.sel { width: 240px; } .num { width: 110px; }
.split { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr); gap: 10px; min-height: 360px; margin-bottom: 8px; }
.tree { border: 1px solid var(--hm-hairline); border-radius: 6px; max-height: 460px; overflow: auto; padding: 4px; }
.tn { display: inline-flex; gap: 8px; font-size: var(--hm-fs-sm); } .tn.Object { font-weight: 600; } .tv { color: var(--hm-text-3); font-size: var(--hm-fs-xs); }
.attrs { border: 1px solid var(--hm-hairline); border-radius: 6px; padding: 8px 10px; font-size: var(--hm-fs-sm); overflow: auto; max-height: 460px; }
.ar { display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 6px; padding: 3px 0; border-bottom: 1px dashed var(--hm-hairline); }
.ak { color: var(--hm-text-3); } .av { word-break: break-all; }
.small { font-size: var(--hm-fs-xs); } .muted { color: var(--hm-text-3); } .pad { padding: 10px; }
</style>
