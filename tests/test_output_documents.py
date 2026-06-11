from io import BytesIO
from pathlib import Path

import fitz
import pytest
from doctotext import DOCX_MIME, PDF_MIME
from docx import Document
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from anonimizator3000.processor import DocumentProcessor

UNICODE_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/local/share/fonts/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
)


def _docx_bytes(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _pdf_bytes(text: str) -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    pdf.drawString(48, 760, text)
    pdf.save()
    return output.getvalue()


def _unicode_pdf_bytes(*pages: str) -> bytes:
    font_path = next((path for path in UNICODE_FONT_CANDIDATES if Path(path).exists()), None)
    if font_path is None:
        pytest.skip("Unicode font unavailable for PDF fixture")

    pdf = fitz.open()
    for text in pages:
        page = pdf.new_page(width=595, height=842)
        page.insert_textbox(
            fitz.Rect(48, 48, 547, 794),
            text,
            fontfile=font_path,
            fontname="anonimizatorunicode",
            fontsize=12,
        )
    return pdf.tobytes()


def _fitz_pdf_text(data: bytes) -> str:
    pdf = fitz.open(stream=data, filetype="pdf")
    return "\n".join(page.get_text("text") or "" for page in pdf).replace("\xa0", " ")


def test_processor_returns_anonymized_docx_document() -> None:
    processor = DocumentProcessor(max_text_chars=10_000)

    result = processor("sample.docx", DOCX_MIME, _docx_bytes("Jan Kowalski, PESEL 44051401359"))

    assert result.filename == "sample.anonimizowany.docx"
    assert result.content_type == DOCX_MIME
    assert result.data.startswith(b"PK")
    assert result.findings["PERSON"] == 1
    assert result.findings["PESEL"] == 1

    output_docx = Document(BytesIO(result.data))
    output_text = "\n".join(paragraph.text for paragraph in output_docx.paragraphs)
    assert "Jan Kowalski" not in output_text
    assert "44051401359" not in output_text


def test_pdf_processor_preserves_polish_text_and_page_count() -> None:
    processor = DocumentProcessor(max_text_chars=10_000)
    data = _unicode_pdf_bytes(
        "Dane nie są fikcyjne. Zażółć gęślą jaźń. Jan Kowalski PESEL 44051401359",
        "Druga strona bez danych.",
    )

    result = processor("sample.pdf", PDF_MIME, data)
    output_pdf = fitz.open(stream=result.data, filetype="pdf")
    output_text = _fitz_pdf_text(result.data)

    assert output_pdf.page_count == 2
    assert "Dane nie są fikcyjne" in output_text
    assert "Zażółć gęślą jaźń" in output_text
    assert "Jan Kowalski" not in output_text
    assert "44051401359" not in output_text


def test_pdf_processor_redacts_broken_bank_account_city_and_street() -> None:
    processor = DocumentProcessor(max_text_chars=10_000)
    data = _unicode_pdf_bytes(
        "Dane obejmują rachu\n"
        "41 1140 2004 0000 3102 1234 5678 oraz korespondencję z Łódźa "
        "i przekazania kluczy w Wrocławu przy Piotrkowskiej."
    )

    result = processor("sample.pdf", PDF_MIME, data)
    output_text = _fitz_pdf_text(result.data)

    assert "41 1140 2004 0000 3102 1234 5678" not in output_text
    assert "Łódźa" not in output_text
    assert "Wrocławu" not in output_text
    assert "Piotrkowskiej" not in output_text


def test_text_input_returns_anonymized_txt_document() -> None:
    processor = DocumentProcessor(max_text_chars=10_000)

    result = processor("sample.txt", "text/plain", b"Anna Nowak email anna@example.com")

    assert result.filename == "sample.anonimizowany.txt"
    assert result.content_type == "text/plain; charset=utf-8"
    assert b"Anna Nowak" not in result.data
    assert b"anna@example.com" not in result.data


def test_pdf_input_returns_anonymized_pdf_document() -> None:
    processor = DocumentProcessor(max_text_chars=10_000)

    result = processor("sample.pdf", PDF_MIME, _pdf_bytes("Jan Kowalski PESEL 44051401359"))

    assert result.filename == "sample.anonimizowany.pdf"
    assert result.content_type == PDF_MIME
    assert result.data.startswith(b"%PDF")
    output_pdf = PdfReader(BytesIO(result.data))
    output_text = "\n".join(page.extract_text() or "" for page in output_pdf.pages)
    assert "Jan Kowalski" not in output_text
    assert "44051401359" not in output_text


def test_processor_respects_docx_text_limit() -> None:
    processor = DocumentProcessor(max_text_chars=3)

    try:
        processor("sample.docx", DOCX_MIME, _docx_bytes("abcdef"))
    except ValueError as error:
        assert "przekracza limit" in str(error)
    else:
        raise AssertionError("Expected ValueError")
