"""LangSmith tracing configuration and helpers.

This module provides three capabilities (Phase 10):

1. **Trace configuration** — :func:`configure_tracing` ensures the
   ``LANGCHAIN_TRACING_V2``, ``LANGCHAIN_API_KEY``, ``LANGCHAIN_PROJECT``
   and ``LANGCHAIN_ENDPOINT`` environment variables are set from the
   application :class:`~config.Settings`.  LangGraph automatically picks
   these up and traces every node execution.

2. **Custom metadata** — :func:`trace_context` is a context manager that
   attaches review-specific metadata (``pr_number``, ``commit_sha``,
   ``repository``) to the current LangSmith trace via the
   ``@traceable``-compatible contextvars mechanism.

3. **Metric extraction** — :func:`extract_trace_metadata` queries the
   LangSmith API (via :class:`langsmith.Client`) for a given run and
   returns a dict of token usage, latency, and error information.

All functions degrade gracefully when LangSmith is not configured (no
API key) or the ``langsmith`` package is unavailable, so the application
continues to work in local/dev environments without tracing.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

__all__ = [
    "ReviewTraceMeta",
    "configure_tracing",
    "is_tracing_enabled",
    "trace_context",
    "get_current_trace_metadata",
    "extract_trace_metadata",
    "record_review_metrics",
]

_logger = logging.getLogger(__name__)

# ── ContextVar for current review metadata ────────────────────────────
# This allows nested code (agents, nodes) to access the current review's
# metadata for logging / tagging without passing it through every call.
_current_trace_meta: ContextVar[ReviewTraceMeta | None] = ContextVar(
    "codeguardian_trace_meta", default=None
)


@dataclass
class ReviewTraceMeta:
    """Metadata attached to a LangSmith trace for a single PR review.

    Attributes
    ----------
    pr_number:
        The GitHub pull-request number being reviewed.
    commit_sha:
        The head commit SHA of the PR.
    repository:
        The ``owner/repo`` full name of the GitHub repository.
    review_id:
        Optional database review ID once the review row is created.
    extra:
        Arbitrary additional key-value pairs to include as trace metadata.
    """

    pr_number: int | None = None
    commit_sha: str = ""
    repository: str = ""
    review_id: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        """Return a flat dict suitable for LangSmith ``metadata``."""
        meta: dict[str, Any] = {
            "pr_number": self.pr_number,
            "commit_sha": self.commit_sha,
            "repository": self.repository,
        }
        if self.review_id is not None:
            meta["review_id"] = self.review_id
        meta.update(self.extra)
        return meta


# ════════════════════════════════════════════════════════════════════
#  1. Trace configuration
# ════════════════════════════════════════════════════════════════════

_CONFIGURED = False


def configure_tracing() -> bool:
    """Set LangSmith environment variables from application settings.

    Reads :class:`~config.Settings` and exports the tracing configuration
    to ``os.environ`` so that LangGraph / LangChain callbacks pick it up.

    Returns ``True`` if tracing is enabled (API key present), ``False``
    otherwise.  Safe to call multiple times — only configures once unless
    :func:`_reset_config` is called (used in tests).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return is_tracing_enabled()

    try:
        from config import get_settings
    except Exception:  # pragma: no cover — config not available in some envs
        return False

    settings = get_settings()

    # Only enable tracing if an API key is configured
    if settings.langchain_api_key:
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key
        os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project
        if settings.langchain_endpoint:
            os.environ["LANGCHAIN_ENDPOINT"] = settings.langchain_endpoint
        _logger.info(
            "LangSmith tracing enabled (project=%s, endpoint=%s)",
            settings.langchain_project,
            settings.langchain_endpoint or "default",
        )
        _CONFIGURED = True
        return True

    # No API key — explicitly disable to avoid noisy warnings
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
    _logger.debug("LangSmith tracing disabled (no LANGCHAIN_API_KEY)")
    _CONFIGURED = True
    return False


def is_tracing_enabled() -> bool:
    """Return ``True`` if LangSmith tracing is currently enabled."""
    return os.environ.get("LANGCHAIN_TRACING_V2", "false").lower() == "true"


