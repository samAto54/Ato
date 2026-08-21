"""Small inert Markdown-like formatter for desktop transcript readability."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class ChatStyle(StrEnum):
    BODY = "body"
    HEADING = "heading"
    BOLD = "bold"
    CODE = "code"


@dataclass(frozen=True, slots=True)
class ChatSpan:
    text: str
    style: ChatStyle


INLINE_MARKUP = re.compile(r"(\*\*[^*\n]+\*\*|`[^`\n]+`)")
HEADING = re.compile(r"^#{1,6}\s+(.+)$")
BULLET = re.compile(r"^\s*[-*]\s+(.+)$")


def format_chat_content(content: str) -> tuple[ChatSpan, ...]:
    """Return styled inert text spans without activating links, images, or HTML."""
    safe = "".join(
        character
        if character in {"\n", "\t"} or ord(character) >= 32 and ord(character) != 127
        else "\ufffd"
        for character in content.replace("\r\n", "\n").replace("\r", "\n")
    )
    spans: list[ChatSpan] = []
    lines = safe.split("\n")
    for index, line in enumerate(lines):
        heading = HEADING.fullmatch(line)
        bullet = BULLET.fullmatch(line)
        if heading:
            spans.append(ChatSpan(heading.group(1), ChatStyle.HEADING))
        else:
            if bullet:
                line = f"• {bullet.group(1)}"
            _append_inline_spans(line, spans)
        if index < len(lines) - 1:
            spans.append(ChatSpan("\n", ChatStyle.BODY))
    return tuple(spans)


def _append_inline_spans(line: str, spans: list[ChatSpan]) -> None:
    position = 0
    for match in INLINE_MARKUP.finditer(line):
        if match.start() > position:
            spans.append(ChatSpan(line[position : match.start()], ChatStyle.BODY))
        token = match.group(0)
        style = ChatStyle.BOLD if token.startswith("**") else ChatStyle.CODE
        trim = 2 if style is ChatStyle.BOLD else 1
        spans.append(ChatSpan(token[trim:-trim], style))
        position = match.end()
    if position < len(line):
        spans.append(ChatSpan(line[position:], ChatStyle.BODY))
    elif not line:
        spans.append(ChatSpan("", ChatStyle.BODY))
