<template>
  <div class="page pk">
    <HmPageHead :title="TITLE[tab]" :sub="SUB[tab]" />
    <el-tabs v-model="tab" class="tabs" data-test="pack-tabs" @tab-change="go">
      <el-tab-pane :label="$t('common.packingStation')" name="station" /><el-tab-pane :label="$t('common.loadingShippingAsn')" name="ship" />
    </el-tabs>

    <!-- ───────────── Packing Station ───────────── -->
    <template v-if="tab === 'station'">
      <HmToolbar>
        <span class="tf"><span class="lb">{{ $t('common.workstation') }}</span><el-input v-model="station" size="large" style="width: 130px" class="hm-mono" data-test="pack-station" @change="onStation" /></span>
        <span class="tf"><span class="lb">{{ $t('common.customer') }}</span>
          <el-select v-model="customer" size="large" filterable allow-create default-first-option clearable :placeholder="$t('packStation.byPackingRule')" style="width: 220px" data-test="pack-customer" @change="onCustomer">
            <el-option v-for="c in customers" :key="c" :value="c" :label="c" />
          </el-select></span>
        <el-button size="large" @click="loadStation"><el-icon><Refresh /></el-icon>{{ $t('common.refresh') }}</el-button>
        <template #right><span class="muted small">{{ $t('packStation.partCodePackIntoCarton') }}</span></template>
      </HmToolbar>

      <div class="scan-row mt">
        <el-input ref="scanEl" v-model="code" size="large" class="scan hm-mono" clearable :placeholder="$t('packStation.scanPartCodeCartonId')" data-test="pack-scan" @keyup.enter="scan">
          <template #prefix><el-icon><Aim /></el-icon></template>
        </el-input>
        <el-input-number v-model="qty" :min="0" :max="100000" size="large" controls-position="right" :placeholder="$t('packStation.lotQty')" class="qty" />
        <el-button type="primary" size="large" class="big" :loading="busy" data-test="pack-scan-go" @click="scan">{{ $t('common.ok') }}</el-button>
      </div>
      <div class="msg" :class="msg.tone" data-test="pack-msg" role="status" aria-live="polite">
        <el-icon class="mi"><component :is="msg.tone === 'bad' ? 'CircleCloseFilled' : msg.tone === 'ok' ? 'CircleCheckFilled' : msg.tone === 'warn' ? 'WarningFilled' : 'InfoFilled'" /></el-icon>
        <div class="mt-t"><b>{{ msg.text }}</b><span v-if="msg.sub" class="ms">{{ msg.sub }}</span></div>
        <span class="hm-num mtime">{{ msg.t }}</span>
      </div>

      <div class="grid g2 mt">
        <HmCard :title="$t('packStation.currentCarton')" :sub="st.carton ? cartonSub : $t('packStation.noCartonBeingPacked')" data-test="pack-carton">
          <template #actions>
            <el-button size="large" type="primary" :disabled="!st.carton || !st.carton.qty" data-test="pack-close" @click="close(st.carton)">{{ $t('common.sealCarton') }}</el-button>
          </template>
          <template v-if="st.carton">
            <div class="pkg-h">
              <span class="hm-mono code">{{ st.carton.code }}</span>
              <span class="hm-num qn"><b>{{ fmtNum(st.carton.qty) }}</b> {{ $t('packStation.n0Pcs', [fmtNum(st.carton.capacity)]) }}</span>
            </div>
            <el-progress :percentage="pct(st.carton.qty, st.carton.capacity)" :stroke-width="14" :show-text="false" :status="pct(st.carton.qty, st.carton.capacity) >= 100 ? 'success' : ''" />
            <div class="meta">
              <span>{{ $t('common.lot') }} <b class="hm-mono">{{ st.carton.lots || '—' }}</b></span>
              <span v-if="st.carton.customer">{{ $t('common.customer') }} <b>{{ st.carton.customer }}</b></span>
            </div>
            <div class="items" data-test="pack-items">
              <div v-for="it in st.carton.items || []" :key="it.id" class="it">
                <span class="hm-mono">{{ it.sn || it.lot }}</span>
                <span v-if="!it.sn" class="hm-num muted">× {{ fmtNum(it.qty) }}</span>
                <span v-else-if="it.lot" class="muted small hm-mono">{{ it.lot }}</span>
                <span class="grow" />
                <span class="hm-num muted small">{{ fmtTime(it.time, 'time') }}</span>
                <el-button v-if="it.sn" size="default" plain data-test="pack-unpack" @click="unpack(it.sn)">{{ $t('packStation.remove') }}</el-button>
              </div>
              <HmEmpty v-if="!(st.carton.items || []).length" plain :title="$t('packStation.cartonIsEmpty')" />
            </div>
          </template>
          <HmEmpty v-else plain :title="$t('packStation.scanningTheFirstPartOpens')" />
        </HmCard>

        <HmCard :title="$t('packStation.currentPallet')" :sub="st.pallet ? palletSub : $t('packStation.noPalletBeingBuilt')" data-test="pack-pallet">
          <template #actions>
            <el-button size="large" type="primary" :disabled="!st.pallet || !(st.pallet.cartons || []).length" data-test="pack-close-pallet" @click="close(st.pallet)">{{ $t('packStation.closePallet') }}</el-button>
          </template>
          <template v-if="st.pallet">
            <div class="pkg-h">
              <span class="hm-mono code">{{ st.pallet.code }}</span>
              <span class="hm-num qn"><b>{{ fmtNum((st.pallet.cartons || []).length) }}</b> {{ $t('packStation.n0Cartons1Pcs', [fmtNum(st.pallet.capacity), fmtNum(st.pallet.qty)]) }}</span>
            </div>
            <el-progress :percentage="pct((st.pallet.cartons || []).length, st.pallet.capacity)" :stroke-width="14" :show-text="false" />
            <div class="meta"><span>{{ $t('common.lot') }} <b class="hm-mono">{{ st.pallet.lots || '—' }}</b></span><span v-if="st.pallet.customer">{{ $t('common.customer') }} <b>{{ st.pallet.customer }}</b></span></div>
            <div class="items">
              <div v-for="c in [...(st.pallet.cartons || [])].reverse()" :key="c.id" class="it">
                <span class="hm-mono">{{ c.code }}</span><span class="hm-num muted">{{ $t('packStation.n0Pcs2', [fmtNum(c.qty)]) }}</span><span class="muted small hm-mono">{{ c.lots }}</span>
              </div>
              <HmEmpty v-if="!(st.pallet.cartons || []).length" plain :title="$t('packStation.palletIsEmpty')" />
            </div>
          </template>
          <HmEmpty v-else plain :title="$t('packStation.scanningASealedCartonId')" />
        </HmCard>
      </div>

      <HmCard :title="$t('packStation.recentlySealedCartonsPallets')" :sub="$t('packStation.thisStation')" flush class="mt" data-test="pack-recent">
        <el-table :data="st.recent || []" size="default"
          v-ai-rows="{ entity: 'package', rows: () => st.recent || [], label: (r: any) => `${r.kind === 'pallet' ? $t('packStation.pallet') : $t('cmpImpact.carton')} ${r.code}`, data: (r: any) => ({ [$t('common.no2')]: r.code, [$t('common.quantity')]: r.qty, [$t('common.lot')]: r.lots, [$t('common.status')]: PST[r.status], [$t('common.customer')]: r.customer }) }">
          <el-table-column :label="$t('packStation.cartonPalletNo')" min-width="160"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
          <el-table-column :label="$t('common.type')" width="80"><template #default="{ row }">{{ row.kind === 'pallet' ? $t('packStation.pallet') : $t('cmpImpact.carton') }}</template></el-table-column>
          <el-table-column :label="$t('common.quantity')" width="90" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty) }}</span></template></el-table-column>
          <el-table-column :label="$t('common.lot')" min-width="140"><template #default="{ row }"><span class="hm-mono">{{ row.lots || '—' }}</span></template></el-table-column>
          <el-table-column prop="customer" :label="$t('common.customer')" min-width="120" />
          <el-table-column :label="$t('common.status')" width="100"><template #default="{ row }"><HmTag :tone="PTONE[row.status] || 'neutral'">{{ PST[row.status] || row.status }}</HmTag></template></el-table-column>
          <el-table-column :label="$t('packStation.sealedAt')" width="120"><template #default="{ row }"><span class="hm-num">{{ fmtTime(row.closed_at) }}</span></template></el-table-column>
          <el-table-column label="" width="190"><template #default="{ row }">
            <el-button size="default" type="primary" plain data-test="pack-print" @click="printLabel(row)">{{ $t('packStation.printLabel') }}</el-button>
            <el-button size="default" plain v-perm:disable="'label.reprint'" data-test="pack-reprint" @click="reprintLabel(row)">{{ $t('labelDesigner.reprint') }}</el-button>
          </template></el-table-column>
        </el-table>
      </HmCard>
    </template>

    <!-- ───────────── Loading & Shipping ───────────── -->
    <template v-else>
      <HmToolbar>
        <el-radio-group v-model="lf" @change="loadLoads">
          <el-radio-button value="">{{ $t('common.all') }}</el-radio-button><el-radio-button value="open">{{ $t('packStation.toShip') }}</el-radio-button><el-radio-button value="shipped">{{ $t('common.shipped') }}</el-radio-button>
        </el-radio-group>
        <el-input v-model="dlvCode" :placeholder="$t('common.deliveryOrderNo')" clearable style="width: 180px" class="hm-mono" data-test="ship-delivery" @keyup.enter="fromDelivery" />
        <el-button :disabled="!dlvCode.trim()" data-test="ship-from-delivery" @click="fromDelivery">{{ $t('packStation.createFromDeliveryOrder') }}</el-button>
        <template #right><el-button type="primary" v-perm="'pack.ship_load.create'" data-test="ship-new" @click="openNew">{{ $t('packStation.newLoadingList') }}</el-button></template>
      </HmToolbar>
      <HmCard flush class="mt">
        <el-table :data="loadsShown" size="default" data-test="ship-loads" highlight-current-row @row-click="(r: any) => openLoad(r.id)"
          v-ai-rows="{ entity: 'ship_load', rows: () => loadsShown, label: (r: any) => $t('packStation.loadingList01', [r.code, LST[r.status] || '']), data: (r: any) => ({ [$t('common.loadingLists')]: r.code, [$t('common.deliveryOrder')]: r.delivery_code, [$t('common.customer')]: r.customer, [$t('packStation.licensePlate')]: r.vehicle, [$t('common.status')]: LST[r.status], [$t('cmpImpact.pallet')]: r.pallets, [$t('cmpImpact.carton')]: r.cartons, [$t('common.pcs')]: r.qty, ASN: r.asn_no }) }">
          <el-table-column :label="$t('common.loadingLists')" min-width="140"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
          <el-table-column :label="$t('common.deliveryOrder')" min-width="130"><template #default="{ row }"><span class="hm-mono">{{ row.delivery_code || '—' }}</span></template></el-table-column>
          <el-table-column prop="customer" :label="$t('common.customer')" min-width="130" />
          <el-table-column :label="$t('packStation.licensePlateDock')" min-width="130"><template #default="{ row }">{{ [row.vehicle, row.dock].filter(Boolean).join(' / ') || '—' }}</template></el-table-column>
          <el-table-column :label="$t('common.status')" width="100"><template #default="{ row }"><HmTag :tone="LTONE[row.status] || 'neutral'">{{ LST[row.status] || row.status }}</HmTag></template></el-table-column>
          <el-table-column :label="$t('packStation.palletsCartonsPcs')" width="130" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.pallets) }} / {{ fmtNum(row.cartons) }} / {{ fmtNum(row.qty) }}</span></template></el-table-column>
          <el-table-column label="ASN" min-width="130"><template #default="{ row }"><span class="hm-mono">{{ row.asn_no || '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('cmpImpact.shippedAt')" width="120"><template #default="{ row }"><span class="hm-num">{{ fmtTime(row.shipped_at) }}</span></template></el-table-column>
        </el-table>
        <HmEmpty v-if="!loadsShown.length" icon="data" :title="$t('packStation.noLoadingListsYet')" :desc="$t('packStation.createALoadingListOr')" />
      </HmCard>
    </template>

    <!-- Loading list detail -->
    <el-drawer v-model="dt.open" :title="dt.d ? $t('cmpImpact.loadingLists0', [dt.d.item.code]) : $t('common.loadingLists')" size="min(760px, 96vw)" destroy-on-close @closed="dt.d = null">
      <div v-if="dt.d" class="dd" data-test="ship-detail">
        <div class="dd-h">
          <HmTag :tone="LTONE[dt.d.item.status] || 'neutral'">{{ LST[dt.d.item.status] || dt.d.item.status }}</HmTag>
          <span>{{ dt.d.item.customer }}</span>
          <span v-if="dt.d.item.delivery_code" class="muted">{{ $t('common.deliveryOrder') }} <span class="hm-mono">{{ dt.d.item.delivery_code }}</span></span>
          <span v-if="dt.d.item.vehicle" class="muted">{{ $t('packStation.plate0', [dt.d.item.vehicle]) }}</span>
          <span v-if="dt.d.item.dock" class="muted">{{ $t('packStation.dock0', [dt.d.item.dock]) }}</span>
          <span v-if="dt.d.item.asn_no" class="muted">ASN <span class="hm-mono">{{ dt.d.item.asn_no }}</span></span>
        </div>
        <div class="grid kp">
          <HmKpi :label="$t('cmpImpact.pallet')" :value="dt.d.item.pallets || 0" format="int" :unit="$t('cmpImpact.pallet')" :ai="false" />
          <HmKpi :label="$t('cmpImpact.carton')" :value="dt.d.item.cartons || 0" format="int" :unit="$t('cmpImpact.carton')" :ai="false" />
          <HmKpi :label="$t('common.pcs')" :value="dt.d.item.qty || 0" format="int" :unit="$t('common.pcs')" :ai="false" :status="overQty ? 'warn' : ''" />
          <HmKpi v-if="dt.d.delivery" :label="$t('packStation.deliveryOrderQty')" :value="dt.d.delivery.qty || 0" format="int" :unit="$t('common.pcs')" :ai="false" />
        </div>

        <template v-if="isOpen">
          <div class="scan-row">
            <el-input ref="loadScanEl" v-model="lcode" size="large" class="scan hm-mono" clearable :placeholder="$t('packStation.scanPalletIdOrLoose')" data-test="ship-load-scan" @keyup.enter="loadScan">
              <template #prefix><el-icon><Aim /></el-icon></template>
            </el-input>
            <el-button type="primary" size="large" class="big" @click="loadScan">{{ $t('packStation.load') }}</el-button>
          </div>
          <div v-if="lmsg.text" class="msg" :class="lmsg.tone" data-test="ship-msg"><b>{{ lmsg.text }}</b><span v-if="lmsg.sub" class="ms">{{ lmsg.sub }}</span></div>
        </template>

        <div class="acts">
          <el-button v-if="isOpen" :disabled="!dt.d.packages.length" data-test="ship-asn" @click="genAsn">{{ $t('packStation.generateAsn') }}</el-button>
          <el-dropdown trigger="click" :disabled="!dt.d.packages.length" @command="downloadAsn">
            <el-button :disabled="!dt.d.packages.length" data-test="ship-asn-dl">{{ $t('packStation.downloadAsn') }}<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
            <template #dropdown><el-dropdown-menu>
              <el-dropdown-item command="json">JSON</el-dropdown-item><el-dropdown-item command="xlsx">Excel</el-dropdown-item>
              <el-dropdown-item command="flat">{{ $t('packStation.flatFileTxt') }}</el-dropdown-item><el-dropdown-item command="edifact">EDIFACT DESADV</el-dropdown-item>
            </el-dropdown-menu></template>
          </el-dropdown>
          <el-button data-test="ship-trace" @click="openTrace">{{ $t('common.trace') }}</el-button>
          <span class="grow" />
          <el-button v-if="isOpen" type="danger" plain :disabled="!!dt.d.packages.length" @click="cancelLoad">{{ $t('packStation.cancelLoadingList') }}</el-button>
          <el-button v-if="isOpen" type="primary" :disabled="!dt.d.packages.length" data-test="ship-confirm" @click="ship">{{ $t('packStation.confirmShipment') }}</el-button>
        </div>

        <HmCard v-if="isOpen" :title="$t('packStation.palletsToLoad')" :sub="dt.d.item.customer ? $t('packStation.sameCustomerClosedNotLoaded') : $t('packStation.closedNotLoaded')" flush>
          <el-table :data="dt.d.ready" size="default" max-height="260" data-test="ship-ready">
            <el-table-column :label="$t('packStation.pallet')" min-width="150"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
            <el-table-column prop="material__label" :label="$t('common.product')" min-width="140" />
            <el-table-column :label="$t('common.parts')" width="80" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty) }}</span></template></el-table-column>
            <el-table-column :label="$t('common.lot')" min-width="120"><template #default="{ row }"><span class="hm-mono">{{ row.lots || '—' }}</span></template></el-table-column>
            <el-table-column label="" width="90"><template #default="{ row }"><el-button size="default" type="primary" plain data-test="ship-ready-load" @click="loadScan(row.code)">{{ $t('packStation.load') }}</el-button></template></el-table-column>
          </el-table>
          <HmEmpty v-if="!dt.d.ready.length" plain :title="$t('packStation.noPalletsWaitingToLoad')" />
        </HmCard>

        <HmCard :title="$t('packStation.onTruck')" :sub="$t('packStation.n0Packages', [dt.d.packages.length])" flush class="mt">
          <el-table :data="dt.d.packages" size="default" max-height="320" data-test="ship-packages">
            <el-table-column :label="$t('packStation.palletsCartons')" min-width="150"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span><span v-if="row.parent" class="muted small"> {{ $t('packStation.at0', [row.parent__label]) }}</span></template></el-table-column>
            <el-table-column :label="$t('common.type')" width="70"><template #default="{ row }">{{ row.kind === 'pallet' ? $t('packStation.pallet') : $t('cmpImpact.carton') }}</template></el-table-column>
            <el-table-column :label="$t('common.parts')" width="80" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty) }}</span></template></el-table-column>
            <el-table-column :label="$t('common.lot')" min-width="120"><template #default="{ row }"><span class="hm-mono">{{ row.lots || '—' }}</span></template></el-table-column>
            <el-table-column :label="$t('common.status')" width="90"><template #default="{ row }"><HmTag :tone="PTONE[row.status] || 'neutral'">{{ PST[row.status] || row.status }}</HmTag></template></el-table-column>
            <el-table-column label="" width="80"><template #default="{ row }">
              <el-button v-if="isOpen && (row.kind === 'pallet' || !row.parent)" size="default" plain data-test="ship-unload" @click="unload(row.code)">{{ $t('packStation.unload') }}</el-button>
            </template></el-table-column>
          </el-table>
          <HmEmpty v-if="!dt.d.packages.length" plain :title="$t('packStation.nothingOnTheTruckYet')" />
        </HmCard>
      </div>
    </el-drawer>

    <!-- New loading list -->
    <el-dialog v-model="nl.open" :title="$t('packStation.newLoadingList')" width="min(520px, 96vw)" destroy-on-close>
      <el-form label-width="90px">
        <el-form-item :label="$t('common.customer')" required>
          <el-select v-model="nl.f.customer" filterable allow-create default-first-option style="width: 100%" data-test="ship-new-customer"><el-option v-for="c in customers" :key="c" :value="c" :label="c" /></el-select>
        </el-form-item>
        <el-form-item :label="$t('common.deliveryOrderNo')"><el-input v-model="nl.f.delivery_code" class="hm-mono" :placeholder="$t('packStation.enterManuallyWithExternalErp')" /></el-form-item>
        <el-form-item :label="$t('packStation.licensePlate')"><el-input v-model="nl.f.vehicle" :placeholder="$t('packStation.abc1234')" /></el-form-item>
        <el-form-item :label="$t('packStation.driver')"><el-input v-model="nl.f.driver" /></el-form-item>
        <el-form-item :label="$t('packStation.dock')"><el-input v-model="nl.f.dock" /></el-form-item>
        <el-form-item :label="$t('packStation.deliveryAddress')"><el-input v-model="nl.f.ship_to" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="nl.open = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :disabled="!nl.f.customer" data-test="ship-new-ok" @click="createLoad">{{ $t('common.create') }}</el-button></template>
    </el-dialog>

    <!-- Traceability -->
    <el-dialog v-model="tr.open" :title="$t('packStation.shipmentTraceability0', [tr.code])" width="min(680px, 96vw)" destroy-on-close>
      <div class="row">
        <el-input v-model="tr.code" class="hm-mono" :placeholder="$t('packStation.loadingListAsnDeliveryOrder')" style="max-width: 360px" data-test="ship-trace-code" @keyup.enter="loadTrace" />
        <el-button @click="loadTrace">{{ $t('packStation.search') }}</el-button>
        <span v-if="tr.d" class="muted small hm-num">{{ $t('packStation.n0PalletsLooseCartons1', [tr.d.tree.length, fmtNum(tr.d.parts)]) }}</span>
      </div>
      <div v-if="tr.d" class="trl">
        <div v-for="l in tr.d.loads" :key="l.id" class="tl-load">
          <span class="hm-mono">{{ l.code }}</span><HmTag :tone="LTONE[l.status] || 'neutral'">{{ LST[l.status] }}</HmTag>
          <span class="muted small">{{ l.customer }}</span><span v-if="l.asn_no" class="muted small">ASN <span class="hm-mono">{{ l.asn_no }}</span></span>
        </div>
        <el-tree :data="treeData" :props="{ label: 'label', children: 'children' }" node-key="key" :default-expand-all="treeData.length <= 3" data-test="ship-trace-tree" class="tree">
          <template #default="{ data }">
            <span class="tn"><HmTag :tone="data.kind === 'pallet' ? 'primary' : data.kind === 'carton' ? 'neutral' : 'ok'">{{ KN[data.kind] }}</HmTag>
              <span class="hm-mono">{{ data.code }}</span><span v-if="data.qty != null" class="muted small hm-num">{{ $t('packStation.n0Pcs2', [fmtNum(data.qty)]) }}</span>
              <span v-if="data.lots" class="muted small hm-mono">{{ data.lots }}</span></span>
          </template>
        </el-tree>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t, lang } from '../i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { call, get, post, session, tok } from '../api';
