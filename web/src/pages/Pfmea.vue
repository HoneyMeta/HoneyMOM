<template>
  <div class="page pf">
    <HmPageHead :title="$t('common.pfmeaControlPlan')" :sub="$t('pfmea.pfmeaComputesApPerAiag')">
      <el-button v-perm:disable="'iatf.pfmea.create'" @click="newFmea"><el-icon><Plus /></el-icon>{{ $t('pfmea.newPfmea') }}</el-button>
    </HmPageHead>

    <div class="lay">
      <!-- Left list -->
      <HmCard flush class="side" data-test="fmea-list">
        <div class="grp">PFMEA <span class="hm-num t3">{{ fmeas.length }}</span></div>
        <button v-for="f in fmeas" :key="'f' + f.id" type="button" class="it" :class="{ on: sel.kind === 'f' && sel.id === f.id }" :data-test="'fmea-item-' + f.id" @click="pick('f', f.id)">
          <span class="hm-mono c">{{ f.code }}</span>
          <span class="n">{{ f.title }}</span>
          <span class="m"><HmTag :tone="FST_T[f.status] || 'neutral'">{{ FST[f.status] || f.status }}</HmTag><span class="t3 small">{{ $t('common.version0', [f.rev || '—']) }}</span></span>
        </button>
        <HmEmpty v-if="!fmeas.length" plain :title="$t('pfmea.noPfmeaYet')" />
        <div class="grp">{{ $t('common.controlPlan') }} <span class="hm-num t3">{{ cps.length }}</span></div>
        <button v-for="p in cps" :key="'c' + p.id" type="button" class="it" :class="{ on: sel.kind === 'c' && sel.id === p.id }" :data-test="'cp-item-' + p.id" @click="pick('c', p.id)">
          <span class="hm-mono c">{{ p.code }}</span>
          <span class="n">{{ p.title }}</span>
          <span class="m"><HmTag :tone="DST_T[p.status] || 'neutral'">{{ DST[p.status] || p.status }}</HmTag><span class="t3 small">{{ $t('pfmea.n0Rev1', [PH[p.phase] || '', p.rev || '—']) }}</span></span>
        </button>
        <HmEmpty v-if="!cps.length" plain :title="$t('pfmea.noControlPlanYet')" />
      </HmCard>

      <!-- PFMEA -->
      <div v-if="sel.kind === 'f' && fm" class="main">
        <HmCard>
          <div class="hd">
            <div class="ttl"><span class="hm-mono">{{ fm.item.code }}</span> {{ fm.item.title }}</div>
            <HmTag :tone="FST_T[fm.item.status] || 'neutral'">{{ FST[fm.item.status] || fm.item.status }}</HmTag>
            <span class="grow" />
            <el-button v-if="aiOn('quality_plus')" class="ai-btn" :loading="busy === 'ai'" data-test="fmea-ai" @click="aiDraft"><el-icon><MagicStick /></el-icon>{{ $t('pfmea.aiDraftFailureModes') }}</el-button>
            <el-button v-perm:disable="'iatf.pfmea_row.create'" data-test="fmea-add" @click="addRow"><el-icon><Plus /></el-icon>{{ $t('pfmea.newItem') }}</el-button>
            <el-button v-perm:disable="'iatf.pfmea.derive_cp'" type="primary" :loading="busy === 'derive'" :disabled="!fm.rows.length" data-test="fmea-derive" @click="derive">{{ $t('pfmea.generateControlPlan') }}</el-button>
          </div>
          <div class="meta">
            <span>{{ $t('common.product') }} <b>{{ fm.item.product__label || '—' }}</b></span><span>{{ $t('common.versions') }} <b>{{ fm.item.rev || '—' }}</b></span>
            <span>{{ $t('common.method2') }} <b>{{ fm.item.method === 'rpn' ? $t('pfmea.rpn4thEd') : $t('pfmea.apAiagVda2019') }}</b></span><span>{{ $t('common.date') }} <b class="hm-num">{{ fm.item.fmea_date || '—' }}</b></span>
            <span class="wide">{{ $t('pfmea.team') }} <b>{{ fm.item.team || '—' }}</b></span>
            <span v-if="fm.item.scope" class="wide">{{ $t('pfmea.scope') }} <b>{{ fm.item.scope }}</b></span>
          </div>
          <div class="apc">
            <div class="ap-k h"><span class="l">{{ $t('pfmea.apHigh') }}</span><span class="hm-num v">{{ fm.ap_counts.H || 0 }}</span></div>
            <div class="ap-k m"><span class="l">{{ $t('pfmea.apMedium') }}</span><span class="hm-num v">{{ fm.ap_counts.M || 0 }}</span></div>
            <div class="ap-k lo"><span class="l">{{ $t('pfmea.apLow') }}</span><span class="hm-num v">{{ fm.ap_counts.L || 0 }}</span></div>
            <div class="legend">
              <b>{{ $t('pfmea.actionPriorityAiagVda2019') }}</b>
              <span><HmTag tone="bad">H</HmTag> {{ $t('pfmea.highActionIsRequiredTo') }}</span>
              <span><HmTag tone="warn">M</HmTag> {{ $t('pfmea.mediumActionShouldBeTaken') }}</span>
              <span><HmTag tone="ok">L</HmTag> {{ $t('pfmea.lowActionCouldBeTaken') }}</span>
            </div>
          </div>
          <div v-if="fm.plans.length" class="plans small t2">{{ $t('pfmea.generatedControlPlans') }}
            <a v-for="p in fm.plans" :key="p.id" class="lnk" @click="pick('c', p.id)"><span class="hm-mono">{{ p.code }}</span> {{ DST[p.status] || p.status }}</a>
          </div>
        </HmCard>

        <HmCard :title="$t('pfmea.pfmeaItems')" :sub="$t('pfmea.n0ItemsEditSO', [fm.rows.length])" flush class="mt">
          <el-table :data="fm.rows" size="small" data-test="fmea-rows" row-key="id"
            v-ai-rows="{ entity: 'pfmea_row', rows: () => fm.rows, label: (r: any) => `PFMEA ${r.seq} ${r.failure_mode || ''} AP ${r.ap || '—'}`, data: (r: any) => ({ [$t('pfmea.process')]: r.process__label, [$t('pfmea.failureMode')]: r.failure_mode, [$t('pfmea.effect')]: r.effect, [$t('common.reason')]: r.cause, S: r.severity, O: r.occurrence, D: r.detection, RPN: r.rpn, AP: r.ap }) }">
            <el-table-column :label="$t('pfmea.k4421')" width="48" align="right"><template #default="{ row }"><span class="hm-num">{{ row.seq }}</span></template></el-table-column>
            <el-table-column prop="process__label" :label="$t('pfmea.processStep')" width="110" />
            <el-table-column prop="function" :label="$t('pfmea.function')" min-width="120" />
            <el-table-column prop="product_char" :label="$t('pfmea.productCharacteristic')" width="110" />
            <el-table-column :label="$t('pfmea.characteristicCode')" width="92"><template #default="{ row }"><span class="hm-mono">{{ row.char || '—' }}</span></template></el-table-column>
            <el-table-column :label="$t('pfmea.special')" width="66"><template #default="{ row }"><HmTag v-if="row.special === 'CC'" tone="bad">◆ CC</HmTag><HmTag v-else-if="row.special === 'SC'" tone="amber">▲ SC</HmTag></template></el-table-column>
            <el-table-column prop="failure_mode" :label="$t('pfmea.failureMode')" min-width="120" />
            <el-table-column prop="effect" :label="$t('pfmea.effect')" min-width="140" />
            <el-table-column label="S" width="58" align="center"><template #default="{ row }"><el-input-number v-model="row.severity" class="sod" :min="1" :max="10" :controls="false" size="small" data-test="fmea-s" @change="saveSod(row)" /></template></el-table-column>
            <el-table-column prop="cause" :label="$t('common.reason')" min-width="140" />
            <el-table-column label="O" width="58" align="center"><template #default="{ row }"><el-input-number v-model="row.occurrence" class="sod" :min="1" :max="10" :controls="false" size="small" data-test="fmea-o" @change="saveSod(row)" /></template></el-table-column>
            <el-table-column prop="detection_ctrl" :label="$t('pfmea.detectionControls')" min-width="120" />
            <el-table-column label="D" width="58" align="center"><template #default="{ row }"><el-input-number v-model="row.detection" class="sod" :min="1" :max="10" :controls="false" size="small" data-test="fmea-d" @change="saveSod(row)" /></template></el-table-column>
            <el-table-column label="RPN" width="58" align="right"><template #default="{ row }"><span class="hm-num">{{ row.rpn ?? '—' }}</span></template></el-table-column>
            <el-table-column label="AP" width="54" align="center"><template #default="{ row }"><HmTag v-if="row.ap" :tone="AP_T[row.ap]" :data-test="'fmea-ap-' + row.id">{{ row.ap }}</HmTag></template></el-table-column>
            <el-table-column :label="$t('ncr.actions')" min-width="130"><template #default="{ row }">
              <span>{{ row.actions || '—' }}</span>
              <HmTag v-if="row.action_status" :tone="row.action_status === 'done' ? 'ok' : 'warn'" class="ml">{{ row.action_status === 'done' ? $t('common.completed') : $t('implHome.open') }}</HmTag>
            </template></el-table-column>
            <el-table-column label="" width="96" fixed="right"><template #default="{ row }">
              <el-button link type="primary" size="small" data-test="fmea-edit" @click="editRow(row)">{{ $t('common.edit') }}</el-button>
              <el-button link type="danger" size="small" @click="delRow(row)">{{ $t('common.delete') }}</el-button>
            </template></el-table-column>
          </el-table>
          <HmEmpty v-if="!fm.rows.length" plain :title="$t('pfmea.noItemsYetClickNew')" />
        </HmCard>
      </div>

      <!-- Control plan -->
      <div v-else-if="sel.kind === 'c' && cp" class="main">
        <HmCard>
          <div class="hd">
            <div class="ttl"><span class="hm-mono">{{ cp.item.code }}</span> {{ cp.item.title }}</div>
            <HmTag :tone="DST_T[cp.item.status] || 'neutral'">{{ DST[cp.item.status] || cp.item.status }}</HmTag>
            <span class="grow" />
            <el-button v-perm:disable="'iatf.cp_item.create'" data-test="cp-add" @click="addItem"><el-icon><Plus /></el-icon>{{ $t('pfmea.newRow') }}</el-button>
            <el-button v-perm:disable="'iatf.control_plan.gen_insp'" type="primary" :loading="busy === 'gen'" data-test="cp-gen" @click="cpAct('gen_insp')">{{ $t('pfmea.generateSyncInspectionTemplates') }}</el-button>
            <el-button v-perm:disable="'iatf.control_plan.to_doc'" :disabled="!!cp.item.ctrl_doc" :title="cp.item.ctrl_doc ? $t('pfmea.controlled') : ''" :loading="busy === 'doc'" data-test="cp-doc" @click="cpAct('to_doc')">{{ $t('docControl.bringDocumentUnderControl') }}</el-button>
            <el-button data-test="cp-print" @click="printCp"><el-icon><Printer /></el-icon>{{ $t('pfmea.print') }}</el-button>
          </div>
          <div class="meta">
            <span>{{ $t('common.product') }} <b>{{ cp.item.product__label || '—' }}</b></span><span>{{ $t('common.phase') }} <b>{{ PH[cp.item.phase] || cp.item.phase || '—' }}</b></span>
            <span>{{ $t('common.versions') }} <b>{{ cp.item.rev || '—' }}</b></span>
            <span>{{ $t('pfmea.sourcePfmea') }} <a v-if="cp.item.pfmea" class="lnk" @click="pick('f', cp.item.pfmea)"><span class="hm-mono">{{ cp.item.pfmea__label }}</span></a><b v-else>—</b></span>
            <span>{{ $t('common.controlledDocuments') }} <b class="hm-mono">{{ cp.item.ctrl_doc__label || $t('pfmea.notControlled') }}</b></span>
            <span class="wide">{{ $t('pfmea.team') }} <b>{{ cp.item.team || '—' }}</b></span>
          </div>
          <div v-if="Object.keys(cp.templates || {}).length" class="plans small t2">{{ $t('pfmea.inspectionTemplates') }}
            <a v-for="(id, code) in cp.templates" :key="code" class="lnk hm-mono" @click="router.push('/e/insp_template?id=' + id)">{{ code }}</a>
          </div>
        </HmCard>

        <HmCard :title="$t('pfmea.controlPlanAiagFormat')" :sub="$t('pfmea.n0RowsCriticalCharacteristicCc', [cp.items.length])" flush class="mt">
          <el-table :data="cp.items" size="small" data-test="cp-items" row-key="id"
            v-ai-rows="{ entity: 'cp_item', rows: () => cp.items, label: (r: any) => $t('pfmea.controlPlan01', [r.char_no || r.seq, r.product_char || '']), data: (r: any) => ({ [$t('pfmea.process')]: r.process__label, [$t('pfmea.productCharacteristic')]: r.product_char, [$t('pfmea.specialCharacteristics')]: r.special, [$t('pfmea.specification')]: r.spec, [$t('common.method2')]: r.method, [$t('erpDocs.sample')]: r.sample_size, [$t('implReports.frequency')]: r.frequency, [$t('pfmea.control')]: r.control_method, [$t('pfmea.reactionPlan')]: r.reaction }) }">
            <el-table-column prop="process__label" :label="$t('pfmea.process')" width="104" />
            <el-table-column prop="machine" :label="$t('pfmea.machineDevice')" width="96" />
            <el-table-column :label="$t('common.no2')" width="52"><template #default="{ row }"><span class="hm-mono">{{ row.char_no || '—' }}</span></template></el-table-column>
            <el-table-column prop="product_char" :label="$t('pfmea.productCharacteristic')" width="110" />
            <el-table-column prop="process_char" :label="$t('pfmea.processCharacteristic')" width="96" />
            <el-table-column :label="$t('pfmea.specialCharacteristics')" width="74" align="center"><template #default="{ row }"><HmTag v-if="row.special === 'CC'" tone="bad">◆ CC</HmTag><HmTag v-else-if="row.special === 'SC'" tone="amber">▲ SC</HmTag></template></el-table-column>
            <el-table-column :label="$t('pfmea.specTolerance')" width="110"><template #default="{ row }"><span class="hm-num">{{ row.spec || '—' }}</span></template></el-table-column>
            <el-table-column prop="method" :label="$t('pfmea.evaluationMeasurementTechnique')" min-width="120" />
            <el-table-column :label="$t('pfmea.gaugeType')" width="106"><template #default="{ row }">
              <el-select v-model="row.gauge_type" size="small" clearable placeholder="—" data-test="cp-gtype" @change="saveItem(row, { gauge_type: row.gauge_type || null })">
                <el-option v-for="(t, k) in cp.gauge_types" :key="k" :value="k" :label="t" />
              </el-select>
            </template></el-table-column>
            <el-table-column prop="sample_size" :label="$t('erpDocs.sample')" width="70" />
            <el-table-column prop="frequency" :label="$t('implReports.frequency')" min-width="110" />
            <el-table-column prop="control_method" :label="$t('pfmea.controlMethod')" min-width="120" />
            <el-table-column :label="$t('pfmea.reactionPlan')" min-width="190"><template #default="{ row }">
              <span>{{ row.reaction || '—' }}</span>
              <el-button v-if="aiOn('quality_plus')" link size="small" class="ai-link" :loading="busy === 'r' + row.id" data-test="cp-ai-reaction" @click="aiReaction(row)">{{ $t('pfmea.aiSuggestion') }}</el-button>
            </template></el-table-column>
            <el-table-column :label="$t('common.inspectionTemplates')" width="130"><template #default="{ row }"><a v-if="row.insp_template" class="lnk hm-mono" @click="router.push('/e/insp_template?id=' + cp.templates[row.insp_template])">{{ row.insp_template }}</a><span v-else class="t3">{{ $t('pfmea.notGenerated') }}</span></template></el-table-column>
            <el-table-column label="" width="96" fixed="right"><template #default="{ row }">
              <el-button link type="primary" size="small" data-test="cp-edit" @click="editItem(row)">{{ $t('common.edit') }}</el-button>
              <el-button link type="danger" size="small" @click="delItem(row)">{{ $t('common.delete') }}</el-button>
            </template></el-table-column>
          </el-table>
          <HmEmpty v-if="!cp.items.length" plain :title="$t('pfmea.noControlPlanRowsYet')" />
        </HmCard>

        <HmCard :title="$t('ecn.documentVersions')" :sub="$t('pfmea.onceUnderControlItGoes')" flush class="mt">
          <template #actions><el-button link type="primary" @click="router.push('/exec/docs')">{{ $t('pfmea.documentControl') }}</el-button></template>
          <el-table v-if="cp.revisions.length" :data="cp.revisions" size="small" data-test="cp-revs">
            <el-table-column :label="$t('common.versions')" width="64"><template #default="{ row }"><span class="hm-mono">{{ row.rev }}</span></template></el-table-column>
            <el-table-column :label="$t('common.status')" width="90"><template #default="{ row }"><HmTag :tone="DST_T[row.status] || 'neutral'">{{ DST[row.status] || row.status }}</HmTag></template></el-table-column>
            <el-table-column prop="change_note" :label="$t('pfmea.changeNotes')" min-width="140" />
            <el-table-column prop="author" :label="$t('pfmea.preparedBy')" width="76" /><el-table-column prop="reviewed_by" :label="$t('pfmea.reviewedBy')" width="76" /><el-table-column prop="approved_by" :label="$t('common.approve3')" width="76" />
            <el-table-column :label="$t('ecn.effectiveTime')" width="110"><template #default="{ row }"><span class="hm-num">{{ fmtTime(row.effective_at) }}</span></template></el-table-column>
          </el-table>
          <HmEmpty v-else plain :title="$t('pfmea.notUnderDocumentControlYet')" />
        </HmCard>
      </div>

      <HmEmpty v-else-if="loaded" class="main" :title="$t('pfmea.selectAPfmeaOrControl')" :desc="$t('pfmea.clickAnItemInThe')" />
    </div>

    <!-- PFMEA item editor -->
    <el-dialog v-model="re.open" :title="re.f.id ? $t('pfmea.editPfmeaItem0', [re.f.seq ?? '']) : $t('pfmea.newPfmeaItem')" width="min(820px, 96vw)" append-to-body destroy-on-close>
      <el-form label-width="92px" size="small" class="fgrid" data-test="fmea-form">
        <el-form-item :label="$t('implFields.order')"><el-input-number v-model="re.f.seq" :min="0" :controls="false" style="width: 100%" /></el-form-item>
        <el-form-item :label="$t('pfmea.processStep')"><RefSelect v-model="re.f.process" entity="process" :label="re.f.process__label" /></el-form-item>
        <el-form-item :label="$t('pfmea.functionRequirement')" class="span2"><el-input v-model="re.f.function" /></el-form-item>
        <el-form-item :label="$t('pfmea.productCharacteristic')"><el-input v-model="re.f.product_char" /></el-form-item>
        <el-form-item :label="$t('pfmea.processCharacteristic')"><el-input v-model="re.f.process_char" /></el-form-item>
        <el-form-item :label="$t('pfmea.characteristicCode')"><el-input v-model="re.f.char" placeholder="BORE_DIA" /></el-form-item>
        <el-form-item :label="$t('pfmea.specialCharacteristics')"><el-select v-model="re.f.special" style="width: 100%"><el-option value="none" :label="$t('common.marginal')" /><el-option value="CC" :label="$t('pfmea.criticalCharacteristicCc')" /><el-option value="SC" :label="$t('pfmea.significantCharacteristicSc')" /></el-select></el-form-item>
        <el-form-item :label="$t('pfmea.lowerUpperLimit')"><div class="pair"><el-input-number v-model="re.f.lo" :controls="false" /><el-input-number v-model="re.f.hi" :controls="false" /></div></el-form-item>
        <el-form-item :label="$t('common.unit')"><el-input v-model="re.f.unit" /></el-form-item>
        <el-form-item :label="$t('pfmea.failureMode')" class="span2"><el-input v-model="re.f.failure_mode" /></el-form-item>
        <el-form-item :label="$t('pfmea.failureEffect')" class="span2"><el-input v-model="re.f.effect" /></el-form-item>
        <el-form-item :label="$t('pfmea.failureCause')" class="span2"><el-input v-model="re.f.cause" /></el-form-item>
        <el-form-item :label="$t('pfmea.preventionControls')"><el-input v-model="re.f.prevention" /></el-form-item>
        <el-form-item :label="$t('pfmea.detectionControls')"><el-input v-model="re.f.detection_ctrl" /></el-form-item>
        <el-form-item label="S / O / D" class="span2">
          <div class="sod3">
            <span>S</span><el-input-number v-model="re.f.severity" :min="1" :max="10" />
            <span>O</span><el-input-number v-model="re.f.occurrence" :min="1" :max="10" />
            <span>D</span><el-input-number v-model="re.f.detection" :min="1" :max="10" />
            <span class="t2">{{ $t('pfmea.theServerCalculatesRpnAnd') }}</span>
          </div>
        </el-form-item>
        <el-form-item :label="$t('pfmea.improvementActions')" class="span2"><el-input v-model="re.f.actions" type="textarea" :rows="2" /></el-form-item>
        <el-form-item :label="$t('docControl.owner')"><el-input v-model="re.f.owner" /></el-form-item>
        <el-form-item :label="$t('pfmea.completionDate')"><el-date-picker v-model="re.f.due" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item>
        <el-form-item :label="$t('pfmea.actionStatus')"><el-select v-model="re.f.action_status" clearable style="width: 100%"><el-option value="open" :label="$t('implHome.open')" /><el-option value="done" :label="$t('common.completed')" /></el-select></el-form-item>
        <el-form-item :label="$t('pfmea.sODAfterActions')">
          <div class="pair3"><el-input-number v-model="re.f.s2" :min="1" :max="10" :controls="false" /><el-input-number v-model="re.f.o2" :min="1" :max="10" :controls="false" /><el-input-number v-model="re.f.d2" :min="1" :max="10" :controls="false" /></div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="re.open = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="busy === 'row'" data-test="fmea-save" @click="saveRow">{{ $t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- Control plan row editor -->
    <el-dialog v-model="ie.open" :title="ie.f.id ? $t('pfmea.editControlPlanRow0', [ie.f.char_no || ie.f.seq || '']) : $t('pfmea.newControlPlanRow')" width="min(820px, 96vw)" append-to-body destroy-on-close>
      <el-form label-width="96px" size="small" class="fgrid" data-test="cp-form">
        <el-form-item :label="$t('implFields.order')"><el-input-number v-model="ie.f.seq" :min="0" :controls="false" style="width: 100%" /></el-form-item>
        <el-form-item :label="$t('pfmea.process')"><RefSelect v-model="ie.f.process" entity="process" :label="ie.f.process__label" /></el-form-item>
        <el-form-item :label="$t('pfmea.machineDevice')"><el-input v-model="ie.f.machine" /></el-form-item>
        <el-form-item :label="$t('pfmea.characteristicNo')"><el-input v-model="ie.f.char_no" /></el-form-item>
        <el-form-item :label="$t('pfmea.productCharacteristic')"><el-input v-model="ie.f.product_char" /></el-form-item>
        <el-form-item :label="$t('pfmea.processCharacteristic')"><el-input v-model="ie.f.process_char" /></el-form-item>
        <el-form-item :label="$t('pfmea.characteristicCode')"><el-input v-model="ie.f.char" /></el-form-item>
        <el-form-item :label="$t('pfmea.specialCharacteristics')"><el-select v-model="ie.f.special" style="width: 100%"><el-option value="none" :label="$t('common.marginal')" /><el-option value="CC" :label="$t('pfmea.criticalCharacteristicCc')" /><el-option value="SC" :label="$t('pfmea.significantCharacteristicSc')" /></el-select></el-form-item>
        <el-form-item :label="$t('pfmea.specTolerance')"><el-input v-model="ie.f.spec" placeholder="20–20.021 mm" /></el-form-item>
        <el-form-item :label="$t('pfmea.lowerUpperLimit')"><div class="pair"><el-input-number v-model="ie.f.lo" :controls="false" /><el-input-number v-model="ie.f.hi" :controls="false" /></div></el-form-item>
        <el-form-item :label="$t('common.unit')"><el-input v-model="ie.f.unit" /></el-form-item>
        <el-form-item :label="$t('pfmea.gaugeType')"><el-select v-model="ie.f.gauge_type" clearable style="width: 100%"><el-option v-for="(t, k) in cp?.gauge_types || {}" :key="k" :value="k" :label="t" /></el-select></el-form-item>
        <el-form-item :label="$t('pfmea.evaluationMeasurementTechnique')" class="span2"><el-input v-model="ie.f.method" /></el-form-item>
        <el-form-item :label="$t('pfmea.sampleSize')"><el-input v-model="ie.f.sample_size" /></el-form-item>
        <el-form-item :label="$t('implReports.frequency')"><el-input v-model="ie.f.frequency" /></el-form-item>
        <el-form-item :label="$t('pfmea.controlMethod')" class="span2"><el-input v-model="ie.f.control_method" /></el-form-item>
        <el-form-item :label="$t('pfmea.reactionPlan')" class="span2"><el-input v-model="ie.f.reaction" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ie.open = false">{{ $t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="busy === 'item'" data-test="cp-save" @click="saveItemDlg">{{ $t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t } from '../i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { aiBus, aiOn, openAiDock } from '../ai';
