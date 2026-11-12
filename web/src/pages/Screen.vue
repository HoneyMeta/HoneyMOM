<template>
  <div class="scr-root" :class="{ hc, idle: idle && !showCtl }" data-test="big-screen" @mousemove="poke" @click="poke">
    <div class="stage" :style="stageStyle">
      <!-- Top bar -->
      <header class="top">
        <div class="brand"><img src="/brand/mark.svg" alt="" /><div><h1>{{ company }}</h1><small>{{ $t('screen.honeymomManufacturingCommandCenter') }}</small></div></div>
        <nav class="scenes" role="tablist" :aria-label="$t('screen.displayPages')">
          <button v-for="(s, i) in SCENES" :key="s.id" role="tab" :aria-selected="i === cur" :class="{ on: i === cur }" :data-scene-tab="i" @click="go(i)">
            <span>{{ s.label }}</span><i v-if="i === cur && !paused" class="prog" :style="{ animationDuration: interval + 's' }" :key="tick" />
          </button>
        </nav>
        <div class="clock">
          <div class="t hm-num" data-test="screen-clock">{{ clock.time }}</div>
          <div class="d">{{ clock.date }} {{ clock.week }}<span v-if="dataClock" class="live"><i />{{ $t('screen.shopFloorData') }}</span></div>
        </div>
      </header>

      <!-- Metrics -->
      <section class="kpis">
        <div v-for="k in kpis" :key="k.label" class="kp" :class="k.cls">
          <small>{{ k.label }}</small>
          <b class="hm-num"><HmNumber :value="k.value" :format="k.format" :digits="k.digits" /><em v-if="k.unit">{{ k.unit }}</em></b>
          <span class="kh">{{ k.hint }}</span>
          <svg v-if="k.spark && k.spark.length > 1" class="sp" viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true"><path :d="sparkPath(k.spark)" /></svg>
        </div>
      </section>

      <!-- Carousel pages -->
      <Transition name="sc" mode="out-in">
        <main :key="cur" class="scene" :data-scene="SCENES[cur].id" data-test="scene">
          <!-- 1 Production execution -->
          <template v-if="SCENES[cur].id === 'prod'">
            <div class="col" style="grid-column: span 7">
              <div class="panel wo-panel">
                <h3><i />{{ $t('screen.productionTaskCompletion') }}<small>{{ $t('screen.n0WipWorkOrders', [orders.length]) }}</small></h3>
                <div class="tbl t-wo"><div class="th"><span>{{ $t('common.woNo') }}</span><span>{{ $t('common.product') }}</span><span>{{ $t('common.customer') }}</span><span class="n">{{ $t('common.plan') }}</span><span class="n">{{ $t('common.done') }}</span><span>{{ $t('orderTimeline.completion') }}</span><span class="n">{{ $t('common.scrapped') }}</span><span>{{ $t('common.dueDate') }}</span><span>{{ $t('common.status') }}</span></div>
                  <HmAutoScroll class="tb" :speed="22" :paused="paused">
                    <div v-for="o in orders" :key="o.id" class="tr" :class="{ late: o.late, risk: o.risk }">
                      <span class="hm-mono">{{ o.code }}</span><span class="el">{{ o.product }}</span><span class="el">{{ o.customer }}</span>
                      <span class="n hm-num">{{ o.qty }}</span><span class="n hm-num">{{ o.qty_done }}</span>
                      <span class="pc"><i><b :style="{ width: Math.min(100, o.qty ? o.qty_done / o.qty * 100 : 0) + '%' }" /></i><em class="hm-num">{{ fmtPct(o.qty ? o.qty_done / o.qty : 0, 0) }}</em></span>
                      <span class="n hm-num">{{ o.qty_scrap }}</span><span class="hm-num">{{ (o.due || '').slice(5, 16) }}</span>
                      <span><em class="pill" :class="o.late ? 'bad' : o.risk ? 'warn' : o.status === 'in_progress' ? 'run' : ''">{{ o.late ? $t('common.overdue') : o.risk ? $t('orderTimeline.atRiskOfDelay') : o.status === 'in_progress' ? $t('common.inProduction') : $t('common.released') }}</em></span>
                    </div>
                    <div v-if="!orders.length" class="none">{{ $t('common.noWorkOrdersInProgress') }}</div>
                  </HmAutoScroll>
                </div>
              </div>
              <div class="panel grow">
                <h3><i />{{ $t('screen.workOrderPlanProgress') }}<small>{{ $t('screen.verticalLinesNowDue') }}</small></h3>
                <OrderTimeline big :orders="orders" :now="now" :max="4" :back="6" :ahead="18" />
              </div>
            </div>
            <div class="col" style="grid-column: span 5">
              <div class="panel">
                <h3><i />{{ $t('screen.todaySOperationFlow') }}<small>{{ $t('common.pcs') }}</small></h3>
                <div class="flow">
                  <div v-for="(f, i) in flow" :key="f.label" class="fs"><small>{{ f.label }}</small><b class="hm-num"><HmNumber :value="f.value" format="int" /></b><span v-if="i < flow.length - 1" class="ar">›</span></div>
                </div>
              </div>
              <div class="panel grow">
                <h3><i />{{ $t('home.outputLast24H') }}<small>{{ $t('screen.hourly') }}</small></h3>
                <Chart v-if="ck" :option="hourOpt" :height="470" />
              </div>
            </div>
          </template>

          <!-- 2 Equipment status -->
          <template v-else-if="SCENES[cur].id === 'equip'">
            <div class="col" style="grid-column: span 8">
              <div class="panel grow">
                <h3><i />{{ $t('home.livePlantMap') }}<small>{{ $t('screen.n0RunningRate1', [lay?.plant || '', fmtPct(runRate, 0)]) }}</small></h3>
                <div class="mapbox"><FactoryMap big fill :layout="lay" :live="liveItems" :now="now" /></div>
              </div>
            </div>
            <div class="col" style="grid-column: span 4">
              <div class="panel">
                <h3><i />{{ $t('screen.equipmentStatus') }}</h3>
                <div class="stc">
                  <div v-for="s in statusCounts" :key="s.k" class="sc-i" :class="'t-' + s.k"><b class="hm-num">{{ s.n }}</b><small>{{ s.label }}</small></div>
                </div>
              </div>
              <div class="panel grow">
                <h3><i />{{ $t('screen.equipmentRunTable') }}<small>{{ $t('screen.oeeLast8H') }}</small></h3>
                <div class="tbl t-eq"><div class="th"><span>{{ $t('common.equipment') }}</span><span>{{ $t('common.status') }}</span><span>OEE</span><span>{{ $t('common.alarm') }}</span></div>
                  <HmAutoScroll class="tb" :speed="26" :paused="paused" data-test="eq-scroll">
                    <div v-for="e in eqRows" :key="e.code" class="tr" :class="'t-' + e.tone">
                      <span class="el"><b class="hm-mono">{{ e.code }}</b> {{ e.name }}</span>
                      <span class="stt"><i />{{ e.text }}</span>
                      <span class="pc"><i><b :style="{ width: (e.oee || 0) * 100 + '%' }" /></i><em class="hm-num">{{ e.oee === null ? '—' : fmtPct(e.oee, 0) }}</em></span>
                      <span class="el al">{{ e.alarm || '—' }}</span>
                    </div>
                  </HmAutoScroll>
                </div>
              </div>
            </div>
          </template>

          <!-- 3 Quality & PM -->
          <template v-else>
            <div class="col" style="grid-column: span 4">
              <div class="panel">
                <h3><i />{{ $t('screen.todaySDefectDistribution') }}<small>{{ $t('common.pcs') }}</small></h3>
                <Chart v-if="ck" :option="pieOpt" :height="300" />
              </div>
              <div class="panel grow">
                <h3><i />{{ $t('screen.openNonconformances') }}<small>{{ $t('common.n03', [(sd?.ncr || []).length]) }}</small></h3>
                <div class="tbl t-ncr"><div class="th"><span>NCR</span><span>{{ $t('common.defect') }}</span><span>{{ $t('common.workstation') }}</span><span>{{ $t('common.discover') }}</span></div>
                  <HmAutoScroll class="tb" :speed="20" :paused="paused">
                    <div v-for="n in sd?.ncr || []" :key="n.id" class="tr"><span class="hm-mono">{{ n.code }}</span><span class="el">{{ n.defect__label || $t('ncr.uncategorized') }}</span><span>{{ n.station || '—' }}</span><span class="hm-num">{{ (n.found_at || '').slice(11, 16) }}</span></div>
                    <div v-if="!(sd?.ncr || []).length" class="none ok">{{ $t('ncr.noOpenNonconformances') }}</div>
                  </HmAutoScroll>
                </div>
              </div>
            </div>
            <div class="col" style="grid-column: span 4">
              <div class="panel">
                <h3><i />{{ $t('screen.qualityKpis') }}</h3>
                <div class="qg">
                  <div><small>{{ $t('common.scrapRate') }}</small><b class="hm-num" :class="{ bad: (K?.scrap_rate.value || 0) > 0.03 }">{{ fmtPct(K?.scrap_rate.value, 2) }}</b></div>
                  <div><small>{{ $t('common.scrapped') }}</small><b class="hm-num">{{ K?.scrap_rate.scrap ?? '—' }}</b></div>
                  <div><small>{{ $t('common.rework') }}</small><b class="hm-num">{{ K?.scrap_rate.rework ?? '—' }}</b></div>
                  <div><small>{{ $t('energy.testing') }}</small><b class="hm-num">{{ K?.inspected ?? '—' }}</b></div>
                </div>
              </div>
              <div class="panel grow">
                <h3><i />{{ $t('screen.checksPmDue') }}<small>{{ $t('screen.includingNext3Days') }}</small></h3>
                <div class="tbl t-mt"><div class="th"><span>{{ $t('common.equipment') }}</span><span>{{ $t('common.item') }}</span><span>{{ $t('common.expires') }}</span><span>{{ $t('common.status') }}</span></div>
                  <HmAutoScroll class="tb" :speed="22" :paused="paused" data-test="maint-scroll">
                    <div v-for="m in sd?.maint || []" :key="m.id" class="tr" :class="{ late: m.overdue_days > 0 }">
                      <span class="el">{{ (m.equipment__label || '').split(' ')[0] }}</span><span class="el">{{ m.name.replace((m.equipment__label || '').split(' ').slice(1).join(' '), '').trim() || m.name }}</span>
                      <span class="hm-num">{{ (m.next_due || '').slice(5) }}</span>
                      <span><em class="pill" :class="m.overdue_days > 0 ? 'bad' : m.overdue_days === 0 ? 'warn' : ''">{{ m.overdue_days > 0 ? $t('common.n0DaysOverdue', [m.overdue_days]) : m.overdue_days === 0 ? $t('common.today') : $t('screen.in0Days', [-m.overdue_days]) }}</em></span>
                    </div>
                  </HmAutoScroll>
                </div>
              </div>
            </div>
            <div class="col" style="grid-column: span 4">
              <div class="panel grow">
                <h3><i />{{ $t('screen.toolLife') }}<small>{{ $t('screen.byShareUsed') }}</small></h3>
                <div class="tbl t-tl"><div class="th"><span>{{ $t('common.machineTool') }}</span><span>{{ $t('screen.toolPos') }}</span><span>{{ $t('common.life') }}</span><span class="n">{{ $t('screen.used') }}</span></div>
                  <HmAutoScroll class="tb" :speed="24" :paused="paused">
                    <div v-for="t in sd?.tools || []" :key="t.id" class="tr" :class="{ late: t.status === 'change', risk: t.status === 'warn' }">
                      <span class="hm-mono">{{ (t.equipment__label || '').split(' ')[0] }}</span><span class="el">{{ t.code }} {{ t.name }}</span>
                      <span class="pc"><i><b :style="{ width: Math.min(100, (t.life_pct || 0) * 100) + '%' }" /></i><em class="hm-num">{{ fmtPct(t.life_pct, 0) }}</em></span>
                      <span class="n hm-num">{{ t.used || 0 }}/{{ t.life_limit || '—' }}</span>
                    </div>
                  </HmAutoScroll>
                </div>
              </div>
            </div>
          </template>
        </main>
      </Transition>

      <!-- Andon -->
      <footer class="foot"><AndonStrip big :open="ck?.andon?.open || []" :closed-today="ck?.andon?.closed_today || 0" :now="now" /></footer>
    </div>

    <!-- Control bar (hidden after 4 s without mouse movement) -->
    <div class="ctl" :class="{ show: showCtl }" data-test="screen-controls">
      <button :title="paused ? $t('screen.resumeCarouselSpace') : $t('screen.pauseCarouselSpace')" data-test="screen-pause" @click="paused = !paused"><el-icon><component :is="paused ? 'VideoPlay' : 'VideoPause'" /></el-icon></button>
      <button :title="$t('screen.previousPage')" @click="go(cur - 1)"><el-icon><ArrowLeft /></el-icon></button>
      <button :title="$t('screen.nextPage')" @click="go(cur + 1)"><el-icon><ArrowRight /></el-icon></button>
      <label>{{ $t('screen.carousel') }}<select v-model.number="interval" data-test="screen-interval"><option v-for="s in INTERVALS" :key="s" :value="s">{{ $t('agentlib.n0S', [s]) }}</option></select></label>
      <button :class="{ on: hc }" :title="$t('screen.highContrastH')" data-test="screen-hc" @click="hc = !hc"><el-icon><Sunny /></el-icon>{{ $t('screen.highContrast') }}</button>
      <button :title="$t('screen.fullScreenF')" @click="fullscreen"><el-icon><FullScreen /></el-icon></button>
      <button :title="$t('screen.exitDisplay')" @click="router.push('/')"><el-icon><Close /></el-icon>{{ $t('screen.exit') }}</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// Shop-floor display: designed at 1920×1080, scaled proportionally to the window (sharp on 4K TVs too). KPIs fixed at top, three rotating pages in the middle (production execution / equipment status / quality & PM),
