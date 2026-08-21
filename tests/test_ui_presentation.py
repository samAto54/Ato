from ato.brain.messages import Message, Role
from ato.ui.presentation import (
    format_local_status,
    latest_assistant_reply,
    recent_history_lines,
    render_history_export,
)


def test_provider_neutral_status_contains_only_supplied_availability() -> None:
    status = format_local_status(
        persistent_conversation=True,
        long_term_memory=False,
        knowledge=True,
        tools=True,
        voice_input=False,
        voice_output=True,
    )
    assert "persistent conversation: ready" in status
    assert "long-term memory: not configured" in status
    assert "voice output: ready" in status


def test_history_views_are_bounded_and_select_latest_assistant() -> None:
    messages = [Message(Role.USER, f"message {index}") for index in range(21)]
    messages.extend(
        [Message(Role.ASSISTANT, "older reply"), Message(Role.ASSISTANT, "latest reply")]
    )
    lines = recent_history_lines(messages)
    assert lines[1] == "  ... 3 older messages omitted"
    assert latest_assistant_reply(messages) == "latest reply"


def test_plain_text_export_sanitizes_controls_and_caps_messages() -> None:
    messages = [Message(Role.USER, f"Ghana\x00{'x' * 1_100}")]
    exported = render_history_export(messages)
    assert "Ghana\ufffd" in exported
    assert "[message truncated]" in exported
    assert len(exported) < 1_100
