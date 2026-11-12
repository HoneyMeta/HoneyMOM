<template>
  <div class="ip" :class="{ 'ip-big': big }" data-test="instruction">
    <template v-if="ins">
      <div class="hd">
        <b>{{ ins.process || $t('instructionPanel.thisOperation') }}</b>
        <HmTag v-if="ins.fai" tone="warn">{{ $t('common.firstArticleRelease') }}</HmTag>
        <HmTag v-if="ins.std_cycle_s" mono>{{ $t('instructionPanel.cycleTime0S', [fmtNum(ins.std_cycle_s, 0)]) }}</HmTag>
        <HmTag v-if="ins.report_mode" tone="primary">{{ $t('instructionPanel.n0Reporting', [MODE[ins.report_mode] || ins.report_mode]) }}</HmTag>
      </div>
      <p v-if="ins.instruction" class="txt" v-ai="{ kind: 'text', label: $t('instructionPanel.workInstructions0', [ins.process]), data: { [$t('common.workInstructions')]: ins.instruction, [$t('common.parameters')]: ins.params } }">{{ ins.instruction }}</p>
      <p v-else class="muted">{{ $t('instructionPanel.noKeyWorkPointsMaintained') }}</p>
      <dl v-if="paramList.length" class="params">
        <div v-for="p in paramList" :key="p[0]" v-ai="{ kind: 'field', label: `${p[0]} ${p[1]}`, data: { [$t('common.parameters')]: p[0], [$t('common.standard')]: p[1], [$t('common.operation')]: ins.process } }"><dt>{{ p[0] }}</dt><dd class="hm-num">{{ p[1] }}</dd></div>
      </dl>
      <div v-if="ins.drawings?.length" class="dw">
        <div class="lab">{{ $t('common.drawing') }}</div>
        <button v-for="d in ins.drawings" :key="d.id" class="chip" data-test="ins-drawing" @click="open = d">
          <el-icon><Document /></el-icon><span>{{ d.title }}</span><small>{{ (d.file_type || '').toUpperCase() }}{{ d.ctrl_status === 'effective' ? $t('instructionPanel.rev0', [d.version || '—']) : '' }}</small>
        </button>
      </div>
      <div v-if="ins.sops?.length" class="sops">
        <div class="lab">{{ $t('instructionPanel.workInstructionsStandards') }}</div>
        <el-collapse>
          <el-collapse-item v-for="s in ins.sops" :key="s.id" :title="s.ctrl_status === 'effective' ? $t('instructionPanel.n0ControlledCurrentRev1', [s.title, s.version || '—']) : s.title" :name="s.id"><div class="md">{{ s.content }}</div></el-collapse-item>
        </el-collapse>
      </div>
      <DrawingViewer v-if="inline && ins.drawings?.length && !open" :doc="ins.drawings[0]" class="inl" />
    </template>
    <HmEmpty v-else compact :title="$t('instructionPanel.noCurrentOperation')" :desc="$t('instructionPanel.thisMachineHasNoWork')" />
    <el-dialog :model-value="!!open" @update:model-value="open = null" :title="open?.title" width="min(1100px, 96vw)" append-to-body destroy-on-close>
      <DrawingViewer v-if="open" :doc="open" tall />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// Work guidance: key points of process steps + process parameters + machining drawings (linked by product and operation) + work instructions. Shared by Workstation Terminal, production report detail and mobile.
import { computed, ref } from 'vue';
import { fmtNum } from '../../design';
import DrawingViewer from './DrawingViewer.vue';

const props = withDefaults(defineProps<{ ins: any; big?: boolean; inline?: boolean }>(), { big: false, inline: false });
const MODE: Record<string, string> = { auto: $t('common.machineAuto'), manual: $t('common.manual2'), scan: $t('common.scan') };
const open = ref<any>(null);
const paramList = computed(() => Object.entries(props.ins?.params || {}).map(([k, v]) => [k, String(v)]));
</script>

<style scoped>
.ip { font-size: var(--hm-fs-sm); color: var(--hm-text); }
.hd { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; } .hd b { font-size: var(--hm-fs-md); }
.txt { margin: 4px 0 10px; line-height: 1.7; color: var(--hm-text-2); white-space: pre-wrap; }
.params { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 6px; margin: 0 0 10px; }
.params div { background: var(--hm-surface-2); border: 1px solid var(--hm-border); border-radius: var(--hm-r-sm); padding: 6px 8px; }
.params dt { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); } .params dd { margin: 2px 0 0; font-weight: var(--hm-fw-semibold); }
.lab { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); margin: 6px 0 4px; }
.dw { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.chip { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--hm-border); background: var(--hm-surface); border-radius: var(--hm-r-pill); padding: 4px 10px;
  cursor: pointer; font: inherit; color: var(--hm-primary); }
.chip:hover { background: var(--hm-primary-soft); } .chip small { color: var(--hm-text-3); }
.md { white-space: pre-wrap; color: var(--hm-text-2); line-height: 1.7; }
.inl { margin-top: 10px; }
.ip-big { font-size: var(--hm-fs-md); } .ip-big .hd b { font-size: var(--hm-fs-lg); } .ip-big .params dd { font-size: var(--hm-fs-lg); } .ip-big .chip { padding: 8px 14px; }
.muted { color: var(--hm-text-3); }
</style>
