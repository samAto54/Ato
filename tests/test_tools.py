import json
import subprocess
from pathlib import Path

import pytest

from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import ToolRegistry, ToolSpec, build_phase3_registry, build_read_only_registry


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


def test_registry_validates_array_item_types() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="paths",
            description="Paths.",
            parameters={
                "type": "object",
                "properties": {
                    "values": {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                },
                "required": ["values"],
            },
            handler=lambda arguments: "ok",
        )
    )

    with pytest.raises(ToolError, match=r"values\[1\].*string"):
        registry.execute("paths", {"values": ["valid", 2]})


def test_registry_enforces_schema_constraints_before_execution() -> None:
    calls = 0

    def handler(arguments: object) -> str:
        nonlocal calls
        calls += 1
        return "ok"

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="bounded",
            description="Bounded input.",
            parameters={
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["safe"]},
                    "name": {"type": "string", "minLength": 2, "maxLength": 4},
                    "count": {"type": "integer", "minimum": 1, "maximum": 3},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 2,
                        "uniqueItems": True,
                    },
                },
                "required": ["mode", "name", "count", "tags"],
                "additionalProperties": False,
            },
            handler=handler,
        )
    )

    invalid_arguments = [
        {"mode": "unsafe", "name": "Ato", "count": 1, "tags": ["a"]},
        {"mode": "safe", "name": "A", "count": 1, "tags": ["a"]},
        {"mode": "safe", "name": "Ato", "count": True, "tags": ["a"]},
        {"mode": "safe", "name": "Ato", "count": 4, "tags": ["a"]},
        {"mode": "safe", "name": "Ato", "count": 1, "tags": []},
        {"mode": "safe", "name": "Ato", "count": 1, "tags": ["a", "a"]},
    ]
    for arguments in invalid_arguments:
        with pytest.raises(ToolError):
            registry.execute("bounded", arguments)

    assert calls == 0
    assert registry.execute(
        "bounded", {"mode": "safe", "name": "Ato", "count": 2, "tags": ["a", "b"]}
    ) == "ok"
    assert calls == 1


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


def test_verify_code_change_reports_all_steps_without_mutating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    valid = tmp_path / "valid.py"
    invalid = tmp_path / "invalid.py"
    valid.write_text("value = 1\n", encoding="utf-8")
    invalid.write_text("def broken(:\n", encoding="utf-8")
    original = {path: path.read_bytes() for path in (valid, invalid)}
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"  # type: ignore[index]
        if "ruff" in command:
            return subprocess.CompletedProcess(command, 1, "lint failure " + "x" * 5_000, "")
        return subprocess.CompletedProcess(command, 0, "tests passed", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    registry = build_phase3_registry(tmp_path, PermissionManager(lambda request: True))

    result = json.loads(registry.execute("verify_code_change", {}))

    assert result["overall"] == "fail"
    assert result["syntax"]["status"] == "fail"
    assert result["syntax"]["errors"][0]["path"] == "invalid.py"
    assert result["lint"]["status"] == "fail"
    assert len(result["lint"]["output"]) == 3_500
    assert result["lint"]["truncated"] is True
    assert result["tests"]["status"] == "pass"
    assert result["automatic_fixes_applied"] is False
    assert calls[0][-5:] == ["-m", "ruff", "check", "--no-cache", "."]
    assert calls[1][-4:] == ["-m", "pytest", "-p", "no:cacheprovider"]
    assert {path: path.read_bytes() for path in (valid, invalid)} == original


def test_verify_code_change_requires_permission_before_any_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("verification command should not execute"),
    )
    registry = build_phase3_registry(tmp_path)

    with pytest.raises(ToolError, match="Permission denied"):
        registry.execute("verify_code_change", {})


