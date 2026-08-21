"""First native desktop shell for the shared Ato Agent Core."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from ato.brain.agent import Agent
from ato.brain.context import ContextManager
from ato.brain.memory import CompositeMemoryRetriever
from ato.brain.prompts import SYSTEM_PROMPT
from ato.config import Settings
from ato.exceptions import AtoError
from ato.knowledge import SqliteKnowledgeStore
from ato.memory import JsonMemoryStore, SqliteLongTermMemory
from ato.providers import DeepSeekProvider
from ato.ui.chat_format import ChatStyle, format_chat_content
from ato.ui.themes import ThemeId, alternate_theme, get_theme

DESKTOP_SYSTEM_PROMPT = f"""{SYSTEM_PROMPT}

Desktop runtime constraint: this interface is currently chat-only and provides no tools. You
cannot browse or fetch web pages, inspect or change files, run commands, use Git, access the
clipboard, record audio, or perform any other tool action in this runtime. Never claim that you
performed an unavailable action. Clearly state the limitation when a request requires a tool and
offer a text-only alternative. Treat recollections of tool results from earlier turns as historical
conversation, not evidence that tools are available now."""


@dataclass(slots=True)
class DesktopChatController:
    """Small synchronous adapter kept independent from Tk for deterministic tests."""

    agent: Agent
    memory_store: JsonMemoryStore | None = None
    long_term_memory: SqliteLongTermMemory | None = None
    knowledge_store: SqliteKnowledgeStore | None = None

    def submit(self, text: str) -> str:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Message cannot be empty.")
        reply = self.agent.respond(cleaned)
        if self.memory_store is not None:
            self.memory_store.save_context(self.agent.conversation, self.agent.summary)
        return reply

    def memory_snapshot(self) -> tuple[str, ...]:
        if self.long_term_memory is None:
            return ("Long-term memory is not configured.",)
        records = self.long_term_memory.list_records(limit=50, include_inactive=True)
        if not records:
            return ("No long-term memories saved.",)
        lines = []
        for record in records:
            status = (
                "archived"
                if record.archived_at
                else "expiration set"
                if record.expires_at
                else "active"
            )
            content = " ".join(record.content.split())
            if len(content) > 240:
                content = f"{content[:237]}..."
            heading = f"#{record.id}  {record.category.value.upper()}  {status.upper()}"
            lines.append(f"{heading}\n{content}")
        return tuple(lines)

    def knowledge_snapshot(self) -> tuple[str, ...]:
        if self.knowledge_store is None:
            return ("Knowledge storage is not configured.",)
        documents = self.knowledge_store.list_documents()[:100]
        if not documents:
            return ("No knowledge documents ingested.",)
        return tuple(
            f"#{document.id}  {document.path}\n{document.chunks} indexed chunks"
            for document in documents
        )


class AtoDesktop:
    """Tk desktop chat with switchable Standard and original Ato HUD themes."""

    def __init__(self, controller: DesktopChatController, theme: ThemeId = ThemeId.STANDARD):
        import tkinter as tk

        self._tk = tk
        self.controller = controller
        self.theme = get_theme(theme)
        self.root = tk.Tk()
        self.root.title("Ato")
        self.root.geometry("1100x720")
        self.root.minsize(820, 560)
        self._busy = False
        self._section = "CHAT"
        self._build()
        self._apply_theme()
        self._restore_visible_history()

    def _build(self) -> None:
        tk = self._tk
        self.sidebar = tk.Frame(self.root, width=230)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.brand = tk.Label(self.sidebar, text="ATO", anchor="w", font=("Segoe UI", 22, "bold"))
        self.brand.pack(fill="x", padx=20, pady=(22, 4))
        self.mode_label = tk.Label(self.sidebar, text="PERSONAL AGENT", anchor="w")
        self.mode_label.pack(fill="x", padx=20, pady=(0, 22))
        self.nav_buttons = {}
        for label in ("CHAT", "MEMORY", "KNOWLEDGE", "RESEARCH", "ACTIVITY"):
            widget = tk.Button(
                self.sidebar,
                text=label,
                anchor="w",
                padx=20,
                pady=8,
                relief="flat",
                command=lambda selected=label: self._show_section(selected),
            )
            widget.pack(fill="x")
            self.nav_buttons[label] = widget

        self.status = tk.Label(
            self.sidebar,
            text="● AGENT READY\n● MEMORY READY\n○ GUI TOOLS LOCKED",
            justify="left",
            anchor="sw",
        )
        self.status.pack(side="bottom", fill="x", padx=20, pady=18)

        self.main_panel = tk.Frame(self.root)
        self.main_panel.pack(side="left", fill="both", expand=True)
        self.header = tk.Frame(self.main_panel, height=64)
        self.header.pack(fill="x")
        self.title = tk.Label(self.header, text="Conversation", anchor="w", font=("Segoe UI", 16))
        self.title.pack(side="left", padx=20, pady=16)
        self.theme_button = tk.Button(self.header, command=self._toggle_theme, relief="flat")
        self.theme_button.pack(side="right", padx=20, pady=12)
        self.lock_badge = tk.Label(self.header, text="CHAT-ONLY • TOOLS LOCKED", padx=10, pady=5)
        self.lock_badge.pack(side="right", pady=15)

        self.transcript = tk.Text(
            self.main_panel,
            wrap="word",
            state="disabled",
            relief="flat",
            padx=20,
            pady=18,
            spacing2=3,
            spacing3=10,
        )
        self.composer = tk.Frame(self.main_panel)
        self.composer.pack(side="bottom", fill="x", padx=16, pady=(0, 16))
        self.input = tk.Text(self.composer, height=3, wrap="word", relief="flat", padx=12, pady=10)
        self.input.pack(side="left", fill="x", expand=True)
        self.input.bind("<Control-Return>", self._submit_event)
        self.send_button = tk.Button(
            self.composer,
            text="SEND",
            command=self._submit,
            width=10,
            relief="flat",
        )
        self.send_button.pack(side="right", fill="y", padx=(10, 0))
        self.transcript.pack(fill="both", expand=True, padx=16, pady=(0, 10))

    def _apply_theme(self) -> None:
        theme = self.theme
        self.root.configure(bg=theme.background)
        self.sidebar.configure(bg=theme.panel_alt)
        self.main_panel.configure(bg=theme.background)
        self.header.configure(bg=theme.panel)
        self.composer.configure(bg=theme.background)
        for widget in self.sidebar.winfo_children():
            widget.configure(bg=theme.panel_alt, fg=theme.text, font=(theme.font_family, 10))
        self.brand.configure(font=(theme.heading_family, 22), fg=theme.accent)
        self.mode_label.configure(fg=theme.muted_text)
        self.status.configure(fg=theme.accent_secondary)
        for label, button in self.nav_buttons.items():
            active = label == self._section
            button.configure(
                bg=theme.border if active else theme.panel_alt,
                fg=theme.accent if active else theme.text,
                activebackground=theme.border,
                activeforeground=theme.accent,
                font=(theme.heading_family if active else theme.font_family, 10),
            )
        self.title.configure(bg=theme.panel, fg=theme.text, font=(theme.heading_family, 16))
        self.lock_badge.configure(
            bg=theme.panel_alt,
            fg=theme.warning,
            font=(theme.font_family, 9),
        )
        self.theme_button.configure(
            text=f"Switch to {alternate_theme(theme.id).display_name}",
            bg=theme.panel_alt,
            fg=theme.accent,
            activebackground=theme.border,
            activeforeground=theme.text,
        )
        self.transcript.configure(
            bg=theme.panel,
            fg=theme.text,
            insertbackground=theme.accent,
            font=(theme.font_family, 11),
        )
        self.transcript.tag_configure(
            "role_you", foreground=theme.accent_secondary, font=(theme.heading_family, 10)
        )
        self.transcript.tag_configure(
            "role_ato", foreground=theme.accent, font=(theme.heading_family, 10)
        )
        self.transcript.tag_configure(
            ChatStyle.BODY.value, foreground=theme.text, font=(theme.font_family, 11)
        )
        self.transcript.tag_configure(
            ChatStyle.HEADING.value, foreground=theme.accent, font=(theme.heading_family, 13)
        )
        self.transcript.tag_configure(
            ChatStyle.BOLD.value, foreground=theme.text, font=(theme.heading_family, 11)
        )
        self.transcript.tag_configure(
            ChatStyle.CODE.value,
            foreground=theme.accent_secondary,
            background=theme.input_background,
            font=("Consolas", 10),
        )
        self.input.configure(
            bg=theme.input_background,
            fg=theme.text,
            insertbackground=theme.accent,
            font=(theme.font_family, 11),
        )
        self.send_button.configure(
            bg=theme.accent,
            fg=theme.background,
            activebackground=theme.accent_secondary,
            activeforeground=theme.background,
            font=(theme.heading_family, 10),
        )

    def _toggle_theme(self) -> None:
        self.theme = alternate_theme(self.theme.id)
        self._apply_theme()

    def _restore_visible_history(self) -> None:
        for message in self.controller.agent.conversation[-20:]:
            self._append(message.role.value.title(), message.content)

    def _show_section(self, section: str) -> None:
        self._section = section
        self._clear_transcript()
        if section == "CHAT":
            self.title.configure(text="Ato is thinking…" if self._busy else "Conversation")
            self.composer.pack(
                side="bottom",
                fill="x",
                padx=16,
                pady=(0, 16),
                before=self.transcript,
            )
            self._restore_visible_history()
            self.input.focus_set()
        else:
            self.composer.pack_forget()
            self.title.configure(text=section.title())
            if section == "MEMORY":
                lines = self.controller.memory_snapshot()
            elif section == "KNOWLEDGE":
                lines = self.controller.knowledge_snapshot()
            elif section == "RESEARCH":
                lines = ("Desktop research is locked until GUI permission dialogs are available.",)
            else:
                lines = ("Desktop tool activity is locked. Use the terminal for audited tools.",)
            self._show_read_only_lines(lines)
        self._apply_theme()

    def _clear_transcript(self) -> None:
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.configure(state="disabled")

    def _show_read_only_lines(self, lines: tuple[str, ...]) -> None:
        self.transcript.configure(state="normal")
        for line in lines:
            self.transcript.insert("end", line, ChatStyle.BODY.value)
            self.transcript.insert("end", "\n\n", ChatStyle.BODY.value)
        self.transcript.configure(state="disabled")

    def _append(self, role: str, content: str) -> None:
        self.transcript.configure(state="normal")
        role_tag = "role_you" if role.casefold() in {"you", "user"} else "role_ato"
        self.transcript.insert("end", f"{role.upper()}\n", role_tag)
        for span in format_chat_content(content):
            self.transcript.insert("end", span.text, span.style.value)
        self.transcript.insert("end", "\n\n", ChatStyle.BODY.value)
        self.transcript.configure(state="disabled")
        self.transcript.see("end")

    def _submit_event(self, event):
        del event
        self._submit()
        return "break"

    def _submit(self) -> None:
        text = self.input.get("1.0", "end").strip()
        if self._busy or not text:
            return
        self.input.delete("1.0", "end")
        self._append("You", text)
        self._set_busy(True)
        threading.Thread(target=self._request_reply, args=(text,), daemon=True).start()

    def _request_reply(self, text: str) -> None:
        try:
            reply = self.controller.submit(text)
        except AtoError as exc:
            self.root.after(0, self._finish_request, None, str(exc))
        except Exception:
            self.root.after(0, self._finish_request, None, "Ato encountered an unexpected error.")
        else:
            self.root.after(0, self._finish_request, reply, None)

    def _finish_request(self, reply: str | None, error: str | None) -> None:
        if self._section == "CHAT":
            self._append("Ato" if reply is not None else "Error", reply or error or "Unknown error")
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.send_button.configure(state="disabled" if busy else "normal")
        if self._section == "CHAT":
            self.title.configure(text="Ato is thinking…" if busy else "Conversation")

    def run(self) -> None:
        self.input.focus_set()
        self.root.mainloop()


def main() -> None:
    """Build the safe chat-only desktop runtime and open its native window."""
    try:
        settings = Settings.from_env()
        memory_store = JsonMemoryStore(settings.memory_file, settings.memory_max_messages)
        memory_context = memory_store.load_context()
        long_term_memory = SqliteLongTermMemory(settings.long_term_memory_file)
        knowledge_store = SqliteKnowledgeStore(settings.knowledge_file, settings.workspace_root)
        agent = Agent(
            DeepSeekProvider(settings.deepseek_api_key, settings.model),
            system_prompt=DESKTOP_SYSTEM_PROMPT,
            history=memory_context.history,
            summary=memory_context.summary,
            context_manager=ContextManager(
                max_tokens=settings.context_max_tokens,
                recent_messages=settings.context_recent_messages,
                max_summary_chars=settings.context_summary_max_chars,
                max_messages=settings.memory_max_messages,
            ),
            memory_retriever=CompositeMemoryRetriever(long_term_memory, knowledge_store),
            tools=None,
        )
        AtoDesktop(
            DesktopChatController(agent, memory_store, long_term_memory, knowledge_store)
        ).run()
    except (AtoError, ValueError) as exc:
        raise SystemExit(f"Unable to start Ato desktop: {exc}") from exc


if __name__ == "__main__":
    main()
