"""End-to-end tests for the FastAPI application — webhook, reviews, and health.

These tests use Starlette's ``TestClient`` (backed by ``httpx``) to exercise
the real HTTP layer of the application.  External dependencies are mocked:

* ``run_review_pipeline`` is patched so the background task is a no-op.
* ``get_db`` is overridden with an in-memory SQLite session so the review
  endpoints can be tested without touching the production database.

Run with::

    pytest tests/test_api.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from database import crud
from database.database import get_db
from database.models import Base
from main import app


# ── Helpers ────────────────────────────────────────────────────────────────


_SECRET = "super-secret-webhook-key"


def _sign(body: bytes, secret: str = _SECRET) -> str:
    """Compute the ``X-Hub-Signature-256`` header value for *body*."""
    digest = hmac.new(
        key=secret.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def _make_pr_payload(
    *,
    action: str = "opened",
    pr_number: int = 42,
    repo: str = "octocat/Hello-World",
    head_sha: str = "abc123def456789",
    branch: str = "feature/cool",
    base: str = "main",
    title: str = "Add cool feature",
) -> dict[str, Any]:
    """Build a minimal but realistic ``pull_request`` webhook payload."""
    return {
        "action": action,
        "pull_request": {
            "number": pr_number,
            "title": title,
            "head": {"sha": head_sha, "ref": branch},
            "base": {"ref": base},
        },
        "repository": {"full_name": repo},
    }


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def db_session() -> Iterator[Session]:
    """Yield a session backed by an in-memory SQLite database."""
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A TestClient with ``get_db`` overridden and webhook secret set."""
    # Ensure the webhook secret matches our test secret.
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", _SECRET)
    # Force settings to re-read env (clear the lru_cache).
    from config import get_settings
    get_settings.cache_clear()
    # Rebind the module-level `settings` in api.routes so the webhook
    # endpoint picks up the new secret (it was bound at import time).
    import api.routes as _routes
    _routes.settings = get_settings()

    def _override_get_db() -> Iterator[Session]:
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture()
def seeded_review_id(db_session: Session) -> int:
    """Create a repository, PR, and review in the test DB; return review id."""
    repo = crud.get_or_create_repository(db_session, name="Hello-World", owner="octocat")
    pr = crud.create_pull_request(
        db_session,
        repository_id=repo.id,
        pr_number=42,
        commit_sha="abc123def456789",
        branch="feature/cool",
    )
    review = crud.create_review(
        db_session,
        pr_id=pr.id,
        overall_score=0.65,
        risk_level="REQUEST_CHANGES",
        summary="Found 2 issues that should be addressed.",
        review_time=3.5,
    )
    crud.bulk_create_issues(
        db_session,
        review_id=review.id,
        issues=[
            {
                "agent": "security",
                "severity": "HIGH",
                "title": "SQL injection in query",
                "description": "User input is concatenated directly.",
                "file": "app.py",
                "line": 42,
                "suggestion": "Use parameterized queries.",
            },
            {
                "agent": "bug",
                "severity": "MEDIUM",
                "title": "Unhandled exception",
                "description": "Division by zero not guarded.",
                "file": "calc.py",
                "line": 10,
                "suggestion": "Add a zero-check.",
            },
        ],
    )
    crud.create_evaluation(
        db_session,
        review_id=review.id,
        confidence=0.85,
        hallucination=False,
        duplicate_rate=0.0,
        quality_score=0.9,
    )
    db_session.commit()
    return review.id


# ══════════════════════════════════════════════════════════════════════════
#  Health & Ready endpoints
# ══════════════════════════════════════════════════════════════════════════


class TestHealthEndpoint:
    """Tests for ``GET /health``."""

    def test_returns_ok(self, client: TestClient) -> None:
        """The health endpoint always returns 200 with status=ok."""
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["service"] == "codeguardian-ai"


