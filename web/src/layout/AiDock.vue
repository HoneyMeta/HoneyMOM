<template>
  <aside class="dock" :class="{ open: aiState.dockOpen }" :aria-label="$t('aiDock.aiAssistant')">
    <!-- Collapsed: thin vertical bar at the edge, doesn't cover content (the shell reserves a column for it) -->
    <button v-if="!aiState.dockOpen" class="edge" data-test="assistant-pill" :class="{ off: !aiOn('ask') }" :title="$t('aiDock.openAiPanel')" @click="openAiDock(true)">
      <span class="spk" aria-hidden="true" />
      <span class="vt" :class="{ lat: !isZh }"><span class="tcu">AI</span>{{ $t('aiDock.assistant') }}</span>
      <em v-if="aiState.items.length" class="cnt hm-num" :title="$t('aiDock.aiContext')">{{ aiState.items.length }}</em>
      <em v-if="pending.length" class="pbadge hm-num" :title="$t('common.pendingApproval')">{{ pending.length }}</em>
    </button>

    <div v-else class="panel" data-test="assistant-panel">
      <div class="rz" role="separator" aria-orientation="vertical" :aria-label="$t('aiDock.dragToResizeTheAi')" :title="$t('aiDock.dragToResize')" @pointerdown="startResize" @dblclick="setDockWidth(400)" />
      <div class="ph">
        <span class="spk big" aria-hidden="true" />
        <b class="ttl" :title="title">{{ title }}</b>
        <span class="mode" :class="mode">{{ modeText }}</span>
        <span class="grow" />
        <el-tooltip :content="$t('aiDock.newChat')" popper-class="assist-pop"><el-button text size="small" :disabled="!aiOn('ask')" @click="newThread"><el-icon><Plus /></el-icon></el-button></el-tooltip>
        <el-popover v-model:visible="histOpen" placement="bottom-end" :width="300" trigger="click" popper-class="assist-pop">
          <template #reference><el-button text size="small" :disabled="!aiOn('ask')"><el-icon><Clock /></el-icon></el-button></template>
          <div class="hist">
            <div class="muted small" v-if="!threads.length">{{ $t('aiDock.noSavedChatsYet') }}</div>
            <div v-for="t in threads" :key="t.id" class="hrow" :class="{ cur: t.id === threadId }">
              <template v-if="renaming === t.id"><el-input v-model="renameText" size="small" @keyup.enter="doRename(t)" /><el-button size="small" link @click="doRename(t)">{{ $t('msaResult.good') }}</el-button></template>
              <template v-else>
                <span class="ht" @click="loadThread(t.id); histOpen = false">{{ t.title }}</span>
                <span class="muted small">{{ (t.updated_at || '').slice(5, 16) }}</span>
                <el-button link size="small" @click="renaming = t.id; renameText = t.title"><el-icon><EditPen /></el-icon></el-button>
                <el-popconfirm :title="$t('aiDock.deleteThisChat')" @confirm="delThread(t)" popper-class="assist-pop" :teleported="false"><template #reference><el-button link size="small"><el-icon><Delete /></el-icon></el-button></template></el-popconfirm>
              </template>
            </div>
          </div>
        </el-popover>
        <el-tooltip :content="$t('aiDock.collapseDockRight')" popper-class="assist-pop"><el-button text size="small" data-test="dock-collapse" @click="openAiDock(false)"><el-icon><DArrowRight /></el-icon></el-button></el-tooltip>
      </div>

      <!-- AI off -->
      <div v-if="!aiOn('ask')" class="offv" data-test="dock-off">
        <HmEmpty icon="lock" :title="$t('aiDock.aiIsOff')" :desc="offDesc">
          <el-button v-if="isAdmin" type="primary" size="small" @click="router.push('/system/ai')">{{ $t('aiDock.enableAiFeatures') }}</el-button>
          <el-button size="small" @click="openAiDock(false)">{{ $t('aiDock.collapsePanel') }}</el-button>
        </HmEmpty>
        <div class="offtips">
          <div><kbd>Ctrl</kbd>+<kbd>K</kbd> {{ $t('aiDock.youCanStillSearchPages') }}</div>
          <div>{{ $t('aiDock.traceCopyOpenRecordIn') }}</div>
        </div>
      </div>

      <template v-else>
        <div v-if="pending.length" class="pend">
          <div class="pbar" @click="pendOpen = !pendOpen"><el-icon><Stamp /></el-icon> {{ $t('aiDock.youHave0ItemsPending', [pending.length]) }} <span class="grow" /><el-icon><ArrowDown v-if="!pendOpen" /><ArrowUp v-else /></el-icon></div>
          <div v-if="pendOpen" class="plist">
            <ApprovalCard v-for="a in pending.slice(0, 3)" :key="a.id" :a="a" compact @done="loadPending" />
            <el-button link type="primary" size="small" @click="router.push('/approvals')">{{ $t('aiDock.seeAllInTheApproval') }}</el-button>
          </div>
        </div>
        <div class="msgs" ref="box">
          <div v-if="!msgs.length" class="hello">
            <div class="hh"><span class="spk big" />{{ $t('aiDock.iMHoneymomAiI') }}</div>
            <ul class="how">
              <li><kbd>Ctrl</kbd>{{ $t('aiDock.clickKpisMachinesOrTable') }}</li>
              <li>{{ $t('aiDock.rightClickAnyDataExplain') }}</li>
              <li>{{ $t('aiDock.whenADocumentIsNeeded') }}</li>
            </ul>
            <div class="hints"><el-button v-for="h in hints" :key="h" size="small" round @click="send(h)">{{ h }}</el-button></div>
          </div>
          <div v-for="(m, i) in msgs" :key="i" :class="['m', m.role]">
            <div v-if="m.role === 'user'" class="ub">{{ m.text }}<div v-if="m.ctx?.length" class="uctx">{{ $t('aiDock.including0', [m.ctx.join($t('common.k42'))]) }}</div></div>
            <div v-else-if="m.role === 'receipt'" class="chips"><a v-for="(r, k) in m.receipts" :key="k" class="chip ok" @click="go(r.link)"><el-icon><CircleCheck /></el-icon>{{ r.text }}</a></div>
            <div v-else class="ab">
              <div v-if="m.progress?.length && m.streaming" class="prog">
                <div v-for="(p, k) in m.progress" :key="k" class="pl" :class="{ last: k === m.progress.length - 1 }"><span class="pd" />{{ p }}</div>
              </div>
              <div v-if="m.navs?.length" class="chips"><a v-for="(n, k) in m.navs" :key="k" class="chip nav" @click="go(n.path)"><el-icon><Position /></el-icon>{{ $t('aiDock.opened0', [n.label]) }}</a></div>
              <div v-if="m.text" class="mdx" v-html="mdWithRefs(m.text, m.refs || [])" @click="refClick" />
              <AiBlocks v-if="m.blocks?.length" :blocks="m.blocks" :refs="m.refs || []" compact :source="m.q || ''" />
              <div v-if="m.pendingDraft" class="prog"><div class="pl last"><span class="pd" />{{ $t('aiDock.drafting0', [m.pendingDraft]) }}</div></div>
              <DraftCard v-for="d in m.drafts || []" :key="'d' + d.id" :draft="d" @done="loadPending" />
              <DemoNote :text="m.note" />
            <div v-if="m.error" class="err">{{ m.error }}</div>
              <div v-for="(c, k) in m.cards || []" :key="'c' + k">
                <div v-if="c.kind === 'confirm'" class="card confirm" :class="c.state">
                  <div class="ch"><el-icon><Warning /></el-icon><b>{{ $t('aiDock.pleaseConfirm0', [c.label]) }}</b></div>
                  <pre class="pv">{{ c.preview }}</pre>
                  <div v-if="!c.state" class="cb"><el-button type="primary" size="small" :loading="c.busy" @click="commit(m, c)" data-test="confirm-commit">{{ $t('aiDock.confirmSubmit') }}</el-button>
                    <el-button size="small" @click="c.state = 'cancelled'">{{ $t('aiDock.noThanks') }}</el-button></div>
                  <div v-else-if="c.state === 'cancelled'" class="muted small">{{ $t('aiDock.cancelledNothingSubmitted') }}</div>
                  <div v-else-if="c.state === 'done'" class="ok small">{{ $t('aiDock.submittedSeeTheReceiptBelow') }}</div>
                  <div v-else-if="c.state === 'old'" class="muted small">{{ $t('aiDock.confirmationCardFromHistoryAsk') }}</div>
                  <div v-else-if="c.state === 'error'" class="err small">{{ c.err }}</div>
                </div>
                <div v-else-if="c.kind === 'agent_run'" class="card agent">
                  <div class="ch"><el-icon><Cpu /></el-icon><b>{{ $t('aiDock.agent0', [c.agent_name]) }}</b><span class="grow" /><el-tag size="small" :type="statusType(c.status)">{{ STATUS_CN[c.status] || c.status }}</el-tag></div>
                  <div v-if="c.reply" class="mdx small rp" v-html="md(c.reply)" />
                  <div v-if="c.error" class="err small">{{ c.error }}</div>
                  <div v-if="c.approval" class="small warnline">{{ $t('aiDock.waitingForApproval0', [c.approval.title]) }} <el-button link type="primary" size="small" @click="pendOpen = true; loadPending()">{{ $t('aiDock.goToApproval') }}</el-button></div>
                  <div class="chips"><a v-for="(r, kk) in c.receipts || []" :key="kk" class="chip ok" @click="go(r.link)"><el-icon><CircleCheck /></el-icon>{{ r.text }}</a></div>
                  <el-button link size="small" @click="router.push(`/agents/${c.agent_id}?run=${c.run_id}`)">{{ $t('aiDock.viewRunSteps') }}</el-button>
                </div>
                <div v-else-if="c.kind === 'approvals'" class="card"><el-button link type="primary" @click="pendOpen = true; loadPending()">{{ $t('aiDock.view0PendingApprovals', [c.count]) }}</el-button></div>
              </div>
              <div v-if="m.receipts?.length" class="chips"><a v-for="(r, k) in m.receipts" :key="k" class="chip ok" @click="go(r.link)"><el-icon><CircleCheck /></el-icon>{{ r.text }}</a></div>
              <div v-if="m.steps?.length && !m.streaming" class="proc">
                <el-button link size="small" @click="m.showSteps = !m.showSteps">{{ m.showSteps ? $t('aiDock.hideSteps') : $t('aiDock.viewSteps0', [m.steps.length]) }}</el-button>
                <TraceViewer v-if="m.showSteps" :steps="m.steps" kind="chat" />
              </div>
              <div v-if="!m.streaming && m.mid && aiOn('skills') && canSaveSkill(m)" class="sksave">
                <el-button link size="small" type="primary" data-test="save-skill" @click="skillMid = m.mid"><el-icon><Collection /></el-icon>{{ $t('skillSaveDialog.saveAsSkill') }}</el-button>
              </div>
            </div>
          </div>
        </div>

        <!-- Context: current page, selected records, elements added via Ctrl+click -->
        <div class="ctx" data-test="dock-context">
          <div class="cl1"><span class="muted">{{ $t('aiDock.currentPage') }}</span>{{ pageTitle }}
            <template v-if="assistantCtx.record"><span class="muted"> {{ $t('aiDock.selected') }}</span>{{ assistantCtx.record.label }}<el-icon class="x" @click="setAssistantRecord(null)"><Close /></el-icon></template>
          </div>
          <div v-if="aiState.items.length" class="chipz" data-test="ai-chips">
            <HmTag v-for="it in aiState.items" :key="it.key" tone="ai" :icon="KIND_ICON[it.kind] || 'Aim'" closable :close-label="it.label" :title="it.label" data-test="ai-chip" @close="aiContext.remove(it)">{{ it.label }}</HmTag>
            <button class="clr" @click="aiContext.clear()">{{ $t('aiDock.clear') }}</button>
          </div>
          <div v-if="aiState.items.length" class="qa">
            <button v-for="a in quick" :key="a.id" class="qb" :data-action="a.id" @click="a.run()">{{ a.label }}</button>
          </div>
        </div>
        <div class="inp">
          <el-input ref="inputRef" v-model="q" type="textarea" :autosize="{ minRows: 2, maxRows: 8 }" resize="none"
            :placeholder="aiState.items.length ? $t('aiDock.askAboutThese0Items', [aiState.items.length]) : $t('aiDock.askAnythingOrAskMe')"
            @keydown.enter.exact.prevent="send()" data-test="assistant-input" />
          <el-button v-if="busy" class="sendbtn stop" circle :aria-label="$t('aiDock.stop')" :title="$t('aiDock.stop')" data-test="assistant-stop" @click="stop()"><span class="stopsq" /></el-button>
          <el-button v-else type="primary" class="sendbtn" circle :aria-label="$t('aiDock.send')" :title="$t('aiDock.sendEnter')" :disabled="!q.trim()" data-test="assistant-send" @click="send()"><el-icon><Promotion /></el-icon></el-button>
        </div>
      </template>
    </div>
    <SkillSaveDialog :mid="skillMid" @close="skillMid = null" />
  </aside>
