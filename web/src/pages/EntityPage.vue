<template>
  <div class="page" v-if="E">
    <HmPageHead :title="E.label" :sub="`${E.module_label || E.module}${E.internal ? $t('entityPage.recordedAutomatically') : ''}`">
      <template #meta><HmTag tone="neutral" class="hm-num" data-test="ent-total">{{ $t('entityPage.n0Records', [total]) }}</HmTag></template>
    </HmPageHead>
    <HmToolbar data-tour="ent-toolbar">
      <el-input v-model="q" :placeholder="$t('entityPage.searchNoName')" clearable class="ent-q" data-tour="ent-search" @change="load(1)"><template #prefix><el-icon><Search /></el-icon></template></el-input>
      <el-select v-for="f in enumFilters" :key="f.name" v-model="filters[f.name]" :placeholder="f.label" clearable class="ent-f" data-tour="ent-filters" @change="load(1)">
        <el-option v-for="(v, k) in f.enum" :key="k" :label="v" :value="k" />
      </el-select>
      <el-button v-if="hasFilter" link type="primary" @click="clearFilters">{{ $t('entityPage.clearFilters') }}</el-button>
      <template #right>
        <el-tooltip :content="$t('common.refresh')" placement="bottom"><el-button :aria-label="$t('common.refresh')" @click="load()"><el-icon><Refresh /></el-icon></el-button></el-tooltip>
        <el-button data-tour="ent-export" @click="exportCsv"><el-icon><Download /></el-icon>{{ $t('common.export') }}</el-button>
        <el-button v-if="!E.internal && canDo('create')" type="primary" data-tour="ent-add" @click="openForm()"><el-icon><Plus /></el-icon>{{ $t('entityPage.new') }}</el-button>
      </template>
    </HmToolbar>
    <div class="hm-card ent-card" data-tour="ent-table">
      <el-table :data="rows" v-loading="loading" v-ai-rows="aiRows" stripe highlight-current-row @row-click="openDetail" @sort-change="onSort" style="width:100%" :height="tableH">
        <template #empty><HmEmpty compact :icon="hasFilter ? 'search' : 'data'" :title="hasFilter ? $t('adminAudit.noMatchingRecords') : $t('entityPage.no0Yet', [E.label])" :desc="hasFilter ? $t('entityPage.tryAnotherKeywordOrClear') : (!E.internal && canDo('create') ? $t('entityPage.clickNewAtTheTop') : '')" /></template>
        <el-table-column v-for="f in cols" :key="f.name" :prop="f.name" :label="f.label" :sortable="'custom'" :min-width="colWidth(f)" :align="f.t === 'int' || f.t === 'float' ? 'right' : undefined"
          :class-name="f.t === 'int' || f.t === 'float' ? 'is-num' : ''" :show-overflow-tooltip="f.t === 'str' || f.t === 'ref' || f.t === undefined">
          <template #default="{ row }"><Cell :f="f" :row="row" /></template>
        </el-table-column>
      </el-table>
      <div class="ent-pg">
        <el-pagination v-model:current-page="page" v-model:page-size="size" :total="total" :page-sizes="[20, 50, 100, 200]" layout="total, sizes, prev, pager, next"
          @current-change="load()" @size-change="load(1)" />
      </div>
    </div>

    <!-- Detail -->
    <el-drawer v-model="drawer" :size="drawerSize" :title="detailTitle" destroy-on-close>
      <template v-if="detail">
        <div class="acts" data-tour="ent-actions">
          <el-button v-for="(lab, a) in acts(E.name, detail.item)" :key="a" type="primary" plain size="small" @click="doAction(E.name, detail.item, a as string, lab)" :disabled="!can(entPerm(E.name, a as string))">{{ lab }}</el-button>
          <el-button v-if="E.name === 'insp_record' && canDo('update')" size="small" type="primary" plain data-tour="ent-insp-values" @click="openValues">{{ $t('entityPage.enterInspectionValues') }}</el-button>
          <el-button v-if="E.name === 'part' || (detail.item?.sn && E.name !== 'part')" size="small" @click="router.push('/trace?sn=' + detail.item?.sn)">{{ $t('common.traceability') }}</el-button>
          <el-button v-if="E.name === 'work_order'" size="small" @click="router.push('/production/gantt')">{{ $t('entityPage.schedulingGantt') }}</el-button>
          <el-button v-if="E.name === 'equipment'" size="small" @click="router.push('/equipment/oee?eid=' + detail.item.id)">OEE</el-button>
          <el-button v-if="aiOn('ask')" size="small" @click="askAI" data-test="ask-ai"><el-icon><MagicStick /></el-icon>{{ $t('entityPage.askAi') }}</el-button>
          <span class="grow" />
          <el-button v-if="!E.internal && canDo('update')" size="small" @click="openForm(detail.item)"><el-icon><Edit /></el-icon>{{ $t('entityPage.edit') }}</el-button>
          <el-popconfirm v-if="!E.internal && canDo('delete')" :title="$t('entityPage.deleteThisRecord')" @confirm="remove(detail.item)">
            <template #reference><el-button size="small" type="danger" plain><el-icon><Delete /></el-icon></el-button></template>
          </el-popconfirm>
        </div>
        <el-descriptions :column="descCols" border size="small">
          <el-descriptions-item v-for="f in E.fields.filter((x: any) => x.name !== 'password_hash')" :key="f.name" :label="f.label" :span="f.t === 'text' || f.t === 'json' ? descCols : 1">
            <Cell :f="f" :row="detail.item" full />
          </el-descriptions-item>
        </el-descriptions>
        <div data-tour="ent-children"><template v-for="(items, child) in detail.children" :key="child">
          <h4 class="child-h">{{ ent(child as string)?.label }} <small>{{ $t('common.n03', [items.length]) }}</small>
            <el-button v-if="!ent(child as string)?.internal && can(entPerm(child as string, 'create'))" size="small" link type="primary" @click="addChild(child as string)">{{ $t('entityPage.new2') }}</el-button></h4>
          <el-table :data="items" size="small" border max-height="360">
            <el-table-column v-for="f in childCols(child as string)" :key="f.name" :label="f.label" :min-width="colWidth(f)">
              <template #default="{ row }"><Cell :f="f" :row="row" /></template>
            </el-table-column>
            <el-table-column v-if="Object.keys(ent(child as string)?.actions || {}).length || !ent(child as string)?.internal" label="" width="140" fixed="right">
              <template #default="{ row }">
                <el-button v-for="(lab, a) in acts(child as string, row)" v-show="can(entPerm(child as string, a as string))" :key="a" link type="primary" size="small" @click="doAction(child as string, row, a as string, lab)">{{ lab }}</el-button>
                <el-button v-if="!ent(child as string)?.internal && can(entPerm(child as string, 'update'))" link size="small" @click="openForm(row, child as string)">{{ $t('common.edit') }}</el-button>
              </template>
            </el-table-column>
          </el-table>
        </template></div>
      </template>
    </el-drawer>

    <!-- Form -->
    <el-dialog v-model="formOpen" :title="(form.id ? $t('common.edit') : $t('common.add')) + (ent(formEntity)?.label || '')" width="min(720px, 96vw)" destroy-on-close>
      <el-form label-width="110px" :model="form">
        <template v-for="g in formGroups" :key="g.title"><div v-if="g.title && formGroups.length > 1" class="form-group-t">{{ g.title }}</div><div class="form-grid">
          <el-form-item v-for="f in g.fields" :key="f.name" :label="f.label" :required="f.req" :class="{ wide: f.t === 'text' || f.t === 'json' }" :data-field="f.name">
            <el-input v-if="f.auto && !form.id" disabled :placeholder="$t('entityPage.autoGenerated')" />
            <span v-else-if="f.roRole" class="muted">{{ form[f.name + '__label'] || form[f.name] || '—' }}</span>
            <el-select v-else-if="f.t === 'enum'" v-model="form[f.name]" clearable style="width:100%"><el-option v-for="(v, k) in f.enum" :key="k" :label="v" :value="k" /></el-select>
            <RefSelect v-else-if="f.t === 'ref'" v-model="form[f.name]" :entity="f.ref" :label="form[f.name + '__label']" />
            <el-input-number v-else-if="f.t === 'int' || f.t === 'float'" v-model="form[f.name]" :step="f.t === 'int' ? 1 : 0.01" :controls="false" style="width:100%" />
            <el-switch v-else-if="f.t === 'bool'" v-model="form[f.name]" />
            <el-date-picker v-else-if="f.t === 'date'" v-model="form[f.name]" type="date" value-format="YYYY-MM-DD" style="width:100%" />
            <el-date-picker v-else-if="f.t === 'datetime'" v-model="form[f.name]" type="datetime" value-format="YYYY-MM-DD HH:mm:ss" style="width:100%" />
            <el-input v-else-if="f.t === 'text' || f.t === 'json'" v-model="form[f.name]" type="textarea" :rows="3" />
            <CfFile v-else-if="f.t === 'file'" v-model="form[f.name]" />
            <el-input v-else v-model="form[f.name]" :placeholder="f.hint" />
          </el-form-item>
        </div></template>
      </el-form>
      <template #footer><el-button @click="formOpen = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" @click="save">{{ $t('common.save') }}</el-button></template>
    </el-dialog>

    <!-- Action parameters -->
    <el-dialog v-model="actOpen" :title="act.label" width="min(480px, 96vw)">
      <el-form label-width="96px">
        <el-form-item v-for="f in act.fields" :key="f.name" :label="f.label">
          <RefSelect v-if="f.t === 'ref'" v-model="act.body[f.name]" :entity="f.ref" />
          <el-select v-else-if="f.t === 'enum'" v-model="act.body[f.name]" style="width:100%"><el-option v-for="(v, k) in f.enum" :key="k" :label="v" :value="k" /></el-select>
          <el-input v-else v-model="act.body[f.name]" :type="f.t === 'text' ? 'textarea' : 'text'" />
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="actOpen = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :loading="act.busy" @click="runAction">{{ $t('common.ok') }}</el-button></template>
    </el-dialog>

    <!-- Inspection value entry -->
    <el-dialog v-model="valOpen" :title="$t('entityPage.enterInspectionValuesJudgedAutomatic')" width="min(820px, 96vw)">
      <el-table :data="valItems" size="small">
        <el-table-column prop="name" :label="$t('common.item')" /><el-table-column prop="std" :label="$t('common.standard')" />
        <el-table-column :label="$t('entityPage.measured')" width="160"><template #default="{ row }">
          <el-input-number v-if="row.lo != null || row.hi != null" v-model="row.value" :controls="false" style="width:130px" />
          <el-select v-else v-model="row.ok" style="width:130px"><el-option :value="true" :label="$t('common.good')" /><el-option :value="false" :label="$t('common.ng')" /></el-select>
        </template></el-table-column>
        <el-table-column :label="$t('common.gauge')" min-width="160"><template #default="{ row }">
          <InspGaugeSelect v-if="row.gauge_type || row.lo != null || row.hi != null" v-model="row.gauge" :item="row" size="small" />
        </template></el-table-column>
      </el-table>
      <template #footer><el-button type="primary" @click="saveValues">{{ $t('common.saveJudge') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { computed, defineComponent, h, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { aiOn } from '../ai';
