"""One place every example reads its settings from.

Values come from `strands-ai/.env`, overridable by real environment variables.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# _shared -> app -> strands-ai
ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["local", "dev"] = "local"
    log_level: str = "INFO"

settings = Settings()
