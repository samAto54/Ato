import pytest

from ato.config import Settings
from ato.exceptions import ConfigurationError


def test_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("ATO_MODEL", "test-model")
    monkeypatch.setenv("ATO_MEMORY_FILE", "custom/memory.json")
    monkeypatch.setenv("ATO_MEMORY_MAX_MESSAGES", "24")
    monkeypatch.setenv("ATO_WORKSPACE_ROOT", "workspace")
    monkeypatch.setenv("ATO_AUDIT_FILE", "logs/audit.jsonl")
    monkeypatch.setenv("ATO_CONTEXT_MAX_TOKENS", "8000")
    monkeypatch.setenv("ATO_CONTEXT_RECENT_MESSAGES", "10")
    monkeypatch.setenv("ATO_CONTEXT_SUMMARY_MAX_CHARS", "4000")
    settings = Settings.from_env()
    assert settings.deepseek_api_key == "test-key"
    assert settings.model == "test-model"
    assert str(settings.memory_file) == "custom\\memory.json"
    assert settings.memory_max_messages == 24
    assert str(settings.workspace_root) == "workspace"
    assert str(settings.audit_file) == "logs\\audit.jsonl"
    assert settings.context_max_tokens == 8000
    assert settings.context_recent_messages == 10
    assert settings.context_summary_max_chars == 4000


def test_settings_require_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("ato.config.load_dotenv", lambda: False)
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        Settings.from_env()


def test_settings_validate_memory_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("ATO_MEMORY_MAX_MESSAGES", "one hundred")

    with pytest.raises(ConfigurationError, match="must be an integer"):
        Settings.from_env()