import { askAssistant, canWrite, del, ent, get, post, put, setAssistantRecord, tok } from '../api';
import { can, entPerm } from '../perm';
import RefSelect from '../components/RefSelect.vue';
import CfFile from '../impl/CfFile.vue';                    // M3: file type for custom fields
import { layoutGroups, missingRequired } from '../impl/forms';    // M3: form config (groups, role-based required / hidden / read-only)
import InspGaugeSelect from '../components/exec/InspGaugeSelect.vue';
import './aiActions';

const props = defineProps<{ entity: string }>();
const route = useRoute(), router = useRouter();
const E = computed(() => ent(props.entity));
const writable = computed(() => E.value && canWrite(E.value.module));
// 5.0: show buttons by entity permission (create / edit / delete / each action), consistent with backend crud checks
const canDo = (op: string) => !!E.value && can(entPerm(E.value.name, op));
const rows = ref<any[]>([]), total = ref(0), page = ref(1), size = ref(20), loading = ref(false), q = ref('');
const filters = reactive<Record<string, any>>({}), sort = ref('');
const drawer = ref(false), detail = ref<any>(null);
const formOpen = ref(false), form = reactive<any>({}), formEntity = ref('');
const actOpen = ref(false), act = reactive<any>({ fields: [], body: {}, label: '', entity: '', row: null, name: '', busy: false });
const valOpen = ref(false), valItems = ref<any[]>([]);
const drawerSize = innerWidth < 800 ? '100%' : '62%';
// Table fills the remaining height (shell + page header + toolbar + pagination, varies with density --hm-scale)
const tableH = 'max(320px, calc(100vh - 318px * var(--hm-scale)))';
const hasFilter = computed(() => !!q.value || Object.values(filters).some((v) => v !== '' && v !== null && v !== undefined));
function clearFilters() { q.value = ''; for (const k of Object.keys(filters)) filters[k] = ''; load(1); }
const descCols = innerWidth < 800 ? 1 : 2;