</template>

<script setup lang="ts">
import { $t, isZh } from '../i18n';
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { AI_OFF, aiBus, aiContext, aiOn, aiState, openAiDock, setDockWidth, toast } from '../ai';
import { assistantCtx, call, del, ent, get, post, session, setAssistantRecord, STATUS_CN, statusType, streamPost } from '../api';
import ApprovalCard from '../components/ApprovalCard.vue';
import AiBlocks from '../ai/AiBlocks.vue';
import { mdWithRefs } from '../ai/blocks';
import DraftCard from '../ai/DraftCard.vue';
import DemoNote from '../ai/DemoNote.vue';
import SkillSaveDialog from '../ai/SkillSaveDialog.vue';
import TraceViewer from '../components/TraceViewer.vue';
import HmEmpty from '../design/components/HmEmpty.vue';
import HmTag from '../design/components/HmTag.vue';
import { md } from '../md';
import { findNav } from './nav';

const route = useRoute(), router = useRouter();
const K_TH = 'mes_assist_thread';
const KIND_ICON: Record<string, string> = { kpi: 'DataLine', 'chart-point': 'TrendCharts', row: 'Tickets', device: 'Monitor', record: 'Document', field: 'Aim', alert: 'Warning', text: 'EditPen', page: 'Document' };
const q = ref(''), busy = ref(false), msgs = ref<any[]>([]), threadId = ref<number | null>(null), title = ref($t('aiDock.aiAssistant'));
const threads = ref<any[]>([]), histOpen = ref(false), renaming = ref<number | null>(null), renameText = ref('');
const pending = ref<any[]>([]), pendOpen = ref(false), box = ref<HTMLElement>(), inputRef = ref<any>();
// 5.0 M6: the answer used a reusable query (tool calls in the trace, or a query in the "data source" block) → can "Save as Skill"
const skillMid = ref<number | null>(null);
const canSaveSkill = (m: any) => (m.steps || []).some((s: any) => s.tool && !String(s.tool).startsWith('propose_') && s.tool !== 'go_to_page' && s.status !== 'failed')
  || (m.blocks || []).some((b: any) => b.type === 'sources' && (b.items || []).some((x: any) => ['sql', 'query', 'kpi', 'tool'].includes(x.kind)));
