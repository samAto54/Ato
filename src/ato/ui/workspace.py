"""Narrow guarded workspace operations for the desktop UI."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ato.exceptions import ToolError
from ato.tools import ToolRegistry

MAX_GUI_SEARCH_RESULTS = 100
MAX_GUI_INSPECTION_CHARS = 20_000
MAX_GUI_REPLACEMENT_CHARS = 10_000


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


@dataclass(frozen=True, slots=True)
class WorkspaceChangePreview:
    path: str
    diff: str
    original_sha256: str
    updated_sha256: str
    truncated: bool
    old_text: str
    new_text: str


@dataclass(frozen=True, slots=True)
class WorkspaceChangeResult:
    path: str
    bytes_written: int
    updated_sha256: str
    checkpoint_id: int | None


@dataclass(frozen=True, slots=True)
class WorkspaceCheckpoint:
    id: int
    path: str
    original_sha256: str
    updated_sha256: str
    created_at: str
    restored: bool

    def display(self) -> str:
        status = "RESTORED" if self.restored else "AVAILABLE"
        return (
            f"#{self.id}  {status}  {self.created_at}\n{self.path}\n"
            f"original {self.original_sha256}\nupdated  {self.updated_sha256}"
        )


@dataclass(frozen=True, slots=True)
class WorkspaceRollbackResult:
    checkpoint_id: int
    path: str
    restored_sha256: str


@dataclass(slots=True)
class DesktopWorkspaceSearch:
    """Expose fixed inspections and exact preview-bound edits from a private registry."""

    registry: ToolRegistry
    reviewed_checkpoints: dict[int, WorkspaceCheckpoint] = field(default_factory=dict, init=False)

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

    def preview_text_change(
        self,
        path: str,
        old_text: str,
        new_text: str,
    ) -> WorkspaceChangePreview:
        cleaned_path = path.strip()
        if not cleaned_path or len(cleaned_path) > 500:
            raise ToolError("Change preview requires a bounded relative file path.")
        if not old_text or len(old_text) > MAX_GUI_REPLACEMENT_CHARS:
            raise ToolError("Existing text must contain 1-10,000 characters.")
        if len(new_text) > MAX_GUI_REPLACEMENT_CHARS:
            raise ToolError("Replacement text cannot exceed 10,000 characters.")
        raw = self.registry.execute(
            "preview_text_change",
            {"path": cleaned_path, "old_text": old_text, "new_text": new_text},
            user_request="Preview one exact text replacement from the desktop",
        )
        try:
            payload = json.loads(raw)
            result_path = str(payload["path"])
            original_sha256 = str(payload["original_sha256"])
            updated_sha256 = str(payload["updated_sha256"])
            diff = str(payload["diff"])
            truncated = bool(payload["diff_truncated"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ToolError("Text change preview returned an invalid result.") from exc
        if (
            len(original_sha256) != 64
            or len(updated_sha256) != 64
            or any(character not in "0123456789abcdef" for character in original_sha256.casefold())
            or any(character not in "0123456789abcdef" for character in updated_sha256.casefold())
        ):
            raise ToolError("Text change preview returned an invalid result.")
        display_truncated = truncated or len(diff) > MAX_GUI_INSPECTION_CHARS
        return WorkspaceChangePreview(
            result_path,
            diff[:MAX_GUI_INSPECTION_CHARS],
            original_sha256.casefold(),
            updated_sha256.casefold(),
            display_truncated,
            old_text,
            new_text,
        )

    def apply_text_change(self, preview: WorkspaceChangePreview) -> WorkspaceChangeResult:
        if preview.truncated:
            raise ToolError("A truncated diff cannot be applied from the desktop.")
        raw = self.registry.execute(
            "replace_text_in_file",
            {
                "path": preview.path,
                "old_text": preview.old_text,
                "new_text": preview.new_text,
                "expected_sha256": preview.original_sha256,
            },
            user_request="Apply the exact reviewed desktop text-change preview",
        )
        try:
            payload = json.loads(raw)
            path = str(payload["path"])
            bytes_written = int(payload["bytes"])
            original_sha256 = str(payload["original_sha256"]).casefold()
            updated_sha256 = str(payload["updated_sha256"]).casefold()
            raw_checkpoint = payload["checkpoint_id"]
            checkpoint_id = None if raw_checkpoint is None else int(raw_checkpoint)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ToolError("Text change returned an invalid result.") from exc
        if (
            path != preview.path
            or original_sha256 != preview.original_sha256
            or updated_sha256 != preview.updated_sha256
            or bytes_written < 0
            or (checkpoint_id is not None and checkpoint_id < 1)
        ):
            raise ToolError("Text change returned an invalid result.")
        return WorkspaceChangeResult(path, bytes_written, updated_sha256, checkpoint_id)

    def list_checkpoints(self) -> tuple[WorkspaceCheckpoint, ...]:
        raw = self.registry.execute(
            "list_edit_checkpoints",
            {"limit": 20},
            user_request="List recent edit checkpoints from the desktop",
        )
        try:
            payload = json.loads(raw)
            records = payload["checkpoints"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ToolError("Checkpoint listing returned an invalid result.") from exc
        if not isinstance(records, list) or len(records) > 20:
            raise ToolError("Checkpoint listing returned an invalid result.")
        checkpoints = []
        for raw_record in records:
            try:
                checkpoint = WorkspaceCheckpoint(
                    int(raw_record["id"]),
                    str(raw_record["path"]),
                    str(raw_record["original_sha256"]).casefold(),
                    str(raw_record["updated_sha256"]).casefold(),
                    str(raw_record["created_at"])[:50],
                    bool(raw_record["restored"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ToolError("Checkpoint listing returned an invalid result.") from exc
            if (
                checkpoint.id < 1
                or len(checkpoint.original_sha256) != 64
                or len(checkpoint.updated_sha256) != 64
            ):
                raise ToolError("Checkpoint listing returned an invalid result.")
            checkpoints.append(checkpoint)
        self.reviewed_checkpoints = {checkpoint.id: checkpoint for checkpoint in checkpoints}
        return tuple(checkpoints)

    def rollback_checkpoint(self, checkpoint_id: int) -> WorkspaceRollbackResult:
        checkpoint = self.reviewed_checkpoints.get(checkpoint_id)
        if checkpoint is None:
            raise ToolError("List checkpoints before selecting one for rollback.")
        if checkpoint.restored:
            raise ToolError("The selected checkpoint is already restored.")
        raw = self.registry.execute(
            "rollback_text_edit",
            {"checkpoint_id": checkpoint_id},
            user_request=f"Restore reviewed checkpoint #{checkpoint_id} for {checkpoint.path}",
        )
        try:
            payload = json.loads(raw)
            result = WorkspaceRollbackResult(
                int(payload["checkpoint_id"]),
                str(payload["path"]),
                str(payload["restored_sha256"]).casefold(),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ToolError("Checkpoint rollback returned an invalid result.") from exc
        if (
            result.checkpoint_id != checkpoint.id
            or result.path != checkpoint.path
            or result.restored_sha256 != checkpoint.original_sha256
        ):
            raise ToolError("Checkpoint rollback returned an invalid result.")
        self.reviewed_checkpoints[checkpoint_id] = WorkspaceCheckpoint(
            checkpoint.id,
            checkpoint.path,
            checkpoint.original_sha256,
            checkpoint.updated_sha256,
            checkpoint.created_at,
            True,
        )
        return result

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
