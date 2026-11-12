<template>
  <div class="ed" v-if="meta && agent">
    <div class="bar">
      <el-button text @click="router.push('/agents')"><el-icon><Back /></el-icon></el-button>
      <el-icon color="#d99a16" size="18"><component :is="agent.icon || 'MagicStick'" /></el-icon>
      <el-input v-model="agent.name" class="nm" size="small" @input="dirty = true" :disabled="!canEdit" />
      <el-tag v-if="agent.published" size="small" type="success">{{ $t('agentEditor.publishedV0', [agent.published]) }}</el-tag><el-tag v-else size="small" type="info">{{ $t('agentEditor.unpublished') }}</el-tag>
      <el-tag v-if="dirty" size="small" type="warning">{{ $t('agentEditor.unsavedChanges') }}</el-tag>
      <el-tag v-else-if="agent.draft_changed" size="small" type="warning" effect="plain">{{ $t('agentEditor.draftDiffersFromPublishedVersion') }}</el-tag>
      <el-tag v-if="agent.enabled" size="small" effect="dark" type="success">{{ $t('agentEditor.enabled') }}</el-tag>
      <el-popover v-if="problems.length" placement="bottom" :width="380" trigger="click">
        <template #reference><el-button size="small" :type="errors.length ? 'danger' : 'warning'" plain>{{ errors.length ? $t('agentEditor.n0Issues', [errors.length]) : $t('agentEditor.n0Warnings', [problems.length]) }}</el-button></template>
        <div v-for="(p, i) in problems" :key="i" class="prob" :class="p.level" @click="p.node && select(p.node)">{{ p.level === 'error' ? '✖' : '!' }} {{ p.msg }}</div>
      </el-popover>
      <span class="grow" />
      <span class="muted small">{{ agent.trigger }}</span>
      <el-button size="small" @click="save()" :disabled="!canEdit" data-test="save"><el-icon><Finished /></el-icon>{{ $t('agentEditor.save') }}</el-button>
      <el-button size="small" type="primary" plain @click="tab = 'debug'"><el-icon><VideoPlay /></el-icon>{{ $t('agentEditor.debug') }}</el-button>
      <el-button size="small" type="primary" @click="openPublish" :disabled="!canEdit" data-test="publish"><el-icon><Upload /></el-icon>{{ $t('agentEditor.publish') }}</el-button>
      <el-dropdown @command="onMore">
        <el-button size="small"><el-icon><MoreFilled /></el-icon></el-button>
        <template #dropdown><el-dropdown-menu>
          <el-dropdown-item command="settings">{{ $t('agentEditor.settingsPermissionsChatInvocation') }}</el-dropdown-item>
          <el-dropdown-item command="export">{{ $t('agentEditor.exportJson') }}</el-dropdown-item>
          <el-dropdown-item command="fit">{{ $t('agentEditor.fitToCanvas') }}</el-dropdown-item>
          <el-dropdown-item command="layout">{{ $t('agentEditor.autoLayout') }}</el-dropdown-item>
        </el-dropdown-menu></template>
      </el-dropdown>
    </div>
    <div class="body">
      <aside class="pal">
        <div v-for="c in meta.categories.filter((x: any) => x.key !== 'trigger')" :key="c.key" class="pg">
          <div class="pgt" :style="{ color: CAT_COLOR[c.key] }">{{ c.label }}</div>
          <div v-for="t in typesIn(c.key)" :key="t.type" class="pi" draggable="true" @dragstart="(e: any) => e.dataTransfer.setData('application/agent-node', t.type)"
            @click="addNode(t.type)" :title="t.desc" :data-test="'pal-' + t.type">
            <el-icon :color="CAT_COLOR[c.key]"><component :is="t.icon" /></el-icon><span>{{ t.label }}</span>
          </div>
        </div>
      </aside>
      <div class="canvas" @drop="onDrop" @dragover.prevent>
        <VueFlow v-model:nodes="nodes" v-model:edges="edges" :default-edge-options="{ type: 'smoothstep' }" :min-zoom="0.2" :max-zoom="1.6" :snap-to-grid="true" :snap-grid="[10, 10]"
          :delete-key-code="canEdit ? ['Backspace', 'Delete'] : null" :is-valid-connection="validConn" @nodes-initialized="initView" @connect="onConnect" @node-click="(e: any) => select(e.node.id)"
          @pane-click="selected = null" @node-drag-stop="dirty = true" @edges-change="onEdgesChange" @nodes-change="onNodesChange">
          <template #node-agent="p"><FlowNode :id="p.id" :data="p.data" :selected="p.id === selected" /></template>
          <Background :gap="20" pattern-color="#e3e7eb" />
          <MiniMap pannable zoomable :node-color="miniColor" position="top-right" :width="160" :height="100" />
          <Controls position="bottom-left" />
        </VueFlow>
        <div class="hint muted">{{ $t('agentEditor.dragNodesInFromThe') }}</div>
      </div>
      <aside class="side">
        <el-tabs v-model="tab" class="tabs" stretch>
          <el-tab-pane :label="$t('agentEditor.node')" name="node">
            <div class="pane">
              <template v-if="selNode">
                <NodeConfig :key="selNode.id" :node-id="selNode.id" :data="selNode.data" :meta="meta" :vars="selVars" :node-options="nodeOptions" @changed="onParamsChanged" />
                <div class="sec" v-if="run && selSteps.length"><div class="sect">{{ $t('agentEditor.resultInThisRun') }}</div><TraceViewer :steps="selSteps" :types="meta.types" /></div>
                <div class="sec" v-if="selNode.data.type !== 'begin' && canEdit">
                  <div class="sect">{{ $t('agentEditor.runThisNodeOnly') }} <span class="muted small">{{ $t('agentEditor.writeActionsRunAsTest') }}</span></div>
                  <div class="muted small">{{ $t('agentEditor.upstreamOutputJsonDefaultsTo') }}</div>
                  <el-input v-model="mockText" type="textarea" :rows="5" class="mono" />
                  <el-button size="small" type="primary" plain :loading="singleBusy" @click="runSingle" style="margin-top:6px" data-test="run-node">{{ $t('agentEditor.runThisNode') }}</el-button>
                  <TraceViewer v-if="singleSteps.length" :steps="singleSteps" :types="meta.types" style="margin-top:6px" />
                </div>
                <el-button v-if="selNode.id !== 'begin' && canEdit" size="small" type="danger" plain @click="removeNode(selNode.id)" style="margin-top:10px">{{ $t('agentEditor.deleteNode') }}</el-button>
              </template>
              <template v-else>
                <p class="muted small">{{ $t('agentEditor.clickANodeOnThe') }}</p>
                <p class="muted small">{{ $t('agentEditor.type') }} <code>/</code> {{ $t('agentEditor.inATextBoxTo') }} <code>{{ $t('agentEditor.nodeidOutput') }}</code>{{ $t('agentEditor.inputs') }} <code>{{ $t('agentEditor.inputsField') }}</code>{{ $t('agentEditor.systemVariables') }} <code>{sys.now}</code> {{ $t('agentEditor.andMore') }}</p>
                <div v-for="(p, i) in problems" :key="i" class="prob" :class="p.level" @click="p.node && select(p.node)">{{ p.level === 'error' ? '✖' : '!' }} {{ p.msg }}</div>
              </template>
            </div>
          </el-tab-pane>
          <el-tab-pane :label="$t('agentEditor.debugRun')" name="debug">
            <div class="pane">
              <div class="f"><label>{{ $t('common.runMode') }}</label>
                <el-radio-group v-model="mode" size="small"><el-radio-button value="dry">{{ $t('agentEditor.testRunWriteActionsAre') }}</el-radio-button><el-radio-button value="live">{{ $t('agentEditor.liveRun') }}</el-radio-button></el-radio-group></div>
              <template v-if="trig === 'chat'"><div class="f"><label>{{ $t('agentlib.chatInput') }}</label><el-input v-model="chatQ" type="textarea" :rows="2" :placeholder="$t('agentEditor.eGWhichMachineHad')" data-test="debug-chat" /></div></template>
              <template v-else-if="trig === 'event'">
                <div class="f"><label>{{ $t('agentEditor.simulateEventPickAReal', [meta.events[beginP.event]?.label]) }}</label>
                  <el-select v-model="sampleIdx" size="small" style="width:100%" :placeholder="$t('agentEditor.selectARecord')" @change="pickSample" data-test="sample-select">
                    <el-option v-for="(s, i) in samples" :key="i" :value="i" :label="s.label" /></el-select>
                  <el-input v-model="eventText" type="textarea" :rows="6" class="mono" style="margin-top:6px" />
                </div>
              </template>
              <template v-else-if="formFields.length">
                <div v-for="f in formFields" :key="f.key" class="f"><label>{{ f.label || f.key }}</label><el-input v-model="formVals[f.key]" size="small" :type="f.type === 'textarea' ? 'textarea' : 'text'" /></div>
              </template>
              <p v-else class="muted small">{{ $t('agentEditor.thisAgentHasNoInputs') }}</p>
              <div class="runbtns">
                <el-button type="primary" :loading="running" @click="startRun" :disabled="!canEdit" data-test="debug-run"><el-icon><VideoPlay /></el-icon>{{ $t('agentEditor.run') }}</el-button>
                <el-button v-if="run && ['running', 'waiting', 'queued'].includes(run.status)" @click="cancelRun">{{ $t('agentEditor.stop') }}</el-button>
              </div>
              <template v-if="run">
                <div class="runhead">{{ $t('agentEditor.run0', [run.id]) }} <el-tag size="small" :type="statusType(run.status)">{{ STATUS_CN[run.status] || run.status }}</el-tag>
                  <el-tag v-if="run.mode === 'dry'" size="small" type="info">{{ $t('common.testRun') }}</el-tag><span class="grow" />
                  <span class="muted small">{{ $t('agentEditor.n0Steps1Tokens', [run.steps_n, run.tokens || 0]) }}</span></div>
                <div v-if="run.error" class="err">{{ run.error }}</div>
                <ApprovalCard v-for="a in runApprovals" :key="a.id" :a="a" compact @done="afterDecide" />
                <div v-if="run.output?.reply" class="reply mdx" v-html="md(run.output.reply)" />
                <div v-if="run.output?.receipts?.length" class="chips"><a v-for="(r, i) in run.output.receipts" :key="i" class="chip" @click="r.link && router.push(r.link)">{{ r.text }}</a></div>
                <div class="sect">{{ $t('agentEditor.steps') }}</div>
                <TraceViewer :steps="run.steps" :types="meta.types" :highlight="selected || ''" />
              </template>
            </div>
          </el-tab-pane>
          <el-tab-pane :label="$t('agentEditor.runHistory')" name="runs">
            <div class="pane">
              <el-button size="small" @click="loadRuns">{{ $t('common.refresh') }}</el-button>
              <div v-for="r in runs" :key="r.id" class="runrow" :class="{ cur: run?.id === r.id }" @click="loadRun(r.id); tab = 'debug'">
                <div><b>#{{ r.id }}</b> <el-tag size="small" :type="statusType(r.status)">{{ STATUS_CN[r.status] || r.status }}</el-tag>
                  <el-tag v-if="r.mode === 'dry'" size="small" type="info">{{ $t('common.testRun') }}</el-tag> <span class="muted small">{{ TRIG[r.trigger] || r.trigger }}{{ r.version ? ' · v' + r.version : $t('agentEditor.draft') }}</span></div>
                <div class="muted small">{{ $t('agentEditor.n01Steps2Tokens', [r.created_at, r.steps, r.tokens || 0, r.trigger_key ? '· ' + r.trigger_key : '']) }}</div>
                <div v-if="r.error" class="small bad">{{ r.error }}</div>
              </div>
              <p v-if="!runs.length" class="muted small">{{ $t('agentEditor.noRunsYet') }}</p>
              <div class="sect" v-if="events.length">{{ $t('agentEditor.triggerLogDebouncedCooldownStarted') }}</div>
              <div v-for="e in events" :key="e.id" class="small evrow"><span class="muted">{{ (e.created_at || '').slice(5, 19) }}</span> {{ EVT[e.outcome] || e.outcome }} · {{ e.key }}<span v-if="e.run"> → #{{ e.run }}</span></div>
            </div>
          </el-tab-pane>
          <el-tab-pane :label="$t('agentEditor.publishSettings')" name="ver">
            <div class="pane">
              <div class="f sw"><label>{{ $t('agentEditor.enabledThePublishedVersionRuns') }}</label><el-switch :model-value="agent.enabled" @change="toggleEnable" :disabled="!canEdit" data-test="enable" /></div>
              <div class="f sw"><label>{{ $t('agentEditor.callableFromChatTheAssistant') }}</label><el-switch v-model="agent.chat_callable" @change="saveSettings" :disabled="!canEdit" /></div>
              <div class="f"><label>{{ $t('agentEditor.allowedWriteActionsPermissionAllowli') }}</label>
                <el-checkbox-group v-model="agent.permissions" @change="saveSettings" :disabled="!canEdit" class="perms">
                  <el-checkbox v-for="(o, k) in meta.ops" :key="k" :value="k">{{ o.label }}<span class="muted small">{{ o.risk === 'high' ? $t('agentEditor.approvalRequired') : '' }}</span></el-checkbox>
                </el-checkbox-group></div>
              <div class="f"><label>{{ $t('common.description') }}</label><el-input v-model="agent.description" type="textarea" :rows="2" @change="saveSettings" :disabled="!canEdit" /></div>
              <div v-if="agent.webhook" class="f"><label>{{ $t('agentEditor.webhookUrlPostJson') }}</label><el-input :model-value="origin + agent.webhook" readonly size="small" /></div>
              <div class="sect">{{ $t('common.versions') }}</div>
              <div v-for="v in agent.versions" :key="v.id" class="ver">
                <b>v{{ v.version }}</b> <el-tag v-if="v.version === agent.published" size="small" type="success">{{ $t('agentEditor.currentPublished') }}</el-tag>
                <span class="muted small">{{ v.published_at }} · {{ v.published_by }}</span>
                <div class="small">{{ v.note || $t('agentEditor.noNotes') }}</div>
                <el-button v-if="v.version !== agent.published && canEdit" link type="primary" size="small" @click="rollback(v)">{{ $t('agentEditor.rollBackToThisVersion') }}</el-button>
              </div>
              <p v-if="!agent.versions.length" class="muted small">{{ $t('agentEditor.notPublishedYetWhenDebugging') }}</p>
            </div>
          </el-tab-pane>
        </el-tabs>
      </aside>
    </div>

    <el-dialog v-model="pubOpen" :title="$t('agentEditor.publish2')" width="min(460px, 96vw)">
      <p class="small muted">{{ $t('agentEditor.afterPublishingTriggersTheChat', [session.user?.name]) }}</p>
      <div v-for="(p, i) in problems.filter((x: any) => x.level === 'error')" :key="i" class="prob error">✖ {{ p.msg }}</div>
      <el-input v-model="pubNote" :placeholder="$t('agentEditor.whatChangedOptional')" />
      <el-checkbox v-model="pubEnable" style="margin-top:8px">{{ $t('agentEditor.enableRightAfterPublishing') }}</el-checkbox>
      <template #footer><el-button @click="pubOpen = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :loading="pubBusy" @click="doPublish" data-test="publish-ok">{{ $t('agentEditor.publish2') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { Background } from '@vue-flow/background';
