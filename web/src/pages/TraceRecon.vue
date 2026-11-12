<template>
  <div class="page rc">
    <HmPageHead :title="$t('common.traceabilityReconciliation')" :sub="$t('traceRecon.walksARealChainHeat')">
      <el-select v-model="heat" filterable :placeholder="$t('traceRecon.selectHeatLot')" style="width: 260px" data-test="recon-heat" @change="load">
        <el-option v-for="h in heats" :key="h.heat" :value="h.heat" :label="$t('traceRecon.n01PcsCompleted2', [h.heat, h.parts, h.done])" />
      </el-select>
      <el-button @click="load"><el-icon><Refresh /></el-icon></el-button>
    </HmPageHead>
    <HmSkeleton v-if="!d && !err" variant="table" :rows="6" />
    <HmEmpty v-else-if="err" icon="search" :title="err" />
    <template v-else-if="d">
      <div class="sumbar" :class="d.ok ? 'ok' : 'bad'" data-test="recon-summary">
        <el-icon><component :is="d.ok ? 'CircleCheckFilled' : 'WarningFilled'" /></el-icon>
        <b>{{ d.heat }}</b><span>{{ $t('traceRecon.n01Pcs2', [d.is_heat ? $t('common.heat') : $t('traceRecon.purchasedLot'), d.parts, d.ok ? $t('traceRecon.wholeChainReconciles') : $t('traceRecon.n0StepsWithDifferences', [d.mismatches])]) }}</span>
        <span class="grow" />
        <el-button v-if="d.sample_sn" size="small" @click="router.push('/trace?sn=' + d.sample_sn)">{{ $t('traceRecon.viewAPart0', [d.sample_sn]) }}</el-button>
        <el-button size="small" @click="router.push('/quality/hold?lot=' + encodeURIComponent(d.heat))">{{ $t('traceRecon.holdThisLot') }}</el-button>
      </div>
      <div class="chain" data-test="recon-chain">
        <template v-for="(s, i) in d.steps" :key="s.key">
          <article class="st" :class="{ bad: !s.ok }" :data-test="'recon-step-' + s.key" tabindex="0" @click="cur = s"
            v-ai="{ kind: 'record', heat: d.heat, label: $t('traceRecon.n01In2Out', [d.heat, s.label, s.in, s.out, s.scrap, s.wip, s.diff ? $t('traceRecon.diff') + s.diff : '']), data: { [$t('implFlows.steps')]: s.label, [$t('traceRecon.in')]: s.in, [$t('traceRecon.out')]: s.out, [$t('common.scrapped')]: s.scrap, [$t('common.wip')]: s.wip, [$t('common.held')]: s.hold, [$t('erpCost.variance')]: s.diff, [$t('overviewTab.check')]: s.checks.map((c: any) => $t('traceRecon.n01Vs23', [c.name, c.a, c.b, c.ok ? $t('traceRecon.match') : $t('traceRecon.mismatch')])) } }">
            <header><span class="n">{{ i + 1 }}</span><b>{{ s.label }}</b><el-icon class="ic" :class="s.ok ? 'ok' : 'no'"><component :is="s.ok ? 'CircleCheckFilled' : 'WarningFilled'" /></el-icon></header>
            <div class="ref hm-mono">{{ s.ref }}</div>
            <dl class="nums">
              <div><dt>{{ $t('traceRecon.in') }}</dt><dd class="hm-num">{{ s.in }}</dd></div><div><dt>{{ $t('traceRecon.out') }}</dt><dd class="hm-num">{{ s.out }}</dd></div>
              <div><dt>{{ $t('common.scrapped') }}</dt><dd class="hm-num" :class="{ w: s.scrap }">{{ s.scrap }}</dd></div><div><dt>{{ $t('common.wip') }}</dt><dd class="hm-num">{{ s.wip }}</dd></div>
              <div v-if="s.hold"><dt>{{ $t('common.held') }}</dt><dd class="hm-num w">{{ s.hold }}</dd></div>
              <div v-if="s.diff"><dt>{{ $t('msaResult.poor') }}</dt><dd class="hm-num bad">{{ s.diff }}</dd></div>
            </dl>
            <ul class="ck">
              <li v-for="c in s.checks" :key="c.name" :class="c.ok ? 'ok' : 'no'" :title="c.detail || ''"><el-icon><component :is="c.ok ? 'Check' : 'Close'" /></el-icon>{{ c.name }}
                <span class="hm-num">{{ c.a }} / {{ c.b }}</span></li>
              <li v-for="a in s.anomalies.slice(0, 3)" :key="a.sn" class="no"><el-icon><Close /></el-icon><span class="hm-mono">{{ a.sn }}</span> {{ a.issue }}</li>
            </ul>
          </article>
        </template>
      </div>
      <div class="grid g2 mt">
        <HmCard :title="cur ? $t('traceRecon.n0Details', [cur.label]) : $t('traceRecon.clickAStepAboveFor')">
          <template v-if="cur">
            <el-descriptions v-if="cur.facts" :column="2" size="small" border>
              <el-descriptions-item v-for="(v, k) in cur.facts" :key="k" :label="String(k)"><span class="hm-num">{{ v ?? '—' }}</span></el-descriptions-item>
            </el-descriptions>
            <el-table v-if="cur.ladles" :data="cur.ladles" size="small" class="mt-s">
              <el-table-column prop="ladle" :label="$t('common.ladle')" width="110" /><el-table-column prop="parts" :label="$t('common.part')" width="70" align="right" />
              <el-table-column prop="treatments" :label="$t('nav.nodularizationRecords')" width="90" align="right" /><el-table-column :label="$t('traceRecon.residualMg')" width="90" align="right"><template #default="{ row }"><span class="hm-num">{{ row.mg ?? '—' }}</span></template></el-table-column>
              <el-table-column :label="$t('traceRecon.ironKg')" align="right"><template #default="{ row }"><span class="hm-num">{{ row.kg ?? '—' }}</span></template></el-table-column>
              <el-table-column label="" width="60"><template #default="{ row }"><HmTag :tone="row.ok ? 'ok' : 'bad'">{{ row.ok ? $t('traceRecon.matched') : $t('traceRecon.missingRecord') }}</HmTag></template></el-table-column>
            </el-table>
            <el-table v-if="cur.anomalies.length" :data="cur.anomalies" size="small" class="mt-s">
              <el-table-column :label="$t('common.partCode')" width="160"><template #default="{ row }"><el-button link type="primary" @click="router.push('/trace?sn=' + row.sn)">{{ row.sn }}</el-button></template></el-table-column>
              <el-table-column prop="issue" :label="$t('erpCost.variance')" />
            </el-table>
          </template>
        </HmCard>
        <HmCard :title="$t('traceRecon.scrappedWipParts')" :sub="$t('traceRecon.scrap0Wip1', [d.lists.scrap.length, d.lists.wip.length])" flush>
          <el-table :data="[...d.lists.scrap.map((x: any) => ({ ...x, k: $t('common.scrapped') })), ...d.lists.wip.map((x: any) => ({ ...x, k: $t('common.wip') }))]" size="small" max-height="360">
            <el-table-column :label="$t('common.partCode')" width="160"><template #default="{ row }"><el-button link type="primary" @click="router.push('/trace?sn=' + row.sn)">{{ row.sn }}</el-button></template></el-table-column>
            <el-table-column label="" width="60"><template #default="{ row }"><HmTag :tone="row.k === $t('common.scrapped') ? 'bad' : 'neutral'">{{ row.k }}</HmTag></template></el-table-column>
            <el-table-column :label="$t('traceRecon.locationReason')"><template #default="{ row }">{{ row.cause || row.station || row.status }}</template></el-table-column>
          </el-table>
        </HmCard>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { get } from '../api';

