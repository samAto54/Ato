import json

import pytest

from ato.coding import SqliteEditCheckpointStore
from ato.exceptions import ToolError
from ato.security import AuditLogger, PermissionManager
from ato.tools import build_read_only_registry
from ato.ui.workspace import DesktopWorkspaceSearch, WorkspaceChangePreview


def test_desktop_workspace_search_reuses_bounded_audited_tool(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ato.py").write_text("Ato core\nother line\n", encoding="utf-8")
    audit_path = tmp_path / "data" / "audit.jsonl"
    service = DesktopWorkspaceSearch(
        build_read_only_registry(
            tmp_path,
            PermissionManager(lambda request: pytest.fail("LOW search must not prompt")),
            AuditLogger(audit_path),
        )
    )
    result = service.search("  ato   core  ")
    assert result.lines == ("src/ato.py:1\nAto core",)
    assert result.files_scanned >= 1
    event = json.loads(audit_path.read_text(encoding="utf-8"))
    assert event["tool"] == "search_files"
    assert event["permission"] == "LOW"
    assert event["decision"] == "ALLOW"


def test_desktop_file_listing_and_text_read_reuse_bounded_tools(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('Ato')\n", encoding="utf-8")
    service = DesktopWorkspaceSearch(build_read_only_registry(tmp_path))
    listing = service.list_files("src")
    assert listing.text == "src/main.py"
    assert listing.truncated is False
    viewed = service.read_text_file("src/main.py")
    assert viewed.text == "print('Ato')\n"
    assert viewed.label == "READ-ONLY TEXT - src/main.py"


def test_desktop_file_inspection_preserves_workspace_boundary(tmp_path) -> None:
    outside = tmp_path.parent / "outside-desktop-test.txt"
    outside.write_text("outside", encoding="utf-8")
    service = DesktopWorkspaceSearch(build_read_only_registry(tmp_path))
    with pytest.raises(ToolError, match="outside"):
        service.read_text_file("../outside-desktop-test.txt")


def test_desktop_file_listing_rejects_malformed_result() -> None:
    class Registry:
        def execute(self, *args, **kwargs):
            return json.dumps({"files": "not-a-list", "truncated": False})

    with pytest.raises(ToolError, match="invalid result"):
        DesktopWorkspaceSearch(Registry()).list_files(".")


def test_desktop_change_preview_returns_diff_without_writing(tmp_path) -> None:
    path = tmp_path / "module.py"
    path.write_text("value = 1\n", encoding="utf-8")
    service = DesktopWorkspaceSearch(build_read_only_registry(tmp_path))
    preview = service.preview_text_change("module.py", "value = 1", "value = 2")
    assert "-value = 1" in preview.diff
    assert "+value = 2" in preview.diff
    assert len(preview.original_sha256) == 64
    assert len(preview.updated_sha256) == 64
    assert path.read_text(encoding="utf-8") == "value = 1\n"


def test_desktop_change_preview_requires_one_unique_match(tmp_path) -> None:
    path = tmp_path / "module.py"
    path.write_text("same\nsame\n", encoding="utf-8")
    service = DesktopWorkspaceSearch(build_read_only_registry(tmp_path))
    with pytest.raises(ToolError, match="exactly once"):
        service.preview_text_change("module.py", "same", "changed")


def test_desktop_change_preview_rejects_malformed_hashes() -> None:
    class Registry:
        def execute(self, *args, **kwargs):
            return json.dumps(
                {
                    "path": "module.py",
                    "original_sha256": "bad",
                    "updated_sha256": "bad",
                    "diff": "diff",
                    "diff_truncated": False,
                }
            )

    with pytest.raises(ToolError, match="invalid result"):
        DesktopWorkspaceSearch(Registry()).preview_text_change("module.py", "old", "new")


def test_desktop_applies_only_exact_preview_with_high_confirmation_and_checkpoint(tmp_path) -> None:
    path = tmp_path / "module.py"
    path.write_text("old\n", encoding="utf-8")
    requests = []
    registry = build_read_only_registry(
        tmp_path,
        PermissionManager(lambda request: requests.append(request) or True),
        checkpoint_store=SqliteEditCheckpointStore(tmp_path / "data" / "checkpoints.db"),
    )
    service = DesktopWorkspaceSearch(registry)
    preview = service.preview_text_change("module.py", "old", "new")
    result = service.apply_text_change(preview)
    assert path.read_text(encoding="utf-8") == "new\n"
    assert requests[0].tool_name == "replace_text_in_file"
    assert requests[0].level.value == "HIGH"
    assert result.checkpoint_id == 1
    assert result.updated_sha256 == preview.updated_sha256


def test_desktop_apply_denial_and_stale_preview_never_overwrite(tmp_path) -> None:
    path = tmp_path / "module.py"
    path.write_text("old\n", encoding="utf-8")
    denied_service = DesktopWorkspaceSearch(build_read_only_registry(tmp_path))
    denied_preview = denied_service.preview_text_change("module.py", "old", "new")
    with pytest.raises(ToolError, match="Permission denied"):
        denied_service.apply_text_change(denied_preview)
    assert path.read_text(encoding="utf-8") == "old\n"

    allowed = DesktopWorkspaceSearch(
        build_read_only_registry(tmp_path, PermissionManager(lambda request: True))
    )
    stale_preview = allowed.preview_text_change("module.py", "old", "new")
    path.write_text("newer work\n", encoding="utf-8")
    with pytest.raises(ToolError, match="changed after preview"):
        allowed.apply_text_change(stale_preview)
    assert path.read_text(encoding="utf-8") == "newer work\n"


def test_desktop_never_applies_truncated_preview() -> None:
    class Registry:
        def execute(self, *args, **kwargs):
            pytest.fail("truncated preview must not execute")

    preview = WorkspaceChangePreview("module.py", "diff", "a" * 64, "b" * 64, True, "old", "new")
    with pytest.raises(ToolError, match="truncated"):
        DesktopWorkspaceSearch(Registry()).apply_text_change(preview)


@pytest.mark.parametrize("query", ["", "   ", "x" * 1_001])
def test_desktop_workspace_search_validates_query_before_execution(query) -> None:
    class Registry:
        def execute(self, *args, **kwargs):
            pytest.fail("invalid search must not execute")

    with pytest.raises(ToolError, match="search"):
        DesktopWorkspaceSearch(Registry()).search(query)


def test_desktop_workspace_search_rejects_malformed_tool_result() -> None:
    class Registry:
        def execute(self, *args, **kwargs):
            return '{"matches": "not-a-list", "files_scanned": 1, "truncated": false}'

    with pytest.raises(ToolError, match="invalid result"):
        DesktopWorkspaceSearch(Registry()).search("needle")


@pytest.mark.parametrize(
    ("action", "tool_name", "arguments"),
    [
        ("status", "git_status", {}),
        ("diff", "git_diff", {"staged": False}),
        ("staged", "git_diff", {"staged": True}),
        ("log", "git_log", {"max_count": 20}),
        ("branches", "git_branches", {}),
    ],
)
def test_desktop_git_inspection_uses_only_fixed_read_only_actions(
    action, tool_name, arguments
) -> None:
    class Registry:
        def __init__(self) -> None:
            self.calls = []

        def execute(self, name, supplied, user_request=None):
            self.calls.append((name, supplied, user_request))
            if name == "git_status":
                return "## main"
            return json.dumps({"exit_code": 0, "output": "bounded output", "truncated": False})

    registry = Registry()
    result = DesktopWorkspaceSearch(registry).inspect_git(action)
    assert registry.calls[0][:2] == (tool_name, arguments)
    assert result.text in {"## main", "bounded output"}


def test_desktop_git_inspection_rejects_unknown_action_without_execution() -> None:
    class Registry:
        def execute(self, *args, **kwargs):
            pytest.fail("unknown action must not execute")

    with pytest.raises(ToolError, match="Unknown"):
        DesktopWorkspaceSearch(Registry()).inspect_git("commit")


def test_desktop_git_inspection_rejects_nonzero_command_result() -> None:
    class Registry:
        def execute(self, *args, **kwargs):
            return json.dumps({"exit_code": 1, "output": "failure", "truncated": False})

    with pytest.raises(ToolError, match="failed safely"):
        DesktopWorkspaceSearch(Registry()).inspect_git("log")


def test_desktop_syntax_check_formats_valid_and_invalid_results() -> None:
    class Registry:
        def __init__(self) -> None:
            self.valid = True

        def execute(self, name, arguments, user_request=None):
            assert name == "python_syntax_check"
            assert arguments == {"path": "src/ato.py"}
            if self.valid:
                return json.dumps({"valid": True})
            return json.dumps({"valid": False, "line": 4, "offset": 2, "message": "bad syntax"})

    registry = Registry()
    service = DesktopWorkspaceSearch(registry)
    assert "parsed successfully" in service.check_syntax("src/ato.py").text
    registry.valid = False
    assert service.check_syntax("src/ato.py").text == "src/ato.py:4:2\nbad syntax"


@pytest.mark.parametrize(
    ("action", "tool_name", "label"),
    [("lint", "lint_project", "RUFF LINT"), ("tests", "test_project", "PYTEST")],
)
def test_desktop_code_checks_use_fixed_registry_actions(action, tool_name, label) -> None:
    class Registry:
        def execute(self, name, arguments, user_request=None):
            assert name == tool_name
            assert arguments == {}
            return json.dumps({"exit_code": 0, "output": "passed", "truncated": False})

    result = DesktopWorkspaceSearch(Registry()).run_code_check(action)
    assert result.label == f"{label} - PASSED"
    assert result.text == "passed"


def test_desktop_code_check_reports_failure_without_generic_tool_error() -> None:
    class Registry:
        def execute(self, *args, **kwargs):
            return json.dumps({"exit_code": 1, "output": "test failed", "truncated": False})

    result = DesktopWorkspaceSearch(Registry()).run_code_check("tests")
    assert result.label == "PYTEST - FAILED (EXIT 1)"
    assert result.text == "test failed"


def test_desktop_code_check_rejects_unknown_action() -> None:
    class Registry:
        def execute(self, *args, **kwargs):
            pytest.fail("unknown action must not execute")

    with pytest.raises(ToolError, match="Unknown"):
        DesktopWorkspaceSearch(Registry()).run_code_check("format")
