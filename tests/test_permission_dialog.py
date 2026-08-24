from ato.ui.permission_dialog import permission_color
from ato.ui.themes import ATO_HUD_THEME


def test_permission_colors_escalate_with_risk() -> None:
    theme = ATO_HUD_THEME

    assert permission_color(theme, "LOW") == theme.accent
    assert permission_color(theme, "MEDIUM") == theme.accent_secondary
    assert permission_color(theme, "HIGH") == theme.warning
    assert permission_color(theme, "CRITICAL") == theme.danger


def test_unknown_permission_color_uses_fail_safe_visual_emphasis() -> None:
    assert permission_color(ATO_HUD_THEME, "unknown") == ATO_HUD_THEME.danger
