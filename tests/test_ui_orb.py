import math

import pytest

from ato.ui.orb import OrbMotion, _ellipse_point, blend_motion, motion_profile, visual_profile
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


def test_motion_profiles_turn_discrete_effects_into_fade_channels() -> None:
    idle = motion_profile(visual_profile(AtoVisualState.IDLE))
    speaking = motion_profile(visual_profile(AtoVisualState.SPEAKING))
    halfway = blend_motion(idle, speaking, 0.5)

    assert halfway.speed == pytest.approx((idle.speed + speaking.speed) / 2)
    assert halfway.energy == pytest.approx((idle.energy + speaking.energy) / 2)
    assert halfway.waveform == pytest.approx(0.5)
    assert halfway.scan == pytest.approx(0.0)


def test_motion_blending_clamps_amount() -> None:
    current = OrbMotion(0.2, 0.03, 0.3, 0.0, 0.0)
    target = OrbMotion(1.2, 0.1, 0.9, 1.0, 1.0)

    assert blend_motion(current, target, -1) == current
    assert blend_motion(current, target, 2) == target
