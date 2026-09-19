"""Configuration: environment settings and the scoring policy."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from jobmatch.errors import ConfigurationError

MAX_FILE_BYTES = 2_000_000


class Settings(BaseSettings):
    """Runtime settings from ``JOBMATCH_*`` environment variables and ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="JOBMATCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    taxonomy_file: Path | None = None
    policy_file: Path | None = None
    max_document_bytes: int = Field(default=1_000_000, ge=1000)
    max_documents: int = Field(default=2000, ge=1)

    llm_provider: Literal["none", "openai", "anthropic"] = "none"
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("JOBMATCH_OPENAI_API_KEY", "OPENAI_API_KEY")
    )
    openai_base_url: str = "https://api.openai.com/v1"
    openai_chat_model: str = "gpt-4o-mini"
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("JOBMATCH_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_model: str = "claude-sonnet-5"
    anthropic_max_tokens: int = Field(default=400, gt=0)

    http_timeout_seconds: float = Field(default=30.0, gt=0)
    retry_attempts: int = Field(default=3, ge=1)
    retry_min_wait: float = Field(default=0.5, ge=0)
    retry_max_wait: float = Field(default=8.0, ge=0)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "WARNING"
    log_json: bool = True

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        if self.retry_max_wait < self.retry_min_wait:
            raise ValueError("retry_max_wait must be >= retry_min_wait")
        return self


class Policy(BaseModel):
    """How a match is scored. Every value here changes the outcome and is covered by a test."""

    model_config = ConfigDict(extra="forbid")

    weight_must: float = Field(default=0.55, ge=0)
    weight_nice: float = Field(default=0.15, ge=0)
    weight_experience: float = Field(default=0.15, ge=0)
    weight_seniority: float = Field(default=0.10, ge=0)
    weight_education: float = Field(default=0.05, ge=0)

    listed_strength: float = Field(default=0.4, ge=0, le=1)
    implied_factor: float = Field(default=0.8, ge=0, le=1)
    related_factor: float = Field(default=0.35, ge=0, le=1)
    recent_months: int = Field(default=24, ge=1)
    dated_months: int = Field(default=60, ge=1)
    recent_factor: float = Field(default=1.0, ge=0, le=1)
    dated_factor: float = Field(default=0.85, ge=0, le=1)
    stale_factor: float = Field(default=0.7, ge=0, le=1)

    strong_min: int = Field(default=75, ge=0, le=100)
    possible_min: int = Field(default=50, ge=0, le=100)
    quantified_ratio_min: float = Field(default=0.3, ge=0, le=1)
    max_gap_months: int = Field(default=6, ge=1)

    @model_validator(mode="after")
    def _check(self) -> Policy:
        if self.possible_min > self.strong_min:
            raise ValueError("possible_min must be <= strong_min")
        if not (
            self.weight_must
            + self.weight_nice
            + self.weight_experience
            + self.weight_seniority
            + self.weight_education
        ):
            raise ValueError("at least one weight must be positive")
        return self


def load_policy(path: Path | None) -> Policy:
    """Read a policy YAML file, or return defaults when ``path`` is ``None``.

    Raises:
        ConfigurationError: If the file cannot be read or is invalid.
    """
    if path is None:
        return Policy()
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ConfigurationError(f"{path.name} is larger than {MAX_FILE_BYTES} bytes")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return Policy.model_validate(data)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"cannot read {path.name}: {exc}") from exc
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise ConfigurationError(f"invalid policy {path.name} ({where}: {first['msg']})") from exc
