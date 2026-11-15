import pytest
from pydantic import ValidationError

from book_agent.config import Settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GEMINI_MODEL", "GEMINI_THINKING_LEVEL", "LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


def load() -> Settings:
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]  # values come from env


def test_gemini_defaults() -> None:
    settings = load()
    assert settings.llm_provider == "gemini"
    assert settings.gemini_model == "gemini-3.5-flash-lite"
    assert settings.gemini_thinking_level is None
    assert settings.gemini_api_key.get_secret_value() == "test-key"


def test_gemini_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "high")
    settings = load()
    assert settings.gemini_model == "gemini-3.8-flash"
    assert settings.gemini_thinking_level == "high"


@pytest.mark.parametrize("level", ["xhigh", "", "LOW-ish"])
def test_unknown_thinking_level_is_rejected(monkeypatch: pytest.MonkeyPatch, level: str) -> None:
    monkeypatch.setenv("GEMINI_THINKING_LEVEL", level)
    with pytest.raises(ValidationError):
        load()


def test_api_key_is_hidden_when_printed() -> None:
    assert "test-key" not in repr(load())