const hints = [$t('aiDock.howAreTodaySOutput'), $t('aiDock.whichMachineHadTheMost'), $t('aiDock.isThereAnyRiskOf'), $t('aiDock.whatToDoAboutLow')];
const isAdmin = computed(() => (session.user?.role_list || []).includes('admin'));
const mode = computed(() => aiState.switches?.mode || 'off');
const modeText = computed(() => ({ off: $t('common.off'), demo: $t('aiDock.demo'), record: $t('aiDock.recording'), live: aiState.switches?.llm_configured ? $t('common.online') : $t('aiDock.ruleBased') } as any)[mode.value] || '');
const offDesc = computed(() => (aiState.switches?.global ? $t('aiDock.theAskDataFeatureIs') : $t('aiDock.allAiFeaturesAreOff')) + (isAdmin.value ? $t('aiDock.youAreAnAdministratorEnable') : $t('aiDock.askAnAdministratorToEnable')) + $t('aiDock.pagesWorkAsUsualWhile'));

const pageTitle = computed(() => {
  const p = route.path;
  const n = findNav(p);
  if (n) return n.label;
  const m = p.match(/^\/e\/(\w+)/);
  if (m) return ent(m[1])?.label || p;
  if (p.startsWith('/agents/')) return $t('aiDock.agentEditor');
  return p;
});