import { fmtNum, fmtTime } from '../design';

const router = useRouter(), route = useRoute();
const TITLE: Record<string, string> = { station: $t('common.packingStation'), ship: $t('common.loadingShippingAsn') };
const SUB: Record<string, string> = { station: $t('packStation.scanToPackAndPalletize'), ship: $t('packStation.scanPalletsToLoadChecking') };
const PST: Record<string, string> = { open: $t('cmpImpact.packing'), closed: $t('packStation.sealed'), loaded: $t('cmpImpact.loaded'), shipped: $t('common.shipped') };
const PTONE: Record<string, any> = { open: 'warn', closed: 'primary', loaded: 'amber', shipped: 'ok' };
const LST: Record<string, string> = { planned: $t('common.plan'), loading: $t('packStation.loading'), shipped: $t('common.shipped'), cancelled: $t('common.cancelled') };
const LTONE: Record<string, any> = { planned: 'neutral', loading: 'warn', shipped: 'ok', cancelled: 'neutral' };
const KN: Record<string, string> = { pallet: $t('cmpImpact.pallet'), carton: $t('cmpImpact.carton'), part: $t('common.pcs') };
const tab = ref<string>((route.meta.tab as string) || 'station');
function go(t: any) { router.replace('/exec/' + (t === 'ship' ? 'ship' : 'pack')); }
const pct = (a: any, b: any) => (Number(b) > 0 ? Math.min(100, Math.round((Number(a) / Number(b)) * 100)) : 0);
const now = () => new Date().toTimeString().slice(0, 8);
function lsGet(k: string, d = '') { try { return localStorage.getItem(k) || d; } catch { return d; } }
function lsSet(k: string, v: string) { try { localStorage.setItem(k, v); } catch { /* no storage */ } }

