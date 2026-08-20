"""Terminal interface for Ato."""

from __future__ import annotations

import json
from collections.abc import Callable

from ato.brain.agent import Agent
from ato.brain.context import ContextManager
from ato.config import Settings
from ato.exceptions import AtoError
from ato.memory import JsonMemoryStore
from ato.providers import DeepSeekProvider
from ato.security import AuditLogger, PermissionManager, PermissionRequest
from ato.tools import build_read_only_registry

EXIT_COMMANDS = {"exit", "quit"}
CLEAR_MEMORY_COMMAND = "/clear-memory"


def confirm_tool(request: PermissionRequest) -> bool:
    """Ask the terminal user to approve a protected tool invocation."""
    safe_arguments = AuditLogger.redact(dict(request.arguments))
    print("\nAto requests permission:")
    print(f"  Tool: {request.tool_name}")
    print(f"  Permission: {request.level.value}")
    print(f"  Arguments: {json.dumps(safe_arguments, ensure_ascii=False)}")
    answer = input("Allow? [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


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
                memory_store.save_context(agent.conversation, agent.summary)
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
        memory_context = memory_store.load_context()
        tool_registry = build_read_only_registry(
            settings.workspace_root,
            permission_manager=PermissionManager(confirm_tool),
            audit_logger=AuditLogger(settings.audit_file),
        )
    except AtoError as exc:
        print(f"Unable to start Ato: {exc}")
        raise SystemExit(1) from exc

    run_terminal(
        Agent(
            provider,
            history=memory_context.history,
            summary=memory_context.summary,
            context_manager=ContextManager(
                max_tokens=settings.context_max_tokens,
                recent_messages=settings.context_recent_messages,
                max_summary_chars=settings.context_summary_max_chars,
                max_messages=settings.memory_max_messages,
            ),
            tools=tool_registry,
        ),
        memory_store=memory_store,
    )