# ════════════════════════════════════════════════════════════════════
#  2. Custom trace metadata
# ════════════════════════════════════════════════════════════════════


@contextmanager
def trace_context(meta: ReviewTraceMeta) -> Iterator[ReviewTraceMeta]:
    """Attach review-specific metadata to the current LangSmith trace.

    Usage::

        with trace_context(ReviewTraceMeta(pr_number=42, commit_sha="abc")):
            review_graph.invoke(initial_state)

    The metadata is stored in a :class:`~contextvars.ContextVar` so that
    nested functions (agents, nodes) can access it via
    :func:`get_current_trace_metadata`.  When the ``langsmith`` package
    is available, the metadata is also attached to the active trace run
    via ``langsmith.context``.

    Parameters
    ----------
    meta:
        The review metadata to attach.

    Yields
    ------
    The same :class:`ReviewTraceMeta` for convenience.
    """
    token = _current_trace_meta.set(meta)

    # If langsmith is installed, try to attach metadata to the current run
    _attach_to_langsmith_run(meta)

    try:
        yield meta
    finally:
        _current_trace_meta.reset(token)


def get_current_trace_metadata() -> ReviewTraceMeta | None:
    """Return the metadata for the currently active trace, or ``None``."""
    return _current_trace_meta.get()


def _attach_to_langsmith_run(meta: ReviewTraceMeta) -> None:
    """Best-effort attachment of metadata to the active LangSmith run.

    Uses the ``langsmith.context`` module if available.  Silently does
    nothing if langsmith is not installed or no run is active.
    """
    try:
        import langsmith  # type: ignore[import-untyped]

        client = langsmith.Client()  # type: ignore[attr-defined]
        # Update the current run's metadata if a run is in progress
        # The langsmith SDK exposes a context-aware helper for this
        if hasattr(langsmith, "context"):
            ctx = langsmith.context  # type: ignore[attr-defined]
            if hasattr(ctx, "update_current_run"):
                ctx.update_current_run(metadata=meta.to_metadata())  # type: ignore[attr-defined]
    except Exception:
        # langsmith not installed, not configured, or no active run —
        # metadata is still available via the ContextVar fallback.
        pass


# ════════════════════════════════════════════════════════════════════
#  3. Metric extraction
# ════════════════════════════════════════════════════════════════════


def extract_trace_metadata(run_id: str | None = None) -> dict[str, Any]:
    """Extract metrics from a completed LangSmith run.

    Queries the LangSmith API for the given ``run_id`` (or the most
    recent root run in the current project if ``run_id`` is ``None``)
    and returns a dict with:

    - ``total_tokens`` — sum of prompt + completion tokens
    - ``prompt_tokens`` — input tokens
    - ``completion_tokens`` — output tokens
    - ``latency_ms`` — total run duration in milliseconds
    - ``error`` — error message if the run failed, ``None`` otherwise
    - ``status`` — run status (``"success"``, ``"error"``, ``"running"``)
    - ``run_count`` — number of child runs (agent/node executions)
    - ``run_id`` — the LangSmith run ID

    Returns an empty dict if LangSmith is not configured or the run
    cannot be found.
    """
    if not is_tracing_enabled():
        return {}

    try:
        import langsmith  # type: ignore[import-untyped]

        client = langsmith.Client()  # type: ignore[attr-defined]
    except Exception:
        _logger.debug("langsmith package not available for metric extraction")
        return {}

    try:
        if run_id is None:
            # Fetch the most recent root run in the project
            runs = list(
                client.list_runs(
                    project_name=os.environ.get("LANGCHAIN_PROJECT", "default"),
                    is_root=True,
                    limit=1,
                )
            )
            if not runs:
                return {}
            run = runs[0]
            run_id = str(run.id) if hasattr(run, "id") else str(run)
        else:
            run = client.read_run(run_id)

        # Extract token usage from run outputs
        usage = _extract_usage(run)
        latency_ms = _extract_latency(run)
        error = _extract_error(run)
        status = getattr(run, "status", "unknown")

        # Count child runs
        child_runs = list(
            client.list_runs(project_name=os.environ.get("LANGCHAIN_PROJECT", "default"), parent_run_id=run_id)
        )
        run_count = len(child_runs) + 1  # include the root run

        return {
            "run_id": run_id,
            "status": status,
            "total_tokens": usage.get("total_tokens", 0),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "latency_ms": latency_ms,
            "error": error,
            "run_count": run_count,
        }
    except Exception as exc:
        _logger.warning("Failed to extract LangSmith trace metadata: %s", exc)
        return {}


