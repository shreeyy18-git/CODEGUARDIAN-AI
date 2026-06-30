"""Unified-diff parser for GitHub pull-request diffs.

GitHub delivers PR diffs in the standard unified-diff format
(``diff --git a/... b/...``).  This module parses that text into
structured :class:`FileDiff` objects so the rest of the pipeline can
know *which* files changed, *how many* lines were added/removed, and
*which line numbers* in the new revision were touched — information
that the static-analysis scanners and the AI agents both need.

The parser is intentionally dependency-free (pure standard library) so
it can be unit-tested without a network connection.

Usage::

    from github.diff import parse_diff

    files = parse_diff(diff_text)
    for f in files:
        print(f.path, f.additions, f.added_lines)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "DiffHunk",
    "FileDiff",
    "parse_diff",
    "extract_added_lines",
]

_log = __import__("logging").getLogger(__name__)

# ── Regexes for the unified-diff header lines ───────────────────────────
# ``diff --git a/path b/path``
_RE_DIFF_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+)$")
# ``--- a/path``  (may be ``--- /dev/null`` for new files)
_RE_OLD_FILE = re.compile(r"^--- (?:a/)?(.+)$")
# ``+++ b/path``  (may be ``+++ /dev/null`` for deletions)
_RE_PLUS_FILE = re.compile(r"^\+\+\+ (?:b/)?(.+)$")
# ``@@ -old_start,old_len +new_start,new_len @@ optional section``
_RE_HUNK = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*$"
)
# ``new file mode 100644`` / ``deleted file mode 100644``
_RE_NEW_FILE = re.compile(r"^new file mode \d+$")
_RE_DELETED_FILE = re.compile(r"^deleted file mode \d+$")
# ``rename from old`` / ``rename to new``
_RE_RENAME_FROM = re.compile(r"^rename from (.+)$")
_RE_RENAME_TO = re.compile(r"^rename to (.+)$")


@dataclass
class DiffHunk:
    """A single ``@@ ... @@`` hunk inside a file diff.

    Attributes
    ----------
    old_start:
        Starting line number in the *old* revision (1-based).
    old_count:
        Number of lines in the old revision covered by this hunk.
    new_start:
        Starting line number in the *new* revision (1-based).
    new_count:
        Number of lines in the new revision covered by this hunk.
    """

    old_start: int
    old_count: int
    new_start: int
    new_count: int


@dataclass
class FileDiff:
    """A single file's worth of changes inside a PR diff.

    Attributes
    ----------
    path:
        Path of the file in the *new* revision (the post-PR state).
        For deletions this is the old path.
    old_path:
        Original path if the file was renamed, otherwise ``None``.
    status:
        One of ``"added"``, ``"modified"``, ``"deleted"``, ``"renamed"``.
    additions:
        Total number of added lines (``+`` lines, excluding headers).
    deletions:
        Total number of removed lines (``-`` lines, excluding headers).
    hunks:
        List of :class:`DiffHunk` objects describing the changed regions.
    added_lines:
        1-based line numbers (in the new revision) that were added.
        Useful for telling scanners/agents exactly where to look.
    patch:
        The raw unified-diff text for this single file.
    """

    path: str
    old_path: str | None = None
    status: str = "modified"
    additions: int = 0
    deletions: int = 0
    hunks: list[DiffHunk] = field(default_factory=list)
    added_lines: list[int] = field(default_factory=list)
    patch: str = ""


def _classify_status(
    *,
    is_new: bool,
    is_deleted: bool,
    is_renamed: bool,
) -> str:
    """Return a human-readable status string from header flags."""
    if is_deleted:
        return "deleted"
    if is_new:
        return "added"
    if is_renamed:
        return "renamed"
    return "modified"


def parse_diff(diff_text: str) -> list[FileDiff]:
    """Parse a unified-diff string into a list of :class:`FileDiff`.

    Parameters
    ----------
    diff_text:
        The full unified diff as returned by
        ``GET /repos/{owner}/{repo}/pulls/{number}`` with
        ``Accept: application/vnd.github.v3.diff``.

    Returns
    -------
    list[FileDiff]
        One entry per changed file, in diff order.  Files whose diff
        could not be parsed are skipped (a warning is logged).
    """
    if not diff_text or not diff_text.strip():
        return []

    lines = diff_text.splitlines()
    files: list[FileDiff] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # ── Start of a new file block ────────────────────────────────
        m = _RE_DIFF_HEADER.match(line)
        if not m:
            i += 1
            continue

        old_name, new_name = m.group(1), m.group(2)
        i += 1

        # Collect metadata lines until we hit ``---`` / ``+++`` or the
        # next ``diff --git`` header.
        is_new = False
        is_deleted = False
        is_renamed = False
        rename_from: str | None = None
        rename_to: str | None = None
        old_path: str | None = None
        new_path: str | None = None

        while i < n:
            meta = lines[i]
            if meta.startswith("diff --git"):
                break  # next file block
            if _RE_NEW_FILE.match(meta):
                is_new = True
            elif _RE_DELETED_FILE.match(meta):
                is_deleted = True
            elif (rm := _RE_RENAME_FROM.match(meta)):
                rename_from = rm.group(1)
                is_renamed = True
            elif (rt := _RE_RENAME_TO.match(meta)):
                rename_to = rt.group(1)
            elif (om := _RE_OLD_FILE.match(meta)):
                old_path = None if om.group(1) == "/dev/null" else om.group(1)
            elif (nm := _RE_PLUS_FILE.match(meta)):
                new_path = None if nm.group(1) == "/dev/null" else nm.group(1)
                i += 1
                break  # ``+++`` is the last header before hunks
            i += 1

        # Determine the canonical path.
        if new_path:
            path = new_path
        elif is_renamed and rename_to:
            path = rename_to
        else:
            path = new_name

        # ``old_path`` is only meaningful for renames; for modified /
        # added / deleted files it is ``None`` per the FileDiff contract.
        if is_renamed and rename_from:
            old_path_final: str | None = rename_from
        else:
            old_path_final = None

        status = _classify_status(
            is_new=is_new,
            is_deleted=is_deleted,
            is_renamed=is_renamed,
        )

        file_diff = FileDiff(
            path=path,
            old_path=old_path_final,
            status=status,
        )

        # ── Parse hunks until the next file header ──────────────────
        patch_lines: list[str] = [line]
        while i < n:
            hline = lines[i]
            if hline.startswith("diff --git"):
                break

            hm = _RE_HUNK.match(hline)
            if hm:
                old_start = int(hm.group(1))
                old_count = int(hm.group(2)) if hm.group(2) else 1
                new_start = int(hm.group(3))
                new_count = int(hm.group(4)) if hm.group(4) else 1
                file_diff.hunks.append(
                    DiffHunk(
                        old_start=old_start,
                        old_count=old_count,
                        new_start=new_start,
                        new_count=new_count,
                    )
                )
                patch_lines.append(hline)
                i += 1
                # Walk the hunk body, tracking the new-revision line number.
                cur_new = new_start
                while i < n and not lines[i].startswith("@@ ") and not lines[i].startswith("diff --git"):
                    body = lines[i]
                    patch_lines.append(body)
                    if body.startswith("+"):
                        file_diff.added_lines.append(cur_new)
                        file_diff.additions += 1
                        cur_new += 1
                    elif body.startswith("-"):
                        file_diff.deletions += 1
                    elif body.startswith(" ") or body == "":
                        # context line (or empty context line)
                        if body.startswith(" "):
                            cur_new += 1
                        else:
                            # A completely blank line in a diff is treated
                            # as a context line by ``git`` — it advances
                            # the new line counter too.
                            cur_new += 1
                    # Lines starting with ``\`` are ``\ No newline...`` and
                    # don't advance either counter.
                    i += 1
            else:
                # Non-hunk, non-header line (e.g. ``index abc..def``) —
                # include in the patch for completeness.
                patch_lines.append(hline)
                i += 1

        file_diff.patch = "\n".join(patch_lines)
        files.append(file_diff)

    return files


def extract_added_lines(patch: str) -> list[int]:
    """Return the 1-based new-revision line numbers that were added.

    Convenience wrapper around :func:`parse_diff` for callers that have
    a single file's patch text and only care about the added lines.

    Parameters
    ----------
    patch:
        A single file's unified-diff patch (may include the
        ``diff --git`` header or just the ``@@`` hunks).

    Returns
    -------
    list[int]
        Sorted list of added line numbers.
    """
    if not patch.strip():
        return []
    # Ensure the patch has a ``diff --git`` header so parse_diff can
    # process it.  A bare hunk (starting with ``@@``) also needs one.
    if not patch.startswith("diff --git"):
        patch = "diff --git a/file b/file\n--- a/file\n+++ b/file\n" + patch
    files = parse_diff(patch)
    if not files:
        return []
    return sorted(files[0].added_lines)
