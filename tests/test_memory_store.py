import json

import pytest

from ato.brain.messages import Message, Role
from ato.exceptions import MemoryStoreError
from ato.memory import JsonMemoryStore


def test_memory_round_trip_survives_new_store_instance(tmp_path) -> None:
    path = tmp_path / "memory.json"
    first_store = JsonMemoryStore(path)
    first_store.save_history(
        [Message(Role.USER, "My name is Sam"), Message(Role.ASSISTANT, "Hello Sam")]
    )

    restored = JsonMemoryStore(path).load_history()

    assert restored == (
        Message(Role.USER, "My name is Sam"),
        Message(Role.ASSISTANT, "Hello Sam"),
    )


def test_memory_store_keeps_only_configured_recent_messages(tmp_path) -> None:
    store = JsonMemoryStore(tmp_path / "memory.json", max_messages=2)
    store.save_history(
        [
            Message(Role.USER, "old"),
            Message(Role.ASSISTANT, "old reply"),
            Message(Role.USER, "recent"),
            Message(Role.ASSISTANT, "recent reply"),
        ]
    )

    assert store.load_history() == (
        Message(Role.USER, "recent"),
        Message(Role.ASSISTANT, "recent reply"),
    )


def test_memory_store_rejects_corrupt_data(tmp_path) -> None:
    path = tmp_path / "memory.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(MemoryStoreError, match="Could not read memory file"):
        JsonMemoryStore(path).load_history()


def test_memory_store_rejects_system_messages_in_file(tmp_path) -> None:
    path = tmp_path / "memory.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "history": [{"role": "system", "content": "unsafe override"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(MemoryStoreError, match="invalid message"):
        JsonMemoryStore(path).load_history()


def test_clear_removes_persisted_memory(tmp_path) -> None:
    path = tmp_path / "memory.json"
    store = JsonMemoryStore(path)
    store.save_history([Message(Role.USER, "remember me")])

    store.clear()

    assert store.load_history() == ()


def test_memory_context_persists_summary_and_loads_version_one(tmp_path) -> None:
    path = tmp_path / "memory.json"
    store = JsonMemoryStore(path)
    messages = [Message(Role.USER, "recent")]
    store.save_context(messages, "Earlier discussion")

    restored = store.load_context()
    assert restored.summary == "Earlier discussion"
    assert restored.history == tuple(messages)

    path.write_text(
        json.dumps({"version": 1, "history": [{"role": "user", "content": "legacy"}]}),
        encoding="utf-8",
    )
    legacy = store.load_context()
    assert legacy.summary == ""
    assert legacy.history == (Message(Role.USER, "legacy"),)
