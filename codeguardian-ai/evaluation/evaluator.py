"""Evaluator — runs metrics on a completed review and persists results.

The evaluator is the orchestration layer between the raw metrics
(:mod:`evaluation.metrics`) and the database (:mod:`database.crud`).
It accepts either individual arguments or a completed LangGraph state
dict, runs all 7 metrics, and returns the evaluation output dict
matching §10.2 of ``plan.md``.

Typical usage after a graph run::

    from evaluation.evaluator import evaluate_from_state

    final_state = review_graph.invoke(initial_state)
    eval_result = evaluate_from_state(final_state)

    # Optionally persist to the database
    from evaluation.evaluator import evaluate_and_store
    evaluation_row = evaluate_and_store(
        db, review_id=42, state=final_state,
    )
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy.orm import Session

from database.crud import create_evaluation
from database.models import Evaluation
from scanners.parser import ScannerFinding, ScannerResult
from evaluation.metrics import compute_all_metrics

__all__ = [
    "scanner_findings_to_dicts",
    "evaluate_review",
    "evaluate_from_state",
    "evaluate_and_store",
]


# ════════════════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════════════════


def scanner_findings_to_dicts(
    scanner_input: ScannerResult | Sequence[ScannerFinding] | Sequence[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Normalise various scanner-output shapes into a list of plain dicts.

    The metrics functions expect dicts with keys like ``title``,
    ``message``, ``file``, ``line``, and ``severity``.  This helper
    converts :class:`ScannerFinding` dataclass instances and
    :class:`ScannerResult` objects into that uniform shape.

    Parameters
    ----------
    scanner_input:
        One of:

        * A :class:`ScannerResult` (its ``.findings`` list is extracted).
        * A sequence of :class:`ScannerFinding` dataclass instances.
        * A sequence of dicts (passed through unchanged).
        * ``None`` (returns an empty list).
    """
    if scanner_input is None:
        return []

    # ScannerResult has a .findings attribute
    if isinstance(scanner_input, ScannerResult):
        findings = scanner_input.findings
    else:
        findings = scanner_input

    result: list[dict[str, Any]] = []
    for item in findings:
        if isinstance(item, ScannerFinding):
            # ScannerFinding has no "title" — use message for both
            result.append({
                "title": item.message,
                "message": item.message,
                "description": item.message,
                "file": item.file,
                "line": item.line,
                "severity": item.severity,
                "scanner": item.scanner,
                "rule_id": item.rule_id,
            })
        elif isinstance(item, dict):
            result.append(item)
        # Silently skip unknown types
    return result


def _collect_agent_findings(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge all five specialist finding lists from a graph state."""
    combined: list[dict[str, Any]] = []
    for key in (
        "security_findings",
        "bug_findings",
        "performance_findings",
        "quality_findings",
        "architecture_findings",
    ):
        combined.extend(state.get(key, []))
    return combined


# ════════════════════════════════════════════════════════════════════
#  Public API
# ════════════════════════════════════════════════════════════════════


def evaluate_review(
    *,
    agent_findings: Sequence[dict[str, Any]],
    consensus_findings: Sequence[dict[str, Any]],
    scanner_findings: ScannerResult | Sequence[ScannerFinding] | Sequence[dict[str, Any]] | None,
    code_diff: str,
    report: str,
    expected_categories: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run all 7 metrics on a completed review.

    This is a thin wrapper around
    :func:`evaluation.metrics.compute_all_metrics` that also
    normalises scanner findings into plain dicts.

    Returns the evaluation output dict matching §10.2 of ``plan.md``.
    """
    scanner_dicts = scanner_findings_to_dicts(scanner_findings)
    return compute_all_metrics(
        agent_findings=agent_findings,
        consensus_findings=consensus_findings,
        scanner_findings=scanner_dicts,
        code_diff=code_diff,
        report=report,
        expected_categories=expected_categories,
    )


def evaluate_from_state(
    state: dict[str, Any],
    *,
    expected_categories: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Evaluate a completed LangGraph state dict.

    Extracts agent findings, consensus findings, scanner results,
    the code diff, and the final report from the state, then runs
    all metrics.

    Parameters
    ----------
    state:
        A completed :class:`~graph.state.CodeGuardianState` dict
        (the output of ``review_graph.invoke(...)``).
    expected_categories:
        Optional override for completeness checking.
    """
    agent_findings = _collect_agent_findings(state)
    consensus_findings = state.get("consensus_findings", [])
    scanner_result = state.get("scanner_result")
    code_diff = state.get("code_diff", "")
    report = state.get("final_report", "")

    return evaluate_review(
        agent_findings=agent_findings,
        consensus_findings=consensus_findings,
        scanner_findings=scanner_result,
        code_diff=code_diff,
        report=report,
        expected_categories=expected_categories,
    )


def evaluate_and_store(
    db: Session,
    *,
    review_id: int,
    agent_findings: Sequence[dict[str, Any]] | None = None,
    consensus_findings: Sequence[dict[str, Any]] | None = None,
    scanner_findings: ScannerResult | Sequence[ScannerFinding] | Sequence[dict[str, Any]] | None = None,
    code_diff: str = "",
    report: str = "",
    state: dict[str, Any] | None = None,
    expected_categories: Sequence[str] | None = None,
) -> Evaluation:
    """Evaluate a review and persist the result to the database.

    Either pass individual arguments or a ``state`` dict (from a
    completed graph run).  If ``state`` is provided, its values take
    precedence for any argument not explicitly supplied.

    The evaluation is stored via
    :func:`database.crud.create_evaluation` with the four persisted
    fields: ``confidence``, ``hallucination``, ``duplicate_rate``,
    and ``quality_score``.

    Returns the created :class:`~database.models.Evaluation` row.
    """
    # If a state dict is provided, extract missing values from it
    if state is not None:
        if agent_findings is None:
            agent_findings = _collect_agent_findings(state)
        if consensus_findings is None:
            consensus_findings = state.get("consensus_findings", [])
        if scanner_findings is None:
            scanner_findings = state.get("scanner_result")
        if not code_diff:
            code_diff = state.get("code_diff", "")
        if not report:
            report = state.get("final_report", "")

    # Guard against None after state extraction
    if agent_findings is None:
        agent_findings = []
    if consensus_findings is None:
        consensus_findings = []

    eval_result = evaluate_review(
        agent_findings=agent_findings,
        consensus_findings=consensus_findings,
        scanner_findings=scanner_findings,
        code_diff=code_diff,
        report=report,
        expected_categories=expected_categories,
    )

    return create_evaluation(
        db,
        review_id=review_id,
        confidence=eval_result["confidence"],
        hallucination=eval_result["hallucination"],
        duplicate_rate=eval_result["duplicate_rate"],
        quality_score=eval_result["overall_quality"],
    )
