from threading import Thread

from ato.security import PermissionLevel, PermissionRequest
from ato.ui.permissions import GuiPermissionBridge, build_gui_permission_prompt


class Scheduler:
    def __init__(self) -> None:
        self.callbacks = []

    def __call__(self, delay_ms, callback):
        assert delay_ms >= 10
        self.callbacks.append(callback)
        return len(self.callbacks)

    def run_next(self) -> None:
        self.callbacks.pop(0)()


def test_permission_prompt_uses_existing_secret_redaction() -> None:
    prompt = build_gui_permission_prompt(
        PermissionRequest(
            "write_clipboard",
            PermissionLevel.HIGH,
            {"token": "ghp_abcdefghijklmnopqrstuvwxyz", "text": "password=hunter2"},
        )
    )
    assert prompt.tool_name == "write_clipboard"
    assert prompt.permission == "HIGH"
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in prompt.details
    assert "hunter2" not in prompt.details
    assert "[REDACTED]" in prompt.details


def test_unattached_bridge_fails_closed() -> None:
    bridge = GuiPermissionBridge(timeout_seconds=0.1)
    request = PermissionRequest("tool", PermissionLevel.HIGH, {})
    assert bridge.confirm(request) is False


def test_worker_request_is_decided_by_ui_poll() -> None:
    scheduler = Scheduler()
    seen = []
    bridge = GuiPermissionBridge(timeout_seconds=1, poll_ms=10)
    bridge.attach(scheduler, lambda prompt: not seen.append(prompt))
    request = PermissionRequest("record_microphone", PermissionLevel.CRITICAL, {"seconds": 3})
    result = []
    worker = Thread(target=lambda: result.append(bridge.confirm(request)))
    worker.start()
    for _ in range(20):
        scheduler.run_next()
        if result:
            break
    worker.join(timeout=1)
    assert result == [True]
    assert seen[0].tool_name == "record_microphone"
    bridge.detach()


def test_timed_out_request_never_opens_a_stale_dialog() -> None:
    scheduler = Scheduler()
    seen = []
    bridge = GuiPermissionBridge(timeout_seconds=0.01, poll_ms=10)
    bridge.attach(scheduler, lambda prompt: not seen.append(prompt))
    request = PermissionRequest("tool", PermissionLevel.HIGH, {})
    result = []
    worker = Thread(target=lambda: result.append(bridge.confirm(request)))
    worker.start()
    worker.join(timeout=1)
    scheduler.run_next()
    assert result == [False]
    assert seen == []
    bridge.detach()


def test_dialog_exception_and_detach_deny() -> None:
    scheduler = Scheduler()
    bridge = GuiPermissionBridge(timeout_seconds=0.1, poll_ms=10)
    bridge.attach(scheduler, lambda prompt: (_ for _ in ()).throw(RuntimeError("boom")))
    request = PermissionRequest("tool", PermissionLevel.MEDIUM, {})
    assert bridge.confirm(request) is False
    bridge.detach()
    assert bridge.confirm(request) is False
