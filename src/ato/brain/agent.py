"""The interface-independent Ato Agent Core."""

from __future__ import annotations

from collections.abc import Sequence

from ato.brain.llm import LLMClient
from ato.brain.messages import Message, Role
from ato.brain.prompts import SYSTEM_PROMPT
from ato.tools.registry import ToolRegistry


class Agent:
    """Manage one in-memory conversation with an injected LLM provider."""

    def __init__(
        self,
        llm: LLMClient,
        system_prompt: str = SYSTEM_PROMPT,
        history: Sequence[Message] = (),
        tools: ToolRegistry | None = None,
    ) -> None:
        if not system_prompt.strip():
            raise ValueError("The system prompt cannot be empty.")
        self._llm = llm
        self._tools = tools
        if any(message.role is Role.SYSTEM for message in history):
            raise ValueError("Restored history cannot contain system messages.")
        self._messages = [Message(Role.SYSTEM, system_prompt), *history]

    @property
    def messages(self) -> Sequence[Message]:
        """Return an immutable snapshot of the current conversation."""
        return tuple(self._messages)

    @property
    def conversation(self) -> Sequence[Message]:
        """Return user and assistant messages without the system prompt."""
        return tuple(self._messages[1:])

    def clear_conversation(self) -> None:
        """Clear conversation context while retaining the system prompt."""
        del self._messages[1:]

    def respond(self, user_input: str) -> str:
        """Add user input, request a reply, and record a successful response."""
        cleaned_input = user_input.strip()
        if not cleaned_input:
            raise ValueError("User input cannot be empty.")

        user_message = Message(Role.USER, cleaned_input)
        pending_messages = [*self._messages, user_message]
        response = self._llm.generate(pending_messages, tools=self._tools).strip()

        if not response:
            raise ValueError("The language model returned an empty response.")

        self._messages.extend(
            [user_message, Message(Role.ASSISTANT, response)]
        )
        return response
