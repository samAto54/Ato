"""Themed settings and capability panel for Ato's desktop interface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ato.ui.themes import UiTheme, alternate_theme


@dataclass(frozen=True, slots=True)
class DesktopCapabilities:
    voice_input: bool
    voice_output: bool

    def rows(self) -> tuple[tuple[str, str], ...]:
        def ready(enabled: bool) -> str:
            return "READY" if enabled else "LOCKED"

        return (
            ("TEXT CHAT", "READY"),
            ("VOICE INPUT", ready(self.voice_input)),
            ("VOICE OUTPUT", ready(self.voice_output)),
            ("BACKGROUND LISTENING", "LOCKED"),
        )


class SettingsDialog:
    """Small modal settings surface with only fixed local UI actions."""

    def __init__(
        self,
        parent,
        theme: UiTheme,
        capabilities: DesktopCapabilities,
        *,
        fullscreen: bool,
        on_toggle_theme: Callable[[], None],
        on_toggle_fullscreen: Callable[[], None],
    ) -> None:
        import tkinter as tk

        self._tk = tk
        self.window = tk.Toplevel(parent)
        self.window.title("Ato Interface Settings")
        self.window.configure(bg=theme.background)
        self.window.resizable(False, False)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda event: self.close())

        shell = tk.Frame(
            self.window,
            bg=theme.panel,
            highlightbackground=theme.border,
            highlightthickness=1,
            padx=22,
            pady=18,
        )
        shell.pack(fill="both", expand=True, padx=10, pady=10)
        tk.Label(
            shell,
            text="INTERFACE CONTROL",
            bg=theme.panel,
            fg=theme.accent,
            font=(theme.heading_family, 15),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            shell,
            text=f"ACTIVE THEME  {theme.display_name.upper()}",
            bg=theme.panel,
            fg=theme.muted_text,
            font=(theme.font_family, 9),
            anchor="w",
        ).pack(fill="x", pady=(3, 14))

        capability_panel = tk.Frame(shell, bg=theme.panel_alt, padx=12, pady=10)
        capability_panel.pack(fill="x")
        for label, status in capabilities.rows():
            row = tk.Frame(capability_panel, bg=theme.panel_alt)
            row.pack(fill="x", pady=3)
            tk.Label(
                row,
                text=label,
                bg=theme.panel_alt,
                fg=theme.text,
                font=(theme.font_family, 9),
                anchor="w",
            ).pack(side="left")
            tk.Label(
                row,
                text=status,
                bg=theme.panel_alt,
                fg=theme.accent_secondary if status == "READY" else theme.muted_text,
                font=(theme.heading_family, 9),
            ).pack(side="right")

        tk.Label(
            shell,
            text="DISPLAY",
            bg=theme.panel,
            fg=theme.accent_secondary,
            font=(theme.heading_family, 9),
            anchor="w",
        ).pack(fill="x", pady=(16, 6))
        self._action_button(
            shell,
            theme,
            f"SWITCH TO {alternate_theme(theme.id).display_name.upper()}",
            lambda: self._run_and_close(on_toggle_theme),
        ).pack(fill="x", pady=3)
        self._action_button(
            shell,
            theme,
            "EXIT FULLSCREEN" if fullscreen else "ENTER FULLSCREEN",
            lambda: self._run_and_close(on_toggle_fullscreen),
        ).pack(fill="x", pady=3)

        tk.Label(
            shell,
            text="F11 toggles fullscreen | Esc closes panels | Ctrl+Enter sends",
            bg=theme.panel,
            fg=theme.muted_text,
            font=(theme.font_family, 8),
        ).pack(fill="x", pady=(14, 0))
        tk.Button(
            shell,
            text="CLOSE",
            command=self.close,
            bg=theme.panel_alt,
            fg=theme.text,
            activebackground=theme.border,
            activeforeground=theme.accent,
            relief="flat",
            pady=7,
        ).pack(fill="x", pady=(12, 0))

        self.window.update_idletasks()
        x = parent.winfo_rootx() + max(
            20, (parent.winfo_width() - self.window.winfo_width()) // 2
        )
        y = parent.winfo_rooty() + max(
            20, (parent.winfo_height() - self.window.winfo_height()) // 2
        )
        self.window.geometry(f"+{x}+{y}")
        self.window.grab_set()
        self.window.focus_set()

    def _action_button(self, parent, theme: UiTheme, text: str, command: Callable[[], None]):
        return self._tk.Button(
            parent,
            text=text,
            command=command,
            bg=theme.input_background,
            fg=theme.accent,
            activebackground=theme.border,
            activeforeground=theme.text,
            relief="flat",
            anchor="w",
            padx=12,
            pady=8,
        )

    def _run_and_close(self, action: Callable[[], None]) -> None:
        self.close()
        action()

    def close(self) -> None:
        try:
            if self.window.winfo_exists():
                self.window.grab_release()
                self.window.destroy()
        except self._tk.TclError:
            pass
