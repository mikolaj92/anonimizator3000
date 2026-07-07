import time
from datetime import date
from io import BytesIO
from zipfile import ZipFile

import pytest
from doctotext import DOCX_MIME
from docx import Document
from fastapi.testclient import TestClient

from anonimizator3000.main import _attachment_header, app
from anonimizator3000.upload import UploadError, read_multipart_document


def _docx_bytes(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _docx_bytes_with_hyperlink(text: str) -> bytes:
    """A DOCX whose body references the ``r:`` namespace, like real Word files."""
    with ZipFile(BytesIO(_docx_bytes(text))) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    document_xml = parts["word/document.xml"].decode()
    parts["word/document.xml"] = document_xml.replace(
        f"<w:r><w:t>{text}</w:t></w:r>",
        f'<w:hyperlink r:id="rId999"><w:r><w:t>{text}</w:t></w:r></w:hyperlink>',
    ).encode()
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return output.getvalue()


def test_index_does_not_show_removed_header_copy() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'aria-label="Informacje o prywatności"' in response.text
    assert "Dokument nie jest zapisywany na dysku" in response.text
    assert "wynik też wygasa" in response.text
    assert "Lokalnie. Bez zapisu." not in response.text
    assert "Anonimizator3000" not in response.text
    assert "in-memory" not in response.text
    assert "Gotowy dokument pojawi się tutaj." not in response.text
    assert "cdn.jsdelivr.net" not in response.text
    assert "unpkg.com" not in response.text
    assert "/static/basecoat/basecoat.css" in response.text
    assert "/static/basecoat/basecoat.js" in response.text
    assert "/static/htmx.min.js" in response.text


def test_docx_upload_poll_and_download_flow_returns_docx() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/jobs",
            files={
                "document": (
                    "sample.docx",
                    _docx_bytes("Jan Kowalski, PESEL 44051401359, email jan@example.com"),
                    DOCX_MIME,
                )
            },
        )

        assert response.status_code == 200
        assert 'role="progressbar"' in response.text
        job_id = response.text.split("/jobs/", 1)[1].split('"', 1)[0]

        for _ in range(50):
            status_response = client.get(f"/jobs/{job_id}")
            assert status_response.status_code == 200
            if "Gotowe" in status_response.text:
                assert "textarea" not in status_response.text
                assert "Pobierz" in status_response.text
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Job did not finish")

        download = client.get(f"/jobs/{job_id}/download")

        assert download.status_code == 200
        assert download.headers["content-type"] == DOCX_MIME
        downloaded = Document(BytesIO(download.content))
        text = "\n".join(paragraph.text for paragraph in downloaded.paragraphs)
        assert "Jan Kowalski" not in text
        assert "44051401359" not in text
        assert "jan@example.com" not in text


def test_docx_download_keeps_root_namespace_declarations() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/jobs",
            files={
                "document": (
                    "sample.docx",
                    _docx_bytes_with_hyperlink("Jan Kowalski"),
                    DOCX_MIME,
                )
            },
        )
        job_id = response.text.split("/jobs/", 1)[1].split('"', 1)[0]

        for _ in range(50):
            status = client.get(f"/jobs/{job_id}")
            if "Gotowe" in status.text:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Job did not finish")

        download = client.get(f"/jobs/{job_id}/download")

    assert download.headers["content-type"] == DOCX_MIME
    document_xml = ZipFile(BytesIO(download.content)).read("word/document.xml").decode()
    start = document_xml.index("<w:document")
    root = document_xml[start : document_xml.index(">", start) + 1]
    # mc:Ignorable references these prefixes; dropping their xmlns breaks Word.
    assert "xmlns:w14=" in root
    assert "xmlns:wp14=" in root
    # The file must still open as a valid DOCX.
    assert "Jan Kowalski" not in "".join(
        p.text for p in Document(BytesIO(download.content)).paragraphs
    )


def test_healthz_returns_only_update_date() -> None:
    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert date.fromisoformat(response.text.strip())
    assert response.text.count("\n") == 1


def test_upload_size_limit_returns_fragment() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/jobs",
            files={"document": ("big.txt", b"x" * 5_200_000, "text/plain")},
        )

        assert response.status_code == 413
        assert "Odrzucono upload" in response.text


@pytest.mark.asyncio
async def test_invalid_content_length_returns_upload_error() -> None:
    request = _FakeUploadRequest(content_length="not-a-number")

    with pytest.raises(UploadError, match="Content-Length") as error:
        await read_multipart_document(request, max_file_bytes=100, max_body_bytes=200)

    assert error.value.status_code == 400


def test_attachment_header_sanitizes_download_filename() -> None:
    header = _attachment_header('..\\evil"\r\nx.docx')

    assert "\r" not in header
    assert "\n" not in header
    assert 'filename="evil___x.docx"' in header
    assert "filename*=UTF-8''evil%22%0D%0Ax.docx" in header


class _FakeUploadRequest:
    def __init__(self, *, content_length: str) -> None:
        self.headers = {
            "content-type": "multipart/form-data; boundary=x",
            "content-length": content_length,
        }

    async def stream(self):
        yield b""