const sel = () => aiState.items.slice();
const names = () => aiState.items.map((x) => x.label).join($t('common.k42'));
const quick = computed(() => {
  const n = aiState.items.length;
  const out = [
    { id: 'explain', label: $t('aiLayer.explain'), run: () => send(n > 1 ? $t('aiDock.explainTheseItems0Is', [names()]) : $t('aiDock.explainThisNumber0How', [names()]), 'explain') },
    { id: 'why', label: $t('aiIndex.whyDidItChange'), run: () => send($t('aiDock.whyDid0ChangeFind', [names()]), 'why') },
  ];
  if (n >= 2) out.push({ id: 'compare', label: $t('aiDock.compare0Items', [n]), run: () => send($t('aiDock.compareThese0Items1', [n, names()]), 'compare') });
  out.push({ id: 'report', label: $t('aiIndex.generateReport'), run: () => send($t('aiDock.writeAShortReportOn', [names()]), 'report') });
  return out;
});

try { const t = Number(localStorage.getItem(K_TH)); if (t) threadId.value = t; } catch { /* */ }
watch(() => aiState.dockOpen, (v) => { if (v && aiOn('ask')) { loadPending(); scroll(); setTimeout(() => inputRef.value?.resizeTextarea?.(), 360); } });
watch(threadId, (v) => { try { v ? localStorage.setItem(K_TH, String(v)) : localStorage.removeItem(K_TH); } catch { /* */ } });
watch(() => route.path, () => { if (!route.path.startsWith('/e/')) setAssistantRecord(null); });
watch(histOpen, (v) => v && loadThreads());
// Load the last conversation only after AI switches are fetched (and Ask Data is on): don't call disabled APIs when off
watch(() => aiOn('ask'), (v) => { if (v && threadId.value && !msgs.value.length) loadThread(threadId.value); if (v) loadPending(); }, { immediate: true });

