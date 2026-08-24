"""First native desktop shell for the shared Ato Agent Core."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ato.brain.agent import Agent
from ato.brain.context import ContextManager
from ato.brain.memory import CompositeMemoryRetriever
from ato.brain.messages import Role
from ato.brain.prompts import SYSTEM_PROMPT
from ato.coding import SqliteEditCheckpointStore
from ato.config import Settings
from ato.exceptions import AtoError
from ato.knowledge import SqliteKnowledgeStore
from ato.memory import JsonMemoryStore, SqliteLongTermMemory
from ato.providers import DeepSeekProvider
from ato.security import AuditLogger, PermissionManager
from ato.tools import build_phase3_registry
from ato.tools.search import BraveSearchClient, TavilySearchClient
from ato.tools.system import collect_system_info
from ato.tools.web import fetch_web_page
from ato.ui.activity import AuditActivityReader
from ato.ui.chat_format import ChatStyle, format_chat_content
from ato.ui.orb import AtoOrbCanvas
from ato.ui.palette import WorkspaceActionPalette
from ato.ui.permission_dialog import show_permission_dialog
from ato.ui.permissions import GuiPermissionBridge, GuiPermissionPrompt
from ato.ui.research import (
    DesktopResearchFetch,
    DesktopResearchSearch,
    ResearchPage,
    ResearchSearchResult,
    ResearchSource,
)
from ato.ui.settings_dialog import DesktopCapabilities, SettingsDialog
from ato.ui.speech import DesktopSpeechService
from ato.ui.state import AtoStateModel, AtoVisualState
from ato.ui.themes import ThemeId, alternate_theme, get_theme
from ato.ui.voice_turn import DesktopVoiceTurnService
from ato.ui.workspace import (
    DesktopWorkspaceSearch,
    WorkspaceChangePreview,
    WorkspaceChangeResult,
    WorkspaceCheckpoint,
    WorkspaceInspectionResult,
    WorkspaceRollbackResult,
    WorkspaceSearchResult,
)
from ato.voice import FasterWhisperTranscriber, SoundDeviceRecorder, WindowsSpeechPlayer

DESKTOP_SYSTEM_PROMPT = f"""{SYSTEM_PROMPT}

