import json

import pytest

from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry
from ato.tools.github import GitHubClient


class IssueRequester:
    def __init__(self) -> None:
        self.requests = []

    def __call__(self, request, timeout: int) -> bytes:
        self.requests.append((request, timeout))
        return json.dumps(
            {
                "number": 42,
                "title": "Bug report",
                "state": "open",
                "html_url": "https://github.com/sam/Ato/issues/42",
            }
        ).encode("utf-8")


def _arguments(preview: dict) -> dict:
    return {
        "title": preview["title"],
        "body": preview["body"],
        "labels": preview["labels"],
        "expected_repository": preview["repository"],
        "expected_sha256": preview["issue_sha256"],
    }


def test_preview_is_local_and_create_posts_exact_reviewed_issue(tmp_path) -> None:
    requester = IssueRequester()
    client = GitHubClient("sam/Ato", "token", requester)
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        github_client=client,
    )

    preview = json.loads(
        registry.execute(
            "preview_github_issue",
            {"title": "  Bug report  ", "body": "Details", "labels": ["bug"]},
        )
    )
    assert requester.requests == []

    result = json.loads(registry.execute("create_github_issue", _arguments(preview)))

    request, timeout = requester.requests[0]
    assert request.method == "POST"
    assert request.full_url == "https://api.github.com/repos/sam/Ato/issues"
    assert request.get_header("Authorization") == "Bearer token"
    assert json.loads(request.data) == {
        "title": "Bug report",
        "body": "Details",
        "labels": ["bug"],
    }
    assert timeout == 15
    assert result["number"] == 42
    assert result["issue_sha256"] == preview["issue_sha256"]


def test_create_rejects_stale_content_wrong_repository_and_missing_token(tmp_path) -> None:
    preview_client = GitHubClient("sam/Ato", "token", IssueRequester())
    preview = preview_client.preview_issue("Bug", "Body", [])

    with pytest.raises(ToolError, match="differs from the reviewed preview"):
        preview_client.create_issue(
            "Changed", "Body", [], "sam/Ato", preview["issue_sha256"]
        )
    with pytest.raises(ToolError, match="reviewed repository"):
        preview_client.create_issue(
            "Bug", "Body", [], "someone/else", preview["issue_sha256"]
        )
    without_token = GitHubClient("sam/Ato", requester=IssueRequester())
    with pytest.raises(ToolError, match="requires GITHUB_TOKEN"):
        without_token.create_issue("Bug", "Body", [], "sam/Ato", preview["issue_sha256"])


def test_exact_issue_cannot_be_submitted_twice_in_one_session() -> None:
    requester = IssueRequester()
    client = GitHubClient("sam/Ato", "token", requester)
    preview = client.preview_issue("Bug", "Body", [])
    arguments = (
        preview["title"],
        preview["body"],
        preview["labels"],
        preview["repository"],
        preview["issue_sha256"],
    )

    client.create_issue(*arguments)
    with pytest.raises(ToolError, match="already submitted"):
        client.create_issue(*arguments)

    assert len(requester.requests) == 1


def test_create_requires_high_confirmation_before_network(tmp_path) -> None:
    requester = IssueRequester()
    client = GitHubClient("sam/Ato", "token", requester)
    registry = build_phase3_registry(tmp_path, github_client=client)
    preview = json.loads(
        registry.execute("preview_github_issue", {"title": "Bug"})
    )

    with pytest.raises(ToolError, match="Permission denied"):
        registry.execute("create_github_issue", _arguments(preview))

    assert requester.requests == []


@pytest.mark.parametrize(
    ("title", "body", "labels", "message"),
    [
        ("   ", "", [], "title"),
        ("Bug", "x" * 10_001, [], "body"),
        ("Bug", "", ["x"] * 11, "at most 10"),
        ("Bug", "", ["bug", "BUG"], "duplicates"),
    ],
)
def test_issue_preview_rejects_invalid_bounded_content(title, body, labels, message) -> None:
    client = GitHubClient("sam/Ato", "token", IssueRequester())

    with pytest.raises(ToolError, match=message):
        client.preview_issue(title, body, labels)
