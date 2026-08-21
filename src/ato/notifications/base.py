"""Notification data and provider contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ato.exceptions import ToolError

MAX_NOTIFICATION_TITLE_CHARS = 100
MAX_NOTIFICATION_MESSAGE_CHARS = 1_000


class NotificationLevel(StrEnum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Notification:
    title: str
    message: str
    level: NotificationLevel

    @classmethod
    def validated(cls, title: str, message: str, level: str) -> Notification:
        title = title.strip()
        message = message.strip()
        if not title or len(title) > MAX_NOTIFICATION_TITLE_CHARS or "\n" in title:
            raise ToolError("Notification title must be one line with 1-100 characters.")
        if not message or len(message) > MAX_NOTIFICATION_MESSAGE_CHARS:
            raise ToolError("Notification message must contain 1-1,000 characters.")
        if _has_unsafe_control(title) or _has_unsafe_control(message):
            raise ToolError("Notification text contains unsafe terminal control characters.")
        try:
            parsed_level = NotificationLevel(level)
        except ValueError as exc:
            raise ToolError("Notification level is not supported.") from exc
        return cls(title, message, parsed_level)


class Notifier(Protocol):
    def send(self, notification: Notification) -> str:
        """Deliver one notification and return the provider name."""
        ...


def _has_unsafe_control(value: str) -> bool:
    return any(
        (ord(character) < 32 and character not in {"\n", "\t"}) or ord(character) == 127
        for character in value
    )
