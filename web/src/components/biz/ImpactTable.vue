<template>
  <el-table :data="shown" size="small" data-test="impact-table" :row-class-name="(r: any) => (r.row.affected ? 'aff' : '')"
    v-ai-rows="{ rows: () => shown, item: (r: any) => ({ kind: 'row', entity: 'work_order', id: r.work_order, label: $t('impactTable.n0Completes12', [r.code, r.new_end, delta(r)]), data: { [$t('common.workOrder')]: r.code, [$t('impactTable.oldCompletion')]: r.old_end, [$t('impactTable.newCompletion')]: r.new_end, [$t('impactTable.changeMin')]: r.delta_min, [$t('common.dueDate')]: r.due, [$t('common.late')]: r.late, [$t('common.reason')]: r.reasons.map((x: any) => x.text) } }) }">
    <el-table-column :label="$t('common.workOrder')" width="140"><template #default="{ row }"><b class="hm-mono">{{ row.code }}</b><div class="muted small">{{ $t('impactTable.n0Priority1', [row.customer, row.priority]) }}</div></template></el-table-column>
    <el-table-column :label="$t('impactTable.oldEstCompletion')" width="130"><template #default="{ row }"><span class="hm-num">{{ (row.old_end || '—').slice(5, 16) }}</span></template></el-table-column>
    <el-table-column :label="$t('impactTable.newEstCompletion')" width="130"><template #default="{ row }"><span class="hm-num" :class="{ bad: row.late }">{{ (row.new_end || '—').slice(5, 16) }}</span></template></el-table-column>
    <el-table-column :label="$t('impactTable.change')" width="100" align="right"><template #default="{ row }"><span class="hm-num" :class="{ bad: (row.delta_min || 0) > 0, good: (row.delta_min || 0) < 0 }">{{ delta(row) }}</span></template></el-table-column>
    <el-table-column :label="$t('common.dueDate')" width="110"><template #default="{ row }"><span class="hm-num">{{ (row.due || '—').slice(5, 16) }}</span><HmTag v-if="row.late" tone="bad">{{ $t('common.late') }}</HmTag></template></el-table-column>
    <el-table-column :label="$t('common.reason')" min-width="240"><template #default="{ row }">
      <div class="rs"><span v-for="(x, i) in row.reasons" :key="i" class="r" :class="'sev-' + x.severity" :title="x.text"><b>{{ x.label }}</b>{{ x.text }}</span>
        <span v-if="!row.reasons.length" class="muted">—</span></div>
    </template></el-table-column>
  </el-table>
  <div v-if="hidden" class="more"><el-button link type="primary" @click="all = !all">{{ all ? $t('impactTable.affectedOnly') : $t('impactTable.n0MoreWorkOrdersAre', [hidden]) }}</el-button></div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// Reschedule impact table: affected work orders, completion time changes and reasons (shortage / equipment downtime / no qualified operator / mold in use / changeover …).
import { computed, ref } from 'vue';

const props = defineProps<{ rows: any[] }>();
const all = ref(false);
const shown = computed(() => (all.value ? props.rows : props.rows.filter((r) => r.affected)));
const hidden = computed(() => props.rows.length - props.rows.filter((r) => r.affected).length);
const delta = (r: any) => (r.delta_min === null || r.delta_min === undefined ? $t('impactTable.newlyScheduled') : r.delta_min === 0 ? $t('impactTable.unchanged')
  : `${r.delta_min > 0 ? '+' : '−'}${Math.abs(r.delta_min) >= 120 ? (Math.abs(r.delta_min) / 60).toFixed(1) + ' h' : Math.abs(r.delta_min) + ' min'}`);
</script>

<style scoped>
.rs { display: flex; flex-direction: column; gap: 3px; }
.r { display: block; white-space: normal; word-break: break-word; line-height: 1.5; font-size: var(--hm-fs-2xs); padding: 2px 6px; border-left: 3px solid; background: var(--hm-surface-2); border-radius: 2px; }
.r b { margin-right: 6px; } .r.sev-error { border-color: var(--hm-fault); } .r.sev-warn { border-color: var(--hm-idle); } .r.sev-info { border-color: var(--hm-setup); }
.bad { color: var(--hm-fault-ink); } .good { color: var(--hm-run-ink); }
.muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-2xs); }
.more { padding: 6px 0; }
:deep(.aff) { background: #fffdf6; }
</style>
