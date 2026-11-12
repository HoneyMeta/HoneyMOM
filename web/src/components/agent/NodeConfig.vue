<template>
  <div class="nc" v-if="t">
    <div class="row2"><el-input v-model="data.name" size="small" :placeholder="$t('nodeConfig.nodeName')" @input="changed" /><code class="nid" :title="$t('nodeConfig.useThisIdToReference')">{{ nodeId }}</code></div>
    <p class="desc">{{ t.desc }}</p>
    <div v-for="s in main" :key="s.key" class="f">
      <label>{{ s.label }}<i v-if="s.required" class="req">*</i></label>
      <Field :s="s" />
      <div v-if="s.help" class="help">{{ s.help }}</div>
    </div>
    <el-collapse v-if="adv.length" class="adv">
      <el-collapse-item :title="$t('nodeConfig.retriesErrorRoutingAndLimits')" name="adv">
        <div v-for="s in adv" :key="s.key" class="f"><label>{{ s.label }}</label><Field :s="s" /><div v-if="s.help" class="help">{{ s.help }}</div></div>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
import { computed, defineComponent, h, resolveComponent } from 'vue';
import { shown } from '../../agentlib';
import VarInput from './VarInput.vue';

const props = defineProps<{ nodeId: string; data: any; meta: any; vars: any[]; nodeOptions: { id: string; name: string }[] }>();
const emit = defineEmits(['changed']);
const t = computed(() => props.meta?.types[props.data.type]);
const P = computed(() => props.data.params);
const all = computed(() => [...(t.value?.params || []), ...(t.value?.common || [])].filter((s: any) => shown(s, withDefaults())));
const main = computed(() => all.value.filter((s: any) => s.group !== 'adv'));
const adv = computed(() => all.value.filter((s: any) => s.group === 'adv'));
function withDefaults() {
  const o: any = {};
  for (const s of [...(t.value?.params || []), ...(t.value?.common || [])]) o[s.key] = props.data.params[s.key] ?? s.default;
  return o;
}
function changed() { emit('changed'); }
const OP_OPTS = [['==', $t('nodeConfig.equals')], ['!=', $t('nodeConfig.doesNotEqual')], ['>', $t('nodeConfig.greaterThan')], ['>=', $t('nodeConfig.greaterThanOrEqualTo')], ['<', $t('nodeConfig.lessThan')], ['<=', $t('nodeConfig.lessThanOrEqualTo')], ['contains', $t('nodeConfig.contains')], ['not_contains', $t('nodeConfig.doesNotContain')], ['empty', $t('nodeConfig.isEmpty')], ['not_empty', $t('nodeConfig.isNotEmpty')]];
const FT = [['text', $t('common.text')], ['number', $t('nodeConfig.number')], ['textarea', $t('nodeConfig.longText')]];
function uid(prefix: string, list: any[]) { let n = 1; while (list.some((x) => x.id === prefix + n)) n++; return prefix + n; }

