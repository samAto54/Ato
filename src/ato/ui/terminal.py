"""Terminal interface for Ato."""

from __future__ import annotations

from collections.abc import Callable

from ato.brain.agent import Agent
from ato.config import Settings
from ato.exceptions import AtoError
from ato.memory import JsonMemoryStore
from ato.providers import DeepSeekProvider

EXIT_COMMANDS = {"exit", "quit"}
CLEAR_MEMORY_COMMAND = "/clear-memory"


def run_terminal(
    agent: Agent,
    memory_store: JsonMemoryStore | None = None,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> None:
    """Run the interactive terminal loop for an existing agent."""
    write("Ato is ready. Type 'exit' or 'quit' to stop. Use /clear-memory to reset history.")

    while True:
        try:
            user_input = read("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            write("\nGoodbye.")
            return

        if user_input.lower() in EXIT_COMMANDS:
            write("Goodbye.")
            return
        if user_input.lower() == CLEAR_MEMORY_COMMAND:
            try:
                if memory_store is not None:
                    memory_store.clear()
                agent.clear_conversation()
            except AtoError as exc:
                write(f"Ato error: {exc}")
            else:
                write("Ato: Persistent conversation memory cleared.")
            continue
        if not user_input:
            continue

        try:
            reply = agent.respond(user_input)
            if memory_store is not None:
                memory_store.save_history(agent.conversation)
        except AtoError as exc:
            write(f"Ato error: {exc}")
            continue

        write(f"Ato: {reply}")


def main() -> None:
    """Build configured dependencies and launch Ato."""
    try:
        settings = Settings.from_env()
        provider = DeepSeekProvider(settings.deepseek_api_key, settings.model)
        memory_store = JsonMemoryStore(
            settings.memory_file,
            max_messages=settings.memory_max_messages,
        )
        history = memory_store.load_history()
    except AtoError as exc:
        print(f"Unable to start Ato: {exc}")
        raise SystemExit(1) from exc

    run_terminal(Agent(provider, history=history), memory_store=memory_store)