def _extract_usage(run: Any) -> dict[str, int]:
    """Extract token usage from a LangSmith run object."""
    usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    # The usage info may be in run.outputs or run.extra
    outputs = getattr(run, "outputs", None) or {}
    if isinstance(outputs, dict):
        # LangChain LLM runs store usage under "usage" or "token_usage"
        raw_usage = outputs.get("usage") or outputs.get("token_usage") or {}
        if isinstance(raw_usage, dict):
            usage["prompt_tokens"] = int(raw_usage.get("prompt_tokens", raw_usage.get("input_tokens", 0)))
            usage["completion_tokens"] = int(raw_usage.get("completion_tokens", raw_usage.get("output_tokens", 0)))
            usage["total_tokens"] = int(raw_usage.get("total_tokens", usage["prompt_tokens"] + usage["completion_tokens"]))
    return usage


def _extract_latency(run: Any) -> float:
    """Extract latency in milliseconds from a LangSmith run object."""
    start = getattr(run, "start_time", None)
    end = getattr(run, "end_time", None)
    if start and end:
        delta = (end - start).total_seconds() * 1000
        return round(delta, 2)
    return 0.0


def _extract_error(run: Any) -> str | None:
    """Extract error message from a failed LangSmith run."""
    error_val = getattr(run, "error", None)
    if error_val:
        if isinstance(error_val, str):
            return error_val
        if isinstance(error_val, dict):
            return error_val.get("message", str(error_val))
        return str(error_val)
    return None


# ════════════════════════════════════════════════════════════════════
#  4. Review metric recording
# ════════════════════════════════════════════════════════════════════


def record_review_metrics(
    *,
    review_id: int,
    confidence: float,
    hallucination: bool,
    duplicate_rate: float,
    quality_score: float,
    verdict: str,
    overall_score: float,
    elapsed_seconds: float | None = None,
) -> dict[str, Any]:
    """Build a metrics dict for a completed review and attach to trace.

    This is called after :func:`~evaluation.evaluator.evaluate_and_store`
    to record the evaluation results as trace metadata.  The returned
    dict can be logged, stored in ``agent_logs``, or attached to the
    LangSmith run.

    Parameters
    ----------
    review_id:
        Database ID of the review.
    confidence:
        Overall evaluation confidence score [0, 1].
    hallucination:
        Whether any hallucinated findings were detected.
    duplicate_rate:
        Fraction of duplicate findings.
    quality_score:
        Composite quality score.
    verdict:
        Risk verdict (``APPROVE``, ``REQUEST_CHANGES``, ``BLOCK_MERGE``).
    overall_score:
        Risk score [0, 1].
    elapsed_seconds:
        Optional total review wall-clock time.

    Returns
    -------
    dict
        A flat metrics dictionary.
    """
    metrics: dict[str, Any] = {
        "review_id": review_id,
        "confidence": round(confidence, 4),
        "hallucination": hallucination,
        "duplicate_rate": round(duplicate_rate, 4),
        "quality_score": round(quality_score, 4),
        "verdict": verdict,
        "overall_risk_score": round(overall_score, 4),
        "timestamp": time.time(),
    }
    if elapsed_seconds is not None:
        metrics["elapsed_seconds"] = round(elapsed_seconds, 3)

    # Attach to the current trace if one is active
    meta = get_current_trace_metadata()
    if meta is not None:
        meta.extra.update({"evaluation": metrics})
        _attach_to_langsmith_run(meta)

    _logger.info("Review metrics recorded: %s", metrics)
    return metrics


# ════════════════════════════════════════════════════════════════════
#  Internal: reset for tests
# ════════════════════════════════════════════════════════════════════


def _reset_config() -> None:
    """Reset the configuration cache (for testing only)."""
    global _CONFIGURED
    _CONFIGURED = False
