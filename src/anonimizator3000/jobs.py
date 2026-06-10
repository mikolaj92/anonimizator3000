import asyncio
import contextlib
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

from fala import (
    AdapterSpec,
    InMemoryStateStore,
    PipelineRegistry,
    PipelineScheduler,
    PipelineSpec,
    ProcessEvent,
    ProcessOutput,
    ProcessSpec,
    ProcessStatus,
    RetryPolicy,
    RuntimeService,
)

from anonimizator3000.processor import ProcessedDocument

JobStatus = Literal["queued", "processing", "done", "failed"]

PIPELINE_ID = "document_anonymization"
PROCESS_ID = "anonymize_document"
QUEUE_NAME = "documents.anonymize"
RUN_ID = "anonimizator3000"


@dataclass
class _Job:
    id: str
    ip: str
    source_filename: str | None
    source_content_type: str
    source_bytes: bytes | None
    status: JobStatus = "queued"
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)
    result_filename: str | None = None
    result_content_type: str | None = None
    result_bytes: bytes | None = None
    findings: dict[str, int] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class JobSnapshot:
    id: str
    status: JobStatus
    created_at: float
    updated_at: float
    elapsed_seconds: float
    progress_percent: int
    result_filename: str | None
    result_content_type: str | None
    result_size: int | None
    findings: dict[str, int]
    error: str | None
    queue_position: int | None
    has_source_bytes: bool


class QueueRejected(RuntimeError):
    def __init__(self, message: str, status_code: int = 429) -> None:
        super().__init__(message)
        self.status_code = status_code


