"""Provider-neutral language model interface."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ato.brain.messages import Message


class LLMClient(Protocol):
    """Contract implemented by every language model provider."""

    def generate(self, messages: Sequence[Message]) -> str:
        """Generate a response from an ordered conversation."""
        ...
