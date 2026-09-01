"""Single-responsibility processing blocks driven by the Fala runtime.

Each function is a Fala ``python_function`` step: it takes the runtime's
``StepRunRequest`` and returns a small JSON-able dict that Fala records as the
step output. The heavy payload (source bytes, the parsed document, the
serialized result) never travels through the runtime -- it lives in an
in-process registry keyed by ``run_id`` so the blocks can hand it off in shared
memory. Only observable metadata (segment counts, findings) flows through Fala.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol, TypedDict
from zipfile import ZipFile

from docxtor import DOCX_MIME, PDF_MIME, DocumentError, document_to_bytes, load_document
from fala.adapters import StepRunRequest
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


class ConvertOutput(TypedDict):
    source_kind: str
    converted: bool


class LoadOutput(TypedDict):
    segment_count: int
    total_chars: int


class AnonymizeOutput(TypedDict):
    findings: dict[str, int]


class SerializeOutput(TypedDict):
    filename: str
    content_type: str
    size: int


class RedactAuthorsOutput(TypedDict):
    authors_redacted: int


@dataclass
class JobContext:
    """Shared-memory state a single job's blocks read from and write to."""

    filename: str
    content_type: str
    source_bytes: bytes
    anonymizer: SegmentAnonymizer
    max_text_chars: int
    replacement_style: str = DEFAULT_REPLACEMENT_STYLE
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


def convert(request: StepRunRequest) -> ConvertOutput:
    """Validate source metadata; Docxtor owns format-specific processing."""
    context = job_context(request.run_id)
    name = Path(context.filename or "dokument").name
    kind = _document_kind(name, context.content_type)
    return {"source_kind": kind, "converted": False}


def load(request: StepRunRequest) -> LoadOutput:
    """Load the source through Docxtor and return text-size metadata."""
    context = job_context(request.run_id)
    name = Path(context.filename or "dokument").name
    document = load_document(name, context.content_type, context.source_bytes)
    total_chars = sum(len(text) for text in document.texts)
    if total_chars == 0:
        raise DocumentError("Nie znaleziono tekstu do anonimizacji.")
    if total_chars > context.max_text_chars:
        raise DocumentError(
            f"Tekst po ekstrakcji przekracza limit {context.max_text_chars} znaków."
        )
    context.document = document
    return {"segment_count": len(document.texts), "total_chars": total_chars}


def anonymize(request: StepRunRequest) -> AnonymizeOutput:
    """Read parsed text, store anonymized text, and return finding counts."""
    context = job_context(request.run_id)
    kind = _STYLE_TO_KIND[normalize_replacement_style(context.replacement_style)]
    result = context.anonymizer.anonymize_segments(
        context.document.texts, replacement_style=kind
    )
    context.anonymized_texts = [text.replace("\xa0", " ") for text in result.texts]
    context.findings = dict(sorted(Counter(result.findings).items()))
    return {"findings": context.findings}


def serialize(request: StepRunRequest) -> SerializeOutput:
    """Read anonymized text, store result bytes, and return file metadata."""
    context = job_context(request.run_id)
    source_texts = list(context.document.texts)
    context.document.apply_texts(context.anonymized_texts)
    result = document_to_bytes(context.document, context.filename)
    context.result_filename = result.filename
    context.result_content_type = result.content_type
    context.result_bytes = result.data
    if result.content_type == DOCX_MIME:
        context.result_bytes = _remove_stale_docx_text(
            result.data, zip(source_texts, context.anonymized_texts, strict=True)
        )
    return {
        "filename": result.filename,
        "content_type": result.content_type,
        "size": len(result.data),
    }


def redact_authors(request: StepRunRequest) -> RedactAuthorsOutput:
    """Read result bytes, redact author metadata, and return the identity count."""
    context = job_context(request.run_id)
    if context.result_content_type != DOCX_MIME:
        return {"authors_redacted": 0}
    context.result_bytes, redacted = _redact_author_metadata(context.result_bytes)
    return {"authors_redacted": redacted}


def _remove_stale_docx_text(
    docx_bytes: bytes, replacements: Any
) -> bytes:
    """Remove source text left behind in hyperlink XML by Docxtor's DOCX writer."""
    changed = [
        (escape(source, quote=False).encode(), escape(target, quote=False).encode())
        for source, target in replacements
        if source != target
    ]
    if not changed:
        return docx_bytes

    with ZipFile(BytesIO(docx_bytes)) as archive:
        infos = archive.infolist()
        parts = {info.filename: archive.read(info.filename) for info in infos}

    updated_parts: dict[str, bytes] = {}
    for name, data in parts.items():
        if not _WORD_XML_RE.match(name):
            continue
        updated = data
        for source, target in changed:
            if source in updated and target in updated:
                updated = updated.replace(source, b"")
        if updated != data:
            updated_parts[name] = updated

    if not updated_parts:
        return docx_bytes
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for info in infos:
            archive.writestr(info, updated_parts.get(info.filename, parts[info.filename]))
    return output.getvalue()


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


def _document_kind(name: str, content_type: str) -> str:
    suffix = Path(name).suffix.lower()
    if content_type == DOCX_MIME or suffix == _DOCX_SUFFIX:
        return "docx"
    if content_type == PDF_MIME or suffix == _PDF_SUFFIX:
        return "pdf"
    raise DocumentError("Obsługujemy tylko pliki DOCX i PDF.")
