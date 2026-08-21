from ato.ui.orb import visual_profile
from ato.ui.state import AtoVisualState


def test_orb_profiles_visibly_distinguish_backend_states() -> None:
    idle = visual_profile(AtoVisualState.IDLE)
    listening = visual_profile(AtoVisualState.LISTENING)
    processing = visual_profile(AtoVisualState.PROCESSING)
    speaking = visual_profile(AtoVisualState.SPEAKING)
    assert processing.speed > idle.speed
    assert listening.waveform is True
    assert speaking.waveform is True
    assert processing.scan is True
    assert speaking.pulse > idle.pulse
