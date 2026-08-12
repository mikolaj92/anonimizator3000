from types import SimpleNamespace

import pytest
from posejdon.detectors.gliner_detector import GLiNERDetector as PosejdonGLiNERDetector
from posejdon.detectors.presidio_detector import PresidioDetector as PosejdonPresidioDetector

import anonimizator3000.anonymizer as anonymizer_module
from anonimizator3000.anonymizer import create_anonymizer
from anonimizator3000.config import Settings


class _Detector:
    def detect(self, text: str) -> list:
        return []


class RegexDetector(_Detector):
    pass


class PresidioDetector(_Detector):
    available = True

    def __init__(self) -> None:
        self._engine = SimpleNamespace(analyze=lambda **kwargs: [])


class GLiNERDetector(_Detector):
    available = True

    def __init__(self) -> None:
        self._model = SimpleNamespace(predict_entities=lambda *args, **kwargs: [])


class _StubTextAnonymizer:
    detectors: list = []

    def __init__(self, **kwargs) -> None:
        self.detectors = list(type(self).detectors)

    def anonymize_segments(self, texts, replacement_style):
        for detector in self.detectors:
            detector.detect(texts[0])
        return SimpleNamespace(texts=texts, findings={})


def _posejdon_detector(name: str, backend) -> object:
    if name == "PresidioDetector":
        detector = PosejdonPresidioDetector.__new__(PosejdonPresidioDetector)
        detector.language = "pl"
        detector._engine = backend
        return detector
    detector = PosejdonGLiNERDetector.__new__(PosejdonGLiNERDetector)
    detector.model_name = "configured/model"
    detector.threshold = 0.45
    detector._model = backend
    return detector


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


@pytest.mark.parametrize("name", ["PresidioDetector", "GLiNERDetector"])
def test_create_anonymizer_rejects_present_but_unavailable_posejdon_detector(
    monkeypatch, name
) -> None:
    detector = _posejdon_detector(name, None)
    detectors = [RegexDetector(), PresidioDetector()]
    settings = Settings(gliner_enabled=False)
    if name == "PresidioDetector":
        detectors[1] = detector
    else:
        detectors.append(detector)
        settings = Settings(gliner_enabled=True, gliner_model="configured/model")
    _StubTextAnonymizer.detectors = detectors
    monkeypatch.setattr(anonymizer_module, "TextAnonymizer", _StubTextAnonymizer)

    with pytest.raises(RuntimeError, match=name):
        create_anonymizer(settings)


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


@pytest.mark.parametrize("name", ["PresidioDetector", "GLiNERDetector"])
def test_errors_swallowed_by_posejdon_detector_fail_closed(monkeypatch, name) -> None:
    def fail(*args, **kwargs):
        raise ValueError("backend unavailable")

    method_name = "analyze" if name == "PresidioDetector" else "predict_entities"
    detector = _posejdon_detector(name, SimpleNamespace(**{method_name: fail}))
    detectors = [RegexDetector(), PresidioDetector()]
    settings = Settings(gliner_enabled=False)
    if name == "PresidioDetector":
        detectors[1] = detector
    else:
        detectors.append(detector)
        settings = Settings(gliner_enabled=True, gliner_model="configured/model")
    _StubTextAnonymizer.detectors = detectors
    monkeypatch.setattr(anonymizer_module, "TextAnonymizer", _StubTextAnonymizer)

    instance = create_anonymizer(settings)
    with pytest.raises(RuntimeError, match=f"{name} failed") as error:
        instance.anonymize_segments(["Jan Kowalski"], replacement_style="mask")

    assert isinstance(error.value.__cause__, ValueError)
