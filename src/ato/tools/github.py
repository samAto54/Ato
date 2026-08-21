"""Bounded read-only access to one configured GitHub repository."""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from ato.exceptions import ToolError

GITHUB_API_ROOT = "https://api.github.com"
MAX_GITHUB_RESPONSE_BYTES = 1_000_000
MAX_GITHUB_FILE_BYTES = 100_000
MAX_GITHUB_ITEMS = 20
GITHUB_TIMEOUT_SECONDS = 15
RepositoryRequester = Callable[[urllib.request.Request, int], bytes]


class GitHubReadClient:
    """Read a fixed repository through a small allowlisted API surface."""

    def __init__(
        self,
        repository: str,
        token: str | None = None,
        requester: RepositoryRequester | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("GitHub repository must use the owner/name format.")
        self.repository = repository
        self._token = token
        self._requester = requester or _request_bytes

    def repository_metadata(self) -> dict[str, Any]:
        payload = self._get(f"/repos/{self.repository}")
        return {
            "full_name": payload.get("full_name"),
            "description": payload.get("description"),
            "private": payload.get("private"),
            "default_branch": payload.get("default_branch"),
            "html_url": payload.get("html_url"),
            "open_issues_count": payload.get("open_issues_count"),
            "updated_at": payload.get("updated_at"),
        }

    def list_issues(self, state: str, limit: int) -> list[dict[str, Any]]:
        payload = self._get(
            f"/repos/{self.repository}/issues",
            {"state": state, "per_page": str(limit)},
        )
        return [
            _issue_summary(item)
            for item in _require_list(payload)
            if "pull_request" not in item
        ][:limit]

    def list_pull_requests(self, state: str, limit: int) -> list[dict[str, Any]]:
        payload = self._get(
            f"/repos/{self.repository}/pulls",
            {"state": state, "per_page": str(limit)},
        )
        return [_pull_summary(item) for item in _require_list(payload)][:limit]

    def list_commits(self, limit: int) -> list[dict[str, Any]]:
        payload = self._get(
            f"/repos/{self.repository}/commits",
            {"per_page": str(limit)},
        )
        return [_commit_summary(item) for item in _require_list(payload)][:limit]

    def read_file(self, path: str, ref: str | None = None) -> dict[str, Any]:
        normalized = path.replace("\\", "/").strip("/")
        if not normalized or any(part in {"", ".", ".."} for part in normalized.split("/")):
            raise ToolError("GitHub file path must be a normalized repository-relative path.")
        encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in normalized.split("/"))
        query = {"ref": ref} if ref else None
        payload = self._get(f"/repos/{self.repository}/contents/{encoded_path}", query)
        if not isinstance(payload, dict) or payload.get("type") != "file":
            raise ToolError("The requested GitHub path is not a file.")
        if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
            raise ToolError("GitHub did not return supported file content.")
        try:
            content = base64.b64decode(payload["content"], validate=True)
        except (ValueError, TypeError) as exc:
            raise ToolError("GitHub returned invalid file content.") from exc
        if len(content) > MAX_GITHUB_FILE_BYTES:
            raise ToolError("GitHub file exceeds the read limit.")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("GitHub file is not readable UTF-8 text.") from exc
        return {
            "path": payload.get("path", normalized),
            "sha": payload.get("sha"),
            "html_url": payload.get("html_url"),
            "content": text,
            "truncated": False,
        }

    def _get(self, path: str, query: dict[str, str] | None = None) -> Any:
        url = f"{GITHUB_API_ROOT}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Ato-Agent",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            raw = self._requester(request, GITHUB_TIMEOUT_SECONDS)
            payload = json.loads(raw)
        except ToolError:
            raise
        except (OSError, ValueError) as exc:
            raise ToolError("GitHub returned an unreadable response.") from exc
        return payload


def _request_bytes(request: urllib.request.Request, timeout: int) -> bytes:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_GITHUB_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ToolError(f"GitHub request failed with HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ToolError("GitHub request failed safely.") from exc
    if len(raw) > MAX_GITHUB_RESPONSE_BYTES:
        raise ToolError("GitHub response exceeds the size limit.")
    return raw


def _require_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ToolError("GitHub returned an unexpected response shape.")
    return payload


def _issue_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "html_url": item.get("html_url"),
        "updated_at": item.get("updated_at"),
        "labels": [
            label.get("name")
            for label in item.get("labels", [])
            if isinstance(label, dict)
        ],
    }


def _pull_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "draft": item.get("draft"),
        "html_url": item.get("html_url"),
        "updated_at": item.get("updated_at"),
    }


def _commit_summary(item: dict[str, Any]) -> dict[str, Any]:
    commit = item.get("commit") if isinstance(item.get("commit"), dict) else {}
    return {
        "sha": item.get("sha"),
        "message": commit.get("message"),
        "html_url": item.get("html_url"),
    }
