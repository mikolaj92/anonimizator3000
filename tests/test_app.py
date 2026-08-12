import base64
import json
import time
from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from doctotext import DOCX_MIME
from docx import Document
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from anonimizator3000.main import _attachment_header, _settings_boot, app
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
    # Platform assets from app-factory (same-origin /static/platform), not vendored.
    assert "/static/platform/" in response.text
    assert "/static/basecoat/" not in response.text
    assert "/static/htmx.min.js" not in response.text
    assert "/static/app.css" in response.text
    assert "100 dokumentów" in response.text
    assert "10 minut" in response.text


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


def test_platform_auth_routes_exist() -> None:
    """Package-owned auth/account/admin surfaces are reachable anonymously."""
    with TestClient(app) as client:
        login = client.get("/login")
        register = client.get("/register")
        account = client.get("/account", follow_redirects=False)
        profile = client.post("/account/profile", follow_redirects=False)
        admin_users = client.get("/admin/users", follow_redirects=False)
        logout = client.post("/logout", follow_redirects=False)

    assert login.status_code == 200
    assert register.status_code == 200
    assert account.status_code == 303
    assert profile.status_code == 401
    assert admin_users.status_code == 303
    assert logout.status_code == 303


@pytest.mark.parametrize("path", ("/login", "/account", "/admin/users"))
def test_platform_ui_shell_loads_same_origin_stack(path: str) -> None:
    session = {"user": {"id": "audit-user", "name": "audit_user", "is_admin": True}}
    payload = base64.b64encode(json.dumps(session).encode())
    cookie = TimestampSigner(_settings_boot.session_secret).sign(payload).decode()

    with TestClient(app) as client:
        client.cookies.set(_settings_boot.session_cookie_name, cookie)
        response = client.get(path)

    assert response.status_code == 200
    for asset in (
        "/static/platform/basecoat-factory.min.css",
        "/static/platform/basecoat-js.min.js",
        "/static/platform/htmx.min.js",
        "/static/platform/alpine.min.js",
    ):
        assert asset in response.text
    assert "cdn.jsdelivr.net" not in response.text
    assert "unpkg.com" not in response.text


def test_platform_asset_is_served_same_origin() -> None:
    with TestClient(app) as client:
        response = client.get("/static/platform/basecoat-factory.min.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


def test_host_static_contains_only_product_assets() -> None:
    static_dir = Path(__file__).resolve().parents[1] / "src/anonimizator3000/static"

    assert {
        path.relative_to(static_dir).as_posix()
        for path in static_dir.rglob("*")
        if path.is_file()
    } == {"app.css"}


def test_base_delegates_platform_chrome_to_product_shell() -> None:
    template = Path(__file__).resolve().parents[1] / "src/anonimizator3000/templates/base.html"
    base = template.read_text(encoding="utf-8")

    assert 'extends "app_factory/product_shell.html"' in base
    assert "basecoat/basecoat" not in base
    assert "htmx.min.js" not in base
    assert "head_assets" not in base
    assert "platform_theme_locale" not in base
    assert "platform_sidebar" not in base
    assert "platform_session" not in base


def test_product_shell_uses_platform_controls_without_logout() -> None:
    session = {"user": {"id": "audit-user", "name": "audit_user", "is_admin": True}}
    payload = base64.b64encode(json.dumps(session).encode())
    cookie = TimestampSigner(_settings_boot.session_secret).sign(payload).decode()

    with TestClient(app) as client:
        client.cookies.set(_settings_boot.session_cookie_name, cookie)
        response = client.get("/")

    assert response.status_code == 200
    assert "data-platform-theme-locale" in response.text
    assert "data-platform-account-link" in response.text
    assert 'action="/logout"' not in response.text


def test_logout_is_only_rendered_on_account_surface() -> None:
    session = {"user": {"id": "audit-user", "name": "audit_user", "is_admin": True}}
    payload = base64.b64encode(json.dumps(session).encode())
    cookie = TimestampSigner(_settings_boot.session_secret).sign(payload).decode()

    with TestClient(app) as client:
        client.cookies.set(_settings_boot.session_cookie_name, cookie)
        response = client.get("/account")

    assert response.status_code == 200
    assert "data-platform-session" in response.text
    assert 'action="/logout"' in response.text