/** Beeps: short high tone on success, two low tones on block (operators may not be watching the screen) */
let ac: AudioContext | null = null;
function beep(ok: boolean) {
  try {
    ac = ac || new (window.AudioContext || (window as any).webkitAudioContext)();
    const seq = ok ? [[1320, 0, 0.08]] : [[300, 0, 0.16], [300, 0.22, 0.16]];
    for (const [f, t0, d] of seq) {
      const o = ac.createOscillator(), g = ac.createGain();
      o.frequency.value = f; g.gain.value = 0.08; o.connect(g); g.connect(ac.destination);
      o.start(ac.currentTime + t0); o.stop(ac.currentTime + t0 + d);
    }
  } catch { /* no sound card */ }
}

// ───────────── Packing Station ─────────────
const station = ref(lsGet('hm_pack_station', 'PACK1'));
const customer = ref(lsGet('hm_pack_customer'));
const customers = ref<string[]>([]);
const code = ref(''), qty = ref<number | undefined>(undefined), busy = ref(false);
const st = ref<any>({ carton: null, pallet: null, recent: [] });
const msg = reactive<any>({ tone: 'idle', text: $t('packStation.waitingForScan'), sub: '', t: '' });
const scanEl = ref<any>();
const cartonSub = computed(() => [st.value.carton?.material__label].filter(Boolean).join(' · ') || $t('cmpImpact.packing'));
const palletSub = computed(() => [st.value.pallet?.material__label].filter(Boolean).join(' · ') || $t('packStation.palletizing'));
const focusScan = () => nextTick(() => scanEl.value?.focus?.());
function say(tone: string, text: string, sub = '') { Object.assign(msg, { tone, text, sub, t: now() }); }

