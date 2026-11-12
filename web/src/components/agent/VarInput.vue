<template>
  <div class="vi" ref="wrap">
    <el-input ref="inp" :model-value="modelValue" @update:model-value="(v: any) => emit('update:modelValue', v)" :type="multi ? 'textarea' : 'text'"
      :autosize="multi ? { minRows: rows || 2, maxRows: 12 } : undefined" :placeholder="placeholder || (vars?.length ? $t('varInput.typeInsertVariable') : '')" :class="{ mono }"
      @keydown="onKey" size="small">
      <template v-if="!multi && vars?.length" #suffix><span class="vbtn" @mousedown.prevent="openPicker()" :title="$t('varInput.insertVariable')">{x}</span></template>
    </el-input>
    <span v-if="multi && vars?.length" class="vbtn abs" @mousedown.prevent="openPicker()" :title="$t('varInput.insertVariable')">{x}</span>
    <div v-if="open" class="pick" @mousedown.prevent>
      <el-input v-model="filter" size="small" :placeholder="$t('varInput.filterVariables')" ref="fin" @keydown.esc="close" @keydown.enter.prevent="pickFirst" />
      <div class="lst">
        <template v-for="g in filtered" :key="g.label">
          <div class="g">{{ g.label }}</div>
          <div v-for="it in g.items" :key="it.ref" class="it" @click="pick(it.ref)"><code>{{ it.ref }}</code><span>{{ it.label }}</span></div>
        </template>
        <div v-if="!filtered.length" class="muted small" style="padding:6px">{{ $t('varInput.noVariablesAvailableConnectAn') }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue';

// Input with variable picker: typing "/" pops up available variables (upstream node outputs, inputs, system variables); selecting inserts {node@output}
const props = defineProps<{ modelValue: any; vars?: { label: string; items: { ref: string; label: string }[] }[]; multi?: boolean; rows?: number; mono?: boolean; placeholder?: string }>();
const emit = defineEmits(['update:modelValue']);
const inp = ref<any>(), fin = ref<any>(), wrap = ref<HTMLElement>();
const open = ref(false), filter = ref('');
let caret = -1;
const filtered = computed(() => {
  const f = filter.value.trim().toLowerCase();
  return (props.vars || []).map((g) => ({ label: g.label, items: g.items.filter((i) => !f || i.ref.toLowerCase().includes(f) || i.label.toLowerCase().includes(f) || g.label.toLowerCase().includes(f)) }))
    .filter((g) => g.items.length);
});
function el(): HTMLInputElement | HTMLTextAreaElement | null { return inp.value?.textarea || inp.value?.input || null; }
function onKey(e: KeyboardEvent) {
  if (e.key === '/' && props.vars?.length) {
    e.preventDefault();
    openPicker();
  }
}
function openPicker() {
  const x = el();
  caret = x ? x.selectionStart ?? String(props.modelValue ?? '').length : String(props.modelValue ?? '').length;
  filter.value = '';
  open.value = true;
  nextTick(() => fin.value?.focus());
  setTimeout(() => document.addEventListener('mousedown', outside), 0);
}
function close() { open.value = false; document.removeEventListener('mousedown', outside); nextTick(() => el()?.focus()); }
function outside(e: MouseEvent) { if (wrap.value && !wrap.value.contains(e.target as Node)) { open.value = false; document.removeEventListener('mousedown', outside); } }
function pick(ref_: string) {
  const s = String(props.modelValue ?? '');
  const at = caret < 0 || caret > s.length ? s.length : caret;
  emit('update:modelValue', s.slice(0, at) + ref_ + s.slice(at));
  close();
  nextTick(() => { const x = el(); if (x) { x.selectionStart = x.selectionEnd = at + ref_.length; } });
}
function pickFirst() { const g = filtered.value[0]; if (g?.items[0]) pick(g.items[0].ref); }
onBeforeUnmount(() => document.removeEventListener('mousedown', outside));
</script>

<style scoped>
.vi { position: relative; width: 100%; }
.vbtn { cursor: pointer; font-family: Consolas, monospace; font-size: 11px; color: #a8730f; background: #fbf4e6; border-radius: 4px; padding: 0 4px; line-height: 18px; user-select: none; }
.vbtn.abs { position: absolute; right: 6px; top: 4px; z-index: 2; }
.vi:has(.vbtn.abs) :deep(textarea) { padding-right: 34px; }
.mono :deep(textarea), .mono :deep(input) { font-family: Consolas, monospace; font-size: 12px; }
.pick { position: absolute; z-index: 3000; left: 0; right: 0; top: 100%; margin-top: 3px; background: #fff; border: 1px solid #dfe4e8; border-radius: 8px; box-shadow: 0 8px 24px rgba(0, 0, 0, .14); padding: 6px; }
.lst { max-height: 260px; overflow: auto; margin-top: 4px; }
.g { font-size: 11.5px; color: #8a5a07; padding: 4px 4px 2px; font-weight: 600; }
.it { display: flex; gap: 8px; align-items: center; padding: 3px 6px; border-radius: 5px; cursor: pointer; font-size: 12px; }
.it:hover { background: #fbf4e6; } .it code { font-size: 11.5px; color: #22578a; } .it span { color: #6b7680; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
