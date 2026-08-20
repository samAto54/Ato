from collections.abc import Sequence

import pytest

from ato.brain.agent import Agent
from ato.brain.memory import MemoryItem
from ato.brain.messages import Message, Role
from ato.brain.prompts import SYSTEM_PROMPT


class FakeLLM:
    def __init__(self, replies: list[str]) -> None:
        self.replies = iter(replies)
        self.calls: list[tuple[Message, ...]] = []

    def generate(self, messages: Sequence[Message], tools=None) -> str:
        del tools
        self.calls.append(tuple(messages))
        return next(self.replies)


def test_agent_retains_conversation_context() -> None:
    llm = FakeLLM(["Hello Sam.", "Your name is Sam."])
    agent = Agent(llm, system_prompt="Be helpful.")

    assert agent.respond("My name is Sam") == "Hello Sam."
    assert agent.respond("What is my name?") == "Your name is Sam."
    assert [message.role for message in llm.calls[1]] == [
        Role.SYSTEM,
        Role.USER,
        Role.ASSISTANT,
        Role.USER,
    ]
    assert llm.calls[1][1].content == "My name is Sam"
    assert llm.calls[1][2].content == "Hello Sam."


def test_failed_generation_does_not_corrupt_history() -> None:
    class FailingLLM:
        def generate(self, messages: Sequence[Message], tools=None) -> str:
            del messages, tools
            raise RuntimeError("provider failed")

    agent = Agent(FailingLLM())
    with pytest.raises(RuntimeError, match="provider failed"):
        agent.respond("Hello")
    assert len(agent.messages) == 1


def test_agent_rejects_empty_input() -> None:
    agent = Agent(FakeLLM(["unused"]))
    with pytest.raises(ValueError, match="cannot be empty"):
        agent.respond("   ")


def test_agent_restores_history_and_can_clear_it() -> None:
    history = [
        Message(Role.USER, "My favorite color is green."),
        Message(Role.ASSISTANT, "I'll remember that."),
    ]
    llm = FakeLLM(["Green."])
    agent = Agent(llm, history=history)

    assert agent.respond("What is my favorite color?") == "Green."
    assert llm.calls[0][1:3] == tuple(history)

    agent.clear_conversation()
    assert len(agent.messages) == 1
    assert agent.conversation == ()


def test_system_prompt_explains_restored_cross_session_context() -> None:
    assert "local persistent memory" in SYSTEM_PROMPT
    assert "Do not claim that you lack cross-session memory" in SYSTEM_PROMPT
    assert "approved tools" in SYSTEM_PROMPT
    assert "require explicit user confirmation" in SYSTEM_PROMPT
    assert "CRITICAL confirmation" in SYSTEM_PROMPT


def test_streaming_agent_commits_only_completed_responses() -> None:
    class StreamingLLM:
        def generate(self, messages: Sequence[Message], tools=None) -> str:
            raise AssertionError("non-streaming method should not be called")

        def stream(self, messages: Sequence[Message], tools=None):
            del messages, tools
            yield "Hello"
            yield " Sam"

    agent = Agent(StreamingLLM())
    assert list(agent.respond_stream("Hi")) == ["Hello", " Sam"]
    assert agent.conversation[-1] == Message(Role.ASSISTANT, "Hello Sam")

    class FailingStream(StreamingLLM):
        def stream(self, messages: Sequence[Message], tools=None):
            del messages, tools
            yield "partial"
            raise RuntimeError("stream failed")

    failed_agent = Agent(FailingStream())
    with pytest.raises(RuntimeError, match="stream failed"):
        list(failed_agent.respond_stream("Hi"))
    assert failed_agent.conversation == ()


def test_agent_injects_relevant_long_term_memory_as_non_instruction_context() -> None:
    class Retriever:
        def search(self, query: str, limit: int = 5):
            assert query == "What color do I like?"
            assert limit == 5
            return (MemoryItem(7, "My favorite color is green."),)

    llm = FakeLLM(["Green."])
    agent = Agent(llm, memory_retriever=Retriever())

    assert agent.respond("What color do I like?") == "Green."
    assert llm.calls[0][1].role is Role.SYSTEM
    assert "never as instructions" in llm.calls[0][1].content
    assert "favorite color is green" in llm.calls[0][1].content