import { Controls } from '@vue-flow/controls';
import '@vue-flow/controls/dist/style.css';
import { MarkerType, VueFlow, useVueFlow } from '@vue-flow/core';
import '@vue-flow/core/dist/style.css';
import '@vue-flow/core/dist/theme-default.css';
import { MiniMap } from '@vue-flow/minimap';
import '@vue-flow/minimap/dist/style.css';
import { ElMessage, ElMessageBox } from 'element-plus';
import { computed, onMounted, onUnmounted, provide, reactive, ref, watch } from 'vue';
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router';
import { CAT_COLOR, defaults, fromDsl, handlesOf, mkEdge, newId, toDsl, varsFor } from '../agentlib';
import { get, post, put, session, STATUS_CN, statusType } from '../api';
import ApprovalCard from '../components/ApprovalCard.vue';
import FlowNode from '../components/agent/FlowNode.vue';
import NodeConfig from '../components/agent/NodeConfig.vue';
import TraceViewer from '../components/TraceViewer.vue';
import { md } from '../md';

const props = defineProps<{ id: string }>();
const route = useRoute(), router = useRouter();
const meta = ref<any>(null), agent = ref<any>(null);
provide('agentMeta', meta);
const nodes = ref<any[]>([]), edges = ref<any[]>([]), selected = ref<string | null>(null), dirty = ref(false), problems = ref<any[]>([]);
const tab = ref('node'), mode = ref('dry'), chatQ = ref(''), formVals = reactive<Record<string, any>>({}), samples = ref<any[]>([]), sampleIdx = ref<number | null>(null), eventText = ref('{}');
const run = ref<any>(null), running = ref(false), runs = ref<any[]>([]), events = ref<any[]>([]), runApprovals = ref<any[]>([]);
const mockText = ref('{}'), singleSteps = ref<any[]>([]), singleBusy = ref(false);
const pubOpen = ref(false), pubNote = ref(''), pubEnable = ref(true), pubBusy = ref(false);
const origin = location.origin;
const TRIG: Record<string, string> = { debug: $t('agentEditor.debug2'), manual: $t('common.manual'), event: $t('common.event'), schedule: $t('common.scheduled'), chat: $t('agentlib.chat'), webhook: 'Webhook' };
const EVT: Record<string, string> = { started: $t('agentEditor.runStarted'), merged: $t('agentEditor.debounced'), cooldown: $t('agentEditor.skippedCooldown'), busy: $t('agentEditor.skippedRunLimitReached'), filtered: $t('agentEditor.skippedAlarmContainsAnExcluded') };
const { screenToFlowCoordinate, fitView, setViewport, dimensions } = useVueFlow();
let viewInit = false;
// On open: show the whole graph if it fits; otherwise keep a readable zoom and left-align from the "Start" node
function initView() {
  if (viewInit || !nodes.value.length) return;
  viewInit = true;
  const xs = nodes.value.map((n) => n.position.x), ys = nodes.value.map((n) => n.position.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs) + 230, minY = Math.min(...ys), maxY = Math.max(...ys) + 130;
  const W = dimensions.value.width || 800, H = dimensions.value.height || 600;
  const zoom = Math.max(0.6, Math.min(1, (W - 60) / (maxX - minX), (H - 80) / (maxY - minY)));
  setViewport({ x: 30 - minX * zoom, y: Math.max(40, (H - (maxY - minY) * zoom) / 2) - minY * zoom, zoom });
}

