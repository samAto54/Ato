"""First native desktop shell for the shared Ato Agent Core."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from ato.brain.agent import Agent
from ato.brain.context import ContextManager
from ato.brain.memory import CompositeMemoryRetriever
from ato.brain.messages import Role
from ato.brain.prompts import SYSTEM_PROMPT
from ato.config import Settings
from ato.exceptions import AtoError
from ato.knowledge import SqliteKnowledgeStore
from ato.memory import JsonMemoryStore, SqliteLongTermMemory
from ato.providers import DeepSeekProvider
from ato.security import AuditLogger, PermissionManager
from ato.tools.system import collect_system_info
from ato.ui.chat_format import ChatStyle, format_chat_content
from ato.ui.orb import AtoOrbCanvas
from ato.ui.permissions import GuiPermissionBridge, GuiPermissionPrompt
from ato.ui.speech import DesktopSpeechService
from ato.ui.state import AtoStateModel, AtoVisualState
from ato.ui.themes import ThemeId, alternate_theme, get_theme
from ato.voice import WindowsSpeechPlayer

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
    workspace_root: Path | None = None
    speech_service: DesktopSpeechService | None = None

    def submit(self, text: str) -> str:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Message cannot be empty.")
        reply = self.agent.respond(cleaned)
        if self.memory_store is not None:
            self.memory_store.save_context(self.agent.conversation, self.agent.summary)
        return reply

    def latest_assistant_reply(self) -> str | None:
        return next(
            (
                message.content
                for message in reversed(self.agent.conversation)
                if message.role is Role.ASSISTANT
            ),
            None,
        )

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

    def system_snapshot(self) -> tuple[str, ...]:
        if self.workspace_root is None:
            return ("SYSTEM DATA UNAVAILABLE",)
        info = collect_system_info(self.workspace_root)
        memory = info["memory_bytes"]
        total = memory["total"]
        available = memory["available"]
        used_percent = (
            round((int(total) - int(available)) / int(total) * 100)
            if total and available is not None
            else None
        )
        return (
            f"OS  {info['os']['system']} {info['os']['release']}",
            f"CPU  {info['cpu']['logical_cores'] or '?'} LOGICAL CORES",
            f"RAM  {used_percent}% USED" if used_percent is not None else "RAM  UNAVAILABLE",
            "NETWORK  NOT PROBED",
        )


class AtoDesktop:
    """Tk desktop chat with switchable Standard and original Ato HUD themes."""

    def __init__(
        self,
        controller: DesktopChatController,
        theme: ThemeId = ThemeId.ATO_HUD,
        permission_bridge: GuiPermissionBridge | None = None,
    ):
        import tkinter as tk

        self._tk = tk
        self.controller = controller
        self.theme = get_theme(theme)
        self.root = tk.Tk()
        self.root.title("Ato")
        self.root.geometry("1440x900")
        self.root.minsize(1024, 680)
        self.permission_bridge = permission_bridge
        if self.permission_bridge is not None:
            self.permission_bridge.attach(self.root.after, self._ask_permission)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        try:
            self.root.state("zoomed")
        except tk.TclError:
            pass
        self._busy = False
        self._section = "CHAT"
        self.state_model = AtoStateModel()
        self._fullscreen = False
        self._build()
        self._apply_theme()
        self._restore_visible_history()
        self.root.bind("<F11>", self._toggle_fullscreen)
        self.root.bind("<Escape>", self._leave_fullscreen)
        self._refresh_hud()

    def _build(self) -> None:
        tk = self._tk
        self.sidebar = tk.Frame(self.root, width=240)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.brand = tk.Label(self.sidebar, text="ATO", anchor="w", font=("Segoe UI", 22, "bold"))
        self.brand.pack(fill="x", padx=20, pady=(22, 4))
        self.mode_label = tk.Label(self.sidebar, text="PERSONAL AGENT", anchor="w")
        self.mode_label.pack(fill="x", padx=20, pady=(0, 22))
        self.mode_status = tk.Label(
            self.sidebar,
            text="MODE\nASSISTANT\n\nSESSION\nACTIVE",
            justify="left",
            anchor="w",
            padx=20,
            pady=10,
        )
        self.mode_status.pack(fill="x")
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
            text=(
                "● AGENT READY\n● MEMORY READY\n● SPEECH READY\n○ OTHER TOOLS LOCKED"
                if self.controller.speech_service is not None
                else "● AGENT READY\n● MEMORY READY\n○ GUI TOOLS LOCKED"
            ),
            justify="left",
            anchor="sw",
        )
        self.status.pack(side="bottom", fill="x", padx=20, pady=18)

        self.right_panel = tk.Frame(self.root, width=260)
        self.right_panel.pack(side="right", fill="y")
        self.right_panel.pack_propagate(False)
        self.task_heading = tk.Label(self.right_panel, text="ACTIVE TASK", anchor="w")
        self.task_heading.pack(fill="x", padx=18, pady=(28, 6))
        self.task_value = tk.Label(
            self.right_panel,
            text="Awaiting input",
            justify="left",
            anchor="nw",
            wraplength=220,
        )
        self.task_value.pack(fill="x", padx=18, pady=(0, 22))
        self.tool_heading = tk.Label(self.right_panel, text="TOOL CHANNEL", anchor="w")
        self.tool_heading.pack(fill="x", padx=18, pady=(0, 6))
        self.tool_value = tk.Label(
            self.right_panel,
            text="LOCKED - PERMISSION BRIDGE PENDING",
            justify="left",
            anchor="nw",
            wraplength=220,
        )
        self.tool_value.pack(fill="x", padx=18, pady=(0, 22))
        self.system_heading = tk.Label(self.right_panel, text="SYSTEM", anchor="w")
        self.system_heading.pack(fill="x", padx=18, pady=(0, 6))
        self.system_value = tk.Label(
            self.right_panel,
            text="\n".join(self.controller.system_snapshot()),
            justify="left",
            anchor="nw",
            wraplength=220,
        )
        self.system_value.pack(fill="x", padx=18)

        self.main_panel = tk.Frame(self.root)
        self.main_panel.pack(side="left", fill="both", expand=True)
        self.header = tk.Frame(self.main_panel, height=64)
        self.header.pack(fill="x")
        self.title = tk.Label(self.header, text="Conversation", anchor="w", font=("Segoe UI", 16))
        self.title.pack(side="left", padx=20, pady=16)
        self.settings_button = tk.Button(
            self.header,
            text="SETTINGS",
            command=self._show_settings,
            relief="flat",
        )
        self.settings_button.pack(side="right", padx=(0, 12), pady=12)
        self.theme_button = tk.Button(self.header, command=self._toggle_theme, relief="flat")
        self.theme_button.pack(side="right", padx=(0, 12), pady=12)
        self.connection_label = tk.Label(self.header, text="LOCAL CORE ONLINE", padx=10, pady=5)
        self.connection_label.pack(side="right", pady=15)
        self.lock_badge = tk.Label(
            self.header,
            text=(
                "CHAT + APPROVED SPEECH"
                if self.controller.speech_service is not None
                else "CHAT-ONLY • TOOLS LOCKED"
            ),
            padx=10,
            pady=5,
        )
        self.lock_badge.pack(side="right", pady=15)

        self.orb = AtoOrbCanvas(self.main_panel, self.state_model, self.theme, height=340)
        self.orb.pack(fill="x", padx=16, pady=(8, 4))

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
        self.microphone_button = tk.Button(
            self.composer,
            text="MIC LOCKED",
            command=self._voice_locked,
            width=11,
            relief="flat",
        )
        self.microphone_button.pack(side="right", fill="y", padx=(10, 0))
        self.speak_button = tk.Button(
            self.composer,
            text="SPEAK LAST" if self.controller.speech_service is not None else "SPEECH OFF",
            command=self._speak_latest,
            width=11,
            relief="flat",
            state="normal" if self.controller.speech_service is not None else "disabled",
        )
        self.speak_button.pack(side="right", fill="y", padx=(10, 0))
        self.current_status = tk.Label(
            self.composer,
            text="READY",
            width=12,
            anchor="center",
        )
        self.current_status.pack(side="right", fill="y", padx=(10, 0))
        self.transcript.pack(fill="both", expand=True, padx=16, pady=(0, 10))

    def _apply_theme(self) -> None:
        theme = self.theme
        self.root.configure(bg=theme.background)
        self.sidebar.configure(bg=theme.panel_alt)
        self.right_panel.configure(bg=theme.panel_alt)
        self.main_panel.configure(bg=theme.background)
        self.header.configure(bg=theme.panel)
        self.composer.configure(bg=theme.background)
        for widget in self.sidebar.winfo_children():
            widget.configure(bg=theme.panel_alt, fg=theme.text, font=(theme.font_family, 10))
        for widget in self.right_panel.winfo_children():
            widget.configure(bg=theme.panel_alt, fg=theme.text, font=(theme.font_family, 9))
        self.brand.configure(font=(theme.heading_family, 22), fg=theme.accent)
        self.mode_label.configure(fg=theme.muted_text)
        self.mode_status.configure(fg=theme.muted_text)
        self.status.configure(fg=theme.accent_secondary)
        for heading in (self.task_heading, self.tool_heading, self.system_heading):
            heading.configure(fg=theme.accent, font=(theme.heading_family, 10))
        self.task_value.configure(fg=theme.text)
        self.tool_value.configure(fg=theme.warning)
        self.system_value.configure(fg=theme.muted_text)
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
        self.connection_label.configure(
            bg=theme.panel,
            fg=theme.accent_secondary,
            font=(theme.font_family, 9),
        )
        self.settings_button.configure(
            bg=theme.panel_alt,
            fg=theme.text,
            activebackground=theme.border,
            activeforeground=theme.accent,
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
        self.microphone_button.configure(
            bg=theme.panel_alt,
            fg=theme.muted_text,
            activebackground=theme.border,
            activeforeground=theme.warning,
            font=(theme.font_family, 9),
        )
        self.speak_button.configure(
            bg=theme.panel_alt,
            fg=theme.accent_secondary if self.controller.speech_service else theme.muted_text,
            activebackground=theme.border,
            activeforeground=theme.accent,
            font=(theme.font_family, 9),
        )
        self.current_status.configure(
            bg=theme.panel_alt,
            fg=theme.accent,
            font=(theme.heading_family, 9),
        )
        self.orb.set_theme(theme)
        self._apply_layout_mode()

    def _toggle_theme(self) -> None:
        self.theme = alternate_theme(self.theme.id)
        self._apply_theme()

    def _apply_layout_mode(self) -> None:
        if self.theme.id is ThemeId.ATO_HUD:
            if not self.right_panel.winfo_manager():
                self.right_panel.pack(side="right", fill="y", before=self.main_panel)
            if not self.orb.canvas.winfo_manager():
                self.orb.pack(fill="x", padx=16, pady=(8, 4), before=self.transcript)
        else:
            self.right_panel.pack_forget()
            self.orb.pack_forget()

    def _toggle_fullscreen(self, event=None) -> str:
        del event
        self._fullscreen = not self._fullscreen
        self.root.attributes("-fullscreen", self._fullscreen)
        return "break"

    def _leave_fullscreen(self, event=None) -> str:
        del event
        self._fullscreen = False
        self.root.attributes("-fullscreen", False)
        return "break"

    def _show_settings(self) -> None:
        from tkinter import messagebox

        messagebox.showinfo(
            "Ato interface settings",
            "Theme: "
            f"{self.theme.display_name}\n\nF11: toggle full screen\nEsc: leave full screen\n"
            "Ctrl+Enter: send message\n\n"
            + (
                "SPEAK LAST is enabled with confirmation. Microphone and other GUI tools remain "
                "locked."
                if self.controller.speech_service is not None
                else "Voice and GUI tools remain locked until explicitly enabled."
            ),
            parent=self.root,
        )

    def _ask_permission(self, prompt: GuiPermissionPrompt) -> bool:
        """Display one redacted tool request on Tk's UI thread."""
        from tkinter import messagebox

        return messagebox.askyesno(
            "Ato permission request",
            f"Tool: {prompt.tool_name}\n"
            f"Permission: {prompt.permission}\n\n"
            f"Arguments (secrets redacted):\n{prompt.details}\n\n"
            "Review the requested action and its effects before allowing it.",
            icon="warning",
            parent=self.root,
        )

    def _close(self) -> None:
        if self.permission_bridge is not None:
            self.permission_bridge.detach()
        self.root.destroy()

    def _voice_locked(self) -> None:
        from tkinter import messagebox

        messagebox.showinfo(
            "Voice is locked",
            "The visual LISTENING and SPEAKING states are ready, but microphone and speech "
            "controls will activate only after GUI permission prompts are connected.",
            parent=self.root,
        )

    def _speak_latest(self) -> None:
        if self._busy or self.controller.speech_service is None:
            return
        reply = self.controller.latest_assistant_reply()
        if reply is None:
            from tkinter import messagebox

            messagebox.showinfo(
                "Nothing to speak",
                "Ask Ato something first, then select SPEAK LAST.",
                parent=self.root,
            )
            return
        self._busy = True
        self.state_model.transition(
            AtoVisualState.TOOL_EXECUTION,
            active_task="Awaiting speech permission",
            tool="OFFLINE WINDOWS SPEECH",
        )
        self.speak_button.configure(state="disabled")
        self.send_button.configure(state="disabled")
        threading.Thread(target=self._run_speech, args=(reply,), daemon=True).start()

    def _run_speech(self, reply: str) -> None:
        assert self.controller.speech_service is not None
        try:
            self.controller.speech_service.speak(
                reply,
                on_playback=lambda: self.state_model.transition(
                    AtoVisualState.SPEAKING,
                    active_task="Playing latest reply",
                    tool="OFFLINE WINDOWS SPEECH",
                ),
            )
        except AtoError as exc:
            self.root.after(0, self._finish_speech, str(exc))
        except Exception:
            self.root.after(0, self._finish_speech, "Speech playback failed safely.")
        else:
            self.root.after(0, self._finish_speech, None)

    def _finish_speech(self, error: str | None) -> None:
        self._busy = False
        self.state_model.transition(AtoVisualState.IDLE)
        self.send_button.configure(state="normal")
        self.speak_button.configure(state="normal")
        if error:
            from tkinter import messagebox

            messagebox.showerror("Ato speech", error, parent=self.root)

    def _refresh_hud(self) -> None:
        snapshot = self.state_model.snapshot()
        self.current_status.configure(text=snapshot.status)
        self.task_value.configure(text=snapshot.active_task)
        self.tool_value.configure(
            text=snapshot.tool
            or (
                "PERMISSION BRIDGE READY - TOOLS LOCKED"
                if self.permission_bridge is not None and self.permission_bridge.attached
                else "LOCKED - PERMISSION BRIDGE UNAVAILABLE"
            )
        )
        self.mode_status.configure(
            text=(
                "MODE\nASSISTANT\n\nSESSION\nACTIVE\n\n"
                f"MESSAGES\n{len(self.controller.agent.conversation)}"
            )
        )
        self.root.after(250, self._refresh_hud)

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
        self.state_model.transition(
            AtoVisualState.PROCESSING if busy else AtoVisualState.IDLE,
            active_task="Generating response" if busy else "Awaiting input",
        )
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
        permission_bridge = GuiPermissionBridge()
        speech_service = (
            DesktopSpeechService(
                WindowsSpeechPlayer(),
                PermissionManager(permission_bridge.confirm),
                AuditLogger(settings.audit_file),
            )
            if settings.voice_enabled
            else None
        )
        AtoDesktop(
            DesktopChatController(
                agent,
                memory_store,
                long_term_memory,
                knowledge_store,
                settings.workspace_root,
                speech_service,
            ),
            permission_bridge=permission_bridge,
        ).run()
    except (AtoError, ValueError) as exc:
        raise SystemExit(f"Unable to start Ato desktop: {exc}") from exc


if __name__ == "__main__":
    main()
