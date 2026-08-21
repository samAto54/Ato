from ato.ui.chat_format import ChatSpan, ChatStyle, format_chat_content


def test_chat_formatter_styles_common_markdown_without_preserving_markers() -> None:
    spans = format_chat_content(
        "## Source\n**Title:** Example Domain\n- safe item\nUse `ato-gui`."
    )
    assert ChatSpan("Source", ChatStyle.HEADING) in spans
    assert ChatSpan("Title:", ChatStyle.BOLD) in spans
    assert ChatSpan("• safe item", ChatStyle.BODY) in spans
    assert ChatSpan("ato-gui", ChatStyle.CODE) in spans
    rendered = "".join(span.text for span in spans)
    assert "##" not in rendered
    assert "**" not in rendered
    assert "`" not in rendered


def test_chat_formatter_keeps_urls_inert_plain_text_and_sanitizes_controls() -> None:
    spans = format_chat_content("https://example.com\x00")
    assert spans == (ChatSpan("https://example.com\ufffd", ChatStyle.BODY),)
