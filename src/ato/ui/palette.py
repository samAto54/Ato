"""Themed fixed-action palette for guarded desktop workspace operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ato.ui.themes import UiTheme


@dataclass(frozen=True, slots=True)
class PaletteAction:
    name: str
    label: str
    description: str
    risk: str


WORKSPACE_ACTION_GROUPS: tuple[tuple[str, tuple[PaletteAction, ...]], ...] = (
    (
        "FILES",
        (
            PaletteAction("list", "LIST FILES", "Bounded project tree", "LOW"),
            PaletteAction("read", "READ TEXT", "One UTF-8 file", "LOW"),
            PaletteAction("search", "SEARCH", "Literal workspace search", "LOW"),
        ),
    ),
    (
        "GIT INSPECTION",
        (
            PaletteAction("status", "STATUS", "Working tree summary", "LOW"),
            PaletteAction("diff", "DIFF", "Unstaged changes", "LOW"),
            PaletteAction("staged", "STAGED", "Staged changes", "LOW"),
            PaletteAction("log", "LOG", "Recent 20 commits", "LOW"),
            PaletteAction("branches", "BRANCHES", "Local branches", "LOW"),
        ),
    ),
    (
        "VERIFY",
        (
            PaletteAction("syntax", "SYNTAX", "Parse one Python file", "LOW"),
            PaletteAction("lint", "RUFF", "Fixed non-fixing lint", "MEDIUM"),
            PaletteAction("tests", "PYTEST", "Fixed project tests", "HIGH"),
        ),
    ),
    (
        "EDIT & RECOVERY",
        (
            PaletteAction("preview", "PREVIEW EDIT", "Exact diff; no write", "LOW"),
            PaletteAction("checkpoints", "CHECKPOINTS", "Recent recovery metadata", "LOW"),
            PaletteAction("rollback", "ROLLBACK", "Restore reviewed checkpoint", "HIGH"),
        ),
    ),
)


def workspace_action_names() -> tuple[str, ...]:
    return tuple(action.name for _, actions in WORKSPACE_ACTION_GROUPS for action in actions)


class WorkspaceActionPalette:
    """Small modal command surface containing only compile-time allowlisted actions."""

    def __init__(
        self,
        parent,
        theme: UiTheme,
        on_select: Callable[[str], None],
    ) -> None:
        import tkinter as tk

        self._tk = tk
        self.window = tk.Toplevel(parent)
        self.window.title("Ato Workspace Command Matrix")
        self.window.configure(bg=theme.background)
        self.window.resizable(False, False)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda event: self.close())

        heading = tk.Label(
            self.window,
            text="WORKSPACE COMMAND MATRIX",
            bg=theme.background,
            fg=theme.accent,
            font=(theme.heading_family, 15),
            anchor="w",
        )
        heading.grid(row=0, column=0, columnspan=2, sticky="ew", padx=22, pady=(18, 2))
        subtitle = tk.Label(
            self.window,
            text="Fixed guarded actions only • Esc closes",
            bg=theme.background,
            fg=theme.muted_text,
            font=(theme.font_family, 9),
            anchor="w",
        )
        subtitle.grid(row=1, column=0, columnspan=2, sticky="ew", padx=22, pady=(0, 12))

        for group_index, (group_name, actions) in enumerate(WORKSPACE_ACTION_GROUPS):
            row = 2 + group_index // 2
            column = group_index % 2
            panel = tk.Frame(
                self.window,
                bg=theme.panel,
                highlightbackground=theme.border,
                highlightthickness=1,
                padx=10,
                pady=10,
            )
            panel.grid(row=row, column=column, sticky="nsew", padx=8, pady=8)
            tk.Label(
                panel,
                text=group_name,
                bg=theme.panel,
                fg=theme.accent_secondary,
                font=(theme.heading_family, 10),
                anchor="w",
            ).pack(fill="x", pady=(0, 6))
            for action in actions:
                button = tk.Button(
                    panel,
                    text=f"{action.label:<14}  {action.risk}\n{action.description}",
                    command=lambda selected=action.name: self._select(selected, on_select),
                    bg=theme.panel_alt,
                    fg=theme.text,
                    activebackground=theme.border,
                    activeforeground=theme.accent,
                    font=(theme.font_family, 9),
                    justify="left",
                    anchor="w",
                    relief="flat",
                    width=34,
                    padx=10,
                    pady=7,
                )
                button.pack(fill="x", pady=3)

        self.window.update_idletasks()
        x = parent.winfo_rootx() + max(20, (parent.winfo_width() - self.window.winfo_width()) // 2)
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
