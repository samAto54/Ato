"""Bounded native reporting for expected desktop startup failures."""

from __future__ import annotations

from dataclasses import dataclass

from ato.security import AuditLogger

MAX_STARTUP_ERROR_CHARS = 600


@dataclass(frozen=True, slots=True)
class StartupFailure:
    title: str
    message: str
    guidance: str

    def display(self) -> str:
        return f"{self.message}\n\n{self.guidance}"


def build_startup_failure(error: Exception) -> StartupFailure:
    """Create a secret-redacted, bounded and actionable startup message."""
    sanitized = AuditLogger.redact(str(error))
    message = sanitized if isinstance(sanitized, str) else "Ato could not initialize."
    message = " ".join(message.split())[:MAX_STARTUP_ERROR_CHARS]
    if not message:
        message = "Ato could not initialize."
    lowered = message.casefold()
    if "deepseek_api_key" in lowered:
        guidance = (
            "Add DEEPSEEK_API_KEY to the project's .env file, save it, then start Ato again. "
            "Never paste the key into source code or Git."
        )
    elif "stt" in lowered or "voice" in lowered:
        guidance = (
            "Check ATO_VOICE_ENABLED and ATO_STT_MODEL_PATH in .env, or disable voice until the "
            "local dependencies are installed."
        )
    else:
        guidance = (
            "Check the project's .env values and that Ato's data directory is writable, then "
            "start Ato again. The terminal command `ato-gui` will also show this message."
        )
    return StartupFailure("Unable to start Ato", message, guidance)


def show_startup_failure(failure: StartupFailure) -> bool:
    """Show a native dialog when Tk is available; return False for console-only fallback."""
    root = None
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(failure.title, failure.display(), parent=root)
        return True
    except Exception:
        return False
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
