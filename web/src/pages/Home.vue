<template>
  <div ref="root" class="ck" :class="['sz-' + size, { editing }]" data-test="cockpit">
    <!-- Page header (data clock and alarm count are already in the top bar, not repeated here) -->
    <header class="ck-head">
      <div class="tt">
        <h2>{{ $t('common.home') }}</h2>
        <span class="sub">{{ company }}<template v-if="ck?.day"> · {{ ck.day }}</template>
          <span class="live" :class="{ stale: !!live.error.value }" data-test="live-badge"><i :class="{ 'hm-pulse': !live.error.value }" />{{ live.error.value ? $t('home.dataInterrupted') : $t('pinnedBlocks.live') }}</span>
        </span>
      </div>
      <span class="grow" />
      <el-button v-if="aiOn('agents')" @click="router.push('/agents')"><el-icon><Cpu /></el-icon>{{ $t('home.agents') }}</el-button>
      <el-button :type="editing ? 'primary' : 'default'" data-test="layout-edit" @click="editing = !editing"><el-icon><Grid /></el-icon>&nbsp;{{ editing ? $t('common.done') : $t('home.layout') }}</el-button>
      <el-button data-test="open-screen" @click="router.push('/screen')"><el-icon><Monitor /></el-icon>{{ $t('home.shopFloorDisplay') }}</el-button>
    </header>

    <!-- AI status bar: one-line situation + one-click question; with AI off, only the rule-based summary -->
    <section v-if="!stripHidden" class="ai-strip" :class="{ off: !aiOn('ask') }" data-test="ai-strip" v-ai="briefAi">
      <div class="orb" aria-hidden="true"><span /></div>
      <div class="brief">
        <div class="bt"><b>{{ aiOn('ask') ? $t('home.aiSituation') : $t('home.situationSummary') }}</b><span class="tag" :class="{ on: aiOn('watch') }">{{ aiOn('watch') ? $t('feedList.aiWatchActive') : aiOn() ? $t('home.aiWatchIsOff') : $t('home.aiOffRuleBasedWatch') }}</span></div>
        <div class="bx" data-test="ai-brief">{{ brief }}</div>
      </div>
      <div class="qs" role="group" :aria-label="$t('home.quickQuestions')">
        <button v-for="q in QUICK" :key="q.id" class="q" :class="{ dis: !aiOn('ask') }" :title="aiOn('ask') ? q.prompt : aiOffText('ask')" :data-q="q.id" @click.stop="quick(q)">
          <span class="spk" />{{ q.label }}</button>
        <button v-if="aiOn('daily')" class="q" data-q="daily" @click.stop="router.push('/ai/daily')"><span class="spk" />{{ $t('home.aiDailyReport') }}</button>
      </div>
      <button class="x" :title="$t('home.collapseSituationBar')" :aria-label="$t('home.collapseSituationBar')" @click.stop="setStrip(true)"><el-icon><ArrowUp /></el-icon></button>
    </section>
    <button v-else class="strip-pill" data-test="ai-strip-show" @click="setStrip(false)"><span class="spk" />{{ $t('home.expandAiSituation') }}</button>

    <!-- Edit layout -->
    <div v-if="editing" class="edit-bar" data-test="layout-bar">
      <el-icon><Rank /></el-icon><span>{{ $t('home.dragCardsOrUseTo') }}</span>
      <span class="grow" />
      <template v-if="hidden.length"><span class="hl">{{ $t('home.hidden') }}</span>
        <button v-for="id in hidden" :key="id" class="chip" :data-show="id" @click="show(id)"><el-icon><Plus /></el-icon>{{ W[id]?.title }}</button></template>
      <el-button size="small" data-test="layout-reset" @click="resetLayout">{{ $t('common.resetToDefault') }}</el-button>
      <el-button size="small" type="primary" @click="editing = false">{{ $t('common.done') }}</el-button>
    </div>

    <div class="wg-grid">
      <section v-for="(id, i) in visible" :key="id" class="wg" :class="['w-' + id, { drag: dragId === id, over: overId === id, before: overId === id && overBefore, after: overId === id && !overBefore }]"
        :style="{ gridColumn: 'span ' + spanOf(id) }" :data-widget="id" :draggable="editing" @dragstart="dragStart($event, id)" @dragover="dragOver($event, id)"
        @dragleave="overId === id && (overId = '')" @drop="drop($event, id)" @dragend="dragEnd">
        <div v-if="editing" class="wg-tools">
          <span class="hd" :title="$t('home.drag')"><el-icon><Rank /></el-icon></span><b>{{ W[id].title }}</b><span class="grow" />
          <button :disabled="i === 0" :title="$t('home.moveEarlier')" :data-move-up="id" @click="move(id, -1)"><el-icon><ArrowLeft /></el-icon></button>
          <button :disabled="i === visible.length - 1" :title="$t('home.moveLater')" :data-move-down="id" @click="move(id, 1)"><el-icon><ArrowRight /></el-icon></button>
          <button class="hide" :data-hide="id" @click="hide(id)"><el-icon><Hide /></el-icon>{{ $t('home.hide') }}</button>
        </div>

        <!-- Key metrics -->
        <MomDomains v-if="id === 'mom'" :ck="ck" />
        <div v-else-if="id === 'kpi'" class="kpis" :class="{ k6: width >= 940, compact: width < 1250 }">
          <HmKpi :label="$t('home.pouredReceivedToday')" :value="K?.castings.value" :unit="$t('common.pcs')" format="int" :loading="!K" :spark="K?.castings.spark" :delta="dlt(K?.castings)" :delta-label="$t('home.vsSameTimeYesterday')"
            :hint="hintY(K?.castings)" :ai="{ data: { [$t('common.date')]: ck?.day, [$t('home.sameTimeYesterday')]: K?.castings.yday, [$t('home.last12H')]: K?.castings.spark } }" />
          <HmKpi :label="$t('home.machinedToday')" :value="K?.machined.value" :unit="$t('common.pcs')" format="int" :loading="!K" :spark="K?.machined.spark" :delta="dlt(K?.machined)" :delta-label="$t('home.vsSameTimeYesterday')"
            :hint="hintY(K?.machined)" :ai="{ data: { [$t('common.date')]: ck?.day, [$t('home.sameTimeYesterday')]: K?.machined.yday, [$t('home.last12H')]: K?.machined.spark } }" />
          <HmKpi :label="$t('home.storedToday')" :value="K?.stored.value" :unit="$t('common.pcs')" format="int" :loading="!K" :spark="K?.stored.spark" :status="attainStatus" :delta="dlt(K?.stored)" :delta-label="$t('home.vsSameTimeYesterday')"
            :hint="K?.stored.plan_state === 'ok' ? $t('home.attainment0Plan1', [attainText, K.stored.plan_now]) : $t('home.shipped0', [K?.stored.shipped ?? '—'])"
            :ai="{ data: { [$t('common.date')]: ck?.day, [$t('home.dueByNow')]: K?.stored.plan_now, [$t('home.fullDayPlan')]: K?.stored.plan_day, [$t('home.planAttainment')]: K?.stored.attain, [$t('common.shipped2')]: K?.stored.shipped, [$t('home.sameTimeYesterday')]: K?.stored.yday } }" />
          <HmKpi :label="$t('common.scrapRate')" :value="K?.scrap_rate.value" format="pct" :digits="2" :loading="!K" :spark="K?.scrap_rate.spark" good-when="down"
            :status="K && K.scrap_rate.value > 0.03 ? 'bad' : K && K.scrap_rate.value > 0.02 ? 'warn' : ''" :delta="dltR(K?.scrap_rate)" delta-pct :delta-label="$t('home.vsSameTimeYesterday')"
            :hint="$t('home.scrap0Rework1', [K?.scrap_rate.scrap ?? '—', K?.scrap_rate.rework ?? '—'])" to="/quality"
            :ai="{ data: { [$t('common.date')]: ck?.day, [$t('common.scrapped')]: K?.scrap_rate.scrap, [$t('common.rework')]: K?.scrap_rate.rework, [$t('home.topDefects')]: (ck?.pareto || []).slice(0, 3), [$t('home.sameTimeYesterday')]: K?.scrap_rate.yday } }" />
          <HmKpi :label="$t('common.avgOee')" :value="K?.oee.value" format="pct" :loading="!K" :spark="(K?.oee.spark || []).filter((x: any) => x !== null)" :delta="dltR(K?.oee)" delta-pct :delta-label="$t('home.vsYesterday')"
            :status="K?.oee.value !== null && K?.oee.value !== undefined && K.oee.value < 0.6 ? 'bad' : ''" to="/equipment/oee"
            :hint="K?.oee.worst?.[0] ? $t('home.lowest01', [K.oee.worst[0].code, fmtPct(K.oee.worst[0].OEE, 0)]) : $t('home.n0Alarms', [K?.oee.alarms ?? '—'])"
            :ai="{ data: { [$t('common.date')]: ck?.day, [$t('checkTab.byEquipment')]: (ck?.oee || []).map((x: any) => ({ [$t('common.equipment')]: x.code, OEE: x.OEE, [$t('home.availability')]: x.A, [$t('home.performance')]: x.P, [$t('home.quality')]: x.Q })), [$t('home.hourlyLast8H')]: K?.oee.spark } }" />
          <HmKpi :label="$t('home.powerToday')" :value="K?.kwh.value" unit="kWh" format="int" :loading="!K" :spark="K?.kwh.spark" good-when="down" :delta="dlt(K?.kwh)" :delta-label="$t('home.vsSameTimeYesterday')"
            :hint="$t('home.n0KwhT1Heats', [K?.kwh.per_t ?? '—', K?.kwh.heats ?? 0])" to="/energy" :ai="{ data: { [$t('common.date')]: ck?.day, [$t('heats.kwhT')]: K?.kwh.per_t, [$t('home.heats')]: K?.kwh.heats, [$t('home.last12H')]: K?.kwh.spark } }" />
          <template v-if="size === 'xl'">
            <HmKpi :label="$t('home.planAttainment')" :value="K?.stored.attain" format="pct" :loading="!K" :status="attainStatus" data-test="kpi-attain"
              :hint="K?.stored.plan_state === 'ok' ? $t('home.due0Actual12', [K.stored.plan_now, K.stored.value, K.stored.attain_capped ? $t('home.above150') : '']) : (K?.stored.plan_state === 'tiny' ? $t('home.plan0Actual1Plan', [K.stored.plan_now, K.stored.value]) : $t('home.noValidPlan'))"
              :title="K?.stored.plan_note || ''" to="/production/gantt"
              :ai="{ data: { [$t('home.dueByNow')]: K?.stored.plan_now, [$t('home.actualStored')]: K?.stored.value, [$t('home.fullDayPlan')]: K?.stored.plan_day, [$t('common.description')]: K?.stored.plan_note } }" />
            <HmKpi :label="$t('home.runningRate')" :value="runRate" format="pct" :loading="!K" :hint="$t('home.running01Machines', [ck?.status?.running || 0, devTotal])" to="/equipment/monitor" :ai="{ data: { [$t('home.machinesByStatus')]: ck?.status } }" />
          </template>
        </div>

        <!-- Live plant map -->
        <HmCard v-else-if="id === 'map'" class="fill" :sub="lay.data.value?.plant ? $t('home.n01MachinesRunningRate', [lay.data.value.plant, liveDev, fmtPct(runRate, 0)]) : ''">
          <template #title><h3 class="ttl"><span class="dot" />{{ $t('home.livePlantMap') }}</h3></template>
          <template #actions><el-button link type="primary" @click="router.push('/equipment/monitor')">{{ $t('home.liveMonitor') }}</el-button></template>
          <FactoryMap :layout="lay.data.value" :live="live.data.value?.items || []" :now="now" :loading="lay.loading.value || (!lay.data.value && !lay.error.value)" @retry="retryLayout" />
        </HmCard>

        <!-- Alerts & AI Watch -->
        <HmCard v-else-if="id === 'feed'" class="fill feedc" :sub="feedSub">
          <template #title><h3 class="ttl">{{ $t('home.alertsAiWatch') }}</h3></template>
          <template #actions><el-button link type="primary" @click="router.push('/e/alarm')">{{ $t('home.alarmRecords') }}</el-button></template>
          <div class="feed-box"><FeedList :items="feed.data.value?.items || []" :counts="feed.data.value?.counts" :watch="!!feed.data.value?.watch" :loading="feed.loading.value" :now="now" @refresh="feed.refresh" /></div>
        </HmCard>

        <!-- Andon -->
        <div v-else-if="id === 'andon'" class="hm-card andon-card">
          <AndonStrip :open="ck?.andon?.open || []" :closed-today="ck?.andon?.closed_today || 0" :now="now" />
        </div>

        <!-- Pinned AI results (shown only if something is pinned and AI is on) -->
        <HmCard v-else-if="id === 'pins'" :title="$t('home.pinnedAiResults')" :sub="$t('home.n0ClickPinToHome', [pinN])" data-test="home-pins">
          <PinnedBlocks hide-empty :cols="size === 'xl' ? 3 : size === 'lg' ? 2 : 1" />
        </HmCard>

        <!-- Work orders -->
        <HmCard v-else-if="id === 'orders'" :title="$t('home.todaySWorkOrderProgress')" :sub="$t('home.planVsActualOneLine')">
          <template #actions><el-button link type="primary" @click="router.push('/production/gantt')">{{ $t('home.scheduling') }}</el-button></template>
          <OrderTimeline :orders="ck?.orders || []" :now="now" />
        </HmCard>

        <!-- Quality -->
        <HmCard v-else-if="id === 'quality'" :title="$t('home.todaySQuality')" :sub="$t('home.n0PcsInspected', [K?.inspected ?? '—'])">
          <template #actions><el-button link type="primary" @click="router.push('/quality')">{{ $t('home.qualityOverview') }}</el-button></template>
          <div class="q-top">
            <div class="qn" v-ai="{ kind: 'kpi', label: $t('home.scrapRate0', [fmtPct(K?.scrap_rate.value, 2)]), data: { [$t('common.scrapped')]: K?.scrap_rate.scrap, [$t('common.rework')]: K?.scrap_rate.rework } }">
              <small>{{ $t('common.scrapRate') }}</small><b class="hm-num" :class="{ bad: (K?.scrap_rate.value || 0) > 0.03 }">{{ fmtPct(K?.scrap_rate.value, 2) }}</b></div>
            <div class="qn" v-ai="{ kind: 'kpi', label: $t('home.n0PcsScrappedToday', [K?.scrap_rate.scrap ?? 0]) }"><small>{{ $t('common.scrapped') }}</small><b class="hm-num">{{ K?.scrap_rate.scrap ?? '—' }}</b></div>
            <div class="qn" v-ai="{ kind: 'kpi', label: $t('home.n0PcsReworkedToday', [K?.scrap_rate.rework ?? 0]) }"><small>{{ $t('common.rework') }}</small><b class="hm-num">{{ K?.scrap_rate.rework ?? '—' }}</b></div>
            <div class="qn click" v-ai="{ kind: 'kpi', label: $t('home.n0OpenNonconformances', [ck?.ncr_open ?? 0]), link: '/e/ncr?view=pending' }" @click="router.push('/e/ncr?view=pending')">
              <small>{{ $t('home.openNcrs') }}</small><b class="hm-num" :class="{ warn: (ck?.ncr_open || 0) > 0 }">{{ ck?.ncr_open ?? '—' }}</b></div>
          </div>
          <div class="sec">{{ $t('home.top5DefectsToday') }}</div>
          <div v-for="p in pareto" :key="p.name" class="bar-row" v-ai="{ kind: 'chart-point', label: $t('home.defect01Pcs', [p.name, p.n]), data: { [$t('common.defect')]: p.name, [$t('common.parts')]: p.n, [$t('chart.share')]: p.share } }">
            <span class="bn">{{ p.name }}</span><span class="bb"><i :style="{ width: p.w + '%' }" /></span><b class="hm-num">{{ p.n }}</b>
          </div>
          <div v-if="!pareto.length" class="ok-note"><el-icon><CircleCheckFilled /></el-icon>{{ $t('home.noDefectsRecordedToday') }}</div>
        </HmCard>

        <!-- Output -->
        <HmCard v-else-if="id === 'output'" :title="$t('home.outputLast24H')" :sub="$t('home.hourlyPcs')">
          <div v-ai="outAi"><Chart v-if="ck" :option="hourOpt" :height="size === 'xl' ? 300 : 250" /><HmSkeleton v-else variant="block" :height="250" /></div>
        </HmCard>

        <!-- OEE -->
        <HmCard v-else-if="id === 'oee'" :title="$t('home.machineOee')" :sub="$t('home.todayLowestFirst')">
          <template #actions><el-button link type="primary" @click="router.push('/equipment/oee')">{{ $t('home.oee') }}</el-button></template>
          <div v-for="o in oeeRows" :key="o.code" class="bar-row oee" :class="o.cls"
            v-ai="{ kind: 'kpi', label: `${o.code} OEE ${fmtPct(o.OEE, 1)}`, data: { [$t('common.equipment')]: `${o.code} ${o.name}`, OEE: o.OEE, [$t('home.availability')]: o.A, [$t('home.performance')]: o.P, [$t('home.quality')]: o.Q, [$t('home.lostHours')]: o.loss } }">
            <span class="bn"><b class="hm-mono">{{ o.code }}</b></span><span class="bb"><i :style="{ width: (o.OEE || 0) * 100 + '%' }" /><em v-for="m in [0.6, 0.85]" :key="m" :style="{ left: m * 100 + '%' }" /></span><b class="hm-num">{{ fmtPct(o.OEE, 0) }}</b>
          </div>
          <HmEmpty v-if="ck && !oeeRows.length" compact :title="$t('home.noOeeDataYet')" />
        </HmCard>
        <!-- Reports pinned to Home from the Report Designer (M3): report:<id> in the layout -->
        <HmCard v-else-if="id.startsWith('report:')" :title="W[id]?.title" :sub="$t('home.reportDesignerPinnedToHome')" :data-test="'home-report-' + id.slice(7)">
          <ReportWidget :rid="+id.slice(7)" />
        </HmCard>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// Home = production cockpit: AI status bar, key metrics (animated numbers + trend + vs yesterday), live plant map (virtual factory layout), alerts & AI Watch feed, Andon strip, work order mini-Gantt, quality, output, OEE.
