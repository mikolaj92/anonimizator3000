from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from test_docx import write_docx

from doctotext import (
    DOCX_MIME,
    MD_MIME,
    PDF_MIME,
    TXT_MIME,
    DocumentError,
    DocumentKind,
    PdfExtractionMode,
    detect_document_type,
    document_to_bytes,
    load_document,
)


def _pdf_bytes(text: str) -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    pdf.drawString(48, 760, text)
    pdf.save()
    return output.getvalue()


def _blank_pdf_bytes() -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    pdf.showPage()
    pdf.save()
    return output.getvalue()


def test_detects_docx_from_bytes_before_metadata(tmp_path: Path) -> None:
    input_path = tmp_path / "input.docx"
    write_docx(input_path)
    data = input_path.read_bytes()

    detection = detect_document_type("upload.bin", "application/octet-stream", data)
    document = load_document("upload.bin", "application/octet-stream", data)

    assert detection.kind == DocumentKind.DOCX
    assert detection.source == "signature"
    assert document.texts == ["Hello world", "Second paragraph", "Header text"]


def test_detects_pdf_from_bytes_before_metadata() -> None:
    data = _pdf_bytes("Jan Kowalski")

    detection = detect_document_type("upload.bin", "application/octet-stream", data)
    document = load_document("upload.bin", "application/octet-stream", data)

    assert detection.kind == DocumentKind.PDF
    assert detection.source == "signature"
    assert document.texts == ["Jan Kowalski\n"]


def test_rejects_unknown_binary_document() -> None:
    with pytest.raises(DocumentError, match="Nieobsługiwany typ dokumentu"):
        load_document("upload.bin", "application/octet-stream", b"\x00\x01\x02\x03")


def test_load_docx_document_and_write_docx_bytes(tmp_path: Path) -> None:
    input_path = tmp_path / "input.docx"
    write_docx(input_path)

    document = load_document("input.docx", DOCX_MIME, input_path.read_bytes())
    document.apply_texts(["One", "Two", "Three"])
    output = document_to_bytes(document, "input.docx")

    assert output.filename == "input.anonimizowany.docx"
    assert output.content_type == DOCX_MIME
    with ZipFile(BytesIO(output.data)) as docx:
        assert "word/document.xml" in docx.namelist()


def test_load_pdf_document_and_write_pdf_bytes() -> None:
    document = load_document("input.pdf", PDF_MIME, _pdf_bytes("Jan Kowalski"))

    assert document.extraction_mode == PdfExtractionMode.TEXT_LAYER
    assert document.texts == ["Jan Kowalski\n"]
    document.apply_texts(["<PERSON>"])
    output = document_to_bytes(document, "input.pdf")

    assert output.filename == "input.anonimizowany.pdf"
    assert output.content_type == PDF_MIME
    assert output.data.startswith(b"%PDF")
    output_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(output.data)).pages
    )
    assert "<PERSON>" in output_text
    assert "Jan Kowalski" not in output_text


def test_pdf_without_text_layer_requires_ocr() -> None:
    with pytest.raises(DocumentError, match="wymaga OCR"):
        load_document("scan.pdf", PDF_MIME, _blank_pdf_bytes())


def test_load_text_document_and_write_txt_bytes() -> None:
    document = load_document("input.txt", "text/plain", "Zażółć".encode("cp1250"))

    assert document.texts == ["Zażółć"]
    document.apply_texts(["<TEXT>"])
    output = document_to_bytes(document, "input.txt")

    assert output.filename == "input.anonimizowany.txt"
    assert output.content_type == TXT_MIME
    assert output.data == b"<TEXT>"


def test_load_markdown_document_and_write_markdown_bytes() -> None:
    document = load_document("notes.md", "", b"# Title\n\nOld")

    assert document.texts == ["# Title\n\nOld"]
    document.apply_texts(["# Title\n\nNew"])
    output = document_to_bytes(document, "notes.md")

    assert output.filename == "notes.anonimizowany.md"
    assert output.content_type == MD_MIME
    assert output.data == b"# Title\n\nNew"
