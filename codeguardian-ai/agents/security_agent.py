"""Security specialist agent — detects vulnerabilities and security issues.

This is a thin wrapper around :func:`agents.base.run_specialist_agent`
that loads the ``security`` prompt and returns parsed findings.

Usage::

    from agents.security_agent import run_security_agent

    findings = run_security_agent(code_diff, scanner_context)
"""

from __future__ import annotations

from typing import Any

from agents.base import run_specialist_agent

__all__ = ["run_security_agent"]


def run_security_agent(
    code_diff: str,
    scanner_context: str = "",
) -> list[dict[str, Any]]:
    """Run the security specialist agent.

    Detects injection flaws, XSS, authentication issues, hardcoded
    secrets, cryptographic weaknesses, path traversal, deserialization,
    SSRF, dependency vulnerabilities, and JWT problems.

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
        A list of security findings, each with ``agent`` set to
        ``"security"``.
    """
    return run_specialist_agent("security", code_diff, scanner_context)
