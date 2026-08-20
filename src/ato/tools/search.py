"""Provider-neutral web search contract and Brave Search adapter."""

from __future__ import annotations

import http.client
import json
from collections.abc import Callable, Mapping
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

from ato.exceptions import ToolError

MAX_SEARCH_QUERY_CHARS = 400
MAX_SEARCH_QUERY_WORDS = 50
MAX_SEARCH_RESULTS = 10
MAX_SEARCH_RESPONSE_BYTES = 1_000_000
MAX_SEARCH_FIELD_CHARS = 2_000
SEARCH_TIMEOUT_SECONDS = 10
BRAVE_SEARCH_HOST = "api.search.brave.com"
BRAVE_SEARCH_PATH = "/res/v1/web/search"


class WebSearchClient(Protocol):
    """Search the public web and return bounded JSON evidence."""

    def search(self, query: str, count: int = 5) -> str:
        """Return ranked web results for one query."""
        ...


class BraveSearchClient:
    """Bounded adapter for Brave's official Web Search API."""

    def __init__(
        self,
        api_key: str,
        connection_factory: Callable[..., http.client.HTTPSConnection] = (
            http.client.HTTPSConnection
        ),
    ) -> None:
        if not api_key.strip():
            raise ValueError("Brave Search API key cannot be empty.")
        self._api_key = api_key.strip()
        self._connection_factory = connection_factory

    def search(self, query: str, count: int = 5) -> str:
        cleaned = " ".join(query.split())
        if not cleaned:
            raise ToolError("Web search query cannot be empty.")
        if len(cleaned) > MAX_SEARCH_QUERY_CHARS or len(cleaned.split()) > MAX_SEARCH_QUERY_WORDS:
            raise ToolError("Web search query exceeds Brave's 400-character or 50-word limit.")
        if not 1 <= count <= MAX_SEARCH_RESULTS:
            raise ToolError(f"Web search count must be between 1 and {MAX_SEARCH_RESULTS}.")
        payload = self._request(cleaned, count)
        raw_results = payload.get("web", {}).get("results", [])
        if not isinstance(raw_results, list):
            raise ToolError("Web search provider returned an invalid results structure.")
        results = []
        for raw in raw_results[:count]:
            if not isinstance(raw, Mapping):
                continue
            url = str(raw.get("url", ""))[:MAX_SEARCH_FIELD_CHARS]
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                continue
            results.append(
                {
                    "title": str(raw.get("title", ""))[:MAX_SEARCH_FIELD_CHARS],
                    "url": url,
                    "description": str(raw.get("description", ""))[:MAX_SEARCH_FIELD_CHARS],
                }
            )
        return json.dumps(
            {
                "query": cleaned,
                "provider": "brave",
                "content_trust": "untrusted_external",
                "results": results,
            }
        )

    def _request(self, query: str, count: int) -> dict[str, Any]:
        parameters = urlencode(
            {"q": query, "count": count, "safesearch": "moderate", "search_lang": "en"}
        )
        connection = self._connection_factory(BRAVE_SEARCH_HOST, timeout=SEARCH_TIMEOUT_SECONDS)
        try:
            connection.request(
                "GET",
                f"{BRAVE_SEARCH_PATH}?{parameters}",
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "X-Subscription-Token": self._api_key,
                    "User-Agent": "Ato/0.1 controlled-web-search",
                },
            )
            response = connection.getresponse()
            if response.status in {401, 403}:
                raise ToolError("Brave Search rejected BRAVE_SEARCH_API_KEY.")
            if response.status == 429:
                raise ToolError("Brave Search rate limit reached. Try again later.")
            if response.status != 200:
                raise ToolError(f"Brave Search returned HTTP {response.status}.")
            body = response.read(MAX_SEARCH_RESPONSE_BYTES + 1)
        except ToolError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise ToolError("Could not connect to Brave Search.") from exc
        finally:
            connection.close()
        if len(body) > MAX_SEARCH_RESPONSE_BYTES:
            raise ToolError("Brave Search response exceeded the safety limit.")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ToolError("Brave Search returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise ToolError("Brave Search returned an invalid response object.")
        return payload
