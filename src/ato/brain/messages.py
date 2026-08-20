"""Conversation message types shared by the agent and providers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    """Roles supported in an Ato conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class Message:
    """One immutable message in the current conversation."""

    role: Role
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, Role):
            raise TypeError("Message role must be a Role value.")
        if not self.content.strip():
            raise ValueError("Message content cannot be empty.")