// Widgets can be reordered / hidden by drag (saved per user on the server at /api/home/prefs, cached locally too); "Restore Defaults". Every data element has v-ai.
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { aiOffText, aiOn, askAi, registerAiActions } from '../ai';
import { call, session } from '../api';
import PinnedBlocks from '../ai/PinnedBlocks.vue';
import Chart from '../components/Chart.vue';
import AndonStrip from '../components/home/AndonStrip.vue';
import FactoryMap from '../components/home/FactoryMap.vue';
import FeedList from '../components/home/FeedList.vue';
import OrderTimeline from '../components/home/OrderTimeline.vue';
import MomDomains from '../components/home/MomDomains.vue';
import ReportWidget from '../impl/ReportWidget.vue';           // M3: reports pinned to Home from the Report Designer
import { usePoll } from '../components/home/usePoll';
import { fmtNum, fmtPct } from '../design/format';

const router = useRouter();
const cockpit = usePoll<any>('/api/home/cockpit', 10000);
const live = usePoll<any>('/api/home/live', 3000);
const feed = usePoll<any>('/api/home/feed', 10000);
const lay = usePoll<any>('/api/home/layout', 120000);
const ck = computed(() => cockpit.data.value);
const K = computed(() => ck.value?.kpi);
const now = computed(() => live.data.value?.now || ck.value?.now || '');
const company = computed(() => ck.value?.company || session.user?.company || $t('home.honeymom'));
function retryLayout() { lay.data.value = null; call('GET', '/api/home/layout?refresh=1', undefined, true).then((d) => { lay.data.value = d; }).catch(() => {}); }

