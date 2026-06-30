"""Report agent — generates a markdown review report for GitHub PR comments.

Receives the consensus findings and risk scores, and produces a
single markdown string suitable for posting as a GitHub PR comment.

The LLM generates the report following the structure defined in
``prompts/report.txt``.  If the LLM is unavailable, a basic fallback
report is generated deterministically.

Usage::

    from agents.report_agent import run_report_agent

    report = run_report_agent(consensus_findings, risk_scores)
    print(report)  # markdown string
"""

from __future__ import annotations

import json
import logging
from typing import Any

from llm.router import invoke_llm
from prompts import load_prompt

__all__ = ["run_report_agent"]

_log = logging.getLogger("codeguardian.agents.report")

# Emoji indicators for merge recommendations.
_REC_EMOJI: dict[str, str] = {
    "APPROVE": "✅",
    "REQUEST_CHANGES": "⚠️",
    "BLOCK_MERGE": "🚫",
}

# Emoji indicators for severity levels.
_SEV_EMOJI: dict[str, str] = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🟡",
    "LOW": "🟢",
    "INFO": "🔵",
}

# Severity display order (CRITICAL first).
_SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]


def run_report_agent(
    consensus_findings: list[dict[str, Any]],
    risk_scores: dict[str, Any],
) -> str:
    """Run the report agent.

    Generates a markdown review report from consensus findings and risk
    scores.  Uses the LLM to produce a well-structured report; falls
    back to a deterministic template if the LLM is unavailable.

    Parameters
    ----------
    consensus_findings:
        The deduplicated findings from the consensus agent.
    risk_scores:
        The risk scores dict from the risk agent (includes
        ``merge_recommendation``, ``overall_score``, ``summary``, etc.).

    Returns
    -------
    str
        A markdown review report string.
    """
    system_prompt = load_prompt("report")
    user_prompt = _build_user_prompt(consensus_findings, risk_scores)

    try:
        response = invoke_llm(system_prompt, user_prompt)
        _log.info(
            "report agent completed via %s (%s)",
            response.provider,
            response.model_name,
        )
        report = response.content.strip()
        if report:
            return report
        _log.warning("report agent returned empty content — using fallback")
    except Exception as exc:
        _log.warning("report agent LLM call failed — using fallback: %s", exc)

    return _fallback_report(consensus_findings, risk_scores)


def _build_user_prompt(
    findings: list[dict[str, Any]],
    risk: dict[str, Any],
) -> str:
    """Build the user prompt with consensus findings and risk scores.

    Parameters
    ----------
    findings:
        The consensus findings list.
    risk:
        The risk scores dict.

    Returns
    -------
    str
        The assembled user prompt.
    """
    parts: list[str] = [
        "## Consensus Findings\n",
        f"```json\n{json.dumps(findings, indent=2)}\n```\n",
        "## Risk Scores\n",
        f"```json\n{json.dumps(risk, indent=2)}\n```\n",
    ]
    return "\n".join(parts)


def _fallback_report(
    findings: list[dict[str, Any]],
    risk: dict[str, Any],
) -> str:
    """Generate a basic markdown report when the LLM is unavailable.

    Parameters
    ----------
    findings:
        The consensus findings list.
    risk:
        The risk scores dict.

    Returns
    -------
    str
        A markdown report string.
    """
    recommendation = risk.get("merge_recommendation", "REQUEST_CHANGES")
    overall = risk.get("overall_score", 0.0)
    rec_emoji = _REC_EMOJI.get(recommendation, "⚠️")
    rec_label = recommendation.replace("_", " ")

    lines: list[str] = [
        "# 🔍 CodeGuardian AI Review\n",
        f"**Merge Recommendation:** {rec_emoji} {rec_label}\n",
        f"**Overall Risk Score:** {overall} / 1.0\n",
        "---\n",
        "## Summary\n",
        risk.get("summary", "Code review completed."),
        "\n## Risk Breakdown\n",
        "| Category | Score |",
        "|----------|-------|",
        f"| 🔒 Security | {risk.get('security_score', 'N/A')} |",
        f"| ⚡ Performance | {risk.get('performance_score', 'N/A')} |",
        f"| 🔧 Maintainability | {risk.get('maintainability_score', 'N/A')} |",
        "\n## Findings\n",
    ]

    if not findings:
        lines.append("No findings. The code looks good.\n")
    else:
        for sev in _SEVERITY_ORDER:
            sev_findings = [f for f in findings if f.get("severity") == sev]
            emoji = _SEV_EMOJI.get(sev, "🔵")
            lines.append(f"### {emoji} {sev} ({len(sev_findings)})\n")
            if not sev_findings:
                lines.append(f"No {sev.lower()}-severity findings.\n")
            else:
                for i, f in enumerate(sev_findings, 1):
                    lines.append(f"#### {i}. {f.get('title', 'Untitled')}")
                    file_ref = f.get("file", "unknown")
                    if f.get("line"):
                        file_ref += f":{f['line']}"
                    lines.append(f"**File:** `{file_ref}`")
                    lines.append(f"**Agent:** {f.get('agent', 'unknown')}\n")
                    desc = f.get("description", "")
                    if desc:
                        lines.append(desc)
                    suggestion = f.get("suggestion", "")
                    if suggestion:
                        lines.append(f"\n**Suggestion:** {suggestion}\n")
                    lines.append("---\n")

    # Statistics section.
    lines.append("## Statistics\n")
    total = len(findings)
    counts = {
        sev: sum(1 for f in findings if f.get("severity") == sev)
        for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    }
    lines.append(f"- **Total findings:** {total}")
    lines.append(
        f"- **Critical:** {counts['CRITICAL']} | "
        f"**High:** {counts['HIGH']} | "
        f"**Medium:** {counts['MEDIUM']} | "
        f"**Low:** {counts['LOW']}\n"
    )
    lines.append("---\n")
    lines.append(
        "*This review was generated by CodeGuardian AI. Findings are "
        "based on AI analysis and static analysis tools.*\n"
    )

    return "\n".join(lines)
