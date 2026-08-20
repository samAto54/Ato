from collections.abc import Sequence

from ato.brain.agent import Agent
from ato.brain.messages import Message
from ato.ui.terminal import run_terminal


class EchoLLM:
    def generate(self, messages: Sequence[Message]) -> str:
        return f"Echo: {messages[-1].content}"


def test_terminal_conversation_and_exit() -> None:
    inputs = iter(["Hello", "quit"])
    output: list[str] = []
    run_terminal(Agent(EchoLLM()), read=lambda prompt: next(inputs), write=output.append)
    assert output == [
        "Ato is ready. Type 'exit' or 'quit' to stop.",
        "Ato: Echo: Hello",
        "Goodbye.",
    ]
