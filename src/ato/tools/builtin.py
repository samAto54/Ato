"""Bounded tools constrained to an authorized workspace root."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ato.exceptions import ToolError
from ato.security.audit import AuditLogger
from ato.security.permissions import PermissionLevel, PermissionManager
from ato.tools.registry import ToolRegistry, ToolSpec

IGNORED_DIRECTORIES = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
MAX_LIST_RESULTS = 200
MAX_TEXT_BYTES = 100_000
MAX_SEARCH_FILES = 500
MAX_SEARCH_RESULTS = 100
MAX_COMMAND_OUTPUT = 50_000
MAX_WRITE_BYTES = 100_000
MAX_COMMIT_PATHS = 20
MAX_COMMIT_MESSAGE_CHARS = 200
PROTECTED_WRITE_DIRECTORIES = {".git", ".github", ".venv", "__pycache__", "data"}
PROTECTED_WRITE_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}


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

    def contains(self, path: Path) -> bool:
        """Return whether a discovered path resolves inside the workspace."""
        try:
            path.resolve().relative_to(self.root)
        except (OSError, ValueError):
            return False
        return True

    def write_target(self, relative_path: str) -> Path:
        """Resolve a non-symlink write target and reject protected locations."""
        requested = Path(relative_path)
        if requested.is_absolute():
            raise ToolError("Tool paths must be relative to the authorized workspace.")
        unresolved = self.root / requested
        if unresolved.is_symlink():
            raise ToolError("Writing through symbolic links is not allowed.")
        target = unresolved.resolve()
        try:
            relative = target.relative_to(self.root)
        except ValueError as exc:
            raise ToolError("Requested path is outside the authorized workspace.") from exc
        lowered_parts = {part.casefold() for part in relative.parts}
        filename = relative.name.casefold()
        if lowered_parts & PROTECTED_WRITE_DIRECTORIES:
            raise ToolError("Writing to a protected workspace directory is not allowed.")
        if filename == ".env" or filename.startswith(".env."):
            raise ToolError("Environment files cannot be modified by tools.")
        if target.suffix.casefold() in PROTECTED_WRITE_SUFFIXES:
            raise ToolError("Credential and private-key files cannot be modified by tools.")
        return target


def build_phase3_registry(
    workspace_root: Path,
    permission_manager: PermissionManager | None = None,
    audit_logger: AuditLogger | None = None,
) -> ToolRegistry:
    """Create bounded Phase 3 tools with no arbitrary command access."""
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
            and boundary.contains(path)
            and not any(
                part in IGNORED_DIRECTORIES for part in path.relative_to(boundary.root).parts
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

    def search_files(arguments: Mapping[str, Any]) -> str:
        query = str(arguments["query"])
        if not query:
            raise ToolError("The search query cannot be empty.")
        directory = boundary.resolve(str(arguments.get("path", ".")))
        if not directory.is_dir():
            raise ToolError("The requested search path is not a directory.")
        case_sensitive = bool(arguments.get("case_sensitive", False))
        needle = query if case_sensitive else query.casefold()
        matches: list[dict[str, Any]] = []
        scanned = 0
        for path in directory.rglob("*"):
            relative = path.relative_to(boundary.root)
            if (
                not path.is_file()
                or not boundary.contains(path)
                or any(part in IGNORED_DIRECTORIES for part in relative.parts)
            ):
                continue
            if scanned >= MAX_SEARCH_FILES:
                break
            scanned += 1
            try:
                if path.stat().st_size > MAX_TEXT_BYTES:
                    continue
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for line_number, line in enumerate(lines, start=1):
                haystack = line if case_sensitive else line.casefold()
                if needle in haystack:
                    matches.append(
                        {"path": relative.as_posix(), "line": line_number, "text": line[:500]}
                    )
                    if len(matches) >= MAX_SEARCH_RESULTS:
                        return json.dumps(
                            {"matches": matches, "files_scanned": scanned, "truncated": True}
                        )
        return json.dumps(
            {"matches": matches, "files_scanned": scanned, "truncated": scanned >= MAX_SEARCH_FILES}
        )

    def python_syntax_check(arguments: Mapping[str, Any]) -> str:
        path = boundary.resolve(str(arguments["path"]))
        if path.suffix.lower() != ".py" or not path.is_file():
            raise ToolError("Syntax checking requires an existing .py file.")
        try:
            source = path.read_text(encoding="utf-8")
            ast.parse(source, filename=path.name)
        except UnicodeDecodeError as exc:
            raise ToolError("The Python file is not readable UTF-8 text.") from exc
        except OSError as exc:
            raise ToolError("The Python file could not be read.") from exc
        except SyntaxError as exc:
            return json.dumps(
                {"valid": False, "line": exc.lineno, "offset": exc.offset, "message": exc.msg}
            )
        return json.dumps({"valid": True})

    def run_fixed(command: list[str], timeout: int) -> str:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            result = subprocess.run(
                command,
                cwd=boundary.root,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"The fixed command exceeded its {timeout}-second timeout.") from exc
        except OSError as exc:
            raise ToolError("The fixed command could not be started.") from exc
        output = (result.stdout + result.stderr).strip()
        truncated = len(output) > MAX_COMMAND_OUTPUT
        return json.dumps(
            {
                "exit_code": result.returncode,
                "output": output[:MAX_COMMAND_OUTPUT],
                "truncated": truncated,
            }
        )

    def git_diff(arguments: Mapping[str, Any]) -> str:
        command = ["git", "-c", f"safe.directory={boundary.root.as_posix()}", "diff"]
        if bool(arguments.get("staged", False)):
            command.append("--cached")
        if "path" in arguments:
            path = boundary.resolve(str(arguments["path"]))
            command.extend(["--", path.relative_to(boundary.root).as_posix()])
        return run_fixed(command, 15)

    def git_log(arguments: Mapping[str, Any]) -> str:
        count = int(arguments.get("max_count", 10))
        if not 1 <= count <= 50:
            raise ToolError("max_count must be between 1 and 50.")
        return run_fixed(
            [
                "git",
                "-c",
                f"safe.directory={boundary.root.as_posix()}",
                "log",
                "--oneline",
                "--decorate",
                "-n",
                str(count),
            ],
            15,
        )

    def git_branches(arguments: Mapping[str, Any]) -> str:
        del arguments
        return run_fixed(
            [
                "git",
                "-c",
                f"safe.directory={boundary.root.as_posix()}",
                "branch",
                "--list",
                "--no-color",
            ],
            15,
        )

    def git_commit_files(arguments: Mapping[str, Any]) -> str:
        raw_paths = arguments["paths"]
        message = str(arguments["message"]).strip()
        if not 1 <= len(raw_paths) <= MAX_COMMIT_PATHS:
            raise ToolError(f"paths must contain between 1 and {MAX_COMMIT_PATHS} entries.")
        if not message or len(message) > MAX_COMMIT_MESSAGE_CHARS or "\n" in message:
            raise ToolError(
                "Commit message must be one line and at most "
                f"{MAX_COMMIT_MESSAGE_CHARS} characters."
            )
        relative_paths: list[str] = []
        for raw_path in raw_paths:
            target = boundary.write_target(str(raw_path))
            relative = target.relative_to(boundary.root).as_posix()
            if relative in relative_paths:
                raise ToolError("Commit paths cannot contain duplicates.")
            relative_paths.append(relative)
        command = [
            "git",
            "-c",
            f"safe.directory={boundary.root.as_posix()}",
            "commit",
            "--only",
            "-m",
            message,
            "--",
            *relative_paths,
        ]
        try:
            result = subprocess.run(
                command,
                cwd=boundary.root,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ToolError("The fixed Git commit command could not be executed.") from exc
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            raise ToolError(f"Git commit failed: {output[:1_000] or 'unknown Git error'}")
        return json.dumps(
            {
                "committed_paths": relative_paths,
                "output": output[:MAX_COMMAND_OUTPUT],
                "truncated": len(output) > MAX_COMMAND_OUTPUT,
            }
        )

    def lint_project(arguments: Mapping[str, Any]) -> str:
        del arguments
        return run_fixed([sys.executable, "-m", "ruff", "check", "--no-cache", "."], 60)

    def test_project(arguments: Mapping[str, Any]) -> str:
        del arguments
        return run_fixed([sys.executable, "-m", "pytest", "-p", "no:cacheprovider"], 120)

    def create_text_file(arguments: Mapping[str, Any]) -> str:
        path = boundary.write_target(str(arguments["path"]))
        content = str(arguments["content"])
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            raise ToolError(f"Content exceeds the {MAX_WRITE_BYTES}-byte write limit.")
        if path.exists():
            raise ToolError("The requested file already exists; create will not overwrite it.")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_create_text(path, content)
        except FileExistsError as exc:
            raise ToolError("The requested file appeared before creation completed.") from exc
        except OSError as exc:
            raise ToolError("The text file could not be created.") from exc
        return json.dumps(
            {"path": path.relative_to(boundary.root).as_posix(), "bytes": len(encoded)}
        )

    def replace_text_in_file(arguments: Mapping[str, Any]) -> str:
        path = boundary.write_target(str(arguments["path"]))
        old_text = str(arguments["old_text"])
        new_text = str(arguments["new_text"])
        if not old_text:
            raise ToolError("old_text cannot be empty.")
        if not path.is_file():
            raise ToolError("The requested replacement path is not an existing file.")
        try:
            if path.stat().st_size > MAX_TEXT_BYTES:
                raise ToolError(f"File exceeds the {MAX_TEXT_BYTES}-byte modification limit.")
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("The requested file is not readable UTF-8 text.") from exc
        except OSError as exc:
            raise ToolError("The requested file could not be read.") from exc
        occurrences = original.count(old_text)
        if occurrences != 1:
            raise ToolError(f"old_text must match exactly once; found {occurrences} matches.")
        updated = original.replace(old_text, new_text, 1)
        encoded = updated.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            raise ToolError(f"Modified file exceeds the {MAX_WRITE_BYTES}-byte limit.")
        try:
            _atomic_replace_text(path, updated)
        except OSError as exc:
            raise ToolError("The text file could not be modified atomically.") from exc
        return json.dumps(
            {"path": path.relative_to(boundary.root).as_posix(), "bytes": len(encoded)}
        )

    def trash_text_file(arguments: Mapping[str, Any]) -> str:
        path = boundary.write_target(str(arguments["path"]))
        if not path.is_file():
            raise ToolError("Only an existing regular file can be moved to trash.")
        try:
            if path.stat().st_size > MAX_TEXT_BYTES:
                raise ToolError(f"File exceeds the {MAX_TEXT_BYTES}-byte trash limit.")
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("Only readable UTF-8 text files can be moved to trash.") from exc
        except OSError as exc:
            raise ToolError("The requested file could not be inspected.") from exc
        trash_directory = boundary.root / "data" / "trash"
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        trash_name = f"{timestamp}_{uuid4().hex}_{path.name}"
        trash_path = trash_directory / trash_name
        try:
            trash_directory.mkdir(parents=True, exist_ok=True)
            os.replace(path, trash_path)
        except OSError as exc:
            raise ToolError("The file could not be moved to Ato's local trash.") from exc
        return json.dumps(
            {
                "original_path": path.relative_to(boundary.root).as_posix(),
                "recovery_path": trash_path.relative_to(boundary.root).as_posix(),
            }
        )

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
                "properties": {"path": {"type": "string", "description": "Relative file path."}},
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
    registry.register(
        ToolSpec(
            name="search_files",
            description="Search text files inside the workspace for a literal string.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "case_sensitive": {"type": "boolean"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=search_files,
            permission=PermissionLevel.LOW,
        )
    )
    registry.register(
        ToolSpec(
            name="python_syntax_check",
            description="Parse one Python file without executing it and report syntax errors.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=python_syntax_check,
            permission=PermissionLevel.LOW,
        )
    )
    registry.register(
        ToolSpec(
            name="git_diff",
            description=(
                "Show an unstaged or staged Git diff, optionally limited to one workspace path."
            ),
            parameters={
                "type": "object",
                "properties": {"staged": {"type": "boolean"}, "path": {"type": "string"}},
                "additionalProperties": False,
            },
            handler=git_diff,
            permission=PermissionLevel.LOW,
        )
    )
    registry.register(
        ToolSpec(
            name="git_log",
            description="Show a bounded, concise Git commit history.",
            parameters={
                "type": "object",
                "properties": {"max_count": {"type": "integer"}},
                "additionalProperties": False,
            },
            handler=git_log,
            permission=PermissionLevel.LOW,
        )
    )
    registry.register(
        ToolSpec(
            name="git_branches",
            description="List local Git branches without changing repository state.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=git_branches,
            permission=PermissionLevel.LOW,
        )
    )
    registry.register(
        ToolSpec(
            name="git_commit_files",
            description=(
                "Create one local Git commit containing only explicitly named workspace paths. "
                "Requires HIGH confirmation and cannot push, pull, reset, or switch branches."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "paths": {"type": "array", "items": {"type": "string"}},
                    "message": {"type": "string"},
                },
                "required": ["paths", "message"],
                "additionalProperties": False,
            },
            handler=git_commit_files,
            permission=PermissionLevel.HIGH,
        )
    )
    registry.register(
        ToolSpec(
            name="lint_project",
            description=(
                "Run the fixed Ruff lint command. Requires confirmation and cannot "
                "accept command arguments."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=lint_project,
            permission=PermissionLevel.MEDIUM,
        )
    )
    registry.register(
        ToolSpec(
            name="test_project",
            description=(
                "Run the fixed pytest command. Requires confirmation because tests "
                "execute repository code."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=test_project,
            permission=PermissionLevel.HIGH,
        )
    )
    registry.register(
        ToolSpec(
            name="create_text_file",
            description=(
                "Create one new UTF-8 text file inside the workspace. Requires HIGH "
                "confirmation and never overwrites an existing file."
            ),
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            handler=create_text_file,
            permission=PermissionLevel.HIGH,
        )
    )
    registry.register(
        ToolSpec(
            name="replace_text_in_file",
            description=(
                "Replace one exact unique text block in an existing UTF-8 workspace file. "
                "Requires HIGH confirmation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
            handler=replace_text_in_file,
            permission=PermissionLevel.HIGH,
        )
    )
    registry.register(
        ToolSpec(
            name="trash_text_file",
            description=(
                "Move one small UTF-8 workspace file to Ato's recoverable local trash. "
                "Requires CRITICAL confirmation and never deletes directories."
            ),
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=trash_text_file,
            permission=PermissionLevel.CRITICAL,
        )
    )
    return registry


def _write_temporary_text(path: Path, content: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _atomic_create_text(path: Path, content: str) -> None:
    temporary = _write_temporary_text(path, content)
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_replace_text(path: Path, content: str) -> None:
    temporary = _write_temporary_text(path, content)
    try:
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


build_read_only_registry = build_phase3_registry