const STATUS_NAMES = ['status', 'result', 'disposition', 'state'];
// Every table row can be handed to AI via Ctrl+click / right-click / hover toolbar (see design/MIGRATION.md)
const aiRows = computed(() => ({
  entity: props.entity, rows: () => rows.value,
  item: (r: any) => ({ kind: 'row', entity: props.entity, id: r.id, label: `${E.value?.label || ''} ${labelOf(r)}`, sn: r.sn || undefined,
    heat: props.entity === 'heat' ? r.code : undefined, link: `/e/${props.entity}?id=${r.id}`,
    data: { ...Object.fromEntries(cols.value.map((f: any) => [f.label, f.t === 'ref' ? r[f.name + '__label'] : f.t === 'enum' ? (f.enum?.[r[f.name]] ?? r[f.name]) : r[f.name]])),
      id: r.id, ...(props.entity === 'equipment' ? { equipment_id: r.id } : r.equipment ? { equipment_id: r.equipment } : {}), ...(r.part ? { part: r.part } : {}) } }),
}));
const cols = computed(() => {        // At most 12 system field columns + 6 custom field columns (those shown in the list)
  const fs = E.value?.fields || [], ok = (f: any) => f.list !== false && !['text', 'json', 'file'].includes(f.t);
  return [...fs.filter((f: any) => !f.cf && ok(f)).slice(0, 12), ...fs.filter((f: any) => f.cf && ok(f)).slice(0, 6)];
});
const enumFilters = computed(() => [...(E.value?.fields || []).filter((f: any) => f.t === 'enum' && !f.cf).sort((a: any, b: any) => +STATUS_NAMES.includes(b.name) - +STATUS_NAMES.includes(a.name)).slice(0, 3),
  ...(E.value?.fields || []).filter((f: any) => f.cf && f.filter && f.t === 'enum')]);
