import time
from io import BytesIO

from doctotext import DOCX_MIME
from docx import Document
from fastapi.testclient import TestClient

from anonimizator3000.main import app


def _docx_bytes(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    output = BytesIO()
    document.save(output)
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


def test_upload_size_limit_returns_fragment() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/jobs",
            files={"document": ("big.txt", b"x" * 5_200_000, "text/plain")},
        )

        assert response.status_code == 200
        assert "Odrzucono upload" in response.text
