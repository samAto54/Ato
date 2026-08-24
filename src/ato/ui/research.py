"""Confirmed, bounded web-search adapter for the desktop Research section."""

from __future__ import annotations

import json
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


@dataclass(frozen=True, slots=True)
class ResearchSearchResult:
    provider: str
    lines: tuple[str, ...]


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
    lines = []
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
        lines.append(f"{title or 'Untitled source'}\n{url}\n{snippet}")
    return ResearchSearchResult(provider or "unknown", tuple(lines))
