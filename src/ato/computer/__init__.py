"""Controlled local-computer interfaces."""

from ato.computer.applications import ApplicationLauncher, WindowsApplicationLauncher
from ato.computer.clipboard import ClipboardWriter, WindowsClipboardWriter, validate_clipboard_text
from ato.computer.processes import ProcessMonitor, WindowsProcessMonitor

__all__ = [
    "ApplicationLauncher",
    "ClipboardWriter",
    "ProcessMonitor",
    "WindowsApplicationLauncher",
    "WindowsClipboardWriter",
    "WindowsProcessMonitor",
    "validate_clipboard_text",
]