def test_verify_code_change_preserves_later_results_after_command_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "valid.py").write_text("value = 1\n", encoding="utf-8")
    calls = 0

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        del kwargs
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired(command, 60)
        return subprocess.CompletedProcess(command, 0, "tests passed", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    registry = build_phase3_registry(tmp_path, PermissionManager(lambda request: True))

    result = json.loads(registry.execute("verify_code_change", {}))

    assert result["overall"] == "incomplete"
    assert result["syntax"]["status"] == "pass"
    assert result["lint"]["status"] == "error"
    assert "timeout" in result["lint"]["error"]
    assert result["tests"]["status"] == "pass"
    assert calls == 2


def test_controlled_text_writes_require_permission_and_never_overwrite(tmp_path: Path) -> None:
    denied = build_phase3_registry(tmp_path)
    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute("create_text_file", {"path": "new.txt", "content": "hello"})
    assert not (tmp_path / "new.txt").exists()

    allowed = build_phase3_registry(tmp_path, PermissionManager(lambda request: True))
    result = json.loads(
        allowed.execute("create_text_file", {"path": "notes/new.txt", "content": "hello"})
    )
    assert result == {"path": "notes/new.txt", "bytes": 5}
    assert (tmp_path / "notes" / "new.txt").read_text(encoding="utf-8") == "hello"

    with pytest.raises(ToolError, match="will not overwrite"):
        allowed.execute("create_text_file", {"path": "notes/new.txt", "content": "changed"})
    assert (tmp_path / "notes" / "new.txt").read_text(encoding="utf-8") == "hello"


def test_controlled_replace_requires_one_exact_match(tmp_path: Path) -> None:
    path = tmp_path / "module.py"
    path.write_text("old\nkeep\n", encoding="utf-8")
    registry = build_phase3_registry(tmp_path, PermissionManager(lambda request: True))

    preview = json.loads(
        registry.execute(
            "preview_text_change",
            {"path": "module.py", "old_text": "old", "new_text": "new"},
        )
    )
    result = json.loads(
        registry.execute(
        "replace_text_in_file",
            {
                "path": "module.py",
                "old_text": "old",
                "new_text": "new",
                "expected_sha256": preview["original_sha256"],
            },
        )
    )
    assert preview["diff"].startswith("--- a/module.py\n+++ b/module.py")
    assert result["original_sha256"] == preview["original_sha256"]
    assert result["updated_sha256"] == preview["updated_sha256"]
    assert path.read_text(encoding="utf-8") == "new\nkeep\n"

    with pytest.raises(ToolError, match="found 0"):
        registry.execute(
            "preview_text_change",
            {"path": "module.py", "old_text": "missing", "new_text": "x"},
        )


def test_previewed_replace_rejects_stale_file_and_requires_high_permission(tmp_path: Path) -> None:
    path = tmp_path / "module.py"
    path.write_text("old\n", encoding="utf-8")
    denied = build_phase3_registry(tmp_path)
    preview = json.loads(
        denied.execute(
            "preview_text_change", {"path": "module.py", "old_text": "old", "new_text": "new"}
        )
    )

    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute(
            "replace_text_in_file",
            {
                "path": "module.py",
                "old_text": "old",
                "new_text": "new",
                "expected_sha256": preview["original_sha256"],
            },
        )
    path.write_text("changed elsewhere\nold\n", encoding="utf-8")
    allowed = build_phase3_registry(tmp_path, PermissionManager(lambda request: True))
    with pytest.raises(ToolError, match="changed after preview"):
        allowed.execute(
            "replace_text_in_file",
            {
                "path": "module.py",
                "old_text": "old",
                "new_text": "new",
                "expected_sha256": preview["original_sha256"],
            },
        )
    assert path.read_text(encoding="utf-8") == "changed elsewhere\nold\n"


def test_text_change_preview_is_bounded_and_never_writes(tmp_path: Path) -> None:
    path = tmp_path / "large.txt"
    original = "a" * 20_000
    path.write_text(original, encoding="utf-8")
    registry = build_phase3_registry(tmp_path)

    preview = json.loads(
        registry.execute(
            "preview_text_change",
            {"path": "large.txt", "old_text": original, "new_text": "b" * 20_000},
        )
    )

    assert preview["diff_truncated"] is True
    assert len(preview["diff"]) == 10_000
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("path", [".env", "data/memory.json", ".github/workflow.yml", "key.pem"])
def test_controlled_writes_reject_protected_targets(tmp_path: Path, path: str) -> None:
    registry = build_phase3_registry(tmp_path, PermissionManager(lambda request: True))

    with pytest.raises(ToolError, match="cannot|protected|Credential"):
        registry.execute("create_text_file", {"path": path, "content": "unsafe"})
    with pytest.raises(ToolError, match="cannot|protected|Credential"):
        registry.execute(
            "preview_text_change",
            {"path": path, "old_text": "unsafe", "new_text": "safe"},
        )


def test_trash_text_file_is_critical_confirmed_and_recoverable(tmp_path: Path) -> None:
    source = tmp_path / "obsolete.txt"
    source.write_text("recover me", encoding="utf-8")
    seen = []
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: not seen.append(request)),
    )

    result = json.loads(registry.execute("trash_text_file", {"path": "obsolete.txt"}))

    assert seen[0].level.value == "CRITICAL"
    assert result["original_path"] == "obsolete.txt"
    recovery = tmp_path / Path(result["recovery_path"])
    assert not source.exists()
    assert recovery.read_text(encoding="utf-8") == "recover me"


