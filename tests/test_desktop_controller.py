from collections.abc import Sequence

import pytest

from ato.brain.agent import Agent
from ato.brain.messages import Message
from ato.memory import JsonMemoryStore
from ato.ui.desktop import DesktopChatController


class EchoLLM:
    def generate(self, messages: Sequence[Message], tools=None) -> str:
        del tools
        return f"Echo: {messages[-1].content}"


def test_desktop_controller_uses_agent_core_and_persists_turn(tmp_path) -> None:
    store = JsonMemoryStore(tmp_path / "memory.json")
    controller = DesktopChatController(Agent(EchoLLM()), store)

    assert controller.submit(" hello ") == "Echo: hello"
    assert [message.content for message in store.load_history()] == ["hello", "Echo: hello"]


def test_desktop_controller_rejects_empty_message() -> None:
    controller = DesktopChatController(Agent(EchoLLM()))
    with pytest.raises(ValueError, match="empty"):
        controller.submit("   ")
