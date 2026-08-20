import json
import socket

import pytest

from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry
from ato.tools.web import _extract_readable_text, _validate_public_https_url


def _resolver_for(address: str):
    def resolve(host: str, port: int, type: int):
        assert host and port == 443 and type == socket.SOCK_STREAM
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    return resolve


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://example.com", "Only HTTPS"),
        ("https://user:pass@example.com", "no credentials"),
        ("https://example.com:8443", "standard HTTPS port"),
    ],
)
def test_web_url_validation_rejects_unsafe_forms(url: str, message: str) -> None:
    with pytest.raises(ToolError, match=message):
        _validate_public_https_url(url, _resolver_for("93.184.216.34"))


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.2", "169.254.169.254", "::1"])
def test_web_url_validation_blocks_non_public_networks(address: str) -> None:
    with pytest.raises(ToolError, match="non-public"):
        _validate_public_https_url("https://example.com", _resolver_for(address))


def test_web_url_validation_accepts_public_https_and_preserves_path() -> None:
    parsed, addresses = _validate_public_https_url(
        "https://example.com/research?q=ato", _resolver_for("93.184.216.34")
    )

    assert parsed.path == "/research"
    assert parsed.query == "q=ato"
    assert addresses == ("93.184.216.34",)


def test_html_extraction_omits_scripts_and_styles() -> None:
    title, text = _extract_readable_text(
        "<html><head><title>Ato Research</title><style>hidden</style></head>"
        "<body><h1>Finding</h1><script>ignore()</script><p>Useful evidence.</p></body></html>",
        "text/html",
    )

    assert title == "Ato Research"
    assert text == "Finding\nUseful evidence."


def test_web_fetch_tool_requires_medium_confirmation(tmp_path) -> None:
    calls: list[str] = []

    def fake_fetch(url: str) -> str:
        calls.append(url)
        return json.dumps({"url": url, "status": 200, "text": "Evidence"})

    denied = build_phase3_registry(tmp_path, web_fetcher=fake_fetch)
    with pytest.raises(ToolError, match="Permission denied"):
        denied.execute("fetch_web_page", {"url": "https://example.com"})
    assert calls == []

    seen = []
    allowed = build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: not seen.append(request)),
        web_fetcher=fake_fetch,
    )
    result = json.loads(
        allowed.execute("fetch_web_page", {"url": "https://example.com/research"})
    )

    assert seen[0].level.value == "MEDIUM"
    assert calls == ["https://example.com/research"]
    assert result["text"] == "Evidence"
