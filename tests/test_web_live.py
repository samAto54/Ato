"""Opt-in integration checks that require real outbound HTTPS access."""

import json
import os

import pytest

from ato.tools.web import fetch_web_page

pytestmark = pytest.mark.live_network


@pytest.mark.skipif(
    os.getenv("ATO_RUN_LIVE_WEB_TESTS") != "1",
    reason="Set ATO_RUN_LIVE_WEB_TESTS=1 to permit real external HTTPS requests.",
)
def test_live_public_https_fetch() -> None:
    result = json.loads(fetch_web_page("https://example.com/"))

    assert result["status"] == 200
    assert result["url"] == "https://example.com/"
    assert result["source_url"] == "https://example.com/"
    assert result["content_trust"] == "untrusted_external"
    assert "Example Domain" in result["title"]
    assert result["text"]
    assert result["truncated"] is False
