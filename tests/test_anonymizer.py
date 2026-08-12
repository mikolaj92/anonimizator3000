from types import SimpleNamespace

import pytest

import anonimizator3000.anonymizer as anonymizer_module
from anonimizator3000.anonymizer import create_anonymizer
from anonimizator3000.config import Settings


class _Detector:
    def detect(self, text: str) -> list:
        return []


class RegexDetector(_Detector):
    pass


class PresidioDetector(_Detector):
    pass


class GLiNERDetector(_Detector):
    pass


class _StubTextAnonymizer:
    detectors: list = []

    def __init__(self, **kwargs) -> None:
        self.detectors = list(type(self).detectors)

    def anonymize_segments(self, texts, replacement_style):
        for detector in self.detectors:
            detector.detect(texts[0])
        return SimpleNamespace(texts=texts, findings={})


def test_create_anonymizer_rejects_silently_missing_enabled_gliner(monkeypatch) -> None:
    _StubTextAnonymizer.detectors = [RegexDetector(), PresidioDetector()]
    monkeypatch.setattr(anonymizer_module, "TextAnonymizer", _StubTextAnonymizer)

    with pytest.raises(RuntimeError, match="GLiNERDetector"):
        create_anonymizer(Settings(gliner_enabled=True, gliner_model="configured/model"))


def test_create_anonymizer_rejects_silently_missing_presidio(monkeypatch) -> None:
    _StubTextAnonymizer.detectors = [RegexDetector()]
    monkeypatch.setattr(anonymizer_module, "TextAnonymizer", _StubTextAnonymizer)

    with pytest.raises(RuntimeError, match="PresidioDetector"):
        create_anonymizer(Settings(gliner_enabled=False))


def test_detector_errors_are_not_silently_ignored(monkeypatch) -> None:
    class BrokenPresidioDetector(PresidioDetector):
        pass

    # Preserve the required detector class name while making detection fail.
    BrokenPresidioDetector.__name__ = "PresidioDetector"
    broken = BrokenPresidioDetector()
    broken.detect = lambda text: (_ for _ in ()).throw(ValueError("backend unavailable"))
    _StubTextAnonymizer.detectors = [RegexDetector(), broken]
    monkeypatch.setattr(anonymizer_module, "TextAnonymizer", _StubTextAnonymizer)

    instance = create_anonymizer(Settings(gliner_enabled=False))
    with pytest.raises(RuntimeError, match="PresidioDetector failed") as error:
        instance.anonymize_segments(["Jan Kowalski"], replacement_style="mask")

    assert isinstance(error.value.__cause__, ValueError)
