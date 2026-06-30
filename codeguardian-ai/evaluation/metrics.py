"""Evaluation metrics for CodeGuardian AI review quality assessment.

Implements 7 rule-based metrics (Phase 1 evaluation strategy):

    1. Hallucination rate — findings referencing code not in the diff
    2. Issue relevance — keyword match against scanner findings
    3. Duplicate issue rate — similar titles across agents
    4. Severity consistency — agent severity vs consensus severity
    5. Completeness — coverage of expected issue categories
    6. Markdown formatting — structural validation of the report
    7. Overall confidence — weighted composite score

Each metric is a standalone function that can be tested independently.
The :func:`compute_all_metrics` function runs all 7 and returns a dict
matching the evaluation output schema (§10.2 of plan.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Sequence

__all__ = [
    "MetricResult",
    "hallucination_rate",
    "issue_relevance",
    "duplicate_rate",
    "severity_consistency",
    "completeness",
    "markdown_formatting",
    "overall_confidence",
    "compute_all_metrics",
]

# ── Severity ordering ────────────────────────────────────────────────
_SEVERITY_ORDER: dict[str, int] = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
    "INFO": 4,
}

#: Known issue categories for completeness checking.
_ISSUE_CATEGORIES: dict[str, list[str]] = {
    "security": ["sql injection", "xss", "csrf", "auth", "secret", "password", "token", "vulnerability", "insecure", "hardcoded"],
    "bug": ["null", "deref", "race", "deadlock", "exception", "unhandled", "error", "crash", "overflow", "off by"],
    "performance": ["loop", "query", "n+1", "memory", "slow", "inefficient", "cache", "batch", "bulk", "latency"],
    "quality": ["complexity", "long function", "naming", "duplicate", "magic", "comment", "docstring", "type hint", "lint"],
    "architecture": ["import", "circular", "coupling", "cohesion", "layer", "separation", "dependency", "god class", "module"],
}


@dataclass(frozen=True)
class MetricResult:
    """Container for a single metric's output.

    Attributes
    ----------
    score:
        Normalised score in ``[0.0, 1.0]`` where ``1.0`` is best.
    details:
        Human-readable explanation or raw count.
    """

    score: float
    details: str


# ════════════════════════════════════════════════════════════════════
#  1. Hallucination rate
# ════════════════════════════════════════════════════════════════════

# Extract file paths from diff headers like +++ b/path/to/file.py
_DIFF_FILE_RE = re.compile(r"^\+\+\+\s+b/(.+)$", re.MULTILINE)
# Extract the new-file start line and (optional) count from hunk headers
# like ``@@ -10,7 +12,9 @@``.  Group 1 = start line, group 2 = count.
_HUNK_HEADER_RE = re.compile(r"@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s+@@")


def _extract_diff_files(code_diff: str) -> set[str]:
    """Return the set of file paths mentioned in a unified diff."""
    return {m.strip() for m in _DIFF_FILE_RE.findall(code_diff)}


def _extract_diff_line_numbers(code_diff: str) -> set[int]:
    """Return the set of added line numbers referenced in a unified diff.

    Parses every ``@@ -old,count +start,count @@`` hunk header and expands
    the ``+start,count`` range into individual line numbers.  When the count
    is omitted it defaults to ``1``.
    """
    lines: set[int] = set()
    for match in _HUNK_HEADER_RE.finditer(code_diff):
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        # count == 0 means no lines in this hunk section
        if count == 0:
            continue
        lines.update(range(start, start + count))
    return lines


def hallucination_rate(
    findings: Sequence[dict[str, Any]],
    code_diff: str,
) -> MetricResult:
    """Check if findings reference code (files/lines) not present in the diff.

    A finding is flagged as a hallucination if:
        * It references a file path not in the diff, OR
        * It references a line number not among the diff's added lines.

    Returns a :class:`MetricResult` whose ``score`` is ``1 - rate``
    (so ``1.0`` means no hallucinations).
    """
    if not findings:
        return MetricResult(score=1.0, details="no findings to check")

    diff_files = _extract_diff_files(code_diff)
    diff_lines = _extract_diff_line_numbers(code_diff)

    hallucinated = 0
    for f in findings:
        # Check file reference
        file_ref = f.get("file") or f.get("filename") or ""
        if file_ref and diff_files and file_ref not in diff_files:
            hallucinated += 1
            continue
        # Check line reference
        line_ref = f.get("line") or f.get("line_number")
        if isinstance(line_ref, int) and diff_lines and line_ref not in diff_lines:
            hallucinated += 1
            continue

    rate = hallucinated / len(findings)
    score = 1.0 - rate
    return MetricResult(
        score=round(score, 4),
        details=f"{hallucinated}/{len(findings)} findings reference code not in diff",
    )


# ════════════════════════════════════════════════════════════════════
#  2. Issue relevance
# ════════════════════════════════════════════════════════════════════

_STOP_WORDS: frozenset[str] = frozenset({
    "the", "a", "an", "is", "are", "in", "on", "at", "to", "of", "and", "or",
    "for", "with", "this", "that", "it", "be", "by", "from", "as", "not",
    "code", "file", "line", "function", "method", "should", "may", "can",
    "use", "used", "using", "if", "then", "else", "when", "where", "which",
})


def _tokenize(text: str) -> set[str]:
    """Extract lowercase keyword tokens from text, excluding stop words."""
    tokens = re.findall(r"[a-z]{2,}", text.lower())
    return {t for t in tokens if t not in _STOP_WORDS}


def issue_relevance(
    findings: Sequence[dict[str, Any]],
    scanner_findings: Sequence[dict[str, Any]],
) -> MetricResult:
    """Measure keyword overlap between agent findings and scanner findings.

    For each agent finding, check if its keywords overlap with any scanner
    finding's keywords.  Returns the fraction of relevant findings.

    If there are no scanner findings, all agent findings are considered
    relevant (score = 1.0) since there's nothing to contradict them.
    """
    if not findings:
        return MetricResult(score=1.0, details="no findings to check")
    if not scanner_findings:
        return MetricResult(score=1.0, details="no scanner findings — all relevant by default")

    scanner_keyword_sets = [
        _tokenize(f"{sf.get('title', '')} {sf.get('message', '')} {sf.get('description', '')}")
        for sf in scanner_findings
    ]

    relevant = 0
    total_with_keywords = 0
    for f in findings:
        f_keywords = _tokenize(f"{f.get('title', '')} {f.get('description', '')}")
        if not f_keywords:
            continue
        total_with_keywords += 1
        for sk in scanner_keyword_sets:
            if f_keywords & sk:
                relevant += 1
                break

    # Divide by the number of findings that actually had keywords, not the
    # total finding count — findings without keywords are skipped entirely.
    score = relevant / total_with_keywords if total_with_keywords > 0 else 1.0
    return MetricResult(
        score=round(score, 4),
        details=f"{relevant}/{total_with_keywords} keyword-bearing findings match scanner output",
    )


# ════════════════════════════════════════════════════════════════════
#  3. Duplicate issue rate
# ════════════════════════════════════════════════════════════════════

_DUPLICATE_SIMILARITY_THRESHOLD = 0.70


def _normalize_title(title: str) -> str:
    """Normalize a finding title for comparison."""
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def _similarity(a: str, b: str) -> float:
    """Return the similarity ratio between two strings."""
    return SequenceMatcher(None, a, b).ratio()


def duplicate_rate(findings: Sequence[dict[str, Any]]) -> MetricResult:
    """Detect duplicate findings by comparing normalized titles.

    Two findings are duplicates if their normalized titles have a
    similarity ratio >= :data:`_DUPLICATE_SIMILARITY_THRESHOLD`.

    Returns a :class:`MetricResult` whose ``score`` is ``1 - rate``
    (so ``1.0`` means no duplicates).
    """
    if len(findings) < 2:
        return MetricResult(score=1.0, details="fewer than 2 findings — no duplicates possible")

    titles = [_normalize_title(f.get("title", "")) for f in findings]
    duplicates = 0
    for i in range(len(titles)):
        for j in range(i + 1, len(titles)):
            if titles[i] and _similarity(titles[i], titles[j]) >= _DUPLICATE_SIMILARITY_THRESHOLD:
                duplicates += 1

    # Rate = duplicate pairs / total possible pairs
    total_pairs = len(findings) * (len(findings) - 1) / 2
    rate = duplicates / total_pairs if total_pairs > 0 else 0.0
    score = 1.0 - rate
    return MetricResult(
        score=round(score, 4),
        details=f"{duplicates} duplicate pair(s) out of {int(total_pairs)} possible",
    )


# ════════════════════════════════════════════════════════════════════
#  4. Severity consistency
# ════════════════════════════════════════════════════════════════════


def _severity_rank(severity: str) -> int:
    """Return numeric rank for a severity string (0=CRITICAL, 4=INFO)."""
    return _SEVERITY_ORDER.get(str(severity).upper(), 4)


def severity_consistency(
    agent_findings: Sequence[dict[str, Any]],
    consensus_findings: Sequence[dict[str, Any]],
) -> MetricResult:
    """Compare agent severity vs consensus severity.

    For each consensus finding, find the best-matching agent finding
    (by title similarity) and check if severities match.  Returns the
    fraction of consistent findings.

    If either list is empty, returns ``1.0`` (nothing to compare).
    """
    if not consensus_findings or not agent_findings:
        return MetricResult(score=1.0, details="no findings to compare")

    consistent = 0
    for cf in consensus_findings:
        cf_title = _normalize_title(cf.get("title", ""))
        cf_sev = _severity_rank(cf.get("severity", "INFO"))

        best_match_score = 0.0
        best_sev = 4  # default to INFO
        for af in agent_findings:
            af_title = _normalize_title(af.get("title", ""))
            sim = _similarity(cf_title, af_title)
            if sim > best_match_score:
                best_match_score = sim
                best_sev = _severity_rank(af.get("severity", "INFO"))

        # Consider consistent if severity matches OR is within 1 rank
        if best_match_score >= 0.5 and abs(best_sev - cf_sev) <= 1:
            consistent += 1

    score = consistent / len(consensus_findings)
    return MetricResult(
        score=round(score, 4),
        details=f"{consistent}/{len(consensus_findings)} consensus findings have consistent severity",
    )


# ════════════════════════════════════════════════════════════════════
#  5. Completeness
# ════════════════════════════════════════════════════════════════════


def completeness(
    findings: Sequence[dict[str, Any]],
    expected_categories: Sequence[str] | None = None,
) -> MetricResult:
    """Check coverage of expected issue categories.

    A category is "covered" if any finding's title/description contains
    keywords associated with that category.

    Parameters
    ----------
    findings:
        The consensus findings to evaluate.
    expected_categories:
        Categories to check.  Defaults to all 5 specialist categories.
    """
    if expected_categories is None:
        expected_categories = list(_ISSUE_CATEGORIES.keys())

    if not expected_categories:
        return MetricResult(score=1.0, details="no categories expected")

    # Build a single text blob from all findings
    all_text = " ".join(
        f"{f.get('title', '')} {f.get('description', '')}" for f in findings
    ).lower()

    covered = 0
    uncovered: list[str] = []
    for cat in expected_categories:
        keywords = _ISSUE_CATEGORIES.get(cat, [cat])
        if any(kw in all_text for kw in keywords):
            covered += 1
        else:
            uncovered.append(cat)

    score = covered / len(expected_categories)
    detail = f"{covered}/{len(expected_categories)} categories covered"
    if uncovered:
        detail += f" (missing: {', '.join(uncovered)})"
    return MetricResult(score=round(score, 4), details=detail)


# ════════════════════════════════════════════════════════════════════
#  6. Markdown formatting
# ════════════════════════════════════════════════════════════════════

_HEADER_RE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
_CODE_BLOCK_RE = re.compile(r"```")
_LIST_RE = re.compile(r"^\s*[-*]\s+\S", re.MULTILINE)


def markdown_formatting(report: str) -> MetricResult:
    """Validate the structural formatting of a markdown report.

    Checks for:
        * At least one header (``#``)
        * Balanced code blocks (even number of ``` markers)
        * At least one list item (``-`` or ``*``)

    Returns ``1.0`` if all checks pass, ``0.0`` otherwise.
    """
    if not report or not report.strip():
        return MetricResult(score=0.0, details="empty report")

    checks_passed = 0
    total_checks = 3
    issues: list[str] = []

    # Check 1: At least one header
    if _HEADER_RE.search(report):
        checks_passed += 1
    else:
        issues.append("no headers")

    # Check 2: Balanced code blocks (at least one pair, even count)
    code_block_count = len(_CODE_BLOCK_RE.findall(report))
    if code_block_count > 0 and code_block_count % 2 == 0:
        checks_passed += 1
    elif code_block_count == 0:
        issues.append("no code blocks")
    else:
        issues.append("unbalanced code blocks")

    # Check 3: At least one list item
    if _LIST_RE.search(report):
        checks_passed += 1
    else:
        issues.append("no list items")

    score = checks_passed / total_checks
    detail = f"{checks_passed}/{total_checks} formatting checks passed"
    if issues:
        detail += f" ({'; '.join(issues)})"
    return MetricResult(score=score, details=detail)


# ════════════════════════════════════════════════════════════════════
#  7. Overall confidence (weighted composite)
# ════════════════════════════════════════════════════════════════════

#: Weights for the composite confidence score.
_CONFIDENCE_WEIGHTS: dict[str, float] = {
    "hallucination": 0.30,
    "relevance": 0.20,
    "duplication": 0.15,
    "severity": 0.15,
    "completeness": 0.10,
    "formatting": 0.10,
}


def overall_confidence(
    hallucination_score: float,
    relevance_score: float,
    duplication_score: float,
    severity_score: float,
    completeness_score: float,
    formatting_score: float,
) -> float:
    """Compute the weighted composite confidence score.

    All inputs should be in ``[0.0, 1.0]``.  Returns a score in
    ``[0.0, 1.0]`` where higher is better.
    """
    weighted = (
        hallucination_score * _CONFIDENCE_WEIGHTS["hallucination"]
        + relevance_score * _CONFIDENCE_WEIGHTS["relevance"]
        + duplication_score * _CONFIDENCE_WEIGHTS["duplication"]
        + severity_score * _CONFIDENCE_WEIGHTS["severity"]
        + completeness_score * _CONFIDENCE_WEIGHTS["completeness"]
        + formatting_score * _CONFIDENCE_WEIGHTS["formatting"]
    )
    return round(weighted, 4)


# ════════════════════════════════════════════════════════════════════
#  Aggregate: compute_all_metrics
# ════════════════════════════════════════════════════════════════════


def compute_all_metrics(
    *,
    agent_findings: Sequence[dict[str, Any]],
    consensus_findings: Sequence[dict[str, Any]],
    scanner_findings: Sequence[dict[str, Any]],
    code_diff: str,
    report: str,
    expected_categories: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run all 7 metrics and return the evaluation output dict.

    The returned dict matches the schema from §10.2 of plan.md::

        {
            "confidence": 0.94,
            "hallucination": false,
            "duplicate_findings": 0,
            "severity_consistency": 0.97,
            "overall_quality": 0.95,
            "hallucination_rate": 0.0,
            "relevance_score": 0.90,
            "duplicate_rate": 0.0,
            "completeness_score": 1.0,
            "formatting_score": 1.0,
            "details": {...}
        }
    """
    hal = hallucination_rate(consensus_findings, code_diff)
    rel = issue_relevance(consensus_findings, scanner_findings)
    dup = duplicate_rate(consensus_findings)
    sev = severity_consistency(agent_findings, consensus_findings)
    comp = completeness(consensus_findings, expected_categories)
    fmt = markdown_formatting(report)

    confidence = overall_confidence(
        hallucination_score=hal.score,
        relevance_score=rel.score,
        duplication_score=dup.score,
        severity_score=sev.score,
        completeness_score=comp.score,
        formatting_score=fmt.score,
    )

    # hallucination_rate returns score = 1 - rate, so rate = 1 - score
    hal_rate = round(1.0 - hal.score, 4)
    dup_rate_val = round(1.0 - dup.score, 4)

    return {
        "confidence": confidence,
        "hallucination": hal_rate > 0.05,
        "duplicate_findings": int(round(dup_rate_val * len(consensus_findings) * (len(consensus_findings) - 1) / 2)) if len(consensus_findings) > 1 else 0,
        "severity_consistency": sev.score,
        "overall_quality": confidence,
        "hallucination_rate": hal_rate,
        "relevance_score": rel.score,
        "duplicate_rate": dup_rate_val,
        "completeness_score": comp.score,
        "formatting_score": fmt.score,
        "details": {
            "hallucination": hal.details,
            "relevance": rel.details,
            "duplication": dup.details,
            "severity": sev.details,
            "completeness": comp.details,
            "formatting": fmt.details,
        },
    }
