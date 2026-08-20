"""Terminal interface for Ato."""

from __future__ import annotations

from collections.abc import Callable

from ato.brain.agent import Agent
from ato.config import Settings
from ato.exceptions import AtoError
from ato.providers import DeepSeekProvider

EXIT_COMMANDS = {"exit", "quit"}


def run_terminal(
    agent: Agent,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> None:
    """Run the interactive terminal loop for an existing agent."""
    write("Ato is ready. Type 'exit' or 'quit' to stop.")

    while True:
        try:
            user_input = read("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            write("\nGoodbye.")
            return

        if user_input.lower() in EXIT_COMMANDS:
            write("Goodbye.")
            return
        if not user_input:
            continue

        try:
            reply = agent.respond(user_input)
        except AtoError as exc:
            write(f"Ato error: {exc}")
            continue

        write(f"Ato: {reply}")


def main() -> None:
    """Build configured dependencies and launch Ato."""
    try:
        settings = Settings.from_env()
        provider = DeepSeekProvider(settings.deepseek_api_key, settings.model)
    except AtoError as exc:
        print(f"Unable to start Ato: {exc}")
        raise SystemExit(1) from exc

    run_terminal(Agent(provider))
