from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from .common import (
    DOCX_MIME,
    PDF_MIME,
    TEXT_EXTENSIONS,
    TEXT_MIME_BY_EXTENSION,
    TXT_MIME,
    DocumentError,
    DocumentKind,
)


@dataclass(frozen=True)
class DetectedDocumentType:
    filename: str
    kind: DocumentKind
    extension: str
    content_type: str
    source: str


def detect_document_type(
    filename: str,
    content_type: str,
    data: bytes,
) -> DetectedDocumentType:
    if not data:
        raise DocumentError("Pusty plik.")

    suffix = Path(filename or "").suffix.lower()
    mime = _normalize_mime(content_type)

    if _is_docx_package(data):
        return DetectedDocumentType(
            filename, DocumentKind.DOCX, ".docx", DOCX_MIME, "signature"
        )
    if _is_pdf(data):
        return DetectedDocumentType(
            filename, DocumentKind.PDF, ".pdf", PDF_MIME, "signature"
        )

    if mime == DOCX_MIME or suffix == ".docx":
        return DetectedDocumentType(
            filename, DocumentKind.DOCX, ".docx", DOCX_MIME, "metadata"
        )
    if mime == PDF_MIME or suffix == ".pdf":
        return DetectedDocumentType(
            filename, DocumentKind.PDF, ".pdf", PDF_MIME, "metadata"
        )

    if suffix in TEXT_EXTENSIONS or mime.startswith("text/"):
        return DetectedDocumentType(
            filename,
            DocumentKind.TEXT,
            _text_extension(suffix),
            _text_content_type(suffix, content_type),
            "metadata",
        )

    if _looks_like_text(data):
        return DetectedDocumentType(
            filename, DocumentKind.TEXT, ".txt", TXT_MIME, "content"
        )

    raise DocumentError("Nieobsługiwany typ dokumentu.")


def _normalize_mime(content_type: str) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _is_pdf(data: bytes) -> bool:
    return data.startswith(b"%PDF-")


def _is_docx_package(data: bytes) -> bool:
    if not data.startswith(b"PK"):
        return False

    try:
        with ZipFile(BytesIO(data)) as archive:
            names = set(archive.namelist())
    except BadZipFile:
        return False

    return "[Content_Types].xml" in names and "word/document.xml" in names


def _text_extension(suffix: str) -> str:
    if suffix in TEXT_MIME_BY_EXTENSION:
        return suffix
    return ".txt"


def _text_content_type(suffix: str, content_type: str) -> str:
    if suffix in TEXT_MIME_BY_EXTENSION:
        return TEXT_MIME_BY_EXTENSION[suffix]
    if content_type and _normalize_mime(content_type).startswith("text/"):
        return content_type
    return TXT_MIME


def _looks_like_text(data: bytes) -> bool:
    sample = data[:4096]
    if b"\x00" in sample:
        return False

    for encoding in ("utf-8-sig", "cp1250"):
        try:
            text = sample.decode(encoding)
        except UnicodeDecodeError:
            continue
        return _has_text_shape(text)

    text = sample.decode("latin-1", errors="ignore")
    return _has_text_shape(text, min_printable_ratio=0.95)


def _has_text_shape(text: str, *, min_printable_ratio: float = 0.9) -> bool:
    if not text:
        return False

    printable = 0
    for character in text:
        if character.isprintable() or character in "\r\n\t":
            printable += 1

    return printable / len(text) >= min_printable_ratio
