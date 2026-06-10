from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from doctotext import DocxDocument

DOC_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:r><w:rPr><w:b/></w:rPr><w:t>Hello</w:t></w:r>
      <w:r><w:rPr><w:i/></w:rPr><w:t xml:space="preserve"> world</w:t></w:r>
    </w:p>
    <w:p>
      <w:r><w:t>Second paragraph</w:t></w:r>
    </w:p>
  </w:body>
</w:document>
"""

HEADER_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p><w:r><w:t>Header text</w:t></w:r></w:p>
</w:hdr>
"""

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/header1.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
</Types>
"""

RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="word/document.xml"/>
</Relationships>
"""


def write_docx(path: Path) -> None:
    with ZipFile(path, "w") as docx:
        docx.writestr("[Content_Types].xml", CONTENT_TYPES)
        docx.writestr("_rels/.rels", RELS)
        docx.writestr("word/document.xml", DOC_XML)
        docx.writestr("word/header1.xml", HEADER_XML)


def read_part(path: Path, name: str) -> str:
    with ZipFile(path) as docx:
        return docx.read(name).decode("utf-8")


def test_extracts_docx_text_segments(tmp_path: Path) -> None:
    input_path = tmp_path / "input.docx"
    write_docx(input_path)

    doc = DocxDocument.open(input_path)

    assert doc.texts == ["Hello world", "Second paragraph", "Header text"]
    assert [segment.part for segment in doc.segments] == [
        "word/document.xml",
        "word/document.xml",
        "word/header1.xml",
    ]


def test_applies_texts_without_removing_run_formatting(tmp_path: Path) -> None:
    input_path = tmp_path / "input.docx"
    output_path = tmp_path / "output.docx"
    write_docx(input_path)

    doc = DocxDocument.open(input_path)
    doc.apply_texts(["Hello there", "Changed paragraph", "Changed header"])
    doc.save_docx(output_path)

    document_xml = read_part(output_path, "word/document.xml")
    header_xml = read_part(output_path, "word/header1.xml")

    assert "<w:b" in document_xml
    assert "<w:i" in document_xml
    assert "Changed paragraph" in document_xml
    assert "Changed header" in header_xml

    output_doc = DocxDocument.open(output_path)
    assert output_doc.texts == ["Hello there", "Changed paragraph", "Changed header"]


def test_docx_round_trip_in_memory(tmp_path: Path) -> None:
    input_path = tmp_path / "input.docx"
    write_docx(input_path)

    doc = DocxDocument.open_bytes(input_path.read_bytes())
    doc.apply_texts(["Hello bytes", "Second bytes", "Header bytes"])
    output_doc = DocxDocument.open_bytes(doc.to_bytes())

    assert output_doc.texts == ["Hello bytes", "Second bytes", "Header bytes"]


def test_applies_markdown_with_segment_markers(tmp_path: Path) -> None:
    input_path = tmp_path / "input.docx"
    output_path = tmp_path / "output.docx"
    write_docx(input_path)

    doc = DocxDocument.open(input_path)
    markdown = doc.to_markdown()
    doc.apply_markdown(markdown.replace("Second paragraph", "Second changed"))
    doc.save_docx(output_path)

    document_xml = read_part(output_path, "word/document.xml")
    assert "Second changed" in document_xml


def test_rejects_wrong_number_of_texts(tmp_path: Path) -> None:
    input_path = tmp_path / "input.docx"
    write_docx(input_path)

    doc = DocxDocument.open(input_path)

    with pytest.raises(ValueError, match="expected 3 text segments"):
        doc.apply_texts(["only one"])
