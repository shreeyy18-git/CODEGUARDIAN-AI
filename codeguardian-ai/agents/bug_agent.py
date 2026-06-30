"""Bug specialist agent — detects logic errors and correctness issues.

This is a thin wrapper around :func:`agents.base.run_specialist_agent`
that loads the ``bug`` prompt and returns parsed findings.

Usage::

    from agents.bug_agent import run_bug_agent

    findings = run_bug_agent(code_diff, scanner_context)
"""

from __future__ import annotations

from typing import Any

from agents.base import run_specialist_agent

__all__ = ["run_bug_agent"]


def run_bug_agent(
    code_diff: str,
    scanner_context: str = "",
) -> list[dict[str, Any]]:
    """Run the bug specialist agent.

    Detects logic errors, null/None reference issues, unhandled
    exceptions, type mismatches, resource leaks, race conditions,
    data-handling bugs, API misuse, state management errors, and
    edge-case violations.

    Parameters
    ----------
    code_diff:
        The unified diff text to review.
    scanner_context:
        Formatted scanner results from
        :func:`scanners.parser.format_as_context`.

    Returns
    -------
    list[dict]
        A list of bug findings, each with ``agent`` set to ``"bug"``.
    """
    return run_specialist_agent("bug", code_diff, scanner_context)
