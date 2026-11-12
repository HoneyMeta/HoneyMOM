<template>
  <div class="page">
    <HmPageHead :title="$t('common.andonBoard')" :sub="$t('andon.dataClock0RefreshesEvery', [(d?.now || '').slice(0, 19) || '—'])">
      <el-button v-if="writable" type="danger" data-tour="andon-raise" @click="openRaise"><el-icon><Bell /></el-icon>{{ $t('andon.raiseAndon') }}</el-button>
      <el-button @click="router.push('/e/andon')"><el-icon><Tickets /></el-icon>{{ $t('andon.allRecords') }}</el-button>
    </HmPageHead>

    <div class="grid g4" data-tour="kpis">
      <HmKpi :label="$t('andonStrip.awaitingResponse')" :value="waiting.length" format="int" :unit="$t('common.k2047')" :status="waiting.length ? 'bad' : 'ok'" :loading="!d" :hint="waiting.length ? $t('andon.longest0', [fmtDur(maxWait)]) : $t('andon.noAndonCallsWaiting')" />
      <HmKpi :label="$t('andonStrip.inProgress3')" :value="handling.length" format="int" :unit="$t('common.k2047')" :status="handling.length ? 'warn' : ''" :loading="!d" />
      <HmKpi :label="$t('andon.closedInLast2Days')" :value="d?.closed?.length ?? null" format="int" :unit="$t('common.k2047')" :loading="!d" />
      <HmKpi :label="$t('andon.boardMessages')" :value="d?.board?.length ?? null" format="int" :unit="$t('common.k2047')" :loading="!d" :hint="$t('andon.includesAgentTriageSuggestions')" />
    </div>

    <div class="grid split mt" v-if="d">
      <HmCard :title="$t('andon.openAndonCalls')" :sub="$t('andon.n0ByTimeRaised', [d.open.length])" data-tour="andon-open">
        <HmEmpty v-if="!d.open.length" compact :title="$t('andon.noOpenAndonCalls')" :desc="$t('andon.everythingIsRunningNormally')" />
        <div v-for="a in d.open" :key="a.id" class="an" :class="['t-' + a.atype, 's-' + a.status]"
          v-ai="{ kind: 'alert', entity: 'andon', id: a.id, label: $t('andon.andon012', [a.code, T[a.atype] || a.atype, a.description]), data: { [$t('common.no2')]: a.code, [$t('common.type')]: T[a.atype], [$t('common.status')]: a.status === 'open' ? $t('andonStrip.awaitingResponse') : $t('andonStrip.inProgress3'), [$t('common.equipment')]: a.equipment__label, [$t('andon.description')]: a.description, [$t('andonStrip.raised')]: a.raised_at, [$t('andon.responder')]: a.responder, equipment_id: a.equipment } }">
          <div class="ah">
            <span class="ty"><i />{{ T[a.atype] || a.atype }}</span>
            <b class="hm-mono">{{ a.code }}</b>
            <HmTag :tone="a.status === 'open' ? 'bad' : 'warn'">{{ a.status === 'open' ? $t('andonStrip.awaitingResponse') : $t('andonStrip.inProgress3') }}</HmTag>
            <span class="grow" />
            <span class="muted small hm-num" :title="a.raised_at">{{ $t('andon.n0Waiting1', [fmtTime(a.raised_at), fmtDur(age(a))]) }}</span>
          </div>
          <div class="desc">{{ a.description }}</div>
          <div class="muted small">{{ a.equipment__label || a.station || '—' }}<template v-if="a.responder"> {{ $t('andon.responder0', [a.responder]) }}</template></div>
          <div class="btns">
            <el-button v-if="writable && a.status === 'open'" size="small" type="primary" @click="act(a, 'respond')">{{ $t('andon.iLlRespond') }}</el-button>
            <el-button v-if="writable" size="small" type="success" plain @click="closeA(a)">{{ $t('common.close') }}</el-button>
            <el-button v-if="a.equipment" size="small" @click="router.push(newRec('repair_order', { equipment: a.equipment, fault: a.description }))">{{ $t('common.requestRepair') }}</el-button>
            <el-button v-if="aiOn('ask')" size="small" @click="askAi($t('andon.andon01HelpMe', [a.code, a.description]), { kind: 'alert', entity: 'andon', id: a.id, label: $t('andon.andon0', [a.code]) }, { action: 'diagnose' })">
              <el-icon><MagicStick /></el-icon>{{ $t('andon.askAiToInvestigate') }}</el-button>
          </div>
        </div>
      </HmCard>
      <div class="col">
        <HmCard :title="$t('andon.boardMessages')" :sub="$t('andon.last2Days')" data-tour="andon-board">
          <HmEmpty v-if="!d.board.length" compact plain :title="$t('andon.noMessagesYet')" :desc="$t('andon.onceTheAndonTriageAgent')" />
          <div v-for="n in d.board" :key="n.id" class="bm" :class="'l-' + n.level" v-ai="{ kind: 'text', label: n.title, data: { [$t('common.title')]: n.title, [$t('common.source')]: n.source, [$t('common.time')]: n.time } }">
            <div class="bh"><b>{{ n.title }}</b><span class="grow" /><span class="muted small hm-num">{{ fmtTime(n.time) }} · {{ n.source }}</span></div>
            <div class="mdx small" v-html="md(n.content || '')" />
          </div>
        </HmCard>
        <HmCard :title="$t('andon.recentlyClosed')" flush>
          <HmDataTable :columns="CL" :rows="d.closed" entity="andon" max-height="260" :empty-text="$t('andon.noAndonCallsClosedIn')" empty-plain :row-label="(r: any) => $t('andon.andon01', [r.code, r.description])" />
        </HmCard>
      </div>
    </div>
    <HmSkeleton v-else variant="table" :rows="6" />

    <el-dialog v-model="raiseOpen" :title="$t('andon.raiseAndon2')" width="min(480px, 96vw)">
      <el-form label-width="80px">
        <el-form-item :label="$t('common.type')"><el-radio-group v-model="form.atype"><el-radio-button v-for="(v, k) in T" :key="k" :value="k">{{ v }}</el-radio-button></el-radio-group></el-form-item>
        <el-form-item :label="$t('common.equipment')"><RefSelect v-model="form.equipment" entity="equipment" /></el-form-item>
        <el-form-item :label="$t('common.problemDescription')"><el-input v-model="form.description" type="textarea" :rows="3" :placeholder="$t('andon.oneSentenceWhereAndWhat')" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="raiseOpen = false">{{ $t('common.cancel') }}</el-button><el-button type="danger" @click="raise">{{ $t('andonStrip.raised') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { aiOn, askAi } from '../ai';
import { canWrite, get, post } from '../api';
import RefSelect from '../components/RefSelect.vue';
import { fmtDur, fmtTime } from '../design/format';
import { md } from '../md';
import { newRec } from './aiActions';

const router = useRouter();
const d = ref<any>(null), raiseOpen = ref(false), form = reactive<any>({ atype: 'equipment', equipment: null, description: '' });
const T: Record<string, string> = { equipment: $t('common.equipment'), quality: $t('common.quality'), material: $t('common.material'), safety: $t('andonStrip.safety'), other: $t('common.other') };
const CL = [{ key: 'code', label: $t('common.no2'), width: 120 }, { key: 'description', label: $t('common.issue') }, { key: 'responder', label: $t('andon.handler'), width: 80 }, { key: 'close_note', label: $t('common.result') }];
const writable = computed(() => canWrite($t('andon.productionManagement')));
const waiting = computed(() => (d.value?.open || []).filter((a: any) => a.status === 'open'));
const handling = computed(() => (d.value?.open || []).filter((a: any) => a.status !== 'open'));
const ts = (s: string) => new Date(String(s || '').replace(' ', 'T')).getTime();
const age = (a: any) => Math.max(0, (ts(d.value?.now) - ts(a.raised_at)) / 1000);
const maxWait = computed(() => Math.max(0, ...waiting.value.map(age)));
async function load() { d.value = await get('/api/andon/board', true).catch(() => d.value); }
let timer: any;
onMounted(() => { load(); timer = setInterval(load, 5000); });
onUnmounted(() => clearInterval(timer));
function openRaise() { Object.assign(form, { atype: 'equipment', equipment: null, description: '' }); raiseOpen.value = true; }
async function raise() {
  if (!form.description) { ElMessage.error($t('andon.pleaseEnterAProblemDescription')); return; }
  await post('/api/e/andon', { ...form, raised_at: d.value?.now, status: 'open', source: 'manual', raised_by: '' });
  raiseOpen.value = false; ElMessage.success($t('andon.raised')); load();
}
async function act(a: any, name: string) { await post(`/api/e/andon/${a.id}/do/${name}`, {}); load(); }
async function closeA(a: any) {
  const { value } = await ElMessageBox.prompt($t('common.resolution'), $t('andon.close0', [a.code]), { inputValue: $t('common.resolved') });
  await post(`/api/e/andon/${a.id}/do/close`, { note: value }); load();
}
</script>

<style scoped>
.split { grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr); align-items: start; }
@media (max-width: 1279px) { .split { grid-template-columns: minmax(0, 1fr); } }
.col { display: flex; flex-direction: column; gap: var(--hm-gap); min-width: 0; }
.an { --c: var(--hm-fault); border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); padding: 10px 12px 10px 14px; margin-bottom: 8px; background: var(--hm-surface);
  box-shadow: inset 3px 0 0 var(--c); }
.an.s-open { background: linear-gradient(90deg, var(--hm-fault-soft), var(--hm-surface) 40%); }
.an.t-quality { --c: var(--hm-ai-3); } .an.t-material { --c: var(--hm-primary); } .an.t-safety { --c: var(--hm-idle); } .an.t-other { --c: var(--hm-offline); }
.ah { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.ty { display: inline-flex; align-items: center; gap: 5px; font-size: var(--hm-fs-xs); font-weight: 600; color: var(--hm-text-2); }
.ty i { width: 8px; height: 8px; border-radius: 2px; background: var(--c); }
.desc { font-size: var(--hm-fs-md); margin: 6px 0 2px; color: var(--hm-text); }
.btns { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
.bm { border-bottom: 1px solid var(--hm-border); padding: 8px 0; }
.bm:last-child { border-bottom: 0; }
.bh { display: flex; align-items: baseline; gap: 8px; }
.bm.l-warn b { color: var(--hm-idle-ink); } .bm.l-urgent b { color: var(--hm-fault-ink); }
.mdx { line-height: 1.6; color: var(--hm-text-2); } .mdx :deep(p) { margin: 2px 0; } .mdx :deep(ol), .mdx :deep(ul) { margin: 2px 0; padding-left: 18px; }
</style>
