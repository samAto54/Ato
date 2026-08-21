import json

import pytest

from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry
from ato.tools.research import WebResearchCoordinator


class FakeSearcher:
    def __init__(self, results: list[dict[str, str]]) -> None:
        self.results = results
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, count: int = 5) -> str:
        self.calls.append((query, count))
        return json.dumps(
            {
                "query": query,
                "provider": "fake",
                "content_trust": "untrusted_external",
                "results": self.results,
            }
        )


def _page(url: str, text: str = "Evidence") -> str:
    return json.dumps(
        {
            "source_url": url,
            "title": "Fetched title",
            "text": text,
            "truncated": False,
            "document_type": "webpage",
            "pages": None,
        }
    )


def test_research_coordinator_deduplicates_diversifies_and_bounds_sources() -> None:
    searcher = FakeSearcher(
        [
            {"title": "One", "url": "https://a.example/one#section", "description": "A"},
            {"title": "Duplicate", "url": "https://a.example/one", "description": "same"},
            {"title": "Two", "url": "https://a.example/two", "description": "B"},
            {"title": "Host cap", "url": "https://a.example/three", "description": "C"},
            {"title": "Unsafe", "url": "http://b.example/plain", "description": "skip"},
            {"title": "Three", "url": "https://c.example/three", "description": "D"},
        ]
    )
    fetched: list[str] = []

    def fetcher(url: str) -> str:
        fetched.append(url)
        return _page(url, "x" * 3_000)

    result = json.loads(WebResearchCoordinator(searcher, fetcher).research("Ato", 3))

    assert searcher.calls == [("Ato", 9)]
    assert fetched == [
        "https://a.example/one",
        "https://a.example/two",
        "https://c.example/three",
    ]
    assert len(result["sources"]) == 3
    assert all(len(source["text"]) == 2_500 for source in result["sources"])
    assert all(source["text_truncated"] is True for source in result["sources"])
    assert result["content_trust"] == "untrusted_external"


def test_research_coordinator_reports_source_failures_without_losing_evidence() -> None:
    searcher = FakeSearcher(
        [
            {"title": "Good", "url": "https://good.example/", "description": "works"},
            {"title": "Bad", "url": "https://bad.example/", "description": "fails"},
        ]
    )

    def fetcher(url: str) -> str:
        if "bad.example" in url:
            raise ToolError("Source rejected safely.")
        return _page(url)

    result = json.loads(WebResearchCoordinator(searcher, fetcher).research("topic", 2))

    assert [source["source_url"] for source in result["sources"]] == ["https://good.example/"]
    assert result["failures"] == [
        {"source_url": "https://bad.example/", "error": "Source rejected safely."}
    ]


@pytest.mark.parametrize("max_sources", [0, 4])
def test_research_coordinator_rejects_invalid_limits_before_search(max_sources: int) -> None:
    searcher = FakeSearcher([])

    with pytest.raises(ToolError, match="between 1 and 3"):
        WebResearchCoordinator(searcher, _page).research("topic", max_sources)
    assert searcher.calls == []


def test_research_tool_is_optional_and_requires_one_medium_confirmation(tmp_path) -> None:
    searcher = FakeSearcher(
        [{"title": "One", "url": "https://one.example/", "description": "snippet"}]
    )
    disabled = build_phase3_registry(tmp_path)
    names = {definition["function"]["name"] for definition in disabled.api_definitions()}
    assert "research_web" not in names

    denied = build_phase3_registry(tmp_path, web_searcher=searcher, web_fetcher=_page)
    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute("research_web", {"query": "Ato", "max_sources": 1})
    assert searcher.calls == []

    confirmations = []
    allowed = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: not confirmations.append(request)),
        web_searcher=searcher,
        web_fetcher=_page,
    )
    result = json.loads(
        allowed.execute("research_web", {"query": "Ato", "max_sources": 1})
    )

    assert confirmations[0].level.value == "MEDIUM"
    assert len(confirmations) == 1
    assert result["sources"][0]["source_url"] == "https://one.example/"
