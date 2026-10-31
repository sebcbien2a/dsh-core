"use client";

import { useCallback, useEffect, useState } from "react";
import type {
  ReconciliationLineStatus,
  ReconciliationLineView,
  ReconciliationLinesResponse,
  ReconciliationStatusFilter,
} from "../../types/reconciliation";
import ArbitrationModal from "./ArbitrationModal";

interface ReconciliationTableProps {
  tenantId: string;
  apiBaseUrl?: string;
  pageSize?: number;
}

const STATUS_BADGES: Record<ReconciliationLineStatus, { label: string; className: string }> = {
  MATCHED: { label: "Rapprochée", className: "bg-green-100 text-green-800" },
  PENDING: { label: "À traiter", className: "bg-orange-100 text-orange-800" },
  ARBITRATED_PERSONAL: { label: "Dépense perso", className: "bg-purple-100 text-purple-800" },
  ARBITRATED_NO_INVOICE: { label: "Sans facture", className: "bg-blue-100 text-blue-800" },
};

const FILTER_OPTIONS: { value: ReconciliationStatusFilter; label: string }[] = [
  { value: "ALL", label: "Tous les statuts" },
  { value: "PENDING", label: "À traiter" },
  { value: "INVOICE_ATTACHED", label: "Rapprochées" },
  { value: "BOOKED_PERSONAL", label: "Dépenses perso" },
  { value: "RELEASED_WITHOUT_INVOICE", label: "Sans facture" },
];

const SEARCH_DEBOUNCE_MS = 300;
const PAGE_SIZE_MAX = 200;

function accounts(line: ReconciliationLineView): string {
  if (!line.debit_account && !line.credit_account) return "—";
  return `${line.debit_account ?? "—"} / ${line.credit_account ?? "—"}`;
}

type ExportKind = "fec" | "reconciliation" | "arbitrations";

const EXPORT_FALLBACK_NAMES: Record<ExportKind, string> = {
  fec: "FEC.txt",
  reconciliation: "REVISION_reconciliation.csv",
  arbitrations: "REVISION_arbitrations.csv",
};

function filenameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim());
    } catch {
      /* fall through to plain filename */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  const name = plain?.[1]?.trim().replace(/[\\/]/g, "_");
  return name || fallback;
}

async function exportErrorMessage(response: Response): Promise<string> {
  let detail = "";
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
    else if (body.detail !== undefined) detail = JSON.stringify(body.detail);
  } catch {
    /* non-JSON body */
  }
  return detail ? `HTTP ${response.status} : ${detail}` : `HTTP ${response.status}`;
}

