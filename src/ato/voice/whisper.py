"""Offline transcription using an explicitly local faster-whisper model."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ato.exceptions import ToolError

MAX_TRANSCRIPT_CHARS = 20_000


class FileTranscriber(Protocol):
    def transcribe_file(self, path: Path) -> str:
        """Transcribe one validated local audio file."""
        ...


class FasterWhisperTranscriber:
    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path.resolve()
        if not self.model_path.is_dir():
            raise ToolError("ATO_STT_MODEL_PATH must be an existing local model directory.")

    def transcribe_file(self, path: Path) -> str:
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ToolError(
                "Offline transcription requires the optional faster-whisper package."
            ) from exc
        try:
            model = WhisperModel(str(self.model_path), device="cpu", compute_type="int8")
            segments, _ = model.transcribe(str(path), beam_size=1)
            transcript = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:
            raise ToolError("Offline transcription failed safely.") from exc
        if not transcript:
            raise ToolError("Offline transcription produced no text.")
        if len(transcript) > MAX_TRANSCRIPT_CHARS:
            raise ToolError("Transcript exceeds the 20,000-character limit.")
        return transcript
