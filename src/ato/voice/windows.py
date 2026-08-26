"""Offline Windows text-to-speech playback."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from threading import Timer

from ato.exceptions import ToolError
from ato.voice.base import validate_synthesis_text


class WindowsSpeechPlayer:
    def speak(self, text: str, *, on_level: Callable[[float], None] | None = None) -> None:
        text = validate_synthesis_text(text)
        if on_level is not None:
            self._speak_with_progress(text, on_level)
            return
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

    def _speak_with_progress(self, text: str, on_level: Callable[[float], None]) -> None:
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.Rate=0; $s.Volume=100; "
            "$s.add_SpeakProgress({param($sender,$e) "
            "$level=[Math]::Min(100,35+$e.Text.Length*9); "
            "[Console]::Out.WriteLine(('ATO_LEVEL:{0}' -f $level)); "
            "[Console]::Out.Flush()}); "
            "$s.Speak([Console]::In.ReadToEnd()); $s.Dispose()"
        )
        try:
            process = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                shell=False,
            )
        except OSError as exc:
            raise ToolError("Windows speech playback could not be started.") from exc
        timer = Timer(120, process.kill)
        timer.daemon = True
        timer.start()
        try:
            assert process.stdin is not None
            assert process.stdout is not None
            process.stdin.write(text)
            process.stdin.close()
            for line in process.stdout:
                if line.startswith("ATO_LEVEL:"):
                    try:
                        on_level(float(line.removeprefix("ATO_LEVEL:").strip()) / 100.0)
                    except Exception:
                        continue
            return_code = process.wait()
        except (BrokenPipeError, OSError) as exc:
            process.kill()
            raise ToolError("Windows speech playback failed.") from exc
        finally:
            timer.cancel()
            try:
                on_level(0.0)
            except Exception:
                pass
        if return_code != 0:
            raise ToolError("Windows speech playback failed.")
