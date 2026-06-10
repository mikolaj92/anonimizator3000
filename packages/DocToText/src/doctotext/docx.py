from __future__ import annotations

import copy
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZipInfo

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
W_P = f"{{{W_NS}}}p"
W_T = f"{{{W_NS}}}t"
XML_SPACE = f"{{{XML_NS}}}space"

TEXT_PART_RE = re.compile(
    r"^word/(?:document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml$"
)
MARKER_RE = re.compile(
    r"<!-- doctotext:(?P<id>s\d+) -->\n(?P<text>.*?)(?=\n<!-- doctotext:s\d+ -->\n|\Z)",
    re.DOTALL,
)

ET.register_namespace("w", W_NS)
ET.register_namespace("xml", XML_NS)


@dataclass(frozen=True)
class TextSegment:
    id: str
    text: str
    part: str
    index: int


@dataclass
class _SegmentRef:
    id: str
    part: str
    paragraph_index: int
    text_nodes: list[ET.Element]


@dataclass
class _XmlPart:
    name: str
    root: ET.Element
    dirty: bool = False


class DocxDocument:
    """Editable text view over a DOCX file.

    The original DOCX archive is kept as the source of truth. Applying text edits
    changes only `w:t` text nodes inside known Word XML story parts.
    """

    def __init__(
        self,
        archive: dict[str, bytes],
        zip_infos: dict[str, ZipInfo],
        parts: dict[str, _XmlPart],
        segments: list[TextSegment],
        refs: list[_SegmentRef],
    ) -> None:
        self._archive = archive
        self._zip_infos = zip_infos
        self._parts = parts
        self._segments = segments
        self._refs = refs

    @classmethod
    def open(cls, path: str | Path) -> DocxDocument:
        path = Path(path)
        with ZipFile(path) as docx:
            return cls._open_zip(docx)

    @classmethod
    def open_bytes(cls, data: bytes) -> DocxDocument:
        with ZipFile(BytesIO(data)) as docx:
            return cls._open_zip(docx)

    @classmethod
    def _open_zip(cls, docx: ZipFile) -> DocxDocument:
        archive = {name: docx.read(name) for name in docx.namelist()}
        zip_infos = {info.filename: copy.copy(info) for info in docx.infolist()}

        parts: dict[str, _XmlPart] = {}
        segments: list[TextSegment] = []
        refs: list[_SegmentRef] = []

        for name, data in archive.items():
            if not TEXT_PART_RE.match(name):
                continue

            try:
                root = ET.fromstring(data)
            except ET.ParseError:
                continue

            part = _XmlPart(name=name, root=root)
            part_segment_index = 0
            for paragraph in root.iter(W_P):
                text_nodes = [node for node in paragraph.iter(W_T)]
                if not text_nodes:
                    continue

                text = "".join(node.text or "" for node in text_nodes)
                if text == "":
                    continue

                segment_id = f"s{len(segments)}"
                segments.append(
                    TextSegment(
                        id=segment_id,
                        text=text,
                        part=name,
                        index=part_segment_index,
                    )
                )
                refs.append(
                    _SegmentRef(
                        id=segment_id,
                        part=name,
                        paragraph_index=part_segment_index,
                        text_nodes=text_nodes,
                    )
                )
                part_segment_index += 1

            parts[name] = part

        return cls(
            archive=archive,
            zip_infos=zip_infos,
            parts=parts,
            segments=segments,
            refs=refs,
        )

    @property
    def segments(self) -> tuple[TextSegment, ...]:
        return tuple(self._segments)

    @property
    def texts(self) -> list[str]:
        return [segment.text for segment in self._segments]

    def to_markdown(self) -> str:
        blocks = []
        for segment in self._segments:
            blocks.append(f"<!-- doctotext:{segment.id} -->\n{segment.text}")
        return "\n\n".join(blocks)

    def apply_texts(self, texts: Iterable[str]) -> None:
        texts = list(texts)
        if len(texts) != len(self._segments):
            raise ValueError(f"expected {len(self._segments)} text segments, got {len(texts)}")

        for index, text in enumerate(texts):
            self._apply_segment_text(index, text)

    def apply_markdown(self, markdown: str, *, strict: bool = True) -> None:
        by_id = {
            match.group("id"): match.group("text").rstrip("\n")
            for match in MARKER_RE.finditer(markdown)
        }

        if strict:
            expected = {segment.id for segment in self._segments}
            actual = set(by_id)
            missing = sorted(expected - actual)
            unknown = sorted(actual - expected)
            if missing or unknown:
                details = []
                if missing:
                    details.append(f"missing: {', '.join(missing)}")
                if unknown:
                    details.append(f"unknown: {', '.join(unknown)}")
                raise ValueError("invalid doctotext markdown markers; " + "; ".join(details))

        for index, segment in enumerate(self._segments):
            if segment.id in by_id:
                self._apply_segment_text(index, by_id[segment.id])

    def save_docx(self, path: str | Path) -> None:
        path = Path(path)
        path.write_bytes(self.to_bytes())

    def to_bytes(self) -> bytes:
        output_bytes = BytesIO()
        with ZipFile(output_bytes, "w") as output:
            for name, data in self._archive.items():
                info = copy.copy(self._zip_infos[name])
                info.compress_type = self._zip_infos[name].compress_type

                if name in self._parts and self._parts[name].dirty:
                    data = ET.tostring(
                        self._parts[name].root,
                        encoding="utf-8",
                        xml_declaration=True,
                    )

                output.writestr(info, data)
        return output_bytes.getvalue()

    def _apply_segment_text(self, index: int, text: str) -> None:
        ref = self._refs[index]
        lengths = [len(node.text or "") for node in ref.text_nodes]

        if len(ref.text_nodes) == 1:
            chunks = [text]
        else:
            chunks = []
            offset = 0
            for length in lengths[:-1]:
                chunks.append(text[offset : offset + length])
                offset += length
            chunks.append(text[offset:])

        for node, chunk in zip(ref.text_nodes, chunks, strict=True):
            node.text = chunk
            _ensure_space_preserved(node, chunk)

        self._parts[ref.part].dirty = True
        self._segments[index] = replace(self._segments[index], text=text)


def _ensure_space_preserved(node: ET.Element, text: str) -> None:
    if text and (text[0].isspace() or text[-1].isspace()):
        node.set(XML_SPACE, "preserve")
