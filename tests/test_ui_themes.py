import re

import pytest

from ato.ui.themes import ThemeId, alternate_theme, get_theme


def test_standard_and_ato_hud_themes_are_complete_original_palettes() -> None:
    standard = get_theme("standard")
    hud = get_theme(ThemeId.ATO_HUD)
    assert standard.display_name == "Standard"
    assert hud.display_name == "Ato HUD"
    assert standard != hud
    for theme in (standard, hud):
        for color in (
            theme.background,
            theme.panel,
            theme.panel_alt,
            theme.text,
            theme.muted_text,
            theme.accent,
            theme.accent_secondary,
            theme.warning,
            theme.danger,
            theme.input_background,
            theme.border,
        ):
            assert re.fullmatch(r"#[0-9A-F]{6}", color)


def test_theme_toggle_is_deterministic() -> None:
    assert alternate_theme("standard").id is ThemeId.ATO_HUD
    assert alternate_theme("ato_hud").id is ThemeId.STANDARD


def test_unknown_theme_is_rejected() -> None:
    with pytest.raises(ValueError, match="standard.*ato_hud"):
        get_theme("jarvis")
