import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from ato.brain.memory import CompositeMemoryRetriever, MemoryItem
from ato.exceptions import MemoryStoreError
from ato.memory import MemoryCategory, SqliteLongTermMemory


def test_long_term_memory_persists_retrieves_and_forgets(tmp_path) -> None:
    path = tmp_path / "facts.db"
    store = SqliteLongTermMemory(path)
    color = store.remember("My favorite color is green.")
    city = store.remember("I live in Accra.")

    restored = SqliteLongTermMemory(path)
    assert restored.search("What is my favorite color?") == (color,)
    assert restored.list_memories() == (city, color)
    assert restored.remember("My favorite color is green.") == color
    assert restored.forget(color.id) is True
    assert restored.forget(color.id) is False


@pytest.mark.parametrize(
    "content",
    ["My password is hunter2", "API key: abc123", "access_token is private", "sk-abcdefghijk"],
)
def test_long_term_memory_rejects_likely_secrets(tmp_path, content: str) -> None:
    store = SqliteLongTermMemory(tmp_path / "facts.db")

    with pytest.raises(MemoryStoreError, match="Refusing"):
        store.remember(content)


def test_long_term_memory_updates_existing_fact_and_search_index(tmp_path) -> None:
    store = SqliteLongTermMemory(tmp_path / "facts.db")
    memory = store.remember("I live in Kumasi.")

    updated = store.update(memory.id, "I now live in Accra.")

    assert updated is not None
    assert updated.id == memory.id
    assert store.search("Kumasi") == ()
    assert store.search("Where do I live?") == (updated,)


def test_long_term_memory_update_is_bounded_and_deduplicated(tmp_path) -> None:
    store = SqliteLongTermMemory(tmp_path / "facts.db")
    first = store.remember("My favorite color is green.")
    second = store.remember("I live in Accra.")

    assert store.update(999, "A harmless fact.") is None
    with pytest.raises(MemoryStoreError, match=f"memory {first.id}"):
        store.update(second.id, first.content)
    with pytest.raises(MemoryStoreError, match="Refusing"):
        store.update(second.id, "My password is hunter2")

    assert store.list_memories() == (second, first)


def test_long_term_memory_categories_are_persisted_and_editable(tmp_path) -> None:
    path = tmp_path / "facts.db"
    store = SqliteLongTermMemory(path)
    preference = store.remember("I prefer concise answers.", MemoryCategory.PREFERENCE)

    restored = SqliteLongTermMemory(path)
    assert preference.source == "long-term memory:preference"
    assert restored.search("How do I prefer answers?") == (preference,)

    updated = restored.update(preference.id, preference.content, MemoryCategory.DECISION)
    assert updated is not None
    assert updated.source == "long-term memory:decision"

    with pytest.raises(MemoryStoreError, match="category"):
        restored.remember("Uncategorized content", "unknown")


def test_long_term_memory_migrates_existing_database_to_fact_category(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "content TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO memories VALUES (1, 'Legacy fact', 'timestamp')")

    store = SqliteLongTermMemory(path)

    assert store.list_memories()[0].source == "long-term memory:fact"


def test_memory_archive_restore_and_expiration_control_retrieval(tmp_path) -> None:
    store = SqliteLongTermMemory(tmp_path / "facts.db")
    memory = store.remember("The launch codename is Starling.", MemoryCategory.PROJECT)

    assert store.archive(memory.id) is True
    assert store.search("launch codename") == ()
    assert store.list_memories() == ()
    archived = store.list_records(include_inactive=True)[0]
    assert archived.archived_at is not None

    assert store.restore(memory.id) is True
    assert store.search("launch codename") == (memory,)
    retrieved = store.list_records()[0]
    assert retrieved.last_retrieved_at is not None

    assert store.set_expiration(memory.id, datetime.now(UTC) - timedelta(seconds=1)) is True
    assert store.search("launch codename") == ()
    assert store.list_memories() == ()
    with pytest.raises(MemoryStoreError, match="inactive memory"):
        store.remember(memory.content, MemoryCategory.PROJECT)

    assert store.set_expiration(memory.id, None) is True
    assert store.search("launch codename") == (memory,)


def test_memory_expiration_requires_timezone_and_valid_id(tmp_path) -> None:
    store = SqliteLongTermMemory(tmp_path / "facts.db")

    with pytest.raises(ValueError, match="timezone"):
        store.set_expiration(1, datetime.now())
    assert store.set_expiration(999, datetime.now(UTC) + timedelta(days=1)) is False
    assert store.archive(999) is False
    assert store.restore(999) is False


def test_lifecycle_migration_backfills_update_timestamp(tmp_path) -> None:
    path = tmp_path / "legacy-lifecycle.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "content TEXT NOT NULL UNIQUE, category TEXT NOT NULL DEFAULT 'fact', "
            "created_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO memories VALUES (1, 'Legacy fact', 'fact', 'timestamp')")

    record = SqliteLongTermMemory(path).list_records()[0]

    assert record.updated_at == record.created_at == "timestamp"
    assert record.last_retrieved_at is None
    assert record.archived_at is None
    assert record.expires_at is None


def test_memory_ranking_uses_phrase_order_and_category_intent(tmp_path) -> None:
    store = SqliteLongTermMemory(tmp_path / "facts.db")
    preference = store.remember(
        "My response style is detailed.", MemoryCategory.PREFERENCE
    )
    store.remember("My response style is concise.", MemoryCategory.FACT)
    phrase_match = store.remember("The Alpha launch plan starts Tuesday.", MemoryCategory.PROJECT)
    store.remember("Tuesday reviews cover the Alpha plan before launch.", MemoryCategory.PROJECT)

    assert store.search("What is my response style preference?")[0] == preference
    assert store.search("Alpha launch plan Tuesday")[0] == phrase_match


def test_memory_deduplication_preserves_conflicting_facts(tmp_path) -> None:
    store = SqliteLongTermMemory(tmp_path / "facts.db")
    store.remember("My favorite color is green.", MemoryCategory.PREFERENCE)
    store.remember("my FAVORITE color is green!", MemoryCategory.PREFERENCE)
    blue = store.remember("My favorite color is blue.", MemoryCategory.PREFERENCE)

    results = store.search("What is my favorite color?", limit=5)

    assert len(results) == 2
    assert blue in results
    assert any("green" in item.content.casefold() for item in results)


def test_composite_memory_retrieval_deduplicates_sources_and_preserves_fairness() -> None:
    class Retriever:
        def __init__(self, *items: MemoryItem) -> None:
            self.items = items

        def search(self, query: str, limit: int = 5) -> tuple[MemoryItem, ...]:
            del query
            return self.items[:limit]

    shared = "Ato uses SQLite for durable memory."
    composite = CompositeMemoryRetriever(
        Retriever(MemoryItem(1, shared), MemoryItem(2, "First source detail.")),
        Retriever(
            MemoryItem(9, "ATO uses sqlite for durable memory!", "knowledge design.md#0"),
            MemoryItem(10, "Second source."),
        ),
    )

    results = composite.search("memory design", limit=3)

    assert [item.content for item in results] == [shared, "First source detail.", "Second source."]
