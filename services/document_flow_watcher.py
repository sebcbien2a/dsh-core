"""P15.3 - Discreet background watcher for the ``00_``-``05_`` document flow.

The watcher automates the two human-in-the-loop entry points:

- it ingests only files of ``00_INBOX`` whose size and ``mtime`` have been
  stable for at least ``settle_delay`` seconds (OneDrive downloads and scanner
  writes are never read half-written);
- it also stabilises the optional OneDrive mirror
  (``ONEDRIVE_INBOX_PATH``, default ``workspace/document_flow/default/00_INBOX``)
  and aspirates phone/other-PC drops into ``00_INBOX``; a ``PermissionError``
  from an open PDF/Excel leaves the file in the mirror for a later retry;
- once the Excel lock (``~$*.xlsx``) is gone it runs
  ``FolderSyncService.sync_human_to_pipeline()`` so human ``VALIDE`` /
  ``REJETE`` decisions are applied continuously.

A single-instance ``.watcher.lock`` file lives in the tenant directory: two
concurrent daemons on the same tenant raise
:class:`WatcherAlreadyRunningError` instead of racing on the same files.

When a cycle actually changes the accounting state (at least one validated,
moved or rejected document), the optional ``auto_export`` pass refreshes the
per-category summary workbooks, the supplier ledger and the Obsidian
``01-contexte/cockpit_compta.md`` dashboard. Export failures are captured in
the cycle report and never block the watcher.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import signal
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from schemas.folder_state_schema import DocumentCategory, DocumentStatus, FolderState
from services.acompleter_workbook_service import derive_document_id
from services.accounting_spreadsheet_export_service import (
    CategorySummaryExporter,
    PersonalExpenseNoteExporter,
    SupplierLedgerExporter,
    collect_supplier_diagnostics,
)
from services.folder_sync_service import FolderSyncService, SyncReport
from services.folder_tree_service import (
    GED_AVOIRS_CLIENTS,
    GED_FACTURES_CLIENTS,
    GED_SUBDIRS,
    ensure_tree,
    validate_tenant_id,
)
from services.inbox_ingestion_service import (
    OUTCOME_A_COMPLETER,
    OUTCOME_DUPLICATE,
    OUTCOME_ERREUR,
    OUTCOME_VALIDE,
    InboxIngestionReport,
    InboxIngestionService,
    resolve_onedrive_inbox,
)
from services.obsidian_vault_service import VaultWriter
from services.sqlite_accounting_store import get_shared_sqlite_store

LOGGER = logging.getLogger(__name__)

LOCK_NAME = ".watcher.lock"
DEFAULT_TENANT = "APD"
DEFAULT_POLL_INTERVAL = 5.0
DEFAULT_SETTLE_DELAY = 2.0
COCKPIT_NOTE_NAME = "cockpit_compta"
COCKPIT_COMPTA_NOTE_NAME = "Cockpit_Compta"
COCKPIT_NOTE_LINKS = (
    "01-contexte/active",
    "04-graph/entities/comptabilite",
)
COCKPIT_COMPTA_NOTE_LINKS = COCKPIT_NOTE_LINKS + (
    "07-compta/Cockpit_Compta",
)


class WatcherAlreadyRunningError(ValueError):
    """Another watcher already owns the tenant's ``.watcher.lock``."""

    code = "WATCHER_ALREADY_RUNNING"

    def __init__(self, lock_path: str | os.PathLike[str], pid: int | None = None) -> None:
        self.lock_path = Path(lock_path)
        self.pid = pid
        detail = f"{self.lock_path}"
        if pid is not None:
            detail += f" (pid={pid})"
        super().__init__(f"{self.code}: {detail}")


@dataclass(frozen=True)
class WatcherCycleReport:
    """Observable outcome of one :meth:`DocumentFlowWatcher.run_once` cycle."""

    stabilized: tuple[str, ...] = ()
    mirrored: int = 0
    deferred_io: int = 0
    ingestion: InboxIngestionReport | None = None
    sync: SyncReport | None = None
    deferred_lock: bool = False
    traces: tuple[str, ...] = ()
    exports: tuple[str, ...] = ()
    export_errors: tuple[str, ...] = ()