import { del, get, post, put, tok } from '../api';
import RefSelect from '../components/RefSelect.vue';
import { fmtTime } from '../design';

const router = useRouter(), route = useRoute();
const FST: Record<string, string> = { draft: $t('common.draft'), review: $t('pfmea.review'), approved: $t('common.approved2'), obsolete: $t('common.void') };
const FST_T: Record<string, any> = { draft: 'neutral', review: 'warn', approved: 'ok', obsolete: 'neutral' };
const DST: Record<string, string> = { draft: $t('common.draft'), review: $t('common.inReview'), approved: $t('common.approved2'), effective: $t('common.effective'), obsolete: $t('common.void') };
const DST_T: Record<string, any> = { draft: 'neutral', review: 'warn', approved: 'primary', effective: 'ok', obsolete: 'neutral' };
const PH: Record<string, string> = { proto: $t('pfmea.prototype'), prelaunch: $t('pfmea.preLaunch'), production: $t('pfmea.production') };
const AP_T: Record<string, any> = { H: 'bad', M: 'warn', L: 'ok' };

const fmeas = ref<any[]>([]), cps = ref<any[]>([]), loaded = ref(false);
const sel = reactive<{ kind: '' | 'f' | 'c'; id: number }>({ kind: '', id: 0 });
const fm = ref<any>(null), cp = ref<any>(null);
const busy = ref('');