export default function ReconciliationTable({
  tenantId,
  apiBaseUrl = "",
  pageSize = 50,
}: ReconciliationTableProps) {
  const limit = Math.min(Math.max(1, pageSize), PAGE_SIZE_MAX);
  const [status, setStatus] = useState<ReconciliationStatusFilter>("ALL");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<ReconciliationLinesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ReconciliationLineView | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [exporting, setExporting] = useState<ExportKind | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  const handleExport = useCallback(
    async (kind: ExportKind) => {
      const params = new URLSearchParams();
      if (kind !== "fec") {
        params.set("scope", kind);
        params.set("format", "csv");
      }
      if (dateFrom) params.set("date_from", dateFrom);
      if (dateTo) params.set("date_to", dateTo);
      const path = kind === "fec" ? "/accounting/export/fec" : "/accounting/export/spreadsheet";
      const query = params.toString();

      setExporting(kind);
      setExportError(null);
      try {
        const response = await fetch(`${apiBaseUrl}${path}${query ? `?${query}` : ""}`, {
          headers: { "X-Tenant-ID": tenantId },
        });
        if (!response.ok) throw new Error(await exportErrorMessage(response));
        const blob = await response.blob();
        const filename = filenameFromDisposition(
          response.headers.get("Content-Disposition"),
          EXPORT_FALLBACK_NAMES[kind],
        );
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        window.URL.revokeObjectURL(url);
      } catch (e: unknown) {
        setExportError(e instanceof Error ? e.message : "Erreur inconnue");
      } finally {
        setExporting(null);
      }
    },
    [apiBaseUrl, tenantId, dateFrom, dateTo],
  );

  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput.trim());
      setOffset(0);
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset), status });
    if (search) params.set("search", search);

    setLoading(true);
    setError(null);
    fetch(`${apiBaseUrl}/reconciliation/lines?${params.toString()}`, {
      headers: { "X-Tenant-ID": tenantId },
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return (await response.json()) as ReconciliationLinesResponse;
      })
      .then(setData)
      .catch((e: unknown) => {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setError(e instanceof Error ? e.message : "Erreur inconnue");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [apiBaseUrl, tenantId, limit, offset, status, search, reloadKey]);

  const handleArbitrated = useCallback(() => {
    setSelected(null);
    setReloadKey((k) => k + 1);
  }, []);

  const total = data?.total ?? 0;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  const hasPrev = offset > 0;
  const hasNext = offset + limit < total;

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <select
          aria-label="Filtrer par statut"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as ReconciliationStatusFilter);
            setOffset(0);
          }}
          className="rounded border border-gray-300 px-2 py-1 text-sm"
        >
          {FILTER_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <input
          type="search"
          aria-label="Rechercher"
          placeholder="Libellé, tiers, n° facture…"
          value={searchInput}
          maxLength={200}
          onChange={(e) => setSearchInput(e.target.value)}
          className="w-72 rounded border border-gray-300 px-2 py-1 text-sm"
        />
        {loading && <span className="text-sm text-gray-500">Chargement…</span>}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-1 text-sm text-gray-700">
          Du
          <input
            type="date"
            aria-label="Date de début"
            value={dateFrom}
            max={dateTo || undefined}
            onChange={(e) => setDateFrom(e.target.value)}
            className="rounded border border-gray-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="flex items-center gap-1 text-sm text-gray-700">
          au
          <input
            type="date"
            aria-label="Date de fin"
            value={dateTo}
            min={dateFrom || undefined}
            onChange={(e) => setDateTo(e.target.value)}
            className="rounded border border-gray-300 px-2 py-1 text-sm"
          />
        </label>
        {(
          [
            ["fec", "Export FEC (.txt)"],
            ["reconciliation", "Journal de révision (.csv)"],
            ["arbitrations", "Relevé des arbitrages (.csv)"],
          ] as [ExportKind, string][]
        ).map(([kind, label]) => (
          <button
            key={kind}
            type="button"
            disabled={exporting !== null}
            onClick={() => void handleExport(kind)}
            className="rounded border border-gray-300 bg-white px-3 py-1 text-sm hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {exporting === kind ? "Export en cours…" : label}
          </button>
        ))}
      </div>

      {exportError && (
        <p role="alert" className="text-sm text-red-600">
          Échec de l&apos;export ({exportError}).
        </p>
      )}

      {error && (
        <p role="alert" className="text-sm text-red-600">
          Impossible de charger les opérations ({error}).
        </p>
      )}

      <div className="overflow-x-auto rounded border border-gray-200">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50 text-left text-xs font-semibold uppercase text-gray-600">
            <tr>
              <th className="px-3 py-2">Date</th>
              <th className="px-3 py-2">Libellé</th>
              <th className="px-3 py-2 text-right">Débit</th>
              <th className="px-3 py-2 text-right">Crédit</th>
              <th className="px-3 py-2">Tiers</th>
              <th className="px-3 py-2">N° Facture</th>
              <th className="px-3 py-2">Compte PCG</th>
              <th className="px-3 py-2">Pièce justificative</th>
              <th className="px-3 py-2">Statut / Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 bg-white">
            {data?.items.map((line) => {
              const badge = STATUS_BADGES[line.status];
              return (
                <tr key={line.line_id}>
                  <td className="whitespace-nowrap px-3 py-2">{line.operation_date ?? "—"}</td>
                  <td className="px-3 py-2">{line.label ?? "—"}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                    {line.debit_amount ?? ""}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                    {line.credit_amount ?? ""}
                  </td>
                  <td className="px-3 py-2">{line.counterparty ?? "—"}</td>
                  <td className="px-3 py-2">{line.invoice_id ?? "—"}</td>
                  <td className="whitespace-nowrap px-3 py-2">{accounts(line)}</td>
                  <td className="px-3 py-2">
                    {line.invoice_url ? (
                      <a
                        href={line.invoice_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-600 underline"
                      >
                        Ouvrir le PDF
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${badge.className}`}>
                      {badge.label}
                    </span>
                    {line.status === "PENDING" && (
                      <button
                        type="button"
                        onClick={() => setSelected(line)}
                        className="ml-2 rounded bg-blue-600 px-2 py-0.5 text-xs text-white hover:bg-blue-700"
                      >
                        Arbitrer
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
            {data && data.items.length === 0 && (
              <tr>
                <td colSpan={9} className="px-3 py-6 text-center text-gray-500">
                  Aucune opération.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-sm text-gray-600">
        <span>
          {from}–{to} sur {total}
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={!hasPrev || loading}
            onClick={() => setOffset(Math.max(0, offset - limit))}
            className="rounded border border-gray-300 px-3 py-1 disabled:opacity-40"
          >
            Précédent
          </button>
          <button
            type="button"
            disabled={!hasNext || loading}
            onClick={() => setOffset(offset + limit)}
            className="rounded border border-gray-300 px-3 py-1 disabled:opacity-40"
          >
            Suivant
          </button>
        </div>
      </div>

      {selected && (
        <ArbitrationModal
          line={selected}
          tenantId={tenantId}
          apiBaseUrl={apiBaseUrl}
          onClose={() => setSelected(null)}
          onArbitrated={handleArbitrated}
        />
      )}
    </section>
  );
}
