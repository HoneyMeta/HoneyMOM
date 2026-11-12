<template>
  <div class="trace">
    <div v-if="!rows.length" class="muted small empty">{{ $t('traceViewer.noStepsYet') }}</div>
    <div v-for="(s, i) in rows" :key="i" class="step" :class="['st-' + s.status, { open: openIdx === i, hi: highlight && s.node === highlight }]">
      <div class="head" @click="openIdx = openIdx === i ? -1 : i">
        <span class="dot" />
        <span class="title">{{ s.title }}</span>
        <span v-if="s.iteration !== null && s.iteration !== undefined" class="it">{{ $t('traceViewer.item0', [s.iteration + 1]) }}</span>
        <span v-if="s.attempt > 1" class="it">{{ $t('traceViewer.iteration0', [s.attempt]) }}</span>
        <span class="grow" />
        <span class="meta">{{ STATUS_CN[s.status] || s.status }}</span>
        <span v-if="s.ms !== null && s.ms !== undefined" class="meta">{{ dur(s.ms) }}</span>
        <span v-if="s.tokens" class="meta">{{ s.tokens }} token</span>
        <el-icon class="arrow"><ArrowRight /></el-icon>
      </div>
      <div v-if="s.sub && openIdx !== i" class="sub">{{ s.sub }}</div>
      <div v-if="s.output && typeof s.output === 'object' && s.output.fallback" class="fb">{{ $t('traceViewer.llmNotUsed0', [s.output.fallback === true ? $t('traceViewer.handledByRules') : s.output.fallback]) }}</div>
      <div v-if="openIdx === i" class="body">
        <div v-if="s.error" class="err">{{ s.error }}</div>
        <template v-if="s.toolCalls?.length">
          <div class="lab">{{ $t('traceViewer.toolsCalled') }}</div>
          <div v-for="(t, k) in s.toolCalls" :key="k" class="tc">
            <div>{{ t.phrase || t.tool }} <span class="muted small">{{ t.ms ? dur(t.ms) : '' }}</span></div>
            <details><summary class="small muted">{{ $t('traceViewer.argumentsAndResults') }}</summary><pre>{{ pretty(t.args) }}</pre><pre>{{ clip(t.result) }}</pre></details>
          </div>
        </template>
        <template v-if="text(s.output)">
          <div class="lab">{{ $t('skillSaveDialog.output') }}</div>
          <div class="out mdx" v-html="md(text(s.output))" />
        </template>
        <details v-if="s.output !== undefined && s.output !== null" :open="!text(s.output)"><summary class="small muted">{{ $t('traceViewer.outputData') }}</summary><pre>{{ clip(pretty(s.output)) }}</pre></details>
        <details v-if="s.input && Object.keys(s.input).length"><summary class="small muted">{{ $t('traceViewer.input') }}</summary><pre>{{ clip(pretty(s.input)) }}</pre></details>
        <details v-if="s.messages?.length"><summary class="small muted">{{ $t('traceViewer.messagesSentToTheLlm', [s.messages.length]) }}</summary>
          <div v-for="(m, k) in s.messages" :key="k" class="msg"><b>{{ ROLE[m.role] || m.role }}</b><pre>{{ clip(m.content || pretty(m.tool_calls)) }}</pre></div>
        </details>
        <div class="muted small" v-if="s.at">{{ s.at }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { computed, ref } from 'vue';
import { STATUS_CN } from '../api';
import { md } from '../md';

// Shared "process viewer": agent run steps (kind=agent) and chat assistant tool calls (kind=chat)
const props = defineProps<{ steps: any[]; kind?: 'agent' | 'chat'; types?: Record<string, any>; highlight?: string }>();
const openIdx = ref(-1);
const ROLE: Record<string, string> = { system: $t('traceViewer.system'), user: $t('traceViewer.question'), assistant: $t('common.model'), tool: $t('traceViewer.toolResult') };
const rows = computed(() => (props.steps || []).map((s: any) => props.kind === 'chat'
  ? { title: s.phrase || s.tool, status: s.status || 'done', ms: s.ms, input: s.args, output: tryJson(s.result), sub: '', toolCalls: null, node: null }
  : { title: s.name || s.node, sub: props.types?.[s.ntype]?.label, status: s.status, ms: s.duration_ms, tokens: s.tokens, input: s.input, output: s.output,
      toolCalls: s.tool_calls, messages: s.messages, error: s.error, iteration: s.iteration, attempt: s.attempt, at: s.started_at, node: s.node }));
function tryJson(v: any) { if (typeof v !== 'string') return v; try { return JSON.parse(v); } catch { return v; } }
const dur = (ms: number) => (ms < 1000 ? ms + ' ms' : ms < 60000 ? (ms / 1000).toFixed(1) + $t('traceViewer.s') : Math.round(ms / 60000) + $t('common.min'));
const pretty = (v: any) => (typeof v === 'string' ? v : JSON.stringify(v, null, 2));
const clip = (s: any) => { const t = typeof s === 'string' ? s : pretty(s); return t && t.length > 6000 ? t.slice(0, 6000) + $t('traceViewer.truncated') : t; };
function text(o: any): string {
  if (!o || typeof o !== 'object') return typeof o === 'string' ? o : '';
  for (const k of ['text', 'content', 'summary', 'answer', 'reply']) if (typeof o[k] === 'string' && o[k]) return o[k];
  if (o.decision) return $t('traceViewer.n012', [o.decision === 'approved' ? $t('common.approved') : $t('common.rejected'), o.by || '', o.reason ? $t('common.k384') + o.reason : '']);
  if (o.category_name) return $t('traceViewer.routedTo01', [o.category_name, o.reason ? $t('common.k25') + o.reason + $t('common.k386') : '']);
  if (o.matched_name) return $t('traceViewer.branchTaken0', [o.matched_name]);
  const w = o.waiting ?? o['\u7b49\u5f85'];  // backend output key (zh), English alias if provided
  if (w) return $t('traceViewer.waitingFor0', [w]);
  return '';
}
</script>

<style scoped>
.trace { display: flex; flex-direction: column; gap: 6px; }
.empty { padding: 8px 2px; }
.step { border: 1px solid #e6eaee; border-radius: 8px; background: #fff; }
.step.hi { box-shadow: 0 0 0 2px #f2c94c; }
.head { display: flex; align-items: center; gap: 6px; padding: 7px 10px; cursor: pointer; font-size: 13px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #b7c0c8; flex: none; }
.st-done .dot, .st-succeeded .dot { background: #2e9d5b; } .st-failed .dot, .st-rejected .dot { background: #c83a3a; }
.st-running .dot { background: #2f7ad8; animation: pulse 1s infinite; } .st-waiting .dot { background: #e0a020; }
@keyframes pulse { 50% { opacity: .3; } }
.title { font-weight: 600; color: #26303a; } .it { font-size: 11px; color: #8a5a07; background: #fbf4e6; border-radius: 4px; padding: 0 5px; }
.grow { flex: 1; } .meta { color: #6b7680; font-size: 12px; }
.arrow { transition: transform .15s; color: #9aa4ad; } .open .arrow { transform: rotate(90deg); }
.sub { padding: 0 10px 7px 24px; font-size: 12px; color: #6b7680; margin-top: -4px; }
.fb { margin: -2px 10px 7px 24px; font-size: 12px; color: #8a5a07; background: #fdf6e3; border-radius: 5px; padding: 2px 7px; }
.body { padding: 2px 12px 10px 24px; font-size: 12.5px; }
.lab { font-size: 12px; color: #6b7680; margin: 8px 0 3px; }
.err { color: #a12a2a; background: #fbe9e9; border-radius: 6px; padding: 6px 8px; white-space: pre-wrap; }
.out { background: #fbf8f1; border-radius: 6px; padding: 6px 10px; max-height: 260px; overflow: auto; }
.tc { border-left: 3px solid #f2c94c; padding: 2px 8px; margin: 4px 0; }
pre { white-space: pre-wrap; word-break: break-all; background: #f6f8fa; border-radius: 6px; padding: 6px 8px; margin: 4px 0; font-size: 11.5px; max-height: 300px; overflow: auto; }
.msg b { font-size: 12px; color: #6b7680; }
.mdx { line-height: 1.6; } .mdx :deep(p) { margin: 3px 0; } .mdx :deep(ul), .mdx :deep(ol) { margin: 3px 0; padding-left: 18px; }
.mdx :deep(h3), .mdx :deep(h4), .mdx :deep(h5) { margin: 6px 0 3px; font-size: 13px; }
.mdx :deep(.mdt) { border-collapse: collapse; } .mdx :deep(.mdt td), .mdx :deep(.mdt th) { border: 1px solid #dde2e6; padding: 2px 6px; }
</style>
