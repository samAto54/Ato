import json
import os

import pytest

from ato.coding import SqliteEditCheckpointStore
from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry


def _changes() -> list[dict[str, str]]:
    return [
        {"path": "one.py", "old_text": "old one", "new_text": "new one"},
        {"path": "two.py", "old_text": "old two", "new_text": "new two"},
    ]


def _apply_arguments(preview: dict, changes: list[dict[str, str]]) -> dict:
    hashes = {item["path"]: item["original_sha256"] for item in preview["changes"]}
    return {
        "changes": [dict(change, expected_sha256=hashes[change["path"]]) for change in changes],
        "expected_change_set_sha256": preview["change_set_sha256"],
    }


def test_multi_file_change_set_previews_and_applies_with_checkpoints(tmp_path) -> None:
    (tmp_path / "one.py").write_text("old one\n", encoding="utf-8")
    (tmp_path / "two.py").write_text("old two\n", encoding="utf-8")
    store = SqliteEditCheckpointStore(tmp_path / "data" / "checkpoints.db")
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        checkpoint_store=store,
    )
    changes = _changes()

    preview = json.loads(registry.execute("preview_text_change_set", {"changes": changes}))
    result = json.loads(
        registry.execute("apply_text_change_set", _apply_arguments(preview, changes))
    )

    assert preview["diff"].count("--- a/") == 2
    assert result["files_changed"] == 2
    assert result["checkpoint_ids"] == [1, 2]
    assert (tmp_path / "one.py").read_text(encoding="utf-8") == "new one\n"
    assert (tmp_path / "two.py").read_text(encoding="utf-8") == "new two\n"


def test_multi_file_change_set_rejects_stale_file_before_any_write(tmp_path) -> None:
    (tmp_path / "one.py").write_text("old one\n", encoding="utf-8")
    (tmp_path / "two.py").write_text("old two\n", encoding="utf-8")
    registry = build_phase3_registry(tmp_path, PermissionManager(lambda request: True))
    changes = _changes()
    preview = json.loads(registry.execute("preview_text_change_set", {"changes": changes}))
    (tmp_path / "two.py").write_text("newer two\nold two\n", encoding="utf-8")

    with pytest.raises(ToolError, match="differs from the reviewed preview"):
        registry.execute("apply_text_change_set", _apply_arguments(preview, changes))

    assert (tmp_path / "one.py").read_text(encoding="utf-8") == "old one\n"
    assert (tmp_path / "two.py").read_text(encoding="utf-8") == "newer two\nold two\n"


def test_multi_file_change_set_requires_permission_and_unique_paths(tmp_path) -> None:
    (tmp_path / "one.py").write_text("old one\n", encoding="utf-8")
    denied = build_phase3_registry(tmp_path)
    duplicate = [
        {"path": "one.py", "old_text": "old", "new_text": "new"},
        {"path": "one.py", "old_text": "one", "new_text": "two"},
    ]
    with pytest.raises(ToolError, match="duplicates"):
        denied.execute("preview_text_change_set", {"changes": duplicate})

    changes = [{"path": "one.py", "old_text": "old one", "new_text": "new one"}]
    preview = json.loads(denied.execute("preview_text_change_set", {"changes": changes}))
    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute("apply_text_change_set", _apply_arguments(preview, changes))
    assert (tmp_path / "one.py").read_text(encoding="utf-8") == "old one\n"


def test_multi_file_write_failure_restores_already_written_files(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "one.py").write_text("old one\n", encoding="utf-8")
    (tmp_path / "two.py").write_text("old two\n", encoding="utf-8")
    store = SqliteEditCheckpointStore(tmp_path / "data" / "checkpoints.db")
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        checkpoint_store=store,
    )
    changes = _changes()
    preview = json.loads(registry.execute("preview_text_change_set", {"changes": changes}))
    real_replace = os.replace
    target_writes = 0

    def failing_replace(source, destination) -> None:
        nonlocal target_writes
        if destination in {tmp_path / "one.py", tmp_path / "two.py"}:
            target_writes += 1
            if target_writes == 2:
                raise OSError("simulated second-file failure")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(ToolError, match="already-written files were restored"):
        registry.execute("apply_text_change_set", _apply_arguments(preview, changes))

    assert (tmp_path / "one.py").read_text(encoding="utf-8") == "old one\n"
    assert (tmp_path / "two.py").read_text(encoding="utf-8") == "old two\n"
    records = store.list_checkpoints()
    assert next(record for record in records if record.path == "one.py").restored_at is not None
