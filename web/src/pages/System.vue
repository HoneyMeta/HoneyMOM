<template>
  <div class="page">
    <HmPageHead :title="$t('common.systemStatus')" :sub="$t('system.statusOfDataCollectionThe')">
      <el-button @click="router.push('/e/datasource')"><el-icon><Connection /></el-icon>{{ $t('system.dataSourcesTags') }}</el-button>
      <el-button @click="router.push('/system/ai')"><el-icon><MagicStick /></el-icon>{{ $t('system.aiFeatures') }}</el-button>
    </HmPageHead>

    <div class="grid g4" data-tour="kpis">
      <HmKpi :label="$t('common.dataClock')" :value="(c?.now || '').slice(11, 19) || '—'" :tick="false" :loading="!c" :hint="(c?.now || '').slice(0, 10)" />
      <HmKpi :label="$t('system.dataSourcesOnline')" :value="c ? `${online} / ${ds.length}` : null" :tick="false" :status="!c ? '' : online === ds.filter((x) => x.enabled !== false).length ? 'ok' : online ? 'warn' : 'bad'" :loading="!c" />
      <HmKpi :label="$t('system.partEvents')" :value="c?.stats.part_events || 0" format="int" :unit="$t('common.k2047')" :loading="!c" :hint="$t('system.sinceThisStart')" />
      <HmKpi :label="$t('system.productionLog')" :value="c?.stats.events || 0" format="int" :unit="$t('common.k2047')" :loading="!c" :hint="$t('system.sinceThisStart')" />
    </div>

    <div class="grid split mt">
      <HmCard :title="$t('system.dataCollection')" :sub="$t('system.mqttUnifiedNamespaceOpcUa')" flush data-tour="sys-ds">
        <HmDataTable :columns="COLS" :rows="ds" entity="datasource" clickable :loading="!c" :empty-text="$t('system.noDataSourcesConfigured')" @row-click="(r: any) => router.push('/e/datasource?id=' + r.id)">
          <template #cell-code="{ row }"><span class="hm-mono">{{ row.code }}</span></template>
          <template #cell-endpoint="{ row }"><span class="hm-mono small">{{ row.endpoint }}</span></template>
          <template #cell-status="{ row }"><HmStatus :status="okState(row.status) ? 'online' : row.enabled === false ? 'offline' : 'down'" :label="row.status || (row.enabled === false ? $t('overviewTab.disabled') : $t('common.notConnected'))" /></template>
        </HmDataTable>
      </HmCard>
      <HmCard :title="$t('common.llm')">
        <template #actions><el-button link type="primary" @click="router.push('/system/ai')">{{ $t('system.aiFeatureSwitches') }}</el-button></template>
        <dl class="kv">
          <dt>{{ $t('common.status') }}</dt><dd><HmTag :tone="ai?.configured ? 'ok' : 'warn'">{{ ai?.configured ? $t('aiSettings.configured') : $t('system.notConfiguredAiFeaturesFall') }}</HmTag></dd>
          <dt>{{ $t('system.aiMode') }}</dt><dd><HmTag :tone="ai?.mode === 'off' ? 'neutral' : 'ai'">{{ ({ off: $t('common.off'), demo: $t('aiDock.demo'), record: $t('system.recording'), live: $t('common.online') } as any)[ai?.mode] || ai?.mode || '—' }}</HmTag></dd>
          <dt>{{ $t('system.api') }}</dt><dd class="hm-mono small">{{ ai?.base_url || '—' }}</dd>
          <dt>{{ $t('common.model') }}</dt><dd class="hm-mono small">{{ ai?.model || '—' }}</dd>
        </dl>
        <p class="small muted">{{ $t('system.setApiKeyIn') }} <span class="hm-mono">{{ ai?.config_file || 'config/llm.local.json' }}</span> {{ $t('system.onTheServerOrSet') }}</p>
      </HmCard>
    </div>

    <div v-if="dbs" class="grid split mt" data-test="sys-db">
      <HmCard :title="$t('system.database')">
        <template #actions><el-button v-perm="'system.backup.view'" link type="primary" @click="router.push('/system/backups')">{{ $t('system.backupRestore') }}</el-button></template>
        <dl class="kv">
          <dt>{{ $t('common.type') }}</dt><dd>{{ dbs.dialect === 'postgresql' ? 'PostgreSQL ' + dbs.server_version : 'SQLite ' + dbs.server_version }}<span v-if="dbs.timescaledb"> · TimescaleDB {{ dbs.timescaledb }}</span></dd>
          <dt>{{ $t('common.address') }}</dt><dd class="hm-mono small">{{ dbs.url }}</dd>
          <dt>{{ $t('adminBackups.size') }}</dt><dd>{{ dbs.size ? (dbs.size / 1048576).toFixed(0) + ' MB' : '—' }}</dd>
          <dt>{{ $t('system.schemaVersion') }}</dt><dd><span class="hm-mono">{{ dbs.migrations.head }}</span>
            <HmTag :tone="dbs.migrations.pending.length || dbs.drift.length ? 'bad' : 'ok'">{{ dbs.migrations.pending.length ? $t('system.n0MigrationsPending', [dbs.migrations.pending.length]) : dbs.drift.length ? $t('system.n0SchemaMismatches', [dbs.drift.length]) : $t('system.upToDate') }}</HmTag></dd>
        </dl>
      </HmCard>
      <HmCard :title="$t('system.equipmentTimeSeriesCollectionBuffer')">
        <dl class="kv">
          <dt>{{ $t('system.storage') }}</dt><dd>{{ $t('system.n01Tags', [TS_MODE[dbs.ts_mode] || dbs.ts_mode, dbs.ts_points]) }}</dd>
          <dt>{{ $t('system.retention') }}</dt><dd class="small">{{ $t('system.raw0DaysMinuteRollups', [dbs.ts_retention.raw, dbs.ts_retention['1m'], dbs.ts_retention['1h']]) }}</dd>
          <dt>{{ $t('system.offlineBuffer') }}</dt><dd><HmTag :tone="dbs.gate.buffering ? 'warn' : 'ok'">{{ dbs.gate.buffering ? $t('system.buffering0PendingUpload', [dbs.gate.pending]) : dbs.gate.pending ? $t('system.uploading0', [dbs.gate.pending]) : $t('system.writingDirectly') }}</HmTag>
            <span v-if="dbs.gate.replayed" class="small muted">{{ $t('system.n0UploadedInTotal', [dbs.gate.replayed]) }}</span></dd>
        </dl>
      </HmCard>
    </div>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { get } from '../api';
