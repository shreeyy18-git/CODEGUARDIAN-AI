"""CRUD operations for CodeGuardian AI.

Every function takes an explicit :class:`sqlalchemy.orm.Session` as its
first argument so callers control transaction boundaries (commit /
rollback).  Functions **do not commit** — the caller is responsible for
calling ``db.commit()`` after one or more operations.

Functions
---------
Repositories:
    :func:`create_repository`, :func:`get_or_create_repository`

Pull requests:
    :func:`create_pull_request`, :func:`get_pull_request`

Reviews:
    :func:`create_review`, :func:`get_review`, :func:`get_reviews_by_pr`

Issues:
    :func:`create_issue`, :func:`bulk_create_issues`

Evaluations:
    :func:`create_evaluation`

Agent logs:
    :func:`create_agent_log`, :func:`bulk_create_agent_logs`
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import (
    AgentLog,
    Evaluation,
    Issue,
    PullRequest,
    Repository,
    Review,
)


# ── Repositories ────────────────────────────────────────────────────────


def create_repository(db: Session, *, name: str, owner: str) -> Repository:
    """Insert a new :class:`Repository` row and return it."""
    repo = Repository(name=name, owner=owner)
    db.add(repo)
    db.flush()  # populate repo.id without committing
    return repo


def get_or_create_repository(db: Session, *, name: str, owner: str) -> Repository:
    """Return an existing repository or create one if it doesn't exist.

    Looks up by ``(owner, name)`` — the natural key for a GitHub repo.
    """
    stmt = select(Repository).where(
        Repository.owner == owner,
        Repository.name == name,
    )
    repo = db.execute(stmt).scalar_one_or_none()
    if repo is None:
        repo = create_repository(db, name=name, owner=owner)
    return repo


# ── Pull Requests ──────────────────────────────────────────────────────


def create_pull_request(
    db: Session,
    *,
    repository_id: int,
    pr_number: int,
    commit_sha: str,
    branch: str,
    status: str = "pending",
) -> PullRequest:
    """Insert a new :class:`PullRequest` row and return it."""
    pr = PullRequest(
        repository_id=repository_id,
        pr_number=pr_number,
        commit_sha=commit_sha,
        branch=branch,
        status=status,
    )
    db.add(pr)
    db.flush()
    return pr


def get_pull_request(db: Session, pr_id: int) -> Optional[PullRequest]:
    """Fetch a pull request by its database primary key."""
    return db.get(PullRequest, pr_id)


def get_pull_request_by_number(
    db: Session, repository_id: int, pr_number: int
) -> Optional[PullRequest]:
    """Fetch a pull request by ``(repository_id, pr_number)``."""
    stmt = select(PullRequest).where(
        PullRequest.repository_id == repository_id,
        PullRequest.pr_number == pr_number,
    )
    return db.execute(stmt).scalar_one_or_none()


# ── Reviews ────────────────────────────────────────────────────────────


def create_review(
    db: Session,
    *,
    pr_id: int,
    overall_score: float = 0.0,
    risk_level: str = "PENDING",
    summary: str = "",
    review_time: float = 0.0,
) -> Review:
    """Insert a new :class:`Review` row and return it."""
    review = Review(
        pr_id=pr_id,
        overall_score=overall_score,
        risk_level=risk_level,
        summary=summary,
        review_time=review_time,
    )
    db.add(review)
    db.flush()
    return review


def get_review(db: Session, review_id: int) -> Optional[Review]:
    """Fetch a review by its database primary key."""
    return db.get(Review, review_id)


def get_reviews_by_pr(db: Session, pr_id: int) -> Sequence[Review]:
    """Return all reviews for a given pull request, newest first.

    A secondary sort on ``id`` guarantees deterministic ordering even when
    multiple reviews share the same ``created_at`` timestamp (common in tests
    and fast bulk inserts where SQLite's second-level ``datetime('now')``
    precision ties records together).
    """
    stmt = (
        select(Review)
        .where(Review.pr_id == pr_id)
        .order_by(Review.created_at.desc(), Review.id.desc())
    )
    return db.execute(stmt).scalars().all()


# ── Issues ──────────────────────────────────────────────────────────────


def create_issue(
    db: Session,
    *,
    review_id: int,
    agent: str,
    severity: str,
    title: str,
    description: str = "",
    file: str = "",
    line: int = 0,
    suggestion: str = "",
) -> Issue:
    """Insert a single :class:`Issue` row and return it."""
    issue = Issue(
        review_id=review_id,
        agent=agent,
        severity=severity,
        title=title,
        description=description,
        file=file,
        line=line,
        suggestion=suggestion,
    )
    db.add(issue)
    db.flush()
    return issue


def bulk_create_issues(db: Session, *, review_id: int, issues: list[dict]) -> list[Issue]:
    """Insert multiple :class:`Issue` rows in one flush.

    Parameters
    ----------
    review_id:
        FK to the parent review.
    issues:
        List of dicts with keys ``agent``, ``severity``, ``title``,
        ``description``, ``file``, ``line``, ``suggestion``.

    Returns
    -------
    list[Issue]
        The created Issue objects (with ids populated after flush).
    """
    created: list[Issue] = []
    for data in issues:
        issue = Issue(
            review_id=review_id,
            agent=data.get("agent", ""),
            severity=data.get("severity", "LOW"),
            title=data.get("title", ""),
            description=data.get("description", ""),
            file=data.get("file", ""),
            line=data.get("line", 0),
            suggestion=data.get("suggestion", ""),
        )
        db.add(issue)
        created.append(issue)
    db.flush()
    return created


# ── Evaluations ────────────────────────────────────────────────────────


def create_evaluation(
    db: Session,
    *,
    review_id: int,
    confidence: float = 0.0,
    hallucination: bool = False,
    duplicate_rate: float = 0.0,
    quality_score: float = 0.0,
) -> Evaluation:
    """Insert a new :class:`Evaluation` row and return it."""
    evaluation = Evaluation(
        review_id=review_id,
        confidence=confidence,
        hallucination=hallucination,
        duplicate_rate=duplicate_rate,
        quality_score=quality_score,
    )
    db.add(evaluation)
    db.flush()
    return evaluation


# ── Agent Logs ──────────────────────────────────────────────────────────


def create_agent_log(
    db: Session,
    *,
    review_id: int,
    agent_name: str,
    model_used: str,
    latency: float = 0.0,
    tokens: int = 0,
    status: str = "success",
) -> AgentLog:
    """Insert a single :class:`AgentLog` row and return it."""
    log = AgentLog(
        review_id=review_id,
        agent_name=agent_name,
        model_used=model_used,
        latency=latency,
        tokens=tokens,
        status=status,
    )
    db.add(log)
    db.flush()
    return log


def bulk_create_agent_logs(db: Session, *, review_id: int, logs: list[dict]) -> list[AgentLog]:
    """Insert multiple :class:`AgentLog` rows in one flush.

    Parameters
    ----------
    review_id:
        FK to the parent review.
    logs:
        List of dicts with keys ``agent_name``, ``model_used``,
        ``latency``, ``tokens``, ``status``.

    Returns
    -------
    list[AgentLog]
        The created AgentLog objects (with ids populated after flush).
    """
    created: list[AgentLog] = []
    for data in logs:
        log = AgentLog(
            review_id=review_id,
            agent_name=data.get("agent_name", ""),
            model_used=data.get("model_used", ""),
            latency=data.get("latency", 0.0),
            tokens=data.get("tokens", 0),
            status=data.get("status", "success"),
        )
        db.add(log)
        created.append(log)
    db.flush()
    return created
