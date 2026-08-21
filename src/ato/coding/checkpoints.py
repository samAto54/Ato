"""Bounded local checkpoints for recoverable exact text edits."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ato.exceptions import CheckpointStoreError

MAX_EDIT_CHECKPOINTS = 10_000
MAX_CHECKPOINT_CONTENT_BYTES = 100_000
MAX_LIST_CHECKPOINTS = 100
SHA256_PATTERN = re.compile(r"[a-f0-9]{64}")


@dataclass(frozen=True, slots=True)
class EditCheckpointRecord:
    id: int
    path: str
    original_sha256: str
    updated_sha256: str
    created_at: str
    restored_at: str | None


@dataclass(frozen=True, slots=True)
class EditCheckpoint:
    record: EditCheckpointRecord
    original_content: str


class SqliteEditCheckpointStore:
    """Persist original text before an approved exact replacement."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._initialize()

    def create(
        self,
        relative_path: str,
        original_content: str,
        original_sha256: str,
        updated_sha256: str,
    ) -> EditCheckpointRecord:
        encoded = original_content.encode("utf-8")
        if len(encoded) > MAX_CHECKPOINT_CONTENT_BYTES:
            raise CheckpointStoreError("Edit checkpoint content exceeds the storage limit.")
        if not SHA256_PATTERN.fullmatch(original_sha256) or not SHA256_PATTERN.fullmatch(
            updated_sha256
        ):
            raise CheckpointStoreError("Edit checkpoint contains an invalid SHA-256 digest.")
        if hashlib.sha256(encoded).hexdigest() != original_sha256:
            raise CheckpointStoreError("Edit checkpoint content does not match its SHA-256.")
        created_at = datetime.now(UTC).isoformat()
        try:
            with self._connect() as connection:
                count = connection.execute("SELECT COUNT(*) FROM edit_checkpoints").fetchone()[0]
                if count >= MAX_EDIT_CHECKPOINTS:
                    raise CheckpointStoreError("Edit checkpoint storage has reached its limit.")
                cursor = connection.execute(
                    "INSERT INTO edit_checkpoints(path, original_content, original_sha256, "
                    "updated_sha256, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        relative_path,
                        original_content,
                        original_sha256,
                        updated_sha256,
                        created_at,
                    ),
                )
                checkpoint_id = int(cursor.lastrowid)
        except CheckpointStoreError:
            raise
        except sqlite3.Error as exc:
            raise CheckpointStoreError("Edit checkpoint could not be saved.") from exc
        return EditCheckpointRecord(
            checkpoint_id,
            relative_path,
            original_sha256,
            updated_sha256,
            created_at,
            None,
        )

    def list_checkpoints(self, limit: int = 20) -> tuple[EditCheckpointRecord, ...]:
        if not 1 <= limit <= MAX_LIST_CHECKPOINTS:
            raise ValueError(f"limit must be between 1 and {MAX_LIST_CHECKPOINTS}.")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT id, path, original_sha256, updated_sha256, created_at, restored_at "
                    "FROM edit_checkpoints ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise CheckpointStoreError("Edit checkpoints could not be listed.") from exc
        return tuple(_record(row) for row in rows)

    def load(self, checkpoint_id: int) -> EditCheckpoint | None:
        if checkpoint_id < 1:
            raise ValueError("checkpoint_id must be positive.")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT id, path, original_sha256, updated_sha256, created_at, restored_at, "
                    "original_content FROM edit_checkpoints WHERE id = ?",
                    (checkpoint_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise CheckpointStoreError("Edit checkpoint could not be loaded.") from exc
        if row is None:
            return None
        return EditCheckpoint(_record(row[:6]), str(row[6]))

    def mark_restored(self, checkpoint_id: int) -> bool:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "UPDATE edit_checkpoints SET restored_at = ? "
                    "WHERE id = ? AND restored_at IS NULL",
                    (datetime.now(UTC).isoformat(), checkpoint_id),
                )
        except sqlite3.Error as exc:
            raise CheckpointStoreError("Edit checkpoint could not be marked restored.") from exc
        return cursor.rowcount > 0

    def _initialize(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS edit_checkpoints ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, "
                    "original_content TEXT NOT NULL, original_sha256 TEXT NOT NULL, "
                    "updated_sha256 TEXT NOT NULL, created_at TEXT NOT NULL, restored_at TEXT)"
                )
        except (OSError, sqlite3.Error) as exc:
            raise CheckpointStoreError("Edit checkpoint storage could not be initialized.") from exc

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)


def _record(row: tuple[object, ...]) -> EditCheckpointRecord:
    return EditCheckpointRecord(
        id=int(row[0]),
        path=str(row[1]),
        original_sha256=str(row[2]),
        updated_sha256=str(row[3]),
        created_at=str(row[4]),
        restored_at=None if row[5] is None else str(row[5]),
    )
