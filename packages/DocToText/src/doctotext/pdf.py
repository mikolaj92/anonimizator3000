from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from textwrap import wrap

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from .common import PDF_MIME, DocumentBytes, DocumentError, output_filename
from .docx import TextSegment


class PdfExtractionMode(StrEnum):
    TEXT_LAYER = "text_layer"


@dataclass
class PdfDocument:
    filename: str
    pages: list[str]
    extraction_mode: PdfExtractionMode = PdfExtractionMode.TEXT_LAYER

    @classmethod
    def open_bytes(cls, data: bytes, *, filename: str = "document.pdf") -> PdfDocument:
        try:
            reader = PdfReader(BytesIO(data))
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as error:
            raise DocumentError("Nie udało się odczytać PDF.") from error

        if not any(page.strip() for page in pages):
            raise DocumentError("PDF nie ma warstwy tekstowej. Ten plik wymaga OCR.")
        return cls(filename=filename, pages=pages)

    @property
    def segments(self) -> tuple[TextSegment, ...]:
        return tuple(
            TextSegment(id=f"p{index}", text=text, part=f"page:{index}", index=index)
            for index, text in enumerate(self.pages)
            if text.strip()
        )

    @property
    def texts(self) -> list[str]:
        return [segment.text for segment in self.segments]

    def apply_texts(self, texts) -> None:
        texts = list(texts)
        segment_indexes = [segment.index for segment in self.segments]
        if len(texts) != len(segment_indexes):
            raise ValueError(
                f"expected {len(segment_indexes)} text segments, got {len(texts)}"
            )
        for index, text in zip(segment_indexes, texts, strict=True):
            self.pages[index] = text

    def to_bytes(self) -> bytes:
        output = BytesIO()
        pdf = canvas.Canvas(output, pagesize=A4)
        width, height = A4
        left = 48
        top = height - 56
        line_height = 14
        max_chars = 96

        pdf.setTitle("DocToText output")
        pdf.setFont("Helvetica", 10)

        for page_index, page_text in enumerate(self.pages):
            if page_index > 0:
                pdf.showPage()
                pdf.setFont("Helvetica", 10)
            y = top
            for source_line in page_text.splitlines() or [""]:
                lines = wrap(source_line, width=max_chars, replace_whitespace=False) or [""]
                for line in lines:
                    if y < 48:
                        pdf.showPage()
                        pdf.setFont("Helvetica", 10)
                        y = top
                    pdf.drawString(left, y, _pdf_safe_text(line))
                    y -= line_height

        pdf.save()
        return output.getvalue()

    def to_document_bytes(self) -> DocumentBytes:
        return DocumentBytes(
            filename=output_filename(self.filename, "pdf"),
            content_type=PDF_MIME,
            data=self.to_bytes(),
        )


def _pdf_safe_text(text: str) -> str:
    return text.encode("latin-1", errors="replace").decode("latin-1")
