"""Original desktop color themes with no third-party artwork or assets."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ThemeId(StrEnum):
    STANDARD = "standard"
    ATO_HUD = "ato_hud"


@dataclass(frozen=True, slots=True)
class UiTheme:
    id: ThemeId
    display_name: str
    background: str
    panel: str
    panel_alt: str
    text: str
    muted_text: str
    accent: str
    accent_secondary: str
    warning: str
    danger: str
    input_background: str
    border: str
    font_family: str
    heading_family: str


STANDARD_THEME = UiTheme(
    id=ThemeId.STANDARD,
    display_name="Standard",
    background="#F4F6F8",
    panel="#FFFFFF",
    panel_alt="#E9EEF3",
    text="#18212B",
    muted_text="#5F6B78",
    accent="#2563EB",
    accent_secondary="#0F766E",
    warning="#B45309",
    danger="#B91C1C",
    input_background="#FFFFFF",
    border="#CBD5E1",
    font_family="Segoe UI",
    heading_family="Segoe UI Semibold",
)

ATO_HUD_THEME = UiTheme(
    id=ThemeId.ATO_HUD,
    display_name="Ato HUD",
    background="#020A0F",
    panel="#061821",
    panel_alt="#0A2530",
    text="#D8FBFF",
    muted_text="#70AEB8",
    accent="#1DE9FF",
    accent_secondary="#FFB547",
    warning="#FFB547",
    danger="#FF5D73",
    input_background="#031117",
    border="#16788A",
    font_family="Consolas",
    heading_family="Bahnschrift SemiBold",
)

THEMES = {
    ThemeId.STANDARD: STANDARD_THEME,
    ThemeId.ATO_HUD: ATO_HUD_THEME,
}


def get_theme(value: ThemeId | str) -> UiTheme:
    """Resolve one allowlisted theme identifier without loading external assets."""
    try:
        theme_id = value if isinstance(value, ThemeId) else ThemeId(value.strip().casefold())
    except (AttributeError, ValueError) as exc:
        raise ValueError("UI theme must be 'standard' or 'ato_hud'.") from exc
    return THEMES[theme_id]


def alternate_theme(current: ThemeId | str) -> UiTheme:
    """Return the other built-in theme for a two-state interface toggle."""
    selected = get_theme(current)
    target = ThemeId.ATO_HUD if selected.id is ThemeId.STANDARD else ThemeId.STANDARD
    return THEMES[target]
