from io import BytesIO

from doctotext import DOCX_MIME, PDF_MIME
from docx import Document
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from anonimizator3000.processor import DocumentProcessor


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
