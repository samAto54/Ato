"""Fixed-action Research palette for Ato's desktop interface."""

from __future__ import annotations

from collections.abc import Callable

from ato.ui.action_palette import FixedAction, FixedActionPalette
from ato.ui.themes import UiTheme

RESEARCH_ACTIONS = (
    FixedAction("search", "SEARCH WEB", "Query the configured public search provider", "MEDIUM"),
    FixedAction("fetch", "FETCH RESULT", "Read one reviewed HTTPS search result", "MEDIUM"),
)


class ResearchActionPalette(FixedActionPalette):
    def __init__(
        self,
        parent,
        theme: UiTheme,
        on_select: Callable[[str], None],
        *,
        can_fetch: bool,
    ) -> None:
        super().__init__(
            parent,
            theme,
            on_select,
            title="Ato Research Controls",
            heading="RESEARCH CONTROL",
            subtitle="External content remains untrusted evidence",
            actions=RESEARCH_ACTIONS,
            disabled=() if can_fetch else ("fetch",),
            width=48,
        )
