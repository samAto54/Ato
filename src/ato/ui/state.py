"""Thread-safe visual state model for Ato interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import RLock


class AtoVisualState(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    TOOL_EXECUTION = "TOOL_EXECUTION"
    SPEAKING = "SPEAKING"


@dataclass(frozen=True, slots=True)
class VisualSnapshot:
    state: AtoVisualState
    status: str
    active_task: str
    tool: str | None
    revision: int


ALLOWED_TRANSITIONS = {
    AtoVisualState.IDLE: {
        AtoVisualState.LISTENING,
        AtoVisualState.PROCESSING,
        AtoVisualState.TOOL_EXECUTION,
    },
    AtoVisualState.LISTENING: {
        AtoVisualState.IDLE,
        AtoVisualState.PROCESSING,
        AtoVisualState.TOOL_EXECUTION,
    },
    AtoVisualState.PROCESSING: {
        AtoVisualState.IDLE,
        AtoVisualState.TOOL_EXECUTION,
        AtoVisualState.SPEAKING,
    },
    AtoVisualState.TOOL_EXECUTION: {
        AtoVisualState.IDLE,
        AtoVisualState.LISTENING,
        AtoVisualState.PROCESSING,
        AtoVisualState.SPEAKING,
    },
    AtoVisualState.SPEAKING: {AtoVisualState.IDLE, AtoVisualState.LISTENING},
}


class AtoStateModel:
    """Store backend-driven UI state without depending on Tk."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._snapshot = VisualSnapshot(AtoVisualState.IDLE, "READY", "Awaiting input", None, 0)

    def snapshot(self) -> VisualSnapshot:
        with self._lock:
            return self._snapshot

    def transition(
        self,
        state: AtoVisualState | str,
        *,
        status: str | None = None,
        active_task: str = "",
        tool: str | None = None,
    ) -> VisualSnapshot:
        target = state if isinstance(state, AtoVisualState) else AtoVisualState(state)
        with self._lock:
            current = self._snapshot
            if target is not current.state and target not in ALLOWED_TRANSITIONS[current.state]:
                raise ValueError(f"Invalid Ato visual transition: {current.state} -> {target}")
            if target is AtoVisualState.TOOL_EXECUTION and not tool:
                raise ValueError("Tool execution state requires a bounded tool label.")
            safe_task = _bounded_label(active_task, 120)
            safe_tool = _bounded_label(tool, 80) if tool else None
            self._snapshot = VisualSnapshot(
                target,
                _bounded_label(status or _default_status(target), 40),
                safe_task or _default_task(target),
                safe_tool,
                current.revision + 1,
            )
            return self._snapshot


def _bounded_label(value: str, limit: int) -> str:
    normalized = " ".join(str(value).split())
    if len(normalized) > limit:
        return f"{normalized[: limit - 3]}..."
    return normalized


def _default_status(state: AtoVisualState) -> str:
    return {
        AtoVisualState.IDLE: "READY",
        AtoVisualState.LISTENING: "LISTENING",
        AtoVisualState.PROCESSING: "PROCESSING",
        AtoVisualState.TOOL_EXECUTION: "TOOL EXECUTION",
        AtoVisualState.SPEAKING: "SPEAKING",
    }[state]


def _default_task(state: AtoVisualState) -> str:
    return {
        AtoVisualState.IDLE: "Awaiting input",
        AtoVisualState.LISTENING: "Capturing approved audio",
        AtoVisualState.PROCESSING: "Generating response",
        AtoVisualState.TOOL_EXECUTION: "Executing approved tool",
        AtoVisualState.SPEAKING: "Playing approved speech",
    }[state]
