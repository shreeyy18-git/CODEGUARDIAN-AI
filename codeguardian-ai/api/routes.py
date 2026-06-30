"""FastAPI routes for CodeGuardian AI.

Endpoints
---------
* ``POST /github/webhook`` — receives GitHub webhook deliveries, verifies
  the HMAC signature, parses the pull-request event, and kicks off the
  full review pipeline as a background task.
* ``GET  /reviews/{review_id}`` — retrieve a single review with its
  issues and evaluation.
* ``GET  /reviews`` — list recent reviews (paginated).

The full pipeline (``run_review_pipeline``) orchestrates:

1.  Start a GitHub Check Run (``in_progress``).
2.  Fetch the PR diff and changed-file list from the GitHub API.
3.  Build the initial LangGraph state and invoke the compiled graph.
4.  Persist the review, issues, and evaluation to the database.
5.  Post / update the PR review comment.
6.  Complete the Check Run with a pass/fail conclusion.

Every step is wrapped in error handling so that a failure at any point
surfaces as a failed Check Run on GitHub rather than a silent crash.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from config import settings
from database import crud
from database.database import get_db, get_session
from evaluation.evaluator import evaluate_and_store
from github_tools.checks import complete_check, fail_check, start_check
from github_tools.comments import format_review_comment, update_or_post_comment
from github_tools.github_api import fetch_changed_files, fetch_pr_diff
from github_tools.webhook import (
    PullRequestEvent,
    is_pull_request_action_relevant,
    parse_pull_request_event,
    safe_parse_json,
    verify_signature,
)
from graph.workflow import review_graph
from observability import ReviewTraceMeta, configure_tracing, record_review_metrics, trace_context

__all__ = ["router", "run_review_pipeline"]

_log = logging.getLogger("codeguardian.api.routes")

router = APIRouter()


# ════════════════════════════════════════════════════════════════════
#  Response schemas
# ════════════════════════════════════════════════════════════════════


class IssueResponse(BaseModel):
    """Serialised :class:`~database.models.Issue`."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    agent: str
    severity: str
    title: str
    file: str = ""
    line: int = 0
    description: str = ""
    suggestion: str = ""


class EvaluationResponse(BaseModel):
    """Serialised :class:`~database.models.Evaluation`."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    confidence: float
    hallucination: bool
    duplicate_rate: float
    quality_score: float


class ReviewResponse(BaseModel):
    """Serialised :class:`~database.models.Review` with nested issues + evaluation."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    pr_id: int
    overall_score: float
    risk_level: str
    summary: str
    review_time: float
    created_at: datetime
    issues: list[IssueResponse] = []
    evaluation: EvaluationResponse | None = None


class ReviewListResponse(BaseModel):
    """Paginated list of reviews."""

    reviews: list[ReviewResponse]
    total: int
    limit: int
    offset: int


class WebhookResponse(BaseModel):
    """Response returned by the webhook endpoint."""

    status: str
    pr_number: int | None = None
    commit_sha: str | None = None
    review_id: int | None = None
    message: str = ""


# ════════════════════════════════════════════════════════════════════
#  Full review pipeline
# ════════════════════════════════════════════════════════════════════


