"""Bounded write-only clipboard support."""

from __future__ import annotations

import hashlib
import re
import subprocess
from typing import Protocol

from ato.exceptions import ToolError

MAX_CLIPBOARD_CHARS = 10_000
CLIPBOARD_TIMEOUT_SECONDS = 5
LIKELY_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)bearer\s+[^\s]+"),
    re.compile(r"(?i)(?:api[_-]?key|token|password|secret)\s*[:=]\s*[^\s]+"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


class ClipboardWriter(Protocol):
    def write(self, text: str) -> None:
        """Replace the local text clipboard without returning its prior contents."""
        ...


class WindowsClipboardWriter:
    """Write via a fixed PowerShell command with content supplied only on stdin."""

    def write(self, text: str) -> None:
        command = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Set-Clipboard -Value ([Console]::In.ReadToEnd())",
        ]
        try:
            result = subprocess.run(
                command,
                input=text,
                capture_output=True,
                text=True,
                timeout=CLIPBOARD_TIMEOUT_SECONDS,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError("Clipboard write exceeded its five-second timeout.") from exc
        except OSError as exc:
            raise ToolError("Windows clipboard support could not be started.") from exc
        if result.returncode != 0:
            raise ToolError("Windows rejected the clipboard write.")


def validate_clipboard_text(text: str) -> dict[str, object]:
    if not text or len(text) > MAX_CLIPBOARD_CHARS:
        raise ToolError("Clipboard text must contain 1-10,000 characters.")
    if any(
        (ord(character) < 32 and character not in {"\n", "\r", "\t"})
        or ord(character) == 127
        for character in text
    ):
        raise ToolError("Clipboard text contains unsupported control characters.")
    if any(pattern.search(text) for pattern in LIKELY_SECRET_PATTERNS):
        raise ToolError("Clipboard text appears to contain a secret or credential.")
    return {
        "characters": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
