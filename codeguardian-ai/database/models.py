"""SQLAlchemy ORM models for CodeGuardian AI.

Six tables matching the ERD in ``plan.md`` §12.1::

    repositories  ─┐
                   ├─ pull_requests ─┐
                   │                 ├─ reviews ─┬─ issues
                   │                 │          ├─ evaluations
                   │                 │          └─ agent_logs

All foreign keys use ``ondelete="CASCADE"`` so deleting a parent row
removes its children automatically.
"""

from __future__ import annotations

from datetime import datetime
from typing import List

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


class Repository(Base):
    """A GitHub repository under review.

    Attributes
    ----------
    id:
        Auto-increment primary key.
    name:
        Repository name (e.g. ``"multi-agent-code-review"``).
    owner:
        Repository owner / org (e.g. ``"shreeyansh"``).
    full_name:
        Convenience property — ``"owner/name"``.
    """

    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)

    pull_requests: Mapped[List["PullRequest"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )

    @property
    def full_name(self) -> str:
        """Return ``"owner/name"`` for GitHub API compatibility."""
        return f"{self.owner}/{self.name}"


class PullRequest(Base):
    """A pull request within a repository.

    Attributes
    ----------
    id:
        Auto-increment primary key.
    repository_id:
        FK to :class:`Repository`.
    pr_number:
        GitHub PR number (not the DB id).
    commit_sha:
        Head commit SHA of the PR.
    branch:
        Head branch name (e.g. ``"feature/add-login"``).
    status:
        Review status — ``"pending"``, ``"in_progress"``, ``"completed"``.
    created_at:
        Timestamp of record creation.
    """

    __tablename__ = "pull_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    repository: Mapped["Repository"] = relationship(back_populates="pull_requests")
    reviews: Mapped[List["Review"]] = relationship(
        back_populates="pull_request", cascade="all, delete-orphan"
    )


class Review(Base):
    """A completed (or in-progress) code review for a PR.

    Attributes
    ----------
    id:
        Auto-increment primary key.
    pr_id:
        FK to :class:`PullRequest`.
    overall_score:
        Weighted risk score in ``[0.0, 1.0]`` (higher = safer).
    risk_level:
        Verdict — ``"APPROVE"``, ``"REQUEST_CHANGES"``, or ``"BLOCK_MERGE"``.
    summary:
        Human-readable review summary (markdown).
    review_time:
        Total review latency in seconds.
    created_at:
        Timestamp of review creation.
    """

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pr_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False
    )
    overall_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    risk_level: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    review_time: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    pull_request: Mapped["PullRequest"] = relationship(back_populates="reviews")
    issues: Mapped[List["Issue"]] = relationship(
        back_populates="review", cascade="all, delete-orphan"
    )
    evaluations: Mapped[List["Evaluation"]] = relationship(
        back_populates="review", cascade="all, delete-orphan"
    )
    agent_logs: Mapped[List["AgentLog"]] = relationship(
        back_populates="review", cascade="all, delete-orphan"
    )


class Issue(Base):
    """An individual finding raised by an agent during a review.

    Attributes
    ----------
    id:
        Auto-increment primary key.
    review_id:
        FK to :class:`Review`.
    agent:
        Which agent found this (e.g. ``"security"``, ``"bug"``).
    severity:
        ``"CRITICAL"``, ``"HIGH"``, ``"MEDIUM"``, or ``"LOW"``.
    title:
        Short one-line summary of the issue.
    description:
        Detailed explanation.
    file:
        File path where the issue was found.
    line:
        Line number (1-based) or ``0`` if unknown.
    suggestion:
        Suggested fix or remediation.
    """

    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False
    )
    agent: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    file: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    line: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    suggestion: Mapped[str] = mapped_column(Text, nullable=False, default="")

    review: Mapped["Review"] = relationship(back_populates="issues")


class Evaluation(Base):
    """Quality evaluation metrics for a review.

    Attributes
    ----------
    id:
        Auto-increment primary key.
    review_id:
        FK to :class:`Review`.
    confidence:
        Overall confidence score ``[0.0, 1.0]``.
    hallucination:
        ``True`` if a hallucination was detected.
    duplicate_rate:
        Fraction ``[0.0, 1.0]`` of duplicate findings.
    quality_score:
        Composite quality score ``[0.0, 1.0]``.
    created_at:
        Timestamp of evaluation creation.
    """

    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    hallucination: Mapped[bool] = mapped_column(nullable=False, default=False)
    duplicate_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    review: Mapped["Review"] = relationship(back_populates="evaluations")


class AgentLog(Base):
    """Execution log for a single agent invocation within a review.

    Attributes
    ----------
    id:
        Auto-increment primary key.
    review_id:
        FK to :class:`Review`.
    agent_name:
        Which agent ran (e.g. ``"security"``, ``"bug"``).
    model_used:
        Which LLM model handled the call (e.g. ``"llama-3.3-70b-versatile"``).
    latency:
        Wall-clock latency in seconds.
    tokens:
        Total tokens consumed (prompt + completion).
    status:
        ``"success"`` or ``"error"``.
    """

    __tablename__ = "agent_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False
    )
    agent_name: Mapped[str] = mapped_column(String(50), nullable=False)
    model_used: Mapped[str] = mapped_column(String(100), nullable=False)
    latency: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")

    review: Mapped["Review"] = relationship(back_populates="agent_logs")
