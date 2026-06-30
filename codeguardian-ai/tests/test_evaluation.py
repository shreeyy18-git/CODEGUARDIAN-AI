"""Tests for the evaluation layer: metrics, evaluator, and datasets.

Covers all 7 rule-based metrics with known inputs, the evaluator
orchestration functions, database persistence, and the curated
eval datasets.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from evaluation.metrics import (
    MetricResult,
    hallucination_rate,
    issue_relevance,
    duplicate_rate,
    severity_consistency,
    completeness,
    markdown_formatting,
    overall_confidence,
    compute_all_metrics,
)
from evaluation.evaluator import (
    scanner_findings_to_dicts,
    evaluate_review,
    evaluate_from_state,
    evaluate_and_store,
)
from evaluation.datasets import (
    EVAL_DATASETS,
    get_dataset,
    get_dataset_names,
)
from scanners.parser import ScannerFinding, ScannerResult
from database.models import Evaluation


# ════════════════════════════════════════════════════════════════════
#  Fixtures
# ════════════════════════════════════════════════════════════════════


@pytest.fixture()
def db() -> Session:
    """Yield a session backed by an in-memory SQLite database."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from database.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_finding(
    title: str = "Test finding",
    description: str = "A test issue",
    file: str = "app.py",
    line: int = 1,
    severity: str = "HIGH",
) -> dict[str, Any]:
    """Create a finding dict with sensible defaults."""
    return {
        "title": title,
        "description": description,
        "file": file,
        "line": line,
        "severity": severity,
    }


# ════════════════════════════════════════════════════════════════════
#  1. Hallucination rate
# ════════════════════════════════════════════════════════════════════


class TestHallucinationRate:
    """Tests for :func:`evaluation.metrics.hallucination_rate`."""

    def test_no_findings_returns_perfect(self) -> None:
        result = hallucination_rate([], "diff")
        assert result.score == 1.0

    def test_finding_with_correct_file(self) -> None:
        diff = "--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,5 @@\n+new line"
        findings = [_make_finding(file="app.py")]
        result = hallucination_rate(findings, diff)
        assert result.score == 1.0

    def test_finding_with_wrong_file_is_hallucination(self) -> None:
        diff = "--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,5 @@\n+new line"
        findings = [_make_finding(file="nonexistent.py")]
        result = hallucination_rate(findings, diff)
        assert result.score == 0.0

    def test_finding_with_correct_line(self) -> None:
        diff = "--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,5 @@\n+new line"
        findings = [_make_finding(line=1)]
        result = hallucination_rate(findings, diff)
        assert result.score == 1.0

    def test_finding_with_wrong_line_is_hallucination(self) -> None:
        diff = "--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,5 @@\n+new line"
        findings = [_make_finding(line=999)]
        result = hallucination_rate(findings, diff)
        assert result.score == 0.0

    def test_mixed_findings(self) -> None:
        diff = "--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,5 @@\n+new line"
        findings = [
            _make_finding(file="app.py", line=1),  # correct
            _make_finding(file="wrong.py", line=1),  # hallucinated
        ]
        result = hallucination_rate(findings, diff)
        assert result.score == 0.5

    def test_finding_without_file_or_line(self) -> None:
        """Findings without file/line references are not flagged."""
        diff = "--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,5 @@\n+new line"
        findings = [{"title": "Issue", "description": "desc"}]
        result = hallucination_rate(findings, diff)
        assert result.score == 1.0

    def test_empty_diff(self) -> None:
        """With an empty diff, file/line checks are skipped."""
        findings = [_make_finding(file="app.py", line=10)]
        result = hallucination_rate(findings, "")
        assert result.score == 1.0

    def test_filename_key_alias(self) -> None:
        """The ``filename`` key is accepted as an alias for ``file``."""
        diff = "--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,5 @@\n+new line"
        findings = [{"title": "T", "filename": "app.py", "line": 1}]
        result = hallucination_rate(findings, diff)
        assert result.score == 1.0

    def test_line_number_key_alias(self) -> None:
        """The ``line_number`` key is accepted as an alias for ``line``."""
        diff = "--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,5 @@\n+new line"
        findings = [{"title": "T", "file": "app.py", "line_number": 1}]
        result = hallucination_rate(findings, diff)
        assert result.score == 1.0


