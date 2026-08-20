"""Provider-neutral language model interface."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, Protocol

from ato.brain.messages import Message
from ato.brain.structured import StructuredOutputSpec
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


class StreamingLLMClient(LLMClient, Protocol):
    """Optional provider capability for incremental text delivery."""

    def stream(
        self,
        messages: Sequence[Message],
        tools: ToolRegistry | None = None,
    ) -> Iterator[str]:
        """Yield ordered non-empty response fragments."""
        ...


class StructuredLLMClient(LLMClient, Protocol):
    """Optional provider capability for validated JSON object responses."""

    def generate_structured(
        self,
        messages: Sequence[Message],
        spec: StructuredOutputSpec,
    ) -> dict[str, Any]:
        """Generate and validate one JSON object."""
        ...
