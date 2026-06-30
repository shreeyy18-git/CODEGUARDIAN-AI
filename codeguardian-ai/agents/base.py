"""Shared utilities for CodeGuardian AI agents.

This module defines the common :class:`AgentFinding` contract, the
:func:`parse_findings_json` helper that safely parses LLM JSON output,
and :func:`run_specialist_agent` — the generic driver used by all five
specialist agents (security, bug, performance, quality, architecture).

Usage::

    from agents.base import run_specialist_agent

    findings = run_specialist_agent("security", code_diff, scanner_context)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TypedDict

from llm.router import invoke_llm
from prompts import load_prompt

__all__ = [
    "AgentFinding",
    "parse_findings_json",
    "parse_json_object",
    "build_user_prompt",
    "run_specialist_agent",
]

_log = logging.getLogger("codeguardian.agents")

# Canonical severity values.
_VALID_SEVERITIES = frozenset({"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"})


class AgentFinding(TypedDict):
    """Uniform finding structure returned by every specialist agent.

    Attributes
    ----------
    agent:
        The source agent name (``"security"``, ``"bug"``, etc.).
    severity:
        One of ``CRITICAL``, ``HIGH``, ``MEDIUM``, ``LOW``, ``INFO``.
    title:
        Short summary of the issue (≤ 80 chars).
    description:
        Detailed explanation of the issue.
    file:
        File path from the diff.
    line:
        1-based line number, or ``None`` if unknown.
    suggestion:
        Actionable fix recommendation.
    """

    agent: str
    severity: str
    title: str
    description: str
    file: str
    line: int | None
    suggestion: str


# ── JSON parsing ────────────────────────────────────────────────────────

_RE_FENCE_START = re.compile(r"^```(?:json)?\s*\n?", re.MULTILINE)
_RE_FENCE_END = re.compile(r"\n?```\s*$", re.MULTILINE)


def _strip_code_fences(text: str) -> str:
    """Remove markdown `` ``` `` code fences if present."""
    if "```" not in text:
        return text.strip()
    stripped = _RE_FENCE_START.sub("", text)
    stripped = _RE_FENCE_END.sub("", stripped)
    return stripped.strip()


def parse_findings_json(raw: str, agent_name: str) -> list[dict[str, Any]]:
    """Parse an LLM JSON-array response into a list of finding dicts.

    Handles common LLM output quirks:
    - Markdown code fences (```` ```json ... ``` ````)
    - Leading/trailing whitespace
    - Non-dict array elements
    - Missing or invalid fields

    Parameters
    ----------
    raw:
        The raw LLM response text.
    agent_name:
        The agent name to assign to each finding's ``agent`` field.

    Returns
    -------
    list[dict]
        A list of normalized finding dicts.  Returns ``[]`` if the
        response is not valid JSON or contains no valid findings.
    """
    text = _strip_code_fences(raw)

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        _log.warning(
            "%s agent returned invalid JSON (%.80s...) — returning empty list",
            agent_name,
            text,
        )
        return []

    if not isinstance(data, list):
        _log.warning("%s agent returned non-array JSON — returning empty list", agent_name)
        return []

    findings: list[dict[str, Any]] = []
    for item in data:
        finding = _normalize_finding(item, agent_name)
        if finding is not None:
            findings.append(finding)

    return findings


def parse_json_object(raw: str, context: str = "") -> dict[str, Any]:
    """Parse an LLM JSON-object response into a dict.

    Handles markdown code fences and leading/trailing whitespace.
    Returns ``{}`` if the response is not valid JSON or not a dict.

    Parameters
    ----------
    raw:
        The raw LLM response text.
    context:
        A label for logging (e.g. ``"risk"``).

    Returns
    -------
    dict
        The parsed JSON object, or ``{}`` on failure.
    """
    text = _strip_code_fences(raw)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        _log.warning(
            "%s returned invalid JSON object (%.80s...) — returning empty dict",
            context or "agent",
            text,
        )
        return {}
    if not isinstance(data, dict):
        _log.warning(
            "%s returned non-object JSON — returning empty dict",
            context or "agent",
        )
        return {}
    return data


def _normalize_finding(item: object, agent_name: str) -> dict[str, Any] | None:
    """Normalize a raw finding dict, returning ``None`` if invalid.

    A finding is valid if it has a non-empty ``title``.
    """
    if not isinstance(item, dict):
        return None

    title = str(item.get("title", "")).strip()
    if not title:
        return None

    severity = str(item.get("severity", "INFO")).upper().strip()
    if severity not in _VALID_SEVERITIES:
        severity = "INFO"

    line_raw = item.get("line")
    line: int | None
    if line_raw is None or line_raw == "":
        line = None
    else:
        try:
            line = int(line_raw)
            if line < 0:
                line = None
        except (ValueError, TypeError):
            line = None

    # Preserve an LLM-provided ``agent`` field (used by the consensus agent);
    # fall back to the caller-supplied ``agent_name`` for specialist agents.
    agent = str(item.get("agent", agent_name)).strip() or agent_name
    return {
        "agent": agent,
        "severity": severity,
        "title": title,
        "description": str(item.get("description", "")).strip(),
        "file": str(item.get("file", "")).strip(),
        "line": line,
        "suggestion": str(item.get("suggestion", "")).strip(),
    }


# ── Prompt construction ────────────────────────────────────────────────


def build_user_prompt(
    code_diff: str,
    scanner_context: str,
    file_tree: str = "",
) -> str:
    """Build the user-message text from diff, scanner context, and file tree.

    Parameters
    ----------
    code_diff:
        The unified diff text to review.
    scanner_context:
        Markdown context block from :func:`scanners.parser.format_as_context`.
    file_tree:
        Optional file-tree listing (for the architecture agent).

    Returns
    -------
    str
        The assembled user prompt.
    """
    parts: list[str] = []
    if file_tree:
        parts.append(f"## File Tree\n\n{file_tree}\n")
    if scanner_context:
        parts.append(f"{scanner_context}\n")
    parts.append(f"## Code Diff\n\n```\n{code_diff}\n```\n")
    return "\n".join(parts)


# ── Specialist agent driver ─────────────────────────────────────────────


def run_specialist_agent(
    agent_name: str,
    code_diff: str,
    scanner_context: str = "",
    file_tree: str = "",
) -> list[dict[str, Any]]:
    """Run a specialist agent end-to-end.

    Loads the agent's system prompt, builds the user prompt, calls the
    LLM router, and parses the JSON response into finding dicts.

    Parameters
    ----------
    agent_name:
        The prompt file basename (e.g. ``"security"``).
    code_diff:
        The unified diff text to review.
    scanner_context:
        Formatted scanner results (from :func:`format_as_context`).
    file_tree:
        Optional file-tree string (used by the architecture agent).

    Returns
    -------
    list[dict]
        A list of normalized finding dicts with ``agent`` set to
        ``agent_name``.
    """
    system_prompt = load_prompt(agent_name)
    user_prompt = build_user_prompt(code_diff, scanner_context, file_tree)

    response = invoke_llm(system_prompt, user_prompt)
    _log.info(
        "%s agent completed via %s (%s)",
        agent_name,
        response.provider,
        response.model_name,
    )

    findings = parse_findings_json(response.content, agent_name)
    _log.info("%s agent found %d findings", agent_name, len(findings))
    return findings
