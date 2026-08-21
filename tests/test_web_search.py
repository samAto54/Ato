import json

import pytest

from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry
from ato.tools.search import BraveSearchClient, TavilySearchClient


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

    def request(
        self,
        method: str,
        path: str,
        body: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.requests.append((method, path, body, headers or {}))

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
    method, path, body, headers = connection.requests[0]
    assert method == "GET"
    assert body is None
    assert "q=Ato+AI+agent" in path
    assert headers["X-Subscription-Token"] == "secret-key"
    assert "secret-key" not in json.dumps(result)
    assert connection.closed is True


def test_tavily_search_uses_basic_mode_and_returns_untrusted_results() -> None:
    connection = FakeConnection(
        FakeResponse(
            200,
            {
                "results": [
                    {
                        "title": "Tavily result",
                        "url": "https://example.com/source",
                        "content": "Bounded source content",
                    }
                ],
                "usage": {"credits": 1},
            },
        )
    )
    client = TavilySearchClient(
        "tvly-secret", connection_factory=lambda *args, **kwargs: connection
    )

    result = json.loads(client.search("latest Ato project", count=3))

    assert result["provider"] == "tavily"
    assert result["content_trust"] == "untrusted_external"
    assert result["results"][0]["description"] == "Bounded source content"
    method, path, body, headers = connection.requests[0]
    assert method == "POST" and path == "/search"
    request = json.loads(body)
    assert request == {
        "query": "latest Ato project",
        "search_depth": "basic",
        "max_results": 3,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }
    assert headers["Authorization"] == "Bearer tvly-secret"
    assert "tvly-secret" not in json.dumps(result)
    assert connection.closed is True


@pytest.mark.parametrize("status", [401, 429, 500])
def test_brave_search_reports_provider_failures_safely(status: int) -> None:
    connection = FakeConnection(FakeResponse(status, {}))
    client = BraveSearchClient("key", connection_factory=lambda *args, **kwargs: connection)

    with pytest.raises(ToolError):
        client.search("Ato")
    assert connection.closed is True


@pytest.mark.parametrize("status", [401, 429, 500])
def test_tavily_search_reports_provider_failures_safely(status: int) -> None:
    connection = FakeConnection(FakeResponse(status, {}))
    client = TavilySearchClient("key", connection_factory=lambda *args, **kwargs: connection)

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
