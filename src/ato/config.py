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

        return cls(
            deepseek_api_key=api_key,
            model=model,
            memory_file=memory_file,
            memory_max_messages=max_messages,
            workspace_root=workspace_root,
            audit_file=audit_file,
        )