# ════════════════════════════════════════════════════════════════════
#  2. Issue relevance
# ════════════════════════════════════════════════════════════════════


class TestIssueRelevance:
    """Tests for :func:`evaluation.metrics.issue_relevance`."""

    def test_no_findings_returns_perfect(self) -> None:
        result = issue_relevance([], [])
        assert result.score == 1.0

    def test_no_scanner_findings_all_relevant(self) -> None:
        findings = [_make_finding(title="SQL injection")]
        result = issue_relevance(findings, [])
        assert result.score == 1.0

    def test_matching_keywords(self) -> None:
        findings = [_make_finding(title="SQL injection vulnerability")]
        scanners = [{"title": "SQL injection", "message": "dangerous query"}]
        result = issue_relevance(findings, scanners)
        assert result.score == 1.0

    def test_no_matching_keywords(self) -> None:
        findings = [_make_finding(title="Naming convention issue")]
        scanners = [{"title": "SQL injection", "message": "dangerous query"}]
        result = issue_relevance(findings, scanners)
        assert result.score == 0.0

    def test_partial_match(self) -> None:
        findings = [
            _make_finding(title="SQL injection"),
            _make_finding(title="Naming convention"),
        ]
        scanners = [{"title": "SQL injection", "message": "query"}]
        result = issue_relevance(findings, scanners)
        assert result.score == 0.5

    def test_finding_without_keywords_skipped(self) -> None:
        """Findings with no extractable keywords are skipped (not counted)."""
        findings = [
            {"title": "", "description": ""},
            _make_finding(title="SQL injection"),
        ]
        scanners = [{"title": "SQL injection", "message": "query"}]
        result = issue_relevance(findings, scanners)
        # Only 1 finding has keywords, and it matches → 1/1 = 1.0
        assert result.score == 1.0


# ════════════════════════════════════════════════════════════════════
#  3. Duplicate rate
# ════════════════════════════════════════════════════════════════════


class TestDuplicateRate:
    """Tests for :func:`evaluation.metrics.duplicate_rate`."""

    def test_no_findings(self) -> None:
        result = duplicate_rate([])
        assert result.score == 1.0

    def test_single_finding(self) -> None:
        result = duplicate_rate([_make_finding()])
        assert result.score == 1.0

    def test_no_duplicates(self) -> None:
        findings = [
            _make_finding(title="SQL injection"),
            _make_finding(title="Memory leak"),
        ]
        result = duplicate_rate(findings)
        assert result.score == 1.0

    def test_exact_duplicates(self) -> None:
        findings = [
            _make_finding(title="SQL injection vulnerability"),
            _make_finding(title="SQL injection vulnerability"),
        ]
        result = duplicate_rate(findings)
        assert result.score == 0.0

    def test_near_duplicates(self) -> None:
        findings = [
            _make_finding(title="SQL injection vulnerability detected"),
            _make_finding(title="SQL injection vulnerability found"),
        ]
        result = duplicate_rate(findings)
        assert result.score < 1.0

    def test_three_findings_one_dup_pair(self) -> None:
        findings = [
            _make_finding(title="SQL injection"),
            _make_finding(title="SQL injection"),
            _make_finding(title="Memory leak"),
        ]
        result = duplicate_rate(findings)
        # 1 dup pair out of 3 possible pairs → rate = 1/3
        assert 0.0 < result.score < 1.0


# ════════════════════════════════════════════════════════════════════
#  4. Severity consistency
# ════════════════════════════════════════════════════════════════════


