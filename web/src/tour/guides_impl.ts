// Guided tours for implementation & configuration (M3) pages: Implementation Wizard, Setup Wizard, data import, industry templates, custom fields, form config, approval workflows, report design.
// guides.ts merges IMPL_GUIDES into GUIDES (the "?" next to the page title finds these tours by path).
import { $t } from '../i18n';
import type { Guide, GuideStep } from './engine';

const T = (n: string) => ({ tour: n });
const S = (sel: string) => ({ sel });
const M = (s: GuideStep): GuideStep => ({ manual: true, ...s });
const HEAD = [T('page-head'), S('.hm-main .page-head'), S('.hm-main h2')];

function mod(key: string, title: string, routes: string[], what: string, steps: GuideStep[], kw = ''): Guide {
  return { key, title, kind: 'module', desc: what.replace(/\*\*/g, '').slice(0, 40), routes, kw,
    steps: [M({ id: 'what', title: $t('common.n0WhatItSFor', [title]), do: what, target: HEAD, route: routes[0] }), ...steps.map(M)] };
}

export const IMPL_GUIDES: Guide[] = [
  mod('impl-home', $t('common.implementationGuide'), ['/impl'], $t('guidesimpl.theTaskListOfAn'), [
    { id: 'progress', title: $t('qualityGates.progress'), target: [S('[data-test=impl-progress]'), ...HEAD], do: $t('guidesimpl.overallProgressAndOverdueItems') },
    { id: 'tasks', title: $t('common.task'), target: [S('[data-test=impl-tasks]'), S('[data-test=impl-init]')], do: $t('guidesimpl.firstTimeClickCreateTasks') },
  ], $t('guidesimpl.implementationWizardGoLiveChecklist')),
  mod('impl-setup', $t('common.setupWizard'), ['/impl/setup'], $t('guidesimpl.aNewInstallationStartsHere'), [
    { id: 'steps', title: $t('guidesimpl.fourSteps'), target: [S('[data-test=setup-steps]'), ...HEAD], do: $t('guidesimpl.youCanComeBackAnd') },
    { id: 'pack', title: $t('guidesimpl.chooseIndustryTemplate'), target: [S('[data-test=pack-preview]'), S('[data-test=setup-steps]')], do: $t('guidesimpl.tickTheIndustriesMultipleAllowed') },
  ], $t('guidesimpl.setupWizardInitializationOnboarding')),
  mod('impl-import', $t('common.dataImport'), ['/impl/import', '/impl/import/history'], $t('guidesimpl.masterAndOpeningDataBefore'), [
    { id: 'cards', title: $t('guidesimpl.chooseADataType'), target: [S('[data-test^=imp-card-]'), S('[data-test=imp-tabs]')], do: $t('guidesimpl.alreadyOrderedForSetupUnits') },
    { id: 'wb', title: $t('guidesimpl.wholeWorkbook'), target: [S('[data-test=imp-workbook]'), ...HEAD], do: $t('guidesimpl.theInitializationWorkbookContainsEve') },
    { id: 'hist', title: $t('common.importHistory'), target: [S('[data-test=imp-tabs]')], do: $t('guidesimpl.everyImportSFileResult') },
  ], $t('guidesimpl.dataImportExcelOpeningBalances')),
  mod('impl-packs', $t('common.industryTemplates'), ['/impl/packs'], $t('guidesimpl.industryTemplatesForFoundryMachining'), [
    { id: 'cards', title: $t('common.template'), target: [S('[data-test^=pack-]'), ...HEAD], do: $t('guidesimpl.detailsShowsWhatEachTemplate') },
  ], $t('guidesimpl.industryTemplatesInjectionMoldingAss')),
  mod('impl-fields', $t('common.customFields'), ['/impl/fields'], $t('guidesimpl.addFieldsToAnyEntity'), [
    { id: 'entity', title: $t('common.selectEntity'), target: [S('[data-test=cf-entity]'), ...HEAD], do: $t('guidesimpl.firstChooseTheEntityTo') },
    { id: 'table', title: $t('common.field'), target: [S('[data-test=cf-table]')], do: $t('guidesimpl.filterableCreatesADatabaseIndex') },
  ], $t('guidesimpl.customFieldsExtension')),
  mod('impl-forms', $t('common.formConfiguration'), ['/impl/forms'], $t('guidesimpl.createEditFormsOnGeneric'), [
    { id: 'groups', title: $t('common.group'), target: [S('[data-test=form-add-group]'), S('[data-test=form-entity]')], do: $t('guidesimpl.addGroupsAndPickFields') },
    { id: 'roles', title: $t('common.byRole'), target: [S('[data-test=form-roles]')], do: $t('guidesimpl.forEachFieldChooseWhich') },
  ], $t('guidesimpl.formConfigRequiredHiddenLayout')),
  mod('impl-flows', $t('common.approvalFlows'), ['/impl/flows'], $t('guidesimpl.addApprovalToAnyAction'), [
    { id: 'list', title: $t('common.flow'), target: [S('[data-test=flow-table]'), S('[data-test=flow-new]')], do: $t('guidesimpl.newWorkflowChooseWhichAction') },
  ], $t('guidesimpl.approvalWorkflow')),
  mod('impl-reports', $t('common.reportDesigner'), ['/impl/reports'], $t('guidesimpl.buildYourOwnReportsChoose'), [
    { id: 'des', title: $t('guidesimpl.design'), target: [S('[data-test=rp-designer]'), S('[data-test=rp-new]'), ...HEAD], do: $t('guidesimpl.chooseADatasetAddGrouping') },
    { id: 'save', title: $t('guidesimpl.saveShareSchedulePinTo'), target: [S('[data-test=rp-save]'), S('[data-test=rp-pin]')], do: $t('guidesimpl.whenSavingYouCanShare') },
  ], $t('guidesimpl.reportDesignerExportScheduled')),
];
