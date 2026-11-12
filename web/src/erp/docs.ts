// Config for the built-in ERP document page (pages/ErpDocs.vue): list columns, header form, line columns and status-dependent actions per document type.
// Backend API: /api/erp/doc/<type> (list / detail / create / edit draft), status actions /api/e/<type>/<id>/do/<action> (docs/dev/erp.md).
import { $t } from '../i18n';
import { session } from '../api';

export interface LineCol { key: string; label?: string; w?: number; edit?: boolean; num?: boolean; digits?: number }
export interface DocAction { act: string; label: string; type?: string; when: (r: any) => boolean; confirm?: string; prompt?: string; custom?: boolean; perm?: string }
export interface DocCfg {
  kind: string; title: string; sub: string; partner: 'customer' | 'supplier'; date: string; newLabel: string;
  head: string[]; list: string[]; lines: LineCol[]; actions: DocAction[]; editable: string[]; addLines?: boolean;
  tour: string;
}

const S = (...xs: string[]) => (r: any) => xs.includes(r.status);

export const DOCS: Record<string, DocCfg> = {
  quotation: {
    kind: 'quotation', title: $t('erpDocs.quotation'), sub: $t('erpDocs.quotationOnceTheCustomerAccepts'), partner: 'customer', date: 'quote_date', newLabel: $t('erpDocs.newQuotation'),
    head: ['customer', 'quote_date', 'valid_until', 'salesman', 'remark'], list: ['valid_until', 'salesman'], editable: ['draft'], addLines: true, tour: 'erp-quotes',
    lines: [{ key: 'material', w: 220, edit: true }, { key: 'customer_part_no', w: 140, edit: true }, { key: 'qty', w: 110, edit: true, num: true },
      { key: 'price', w: 110, edit: true, num: true, digits: 2 }, { key: 'tax_rate', w: 80, edit: true, num: true }, { key: 'lead_days', w: 90, edit: true, num: true },
      { key: 'amount', w: 120, num: true, digits: 2 }],
    actions: [{ act: 'send', label: $t('erpDocs.sendQuotation'), when: S('draft') }, { act: 'accept', label: $t('erpDocs.convertToOrder'), type: 'primary', when: S('draft', 'sent'), confirm: $t('erpDocs.createASalesOrderFrom') },
      { act: 'lose', label: $t('erpDocs.lost'), when: S('draft', 'sent'), prompt: $t('erpDocs.lossReason') }],
  },
  sales_order: {
    kind: 'sales_order', title: $t('erpDocs.salesOrder'), sub: $t('erpDocs.orderDeliveryScheduleCreditLimit'), partner: 'customer', date: 'order_date',
    newLabel: $t('erpDocs.newOrder'), head: ['customer', 'customer_po', 'order_date', 'due', 'plant', 'mto', 'salesman', 'ship_to', 'remark'], list: ['customer_po', 'due', 'version'],
    editable: ['draft', 'credit_hold'], addLines: true, tour: 'erp-so',
    lines: [{ key: 'material', w: 220, edit: true }, { key: 'customer_part_no', w: 130, edit: true }, { key: 'qty', w: 100, edit: true, num: true },
      { key: 'price', w: 100, edit: true, num: true, digits: 2 }, { key: 'tax_rate', w: 76, edit: true, num: true }, { key: 'due', w: 140, edit: true },
      { key: 'amount', w: 116, num: true, digits: 2 }, { key: 'qty_shipped', w: 80, num: true }, { key: 'work_order', w: 140 }],
    actions: [{ act: 'confirm', label: $t('common.confirm'), type: 'primary', when: S('draft') }, { act: 'approve_credit', label: $t('erpDocs.creditOverride'), type: 'warning', when: S('credit_hold'), prompt: $t('erpDocs.overrideReason') },
      { act: 'delivery', label: $t('erpDocs.createDeliveryOrder'), type: 'primary', when: S('confirmed', 'partial'), custom: true, perm: 'sales.delivery_order.create' },
      { act: 'change', label: $t('erpDocs.changeOrder'), when: S('confirmed', 'partial', 'credit_hold'), custom: true, perm: 'sales.sales_order.update' },
      { act: 'make_wo', label: $t('erpDocs.createWorkOrderMakeTo'), when: (r) => ['confirmed', 'partial'].includes(r.status) },
      { act: 'close', label: $t('common.close'), when: S('confirmed', 'partial'), confirm: $t('erpDocs.unshippedQuantitiesWillNoLonger') },
      { act: 'cancel', label: $t('common.cancel'), when: S('draft', 'credit_hold', 'confirmed'), prompt: $t('erpDocs.cancellationReason') }],
  },
  delivery_order: {
    kind: 'delivery_order', title: $t('common.deliveryOrder'), sub: $t('erpDocs.onPostingChecksOrderBalance'), partner: 'customer', date: 'ship_date',
    newLabel: $t('erpDocs.newDeliveryOrder'), head: ['customer', 'sales_order', 'ship_date', 'warehouse', 'ship_to', 'carrier', 'tracking', 'remark'], list: ['sales_order', 'warehouse', 'invoiced'],
    editable: ['draft'], addLines: true, tour: 'erp-delivery',
    lines: [{ key: 'material', w: 220, edit: true }, { key: 'so_line', w: 90 }, { key: 'qty', w: 110, edit: true, num: true }, { key: 'lot', w: 130, edit: true },
      { key: 'price', w: 100, num: true, digits: 2 }, { key: 'amount', w: 120, num: true, digits: 2 }, { key: 'unit_cost', w: 100, num: true, digits: 2 }, { key: 'work_order', w: 140 }],
    actions: [{ act: 'post', label: $t('erpDocs.postShipment'), type: 'primary', when: S('draft'), confirm: $t('erpDocs.postingDeductsInventoryAndUpdates') }, { act: 'cancel', label: $t('common.cancel'), when: S('draft') },
      { act: 'return', label: $t('erpDocs.customerReturn'), when: S('posted'), custom: true, perm: 'sales.sales_return.create' }],
  },
  sales_return: {
    kind: 'sales_return', title: $t('common.salesReturn'), sub: $t('erpDocs.customerReturnRmaReturnsTo'), partner: 'customer', date: 'return_date',
    newLabel: $t('erpDocs.newReturn'), head: ['customer', 'delivery_order', 'return_date', 'warehouse', 'disposition', 'reason', 'complaint_code', 'remark'], list: ['delivery_order', 'disposition', 'reason'],
    editable: ['draft'], addLines: true, tour: 'erp-return',
    lines: [{ key: 'material', w: 220, edit: true }, { key: 'delivery_line', w: 90 }, { key: 'qty', w: 110, edit: true, num: true }, { key: 'lot', w: 130, edit: true },
      { key: 'price', w: 100, num: true, digits: 2 }, { key: 'amount', w: 120, num: true, digits: 2 }],
    actions: [{ act: 'receive', label: $t('erpDocs.receiveReturn'), type: 'primary', when: S('draft'), confirm: $t('erpDocs.confirmTheReturnHasBeen') }, { act: 'cancel', label: $t('common.cancel'), when: S('draft') }],
  },
  purchase_order: {
    kind: 'purchase_order', title: $t('common.purchaseOrder'), sub: $t('erpDocs.ordersCanOnlyBePlaced'), partner: 'supplier', date: 'order_date',
    newLabel: $t('erpDocs.newPurchaseOrder'), head: ['supplier', 'order_date', 'due', 'plant', 'buyer', 'remark'], list: ['due', 'buyer', 'source'], editable: ['draft'], addLines: true, tour: 'erp-po',
    lines: [{ key: 'material', w: 220, edit: true }, { key: 'qty', w: 100, edit: true, num: true }, { key: 'price', w: 100, edit: true, num: true, digits: 2 },
      { key: 'tax_rate', w: 76, edit: true, num: true }, { key: 'due', w: 140, edit: true }, { key: 'amount', w: 116, num: true, digits: 2 },
      { key: 'qty_received', w: 76, num: true }, { key: 'qty_accepted', w: 86, num: true }, { key: 'qty_rejected', w: 76, num: true }],
    actions: [{ act: 'approve', label: $t('common.approve2'), type: 'primary', when: S('draft') }, { act: 'send', label: $t('erpDocs.sendToSupplier'), type: 'primary', when: S('approved') },
      { act: 'receipt', label: $t('common.receive'), type: 'primary', when: S('approved', 'sent', 'partial'), custom: true, perm: 'purchase.purchase_receipt.create' },
      { act: 'close', label: $t('common.close'), when: S('approved', 'sent', 'partial'), confirm: $t('erpDocs.outstandingQuantitiesWillNoLonger') }, { act: 'cancel', label: $t('common.cancel'), when: S('draft', 'approved') }],
  },
  purchase_receipt: {
    kind: 'purchase_receipt', title: $t('erpDocs.goodsReceipt'), sub: $t('erpDocs.receiveForInspectionEachLine'),
    partner: 'supplier', date: 'receive_date', newLabel: $t('erpDocs.newReceipt'), head: ['supplier', 'purchase_order', 'receive_date', 'warehouse', 'delivery_note', 'remark'],
    list: ['purchase_order', 'delivery_note', 'invoiced'], editable: ['draft'], addLines: true, tour: 'erp-receipt',
    lines: [{ key: 'material', w: 200, edit: true }, { key: 'po_line', w: 80 }, { key: 'qty', w: 100, edit: true, num: true }, { key: 'lot', w: 130, edit: true },
      { key: 'status', w: 90 }, { key: 'insp_record', w: 140 }, { key: 'qty_accepted', w: 90, num: true }, { key: 'qty_rejected', w: 80, num: true },
      { key: 'price', w: 90, num: true, digits: 2 }, { key: 'amount', w: 110, num: true, digits: 2 }],
    actions: [{ act: 'post', label: $t('erpDocs.receiveForInspection'), type: 'primary', when: S('draft'), confirm: $t('erpDocs.receiveAndOpenIncomingInspections') },
      { act: 'settle', label: $t('erpDocs.putAwayByInspectionResult'), type: 'primary', when: S('inspecting'), custom: true }, { act: 'cancel', label: $t('common.cancel'), when: S('draft') }],
  },
  supplier_return: {
    kind: 'supplier_return', title: $t('common.returnToSupplier'), sub: $t('erpDocs.incomingInspectionRejectsNotYet'), partner: 'supplier', date: 'return_date', newLabel: $t('erpDocs.newSupplierReturn'),
    head: ['supplier', 'purchase_receipt', 'return_date', 'kind', 'warehouse', 'reason', 'remark'], list: ['purchase_receipt', 'kind', 'reason'], editable: ['draft'], addLines: true,
    tour: 'erp-sret',
    lines: [{ key: 'material', w: 220, edit: true }, { key: 'qty', w: 110, edit: true, num: true }, { key: 'lot', w: 130, edit: true },
      { key: 'price', w: 100, edit: true, num: true, digits: 2 }, { key: 'amount', w: 120, num: true, digits: 2 }],
    actions: [{ act: 'ship', label: $t('erpDocs.shipReturn'), type: 'primary', when: S('draft'), confirm: $t('erpDocs.confirmTheReturnShipment') }, { act: 'cancel', label: $t('common.cancel'), when: S('draft') }],
  },
  ar_invoice: {
    kind: 'ar_invoice', title: $t('common.salesInvoice'), sub: $t('erpDocs.invoiceRegistrationAgainstDeliveryOr'), partner: 'customer', date: 'invoice_date', newLabel: $t('erpDocs.registerInvoice'),
    head: ['customer', 'invoice_no', 'itype', 'invoice_date', 'due_date', 'remark'], list: ['invoice_no', 'due_date', 'paid'], editable: ['open'], addLines: true, tour: 'erp-arinv',
    lines: [{ key: 'delivery_order', w: 200, edit: true }, { key: 'sales_return', w: 160, edit: true }, { key: 'amount', w: 120, edit: true, num: true, digits: 2 },
      { key: 'tax', w: 110, edit: true, num: true, digits: 2 }, { key: 'total', w: 120, num: true, digits: 2 }],
    actions: [{ act: 'void', label: $t('common.void'), when: S('open'), confirm: $t('erpDocs.voidThisInvoice') }],
  },
  ap_invoice: {
    kind: 'ap_invoice', title: $t('common.purchaseInvoice'), sub: $t('erpDocs.invoiceReceiptRegistrationAgainstGoo'), partner: 'supplier', date: 'invoice_date', newLabel: $t('erpDocs.registerInvoice'),
    head: ['supplier', 'invoice_no', 'itype', 'invoice_date', 'due_date', 'remark'], list: ['invoice_no', 'due_date', 'paid'], editable: ['open'], addLines: true, tour: 'erp-apinv',
    lines: [{ key: 'purchase_receipt', w: 200, edit: true }, { key: 'supplier_return', w: 160, edit: true }, { key: 'amount', w: 120, edit: true, num: true, digits: 2 },
      { key: 'tax', w: 110, edit: true, num: true, digits: 2 }, { key: 'total', w: 120, num: true, digits: 2 }],
    actions: [{ act: 'void', label: $t('common.void'), when: S('open'), confirm: $t('erpDocs.voidThisInvoice') }],
  },
};

