from collections.abc import Sequence

from ato.brain.agent import Agent
from ato.brain.messages import Message, Role
from ato.memory import JsonMemoryStore
from ato.ui.terminal import run_terminal


class EchoLLM:
    def generate(self, messages: Sequence[Message]) -> str:
        return f"Echo: {messages[-1].content}"


def test_terminal_conversation_and_exit() -> None:
    inputs = iter(["Hello", "quit"])
    output: list[str] = []
    run_terminal(Agent(EchoLLM()), read=lambda prompt: next(inputs), write=output.append)
    assert output == [
        "Ato is ready. Type 'exit' or 'quit' to stop. Use /clear-memory to reset history.",
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
    assert "Ato: Persistent conversation memory cleared." in output
