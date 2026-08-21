"""Bounded access to one configured GitHub repository."""

from __future__ import annotations

import base64
import hashlib
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


MAX_GITHUB_ISSUE_TITLE_CHARS = 200
MAX_GITHUB_ISSUE_BODY_CHARS = 10_000
MAX_GITHUB_ISSUE_LABELS = 10
MAX_GITHUB_COMMENT_CHARS = 10_000
MAX_GITHUB_PULL_TITLE_CHARS = 200
MAX_GITHUB_PULL_BODY_CHARS = 10_000


class GitHubClient:
    """Access a fixed repository through a small allowlisted API surface."""

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
        self._submitted_issue_fingerprints: set[str] = set()
        self._submitted_comment_fingerprints: set[str] = set()
        self._submitted_pull_fingerprints: set[str] = set()

    @property
    def can_write(self) -> bool:
        return bool(self._token)

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

    def preview_issue(self, title: str, body: str, labels: list[str]) -> dict[str, Any]:
        normalized_title, normalized_body, normalized_labels = _validate_issue(title, body, labels)
        fingerprint = _issue_fingerprint(
            self.repository, normalized_title, normalized_body, normalized_labels
        )
        return {
            "repository": self.repository,
            "title": normalized_title,
            "body": normalized_body,
            "labels": normalized_labels,
            "issue_sha256": fingerprint,
        }

    def create_issue(
        self,
        title: str,
        body: str,
        labels: list[str],
        expected_repository: str,
        expected_sha256: str,
    ) -> dict[str, Any]:
        if not self._token:
            raise ToolError("GitHub issue creation requires GITHUB_TOKEN.")
        preview = self.preview_issue(title, body, labels)
        if expected_repository != self.repository:
            raise ToolError("Configured GitHub repository differs from the reviewed repository.")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
            raise ToolError("expected_sha256 must be a 64-character SHA-256 digest.")
        fingerprint = preview["issue_sha256"]
        if expected_sha256.casefold() != fingerprint:
            raise ToolError("GitHub issue content differs from the reviewed preview.")
        if fingerprint in self._submitted_issue_fingerprints:
            raise ToolError("This exact GitHub issue was already submitted during this session.")
        payload = self._request(
            f"/repos/{self.repository}/issues",
            method="POST",
            body={"title": preview["title"], "body": preview["body"], "labels": preview["labels"]},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("number"), int):
            raise ToolError("GitHub returned an unexpected issue-creation response.")
        self._submitted_issue_fingerprints.add(fingerprint)
        return {
            "number": payload["number"],
            "title": payload.get("title"),
            "state": payload.get("state"),
            "html_url": payload.get("html_url"),
            "repository": self.repository,
            "issue_sha256": fingerprint,
        }

    def preview_comment(self, issue_number: int, body: str) -> dict[str, Any]:
        normalized_body = _validate_comment(issue_number, body)
        fingerprint = _comment_fingerprint(self.repository, issue_number, normalized_body)
        return {
            "repository": self.repository,
            "issue_number": issue_number,
            "body": normalized_body,
            "comment_sha256": fingerprint,
        }

    def create_comment(
        self,
        issue_number: int,
        body: str,
        expected_repository: str,
        expected_sha256: str,
    ) -> dict[str, Any]:
        if not self._token:
            raise ToolError("GitHub issue comment creation requires GITHUB_TOKEN.")
        preview = self.preview_comment(issue_number, body)
        if expected_repository != self.repository:
            raise ToolError("Configured GitHub repository differs from the reviewed repository.")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
            raise ToolError("expected_sha256 must be a 64-character SHA-256 digest.")
        fingerprint = preview["comment_sha256"]
        if expected_sha256.casefold() != fingerprint:
            raise ToolError("GitHub comment differs from the reviewed preview.")
        if fingerprint in self._submitted_comment_fingerprints:
            raise ToolError("This exact GitHub comment was already submitted during this session.")
        payload = self._request(
            f"/repos/{self.repository}/issues/{issue_number}/comments",
            method="POST",
            body={"body": preview["body"]},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), int):
            raise ToolError("GitHub returned an unexpected comment-creation response.")
        self._submitted_comment_fingerprints.add(fingerprint)
        return {
            "id": payload["id"],
            "issue_number": issue_number,
            "html_url": payload.get("html_url"),
            "repository": self.repository,
            "comment_sha256": fingerprint,
        }

    def preview_pull_request(
        self, base: str, head: str, title: str, body: str, draft: bool
    ) -> dict[str, Any]:
        base, head, title, body = _validate_pull_request(base, head, title, body)
        fingerprint = _pull_fingerprint(self.repository, base, head, title, body, draft)
        return {
            "repository": self.repository,
            "base": base,
            "head": head,
            "title": title,
            "body": body,
            "draft": draft,
            "pull_request_sha256": fingerprint,
        }

    def create_pull_request(
        self,
        base: str,
        head: str,
        title: str,
        body: str,
        draft: bool,
        expected_repository: str,
        expected_sha256: str,
    ) -> dict[str, Any]:
        if not self._token:
            raise ToolError("GitHub pull-request creation requires GITHUB_TOKEN.")
        preview = self.preview_pull_request(base, head, title, body, draft)
        if expected_repository != self.repository:
            raise ToolError("Configured GitHub repository differs from the reviewed repository.")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
            raise ToolError("expected_sha256 must be a 64-character SHA-256 digest.")
        fingerprint = preview["pull_request_sha256"]
        if expected_sha256.casefold() != fingerprint:
            raise ToolError("GitHub pull request differs from the reviewed preview.")
        if fingerprint in self._submitted_pull_fingerprints:
            raise ToolError("This exact pull request was already submitted during this session.")
        payload = self._request(
            f"/repos/{self.repository}/pulls",
            method="POST",
            body={
                "base": preview["base"],
                "head": preview["head"],
                "title": preview["title"],
                "body": preview["body"],
                "draft": preview["draft"],
            },
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("number"), int):
            raise ToolError("GitHub returned an unexpected pull-request response.")
        self._submitted_pull_fingerprints.add(fingerprint)
        return {
            "number": payload["number"],
            "title": payload.get("title"),
            "state": payload.get("state"),
            "draft": payload.get("draft"),
            "html_url": payload.get("html_url"),
            "repository": self.repository,
            "pull_request_sha256": fingerprint,
        }

    def _get(self, path: str, query: dict[str, str] | None = None) -> Any:
        return self._request(path, query=query)

    def _request(
        self,
        path: str,
        query: dict[str, str] | None = None,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> Any:
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
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(url, headers=headers, data=data, method=method)
        try:
            raw = self._requester(request, GITHUB_TIMEOUT_SECONDS)
            payload = json.loads(raw)
        except ToolError:
            raise
        except (OSError, ValueError) as exc:
            raise ToolError("GitHub returned an unreadable response.") from exc
        return payload


def _validate_issue(title: str, body: str, labels: list[str]) -> tuple[str, str, list[str]]:
    normalized_title = title.strip()
    if not normalized_title or len(normalized_title) > MAX_GITHUB_ISSUE_TITLE_CHARS:
        raise ToolError("GitHub issue title must be 1-200 non-whitespace characters.")
    if len(body) > MAX_GITHUB_ISSUE_BODY_CHARS:
        raise ToolError("GitHub issue body exceeds the 10,000-character limit.")
    if len(labels) > MAX_GITHUB_ISSUE_LABELS:
        raise ToolError("GitHub issue accepts at most 10 labels.")
    normalized_labels: list[str] = []
    for label in labels:
        normalized = label.strip()
        if not normalized or len(normalized) > 50:
            raise ToolError("GitHub issue labels must be 1-50 non-whitespace characters.")
        if normalized.casefold() in {existing.casefold() for existing in normalized_labels}:
            raise ToolError("GitHub issue labels cannot contain duplicates.")
        normalized_labels.append(normalized)
    return normalized_title, body, normalized_labels


def _issue_fingerprint(repository: str, title: str, body: str, labels: list[str]) -> str:
    contract = json.dumps(
        {"repository": repository, "title": title, "body": body, "labels": labels},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(contract).hexdigest()


def _validate_comment(issue_number: int, body: str) -> str:
    if issue_number < 1:
        raise ToolError("GitHub issue number must be positive.")
    normalized = body.strip()
    if not normalized or len(normalized) > MAX_GITHUB_COMMENT_CHARS:
        raise ToolError("GitHub comment must be 1-10,000 non-whitespace characters.")
    return normalized


def _comment_fingerprint(repository: str, issue_number: int, body: str) -> str:
    contract = json.dumps(
        {"repository": repository, "issue_number": issue_number, "body": body},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(contract).hexdigest()


def _validate_pull_request(
    base: str, head: str, title: str, body: str
) -> tuple[str, str, str, str]:
    normalized_base = _validate_branch(base, "base")
    normalized_head = _validate_branch(head, "head")
    if normalized_base == normalized_head:
        raise ToolError("GitHub pull-request base and head branches must differ.")
    normalized_title = title.strip()
    if not normalized_title or len(normalized_title) > MAX_GITHUB_PULL_TITLE_CHARS:
        raise ToolError("GitHub pull-request title must be 1-200 non-whitespace characters.")
    if len(body) > MAX_GITHUB_PULL_BODY_CHARS:
        raise ToolError("GitHub pull-request body exceeds the 10,000-character limit.")
    return normalized_base, normalized_head, normalized_title, body


def _validate_branch(branch: str, field: str) -> str:
    normalized = branch.strip()
    valid_shape = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", normalized)
    segments = normalized.split("/")
    if (
        not valid_shape
        or ".." in normalized
        or "//" in normalized
        or normalized.endswith(("/", "."))
        or any(segment.startswith(".") or segment.endswith(".lock") for segment in segments)
    ):
        raise ToolError(f"GitHub pull-request {field} branch is not a safe branch name.")
    return normalized


def _pull_fingerprint(
    repository: str, base: str, head: str, title: str, body: str, draft: bool
) -> str:
    contract = json.dumps(
        {
            "repository": repository,
            "base": base,
            "head": head,
            "title": title,
            "body": body,
            "draft": draft,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(contract).hexdigest()


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
