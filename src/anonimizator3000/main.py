from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import uvicorn
from app_factory.fastapi import install_app_factory_ui
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from anonimizator3000.anonymizer import create_anonymizer
from anonimizator3000.auth_stores import migrate_auth_database
from anonimizator3000.config import Settings, normalize_replacement_style, settings_from_env
from anonimizator3000.jobs import DocumentProcessingQueue, JobSnapshot, QueueRejected
from anonimizator3000.passkey_setup import bootstrap_admin, install_passkey_routes
from anonimizator3000.platform_chrome import (
    DEFAULT_LOCALE,
    LOCALE_COOKIE_NAME,
    install_platform_chrome,
    platform_request_context,
)
from anonimizator3000.upload import UploadError, read_multipart_document
from anonimizator3000.usermanager_ui import install_anon_usermanager_ui

PACKAGE_DIR = Path(__file__).parent
REPO_DIR = PACKAGE_DIR.parents[1]
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

# Module-level settings for middleware/install; lifespan reloads from env.
_settings_boot = settings_from_env()
_auth_database_boot = migrate_auth_database(_settings_boot)


def _session_user(request: Request) -> dict | None:
    if "session" not in request.scope:
        return None
    user = request.session.get("user")
    return user if isinstance(user, dict) and user.get("id") else None


def _request_locale(request: Request) -> str:
    query = request.query_params.get("lang")
    if isinstance(query, str) and query:
        return query
    cookie = request.cookies.get(LOCALE_COOKIE_NAME)
    if isinstance(cookie, str) and cookie:
        return cookie
    return DEFAULT_LOCALE


def _page_context(request: Request, **extra) -> dict:
    user = _session_user(request)
    return {
        "request": request,
        "user": user,
        **platform_request_context(
            user=user,
            current_path=request.url.path,
            locale=_request_locale(request),
        ),
        **extra,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = settings_from_env()
    auth_database = migrate_auth_database(settings)
    bootstrap_admin(auth_database)
    binding = getattr(app.state, "auth_database_binding", None)
    if binding is not None:
        binding.update(auth_database)
    app.state.auth_database = auth_database

    anonymizer = create_anonymizer(settings)
    queue = DocumentProcessingQueue(
        anonymizer=anonymizer,
        max_text_chars=settings.max_text_chars,
        max_size=settings.queue_max_size,
        worker_count=settings.worker_count,
        max_active_jobs_per_ip=settings.max_active_jobs_per_ip,
        rate_limit_submissions=settings.rate_limit_submissions,
        rate_limit_window_seconds=settings.rate_limit_window_seconds,
        job_ttl_seconds=settings.job_ttl_seconds,
    )
    app.state.settings = settings
    app.state.queue = queue
    await queue.start()
    try:
        yield
    finally:
        await queue.stop()


app = FastAPI(title="Anonimizator3000", lifespan=lifespan)

# Install order: app-factory platform assets, then host /static (domain CSS only).
_platform = install_app_factory_ui(app, environments=(templates.env,))
app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
install_platform_chrome([templates.env])

_passkey_ui, _auth_binding = install_passkey_routes(
    app,
    platform=_platform,
    auth_database=_auth_database_boot,
    settings=_settings_boot,
)
install_anon_usermanager_ui(
    app,
    platform=_platform,
    environment=templates.env,
    database=_auth_binding,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=_settings_boot.session_secret,
    max_age=_settings_boot.session_max_age,
    session_cookie=_settings_boot.session_cookie_name,
    https_only=_settings_boot.session_cookie_secure,
    same_site=_settings_boot.session_cookie_samesite,
)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return _index_response(request)


@app.post("/jobs", response_class=HTMLResponse)
async def create_job(request: Request) -> HTMLResponse:
    settings = _settings(request)
    queue = _queue(request)
    try:
        upload = await read_multipart_document(
            request,
            max_file_bytes=settings.max_file_bytes,
            max_body_bytes=settings.max_multipart_body_bytes,
        )
        job = await queue.submit(
            ip=_client_ip(request, settings),
            filename=upload.filename,
            content_type=upload.content_type,
            data=upload.data,
            replacement_style=normalize_replacement_style(
                upload.replacement_style, settings.replacement_style
            ),
        )
    except (UploadError, QueueRejected) as error:
        if _is_htmx(request):
            return _error_fragment(request, str(error), status_code=error.status_code)
        return _index_response(request, error_message=str(error), status_code=error.status_code)

    if _is_htmx(request):
        return _job_fragment(request, job)
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def get_job(request: Request, job_id: str) -> HTMLResponse:
    job = await _queue(request).get(job_id)
    if job is None:
        message = "Zadanie wygasło albo nie istnieje."
        if _is_htmx(request):
            return _error_fragment(request, message, status_code=404)
        return _index_response(request, error_message=message, status_code=404)
    if _is_htmx(request):
        return _job_fragment(request, job)
    return _index_response(request, job=job)


@app.get("/jobs/{job_id}/download")
async def download_job(request: Request, job_id: str) -> Response:
    document = await _queue(request).result_document(job_id)
    if document is None:
        return PlainTextResponse("Wynik nie jest dostępny.", status_code=404)
    filename, content_type, data = document
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": _attachment_header(filename)},
    )


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> PlainTextResponse:
    return PlainTextResponse(f"{_version_date()}\n")


def _version_date() -> str:
    lock = REPO_DIR / "uv.lock"
    if lock.exists():
        return datetime.fromtimestamp(lock.stat().st_mtime, UTC).date().isoformat()
    return datetime.now(UTC).date().isoformat()


def _index_response(
    request: Request,
    *,
    job: JobSnapshot | None = None,
    error_message: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=_page_context(
            request,
            settings=_settings(request),
            job=job,
            error_message=error_message,
        ),
        status_code=status_code,
    )


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _job_fragment(request: Request, job: JobSnapshot) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="partials/job.html",
        context={"job": job, "request": request},
    )


def _error_fragment(request: Request, message: str, *, status_code: int = 400) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="partials/error.html",
        context={"message": message, "request": request},
        status_code=status_code,
    )


def _client_ip(request: Request, settings: Settings) -> str:
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _attachment_header(filename: str) -> str:
    safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip() or "download"
    encoded = quote(safe_name, safe="")
    return f"attachment; filename*=UTF-8''{encoded}"


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _queue(request: Request) -> DocumentProcessingQueue:
    return request.app.state.queue


def main() -> None:
    uvicorn.run("anonimizator3000.main:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()
