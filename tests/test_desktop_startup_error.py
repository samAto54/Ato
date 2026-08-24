from ato.ui.startup_error import MAX_STARTUP_ERROR_CHARS, build_startup_failure


def test_startup_failure_gives_actionable_missing_key_guidance() -> None:
    failure = build_startup_failure(ValueError("DEEPSEEK_API_KEY is required."))

    assert failure.title == "Unable to start Ato"
    assert "DEEPSEEK_API_KEY" in failure.message
    assert ".env" in failure.guidance
    assert "source code" in failure.guidance


def test_startup_failure_redacts_secrets_and_bounds_untrusted_error_text() -> None:
    failure = build_startup_failure(ValueError("api_key=supersecretvalue " + "x" * 2_000))

    assert "supersecretvalue" not in failure.message
    assert "[REDACTED]" in failure.message
    assert len(failure.message) <= MAX_STARTUP_ERROR_CHARS
