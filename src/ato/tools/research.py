"""Bounded coordination of search results and public-source retrieval."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ato.exceptions import ToolError
from ato.tools.search import WebSearchClient

MAX_RESEARCH_SOURCES = 3
MAX_CANDIDATES = 10
MAX_SOURCES_PER_HOST = 2
MAX_SOURCE_TEXT_CHARS = 2_500
MAX_METADATA_CHARS = 1_000
MAX_FAILURE_CHARS = 300


class WebResearchCoordinator:
    """Search, diversify, and fetch a small set of untrusted public sources."""

    def __init__(self, searcher: WebSearchClient, fetcher: Callable[[str], str]) -> None:
        self._searcher = searcher
        self._fetcher = fetcher

    def research(self, query: str, max_sources: int = 3) -> str:
        if not 1 <= max_sources <= MAX_RESEARCH_SOURCES:
            raise ToolError(f"Research source count must be between 1 and {MAX_RESEARCH_SOURCES}.")
        search_payload = _parse_object(
            self._searcher.search(query, count=min(MAX_CANDIDATES, max_sources * 3)),
            "Web search returned invalid research data.",
        )
        raw_results = search_payload.get("results")
        if not isinstance(raw_results, list):
            raise ToolError("Web search returned invalid research results.")

        candidates = _select_candidates(raw_results, max_sources)
        sources: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        for rank, candidate in enumerate(candidates, start=1):
            url = candidate["url"]
            try:
                page = _parse_object(
                    self._fetcher(url), "Fetched source returned invalid research data."
                )
                source_url = str(page.get("source_url", ""))
                if source_url != url:
                    raise ToolError("Fetched source URL did not match the approved search result.")
                text = str(page.get("text", ""))
                if not text.strip():
                    raise ToolError("Fetched source contained no readable text.")
                sources.append(
                    {
                        "rank": rank,
                        "title": str(page.get("title") or candidate["title"])[
                            :MAX_METADATA_CHARS
                        ],
                        "source_url": source_url,
                        "search_snippet": candidate["description"],
                        "text": text[:MAX_SOURCE_TEXT_CHARS],
                        "text_truncated": bool(page.get("truncated"))
                        or len(text) > MAX_SOURCE_TEXT_CHARS,
                        "document_type": str(page.get("document_type", "webpage")),
                        "pages": page.get("pages"),
                    }
                )
            except ToolError as exc:
                failures.append({"source_url": url, "error": str(exc)[:MAX_FAILURE_CHARS]})

        return json.dumps(
            {
                "query": str(search_payload.get("query", query)),
                "provider": str(search_payload.get("provider", "unknown")),
                "content_trust": "untrusted_external",
                "sources": sources,
                "failures": failures,
                "searched_results": len(raw_results),
                "selected_results": len(candidates),
            }
        )


def _parse_object(value: str, error: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ToolError(error) from exc
    if not isinstance(payload, Mapping):
        raise ToolError(error)
    return payload


def _select_candidates(raw_results: list[Any], limit: int) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    host_counts: Counter[str] = Counter()
    for raw in raw_results[:MAX_CANDIDATES]:
        if not isinstance(raw, Mapping):
            continue
        canonical = _canonical_https_url(str(raw.get("url", "")))
        if canonical is None or canonical in seen_urls:
            continue
        host = urlsplit(canonical).hostname or ""
        if host_counts[host] >= MAX_SOURCES_PER_HOST:
            continue
        seen_urls.add(canonical)
        host_counts[host] += 1
        selected.append(
            {
                "url": canonical,
                "title": str(raw.get("title", ""))[:MAX_METADATA_CHARS],
                "description": str(raw.get("description", ""))[:MAX_METADATA_CHARS],
            }
        )
        if len(selected) >= limit:
            break
    return selected


def _canonical_https_url(value: str) -> str | None:
    parsed = urlsplit(value)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return None
    return urlunsplit(("https", parsed.netloc.casefold(), parsed.path or "/", parsed.query, ""))
