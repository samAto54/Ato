"""Themed, fail-closed permission dialog for protected desktop actions."""

from __future__ import annotations

from ato.ui.permissions import GuiPermissionPrompt
from ato.ui.themes import UiTheme


def permission_color(theme: UiTheme, level: str) -> str:
    """Map a known permission level to an unambiguous theme color."""
    normalized = level.strip().upper()
    if normalized == "CRITICAL":
        return theme.danger
    if normalized == "HIGH":
        return theme.warning
    if normalized == "MEDIUM":
        return theme.accent_secondary
    if normalized == "LOW":
        return theme.accent
    return theme.danger


class PermissionDialog:
    """Modal-ready panel whose default and every close path deny the request."""

    def __init__(self, parent, theme: UiTheme, prompt: GuiPermissionPrompt) -> None:
        import tkinter as tk

        self._tk = tk
        self.result = False
        self.window = tk.Toplevel(parent)
        self.window.title("Ato Permission Request")
        self.window.configure(bg=theme.background)
        self.window.resizable(False, False)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.deny)
        self.window.bind("<Escape>", lambda event: self.deny())

        risk_color = permission_color(theme, prompt.permission)
        shell = tk.Frame(
            self.window,
            bg=theme.panel,
            highlightbackground=risk_color,
            highlightthickness=2,
            padx=22,
            pady=18,
        )
        shell.pack(fill="both", expand=True, padx=10, pady=10)
        tk.Label(
            shell,
            text="SECURITY AUTHORIZATION",
            bg=theme.panel,
            fg=risk_color,
            font=(theme.heading_family, 15),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            shell,
            text="Review the exact protected operation before allowing it.",
            bg=theme.panel,
            fg=theme.muted_text,
            font=(theme.font_family, 9),
            anchor="w",
        ).pack(fill="x", pady=(2, 14))

        summary = tk.Frame(shell, bg=theme.panel_alt, padx=12, pady=10)
        summary.pack(fill="x")
        tk.Label(
            summary,
            text=f"TOOL  {prompt.tool_name}",
            bg=theme.panel_alt,
            fg=theme.text,
            font=(theme.font_family, 10),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            summary,
            text=f"RISK  {prompt.permission}",
            bg=theme.panel_alt,
            fg=risk_color,
            font=(theme.heading_family, 10),
            anchor="w",
        ).pack(fill="x", pady=(5, 0))

        tk.Label(
            shell,
            text="REDACTED ARGUMENTS",
            bg=theme.panel,
            fg=theme.accent_secondary,
            font=(theme.heading_family, 9),
            anchor="w",
        ).pack(fill="x", pady=(14, 5))
        details = tk.Text(
            shell,
            width=68,
            height=12,
            wrap="word",
            bg=theme.input_background,
            fg=theme.text,
            insertbackground=theme.text,
            selectbackground=theme.border,
            relief="flat",
            padx=10,
            pady=10,
            font=(theme.font_family, 9),
        )
        details.insert("1.0", prompt.details)
        details.configure(state="disabled")
        details.pack(fill="both", expand=True)

        actions = tk.Frame(shell, bg=theme.panel)
        actions.pack(fill="x", pady=(16, 0))
        tk.Button(
            actions,
            text="DENY",
            command=self.deny,
            bg=theme.panel_alt,
            fg=theme.text,
            activebackground=theme.border,
            activeforeground=theme.text,
            relief="flat",
            width=14,
            padx=8,
            pady=8,
        ).pack(side="left")
        tk.Button(
            actions,
            text="ALLOW ONCE",
            command=self.allow,
            bg=risk_color,
            fg=theme.background,
            activebackground=theme.text,
            activeforeground=theme.background,
            relief="flat",
            width=16,
            padx=8,
            pady=8,
        ).pack(side="right")

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

    def allow(self) -> None:
        self.result = True
        self.close()

    def deny(self) -> None:
        self.result = False
        self.close()

    def close(self) -> None:
        try:
            if self.window.winfo_exists():
                self.window.grab_release()
                self.window.destroy()
        except self._tk.TclError:
            pass


def show_permission_dialog(parent, theme: UiTheme, prompt: GuiPermissionPrompt) -> bool:
    """Block on the Tk event loop until the user explicitly allows or denies once."""
    dialog = PermissionDialog(parent, theme, prompt)
    parent.wait_window(dialog.window)
    return dialog.result
