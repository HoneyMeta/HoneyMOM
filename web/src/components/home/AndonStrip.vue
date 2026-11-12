<template>
  <div class="ast" :class="{ big, hot: open.length > 0 }" data-test="andon-strip">
    <div class="sum" v-ai="sumAi">
      <div class="lamp" :class="{ on: waiting > 0, mid: !waiting && open.length > 0 }" aria-hidden="true"><i /><i /><i /></div>
      <div class="st">
        <b class="hm-num">{{ open.length }}</b><span>{{ open.length ? $t('andonStrip.openAndonCalls') : $t('andonStrip.allAndonCallsClosed') }}</span>
        <small>{{ $t('andonStrip.awaitingResponse') }} <em class="hm-num">{{ waiting }}</em> {{ $t('andonStrip.inProgress') }} <em class="hm-num">{{ open.length - waiting }}</em> {{ $t('andonStrip.closedToday') }} <em class="hm-num">{{ closedToday }}</em></small>
      </div>
    </div>
    <div class="cards" :class="{ marquee: big && open.length > 3 }">
      <div class="lane" :style="big && open.length > 3 ? { animationDuration: open.length * 6 + 's' } : {}">
        <template v-for="copy in (big && open.length > 3 ? 2 : 1)" :key="copy">
          <button v-for="a in open" :key="a.id + '-' + copy" class="ac" :class="['t-' + a.atype, a.status]" v-ai="aiOf(a)" data-test="andon-card" @click="go($event)">
            <span class="ty">{{ TYPE[a.atype] || $t('common.andon') }}</span>
            <span class="tx"><b>{{ a.description }}</b><small>{{ a.equipment__label || a.station || a.code }} · {{ a.status === 'open' ? $t('andonStrip.awaitingResponse') : (a.responder || '') + $t('andonStrip.inProgress2') }}</small></span>
            <span class="tm hm-num" :title="a.raised_at">{{ since(a.raised_at, now) }}</span>
          </button>
        </template>
      </div>
      <div v-if="!open.length" class="calm"><el-icon><CircleCheckFilled /></el-icon>{{ $t('andonStrip.productionNormalNoPendingAndon') }}</div>
    </div>
    <el-button v-if="!big" class="more" link type="primary" @click="router.push('/andon')">{{ $t('andonStrip.andonBoard') }}</el-button>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// Andon strip: summary on the left (light tower: red = awaiting response, yellow = in progress, green = all closed), one card per open Andon on the right (type, issue, equipment, waiting time). On displays, more than 3 scroll horizontally.
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import { since } from './usePoll';

const props = withDefaults(defineProps<{ open?: any[]; closedToday?: number; now?: string; big?: boolean }>(), { open: () => [], closedToday: 0, now: '', big: false });
const router = useRouter();
const TYPE: Record<string, string> = { equipment: $t('common.equipment'), quality: $t('common.quality'), material: $t('common.material'), safety: $t('andonStrip.safety'), other: $t('common.other') };
const waiting = computed(() => props.open.filter((a) => a.status === 'open').length);
const sumAi = computed(() => ({ kind: 'kpi', label: $t('andonStrip.n0OpenAndonCalls1', [props.open.length, waiting.value]),
  data: { [$t('andonStrip.open')]: props.open.length, [$t('andonStrip.awaitingResponse')]: waiting.value, [$t('andonStrip.closedToday2')]: props.closedToday, [$t('common.details')]: props.open.map((a) => `${a.code} ${TYPE[a.atype] || ''} ${a.description}`).join($t('common.k764')) } }));
const aiOf = (a: any) => ({ kind: 'alert', entity: 'andon', id: a.id, label: $t('andonStrip.andon012', [a.code, a.description, a.status === 'open' ? $t('andonStrip.awaitingResponse') : $t('andonStrip.inProgress3')]),
  data: { [$t('common.andon')]: a.code, [$t('common.type')]: TYPE[a.atype], [$t('common.issue')]: a.description, [$t('common.equipment')]: a.equipment__label, [$t('common.workstation')]: a.station, [$t('andonStrip.raised')]: a.raised_at, [$t('andonStrip.waiting')]: since(a.raised_at, props.now), [$t('common.status')]: a.status } });
