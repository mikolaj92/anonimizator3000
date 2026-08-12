import pytest

from anonimizator3000.config import settings_from_env


def test_gliner_remains_enabled_with_the_configured_model_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANON_GLINER_ENABLED", raising=False)
    monkeypatch.delenv("ANON_GLINER_MODEL", raising=False)

    settings = settings_from_env()

    assert settings.gliner_enabled is True
    assert settings.gliner_model == "urchade/gliner_multi_pii-v1"


def test_gliner_requires_model_when_explicitly_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANON_GLINER_ENABLED", "true")
    monkeypatch.setenv("ANON_GLINER_MODEL", "  ")

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
