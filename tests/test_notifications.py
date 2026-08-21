import json

import pytest

from ato.exceptions import ToolError
from ato.notifications import Notification, TerminalNotifier
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry


class RecordingNotifier:
    def __init__(self) -> None:
        self.notifications = []

    def send(self, notification: Notification) -> str:
        self.notifications.append(notification)
        return "recording"


def test_notification_tool_requires_permission_then_delivers(tmp_path) -> None:
    notifier = RecordingNotifier()
    denied = build_phase3_registry(tmp_path, notifier=notifier)
    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute(
            "send_notification",
            {"title": "Build", "message": "Tests passed", "level": "success"},
        )
    assert notifier.notifications == []

    approved = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        notifier=notifier,
    )
    result = json.loads(
        approved.execute(
            "send_notification",
            {"title": "Build", "message": "Tests passed", "level": "success"},
        )
    )
    assert result == {
        "delivered": True,
        "provider": "recording",
        "level": "success",
        "title": "Build",
    }
    assert notifier.notifications[0].message == "Tests passed"


def test_terminal_notification_is_clearly_prefixed_line_by_line() -> None:
    lines = []
    notifier = TerminalNotifier(lines.append)
    notification = Notification.validated("Reminder", "First line\nSecond line", "warning")

    assert notifier.send(notification) == "terminal"
    assert lines == [
        "[Ato notification/WARNING] Reminder",
        "  First line",
        "  Second line",
    ]


@pytest.mark.parametrize(
    ("title", "message", "level", "error"),
    [
        ("   ", "message", "info", "title"),
        ("title\nspoof", "message", "info", "title"),
        ("title", "   ", "info", "message"),
        ("title", "bad\x1b[31m", "info", "control"),
        ("title", "message", "urgent", "level"),
    ],
)
def test_notification_validation_rejects_unsafe_or_invalid_text(
    title, message, level, error
) -> None:
    with pytest.raises(ToolError, match=error):
        Notification.validated(title, message, level)


def test_notification_tool_is_absent_without_provider(tmp_path) -> None:
    registry = build_phase3_registry(tmp_path)
    names = {definition["function"]["name"] for definition in registry.api_definitions()}
    assert "send_notification" not in names
