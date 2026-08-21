"""Controlled local-computer interfaces."""

from ato.computer.clipboard import ClipboardWriter, WindowsClipboardWriter, validate_clipboard_text

__all__ = ["ClipboardWriter", "WindowsClipboardWriter", "validate_clipboard_text"]
