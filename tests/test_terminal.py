from collections.abc import Sequence

from ato.brain.agent import Agent
from ato.brain.messages import Message, Role
from ato.knowledge import SqliteKnowledgeStore
from ato.memory import JsonMemoryStore, SqliteLongTermMemory
from ato.security import PermissionManager
from ato.tools import build_phase3_registry
from ato.ui.terminal import HELP_TEXT, run_terminal


class EchoLLM:
    def generate(self, messages: Sequence[Message], tools=None) -> str:
        del tools
        return f"Echo: {messages[-1].content}"


class StreamingEchoLLM(EchoLLM):
    def stream(self, messages: Sequence[Message], tools=None):
        del tools
        yield "Echo: "
        yield messages[-1].content


class RecordingClipboard:
    def __init__(self) -> None:
        self.values: list[str] = []

    def write(self, text: str) -> None:
        self.values.append(text)


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


def test_terminal_help_lists_commands_without_calling_the_model() -> None:
    inputs = iter(["/help", "quit"])
    output: list[str] = []

    run_terminal(Agent(EchoLLM()), read=lambda prompt: next(inputs), write=output.append)

    assert HELP_TEXT in output
    assert "/voice <seconds>" in HELP_TEXT
    assert "/speak-last" in HELP_TEXT
    assert not any(line.startswith("Ato: Echo:") for line in output)


def test_terminal_status_reports_local_subsystem_availability() -> None:
    inputs = iter(["/status", "quit"])
    output: list[str] = []

    run_terminal(Agent(EchoLLM()), read=lambda prompt: next(inputs), write=output.append)

    status = next(line for line in output if line.startswith("Ato local status:"))
    assert "conversation: ready" in status
    assert "persistent conversation: not configured" in status
    assert "voice input: not configured" in status
    assert not any(line.startswith("Ato: Echo:") for line in output)


def test_terminal_rejects_unknown_slash_command_without_calling_model() -> None:
    inputs = iter(["/memorries", "quit"])
    output: list[str] = []

    run_terminal(Agent(EchoLLM()), read=lambda prompt: next(inputs), write=output.append)

    assert "Ato error: Unknown command /memorries. Use /help to list commands." in output
    assert not any(line.startswith("Ato: Echo:") for line in output)


def test_terminal_history_shows_bounded_recent_messages_without_model_call() -> None:
    history = [
        Message(Role.USER, f"message {index}\nwith spacing")
        if index % 2 == 0
        else Message(Role.ASSISTANT, "x" * 220)
        for index in range(22)
    ]
    inputs = iter(["/history", "quit"])
    output: list[str] = []

    run_terminal(
        Agent(EchoLLM(), history=history),
        read=lambda prompt: next(inputs),
        write=output.append,
    )

    assert "Ato recent conversation:" in output
    assert "  ... 2 older messages omitted" in output
    assert "  user: message 2 with spacing" in output
    assistant_lines = [line for line in output if line.startswith("  assistant:")]
    assert assistant_lines
    assert all(len(line.removeprefix("  assistant: ")) == 200 for line in assistant_lines)
    assert not any(line.startswith("Ato: Echo:") for line in output)


def test_terminal_history_reports_empty_conversation() -> None:
    inputs = iter(["/history", "quit"])
    output: list[str] = []

    run_terminal(Agent(EchoLLM()), read=lambda prompt: next(inputs), write=output.append)

    assert "Ato: No conversation messages yet." in output


def test_terminal_copies_latest_assistant_reply_after_confirmation(tmp_path) -> None:
    clipboard = RecordingClipboard()
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        clipboard_writer=clipboard,
    )
    inputs = iter(["first", "second", "/copy-last", "quit"])
    output: list[str] = []

    run_terminal(
        Agent(EchoLLM()),
        tool_registry=registry,
        read=lambda prompt: next(inputs),
        write=output.append,
    )

    assert clipboard.values == ["Echo: second"]
    assert "Ato: Copied the latest reply to the clipboard." in output


def test_terminal_copy_last_requires_existing_reply(tmp_path) -> None:
    clipboard = RecordingClipboard()
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        clipboard_writer=clipboard,
    )
    inputs = iter(["/copy-last", "quit"])
    output: list[str] = []

    run_terminal(
        Agent(EchoLLM()),
        tool_registry=registry,
        read=lambda prompt: next(inputs),
        write=output.append,
    )

    assert clipboard.values == []
    assert "Ato error: There is no assistant reply to copy yet." in output


def test_terminal_exports_bounded_plain_text_history(tmp_path) -> None:
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
    )
    history = [
        Message(Role.USER, "first line\nsecond line"),
        Message(Role.ASSISTANT, "answer"),
    ]
    inputs = iter(["/export-history exports/chat.txt", "quit"])
    output: list[str] = []

    run_terminal(
        Agent(EchoLLM(), history=history),
        tool_registry=registry,
        read=lambda prompt: next(inputs),
        write=output.append,
    )

    exported = (tmp_path / "exports" / "chat.txt").read_text(encoding="utf-8")
    assert "USER:\n  first line\n  second line" in exported
    assert "ASSISTANT:\n  answer" in exported
    assert "Ato: Exported conversation history to exports/chat.txt." in output


def test_terminal_history_export_never_overwrites(tmp_path) -> None:
    target = tmp_path / "chat.txt"
    target.write_text("keep", encoding="utf-8")
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
    )
    inputs = iter(["/export-history chat.txt", "quit"])
    output: list[str] = []

    run_terminal(
        Agent(EchoLLM(), history=[Message(Role.USER, "private")]),
        tool_registry=registry,
        read=lambda prompt: next(inputs),
        write=output.append,
    )

    assert target.read_text(encoding="utf-8") == "keep"
    assert any("will not overwrite" in line for line in output)


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
            "/remember preference: My favorite color is green.",
            "/edit-memory 1 decision: My favorite color is blue.",
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
    assert "  1 [decision]: My favorite color is blue." in output
    assert "Ato: Memory forgotten." in output
    assert store.list_memories() == ()


def test_terminal_manages_memory_lifecycle(tmp_path) -> None:
    store = SqliteLongTermMemory(tmp_path / "facts.db")
    store.remember("Temporary project detail.", "project")
    inputs = iter(
        [
            "/archive-memory 1",
            "yes",
            "/all-memories",
            "/restore-memory 1",
            "yes",
            "/expire-memory 1 30",
            "yes",
            "/clear-memory-expiration 1",
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

    assert "Ato: Memory archived." in output
    assert "  1 [project, archived]: Temporary project detail." in output
    assert "Ato: Memory restored." in output
    assert "Ato: Expiration set." in output
    assert "Ato: Expiration cleared." in output
    assert store.list_memories()[0].content == "Temporary project detail."


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