// long tables auto-scroll, Andon strip at the bottom. Rotation interval, pause, high contrast set in the control bar (stored locally); URL params also work: ?scene=2&interval=30&hc=1.
// Keyboard: ← → switch page, Space pause, H high contrast, F full screen.
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { session } from '../api';
import Chart from '../components/Chart.vue';
import AndonStrip from '../components/home/AndonStrip.vue';
import FactoryMap from '../components/home/FactoryMap.vue';
import OrderTimeline from '../components/home/OrderTimeline.vue';
import { STATE_TEXT, TONE, toDate, usePoll } from '../components/home/usePoll';
import { fmtPct } from '../design/format';

const router = useRouter(), route = useRoute();
const SCENES = [{ id: 'prod', label: $t('screen.productionExecution') }, { id: 'equip', label: $t('screen.equipmentStatus') }, { id: 'quality', label: $t('screen.qualityMaintenance') }];
const INTERVALS = [10, 20, 30, 60, 120];
const SK = 'hm_screen';
let saved: any = {};
try { saved = JSON.parse(localStorage.getItem(SK) || '{}'); } catch { /* */ }
const q = route.query;
const cur = ref(Math.max(0, Math.min(SCENES.length - 1, (Number(q.scene) || 1) - 1)));
const interval = ref(Math.max(3, Number(q.interval) || saved.interval || 20));
const hc = ref(q.hc !== undefined ? q.hc === '1' : !!saved.hc);
const paused = ref(q.paused === '1');
watch([interval, hc], () => { try { localStorage.setItem(SK, JSON.stringify({ interval: interval.value, hc: hc.value })); } catch { /* */ } });