const formFields = computed(() => (ent(formEntity.value)?.fields || []).filter((f: any) => !f.ro));
const formGroups = computed(() => layoutGroups(ent(formEntity.value), formFields.value));
const detailTitle = computed(() => detail.value?.item ? $t('common.n012', [E.value?.label || '', labelOf(detail.value.item)]) : '');
const labelOf = (r: any) => !r ? '' : r.code ? `${r.code}${r.name ? ' ' + r.name : ''}` : r.sn || r.name || r.title || r.username || '#' + r.id;
const colWidth = (f: any) => (f.t === 'datetime' ? 150 : f.t === 'ref' ? 140 : f.name === 'code' || f.name === 'sn' ? 130 : f.t === 'enum' || f.t === 'bool' ? 90 : 110);
const childCols = (c: string) => {
  const fk = (E.value?.children || []).find((x: any) => x[0] === c)?.[1];
  return (ent(c)?.fields || []).filter((f: any) => f.list !== false && !['text', 'json'].includes(f.t) && f.name !== fk).slice(0, 10);
};

const Cell = defineComponent({
  props: { f: Object, row: Object, full: Boolean },
  setup(p: any) {
    return () => {
      const f = p.f, v = p.row[f.name];
      if (v === null || v === undefined || v === '') return h('span', { class: 'muted' }, '—');
      if (f.t === 'ref') return h('span', p.row[f.name + '__label'] || '#' + v);
      if (f.t === 'enum') return STATUS_NAMES.includes(f.name) || f.name === 'level' ? h('span', { class: 'tag-st st-' + v }, f.enum?.[v] || v) : h('span', f.enum?.[v] || v);
      if (f.t === 'bool') return h('span', { class: v ? 'ok' : 'muted' }, v ? '✓' : '—');
      if (f.t === 'float') return h('span', { class: 'mono' }, Math.abs(v) >= 100 ? (+v).toFixed(1) : (+v).toFixed(Math.abs(v) < 1 ? 4 : 2).replace(/0+$/, '').replace(/\.$/, ''));
      if (f.t === 'datetime') return h('span', { class: 'mono' }, p.full ? v : String(v).slice(5, 16));
      if (f.t === 'file') return v?.token ? h('a', { href: `/api/impl/files/${v.token}?token=${tok()}`, target: '_blank', class: 'lnk', onClick: (ev: Event) => ev.stopPropagation() }, v.name) : h('span', { class: 'muted' }, '—');
      if (f.t === 'json') return h('pre', { style: 'margin:0;white-space:pre-wrap;font-size:12px' }, JSON.stringify(v, null, 1));
      if (f.t === 'text') return h('div', { class: 'md' }, String(v));
      return h('span', String(v));
    };
  },
});

