import json
import subprocess

import pytest

from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry
from ato.voice import WindowsSpeechPlayer


class Player:
    def __init__(self):
        self.texts = []

    def speak(self, text):
        self.texts.append(text)


def test_speech_tool_requires_confirmation_then_plays(tmp_path) -> None:
    player = Player()
    denied = build_phase3_registry(tmp_path, speech_player=player)
    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute("speak_text", {"text": "hello"})
    assert player.texts == []
    registry = build_phase3_registry(
        tmp_path, PermissionManager(lambda request: True), speech_player=player
    )
    result = json.loads(registry.execute("speak_text", {"text": " hello "}))
    assert player.texts == ["hello"]
    assert result["characters"] == 5


def test_windows_tts_passes_text_only_via_stdin(monkeypatch) -> None:
    captured = {}
    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(subprocess, "run", fake_run)
    WindowsSpeechPlayer().speak("hello; dangerous")
    assert "hello; dangerous" not in " ".join(captured["command"])
    assert captured["input"] == "hello; dangerous"
    assert captured["shell"] is False
