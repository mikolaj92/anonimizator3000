"""Fail-closed construction of the Posejdon anonymizer."""

from __future__ import annotations

from typing import Any, Protocol

from posejdon import TextAnonymizer

from anonimizator3000.config import Settings


class _DetectorFailure(BaseException):
    """Escape Posejdon's broad ``suppress(Exception)`` detector boundary."""


class _FailClosedDetector:
    def __init__(self, detector: Any) -> None:
        self._detector = detector
        self.name = type(detector).__name__

    def detect(self, text: str) -> Any:
        try:
            return self._detector.detect(text)
        except Exception as error:
            failure = _DetectorFailure(f"{self.name} failed")
            raise failure from error


class SegmentAnonymizer(Protocol):
    def anonymize_segments(self, texts: list[str], *, replacement_style: Any) -> Any: ...


def create_anonymizer(settings: Settings) -> SegmentAnonymizer:
    """Build the configured detector stack or reject an incomplete stack."""
    options: dict[str, Any] = {
        "gliner_enabled": settings.gliner_enabled,
        "gliner_threshold": settings.gliner_threshold,
    }
    if settings.gliner_model is not None:
        options["gliner_model"] = settings.gliner_model
    anonymizer = TextAnonymizer(**options)
    detector_names = {type(detector).__name__ for detector in anonymizer.detectors}
    required = {"RegexDetector", "PresidioDetector"}
    if settings.gliner_enabled:
        required.add("GLiNERDetector")
    missing = sorted(required - detector_names)
    if missing:
        names = ", ".join(missing)
        raise RuntimeError(f"Posejdon failed to initialize required detectors: {names}")

    anonymizer.detectors = [_FailClosedDetector(detector) for detector in anonymizer.detectors]
    return _AnonymizerBoundary(anonymizer)


class _AnonymizerBoundary:
    def __init__(self, anonymizer: TextAnonymizer) -> None:
        self._anonymizer = anonymizer

    def anonymize_segments(self, texts: list[str], *, replacement_style: Any) -> Any:
        try:
            return self._anonymizer.anonymize_segments(texts, replacement_style)
        except _DetectorFailure as failure:
            raise RuntimeError(str(failure)) from failure.__cause__