async function loadLists() {
  const [a, b] = await Promise.all([get('/api/e/pfmea?size=200&sort=-id'), get('/api/e/control_plan?size=200&sort=-id')]);
  fmeas.value = a.items; cps.value = b.items; loaded.value = true;
}
async function loadFm() { fm.value = await get(`/api/iatf/pfmea/${sel.id}`); }
async function loadCp() { cp.value = await get(`/api/iatf/cp/${sel.id}`); }
async function reload() { if (sel.kind === 'f') await loadFm(); else if (sel.kind === 'c') await loadCp(); }
function pick(kind: 'f' | 'c', id: number) { router.replace({ query: kind === 'f' ? { id } : { cp: id } }); }
async function fromRoute() {
  const id = Number(route.query.id || 0), c = Number(route.query.cp || 0);
  if (c) { sel.kind = 'c'; sel.id = c; fm.value = null; await loadCp().catch(() => { sel.kind = ''; }); }
  else if (id) { sel.kind = 'f'; sel.id = id; cp.value = null; await loadFm().catch(() => { sel.kind = ''; }); }
  else if (fmeas.value.length) pick('f', fmeas.value[0].id);
  else if (cps.value.length) pick('c', cps.value[0].id);
}

async function newFmea() {
  const { value } = await ElMessageBox.prompt($t('pfmea.pfmeaNameEGSgh'), $t('pfmea.newPfmea2'), { inputValidator: (v: string) => !!v?.trim() || $t('pfmea.pleaseEnterAName') }).catch(() => ({ value: null } as any));
  if (value == null) return;
  const r = await post('/api/e/pfmea', { title: value.trim(), method: 'ap' });
  await loadLists(); pick('f', r.id);
}

