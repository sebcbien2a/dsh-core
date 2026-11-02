"""Declarative task engine dispatching ``instructions.md`` workflows (P15.2).

The engine is the small orchestration layer above the two workflow services:
it loads the project folder, parses ``instructions.md`` once and dispatches to
:class:`~services.declarative_tarif_service.DeclarativeTarifService` for
``TARIFS_EXCEL`` or
:class:`~services.declarative_pdf_service.DeclarativePdfService` for
``PDF_PRESENTATION``. Execution stays deterministic, offline and confined to the
project folder.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Union

from schemas.declarative_task_schema import DeclarativeTask, WorkflowType, load_task
from services.declarative_pdf_service import (
    DeclarativePdfService,
    PdfRenderer,
    PdfRunResult,
)
from services.declarative_tarif_service import DeclarativeTarifService, TarifRunResult

WorkflowResult = Union[TarifRunResult, PdfRunResult]


class InstructionsTaskEngine:
    """Parse one project folder and execute its declared workflow."""

    def __init__(
        self,
        task: DeclarativeTask | str | os.PathLike[str],
        *,
        renderer: str | PdfRenderer | None = None,
        clock: Callable[[], datetime | date] | None = None,
        strict: bool = False,
    ) -> None:
        self._task = task if isinstance(task, DeclarativeTask) else load_task(task, strict=strict)
        self._renderer = renderer
        self._clock = clock

    @property
    def task(self) -> DeclarativeTask:
        return self._task

    @property
    def workflow(self) -> WorkflowType:
        return self._task.workflow

    def run(self) -> WorkflowResult:
        if self.workflow is WorkflowType.TARIFS_EXCEL:
            return DeclarativeTarifService(self._task, clock=self._clock).run()
        return DeclarativePdfService(
            self._task, renderer=self._renderer, clock=self._clock
        ).run()

    def execute(self) -> WorkflowResult:
        """Alias of :meth:`run` for callers preferring an execute verb."""
        return self.run()


def run_instructions_task(
    root: str | os.PathLike[str] | DeclarativeTask,
    *,
    renderer: str | PdfRenderer | None = None,
    clock: Callable[[], datetime | date] | None = None,
    strict: bool = False,
) -> WorkflowResult:
    """Convenience entry point: run the workflow declared by ``instructions.md``."""
    return InstructionsTaskEngine(
        root, renderer=renderer, clock=clock, strict=strict
    ).run()
