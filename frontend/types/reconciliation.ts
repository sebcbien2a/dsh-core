export type ReconciliationLineStatus =
  | "PENDING"
  | "MATCHED"
  | "ARBITRATED_PERSONAL"
  | "ARBITRATED_NO_INVOICE";

export type ReconciliationStatusFilter =
  | "ALL"
  | "PENDING"
  | "INVOICE_ATTACHED"
  | "BOOKED_PERSONAL"
  | "RELEASED_WITHOUT_INVOICE";

export type ArbitrationAction =
  | "ATTACH_INVOICE"
  | "PERSONAL_EXPENSE"
  | "VALIDATE_WITHOUT_INVOICE";

export interface ArbitrationMetadata {
  reason: string | null;
  arbitrated_at: string | null;
}

export interface ReconciliationLineView {
  line_id: string;
  operation_date: string | null;
  label: string | null;
  debit_amount: string | null;
  credit_amount: string | null;
  currency: string | null;
  status: ReconciliationLineStatus;
  counterparty: string | null;
  invoice_id: string | null;
  invoice_url: string | null;
  debit_account: string | null;
  credit_account: string | null;
  vat_amount: string | null;
  arbitration_metadata: ArbitrationMetadata | null;
}

export interface ArbitrationRequest {
  line_id: string;
  action: ArbitrationAction;
  invoice_id: string | null;
  debit_account: string | null;
  credit_account: string | null;
  reason: string | null;
  remember_rule: boolean;
  tenant_id: string | null;
}

export interface ReconciliationLinesResponse {
  items: ReconciliationLineView[];
  total: number;
  limit: number;
  offset: number;
}
