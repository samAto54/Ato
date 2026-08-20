import json

import pytest

from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry
from ato.tools.search import BraveSearchClient


class FakeResponse:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._body = json.dumps(payload).encode()

    def read(self, limit: int) -> bytes:
        return self._body[:limit]


class FakeConnection:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests = []
        self.closed = False

    def request(self, method: str, path: str, headers: dict[str, str]) -> None:
        self.requests.append((method, path, headers))

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


def test_brave_search_returns_bounded_untrusted_results_without_leaking_key() -> None:
    response = FakeResponse(
        200,
        {
            "web": {
                "results": [
                    {
                        "title": "Ato result",
                        "url": "https://example.com/ato",
                        "description": "Research evidence",
                    },
                    {
                        "title": "Unsafe credentials",
                        "url": "https://user:pass@example.com/private",
                        "description": "skip",
                    },
                ]
            }
        },
    )
    connection = FakeConnection(response)
    client = BraveSearchClient("secret-key", connection_factory=lambda *args, **kwargs: connection)

    result = json.loads(client.search("  Ato   AI agent  ", count=2))

    assert result == {
        "query": "Ato AI agent",
        "provider": "brave",
        "content_trust": "untrusted_external",
        "results": [
            {
                "title": "Ato result",
                "url": "https://example.com/ato",
                "description": "Research evidence",
            }
        ],
    }
    method, path, headers = connection.requests[0]
    assert method == "GET"
    assert "q=Ato+AI+agent" in path
    assert headers["X-Subscription-Token"] == "secret-key"
    assert "secret-key" not in json.dumps(result)
    assert connection.closed is True


@pytest.mark.parametrize("status", [401, 429, 500])
def test_brave_search_reports_provider_failures_safely(status: int) -> None:
    connection = FakeConnection(FakeResponse(status, {}))
    client = BraveSearchClient("key", connection_factory=lambda *args, **kwargs: connection)

    with pytest.raises(ToolError):
        client.search("Ato")
    assert connection.closed is True


def test_web_search_tool_is_optional_and_requires_medium_confirmation(tmp_path) -> None:
    class FakeSearcher:
        def __init__(self) -> None:
            self.calls = []

        def search(self, query: str, count: int = 5) -> str:
            self.calls.append((query, count))
            return json.dumps({"query": query, "results": []})

    disabled = build_phase3_registry(tmp_path)
    assert "web_search" not in {
        definition["function"]["name"] for definition in disabled.api_definitions()
    }

    searcher = FakeSearcher()
    denied = build_phase3_registry(tmp_path, web_searcher=searcher)
    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute("web_search", {"query": "Ato"})
    assert searcher.calls == []

    seen = []
    allowed = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: not seen.append(request)),
        web_searcher=searcher,
    )
    allowed.execute("web_search", {"query": "Ato", "count": 3})

    assert seen[0].level.value == "MEDIUM"
    assert searcher.calls == [("Ato", 3)]


@pytest.mark.parametrize("query", ["", "word " * 51, "x" * 401])
def test_brave_search_rejects_invalid_queries_before_network(query: str) -> None:
    client = BraveSearchClient(
        "key",
        connection_factory=lambda *args, **kwargs: pytest.fail("network should not run"),
    )

    with pytest.raises(ToolError):
        client.search(query)
