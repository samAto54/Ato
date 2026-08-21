"""Fixed-profile Windows application launching."""

from __future__ import annotations

import subprocess
from typing import Protocol

from ato.exceptions import ToolError

APPLICATION_COMMANDS = {
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "file_explorer": ["explorer.exe"],
}


class ApplicationLauncher(Protocol):
    def launch(self, application: str) -> int:
        """Launch one fixed application profile and return the process ID."""
        ...


class WindowsApplicationLauncher:
    """Launch only internal, argument-free Windows application profiles."""

    def launch(self, application: str) -> int:
        command = APPLICATION_COMMANDS.get(application)
        if command is None:
            raise ToolError("Application is not in Ato's launch allowlist.")
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                close_fds=True,
            )
        except OSError as exc:
            raise ToolError(f"The {application} application could not be launched.") from exc
        return process.pid