// ---------------- Metrics ----------------
const dlt = (x: any) => (x && x.yday ? x.value - x.yday : null);                         // No delta shown if yesterday has no data
const dltR = (x: any) => (x && x.yday !== null && x.yday !== undefined && x.value !== null && x.value !== undefined ? x.value - x.yday : null);
const hintY = (x: any) => (x?.yday ? $t('home.sameTimeYesterday0', [fmtNum(x.yday)]) : $t('home.n12HourTrend'));
const attainStatus = computed(() => { const a = K.value?.stored.attain; return a === null || a === undefined ? '' : a >= 0.95 ? 'ok' : a >= 0.8 ? 'warn' : 'bad'; });
// Plan attainment shows at most 150% (beyond that shows ">150%"); no percentage without a valid plan (backend home.attainment plan_state / plan_note)
const attainText = computed(() => { const s = K.value?.stored; return !s || s.attain === null || s.attain === undefined ? '—' : (s.attain_capped ? $t('home.k3419') : '') + fmtPct(s.attain, 0); });
const devTotal = computed(() => Object.values(ck.value?.status || {}).reduce((a: number, b: any) => a + Number(b || 0), 0));
const runRate = computed(() => (devTotal.value ? (ck.value?.status?.running || 0) / devTotal.value : null));
const liveDev = computed(() => (live.data.value?.items || []).filter((x: any) => !['crew', 'agv'].includes(x.etype)).length);

