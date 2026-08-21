from collections.abc import Sequence

from ato.brain.agent import Agent
from ato.brain.messages import Message, Role
from ato.knowledge import SqliteKnowledgeStore
from ato.memory import JsonMemoryStore, SqliteLongTermMemory
from ato.ui.terminal import run_terminal


class EchoLLM:
    def generate(self, messages: Sequence[Message], tools=None) -> str:
        del tools
        return f"Echo: {messages[-1].content}"


class StreamingEchoLLM(EchoLLM):
    def stream(self, messages: Sequence[Message], tools=None):
        del tools
        yield "Echo: "
        yield messages[-1].content


def test_terminal_conversation_and_exit() -> None:
    inputs = iter(["Hello", "quit"])
    output: list[str] = []
    run_terminal(Agent(EchoLLM()), read=lambda prompt: next(inputs), write=output.append)
    assert output == [
        "Ato is ready. Type 'exit' or 'quit' to stop. Use /clear-memory to reset "
        "conversation history.",
        "Ato: Echo: Hello",
        "Goodbye.",
    ]


def test_terminal_saves_successful_turns(tmp_path) -> None:
    store = JsonMemoryStore(tmp_path / "memory.json")
    inputs = iter(["Hello", "quit"])

    run_terminal(
        Agent(EchoLLM()),
        memory_store=store,
        read=lambda prompt: next(inputs),
        write=lambda text: None,
    )

    assert store.load_history() == (
        Message(role=Role.USER, content="Hello"),
        Message(role=Role.ASSISTANT, content="Echo: Hello"),
    )


def test_terminal_clear_memory_resets_disk_and_agent(tmp_path) -> None:
    store = JsonMemoryStore(tmp_path / "memory.json")
    agent = Agent(EchoLLM())
    store.save_history([Message(role=Role.USER, content="old")])
    inputs = iter(["/clear-memory", "quit"])
    output: list[str] = []

    run_terminal(
        agent,
        memory_store=store,
        read=lambda prompt: next(inputs),
        write=output.append,
    )

    assert store.load_history() == ()
    assert agent.conversation == ()
    assert "Ato: Conversation memory cleared. Long-term facts were preserved." in output


def test_terminal_displays_streaming_chunks_and_saves_completed_turn(tmp_path) -> None:
    store = JsonMemoryStore(tmp_path / "memory.json")
    inputs = iter(["Hello", "quit"])
    chunks: list[str] = []

    run_terminal(
        Agent(StreamingEchoLLM()),
        memory_store=store,
        read=lambda prompt: next(inputs),
        write=lambda text: None,
        write_chunk=chunks.append,
    )

    assert chunks == ["Ato: ", "Echo: ", "Hello", "\n"]
    assert store.load_history()[-1] == Message(Role.ASSISTANT, "Echo: Hello")


def test_terminal_manages_explicit_long_term_memories(tmp_path) -> None:
    store = SqliteLongTermMemory(tmp_path / "facts.db")
    inputs = iter(
        [
            "/remember My favorite color is green.",
            "/edit-memory 1 My favorite color is blue.",
            "yes",
            "/memories",
            "/forget 1",
            "yes",
            "quit",
        ]
    )
    output: list[str] = []

    run_terminal(
        Agent(EchoLLM(), memory_retriever=store),
        long_term_memory=store,
        read=lambda prompt: next(inputs),
        write=output.append,
    )

    assert "Ato: Remembered as memory 1." in output
    assert "Ato: Memory updated." in output
    assert "  1: My favorite color is blue." in output
    assert "Ato: Memory forgotten." in output
    assert store.list_memories() == ()


def test_terminal_manages_knowledge_documents(tmp_path) -> None:
    (tmp_path / "notes.md").write_text("Ato knowledge", encoding="utf-8")
    store = SqliteKnowledgeStore(tmp_path / "data" / "knowledge.db", tmp_path)
    inputs = iter(
        ["/ingest notes.md", "yes", "/knowledge", "/remove-document 1", "yes", "quit"]
    )
    output: list[str] = []

    run_terminal(
        Agent(EchoLLM()),
        knowledge_store=store,
        read=lambda prompt: next(inputs),
        write=output.append,
    )

    assert any("Ingested document 1" in line for line in output)
    assert any("1: notes.md" in line for line in output)
    assert "Ato: Document removed." in output
    assert store.list_documents() == ()


def test_terminal_cancelled_ingestion_stores_nothing(tmp_path) -> None:
    (tmp_path / "private.md").write_text("private notes", encoding="utf-8")
    store = SqliteKnowledgeStore(tmp_path / "knowledge.db", tmp_path)
    inputs = iter(["/ingest private.md", "no", "quit"])

    run_terminal(
        Agent(EchoLLM()),
        knowledge_store=store,
        read=lambda prompt: next(inputs),
        write=lambda text: None,
    )

    assert store.list_documents() == ()