def run_review_pipeline(event: PullRequestEvent) -> dict[str, Any]:
    """Execute the full CodeGuardian AI review pipeline for a PR event.

    This function is designed to run as a FastAPI background task.  It:

    1.  Starts a GitHub Check Run (``in_progress``).
    2.  Fetches the PR diff and changed files.
    3.  Invokes the LangGraph workflow.
    4.  Persists the review + issues + evaluation to the database.
    5.  Posts / updates the PR comment.
    6.  Completes the Check Run.

    On any unrecoverable error, the Check Run is marked as ``failure``
    with the error message so the developer sees it in the GitHub UI.

    Parameters
    ----------
    event:
        The parsed :class:`PullRequestEvent` from the webhook payload.

    Returns
    -------
    dict
        A result dict with ``status``, ``review_id``, ``verdict``,
        ``score``, and ``elapsed_seconds``.
    """
    start_time = time.time()
    repo_full = event.repo_full_name
    commit_sha = event.commit_sha
    pr_number = event.pr_number

    _log.info(
        "Starting review pipeline for PR #%s in %s @ %s",
        pr_number,
        repo_full,
        commit_sha[:7],
    )

    # ── 1. Start check run ──────────────────────────────────────────
    _safe_start_check(repo_full, commit_sha)

    try:
        # ── 2. Fetch PR data ─────────────────────────────────────────
        code_diff = fetch_pr_diff(repo_full, pr_number)
        changed_files = fetch_changed_files(repo_full, pr_number)

        # Truncate oversized diffs to protect LLM context windows.
        if len(code_diff) > settings.max_diff_chars:
            _log.warning(
                "Diff for PR #%s is %d chars — truncating to %d",
                pr_number,
                len(code_diff),
                settings.max_diff_chars,
            )
            code_diff = code_diff[: settings.max_diff_chars]

        # ── 3. Build initial state & invoke graph ────────────────────
        configure_tracing()

        initial_state: dict[str, Any] = {
            "pr_number": pr_number,
            "commit_sha": commit_sha,
            "repository": repo_full,
            "branch": event.branch,
            "code_diff": code_diff,
            "changed_files": changed_files,
        }

        trace_meta = ReviewTraceMeta(
            pr_number=pr_number,
            commit_sha=commit_sha,
            repository=repo_full,
        )

        with trace_context(trace_meta):
            final_state: dict[str, Any] = review_graph.invoke(initial_state)

        # ── 4. Extract results ───────────────────────────────────────
        risk_scores: dict[str, Any] = final_state.get("risk_scores", {})
        overall_score: float = float(risk_scores.get("overall_score", 0.0))
        verdict: str = final_state.get(
            "merge_recommendation",
            "REQUEST_CHANGES",
        )
        final_report: str = final_state.get("final_report", "")
        consensus_findings: list[dict[str, Any]] = final_state.get(
            "consensus_findings", []
        )

        # Scanner findings count (for the comment).
        scanner_result = final_state.get("scanner_result")
        scanner_count = 0
        if scanner_result is not None and hasattr(scanner_result, "findings"):
            scanner_count = len(scanner_result.findings)

        elapsed = time.time() - start_time

        # ── 5. Persist to database ───────────────────────────────────
        review_id = _persist_review(
            event=event,
            verdict=verdict,
            overall_score=overall_score,
            summary=final_report,
            elapsed=elapsed,
            consensus_findings=consensus_findings,
            final_state=final_state,
        )

        # ── 6. Post / update PR comment ──────────────────────────────
        agent_findings = _compute_agent_finding_counts(final_state)
        _post_review_comment(
            repo_full=repo_full,
            pr_number=pr_number,
            verdict=verdict,
            score=overall_score,
            summary=final_report,
            review_id=review_id,
            scanner_count=scanner_count,
            issues=consensus_findings,
            agent_findings=agent_findings,
        )

        # ── 7. Complete check run ────────────────────────────────────
        _safe_complete_check(
            repo_full=repo_full,
            commit_sha=commit_sha,
            verdict=verdict,
            score=overall_score,
            summary=final_report,
        )

        # ── 8. Record metrics ────────────────────────────────────────
        record_review_metrics(
            review_id=review_id,
            confidence=0.0,  # updated by evaluate_and_store below
            hallucination=False,
            duplicate_rate=0.0,
            quality_score=overall_score,
            verdict=verdict,
            overall_score=overall_score,
            elapsed_seconds=elapsed,
        )

        _log.info(
            "Review pipeline complete for PR #%s — verdict=%s, score=%.2f, "
            "review_id=%d, elapsed=%.1fs",
            pr_number,
            verdict,
            overall_score,
            review_id,
            elapsed,
        )

        return {
            "status": "completed",
            "review_id": review_id,
            "verdict": verdict,
            "score": overall_score,
            "elapsed_seconds": round(elapsed, 2),
        }

    except Exception as exc:
        elapsed = time.time() - start_time
        _log.exception(
            "Review pipeline failed for PR #%s after %.1fs: %s",
            pr_number,
            elapsed,
            exc,
        )
        _safe_fail_check(repo_full, commit_sha, str(exc))
        return {
            "status": "error",
            "error": str(exc),
            "elapsed_seconds": round(elapsed, 2),
        }


# ── Pipeline helpers ─────────────────────────────────────────────────


