"""Performance specialist agent — detects efficiency and scalability issues.

This is a thin wrapper around :func:`agents.base.run_specialist_agent`
that loads the ``performance`` prompt and returns parsed findings.

Usage::

    from agents.performance_agent import run_performance_agent

    findings = run_performance_agent(code_diff, scanner_context)
"""

from __future__ import annotations

from typing import Any

from agents.base import run_specialist_agent

__all__ = ["run_performance_agent"]


def run_performance_agent(
    code_diff: str,
    scanner_context: str = "",
) -> list[dict[str, Any]]:
    """Run the performance specialist agent.

    Detects algorithmic complexity issues, inefficient database queries,
    memory inefficiencies, I/O bottlenecks, string concatenation in
    loops, suboptimal collection usage, concurrency inefficiencies,
    resource lifecycle problems, missing caching, and hot-path
    inefficiencies.

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
        A list of performance findings, each with ``agent`` set to
        ``"performance"``.
    """
    return run_specialist_agent("performance", code_diff, scanner_context)
