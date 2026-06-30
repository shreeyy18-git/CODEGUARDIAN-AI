"""Tests for the database models and CRUD operations.

Uses an in-memory SQLite database so tests are fast and isolated.

Run with::

    pytest tests/test_database.py -v
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from database.models import (
    AgentLog,
    Base,
    Evaluation,
    Issue,
    PullRequest,
    Repository,
    Review,
)
from database import crud


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture()
def db() -> Session:
    """Yield a session backed by an in-memory SQLite database."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


# ── Repository tests ────────────────────────────────────────────────────


class TestRepository:
    def test_create_repository(self, db: Session) -> None:
        repo = crud.create_repository(db, name="my-repo", owner="shreeyansh")
        db.commit()
        assert repo.id is not None
        assert repo.name == "my-repo"
        assert repo.owner == "shreeyansh"
        assert repo.full_name == "shreeyansh/my-repo"

    def test_get_or_create_creates_new(self, db: Session) -> None:
        repo = crud.get_or_create_repository(db, name="new-repo", owner="octocat")
        db.commit()
        assert repo.id is not None

    def test_get_or_create_returns_existing(self, db: Session) -> None:
        repo1 = crud.get_or_create_repository(db, name="dup-repo", owner="octocat")
        db.commit()
        repo2 = crud.get_or_create_repository(db, name="dup-repo", owner="octocat")
        db.commit()
        assert repo1.id == repo2.id


# ── PullRequest tests ──────────────────────────────────────────────────


class TestPullRequest:
    def test_create_and_get_pull_request(self, db: Session) -> None:
        repo = crud.create_repository(db, name="repo", owner="owner")
        db.flush()
        pr = crud.create_pull_request(
            db,
            repository_id=repo.id,
            pr_number=42,
            commit_sha="abc123",
            branch="feature/test",
        )
        db.commit()

        fetched = crud.get_pull_request(db, pr.id)
        assert fetched is not None
        assert fetched.pr_number == 42
        assert fetched.commit_sha == "abc123"
        assert fetched.branch == "feature/test"
        assert fetched.status == "pending"

    def test_get_pull_request_by_number(self, db: Session) -> None:
        repo = crud.create_repository(db, name="repo", owner="owner")
        db.flush()
        crud.create_pull_request(
            db,
            repository_id=repo.id,
            pr_number=7,
            commit_sha="def456",
            branch="main",
        )
        db.commit()

        pr = crud.get_pull_request_by_number(db, repo.id, 7)
        assert pr is not None
        assert pr.commit_sha == "def456"


# ── Review tests ────────────────────────────────────────────────────────


class TestReview:
    def test_create_and_get_review(self, db: Session) -> None:
        repo = crud.create_repository(db, name="repo", owner="owner")
        db.flush()
        pr = crud.create_pull_request(
            db, repository_id=repo.id, pr_number=1, commit_sha="sha", branch="b"
        )
        db.flush()
        review = crud.create_review(
            db,
            pr_id=pr.id,
            overall_score=0.85,
            risk_level="APPROVE",
            summary="Looks good!",
            review_time=3.5,
        )
        db.commit()

        fetched = crud.get_review(db, review.id)
        assert fetched is not None
        assert fetched.overall_score == pytest.approx(0.85)
        assert fetched.risk_level == "APPROVE"
        assert fetched.summary == "Looks good!"
        assert fetched.review_time == pytest.approx(3.5)

    def test_get_reviews_by_pr(self, db: Session) -> None:
        repo = crud.create_repository(db, name="repo", owner="owner")
        db.flush()
        pr = crud.create_pull_request(
            db, repository_id=repo.id, pr_number=1, commit_sha="sha", branch="b"
        )
        db.flush()
        crud.create_review(db, pr_id=pr.id, summary="first")
        crud.create_review(db, pr_id=pr.id, summary="second")
        db.commit()

        reviews = crud.get_reviews_by_pr(db, pr.id)
        assert len(reviews) == 2
        # Newest first
        assert reviews[0].summary == "second"


# ── Issue tests ─────────────────────────────────────────────────────────


class TestIssue:
    def test_create_issue(self, db: Session) -> None:
        repo = crud.create_repository(db, name="repo", owner="owner")
        db.flush()
        pr = crud.create_pull_request(
            db, repository_id=repo.id, pr_number=1, commit_sha="sha", branch="b"
        )
        db.flush()
        review = crud.create_review(db, pr_id=pr.id)
        db.flush()

        issue = crud.create_issue(
            db,
            review_id=review.id,
            agent="security",
            severity="CRITICAL",
            title="SQL Injection",
            description="Unparameterized query",
            file="app.py",
            line=42,
            suggestion="Use parameterized queries",
        )
        db.commit()

        assert issue.id is not None
        assert issue.agent == "security"
        assert issue.severity == "CRITICAL"
        assert issue.line == 42

    def test_bulk_create_issues(self, db: Session) -> None:
        repo = crud.create_repository(db, name="repo", owner="owner")
        db.flush()
        pr = crud.create_pull_request(
            db, repository_id=repo.id, pr_number=1, commit_sha="sha", branch="b"
        )
        db.flush()
        review = crud.create_review(db, pr_id=pr.id)
        db.flush()

        issues_data = [
            {
                "agent": "security",
                "severity": "HIGH",
                "title": "Issue 1",
                "file": "a.py",
                "line": 10,
            },
            {
                "agent": "bug",
                "severity": "MEDIUM",
                "title": "Issue 2",
                "file": "b.py",
                "line": 20,
            },
        ]
        created = crud.bulk_create_issues(db, review_id=review.id, issues=issues_data)
        db.commit()

        assert len(created) == 2
        assert all(i.id is not None for i in created)
        assert created[0].title == "Issue 1"
        assert created[1].agent == "bug"


