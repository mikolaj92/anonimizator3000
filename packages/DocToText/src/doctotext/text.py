from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .common import (
    TEXT_MIME_BY_EXTENSION,
    TXT_MIME,
    DocumentBytes,
    DocumentError,
    output_filename,
)
from .docx import TextSegment


@dataclass
class PlainTextDocument:
    filename: str
    content_type: str
    text: str

    @classmethod
    def open_bytes(
        cls,
        data: bytes,
        *,
        filename: str = "document.txt",
        content_type: str = TXT_MIME,
    ) -> PlainTextDocument:
        return cls(filename=filename, content_type=content_type, text=_decode_text(data))

    @property
    def segments(self) -> tuple[TextSegment, ...]:
        return (TextSegment(id="s0", text=self.text, part="text", index=0),)

    @property
    def texts(self) -> list[str]:
        return [self.text]

    def apply_texts(self, texts) -> None:
        texts = list(texts)
        if len(texts) != 1:
            raise ValueError(f"expected 1 text segment, got {len(texts)}")
        self.text = texts[0]

    def to_bytes(self) -> bytes:
        return self.text.encode("utf-8")

    def to_document_bytes(self) -> DocumentBytes:
        extension = _output_extension(self.filename)
        return DocumentBytes(
            filename=output_filename(self.filename, extension),
            content_type=TEXT_MIME_BY_EXTENSION.get(extension, TXT_MIME),
            data=self.to_bytes(),
        )


def _decode_text(data: bytes) -> str:
    encodings = ["utf-8-sig", "cp1250", "latin-1"]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.insert(0, "utf-16")

    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentError("Nie udało się odczytać pliku tekstowego.")


def _output_extension(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix in TEXT_MIME_BY_EXTENSION:
        return suffix
    return ".txt"
