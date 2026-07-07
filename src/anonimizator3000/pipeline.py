"""Synchronous runner that drives one document through the Carrier flow.

``process_document`` seeds a :class:`JobContext`, spins up an embedded
SQLite-backed Fala runtime, instantiates :data:`DOCUMENT_FLOW`, and drives it to
idle. Fala orchestrates the convert -> load -> anonymize -> serialize blocks; the
runner just collects the result the blocks left in the shared registry. It calls
``asyncio.run`` internally, so invoke it from a plain thread (never from inside a
running event loop) -- the job queue does exactly that via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from doctotext import DocumentError
from fala.carrier_runtime import FalaRuntime
from fala.runtime_backend import CarrierProcessStatus, Run

from anonimizator3000.config import DEFAULT_REPLACEMENT_STYLE
from anonimizator3000.flow import CONVERT_STEP, DOCUMENT_FLOW
from anonimizator3000.steps import (
    JobContext,
    SegmentAnonymizer,
    discard_job,
    job_context,
    register_job,
)

_FAILED_STATUSES = {
    CarrierProcessStatus.failed,
    CarrierProcessStatus.cancelled,
    CarrierProcessStatus.timed_out,
}
_MAX_TICKS = 20


@dataclass(frozen=True)
class ProcessedDocument:
    filename: str
    content_type: str
    data: bytes
    findings: dict[str, int]


def process_document(
    filename: str,
    content_type: str,
    data: bytes,
    *,
    anonymizer: SegmentAnonymizer,
    max_text_chars: int,
    replacement_style: str = DEFAULT_REPLACEMENT_STYLE,
    run_id: str | None = None,
) -> ProcessedDocument:
    run_id = run_id or f"run_{uuid4().hex}"
    context = JobContext(
        filename=filename,
        content_type=content_type,
        source_bytes=data,
        anonymizer=anonymizer,
        max_text_chars=max_text_chars,
        replacement_style=replacement_style,
    )
    register_job(run_id, context)
    try:
        asyncio.run(_drive(run_id))
        context = job_context(run_id)
        if context.result_bytes is None:
            raise DocumentError("Nie udało się przetworzyć dokumentu.")
        return ProcessedDocument(
            filename=context.result_filename,
            content_type=context.result_content_type,
            data=context.result_bytes,
            findings=dict(context.findings),
        )
    finally:
        discard_job(run_id)


async def _drive(run_id: str) -> None:
    with tempfile.TemporaryDirectory(prefix="anon-run-") as tmp:
        runtime = FalaRuntime.sqlite(Path(tmp) / "run.sqlite")
        await runtime.create_run(
            Run(id=run_id, title="Anonimizacja dokumentu"),
            idempotency_key=f"{run_id}:run.create",
        )
        await runtime.instantiate_flow(
            run_id=run_id,
            flow=DOCUMENT_FLOW,
            step_inputs={CONVERT_STEP: {}},
        )
        await runtime.run_until_idle(
            worker_id=f"{run_id}:worker",
            run_id=run_id,
            max_ticks=_MAX_TICKS,
        )
        processes = await runtime.list_processes(run_id=run_id)
        failed = [process for process in processes if process.status in _FAILED_STATUSES]
        if failed:
            raise DocumentError(_error_message(failed[0]))
        incomplete = [
            process
            for process in processes
            if process.status != CarrierProcessStatus.succeeded
        ]
        if incomplete:
            raise DocumentError("Przetwarzanie dokumentu nie zostało zakończone.")


def _error_message(process: object) -> str:
    error = getattr(process, "error", None)
    if isinstance(error, dict):
        message = error.get("message")
        if message:
            return str(message)
    return "Nie udało się przetworzyć dokumentu."
