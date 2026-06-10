import asyncio

import pytest

from anonimizator3000.jobs import InMemoryJobQueue, QueueRejected
from anonimizator3000.processor import ProcessedDocument


def _processor(filename: str, content_type: str, data: bytes) -> ProcessedDocument:
    return ProcessedDocument(
        filename="result.txt",
        content_type="text/plain; charset=utf-8",
        data=data.decode().replace("secret", "<REDACTED>").encode(),
        findings={"TEST": 1},
    )


async def _wait_for_done(queue: InMemoryJobQueue, job_id: str):
    for _ in range(50):
        snapshot = await queue.get(job_id)
        if snapshot and snapshot.status == "done":
            return snapshot
        await asyncio.sleep(0.02)
    raise AssertionError("Job did not finish")


@pytest.mark.asyncio
async def test_queue_limits_active_jobs_per_ip_and_drops_source_bytes_after_processing() -> None:
    queue = InMemoryJobQueue(
        processor=_processor,
        max_size=10,
        worker_count=1,
        max_active_jobs_per_ip=1,
        rate_limit_submissions=10,
        rate_limit_window_seconds=60,
        job_ttl_seconds=60,
    )

    first = await queue.submit(
        ip="127.0.0.1", filename="a.txt", content_type="text/plain", data=b"secret"
    )
    with pytest.raises(QueueRejected):
        await queue.submit(
            ip="127.0.0.1", filename="b.txt", content_type="text/plain", data=b"secret"
        )

    await queue.start()
    try:
        done = await _wait_for_done(queue, first.id)
    finally:
        await queue.stop()

    assert done.result_filename == "result.txt"
    assert done.result_size == len(b"<REDACTED>")
    assert done.has_source_bytes is False

    document = await queue.result_document(first.id)
    assert document == ("result.txt", "text/plain; charset=utf-8", b"<REDACTED>")


@pytest.mark.asyncio
async def test_queue_rate_limits_submissions_per_ip() -> None:
    queue = InMemoryJobQueue(
        processor=_processor,
        max_size=10,
        worker_count=1,
        max_active_jobs_per_ip=10,
        rate_limit_submissions=1,
        rate_limit_window_seconds=60,
        job_ttl_seconds=60,
    )

    await queue.submit(ip="127.0.0.1", filename="a.txt", content_type="text/plain", data=b"a")

    with pytest.raises(QueueRejected, match="Limit uploadów"):
        await queue.submit(ip="127.0.0.1", filename="b.txt", content_type="text/plain", data=b"b")