async function loadCustomers() {
  const r = await get('/api/e/pack_rule?size=500', true).catch(() => ({ items: [] }));
  const s = new Set<string>((r.items || []).map((x: any) => x.customer).filter(Boolean));
  if (customer.value) s.add(customer.value);
  customers.value = [...s];
}
async function loadStation() {
  const s = station.value.trim() || 'PACK1';
  st.value = await get('/api/pack/station?station=' + encodeURIComponent(s), true).catch(() => st.value);
}
function onStation() { station.value = station.value.trim().toUpperCase() || 'PACK1'; lsSet('hm_pack_station', station.value); loadStation(); focusScan(); }
function onCustomer() { lsSet('hm_pack_customer', customer.value || ''); focusScan(); }

async function scan() {
  const c = code.value.trim();
  if (!c || busy.value) { focusScan(); return; }
  busy.value = true;
  try {
    const body: any = { code: c, station: station.value.trim() || 'PACK1', customer: customer.value || undefined };
    if (qty.value) body.qty = qty.value;
    const r = await call('POST', '/api/pack/scan', body, true);
    if (r.state) st.value = r.state;
    const extra = r.closed ? (r.action === 'pallet' ? $t('packStation.palletFullAndClosedPallet') : $t('packStation.cartonFullAndSealedCarton')) : '';
    say(r.closed ? 'warn' : 'ok', r.msg || $t('packStation.loaded'), extra);
    beep(true);
    qty.value = undefined;
    if (r.closed && r.jobs?.length) autoOpenLabels(r.carton && r.action === 'pack' ? r.carton : r.pallet);
  } catch (e: any) {
    say('bad', e.message || $t('packStation.scanFailed'), $t('packStation.blockedThisPartWasNot'));
    beep(false);
  } finally {
    code.value = ''; busy.value = false; focusScan();
  }
}
async function unpack(sn: string) {
  try { const r = await call('POST', '/api/pack/unpack', { sn }, true); if (r.state) st.value = r.state; say('ok', r.msg); }
  catch (e: any) { say('bad', e.message); beep(false); }
  focusScan();
}
async function close(p: any) {
  if (!p) return;
  try {
    await ElMessageBox.confirm($t('packStation.n0CurrentlyHas1Seal', [p.code, p.kind === 'pallet' ? (p.cartons || []).length + $t('packStation.cartons') : p.qty + $t('packStation.pcs'), p.kind === 'pallet' ? $t('cmpImpact.pallet') : $t('cmpImpact.carton')]), p.kind === 'pallet' ? $t('packStation.closePallet') : $t('common.sealCarton'), { type: 'warning', confirmButtonText: $t('packStation.seal') });
  } catch { focusScan(); return; }
  try {
    const r = await call('POST', '/api/pack/close', { code: p.code }, true);
    if (r.state) st.value = r.state; else await loadStation();
    say('warn', r.msg);
    if (r.jobs?.length) autoOpenLabels(p.code);
  } catch (e: any) { say('bad', e.message); beep(false); }
  focusScan();
}