def test_trash_text_file_denial_and_target_guards(tmp_path: Path) -> None:
    source = tmp_path / "keep.txt"
    source.write_text("keep me", encoding="utf-8")
    denied = build_phase3_registry(tmp_path, PermissionManager(lambda request: False))

    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute("trash_text_file", {"path": "keep.txt"})
    assert source.exists()

    allowed = build_phase3_registry(tmp_path, PermissionManager(lambda request: True))
    (tmp_path / "folder").mkdir()
    with pytest.raises(ToolError, match="regular file"):
        allowed.execute("trash_text_file", {"path": "folder"})
    with pytest.raises(ToolError, match="protected"):
        allowed.execute("trash_text_file", {"path": "data/memory.json"})


def test_git_commit_files_commits_only_named_paths_and_preserves_staging(tmp_path: Path) -> None:
    def git(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments], cwd=tmp_path, capture_output=True, text=True, check=True
        )

    git("init")
    git("config", "user.email", "ato-tests@example.invalid")
    git("config", "user.name", "Ato Tests")
    tracked = tmp_path / "tracked.txt"
    unrelated = tmp_path / "unrelated.txt"
    tracked.write_text("initial", encoding="utf-8")
    unrelated.write_text("initial", encoding="utf-8")
    git("add", "tracked.txt", "unrelated.txt")
    git("commit", "-m", "initial")
    tracked.write_text("selected change", encoding="utf-8")
    unrelated.write_text("staged change", encoding="utf-8")
    git("add", "unrelated.txt")
    seen = []
    registry = build_phase3_registry(
        tmp_path, PermissionManager(lambda request: not seen.append(request))
    )
    branches = json.loads(registry.execute("git_branches", {}))
    assert "* " in branches["output"]

    result = json.loads(
        registry.execute(
            "git_commit_files",
            {"paths": ["tracked.txt"], "message": "Commit selected path"},
        )
    )

    assert seen[0].level.value == "HIGH"
    assert result["committed_paths"] == ["tracked.txt"]
    assert "tracked.txt" in git("show", "--pretty=", "--name-only", "HEAD").stdout
    assert git("diff", "--cached", "--name-only").stdout.strip() == "unrelated.txt"


def test_git_commit_files_fails_closed_without_confirmation(tmp_path: Path) -> None:
    registry = build_phase3_registry(tmp_path)

    with pytest.raises(ToolError, match="Permission denied"):
        registry.execute(
            "git_commit_files",
            {"paths": ["file.txt"], "message": "No permission"},
        )


def test_system_info_is_read_only_and_omits_host_identity(tmp_path: Path) -> None:
    registry = build_phase3_registry(tmp_path)

    result = json.loads(registry.execute("system_info", {}))

    assert set(result) == {
        "os",
        "python",
        "cpu",
        "memory_bytes",
        "workspace_disk_bytes",
        "network",
    }
    assert result["workspace_disk_bytes"]["total"] > 0
    assert result["workspace_disk_bytes"]["free"] >= 0
    assert result["network"]["status"] == "not_probed"
    serialized = json.dumps(result).casefold()
    assert "hostname" not in serialized
    assert "username" not in serialized
