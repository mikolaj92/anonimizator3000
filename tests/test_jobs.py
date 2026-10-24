import asyncio
from io import BytesIO

import pytest
from docx import Document
from docxtor import DOCX_MIME
from posejdon import ReplacementKind

from anonimizator3000 import jobs
from anonimizator3000.jobs import DocumentProcessingQueue, QueueRejected


def _docx_bytes(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


class _FakeResult:
    def __init__(self, texts: list[str], findings: list[str]) -> None:
        self.texts = texts
        self.findings = findings


class _FakeAnonymizer:
    def __init__(self) -> None:
        self.seen_styles: list[ReplacementKind] = []

    def anonymize_segments(self, texts: list[str], *, replacement_style: ReplacementKind):
        self.seen_styles.append(replacement_style)
        return _FakeResult(
            [text.replace("secret", "<REDACTED>") for text in texts],
            ["TEST"],
        )


def _make_queue(anonymizer: _FakeAnonymizer, **overrides) -> DocumentProcessingQueue:
    kwargs = dict(
        anonymizer=anonymizer,
        max_text_chars=250_000,
        max_size=10,
        worker_count=1,
        max_active_jobs_per_ip=10,
        rate_limit_submissions=10,
        rate_limit_window_seconds=60,
        job_ttl_seconds=60,
    )
    kwargs.update(overrides)
    return DocumentProcessingQueue(**kwargs)


async def _wait_for_done(queue: DocumentProcessingQueue, job_id: str):
    for _ in range(100):
        snapshot = await queue.get(job_id)
        if snapshot and snapshot.status in {"done", "failed"}:
            return snapshot
        await asyncio.sleep(0.02)
    raise AssertionError("Job did not finish")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["done", "failed", "queued", "processing"])
async def test_queue_limits_active_jobs_per_ip_source_cleanup_and_ttl(monkeypatch, status) -> None:
    queue = _make_queue(_FakeAnonymizer(), max_active_jobs_per_ip=1)
    upload = dict(ip="127.0.0.1", content_type=DOCX_MIME, data=_docx_bytes("secret text"))
    first = await queue.submit(filename="a.docx", **upload)
    with pytest.raises(QueueRejected):
        await queue.submit(filename="b.docx", **upload)

    await queue.start()
    try:
        done = await _wait_for_done(queue, first.id)
    finally:
        await queue.stop()

    assert done.status == "done"
    assert done.result_content_type == DOCX_MIME
    assert done.has_source_bytes is False
    assert done.findings == {"TEST": 1}

    document = await queue.result_document(first.id)
    assert document is not None
    _, content_type, data = document
    assert content_type == DOCX_MIME
    text = "\n".join(p.text for p in Document(BytesIO(data)).paragraphs)
    assert "secret" not in text
    assert "<REDACTED>" in text

    queue._jobs[first.id].status = status
    for elapsed in (0, 60, 61):
        monkeypatch.setattr(jobs.time, "monotonic", lambda age=elapsed: done.updated_at + age)
        await queue._cleanup()
        retained = elapsed <= 60 or status in {"queued", "processing"}
        assert (await queue.get(first.id) is not None) is retained
        assert bool(await queue.result_document(first.id)) is (retained and status == "done")


@pytest.mark.asyncio
async def test_queue_passes_replacement_style_to_anonymizer() -> None:
    anonymizer = _FakeAnonymizer()
    queue = _make_queue(anonymizer)

    job = await queue.submit(
        ip="127.0.0.1",
        filename="a.docx",
        content_type=DOCX_MIME,
        data=_docx_bytes("secret text"),
        replacement_style="labels",
    )

    await queue.start()
    try:
        done = await _wait_for_done(queue, job.id)
    finally:
        await queue.stop()

    assert done.status == "done"
    assert anonymizer.seen_styles == [ReplacementKind.CATEGORY_PLACEHOLDER]


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,code", [("rate_limit_submissions", 429), ("max_size", 503)])
async def test_queue_rejects_submissions_over_capacity_or_ip_rate_limit(limit, code) -> None:
    queue = _make_queue(_FakeAnonymizer(), **{limit: 1})
    upload = dict(ip="127.0.0.1", content_type=DOCX_MIME, data=_docx_bytes("a"))
    await queue.submit(filename="a.docx", **upload)

    message = "Limit uploadów" if code == 429 else "Kolejka jest pełna"
    with pytest.raises(QueueRejected, match=message) as error:
        await queue.submit(filename="b.docx", **upload)
    assert error.value.status_code == code
