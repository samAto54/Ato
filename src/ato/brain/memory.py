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
    source: str = "long-term memory"


class MemoryRetriever(Protocol):
    """Retrieve long-term memories relevant to a query."""

    def search(self, query: str, limit: int = 5) -> Sequence[MemoryItem]:
        """Return the most relevant bounded set of facts."""
        ...


class CompositeMemoryRetriever:
    """Combine multiple retrievers while giving each source a chance to contribute."""

    def __init__(self, *retrievers: MemoryRetriever) -> None:
        self._retrievers = retrievers

    def search(self, query: str, limit: int = 5) -> tuple[MemoryItem, ...]:
        groups = [tuple(retriever.search(query, limit=limit)) for retriever in self._retrievers]
        combined: list[MemoryItem] = []
        for index in range(limit):
            for group in groups:
                if index < len(group):
                    combined.append(group[index])
                    if len(combined) >= limit:
                        return tuple(combined)
        return tuple(combined)