const canEdit = computed(() => !!agent.value?.can_edit);
const errors = computed(() => problems.value.filter((p) => p.level === 'error'));
const selNode = computed(() => nodes.value.find((n) => n.id === selected.value));
const selVars = computed(() => (selected.value && meta.value ? varsFor(meta.value, selected.value, nodes.value, edges.value) : []));
const nodeOptions = computed(() => nodes.value.map((n) => ({ id: n.id, name: n.data.name })));
const begin = computed(() => nodes.value.find((n) => n.id === 'begin'));
const beginP = computed(() => begin.value?.data.params || {});
const trig = computed(() => beginP.value.trigger || 'manual');
const formFields = computed(() => (['manual', 'webhook'].includes(trig.value) ? beginP.value.inputs || [] : []));
const selSteps = computed(() => (run.value?.steps || []).filter((s: any) => s.node === selected.value));
const typesIn = (cat: string) => Object.values(meta.value.types).filter((t: any) => t.cat === cat);
const miniColor = (n: any) => CAT_COLOR[meta.value?.types[n.data?.type]?.cat] || '#999';

async function load() {
  meta.value = meta.value || await get('/api/agents/meta');
  agent.value = await get(`/api/agents/${props.id}`);
  const g = fromDsl(agent.value.draft);
  for (const n of g.nodes) n.data.params = { ...defaults(meta.value, n.data.type), ...n.data.params };
  nodes.value = g.nodes; edges.value = g.edges.map(styleEdge);
  problems.value = agent.value.problems;
  markProblems();
  dirty.value = false;
}
function styleEdge(e: any) {
  const col = e.sourceHandle === 'rejected' ? '#c83a3a' : e.sourceHandle === 'approved' ? '#2e9d5b' : '#9aa4ad';
  return { ...e, markerEnd: { type: MarkerType.ArrowClosed, color: col }, style: { stroke: col, strokeWidth: 1.6 }, labelBgStyle: { fill: '#fff' }, labelStyle: { fontSize: '11px', fill: col } };
}
function markProblems() {
  const by: Record<string, string> = {};
  for (const p of problems.value) if (p.node && p.level === 'error') by[p.node] = p.msg;
  for (const n of nodes.value) n.data.problem = by[n.id] || '';
}
function select(id: string) { selected.value = id; if (tab.value !== 'debug') tab.value = 'node'; prefillMocks(); }
function onParamsChanged() { dirty.value = true; pruneEdges(); }
function pruneEdges() {
  const n = selNode.value;
  if (!n) return;
  const hs = handlesOf(n.data.type, n.data.params);
  if (!hs) return;
  const ids = hs.map((h) => h.id);
  const keep = edges.value.filter((e) => e.source !== n.id || !e.sourceHandle || ids.includes(e.sourceHandle));
  if (keep.length !== edges.value.length) edges.value = keep;
  for (const e of edges.value) if (e.source === n.id && e.sourceHandle) e.label = hs.find((h) => h.id === e.sourceHandle)?.label;
}
function validConn(c: any) { return c.source !== c.target && c.target !== 'begin'; }
function onConnect(c: any) {
  if (!canEdit.value) return;
  const e = styleEdge(mkEdge(c.source, c.target, c.sourceHandle || null));
  const n = nodes.value.find((x) => x.id === c.source);
  const hs = n ? handlesOf(n.data.type, n.data.params) : null;
  if (hs && c.sourceHandle) e.label = hs.find((h) => h.id === c.sourceHandle)?.label;
  if (!edges.value.some((x) => x.id === e.id)) edges.value = [...edges.value, e];
  dirty.value = true;
}
function onEdgesChange(ch: any[]) { if (ch.some((c) => c.type === 'remove')) dirty.value = true; }
function onNodesChange(ch: any[]) {
  if (ch.some((c) => c.type === 'remove')) { dirty.value = true; if (selected.value && !nodes.value.some((n) => n.id === selected.value)) selected.value = null; }
}
function addNode(type: string, pos?: { x: number; y: number }) {
  if (!canEdit.value) return;
  const id = newId(type, nodes.value.map((n) => n.id));
  const t = meta.value.types[type];
  let position = pos;
  if (!position) {
    const ref_ = selNode.value || nodes.value[nodes.value.length - 1];
    position = ref_ ? { x: ref_.position.x + 280, y: ref_.position.y + 30 } : { x: 300, y: 200 };
  }
  nodes.value = [...nodes.value, { id, type: 'agent', position, deletable: true, data: { type, name: t.label, params: defaults(meta.value, type), status: '' } }];
  if (selNode.value && !handlesOf(selNode.value.data.type, selNode.value.data.params) && !pos) {
    edges.value = [...edges.value, styleEdge(mkEdge(selNode.value.id, id, null))];
  }
  dirty.value = true;
  select(id);
}
function onDrop(e: DragEvent) {
  const type = e.dataTransfer?.getData('application/agent-node');
  if (!type) return;
  addNode(type, screenToFlowCoordinate({ x: e.clientX - 110, y: e.clientY - 30 }));
}
function removeNode(id: string) {
  nodes.value = nodes.value.filter((n) => n.id !== id);
  edges.value = edges.value.filter((e) => e.source !== id && e.target !== id);
  selected.value = null; dirty.value = true;
}
function autoLayout() {
  const up: Record<string, string[]> = {};
  for (const e of edges.value) if (e.sourceHandle !== 'rejected') (up[e.target] ||= []).push(e.source);
  const layer: Record<string, number> = {};
  const depth = (id: string, seen = new Set<string>()): number => {
    if (layer[id] !== undefined) return layer[id];
    if (seen.has(id)) return 0;
    seen.add(id);
    const ups = (up[id] || []).map((u) => depth(u, seen));
    return (layer[id] = ups.length ? Math.max(...ups) + 1 : 0);
  };
  nodes.value.forEach((n) => depth(n.id));
  const cols: Record<number, any[]> = {};
  for (const n of nodes.value) (cols[layer[n.id]] ||= []).push(n);
  nodes.value = nodes.value.map((n) => { const col = cols[layer[n.id]]; const i = col.indexOf(n); return { ...n, position: { x: 40 + layer[n.id] * 290, y: 260 + (i - (col.length - 1) / 2) * 150 } }; });
  dirty.value = true;
  setTimeout(() => fitView({ padding: 0.15 }), 50);
}