// ---------------- AI status bar ----------------
const STRIP_K = 'hm_home_strip_hidden';
const stripHidden = ref(false);
try { stripHidden.value = localStorage.getItem(STRIP_K) === '1'; } catch { /* */ }
function setStrip(v: boolean) { stripHidden.value = v; try { localStorage.setItem(STRIP_K, v ? '1' : '0'); } catch { /* */ } }
const brief = computed(() => {
  const k = K.value; if (!k) return $t('home.aggregatingShopFloorData');
  const c = feed.data.value?.counts || {};
  const parts = [$t('home.asOf01Pcs', [(now.value || '').slice(11, 16), k.stored.value]) + (k.stored.attain !== null && k.stored.attain !== undefined ? $t('home.planAttainment0', [attainText.value]) : ''),
    $t('home.avgOee0', [fmtPct(k.oee.value, 1)]), $t('home.scrapRate0', [fmtPct(k.scrap_rate.value, 2)])];
  const top = (feed.data.value?.items || []).find((x: any) => x.severity === 'critical');
  const tail = c.critical ? $t('home.n0Urgent1MostUrgent', [c.critical, c.warning ? $t('home.n0NeedAttention', [c.warning]) : '', top?.title || '']) : c.warning ? $t('home.n0NeedAttentionNothingUrgent', [c.warning]) : $t('home.nothingNeedsImmediateAction');
  return parts.join($t('common.k362')) + tail + $t('home.k3429');
});
const briefAi = computed(() => ({ kind: 'text', key: 'home:brief', label: $t('home.todaySSituation'), data: { [$t('common.summary')]: brief.value, [$t('common.dataClock')]: now.value } }));
const QUICK = [
  { id: 'today', label: $t('home.howIsTodayGoing'), prompt: $t('home.howAreProductionQualityAnd') },
  { id: 'bottleneck', label: $t('home.whereIsTheBottleneck'), prompt: $t('home.whichOperationMachineIsThe') },
  { id: 'due', label: $t('home.deliveryRisk'), prompt: $t('home.areAnyWipWorkOrders') },
  { id: 'quality', label: $t('home.qualityIssues'), prompt: $t('home.areThereAnyQualityAnomalies') },
];
function quick(q: any) {
  const ctx = { kind: 'kpi', key: 'home:snapshot', label: $t('home.homeSnapshot0', [(now.value || '').slice(5, 16)]),
    data: { [$t('common.summary')]: brief.value, [$t('common.goodsReceipt')]: K.value?.stored.value, [$t('home.planAttainment')]: K.value?.stored.attain, [$t('home.avgOee')]: K.value?.oee.value, [$t('common.scrapRate')]: K.value?.scrap_rate.value, [$t('home.powerTodayKwh')]: K.value?.kwh.value,
      [$t('home.urgentAlerts')]: (feed.data.value?.items || []).filter((x: any) => x.severity === 'critical').map((x: any) => x.title).slice(0, 5) } };
  askAi(q.prompt, ctx, { action: 'home-' + q.id });
}

