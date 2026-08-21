import json
import subprocess

import pytest

from ato.computer import WindowsApplicationLauncher
from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry


class RecordingLauncher:
    def __init__(self) -> None:
        self.applications = []

    def launch(self, application: str) -> int:
        self.applications.append(application)
        return 4321


def test_application_tool_requires_high_confirmation_then_launches(tmp_path) -> None:
    launcher = RecordingLauncher()
    denied = build_phase3_registry(tmp_path, application_launcher=launcher)
    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute("launch_application", {"application": "notepad"})
    assert launcher.applications == []

    approved = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        application_launcher=launcher,
    )
    result = json.loads(
        approved.execute("launch_application", {"application": "calculator"})
    )
    assert launcher.applications == ["calculator"]
    assert result == {
        "launched": True,
        "application": "calculator",
        "process_id": 4321,
    }


@pytest.mark.parametrize(
    ("application", "command"),
    [
        ("notepad", ["notepad.exe"]),
        ("calculator", ["calc.exe"]),
        ("file_explorer", ["explorer.exe"]),
    ],
)
def test_windows_launcher_uses_only_fixed_argument_free_commands(
    monkeypatch, application, command
) -> None:
    captured = {}

    class Process:
        pid = 123

    def fake_popen(actual_command, **kwargs):
        captured["command"] = actual_command
        captured.update(kwargs)
        return Process()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    assert WindowsApplicationLauncher().launch(application) == 123
    assert captured["command"] == command
    assert captured["shell"] is False
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL


def test_unknown_application_is_rejected_before_launcher(tmp_path) -> None:
    launcher = RecordingLauncher()
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        application_launcher=launcher,
    )
    with pytest.raises(ToolError, match="not an allowed value"):
        registry.execute("launch_application", {"application": "powershell"})
    assert launcher.applications == []


def test_windows_launcher_reports_start_failure(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise OSError("missing")

    monkeypatch.setattr(subprocess, "Popen", fail)
    with pytest.raises(ToolError, match="notepad application could not be launched"):
        WindowsApplicationLauncher().launch("notepad")
