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

    def list_files(self, path: str = ".") -> WorkspaceInspectionResult:
        cleaned = path.strip() or "."
        if len(cleaned) > 500:
            raise ToolError("Workspace listing path cannot exceed 500 characters.")
        raw = self.registry.execute(
            "list_files",
            {"path": cleaned, "recursive": True},
            user_request="List files from the desktop workspace",
        )
        try:
            payload = json.loads(raw)
            files = payload["files"]
            truncated = bool(payload["truncated"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ToolError("Workspace listing returned an invalid result.") from exc
        if not isinstance(files, list) or len(files) > 200 or not all(
            isinstance(item, str) for item in files
        ):
            raise ToolError("Workspace listing returned an invalid result.")
        output = "\n".join(files) or "No files found."
        display_truncated = truncated or len(output) > MAX_GUI_INSPECTION_CHARS
        return WorkspaceInspectionResult(
            f"WORKSPACE FILES - {cleaned}",
            output[:MAX_GUI_INSPECTION_CHARS],
            display_truncated,
        )

    def read_text_file(self, path: str) -> WorkspaceInspectionResult:
        cleaned = path.strip()
        if not cleaned or len(cleaned) > 500:
            raise ToolError("Text reading requires a bounded relative file path.")
        output = self.registry.execute(
            "read_text_file",
            {"path": cleaned},
            user_request="Read one text file from the desktop workspace",
        )
        display_truncated = len(output) > MAX_GUI_INSPECTION_CHARS
        return WorkspaceInspectionResult(
            f"READ-ONLY TEXT - {cleaned}",
            output[:MAX_GUI_INSPECTION_CHARS] or "File is empty.",
            display_truncated,
        )

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

    def check_syntax(self, path: str) -> WorkspaceInspectionResult:
        cleaned = path.strip()
        if not cleaned or len(cleaned) > 500:
            raise ToolError("Python syntax checking requires a bounded relative path.")
        raw = self.registry.execute(
            "python_syntax_check",
            {"path": cleaned},
            user_request="Check one Python file from the desktop",
        )
        try:
            payload = json.loads(raw)
            valid = payload["valid"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ToolError("Python syntax check returned an invalid result.") from exc
        if not isinstance(valid, bool):
            raise ToolError("Python syntax check returned an invalid result.")
        if valid:
            output = f"{cleaned} parsed successfully without execution."
        else:
            try:
                line = int(payload["line"])
                offset = int(payload["offset"])
                message = " ".join(str(payload["message"]).split())[:500]
            except (KeyError, TypeError, ValueError) as exc:
                raise ToolError("Python syntax check returned an invalid result.") from exc
            output = f"{cleaned}:{line}:{offset}\n{message}"
        return WorkspaceInspectionResult("PYTHON SYNTAX CHECK", output, False)

    def run_code_check(self, action: str) -> WorkspaceInspectionResult:
        normalized = action.strip().casefold()
        definitions = {
            "lint": ("lint_project", "RUFF LINT"),
            "tests": ("test_project", "PYTEST"),
        }
        definition = definitions.get(normalized)
        if definition is None:
            raise ToolError("Unknown code verification action.")
        tool_name, label = definition
        raw = self.registry.execute(
            tool_name,
            {},
            user_request=f"Run fixed {label.casefold()} verification from the desktop",
        )
        try:
            payload = json.loads(raw)
            exit_code = int(payload["exit_code"])
            output = str(payload["output"])
            truncated = bool(payload["truncated"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ToolError("Code verification returned an invalid result.") from exc
        display_truncated = truncated or len(output) > MAX_GUI_INSPECTION_CHARS
        status = "PASSED" if exit_code == 0 else f"FAILED (EXIT {exit_code})"
        return WorkspaceInspectionResult(
            f"{label} - {status}",
            output[:MAX_GUI_INSPECTION_CHARS] or "No output.",
            display_truncated,
        )