async function save(quiet = false) {
  const draft = toDsl(nodes.value, edges.value);
  const r = await put(`/api/agents/${props.id}`, { draft, name: agent.value.name });
  problems.value = r.problems; markProblems();
  dirty.value = false;
  agent.value = { ...agent.value, ...(await get(`/api/agents/${props.id}`)), name: agent.value.name };
  if (!quiet) ElMessage.success(r.problems.some((p: any) => p.level === 'error') ? $t('agentEditor.savedFixTheRemainingIssues') : $t('common.saved'));
}
async function saveSettings() {
  await put(`/api/agents/${props.id}`, { chat_callable: agent.value.chat_callable, permissions: agent.value.permissions, description: agent.value.description });
  const r = await post(`/api/agents/${props.id}/validate`, { draft: toDsl(nodes.value, edges.value), permissions: agent.value.permissions });
  problems.value = r.problems; markProblems();
  ElMessage.success($t('agentEditor.settingsSaved'));
}
async function toggleEnable(v: any) {
  await post(`/api/agents/${props.id}/enable`, { enabled: v });
  agent.value.enabled = v; ElMessage.success(v ? $t('agentEditor.enabled') : $t('agentEditor.disabled'));
}
async function openPublish() {
  await save(true);
  pubNote.value = ''; pubEnable.value = !agent.value.enabled; pubOpen.value = true;
}
async function doPublish() {
  pubBusy.value = true;
  try {
    const r = await post(`/api/agents/${props.id}/publish`, { note: pubNote.value });
    if (pubEnable.value && !agent.value.enabled) await post(`/api/agents/${props.id}/enable`, { enabled: true });
    ElMessage.success($t('agentEditor.publishedV0', [r.version])); pubOpen.value = false;
    agent.value = { ...agent.value, ...(await get(`/api/agents/${props.id}`)) };
  } finally { pubBusy.value = false; }
}
async function rollback(v: any) {
  await ElMessageBox.confirm($t('agentEditor.rollThePublishedVersionBack', [v.version, v.version]), $t('agentEditor.rollBack'));
  await post(`/api/agents/${props.id}/rollback`, { version_id: v.id, to_draft: true });
  ElMessage.success($t('agentEditor.rolledBackToV0', [v.version]));
  await load();
}
async function onMore(c: string) {
  if (c === 'settings') tab.value = 'ver';
  if (c === 'fit') fitView({ padding: 0.15 });
  if (c === 'layout') autoLayout();
  if (c === 'export') {
    if (dirty.value) await save(true);
    const j = await get(`/api/agents/${props.id}/export`);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(j, null, 2)], { type: 'application/json' }));
    a.download = `agent-${props.id}.json`; a.click();
  }
}

