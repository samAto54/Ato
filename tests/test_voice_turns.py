from collections.abc import Sequence
from pathlib import Path

from ato.brain.agent import Agent
from ato.brain.messages import Message
from ato.security import PermissionManager
from ato.tools import build_phase3_registry
from ato.ui.terminal import run_terminal


class RecordingLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, messages: Sequence[Message], tools=None) -> str:
        del tools
        prompt = messages[-1].content
        self.prompts.append(prompt)
        return f"heard: {prompt}"


class Recorder:
    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, duration_seconds: int) -> Path:
        assert duration_seconds == 2
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(b"RIFF")
        return self.path


class Transcriber:
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript

    def transcribe_file(self, path: Path) -> str:
        assert path.name == "clip.wav"
        return self.transcript


def _registry(tmp_path: Path, transcript: str):
    return build_phase3_registry(
        tmp_path,
        PermissionManager(lambda request: True),
        microphone_recorder=Recorder(tmp_path / "data" / "audio" / "clip.wav"),
        transcriber=Transcriber(transcript),
    )


def test_reviewed_voice_turn_is_submitted_to_agent(tmp_path) -> None:
    llm = RecordingLLM()
    output: list[str] = []
    inputs = iter(["/voice 2", "yes", "quit"])

    run_terminal(
        Agent(llm),
        tool_registry=_registry(tmp_path, "hello from voice"),
        read=lambda prompt: next(inputs),
        write=output.append,
    )

    assert llm.prompts == ["hello from voice"]
    assert "Ato transcript (review before submitting): hello from voice" in output
    assert "Ato: heard: hello from voice" in output


def test_cancelled_voice_turn_is_not_submitted(tmp_path) -> None:
    llm = RecordingLLM()
    output: list[str] = []
    inputs = iter(["/voice 2", "n", "quit"])

    run_terminal(
        Agent(llm),
        tool_registry=_registry(tmp_path, "do not send"),
        read=lambda prompt: next(inputs),
        write=output.append,
    )

    assert llm.prompts == []
    assert "Ato: Voice turn cancelled; transcript was not submitted." in output


def test_approved_voice_transcript_bypasses_terminal_commands(tmp_path) -> None:
    llm = RecordingLLM()
    inputs = iter(["/voice 2", "y", "quit"])

    run_terminal(
        Agent(llm),
        tool_registry=_registry(tmp_path, "/clear-memory"),
        read=lambda prompt: next(inputs),
        write=lambda text: None,
    )

    assert llm.prompts == ["/clear-memory"]


def test_voice_command_reports_unavailable_tools() -> None:
    output: list[str] = []
    inputs = iter(["/voice 2", "quit"])

    run_terminal(
        Agent(RecordingLLM()),
        read=lambda prompt: next(inputs),
        write=output.append,
    )

    assert "Ato error: Voice tools are unavailable." in output
