"""Fixed-action long-term Memory palette for Ato's desktop interface."""

from __future__ import annotations

from collections.abc import Callable

from ato.ui.action_palette import FixedAction, FixedActionPalette
from ato.ui.themes import UiTheme

MEMORY_ACTIONS = (
    FixedAction("remember", "REMEMBER", "Store one explicit fact", "HIGH"),
    FixedAction("edit", "EDIT", "Replace one fact; preserve its ID", "HIGH"),
    FixedAction("refresh", "REFRESH", "Reload memory metadata", "LOW"),
    FixedAction("archive", "ARCHIVE", "Hide one memory from retrieval", "HIGH"),
    FixedAction("restore", "RESTORE", "Reactivate one archived memory", "HIGH"),
    FixedAction("expire", "SET EXPIRATION", "Exclude after a bounded number of days", "HIGH"),
    FixedAction("clear_expiration", "CLEAR EXPIRATION", "Remove a memory expiry date", "HIGH"),
    FixedAction("forget", "FORGET", "Permanently delete one memory", "CRITICAL"),
)


class MemoryActionPalette(FixedActionPalette):
    def __init__(self, parent, theme: UiTheme, on_select: Callable[[str], None]) -> None:
        super().__init__(
            parent,
            theme,
            on_select,
            title="Ato Memory Controls",
            heading="MEMORY CONTROL",
            subtitle="Explicit local long-term memory",
            actions=MEMORY_ACTIONS,
            width=42,
        )
