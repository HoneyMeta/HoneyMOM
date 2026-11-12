<template>
  <el-dialog :model-value="!!mid" :title="$t('skillSaveDialog.saveAsSkill')" width="min(760px, 96vw)" append-to-body data-test="skill-save-dlg" @update:model-value="(v: boolean) => !v && emit('close')">
    <HmSkeleton v-if="loading" variant="block" :height="200" />
    <div v-else-if="err" class="err">{{ err }}</div>
    <template v-else-if="d">
      <el-form label-width="90px" size="small">
        <el-form-item :label="$t('common.name')"><el-input v-model="d.name" data-test="skill-save-name" /></el-form-item>
        <el-form-item :label="$t('common.description')"><el-input v-model="d.description" /></el-form-item>
        <el-form-item :label="$t('common.parameters')">
          <div class="ps">
            <div class="muted small">{{ $t('skillSaveDialog.n0SuggestsTurningTheseLiterals', [d.by === 'rule' ? $t('skillSaveDialog.ruleBased') : 'AI']) }}</div>
            <div v-for="(p, i) in d.params" :key="p.key" class="pr" data-test="skill-save-param">
              <code>{{ '{' + p.key + '}' }}</code><el-input v-model="p.label" style="width: 110px" /><span class="muted">{{ $t('skillSaveDialog.default') }}</span><el-input v-model="p.default" style="width: 150px" />
              <el-button link size="small" @click="dropParam(i)"><el-icon><Delete /></el-icon></el-button>
            </div>
            <div v-if="!d.params.length" class="muted small">{{ $t('skillSaveDialog.noLiteralsToParameterize') }}</div>
          </div>
        </el-form-item>
        <el-form-item :label="$t('skillSaveDialog.queryPlan')">
          <ol class="plan"><li v-for="(s, i) in d.plan.steps" :key="i"><code>{{ s.tool }}</code> <span class="muted">{{ JSON.stringify(s.args) }}</span></li></ol>
        </el-form-item>
        <el-form-item :label="$t('skillSaveDialog.output')"><el-checkbox-group v-model="d.output.blocks"><el-checkbox v-for="b in BL" :key="b.k" :value="b.k">{{ b.l }}</el-checkbox></el-checkbox-group></el-form-item>
        <el-form-item :label="$t('common.runMode')">
          <el-select v-model="d.schedule.mode" style="width: 130px" data-test="skill-save-mode"><el-option v-for="(l, k) in MODES" :key="k" :value="k" :label="l" /></el-select>
          <el-time-select v-if="d.schedule.mode === 'daily'" v-model="d.schedule.time" start="00:00" end="23:30" step="00:30" style="width: 110px; margin-left: 8px" />
          <el-input v-if="d.schedule.mode === 'cron'" v-model="d.schedule.cron" placeholder="0 8 * * 1-5" style="width: 150px; margin-left: 8px" />
        </el-form-item>
        <el-form-item :label="$t('skillSaveDialog.sendResultsTo')"><el-checkbox v-model="d.targets.feed">{{ $t('skillSaveDialog.aiWatchFeed') }}</el-checkbox><el-checkbox v-model="d.targets.daily">{{ $t('common.productionDailyReport') }}</el-checkbox>
          <el-checkbox v-model="d.narrative">{{ $t('skillSaveDialog.aiConclusionOneLlmCall') }}</el-checkbox></el-form-item>
        <el-form-item :label="$t('common.shareWith')"><el-select v-model="d.share_roles" multiple :placeholder="$t('skillSaveDialog.onlyMe')" style="width: 100%"><el-option value="*" :label="$t('common.everyone')" /><el-option v-for="(l, k) in ROLES" :key="k" :value="k" :label="l" /></el-select></el-form-item>
      </el-form>
    </template>
    <template #footer>
      <el-button @click="emit('close')">{{ $t('common.cancel') }}</el-button>
      <el-button type="primary" :disabled="!d" :loading="saving" data-test="skill-save-ok" @click="save">{{ $t('skillSaveDialog.saveSkill') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
// "Save as Skill": rebuild the query plan (actual tools and args called) + suggested params + output format from an assistant answer; tweak and save to the skill library (services/skills.py).
import { ElMessage } from 'element-plus';
import { ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { post } from '../api';

const props = defineProps<{ mid: number | null }>();
const emit = defineEmits<{ close: [] }>();
const router = useRouter();
const MODES: Record<string, string> = { manual: $t('common.manual'), hourly: $t('skillSaveDialog.hourly'), daily: $t('skillSaveDialog.daily'), shift_end: $t('skillSaveDialog.endOfEachShift'), cron: 'cron' };
const ROLES: Record<string, string> = { manager: $t('skillSaveDialog.plantManager'), planner: $t('skillSaveDialog.planner'), leader: $t('skillSaveDialog.teamLeader'), operator: $t('skillSaveDialog.operator'), qc: $t('skillSaveDialog.qualityInspector'), maint: $t('skillSaveDialog.maintenanceTechnician'), warehouse: $t('skillSaveDialog.warehouseKeeper'), engineer: $t('skillSaveDialog.processEngineer') };
const BL = [{ k: 'kpis', l: $t('skillSaveDialog.kpiCard') }, { k: 'chart', l: $t('common.chart') }, { k: 'table', l: $t('common.table') }, { k: 'callout', l: $t('common.hint') }];
const d = ref<any>(null), loading = ref(false), err = ref(''), saving = ref(false);

watch(() => props.mid, async (v) => {
  d.value = null; err.value = '';
  if (!v) return;
  loading.value = true;
  try {
    const r = await post('/api/skills/from_message', { message_id: v });
    r.schedule = { time: '07:30', cron: '', ...r.schedule };
    d.value = r;
  } catch (e: any) { err.value = e.message || String(e); } finally { loading.value = false; }
}, { immediate: true });

function dropParam(i: number) {
  const p = d.value.params[i];
  d.value.params.splice(i, 1);
  // Removed params: placeholders in the plan revert to default values (literals)
  const rep = (x: any): any => typeof x === 'string' ? (x === `{${p.key}}` ? p.default : x.split(`{${p.key}}`).join(String(p.default ?? ''))) : Array.isArray(x) ? x.map(rep) : x && typeof x === 'object' ? Object.fromEntries(Object.entries(x).map(([k, v]) => [k, rep(v)])) : x;
  d.value.plan.steps = d.value.plan.steps.map((s: any) => ({ ...s, args: rep(s.args) }));
}
async function save() {
  saving.value = true;
  try {
    const s = { ...d.value, schedule: { ...d.value.schedule, enabled: d.value.schedule.mode !== 'manual' } };
    const r = await post('/api/skills', s);
    ElMessage.success({ message: $t('skillSaveDialog.skill0Saved', [r.name]), duration: 2500 });
    emit('close');
    router.push('/ai/skills?id=' + r.id);
  } finally { saving.value = false; }
}
</script>

<style scoped>
.ps { width: 100%; } .pr { display: flex; align-items: center; gap: 6px; margin-top: 6px; } .pr code { width: 90px; font-size: 12px; }
.plan { margin: 0; padding-left: 18px; font-size: 12px; line-height: 1.8; word-break: break-all; }
.err { color: var(--hm-fault-ink); padding: 10px; }
.muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-xs); }
</style>
