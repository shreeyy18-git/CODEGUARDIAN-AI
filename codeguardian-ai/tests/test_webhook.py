"""Tests for the GitHub webhook verification, payload parsing, and diff parser.

These tests are fully self-contained — no network access or GitHub API
calls are required.  They cover:

* HMAC-SHA256 signature verification (valid, invalid, missing, dev-mode).
* Pull-request event parsing (happy path and malformed payloads).
* Action-relevance filtering.
* Safe JSON parsing of the raw webhook body.
* Unified-diff parsing (added / modified / deleted / renamed files,
  hunk line-counting, and added-line extraction).

Run with::

    pytest tests/test_webhook.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest

from github_tools.diff import (
    DiffHunk,
    FileDiff,
    extract_added_lines,
    parse_diff,
)
from github_tools.webhook import (
    PullRequestEvent,
    is_pull_request_action_relevant,
    parse_pull_request_event,
    safe_parse_json,
    verify_signature,
)


# ── Helpers ─────────────────────────────────────────────────────────────


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


# A realistic single-file unified diff used across the diff-parser tests.
_DIFF_MODIFIED = """\
diff --git a/example.py b/example.py
index 1234567..abcdefg 100644
--- a/example.py
+++ b/example.py
@@ -1,3 +1,4 @@
 def hello():
-    print("hi")
+    print("hello")
+    print("world")
     return True
"""

_DIFF_ADDED = """\
diff --git a/newfile.py b/newfile.py
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/newfile.py
@@ -0,0 +1,3 @@
+def new_func():
+    return 42
+    # end
"""

_DIFF_DELETED = """\
diff --git a/oldfile.py b/oldfile.py
deleted file mode 100644
index abc1234..0000000
--- a/oldfile.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def gone():
-    return None
"""

_DIFF_RENAMED = """\
diff --git a/old_name.py b/new_name.py
similarity index 90%
rename from old_name.py
rename to new_name.py
index abc1234..def5678 100644
--- a/old_name.py
+++ b/new_name.py
@@ -1,3 +1,3 @@
 def func():
-    return 1
+    return 2
     # end
