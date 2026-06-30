"""FastAPI application entry point for CodeGuardian AI.

Run locally::

    uvicorn main:app --reload

Or via Docker::

    docker compose up --build

This module wires together the full webhook pipeline (GitHub signature
verification → static analysis → LangGraph agent workflow → risk
scoring → GitHub comment) by mounting the API router from
:mod:`api.routes` and initialising the database + LangSmith tracing on
startup.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from api.routes import router as api_router
from config import settings
from database.database import init_db
from observability import configure_tracing, is_tracing_enabled

_log = logging.getLogger("codeguardian.main")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application startup / shutdown lifecycle.

    On startup:
    * Initialise the SQLite database (create tables if missing).
    * Configure LangSmith tracing (no-op if no API key).
    * Log readiness.

    On shutdown:
    * Log shutdown.
    """
    # ── Startup ────────────────────────────────────────────────────────
    logging.basicConfig(
        level=getattr(logging, settings.app_log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    _log.info(
        "Starting CodeGuardian AI on %s:%s (log_level=%s)",
        settings.app_host,
        settings.app_port,
        settings.app_log_level,
    )

    # Create database tables (idempotent).
    try:
        init_db()
        _log.info("Database initialised — %s", settings.database_url)
    except Exception as exc:
        _log.error("Database initialisation failed: %s", exc)

    # Configure LangSmith tracing (best-effort).
    try:
        if configure_tracing():
            _log.info("LangSmith tracing enabled — project=%s", settings.langchain_project)
        else:
            _log.info("LangSmith tracing disabled (no API key or disabled)")
    except Exception as exc:
        _log.warning("LangSmith tracing setup failed: %s", exc)

    yield

    # ── Shutdown ───────────────────────────────────────────────────────
    _log.info("Shutting down CodeGuardian AI")


app = FastAPI(
    title="CodeGuardian AI",
    description=(
        "Production-ready Multi-Agent AI Code Review Platform with "
        "Automated GitHub PR Gatekeeping."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Mount the API router (webhook + review endpoints).
app.include_router(api_router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """Liveness probe — returns ``200`` when the process is up."""
    return {"status": "ok", "service": "codeguardian-ai"}


@app.get("/ready", tags=["meta"])
async def ready() -> dict[str, str]:
    """Readiness probe — checks that configuration is loaded."""
    checks: dict[str, str] = {"status": "ok"}
    if not settings.groq_api_key and not settings.gemini_api_key:
        checks["status"] = "degraded"
        checks["warning"] = "No LLM API keys configured"
    if not settings.github_token:
        checks["github"] = "warning"
        checks["warning"] = checks.get("warning", "") + "; GitHub token not set"
    if is_tracing_enabled():
        checks["tracing"] = "enabled"
    return checks


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.app_host,
        port=settings.app_port,
        log_level=settings.app_log_level,
        reload=False,
    )
