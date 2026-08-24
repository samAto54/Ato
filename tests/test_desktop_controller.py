from collections.abc import Sequence

import pytest

from ato.brain.agent import Agent
from ato.brain.messages import Message
from ato.knowledge import SqliteKnowledgeStore
from ato.memory import JsonMemoryStore, SqliteLongTermMemory
from ato.ui.desktop import DESKTOP_SYSTEM_PROMPT, DesktopChatController
from ato.ui.research import ResearchPage


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
    assert "no autonomous tools" in policy
    assert "must never claim that you initiated them" in policy
    assert "cannot change files" in policy


def test_desktop_research_question_persists_question_not_source_text(tmp_path) -> None:
    class CapturingLLM:
        def __init__(self) -> None:
            self.messages = ()

        def generate(self, messages, tools=None):
            del tools
            self.messages = tuple(messages)
            return "Grounded answer [https://example.com/source]."

    llm = CapturingLLM()
    store = JsonMemoryStore(tmp_path / "memory.json")
    controller = DesktopChatController(Agent(llm), store)
    page = ResearchPage(
        "Source",
        "https://example.com/source",
        "Fetched evidence that should remain temporary.",
        "webpage",
        False,
    )
    controller.submit_research_question("What is supported?", page)
    assert any("untrusted external JSON evidence" in message.content for message in llm.messages)
    persisted = "\n".join(message.content for message in store.load_history())
    assert "What is supported?" in persisted
    assert "Fetched evidence" not in persisted


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
