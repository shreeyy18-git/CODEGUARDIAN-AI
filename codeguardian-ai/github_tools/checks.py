"""GitHub Check Run lifecycle management.

A Check Run is GitHub's mechanism for gating merges — when branch
protection requires the "CodeGuardian AI Review" check to pass, a
``failure`` conclusion blocks the merge button.

This module provides a small state machine:

* :func:`start_check` — create a check run in ``in_progress`` state.
* :func:`complete_check` — mark it ``completed`` with ``success`` or
  ``failure`` based on the review verdict.
* :func:`fail_check` — mark it ``completed`` with ``failure`` (error).

Usage::

    from github.checks import start_check, complete_check

    check = start_check("owner/repo", commit_sha)
    # ... run review ...
    complete_check("owner/repo", commit_sha, verdict="APPROVE",
                   summary="No issues found", score=0.95)
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = [
    "CHECK_NAME",
    "start_check",
    "complete_check",
    "fail_check",
]

_log = logging.getLogger(__name__)

# The check-run name shown in the GitHub UI.  This is the name users
# add to their branch-protection "required status checks" list.
CHECK_NAME = "CodeGuardian AI Review"

# Map review verdicts to check-run conclusions.
_VERDICT_TO_CONCLUSION = {
    "APPROVE": "success",
    "REQUEST_CHANGES": "failure",
    "BLOCK_MERGE": "failure",
    "PENDING": None,  # not completed yet
}


def start_check(
    repo_full_name: str,
    head_sha: str,
    *,
    summary: str = "Review in progress…",
) -> Any:
    """Create a check run in the ``in_progress`` state.

    Parameters
    ----------
    repo_full_name:
        ``"owner/repo"``.
    head_sha:
        The commit SHA the check applies to.
    summary:
        Initial summary text shown while the review runs.

    Returns
    -------
    github.CheckRun.CheckRun
        The created check-run object.
    """
    from github_tools.github_api import create_check_run

    _log.info("Starting check run for %s @ %s", repo_full_name, head_sha[:7])
    return create_check_run(
        repo_full_name,
        head_sha,
        status="in_progress",
        conclusion=None,
        title=CHECK_NAME,
        summary=summary,
    )


def complete_check(
    repo_full_name: str,
    head_sha: str,
    *,
    verdict: str,
    score: float,
    summary: str,
    text: str = "",
) -> Any:
    """Mark the check run as ``completed`` with a pass/fail conclusion.

    The conclusion is derived from the ``verdict``:

    +---------------------+-------------+
    | Verdict             | Conclusion  |
    +=====================+=============+
    | APPROVE             | success     |
    | REQUEST_CHANGES     | failure     |
    | BLOCK_MERGE         | failure     |
    +---------------------+-------------+

    Parameters
    ----------
    repo_full_name:
        ``"owner/repo"``.
    head_sha:
        The commit SHA the check applies to.
    verdict:
        One of ``"APPROVE"``, ``"REQUEST_CHANGES"``, ``"BLOCK_MERGE"``.
    score:
        Overall risk score in ``[0.0, 1.0]`` (higher = safer).
    summary:
        Markdown summary for the check output.
    text:
        Optional longer markdown body.

    Returns
    -------
    github.CheckRun.CheckRun
        The updated check-run object.
    """
    from github_tools.github_api import create_check_run

    conclusion = _VERDICT_TO_CONCLUSION.get(verdict, "neutral")
    score_pct = round(score * 100)

    full_summary = (
        f"**Verdict:** {verdict}  |  **Risk Score:** {score_pct}/100\n\n"
        f"{summary}"
    )

    _log.info(
        "Completing check run for %s @ %s — verdict=%s, conclusion=%s",
        repo_full_name,
        head_sha[:7],
        verdict,
        conclusion,
    )
    return create_check_run(
        repo_full_name,
        head_sha,
        status="completed",
        conclusion=conclusion,
        title=CHECK_NAME,
        summary=full_summary,
        text=text,
    )


def fail_check(
    repo_full_name: str,
    head_sha: str,
    *,
    error_message: str,
) -> Any:
    """Mark the check run as ``completed`` with ``failure`` (error).

    Used when the review pipeline itself crashes and we need to
    surface the error to the developer via the checks UI.

    Parameters
    ----------
    repo_full_name:
        ``"owner/repo"``.
    head_sha:
        The commit SHA the check applies to.
    error_message:
        Description of what went wrong.

    Returns
    -------
    github.CheckRun.CheckRun
        The created check-run object.
    """
    from github_tools.github_api import create_check_run

    _log.error(
        "Failing check run for %s @ %s: %s",
        repo_full_name,
        head_sha[:7],
        error_message,
    )
    return create_check_run(
        repo_full_name,
        head_sha,
        status="completed",
        conclusion="failure",
        title=CHECK_NAME,
        summary=f"❌ CodeGuardian AI encountered an error:\n\n```\n{error_message}\n```",
    )