def _persist_review(
    *,
    event: PullRequestEvent,
    verdict: str,
    overall_score: float,
    summary: str,
    elapsed: float,
    consensus_findings: list[dict[str, Any]],
    final_state: dict[str, Any],
) -> int:
    """Store the review, issues, and evaluation in the database.

    Returns the ``review_id``.
    """
    # Parse owner/repo from the full name.
    parts = event.repo_full_name.split("/", 1)
    owner = parts[0] if len(parts) == 2 else ""
    name = parts[1] if len(parts) == 2 else event.repo_full_name

    with get_session() as db:
        repo = crud.get_or_create_repository(db, name=name, owner=owner)

        pr = crud.get_pull_request_by_number(db, repo.id, event.pr_number)
        if pr is None:
            pr = crud.create_pull_request(
                db,
                repository_id=repo.id,
                pr_number=event.pr_number,
                commit_sha=event.commit_sha,
                branch=event.branch,
                status="completed",
            )
        else:
            pr.commit_sha = event.commit_sha
            pr.branch = event.branch
            pr.status = "completed"

        review = crud.create_review(
            db,
            pr_id=pr.id,
            overall_score=overall_score,
            risk_level=verdict,
            summary=summary,
            review_time=elapsed,
        )

        # Bulk-insert issues from consensus findings.
        if consensus_findings:
            issue_dicts = [
                {
                    "agent": f.get("agent", "unknown"),
                    "severity": f.get("severity", "MEDIUM"),
                    "title": f.get("title", "Untitled finding"),
                    "description": f.get("description", ""),
                    "file": f.get("file", ""),
                    "line": f.get("line", 0),
                    "suggestion": f.get("suggestion", ""),
                }
                for f in consensus_findings
            ]
            crud.bulk_create_issues(db, review_id=review.id, issues=issue_dicts)

        db.commit()

        review_id = review.id

    # Run evaluation and persist (separate session).
    try:
        with get_session() as db:
            evaluate_and_store(
                db,
                review_id=review_id,
                state=final_state,
            )
            db.commit()
    except Exception as exc:
        _log.warning("Evaluation failed for review %d: %s", review_id, exc)

    return review_id


def _compute_agent_finding_counts(state: dict[str, Any]) -> dict[str, int]:
    """Extract per-agent finding counts from the LangGraph final state."""
    agents = ["security", "bug", "performance", "quality", "architecture"]
    counts: dict[str, int] = {}
    for agent in agents:
        key = f"{agent}_findings"
        findings = state.get(key, [])
        if isinstance(findings, list):
            counts[agent] = len(findings)
        else:
            counts[agent] = 0
    return counts


def _post_review_comment(
    *,
    repo_full: str,
    pr_number: int,
    verdict: str,
    score: float,
    summary: str,
    review_id: int,
    scanner_count: int,
    issues: list[dict[str, Any]],
    agent_findings: dict[str, int] | None = None,
) -> None:
    """Format and post/update the PR review comment."""
    try:
        body = format_review_comment(
            verdict=verdict,
            score=score,
            summary=summary[:500] if summary else "",
            issues=issues,
            review_id=review_id,
            scanner_findings_count=scanner_count,
            agent_findings=agent_findings,
        )
        update_or_post_comment(repo_full, pr_number, body)
    except Exception as exc:
        _log.warning("Failed to post comment on PR #%s: %s", pr_number, exc)


def _safe_start_check(repo_full: str, commit_sha: str) -> None:
    """Start a check run, swallowing errors (best-effort)."""
    try:
        start_check(repo_full, commit_sha)
    except Exception as exc:
        _log.warning("Failed to start check run: %s", exc)


def _safe_complete_check(
    *,
    repo_full: str,
    commit_sha: str,
    verdict: str,
    score: float,
    summary: str,
) -> None:
    """Complete a check run, swallowing errors (best-effort)."""
    try:
        complete_check(
            repo_full,
            commit_sha,
            verdict=verdict,
            score=score,
            summary=summary[:500] if summary else "",
        )
    except Exception as exc:
        _log.warning("Failed to complete check run: %s", exc)


def _safe_fail_check(repo_full: str, commit_sha: str, error_message: str) -> None:
    """Fail a check run, swallowing errors (best-effort)."""
    try:
        fail_check(repo_full, commit_sha, error_message=error_message)
    except Exception as exc:
        _log.warning("Failed to fail check run: %s", exc)


