"""Provider-neutral language model interface."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ato.brain.messages import Message
from ato.tools.registry import ToolRegistry


class LLMClient(Protocol):
    """Contract implemented by every language model provider."""

    def generate(
        self,
        messages: Sequence[Message],
        tools: ToolRegistry | None = None,
    ) -> str:
        """Generate a response from an ordered conversation."""
        ...