/** Entity schema (/api/meta) */
export function spec(entity: string) { return session.meta?.entities?.find((e: any) => e.name === entity); }
export function field(entity: string, name: string) { return spec(entity)?.fields?.find((f: any) => f.name === name); }
export function lineEntity(kind: string) {
  return ({ quotation: 'quotation_line', sales_order: 'so_line', delivery_order: 'delivery_line', sales_return: 'return_line', purchase_order: 'po_line',
    purchase_receipt: 'receipt_line', supplier_return: 'supplier_return_line', ar_invoice: 'ar_invoice_line', ap_invoice: 'ap_invoice_line' } as any)[kind];
}
export function statusTone(s: string): 'ok' | 'warn' | 'bad' | 'neutral' | 'primary' {
  if (['confirmed', 'approved', 'posted', 'received', 'done', 'accepted', 'paid', 'shipped', 'closed_ok', 'applied', 'confirmed_ok'].includes(s)) return 'ok';
  if (['partial', 'inspecting', 'credit_hold', 'sent', 'open', 'submitted', 'adjusted', 'retry'].includes(s)) return 'warn';
  if (['cancelled', 'lost', 'void', 'rejected', 'dead', 'error', 'disputed'].includes(s)) return 'bad';
  return 'neutral';
}
export function enumLabel(entity: string, name: string, v: any) { return field(entity, name)?.enum?.[v] ?? v ?? '—'; }
