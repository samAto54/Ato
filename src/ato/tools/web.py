"""Bounded HTTPS page retrieval with private-network protections."""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
from collections.abc import Callable
from html.parser import HTMLParser
from urllib.parse import SplitResult, urlsplit

from ato.exceptions import ToolError

MAX_WEB_BYTES = 500_000
MAX_WEB_TEXT_CHARS = 50_000
WEB_TIMEOUT_SECONDS = 10
ALLOWED_CONTENT_TYPES = {"text/html", "text/plain", "application/xhtml+xml"}
Resolver = Callable[..., list[tuple]]


class _ReadableHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._title_depth = 0
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored_depth += 1
        elif tag == "title":
            self._title_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif tag == "title" and self._title_depth:
            self._title_depth -= 1

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(data.split())
        if not cleaned or self._ignored_depth:
            return
        if self._title_depth:
            self.title_parts.append(cleaned)
        else:
            self.text_parts.append(cleaned)


def fetch_web_page(url: str) -> str:
    """Fetch one public HTTPS page without following redirects."""
    parsed, addresses = _validate_public_https_url(url)
    response = _request_public_address(parsed, addresses)
    try:
        if 300 <= response.status < 400:
            raise ToolError(
                "Web redirects are not followed; approve the destination URL separately."
            )
        if not 200 <= response.status < 300:
            raise ToolError(f"Web server returned HTTP {response.status}.")
        content_type = response.headers.get_content_type().casefold()
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ToolError("Web response is not a supported text or HTML page.")
        if response.headers.get("Content-Encoding", "identity").casefold() != "identity":
            raise ToolError("Compressed web responses are not accepted in this phase.")
        body = response.read(MAX_WEB_BYTES + 1)
    except (OSError, http.client.HTTPException) as exc:
        raise ToolError("Web response could not be read safely.") from exc
    finally:
        response.close()
    if len(body) > MAX_WEB_BYTES:
        raise ToolError(f"Web response exceeds the {MAX_WEB_BYTES}-byte limit.")
    charset = response.headers.get_content_charset() or "utf-8"
    try:
        decoded = body.decode(charset, errors="replace")
    except LookupError as exc:
        raise ToolError("Web response declares an unsupported text encoding.") from exc
    title, text = _extract_readable_text(decoded, content_type)
    truncated = len(text) > MAX_WEB_TEXT_CHARS
    return json.dumps(
        {
            "url": parsed.geturl(),
            "status": response.status,
            "title": title,
            "text": text[:MAX_WEB_TEXT_CHARS],
            "truncated": truncated,
        }
    )


def _validate_public_https_url(
    url: str, resolver: Resolver = socket.getaddrinfo
) -> tuple[SplitResult, tuple[str, ...]]:
    if len(url) > 2_048:
        raise ToolError("Web URL exceeds the 2,048-character limit.")
    parsed = urlsplit(url)
    if parsed.scheme.casefold() != "https":
        raise ToolError("Only HTTPS web URLs are allowed.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ToolError("Web URL must contain a public hostname and no credentials.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ToolError("Web URL contains an invalid port.") from exc
    if port not in {None, 443}:
        raise ToolError("Only the standard HTTPS port is allowed.")
    try:
        records = resolver(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ToolError("Web hostname could not be resolved.") from exc
    addresses = tuple(dict.fromkeys(str(record[4][0]) for record in records))
    if not addresses:
        raise ToolError("Web hostname resolved to no addresses.")
    try:
        if any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ToolError("Private, local, reserved, and non-public web addresses are blocked.")
    except ValueError as exc:
        raise ToolError("Web hostname returned an invalid network address.") from exc
    return parsed, addresses


def _request_public_address(
    parsed: SplitResult, addresses: tuple[str, ...]
) -> http.client.HTTPResponse:
    hostname = parsed.hostname
    assert hostname is not None
    target = parsed.path or "/"
    if parsed.query:
        target += f"?{parsed.query}"
    context = ssl.create_default_context()
    last_error: OSError | None = None
    for address in addresses:
        connection = http.client.HTTPSConnection(hostname, 443, timeout=WEB_TIMEOUT_SECONDS)
        raw_socket: socket.socket | None = None
        try:
            raw_socket = socket.create_connection((address, 443), timeout=WEB_TIMEOUT_SECONDS)
            connection.sock = context.wrap_socket(raw_socket, server_hostname=hostname)
            raw_socket = None
            connection.request(
                "GET",
                target,
                headers={
                    "Accept": "text/html,text/plain,application/xhtml+xml",
                    "Accept-Encoding": "identity",
                    "User-Agent": "Ato/0.1 controlled-web-fetch",
                },
            )
            return connection.getresponse()
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            connection.close()
            if raw_socket is not None:
                raw_socket.close()
            last_error = exc
    raise ToolError("Public HTTPS page could not be fetched.") from last_error


def _extract_readable_text(content: str, content_type: str) -> tuple[str, str]:
    if content_type == "text/plain":
        return "", "\n".join(line.strip() for line in content.splitlines() if line.strip())
    parser = _ReadableHtmlParser()
    try:
        parser.feed(content)
        parser.close()
    except (ValueError, AssertionError) as exc:
        raise ToolError("HTML response could not be parsed safely.") from exc
    return " ".join(parser.title_parts), "\n".join(parser.text_parts)
