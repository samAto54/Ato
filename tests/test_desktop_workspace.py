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
