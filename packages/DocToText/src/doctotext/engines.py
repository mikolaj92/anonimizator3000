from __future__ import annotations

from typing import Protocol

from .common import DocumentError, DocumentKind
from .detection import DetectedDocumentType
from .docx import DocxDocument
from .pdf import PdfDocument
from .text import PlainTextDocument


class DocumentEngine(Protocol):
    kind: DocumentKind

    def open(self, detection: DetectedDocumentType, data: bytes): ...


class PlainTextEngine:
    kind = DocumentKind.TEXT

    def open(self, detection: DetectedDocumentType, data: bytes) -> PlainTextDocument:
        return PlainTextDocument.open_bytes(
            data,
            filename=detection.filename or f"document{detection.extension}",
            content_type=detection.content_type,
        )


class DocxEngine:
    kind = DocumentKind.DOCX

    def open(self, detection: DetectedDocumentType, data: bytes) -> DocxDocument:
        try:
            return DocxDocument.open_bytes(data)
        except Exception as error:
            raise DocumentError("Nie udało się odczytać DOCX.") from error


class PdfEngine:
    kind = DocumentKind.PDF

    def open(self, detection: DetectedDocumentType, data: bytes) -> PdfDocument:
        return PdfDocument.open_bytes(
            data,
            filename=detection.filename or f"document{detection.extension}",
        )


ENGINES: tuple[DocumentEngine, ...] = (
    DocxEngine(),
    PdfEngine(),
    PlainTextEngine(),
)


def engine_for(kind: DocumentKind) -> DocumentEngine:
    for engine in ENGINES:
        if engine.kind == kind:
            return engine
    raise LookupError(f"missing engine for document kind: {kind.value}")
