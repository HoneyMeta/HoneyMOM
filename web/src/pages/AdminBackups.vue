<template>
  <div class="page" data-test="admin-backups">
    <HmPageHead :title="$t('nav.backupRestore')" :sub="$t('adminBackups.backupDatabaseUploadedFilesDrawings')">
      <el-button v-perm="'system.backup.manage'" type="primary" :loading="busy" data-test="backup-now" @click="backupNow"><el-icon><FolderAdd /></el-icon>{{ $t('adminBackups.backUpNow') }}</el-button>
    </HmPageHead>

    <div class="grid split">
      <HmCard :title="$t('adminBackups.backupHistory')" flush>
        <HmDataTable :columns="COLS" :rows="items" row-key="id" :ai="false" :loading="!loaded" :empty-text="$t('adminBackups.noBackupsYet')">
          <template #cell-created_at="{ row }"><span class="hm-mono small">{{ row.created_at }}</span></template>
          <template #cell-kind="{ row }"><HmTag :tone="row.kind === 'scheduled' ? 'neutral' : 'primary'">{{ row.kind === 'scheduled' ? $t('common.scheduled') : $t('common.manual') }}</HmTag></template>
          <template #cell-size="{ row }"><span class="hm-num">{{ mb(row.size) }}</span></template>
          <template #cell-status="{ row }"><HmTag :tone="row.status === 'ok' && row.exists ? 'ok' : 'neutral'">{{ row.status === 'ok' ? (row.exists ? $t('common.available') : $t('adminBackups.fileMissing')) : row.status === 'pruned' ? $t('adminBackups.expiredAndDeleted') : $t('common.deleted') }}</HmTag></template>
          <template #cell-verify="{ row }">
            <a v-if="row.verify" class="lnk" @click="report = row.verify; rdlg = true"><HmTag :tone="row.verify.ok ? 'ok' : 'bad'">{{ row.verify.ok ? $t('adminBackups.drillPassed') : $t('adminBackups.drillFailed') }}</HmTag></a>
            <span v-else class="muted small">{{ $t('adminBackups.notDrilled') }}</span>
          </template>
          <template #cell-op="{ row }">
            <template v-if="row.status === 'ok' && row.exists">
              <el-button v-perm="'system.backup.manage'" link size="small" type="primary" :loading="drilling === row.id" :data-test="'backup-drill-' + row.id" @click="drill(row)">{{ $t('adminAudit.restoreDrill') }}</el-button>
              <el-button v-perm="'system.backup.manage'" link size="small" @click="download(row)">{{ $t('adminBackups.download') }}</el-button>
              <el-popconfirm :title="$t('adminBackups.deleteThisBackupFile')" @confirm="remove(row)"><template #reference><el-button v-perm="'system.backup.manage'" link size="small" type="danger">{{ $t('common.delete') }}</el-button></template></el-popconfirm>
            </template>
          </template>
        </HmDataTable>
      </HmCard>

      <div class="side">
        <HmCard :title="$t('adminBackups.scheduledBackup')">
          <el-form label-width="96px" size="small" :disabled="!can('system.backup.manage')">
            <el-form-item :label="$t('adminBackups.dailyBackup')"><el-switch v-model="sch.enabled" /></el-form-item>
            <el-form-item :label="$t('common.time')"><el-time-select v-model="sch.time" start="00:00" step="00:30" end="23:30" style="width:120px" /></el-form-item>
            <el-form-item :label="$t('adminBackups.keepScheduledBackups')"><el-input-number v-model="sch.keep" :min="1" :max="365" /> <span class="muted small">{{ $t('adminBackups.k1853') }}</span></el-form-item>
            <el-form-item :label="$t('adminBackups.keepManualBackups')"><el-input-number v-model="sch.keep_manual_days" :min="1" :max="3650" /> <span class="muted small">{{ $t('common.days') }}</span></el-form-item>
            <el-form-item><el-button v-perm="'system.backup.manage'" type="primary" @click="saveSch">{{ $t('common.save') }}</el-button></el-form-item>
          </el-form>
          <p class="small muted">{{ $t('adminBackups.backupDirectory') }}<span class="hm-mono">{{ sch.dir }}</span>{{ $t('adminBackups.setTheMesBackupDir', [dialect === 'postgresql' ? $t('adminBackups.postgresqlPgDump') : $t('adminBackups.sqliteOnlineBackup')]) }}</p>
        </HmCard>
        <HmCard :title="$t('adminBackups.productionRestoreCommandLine')">
          <ol class="steps">
            <li>{{ $t('adminBackups.pickABackupWhoseDrill') }}</li>
            <li>{{ $t('adminBackups.onTheServerRestoreInto') }}<b>{{ $t('adminBackups.newDirectory') }}</b>{{ $t('common.k384') }}<pre>{{ $t('adminBackups.pythonMHoneymomRestoreBackup', [dialect === 'postgresql' ? $t('adminBackups.toDbPostgresqlPsycopgUser') : '']) }}</pre></li>
            <li>{{ $t('adminBackups.checkThatEveryItemIn') }}</li>
            <li>{{ $t('adminBackups.stopTheServicePointHm', [dialect === 'postgresql' ? ' / MOM_DB' : '', dialect === 'postgresql' ? $t('adminBackups.andANewDatabase') : '']) }}</li>
          </ol>
        </HmCard>
      </div>
    </div>

    <el-dialog v-model="rdlg" :title="$t('adminBackups.restoreVerificationReport')" width="640px" append-to-body>
      <template v-if="report">
        <el-alert :type="report.ok ? 'success' : 'error'" :closable="false" show-icon :title="report.ok ? $t('adminBackups.allChecksPassed') : (report.error || $t('adminBackups.someChecksFailed'))" />
        <table class="rt" data-test="drill-report">
          <tbody><tr v-for="(c, i) in report.checks" :key="i"><td><HmTag :tone="c.ok ? 'ok' : 'bad'">{{ c.ok ? $t('common.approve') : $t('adminBackups.failed') }}</HmTag></td><td>{{ c.check }}<div v-if="c.detail" class="small muted">{{ c.detail }}</div></td></tr></tbody>
        </table>
        <p class="small muted">{{ $t('adminBackups.drillTime0Took1', [report.drill_at, ((report.ms || 0) / 1000).toFixed(1), report.kept ? $t('adminBackups.keptAt') + report.to : $t('adminBackups.cleanedUp')]) }}</p>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage } from 'element-plus';