// Render by parameter type (render function, to avoid one file per type)
const Field = defineComponent({
  props: { s: { type: Object, required: true } },
  setup(fp) {
    const ElInput = resolveComponent('ElInput'), ElInputNumber = resolveComponent('ElInputNumber'), ElSwitch = resolveComponent('ElSwitch'), ElSelect = resolveComponent('ElSelect'),
      ElOption = resolveComponent('ElOption'), ElButton = resolveComponent('ElButton');
    return () => {
      const s: any = fp.s;
      const p = P.value;
      const k = s.key;
      if (p[k] === undefined && s.default !== undefined) p[k] = JSON.parse(JSON.stringify(s.default));
      const set = (v: any) => { p[k] = v; changed(); };
      const vi = (val: any, on: (v: any) => void, extra: any = {}) => h(VarInput, { modelValue: val, 'onUpdate:modelValue': on, vars: props.vars, ...extra });
      const rm = (arr: any[], i: number) => h(ElButton, { link: true, size: 'small', type: 'danger', onClick: () => { arr.splice(i, 1); changed(); } }, () => $t('nodeConfig.del'));
      switch (s.type) {
        case 'text': return s.vars ? vi(p[k], set) : h(ElInput, { modelValue: p[k], 'onUpdate:modelValue': set, size: 'small' });
        case 'textarea': return vi(p[k], set, { multi: true, rows: 3 });
        case 'json': return vi(typeof p[k] === 'string' ? p[k] : JSON.stringify(p[k] ?? '', null, 1), set, { multi: true, rows: 4, mono: true });
        case 'number': return h(ElInputNumber, { modelValue: p[k] === '' ? undefined : p[k], 'onUpdate:modelValue': set, size: 'small', min: s.min, max: s.max, controlsPosition: 'right', style: 'width:160px' });
        case 'bool': return h(ElSwitch, { modelValue: p[k] !== false && p[k] !== undefined ? !!p[k] : false, 'onUpdate:modelValue': set });
        case 'select': return h(ElSelect, { modelValue: p[k], 'onUpdate:modelValue': set, size: 'small', style: 'width:100%' },
          () => (s.options || []).map((o: any) => h(ElOption, { value: o.value, label: o.label })));
        case 'multiselect': return h(ElSelect, { modelValue: p[k] || [], 'onUpdate:modelValue': set, size: 'small', multiple: true, style: 'width:100%', placeholder: $t('nodeConfig.noneSelectedAll') },
          () => (s.options || []).map((o: any) => h(ElOption, { value: o.value, label: o.label })));
        case 'node': return h(ElSelect, { modelValue: p[k], 'onUpdate:modelValue': set, size: 'small', style: 'width:100%', clearable: true },
          () => props.nodeOptions.filter((o) => o.id !== props.nodeId).map((o) => h(ElOption, { value: o.id, label: $t('common.n01', [o.name, o.id]) })));
        case 'kv': {
          const arr = (p[k] ||= []);
          return h('div', { class: 'lst' }, [...arr.map((a: any, i: number) => h('div', { class: 'kv' }, [
            h(ElInput, { modelValue: a.key, 'onUpdate:modelValue': (v: any) => { a.key = v; changed(); }, size: 'small', placeholder: $t('nodeConfig.name'), style: 'width:90px;flex:none' }),
            s.vars ? vi(a.value, (v: any) => { a.value = v; changed(); }) : h(ElInput, { modelValue: a.value, 'onUpdate:modelValue': (v: any) => { a.value = v; changed(); }, size: 'small' }),
            rm(arr, i)])), h(ElButton, { size: 'small', link: true, type: 'primary', onClick: () => { arr.push({ key: '', value: '' }); changed(); } }, () => $t('nodeConfig.add'))]);
        }
        case 'fields': {
          const arr = (p[k] ||= []);
          return h('div', { class: 'lst' }, [...arr.map((f: any, i: number) => h('div', { class: 'fld' }, [
            h('div', { class: 'kv' }, [
              h(ElInput, { modelValue: f.key, 'onUpdate:modelValue': (v: any) => { f.key = v.replace(/[^\w]/g, ''); changed(); }, size: 'small', placeholder: $t('nodeConfig.fieldNameEnglish'), style: 'width:110px;flex:none' }),
              h(ElInput, { modelValue: f.label, 'onUpdate:modelValue': (v: any) => { f.label = v; changed(); }, size: 'small', placeholder: $t('nodeConfig.displayName') }),
              h(ElSelect, { modelValue: f.type || 'text', 'onUpdate:modelValue': (v: any) => { f.type = v; changed(); }, size: 'small', style: 'width:84px;flex:none' },
                () => FT.map(([v, l]) => h(ElOption, { value: v, label: l }))), rm(arr, i)]),
            ...(props.data.type !== 'begin' ? [h(ElInput, { modelValue: f.description, 'onUpdate:modelValue': (v: any) => { f.description = v; changed(); }, size: 'small', placeholder: $t('nodeConfig.descriptionForTheLlm') })] : []),
            vi(f.default, (v: any) => { f.default = v; changed(); }, { placeholder: $t('nodeConfig.defaultUsedWithoutAnLlm') })])),
          h(ElButton, { size: 'small', link: true, type: 'primary', onClick: () => { arr.push({ key: 'f' + (arr.length + 1), label: '', type: 'text', description: '', default: '' }); changed(); } }, () => $t('nodeConfig.addField'))]);
        }
        case 'categories': {
          const arr = (p[k] ||= []);
          return h('div', { class: 'lst' }, [...arr.map((c: any, i: number) => h('div', { class: 'fld' }, [
            h('div', { class: 'kv' }, [h('code', { class: 'cid' }, c.id), h(ElInput, { modelValue: c.name, 'onUpdate:modelValue': (v: any) => { c.name = v; changed(); }, size: 'small', placeholder: $t('nodeConfig.categoryName') }), rm(arr, i)]),
            h(ElInput, { modelValue: c.description, 'onUpdate:modelValue': (v: any) => { c.description = v; changed(); }, size: 'small', placeholder: $t('nodeConfig.whatCountsAsThisCategory') }),
            h(ElInput, { modelValue: c.keywords, 'onUpdate:modelValue': (v: any) => { c.keywords = v; changed(); }, size: 'small', placeholder: $t('nodeConfig.keywordsCommaSeparatedUsedWithout') })])),
          h(ElButton, { size: 'small', link: true, type: 'primary', onClick: () => { arr.push({ id: uid('c', arr), name: $t('nodeConfig.newCategory'), description: '', keywords: '' }); changed(); } }, () => $t('nodeConfig.addCategory'))]);
        }
        case 'cases': {
          const arr = (p[k] ||= []);
          return h('div', { class: 'lst' }, [...arr.map((c: any, i: number) => h('div', { class: 'fld case' }, [
            h('div', { class: 'kv' }, [h('code', { class: 'cid' }, c.id), h(ElInput, { modelValue: c.name, 'onUpdate:modelValue': (v: any) => { c.name = v; changed(); }, size: 'small', placeholder: $t('nodeConfig.branchName') }),
              h(ElSelect, { modelValue: c.logic || 'and', 'onUpdate:modelValue': (v: any) => { c.logic = v; changed(); }, size: 'small', style: 'width:90px;flex:none' },
                () => [h(ElOption, { value: 'and', label: $t('nodeConfig.allConditionsMet') }), h(ElOption, { value: 'or', label: $t('nodeConfig.anyConditionMet') })]), rm(arr, i)]),
            ...(c.conditions ||= []).map((x: any, j: number) => h('div', { class: 'cond' }, [
              vi(x.left, (v: any) => { x.left = v; changed(); }, { placeholder: $t('nodeConfig.leftVariable') }),
              h(ElSelect, { modelValue: x.op, 'onUpdate:modelValue': (v: any) => { x.op = v; changed(); }, size: 'small', style: 'width:96px;flex:none' }, () => OP_OPTS.map(([v, l]) => h(ElOption, { value: v, label: l }))),
              ...(['empty', 'not_empty'].includes(x.op) ? [] : [vi(x.right, (v: any) => { x.right = v; changed(); }, { placeholder: $t('nodeConfig.right') })]), rm(c.conditions, j)])),
            h(ElButton, { size: 'small', link: true, onClick: () => { c.conditions.push({ left: '', op: '==', right: '' }); changed(); } }, () => $t('nodeConfig.condition'))])),
          h(ElButton, { size: 'small', link: true, type: 'primary', onClick: () => { arr.push({ id: uid('case', arr), name: $t('nodeConfig.branch') + (arr.length + 1), logic: 'and', conditions: [{ left: '', op: '==', right: '' }] }); changed(); } }, () => $t('nodeConfig.addBranch'))]);
        }
        default: return h(ElInput, { modelValue: p[k], 'onUpdate:modelValue': set, size: 'small' });
      }
    };
  },
});
</script>

