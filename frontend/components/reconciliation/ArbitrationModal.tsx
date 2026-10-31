"use client";

import { FormEvent, useState } from "react";
import type {
  ArbitrationAction,
  ArbitrationRequest,
  ReconciliationLineView,
} from "../../types/reconciliation";

const ACCOUNT_PATTERN = /^\d{6}$/;

interface ArbitrationModalProps {
  line: ReconciliationLineView;
  tenantId: string;
  apiBaseUrl?: string;
  onClose: () => void;
  onArbitrated: () => void;
}

const ACTIONS: { value: ArbitrationAction; label: string; hint: string }[] = [
  { value: "ATTACH_INVOICE", label: "Rattacher une facture", hint: "Lier cette opération à une facture existante." },
  { value: "PERSONAL_EXPENSE", label: "Dépense personnelle", hint: "Imputation automatique 108000 / 455000, sans TVA." },
  { value: "VALIDATE_WITHOUT_INVOICE", label: "Valider sans facture", hint: "Motif obligatoire ; comptes optionnels." },
];

export default function ArbitrationModal({
  line,
  tenantId,
  apiBaseUrl = "",
  onClose,
  onArbitrated,
}: ArbitrationModalProps) {
  const [action, setAction] = useState<ArbitrationAction>("ATTACH_INVOICE");
  const [invoiceId, setInvoiceId] = useState("");
  const [reason, setReason] = useState("");
  const [debit, setDebit] = useState("");
  const [credit, setCredit] = useState("");
  const [remember, setRemember] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const showAccounts = action !== "PERSONAL_EXPENSE";

  function validate(): string | null {
    if (action === "ATTACH_INVOICE" && !invoiceId.trim()) return "Le numéro de facture est obligatoire.";
    if (action === "VALIDATE_WITHOUT_INVOICE" && !reason.trim()) return "Le motif est obligatoire.";
    if (showAccounts) {
      if (debit && !ACCOUNT_PATTERN.test(debit)) return "Le compte débit doit comporter 6 chiffres.";
      if (credit && !ACCOUNT_PATTERN.test(credit)) return "Le compte crédit doit comporter 6 chiffres.";
    }
    return null;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const problem = validate();
    if (problem) {
      setError(problem);
      return;
    }
    const payload: ArbitrationRequest = {
      line_id: line.line_id,
      action,
      invoice_id: action === "ATTACH_INVOICE" ? invoiceId.trim() : null,
      debit_account: showAccounts && debit ? debit : null,
      credit_account: showAccounts && credit ? credit : null,
      reason: action !== "ATTACH_INVOICE" && reason.trim() ? reason.trim() : null,
      remember_rule: remember,
      tenant_id: tenantId,
    };
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch(`${apiBaseUrl}/reconciliation/arbitrate`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Tenant-ID": tenantId },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
        const detail = typeof body?.detail === "string" ? body.detail : `HTTP ${response.status}`;
        throw new Error(detail);
      }
      onArbitrated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Erreur inconnue");
    } finally {
      setSubmitting(false);
    }
  }

  const inputClass =
    "mt-1 w-full rounded border border-gray-300 px-2 py-1 text-sm focus:border-blue-500 focus:outline-none";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="arbitration-title"
    >
      <form onSubmit={handleSubmit} className="w-full max-w-lg rounded-lg bg-white p-6 shadow-xl">
        <h2 id="arbitration-title" className="text-lg font-semibold text-gray-900">
          Arbitrer l&apos;opération
        </h2>
        <p className="mt-1 text-sm text-gray-600">
          {line.operation_date ?? "—"} · {line.label ?? "—"} ·{" "}
          {line.debit_amount ?? line.credit_amount ?? "—"} {line.currency ?? ""}
        </p>

        <fieldset className="mt-4 space-y-2">
          <legend className="sr-only">Action</legend>
          {ACTIONS.map((a) => (
            <label
              key={a.value}
              className={`flex cursor-pointer items-start gap-2 rounded border p-2 ${
                action === a.value ? "border-blue-500 bg-blue-50" : "border-gray-200"
              }`}
            >
              <input
                type="radio"
                name="action"
                className="mt-1"
                checked={action === a.value}
                onChange={() => setAction(a.value)}
              />
              <span>
                <span className="block text-sm font-medium text-gray-900">{a.label}</span>
                <span className="block text-xs text-gray-500">{a.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>

        {action === "ATTACH_INVOICE" && (
          <label className="mt-4 block text-sm text-gray-700">
            N° de facture
            <input
              className={inputClass}
              value={invoiceId}
              maxLength={128}
              onChange={(e) => setInvoiceId(e.target.value)}
              required
            />
          </label>
        )}

        {action === "VALIDATE_WITHOUT_INVOICE" && (
          <label className="mt-4 block text-sm text-gray-700">
            Motif (obligatoire)
            <textarea
              className={inputClass}
              value={reason}
              maxLength={500}
              rows={2}
              onChange={(e) => setReason(e.target.value)}
              required
            />
          </label>
        )}

        {action === "PERSONAL_EXPENSE" && (
          <p className="mt-4 rounded bg-purple-50 p-2 text-sm text-purple-800">
            Débit 108000 · Crédit 455000 · TVA 0,00
          </p>
        )}

        {showAccounts && (
          <div className="mt-4 grid grid-cols-2 gap-3">
            <label className="text-sm text-gray-700">
              Compte débit (optionnel)
              <input
                className={inputClass}
                value={debit}
                inputMode="numeric"
                maxLength={6}
                onChange={(e) => setDebit(e.target.value)}
              />
            </label>
            <label className="text-sm text-gray-700">
              Compte crédit (optionnel)
              <input
                className={inputClass}
                value={credit}
                inputMode="numeric"
                maxLength={6}
                onChange={(e) => setCredit(e.target.value)}
              />
            </label>
          </div>
        )}

        <label className="mt-4 flex items-center gap-2 text-sm text-gray-700">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Mémoriser cette règle pour les prochaines opérations
        </label>

        {error && (
          <p role="alert" className="mt-3 text-sm text-red-600">
            {error}
          </p>
        )}

        <div className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
          >
            Annuler
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {submitting ? "Envoi…" : "Confirmer"}
          </button>
        </div>
      </form>
    </div>
  );
}
