// Guided tours for shop-floor execution & quality compliance (M5) pages: label design & printing, packing station, loading & shipping and ASN, outsourcing, spare parts, controlled documents, ECN, gauges, PFMEA & control plan, MSA, complaints & recalls.
// guides.ts merges EXEC_GUIDES into GUIDES (the "?" next to the page title finds these tours by path). Falls back to the page title when a target isn't found.
import { $t } from '../i18n';
import type { Guide, GuideStep } from './engine';

const T = (n: string) => ({ tour: n });
const S = (sel: string) => ({ sel });
const M = (s: GuideStep): GuideStep => ({ manual: true, ...s });
const HEAD = [T('page-head'), S('.hm-main .page-head'), S('.hm-main h2')];
const D = (t: string) => [S(`[data-test=${t}]`), ...HEAD];

function mod(key: string, title: string, routes: string[], what: string, steps: GuideStep[], kw = ''): Guide {
  return { key, title, kind: 'module', desc: what.replace(/\*\*/g, '').slice(0, 40), routes, kw,
    steps: [M({ id: 'what', title: $t('common.n0WhatItSFor', [title]), do: what, target: HEAD, route: routes[0] }), ...steps.map(M)] };
}

export const EXEC_GUIDES: Guide[] = [
  mod('exec-labels', $t('common.labelDesignPrinting'), ['/exec/labels'], $t('guidesexec.labelTemplatesForPartsLots'), [
    { id: 'list', title: $t('common.template'), target: D('lbl-tpl-list'), do: $t('guidesexec.filterByPurposeDuplicateA') },
    { id: 'canvas', title: $t('guidesexec.canvas'), target: D('lbl-canvas'), do: $t('guidesexec.dragElementsToPositionThem') },
    { id: 'print', title: $t('labelDesigner.print'), target: D('lbl-print'), do: $t('guidesexec.chooseAPrinterOrMarker') },
  ], $t('guidesexec.labelBarcodePrintZplTspl')),
  mod('exec-pack', $t('common.packingStation'), ['/exec/pack'], $t('guidesexec.scanPartCodesToPack'), [
    { id: 'scan', title: $t('common.scan'), target: D('pack-scan'), do: $t('guidesexec.scanDirectlyWithTheScanner') },
    { id: 'carton', title: $t('guidesexec.currentCartonPallet'), target: D('pack-carton'), do: $t('guidesexec.sealsAutomaticallyWhenProgressReache') },
  ], $t('guidesexec.packingPackCartonPalletize')),
  mod('exec-ship', $t('common.loadingShippingAsn'), ['/exec/ship'], $t('guidesexec.createLoadingListsFromDelivery'), [
    { id: 'loads', title: $t('common.loadingLists'), target: D('ship-loads'), do: $t('guidesexec.createFromDeliveryOrderFills') },
    { id: 'asn', title: 'ASN', target: [S('[data-test=ship-asn]'), S('[data-test=ship-loads]'), ...HEAD], do: $t('guidesexec.theAsnGoesToThe') },
  ], $t('guidesexec.shippingShipAsnLoadingDispatch')),
  mod('exec-outsource', $t('common.outsourcing'), ['/exec/outsource'], $t('guidesexec.operationsDoneBySuppliersSuch'), [
    { id: 'kpi', title: $t('outsource.qtyOut'), target: D('os-kpis'), do: $t('guidesexec.quantitiesOutAndOverdueOrders') },
    { id: 'orders', title: $t('common.outsourcingOrders'), target: D('os-table'), do: $t('guidesexec.operationsMarkedOutsourcedInThe') },
  ], $t('guidesexec.outsourcingOutsourceSubcontract')),
  mod('exec-spares', $t('common.spareParts'), ['/exec/spares'], $t('guidesexec.sparePartsRegisterAndStock'), [
    { id: 'table', title: $t('guidesexec.sparePartsStock'), target: D('sp-table'), do: $t('guidesexec.redMeansBelowMinimumStock') },
    { id: 'reorder', title: $t('guidesexec.replenishmentSuggestions'), target: [S('[data-test=sp-reorder]'), S('[data-test=sp-table]'), ...HEAD], do: $t('guidesexec.suggestedQtyTopUpTo') },
  ], $t('guidesexec.sparePartsSpares')),
  mod('exec-docs', $t('common.controlledDocuments'), ['/exec/docs'], $t('guidesexec.versionControlForWorkInstructions'), [
    { id: 'table', title: $t('docControl.documentList'), target: D('doc-table'), do: $t('guidesexec.currentAndPendingVersionsClick') },
    { id: 'effective', title: $t('guidesexec.whatTheShopFloorSees'), target: [S('[data-test=doc-effective]'), S('[data-test=doc-table]'), ...HEAD], do: $t('guidesexec.chooseAProductAndOperation') },
  ], $t('guidesexec.controlledDocumentsDocVersion')),
  mod('exec-ecn', $t('common.engineeringChangeEcn'), ['/exec/ecn'], $t('guidesexec.changeRequestAutomaticImpactAnalysis'), [
    { id: 'table', title: $t('guidesexec.ecnList'), target: D('ecn-table'), do: $t('guidesexec.newEcnDescribeTheChange') },
    { id: 'impact', title: $t('common.impactAnalysis'), target: [S('[data-test=ecn-impact]'), S('[data-test=ecn-table]'), ...HEAD], do: $t('guidesexec.affectedDataIsListedAutomatically') },
  ], $t('guidesexec.ecnEngineeringChange')),
  mod('iatf-gauges', $t('common.gauges'), ['/iatf/gauges'], $t('guidesexec.gaugeRegisterCalibrationScheduleAnd'), [
    { id: 'kpi', title: $t('common.status'), target: D('g-kpis'), do: $t('guidesexec.scheduleGaugesDueSoonWithin') },
    { id: 'table', title: $t('modelTab.register'), target: D('g-table'), do: $t('guidesexec.clickARowToLog') },
  ], $t('guidesexec.gaugeGaugesCalibration')),
  mod('iatf-pfmea', $t('common.pfmeaControlPlan'), ['/iatf/pfmea'], $t('guidesexec.processFmeaSOD'), [
    { id: 'rows', title: $t('pfmea.pfmeaItems'), target: D('fmea-rows'), do: $t('guidesexec.rpnAndApRecalculateAutomatically') },
    { id: 'derive', title: $t('pfmea.generateControlPlan'), target: [S('[data-test=fmea-derive]'), S('[data-test=cp-items]'), ...HEAD], do: $t('guidesexec.itemsWithCharacteristicsBecomeContro') },
  ], $t('guidesexec.pfmeaFmeaControlPlanCp')),
  mod('iatf-msa', $t('common.msaMeasurementSystemAnalysis'), ['/iatf/msa'], $t('guidesexec.grRAverageRangeAnova'), [
    { id: 'list', title: $t('guidesexec.studies'), target: D('msa-list'), do: $t('guidesexec.createAStudyByChoosing') },
    { id: 'result', title: $t('common.conclusion'), target: [S('[data-test=msa-result]'), S('[data-test=msa-list]'), ...HEAD], do: $t('guidesexec.grr10Acceptable1030') },
  ], $t('guidesexec.msaGrrMeasurementSystemAnalysis')),
  mod('iatf-complaints', $t('common.complaintsRecalls'), ['/iatf/complaints'], $t('guidesexec.logACustomerComplaintDelivery'), [
    { id: 'table', title: $t('guidesexec.complaints'), target: D('cmp-table'), do: $t('guidesexec.clickARowToSee') },
    { id: 'recall', title: $t('complaints.recallSimulation'), target: [S('[data-test=recall-code]'), S('[data-test=cmp-table]'), ...HEAD], do: $t('guidesexec.enterAHeatPartCode') },
  ], $t('guidesexec.customerComplaintRecall')),
  mod('iatf-esign', $t('nav.eSignatureRecords'), ['/e/esign'], $t('guidesexec.eSignaturesForFirstArticle'), [], $t('guidesexec.eSignatureEsignSignature')),
];