// Labels: browser-print jobs → open the PDF (when the rule targets a browser printer)
async function jobsFor(c: string) {
  const r = await get(`/api/label/jobs?ref=${encodeURIComponent(c)}&limit=50`, true).catch(() => ({ items: [] }));
  return (r.items || []).filter((j: any) => j.ref === c || String(j.target || '').endsWith(':' + c));
}
const openPdf = (id: number) => window.open(`/api/label/job/${id}/pdf?token=${tok()}`, '_blank');
async function autoOpenLabels(c: string) {
  if (!c) return;
  const js = (await jobsFor(c)).filter((j: any) => j.kind === 'browser');
  if (js.length) openPdf(js[0].id);
}
async function printLabel(row: any) {
  const js = await jobsFor(row.code);
  const b = js.find((j: any) => j.kind === 'browser');
  if (b) { openPdf(b.id); return; }
  if (js.length) ElMessage.info($t('packStation.labelsFor0GoTo', [row.code, js[0].code, js[0].status === 'done' ? $t('common.completed') : js[0].status]));
  else ElMessage.info($t('packStation.n0HasNoLabelJob', [row.code]));
  focusScan();
}
async function reprintLabel(row: any) {
  const js = await jobsFor(row.code);
  if (!js.length) { ElMessage.info($t('packStation.n0HasNoLabelJob2', [row.code])); return; }
  let reason = '';
  try {
    ({ value: reason } = await ElMessageBox.prompt($t('packStation.reprintTheLabelFor0', [row.code, js[0].template__label || '']), $t('labelDesigner.reprint'), {
      inputPlaceholder: $t('labelDesigner.eGLabelSoiledWrong'), inputValidator: (v: string) => (v || '').trim().length >= 2 || $t('labelDesigner.pleaseEnterTheReprintReason') }) as any);
  } catch { focusScan(); return; }
  const r = await post('/api/label/reprint', { job: js[0].id, reason: reason.trim() });
  ElMessage.success($t('labelDesigner.reprinted0', [r.code]));
  if (r.pdf) window.open(`${r.pdf}?token=${tok()}`, '_blank');
  focusScan();
}

