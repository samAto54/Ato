import hashlib
import json

import pytest

from ato.coding import SqliteEditCheckpointStore
from ato.exceptions import CheckpointStoreError, ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_checkpoint_store_persists_lists_loads_and_marks_restored(tmp_path) -> None:
    path = tmp_path / "data" / "checkpoints.db"
    store = SqliteEditCheckpointStore(path)
    record = store.create("module.py", "old\n", _sha("old\n"), _sha("new\n"))

    restored = SqliteEditCheckpointStore(path)
    assert restored.list_checkpoints() == (record,)
    checkpoint = restored.load(record.id)
    assert checkpoint is not None and checkpoint.original_content == "old\n"
    assert restored.mark_restored(record.id) is True
    assert restored.mark_restored(record.id) is False
    assert restored.list_checkpoints()[0].restored_at is not None


def test_checkpoint_store_validates_content_digest(tmp_path) -> None:
    store = SqliteEditCheckpointStore(tmp_path / "checkpoints.db")

    with pytest.raises(CheckpointStoreError, match="does not match"):
        store.create("module.py", "old", _sha("different"), _sha("new"))


def test_previewed_edit_creates_one_time_digest_guarded_rollback(tmp_path) -> None:
    path = tmp_path / "module.py"
    path.write_text("old\n", encoding="utf-8")
    store = SqliteEditCheckpointStore(tmp_path / "data" / "checkpoints.db")
    allowed = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        checkpoint_store=store,
    )
    preview = json.loads(
        allowed.execute(
            "preview_text_change", {"path": "module.py", "old_text": "old", "new_text": "new"}
        )
    )
    edit = json.loads(
        allowed.execute(
            "replace_text_in_file",
            {
                "path": "module.py",
                "old_text": "old",
                "new_text": "new",
                "expected_sha256": preview["original_sha256"],
            },
        )
    )

    assert edit["checkpoint_id"] == 1
    assert path.read_text(encoding="utf-8") == "new\n"
    listing = json.loads(allowed.execute("list_edit_checkpoints", {}))
    assert listing["checkpoints"][0]["restored"] is False

    denied = build_phase3_registry(tmp_path, checkpoint_store=store)
    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute("rollback_text_edit", {"checkpoint_id": 1})
    assert path.read_text(encoding="utf-8") == "new\n"

    rollback = json.loads(allowed.execute("rollback_text_edit", {"checkpoint_id": 1}))
    assert rollback["restored_sha256"] == preview["original_sha256"]
    assert path.read_text(encoding="utf-8") == "old\n"
    with pytest.raises(ToolError, match="already been restored"):
        allowed.execute("rollback_text_edit", {"checkpoint_id": 1})


def test_checkpoint_rollback_refuses_to_overwrite_newer_changes(tmp_path) -> None:
    path = tmp_path / "module.py"
    path.write_text("old\n", encoding="utf-8")
    store = SqliteEditCheckpointStore(tmp_path / "checkpoints.db")
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        checkpoint_store=store,
    )
    preview = json.loads(
        registry.execute(
            "preview_text_change", {"path": "module.py", "old_text": "old", "new_text": "new"}
        )
    )
    registry.execute(
        "replace_text_in_file",
        {
            "path": "module.py",
            "old_text": "old",
            "new_text": "new",
            "expected_sha256": preview["original_sha256"],
        },
    )
    path.write_text("newer work\n", encoding="utf-8")

    with pytest.raises(ToolError, match="will not overwrite newer work"):
        registry.execute("rollback_text_edit", {"checkpoint_id": 1})

    assert path.read_text(encoding="utf-8") == "newer work\n"
    assert store.list_checkpoints()[0].restored_at is None
