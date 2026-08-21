import json
import subprocess
import sys

import pytest

from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry


def test_allowed_command_uses_fixed_profile_without_shell(tmp_path, monkeypatch) -> None:
    target = tmp_path / "src"
    target.mkdir()
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "clean", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    registry = build_phase3_registry(tmp_path, PermissionManager(lambda request: True))

    result = json.loads(
        registry.execute("run_allowed_command", {"command": "ruff_check", "target": "src"})
    )

    assert captured["command"] == [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--no-cache",
        str(target.resolve()),
    ]
    assert captured["cwd"] == tmp_path.resolve()
    assert captured["shell"] is False
    assert result["command"] == "ruff_check"
    assert result["timeout_seconds"] == 60


def test_allowed_command_rejects_unknown_profiles_and_workspace_escape(tmp_path) -> None:
    registry = build_phase3_registry(tmp_path, PermissionManager(lambda request: True))

    with pytest.raises(ToolError, match="not an allowed value"):
        registry.execute("run_allowed_command", {"command": "powershell"})
    with pytest.raises(ToolError, match="outside the authorized workspace"):
        registry.execute(
            "run_allowed_command", {"command": "pytest", "target": "../outside.py"}
        )


def test_allowed_command_denial_occurs_before_process_start(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("process must not start before permission"),
    )
    registry = build_phase3_registry(tmp_path)

    with pytest.raises(ToolError, match="Permission denied"):
        registry.execute("run_allowed_command", {"command": "python_version"})


def test_version_profile_rejects_target_and_timeout_is_safe(tmp_path, monkeypatch) -> None:
    registry = build_phase3_registry(tmp_path, PermissionManager(lambda request: True))
    with pytest.raises(ToolError, match="does not accept a target"):
        registry.execute(
            "run_allowed_command", {"command": "python_version", "target": "."}
        )

    def time_out(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", time_out)
    with pytest.raises(ToolError, match="10-second timeout"):
        registry.execute("run_allowed_command", {"command": "git_version"})
