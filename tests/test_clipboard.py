import json
import subprocess

import pytest

from ato.computer import WindowsClipboardWriter, validate_clipboard_text
from ato.exceptions import ToolError
from ato.security.audit import AuditLogger
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry


class RecordingClipboard:
    def __init__(self) -> None:
        self.values = []

    def write(self, text: str) -> None:
        self.values.append(text)


def test_clipboard_tool_requires_high_confirmation_then_writes(tmp_path) -> None:
    clipboard = RecordingClipboard()
    denied = build_phase3_registry(tmp_path, clipboard_writer=clipboard)
    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute("write_clipboard", {"text": "hello"})
    assert clipboard.values == []

    approved = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        clipboard_writer=clipboard,
    )
    result = json.loads(approved.execute("write_clipboard", {"text": "hello"}))
    assert clipboard.values == ["hello"]
    assert result["written"] is True
    assert result["characters"] == 5
    assert len(result["sha256"]) == 64


def test_windows_clipboard_passes_text_only_through_stdin(monkeypatch) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    text = "value; Remove-Item important.txt"
    WindowsClipboardWriter().write(text)

    assert text not in " ".join(captured["command"])
    assert captured["input"] == text
    assert captured["shell"] is False
    assert captured["timeout"] == 5


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "1-10,000"),
        ("x" * 10_001, "1-10,000"),
        ("bad\x00value", "control"),
        ("password=hunter2", "secret"),
        ("ghp_abcdefghijklmnopqrstuvwxyz", "secret"),
        ("-----BEGIN PRIVATE KEY-----", "secret"),
    ],
)
def test_clipboard_rejects_invalid_or_likely_secret_text(text, message) -> None:
    with pytest.raises(ToolError, match=message):
        validate_clipboard_text(text)


def test_clipboard_content_is_summarized_in_audit_log(tmp_path) -> None:
    clipboard = RecordingClipboard()
    audit_path = tmp_path / "audit.jsonl"
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        AuditLogger(audit_path),
        clipboard_writer=clipboard,
    )

    registry.execute("write_clipboard", {"text": "private draft"})
    event = json.loads(audit_path.read_text(encoding="utf-8"))

    assert event["arguments"]["text"]["characters"] == 13
    assert "private draft" not in audit_path.read_text(encoding="utf-8")
