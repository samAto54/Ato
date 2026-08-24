"""Confirmed, bounded web-search adapter for the desktop Research section."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from ato.exceptions import ToolError
from ato.security import (
    AuditLogger,
    PermissionDecision,
    PermissionLevel,
    PermissionManager,
    PermissionRequest,
)
from ato.tools.search import MAX_SEARCH_QUERY_CHARS, MAX_SEARCH_QUERY_WORDS, WebSearchClient

DESKTOP_SEARCH_RESULTS = 5
MAX_DESKTOP_PAGE_CHARS = 20_000


@dataclass(frozen=True, slots=True)
class ResearchSource:
    title: str
    url: str
    snippet: str

    def display(self) -> str:
        return f"{self.title}\n{self.url}\n{self.snippet}"


@dataclass(frozen=True, slots=True)
class ResearchSearchResult:
    provider: str
    sources: tuple[ResearchSource, ...]

    @property
    def lines(self) -> tuple[str, ...]:
        return tuple(
            f"RESULT {index}\n{source.display()}"
            for index, source in enumerate(self.sources, start=1)
        )


@dataclass(frozen=True, slots=True)
class ResearchPage:
    title: str
    source_url: str
    text: str
    document_type: str
    truncated: bool


@dataclass(slots=True)
class DesktopResearchFetch:
    fetcher: Callable[[str], str]
    permission_manager: PermissionManager
    audit_logger: AuditLogger

    def fetch(self, url: str) -> ResearchPage:
        cleaned = url.strip()
        parsed = urlsplit(cleaned)
        if (
            len(cleaned) > 2_048
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ToolError("Research fetch requires one credential-free HTTPS source URL.")
        arguments = {"url": cleaned}
        self.audit_logger.ensure_ready()
        decision = self.permission_manager.authorize(
            PermissionRequest(
                "fetch_web_page",
                PermissionLevel.MEDIUM,
                arguments,
                "Fetch one exact public HTTPS research result",
            )
        )
        if decision is PermissionDecision.DENY:
            self._audit(arguments, decision, error="User denied permission.")
            raise ToolError("Permission denied for web page fetching.")
        try:
            page = _parse_page(self.fetcher(cleaned), cleaned)
        except ToolError as exc:
            self._audit(arguments, decision, error=str(exc))
            raise
        except Exception as exc:
            self._audit(arguments, decision, error="Web page fetch failed safely.")
            raise ToolError("Web page fetch failed safely.") from exc
        self._audit(arguments, decision, result="Bounded web page fetch completed.")
        return page

    def _audit(
        self,
        arguments: dict[str, object],
        decision: PermissionDecision,
        *,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        self.audit_logger.record(
            user_request="Desktop exact-source fetch",
            tool_name="fetch_web_page",
            arguments=arguments,
            permission=PermissionLevel.MEDIUM,
            decision=decision,
            result=result,
            error=error,
        )


@dataclass(slots=True)
class DesktopResearchSearch:
    searcher: WebSearchClient
    permission_manager: PermissionManager
    audit_logger: AuditLogger

    def search(self, query: str) -> ResearchSearchResult:
        cleaned = " ".join(query.split())
        if not cleaned:
            raise ToolError("Web search query cannot be empty.")
        if len(cleaned) > MAX_SEARCH_QUERY_CHARS or len(cleaned.split()) > MAX_SEARCH_QUERY_WORDS:
            raise ToolError("Web search query exceeds Ato's 400-character or 50-word limit.")
        arguments = {"query": cleaned, "count": DESKTOP_SEARCH_RESULTS}
        self.audit_logger.ensure_ready()
        decision = self.permission_manager.authorize(
            PermissionRequest(
                "web_search",
                PermissionLevel.MEDIUM,
                arguments,
                "Search the public web from the desktop Research section",
            )
        )
        if decision is PermissionDecision.DENY:
            self._audit(arguments, decision, error="User denied permission.")
            raise ToolError("Permission denied for web search.")
        try:
            raw = self.searcher.search(cleaned, DESKTOP_SEARCH_RESULTS)
            result = _parse_result(raw)
        except ToolError as exc:
            self._audit(arguments, decision, error=str(exc))
            raise
        except Exception as exc:
            self._audit(arguments, decision, error="Web search failed safely.")
            raise ToolError("Web search failed safely.") from exc
        self._audit(arguments, decision, result="Bounded web search completed.")
        return result

    def _audit(
        self,
        arguments: dict[str, object],
        decision: PermissionDecision,
        *,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        self.audit_logger.record(
            user_request="Desktop public web search",
            tool_name="web_search",
            arguments=arguments,
            permission=PermissionLevel.MEDIUM,
            decision=decision,
            result=result,
            error=error,
        )


def _parse_result(raw: str) -> ResearchSearchResult:
    try:
        payload = json.loads(raw)
        provider = " ".join(str(payload["provider"]).split())[:40]
        trust = payload["content_trust"]
        results = payload["results"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ToolError("Web search returned an invalid result.") from exc
    if trust != "untrusted_external" or not isinstance(results, list):
        raise ToolError("Web search returned an invalid result.")
    sources = []
    for item in results[:DESKTOP_SEARCH_RESULTS]:
        if not isinstance(item, dict):
            raise ToolError("Web search returned an invalid result.")
        title = " ".join(str(item.get("title", "Untitled source")).split())[:300]
        url = str(item.get("url", ""))[:2_048]
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            continue
        snippet = " ".join(str(item.get("description", "")).split())[:1_000]
        sources.append(ResearchSource(title or "Untitled source", url, snippet))
    return ResearchSearchResult(provider or "unknown", tuple(sources))


def _parse_page(raw: str, requested_url: str) -> ResearchPage:
    try:
        payload = json.loads(raw)
        source_url = str(payload["source_url"])
        trust = payload["content_trust"]
        text = str(payload["text"])
        title = " ".join(str(payload.get("title", "Untitled source")).split())[:300]
        document_type = str(payload.get("document_type", "webpage"))
        truncated = bool(payload.get("truncated", False))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ToolError("Web page fetch returned an invalid result.") from exc
    if trust != "untrusted_external" or source_url != requested_url:
        raise ToolError("Web page fetch returned an invalid result.")
    if document_type not in {"webpage", "pdf"} or not text.strip():
        raise ToolError("Web page fetch returned an invalid result.")
    display_truncated = truncated or len(text) > MAX_DESKTOP_PAGE_CHARS
    return ResearchPage(
        title or "Untitled source",
        source_url,
        text[:MAX_DESKTOP_PAGE_CHARS],
        document_type,
        display_truncated,
    )