async function loadThreads() { threads.value = await get('/api/assistant/threads', true).catch(() => []); }
async function loadPending() {
  if (!aiOn('agents')) { pending.value = []; return; }
  pending.value = (await get('/api/approvals?status=pending', true).catch(() => [])).filter((a: any) => a.can_decide);
}
async function loadThread(id: number) {
  try {
    const r = await get(`/api/assistant/threads/${id}`, true);
    threadId.value = id; title.value = r.thread.title;
    msgs.value = r.messages.map((m: any) => {
      const cards = m.cards || [], bl = cards.find((c: any) => c.kind === 'blocks');
      return { role: m.role, text: m.content, steps: m.steps || [], receipts: m.role === 'receipt' ? m.receipts : (m.receipts || []),
        cards: cards.filter((c: any) => c.kind !== 'blocks' && c.kind !== 'draft').map((c: any) => ({ ...c, state: c.kind === 'confirm' ? 'old' : undefined })),
        blocks: bl?.blocks || [], refs: bl?.refs || [], drafts: cards.filter((c: any) => c.kind === 'draft').map((c: any) => c.draft),
        navs: [], ctx: (m.record?.context || []).map((x: any) => x.label), mid: m.role === 'assistant' ? m.id : null };
    });
    scroll();
  } catch { threadId.value = null; msgs.value = []; title.value = $t('aiDock.aiAssistant'); }
}
function newThread() { threadId.value = null; msgs.value = []; title.value = $t('aiDock.newChat'); }
async function doRename(t: any) {
  const v = renameText.value.trim() || $t('aiDock.untitled');
  await call('PATCH', `/api/assistant/threads/${t.id}`, { title: v });
  renaming.value = null;
  if (t.id === threadId.value) title.value = v;
  loadThreads();
}
async function delThread(t: any) {
  await del(`/api/assistant/threads/${t.id}`);
  if (t.id === threadId.value) newThread();
  loadThreads();
}
function go(path: string) { if (path) router.push(path); }
const scroll = () => nextTick(() => box.value && (box.value.scrollTop = box.value.scrollHeight));

async function send(text?: string, action = '') {
  const v = (text ?? q.value).trim();
  if (!v || busy.value) return;
  if (!aiOn('ask')) { toast(AI_OFF); return; }
  q.value = ''; busy.value = true;
  ctrl = new AbortController();
  const ctx = sel();
  msgs.value.push({ role: 'user', text: v, ctx: ctx.map((x) => x.label) });
  const m: any = { role: 'assistant', text: '', progress: [], cards: [], receipts: [], navs: [], steps: [], blocks: [], refs: [], drafts: [], streaming: true, showSteps: false, q: v };
  msgs.value.push(m);
  const mm = msgs.value[msgs.value.length - 1];
  scroll();
  try {
    await streamPost('/api/assistant/chat', { thread_id: threadId.value, text: v, page: route.fullPath, page_title: pageTitle.value, record: assistantCtx.record, action,
      context: ctx.map((x) => ({ kind: x.kind, id: x.id, label: x.label, data: x.data, entity: x.entity, sn: x.sn })) }, (ev, d) => {
      if (d && typeof d.demo_note === 'string' && d.demo_note) mm.note = d.demo_note;
          if (ev === 'note') mm.note = d.text;   // demo playback without a recording in this language
          else if (ev === 'progress' || ev === 'tool') mm.progress.push(d.text);
      else if (ev === 'navigate') { mm.navs.push(d); router.push(d.path); }
      else if (ev === 'card') { mm.cards.push({ ...d }); if (d.kind === 'agent_run') watchRun(mm.cards[mm.cards.length - 1]); }
      else if (ev === 'wrote') mm.receipts.push(d);
      else if (ev === 'answer') mm.text = d.text;
      else if (ev === 'blocks') { mm.blocks = d.blocks || []; mm.refs = d.refs || []; }
      else if (ev === 'draft') mm.drafts.push(d);
      else if (ev === 'error') mm.error = d.text;
      else if (ev === 'done') { threadId.value = d.thread_id; title.value = d.title || title.value; mm.steps = d.steps || []; mm.mid = d.message_id || null; }
      scroll();
    }, ctrl.signal);
  } catch (e: any) {
    if (e?.name === 'AbortError') { if (!mm.text) mm.text = $t('aiDock.answerStopped'); } else mm.error = e.message || String(e);
  } finally { mm.streaming = false; busy.value = false; ctrl = null; scroll(); loadPending(); }
}
let ctrl: AbortController | null = null;
function stop() { ctrl?.abort(); }

