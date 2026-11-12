<template>
  <div class="page lbl">
    <HmPageHead :title="$t('common.labelDesignPrinting')" :sub="$t('labelDesigner.templatesAreDesignedInMillimetres')" />
    <el-tabs v-model="tab" class="tabs" data-test="lbl-tabs">
      <el-tab-pane :label="$t('labelDesigner.templateDesign')" name="design" />
      <el-tab-pane name="devices"><template #label><span data-test="lbl-tab-devices">{{ $t('labelDesigner.printersMarkers') }}</span></template></el-tab-pane>
      <el-tab-pane name="rules"><template #label><span data-test="lbl-tab-rules">{{ $t('labelDesigner.autoPrintRules') }}</span></template></el-tab-pane>
      <el-tab-pane name="jobs"><template #label><span data-test="lbl-tab-jobs">{{ $t('labelDesigner.printJobs') }}</span></template></el-tab-pane>
    </el-tabs>

    <!-- ───────────── Template Design ───────────── -->
    <div v-show="tab === 'design'" class="dz">
      <HmCard class="tl" :title="$t('common.template')" flush data-test="lbl-tpl-list">
        <template #actions>
          <el-button size="small" type="primary" plain data-test="lbl-new" @click="openNew">{{ $t('labelDesigner.new') }}</el-button>
          <el-button size="small" :disabled="!T.id" data-test="lbl-copy" @click="copyTpl">{{ $t('common.copy') }}</el-button>
        </template>
        <div class="tl-f">
          <el-select v-model="useF" size="small" clearable :placeholder="$t('labelDesigner.allPurposes')"><el-option v-for="(v, k) in USE" :key="k" :value="k" :label="v" /></el-select>
          <el-input v-model="q" size="small" clearable :placeholder="$t('labelDesigner.codeName')" />
        </div>
        <div class="tl-list">
          <button v-for="t in tplShown" :key="t.id" type="button" class="ti" :class="{ on: t.id === T.id }" :data-code="t.code" @click="pick(t.id)">
            <span class="ti-c hm-mono">{{ t.code }}</span>
            <span class="ti-n">{{ t.name }}</span>
            <span class="ti-m"><HmTag :tone="t.status === 'active' ? 'primary' : 'neutral'">{{ USE[t.usage] || t.usage }}</HmTag>
              <span class="hm-num muted small">{{ fmtNum(t.width_mm) }}×{{ fmtNum(t.height_mm) }} mm</span>
              <HmTag v-if="t.status !== 'active'" tone="warn">{{ TST[t.status] || t.status }}</HmTag></span>
          </button>
          <HmEmpty v-if="!tplShown.length" plain :title="$t('labelDesigner.noTemplates')" />
        </div>
      </HmCard>

      <HmToolbar dense class="tb">
        <span class="tg">
          <el-button size="small" data-test="lbl-add-text" @click="add('text')"><el-icon><EditPen /></el-icon>{{ $t('common.text') }}</el-button>
          <el-dropdown trigger="click" max-height="320px" @command="(k: string) => add('var', k)">
            <el-button size="small" data-test="lbl-add-var">{{ $t('lblProps.variable') }}<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
            <template #dropdown><el-dropdown-menu>
              <el-dropdown-item v-for="v in vars" :key="v.key" :command="v.key"><span class="hm-mono" v-text="brace(v.key)" /><span class="muted dv">{{ String(v.sample ?? '') }}</span></el-dropdown-item>
            </el-dropdown-menu></template>
          </el-dropdown>
          <el-button size="small" data-test="lbl-add-barcode" @click="add('code128')"><el-icon><Tickets /></el-icon>Code128</el-button>
          <el-button size="small" data-test="lbl-add-qr" @click="add('qr')">QR</el-button>
          <el-button size="small" data-test="lbl-add-dm" @click="add('datamatrix')">DataMatrix</el-button>
          <el-button size="small" @click="add('line')"><el-icon><Minus /></el-icon>{{ $t('lblProps.line') }}</el-button>
          <el-button size="small" @click="add('box')"><el-icon><Crop /></el-icon>{{ $t('lblProps.box') }}</el-button>
          <el-button size="small" @click="add('image')"><el-icon><Picture /></el-icon>{{ $t('common.image') }}</el-button>
        </span>
        <span class="tg">
          <el-button size="small" :disabled="!canUndo" :title="$t('labelDesigner.undoCtrlZ')" @click="undo"><el-icon><RefreshLeft /></el-icon></el-button>
          <el-button size="small" :disabled="!canRedo" :title="$t('labelDesigner.redoCtrlY')" @click="redo"><el-icon><RefreshRight /></el-icon></el-button>
          <el-button size="small" :title="$t('common.zoomOut')" @click="zoomBy(1 / 1.25)"><el-icon><ZoomOut /></el-icon></el-button>
          <span class="zv hm-num">{{ Math.round((zoom / MM_PX) * 100) }}%</span>
          <el-button size="small" :title="$t('common.zoomIn')" @click="zoomBy(1.25)"><el-icon><ZoomIn /></el-icon></el-button>
          <el-button size="small" :type="zoomMode === 'fit' ? 'primary' : ''" plain @click="zoomMode = 'fit'">{{ $t('labelDesigner.fit') }}</el-button>
          <el-radio-group v-model="showVars" size="small"><el-radio-button :value="false">{{ $t('labelDesigner.sampleData') }}</el-radio-button><el-radio-button :value="true">{{ $t('labelDesigner.variableName') }}</el-radio-button></el-radio-group>
        </span>
        <template #right>
          <el-button size="small" :disabled="!T.id" data-test="lbl-check" @click="check(true)">{{ $t('labelDesigner.validate') }}</el-button>
          <el-button size="small" :disabled="!T.id" data-test="lbl-zpl" @click="openPayload('zpl')">ZPL</el-button>
          <el-button size="small" :disabled="!T.id" data-test="lbl-tspl" @click="openPayload('tspl')">TSPL</el-button>
          <el-button size="small" :disabled="!T.id" data-test="lbl-pdf" @click="pdfPreview">{{ $t('drawingViewer.pdfPreview') }}</el-button>
          <el-button size="small" type="primary" :disabled="!T.id" :loading="saving" data-test="lbl-save" @click="save()">{{ $t('labelDesigner.save0', [dirty ? ' *' : '']) }}</el-button>
          <el-button size="small" type="primary" plain :disabled="!T.id" data-test="lbl-print" @click="openPrint"><el-icon><Printer /></el-icon>{{ $t('labelDesigner.print') }}</el-button>
        </template>
      </HmToolbar>

      <div class="cv">
        <LblCanvas v-if="T.id" ref="canvas" class="cv-c" :width="T.width_mm" :height="T.height_mm" :elements="els" :selected="sel" :zoom="zoom" :data="sample"
          :show-vars="showVars" @select="(i: number) => { sel = i; if (i >= 0) side = 'el'; }" @begin="dragging = true" @end="onDragEnd" />
        <div v-else class="cv-c cv-empty"><HmEmpty icon="data" :title="$t('labelDesigner.selectATemplateToStart')" :desc="$t('labelDesigner.pickATemplateFromThe')" /></div>
        <div class="sb">
          <span class="hm-num">{{ fmtNum(T.width_mm) }} × {{ fmtNum(T.height_mm) }} mm</span><span class="muted">·</span>
          <span>{{ $t('labelDesigner.n0Elements', [els.length]) }}</span><span class="muted">·</span>
          <span class="muted">{{ $t('labelDesigner.dragToMoveDragHandles') }}</span>
          <span class="grow" />
          <HmTag v-if="dirty" tone="warn">{{ $t('labelDesigner.unsaved') }}</HmTag>
          <HmTag v-if="problems === null" tone="neutral">{{ $t('labelDesigner.notValidated') }}</HmTag>
          <HmTag v-else-if="!problems.length" tone="ok">{{ $t('labelDesigner.validationPassed') }}</HmTag>
          <HmTag v-else tone="bad">{{ $t('agentEditor.n0Issues', [problems.length]) }}</HmTag>
        </div>
        <el-alert v-if="problems && problems.length" type="error" :closable="false" class="probs" data-test="lbl-problems">
          <template #title>{{ $t('labelDesigner.layoutIssuesFixBeforePrinting') }}</template>
          <ul><li v-for="(p, i) in problems" :key="i">{{ p }}</li></ul>
        </el-alert>
      </div>

      <HmCard class="pp" flush data-test="lbl-props">
        <template #title>
          <el-radio-group v-model="side" size="small"><el-radio-button value="el" :disabled="sel < 0">{{ $t('labelDesigner.elementProperties') }}</el-radio-button><el-radio-button value="tpl">{{ $t('labelDesigner.templateProperties') }}</el-radio-button></el-radio-group>
        </template>
        <div class="pp-b">
          <template v-if="side === 'el' && sel >= 0 && els[sel]">
            <LblProps :el="els[sel]" :vars="vars" :index="sel" :count="els.length" @remove="removeSel" @dup="dupSel" @order="order" />
          </template>
          <template v-else-if="T.id">
            <div class="pf">
              <label class="fl"><span class="lb">{{ $t('common.code') }}</span><el-input v-model="T.code" size="small" class="hm-mono" /></label>
              <label class="fl"><span class="lb">{{ $t('common.status') }}</span><el-select v-model="T.status" size="small"><el-option v-for="(v, k) in TST" :key="k" :value="k" :label="v" /></el-select></label>
              <label class="fl full"><span class="lb">{{ $t('common.name') }}</span><el-input v-model="T.name" size="small" /></label>
              <label class="fl"><span class="lb">{{ $t('common.purpose') }}</span><el-select v-model="T.usage" size="small" @change="loadVars"><el-option v-for="(v, k) in USE" :key="k" :value="k" :label="v" /></el-select></label>
              <label class="fl"><span class="lb">{{ $t('labelDesigner.customerEmptyGeneric') }}</span><el-input v-model="T.customer" size="small" /></label>
              <label class="fl"><span class="lb">{{ $t('lblProps.widthMm') }}</span><el-input-number v-model="T.width_mm" :min="5" :max="300" :step="1" size="small" controls-position="right" /></label>
              <label class="fl"><span class="lb">{{ $t('lblProps.heightMm') }}</span><el-input-number v-model="T.height_mm" :min="5" :max="300" :step="1" size="small" controls-position="right" /></label>
              <div class="fl full">
                <span class="lb">{{ $t('labelDesigner.sampleDataJsonUsedFor') }}</span>
                <el-input v-model="sampleText" type="textarea" :autosize="{ minRows: 5, maxRows: 14 }" class="hm-mono" data-test="lbl-sample" @blur="parseSample" />
                <div class="row">
                  <el-button size="small" @click="fillSample">{{ $t('labelDesigner.fillFromVariables') }}</el-button>
                  <span v-if="sampleErr" class="err small">{{ sampleErr }}</span>
                </div>
              </div>
            </div>
            <p class="muted small tip">{{ $t('labelDesigner.shownWhenNoElementIs') }}</p>
          </template>
        </div>
      </HmCard>
    </div>

    <!-- ───────────── Printers & Markers ───────────── -->
    <template v-if="tab === 'devices'">
      <HmCard :title="$t('labelDesigner.labelPrinters')" :sub="$t('labelDesigner.zplTsplOverRawTcp')" flush>
        <template #actions><el-button size="small" type="primary" plain v-perm="'label.printer.create'" data-test="lbl-printer-add" @click="editDev('printer')">{{ $t('labelDesigner.addPrinter') }}</el-button></template>
        <el-table :data="printers" size="small" data-test="lbl-printers"
          v-ai-rows="{ entity: 'printer', rows: () => printers, label: (r: any) => $t('labelDesigner.printer01', [r.code, DST[r.status] || '']), data: (r: any) => ({ [$t('common.code')]: r.code, [$t('labelDesigner.commandLanguage')]: PK[r.kind], [$t('common.address')]: addr(r), [$t('common.status')]: DST[r.status], [$t('common.error')]: r.last_error }) }">
          <el-table-column :label="$t('common.code')" min-width="120"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
          <el-table-column prop="name" :label="$t('common.name')" min-width="160" />
          <el-table-column :label="$t('labelDesigner.commandLanguage')" width="130"><template #default="{ row }">{{ PK[row.kind] || row.kind }}</template></el-table-column>
          <el-table-column :label="$t('common.address')" min-width="150"><template #default="{ row }"><span class="hm-mono">{{ addr(row) }}</span></template></el-table-column>
          <el-table-column label="dpi" width="70" align="right"><template #default="{ row }"><span class="hm-num">{{ row.kind === 'browser' ? '—' : fmtNum(row.dpi) }}</span></template></el-table-column>
          <el-table-column prop="location" :label="$t('common.position')" min-width="100" />
          <el-table-column :label="$t('common.status')" min-width="160"><template #default="{ row }">
            <HmTag :tone="DTONE[row.status] || 'neutral'">{{ DST[row.status] || row.status }}</HmTag>
            <div v-if="row.last_error && row.status === 'error'" class="err small">{{ row.last_error }}</div>
          </template></el-table-column>
          <el-table-column :label="$t('common.enable')" width="70"><template #default="{ row }"><el-switch v-model="row.enabled" size="small" @change="(v: any) => toggle('printer', row, v)" /></template></el-table-column>
          <el-table-column label="" width="150"><template #default="{ row }">
            <el-button size="small" type="primary" plain :disabled="!row.enabled" data-test="lbl-printer-test" @click="testDev('printer', row)">{{ $t('labelDesigner.testPrint') }}</el-button>
            <el-button size="small" link type="primary" @click="editDev('printer', row)">{{ $t('common.edit') }}</el-button>
          </template></el-table-column>
        </el-table>
      </HmCard>
      <HmCard :title="$t('labelDesigner.laserMarkers')" :sub="$t('labelDesigner.tcpTextProtocolSimulationMode')" flush class="mt">
        <template #actions><el-button size="small" type="primary" plain v-perm="'label.marker.create'" @click="editDev('marker')">{{ $t('labelDesigner.addMarker') }}</el-button></template>
        <el-table :data="markers" size="small" data-test="lbl-markers"
          v-ai-rows="{ entity: 'marker', rows: () => markers, label: (r: any) => $t('labelDesigner.marker01', [r.code, DST[r.status] || '']), data: (r: any) => ({ [$t('common.code')]: r.code, [$t('labelDesigner.connection')]: MM[r.mode], [$t('common.address')]: addr(r), [$t('common.status')]: DST[r.status], [$t('common.error')]: r.last_error }) }">
          <el-table-column :label="$t('common.code')" min-width="120"><template #default="{ row }"><span class="hm-mono">{{ row.code }}</span></template></el-table-column>
          <el-table-column prop="name" :label="$t('common.name')" min-width="160" />
          <el-table-column :label="$t('labelDesigner.connection')" width="150"><template #default="{ row }">{{ MM[row.mode] || row.mode }}</template></el-table-column>
          <el-table-column :label="$t('common.address')" min-width="140"><template #default="{ row }"><span class="hm-mono">{{ row.mode === 'sim' ? $t('common.builtIn') : addr(row) }}</span></template></el-table-column>
          <el-table-column :label="$t('lblProps.symbology')" width="110"><template #default="{ row }">{{ SYM[row.symbology] || row.symbology }}</template></el-table-column>
          <el-table-column prop="location" :label="$t('common.position')" min-width="100" />
          <el-table-column :label="$t('common.status')" min-width="160"><template #default="{ row }">
            <HmTag :tone="DTONE[row.status] || 'neutral'">{{ DST[row.status] || row.status }}</HmTag>
            <div v-if="row.last_error && row.status === 'error'" class="err small">{{ row.last_error }}</div>
          </template></el-table-column>
          <el-table-column :label="$t('common.enable')" width="70"><template #default="{ row }"><el-switch v-model="row.enabled" size="small" @change="(v: any) => toggle('marker', row, v)" /></template></el-table-column>
          <el-table-column label="" width="150"><template #default="{ row }">
            <el-button size="small" type="primary" plain :disabled="!row.enabled" data-test="lbl-marker-test" @click="testDev('marker', row)">{{ $t('labelDesigner.testMark') }}</el-button>
            <el-button size="small" link type="primary" @click="editDev('marker', row)">{{ $t('common.edit') }}</el-button>
          </template></el-table-column>
        </el-table>
      </HmCard>
    </template>

    <!-- ───────────── Auto-Print Rules ───────────── -->
    <template v-if="tab === 'rules'">
      <HmToolbar>
        <span class="muted small">{{ $t('labelDesigner.printJobsAreQueuedAutomatically') }}</span>
        <template #right><el-button type="primary" v-perm="'label.label_rule.create'" data-test="lbl-rule-add" @click="editRule()">{{ $t('labelDesigner.addRule') }}</el-button></template>
      </HmToolbar>
      <HmCard flush class="mt">
        <el-table :data="rules" size="small" data-test="lbl-rules"
          v-ai-rows="{ entity: 'label_rule', rows: () => rules, label: (r: any) => $t('labelDesigner.printRule01', [EVT[r.event] || r.event, r.template__label || '']), data: (r: any) => ({ [$t('labelDesigner.trigger')]: EVT[r.event], [$t('common.template')]: r.template__label, [$t('labelDesigner.printer')]: r.printer__label, [$t('labelDesigner.marker')]: r.marker__label, [$t('common.enable')]: r.enabled }) }">
          <el-table-column :label="$t('labelDesigner.trigger')" min-width="150"><template #default="{ row }">{{ EVT[row.event] || row.event }}</template></el-table-column>
          <el-table-column prop="template__label" :label="$t('common.template')" min-width="180" />
          <el-table-column :label="$t('common.equipment')" min-width="180"><template #default="{ row }">{{ row.marker__label || row.printer__label || $t('labelDesigner.browserPrintPdf') }}</template></el-table-column>
          <el-table-column :label="$t('labelDesigner.copies')" width="60" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.copies) }}</span></template></el-table-column>
          <el-table-column :label="$t('labelDesigner.customerProduct')" min-width="140"><template #default="{ row }">{{ [row.customer, row.material__label].filter(Boolean).join(' / ') || $t('common.all') }}</template></el-table-column>
          <el-table-column prop="remark" :label="$t('common.notes')" min-width="160" />
          <el-table-column :label="$t('common.enable')" width="70"><template #default="{ row }"><el-switch v-model="row.enabled" size="small" data-test="lbl-rule-enabled" @change="(v: any) => toggle('label_rule', row, v)" /></template></el-table-column>
          <el-table-column label="" width="110"><template #default="{ row }">
            <el-button size="small" link type="primary" @click="editRule(row)">{{ $t('common.edit') }}</el-button>
            <el-button size="small" link type="danger" v-perm="'label.label_rule.delete'" @click="delRule(row)">{{ $t('common.delete') }}</el-button>
          </template></el-table-column>
        </el-table>
      </HmCard>
    </template>

    <!-- ───────────── Print Jobs ───────────── -->
    <template v-if="tab === 'jobs'">
      <HmToolbar>
        <el-radio-group v-model="jf.status" size="small" @change="loadJobs">
          <el-radio-button value="">{{ $t('common.all0', [fmtNum(jobTotal)]) }}</el-radio-button>
          <el-radio-button v-for="(v, k) in JST" :key="k" :value="k">{{ v }} {{ fmtNum(jobCounts[k] || 0) }}</el-radio-button>
        </el-radio-group>
        <el-input v-model="jf.ref" size="small" clearable :placeholder="$t('labelDesigner.docNoPartCodeJob')" style="width: 200px" @keyup.enter="loadJobs" @clear="loadJobs" />
        <el-button size="small" @click="loadJobs">{{ $t('common.search') }}</el-button>
        <template #right><span class="muted small">{{ $t('labelDesigner.refreshesEvery5SFailed') }}</span></template>
      </HmToolbar>
      <HmCard flush class="mt">
        <el-table :data="jobs" size="small" row-key="id" data-test="lbl-jobs"
          v-ai-rows="{ entity: 'print_job', rows: () => jobs, label: (r: any) => $t('labelDesigner.printJob01', [r.code, JST[r.status] || r.status]), data: (r: any) => ({ [$t('common.task')]: r.code, [$t('common.type')]: JK[r.kind], [$t('common.template')]: r.template__label, [$t('common.object')]: r.ref, [$t('common.status')]: JST[r.status], [$t('common.error')]: r.last_error, [$t('labelDesigner.reprint')]: r.reprint ? r.reason : '' }) }">
          <el-table-column type="expand" width="36"><template #default="{ row }">
            <div class="jx">
              <div class="jx-l">
                <b>{{ $t('labelDesigner.sendLog') }}</b>
                <div v-if="!(row.log || []).length" class="muted small">{{ $t('labelDesigner.notSentYet') }}</div>
                <div v-for="(l, i) in row.log || []" :key="i" class="lg"><HmTag :tone="l.ok ? 'ok' : 'bad'">{{ l.ok ? $t('common.succeeded') : $t('common.failed') }}</HmTag><span class="hm-num muted">{{ fmtTime(l.t, 'full') }}</span><span>{{ l.msg }}</span></div>
                <div v-if="row.next_try && row.status === 'queued'" class="muted small">{{ $t('labelDesigner.nextRetry0', [fmtTime(row.next_try, 'time')]) }}</div>
              </div>
              <div class="jx-r">
                <b>{{ $t('labelDesigner.commandContent0', [row.payload ? $t('labelDesigner.first12Lines') : '']) }}</b>
                <pre v-if="row.payload" class="hm-mono pl">{{ head(row.payload) }}</pre>
                <div v-else class="muted small">{{ row.kind === 'browser' ? $t('labelDesigner.browserPrintPdfGeneratedLive') : '—' }}</div>
              </div>
            </div>
          </template></el-table-column>
          <el-table-column :label="$t('labelDesigner.jobNo')" min-width="150"><template #default="{ row }"><div class="hm-mono">{{ row.code }}</div><div class="hm-num muted small">{{ fmtTime(row.created_at) }}</div></template></el-table-column>
          <el-table-column :label="$t('common.type')" width="90"><template #default="{ row }">{{ JK[row.kind] || row.kind }}</template></el-table-column>
          <el-table-column :label="$t('labelDesigner.templateDevice')" min-width="190"><template #default="{ row }"><div>{{ row.template__label || '—' }}</div><div class="muted small">{{ row.marker__label || row.printer__label || $t('labelDesigner.browser') }}</div></template></el-table-column>
          <el-table-column :label="$t('common.object')" min-width="150"><template #default="{ row }"><span class="hm-mono">{{ row.ref || row.target || '—' }}</span></template></el-table-column>
          <el-table-column :label="$t('common.status')" min-width="170"><template #default="{ row }">
            <HmTag :tone="JTONE[row.status] || 'neutral'">{{ JST[row.status] || row.status }}</HmTag>
            <HmTag v-if="row.reprint" tone="amber" class="ml">{{ $t('labelDesigner.reprint') }}</HmTag>
            <div v-if="row.reprint && row.reason" class="small">{{ $t('labelDesigner.reason0', [row.reason]) }}</div>
            <div v-if="row.last_error && row.status !== 'done'" class="err small">{{ row.last_error }}</div>
          </template></el-table-column>
          <el-table-column :label="$t('common.count')" width="60" align="right"><template #default="{ row }"><span class="hm-num">{{ fmtNum(row.attempts) }}</span></template></el-table-column>
          <el-table-column prop="requested_by" :label="$t('common.requester')" width="90" />
          <el-table-column label="" width="190"><template #default="{ row }">
            <el-button v-if="['failed', 'cancelled'].includes(row.status)" size="small" type="primary" plain data-test="lbl-job-retry" @click="jobDo(row, 'retry')">{{ $t('common.retry') }}</el-button>
            <el-button v-if="['queued', 'failed'].includes(row.status)" size="small" plain @click="jobDo(row, 'cancel')">{{ $t('common.cancel') }}</el-button>
            <el-button v-if="row.template" size="small" plain v-perm:disable="'label.reprint'" data-test="lbl-job-reprint" @click="reprint(row)">{{ $t('labelDesigner.reprint') }}</el-button>
            <el-button v-if="row.kind === 'browser'" size="small" link type="primary" @click="openJobPdf(row.id)">PDF</el-button>
          </template></el-table-column>
        </el-table>
      </HmCard>
    </template>

    <!-- New template -->
    <el-dialog v-model="nt.open" :title="$t('labelDesigner.newLabelTemplate')" width="min(480px, 96vw)" destroy-on-close>
      <el-form label-width="84px" size="default">
        <el-form-item :label="$t('common.code')"><el-input v-model="nt.code" :placeholder="$t('labelDesigner.eGLblCartonB')" data-test="lbl-new-code" /></el-form-item>
        <el-form-item :label="$t('common.name')"><el-input v-model="nt.name" data-test="lbl-new-name" /></el-form-item>
        <el-form-item :label="$t('common.purpose')"><el-select v-model="nt.usage" style="width: 100%"><el-option v-for="(v, k) in USE" :key="k" :value="k" :label="v" /></el-select></el-form-item>
        <el-form-item :label="$t('labelDesigner.sizeMm')">
          <div class="row"><el-input-number v-model="nt.width_mm" :min="5" :max="300" controls-position="right" /><span>×</span><el-input-number v-model="nt.height_mm" :min="5" :max="300" controls-position="right" /></div>
        </el-form-item>
        <el-form-item :label="$t('labelDesigner.commonSizes')">
          <div class="row"><el-button v-for="s in SIZES" :key="s" size="small" @click="setSize(s)">{{ s }}</el-button></div>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="nt.open = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :disabled="!nt.code || !nt.name" data-test="lbl-new-ok" @click="createTpl">{{ $t('common.create') }}</el-button></template>
    </el-dialog>

    <!-- ZPL / TSPL preview -->
    <el-dialog v-model="pv.open" :title="$t('labelDesigner.n0CommandPreview1', [pv.format.toUpperCase(), T.code])" width="min(820px, 96vw)" destroy-on-close>
      <div class="row">
        <el-radio-group v-model="pv.format" size="small" @change="loadPayload"><el-radio-button value="zpl">{{ $t('labelDesigner.zplZebra') }}</el-radio-button><el-radio-button value="tspl">{{ $t('labelDesigner.tsplTsc') }}</el-radio-button></el-radio-group>
        <el-select v-model="pv.dpi" size="small" style="width: 110px" @change="loadPayload"><el-option v-for="d in [203, 300, 600]" :key="d" :value="d" :label="d + ' dpi'" /></el-select>
        <span class="muted small hm-num">{{ $t('labelDesigner.n0BytesSampleDataUnsaved', [fmtNum(pv.bytes)]) }}</span>
        <span class="grow" />
        <el-button size="small" @click="copyPayload">{{ $t('common.copy') }}</el-button>
      </div>
      <el-alert v-if="pv.problems.length" type="warning" :closable="false" class="mt-s"><ul class="pl-ul"><li v-for="(p, i) in pv.problems" :key="i">{{ p }}</li></ul></el-alert>
      <pre class="hm-mono payload" data-test="lbl-payload">{{ pv.payload }}</pre>
    </el-dialog>

    <!-- Print -->
    <el-dialog v-model="pd.open" :title="$t('labelDesigner.print01', [T.code, T.name])" width="min(520px, 96vw)" destroy-on-close>
      <el-form label-width="96px">
        <el-form-item :label="$t('common.equipment')">
          <el-select v-model="pd.dev" style="width: 100%" data-test="lbl-print-dev">
            <el-option-group :label="$t('labelDesigner.printer')"><el-option v-for="p in printers.filter((x) => x.enabled)" :key="'p' + p.id" :value="'p:' + p.id" :label="$t('modelTab.n0122', [p.code, p.name, PK[p.kind] || p.kind])" /></el-option-group>
            <el-option-group :label="$t('labelDesigner.laserMarkers')"><el-option v-for="m in markers.filter((x) => x.enabled)" :key="'m' + m.id" :value="'m:' + m.id" :label="`${m.code} ${m.name}`" /></el-option-group>
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('labelDesigner.dataSource')">
          <el-radio-group v-model="pd.src"><el-radio value="ref">{{ $t('labelDesigner.businessObject') }}</el-radio><el-radio value="sample">{{ $t('labelDesigner.sampleData') }}</el-radio></el-radio-group>
        </el-form-item>
        <el-form-item v-if="pd.src === 'ref'" :label="REF_HINT[T.usage] || $t('common.object')">
          <el-input v-model="pd.ref" :placeholder="$t('labelDesigner.enterOrScan0', [REF_HINT[T.usage] || $t('common.docNo')])" data-test="lbl-print-ref" />
        </el-form-item>
        <el-form-item :label="$t('labelDesigner.copies')"><el-input-number v-model="pd.copies" :min="1" :max="500" :disabled="pd.dev.startsWith('m:')" /></el-form-item>
      </el-form>
      <p v-if="dirty" class="muted small">{{ $t('labelDesigner.theLayoutHasUnsavedChanges') }}</p>
      <el-alert v-if="pd.res" :type="pd.res.type" :closable="false" :title="pd.res.msg" data-test="lbl-print-res" />
      <template #footer>
        <el-button @click="pd.open = false">{{ $t('common.close') }}</el-button>
        <el-button type="primary" :loading="pd.busy" :disabled="!pd.dev || (pd.src === 'ref' && !pd.ref.trim())" data-test="lbl-print-go" @click="doPrint">{{ $t('labelDesigner.print') }}</el-button>
      </template>
    </el-dialog>

    <!-- Printers / markers -->
    <el-dialog v-model="dv.open" :title="(dv.f.id ? $t('common.edit') : $t('common.add')) + (dv.kind === 'printer' ? $t('labelDesigner.printer') : $t('labelDesigner.marker'))" width="min(560px, 96vw)" destroy-on-close>
      <el-form label-width="96px">
        <el-form-item :label="$t('common.code')"><el-input v-model="dv.f.code" /></el-form-item>
        <el-form-item :label="$t('common.name')"><el-input v-model="dv.f.name" /></el-form-item>
        <template v-if="dv.kind === 'printer'">
          <el-form-item :label="$t('labelDesigner.commandLanguage')"><el-select v-model="dv.f.kind" style="width: 100%"><el-option v-for="(v, k) in PK" :key="k" :value="k" :label="v" /></el-select></el-form-item>
          <template v-if="dv.f.kind !== 'browser'">
            <el-form-item :label="$t('labelDesigner.ipPort')"><div class="row"><el-input v-model="dv.f.host" placeholder="192.168.10.51" style="width: 200px" /><el-input-number v-model="dv.f.port" :min="1" :max="65535" controls-position="right" /></div></el-form-item>
            <el-form-item :label="$t('labelDesigner.resolution')"><el-select v-model="dv.f.dpi" style="width: 140px"><el-option v-for="d in [203, 300, 600]" :key="d" :value="d" :label="d + ' dpi'" /></el-select></el-form-item>
            <el-form-item :label="$t('labelDesigner.darkness')"><el-input-number v-model="dv.f.darkness" :min="0" :max="30" controls-position="right" /></el-form-item>
            <el-form-item :label="$t('labelDesigner.cjkFont')"><el-input v-model="dv.f.cjk_font" :placeholder="dv.f.kind === 'tspl' ? 'TSS24.BF2' : 'E:SIMSUN.TTF'" /></el-form-item>
          </template>
        </template>
        <template v-else>
          <el-form-item :label="$t('labelDesigner.connection')"><el-select v-model="dv.f.mode" style="width: 100%"><el-option v-for="(v, k) in MM" :key="k" :value="k" :label="v" /></el-select></el-form-item>
          <el-form-item v-if="dv.f.mode !== 'sim'" :label="$t('labelDesigner.ipPort')"><div class="row"><el-input v-model="dv.f.host" style="width: 200px" /><el-input-number v-model="dv.f.port" :min="1" :max="65535" controls-position="right" /></div></el-form-item>
          <el-form-item :label="$t('labelDesigner.commandTemplate')"><el-input v-model="dv.f.cmd_template" class="hm-mono" placeholder="MARK {job} {data}\r\n" /></el-form-item>
          <el-form-item :label="$t('labelDesigner.succeededFailed')"><div class="row"><el-input v-model="dv.f.ack" placeholder="OK" style="width: 140px" /><el-input v-model="dv.f.err" placeholder="ERR|NG" style="width: 140px" /></div></el-form-item>
          <el-form-item :label="$t('labelDesigner.timeoutMs')"><el-input-number v-model="dv.f.timeout_ms" :min="200" :step="500" controls-position="right" /></el-form-item>
          <el-form-item :label="$t('lblProps.symbology')"><el-select v-model="dv.f.symbology" style="width: 160px"><el-option v-for="(v, k) in SYM" :key="k" :value="k" :label="v" /></el-select></el-form-item>
        </template>
        <el-form-item :label="$t('common.position')"><el-input v-model="dv.f.location" /></el-form-item>
        <el-form-item :label="$t('common.enable')"><el-switch v-model="dv.f.enabled" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="dv.open = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :disabled="!dv.f.code || !dv.f.name" @click="saveDev">{{ $t('common.save') }}</el-button></template>
    </el-dialog>

    <!-- Rules -->
    <el-dialog v-model="rd.open" :title="rd.f.id ? $t('labelDesigner.editPrintRule') : $t('labelDesigner.addPrintRule')" width="min(560px, 96vw)" destroy-on-close>
      <el-form label-width="96px">
        <el-form-item :label="$t('labelDesigner.trigger')"><el-select v-model="rd.f.event" style="width: 100%"><el-option v-for="(v, k) in EVT" :key="k" :value="k" :label="v" /></el-select></el-form-item>
        <el-form-item :label="$t('labelDesigner.labelTemplate')"><el-select v-model="rd.f.template" filterable style="width: 100%"><el-option v-for="t in tpls" :key="t.id" :value="t.id" :label="`${t.code} ${t.name}`" /></el-select></el-form-item>
        <el-form-item :label="$t('labelDesigner.printer')"><el-select v-model="rd.f.printer" clearable style="width: 100%" :placeholder="$t('labelDesigner.emptyBrowserPrint')"><el-option v-for="p in printers" :key="p.id" :value="p.id" :label="`${p.code} ${p.name}`" /></el-select></el-form-item>
        <el-form-item :label="$t('labelDesigner.marker')"><el-select v-model="rd.f.marker" clearable style="width: 100%" :placeholder="$t('labelDesigner.ifAMarkerIsSelected')"><el-option v-for="m in markers" :key="m.id" :value="m.id" :label="`${m.code} ${m.name}`" /></el-select></el-form-item>
        <el-form-item :label="$t('labelDesigner.copies')"><el-input-number v-model="rd.f.copies" :min="1" :max="50" controls-position="right" /></el-form-item>
        <el-form-item :label="$t('common.customer')"><el-input v-model="rd.f.customer" :placeholder="$t('labelDesigner.emptyAll')" /></el-form-item>
        <el-form-item :label="$t('common.product')"><el-select v-model="rd.f.material" clearable filterable style="width: 100%" :placeholder="$t('labelDesigner.emptyAll')"><el-option v-for="m in materials" :key="m.id" :value="m.id" :label="`${m.code} ${m.name}`" /></el-select></el-form-item>
        <el-form-item :label="$t('common.notes')"><el-input v-model="rd.f.remark" /></el-form-item>
        <el-form-item :label="$t('common.enable')"><el-switch v-model="rd.f.enabled" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="rd.open = false">{{ $t('common.cancel') }}</el-button><el-button type="primary" :disabled="!rd.f.template || !rd.f.event" data-test="lbl-rule-save" @click="saveRule">{{ $t('common.save') }}</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { $t, lang, intlLocale } from '../i18n';
