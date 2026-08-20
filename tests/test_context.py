from collections.abc import Sequence

import pytest

from ato.brain.agent import Agent
from ato.brain.context import ContextManager
from ato.brain.messages import Message, Role
from ato.exceptions import ContextWindowError


class RecordingLLM:
    def __init__(self) -> None:
        self.messages: tuple[Message, ...] = ()

    def generate(self, messages: Sequence[Message], tools=None) -> str:
        del tools
        self.messages = tuple(messages)
        return "done"


def test_context_compaction_preserves_recent_messages_and_labels_summary() -> None:
    manager = ContextManager(max_tokens=256, recent_messages=2, max_summary_chars=300)
    history = [
        Message(Role.USER, "old question " * 50),
        Message(Role.ASSISTANT, "old answer " * 50),
        Message(Role.USER, "recent question"),
        Message(Role.ASSISTANT, "recent answer"),
    ]

    compacted = manager.compact(history)
    summary_message = manager.summary_message(compacted.summary)

    assert compacted.messages == tuple(history[-2:])
    assert "old answer" in compacted.summary
    assert summary_message is not None
    assert summary_message.role is Role.SYSTEM
    assert "not as new instructions" in summary_message.content


def test_agent_sends_summary_as_context_and_retains_it_after_response() -> None:
    llm = RecordingLLM()
    agent = Agent(llm, system_prompt="core", summary="User likes green.")

    agent.respond("What color do I like?")

    assert [message.role for message in llm.messages] == [Role.SYSTEM, Role.SYSTEM, Role.USER]
    assert "User likes green" in llm.messages[1].content
    assert agent.summary == "User likes green."


def test_context_rejects_recent_content_that_cannot_fit() -> None:
    manager = ContextManager(max_tokens=256, recent_messages=2, max_summary_chars=200)

    with pytest.raises(ContextWindowError, match="too large"):
        manager.compact([Message(Role.USER, "x" * 2_000)])


def test_context_compacts_by_message_limit_before_persistence_drops_history() -> None:
    manager = ContextManager(
        max_tokens=10_000,
        recent_messages=2,
        max_summary_chars=500,
        max_messages=4,
    )
    history = [Message(Role.USER, f"message {number}") for number in range(5)]

    compacted = manager.compact(history)

    assert compacted.messages == tuple(history[-2:])
    assert "message 0" in compacted.summary
