<template>
  <div class="page" data-test="impl-packs">
    <HmPageHead :title="$t('common.industryTemplates')" :sub="$t('implPacks.anIndustryTemplateOperationTypes')">
      <el-button v-if="d.can_apply" type="primary" data-test="pack-open-preview" @click="openPreview([])">{{ $t('implPacks.previewApply') }}</el-button>
    </HmPageHead>
    <div class="cards">
      <div v-for="p in industry" :key="p.code" class="pc" :class="{ on: p.applied }" :data-test="'pack-' + p.code">
        <div class="ph"><b>{{ p.name }}</b><span class="muted small">v{{ p.version }}</span><span class="grow" />
          <HmTag v-if="p.applied" tone="ok">{{ $t('implPacks.applied0', [p.applied_demo ? $t('implPacks.withDemo') : '']) }}</HmTag><HmTag v-else tone="neutral">{{ $t('implPacks.notApplied') }}</HmTag></div>
        <div class="small ind">{{ p.industry }}</div>
        <div class="small muted dsc">{{ p.description }}</div>
        <div class="chips"><HmTag v-for="m in p.modules" :key="m" tone="primary">{{ $t('implPacks.modules0', [MOD[m] || m]) }}</HmTag><HmTag tone="neutral">KPI {{ p.kpis.length }}</HmTag>
          <HmTag tone="neutral">{{ $t('implPacks.reports0', [p.reports.length]) }}</HmTag><HmTag v-if="p.demo" tone="amber">{{ $t('implPacks.demo0', [p.demo.name]) }}</HmTag></div>
        <div class="pa"><el-button size="small" :data-test="'pack-detail-' + p.code" @click="detail = p; dd = true">{{ $t('common.details2') }}</el-button>
          <el-button v-if="d.can_apply" size="small" type="primary" plain :data-test="'pack-preview-' + p.code" @click="openPreview([p.code])">{{ $t('implPacks.previewApply') }}</el-button></div>
      </div>
    </div>

    <div class="grid two">
      <HmCard :title="$t('implPacks.enabledKpis')" :sub="$t('implPacks.n0ItemsFromAppliedIndustry', [d.kpis?.length || 0])">
        <table class="hm-table dense"><thead><tr><th>{{ $t('common.kpi') }}</th><th>{{ $t('coplan.target') }}</th><th>{{ $t('common.description') }}</th></tr></thead>
          <tbody><tr v-for="k in d.kpis" :key="k.pack + k.key"><td>{{ k.label }}</td><td class="hm-num">{{ k.dir === 'down' ? '≤' : '≥' }} {{ k.target }} {{ k.unit }}</td><td class="small muted">{{ k.desc }}</td></tr></tbody></table>
      </HmCard>
      <HmCard :title="$t('implPacks.applicationHistory')" flush>
        <HmDataTable :columns="HCOLS" :rows="d.history || []" row-key="id" :ai="false" data-test="pack-history">
          <template #cell-pack="{ row }">{{ nameOf(row.pack) }}</template><template #cell-demo="{ row }">{{ row.demo ? $t('implPacks.demoData') : $t('implPacks.configuration') }}</template>
        </HmDataTable>
      </HmCard>
    </div>

    <el-drawer v-model="dd" :title="detail ? `${detail.name} v${detail.version}` : ''" :size="innerWidth < 900 ? '100%' : '760px'" data-test="pack-drawer">
      <template v-if="detail">
        <el-tabs>
          <el-tab-pane :label="$t('implPacks.overview')">
            <p>{{ detail.description }}</p>
            <p class="small muted">{{ $t('implPacks.dependsOn0OptionalModules', [(detail.requires || []).map(nameOf).join($t('common.k42')) || '—', detail.modules.map((m: string) => MOD[m] || m).join($t('common.k42')) || '—']) }}</p>
            <h4>{{ $t('implPacks.enumOptions') }}</h4>
            <table class="hm-table dense"><tbody><tr v-for="(opts, path) in detail.enums" :key="path"><td style="width:110px">{{ ENUM_CN[path] || path }}</td><td>{{ Object.values(opts).join($t('common.k42')) }}</td></tr></tbody></table>
          </el-tab-pane>
          <el-tab-pane :label="$t('common.equipmentTypeTemplates')">
            <table class="hm-table dense"><thead><tr><th>{{ $t('implPacks.templateCode') }}</th><th>{{ $t('common.name') }}</th><th>{{ $t('common.equipmentType') }}</th><th>{{ $t('implPacks.templateLibrary') }}</th></tr></thead>
              <tbody><tr v-for="t in detail.device_templates" :key="t.code"><td class="hm-mono">{{ t.code }}</td><td>{{ t.name }}</td><td>{{ t.etype }}</td>
                <td><HmTag :tone="t.installed ? 'ok' : 'neutral'">{{ t.library ? (t.installed ? $t('implPacks.exists') : $t('implPacks.notInTemplateLibrary')) : $t('implPacks.templateLibraryNotEnabled') }}</HmTag></td></tr></tbody></table>
            <p class="small muted">{{ $t('implPacks.equipmentTypeTemplatesTagSets') }}</p>
            <ul class="small"><li v-for="m in detail.maint_templates" :key="m.key">{{ $t('common.n012', [m.key === 'D' ? $t('implPacks.dailyInspectionRounds') : m.key === 'M' ? $t('implPacks.monthlyPm') : m.key, Object.keys(m.items_by_type || {}).join($t('common.k42')) || (m.types || []).join($t('common.k42'))]) }}</li></ul>
          </el-tab-pane>
          <el-tab-pane :label="$t('implPacks.kpisReports')">
            <table class="hm-table dense"><tbody><tr v-for="k in detail.kpis" :key="k.key"><td>{{ k.label }}</td><td class="hm-num">{{ k.dir === 'down' ? '≤' : '≥' }} {{ k.target }} {{ k.unit }}</td><td class="small muted">{{ k.desc }}</td></tr></tbody></table>
            <h4>{{ $t('implPacks.reportSetBecomesSharedReports') }}</h4>
            <ul><li v-for="r in detail.reports" :key="r.name">{{ r.name }} <span class="muted small">{{ $t('implPacks.n01', [r.dataset, r.chart]) }}</span></li></ul>
          </el-tab-pane>
          <el-tab-pane :label="$t('implPacks.configurationData')">
            <table class="hm-table dense"><tbody><tr v-for="(s, i) in detail.config" :key="i"><td>{{ s.what }}</td><td class="num">{{ $t('common.n03', [s.rows]) }}</td></tr></tbody></table>
          </el-tab-pane>
          <el-tab-pane v-if="detail.demo" :label="$t('implPacks.demoData')">
            <p><b>{{ detail.demo.name }}</b></p><p class="small">{{ detail.demo.description }}</p>
            <table class="hm-table dense"><tbody><tr v-for="(s, i) in detail.demo.steps" :key="i"><td>{{ s.what }}</td><td class="num">{{ $t('common.n03', [s.rows]) }}</td></tr></tbody></table>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>

    <el-dialog v-model="pdlg" :title="$t('implPacks.previewApplyIndustryTemplate')" width="min(900px, 96vw)" append-to-body data-test="pack-dialog">
      <PackPreview :packs="industry" :initial="initial" :can-apply="d.can_apply" @applied="onApplied" />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { computed, onMounted, ref } from 'vue';