import { ElMessage, ElMessageBox } from 'element-plus';
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { del, get, post, put, session, tok } from '../api';
import LblCanvas from '../components/exec/LblCanvas.vue';
import LblProps from '../components/exec/LblProps.vue';
import { fmtNum, fmtTime } from '../design';

const USE: Record<string, string> = { part: $t('labelDesigner.partSerialized'), lot: $t('common.lotHeat'), carton: $t('labelDesigner.cartonLabel'), pallet: $t('labelDesigner.palletLabel'), customer: $t('labelDesigner.customerLabel'), location: $t('labelDesigner.location'), gauge: $t('common.gauge'), tool: $t('labelDesigner.toolingCuttingTool'), wo: $t('labelDesigner.workOrderTraveler'), other: $t('common.other') };
const TST: Record<string, string> = { draft: $t('common.draft'), active: $t('common.enable'), obsolete: $t('common.disabled') };
const PK: Record<string, string> = { zpl: $t('labelDesigner.zebraZpl'), tspl: 'TSC TSPL', browser: $t('labelDesigner.browserPrintPdf') };
const MM: Record<string, string> = { tcp: $t('labelDesigner.tcpTextProtocol'), serial_tcp: $t('labelDesigner.serialDeviceServerTcpPassthrough'), sim: $t('labelDesigner.simulatedMarker') };
const SYM: Record<string, string> = { datamatrix: 'DataMatrix', qr: 'QR', code128: 'Code128', text: $t('common.text') };
const DST: Record<string, string> = { ok: $t('common.normal'), error: $t('common.fault'), unknown: $t('hmStatus.unknown') };
const DTONE: Record<string, any> = { ok: 'ok', error: 'bad', unknown: 'neutral' };
const EVT: Record<string, string> = { wo_release: $t('labelDesigner.workOrderReleased'), part_create: $t('labelDesigner.partStartedSerialized'), carton_close: $t('labelDesigner.cartonPacked'), pallet_close: $t('labelDesigner.palletCompleted'), receive: $t('common.receive'), gauge_cal: $t('labelDesigner.gaugeReleased'), outsource_send: $t('labelDesigner.outsourcingShipped') };
const JST: Record<string, string> = { queued: $t('labelDesigner.queued'), sending: $t('labelDesigner.sending'), done: $t('common.done'), failed: $t('common.failed'), cancelled: $t('common.cancelled') };
const JTONE: Record<string, any> = { queued: 'primary', sending: 'primary', done: 'ok', failed: 'bad', cancelled: 'neutral' };
const JK: Record<string, string> = { print: $t('labelDesigner.labelPrinting'), mark: $t('labelDesigner.laserMarking'), browser: $t('labelDesigner.browser') };
const REF_HINT: Record<string, string> = { part: $t('common.partCode'), lot: $t('labelDesigner.lotNo'), carton: $t('common.cartonNo'), pallet: $t('labelDesigner.palletNo'), customer: $t('labelDesigner.cartonNoPalletNo'), location: $t('labelDesigner.locationCode'), gauge: $t('labelDesigner.gaugeNo'), tool: $t('labelDesigner.toolingNo'), wo: $t('common.woNo') };
const SIZES = ['30×20', '50×30', '60×40', '70×40', '100×60', '100×150', '210×74'];
const MM_PX = 96 / 25.4;
const brace = (k: string) => '{' + '{' + k + '}' + '}';      // 100% = actual size

