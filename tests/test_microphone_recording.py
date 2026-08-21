import json
from pathlib import Path

import pytest

from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry


class Recorder:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.durations = []

    def record(self, duration_seconds: int) -> Path:
        self.durations.append(duration_seconds)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(b"RIFF")
        return self.path


def test_microphone_requires_critical_confirmation_then_records(tmp_path) -> None:
    recorder = Recorder(tmp_path / "data" / "audio" / "clip.wav")
    denied = build_phase3_registry(tmp_path, microphone_recorder=recorder)
    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute("record_microphone", {"duration_seconds": 3})
    assert recorder.durations == []

    seen = []
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: seen.append(request) or True),
        microphone_recorder=recorder,
    )
    result = json.loads(
        registry.execute("record_microphone", {"duration_seconds": 3})
    )
    assert result["path"] == "data/audio/clip.wav"
    assert recorder.durations == [3]
    assert seen[0].level.value == "CRITICAL"


def test_microphone_rejects_provider_path_outside_workspace(tmp_path) -> None:
    recorder = Recorder(tmp_path.parent / "outside.wav")
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        microphone_recorder=recorder,
    )
    with pytest.raises(ToolError, match="unsafe recording path"):
        registry.execute("record_microphone", {"duration_seconds": 1})


def test_microphone_duration_is_schema_bounded(tmp_path) -> None:
    recorder = Recorder(tmp_path / "clip.wav")
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        microphone_recorder=recorder,
    )
    with pytest.raises(ToolError, match="below the minimum"):
        registry.execute("record_microphone", {"duration_seconds": 0})
    with pytest.raises(ToolError, match="exceeds the maximum"):
        registry.execute("record_microphone", {"duration_seconds": 121})
