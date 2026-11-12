<template>
  <Teleport to="body">
    <Transition name="hm-fade">
      <div v-if="open" class="pal-mask" @mousedown.self="close">
        <div class="palette hm-glass" role="dialog" aria-modal="true" :aria-label="$t('commandPalette.searchCommands')" data-test="palette" @keydown="onKey">
          <div class="pi">
            <el-icon class="pic"><Search /></el-icon>
            <input ref="inp" v-model="q" :placeholder="$t('commandPalette.searchPagesDocumentNumbersPart')" :aria-label="$t('commandPalette.search')" data-test="palette-input" autocomplete="off" />
            <span v-if="busy" class="spin" aria-hidden="true" />
            <kbd>Esc</kbd>
          </div>
          <div class="pl" ref="list" role="listbox">
            <template v-for="sec in sections" :key="sec.key">
              <div v-if="sec.items.length" class="ps">{{ sec.label }}</div>
              <button v-for="it in sec.items" :key="it.id" class="po" role="option" :class="{ cur: it.idx === cur, dis: it.disabled, ai: it.kind === 'ai' }" :aria-selected="it.idx === cur"
                :data-kind="it.kind" @mouseenter="cur = it.idx" @click="run(it)">
                <span class="oi"><el-icon><component :is="it.icon" /></el-icon></span>
                <span class="ot"><span class="t1" v-html="it.html || esc(it.label)" /><span v-if="it.sub" class="t2">{{ it.sub }}</span></span>
                <span v-if="it.hint" class="oh">{{ it.hint }}</span>
              </button>
            </template>
            <div v-if="!flat.length" class="empty">{{ $t('commandPalette.nothingFoundFor0Try', [q]) }}</div>
          </div>
          <div class="pf"><span><kbd>↑</kbd><kbd>↓</kbd> {{ $t('commandPalette.select') }}</span><span><kbd>Enter</kbd> {{ $t('commandPalette.open') }}</span><span><kbd>Ctrl</kbd>+<kbd>K</kbd> {{ $t('commandPalette.openAnytime') }}</span><span class="grow" /><span class="ai-st">{{ aiOn('ask') ? $t('commandPalette.aiOnline') : AI_OFF }}</span></div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { computed, nextTick, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { AI_OFF, aiContext, aiOffText, aiOn, aiState, askAi, openAiDock } from '../ai';
import { get, session } from '../api';
import { ALL_NAV as ALL_NAV0, navAllowed } from './nav';
import { GUIDES, guideForPath, openGuide } from '../tour';
import { setNavCollapsed, shell } from './shell';

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ 'update:open': [v: boolean] }>();
const router = useRouter();
const q = ref(''), cur = ref(0), inp = ref<HTMLInputElement>(), list = ref<HTMLElement>(), busy = ref(false), records = ref<any[]>([]);

// Record search: find by number/code/name in these entities (3 each)
const RECORDS: [string, string, string][] = [
  ['work_order', $t('common.workOrders'), 'Tickets'], ['part', $t('common.part'), 'Postcard'], ['equipment', $t('common.equipment'), 'Monitor'], ['ncr', $t('common.nonconformances'), 'CircleClose'],
  ['repair_order', $t('common.repairOrders'), 'SetUp'], ['heat', $t('common.heat'), 'HotWater'], ['alarm', $t('common.alarm'), 'Warning'], ['material', $t('common.material'), 'Box'], ['andon', $t('common.andon'), 'Bell'],
];
const COMMANDS = [
  { id: 'c-tour', label: $t('commandPalette.guidedTourOfThisPage'), icon: 'Guide', kw: $t('commandPalette.tourHelpTutorialGuide'), run: () => { const g = guideForPath(router.currentRoute.value.path); openGuide(g ? g.key : 'welcome'); } },
  { id: 'c-ai', label: $t('commandPalette.openCollapseAiPanel'), icon: 'MagicStick', kw: $t('commandPalette.aiPanelAssistant'), run: () => openAiDock(!aiState.dockOpen) },
  { id: 'c-ctx', label: $t('commandPalette.clearAiContext'), icon: 'Delete', kw: $t('commandPalette.clearContext'), run: () => aiContext.clear() },
  { id: 'c-nav', label: $t('commandPalette.collapseExpandSideMenu'), icon: 'Fold', kw: $t('commandPalette.menuCollapseExpandNav'), run: () => setNavCollapsed(!shell.navCollapsed) },
  { id: 'c-aiset', label: $t('commandPalette.aiFeatureSettings'), icon: 'Setting', kw: $t('commandPalette.aiSwitchesSettings'), run: () => router.push('/system/ai') },
  { id: 'c-wo', label: $t('commandPalette.newWorkOrder'), icon: 'Plus', kw: $t('commandPalette.newWorkOrderWoCreate'), run: () => router.push('/e/work_order') },
  { id: 'c-screen', label: $t('commandPalette.openShopFloorDisplay'), icon: 'FullScreen', kw: $t('commandPalette.displayScreenAndonBoard'), run: () => router.push('/screen') },
  { id: 'c-out', label: $t('commandPalette.signOut'), icon: 'SwitchButton', kw: $t('commandPalette.signOutLogout'), run: () => { session.token = ''; session.user = null; localStorage.removeItem('mes_token'); router.push('/login'); } },
];