const tab = ref('design');

// ───────────── Template list ─────────────
const tpls = ref<any[]>([]);
const useF = ref(''), q = ref('');
const tplShown = computed(() => tpls.value.filter((t) => (!useF.value || t.usage === useF.value) && (!q.value || `${t.code} ${t.name}`.toLowerCase().includes(q.value.toLowerCase()))));
async function loadTpls() { tpls.value = (await get('/api/e/label_template?size=500&sort=code')).items; }

// ───────────── Current template ─────────────
const T = reactive<any>({ id: null, code: '', name: '', usage: 'part', width_mm: 100, height_mm: 60, customer: '', status: 'active' });
const els = ref<any[]>([]);
const sample = ref<Record<string, any>>({});
const sampleText = ref(''), sampleErr = ref('');
const vars = ref<{ key: string; sample: any }[]>([]);
const sel = ref(-1), side = ref<'el' | 'tpl'>('tpl');
const showVars = ref(false);
const problems = ref<string[] | null>(null);
const saving = ref(false);
const canvas = ref<any>();

async function loadVars() { vars.value = (await get('/api/label/variables?usage=' + encodeURIComponent(T.usage || 'other'))).vars || []; }
async function pick(id: number) {
  if (id === T.id) return;
  if (dirty.value) {
    try { await ElMessageBox.confirm($t('labelDesigner.n0HasUnsavedChangesDiscard', [T.code]), $t('labelDesigner.switchTemplate'), { type: 'warning', confirmButtonText: $t('labelDesigner.discardChanges'), cancelButtonText: $t('labelDesigner.stay') }); } catch { return; }
  }
  const t = tpls.value.find((x) => x.id === id) || (await get(`/api/e/label_template/${id}`));
  Object.assign(T, { id: t.id, code: t.code, name: t.name, usage: t.usage || 'other', width_mm: Number(t.width_mm) || 100, height_mm: Number(t.height_mm) || 60, customer: t.customer || '', status: t.status || 'active' });
  const lay = t.layout || {};
  els.value = JSON.parse(JSON.stringify(Array.isArray(lay) ? lay : lay.elements || []));
  sample.value = { ...(t.sample || {}) };
  sampleText.value = JSON.stringify(sample.value, null, 1); sampleErr.value = '';
  sel.value = -1; side.value = 'tpl'; problems.value = null; zoomMode.value = 'fit';
  resetHist();
  try { localStorage.setItem('hm_lbl_tpl', String(t.id)); } catch { /* no storage */ }
  await loadVars();
  check(false);
}
function parseSample() {
  try { const v = sampleText.value.trim() ? JSON.parse(sampleText.value) : {}; if (typeof v !== 'object' || Array.isArray(v)) throw new Error($t('labelDesigner.mustBeAnObject')); sample.value = v; sampleErr.value = ''; }
  catch (e: any) { sampleErr.value = $t('labelDesigner.invalidJson') + e.message; }
}
function fillSample() {
  parseSample(); if (sampleErr.value) return;
  const s = { ...sample.value };
  for (const v of vars.value) if (!(v.key in s)) s[v.key] = v.sample;
  sample.value = s; sampleText.value = JSON.stringify(s, null, 1);
}

