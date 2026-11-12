<template>
  <div class="hm-dt" :style="{ maxHeight: maxHeight ? (typeof maxHeight === 'number' ? maxHeight + 'px' : maxHeight) : undefined }">
    <table class="hm-table" :class="{ dense }">
      <thead>
        <tr>
          <th v-for="c in columns" :key="c.key" :class="{ num: c.num, sortable: c.sortable !== false && sortable }" :style="{ width: c.width ? c.width + 'px' : undefined, textAlign: c.align }"
            :aria-sort="sortKey === c.key ? (sortDir > 0 ? 'ascending' : 'descending') : undefined" @click="toggleSort(c)">
            {{ c.label }}<span v-if="sortKey === c.key" class="si">{{ sortDir > 0 ? '▲' : '▼' }}</span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(r, i) in sorted" :key="rowKey ? r[rowKey] : i" :class="{ clickable }" v-ai="aiOf(r, i)" @click="$emit('row-click', r)">
          <td v-for="c in columns" :key="c.key" :class="{ num: c.num }" :style="{ textAlign: c.align }">
            <slot :name="'cell-' + c.key" :row="r" :value="r[c.key]">{{ cellText(c, r) }}</slot>
          </td>
        </tr>
        <tr v-if="!rows.length && !loading"><td :colspan="columns.length" class="empty"><HmEmpty compact :plain="emptyPlain" :title="emptyText" /></td></tr>
      </tbody>
    </table>
    <HmSkeleton v-if="loading && !rows.length" variant="table" :rows="5" style="padding:12px" />
  </div>
</template>

<script setup lang="ts">
import { $t, intlLocale } from '../../i18n';
// Dense data table: sticky header, click header to sort (local), numbers right-aligned monospace, v-ai on every row (Ctrl+click / right-click / hover toolbar).
// columns: [{ key, label, num?, unit?, digits?, format?, width?, align?, sortable? }]
// ai: (row, index) => AiItem | null; if omitted, auto-generated from entity as { kind:'row', entity, id: row.id, label }; false disables
import { computed, ref } from 'vue';
import { fmtBy } from '../format';
import HmEmpty from './HmEmpty.vue';
import HmSkeleton from './HmSkeleton.vue';

export interface HmColumn { key: string; label: string; num?: boolean; unit?: string; digits?: number; format?: string; width?: number; align?: 'left' | 'right' | 'center'; sortable?: boolean }
const props = withDefaults(defineProps<{ columns: HmColumn[]; rows: any[]; rowKey?: string; maxHeight?: number | string; dense?: boolean; sortable?: boolean;
  entity?: string; ai?: ((row: any, i: number) => any) | false; rowLabel?: (row: any) => string; loading?: boolean; emptyText?: string; emptyPlain?: boolean; clickable?: boolean }>(),
  { sortable: true, emptyText: $t('hmDataTable.noData'), dense: true, clickable: false, ai: undefined });
defineEmits<{ 'row-click': [row: any] }>();

const sortKey = ref(''), sortDir = ref(1);
function toggleSort(c: HmColumn) {
  if (!props.sortable || c.sortable === false) return;
  if (sortKey.value === c.key) { if (sortDir.value > 0) sortDir.value = -1; else { sortKey.value = ''; sortDir.value = 1; } } else { sortKey.value = c.key; sortDir.value = 1; }
}
const sorted = computed(() => {
  if (!sortKey.value) return props.rows;
  const k = sortKey.value, d = sortDir.value;
  return [...props.rows].sort((a, b) => {
    const x = a[k], y = b[k];
    if (x === y) return 0; if (x === null || x === undefined) return 1; if (y === null || y === undefined) return -1;
    return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), intlLocale)) * d;
  });
});
const cellText = (c: HmColumn, r: any) => {
  const v = r[c.key];
  if (c.num || c.format) return fmtBy(v, c.format || 'num', c.digits) + (c.unit && v !== null && v !== undefined ? ' ' + c.unit : '');
  return v === null || v === undefined || v === '' ? '—' : v;
};
function aiOf(r: any, i: number) {
  if (props.ai === false) return null;
  if (typeof props.ai === 'function') return props.ai(r, i);
  const label = props.rowLabel ? props.rowLabel(r) : r.code || r.sn || r.name || $t('hmDataTable.row0', [i + 1]);
  const data: Record<string, any> = {};
  for (const c of props.columns) data[c.label] = r[c.key];
  return { kind: 'row', entity: props.entity, id: r.id, label, data, sn: r.sn };
}
</script>

<style scoped>
.hm-dt { overflow: auto; position: relative; }
.empty { padding: 0 !important; }
.si { font-size: 9px; margin-left: 4px; color: var(--hm-primary); }
.hm-table.dense td { padding-top: 5px; padding-bottom: 5px; }
</style>
