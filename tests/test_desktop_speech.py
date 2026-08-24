import json

import pytest

from ato.exceptions import AtoError, ToolError
from ato.security import AuditLogger, PermissionManager
from ato.ui.speech import DesktopSpeechService


class Player:
    def __init__(self) -> None:
        self.texts = []

    def speak(self, text: str) -> None:
        self.texts.append(text)


def test_desktop_speech_denies_without_playback_and_audits(tmp_path) -> None:
    player = Player()
    audit_path = tmp_path / "audit.jsonl"
    service = DesktopSpeechService(
        player, PermissionManager(lambda request: False), AuditLogger(audit_path)
    )
    with pytest.raises(ToolError, match="Permission denied"):
        service.speak("private reply")
    assert player.texts == []
    event = json.loads(audit_path.read_text(encoding="utf-8"))
    assert event["decision"] == "DENY"
    assert event["arguments"]["text"]["characters"] == 13
    assert "private reply" not in audit_path.read_text(encoding="utf-8")


def test_desktop_speech_calls_state_hook_only_after_approval(tmp_path) -> None:
    player = Player()
    events = []
    service = DesktopSpeechService(
        player, PermissionManager(lambda request: events.append("approved") or True),
        AuditLogger(tmp_path / "audit.jsonl")
    )
    service.speak("hello", on_playback=lambda: events.append("speaking"))
    assert events == ["approved", "speaking"]
    assert player.texts == ["hello"]


def test_desktop_speech_rejects_unbounded_text_before_permission(tmp_path) -> None:
    confirmations = []
    service = DesktopSpeechService(
        Player(), PermissionManager(lambda request: confirmations.append(request) or True),
        AuditLogger(tmp_path / "audit.jsonl")
    )
    with pytest.raises(AtoError, match="1-4,000"):
        service.speak("x" * 4_001)
    assert confirmations == []