// ───────────── Undo / redo (snapshot: elements + size) ─────────────
const hist = reactive<{ stack: string[]; i: number; saved: string }>({ stack: [], i: -1, saved: '' });
let restoring = false;
const dragging = ref(false);
const snapState = () => JSON.stringify({ e: els.value, w: T.width_mm, h: T.height_mm, m: [T.code, T.name, T.usage, T.customer, T.status], s: sampleText.value });
function resetHist() { const s = snapState(); hist.stack = [s]; hist.i = 0; hist.saved = s; }
function commit() {
  if (restoring || !T.id) return;
  const s = snapState();
  if (hist.stack[hist.i] === s) return;
  hist.stack = hist.stack.slice(0, hist.i + 1); hist.stack.push(s);
  if (hist.stack.length > 120) hist.stack.shift();
  hist.i = hist.stack.length - 1;
}
function restore(s: string) {
  restoring = true;
  const o = JSON.parse(s);
  els.value = o.e; T.width_mm = o.w; T.height_mm = o.h; [T.code, T.name, T.usage, T.customer, T.status] = o.m; sampleText.value = o.s; parseSample();
  if (sel.value >= els.value.length) sel.value = -1;
  nextTick(() => { restoring = false; });
}
const canUndo = computed(() => hist.i > 0), canRedo = computed(() => hist.i < hist.stack.length - 1);
function undo() { if (canUndo.value) { hist.i--; restore(hist.stack[hist.i]); } }
function redo() { if (canRedo.value) { hist.i++; restore(hist.stack[hist.i]); } }
const dirty = computed(() => !!T.id && hist.stack[hist.i] !== hist.saved);
let tmr: any = null;
watch([els, () => [T.width_mm, T.height_mm, T.code, T.name, T.usage, T.customer, T.status], sampleText], () => {
  if (restoring || dragging.value) return;
  clearTimeout(tmr); tmr = setTimeout(commit, 350);
}, { deep: true });
function onDragEnd(changed: boolean) { dragging.value = false; if (changed) commit(); }

