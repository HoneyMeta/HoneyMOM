<template>
  <div class="page skl">
    <HmPageHead :title="$t('common.aiSkills')" :sub="$t('skills.saveAGoodQA')">
      <el-segmented v-model="scope" :options="SCOPES" data-tour="skl-scope" @change="load" />
    </HmPageHead>

    <div class="grid lay">
      <HmCard :title="$t('skills.skillLibrary')" :sub="$t('skills.n0', [items.length])" flush data-tour="skl-list">
        <div class="list" data-test="skl-list">
          <HmEmpty v-if="!items.length" compact :title="$t('skills.noSkillsYet')" :desc="$t('skills.askAQuestionInThe')" />
          <button v-for="s in items" :key="s.id" class="it" :class="{ on: cur?.id === s.id }" :data-test="'skl-item-' + (s.key || s.id)" @click="open(s.id)">
            <div class="r1"><b>{{ s.name }}</b><span class="grow" /><HmTag :tone="s.status === 'broken' ? 'bad' : s.status === 'disabled' ? 'neutral' : 'ok'">{{ STATUS[s.status] || s.status }}</HmTag></div>
            <div class="r2"><span>{{ s.mode_label }}{{ s.schedule?.enabled === false && s.schedule?.mode !== 'manual' ? $t('skills.disabled') : '' }}</span><span>· {{ s.owner }}</span>
              <span v-if="s.key" class="bi">{{ $t('common.builtIn') }}</span><span v-if="s.last_run_at">{{ $t('skills.last0', [s.last_run_at.slice(5, 16)]) }}</span></div>
          </button>
        </div>
      </HmCard>

      <div v-if="cur" class="main" data-test="skl-detail">
        <HmCard :title="cur.name" :sub="$t('skills.v0Owner12', [cur.version, cur.owner, cur.run_count || 0, fmtNum(cur.cost_total || 0, 3)])">
          <template #actions>
            <el-button v-if="cur.can_edit" size="small" data-test="skl-save" type="primary" :loading="busy === 'save'" @click="save">{{ $t('skills.saveChanges') }}</el-button>
            <el-popconfirm v-if="cur.can_edit && !cur.key" :title="$t('skills.deleteThisSkill')" @confirm="remove"><template #reference><el-button size="small" text><el-icon><Delete /></el-icon></el-button></template></el-popconfirm>
          </template>
          <div v-if="cur.status === 'broken'" class="broken" data-test="skl-broken"><el-icon><WarningFilled /></el-icon>{{ $t('skills.broken0RecoversAutomaticallyOnce', [cur.broken_reason]) }}</div>
          <p class="muted">{{ cur.description }}</p>
          <div class="q" v-if="cur.question"><span class="muted">{{ $t('skills.originalQuestion') }}</span>{{ cur.question }}</div>

          <div class="grid g2 mt">
            <section>
              <h4>{{ $t('common.parameters') }}</h4>
              <HmEmpty v-if="!cur.params.length" compact plain :title="$t('skills.noParameters')" />
              <div v-for="p in cur.params" :key="p.key" class="prm">
                <span class="pl">{{ p.label }}<code>{{ '{' + p.key + '}' }}</code></span>
                <el-select v-if="p.type === 'period'" v-model="vals[p.key]" size="small" filterable allow-create style="width: 170px">
                  <el-option v-for="(lab, k) in meta.periods" :key="k" :value="k" :label="lab" /></el-select>
                <el-select v-else-if="p.type === 'line'" v-model="vals[p.key]" size="small" filterable allow-create clearable style="width: 170px">
                  <el-option v-for="l in lines" :key="l.id" :value="l.name" :label="l.name" /></el-select>
                <el-input v-else v-model="vals[p.key]" size="small" style="width: 170px" />
              </div>
              <div class="runb"><el-button type="primary" size="small" :loading="busy === 'run'" data-test="skl-run" @click="run(false)"><el-icon><VideoPlay /></el-icon>{{ $t('common.running') }}</el-button>
                <el-button size="small" :disabled="!aiOn('skills')" :loading="busy === 'runai'" data-test="skl-run-ai" @click="run(true)">{{ $t('skills.runWithAiConclusion') }}</el-button></div>
            </section>
            <section>
              <h4>{{ $t('common.runMode') }}</h4>
              <div class="prm"><span class="pl">{{ $t('common.method') }}</span><el-select v-model="ed.schedule.mode" size="small" style="width: 140px" data-test="skl-mode" :disabled="!cur.can_edit">
                <el-option v-for="(lab, k) in meta.modes" :key="k" :value="k" :label="lab" /></el-select>
                <el-switch v-if="ed.schedule.mode !== 'manual'" v-model="ed.schedule.enabled" size="small" :active-text="$t('common.enable')" :disabled="!cur.can_edit" data-test="skl-enabled" /></div>
              <div v-if="ed.schedule.mode === 'daily'" class="prm"><span class="pl">{{ $t('common.time') }}</span><el-time-select v-model="ed.schedule.time" size="small" start="00:00" end="23:30" step="00:30" style="width: 120px" :disabled="!cur.can_edit" /></div>
              <div v-if="ed.schedule.mode === 'cron'" class="prm"><span class="pl">cron</span><el-input v-model="ed.schedule.cron" size="small" :placeholder="$t('skills.minHourDayMonthWeekday')" style="width: 200px" :disabled="!cur.can_edit" /></div>
              <div v-if="cur.next_run_at" class="muted small">{{ $t('skills.nextRun0ByData', [cur.next_run_at.slice(0, 16)]) }}</div>
              <h4 class="mt">{{ $t('skillSaveDialog.sendResultsTo') }}</h4>
              <div class="prm"><el-checkbox v-model="ed.targets.feed" :disabled="!cur.can_edit">{{ $t('skillSaveDialog.aiWatchFeed') }}</el-checkbox><el-checkbox v-model="ed.targets.daily" :disabled="!cur.can_edit">{{ $t('common.productionDailyReport') }}</el-checkbox>
                <el-checkbox v-model="ed.narrative" :disabled="!cur.can_edit" data-test="skl-narr">{{ $t('skills.aiConclusionOneLlmCall') }}</el-checkbox></div>
              <div class="prm"><span class="pl">{{ $t('skills.notifyRoles') }}</span><el-select v-model="ed.targets.notify_roles" multiple size="small" style="width: 260px" :disabled="!cur.can_edit">
                <el-option v-for="(lab, k) in meta.roles" :key="k" :value="k" :label="lab" /></el-select></div>
              <div class="prm"><span class="pl">{{ $t('common.shareWith') }}</span><el-select v-model="ed.share_roles" multiple size="small" style="width: 260px" :disabled="!cur.can_edit">
                <el-option value="*" :label="$t('common.everyone')" /><el-option v-for="(lab, k) in meta.roles" :key="k" :value="k" :label="lab" /></el-select></div>
              <div class="prm"><el-checkbox v-model="ed.mcp_expose" :disabled="!cur.can_edit">{{ $t('skills.exposeAsAnMcpTool', [cur.id]) }}</el-checkbox></div>
            </section>
          </div>

          <h4 class="mt">{{ $t('skillSaveDialog.queryPlan') }} <span class="muted small">{{ $t('skills.toolCallsCapturedAtSave') }}</span></h4>
          <ol class="plan" data-test="skl-plan">
            <li v-for="(s, i) in cur.plan.steps" :key="i"><b>{{ toolTitle(s.tool) }}</b> <code>{{ s.tool }}</code> <span class="args">{{ JSON.stringify(s.args) }}</span></li>
          </ol>
        </HmCard>

        <HmCard v-if="res" class="mt" :title="$t('skills.runResult0', [res.id])" :sub="$t('skills.n012Ms3', [res.trigger === 'schedule' ? $t('common.scheduled') : res.trigger === 'manual' ? $t('common.manual') : res.trigger, res.started_at, res.ms, res.llm_calls, fmtNum(res.cost || 0, 4)])" data-test="skl-result">
          <div v-if="res.status !== 'ok'" class="broken">{{ $t('common.n012', [res.status === 'broken' ? $t('skills.broken') : $t('common.error2'), res.error]) }}</div>
          <div v-if="res.narrative" class="narr" data-test="skl-narrative"><b>{{ $t('common.n02', [res.by === 'demo' ? $t('skills.aiConclusionDemoMode') : $t('skills.aiConclusion')]) }}</b>{{ res.narrative }}</div>
          <AiBlocks v-if="res.blocks?.length" :blocks="res.blocks" :refs="[]" />
          <details class="steps"><summary>{{ $t('skills.steps0', [(res.steps || []).length]) }}</summary>
            <div v-for="(s, i) in res.steps || []" :key="i" class="st"><b>{{ toolTitle(s.tool) }}</b> {{ s.status === 'done' ? $t('skills.n0Rows1Ms', [s.rows ?? '', s.ms]) : s.error }}<div class="muted small">{{ s.summary }}</div></div>
          </details>
        </HmCard>

        <div class="grid g2 mt">
          <HmCard :title="$t('agentEditor.runHistory')" flush>
            <HmDataTable :columns="RCOLS" :rows="cur.runs || []" clickable max-height="260" :ai="false" @row-click="(r: any) => showRun(r.id)">
              <template #cell-status="{ row }"><HmTag :tone="row.status === 'ok' ? 'ok' : 'bad'">{{ RUNST[row.status] || row.status }}</HmTag></template>
            </HmDataTable>
          </HmCard>
          <HmCard :title="$t('common.versions')" flush>
            <HmDataTable :columns="VCOLS" :rows="cur.versions || []" max-height="260" :ai="false" />
          </HmCard>
        </div>
      </div>
      <HmEmpty v-else :title="$t('skills.selectASkill')" :desc="$t('skills.onTheLeftAreThe')" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { onMounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { aiOn } from '../ai';
import AiBlocks from '../ai/AiBlocks.vue';
import { del, get, post, put } from '../api';
import { fmtNum } from '../design/format';

const route = useRoute(), router = useRouter();
const SCOPES = [{ label: $t('common.all'), value: 'all' }, { label: $t('skills.mine'), value: 'mine' }, { label: $t('skills.sharedWithMe'), value: 'shared' }];
const STATUS: Record<string, string> = { active: $t('common.available'), broken: $t('skills.broken'), disabled: $t('common.disabled') };
const RUNST: Record<string, string> = { ok: $t('common.succeeded'), broken: $t('skills.broken'), error: $t('common.error2') };
const RCOLS = [{ key: 'id', label: '#', width: 50 }, { key: 'started_at', label: $t('common.time') }, { key: 'trigger', label: $t('common.trigger') }, { key: 'status', label: $t('common.result') },
  { key: 'llm_calls', label: $t('common.llm'), num: true }, { key: 'cost', label: $t('common.cny'), num: true, digits: 4 }, { key: 'ms', label: 'ms', num: true }];
const VCOLS = [{ key: 'version', label: $t('common.versions'), width: 60 }, { key: 'note', label: $t('common.description') }, { key: 'by', label: $t('skills.modifiedBy') }, { key: 'created_at', label: $t('common.time') }];
const scope = ref('all'), items = ref<any[]>([]), cur = ref<any>(null), res = ref<any>(null), busy = ref(''), lines = ref<any[]>([]);
const meta = reactive<any>({ modes: {}, periods: {}, roles: {}, tools: [] });
const vals = reactive<Record<string, any>>({});
const ed = reactive<any>({ schedule: { mode: 'manual' }, targets: {}, share_roles: [], narrative: false, mcp_expose: true });

const toolTitle = (n: string) => meta.tools.find((t: any) => t.name === n)?.title || n;
async function load() { items.value = (await get('/api/skills?scope=' + scope.value)).items; }
async function open(id: number) {
  cur.value = await get('/api/skills/' + id); res.value = null;
  for (const k of Object.keys(vals)) delete vals[k];
  for (const p of cur.value.params) vals[p.key] = p.default ?? '';
  Object.assign(ed, { schedule: { time: '07:30', cron: '', enabled: false, ...JSON.parse(JSON.stringify(cur.value.schedule)) }, targets: { feed: false, daily: false, notify_roles: [], notify_users: [], ...cur.value.targets },
    share_roles: [...(cur.value.share_roles || [])], narrative: !!cur.value.narrative, mcp_expose: cur.value.mcp_expose !== false });
  if (route.query.id !== String(id)) router.replace({ query: { id: String(id) } });
  if (cur.value.runs?.[0] && !route.query.run) showRun(cur.value.runs[0].id);
}
async function showRun(rid: number) { res.value = await get('/api/skills/runs/' + rid); }
async function run(narr: boolean) {
  busy.value = narr ? 'runai' : 'run';
  try { const r = await post(`/api/skills/${cur.value.id}/run`, { params: { ...vals }, narrative: narr }); const sid = cur.value.id; await open(sid); res.value = await get('/api/skills/runs/' + r.id); load(); }
  finally { busy.value = ''; }
}
async function save() {
  busy.value = 'save';
  try {
    const params = cur.value.params.map((p: any) => ({ ...p, default: vals[p.key] }));
    await put('/api/skills/' + cur.value.id, { params, schedule: ed.schedule, targets: ed.targets, share_roles: ed.share_roles, narrative: ed.narrative, mcp_expose: ed.mcp_expose });
    ElMessage.success($t('skills.savedNewVersion')); await open(cur.value.id); load();
  } finally { busy.value = ''; }
}
async function remove() { await del('/api/skills/' + cur.value.id); cur.value = null; router.replace({ query: {} }); load(); }
onMounted(async () => {
  Object.assign(meta, await get('/api/skills/meta'));
  get('/api/cycle/lines', true).then((r) => (lines.value = r.lines.filter((l: any) => l.id))).catch(() => {});
  await load();
  if (route.query.run) { const r = await get('/api/skills/runs/' + route.query.run); await open(r.skill); res.value = r; }
  else if (route.query.id) open(Number(route.query.id));
  else if (items.value[0]) open(items.value[0].id);
});
</script>

<style scoped>
.lay { grid-template-columns: 300px minmax(0, 1fr); align-items: start; }
@media (max-width: 1100px) { .lay { grid-template-columns: minmax(0, 1fr); } }
.list { display: flex; flex-direction: column; max-height: calc(100vh - 220px); overflow: auto; }
.it { text-align: left; border: 0; background: none; padding: 9px 14px; border-bottom: 1px solid var(--hm-border); cursor: pointer; font: inherit; color: inherit; }
.it:hover { background: var(--hm-surface-2); } .it.on { background: var(--hm-primary-soft, rgba(20, 99, 214, .08)); }
.r1 { display: flex; align-items: center; gap: 6px; } .r2 { font-size: var(--hm-fs-xs); color: var(--hm-text-3); margin-top: 2px; display: flex; gap: 4px; flex-wrap: wrap; }
.bi { color: var(--hm-primary, #1463d6); }
h4 { margin: 0 0 8px; font-size: var(--hm-fs-sm); }
.prm { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; font-size: var(--hm-fs-sm); }
.pl { width: 100px; color: var(--hm-text-2); } .pl code { margin-left: 4px; font-size: 11px; color: var(--hm-text-3); }
.runb { margin-top: 10px; display: flex; gap: 8px; }
.plan { margin: 0; padding-left: 20px; font-size: var(--hm-fs-sm); line-height: 1.9; } .plan .args { color: var(--hm-text-3); font-family: monospace; font-size: 12px; word-break: break-all; }
.broken { color: var(--hm-fault-ink); background: var(--hm-fault-bg, #fdecec); padding: 6px 10px; border-radius: var(--hm-r-sm); margin-bottom: 8px; display: flex; gap: 6px; align-items: center; }
.narr { padding: 8px 10px; border-left: 3px solid var(--hm-ai, #6d5ae6); background: var(--hm-surface-2); margin-bottom: 10px; line-height: 1.7; font-size: var(--hm-fs-sm); }
.q { font-size: var(--hm-fs-sm); margin-bottom: 6px; }
.steps { margin-top: 10px; font-size: var(--hm-fs-sm); } .st { padding: 4px 0; border-bottom: 1px dashed var(--hm-border); }
.grow { flex: 1; } .muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-xs); }
</style>
