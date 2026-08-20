"""Fail-closed permission decisions for tool execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PermissionLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PermissionDecision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """Information shown to a user before a protected tool runs."""

    tool_name: str
    level: PermissionLevel
    arguments: Mapping[str, Any]
    user_request: str | None = None


ConfirmationHandler = Callable[[PermissionRequest], bool]


class PermissionManager:
    """Automatically allow LOW tools and confirm all higher levels."""

    def __init__(self, confirmation_handler: ConfirmationHandler | None = None) -> None:
        self._confirmation_handler = confirmation_handler

    def authorize(self, request: PermissionRequest) -> PermissionDecision:
        if request.level is PermissionLevel.LOW:
            return PermissionDecision.ALLOW
        if self._confirmation_handler is None:
            return PermissionDecision.DENY
        try:
            allowed = self._confirmation_handler(request)
        except (EOFError, KeyboardInterrupt):
            return PermissionDecision.DENY
        except Exception:
            return PermissionDecision.DENY
        return PermissionDecision.ALLOW if allowed else PermissionDecision.DENY
