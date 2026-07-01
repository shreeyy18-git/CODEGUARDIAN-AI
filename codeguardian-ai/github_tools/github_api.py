"""Thin wrapper around PyGithub for PR data retrieval.

This module isolates all GitHub REST-API calls behind a small set of
functions so the rest of the codebase never touches ``github.Github``
objects directly.  That makes the pipeline easy to mock in tests and
keeps the API surface minimal.

All functions accept a ``repo_full_name`` (``"owner/repo"``) and use
the ``GITHUB_TOKEN`` from :mod:`config` for authentication.

Usage::

    from github.github_api import (
        fetch_pr_diff, fetch_changed_files, fetch_file_content,
        post_pr_comment, create_check_run,
    )

    diff = fetch_pr_diff("owner/repo", 42)
    files = fetch_changed_files("owner/repo", 42)
"""

from __future__ import annotations

import logging
from typing import Any

from config import settings

__all__ = [
    "get_github_client",
    "get_repo",
    "fetch_pr_diff",
    "fetch_changed_files",
    "fetch_file_content",
    "post_pr_comment",
    "create_check_run",
]

_log = logging.getLogger(__name__)

# Module-level cache for the Github client so we don't re-authenticate
# on every call.  Cleared by :func:`_reset_client_cache` in tests.
_client: Any = None
_repo_cache: dict[str, Any] = {}


def get_github_client() -> Any:
    """Return a cached :class:`github.Github` instance.

    Uses ``settings.github_token`` for authentication.  If the token is
    empty, an unauthenticated client is returned (rate-limited but
    useful for public-repo reads in development).

    Returns
    -------
    github.Github
        Authenticated (or anonymous) GitHub client.
    """
    global _client
    if _client is not None:
        return _client

    # Lazy import so the module loads even if PyGithub isn't installed.
    from github import Github  # type: ignore[import-not-found]

    if settings.github_token:
        _client = Github(login_or_token=settings.github_token)
        _log.debug("GitHub client created with token auth")
    else:
        _client = Github()
        _log.warning(
            "GITHUB_TOKEN is empty — using unauthenticated GitHub "
            "client (rate-limited, no write access)."
        )
    return _client


def get_repo(repo_full_name: str) -> Any:
    """Return a cached :class:`github.Repository.Repository` by full name.

    Parameters
    ----------
    repo_full_name:
        ``"owner/repo"`` identifier.
    """
    if repo_full_name in _repo_cache:
        return _repo_cache[repo_full_name]
    client = get_github_client()
    repo = client.get_repo(repo_full_name)
    _repo_cache[repo_full_name] = repo
    return repo


def fetch_pr_diff(repo_full_name: str, pr_number: int) -> str:
    """Fetch the unified diff for a pull request.

    Parameters
    ----------
    repo_full_name:
        ``"owner/repo"``.
    pr_number:
        The PR issue number.

    Returns
    -------
    str
        The full unified-diff text.  Returns an empty string if the
        diff cannot be retrieved.
    """
    repo = get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)
    # PyGithub's ``.get_files()`` truncates large diffs; the raw diff
    # endpoint via the ``patch`` / ``diff`` accept header is more
    # reliable.  We use the ``diff_url`` attribute which points to the
    # raw patch.
    diff_url = getattr(pr, "diff_url", None)
    if diff_url:
        import httpx

        headers: dict[str, str] = {}
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        resp = httpx.get(diff_url, headers=headers, timeout=30, follow_redirects=True)
        if resp.status_code == 200:
            return resp.text
        _log.warning(
            "Failed to fetch diff from %s: HTTP %s",
            diff_url,
            resp.status_code,
        )
    # Fallback: assemble from individual file patches.
    files = pr.get_files()
    parts: list[str] = []
    for f in files:
        if f.patch:
            parts.append(f.patch)
    return "\n".join(parts)


def fetch_changed_files(repo_full_name: str, pr_number: int) -> list[str]:
    """Return the list of file paths changed in a pull request.

    Parameters
    ----------
    repo_full_name:
        ``"owner/repo"``.
    pr_number:
        The PR issue number.

    Returns
    -------
    list[str]
        File paths relative to the repo root (new-revision paths).
    """
    repo = get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)
    return [f.filename for f in pr.get_files()]


def fetch_file_content(
    repo_full_name: str,
    path: str,
    ref: str,
) -> str:
    """Fetch the raw content of a single file at a given ref.

    Parameters
    ----------
    repo_full_name:
        ``"owner/repo"``.
    path:
        Path to the file within the repository.
    ref:
        Git ref (commit SHA, branch, or tag) to read from.

    Returns
    -------
    str
        Decoded file content.  Returns an empty string if the file is
        empty or cannot be read (e.g. it was deleted in this PR).
    """
    repo = get_repo(repo_full_name)
    try:
        content = repo.get_contents(path, ref=ref)
    except Exception as exc:  # noqa: BLE001 — broad on purpose
        _log.debug("Could not fetch %s @ %s: %s", path, ref, exc)
        return ""
    # ``get_contents`` may return a list (directory) — guard against that.
    if isinstance(content, list):
        return ""
    if content.content is None:
        # Large files use ``content.encoding == "none"``; fetch via raw URL.
        if getattr(content, "download_url", None):
            import httpx

            resp = httpx.get(content.download_url, timeout=30)
            if resp.status_code == 200:
                return resp.text
        return ""
    return content.decoded_content.decode("utf-8", errors="replace")


def post_pr_comment(repo_full_name: str, pr_number: int, body: str) -> None:
    """Post (or update) a review comment on a pull request.

    If a previous CodeGuardian comment exists it is updated in place
    rather than creating a duplicate (see :mod:`github.comments` for
    the update logic — this function is the low-level poster).

    Parameters
    ----------
    repo_full_name:
        ``"owner/repo"``.
    pr_number:
        The PR issue number.
    body:
        Markdown body of the comment.
    """
    repo = get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)
    pr.create_issue_comment(body)
    _log.info("Posted review comment on PR #%s in %s", pr_number, repo_full_name)


def create_check_run(
    repo_full_name: str,
    head_sha: str,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
    title: str = "CodeGuardian AI Review",
    summary: str = "",
    text: str = "",
) -> Any:
    """Create a GitHub Check Run for a commit.

    Parameters
    ----------
    repo_full_name:
        ``"owner/repo"``.
    head_sha:
        The commit SHA the check run applies to.
    status:
        ``"queued"``, ``"in_progress"``, or ``"completed"``.
    conclusion:
        Required when ``status == "completed"`` — one of ``"success"``,
        ``"failure"``, ``"neutral"``, ``"cancelled"``, ``"timed_out"``,
        or ``"action_required"``.  Pass ``None`` for non-completed runs.
    title:
        Short title shown in the checks UI.
    summary:
        Markdown summary (first line of the check output).
    text:
        Optional longer markdown body for the check output.

    Returns
    -------
    github.CheckRun.CheckRun
        The created check-run object.
    """
    repo = get_repo(repo_full_name)
    output: dict[str, str] | None = None
    if summary or text:
        output = {
            "title": title,
            "summary": summary or "",
            "text": text or "",
        }
    check = repo.create_check_run(
        name=title,
        head_sha=head_sha,
        status=status,
        conclusion=conclusion,
        output=output,
    )
    _log.info(
        "Created check run '%s' (status=%s, conclusion=%s) for %s @ %s",
        title,
        status,
        conclusion,
        repo_full_name,
        head_sha[:7],
    )
    return check


def _reset_client_cache() -> None:
    """Clear the cached GitHub client and repo objects (for tests)."""
    global _client
    _client = None
    _repo_cache.clear()
