"""Tests for the agent implementations and shared utilities.

These tests mock :func:`llm.router.invoke_llm` so no real LLM calls are
made.  They verify:

* JSON parsing helpers (``parse_findings_json``, ``parse_json_object``)
* Finding normalization (``_normalize_finding``)
* Specialist agent wrappers (security, bug, performance, quality, architecture)
* Consensus agent (merge + dedup + early-exit)
* Risk agent (deterministic scoring + LLM summary + fallback)
* Report agent (LLM markdown + fallback template)

Run with::

    pytest tests/test_agents.py -v
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.base import (
    _normalize_finding,
    build_user_prompt,
    parse_findings_json,
    parse_json_object,
    run_specialist_agent,
)
from agents.security_agent import run_security_agent
from agents.bug_agent import run_bug_agent
from agents.performance_agent import run_performance_agent
from agents.quality_agent import run_quality_agent
from agents.architecture_agent import run_architecture_agent
from agents.consensus_agent import run_consensus_agent
from agents.risk_agent import (
    _compute_scores,
    _default_summary,
    _worst_score,
    run_risk_agent,
)
from agents.report_agent import run_report_agent, _fallback_report
from llm.router import LLMResponse


# ── Helpers ──────────────────────────────────────────────────────────────


def _mock_llm_response(content: str) -> LLMResponse:
    """Build a fake :class:`LLMResponse` with the given content."""
    return LLMResponse(
        content=content,
        provider="groq",
        model_name="llama-3.3-70b-versatile",
        fell_back=False,
        error=None,
    )


def _finding(
    agent: str = "security",
    severity: str = "HIGH",
    title: str = "Test finding",
    description: str = "A test description",
    file: str = "src/app.py",
    line: int | None = 42,
    suggestion: str = "Fix it",
) -> dict:
    """Build a single normalized finding dict."""
    return {
        "agent": agent,
        "severity": severity,
        "title": title,
        "description": description,
        "file": file,
        "line": line,
        "suggestion": suggestion,
    }


# ── parse_findings_json ─────────────────────────────────────────────────


class TestParseFindingsJson:
    """Tests for :func:`agents.base.parse_findings_json`."""

    def test_valid_json_array(self) -> None:
        """A valid JSON array of findings is parsed correctly."""
        raw = json.dumps([
            {"title": "Issue 1", "severity": "HIGH", "file": "a.py", "line": 10},
            {"title": "Issue 2", "severity": "LOW", "file": "b.py"},
        ])
        result = parse_findings_json(raw, "security")
        assert len(result) == 2
        assert result[0]["title"] == "Issue 1"
        assert result[0]["agent"] == "security"
        assert result[0]["severity"] == "HIGH"
        assert result[1]["line"] is None

    def test_code_fenced_json(self) -> None:
        """Markdown code fences are stripped before parsing."""
        raw = "```json\n[{\"title\": \"Fenced\"}]\n```"
        result = parse_findings_json(raw, "bug")
        assert len(result) == 1
        assert result[0]["title"] == "Fenced"

    def test_invalid_json_returns_empty(self) -> None:
        """Invalid JSON returns an empty list."""
        result = parse_findings_json("not json at all", "security")
        assert result == []

    def test_non_array_json_returns_empty(self) -> None:
        """A JSON object (not array) returns an empty list."""
        result = parse_findings_json('{"key": "value"}', "security")
        assert result == []

    def test_non_dict_elements_skipped(self) -> None:
        """Non-dict array elements are silently skipped."""
        raw = json.dumps(["string", 42, {"title": "Valid"}, None])
        result = parse_findings_json(raw, "security")
        assert len(result) == 1
        assert result[0]["title"] == "Valid"

    def test_missing_title_skipped(self) -> None:
        """Findings without a title are skipped."""
        raw = json.dumps([{"severity": "HIGH"}, {"title": "Has title"}])
        result = parse_findings_json(raw, "security")
        assert len(result) == 1

    def test_empty_array(self) -> None:
        """An empty JSON array returns an empty list."""
        assert parse_findings_json("[]", "security") == []

    def test_preserves_llm_agent_field(self) -> None:
        """The LLM-provided ``agent`` field is preserved (for consensus)."""
        raw = json.dumps([{"title": "X", "agent": "bug"}])
        result = parse_findings_json(raw, "consensus")
        assert result[0]["agent"] == "bug"


# ── parse_json_object ───────────────────────────────────────────────────


class TestParseJsonObject:
    """Tests for :func:`agents.base.parse_json_object`."""

    def test_valid_object(self) -> None:
        """A valid JSON object is parsed correctly."""
        result = parse_json_object('{"summary": "hello"}', "risk")
        assert result == {"summary": "hello"}

    def test_code_fenced_object(self) -> None:
        """Code fences are stripped."""
        result = parse_json_object("```json\n{\"a\": 1}\n```", "risk")
        assert result == {"a": 1}

    def test_invalid_json_returns_empty(self) -> None:
        """Invalid JSON returns an empty dict."""
        assert parse_json_object("not json", "risk") == {}

    def test_non_object_returns_empty(self) -> None:
        """A JSON array (not object) returns an empty dict."""
        assert parse_json_object("[1, 2, 3]", "risk") == {}


# ── _normalize_finding ──────────────────────────────────────────────────


class TestNormalizeFinding:
    """Tests for :func:`agents.base._normalize_finding`."""

    def test_full_valid_finding(self) -> None:
        """A complete finding dict is normalized with all fields."""
        item = {
            "title": "SQL Injection",
            "severity": "critical",
            "description": "Bad query",
            "file": "db.py",
            "line": "15",
            "suggestion": "Use parameterized queries",
        }
        result = _normalize_finding(item, "security")
        assert result is not None
        assert result["title"] == "SQL Injection"
        assert result["severity"] == "CRITICAL"
        assert result["line"] == 15
        assert result["agent"] == "security"

    def test_invalid_severity_defaults_to_info(self) -> None:
        """An unrecognized severity is normalized to INFO."""
        result = _normalize_finding(
            {"title": "X", "severity": "URGENT"}, "bug",
        )
        assert result is not None
        assert result["severity"] == "INFO"

    def test_negative_line_becomes_none(self) -> None:
        """A negative line number is converted to None."""
        result = _normalize_finding(
            {"title": "X", "line": -5}, "bug",
        )
        assert result is not None
        assert result["line"] is None

    def test_non_integer_line_becomes_none(self) -> None:
        """A non-numeric line value is converted to None."""
        result = _normalize_finding(
            {"title": "X", "line": "abc"}, "bug",
        )
        assert result is not None
        assert result["line"] is None

    def test_non_dict_returns_none(self) -> None:
        """A non-dict item returns None."""
        assert _normalize_finding("not a dict", "security") is None
        assert _normalize_finding(42, "security") is None
        assert _normalize_finding(None, "security") is None

    def test_missing_title_returns_none(self) -> None:
        """An item without a title returns None."""
        assert _normalize_finding({"severity": "HIGH"}, "security") is None

    def test_empty_title_returns_none(self) -> None:
        """An item with an empty/whitespace title returns None."""
        assert _normalize_finding({"title": "   "}, "security") is None


# ── build_user_prompt ──────────────────────────────────────────────────


class TestBuildUserPrompt:
    """Tests for :func:`agents.base.build_user_prompt`."""

    def test_includes_diff(self) -> None:
        """The code diff is included in the prompt."""
        prompt = build_user_prompt("diff content", "")
        assert "diff content" in prompt
        assert "## Code Diff" in prompt

    def test_includes_scanner_context(self) -> None:
        """Scanner context is included when provided."""
        prompt = build_user_prompt("diff", "scanner data")
        assert "scanner data" in prompt

    def test_includes_file_tree(self) -> None:
        """The file tree is included when provided."""
        prompt = build_user_prompt("diff", "", "tree structure")
        assert "## File Tree" in prompt
        assert "tree structure" in prompt

    def test_omits_empty_sections(self) -> None:
        """Empty sections are omitted from the prompt."""
        prompt = build_user_prompt("diff", "")
        assert "## File Tree" not in prompt


# ── Specialist agents ───────────────────────────────────────────────────


class TestSpecialistAgents:
    """Tests for the five specialist agent wrappers."""

    @pytest.mark.parametrize(
        "agent_func, agent_name",
        [
            (run_security_agent, "security"),
            (run_bug_agent, "bug"),
            (run_performance_agent, "performance"),
            (run_quality_agent, "quality"),
        ],
    )
    def test_specialist_returns_findings(
        self, agent_func, agent_name: str,
    ) -> None:
        """Each specialist agent returns parsed findings with the correct agent name."""
        raw = json.dumps([{"title": "Test issue", "severity": "HIGH"}])
        with patch("agents.base.invoke_llm", return_value=_mock_llm_response(raw)):
            findings = agent_func("some diff", "scanner ctx")
        assert len(findings) == 1
        assert findings[0]["agent"] == agent_name
        assert findings[0]["title"] == "Test issue"

    def test_architecture_agent_with_file_tree(self) -> None:
        """The architecture agent passes the file_tree to the driver."""
        raw = json.dumps([{"title": "Arch issue"}])
        with patch("agents.base.invoke_llm", return_value=_mock_llm_response(raw)) as mock_llm:
            findings = run_architecture_agent("diff", "ctx", "file tree here")
        assert len(findings) == 1
        assert findings[0]["agent"] == "architecture"
        # Verify the file tree was included in the user prompt.
        _, kwargs = mock_llm.call_args
        user_prompt = mock_llm.call_args[0][1]
        assert "file tree here" in user_prompt

    def test_specialist_empty_llm_response(self) -> None:
        """An empty LLM response yields an empty findings list."""
        with patch("agents.base.invoke_llm", return_value=_mock_llm_response("[]")):
            findings = run_security_agent("diff", "")
        assert findings == []

    def test_specialist_invalid_json_returns_empty(self) -> None:
        """Invalid JSON from the LLM yields an empty findings list."""
        with patch("agents.base.invoke_llm", return_value=_mock_llm_response("not json")):
            findings = run_bug_agent("diff", "")
        assert findings == []

    def test_run_specialist_agent_directly(self) -> None:
        """The generic driver works for any agent name."""
        raw = json.dumps([{"title": "Direct", "severity": "MEDIUM"}])
        with patch("agents.base.invoke_llm", return_value=_mock_llm_response(raw)):
            findings = run_specialist_agent("quality", "diff", "ctx")
        assert len(findings) == 1
        assert findings[0]["agent"] == "quality"


# ── Consensus agent ────────────────────────────────────────────────────


class TestConsensusAgent:
    """Tests for :func:`agents.consensus_agent.run_consensus_agent`."""

    def test_merges_all_findings(self) -> None:
        """The consensus agent merges findings from all specialists."""
        sec = [_finding(agent="security", title="Sec issue")]
        bug = [_finding(agent="bug", title="Bug issue")]
        perf = [_finding(agent="performance", title="Perf issue")]
        qual = [_finding(agent="quality", title="Qual issue")]
        arch = [_finding(agent="architecture", title="Arch issue")]

        merged = json.dumps([
            {"title": "Merged 1", "severity": "CRITICAL", "agent": "security"},
            {"title": "Merged 2", "severity": "LOW", "agent": "bug"},
        ])
        with patch("agents.consensus_agent.invoke_llm", return_value=_mock_llm_response(merged)):
            result = run_consensus_agent(sec, bug, perf, qual, arch)
        assert len(result) == 2
        assert result[0]["title"] == "Merged 1"

    def test_empty_findings_early_exit(self) -> None:
        """When all specialist findings are empty, no LLM call is made."""
        with patch("agents.consensus_agent.invoke_llm") as mock_llm:
            result = run_consensus_agent([], [], [], [], [])
        assert result == []
        mock_llm.assert_not_called()

    def test_some_empty_some_not(self) -> None:
        """Mixed empty/non-empty findings still trigger the LLM call."""
        merged = json.dumps([{"title": "Only one"}])
        with patch("agents.consensus_agent.invoke_llm", return_value=_mock_llm_response(merged)):
            result = run_consensus_agent(
                [_finding(title="X")], [], [], [], [],
            )
        assert len(result) == 1


# ── Risk agent: deterministic scoring ──────────────────────────────────


class TestRiskScoring:
    """Tests for the deterministic risk scoring functions."""

    def test_worst_score_no_findings(self) -> None:
        """No findings → score 1.0 (no risk)."""
        assert _worst_score([], {"security"}) == 1.0

    def test_worst_score_only_info(self) -> None:
        """INFO-only findings → score 1.0 (no risk)."""
        findings = [_finding(severity="INFO")]
        assert _worst_score(findings, {"security"}) == 1.0

    def test_worst_score_critical(self) -> None:
        """A CRITICAL finding → score 0.0."""
        findings = [_finding(severity="CRITICAL")]
        assert _worst_score(findings, {"security"}) == 0.0

    def test_worst_score_takes_minimum(self) -> None:
        """The worst (minimum) score among findings is returned."""
        findings = [
            _finding(severity="LOW"),
            _finding(severity="HIGH"),
            _finding(severity="MEDIUM"),
        ]
        assert _worst_score(findings, {"security"}) == 0.25

    def test_worst_score_filters_by_agent(self) -> None:
        """Only findings from the specified agents are considered."""
        findings = [
            _finding(agent="security", severity="CRITICAL"),
            _finding(agent="bug", severity="LOW"),
        ]
        assert _worst_score(findings, {"security"}) == 0.0
        assert _worst_score(findings, {"bug"}) == 0.75
        assert _worst_score(findings, {"performance"}) == 1.0

    def test_compute_scores_all_clean(self) -> None:
        """No findings → all scores 1.0, overall 1.0 → APPROVE."""
        scores = _compute_scores([])
        assert scores["security_score"] == 1.0
        assert scores["performance_score"] == 1.0
        assert scores["maintainability_score"] == 1.0
        assert scores["overall_score"] == 1.0

    def test_compute_scores_formula(self) -> None:
        """overall = 0.5*security + 0.3*maintainability + 0.2*performance."""
        findings = [
            _finding(agent="security", severity="CRITICAL"),       # 0.0
            _finding(agent="quality", severity="MEDIUM"),           # 0.5
            _finding(agent="performance", severity="LOW"),          # 0.75
        ]
        scores = _compute_scores(findings)
        assert scores["security_score"] == 0.0
        assert scores["maintainability_score"] == 0.5
        assert scores["performance_score"] == 0.75
        expected = 0.5 * 0.0 + 0.3 * 0.5 + 0.2 * 0.75
        assert scores["overall_score"] == round(expected, 3)

    def test_compute_scores_maintainability_uses_quality_and_arch(self) -> None:
        """Maintainability considers both quality and architecture agents."""
        findings = [
            _finding(agent="architecture", severity="HIGH"),  # 0.25
            _finding(agent="quality", severity="LOW"),         # 0.75
        ]
        scores = _compute_scores(findings)
        # Worst of (0.25, 0.75) = 0.25
        assert scores["maintainability_score"] == 0.25


# ── Risk agent: run_risk_agent ─────────────────────────────────────────


class TestRiskAgent:
    """Tests for :func:`agents.risk_agent.run_risk_agent`."""

    def test_approve_when_no_findings(self) -> None:
        """No findings → overall 1.0 → APPROVE."""
        with patch("agents.risk_agent.invoke_llm", return_value=_mock_llm_response('{"summary": "Clean"}')):
            result = run_risk_agent([])
        assert result["overall_score"] == 1.0
        assert result["merge_recommendation"] == "APPROVE"
        assert result["summary"] == "Clean"

    def test_block_merge_on_critical(self) -> None:
        """CRITICAL findings in security + quality → overall 0.2 → BLOCK_MERGE.

        overall = 0.5*0.0 + 0.3*0.0 + 0.2*1.0 = 0.2 (< 0.4 threshold).
        """
        findings = [
            _finding(agent="security", severity="CRITICAL"),
            _finding(agent="quality", severity="CRITICAL"),
        ]
        with patch("agents.risk_agent.invoke_llm", return_value=_mock_llm_response('{"summary": "Bad"}')):
            result = run_risk_agent(findings)
        assert result["overall_score"] == 0.2
        assert result["merge_recommendation"] == "BLOCK_MERGE"

    def test_request_changes_on_medium(self) -> None:
        """A MEDIUM quality finding → overall 0.85 → APPROVE (>= 0.8)."""
        # maintainability = 0.5, overall = 0.3*0.5 + 0.7*1.0 = 0.85
        findings = [_finding(agent="quality", severity="MEDIUM")]
        with patch("agents.risk_agent.invoke_llm", return_value=_mock_llm_response('{"summary": "OK"}')):
            result = run_risk_agent(findings)
        assert result["overall_score"] == 0.85
        assert result["merge_recommendation"] == "APPROVE"

    def test_llm_failure_uses_default_summary(self) -> None:
        """When the LLM call fails, a default summary is generated."""
        with patch("agents.risk_agent.invoke_llm", side_effect=RuntimeError("LLM down")):
            result = run_risk_agent([])
        assert result["merge_recommendation"] == "APPROVE"
        assert isinstance(result["summary"], str)
        assert len(result["summary"]) > 0

    def test_llm_empty_summary_uses_default(self) -> None:
        """When the LLM returns an empty summary, the default is used."""
        with patch("agents.risk_agent.invoke_llm", return_value=_mock_llm_response('{"summary": ""}')):
            result = run_risk_agent([])
        assert result["summary"]  # non-empty default

    def test_result_has_all_keys(self) -> None:
        """The result dict contains all expected keys."""
        with patch("agents.risk_agent.invoke_llm", return_value=_mock_llm_response('{"summary": "x"}')):
            result = run_risk_agent([])
        for key in (
            "security_score", "performance_score", "maintainability_score",
            "overall_score", "merge_recommendation", "summary",
        ):
            assert key in result

    def test_default_summary_approve(self) -> None:
        """The default summary for APPROVE mentions no critical issues."""
        summary = _default_summary(
            {"overall_score": 1.0}, "APPROVE", [],
        )
        assert "safe to merge" in summary.lower()

    def test_default_summary_block(self) -> None:
        """The default summary for BLOCK_MERGE mentions critical issues."""
        findings = [_finding(severity="CRITICAL"), _finding(severity="HIGH")]
        summary = _default_summary(
            {"overall_score": 0.0}, "BLOCK_MERGE", findings,
        )
        assert "should not be merged" in summary.lower()


# ── Report agent ───────────────────────────────────────────────────────


class TestReportAgent:
    """Tests for :func:`agents.report_agent.run_report_agent`."""

    def test_llm_report_returned(self) -> None:
        """When the LLM succeeds, its markdown is returned."""
        markdown = "# Review\n\nAll good."
        risk = {
            "overall_score": 1.0,
            "merge_recommendation": "APPROVE",
            "summary": "Clean code",
            "security_score": 1.0,
            "performance_score": 1.0,
            "maintainability_score": 1.0,
        }
        with patch("agents.report_agent.invoke_llm", return_value=_mock_llm_response(markdown)):
            result = run_report_agent([], risk)
        assert result == markdown

    def test_llm_failure_uses_fallback(self) -> None:
        """When the LLM fails, the deterministic fallback is used."""
        risk = {
            "overall_score": 0.0,
            "merge_recommendation": "BLOCK_MERGE",
            "summary": "Bad code",
            "security_score": 0.0,
            "performance_score": 1.0,
            "maintainability_score": 1.0,
        }
        findings = [_finding(severity="CRITICAL", title="Bad")]
        with patch("agents.report_agent.invoke_llm", side_effect=RuntimeError("down")):
            result = run_report_agent(findings, risk)
        assert "CodeGuardian AI" in result
        assert "BLOCK MERGE" in result or "BLOCK_MERGE" in result.replace("_", " ")

    def test_llm_empty_content_uses_fallback(self) -> None:
        """When the LLM returns empty content, the fallback is used."""
        risk = {"merge_recommendation": "APPROVE", "overall_score": 1.0, "summary": ""}
        with patch("agents.report_agent.invoke_llm", return_value=_mock_llm_response("   ")):
            result = run_report_agent([], risk)
        assert "CodeGuardian AI" in result

    def test_fallback_report_structure(self) -> None:
        """The fallback report has the expected markdown structure."""
        risk = {
            "overall_score": 0.5,
            "merge_recommendation": "REQUEST_CHANGES",
            "summary": "Some issues",
            "security_score": 0.5,
            "performance_score": 1.0,
            "maintainability_score": 0.5,
        }
        findings = [
            _finding(severity="CRITICAL", title="Critical bug"),
            _finding(severity="LOW", title="Minor issue"),
        ]
        report = _fallback_report(findings, risk)
        assert "# 🔍 CodeGuardian AI Review" in report
        assert "## Risk Breakdown" in report
        assert "## Findings" in report
        assert "## Statistics" in report
        assert "Critical bug" in report
        assert "Minor issue" in report

    def test_fallback_report_no_findings(self) -> None:
        """The fallback report handles zero findings gracefully."""
        risk = {
            "overall_score": 1.0,
            "merge_recommendation": "APPROVE",
            "summary": "Clean",
            "security_score": 1.0,
            "performance_score": 1.0,
            "maintainability_score": 1.0,
        }
        report = _fallback_report([], risk)
        assert "No findings" in report
        assert "Total findings:** 0" in report

    def test_fallback_report_groups_by_severity(self) -> None:
        """The fallback report groups findings by severity (CRITICAL first)."""
        risk = {"merge_recommendation": "BLOCK_MERGE", "overall_score": 0.0, "summary": ""}
        findings = [
            _finding(severity="LOW", title="Low issue"),
            _finding(severity="CRITICAL", title="Critical issue"),
            _finding(severity="HIGH", title="High issue"),
        ]
        report = _fallback_report(findings, risk)
        # CRITICAL section should appear before LOW section.
        crit_pos = report.find("Critical issue")
        low_pos = report.find("Low issue")
        assert crit_pos < low_pos
