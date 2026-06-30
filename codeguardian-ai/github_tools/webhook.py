"""GitHub webhook verification and payload parsing.

GitHub signs every webhook delivery with an HMAC-SHA256 of the raw
request body using a shared secret.  This module provides:

* :func:`verify_signature` — constant-time HMAC verification.
* :func:`parse_pull_request_event` — extract the fields the pipeline
  needs (PR number, commit SHA, repo full name, branch) from a
  ``pull_request`` webhook payload.

The FastAPI route (Phase 11) will call these functions; they are kept
framework-agnostic here so they can be unit-tested in isolation.

Usage::

    from github.webhook import verify_signature, parse_pull_request_event

    if not verify_signature(raw_body, signature_header, secret):
        raise HTTPException(401, "Invalid signature")

    event = parse_pull_request_event(payload)
    if event is None:        # not a PR event we care about
        return {"status": "ignored"}
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

__all__ = [
    "PullRequestEvent",
    "verify_signature",
    "parse_pull_request_event",
    "is_pull_request_action_relevant",
]

_log = __import__("logging").getLogger(__name__)

# Actions that should trigger a review.  ``opened`` = new PR;
# ``synchronize`` = new commits pushed to an existing PR.
_RELEVANT_ACTIONS: frozenset[str] = frozenset({"opened", "synchronize", "reopened"})


@dataclass(frozen=True)
class PullRequestEvent:
    """Essential fields extracted from a ``pull_request`` webhook.

    Attributes
    ----------
    action:
        The webhook action (``"opened"``, ``"synchronize"``, …).
    pr_number:
        The PR's issue number (e.g. ``42``).
    pr_title:
        The PR title.
    commit_sha:
        The SHA of the PR head commit (the code under review).
    repo_full_name:
        ``"owner/repo"`` — used to look up the repository via the API.
    branch:
        The head branch name (e.g. ``"feature/login"``).
    base_branch:
        The target branch (e.g. ``"main"``).
    """

    action: str
    pr_number: int
    pr_title: str
    commit_sha: str
    repo_full_name: str
    branch: str
    base_branch: str


def verify_signature(raw_body: bytes, signature_header: str, secret: str) -> bool:
    """Verify a GitHub webhook signature.

    GitHub sends the HMAC-SHA256 digest in the ``X-Hub-Signature-256``
    header in the form ``sha256=<hex>``.

    Parameters
    ----------
    raw_body:
        The *raw* request body bytes (before any JSON parsing).
    signature_header:
        Value of the ``X-Hub-Signature-256`` header.
    secret:
        The shared webhook secret (``GITHUB_WEBHOOK_SECRET``).

    Returns
    -------
    bool
        ``True`` if the signature matches, ``False`` otherwise.
        Also returns ``True`` when *no* secret is configured (development
        mode) so local testing works without signature checks.
    """
    # Development convenience: if no secret is configured, skip
    # verification.  In production a secret MUST be set.
    if not secret:
        _log.warning(
            "GITHUB_WEBHOOK_SECRET is empty — skipping signature "
            "verification.  This is insecure for production."
        )
        return True

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = signature_header.removeprefix("sha256=").strip()
    digest = hmac.new(
        key=secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    # ``hmac.compare_digest`` is constant-time to prevent timing attacks.
    return hmac.compare_digest(digest, expected)


def is_pull_request_action_relevant(action: str) -> bool:
    """Return ``True`` if the webhook action should trigger a review.

    Only ``opened``, ``synchronize``, and ``reopened`` trigger reviews.
    Other actions (``closed``, ``edited``, ``assigned``, …) are ignored.
    """
    return action in _RELEVANT_ACTIONS


def parse_pull_request_event(payload: dict[str, Any]) -> PullRequestEvent | None:
    """Extract a :class:`PullRequestEvent` from a webhook payload.

    Parameters
    ----------
    payload:
        The parsed JSON body of the webhook delivery.  Must be a
        ``pull_request`` event payload.

    Returns
    -------
    PullRequestEvent | None
        The extracted event, or ``None`` if the payload is not a
        ``pull_request`` event or is missing required fields.
    """
    if "pull_request" not in payload:
        return None

    pr = payload["pull_request"]
    action = payload.get("action", "")

    try:
        repo_full_name = payload["repository"]["full_name"]
    except (KeyError, TypeError):
        _log.error("Webhook payload missing repository.full_name")
        return None

    try:
        commit_sha = pr["head"]["sha"]
        branch = pr["head"]["ref"]
        base_branch = pr["base"]["ref"]
    except (KeyError, TypeError):
        _log.error("Webhook payload missing pull_request.head/base fields")
        return None

    try:
        pr_number = int(pr["number"])
    except (KeyError, ValueError, TypeError):
        _log.error("Webhook payload missing or invalid pull_request.number")
        return None

    pr_title = pr.get("title", "")

    return PullRequestEvent(
        action=action,
        pr_number=pr_number,
        pr_title=pr_title,
        commit_sha=commit_sha,
        repo_full_name=repo_full_name,
        branch=branch,
        base_branch=base_branch,
    )


def safe_parse_json(raw_body: bytes) -> dict[str, Any] | None:
    """Parse a webhook body as JSON, returning ``None`` on failure.

    Parameters
    ----------
    raw_body:
        Raw request body bytes.

    Returns
    -------
    dict | None
        Parsed payload or ``None`` if the body is not valid JSON.
    """
    try:
        parsed = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed
