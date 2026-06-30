"""Central configuration for CodeGuardian AI.

All settings are loaded from environment variables (or a local ``.env``
file) via :class:`pydantic_settings.BaseSettings`.  Importing this module
is side-effect free except for reading the environment.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ── Project paths ───────────────────────────────────────────────────────
BASE_DIR: Path = Path(__file__).resolve().parent
REPORTS_DIR: Path = BASE_DIR / "reports"
DATABASE_PATH: Path = BASE_DIR / "review.db"


class Settings(BaseSettings):
    """Application settings sourced from the environment.

    Every field maps 1-to-1 to an environment variable of the same name
    (case-insensitive).  Sensitive values should be provided via a
    ``.env`` file or real environment variables — never committed.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM providers ───────────────────────────────────────────────────
    groq_api_key: str = Field(default="", description="Groq API key (primary LLM)")
    gemini_api_key: str = Field(default="", description="Google Gemini API key (fallback LLM)")

    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Groq chat model name",
    )
    gemini_model: str = Field(
        default="gemini-2.0-flash",
        description="Google Gemini model name",
    )
    llm_temperature: float = Field(default=0.0, description="LLM sampling temperature")
    llm_timeout_seconds: int = Field(default=30, description="Per-request LLM timeout")

    # ── GitHub integration ────────────────────────────────────────────
    github_token: str = Field(default="", description="GitHub PAT for API calls")
    github_webhook_secret: str = Field(
        default="",
        description="Shared secret for HMAC-SHA256 webhook verification",
    )

    # ── LangSmith observability ────────────────────────────────────────
    langchain_tracing_v2: bool = Field(default=True, description="Enable LangSmith tracing")
    langchain_api_key: str = Field(default="", description="LangSmith API key")
    langchain_project: str = Field(
        default="codeguardian-ai",
        description="LangSmith project name",
    )
    langchain_endpoint: str = Field(
        default="https://api.smith.langchain.com",
        description="LangSmith API endpoint",
    )

    # ── Database ───────────────────────────────────────────────────────
    database_url: str = Field(
        default=f"sqlite:///{DATABASE_PATH}",
        description="SQLAlchemy database URL",
    )

    # ── Review pipeline ────────────────────────────────────────────────
    max_diff_chars: int = Field(
        default=40_000,
        description="Maximum characters of diff to send to an LLM in one chunk",
    )

    # ── Risk scoring thresholds ───────────────────────────────────────
    # overall_score = 0.5*security + 0.3*maintainability + 0.2*performance
    risk_pass_threshold: float = Field(
        default=0.8,
        description="Score >= this → APPROVE",
    )
    risk_warn_threshold: float = Field(
        default=0.4,
        description="Score >= this and < pass → REQUEST CHANGES",
    )
    # Score < warn_threshold → BLOCK MERGE

    # ── Server ─────────────────────────────────────────────────────────
    app_host: str = Field(default="0.0.0.0", description="FastAPI bind host")
    app_port: int = Field(default=8000, description="FastAPI bind port")
    app_log_level: str = Field(default="info", description="Uvicorn log level")

    # ── Static analysis ────────────────────────────────────────────────
    enable_semgrep: bool = Field(default=True, description="Run Semgrep scanner")
    enable_bandit: bool = Field(default=True, description="Run Bandit scanner")
    enable_ruff: bool = Field(default=True, description="Run Ruff scanner")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` singleton.

    Using ``lru_cache`` means the environment is read only once per
    process lifetime.  Call :func:`get_settings.cache_clear` to force a
    re-read (useful in tests).
    """
    return Settings()


# Module-level instance for convenient ``from config import settings``.
settings: Settings = get_settings()


def risk_verdict(score: float) -> str:
    """Map a numeric risk score to a human-readable verdict.

    Parameters
    ----------
    score:
        Overall risk score in ``[0.0, 1.0]`` where ``1.0`` is best.

    Returns
    -------
    str
        One of ``"APPROVE"``, ``"REQUEST_CHANGES"``, or ``"BLOCK_MERGE"``.
    """
    if score >= settings.risk_pass_threshold:
        return "APPROVE"
    if score >= settings.risk_warn_threshold:
        return "REQUEST_CHANGES"
    return "BLOCK_MERGE"
