import json
from pathlib import Path

import pytest

from ato.exceptions import ToolError
from ato.security import AuditLogger, PermissionManager
from ato.ui.voice_turn import DesktopVoiceTurnService


class Recorder:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.durations = []

    def record(self, duration_seconds: int, *, on_level=None) -> Path:
        self.durations.append(duration_seconds)
        if on_level is not None:
            on_level(0.65)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(b"RIFF")
        return self.path


class Transcriber:
    def __init__(self, transcript: str = "draft from voice") -> None:
        self.transcript = transcript
        self.paths = []

    def transcribe_file(self, path: Path) -> str:
        self.paths.append(path)
        return self.transcript


def build_service(tmp_path, decisions, *, path=None, transcript="draft from voice"):
    recorder = Recorder(path or tmp_path / "data" / "audio" / "voice.wav")
    transcriber = Transcriber(transcript)
    requests = []
    decision_iterator = iter(decisions)
    service = DesktopVoiceTurnService(
        recorder,
        transcriber,
        PermissionManager(lambda request: requests.append(request) or next(decision_iterator)),
        AuditLogger(tmp_path / "data" / "audit.jsonl"),
        tmp_path,
    )
    return service, recorder, transcriber, requests


def test_voice_turn_requires_two_permissions_and_returns_draft(tmp_path) -> None:
    service, recorder, transcriber, requests = build_service(tmp_path, [True, True])
    states = []
    levels = []
    transcript = service.capture(
        5,
        on_recording=lambda: states.append("listening"),
        on_audio_level=levels.append,
        on_transcription_request=lambda: states.append("transcription_permission"),
        on_transcribing=lambda: states.append("processing"),
    )
    assert transcript == "draft from voice"
    assert states == ["listening", "transcription_permission", "processing"]
    assert levels == [0.65]
    assert recorder.durations == [5]
    assert len(transcriber.paths) == 1
    assert [(request.tool_name, request.level.value) for request in requests] == [
        ("record_microphone", "CRITICAL"),
        ("transcribe_audio", "HIGH"),
    ]
    audit_text = (tmp_path / "data" / "audit.jsonl").read_text()
    events = [json.loads(line) for line in audit_text.splitlines()]
    assert [event["decision"] for event in events] == ["ALLOW", "ALLOW"]
    assert "draft from voice" not in audit_text


def test_voice_turn_transcription_denial_never_calls_transcriber(tmp_path) -> None:
    service, recorder, transcriber, _ = build_service(tmp_path, [True, False])
    with pytest.raises(ToolError, match="transcription"):
        service.capture(3)
    assert recorder.durations == [3]
    assert transcriber.paths == []


def test_voice_turn_rejects_unsafe_recording_path(tmp_path) -> None:
    service, _, transcriber, _ = build_service(tmp_path, [True], path=tmp_path / "outside.wav")
    with pytest.raises(ToolError, match="unsafe recording path"):
        service.capture(2)
    assert transcriber.paths == []


@pytest.mark.parametrize("duration", [0, 121, True, 1.5])
def test_voice_turn_validates_duration_before_permission(tmp_path, duration) -> None:
    service, recorder, _, requests = build_service(tmp_path, [])
    with pytest.raises(ToolError, match="duration"):
        service.capture(duration)
    assert recorder.durations == []
    assert requests == []
