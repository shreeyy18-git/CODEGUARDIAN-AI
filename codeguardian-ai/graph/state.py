"""LangGraph state definition for CodeGuardian AI.

Defines the :class:`CodeGuardianState` TypedDict that flows through every
node in the review graph.  Specialist-finding fields use
``Annotated[list[dict], operator.add]`` reducers so that parallel fan-out
nodes can each append to their own key without overwriting siblings.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from scanners.parser import ScannerResult

__all__ = ["CodeGuardianState"]


class CodeGuardianState(TypedDict, total=False):
    """State object passed through the LangGraph workflow.

    Not all fields are present in the initial input — ``total=False``
    allows partial dicts.  Fields annotated with ``operator.add`` are
    accumulated across parallel specialist nodes.

    Required input fields
    ---------------------
    pr_number:
        GitHub PR number.
    commit_sha:
        SHA of the head commit under review.
    repository:
        ``"owner/repo"`` string.
    branch:
        Head branch name.
    code_diff:
        Unified diff text of all changed files.

    Optional input fields
    ---------------------
    changed_files:
        List of file paths relative to the repo root.
    file_contents:
        Mapping of file path → full file content.
    file_tree:
        Pre-formatted file-tree string for architecture context.
    repo_path:
        Path to the local checkout for static analysis.
    """

    # ── Input ──────────────────────────────────────────────────────
    pr_number: int
    commit_sha: str
    repository: str
    branch: str
    code_diff: str
    changed_files: list[str]
    file_contents: dict[str, str]
    file_tree: str
    repo_path: str

    # ── Static analysis ───────────────────────────────────────────
    scanner_result: ScannerResult
    scanner_context: str

    # ── Agent findings (accumulated via operator.add) ─────────────
    security_findings: Annotated[list[dict[str, Any]], operator.add]
    bug_findings: Annotated[list[dict[str, Any]], operator.add]
    performance_findings: Annotated[list[dict[str, Any]], operator.add]
    quality_findings: Annotated[list[dict[str, Any]], operator.add]
    architecture_findings: Annotated[list[dict[str, Any]], operator.add]

    # ── Consensus + risk ─────────────────────────────────────────
    consensus_findings: list[dict[str, Any]]
    risk_scores: dict[str, Any]
    merge_recommendation: str

    # ── Output ───────────────────────────────────────────────────
    final_report: str
    evaluation: dict[str, Any]
    review_id: int
    error: str