// ---------- PFMEA items ----------
const ROW_KEYS = ['seq', 'process', 'function', 'product_char', 'process_char', 'char', 'special', 'lo', 'hi', 'unit', 'failure_mode', 'effect', 'severity', 'cause', 'prevention',
  'occurrence', 'detection_ctrl', 'detection', 'actions', 'owner', 'due', 'action_status', 's2', 'o2', 'd2'];
const re = reactive<any>({ open: false, f: {} });
function addRow() {
  const last = fm.value.rows[fm.value.rows.length - 1];
  re.f = { seq: (last?.seq || 0) + 10, special: 'none', severity: 5, occurrence: 5, detection: 5, process: last?.process ?? null, process__label: last?.process__label };
  re.open = true;
}
function editRow(r: any) { re.f = { ...r }; re.open = true; }
const nz = (v: any) => (v === '' || v === undefined ? null : v);
async function saveRow() {
  busy.value = 'row';
  try {
    const body: any = Object.fromEntries(ROW_KEYS.map((k) => [k, nz(re.f[k])]));
    if (re.f.id) await put(`/api/e/pfmea_row/${re.f.id}`, body);
    else await post('/api/e/pfmea_row', { ...body, pfmea: fm.value.item.id });
    ElMessage.success($t('common.saved')); re.open = false; await loadFm();
  } catch { /* already notified */ } finally { busy.value = ''; }
}
async function saveSod(r: any) {
  if (!r.severity || !r.occurrence || !r.detection) return;
  try {
    await put(`/api/e/pfmea_row/${r.id}`, { severity: r.severity, occurrence: r.occurrence, detection: r.detection });
    const x = (await get(`/api/e/pfmea_row/${r.id}`)).item;
    Object.assign(r, { rpn: x.rpn, ap: x.ap });
    const ap: any = { H: 0, M: 0, L: 0 }; for (const y of fm.value.rows) if (y.ap in ap) ap[y.ap]++;
    fm.value.ap_counts = ap;
  } catch { await loadFm(); }
}
async function delRow(r: any) {
  const ok = await ElMessageBox.confirm($t('pfmea.deleteItem01', [r.seq, r.failure_mode || '']), $t('common.delete'), { type: 'warning' }).catch(() => false);
  if (!ok) return;
  await del(`/api/e/pfmea_row/${r.id}`); ElMessage.success($t('common.deleted')); await loadFm();
}
async function derive() {
  busy.value = 'derive';
  try {
    const r = await post(`/api/e/pfmea/${fm.value.item.id}/do/derive_cp`, {});
    ElMessage.success(r.msg || $t('pfmea.controlPlanGenerated')); await loadLists(); if (r.id) pick('c', r.id);
  } catch { /* already notified */ } finally { busy.value = ''; }
}

