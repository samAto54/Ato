"""Privacy-conscious read-only Windows process snapshots."""

from __future__ import annotations

import json
import subprocess
from typing import Any, Protocol

from ato.exceptions import ToolError

MAX_PROCESS_SNAPSHOT = 500
MAX_PROCESS_OUTPUT_BYTES = 200_000


class ProcessMonitor(Protocol):
    def snapshot(self) -> list[dict[str, Any]]:
        """Return bounded non-identifying process capacity information."""
        ...


class WindowsProcessMonitor:
    def snapshot(self) -> list[dict[str, Any]]:
        command = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                "Get-Process | Sort-Object ProcessName,Id | Select-Object -First 500 "
                "Id,ProcessName,CPU,WorkingSet64 | ConvertTo-Json -Compress"
            ),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError("Process snapshot exceeded its ten-second timeout.") from exc
        except OSError as exc:
            raise ToolError("Windows process monitoring could not be started.") from exc
        if result.returncode != 0:
            raise ToolError("Windows process snapshot failed.")
        if len(result.stdout.encode("utf-8")) > MAX_PROCESS_OUTPUT_BYTES:
            raise ToolError("Process snapshot exceeds the output limit.")
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise ToolError("Windows returned an unreadable process snapshot.") from exc
        items = payload if isinstance(payload, list) else [payload]
        processes = [_normalize_process(item) for item in items if isinstance(item, dict)]
        return [process for process in processes if process is not None][:MAX_PROCESS_SNAPSHOT]


def _normalize_process(item: dict[str, Any]) -> dict[str, Any] | None:
    process_id = item.get("Id")
    name = item.get("ProcessName")
    if not isinstance(process_id, int) or process_id < 0 or not isinstance(name, str):
        return None
    cpu = item.get("CPU")
    working_set = item.get("WorkingSet64")
    return {
        "process_id": process_id,
        "name": name[:200],
        "cpu_seconds": round(float(cpu), 3) if isinstance(cpu, (int, float)) else None,
        "working_set_bytes": working_set if isinstance(working_set, int) else None,
    }
