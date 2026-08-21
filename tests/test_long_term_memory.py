import pytest

from ato.exceptions import MemoryStoreError
from ato.memory import SqliteLongTermMemory


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