class DocumentProcessingQueue:
    def __init__(
        self,
        *,
        processor: Callable[[str, str, bytes], ProcessedDocument],
        max_size: int,
        worker_count: int,
        max_active_jobs_per_ip: int,
        rate_limit_submissions: int,
        rate_limit_window_seconds: int,
        job_ttl_seconds: int,
    ) -> None:
        self._processor = processor
        self._max_size = max_size
        self._worker_count = worker_count
        self._max_active_jobs_per_ip = max_active_jobs_per_ip
        self._rate_limit_submissions = rate_limit_submissions
        self._rate_limit_window_seconds = rate_limit_window_seconds
        self._job_ttl_seconds = job_ttl_seconds
        self._jobs: dict[str, _Job] = {}
        self._submissions: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()
        self._workers: list[asyncio.Task] = []
        self._cleanup_task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

        self._pipeline = _document_pipeline()
        self._store = InMemoryStateStore()
        self._runtime = RuntimeService(
            registry=PipelineRegistry([self._pipeline]),
            store=self._store,
        )

    async def start(self) -> None:
        if self._workers:
            return
        self._stopping.clear()
        self._workers = [
            asyncio.create_task(
                self._worker_loop(worker_id=f"anonimizator-worker-{index}"),
                name=f"anonimizator-worker-{index}",
            )
            for index in range(self._worker_count)
        ]
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="anon-cleanup")

    async def stop(self) -> None:
        self._stopping.set()
        for worker in self._workers:
            worker.cancel()
        if self._cleanup_task:
            self._cleanup_task.cancel()
        tasks = [*self._workers, *(task for task in [self._cleanup_task] if task)]
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._workers.clear()
        self._cleanup_task = None

    async def submit(
        self, *, ip: str, filename: str, content_type: str, data: bytes
    ) -> JobSnapshot:
        now = time.monotonic()
        job = _Job(
            id=_new_job_id(),
            ip=ip,
            source_filename=filename,
            source_content_type=content_type,
            source_bytes=data,
        )

        async with self._lock:
            self._enforce_rate_limit(ip, now)
            if self._active_jobs_for_ip(ip) >= self._max_active_jobs_per_ip:
                raise QueueRejected("Za dużo aktywnych zadań z tego IP.")
            if self._active_jobs() >= self._max_size:
                raise QueueRejected("Kolejka jest pełna. Spróbuj później.", status_code=503)

            self._jobs[job.id] = job
            self._submissions[ip].append(now)

        try:
            await self._runtime.initialize_document(
                run_id=RUN_ID,
                document_id=job.id,
                pipeline_id=PIPELINE_ID,
                values={
                    "filename": filename,
                    "content_type": content_type,
                    "size": len(data),
                    "client_ip": ip,
                },
            )
        except Exception:
            async with self._lock:
                self._jobs.pop(job.id, None)
                with contextlib.suppress(ValueError):
                    self._submissions[ip].remove(now)
            raise

        snapshot = await self.get(job.id)
        if snapshot is None:
            raise RuntimeError("Zadanie nie zostało utworzone.")
        return snapshot

    async def get(self, job_id: str) -> JobSnapshot | None:
        status = await self._job_status(job_id)
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            self._sync_local_status(job, status)
            return self._snapshot(job, status)

    async def result_document(self, job_id: str) -> tuple[str, str, bytes] | None:
        status = await self._job_status(job_id)
        async with self._lock:
            job = self._jobs.get(job_id)
            if (
                not job
                or status != "done"
                or job.result_filename is None
                or job.result_content_type is None
                or job.result_bytes is None
            ):
                return None
            return job.result_filename, job.result_content_type, job.result_bytes

    async def events(self, job_id: str) -> list[ProcessEvent]:
        return await self._store.list_events(run_id=RUN_ID, document_id=job_id)

    async def _worker_loop(self, *, worker_id: str) -> None:
        while not self._stopping.is_set():
            claim = await self._runtime.claim_next(
                run_id=RUN_ID,
                pipeline_id=PIPELINE_ID,
                worker_id=worker_id,
                process_id=PROCESS_ID,
                adapter_kind="queue",
                lease_seconds=300,
            )
            if claim is None:
                await asyncio.sleep(0.05)
                continue
            await self._process_claim(claim.document_id)

    async def _process_claim(self, job_id: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.source_bytes is None or job.source_filename is None:
                filename = None
                content_type = None
                data = None
            else:
                job.status = "processing"
                job.updated_at = time.monotonic()
                filename = job.source_filename
                content_type = job.source_content_type
                data = job.source_bytes

        if filename is None or content_type is None or data is None:
            await self._record_failure(job_id, "Brak dokumentu źródłowego.")
            return

        try:
            result = await asyncio.to_thread(self._processor, filename, content_type, data)
        except Exception as error:
            message = str(error)
            async with self._lock:
                job = self._jobs.get(job_id)
                if job:
                    job.status = "failed"
                    job.error = message
                    job.source_bytes = None
                    job.source_filename = None
                    job.updated_at = time.monotonic()
            await self._record_failure(job_id, message)
            return

        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.result_filename = result.filename
                job.result_content_type = result.content_type
                job.result_bytes = result.data
                job.findings = result.findings
                job.source_bytes = None
                job.source_filename = None
                job.updated_at = time.monotonic()

        await self._store.put_output(
            run_id=RUN_ID,
            document_id=job_id,
            process_id=PROCESS_ID,
            output=ProcessOutput(
                values={
                    "filename": result.filename,
                    "content_type": result.content_type,
                    "size": len(result.data),
                    "findings": result.findings,
                }
            ),
        )
        await self._runtime.schedule_document(
            run_id=RUN_ID,
            document_id=job_id,
            pipeline_id=PIPELINE_ID,
        )

        async with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = "done"
                job.updated_at = time.monotonic()

    async def _record_failure(self, job_id: str, message: str) -> None:
        await PipelineScheduler(self._pipeline, self._store).record_process_failure(
            run_id=RUN_ID,
            document_id=job_id,
            process_id=PROCESS_ID,
            reason=message,
        )

    async def _cleanup_loop(self) -> None:
        while not self._stopping.is_set():
            await asyncio.sleep(30)
            await self._cleanup()

    async def _cleanup(self) -> None:
        now = time.monotonic()
        async with self._lock:
            expired = [
                job_id
                for job_id, job in self._jobs.items()
                if job.status in {"done", "failed"} and now - job.updated_at > self._job_ttl_seconds
            ]
            for job_id in expired:
                del self._jobs[job_id]

            for ip, timestamps in list(self._submissions.items()):
                self._prune_timestamps(timestamps, now)
                if not timestamps:
                    del self._submissions[ip]

    async def _job_status(self, job_id: str) -> JobStatus:
        statuses = await self._store.list_statuses(run_id=RUN_ID, document_id=job_id)
        return _job_status(statuses.get(PROCESS_ID))

    def _snapshot(self, job: _Job, status: JobStatus) -> JobSnapshot:
        now = time.monotonic()
        return JobSnapshot(
            id=job.id,
            status=status,
            created_at=job.created_at,
            updated_at=job.updated_at,
            elapsed_seconds=(
                job.updated_at - job.created_at
                if status in {"done", "failed"}
                else now - job.created_at
            ),
            progress_percent=_progress_percent(status),
            result_filename=job.result_filename,
            result_content_type=job.result_content_type,
            result_size=len(job.result_bytes) if job.result_bytes is not None else None,
            findings=dict(job.findings),
            error=job.error,
            queue_position=self._queue_position(job.id) if status == "queued" else None,
            has_source_bytes=job.source_bytes is not None,
        )

    def _queue_position(self, job_id: str) -> int | None:
        queued_ids = [
            job.id
            for job in sorted(self._jobs.values(), key=lambda item: item.created_at)
            if job.status == "queued"
        ]
        if job_id not in queued_ids:
            return None
        return queued_ids.index(job_id) + 1

    def _sync_local_status(self, job: _Job, status: JobStatus) -> None:
        if job.status == status:
            return
        job.status = status
        job.updated_at = time.monotonic()

    def _enforce_rate_limit(self, ip: str, now: float) -> None:
        timestamps = self._submissions[ip]
        self._prune_timestamps(timestamps, now)
        if len(timestamps) >= self._rate_limit_submissions:
            raise QueueRejected("Limit uploadów z tego IP został przekroczony.")

    def _active_jobs_for_ip(self, ip: str) -> int:
        return sum(
            1
            for job in self._jobs.values()
            if job.ip == ip and job.status in {"queued", "processing"}
        )

    def _active_jobs(self) -> int:
        return sum(1 for job in self._jobs.values() if job.status in {"queued", "processing"})

    def _prune_timestamps(self, timestamps: deque[float], now: float) -> None:
        cutoff = now - self._rate_limit_window_seconds
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()


def _document_pipeline() -> PipelineSpec:
    return PipelineSpec(
        id=PIPELINE_ID,
        title="Document anonymization",
        steps=[
            ProcessSpec(
                id=PROCESS_ID,
                title="Anonymize document",
                adapter=AdapterSpec(kind="queue", queue=QUEUE_NAME),
                timeout_seconds=300,
                retry=RetryPolicy(max_attempts=1),
            )
        ],
    )


def _new_job_id() -> str:
    return f"job_{time.time_ns()}_{uuid4().hex[:12]}"


def _job_status(status: ProcessStatus | None) -> JobStatus:
    match status:
        case ProcessStatus.running:
            return "processing"
        case ProcessStatus.completed:
            return "done"
        case ProcessStatus.failed | ProcessStatus.cancelled:
            return "failed"
        case _:
            return "queued"


def _progress_percent(status: JobStatus) -> int:
    match status:
        case "queued":
            return 18
        case "processing":
            return 68
        case "done":
            return 100
        case "failed":
            return 100