// ---------- AI ----------
async function showDraft(path: string, body: any, item: any, key: string) {
  busy.value = key;
  openAiDock(true);
  aiBus.emit('draft-pending', { label: item.label, op: key });
  try {
    const d = await post(path, body);
    aiBus.emit('draft', { draft: d, item });
  } catch (e: any) {
    aiBus.emit('draft', { error: e?.message || String(e), item });
  } finally { busy.value = ''; }
}
const aiDraft = () => showDraft('/api/iatf/ai/pfmea_draft', { pfmea: fm.value.item.id }, { kind: 'record', entity: 'pfmea', id: fm.value.item.id, label: `PFMEA ${fm.value.item.code}` }, 'ai');
const aiReaction = (r: any) => showDraft('/api/iatf/ai/reaction', { cp_item: r.id }, { kind: 'row', entity: 'cp_item', id: r.id, label: $t('pfmea.controlPlan0Rows1', [cp.value.item.code, r.char_no || r.seq]) }, 'r' + r.id);

// ---------- Control plan ----------
const ITEM_KEYS = ['seq', 'process', 'machine', 'char_no', 'product_char', 'process_char', 'char', 'special', 'spec', 'lo', 'hi', 'unit', 'method', 'gauge_type', 'sample_size',
  'frequency', 'control_method', 'reaction'];
