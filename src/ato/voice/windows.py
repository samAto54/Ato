"""Offline Windows text-to-speech playback."""

from __future__ import annotations

import subprocess

from ato.exceptions import ToolError
from ato.voice.base import validate_synthesis_text


class WindowsSpeechPlayer:
    def speak(self, text: str) -> None:
        text = validate_synthesis_text(text)
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.Rate=0; $s.Volume=100; $s.Speak([Console]::In.ReadToEnd()); $s.Dispose()"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                input=text,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError("Speech playback exceeded its 120-second timeout.") from exc
        except OSError as exc:
            raise ToolError("Windows speech playback could not be started.") from exc
        if result.returncode != 0:
            raise ToolError("Windows speech playback failed.")
