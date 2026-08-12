"""Fail-closed construction of the Posejdon anonymizer."""

from __future__ import annotations

from threading import local
from typing import Any, Protocol

from posejdon import TextAnonymizer

from anonimizator3000.config import Settings


class _DetectorFailure(BaseException):
    """Escape Posejdon's broad ``suppress(Exception)`` detector boundary."""


class _BackendMonitor:
    """Remember errors that a Posejdon detector catches and turns into no findings."""

    def __init__(self, backend: Any, method_name: str) -> None:
        self._backend = backend
        self._method_name = method_name
        self._state = local()

    def reset(self) -> None:
        self._state.failure = None

    def failure(self) -> Exception | None:
        return getattr(self._state, "failure", None)

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._backend, name)
        if name != self._method_name:
            return attribute

        def monitored(*args: Any, **kwargs: Any) -> Any:
            try:
                return attribute(*args, **kwargs)
            except Exception as error:
                self._state.failure = error
                raise

        return monitored


class _FailClosedDetector:
    def __init__(self, detector: Any, monitor: _BackendMonitor | None = None) -> None:
        self._detector = detector
        self._monitor = monitor
        self.name = type(detector).__name__

    def detect(self, text: str) -> Any:
        if self._monitor is not None:
            self._monitor.reset()
        try:
            result = self._detector.detect(text)
        except Exception as error:
            failure = _DetectorFailure(f"{self.name} failed")
            raise failure from error
        backend_failure = self._monitor.failure() if self._monitor is not None else None
        if backend_failure is not None:
            failure = _DetectorFailure(f"{self.name} failed")
            raise failure from backend_failure
        return result


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
    detectors = {type(detector).__name__: detector for detector in anonymizer.detectors}
    required = {"RegexDetector", "PresidioDetector"}
    if settings.gliner_enabled:
        required.add("GLiNERDetector")
    unavailable = sorted(
        name
        for name in required
        if name not in detectors or getattr(detectors[name], "available", True) is not True
    )
    if unavailable:
        names = ", ".join(unavailable)
        raise RuntimeError(f"Posejdon failed to initialize required detectors: {names}")

    backend_methods = {
        "PresidioDetector": ("_engine", "analyze"),
        "GLiNERDetector": ("_model", "predict_entities"),
    }
    wrapped = []
    for detector in anonymizer.detectors:
        name = type(detector).__name__
        monitor = None
        if name in backend_methods:
            backend_name, method_name = backend_methods[name]
            backend = getattr(detector, backend_name, None)
            if backend is None:
                raise RuntimeError(f"Posejdon {name} backend is unavailable")
            monitor = _BackendMonitor(backend, method_name)
            setattr(detector, backend_name, monitor)
        wrapped.append(_FailClosedDetector(detector, monitor))
    anonymizer.detectors = wrapped
    return _AnonymizerBoundary(anonymizer)


class _AnonymizerBoundary:
    def __init__(self, anonymizer: TextAnonymizer) -> None:
        self._anonymizer = anonymizer

    def anonymize_segments(self, texts: list[str], *, replacement_style: Any) -> Any:
        try:
            return self._anonymizer.anonymize_segments(texts, replacement_style)
        except _DetectorFailure as failure:
            raise RuntimeError(str(failure)) from failure.__cause__