// ───────────── Element operations ─────────────
function place() { const n = els.value.length % 6; return { x: 3 + n * 2, y: 3 + n * 2 }; }
function add(kind: string, key?: string) {
  if (!T.id) { ElMessage.info($t('labelDesigner.selectATemplateFirst')); return; }
  const p = place();
  const firstVar = vars.value[0]?.key || 'code';
  const m: Record<string, any> = {
    text: { type: 'text', ...p, h: 3.5, text: $t('common.text'), bold: false, align: 'left', rotate: 0 },
    var: { type: 'text', ...p, h: 3.5, text: `{{${key || firstVar}}}`, bold: false, align: 'left', rotate: 0 },
    code128: { type: 'barcode', ...p, w: Math.min(50, T.width_mm - p.x - 2), h: 12, symbology: 'code128', data: `{{${firstVar}}}`, hr: true, gs1: false },
    qr: { type: 'barcode', ...p, w: 16, h: 16, symbology: 'qr', data: `{{${firstVar}}}` },
    datamatrix: { type: 'barcode', ...p, w: 12, h: 12, symbology: 'datamatrix', data: `{{${firstVar}}}`, gs1: false },
    line: { type: 'line', x: p.x, y: p.y + 10, x2: Math.max(p.x + 10, T.width_mm - 3), y2: p.y + 10, t: 0.3 },
    box: { type: 'box', ...p, w: Math.min(30, T.width_mm - p.x - 1), h: Math.min(15, T.height_mm - p.y - 1), t: 0.4, fill: false },
    image: { type: 'image', ...p, w: 20, h: 10, src: '' },
  };
  els.value.push(m[kind]);
  sel.value = els.value.length - 1; side.value = 'el';
}
function removeSel() { if (sel.value < 0) return; els.value.splice(sel.value, 1); sel.value = Math.min(sel.value, els.value.length - 1); if (sel.value < 0) side.value = 'tpl'; }
function dupSel() {
  if (sel.value < 0) return;
  const c = JSON.parse(JSON.stringify(els.value[sel.value]));
  c.x = +(Number(c.x || 0) + 2).toFixed(2); c.y = +(Number(c.y || 0) + 2).toFixed(2);
  if (c.type === 'line') { c.x2 = +(Number(c.x2 || 0) + 2).toFixed(2); c.y2 = +(Number(c.y2 || 0) + 2).toFixed(2); }
  els.value.splice(sel.value + 1, 0, c); sel.value++;
}
function order(dir: number) {
  const i = sel.value, j = i + dir;
  if (i < 0 || j < 0 || j >= els.value.length) return;
  const a = els.value; [a[i], a[j]] = [a[j], a[i]]; sel.value = j;
}
function nudge(dx: number, dy: number) {
  const el = els.value[sel.value]; if (!el) return;
  el.x = +(Number(el.x || 0) + dx).toFixed(2); el.y = +(Number(el.y || 0) + dy).toFixed(2);
  if (el.type === 'line') { el.x2 = +(Number(el.x2 ?? el.x) + dx).toFixed(2); el.y2 = +(Number(el.y2 ?? el.y) + dy).toFixed(2); }
}
function onKey(ev: KeyboardEvent) {
  if (tab.value !== 'design' || !T.id) return;
  const t = ev.target as HTMLElement;
  if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
  if (document.querySelector('.el-overlay:not([style*="display: none"]) .el-dialog')) return;
  const k = ev.key, ctrl = ev.ctrlKey || ev.metaKey;
  if (ctrl && (k === 'z' || k === 'Z') && !ev.shiftKey) { ev.preventDefault(); undo(); return; }
  if (ctrl && (k === 'y' || k === 'Y' || ((k === 'z' || k === 'Z') && ev.shiftKey))) { ev.preventDefault(); redo(); return; }
  if (ctrl && (k === 's' || k === 'S')) { ev.preventDefault(); save(); return; }
  if (sel.value < 0) return;
  const st = ev.shiftKey ? 5 : 0.5;
  const mv: Record<string, [number, number]> = { ArrowLeft: [-st, 0], ArrowRight: [st, 0], ArrowUp: [0, -st], ArrowDown: [0, st] };
  if (mv[k]) { ev.preventDefault(); nudge(...mv[k]); return; }
  if (k === 'Delete' || k === 'Backspace') { ev.preventDefault(); removeSel(); return; }
  if (ctrl && (k === 'd' || k === 'D')) { ev.preventDefault(); dupSel(); return; }
  if (k === 'Escape') { sel.value = -1; side.value = 'tpl'; }
}

