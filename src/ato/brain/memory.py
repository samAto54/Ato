"""Provider-neutral long-term memory retrieval contract."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MemoryItem:
    """One durable user-approved fact available to the Agent Core."""

    id: int
    content: str


class MemoryRetriever(Protocol):
    """Retrieve long-term memories relevant to a query."""

    def search(self, query: str, limit: int = 5) -> Sequence[MemoryItem]:
        """Return the most relevant bounded set of facts."""
        ...
