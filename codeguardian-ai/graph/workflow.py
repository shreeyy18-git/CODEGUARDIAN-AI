"""LangGraph workflow builder — assembles and compiles the review graph.

Topology::

    START → load_pr → static_analysis → router
                                        │
                    ┌───────────┬───────┴────────┬───────────┐
                    ▼           ▼                ▼           ▼
                security      bug          performance     quality
                    │           │                │           │
                    └─────┬─────┴────────┬───────┘           │
                          │                │                  │
                          ▼                ▼                  ▼
                     architecture ──→ consensus ←──────────────┘
                                          │
                                        risk
                                          │
                                        report
                                          │
                                         END

The router uses a conditional edge to fan out to a subset of specialist
nodes.  All specialists converge on ``consensus``, which feeds into
``risk`` and ``report`` sequentially.
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

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
from graph.router import route_agents
from graph.state import CodeGuardianState

__all__ = ["build_graph", "review_graph"]

_log = logging.getLogger("codeguardian.graph.workflow")

#: All possible specialist node names (used as the conditional-edge path map).
_SPECIALIST_NODES = [
    "security",
    "bug",
    "performance",
    "quality",
    "architecture",
]


def build_graph():
    """Build and compile the CodeGuardian AI review graph.

    Returns
    -------
    CompiledGraph
        A compiled LangGraph ready to ``.invoke(initial_state)``.
    """
    graph = StateGraph(CodeGuardianState)

    # ── Register nodes ───────────────────────────────────────────
    graph.add_node("load_pr", load_pr_node)
    graph.add_node("static_analysis", static_analysis_node)
    graph.add_node("router", router_node)
    graph.add_node("security", security_node)
    graph.add_node("bug", bug_node)
    graph.add_node("performance", performance_node)
    graph.add_node("quality", quality_node)
    graph.add_node("architecture", architecture_node)
    graph.add_node("consensus", consensus_node)
    graph.add_node("risk", risk_node)
    graph.add_node("report", report_node)

    # ── Linear prefix: START → load_pr → static_analysis → router ─
    graph.add_edge(START, "load_pr")
    graph.add_edge("load_pr", "static_analysis")
    graph.add_edge("static_analysis", "router")

    # ── Conditional fan-out: router → [specialists] ──────────────
    graph.add_conditional_edges(
        "router",
        route_agents,
        _SPECIALIST_NODES,
    )

    # ── Fan-in: every specialist → consensus ─────────────────────
    for node in _SPECIALIST_NODES:
        graph.add_edge(node, "consensus")

    # ── Linear suffix: consensus → risk → report → END ───────────
    graph.add_edge("consensus", "risk")
    graph.add_edge("risk", "report")
    graph.add_edge("report", END)

    compiled = graph.compile()
    _log.info("CodeGuardian AI graph compiled successfully")
    return compiled


#: Pre-built compiled graph for convenience.
review_graph = build_graph()