// ───────────── Zoom ─────────────
const zoomMode = ref<'fit' | number>('fit');
const box = reactive({ w: 600, h: 400 });
let ro: ResizeObserver | null = null;
const fitZoom = computed(() => Math.max(1, Math.min(40, Math.min((box.w - 44) / (T.width_mm || 100), (box.h - 44) / (T.height_mm || 60)))));
const zoom = computed(() => (zoomMode.value === 'fit' ? fitZoom.value : zoomMode.value));
function zoomBy(f: number) { zoomMode.value = Math.max(1, Math.min(60, +(zoom.value * f).toFixed(3))); }
watch(() => canvas.value?.wrap, (w: HTMLElement | undefined) => {
  ro?.disconnect();
  if (!w) return;
  ro = new ResizeObserver(() => { box.w = w.clientWidth; box.h = w.clientHeight; });
  ro.observe(w); box.w = w.clientWidth; box.h = w.clientHeight;
});

// ───────────── Save / validate / preview ─────────────
const layoutBody = () => ({ template: T.id, layout: { elements: els.value }, width_mm: T.width_mm, height_mm: T.height_mm, data: sample.value });
async function check(say: boolean) {
  if (!T.id) return;
  try {
    const r = await post('/api/label/preview', { ...layoutBody(), format: 'zpl', dpi: 203 });
    problems.value = r.problems || [];
    if (say) ElMessage({ type: problems.value!.length ? 'warning' : 'success', message: problems.value!.length ? $t('agentEditor.n0Issues', [problems.value!.length]) : $t('labelDesigner.validationPassedBothZplAnd') });
  } catch { problems.value = [$t('labelDesigner.failedToGenerateCommandsSee')]; }
}
async function save(quiet = false) {
  if (!T.id) return false;
  parseSample();
  if (sampleErr.value) { ElMessage.error(sampleErr.value); return false; }
  saving.value = true;
  try {
    const body = { code: T.code.trim(), name: T.name.trim(), usage: T.usage, width_mm: T.width_mm, height_mm: T.height_mm, customer: T.customer || null, status: T.status,
      sample: sample.value, layout: { elements: els.value } };
    await put(`/api/e/label_template/${T.id}`, body);
    commit(); hist.saved = hist.stack[hist.i];
    const i = tpls.value.findIndex((x) => x.id === T.id);
    if (i >= 0) tpls.value[i] = { ...tpls.value[i], ...body };
    if (!quiet) ElMessage.success($t('labelDesigner.n0Saved', [T.code]));
    await check(false);
    return true;
  } catch { return false; } finally { saving.value = false; }
}
const pv = reactive<any>({ open: false, format: 'zpl', dpi: 203, payload: '', bytes: 0, problems: [] });
async function loadPayload() {
  const r = await post('/api/label/preview', { ...layoutBody(), format: pv.format, dpi: pv.dpi });
  pv.payload = r.payload; pv.bytes = r.bytes; pv.problems = r.problems || []; problems.value = pv.problems;
}
async function openPayload(f: string) { pv.format = f; pv.payload = ''; pv.open = true; await loadPayload(); }
async function copyPayload() { try { await navigator.clipboard.writeText(pv.payload); ElMessage.success($t('common.copied')); } catch { ElMessage.warning($t('labelDesigner.theBrowserBlockedCopyingPlease')); } }

