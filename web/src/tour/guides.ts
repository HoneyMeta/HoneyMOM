// Tour content: for each module "what it's for, when to use it, what to do next", three task walkthroughs (production report, quality inspection, scheduling), welcome tour on first login.
// Step format: see the top of engine.ts. Targets prefer data-tour="name"; later array entries are fallbacks (still found if someone changes the page).
// To add a tour for a new page: add an entry to GUIDES with routes set to the page path; pages using <HmPageHead> get the "?" automatically.
import { $t } from '../i18n';
import type { Guide, GuideStep } from './engine';
import { IMPL_GUIDES } from './guides_impl';          // M3: Setup Wizard, data import, industry templates, custom fields, forms, approval workflows, report design
import { EXEC_GUIDES } from './guides_exec';          // M5: labels, packing & shipping, outsourcing, spare parts, controlled documents, ECN, gauges, PFMEA, MSA, customer complaints

const T = (n: string) => ({ tour: n });
const S = (sel: string) => ({ sel });
const M = (s: GuideStep): GuideStep => ({ manual: true, ...s });
const HEAD = [T('page-head'), S('.hm-main .page-head'), S('.hm-main h2')];
/** Pages needing an AI feature show the "Disabled" page when it's off: the tour points to it (explaining how to enable) */
const OFF = S('[data-test=feature-off]');
const TABLE = [T('ent-table'), S('.hm-main .el-table'), S('.hm-main .hm-table'), S('.hm-main table')];
const KPIS = [T('kpis'), S('.hm-main .hm-kpi')];
const FIRST_ROW = '.hm-main .el-table__body tr.el-table__row';

/** A module tour: the first step covers "what it is, when to use it" (spotlighting the page title), later steps cover the page's main areas */
function mod(key: string, title: string, routes: string[], what: string, steps: GuideStep[], kw = ''): Guide {
  return { key, title, kind: 'module', desc: what.replace(/\*\*/g, '').slice(0, 40), routes, kw,
    steps: [M({ id: 'what', title: $t('common.n0WhatItSFor', [title]), do: what, target: [...HEAD, OFF], route: routes[0] }), ...steps.map(M)] };
}

