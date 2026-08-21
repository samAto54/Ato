"""Clearly labelled local terminal notifications."""

from __future__ import annotations

from collections.abc import Callable

from ato.notifications.base import Notification


class TerminalNotifier:
    def __init__(self, write: Callable[[str], None] = print) -> None:
        self._write = write

    def send(self, notification: Notification) -> str:
        self._write(f"[Ato notification/{notification.level.value.upper()}] {notification.title}")
        for line in notification.message.splitlines():
            self._write(f"  {line}")
        return "terminal"
