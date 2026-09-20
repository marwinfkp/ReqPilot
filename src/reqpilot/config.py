"""Typed application configuration (architecture ADR-011).

Three properties this module exists to guarantee:

1. **Typed.** Every setting is declared with a type and validated on load.
2. **Fail-fast.** A missing or malformed required value raises at startup, not
   three nodes into a graph run.
3. **No rules here.** Rule-like configuration - the risk matrix, MCDA weights,
   expected-control checklists - deliberately lives in versioned data files
   loaded by :mod:`reqpilot.rules`, not in the environment (Phase 0 DQ-03).

Nothing in P0 requires an LLM API key. Installing the project, running
migrations, starting the API, and running the default test suite all work with
``LLM_PROVIDER=stub`` and no credentials.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    """Deployment context. Drives which validations are strict."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LLMProvider(StrEnum):
    """Provider selector for the gateway abstraction (ADR-006).

    ``STUB`` is the P0 default: it makes no network call and needs no key, so
    the project is usable end to end with zero external API access.

    The concrete provider and model tier remain **TBD** pending the open Phase 0
    questions on budget and deployment; nothing here commits to either.
    """

    STUB = "stub"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"


class Settings(BaseSettings):
    """Application settings, loaded from the environment and an untracked ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ------------------------------------------------------
    app_env: AppEnv = Field(default=AppEnv.DEVELOPMENT, alias="REQPILOT_ENV")
    secret_key: str = Field(default="dev-only-not-a-real-secret", alias="REQPILOT_SECRET_KEY")
    log_level: str = Field(default="INFO", alias="REQPILOT_LOG_LEVEL")

    # --- Database (ADR-003) ----------------------------------------------
    database_url: str = Field(
        default="postgresql+psycopg://reqpilot:reqpilot@localhost:5432/reqpilot",
        alias="DATABASE_URL",
    )
    database_pool_size: int = Field(default=5, ge=1, le=50, alias="DATABASE_POOL_SIZE")

    # --- Orchestration (ADR-001, architecture C.7) ------------------------
    checkpoint_backend: str = Field(
        default="memory", alias="LANGGRAPH_CHECKPOINT_BACKEND"
    )  # memory | postgres
    checkpoint_retention_days: int = Field(default=30, ge=1, alias="CHECKPOINT_RETENTION_DAYS")
    node_timeout_seconds: int = Field(default=300, ge=1, alias="NODE_TIMEOUT_SECONDS")
    max_node_failures: int = Field(default=3, ge=1, alias="MAX_NODE_FAILURES")

    # --- LLM gateway (ADR-006) -------------------------------------------
    llm_provider: LLMProvider = Field(default=LLMProvider.STUB, alias="LLM_PROVIDER")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")
    # Model identifiers are configuration placeholders, not architectural
    # commitments: the tier decision is still open.
    llm_model_default: str | None = Field(default=None, alias="LLM_MODEL_DEFAULT")
    llm_model_reasoning: str | None = Field(default=None, alias="LLM_MODEL_REASONING")
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0, alias="LLM_TEMPERATURE")
    llm_max_retries: int = Field(default=3, ge=0, le=10, alias="LLM_MAX_RETRIES")
    llm_timeout_seconds: int = Field(default=60, ge=1, alias="LLM_TIMEOUT_SECONDS")
    llm_fixture_mode: str = Field(default="replay", alias="LLM_FIXTURE_MODE")
    llm_fixture_dir: Path = Field(default=Path("tests/fixtures/llm"), alias="LLM_FIXTURE_DIR")

    # --- Retrieval (placeholders; subsystem belongs to a later phase) -----
    retrieval_top_k: int = Field(default=8, ge=1, le=50, alias="RETRIEVAL_TOP_K")
    retrieval_min_score: float = Field(default=0.25, ge=0.0, le=1.0, alias="RETRIEVAL_MIN_SCORE")

    # --- Rules / deterministic configuration (DQ-03) ----------------------
    rules_dir: Path = Field(default=Path("src/reqpilot/rules/data"), alias="RULES_DIR")

    # --- Audit (architecture ADR-010) -------------------------------------
    audit_hash_chain_enabled: bool = Field(default=True, alias="AUDIT_HASH_CHAIN_ENABLED")

    # --- Data locations ---------------------------------------------------
    kb_seed_dir: Path = Field(default=Path("data/kb_seed"), alias="KB_SEED_DIR")
    gold_data_dir: Path = Field(default=Path("data/gold"), alias="GOLD_DATA_DIR")
    dev_data_dir: Path = Field(default=Path("data/dev"), alias="DEV_DATA_DIR")

    # --- Feature flags ----------------------------------------------------
    masking_enabled: bool = Field(default=True, alias="MASKING_ENABLED")
    injection_detection_enabled: bool = Field(default=True, alias="INJECTION_DETECTION_ENABLED")

    # --- Retention (FR-ADM-006) -------------------------------------------
    transcript_retention_days: int = Field(default=365, ge=1, alias="TRANSCRIPT_RETENTION_DAYS")

    # ---------------------------------------------------------------------
    # Validation
    # ---------------------------------------------------------------------
    @field_validator("llm_api_key")
    @classmethod
    def _key_required_only_for_real_providers(
        cls, value: str | None, info: ValidationInfo
    ) -> str | None:
        """A key is required only when a network provider is actually selected.

        This is what keeps P0 installable and testable with no credentials.
        """
        provider = info.data.get("llm_provider", LLMProvider.STUB)
        if provider in (LLMProvider.STUB, LLMProvider.OLLAMA):
            return value
        if not value:
            raise ValueError(
                f"LLM_API_KEY is required when LLM_PROVIDER={provider}. "
                "Use LLM_PROVIDER=stub for offline development and testing."
            )
        return value

    @field_validator("secret_key")
    @classmethod
    def _production_needs_a_real_secret(cls, value: str, info: ValidationInfo) -> str:
        if info.data.get("app_env") is AppEnv.PRODUCTION and value.startswith("dev-only"):
            raise ValueError("REQPILOT_SECRET_KEY must be set to a real value outside development")
        return value

    @field_validator("llm_fixture_mode")
    @classmethod
    def _known_fixture_mode(cls, value: str) -> str:
        allowed = {"replay", "record", "live"}
        if value not in allowed:
            raise ValueError(f"LLM_FIXTURE_MODE must be one of {sorted(allowed)}, got {value!r}")
        return value

    @field_validator("checkpoint_backend")
    @classmethod
    def _known_checkpoint_backend(cls, value: str) -> str:
        allowed = {"memory", "postgres"}
        if value not in allowed:
            raise ValueError(
                f"LANGGRAPH_CHECKPOINT_BACKEND must be one of {sorted(allowed)}, got {value!r}"
            )
        return value

    # ---------------------------------------------------------------------
    # Safety
    # ---------------------------------------------------------------------
    #: Fields that must never appear in logs, error messages, or audit payloads.
    #: A ClassVar, not a setting - it describes the settings rather than being one.
    SECRET_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"secret_key", "llm_api_key", "database_url"}
    )

    def safe_dump(self) -> dict[str, object]:
        """Return settings with every secret replaced by a marker.

        The only representation of settings that is safe to log. ``database_url``
        counts as a secret because it carries a password.
        """
        data: dict[str, object] = {}
        for name in type(self).model_fields:
            value = getattr(self, name)
            data[name] = "***redacted***" if name in self.SECRET_FIELDS else value
        return data

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"Settings({self.safe_dump()})"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, loaded once.

    Cached so that configuration is read exactly once per process. Tests that
    need different values call :meth:`cache_clear` or construct ``Settings``
    directly with explicit keyword arguments.
    """
    return Settings()