import { get, loadSession } from '../api';
import PackPreview from '../impl/PackPreview.vue';
import { loadImplState } from '../impl/state';

const d = ref<any>({ packs: [] }), detail = ref<any>(null), dd = ref(false), pdlg = ref(false), initial = ref<string[]>([]);
const innerWidth = window.innerWidth;
const MOD: Record<string, string> = { foundry: $t('implPacks.foundry'), machining: $t('common.machining'), injection: $t('implPacks.injectionMolding'), assembly: $t('implPacks.assembly') };
const ENUM_CN: Record<string, string> = { 'process.ptype': $t('packPreview.operationTypes'), 'equipment.etype': $t('common.equipmentType'), 'workshop.wtype': $t('packPreview.workshopTypes'), 'defect_code.category': $t('packPreview.defectCategories') };
const HCOLS = [{ key: 'applied_at', label: $t('common.time'), width: 150 }, { key: 'pack', label: $t('common.template'), width: 100 }, { key: 'version', label: $t('common.versions'), width: 70 },
  { key: 'demo', label: $t('common.content'), width: 80 }, { key: 'applied_by', label: $t('common.operator'), width: 90 }];
const industry = computed(() => (d.value.packs || []).filter((p: any) => p.kind !== 'base'));
const nameOf = (c: string) => (d.value.packs || []).find((p: any) => p.code === c)?.name || c;
async function load() { d.value = await get('/api/impl/packs'); }
function openPreview(codes: string[]) { initial.value = codes; pdlg.value = true; }
async function onApplied() { pdlg.value = false; await load(); await loadImplState(true); await loadSession(); }
onMounted(load);
</script>

<style scoped>
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; margin-bottom: 12px; }
.pc { background: var(--hm-surface); border: 1px solid var(--hm-border); border-radius: var(--hm-r-lg); padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; }
.pc.on { border-color: rgba(21, 163, 74, .4); }
.ph { display: flex; align-items: center; gap: 8px; } .ph b { font-size: var(--hm-fs-lg); }
.ind { color: var(--hm-text-2); } .dsc { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; } .pa { display: flex; justify-content: flex-end; gap: 6px; }
.two { grid-template-columns: 1fr 1fr; } @media (max-width: 1100px) { .two { grid-template-columns: 1fr; } }
h4 { margin: 14px 0 6px; }
</style>
