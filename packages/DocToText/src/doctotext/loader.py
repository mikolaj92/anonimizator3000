from __future__ import annotations

from .common import (
    DOCX_MIME,
    DocumentBytes,
    output_filename,
)
from .detection import detect_document_type
from .engines import engine_for


def load_document(filename: str, content_type: str, data: bytes):
    detection = detect_document_type(filename, content_type, data)
    return engine_for(detection.kind).open(detection, data)


def document_to_bytes(document, filename: str):
    if hasattr(document, "to_document_bytes"):
        return document.to_document_bytes()

    return DocumentBytes(
        filename=output_filename(filename, "docx"),
        content_type=DOCX_MIME,
        data=document.to_bytes(),
    )
