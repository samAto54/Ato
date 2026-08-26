"""Explicit one-shot microphone recording with a lazy optional backend."""

from __future__ import annotations

import wave
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from ato.exceptions import ToolError

SAMPLE_RATE = 16_000
MAX_RECORDING_SECONDS = 120
LEVEL_UPDATES_PER_SECOND = 20
SPEECH_LEVEL_THRESHOLD = 0.08
SILENCE_SECONDS_TO_STOP = 1.1


class MicrophoneRecorder(Protocol):
    def record(
        self,
        duration_seconds: int,
        *,
        on_level: Callable[[float], None] | None = None,
        stop_on_silence: bool = False,
    ) -> Path:
        """Record one explicit bounded clip and return its WAV path."""
        ...


class SoundDeviceRecorder:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def record(
        self,
        duration_seconds: int,
        *,
        on_level: Callable[[float], None] | None = None,
        stop_on_silence: bool = False,
    ) -> Path:
        if not 1 <= duration_seconds <= MAX_RECORDING_SECONDS:
            raise ToolError("Recording duration must be between 1 and 120 seconds.")
        try:
            import sounddevice  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ToolError(
                "Microphone recording requires the optional sounddevice package."
            ) from exc
        try:
            samples = sounddevice.rec(
                duration_seconds * SAMPLE_RATE,
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
            )
            recorded_frames = len(samples)
            if on_level is not None or stop_on_silence:
                window_frames = SAMPLE_RATE // LEVEL_UPDATES_PER_SECOND
                total_frames = len(samples)
                speech_started = False
                silent_windows = 0
                silence_limit = round(SILENCE_SECONDS_TO_STOP * LEVEL_UPDATES_PER_SECOND)
                for start in range(0, total_frames, window_frames):
                    end = min(start + window_frames, total_frames)
                    sounddevice.sleep(round(1000 / LEVEL_UPDATES_PER_SECOND))
                    block = samples[start:end]
                    if len(block):
                        normalized = block.astype("float64") / 32768.0
                        rms = float((normalized * normalized).mean() ** 0.5)
                        level = min(1.0, rms * 10.0)
                        if on_level is not None:
                            try:
                                on_level(level)
                            except Exception:
                                pass
                        if level >= SPEECH_LEVEL_THRESHOLD:
                            speech_started = True
                            silent_windows = 0
                        elif speech_started:
                            silent_windows += 1
                        if stop_on_silence and speech_started and silent_windows >= silence_limit:
                            recorded_frames = end
                            sounddevice.stop()
                            break
            sounddevice.wait()
            if on_level is not None:
                try:
                    on_level(0.0)
                except Exception:
                    pass
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"recording-{uuid4().hex}.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(SAMPLE_RATE)
                output.writeframes(samples[:recorded_frames].tobytes())
        except Exception as exc:
            raise ToolError("Microphone recording failed safely.") from exc
        return path