async function commit(m: any, c: any) {
  c.busy = true;
  try {
    const r = await post('/api/assistant/commit', { token: c.token, payload: c.payload });
    c.state = 'done';
    msgs.value.push({ role: 'receipt', receipts: [r.receipt, ...(r.receipt.extra || [])] });
    scroll();
  } catch (e: any) { c.state = 'error'; c.err = e.message; } finally { c.busy = false; }
}

// Agent card: keep refreshing while still running or awaiting approval
function watchRun(c: any) {
  let n = 0;
  const tick = async () => {
    if (!['queued', 'running', 'waiting'].includes(c.status) || n++ > 60) return;
    try {
      const d = await get(`/api/agents/runs/${c.run_id}`, true);
      c.status = d.status; c.reply = (d.output?.reply || c.reply || '').slice(0, 4000); c.receipts = d.output?.receipts || c.receipts; c.error = d.error;
      const p = d.approvals.find((x: any) => x.status === 'pending'); c.approval = p ? { id: p.id, title: p.title } : null;
    } catch { /* */ }
    setTimeout(tick, 4000);
  };
  setTimeout(tick, 3000);
}

// Drag to resize width
function startResize(e: PointerEvent) {
  const x0 = e.clientX, w0 = aiState.dockWidth;
  (e.target as HTMLElement).setPointerCapture(e.pointerId);
  document.body.classList.add('hm-resizing');
  const mv = (ev: PointerEvent) => setDockWidth(w0 + (x0 - ev.clientX));
  const up = () => { document.body.classList.remove('hm-resizing'); window.removeEventListener('pointermove', mv); window.removeEventListener('pointerup', up); };
  window.addEventListener('pointermove', mv); window.addEventListener('pointerup', up);
}

function onAsk(e: any) {
  const d = e.detail || {};
  if (!aiOn('ask')) return;
  openAiDock(true);
  if (d.send === false) { q.value = d.prompt || ''; nextTick(() => focusInput()); return; }
  nextTick(() => send(d.prompt, d.action || ''));
}
function onLegacy(e: any) {
  if (!aiOn('ask')) { toast(AI_OFF); return; }
  openAiDock(true);
  nextTick(() => send(e.detail.prompt));
}
// Tags linking to records ([[work_order:WO…]])
function refClick(e: MouseEvent) {
  const a = (e.target as HTMLElement).closest('[data-ref-link]') as HTMLElement | null;
  if (a) { e.preventDefault(); router.push(a.dataset.refLink!); }
}
// Right-click "AI Dispatch Repair / Raise Andon / Adjust Schedule" (ai/actions.ts): draft appears in the panel
const offPend = aiBus.on('draft-pending', (p: any) => {
  msgs.value.push({ role: 'assistant', text: '', pendingDraft: `${p.label}`, drafts: [], blocks: [], refs: [] });
  scroll();
});
const offDraft = aiBus.on('draft', (p: any) => {
  const last = [...msgs.value].reverse().find((x) => x.pendingDraft !== undefined && x.pendingDraft);
  const m = last || (msgs.value.push({ role: 'assistant', text: '', drafts: [], blocks: [], refs: [] }), msgs.value[msgs.value.length - 1]);
  m.pendingDraft = '';
  if (p.error) m.error = $t('aiDock.draftingFailed0', [p.error]);
  else { m.text = $t('aiDock.draftedAnActionBasedOn', [p.item?.label || '']); m.drafts.push(p.draft); }
  scroll();
});
function focusInput() { nextTick(() => (inputRef.value?.focus?.() || (document.querySelector('.dock .inp textarea') as HTMLElement | null)?.focus())); }
let timer: any;
const offFocus = aiBus.on('focus-input', focusInput);
onMounted(() => {
  window.addEventListener('ask-assistant', onAsk);
  window.addEventListener('hm-ask-legacy', onLegacy);
  loadPending();
  timer = setInterval(() => session.user && aiState.dockOpen && loadPending(), 20000);
});
onUnmounted(() => { window.removeEventListener('ask-assistant', onAsk); window.removeEventListener('hm-ask-legacy', onLegacy); clearInterval(timer); offFocus(); offPend(); offDraft(); });
</script>