// Data
const cockpit = usePoll<any>('/api/home/cockpit', 10000);
const live = usePoll<any>('/api/home/live', 4000);
const layout = usePoll<any>('/api/home/layout', 120000);
const screen = usePoll<any>('/api/home/screen', 15000);
const ck = computed(() => cockpit.data.value), K = computed(() => ck.value?.kpi), sd = computed(() => screen.data.value), lay = computed(() => layout.data.value);
const liveItems = computed(() => live.data.value?.items || []);
const now = computed(() => live.data.value?.now || ck.value?.now || '');
const dataClock = computed(() => !!now.value);
const company = computed(() => ck.value?.company || session.user?.company || $t('home.honeymom'));
const orders = computed(() => ck.value?.orders || []);

// Carousel
const tick = ref(0);
let rot: any = null;
function restart() { clearInterval(rot); tick.value++; if (!paused.value) rot = setInterval(() => { cur.value = (cur.value + 1) % SCENES.length; tick.value++; }, interval.value * 1000); }
function go(i: number) { cur.value = (i + SCENES.length) % SCENES.length; restart(); }
watch([interval, paused], restart);

// Clock: uses the data clock (faster than wall time when the virtual factory is accelerated), ticks every second between fetches
const base = reactive({ data: 0, wall: 0 });
watch(now, (v) => { const d = toDate(v); if (d) { base.data = d.getTime(); base.wall = Date.now(); } }, { immediate: true });
const clockNow = ref(Date.now());
let clk: any = null;
const WEEK = [$t('screen.sunday'), $t('screen.monday'), $t('screen.tuesday'), $t('screen.wednesday'), $t('screen.thursday'), $t('screen.friday'), $t('screen.saturday')];
const clock = computed(() => {
  const t = new Date(base.data ? base.data + Math.min(4000, clockNow.value - base.wall) : clockNow.value);
  const p = (n: number) => String(n).padStart(2, '0');
  return { time: `${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())}`, date: `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())}`, week: WEEK[t.getDay()] };
});

