"""Fixed-action Knowledge palette for Ato's desktop interface."""

from __future__ import annotations

from collections.abc import Callable

from ato.ui.action_palette import FixedAction, FixedActionPalette
from ato.ui.themes import UiTheme

KNOWLEDGE_ACTIONS = (
    FixedAction("import", "IMPORT DOCUMENT", "Index one workspace file", "HIGH"),
    FixedAction("refresh", "REFRESH INDEX", "Reload document metadata", "LOW"),
    FixedAction("remove", "REMOVE DOCUMENT", "Delete indexed excerpts by ID", "HIGH"),
)


class KnowledgeActionPalette(FixedActionPalette):
    def __init__(self, parent, theme: UiTheme, on_select: Callable[[str], None]) -> None:
        super().__init__(
            parent,
            theme,
            on_select,
            title="Ato Knowledge Controls",
            heading="KNOWLEDGE CONTROL",
            subtitle="Local bounded RAG index",
            actions=KNOWLEDGE_ACTIONS,
            width=42,
        )