const esc = (s: string) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' } as any)[c]);
const hl = (s: string, k: string) => { const i = k ? s.toLowerCase().indexOf(k.toLowerCase()) : -1; return i < 0 ? esc(s) : esc(s.slice(0, i)) + '<mark>' + esc(s.slice(i, i + k.length)) + '</mark>' + esc(s.slice(i + k.length)); };
const norm = (s: string) => s.toLowerCase().replace(/\s+/g, '');

const sections = computed(() => {
  const ALL_NAV = ALL_NAV0.filter(navAllowed);
  const k = q.value.trim(), nk = norm(k);
  let idx = 0;
  const pages = (nk ? ALL_NAV.filter((n) => norm(n.label + n.kw + n.path + n.group).includes(nk)) : ALL_NAV.filter((n) => ['/', '/equipment/monitor', '/e/work_order', '/quality', '/trace', '/system/ai'].includes(n.path)))
    .slice(0, nk ? 8 : 6).map((n) => ({ id: 'p' + n.path, kind: 'page', label: n.label, html: hl(n.label, k), sub: n.group, icon: 'Document', hint: n.feature && !aiOn(n.feature) ? $t('common.off') : $t('aiLayer.page'),
      disabled: false, run: () => router.push(n.path) }));
  const recs = records.value.map((r) => ({ id: 'r' + r.entity + r.id, kind: 'record', label: r.label, html: hl(r.label, k), sub: r.entityLabel, icon: r.icon, hint: $t('common.record'), disabled: false,
    run: () => router.push(r.entity === 'part' && r.sn ? `/trace?sn=${encodeURIComponent(r.sn)}` : `/e/${r.entity}?id=${r.id}`) }));
  const cmds = COMMANDS.filter((c) => !nk || norm(c.label + c.kw).includes(nk)).slice(0, nk ? 5 : 4)
    .map((c) => ({ id: c.id, kind: 'cmd', label: c.label, html: hl(c.label, k), sub: '', icon: c.icon, hint: $t('commandPalette.commands'), disabled: false, run: c.run }));
  const ai = k ? [{ id: 'ai', kind: 'ai', label: $t('commandPalette.askAiToDo0', [k]), html: `${$t('commandPalette.askAiToDo')}<b>${esc(k)}</b>`, sub: aiOn('ask') ? (aiState.items.length ? $t('commandPalette.with0ContextItems', [aiState.items.length]) : $t('commandPalette.runsInTheAiPanel')) : aiOffText('ask'),
    icon: 'MagicStick', hint: 'Enter', disabled: !aiOn('ask'), run: () => askAi(k, null, { action: 'palette' }) }] : [];
  // When pages/records match they come first (finding things takes priority); otherwise "Let AI do it" is first
  // Guided tours: found by typing "tour"/"help"/module name; this page's tour first, then task walkthroughs
  const here = guideForPath(router.currentRoute.value.path);
  const gl = [...(here ? [here] : []), ...GUIDES.filter((g) => g.kind === 'task'), ...GUIDES.filter((g) => g !== here && g.kind !== 'task')];
  const tours = (nk ? gl.filter((g, i) => gl.indexOf(g) === i && norm($t('commandPalette.tourGuidedHelpTutorial') + g.title + g.desc + (g.kw || '')).includes(nk)) : []).slice(0, 8)
    .map((g) => ({ id: 't-' + g.key, kind: 'tour', label: $t('commandPalette.tour0', [g.title]), html: hl($t('commandPalette.tour0', [g.title]), k), sub: g === here ? $t('commandPalette.thisPage0', [g.desc]) : g.desc,
      icon: g.kind === 'task' ? 'Pointer' : g.kind === 'welcome' ? 'Compass' : 'Guide', hint: $t('commandPalette.tour'), disabled: false, run: () => openGuide(g.key) }));
  const found = recs.length || pages.length;
  const secs = [
    ...(found ? [] : [{ key: 'ai', label: 'AI', items: ai }]),
    { key: 'page', label: $t('aiLayer.page'), items: pages },
    { key: 'tour', label: $t('common.guidedTour'), items: tours },
    { key: 'rec', label: $t('common.record'), items: recs },
    ...(found ? [{ key: 'ai', label: 'AI', items: ai }] : []),
    { key: 'cmd', label: $t('commandPalette.commands'), items: cmds },
  ];
  for (const s of secs) for (const it of s.items as any[]) it.idx = idx++;
  return secs;
});
const flat = computed(() => sections.value.flatMap((s) => s.items as any[]));