<style scoped>
.nc { font-size: 13px; }
.row2 { display: flex; gap: 6px; align-items: center; } .nid { font-size: 11.5px; color: #6b7680; background: #f3f5f7; border-radius: 4px; padding: 2px 6px; flex: none; }
.desc { color: #6b7680; font-size: 12px; margin: 6px 0 10px; line-height: 1.5; }
.f { margin-bottom: 10px; } .f > label { display: block; font-size: 12.5px; color: #3a434c; margin-bottom: 3px; font-weight: 500; } .req { color: #c83a3a; font-style: normal; margin-left: 2px; }
.help { font-size: 11.5px; color: #8b959e; margin-top: 2px; }
.adv { margin-top: 6px; } .adv :deep(.el-collapse-item__header) { font-size: 12.5px; height: 34px; }
:deep(.lst) { display: flex; flex-direction: column; gap: 6px; }
:deep(.kv) { display: flex; gap: 4px; align-items: center; }
:deep(.fld) { border: 1px dashed #dfe4e8; border-radius: 8px; padding: 6px; display: flex; flex-direction: column; gap: 4px; }
:deep(.cond) { display: flex; gap: 4px; align-items: center; padding-left: 8px; }
:deep(.cid) { font-size: 11px; color: #8a5a07; background: #fbf4e6; border-radius: 4px; padding: 1px 4px; flex: none; }
</style>
