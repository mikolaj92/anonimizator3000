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
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol
from zipfile import ZipFile

from doctotext import DOCX_MIME, PDF_MIME, DocumentError, document_to_bytes, load_document
from posejdon import ReplacementKind

from anonimizator3000.config import DEFAULT_REPLACEMENT_STYLE, normalize_replacement_style

_STYLE_TO_KIND: dict[str, ReplacementKind] = {
    "labels": ReplacementKind.CATEGORY_PLACEHOLDER,
    "mask": ReplacementKind.MASK,
}
_DOCX_SUFFIX = ".docx"
_PDF_SUFFIX = ".pdf"

# Author identity lives in DOCX metadata, not in the `w:t` text the extractor
# sees: comment authors (word/comments.xml), tracked-change authors
# (w:ins/w:del/... in the story parts), word/people.xml, and the document-level
# author fields in docProps. Redact all of those too.
_WORD_XML_RE = re.compile(r"^word/.*\.xml$")
_AUTHOR_RE = re.compile(r'(\b\w+:author=")([^"]*)(")')
_INITIALS_RE = re.compile(r'(\b\w+:initials=")([^"]*)(")')
_CORE_PROPS_PART = "docProps/core.xml"
_APP_PROPS_PART = "docProps/app.xml"
_CREATOR_RE = re.compile(r"(<dc:creator[^>]*>)([^<]*)(</dc:creator>)")
_LAST_MODIFIED_RE = re.compile(r"(<cp:lastModifiedBy[^>]*>)([^<]*)(</cp:lastModifiedBy>)")
_MANAGER_RE = re.compile(r"(<Manager[^>]*>)([^<]*)(</Manager>)")
_COMPANY_RE = re.compile(r"(<Company[^>]*>)([^<]*)(</Company>)")


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


def redact_authors(request: Any) -> dict[str, Any]:
    """Pseudonymize author identity in DOCX metadata (comments, revisions, people)."""
    context = job_context(request.run_id)
    context.result_bytes, redacted = _redact_author_metadata(context.result_bytes)
    return {"authors_redacted": redacted}


def _redact_author_metadata(docx_bytes: bytes) -> tuple[bytes, int]:
    with ZipFile(BytesIO(docx_bytes)) as archive:
        infos = archive.infolist()
        parts = {info.filename: archive.read(info.filename) for info in infos}

    aliases: dict[str, str] = {}

    def _pseudonym(original: str) -> str:
        if original == "":
            return ""
        alias = aliases.get(original)
        if alias is None:
            alias = f"Autor {len(aliases) + 1}"
            aliases[original] = alias
        return alias

    def _replace_value(match: re.Match[str]) -> str:
        return f"{match.group(1)}{_pseudonym(match.group(2))}{match.group(3)}"

    def _clear_value(match: re.Match[str]) -> str:
        return f"{match.group(1)}{match.group(3)}"

    changed: dict[str, bytes] = {}
    for name, data in parts.items():
        if not (_WORD_XML_RE.match(name) or name in (_CORE_PROPS_PART, _APP_PROPS_PART)):
            continue
        text = data.decode("utf-8")
        updated = text
        if _WORD_XML_RE.match(name):
            updated = _AUTHOR_RE.sub(_replace_value, updated)
            updated = _INITIALS_RE.sub(_clear_value, updated)
        elif name == _CORE_PROPS_PART:
            updated = _CREATOR_RE.sub(_replace_value, updated)
            updated = _LAST_MODIFIED_RE.sub(_replace_value, updated)
        elif name == _APP_PROPS_PART:
            updated = _MANAGER_RE.sub(_replace_value, updated)
            updated = _COMPANY_RE.sub(_clear_value, updated)
        if updated != text:
            changed[name] = updated.encode("utf-8")

    if not changed:
        return docx_bytes, 0

    output = BytesIO()
    with ZipFile(output, "w") as out:
        for info in infos:
            out.writestr(info, changed.get(info.filename, parts[info.filename]))
    return output.getvalue(), len(aliases)


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
