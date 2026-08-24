import json

import pytest

from ato.exceptions import ToolError
from ato.security import AuditLogger, PermissionManager
from ato.ui.research import DesktopResearchFetch, DesktopResearchSearch


class Searcher:
    def __init__(self, payload=None) -> None:
        self.calls = []
        self.payload = payload or {
            "provider": "tavily",
            "content_trust": "untrusted_external",
            "results": [
                {
                    "title": "Ato result",
                    "url": "https://example.com/source",
                    "description": "External snippet",
                }
            ],
        }

    def search(self, query, count=5):
        self.calls.append((query, count))
        return json.dumps(self.payload)


def test_desktop_research_confirms_search_and_returns_bounded_sources(tmp_path) -> None:
    searcher = Searcher()
    requests = []
    audit_path = tmp_path / "audit.jsonl"
    service = DesktopResearchSearch(
        searcher,
        PermissionManager(lambda request: requests.append(request) or True),
        AuditLogger(audit_path),
    )
    result = service.search("  Ato   agent  ")
    assert searcher.calls == [("Ato agent", 5)]
    assert requests[0].level.value == "MEDIUM"
    assert result.provider == "tavily"
    assert result.lines == (
        "RESULT 1\nAto result\nhttps://example.com/source\nExternal snippet",
    )
    event = json.loads(audit_path.read_text(encoding="utf-8"))
    assert event["tool"] == "web_search"
    assert event["decision"] == "ALLOW"


def test_desktop_research_denial_never_accesses_provider(tmp_path) -> None:
    searcher = Searcher()
    service = DesktopResearchSearch(
        searcher,
        PermissionManager(lambda request: False),
        AuditLogger(tmp_path / "audit.jsonl"),
    )
    with pytest.raises(ToolError, match="Permission denied"):
        service.search("Ato")
    assert searcher.calls == []


@pytest.mark.parametrize("query", ["", "word " * 51, "x" * 401])
def test_desktop_research_rejects_invalid_query_before_permission(tmp_path, query) -> None:
    searcher = Searcher()
    requests = []
    service = DesktopResearchSearch(
        searcher,
        PermissionManager(lambda request: requests.append(request) or True),
        AuditLogger(tmp_path / "audit.jsonl"),
    )
    with pytest.raises(ToolError, match="query"):
        service.search(query)
    assert requests == []
    assert searcher.calls == []


def test_desktop_research_rejects_result_without_untrusted_label(tmp_path) -> None:
    searcher = Searcher({"provider": "bad", "content_trust": "trusted", "results": []})
    service = DesktopResearchSearch(
        searcher,
        PermissionManager(lambda request: True),
        AuditLogger(tmp_path / "audit.jsonl"),
    )
    with pytest.raises(ToolError, match="invalid result"):
        service.search("Ato")


def test_desktop_research_fetch_confirms_exact_source_and_bounds_text(tmp_path) -> None:
    calls = []

    def fetcher(url):
        calls.append(url)
        return json.dumps(
            {
                "source_url": url,
                "content_trust": "untrusted_external",
                "title": "Source page",
                "text": "evidence " * 3_000,
                "document_type": "webpage",
                "truncated": False,
            }
        )

    service = DesktopResearchFetch(
        fetcher,
        PermissionManager(lambda request: True),
        AuditLogger(tmp_path / "audit.jsonl"),
    )
    page = service.fetch("https://example.com/source")
    assert calls == ["https://example.com/source"]
    assert page.source_url == "https://example.com/source"
    assert len(page.text) == 20_000
    assert page.truncated is True


def test_desktop_research_fetch_denial_prevents_network(tmp_path) -> None:
    calls = []
    service = DesktopResearchFetch(
        lambda url: calls.append(url) or "{}",
        PermissionManager(lambda request: False),
        AuditLogger(tmp_path / "audit.jsonl"),
    )
    with pytest.raises(ToolError, match="Permission denied"):
        service.fetch("https://example.com/source")
    assert calls == []


@pytest.mark.parametrize("url", ["", "http://example.com", "https://user:pass@example.com"])
def test_desktop_research_fetch_rejects_unsafe_url_before_permission(tmp_path, url) -> None:
    requests = []
    service = DesktopResearchFetch(
        lambda value: pytest.fail("unsafe URL must not be fetched"),
        PermissionManager(lambda request: requests.append(request) or True),
        AuditLogger(tmp_path / "audit.jsonl"),
    )
    with pytest.raises(ToolError, match="HTTPS"):
        service.fetch(url)
    assert requests == []
