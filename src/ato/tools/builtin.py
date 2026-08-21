"""Bounded tools constrained to an authorized workspace root."""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ato.coding import SqliteEditCheckpointStore
from ato.computer import (
    ApplicationLauncher,
    ClipboardWriter,
    ProcessMonitor,
    validate_clipboard_text,
)
from ato.exceptions import CheckpointStoreError, ToolError
from ato.notifications import Notification, Notifier
from ato.research import SqliteResearchStore
from ato.security.audit import AuditLogger
from ato.security.permissions import PermissionLevel, PermissionManager
from ato.tools.github import MAX_GITHUB_ITEMS, GitHubClient
from ato.tools.python_exec import validate_numeric_python
from ato.tools.registry import ToolRegistry, ToolSpec
from ato.tools.research import WebResearchCoordinator
from ato.tools.search import WebSearchClient
from ato.tools.system import collect_system_info
from ato.tools.web import fetch_web_page

IGNORED_DIRECTORIES = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
MAX_LIST_RESULTS = 200
MAX_TEXT_BYTES = 100_000
MAX_SEARCH_FILES = 500
MAX_SEARCH_RESULTS = 100
MAX_COMMAND_OUTPUT = 50_000
MAX_WRITE_BYTES = 100_000
MAX_COMMIT_PATHS = 20
MAX_COMMIT_MESSAGE_CHARS = 200
MAX_PREVIEW_DIFF_CHARS = 10_000
MAX_VERIFY_PYTHON_FILES = 500
MAX_VERIFY_SYNTAX_ERRORS = 20
MAX_VERIFY_STEP_OUTPUT = 3_500
MAX_CHANGE_SET_FILES = 5
MAX_PYTHON_EXEC_OUTPUT = 10_000
PYTHON_EXEC_TIMEOUT_SECONDS = 3
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
    web_fetcher: Callable[[str], str] | None = None,
    web_searcher: WebSearchClient | None = None,
    research_store: SqliteResearchStore | None = None,
    checkpoint_store: SqliteEditCheckpointStore | None = None,
    github_client: GitHubClient | None = None,
    notifier: Notifier | None = None,
    clipboard_writer: ClipboardWriter | None = None,
    application_launcher: ApplicationLauncher | None = None,
    process_monitor: ProcessMonitor | None = None,
) -> ToolRegistry:
    """Create bounded Phase 3 tools with no arbitrary command access."""
    boundary = WorkspaceBoundary(workspace_root)
    registry = ToolRegistry(permission_manager, audit_logger)
    approved_web_fetcher = web_fetcher or fetch_web_page
    research_coordinator = (
        WebResearchCoordinator(web_searcher, approved_web_fetcher, research_store)
        if web_searcher is not None
        else None
    )

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
                shell=False,
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

    def run_allowed_command(arguments: Mapping[str, Any]) -> str:
        command_id = str(arguments["command"])
        raw_target = arguments.get("target")
        if command_id in {"python_version", "git_version"}:
            if raw_target is not None:
                raise ToolError(f"The {command_id} command does not accept a target.")
            command = (
                [sys.executable, "--version"]
                if command_id == "python_version"
                else ["git", "--version"]
            )
            timeout = 10
        else:
            target = boundary.resolve(str(raw_target or "."))
            if not target.exists():
                raise ToolError("The requested command target does not exist.")
            if command_id == "ruff_check":
                command = [
                    sys.executable,
                    "-m",
                    "ruff",
                    "check",
                    "--no-cache",
                    str(target),
                ]
                timeout = 60
            elif command_id == "pytest":
                command = [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-p",
                    "no:cacheprovider",
                    str(target),
                ]
                timeout = 120
            else:  # The registry schema rejects this before execution.
                raise ToolError("The requested command is not allowlisted.")
        result = json.loads(run_fixed(command, timeout))
        result["command"] = command_id
        result["timeout_seconds"] = timeout
        return json.dumps(result)

    def execute_python_calculation(arguments: Mapping[str, Any]) -> str:
        source = str(arguments["code"])
        node_count = validate_numeric_python(source)
        environment = {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
        try:
            with tempfile.TemporaryDirectory(prefix="ato-python-") as temporary_directory:
                result = subprocess.run(
                    [sys.executable, "-I", "-S", "-c", source],
                    cwd=temporary_directory,
                    capture_output=True,
                    text=True,
                    timeout=PYTHON_EXEC_TIMEOUT_SECONDS,
                    check=False,
                    env=environment,
                    shell=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise ToolError(
                f"Python calculation exceeded its {PYTHON_EXEC_TIMEOUT_SECONDS}-second timeout."
            ) from exc
        except OSError as exc:
            raise ToolError("The isolated Python interpreter could not be started.") from exc
        stdout = result.stdout[:MAX_PYTHON_EXEC_OUTPUT]
        stderr = result.stderr[:MAX_PYTHON_EXEC_OUTPUT]
        return json.dumps(
            {
                "exit_code": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "stdout_truncated": len(result.stdout) > MAX_PYTHON_EXEC_OUTPUT,
                "stderr_truncated": len(result.stderr) > MAX_PYTHON_EXEC_OUTPUT,
                "ast_nodes": node_count,
                "isolated_interpreter": True,
                "os_sandbox": False,
            }
        )

    def github_read(arguments: Mapping[str, Any]) -> str:
        assert github_client is not None
        operation = str(arguments["operation"])
        state = str(arguments.get("state", "open"))
        limit = int(arguments.get("limit", 10))
        path = arguments.get("path")
        ref = arguments.get("ref")
        if operation == "repository":
            if any(value is not None for value in (path, ref)) or "state" in arguments:
                raise ToolError("Repository metadata does not accept path, ref, or state.")
            result: Any = github_client.repository_metadata()
        elif operation == "issues":
            if path is not None or ref is not None:
                raise ToolError("Issue listing does not accept path or ref.")
            result = github_client.list_issues(state, limit)
        elif operation == "pull_requests":
            if path is not None or ref is not None:
                raise ToolError("Pull-request listing does not accept path or ref.")
            result = github_client.list_pull_requests(state, limit)
        elif operation == "commits":
            if path is not None or ref is not None or "state" in arguments:
                raise ToolError("Commit listing does not accept path, ref, or state.")
            result = github_client.list_commits(limit)
        elif operation == "file":
            if not isinstance(path, str):
                raise ToolError("GitHub file reading requires path.")
            if "state" in arguments or "limit" in arguments:
                raise ToolError("GitHub file reading does not accept state or limit.")
            result = github_client.read_file(path, None if ref is None else str(ref))
        else:
            raise ToolError("The requested GitHub operation is not allowlisted.")
        return json.dumps(
            {
                "repository": github_client.repository,
                "operation": operation,
                "untrusted_external": True,
                "result": result,
            }
        )

    def send_notification(arguments: Mapping[str, Any]) -> str:
        assert notifier is not None
        notification = Notification.validated(
            str(arguments["title"]),
            str(arguments["message"]),
            str(arguments.get("level", "info")),
        )
        provider = notifier.send(notification)
        return json.dumps(
            {
                "delivered": True,
                "provider": provider,
                "level": notification.level.value,
                "title": notification.title,
            }
        )

    def write_clipboard(arguments: Mapping[str, Any]) -> str:
        assert clipboard_writer is not None
        text = str(arguments["text"])
        summary = validate_clipboard_text(text)
        clipboard_writer.write(text)
        return json.dumps({"written": True, "provider": "windows", **summary})

    def launch_application(arguments: Mapping[str, Any]) -> str:
        assert application_launcher is not None
        application = str(arguments["application"])
        process_id = application_launcher.launch(application)
        return json.dumps(
            {"launched": True, "application": application, "process_id": process_id}
        )

    def inspect_processes(arguments: Mapping[str, Any]) -> str:
        assert process_monitor is not None
        processes = process_monitor.snapshot()
        operation = str(arguments["operation"])
        if operation == "status":
            if "name" in arguments or "limit" in arguments:
                raise ToolError("Process status does not accept name or limit.")
            if "process_id" not in arguments:
                raise ToolError("Process status requires process_id.")
            process_id = int(arguments["process_id"])
            match = next(
                (process for process in processes if process["process_id"] == process_id), None
            )
            return json.dumps({"found": match is not None, "process": match})
        if "process_id" in arguments:
            raise ToolError("Process listing does not accept process_id.")
        name = str(arguments.get("name", "")).casefold()
        if name:
            processes = [process for process in processes if name in process["name"].casefold()]
        limit = int(arguments.get("limit", 50))
        return json.dumps(
            {"processes": processes[:limit], "truncated": len(processes) > limit}
        )

    def preview_github_issue(arguments: Mapping[str, Any]) -> str:
        assert github_client is not None
        return json.dumps(
            github_client.preview_issue(
                str(arguments["title"]),
                str(arguments.get("body", "")),
                [str(label) for label in arguments.get("labels", [])],
            )
        )

    def create_github_issue(arguments: Mapping[str, Any]) -> str:
        assert github_client is not None
        return json.dumps(
            github_client.create_issue(
                str(arguments["title"]),
                str(arguments.get("body", "")),
                [str(label) for label in arguments.get("labels", [])],
                str(arguments["expected_repository"]),
                str(arguments["expected_sha256"]),
            )
        )

    def preview_github_comment(arguments: Mapping[str, Any]) -> str:
        assert github_client is not None
        return json.dumps(
            github_client.preview_comment(int(arguments["issue_number"]), str(arguments["body"]))
        )

    def create_github_comment(arguments: Mapping[str, Any]) -> str:
        assert github_client is not None
        return json.dumps(
            github_client.create_comment(
                int(arguments["issue_number"]),
                str(arguments["body"]),
                str(arguments["expected_repository"]),
                str(arguments["expected_sha256"]),
            )
        )

    def preview_github_pull_request(arguments: Mapping[str, Any]) -> str:
        assert github_client is not None
        return json.dumps(
            github_client.preview_pull_request(
                str(arguments["base"]),
                str(arguments["head"]),
                str(arguments["title"]),
                str(arguments.get("body", "")),
                bool(arguments.get("draft", False)),
            )
        )

    def create_github_pull_request(arguments: Mapping[str, Any]) -> str:
        assert github_client is not None
        return json.dumps(
            github_client.create_pull_request(
                str(arguments["base"]),
                str(arguments["head"]),
                str(arguments["title"]),
                str(arguments.get("body", "")),
                bool(arguments.get("draft", False)),
                str(arguments["expected_repository"]),
                str(arguments["expected_sha256"]),
            )
        )

    def verify_code_change(arguments: Mapping[str, Any]) -> str:
        del arguments
        syntax = _verify_python_syntax()
        lint = _verification_command(
            [sys.executable, "-m", "ruff", "check", "--no-cache", "."], 60
        )
        tests = _verification_command(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"], 120
        )
        statuses = {syntax["status"], lint["status"], tests["status"]}
        if "fail" in statuses:
            overall = "fail"
        elif statuses == {"pass"}:
            overall = "pass"
        else:
            overall = "incomplete"
        return json.dumps(
            {
                "overall": overall,
                "syntax": syntax,
                "lint": lint,
                "tests": tests,
                "automatic_fixes_applied": False,
            }
        )

    def _verify_python_syntax() -> dict[str, Any]:
        candidates = sorted(boundary.root.rglob("*.py"))
        checked = 0
        skipped_large = 0
        errors: list[dict[str, Any]] = []
        truncated = False
        for path in candidates:
            relative = path.relative_to(boundary.root)
            if (
                not path.is_file()
                or not boundary.contains(path)
                or any(part in IGNORED_DIRECTORIES for part in relative.parts)
            ):
                continue
            if checked >= MAX_VERIFY_PYTHON_FILES:
                truncated = True
                break
            try:
                if path.stat().st_size > MAX_TEXT_BYTES:
                    skipped_large += 1
                    continue
                source = path.read_text(encoding="utf-8")
                checked += 1
                ast.parse(source, filename=relative.as_posix())
            except (OSError, UnicodeDecodeError) as exc:
                errors.append({"path": relative.as_posix(), "message": type(exc).__name__})
            except SyntaxError as exc:
                errors.append(
                    {
                        "path": relative.as_posix(),
                        "line": exc.lineno,
                        "offset": exc.offset,
                        "message": exc.msg,
                    }
                )
            if len(errors) >= MAX_VERIFY_SYNTAX_ERRORS:
                truncated = True
                break
        if errors:
            status = "fail"
        elif truncated or skipped_large:
            status = "incomplete"
        else:
            status = "pass"
        return {
            "status": status,
            "files_checked": checked,
            "skipped_large": skipped_large,
            "errors": errors,
            "truncated": truncated,
        }

    def _verification_command(command: list[str], timeout: int) -> dict[str, Any]:
        try:
            result = json.loads(run_fixed(command, timeout))
        except ToolError as exc:
            return {"status": "error", "error": str(exc)}
        output = str(result.get("output", ""))
        return {
            "status": "pass" if int(result.get("exit_code", -1)) == 0 else "fail",
            "exit_code": int(result.get("exit_code", -1)),
            "output": output[:MAX_VERIFY_STEP_OUTPUT],
            "truncated": bool(result.get("truncated"))
            or len(output) > MAX_VERIFY_STEP_OUTPUT,
        }

    def system_info(arguments: Mapping[str, Any]) -> str:
        del arguments
        try:
            return json.dumps(collect_system_info(boundary.root))
        except OSError as exc:
            raise ToolError("System capacity information could not be collected.") from exc

    def fetch_page(arguments: Mapping[str, Any]) -> str:
        return approved_web_fetcher(str(arguments["url"]))

    def search_web(arguments: Mapping[str, Any]) -> str:
        assert web_searcher is not None
        return web_searcher.search(
            str(arguments["query"]), count=int(arguments.get("count", 5))
        )

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
        raw_sha256 = str(arguments["expected_sha256"])
        if not re.fullmatch(r"[a-fA-F0-9]{64}", raw_sha256):
            raise ToolError("expected_sha256 must be a 64-character hexadecimal SHA-256 digest.")
        expected_sha256 = raw_sha256.casefold()
        original = _read_modifiable_text(path)
        original_sha256 = hashlib.sha256(original.encode("utf-8")).hexdigest()
        if original_sha256 != expected_sha256:
            raise ToolError("File changed after preview; request a new preview before editing.")
        updated = _replace_unique_text(original, old_text, new_text)
        encoded = updated.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            raise ToolError(f"Modified file exceeds the {MAX_WRITE_BYTES}-byte limit.")
        updated_sha256 = hashlib.sha256(encoded).hexdigest()
        relative = path.relative_to(boundary.root).as_posix()
        checkpoint = (
            checkpoint_store.create(
                relative, original, original_sha256, updated_sha256
            )
            if checkpoint_store is not None
            else None
        )
        try:
            _atomic_replace_text(path, updated)
        except OSError as exc:
            raise ToolError("The text file could not be modified atomically.") from exc
        return json.dumps(
            {
                "path": relative,
                "bytes": len(encoded),
                "original_sha256": original_sha256,
                "updated_sha256": updated_sha256,
                "checkpoint_id": None if checkpoint is None else checkpoint.id,
            }
        )

    def preview_text_change(arguments: Mapping[str, Any]) -> str:
        path = boundary.write_target(str(arguments["path"]))
        old_text = str(arguments["old_text"])
        new_text = str(arguments["new_text"])
        original = _read_modifiable_text(path)
        updated = _replace_unique_text(original, old_text, new_text)
        relative = path.relative_to(boundary.root).as_posix()
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            )
        )
        truncated = len(diff) > MAX_PREVIEW_DIFF_CHARS
        return json.dumps(
            {
                "path": relative,
                "original_sha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
                "updated_sha256": hashlib.sha256(updated.encode("utf-8")).hexdigest(),
                "diff": diff[:MAX_PREVIEW_DIFF_CHARS],
                "diff_truncated": truncated,
            }
        )

    def preview_text_change_set(arguments: Mapping[str, Any]) -> str:
        prepared = _prepare_change_set(arguments["changes"])
        combined_diff = ""
        for change in prepared:
            combined_diff += "".join(
                difflib.unified_diff(
                    change["original"].splitlines(keepends=True),
                    change["updated"].splitlines(keepends=True),
                    fromfile=f"a/{change['relative']}",
                    tofile=f"b/{change['relative']}",
                )
            )
        truncated = len(combined_diff) > MAX_PREVIEW_DIFF_CHARS
        return json.dumps(
            {
                "changes": [
                    {
                        "path": change["relative"],
                        "original_sha256": change["original_sha256"],
                        "updated_sha256": change["updated_sha256"],
                    }
                    for change in prepared
                ],
                "change_set_sha256": _change_set_sha256(prepared),
                "diff": combined_diff[:MAX_PREVIEW_DIFF_CHARS],
                "diff_truncated": truncated,
            }
        )

    def apply_text_change_set(arguments: Mapping[str, Any]) -> str:
        raw_changes = arguments["changes"]
        prepared = _prepare_change_set(raw_changes)
        expected_set_sha256 = str(arguments["expected_change_set_sha256"]).casefold()
        if not re.fullmatch(r"[a-f0-9]{64}", expected_set_sha256):
            raise ToolError("expected_change_set_sha256 must be a 64-character SHA-256 digest.")
        if _change_set_sha256(prepared) != expected_set_sha256:
            raise ToolError("Change set differs from the reviewed preview; request a new preview.")
        for raw, change in zip(raw_changes, prepared, strict=True):
            expected_file_sha256 = str(raw.get("expected_sha256", "")).casefold()
            if expected_file_sha256 != change["original_sha256"]:
                raise ToolError(
                    f"File {change['relative']} differs from its reviewed preview digest."
                )

        temporaries: list[Path] = []
        checkpoints = []
        applied: list[dict[str, Any]] = []
        try:
            for change in prepared:
                temporaries.append(_write_temporary_text(change["path"], change["updated"]))
            if checkpoint_store is not None:
                for change in prepared:
                    checkpoints.append(
                        checkpoint_store.create(
                            change["relative"],
                            change["original"],
                            change["original_sha256"],
                            change["updated_sha256"],
                        )
                    )
            for change in prepared:
                current = _read_modifiable_text(change["path"])
                if hashlib.sha256(current.encode("utf-8")).hexdigest() != change["original_sha256"]:
                    raise ToolError(
                        f"File {change['relative']} changed while preparing the transaction."
                    )
            for temporary, change in zip(temporaries, prepared, strict=True):
                os.replace(temporary, change["path"])
                applied.append(change)
        except ToolError:
            raise
        except OSError as exc:
            recovery_failed = False
            for change in reversed(applied):
                try:
                    _atomic_replace_text(change["path"], change["original"])
                    if checkpoints:
                        checkpoint_index = prepared.index(change)
                        if not checkpoint_store or not checkpoint_store.mark_restored(
                            checkpoints[checkpoint_index].id
                        ):
                            recovery_failed = True
                except (OSError, CheckpointStoreError):
                    recovery_failed = True
            if recovery_failed:
                raise ToolError(
                    "Multi-file write failed and automatic recovery was incomplete; "
                    "use edit checkpoints before further changes."
                ) from exc
            raise ToolError(
                "Multi-file write failed; already-written files were restored."
            ) from exc
        finally:
            for temporary in temporaries:
                temporary.unlink(missing_ok=True)
        return json.dumps(
            {
                "change_set_sha256": expected_set_sha256,
                "paths": [change["relative"] for change in prepared],
                "checkpoint_ids": [checkpoint.id for checkpoint in checkpoints],
                "files_changed": len(prepared),
            }
        )

    def _read_modifiable_text(path: Path) -> str:
        if not path.is_file():
            raise ToolError("The requested replacement path is not an existing file.")
        try:
            if path.stat().st_size > MAX_TEXT_BYTES:
                raise ToolError(f"File exceeds the {MAX_TEXT_BYTES}-byte modification limit.")
            return path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("The requested file is not readable UTF-8 text.") from exc
        except OSError as exc:
            raise ToolError("The requested file could not be read.") from exc

    def _replace_unique_text(original: str, old_text: str, new_text: str) -> str:
        if not old_text:
            raise ToolError("old_text cannot be empty.")
        occurrences = original.count(old_text)
        if occurrences != 1:
            raise ToolError(f"old_text must match exactly once; found {occurrences} matches.")
        updated = original.replace(old_text, new_text, 1)
        if updated == original:
            raise ToolError("Replacement would not change the file.")
        return updated

    def _prepare_change_set(raw_changes: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_changes, list) or not 1 <= len(raw_changes) <= MAX_CHANGE_SET_FILES:
            raise ToolError(f"changes must contain between 1 and {MAX_CHANGE_SET_FILES} items.")
        prepared: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for raw in raw_changes:
            if not isinstance(raw, Mapping):
                raise ToolError("Every change must be an object.")
            path = boundary.write_target(str(raw["path"]))
            relative = path.relative_to(boundary.root).as_posix()
            if relative in seen_paths:
                raise ToolError("Change-set paths cannot contain duplicates.")
            seen_paths.add(relative)
            original = _read_modifiable_text(path)
            updated = _replace_unique_text(original, str(raw["old_text"]), str(raw["new_text"]))
            if len(updated.encode("utf-8")) > MAX_WRITE_BYTES:
                raise ToolError(f"Modified file {relative} exceeds the write limit.")
            prepared.append(
                {
                    "path": path,
                    "relative": relative,
                    "original": original,
                    "updated": updated,
                    "original_sha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
                    "updated_sha256": hashlib.sha256(updated.encode("utf-8")).hexdigest(),
                }
            )
        return prepared

    def _change_set_sha256(prepared: list[dict[str, Any]]) -> str:
        contract = [
            {
                "path": change["relative"],
                "original_sha256": change["original_sha256"],
                "updated_sha256": change["updated_sha256"],
            }
            for change in prepared
        ]
        encoded = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def list_edit_checkpoints(arguments: Mapping[str, Any]) -> str:
        assert checkpoint_store is not None
        records = checkpoint_store.list_checkpoints(int(arguments.get("limit", 20)))
        return json.dumps(
            {
                "checkpoints": [
                    {
                        "id": record.id,
                        "path": record.path,
                        "original_sha256": record.original_sha256,
                        "updated_sha256": record.updated_sha256,
                        "created_at": record.created_at,
                        "restored": record.restored_at is not None,
                    }
                    for record in records
                ]
            }
        )

    def rollback_text_edit(arguments: Mapping[str, Any]) -> str:
        assert checkpoint_store is not None
        checkpoint_id = int(arguments["checkpoint_id"])
        checkpoint = checkpoint_store.load(checkpoint_id)
        if checkpoint is None:
            raise ToolError("Edit checkpoint ID was not found.")
        record = checkpoint.record
        if record.restored_at is not None:
            raise ToolError("Edit checkpoint has already been restored.")
        path = boundary.write_target(record.path)
        current = _read_modifiable_text(path)
        current_sha256 = hashlib.sha256(current.encode("utf-8")).hexdigest()
        if current_sha256 != record.updated_sha256:
            raise ToolError(
                "File changed after the checkpointed edit; rollback will not overwrite newer work."
            )
        original_encoded = checkpoint.original_content.encode("utf-8")
        if hashlib.sha256(original_encoded).hexdigest() != record.original_sha256:
            raise ToolError("Edit checkpoint content failed SHA-256 verification.")
        try:
            _atomic_replace_text(path, checkpoint.original_content)
        except OSError as exc:
            raise ToolError("Checkpoint rollback could not restore the file atomically.") from exc
        if not checkpoint_store.mark_restored(checkpoint_id):
            raise ToolError("Checkpoint rollback state could not be finalized safely.")
        return json.dumps(
            {
                "checkpoint_id": checkpoint_id,
                "path": record.path,
                "restored_sha256": record.original_sha256,
            }
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
            name="preview_text_change",
            description=(
                "Preview one exact unique text replacement as a bounded unified diff without "
                "writing. Returns the SHA-256 required by replace_text_in_file."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string", "minLength": 1},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
            handler=preview_text_change,
            permission=PermissionLevel.LOW,
        )
    )
    if github_client is not None:
        registry.register(
            ToolSpec(
                name="github_read",
                description=(
                    "Read bounded metadata, issues, pull requests, commits, or one UTF-8 file "
                    "from the single configured GitHub repository. Never mutates GitHub."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": ["repository", "issues", "pull_requests", "commits", "file"],
                        },
                        "state": {"type": "string", "enum": ["open", "closed", "all"]},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_GITHUB_ITEMS,
                        },
                        "path": {"type": "string", "minLength": 1, "maxLength": 500},
                        "ref": {"type": "string", "minLength": 1, "maxLength": 200},
                    },
                    "required": ["operation"],
                    "additionalProperties": False,
                },
                handler=github_read,
                permission=PermissionLevel.MEDIUM,
            )
        )
    if github_client is not None:
        registry.register(
            ToolSpec(
                name="preview_github_issue",
                description=(
                    "Preview a bounded issue for the configured GitHub repository and return "
                    "the SHA-256 fingerprint required for confirmed creation. Does not use "
                    "the network or change GitHub."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 200},
                        "body": {"type": "string", "maxLength": 10_000},
                        "labels": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1, "maxLength": 50},
                            "maxItems": 10,
                        },
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                },
                handler=preview_github_issue,
                permission=PermissionLevel.LOW,
            )
        )
        registry.register(
            ToolSpec(
                name="create_github_issue",
                description=(
                    "Create exactly one previously previewed issue in the configured GitHub "
                    "repository after HIGH confirmation. Requires a configured token."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 200},
                        "body": {"type": "string", "maxLength": 10_000},
                        "labels": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1, "maxLength": 50},
                            "maxItems": 10,
                        },
                        "expected_repository": {
                            "type": "string",
                            "minLength": 3,
                            "maxLength": 200,
                        },
                        "expected_sha256": {
                            "type": "string",
                            "minLength": 64,
                            "maxLength": 64,
                        },
                    },
                    "required": ["title", "expected_repository", "expected_sha256"],
                    "additionalProperties": False,
                },
                handler=create_github_issue,
                permission=PermissionLevel.HIGH,
            )
        )
    if github_client is not None:
        registry.register(
            ToolSpec(
                name="preview_github_comment",
                description=(
                    "Preview one bounded comment for an exact GitHub issue number and return "
                    "the fingerprint required for confirmed creation. Does not use the network."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "issue_number": {"type": "integer", "minimum": 1},
                        "body": {"type": "string", "minLength": 1, "maxLength": 10_000},
                    },
                    "required": ["issue_number", "body"],
                    "additionalProperties": False,
                },
                handler=preview_github_comment,
                permission=PermissionLevel.LOW,
            )
        )
        registry.register(
            ToolSpec(
                name="create_github_comment",
                description=(
                    "Post exactly one previously previewed comment to an exact GitHub issue "
                    "after HIGH confirmation. Cannot edit or delete comments."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "issue_number": {"type": "integer", "minimum": 1},
                        "body": {"type": "string", "minLength": 1, "maxLength": 10_000},
                        "expected_repository": {
                            "type": "string",
                            "minLength": 3,
                            "maxLength": 200,
                        },
                        "expected_sha256": {
                            "type": "string",
                            "minLength": 64,
                            "maxLength": 64,
                        },
                    },
                    "required": [
                        "issue_number",
                        "body",
                        "expected_repository",
                        "expected_sha256",
                    ],
                    "additionalProperties": False,
                },
                handler=create_github_comment,
                permission=PermissionLevel.HIGH,
            )
        )
        pull_properties = {
            "base": {"type": "string", "minLength": 1, "maxLength": 200},
            "head": {"type": "string", "minLength": 1, "maxLength": 200},
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "body": {"type": "string", "maxLength": 10_000},
            "draft": {"type": "boolean"},
        }
        registry.register(
            ToolSpec(
                name="preview_github_pull_request",
                description=(
                    "Preview a pull request between two branches in the configured repository "
                    "and return its review fingerprint. Does not use the network."
                ),
                parameters={
                    "type": "object",
                    "properties": pull_properties,
                    "required": ["base", "head", "title"],
                    "additionalProperties": False,
                },
                handler=preview_github_pull_request,
                permission=PermissionLevel.LOW,
            )
        )
        registry.register(
            ToolSpec(
                name="create_github_pull_request",
                description=(
                    "Create one previously previewed pull request between existing branches "
                    "after HIGH confirmation. Cannot merge or delete branches."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        **pull_properties,
                        "expected_repository": {
                            "type": "string",
                            "minLength": 3,
                            "maxLength": 200,
                        },
                        "expected_sha256": {
                            "type": "string",
                            "minLength": 64,
                            "maxLength": 64,
                        },
                    },
                    "required": [
                        "base",
                        "head",
                        "title",
                        "expected_repository",
                        "expected_sha256",
                    ],
                    "additionalProperties": False,
                },
                handler=create_github_pull_request,
                permission=PermissionLevel.HIGH,
            )
        )
    if notifier is not None:
        registry.register(
            ToolSpec(
                name="send_notification",
                description=(
                    "Send one clearly labelled local notification through the configured "
                    "provider after MEDIUM confirmation."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 100},
                        "message": {"type": "string", "minLength": 1, "maxLength": 1_000},
                        "level": {
                            "type": "string",
                            "enum": ["info", "success", "warning", "error"],
                        },
                    },
                    "required": ["title", "message"],
                    "additionalProperties": False,
                },
                handler=send_notification,
                permission=PermissionLevel.MEDIUM,
            )
        )
    if clipboard_writer is not None:
        registry.register(
            ToolSpec(
                name="write_clipboard",
                description=(
                    "Replace the local text clipboard with bounded non-secret text after HIGH "
                    "confirmation. Cannot read the clipboard."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "minLength": 1, "maxLength": 10_000}
                    },
                    "required": ["text"],
                    "additionalProperties": False,
                },
                handler=write_clipboard,
                permission=PermissionLevel.HIGH,
            )
        )
    if application_launcher is not None:
        registry.register(
            ToolSpec(
                name="launch_application",
                description=(
                    "Launch one fixed argument-free Windows application profile after HIGH "
                    "confirmation. Cannot open a path, file, or URL."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "application": {
                            "type": "string",
                            "enum": ["notepad", "calculator", "file_explorer"],
                        }
                    },
                    "required": ["application"],
                    "additionalProperties": False,
                },
                handler=launch_application,
                permission=PermissionLevel.HIGH,
            )
        )
    if process_monitor is not None:
        registry.register(
            ToolSpec(
                name="inspect_processes",
                description=(
                    "List bounded process capacity metadata or inspect one exact PID. Excludes "
                    "users, paths, arguments, environment, windows, and network activity."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string", "enum": ["list", "status"]},
                        "process_id": {"type": "integer", "minimum": 0},
                        "name": {"type": "string", "minLength": 1, "maxLength": 100},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    "required": ["operation"],
                    "additionalProperties": False,
                },
                handler=inspect_processes,
                permission=PermissionLevel.MEDIUM,
            )
        )
    registry.register(
        ToolSpec(
            name="preview_text_change_set",
            description=(
                "Preview one to five exact text replacements as one bounded combined diff and "
                "review fingerprint without writing."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "changes": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 5,
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "old_text": {"type": "string", "minLength": 1},
                                "new_text": {"type": "string"},
                            },
                            "required": ["path", "old_text", "new_text"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["changes"],
                "additionalProperties": False,
            },
            handler=preview_text_change_set,
            permission=PermissionLevel.LOW,
        )
    )
    registry.register(
        ToolSpec(
            name="apply_text_change_set",
            description=(
                "Apply one reviewed one-to-five-file exact change set after HIGH confirmation. "
                "Prevalidates every digest and restores earlier files if a later write fails."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "changes": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 5,
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "old_text": {"type": "string", "minLength": 1},
                                "new_text": {"type": "string"},
                                "expected_sha256": {
                                    "type": "string",
                                    "minLength": 64,
                                    "maxLength": 64,
                                },
                            },
                            "required": ["path", "old_text", "new_text", "expected_sha256"],
                            "additionalProperties": False,
                        },
                    },
                    "expected_change_set_sha256": {
                        "type": "string",
                        "minLength": 64,
                        "maxLength": 64,
                    },
                },
                "required": ["changes", "expected_change_set_sha256"],
                "additionalProperties": False,
            },
            handler=apply_text_change_set,
            permission=PermissionLevel.HIGH,
        )
    )
    if checkpoint_store is not None:
        registry.register(
            ToolSpec(
                name="list_edit_checkpoints",
                description="List bounded recoverable edit checkpoint metadata without content.",
                parameters={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100}
                    },
                    "additionalProperties": False,
                },
                handler=list_edit_checkpoints,
                permission=PermissionLevel.LOW,
            )
        )
        registry.register(
            ToolSpec(
                name="rollback_text_edit",
                description=(
                    "Restore one checkpointed text edit after HIGH confirmation. Refuses stale "
                    "files, protected paths, and previously restored checkpoints."
                ),
                parameters={
                    "type": "object",
                    "properties": {"checkpoint_id": {"type": "integer", "minimum": 1}},
                    "required": ["checkpoint_id"],
                    "additionalProperties": False,
                },
                handler=rollback_text_edit,
                permission=PermissionLevel.HIGH,
            )
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
                    "query": {"type": "string", "minLength": 1, "maxLength": 1000},
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
                "properties": {
                    "max_count": {"type": "integer", "minimum": 1, "maximum": 50}
                },
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
                    "paths": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "minItems": 1,
                        "maxItems": 20,
                        "uniqueItems": True,
                    },
                    "message": {"type": "string", "minLength": 1, "maxLength": 200},
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
            name="verify_code_change",
            description=(
                "Run bounded Python syntax parsing, fixed Ruff linting, and fixed pytest after "
                "confirmation. Reports each result and never applies automatic fixes."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=verify_code_change,
            permission=PermissionLevel.HIGH,
        )
    )
    registry.register(
        ToolSpec(
            name="run_allowed_command",
            description=(
                "Run one named development command profile without a shell or arbitrary "
                "arguments. Targets are optional and restricted to the workspace."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "enum": ["python_version", "git_version", "ruff_check", "pytest"],
                    },
                    "target": {"type": "string", "minLength": 1, "maxLength": 500},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            handler=run_allowed_command,
            permission=PermissionLevel.HIGH,
        )
    )
    registry.register(
        ToolSpec(
            name="execute_python_calculation",
            description=(
                "Execute validated numeric-only Python in isolated interpreter mode and a "
                "temporary directory. Imports, attributes, containers, loops, and file, "
                "network, or process access are rejected. This is not an OS sandbox."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 3_000,
                    }
                },
                "required": ["code"],
                "additionalProperties": False,
            },
            handler=execute_python_calculation,
            permission=PermissionLevel.CRITICAL,
        )
    )
    registry.register(
        ToolSpec(
            name="system_info",
            description=(
                "Report non-identifying OS, CPU, RAM, Python, and workspace-disk capacity "
                "without probing the network."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=system_info,
            permission=PermissionLevel.LOW,
        )
    )
    registry.register(
        ToolSpec(
            name="fetch_web_page",
            description=(
                "Fetch readable text from one explicitly approved public HTTPS page. "
                "Supports bounded HTML, plain text, and text-based PDF sources. Blocks private "
                "networks and redirects and enforces strict size, page, and timeout limits."
            ),
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string", "minLength": 1, "maxLength": 2048}},
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=fetch_page,
            permission=PermissionLevel.MEDIUM,
        )
    )
    if web_searcher is not None:
        registry.register(
            ToolSpec(
                name="web_search",
                description=(
                    "Search the public web through the configured provider after confirmation. "
                    "Returns untrusted titles, URLs, and snippets; fetch sources before making "
                    "detailed claims."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": 400},
                        "count": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=search_web,
                permission=PermissionLevel.MEDIUM,
            )
        )
        if research_store is not None:

            def list_research_sessions(arguments: Mapping[str, Any]) -> str:
                records = research_store.list_sessions(int(arguments.get("limit", 20)))
                return json.dumps(
                    {
                        "sessions": [
                            {
                                "id": record.id,
                                "query": record.query,
                                "created_at": record.created_at,
                                "successful_sources": record.successful_sources,
                                "coverage": record.coverage,
                            }
                            for record in records
                        ]
                    }
                )

            def export_research_report(arguments: Mapping[str, Any]) -> str:
                session_id = int(arguments["session_id"])
                path = boundary.write_target(str(arguments["path"]))
                if path.suffix.casefold() != ".md":
                    raise ToolError("Research reports must use a .md file extension.")
                if path.exists():
                    raise ToolError("Research report export never overwrites an existing file.")
                if not path.parent.is_dir():
                    raise ToolError("Research report parent directory does not exist.")
                report = research_store.render_markdown(session_id)
                if report is None:
                    raise ToolError("Research session ID was not found.")
                if len(report.encode("utf-8")) > MAX_WRITE_BYTES:
                    raise ToolError("Research report exceeds the export size limit.")
                try:
                    _atomic_create_text(path, report)
                except FileExistsError as exc:
                    raise ToolError(
                        "Research report export never overwrites an existing file."
                    ) from exc
                except OSError as exc:
                    raise ToolError("Research report could not be created atomically.") from exc
                return json.dumps(
                    {
                        "session_id": session_id,
                        "path": path.relative_to(boundary.root).as_posix(),
                        "bytes": len(report.encode("utf-8")),
                    }
                )

            registry.register(
                ToolSpec(
                    name="list_research_sessions",
                    description="List bounded locally saved research sessions without page text.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100}
                        },
                        "additionalProperties": False,
                    },
                    handler=list_research_sessions,
                    permission=PermissionLevel.LOW,
                )
            )
            registry.register(
                ToolSpec(
                    name="export_research_report",
                    description=(
                        "Export one saved research evidence session to a new Markdown file. "
                        "Requires HIGH confirmation and never overwrites files."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "session_id": {"type": "integer", "minimum": 1},
                            "path": {"type": "string", "minLength": 1, "maxLength": 500},
                        },
                        "required": ["session_id", "path"],
                        "additionalProperties": False,
                    },
                    handler=export_research_report,
                    permission=PermissionLevel.HIGH,
                )
            )
        assert research_coordinator is not None
        registry.register(
            ToolSpec(
                name="research_web",
                description=(
                    "Search and fetch up to three diversified public HTTPS sources after one "
                    "confirmation. Returns bounded untrusted evidence and per-source failures."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": 400},
                        "max_sources": {"type": "integer", "minimum": 1, "maximum": 3},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=lambda arguments: research_coordinator.research(
                    str(arguments["query"]), int(arguments.get("max_sources", 3))
                ),
                permission=PermissionLevel.MEDIUM,
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
                "Requires HIGH confirmation and the SHA-256 returned by preview_text_change."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "expected_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
                },
                "required": ["path", "old_text", "new_text", "expected_sha256"],
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
