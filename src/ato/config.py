"""Environment-based application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from ato.exceptions import ConfigurationError


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated settings required to run Ato."""

    deepseek_api_key: str
    model: str = "deepseek-v4-flash"
    memory_file: Path = Path("data/memory.json")
    memory_max_messages: int = 40
    workspace_root: Path = Path(".")
    audit_file: Path = Path("data/audit.jsonl")
    context_max_tokens: int = 12_000
    context_recent_messages: int = 12
    context_summary_max_chars: int = 6_000

    @classmethod
    def from_env(cls) -> Settings:
        """Load settings from a local .env file and the process environment."""
        load_dotenv()
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        model = os.getenv("ATO_MODEL", "deepseek-v4-flash").strip()
        memory_file = Path(os.getenv("ATO_MEMORY_FILE", "data/memory.json").strip())
        raw_max_messages = os.getenv("ATO_MEMORY_MAX_MESSAGES", "40").strip()
        workspace_root = Path(os.getenv("ATO_WORKSPACE_ROOT", ".").strip())
        audit_file = Path(os.getenv("ATO_AUDIT_FILE", "data/audit.jsonl").strip())
        raw_context_tokens = os.getenv("ATO_CONTEXT_MAX_TOKENS", "12000").strip()
        raw_recent_messages = os.getenv("ATO_CONTEXT_RECENT_MESSAGES", "12").strip()
        raw_summary_chars = os.getenv("ATO_CONTEXT_SUMMARY_MAX_CHARS", "6000").strip()

        if not api_key or api_key == "your_deepseek_api_key_here":
            raise ConfigurationError(
                "DEEPSEEK_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        if not model:
            raise ConfigurationError("ATO_MODEL cannot be empty.")

        try:
            max_messages = int(raw_max_messages)
        except ValueError as exc:
            raise ConfigurationError("ATO_MEMORY_MAX_MESSAGES must be an integer.") from exc
        if max_messages < 2:
            raise ConfigurationError("ATO_MEMORY_MAX_MESSAGES must be at least 2.")
        try:
            context_tokens = int(raw_context_tokens)
            recent_messages = int(raw_recent_messages)
            summary_chars = int(raw_summary_chars)
        except ValueError as exc:
            raise ConfigurationError("Context limits must be integers.") from exc
        if context_tokens < 256:
            raise ConfigurationError("ATO_CONTEXT_MAX_TOKENS must be at least 256.")
        if recent_messages < 2:
            raise ConfigurationError("ATO_CONTEXT_RECENT_MESSAGES must be at least 2.")
        if recent_messages > max_messages:
            raise ConfigurationError(
                "ATO_CONTEXT_RECENT_MESSAGES cannot exceed ATO_MEMORY_MAX_MESSAGES."
            )
        if summary_chars < 200:
            raise ConfigurationError("ATO_CONTEXT_SUMMARY_MAX_CHARS must be at least 200.")

        return cls(
            deepseek_api_key=api_key,
            model=model,
            memory_file=memory_file,
            memory_max_messages=max_messages,
            workspace_root=workspace_root,
            audit_file=audit_file,
            context_max_tokens=context_tokens,
            context_recent_messages=recent_messages,
            context_summary_max_chars=summary_chars,
        )
