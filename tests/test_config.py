import pytest

from ato.config import Settings
from ato.exceptions import ConfigurationError


def test_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("ATO_MODEL", "test-model")
    settings = Settings.from_env()
    assert settings.deepseek_api_key == "test-key"
    assert settings.model == "test-model"


def test_settings_require_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("ato.config.load_dotenv", lambda: False)
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        Settings.from_env()
