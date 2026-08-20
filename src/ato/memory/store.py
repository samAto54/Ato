"""Small, reliable JSON persistence for recent conversation context."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ato.brain.messages import Message, Role
from ato.exceptions import MemoryStoreError

SCHEMA_VERSION = 2
PERSISTED_ROLES = {Role.USER, Role.ASSISTANT}


@dataclass(frozen=True, slots=True)
class MemoryContext:
    """Persisted summary and recent verbatim conversation history."""

    summary: str
    history: tuple[Message, ...]


class JsonMemoryStore:
    """Persist a bounded conversation history in a versioned JSON file."""

    def __init__(self, path: Path, max_messages: int = 40) -> None:
        if max_messages < 2:
            raise ValueError("max_messages must be at least 2.")
        self.path = path
        self.max_messages = max_messages

    def load_history(self) -> tuple[Message, ...]:
        """Load and validate persisted user/assistant messages."""
        return self.load_context().history

    def load_context(self) -> MemoryContext:
        """Load and validate the persisted summary and recent messages."""
        if not self.path.exists():
            return MemoryContext("", ())

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MemoryStoreError(f"Could not read memory file: {self.path}") from exc

        return self._parse_payload(payload)

    def save_history(self, messages: Sequence[Message]) -> None:
        """Atomically save the most recent user/assistant messages."""
        self.save_context(messages)

    def save_context(self, messages: Sequence[Message], summary: str = "") -> None:
        """Atomically save a bounded summary and recent conversation messages."""
        if not isinstance(summary, str):
            raise TypeError("summary must be a string.")
        persisted = [message for message in messages if message.role in PERSISTED_ROLES]
        persisted = persisted[-self.max_messages :]
        payload = {
            "version": SCHEMA_VERSION,
            "summary": summary.strip(),
            "history": [
                {"role": message.role.value, "content": message.content} for message in persisted
            ],
        }

        temporary_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_path, self.path)
        except OSError as exc:
            temporary_path.unlink(missing_ok=True)
            raise MemoryStoreError(f"Could not save memory file: {self.path}") from exc

    def clear(self) -> None:
        """Remove persisted history if it exists."""
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise MemoryStoreError(f"Could not clear memory file: {self.path}") from exc

    def _parse_payload(self, payload: Any) -> MemoryContext:
        if not isinstance(payload, dict) or payload.get("version") not in {1, SCHEMA_VERSION}:
            raise MemoryStoreError("Memory file has an unsupported schema version.")

        summary = payload.get("summary", "")
        if not isinstance(summary, str):
            raise MemoryStoreError("Memory file summary must be text.")

        raw_history = payload.get("history")
        if not isinstance(raw_history, list):
            raise MemoryStoreError("Memory file history must be a list.")

        history: list[Message] = []
        for item in raw_history:
            if not isinstance(item, dict):
                raise MemoryStoreError("Memory file contains an invalid message.")
            try:
                role = Role(item["role"])
                content = item["content"]
            except (KeyError, TypeError, ValueError) as exc:
                raise MemoryStoreError("Memory file contains an invalid message.") from exc
            if role not in PERSISTED_ROLES or not isinstance(content, str):
                raise MemoryStoreError("Memory file contains an invalid message.")
            try:
                history.append(Message(role, content))
            except ValueError as exc:
                raise MemoryStoreError("Memory file contains an empty message.") from exc

        return MemoryContext(summary.strip(), tuple(history[-self.max_messages :]))