// Scaling: the 1920×1080 stage scales up/down proportionally to the window
const vp = reactive({ w: innerWidth, h: innerHeight });
const onResize = () => { vp.w = innerWidth; vp.h = innerHeight; };
const stageStyle = computed(() => { const s = Math.min(vp.w / 1920, vp.h / 1080); return { transform: `translate(${(vp.w - 1920 * s) / 2}px, ${(vp.h - 1080 * s) / 2}px) scale(${s})` }; });

// Control bar: shown on mouse move, hidden after 4 s
const showCtl = ref(true), idle = ref(false);
let idleT: any = null;
function poke() { showCtl.value = true; idle.value = false; clearTimeout(idleT); idleT = setTimeout(() => { showCtl.value = false; idle.value = true; }, 4000); }
function fullscreen() { if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen?.().catch(() => {}); }
function onKey(e: KeyboardEvent) {
  if (e.target instanceof HTMLSelectElement) return;
  if (e.key === 'ArrowRight') go(cur.value + 1); else if (e.key === 'ArrowLeft') go(cur.value - 1);
  else if (e.key === ' ') { e.preventDefault(); paused.value = !paused.value; } else if (e.key.toLowerCase() === 'h') hc.value = !hc.value;
  else if (e.key.toLowerCase() === 'f') fullscreen();
  poke();
}
onMounted(() => { restart(); poke(); clk = setInterval(() => (clockNow.value = Date.now()), 1000); addEventListener('resize', onResize); addEventListener('keydown', onKey); });
onUnmounted(() => { clearInterval(rot); clearInterval(clk); clearTimeout(idleT); removeEventListener('resize', onResize); removeEventListener('keydown', onKey); });

