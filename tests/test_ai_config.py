import pytest

from anonimizator3000.config import settings_from_env


def test_gliner_requires_explicit_model_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANON_GLINER_ENABLED", "true")
    monkeypatch.delenv("ANON_GLINER_MODEL", raising=False)

    with pytest.raises(ValueError, match="ANON_GLINER_MODEL is required"):
        settings_from_env()


def test_gliner_accepts_explicit_model_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANON_GLINER_ENABLED", "true")
    monkeypatch.setenv("ANON_GLINER_MODEL", "configured/model")

    settings = settings_from_env()

    assert settings.gliner_enabled is True
    assert settings.gliner_model == "configured/model"


def test_invalid_boolean_configuration_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANON_GLINER_ENABLED", "maybe")

    with pytest.raises(ValueError, match="ANON_GLINER_ENABLED must be a boolean"):
        settings_from_env()