// ───────────── Loading & Shipping ─────────────
const loads = ref<any[]>([]), lf = ref('open');
const loadsShown = computed(() => loads.value.filter((l) => !lf.value || (lf.value === 'open' ? ['planned', 'loading'].includes(l.status) : l.status === lf.value)));
async function loadLoads() { loads.value = (await get('/api/e/ship_load?size=100&sort=-id')).items; }
const dlvCode = ref('');
async function fromDelivery() {
  const c = dlvCode.value.trim(); if (!c) return;
  try { const r = await call('POST', '/api/pack/load/from_delivery', { code: c }, true); ElMessage.success(r.msg); dlvCode.value = ''; await loadLoads(); openLoad(r.id); }
  catch (e: any) { ElMessage.error(e.message); }
}
const nl = reactive<any>({ open: false, f: {} });
async function openNew() { if (!customers.value.length) await loadCustomers(); nl.f = { customer: customer.value || customers.value[0] || '', delivery_code: '', vehicle: '', driver: '', dock: '', ship_to: '' }; nl.open = true; }
async function createLoad() {
  const f = Object.fromEntries(Object.entries(nl.f).map(([k, v]) => [k, typeof v === 'string' ? v.trim() || null : v]));
  const r = await post('/api/e/ship_load', f);
  nl.open = false; ElMessage.success($t('packStation.loadingListCreated')); await loadLoads();
  const id = r.id || r.item?.id; if (id) openLoad(id);
}

