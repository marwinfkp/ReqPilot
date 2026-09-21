"""``analysis_graph`` - the P3 subset of architecture C.3.

Only the nodes batch extraction and classification need::

    START -> load_scope -+-> extract_requirements -> validate_extraction
                         |        -> persist_candidates -> classify -> END
                         +-> classify -> END            (classification-only run)
                         +-> error_handler -> END       (a failure at any step)

Nodes 6-24 of C.3 - quality, conflicts, clarification, compliance, security,
risk, gate fan-out, validation, G1 - belong to later roadmap phases and are
deliberately absent. Nothing here reaches an approval: the graph ends at
``CLASSIFIED`` at the furthest, and a human takes it from there.

Every edge is deterministic. The conditional edges call the routers in
:mod:`reqpilot.graph.routers`, which read counts and flags - never model text.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from reqpilot.graph.nodes.analysis import AnalysisNodes
from reqpilot.graph.routers import route_after_scope, route_extraction, route_validation
from reqpilot.graph.state import AnalysisState

NODE_NAMES = (
    "load_scope",
    "extract_requirements",
    "validate_extraction",
    "persist_candidates",
    "classify",
    "error_handler",
)


def build_analysis_graph(nodes: AnalysisNodes, checkpointer: Any | None = None) -> Any:
    graph = StateGraph(AnalysisState)
    graph.add_node("load_scope", nodes.load_scope)
    graph.add_node("extract_requirements", nodes.extract_requirements)
    graph.add_node("validate_extraction", nodes.validate_extraction)
    graph.add_node("persist_candidates", nodes.persist_candidates)
    graph.add_node("classify", nodes.classify)
    graph.add_node("error_handler", nodes.error_handler)

    graph.add_edge(START, "load_scope")
    graph.add_conditional_edges(
        "load_scope",
        route_after_scope,
        {
            "extract_requirements": "extract_requirements",
            "classify": "classify",
            "error_handler": "error_handler",
        },
    )
    graph.add_conditional_edges(
        "extract_requirements",
        route_extraction,
        {"validate_extraction": "validate_extraction", "error_handler": "error_handler"},
    )
    graph.add_conditional_edges(
        "validate_extraction",
        route_validation,
        {"persist_candidates": "persist_candidates", "error_handler": "error_handler"},
    )
    graph.add_edge("persist_candidates", "classify")
    graph.add_edge("classify", END)
    graph.add_edge("error_handler", END)
    return graph.compile(checkpointer=checkpointer)