const ie = reactive<any>({ open: false, f: {} });
function addItem() {
  const last = cp.value.items[cp.value.items.length - 1];
  ie.f = { seq: (last?.seq || 0) + 10, special: 'none', process: last?.process ?? null, process__label: last?.process__label };
  ie.open = true;
}
function editItem(r: any) { ie.f = { ...r }; ie.open = true; }
async function saveItemDlg() {
  busy.value = 'item';
  try {
    const body: any = Object.fromEntries(ITEM_KEYS.map((k) => [k, nz(ie.f[k])]));
    if (ie.f.id) await put(`/api/e/cp_item/${ie.f.id}`, body);
    else await post('/api/e/cp_item', { ...body, plan: cp.value.item.id });
    ElMessage.success($t('common.saved')); ie.open = false; await loadCp();
  } catch { /* already notified */ } finally { busy.value = ''; }
}
async function saveItem(r: any, body: any) {
  try { await put(`/api/e/cp_item/${r.id}`, body); ElMessage.success($t('common.saved')); } catch { await loadCp(); }
}
async function delItem(r: any) {
  const ok = await ElMessageBox.confirm($t('pfmea.deleteControlPlanRow0', [r.char_no || r.seq, r.product_char || '']), $t('common.delete'), { type: 'warning' }).catch(() => false);
  if (!ok) return;
  await del(`/api/e/cp_item/${r.id}`); ElMessage.success($t('common.deleted')); await loadCp();
}
async function cpAct(a: 'gen_insp' | 'to_doc') {
  busy.value = a === 'gen_insp' ? 'gen' : 'doc';
  try { const r = await post(`/api/e/control_plan/${cp.value.item.id}/do/${a}`, {}); ElMessage.success(r.msg || $t('common.done')); await Promise.all([loadCp(), loadLists()]); }
  catch { /* already notified */ } finally { busy.value = ''; }
}
function printCp() { window.open(`/api/iatf/cp/${cp.value.item.id}/print?token=${tok()}`, '_blank'); }

