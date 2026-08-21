"""Environment-based application configuration."""

from __future__ import annotations

import os
import re
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
    long_term_memory_file: Path = Path("data/long_term_memory.db")
    knowledge_file: Path = Path("data/knowledge.db")
    research_file: Path = Path("data/research.db")
    edit_checkpoint_file: Path = Path("data/edit_checkpoints.db")
    brave_search_api_key: str | None = None
    tavily_api_key: str | None = None
    github_repository: str | None = None
    github_token: str | None = None

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
        long_term_memory_file = Path(
            os.getenv("ATO_LONG_TERM_MEMORY_FILE", "data/long_term_memory.db").strip()
        )
        knowledge_file = Path(os.getenv("ATO_KNOWLEDGE_FILE", "data/knowledge.db").strip())
        research_file = Path(os.getenv("ATO_RESEARCH_FILE", "data/research.db").strip())
        edit_checkpoint_file = Path(
            os.getenv("ATO_EDIT_CHECKPOINT_FILE", "data/edit_checkpoints.db").strip()
        )
        raw_brave_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
        brave_search_api_key = (
            None if raw_brave_key in {"", "your_brave_search_api_key_here"} else raw_brave_key
        )
        raw_tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
        tavily_api_key = (
            None if raw_tavily_key in {"", "your_tavily_api_key_here"} else raw_tavily_key
        )
        raw_github_repository = os.getenv("ATO_GITHUB_REPOSITORY", "").strip()
        github_repository = raw_github_repository or None
        raw_github_token = os.getenv("GITHUB_TOKEN", "").strip()
        github_token = (
            None if raw_github_token in {"", "your_github_token_here"} else raw_github_token
        )

        if not api_key or api_key == "your_deepseek_api_key_here":
            raise ConfigurationError(
                "DEEPSEEK_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        if not model:
            raise ConfigurationError("ATO_MODEL cannot be empty.")
        if github_repository and not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", github_repository
        ):
            raise ConfigurationError("ATO_GITHUB_REPOSITORY must use the owner/name format.")

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
            long_term_memory_file=long_term_memory_file,
            knowledge_file=knowledge_file,
            research_file=research_file,
            edit_checkpoint_file=edit_checkpoint_file,
            brave_search_api_key=brave_search_api_key,
            tavily_api_key=tavily_api_key,
            github_repository=github_repository,
            github_token=github_token,
        )
