import json

import pytest

from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry
from ato.tools.github import GitHubClient


class PullRequester:
    def __init__(self) -> None:
        self.requests = []

    def __call__(self, request, timeout: int) -> bytes:
        self.requests.append((request, timeout))
        return json.dumps(
            {
                "number": 12,
                "title": "Add feature",
                "state": "open",
                "draft": True,
                "html_url": "https://github.com/sam/Ato/pull/12",
            }
        ).encode()


def _arguments(preview: dict) -> dict:
    return {
        "base": preview["base"],
        "head": preview["head"],
        "title": preview["title"],
        "body": preview["body"],
        "draft": preview["draft"],
        "expected_repository": preview["repository"],
        "expected_sha256": preview["pull_request_sha256"],
    }


def test_preview_is_local_and_create_posts_exact_pull_request(tmp_path) -> None:
    requester = PullRequester()
    client = GitHubClient("sam/Ato", "token", requester)
    registry = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        github_client=client,
    )
    preview = json.loads(
        registry.execute(
            "preview_github_pull_request",
            {
                "base": "main",
                "head": "codex/feature",
                "title": "  Add feature  ",
                "body": "Details",
                "draft": True,
            },
        )
    )
    assert requester.requests == []

    result = json.loads(registry.execute("create_github_pull_request", _arguments(preview)))

    request, timeout = requester.requests[0]
    assert request.method == "POST"
    assert request.full_url == "https://api.github.com/repos/sam/Ato/pulls"
    assert json.loads(request.data) == {
        "base": "main",
        "head": "codex/feature",
        "title": "Add feature",
        "body": "Details",
        "draft": True,
    }
    assert timeout == 15
    assert result["number"] == 12


def test_pull_request_rejects_stale_content_missing_token_and_duplicates() -> None:
    requester = PullRequester()
    client = GitHubClient("sam/Ato", "token", requester)
    preview = client.preview_pull_request("main", "feature", "Change", "", False)
    arguments = ("main", "feature", "Change", "", False, "sam/Ato", preview["pull_request_sha256"])

    with pytest.raises(ToolError, match="differs from the reviewed preview"):
        client.create_pull_request(
            "main", "other", "Change", "", False, "sam/Ato", preview["pull_request_sha256"]
        )
    client.create_pull_request(*arguments)
    with pytest.raises(ToolError, match="already submitted"):
        client.create_pull_request(*arguments)
    assert len(requester.requests) == 1

    without_token = GitHubClient("sam/Ato", requester=PullRequester())
    with pytest.raises(ToolError, match="requires GITHUB_TOKEN"):
        without_token.create_pull_request(*arguments)


def test_pull_request_requires_high_confirmation_before_network(tmp_path) -> None:
    requester = PullRequester()
    client = GitHubClient("sam/Ato", "token", requester)
    registry = build_phase3_registry(tmp_path, github_client=client)
    preview = json.loads(
        registry.execute(
            "preview_github_pull_request",
            {"base": "main", "head": "feature", "title": "Change"},
        )
    )
    with pytest.raises(ToolError, match="Permission denied"):
        registry.execute("create_github_pull_request", _arguments(preview))
    assert requester.requests == []


@pytest.mark.parametrize(
    ("base", "head", "message"),
    [
        ("main", "main", "must differ"),
        ("main", "../feature", "safe branch"),
        ("main", "fork:feature", "safe branch"),
        ("main", "feature.lock", "safe branch"),
    ],
)
def test_pull_request_rejects_same_or_unsafe_branches(base, head, message) -> None:
    client = GitHubClient("sam/Ato", "token", PullRequester())
    with pytest.raises(ToolError, match=message):
        client.preview_pull_request(base, head, "Change", "", False)
