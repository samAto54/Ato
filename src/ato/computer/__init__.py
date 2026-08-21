"""Controlled local-computer interfaces."""

from ato.computer.applications import ApplicationLauncher, WindowsApplicationLauncher
from ato.computer.clipboard import ClipboardWriter, WindowsClipboardWriter, validate_clipboard_text

__all__ = [
    "ApplicationLauncher",
    "ClipboardWriter",
    "WindowsApplicationLauncher",
    "WindowsClipboardWriter",
    "validate_clipboard_text",
]
