"""Safe data boundaries for future speech providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ato.exceptions import AtoError

MAX_AUDIO_BYTES = 10_000_000
MAX_AUDIO_SECONDS = 120.0
MAX_SYNTHESIS_CHARS = 4_000
SUPPORTED_AUDIO_TYPES = {"audio/wav", "audio/mpeg", "audio/ogg"}


@dataclass(frozen=True, slots=True)
class AudioPayload:
    data: bytes
    media_type: str
    duration_seconds: float

    def __post_init__(self) -> None:
        if not self.data or len(self.data) > MAX_AUDIO_BYTES:
            raise AtoError("Audio payload must contain 1-10,000,000 bytes.")
        if self.media_type not in SUPPORTED_AUDIO_TYPES:
            raise AtoError("Audio payload type is not supported.")
        if not 0 < self.duration_seconds <= MAX_AUDIO_SECONDS:
            raise AtoError("Audio duration must be greater than zero and at most 120 seconds.")


class SpeechRecognizer(Protocol):
    def transcribe(self, audio: AudioPayload) -> str:
        """Convert one bounded audio payload to text."""
        ...


class SpeechSynthesizer(Protocol):
    def synthesize(self, text: str) -> AudioPayload:
        """Convert bounded text to an audio payload."""
        ...


def validate_synthesis_text(text: str) -> str:
    normalized = text.strip()
    if not normalized or len(normalized) > MAX_SYNTHESIS_CHARS:
        raise AtoError("Speech synthesis text must contain 1-4,000 characters.")
    if any(ord(character) == 0 for character in normalized):
        raise AtoError("Speech synthesis text contains unsupported control characters.")
    return normalized