class TestSeverityConsistency:
    """Tests for :func:`evaluation.metrics.severity_consistency`."""

    def test_empty_agent_findings(self) -> None:
        result = severity_consistency([], [_make_finding()])
        assert result.score == 1.0

    def test_empty_consensus_findings(self) -> None:
        result = severity_consistency([_make_finding()], [])
        assert result.score == 1.0

    def test_matching_severity(self) -> None:
        agent = [_make_finding(title="SQL injection", severity="HIGH")]
        consensus = [_make_finding(title="SQL injection", severity="HIGH")]
        result = severity_consistency(agent, consensus)
        assert result.score == 1.0

    def test_severity_within_one_rank(self) -> None:
        """HIGH vs MEDIUM is within 1 rank → consistent."""
        agent = [_make_finding(title="SQL injection", severity="HIGH")]
        consensus = [_make_finding(title="SQL injection", severity="MEDIUM")]
        result = severity_consistency(agent, consensus)
        assert result.score == 1.0

    def test_severity_too_far_apart(self) -> None:
        """CRITICAL vs INFO is 4 ranks apart → inconsistent."""
        agent = [_make_finding(title="SQL injection", severity="CRITICAL")]
        consensus = [_make_finding(title="SQL injection", severity="INFO")]
        result = severity_consistency(agent, consensus)
        assert result.score == 0.0

    def test_no_title_match(self) -> None:
        """If no agent finding matches the consensus title, it's inconsistent."""
        agent = [_make_finding(title="Memory leak", severity="HIGH")]
        consensus = [_make_finding(title="SQL injection", severity="HIGH")]
        result = severity_consistency(agent, consensus)
        assert result.score == 0.0


# ════════════════════════════════════════════════════════════════════
#  5. Completeness
# ════════════════════════════════════════════════════════════════════


class TestCompleteness:
    """Tests for :func:`evaluation.metrics.completeness`."""

    def test_no_findings(self) -> None:
        result = completeness([])
        # No findings → 0 categories covered
        assert result.score == 0.0

    def test_all_categories_covered(self) -> None:
        findings = [
            _make_finding(title="SQL injection", description="security vulnerability"),
            _make_finding(title="Null pointer", description="bug deref crash"),
            _make_finding(title="Slow loop", description="performance query n+1"),
            _make_finding(title="High complexity", description="quality long function"),
            _make_finding(title="Circular import", description="architecture coupling"),
        ]
        result = completeness(findings)
        assert result.score == 1.0

    def test_partial_coverage(self) -> None:
        findings = [
            _make_finding(title="SQL injection", description="security"),
            _make_finding(title="Null pointer", description="bug"),
        ]
        result = completeness(findings)
        # 2 out of 5 categories
        assert result.score == pytest.approx(0.4)

    def test_custom_categories(self) -> None:
        findings = [_make_finding(title="SQL injection", description="security")]
        result = completeness(findings, expected_categories=["security"])
        assert result.score == 1.0

    def test_empty_expected_categories(self) -> None:
        result = completeness([], expected_categories=[])
        assert result.score == 1.0

    def test_single_category_not_covered(self) -> None:
        findings = [_make_finding(title="Memory leak", description="performance")]
        result = completeness(findings, expected_categories=["security"])
        assert result.score == 0.0


# ════════════════════════════════════════════════════════════════════
#  6. Markdown formatting
# ════════════════════════════════════════════════════════════════════


class TestMarkdownFormatting:
    """Tests for :func:`evaluation.metrics.markdown_formatting`."""

    def test_empty_report(self) -> None:
        result = markdown_formatting("")
        assert result.score == 0.0

    def test_whitespace_only_report(self) -> None:
        result = markdown_formatting("   \n  ")
        assert result.score == 0.0

    def test_perfect_report(self) -> None:
        report = "# Header\n\n- List item\n\n```python\ncode\n```\n"
        result = markdown_formatting(report)
        assert result.score == 1.0

    def test_missing_header(self) -> None:
        report = "- List item\n\n```\ncode\n```\n"
        result = markdown_formatting(report)
        assert result.score < 1.0

    def test_missing_list(self) -> None:
        report = "# Header\n\n```\ncode\n```\n"
        result = markdown_formatting(report)
        assert result.score < 1.0

    def test_unbalanced_code_blocks(self) -> None:
        report = "# Header\n\n- Item\n\n```python\ncode\n"
        result = markdown_formatting(report)
        assert result.score < 1.0

    def test_only_header(self) -> None:
        result = markdown_formatting("# Just a header")
        # 1/3 checks pass
        assert result.score == pytest.approx(1 / 3)