function go(e: MouseEvent) { if (!e.ctrlKey && !e.metaKey) router.push('/andon'); }
</script>

<style scoped>
.ast { display: flex; align-items: stretch; gap: 14px; min-width: 0; }
.sum { display: flex; align-items: center; gap: 12px; flex: none; padding-right: 14px; border-right: 1px solid var(--hm-border); min-width: 230px; }
.lamp { display: flex; flex-direction: column; gap: 2px; padding: 3px; border-radius: 6px; background: #e8edf3; }
.lamp i { width: 12px; height: 9px; border-radius: 2px; background: #cfd8e3; }
.lamp i:nth-child(3) { background: var(--hm-run); box-shadow: 0 0 6px rgba(21, 163, 74, .6); }
.lamp.mid i:nth-child(2) { background: var(--hm-idle); box-shadow: 0 0 6px rgba(217, 154, 22, .6); } .lamp.mid i:nth-child(3), .lamp.on i:nth-child(3) { background: #cfd8e3; box-shadow: none; }
.lamp.on i:nth-child(1) { background: var(--hm-fault); box-shadow: 0 0 8px rgba(220, 43, 43, .7); animation: as-blink 1s steps(2) infinite; }
@keyframes as-blink { 50% { opacity: .35; } }
.st { display: grid; grid-template-columns: auto 1fr; column-gap: 8px; align-items: baseline; }
.st b { font-size: 26px; font-weight: 600; line-height: 1; } .st span { font-size: var(--hm-fs-sm); color: var(--hm-text-2); font-weight: 600; }
.st small { grid-column: 1 / -1; font-size: 11px; color: var(--hm-text-3); margin-top: 3px; } .st small em { font-style: normal; color: var(--hm-text-2); font-weight: 600; }
.hot .st b { color: var(--hm-fault-ink); }
.cards { flex: 1; min-width: 0; overflow-x: auto; display: flex; align-items: center; }
.lane { display: flex; gap: 8px; }
.ac { display: flex; align-items: center; gap: 10px; width: 280px; flex: none; text-align: left; border: 1px solid var(--hm-border); background: var(--hm-surface); border-radius: var(--hm-r-md); padding: 7px 10px; cursor: pointer; font: inherit; }
.ac:hover { box-shadow: var(--hm-e2); border-color: var(--hm-border-strong); }
.ac.open { border-color: rgba(220, 43, 43, .45); background: linear-gradient(90deg, var(--hm-fault-soft), #fff 70%); }
.ac.responding { border-color: rgba(217, 154, 22, .45); background: linear-gradient(90deg, var(--hm-idle-soft), #fff 70%); }
.ty { flex: none; font-size: 11px; font-weight: 700; color: #fff; background: var(--hm-text-2); border-radius: var(--hm-r-sm); padding: 2px 6px; }
.open .ty { background: var(--hm-fault); } .responding .ty { background: var(--hm-idle); } .t-safety .ty { background: #b42318; }
.tx { flex: 1; min-width: 0; display: flex; flex-direction: column; } .tx b { font-size: 12.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tx small { font-size: 11px; color: var(--hm-text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tm { font-size: 12px; font-weight: 600; color: var(--hm-fault-ink); white-space: nowrap; } .responding .tm { color: var(--hm-idle-ink); }
.calm { display: flex; align-items: center; gap: 8px; color: var(--hm-run-ink); font-size: var(--hm-fs-sm); } .calm .el-icon { font-size: 18px; }
.more { align-self: center; flex: none; }
.marquee { overflow: hidden; } .marquee .lane { animation: as-scroll linear infinite; }
@keyframes as-scroll { to { transform: translateX(-50%); } }
.big .st b { font-size: 40px; } .big .st span { font-size: 18px; } .big .st small { font-size: 14px; } .big .ac { width: 380px; padding: 10px 14px; }
.big .tx b { font-size: 17px; } .big .tx small { font-size: 14px; } .big .ty { font-size: 14px; } .big .tm { font-size: 16px; } .big .calm { font-size: 18px; }
.big .lamp i { width: 16px; height: 12px; }
@media (max-width: 760px) { .ast { flex-direction: column; } .sum { border-right: 0; padding-right: 0; } }
@media (prefers-reduced-motion: reduce) { .marquee .lane, .lamp.on i { animation: none; } }
</style>
