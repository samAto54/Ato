"""Terminal interface for Ato."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

from ato.brain.agent import Agent
from ato.brain.context import ContextManager
from ato.config import Settings
from ato.exceptions import AtoError
from ato.memory import JsonMemoryStore, SqliteLongTermMemory
from ato.providers import DeepSeekProvider
from ato.security import AuditLogger, PermissionManager, PermissionRequest
from ato.tools import build_phase3_registry

EXIT_COMMANDS = {"exit", "quit"}
CLEAR_MEMORY_COMMAND = "/clear-memory"
LIST_MEMORIES_COMMAND = "/memories"
REMEMBER_PREFIX = "/remember "
FORGET_PREFIX = "/forget "


def confirm_tool(request: PermissionRequest) -> bool:
    """Ask the terminal user to approve a protected tool invocation."""
    safe_arguments = AuditLogger.confirmation_view(request.arguments)
    print("\nAto requests permission:")
    print(f"  Tool: {request.tool_name}")
    print(f"  Permission: {request.level.value}")
    print(f"  Arguments: {json.dumps(safe_arguments, ensure_ascii=False)}")
    answer = input("Allow? [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


def run_terminal(
    agent: Agent,
    memory_store: JsonMemoryStore | None = None,
    long_term_memory: SqliteLongTermMemory | None = None,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
    write_chunk: Callable[[str], None] | None = None,
) -> None:
    """Run the interactive terminal loop for an existing agent."""
    write(
        "Ato is ready. Type 'exit' or 'quit' to stop. Use /clear-memory to reset "
        "conversation history."
    )

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
                write("Ato: Conversation memory cleared. Long-term facts were preserved.")
            continue
        if user_input.lower().startswith(REMEMBER_PREFIX):
            if long_term_memory is None:
                write("Ato error: Long-term memory is unavailable.")
                continue
            try:
                item = long_term_memory.remember(user_input[len(REMEMBER_PREFIX) :])
            except AtoError as exc:
                write(f"Ato error: {exc}")
            else:
                write(f"Ato: Remembered as memory {item.id}.")
            continue
        if user_input.lower() == REMEMBER_PREFIX.strip():
            write("Ato error: Use /remember followed by the fact to save.")
            continue
        if user_input.lower() == LIST_MEMORIES_COMMAND:
            if long_term_memory is None:
                write("Ato error: Long-term memory is unavailable.")
                continue
            try:
                memories = long_term_memory.list_memories()
            except AtoError as exc:
                write(f"Ato error: {exc}")
            else:
                if memories:
                    write("Ato long-term memories:")
                    for item in memories:
                        write(f"  {item.id}: {item.content}")
                else:
                    write("Ato: No long-term memories saved.")
            continue
        if user_input.lower().startswith(FORGET_PREFIX):
            if long_term_memory is None:
                write("Ato error: Long-term memory is unavailable.")
                continue
            raw_id = user_input[len(FORGET_PREFIX) :].strip()
            try:
                memory_id = int(raw_id)
            except ValueError:
                write("Ato error: Use /forget followed by a numeric memory ID.")
                continue
            confirmation = read(f"Delete long-term memory {memory_id}? [y/N]: ").strip().lower()
            if confirmation not in {"y", "yes"}:
                write("Ato: Forget cancelled.")
                continue
            try:
                deleted = long_term_memory.forget(memory_id)
            except (AtoError, ValueError) as exc:
                write(f"Ato error: {exc}")
            else:
                write("Ato: Memory forgotten." if deleted else "Ato: Memory ID not found.")
            continue
        if user_input.lower() == FORGET_PREFIX.strip():
            write("Ato error: Use /forget followed by a numeric memory ID.")
            continue
        if not user_input:
            continue

        streaming_started = False
        emit = write_chunk or _write_terminal_chunk
        try:
            if agent.can_stream:
                chunks = agent.respond_stream(user_input)
                first = next(chunks)
                streaming_started = True
                emit("Ato: ")
                emit(first)
                for chunk in chunks:
                    emit(chunk)
                emit("\n")
            else:
                reply = agent.respond(user_input)
                write(f"Ato: {reply}")
            if memory_store is not None:
                memory_store.save_context(agent.conversation, agent.summary)
        except StopIteration:
            write("Ato error: The language model returned an empty response.")
            continue
        except AtoError as exc:
            if streaming_started:
                emit("\n")
            write(f"Ato error: {exc}")
            continue


def _write_terminal_chunk(text: str) -> None:
    """Write one response fragment without inserting extra line breaks."""
    sys.stdout.write(text)
    sys.stdout.flush()


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
        long_term_memory = SqliteLongTermMemory(settings.long_term_memory_file)
        tool_registry = build_phase3_registry(
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
            memory_retriever=long_term_memory,
            tools=tool_registry,
        ),
        memory_store=memory_store,
        long_term_memory=long_term_memory,
    )
