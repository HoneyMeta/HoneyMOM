<template>
  <div class="page ecn">
    <HmPageHead :title="$t('common.engineeringChangeEcn')" :sub="$t('ecn.requestImpactAnalysisApprovalE')">
      <el-button v-perm="'ecn.ecn.create'" type="primary" data-test="ecn-new" @click="openNew">{{ $t('ecn.newEcn') }}</el-button>
    </HmPageHead>

    <HmToolbar>
      <el-radio-group v-model="flt" size="small">
        <el-radio-button value="open">{{ $t('common.inProgress') }}</el-radio-button><el-radio-button value="done">{{ $t('ecn.closed') }}</el-radio-button><el-radio-button value="">{{ $t('common.all') }}</el-radio-button>
      </el-radio-group>
      <el-input v-model="q" :placeholder="$t('ecn.ecnNoTitle')" clearable style="width: 200px" />
      <template #right><span class="muted small">{{ $t('ecn.clickARowForImpact') }}</span></template>
    </HmToolbar>
    <HmCard flush class="mt">
      <el-table :data="shownList" size="small" max-height="300" highlight-current-row data-test="ecn-table" class="clickable" :row-class-name="(p: any) => (p.row.id === cur?.item.id ? 'cur' : '')"
        @row-click="(r: any) => select(r.id)"
        v-ai-rows="{ entity: 'ecn', rows: () => shownList, label: (r: any) => `ECN ${r.code} ${r.title} ${ST[r.status]}`, data: (r: any) => ({ [$t('common.product')]: r.product__label, [$t('common.status')]: ST[r.status], [$t('ecn.wipWorkOrders')]: WP[r.wo_policy], [$t('common.reason')]: r.reason }) }">
        <el-table-column :label="$t('ecn.ecnNo')" width="130"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
        <el-table-column prop="title" :label="$t('ecn.changeTitle')" min-width="240" />
        <el-table-column :label="$t('common.type')" width="90"><template #default="{ row }">{{ CT[row.ctype] || row.ctype }}</template></el-table-column>
        <el-table-column prop="product__label" :label="$t('common.product')" min-width="150" />
        <el-table-column :label="$t('common.status')" width="96"><template #default="{ row }"><HmTag :tone="STT[row.status]">{{ ST[row.status] }}</HmTag></template></el-table-column>
        <el-table-column :label="$t('ecn.wipWorkOrders')" width="104"><template #default="{ row }">{{ WP[row.wo_policy] || '—' }}</template></el-table-column>
        <el-table-column :label="$t('ecn.plannedEffective')" width="100"><template #default="{ row }"><span class="hm-num">{{ row.effective_date || '—' }}</span></template></el-table-column>
        <el-table-column prop="requested_by" :label="$t('common.requester')" width="86" />
        <template #empty><HmEmpty plain :title="$t('ecn.noEcns')" /></template>
      </el-table>
    </HmCard>

    <!-- Detail -->
    <div v-if="cur" class="mt" data-test="ecn-detail">
      <HmCard>
        <template #title><div class="tt"><span class="hm-mono code">{{ it.code }}</span><h3>{{ it.title }}</h3><HmTag :tone="STT[it.status]">{{ ST[it.status] }}</HmTag></div></template>
        <template #actions>
          <el-button v-for="a in ACTS[it.status] || []" :key="a" v-perm="'ecn.ecn.' + a" size="small" :type="a === 'reject' || a === 'cancel' ? 'danger' : 'primary'"
            :plain="!PRIMARY.includes(a)" :data-test="'ecn-act-' + a" :loading="busy === a" @click="doAct(a)">{{ AL[a] }}{{ a === 'analyze' && it.impact ? $t('ecn.redo') : '' }}</el-button>
        </template>
        <div class="info">
          <div class="kv"><span>{{ $t('ecn.changeType') }}</span><b>{{ CT[it.ctype] || '—' }}</b></div>
          <div class="kv"><span>{{ $t('common.product') }}</span><b>{{ it.product__label || '—' }}</b></div>
          <div class="kv"><span>{{ $t('ecn.wipWorkOrders') }}</span><b>{{ WP[it.wo_policy] || '—' }}</b></div>
          <div class="kv"><span>{{ $t('ecn.plannedEffective') }}</span><b class="hm-num">{{ it.effective_date || '—' }}</b></div>
          <div class="kv"><span>{{ $t('common.requester') }}</span><b>{{ it.requested_by || '—' }}</b></div>
          <div class="kv"><span>{{ $t('ecn.approver') }}</span><b>{{ it.approved_by || '—' }}</b></div>
          <div class="kv"><span>{{ $t('ecn.effectiveTime') }}</span><b class="hm-num">{{ fmtTime(it.effective_at) }}</b></div>
        </div>
        <div v-if="it.reason || it.description" class="txt">
          <p v-if="it.reason"><span class="k">{{ $t('common.reason') }}</span>{{ it.reason }}</p>
          <p v-if="it.description"><span class="k">{{ $t('common.content') }}</span>{{ it.description }}</p>
        </div>
        <el-steps :active="stepActive" finish-status="success" :process-status="ended ? 'error' : 'process'" align-center class="steps" data-test="ecn-steps">
          <el-step v-for="s in STEPS" :key="s.k" :title="s.t" :description="stepDesc(s.k)" />
        </el-steps>
      </HmCard>

      <!-- Impact analysis -->
      <HmCard :title="$t('common.impactAnalysis')" :sub="imp ? $t('ecn.listedAutomaticallyByProduct') : ''" class="mt">
        <div v-if="imp" data-test="ecn-impact">
          <div class="grid g-auto kpis" style="--min: 128px">
            <HmKpi compact :label="$t('nav.routings')" :value="imp.routes.length" format="int" :ai="false" />
            <HmKpi compact label="BOM" :value="imp.bom.length" format="int" :ai="false" />
            <HmKpi compact :label="$t('ecn.openWorkOrders')" :value="imp.work_orders.length" format="int" :status="imp.work_orders.length ? 'warn' : ''" :ai="false" />
            <HmKpi compact :label="$t('ecn.wip')" :value="imp.wip_total" format="int" :unit="$t('common.pcs')" :ai="false" />
            <HmKpi compact :label="$t('common.stock')" :value="imp.stock_total" format="num" :unit="$t('common.pcs')" :ai="false" />
            <HmKpi compact :label="$t('common.controlledDocuments')" :value="imp.docs.length" format="int" :ai="false" />
            <HmKpi compact :label="$t('common.inspectionTemplates')" :value="imp.templates.length" format="int" :ai="false" />
          </div>
          <div class="grid g-auto blks mt" style="--min: 400px">
            <div v-if="imp.routes.length" class="blk"><h4>{{ $t('nav.routings') }}</h4>
              <el-table :data="imp.routes" size="small"><el-table-column :label="$t('common.code')" min-width="110"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
                <el-table-column prop="name" :label="$t('common.name')" min-width="130" /><el-table-column prop="version" :label="$t('common.versions')" width="70" /><el-table-column :label="$t('ecn.step')" width="64" align="right"><template #default="{ row }"><span class="hm-num">{{ row.steps }}</span></template></el-table-column></el-table></div>
            <div v-if="imp.work_orders.length" class="blk"><h4>{{ $t('ecn.openWorkOrders') }}</h4>
              <el-table :data="imp.work_orders" size="small" max-height="240"><el-table-column :label="$t('common.workOrder')" min-width="130"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
                <el-table-column :label="$t('common.status')" width="86"><template #default="{ row }">{{ WOS[row.status] || row.status }}</template></el-table-column>
                <el-table-column :label="$t('ecn.doneQty')" width="96" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty_done || 0) }}/{{ fmtNum(row.qty) }}</span></template></el-table-column>
                <el-table-column :label="$t('common.dueDate')" width="110"><template #default="{ row }"><span class="hm-num">{{ fmtTime(row.due) }}</span></template></el-table-column></el-table></div>
            <div v-if="imp.wip_total" class="blk"><h4>{{ $t('ecn.wipByStatus') }}</h4>
              <div class="wip"><span v-for="(n, s) in imp.wip" :key="s" class="wi"><span>{{ PST[s] || s }}</span><b class="hm-num">{{ fmtNum(n) }}</b></span></div></div>
            <div v-if="imp.stock.length" class="blk"><h4>{{ $t('common.stock') }}</h4>
              <el-table :data="imp.stock" size="small" max-height="240"><el-table-column :label="$t('common.material')" min-width="120"><template #default="{ row }"><span class="hm-mono">{{ row.material }}</span></template></el-table-column>
                <el-table-column prop="warehouse" :label="$t('common.warehouse')" min-width="110" /><el-table-column prop="lot" :label="$t('common.lot')" min-width="100" />
                <el-table-column :label="$t('common.quantity')" width="80" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty) }}</span></template></el-table-column></el-table></div>
            <div v-if="imp.docs.length" class="blk"><h4>{{ $t('common.controlledDocuments') }}</h4>
              <el-table :data="imp.docs" size="small"><el-table-column :label="$t('common.no2')" width="96"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
                <el-table-column prop="title" :label="$t('common.name')" min-width="150" /><el-table-column :label="$t('common.type')" width="96"><template #default="{ row }">{{ DT[row.dtype] || row.dtype }}</template></el-table-column>
                <el-table-column :label="$t('common.versions')" width="60"><template #default="{ row }"><span class="hm-mono">{{ row.rev || '—' }}</span></template></el-table-column></el-table></div>
            <div v-if="imp.templates.length" class="blk"><h4>{{ $t('common.inspectionTemplates') }}</h4>
              <el-table :data="imp.templates" size="small"><el-table-column :label="$t('common.code')" min-width="130"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column><el-table-column prop="name" :label="$t('common.name')" min-width="150" /></el-table></div>
            <div v-if="plans.length" class="blk"><h4>{{ $t('ecn.controlPlanPfmea') }}</h4>
              <el-table :data="plans" size="small"><el-table-column prop="kind" :label="$t('common.type')" width="90" /><el-table-column :label="$t('common.no2')" min-width="120"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
                <el-table-column :label="$t('common.versions')" width="60"><template #default="{ row }"><span class="hm-mono">{{ row.rev }}</span></template></el-table-column>
                <el-table-column :label="$t('common.status')" width="80"><template #default="{ row }">{{ RST[row.status] || row.status }}</template></el-table-column></el-table></div>
            <div v-if="imp.bom.length" class="blk"><h4>BOM</h4>
              <el-table :data="imp.bom" size="small"><el-table-column :label="$t('common.product')" min-width="110"><template #default="{ row }"><span class="hm-mono">{{ row.product }}</span></template></el-table-column>
                <el-table-column :label="$t('ecn.component')" min-width="110"><template #default="{ row }"><span class="hm-mono">{{ row.component }}</span></template></el-table-column>
                <el-table-column :label="$t('ecn.usage')" width="70" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.qty) }}</span></template></el-table-column></el-table></div>
            <div v-if="imp.outsource.length" class="blk"><h4>{{ $t('common.outsourcingOrders') }}</h4>
              <el-table :data="imp.outsource" size="small"><el-table-column :label="$t('common.outsourcingOrders')" min-width="120"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
                <el-table-column prop="supplier" :label="$t('common.suppliers')" min-width="120" /><el-table-column :label="$t('common.status')" width="80"><template #default="{ row }">{{ OSS[row.status] || row.status }}</template></el-table-column></el-table></div>
          </div>
          <p v-if="imp.packages" class="muted small">{{ $t('ecn.unshippedPackagesCartons') }}<span class="hm-num">{{ imp.packages }}</span></p>
        </div>
        <HmEmpty v-else plain :title="it.product ? $t('ecn.noImpactAnalysisYetClick') : $t('ecn.noProductSpecifiedImpactAnalysis')" />
      </HmCard>

      <div class="grid g2 mt">
        <!-- Implementation tasks -->
        <HmCard :title="$t('ecn.implementationTasks01', [tasksDone, cur.tasks.length])" flush>
          <el-table :data="cur.tasks" size="small" data-test="ecn-tasks">
            <el-table-column prop="title" :label="$t('common.task')" min-width="200" />
            <el-table-column :label="$t('common.status')" width="80"><template #default="{ row }"><HmTag :tone="row.status === 'done' ? 'ok' : row.status === 'skipped' ? 'neutral' : 'warn'">{{ TS[row.status] }}</HmTag></template></el-table-column>
            <el-table-column :label="$t('common.done')" min-width="110"><template #default="{ row }"><template v-if="row.done_by">{{ row.done_by }} <span class="hm-num muted">{{ fmtTime(row.done_at) }}</span></template><div v-if="row.note" class="muted small">{{ row.note }}</div></template></el-table-column>
            <el-table-column label="" width="128"><template #default="{ row }">
              <template v-if="row.status === 'open' && live">
                <el-button v-perm="'ecn.ecn_task.done'" size="small" type="primary" plain data-test="ecn-task-done" @click="taskDone(row, false)">{{ $t('common.done') }}</el-button>
                <el-button v-perm="'ecn.ecn_task.done'" size="small" link data-test="ecn-task-skip" @click="taskDone(row, true)">{{ $t('ecn.nA') }}</el-button>
              </template>
            </template></el-table-column>
            <template #empty><span class="muted">{{ $t('ecn.generatedAutomaticallyAfterImpactAna') }}</span></template>
          </el-table>
        </HmCard>
        <!-- WIP work orders -->
        <HmCard :title="$t('ecn.wipWorkOrderDisposition')" :sub="WP[it.wo_policy]" flush>
          <el-table :data="cur.work_orders" size="small" data-test="ecn-wos">
            <el-table-column :label="$t('common.workOrder')" min-width="130"><template #default="{ row }"><span class="hm-mono">{{ row.work_order__label }}</span></template></el-table-column>
            <el-table-column :label="$t('common.disposition')" width="90"><template #default="{ row }"><HmTag :tone="row.decision === 'block' ? 'bad' : 'ok'">{{ row.decision === 'block' ? $t('ecn.stopWork') : $t('ecn.continueAsIs') }}</HmTag></template></el-table-column>
            <el-table-column :label="$t('ecn.release')" min-width="120"><template #default="{ row }">
              <template v-if="row.released"><HmTag tone="ok">{{ $t('ecn.released') }}</HmTag> <span class="small">{{ row.released_by }}</span></template><span v-else class="muted">—</span>
            </template></el-table-column>
            <el-table-column label="" width="100"><template #default="{ row }">
              <el-button v-if="row.decision === 'block' && !row.released" v-perm="'ecn.ecn_wo.unblock'" size="small" type="primary" plain data-test="ecn-wo-unblock" @click="unblock(row)">{{ $t('ecn.releaseStop') }}</el-button>
            </template></el-table-column>
            <template #empty><span class="muted">{{ $t('ecn.noWipWorkOrdersTo') }}</span></template>
          </el-table>
        </HmCard>
      </div>

      <!-- Document versions -->
      <HmCard :title="$t('ecn.documentVersions')" :sub="$t('ecn.reviseUnderTheEcnTakes')" flush class="mt">
        <template #actions><el-button v-if="live" v-perm="'doc.ctrl_doc.revise'" size="small" type="primary" plain data-test="ecn-revise" @click="openRevise">{{ $t('ecn.reviseDocument') }}</el-button></template>
        <el-table :data="cur.revisions" size="small" data-test="ecn-revs">
          <el-table-column :label="$t('common.file')" min-width="220"><template #default="{ row }"><span class="hm-mono">{{ row.doc_code }}</span> {{ row.doc_title }}</template></el-table-column>
          <el-table-column :label="$t('ecn.newVersion')" width="80"><template #default="{ row }"><b class="hm-mono">{{ row.rev }}</b></template></el-table-column>
          <el-table-column :label="$t('ecn.current')" width="70"><template #default="{ row }"><span class="hm-mono">{{ row.doc_cur_rev || '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('common.status')" width="90"><template #default="{ row }"><HmTag :tone="RT[row.status]">{{ RST[row.status] }}</HmTag></template></el-table-column>
          <el-table-column :label="$t('ecn.preparedApproved')" min-width="120"><template #default="{ row }">{{ row.author || '—' }} / {{ row.approved_by || '—' }}</template></el-table-column>
          <el-table-column label="" width="250"><template #default="{ row }">
            <el-button v-if="row.status === 'draft'" size="small" link type="primary" @click="router.push(`/e/${row.target_entity}?id=${row.target_id}`)">{{ $t('docControl.editContent') }}</el-button>
            <el-button v-if="row.status === 'draft'" v-perm="'doc.ctrl_doc_rev.submit'" size="small" type="primary" plain data-test="ecn-rev-submit" @click="revAct(row, 'submit')">{{ $t('docControl.submitForReview') }}</el-button>
            <el-button v-if="row.status === 'review'" v-perm="'doc.ctrl_doc_rev.approve'" size="small" type="primary" data-test="ecn-rev-approve" @click="revAct(row, 'approve')">{{ $t('docControl.approveESignature') }}</el-button>
            <el-button size="small" link type="primary" @click="router.push('/exec/docs?id=' + row.doc)">{{ $t('docControl.versionHistory') }}</el-button>
          </template></el-table-column>
          <template #empty><span class="muted">{{ $t('ecn.noDocumentsRevisedUnderThis') }}</span></template>
        </el-table>
      </HmCard>

      <!-- Effectivity log -->
      <HmCard v-if="it.snapshot" :title="$t('ecn.effectivityRecord')" :sub="$t('ecn.effectiveAt') + fmtTime(it.snapshot.at, 'full')" class="mt" data-test="ecn-snapshot">
        <div class="grid g2">
          <el-table :data="it.snapshot.docs" size="small">
            <el-table-column :label="$t('common.file')" min-width="200"><template #default="{ row }"><span class="hm-mono">{{ row.doc }}</span> {{ row.title }}</template></el-table-column>
            <el-table-column :label="$t('ecn.versionSwitch')" width="140"><template #default="{ row }"><span class="hm-mono">{{ (row.old || []).join($t('common.k42')) || '—' }} → {{ row.new }}</span></template></el-table-column>
            <template #empty><span class="muted">{{ $t('ecn.noDocumentSwitches') }}</span></template>
          </el-table>
          <el-table :data="it.snapshot.work_orders" size="small">
            <el-table-column :label="$t('common.workOrder')" min-width="130"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
            <el-table-column :label="$t('common.status')" width="90"><template #default="{ row }">{{ WOS[row.status] || row.status }}</template></el-table-column>
            <el-table-column :label="$t('common.disposition')" width="140"><template #default="{ row }">{{ row.decision === 'block' ? (row.released ? $t('ecn.stoppedReleased') : $t('ecn.stoppedAwaitingDisposition')) : $t('ecn.continueAsIs') }}</template></el-table-column>
            <template #empty><span class="muted">{{ $t('common.noWorkOrdersInProgress') }}</span></template>
          </el-table>
        </div>
      </HmCard>

      <!-- E-signature -->
      <HmCard v-if="cur.signatures?.length" :title="$t('complaints.eSignature')" class="mt">
        <div class="sigs"><span v-for="s in cur.signatures" :key="s.id" class="sig"><el-icon><EditPen /></el-icon>{{ $t('ecn.n012', [s.name || s.user, s.user, s.meaning]) }} <span class="hm-num">{{ fmtTime(s.signed_at, 'full') }}</span></span></div>
      </HmCard>
    </div>

    <!-- New -->
    <el-dialog v-model="nw.open" :title="$t('ecn.newEngineeringChangeEcn')" width="min(620px, 96vw)" destroy-on-close>
      <el-form label-width="84px" data-test="ecn-new-form">
        <el-form-item :label="$t('ecn.changeTitle')" required><el-input v-model="nw.f.title" maxlength="190" data-test="ecn-new-title" /></el-form-item>
        <el-form-item :label="$t('ecn.changeType')"><el-select v-model="nw.f.ctype" style="width: 200px"><el-option v-for="(v, k) in CT" :key="k" :value="k" :label="v" /></el-select></el-form-item>
        <el-form-item :label="$t('common.product')"><RefSelect v-model="nw.f.product" entity="material" /></el-form-item>
        <el-form-item :label="$t('ecn.reasonForChange')"><el-input v-model="nw.f.reason" type="textarea" :rows="2" /></el-form-item>
        <el-form-item :label="$t('ecn.changeContent')"><el-input v-model="nw.f.description" type="textarea" :rows="3" /></el-form-item>
        <el-form-item :label="$t('ecn.wipWorkOrders')">
          <el-radio-group v-model="nw.f.wo_policy"><el-radio v-for="(v, k) in WP" :key="k" :value="k">{{ v }}</el-radio></el-radio-group>
        </el-form-item>
        <el-form-item :label="$t('ecn.plannedEffective')"><el-date-picker v-model="nw.f.effective_date" type="date" value-format="YYYY-MM-DD" :placeholder="$t('ecn.optional')" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="nw.open = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!nw.f.title?.trim()" :loading="nw.busy" data-test="ecn-new-save" @click="saveNew">{{ $t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- Revise -->
    <el-dialog v-model="rv.open" :title="$t('ecn.reviseControlledDocumentUnderEcn')" width="min(560px, 96vw)" destroy-on-close>
      <el-select v-model="rv.doc" filterable :placeholder="$t('ecn.selectControlledDocument')" style="width: 100%" data-test="ecn-revise-pick">
        <el-option v-for="d in rv.docs" :key="d.id" :value="d.id" :label="$t('ecn.n01Current2', [d.code, d.title, d.cur_rev || '—'])" />
      </el-select>
      <p class="muted small">{{ $t('ecn.createsANewDraftVersion') }}</p>
      <template #footer>
        <el-button @click="rv.open = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!rv.doc" :loading="rv.busy" data-test="ecn-revise-ok" @click="doRevise">{{ $t('docControl.revise') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { call, get, post } from '../api';
import RefSelect from '../components/RefSelect.vue';
import { fmtNum, fmtTime } from '../design';

const router = useRouter(), route = useRoute();
const ask = <T,>(p: Promise<T>) => p.then((v) => v, () => null);
const ST: Record<string, string> = { request: $t('ecn.requested'), analysis: $t('common.impactAnalysis'), approval: $t('ecn.pendingApproval'), implementing: $t('ecn.implementing'), effective: $t('ecn.effective'), rejected: $t('common.rejected'), cancelled: $t('common.cancelled') };
const STT: Record<string, any> = { request: 'neutral', analysis: 'primary', approval: 'warn', implementing: 'primary', effective: 'ok', rejected: 'bad', cancelled: 'neutral' };
const CT: Record<string, string> = { process: $t('nav.process'), design: $t('ecn.designDrawing'), material: $t('ecn.material'), supplier: $t('common.suppliers'), cp: $t('common.controlPlan'), other: $t('common.other') };
const WP: Record<string, string> = { allow: $t('ecn.continueAsIs'), block: $t('ecn.stoppedAwaitingDisposition'), finish: $t('ecn.finishThenSwitch') };
const DT: Record<string, string> = { wi: $t('common.workInstruction'), drawing: $t('common.drawing'), control_plan: $t('common.controlPlan'), pfmea: 'PFMEA', nc: $t('common.ncPrograms'), other: $t('common.other') };
const RST: Record<string, string> = { draft: $t('common.draft'), review: $t('common.inReview'), approved: $t('common.approved2'), effective: $t('common.effective'), obsolete: $t('common.void') };
const RT: Record<string, any> = { draft: 'neutral', review: 'warn', approved: 'primary', effective: 'ok', obsolete: 'neutral' };
const WOS: Record<string, string> = { draft: $t('common.draft'), released: $t('common.released'), in_progress: $t('common.inProduction'), done: $t('common.complete'), closed: $t('common.off'), cancelled: $t('common.cancelled') };
const PST: Record<string, string> = { wip: $t('common.wip'), hold: $t('common.held'), rework: $t('common.rework'), good: $t('ecn.goodAwaitingStorage'), raw_store: $t('common.rawCastingStore') };
const OSS: Record<string, string> = { draft: $t('common.draft'), sent: $t('common.sentOut'), partial: $t('ecn.partiallyReceived') };
const TS: Record<string, string> = { open: $t('ecn.toDo'), done: $t('common.completed'), skipped: $t('ecn.nA') };
const AL: Record<string, string> = { analyze: $t('common.impactAnalysis'), submit: $t('ecn.submitForApproval'), approve: $t('docControl.approveESignature'), reject: $t('common.reject'), effective: $t('common.effective'), cancel: $t('common.cancel') };
const ACTS: Record<string, string[]> = { request: ['analyze', 'cancel'], analysis: ['analyze', 'submit', 'reject', 'cancel'], approval: ['approve', 'reject', 'cancel'], implementing: ['effective', 'cancel'] };
const PRIMARY = ['submit', 'approve', 'effective'];
const STEPS = [{ k: 'request', t: $t('ecn.requested') }, { k: 'analysis', t: $t('common.impactAnalysis') }, { k: 'approval', t: $t('common.approve3') }, { k: 'implementing', t: $t('nav.implementation') }, { k: 'effective', t: $t('common.effective') }];

const list = ref<any[]>([]), cur = ref<any>(null), busy = ref('');
const flt = ref('open'), q = ref('');
const nw = reactive<any>({ open: false, busy: false, f: {} });
const rv = reactive<any>({ open: false, busy: false, doc: null, docs: [] });

const shownList = computed(() => list.value.filter((r) => (!flt.value || (flt.value === 'open') === !['effective', 'rejected', 'cancelled'].includes(r.status))
  && (!q.value || `${r.code} ${r.title}`.toLowerCase().includes(q.value.toLowerCase()))));
const it = computed(() => cur.value?.item || {});
const imp = computed(() => it.value.impact || null);
const ended = computed(() => ['rejected', 'cancelled'].includes(it.value.status));
const live = computed(() => !['effective', 'rejected', 'cancelled'].includes(it.value.status));
const tasksDone = computed(() => (cur.value?.tasks || []).filter((t: any) => t.status !== 'open').length);
const plans = computed(() => imp.value ? [...imp.value.control_plans.map((x: any) => ({ ...x, kind: $t('common.controlPlan') })), ...imp.value.pfmea.map((x: any) => ({ ...x, kind: 'PFMEA' }))] : []);
const stepActive = computed(() => {
  const s = it.value.status;
  if (s === 'effective') return 5;
  if (ended.value) return it.value.approved_by ? 3 : it.value.impact ? 2 : 0;
  return Math.max(0, STEPS.findIndex((x) => x.k === s));
});
function stepDesc(k: string) {
  const e = it.value;
  if (k === 'request') return e.requested_by || '';
  if (k === 'analysis') return e.impact ? $t('ecn.n0Tasks', [cur.value.tasks.length]) : '';
  if (k === 'approval') return e.approved_by ? `${e.approved_by} ${fmtTime(e.approved_at)}` : '';
  if (k === 'implementing') return cur.value.tasks.length ? $t('ecn.done01', [tasksDone.value, cur.value.tasks.length]) : '';
  if (k === 'effective') return e.effective_at ? fmtTime(e.effective_at) : ended.value ? ST[e.status] : '';
  return '';
}

async function loadList() { list.value = (await get('/api/e/ecn?size=100&sort=-id')).items; }
async function select(id: number) {
  cur.value = await get(`/api/ecn/${id}/full`);
  if (String(route.query.id) !== String(id)) router.replace({ query: { ...route.query, id } });
}
async function reload() { await Promise.all([loadList(), cur.value ? select(cur.value.item.id) : null]); }

async function doAct(a: string) {
  const id = it.value.id;
  let body: any = {};
  if (a === 'reject') {
    const p: any = await ask(ElMessageBox.prompt($t('ecn.rejectionReason'), $t('ecn.reject0', [it.value.code]), { inputValidator: (v: string) => !!v?.trim() || $t('common.pleaseEnterAReason') }));
    if (!p) return;
    body = { reason: p.value };
  } else if (a === 'cancel') {
    if (!(await ask(ElMessageBox.confirm($t('ecn.cancel0ThisCannotBe', [it.value.code]), $t('ecn.cancelEcn'), { type: 'warning' })))) return;
  }
  busy.value = a;
  try {
    let r: any;
    if (a === 'effective') {
      try { r = await call('POST', `/api/e/ecn/${id}/do/effective`, {}, true); }
      catch (e: any) {
        const msg = String(e?.message || e);
        if (!/implementation task|\u5b9e\u65bd\u4efb\u52a1/i.test(msg)) { ElMessage.error(msg); return; }
        if (!(await ask(ElMessageBox.confirm(msg + $t('ecn.forceItToTakeEffect'), $t('ecn.implementationTasksNotFinished'), { type: 'warning', confirmButtonText: $t('ecn.forceEffective'), cancelButtonText: $t('ecn.finishTasksFirst') })))) return;
        r = await post(`/api/e/ecn/${id}/do/effective`, { force: true });
      }
    } else r = await post(`/api/e/ecn/${id}/do/${a}`, body);
    ElMessage.success(r.msg || $t('common.completed'));
  } finally { busy.value = ''; await reload(); }
}
async function taskDone(t: any, skip: boolean) {
  let note = '';
  if (skip) {
    const p: any = await ask(ElMessageBox.prompt($t('ecn.reasonItDoesnTApply'), t.title, { inputValue: '' }));
    if (!p) return;
    note = p.value || '';
  }
  const r = await post(`/api/e/ecn_task/${t.id}/do/done`, skip ? { skip: true, note } : {});
  ElMessage.success(r.msg); await select(it.value.id);
}
async function unblock(w: any) {
  const p: any = await ask(ElMessageBox.prompt($t('ecn.releaseNoteEGContinue'), $t('ecn.releaseStop0', [w.work_order__label]), { inputValue: $t('ecn.continueOnNewVersion') }));
  if (!p) return;
  const r = await post(`/api/e/ecn_wo/${w.id}/do/unblock`, { note: p.value }); ElMessage.success(r.msg); await select(it.value.id);
}
async function revAct(r: any, a: string) {
  const res = await post(`/api/e/ctrl_doc_rev/${r.id}/do/${a}`, {}); ElMessage.success(res.msg); await select(it.value.id);
}
async function openRevise() {
  rv.open = true; rv.doc = null;
  const docs = (await get('/api/doc/list')).filter((d: any) => d.status !== 'obsolete');
  const pid = it.value.product;
  rv.docs = docs.sort((a: any, b: any) => Number(b.product === pid) - Number(a.product === pid));
}
async function doRevise() {
  rv.busy = true;
  try {
    const r = await post(`/api/ecn/${it.value.id}/revise`, { doc: rv.doc });
    rv.open = false; await select(it.value.id);
    const go = await ElMessageBox.confirm($t('ecn.n012EditThe', [r.new ? $t('ecn.newVersionCreated') : $t('ecn.anUnreleasedVersionAlreadyExists'), r.rev, r.new ? $t('common.draft') : $t('ecn.editThatOne')]), $t('ecn.reviseDocument'),
      { confirmButtonText: $t('ecn.editContent'), cancelButtonText: $t('ecn.later'), type: 'success' }).then(() => true).catch(() => false);
    if (go) router.push(`/e/${r.target_entity}?id=${r.target_id}`);
  } finally { rv.busy = false; }
}

function openNew() { nw.f = { title: '', ctype: 'process', product: null, reason: '', description: '', wo_policy: 'finish', effective_date: null }; nw.open = true; }
async function saveNew() {
  nw.busy = true;
  try {
    const r = await post('/api/e/ecn', { ...nw.f, title: nw.f.title.trim() });
    ElMessage.success($t('ecn.ecnCreated')); nw.open = false; flt.value = 'open';
    await loadList(); await select(r.id);
  } finally { nw.busy = false; }
}

watch(() => route.query.id, (v) => { if (v && Number(v) !== cur.value?.item.id) select(Number(v)); });
onMounted(async () => {
  await loadList();
  const id = Number(route.query.id) || shownList.value[0]?.id;
  if (id) await select(id);
});
</script>

<style scoped>
.steps :deep(.is-wait) { color: var(--hm-text-2); border-color: var(--hm-text-2); }
.steps :deep(.el-step__title.is-wait), .steps :deep(.el-step__head.is-wait .el-step__icon) { color: var(--hm-text-2); }
.muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-xs); }
.clickable :deep(tbody tr) { cursor: pointer; }
.clickable :deep(tr.cur > td) { background: var(--hm-primary-soft) !important; }
.tt { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
.tt h3 { margin: 0; font-size: var(--hm-fs-md); font-weight: 600; color: var(--hm-text); }
.code { color: var(--hm-text-2); }
.info { display: flex; gap: 22px; flex-wrap: wrap; padding: 4px 0 8px; }
.kv { display: flex; flex-direction: column; gap: 2px; font-size: var(--hm-fs-xs); }
.kv span { color: var(--hm-text-2); } .kv b { font-weight: 600; color: var(--hm-text); font-size: var(--hm-fs-sm); }
.txt p { margin: 2px 0; font-size: var(--hm-fs-sm); color: var(--hm-text); white-space: pre-wrap; }
.txt .k { color: var(--hm-text-2); margin-right: 8px; font-size: var(--hm-fs-xs); }
.steps { margin-top: 12px; }
.blk { border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); padding: 8px 10px 6px; min-width: 0; }
.blk h4 { margin: 0 0 6px; font-size: var(--hm-fs-sm); font-weight: 600; color: var(--hm-text); }
.wip { display: flex; gap: 10px; flex-wrap: wrap; padding: 4px 0; }
.wi { display: inline-flex; flex-direction: column; padding: 6px 12px; background: var(--hm-surface-2); border-radius: var(--hm-r-sm); font-size: var(--hm-fs-xs); color: var(--hm-text-2); }
.wi b { font-size: var(--hm-fs-lg); color: var(--hm-text); }
.sigs { display: flex; gap: 10px; flex-wrap: wrap; }
.sig { display: inline-flex; align-items: center; gap: 4px; font-size: var(--hm-fs-xs); color: var(--hm-primary); background: var(--hm-primary-soft); padding: 3px 10px; border-radius: var(--hm-r-pill); }
@media (max-width: 1279px) { .g2 { grid-template-columns: minmax(0, 1fr); } }
</style>
