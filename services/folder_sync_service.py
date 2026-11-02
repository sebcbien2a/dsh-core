"""Bidirectional synchronisation of the ``00_``-``05_`` document flow (P14.3).

Pipeline -> Human: every file parked in ``02_A_COMPLETER`` gets its row in
``_A_COMPLETER.xlsx`` (non-destructive rules of P14.2). Human -> Pipeline: once
the Excel lock (``~$*.xlsx``) is gone, the workbook is processed only when its
``mtime`` + SHA-256 signature changed; ``VALIDE`` rows are persisted to
:class:`services.sqlite_accounting_store.SqliteAccountingStore` then moved to
``03_VALIDE_PRET_FEC/<CATEGORIE>/``, ``REJETE`` rows move to
``05_ARCHIVE_ERREUR/REJET_MANUEL/`` (state traced in the store). A file the
human moved by hand into ``03_`` wins over every workbook intent: its state and
category are synced to the store and the ``Statut`` cell is aligned on
``VALIDE``. An already-validated document whose human ``Statut`` switches to
``REJETE`` ("annule et remplace") is pulled back from ``03_`` to
``REJET_MANUEL/`` and deactivated in the store so it leaves every
reconciliation pool and export. SHA-256 duplicates and unreadable files are
routed to ``05_ARCHIVE_ERREUR/``; nothing is ever deleted.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from schemas.folder_state_schema import (
    DocumentCategory,
    DocumentStateRecord,
    DocumentStatus,
    FolderState,
)
from services.acompleter_workbook_service import (
    OUTCOME_CREATED,
    OUTCOME_UPDATED,
    AcompleterWorkbook,
    derive_document_id,
)
from services.folder_tree_service import ensure_tree, validate_tenant_id

_CHUNK = 1 << 16
_STATUT_VALIDE = DocumentStatus.VALIDE.value
_STATUT_REJETE = DocumentStatus.REJETE.value


class FolderSyncError(ValueError):
    """Invalid folder sync request; ``code`` is stable for tests and logs."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class SyncReport:
    """Observable outcome of one synchronisation pass."""

    skipped: bool = False
    deferred_lock: int = 0
    rows_written: int = 0
    rows_updated: int = 0
    moved_to_03: int = 0
    archived: int = 0
    manual_moves: int = 0
    skipped_invalid: int = 0
    traces: tuple[str, ...] = ()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_dest(path: Path) -> Path:
    if not path.exists():
        return path
    index = 1
    while True:
        candidate = path.with_name(f"{path.stem}__{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _category_of_cell(value: object) -> DocumentCategory | None:
    try:
        return DocumentCategory(str(value).strip())
    except (ValueError, TypeError):
        return None


class _Acc:
    def __init__(self) -> None:
        self.rows_written = 0
        self.rows_updated = 0
        self.moved_to_03 = 0
        self.archived = 0
        self.manual_moves = 0
        self.skipped_invalid = 0
        self.traces: list[str] = []

    def report(self, *, skipped: bool = False, deferred_lock: int = 0) -> SyncReport:
        return SyncReport(
            skipped=skipped,
            deferred_lock=deferred_lock,
            rows_written=self.rows_written,
            rows_updated=self.rows_updated,
            moved_to_03=self.moved_to_03,
            archived=self.archived,
            manual_moves=self.manual_moves,
            skipped_invalid=self.skipped_invalid,
            traces=tuple(self.traces),
        )


class FolderSyncService:
    """Synchronise one tenant's folder tree, workbook and accounting store."""

    def __init__(
        self,
        tenant_id: str,
        store: Any,
        root: str | os.PathLike[str] | None = None,
    ) -> None:
        self._tenant_id = validate_tenant_id(tenant_id)
        self._store = store
        self._tree = ensure_tree(self._tenant_id, root)
        self._workbook = AcompleterWorkbook(self._tree.root / self._tenant_id)
        self._last_seen: tuple[Any, Any] | None = None

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def tree(self) -> Any:
        return self._tree

    @property
    def workbook(self) -> AcompleterWorkbook:
        return self._workbook

    def workbook_signature(self) -> tuple[int, str]:
        """``(mtime_ns, sha256)`` of ``_A_COMPLETER.xlsx`` (detection key)."""
        self._workbook.ensure_workbook()
        path = self._workbook.workbook_path
        stat = path.stat()
        return (stat.st_mtime_ns, _file_sha256(path))

    def sync_pipeline_to_human(
        self, records: Iterable[DocumentStateRecord] = ()
    ) -> SyncReport:
        """Provision the workbook rows from explicit records and ``02_`` files."""
        if self._workbook.find_lock_file() is not None:
            return SyncReport(deferred_lock=1)
        self._workbook.ensure_workbook()
        acc = _Acc()
        for record in records:
            outcome = self._workbook.upsert(
                record.sha256,
                record.nom_fichier,
                record.champs_manquants,
                record.categorie_suggeree,
            )
            self._count_upsert(outcome, acc)
        known = {
            row.sha256.strip().lower() for row in self._workbook.read_rows()
        }
        for path in sorted(self._inbox_dir().iterdir(), key=lambda p: p.name):
            if not path.is_file():
                continue
            try:
                digest = _file_sha256(path)
            except OSError:
                continue
            if digest in known:
                continue
            known.add(digest)
            outcome = self._workbook.upsert(
                digest, path.name, [], DocumentCategory.DIVERS_A_CLASSER
            )
            self._count_upsert(outcome, acc)
        return acc.report()

    def sync_human_to_pipeline(self) -> SyncReport:
        """Detect human changes and reconcile tree, workbook and store."""
        if self._workbook.find_lock_file() is not None:
            return SyncReport(deferred_lock=1)
        self._workbook.ensure_workbook()
        seen = (self.workbook_signature(), self._inbox_fingerprint())
        if self._last_seen == seen:
            return SyncReport(skipped=True)
        acc = _Acc()
        inbox_by_hash, extras = self._collect_inbox()
        validated = self._validated_index()
        for path, reason in extras:
            self._archive(path, acc, reason)
        for digest, path in list(inbox_by_hash.items()):
            if digest in validated:
                self._archive(path, acc, "DUPLICATE_SHA256")
                del inbox_by_hash[digest]
        self._process_manual_moves(acc, inbox_by_hash, validated)
        self._process_rows(acc, inbox_by_hash)
        self._last_seen = (self.workbook_signature(), self._inbox_fingerprint())
        return acc.report()

    # -- internals -------------------------------------------------------

    @staticmethod
    def _count_upsert(outcome: str, acc: _Acc) -> None:
        if outcome == OUTCOME_CREATED:
            acc.rows_written += 1
        elif outcome == OUTCOME_UPDATED:
            acc.rows_updated += 1

    def _inbox_dir(self) -> Path:
        return self._tree.state_dir(FolderState.A_COMPLETER)

    def _tenant_dir(self) -> Path:
        return self._tree.root / self._tenant_id

    def _archive_dir(self) -> Path:
        return self._tree.state_dir(FolderState.ARCHIVE_ERREUR)

    def _inbox_fingerprint(self) -> tuple[tuple[str, int], ...]:
        inbox = self._inbox_dir()
        return tuple(
            sorted(
                (p.name, p.stat().st_mtime_ns) for p in inbox.iterdir() if p.is_file()
            )
        )

    def _collect_inbox(self) -> tuple[dict[str, Path], list[tuple[Path, str]]]:
        inbox = self._inbox_dir()
        by_hash: dict[str, Path] = {}
        extras: list[tuple[Path, str]] = []
        for path in sorted(inbox.iterdir(), key=lambda p: p.name):
            if not path.is_file():
                continue
            try:
                digest = _file_sha256(path)
            except OSError:
                extras.append((path, "UNREADABLE"))
                continue
            if digest in by_hash:
                extras.append((path, "DUPLICATE_SHA256"))
            else:
                by_hash[digest] = path
        return by_hash, extras

    def _validated_index(self) -> dict[str, tuple[Path, DocumentCategory]]:
        index: dict[str, tuple[Path, DocumentCategory]] = {}
        for category in DocumentCategory:
            directory = self._tree.category_dir(
                FolderState.VALIDE_PRET_FEC, category
            )
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir(), key=lambda p: p.name):
                if not path.is_file():
                    continue
                try:
                    digest = _file_sha256(path)
                except OSError:
                    continue
                index.setdefault(digest, (path, category))
        return index

    def _archive(self, path: Path, acc: _Acc, reason: str) -> None:
        dest = _unique_dest(self._archive_dir() / path.name)
        os.replace(path, dest)
        acc.archived += 1
        acc.traces.append(f"{reason}:{path.name}->{dest.relative_to(self._tenant_dir())}")

    def _persist(self, sha256: str, nom_fichier: str, status: str, rel_path: str) -> str:
        doc_id = derive_document_id(sha256)
        line = {
            "operation_date": None,
            "label": nom_fichier,
            "debit_amount": None,
            "credit_amount": None,
            "currency": None,
            "counterparty": None,
            "invoice_id": doc_id,
            "invoice_url": rel_path,
            "status": status,
        }
        return self._store.upsert_document_line(self._tenant_id, doc_id, line)

    def _process_manual_moves(
        self,
        acc: _Acc,
        inbox_by_hash: dict[str, Path],
        validated: dict[str, tuple[Path, DocumentCategory]],
    ) -> None:
        for row in self._workbook.read_rows():
            digest = row.sha256.strip().lower()
            if digest in inbox_by_hash:
                continue
            found = validated.get(digest)
            if found is None:
                continue
            dest_path, category = found
            statut = row.statut.strip()
            if statut == _STATUT_REJETE and self._store_status(digest) is not None:
                self._cancel_validated(acc, dest_path, digest)
                continue
            rel_path = dest_path.relative_to(self._tenant_dir()).as_posix()
            self._persist(digest, dest_path.name, _STATUT_VALIDE, rel_path)
            self._workbook.align_statut(digest, DocumentStatus.VALIDE)
            acc.manual_moves += 1
            acc.traces.append(f"{digest}:MANUAL_MOVED:{rel_path}")

    def _process_rows(self, acc: _Acc, inbox_by_hash: dict[str, Path]) -> None:
        for row in self._workbook.read_rows():
            digest = row.sha256.strip().lower()
            source = inbox_by_hash.get(digest)
            if source is None:
                continue
            statut = row.statut.strip()
            if statut == _STATUT_VALIDE:
                self._process_valide(acc, row, digest, source)
            elif statut == _STATUT_REJETE:
                self._process_rejete(acc, row, digest, source)

    def _process_valide(
        self, acc: _Acc, row: Any, digest: str, source: Path
    ) -> None:
        category = _category_of_cell(row.categorie_suggeree)
        champs = {t.strip() for t in row.champs_manquants.split(",") if t.strip()}
        if category is None or "categorie" in champs:
            acc.skipped_invalid += 1
            acc.traces.append(f"{digest}:SKIPPED_INVALID_CATEGORY")
            return
        dest_dir = self._tree.category_dir(FolderState.VALIDE_PRET_FEC, category)
        dest = dest_dir / source.name
        if dest.exists():
            self._archive(source, acc, "DEST_EXISTS")
            return
        rel_path = dest.relative_to(self._tenant_dir()).as_posix()
        self._persist(digest, source.name, _STATUT_VALIDE, rel_path)
        os.replace(source, dest)
        acc.moved_to_03 += 1
        acc.traces.append(f"{digest}:02_A_COMPLETER->{rel_path}")

    def _process_rejete(
        self, acc: _Acc, row: Any, digest: str, source: Path
    ) -> None:
        dest = _unique_dest(self._tree.rejet_manuel_dir() / source.name)
        rel_path = dest.relative_to(self._tenant_dir()).as_posix()
        self._persist(digest, source.name, _STATUT_REJETE, rel_path)
        self._deactivate(digest)
        os.replace(source, dest)
        acc.archived += 1
        acc.traces.append(f"{digest}:02_A_COMPLETER->{rel_path}")

    def _cancel_validated(self, acc: _Acc, source: Path, digest: str) -> None:
        dest = _unique_dest(self._tree.rejet_manuel_dir() / source.name)
        rel_path = dest.relative_to(self._tenant_dir()).as_posix()
        self._persist(digest, source.name, _STATUT_REJETE, rel_path)
        self._deactivate(digest)
        os.replace(source, dest)
        acc.archived += 1
        acc.traces.append(f"{digest}:03_VALIDE_PRET_FEC->{rel_path}")

    def _store_status(self, digest: str) -> str | None:
        line = self._store.get_line(self._tenant_id, derive_document_id(digest))
        if line is None:
            return None
        return str(line.get("raw_status") or "")

    def _deactivate(self, digest: str) -> None:
        deactivate = getattr(self._store, "deactivate_rejected_document", None)
        if callable(deactivate):
            deactivate(self._tenant_id, digest)
