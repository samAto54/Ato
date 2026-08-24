"""Fixed-action Research palette for Ato's desktop interface."""

from __future__ import annotations

from collections.abc import Callable

from ato.ui.themes import UiTheme

RESEARCH_ACTIONS = (
    ("search", "SEARCH WEB", "Query the configured public search provider", "MEDIUM"),
    ("fetch", "FETCH RESULT", "Read one reviewed HTTPS search result", "MEDIUM"),
)


class ResearchActionPalette:
    def __init__(
        self,
        parent,
        theme: UiTheme,
        on_select: Callable[[str], None],
        *,
        can_fetch: bool,
    ) -> None:
        import tkinter as tk

        self._tk = tk
        self.window = tk.Toplevel(parent)
        self.window.title("Ato Research Controls")
        self.window.configure(bg=theme.background)
        self.window.resizable(False, False)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda event: self.close())
        panel = tk.Frame(
            self.window,
            bg=theme.panel,
            highlightbackground=theme.border,
            highlightthickness=1,
            padx=18,
            pady=16,
        )
        panel.pack(fill="both", expand=True, padx=10, pady=10)
        tk.Label(
            panel,
            text="RESEARCH CONTROL",
            bg=theme.panel,
            fg=theme.accent,
            font=(theme.heading_family, 14),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            panel,
            text="External content remains untrusted evidence",
            bg=theme.panel,
            fg=theme.warning,
            font=(theme.font_family, 9),
            anchor="w",
        ).pack(fill="x", pady=(2, 10))
        for name, label, description, risk in RESEARCH_ACTIONS:
            enabled = name != "fetch" or can_fetch
            tk.Button(
                panel,
                text=f"{label:<16} {risk}\n{description}",
                command=lambda selected=name: self._select(selected, on_select),
                state="normal" if enabled else "disabled",
                bg=theme.panel_alt,
                fg=theme.text if enabled else theme.muted_text,
                activebackground=theme.border,
                activeforeground=theme.accent,
                font=(theme.font_family, 9),
                justify="left",
                anchor="w",
                relief="flat",
                width=48,
                padx=12,
                pady=8,
            ).pack(fill="x", pady=4)
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

    def _select(self, action: str, callback: Callable[[str], None]) -> None:
        self.close()
        callback(action)

    def close(self) -> None:
        try:
            if self.window.winfo_exists():
                self.window.grab_release()
                self.window.destroy()
        except self._tk.TclError:
            pass