/** Fetch the PDF with login credentials (POST preview / GET job) and open it in a new window (window opened first to avoid popup blocking). */
async function openPdf(url: string, body?: any) {
  const w = window.open('', '_blank');
  try {
    const r = await fetch(url, { method: body ? 'POST' : 'GET', headers: { 'X-Lang': lang, Authorization: 'Bearer ' + session.token, ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(typeof j.detail === 'string' ? j.detail : j.detail?.msg || r.statusText); }
    const u = URL.createObjectURL(await r.blob());
    if (w) w.location.href = u; else window.open(u, '_blank');
    setTimeout(() => URL.revokeObjectURL(u), 120000);
  } catch (e: any) { w?.close(); ElMessage.error($t('labelDesigner.failedToOpenPdf') + e.message); }
}
const openJobPdf = (id: number) => window.open(`/api/label/job/${id}/pdf?token=${tok()}`, '_blank');
function pdfPreview() { openPdf('/api/label/preview', { ...layoutBody(), format: 'pdf' }); }

// ───────────── New / duplicate ─────────────
const nt = reactive<any>({ open: false, code: '', name: '', usage: 'carton', width_mm: 100, height_mm: 60 });
function openNew() { Object.assign(nt, { open: true, code: '', name: '', usage: useF.value || 'carton', width_mm: 100, height_mm: 60 }); }
function setSize(s: string) { const [w, h] = s.split('×').map(Number); nt.width_mm = w; nt.height_mm = h; }
async function createTpl() {
  const vs = (await get('/api/label/variables?usage=' + nt.usage)).vars || [];
  const sm = Object.fromEntries(vs.map((v: any) => [v.key, v.sample]));
  const k0 = vs[0]?.key || 'code';
  const els0 = [{ type: 'box', x: 0.5, y: 0.5, w: nt.width_mm - 1, h: nt.height_mm - 1, t: 0.3, fill: false },
    { type: 'text', x: 3, y: 3, h: Math.min(4, nt.height_mm / 6), text: nt.name, bold: true, align: 'left', rotate: 0 }];
  if (nt.height_mm >= 20) els0.push({ type: 'barcode', x: 3, y: Math.min(10, nt.height_mm / 2), w: Math.max(10, Math.min(60, nt.width_mm - 6)), h: Math.max(6, Math.min(12, nt.height_mm / 3)), symbology: 'code128', data: `{{${k0}}}`, hr: true, gs1: false } as any);
  const r = await post('/api/e/label_template', { code: nt.code.trim(), name: nt.name.trim(), usage: nt.usage, width_mm: nt.width_mm, height_mm: nt.height_mm, status: 'draft', sample: sm, layout: { elements: els0 } });
  nt.open = false;
  await loadTpls();
  const id = r.id || r.item?.id || tpls.value.find((x) => x.code === nt.code.trim())?.id;
  ElMessage.success($t('labelDesigner.created')); if (id) await pick(id);
}
async function copyTpl() {
  if (!T.id) return;
  const r = await post(`/api/e/label_template/${T.id}/do/copy`, {});
  ElMessage.success(r.msg || $t('common.copied'));
  await loadTpls(); if (r.id) await pick(r.id);
}

// ───────────── Print ─────────────
const printers = ref<any[]>([]), markers = ref<any[]>([]);
async function loadDevs() { [printers.value, markers.value] = await Promise.all([get('/api/e/printer?size=200&sort=code').then((r) => r.items), get('/api/e/marker?size=200&sort=code').then((r) => r.items)]); }
const pd = reactive<any>({ open: false, dev: '', src: 'ref', ref: '', copies: 1, busy: false, res: null });
async function openPrint() {
  await loadDevs();
  const firstP = printers.value.find((p) => p.enabled && p.kind === 'browser') || printers.value.find((p) => p.enabled);
  const ok = (v: string) => { const [k, id] = v.split(':'); return (k === 'p' ? printers.value : markers.value).some((x) => x.enabled && String(x.id) === id); };
  const firstM = markers.value.find((m) => m.enabled);
  if (!pd.dev || !ok(pd.dev)) pd.dev = firstP ? 'p:' + firstP.id : firstM ? 'm:' + firstM.id : '';
  pd.src = REF_HINT[T.usage] ? pd.src : 'sample'; pd.res = null; pd.open = true;
}
async function doPrint() {
  pd.busy = true; pd.res = null;
  try {
    if (dirty.value && !(await save(true))) return;
    const [k, id] = pd.dev.split(':');
    const body: any = { template: T.id, copies: pd.copies, wait: 5, ...(k === 'm' ? { marker: +id } : { printer: +id }) };
    if (pd.src === 'ref') body.ref = pd.ref.trim(); else body.data = sample.value;
    const r = await post('/api/label/print', body);
    if (r.pdf) {
      window.open(`${r.pdf}?token=${tok()}`, '_blank');
      pd.res = { type: 'success', msg: $t('labelDesigner.job0BrowserPrintPdf', [r.code]) };
    } else {
      const st = r.status;
      pd.res = { type: st === 'done' ? 'success' : st === 'failed' || r.last_error ? 'error' : 'info',
        msg: $t('labelDesigner.job0123', [r.code, JST[st] || st, r.last_error ? $t('common.k362') + r.last_error : '', st === 'queued' && r.attempts ? $t('labelDesigner.willRetryAutomatically') : '']) };
    }
  } catch (e: any) { pd.res = { type: 'error', msg: e.message }; } finally { pd.busy = false; }
}

// ───────────── Devices ─────────────
const addr = (r: any) => (r.kind === 'browser' ? '—' : r.host ? `${r.host}:${r.port}` : $t('aiSettings.notConfigured'));
async function toggle(ent: string, row: any, v: any) {
  try { await put(`/api/e/${ent}/${row.id}`, { enabled: !!v }); ElMessage.success(v ? $t('agentEditor.enabled') : $t('agentEditor.disabled')); } catch { row.enabled = !v; }
}
async function testDev(ent: 'printer' | 'marker', row: any) {
  try {
    if (ent === 'printer' && row.kind === 'browser') {
      await openPdf('/api/label/preview', { width_mm: 60, height_mm: 40, format: 'pdf', data: {}, layout: { elements: [
        { type: 'box', x: 1, y: 1, w: 58, h: 38, t: 0.4 }, { type: 'text', x: 4, y: 3, h: 4, text: $t('labelDesigner.honeymomTestPrint'), bold: true },
        { type: 'text', x: 4, y: 9, h: 2.8, text: `${row.code}  ${new Date().toLocaleString(intlLocale)}` }, { type: 'barcode', x: 4, y: 15, w: 34, h: 10, symbology: 'code128', data: 'TEST-' + row.code, hr: true },
        { type: 'barcode', x: 44, y: 15, w: 12, h: 12, symbology: 'datamatrix', data: 'TEST-' + row.code }] } });
      return;
    }
    const r = await post(`/api/e/${ent}/${row.id}/do/test`, {});
    ElMessage.success(r.msg || $t('labelDesigner.sent'));
  } finally { await loadDevs(); }
}
const dv = reactive<any>({ open: false, kind: 'printer', f: {} });
function editDev(kind: 'printer' | 'marker', row?: any) {
  dv.kind = kind;
  dv.f = row ? { ...row } : kind === 'printer' ? { code: '', name: '', kind: 'zpl', host: '', port: 9100, dpi: 203, enabled: true, location: '' }
    : { code: '', name: '', mode: 'tcp', host: '', port: 9000, cmd_template: 'MARK {job} {data}\\r\\n', ack: 'OK', err: 'ERR|NG', timeout_ms: 5000, symbology: 'datamatrix', enabled: true, location: '' };
  dv.open = true;
}
async function saveDev() {
  const f = { ...dv.f };
  for (const k of ['id', 'status', 'last_ok', 'last_error', 'created_at', 'updated_at']) delete f[k];
  if (dv.f.id) await put(`/api/e/${dv.kind}/${dv.f.id}`, f); else await post(`/api/e/${dv.kind}`, f);
  ElMessage.success($t('common.saved')); dv.open = false; await loadDevs();
}

// ───────────── Rules ─────────────
const rules = ref<any[]>([]), materials = ref<any[]>([]);
async function loadRules() { rules.value = (await get('/api/e/label_rule?size=200')).items; }
const rd = reactive<any>({ open: false, f: {} });
async function editRule(row?: any) {
  if (!materials.value.length) materials.value = (await get('/api/e/material?size=500', true).catch(() => ({ items: [] }))).items || [];
  if (!printers.value.length) await loadDevs();
  rd.f = row ? { id: row.id, event: row.event, template: row.template, printer: row.printer, marker: row.marker, copies: row.copies || 1, customer: row.customer || '', material: row.material, remark: row.remark || '', enabled: !!row.enabled }
    : { event: 'carton_close', template: null, printer: printers.value.find((p) => p.kind === 'browser')?.id || null, marker: null, copies: 1, customer: '', material: null, remark: '', enabled: true };
  rd.open = true;
}
async function saveRule() {
  const { id, ...f } = rd.f;
  f.customer = f.customer || null; f.printer = f.printer || null; f.marker = f.marker || null; f.material = f.material || null;
  if (id) await put(`/api/e/label_rule/${id}`, f); else await post('/api/e/label_rule', f);
  ElMessage.success($t('common.saved')); rd.open = false; await loadRules();
}
async function delRule(row: any) {
  await ElMessageBox.confirm($t('labelDesigner.deleteRule01', [EVT[row.event] || row.event, row.template__label]), $t('common.delete'), { type: 'warning' });
  await del(`/api/e/label_rule/${row.id}`); ElMessage.success($t('common.deleted')); await loadRules();
}

// ───────────── Jobs ─────────────
const jobs = ref<any[]>([]), jobCounts = ref<Record<string, number>>({});
const jf = reactive({ status: '', ref: '' });
const jobTotal = computed(() => Object.values(jobCounts.value).reduce((a, b) => a + Number(b || 0), 0));
async function loadJobs() {
  const r = await get(`/api/label/jobs?status=${jf.status}&ref=${encodeURIComponent(jf.ref.trim())}&limit=200`, true).catch(() => null);
  if (!r) return;
  jobs.value = r.items; jobCounts.value = r.counts || {};
}
const head = (s: string) => String(s || '').split(/\r?\n/).slice(0, 12).join('\n');
async function jobDo(row: any, act: 'retry' | 'cancel') { const r = await post(`/api/e/print_job/${row.id}/do/${act}`, {}); ElMessage.success(r.msg); await loadJobs(); }
async function reprint(row: any) {
  let reason = '';
  try {
    ({ value: reason } = await ElMessageBox.prompt($t('labelDesigner.reprint01StateThe', [row.code, row.ref || row.target || '']), $t('labelDesigner.reprintLabel'), {
      inputPlaceholder: $t('labelDesigner.eGLabelSoiledWrong'), inputValidator: (v: string) => (v || '').trim().length >= 2 || $t('labelDesigner.pleaseEnterTheReprintReason') }) as any);
  } catch { return; }
  const r = await post('/api/label/reprint', { job: row.id, reason: reason.trim() });
  ElMessage.success($t('labelDesigner.reprinted0', [r.code]));
  if (r.pdf) window.open(`${r.pdf}?token=${tok()}`, '_blank');
  await loadJobs();
}
let poll: any = null;

// ───────────── Lifecycle ─────────────
const loaders: Record<string, () => Promise<any>> = { design: async () => {}, devices: loadDevs, rules: async () => { await Promise.all([loadRules(), loadDevs()]); }, jobs: loadJobs };
watch(tab, (t) => { loaders[t]?.(); });
onMounted(async () => {
  window.addEventListener('keydown', onKey);
  poll = setInterval(() => { if (tab.value === 'jobs' && document.visibilityState === 'visible') loadJobs(); }, 5000);
  await Promise.all([loadTpls(), loadDevs()]);
  let last = 0;
  try { last = Number(localStorage.getItem('hm_lbl_tpl')) || 0; } catch { /* no storage */ }
  const first = tpls.value.find((t) => t.id === last) || tpls.value[0];
  if (first) await pick(first.id);
});
onUnmounted(() => { window.removeEventListener('keydown', onKey); clearInterval(poll); ro?.disconnect(); clearTimeout(tmr); });
</script>

<style scoped>
.tabs { margin-bottom: 4px; }
.muted { color: var(--hm-text-3); } .small { font-size: var(--hm-fs-2xs); } .grow { flex: 1; }
.err { color: var(--hm-fault-ink); }
.row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ml { margin-left: 4px; }
.mt-s { margin-top: 8px; }

/* Design area: template list | toolbar + canvas | properties */
.dz { display: grid; grid-template-columns: minmax(196px, 230px) minmax(0, 1fr) minmax(250px, 300px); grid-template-rows: auto 1fr; gap: var(--hm-gap); align-items: start; }
.tl { grid-row: 1 / span 2; }
.tb { grid-column: 2 / span 2; }
.tg { display: inline-flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.tg + .tg { padding-left: 8px; border-left: 1px solid var(--hm-border); }
.tg .el-button + .el-button { margin-left: 0; }
.zv { min-width: 44px; text-align: center; font-size: var(--hm-fs-xs); color: var(--hm-text-2); }
.dv { margin-left: 10px; font-size: var(--hm-fs-2xs); }
.tl-f { display: flex; flex-direction: column; gap: 6px; padding: 0 10px 8px; }
.tl-list { max-height: max(380px, calc(100vh - 330px)); overflow: auto; border-top: 1px solid var(--hm-border); }
.ti { display: flex; flex-direction: column; gap: 3px; width: 100%; padding: 8px 12px; text-align: left; background: transparent; border: 0; border-bottom: 1px solid var(--hm-hairline);
  cursor: pointer; color: var(--hm-text); font: inherit; }
.ti:hover { background: var(--hm-surface-3); }
.ti.on { background: var(--hm-primary-soft); box-shadow: inset 3px 0 0 var(--hm-primary); }
.ti-c { font-size: var(--hm-fs-xs); color: var(--hm-text); font-weight: 600; word-break: break-all; }
.ti-n { font-size: var(--hm-fs-xs); color: var(--hm-text-2); line-height: 1.4; }
.ti-m { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.ti.on .ti-m .muted { color: var(--hm-text-2); }
.cv { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.cv-c { height: max(360px, calc(100vh - 340px)); }
.cv-empty { display: grid; place-items: center; background: var(--hm-surface-2); border: 1px solid var(--hm-border); border-radius: var(--hm-r-md); }
.sb { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: var(--hm-fs-2xs); color: var(--hm-text-2); }
.probs ul { margin: 4px 0 0; padding-left: 18px; }
.pp-b { padding: 4px 12px 12px; max-height: max(380px, calc(100vh - 300px)); overflow: auto; }
.pf { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 10px; }
.fl { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.fl.full { grid-column: 1 / -1; }
.fl :deep(.el-input-number), .fl :deep(.el-select) { width: 100%; }
.lb { font-size: var(--hm-fs-2xs); color: var(--hm-text-2); }
.tip { margin: 10px 0 0; }
@media (max-width: 1360px) {
  .dz { grid-template-columns: minmax(180px, 210px) minmax(0, 1fr); }
  .tb, .pp { grid-column: 2; }
}

/* Job expanded */
.jx { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; padding: 6px 16px 10px 48px; }
.jx b { display: block; font-size: var(--hm-fs-xs); margin-bottom: 6px; }
.lg { display: flex; align-items: baseline; gap: 8px; font-size: var(--hm-fs-xs); padding: 2px 0; flex-wrap: wrap; }
.pl, .payload { margin: 0; padding: 8px 10px; background: var(--hm-surface-2); border: 1px solid var(--hm-border); border-radius: var(--hm-r-sm); font-size: var(--hm-fs-2xs);
  line-height: 1.5; white-space: pre-wrap; word-break: break-all; color: var(--hm-text); }
.payload { margin-top: 10px; max-height: 56vh; overflow: auto; }
.pl-ul { margin: 0; padding-left: 18px; }
</style>