let seq = 0, tmr: any;
watch(q, (v) => {
  cur.value = 0;
  clearTimeout(tmr);
  const k = v.trim();
  if (k.length < 2) { records.value = []; busy.value = false; return; }
  tmr = setTimeout(async () => {
    const my = ++seq; busy.value = true;
    const res = await Promise.all(RECORDS.map(async ([e, lab, icon]) => {
      try {
        const r = await get(`/api/e/${e}?q=${encodeURIComponent(k)}&size=3`, true);
        return r.items.map((x: any) => ({ entity: e, entityLabel: lab, icon, id: x.id, sn: x.sn,
          label: [x.code || x.sn || x.username || x.title || x.name || '#' + x.id, x.code && x.name ? x.name : x.equipment__label || x.product__label || x.msg || ''].filter(Boolean).join('  ') }));
      } catch { return []; }
    }));
    if (my !== seq) return;
    records.value = res.flat().slice(0, 9); busy.value = false;
  }, 220);
});
watch(() => props.open, (v) => { if (v) { q.value = ''; records.value = []; cur.value = 0; nextTick(() => inp.value?.focus()); } });

function close() { emit('update:open', false); }
function run(it: any) {
  if (it.disabled) return;
  close();
  it.run();
}
function onKey(e: KeyboardEvent) {
  const n = flat.value.length;
  if (e.key === 'Escape') { e.preventDefault(); close(); }
  else if (e.key === 'ArrowDown') { e.preventDefault(); cur.value = (cur.value + 1) % Math.max(1, n); scrollCur(); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); cur.value = (cur.value - 1 + n) % Math.max(1, n); scrollCur(); }
  else if (e.key === 'Enter') { e.preventDefault(); const it = flat.value[cur.value]; if (it) run(it); }
}
function scrollCur() { nextTick(() => list.value?.querySelector('.po.cur')?.scrollIntoView({ block: 'nearest' })); }
</script>

<style scoped>
.pal-mask { position: fixed; inset: 0; z-index: var(--hm-z-palette); background: rgba(15, 27, 45, .18); display: flex; justify-content: center; align-items: flex-start; padding-top: 12vh; }
.palette { width: min(640px, calc(100vw - 24px)); border-radius: 14px; box-shadow: var(--hm-e3); background: var(--hm-glass-strong); overflow: hidden; display: flex; flex-direction: column; max-height: 70vh; }
.pi { display: flex; align-items: center; gap: 10px; padding: 0 14px; height: 52px; border-bottom: 1px solid var(--hm-hairline); }
.pic { font-size: 18px; color: var(--hm-text-3); }
.pi input { flex: 1; border: 0; outline: none; background: transparent; font: inherit; font-size: 15px; color: var(--hm-text); }
.spin { width: 14px; height: 14px; border: 2px solid var(--hm-primary-soft-2); border-top-color: var(--hm-primary); border-radius: 50%; animation: spin .8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.pl { overflow: auto; padding: 6px; flex: 1; }
.ps { font-size: 11px; color: var(--hm-text-3); letter-spacing: .08em; padding: 8px 10px 4px; }
.po { width: 100%; display: flex; align-items: center; gap: 10px; padding: 7px 10px; border: 0; background: transparent; border-radius: 8px; cursor: pointer; font: inherit; text-align: left; color: var(--hm-text); }
.po.cur { background: var(--hm-primary-soft); }
.po.dis { color: var(--hm-text-3); cursor: default; }
.oi { width: 28px; height: 28px; border-radius: 7px; display: grid; place-items: center; background: var(--hm-surface-3); color: var(--hm-text-2); flex: none; font-size: 15px; }
.po.ai .oi { background: var(--hm-ai-grad); color: #fff; } .po.ai.dis .oi { background: var(--hm-surface-3); color: var(--hm-text-3); }
.ot { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.t1 { font-size: 13.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; } .t2 { font-size: 11.5px; color: var(--hm-text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.t1 :deep(mark) { background: rgba(217, 130, 43, .22); color: inherit; border-radius: 2px; }
.oh { font-size: 11px; color: var(--hm-text-3); }
.empty { padding: 24px 16px; text-align: center; color: var(--hm-text-3); font-size: 13px; }
.pf { display: flex; gap: 14px; align-items: center; padding: 8px 14px; border-top: 1px solid var(--hm-hairline); font-size: 11.5px; color: var(--hm-text-3); background: rgba(247, 249, 252, .7); }
.pf .grow { flex: 1; } .ai-st { color: var(--hm-text-3); }
kbd { font-family: var(--hm-font-mono); font-size: 10.5px; border: 1px solid var(--hm-border-strong); border-bottom-width: 2px; border-radius: 4px; padding: 0 4px; background: #fff; color: var(--hm-text-2); margin-right: 2px; }
</style>