async function load(p?: number) {
  if (p) page.value = p;
  loading.value = true;
  const qs = new URLSearchParams({ page: String(page.value), size: String(size.value), q: q.value, sort: sort.value });
  for (const [k, v] of Object.entries(filters)) if (v !== '' && v !== null && v !== undefined) qs.set('f_' + k, v);
  for (const [k, v] of Object.entries(route.query)) if (k.startsWith('f_') && !qs.has(k)) qs.set(k, String(v));
  try { const r = await get(`/api/e/${props.entity}?${qs}`); rows.value = r.items; total.value = r.total; } finally { loading.value = false; }
}
function onSort({ prop, order }: any) { sort.value = order ? (order === 'descending' ? '-' : '') + prop : ''; load(1); }
async function openDetail(row: any) {
  detail.value = await get(`/api/e/${props.entity}/${row.id}`); drawer.value = true;
  setAssistantRecord({ entity: props.entity, id: detail.value.item.id, label: `${E.value.label} ${labelOf(detail.value.item)}` });
}
function openForm(row?: any, entity?: string) {
  formEntity.value = entity || props.entity;
  for (const k of Object.keys(form)) delete form[k];
  const fe = ent(formEntity.value);
  if (row) Object.assign(form, row);
  else for (const f of fe.fields) if (f.default !== undefined) form[f.name] = f.default;
  formOpen.value = true;
}
function addChild(child: string) {
  const ch = E.value.children.find((c: any) => c[0] === child);
  openForm(undefined, child);
  form[ch[1]] = ch[2] ? detail.value.item[ch[2]] : detail.value.item.id;
  form[ch[1] + '__label'] = labelOf(detail.value.item);
}
async function save() {
  const e = formEntity.value;
  const miss = missingRequired(formGroups.value, form);          // Required by role in the form config
  if (miss.length) { ElMessage.warning($t('common.required') + miss.join($t('common.k42'))); return; }
  const r = form.id ? await put(`/api/e/${e}/${form.id}`, form) : await post(`/api/e/${e}`, form);
  if (r?.pending_approval) ElMessage.warning(r.msg); else ElMessage.success($t('common.saved'));          // Approval workflow: submitted for approval, takes effect only once approved
  formOpen.value = false; load();
  if (drawer.value && detail.value) detail.value = await get(`/api/e/${props.entity}/${detail.value.item.id}`);
}
async function remove(row: any) {
  await del(`/api/e/${props.entity}/${row.id}`); ElMessage.success($t('common.deleted')); drawer.value = false; load();
}

