"""Fail-closed thread bridge for protected desktop permission prompts."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Event, Lock, RLock, get_ident

from ato.security import AuditLogger, PermissionRequest

MAX_PERMISSION_DETAILS = 5_000
ScheduleHandler = Callable[[int, Callable[[], None]], object]


@dataclass(frozen=True, slots=True)
class GuiPermissionPrompt:
    tool_name: str
    permission: str
    details: str


DialogHandler = Callable[[GuiPermissionPrompt], bool]


@dataclass(slots=True)
class _PendingDecision:
    prompt: GuiPermissionPrompt
    completed: Event
    allowed: bool = False


class GuiPermissionBridge:
    """Move worker-thread confirmation requests onto an attached UI event loop."""

    def __init__(self, *, timeout_seconds: float = 300.0, poll_ms: int = 50) -> None:
        if timeout_seconds <= 0:
            raise ValueError("GUI permission timeout must be positive.")
        if poll_ms < 10:
            raise ValueError("GUI permission polling interval must be at least 10 ms.")
        self.timeout_seconds = timeout_seconds
        self.poll_ms = poll_ms
        self._queue: Queue[_PendingDecision] = Queue()
        self._attachment_lock = RLock()
        self._confirmation_lock = Lock()
        self._schedule: ScheduleHandler | None = None
        self._dialog: DialogHandler | None = None
        self._ui_thread_id: int | None = None
        self._attached = False

    @property
    def attached(self) -> bool:
        with self._attachment_lock:
            return self._attached

    def attach(self, schedule: ScheduleHandler, dialog: DialogHandler) -> None:
        """Attach once from the GUI thread and start bounded queue polling."""
        with self._attachment_lock:
            if self._attached:
                raise RuntimeError("GUI permission bridge is already attached.")
            self._schedule = schedule
            self._dialog = dialog
            self._ui_thread_id = get_ident()
            self._attached = True
            try:
                schedule(self.poll_ms, self._poll)
            except Exception:
                self._attached = False
                self._schedule = None
                self._dialog = None
                self._ui_thread_id = None
                raise

    def detach(self) -> None:
        """Fail all waiting requests closed before the GUI is destroyed."""
        with self._attachment_lock:
            self._attached = False
            self._schedule = None
            self._dialog = None
            self._ui_thread_id = None
        while True:
            try:
                pending = self._queue.get_nowait()
            except Empty:
                break
            pending.allowed = False
            pending.completed.set()

    def confirm(self, request: PermissionRequest) -> bool:
        """Wait for one serialized GUI decision, returning False for every bridge failure."""
        prompt = build_gui_permission_prompt(request)
        with self._confirmation_lock:
            with self._attachment_lock:
                if not self._attached or self._dialog is None:
                    return False
                dialog = self._dialog
                on_ui_thread = self._ui_thread_id == get_ident()
            if on_ui_thread:
                return _safe_dialog(dialog, prompt)
            pending = _PendingDecision(prompt, Event())
            self._queue.put(pending)
            if not pending.completed.wait(self.timeout_seconds):
                # Mark an expired request so a delayed UI poll cannot show a stale dialog.
                pending.completed.set()
                return False
            return pending.allowed

    def _poll(self) -> None:
        with self._attachment_lock:
            if not self._attached or self._dialog is None or self._schedule is None:
                return
            dialog = self._dialog
            schedule = self._schedule
        try:
            pending = self._queue.get_nowait()
        except Empty:
            pending = None
        if pending is not None and not pending.completed.is_set():
            pending.allowed = _safe_dialog(dialog, pending.prompt)
            pending.completed.set()
        with self._attachment_lock:
            if not self._attached:
                return
        try:
            schedule(self.poll_ms, self._poll)
        except Exception:
            self.detach()


def build_gui_permission_prompt(request: PermissionRequest) -> GuiPermissionPrompt:
    """Create a bounded display model using the established audit redaction policy."""
    safe_arguments = AuditLogger.confirmation_view(request.arguments)
    details = json.dumps(safe_arguments, ensure_ascii=False, indent=2, sort_keys=True)
    if len(details) > MAX_PERMISSION_DETAILS:
        details = f"{details[: MAX_PERMISSION_DETAILS - 16]}\n...[truncated]"
    return GuiPermissionPrompt(request.tool_name, request.level.value, details)


def _safe_dialog(dialog: DialogHandler, prompt: GuiPermissionPrompt) -> bool:
    try:
        return bool(dialog(prompt))
    except Exception:
        return False
