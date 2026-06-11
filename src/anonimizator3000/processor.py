from collections import Counter
from dataclasses import dataclass

from doctotext import DocumentError, document_to_bytes, load_document
from posejdon import TextAnonymizer


@dataclass(frozen=True)
class ProcessedDocument:
    filename: str
    content_type: str
    data: bytes
    findings: dict[str, int]


class DocumentProcessor:
    def __init__(
        self,
        *,
        max_text_chars: int,
        gliner_enabled: bool = False,
        gliner_model: str = "urchade/gliner_multi_pii-v1",
        gliner_threshold: float = 0.45,
    ) -> None:
        self._max_text_chars = max_text_chars
        self._anonymizer = TextAnonymizer(
            gliner_enabled=gliner_enabled,
            gliner_model=gliner_model,
            gliner_threshold=gliner_threshold,
        )

    def __call__(self, filename: str, content_type: str, data: bytes) -> ProcessedDocument:
        document = load_document(filename, content_type, data)
        texts = document.texts
        total_chars = sum(len(text) for text in texts)
        if total_chars == 0:
            raise DocumentError("Nie znaleziono tekstu do anonimizacji.")
        if total_chars > self._max_text_chars:
            raise DocumentError(
                f"Tekst po ekstrakcji przekracza limit {self._max_text_chars} znaków."
            )

        findings: Counter[str] = Counter()
        if hasattr(self._anonymizer, "anonymize_segments"):
            anonymized = self._anonymizer.anonymize_segments(texts)
            anonymized_texts = anonymized.texts
            findings.update(anonymized.findings)
        else:
            anonymized_texts = []
            for text in texts:
                if not text.strip():
                    anonymized_texts.append(text)
                    continue
                anonymized = self._anonymizer.anonymize(text)
                anonymized_texts.append(anonymized.text)
                findings.update(anonymized.findings)

        document.apply_texts(anonymized_texts)
        result = document_to_bytes(document, filename)
        return ProcessedDocument(
            filename=result.filename,
            content_type=result.content_type,
            data=result.data,
            findings=dict(sorted(findings.items())),
        )
