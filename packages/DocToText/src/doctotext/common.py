from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from re import sub

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME = "application/pdf"
TXT_MIME = "text/plain; charset=utf-8"
MD_MIME = "text/markdown; charset=utf-8"

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".log",
    ".rtf",
}

TEXT_MIME_BY_EXTENSION = {
    ".txt": TXT_MIME,
    ".md": MD_MIME,
    ".markdown": MD_MIME,
    ".csv": "text/csv; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".log": TXT_MIME,
    ".rtf": "application/rtf; charset=utf-8",
}


class DocumentKind(StrEnum):
    TEXT = "text"
    DOCX = "docx"
    PDF = "pdf"


class DocumentError(ValueError):
    """Raised when document text cannot be read or replaced safely."""


@dataclass(frozen=True)
class DocumentBytes:
    filename: str
    content_type: str
    data: bytes


def output_filename(filename: str, extension: str) -> str:
    stem = Path(filename or "document").stem
    safe = sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return f"{safe or 'document'}.anonimizowany.{extension.lstrip('.')}"
