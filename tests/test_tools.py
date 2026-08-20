import json
import subprocess
from pathlib import Path

import pytest

from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
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


def test_search_and_syntax_tools_are_bounded_and_non_executing(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    marker = tmp_path / "executed.txt"
    (tmp_path / "src" / "main.py").write_text(
        f"# Ato Agent\nopen({str(marker)!r}, 'w').write('bad')\n", encoding="utf-8"
    )
    (tmp_path / "invalid.py").write_text("def broken(:\n", encoding="utf-8")
    registry = build_read_only_registry(tmp_path)

    search = json.loads(registry.execute("search_files", {"query": "ATO AGENT"}))
    assert search["matches"][0]["path"] == "src/main.py"
    assert json.loads(registry.execute("python_syntax_check", {"path": "src/main.py"})) == {
        "valid": True
    }
    assert (
        json.loads(registry.execute("python_syntax_check", {"path": "invalid.py"}))["valid"]
        is False
    )
    assert not marker.exists()


def test_fixed_analysis_commands_and_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert kwargs["cwd"] == tmp_path.resolve()
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    registry = build_read_only_registry(tmp_path, PermissionManager(lambda request: True))
    registry.execute("git_diff", {"staged": True})
    registry.execute("git_log", {"max_count": 5})
    registry.execute("lint_project", {})
    registry.execute("test_project", {})

    assert calls[0][-2:] == ["diff", "--cached"]
    assert calls[1][-5:] == ["log", "--oneline", "--decorate", "-n", "5"]
    assert calls[2][-5:] == ["-m", "ruff", "check", "--no-cache", "."]
    assert calls[3][-4:] == ["-m", "pytest", "-p", "no:cacheprovider"]

    denied = build_read_only_registry(tmp_path)
    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute("lint_project", {})
    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute("test_project", {})
