"""Environment-based application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from ato.exceptions import ConfigurationError


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated settings required to run Ato."""

    deepseek_api_key: str
    model: str = "deepseek-v4-flash"

    @classmethod
    def from_env(cls) -> Settings:
        """Load settings from a local .env file and the process environment."""
        load_dotenv()
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        model = os.getenv("ATO_MODEL", "deepseek-v4-flash").strip()

        if not api_key or api_key == "your_deepseek_api_key_here":
            raise ConfigurationError(
                "DEEPSEEK_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        if not model:
            raise ConfigurationError("ATO_MODEL cannot be empty.")

        return cls(deepseek_api_key=api_key, model=model)