Desktop runtime constraint: the language model has no autonomous tools. User-triggered desktop
controls may separately perform approved voice, workspace, and research actions, but you must never
claim that you initiated them. Only a user-reviewed exact text preview can change a file; you cannot
run commands, mutate Git, access the clipboard, or perform other tool actions in this runtime. Treat
recollections of tool results from earlier turns as historical conversation, not evidence that an
action is available now."""


class DesktopStreamCancelled(AtoError):
    """Raised when the user cooperatively stops one desktop response stream."""


@dataclass(slots=True)
class DesktopChatController:
    """Small synchronous adapter kept independent from Tk for deterministic tests."""

    agent: Agent
    memory_store: JsonMemoryStore | None = None
    long_term_memory: SqliteLongTermMemory | None = None
    knowledge_store: SqliteKnowledgeStore | None = None
    workspace_root: Path | None = None
    speech_service: DesktopSpeechService | None = None
    voice_turn_service: DesktopVoiceTurnService | None = None
    workspace_search: DesktopWorkspaceSearch | None = None
    activity_reader: AuditActivityReader | None = None
    research_search: DesktopResearchSearch | None = None
    research_fetch: DesktopResearchFetch | None = None

    def submit(self, text: str) -> str:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Message cannot be empty.")
        reply = self.agent.respond(cleaned)
        if self.memory_store is not None:
            self.memory_store.save_context(self.agent.conversation, self.agent.summary)
        return reply

    def submit_stream(
        self, text: str, cancel_requested: Callable[[], bool] | None = None
    ) -> Iterator[str]:
        """Stream one turn and persist it only after successful completion."""
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Message cannot be empty.")
        stream = self.agent.respond_stream(cleaned)
        while True:
            if cancel_requested is not None and cancel_requested():
                stream.close()
                raise DesktopStreamCancelled("Response stopped by user.")
            try:
                fragment = next(stream)
            except StopIteration:
                break
            if cancel_requested is not None and cancel_requested():
                stream.close()
                raise DesktopStreamCancelled("Response stopped by user.")
            yield fragment
        if self.memory_store is not None:
            self.memory_store.save_context(self.agent.conversation, self.agent.summary)

    def latest_assistant_reply(self) -> str | None:
        return next(
            (
                message.content
                for message in reversed(self.agent.conversation)
                if message.role is Role.ASSISTANT
            ),
            None,
        )

    def submit_research_question(self, question: str, page: ResearchPage) -> str:
        cleaned = question.strip()
        if not cleaned:
            raise ValueError("Research question cannot be empty.")
        reply = self.agent.respond_with_external_evidence(
            cleaned,
            source_url=page.source_url,
            title=page.title,
            evidence=page.text,
        )
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

    def activity_snapshot(self) -> tuple[str, ...]:
        if self.activity_reader is None:
            return ("Audit activity is not configured.",)
        events = self.activity_reader.recent()
        if not events:
            return ("No audited tool activity yet.",)
        return tuple(event.display() for event in events)


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
        self._workspace_palette: WorkspaceActionPalette | None = None
        self._settings_dialog: SettingsDialog | None = None
        self._research_sources: tuple[ResearchSource, ...] = ()
        self._stream_fragments: list[str] = []
        self._stream_start = "ato_stream_start"
        self._stream_visible = False
        self._stream_active = False
        self._stream_cancel = threading.Event()
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
        for label in ("CHAT", "MEMORY", "KNOWLEDGE", "WORKSPACE", "RESEARCH", "ACTIVITY"):
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
                "● AGENT READY\n● MEMORY READY\n● VOICE READY\n○ OTHER TOOLS LOCKED"
                if self.controller.voice_turn_service is not None
                else "● AGENT READY\n● MEMORY READY\n● SPEECH READY\n○ OTHER TOOLS LOCKED"
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
                "CHAT + APPROVED VOICE"
                if self.controller.voice_turn_service is not None
                else "CHAT + APPROVED SPEECH"
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
            text="VOICE TURN" if self.controller.voice_turn_service is not None else "MIC LOCKED",
            command=(
                self._start_voice_turn
                if self.controller.voice_turn_service is not None
                else self._voice_locked
            ),
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
            fg=(
                theme.accent_secondary
                if self.controller.voice_turn_service is not None
                else theme.muted_text
            ),
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
        self._settings_dialog = SettingsDialog(
            self.root,
            self.theme,
            DesktopCapabilities(
                voice_input=self.controller.voice_turn_service is not None,
                voice_output=self.controller.speech_service is not None,
            ),
            fullscreen=self._fullscreen,
            on_toggle_theme=self._toggle_theme,
            on_toggle_fullscreen=lambda: self._toggle_fullscreen(),
        )

    def _ask_permission(self, prompt: GuiPermissionPrompt) -> bool:
        """Display one redacted tool request on Tk's UI thread."""
        return show_permission_dialog(self.root, self.theme, prompt)

    def _close(self) -> None:
        if self._settings_dialog is not None:
            self._settings_dialog.close()
        if self._workspace_palette is not None:
            self._workspace_palette.close()
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

    def _start_voice_turn(self) -> None:
        if self._busy or self.controller.voice_turn_service is None:
            return
        from tkinter import simpledialog

        duration = simpledialog.askinteger(
            "Ato voice turn",
            "Recording duration in seconds (1-120):",
            parent=self.root,
            minvalue=1,
            maxvalue=120,
            initialvalue=5,
        )
        if duration is None:
            return
        self._busy = True
        self.state_model.transition(
            AtoVisualState.TOOL_EXECUTION,
            active_task="Awaiting microphone permission",
            tool="MICROPHONE RECORDING",
        )
        self.microphone_button.configure(state="disabled")
        self.speak_button.configure(state="disabled")
        self.send_button.configure(state="disabled")
        threading.Thread(target=self._run_voice_turn, args=(duration,), daemon=True).start()

    def _run_voice_turn(self, duration: int) -> None:
        assert self.controller.voice_turn_service is not None
        try:
            transcript = self.controller.voice_turn_service.capture(
                duration,
                on_recording=lambda: self.state_model.transition(
                    AtoVisualState.LISTENING,
                    active_task=f"Recording {duration}-second voice turn",
                    tool="MICROPHONE RECORDING",
                ),
                on_transcription_request=lambda: self.state_model.transition(
                    AtoVisualState.TOOL_EXECUTION,
                    active_task="Awaiting transcription permission",
                    tool="LOCAL TRANSCRIPTION",
                ),
                on_transcribing=lambda: self.state_model.transition(
                    AtoVisualState.PROCESSING,
                    active_task="Transcribing recording locally",
                    tool="LOCAL TRANSCRIPTION",
                ),
            )
        except AtoError as exc:
            self.root.after(0, self._finish_voice_turn, None, str(exc))
        except Exception:
            self.root.after(0, self._finish_voice_turn, None, "Voice turn failed safely.")
        else:
            self.root.after(0, self._finish_voice_turn, transcript, None)

    def _finish_voice_turn(self, transcript: str | None, error: str | None) -> None:
        self._busy = False
        self.state_model.transition(AtoVisualState.IDLE)
        self.send_button.configure(state="normal")
        self.speak_button.configure(
            state="normal" if self.controller.speech_service is not None else "disabled"
        )
        self.microphone_button.configure(state="normal")
        if error:
            from tkinter import messagebox

            messagebox.showerror("Ato voice turn", error, parent=self.root)
            return
        assert transcript is not None
        self.input.delete("1.0", "end")
        self.input.insert("1.0", transcript)
        self.input.focus_set()
        from tkinter import messagebox

        messagebox.showinfo(
            "Review voice transcript",
            "The local transcript is now in the message box. Review or edit it, then select SEND. "
            "Ato has not submitted it automatically.",
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
                "GUARDED WORKSPACE TOOLS READY"
                if self.controller.workspace_search is not None
                else "PERMISSION BRIDGE READY - TOOLS LOCKED"
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
        self._stream_visible = False
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
            elif section == "WORKSPACE":
                lines = (
                    "Choose LIST, READ, SEARCH, STATUS, DIFF, STAGED, LOG, BRANCHES, SYNTAX, "
                    "LINT, TESTS, PREVIEW, CHECKPOINTS, or ROLLBACK. All actions are bounded.",
                )
            elif section == "RESEARCH":
                lines = (
                    ("Choose a query to search the public web with confirmation.",)
                    if self.controller.research_search is not None
                    else ("Web search is not configured. Add a Tavily or Brave API key.",)
                )
            elif section == "ACTIVITY":
                lines = self.controller.activity_snapshot()
            else:
                lines = ("This desktop section is unavailable.",)
            self._show_read_only_lines(lines)
            if section == "WORKSPACE":
                self.root.after(0, self._start_workspace_search)
            elif section == "RESEARCH" and self.controller.research_search is not None:
                self.root.after(0, self._start_research_search)
        self._apply_theme()

    def _clear_transcript(self) -> None:
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.configure(state="disabled")

    def _start_workspace_search(self) -> None:
        if self._busy or self.controller.workspace_search is None:
            return
        self._workspace_palette = WorkspaceActionPalette(
            self.root, self.theme, self._dispatch_workspace_action
        )

    def _dispatch_workspace_action(self, normalized: str) -> None:
        from tkinter import simpledialog

        if normalized == "checkpoints":
            self._start_checkpoint_listing()
            return
        if normalized == "rollback":
            self._start_checkpoint_rollback_dialog()
            return
        if normalized == "preview":
            self._start_change_preview_dialog()
            return
        if normalized in {"list", "read"}:
            prompt = (
                "Relative directory path (blank for project root):"
                if normalized == "list"
                else "Relative UTF-8 text file path:"
            )
            path = simpledialog.askstring(
                "List workspace files" if normalized == "list" else "Read text file",
                prompt,
                parent=self.root,
            )
            if path is not None and (normalized == "list" or path.strip()):
                self._start_file_inspection(normalized, path)
            return
        if normalized == "syntax":
            path = simpledialog.askstring(
                "Python syntax check",
                "Relative .py file path:",
                parent=self.root,
            )
            if path is not None and path.strip():
                self._start_code_inspection("syntax", path)
            return
        if normalized in {"lint", "tests"}:
            self._start_code_inspection(normalized)
            return
        if normalized != "search":
            if normalized not in {"status", "diff", "staged", "log", "branches"}:
                from tkinter import messagebox

                messagebox.showerror("Workspace", "Unknown read-only action.", parent=self.root)
                return
            self._start_git_inspection(normalized)
            return
        query = simpledialog.askstring(
            "Search workspace", "Literal text to find:", parent=self.root
        )
        if query is None or not query.strip():
            return
        self._busy = True
        self.state_model.transition(
            AtoVisualState.TOOL_EXECUTION,
            active_task=f"Searching workspace for {query[:60]!r}",
            tool="READ-ONLY FILE SEARCH",
        )
        threading.Thread(target=self._run_workspace_search, args=(query,), daemon=True).start()

    def _start_checkpoint_listing(self) -> None:
        self._busy = True
        self.state_model.transition(
            AtoVisualState.TOOL_EXECUTION,
            active_task="Listing recoverable edit checkpoints",
            tool="READ-ONLY CHECKPOINT LIST",
        )
        threading.Thread(target=self._run_checkpoint_listing, daemon=True).start()

    def _run_checkpoint_listing(self) -> None:
        assert self.controller.workspace_search is not None
        try:
            checkpoints = self.controller.workspace_search.list_checkpoints()
        except AtoError as exc:
            self.root.after(0, self._finish_checkpoint_listing, None, str(exc))
        except Exception:
            self.root.after(0, self._finish_checkpoint_listing, None, "Listing failed safely.")
        else:
            self.root.after(0, self._finish_checkpoint_listing, checkpoints, None)

    def _finish_checkpoint_listing(
        self,
        checkpoints: tuple[WorkspaceCheckpoint, ...] | None,
        error: str | None,
    ) -> None:
        self._busy = False
        self.state_model.transition(AtoVisualState.IDLE)
        if self._section != "WORKSPACE":
            return
        self._clear_transcript()
        if error:
            self._show_read_only_lines((f"CHECKPOINT ERROR\n{error}",))
            return
        assert checkpoints is not None
        lines = tuple(item.display() for item in checkpoints) or ("No edit checkpoints found.",)
        self._show_read_only_lines(("RECENT EDIT CHECKPOINTS", *lines))

    def _start_checkpoint_rollback_dialog(self) -> None:
        assert self.controller.workspace_search is not None
        from tkinter import messagebox, simpledialog

        checkpoint_id = simpledialog.askinteger(
            "Rollback text edit",
            "Reviewed checkpoint ID (run CHECKPOINTS first):",
            parent=self.root,
            minvalue=1,
        )
        if checkpoint_id is None:
            return
        checkpoint = self.controller.workspace_search.reviewed_checkpoints.get(checkpoint_id)
        if checkpoint is None:
            messagebox.showerror(
                "Rollback text edit",
                "That checkpoint was not present in the most recent CHECKPOINTS view.",
                parent=self.root,
            )
            return
        proceed = messagebox.askyesno(
            "Rollback reviewed checkpoint?",
            f"Checkpoint: #{checkpoint.id}\nPath: {checkpoint.path}\n"
            f"Restore SHA-256: {checkpoint.original_sha256}\n\n"
            "Rollback will be refused if newer work exists.",
            icon="warning",
            parent=self.root,
        )
        if not proceed:
            return
        self._busy = True
        self.state_model.transition(
            AtoVisualState.TOOL_EXECUTION,
            active_task=f"Awaiting HIGH permission for checkpoint #{checkpoint.id}",
            tool="CHECKPOINT ROLLBACK",
        )
        threading.Thread(
            target=self._run_checkpoint_rollback,
            args=(checkpoint.id,),
            daemon=True,
        ).start()

    def _run_checkpoint_rollback(self, checkpoint_id: int) -> None:
        assert self.controller.workspace_search is not None
        try:
            result = self.controller.workspace_search.rollback_checkpoint(checkpoint_id)
        except AtoError as exc:
            self.root.after(0, self._finish_checkpoint_rollback, None, str(exc))
        except Exception:
            self.root.after(0, self._finish_checkpoint_rollback, None, "Rollback failed safely.")
        else:
            self.root.after(0, self._finish_checkpoint_rollback, result, None)

    def _finish_checkpoint_rollback(
        self, result: WorkspaceRollbackResult | None, error: str | None
    ) -> None:
        self._busy = False
        self.state_model.transition(AtoVisualState.IDLE)
        if self._section != "WORKSPACE":
            return
        if error:
            self._show_read_only_lines((f"ROLLBACK ERROR\n{error}",))
            return
        assert result is not None
        self._show_read_only_lines(
            (
                f"CHECKPOINT #{result.checkpoint_id} RESTORED\n{result.path}\n"
                f"Restored SHA-256: {result.restored_sha256}",
            )
        )

    def _start_change_preview_dialog(self) -> None:
        from tkinter import simpledialog

        path = simpledialog.askstring(
            "Preview exact text change",
            "Relative text file path:",
            parent=self.root,
        )
        if path is None or not path.strip():
            return
        old_text = simpledialog.askstring(
            "Preview exact text change",
            "Exact existing text (must occur once):",
            parent=self.root,
        )
        if old_text is None or not old_text:
            return
        new_text = simpledialog.askstring(
            "Preview exact text change",
            "Replacement text (blank deletes the exact match):",
            parent=self.root,
        )
        if new_text is None:
            return
        self._busy = True
        self.state_model.transition(
            AtoVisualState.TOOL_EXECUTION,
            active_task="Generating read-only exact-change preview",
            tool="READ-ONLY CHANGE PREVIEW",
        )
        threading.Thread(
            target=self._run_change_preview,
            args=(path, old_text, new_text),
            daemon=True,
        ).start()

    def _run_change_preview(self, path: str, old_text: str, new_text: str) -> None:
        assert self.controller.workspace_search is not None
        try:
            preview = self.controller.workspace_search.preview_text_change(
                path, old_text, new_text
            )
        except AtoError as exc:
            self.root.after(0, self._finish_change_preview, None, str(exc))
        except Exception:
            self.root.after(0, self._finish_change_preview, None, "Change preview failed safely.")
        else:
            self.root.after(0, self._finish_change_preview, preview, None)

    def _finish_change_preview(
        self, preview: WorkspaceChangePreview | None, error: str | None
    ) -> None:
        self._busy = False
        self.state_model.transition(AtoVisualState.IDLE)
        if self._section != "WORKSPACE":
            return
        self._clear_transcript()
        if error:
            self._show_read_only_lines((f"PREVIEW ERROR\n{error}",))
            return
        assert preview is not None
        heading = (
            f"READ-ONLY CHANGE PREVIEW\n{preview.path}\n"
            f"ORIGINAL SHA-256  {preview.original_sha256}\n"
            f"UPDATED SHA-256   {preview.updated_sha256}\n"
            "NO FILE WAS MODIFIED."
            + ("\nDIFF TRUNCATED" if preview.truncated else "")
        )
        self._show_read_only_lines((heading, preview.diff or "No visible diff."))
        if preview.truncated:
            self._show_read_only_lines(("APPLY DISABLED: the complete diff was not displayed.",))
        else:
            self.root.after(0, self._offer_change_apply, preview)

    def _offer_change_apply(self, preview: WorkspaceChangePreview) -> None:
        if self._busy or self._section != "WORKSPACE":
            return
        from tkinter import messagebox

        proceed = messagebox.askyesno(
            "Apply reviewed change?",
            "Proceed to the protected HIGH-confirmation step for this exact preview?\n\n"
            f"Path: {preview.path}\n"
            f"Original SHA-256: {preview.original_sha256}\n\n"
            "The change will be rejected if the file has changed since preview.",
            icon="warning",
            parent=self.root,
        )
        if not proceed:
            return
        self._busy = True
        self.state_model.transition(
            AtoVisualState.TOOL_EXECUTION,
            active_task="Awaiting HIGH permission for reviewed edit",
            tool="ATOMIC EXACT TEXT REPLACEMENT",
        )
        threading.Thread(target=self._run_change_apply, args=(preview,), daemon=True).start()

    def _run_change_apply(self, preview: WorkspaceChangePreview) -> None:
        assert self.controller.workspace_search is not None
        try:
            result = self.controller.workspace_search.apply_text_change(preview)
        except AtoError as exc:
            self.root.after(0, self._finish_change_apply, None, str(exc))
        except Exception:
            self.root.after(0, self._finish_change_apply, None, "Text change failed safely.")
        else:
            self.root.after(0, self._finish_change_apply, result, None)

    def _finish_change_apply(
        self, result: WorkspaceChangeResult | None, error: str | None
    ) -> None:
        self._busy = False
        self.state_model.transition(AtoVisualState.IDLE)
        if self._section != "WORKSPACE":
            return
        if error:
            self._show_read_only_lines((f"APPLY ERROR\n{error}",))
            return
        assert result is not None
        checkpoint = (
            f"Rollback checkpoint: #{result.checkpoint_id}"
            if result.checkpoint_id is not None
            else "Rollback checkpoint: unavailable"
        )
        self._show_read_only_lines(
            (
                "CHANGE APPLIED\n"
                f"{result.path}\n{result.bytes_written} bytes\n"
                f"Updated SHA-256: {result.updated_sha256}\n{checkpoint}",
            )
        )

    def _start_file_inspection(self, action: str, path: str) -> None:
        self._busy = True
        self.state_model.transition(
            AtoVisualState.TOOL_EXECUTION,
            active_task="Listing workspace files" if action == "list" else "Reading text file",
            tool="READ-ONLY FILE LIST" if action == "list" else "READ-ONLY TEXT VIEW",
        )
        threading.Thread(
            target=self._run_file_inspection,
            args=(action, path),
            daemon=True,
        ).start()

    def _run_file_inspection(self, action: str, path: str) -> None:
        assert self.controller.workspace_search is not None
        try:
            result = (
                self.controller.workspace_search.list_files(path)
                if action == "list"
                else self.controller.workspace_search.read_text_file(path)
            )
        except AtoError as exc:
            self.root.after(0, self._finish_file_inspection, None, str(exc))
        except Exception:
            self.root.after(0, self._finish_file_inspection, None, "File inspection failed safely.")
        else:
            self.root.after(0, self._finish_file_inspection, result, None)

    def _finish_file_inspection(
        self, result: WorkspaceInspectionResult | None, error: str | None
    ) -> None:
        self._busy = False
        self.state_model.transition(AtoVisualState.IDLE)
        if self._section != "WORKSPACE":
            return
        self._clear_transcript()
        if error:
            self._show_read_only_lines((f"FILE INSPECTION ERROR\n{error}",))
            return
        assert result is not None
        heading = result.label + ("\nDISPLAY OUTPUT TRUNCATED" if result.truncated else "")
        self._show_read_only_lines((heading, result.text))

    def _start_code_inspection(self, action: str, path: str | None = None) -> None:
        self._busy = True
        label = "PYTHON SYNTAX" if action == "syntax" else action.upper()
        self.state_model.transition(
            AtoVisualState.TOOL_EXECUTION,
            active_task=f"Running fixed {label.casefold()} verification",
            tool=f"CODE CHECK - {label}",
        )
        threading.Thread(
            target=self._run_code_inspection,
            args=(action, path),
            daemon=True,
        ).start()

    def _run_code_inspection(self, action: str, path: str | None) -> None:
        assert self.controller.workspace_search is not None
        try:
            result = (
                self.controller.workspace_search.check_syntax(path or "")
                if action == "syntax"
                else self.controller.workspace_search.run_code_check(action)
            )
        except AtoError as exc:
            self.root.after(0, self._finish_code_inspection, None, str(exc))
        except Exception:
            self.root.after(0, self._finish_code_inspection, None, "Code check failed safely.")
        else:
            self.root.after(0, self._finish_code_inspection, result, None)

    def _finish_code_inspection(
        self, result: WorkspaceInspectionResult | None, error: str | None
    ) -> None:
        self._busy = False
        self.state_model.transition(AtoVisualState.IDLE)
        if self._section != "WORKSPACE":
            return
        self._clear_transcript()
        if error:
            self._show_read_only_lines((f"CODE CHECK ERROR\n{error}",))
            return
        assert result is not None
        heading = result.label + ("\nDISPLAY OUTPUT TRUNCATED" if result.truncated else "")
        self._show_read_only_lines((heading, result.text))

    def _start_git_inspection(self, action: str) -> None:
        self._busy = True
        self.state_model.transition(
            AtoVisualState.TOOL_EXECUTION,
            active_task=f"Inspecting Git {action}",
            tool=f"READ-ONLY GIT {action.upper()}",
        )
        threading.Thread(target=self._run_git_inspection, args=(action,), daemon=True).start()

    def _run_git_inspection(self, action: str) -> None:
        assert self.controller.workspace_search is not None
        try:
            result = self.controller.workspace_search.inspect_git(action)
        except AtoError as exc:
            self.root.after(0, self._finish_git_inspection, None, str(exc))
        except Exception:
            self.root.after(0, self._finish_git_inspection, None, "Git inspection failed safely.")
        else:
            self.root.after(0, self._finish_git_inspection, result, None)

    def _finish_git_inspection(
        self, result: WorkspaceInspectionResult | None, error: str | None
    ) -> None:
        self._busy = False
        self.state_model.transition(AtoVisualState.IDLE)
        if self._section != "WORKSPACE":
            return
        self._clear_transcript()
        if error:
            self._show_read_only_lines((f"GIT ERROR\n{error}",))
            return
        assert result is not None
        heading = result.label + ("\nDISPLAY OUTPUT TRUNCATED" if result.truncated else "")
        self._show_read_only_lines((heading, result.text))

    def _run_workspace_search(self, query: str) -> None:
        assert self.controller.workspace_search is not None
        try:
            result = self.controller.workspace_search.search(query)
        except AtoError as exc:
            self.root.after(0, self._finish_workspace_search, None, str(exc))
        except Exception:
            self.root.after(
                0, self._finish_workspace_search, None, "Workspace search failed safely."
            )
        else:
            self.root.after(0, self._finish_workspace_search, result, None)

    def _finish_workspace_search(
        self, result: WorkspaceSearchResult | None, error: str | None
    ) -> None:
        self._busy = False
        self.state_model.transition(AtoVisualState.IDLE)
        if self._section != "WORKSPACE":
            return
        self._clear_transcript()
        if error:
            self._show_read_only_lines((f"SEARCH ERROR\n{error}",))
            return
        assert result is not None
        self._research_sources = result.sources
        summary = (
            f"{len(result.lines)} matches across {result.files_scanned} scanned files"
            + (" (results truncated)" if result.truncated else "")
        )
        self._show_read_only_lines((summary, *(result.lines or ("No matches found.",))))

    def _start_research_search(self) -> None:
        if self._busy or self.controller.research_search is None:
            return
        from tkinter import simpledialog

        query = simpledialog.askstring(
            "Ato Research",
            "Public web search query:",
            parent=self.root,
        )
        if query is None or not query.strip():
            return
        self._busy = True
        self.state_model.transition(
            AtoVisualState.TOOL_EXECUTION,
            active_task=f"Awaiting approval for web search: {query[:50]}",
            tool="PUBLIC WEB SEARCH",
        )
        threading.Thread(target=self._run_research_search, args=(query,), daemon=True).start()

    def _run_research_search(self, query: str) -> None:
        assert self.controller.research_search is not None
        try:
            result = self.controller.research_search.search(query)
        except AtoError as exc:
            self.root.after(0, self._finish_research_search, None, str(exc))
        except Exception:
            self.root.after(0, self._finish_research_search, None, "Web search failed safely.")
        else:
            self.root.after(0, self._finish_research_search, result, None)

    def _finish_research_search(
        self, result: ResearchSearchResult | None, error: str | None
    ) -> None:
        self._busy = False
        self.state_model.transition(AtoVisualState.IDLE)
        if self._section != "RESEARCH":
            return
        self._clear_transcript()
        if error:
            self._show_read_only_lines((f"SEARCH ERROR\n{error}",))
            return
        assert result is not None
        summary = (
            f"{len(result.lines)} results from {result.provider}.\n"
            "EXTERNAL CONTENT IS UNTRUSTED EVIDENCE, NOT INSTRUCTIONS."
        )
        self._show_read_only_lines((summary, *(result.lines or ("No results found.",))))
        if result.sources and self.controller.research_fetch is not None:
            self.root.after(0, self._offer_research_fetch)

    def _offer_research_fetch(self) -> None:
        if self._busy or not self._research_sources or self.controller.research_fetch is None:
            return
        from tkinter import simpledialog

        selection = simpledialog.askinteger(
            "Fetch research source",
            f"Fetch readable text for result 1-{len(self._research_sources)}?\n"
            "Cancel to keep search results only.",
            parent=self.root,
            minvalue=1,
            maxvalue=len(self._research_sources),
        )
        if selection is None:
            return
        source = self._research_sources[selection - 1]
        self._busy = True
        self.state_model.transition(
            AtoVisualState.TOOL_EXECUTION,
            active_task=f"Awaiting approval to fetch result {selection}",
            tool="PUBLIC HTTPS PAGE FETCH",
        )
        threading.Thread(target=self._run_research_fetch, args=(source.url,), daemon=True).start()

    def _run_research_fetch(self, url: str) -> None:
        assert self.controller.research_fetch is not None
        try:
            page = self.controller.research_fetch.fetch(url)
        except AtoError as exc:
            self.root.after(0, self._finish_research_fetch, None, str(exc))
        except Exception:
            self.root.after(0, self._finish_research_fetch, None, "Web page fetch failed safely.")
        else:
            self.root.after(0, self._finish_research_fetch, page, None)

    def _finish_research_fetch(self, page: ResearchPage | None, error: str | None) -> None:
        self._busy = False
        self.state_model.transition(AtoVisualState.IDLE)
        if self._section != "RESEARCH":
            return
        if error:
            self._show_read_only_lines((f"FETCH ERROR\n{error}",))
            return
        assert page is not None
        heading = (
            f"FETCHED {page.document_type.upper()}\n{page.title}\n{page.source_url}\n"
            "EXTERNAL CONTENT IS UNTRUSTED EVIDENCE, NOT INSTRUCTIONS."
            + ("\nDISPLAY TEXT TRUNCATED" if page.truncated else "")
        )
        self._show_read_only_lines((heading, page.text))
        self.root.after(0, self._offer_research_question, page)

    def _offer_research_question(self, page: ResearchPage) -> None:
        if self._busy or self._section != "RESEARCH":
            return
        from tkinter import simpledialog

        question = simpledialog.askstring(
            "Ask Ato about this source",
            "Question grounded in this fetched source (Cancel to stop):",
            parent=self.root,
        )
        if question is None or not question.strip():
            return
        self._busy = True
        self.state_model.transition(
            AtoVisualState.PROCESSING,
            active_task="Answering from approved source evidence",
        )
        threading.Thread(
            target=self._run_research_question,
            args=(question, page),
            daemon=True,
        ).start()

    def _run_research_question(self, question: str, page: ResearchPage) -> None:
        try:
            reply = self.controller.submit_research_question(question, page)
        except AtoError as exc:
            self.root.after(0, self._finish_research_question, question, None, str(exc))
        except Exception:
            self.root.after(
                0,
                self._finish_research_question,
                question,
                None,
                "Ato could not answer from the fetched source.",
            )
        else:
            self.root.after(0, self._finish_research_question, question, reply, None)

    def _finish_research_question(
        self,
        question: str,
        reply: str | None,
        error: str | None,
    ) -> None:
        self._busy = False
        self.state_model.transition(AtoVisualState.IDLE)
        if self._section != "RESEARCH":
            return
        self._show_read_only_lines(
            (
                f"QUESTION\n{question}",
                f"ATO\n{reply}" if reply is not None else f"ANSWER ERROR\n{error}",
            )
        )

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
        if self._stream_active:
            self._cancel_stream()
            return
        if self._busy or not text:
            return
        self.input.delete("1.0", "end")
        self._append("You", text)
        self._set_busy(True)
        self._begin_stream_response()
        threading.Thread(target=self._request_reply, args=(text,), daemon=True).start()

    def _begin_stream_response(self) -> None:
        self._stream_fragments.clear()
        self._stream_cancel.clear()
        self._stream_active = True
        self.send_button.configure(text="STOP", state="normal")
        self._stream_visible = self._section == "CHAT"
        if not self._stream_visible:
            return
        self.transcript.configure(state="normal")
        self.transcript.mark_set(self._stream_start, "end-1c")
        self.transcript.mark_gravity(self._stream_start, "left")
        self.transcript.insert("end", "ATO\n", "role_ato")
        self.transcript.configure(state="disabled")
        self.transcript.see("end")

    def _request_reply(self, text: str) -> None:
        try:
            for fragment in self.controller.submit_stream(text, self._stream_cancel.is_set):
                self.root.after(0, self._append_stream_fragment, fragment)
        except DesktopStreamCancelled:
            self.root.after(0, self._finish_stream_request, None, True)
        except AtoError as exc:
            self.root.after(0, self._finish_stream_request, str(exc), False)
        except Exception:
            self.root.after(
                0,
                self._finish_stream_request,
                "Ato encountered an unexpected error.",
                False,
            )
        else:
            self.root.after(0, self._finish_stream_request, None, False)

    def _cancel_stream(self) -> None:
        self._stream_cancel.set()
        self.send_button.configure(text="STOPPING...", state="disabled")
        self.state_model.transition(
            AtoVisualState.PROCESSING,
            active_task="Stopping response safely",
        )

    def _append_stream_fragment(self, fragment: str) -> None:
        self._stream_fragments.append(fragment)
        if self._stream_visible and self._section == "CHAT":
            self.transcript.configure(state="normal")
            self.transcript.insert("end", fragment, ChatStyle.BODY.value)
            self.transcript.configure(state="disabled")
            self.transcript.see("end")

    def _finish_stream_request(self, error: str | None, cancelled: bool) -> None:
        reply = "".join(self._stream_fragments).strip()
        if self._section == "CHAT":
            if self._stream_visible:
                self.transcript.configure(state="normal")
                self.transcript.delete(self._stream_start, "end")
                self.transcript.configure(state="disabled")
            if cancelled:
                self._append("System", "Response stopped. Partial output was discarded.")
            else:
                self._append("Error" if error else "Ato", error or reply or "Unknown error")
        self._stream_fragments.clear()
        self._stream_visible = False
        self._stream_active = False
        self._stream_cancel.clear()
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.state_model.transition(
            AtoVisualState.PROCESSING if busy else AtoVisualState.IDLE,
            active_task="Generating response" if busy else "Awaiting input",
        )
        self.send_button.configure(
            text="STOP" if self._stream_active else "SEND",
            state="normal" if self._stream_active or not busy else "disabled",
        )
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
        voice_turn_service = (
            DesktopVoiceTurnService(
                SoundDeviceRecorder(settings.workspace_root / "data" / "audio"),
                FasterWhisperTranscriber(settings.stt_model_path),
                PermissionManager(permission_bridge.confirm),
                AuditLogger(settings.audit_file),
                settings.workspace_root,
            )
            if settings.voice_enabled and settings.stt_model_path is not None
            else None
        )
        workspace_search = DesktopWorkspaceSearch(
            build_phase3_registry(
                settings.workspace_root,
                PermissionManager(permission_bridge.confirm),
                AuditLogger(settings.audit_file),
                checkpoint_store=SqliteEditCheckpointStore(settings.edit_checkpoint_file),
            )
        )
        activity_reader = AuditActivityReader(settings.audit_file)
        web_searcher = (
            TavilySearchClient(settings.tavily_api_key)
            if settings.tavily_api_key
            else BraveSearchClient(settings.brave_search_api_key)
            if settings.brave_search_api_key
            else None
        )
        research_search = (
            DesktopResearchSearch(
                web_searcher,
                PermissionManager(permission_bridge.confirm),
                AuditLogger(settings.audit_file),
            )
            if web_searcher is not None
            else None
        )
        research_fetch = DesktopResearchFetch(
            fetch_web_page,
            PermissionManager(permission_bridge.confirm),
            AuditLogger(settings.audit_file),
        )
        AtoDesktop(
            DesktopChatController(
                agent,
                memory_store,
                long_term_memory,
                knowledge_store,
                settings.workspace_root,
                speech_service,
                voice_turn_service,
                workspace_search,
                activity_reader,
                research_search,
                research_fetch,
            ),
            permission_bridge=permission_bridge,
        ).run()
    except (AtoError, ValueError) as exc:
        raise SystemExit(f"Unable to start Ato desktop: {exc}") from exc


if __name__ == "__main__":
    main()
