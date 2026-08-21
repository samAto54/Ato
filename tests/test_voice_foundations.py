import pytest

from ato.exceptions import AtoError
from ato.voice import AudioPayload, validate_synthesis_text


def test_audio_payload_accepts_bounded_supported_audio() -> None:
    payload = AudioPayload(b"RIFFdata", "audio/wav", 1.5)
    assert payload.duration_seconds == 1.5


@pytest.mark.parametrize(
    ("data", "media_type", "duration", "message"),
    [
        (b"", "audio/wav", 1, "1-10,000,000"),
        (b"x", "audio/flac", 1, "not supported"),
        (b"x", "audio/wav", 0, "greater than zero"),
        (b"x", "audio/wav", 121, "at most 120"),
    ],
)
def test_audio_payload_rejects_invalid_boundaries(data, media_type, duration, message) -> None:
    with pytest.raises(AtoError, match=message):
        AudioPayload(data, media_type, duration)


def test_synthesis_text_is_normalized_and_bounded() -> None:
    assert validate_synthesis_text("  hello  ") == "hello"
    with pytest.raises(AtoError, match="1-4,000"):
        validate_synthesis_text("   ")
    with pytest.raises(AtoError, match="control"):
        validate_synthesis_text("bad\x00text")
