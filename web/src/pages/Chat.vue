<template>
  <div class="page chat">
    <div class="page-head"><h2>{{ $t('chat.askData') }}</h2>
      <span class="sub">{{ st?.available ? $t('chat.connectedToLlm0Ask', [st.model]) : $t('chat.noLlmConfiguredConfigLlm') }}</span>
      <span class="grow" /><el-radio-group v-model="mode"><el-radio-button value="data">{{ $t('chat.askData2') }}</el-radio-button><el-radio-button value="kb">{{ $t('chat.askKnowledgeBase') }}</el-radio-button></el-radio-group></div>
    <div class="card msgs" ref="box">
      <div v-if="!msgs.length" class="hints">
        <p class="muted">{{ $t('chat.tryTheseQuestions') }}</p>
        <el-button v-for="h in (mode === 'data' ? HINTS : KBH)" :key="h" round size="small" @click="ask(h)">{{ h }}</el-button>
      </div>
      <div v-for="(m, i) in msgs" :key="i" :class="['msg', m.role]">
        <div class="bubble">
          <DemoNote v-if="m.role === 'assistant'" :text="m.note" /><div v-if="m.role === 'assistant'" class="mdx" v-html="md(m.text)" /><div v-else class="md">{{ m.text }}</div>
          <Chart v-if="m.chart" :option="m.chart" :height="260" />
          <details v-if="m.sqls?.length"><summary class="small muted">{{ $t('chat.ran0Queries', [m.sqls.length]) }}</summary><pre class="small">{{ m.sqls.join('\n\n') }}</pre></details>
          <div v-if="m.refs?.length" class="small muted">{{ $t('chat.source0', [m.refs.map((r: any) => r.title).join($t('common.k42'))]) }}</div>
        </div>
      </div>
      <div v-if="busy" class="msg assistant"><div class="bubble muted">{{ $t('chat.thinking') }}</div></div>
    </div>
    <div class="input"><el-input v-model="q" :placeholder="mode === 'data' ? $t('chat.eGWhichMachineTool') : $t('chat.eGWhatToDo')" @keyup.enter="ask()" size="large" />
      <el-button type="primary" size="large" :loading="busy" @click="ask()">{{ $t('aiDock.send') }}</el-button></div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { nextTick, onMounted, ref } from 'vue';
import { get, post } from '../api';
import DemoNote from '../ai/DemoNote.vue';
import Chart from '../components/Chart.vue';
import { md } from '../md';

const HINTS = [$t('aiDock.howAreTodaySOutput'), $t('chat.whichMachineHadTheLowest'), $t('chat.countNonconformancesByDefectType'), $t('chat.compareEnergyPerTonneAnd'),
  $t('chat.howLongWillTheCurrent'), $t('chat.whatAreTheMainCauses')];
const KBH = [$t('chat.whatToDoAboutLow'), $t('chat.whatAreTheCoolingWater'), $t('chat.whatAreTheAcceptanceCriteria'), $t('chat.howOftenShouldDrillT03')];
const st = ref<any>(null), q = ref(''), busy = ref(false), msgs = ref<any[]>([]), mode = ref('data'), box = ref<HTMLElement>();
onMounted(async () => { st.value = await get('/api/ai/status'); });
function parse(text: string) {
  const m = text.match(/```chart\s*([\s\S]*?)```/);
  if (!m) return { text, chart: null };
  try {
    const c = JSON.parse(m[1]);
    const chart = c.type === 'pie' ? { title: { text: c.title, left: 'center', textStyle: { fontSize: 13 } }, tooltip: {}, series: [{ type: 'pie', radius: '60%', data: (c.x || []).map((x: any, i: number) => ({ name: x, value: c.series?.[0]?.data[i] })) }] }
      : { title: { text: c.title, textStyle: { fontSize: 13 } }, tooltip: { trigger: 'axis' }, legend: { top: 0, right: 0 }, grid: { left: 50, right: 10, top: 36, bottom: 30 },
        xAxis: { type: 'category', data: c.x }, yAxis: { type: 'value' }, series: (c.series || []).map((s: any) => ({ ...s, type: c.type || 'bar' })) };
    return { text: text.replace(m[0], '').trim(), chart };
  } catch { return { text, chart: null }; }
}
async function ask(text?: string) {
  const v = (text || q.value).trim(); if (!v) return;
  msgs.value.push({ role: 'user', text: v }); q.value = ''; busy.value = true; scroll();
  try {
    if (mode.value === 'kb') { const r = await post('/api/ai/kb_ask', { q: v }); msgs.value.push({ role: 'assistant', text: r.answer, refs: r.refs, note: r.demo_note }); }
    else {
      const hist = msgs.value.slice(0, -1).map((m) => ({ role: m.role, content: m.text }));
      const r = await post('/api/ai/ask', { q: v, history: hist });
      msgs.value.push({ role: 'assistant', ...parse(r.answer), sqls: r.sqls, note: r.demo_note });
    }
  } finally { busy.value = false; scroll(); }
}
const scroll = () => nextTick(() => box.value && (box.value.scrollTop = box.value.scrollHeight));
</script>

<style scoped>
.chat { display: flex; flex-direction: column; height: calc(100vh - 52px); } .msgs { flex: 1; overflow: auto; margin-bottom: 10px; }
.msg { display: flex; margin: 8px 0; } .msg.user { justify-content: flex-end; } .bubble { max-width: min(760px, 90%); padding: 10px 14px; border-radius: 12px; background: #f3f5f7; }
.msg.user .bubble { background: #d99a16; color: #fff; } .input { display: flex; gap: 8px; } .hints .el-button { margin: 4px; }
pre { white-space: pre-wrap; background: #fff; padding: 6px; border-radius: 6px; }
.mdx { line-height: 1.7; font-size: 13.5px; } .mdx :deep(p) { margin: 4px 0; } .mdx :deep(ul), .mdx :deep(ol) { margin: 4px 0; padding-left: 20px; }
.mdx :deep(.mdt) { border-collapse: collapse; margin: 6px 0; background: #fff; } .mdx :deep(.mdt th), .mdx :deep(.mdt td) { border: 1px solid #dde2e6; padding: 3px 8px; font-size: 12.5px; }
.mdx :deep(.mdt th) { background: #f6f2e6; } .mdx :deep(code) { background: #fff; padding: 0 4px; border-radius: 4px; } .mdx :deep(h3), .mdx :deep(h4) { margin: 8px 0 4px; }
</style>
