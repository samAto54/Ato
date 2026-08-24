"""Explicit, audited microphone-to-draft workflow for the desktop UI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ato.exceptions import ToolError
from ato.security import (
    AuditLogger,
    PermissionDecision,
    PermissionLevel,
    PermissionManager,
    PermissionRequest,
)
from ato.tools.builtin import WorkspaceBoundary
from ato.voice import FileTranscriber, MicrophoneRecorder
from ato.voice.microphone import MAX_RECORDING_SECONDS
from ato.voice.whisper import MAX_TRANSCRIPT_CHARS


@dataclass(slots=True)
class DesktopVoiceTurnService:
    """Record and locally transcribe once, without submitting the resulting text."""

    recorder: MicrophoneRecorder
    transcriber: FileTranscriber
    permission_manager: PermissionManager
    audit_logger: AuditLogger
    workspace_root: Path

    def capture(
        self,
        duration_seconds: int,
        *,
        on_recording: Callable[[], None] | None = None,
        on_audio_level: Callable[[float], None] | None = None,
        on_transcription_request: Callable[[], None] | None = None,
        on_transcribing: Callable[[], None] | None = None,
    ) -> str:
        if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, int):
            raise ToolError("Recording duration must be a whole number of seconds.")
        if not 1 <= duration_seconds <= MAX_RECORDING_SECONDS:
            raise ToolError("Recording duration must be between 1 and 120 seconds.")
        self.audit_logger.ensure_ready()
        record_arguments = {"duration_seconds": duration_seconds}
        record_decision = self.permission_manager.authorize(
            PermissionRequest(
                "record_microphone",
                PermissionLevel.CRITICAL,
                record_arguments,
                "Record one desktop voice turn",
            )
        )
        if record_decision is PermissionDecision.DENY:
            self._audit(
                "record_microphone",
                record_arguments,
                PermissionLevel.CRITICAL,
                record_decision,
                error="User denied permission.",
            )
            raise ToolError("Permission denied for microphone recording.")
        try:
            if on_recording is not None:
                on_recording()
            if on_audio_level is None:
                recording = self.recorder.record(duration_seconds).resolve()
            else:
                recording = self.recorder.record(
                    duration_seconds, on_level=on_audio_level
                ).resolve()
            relative_path = self._validate_recording(recording)
        except ToolError as exc:
            self._audit(
                "record_microphone",
                record_arguments,
                PermissionLevel.CRITICAL,
                record_decision,
                error=str(exc),
            )
            raise
        except Exception as exc:
            self._audit(
                "record_microphone",
                record_arguments,
                PermissionLevel.CRITICAL,
                record_decision,
                error="Microphone recording failed safely.",
            )
            raise ToolError("Microphone recording failed safely.") from exc
        self._audit(
            "record_microphone",
            record_arguments,
            PermissionLevel.CRITICAL,
            record_decision,
            result=f"Recorded {relative_path}",
        )

        if on_transcription_request is not None:
            on_transcription_request()
        transcribe_arguments = {"path": relative_path}
        transcribe_decision = self.permission_manager.authorize(
            PermissionRequest(
                "transcribe_audio",
                PermissionLevel.HIGH,
                transcribe_arguments,
                "Transcribe the approved desktop recording locally",
            )
        )
        if transcribe_decision is PermissionDecision.DENY:
            self._audit(
                "transcribe_audio",
                transcribe_arguments,
                PermissionLevel.HIGH,
                transcribe_decision,
                error="User denied permission.",
            )
            raise ToolError("Permission denied for local transcription.")
        try:
            if on_transcribing is not None:
                on_transcribing()
            transcript = self.transcriber.transcribe_file(recording).strip()
            if not transcript:
                raise ToolError("Offline transcription produced no text.")
            if len(transcript) > MAX_TRANSCRIPT_CHARS:
                raise ToolError("Transcript exceeds the 20,000-character limit.")
            if "\x00" in transcript:
                raise ToolError("Transcript contains unsupported control characters.")
        except ToolError as exc:
            self._audit(
                "transcribe_audio",
                transcribe_arguments,
                PermissionLevel.HIGH,
                transcribe_decision,
                error=str(exc),
            )
            raise
        except Exception as exc:
            self._audit(
                "transcribe_audio",
                transcribe_arguments,
                PermissionLevel.HIGH,
                transcribe_decision,
                error="Offline transcription failed safely.",
            )
            raise ToolError("Offline transcription failed safely.") from exc
        self._audit(
            "transcribe_audio",
            transcribe_arguments,
            PermissionLevel.HIGH,
            transcribe_decision,
            result="Local transcription completed.",
        )
        return transcript

    def _validate_recording(self, recording: Path) -> str:
        boundary = WorkspaceBoundary(self.workspace_root)
        audio_root = (boundary.root / "data" / "audio").resolve()
        try:
            relative_audio = recording.relative_to(audio_root)
        except ValueError as exc:
            raise ToolError("Microphone provider returned an unsafe recording path.") from exc
        if recording.suffix.casefold() != ".wav" or not recording.is_file():
            raise ToolError("Microphone provider did not return a valid WAV recording.")
        return (Path("data") / "audio" / relative_audio).as_posix()

    def _audit(
        self,
        tool_name: str,
        arguments: dict[str, object],
        permission: PermissionLevel,
        decision: PermissionDecision,
        *,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        self.audit_logger.record(
            user_request="Desktop voice turn",
            tool_name=tool_name,
            arguments=arguments,
            permission=permission,
            decision=decision,
            result=result,
            error=error,
        )
