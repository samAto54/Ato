"""Narrow, audited speech capability for the native desktop interface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ato.exceptions import ToolError
from ato.security import (
    AuditLogger,
    PermissionDecision,
    PermissionLevel,
    PermissionManager,
    PermissionRequest,
)
from ato.voice import WindowsSpeechPlayer
from ato.voice.base import validate_synthesis_text


@dataclass(slots=True)
class DesktopSpeechService:
    """Speak bounded text after HIGH permission and record the outcome."""

    player: WindowsSpeechPlayer
    permission_manager: PermissionManager
    audit_logger: AuditLogger

    def speak(self, text: str, *, on_playback: Callable[[], None] | None = None) -> None:
        cleaned = validate_synthesis_text(text)
        arguments = {"text": cleaned}
        self.audit_logger.ensure_ready()
        decision = self.permission_manager.authorize(
            PermissionRequest(
                "speak_latest_reply",
                PermissionLevel.HIGH,
                arguments,
                "Speak the latest assistant reply",
            )
        )
        if decision is PermissionDecision.DENY:
            self._audit(arguments, decision, error="User denied permission.")
            raise ToolError("Permission denied for speech playback.")
        try:
            if on_playback is not None:
                on_playback()
            self.player.speak(cleaned)
        except ToolError as exc:
            self._audit(arguments, decision, error=str(exc))
            raise
        except Exception as exc:
            self._audit(arguments, decision, error="Speech playback failed safely.")
            raise ToolError("Speech playback failed safely.") from exc
        self._audit(arguments, decision, result="Speech playback completed.")

    def _audit(
        self,
        arguments: dict[str, str],
        decision: PermissionDecision,
        *,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        self.audit_logger.record(
            user_request="Speak the latest assistant reply",
            tool_name="speak_latest_reply",
            arguments=arguments,
            permission=PermissionLevel.HIGH,
            decision=decision,
            result=result,
            error=error,
        )
