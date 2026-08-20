"""The interface-independent Ato Agent Core."""

from __future__ import annotations

from collections.abc import Sequence

from ato.brain.llm import LLMClient
from ato.brain.messages import Message, Role
from ato.brain.prompts import SYSTEM_PROMPT


class Agent:
    """Manage one in-memory conversation with an injected LLM provider."""

    def __init__(self, llm: LLMClient, system_prompt: str = SYSTEM_PROMPT) -> None:
        if not system_prompt.strip():
            raise ValueError("The system prompt cannot be empty.")
        self._llm = llm
        self._messages = [Message(Role.SYSTEM, system_prompt)]

    @property
    def messages(self) -> Sequence[Message]:
        """Return an immutable snapshot of the current conversation."""
        return tuple(self._messages)

    def respond(self, user_input: str) -> str:
        """Add user input, request a reply, and record a successful response."""
        cleaned_input = user_input.strip()
        if not cleaned_input:
            raise ValueError("User input cannot be empty.")

        user_message = Message(Role.USER, cleaned_input)
        pending_messages = [*self._messages, user_message]
        response = self._llm.generate(pending_messages).strip()

        if not response:
            raise ValueError("The language model returned an empty response.")

        self._messages.extend(
            [user_message, Message(Role.ASSISTANT, response)]
        )
        return response
