"""Secret-free, offline readiness diagnostics for Ato installations."""

from __future__ import annotations

import argparse
import importlib.util
import platform
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ato.config import Settings
from ato.exceptions import AtoError


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    level: str
    name: str
    detail: str

    def display(self) -> str:
        return f"[{self.level}] {self.name}: {self.detail}"


def run_checks(
    settings_loader: Callable[[], Settings] = Settings.from_env,
    module_finder: Callable[[str], object | None] = importlib.util.find_spec,
) -> tuple[DoctorCheck, ...]:
    """Inspect local readiness without network access or secret-value output."""
    checks: list[DoctorCheck] = []
    python_ready = sys.version_info >= (3, 11)
    checks.append(
        DoctorCheck(
            "PASS" if python_ready else "FAIL",
            "Python",
            f"{platform.python_version()} ({'supported' if python_ready else 'requires 3.11+'})",
        )
    )
    missing = [
        name
        for name in ("openai", "pypdf", "docx", "dotenv")
        if module_finder(name) is None
    ]
    checks.append(
        DoctorCheck(
            "FAIL" if missing else "PASS",
            "Required packages",
            f"missing {', '.join(missing)}" if missing else "available",
        )
    )
    checks.append(
        DoctorCheck(
            "PASS" if module_finder("tkinter") is not None else "FAIL",
            "Desktop UI",
            "Tkinter available" if module_finder("tkinter") is not None else "Tkinter unavailable",
        )
    )
    try:
        settings = settings_loader()
    except (AtoError, ValueError) as exc:
        checks.append(DoctorCheck("FAIL", "Configuration", str(exc)))
        return tuple(checks)
    checks.append(DoctorCheck("PASS", "Configuration", "validated; secret values hidden"))
    workspace = settings.workspace_root.resolve()
    checks.append(
        DoctorCheck(
            "PASS" if workspace.is_dir() else "FAIL",
            "Workspace",
            str(workspace) if workspace.is_dir() else "configured directory does not exist",
        )
    )
    search_ready = bool(settings.tavily_api_key or settings.brave_search_api_key)
    checks.append(
        DoctorCheck(
            "PASS" if search_ready else "WARN",
            "Web search",
            "provider configured" if search_ready else "optional provider not configured",
        )
    )
    if not settings.voice_enabled:
        checks.append(DoctorCheck("WARN", "Voice", "disabled by configuration"))
    else:
        sound_ready = module_finder("sounddevice") is not None
        stt_ready = settings.stt_model_path is not None and settings.stt_model_path.is_dir()
        checks.append(
            DoctorCheck(
                "PASS" if sound_ready else "FAIL",
                "Voice audio",
                "sounddevice available" if sound_ready else "sounddevice package missing",
            )
        )
        checks.append(
            DoctorCheck(
                "PASS" if stt_ready else "WARN",
                "Speech recognition",
                "local model available" if stt_ready else "local STT model not configured",
            )
        )
    return tuple(checks)


def exit_code(checks: Sequence[DoctorCheck]) -> int:
    return 1 if any(check.level == "FAIL" for check in checks) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    checks = run_checks()
    print("Ato readiness doctor (offline; secret values hidden)")
    for check in checks:
        print(check.display())
    result = exit_code(checks)
    print("Ato is ready." if result == 0 else "Ato needs attention before launch.")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
