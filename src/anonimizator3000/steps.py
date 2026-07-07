"""Single-responsibility processing blocks driven by the Fala runtime.

Each function is a Fala ``python_function`` step: it takes the runtime's
``StepRunRequest`` and returns a small JSON-able dict that Fala records as the
step output. The heavy payload (source bytes, the parsed document, the
serialized result) never travels through the runtime -- it lives in an
in-process registry keyed by ``run_id`` so the blocks can hand it off in shared
memory. Only observable metadata (segment counts, findings) flows through Fala.
"""

from __future__ import annotations

import contextlib
import logging
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from doctotext import DOCX_MIME, PDF_MIME, DocumentError, document_to_bytes, load_document
from posejdon import ReplacementKind

from anonimizator3000.config import DEFAULT_REPLACEMENT_STYLE, normalize_replacement_style

_STYLE_TO_KIND: dict[str, ReplacementKind] = {
    "labels": ReplacementKind.CATEGORY_PLACEHOLDER,
    "mask": ReplacementKind.MASK,
}
_DOCX_SUFFIX = ".docx"
_PDF_SUFFIX = ".pdf"


class SegmentAnonymizer(Protocol):
    def anonymize_segments(
        self, texts: list[str], *, replacement_style: ReplacementKind
    ) -> Any: ...


@dataclass
class JobContext:
    """Shared-memory state a single job's blocks read from and write to."""

    filename: str
    content_type: str
    source_bytes: bytes
    anonymizer: SegmentAnonymizer
    max_text_chars: int
    replacement_style: str = DEFAULT_REPLACEMENT_STYLE
    docx_filename: str | None = None
    docx_bytes: bytes | None = None
    document: Any | None = None
    anonymized_texts: list[str] | None = None
    result_filename: str | None = None
    result_content_type: str | None = None
    result_bytes: bytes | None = None
    findings: dict[str, int] = field(default_factory=dict)


_REGISTRY: dict[str, JobContext] = {}


def register_job(run_id: str, context: JobContext) -> None:
    _REGISTRY[run_id] = context


def job_context(run_id: str) -> JobContext:
    try:
        return _REGISTRY[run_id]
    except KeyError as error:
        raise DocumentError("Kontekst zadania wygasł.") from error


def discard_job(run_id: str) -> None:
    _REGISTRY.pop(run_id, None)


def convert(request: Any) -> dict[str, Any]:
    """Normalize the input to DOCX bytes: passthrough for DOCX, pdf2docx for PDF."""
    context = job_context(request.run_id)
    name = Path(context.filename or "dokument").name
    kind = _document_kind(name, context.content_type)
    if kind == "docx":
        context.docx_filename = name or f"dokument{_DOCX_SUFFIX}"
        context.docx_bytes = context.source_bytes
        return {"source_kind": "docx", "converted": False}
    context.docx_filename = f"{Path(name).stem or 'dokument'}{_DOCX_SUFFIX}"
    context.docx_bytes = _pdf_to_docx(context.source_bytes, request.work_dir)
    return {"source_kind": "pdf", "converted": True}


def load(request: Any) -> dict[str, Any]:
    """Extract the DOCX text segments and enforce the size limits."""
    context = job_context(request.run_id)
    document = load_document(context.docx_filename, DOCX_MIME, context.docx_bytes)
    total_chars = sum(len(text) for text in document.texts)
    if total_chars == 0:
        raise DocumentError("Nie znaleziono tekstu do anonimizacji.")
    if total_chars > context.max_text_chars:
        raise DocumentError(
            f"Tekst po ekstrakcji przekracza limit {context.max_text_chars} znaków."
        )
    context.document = document
    return {"segment_count": len(document.texts), "total_chars": total_chars}


def anonymize(request: Any) -> dict[str, Any]:
    """Detect and replace PII across the extracted segments (Posejdon)."""
    context = job_context(request.run_id)
    kind = _STYLE_TO_KIND[normalize_replacement_style(context.replacement_style)]
    result = context.anonymizer.anonymize_segments(
        context.document.texts, replacement_style=kind
    )
    context.anonymized_texts = [text.replace("\xa0", " ") for text in result.texts]
    context.findings = dict(sorted(Counter(result.findings).items()))
    return {"findings": context.findings}


def serialize(request: Any) -> dict[str, Any]:
    """Write the anonymized segments back into the DOCX and produce bytes."""
    context = job_context(request.run_id)
    context.document.apply_texts(context.anonymized_texts)
    result = document_to_bytes(context.document, context.docx_filename)
    context.result_filename = result.filename
    context.result_content_type = result.content_type
    context.result_bytes = result.data
    return {
        "filename": result.filename,
        "content_type": result.content_type,
        "size": len(result.data),
    }


@contextlib.contextmanager
def _quiet_root_logging():
    """Silence pdf2docx's page-by-page INFO banner (it logs on the root logger)."""
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.WARNING)
    try:
        yield
    finally:
        root.setLevel(previous)


def _document_kind(name: str, content_type: str) -> str:
    suffix = Path(name).suffix.lower()
    if content_type == DOCX_MIME or suffix == _DOCX_SUFFIX:
        return "docx"
    if content_type == PDF_MIME or suffix == _PDF_SUFFIX:
        return "pdf"
    raise DocumentError("Obsługujemy tylko pliki DOCX i PDF.")


def _pdf_to_docx(pdf_bytes: bytes, work_dir: Any | None) -> bytes:
    from pdf2docx import Converter

    root = Path(work_dir) if work_dir is not None else Path(tempfile.mkdtemp(prefix="anon-pdf-"))
    docx_path = root / "converted.docx"
    converter = Converter(stream=pdf_bytes)
    try:
        with _quiet_root_logging():
            converter.convert(str(docx_path))
    finally:
        converter.close()
    data = docx_path.read_bytes()
    if work_dir is None:
        with contextlib.suppress(OSError):
            docx_path.unlink()
            root.rmdir()
    return data