// ----- Debug -----
watch(() => [trig.value, beginP.value.event], async () => {
  samples.value = []; sampleIdx.value = null;
  if (trig.value === 'event' && beginP.value.event) {
    samples.value = await get(`/api/agents/sample_events/${beginP.value.event}`, true).catch(() => []);
    if (samples.value.length) { sampleIdx.value = 0; pickSample(0); }
  }
});
function pickSample(i: any) { eventText.value = JSON.stringify(samples.value[i]?.payload || {}, null, 2); }
function inputsNow() {
  if (trig.value === 'chat') return { query: chatQ.value };
  if (trig.value === 'event') { try { return JSON.parse(eventText.value || '{}'); } catch { throw new Error($t('agentEditor.eventContentIsNotValid')); } }
  return { ...formVals };
}
let poll: any = null;
async function startRun() {
  let inputs: any;
  try { inputs = inputsNow(); } catch (e: any) { ElMessage.error(e.message); return; }
  running.value = true;
  try {
    if (dirty.value) await save(true);
    if (errors.value.length) { ElMessage.error($t('agentEditor.theCanvasStillHasIssues') + errors.value[0].msg); return; }
    const r = await post(`/api/agents/${props.id}/run`, { inputs, mode: mode.value, use: 'draft' });
    await loadRun(r.run_id);
  } finally { running.value = false; }
}
async function loadRun(rid: number) {
  clearTimeout(poll);
  const d = await get(`/api/agents/runs/${rid}`);
  run.value = d;
  for (const n of nodes.value) n.data.status = d.node_status?.[n.id] || '';
  runApprovals.value = d.status === 'waiting' ? await get(`/api/approvals?run=${rid}`, true).then((x: any[]) => x.filter((a) => a.status === 'pending')).catch(() => []) : [];
  if (['queued', 'running', 'waiting'].includes(d.status)) poll = setTimeout(() => loadRun(rid), d.status === 'waiting' ? 2500 : 700);
  else { loadRuns(); prefillMocks(); }
}
function afterDecide() { if (run.value) setTimeout(() => loadRun(run.value.id), 400); }
async function cancelRun() { await post(`/api/agents/runs/${run.value.id}/cancel`); loadRun(run.value.id); }
async function loadRuns() {
  runs.value = await get(`/api/agents/${props.id}/runs`, true).catch(() => []);
  events.value = await get(`/api/agents/${props.id}/events?limit=30`, true).catch(() => []);
}
function prefillMocks() {
  if (!selected.value) return;
  const anc = new Set<string>();
  const st = edges.value.filter((e) => e.target === selected.value).map((e) => e.source);
  while (st.length) { const x = st.pop()!; if (anc.has(x)) continue; anc.add(x); st.push(...edges.value.filter((e) => e.target === x).map((e) => e.source)); }
  const outs = run.value?.outputs || {};
  const m: any = {};
  for (const id of anc) m[id] = outs[id] ?? (id === 'begin' ? safeInputs() : {});
  mockText.value = JSON.stringify(m, null, 1);
  singleSteps.value = [];
}
function safeInputs() { try { return inputsNow(); } catch { return {}; } }
async function runSingle() {
  let mocks: any;
  try { mocks = JSON.parse(mockText.value || '{}'); } catch { ElMessage.error($t('agentEditor.upstreamOutputIsNotValid')); return; }
  singleBusy.value = true;
  try {
    const d = await post(`/api/agents/${props.id}/run_node`, { node: selected.value, inputs: mocks.begin || safeInputs(), mocks, draft: toDsl(nodes.value, edges.value) });
    singleSteps.value = d.steps;
    if (d.error) ElMessage.error(d.error);
  } finally { singleBusy.value = false; }
}

