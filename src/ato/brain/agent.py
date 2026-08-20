"""The interface-independent Ato Agent Core."""

from __future__ import annotations

from collections.abc import Sequence

from ato.brain.context import ContextManager
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
        summary: str = "",
        context_manager: ContextManager | None = None,
        tools: ToolRegistry | None = None,
    ) -> None:
        if not system_prompt.strip():
            raise ValueError("The system prompt cannot be empty.")
        self._llm = llm
        self._tools = tools
        self._context_manager = context_manager or ContextManager()
        self._summary = summary.strip()
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

    @property
    def summary(self) -> str:
        """Return the compacted context from older conversation turns."""
        return self._summary

    def clear_conversation(self) -> None:
        """Clear conversation context while retaining the system prompt."""
        del self._messages[1:]
        self._summary = ""

    def respond(self, user_input: str) -> str:
        """Add user input, request a reply, and record a successful response."""
        cleaned_input = user_input.strip()
        if not cleaned_input:
            raise ValueError("User input cannot be empty.")

        user_message = Message(Role.USER, cleaned_input)
        pending = self._context_manager.compact([*self._messages[1:], user_message], self._summary)
        model_messages = [self._messages[0]]
        summary_message = self._context_manager.summary_message(pending.summary)
        if summary_message is not None:
            model_messages.append(summary_message)
        model_messages.extend(pending.messages)
        response = self._llm.generate(model_messages, tools=self._tools).strip()

        if not response:
            raise ValueError("The language model returned an empty response.")

        completed = self._context_manager.compact(
            [*self._messages[1:], user_message, Message(Role.ASSISTANT, response)],
            self._summary,
        )
        self._summary = completed.summary
        self._messages = [self._messages[0], *completed.messages]
        return response
