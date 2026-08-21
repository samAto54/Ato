"""Terminal interface for Ato."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ato.brain.agent import Agent
from ato.brain.context import ContextManager
from ato.brain.memory import CompositeMemoryRetriever
from ato.coding import SqliteEditCheckpointStore
from ato.config import Settings
from ato.exceptions import AtoError
from ato.knowledge import SqliteKnowledgeStore
from ato.memory import JsonMemoryStore, MemoryCategory, SqliteLongTermMemory
from ato.providers import DeepSeekProvider
from ato.research import SqliteResearchStore
from ato.security import AuditLogger, PermissionManager, PermissionRequest
from ato.tools import build_phase3_registry
from ato.tools.github import GitHubClient
from ato.tools.search import BraveSearchClient, TavilySearchClient

EXIT_COMMANDS = {"exit", "quit"}
CLEAR_MEMORY_COMMAND = "/clear-memory"
LIST_MEMORIES_COMMAND = "/memories"
REMEMBER_PREFIX = "/remember "
FORGET_PREFIX = "/forget "
EDIT_MEMORY_PREFIX = "/edit-memory "
LIST_ALL_MEMORIES_COMMAND = "/all-memories"
ARCHIVE_MEMORY_PREFIX = "/archive-memory "
RESTORE_MEMORY_PREFIX = "/restore-memory "
EXPIRE_MEMORY_PREFIX = "/expire-memory "
CLEAR_EXPIRATION_PREFIX = "/clear-memory-expiration "
INGEST_PREFIX = "/ingest "
LIST_KNOWLEDGE_COMMAND = "/knowledge"
REMOVE_DOCUMENT_PREFIX = "/remove-document "


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
    knowledge_store: SqliteKnowledgeStore | None = None,
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
                content, category = _parse_memory_content(user_input[len(REMEMBER_PREFIX) :])
                item = long_term_memory.remember(
                    content, category if category is not None else MemoryCategory.FACT
                )
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
                        category = item.source.partition(":")[2] or MemoryCategory.FACT.value
                        write(f"  {item.id} [{category}]: {item.content}")
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
        if user_input.lower().startswith(EDIT_MEMORY_PREFIX):
            if long_term_memory is None:
                write("Ato error: Long-term memory is unavailable.")
                continue
            raw_edit = user_input[len(EDIT_MEMORY_PREFIX) :].strip()
            raw_id, separator, raw_content = raw_edit.partition(" ")
            try:
                memory_id = int(raw_id)
            except ValueError:
                write("Ato error: Use /edit-memory <id> <replacement fact>.")
                continue
            if not separator or not raw_content.strip():
                write("Ato error: Use /edit-memory <id> <replacement fact>.")
                continue
            content, category = _parse_memory_content(raw_content)
            confirmation = read(f"Replace long-term memory {memory_id}? [y/N]: ").strip().lower()
            if confirmation not in {"y", "yes"}:
                write("Ato: Memory edit cancelled.")
                continue
            try:
                updated = long_term_memory.update(memory_id, content, category)
            except (AtoError, ValueError) as exc:
                write(f"Ato error: {exc}")
            else:
                write("Ato: Memory updated." if updated else "Ato: Memory ID not found.")
            continue
        if user_input.lower() == EDIT_MEMORY_PREFIX.strip():
            write("Ato error: Use /edit-memory <id> <replacement fact>.")
            continue
        if user_input.lower() == LIST_ALL_MEMORIES_COMMAND:
            if long_term_memory is None:
                write("Ato error: Long-term memory is unavailable.")
                continue
            try:
                records = long_term_memory.list_records(include_inactive=True)
            except AtoError as exc:
                write(f"Ato error: {exc}")
            else:
                write("Ato memory lifecycle records:" if records else "Ato: No memories saved.")
                now = datetime.now(UTC).isoformat()
                for record in records:
                    if record.archived_at is not None:
                        status = "archived"
                    elif record.expires_at is not None and record.expires_at <= now:
                        status = "expired"
                    else:
                        status = "active"
                    write(f"  {record.id} [{record.category.value}, {status}]: {record.content}")
            continue
        lifecycle_usage = {
            ARCHIVE_MEMORY_PREFIX.strip(): "/archive-memory <id>",
            RESTORE_MEMORY_PREFIX.strip(): "/restore-memory <id>",
            EXPIRE_MEMORY_PREFIX.strip(): "/expire-memory <id> <days from 1 to 3650>",
            CLEAR_EXPIRATION_PREFIX.strip(): "/clear-memory-expiration <id>",
        }
        if user_input.lower() in lifecycle_usage:
            write(f"Ato error: Use {lifecycle_usage[user_input.lower()]}.")
            continue
        if user_input.lower().startswith(ARCHIVE_MEMORY_PREFIX):
            _handle_memory_archive_change(
                user_input[len(ARCHIVE_MEMORY_PREFIX) :],
                archive=True,
                store=long_term_memory,
                read=read,
                write=write,
            )
            continue
        if user_input.lower().startswith(RESTORE_MEMORY_PREFIX):
            _handle_memory_archive_change(
                user_input[len(RESTORE_MEMORY_PREFIX) :],
                archive=False,
                store=long_term_memory,
                read=read,
                write=write,
            )
            continue
        if user_input.lower().startswith(EXPIRE_MEMORY_PREFIX):
            if long_term_memory is None:
                write("Ato error: Long-term memory is unavailable.")
                continue
            raw_id, separator, raw_days = user_input[len(EXPIRE_MEMORY_PREFIX) :].partition(" ")
            try:
                memory_id = int(raw_id)
                days = int(raw_days) if separator else 0
                if not 1 <= days <= 3650:
                    raise ValueError
            except ValueError:
                write("Ato error: Use /expire-memory <id> <days from 1 to 3650>.")
                continue
            answer = read(f"Expire memory {memory_id} in {days} days? [y/N]: ").strip().lower()
            if answer not in {"y", "yes"}:
                write("Ato: Expiration change cancelled.")
                continue
            try:
                changed = long_term_memory.set_expiration(
                    memory_id, datetime.now(UTC) + timedelta(days=days)
                )
            except (AtoError, ValueError) as exc:
                write(f"Ato error: {exc}")
            else:
                write("Ato: Expiration set." if changed else "Ato: Memory ID not found.")
            continue
        if user_input.lower().startswith(CLEAR_EXPIRATION_PREFIX):
            if long_term_memory is None:
                write("Ato error: Long-term memory is unavailable.")
                continue
            raw_id = user_input[len(CLEAR_EXPIRATION_PREFIX) :].strip()
            try:
                memory_id = int(raw_id)
            except ValueError:
                write("Ato error: Use /clear-memory-expiration <id>.")
                continue
            answer = read(f"Clear expiration for memory {memory_id}? [y/N]: ").strip().lower()
            if answer not in {"y", "yes"}:
                write("Ato: Expiration change cancelled.")
                continue
            try:
                changed = long_term_memory.set_expiration(memory_id, None)
            except (AtoError, ValueError) as exc:
                write(f"Ato error: {exc}")
            else:
                write("Ato: Expiration cleared." if changed else "Ato: Memory ID not found.")
            continue
        if user_input.lower().startswith(INGEST_PREFIX):
            if knowledge_store is None:
                write("Ato error: Knowledge storage is unavailable.")
                continue
            answer = read(
                "Ingest this document and allow relevant excerpts to be sent to the "
                "configured model? [y/N]: "
            ).strip().lower()
            if answer not in {"y", "yes"}:
                write("Ato: Document ingestion cancelled.")
                continue
            try:
                document = knowledge_store.ingest(user_input[len(INGEST_PREFIX) :])
            except AtoError as exc:
                write(f"Ato error: {exc}")
            else:
                write(
                    f"Ato: Ingested document {document.id} ({document.path}, "
                    f"{document.chunks} chunks)."
                )
            continue
        if user_input.lower() == INGEST_PREFIX.strip():
            write("Ato error: Use /ingest followed by a workspace-relative file path.")
            continue
        if user_input.lower() == LIST_KNOWLEDGE_COMMAND:
            if knowledge_store is None:
                write("Ato error: Knowledge storage is unavailable.")
                continue
            try:
                documents = knowledge_store.list_documents()
            except AtoError as exc:
                write(f"Ato error: {exc}")
            else:
                if not documents:
                    write("Ato: No knowledge documents ingested.")
                else:
                    write("Ato knowledge documents:")
                    for document in documents:
                        write(f"  {document.id}: {document.path} ({document.chunks} chunks)")
            continue
        if user_input.lower().startswith(REMOVE_DOCUMENT_PREFIX):
            if knowledge_store is None:
                write("Ato error: Knowledge storage is unavailable.")
                continue
            raw_id = user_input[len(REMOVE_DOCUMENT_PREFIX) :].strip()
            try:
                document_id = int(raw_id)
            except ValueError:
                write("Ato error: Use /remove-document followed by a numeric document ID.")
                continue
            answer = read(f"Remove knowledge document {document_id}? [y/N]: ").strip().lower()
            if answer not in {"y", "yes"}:
                write("Ato: Document removal cancelled.")
                continue
            try:
                removed = knowledge_store.remove_document(document_id)
            except (AtoError, ValueError) as exc:
                write(f"Ato error: {exc}")
            else:
                write("Ato: Document removed." if removed else "Ato: Document ID not found.")
            continue
        if user_input.lower() == REMOVE_DOCUMENT_PREFIX.strip():
            write("Ato error: Use /remove-document followed by a numeric document ID.")
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


def _parse_memory_content(value: str) -> tuple[str, MemoryCategory | None]:
    """Extract an optional recognized ``category:`` prefix from memory text."""
    prefix, separator, content = value.strip().partition(":")
    if separator:
        try:
            return content.strip(), MemoryCategory(prefix.strip().casefold())
        except ValueError:
            pass
    return value.strip(), None


def _handle_memory_archive_change(
    raw_id: str,
    *,
    archive: bool,
    store: SqliteLongTermMemory | None,
    read: Callable[[str], str],
    write: Callable[[str], None],
) -> None:
    """Confirm and apply one archive or restore operation."""
    if store is None:
        write("Ato error: Long-term memory is unavailable.")
        return
    try:
        memory_id = int(raw_id.strip())
    except ValueError:
        command = "/archive-memory" if archive else "/restore-memory"
        write(f"Ato error: Use {command} <id>.")
        return
    verb = "Archive" if archive else "Restore"
    answer = read(f"{verb} long-term memory {memory_id}? [y/N]: ").strip().lower()
    if answer not in {"y", "yes"}:
        write(f"Ato: Memory {verb.casefold()} cancelled.")
        return
    try:
        changed = store.archive(memory_id) if archive else store.restore(memory_id)
    except (AtoError, ValueError) as exc:
        write(f"Ato error: {exc}")
    else:
        write(f"Ato: Memory {verb.casefold()}d." if changed else "Ato: Memory ID not found.")


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
        knowledge_store = SqliteKnowledgeStore(settings.knowledge_file, settings.workspace_root)
        research_store = SqliteResearchStore(settings.research_file)
        checkpoint_store = SqliteEditCheckpointStore(settings.edit_checkpoint_file)
        tool_registry = build_phase3_registry(
            settings.workspace_root,
            permission_manager=PermissionManager(confirm_tool),
            audit_logger=AuditLogger(settings.audit_file),
            web_searcher=(
                TavilySearchClient(settings.tavily_api_key)
                if settings.tavily_api_key
                else (
                    BraveSearchClient(settings.brave_search_api_key)
                    if settings.brave_search_api_key
                    else None
                )
            ),
            research_store=research_store,
            checkpoint_store=checkpoint_store,
            github_client=(
                GitHubClient(settings.github_repository, settings.github_token)
                if settings.github_repository
                else None
            ),
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
            memory_retriever=CompositeMemoryRetriever(long_term_memory, knowledge_store),
            tools=tool_registry,
        ),
        memory_store=memory_store,
        long_term_memory=long_term_memory,
        knowledge_store=knowledge_store,
    )
