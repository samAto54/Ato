import json
import socket
from email.message import Message
from io import BytesIO
from urllib.parse import urlsplit

import pytest
from pypdf import PdfWriter
from reportlab.pdfgen import canvas

import ato.tools.web as web_tools
from ato.exceptions import ToolError
from ato.security.permissions import PermissionManager
from ato.tools import build_phase3_registry
from ato.tools.web import _extract_readable_text, _validate_public_https_url


class FakeWebResponse:
    def __init__(self, body: bytes, content_type: str = "application/pdf") -> None:
        self.status = 200
        self.body = body
        self.closed = False
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def read(self, limit: int) -> bytes:
        return self.body[:limit]

    def close(self) -> None:
        self.closed = True


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


def test_web_fetch_extracts_public_pdf_with_page_labels(monkeypatch) -> None:
    stream = BytesIO()
    pdf = canvas.Canvas(stream)
    pdf.drawString(72, 720, "Ato public research evidence")
    pdf.showPage()
    pdf.drawString(72, 720, "Second page finding")
    pdf.save()
    response = FakeWebResponse(stream.getvalue())
    monkeypatch.setattr(
        web_tools,
        "_validate_public_https_url",
        lambda url: (urlsplit(url), ("93.184.216.34",)),
    )
    monkeypatch.setattr(web_tools, "_request_public_address", lambda parsed, addresses: response)

    result = json.loads(web_tools.fetch_web_page("https://example.com/report.pdf"))

    assert result["document_type"] == "pdf"
    assert result["pages"] == 2
    assert "[PDF page 1]" in result["text"]
    assert "[PDF page 2]" in result["text"]
    assert result["source_url"] == "https://example.com/report.pdf"
    assert response.closed is True


def test_web_fetch_rejects_encrypted_and_image_only_pdfs(monkeypatch) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("password")
    encrypted = BytesIO()
    writer.write(encrypted)
    responses = iter(
        [
            FakeWebResponse(encrypted.getvalue()),
            FakeWebResponse(_blank_pdf()),
        ]
    )
    monkeypatch.setattr(
        web_tools,
        "_validate_public_https_url",
        lambda url: (urlsplit(url), ("93.184.216.34",)),
    )
    monkeypatch.setattr(
        web_tools, "_request_public_address", lambda parsed, addresses: next(responses)
    )

    with pytest.raises(ToolError, match="Encrypted"):
        web_tools.fetch_web_page("https://example.com/encrypted.pdf")
    with pytest.raises(ToolError, match="OCR is unavailable"):
        web_tools.fetch_web_page("https://example.com/scanned.pdf")


def _blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()
