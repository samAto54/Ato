import json

import pytest

from ato.exceptions import ToolError
from ato.security import AuditLogger, PermissionManager
from ato.tools import build_read_only_registry
from ato.ui.workspace import DesktopWorkspaceSearch


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
