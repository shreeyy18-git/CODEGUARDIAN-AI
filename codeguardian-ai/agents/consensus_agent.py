"""Consensus agent — merges, deduplicates, and prioritizes all specialist findings.

Receives the findings from all five specialist agents (Security, Bug,
Performance, Quality, Architecture) and produces a single unified,
deduplicated, prioritized list via LLM reasoning.

Usage::

    from agents.consensus_agent import run_consensus_agent

    consensus = run_consensus_agent(
        security_findings, bug_findings,
        performance_findings, quality_findings,
        architecture_findings,
    )
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.base import parse_findings_json
from llm.router import invoke_llm
from prompts import load_prompt

__all__ = ["run_consensus_agent"]

_log = logging.getLogger("codeguardian.agents.consensus")


def run_consensus_agent(
    security_findings: list[dict[str, Any]],
    bug_findings: list[dict[str, Any]],
    performance_findings: list[dict[str, Any]],
    quality_findings: list[dict[str, Any]],
    architecture_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Run the consensus agent.

    Merges, deduplicates, and prioritizes findings from all specialist
    agents into a single coherent, conflict-free list sorted by severity
    (CRITICAL first).

    Parameters
    ----------
    security_findings:
        Findings from the security agent.
    bug_findings:
        Findings from the bug agent.
    performance_findings:
        Findings from the performance agent.
    quality_findings:
        Findings from the quality agent.
    architecture_findings:
        Findings from the architecture agent.

    Returns
    -------
    list[dict]
        A deduplicated, prioritized list of consensus findings. Each
        finding includes an ``agent`` field indicating the primary
        source agent.
    """
    all_findings: dict[str, list[dict[str, Any]]] = {
        "security": security_findings,
        "bug": bug_findings,
        "performance": performance_findings,
        "quality": quality_findings,
        "architecture": architecture_findings,
    }

    total = sum(len(v) for v in all_findings.values())
    if total == 0:
        _log.info("consensus agent: no findings to merge — returning empty list")
        return []

    system_prompt = load_prompt("consensus")
    user_prompt = _build_user_prompt(all_findings)

    response = invoke_llm(system_prompt, user_prompt)
    _log.info(
        "consensus agent completed via %s (%s)",
        response.provider,
        response.model_name,
    )

    findings = parse_findings_json(response.content, "consensus")
    _log.info("consensus agent: %d findings after deduplication", len(findings))
    return findings


def _build_user_prompt(
    all_findings: dict[str, list[dict[str, Any]]],
) -> str:
    """Build the user prompt with all specialist findings as JSON.

    Parameters
    ----------
    all_findings:
        A dict mapping agent names to their finding lists.

    Returns
    -------
    str
        The assembled user prompt.
    """
    parts: list[str] = ["## Specialist Agent Findings\n"]
    for agent_name, findings in all_findings.items():
        count = len(findings)
        parts.append(f"### {agent_name.title()} Agent ({count} findings)\n")
        if findings:
            parts.append(f"```json\n{json.dumps(findings, indent=2)}\n```\n")
        else:
            parts.append("No findings.\n")
    return "\n".join(parts)