// States in which an action is available (actions not in the table are always available)
const ACTION_WHEN: Record<string, (r: any) => boolean> = {
  'work_order.release': (r) => r.status === 'draft', 'work_order.cancel': (r) => ['draft', 'released'].includes(r.status),
  'work_order.close': (r) => ['in_progress', 'completed', 'released'].includes(r.status),
  'wo_op.dispatch': (r) => r.status !== 'done',
  'ncr.rework': (r) => r.status !== 'closed', 'ncr.scrap': (r) => r.status !== 'closed', 'ncr.concession': (r) => r.status !== 'closed', 'ncr.close': (r) => r.status !== 'closed',
  'repair_order.assign': (r) => r.status === 'reported', 'repair_order.start': (r) => ['reported', 'assigned'].includes(r.status),
  'repair_order.finish': (r) => r.status === 'repairing', 'repair_order.verify': (r) => r.status === 'done',
  'nc_program.approve': (r) => r.status === 'draft', 'nc_program.release': (r) => r.status === 'approved', 'nc_program.obsolete': (r) => r.status !== 'obsolete',
  'alarm.ack': (r) => !r.ack_by, 'report_8d.ai_draft': (r) => r.status === 'draft',
  'andon.respond': (r) => r.status === 'open', 'andon.close': (r) => r.status !== 'closed',
  'material_request.issue': (r) => r.status === 'submitted', 'material_request.reject': (r) => r.status === 'submitted',
  'schedule_proposal.accept': (r) => r.status === 'pending', 'schedule_proposal.reject': (r) => r.status === 'pending',
};
function acts(entity: string, row: any) {
  const all = ent(entity)?.actions || {};
  return Object.fromEntries(Object.entries(all).filter(([a]) => (ACTION_WHEN[`${entity}.${a}`] || (() => true))(row)));
}
const ACTION_FORM: Record<string, any[]> = {
  'wo_op.dispatch': [{ name: 'assignee', label: $t('entityPage.assignTo'), t: 'ref', ref: 'employee' }],
  'repair_order.assign': [{ name: 'assignee', label: $t('entityPage.repairedBy'), t: 'ref', ref: 'employee' }],
  'repair_order.finish': [{ name: 'cause', label: $t('entityPage.failureCause'), t: 'text' }, { name: 'solution', label: $t('entityPage.fixApplied'), t: 'text' }],
  'ncr.rework': [{ name: 'remark', label: $t('entityPage.reworkInstructions') }], 'ncr.scrap': [{ name: 'remark', label: $t('entityPage.scrapNote') }], 'ncr.concession': [{ name: 'remark', label: $t('entityPage.concessionNote') }],
  'maint_plan.execute': [{ name: 'result', label: $t('common.result'), t: 'enum', enum: { ok: $t('common.normal'), abnormal: $t('common.abnormal') } }, { name: 'notes', label: $t('common.description'), t: 'text' }],
  'alarm.ack': [{ name: 'note', label: $t('entityPage.resolutionNote') }],
  'sys_user.reset_pw': [{ name: 'password', label: $t('entityPage.newPassword'), default: 'honey123' }],
  'datasource.discover': [{ name: 'minutes', label: $t('entityPage.detectionLeadTimeMin'), default: '5' }],
  'andon.close': [{ name: 'note', label: $t('common.resolution'), default: $t('common.resolved') }], 'material_request.reject': [{ name: 'note', label: $t('docControl.reasonForReturn') }],
  'tool.change': [{ name: 'reason', label: $t('common.reason'), t: 'enum', enum: { manual: $t('common.manual2'), ai: $t('entityPage.aiEarlyWarning'), broken: $t('entityPage.toolBreakage') }, default: 'manual' }],
};
async function doAction(entity: string, row: any, name: string, label: any) {
  const fields = ACTION_FORM[`${entity}.${name}`];
  Object.assign(act, { entity, row, name, label: $t('common.n012', [label, labelOf(row)]), fields: fields || [], body: {}, busy: false });
  for (const f of fields || []) if (f.default) act.body[f.name] = f.default;
  if (fields) { actOpen.value = true; return; }
  await ElMessageBox.confirm($t('entityPage.confirm0', [label]), labelOf(row));
  await runAction();
}
async function runAction() {
  act.busy = true;
  try {
    const r = await post(`/api/e/${act.entity}/${act.row.id}/do/${act.name}`, act.body);
    (r.pending_approval ? ElMessage.warning : ElMessage.success)(r.msg || $t('common.completed')); actOpen.value = false; load();
    if (detail.value) detail.value = await get(`/api/e/${props.entity}/${detail.value.item.id}`);
  } finally { act.busy = false; }
}
async function openValues() {
  const rec = detail.value.item;
  if (!rec.template) { ElMessage.warning($t('entityPage.thisInspectionHasNoTemplate')); return; }
  const t = await get(`/api/e/insp_template/${rec.template}`);
  const old = Object.fromEntries((detail.value.children.insp_value || []).map((v: any) => [v.item, v]));
  valItems.value = (t.children.insp_item || []).map((i: any) => ({ ...i, value: old[i.id]?.value ?? null, ok: old[i.id]?.ok ?? true, gauge: old[i.id]?.gauge ?? null }));
  valOpen.value = true;
}
async function saveValues() {
  const r = await post(`/api/quality/record/${detail.value.item.id}/values`, { values: valItems.value.map((i) => ({ item: i.id, value: i.value, ok: i.ok, gauge: i.gauge || null })) });
  ElMessage.success($t('entityPage.result') + (r.record === 'OK' ? $t('common.good') : $t('common.ng'))); valOpen.value = false;
  detail.value = await get(`/api/e/insp_record/${detail.value.item.id}`); load();
}
function exportCsv() {
  const qs = new URLSearchParams({ q: q.value, token: tok() });
  for (const [k, v] of Object.entries(filters)) if (v) qs.set('f_' + k, v);
  window.open(`/api/e/${props.entity}.csv?${decodeURIComponent(qs.toString())}`);
}
let keepCtx = false;
watch(drawer, (v) => { if (!v && !keepCtx) setAssistantRecord(null); keepCtx = false; });
function askAI() {
  const it = detail.value.item;
  const what = `${E.value.label} ${labelOf(it)}`;
  const prompt = props.entity === 'repair_order' ? $t('entityPage.analyzeRepairOrder0This', [labelOf(it)])
    : props.entity === 'ncr' ? $t('entityPage.analyzeNonconformance0HowOften', [labelOf(it)])
      : props.entity === 'alarm' ? $t('entityPage.whatIsThisAlarm0', [labelOf(it)])
        : props.entity === 'work_order' ? $t('entityPage.howIsWorkOrder0', [labelOf(it)])
          : props.entity === 'equipment' ? $t('entityPage.howWereOeeDowntimeAnd', [labelOf(it)])
            : $t('entityPage.reviewThis0ForMe', [what]);
  keepCtx = true;
  drawer.value = false;      // Close the detail drawer so it doesn't cover the assistant; the selected record is still passed to it
  askAssistant(prompt, { entity: props.entity, id: it.id, label: what });
}
onMounted(() => {
  for (const [k, v] of Object.entries(route.query)) if (k.startsWith('f_') && enumFilters.value.some((f: any) => 'f_' + f.name === k)) filters[k.slice(2)] = v;
  load();
  if (route.query.id) openDetail({ id: route.query.id });
  // Create a linked document: /e/<entity>?new=1&d_<field>=<value> (used by right-click actions like "Request Repair", "Start 8D")
  if (route.query.new && !E.value?.internal && canDo('create')) {
    openForm();
    for (const [k, v] of Object.entries(route.query)) if (k.startsWith('d_') && v !== '') { const f = E.value.fields.find((x: any) => x.name === k.slice(2)); if (f) form[f.name] = f.t === 'ref' || f.t === 'int' ? Number(v) : v; }
  }
});
</script>

