"""Durable SQLite-backed long-term facts with bounded lexical retrieval."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
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


class MemoryCategory(StrEnum):
    """User-visible categories for durable personal context."""

    FACT = "fact"
    PREFERENCE = "preference"
    PROJECT = "project"
    DECISION = "decision"


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """Lifecycle metadata for one durable memory."""

    id: int
    content: str
    category: MemoryCategory
    created_at: str
    updated_at: str
    last_retrieved_at: str | None
    archived_at: str | None
    expires_at: str | None

    def as_item(self) -> MemoryItem:
        return _memory_item(self.id, self.content, self.category)


class SqliteLongTermMemory:
    """Store explicit facts locally and retrieve them without embeddings."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._initialize()

    def remember(
        self, content: str, category: MemoryCategory | str = MemoryCategory.FACT
    ) -> MemoryItem:
        """Persist one explicit, non-sensitive user fact."""
        cleaned = _validate_content(content)
        normalized_category = _validate_category(category)
        try:
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT id, category, archived_at, expires_at FROM memories "
                    "WHERE content = ?",
                    (cleaned,),
                ).fetchone()
                if existing is not None:
                    if existing[2] is not None or _is_expired(existing[3]):
                        raise MemoryStoreError(
                            f"That fact is stored as inactive memory {int(existing[0])}; "
                            "restore it or clear its expiration instead."
                        )
                    return _memory_item(int(existing[0]), cleaned, str(existing[1]))
                count = connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                if count >= MAX_STORED_FACTS:
                    raise MemoryStoreError("Long-term memory has reached its storage limit.")
                cursor = connection.execute(
                    "INSERT INTO memories(content, category, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (cleaned, normalized_category.value, _now(), _now()),
                )
                memory_id = int(cursor.lastrowid)
        except sqlite3.Error as exc:
            raise MemoryStoreError("Could not save long-term memory.") from exc
        return _memory_item(memory_id, cleaned, normalized_category)

    def update(
        self,
        memory_id: int,
        content: str,
        category: MemoryCategory | str | None = None,
    ) -> MemoryItem | None:
        """Replace one explicitly selected fact while preserving its identifier."""
        if memory_id < 1:
            raise ValueError("memory_id must be positive.")
        cleaned = _validate_content(content)
        normalized_category = None if category is None else _validate_category(category)
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
                current = connection.execute(
                    "SELECT category FROM memories WHERE id = ?", (memory_id,)
                ).fetchone()
                if current is None:
                    return None
                selected_category = (
                    normalized_category.value
                    if normalized_category is not None
                    else str(current[0])
                )
                connection.execute(
                    "UPDATE memories SET content = ?, category = ?, updated_at = ? WHERE id = ?",
                    (cleaned, selected_category, _now(), memory_id),
                )
        except MemoryStoreError:
            raise
        except sqlite3.Error as exc:
            raise MemoryStoreError("Could not update long-term memory.") from exc
        return _memory_item(memory_id, cleaned, selected_category)

    def list_memories(self, limit: int = 50) -> tuple[MemoryItem, ...]:
        """List recent active durable facts in newest-first order."""
        return tuple(record.as_item() for record in self.list_records(limit=limit))

    def list_records(
        self, limit: int = 50, *, include_inactive: bool = False
    ) -> tuple[MemoryRecord, ...]:
        """List bounded memory records with user-visible lifecycle metadata."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        where = "" if include_inactive else _active_where()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT id, content, category, created_at, updated_at, "
                    "last_retrieved_at, archived_at, expires_at FROM memories "
                    f"{where} ORDER BY id DESC LIMIT ?",
                    (*(() if include_inactive else (_now(),)), limit),
                ).fetchall()
        except sqlite3.Error as exc:
            raise MemoryStoreError("Could not list long-term memories.") from exc
        return tuple(_record_from_row(row) for row in rows)

    def archive(self, memory_id: int) -> bool:
        """Exclude one memory from normal listing and retrieval without deleting it."""
        return self._set_archived(memory_id, _now())

    def restore(self, memory_id: int) -> bool:
        """Restore one archived memory; any independent expiration remains in effect."""
        return self._set_archived(memory_id, None)

    def _set_archived(self, memory_id: int, archived_at: str | None) -> bool:
        if memory_id < 1:
            raise ValueError("memory_id must be positive.")
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "UPDATE memories SET archived_at = ?, updated_at = ? WHERE id = ?",
                    (archived_at, _now(), memory_id),
                )
        except sqlite3.Error as exc:
            raise MemoryStoreError("Could not change long-term memory archive state.") from exc
        return cursor.rowcount > 0

    def set_expiration(self, memory_id: int, expires_at: datetime | None) -> bool:
        """Set or clear an absolute UTC expiration for one memory."""
        if memory_id < 1:
            raise ValueError("memory_id must be positive.")
        if expires_at is not None:
            if expires_at.tzinfo is None:
                raise ValueError("expires_at must include a timezone.")
            normalized = expires_at.astimezone(UTC).isoformat()
        else:
            normalized = None
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "UPDATE memories SET expires_at = ?, updated_at = ? WHERE id = ?",
                    (normalized, _now(), memory_id),
                )
        except sqlite3.Error as exc:
            raise MemoryStoreError("Could not change long-term memory expiration.") from exc
        return cursor.rowcount > 0

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
        """Rank a bounded candidate set with deterministic lexical and category signals."""
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20.")
        query_terms = _meaningful_terms(query)
        if not query_terms:
            return ()
        query_set = set(query_terms)
        intended_categories = _infer_categories(query_terms)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT id, content, category FROM memories "
                    f"{_active_where()} ORDER BY id DESC LIMIT ?",
                    (_now(), MAX_SEARCH_CANDIDATES),
                ).fetchall()
        except sqlite3.Error as exc:
            raise MemoryStoreError("Could not search long-term memory.") from exc

        ranked: list[tuple[float, int, MemoryItem]] = []
        for row in rows:
            item = _memory_item(int(row[0]), str(row[1]), str(row[2]))
            content_terms = _meaningful_terms(item.content)
            overlap = query_set & set(content_terms)
            if overlap:
                score = _relevance_score(
                    query_terms,
                    content_terms,
                    overlap,
                    _validate_category(str(row[2])),
                    intended_categories,
                )
                ranked.append((score, item.id, item))
        ranked.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        selected: list[MemoryItem] = []
        seen: set[str] = set()
        for _, _, item in ranked:
            key = _dedupe_key(item.content)
            if key in seen:
                continue
            seen.add(key)
            selected.append(item)
            if len(selected) >= limit:
                break
        results = tuple(selected)
        if results:
            try:
                with self._connect() as connection:
                    connection.executemany(
                        "UPDATE memories SET last_retrieved_at = ? WHERE id = ?",
                        [(_now(), item.id) for item in results],
                    )
            except sqlite3.Error as exc:
                raise MemoryStoreError("Could not update memory retrieval metadata.") from exc
        return results

    def _initialize(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS memories ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "content TEXT NOT NULL UNIQUE, "
                    "category TEXT NOT NULL DEFAULT 'fact', "
                    "created_at TEXT NOT NULL, "
                    "updated_at TEXT NOT NULL, "
                    "last_retrieved_at TEXT, "
                    "archived_at TEXT, "
                    "expires_at TEXT)"
                )
                columns = {
                    str(row[1]) for row in connection.execute("PRAGMA table_info(memories)")
                }
                if "category" not in columns:
                    connection.execute(
                        "ALTER TABLE memories ADD COLUMN category TEXT NOT NULL DEFAULT 'fact'"
                    )
                if "updated_at" not in columns:
                    connection.execute("ALTER TABLE memories ADD COLUMN updated_at TEXT")
                    connection.execute("UPDATE memories SET updated_at = created_at")
                for name in ("last_retrieved_at", "archived_at", "expires_at"):
                    if name not in columns:
                        connection.execute(f"ALTER TABLE memories ADD COLUMN {name} TEXT")
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


def _validate_category(category: MemoryCategory | str) -> MemoryCategory:
    try:
        return MemoryCategory(str(category).strip().casefold())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in MemoryCategory)
        raise MemoryStoreError(f"Memory category must be one of: {allowed}.") from exc


def _memory_item(
    memory_id: int, content: str, category: MemoryCategory | str
) -> MemoryItem:
    normalized = _validate_category(category)
    return MemoryItem(memory_id, content, f"long-term memory:{normalized.value}")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _active_where() -> str:
    return "WHERE archived_at IS NULL AND (expires_at IS NULL OR expires_at > ?)"


def _is_expired(value: object) -> bool:
    return value is not None and str(value) <= _now()


def _record_from_row(row: sqlite3.Row | tuple[object, ...]) -> MemoryRecord:
    return MemoryRecord(
        id=int(row[0]),
        content=str(row[1]),
        category=_validate_category(str(row[2])),
        created_at=str(row[3]),
        updated_at=str(row[4]),
        last_retrieved_at=None if row[5] is None else str(row[5]),
        archived_at=None if row[6] is None else str(row[6]),
        expires_at=None if row[7] is None else str(row[7]),
    )


def _meaningful_terms(value: str) -> tuple[str, ...]:
    return tuple(
        term for term in WORD_PATTERN.findall(value.casefold()) if term not in STOP_WORDS
    )


def _infer_categories(terms: tuple[str, ...]) -> set[MemoryCategory]:
    term_set = set(terms)
    categories: set[MemoryCategory] = set()
    if term_set & {"prefer", "preference", "favorite", "favourite", "style"}:
        categories.add(MemoryCategory.PREFERENCE)
    if term_set & {"project", "roadmap", "code", "implementation", "ato"}:
        categories.add(MemoryCategory.PROJECT)
    if term_set & {"decide", "decided", "decision", "choose", "chose", "selected"}:
        categories.add(MemoryCategory.DECISION)
    return categories


def _relevance_score(
    query_terms: tuple[str, ...],
    content_terms: tuple[str, ...],
    overlap: set[str],
    category: MemoryCategory,
    intended_categories: set[MemoryCategory],
) -> float:
    coverage = len(overlap) / len(set(query_terms))
    query_pairs = set(zip(query_terms, query_terms[1:], strict=False))
    content_pairs = set(zip(content_terms, content_terms[1:], strict=False))
    phrase_score = len(query_pairs & content_pairs) / max(1, len(query_pairs))
    category_score = 1.0 if category in intended_categories else 0.0
    return coverage * 4.0 + phrase_score * 1.5 + category_score


def _dedupe_key(content: str) -> str:
    return " ".join(WORD_PATTERN.findall(content.casefold()))