// ---------------- Charts / lists ----------------
const pareto = computed(() => { const p = (ck.value?.pareto || []).slice(0, 5); const mx = Math.max(1, ...p.map((x: any) => x.n)); const tot = p.reduce((a: number, x: any) => a + x.n, 0) || 1;
  return p.map((x: any) => ({ ...x, w: (x.n / mx) * 100, share: fmtPct(x.n / tot, 0) })); });
const oeeRows = computed(() => [...(ck.value?.oee || [])].filter((x: any) => x.OEE !== null).sort((a: any, b: any) => a.OEE - b.OEE).slice(0, size.value === 'xl' ? 12 : 8)
  .map((o: any) => ({ ...o, cls: o.OEE < 0.6 ? 'bad' : o.OEE < 0.85 ? 'warn' : 'good' })));
const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim() || undefined;
const hourOpt = computed(() => {
  const hs = ck.value.hours, H = ck.value.hourly;
  const s = (k: string) => hs.map((h: string) => H[h]?.[k] || 0);
  const ax = { axisLine: { lineStyle: { color: '#cbd5e1' } }, axisLabel: { color: '#7b8aa0', fontSize: 11 }, axisTick: { show: false } };
  return { color: [css('--hm-primary'), css('--hm-cyan'), css('--hm-run'), css('--hm-fault')], tooltip: { trigger: 'axis' }, legend: { top: 0, right: 0, itemWidth: 12, itemHeight: 8, textStyle: { color: '#44536a', fontSize: 12 } },
    grid: { left: 36, right: 8, top: 30, bottom: 22 }, xAxis: { type: 'category', data: hs.map((h: string) => h.slice(6, 8)), ...ax },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#eef2f7' } }, axisLabel: { color: '#7b8aa0', fontSize: 11 } },
    series: [{ name: $t('home.pouredReceived'), type: 'bar', barMaxWidth: 12, itemStyle: { borderRadius: [2, 2, 0, 0] }, data: s('poured').map((v: number, i: number) => v + (H[hs[i]]?.received || 0)) },
      { name: $t('common.machining'), type: 'bar', barMaxWidth: 12, itemStyle: { borderRadius: [2, 2, 0, 0] }, data: s('machined') },
      { name: $t('common.goodsReceipt'), type: 'line', smooth: true, symbol: 'none', lineStyle: { width: 2 }, areaStyle: { opacity: 0.08 }, data: s('stored') },
      { name: $t('common.scrapped'), type: 'line', symbol: 'circle', symbolSize: 4, lineStyle: { width: 1.5 }, data: s('scrap') }] };
});
const outAi = computed(() => {
  const hs = ck.value?.hours || [], H = ck.value?.hourly || {};
  const last = hs.slice(-6).map((h: string) => $t('home.n0Poured1Machined2', [h.slice(6, 11), (H[h]?.poured || 0) + (H[h]?.received || 0), H[h]?.machined || 0, H[h]?.stored || 0, H[h]?.scrap || 0]));
  return { kind: 'chart', key: 'home:output24', label: $t('home.outputLast24HHourly'), data: { [$t('home.last6Hours')]: last.join($t('common.k764')), [$t('home.storedToday')]: K.value?.stored.value } };
});
const feedSub = computed(() => { const c = feed.data.value?.counts; return c ? $t('home.urgent0Warning1Info', [c.critical, c.warning, c.info]) + (c.ai ? ` · AI ${c.ai}` : '') : ''; });

