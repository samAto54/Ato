"""Bounded, privacy-reduced audit activity views for the desktop UI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_ACTIVITY_BYTES = 256_000
MAX_ACTIVITY_EVENTS = 100
VALID_PERMISSIONS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
VALID_DECISIONS = {"ALLOW", "DENY"}


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    time: str
    tool: str
    permission: str
    decision: str
    outcome: str

    def display(self) -> str:
        return (
            f"{self.time}\n"
            f"{self.tool}  •  {self.permission}  •  {self.decision}  •  {self.outcome}"
        )


@dataclass(slots=True)
class AuditActivityReader:
    path: Path

    def recent(self) -> tuple[ActivityEvent, ...]:
        if not self.path.is_file():
            return ()
        try:
            with self.path.open("rb") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - MAX_ACTIVITY_BYTES))
                data = stream.read(MAX_ACTIVITY_BYTES)
        except OSError:
            return ()
        if size > MAX_ACTIVITY_BYTES:
            _, _, data = data.partition(b"\n")
        events = []
        for raw_line in data.splitlines()[-MAX_ACTIVITY_EVENTS:]:
            try:
                payload = json.loads(raw_line.decode("utf-8"))
                event = _parse_event(payload)
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                continue
            events.append(event)
        events.reverse()
        return tuple(events)


def _parse_event(payload: Any) -> ActivityEvent:
    if not isinstance(payload, dict):
        raise ValueError("Audit event must be an object.")
    timestamp = _bounded_label(payload.get("time"), 40)
    tool = _bounded_label(payload.get("tool"), 80)
    permission = str(payload.get("permission", "UNKNOWN"))
    decision = str(payload.get("decision", "UNKNOWN"))
    if permission not in VALID_PERMISSIONS:
        permission = "UNKNOWN"
    if decision not in VALID_DECISIONS:
        decision = "UNKNOWN"
    outcome = "ERROR RECORDED" if payload.get("error") else "SUCCESS"
    return ActivityEvent(
        timestamp or "UNKNOWN TIME",
        tool or "UNKNOWN TOOL",
        permission,
        decision,
        outcome,
    )


def _bounded_label(value: Any, limit: int) -> str:
    normalized = " ".join(str(value or "").split())
    return normalized[:limit]
