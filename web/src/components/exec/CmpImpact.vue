<template>
  <div class="ci" data-test="cmp-impact">
    <div class="kp">
      <HmKpi compact :label="$t('cmpImpact.affectedHeatsLots')" :value="(im.heats || []).length" format="int" :unit="$t('common.k550')" />
      <HmKpi compact :label="$t('cmpImpact.totalParts')" :value="im.parts_total" format="int" :unit="$t('common.pcs')" />
      <HmKpi compact :label="$t('common.shipped')" :value="im.shipped?.count" format="int" :unit="$t('common.pcs')" :status="im.shipped?.count ? 'bad' : ''" />
      <HmKpi compact :label="$t('cmpImpact.inPlant')" :value="im.in_house?.count" format="int" :unit="$t('common.pcs')" />
      <HmKpi compact :label="$t('cmpImpact.notHeld')" :value="im.in_house?.not_held" format="int" :unit="$t('common.pcs')" :status="im.in_house?.not_held ? 'warn' : im.in_house?.count ? 'ok' : ''" />
      <HmKpi compact :label="$t('common.scrapped')" :value="im.scrap" format="int" :unit="$t('common.pcs')" />
      <HmKpi compact :label="$t('cmpImpact.analysisTime')" :value="im.elapsed_ms" format="int" unit="ms" />
    </div>

    <div class="line">
      <span class="lb">{{ $t('cmpImpact.heatsLots') }}</span>
      <HmTag v-for="h in im.heats || []" :key="h" mono :tone="held.has(h) ? 'bad' : 'neutral'">{{ h }}{{ held.has(h) ? $t('cmpImpact.held') : '' }}</HmTag>
      <span v-if="!(im.heats || []).length" class="t2">—</span>
    </div>
    <div class="line">
      <span class="lb">{{ $t('cmpImpact.customersAffected') }}</span>
      <HmTag v-for="(n, c) in im.shipped?.customers || {}" :key="c" tone="amber">{{ c }} <span class="hm-num">{{ n }}</span> {{ $t('common.pcs') }}</HmTag>
      <span v-if="!Object.keys(im.shipped?.customers || {}).length" class="t2">{{ $t('cmpImpact.noPartsHaveBeenShipped') }}</span>
    </div>
    <div v-if="(im.loads || []).length" class="line">
      <span class="lb">{{ $t('common.loadingLists') }}</span>
      <span v-for="l in im.loads" :key="l.code" class="ld"><span class="hm-mono">{{ l.code }}</span> {{ l.customer || '' }} <span class="t2">{{ LST[l.status] || l.status }}</span><span v-if="l.asn_no" class="t2"> · ASN <span class="hm-mono">{{ l.asn_no }}</span></span></span>
    </div>

    <el-tabs v-model="tab" class="tb">
      <el-tab-pane :label="$t('cmpImpact.shipped0', [im.shipped?.count || 0])" name="shipped">
        <el-table :data="im.shipped?.parts || []" size="small" max-height="320" data-test="cmp-shipped">
          <el-table-column :label="$t('common.partCode')" min-width="130"><template #default="{ row }"><a class="lnk hm-mono" @click="trace(row.sn)">{{ row.sn }}</a></template></el-table-column>
          <el-table-column :label="$t('common.heat')" width="110"><template #default="{ row }"><span class="hm-mono">{{ row.heat || '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('cmpImpact.carton')" width="130"><template #default="{ row }"><span class="hm-mono">{{ row.carton || '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('cmpImpact.pallet')" width="130"><template #default="{ row }"><span class="hm-mono">{{ row.pallet || '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('common.loadingLists')" width="130"><template #default="{ row }"><span class="hm-mono">{{ row.load || '—' }}</span></template></el-table-column>
          <el-table-column prop="customer" :label="$t('common.customer')" min-width="100" />
        </el-table>
      </el-tab-pane>
      <el-tab-pane :label="$t('cmpImpact.inPlant0', [im.in_house?.count || 0])" name="in">
        <el-table :data="im.in_house?.parts || []" size="small" max-height="320" data-test="cmp-inhouse">
          <el-table-column :label="$t('common.partCode')" min-width="130"><template #default="{ row }"><a class="lnk hm-mono" @click="trace(row.sn)">{{ row.sn }}</a></template></el-table-column>
          <el-table-column :label="$t('common.heat')" width="110"><template #default="{ row }"><span class="hm-mono">{{ row.heat || '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('common.status')" width="90"><template #default="{ row }"><HmTag :tone="row.status === 'hold' ? 'bad' : 'neutral'">{{ PST[row.status] || row.status }}</HmTag></template></el-table-column>
          <el-table-column :label="$t('cmpImpact.carton')" width="130"><template #default="{ row }"><span class="hm-mono">{{ row.carton || '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('cmpImpact.pallet')" min-width="120"><template #default="{ row }"><span class="hm-mono">{{ row.pallet || '—' }}</span></template></el-table-column>
        </el-table>
      </el-tab-pane>
      <el-tab-pane :label="$t('cmpImpact.cartons0', [(im.cartons || []).length])" name="cartons">
        <el-table :data="im.cartons || []" size="small" max-height="320">
          <el-table-column :label="$t('common.cartonNo')" min-width="130"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
          <el-table-column :label="$t('cmpImpact.pallet')" width="130"><template #default="{ row }"><span class="hm-mono">{{ row.pallet || '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('common.loadingLists')" width="130"><template #default="{ row }"><span class="hm-mono">{{ row.load || '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('common.status')" width="90"><template #default="{ row }">{{ KST[row.status] || row.status }}</template></el-table-column>
          <el-table-column :label="$t('cmpImpact.affectedQty')" width="100" align="right"><template #default="{ row }"><span class="hm-num">{{ row.n }}</span></template></el-table-column>
        </el-table>
      </el-tab-pane>
      <el-tab-pane :label="$t('cmpImpact.loadingLists0', [(im.loads || []).length])" name="loads">
        <el-table :data="im.loads || []" size="small" max-height="320">
          <el-table-column :label="$t('common.loadingLists')" width="130"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
          <el-table-column prop="customer" :label="$t('common.customer')" min-width="100" />
          <el-table-column :label="$t('common.status')" width="80"><template #default="{ row }">{{ LST[row.status] || row.status }}</template></el-table-column>
          <el-table-column :label="$t('common.deliveryOrder')" width="120"><template #default="{ row }"><span class="hm-mono">{{ row.delivery_code || '—' }}</span></template></el-table-column>
          <el-table-column label="ASN" width="130"><template #default="{ row }"><span class="hm-mono">{{ row.asn_no || '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('cmpImpact.shippedAt')" width="110"><template #default="{ row }"><span class="hm-num">{{ fmtTime(row.shipped_at) }}</span></template></el-table-column>
        </el-table>
      </el-tab-pane>
      <el-tab-pane :label="$t('cmpImpact.inventoryLots0', [(im.stock_lots || []).length])" name="stock">
        <el-table :data="im.stock_lots || []" size="small" max-height="320">
          <el-table-column prop="material" :label="$t('common.material')" min-width="100" />
          <el-table-column prop="warehouse" :label="$t('common.warehouse')" width="110" />
          <el-table-column :label="$t('common.lot')" width="130"><template #default="{ row }"><span class="hm-mono">{{ row.lot }}</span></template></el-table-column>
          <el-table-column :label="$t('common.quantity')" width="90" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty) }}</span></template></el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../../i18n';