export const GUIDES: Guide[] = [
  // ---------------- Connectivity Center (5.0 M2) ----------------
  mod('onboard', $t('common.connectivity'), ['/onboard'], $t('guides.whereARealPlantS'),
    [{ id: 'kpi', title: $t('common.coverage'), target: [T('onboard-kpis')], do: $t('guides.coverageConfirmedTagsAllTags') },
      { id: 'pkg', title: $t('overviewTab.importDeliveryPackage'), target: [T('onboard-package')], do: $t('guides.uploadTheIntegratorSZip') },
      { id: 'src', title: $t('common.dataSource'), target: [T('onboard-sources')], do: $t('guides.oneDataSourceEachFor') }]),
  mod('onboard-check', $t('common.tagCheck'), ['/onboard/check'], $t('guides.checkTagsOneByOne'),
    [{ id: 'f', title: $t('implReports.filters'), target: [T('check-filters')], do: $t('guides.filterByDataSourceEquipment') },
      { id: 't', title: $t('overviewTab.tags'), target: [T('check-table')], do: $t('guides.eachRowShowsTheLast') },
      { id: 'c', title: $t('guides.bulkConfirm'), target: [T('check-confirm')], do: $t('guides.confirmAllGreenTagsAt') }]),
  mod('onboard-discover', $t('common.discoverBrowse'), ['/onboard/discover'], $t('guides.useWhenTheTagList'),
    [{ id: 'o', title: $t('discoverTab.opcUaBrowse'), target: [T('discover-opcua')], do: $t('guides.expandNodesToSeeAttributes') },
      { id: 'm', title: $t('guides.mqttDiscovery'), target: [T('discover-mqtt')], do: $t('guides.chooseADataSourceEnter') }]),
  mod('onboard-model', $t('common.plantModel'), ['/onboard/model'], $t('guides.oneModelDrivesTheEquipment'),
    [{ id: 't', title: $t('guides.hierarchy'), target: [T('model-tree')], do: $t('guides.clickEquipmentToSeeIts') },
      { id: 'p', title: $t('common.equipmentTypeTemplates'), target: [T('model-templates')], do: $t('guides.aTemplateDefinesTheStandard') },
      { id: 'v', title: $t('common.versions'), target: [T('model-versions')], do: $t('guides.publishAVersionWhenA') }]),
  mod('onboard-twin', $t('common.floorPlanDigitalTwin'), ['/onboard/twin'], $t('guides.afterImportingTheWorkshopCad'),
    [{ id: 'b', title: $t('guides.toolbar'), target: [T('twin-bar'), S('[data-test=twin-view]'), S('[data-test=onboard-twin]')], do: $t('guides.n2d3dToggleImportDxf') }]),
  mod('onboard-ai', $t('onboard.connectivityAiAssist'), ['/onboard/ai'], $t('guides.onSiteTagListsOften'),
    [{ id: 'd', title: $t('common.draft2'), target: [T('ai-draft'), OFF], do: $t('guides.draftsForTagsAwaitingConfirmation') },
      { id: 'r', title: $t('common.approve2'), target: [T('ai-drafts'), OFF], do: $t('guides.youCanEditEquipmentMeaning') },
      { id: 's', title: $t('guides.suspiciousTags'), target: [T('ai-suspicious'), OFF], do: $t('guides.noAiNeededValuesThat') }]),
  // ---------------- Welcome ----------------
  { key: 'welcome', title: $t('guides.welcomeToHoneymom'), kind: 'welcome', desc: $t('guides.getToKnowTheInterface'), routes: [], kw: $t('guides.welcomeNewUserGettingStarted'),
    outro: $t('guides.thatSItFromNow'),
    steps: [
      M({ id: 'hi', title: $t('common.welcome'), route: '/', do: $t('guides.honeymomBringsProductionQualityEquip') }),
      M({ id: 'nav', title: $t('guides.leftMenu'), target: [S('[data-test=side-nav]'), S('.hm-nav')], do: $t('guides.groupedByBusinessAreaProduction') }),
      M({ id: 'cmd', title: $t('guides.searchAnything'), target: [S('[data-test=cmd-bar]')], do: $t('guides.pressCtrlKSearchPages') }),
      M({ id: 'clock', title: $t('guides.dataClockCollectionStatus'), target: [S('[data-test=data-clock]'), S('[data-test=conn-status]')], do: $t('guides.timeIsBasedOnData') }),
      M({ id: 'alarm', title: $t('guides.alarmsNotifications'), target: [S('[data-test=alarm-count]'), S('[data-test=notices]')], do: $t('guides.theRedNumberIsActive') }),
      M({ id: 'ai', title: $t('guides.handDataToAi'), target: [S('[data-test=ai-toggle]')], do: $t('guides.anyNumberTableRowOr'),
        why: $t('guides.aiIsOffByDefault') }),
      M({ id: 'help', title: $t('guides.openATourAnytime'), target: [T('page-help'), T('shell-help')], do: $t('guides.everyPageTitleHasA') }),
    ] },

  // ---------------- Task walkthroughs ----------------
  { key: 'task-report', title: $t('guides.walkthroughReportProduction'), kind: 'task', desc: $t('guides.scanReportProductionVerifyOn'), kw: $t('guides.reportProductionScanMobileTask'),
    outro: $t('guides.productionReportedWorkOrderProgress'),
    steps: [
      M({ id: 'scan', title: $t('guides.n1ScanThePartCode'), route: '/m', target: [T('m-scan'), S('[data-test=m-scan] input'), S('.mob input'), S('input')],
        do: $t('guides.openMobileAtTheWorkstation') }),
      M({ id: 'form', title: $t('guides.n2EnterOperationQuantity'), target: [T('m-form'), S('[data-test=m-scan] .qty2'), S('[data-test=m-good]'), S('[data-test=m-scan]'), S('.mob .el-form')],
        do: $t('guides.onceAPartIsScanned') }),
      M({ id: 'submit', title: $t('guides.n3SubmitTheReport'), target: [T('m-submit'), S('[data-test=m-report]'), S('[data-test=m-scan]'), S('.mob .el-button--primary')],
        do: $t('guides.tapReportOperationsThatEquipment') }),
      M({ id: 'verify', title: $t('guides.n4VerifyOnTheDesktop'), route: '/e/report', target: [T('report-table'), ...TABLE],
        do: $t('guides.backOnTheDesktopOpen') }),
      M({ id: 'wo', title: $t('guides.n5CheckWorkOrderProgress'), route: '/e/work_order', target: TABLE,
        do: $t('guides.theWorkOrderSCompleted') }),
    ] },
  { key: 'task-quality', title: $t('guides.walkthroughQualityInspection'), kind: 'task', desc: $t('guides.inspectionNonconformanceDisposition'), kw: $t('guides.qualityInspectionNonconformanceNcrDi'),
    outro: $t('guides.theQualityLoopInspectionDecision'),
    steps: [
      M({ id: 'new', title: $t('guides.n1OpenAnInspection'), route: '/e/insp_record', target: [T('ent-add'), T('ent-toolbar')],
        do: $t('guides.firstArticlePatrolAndFinal') }),
      M({ id: 'values', title: $t('guides.n2EnterInspectionValues'), click: FIRST_ROW, target: [T('ent-insp-values'), T('ent-actions')],
        do: $t('guides.openTheInspectionAndClick') }),
      M({ id: 'ncr', title: $t('guides.n3Nonconformances'), route: '/e/ncr', target: [T('ncr-table'), S('[data-test=ncr-table]'), ...TABLE],
        do: $t('guides.partsJudgedNonconformingGenerateA') }),
      M({ id: 'dispose', title: $t('guides.n4Disposition'), click: FIRST_ROW, target: [T('ncr-actions'), T('ent-actions'), S('.el-drawer .acts')],
        do: $t('guides.openAnNcrAndDisposition') }),
      M({ id: 'stats', title: $t('guides.n5ReviewStatistics'), route: '/quality', target: [T('q-pareto'), T('q-disp'), ...KPIS],
        do: $t('guides.inQualityOverviewCheckThe') }),
    ] },
  { key: 'task-plan', title: $t('guides.walkthroughScheduling'), kind: 'task', desc: $t('guides.createWorkOrderScheduleDispatch'), kw: $t('guides.schedulingWorkOrderReleaseDispatch'),
    outro: $t('guides.thatSThePathFrom'),
    steps: [
      M({ id: 'new', title: $t('guides.n1CreateAWorkOrder'), route: '/e/work_order', target: [T('ent-add'), T('ent-toolbar')],
        do: $t('guides.clickNewChooseTheProduct') }),
      M({ id: 'release', title: $t('guides.n2Release'), click: FIRST_ROW, target: [T('ent-actions')],
        do: $t('guides.openTheWorkOrderAnd') }),
      M({ id: 'gantt', title: $t('guides.n3Schedule'), route: '/production/gantt', target: [T('gantt-reschedule'), S('.hm-main .page-head .el-button--primary'), ...HEAD],
        do: $t('guides.schedulingProgressSchedulesByPriorit') }),
      M({ id: 'bars', title: $t('guides.n4ReviewTheSchedule'), target: [T('gantt'), S('.hm-main .gantt'), S('.hm-main .card')],
        do: $t('guides.oneBarPerOperationIts') }),
      M({ id: 'dispatch', title: $t('guides.n5DispatchToWorkstations'), route: '/e/work_order', click: FIRST_ROW, target: [T('ent-children'), T('ent-actions')],
        do: $t('guides.belowTheWorkOrderDetails') }),
      M({ id: 'proposal', title: $t('guides.n6ScheduleAdjustmentSuggestions'), route: '/e/schedule_proposal', target: [T('proposal-table'), ...TABLE],
        do: $t('guides.whenEquipmentGoesDownOr') }),
    ] },

  // ---------------- Module tours ----------------
  mod('home', $t('common.home'), ['/'], $t('guides.theFirstPageAfterLogin'), [
    { id: 'kpi', title: $t('home.keyMetrics'), target: KPIS, do: $t('guides.clickAMetricToOpen') },
    { id: 'next', title: $t('common.next'), target: [S('[data-test=side-nav]')], do: $t('guides.toActOnSomethingSpecific') },
  ], $t('guides.homeDashboardCockpitStart')),
  mod('monitor', $t('common.liveMonitor'), ['/equipment/monitor'], $t('guides.eachMachineSCurrentStatus'), [
    { id: 'filter', title: $t('guides.showOnlyProblems'), target: [T('mon-filter')], do: $t('guides.switchToDownOrWith') },
    { id: 'card', title: $t('guides.equipmentCards'), target: [T('mon-card'), S('.hm-main .eq')], do: $t('guides.eachCardEquipmentStatusPower') },
    { id: 'next', title: $t('common.next'), target: [T('mon-card'), S('.hm-main .eq')], do: $t('guides.forMachinesWithALot') },
  ], $t('guides.equipmentStatusLiveMonitorMachines')),
  mod('oee', $t('common.oee'), ['/equipment/oee'], $t('guides.oeeAvailabilityPerformanceQualityAll'), [
    { id: 'range', title: $t('guides.timeRange'), target: [T('oee-range')], do: $t('guides.chooseFromTheLast2') },
    { id: 'chart', title: $t('guides.compareMachines'), target: [T('oee-chart')], do: $t('guides.barsAreOeeTheThree') },
    { id: 'trend', title: $t('oee.machineTimeline'), target: [T('oee-trend')], do: $t('guides.hourlyOeeAndStatusTimeline') },
  ], $t('nav.oeeUtilization')),
  mod('maint', $t('common.checksPm'), ['/equipment/maint'], $t('guides.inspectionRoundsPmAndLubrication'), [
    { id: 'list', title: $t('guides.dueTasks'), target: [T('maint-table'), ...TABLE], do: $t('guides.overdueItemsAreRedWith') },
    { id: 'next', title: $t('guides.plansRecords'), target: [T('maint-links')], do: $t('guides.changeIntervalsAndContentIn') },
  ], $t('guides.inspectionRoundsPmMaintenanceChecks')),
  // 5.0 M6: cycle time analysis / AI skills / MCP connectivity
  mod('cycle', $t('common.cycleTimeAnalysis'), ['/cycle'], $t('guides.automaticallySlicesEveryCycleAnd'), [
    { id: 'kpis', title: $t('guides.bottlenecksLosses'), target: [T('cyc-kpis')], do: $t('guides.bottleneckWorkstationTheOneWith') },
    { id: 'line', title: $t('cycle.lineCycleTime'), target: [T('cyc-line-chart')], do: $t('guides.perPieceCycleTimeFor') },
    { id: 'losses', title: $t('guides.slowestMotions'), target: [T('cyc-losses')], do: $t('guides.allMotionsSlowerThanStandard') },
    { id: 'stations', title: $t('guides.workstationsModeling'), target: [T('cyc-stations')], do: $t('guides.forMachinesWithMotionSignals') },
    { id: 'drill', title: $t('guides.singleMachineMotionDetails'), target: [T('cyc-drill'), T('cyc-stations')], do: $t('guides.medianP90StandardBaselineFor') },
  ], $t('guides.cycleTimeAnalysisTaktMotion')),
  mod('skills', $t('common.aiSkills'), ['/ai/skills'], $t('guides.whenYouGetAGood'), [
    { id: 'list', title: $t('skills.skillLibrary'), target: [T('skl-list'), OFF], do: $t('guides.yourOwnSkillsAndSkills') },
    { id: 'run', title: $t('guides.runWithDifferentParameters'), target: [S('[data-test=skl-run]'), S('[data-test=skl-detail]'), T('skl-list'), OFF], do: $t('guides.changeParametersLineTimeRange') },
    { id: 'sched', title: $t('guides.scheduleDestination'), target: [S('[data-test=skl-mode]'), S('[data-test=skl-detail]'), T('skl-list'), OFF], do: $t('guides.hourlyDailyEndOfShift') },
  ], $t('guides.skillSkillsAiScheduled')),
  mod('mcp', $t('common.mcpAccess'), ['/system/mcp'], $t('guides.letYourOwnAiTools'), [
    { id: 'key', title: $t('guides.createKey'), target: [T('mcp-key')], do: $t('guides.chooseAPermissionPresetAll') },
    { id: 'clients', title: $t('guides.connectToAiTools'), target: [T('mcp-clients')], do: $t('guides.copyTheConfigForYour') },
    { id: 'tools', title: $t('guides.toolsTryIt'), target: [T('mcp-tools'), T('mcp-try')], do: $t('guides.toolsAvailableToExternalAi') },
    { id: 'calls', title: $t('guides.callLog'), target: [T('mcp-calls')], do: $t('guides.everyConnectionAndCallIs') },
  ], 'mcp claude cursor dify'),
  mod('andon', $t('common.andonBoard'), ['/andon'], $t('guides.whenEquipmentQualityMaterialOr'), [
    { id: 'raise', title: $t('andon.raiseAndon2'), target: [T('andon-raise')], do: $t('guides.chooseTheTypeAndEquipment') },
    { id: 'open', title: $t('andon.openAndonCalls'), target: [T('andon-open')], do: $t('guides.iLlRespondMeansSomeone') },
    { id: 'board', title: $t('andon.boardMessages'), target: [T('andon-board')], do: $t('guides.agentTriageSuggestionsAndRecently') },
  ], 'ad andon'),
  mod('quality', $t('common.qualityOverview'), ['/quality'], $t('guides.inspectionVolumeNgRatePending'), [
    { id: 'kpi', title: $t('screen.qualityKpis'), target: KPIS, do: $t('guides.clickPendingNcrsToDisposition') },
    { id: 'pareto', title: $t('quality.defectPareto'), target: [T('q-pareto')], do: $t('guides.barsRunFromMostTo') },
    { id: 'disp', title: $t('guides.dispositionBreakdown'), target: [T('q-disp')], do: $t('guides.howNonconformancesWereDispositionedR') },
  ], 'zl quality'),
  mod('spc', $t('common.spc'), ['/quality/spc'], $t('guides.xRControlChartsProcess'), [
    { id: 'pick', title: $t('guides.chooseCharacteristicEquipment'), target: [T('spc-filter')], do: $t('guides.chooseTheInspectionCharacteristicE') },
    { id: 'kpi', title: $t('guides.capabilityIndices'), target: KPIS, do: $t('guides.cpk133IsCapable') },
    { id: 'x', title: $t('spc.xChart'), target: [T('spc-x')], do: $t('guides.redDotsAreOutOf') },
  ], $t('nav.spcCpkControlChart')),
  mod('machdefects', $t('common.machiningDefectStats'), ['/machining/defects'], $t('guides.scrapAndReworkStatisticsBy'), [
    { id: 'chart', title: $t('guides.compareMachines2'), target: [T('md-chart')], do: $t('guides.forMachinesWithHighScrap') },
    { id: 'import', title: $t('guides.importCmmData'), target: [T('md-import')], do: $t('guides.cmmAndFirstArticleMeasurement') },
  ], 'jjbl'),
  mod('tools', $t('common.toolLifeMonitoring'), ['/machining/tools'], $t('guides.toolLifeCountingAndWear'), [
    { id: 'kpi', title: $t('guides.toolStatus'), target: KPIS, do: $t('guides.countsOfWarningsAndTools') },
    { id: 'table', title: $t('guides.eachTool'), target: [T('tools-table'), ...TABLE], do: $t('guides.theProgressBarIsThe') },
  ], 'dj tool'),
  mod('stock', $t('common.stock'), ['/warehouse'], $t('guides.onHandStockAndSafety'), [
    { id: 'table', title: $t('guides.inventoryTable'), target: [T('stock-table'), ...TABLE], do: $t('guides.itemsBelowSafetyStockAre') },
    { id: 'count', title: $t('stock.cycleCount2'), target: [T('stock-count')], do: $t('guides.chooseAWarehouseEnterThe') },
  ], 'kc stock'),
  mod('heats', $t('common.heatAnalysis'), ['/foundry/heats'], $t('guides.eachHeatSChemistryPower'), [
    { id: 'trend', title: $t('heats.chemistryEnergyTrend'), target: [T('heats-trend')], do: $t('guides.linesForCSiAnd') },
    { id: 'table', title: $t('heats.heatDetails'), target: [T('heats-table'), ...TABLE], do: $t('guides.clickARowToOpen') },
    { id: 'next', title: $t('common.next'), target: [T('heats-charge')], do: $t('guides.whenChargeCostIsHigh') },
  ], $t('guides.heatMeltingFurnaceChemistry')),
  mod('charge', $t('nav.chargeOptimization'), ['/foundry/charge'], $t('guides.usesLinearProgrammingToFind'), [
    { id: 'target', title: $t('charge.targetChemistry'), target: [T('charge-target'), OFF], do: $t('guides.enterTheChargeWeightAnd') },
    { id: 'result', title: $t('guides.solution'), target: [T('charge-result'), OFF], do: $t('guides.compareCostAndSavingsPer') },
  ], 'plyh'),
  mod('energy', $t('common.energyAnalysis'), ['/energy'], $t('guides.electricityMeterReadingsAggregatedHo'), [
    { id: 'kpi', title: $t('guides.energyMetrics'), target: KPIS, do: $t('guides.furnaceEnergyPerTonIs') },
    { id: 'hourly', title: $t('energy.hourlyPower'), target: [T('energy-hourly')], do: $t('guides.stackedByEquipmentTypeFor') },
    { id: 'rank', title: $t('guides.equipmentRanking'), target: [T('energy-rank')], do: $t('guides.theBiggestPowerConsumersAre') },
  ], 'nh energy'),
  mod('backtrace', $t('nav.defectBacktrace'), ['/cross/backtrace'], $t('guides.defectsThatOnlyShowUp'), [
    { id: 'filter', title: $t('guides.chooseDefectTime'), target: [T('bt-filter')], do: $t('guides.chooseTheDefectCategoryAnd') },
    { id: 'factors', title: $t('guides.contributingFactors'), target: [T('bt-factors')], do: $t('guides.eachChartShowsTheDefect') },
    { id: '8d', title: $t('common.start8d'), target: [T('bt-8d')], do: $t('guides.onceConfirmedAsASystemic') },
  ], 'qxhs'),
  mod('coplan', $t('nav.castingMachiningCoScheduling'), ['/cross/coplan'], $t('guides.theMachiningBottleneckSetsThe'), [
    { id: 'caps', title: $t('guides.capacityByStage'), target: [T('cp-caps')], do: $t('guides.theBottleneckStageIsShown') },
    { id: 'plan', title: $t('guides.planForThreeShifts'), target: [T('cp-plan')], do: $t('guides.howManyPiecesMachiningAnd') },
  ], 'xtpc'),
  mod('trace', $t('common.traceability'), ['/trace'], $t('guides.enterAPartCodeOr'), [
    { id: 'search', title: $t('guides.enterAPartCode'), target: [T('trace-search'), S('.hm-main input')], do: $t('guides.scanOrEnterAPart') },
  ], 'zs'),
  mod('gantt', $t('common.schedulingProgress'), ['/production/gantt'], $t('guides.operationScheduleAndCompletionProgre'), [
    { id: 'bars', title: $t('watchFeed.schedule'), target: [T('gantt'), S('.hm-main .gantt'), S('.hm-main .card')], do: $t('guides.oneBarPerOperationThe') },
  ], 'pc gantt'),
  mod('ai-settings', $t('common.aiFeatures'), ['/system/ai'], $t('guides.adminsTurnOnTheAi'), [
    { id: 'master', title: $t('guides.masterSwitch'), target: [S('[data-test=ai-global]')], do: $t('guides.withTheMasterSwitchOff') },
    { id: 'mode', title: $t('common.runMode'), target: [S('[data-test=ai-mode]')], do: $t('guides.demoModeReplaysRecordedAnswers') },
  ], $t('guides.aiSwitchSettingsFeatures')),
  mod('system', $t('common.systemStatus'), ['/system'], $t('guides.whetherDataCollectionIsWorking'), [
    { id: 'ds', title: $t('common.dataSource'), target: [T('sys-ds'), ...TABLE], do: $t('guides.protocolAddressAndConnectionStatus') },
  ], 'xtzt'),
  mod('repair', $t('common.repairOrders'), ['/e/repair_order'], $t('guides.theFullEquipmentRepairProcess'), [
    { id: 'table', title: $t('guides.repairOrderList'), target: TABLE, do: $t('guides.filterByStatusRequestedDispatched') },
    { id: 'act', title: $t('guides.handling'), target: [T('ent-table')], do: $t('guides.clickARowToOpen2') },
  ], 'wx'),
  mod('equipment', $t('common.equipmentRegister'), ['/e/equipment'], $t('guides.equipmentMasterDataCodeType'), [
    { id: 'table', title: $t('guides.equipmentList'), target: TABLE, do: $t('guides.clickARowToSee') },
  ], 'sbtz'),
  mod('workorder', $t('common.workOrders'), ['/e/work_order'], $t('guides.theFullWorkOrderLifecycle'), [
    { id: 'table', title: $t('guides.repairOrderList'), target: TABLE, do: $t('guides.filterByStatusClickA') },
  ], 'scgd wo'),
  mod('insp', $t('nav.inspectionRecords'), ['/e/insp_record'], $t('guides.inspectionRecordsAndValuesFor'), [
    { id: 'table', title: $t('erpDocs.inspection'), target: TABLE, do: $t('guides.clickARowToOpen3') },
  ], 'jy'),
  mod('report8d', $t('common.n8dReports'), ['/e/report_8d'], $t('guides.launchAndTrack8dsFor'), [
    { id: 'table', title: $t('guides.reportList'), target: TABLE, do: $t('guides.clickARowToOpen4') },
  ], '8d'),
  mod('datasource', $t('common.dataSourcesTags'), ['/e/datasource'], $t('guides.configureDataCollectionMqttUnified'), [
    { id: 'table', title: $t('common.dataSource'), target: TABLE, do: $t('guides.clickARowToSee2') },
  ], $t('guides.dataSourcesTagListCollection')),

  // ---------------- Other pages (owned by H2 / H3; targets use generic selectors where possible) ----------------
  mod('reports', $t('common.productionReports'), ['/e/report'], $t('guides.everyProductionReportMobileWorkstati'), [
    { id: 'table', title: $t('guides.productionReportList'), target: [T('report-table'), ...TABLE], do: $t('guides.newestAtTheTopHold') },
  ], 'bg'),
  mod('ncr', $t('common.nonconformances'), ['/e/ncr'], $t('guides.partsJudgedNonconformingInInspection'), [
    { id: 'tabs', title: $t('guides.viewByScope'), target: [S('[data-test=ncr-tabs]'), ...TABLE], do: $t('guides.switchBetweenPendingAllAnd') },
  ], 'bhg ncr'),
  mod('gates', $t('guides.firstArticleHoldRework'), ['/quality/fai', '/quality/hold', '/quality/rework'], $t('guides.threeQualityGatesFirstArticle'), [
    { id: 'tabs', title: $t('guides.threeTabs'), target: [S('[data-test=qg-tabs]'), ...HEAD], do: $t('guides.switchBetweenTabsTheButtons') },
  ], $t('guides.firstArticleLotHoldRework')),
  mod('schedule-adjust', $t('common.scheduleChangeProposals'), ['/e/schedule_proposal'], $t('guides.scheduleAdjustmentSuggestionsRaisedW'), [
    { id: 'list', title: $t('common.recommendation'), target: [T('proposal-table'), ...TABLE, S('.hm-main .hm-card')], do: $t('guides.eachSuggestionExplainsWhichWork') },
  ], 'pctz'),
  mod('labor', $t('laborDaily.operatorDailyReportsLaborHours'), ['/labor/daily', '/labor/hours'], $t('guides.eachPersonSDailyProduction'), [
    { id: 'table', title: $t('daily.dailyReport'), target: [S('[data-test=ld-table]'), ...TABLE], do: $t('guides.checkQuantitiesAndLaborHours') },
  ], 'grrb gs'),
  mod('trace-recon', $t('common.traceabilityReconciliation'), ['/trace/recon'], $t('guides.checksSerializedPartDataFor'), [
    { id: 'list', title: $t('guides.reconciliationResults'), target: [...TABLE, S('.hm-main .hm-card')], do: $t('guides.clickIntoAPartWith') },
  ], 'zsdz'),
  mod('kb', $t('common.knowledgeBase'), ['/e/kb_doc'], $t('guides.sopsWorkInstructionsMaintenanceKnow'), [
    { id: 'list', title: $t('guides.documents'), target: [S('[data-test=kb-list]'), S('.hm-main .hm-toolbar'), ...TABLE], do: $t('guides.filterByTypeOperationOr') },
  ], 'zsk sop'),
  mod('daily', $t('common.productionDailyReport'), ['/ai/daily'], $t('guides.outputQualityEquipmentAndExceptions'), [
    { id: 'body', title: $t('guides.reportContent'), target: [S('.hm-main .hm-card'), ...HEAD], do: $t('guides.ctrlClickAnyNumberTo') },
  ], 'rb'),
  mod('ai-pages', $t('guides.aiPages'), ['/ai/chat', '/ai/watch', '/ai/automations', '/agents', '/approvals'], $t('guides.askDataAiWatchAutomations'), [
    { id: 'main', title: $t('guides.thisPage'), target: [S('[data-test=feature-off]'), S('.hm-main .hm-card'), ...HEAD], do: $t('guides.anythingAiDoesThatNeeds') },
  ], $t('guides.aiAgentsApprovalsWatchAutomations')),
  { key: 'station', title: $t('common.workstationTerminal'), kind: 'module', desc: $t('guides.fullScreenShopFloorPc'), routes: ['/station'], kw: $t('guides.workstationTerminalStationShopFloor'),
    steps: [M({ id: 'what', title: $t('guides.workstationTerminalWhatItS'), route: '/station', target: [S('[data-test=station]')], do: $t('guides.aFullScreenPageOn') }),
      M({ id: 'acts', title: $t('guides.commonActions'), target: [S('[data-test=act-report]'), S('[data-test=station-go]'), S('[data-test=station]')], do: $t('guides.selectTheWorkstationFirstThen') })] },
  { key: 'mobile', title: $t('topBar.mobile'), kind: 'module', desc: $t('guides.scanToReportMyTasks'), routes: ['/m'], kw: 'sjd mobile',
    steps: [M({ id: 'what', title: $t('guides.mobileWhatItSFor'), route: '/m', target: [S('[data-test=mobile]'), S('.mob')], do: $t('guides.useAPhoneOrTablet') })] },
  // ---------------- 5.0 M4 built-in ERP ----------------
  mod('erp-board', $t('common.orderBoard'), ['/sales/board'], $t('guides.salesOrderOnTimeDelivery'), [
    { id: 'kpi', title: $t('home.keyMetrics'), target: KPIS, do: $t('guides.onTimeDeliveryIsCalculated') },
    { id: 'risk', title: $t('erpSalesBoard.atRiskDeliverySchedules'), target: [T('board-risk'), ...TABLE], do: $t('guides.overdueExpectedLateDueSoon') },
    { id: 'cust', title: $t('guides.byCustomer'), target: [T('board-cust'), T('board-weeks')], do: $t('guides.openAmountSummarizedByCustomer') },
  ], $t('guides.orderBoardOtdOnTime')),
  mod('erp-docs', $t('guides.salesPurchasingDocuments'), ['/sales/orders', '/sales/quotes', '/sales/deliveries', '/sales/returns', '/purchase/orders', '/purchase/receipts', '/purchase/returns',
    '/finance/ar_invoices', '/finance/ap_invoices'],
  $t('guides.quotationsSalesOrdersDeliveriesRetur'), [
    { id: 'filter', title: $t('implReports.filters'), target: [T('erp-filter'), S('.hm-main .hm-toolbar')], do: $t('guides.searchByDocumentNumberBusiness') },
    { id: 'table', title: $t('guides.documentList'), target: [T('erp-table'), ...TABLE], do: $t('guides.clickARowToOpen5') },
    { id: 'new', title: $t('labelDesigner.new'), target: [T('erp-new'), ...HEAD], do: $t('guides.leaveUnitPriceEmptyTo') },
  ], $t('guides.salesOrderPurchaseOrderDelivery')),
  mod('erp-pboard', $t('common.purchasingBoard'), ['/purchase/board'], $t('guides.openPurchaseOrderLinesOverdue'), [
    { id: 'kpi', title: $t('home.keyMetrics'), target: KPIS, do: $t('guides.chaseOverdueDeliveriesFirstReceipts') },
    { id: 'open', title: $t('guides.outstanding'), target: [T('pb-open'), ...TABLE], do: $t('guides.clickARowToOpen6') },
    { id: 'perf', title: $t('guides.supplierPerformance'), target: [T('pb-perf'), ...TABLE], do: $t('guides.score60OnTimeDelivery') },
  ], $t('guides.purchasingBoardSupplierPerformancePr')),
  mod('erp-mrp', $t('common.mrp'), ['/mrp'], $t('guides.fromSalesOrderDeliverySchedules'), [
    { id: 'run', title: $t('common.running'), target: [T('mrp-run'), ...HEAD], do: $t('guides.chooseThePlanningHorizonAnd') },
    { id: 'table', title: $t('common.recommendation'), target: [T('mrp-table'), ...TABLE], do: $t('guides.sourceTracesDemandBackTo') },
    { id: 'release', title: $t('guides.release'), target: [T('mrp-release'), T('mrp-filter')], do: $t('guides.tickAndReleaseProductionDraft') },
    { id: 'cap', title: $t('guides.roughCutCapacity'), target: [T('mrp-cap'), ...TABLE], do: $t('guides.loadPerWorkstationGroupWeek') },
  ], $t('guides.mrpMaterialRequirementsPlanning')),
  mod('erp-cost', $t('common.costing'), ['/finance/cost'], $t('guides.workOrderActualCostMaterial'), [
    { id: 'tabs', title: $t('guides.tabs'), target: [T('cost-tabs'), ...HEAD], do: $t('guides.workOrderCostsVarianceAnalysis') },
    { id: 'table', title: $t('erpCost.workOrderCosts'), target: [T('cost-table'), ...TABLE, ...KPIS], do: $t('guides.clickARowToSee3') },
  ], $t('guides.costingCostAccountingVariance')),
  mod('erp-arap', $t('common.arAp'), ['/finance/arap'], $t('guides.arShippedReturnsReceiptsMatches'), [
    { id: 'kpi', title: $t('erpArAp.balance'), target: KPIS, do: $t('guides.arAndApBalancesAnd') },
    { id: 'tabs', title: $t('guides.tabs'), target: [T('arap-tabs'), ...HEAD], do: $t('guides.arApAgingExpandTo') },
  ], $t('guides.arApReceivablesPayablesAging')),
  mod('erp-voucher', $t('common.voucherExport'), ['/finance/vouchers'], $t('guides.generatesAccountingVouchersBalancedD'), [
    { id: 'bar', title: $t('guides.generateExport'), target: [T('v-bar'), S('.hm-main .hm-toolbar')], do: $t('guides.chooseAPeriodGenerateOne') },
    { id: 'table', title: $t('guides.vouchersEntries'), target: [T('v-table'), ...TABLE], do: $t('guides.clickAVoucherToSee') },
  ], $t('guides.vouchersJournalKingdeeYonyouAccounti')),
  mod('erp-int', $t('common.erpIntegration'), ['/system/erp'], $t('guides.chooseBuiltInErpOr'), [
    { id: 'tabs', title: $t('guides.tabs'), target: [T('int-tabs'), ...HEAD], do: $t('guides.modeOwnershipQueueFailuresWebhooks') },
    { id: 'mode', title: $t('guides.whoIsMaster'), target: [T('int-mode-card'), ...HEAD], do: $t('guides.inExternalErpModeErp') },
  ], $t('guides.erpIntegrationKingdeeYonyouWebhook')),
  // Shared tour for the remaining data table pages (/e/xxx)
  { key: 'entity', title: $t('guides.dataTablePages'), kind: 'module', desc: $t('guides.searchFilterCreateDetailsExport'), routes: [], kw: $t('guides.dataTableListCreateExport'),
    steps: [
      M({ id: 'what', title: $t('guides.dataTablePages'), target: HEAD, do: $t('guides.materialsBomsRoutingsEmployeesWareho') }),
      M({ id: 'bar', title: $t('guides.searchFilterCreate'), target: [T('ent-toolbar')], do: $t('guides.searchByCodeOrName') }),
      M({ id: 'table', title: $t('common.table'), target: TABLE, do: $t('guides.clickAHeaderToSort') }),
    ] },
  ...IMPL_GUIDES,
  ...EXEC_GUIDES,
];

/** Which tour a path uses: first a module tour listing this path in routes, /e/xxx uses the generic data table tour, otherwise null */
export function guideForPath(path: string): Guide | null {
  const p = path.split('?')[0];
  return GUIDES.find((g) => g.kind === 'module' && g.routes?.includes(p)) || (p.startsWith('/e/') ? GUIDES.find((g) => g.key === 'entity')! : null);
}
export const TASK_GUIDES = () => GUIDES.filter((g) => g.kind === 'task');
