"""Tests for the LangGraph workflow: state, router, nodes, and graph execution.

Covers:
    * Router logic (``should_run_performance``, ``should_run_architecture``,
      ``route_agents``)
    * Individual node functions (load_pr, static_analysis, router, specialists,
      consensus, risk, report)
    * Graph compilation (``build_graph``)
    * End-to-end graph execution with mocked agents
    * ``operator.add`` reducer accumulation across parallel specialist nodes
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from graph.nodes import (
    architecture_node,
    bug_node,
    consensus_node,
    load_pr_node,
    performance_node,
    quality_node,
    report_node,
    risk_node,
    router_node,
    security_node,
    static_analysis_node,
)
from graph.router import (
    route_agents,
    should_run_architecture,
    should_run_performance,
)
from graph.state import CodeGuardianState
from graph.workflow import build_graph, review_graph
from scanners.parser import ScannerResult


# ════════════════════════════════════════════════════════════════════
#  Router logic
# ════════════════════════════════════════════════════════════════════


class TestShouldRunPerformance:
    """Tests for :func:`graph.router.should_run_performance`."""

    def test_for_loop_triggers(self) -> None:
        assert should_run_performance("for item in items:\n    pass") is True

    def test_while_loop_triggers(self) -> None:
        assert should_run_performance("while True:\n    break") is True

    def test_sql_select_triggers(self) -> None:
        assert should_run_performance("SELECT * FROM users") is True

    def test_sql_insert_triggers(self) -> None:
        assert should_run_performance("INSERT INTO users VALUES (1)") is True

    def test_orm_query_triggers(self) -> None:
        assert should_run_performance("db.query(User).filter(User.id == 1).all()") is True

    def test_pandas_dataframe_triggers(self) -> None:
        assert should_run_performance("df = pd.DataFrame(data)") is True

    def test_pandas_iterrows_triggers(self) -> None:
        assert should_run_performance("for idx, row in df.iterrows():\n    pass") is True

    def test_numpy_triggers(self) -> None:
        assert should_run_performance("arr = np.array([1, 2, 3])") is True

    def test_async_for_triggers(self) -> None:
        assert should_run_performance("async for item in stream:\n    pass") is True

    def test_gather_triggers(self) -> None:
        assert should_run_performance("await asyncio.gather(*tasks)") is True

    def test_db_execute_triggers(self) -> None:
        assert should_run_performance("cursor.execute('SELECT 1')") is True

    def test_plain_code_no_trigger(self) -> None:
        assert should_run_performance("x = 1 + 2\nprint(x)") is False

    def test_empty_diff_no_trigger(self) -> None:
        assert should_run_performance("") is False

    def test_whitespace_only_no_trigger(self) -> None:
        assert should_run_performance("   \n  \n") is False


class TestShouldRunArchitecture:
    """Tests for :func:`graph.router.should_run_architecture`."""

    def test_new_file_triggers(self) -> None:
        diff = "--- /dev/null\n+++ b/new_file.py"
        assert should_run_architecture(diff, ["new_file.py"]) is True

    def test_added_import_triggers(self) -> None:
        diff = "+import os\n+from pathlib import Path"
        assert should_run_architecture(diff, ["file.py"]) is True

    def test_removed_import_triggers(self) -> None:
        diff = "-import os"
        assert should_run_architecture(diff, ["file.py"]) is True

    def test_new_class_triggers(self) -> None:
        diff = "+class MyClass:\n+    pass"
        assert should_run_architecture(diff, ["file.py"]) is True

    def test_new_function_triggers(self) -> None:
        diff = "+def my_function():\n+    pass"
        assert should_run_architecture(diff, ["file.py"]) is True

    def test_many_files_triggers(self) -> None:
        files = [f"file_{i}.py" for i in range(6)]
        assert should_run_architecture("", files) is True

    def test_exactly_threshold_does_not_trigger(self) -> None:
        """Five files (== threshold) should NOT trigger — only > threshold."""
        files = [f"file_{i}.py" for i in range(5)]
        assert should_run_architecture("", files) is False

    def test_few_files_no_trigger(self) -> None:
        diff = "+    x = 1\n+    y = 2"
        assert should_run_architecture(diff, ["file.py"]) is False

    def test_empty_diff_empty_files_no_trigger(self) -> None:
        assert should_run_architecture("", []) is False


class TestRouteAgents:
    """Tests for :func:`graph.router.route_agents`."""

    def test_always_runs_security_bug_quality(self) -> None:
        state: CodeGuardianState = {"code_diff": "x = 1", "changed_files": ["a.py"]}
        result = route_agents(state)
        assert "security" in result
        assert "bug" in result
        assert "quality" in result

    def test_plain_diff_only_three(self) -> None:
        state: CodeGuardianState = {"code_diff": "x = 1\ny = 2", "changed_files": ["a.py"]}
        result = route_agents(state)
        assert result == ["security", "bug", "quality"]

    def test_performance_diff_adds_performance(self) -> None:
        state: CodeGuardianState = {
            "code_diff": "for x in items:\n    pass",
            "changed_files": ["a.py"],
        }
        result = route_agents(state)
        assert "performance" in result
        assert len(result) == 4

    def test_architecture_diff_adds_architecture(self) -> None:
        state: CodeGuardianState = {
            "code_diff": "+import os",
            "changed_files": ["a.py"],
        }
        result = route_agents(state)
        assert "architecture" in result
        assert len(result) == 4

    def test_both_triggers_adds_both(self) -> None:
        state: CodeGuardianState = {
            "code_diff": "for x in items:\n    pass\n+import os",
            "changed_files": ["a.py"],
        }
        result = route_agents(state)
        assert "performance" in result
        assert "architecture" in result
        assert len(result) == 5

    def test_many_files_adds_architecture(self) -> None:
        state: CodeGuardianState = {
            "code_diff": "x = 1",
            "changed_files": [f"f{i}.py" for i in range(6)],
        }
        result = route_agents(state)
        assert "architecture" in result
        assert "performance" not in result

    def test_empty_state(self) -> None:
        state: CodeGuardianState = {}
        result = route_agents(state)
        assert result == ["security", "bug", "quality"]

    def test_order_security_bug_quality_first(self) -> None:
        """Security, bug, quality always come before conditional agents."""
        state: CodeGuardianState = {
            "code_diff": "for x in items:\n+import os",
            "changed_files": ["a.py"],
        }
        result = route_agents(state)
        assert result[:3] == ["security", "bug", "quality"]


# ════════════════════════════════════════════════════════════════════
#  Node functions
# ════════════════════════════════════════════════════════════════════


class TestLoadPrNode:
    """Tests for :func:`graph.nodes.load_pr_node`."""

    def test_derives_file_tree_sorted(self) -> None:
        state: CodeGuardianState = {"changed_files": ["b.py", "a.py", "c.py"]}
        result = load_pr_node(state)
        assert result["file_tree"] == "a.py\nb.py\nc.py"

    def test_empty_changed_files(self) -> None:
        state: CodeGuardianState = {"changed_files": []}
        result = load_pr_node(state)
        assert result["file_tree"] == ""

    def test_missing_changed_files(self) -> None:
        result = load_pr_node({})
        assert result["file_tree"] == ""

    def test_single_file(self) -> None:
        state: CodeGuardianState = {"changed_files": ["only.py"]}
        result = load_pr_node(state)
        assert result["file_tree"] == "only.py"


class TestStaticAnalysisNode:
    """Tests for :func:`graph.nodes.static_analysis_node`."""

    def test_no_repo_path_returns_empty_result(self) -> None:
        state: CodeGuardianState = {"changed_files": ["a.py"]}
        result = static_analysis_node(state)
        assert isinstance(result["scanner_result"], ScannerResult)
        assert result["scanner_result"].total_findings == 0
        assert "scanner_context" in result

    def test_no_changed_files_returns_empty_result(self) -> None:
        state: CodeGuardianState = {"repo_path": "/tmp/repo"}
        result = static_analysis_node(state)
        assert isinstance(result["scanner_result"], ScannerResult)
        assert result["scanner_result"].total_findings == 0

    def test_empty_state_returns_empty_result(self) -> None:
        result = static_analysis_node({})
        assert isinstance(result["scanner_result"], ScannerResult)
        assert result["scanner_result"].total_findings == 0

    @patch("graph.nodes.run_static_analysis")
    @patch("graph.nodes.get_context_block")
    def test_runs_analysis_when_repo_and_files(
        self, mock_context: MagicMock, mock_run: MagicMock
    ) -> None:
        mock_result = MagicMock()
        mock_result.total_findings = 3
        mock_run.return_value = mock_result
        mock_context.return_value = "## Scanner Context\n\n3 findings"

        state: CodeGuardianState = {"repo_path": "/tmp/repo", "changed_files": ["a.py"]}
        result = static_analysis_node(state)

        mock_run.assert_called_once_with("/tmp/repo", ["a.py"])
        mock_context.assert_called_once_with(mock_result)
        assert result["scanner_result"] is mock_result
        assert result["scanner_context"] == "## Scanner Context\n\n3 findings"


class TestRouterNode:
    """Tests for :func:`graph.nodes.router_node`."""

    def test_returns_empty_dict(self) -> None:
        assert router_node({}) == {}

    def test_returns_empty_dict_with_state(self) -> None:
        assert router_node({"code_diff": "x = 1"}) == {}


class TestSpecialistNodes:
    """Tests for the five specialist agent node functions."""

    @patch("agents.security_agent.run_security_agent")
    def test_security_node(self, mock_agent: MagicMock) -> None:
        mock_agent.return_value = [{"severity": "HIGH", "title": "SQL Injection"}]
        state: CodeGuardianState = {"code_diff": "diff", "scanner_context": "ctx"}
        result = security_node(state)
        mock_agent.assert_called_once_with(code_diff="diff", scanner_context="ctx")
        assert result == {"security_findings": [{"severity": "HIGH", "title": "SQL Injection"}]}

    @patch("agents.bug_agent.run_bug_agent")
    def test_bug_node(self, mock_agent: MagicMock) -> None:
        mock_agent.return_value = [{"severity": "MEDIUM", "title": "Null deref"}]
        state: CodeGuardianState = {"code_diff": "diff", "scanner_context": "ctx"}
        result = bug_node(state)
        mock_agent.assert_called_once_with(code_diff="diff", scanner_context="ctx")
        assert result == {"bug_findings": [{"severity": "MEDIUM", "title": "Null deref"}]}

    @patch("agents.performance_agent.run_performance_agent")
    def test_performance_node(self, mock_agent: MagicMock) -> None:
        mock_agent.return_value = [{"severity": "LOW", "title": "N+1 query"}]
        state: CodeGuardianState = {"code_diff": "diff", "scanner_context": "ctx"}
        result = performance_node(state)
        mock_agent.assert_called_once_with(code_diff="diff", scanner_context="ctx")
        assert result == {"performance_findings": [{"severity": "LOW", "title": "N+1 query"}]}

    @patch("agents.quality_agent.run_quality_agent")
    def test_quality_node(self, mock_agent: MagicMock) -> None:
        mock_agent.return_value = [{"severity": "LOW", "title": "Long function"}]
        state: CodeGuardianState = {"code_diff": "diff", "scanner_context": "ctx"}
        result = quality_node(state)
        mock_agent.assert_called_once_with(code_diff="diff", scanner_context="ctx")
        assert result == {"quality_findings": [{"severity": "LOW", "title": "Long function"}]}

    @patch("agents.architecture_agent.run_architecture_agent")
    def test_architecture_node(self, mock_agent: MagicMock) -> None:
        mock_agent.return_value = [{"severity": "INFO", "title": "Circular import"}]
        state: CodeGuardianState = {
            "code_diff": "diff",
            "scanner_context": "ctx",
            "file_tree": "a.py\nb.py",
        }
        result = architecture_node(state)
        mock_agent.assert_called_once_with(
            code_diff="diff", scanner_context="ctx", file_tree="a.py\nb.py"
        )
        assert result == {"architecture_findings": [{"severity": "INFO", "title": "Circular import"}]}

    @patch("agents.security_agent.run_security_agent")
    def test_security_node_empty_state(self, mock_agent: MagicMock) -> None:
        mock_agent.return_value = []
        result = security_node({})
        mock_agent.assert_called_once_with(code_diff="", scanner_context="")
        assert result == {"security_findings": []}

    @patch("agents.architecture_agent.run_architecture_agent")
    def test_architecture_node_missing_file_tree(self, mock_agent: MagicMock) -> None:
        mock_agent.return_value = []
        result = architecture_node({"code_diff": "diff"})
        mock_agent.assert_called_once_with(code_diff="diff", scanner_context="", file_tree="")
        assert result == {"architecture_findings": []}


class TestSynthesisNodes:
    """Tests for consensus, risk, and report node functions."""

    @patch("agents.consensus_agent.run_consensus_agent")
    def test_consensus_node(self, mock_agent: MagicMock) -> None:
        mock_agent.return_value = [{"severity": "HIGH", "title": "Merged finding"}]
        state: CodeGuardianState = {
            "security_findings": [{"severity": "HIGH"}],
            "bug_findings": [{"severity": "MEDIUM"}],
            "performance_findings": [],
            "quality_findings": [{"severity": "LOW"}],
            "architecture_findings": [],
        }
        result = consensus_node(state)
        mock_agent.assert_called_once_with(
            security_findings=[{"severity": "HIGH"}],
            bug_findings=[{"severity": "MEDIUM"}],
            performance_findings=[],
            quality_findings=[{"severity": "LOW"}],
            architecture_findings=[],
        )
        assert result == {"consensus_findings": [{"severity": "HIGH", "title": "Merged finding"}]}

    @patch("agents.consensus_agent.run_consensus_agent")
    def test_consensus_node_empty_state(self, mock_agent: MagicMock) -> None:
        mock_agent.return_value = []
        result = consensus_node({})
        mock_agent.assert_called_once_with(
            security_findings=[],
            bug_findings=[],
            performance_findings=[],
            quality_findings=[],
            architecture_findings=[],
        )
        assert result == {"consensus_findings": []}

    @patch("agents.risk_agent.run_risk_agent")
    def test_risk_node(self, mock_agent: MagicMock) -> None:
        mock_agent.return_value = {
            "security_score": 0.25,
            "performance_score": 0.75,
            "maintainability_score": 0.75,
            "overall_score": 0.5,
            "merge_recommendation": "REQUEST_CHANGES",
            "summary": "Some issues found",
        }
        state: CodeGuardianState = {"consensus_findings": [{"severity": "HIGH"}]}
        result = risk_node(state)
        mock_agent.assert_called_once_with([{"severity": "HIGH"}])
        assert result["risk_scores"]["overall_score"] == 0.5
        assert result["merge_recommendation"] == "REQUEST_CHANGES"

    @patch("agents.risk_agent.run_risk_agent")
    def test_risk_node_missing_recommendation(self, mock_agent: MagicMock) -> None:
        """When risk agent omits merge_recommendation, default to UNKNOWN."""
        mock_agent.return_value = {"overall_score": 1.0}
        result = risk_node({})
        assert result["merge_recommendation"] == "UNKNOWN"

    @patch("agents.report_agent.run_report_agent")
    def test_report_node(self, mock_agent: MagicMock) -> None:
        mock_agent.return_value = "# Code Review Report\n\n## Summary\n\nAll good"
        state: CodeGuardianState = {
            "consensus_findings": [{"severity": "LOW"}],
            "risk_scores": {"overall_score": 0.9},
        }
        result = report_node(state)
        mock_agent.assert_called_once_with(
            consensus_findings=[{"severity": "LOW"}],
            risk_scores={"overall_score": 0.9},
        )
        assert result == {"final_report": "# Code Review Report\n\n## Summary\n\nAll good"}

    @patch("agents.report_agent.run_report_agent")
    def test_report_node_empty_state(self, mock_agent: MagicMock) -> None:
        mock_agent.return_value = "# Review\n\nNo issues"
        result = report_node({})
        mock_agent.assert_called_once_with(consensus_findings=[], risk_scores={})
        assert result == {"final_report": "# Review\n\nNo issues"}


# ════════════════════════════════════════════════════════════════════
#  Graph compilation
# ════════════════════════════════════════════════════════════════════


class TestBuildGraph:
    """Tests for :func:`graph.workflow.build_graph`."""

    def test_build_graph_returns_compiled(self) -> None:
        graph = build_graph()
        assert graph is not None
        assert hasattr(graph, "invoke")

    def test_review_graph_exists(self) -> None:
        assert review_graph is not None
        assert hasattr(review_graph, "invoke")

    def test_build_graph_returns_new_instance(self) -> None:
        """Each call to build_graph returns a fresh compiled graph."""
        g1 = build_graph()
        g2 = build_graph()
        assert g1 is not g2


# ════════════════════════════════════════════════════════════════════
#  End-to-end graph execution
# ════════════════════════════════════════════════════════════════════


def _mock_all_agents():
    """Return a dict of patchers for all 8 agents, pre-configured with returns."""
    patches = {
        "security": patch("agents.security_agent.run_security_agent"),
        "bug": patch("agents.bug_agent.run_bug_agent"),
        "performance": patch("agents.performance_agent.run_performance_agent"),
        "quality": patch("agents.quality_agent.run_quality_agent"),
        "architecture": patch("agents.architecture_agent.run_architecture_agent"),
        "consensus": patch("agents.consensus_agent.run_consensus_agent"),
        "risk": patch("agents.risk_agent.run_risk_agent"),
        "report": patch("agents.report_agent.run_report_agent"),
    }
    started = {name: p.start() for name, p in patches.items()}
    return started, patches


def _stop_all(patches: dict) -> None:
    for p in patches.values():
        p.stop()


class TestGraphExecution:
    """End-to-end tests for the compiled LangGraph workflow."""

    def test_all_five_agents_triggered(self) -> None:
        """Diff with loops + imports triggers all 5 specialist agents."""
        started, patches = _mock_all_agents()
        try:
            started["security"].return_value = [{"severity": "HIGH", "title": "SQL Injection"}]
            started["bug"].return_value = [{"severity": "MEDIUM", "title": "Null deref"}]
            started["performance"].return_value = [{"severity": "LOW", "title": "N+1 query"}]
            started["quality"].return_value = [{"severity": "LOW", "title": "Long function"}]
            started["architecture"].return_value = [{"severity": "INFO", "title": "Circular import"}]
            started["consensus"].return_value = [
                {"severity": "HIGH", "title": "SQL Injection", "agent": "security"},
                {"severity": "MEDIUM", "title": "Null deref", "agent": "bug"},
            ]
            started["risk"].return_value = {
                "security_score": 0.25,
                "performance_score": 0.75,
                "maintainability_score": 0.75,
                "overall_score": 0.5,
                "merge_recommendation": "REQUEST_CHANGES",
                "summary": "Some issues found",
            }
            started["report"].return_value = "# Code Review Report\n\n## Summary\n\nIssues found."

            initial_state: CodeGuardianState = {
                "pr_number": 42,
                "commit_sha": "abc123",
                "repository": "owner/repo",
                "branch": "feature",
                "code_diff": "for x in items:\n    pass\n+import os",
                "changed_files": ["a.py", "b.py"],
            }

            graph = build_graph()
            result = graph.invoke(initial_state)

            # All 5 specialist agents should have been called
            started["security"].assert_called_once()
            started["bug"].assert_called_once()
            started["performance"].assert_called_once()
            started["quality"].assert_called_once()
            started["architecture"].assert_called_once()

            # Final state should contain all expected keys
            assert "final_report" in result
            assert "consensus_findings" in result
            assert "risk_scores" in result
            assert "merge_recommendation" in result
            assert result["merge_recommendation"] == "REQUEST_CHANGES"
            assert result["final_report"] == "# Code Review Report\n\n## Summary\n\nIssues found."
        finally:
            _stop_all(patches)

    def test_minimal_diff_only_three_agents(self) -> None:
        """Plain diff triggers only security, bug, quality — not performance/architecture."""
        started, patches = _mock_all_agents()
        try:
            started["security"].return_value = [{"severity": "LOW", "title": "Minor issue"}]
            started["bug"].return_value = []
            started["quality"].return_value = []
            started["consensus"].return_value = [{"severity": "LOW", "title": "Minor issue"}]
            started["risk"].return_value = {
                "security_score": 0.75,
                "performance_score": 1.0,
                "maintainability_score": 1.0,
                "overall_score": 0.875,
                "merge_recommendation": "APPROVE",
                "summary": "Looks good",
            }
            started["report"].return_value = "# Review\n\nApproved"

            initial_state: CodeGuardianState = {
                "pr_number": 1,
                "commit_sha": "def456",
                "repository": "owner/repo",
                "branch": "main",
                "code_diff": "x = 1\ny = 2",
                "changed_files": ["a.py"],
            }

            graph = build_graph()
            result = graph.invoke(initial_state)

            # Only 3 agents should have been called
            started["security"].assert_called_once()
            started["bug"].assert_called_once()
            started["quality"].assert_called_once()
            started["performance"].assert_not_called()
            started["architecture"].assert_not_called()

            assert result["merge_recommendation"] == "APPROVE"
            assert result["final_report"] == "# Review\n\nApproved"
        finally:
            _stop_all(patches)

    def test_reducer_accumulates_findings(self) -> None:
        """operator.add reducers accumulate findings from parallel specialist nodes."""
        started, patches = _mock_all_agents()
        try:
            sec_finding = {"severity": "HIGH", "title": "SQL Injection"}
            bug_finding = {"severity": "MEDIUM", "title": "Null deref"}
            perf_finding = {"severity": "LOW", "title": "N+1 query"}
            qual_finding = {"severity": "LOW", "title": "Long function"}
            arch_finding = {"severity": "INFO", "title": "Circular import"}

            started["security"].return_value = [sec_finding]
            started["bug"].return_value = [bug_finding]
            started["performance"].return_value = [perf_finding]
            started["quality"].return_value = [qual_finding]
            started["architecture"].return_value = [arch_finding]
            started["consensus"].return_value = [
                sec_finding, bug_finding, perf_finding, qual_finding, arch_finding,
            ]
            started["risk"].return_value = {
                "security_score": 0.25,
                "performance_score": 0.75,
                "maintainability_score": 0.75,
                "overall_score": 0.5,
                "merge_recommendation": "REQUEST_CHANGES",
                "summary": "Multiple issues",
            }
            started["report"].return_value = "# Review"

            initial_state: CodeGuardianState = {
                "pr_number": 7,
                "commit_sha": "sha789",
                "repository": "owner/repo",
                "branch": "feature",
                "code_diff": "for x in items:\n    pass\n+import os",
                "changed_files": ["a.py", "b.py"],
            }

            graph = build_graph()
            graph.invoke(initial_state)

            # Consensus should have received findings from ALL 5 agents
            started["consensus"].assert_called_once()
            call_kwargs = started["consensus"].call_args.kwargs
            assert call_kwargs["security_findings"] == [sec_finding]
            assert call_kwargs["bug_findings"] == [bug_finding]
            assert call_kwargs["performance_findings"] == [perf_finding]
            assert call_kwargs["quality_findings"] == [qual_finding]
            assert call_kwargs["architecture_findings"] == [arch_finding]
        finally:
            _stop_all(patches)

    def test_empty_findings_flow(self) -> None:
        """Graph completes successfully when all agents return empty findings."""
        started, patches = _mock_all_agents()
        try:
            for name in ("security", "bug", "performance", "quality", "architecture"):
                started[name].return_value = []
            started["consensus"].return_value = []
            started["risk"].return_value = {
                "security_score": 1.0,
                "performance_score": 1.0,
                "maintainability_score": 1.0,
                "overall_score": 1.0,
                "merge_recommendation": "APPROVE",
                "summary": "No issues found",
            }
            started["report"].return_value = "# Review\n\nNo issues found. Approved."

            initial_state: CodeGuardianState = {
                "pr_number": 99,
                "commit_sha": "empty",
                "repository": "owner/repo",
                "branch": "main",
                "code_diff": "for x in items:\n    pass\n+import os",
                "changed_files": ["a.py"],
            }

            graph = build_graph()
            result = graph.invoke(initial_state)

            assert result["merge_recommendation"] == "APPROVE"
            assert result["consensus_findings"] == []
            assert "No issues found" in result["final_report"]
        finally:
            _stop_all(patches)

    def test_file_tree_passed_to_architecture(self) -> None:
        """The architecture agent receives the file_tree derived by load_pr_node."""
        started, patches = _mock_all_agents()
        try:
            started["security"].return_value = []
            started["bug"].return_value = []
            started["performance"].return_value = []
            started["quality"].return_value = []
            started["architecture"].return_value = []
            started["consensus"].return_value = []
            started["risk"].return_value = {
                "overall_score": 1.0,
                "merge_recommendation": "APPROVE",
                "summary": "Clean",
            }
            started["report"].return_value = "# Review"

            changed = ["zeta.py", "alpha.py", "mid.py"]
            initial_state: CodeGuardianState = {
                "pr_number": 3,
                "commit_sha": "tree123",
                "repository": "owner/repo",
                "branch": "feature",
                "code_diff": "+import os",
                "changed_files": changed,
            }

            graph = build_graph()
            graph.invoke(initial_state)

            # Architecture agent should have been called with sorted file_tree
            started["architecture"].assert_called_once()
            call_kwargs = started["architecture"].call_args.kwargs
            assert call_kwargs["file_tree"] == "alpha.py\nmid.py\nzeta.py"
        finally:
            _stop_all(patches)

    def test_scanner_context_passed_to_specialists(self) -> None:
        """Specialist agents receive the scanner_context from static_analysis_node."""
        started, patches = _mock_all_agents()
        try:
            started["security"].return_value = []
            started["bug"].return_value = []
            started["quality"].return_value = []
            started["consensus"].return_value = []
            started["risk"].return_value = {
                "overall_score": 1.0,
                "merge_recommendation": "APPROVE",
                "summary": "Clean",
            }
            started["report"].return_value = "# Review"

            # No repo_path → static_analysis returns empty ScannerResult
            # but scanner_context should still be a string
            initial_state: CodeGuardianState = {
                "pr_number": 5,
                "commit_sha": "ctx123",
                "repository": "owner/repo",
                "branch": "main",
                "code_diff": "x = 1",
                "changed_files": ["a.py"],
            }

            graph = build_graph()
            graph.invoke(initial_state)

            # Each specialist should have received a string scanner_context
            for name in ("security", "bug", "quality"):
                call_kwargs = started[name].call_args.kwargs
                assert isinstance(call_kwargs["scanner_context"], str)
        finally:
            _stop_all(patches)
