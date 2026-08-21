import base64
import json
import urllib.parse

import pytest

from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry
from ato.tools.github import GitHubClient


class FakeRequester:
    def __init__(self, payloads) -> None:
        self.payloads = iter(payloads)
        self.requests = []

    def __call__(self, request, timeout: int) -> bytes:
        self.requests.append((request, timeout))
        return json.dumps(next(self.payloads)).encode("utf-8")


def test_repository_metadata_uses_fixed_host_and_token_header() -> None:
    requester = FakeRequester(
        [
            {
                "full_name": "sam/Ato",
                "description": "agent",
                "private": False,
                "default_branch": "main",
                "html_url": "https://github.com/sam/Ato",
                "open_issues_count": 2,
                "updated_at": "2026-08-21T00:00:00Z",
                "ignored": "not returned",
            }
        ]
    )
    client = GitHubClient("sam/Ato", "secret-token", requester)

    result = client.repository_metadata()

    request, timeout = requester.requests[0]
    assert request.full_url == "https://api.github.com/repos/sam/Ato"
    assert request.get_header("Authorization") == "Bearer secret-token"
    assert timeout == 15
    assert "ignored" not in result


def test_issue_and_pull_request_lists_are_bounded_and_separate() -> None:
    requester = FakeRequester(
        [
            [
                {"number": 1, "title": "Issue", "state": "open", "labels": []},
                {"number": 2, "title": "PR", "state": "open", "pull_request": {}},
            ],
            [{"number": 3, "title": "Change", "state": "open", "draft": False}],
        ]
    )
    client = GitHubClient("sam/Ato", requester=requester)

    issues = client.list_issues("open", 5)
    pulls = client.list_pull_requests("all", 5)

    assert [item["number"] for item in issues] == [1]
    assert [item["number"] for item in pulls] == [3]
    issue_url = requester.requests[0][0].full_url
    issue_query = urllib.parse.parse_qs(urllib.parse.urlsplit(issue_url).query)
    assert issue_query == {"state": ["open"], "per_page": ["5"]}


def test_file_read_decodes_bounded_utf8_and_encodes_path_and_ref() -> None:
    requester = FakeRequester(
        [
            {
                "type": "file",
                "path": "docs/my file.md",
                "sha": "abc",
                "encoding": "base64",
                "content": base64.b64encode(b"hello\n").decode("ascii"),
            }
        ]
    )
    client = GitHubClient("sam/Ato", requester=requester)

    result = client.read_file("docs/my file.md", "feature/test")

    url = requester.requests[0][0].full_url
    assert "/contents/docs/my%20file.md" in url
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(url).query) == {"ref": ["feature/test"]}
    assert result["content"] == "hello\n"


def test_github_tool_requires_permission_and_rejects_operation_field_mixing(tmp_path) -> None:
    client = GitHubClient("sam/Ato", requester=FakeRequester([]))
    denied = build_phase3_registry(tmp_path, github_client=client)
    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute("github_read", {"operation": "repository"})

    approved = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        github_client=client,
    )
    with pytest.raises(ToolError, match="does not accept path"):
        approved.execute("github_read", {"operation": "issues", "path": "README.md"})


@pytest.mark.parametrize("repository", ["missing-slash", "a/b/c", "../owner/repo", "a b/repo"])
def test_repository_name_is_strictly_validated(repository) -> None:
    with pytest.raises(ValueError, match="owner/name"):
        GitHubClient(repository)


def test_github_file_rejects_traversal_and_binary_content() -> None:
    client = GitHubClient("sam/Ato", requester=FakeRequester([]))
    with pytest.raises(ToolError, match="normalized"):
        client.read_file("../secret")

    binary = FakeRequester(
        [{"type": "file", "encoding": "base64", "content": base64.b64encode(b"\xff").decode()}]
    )
    client = GitHubClient("sam/Ato", requester=binary)
    with pytest.raises(ToolError, match="UTF-8"):
        client.read_file("asset.bin")