# ════════════════════════════════════════════════════════════════════
#  7. Overall confidence
# ════════════════════════════════════════════════════════════════════


class TestOverallConfidence:
    """Tests for :func:`evaluation.metrics.overall_confidence`."""

    def test_perfect_scores(self) -> None:
        score = overall_confidence(1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
        assert score == 1.0

    def test_zero_scores(self) -> None:
        score = overall_confidence(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        assert score == 0.0

    def test_weighted_average(self) -> None:
        """Hallucination has weight 0.30, so it dominates."""
        score = overall_confidence(0.0, 1.0, 1.0, 1.0, 1.0, 1.0)
        # 0*0.30 + 1*0.20 + 1*0.15 + 1*0.15 + 1*0.10 + 1*0.10 = 0.70
        assert score == pytest.approx(0.70)

    def test_hallucination_weight_is_highest(self) -> None:
        """Hallucination (0.30) should have more impact than formatting (0.10)."""
        hal_zero = overall_confidence(0.0, 1.0, 1.0, 1.0, 1.0, 1.0)
        fmt_zero = overall_confidence(1.0, 1.0, 1.0, 1.0, 1.0, 0.0)
        assert hal_zero < fmt_zero

    def test_score_in_range(self) -> None:
        score = overall_confidence(0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
        assert 0.0 <= score <= 1.0


# ════════════════════════════════════════════════════════════════════
#  8. compute_all_metrics
# ════════════════════════════════════════════════════════════════════


class TestComputeAllMetrics:
    """Tests for :func:`evaluation.metrics.compute_all_metrics`."""

    def test_returns_dict_with_all_keys(self) -> None:
        result = compute_all_metrics(
            agent_findings=[],
            consensus_findings=[],
            scanner_findings=[],
            code_diff="",
            report="# Report\n\n- item\n\n```\ncode\n```\n",
        )
        expected_keys = {
            "confidence",
            "hallucination",
            "duplicate_findings",
            "severity_consistency",
            "overall_quality",
            "hallucination_rate",
            "relevance_score",
            "duplicate_rate",
            "completeness_score",
            "formatting_score",
            "details",
        }
        assert set(result.keys()) == expected_keys

    def test_empty_inputs_high_confidence(self) -> None:
        """With no findings, most metrics return 1.0."""
        result = compute_all_metrics(
            agent_findings=[],
            consensus_findings=[],
            scanner_findings=[],
            code_diff="",
            report="# Report\n\n- item\n\n```\ncode\n```\n",
        )
        assert result["confidence"] > 0.8
        assert result["hallucination"] is False
        assert result["duplicate_findings"] == 0

    def test_hallucination_flag_true_when_rate_high(self) -> None:
        diff = "--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,5 @@\n+new line"
        findings = [_make_finding(file="wrong.py", line=1)]
        result = compute_all_metrics(
            agent_findings=findings,
            consensus_findings=findings,
            scanner_findings=[],
            code_diff=diff,
            report="# R\n\n- i\n\n```\nc\n```\n",
        )
        assert result["hallucination"] is True

    def test_details_subdict(self) -> None:
        result = compute_all_metrics(
            agent_findings=[],
            consensus_findings=[],
            scanner_findings=[],
            code_diff="",
            report="# R\n\n- i\n\n```\nc\n```\n",
        )
        assert "details" in result
        assert isinstance(result["details"], dict)
        assert "hallucination" in result["details"]
        assert "relevance" in result["details"]

    def test_duplicate_findings_count(self) -> None:
        findings = [
            _make_finding(title="SQL injection"),
            _make_finding(title="SQL injection"),
        ]
        result = compute_all_metrics(
            agent_findings=findings,
            consensus_findings=findings,
            scanner_findings=[],
            code_diff="",
            report="# R\n\n- i\n\n```\nc\n```\n",
        )
        assert result["duplicate_findings"] >= 1


# ════════════════════════════════════════════════════════════════════
#  9. Evaluator: scanner_findings_to_dicts
# ════════════════════════════════════════════════════════════════════


class TestScannerFindingsToDicts:
    """Tests for :func:`evaluation.evaluator.scanner_findings_to_dicts`."""

    def test_none_returns_empty(self) -> None:
        assert scanner_findings_to_dicts(None) == []

    def test_list_of_dicts_passthrough(self) -> None:
        dicts = [{"title": "T", "message": "M"}]
        result = scanner_findings_to_dicts(dicts)
        assert result == dicts

    def test_scanner_finding_dataclass(self) -> None:
        sf = ScannerFinding(
            scanner="bandit",
            rule_id="B608",
            severity="HIGH",
            file="app.py",
            line=10,
            message="SQL injection",
        )
        result = scanner_findings_to_dicts([sf])
        assert len(result) == 1
        assert result[0]["title"] == "SQL injection"
        assert result[0]["file"] == "app.py"
        assert result[0]["line"] == 10
        assert result[0]["severity"] == "HIGH"

    def test_scanner_result_object(self) -> None:
        sf = ScannerFinding(
            scanner="ruff",
            rule_id="C901",
            severity="MEDIUM",
            file="proc.py",
            line=2,
            message="Too complex",
        )
        sr = ScannerResult(findings=[sf])
        result = scanner_findings_to_dicts(sr)
        assert len(result) == 1
        assert result[0]["title"] == "Too complex"

    def test_mixed_list(self) -> None:
        sf = ScannerFinding(
            scanner="bandit",
            rule_id="B105",
            severity="HIGH",
            file="config.py",
            line=7,
            message="Hardcoded secret",
        )
        mixed = [sf, {"title": "Dict finding", "message": "msg"}]
        result = scanner_findings_to_dicts(mixed)
        assert len(result) == 2

    def test_unknown_type_skipped(self) -> None:
        result = scanner_findings_to_dicts([42, "string", None])  # type: ignore[list-item]
        assert result == []


# ════════════════════════════════════════════════════════════════════
#  10. Evaluator: evaluate_review
# ════════════════════════════════════════════════════════════════════


class TestEvaluateReview:
    """Tests for :func:`evaluation.evaluator.evaluate_review`."""

    def test_returns_dict(self) -> None:
        result = evaluate_review(
            agent_findings=[],
            consensus_findings=[],
            scanner_findings=None,
            code_diff="",
            report="# R\n\n- i\n\n```\nc\n```\n",
        )
        assert isinstance(result, dict)
        assert "confidence" in result

    def test_with_scanner_result(self) -> None:
        sf = ScannerFinding(
            scanner="bandit",
            rule_id="B608",
            severity="HIGH",
            file="app.py",
            line=10,
            message="SQL injection",
        )
        sr = ScannerResult(findings=[sf])
        result = evaluate_review(
            agent_findings=[_make_finding(title="SQL injection")],
            consensus_findings=[_make_finding(title="SQL injection")],
            scanner_findings=sr,
            code_diff="--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,5 @@\n+new",
            report="# R\n\n- i\n\n```\nc\n```\n",
        )
        assert result["relevance_score"] > 0.0

    def test_with_expected_categories(self) -> None:
        result = evaluate_review(
            agent_findings=[],
            consensus_findings=[_make_finding(title="SQL injection", description="security")],
            scanner_findings=[],
            code_diff="",
            report="# R\n\n- i\n\n```\nc\n```\n",
            expected_categories=["security"],
        )
        assert result["completeness_score"] == 1.0


# ════════════════════════════════════════════════════════════════════
#  11. Evaluator: evaluate_from_state
# ════════════════════════════════════════════════════════════════════


class TestEvaluateFromState:
    """Tests for :func:`evaluation.evaluator.evaluate_from_state`."""

    def test_extracts_from_state(self) -> None:
        state: dict[str, Any] = {
            "security_findings": [_make_finding(title="SQL injection")],
            "bug_findings": [],
            "performance_findings": [],
            "quality_findings": [],
            "architecture_findings": [],
            "consensus_findings": [_make_finding(title="SQL injection")],
            "scanner_result": ScannerResult(findings=[]),
            "code_diff": "--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,5 @@\n+new",
            "final_report": "# Report\n\n- item\n\n```\ncode\n```\n",
        }
        result = evaluate_from_state(state)
        assert "confidence" in result
        assert isinstance(result["confidence"], float)

    def test_empty_state(self) -> None:
        result = evaluate_from_state({})
        assert result["confidence"] >= 0.8

    def test_merges_all_agent_findings(self) -> None:
        state: dict[str, Any] = {
            "security_findings": [_make_finding(title="SQL injection")],
            "bug_findings": [_make_finding(title="Null pointer")],
            "performance_findings": [_make_finding(title="Slow loop")],
            "quality_findings": [_make_finding(title="Complex function")],
            "architecture_findings": [_make_finding(title="Circular import")],
            "consensus_findings": [_make_finding(title="SQL injection")],
            "code_diff": "",
            "final_report": "# R\n\n- i\n\n```\nc\n```\n",
        }
        result = evaluate_from_state(state)
        # All 5 agent findings should be passed to severity_consistency
        assert "severity_consistency" in result


# ════════════════════════════════════════════════════════════════════
#  12. Evaluator: evaluate_and_store
# ════════════════════════════════════════════════════════════════════


class TestEvaluateAndStore:
    """Tests for :func:`evaluation.evaluator.evaluate_and_store`."""

    def _create_review(self, db: Session) -> int:
        """Create a minimal review row and return its ID."""
        from database.crud import create_repository, create_pull_request, create_review

        repo = create_repository(db, name="test-repo", owner="test-owner")
        pr = create_pull_request(
            db,
            repository_id=repo.id,
            pr_number=1,
            commit_sha="abc123",
            branch="main",
        )
        review = create_review(db, pr_id=pr.id)
        return review.id

    def test_persists_evaluation_row(self, db: Session) -> None:
        review_id = self._create_review(db)
        result = evaluate_and_store(
            db,
            review_id=review_id,
            agent_findings=[],
            consensus_findings=[],
            scanner_findings=None,
            code_diff="",
            report="# R\n\n- i\n\n```\nc\n```\n",
        )
        assert isinstance(result, Evaluation)
        assert result.review_id == review_id
        assert result.id is not None
        assert isinstance(result.confidence, float)
        assert isinstance(result.hallucination, bool)
        assert isinstance(result.duplicate_rate, float)
        assert isinstance(result.quality_score, float)

    def test_with_state_dict(self, db: Session) -> None:
        review_id = self._create_review(db)
        state: dict[str, Any] = {
            "security_findings": [_make_finding(title="SQL injection")],
            "consensus_findings": [_make_finding(title="SQL injection")],
            "code_diff": "",
            "final_report": "# R\n\n- i\n\n```\nc\n```\n",
        }
        result = evaluate_and_store(db, review_id=review_id, state=state)
        assert isinstance(result, Evaluation)
        assert result.review_id == review_id

    def test_explicit_args_override_state(self, db: Session) -> None:
        """Explicitly passed args take precedence over state values."""
        review_id = self._create_review(db)
        state: dict[str, Any] = {
            "consensus_findings": [_make_finding(title="From state")],
            "final_report": "# State report\n\n- item\n\n```\ncode\n```\n",
        }
        result = evaluate_and_store(
            db,
            review_id=review_id,
            consensus_findings=[_make_finding(title="Explicit")],
            code_diff="",
            report="# Explicit report\n\n- item\n\n```\ncode\n```\n",
            state=state,
        )
        assert isinstance(result, Evaluation)


# ════════════════════════════════════════════════════════════════════
#  13. Datasets
# ════════════════════════════════════════════════════════════════════


class TestDatasets:
    """Tests for the curated evaluation datasets."""

    def test_datasets_is_list(self) -> None:
        assert isinstance(EVAL_DATASETS, list)
        assert len(EVAL_DATASETS) >= 5

    def test_each_dataset_has_required_keys(self) -> None:
        required = {"name", "description", "code_diff", "scanner_findings", "expected_categories", "expected_min_findings", "sample_report"}
        for ds in EVAL_DATASETS:
            missing = required - set(ds.keys())
            assert not missing, f"Dataset '{ds.get('name', '?')}' missing keys: {missing}"

    def test_get_dataset_by_name(self) -> None:
        ds = get_dataset("sql_injection")
        assert ds is not None
        assert ds["name"] == "sql_injection"

    def test_get_dataset_unknown_returns_none(self) -> None:
        assert get_dataset("nonexistent") is None

    def test_get_dataset_names(self) -> None:
        names = get_dataset_names()
        assert "sql_injection" in names
        assert "clean_code" in names
        assert len(names) == len(EVAL_DATASETS)

    def test_sql_injection_diff_has_file(self) -> None:
        ds = get_dataset("sql_injection")
        assert ds is not None
        assert "app.py" in ds["code_diff"]

    def test_clean_code_has_no_scanner_findings(self) -> None:
        ds = get_dataset("clean_code")
        assert ds is not None
        assert ds["scanner_findings"] == []
        assert ds["expected_min_findings"] == 0

    def test_new_module_diff_has_dev_null(self) -> None:
        ds = get_dataset("new_module")
        assert ds is not None
        assert "/dev/null" in ds["code_diff"]

    def test_sample_reports_are_valid_markdown(self) -> None:
        """All sample reports should pass the formatting metric."""
        for ds in EVAL_DATASETS:
            result = markdown_formatting(ds["sample_report"])
            assert result.score == 1.0, f"Report for '{ds['name']}' failed formatting: {result.details}"


# ════════════════════════════════════════════════════════════════════
#  14. Integration: datasets + metrics
# ════════════════════════════════════════════════════════════════════


class TestDatasetMetricsIntegration:
    """Run metrics on the curated datasets to verify they produce sane results."""

    def test_sql_injection_evaluates_well(self) -> None:
        ds = get_dataset("sql_injection")
        assert ds is not None
        # Simulate consensus findings matching the scanner
        consensus = [
            _make_finding(
                title="SQL Injection in app.py",
                description="User input concatenated into SQL query",
                file="app.py",
                line=12,
                severity="HIGH",
            ),
        ]
        result = compute_all_metrics(
            agent_findings=consensus,
            consensus_findings=consensus,
            scanner_findings=ds["scanner_findings"],
            code_diff=ds["code_diff"],
            report=ds["sample_report"],
        )
        # Should have high relevance (keywords match)
        assert result["relevance_score"] > 0.0
        # Should have no hallucinations (file/line in diff)
        assert result["hallucination"] is False
        # Formatting should be perfect
        assert result["formatting_score"] == 1.0

    def test_clean_code_evaluates_well(self) -> None:
        ds = get_dataset("clean_code")
        assert ds is not None
        result = compute_all_metrics(
            agent_findings=[],
            consensus_findings=[],
            scanner_findings=ds["scanner_findings"],
            code_diff=ds["code_diff"],
            report=ds["sample_report"],
        )
        # No findings → no hallucinations, no duplicates
        assert result["hallucination"] is False
        assert result["duplicate_findings"] == 0
        assert result["formatting_score"] == 1.0

    def test_hardcoded_secret_evaluates_well(self) -> None:
        ds = get_dataset("hardcoded_secret")
        assert ds is not None
        consensus = [
            _make_finding(
                title="Hardcoded Secret in config.py",
                description="API key and secret stored in source code",
                file="config.py",
                line=7,
                severity="HIGH",
            ),
        ]
        result = compute_all_metrics(
            agent_findings=consensus,
            consensus_findings=consensus,
            scanner_findings=ds["scanner_findings"],
            code_diff=ds["code_diff"],
            report=ds["sample_report"],
        )
        assert result["relevance_score"] > 0.0
        assert result["hallucination"] is False