# ── Evaluation tests ────────────────────────────────────────────────────


class TestEvaluation:
    def test_create_evaluation(self, db: Session) -> None:
        repo = crud.create_repository(db, name="repo", owner="owner")
        db.flush()
        pr = crud.create_pull_request(
            db, repository_id=repo.id, pr_number=1, commit_sha="sha", branch="b"
        )
        db.flush()
        review = crud.create_review(db, pr_id=pr.id)
        db.flush()

        evaluation = crud.create_evaluation(
            db,
            review_id=review.id,
            confidence=0.92,
            hallucination=False,
            duplicate_rate=0.1,
            quality_score=0.88,
        )
        db.commit()

        assert evaluation.id is not None
        assert evaluation.confidence == pytest.approx(0.92)
        assert evaluation.hallucination is False
        assert evaluation.duplicate_rate == pytest.approx(0.1)


# ── AgentLog tests ──────────────────────────────────────────────────────


class TestAgentLog:
    def test_create_agent_log(self, db: Session) -> None:
        repo = crud.create_repository(db, name="repo", owner="owner")
        db.flush()
        pr = crud.create_pull_request(
            db, repository_id=repo.id, pr_number=1, commit_sha="sha", branch="b"
        )
        db.flush()
        review = crud.create_review(db, pr_id=pr.id)
        db.flush()

        log = crud.create_agent_log(
            db,
            review_id=review.id,
            agent_name="security",
            model_used="llama-3.3-70b-versatile",
            latency=1.2,
            tokens=500,
            status="success",
        )
        db.commit()

        assert log.id is not None
        assert log.agent_name == "security"
        assert log.model_used == "llama-3.3-70b-versatile"
        assert log.tokens == 500

    def test_bulk_create_agent_logs(self, db: Session) -> None:
        repo = crud.create_repository(db, name="repo", owner="owner")
        db.flush()
        pr = crud.create_pull_request(
            db, repository_id=repo.id, pr_number=1, commit_sha="sha", branch="b"
        )
        db.flush()
        review = crud.create_review(db, pr_id=pr.id)
        db.flush()

        logs_data = [
            {"agent_name": "security", "model_used": "llama-3.3-70b-versatile", "latency": 1.0},
            {"agent_name": "bug", "model_used": "gemini-2.0-flash", "latency": 2.0, "status": "error"},
        ]
        created = crud.bulk_create_agent_logs(db, review_id=review.id, logs=logs_data)
        db.commit()

        assert len(created) == 2
        assert created[0].agent_name == "security"
        assert created[1].status == "error"


# ── Cascade delete tests ────────────────────────────────────────────────


class TestCascadeDelete:
    def test_deleting_review_cascades_to_children(self, db: Session) -> None:
        """Deleting a review should delete its issues, evaluations, and logs."""
        repo = crud.create_repository(db, name="repo", owner="owner")
        db.flush()
        pr = crud.create_pull_request(
            db, repository_id=repo.id, pr_number=1, commit_sha="sha", branch="b"
        )
        db.flush()
        review = crud.create_review(db, pr_id=pr.id)
        db.flush()

        crud.create_issue(db, review_id=review.id, agent="sec", severity="LOW", title="t")
        crud.create_evaluation(db, review_id=review.id)
        crud.create_agent_log(
            db, review_id=review.id, agent_name="sec", model_used="m"
        )
        db.commit()
        review_id = review.id

        db.delete(review)
        db.commit()

        assert db.get(Issue, 1) is None
        assert db.get(Evaluation, 1) is None
        assert db.get(AgentLog, 1) is None
        assert db.get(Review, review_id) is None

    def test_deleting_pr_cascades_to_reviews(self, db: Session) -> None:
        """Deleting a PR should delete its reviews."""
        repo = crud.create_repository(db, name="repo", owner="owner")
        db.flush()
        pr = crud.create_pull_request(
            db, repository_id=repo.id, pr_number=1, commit_sha="sha", branch="b"
        )
        db.flush()
        review = crud.create_review(db, pr_id=pr.id)
        db.commit()
        review_id = review.id

        db.delete(pr)
        db.commit()

        assert db.get(Review, review_id) is None
