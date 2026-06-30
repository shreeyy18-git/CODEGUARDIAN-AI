"""Risk score agent — computes risk scores and merge recommendation.

Computes security, performance, maintainability, and overall risk
scores from the consensus findings.  Scores are computed
**deterministically** (the source of truth) using the severity-based
scoring methodology defined in ``prompts/risk.txt``.  The LLM is used
only for the narrative summary; if the LLM is unavailable, a basic
summary is generated automatically.

Scoring formula::

    overall = 0.5 * security + 0.3 * maintainability + 0.2 * performance

Severity → score mapping::

    CRITICAL → 0.0
    HIGH     → 0.25
    MEDIUM   → 0.5
    LOW      → 0.75
    (none)   → 1.0

Usage::

    from agents.risk_agent import run_risk_agent

    risk = run_risk_agent(consensus_findings)
    print(risk["merge_recommendation"])  # "APPROVE"
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.base import parse_json_object
from config import risk_verdict
from llm.router import invoke_llm
from prompts import load_prompt

__all__ = ["run_risk_agent"]

_log = logging.getLogger("codeguardian.agents.risk")

# Severity → score mapping (1.0 = no risk, 0.0 = maximum risk).
# INFO findings do not affect the score (treated as "no issue").
_SEVERITY_SCORES: dict[str, float] = {
    "CRITICAL": 0.0,
    "HIGH": 0.25,
    "MEDIUM": 0.5,
    "LOW": 0.75,
}


def _worst_score(
    findings: list[dict[str, Any]],
    agent_names: set[str],
) -> float:
    """Return the score for the worst-severity finding from the given agents.

    Parameters
    ----------
    findings:
        The consensus findings list.
    agent_names:
        The set of agent names to filter by (e.g. ``{"security"}``).

    Returns
    -------
    float
        A score in ``[0.0, 1.0]`` where 1.0 means no risk.  Returns
        ``1.0`` if there are no relevant findings or only INFO-level
        findings.
    """
    relevant = [f for f in findings if f.get("agent") in agent_names]
    if not relevant:
        return 1.0

    scores = [
        _SEVERITY_SCORES.get(f.get("severity", ""), 1.0)
        for f in relevant
    ]
    # Only consider actual issues (score < 1.0); ignore INFO/unknown.
    real_scores = [s for s in scores if s < 1.0]
    if not real_scores:
        return 1.0
    return min(real_scores)


def _compute_scores(
    findings: list[dict[str, Any]],
) -> dict[str, float]:
    """Compute all risk scores deterministically from findings.

    Parameters
    ----------
    findings:
        The consensus findings list.

    Returns
    -------
    dict
        A dict with keys ``security_score``, ``performance_score``,
        ``maintainability_score``, and ``overall_score``, each rounded
        to 3 decimal places.
    """
    security_score = _worst_score(findings, {"security"})
    performance_score = _worst_score(findings, {"performance"})
    maintainability_score = _worst_score(findings, {"quality", "architecture"})
    overall_score = (
        0.5 * security_score
        + 0.3 * maintainability_score
        + 0.2 * performance_score
    )
    return {
        "security_score": round(security_score, 3),
        "performance_score": round(performance_score, 3),
        "maintainability_score": round(maintainability_score, 3),
        "overall_score": round(overall_score, 3),
    }


def _default_summary(
    scores: dict[str, float],
    recommendation: str,
    findings: list[dict[str, Any]],
) -> str:
    """Generate a basic summary when the LLM is unavailable.

    Parameters
    ----------
    scores:
        The computed risk scores.
    recommendation:
        The merge recommendation (``APPROVE``, ``REQUEST_CHANGES``,
        ``BLOCK_MERGE``).
    findings:
        The consensus findings list.

    Returns
    -------
    str
        A concise (1-3 sentence) summary.
    """
    total = len(findings)
    critical = sum(1 for f in findings if f.get("severity") == "CRITICAL")
    high = sum(1 for f in findings if f.get("severity") == "HIGH")

    if recommendation == "APPROVE":
        return (
            f"Code review found {total} finding(s) with no critical or "
            f"high-severity issues. The PR is safe to merge."
        )
    if recommendation == "BLOCK_MERGE":
        return (
            f"Code review found {critical} critical and {high} "
            f"high-severity issue(s). The PR should not be merged until "
            f"these are resolved."
        )
    return (
        f"Code review found {total} finding(s) including {critical} "
        f"critical and {high} high-severity issue(s). Changes are "
        f"recommended before merge."
    )


def run_risk_agent(
    consensus_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run the risk score agent.

    Computes security, performance, maintainability, and overall risk
    scores from the consensus findings, and produces a merge
    recommendation.

    Scores are computed **deterministically** (always correct).  The
    LLM is used only for the narrative summary; if the LLM is
    unavailable, a basic summary is generated.

    Parameters
    ----------
    consensus_findings:
        The deduplicated findings from the consensus agent.

    Returns
    -------
    dict
        A dict with keys: ``security_score``, ``performance_score``,
        ``maintainability_score``, ``overall_score``,
        ``merge_recommendation``, and ``summary``.
    """
    # 1. Compute scores deterministically (source of truth).
    scores = _compute_scores(consensus_findings)
    recommendation = risk_verdict(scores["overall_score"])

    # 2. Try LLM for a richer narrative summary.
    summary = ""
    try:
        system_prompt = load_prompt("risk")
        user_prompt = _build_user_prompt(
            consensus_findings, scores, recommendation,
        )
        response = invoke_llm(system_prompt, user_prompt)
        llm_result = parse_json_object(response.content, "risk")
        if llm_result.get("summary"):
            summary = str(llm_result["summary"]).strip()
        _log.info(
            "risk agent summary generated via %s (%s)",
            response.provider,
            response.model_name,
        )
    except Exception as exc:
        _log.warning(
            "risk agent LLM call failed — using default summary: %s",
            exc,
        )

    if not summary:
        summary = _default_summary(scores, recommendation, consensus_findings)

    result: dict[str, Any] = {
        **scores,
        "merge_recommendation": recommendation,
        "summary": summary,
    }
    _log.info(
        "risk agent: overall_score=%.3f, recommendation=%s",
        scores["overall_score"],
        recommendation,
    )
    return result


def _build_user_prompt(
    findings: list[dict[str, Any]],
    scores: dict[str, float],
    recommendation: str,
) -> str:
    """Build the user prompt with consensus findings and pre-computed scores.

    The LLM is asked to produce the full JSON object per the risk
    prompt, but only the ``summary`` field is used — all numeric scores
    are overridden with the deterministic values.

    Parameters
    ----------
    findings:
        The consensus findings list.
    scores:
        The pre-computed risk scores.
    recommendation:
        The pre-computed merge recommendation.

    Returns
    -------
    str
        The assembled user prompt.
    """
    parts: list[str] = [
        "## Consensus Findings\n",
        f"```json\n{json.dumps(findings, indent=2)}\n```\n",
        "## Pre-computed Scores (for reference)\n",
        f"- security_score: {scores['security_score']}\n",
        f"- performance_score: {scores['performance_score']}\n",
        f"- maintainability_score: {scores['maintainability_score']}\n",
        f"- overall_score: {scores['overall_score']}\n",
        f"- merge_recommendation: {recommendation}\n",
    ]
    return "\n".join(parts)
