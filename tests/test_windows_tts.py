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


def test_windows_tts_reports_speech_progress_without_exposing_text(monkeypatch) -> None:
    captured = {}

    class FakeInput:
        def write(self, value):
            captured["input"] = value

        def close(self):
            captured["closed"] = True

    class FakeProcess:
        stdin = FakeInput()
        stdout = iter(["ATO_LEVEL:71\n", "untrusted output\n"])

        def wait(self):
            return 0

        def kill(self):
            captured["killed"] = True

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    levels = []
    WindowsSpeechPlayer().speak("private reply", on_level=levels.append)

    assert captured["input"] == "private reply"
    assert "private reply" not in " ".join(captured["command"])
    assert levels == [0.71, 0.0]
    assert captured["shell"] is False


def test_windows_tts_visual_callback_cannot_break_playback(monkeypatch) -> None:
    class FakeInput:
        def write(self, value):
            del value

        def close(self):
            pass

    class FakeProcess:
        stdin = FakeInput()
        stdout = iter(["ATO_LEVEL:50\n"])

        def wait(self):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    WindowsSpeechPlayer().speak(
        "still speaks", on_level=lambda level: (_ for _ in ()).throw(RuntimeError())
    )
