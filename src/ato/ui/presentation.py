"""Provider-neutral bounded views shared by Ato user interfaces."""

from __future__ import annotations

from collections.abc import Sequence

from ato.brain.messages import Message, Role


def format_local_status(
    *, persistent_conversation: bool, long_term_memory: bool, knowledge: bool,
    tools: bool, voice_input: bool, voice_output: bool,
) -> str:
    """Render a secret-free local capability snapshot."""
    def label(available: bool) -> str:
        return "ready" if available else "not configured"
    return (
        "Ato local status:\n"
        "  conversation: ready\n"
        f"  persistent conversation: {label(persistent_conversation)}\n"
        f"  long-term memory: {label(long_term_memory)}\n"
        f"  knowledge: {label(knowledge)}\n"
        f"  tools: {label(tools)}\n"
        f"  voice input: {label(voice_input)}\n"
        f"  voice output: {label(voice_output)}"
    )


def recent_history_lines(messages: Sequence[Message]) -> tuple[str, ...]:
    """Render at most 20 recent messages as bounded one-line labels."""
    visible = messages[-20:]
    omitted = len(messages) - len(visible)
    lines = ["Ato recent conversation:"]
    if omitted:
        lines.append(f"  ... {omitted} older messages omitted")
    for message in visible:
        content = " ".join(message.content.split())
        if len(content) > 200:
            content = f"{content[:197]}..."
        lines.append(f"  {message.role.value}: {content}")
    return tuple(lines)


def latest_assistant_reply(messages: Sequence[Message]) -> str | None:
    """Select the newest assistant message from a conversation snapshot."""
    return next(
        (message.content for message in reversed(messages) if message.role is Role.ASSISTANT),
        None,
    )


def render_history_export(messages: Sequence[Message]) -> str:
    """Render a bounded inert plain-text conversation export."""
    visible = messages[-50:]
    omitted = len(messages) - len(visible)
    lines = ["Ato conversation export", ""]
    if omitted:
        lines.extend([f"[{omitted} older messages omitted]", ""])
    for message in visible:
        content = "".join(
            character
            if character in {"\n", "\t"} or ord(character) >= 32 and ord(character) != 127
            else "\ufffd"
            for character in message.content[:1_000]
        )
        lines.append(f"{message.role.value.upper()}:")
        lines.extend(f"  {line}" for line in content.splitlines() or [""])
        if len(message.content) > 1_000:
            lines.append("  [message truncated]")
        lines.append("")
    return "\n".join(lines)
