"""``analysis_graph`` - the P3-P5 subset of architecture C.3.

The nodes batch extraction, classification and (P5) quality and conflict
detection need::

    START -> load_scope -+-> extract_requirements -> validate_extraction
                         |        -> persist_candidates -> classify -> END
                         |        -> persist_revision -> classify
                         |              -> quality_analysis -> ...   (P4/P5: a
                         |                                 clarification's re-analysis)
                         +-> classify -> END            (classification-only run)
                         +-> quality_analysis -> conflict_shortlist
                         |        -> conflict_adjudicate -> END    (P5 quality run)
                         +-> error_handler -> END       (a failure at any step)

Nodes 9-24 of C.3 - the clarification router, compliance, security, risk, gate
fan-out, validation, G1 - belong to later roadmap phases and are deliberately
absent (the clarification loop itself is P4's runner, not an interrupt here).
Nothing here reaches an approval: the graph ends at ``CLASSIFIED`` at the
furthest, with findings and conflicts recorded, and a human takes it from there.

Every edge is deterministic. The conditional edges call the routers in
:mod:`reqpilot.graph.routers`, which read counts and flags - never model text.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from reqpilot.graph.nodes.analysis import AnalysisNodes
from reqpilot.graph.routers import (
    route_after_classify,
    route_after_quality,
    route_after_scope,
    route_conflict_shortlist,
    route_extraction,
    route_validation,
)
from reqpilot.graph.state import AnalysisState

NODE_NAMES = (
    "load_scope",
    "extract_requirements",
    "validate_extraction",
    "persist_candidates",
    "persist_revision",
    "classify",
    "quality_analysis",
    "conflict_shortlist",
    "conflict_adjudicate",
    "error_handler",
)


def build_analysis_graph(nodes: AnalysisNodes, checkpointer: Any | None = None) -> Any:
    graph = StateGraph(AnalysisState)
    graph.add_node("load_scope", nodes.load_scope)
    graph.add_node("extract_requirements", nodes.extract_requirements)
    graph.add_node("validate_extraction", nodes.validate_extraction)
    graph.add_node("persist_candidates", nodes.persist_candidates)
    graph.add_node("persist_revision", nodes.persist_revision)
    graph.add_node("classify", nodes.classify)
    graph.add_node("quality_analysis", nodes.quality.quality_analysis)
    graph.add_node("conflict_shortlist", nodes.quality.conflict_shortlist)
    graph.add_node("conflict_adjudicate", nodes.quality.conflict_adjudicate)
    graph.add_node("error_handler", nodes.error_handler)

    graph.add_edge(START, "load_scope")
    graph.add_conditional_edges(
        "load_scope",
        route_after_scope,
        {
            "extract_requirements": "extract_requirements",
            "classify": "classify",
            "quality_analysis": "quality_analysis",
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
        {
            "persist_candidates": "persist_candidates",
            "persist_revision": "persist_revision",
            "error_handler": "error_handler",
        },
    )
    graph.add_edge("persist_candidates", "classify")
    graph.add_edge("persist_revision", "classify")
    graph.add_conditional_edges(
        "classify", route_after_classify, {"quality_analysis": "quality_analysis", "__end__": END}
    )
    graph.add_conditional_edges(
        "quality_analysis",
        route_after_quality,
        {
            "conflict_shortlist": "conflict_shortlist",
            "error_handler": "error_handler",
            "__end__": END,
        },
    )
    graph.add_conditional_edges(
        "conflict_shortlist",
        route_conflict_shortlist,
        {"conflict_adjudicate": "conflict_adjudicate", "__end__": END},
    )
    graph.add_edge("conflict_adjudicate", END)
    graph.add_edge("error_handler", END)
    return graph.compile(checkpointer=checkpointer)