function onKey(e: KeyboardEvent) { if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); if (canEdit.value) save(); } }
onMounted(async () => {
  await load();
  loadRuns();
  window.addEventListener('keydown', onKey);
  if (route.query.run) { tab.value = 'debug'; loadRun(Number(route.query.run)); }
});
onUnmounted(() => { window.removeEventListener('keydown', onKey); clearTimeout(poll); });
onBeforeRouteLeave(async () => {
  if (!dirty.value) return true;
  try { await ElMessageBox.confirm($t('agentEditor.youHaveUnsavedChangesThat'), $t('common.hint'), { confirmButtonText: $t('agentEditor.saveLeave'), cancelButtonText: $t('agentEditor.donTSave'), distinguishCancelAndClose: true }); await save(true); return true; }
  catch (a: any) { return a === 'cancel'; }
});
</script>

<style scoped>
.ed { display: flex; flex-direction: column; height: calc(100vh - 52px); }
.bar { display: flex; align-items: center; gap: 8px; padding: 6px 12px; background: #fff; border-bottom: 1px solid #e6eaee; flex-wrap: wrap; }
.bar .nm { width: 220px; } .bar .nm :deep(.el-input__inner) { font-weight: 600; font-size: 14px; } .grow { flex: 1; }
.body { flex: 1; display: flex; min-height: 0; }
.pal { width: 176px; flex: none; background: #fafbfc; border-right: 1px solid #e6eaee; overflow: auto; padding: 8px; }
.pg { margin-bottom: 10px; } .pgt { font-size: 12px; font-weight: 700; margin: 4px 4px 4px; }
.pi { display: flex; align-items: center; gap: 6px; padding: 5px 8px; border-radius: 6px; cursor: grab; font-size: 12.5px; background: #fff; border: 1px solid #edf0f2; margin-bottom: 4px; }
.pi:hover { border-color: #e6b75a; background: #fffaf0; }
.canvas { flex: 1; position: relative; min-width: 0; background: #f6f7f9; }
.canvas .hint { position: absolute; bottom: 8px; left: 50%; transform: translateX(-50%); background: rgba(255, 255, 255, .85); padding: 2px 10px; border-radius: 999px; pointer-events: none; white-space: nowrap; font-size: 11.5px; }
.side { width: 400px; flex: none; background: #fff; border-left: 1px solid #e6eaee; display: flex; flex-direction: column; min-height: 0; }
.tabs { height: 100%; min-height: 0; } .tabs :deep(.el-tabs__header) { margin: 0; padding: 0 8px; flex: none; }
.tabs :deep(.el-tabs__content) { flex: 1; overflow: auto; min-height: 0; } .pane { padding: 10px 14px 30px; }
.f { margin-bottom: 10px; } .f > label { display: block; font-size: 12.5px; color: #3a434c; margin-bottom: 4px; font-weight: 500; }
.f.sw { display: flex; align-items: center; justify-content: space-between; } .f.sw label { margin: 0; }
.perms { display: flex; flex-direction: column; align-items: flex-start; }
.sec { margin-top: 14px; border-top: 1px dashed #e6eaee; padding-top: 10px; } .sect { font-size: 12.5px; font-weight: 600; margin: 10px 0 6px; color: #3a434c; }
.prob { font-size: 12.5px; padding: 4px 6px; border-radius: 6px; cursor: pointer; margin-bottom: 3px; } .prob.error { color: #a12a2a; background: #fdf0f0; } .prob.warn { color: #8a5a07; background: #fdf8ec; }
.runbtns { display: flex; gap: 6px; margin: 4px 0 10px; }
.runhead { display: flex; align-items: center; gap: 6px; font-weight: 600; margin: 8px 0; }
.err { color: #a12a2a; background: #fbe9e9; border-radius: 6px; padding: 6px 8px; font-size: 12.5px; margin-bottom: 6px; white-space: pre-wrap; }
.reply { background: #f6fbf8; border: 1px solid #d5ecdf; border-radius: 8px; padding: 6px 10px; font-size: 13px; margin: 6px 0; max-height: 300px; overflow: auto; }
.chips { display: flex; flex-wrap: wrap; gap: 5px; margin: 6px 0; } .chip { font-size: 12px; background: #dff3e7; color: #1d6d3e; border-radius: 999px; padding: 2px 9px; cursor: pointer; }
.runrow { border: 1px solid #e6eaee; border-radius: 8px; padding: 6px 8px; margin-top: 6px; cursor: pointer; } .runrow:hover, .runrow.cur { border-color: #e6b75a; background: #fffaf0; }
.evrow { padding: 2px 0; border-bottom: 1px dotted #eef1f3; }
.ver { border-bottom: 1px solid #eef1f3; padding: 6px 0; }
.mono :deep(textarea) { font-family: Consolas, monospace; font-size: 12px; }
.mdx { line-height: 1.6; } .mdx :deep(p) { margin: 3px 0; } .mdx :deep(ul), .mdx :deep(ol) { margin: 3px 0; padding-left: 18px; } .mdx :deep(h3), .mdx :deep(h4), .mdx :deep(h5) { margin: 6px 0 3px; font-size: 13px; }
code { font-size: 11.5px; background: #f3f5f7; padding: 0 3px; border-radius: 3px; }
@media (max-width: 1100px) { .side { width: 340px; } .pal { width: 140px; } }
</style>