class DocumentFlowWatcher:
    """Poll ``00_INBOX`` and the Excel workbook without blocking the pipeline."""

    def __init__(
        self,
        tenant_id: str = DEFAULT_TENANT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        settle_delay: float = DEFAULT_SETTLE_DELAY,
        *,
        root: str | os.PathLike[str] | None = None,
        store: Any | None = None,
        ingestion: InboxIngestionService | None = None,
        onedrive_inbox: str | os.PathLike[str] | None = None,
        sync: FolderSyncService | None = None,
        vault_bridge: Any | None = None,
        clock: Callable[[], float] | None = None,
        auto_export: bool = True,
        export_dir: str | os.PathLike[str] | None = None,
        vault_writer: Any | None = None,
        vault_root: str | os.PathLike[str] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if isinstance(tenant_id, (Path, os.PathLike)) and not isinstance(
            tenant_id, str
        ):
            if root is None:
                root = tenant_id
            tenant_id = DEFAULT_TENANT
        self._tenant_id = validate_tenant_id(tenant_id)
        self._poll_interval = _positive_float("poll_interval", poll_interval)
        self._settle_delay = _non_negative_float("settle_delay", settle_delay)
        if not isinstance(auto_export, bool):
            raise ValueError("auto_export must be a boolean")
        self._auto_export = auto_export
        self._export_dir = export_dir
        self._vault_writer = vault_writer
        self._vault_root = vault_root
        self._vault_bridge = vault_bridge
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._tree = ensure_tree(self._tenant_id, root)
        resolved_store = store
        if resolved_store is None and (ingestion is None or sync is None):
            resolved_store = get_shared_sqlite_store()
        self._store = resolved_store
        self._ingestion = ingestion or InboxIngestionService(
            self._tenant_id,
            resolved_store,
            root,
            vault_bridge=vault_bridge,
            onedrive_inbox=onedrive_inbox,
        )
        if onedrive_inbox is not None:
            self._onedrive_inbox = Path(onedrive_inbox).expanduser().resolve()
        else:
            mirror = getattr(self._ingestion, "onedrive_inbox", None)
            self._onedrive_inbox = (
                Path(mirror).expanduser().resolve()
                if mirror is not None
                else resolve_onedrive_inbox(self._tenant_id, root)
            )
        self._sync = sync or FolderSyncService(
            self._tenant_id,
            resolved_store,
            root,
            vault_bridge=vault_bridge,
        )
        self._clock = clock or time.monotonic
        self._logger = logger or LOGGER
        self._seen: dict[Path, tuple[tuple[int, int], float]] = {}
        self._lock_held = False
        self._stop = threading.Event()

    # -- public surface ----------------------------------------------------

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def poll_interval(self) -> float:
        return self._poll_interval

    @property
    def settle_delay(self) -> float:
        return self._settle_delay

    @property
    def auto_export(self) -> bool:
        return self._auto_export

    @property
    def tree(self) -> Any:
        return self._tree

    @property
    def ingestion(self) -> InboxIngestionService:
        return self._ingestion

    @property
    def onedrive_inbox(self) -> Path:
        return self._onedrive_inbox

    @property
    def sync(self) -> FolderSyncService:
        return self._sync

    @property
    def lock_path(self) -> Path:
        return self._tree.root / self._tenant_id / LOCK_NAME

    @property
    def lock_held(self) -> bool:
        return self._lock_held

    def run_once(self, *, now: float | None = None) -> WatcherCycleReport:
        """Run one stabilisation scan, ingestion pass and Excel sync pass."""
        acquired = False
        if not self._lock_held:
            self.acquire_lock()
            acquired = True
        try:
            return self._run_cycle(now)
        finally:
            if acquired:
                self.release_lock()

    def run_forever(self) -> None:
        """Poll until :meth:`request_stop`, ``KeyboardInterrupt`` or a signal."""
        self.acquire_lock()
        self._stop.clear()
        previous_handlers = self._install_signal_handlers()
        self._logger.info(
            "Watcher démarré (tenant=%s, intervalle=%.1fs, stabilisation=%.1fs)",
            self._tenant_id,
            self._poll_interval,
            self._settle_delay,
        )
        try:
            while not self._stop.is_set():
                try:
                    self.run_once()
                except KeyboardInterrupt:
                    self._logger.info("Arrêt demandé (clavier)")
                    break
                except Exception:  # noqa: BLE001 - daemon keeps running
                    self._logger.exception("Cycle watcher en échec; poursuite")
                if self._stop.is_set():
                    break
                try:
                    time.sleep(self._poll_interval)
                except KeyboardInterrupt:
                    self._logger.info("Arrêt demandé (clavier)")
                    break
        finally:
            self._restore_signal_handlers(previous_handlers)
            self.release_lock()
            self._logger.info("Watcher arrêté (tenant=%s)", self._tenant_id)

    def request_stop(self) -> None:
        """Ask a running loop to exit at the next safe boundary."""
        self._stop.set()

    def acquire_lock(self) -> Path:
        """Create ``.watcher.lock`` exclusively or raise if another owns it."""
        if self._lock_held:
            return self.lock_path
        path = self.lock_path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise WatcherAlreadyRunningError(path, _read_lock_pid(path)) from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(
                    {
                        "pid": os.getpid(),
                        "tenant_id": self._tenant_id,
                        "started_at": time.time(),
                    },
                    stream,
                    sort_keys=True,
                )
        except BaseException:
            _unlink_quietly(path)
            raise
        self._lock_held = True
        self._logger.debug("Verrou watcher acquis: %s", path)
        return path

    def release_lock(self) -> None:
        """Remove our own lock file; never delete a lock owned by another pid."""
        if not self._lock_held:
            return
        path = self.lock_path
        pid = _read_lock_pid(path)
        self._lock_held = False
        if pid is not None and pid != os.getpid():
            return
        _unlink_quietly(path)
        self._logger.debug("Verrou watcher libéré: %s", path)

    def __enter__(self) -> "DocumentFlowWatcher":
        self.acquire_lock()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.release_lock()

    # -- cycle internals ---------------------------------------------------

    def _run_cycle(self, now: float | None) -> WatcherCycleReport:
        moment = self._clock() if now is None else float(now)
        ready = self._stabilized_paths(moment)
        traces: list[str] = []
        ingestion_report: InboxIngestionReport | None = None
        mirrored = 0
        deferred_io = 0

        mirror_ready = (
            self._stabilized_paths(moment, self._onedrive_inbox)
            if self._onedrive_inbox != self._tree.state_dir(FolderState.INBOX)
            else []
        )
        if mirror_ready:
            aspirate = getattr(self._ingestion, "aspirate_onedrive", None)
            if callable(aspirate):
                aspiration = aspirate(mirror_ready, mirror=self._onedrive_inbox)
                ready = ready + list(aspiration.moved)
                mirrored = len(aspiration.moved)
                deferred_io = len(aspiration.locked)
                for destination in aspiration.moved:
                    traces.append(f"MIRRORED:{destination.name}")
                for name in aspiration.locked:
                    traces.append(f"MIRROR_LOCKED:{name}")
                    self._logger.debug(
                        "Miroir OneDrive verrouillé (réessai différé): %s", name
                    )
                for detail in aspiration.failed:
                    traces.append(f"MIRROR_FAILED:{detail}")

        if ready:
            names = ", ".join(path.name for path in ready)
            self._logger.info(
                "Ingestion 00_INBOX: %d fichier(s) stabilisé(s) [%s]",
                len(ready),
                names,
            )
            ingestion_report = self._ingestion.process_inbox(ready)
            for document in ingestion_report.documents:
                traces.append(
                    f"{document.sha256}:{document.outcome}:{document.destination or '-'}"
                )
                self._log_routing(document)
        else:
            ingestion_report = InboxIngestionReport()

        deferred = False
        sync_report: SyncReport | None = None
        lock = self._sync.workbook.find_lock_file()
        if lock is not None:
            deferred = True
            traces.append(f"WORKBOOK_LOCKED:{lock.name}")
            self._logger.info(
                "Synchronisation Excel différée (verrou %s)", lock.name
            )
        else:
            sync_report = self._sync.sync_human_to_pipeline()
            if sync_report.deferred_lock:
                deferred = True
                traces.append("WORKBOOK_LOCKED:race")
                self._logger.info("Synchronisation Excel différée (verrou détecté)")
            elif sync_report.skipped:
                self._logger.debug("Synchronisation Excel: aucune modification")
            else:
                self._logger.info(
                    "Synchronisation Excel: %d déplacé(s), %d archivé(s), "
                    "%d manuel(s), %d invalide(s)",
                    sync_report.moved_to_03,
                    sync_report.archived,
                    sync_report.manual_moves,
                    sync_report.skipped_invalid,
                )
            traces.append(
                "SYNC:"
                f"moved={sync_report.moved_to_03},"
                f"archived={sync_report.archived},"
                f"manual={sync_report.manual_moves},"
                f"skipped={sync_report.skipped}"
            )

        exports: tuple[str, ...] = ()
        export_errors: tuple[str, ...] = ()
        if self._auto_export and _cycle_changed(ingestion_report, sync_report):
            exports, export_errors = self._run_auto_export(
                ingestion_report, sync_report
            )
            traces.extend(exports)
            traces.extend(
                f"AUTO_EXPORT_ERROR:{detail}" for detail in export_errors
            )

        return WatcherCycleReport(
            stabilized=tuple(path.name for path in ready),
            mirrored=mirrored,
            deferred_io=deferred_io,
            ingestion=ingestion_report,
            sync=sync_report,
            deferred_lock=deferred,
            traces=tuple(traces),
            exports=exports,
            export_errors=export_errors,
        )

    # -- auto export -------------------------------------------------------

    def _run_auto_export(
        self,
        ingestion_report: InboxIngestionReport | None,
        sync_report: SyncReport | None,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Refresh summaries, ledger and cockpit; every failure is non-blocking."""
        traces: list[str] = []
        errors: list[str] = []
        if self._store is None:
            errors.append("SUMMARY:NO_STORE")
            self._logger.warning("Auto-export impossible: aucun store comptable")
            return tuple(traces), tuple(errors)

        for category in self._impacted_categories(
            ingestion_report, sync_report
        ):
            try:
                path = CategorySummaryExporter(
                    self._store, export_dir=self._export_dir
                ).export_category_summary(self._tenant_id, category)
            except Exception as exc:  # noqa: BLE001 - export never blocks
                detail = _error_detail(exc)
                errors.append(f"SUMMARY:{category.value}:{detail}")
                self._logger.warning(
                    "Auto-export synthèse %s en échec: %s",
                    category.value,
                    detail,
                )
            else:
                traces.append(f"EXPORT:SUMMARY:{category.value}:{path.name}")
                self._logger.info(
                    "Auto-export synthèse %s: %s", category.value, path.name
                )

        try:
            ledger = SupplierLedgerExporter(
                self._store, export_dir=self._export_dir
            ).export_supplier_ledger(self._tenant_id)
        except Exception as exc:  # noqa: BLE001 - export never blocks
            detail = _error_detail(exc)
            errors.append(f"LEDGER:{detail}")
            self._logger.warning("Auto-export grand livre en échec: %s", detail)
        else:
            traces.append(f"EXPORT:LEDGER:{ledger.name}")
            self._logger.info("Auto-export grand livre: %s", ledger.name)

        try:
            notes = PersonalExpenseNoteExporter(
                self._store, export_dir=self._export_dir
            ).export(self._tenant_id)
        except Exception as exc:  # noqa: BLE001 - export never blocks
            errors.append(f"PERSONAL_NOTE:{_error_detail(exc)}")
            self._logger.warning(
                "Auto-export note de frais dirigeant en échec: %s", exc
            )
        else:
            for month, path in notes.paths.items():
                traces.append(f"EXPORT:PERSONAL_NOTE:{month}:{path.name}")

        try:
            writer = self._resolve_vault_writer()
            body = self._build_cockpit_body()
            primary_result = writer.write_compta_note(
                COCKPIT_COMPTA_NOTE_NAME,
                body=body,
                wikilinks=COCKPIT_NOTE_LINKS,
            )
            alias_body = (
                body
                + "\n> Note principale : "
                "[[07-compta/Cockpit_Compta|Cockpit comptable]]\n"
            )
            alias_result = writer.write_context_note(
                COCKPIT_NOTE_NAME,
                body=alias_body,
                wikilinks=COCKPIT_COMPTA_NOTE_LINKS,
            )
        except Exception as exc:  # noqa: BLE001 - export never blocks
            detail = _error_detail(exc)
            errors.append(f"COCKPIT:{detail}")
            self._logger.warning("Auto-export cockpit en échec: %s", detail)
        else:
            traces.append(f"EXPORT:COCKPIT:{alias_result.status}")
            traces.append(
                "EXPORT:COCKPIT_07_COMPTA:"
                f"{primary_result.status}"
            )
            self._logger.info(
                "Cockpit comptable mis à jour: %s (07-compta: %s)",
                alias_result.status,
                primary_result.status,
            )

        return tuple(traces), tuple(errors)

    def _impacted_categories(
        self,
        ingestion_report: InboxIngestionReport | None,
        sync_report: SyncReport | None,
    ) -> tuple[DocumentCategory, ...]:
        """Categories touched by this cycle, with a best-effort store fallback."""
        found: list[DocumentCategory] = []
        seen: set[DocumentCategory] = set()

        def add(category: DocumentCategory | None) -> None:
            if category is not None and category not in seen:
                seen.add(category)
                found.append(category)

        if ingestion_report is not None:
            for document in ingestion_report.documents:
                if document.outcome in (OUTCOME_VALIDE, OUTCOME_A_COMPLETER):
                    add(document.category)
        if sync_report is not None:
            for trace in sync_report.traces:
                for category in DocumentCategory:
                    if f"/{category.value}/" in trace:
                        add(category)
        if found:
            return tuple(found)

        for category in DocumentCategory:
            try:
                if self._store.list_category_lines(self._tenant_id, category):
                    add(category)
                    continue
            except Exception:  # noqa: BLE001 - fallback must stay best effort
                continue
            try:
                export_root = CategorySummaryExporter(
                    self._store, export_dir=self._export_dir
                ).export_dir / self._tenant_id
                if (
                    export_root
                    / f"SYNTHESE_{self._tenant_id}_{category.value}.xlsx"
                ).is_file():
                    add(category)
            except Exception:  # noqa: BLE001
                continue
        return tuple(found)

    def _resolve_vault_writer(self) -> Any:
        if self._vault_writer is not None:
            return self._vault_writer
        writer = getattr(self._vault_bridge, "writer", None)
        if writer is not None:
            self._vault_writer = writer
            return writer
        self._vault_writer = VaultWriter(self._vault_root)
        return self._vault_writer

    def _build_cockpit_body(self) -> str:
        pending = self._count_pending_pieces()
        pending_names = self._pending_piece_names()
        latest = self._latest_validated_pieces()
        alerts = self._reconciliation_alerts()

        lines = [
            "# Cockpit comptable",
            "",
            f"- **Tenant** : `{self._tenant_id}`",
            f"- **Généré le** : {self._now_fn().isoformat()}",
            "",
            "## Pièces en 02_A_COMPLETER",
            "",
            f"- **{pending}** pièce(s) en attente de complétion.",
            "",
        ]
        if pending_names:
            for name in pending_names:
                lines.append(f"- [{name}](02_A_COMPLETER/{name})")
        else:
            lines.append("- _(aucune pièce en attente)_")
        lines.extend(["", "## Dernières pièces validées par catégorie", ""])
        if latest:
            for category, pieces in latest:
                lines.append(f"### {category.value}")
                for name, moment in pieces:
                    lines.append(f"- `{name}` — {moment}")
                lines.append("")
        else:
            lines.extend(["- _(aucune pièce validée)_", ""])

        lines.extend(["## Alertes d'écarts de rapprochement", ""])
        if alerts is None:
            lines.append("- _(diagnostic de rapprochement indisponible)_")
        elif not alerts:
            lines.append(
                "- Aucun écart résiduel détecté (`residual_delta = 0`)."
            )
        else:
            for entry in alerts:
                diagnostic = entry.diagnostic
                lines.append(
                    f"- **{entry.supplier}** — paiement "
                    f"`{diagnostic.payment_id}` de {diagnostic.amount:.2f} : "
                    f"écart résiduel **{diagnostic.residual_delta:.2f}** "
                    f"({diagnostic.status}) — {diagnostic.message}"
                )

        lines.extend(
            [
                "",
                "## Navigation",
                "",
                f"- [[{COCKPIT_NOTE_LINKS[0]}|Contexte actif]]",
                f"- [[{COCKPIT_NOTE_LINKS[1]}|Comptabilité]]",
                "- [[07-compta/Cockpit_Compta|Cockpit comptable 07-compta]]",
                "",
            ]
        )
        return "\n".join(lines)

    def _count_pending_pieces(self) -> int:
        return len(self._pending_piece_names())

    def _pending_piece_names(self) -> list[str]:
        directory = self._tree.state_dir(FolderState.A_COMPLETER)
        try:
            names = [
                path.name
                for path in directory.iterdir()
                if path.is_file()
                and not path.name.startswith("~$")
                and path.suffix.casefold() == ".pdf"
            ]
        except OSError:
            return []
        try:
            rows = self._sync.workbook.read_rows()
        except Exception:  # noqa: BLE001 - the cockpit must always be written
            rows = []
        resolved = {
            row.nom_fichier
            for row in rows
            if str(row.statut or "").upper()
            in (DocumentStatus.VALIDE.value, DocumentStatus.REJETE.value)
        }
        return sorted(name for name in names if name not in resolved)

    def _latest_validated_pieces(
        self,
    ) -> list[tuple[DocumentCategory, list[tuple[str, str]]]]:
        root = self._tree.state_dir(FolderState.VALIDE_PRET_FEC)
        grouped: dict[DocumentCategory, list[tuple[int, str, float]]] = {}
        directories: list[tuple[Path, DocumentCategory | None]] = [
            (root / category.value, category) for category in DocumentCategory
        ]
        directories.extend(
            (
                root / subdir,
                (
                    DocumentCategory.VENTES_CLIENTS
                    if subdir in {GED_FACTURES_CLIENTS, GED_AVOIRS_CLIENTS}
                    else None
                ),
            )
            for subdir in GED_SUBDIRS
        )
        for directory, folder_category in directories:
            if not directory.is_dir():
                continue
            candidates = grouped.setdefault(
                folder_category or DocumentCategory.DIVERS_A_CLASSER, []
            )
            try:
                for path in directory.iterdir():
                    if not path.is_file():
                        continue
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    category = folder_category
                    if self._store is not None:
                        try:
                            digest = hashlib.sha256(path.read_bytes()).hexdigest()
                            line = self._store.get_line(
                                self._tenant_id, derive_document_id(digest)
                            )
                            if line is not None and line.get("category"):
                                category = DocumentCategory(
                                    str(line.get("category"))
                                )
                        except Exception:  # noqa: BLE001
                            category = folder_category
                    target = grouped.setdefault(
                        category or DocumentCategory.DIVERS_A_CLASSER, []
                    )
                    target.append((stat.st_mtime_ns, path.name, stat.st_mtime))
                    if candidates is not target:
                        candidates[:] = [
                            item for item in candidates if item[1] != path.name
                        ]
            except OSError:
                continue
        latest: list[tuple[DocumentCategory, list[tuple[str, str]]]] = []
        for category in DocumentCategory:
            candidates = grouped.get(category) or []
            if not candidates:
                continue
            candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            pieces = [
                (
                    name,
                    datetime.fromtimestamp(
                        mtime, tz=timezone.utc
                    ).strftime("%Y-%m-%d %H:%M"),
                )
                for _mtime_ns, name, mtime in candidates[:3]
            ]
            latest.append((category, pieces))
        return latest

    def _reconciliation_alerts(self) -> list[Any] | None:
        try:
            entries = collect_supplier_diagnostics(
                self._store, self._tenant_id
            )
        except Exception as exc:  # noqa: BLE001 - cockpit stays available
            self._logger.warning(
                "Cockpit: diagnostic de rapprochement indisponible: %s", exc
            )
            return None
        alerts = [
            entry
            for entry in entries
            if entry.diagnostic.residual_delta != 0
        ]
        alerts.sort(
            key=lambda entry: abs(entry.diagnostic.residual_delta),
            reverse=True,
        )
        return alerts

    def _stabilized_paths(
        self, now: float, directory: Path | None = None
    ) -> list[Path]:
        inbox = directory or self._tree.state_dir(FolderState.INBOX)
        try:
            entries = sorted(inbox.iterdir(), key=lambda item: item.name)
        except OSError:
            return []
        current: dict[Path, tuple[int, int]] = {}
        ready: list[Path] = []
        for path in entries:
            if not path.is_file() or path.name.startswith("~$"):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            fingerprint = (stat.st_size, stat.st_mtime_ns)
            current[path] = fingerprint
            previous = self._seen.get(path)
            if previous is None or previous[0] != fingerprint:
                self._seen[path] = (fingerprint, now)
                if previous is None:
                    self._logger.info(
                        "Détection 00_INBOX: %s (%d octets) — stabilisation",
                        path.name,
                        stat.st_size,
                    )
                else:
                    self._logger.debug(
                        "Fichier encore en écriture: %s — stabilisation relancée",
                        path.name,
                    )
                continue
            if now - previous[1] >= self._settle_delay:
                ready.append(path)
        for path in tuple(self._seen):
            if path.parent == inbox and path not in current:
                del self._seen[path]
        return ready

    def _log_routing(self, document: Any) -> None:
        destination = document.destination or "-"
        if document.outcome == OUTCOME_VALIDE:
            self._logger.info("Routage 03_VALIDE_PRET_FEC: %s", destination)
        elif document.outcome == OUTCOME_A_COMPLETER:
            self._logger.info("Routage 02_A_COMPLETER: %s", destination)
        elif document.outcome == OUTCOME_DUPLICATE:
            self._logger.info(
                "Routage 05_ARCHIVE_ERREUR (doublon): %s", destination
            )
        elif document.outcome == OUTCOME_ERREUR:
            self._logger.info(
                "Routage 05_ARCHIVE_ERREUR: %s (%s)",
                destination,
                document.detail or "ERREUR",
            )

    # -- signals -----------------------------------------------------------

    def _install_signal_handlers(self) -> dict[int, Any]:
        previous: dict[int, Any] = {}
        for name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                previous[sig] = signal.getsignal(sig)
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError, RuntimeError):
                previous.pop(sig, None)
        return previous

    def _restore_signal_handlers(self, previous: dict[int, Any]) -> None:
        for sig, handler in previous.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError, RuntimeError):
                continue

    def _on_signal(self, signum: int, _frame: Any) -> None:
        self._logger.info("Signal %s reçu — arrêt du watcher", signum)
        self._stop.set()


def _cycle_changed(
    ingestion_report: InboxIngestionReport | None,
    sync_report: SyncReport | None,
) -> bool:
    """True when the cycle validated, moved or rejected at least one document."""
    if ingestion_report is not None and (
        ingestion_report.moved_to_03
        or ingestion_report.moved_to_02
        or ingestion_report.archived
    ):
        return True
    if sync_report is not None and (
        sync_report.moved_to_03
        or sync_report.archived
        or sync_report.manual_moves
    ):
        return True
    return False


def _error_detail(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    label = (
        code
        if isinstance(code, str) and code
        else type(exc).__name__.upper()
    )
    message = str(exc).strip()
    return f"{label}: {message}" if message and message != label else label


def _positive_float(name: str, value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a positive number")
    return number


def _non_negative_float(name: str, value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative number") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be a non-negative number")
    return number


def _read_lock_pid(path: Path) -> int | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    pid = data.get("pid") if isinstance(data, dict) else None
    return pid if isinstance(pid, int) else None


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


__all__ = [
    "COCKPIT_COMPTA_NOTE_NAME",
    "COCKPIT_NOTE_NAME",
    "DEFAULT_POLL_INTERVAL",
    "DEFAULT_SETTLE_DELAY",
    "DEFAULT_TENANT",
    "DocumentFlowWatcher",
    "LOCK_NAME",
    "WatcherAlreadyRunningError",
    "WatcherCycleReport",
]
