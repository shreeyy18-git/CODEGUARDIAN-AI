"""Routing logic — determines which specialist agents to activate.

The router inspects the code diff and changed-file list to decide whether
the performance and architecture agents should run.  Security, bug, and
quality agents always run.
"""

from __future__ import annotations

import re

from graph.state import CodeGuardianState

__all__ = [
    "should_run_performance",
    "should_run_architecture",
    "route_agents",
]

# ── Performance triggers ────────────────────────────────────────────
# Patterns that suggest performance-sensitive code: loops, DB queries,
# bulk data operations, and heavy numerical libraries.
_PERF_PATTERNS: list[str] = [
    r"\bfor\s+.*\bin\b",          # for … in loops
    r"\bwhile\s+",                 # while loops
    r"\.query\b",                  # SQLAlchemy / ORM queries
    r"\.filter\b",                 # ORM filter calls
    r"\.all\(\)",                  # ORM fetch-all
    r"\bSELECT\b",                 # raw SQL
    r"\bINSERT\b",
    r"\bUPDATE\b",
    r"\bDELETE\b",
    r"\bDataFrame\b",              # pandas
    r"\bnp\.",                     # numpy
    r"\btorch\.",                  # pytorch
    r"\.iterrows\b",               # pandas row iteration
    r"\basync\s+for\b",            # async iteration
    r"\bgather\b",                 # asyncio.gather
    r"\.execute\b",                # DB execute
    r"\bcursor\b",                 # DB cursor
]

# ── Architecture triggers ───────────────────────────────────────────
# Patterns that suggest structural changes: new files, import changes,
# new class/function definitions.
_ARCH_PATTERNS: list[str] = [
    r"^---\s*/dev/null",           # new file (old path is /dev/null)
    r"^\+\+\+\s*b/",               # new file header
    r"^\+\s*(?:import|from)\s+",   # added imports
    r"^\-\s*(?:import|from)\s+",   # removed imports
    r"^\+\s*(?:class|def)\s+",     # added classes/functions
]

_PERF_REGEX = re.compile("|".join(_PERF_PATTERNS), re.MULTILINE | re.IGNORECASE)
_ARCH_REGEX = re.compile("|".join(_ARCH_PATTERNS), re.MULTILINE)

#: Maximum number of changed files before architecture review is forced.
_MANY_FILES_THRESHOLD = 5


def should_run_performance(code_diff: str) -> bool:
    """Return ``True`` if the diff contains performance-sensitive patterns.

    Checks for loops, database queries, bulk data operations, and heavy
    numerical library usage.
    """
    return bool(_PERF_REGEX.search(code_diff))


def should_run_architecture(code_diff: str, changed_files: list[str]) -> bool:
    """Return ``True`` if the diff contains architecture-relevant changes.

    Triggers on new files, import changes, new class/function definitions,
    or when more than :data:`_MANY_FILES_THRESHOLD` files are changed.
    """
    # New file added?
    if "--- /dev/null" in code_diff:
        return True
    # Imports or structural elements changed?
    if _ARCH_REGEX.search(code_diff):
        return True
    # Many files changed → likely a structural refactor.
    if len(changed_files) > _MANY_FILES_THRESHOLD:
        return True
    return False


def route_agents(state: CodeGuardianState) -> list[str]:
    """Determine which specialist agents to activate.

    Always activates: ``security``, ``bug``, ``quality``.
    Conditionally activates: ``performance`` (loops/DB/large data),
    ``architecture`` (new files/imports/many files).

    Returns a list of node names that LangGraph will execute in parallel.
    """
    agents: list[str] = ["security", "bug", "quality"]

    diff = state.get("code_diff", "")
    changed_files = state.get("changed_files", [])

    if should_run_performance(diff):
        agents.append("performance")

    if should_run_architecture(diff, changed_files):
        agents.append("architecture")

    return agents