"""

# A hunk header with no comma-separated counts (single-line hunks).
_DIFF_NO_COUNT = """\
diff --git a/single.py b/single.py
index 111..222 100644
--- a/single.py
+++ b/single.py
@@ -10 +10 @@
-x = 1
+x = 2
"""


# ── Signature verification tests ────────────────────────────────────────


class TestVerifySignature:
    """Tests for :func:`github.webhook.verify_signature`."""

    def test_valid_signature(self) -> None:
        """A correctly computed HMAC-SHA256 signature is accepted."""
        body = b'{"action":"opened"}'
        header = _sign(body)
        assert verify_signature(body, header, _SECRET) is True

    def test_invalid_signature(self) -> None:
        """A wrong digest is rejected."""
        body = b'{"action":"opened"}'
        header = "sha256=deadbeef" + "0" * 56
        assert verify_signature(body, header, _SECRET) is False

    def test_tampered_body(self) -> None:
        """If the body is changed after signing, verification fails."""
        body = b'{"action":"opened"}'
        header = _sign(body)
        assert verify_signature(b'{"action":"closed"}', header, _SECRET) is False

    def test_missing_header(self) -> None:
        """An empty header string is rejected (when a secret is set)."""
        assert verify_signature(b"{}", "", _SECRET) is False

    def test_malformed_header_no_prefix(self) -> None:
        """A header without the ``sha256=`` prefix is rejected."""
        body = b"{}"
        digest = hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
        assert verify_signature(body, digest, _SECRET) is False

    def test_empty_secret_returns_true(self) -> None:
        """In dev mode (no secret) verification is skipped and returns True."""
        assert verify_signature(b"{}", "sha256=anything", "") is True

    def test_empty_secret_ignores_header(self) -> None:
        """Dev mode returns True even with a malformed/empty header."""
        assert verify_signature(b"{}", "", "") is True

    def test_wrong_secret_rejected(self) -> None:
        """A signature from a different secret is rejected."""
        body = b'{"action":"opened"}'
        header = _sign(body, secret="wrong-secret")
        assert verify_signature(body, header, _SECRET) is False


# ── Action-relevance tests ─────────────────────────────────────────────


class TestIsPullRequestActionRelevant:
    """Tests for :func:`github.webhook.is_pull_request_action_relevant`."""

    @pytest.mark.parametrize("action", ["opened", "synchronize", "reopened"])
    def test_relevant_actions(self, action: str) -> None:
        """Actions that should trigger a review return ``True``."""
        assert is_pull_request_action_relevant(action) is True

    @pytest.mark.parametrize(
        "action",
        ["closed", "edited", "assigned", "labeled", "ready_for_review", ""],
    )
    def test_irrelevant_actions(self, action: str) -> None:
        """Actions that should NOT trigger a review return ``False``."""
        assert is_pull_request_action_relevant(action) is False


# ── Pull-request event parsing tests ───────────────────────────────────


class TestParsePullRequestEvent:
    """Tests for :func:`github.webhook.parse_pull_request_event`."""

    def test_valid_payload(self) -> None:
        """A well-formed payload yields a fully-populated event."""
        payload = _make_pr_payload()
        event = parse_pull_request_event(payload)
        assert isinstance(event, PullRequestEvent)
        assert event.action == "opened"
        assert event.pr_number == 42
        assert event.pr_title == "Add cool feature"
        assert event.commit_sha == "abc123def456789"
        assert event.repo_full_name == "octocat/Hello-World"
        assert event.branch == "feature/cool"
        assert event.base_branch == "main"

    def test_missing_pull_request_key(self) -> None:
        """A payload without ``pull_request`` returns ``None``."""
        assert parse_pull_request_event({"action": "opened"}) is None

    def test_missing_repository(self) -> None:
        """A payload without ``repository`` returns ``None``."""
        payload = _make_pr_payload()
        del payload["repository"]
        assert parse_pull_request_event(payload) is None

    def test_missing_repository_full_name(self) -> None:
        """A ``repository`` dict without ``full_name`` returns ``None``."""
        payload = _make_pr_payload()
        payload["repository"] = {"id": 123}
        assert parse_pull_request_event(payload) is None

    def test_missing_head_sha(self) -> None:
        """A ``pull_request.head`` without ``sha`` returns ``None``."""
        payload = _make_pr_payload()
        payload["pull_request"]["head"] = {"ref": "feature/cool"}
        assert parse_pull_request_event(payload) is None

    def test_missing_base_ref(self) -> None:
        """A ``pull_request.base`` without ``ref`` returns ``None``."""
        payload = _make_pr_payload()
        payload["pull_request"]["base"] = {"label": "octocat:main"}
        assert parse_pull_request_event(payload) is None

    def test_invalid_pr_number(self) -> None:
        """A non-integer ``number`` returns ``None``."""
        payload = _make_pr_payload()
        payload["pull_request"]["number"] = "not-a-number"
        assert parse_pull_request_event(payload) is None

    def test_missing_title_defaults_empty(self) -> None:
        """When ``title`` is absent the event title is an empty string."""
        payload = _make_pr_payload()
        del payload["pull_request"]["title"]
        event = parse_pull_request_event(payload)
        assert event is not None
        assert event.pr_title == ""

    def test_head_is_none(self) -> None:
        """A ``None`` head dict is handled gracefully (returns ``None``)."""
        payload = _make_pr_payload()
        payload["pull_request"]["head"] = None  # type: ignore[assignment]
        assert parse_pull_request_event(payload) is None


# ── Safe JSON parsing tests ────────────────────────────────────────────


class TestSafeParseJson:
    """Tests for :func:`github.webhook.safe_parse_json`."""

    def test_valid_json(self) -> None:
        """A valid JSON object is returned as a dict."""
        body = json.dumps({"action": "opened"}).encode()
        result = safe_parse_json(body)
        assert result == {"action": "opened"}

    def test_invalid_json(self) -> None:
        """Malformed JSON returns ``None``."""
        assert safe_parse_json(b"{not json}") is None

    def test_non_dict_json(self) -> None:
        """A JSON array or scalar returns ``None`` (only dicts are valid)."""
        assert safe_parse_json(b"[1, 2, 3]") is None
        assert safe_parse_json(b"42") is None
        assert safe_parse_json(b'"hello"') is None

    def test_empty_body(self) -> None:
        """An empty body returns ``None``."""
        assert safe_parse_json(b"") is None

    def test_unicode_decode_error(self) -> None:
        """Invalid UTF-8 bytes return ``None`` rather than raising."""
        assert safe_parse_json(b"\xff\xfe\x00") is None


# ── Diff parser tests ──────────────────────────────────────────────────


class TestParseDiff:
    """Tests for :func:`github.diff.parse_diff`."""

    def test_empty_diff(self) -> None:
        """An empty or whitespace-only diff yields no files."""
        assert parse_diff("") == []
        assert parse_diff("   \n  ") == []

    def test_single_modified_file(self) -> None:
        """A modified file is parsed with correct counts and status."""
        files = parse_diff(_DIFF_MODIFIED)
        assert len(files) == 1
        f = files[0]
        assert f.path == "example.py"
        assert f.status == "modified"
        assert f.additions == 2
        assert f.deletions == 1
        assert f.old_path is None

    def test_modified_file_added_lines(self) -> None:
        """Added line numbers are tracked in the new revision."""
        files = parse_diff(_DIFF_MODIFIED)
        f = files[0]
        # Hunk @@ -1,3 +1,4 @@: context at line 1, then two additions.
        assert f.added_lines == [2, 3]

    def test_modified_file_hunk(self) -> None:
        """The hunk metadata is captured correctly."""
        files = parse_diff(_DIFF_MODIFIED)
        f = files[0]
        assert len(f.hunks) == 1
        h = f.hunks[0]
        assert isinstance(h, DiffHunk)
        assert h.old_start == 1
        assert h.old_count == 3
        assert h.new_start == 1
        assert h.new_count == 4

    def test_added_file(self) -> None:
        """A new file is classified as ``added`` with all lines added."""
        files = parse_diff(_DIFF_ADDED)
        assert len(files) == 1
        f = files[0]
        assert f.path == "newfile.py"
        assert f.status == "added"
        assert f.additions == 3
        assert f.deletions == 0
        assert f.added_lines == [1, 2, 3]

    def test_deleted_file(self) -> None:
        """A deleted file is classified as ``deleted`` with all lines removed."""
        files = parse_diff(_DIFF_DELETED)
        assert len(files) == 1
        f = files[0]
        assert f.path == "oldfile.py"
        assert f.status == "deleted"
        assert f.additions == 0
        assert f.deletions == 2
        assert f.added_lines == []

    def test_renamed_file(self) -> None:
        """A renamed file keeps the old path and ``renamed`` status."""
        files = parse_diff(_DIFF_RENAMED)
        assert len(files) == 1
        f = files[0]
        assert f.path == "new_name.py"
        assert f.old_path == "old_name.py"
        assert f.status == "renamed"
        assert f.additions == 1
        assert f.deletions == 1

    def test_multiple_files(self) -> None:
        """Multiple files are parsed in diff order."""
        combined = _DIFF_MODIFIED + _DIFF_ADDED
        files = parse_diff(combined)
        assert len(files) == 2
        assert files[0].path == "example.py"
        assert files[1].path == "newfile.py"

    def test_hunk_without_count_defaults_to_one(self) -> None:
        """A hunk header without comma counts defaults each count to 1."""
        files = parse_diff(_DIFF_NO_COUNT)
        assert len(files) == 1
        f = files[0]
        assert len(f.hunks) == 1
        h = f.hunks[0]
        assert h.old_start == 10
        assert h.old_count == 1
        assert h.new_start == 10
        assert h.new_count == 1
        assert f.additions == 1
        assert f.deletions == 1

    def test_patch_text_captured(self) -> None:
        """The raw patch text for each file is stored on the FileDiff."""
        files = parse_diff(_DIFF_MODIFIED)
        f = files[0]
        assert f.patch.startswith("diff --git a/example.py b/example.py")
        assert "@@ -1,3 +1,4 @@" in f.patch

    def test_filediff_is_dataclass(self) -> None:
        """FileDiff is a dataclass with the expected default fields."""
        f = FileDiff(path="x.py")
        assert f.old_path is None
        assert f.status == "modified"
        assert f.additions == 0
        assert f.deletions == 0
        assert f.hunks == []
        assert f.added_lines == []
        assert f.patch == ""


# ── extract_added_lines tests ──────────────────────────────────────────


class TestExtractAddedLines:
    """Tests for :func:`github.diff.extract_added_lines`."""

    def test_with_full_header(self) -> None:
        """A patch with a ``diff --git`` header yields added line numbers."""
        lines = extract_added_lines(_DIFF_MODIFIED)
        assert lines == [2, 3]

    def test_bare_hunk_gets_header_prepended(self) -> None:
        """A bare hunk (no diff header) is still parsed correctly."""
        bare = (
            "@@ -1,3 +1,4 @@\n"
            " def hello():\n"
            '-    print("hi")\n'
            '+    print("hello")\n'
            '+    print("world")\n'
            "     return True\n"
        )
        lines = extract_added_lines(bare)
        assert lines == [2, 3]

    def test_empty_patch(self) -> None:
        """An empty or whitespace-only patch yields no lines."""
        assert extract_added_lines("") == []
        assert extract_added_lines("   ") == []

    def test_deletion_only_patch(self) -> None:
        """A patch with only deletions yields no added lines."""
        lines = extract_added_lines(_DIFF_DELETED)
        assert lines == []
