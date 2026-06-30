"""Unified scanner data structures and result-merging logic.

This module defines the canonical dataclasses that every scanner runner
(Semgrep, Bandit, Ruff) returns, plus the :func:`merge_results` helper
that combines individual scanner outputs into a single
:class:`ScannerResult` consumed by the LangGraph workflow.

The merged result is also formatted into a markdown context block via
:func:`format_as_context` so it can be injected directly into agent
prompts.

Usage::

    from scanners.parser import ScannerFinding, merge_results, format_as_context

    result = merge_results([semgrep_result, bandit_result, ruff_result])
    context_block = format_as_context(result)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

__all__ = [
    "ScannerFinding",
    "ScannerResult",
    "merge_results",
    "format_as_context",
]

# Canonical severity ordering — higher index = more severe.
_SEVERITY_ORDER = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


@dataclass
class ScannerFinding:
    """A single issue discovered by a static-analysis scanner.

    Attributes
    ----------
    scanner:
        Which scanner produced this finding — ``"semgrep"``,
        ``"bandit"``, or ``"ruff"``.
    rule_id:
        The scanner-specific rule identifier (e.g. ``"B602"``,
        ``"python.lang.security.audit.xxe"``).
    severity:
        Normalised severity — one of ``CRITICAL``, ``HIGH``,
        ``MEDIUM``, ``LOW``, ``INFO``.
    file:
        Path to the file containing the finding.
    line:
        1-based line number of the finding (``0`` if unknown).
    message:
        Human-readable description of the issue.
    """

    scanner: str
    rule_id: str
    severity: str
    file: str
    line: int
    message: str

    @property
    def severity_rank(self) -> int:
        """Return a numeric severity rank (0 = INFO, 4 = CRITICAL)."""
        return _SEVERITY_ORDER.get(self.severity.upper(), 0)


@dataclass
class ScannerResult:
    """Aggregated output from one or more scanners.

    Attributes
    ----------
    findings:
        List of :class:`ScannerFinding` objects.
    raw_outputs:
        Mapping of scanner name → raw JSON string output.  Kept for
        debugging and audit purposes.
    total_findings:
        Convenience count of ``len(findings)``.
    """

    findings: list[ScannerFinding] = field(default_factory=list)
    raw_outputs: dict[str, str] = field(default_factory=dict)

    @property
    def total_findings(self) -> int:
        """Return the number of findings."""
        return len(self.findings)

    def findings_by_scanner(self) -> dict[str, list[ScannerFinding]]:
        """Group findings by scanner name."""
        grouped: dict[str, list[ScannerFinding]] = {}
        for f in self.findings:
            grouped.setdefault(f.scanner, []).append(f)
        return grouped

    def findings_by_severity(self) -> dict[str, list[ScannerFinding]]:
        """Group findings by severity level."""
        grouped: dict[str, list[ScannerFinding]] = {}
        for f in self.findings:
            grouped.setdefault(f.severity.upper(), []).append(f)
        return grouped

    def has_critical(self) -> bool:
        """Return ``True`` if any finding is CRITICAL severity."""
        return any(f.severity.upper() == "CRITICAL" for f in self.findings)


def merge_results(results: Sequence[ScannerResult]) -> ScannerResult:
    """Merge multiple :class:`ScannerResult` objects into one.

    Findings are concatenated and sorted by severity (descending) so the
    most urgent issues appear first.  Raw outputs are merged into a
    single dict.

    Parameters
    ----------
    results:
        One or more :class:`ScannerResult` objects (may be empty).

    Returns
    -------
    ScannerResult
        A single merged result containing all findings.
    """
    all_findings: list[ScannerFinding] = []
    all_raw: dict[str, str] = {}

    for res in results:
        all_findings.extend(res.findings)
        all_raw.update(res.raw_outputs)

    # Sort: CRITICAL first, then HIGH, MEDIUM, LOW, INFO.
    all_findings.sort(key=lambda f: f.severity_rank, reverse=True)

    return ScannerResult(findings=all_findings, raw_outputs=all_raw)


def format_as_context(result: ScannerResult) -> str:
    """Format a :class:`ScannerResult` as a markdown context block.

    This block is injected into agent system prompts so the AI agents
    are aware of static-analysis findings before producing their own
    review.

    Parameters
    ----------
    result:
        The merged scanner result.

    Returns
    -------
    str
        A markdown-formatted string summarising all findings.
    """
    if result.total_findings == 0:
        return "## Static Analysis Results\n\nNo issues found by Semgrep, Bandit, or Ruff.\n"

    lines: list[str] = [
        "## Static Analysis Results",
        "",
        f"**Total findings:** {result.total_findings}",
        "",
    ]

    for scanner_name, findings in result.findings_by_scanner().items():
        lines.append(f"### {scanner_name.title()} ({len(findings)} findings)")
        lines.append("")
        lines.append("| Severity | File | Line | Rule | Message |")
        lines.append("|----------|------|------|------|---------|")
        for f in findings:
            # Escape pipe characters in messages to not break the table.
            safe_msg = f.message.replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {f.severity} | {f.file} | {f.line} | {f.rule_id} | {safe_msg} |"
            )
        lines.append("")

    return "\n".join(lines)
