"""Read-only tools constrained to an authorized workspace root."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ato.exceptions import ToolError
from ato.security.audit import AuditLogger
from ato.security.permissions import PermissionLevel, PermissionManager
from ato.tools.registry import ToolRegistry, ToolSpec

IGNORED_DIRECTORIES = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
MAX_LIST_RESULTS = 200
MAX_TEXT_BYTES = 100_000


class WorkspaceBoundary:
    """Resolve relative paths without allowing workspace escape."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def resolve(self, relative_path: str) -> Path:
        requested = Path(relative_path)
        if requested.is_absolute():
            raise ToolError("Tool paths must be relative to the authorized workspace.")
        candidate = (self.root / requested).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ToolError("Requested path is outside the authorized workspace.") from exc
        return candidate


def build_read_only_registry(
    workspace_root: Path,
    permission_manager: PermissionManager | None = None,
    audit_logger: AuditLogger | None = None,
) -> ToolRegistry:
    """Create the Phase 3 registry containing only read-only tools."""
    boundary = WorkspaceBoundary(workspace_root)
    registry = ToolRegistry(permission_manager, audit_logger)

    def list_files(arguments: Mapping[str, Any]) -> str:
        directory = boundary.resolve(str(arguments.get("path", ".")))
        recursive = bool(arguments.get("recursive", True))
        if not directory.is_dir():
            raise ToolError("The requested list path is not a directory.")
        candidates = directory.rglob("*") if recursive else directory.iterdir()
        files = [
            path.relative_to(boundary.root).as_posix()
            for path in candidates
            if path.is_file()
            and not any(
                part in IGNORED_DIRECTORIES
                for part in path.relative_to(boundary.root).parts
            )
        ]
        files.sort()
        truncated = len(files) > MAX_LIST_RESULTS
        return json.dumps({"files": files[:MAX_LIST_RESULTS], "truncated": truncated})

    def read_text_file(arguments: Mapping[str, Any]) -> str:
        path = boundary.resolve(str(arguments["path"]))
        if not path.is_file():
            raise ToolError("The requested path is not a file.")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ToolError("The requested file could not be inspected.") from exc
        if size > MAX_TEXT_BYTES:
            raise ToolError(f"File exceeds the {MAX_TEXT_BYTES}-byte read limit.")
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ToolError("The requested file is not readable UTF-8 text.") from exc

    def git_status(arguments: Mapping[str, Any]) -> str:
        del arguments
        try:
            result = subprocess.run(
                [
                    "git",
                    "-c",
                    f"safe.directory={boundary.root.as_posix()}",
                    "status",
                    "--short",
                    "--branch",
                ],
                cwd=boundary.root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ToolError("Git status could not be executed.") from exc
        if result.returncode != 0:
            raise ToolError("Git status failed for the authorized workspace.")
        return result.stdout.strip() or "Working tree is clean."

    registry.register(
        ToolSpec(
            name="list_files",
            description="List files inside the authorized Ato workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative directory path."},
                    "recursive": {"type": "boolean", "description": "List recursively."},
                },
                "additionalProperties": False,
            },
            handler=list_files,
            permission=PermissionLevel.LOW,
        )
    )
    registry.register(
        ToolSpec(
            name="read_text_file",
            description="Read a small UTF-8 text file inside the authorized Ato workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path."}
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=read_text_file,
            permission=PermissionLevel.LOW,
        )
    )
    registry.register(
        ToolSpec(
            name="git_status",
            description="Show the read-only Git status of the authorized Ato workspace.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=git_status,
            permission=PermissionLevel.LOW,
        )
    )
    return registry