import { onMounted, ref } from 'vue';
import { call, get, post, put, tok } from '../api';
import { can } from '../perm';

const items = ref<any[]>([]), loaded = ref(false), busy = ref(false), drilling = ref<number | null>(null), sch = ref<any>({}), dialect = ref('');
const rdlg = ref(false), report = ref<any>(null);
const COLS = [{ key: 'created_at', label: $t('common.time'), width: 150 }, { key: 'kind', label: $t('common.type'), width: 60 }, { key: 'size', label: $t('adminBackups.size'), num: true, width: 80 },
  { key: 'created_by', label: $t('common.operator'), width: 100 }, { key: 'status', label: $t('common.status'), width: 90 }, { key: 'verify', label: $t('adminAudit.restoreDrill'), width: 96 }, { key: 'op', label: '', width: 170, sortable: false }];
const mb = (n: number) => (n ? (n / 1048576).toFixed(n > 104857600 ? 0 : 1) + ' MB' : '—');

async function load() { const r = await get('/api/admin/backups'); items.value = r.items; sch.value = r.schedule; dialect.value = r.dialect; loaded.value = true; }
async function backupNow() { busy.value = true; try { const r = await post('/api/admin/backups', {}); ElMessage.success($t('adminBackups.backedUp0', [r.file])); await load(); } finally { busy.value = false; } }
async function drill(r: any) {
  drilling.value = r.id;
  try { report.value = await post(`/api/admin/backups/${r.id}/drill`, {}); rdlg.value = true; await load(); } finally { drilling.value = null; }
}
function download(r: any) { location.href = `/api/admin/backups/${r.id}/file?token=${tok()}`; }
async function remove(r: any) { await call('DELETE', '/api/admin/backups/' + r.id); ElMessage.success($t('common.deleted')); await load(); }
async function saveSch() { sch.value = await put('/api/admin/backups/schedule', sch.value); ElMessage.success($t('common.saved')); }
onMounted(load);
</script>

<style scoped>
.split { grid-template-columns: minmax(0, 1.7fr) minmax(0, 1fr); align-items: start; }
@media (max-width: 1279px) { .split { grid-template-columns: minmax(0, 1fr); } }
.side { display: grid; gap: 16px; }
.small { font-size: var(--hm-fs-xs); } .muted { color: var(--hm-text-3); }
.lnk { cursor: pointer; }
.steps { margin: 0; padding-left: 18px; font-size: var(--hm-fs-sm); line-height: 1.7; }
.steps pre { white-space: pre-wrap; word-break: break-all; background: var(--hm-bg-2, #f5f7fa); padding: 6px 8px; border-radius: var(--hm-r-sm); font-size: 12px; margin: 4px 0; }
.rt { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: var(--hm-fs-sm); }
.rt td { padding: 6px; border-bottom: 1px solid var(--hm-border); vertical-align: top; }
.rt td:first-child { width: 72px; }
</style>
