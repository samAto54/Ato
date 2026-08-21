import json

import pytest

from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry
from ato.tools.github import GitHubClient


class CommentRequester:
    def __init__(self) -> None:
        self.requests = []

    def __call__(self, request, timeout: int) -> bytes:
        self.requests.append((request, timeout))
        return json.dumps(
            {"id": 99, "html_url": "https://github.com/sam/Ato/issues/7#issuecomment-99"}
        ).encode()


def _arguments(preview: dict) -> dict:
    return {
        "issue_number": preview["issue_number"],
        "body": preview["body"],
        "expected_repository": preview["repository"],
        "expected_sha256": preview["comment_sha256"],
    }


def test_preview_is_local_and_create_posts_to_exact_issue(tmp_path) -> None:
    requester = CommentRequester()
    client = GitHubClient("sam/Ato", "token", requester)
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        github_client=client,
    )

    preview = json.loads(
        registry.execute(
            "preview_github_comment", {"issue_number": 7, "body": "  Looks good.  "}
        )
    )
    assert requester.requests == []
    result = json.loads(registry.execute("create_github_comment", _arguments(preview)))

    request, timeout = requester.requests[0]
    assert request.method == "POST"
    assert request.full_url == "https://api.github.com/repos/sam/Ato/issues/7/comments"
    assert json.loads(request.data) == {"body": "Looks good."}
    assert timeout == 15
    assert result["id"] == 99
    assert result["issue_number"] == 7


def test_comment_rejects_stale_target_repository_and_missing_token() -> None:
    client = GitHubClient("sam/Ato", "token", CommentRequester())
    preview = client.preview_comment(7, "Comment")

    with pytest.raises(ToolError, match="differs from the reviewed preview"):
        client.create_comment(8, "Comment", "sam/Ato", preview["comment_sha256"])
    with pytest.raises(ToolError, match="reviewed repository"):
        client.create_comment(7, "Comment", "other/repo", preview["comment_sha256"])
    without_token = GitHubClient("sam/Ato", requester=CommentRequester())
    with pytest.raises(ToolError, match="requires GITHUB_TOKEN"):
        without_token.create_comment(7, "Comment", "sam/Ato", preview["comment_sha256"])


def test_exact_comment_cannot_be_submitted_twice() -> None:
    requester = CommentRequester()
    client = GitHubClient("sam/Ato", "token", requester)
    preview = client.preview_comment(7, "Comment")
    arguments = (7, "Comment", "sam/Ato", preview["comment_sha256"])

    client.create_comment(*arguments)
    with pytest.raises(ToolError, match="already submitted"):
        client.create_comment(*arguments)
    assert len(requester.requests) == 1


def test_comment_requires_high_confirmation_before_network(tmp_path) -> None:
    requester = CommentRequester()
    client = GitHubClient("sam/Ato", "token", requester)
    registry = build_phase3_registry(tmp_path, github_client=client)
    preview = json.loads(
        registry.execute("preview_github_comment", {"issue_number": 1, "body": "Comment"})
    )

    with pytest.raises(ToolError, match="Permission denied"):
        registry.execute("create_github_comment", _arguments(preview))
    assert requester.requests == []


@pytest.mark.parametrize(
    ("issue_number", "body", "message"),
    [(0, "Comment", "positive"), (1, "   ", "non-whitespace"), (1, "x" * 10_001, "10,000")],
)
def test_comment_preview_validates_target_and_body(issue_number, body, message) -> None:
    client = GitHubClient("sam/Ato", "token", CommentRequester())
    with pytest.raises(ToolError, match=message):
        client.preview_comment(issue_number, body)
