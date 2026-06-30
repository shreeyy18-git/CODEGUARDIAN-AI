"""Tests for the static analysis scanner parsers and pipeline.

These tests exercise the JSON-parsing logic of each scanner runner
using sample outputs (no real scanner binaries required).  They also
test the merge and formatting logic in :mod:`scanners.parser`.

Run with::

    pytest tests/test_scanners.py -v
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from scanners.parser import (
    ScannerFinding,
    ScannerResult,
    format_as_context,
    merge_results,
)
from scanners.bandit_runner import _parse_bandit_json
from scanners.ruff_runner import _parse_ruff_json, _infer_severity
from scanners.semgrep_runner import _parse_semgrep_json


# ── ScannerFinding / ScannerResult tests ────────────────────────────────


class TestScannerFinding:
    """Tests for :class:`ScannerFinding`."""

    def test_severity_rank_critical(self) -> None:
        finding = ScannerFinding(
            scanner="bandit",
            rule_id="B602",
            severity="CRITICAL",
            file="app.py",
            line=10,
            message="subprocess call with shell=True",
        )
        assert finding.severity_rank == 4

    def test_severity_rank_info(self) -> None:
        finding = ScannerFinding(
            scanner="ruff",
            rule_id="D100",
            severity="INFO",
            file="app.py",
            line=1,
            message="Missing docstring",
        )
        assert finding.severity_rank == 0

    def test_severity_rank_unknown_defaults_to_zero(self) -> None:
        finding = ScannerFinding(
            scanner="semgrep",
            rule_id="x",
            severity="BOGUS",
            file="app.py",
            line=1,
            message="test",
        )
        assert finding.severity_rank == 0


class TestScannerResult:
    """Tests for :class:`ScannerResult`."""

    def _make_result(self) -> ScannerResult:
        return ScannerResult(
            findings=[
                ScannerFinding("semgrep", "rule-a", "HIGH", "a.py", 5, "High issue"),
                ScannerFinding("bandit", "B101", "LOW", "b.py", 10, "Low issue"),
                ScannerFinding("ruff", "F401", "MEDIUM", "c.py", 3, "Unused import"),
            ],
            raw_outputs={"semgrep": "{}"},
        )

    def test_total_findings(self) -> None:
        assert self._make_result().total_findings == 3

    def test_findings_by_scanner(self) -> None:
        grouped = self._make_result().findings_by_scanner()
        assert set(grouped.keys()) == {"semgrep", "bandit", "ruff"}
        assert len(grouped["semgrep"]) == 1

    def test_findings_by_severity(self) -> None:
        grouped = self._make_result().findings_by_severity()
        assert "HIGH" in grouped
        assert "LOW" in grouped
        assert "MEDIUM" in grouped

    def test_has_critical_false(self) -> None:
        assert self._make_result().has_critical() is False

    def test_has_critical_true(self) -> None:
        result = ScannerResult(
            findings=[
                ScannerFinding("bandit", "B602", "CRITICAL", "a.py", 1, "RCE"),
            ]
        )
        assert result.has_critical() is True

    def test_empty_result(self) -> None:
        result = ScannerResult()
        assert result.total_findings == 0
        assert result.findings_by_scanner() == {}
        assert result.has_critical() is False


# ── merge_results tests ─────────────────────────────────────────────────


class TestMergeResults:
    """Tests for :func:`scanners.parser.merge_results`."""

    def test_merge_multiple_results(self) -> None:
        r1 = ScannerResult(
            findings=[
                ScannerFinding("semgrep", "r1", "HIGH", "a.py", 1, "msg1"),
            ]
        )
        r2 = ScannerResult(
            findings=[
                ScannerFinding("bandit", "r2", "CRITICAL", "b.py", 2, "msg2"),
                ScannerFinding("ruff", "r3", "LOW", "c.py", 3, "msg3"),
            ]
        )
        merged = merge_results([r1, r2])
        assert merged.total_findings == 3
        # CRITICAL should be first after sorting by severity desc.
        assert merged.findings[0].severity == "CRITICAL"
        assert merged.findings[1].severity == "HIGH"
        assert merged.findings[2].severity == "LOW"

    def test_merge_empty_list(self) -> None:
        merged = merge_results([])
        assert merged.total_findings == 0

    def test_merge_preserves_raw_outputs(self) -> None:
        r1 = ScannerResult(raw_outputs={"semgrep": '{"a": 1}'})
        r2 = ScannerResult(raw_outputs={"bandit": '{"b": 2}'})
        merged = merge_results([r1, r2])
        assert merged.raw_outputs == {"semgrep": '{"a": 1}', "bandit": '{"b": 2}'}


# ── format_as_context tests ─────────────────────────────────────────────


class TestFormatAsContext:
    """Tests for :func:`scanners.parser.format_as_context`."""

    def test_empty_result_message(self) -> None:
        text = format_as_context(ScannerResult())
        assert "No issues found" in text

    def test_result_with_findings(self) -> None:
        result = ScannerResult(
            findings=[
                ScannerFinding("bandit", "B101", "HIGH", "app.py", 42, "Use of assert"),
            ]
        )
        text = format_as_context(result)
        assert "Static Analysis Results" in text
        assert "Total findings" in text
        assert "Bandit" in text
        assert "B101" in text
        assert "app.py" in text

    def test_pipe_in_message_escaped(self) -> None:
        result = ScannerResult(
            findings=[
                ScannerFinding("ruff", "E501", "LOW", "x.py", 1, "Line too | long"),
            ]
        )
        text = format_as_context(result)
        assert "\\|" in text


# ── Semgrep JSON parser tests ───────────────────────────────────────────


class TestSemgrepParser:
    """Tests for :func:`scanners.semgrep_runner._parse_semgrep_json`."""

    def test_parse_valid_output(self) -> None:
        raw = json.dumps({
            "results": [
                {
                    "check_id": "python.lang.security.audit.xxe",
                    "path": "src/parse.py",
                    "start": {"line": 15},
                    "extra": {
                        "severity": "error",
                        "message": "Possible XML external entity attack",
                    },
                },
                {
                    "check_id": "python.lang.best-practice",
                    "path": "src/util.py",
                    "start": {"line": 30},
                    "extra": {
                        "severity": "warning",
                        "message": "Use of deprecated function",
                    },
                },
            ]
        })
        findings = _parse_semgrep_json(raw)
        assert len(findings) == 2
        assert findings[0].scanner == "semgrep"
        assert findings[0].rule_id == "python.lang.security.audit.xxe"
        assert findings[0].severity == "HIGH"
        assert findings[0].file == "src/parse.py"
        assert findings[0].line == 15
        assert findings[1].severity == "MEDIUM"

    def test_parse_empty_output(self) -> None:
        assert _parse_semgrep_json("") == []

    def test_parse_invalid_json(self) -> None:
        assert _parse_semgrep_json("not json") == []

    def test_parse_no_results_key(self) -> None:
        assert _parse_semgrep_json(json.dumps({"errors": []})) == []


# ── Bandit JSON parser tests ────────────────────────────────────────────


class TestBanditParser:
    """Tests for :func:`scanners.bandit_runner._parse_bandit_json`."""

    def test_parse_valid_output(self) -> None:
        raw = json.dumps({
            "results": [
                {
                    "test_id": "B602",
                    "issue_severity": "HIGH",
                    "issue_text": "subprocess call with shell=True",
                    "filename": "src/runner.py",
                    "line_number": 25,
                },
                {
                    "test_id": "B101",
                    "issue_severity": "LOW",
                    "issue_text": "Use of assert detected",
                    "filename": "tests/test_app.py",
                    "line_number": 5,
                },
            ]
        })
        findings = _parse_bandit_json(raw)
        assert len(findings) == 2
        assert findings[0].scanner == "bandit"
        assert findings[0].rule_id == "B602"
        assert findings[0].severity == "HIGH"
        assert findings[0].file == "src/runner.py"
        assert findings[0].line == 25

    def test_parse_empty_output(self) -> None:
        assert _parse_bandit_json("") == []

    def test_parse_invalid_json(self) -> None:
        assert _parse_bandit_json("{broken") == []


# ── Ruff JSON parser tests ──────────────────────────────────────────────


class TestRuffParser:
    """Tests for :func:`scanners.ruff_runner._parse_ruff_json`."""

    def test_parse_valid_output(self) -> None:
        raw = json.dumps([
            {
                "code": "F401",
                "message": "'os' imported but unused",
                "filename": "src/app.py",
                "location": {"row": 1, "column": 1},
            },
            {
                "code": "E501",
                "message": "Line too long (95 > 88 characters)",
                "filename": "src/models.py",
                "location": {"row": 42, "column": 89},
            },
        ])
        findings = _parse_ruff_json(raw)
        assert len(findings) == 2
        assert findings[0].scanner == "ruff"
        assert findings[0].rule_id == "F401"
        assert findings[0].severity == "MEDIUM"
        assert findings[0].file == "src/app.py"
        assert findings[0].line == 1

    def test_parse_empty_output(self) -> None:
        assert _parse_ruff_json("") == []

    def test_parse_invalid_json(self) -> None:
        assert _parse_ruff_json("not json") == []

    def test_parse_dict_with_results_key(self) -> None:
        raw = json.dumps({
            "results": [
                {
                    "code": "S101",
                    "message": "Use of assert",
                    "filename": "test.py",
                    "location": {"row": 10},
                }
            ]
        })
        findings = _parse_ruff_json(raw)
        assert len(findings) == 1
        assert findings[0].rule_id == "S101"


# ── Ruff severity inference tests ───────────────────────────────────────


class TestRuffSeverityInference:
    """Tests for :func:`scanners.ruff_runner._infer_severity`."""

    @pytest.mark.parametrize(
        "rule_id, expected",
        [
            ("F401", "MEDIUM"),
            ("S101", "HIGH"),
            ("E501", "LOW"),
            ("W291", "LOW"),
            ("B006", "MEDIUM"),
            ("UP007", "LOW"),
            ("I001", "LOW"),
            ("RUF001", "MEDIUM"),
            ("PLR0913", "MEDIUM"),
            ("UNKNOWN", "INFO"),
        ],
    )
    def test_severity_inference(self, rule_id: str, expected: str) -> None:
        assert _infer_severity(rule_id) == expected


# ── Scanner runner subprocess tests (mocked) ────────────────────────────


class TestSemgrepRunner:
    """Tests for :func:`scanners.semgrep_runner.run_semgrep` with mocked subprocess."""

    def test_no_files_returns_empty(self) -> None:
        from scanners.semgrep_runner import run_semgrep

        result = run_semgrep("/repo", [])
        assert result.total_findings == 0

    def test_binary_not_found(self) -> None:
        from scanners.semgrep_runner import run_semgrep

        with patch("scanners.semgrep_runner.subprocess.run", side_effect=FileNotFoundError):
            result = run_semgrep("/repo", ["app.py"])
        assert result.total_findings == 0
        assert "semgrep_error" in result.raw_outputs


class TestBanditRunner:
    """Tests for :func:`scanners.bandit_runner.run_bandit` with mocked subprocess."""

    def test_no_python_files_returns_empty(self) -> None:
        from scanners.bandit_runner import run_bandit

        result = run_bandit("/repo", ["README.md", "config.json"])
        assert result.total_findings == 0

    def test_binary_not_found(self) -> None:
        from scanners.bandit_runner import run_bandit

        with patch("scanners.bandit_runner.subprocess.run", side_effect=FileNotFoundError):
            result = run_bandit("/repo", ["app.py"])
        assert result.total_findings == 0
        assert "bandit_error" in result.raw_outputs


class TestRuffRunner:
    """Tests for :func:`scanners.ruff_runner.run_ruff` with mocked subprocess."""

    def test_no_python_files_returns_empty(self) -> None:
        from scanners.ruff_runner import run_ruff

        result = run_ruff("/repo", ["style.css", "index.html"])
        assert result.total_findings == 0

    def test_binary_not_found(self) -> None:
        from scanners.ruff_runner import run_ruff

        with patch("scanners.ruff_runner.subprocess.run", side_effect=FileNotFoundError):
            result = run_ruff("/repo", ["app.py"])
        assert result.total_findings == 0
        assert "ruff_error" in result.raw_outputs