class TestReadyEndpoint:
    """Tests for ``GET /ready``."""

    def test_returns_200(self, client: TestClient) -> None:
        """The ready endpoint returns 200."""
        resp = client.get("/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert "status" in body


# ══════════════════════════════════════════════════════════════════════════
#  Webhook endpoint
# ══════════════════════════════════════════════════════════════════════════


class TestWebhookEndpoint:
    """End-to-end tests for ``POST /github/webhook``."""

    def test_valid_pr_webhook_accepted(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A properly signed ``opened`` PR webhook returns status=accepted."""
        # Mock the background pipeline so it doesn't run.
        monkeypatch.setattr("api.routes.run_review_pipeline", lambda event: None)

        payload = _make_pr_payload(action="opened", pr_number=99)
        body = json.dumps(payload).encode("utf-8")

        resp = client.post(
            "/github/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body),
                "Content-Type": "application/json",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["pr_number"] == 99
        assert data["commit_sha"] == "abc123def456789"
        assert "pipeline started" in data["message"].lower()

    def test_invalid_signature_returns_401(self, client: TestClient) -> None:
        """A request with a wrong signature is rejected with 401."""
        payload = _make_pr_payload()
        body = json.dumps(payload).encode("utf-8")

        resp = client.post(
            "/github/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": "sha256=deadbeef",
                "Content-Type": "application/json",
            },
        )

        assert resp.status_code == 401
        assert "signature" in resp.json()["detail"].lower()

    def test_missing_signature_returns_401(self, client: TestClient) -> None:
        """A request with no signature header is rejected with 401."""
        payload = _make_pr_payload()
        body = json.dumps(payload).encode("utf-8")

        resp = client.post(
            "/github/webhook",
            content=body,
            headers={"Content-Type": "application/json"},
        )

        assert resp.status_code == 401

    def test_non_pr_event_ignored(self, client: TestClient) -> None:
        """A ``push`` event (no ``pull_request`` key) returns status=ignored."""
        payload = {"action": "opened", "repository": {"full_name": "octocat/Hello-World"}}
        body = json.dumps(payload).encode("utf-8")

        resp = client.post(
            "/github/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body),
                "Content-Type": "application/json",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ignored"
        assert "pull_request" in data["message"].lower()

    def test_irrelevant_action_ignored(self, client: TestClient) -> None:
        """A ``closed`` action (not in the relevant set) returns status=ignored."""
        payload = _make_pr_payload(action="closed", pr_number=7)
        body = json.dumps(payload).encode("utf-8")

        resp = client.post(
            "/github/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body),
                "Content-Type": "application/json",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ignored"
        assert data["pr_number"] == 7
        assert "does not trigger" in data["message"].lower()

    def test_invalid_json_returns_400(self, client: TestClient) -> None:
        """A body that is not valid JSON returns 400."""
        body = b"not json at all {{{"

        resp = client.post(
            "/github/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body),
                "Content-Type": "application/json",
            },
        )

        assert resp.status_code == 400
        assert "json" in resp.json()["detail"].lower()

    def test_synchronize_action_accepted(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A ``synchronize`` action (new push to PR) triggers a review."""
        monkeypatch.setattr("api.routes.run_review_pipeline", lambda event: None)

        payload = _make_pr_payload(action="synchronize", pr_number=3)
        body = json.dumps(payload).encode("utf-8")

        resp = client.post(
            "/github/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _sign(body),
                "Content-Type": "application/json",
            },
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"


# ══════════════════════════════════════════════════════════════════════════
#  Review retrieval endpoints
# ══════════════════════════════════════════════════════════════════════════


class TestGetReviewEndpoint:
    """Tests for ``GET /reviews/{review_id}``."""

    def test_get_existing_review(
        self,
        client: TestClient,
        seeded_review_id: int,
    ) -> None:
        """Fetching an existing review returns 200 with issues and evaluation."""
        resp = client.get(f"/reviews/{seeded_review_id}")
        assert resp.status_code == 200

        data = resp.json()
        assert data["id"] == seeded_review_id
        assert data["overall_score"] == pytest.approx(0.65)
        assert data["risk_level"] == "REQUEST_CHANGES"
        assert "issues" in data
        assert len(data["issues"]) == 2

        # Check first issue fields.
        issue = data["issues"][0]
        assert issue["agent"] == "security"
        assert issue["severity"] == "HIGH"
        assert issue["file"] == "app.py"
        assert issue["line"] == 42

        # Check evaluation is present.
        assert data["evaluation"] is not None
        assert data["evaluation"]["confidence"] == pytest.approx(0.85)

    def test_get_nonexistent_review_returns_404(self, client: TestClient) -> None:
        """Fetching a review ID that doesn't exist returns 404."""
        resp = client.get("/reviews/99999")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


class TestListReviewsEndpoint:
    """Tests for ``GET /reviews``."""

    def test_empty_list(self, client: TestClient) -> None:
        """With no reviews in the DB, the list is empty and total is 0."""
        resp = client.get("/reviews")
        assert resp.status_code == 200

        data = resp.json()
        assert data["reviews"] == []
        assert data["total"] == 0
        assert data["limit"] == 20
        assert data["offset"] == 0

    def test_list_with_seeded_review(
        self,
        client: TestClient,
        seeded_review_id: int,
    ) -> None:
        """A seeded review appears in the list."""
        resp = client.get("/reviews")
        assert resp.status_code == 200

        data = resp.json()
        assert data["total"] == 1
        assert len(data["reviews"]) == 1
        assert data["reviews"][0]["id"] == seeded_review_id

    def test_pagination_params(
        self,
        client: TestClient,
        seeded_review_id: int,
    ) -> None:
        """Custom limit/offset query params are respected."""
        resp = client.get("/reviews?limit=5&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 5
        assert data["offset"] == 0

    def test_invalid_limit_returns_422(self, client: TestClient) -> None:
        """A limit of 0 (below the ge=1 constraint) returns 422."""
        resp = client.get("/reviews?limit=0")
        assert resp.status_code == 422

    def test_limit_exceeds_max_returns_422(self, client: TestClient) -> None:
        """A limit above 100 (the le=100 constraint) returns 422."""
        resp = client.get("/reviews?limit=101")
        assert resp.status_code == 422
