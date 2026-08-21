"""Opt-in web-search provider integration checks."""

import json
import os

import pytest
from dotenv import load_dotenv

from ato.tools.search import BraveSearchClient, TavilySearchClient

pytestmark = pytest.mark.live_network
load_dotenv()


@pytest.mark.skipif(
    os.getenv("ATO_RUN_LIVE_WEB_TESTS") != "1" or not os.getenv("BRAVE_SEARCH_API_KEY"),
    reason="Set ATO_RUN_LIVE_WEB_TESTS=1 and BRAVE_SEARCH_API_KEY for a paid API request.",
)
def test_live_brave_web_search() -> None:
    client = BraveSearchClient(os.environ["BRAVE_SEARCH_API_KEY"])
    result = json.loads(client.search("Ato AI agent Python", count=3))

    assert result["provider"] == "brave"
    assert result["content_trust"] == "untrusted_external"
    assert 1 <= len(result["results"]) <= 3
    assert all(item["url"].startswith(("http://", "https://")) for item in result["results"])


@pytest.mark.skipif(
    os.getenv("ATO_RUN_LIVE_WEB_TESTS") != "1" or not os.getenv("TAVILY_API_KEY"),
    reason="Set ATO_RUN_LIVE_WEB_TESTS=1 and TAVILY_API_KEY for one free-credit request.",
)
def test_live_tavily_web_search() -> None:
    client = TavilySearchClient(os.environ["TAVILY_API_KEY"])
    result = json.loads(client.search("Ato AI agent Python", count=3))

    assert result["provider"] == "tavily"
    assert result["content_trust"] == "untrusted_external"
    assert 1 <= len(result["results"]) <= 3
    assert all(item["url"].startswith(("http://", "https://")) for item in result["results"])