// Impact scope of a customer complaint / recall simulation (result of services/complaint.analyze): metrics, heats, customers, shipped / in-plant parts, cartons, loading lists, inventory lots.
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { ent } from '../../api';
import { fmtNum, fmtTime } from '../../design';

const props = defineProps<{ impact: any }>();
const router = useRouter();
const im = computed(() => props.impact || {});
const held = computed(() => new Set(im.value.held_heats || []));
const tab = ref(im.value.shipped?.count ? 'shipped' : 'in');
const PST = computed<Record<string, string>>(() => ent('part')?.fields?.find((f: any) => f.name === 'status')?.enum || { wip: $t('common.wip'), good: $t('common.good'), hold: $t('common.held'), scrap: $t('common.scrapped'), shipped: $t('common.shipped') });
const enumOf = (e: string, f: string) => ent(e)?.fields?.find((x: any) => x.name === f)?.enum || {};
const LST = computed<Record<string, string>>(() => ({ shipped: $t('common.shipped'), ...enumOf('ship_load', 'status') }));
const KST = computed<Record<string, string>>(() => ({ open: $t('cmpImpact.packing'), closed: $t('cmpImpact.sealed'), loaded: $t('cmpImpact.loaded'), shipped: $t('common.shipped'), ...enumOf('package', 'status') }));
const trace = (sn: string) => router.push('/trace?sn=' + encodeURIComponent(sn));
</script>

<style scoped>
.ci { display: flex; flex-direction: column; gap: var(--hm-sp-2); }
.kp { display: grid; grid-template-columns: repeat(auto-fill, minmax(calc(118px * var(--hm-scale)), 1fr)); gap: 8px; }
.line { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: var(--hm-fs-xs); }
.lb { color: var(--hm-text-2); min-width: 64px; }
.ld { padding-right: 10px; }
.t2 { color: var(--hm-text-2); }
.lnk { color: var(--hm-primary); cursor: pointer; }
.tb { margin-top: var(--hm-sp-1); }
</style>
