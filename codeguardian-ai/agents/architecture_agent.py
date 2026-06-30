"""Architecture specialist agent — detects structural and design issues.

This is a thin wrapper around :func:`agents.base.run_specialist_agent`
that loads the ``architecture`` prompt and returns parsed findings.
Unlike the other specialists, the architecture agent also receives a
file-tree listing for structural context.

Usage::

    from agents.architecture_agent import run_architecture_agent

    findings = run_architecture_agent(code_diff, scanner_context, file_tree)
"""

from __future__ import annotations

from typing import Any

from agents.base import run_specialist_agent

__all__ = ["run_architecture_agent"]


def run_architecture_agent(
    code_diff: str,
    scanner_context: str = "",
    file_tree: str = "",
) -> list[dict[str, Any]]:
    """Run the architecture specialist agent.

    Detects layer-separation violations, dependency-direction issues,
    module organization problems, circular dependencies, interface
    design issues, coupling/cohesion problems, design pattern misuse,
    scalability concerns, file structure issues, and API contract
    violations.

    Parameters
    ----------
    code_diff:
        The unified diff text to review.
    scanner_context:
        Formatted scanner results from
        :func:`scanners.parser.format_as_context`.
    file_tree:
        Optional file-tree listing for structural context.

    Returns
    -------
    list[dict]
        A list of architecture findings, each with ``agent`` set to
        ``"architecture"``.
    """
    return run_specialist_agent(
        "architecture",
        code_diff,
        scanner_context,
        file_tree,
    )
