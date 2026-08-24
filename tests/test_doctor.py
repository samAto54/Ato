from dataclasses import replace
from pathlib import Path

from ato.config import Settings
from ato.doctor import exit_code, run_checks
from ato.exceptions import ConfigurationError


def _settings(tmp_path: Path) -> Settings:
    return Settings(deepseek_api_key="hidden-test-key", workspace_root=tmp_path)


def test_doctor_reports_ready_core_without_revealing_secret(tmp_path) -> None:
    checks = run_checks(
        lambda: _settings(tmp_path),
        lambda name: object(),
    )
    output = "\n".join(check.display() for check in checks)

    assert exit_code(checks) == 0
    assert "hidden-test-key" not in output
    assert "[PASS] Configuration" in output
    assert "[WARN] Web search" in output


def test_doctor_fails_closed_for_invalid_configuration() -> None:
    def invalid_settings() -> Settings:
        raise ConfigurationError("DEEPSEEK_API_KEY is not set.")

    checks = run_checks(invalid_settings, lambda name: object())

    assert exit_code(checks) == 1
    assert checks[-1].name == "Configuration"


def test_doctor_checks_enabled_voice_dependencies_and_model(tmp_path) -> None:
    settings = replace(
        _settings(tmp_path),
        voice_enabled=True,
        stt_model_path=tmp_path / "missing-model",
    )
    checks = run_checks(
        lambda: settings,
        lambda name: None if name == "sounddevice" else object(),
    )
    by_name = {check.name: check for check in checks}

    assert by_name["Voice audio"].level == "FAIL"
    assert by_name["Speech recognition"].level == "WARN"