// Metrics
const devTotal = computed(() => Object.values(ck.value?.status || {}).reduce((a: number, b: any) => a + Number(b || 0), 0));
const runRate = computed(() => (devTotal.value ? (ck.value?.status?.running || 0) / devTotal.value : null));
const kpis = computed(() => {
  const k = K.value || {};
  const at = k.stored?.attain;
  const nAndon = (ck.value?.andon?.open || []).length;
  return [
    { label: $t('home.storedToday'), value: k.stored?.value, format: 'int', unit: $t('common.pcs'), hint: k.stored?.plan_now ? $t('screen.plan0', [k.stored.plan_now]) : $t('home.shipped0', [k.stored?.shipped ?? '—']), spark: k.stored?.spark, cls: '' },
    { label: $t('home.planAttainment'), value: at, format: 'pct',
      hint: k.stored?.plan_state === 'ok' ? $t('screen.plan0Actual12', [k.stored.plan_now, k.stored.value, k.stored.attain_capped ? $t('screen.above150') : '']) : k.stored?.plan_state === 'tiny' ? $t('screen.plan0Actual1Plan', [k.stored.plan_now, k.stored.value]) : $t('home.noValidPlan'),
      cls: at === null || at === undefined ? '' : at >= 0.95 ? 'ok' : at >= 0.8 ? 'warn' : 'bad' },
    { label: $t('home.pouredReceivedToday'), value: k.castings?.value, format: 'int', unit: $t('common.pcs'), hint: $t('screen.last12H'), spark: k.castings?.spark, cls: '' },
    { label: $t('home.machinedToday'), value: k.machined?.value, format: 'int', unit: $t('common.pcs'), hint: $t('screen.last12H'), spark: k.machined?.spark, cls: '' },
    { label: $t('common.scrapRate'), value: k.scrap_rate?.value, format: 'pct', digits: 2, hint: $t('home.scrap0Rework1', [k.scrap_rate?.scrap ?? '—', k.scrap_rate?.rework ?? '—']), cls: (k.scrap_rate?.value || 0) > 0.03 ? 'bad' : (k.scrap_rate?.value || 0) > 0.02 ? 'warn' : 'ok' },
    { label: $t('common.avgOee'), value: k.oee?.value, format: 'pct', hint: k.oee?.worst?.[0] ? $t('home.lowest01', [k.oee.worst[0].code, fmtPct(k.oee.worst[0].OEE, 0)]) : '', spark: (k.oee?.spark || []).filter((x: any) => x !== null), cls: '' },
    { label: $t('home.runningRate'), value: runRate.value, format: 'pct', hint: $t('home.running01Machines', [ck.value?.status?.running || 0, devTotal.value]), cls: '' },
    { label: $t('screen.openAndonCalls'), value: nAndon, format: 'int', unit: $t('complaints.k2242'), hint: $t('screen.closedToday0', [ck.value?.andon?.closed_today || 0]), cls: nAndon ? 'bad' : 'ok' },
  ];
});
function sparkPath(s: number[]) {
  const lo = Math.min(...s), hi = Math.max(...s), rg = hi - lo || 1;
  return s.map((v, i) => `${i ? 'L' : 'M'}${(i / (s.length - 1) * 100).toFixed(1)},${(22 - (v - lo) / rg * 20).toFixed(1)}`).join(' ');
}
const flow = computed(() => {
  const k = K.value || {};
  return [{ label: $t('home.pouredReceived'), value: k.castings?.value }, { label: $t('common.machining'), value: k.machined?.value }, { label: $t('energy.testing'), value: k.inspected }, { label: $t('common.goodsReceipt'), value: k.stored?.value }, { label: $t('common.shipped2'), value: k.stored?.shipped }];
});
const statusCounts = computed(() => {
  const n: Record<string, number> = { run: 0, idle: 0, setup: 0, fault: 0, offline: 0 };
  for (const e of liveItems.value) { if (['crew', 'agv', 'other'].includes(e.etype)) continue; n[TONE[e.status] || 'offline']++; }
  return [{ k: 'run', label: $t('common.running'), n: n.run }, { k: 'idle', label: $t('common.idle'), n: n.idle + n.setup }, { k: 'fault', label: $t('common.fault'), n: n.fault }, { k: 'offline', label: $t('common.offline'), n: n.offline }];
});
const eqRows = computed(() => {
  const oee = new Map((sd.value?.equipment || []).map((e: any) => [e.code, e.oee8h]));
  return liveItems.value.filter((e: any) => !['crew', 'agv'].includes(e.etype) && e.etype !== 'other')
    .map((e: any) => ({ code: e.code, name: e.name, tone: TONE[e.status] || 'offline', text: STATE_TEXT[e.status] || e.status, oee: oee.get(e.code) ?? null, alarm: e.alarms?.[0]?.msg || '' }))
    .sort((a: any, b: any) => (['fault', 'idle', 'setup', 'offline', 'run'].indexOf(a.tone) - ['fault', 'idle', 'setup', 'offline', 'run'].indexOf(b.tone)) || a.code.localeCompare(b.code));
});

