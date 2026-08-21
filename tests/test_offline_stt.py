import json

import pytest

from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry
from ato.voice import FasterWhisperTranscriber


class Transcriber:
    def __init__(self) -> None:
        self.paths = []

    def transcribe_file(self, path):
        self.paths.append(path)
        return "hello from local audio"


def test_transcription_requires_confirmation_and_stays_in_audio_directory(tmp_path) -> None:
    audio = tmp_path / "data" / "audio" / "clip.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"RIFF")
    transcriber = Transcriber()
    denied = build_phase3_registry(tmp_path, transcriber=transcriber)
    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute("transcribe_audio", {"path": "data/audio/clip.wav"})
    assert transcriber.paths == []

    registry = build_phase3_registry(
        tmp_path, PermissionManager(lambda request: True), transcriber=transcriber
    )
    result = json.loads(
        registry.execute("transcribe_audio", {"path": "data/audio/clip.wav"})
    )
    assert result["offline"] is True
    assert result["transcript"] == "hello from local audio"


def test_transcription_rejects_files_outside_audio_directory(tmp_path) -> None:
    path = tmp_path / "clip.wav"
    path.write_bytes(b"RIFF")
    registry = build_phase3_registry(
        tmp_path, PermissionManager(lambda request: True), transcriber=Transcriber()
    )
    with pytest.raises(ToolError, match="restricted"):
        registry.execute("transcribe_audio", {"path": "clip.wav"})


def test_local_model_path_must_be_existing_directory(tmp_path) -> None:
    with pytest.raises(ToolError, match="existing local model directory"):
        FasterWhisperTranscriber(tmp_path / "missing")
    model = tmp_path / "model"
    model.mkdir()
    assert FasterWhisperTranscriber(model).model_path == model.resolve()