<style scoped>
.acts { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; align-items: center; } .grow { flex: 1; }
.child-h { margin: 18px 0 6px; font-size: var(--hm-fs-md); display: flex; align-items: center; gap: 8px; } .child-h small { color: var(--hm-text-3); font-weight: 400; font-size: var(--hm-fs-xs); }
.ent-q { width: calc(220px * var(--hm-scale)); } .ent-f { width: calc(132px * var(--hm-scale)); }
.ent-card { margin-top: var(--hm-gap); padding: 0; overflow: hidden; }
.ent-card :deep(.el-table) { --el-table-border-color: var(--hm-border); }
.ent-card :deep(.el-table__inner-wrapper::before) { display: none; }
.ent-card :deep(td.is-num .cell) { font-family: var(--hm-font-num); font-variant-numeric: tabular-nums; }
.ent-pg { padding: 8px 12px; display: flex; justify-content: flex-end; border-top: 1px solid var(--hm-border); background: var(--hm-surface-2); }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; column-gap: 12px; } .form-grid .wide { grid-column: 1 / -1; }
.form-group-t { font-weight: 600; color: var(--hm-text-2); margin: 6px 0 8px; padding-bottom: 4px; border-bottom: 1px solid var(--hm-hairline); }
@media (max-width: 700px) { .form-grid { grid-template-columns: 1fr; } }
</style>
