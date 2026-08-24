"""Narrow read-only workspace search adapter for the desktop UI."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ato.exceptions import ToolError
from ato.tools import ToolRegistry

MAX_GUI_SEARCH_RESULTS = 100


@dataclass(frozen=True, slots=True)
class WorkspaceSearchResult:
    lines: tuple[str, ...]
    files_scanned: int
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
