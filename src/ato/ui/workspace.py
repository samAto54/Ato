"""Narrow read-only workspace search adapter for the desktop UI."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ato.exceptions import ToolError
from ato.tools import ToolRegistry

MAX_GUI_SEARCH_RESULTS = 100
MAX_GUI_INSPECTION_CHARS = 20_000


@dataclass(frozen=True, slots=True)
class WorkspaceSearchResult:
    lines: tuple[str, ...]
    files_scanned: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class WorkspaceInspectionResult:
    label: str
    text: str
    truncated: bool


@dataclass(slots=True)
class DesktopWorkspaceSearch:
    """Expose only literal read-only search from a private tool registry."""

    registry: ToolRegistry

    def search(self, query: str) -> WorkspaceSearchResult:
        cleaned = " ".join(query.split())
        if not cleaned:
            raise ToolError("Workspace search cannot be empty.")
        if len(cleaned) > 1_000:
            raise ToolError("Workspace search cannot exceed 1,000 characters.")
        raw = self.registry.execute(
            "search_files",
            {"query": cleaned, "path": ".", "case_sensitive": False},
            user_request="Search the workspace from the desktop",
        )
        try:
            payload = json.loads(raw)
            matches = payload["matches"]
            files_scanned = int(payload["files_scanned"])
            truncated = bool(payload["truncated"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ToolError("Workspace search returned an invalid result.") from exc
        if not isinstance(matches, list) or len(matches) > MAX_GUI_SEARCH_RESULTS:
            raise ToolError("Workspace search returned an invalid result.")
        lines = []
        for match in matches:
            if not isinstance(match, dict):
                raise ToolError("Workspace search returned an invalid result.")
            try:
                path = str(match["path"])
                line_number = int(match["line"])
                text = " ".join(str(match["text"]).split())
            except (KeyError, TypeError, ValueError) as exc:
                raise ToolError("Workspace search returned an invalid result.") from exc
            lines.append(f"{path}:{line_number}\n{text[:500]}")
        return WorkspaceSearchResult(tuple(lines), files_scanned, truncated)

    def inspect_git(self, action: str) -> WorkspaceInspectionResult:
        normalized = action.strip().casefold()
        definitions = {
            "status": ("git_status", {}, "GIT STATUS", False),
            "diff": ("git_diff", {"staged": False}, "UNSTAGED GIT DIFF", True),
            "staged": ("git_diff", {"staged": True}, "STAGED GIT DIFF", True),
            "log": ("git_log", {"max_count": 20}, "RECENT GIT LOG", True),
            "branches": ("git_branches", {}, "LOCAL GIT BRANCHES", True),
        }
        definition = definitions.get(normalized)
        if definition is None:
            raise ToolError("Unknown Git inspection action.")
        tool_name, arguments, label, structured = definition
        raw = self.registry.execute(
            tool_name,
            arguments,
            user_request=f"Inspect {label.casefold()} from the desktop",
        )
        truncated = False
        output = raw
        if structured:
            try:
                payload = json.loads(raw)
                exit_code = int(payload["exit_code"])
                output = str(payload["output"])
                truncated = bool(payload["truncated"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ToolError("Git inspection returned an invalid result.") from exc
            if exit_code != 0:
                raise ToolError(f"{label.title()} failed safely.")
        display_truncated = truncated or len(output) > MAX_GUI_INSPECTION_CHARS
        return WorkspaceInspectionResult(
            label,
            output[:MAX_GUI_INSPECTION_CHARS] or "No output.",
            display_truncated,
        )