const onApproved = () => { reload(); };
watch(() => [route.query.id, route.query.cp], () => { if (loaded.value) fromRoute(); });
onMounted(async () => { window.addEventListener('approvals-changed', onApproved); await loadLists(); await fromRoute(); });
onUnmounted(() => window.removeEventListener('approvals-changed', onApproved));
</script>

<style scoped>
.lay { display: grid; grid-template-columns: calc(250px * var(--hm-scale)) minmax(0, 1fr); gap: var(--hm-gap); align-items: start; }
.side { padding: var(--hm-sp-2) 0; }
.grp { padding: var(--hm-sp-2) var(--hm-sp-3) var(--hm-sp-1); font-size: var(--hm-fs-xs); color: var(--hm-text-2); font-weight: 600; display: flex; gap: 6px; }
.it { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; width: 100%; padding: 8px var(--hm-sp-3); border: 0; border-left: 3px solid transparent; background: transparent;
  text-align: left; cursor: pointer; font: inherit; color: var(--hm-text); }
.it:hover { background: var(--hm-surface-3); }
.it.on { background: var(--hm-primary-soft); border-left-color: var(--hm-primary); }
.it .c { font-size: var(--hm-fs-2xs); color: var(--hm-text-2); }
.it .n { font-size: var(--hm-fs-sm); line-height: 1.35; white-space: normal; word-break: break-all; }
.it .m { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.main { min-width: 0; }
.hd { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.hd .el-button + .el-button { margin-left: 0; }
.ttl { font-size: var(--hm-fs-lg); font-weight: 600; color: var(--hm-text); }
.meta { display: flex; flex-wrap: wrap; gap: 6px 20px; margin-top: var(--hm-sp-2); font-size: var(--hm-fs-xs); color: var(--hm-text-2); }
.meta b { font-weight: 500; color: var(--hm-text); }
.apc { display: flex; gap: var(--hm-sp-2); margin-top: var(--hm-sp-3); flex-wrap: wrap; align-items: stretch; }
.ap-k { display: flex; flex-direction: column; justify-content: center; min-width: 76px; padding: 6px 12px; border-radius: var(--hm-r-md); border: 1px solid var(--hm-border); }
.ap-k .l { font-size: var(--hm-fs-2xs); color: var(--hm-text-2); }
.ap-k .v { font-size: var(--hm-fs-2xl); font-weight: 600; line-height: 1.15; }
.ap-k.h { background: var(--hm-fault-soft); } .ap-k.h .v { color: var(--hm-fault-ink); }
.ap-k.m { background: var(--hm-idle-soft); } .ap-k.m .v { color: var(--hm-idle-ink); }
.ap-k.lo { background: var(--hm-run-soft); } .ap-k.lo .v { color: var(--hm-run-ink); }
.legend { flex: 1; min-width: 260px; display: flex; flex-direction: column; gap: 3px; font-size: var(--hm-fs-2xs); color: var(--hm-text-2); padding: 6px 12px;
  background: var(--hm-surface-2); border-radius: var(--hm-r-md); }
.legend b { color: var(--hm-text); font-weight: 600; }
.legend span { display: flex; gap: 6px; align-items: center; }
.plans { margin-top: var(--hm-sp-2); display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.lnk { color: var(--hm-primary); cursor: pointer; text-decoration: none; }
.lnk:hover { text-decoration: underline; }
.t2 { color: var(--hm-text-2); } .t3 { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-2xs); }
.ml { margin-left: 4px; }
.sod { width: 42px; }
.sod :deep(.el-input__inner) { text-align: center; font-family: var(--hm-font-num); }
.ai-btn { background: linear-gradient(var(--hm-surface), var(--hm-surface)) padding-box, var(--hm-ai-grad) border-box; border: 1px solid transparent; color: var(--hm-primary-press); }
.ai-link { margin-left: 4px; color: var(--hm-primary-press); }
.fgrid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 12px; }
.fgrid .span2 { grid-column: span 2; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; width: 100%; }
.pair3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; width: 100%; }
.pair :deep(.el-input-number), .pair3 :deep(.el-input-number) { width: 100%; }
.sod3 { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.sod3 :deep(.el-input-number) { width: 110px; }
@media (max-width: 1100px) { .lay { grid-template-columns: minmax(0, 1fr); } }
</style>
