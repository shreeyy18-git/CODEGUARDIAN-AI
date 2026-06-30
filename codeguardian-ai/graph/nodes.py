"""LangGraph node functions — bind agents to the graph state.

Each node is a plain function ``(state) -> partial_state_update``.
Specialist nodes return a single key (e.g. ``{"security_findings": [...]}``)
which the ``operator.add`` reducer appends to the accumulated state.
"""

from __future__ import annotations

import logging
from typing import Any

from graph.state import CodeGuardianState
from scanners.parser import ScannerResult
from scanners.pipeline import get_context_block, run_static_analysis

__all__ = [
    "load_pr_node",
    "static_analysis_node",
    "router_node",
    "security_node",
    "bug_node",
    "performance_node",
    "quality_node",
    "architecture_node",
    "consensus_node",
    "risk_node",
    "report_node",
]

_log = logging.getLogger("codeguardian.graph.nodes")


# ── Pipeline nodes ──────────────────────────────────────────────────


def load_pr_node(state: CodeGuardianState) -> dict[str, Any]:
    """Load PR metadata — derives ``file_tree`` from changed files.

    In production this node may fetch the diff from the GitHub API.
    For now the diff is provided directly in the input state.
    """
    changed_files = state.get("changed_files", [])
    file_tree = "\n".join(sorted(changed_files)) if changed_files else ""
    _log.info(
        "load_pr: PR #%s, repo=%s, %d changed file(s)",
        state.get("pr_number"),
        state.get("repository"),
        len(changed_files),
    )
    return {"file_tree": file_tree}


def static_analysis_node(state: CodeGuardianState) -> dict[str, Any]:
    """Run static-analysis scanners and store the merged result.

    If ``repo_path`` or ``changed_files`` are missing, returns an empty
    :class:`ScannerResult` so downstream agents still receive a valid
    context block.
    """
    repo_path = state.get("repo_path", "")
    changed_files = state.get("changed_files", [])

    if not repo_path or not changed_files:
        _log.info("static_analysis: no repo_path or changed_files — skipping")
        result = ScannerResult()
    else:
        result = run_static_analysis(repo_path, changed_files)

    context = get_context_block(result)
    _log.info("static_analysis: %d finding(s)", result.total_findings)
    return {"scanner_result": result, "scanner_context": context}


def router_node(state: CodeGuardianState) -> dict[str, Any]:
    """Router node — no-op placeholder.

    The actual routing decision is made by the conditional-edge function
    :func:`graph.router.route_agents`.  This node exists so the graph has
    a named step between ``static_analysis`` and the specialist fan-out.
    """
    _log.debug("router: evaluating which agents to activate")
    return {}


# ── Specialist agent nodes ──────────────────────────────────────────


def security_node(state: CodeGuardianState) -> dict[str, Any]:
    """Run the security specialist agent."""
    from agents.security_agent import run_security_agent

    findings = run_security_agent(
        code_diff=state.get("code_diff", ""),
        scanner_context=state.get("scanner_context", ""),
    )
    _log.info("security agent: %d finding(s)", len(findings))
    return {"security_findings": findings}


def bug_node(state: CodeGuardianState) -> dict[str, Any]:
    """Run the bug specialist agent."""
    from agents.bug_agent import run_bug_agent

    findings = run_bug_agent(
        code_diff=state.get("code_diff", ""),
        scanner_context=state.get("scanner_context", ""),
    )
    _log.info("bug agent: %d finding(s)", len(findings))
    return {"bug_findings": findings}


def performance_node(state: CodeGuardianState) -> dict[str, Any]:
    """Run the performance specialist agent."""
    from agents.performance_agent import run_performance_agent

    findings = run_performance_agent(
        code_diff=state.get("code_diff", ""),
        scanner_context=state.get("scanner_context", ""),
    )
    _log.info("performance agent: %d finding(s)", len(findings))
    return {"performance_findings": findings}


def quality_node(state: CodeGuardianState) -> dict[str, Any]:
    """Run the quality specialist agent."""
    from agents.quality_agent import run_quality_agent

    findings = run_quality_agent(
        code_diff=state.get("code_diff", ""),
        scanner_context=state.get("scanner_context", ""),
    )
    _log.info("quality agent: %d finding(s)", len(findings))
    return {"quality_findings": findings}


def architecture_node(state: CodeGuardianState) -> dict[str, Any]:
    """Run the architecture specialist agent."""
    from agents.architecture_agent import run_architecture_agent

    findings = run_architecture_agent(
        code_diff=state.get("code_diff", ""),
        scanner_context=state.get("scanner_context", ""),
        file_tree=state.get("file_tree", ""),
    )
    _log.info("architecture agent: %d finding(s)", len(findings))
    return {"architecture_findings": findings}


# ── Synthesis nodes ─────────────────────────────────────────────────


def consensus_node(state: CodeGuardianState) -> dict[str, Any]:
    """Run the consensus agent to merge and deduplicate findings."""
    from agents.consensus_agent import run_consensus_agent

    findings = run_consensus_agent(
        security_findings=state.get("security_findings", []),
        bug_findings=state.get("bug_findings", []),
        performance_findings=state.get("performance_findings", []),
        quality_findings=state.get("quality_findings", []),
        architecture_findings=state.get("architecture_findings", []),
    )
    _log.info("consensus agent: %d finding(s) after merge", len(findings))
    return {"consensus_findings": findings}


def risk_node(state: CodeGuardianState) -> dict[str, Any]:
    """Run the risk-scoring agent."""
    from agents.risk_agent import run_risk_agent

    risk_scores = run_risk_agent(state.get("consensus_findings", []))
    _log.info(
        "risk agent: overall=%.3f, recommendation=%s",
        risk_scores.get("overall_score", 0.0),
        risk_scores.get("merge_recommendation", "UNKNOWN"),
    )
    return {
        "risk_scores": risk_scores,
        "merge_recommendation": risk_scores.get("merge_recommendation", "UNKNOWN"),
    }


def report_node(state: CodeGuardianState) -> dict[str, Any]:
    """Run the report agent to generate the final markdown review."""
    from agents.report_agent import run_report_agent

    report = run_report_agent(
        consensus_findings=state.get("consensus_findings", []),
        risk_scores=state.get("risk_scores", {}),
    )
    _log.info("report agent: %d char(s)", len(report))
    return {"final_report": report}
