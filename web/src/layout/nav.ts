// Left menu: groups → pages. Every existing route can be found here (the Ctrl+K command palette uses it too).
// feature: required AI feature (when off, the menu item is greyed and the page shows "Disabled"). kw: search keywords (pinyin initials, English, synonyms).
// perm: required permission (default inferred from path: /e/<entity> → entity read, otherwise page.<path>, see perm.ts). Menu items without permission are hidden.
import { $t } from '../i18n';
import { can, navPerm } from '../perm';
import { moduleOn } from '../impl/state';          // M3: menus for optional industry-template modules (Foundry / Machining…) are hidden when not enabled

export interface NavItem { path: string; label: string; feature?: string; kw?: string; perm?: string; module?: string }
/** Menus for optional modules (industry template modules): hidden when the module isn't enabled (M3, docs/dev/config.md "Industry Templates") */
export const MODULE_OF: Record<string, string> = { '/foundry/heats': 'foundry', '/foundry/charge': 'foundry', '/e/heat': 'foundry', '/e/treatment_rec': 'foundry',
  '/cross/coplan': 'foundry', '/machining/tools': 'machining', '/e/nc_program': 'machining', '/machining/defects': 'machining' };
export interface NavGroup { key: string; label: string; icon: string; items: NavItem[] }