const dt = reactive<any>({ open: false, d: null, id: 0 });
const lcode = ref(''), loadScanEl = ref<any>();
const lmsg = reactive<any>({ tone: 'idle', text: '', sub: '' });
const isOpen = computed(() => dt.d && ['planned', 'loading'].includes(dt.d.item.status));
const overQty = computed(() => !!dt.d?.delivery?.qty && (dt.d.item.qty || 0) > dt.d.delivery.qty);
async function refreshLoad() { dt.d = await get(`/api/pack/load/${dt.id}`); }
async function openLoad(id: number) {
  dt.id = id; lmsg.text = ''; lcode.value = '';
  await refreshLoad(); dt.open = true;
  nextTick(() => loadScanEl.value?.focus?.());
}
async function loadScan(c?: any) {
  const v = (typeof c === 'string' ? c : lcode.value).trim();
  if (!v) return;
  try {
    const r = await call('POST', `/api/pack/load/${dt.id}/scan`, { code: v }, true);
    Object.assign(lmsg, { tone: r.warn ? 'warn' : 'ok', text: r.msg, sub: r.warn || '' }); beep(true);
  } catch (e: any) { Object.assign(lmsg, { tone: 'bad', text: e.message, sub: $t('packStation.notLoaded') }); beep(false); }
  lcode.value = '';
  await refreshLoad(); loadLoads();
  nextTick(() => loadScanEl.value?.focus?.());
}
async function unload(c: string) {
  try { const r = await call('POST', `/api/pack/load/${dt.id}/unload`, { code: c }, true); Object.assign(lmsg, { tone: 'ok', text: r.msg, sub: '' }); }
  catch (e: any) { Object.assign(lmsg, { tone: 'bad', text: e.message, sub: '' }); }
  await refreshLoad(); loadLoads();
}
async function genAsn() { const r = await post(`/api/e/ship_load/${dt.id}/do/asn`, {}); ElMessage.success(r.msg); await refreshLoad(); loadLoads(); }
async function downloadAsn(fmt: string) {
  try {
    const r = await fetch(`/api/pack/load/${dt.id}/asn?format=${fmt}`, { headers: { 'X-Lang': lang, Authorization: 'Bearer ' + session.token } });
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(typeof j.detail === 'string' ? j.detail : r.statusText); }
    const cd = r.headers.get('Content-Disposition') || '';
    const name = /filename="([^"]+)"/.exec(cd)?.[1] || `${dt.d.item.asn_no || dt.d.item.code}.${{ json: 'json', xlsx: 'xlsx', flat: 'txt', edifact: 'edi' }[fmt] || 'txt'}`;
    const u = URL.createObjectURL(await r.blob());
    const a = document.createElement('a'); a.href = u; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(u), 30000);
  } catch (e: any) { ElMessage.error($t('packStation.downloadFailed') + e.message); }
}
async function ship() {
  const it = dt.d.item;
  try {
    await ElMessageBox.confirm($t('packStation.ship01Pallets2', [it.code, it.pallets, it.cartons, it.qty, it.customer, overQty.value ? $t('packStation.exceedsDeliveryOrderBy0', [dt.d.delivery.qty]) : '']), $t('packStation.confirmShipment'), { type: 'warning', confirmButtonText: $t('packStation.confirmShipment2') });
  } catch { return; }
  const r = await post(`/api/e/ship_load/${dt.id}/do/ship`, {});
  ElMessage.success(r.msg); await refreshLoad(); loadLoads();
}
async function cancelLoad() {
  try { await ElMessageBox.confirm($t('packStation.cancelLoadingList0', [dt.d.item.code]), $t('common.cancel'), { type: 'warning' }); } catch { return; }
  const r = await post(`/api/e/ship_load/${dt.id}/do/cancel`, {}); ElMessage.success(r.msg); await refreshLoad(); loadLoads();
}