// ---------------- Layout: order / hidden / size ----------------
const W: Record<string, { title: string; span: Record<string, number> }> = {
  mom: { title: $t('mom.domains'), span: { sm: 12, md: 12, lg: 12, xl: 12 } },   // ISA-95 operations domains: production / quality / maintenance / inventory
  kpi: { title: $t('home.keyMetrics'), span: { sm: 12, md: 12, lg: 12, xl: 12 } },
  map: { title: $t('home.livePlantMap'), span: { sm: 12, md: 12, lg: 8, xl: 8 } },
  feed: { title: $t('home.alertsAiWatch'), span: { sm: 12, md: 5, lg: 4, xl: 4 } },
  andon: { title: $t('common.andon'), span: { sm: 12, md: 12, lg: 12, xl: 12 } },
  pins: { title: $t('home.pinnedAiResults'), span: { sm: 12, md: 12, lg: 12, xl: 12 } },
  orders: { title: $t('home.todaySWorkOrderProgress'), span: { sm: 12, md: 7, lg: 8, xl: 6 } },
  quality: { title: $t('home.todaySQuality'), span: { sm: 12, md: 6, lg: 4, xl: 3 } },
  output: { title: $t('home.outputLast24H'), span: { sm: 12, md: 12, lg: 8, xl: 12 } },
  oee: { title: $t('home.machineOee'), span: { sm: 12, md: 6, lg: 4, xl: 3 } },
};
const DEFAULT = ['mom', 'kpi', 'map', 'feed', 'andon', 'pins', 'orders', 'quality', 'output', 'oee'];
const order = ref<string[]>([...DEFAULT]), hidden = ref<string[]>([]);
// Pinned AI results: available only if there are pinned blocks and the AI master switch is on (order / hidden still stored in the layout)
const pinN = ref(0);
async function loadPins() { try { pinN.value = ((await call('GET', '/api/ai/pins', undefined, true)) || []).length; } catch { pinN.value = 0; } }
const available = (id: string) => id !== 'pins' || (aiOn() && pinN.value > 0);
const visible = computed(() => order.value.filter((id) => W[id] && !hidden.value.includes(id) && available(id)));
const LK = () => 'hm_home_layout:' + (session.user?.username || '');
function apply(p: any) {
  if (!p || !Array.isArray(p.order)) return;
  const known = p.order.filter((x: string) => W[x]);
  for (const id of DEFAULT) if (!known.includes(id)) known.splice(Math.min(DEFAULT.indexOf(id), known.length), 0, id);   // New widgets go back to their default position
  order.value = known; hidden.value = (p.hidden || []).filter((x: string) => W[x]);
}
try { apply(JSON.parse(localStorage.getItem(LK()) || 'null')); } catch { /* */ }
let saveT: any;
function save() {
  const p = { order: order.value, hidden: hidden.value };
  try { localStorage.setItem(LK(), JSON.stringify(p)); } catch { /* */ }
  clearTimeout(saveT); saveT = setTimeout(() => call('PUT', '/api/home/prefs', p, true).catch(() => {}), 250);
}
function move(id: string, d: number) {
  const a = [...order.value], vis = visible.value, j = vis.indexOf(id) + d;
  if (j < 0 || j >= vis.length) return;
  a.splice(a.indexOf(id), 1); a.splice(a.indexOf(vis[j]) + (d > 0 ? 1 : 0), 0, id); order.value = a; save();
}
function hide(id: string) { if (!hidden.value.includes(id)) hidden.value = [...hidden.value, id]; save(); }
function show(id: string) { hidden.value = hidden.value.filter((x) => x !== id); save(); }
async function resetLayout() { order.value = [...DEFAULT]; hidden.value = []; try { localStorage.removeItem(LK()); } catch { /* */ } await call('DELETE', '/api/home/prefs', undefined, true).catch(() => {}); }
const editing = ref(false);
const dragId = ref(''), overId = ref(''), overBefore = ref(true);
function dragStart(e: DragEvent, id: string) { if (!editing.value) return; dragId.value = id; e.dataTransfer?.setData('text/plain', id); if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'; }
function dragOver(e: DragEvent, id: string) {
  if (!editing.value || !dragId.value || id === dragId.value) return;
  e.preventDefault();
  const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
  overId.value = id; overBefore.value = spanOf(id) >= 12 ? e.clientY < r.top + r.height / 2 : e.clientX < r.left + r.width / 2;
}
function drop(e: DragEvent, id: string) {
  e.preventDefault();
  const src = dragId.value || e.dataTransfer?.getData('text/plain') || '';
  if (src && src !== id && W[src]) { const a = order.value.filter((x) => x !== src); a.splice(a.indexOf(id) + (overBefore.value ? 0 : 1), 0, src); order.value = a; save(); }
  dragEnd();
}
function dragEnd() { dragId.value = ''; overId.value = ''; }

const root = ref<HTMLElement>();
const width = ref(1200);
// sm phone / md 1280 screen (map full width) / lg 1680 screen / xl 2560 screen (larger font, two extra metrics)
const size = computed(() => (width.value < 700 ? 'sm' : width.value < 1300 ? 'md' : width.value < 1880 ? 'lg' : 'xl'));
const spanOf = (id: string) => W[id].span[size.value] || 12;
let ro: ResizeObserver | null = null;

// Page actions for equipment (context menu / toolbar)
const offActs = registerAiActions('device', [
  { id: 'diagnose', label: $t('home.aiDiagnoseThisMachine'), icon: 'MagicStick', ai: true, order: 25, toolbar: true, when: (it) => it.entity === 'equipment',
    run: (it, sel) => askAi($t('home.diagnose0RecentStatusCauses', [it.label]), sel, { action: 'diagnose' }) },
  { id: 'oee-tl', label: $t('home.oeeDowntimeTimeline'), icon: 'TrendCharts', order: 110, when: (it) => it.entity === 'equipment' && !!it.id, run: (it) => router.push(`/equipment/oee?eid=${it.id}`) },
  { id: 'repair', label: $t('common.requestRepair'), icon: 'SetUp', order: 120, when: (it) => it.entity === 'equipment' && !!it.id, run: (it) => router.push(`/e/repair_order?equipment=${it.id}`) },
]);
// Reports pinned via Report Designer "Pin to Home": report:<id> in the /api/home/prefs layout; register as widgets first, then apply the layout (M3)
async function loadReportWidgets() {
  try { for (const w of (await call('GET', '/api/impl/reports/widgets', undefined, true)) || []) W['report:' + w.id] = { title: w.name, span: { sm: 12, md: 6, lg: 6, xl: 4 } }; } catch { /* */ }
}
onMounted(async () => {
  ro = new ResizeObserver(() => { if (root.value) width.value = root.value.clientWidth; });
  if (root.value) { width.value = root.value.clientWidth; ro.observe(root.value); }
  loadPins(); window.addEventListener('ai-pins-changed', loadPins);
  await loadReportWidgets();
  const p = await call('GET', '/api/home/prefs', undefined, true).catch(() => null);
  if (p && Array.isArray(p.order)) { apply(p); try { localStorage.setItem(LK(), JSON.stringify({ order: order.value, hidden: hidden.value })); } catch { /* */ } }
});
onUnmounted(() => { ro?.disconnect(); offActs(); clearTimeout(saveT); window.removeEventListener('ai-pins-changed', loadPins); });
</script>

<style scoped>
.ck { padding: var(--hm-sp-3) var(--hm-sp-5) var(--hm-sp-6); display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.ck-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.tt { display: flex; align-items: baseline; gap: 12px; min-width: 0; }
.tt h2 { margin: 0; font-size: var(--hm-fs-xl); font-weight: 600; letter-spacing: .02em; }
.sub { color: var(--hm-text-3); font-size: var(--hm-fs-xs); display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; }
.live { display: inline-flex; align-items: center; gap: 5px; margin-left: 8px; color: var(--hm-run-ink); background: var(--hm-run-soft); border-radius: var(--hm-r-pill); padding: 0 8px 0 6px; font-weight: 600; font-size: 11px; line-height: 18px; }
.live i { width: 6px; height: 6px; border-radius: 50%; background: var(--hm-run); color: rgba(21, 163, 74, .45); }
.live.stale { color: var(--hm-fault-ink); background: var(--hm-fault-soft); } .live.stale i { background: var(--hm-fault); }

/* AI status bar */
.ai-strip { position: relative; display: flex; align-items: center; gap: 14px; padding: 10px 44px 10px 14px; border-radius: var(--hm-r-lg); border: 1px solid transparent;
  background: linear-gradient(100deg, rgba(238, 243, 255, .97), rgba(255, 255, 255, .97) 45%, rgba(227, 246, 250, .92)) padding-box, var(--hm-ai-grad) border-box; box-shadow: var(--hm-e1); overflow: hidden; }
.ai-strip::after { content: ''; position: absolute; top: 0; bottom: 0; left: 0; width: 40%; background: linear-gradient(100deg, transparent, rgba(255, 255, 255, .6) 50%, transparent);
  transform: translateX(-120%); will-change: transform; animation: ck-sheen 8s ease-in-out infinite; pointer-events: none; }
@keyframes ck-sheen { 0%, 70% { transform: translateX(-120%); } 100% { transform: translateX(320%); } }
.ai-strip.off { background: var(--hm-surface); border-color: var(--hm-border); } .ai-strip.off::after { display: none; }
.orb { width: 34px; height: 34px; border-radius: 50%; flex: none; display: grid; place-items: center; background: var(--hm-ai-grad); box-shadow: 0 0 0 4px rgba(20, 99, 214, .1), 0 4px 14px rgba(20, 99, 214, .35); }
.orb span { width: 16px; height: 16px; will-change: transform; background: #fff; clip-path: polygon(50% 0, 62% 38%, 100% 50%, 62% 62%, 50% 100%, 38% 62%, 0 50%, 38% 38%); animation: ck-spin 9s linear infinite; }
@keyframes ck-spin { to { transform: rotate(360deg); } }
.off .orb { background: var(--hm-surface-3); box-shadow: none; } .off .orb span { background: var(--hm-text-3); animation: none; }
.brief { flex: 1; min-width: 0; }
.bt { display: flex; align-items: center; gap: 8px; } .bt b { font-size: var(--hm-fs-sm); letter-spacing: .04em; background: var(--hm-ai-grad); -webkit-background-clip: text; background-clip: text; color: transparent; }
.off .bt b { background: none; color: var(--hm-text); }
.tag { font-size: 10.5px; color: var(--hm-text-3); border: 1px solid var(--hm-border); border-radius: var(--hm-r-pill); padding: 0 7px; line-height: 17px; background: #fff; }
.tag.on { color: var(--hm-primary-press); border-color: rgba(20, 99, 214, .3); }
.bx { font-size: var(--hm-fs-sm); color: var(--hm-text); margin-top: 2px; line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.qs { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; max-width: 46%; position: relative; z-index: 1; }
.q { display: inline-flex; align-items: center; gap: 5px; height: 28px; padding: 0 11px; border-radius: var(--hm-r-pill); font: inherit; font-size: var(--hm-fs-xs); cursor: pointer; white-space: nowrap;
  color: var(--hm-primary-press); border: 1px solid rgba(20, 99, 214, .25); background: rgba(255, 255, 255, .85); transition: all var(--hm-dur-2); }
.q:hover { background: #fff; border-color: var(--hm-primary); box-shadow: 0 2px 8px rgba(20, 99, 214, .15); }
.q.dis { color: var(--hm-text-3); border-color: var(--hm-border); cursor: not-allowed; } .q.dis .spk { background: var(--hm-text-3); }
.spk { width: 10px; height: 10px; background: var(--hm-ai-grad); clip-path: polygon(50% 0, 62% 38%, 100% 50%, 62% 62%, 50% 100%, 38% 62%, 0 50%, 38% 38%); flex: none; }
.x { position: absolute; right: 8px; top: 8px; width: 26px; height: 26px; border: 0; background: transparent; color: var(--hm-text-3); cursor: pointer; border-radius: var(--hm-r-sm); z-index: 1; }
.x:hover { background: rgba(15, 27, 45, .06); color: var(--hm-text); }
.strip-pill { align-self: flex-start; display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0 12px; border-radius: var(--hm-r-pill); font: inherit; font-size: var(--hm-fs-xs); cursor: pointer;
  color: var(--hm-primary-press); border: 1px solid transparent; background: linear-gradient(#fff, #fff) padding-box, var(--hm-ai-grad) border-box; margin-top: -4px; }

/* Edit layout */
.edit-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 12px; border-radius: var(--hm-r-md); background: var(--hm-primary-soft); color: var(--hm-primary-press); font-size: var(--hm-fs-xs); border: 1px dashed rgba(20, 99, 214, .4); }
.hl { color: var(--hm-text-2); }
.chip { display: inline-flex; align-items: center; gap: 3px; border: 1px solid rgba(20, 99, 214, .35); background: #fff; color: var(--hm-primary-press); border-radius: var(--hm-r-pill); font: inherit; font-size: 12px; padding: 1px 9px; cursor: pointer; }

/* Widget grid */
.wg-grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); grid-auto-flow: row dense; gap: 12px; align-items: stretch; }
.wg { position: relative; min-width: 0; display: flex; flex-direction: column; animation: hm-fade-up var(--hm-dur-3) var(--hm-ease) both; }
.wg > :deep(.hm-card) { flex: 1; }
.editing .wg { outline: 1.5px dashed rgba(20, 99, 214, .35); outline-offset: 3px; border-radius: var(--hm-r-lg); cursor: grab; }
.editing .wg > :not(.wg-tools) { pointer-events: none; opacity: .8; }
.wg.drag { opacity: .4; }
.wg.over.before::before, .wg.over.after::after { content: ''; position: absolute; z-index: 3; background: var(--hm-primary); border-radius: 2px; }
.wg.over.before::before { top: 0; bottom: 0; left: -8px; width: 3px; } .wg.over.after::after { top: 0; bottom: 0; right: -8px; width: 3px; }
.w-kpi.over.before::before, .w-andon.over.before::before { left: 0; right: 0; top: -8px; bottom: auto; width: auto; height: 3px; }
.wg-tools { display: flex; align-items: center; gap: 4px; padding: 4px 6px 6px; font-size: var(--hm-fs-xs); color: var(--hm-primary-press); }
.wg-tools .hd { cursor: grab; display: inline-flex; } .wg-tools b { font-weight: 600; }
.wg-tools button { border: 1px solid rgba(20, 99, 214, .3); background: #fff; color: var(--hm-primary-press); border-radius: var(--hm-r-sm); height: 24px; min-width: 24px; display: inline-flex; align-items: center; justify-content: center; gap: 3px; cursor: pointer; font: inherit; font-size: 12px; padding: 0 6px; }
.wg-tools button:disabled { opacity: .35; cursor: default; }
.wg-tools .hide { color: var(--hm-fault-ink); border-color: rgba(220, 43, 43, .3); }

.kpis { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; }
.sz-xl .kpis { grid-template-columns: repeat(8, minmax(0, 1fr)); }
.kpis:not(.k6) { grid-template-columns: repeat(3, minmax(0, 1fr)); } .sz-sm .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.kpis.compact { gap: 10px; } .kpis.compact :deep(.hm-kpi) { padding: 9px 11px 8px; } .kpis.compact :deep(.hm-kpi .v) { font-size: var(--hm-fs-2xl); }
.kpis.compact :deep(.hm-kpi .spark) { width: 54px; }
.kpis :deep(.hm-kpi .spark) { width: 64px; height: 22px; }
.sz-xl .kpis :deep(.hm-kpi .spark) { width: 80px; }
.ttl { display: inline-flex; align-items: center; gap: 8px; }
.ttl .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--hm-run); color: rgba(21, 163, 74, .45); }
.fill :deep(.cb) { display: flex; flex-direction: column; min-height: 0; }
.feedc { min-height: 380px; }
.feed-box { position: relative; flex: 1; min-height: 300px; }
.feed-box > * { position: absolute; inset: 0; }
.sz-sm .feed-box { min-height: 420px; }
.andon-card { padding: 10px 14px; }

.q-top { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; margin-bottom: 10px; }
.qn { background: var(--hm-surface-2); border: 1px solid var(--hm-hairline); border-radius: var(--hm-r-md); padding: 6px 8px; display: flex; flex-direction: column; min-width: 0; }
.qn small { font-size: 11px; color: var(--hm-text-3); white-space: nowrap; } .qn b { font-size: 20px; font-weight: 600; line-height: 1.2; }
.qn b.bad { color: var(--hm-fault-ink); } .qn b.warn { color: var(--hm-idle-ink); } .qn.click { cursor: pointer; } .qn.click:hover { border-color: var(--hm-primary); }
.sec { font-size: 11px; color: var(--hm-text-3); font-weight: 600; letter-spacing: .06em; margin: 4px 0 6px; }
.bar-row { display: grid; grid-template-columns: 96px minmax(0, 1fr) 42px; align-items: center; gap: 8px; padding: 3px 4px; border-radius: var(--hm-r-sm); font-size: var(--hm-fs-xs); }
.bar-row:hover { background: var(--hm-surface-2); }
.bn { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--hm-text-2); }
.bb { position: relative; height: 8px; background: var(--hm-surface-3); border-radius: 4px; overflow: hidden; }
.bb i { position: absolute; left: 0; top: 0; bottom: 0; background: linear-gradient(90deg, var(--hm-amber), #e9a659); border-radius: 4px; transition: width var(--hm-dur-3) var(--hm-ease); }
.bar-row > b { text-align: right; font-weight: 600; }
.oee { grid-template-columns: 64px minmax(0, 1fr) 42px; }
.oee .bb i { background: var(--hm-run); } .oee.warn .bb i { background: var(--hm-idle); } .oee.bad .bb i { background: var(--hm-fault); }
.oee .bb em { position: absolute; top: 0; bottom: 0; width: 1px; background: rgba(15, 27, 45, .25); }
.oee.bad > b { color: var(--hm-fault-ink); }
.ok-note { display: flex; align-items: center; gap: 6px; color: var(--hm-run-ink); font-size: var(--hm-fs-xs); padding: 8px 4px; }

/* Ultra-wide screens like 2560: bump font size one step, denser info */
.sz-xl { --hm-fs-2xs: 12px; --hm-fs-xs: 13px; --hm-fs-sm: 14px; --hm-fs-md: 15.5px; --hm-fs-xl: 22px; --hm-fs-3xl: 38px; gap: 14px; padding: var(--hm-sp-4) var(--hm-sp-6) var(--hm-sp-6); }
.sz-xl .wg-grid { gap: 14px; }
.sz-xl .bar-row { padding: 4px; } .sz-xl .bb { height: 10px; }
.sz-md .qs, .sz-sm .qs { max-width: none; } .sz-sm .ai-strip, .sz-md .ai-strip { flex-wrap: wrap; }
@media (prefers-reduced-motion: reduce) { .ai-strip::after, .orb span { animation: none; } }
</style>
