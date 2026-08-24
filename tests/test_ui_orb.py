import math

import pytest

from ato.ui.orb import _ellipse_point, visual_profile
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


def test_ellipse_geometry_supports_orbit_planes_and_tick_rings() -> None:
    assert _ellipse_point(100, 50, 20, 10, 0) == pytest.approx((120, 50))
    assert _ellipse_point(100, 50, 20, 10, math.pi / 2) == pytest.approx((100, 60))