import { can } from '../perm';

const c = ref<any>(null), ai = ref<any>(null), ds = ref<any[]>([]), dbs = ref<any>(null);
const TS_MODE: Record<string, string> = { timescale: $t('system.timescaledbHypertableContinuousAggre'), pgpart: $t('system.postgresqlPartitionedTable'), sqlite: $t('system.sqliteTableRollupJobs') };
const router = useRouter();
const COLS = [{ key: 'code', label: $t('common.dataSource'), width: 90 }, { key: 'name', label: $t('common.name') }, { key: 'protocol', label: $t('common.protocol'), width: 90 }, { key: 'endpoint', label: $t('common.address') }, { key: 'status', label: $t('common.status'), width: 120 }];
const okState = (s: any) => /connected|online|ok|running|\u8fd0\u884c|\u5df2\u8fde\u63a5|\u5728\u7ebf/i.test(String(s || ''));
const online = computed(() => ds.value.filter((x) => okState(x.status)).length);
onMounted(async () => {
  if (can('system.db.view')) get('/api/admin/db', true).then((r) => { dbs.value = r; }).catch(() => null);
  [c.value, ai.value, ds.value] = await Promise.all([get('/api/collector'), get('/api/ai/status', true).catch(() => null), get('/api/e/datasource', true).then((r) => r.items || []).catch(() => [])]);
});
</script>

<style scoped>
.split { grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr); align-items: start; }
@media (max-width: 1279px) { .split { grid-template-columns: minmax(0, 1fr); } }
.kv { display: grid; grid-template-columns: 72px minmax(0, 1fr); gap: 8px 12px; margin: 4px 0 12px; align-items: center; }
.kv dt { color: var(--hm-text-3); font-size: var(--hm-fs-xs); } .kv dd { margin: 0; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