# ════════════════════════════════════════════════════════════════════
#  Endpoints
# ════════════════════════════════════════════════════════════════════


@router.post(
    "/github/webhook",
    response_model=WebhookResponse,
    tags=["webhook"],
    summary="Receive a GitHub webhook delivery",
)
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> WebhookResponse:
    """Handle incoming GitHub webhook deliveries.

    Verifies the HMAC-SHA256 signature, parses the payload, and — if it
    is a relevant ``pull_request`` event — kicks off the full review
    pipeline as a background task.

    Returns ``200`` with ``status="accepted"`` for events that trigger a
    review, ``200`` with ``status="ignored"`` for non-relevant events,
    ``401`` for invalid signatures, and ``400`` for malformed payloads.
    """
    # ── 1. Get raw body ──────────────────────────────────────────────
    raw_body = await request.body()

    # ── 2. Verify signature ─────────────────────────────────────────
    signature_header = request.headers.get("X-Hub-Signature-256", "")
    if not verify_signature(raw_body, signature_header, settings.github_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # ── 3. Parse JSON ────────────────────────────────────────────────
    payload = safe_parse_json(raw_body)
    if payload is None:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # ── 4. Parse PR event ───────────────────────────────────────────
    event = parse_pull_request_event(payload)
    if event is None:
        return WebhookResponse(
            status="ignored",
            message="Not a pull_request event",
        )

    # ── 5. Check action relevance ───────────────────────────────────
    if not is_pull_request_action_relevant(event.action):
        return WebhookResponse(
            status="ignored",
            pr_number=event.pr_number,
            commit_sha=event.commit_sha,
            message=f"Action '{event.action}' does not trigger a review",
        )

    # ── 6. Kick off background pipeline ─────────────────────────────
    background_tasks.add_task(run_review_pipeline, event)

    _log.info(
        "Accepted webhook for PR #%s in %s @ %s — pipeline started",
        event.pr_number,
        event.repo_full_name,
        event.commit_sha[:7],
    )

    return WebhookResponse(
        status="accepted",
        pr_number=event.pr_number,
        commit_sha=event.commit_sha,
        message="Review pipeline started",
    )


@router.get(
    "/reviews/{review_id}",
    response_model=ReviewResponse,
    tags=["reviews"],
    summary="Retrieve a single review",
)
def get_review_endpoint(
    review_id: int,
    db: Session = Depends(get_db),
) -> ReviewResponse:
    """Fetch a review by its database ID, including issues and evaluation."""
    review = crud.get_review(db, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail=f"Review {review_id} not found")

    # Load the evaluation (most recent if multiple exist).
    evaluation = None
    if review.evaluations:
        evaluation = review.evaluations[-1]

    return ReviewResponse(
        id=review.id,
        pr_id=review.pr_id,
        overall_score=review.overall_score,
        risk_level=review.risk_level,
        summary=review.summary,
        review_time=review.review_time,
        created_at=review.created_at,
        issues=[IssueResponse.model_validate(i) for i in review.issues],
        evaluation=(
            EvaluationResponse.model_validate(evaluation) if evaluation else None
        ),
    )


@router.get(
    "/reviews",
    response_model=ReviewListResponse,
    tags=["reviews"],
    summary="List recent reviews",
)
def list_reviews_endpoint(
    limit: int = Query(default=20, ge=1, le=100, description="Max results"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    db: Session = Depends(get_db),
) -> ReviewListResponse:
    """List recent reviews, newest first, with pagination."""
    from sqlalchemy import func, select

    from database.models import Review

    total = db.execute(select(func.count(Review.id))).scalar() or 0

    stmt = (
        select(Review)
        .order_by(Review.created_at.desc(), Review.id.desc())
        .limit(limit)
        .offset(offset)
    )
    reviews = db.execute(stmt).scalars().all()

    return ReviewListResponse(
        reviews=[
            ReviewResponse(
                id=r.id,
                pr_id=r.pr_id,
                overall_score=r.overall_score,
                risk_level=r.risk_level,
                summary=r.summary,
                review_time=r.review_time,
                created_at=r.created_at,
                issues=[IssueResponse.model_validate(i) for i in r.issues],
                evaluation=(
                    EvaluationResponse.model_validate(r.evaluations[-1])
                    if r.evaluations
                    else None
                ),
            )
            for r in reviews
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
