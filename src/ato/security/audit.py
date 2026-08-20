"""Append-only, redacted JSONL audit logging for tool activity."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ato.exceptions import AuditError
from ato.security.permissions import PermissionDecision, PermissionLevel

MAX_AUDIT_TEXT = 2_000
SENSITIVE_KEY_PARTS = ("api_key", "authorization", "password", "secret", "token")
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)(bearer\s+)[^\s]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s]+"),
)


class AuditLogger:
    """Write one sanitized JSON object per tool execution decision."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def ensure_ready(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch(exist_ok=True)
        except OSError as exc:
            raise AuditError(f"Audit log is not writable: {self.path}") from exc

    def record(
        self,
        *,
        user_request: str | None,
        tool_name: str,
        arguments: Mapping[str, Any],
        permission: PermissionLevel,
        decision: PermissionDecision,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        event = {
            "time": datetime.now(UTC).isoformat(),
            "user_request": self._sanitize_text(user_request),
            "tool": tool_name,
            "arguments": self.redact(dict(arguments)),
            "permission": permission.value,
            "decision": decision.value,
            "result": self._summarize_result(result),
            "error": self._sanitize_text(error),
        }
        try:
            with self.path.open("a", encoding="utf-8", newline="\n") as audit_file:
                audit_file.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError as exc:
            raise AuditError(f"Could not write audit log: {self.path}") from exc

    @classmethod
    def redact(cls, value: Any, key: str = "") -> Any:
        if any(part in key.lower() for part in SENSITIVE_KEY_PARTS):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {
                str(item_key): cls.redact(item, str(item_key))
                for item_key, item in value.items()
            }
        if isinstance(value, list):
            return [cls.redact(item) for item in value]
        if isinstance(value, str):
            return cls._truncate(value)
        return value

    @staticmethod
    def _truncate(value: str | None) -> str | None:
        if value is None or len(value) <= MAX_AUDIT_TEXT:
            return value
        return value[:MAX_AUDIT_TEXT] + "...[truncated]"

    @classmethod
    def _sanitize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        sanitized = value
        for pattern in SECRET_PATTERNS:
            if pattern.groups:
                sanitized = pattern.sub(r"\1[REDACTED]", sanitized)
            else:
                sanitized = pattern.sub("[REDACTED]", sanitized)
        return cls._truncate(sanitized)

    @staticmethod
    def _summarize_result(result: str | None) -> dict[str, Any] | None:
        if result is None:
            return None
        digest = hashlib.sha256(result.encode("utf-8")).hexdigest()
        return {"status": "success", "characters": len(result), "sha256": digest}