const router = useRouter(), route = useRoute();
const heats = ref<any[]>([]), heat = ref<string>((route.query.heat as string) || ''), d = ref<any>(null), err = ref(''), cur = ref<any>(null);
async function load() {
  err.value = '';
  try {
    d.value = await get('/api/trace/recon?heat=' + encodeURIComponent(heat.value), true);
    heat.value = d.value.heat;
    cur.value = d.value.steps.find((s: any) => !s.ok) || d.value.steps[0];
    router.replace({ query: { heat: heat.value } });
  } catch (e: any) { d.value = null; err.value = e.message || $t('common.noData'); }
}
onMounted(async () => { heats.value = await get('/api/trace/recon/heats'); await load(); });
</script>

<style scoped>
.sumbar { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-radius: var(--hm-r-md); border: 1px solid; margin-bottom: var(--hm-gap); flex-wrap: wrap; }
.sumbar.ok { background: var(--hm-run-soft); border-color: var(--hm-run); color: var(--hm-run-ink); } .sumbar.bad { background: var(--hm-fault-soft); border-color: var(--hm-fault); color: var(--hm-fault-ink); }
.sumbar b { font-family: var(--hm-font-mono); font-size: var(--hm-fs-md); }
.chain { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 14px; }
@media (max-width: 1280px) { .chain { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.st { position: relative; min-width: 0; background: var(--hm-surface); border: 1px solid var(--hm-border); border-radius: var(--hm-r-lg); padding: 10px 12px; box-shadow: var(--hm-e1); cursor: pointer; }
.st:hover { box-shadow: var(--hm-e2); } .st.bad { border-color: var(--hm-fault); background: linear-gradient(var(--hm-fault-soft), var(--hm-surface) 60%); }
.st header { display: flex; align-items: center; gap: 6px; } .st header b { flex: 1; font-size: var(--hm-fs-md); }
.n { width: 20px; height: 20px; border-radius: 50%; background: var(--hm-primary-soft); color: var(--hm-primary); display: inline-flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 600; }
.ic.ok { color: var(--hm-run); } .ic.no { color: var(--hm-fault); }
.ref { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); margin: 2px 0 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nums { display: grid; grid-template-columns: repeat(2, 1fr); gap: 4px 10px; margin: 0 0 8px; }
.nums dt { font-size: var(--hm-fs-2xs); color: var(--hm-text-3); } .nums dd { margin: 0; font-size: var(--hm-fs-lg); font-weight: 600; }
.st.bad .ref, .st.bad .nums dt { color: var(--hm-text-2); }          /* text-3 on the red gradient is below 3:1 */
.nums .w { color: var(--hm-idle-ink); } .nums .bad { color: var(--hm-fault); }
.ck { list-style: none; padding: 0; margin: 0; font-size: var(--hm-fs-2xs); display: flex; flex-direction: column; gap: 3px; }
.ck li { display: flex; gap: 4px; align-items: flex-start; } .ck li .hm-num { margin-left: auto; white-space: nowrap; }
.ck li.ok { color: var(--hm-text-2); } .ck li.ok .el-icon { color: var(--hm-run); } .ck li.no { color: var(--hm-fault-ink); font-weight: 500; }
.st:not(:last-child)::after { content: '›'; position: absolute; right: -12px; top: 42%; color: var(--hm-text-3); font-size: 18px; }
.mt-s { margin-top: 10px; }
</style>