// Traceability
const tr = reactive<any>({ open: false, code: '', d: null });
async function loadTrace() {
  if (!tr.code.trim()) return;
  try { tr.d = await call('GET', '/api/pack/trace?code=' + encodeURIComponent(tr.code.trim()), undefined, true); }
  catch (e: any) { tr.d = null; ElMessage.error(e.message); }
}
function openTrace() { tr.code = dt.d?.item.asn_no || dt.d?.item.code || ''; tr.d = null; tr.open = true; loadTrace(); }
const treeData = computed(() => {
  let n = 0;
  const node = (p: any): any => ({ key: 'k' + n++, code: p.code, kind: p.kind, qty: p.qty, lots: p.lots,
    children: p.kind === 'pallet' ? (p.children || []).map(node) : (p.parts || []).map((x: any) => ({ key: 'k' + n++, code: x.sn || x.lot, kind: 'part', qty: x.sn ? null : x.qty, lots: x.sn ? x.lot : '' })) });
  return (tr.d?.tree || []).map(node);
});

// ───────────── Lifecycle ─────────────
let poll: any = null;
async function loadTab() {
  if (tab.value === 'station') { await Promise.all([loadStation(), loadCustomers()]); focusScan(); }
  else { await Promise.all([loadLoads(), loadCustomers()]); if (route.query.load) openLoad(Number(route.query.load)); }
}
watch(() => route.meta.tab, (t) => { if (t && t !== tab.value) { tab.value = t as string; loadTab(); } });
onMounted(() => {
  loadTab();
  // Packing station: status changed by another terminal (or auto-seal on full carton) syncs every 10 s; doesn't interrupt scanning
  poll = setInterval(() => { if (tab.value === 'station' && document.visibilityState === 'visible' && !busy.value) loadStation(); }, 10000);
});
onUnmounted(() => { clearInterval(poll); try { ac?.close(); } catch { /* */ } });
</script>

<style scoped>
.tabs { margin-bottom: 4px; }
.muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-2xs); } .grow { flex: 1; }
.row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.tf { display: inline-flex; align-items: center; gap: 6px; }
.lb { font-size: var(--hm-fs-sm); color: var(--hm-text-2); }

/* Scan */
.scan-row { display: flex; gap: 10px; align-items: stretch; flex-wrap: wrap; }
.scan { flex: 1 1 320px; min-width: 0; }
.scan :deep(.el-input__wrapper) { min-height: calc(56px * var(--hm-scale)); }
.scan :deep(.el-input__inner) { font-size: calc(22px * var(--hm-scale)); font-family: var(--hm-font-mono); letter-spacing: .02em; }
.scan :deep(.el-input__prefix) { font-size: calc(22px * var(--hm-scale)); }
.qty { width: calc(150px * var(--hm-scale)); }
.qty :deep(.el-input__wrapper) { min-height: calc(56px * var(--hm-scale)); }
.big { min-height: calc(56px * var(--hm-scale)); min-width: calc(110px * var(--hm-scale)); font-size: var(--hm-fs-lg); }

/* Result banner */
.msg { display: flex; align-items: center; gap: 12px; margin-top: 10px; padding: 12px 16px; border-radius: var(--hm-r-md); border: 1px solid var(--hm-border);
  background: var(--hm-surface-2); color: var(--hm-text); min-height: calc(56px * var(--hm-scale)); }
.msg b { font-size: var(--hm-fs-lg); font-weight: 600; }
.msg .ms { display: block; font-size: var(--hm-fs-sm); color: var(--hm-text-2); margin-top: 2px; }
.mt-t { flex: 1; min-width: 0; }
.mi { font-size: calc(28px * var(--hm-scale)); flex: none; color: var(--hm-text-3); }
.mtime { color: var(--hm-text-2); font-size: var(--hm-fs-sm); }
.msg.ok { background: var(--hm-run-soft); border-color: var(--hm-run); color: var(--hm-run-ink); } .msg.ok .mi { color: var(--hm-run-ink); }
.msg.warn { background: var(--hm-idle-soft); border-color: var(--hm-idle); color: var(--hm-idle-ink); } .msg.warn .mi { color: var(--hm-idle-ink); }
.msg.bad { background: var(--hm-fault-soft); border: 2px solid var(--hm-fault); color: var(--hm-fault-ink); } .msg.bad .mi { color: var(--hm-fault-ink); }
.msg.bad b { font-size: var(--hm-fs-xl); }

/* Carton / pallet */
.pkg-h { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }
.code { font-size: var(--hm-fs-xl); font-weight: 600; color: var(--hm-text); }
.qn { font-size: var(--hm-fs-lg); color: var(--hm-text-2); }
.qn b { font-size: var(--hm-fs-2xl); color: var(--hm-text); }
.meta { display: flex; gap: 16px; flex-wrap: wrap; margin: 8px 0; font-size: var(--hm-fs-sm); color: var(--hm-text-2); }
.meta b { color: var(--hm-text); font-weight: 500; }
.items { max-height: calc(300px * var(--hm-scale)); overflow: auto; border-top: 1px solid var(--hm-border); }
.it { display: flex; align-items: center; gap: 10px; padding: 6px 2px; border-bottom: 1px solid var(--hm-hairline); font-size: var(--hm-fs-md); flex-wrap: wrap; }

/* Loading list */
.dd { display: flex; flex-direction: column; gap: 12px; }
.dd-h { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.kp { grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); }
.acts { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.acts .el-button + .el-button { margin-left: 0; }
.trl { margin-top: 12px; }
.tl-load { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; }
.tree { max-height: 56vh; overflow: auto; }
.tn { display: inline-flex; align-items: center; gap: 8px; }
</style>
