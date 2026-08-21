import json
import subprocess

import pytest

from ato.computer import WindowsProcessMonitor
from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry


class FakeMonitor:
    def snapshot(self):
        return [
            {"process_id": 10, "name": "notepad", "cpu_seconds": 1.25, "working_set_bytes": 20},
            {"process_id": 20, "name": "python", "cpu_seconds": None, "working_set_bytes": 30},
        ]


def test_process_tool_requires_confirmation_and_supports_list_and_status(tmp_path) -> None:
    denied = build_phase3_registry(tmp_path, process_monitor=FakeMonitor())
    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute("inspect_processes", {"operation": "list"})

    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        process_monitor=FakeMonitor(),
    )
    listing = json.loads(
        registry.execute("inspect_processes", {"operation": "list", "name": "PY", "limit": 1})
    )
    status = json.loads(
        registry.execute("inspect_processes", {"operation": "status", "process_id": 10})
    )
    assert [item["process_id"] for item in listing["processes"]] == [20]
    assert status["found"] is True
    assert status["process"]["name"] == "notepad"


def test_process_operation_fields_cannot_be_mixed(tmp_path) -> None:
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        process_monitor=FakeMonitor(),
    )
    with pytest.raises(ToolError, match="requires process_id"):
        registry.execute("inspect_processes", {"operation": "status"})
    with pytest.raises(ToolError, match="does not accept process_id"):
        registry.execute("inspect_processes", {"operation": "list", "process_id": 10})


def test_windows_snapshot_uses_fixed_command_and_privacy_fields(monkeypatch) -> None:
    captured = {}
    payload = json.dumps(
        [{"Id": 10, "ProcessName": "python", "CPU": 1.23456, "WorkingSet64": 4096}]
    )

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, payload, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    processes = WindowsProcessMonitor().snapshot()
    assert processes == [
        {"process_id": 10, "name": "python", "cpu_seconds": 1.235, "working_set_bytes": 4096}
    ]
    assert captured["shell"] is False
    command_text = " ".join(captured["command"])
    assert "CommandLine" not in command_text
    assert "UserName" not in command_text
    assert "Path" not in command_text


def test_windows_snapshot_reports_invalid_json(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "not-json", ""),
    )
    with pytest.raises(ToolError, match="unreadable"):
        WindowsProcessMonitor().snapshot()
