import json
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry
from ato.voice.microphone import SAMPLE_RATE, SoundDeviceRecorder


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


def test_sounddevice_recorder_reports_normalized_transient_levels(tmp_path, monkeypatch) -> None:
    samples = np.full((SAMPLE_RATE, 1), 3_276, dtype=np.int16)
    fake_sounddevice = SimpleNamespace(
        rec=lambda *args, **kwargs: samples,
        sleep=lambda milliseconds: None,
        wait=lambda: None,
        stop=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sounddevice)
    levels = []

    path = SoundDeviceRecorder(tmp_path).record(1, on_level=levels.append)

    assert path.is_file()
    assert levels[:-1] == pytest.approx([1.0] * 20, abs=0.01)
    assert levels[-1] == 0.0


def test_sounddevice_recorder_can_stop_after_speech_then_silence(tmp_path, monkeypatch) -> None:
    samples = np.zeros((SAMPLE_RATE * 5, 1), dtype=np.int16)
    samples[: SAMPLE_RATE // 2] = 6_000
    stopped = []
    fake_sounddevice = SimpleNamespace(
        rec=lambda *args, **kwargs: samples,
        sleep=lambda milliseconds: None,
        wait=lambda: None,
        stop=lambda: stopped.append(True),
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sounddevice)

    path = SoundDeviceRecorder(tmp_path).record(5, stop_on_silence=True)

    with wave.open(str(path), "rb") as recording:
        recorded_seconds = recording.getnframes() / recording.getframerate()
    assert stopped == [True]
    assert 1.5 <= recorded_seconds < 2.0
