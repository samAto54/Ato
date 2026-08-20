import json
from pathlib import Path

import pytest

from ato.exceptions import ToolError
from ato.tools import ToolRegistry, ToolSpec, build_read_only_registry


def test_registry_rejects_unknown_and_invalid_arguments() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="sample",
            description="Sample tool.",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            handler=lambda arguments: str(arguments["value"]),
        )
    )

    with pytest.raises(ToolError, match="not registered"):
        registry.execute("missing", {})
    with pytest.raises(ToolError, match="Missing arguments"):
        registry.execute("sample", {})
    with pytest.raises(ToolError, match="Unexpected arguments"):
        registry.execute("sample", {"value": "yes", "extra": "no"})


def test_file_tools_are_limited_to_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("Ato notes", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    registry = build_read_only_registry(workspace)

    listing = json.loads(registry.execute("list_files", {"path": ".", "recursive": True}))
    assert listing["files"] == ["notes.txt"]
    assert registry.execute("read_text_file", {"path": "notes.txt"}) == "Ato notes"

    with pytest.raises(ToolError, match="outside"):
        registry.execute("read_text_file", {"path": "../outside.txt"})
    with pytest.raises(ToolError, match="relative"):
        registry.execute("read_text_file", {"path": str(outside.resolve())})


def test_list_files_ignores_internal_directories(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret-ish metadata", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')", encoding="utf-8")
    registry = build_read_only_registry(tmp_path)

    result = json.loads(registry.execute("list_files", {}))

    assert result["files"] == ["src/main.py"]
