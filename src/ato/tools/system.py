"""Privacy-conscious, read-only system information helpers."""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
from pathlib import Path
from typing import Any


def collect_system_info(workspace_root: Path) -> dict[str, Any]:
    """Collect non-identifying host capacity information without network probes."""
    disk = shutil.disk_usage(workspace_root)
    total_memory, available_memory = _memory_bytes()
    return {
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
        },
        "python": platform.python_version(),
        "cpu": {"logical_cores": os.cpu_count()},
        "memory_bytes": {"total": total_memory, "available": available_memory},
        "workspace_disk_bytes": {"total": disk.total, "used": disk.used, "free": disk.free},
        "network": {"status": "not_probed"},
    }


def _memory_bytes() -> tuple[int | None, int | None]:
    if os.name == "nt":
        return _windows_memory_bytes()
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        total_pages = int(os.sysconf("SC_PHYS_PAGES"))
        available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
    except (AttributeError, OSError, TypeError, ValueError):
        return None, None
    return page_size * total_pages, page_size * available_pages


def _windows_memory_bytes() -> tuple[int | None, int | None]:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(MemoryStatus)
    try:
        succeeded = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    except (AttributeError, OSError):
        return None, None
    if not succeeded:
        return None, None
    return int(status.total_physical), int(status.available_physical)
