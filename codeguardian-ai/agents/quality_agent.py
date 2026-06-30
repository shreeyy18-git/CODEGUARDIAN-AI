"""Quality specialist agent — detects maintainability and code-quality issues.

This is a thin wrapper around :func:`agents.base.run_specialist_agent`
that loads the ``quality`` prompt and returns parsed findings.

Usage::

    from agents.quality_agent import run_quality_agent

    findings = run_quality_agent(code_diff, scanner_context)
"""

from __future__ import annotations

from typing import Any

from agents.base import run_specialist_agent

__all__ = ["run_quality_agent"]


def run_quality_agent(
    code_diff: str,
    scanner_context: str = "",
) -> list[dict[str, Any]]:
    """Run the quality specialist agent.

    Detects SOLID violations, DRY/duplication issues, naming problems,
    function design issues, error-handling gaps, code organization,
    maintainability concerns, testing gaps, consistency issues, and
    documentation problems.

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
        A list of quality findings, each with ``agent`` set to
        ``"quality"``.
    """
    return run_specialist_agent("quality", code_diff, scanner_context)