// Charts (light, large text)
const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim() || undefined;
const txt = computed(() => (hc.value ? '#000' : '#44536a'));
const hourOpt = computed(() => {
  const hs = ck.value.hours, H = ck.value.hourly;
  const s = (k: string) => hs.map((h: string) => H[h]?.[k] || 0);
  return { color: [css('--hm-primary'), css('--hm-cyan'), css('--hm-run'), css('--hm-fault')], tooltip: { trigger: 'axis' },
    legend: { top: 0, right: 0, itemWidth: 14, itemHeight: 10, textStyle: { color: txt.value, fontSize: 15 } }, grid: { left: 46, right: 8, top: 36, bottom: 28 },
    xAxis: { type: 'category', data: hs.map((h: string) => h.slice(6, 8)), axisLabel: { color: txt.value, fontSize: 14 }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#cbd5e1' } } },
    yAxis: { type: 'value', axisLabel: { color: txt.value, fontSize: 14 }, splitLine: { lineStyle: { color: hc.value ? '#bbb' : '#e6ebf2' } } },
    series: [{ name: $t('home.pouredReceived'), type: 'bar', barMaxWidth: 14, data: s('poured').map((v: number, i: number) => v + (H[hs[i]]?.received || 0)) }, { name: $t('common.machining'), type: 'bar', barMaxWidth: 14, data: s('machined') },
      { name: $t('common.goodsReceipt'), type: 'line', smooth: true, symbol: 'none', lineStyle: { width: 3 }, areaStyle: { opacity: 0.1 }, data: s('stored') }, { name: $t('common.scrapped'), type: 'line', symbolSize: 6, data: s('scrap') }] };
});
const pieOpt = computed(() => {
  const p = (ck.value?.pareto || []).slice(0, 6);
  return { color: [css('--hm-amber'), css('--hm-primary'), css('--hm-cyan'), '#6d5ae6', css('--hm-fault'), '#94a3b8'], tooltip: { trigger: 'item' },
    legend: { orient: 'vertical', right: 0, top: 'middle', textStyle: { color: txt.value, fontSize: 15 }, itemWidth: 12, itemHeight: 12 },
    series: [{ type: 'pie', minShowLabelAngle: 14, radius: ['48%', '74%'], center: ['34%', '52%'], label: { show: true, formatter: '{c}', color: txt.value, fontSize: 16, fontWeight: 600 }, labelLine: { length: 8, length2: 6 },
      itemStyle: { borderColor: '#fff', borderWidth: 2 }, data: p.length ? p.map((x: any) => ({ name: x.name, value: x.n })) : [{ name: $t('screen.noDefectsToday'), value: 1, itemStyle: { color: css('--hm-run-soft') }, label: { show: false } }] }],
    graphic: [{ type: 'text', left: '27%', top: '46%', style: { text: String(p.reduce((a: number, x: any) => a + x.n, 0)), fontSize: 30, fontWeight: 700, fill: hc.value ? '#000' : '#0f1b2d' } }] };
});
</script>

<style scoped>
.scr-root { position: fixed; inset: 0; overflow: hidden; background: #eef3f9; color: var(--hm-text); font-family: var(--hm-font);
  background-image: radial-gradient(1200px 700px at 80% -10%, rgba(11, 165, 196, .12), transparent 60%), radial-gradient(900px 600px at -5% 110%, rgba(20, 99, 214, .08), transparent 60%),
    linear-gradient(rgba(30, 64, 120, .05) 1px, transparent 1px), linear-gradient(90deg, rgba(30, 64, 120, .05) 1px, transparent 1px);
  background-size: auto, auto, 32px 32px, 32px 32px; }
/* Background art (public/art/screen-bg.webp, dark line-drawn factory): inverted to fine lines on a light background for the light display, lower half only, faintly overlaid on the grid as texture; hidden in high contrast */
.scr-root::before { content: ''; position: absolute; inset: 0; pointer-events: none; background: url(/art/screen-bg.webp) center bottom / cover no-repeat;
  filter: invert(1) hue-rotate(180deg) saturate(.7); mix-blend-mode: multiply; opacity: .45;
  -webkit-mask-image: linear-gradient(to bottom, transparent 30%, #000 78%); mask-image: linear-gradient(to bottom, transparent 30%, #000 78%); }
.scr-root.hc::before { display: none; }
.scr-root.idle { cursor: none; }
.stage { position: absolute; left: 0; top: 0; width: 1920px; height: 1080px; transform-origin: 0 0; padding: 0 28px 20px; display: flex; flex-direction: column; gap: 14px; }

.top { height: 92px; display: flex; align-items: center; gap: 24px; border-bottom: 1px solid rgba(20, 99, 214, .15); position: relative; flex: none; }
.top::after { content: ''; position: absolute; left: 0; right: 0; bottom: -1px; height: 2px; background: linear-gradient(90deg, transparent, var(--hm-cyan) 20%, var(--hm-primary) 50%, #6d5ae6 80%, transparent); opacity: .7; }
.brand { display: flex; align-items: center; gap: 14px; width: 560px; } .brand img { width: 46px; height: 46px; }
.brand h1 { margin: 0; font-size: 30px; letter-spacing: .08em; font-weight: 700; color: var(--hm-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 490px; }
.brand small { font-size: 15px; color: var(--hm-text-3); letter-spacing: .3em; }
.scenes { flex: 1; display: flex; justify-content: center; gap: 10px; }
.scenes button { position: relative; border: 1px solid rgba(20, 99, 214, .2); background: rgba(255, 255, 255, .7); color: var(--hm-text-2); font: inherit; font-size: 20px; font-weight: 600; letter-spacing: .1em;
  padding: 8px 28px 10px; border-radius: 10px; cursor: pointer; overflow: hidden; }
.scenes button.on { color: #fff; background: linear-gradient(120deg, var(--hm-primary), var(--hm-cyan)); border-color: transparent; box-shadow: 0 6px 18px rgba(20, 99, 214, .3); }
.prog { position: absolute; left: 0; bottom: 0; height: 3px; width: 100%; background: rgba(255, 255, 255, .85); transform-origin: 0 50%; animation: sc-prog linear forwards; }
@keyframes sc-prog { from { transform: scaleX(0); } to { transform: scaleX(1); } }
.clock { width: 560px; text-align: right; } .clock .t { font-size: 46px; font-weight: 600; line-height: 1; color: var(--hm-text); letter-spacing: .04em; }
.clock .d { font-size: 16px; color: var(--hm-text-3); margin-top: 6px; letter-spacing: .08em; }
.live { margin-left: 12px; color: var(--hm-run-ink); font-weight: 600; } .live i { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--hm-run); margin-right: 6px; animation: hm-pulse 1.8s infinite; color: rgba(21, 163, 74, .45); }

.kpis { display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); gap: 14px; flex: none; }
.kp { position: relative; background: rgba(255, 255, 255, .88); border: 1px solid rgba(20, 99, 214, .14); border-radius: 12px; padding: 12px 16px 10px; overflow: hidden; box-shadow: 0 2px 10px rgba(15, 32, 60, .05); }
.kp::before { content: ''; position: absolute; left: 0; top: 0; width: 34px; height: 3px; background: var(--hm-cyan); }
.kp.ok::before { background: var(--hm-run); width: 100%; } .kp.warn::before { background: var(--hm-idle); width: 100%; } .kp.bad::before { background: var(--hm-fault); width: 100%; }
.kp small { font-size: 16px; color: var(--hm-text-2); letter-spacing: .04em; }
.kp b { display: flex; align-items: baseline; gap: 4px; font-size: 44px; font-weight: 600; line-height: 1.15; color: var(--hm-text); }
.kp b em { font-style: normal; font-size: 16px; color: var(--hm-text-3); font-weight: 400; }
.kp.ok b { color: var(--hm-run-ink); } .kp.warn b { color: var(--hm-idle-ink); } .kp.bad b { color: var(--hm-fault-ink); }
.kh { font-size: 14px; color: var(--hm-text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; max-width: 62%; }
.sp { position: absolute; right: 12px; bottom: 10px; width: 92px; height: 26px; } .sp path { fill: none; stroke: var(--hm-cyan); stroke-width: 2; vector-effect: non-scaling-stroke; }

.scene { flex: 1; min-height: 0; display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 14px; }
.col { display: flex; flex-direction: column; gap: 14px; min-height: 0; min-width: 0; }
.panel { position: relative; background: rgba(255, 255, 255, .9); border: 1px solid rgba(20, 99, 214, .14); border-radius: 12px; padding: 12px 16px 14px; display: flex; flex-direction: column; min-height: 0;
  box-shadow: 0 2px 10px rgba(15, 32, 60, .05); }
.panel::before, .panel::after { content: ''; position: absolute; width: 14px; height: 14px; border: 2px solid var(--hm-cyan); pointer-events: none; }
.panel::before { left: -1px; top: -1px; border-right: 0; border-bottom: 0; border-top-left-radius: 12px; }
.panel::after { right: -1px; bottom: -1px; border-left: 0; border-top: 0; border-bottom-right-radius: 12px; }
.panel.grow { flex: 1; }
.panel h3 { margin: 0 0 10px; font-size: 20px; font-weight: 700; letter-spacing: .06em; display: flex; align-items: center; gap: 10px; color: var(--hm-text); flex: none; }
.panel h3 i { width: 5px; height: 20px; border-radius: 3px; background: linear-gradient(var(--hm-cyan), var(--hm-primary)); }
.panel h3 small { margin-left: auto; font-size: 15px; font-weight: 400; color: var(--hm-text-3); letter-spacing: 0; }
.mapbox { flex: 1; min-height: 0; }
.wo-panel { flex: 0 0 330px; }

/* Tables */
.tbl { flex: 1; min-height: 0; display: flex; flex-direction: column; font-size: 17px; }
.th, .tr { display: grid; align-items: center; gap: 10px; padding: 0 10px; }
.th { height: 40px; background: linear-gradient(90deg, rgba(20, 99, 214, .1), rgba(11, 165, 196, .06)); border-radius: 8px; font-size: 15px; color: var(--hm-text-2); font-weight: 600; flex: none; }
.tr { height: 48px; border-bottom: 1px solid #e6ebf2; }
.tb { flex: 1; min-height: 0; }
.tr:nth-child(even) { background: rgba(243, 246, 250, .7); }
.n { text-align: right; } .el { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.t-wo .th, .t-wo .tr { grid-template-columns: 150px 1.4fr 1fr 70px 70px 1.3fr 60px 110px 104px; }
.t-eq .th, .t-eq .tr { grid-template-columns: 1.5fr 90px 1.2fr 1.1fr; }
.t-ncr .th, .t-ncr .tr { grid-template-columns: 150px 1fr 80px 70px; }
.t-mt .th, .t-mt .tr { grid-template-columns: 80px 1fr 70px 110px; }
.t-tl .th, .t-tl .tr { grid-template-columns: 80px 1fr 1fr 90px; }
.pc { display: flex; align-items: center; gap: 8px; } .pc i { flex: 1; height: 10px; background: #e6ebf2; border-radius: 5px; overflow: hidden; }
.pc i b { display: block; height: 100%; background: linear-gradient(90deg, var(--hm-primary), var(--hm-cyan)); border-radius: 5px; }
.pc em { font-style: normal; width: 50px; text-align: right; font-weight: 600; }
.tr.late { color: var(--hm-fault-ink); } .tr.late .pc i b { background: var(--hm-fault); } .tr.risk .pc i b { background: var(--hm-idle); }
.pill { font-style: normal; font-size: 14px; padding: 2px 10px; border-radius: 999px; background: var(--hm-surface-3); color: var(--hm-text-2); white-space: nowrap; }
.pill.run { background: var(--hm-run-soft); color: var(--hm-run-ink); } .pill.warn { background: var(--hm-idle-soft); color: var(--hm-idle-ink); } .pill.bad { background: var(--hm-fault-soft); color: var(--hm-fault-ink); font-weight: 700; }
.stt { display: flex; align-items: center; gap: 8px; font-weight: 600; } .stt i { width: 10px; height: 10px; border-radius: 50%; }
.t-run .stt { color: var(--hm-run-ink); } .t-run .stt i { background: var(--hm-run); } .t-idle .stt, .t-setup .stt { color: var(--hm-idle-ink); } .t-idle .stt i, .t-setup .stt i { background: var(--hm-idle); }
.t-fault { background: var(--hm-fault-soft) !important; } .t-fault .stt { color: var(--hm-fault-ink); } .t-fault .stt i { background: var(--hm-fault); animation: hm-pulse 1s infinite; color: rgba(220, 43, 43, .5); }
.t-offline .stt { color: var(--hm-offline-ink); } .t-offline .stt i { background: var(--hm-offline); }
.al { color: var(--hm-fault-ink); font-size: 15px; }
.none { padding: 20px; text-align: center; color: var(--hm-text-3); } .none.ok { color: var(--hm-run-ink); }

.flow { display: flex; align-items: stretch; gap: 8px; }
.fs { position: relative; flex: 1; background: linear-gradient(180deg, rgba(20, 99, 214, .06), rgba(11, 165, 196, .04)); border: 1px solid rgba(20, 99, 214, .14); border-radius: 10px; padding: 10px 12px; text-align: center; }
.fs small { display: block; font-size: 15px; color: var(--hm-text-2); } .fs b { font-size: 34px; font-weight: 600; color: var(--hm-primary-press); }
.ar { position: absolute; right: -10px; top: 50%; transform: translateY(-50%); font-size: 24px; color: var(--hm-cyan-ink); z-index: 1; font-weight: 700; }
.stc { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.sc-i { border-radius: 10px; padding: 8px 10px; text-align: center; border: 1px solid; } .sc-i b { display: block; font-size: 40px; font-weight: 600; line-height: 1.1; } .sc-i small { font-size: 15px; }
.sc-i.t-run { background: var(--hm-run-soft); border-color: rgba(21, 163, 74, .3); color: var(--hm-run-ink); } .sc-i.t-idle { background: var(--hm-idle-soft); border-color: rgba(217, 154, 22, .35); color: var(--hm-idle-ink); }
.sc-i.t-fault { background: var(--hm-fault-soft); border-color: rgba(220, 43, 43, .35); color: var(--hm-fault-ink); } .sc-i.t-offline { background: var(--hm-offline-soft); border-color: var(--hm-border-strong); color: var(--hm-offline-ink); }
.qg { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.qg div { background: var(--hm-surface-2); border: 1px solid var(--hm-border); border-radius: 10px; padding: 8px 12px; } .qg small { font-size: 15px; color: var(--hm-text-2); display: block; }
.qg b { font-size: 34px; font-weight: 600; } .qg b.bad { color: var(--hm-fault-ink); }

.foot { flex: none; height: 84px; background: rgba(255, 255, 255, .9); border: 1px solid rgba(20, 99, 214, .14); border-radius: 12px; padding: 10px 16px; display: flex; align-items: center; }
.foot > * { flex: 1; min-width: 0; }

.sc-enter-active, .sc-leave-active { transition: opacity .45s var(--hm-ease), transform .45s var(--hm-ease); }
.sc-enter-from { opacity: 0; transform: translateX(24px); } .sc-leave-to { opacity: 0; transform: translateX(-24px); }

/* Control bar */
.ctl { position: fixed; right: 18px; bottom: 18px; display: flex; align-items: center; gap: 6px; padding: 6px 8px; border-radius: 12px; background: rgba(255, 255, 255, .94); box-shadow: var(--hm-e3);
  border: 1px solid var(--hm-border); opacity: 0; transform: translateY(8px); transition: all .25s var(--hm-ease); pointer-events: none; z-index: 10; font-size: 13px; }
.ctl.show { opacity: 1; transform: none; pointer-events: auto; }
.ctl button { display: inline-flex; align-items: center; gap: 4px; height: 30px; padding: 0 10px; border: 1px solid var(--hm-border); background: #fff; border-radius: 8px; font: inherit; cursor: pointer; color: var(--hm-text-2); }
.ctl button:hover { border-color: var(--hm-primary); color: var(--hm-primary); } .ctl button.on { background: var(--hm-text); color: #fff; border-color: var(--hm-text); }
.ctl label { display: inline-flex; align-items: center; gap: 6px; color: var(--hm-text-2); padding: 0 4px; }
.ctl select { height: 30px; border: 1px solid var(--hm-border); border-radius: 8px; font: inherit; padding: 0 6px; background: #fff; }

/* High contrast: pure white background, black text, thick borders, more saturated status colors (bright shop floors / viewing from a distance) */
.hc { background: #fff; background-image: none; --hm-text: #000; --hm-text-2: #111; --hm-text-3: #333; --hm-run-ink: #006b2b; --hm-idle-ink: #7a4500; --hm-fault-ink: #b00000; --hm-primary-press: #003a99; }
.hc .kp, .hc .panel, .hc .foot { background: #fff; border: 2px solid #111; box-shadow: none; }
.hc .panel::before, .hc .panel::after { border-color: #111; }
.hc .th { background: #111; color: #fff; } .hc .tr { border-bottom: 1px solid #999; } .hc .tr:nth-child(even) { background: #f2f2f2; }
.hc .scenes button { border: 2px solid #111; color: #111; background: #fff; } .hc .scenes button.on { background: #111; color: #fff; box-shadow: none; }
.hc .kp b, .hc .fs b, .hc .qg b, .hc .sc-i b { font-weight: 700; } .hc .top::after { background: #111; opacity: 1; height: 3px; }
@media (prefers-reduced-motion: reduce) { .sc-enter-active, .sc-leave-active, .prog { transition: none; animation: none; } }
</style>
