# DocToText

Document text round-trip helper.

Goal: expose editable text segments from documents, then write changed text back as document bytes.

Current support:

- DOCX -> DOCX, editing WordprocessingML text nodes in memory
- PDF -> PDF, extracting text and rendering a new text PDF in memory
- text files -> same text format when known (`.txt`, `.md`, `.csv`, etc.)

`load_document` first detects document kind from bytes, then MIME/extension:

- `DocumentKind.DOCX` -> `DocxDocument`
- `DocumentKind.PDF` -> `PdfDocument`
- `DocumentKind.TEXT` -> `PlainTextDocument`

Each document exposes the same editing surface:

- `texts`
- `segments`
- `apply_texts(...)`
- `to_document_bytes()` through `document_to_bytes(...)`

## Basic usage

```python
from doctotext import DOCX_MIME, document_to_bytes, load_document

doc = load_document("input.docx", DOCX_MIME, input_bytes)

texts = doc.texts
updated = [
    text.replace("old", "new")
    for text in texts
]

doc.apply_texts(updated)
output = document_to_bytes(doc, "input.docx")
```

## Type detection

```python
from doctotext import DocumentKind, detect_document_type

detection = detect_document_type("upload.bin", "application/octet-stream", input_bytes)
assert detection.kind in {DocumentKind.DOCX, DocumentKind.PDF, DocumentKind.TEXT}
```

## Markdown bridge

```python
from doctotext import DocxDocument

doc = DocxDocument.open("input.docx")
markdown = doc.to_markdown()

# Send markdown to user or LLM. Keep doctotext markers intact.
edited_markdown = markdown.replace("old", "new")

doc.apply_markdown(edited_markdown)
doc.save_docx("output.docx")
```

## Limits

- Preserves original archive entries and edits only selected Word XML parts.
- Rewrites edited XML parts, so output is not byte-identical.
- New text inherits formatting by original text-node spans. If replacement length changes a lot, style boundaries can move.
- Layout can change if text length changes.
- Images, tables, numbering, headers, footers, footnotes, comments, and most structure stay untouched unless their text nodes are edited.
- PDF output is a new text PDF, not original visual layout with redaction overlays.
- PDF input must have a text layer. Scanned/image-only PDF currently returns a controlled OCR-required error.
