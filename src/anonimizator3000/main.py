from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from anonimizator3000.config import Settings, settings_from_env
from anonimizator3000.jobs import InMemoryJobQueue, JobSnapshot, QueueRejected
from anonimizator3000.processor import DocumentProcessor
from anonimizator3000.upload import UploadError, read_multipart_document

PACKAGE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = settings_from_env()
    processor = DocumentProcessor(
        max_text_chars=settings.max_text_chars,
        gliner_enabled=settings.gliner_enabled,
        gliner_model=settings.gliner_model,
        gliner_threshold=settings.gliner_threshold,
    )
    queue = InMemoryJobQueue(
        processor=processor,
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
app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"settings": _settings(request), "request": request},
    )


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
        )
    except (UploadError, QueueRejected) as error:
        return _error_fragment(request, str(error))

    return _job_fragment(request, job)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def get_job(request: Request, job_id: str) -> HTMLResponse:
    job = await _queue(request).get(job_id)
    if job is None:
        return _error_fragment(request, "Zadanie wygasło albo nie istnieje.")
    return _job_fragment(request, job)


@app.get("/jobs/{job_id}/download")
async def download_job(request: Request, job_id: str) -> Response:
    document = await _queue(request).result_document(job_id)
    if document is None:
        return PlainTextResponse("Wynik nie jest dostępny.", status_code=404)
    filename, content_type, data = document
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


def _job_fragment(request: Request, job: JobSnapshot) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="partials/job.html",
        context={"job": job, "request": request},
    )


def _error_fragment(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="partials/error.html",
        context={"message": message, "request": request},
    )


def _client_ip(request: Request, settings: Settings) -> str:
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _queue(request: Request) -> InMemoryJobQueue:
    return request.app.state.queue


def main() -> None:
    uvicorn.run("anonimizator3000.main:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()
