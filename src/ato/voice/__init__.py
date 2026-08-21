"""Provider-neutral voice contracts and bounded payloads."""

from ato.voice.base import (
    AudioPayload,
    SpeechRecognizer,
    SpeechSynthesizer,
    validate_synthesis_text,
)
from ato.voice.microphone import MicrophoneRecorder, SoundDeviceRecorder
from ato.voice.windows import WindowsSpeechPlayer

__all__ = [
    "AudioPayload",
    "SpeechRecognizer",
    "SpeechSynthesizer",
    "SoundDeviceRecorder",
    "MicrophoneRecorder",
    "WindowsSpeechPlayer",
    "validate_synthesis_text",
]