export const NAV: NavGroup[] = [
  // ISA-95 / MOM structure: Home, then the four operations domains (production, quality, maintenance, inventory),
  // the built-in ERP (planning & business), the factory model & connectivity, intelligence and system administration.
  { key: 'home', label: $t('common.home'), icon: 'Odometer', items: [
    { path: '/', label: $t('common.home'), kw: $t('nav.homeDashboardCockpitBoardAlerts') },
    { path: '/screen', label: $t('nav.shopFloorDisplay'), kw: $t('nav.screenDisplayBigScreen') },
    { path: '/m', label: $t('nav.mobileScanToReport'), kw: $t('nav.mobileScanReport') },
  ] },
  { key: 'prod', label: $t('nav.grpProduction'), icon: 'Operation', items: [
    { path: '/e/work_order', label: $t('common.workOrders'), kw: $t('nav.woWorkOrder') },
    { path: '/production/gantt', label: $t('common.schedulingProgress'), kw: $t('nav.ganttScheduleSchedulingProgress') },
    { path: '/e/schedule_proposal', label: $t('common.scheduleChangeProposals'), kw: 'pctz schedule' },
    { path: '/cross/coplan', label: $t('nav.castingMachiningCoScheduling'), kw: $t('nav.coplanCoSchedulingFoundryMachining') },
    { path: '/station', label: $t('common.workstationTerminal'), kw: $t('nav.stationTerminalWorkstationClockIn') },
    { path: '/e/report', label: $t('common.productionReports'), kw: $t('nav.reportProductionReporting') },
    { path: '/labor/daily', label: $t('nav.operatorDailyReports'), kw: $t('nav.dailyReportReviewTeamLeader') },
    { path: '/labor/hours', label: $t('nav.laborHours'), kw: $t('nav.hoursLaborPeople') },
    { path: '/e/attendance', label: $t('nav.clockInRecords'), kw: $t('nav.attendanceClockInClockOut') },
    { path: '/cycle', label: $t('common.cycleTimeAnalysis'), perm: 'cycle.view', kw: $t('nav.cycleTimeTaktMotionCylinder') },
    { path: '/andon', label: $t('common.andonBoard'), kw: $t('nav.andon') },
    { path: '/e/andon', label: $t('nav.andonRecords'), kw: 'ad andon' },
    { path: '/trace', label: $t('common.traceability'), kw: $t('nav.traceTraceabilitySerialGenealogy') },
    { path: '/trace/recon', label: $t('common.traceabilityReconciliation'), kw: $t('nav.reconciliationHeatLadleQuantity') },
    { path: '/e/part', label: $t('nav.wipSerializedParts'), kw: $t('nav.wipPartSerialSn') },
    { path: '/foundry/heats', label: $t('common.heatAnalysis'), kw: $t('nav.heatMeltingFoundryCasting') },
    { path: '/foundry/charge', label: $t('nav.chargeOptimization'), feature: 'charge_opt', kw: $t('nav.chargeMixOptimization') },
    { path: '/e/heat', label: $t('nav.heatRecords'), kw: 'lc heat' },
    { path: '/e/treatment_rec', label: $t('nav.nodularizationRecords'), kw: $t('nav.nodularizationTreatment') },
    { path: '/ai/daily', label: $t('common.productionDailyReport'), kw: $t('nav.dailyReportPdf') },
  ] },
  { key: 'quality', label: $t('nav.grpQuality'), icon: 'CircleCheck', items: [
    { path: '/quality', label: $t('common.qualityOverview'), kw: 'zl quality' },
    { path: '/e/insp_record', label: $t('nav.inspectionRecords'), kw: $t('nav.inspectionIqcIpqcOqc') },
    { path: '/e/ncr', label: $t('common.nonconformances'), kw: $t('nav.ncrNonconformanceDefect') },
    { path: '/quality/fai', label: $t('common.firstArticleRelease'), kw: $t('nav.faiFirstArticleRelease') },
    { path: '/quality/hold', label: $t('common.lotOnHold'), kw: $t('nav.holdLotHoldQuarantine') },
    { path: '/quality/rework', label: $t('common.reworkReInspection'), kw: $t('nav.reworkReinspection') },
    { path: '/quality/spc', label: $t('common.spc'), kw: $t('nav.spcCpkControlChart') },
    { path: '/e/report_8d', label: $t('common.n8dReports'), kw: '8d' },
    { path: '/cross/backtrace', label: $t('nav.defectBacktrace'), kw: $t('nav.backtraceDefectRootCause') },
    { path: '/machining/defects', label: $t('common.machiningDefectStats'), kw: 'jjbl defects' },
    { path: '/iatf/pfmea', label: $t('common.pfmeaControlPlan'), kw: $t('nav.pfmeaFmeaControlPlanSpecial') },
    { path: '/iatf/msa', label: $t('common.msaMeasurementSystemAnalysis'), kw: $t('nav.msaGrrGageRR') },
    { path: '/iatf/gauges', label: $t('common.gauges'), kw: $t('nav.gaugeCalibrationMetrologyMsa') },
    { path: '/iatf/complaints', label: $t('common.complaintsRecalls'), kw: $t('nav.complaintCustomerComplaintRecall') },
    { path: '/exec/docs', label: $t('common.controlledDocuments'), kw: $t('nav.documentControlRevisionWorkInstructi') },
    { path: '/exec/ecn', label: $t('common.engineeringChangeEcn'), kw: $t('nav.ecnEngineeringChange') },
    { path: '/e/insp_template', label: $t('common.inspectionTemplates'), kw: 'jymb template' },
    { path: '/e/defect_code', label: $t('nav.defectCodes'), kw: 'qxdm defect' },
    { path: '/e/esign', label: $t('nav.eSignatureRecords'), kw: $t('nav.esignESignatureSignature') },
  ] },
  { key: 'maint', label: $t('nav.grpMaintenance'), icon: 'Monitor', items: [
    { path: '/e/equipment', label: $t('common.equipmentRegister'), kw: $t('nav.equipmentRegisterAssetLedger') },
    { path: '/equipment/monitor', label: $t('common.liveMonitor'), kw: $t('nav.monitorEquipmentStatusLive') },
    { path: '/equipment/oee', label: $t('common.oee'), kw: $t('nav.oeeUtilization') },
    { path: '/equipment/maint', label: $t('common.checksPm'), kw: $t('nav.maintenancePmPreventiveInspectionChe') },
    { path: '/e/repair_order', label: $t('common.repairOrders'), kw: $t('nav.repairMaintenanceBreakdownRequest') },
    { path: '/e/alarm', label: $t('nav.alarmRecords'), kw: $t('nav.alarmAlarms') },
    { path: '/e/downtime_plan', label: $t('nav.plannedDowntime'), kw: $t('nav.downtimePlannedMaintenanceWindow') },
    { path: '/exec/spares', label: $t('common.spareParts'), kw: $t('nav.sparePartsReplenishment') },
    { path: '/e/tooling', label: $t('common.moldsFixtures'), kw: $t('nav.toolingMoldFixtureShotCount') },
    { path: '/machining/tools', label: $t('common.toolLifeMonitoring'), kw: $t('nav.toolCuttingToolWearLife') },
    { path: '/e/nc_program', label: $t('common.ncPrograms'), kw: $t('nav.ncCncProgram') },
    { path: '/energy', label: $t('common.energyAnalysis'), kw: $t('nav.energyPowerConsumption') },
  ] },
  { key: 'inventory', label: $t('nav.grpInventory'), icon: 'Box', items: [
    { path: '/warehouse', label: $t('common.stock'), kw: $t('nav.stockInventory') },
    { path: '/e/stock_txn', label: $t('nav.inventoryTransactions'), kw: 'crk stock txn' },
    { path: '/e/warehouse', label: $t('nav.warehousesLocations'), kw: 'ck kw warehouse' },
    { path: '/e/material_issue', label: $t('nav.materialIssueReturn'), kw: $t('nav.issueMaterialIssueReturn') },
    { path: '/e/material_request', label: $t('nav.materialRequests'), kw: 'wlsq request' },
    { path: '/e/wo_kitting', label: $t('nav.materialKitting'), kw: $t('nav.kittingShortage') },
    { path: '/e/agv_task', label: $t('nav.agvTasks'), kw: 'agv' },
    { path: '/exec/labels', label: $t('common.labelDesignPrinting'), kw: $t('nav.labelBarcodePrintZplZebra') },
    { path: '/exec/pack', label: $t('common.packingStation'), kw: $t('nav.packPackingCartonPalletizeScan') },
    { path: '/exec/ship', label: $t('common.loadingShippingAsn'), kw: $t('nav.shipShippingAsnLoadingDispatch') },
    { path: '/e/shipment', label: $t('nav.shipmentRecords'), kw: $t('nav.shipmentShippingDelivery') },
    { path: '/exec/outsource', label: $t('common.outsourcing'), kw: $t('nav.outsourceSubcontractHeatTreatment') },
  ] },
  { key: 'business', label: $t('nav.grpPlanning'), icon: 'Sell', items: [
    { path: '/sales/board', label: $t('common.orderBoard'), kw: $t('nav.salesBoardOrdersOnTime'), perm: 'sales.sales_order.read' },
    { path: '/sales/orders', label: $t('erpDocs.salesOrder'), kw: $t('nav.soSalesOrderDeliverySchedule'), perm: 'sales.sales_order.read' },
    { path: '/sales/quotes', label: $t('erpDocs.quotation'), kw: $t('nav.quoteQuotation'), perm: 'sales.quotation.read' },
    { path: '/sales/deliveries', label: $t('common.deliveryOrder'), kw: $t('nav.deliveryShippingOutbound'), perm: 'sales.delivery_order.read' },
    { path: '/sales/returns', label: $t('common.salesReturn'), kw: $t('nav.rmaReturnSalesReturnCustomer'), perm: 'sales.sales_return.read' },
    { path: '/e/customer', label: $t('common.customer'), kw: $t('nav.customerCreditLimitPaymentTerms') },
    { path: '/e/price_list', label: $t('nav.priceLists'), kw: $t('nav.priceListUnitPrice') },
    { path: '/e/customer_part', label: $t('nav.customerPartNumbers'), kw: $t('nav.customerPartNumber') },
    { path: '/purchase/board', label: $t('common.purchasingBoard'), kw: $t('nav.purchaseBoardArrivalsOverdueSupplier'), perm: 'purchase.purchase_order.read' },
    { path: '/e/purchase_req', label: $t('nav.purchaseRequisitions'), kw: $t('nav.requisitionPurchaseRequest') },
    { path: '/purchase/orders', label: $t('common.purchaseOrder'), kw: $t('nav.poPurchaseOrder'), perm: 'purchase.purchase_order.read' },
    { path: '/purchase/receipts', label: $t('erpDocs.goodsReceipt'), kw: $t('nav.receiptGoodsReceiptIncomingIqc'), perm: 'purchase.purchase_receipt.read' },
    { path: '/purchase/returns', label: $t('common.returnToSupplier'), kw: $t('nav.returnToSupplier'), perm: 'purchase.supplier_return.read' },
    { path: '/e/supplier', label: $t('common.suppliers'), kw: $t('nav.supplierVendor') },
    { path: '/e/supplier_material', label: $t('nav.approvedSupplierList'), kw: $t('nav.aslApprovedSupplierPriceSourcing') },
    { path: '/mrp', label: $t('common.mrp'), kw: $t('nav.mrpMaterialRequirementsPlanningNetti'), perm: 'mrp.mrp_suggestion.read' },
    { path: '/e/forecast', label: $t('nav.demandForecast'), kw: $t('nav.forecastDemand') },
    { path: '/e/mat_plan', label: $t('nav.materialPlanningParameters'), kw: $t('nav.leadTimeLotSizeSafety') },
    { path: '/finance/cost', label: $t('common.costing'), kw: $t('nav.costCostingVarianceMovingAverage'), perm: 'cost.wo_cost.read' },
    { path: '/finance/arap', label: $t('common.arAp'), kw: $t('nav.arApReceivablesPayablesAging'), perm: 'finance.ar_invoice.read' },
    { path: '/finance/ar_invoices', label: $t('common.salesInvoice'), kw: $t('nav.invoiceSalesInvoiceBilling'), perm: 'finance.ar_invoice.read' },
    { path: '/finance/ap_invoices', label: $t('common.purchaseInvoice'), kw: $t('nav.invoiceSupplierInvoiceAp'), perm: 'finance.ap_invoice.read' },
    { path: '/finance/vouchers', label: $t('common.voucherExport'), kw: $t('nav.voucherJournalExportKingdeeYonyou'), perm: 'finance.voucher.read' },
    { path: '/e/cost_rate', label: $t('nav.costRates'), kw: $t('nav.costRateLaborMachineElectricity'), perm: 'cost.cost_rate.read' },
    { path: '/e/acct_map', label: $t('nav.accountMapping'), kw: $t('nav.accountGlMapping'), perm: 'finance.acct_map.read' },
  ] },
  { key: 'model', label: $t('nav.grpModel'), icon: 'Cpu', items: [
    { path: '/e/plant', label: $t('common.plant'), kw: 'gc plant' },
    { path: '/e/workshop', label: $t('common.workshop'), kw: 'cj workshop' },
    { path: '/e/line', label: $t('common.line'), kw: 'cx line' },
    { path: '/e/workstation', label: $t('common.workstation'), kw: 'gw workstation' },
    { path: '/e/route', label: $t('nav.routings'), kw: 'gylx route' },
    { path: '/e/process', label: $t('common.operation'), kw: 'gx process' },
    { path: '/e/material', label: $t('common.material'), kw: $t('nav.materialItemMasterData') },
    { path: '/e/bom', label: 'BOM', kw: $t('nav.bomBillOfMaterials') },
    { path: '/e/changeover', label: $t('nav.changeoverTimes'), kw: $t('nav.changeoverSetup') },
    { path: '/e/employee', label: $t('common.employees'), kw: $t('nav.employeePeopleStaff') },
    { path: '/e/team', label: $t('common.teams'), kw: 'bz team' },
    { path: '/e/shift', label: $t('common.shift'), kw: 'bc shift' },
    { path: '/e/skill', label: $t('nav.skillsMatrix'), kw: $t('nav.skillQualificationCertification') },
    { path: '/onboard', label: $t('nav.overviewImport'), kw: $t('nav.onboardConnectivityDeliveryPackageTa') },
    { path: '/onboard/check', label: $t('common.tagCheck'), kw: $t('nav.checkVerifyCoverageConfirmUnknown') },
    { path: '/onboard/discover', label: $t('common.discoverBrowse'), kw: $t('nav.discoverOpcuaBrowseMqttSparkplug') },
    { path: '/onboard/model', label: $t('common.plantModel'), kw: $t('nav.modelTemplateVersionHierarchyEquipme') },
    { path: '/onboard/twin', label: $t('common.floorPlanDigitalTwin'), kw: $t('nav.twinDxfCad3dFloor') },
    { path: '/onboard/ai', label: $t('nav.aiAssist'), kw: $t('nav.aiPlcCompleteDraftSuspicious') },
    { path: '/e/datasource', label: $t('common.dataSourcesTags'), kw: $t('nav.datasourceMqttOpcModbusCollection') },
  ] },
  { key: 'ai', label: $t('nav.grpIntelligence'), icon: 'MagicStick', items: [
    { path: '/ai/chat', label: $t('nav.askDataFullPage'), feature: 'ask', kw: $t('nav.chatAskDataAi') },
    { path: '/ai/watch', label: $t('common.aiWatch'), feature: 'watch', kw: $t('nav.aiWatchMonitoringAnomalyFindings') },
    { path: '/ai/automations', label: $t('nav.automations'), feature: 'automation', kw: $t('nav.automationRulesAlerts') },
    { path: '/ai/skills', label: $t('common.aiSkills'), feature: 'skills', perm: 'skill.use', kw: $t('nav.skillSkillsScheduledSaved') },
    { path: '/agents', label: $t('common.agents'), feature: 'agents', kw: $t('nav.agentAgentsWorkflowOrchestration') },
    { path: '/approvals', label: $t('common.approvalCenter'), kw: $t('nav.approvalApprovalsWorkflow') },
    { path: '/e/kb_doc', label: $t('common.knowledgeBase'), kw: $t('nav.kbKnowledgeBaseSop') },
    { path: '/e/ai_report', label: $t('nav.aiReports'), kw: 'bg ai report' },
    { path: '/system/mcp', label: $t('common.mcpAccess'), perm: 'system.apikey.manage', kw: $t('nav.mcpClaudeCursorDifyExternal') },
    { path: '/system/ai', label: $t('common.aiFeatures'), kw: $t('nav.aiFeaturesSwitchesSettings') },
  ] },
  { key: 'system', label: $t('nav.grpSystem'), icon: 'Setting', items: [
    { path: '/e/sys_user', label: $t('common.users'), kw: $t('nav.userAccount') },
    { path: '/system/roles', label: $t('nav.rolesPermissions'), perm: 'system.role.manage', kw: $t('nav.rolePermissionDataScope') },
    { path: '/system/audit', label: $t('nav.auditTrail'), perm: 'system.audit.view', kw: $t('nav.auditLogTrailHistory') },
    { path: '/system/apikeys', label: $t('common.apiKeys'), perm: 'system.apikey.manage', kw: $t('nav.apiKeyTokenIntegration') },
    { path: '/system/erp', label: $t('common.erpIntegration'), perm: 'erp.monitor.view', kw: $t('nav.erpIntegrationKingdeeYonyouWebhook') },
    { path: '/system/backups', label: $t('nav.backupRestore'), perm: 'system.backup.view', kw: $t('nav.backupRestore2') },
    { path: '/system', label: $t('common.systemStatus'), kw: $t('nav.systemStatusDatabaseMigration') },
    { path: '/impl', label: $t('common.implementationGuide'), perm: 'impl.impl_task.read', kw: $t('nav.implementationGoLiveChecklistProgres') },
    { path: '/impl/setup', label: $t('common.setupWizard'), perm: 'impl.setup.run', kw: $t('nav.setupInitializeWizard') },
    { path: '/impl/import', label: $t('common.dataImport'), perm: 'impl.import.run', kw: $t('nav.importExcelTemplateMasterData') },
    { path: '/impl/packs', label: $t('common.industryTemplates'), perm: 'impl.pack.view', kw: $t('nav.packIndustryTemplateInjectionMolding') },
    { path: '/impl/fields', label: $t('common.customFields'), perm: 'config.field.manage', kw: $t('nav.customFieldExtension') },
    { path: '/impl/forms', label: $t('common.formConfiguration'), perm: 'config.form.manage', kw: $t('nav.formRequiredHiddenGroupLayout') },
    { path: '/impl/flows', label: $t('common.approvalFlows'), perm: 'config.flow.manage', kw: $t('nav.approvalFlowWorkflowCondition') },
    { path: '/impl/reports', label: $t('common.reportDesigner'), kw: $t('nav.reportDesignerExportScheduledCustom') },
  ] },
];

export const ALL_NAV = NAV.flatMap((g) => g.items.map((it) => ({ ...it, group: g.label, groupKey: g.key })));
/** Menus visible to the current user (filtered by permission; empty groups hidden). */
export function visibleNav(): NavGroup[] {
  return NAV.map((g) => ({ ...g, items: g.items.filter((it) => can(navPerm(it)) && moduleOn(it.module || MODULE_OF[it.path])) })).filter((g) => g.items.length);
}
export const navAllowed = (it: NavItem) => can(navPerm(it));
/** Route → menu item (with group). Entity pages with query params are matched too. */
export function findNav(path: string) {
  const p = path.split('?')[0];
  return ALL_NAV.find((x) => x.path === p) || null;
}
/** Legacy compatibility: [{ group, icon, items: [[path, label]] }] */
export const MENU = NAV.map((g) => ({ group: g.label, icon: g.icon, items: g.items.map((i) => [i.path, i.label] as [string, string]) }));