<style scoped>
.dock { grid-area: dock; position: relative; min-width: 0; min-height: 0; height: 100%; z-index: var(--hm-z-dock); }
.edge { position: absolute; inset: 8px 0 8px 2px; width: calc(var(--hm-dock-tab-w) - 4px); display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 12px 0;
  border: 1px solid var(--hm-hairline); border-right: 0; border-radius: 10px 0 0 10px; background: var(--hm-glass-strong); backdrop-filter: var(--hm-glass-blur); cursor: pointer; color: var(--hm-primary-press);
  box-shadow: -2px 0 10px rgba(15, 32, 60, .05); font: inherit; }
.edge:hover { background: #fff; box-shadow: -4px 0 16px rgba(20, 99, 214, .12); }
.edge.off { color: var(--hm-text-3); } .edge.off .spk { background: var(--hm-text-3); }
.vt { writing-mode: vertical-rl; letter-spacing: .3em; font-size: 12px; font-weight: 600; } .tcu { text-combine-upright: all; letter-spacing: 0; margin-bottom: 4px; }
/* Latin text: rotate the whole word instead of stacking letters */
.vt.lat { text-orientation: sideways; letter-spacing: .06em; white-space: nowrap; } .vt.lat .tcu { text-combine-upright: none; margin: 0 0 0 0; padding-bottom: 4px; }
.cnt, .pbadge { font-style: normal; font-size: 10.5px; border-radius: var(--hm-r-pill); padding: 0 5px; line-height: 16px; color: #fff; background: var(--hm-amber); }
.pbadge { background: var(--hm-fault); }
.spk { width: 14px; height: 14px; background: var(--hm-ai-grad); clip-path: polygon(50% 0, 62% 38%, 100% 50%, 62% 62%, 50% 100%, 38% 62%, 0 50%, 38% 38%); flex: none; display: inline-block; }
.spk.big { width: 16px; height: 16px; }

.panel { position: absolute; inset: 0; display: flex; flex-direction: column; background: var(--hm-glass-strong); backdrop-filter: var(--hm-glass-blur); border-left: 1px solid var(--hm-border); overflow: hidden; }
.panel::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 1px; background: linear-gradient(180deg, var(--hm-ai-1), var(--hm-ai-2) 50%, var(--hm-ai-3)); opacity: .55; pointer-events: none; }
.rz { position: absolute; left: -3px; top: 0; bottom: 0; width: 7px; cursor: ew-resize; z-index: 2; }
.rz:hover, :global(body.hm-resizing) .rz { background: linear-gradient(90deg, transparent 2px, rgba(20, 99, 214, .5) 3px, transparent 4px); }
:global(body.hm-resizing) { cursor: ew-resize; user-select: none; }
.ph { display: flex; align-items: center; gap: 6px; padding: 9px 8px 9px 14px; border-bottom: 1px solid var(--hm-hairline); }
.ph .ttl { font-size: 13.5px; max-width: 46%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ph .el-button { margin: 0; padding: 4px 6px; } .grow { flex: 1; }
.mode { font-size: 10.5px; padding: 0 7px; border-radius: var(--hm-r-pill); line-height: 17px; background: var(--hm-run-soft); color: var(--hm-run-ink); }
.mode.off { background: var(--hm-offline-soft); color: var(--hm-offline-ink); } .mode.demo, .mode.record { background: var(--hm-amber-soft); color: var(--hm-amber-ink); }
.offv { flex: 1; display: flex; flex-direction: column; justify-content: center; padding: 10px 18px 40px; }
.offtips { margin: 8px auto 0; font-size: 11.5px; color: var(--hm-text-3); line-height: 2; text-align: center; }
.hist { max-height: 320px; overflow: auto; } .hrow { display: flex; align-items: center; gap: 4px; padding: 3px 2px; border-radius: 6px; }
.hrow.cur { background: var(--hm-primary-soft); } .ht { flex: 1; cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.pend { border-bottom: 1px solid #f0e6cc; background: #fffaf0; }
.pbar { display: flex; align-items: center; gap: 6px; padding: 6px 12px; font-size: 12.5px; color: var(--hm-idle-ink); cursor: pointer; }
.plist { padding: 0 10px 8px; display: flex; flex-direction: column; gap: 6px; max-height: 300px; overflow: auto; }
.msgs { flex: 1; overflow: auto; padding: 12px 14px; display: flex; flex-direction: column; gap: 12px; }
.hello { font-size: 13px; color: var(--hm-text-2); line-height: 1.7; }
.hh { display: flex; align-items: center; gap: 8px; font-weight: 600; color: var(--hm-text); font-size: 14px; margin: 4px 0 6px; }
.how { margin: 0 0 10px; padding-left: 18px; font-size: 12.5px; color: var(--hm-text-2); }
.hints .el-button { margin: 3px 4px 3px 0; }
kbd { font-family: var(--hm-font-mono); font-size: 10px; border: 1px solid var(--hm-border-strong); border-bottom-width: 2px; border-radius: 3px; padding: 0 3px; background: #fff; color: var(--hm-text-2); }
.m.user { display: flex; justify-content: flex-end; }
.ub { background: var(--hm-primary); color: #fff; padding: 7px 11px; border-radius: 12px 12px 3px 12px; max-width: 88%; white-space: pre-wrap; font-size: 13.5px; }
.uctx { font-size: 11px; opacity: .8; margin-top: 3px; border-top: 1px solid rgba(255, 255, 255, .25); padding-top: 3px; }
.ab { font-size: 13.5px; color: var(--hm-text); max-width: 100%; }
.prog { display: flex; flex-direction: column; gap: 3px; margin-bottom: 4px; }
.pl { font-size: 12.5px; color: var(--hm-text-3); display: flex; align-items: center; gap: 6px; } .pd { width: 6px; height: 6px; border-radius: 50%; background: var(--hm-run); }
.pl.last .pd { background: var(--hm-cyan); animation: pulse 1s infinite; } @keyframes pulse { 50% { opacity: .3; } }
.mdx { line-height: 1.65; } .mdx :deep(p) { margin: 3px 0; } .mdx :deep(ul), .mdx :deep(ol) { margin: 3px 0; padding-left: 18px; }
.mdx :deep(h3), .mdx :deep(h4), .mdx :deep(h5) { margin: 6px 0 3px; font-size: 13.5px; }
.mdx :deep(.mdt) { border-collapse: collapse; margin: 4px 0; font-size: 12px; } .mdx :deep(.mdt td), .mdx :deep(.mdt th) { border: 1px solid var(--hm-border); padding: 2px 6px; }
.mdx :deep(code) { background: var(--hm-surface-3); padding: 0 3px; border-radius: 3px; }
.chips { display: flex; flex-wrap: wrap; gap: 5px; margin: 4px 0; }
.chip { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; border-radius: 999px; padding: 2px 9px; cursor: pointer; text-decoration: none; }
.chip.ok { background: var(--hm-run-soft); color: var(--hm-run-ink); } .chip.nav { background: var(--hm-primary-soft); color: var(--hm-primary-press); }
.card { border: 1px solid var(--hm-border); border-radius: 10px; padding: 8px 10px; margin: 6px 0; background: var(--hm-surface-2); box-shadow: none; }
.card.confirm { border-color: #ecd9a8; background: #fffdf5; } .card.confirm.done { opacity: .7; }
.ch { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.pv { white-space: pre-wrap; font-size: 12.5px; background: #fff; border-radius: 6px; padding: 6px 8px; margin: 6px 0; font-family: inherit; border: 1px solid #f0e6cc; }
.cb { display: flex; gap: 6px; } .rp { max-height: 200px; overflow: auto; margin-top: 4px; } .warnline { color: var(--hm-idle-ink); margin-top: 4px; }
.err { color: var(--hm-fault-ink); background: var(--hm-fault-soft); border-radius: 6px; padding: 5px 8px; margin-top: 4px; font-size: 12.5px; }
.ctx { font-size: 11.5px; padding: 6px 12px; border-top: 1px solid var(--hm-hairline); color: var(--hm-text-2); background: rgba(247, 249, 252, .75); }
.cl1 { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ctx .x { cursor: pointer; vertical-align: middle; margin-left: 3px; }
.chipz { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; max-height: 76px; overflow: auto; align-items: center; }
.chipz :deep(.hm-tag) { max-width: 100%; }
.clr { border: 0; background: transparent; color: var(--hm-text-3); font: inherit; font-size: 11px; cursor: pointer; padding: 0 4px; } .clr:hover { color: var(--hm-fault-ink); }
.qa { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.qb { border: 1px solid var(--hm-primary-soft-2); background: #fff; color: var(--hm-primary-press); font: inherit; font-size: 11.5px; padding: 2px 9px; border-radius: var(--hm-r-pill); cursor: pointer; }
.qb:hover { background: var(--hm-primary-soft); }
.inp { display: flex; gap: 8px; padding: 8px 10px 10px; align-items: flex-end; }
.inp :deep(.el-textarea__inner) { line-height: 1.55; padding: 8px 10px; }
.sendbtn { flex: none; width: 36px; height: 36px; margin-bottom: 2px; }
.sendbtn.stop { border-color: var(--hm-primary); color: var(--hm-primary); }
.stopsq { display: block; width: 11px; height: 11px; border-radius: 2px; background: currentColor; }
</style>
