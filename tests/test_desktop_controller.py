from collections.abc import Sequence

import pytest

from ato.brain.agent import Agent
from ato.brain.messages import Message
from ato.knowledge import SqliteKnowledgeStore
from ato.memory import JsonMemoryStore, SqliteLongTermMemory
from ato.ui.desktop import DESKTOP_SYSTEM_PROMPT, DesktopChatController


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


def test_desktop_policy_forbids_claims_of_unavailable_tool_use() -> None:
    policy = " ".join(DESKTOP_SYSTEM_PROMPT.split())
    assert "chat-only" in policy
    assert "provides no tools" in policy
    assert "Never claim that you performed an unavailable action" in policy
    assert "browse or fetch web pages" in policy


def test_desktop_controller_exposes_bounded_read_only_sidebar_snapshots(tmp_path) -> None:
    memory = SqliteLongTermMemory(tmp_path / "memory.db")
    memory.remember("Use cyan for the Ato HUD.", "preference")
    (tmp_path / "guide.md").write_text("Ato guide", encoding="utf-8")
    knowledge = SqliteKnowledgeStore(tmp_path / "knowledge.db", tmp_path)
    knowledge.ingest("guide.md")
    controller = DesktopChatController(
        Agent(EchoLLM()),
        long_term_memory=memory,
        knowledge_store=knowledge,
    )

    assert controller.memory_snapshot() == (
        "#1  PREFERENCE  ACTIVE\nUse cyan for the Ato HUD.",
    )
    assert controller.knowledge_snapshot() == ("#1  guide.md\n1 indexed chunks",)


def test_desktop_system_snapshot_is_local_and_does_not_probe_network(tmp_path) -> None:
    controller = DesktopChatController(Agent(EchoLLM()), workspace_root=tmp_path)
    lines = controller.system_snapshot()
    assert any(line.startswith("OS  ") for line in lines)
    assert any(line.startswith("CPU  ") for line in lines)
    assert lines[-1] == "NETWORK  NOT PROBED"
