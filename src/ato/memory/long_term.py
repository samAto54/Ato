"""Durable SQLite-backed long-term facts with bounded lexical retrieval."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ato.brain.memory import MemoryItem
from ato.exceptions import MemoryStoreError

MAX_FACT_CHARS = 2_000
MAX_STORED_FACTS = 10_000
MAX_SEARCH_CANDIDATES = 1_000
WORD_PATTERN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "do",
    "does",
    "for",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "the",
    "to",
    "what",
    "when",
    "where",
    "who",
    "why",
}
SENSITIVE_PATTERN = re.compile(
    r"\b(password|passcode|api[_ -]?key|access[_ -]?token|secret[_ -]?key)\b|\bsk-[a-z0-9_-]{8,}",
    re.IGNORECASE,
)


class SqliteLongTermMemory:
    """Store explicit facts locally and retrieve them without embeddings."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._initialize()

    def remember(self, content: str) -> MemoryItem:
        """Persist one explicit, non-sensitive user fact."""
        cleaned = _validate_content(content)
        try:
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT id FROM memories WHERE content = ?", (cleaned,)
                ).fetchone()
                if existing is not None:
                    return MemoryItem(int(existing[0]), cleaned)
                count = connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                if count >= MAX_STORED_FACTS:
                    raise MemoryStoreError("Long-term memory has reached its storage limit.")
                cursor = connection.execute(
                    "INSERT INTO memories(content, created_at) VALUES (?, ?)",
                    (cleaned, datetime.now(UTC).isoformat()),
                )
                memory_id = int(cursor.lastrowid)
        except sqlite3.Error as exc:
            raise MemoryStoreError("Could not save long-term memory.") from exc
        return MemoryItem(memory_id, cleaned)

    def update(self, memory_id: int, content: str) -> MemoryItem | None:
        """Replace one explicitly selected fact while preserving its identifier."""
        if memory_id < 1:
            raise ValueError("memory_id must be positive.")
        cleaned = _validate_content(content)
        try:
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT id FROM memories WHERE content = ? AND id != ?",
                    (cleaned, memory_id),
                ).fetchone()
                if existing is not None:
                    raise MemoryStoreError(
                        f"That fact is already stored as memory {int(existing[0])}."
                    )
                cursor = connection.execute(
                    "UPDATE memories SET content = ? WHERE id = ?", (cleaned, memory_id)
                )
        except MemoryStoreError:
            raise
        except sqlite3.Error as exc:
            raise MemoryStoreError("Could not update long-term memory.") from exc
        if cursor.rowcount == 0:
            return None
        return MemoryItem(memory_id, cleaned)

    def list_memories(self, limit: int = 50) -> tuple[MemoryItem, ...]:
        """List recent durable facts in newest-first order."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT id, content FROM memories ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        except sqlite3.Error as exc:
            raise MemoryStoreError("Could not list long-term memories.") from exc
        return tuple(MemoryItem(int(row[0]), str(row[1])) for row in rows)

    def forget(self, memory_id: int) -> bool:
        """Delete one explicitly selected fact by identifier."""
        if memory_id < 1:
            raise ValueError("memory_id must be positive.")
        try:
            with self._connect() as connection:
                cursor = connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        except sqlite3.Error as exc:
            raise MemoryStoreError("Could not delete long-term memory.") from exc
        return cursor.rowcount > 0

    def search(self, query: str, limit: int = 5) -> tuple[MemoryItem, ...]:
        """Rank a bounded candidate set by case-insensitive word overlap."""
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20.")
        query_terms = {
            term for term in WORD_PATTERN.findall(query.casefold()) if term not in STOP_WORDS
        }
        if not query_terms:
            return ()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT id, content FROM memories ORDER BY id DESC LIMIT ?",
                    (MAX_SEARCH_CANDIDATES,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise MemoryStoreError("Could not search long-term memory.") from exc

        ranked: list[tuple[float, int, MemoryItem]] = []
        for row in rows:
            item = MemoryItem(int(row[0]), str(row[1]))
            terms = {
                term
                for term in WORD_PATTERN.findall(item.content.casefold())
                if term not in STOP_WORDS
            }
            overlap = query_terms & terms
            if overlap:
                score = len(overlap) / len(query_terms)
                ranked.append((score, item.id, item))
        ranked.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        return tuple(entry[2] for entry in ranked[:limit])

    def _initialize(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS memories ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "content TEXT NOT NULL UNIQUE, "
                    "created_at TEXT NOT NULL)"
                )
        except (OSError, sqlite3.Error) as exc:
            raise MemoryStoreError("Could not initialize long-term memory.") from exc

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)


def _validate_content(content: str) -> str:
    """Normalize and validate a fact before it enters durable memory."""
    cleaned = " ".join(content.split())
    if not cleaned:
        raise MemoryStoreError("Long-term memory cannot be empty.")
    if len(cleaned) > MAX_FACT_CHARS:
        raise MemoryStoreError(f"Long-term memory exceeds {MAX_FACT_CHARS} characters.")
    if SENSITIVE_PATTERN.search(cleaned):
        raise MemoryStoreError("Refusing to store a possible password, API key, token, or secret.")
    return cleaned
